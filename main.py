import asyncio
import sys
import logging
import textwrap
from datetime import datetime, timedelta, timezone
from rich.logging import RichHandler

import config
import discord
from discord.ext import commands

from utils import JimmyBanException, JimmyBans, console, get_or_none
from utils.client import bot

logging.basicConfig(
    filename=getattr(config, "LOG_FILE", "jimmy.log"),
    filemode=getattr(config, "LOG_MODE", "a"),
    format="%(asctime)s:%(level)s:%(name)s: %(message)s",
    datefmt="%Y-%m-%d:%H:%M",
    level=getattr(config, "LOG_LEVEL", logging.INFO)
)
# echo to stdout
handler = RichHandler(getattr(config, "LOG_LEVEL", logging.INFO), console=console, markup=True)
logging.getLogger().addHandler(handler)


@bot.listen()
async def on_connect():
    bot.log.info("[green]Connected to discord!")


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
    elif isinstance(error, JimmyBanException):
        return await ctx.respond(str(error))
    elif isinstance(error, commands.CommandError):
        if error.args and error.args[0] == "User not connected to a voice channel.":
            return

    if ctx.user.id == 1019233057519177778:
        await ctx.respond("Uh oh! I did a fucky wucky >.< I'll make sure to let important peoplez know straight away!!")
    else:
        text = "Application Command Error: `%r`" % error
        await ctx.respond(textwrap.shorten(text, 2000))
    raise error


@bot.listen("on_command_error")
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, JimmyBanException):
        return await ctx.reply(str(error))
    await ctx.reply(textwrap.shorten("Command Error: `%r`" % error, 2000))
    raise error


@bot.listen("on_application_command")
async def on_application_command(ctx: discord.ApplicationContext):
    bot.log.info(
        "{0.author} ({0.author.id}) used application command /{0.command.qualified_name} in "
        "[blue]#{0.channel}[/], {0.guild}".format(ctx)
    )


@bot.event
async def on_ready():
    bot.log.info("(READY) Logged in as", bot.user)
    if getattr(config, "CONNECT_MODE", None) == 1:
        bot.log.critical("Bot is now ready and exit target 1 is set, shutting down.")
        await bot.close()
        sys.exit(0)


@bot.slash_command()
async def ping(ctx: discord.ApplicationContext):
    # noinspection SpellCheckingInspection
    """Checks the bot's response time"""
    gateway = round(ctx.bot.latency * 1000, 2)
    return await ctx.respond(f"\N{white heavy check mark} Pong! `{gateway}ms`.")


@bot.check_once
async def check_not_banned(ctx: discord.ApplicationContext | commands.Context):
    if await bot.is_owner(ctx.author) or ctx.command.name in ("block", "unblock", "timetable", "verify", "kys"):
        return True
    user = ctx.author
    ban: JimmyBans = await get_or_none(JimmyBans, user_id=user.id)
    if ban:
        dt = datetime.fromtimestamp(ban.until, timezone.utc)
        if dt < discord.utils.utcnow():
            await ban.delete()
        else:
            raise JimmyBanException(dt, ban.reason)
    return True


if __name__ == "__main__":
    bot.log.info("Starting...")
    bot.started_at = discord.utils.utcnow()

    if getattr(config, "WEB_SERVER", True):
        bot.log.info("Web server is enabled (WEB_SERVER=True in config.py), initialising.")
        import uvicorn

        from web.server import app

        app.state.bot = bot

        http_config = uvicorn.Config(
            app,
            host=getattr(config, "HTTP_HOST", "127.0.0.1"),
            port=getattr(config, "HTTP_PORT", 3762),
            **getattr(config, "UVICORN_CONFIG", {}),
        )
        bot.log.info("Web server will listen on %s:%s", http_config.host, http_config.port)
        server = uvicorn.Server(http_config)
        bot.log.info("Starting web server...")
        loop = bot.loop
        http_server_task = loop.create_task(server.serve())
        bot.web = {
            "server": server,
            "config": http_config,
            "task": http_server_task,
        }
    bot.log.info("Beginning main loop.")
    bot.run(config.token)
