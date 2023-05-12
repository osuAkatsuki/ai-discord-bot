#!/usr/bin/env python3
from typing import Any
import os.path
import sys

import discord
from typing import Literal
import openai
from openai.openai_object import OpenAIObject

# add .. to path
srv_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.append(srv_root)

from app import openai_pricing, state
from app.adapters import database
from app.repositories import thread_messages, threads
from app import settings


# make parent class for lifecycle hooks :/
# haven't been able to find anything reliable in discord.py for them
class Bot(discord.Client):
    async def start(self, *args, **kwargs) -> None:
        state.read_database = database.Database(
            database.dsn(
                scheme="postgresql",
                user=settings.READ_DB_USER,
                password=settings.READ_DB_PASS,
                host=settings.READ_DB_HOST,
                port=settings.READ_DB_PORT,
                database=settings.READ_DB_NAME,
            ),
            db_ssl=settings.READ_DB_USE_SSL,
            min_pool_size=settings.DB_POOL_MIN_SIZE,
            max_pool_size=settings.DB_POOL_MAX_SIZE,
        )
        await state.read_database.connect()

        state.write_database = database.Database(
            database.dsn(
                scheme="postgresql",
                user=settings.WRITE_DB_USER,
                password=settings.WRITE_DB_PASS,
                host=settings.WRITE_DB_HOST,
                port=settings.WRITE_DB_PORT,
                database=settings.WRITE_DB_NAME,
            ),
            db_ssl=settings.WRITE_DB_USE_SSL,
            min_pool_size=settings.DB_POOL_MIN_SIZE,
            max_pool_size=settings.DB_POOL_MAX_SIZE,
        )
        await state.write_database.connect()

        await super().start(*args, **kwargs)

    async def close(self, *args: Any, **kwargs: Any) -> None:
        await state.read_database.disconnect()
        await state.write_database.disconnect()

        await super().close(*args, **kwargs)


intents = discord.Intents.default()
intents.message_content = True


bot = Bot(intents=intents)
command_tree = discord.app_commands.CommandTree(bot)


# whitelist users who are allowed to use the askai command
class Users:
    cmyui = 285190493703503872
    rapha = 153954447247147018
    fkzoink = 332722012877357066


allowed_to_prompt_ai = {
    Users.cmyui,
    Users.rapha,
    Users.fkzoink,
}


@bot.event
async def on_ready():
    # NOTE: we can't use this as a lifecycle hook because
    # it may be called more than a single time.
    # our lifecycle hook is in our Bot class definition

    openai.api_key = settings.OPENAI_API_KEY
    await command_tree.sync()


def split_message(message: str, max_length: int) -> list[str]:
    if len(message) <= max_length:
        return [message]
    else:
        # split on last space before max_length
        split_index = message.rfind(" ", 0, max_length)
        if split_index == -1:
            split_index = max_length
        return [message[:split_index]] + split_message(
            message[split_index:], max_length
        )


@bot.event
async def on_message(message: discord.Message):
    # we only care about messages when

    # we are logged in
    if bot.user is None:
        return

    # they are not from us
    if message.author.id == bot.user.id:
        return

    # we are mentioned in the message
    if bot.user not in message.mentions:
        return

    # they are in a thread that we are tracking
    tracked_thread = await threads.fetch_one(message.channel.id)
    if tracked_thread is None:
        return

    prompt = message.content
    if prompt.startswith(f"{bot.user.mention} "):
        prompt = prompt.removeprefix(f"{bot.user.mention} ")

    prompt = f"{message.author.display_name}: {prompt}"

    await thread_messages.create(
        message.channel.id,
        prompt,
        role="user",
        tokens_used=0,
    )

    thread_history = await thread_messages.fetch_many(thread_id=message.channel.id)

    message_history = [
        {"role": m["role"], "content": m["content"]}
        # keep 10 messages before the prompt
        # TODO: allow some users to configure this per-thread
        for m in thread_history[-10:]
    ]
    message_history.append({"role": "user", "content": prompt})

    gpt_response = await openai.ChatCompletion.acreate(
        model=tracked_thread["model"],
        messages=message_history,
    )
    assert isinstance(gpt_response, OpenAIObject)  # TODO: can we do better?

    gpt_response_content = gpt_response.choices[0].message.content
    tokens_spent = gpt_response.usage.total_tokens

    for chunk in split_message(gpt_response_content, 2000):
        await message.channel.send(chunk)

    await thread_messages.create(
        message.channel.id,
        gpt_response_content,
        role="assistant",
        tokens_used=tokens_spent,
    )


@command_tree.command(name="cost")
async def cost(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.Thread):
        return

    thread = await threads.fetch_one(interaction.channel.id)
    if thread is None:
        return

    # TODO: display cost per user
    messages = await thread_messages.fetch_many(thread_id=interaction.channel.id)
    tokens_used = sum(m["tokens_used"] for m in messages)
    response_cost = openai_pricing.tokens_to_dollars(thread["model"], tokens_used)

    await interaction.response.send_message(
        f"The running total of this thread is ${response_cost:.5f} ({tokens_used} tokens) over {len(messages)} messages",
    )


@command_tree.command(name="model")
async def model(
    interaction: discord.Interaction,
    model: Literal["gpt-4", "gpt-3.5-turbo"],
):
    if not isinstance(interaction.channel, discord.Thread):
        return

    thread = await threads.fetch_one(interaction.channel.id)
    if thread is None:
        return

    await threads.partial_update(thread["thread_id"], model=model)

    await interaction.response.send_message(
        content="\n".join(
            (
                f"**Model switched to {model}**",
                f"Model Rate: ${openai_pricing.price_for_model(model)}/1000 tokens",
            )
        ),
    )


@command_tree.command(name="context")
async def context(
    interaction: discord.Interaction,
    context_length: int,
):
    if not isinstance(interaction.channel, discord.Thread):
        return

    thread = await threads.fetch_one(interaction.channel.id)
    if thread is None:
        return

    await threads.partial_update(
        thread["thread_id"],
        context_length=context_length,
    )

    await interaction.response.send_message(
        content="\n".join(
            (
                f"**Context length (messages length preserved) updated to {context_length}**",
                f"NOTE: longer context costs linearly more tokens, so please take care.",
            )
        )
    )


@command_tree.command(name="ai")
async def ai(
    interaction: discord.Interaction,
    model: Literal["gpt-4", "gpt-3.5-turbo"] = "gpt-4",
):
    if interaction.user.id not in allowed_to_prompt_ai:
        await interaction.response.send_message(
            "You are not allowed to use this command",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    assert interaction.channel is not None
    assert isinstance(interaction.channel, discord.TextChannel)

    thread_creation_message = await interaction.followup.send(
        content="\n".join(
            (
                f"**Opening a thread with {model}**",
                f"Model Rate: ${openai_pricing.price_for_model(model)}/1000 tokens",
            )
        ),
        ephemeral=True,
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
    )


if __name__ == "__main__":
    bot.run(settings.DISCORD_TOKEN)
