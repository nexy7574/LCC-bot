import datetime
import sqlite3
import textwrap
from typing import Optional

import discord
from discord.ext import commands, tasks
import config
from utils import Assignments, Tutors, simple_embed_paginator, get_or_none, Student, hyperlink, console

BOOL_EMOJI = {True: "\N{white heavy check mark}", False: "\N{cross mark}"}

TUTOR_OPTION = discord.Option(
    str,
    "The tutor who assigned the project",
    default=None,
    choices=[x.title() for x in dir(Tutors) if not x.startswith("__") and x not in ("name", "value")],
)
__MARK_AS_OPTION_OPTIONS = ("unfinished", "finished", "unsubmitted", "submitted")
MARK_AS_OPTION = discord.Option(
    int,
    name="status",
    choices=[
        discord.OptionChoice(
            name="{}{}".format(BOOL_EMOJI[not x.startswith("un")], x),
            value=__MARK_AS_OPTION_OPTIONS.index(x),
        )
        for x in __MARK_AS_OPTION_OPTIONS
    ],
)


class TutorSelector(discord.ui.View):
    value: Optional[Tutors] = None

    @discord.ui.select(
        placeholder="Select a tutor name",
        options=[
            discord.SelectOption(label=x.title(), value=x.upper()) for x in [y.name for y in TUTOR_OPTION.choices]
        ],
    )
    async def select_tutor(self, select: discord.ui.Select, interaction2: discord.Interaction):
        await interaction2.response.defer(invisible=True)
        self.value = getattr(Tutors, select.values[0].upper())
        self.stop()


async def assignment_autocomplete(ctx: discord.AutocompleteContext) -> list[str]:
    if not ctx.value:
        results: list[Assignments] = await Assignments.objects.order_by("-due_by").limit(7).all()
    else:
        results: list[Assignments] = (
            await Assignments.objects.filter(title__icontains=ctx.value).limit(30).order_by("-entry_id").all()
        )
    return [textwrap.shorten(f"{x.entry_id}: {x.title}", 100, placeholder="...") for x in results]


# noinspection DuplicatedCode
class AssignmentsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.stop()

    def resolve_user(self, user__id: int) -> str:
        usr = self.bot.get_user(user__id)
        if usr:
            return usr.mention
        else:
            return f"<@{user__id}>"

    @tasks.loop(minutes=10)
    async def reminder_loop(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()
        try:
            view_command = "</{0.name} view:{0.id}>".format(
                self.bot.get_application_command("assignments", type=discord.SlashCommandGroup)
            )
            edit_command = "</{0.name} edit:{0.id}>".format(
                self.bot.get_application_command("assignments", type=discord.SlashCommandGroup)
            )
        except AttributeError:
            view_command = "`/assignments view`"
            edit_command = "`/assignments edit`"
        allowed_mentions = discord.AllowedMentions(everyone=True) if not config.dev else discord.AllowedMentions.none()
        allowed_mentions.users = True
        guild = self.bot.get_guild(config.guilds[0])
        general = discord.utils.get(guild.text_channels, name="general")
        if not general.can_send():
            return

        msg_format = (
            "{mentions} - {reminder_name} reminder for project {project_title} for **{project_tutor}**!\n"
            "Run '%s {project_title}' to view information on the assignment.\n"
            "*You can mark this assignment as complete with '%s {project_title}', which will prevent"
            " further reminders.*" % (view_command, edit_command)
        )

        now = datetime.datetime.now()
        assignments: list[Assignments] = await Assignments.objects.filter(submitted=False).all()
        for assignment in assignments:
            due = datetime.datetime.fromtimestamp(assignment.due_by)
            for reminder_name, reminder_time in config.reminders.items():
                if reminder_name in assignment.reminders:
                    # already sent
                    continue
                elif reminder_time != 3600 * 3 and assignment.finished is True:
                    continue
                elif isinstance(reminder_time, int) and reminder_time >= (assignment.due_by - assignment.created_at):
                    await assignment.update(reminders=assignment.reminders + [reminder_name])
                else:
                    cur_text = msg_format.format(
                        mentions=", ".join(map(self.resolve_user, assignment.assignees)) or '@everyone',
                        reminder_name=reminder_name,
                        project_title=textwrap.shorten(assignment.title, 100, placeholder="..."),
                        project_tutor=assignment.tutor.name.title(),
                    )
                    if isinstance(reminder_time, datetime.time):
                        if now.date() == due.date():
                            if now.time().hour == reminder_time.hour:
                                try:
                                    await general.send(
                                        cur_text,
                                        allowed_mentions=allowed_mentions,
                                    )
                                except discord.HTTPException:
                                    pass
                                else:
                                    await assignment.update(reminders=assignment.reminders + [reminder_name])
                    else:
                        time = due - datetime.timedelta(seconds=reminder_time)
                        if time <= now:
                            try:
                                await general.send(
                                    cur_text,
                                    allowed_mentions=allowed_mentions,
                                )
                            except discord.HTTPException:
                                pass
                            else:
                                await assignment.update(reminders=assignment.reminders + [reminder_name])

    def generate_assignment_embed(self, assignment: Assignments) -> discord.Embed:
        embed = discord.Embed(
            title=f"Assignment #{assignment.entry_id}",
            description=f"**Title:**\n>>> {assignment.title}",
            colour=discord.Colour.random(),
        )

        if assignment.classroom:
            classroom = hyperlink(assignment.classroom, max_length=1024)
        else:
            classroom = "No classroom link."

        if assignment.shared_doc:
            shared_doc = hyperlink(assignment.shared_doc, max_length=1024)
        else:
            shared_doc = "No shared document."

        embed.add_field(name="Classroom URL:", value=classroom, inline=False),
        embed.add_field(name="Shared Document URL:", value=shared_doc)
        embed.add_field(name="Tutor:", value=assignment.tutor.name.title(), inline=False)
        user_id = getattr(assignment.created_by, "user_id", assignment.entry_id)
        embed.add_field(name="Created:", value=f"<t:{assignment.created_at:.0f}:R> by <@{user_id}>", inline=False)
        embed.add_field(
            name="Due:",
            value=f"<t:{assignment.due_by:.0f}:R> "
            f"(finished: {BOOL_EMOJI[assignment.finished]} | Submitted: {BOOL_EMOJI[assignment.submitted]})",
            inline=False,
        )
        embed.add_field(
            name="Assignees",
            value=", ".join(map(self.resolve_user, assignment.assignees))
        )
        if assignment.reminders:
            embed.set_footer(text="Reminders sent: " + ", ".join(assignment.reminders))
        return embed

    assignments_command = discord.SlashCommandGroup("assignments", "Assignment/project management", guild_only=True)

    @assignments_command.command(name="list")
    async def list_assignments(
        self,
        ctx: discord.ApplicationContext,
        limit: int = 20,
        upcoming_only: bool = True,
        tutor_name: TUTOR_OPTION = None,
        unfinished_only: bool = False,
        unsubmitted_only: bool = False,
    ):
        """Lists assignments."""
        tutor_name: Optional[str]
        query = Assignments.objects.limit(limit).order_by("-due_by")
        if upcoming_only is True:
            now = datetime.datetime.now().timestamp()
            query = query.filter(due_by__gte=now)

        if tutor_name is not None:
            query = query.filter(tutor=getattr(Tutors, tutor_name.upper()))

        if unfinished_only is True:
            query = query.filter(finished=False)

        if unsubmitted_only:
            query = query.filter(submitted=False)

        await ctx.defer()
        lines = []
        for assignment in await query.all():
            assignment: Assignments
            due_by = datetime.datetime.fromtimestamp(assignment.due_by)
            lines.append(
                f"#{assignment.entry_id!s}: Set by **{assignment.tutor.name.title()}**, "
                f"due {discord.utils.format_dt(due_by, 'R')}"
            )

        embeds = simple_embed_paginator(lines, assert_ten=True, colour=ctx.author.colour)
        embeds = embeds or [discord.Embed(description="No projects match the provided criteria.")]

        return await ctx.respond(embeds=embeds)

    @assignments_command.command(name="add")
    async def create_assignment(self, ctx: discord.ApplicationContext):
        """Adds/creates an assignment."""
        author = await get_or_none(Student, user_id=ctx.author.id)
        if author is None:
            return await ctx.respond("\N{cross mark} You must have verified to use this command.", ephemeral=True)

        class AddModal(discord.ui.Modal):
            def __init__(self, kwargs: dict = None):
                self.msg: Optional[discord.WebhookMessage] = None
                self.create_kwargs = kwargs or {
                    "created_by": author,
                    "title": None,
                    "classroom": None,
                    "shared_doc": None,
                    "due_by": None,
                    "tutor": None,
                    "assignees": []
                }
                super().__init__(
                    discord.ui.InputText(
                        custom_id="title",
                        label="Assignment Title",
                        min_length=2,
                        max_length=2000,
                        value=self.create_kwargs["title"],
                    ),
                    discord.ui.InputText(
                        custom_id="classroom",
                        label="Google Classroom Link",
                        max_length=4000,
                        required=False,
                        placeholder="Optional, can be added later.",
                        value=self.create_kwargs["classroom"],
                    ),
                    discord.ui.InputText(
                        custom_id="shared_doc",
                        label="Shared Document Link",
                        max_length=4000,
                        required=False,
                        placeholder="Google docs, slides, powerpoint, etc. Optional.",
                        value=self.create_kwargs["shared_doc"],
                    ),
                    discord.ui.InputText(
                        custom_id="due_by",
                        label="Due by",
                        max_length=16,
                        min_length=14,
                        placeholder="dd/mm/yy hh:mm".upper(),
                        value=(
                            self.create_kwargs["due_by"].strftime("%d/%m/%y %H:%M")
                            if self.create_kwargs["due_by"]
                            else None
                        ),
                    ),
                    title="Add an assignment",
                    timeout=300,
                )

            async def callback(self, interaction: discord.Interaction):
                await interaction.response.defer()
                self.create_kwargs["title"] = self.children[0].value
                self.create_kwargs["classroom"] = self.children[1].value or None
                self.create_kwargs["shared_doc"] = self.children[2].value or None
                try:
                    self.create_kwargs["due_by"] = datetime.datetime.strptime(
                        self.children[3].value,
                        "%d/%m/%y %H:%M" if len(self.children[3].value) == 14 else "%d/%m/%Y %H:%M",
                    )
                except ValueError:

                    class TryAgainView(discord.ui.View):
                        def __init__(self, kw):
                            self._mod = None
                            self.kw = kw
                            super().__init__(timeout=330)

                        @property
                        def modal(self) -> Optional[AddModal]:
                            return self._mod

                        @discord.ui.button(label="Try again", style=discord.ButtonStyle.primary)
                        async def try_again(self, _, interaction2: discord.Interaction):
                            self.disable_all_items()
                            self._mod = AddModal(self.kw)
                            await interaction2.response.send_modal(self._mod)
                            await interaction2.edit_original_response(view=self)
                            await self._mod.wait()
                            self.stop()

                    v = TryAgainView(self.create_kwargs)
                    msg = await interaction.followup.send("\N{cross mark} Failed to parse date - try again?", view=v)
                    await v.wait()
                    if v.modal:
                        self.create_kwargs = v.modal.create_kwargs
                    else:
                        return
                else:
                    view = TutorSelector()
                    msg = await interaction.followup.send("Which tutor assigned this project?", view=view)
                    await view.wait()
                    self.create_kwargs["tutor"] = view.value

                    class SelectAssigneesView(discord.ui.View):
                        def __init__(self):
                            super().__init__()
                            self.users = []

                        @discord.ui.user_select(placeholder="Select some people...", min_values=0, max_values=20)
                        async def select_users(self, select: discord.ui.Select, interaction2: discord.Interaction):
                            self.disable_all_items()
                            self.users = select.values
                            await interaction2.edit_original_response(view=self)
                            self.stop()

                        @discord.ui.button(label="skip", style=discord.ButtonStyle.primary)
                        async def skip(self, _, interaction2: discord.Interaction):
                            self.disable_all_items()
                            await interaction2.edit_original_response(view=self)
                            self.stop()

                    assigner = SelectAssigneesView()
                    await msg.edit(
                        content="Please select people who've been assigned to this task (leave blank or skip to assign"
                                " everyone)",
                        view=assigner
                    )
                    await assigner.wait()
                    self.create_kwargs["assignees"] = [x.id for x in assigner.users]

                self.msg = msg
                self.stop()

        modal = AddModal()
        await ctx.send_modal(modal)
        await modal.wait()
        if not modal.msg:
            return
        await modal.msg.edit(content="Creating assignment...", view=None)
        try:
            modal.create_kwargs["due_by"] = modal.create_kwargs["due_by"].timestamp()
            await Assignments.objects.create(**modal.create_kwargs)
        except sqlite3.Error as e:
            return await modal.msg.edit(content="SQL Error: %s.\nAssignment not saved." % e)
        else:
            return await modal.msg.edit(content=f"\N{white heavy check mark} Created assignment!")

    @assignments_command.command(name="view")
    async def get_assignment(
        self, ctx: discord.ApplicationContext, title: discord.Option(str, autocomplete=assignment_autocomplete)
    ):
        """Views an assignment's details"""
        try:
            entry_id = int(title.split(":", 1)[0])
        except ValueError:
            return await ctx.respond("\N{cross mark} Invalid Input.")
        assignment: Assignments = await get_or_none(Assignments, entry_id=int(entry_id))
        if not assignment:
            return await ctx.respond("\N{cross mark} Unknown assignment.")
        try:
            await assignment.created_by.load()
        except AttributeError:
            console.log(f"[red]Failed to load created_by row for assignment {assignment.entry_id}")
        return await ctx.respond(embed=self.generate_assignment_embed(assignment))

    @assignments_command.command(name="edit")
    async def edit_assignment(
        self, ctx: discord.ApplicationContext, title: discord.Option(str, autocomplete=assignment_autocomplete)
    ):
        """Edits an assignment"""
        try:
            entry_id = int(title.split(":", 1)[0])
        except ValueError:
            return await ctx.respond("\N{cross mark} Invalid Input.")
        assignment: Assignments = await get_or_none(Assignments, entry_id=int(entry_id))
        if not assignment:
            return await ctx.respond("\N{cross mark} Unknown assignment.")
        await assignment.created_by.load()
        cog = self

        class EditAssignmentView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=300)

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user == ctx.author

            async def on_timeout(self) -> None:
                await self.message.delete(delay=0.1)

            async def update_display(self, interaction: discord.Interaction):
                complete_d = "incomplete" if assignment.finished else "complete"
                submitted_d = "unsubmitted" if assignment.submitted else "submitted"
                self.get_item("complete").label = "Mark as " + complete_d
                self.get_item("submitted").label = "Mark as " + submitted_d
                await interaction.edit_original_response(view=self)

            @discord.ui.button(label="Update title")
            async def update_title(self, _, interaction: discord.Interaction):
                class UpdateTitleModal(discord.ui.Modal):
                    def __init__(self):
                        super().__init__(
                            discord.ui.InputText(
                                style=discord.InputTextStyle.long,
                                label="New title",
                                value=assignment.title,
                                min_length=2,
                                max_length=4000,
                            ),
                            title="Update assignment title",
                        )

                    async def callback(self, _interaction: discord.Interaction):
                        await _interaction.response.defer()
                        await assignment.update(title=self.children[0].value)
                        await _interaction.followup.send(
                            "\N{white heavy check mark} Changed assignment title!", delete_after=5
                        )
                        self.stop()

                modal = UpdateTitleModal()
                await interaction.response.send_modal(modal)
                await self.update_display(interaction)

            @discord.ui.button(label="Update classroom URL")
            async def update_classroom_url(self, _, interaction: discord.Interaction):
                class UpdateClassroomURL(discord.ui.Modal):
                    def __init__(self):
                        super().__init__(
                            discord.ui.InputText(
                                style=discord.InputTextStyle.long,
                                label="New Classroom URL",
                                value=assignment.classroom,
                                required=False,
                                max_length=4000,
                            ),
                            title="Update Classroom url",
                        )

                    async def callback(self, _interaction: discord.Interaction):
                        await _interaction.response.defer()
                        try:
                            await assignment.update(classroom=self.children[0].value)
                            await _interaction.followup.send(
                                "\N{white heavy check mark} Changed classroom URL!", delete_after=5
                            )
                        except sqlite3.Error:
                            await _interaction.followup.send(
                                "\N{cross mark} Failed to apply changes - are you sure you put a valid URL in?"
                            )
                        finally:
                            self.stop()

                modal = UpdateClassroomURL()
                await interaction.response.send_modal(modal)
                await self.update_display(interaction)

            @discord.ui.button(label="Update shared document url")
            async def update_shared_document_url(self, _, interaction: discord.Interaction):
                class UpdateSharedDocumentModal(discord.ui.Modal):
                    def __init__(self):
                        super().__init__(
                            discord.ui.InputText(
                                style=discord.InputTextStyle.long,
                                label="New shared document URL",
                                value=assignment.shared_doc,
                                required=False,
                                max_length=4000,
                            ),
                            title="Update shared document url",
                        )

                    async def callback(self, _interaction: discord.Interaction):
                        await _interaction.response.defer()
                        try:
                            await assignment.update(shared_doc=self.children[0].value)
                            await _interaction.followup.send(
                                "\N{white heavy check mark} Changed shared doc URL!", delete_after=5
                            )
                        except sqlite3.Error:
                            await _interaction.followup.send(
                                "\N{cross mark} Failed to apply changes - are you sure you put a valid URL in?"
                            )
                        finally:
                            self.stop()

                modal = UpdateSharedDocumentModal()
                await interaction.response.send_modal(modal)
                await self.update_display(interaction)

            @discord.ui.button(label="Update tutor")
            async def update_tutor(self, _, interaction: discord.Interaction):
                await interaction.response.defer()
                view = TutorSelector()
                msg: discord.WebhookMessage = await interaction.followup.send(
                    "Which tutor assigned this project?", view=view
                )
                await view.wait()
                await assignment.update(tutor=view.value)
                await msg.edit(
                    content=f"\N{white heavy check mark} Changed tutor to {view.value.name.title()}", view=None
                )
                await msg.delete(delay=5)
                await self.update_display(interaction)

            @discord.ui.button(label="Update due date")
            async def update_due(self, _, interaction: discord.Interaction):
                class UpdateDateModal(discord.ui.Modal):
                    def __init__(self):
                        self.date = datetime.datetime.fromtimestamp(assignment.due_by)
                        super().__init__(
                            discord.ui.InputText(
                                label="New due by date",
                                placeholder=self.date.strftime("%d/%m/%y %H:%M"),
                                value=self.date.strftime("%d/%m/%y %H:%M"),
                                min_length=14,
                                max_length=16,
                            ),
                            title="Change due by date",
                        )

                    async def callback(self, _interaction: discord.Interaction):
                        await _interaction.response.defer()
                        try:
                            new = datetime.datetime.strptime(
                                self.children[1].value,
                                "%d/%m/%y %H:%M" if len(self.children[1].value) == 14 else "%d/%m/%Y %H:%M",
                            )
                        except ValueError:
                            await _interaction.followup.send(
                                "\N{cross mark} Failed to parse URL. Make sure you passed in dd/mm/yy hh:mm"
                                " (e.g. {})".format(datetime.datetime.now().strftime("%d/%m/%y %H:%M"))
                            )
                            self.stop()
                        else:
                            try:
                                await assignment.update(due_by=new.timestamp(), reminders=[])
                                await _interaction.followup.send(
                                    "\N{white heavy check mark} Changed due by date & reset reminders.", delete_after=5
                                )
                            except sqlite3.Error:
                                await _interaction.followup.send("\N{cross mark} Failed to apply changes.")
                            finally:
                                self.stop()

                await interaction.response.send_modal(UpdateDateModal())
                await self.update_display(interaction)

            @discord.ui.button(label="Mark as [in]complete", custom_id="complete")
            async def mark_as_complete(self, _, interaction: discord.Interaction):
                await interaction.response.defer()
                if assignment.submitted is True and assignment.submitted is True:
                    return await interaction.followup.send(
                        "\N{cross mark} You cannot mark an assignment as incomplete if it is marked as submitted!"
                    )
                await assignment.update(finished=not assignment.finished)
                await self.update_display(interaction)
                return await interaction.followup.send(
                    "\N{white heavy check mark} Assignment is now marked as {}complete.".format(
                        "in" if assignment.finished is False else ""
                    )
                )

            @discord.ui.button(label="Mark as [un]submitted", custom_id="submitted")
            async def mark_as_submitted(self, _, interaction: discord.Interaction):
                await interaction.response.defer()
                if assignment.finished is False and assignment.submitted is False:
                    return await interaction.followup.send(
                        "\N{cross mark} You cannot mark an assignment as submitted if it is not marked as complete!",
                        delete_after=10,
                    )
                await assignment.update(submitted=not assignment.submitted)
                await self.update_display(interaction)
                return await interaction.followup.send(
                    "\N{white heavy check mark} Assignment is now marked as {}submitted.".format(
                        "in" if assignment.submitted is False else ""
                    ),
                    delete_after=5,
                )

            @discord.ui.button(label="Save & Exit")
            async def finish(self, _, interaction: discord.Interaction):
                await interaction.response.defer()
                await interaction.delete_original_response(delay=0.1)
                self.stop()

            @discord.ui.button(label="View details")
            async def view_details(self, _, interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                await assignment.created_by.load()
                await interaction.followup.send(
                    embed=cog.generate_assignment_embed(assignment), ephemeral=True
                )
                await self.update_display(interaction)

        await ctx.respond(view=EditAssignmentView())

    @assignments_command.command(name="remove")
    async def remove_assignment(
        self, ctx: discord.ApplicationContext, title: discord.Option(str, autocomplete=assignment_autocomplete)
    ):
        """Edits an assignment"""
        try:
            entry_id = int(title.split(":", 1)[0])
        except ValueError:
            return await ctx.respond("\N{cross mark} Invalid Input.")
        assignment: Assignments = await get_or_none(Assignments, entry_id=int(entry_id))
        if not assignment:
            return await ctx.respond("\N{cross mark} Unknown assignment.")
        await assignment.delete()
        return await ctx.respond(f"\N{white heavy check mark} Deleted assignment #{assignment.entry_id}.")


def setup(bot):
    bot.add_cog(AssignmentsCog(bot))
