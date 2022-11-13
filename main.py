import discord
from discord.ext import commands
import config
from utils import registry, console


bot = commands.Bot(
    commands.when_mentioned_or("h!"),
    debug_guilds=config.guilds,
    allowed_mentions=discord.AllowedMentions.none(),
    intents=discord.Intents.default() + discord.Intents.members,
)

extensions = ["jishaku", "cogs.verify", "cogs.mod", "cogs.events", "cogs.assignments", "cogs.timetable", "cogs.other"]
for ext in extensions:
    bot.load_extension(ext)
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


if __name__ == "__main__":
    print("Starting...")
    bot.run(config.token)
