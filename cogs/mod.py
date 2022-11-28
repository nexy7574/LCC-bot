import discord
from discord.ext import commands
from utils import Student, get_or_none, BannedStudentID


class Mod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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


def setup(bot):
    bot.add_cog(Mod(bot))
