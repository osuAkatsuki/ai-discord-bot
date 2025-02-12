#!/usr/bin/env python3
import io
import logging
import os.path
import sys
from collections import defaultdict
from datetime import datetime
from datetime import timedelta

import discord.abc

# add .. to path
srv_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.append(srv_root)

from app.errors import Error
from app.models import DiscordBot
from app.usecases import ai_conversations


from app import discord_message_utils, openai_pricing
from app import settings
from app.adapters.openai import gpt
from app.repositories import thread_messages
from app.repositories import threads


LOGGER = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 100


intents = discord.Intents.default()
intents.message_content = True

bot = DiscordBot(intents=intents)
command_tree = discord.app_commands.CommandTree(bot)


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


async def _calculate_per_user_costs(
    thread_id: int | None = None, created_at_gte: datetime | None = None
) -> dict[int, float]:
    messages = await thread_messages.fetch_many(
        thread_id=thread_id,
        created_at_gte=created_at_gte,
    )
    threads_cache: dict[int, threads.Thread] = {}
    per_user_per_model_input_tokens: dict[int, dict[gpt.AIModel, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for message in messages:
        if message.role != "user":
            continue

        thread_id = message.thread_id
        thread = threads_cache.get(thread_id)
        if thread is None:
            thread = await threads.fetch_one(thread_id)
            if thread is None:
                LOGGER.warning(
                    "Thread %s not found",
                    thread_id,
                    extra={"thread_id": thread_id},
                )
                continue
            threads_cache[thread_id] = thread

        user_id = message.discord_user_id
        per_model_input_tokens = per_user_per_model_input_tokens[user_id]
        per_model_input_tokens[thread.model] += message.tokens_used

    per_user_cost: dict[int, float] = defaultdict(float)
    for user_id, per_model_tokens in per_user_per_model_input_tokens.items():
        for model, input_tokens in per_model_tokens.items():
            user_cost = per_user_cost[user_id]
            user_cost += openai_pricing.tokens_to_dollars(
                model, input_tokens, output_tokens=0
            )
            per_user_cost[user_id] = user_cost

    return per_user_cost


@command_tree.command(name=command_name("monthlycost"))
async def monthlycost(interaction: discord.Interaction):
    if interaction.user.id not in ai_conversations.DISCORD_USER_ID_WHITELIST:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    per_user_cost = await _calculate_per_user_costs(
        created_at_gte=datetime.now() - timedelta(days=30)
    )
    response_cost = sum(per_user_cost.values())

    message_chunks = [
        "**Monthly Cost Breakdown**",
        "**----------------------**",
        "",
    ]
    for user_id, cost in per_user_cost.items():
        user = await bot.fetch_user(user_id)
        message_chunks.append(f"{user.mention}: ${cost:.5f}")

    message_chunks.append("")
    message_chunks.append(f"**Total Cost: ${response_cost:.5f}**")

    await interaction.followup.send("\n".join(message_chunks))


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

    per_user_cost = await _calculate_per_user_costs(thread_id=interaction.channel.id)
    response_cost = sum(per_user_cost.values())

    message_chunks = [
        "**Thread Cost Breakdown**",
        "**---------------------**",
        "",
    ]
    for user_id, cost in per_user_cost.items():
        user = await bot.fetch_user(user_id)
        message_chunks.append(f"{user.mention}: ${cost:.5f}")

    message_chunks.append("")
    message_chunks.append(f"**Total Cost: ${response_cost:.5f}**")

    await interaction.followup.send("\n".join(message_chunks))


@command_tree.command(name=command_name("model"))
async def model(
    interaction: discord.Interaction,
    model: gpt.AIModel,
):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.followup.send(
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

    await threads.partial_update(thread.thread_id, model=model)

    await interaction.followup.send(
        content="\n".join(
            (
                f"**Model switched to {model}**",
                f"Model Input Rate: ${openai_pricing.input_price_per_million_tokens(model)}/1M tokens",
                f"Model Output Rate: ${openai_pricing.output_price_per_million_tokens(model)}/1M tokens",
            )
        ),
    )


@command_tree.command(name=command_name("context"))
async def context(
    interaction: discord.Interaction,
    context_length: int,
):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.followup.send(
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

    if context_length > MAX_CONTENT_LENGTH:
        await interaction.response.send_message(
            f"Context length cannot be greater than {MAX_CONTENT_LENGTH}",
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

    limit = max(num_messages, MAX_CONTENT_LENGTH)

    start_message_id = None
    if start_message is not None:
        start_message_id = int(start_message)

    end_message_id = None
    if end_message is not None:
        end_message_id = int(end_message)
        tracking = False
    else:
        tracking = True

    messages: list[gpt.Message] = []

    async for message in interaction.channel.history(limit=limit):
        content = message.clean_content
        if not content:  # ignore empty messages (e.g. only images)
            continue

        author_name = ai_conversations.get_author_name(message.author.name)

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

    try:
        gpt_response = await gpt.send(
            model=gpt.AIModel.DEEPSEEK_REASONER,
            messages=messages,
        )
    except Exception as exc:
        # NOTE: this is *generally* bad practice to expose this information
        # to end users, and should be removed if we are to deploy this app
        # more widely. Right now it's okay because it's a private bot.
        await interaction.followup.send(
            f"Request to OpenAI failed with the following error:\n```\n{exc}```"
        )
        return

    gpt_response_content = gpt_response.choices[0].message.content
    assert gpt_response_content is not None

    # tokens_spent = gpt_response.usage.total_tokens

    for chunk in discord_message_utils.split_message_into_chunks(
        gpt_response_content, max_length=2000
    ):
        await interaction.followup.send(chunk)


@command_tree.command(name=command_name("ai"))
async def ai(
    interaction: discord.Interaction,
    model: gpt.AIModel = gpt.AIModel.DEEPSEEK_REASONER,
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

    thread_creation_message = await interaction.followup.send(
        content="\n".join(
            (
                f"**Opening a thread with {model}**",
                f"Model Input Rate: ${openai_pricing.input_price_per_million_tokens(model)}/1M tokens",
                f"Model Output Rate: ${openai_pricing.output_price_per_million_tokens(model)}/1M tokens",
            )
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
        model=model,
        context_length=5,  # messages
    )


@command_tree.command(name=command_name("transcript"))
async def transcript(
    interaction: discord.Interaction,
    context_length: int | None = None,
):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.followup.send(
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

    current_thread_messages = await thread_messages.fetch_many(
        thread_id=interaction.channel.id,
        page_size=context_length,
    )

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
async def query(
    interaction: discord.Interaction,
    query: str,
    model: gpt.AIModel = gpt.AIModel.DEEPSEEK_REASONER,
):
    """Query a model without any context."""

    await interaction.response.defer()

    result = await ai_conversations.send_message_without_context(
        bot,
        interaction,
        query,
        model,
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


if __name__ == "__main__":
    bot.run(settings.DISCORD_TOKEN)
