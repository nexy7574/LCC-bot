import random
import secrets
from datetime import datetime, timedelta

import discord
import typing
import re
import orm
from discord.ui import View

from utils import send_verification_code, get_or_none, Student, VerifyCode, console, TOKEN_LENGTH, BannedStudentID

if typing.TYPE_CHECKING:
    from cogs.timetable import TimeTableCog


class VerifyView(View):
    def __init__(self, ctx: discord.ApplicationContext):
        self.ctx = ctx
        super().__init__(timeout=300, disable_on_timeout=True)

    @discord.ui.button(label="I have a verification code!", emoji="\U0001f4e7", custom_id="have")
    async def have(self, _, interaction1: discord.Interaction):
        class Modal(discord.ui.Modal):
            def __init__(self):
                super().__init__(
                    discord.ui.InputText(
                        custom_id="code",
                        label="Verification Code",
                        placeholder="e.g: " + secrets.token_hex(TOKEN_LENGTH),
                        min_length=TOKEN_LENGTH * 2,
                        max_length=TOKEN_LENGTH * 2,
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
                            return await interaction.user.kick(
                                reason=f"Attempted to verify with banned student ID {ban.student_id}"
                                f" (originally associated with account {ban.associated_account})"
                            )
                        await Student.objects.create(
                            id=existing.student_id,
                            user_id=interaction.user.id,
                            name=existing.name
                        )
                        await existing.delete()
                        role = discord.utils.find(lambda r: r.name.lower() == "verified", interaction.guild.roles)
                        member = await interaction.guild.fetch_member(interaction.user.id)
                        if role and role < interaction.guild.me.top_role:
                            await member.add_roles(role, reason="Verified")
                        try:
                            await member.edit(
                                nick=f"{existing.name}",
                                reason="Verified"
                            )
                        except discord.HTTPException:
                            pass
                        console.log(f"[green]{interaction.user} verified ({interaction.user.id}/{existing.student_id})")
                        self.stop()
                        return await interaction.followup.send(
                            "\N{white heavy check mark} Verification complete!", ephemeral=True
                        )

        await interaction1.response.send_modal(Modal())
        self.disable_all_items()
        await interaction1.edit_original_response(view=self)
        await interaction1.delete_original_response(delay=1)

    @discord.ui.button(label="Send me a verification code.", emoji="\U0001f4e5")
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
                    discord.ui.InputText(
                        custom_id="name",
                        label="What is your name?",
                        placeholder="Nicknames are okay too.",
                        min_length=2,
                        max_length=32,
                    ),
                    title="Enter your student ID number",
                    timeout=120,
                )

            async def callback(self, interaction: discord.Interaction):
                await interaction.response.defer()
                st = self.children[0].value.strip()
                if not st:  # timed out
                    return

                if not re.match(r"^B\d{6}$", st):
                    btn.disabled = False
                    return await interaction.followup.send(
                        "\N{cross mark} Invalid student ID - Failed to verify with regex."
                        " Please try again with a valid student ID. Make sure it is formatted as `BXXXXXX` "
                        "(e.g. `B{}`)".format(''.join(str(random.randint(0, 9)) for _ in range(6))),
                        delete_after=60
                    )

                ex = await get_or_none(Student, id=st)
                if ex:
                    btn.disabled = False
                    return await interaction.followup.send(
                        "\N{cross mark} Student ID is already associated.", delete_after=60
                    )

                try:
                    _code = await send_verification_code(interaction.user, st)
                except Exception as e:
                    return await interaction.followup.send(f"\N{cross mark} Failed to send email - {e}. Try again?")
                console.log(f"Sending verification email to {interaction.user} ({interaction.user.id}/{st})...")
                name = self.children[1].value
                __code = await VerifyCode.objects.create(code=_code, bind=interaction.id, student_id=st, name=name)
                console.log(
                    f"[green]Sent verification email to {interaction.user} ({interaction.user.id}/{st}): " f"{_code!r}"
                )
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

        modal = Modal()
        await interaction1.response.send_modal(modal)
        btn.disabled = True
        await interaction1.edit_original_response(view=self)
        await modal.wait()
        await interaction1.edit_original_response(view=self)

    @discord.ui.button(label="Why do I need a verification code?", emoji="\U0001f616")
    async def why(self, _, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            "In order to access this server, you need to enter your student ID.\n"
            "We require this to make sure only **students** in our course can access the server.\n"
            "Your B number (student ID) is found on your ID card (the one you use to scan into the building).\n"
            "This is not invading your privacy, your B number is publicly visible, as it is the start of your email,"
            " plus can be found on google chat.",
            ephemeral=True,
            delete_after=60,
        )


class TimeTableDaySwitcherView(View):
    def mod_date(self, by: int):
        self.current_date += timedelta(days=by)
        self.update_buttons()

    def update_buttons(self):
        def _format(d: datetime) -> str:
            return d.strftime("(%A) %d/%m/%Y")

        day_before = self.current_date + timedelta(days=-1)
        day_after = self.current_date + timedelta(days=1)
        for child in self.children:
            # noinspection PyUnresolvedReferences
            if child.custom_id == "day_before":
                child.label = _format(day_before)
            elif child.custom_id == "day_after":
                child.label = _format(day_after)
            else:
                child.label = _format(self.current_date)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user == self.user

    @discord.ui.button(custom_id="day_before", emoji="\N{leftwards black arrow}")
    async def day_before(self, _, interaction: discord.Interaction):
        self.mod_date(-1)
        await interaction.response.edit_message(content=self.cog.format_timetable_message(self.current_date), view=self)

    @discord.ui.button(custom_id="custom_day", emoji="\N{tear-off calendar}", style=discord.ButtonStyle.primary)
    async def current_day(self, _, interaction1: discord.Interaction):
        self1 = self

        class InputModal(discord.ui.Modal):
            def __init__(self):
                super().__init__(
                    discord.ui.InputText(
                        label="Date",
                        placeholder="DD/MM/YY",
                        min_length=6,
                        max_length=8,
                        required=True,
                    ),
                    title="Date to view timetable of:",
                )

            async def callback(self, interaction2: discord.Interaction):
                try:
                    self1.current_date = datetime.strptime(self.children[0].value, "%d/%m/%y")
                except ValueError:
                    await interaction2.response.send_message("Invalid date", ephemeral=True)
                else:
                    self1.update_buttons()
                    await interaction2.response.edit_message(
                        content=self1.cog.format_timetable_message(self1.current_date), view=self1
                    )

        return await interaction1.response.send_modal(InputModal())

    @discord.ui.button(custom_id="day_after", emoji="\N{black rightwards arrow}")
    async def day_after(self, _, interaction: discord.Interaction):
        self.mod_date(1)
        await interaction.response.edit_message(content=self.cog.format_timetable_message(self.current_date), view=self)

    def __init__(self, user: discord.User, instance: "TimeTableCog", date: datetime):
        super().__init__(disable_on_timeout=True)
        self.user = user
        self.cog = instance
        self.current_date = date


class SelectAssigneesView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.users = []

    @discord.ui.user_select(placeholder="Select some people...", min_values=0, max_values=20)
    async def select_users(self, select: discord.ui.Select, interaction2: discord.Interaction):
        await interaction2.response.defer()
        self.disable_all_items()
        self.users = select.values
        await interaction2.edit_original_response(view=self)
        self.stop()

    @discord.ui.button(label="skip", style=discord.ButtonStyle.primary)
    async def skip(self, _, interaction2: discord.Interaction):
        await interaction2.response.defer()
        self.disable_all_items()
        await interaction2.edit_original_response(view=self)
        self.stop()
