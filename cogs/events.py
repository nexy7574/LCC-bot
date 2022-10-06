from typing import Optional

import discord
from discord.ext import commands
from utils import Student, get_or_none
from config import guilds


LTR = "\N{black rightwards arrow}\U0000fe0f"
RTL = "\N{leftwards black arrow}\U0000fe0f"


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        channel: Optional[discord.TextChannel] = self.bot.get_channel(payload.channel_id)
        if channel:
            try:
                message: discord.Message = await channel.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            if message.author == self.bot.user:
                if str(payload.emoji) == "\N{WASTEBASKET}":
                    try:
                        await message.edit(content=f"[deleted by <@{payload.user_id}>]", embed=None, view=None)
                    except discord.HTTPException:
                        pass
                    finally:
                        await message.delete(delay=1)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild is None or member.guild.id not in guilds:
            return

        student: Optional[Student] = await get_or_none(Student, user_id=member.id)
        if student and student.id:
            role = discord.utils.find(lambda r: r.name.lower() == "verified", member.guild.roles)
            if role and role < member.guild.me.top_role:
                await member.add_roles(role, reason="Verified")

        channel: discord.TextChannel = discord.utils.get(member.guild.text_channels, name="general")
        if channel and channel.can_send():
            await channel.send(
                f"{LTR} {member.mention} {f'({student.id})' if student else '(pending verification)'}"
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.guild is None or member.guild.id not in guilds:
            return

        student: Optional[Student] = await get_or_none(Student, user_id=member.id)
        channel: discord.TextChannel = discord.utils.get(member.guild.text_channels, name="general")
        if channel and channel.can_send():
            await channel.send(
                f"{RTL} {member.mention} {f'({student.id})' if student else '(pending verification)'}"
            )


def setup(bot):
    bot.add_cog(Events(bot))
