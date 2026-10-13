#!/usr/bin/env python3
import io
import logging
import os.path
import sys
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import PurePosixPath
from typing import Literal

import discord.abc

# add .. to path
srv_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.append(srv_root)

from app.errors import Error
from app.models import DiscordBot
from app.usecases import ai_conversations
from app.usecases import cloud_execution


from app import ai
from app import attachments
from app import discord_message_utils, openai_pricing
from app import settings
from app.repositories import ai_requests
from app.repositories import thread_messages
from app.repositories import threads

LOGGER = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 100


intents = discord.Intents.default()
intents.message_content = True

allowed_installs = discord.app_commands.AppInstallationType(guild=True, user=True)
allowed_contexts = discord.app_commands.AppCommandContext(
    guild=True,
    dm_channel=True,
    private_channel=True,
)

bot = DiscordBot(intents=intents)
command_tree = discord.app_commands.CommandTree(
    bot,
    allowed_contexts=allowed_contexts,
    allowed_installs=allowed_installs,
)


def command_name(command_name: str) -> str:
    """Prepends command names with "dev" in test env(s) to avoid overlap."""
    if settings.APP_ENV != "production":
        command_name = f"dev{command_name}"
    return command_name


@bot.event
async def on_ready():
    # NOTE: we can't use this as a lifecycle hook because
    # it may be called more than a single time.
    # our lifecycle hook is in our Bot class definition
    await command_tree.sync()


@bot.event
async def on_message(message: discord.Message):
    data = await ai_conversations.send_message_to_thread(bot, message)
    if isinstance(data, Error):
        for msg in data.messages:
            await message.channel.send(msg)
        return

    for msg in data.response_messages:
        await message.channel.send(msg)
    upload_limit = message.guild.filesize_limit if message.guild else 10 * 1024 * 1024
    for file in data.files[:10]:
        try:
            content = await ai.download_execution_file(file, upload_limit)
        except Exception:
            LOGGER.exception("Cloud artifact download failed")
            await message.channel.send(
                "A generated file could not be attached: it may exceed the channel's "
                "upload limit or its cloud workspace may have expired."
            )
            continue
        filename = PurePosixPath(file.filename).name or "output.bin"
        with io.BytesIO(content) as buffer:
            await message.channel.send(file=discord.File(buffer, filename=filename))
    if len(data.files) > 10:
        await message.channel.send(
            "Only the first 10 files were attached. Ask for a ZIP to download more."
        )


MODEL_CHOICES = [
    discord.app_commands.Choice(name=model.value, value=model.value)
    for model in ai.SELECTABLE_MODELS
]


def _model_description(
    model: ai.AIModel,
    reasoning_effort: str | None,
    web_search: bool,
    cloud_execution: bool = False,
) -> str:
    return "\n".join(
        (
            f"Model: **{model.value}**",
            f"Reasoning: {reasoning_effort or 'low'} | Web search: {'on' if web_search else 'off'}",
            f"Standard input/output: ${openai_pricing.input_price_per_million_tokens(model)}/${openai_pricing.output_price_per_million_tokens(model)} per 1M tokens.",
            "Cached input, cache writes, and long context have separate rates. Search costs $0.01 per call plus tokens.",
            (
                "Cloud execution: on; 1 GB workspace estimated at $0.03 per 20-minute session. "
                "Files expire after 20 minutes of inactivity. Configure/reset with /execution."
                if cloud_execution
                else "Cloud execution: off (enable with /execution)."
            ),
        )
    )


async def _send_cost_report(
    interaction: discord.Interaction,
    *,
    thread_id: int | None = None,
    created_at_gte: datetime | None = None,
) -> None:
    rows = await ai_requests.costs_by_requester(thread_id, created_at_gte)
    lines = [
        "**Estimated requester costs (USD)**",
        "Includes recorded input, output/reasoning, search, image, and estimated cloud-container usage.",
        "Earlier requests without usage records are excluded; this is not a provider invoice.",
        "",
    ]
    for row in rows:
        lines.append(f"<@{row['requester_id']}>: ${row['cost']:.5f}")
    lines.append(f"\n**Recorded total: ${sum(row['cost'] for row in rows):.5f}**")
    unpriced = sum(row["unpriced_requests"] for row in rows)
    if unpriced:
        lines.append(
            f"{unpriced} request(s) had no usage data and are not included in the total."
        )
    for chunk in discord_message_utils.split_message_into_chunks(
        "\n".join(lines), max_length=2000
    ):
        await interaction.followup.send(chunk)


@command_tree.command(name=command_name("monthlycost"))
async def monthlycost(interaction: discord.Interaction):
    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    await _send_cost_report(
        interaction, created_at_gte=datetime.now(timezone.utc) - timedelta(days=30)
    )


@command_tree.command(name=command_name("threadcost"))
async def threadcost(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "This command can only be used in a thread",
            ephemeral=True,
        )
        return

    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    await _send_cost_report(interaction, thread_id=interaction.channel.id)


@command_tree.command(name=command_name("model"))
@discord.app_commands.choices(model=MODEL_CHOICES)
async def model(
    interaction: discord.Interaction,
    model: str,
    reasoning: ai.ReasoningEffort | None = None,
    web_search: bool = False,
):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "This command can only be used in threads.",
            ephemeral=True,
        )
        return

    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    thread = await threads.fetch_one(interaction.channel.id)
    if thread is None:
        await interaction.followup.send(
            "This thread is not tracked by the bot.",
            ephemeral=True,
        )
        return

    selected_model = ai.AIModel(model)
    try:
        ai.validate_options(selected_model, reasoning, web_search)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await threads.set_model_options(
        thread.thread_id, selected_model, reasoning, web_search
    )

    await interaction.followup.send(
        _model_description(
            selected_model, reasoning, web_search, thread.cloud_execution
        )
    )


@command_tree.command(name=command_name("execution"))
async def execution(
    interaction: discord.Interaction,
    enabled: bool,
    reset: bool = False,
):
    """Enable cloud commands for a thread; disabling or resetting deletes its workspace."""
    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command", ephemeral=True
        )
        return
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "This command can only be used in threads.", ephemeral=True
        )
        return
    await interaction.response.defer()
    async with cloud_execution.thread_lock(interaction.channel.id):
        thread = await threads.fetch_one(interaction.channel.id)
        if thread is None:
            await interaction.followup.send(
                "This thread is not tracked by the bot.", ephemeral=True
            )
            return
        try:
            await cloud_execution.configure(thread, enabled, reset)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Cloud execution configuration failed")
            await interaction.followup.send(
                "Could not update the cloud workspace. Try again.", ephemeral=True
            )
            return
    if enabled:
        domains = ", ".join(settings.OPENAI_EXECUTION_ALLOWED_DOMAINS) or "disabled"
        text = (
            "Cloud execution is on for this shared thread. Commands run in OpenAI's "
            "1 GB container; files and installed packages persist until it expires "
            "after 20 minutes of inactivity. All authorized participants share this workspace. "
            "Container estimates start at $0.03 per 20-minute session, plus model tokens. "
            f"Outbound network domains: {domains}."
        )
        if reset:
            text += (
                " The previous workspace was deleted; the next request starts fresh."
            )
    else:
        text = "Cloud execution is off. Any previous cloud workspace was deleted."
    await interaction.followup.send(text)


@command_tree.command(name=command_name("context"))
async def context(
    interaction: discord.Interaction,
    context_length: int,
):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "This command can only be used in threads.",
            ephemeral=True,
        )
        return

    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    if not 0 <= context_length <= MAX_CONTENT_LENGTH:
        await interaction.response.send_message(
            f"Context length must be between 0 and {MAX_CONTENT_LENGTH}",
        )
        return

    await interaction.response.defer()

    thread = await threads.fetch_one(interaction.channel.id)
    if thread is None:
        await interaction.followup.send(
            "This thread is not tracked by the bot.",
            ephemeral=True,
        )
        return

    await threads.partial_update(
        thread.thread_id,
        context_length=context_length,
    )

    await interaction.followup.send(
        content="\n".join(
            (
                f"**Context length (messages length preserved) updated to {context_length}**",
                "NOTE: longer context costs linearly more tokens, so please take care.",
            )
        )
    )


@command_tree.command(name=command_name("summarize"))
async def summarize(
    interaction: discord.Interaction,
    # support num of messages OR a starting location (message id)
    num_messages: int = MAX_CONTENT_LENGTH,
    # discord calls message ids "invalid integers". maybe <=i32 or something?
    start_message: str | None = None,
    end_message: str | None = None,
):
    if not isinstance(interaction.channel, discord.abc.Messageable):
        return

    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    limit = min(num_messages, MAX_CONTENT_LENGTH)

    start_message_id = None
    if start_message is not None:
        start_message_id = int(start_message)

    end_message_id = None
    if end_message is not None:
        end_message_id = int(end_message)
        tracking = False
    else:
        tracking = True

    messages: list[ai.Message] = []

    async for message in interaction.channel.history(limit=limit):
        content = message.clean_content
        if not content:  # ignore empty messages (e.g. only images)
            continue

        author_name = ai_conversations.get_author_name(message.author.id)

        if tracking:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"{author_name}: {content}",
                        }
                    ],
                }
            )
        elif end_message_id is not None:
            if message.id == end_message_id:
                tracking = True

        if start_message_id is not None and message.id == start_message_id:
            break

    messages = messages[::-1]  # reverse it

    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Could you summarize the above conversation?",
                }
            ],
        }
    )

    result = await ai_conversations.make_ai_request(
        messages,
        ai.DEFAULT_AI_MODEL,
        thread_id=interaction.channel.id,
        requester_id=interaction.user.id,
        functions=[],
    )
    if isinstance(result, Error):
        for message in result.messages:
            await interaction.followup.send(message)
        return
    for chunk in discord_message_utils.split_message_into_chunks(
        result.response_content, max_length=2000
    ):
        await interaction.followup.send(chunk)


@command_tree.command(name=command_name("ai"))
@discord.app_commands.choices(model=MODEL_CHOICES)
async def create_ai_thread(
    interaction: discord.Interaction,
    model: str = ai.DEFAULT_AI_MODEL.value,
    reasoning: ai.ReasoningEffort | None = None,
    web_search: bool = False,
    cloud_execution: bool = False,
):
    if (
        interaction.channel is not None
        and interaction.channel.type == discord.ChannelType.private
    ):
        await interaction.response.send_message(
            "This command cannot be used in private messages",
            ephemeral=True,
        )
        return

    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.followup.send(
            "This command can only be used in text channels.",
            ephemeral=True,
        )
        return

    selected_model = ai.AIModel(model)
    try:
        ai.validate_options(selected_model, reasoning, web_search)
        if cloud_execution:
            ai.validate_execution(selected_model)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    thread_creation_message = await interaction.followup.send(
        content=_model_description(
            selected_model, reasoning, web_search, cloud_execution
        ),
        wait=True,
    )

    thread_creation_message.guild = interaction.guild  # needed for thread. weird.
    thread = await thread_creation_message.create_thread(
        name=f"@{interaction.user.display_name}'s AI Thread",
    )
    await threads.create(
        thread.id,
        initiator_user_id=interaction.user.id,
        model=selected_model,
        reasoning_effort=reasoning,
        web_search=web_search,
        cloud_execution=cloud_execution,
        context_length=5,  # messages
    )


@command_tree.command(name=command_name("transcript"))
async def transcript(
    interaction: discord.Interaction,
    context_length: int | None = None,
):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "This command can only be used in threads.",
            ephemeral=True,
        )
        return

    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    thread = await threads.fetch_one(interaction.channel.id)
    if thread is None:
        await interaction.followup.send(
            "This thread is not tracked by the bot.",
            ephemeral=True,
        )
        return

    if context_length is None:
        current_thread_messages = await thread_messages.fetch_many(
            thread_id=interaction.channel.id
        )
    else:
        current_thread_messages = await thread_messages.fetch_many(
            thread_id=interaction.channel.id,
            page=1,
            page_size=context_length,
            sort_order="desc",
        )
        current_thread_messages = current_thread_messages[::-1]

    transcript_content = "\n".join(
        f"[{msg.created_at:%d/%m/%Y %I:%M:%S%p}] {msg.content}"
        for msg in current_thread_messages
    )
    with io.BytesIO(transcript_content.encode()) as f:
        await interaction.followup.send(
            content=f"{interaction.user.mention}: here is your AI transcript for this thread.",
            file=discord.File(f, filename="transcript.txt"),
        )


@command_tree.command(name=command_name("query"))
@discord.app_commands.choices(model=MODEL_CHOICES)
async def query(
    interaction: discord.Interaction,
    query: str,
    model: str = ai.DEFAULT_AI_MODEL.value,
    reasoning: ai.ReasoningEffort | None = None,
    web_search: bool = False,
    attachment: discord.Attachment | None = None,
):
    """Query a model without any context."""

    await interaction.response.defer()

    result = await ai_conversations.send_message_without_context(
        bot,
        interaction,
        query,
        ai.AIModel(model),
        reasoning_effort=reasoning,
        web_search=web_search,
        attachment=attachment,
    )

    # I do not think interactions allow multiple messages.
    messages_to_send: list[str] = []
    if isinstance(result, Error):
        messages_to_send = result.messages
    else:
        messages_to_send = result.response_messages

    # I have no idea whether they actually allow you to send multiple follow-ups.
    for message_text in messages_to_send:
        await interaction.followup.send(message_text)


@command_tree.command(name=command_name("image"))
async def image(
    interaction: discord.Interaction,
    prompt: str,
    reference: discord.Attachment | None = None,
    model: ai.ImageModel | None = None,
    quality: Literal["low", "medium", "high", "xhigh", "max"] = "medium",
    size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1024x1024",
    background: Literal["auto", "opaque", "transparent"] = "auto",
):
    """Generate an image, or edit an attached reference image."""
    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command", ephemeral=True
        )
        return
    await interaction.response.defer()
    reference_image = None
    if reference is not None:
        suffix = attachments.extension(reference.filename)
        if (
            suffix not in {".png", ".jpg", ".jpeg", ".webp"}
            or reference.size > attachments.MAX_ATTACHMENT_BYTES
        ):
            await interaction.followup.send(
                "Use a PNG, JPEG, or WebP reference up to 20 MiB."
            )
            return
    selected_model = model or (
        ai.ImageModel.OPENAI_SUNBURST if reference else ai.ImageModel.OPENAI_FLARE
    )
    try:
        if reference is not None:
            reference_image = (
                reference.filename,
                await reference.read(),
                reference.content_type or "application/octet-stream",
            )
        result = await ai.generate_image(
            model=selected_model,
            prompt=prompt,
            quality=quality,
            size=size,
            background=background,
            reference_image=reference_image,
        )
    except Exception:
        LOGGER.exception("Image request failed")
        await interaction.followup.send(
            "The image request failed. Check model access and try again."
        )
        return
    await ai_requests.create(
        thread_id=interaction.channel_id or interaction.id,
        requester_id=interaction.user.id,
        model=selected_model.value,
        usage=result.usage,
        estimated_cost_usd=result.estimated_cost_usd,
    )
    if not result.images:
        await interaction.followup.send(
            "The model returned no image. Try revising the prompt."
        )
        return
    upload_limit = (
        interaction.guild.filesize_limit if interaction.guild else 10 * 1024 * 1024
    )
    for image_data in result.images:
        if len(image_data) > upload_limit:
            await interaction.followup.send(
                "The generated image exceeds this channel's upload limit. Try a smaller size or lower quality. The generation was still billed."
            )
            continue
        with io.BytesIO(image_data) as f:
            await interaction.followup.send(file=discord.File(f, filename="image.webp"))


if __name__ == "__main__":
    bot.run(settings.DISCORD_TOKEN)
