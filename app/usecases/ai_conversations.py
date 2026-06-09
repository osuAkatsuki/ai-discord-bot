import json
import traceback
from typing import Any
from typing import NamedTuple

import discord
from pydantic import BaseModel

from app import discord_message_utils
from app import openai_functions
from app.adapters.openai import gpt
from app.adapters.openai.gpt import MessageContent
from app.errors import Error
from app.errors import ErrorCode
from app.models import DiscordBot
from app.repositories import thread_messages
from app.repositories import threads


DISCORD_USER_ID_WHITELIST: set[int] = {
    # Akatsuki
    285190493703503872,  # cmyui
    347459855449325570,  # flame
    1011439359083413564,  # kat
    291927822635761665,  # len
    418325367724703755,  # niotid
    263413454709194753,  # realistik
    241178004682833920,  # riffee
    272111921610752003,  # tsunyoku
    793331642801324063,  # woot
    250059887927623680,  # rapha
    190278149030936576,  # randomize
    249596453457100801,  # mistral
    151670779782758400,  # aesth
    109493893803819008,  # jackson
    # Super
    332722012877357066,  # fkzoink
}


def _get_author_id(author_name: str) -> int:
    # get a 3 digit highly unique id
    return hash(author_name) % 1000


def get_author_name(discord_author_name: str) -> str:
    return f"User #{_get_author_id(discord_author_name)}"


class _GptRequestResponse(NamedTuple):
    response_content: str
    input_tokens: int
    output_tokens: int


MAX_FUNCTION_CALL_ROUNDS = 5


async def _make_gpt_request(
    message_history: list[gpt.Message], model: gpt.AIModel
) -> _GptRequestResponse | Error:
    functions = openai_functions.get_full_openai_functions_schema()
    response_context_items: list[dict[str, Any]] = []
    input_tokens = 0
    output_tokens = 0

    for _ in range(MAX_FUNCTION_CALL_ROUNDS):
        try:
            gpt_response = await gpt.send(
                model=model,
                messages=message_history,
                functions=functions,
                response_context_items=response_context_items,
            )
        except Exception as exc:
            traceback.print_exc()
            # NOTE: this is *generally* bad practice to expose this information
            # to end users, and should be removed if we are to deploy this app
            # more widely. Right now it's okay because it's a private bot.
            return Error(
                code=ErrorCode.UNEXPECTED_ERROR,
                messages=[
                    f"Request to OpenAI failed with the following error:\n```\n{exc}```"
                ],
            )

        input_tokens += gpt_response.input_tokens
        output_tokens += gpt_response.output_tokens

        if not gpt_response.function_calls:
            assert gpt_response.response_content is not None
            return _GptRequestResponse(
                gpt_response.response_content,
                input_tokens,
                output_tokens,
            )

        response_context_items.extend(gpt_response.response_items)
        for function_call in gpt_response.function_calls:
            function_kwargs = json.loads(function_call.arguments)

            ai_function = openai_functions.ai_functions[function_call.name]
            function_response = await ai_function["callback"](**function_kwargs)

            if function_call.call_id is None:
                message_history.append(
                    {
                        "role": "function",
                        "name": function_call.name,
                        "content": [function_response],
                    }
                )
            else:
                response_context_items.append(
                    gpt.function_call_output_item(function_call, function_response)
                )

    raise NotImplementedError(
        f"OpenAI function calling exceeded {MAX_FUNCTION_CALL_ROUNDS} rounds"
    )


def _image_url_message_content(image_url: str) -> MessageContent:
    return {
        "type": "image_url",
        "image_url": {"url": image_url},
    }


def _message_content_from_prompt(
    prompt: str,
    attachment_urls: list[str],
) -> tuple[str, list[MessageContent]]:
    image_urls: list[str] = []

    # Extract image URLs from the prompt and replace them with {IMAGE}
    prompt_parts = prompt.split()
    images_seen = 0
    for i, part in enumerate(prompt_parts):
        if (part.startswith("http://") or part.startswith("https://")) and any(
            part.endswith(ext) for ext in gpt.VALID_IMAGE_EXTENSIONS
        ):
            image_urls.append(part)
            prompt_parts[i] = f"{{IMAGE #{images_seen + 1}}}"
            images_seen += 1
    prompt = " ".join(prompt_parts)

    new_message_content: list[MessageContent] = [
        {
            "type": "text",
            "text": prompt,
        }
    ]
    for image_url in [*image_urls, *attachment_urls]:
        new_message_content.append(_image_url_message_content(image_url))

    return prompt, new_message_content


class SendAndReceiveResponse(BaseModel):
    response_messages: list[str]


async def send_message_to_thread(
    bot: DiscordBot,
    message: discord.Message,
) -> SendAndReceiveResponse | Error:
    if bot.user is None:
        return Error(
            code=ErrorCode.NOT_READY,
            messages=["The server is not ready to handle requests"],
        )

    if message.author.id == bot.user.id:
        return Error(code=ErrorCode.SKIP, messages=[])

    if bot.user not in message.mentions:
        return Error(code=ErrorCode.SKIP, messages=[])

    if message.author.id not in DISCORD_USER_ID_WHITELIST:
        return Error(
            code=ErrorCode.UNAUTHORIZED,
            messages=["User is not authorized to use this bot"],
        )

    tracked_thread = await threads.fetch_one(message.channel.id)
    if tracked_thread is None:
        return Error(
            code=ErrorCode.NOT_FOUND,
            messages=["Thread not found"],
        )

    prompt = message.clean_content
    if prompt.startswith(f"{bot.user.mention} "):
        prompt = prompt.removeprefix(f"{bot.user.mention} ")

    author_name = get_author_name(message.author.name)
    prompt = f"{author_name}: {prompt}"

    async with message.channel.typing():
        thread_history = await thread_messages.fetch_many(thread_id=message.channel.id)

        message_history: list[gpt.Message] = [
            {
                "role": m.role,
                "content": [{"type": "text", "text": m.content}],
            }
            for m in thread_history[-tracked_thread.context_length :]
        ]

        prompt, new_message_content = _message_content_from_prompt(
            prompt,
            attachment_urls=[attachment.url for attachment in message.attachments],
        )

        message_history.append(
            {
                "role": "user",
                "content": new_message_content,
            }
        )

        gpt_response = await _make_gpt_request(
            message_history,
            tracked_thread.model,
        )
        if isinstance(gpt_response, Error):
            return gpt_response

        # Handle code blocks which may exceed the previous message.
        response_messages: list[str] = (
            discord_message_utils.smart_split_message_into_chunks(
                gpt_response.response_content,
                max_length=2000,
            )
        )

        await thread_messages.create(
            message.channel.id,
            prompt,
            discord_user_id=message.author.id,
            role="user",
            tokens_used=gpt_response.input_tokens,
        )

        await thread_messages.create(
            message.channel.id,
            gpt_response.response_content,
            discord_user_id=bot.user.id,
            role="assistant",
            tokens_used=gpt_response.output_tokens,
        )

    return SendAndReceiveResponse(
        response_messages=response_messages,
    )


async def send_message_without_context(
    bot: DiscordBot,
    interaction: discord.Interaction,
    message_content: str,
    model: gpt.AIModel,
) -> SendAndReceiveResponse | Error:
    if bot.user is None:
        return Error(
            code=ErrorCode.NOT_READY,
            messages=["The server is not ready to handle requests"],
        )

    if interaction.user.id == bot.user.id:
        return Error(code=ErrorCode.SKIP, messages=[])

    if interaction.user.id not in DISCORD_USER_ID_WHITELIST:
        return Error(
            code=ErrorCode.UNAUTHORIZED,
            messages=["User is not authorised to use this bot"],
        )

    # author_name = get_author_name(interaction.user.name)
    # prompt = f"{author_name}: {message_content}"

    # Since there is no context nor multi-user convos, we can just send the message as is
    prompt = message_content

    user_messages: list[MessageContent] = [
        {
            "type": "text",
            "text": prompt,
        }
    ]
    message_context: list[gpt.Message] = [
        {
            "role": "user",
            "content": user_messages,
        }
    ]

    gpt_response = await _make_gpt_request(message_context, model)
    if isinstance(gpt_response, Error):
        return gpt_response

    # TODO: Track input and output tokens here.

    response_messages: list[str] = (
        discord_message_utils.smart_split_message_into_chunks(
            gpt_response.response_content,
            max_length=2000,
        )
    )
    return SendAndReceiveResponse(response_messages=response_messages)
