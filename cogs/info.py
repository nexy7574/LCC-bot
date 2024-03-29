import logging
import typing

import discord
import httpx
from discord.ext import commands

from utils import get_or_none
from utils.db import AccessTokens

try:
    from config import OAUTH_ID, OAUTH_REDIRECT_URI
except ImportError:
    OAUTH_REDIRECT_URI = OAUTH_ID = None


class InfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = httpx.AsyncClient(base_url="https://discord.com/api/v10")

    async def get_user_info(self, token: str):
        try:
            response = await self.client.get("/users/@me", headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
        except (httpx.HTTPError, httpx.RequestError, ConnectionError):
            return
        return response.json()

    async def get_user_guilds(self, token: str):
        try:
            response = await self.client.get("/users/@me/guilds", headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
        except (httpx.HTTPError, httpx.RequestError, ConnectionError):
            return
        return response.json()

    async def get_user_connections(self, token: str):
        try:
            response = await self.client.get("/users/@me/connections", headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
        except (httpx.HTTPError, httpx.RequestError, ConnectionError):
            return
        return response.json()

    @commands.slash_command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def me(self, ctx: discord.ApplicationContext):
        """Displays oauth info about you"""
        await ctx.defer()

        user = await get_or_none(AccessTokens, user_id=ctx.author.id)
        if not user:
            url = OAUTH_REDIRECT_URI
            return await ctx.respond(
                embed=discord.Embed(
                    title="You must link your account first!",
                    description="Don't worry, [I only store your IP information. And the access token.](%s)" % url,
                    url=url,
                )
            )
        user_data = await self.get_user_info(user.access_token)
        guilds = await self.get_user_guilds(user.access_token)
        connections = await self.get_user_connections(user.access_token)
        embed = discord.Embed(
            title="Your info",
        )
        if user_data:
            for field in (
                "bot",
                "system",
                "mfa_enabled",
                "banner",
                "accent_color",
                "mfa_enabled",
                "locale",
                "verified",
                "email",
                "flags",
                "premium_type",
                "public_flags",
            ):
                user_data.setdefault(field, None)
            lines = [
                "ID: {0[id]}",
                "Username: {0[username]}",
                "Discriminator: #{0[discriminator]}",
                "Avatar: {0[avatar]}",
                "Bot: {0[bot]}",
                "System: {0[system]}",
                "MFA Enabled: {0[mfa_enabled]}",
                "Banner: {0[banner]}",
                "Banner Color: {0[banner_color]}",
                "Locale: {0[locale]}",
                "Email Verified: {0[verified]}",
                "Email: {1}",
                "Flags: {0[flags]}",
                "Premium Type: {0[premium_type]}",
                "Public Flags: {0[public_flags]}",
            ]
            email = user_data["email"]
            if email:
                email = email.replace("@", "\u200b@\u200b").replace(".", "\u200b.\u200b")
            embed.add_field(
                name="User Info",
                value="\n".join(lines).format(user_data, email),
            )

        if guilds:
            guilds = sorted(guilds, key=lambda x: x["name"])
            embed.add_field(
                name="Guilds (%d):" % len(guilds),
                value="\n".join(f"{guild['name']} ({guild['id']})" for guild in guilds),
            )

        if connections:
            embed.add_field(
                name="Connections (%d):" % len(connections),
                value="\n".join(f"{connection['type'].title()} ({connection['id']})" for connection in connections),
            )

        if not embed.fields:
            await user.delete()
            embed.description = "No data found. You may need to [reauthenticate.](%s)" % OAUTH_REDIRECT_URI
            embed.url = OAUTH_REDIRECT_URI
            embed.colour = discord.Color.red()

        await ctx.respond(embed=embed)

    @commands.command(name="set-log-level")
    @commands.is_owner()
    async def set_log_level(self, ctx: commands.Context, logger_name: str, logger_level: typing.Union[str, int]):
        """Sets a module's log level."""
        logger_level = logger_level.upper()
        if getattr(logging, logger_level, None) is None or logging.getLevelNamesMapping().get(logger_level) is None:
            return await ctx.reply(":x: Invalid log level.")
        logger = logging.getLogger(logger_name)
        old_level = logger.getEffectiveLevel()

        def name(level):
            return logging.getLevelName(level)

        logger.setLevel(logger_level)
        return await ctx.reply(
            f"\N{white heavy check mark} {logger_name}: {name(old_level)} -> {name(logger.getEffectiveLevel())}"
        )


def setup(bot):
    if OAUTH_REDIRECT_URI and OAUTH_ID:
        bot.add_cog(InfoCog(bot))
    else:
        logging.getLogger("jimmy.cogs.info").warning("OAUTH_REDIRECT_URI not set, not loading info cog")
