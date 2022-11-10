from typing import Optional
from datetime import datetime, time

import discord
from discord.ext import commands, tasks
from utils import Student, get_or_none
from config import guilds, lupupa_warning


LTR = "\N{black rightwards arrow}\U0000fe0f"
RTL = "\N{leftwards black arrow}\U0000fe0f"


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.lupupa_warning_task.start()

    def cog_unload(self):
        self.lupupa_warning_task.stop()

    @tasks.loop(minutes=30)
    async def lupupa_warning_task(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()
        now = datetime.now()
        lupupa_warning_text = "\N{warning sign} Lupupa warning!!!"
        lupupa_recovery_text = "\N{loudly crying face} Lupupa recovery..."
        if lupupa_warning and now.strftime("%A") == "Thursday":
            if now.time() > time(15, 15):
                text = lupupa_recovery_text
                status = discord.Status.idle
            else:
                text = lupupa_warning_text
                status = discord.Status.dnd
            if self.bot.activity:
                if self.bot.activity.name == text:
                    return
            await self.bot.change_presence(
                activity=discord.Activity(name=text, type=discord.ActivityType.playing), status=status
            )
        else:
            await self.bot.change_presence()

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        channel: Optional[discord.TextChannel] = self.bot.get_channel(payload.channel_id)
        if channel is not None:
            try:
                message: discord.Message = await channel.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            if message.author.id == self.bot.user.id:
                if payload.emoji.name == "\N{wastebasket}\U0000fe0f":
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
            await channel.send(f"{LTR} {member.mention} {f'({student.id})' if student else '(pending verification)'}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.guild is None or member.guild.id not in guilds:
            return

        student: Optional[Student] = await get_or_none(Student, user_id=member.id)
        channel: discord.TextChannel = discord.utils.get(member.guild.text_channels, name="general")
        if channel and channel.can_send():
            await channel.send(f"{RTL} {member.mention} {f'({student.id})' if student else '(pending verification)'}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        if message.channel.name == "pinboard":
            if message.type == discord.MessageType.pins_add:
                await message.delete(delay=0.01)
            else:
                try:
                    await message.pin(reason="Automatic pinboard pinning")
                except discord.HTTPException as e:
                    return await message.reply(f"Failed to auto-pin: {e}", delete_after=10)
        elif message.channel.name in ("verify", "timetable") and message.author != self.bot.user:
            if message.channel.permissions_for(message.guild.me).manage_messages:
                await message.delete(delay=1)

        else:
            if message.bot is True:
                return
            if self.bot.user in message.mentions:
                if message.content.lower() == "good bot":
                    return await message.reply("Thank you! :D")


def setup(bot):
    bot.add_cog(Events(bot))
