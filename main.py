import discord
from discord.ext import commands
from asyncio import Lock
import config
from datetime import datetime, timezone
from utils import registry, console, get_or_none, JimmyBans


intents = discord.Intents.default()
intents += discord.Intents.messages
intents += discord.Intents.message_content
intents += discord.Intents.members
intents += discord.Intents.presences


bot = commands.Bot(
    commands.when_mentioned_or("h!"),
    debug_guilds=config.guilds,
    allowed_mentions=discord.AllowedMentions.none(),
    intents=intents,
)
bot.training_lock = Lock()

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
for ext in extensions:
    try:
        bot.load_extension(ext)
    except discord.ExtensionFailed as e:
        console.log(f"[red]Failed to load extension {ext}: {e}")
    else:
        console.log(f"Loaded extension [green]{ext}")
bot.loop.run_until_complete(registry.create_all())


@bot.listen()
async def on_connect():
    console.log("[green]Connected to discord!")


@bot.listen("on_application_command_error")
async def on_application_command_error(ctx: discord.ApplicationContext, error: Exception):
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
    bot.run(config.token)
