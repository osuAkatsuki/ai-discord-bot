import json
import re
import traceback
from hashlib import sha256
from pathlib import PurePosixPath
from typing import NamedTuple

import discord
from pydantic import BaseModel

from app import ai
from app import attachments
from app import discord_message_utils
from app import openai_functions
from app.ai import MessageContent
from app.errors import Error
from app.errors import ErrorCode
from app.models import DiscordBot
from app.repositories import ai_requests
from app.repositories import thread_messages
from app.repositories import threads
from app.usecases import cloud_execution

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


def _get_author_id(discord_user_id: int) -> str:
    return sha256(str(discord_user_id).encode()).hexdigest()[:8]


def get_author_name(discord_user_id: int) -> str:
    return f"User #{_get_author_id(discord_user_id)}"


class AIRequestResponse(NamedTuple):
    response_content: str
    input_tokens: int
    output_tokens: int
    files: tuple[ai.ExecutionFile, ...] = ()


MAX_FUNCTION_CALL_ROUNDS = 5


async def make_ai_request(
    message_history: list[ai.Message],
    model: ai.AIModel,
    *,
    thread_id: int,
    requester_id: int,
    reasoning_effort: str | None = None,
    web_search: bool = False,
    functions: list[ai.FunctionSchema] | None = None,
    execution_session_id: str | None = None,
) -> AIRequestResponse | Error:
    if functions is None:
        functions = openai_functions.get_full_openai_functions_schema()
    tool_rounds: list[ai.ToolRound] = []
    input_tokens = 0
    output_tokens = 0
    files: dict[str, ai.ExecutionFile] = {}

    for _ in range(MAX_FUNCTION_CALL_ROUNDS):
        try:
            response = await ai.send(
                model=model,
                messages=message_history,
                functions=functions,
                tool_rounds=tool_rounds,
                reasoning_effort=reasoning_effort,
                web_search=web_search,
                execution_session_id=execution_session_id,
            )
        except Exception as exc:
            traceback.print_exc()
            # NOTE: this is *generally* bad practice to expose this information
            # to end users, and should be removed if we are to deploy this app
            # more widely. Right now it's okay because it's a private bot.
            return Error(
                code=ErrorCode.UNEXPECTED_ERROR,
                messages=[
                    f"AI request failed with the following error:\n```\n{exc}```"
                ],
            )

        await ai_requests.create(
            thread_id=thread_id,
            requester_id=requester_id,
            model=model.value,
            usage=response.usage,
            estimated_cost_usd=response.estimated_cost_usd,
        )
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        files.update((file.id, file) for file in response.files)

        if not response.function_calls:
            assert response.response_content is not None
            return AIRequestResponse(
                response.response_content,
                input_tokens,
                output_tokens,
                tuple(files.values()),
            )

        results: list[ai.ToolResult] = []
        for function_call in response.function_calls:
            try:
                function_kwargs = json.loads(function_call.arguments)
                ai_function = openai_functions.ai_functions[function_call.name]
                function_response = await ai_function["callback"](**function_kwargs)
            except Exception:
                traceback.print_exc()
                function_response = {
                    "type": "text",
                    "text": "The tool failed. Explain that this information is unavailable.",
                }
            results.append(ai.ToolResult(call=function_call, content=function_response))
        tool_rounds.append(ai.ToolRound(response=response, results=results))

    return Error(
        code=ErrorCode.UNEXPECTED_ERROR,
        messages=["The model reached the tool-call limit. Try a more focused request."],
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

    def replace_image(match: re.Match[str]) -> str:
        url = match.group()
        if attachments.extension(url) not in attachments.IMAGE_EXTENSIONS:
            return url
        image_urls.append(url)
        return f"{{IMAGE #{len(image_urls)}}}"

    prompt = re.sub(r"https?://[^\s<>]+", replace_image, prompt)

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
    files: tuple[ai.ExecutionFile, ...] = ()


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

    async with cloud_execution.thread_lock(message.channel.id):
        return await _send_message_to_thread(bot, message)


async def _send_message_to_thread(
    bot: DiscordBot, message: discord.Message
) -> SendAndReceiveResponse | Error:
    assert bot.user is not None
    tracked_thread = await threads.fetch_one(message.channel.id)
    if tracked_thread is None:
        return Error(
            code=ErrorCode.NOT_FOUND,
            messages=["Thread not found"],
        )

    prompt = message.clean_content
    if prompt.startswith(f"{bot.user.mention} "):
        prompt = prompt.removeprefix(f"{bot.user.mention} ")

    author_name = get_author_name(message.author.id)
    prompt = f"{author_name}: {prompt}"

    async with message.channel.typing():
        thread_history = await thread_messages.fetch_many(thread_id=message.channel.id)

        recent_history = (
            thread_history[-tracked_thread.context_length :]
            if tracked_thread.context_length
            else []
        )
        message_history: list[ai.Message] = [
            {
                "role": m.role,
                "content": m.content_parts or [{"type": "text", "text": m.content}],
            }
            for m in recent_history
        ]

        prompt, new_message_content = _message_content_from_prompt(
            prompt,
            attachment_urls=[],
        )

        session = None
        notice = None
        try:
            if tracked_thread.cloud_execution:
                # Raw bytes are uploaded to the remote workspace, never executed here.
                if sum(
                    a.size for a in message.attachments
                ) >= attachments.MAX_REQUEST_FILE_BYTES or any(
                    a.size > attachments.MAX_ATTACHMENT_BYTES
                    for a in message.attachments
                ):
                    raise ValueError(
                        "Use files up to 20 MiB each, totaling less than 50 MiB."
                    )
                session, notice = await cloud_execution.prepare_session(
                    tracked_thread, message.author.id
                )
                if notice:
                    await message.channel.send(notice)
                    new_message_content.append({"type": "text", "text": notice})
                for attachment in message.attachments:
                    filename = (
                        f"{attachment.id}_{PurePosixPath(attachment.filename).name}"
                    )
                    path = await ai.upload_execution_file(
                        session.id, filename, await attachment.read()
                    )
                    new_message_content.append(
                        {
                            "type": "text",
                            "text": f"Uploaded file in the cloud workspace: {path}",
                        }
                    )
                    if (
                        attachments.extension(attachment.filename)
                        in attachments.IMAGE_EXTENSIONS
                    ):
                        new_message_content.append(
                            _image_url_message_content(attachment.url)
                        )
            else:
                new_message_content.extend(
                    attachments.content_from_attachments(message.attachments)
                )
        except ValueError as exc:
            return Error(code=ErrorCode.UNEXPECTED_ERROR, messages=[str(exc)])
        except Exception:
            traceback.print_exc()
            return Error(
                code=ErrorCode.UNEXPECTED_ERROR,
                messages=[
                    "Could not prepare the attachments or cloud workspace. Check file sizes, model access, and the configured network allowlist."
                ],
            )

        message_history.append(
            {
                "role": "user",
                "content": new_message_content,
            }
        )

        try:
            response = await make_ai_request(
                message_history,
                tracked_thread.model,
                thread_id=message.channel.id,
                requester_id=message.author.id,
                reasoning_effort=tracked_thread.reasoning_effort,
                web_search=tracked_thread.web_search,
                execution_session_id=session.id if session else None,
            )
        finally:
            # Even an unsuccessful model call can consume a container session.
            if session:
                await cloud_execution.record_cost(
                    tracked_thread.thread_id, message.author.id, session
                )
        if isinstance(response, Error):
            return response

        # Handle code blocks which may exceed the previous message.
        response_messages: list[str] = (
            discord_message_utils.smart_split_message_into_chunks(
                response.response_content,
                max_length=2000,
            )
        )

        await thread_messages.create(
            message.channel.id,
            prompt,
            discord_user_id=message.author.id,
            role="user",
            tokens_used=response.input_tokens,
            content_parts=new_message_content,
        )

        await thread_messages.create(
            message.channel.id,
            response.response_content,
            discord_user_id=bot.user.id,
            role="assistant",
            tokens_used=response.output_tokens,
        )

    return SendAndReceiveResponse(
        response_messages=response_messages,
        files=response.files,
    )


async def send_message_without_context(
    bot: DiscordBot,
    interaction: discord.Interaction,
    message_content: str,
    model: ai.AIModel,
    *,
    reasoning_effort: str | None = None,
    web_search: bool = False,
    attachment: discord.Attachment | None = None,
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

    prompt, user_messages = _message_content_from_prompt(prompt, [])
    try:
        user_messages.extend(
            attachments.content_from_attachments([attachment] if attachment else [])
        )
    except ValueError as exc:
        return Error(code=ErrorCode.UNEXPECTED_ERROR, messages=[str(exc)])
    message_context: list[ai.Message] = [
        {
            "role": "user",
            "content": user_messages,
        }
    ]

    response = await make_ai_request(
        message_context,
        model,
        thread_id=interaction.channel_id or interaction.id,
        requester_id=interaction.user.id,
        reasoning_effort=reasoning_effort,
        web_search=web_search,
    )
    if isinstance(response, Error):
        return response

    await threads.create(
        interaction.id,
        initiator_user_id=interaction.user.id,
        model=model,
        context_length=0,
    )

    await thread_messages.create(
        interaction.id,
        prompt,
        discord_user_id=interaction.user.id,
        role="user",
        tokens_used=response.input_tokens,
        content_parts=user_messages,
    )

    await thread_messages.create(
        interaction.id,
        response.response_content,
        discord_user_id=bot.user.id,
        role="assistant",
        tokens_used=response.output_tokens,
    )

    response_messages: list[str] = (
        discord_message_utils.smart_split_message_into_chunks(
            response.response_content,
            max_length=2000,
        )
    )
    return SendAndReceiveResponse(response_messages=response_messages)
