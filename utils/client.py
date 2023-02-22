import discord
import config
from asyncio import Lock
from discord.ext import commands
from datetime import datetime, timezone


__all__ = ("Bot", 'bot')


# noinspection PyAbstractClass
class Bot(commands.Bot):
    def __init__(self, intents: discord.Intents, guilds: list[int], extensions: list[str]):
        from .db import JimmyBans, registry
        from .console import console
        super().__init__(
            command_prefix=self.get_prefix,
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
