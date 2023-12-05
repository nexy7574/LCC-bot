import asyncio
import re

import aiosqlite

import discord
from discord.ext import commands


class McDonaldsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.targets: dict[discord.Member, float] = {}
        self.lock = asyncio.Lock()
        self.cooldown: dict[discord.Member, float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        me = message.guild.me if message.guild else self.bot.user
        if not message.channel.permissions_for(me).manage_messages:
            return

        async with self.lock:
            NIGHTMARE_REGEX = re.compile(r"\|\| \|\|([^|]+)#0\|\| \|\|: bypassed.*")
            if m := NIGHTMARE_REGEX.match(message.content):
                username = m.group(1)
                member = discord.utils.get(message.guild.members, name=username)
                if member:
                    author = member

            if author in self.targets:
                if message.content.upper() != "MCDONALDS!":
                    await message.delete()
                    if (message.created_at.timestamp() - self.targets[author]) > 10:
                        await message.channel.send(
                            f"{message.author.mention} Please say `MCDONALDS!` to end commercial.",
                            delete_after=30
                        )
                        self.targets[author] = message.created_at.timestamp()
                else:
                    self.targets.pop(author, None)
                    self.cooldown[author] = message.created_at.timestamp()
                    await message.reply(
                        "Thank you. You may now resume your activity.",
                        delete_after=120
                    )

    @commands.user_command(name="Commercial Break")
    async def commercial_break(self, ctx: discord.ApplicationContext, member: discord.Member):
        await ctx.defer(ephemeral=True)

        if not ctx.channel.permissions_for(ctx.me).manage_messages:
            return await ctx.respond("I don't have permission to manage messages in this channel.", ephemeral=True)

        if member.bot or member == ctx.user:
            return await ctx.respond("No.", ephemeral=True)

        if member in self.targets.keys():
            await ctx.respond(f"{member.mention} is already in a commercial break.")
            return
        elif member in self.cooldown.keys():
            expires = self.cooldown[member] + 300
            if expires > discord.utils.utcnow().timestamp():
                await ctx.respond(
                    f"{member.mention} is not due another ad break yet. Their next commercial break will start "
                    f"<t:{int(expires)}:R> at the earliest."
                )
                return
            else:
                self.cooldown.pop(member, None)

        self.targets[member] = discord.utils.utcnow().timestamp()
        await ctx.send(
            f"{member.mention} Commercial break! Please say `MCDONALDS!` to end commercial.\n"
            f"*This commercial break is sponsored by {ctx.user.mention}.*",
            delete_after=300,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False)
        )
        await ctx.respond("Commercial break started.", ephemeral=True)
        await ctx.delete(delay=120)


def setup(bot):
    bot.add_cog(McDonaldsCog(bot))
