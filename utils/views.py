import secrets

import discord
import re
import orm

from utils import send_verification_code, get_or_none, Student, VerifyCode, console, TOKEN_LENGTH, BannedStudentID


class VerifyView(discord.ui.View):
    def __init__(self, ctx: discord.ApplicationContext):
        self.ctx = ctx
        super().__init__(timeout=300, disable_on_timeout=True)

    @discord.ui.button(
        label="I have a verification code!",
        emoji="\U0001f4e7",
        custom_id="have"
    )
    async def have(self, _, interaction1: discord.Interaction):
        class Modal(discord.ui.Modal):
            def __init__(self):
                super().__init__(
                    discord.ui.InputText(
                        custom_id="code",
                        label="Verification Code",
                        placeholder="e.g: " + secrets.token_hex(TOKEN_LENGTH),
                        min_length=TOKEN_LENGTH*2,
                        max_length=TOKEN_LENGTH*2,
                    ),
                    title="Enter the verification code in your inbox",
                )

            async def callback(self, interaction: discord.Interaction):
                await interaction.response.defer()
                code = self.children[0].value
                if not code:  # timed out
                    self.stop()
                    return
                else:
                    try:
                        existing: VerifyCode = await VerifyCode.objects.get(code=code)
                    except orm.NoMatch:
                        self.stop()
                        return await interaction.followup.send(
                            "\N{cross mark} Invalid or unknown verification code. Try again!", ephemeral=True
                        )
                    else:
                        ban = await get_or_none(BannedStudentID, student_id=existing.student_id)
                        if ban is not None:
                            self.stop()
                            return await interaction.user.ban(
                                reason=f"Attempted to verify with banned student ID {ban.student_id}"
                                       f" (originally associated with account {ban.associated_account})"
                            )
                        await Student.objects.create(id=existing.student_id, user_id=interaction.user.id)
                        await existing.delete()
                        role = discord.utils.find(lambda r: r.name.lower() == "verified", interaction.guild.roles)
                        if role and role < interaction.guild.me.top_role:
                            member = await interaction.guild.fetch_member(interaction.user.id)
                            await member.add_roles(role, reason="Verified")
                        console.log(f"[green]{interaction.user} verified ({interaction.user.id}/{existing.student_id})")
                        self.stop()
                        return await interaction.followup.send(
                            "\N{white heavy check mark} Verification complete!",
                            ephemeral=True
                        )

        await interaction1.response.send_modal(Modal())
        self.disable_all_items()
        await interaction1.edit_original_response(view=self)
        await interaction1.delete_original_response(delay=1)

    @discord.ui.button(
        label="Send me a verification code.",
        emoji="\U0001f4e5"
    )
    async def send(self, btn: discord.ui.Button, interaction1: discord.Interaction):
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
                    return await interaction.response.send_message(
                        "\N{cross mark} Invalid student ID.",
                        delete_after=60
                    )

                ex = await get_or_none(Student, id=st)
                if ex:
                    return await interaction.response.send_message(
                        "\N{cross mark} Student ID is already associated.",
                        delete_after=60
                    )

                _code = await send_verification_code(interaction.user, st)
                console.log(f"Sending verification email to {interaction.user} ({interaction.user.id}/{st})...")
                __code = await VerifyCode.objects.create(code=_code, bind=interaction.id, student_id=st)
                console.log(f"[green]Sent verification email to {interaction.user} ({interaction.user.id}/{st}): "
                            f"{_code!r}")
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

        await interaction1.response.send_modal(Modal())
        btn.disabled = True
        await interaction1.edit_original_response(view=self)

    @discord.ui.button(
        label="Why do I need a verification code?",
        emoji="\U0001f616"
    )
    async def why(self, _, interaction: discord.Interaction):
        await interaction.response.defer(
            ephemeral=True
        )
        await interaction.followup.send(
            "In order to access this server, you need to enter your student ID.\n"
            "We require this to make sure only **students** in our course can access the server.\n"
            "Your B number (student ID) is found on your ID card (the one you use to scan into the building).\n"
            "This is not invading your privacy, your B number is publicly visible, as it is the start of your email,"
            " plus can be found on google chat.",
            ephemeral=True,
            delete_after=60
        )

