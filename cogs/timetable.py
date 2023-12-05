import json
import logging
import random
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Union

import config
import discord
from discord.ext import commands, tasks

from utils import TimeTableDaySwitcherView, console


def schedule_times():
    times = []
    for h in range(24):
        for m in range(0, 60, 15):
            times.append(time(h, m, 0))
    return times


class TimeTableCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = logging.getLogger("jimmy.cogs.timetable")
        with (Path.cwd() / "utils" / "timetable.json").open() as file:
            self.timetable = json.load(file)
        self.update_status.start()

    def cog_unload(self):
        self.update_status.stop()

    def are_on_break(self, date: datetime = None) -> Optional[Dict[str, Union[str, datetime]]]:
        """Checks if the date is one as a term break"""
        date = date or datetime.now()
        # That description made no sense what
        for name, dates in self.timetable["breaks"].items():
            start_date = datetime.strptime(dates["start"], "%d/%m/%Y")
            end_date = datetime.strptime(dates["end"], "%d/%m/%Y")
            # noinspection PyChainedComparisons
            if date.timestamp() <= end_date.timestamp() and date.timestamp() >= start_date.timestamp():
                return {"name": name, "start": start_date, "end": end_date}

    def format_timetable_message(self, date: datetime) -> str:
        """Pre-formats the timetable or error message."""
        if _break := self.are_on_break(date):
            return f"No lessons on {discord.utils.format_dt(date, 'D')} - On break {_break['name']!r}."

        lessons = self.timetable.get(date.strftime("%A").lower(), [])
        if not lessons:
            return f"No lessons on {discord.utils.format_dt(date, 'D')}."

        blocks = [f"```\nTimetable for {date.strftime('%A')} ({date.strftime('%d/%m/%Y')}):\n```"]
        for lesson in lessons:
            lesson.setdefault("name", "unknown")
            lesson.setdefault("tutor", "unknown")
            lesson.setdefault("room", "unknown")
            start_datetime = date.replace(hour=lesson["start"][0], minute=lesson["start"][1])
            end_datetime = date.replace(hour=lesson["end"][0], minute=lesson["end"][1])
            text = (
                f"{discord.utils.format_dt(start_datetime, 't')} to {discord.utils.format_dt(end_datetime, 't')}"
                f":\n> Lesson Name: {lesson['name']!r}\n"
                f"> Tutor: **{lesson['tutor']}**\n> Room: `{lesson['room']}`"
            )
            blocks.append(text)
        return "\n\n".join(blocks)

    def current_lesson(self, date: datetime = None) -> Optional[dict]:
        date = date or datetime.now()
        lessons = self.timetable.get(date.strftime("%A").lower(), [])
        if not lessons:
            return
        for lesson in lessons:
            now = date.time()
            start_time = time(*lesson["start"])
            end_time = time(*lesson["end"])
            if now >= end_time:
                continue
            elif now < start_time:
                continue
            else:
                # We are now currently actively in the lesson.
                lesson = lesson.copy()
                end_datetime = date.replace(hour=end_time.hour, minute=end_time.minute)
                lesson["end"] = end_time
                lesson["start"] = start_time
                lesson["end_datetime"] = end_datetime
                return lesson

    def next_lesson(self, date: datetime = None) -> Optional[dict]:
        date = date or datetime.now()
        lessons = self.timetable.get(date.strftime("%A").lower(), []).copy()
        if not lessons:
            return
        for lesson in lessons:
            now = date.time()
            start_time = time(*lesson["start"])
            end_time = time(*lesson["end"])
            if now > end_time:
                continue
            elif now < start_time:
                lesson = lesson.copy()
                end_datetime = date.replace(hour=start_time.hour, minute=start_time.minute)
                lesson["end"] = end_time
                lesson["start"] = start_time
                lesson["start_datetime"] = end_datetime
                return lesson

    def absolute_next_lesson(self, date: datetime = None) -> dict:
        date = date or datetime.now()
        # Check if there's another lesson today
        lesson = self.next_lesson(date)
        # If there's another lesson, great, return that
        # Otherwise, we need to start looking ahead.
        if lesson is None or lesson["start_datetime"] < datetime.now():
            # Loop until we find the next day when it isn't the weekend, and we aren't on break.
            next_available_date = date.replace(hour=0, minute=0, second=0)
            while self.are_on_break(next_available_date) or not self.timetable.get(
                next_available_date.strftime("%A").lower()
            ):
                next_available_date += timedelta(days=1)
                if next_available_date.year >= 2024:
                    raise RuntimeError("Failed to fetch absolute next lesson")
                # NOTE: This could be *even* more efficient but honestly as long as it works it's fine
            lesson = self.next_lesson(next_available_date)  # This *must* be a date given the second part of the
            # while loop's `or` statement.
            assert lesson, "Unable to figure out the next lesson."
        return lesson

    async def update_timetable_message(
        self,
        message: Union[discord.Message, discord.ApplicationContext, discord.InteractionMessage],
        date: datetime = None,
        *,
        no_prefix: bool = False,
    ):
        date = date or datetime.now()
        _break = self.are_on_break(date)
        if _break is not None:
            next_lesson = self.absolute_next_lesson(date + timedelta(days=1))
            next_lesson.setdefault("name", "unknown")
            next_lesson.setdefault("tutor", "unknown")
            next_lesson.setdefault("room", "unknown")
            next_lesson.setdefault("start_datetime", discord.utils.utcnow())
            text = (
                "[tt] On break {!r} from {} until {}. Break ends {}, and the first lesson back is "
                "{lesson[name]!r} with {lesson[tutor]} in {lesson[room]}.".format(
                    _break["name"],
                    discord.utils.format_dt(_break["start"], "d"),
                    discord.utils.format_dt(_break["end"], "d"),
                    discord.utils.format_dt(_break["end"], "R"),
                    lesson=next_lesson,
                )
            )
        else:
            lesson = self.current_lesson(date)
            if not lesson:
                next_lesson = self.next_lesson(date)
                if next_lesson is None:
                    try:
                        next_lesson = self.absolute_next_lesson(date + timedelta(days=1))
                    except RuntimeError:
                        self.log.critical("Failed to fetch absolute next lesson. Is this the end?")
                        return
                    next_lesson.setdefault("name", "unknown")
                    next_lesson.setdefault("tutor", "unknown")
                    next_lesson.setdefault("room", "unknown")
                    next_lesson.setdefault("start_datetime", discord.utils.utcnow())
                    text = (
                        "[tt] No more lessons today!\n"
                        f"[tt] Next Lesson: {next_lesson['name']!r} with {next_lesson['tutor']} in "
                        f"{next_lesson['room']} - "
                        f"Starts {discord.utils.format_dt(next_lesson['start_datetime'], 'R')}"
                    )

                else:
                    next_lesson.setdefault("name", "unknown")
                    next_lesson.setdefault("tutor", "unknown")
                    next_lesson.setdefault("room", "unknown")
                    next_lesson.setdefault("start_datetime", discord.utils.utcnow())
                    text = "[tt] Next Lesson: {0[name]!r} with {0[tutor]} in {0[room]} - Starts {1}".format(
                        next_lesson, discord.utils.format_dt(next_lesson["start_datetime"], "R")
                    )
            else:
                lesson.setdefault("name", "unknown")
                lesson.setdefault("tutor", "unknown")
                lesson.setdefault("room", "unknown")
                lesson.setdefault("start_datetime", discord.utils.utcnow())
                if lesson["name"].lower() != "lunch":
                    text = "[tt] Current Lesson: {0[name]!r} with {0[tutor]} in {0[room]} - ends {1}".format(
                        lesson, discord.utils.format_dt(lesson["end_datetime"], "R")
                    )
                else:
                    text = "[tt] \U0001f37d\U0000fe0f Lunch! {0}-{1}, ends in {2}".format(
                        discord.utils.format_dt(lesson["start_datetime"], "t"),
                        discord.utils.format_dt(lesson["end_datetime"], "t"),
                        discord.utils.format_dt(lesson["end_datetime"], "R"),
                    )
                next_lesson = self.next_lesson(date)
                if next_lesson:
                    next_lesson.setdefault("name", "unknown")
                    next_lesson.setdefault("tutor", "unknown")
                    next_lesson.setdefault("room", "unknown")
                    next_lesson.setdefault("start_datetime", discord.utils.utcnow())
                    if lesson["name"].lower() != "lunch":
                        text += "\n[tt] Next lesson: {0[name]!r} with {0[tutor]} in {0[room]} - starts {1}".format(
                            next_lesson, discord.utils.format_dt(next_lesson["start_datetime"], "R")
                        )
                    else:
                        text = "[tt] \U0001f37d\U0000fe0f Lunch! {0}-{1}.".format(
                            discord.utils.format_dt(lesson["start_datetime"], "t"),
                            discord.utils.format_dt(lesson["end_datetime"], "t"),
                        )

        if no_prefix:
            text = text.replace("[tt] ", "")
        await message.edit(content=text, allowed_mentions=discord.AllowedMentions.none())

    # noinspection DuplicatedCode
    # @tasks.loop(time=schedule_times())
    @tasks.loop(minutes=5)
    async def update_status(self):
        if config.dev:
            return
        # console.log("[Timetable Updater Task] Running!")
        if not self.bot.is_ready():
            # console.log("[Timetable Updater Task] Bot is not ready, waiting until ready.")
            await self.bot.wait_until_ready()
        guild: discord.Guild = self.bot.get_guild(994710566612500550)
        # console.log("[Timetable Updater Task] Fetched source server.")
        channel = discord.utils.get(guild.text_channels, name="timetable")
        channel = channel or discord.utils.get(guild.text_channels, name="general")
        if not channel:
            # console.log("[Timetable Updater Task] No channel to update in!!", file=sys.stderr)
            return
        channel: discord.TextChannel
        # console.log("[Timetable Updater Task] Updating in channel %r." % channel.name)

        async for _message in channel.history(limit=20, oldest_first=False):
            if _message.author == self.bot.user and _message.content.startswith("[tt]"):
                message = _message
                break
        else:
            # console.log(f"[TimeTable Updater Task] Sending new message in {channel.name!r}.")
            message = await channel.send("[tt] (loading)")

        message: discord.Message
        # console.log(f"[TimeTable Updater Task] Updating message: {channel.id}/{message.id}")
        await self.update_timetable_message(message)
        # console.log("[Timetable Updater Task] Done! (exit result %r)" % r)

    @commands.slash_command()
    async def lesson(self, ctx: discord.ApplicationContext, *, date: str = None):
        """Shows the current/next lesson."""
        if date:
            try:
                date = datetime.strptime(date, "%d/%m/%y %H:%M")
            except ValueError:
                return await ctx.respond("Invalid date (DD/MM/YY HH:MM).")
        else:
            date = datetime.now()
        await ctx.defer()
        await self.update_timetable_message(ctx, date, no_prefix=True)
        if random.randint(1, 10) == 1:
            end_date = datetime(2024, 7, 13, 0, 0, 0, tzinfo=timezone.utc)
            days_left = (end_date - discord.utils.utcnow()).days
            await ctx.respond("There are only {:,} days left of this academic year.".format(days_left))

    @commands.slash_command(name="timetable")
    async def _timetable(self, ctx: discord.ApplicationContext, date: str = None):
        """Shows the timetable for today/the specified date"""
        if date:
            try:
                date = datetime.strptime(date, "%d/%m/%y")
            except ValueError:
                return await ctx.respond("Invalid date (DD/MM/YY).")
        else:
            date = datetime.now()

        text = self.format_timetable_message(date)
        view = TimeTableDaySwitcherView(ctx.user, self, date)
        view.update_buttons()
        await ctx.respond(text, view=view)

    @commands.slash_command(name="exams")
    async def _exams(self, ctx: discord.ApplicationContext):
        """Shows when exams are."""
        paper_1 = datetime(2023, 6, 14, 12, tzinfo=timezone.utc)
        paper_2 = datetime(2023, 6, 21, 12, tzinfo=timezone.utc)
        paper_1_url = "https://classroom.google.com/c/NTQ5MzE5ODg0ODQ2/m/NTUzNjI5NjAyMDQ2/details"
        paper_2_url = "https://classroom.google.com/c/NTQ5MzE5ODg0ODQ2/m/NjA1Nzk3ODQ4OTg0/details"
        await ctx.respond(
            f"Paper A: [{discord.utils.format_dt(paper_1, 'R')}]({paper_1_url})\n"
            f"Paper B: [{discord.utils.format_dt(paper_2, 'R')}]({paper_2_url})"
        )
        message_id = (await ctx.interaction.original_response()).id
        message = await ctx.channel.fetch_message(message_id)
        await message.edit(suppress=True)


def setup(bot):
    bot.add_cog(TimeTableCog(bot))
