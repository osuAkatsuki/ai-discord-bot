#!/usr/bin/env python3
import io
import json
import os.path
import sys
from typing import Any
from typing import Literal

import discord.abc
import httpx

# add .. to path
srv_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.append(srv_root)

from app import openai_functions
from app import openai_pricing
from app import settings
from app import initial_setups
from app import state
from app.adapters.openai import gpt
from app.repositories import thread_messages
from app.repositories import threads


MAX_CONTENT_LENGTH = 100


# make parent class for lifecycle hooks :/
# haven't been able to find anything reliable in discord.py for them
class Bot(discord.Client):
    async def start(self, *args, **kwargs) -> None:
        await state.read_database.connect()
        await state.write_database.connect()

        # also make a read-only connection to akatsuki's
        # mysql database for ai-assisted analysis
        await state.akatsuki_read_database.connect()

        state.http_client = httpx.AsyncClient()

        await super().start(*args, **kwargs)

    async def close(self, *args: Any, **kwargs: Any) -> None:
        await state.read_database.disconnect()
        await state.write_database.disconnect()

        await state.akatsuki_read_database.disconnect()

        await state.http_client.aclose()

        await super().close(*args, **kwargs)


intents = discord.Intents.default()
intents.message_content = True


bot = Bot(intents=intents)
command_tree = discord.app_commands.CommandTree(bot)


DISCORD_USER_ID_WHITELIST = {
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
    153954447247147018,  # rapha
    190278149030936576,  # randomize
    249596453457100801,  # mistral
    # Super
    332722012877357066,  # fkzoink
}


def command_name(name: str) -> str:
    if settings.APP_ENV != "production":
        name = f"dev{name}"  # e.g. "/ai" becomes "/devai" in test envs
    return name


@bot.event
async def on_ready():
    # NOTE: we can't use this as a lifecycle hook because
    # it may be called more than a single time.
    # our lifecycle hook is in our Bot class definition

    import openai

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
    if message.author.id not in DISCORD_USER_ID_WHITELIST:
        await message.channel.send("You are not allowed to use this command")
        return

    # they are in a thread that we are tracking
    our_thread = await threads.fetch_one(message.channel.id)
    if our_thread is None:
        return

    our_thread_messages = await thread_messages.fetch_many(
        thread_id=message.channel.id, page_size=our_thread["context_length"]
    )

    prompt = message.clean_content
    if prompt.startswith(f"{bot.user.mention} "):
        prompt = prompt.removeprefix(f"{bot.user.mention} ")

    prompt = f"{message.author.display_name}: {prompt}"

    async with message.channel.typing():
        thread_message = await thread_messages.create(
            message.channel.id,
            prompt,
            role="user",
            tokens_used=0,
        )
        our_thread_messages.append(thread_message)

        functions = openai_functions.get_full_openai_functions_schema()

        gpt_response = await gpt.send(
            our_thread["model"],
            our_thread_messages,
            functions,
        )
        if not gpt_response:
            await message.channel.send(
                f"Request failed after multiple retries.\n"
                f"Please try again after some time.\n"
                f"If this issue persists, please contact cmyui#0425 on discord."
            )
            return

        gpt_choice = gpt_response["choices"][0]
        gpt_message = gpt_choice["message"]

        if gpt_choice["finish_reason"] in ("stop", "length"):
            # assistant is replying to our message history
            gpt_response_content = gpt_message["content"]
            thread_message = await thread_messages.create(
                message.channel.id,
                gpt_message["content"],
                role="assistant",
                tokens_used=gpt_response["usage"]["total_tokens"],
            )
            our_thread_messages.append(thread_message)

        elif gpt_choice["finish_reason"] == "function_call":
            # assistant is invoking a function of ours
            function_name = gpt_message["function_call"]["name"]
            function_args = json.loads(gpt_message["function_call"]["arguments"])

            ai_function = openai_functions.ai_functions[function_name]
            function_response = await ai_function["callback"](**function_args)

            thread_message = await thread_messages.create(
                message.channel.id,
                content=None,
                role="assistant",
                tokens_used=gpt_response["usage"]["total_tokens"],
                function_name=function_name,
                function_args=function_args,
            )
            our_thread_messages.append(thread_message)

            # send function response back to gpt for the final response
            thread_message = await thread_messages.create(
                message.channel.id,
                function_response,
                role="function",
                tokens_used=0,
                function_name=function_name,
                function_args=function_args,
            )
            our_thread_messages.append(thread_message)

            # TODO: can gpt chain function calls?
            gpt_response = await gpt.send(our_thread["model"], our_thread_messages)
            gpt_response_content = gpt_response["choices"][0]["message"]["content"]

            thread_message = await thread_messages.create(
                message.channel.id,
                gpt_response_content,
                role="assistant",
                tokens_used=gpt_response["usage"]["total_tokens"],
            )
            our_thread_messages.append(thread_message)

        else:
            print(gpt_response)
            raise NotImplementedError(
                f"Unknown chatgpt finish reason: {gpt_choice['finish_reason']}"
            )

        for chunk in split_message(gpt_response_content, 2000):
            await message.channel.send(chunk)


@command_tree.command(name=command_name("cost"))
async def cost(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.Thread):
        return

    if interaction.user.id not in DISCORD_USER_ID_WHITELIST:
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


@command_tree.command(name=command_name("model"))
async def model(
    interaction: discord.Interaction,
    model: Literal["gpt-4", "gpt-3.5-turbo"],
):
    if not isinstance(interaction.channel, discord.Thread):
        return

    if interaction.user.id not in DISCORD_USER_ID_WHITELIST:
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


@command_tree.command(name=command_name("context"))
async def context(
    interaction: discord.Interaction,
    context_length: int,
):
    if not isinstance(interaction.channel, discord.Thread):
        return

    if interaction.user.id not in DISCORD_USER_ID_WHITELIST:
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

    if interaction.user.id not in DISCORD_USER_ID_WHITELIST:
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

    gpt_response = await gpt.send("gpt-4", messages)

    gpt_response_content = gpt_response.choices[0].message.content
    # tokens_spent = gpt_response.usage.total_tokens

    for chunk in split_message(gpt_response_content, 2000):
        await interaction.followup.send(chunk)


@command_tree.command(name=command_name("ai"))
async def ai(
    interaction: discord.Interaction,
    model: Literal["gpt-4", "gpt-3.5-turbo"] = "gpt-4",
    initial_setup: Literal["akatsuki-db"] = "akatsuki-db",
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

    if interaction.user.id not in DISCORD_USER_ID_WHITELIST:
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
        initial_setup=initial_setup,
        context_length=5,  # messages
    )

    if initial_setup is not None:
        initial_messages = initial_setups.get_initial_setup(initial_setup)
        if initial_messages is None:
            await interaction.followup.send(f"Unknown initial setup: {initial_setup}")
            return

        for m in initial_messages:
            await thread_messages.create(
                thread.id,
                content=m["content"],
                role=m["role"],
                tokens_used=0,
            )


@command_tree.command(name=command_name("transcript"))
async def transcript(
    interaction: discord.Interaction,
    context_length: int | None = None,
):
    if not isinstance(interaction.channel, discord.Thread):
        return

    if interaction.user.id not in DISCORD_USER_ID_WHITELIST:
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
            file=discord.File(f, filename="transcript.txt"),
        )


if __name__ == "__main__":
    bot.run(settings.DISCORD_TOKEN)
