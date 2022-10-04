import discord
import orm
import re
from discord.ext import commands
from utils import VerifyCode, Student, VerifyView
import config


class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command()
    @discord.guild_only()
    async def verify(self, ctx: discord.ApplicationContext):
        """Verifies or generates a verification code"""

        try:
            student: Student = await Student.objects.get(user_id=ctx.author.id)
            return await ctx.respond(f"\N{cross mark} You're already verified as {student.id}!", ephemeral=True)
        except orm.NoMatch:
            pass

        view = VerifyView(ctx)
        return await ctx.respond(view=view, ephemeral=True)

    @commands.command(name="de-verify")
    @commands.is_owner()
    async def verification_del(self, ctx: commands.Context, *, user: discord.Member):
        """Removes a user's verification status"""
        await ctx.trigger_typing()
        for code in await VerifyCode.objects.all(bind=user.id):
            await code.delete()
        usr = await Student.objects.first(user_id=user.id)
        if usr:
            await usr.delete()

        role = discord.utils.find(lambda r: r.name.lower() == "verified", ctx.guild.roles)
        if role and role < ctx.me.top_role:
            await user.remove_roles(role, reason=f"De-verified by {ctx.author}")

        return await ctx.reply(f"\N{white heavy check mark} De-verified {user}.")

    @commands.command(name="verify")
    @commands.is_owner()
    @commands.guild_only()
    async def verification_force(self, ctx: commands.Context, user: discord.Member, _id: str):
        """Manually verifies someone"""
        await Student.objects.create(id=_id, user_id=user.id)
        role = discord.utils.find(lambda r: r.name.lower() == "verified", ctx.guild.roles)
        if role and role < ctx.me.top_role:
            member = await ctx.guild.fetch_member(ctx.author.id)
            await member.add_roles(role, reason="Verified")
        return await ctx.reply(
            "\N{white heavy check mark} Verification complete!",
        )

    @commands.user_command(name="B Number")
    @discord.guild_only()
    async def get_b_number(self, ctx: discord.ApplicationContext, member: discord.Member):
        try:
            student: Student = await Student.objects.get(user_id=member.id)
            return await ctx.respond(
                f"{member.mention}'s B number is saved as {student.id!r}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none()
            )
        except orm.NoMatch:
            return await ctx.respond(
                f"{member.mention} has no saved B number.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none()
            )


def setup(bot):
    bot.add_cog(VerifyCog(bot))
