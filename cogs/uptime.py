import asyncio
import datetime
from datetime import timedelta
from typing import Dict, Tuple

import discord
import httpx
from httpx import AsyncClient, Response
from discord.ext import commands, tasks
from utils import UptimeEntry, console


class UptimeCompetition(commands.Cog):
    USER_ID = 1063875884274163732
    OUR_ENDPOINT = "http://wg.nexy7574.cyou:9000/"
    SHRONK_SERVER = "http://shronk.nexy7574.cyou:9000/"
    OUR_DROPLET = "http://droplet.nexy7574.co.uk:9000/"
    TARGETS = {
        "SHRONK": USER_ID,
        "NEX_DROPLET": OUR_DROPLET,
        "PI": OUR_ENDPOINT,
        "SHRONK_DROPLET": SHRONK_SERVER
    }
    TARGETS_NAMES = {
        "SHRONK": "SHRoNK Bot",
        "NEX_DROPLET": "Nex's Droplet",
        "PI": "Nex's Pi",
        "SHRONK_DROPLET": "SHRoNK's Droplet"
    }

    class CancelTaskView(discord.ui.View):
        def __init__(self, task: asyncio.Task, expires: datetime.datetime):
            timeout = expires - discord.utils.utcnow()
            super().__init__(timeout=timeout.total_seconds() + 3, disable_on_timeout=True)
            self.task = task

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
        async def cancel(self, _, interaction: discord.Interaction):
            self.stop()
            if self.task:
                self.task.cancel()
            await interaction.response.edit_message(
                content="Uptime test cancelled.",
                view=None
            )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http = AsyncClient()
        self._warning_posted = False
        self.test_uptimes.start()
        self.last_result: list[UptimeEntry] = []
        self.task_lock = asyncio.Lock()
        self.task_event = asyncio.Event()

    def cog_unload(self):
        self.test_uptimes.cancel()

    @staticmethod
    def assert_uptime_server_response(response: Response):
        assert response.status_code == 200
        assert response.text.strip() == "<!DOCTYPE html><html><body>Hello Jimmy!</body></html>"

    async def _test_url(self, url: str, max_retries: int = 10, timeout: int = 30) -> Tuple[int, Response | Exception]:
        attempts = 1
        err = RuntimeError("Unknown Error")
        while attempts < max_retries:
            try:
                response = await self.http.get(url, timeout=timeout)
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.HTTPStatusError) as err:
                attempts += 1
                continue
            else:
                return attempts, response
        return attempts, err

    async def do_test_uptimes(self):
        console.log("Testing uptimes...")
        # First we need to check that we are online.
        # If we aren't online, this isn't very fair.
        try:
            await self.http.get("https://google.co.uk/")
        except httpx.ConnectError:
            return  # Offline :pensive:

        create_tasks = []
        # We need to collect the tasks in case the sqlite server is being sluggish

        for key, url in self.TARGETS.items():
            if key == "SHRONK":
                continue
            kwargs: Dict[str, str | int | None] = {
                "target_id": key,
                "target": url,
                "notes": ""
            }
            attempts, response = await self._test_url(url)
            if isinstance(response, Exception):
                kwargs["is_up"] = False
                kwargs["response_time"] = None
                kwargs["notes"] += f"Failed to access page after {attempts:,} attempts: {response}"
            else:
                if attempts > 1:
                    kwargs["notes"] += f"After {attempts:,} attempts, "
                try:
                    self.assert_uptime_server_response(response)
                except AssertionError as e:
                    kwargs["is_up"] = False
                    kwargs["notes"] += "content was invalid: " + str(e)
                else:
                    kwargs["is_up"] = True
                    kwargs["response_time"] = round(response.elapsed.total_seconds() * 1000)
                    kwargs["notes"] += "nothing notable."
            create_tasks.append(
                self.bot.loop.create_task(
                    UptimeEntry.objects.create(
                        **kwargs
                    )
                )
            )

        # We need to check if the shronk bot is online since matthew
        # Won't let us access their server (cough cough)
        if self.bot.intents.presences is True:
            # If we don't have presences this is useless.
            if not self.bot.is_ready():
                await self.bot.wait_until_ready()

            guild: discord.Guild = self.bot.get_guild(994710566612500550)
            if guild is None:
                console.log(
                    "[yellow]:warning: Unable to locate the LCC server! Can't uptime check shronk."
                )
            else:
                shronk_bot: discord.Member | None = discord.utils.get(guild.members, name="SHRoNK Bot")
                if not shronk_bot:
                    # SHRoNK Bot is not in members cache.
                    shronk_bots = await guild.query_members(query="SHRoNK Bot", limit=1)
                    if not shronk_bots:
                        console.log(
                            "[yellow]:warning: Unable to locate SHRoNK Bot! Can't uptime check shronk."
                        )
                    else:
                        shronk_bot = shronk_bots[0]
                if shronk_bot:
                    create_tasks.append(
                        self.bot.loop.create_task(
                            UptimeEntry.objects.create(
                                target_id="SHRONK",
                                target="SHRoNK Bot",
                                is_up=shronk_bot.status is not discord.Status.offline,
                                response_time=None,
                                notes="*Unable to monitor response time, not a HTTP request.*",
                            )
                        )
                    )
        else:
            if self._warning_posted is False:
                console.log(
                    "[yellow]:warning: Jimmy does not have the presences intent enabled. Uptime monitoring of the"
                    " shronk bot is disabled."
                )
                self._warning_posted = True

        # Now we have to collect the tasks
        return await asyncio.gather(*create_tasks, return_exceptions=True)
        # All done!

    @tasks.loop(minutes=1)
    async def test_uptimes(self):
        self.task_event.clear()
        async with self.task_lock:
            self.last_result = await self.do_test_uptimes()
        self.task_event.set()

    uptime = discord.SlashCommandGroup(
        "uptime",
        "Commands for the uptime competition."
    )

    @uptime.command(name="stats")
    async def stats(
            self,
            ctx: discord.ApplicationContext,
            query_target: discord.Option(
                str,
                name="target",
                description="The target to check the uptime of. Defaults to all.",
                required=False,
                choices=[
                    discord.OptionChoice("SHRoNK Bot", "SHRONK"),
                    discord.OptionChoice("Nex Droplet", "NEX_DROPLET"),
                    discord.OptionChoice("Shronk Server", "SHRONK_DROPLET"),
                    discord.OptionChoice("Nex Pi", "PI"),
                    discord.OptionChoice("ALL", "ALL"),
                ],
                default="ALL"
            ),
            look_back: discord.Option(
                int,
                description="How many days to look back. Defaults to a year.",
                required=False,
                default=365
            )
    ):
        """View collected uptime stats."""
        def generate_embed(target, specific_entries: list[UptimeEntry]):
            embed = discord.Embed(
                title=f"Uptime stats for {self.TARGETS_NAMES[target]}",
                description=f"Showing uptime stats for the last {look_back:,} days.",
                color=discord.Color.blurple()
            )
            first_check = datetime.datetime.fromtimestamp(
                specific_entries[-1].timestamp,
                datetime.timezone.utc
            )
            last_offline = last_online = None
            online_count = offline_count = 0
            for entry in reversed(specific_entries):
                if entry.is_up is False:
                    last_offline = datetime.datetime.fromtimestamp(
                        entry.timestamp,
                        datetime.timezone.utc
                    )
                    offline_count += 1
                else:
                    last_online = datetime.datetime.fromtimestamp(
                        entry.timestamp,
                        datetime.timezone.utc
                    )
                    online_count += 1
            total_count = online_count + offline_count
            online_avg = (online_count / total_count) * 100
            average_response_time = (
                    sum(entry.response_time for entry in entries if entry.response_time is not None) / total_count
            )
            embed.add_field(
                name="\u200b",
                value=f"*Started monitoring {discord.utils.format_dt(first_check, style='R')}, "
                      f"{total_count:,} monitoring events collected*\n"
                      f"**Online:**\n\t\\* {online_avg:.2f}% of the time\n\t\\* Last online: "
                      f"{discord.utils.format_dt(last_online, 'R') if last_online else 'Never'}\n"
                      f"\n"
                      f"**Offline:**\n\t\\* {100 - online_avg:.2f}% of the time\n\t\\* Last offline: "
                      f"{discord.utils.format_dt(last_offline, 'R') if last_offline else 'Never'}\n"
                      f"\n"
                      f"**Average Response Time:**\n\t\\* {average_response_time:.2f}ms",
            )
            return embed

        await ctx.defer()
        now = discord.utils.utcnow()
        look_back_timestamp = (now - timedelta(days=look_back)).timestamp()
        targets = [query_target]
        if query_target == "ALL":
            targets = ["SHRONK", "NEX_DROPLET", "SHRONK_DROPLET", "PI"]
        embeds = []
        for _target in targets:
            query = UptimeEntry.objects.filter(UptimeEntry.columns.timestamp >= look_back_timestamp).filter(target_id=_target)
            query = query.order_by("-timestamp")
            entries = await query.all()
            if not entries:
                embeds.append(
                    discord.Embed(
                        description=f"No uptime entries found for {_target}."
                    )
                )
            else:
                embeds.append(generate_embed(_target, entries))
        await ctx.respond(embeds=embeds)

    @uptime.command(name="view-next-run")
    async def view_next_run(
            self,
            ctx: discord.ApplicationContext
    ):
        """View when the next uptime test will run."""
        await ctx.defer()
        next_run = self.test_uptimes.next_iteration
        if next_run is None:
            return await ctx.respond("The uptime test is not running!")
        else:
            _wait = self.bot.loop.create_task(discord.utils.sleep_until(next_run))
            view = self.CancelTaskView(_wait, next_run)
            await ctx.respond(
                f"The next uptime test will run in {discord.utils.format_dt(next_run, 'R')}. Waiting...",
                view=view
            )
            await _wait
            if not self.task_event.is_set():
                await ctx.edit(content="Uptime test running! Waiting for results...", view=None)
                await self.task_event.wait()
            embeds = []
            for result in self.last_result:
                if isinstance(result, Exception):
                    embed = discord.Embed(
                        title="Error",
                        description=f"An error occurred while running an uptime test: {result}",
                        color=discord.Color.red()
                    )
                else:
                    result = await UptimeEntry.objects.get(entry_id=result.entry_id)
                    embed = discord.Embed(
                        title="Uptime for: " + self.TARGETS_NAMES[result.target_id],
                        description="Is up: {0.is_up!s}\n"
                                    "Response time: {1:,.2f}ms\n"
                                    "Notes: {0.notes!s}".format(result, result.response_time or -1),
                        color=discord.Color.green()
                    )
                embeds.append(embed)
            await ctx.edit(content="Uptime test complete! Results are in.", embeds=embeds, view=None)


def setup(bot):
    bot.add_cog(UptimeCompetition(bot))
