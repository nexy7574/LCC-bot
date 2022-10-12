import discord
from discord.ext import commands
import config
from utils import registry, console


bot = commands.Bot(
    commands.when_mentioned_or("h!"),
    debug_guilds=config.guilds,
    allowed_mentions=discord.AllowedMentions.none(),
    intents=discord.Intents.default() + discord.Intents.members
)

for ext in ["jishaku", "cogs.verify", "cogs.mod", "cogs.events", "cogs.assignments"]:
    bot.load_extension(ext)
    console.log(f"Loaded extension [green]{ext}")
bot.loop.run_until_complete(registry.create_all())


@bot.listen()
async def on_connect():
    console.log("[green]Connected to discord!")


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
