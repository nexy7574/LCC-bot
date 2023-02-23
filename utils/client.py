import asyncio
import sys

import discord
import config
from asyncio import Lock
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, Dict, TYPE_CHECKING, Union
if TYPE_CHECKING:
    from uvicorn import Server, Config
    from asyncio import Task


__all__ = ("Bot", 'bot')


# noinspection PyAbstractClass
class Bot(commands.Bot):
    if TYPE_CHECKING:
        web: Optional[Dict[str, Union[Server, Config, Task]]]

    def __init__(self, intents: discord.Intents, guilds: list[int], extensions: list[str]):
        from .db import JimmyBans, registry
        from .console import console
        super().__init__(
            command_prefix=commands.when_mentioned_or("h!", "r!"),
            debug_guilds=guilds,
            allowed_mentions=discord.AllowedMentions.none(),
            intents=intents,
        )
        self.loop.run_until_complete(registry.create_all())
        self.training_lock = Lock()
        self.started_at = datetime.now(tz=timezone.utc)
        self.bans = JimmyBans()
        self.console = console
        for ext in extensions:
            try:
                self.load_extension(ext)
            except discord.ExtensionFailed as e:
                console.log(f"[red]Failed to load extension {ext}: {e}")
                if getattr(config, "dev", False):
                    console.print_exception()
            else:
                console.log(f"Loaded extension [green]{ext}")

    if getattr(config, "CONNECT_MODE", None) == 2:
        async def connect(self, *, reconnect: bool = True) -> None:
            self.console.log("Exit target 2 reached, shutting down (not connecting to discord).")
            return

    async def close(self) -> None:
        if getattr(self, "web", None) is not None:
            await self.http.close()
            self.console.log("Closing web server...")
            await self.web["server"].shutdown()
            self.web["task"].cancel()
            self.console.log("Web server closed.")
            try:
                await self.web["task"]
            except asyncio.CancelledError:
                pass
            del self.web["server"]
            del self.web["config"]
            del self.web["task"]
            del self.web
        try:
            await asyncio.wait_for(asyncio.create_task(super().close()), timeout=10)
        except asyncio.TimeoutError:
            self.console.log("Timed out while closing, forcing shutdown.")
            sys.exit(1)
        self.console.log("Finished shutting down.")


try:
    from config import intents as _intents
except ImportError:
    _intents = discord.Intents.all()

try:
    from config import extensions as _extensions
except ImportError:
    _extensions = [
        "jishaku",
        "cogs.verify",
        "cogs.mod",
        "cogs.events",
        "cogs.assignments",
        "cogs.timetable",
        "cogs.other",
        "cogs.starboard",
        "cogs.uptime",
    ]


bot = Bot(_intents, config.guilds, _extensions)
