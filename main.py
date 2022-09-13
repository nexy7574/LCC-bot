import discord
from discord.ext import commands
import config


bot = commands.Bot(
    commands.when_mentioned_or("h!"),
    debug_guilds=config.guilds
)


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
