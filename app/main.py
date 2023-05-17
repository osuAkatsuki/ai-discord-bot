#!/usr/bin/env python3
import os.path
import sys
from typing import Any
from typing import Literal
import io

import discord.abc
import openai
from openai.openai_object import OpenAIObject

# add .. to path
srv_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.append(srv_root)

from app import openai_pricing, state
from app.adapters import database
from app.repositories import thread_messages, threads
from app import settings

MAX_CONTENT_LENGTH = 100


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
        print("closing")
        await state.read_database.disconnect()
        await state.write_database.disconnect()

        await super().close(*args, **kwargs)
        print("closed")


intents = discord.Intents.default()
intents.message_content = True


bot = Bot(intents=intents)
command_tree = discord.app_commands.CommandTree(bot)


# whitelist users who are allowed to use the askai command
class Users:
    cmyui = 285190493703503872
    rapha = 153954447247147018
    fkzoink = 332722012877357066
    flame = 347459855449325570
    mistral = 249596453457100801


allowed_to_prompt_ai = {
    Users.cmyui,
    Users.rapha,
    Users.fkzoink,
    Users.flame,
    Users.mistral,
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

    # has permissions to use this bot
    if message.author.id not in allowed_to_prompt_ai:
        await message.channel.send("You are not allowed to use this command")
        return

    # they are in a thread that we are tracking
    tracked_thread = await threads.fetch_one(message.channel.id)
    if tracked_thread is None:
        return

    prompt = message.clean_content
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
        for m in thread_history[-tracked_thread["context_length"] :]
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

    if interaction.user.id not in allowed_to_prompt_ai:
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

    # TODO: display cost per user
    messages = await thread_messages.fetch_many(thread_id=interaction.channel.id)
    tokens_used = sum(m["tokens_used"] for m in messages)
    response_cost = openai_pricing.tokens_to_dollars(thread["model"], tokens_used)

    await interaction.followup.send(
        f"The running total of this thread is ${response_cost:.5f} ({tokens_used} tokens) over {len(messages)} messages",
    )


@command_tree.command(name="model")
async def model(
    interaction: discord.Interaction,
    model: Literal["gpt-4", "gpt-3.5-turbo"],
):
    if not isinstance(interaction.channel, discord.Thread):
        return

    if interaction.user.id not in allowed_to_prompt_ai:
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

    await threads.partial_update(thread["thread_id"], model=model)

    await interaction.followup.send(
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

    if interaction.user.id not in allowed_to_prompt_ai:
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
        thread["thread_id"],
        context_length=context_length,
    )

    await interaction.followup.send(
        content="\n".join(
            (
                f"**Context length (messages length preserved) updated to {context_length}**",
                f"NOTE: longer context costs linearly more tokens, so please take care.",
            )
        )
    )


@command_tree.command(name="summarize")
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

    if interaction.user.id not in allowed_to_prompt_ai:
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

    messages = []

    async for message in interaction.channel.history(limit=limit):
        content = message.clean_content
        if not content:  # ignore empty messages (e.g. only images)
            continue

        author = message.author.display_name

        if tracking:
            messages.append({"role": "user", "content": f"{author}: {content}"})
        elif end_message_id is not None:
            if message.id == end_message_id:
                tracking = True

        if start_message_id is not None and message.id == start_message_id:
            break

    messages = messages[::-1]  # reverse it

    messages.append(
        {
            "role": "user",
            "content": "Could you summarize the above conversation?",
        }
    )

    gpt_response = await openai.ChatCompletion.acreate(
        model="gpt-4",
        messages=messages,
    )
    assert isinstance(gpt_response, OpenAIObject)  # TODO: can we do better?

    gpt_response_content = gpt_response.choices[0].message.content
    # tokens_spent = gpt_response.usage.total_tokens

    for chunk in split_message(gpt_response_content, 2000):
        await interaction.followup.send(chunk)


@command_tree.command(name="ai")
async def ai(
    interaction: discord.Interaction,
    model: Literal["gpt-4", "gpt-3.5-turbo"] = "gpt-4",
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


@command_tree.command(name="transcript")
async def transcript(
    interaction: discord.Interaction,
    context_length: int | None = None,
):
    if not isinstance(interaction.channel, discord.Thread):
        return

    if interaction.user.id not in allowed_to_prompt_ai:
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
        "[{created_at:%d/%m/%Y %I:%M:%S%p}] {content}".format(**msg)
        for msg in current_thread_messages
    )
    with io.BytesIO(transcript_content.encode()) as f:
        await interaction.followup.send(
            content=f"{interaction.user.mention}: here is your AI transcript for this thread.",
            file=discord.File(f),
        )


if __name__ == "__main__":
    bot.run(settings.DISCORD_TOKEN)
