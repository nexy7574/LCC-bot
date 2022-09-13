import discord
from discord.ext import commands
import config
from utils import registry


bot = commands.Bot(
    commands.when_mentioned_or("h!"),
    debug_guilds=config.guilds,
    allowed_mentions=discord.AllowedMentions.none()
)
bot.load_extension("jishaku")
bot.load_extension("cogs.verify")
bot.loop.run_until_complete(registry.create_all())


@bot.event
async def on_ready():
    print("Logged in as", bot.user)


@bot.slash_command()
async def ping(ctx: discord.ApplicationContext):
    """Checks the bot's response time"""
    gateway = round(ctx.bot.latency * 1000, 2)
    return await ctx.respond(
        f"\N{white heavy check mark} Pong! `{gateway}ms`."
    )


bot.run(config.token)
