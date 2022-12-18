import discord
import orm
from discord.ext import commands
from utils import VerifyCode, Student, VerifyView, get_or_none


class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command()
    @discord.guild_only()
    async def verify(self, ctx: discord.ApplicationContext):
        """Verifies or generates a verification code"""

        try:
            student: Student = await Student.objects.get(user_id=ctx.user.id)
            return await ctx.respond(f"\N{cross mark} You're already verified as {student.id}!", ephemeral=True)
        except orm.NoMatch:
            pass

        role = discord.utils.find(lambda r: r.name.lower() == "verified", ctx.guild.roles)
        channel = discord.utils.get(ctx.guild.text_channels, name="verify")
        if role in ctx.user.roles:
            if role and role < ctx.me.top_role:
                await ctx.user.remove_roles(role, reason=f"Auto de-verified")
                if channel:
                    try:
                        await ctx.user.send(
                            f"You have been automatically de-verified. Please re-verify by going to {channel.mention} "
                            f"and typing </verify:{ctx.command.id}>."
                        )
                    except discord.Forbidden:
                        pass
                return
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

        await ctx.message.delete(delay=10)
        return await ctx.reply(f"\N{white heavy check mark} De-verified {user}.")

    @commands.command(name="verify")
    @commands.is_owner()
    @commands.guild_only()
    async def verification_force(self, ctx: commands.Context, user: discord.Member, _id: str, name: str):
        """Manually verifies someone"""
        await Student.objects.create(id=_id, user_id=user.id, name=name)
        role = discord.utils.find(lambda r: r.name.lower() == "verified", ctx.guild.roles)
        if role and role < ctx.me.top_role:
            member = await ctx.guild.fetch_member(ctx.author.id)
            await member.add_roles(role, reason="Verified")
        await ctx.message.delete(delay=10)
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
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except orm.NoMatch:
            return await ctx.respond(
                f"{member.mention} has no saved B number.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @commands.command(name="rebind")
    @commands.is_owner()
    async def rebind_code(self, ctx: commands.Context, b_number: str, *, user: discord.Member):
        # noinspection GrazieInspection
        """Changes which account a B number is bound to"""
        student = await get_or_none(Student, id=b_number.upper())
        if student:
            await student.update(user_id=user.id)
            return await ctx.message.add_reaction("\N{white heavy check mark}")
        await ctx.message.add_reaction("\N{cross mark}")
        await ctx.message.delete(delay=10)


def setup(bot):
    bot.add_cog(VerifyCog(bot))
