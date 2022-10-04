import discord
import orm
import re
from discord.ext import commands
from utils import send_verification_code, VerifyCode, Student, console
import config


class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command()
    async def verify(self, ctx: discord.ApplicationContext, *, code: str = None):
        """Verifies or generates a verification code"""
        guild = self.bot.get_guild(config.guilds[0])
        # if ctx.guild is not None:
        #     return await ctx.respond("\N{cross mark} This command can only be run in my DMs!", ephemeral=True)

        try:
            student: Student = await Student.objects.get(user_id=ctx.author.id)
            return await ctx.respond(f"\N{cross mark} You're already verified as {student.id}!", ephemeral=True)
        except orm.NoMatch:
            pass

        if code is None:

            class Modal(discord.ui.Modal):
                def __init__(self):
                    super().__init__(
                        discord.ui.InputText(
                            custom_id="student_id",
                            label="What is your student ID",
                            placeholder="B...",
                            min_length=7,
                            max_length=7,
                        ),
                        title="Enter your student ID number",
                    )

                async def callback(self, interaction: discord.Interaction):
                    await interaction.response.defer()
                    st = self.children[0].value
                    if not st:  # timed out
                        return

                    if not re.match(r"^B\d{6}$", st):
                        return await interaction.response.send_message("\N{cross mark} Invalid student ID.")

                    _code = await send_verification_code(ctx.author, st)
                    console.log(f"Sending verification email to {ctx.author} ({ctx.author.id}/{st})...")
                    __code = await VerifyCode.objects.create(code=_code, bind=ctx.author.id, student_id=st)
                    console.log(f"[green]Sent verification email to {ctx.author} ({ctx.author.id}/{st}): {_code!r}")
                    await interaction.followup.send(
                        "\N{white heavy check mark} Verification email sent to your college email "
                        f"({st}@my.leedscitycollege.ac.uk)\n"
                        f"Once you get that email, run this command again, with the first option being the 16"
                        f" character code.\n\n"
                        f">>> If you don't know how to access your email, go to <https://gmail.com>, then "
                        f"sign in as `{st}@leedscitycollege.ac.uk` (notice there's no `my.` "
                        f"prefix to sign into gmail), and you should be greeted by your inbox. The default password "
                        f"is your birthday, !, and the first three letters of your first or last name"
                        f" (for example, `John Doe`, born on the 1st of february 2006, would be either "
                        f"`01022006!Joh` or `01022006!Doe`).",
                        ephemeral=True,
                    )

            return await ctx.send_modal(Modal())
        else:
            try:
                existing: VerifyCode = await VerifyCode.objects.get(code=code)
            except orm.NoMatch:
                return await ctx.respond(
                    "\N{cross mark} Invalid or unknown verification code. Try again!", ephemeral=True
                )
            else:
                await Student.objects.create(id=existing.student_id, user_id=ctx.author.id)
                await existing.delete()
                role = discord.utils.find(lambda r: r.name.lower() == "verified", guild.roles)
                if role and role < guild.me.top_role:
                    member = await guild.fetch_member(ctx.author.id)
                    await member.add_roles(role, reason="Verified")
                console.log(f"[green]{ctx.author} verified ({ctx.author.id}/{existing.student_id})")
                return await ctx.respond("\N{white heavy check mark} Verification complete!", ephemeral=True)

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
            student: Student = await Student.objects.get(user_id=ctx.author.id)
            return await ctx.respond(
                f"{member.mention}'s B number is saved as {student.id!r}.",
                allowed_mentions=discord.AllowedMentions.none()
            )
        except orm.NoMatch:
            pass
        return await ctx.respond(
            f"{member.mention} has no saved B number.",
            allowed_mentions=discord.AllowedMentions.none()
        )


def setup(bot):
    bot.add_cog(VerifyCog(bot))
