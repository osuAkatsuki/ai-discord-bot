import json
import traceback

import discord
from pydantic import BaseModel

from app import discord_message_utils
from app import openai_functions
from app.adapters.openai import gpt
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
    # Super
    332722012877357066,  # fkzoink
}


def _get_author_id(author_name: str) -> int:
    # get a 3 digit highly unique id
    return hash(author_name) % 1000


def get_author_name(discord_author_name: str) -> str:
    return f"User #{_get_author_id(discord_author_name)}"


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
                "role": m["role"],
                "content": [{"type": "text", "text": m["content"]}],
            }
            for m in thread_history[-tracked_thread["context_length"] :]
        ]

        # Append this new message (along w/ any attachments) to the history
        message_history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    }
                ],
            }
        )
        if message.attachments:
            for attachment in message.attachments:
                message_history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": attachment.url},
                            }
                        ],
                    }
                )

        functions = openai_functions.get_full_openai_functions_schema()
        try:
            gpt_response = await gpt.send(
                model=tracked_thread["model"],
                messages=message_history,
                functions=functions,
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

        gpt_choice = gpt_response.choices[0]
        gpt_message = gpt_choice.message

        if gpt_choice.finish_reason == "stop":
            assert gpt_message.content is not None
            gpt_response_content: str = gpt_message.content

            message_history.append(
                {
                    "role": gpt_message.role,
                    "content": [
                        {
                            "type": "text",
                            "text": gpt_message.content,
                        }
                    ],
                }
            )
        elif (
            gpt_choice.finish_reason == "function_call"
            and gpt_message.function_call is not None
        ):
            function_name = gpt_message.function_call.name
            function_kwargs = json.loads(gpt_message.function_call.arguments)

            ai_function = openai_functions.ai_functions[function_name]
            function_response = await ai_function["callback"](**function_kwargs)

            # send function response back to gpt for the final response
            # TODO: could it call another function?
            #       i think this should they may expect/support recursive calls
            message_history.append(
                {
                    "role": "function",
                    "name": function_name,
                    "content": [function_response],
                }
            )
            try:
                gpt_response = await gpt.send(
                    model=tracked_thread["model"],
                    messages=message_history,
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

            assert gpt_response.choices[0].message.content is not None
            gpt_response_content = gpt_response.choices[0].message.content

        else:
            raise NotImplementedError(
                f"Unknown chatgpt finish reason: {gpt_choice.finish_reason}"
            )

        assert gpt_response.usage is not None
        input_tokens = gpt_response.usage.prompt_tokens
        output_tokens = gpt_response.usage.completion_tokens

        # Handle code blocks which may exceed the previous message.
        response_messages: list[str] = []
        code_block_language: str | None = None
        for chunk in discord_message_utils.split_message_into_chunks(
            gpt_response_content,
            max_length=1985,
        ):
            if code_block_language is not None:
                chunk = f"```{code_block_language}\n" + chunk
                code_block_language = None

            code_block_language = (
                discord_message_utils.get_unclosed_code_block_language(chunk)
            )
            if code_block_language is not None:
                chunk += "\n```"

            response_messages.append(chunk)

        await thread_messages.create(
            message.channel.id,
            prompt,
            discord_user_id=message.author.id,
            role="user",
            tokens_used=input_tokens,
        )

        await thread_messages.create(
            message.channel.id,
            gpt_response_content,
            discord_user_id=bot.user.id,
            role="assistant",
            tokens_used=output_tokens,
        )

    return SendAndReceiveResponse(
        response_messages=response_messages,
    )
