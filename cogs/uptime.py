import asyncio
import datetime
import json
from datetime import timedelta
from pathlib import Path
from typing import Dict, Tuple, List, Optional
from urllib.parse import urlparse, parse_qs, ParseResult

import discord
import httpx
from httpx import AsyncClient, Response
from discord.ext import commands, tasks, pages
from utils import UptimeEntry, console

"""
Notice to anyone looking at this code:
Don't
It doesn't look nice
It's not well written
It just works
"""


BASE_JSON = """[
    {
        "name": "SHRoNK Bot",
        "id": "SHRONK",
        "uri": "user://994710566612500550/1063875884274163732?online=1&idle=0&dnd=0"
    },
    {
        "name": "Nex's Droplet",
        "id": "NEX_DROPLET",
        "uri": "http://droplet.nexy7574.co.uk:9000/"
    },
    {
        "name": "Nex's Pi",
        "id": "PI",
        "uri": "http://wg.nexy7574.co.uk:9000/"
    },
    {
        "name": "SHRoNK Droplet",
        "id": "SHRONK_DROPLET",
        "uri": "http://shronkservz.tk:9000/"
    }
]
"""


class UptimeCompetition(commands.Cog):
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
            await interaction.response.edit_message(content="Uptime test cancelled.", view=None)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http = AsyncClient()
        self._warning_posted = False
        self.test_uptimes.start()
        self.last_result: list[UptimeEntry] = []
        self.task_lock = asyncio.Lock()
        self.task_event = asyncio.Event()
        if not (pth := Path("targets.json")).exists():
            pth.write_text(BASE_JSON)
        self._cached_targets = self.read_targets()

    @property
    def cached_targets(self) -> List[Dict[str, str]]:
        return self._cached_targets.copy()

    def get_target(self, name: str = None, target_id: str = None) -> Optional[Dict[str, str]]:
        for target in self.cached_targets:
            if name and target["name"].lower() == name.lower():
                return target
            if target["id"] == target_id:
                return target
        return

    def stats_autocomplete(self, ctx: discord.ApplicationContext) -> List[str]:
        return [x["name"] for x in self.cached_targets if ctx.value.lower() in x["name"].lower()] + ["ALL"]

    @staticmethod
    def parse_user_uri(uri: str) -> Dict[str, str | None | List[str]]:
        parsed = urlparse(uri)
        response = {"guild": parsed.hostname, "user": None, "statuses": []}
        if parsed.path:
            response["user"] = parsed.path.strip("/")
        if parsed.query:
            response["statuses"] = [x for x, y in parse_qs(parsed.query).items() if y[0] == "1"]
        return response

    def read_targets(self) -> List[Dict[str, str]]:
        with open("targets.json") as f:
            data: list = json.load(f)
            data.sort(key=lambda x: x["name"])
            self._cached_targets = data.copy()
        return data

    def write_targets(self, data: List[Dict[str, str]]):
        self._cached_targets = data
        with open("targets.json", "w") as f:
            json.dump(data, f, indent=4, default=str)

    def cog_unload(self):
        self.test_uptimes.cancel()

    @staticmethod
    def assert_uptime_server_response(response: Response):
        assert response.status_code == 200
        assert response.text.strip() == "<!DOCTYPE html><html><body>Hello Jimmy!</body></html>"

    async def _test_url(
        self, url: str, *, max_retries: int | None = 10, timeout: int | None = 30
    ) -> Tuple[int, Response | Exception]:
        attempts = 1
        if max_retries is None:
            max_retries = 1
        if timeout is None:
            timeout = 10
        err = RuntimeError("Unknown Error")
        while attempts < max_retries:
            try:
                response = await self.http.get(url, timeout=timeout)
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.HTTPStatusError, ConnectionError, TimeoutError) as err:
                attempts += 1
                continue
            else:
                return attempts, response
        return attempts, err

    async def do_test_uptimes(self):
        targets = self.cached_targets
        # First we need to check that we are online.
        # If we aren't online, this isn't very fair.
        try:
            await self.http.get("https://google.co.uk/")
        except (httpx.HTTPError, Exception):
            return  # Offline :pensive:

        create_tasks = []
        # We need to collect the tasks in case the sqlite server is being sluggish
        for target in targets.copy():
            if not target["uri"].startswith("http"):
                continue
            targets.remove(target)
            request_kwargs = {}
            if timeout := target.get("timeout"):
                request_kwargs["timeout"] = timeout
            if max_retries := target.get("max_retries"):
                request_kwargs["max_retries"] = max_retries
            kwargs: Dict[str, str | int | None] = {"target_id": target["id"], "target": target["uri"], "notes": ""}
            attempts, response = await self._test_url(target["uri"], **request_kwargs)
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
            create_tasks.append(self.bot.loop.create_task(UptimeEntry.objects.create(**kwargs)))

        # We need to check if the shronk bot is online since matthew
        # Won't let us access their server (cough cough)
        if self.bot.intents.presences is True:
            # If we don't have presences this is useless.
            if not self.bot.is_ready():
                await self.bot.wait_until_ready()
            for target in targets:
                parsed = urlparse(target["uri"])
                if parsed.scheme != "user":
                    continue
                guild_id = int(parsed.hostname)
                user_id = int(parsed.path.strip("/"))
                okay_statuses = [
                    discord.Status.online if "online=1" in parsed.query.lower() else None,
                    discord.Status.idle if "idle=1" in parsed.query.lower() else None,
                    discord.Status.dnd if "dnd=1" in parsed.query.lower() else None,
                ]
                if "offline=1" in parsed.query:
                    okay_statuses = [discord.Status.offline]
                okay_statuses = list(filter(None, okay_statuses))
                guild: discord.Guild = self.bot.get_guild(guild_id)
                if guild is None:
                    console.log(
                        f"[yellow]:warning: Unable to locate the guild for {target['name']!r}! Can't uptime check."
                    )
                else:
                    user: discord.Member | None = guild.get_member(user_id)
                    if not user:
                        # SHRoNK Bot is not in members cache.
                        try:
                            user = await guild.fetch_member(user_id)
                        except discord.HTTPException:
                            console.log(f"[yellow]:warning: Unable to locate {target['name']!r}! Can't uptime check.")
                            user = None
                    if user:
                        create_tasks.append(
                            self.bot.loop.create_task(
                                UptimeEntry.objects.create(
                                    target_id=target["id"],
                                    target=target["name"],
                                    is_up=user.status in okay_statuses,
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
    # @tasks.loop(seconds=30)
    async def test_uptimes(self):
        self.task_event.clear()
        async with self.task_lock:
            self.last_result = await self.do_test_uptimes()
        self.task_event.set()

    uptime = discord.SlashCommandGroup("uptime", "Commands for the uptime competition.")

    @uptime.command(name="stats")
    async def stats(
        self,
        ctx: discord.ApplicationContext,
        query_target: discord.Option(
            str,
            name="target",
            description="The target to check the uptime of. Defaults to all.",
            required=False,
            autocomplete=stats_autocomplete,
            default="ALL",
        ),
        look_back: discord.Option(
            int, description="How many days to look back. Defaults to a year.", required=False, default=365
        ),
    ):
        """View collected uptime stats."""
        org_target = query_target

        def generate_embed(target, specific_entries: list[UptimeEntry]):
            targ = target
            # targ = self.get_target(target_id=target)
            embed = discord.Embed(
                title=f"Uptime stats for {targ['name']}",
                description=f"Showing uptime stats for the last {look_back:,} days.",
                color=discord.Color.blurple(),
            )
            first_check = datetime.datetime.fromtimestamp(specific_entries[-1].timestamp, datetime.timezone.utc)
            last_offline = last_online = None
            online_count = offline_count = 0
            for entry in reversed(specific_entries):
                if entry.is_up is False:
                    last_offline = datetime.datetime.fromtimestamp(entry.timestamp, datetime.timezone.utc)
                    offline_count += 1
                else:
                    last_online = datetime.datetime.fromtimestamp(entry.timestamp, datetime.timezone.utc)
                    online_count += 1
            total_count = online_count + offline_count
            online_avg = (online_count / total_count) * 100
            average_response_time = (
                sum(entry.response_time for entry in entries if entry.response_time is not None) / total_count
            )
            if org_target != "ALL":
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
            else:
                embed.title = None
                embed.description = f"{targ['name']}: {online_avg:.2f}% uptime, last offline: "
                if last_offline:
                    embed.description += f"{discord.utils.format_dt(last_offline, 'R')}"
            return embed

        await ctx.defer()
        now = discord.utils.utcnow()
        look_back_timestamp = (now - timedelta(days=look_back)).timestamp()
        targets = [query_target]
        if query_target == "ALL":
            targets = [t["id"] for t in self.cached_targets]
        embeds = []
        for _target in targets:
            _target = self.get_target(_target, _target)
            query = UptimeEntry.objects.filter(UptimeEntry.columns.timestamp >= look_back_timestamp).filter(
                target_id=_target["id"]
            )
            query = query.order_by("-timestamp")
            entries = await query.all()
            if not entries:
                embeds.append(discord.Embed(description=f"No uptime entries found for {_target}."))
            else:
                embeds.append(generate_embed(_target, entries))

        if org_target == "ALL":
            new_embed = discord.Embed(
                title="Uptime stats for all monitored targets:",
                description=f"Showing uptime stats for the last {look_back:,} days.\n\n",
                color=discord.Color.blurple(),
            )
            for embed_ in embeds:
                new_embed.description += f"{embed_.description}\n"
            embeds = [new_embed]
        await ctx.respond(embeds=embeds)

    @uptime.command(name="view-next-run")
    async def view_next_run(self, ctx: discord.ApplicationContext):
        """View when the next uptime test will run."""
        await ctx.defer()
        next_run = self.test_uptimes.next_iteration
        if next_run is None:
            return await ctx.respond("The uptime test is not running!")
        else:
            _wait = self.bot.loop.create_task(discord.utils.sleep_until(next_run))
            view = self.CancelTaskView(_wait, next_run)
            await ctx.respond(
                f"The next uptime test will run in {discord.utils.format_dt(next_run, 'R')}. Waiting...", view=view
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
                        color=discord.Color.red(),
                    )
                else:
                    result = await UptimeEntry.objects.get(entry_id=result.entry_id)
                    target = self.get_target(target_id=result.target_id) or {"name": result.target_id}
                    embed = discord.Embed(
                        title="Uptime for: " + target["name"],
                        description="Is up: {0.is_up!s}\n"
                        "Response time: {1:,.2f}ms\n"
                        "Notes: {0.notes!s}".format(result, result.response_time or -1),
                        color=discord.Color.green(),
                    )
                embeds.append(embed)
            if len(embeds) >= 3:
                paginator = pages.Paginator(embeds, loop_pages=True)
                await ctx.delete(delay=0.1)
                await paginator.respond(ctx.interaction)
            else:
                await ctx.edit(content="Uptime test complete! Results are in:", embeds=embeds, view=None)

    monitors = uptime.create_subgroup(name="monitors", description="Manage uptime monitors.")

    @monitors.command(name="add")
    @commands.is_owner()
    async def add_monitor(
        self,
        ctx: discord.ApplicationContext,
        name: discord.Option(str, description="The name of the monitor."),
        uri: discord.Option(
            str,
            description="The URI to monitor. Enter HELP for more info.",
        ),
        http_max_retries: discord.Option(
            int,
            description="The maximum number of HTTP retries to make if the request fails.",
            required=False,
            default=None,
        ),
        http_timeout: discord.Option(
            int, description="The timeout for the HTTP request.", required=False, default=None
        ),
    ):
        """Creates a monitor to... monitor"""
        await ctx.defer()
        name: str
        uri: str
        http_max_retries: Optional[int]
        http_timeout: Optional[int]

        uri: ParseResult = urlparse(uri.lower())
        if uri.scheme == "" and uri.path.lower() == "help":
            return await ctx.respond(
                "URI can be either `HTTP(S)`, or the custom `USER` scheme.\n"
                "Examples:\n"
                "`http://example.com` - HTTP GET request to example.com\n"
                "`https://example.com` - HTTPS GET request to example.com\n\n"
                f"`user://{ctx.guild.id}/{ctx.user.id}?dnd=1` - Checks if user `{ctx.user.id}` (you) is"
                f" in `dnd` (the red status) in the server `{ctx.guild.id}` (this server)."
                f"\nQuery options are: `dnd`, `idle`, `online`, `offline`. `1` means the status is classed as "
                f"'online' - anything else means offline.\n"
                f"The format is `user://{{server_id}}/{{user_id}}?{{status}}={{1|0}}`\n"
                f"Setting `server_id` to `$GUILD` will auto-fill in the current server ID."
            )

        options = {
            "name": name,
            "id": name.upper().strip().replace(" ", "_"),
        }

        if uri.scheme == "user":
            uri: ParseResult = uri._replace(netloc=uri.hostname.replace("$guild", str(ctx.guild.id)))
            data = self.parse_user_uri(uri.geturl())
            if data["guild"] is None or data["guild"].isdigit() is False:
                return await ctx.respond("Invalid guild ID in URI.")
            if data["user"] is None or data["user"].isdigit() is False:
                return await ctx.respond("Invalid user ID in URI.")
            if not data["statuses"] or any(
                x.lower() not in ("dnd", "idle", "online", "offline") for x in data["statuses"]
            ):
                return await ctx.respond("Invalid status query string in URI. Did you forget to supply one?")

            guild = self.bot.get_guild(int(data["guild"]))
            if guild is None:
                return await ctx.respond("Invalid guild ID in URI (not found).")

            try:
                guild.get_member(int(data["user"])) or await guild.fetch_member(int(data["user"]))
            except discord.HTTPException:
                return await ctx.respond("Invalid user ID in URI (not found).")

            options["uri"] = uri.geturl()

        elif uri.scheme in ["http", "https"]:
            attempts, response = await self._test_url(uri.geturl(), max_retries=http_max_retries, timeout=http_timeout)
            if response is None:
                return await ctx.respond(
                    f"Failed to connect to {uri.geturl()!r} after {attempts} attempts. Please ensure the target page"
                    f" is up, and try again."
                )

            options["uri"] = uri.geturl()
            options["http_max_retries"] = http_max_retries
            options["http_timeout"] = http_timeout
        else:
            return await ctx.respond("Invalid URI scheme. Supported: HTTP[S], USER.")

        targets = self.cached_targets
        for target in targets:
            if target["uri"] == options["uri"] or target["name"] == options["name"]:
                return await ctx.respond("This monitor already exists.")

        targets.append(options)
        self.write_targets(targets)
        await ctx.respond("Monitor added!")

    @monitors.command(name="remove")
    @commands.is_owner()
    async def remove_monitor(
        self,
        ctx: discord.ApplicationContext,
        name: discord.Option(str, description="The name of the monitor."),
    ):
        """Removes a monitor."""
        await ctx.defer()
        name: str

        targets = self.cached_targets
        for target in targets:
            if target["name"] == name or target["id"] == name:
                targets.remove(target)
                self.write_targets(targets)
                return await ctx.respond("Monitor removed.")
        await ctx.respond("Monitor not found.")


def setup(bot):
    bot.add_cog(UptimeCompetition(bot))
