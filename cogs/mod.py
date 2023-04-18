from datetime import datetime

import discord
from discord.ext import commands
from utils import Student, get_or_none, BannedStudentID, owner_or_admin, JimmyBans
from typing import Sized


class LimitedList(list):
    """FIFO Limited list"""
    def __init__(self, iterable: Sized = None, size: int = 5000):
        if iterable:
            assert len(iterable) <= size, "Initial iterable too big."
        super().__init__(iterable or [])
        self._max_size = size

    def append(self, __object) -> None:
        if len(self) + 1 >= self._max_size:
            self.pop(0)
        super().append(__object)

    def __add__(self, other):
        if len(other) > self._max_size:
            raise ValueError("Other is too large")
        elif len(other) == self._max_size:
            self.clear()
            super().__add__(other)
        else:
            for item in other:
                self.append(item)


class Mod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = LimitedList()

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        self.cache.append(message)

    @commands.user_command(name="Ban Account's B Number")
    @discord.default_permissions(ban_members=True)
    async def ban_student_id(self, ctx: discord.ApplicationContext, member: discord.Member):
        """Bans a student ID from registering. Also bans an account associated with it."""
        await ctx.defer(ephemeral=True)
        student_id = await get_or_none(Student, user_id=member.id)
        if student_id is None:
            return await ctx.respond("\N{cross mark} Unknown B number (is the user verified yet?)", ephemeral=True)

        ban = await get_or_none(BannedStudentID, student_id=student_id.id)
        if ban is None:
            ban = await BannedStudentID.objects.create(
                student_id=student_id.id,
                associated_account=member.id,
            )

        await member.ban(reason=f"Banned ID {ban.student_id} by {ctx.user}")
        return await ctx.respond(
            f"\N{white heavy check mark} Banned {ban.student_id} (and {member.mention})", ephemeral=True
        )

    @commands.slash_command(name="unban-student-number")
    @discord.default_permissions(ban_members=True)
    async def unban_student_id(self, ctx: discord.ApplicationContext, student_id: str):
        """Unbans a student ID and the account associated with it."""
        student_id = student_id.upper()
        ban = await get_or_none(BannedStudentID, student_id=student_id)
        if not ban:
            return await ctx.respond("\N{cross mark} That student ID isn't banned.")
        await ctx.defer()
        user_id = ban.associated_account
        await ban.delete()
        if not user_id:
            return await ctx.respond(f"\N{white heavy check mark} Unbanned {student_id}. No user to unban.")
        else:
            try:
                await ctx.guild.unban(discord.Object(user_id), reason=f"Unbanned by {ctx.user}")
            except discord.HTTPException as e:
                return await ctx.respond(
                    f"\N{white heavy check mark} Unbanned {student_id}. Failed to unban {user_id} - HTTP {e.status}."
                )
            else:
                return await ctx.respond(f"\N{white heavy check mark} Unbanned {student_id}. Unbanned {user_id}.")

    @commands.slash_command(name="block")
    @owner_or_admin()
    async def block_user(self, ctx: discord.ApplicationContext, user: discord.Member, reason: str, until: str):
        """Blocks a user from using the bot."""
        await ctx.defer()
        date = datetime.utcnow()
        _time = until
        try:
            date, _time = until.split(" ")
        except ValueError:
            pass
        else:
            try:
                date = datetime.strptime(date, "%d/%m/%Y")
            except ValueError:
                return await ctx.respond("Invalid date format. Use `DD/MM/YYYY`.")
        try:
            hour, minute = map(int, _time.split(":"))
        except ValueError:
            return await ctx.respond("\N{cross mark} Invalid time format. Use HH:MM.")
        end = date.replace(hour=hour, minute=minute)

        # get an entry for the user's ID, and if it doesn't exist, create it. Otherwise, alert the user.
        entry = await get_or_none(JimmyBans, user_id=user.id)
        if entry is None:
            await JimmyBans.objects.create(user_id=user.id, reason=reason, until=end.timestamp())
        else:
            return await ctx.respond("\N{cross mark} That user is already blocked.")
        await ctx.respond(f"\N{white heavy check mark} Blocked {user.mention} until {discord.utils.format_dt(end)}.")

    @commands.slash_command(name="unblock")
    @owner_or_admin()
    async def unblock_user(self, ctx: discord.ApplicationContext, user: discord.Member):
        """Unblocks a user from using the bot."""
        await ctx.defer()
        entry = await get_or_none(JimmyBans, user_id=user.id)
        if entry is None:
            return await ctx.respond("\N{cross mark} That user isn't blocked.")
        await entry.delete()
        await ctx.respond(f"\N{white heavy check mark} Unblocked {user.mention}.")

    @commands.command()
    async def undelete(self, ctx: commands.Context, user: discord.User, *, query: str = None):
        """Searches through the message cache to see if there's any deleted messages."""
        for message in self.cache:
            message: discord.Message
            if message.author == user:
                query_ = query.lower() if query else None
                content_ = str(message.clean_content or '').lower()
                if query_ is not None and (query_ in content_ or content_ in query_):
                    break
        else:
            return await ctx.reply("\N{cross mark} No matches in cache.")

        embeds = [
            discord.Embed(title="Message found!"),
            discord.Embed(
                description=message.content,
                colour=message.author.colour,
                timestamp=message.created_at,
                fields=[
                    discord.EmbedField(
                        "Attachment count",
                        str(len(message.attachments)),
                        False
                    ),
                    discord.EmbedField(
                        "Location",
                        str(message.channel.mention)
                    ),
                    discord.EmbedField(
                        "Times",
                        f"Created: {discord.utils.format_dt(message.created_at, 'R')} | Edited: "
                        f"{'None' if message.edited_at is None else discord.utils.format_dt(message.edited_at, 'R')}"
                    )
                ]
            ).set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        ]
        await ctx.reply(embeds=embeds)


def setup(bot):
    bot.add_cog(Mod(bot))
