import asyncio
import logging
import sys
from asyncio import Lock
from orm import __version__ as orm_version
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Union

import discord
from discord.ext import commands

import config

if TYPE_CHECKING:
    from asyncio import Task

    from uvicorn import Config, Server


__all__ = ("Bot", "bot")


# noinspection PyAbstractClass
class Bot(commands.Bot):
    if TYPE_CHECKING:
        web: Optional[Dict[str, Union[Server, Config, Task]]]

    def __init__(self, intents: discord.Intents, guilds: list[int], extensions: list[str], prefixes: list[str]):
        from .console import console
        from .db import registry

        super().__init__(
            command_prefix=commands.when_mentioned_or(*prefixes),
            debug_guilds=guilds,
            allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=True),
            intents=intents,
            max_messages=5000,
            case_insensitive=True,
        )
        if tuple(map(int, orm_version.split("."))) >= (0, 3, 1):
            self.loop.run_until_complete(registry.create_all())
        else:
            registry.create_all()
        self.training_lock = Lock()
        self.started_at = discord.utils.utcnow()
        self.console = console
        self.log = log = logging.getLogger("jimmy.client")
        self.debug = log.debug
        self.info = log.info
        self.warning = self.warn = log.warning
        self.error = self.log.error
        self.critical = self.log.critical
        for ext in extensions:
            try:
                self.load_extension(ext)
            except discord.ExtensionNotFound:
                log.error(f"[red]Failed to load extension {ext}: Extension not found.")
            except (discord.ExtensionFailed, OSError) as e:
                log.error(f"[red]Failed to load extension {ext}: {e}", exc_info=True)
            else:
                log.info(f"Loaded extension [green]{ext}")

    if getattr(config, "CONNECT_MODE", None) == 2:

        async def connect(self, *, reconnect: bool = True) -> None:
            self.log.critical("Exit target 2 reached, shutting down (not connecting to discord).")
            return

    async def on_error(self, event: str, *args, **kwargs):
        e_type, e, tb = sys.exc_info()
        if isinstance(e, discord.NotFound) and e.code == 10062:  # invalid interaction
            self.log.warning(f"Invalid interaction received, ignoring. {e!r}")
            return
        if isinstance(e, discord.CheckFailure) and "The global check once functions failed." in str(e):
            return
        await super().on_error(event, *args, **kwargs)

    async def close(self) -> None:
        await self.http.close()
        if getattr(self, "web", None) is not None:
            self.log.info("Closing web server...")
            try:
                await asyncio.wait_for(self.web["server"].shutdown(), timeout=5)
                self.web["task"].cancel()
                self.console.log("Web server closed.")
                try:
                    await asyncio.wait_for(self.web["task"], timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                del self.web["server"]
                del self.web["config"]
                del self.web["task"]
                del self.web
            except asyncio.TimeoutError:
                pass
        try:
            await super().close()
        except asyncio.TimeoutError:
            self.log.critical("Timed out while closing, forcing shutdown.")
            sys.exit(1)
        self.log.info("Finished shutting down.")


try:
    from config import intents as _intents
except ImportError:
    _intents = discord.Intents.all()

try:
    from config import extensions as _extensions
except ImportError:
    _extensions = [
        "jishaku",
    ]
    for file in Path("cogs").glob("*.py"):
        if file.name.startswith(("_", ".")):
            continue
        _extensions.append(f"cogs.{file.stem}")

try:
    from config import prefixes as _prefixes
except ImportError:
    _prefixes = ("h!", "r!")


bot = Bot(_intents, config.guilds, _extensions, list(_prefixes))
