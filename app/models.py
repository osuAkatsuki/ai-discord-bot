import asyncio
import logging
from typing import Any

import discord

from app import lifecycle

SHUTDOWN_TIMEOUT = 15


class DiscordBot(discord.Client):
    async def start(self, *args, **kwargs) -> None:
        await lifecycle.start()
        await super().start(*args, **kwargs)

    async def close(self, *args: Any, **kwargs: Any) -> None:
        async def _shutdown():
            await lifecycle.stop()
            await super().close(*args, **kwargs)

        try:
            await asyncio.wait_for(_shutdown(), timeout=SHUTDOWN_TIMEOUT)
        except asyncio.CancelledError:
            logging.exception(
                "Shutdown timeout of %s exceeded, exiting immediately",
                SHUTDOWN_TIMEOUT,
                extra={"timeout": SHUTDOWN_TIMEOUT},
            )
