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
from app import state
from app.adapters import database
from app.adapters.openai import gpt
from app.repositories import thread_messages
from app.repositories import threads


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

        state.http_client = httpx.AsyncClient()

        await super().start(*args, **kwargs)

    async def close(self, *args: Any, **kwargs: Any) -> None:
        await state.read_database.disconnect()
        await state.write_database.disconnect()

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
    250059887927623680,  # rapha
    190278149030936576,  # randomize
    249596453457100801,  # mistral
    # Super
    332722012877357066,  # fkzoink
}


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
    tracked_thread = await threads.fetch_one(message.channel.id)
    if tracked_thread is None:
        return

    prompt = message.clean_content
    if prompt.startswith(f"{bot.user.mention} "):
        prompt = prompt.removeprefix(f"{bot.user.mention} ")

    prompt = f"{message.author.name}: {prompt}"

    async with message.channel.typing():
        thread_history = await thread_messages.fetch_many(thread_id=message.channel.id)

        message_history = [
            gpt.Message(role=m["role"], content=m["content"])
            for m in thread_history[-tracked_thread["context_length"] :]
        ]
        message_history.append({"role": "user", "content": prompt})

        functions = openai_functions.get_full_openai_functions_schema()
        gpt_response = await gpt.send(
            tracked_thread["model"],
            message_history,
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
        message_history.append(gpt_message)

        if gpt_choice["finish_reason"] == "stop":
            gpt_response_content = gpt_message["content"]

        elif gpt_choice["finish_reason"] == "function_call":
            function_name = gpt_message["function_call"]["name"]
            function_kwargs = json.loads(gpt_message["function_call"]["arguments"])

            ai_function = openai_functions.ai_functions[function_name]
            function_response = await ai_function["callback"](**function_kwargs)

            # send function response back to gpt for the final response
            # TODO: could it call another function?
            #       i think this should support recursive calls
            message_history.append(
                {
                    "role": "function",
                    "name": function_name,
                    "content": function_response,
                }
            )
            gpt_response = await gpt.send(tracked_thread["model"], message_history)
            gpt_response_content = gpt_response["choices"][0]["message"]["content"]

        else:
            raise NotImplementedError(
                f"Unknown chatgpt finish reason: {gpt_choice['finish_reason']}"
            )

        input_tokens = gpt_response.usage.prompt_tokens
        output_tokens = gpt_response.usage.completion_tokens

        for chunk in split_message(gpt_response_content, 2000):
            await message.channel.send(chunk)

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

    messages = await thread_messages.fetch_many(thread_id=interaction.channel.id)

    per_user_tokens: dict[int, dict[Literal["input", "output"], int]] = {}
    per_user_message_count: dict[int, int] = {}
    for message in messages:
        user_id = message["discord_user_id"]
        user_tokens = per_user_tokens.get(user_id, {"input": 0, "output": 0})
        if message["role"] == "user":
            user_tokens["input"] += message["tokens_used"]
        else:
            user_tokens["output"] += message["tokens_used"]
        per_user_tokens[user_id] = user_tokens
        per_user_message_count[user_id] = per_user_message_count.get(user_id, 0) + 1

    per_user_cost: dict[int, float] = {}
    for user_id, tokens in per_user_tokens.items():
        user_cost = openai_pricing.tokens_to_dollars(
            thread["model"],
            input_tokens=tokens["input"],
            output_tokens=tokens["output"],
        )
        per_user_cost[user_id] = user_cost

    response_cost = sum(per_user_cost.values())

    message_chunks = [
        f"**Thread Cost Breakdown**",
        f"**---------------------**",
    ]
    for user_id, tokens in per_user_tokens.items():
        user_cost = per_user_cost[user_id]
        user_message_count = per_user_message_count[user_id]
        message_chunks.append(
            f"<@{user_id}>: ${user_cost:.5f} ({tokens['input']} input tokens) ({tokens['output']} output tokens) over {user_message_count} messages"
        )

    message_chunks.append(f"**Total Cost: ${response_cost:.5f}**")

    await interaction.followup.send("\n".join(message_chunks))


@command_tree.command(name=command_name("model"))
async def model(
    interaction: discord.Interaction,
    model: gpt.OpenAIModel,
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

        author_name = message.author.name

        if tracking:
            messages.append({"role": "user", "content": f"{author_name}: {content}"})
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

    gpt_response = await gpt.send(gpt.OpenAIModel.GPT_4_TURBO_PREVIEW, messages)

    gpt_response_content = gpt_response.choices[0].message.content
    # tokens_spent = gpt_response.usage.total_tokens

    for chunk in split_message(gpt_response_content, 2000):
        await interaction.followup.send(chunk)


@command_tree.command(name=command_name("ai"))
async def ai(
    interaction: discord.Interaction,
    model: gpt.OpenAIModel = gpt.OpenAIModel.GPT_4_TURBO_PREVIEW,
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
