import discord
from discord.ext import commands
import config
from utils import registry


bot = commands.Bot(
    commands.when_mentioned_or("h!"),
    debug_guilds=config.guilds,
    allowed_mentions=discord.AllowedMentions.none(),
    intents=discord.Intents.default() + discord.Intents.members
)
bot.load_extension("jishaku")
bot.load_extension("cogs.verify")
bot.load_extension("cogs.mod")
bot.load_extension("cogs.events")
bot.load_extension("cogs.assignments")
bot.loop.run_until_complete(registry.create_all())


@bot.event
async def on_connect():
    print("Connected to discord!")


@bot.listen()
async def on_ready():
    print("Logged in as", bot.user)


@bot.slash_command()
async def ping(ctx: discord.ApplicationContext):
    """Checks the bot's response time"""
    gateway = round(ctx.bot.latency * 1000, 2)
    return await ctx.respond(f"\N{white heavy check mark} Pong! `{gateway}ms`.")


if __name__ == "__main__":
    print("Starting...")
    bot.run(config.token)
