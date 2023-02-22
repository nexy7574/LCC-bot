import discord
from discord.ext import commands
from asyncio import Lock
import config
from datetime import datetime, timezone, timedelta
from utils import registry, console, get_or_none, JimmyBans
from web.server import app
import uvicorn


intents = discord.Intents.default()
intents += discord.Intents.messages
intents += discord.Intents.message_content
intents += discord.Intents.members
intents += discord.Intents.presences


extensions = [
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


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=self.get_prefix,
            debug_guilds=config.guilds,
            allowed_mentions=discord.AllowedMentions.none(),
            intents=intents,
        )
        self.training_lock = Lock()
        self.started_at = datetime.now(tz=timezone.utc)
        self.bans = JimmyBans()
        for ext in extensions:
            try:
                bot.load_extension(ext)
            except discord.ExtensionFailed as e:
                console.log(f"[red]Failed to load extension {ext}: {e}")
            else:
                console.log(f"Loaded extension [green]{ext}")

        app.state.bot = self
        config = uvicorn.Config(
            app,
            port=3762
        )


bot = Bot()
bot.loop.run_until_complete(registry.create_all())


@bot.listen()
async def on_connect():
    console.log("[green]Connected to discord!")


@bot.listen("on_application_command_error")
async def on_application_command_error(ctx: discord.ApplicationContext, error: Exception):
    if isinstance(error, commands.CommandOnCooldown):
        now = discord.utils.utcnow()
        now += timedelta(seconds=error.retry_after)
        return await ctx.respond(
            f"\N{stopwatch} This command is on cooldown. You can use this command again "
            f"{discord.utils.format_dt(now, 'R')}.",
            delete_after=error.retry_after,
        )
    elif isinstance(error, commands.MaxConcurrencyReached):
        return await ctx.respond(
            f"\N{warning sign} This command is already running. Please wait for it to finish.",
            ephemeral=True,
        )
    await ctx.respond("Application Command Error: `%r`" % error)
    raise error


@bot.listen("on_command_error")
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    await ctx.reply("Command Error: `%r`" % error)
    raise error


@bot.listen("on_application_command")
async def on_application_command(ctx: discord.ApplicationContext):
    console.log(
        "{0.author} ({0.author.id}) used application command /{0.command.qualified_name} in "
        "#{0.channel}, {0.guild}".format(ctx)
    )


@bot.event
async def on_ready():
    console.log("Logged in as", bot.user)


@bot.slash_command()
async def ping(ctx: discord.ApplicationContext):
    # noinspection SpellCheckingInspection
    """Checks the bot's response time"""
    gateway = round(ctx.bot.latency * 1000, 2)
    return await ctx.respond(f"\N{white heavy check mark} Pong! `{gateway}ms`.")


@bot.check_once
async def check_not_banned(ctx: discord.ApplicationContext | commands.Context):
    if await bot.is_owner(ctx.author):
        return True
    user = ctx.author
    ban: JimmyBans = await get_or_none(JimmyBans, user_id=user.id)
    if ban:
        dt = datetime.fromtimestamp(ban.until, timezone.utc)
        if dt < discord.utils.utcnow():
            await ban.delete()
        else:
            reply = ctx.reply if isinstance(ctx, commands.Context) else ctx.respond
            try:
                await reply(content=f":x: You can use commands {discord.utils.format_dt(dt, 'R')}")
            except discord.HTTPException:
                pass
            finally:
                return False
    return True


if __name__ == "__main__":
    console.log("Starting...")
    bot.started_at = discord.utils.utcnow()
    bot.run(config.token)
