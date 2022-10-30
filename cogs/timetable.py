import asyncio
from typing import Optional, Union, Dict

import discord
from discord.ext import commands, tasks
import json
from pathlib import Path
from utils import console
from datetime import time, datetime, timedelta


def schedule_times():
    times = []
    for h in range(24):
        for m in range(0, 60, 15):
            times.append(time(h, m, 0))
    return times


class TimeTableCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        with (Path.cwd() / "utils" / "timetable.json").open() as file:
            self.timetable = json.load(file)
        self.update_status.start()

    def are_on_break(self, date: datetime = None) -> Optional[Dict[str, Union[str, datetime]]]:
        """Checks if the date is one as a term break"""
        date = date or datetime.now()
        # That description made no sense what
        for name, dates in self.timetable["breaks"].items():
            start_date = datetime.strptime(dates["start"], "%d/%m/%Y")
            end_date = datetime.strptime(dates["end"], "%d/%m/%Y")
            if date.timestamp() <= end_date.timestamp() and date.timestamp() >= start_date.timestamp():
                return {
                    "name": name,
                    "start": start_date,
                    "end": end_date
                }

    def cog_unload(self):
        self.update_status.stop()

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

    def absolute_next_lesson(self, date: datetime = None, *, new_method: bool = False) -> dict:
        lesson = None
        date = date or datetime.now()
        print("[absolute next lesson] Date:", date)
        if new_method is True:
            print("[absolute next lesson] Using new method.")
            # Check if there's another lesson today
            lesson = self.next_lesson(date)
            print("[absolute next lesson] Next lesson today:", lesson)
            # If there's another lesson, great, return that
            # Otherwise, we need to start looking ahead.
            if lesson is None:
                print("[absolute next lesson] Next lesson was None")
                # Loop until we find the next day when it isn't the weekend, and we aren't on break.
                next_available_date = date.replace(hour=0, minute=0, second=0)
                print("[absolute next lesson] next available date: ", next_available_date)
                while self.are_on_break(next_available_date) or not self.timetable.get(date.strftime("%A").lower()):
                    print("[absolute next lesson] Next available date is on break? ",
                          bool(self.are_on_break(next_available_date)))
                    print("[absolute next lesson] Is timetabled date?", self.timetable.get(date.strftime("%A").lower()))
                    next_available_date += timedelta(days=1)
                    if next_available_date.year >= 2024:
                        raise RuntimeError("Failed to fetch absolute next lesson")
                    print("[absolute next lesson] next available date: ", next_available_date)
                    # NOTE: This could be *even* more efficient but honestly as long as it works it's fine
                print("[absolute next lesson] fetching next lesson on", next_available_date)
                lesson = self.next_lesson(next_available_date)  # This *must* be a date given the second part of the
                # while loop's `or` statement.
                print("[absolute next lesson] next lesson on %s: %s" % (next_available_date, lesson))
                assert lesson, "Unable to figure out the next lesson."
        else:
            # this function wastes so many CPU cycles.
            # Its also so computationally expensive that its async and cast to a thread to avoid blocking.
            # Why do I not just do this the smart way? I'm lazy and have a headache.
            while lesson is None:
                lesson = self.next_lesson(date)
                if lesson is None:
                    date += timedelta(minutes=5)
                else:
                    break
        return lesson

    async def update_timetable_message(
            self,
            message: Union[discord.Message, discord.ApplicationContext],
            date: datetime = None,
            *,
            no_prefix: bool = False,
    ):
        date = date or datetime.now()
        _break = self.are_on_break(date)
        if _break:
            next_lesson = self.next_lesson(_break["end"] + timedelta(days=1, hours=7))
            next_lesson = next_lesson or {
                "name": "Unknown",
                "tutor": "Unknown",
                "room": "Unknown"
            }
            text = "[tt] On break {!r} from {} until {}. Break ends {}, and the first lesson back is " \
                   "{lesson[name]!r} with {lesson[tutor]} in {lesson[room]}.".format(
                _break["name"],
                discord.utils.format_dt(_break["start"], "d"),
                discord.utils.format_dt(_break["end"], "d"),
                discord.utils.format_dt(_break["end"], "R"),
                lesson=next_lesson
            )
        else:
            lesson = self.current_lesson(date)
            if not lesson:
                next_lesson = self.next_lesson(date)
                if not next_lesson:
                    next_lesson = await asyncio.to_thread(
                        self.absolute_next_lesson,
                        new_method=True
                    )
                    next_lesson = next_lesson or {
                        "name": "unknown",
                        "tutor": "unknown",
                        "room": "unknown",
                        "start_datetime": datetime.max
                    }
                    text = "[tt] No more lessons today!\n" \
                           f"[tt] Next Lesson: {next_lesson['name']!r} with {next_lesson['tutor']} in " \
                           f"{next_lesson['room']} - " \
                           f"Starts {discord.utils.format_dt(next_lesson['start_datetime'], 'R')}"

                else:
                    text = f"[tt] Next Lesson: {next_lesson['name']!r} with {next_lesson['tutor']} in " \
                           f"{next_lesson['room']} - Starts {discord.utils.format_dt(next_lesson['start_datetime'], 'R')}"
            else:
                text = f"[tt] Current Lesson: {lesson['name']!r} with {lesson['tutor']} in {lesson['room']} - " \
                       f"ends {discord.utils.format_dt(lesson['end_datetime'], 'R')}"
                next_lesson = self.next_lesson(date)
                if next_lesson:
                    text += "\n[tt] Next lesson: {0[name]!r} with {0[tutor]} in {0[room]} - starts {1}".format(
                        next_lesson,
                        discord.utils.format_dt(next_lesson["start_datetime"], 'R')
                    )

        if no_prefix:
            text = text.replace("[tt] ", "")
        await message.edit(content=text, allowed_mentions=discord.AllowedMentions.none())

    # noinspection DuplicatedCode
    @tasks.loop(time=schedule_times())
    async def update_status(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()
        guild: discord.Guild = self.bot.get_guild(994710566612500550)
        channel = discord.utils.get(guild.text_channels, name="timetable")
        channel = channel or discord.utils.get(guild.text_channels, name="general")
        channel: discord.TextChannel

        async for _message in channel.history(limit=20, oldest_first=False):
            if _message.author == self.bot.user and _message.content.startswith("[tt]"):
                message = _message
                break
        else:
            message = await channel.send("[tt] (loading)")

        message: discord.Message
        await self.update_timetable_message(message)

    @commands.slash_command()
    async def lesson(self, ctx: discord.ApplicationContext, *, date: str = None):
        """Shows the current/next lesson."""
        if date:
            try:
                date = datetime.strptime(date, "%d/%m/%Y %H:%M")
            except ValueError:
                return await ctx.respond("Invalid date (DD/MM/YYYY HH:MM).")
        else:
            date = datetime.now()
        await ctx.defer()
        await self.update_timetable_message(ctx, date, no_prefix=True)

    @commands.slash_command(name="timetable")
    async def _timetable(self, ctx: discord.ApplicationContext, date: str = None):
        """Shows the timetable for today/the specified date"""
        if date:
            try:
                date = datetime.strptime(date, "%d/%m/%Y")
            except ValueError:
                return await ctx.respond("Invalid date (DD/MM/YYYY).")
        else:
            date = datetime.now()

        lessons = self.timetable.get(date.strftime("%A").lower(), [])
        if not lessons:
            return await ctx.respond(f"No lessons on {discord.utils.format_dt(date, 'D')}.")

        blocks = [f"```\nTimetable for {date.strftime('%A')}:\n```"]
        for lesson in lessons:
            start_datetime = date.replace(hour=lesson["start"][0], minute=lesson["start"][1])
            end_datetime = date.replace(hour=lesson["end"][0], minute=lesson["end"][1])
            text = f"{discord.utils.format_dt(start_datetime, 't')} to {discord.utils.format_dt(end_datetime, 't')}" \
                   f":\n> Lesson Name: {lesson['name']!r}\n" \
                   f"> Tutor: **{lesson['tutor']}**\n> Room: `{lesson['room']}`"
            blocks.append(text)
        await ctx.respond("\n\n".join(blocks))


def setup(bot):
    bot.add_cog(TimeTableCog(bot))
