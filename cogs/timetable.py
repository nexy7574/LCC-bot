import asyncio
from typing import Optional, Union

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

    def absolute_next_lesson(self, date: datetime = None) -> dict:
        # this function wastes so many CPU cycles.
        # Its also so computationally expensive that its async and cast to a thread to avoid blocking.
        # Why do I not just do this the smart way? I'm lazy and have a headache.
        lesson = None
        date = date or datetime.now()
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
        lesson = self.current_lesson(date)
        if not lesson:
            next_lesson = self.next_lesson(date)
            if not next_lesson:
                next_lesson = await asyncio.to_thread(
                    self.absolute_next_lesson
                )
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
                text += "\n[tt]Next lesson: {0[name]!r] with {0[tutor]} in {0[room]} - starts {1}".format(
                    next_lesson,
                    next_lesson["start_datetime"]
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
