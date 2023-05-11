import asyncio
import io
import json
import os
import subprocess
import random
import re
import tempfile
import textwrap
import gzip
from datetime import timedelta
from functools import partial
from io import BytesIO

import dns.resolver
import httpx
from dns import asyncresolver
import aiofiles
import pyttsx3
from time import time, time_ns, sleep
from typing import Literal
from typing import Tuple, Optional, Dict
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image
import pytesseract

import aiohttp
import discord
import psutil
from discord.ext import commands, pages
from rich.tree import Tree
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from utils import console, Timer

try:
    from config import proxy
except ImportError:
    proxy = None
try:
    from config import proxies
except ImportError:
    if proxy:
        proxies = [proxy] * 2
    else:
        proxies = []

_engine = pyttsx3.init()
# noinspection PyTypeChecker
VOICES = [x.id for x in _engine.getProperty("voices")]
del _engine


def format_autocomplete(ctx: discord.AutocompleteContext):
    url = ctx.options.get("url", os.urandom(6).hex())
    self: "OtherCog" = ctx.bot.cogs["OtherCog"]  # type: ignore
    if url in self._fmt_cache:
        suitable = []
        for _format_key in self._fmt_cache[url]:
            _format = self._fmt_cache[url][_format_key]
            _format_nice = _format["format"]
            if ctx.value.lower() in _format_nice.lower():
                suitable.append(_format_nice)
        return suitable

    try:
        parsed = urlparse(url, allow_fragments=True)
    except ValueError:
        pass
    else:
        if parsed.scheme in ("http", "https") and parsed.netloc:
            self._fmt_queue.put_nowait(url)
    return []


# noinspection DuplicatedCode
class OtherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.http = httpx.AsyncClient()
        self._fmt_cache = {}
        self._fmt_queue = asyncio.Queue()
        self._worker_task = self.bot.loop.create_task(self.cache_population_job())

    def cog_unload(self):
        self._worker_task.cancel()

    async def cache_population_job(self):
        while True:
            url = await self._fmt_queue.get()
            if url not in self._fmt_cache:
                await self.list_formats(url, use_proxy=1)
            self._fmt_queue.task_done()

    async def list_formats(self, url: str, *, use_proxy: int = 0) -> dict:
        if url in self._fmt_cache:
            return self._fmt_cache[url]

        import yt_dlp

        class NullLogger:
            def debug(self, *args, **kwargs):
                pass

            def info(self, *args, **kwargs):
                pass

            def warning(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

        with tempfile.TemporaryDirectory(prefix="jimmy-ytdl", suffix="-info") as tempdir:
            with yt_dlp.YoutubeDL(
                    {
                        "windowsfilenames": True,
                        "restrictfilenames": True,
                        "noplaylist": True,
                        "nocheckcertificate": True,
                        "no_color": True,
                        "noprogress": True,
                        "logger": NullLogger(),
                        "paths": {"home": tempdir, "temp": tempdir},
                    }
            ) as downloader:
                try:
                    info = await self.bot.loop.run_in_executor(
                        None,
                        partial(downloader.extract_info, url, download=False)
                    )
                except yt_dlp.utils.DownloadError:
                    return {}
                info = downloader.sanitize_info(info)
                new = {
                    fmt["format_id"]: {
                        "id": fmt["format_id"],
                        "ext": fmt["ext"],
                        "protocol": fmt["protocol"],
                        "acodec": fmt["acodec"],
                        "vcodec": fmt["vcodec"],
                        "resolution": fmt["resolution"],
                        "filesize": fmt.get("filesize", float('inf')),
                        "format": fmt["format"],
                    }
                    for fmt in info["formats"]
                }
        self._fmt_cache[url] = new
        return new

    class AbortScreenshotTask(discord.ui.View):
        def __init__(self, task: asyncio.Task):
            super().__init__()
            self.task = task

        @discord.ui.button(label="Abort", style=discord.ButtonStyle.red)
        async def abort(self, button: discord.ui.Button, interaction: discord.Interaction):
            new: discord.Interaction = await interaction.response.send_message("Aborting...", ephemeral=True)
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.disable_all_items()
            button.label = "[ aborted ]"
            await new.edit_original_response(content="Aborted screenshot task.", view=self)
            self.stop()

    async def screenshot_website(
        self,
        ctx: discord.ApplicationContext,
        website: str,
        driver: Literal["chrome", "firefox"],
        render_time: int = 10,
        load_timeout: int = 30,
        window_height: int = 1920,
        window_width: int = 1080,
        full_screenshot: bool = False,
    ) -> Tuple[discord.File, str, int, int]:
        async def _blocking(*args):
            return await self.bot.loop.run_in_executor(None, *args)

        def find_driver():
            nonlocal driver, driver_path
            drivers = {
                "firefox": [
                    "/usr/bin/firefox-esr",
                    "/usr/bin/firefox",
                ],
                "chrome": ["/usr/bin/chromium", "/usr/bin/chrome", "/usr/bin/chrome-browser", "/usr/bin/google-chrome"],
            }
            selected_driver = driver
            arr = drivers.pop(selected_driver)
            for binary in arr:
                b = Path(binary).resolve()
                if not b.exists():
                    continue
                driver = selected_driver
                driver_path = b
                break
            else:
                for key, value in drivers.items():
                    for binary in value:
                        b = Path(binary).resolve()
                        if not b.exists():
                            continue
                        driver = key
                        driver_path = b
                        break
                    else:
                        continue
                    break
                else:
                    raise RuntimeError("No browser binary.")
            return driver, driver_path

        driver, driver_path = find_driver()
        console.log(
            "Using driver '{}' with binary '{}' to screenshot '{}', as requested by {}.".format(
                driver, driver_path, website, ctx.user
            )
        )

        def _setup():
            nonlocal driver
            if driver == "chrome":
                options = ChromeOptions()
                options.add_argument("--headless")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                options.add_argument("--disable-extensions")
                options.add_argument("--incognito")
                options.binary_location = str(driver_path)
                service = ChromeService("/usr/bin/chromedriver")
                driver = webdriver.Chrome(service=service, options=options)
                driver.set_window_size(window_height, window_width)
            else:
                options = FirefoxOptions()
                options.add_argument("--headless")
                options.add_argument("--private-window")
                options.add_argument("--safe-mode")
                options.add_argument("--new-instance")
                options.binary_location = str(driver_path)
                service = FirefoxService("/usr/bin/geckodriver")
                driver = webdriver.Firefox(service=service, options=options)
                driver.set_window_size(window_height, window_width)
            return driver, textwrap.shorten(website, 100)

        # Is it overkill to cast this to a thread? yes
        # Do I give a flying fuck? kinda
        # Why am I doing this? I suspect setup is causing a ~10-second block of the event loop
        driver_name = driver
        start_init = time()
        driver, friendly_url = await asyncio.to_thread(_setup)
        end_init = time()
        console.log("Driver '{}' initialised in {} seconds.".format(driver_name, round(end_init - start_init, 2)))

        def _edit(content: str):
            self.bot.loop.create_task(ctx.interaction.edit_original_response(content=content))

        expires = round(time() + load_timeout)
        _edit(content=f"Screenshotting <{friendly_url}>... (49%, loading webpage, aborts <t:{expires}:R>)")
        await _blocking(driver.set_page_load_timeout, load_timeout)
        start = time()
        await _blocking(driver.get, website)
        end = time()
        get_time = round((end - start) * 1000)
        render_time_expires = round(time() + render_time)
        _edit(content=f"Screenshotting <{friendly_url}>... (66%, stopping render <t:{render_time_expires}:R>)")
        await asyncio.sleep(render_time)
        _edit(content=f"Screenshotting <{friendly_url}>... (83%, saving screenshot)")
        domain = re.sub(r"https?://", "", website)

        screenshot_method = driver.get_screenshot_as_png
        if full_screenshot and driver_name == "firefox":
            screenshot_method = driver.get_full_page_screenshot_as_png

        start = time()
        data = await _blocking(screenshot_method)
        _io = io.BytesIO()
        # Write the data async because HAHAHAHAHAHAHA
        # We'll do it in the existing event loop though because less overhead
        await _blocking(_io.write, data)
        _io.seek(0)
        end = time()
        screenshot_time = round((end - start) * 1000)
        driver.quit()
        return discord.File(_io, f"{domain}.png"), driver_name, get_time, screenshot_time

    @staticmethod
    async def get_interface_ip_addresses() -> Dict[str, list[Dict[str, str | bool | int]]]:
        addresses = await asyncio.to_thread(psutil.net_if_addrs)
        stats = await asyncio.to_thread(psutil.net_if_stats)
        result = {}
        for key in addresses.keys():
            result[key] = []
            for ip_addr in addresses[key]:
                if ip_addr.broadcast is None:
                    continue
                else:
                    result[key].append(
                        {
                            "ip": ip_addr.address,
                            "netmask": ip_addr.netmask,
                            "broadcast": ip_addr.broadcast,
                            "up": stats[key].isup,
                            "speed": stats[key].speed,
                        }
                    )
        return result

    async def analyse_text(self, text: str) -> Optional[Tuple[float, float, float, float]]:
        """Analyse text for positivity, negativity and neutrality."""

        def inner():
            try:
                from utils.sentiment_analysis import intensity_analyser
            except ImportError:
                return None
            scores = intensity_analyser.polarity_scores(text)
            return scores["pos"], scores["neu"], scores["neg"], scores["compound"]

        async with self.bot.training_lock:
            return await self.bot.loop.run_in_executor(None, inner)

    @staticmethod
    async def get_xkcd(session: aiohttp.ClientSession, n: int) -> dict | None:
        async with session.get("https://xkcd.com/{!s}/info.0.json".format(n)) as response:
            if response.status == 200:
                data = await response.json()
                return data

    @staticmethod
    async def random_xkcd_number(session: aiohttp.ClientSession) -> int:
        async with session.get("https://c.xkcd.com/random/comic") as response:
            if response.status != 302:
                number = random.randint(100, 999)
            else:
                number = int(response.headers["location"].split("/")[-2])
        return number

    @staticmethod
    async def random_xkcd(session: aiohttp.ClientSession) -> dict | None:
        """Fetches a random XKCD.

        Basically a shorthand for random_xkcd_number and get_xkcd.
        """
        number = await OtherCog.random_xkcd_number(session)
        return await OtherCog.get_xkcd(session, number)

    @staticmethod
    def get_xkcd_embed(data: dict) -> discord.Embed:
        embed = discord.Embed(
            title=data["safe_title"], description=data["alt"], color=discord.Colour.embed_background()
        )
        embed.set_footer(text="XKCD #{!s}".format(data["num"]))
        embed.set_image(url=data["img"])
        return embed

    @staticmethod
    async def generate_xkcd(n: int = None) -> discord.Embed:
        async with aiohttp.ClientSession() as session:
            if n is None:
                data = await OtherCog.random_xkcd(session)
                n = data["num"]
            else:
                data = await OtherCog.get_xkcd(session, n)
            if data is None:
                return discord.Embed(
                    title="Failed to load XKCD :(", description="Try again later.", color=discord.Colour.red()
                ).set_footer(text="Attempted to retrieve XKCD #{!s}".format(n))
            return OtherCog.get_xkcd_embed(data)

    class XKCDGalleryView(discord.ui.View):
        def __init__(self, n: int):
            super().__init__(timeout=300, disable_on_timeout=True)
            self.n = n

        def __rich_repr__(self):
            yield "n", self.n
            yield "message", self.message

        @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
        async def previous_comic(self, _, interaction: discord.Interaction):
            self.n -= 1
            await interaction.response.defer()
            await interaction.edit_original_response(embed=await OtherCog.generate_xkcd(self.n))

        @discord.ui.button(label="Random", style=discord.ButtonStyle.blurple)
        async def random_comic(self, _, interaction: discord.Interaction):
            await interaction.response.defer()
            await interaction.edit_original_response(embed=await OtherCog.generate_xkcd())
            self.n = random.randint(1, 999)

        @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
        async def next_comic(self, _, interaction: discord.Interaction):
            self.n += 1
            await interaction.response.defer()
            await interaction.edit_original_response(embed=await OtherCog.generate_xkcd(self.n))

    @commands.slash_command()
    async def xkcd(self, ctx: discord.ApplicationContext, *, number: int = None):
        """Shows an XKCD comic"""
        embed = await self.generate_xkcd(number)
        view = self.XKCDGalleryView(number)
        return await ctx.respond(embed=embed, view=view)

    @commands.slash_command()
    async def sentiment(self, ctx: discord.ApplicationContext, *, text: str):
        """Attempts to detect a text's tone"""
        await ctx.defer()
        if not text:
            return await ctx.respond("You need to provide some text to analyse.")
        result = await self.analyse_text(text)
        if result is None:
            return await ctx.edit(content="Failed to load sentiment analysis module.")
        embed = discord.Embed(title="Sentiment Analysis", color=discord.Colour.embed_background())
        embed.add_field(name="Positive", value="{:.2%}".format(result[0]))
        embed.add_field(name="Neutral", value="{:.2%}".format(result[2]))
        embed.add_field(name="Negative", value="{:.2%}".format(result[1]))
        embed.add_field(name="Compound", value="{:.2%}".format(result[3]))
        return await ctx.edit(content=None, embed=embed)

    @commands.message_command(name="Detect Sentiment")
    async def message_sentiment(self, ctx: discord.ApplicationContext, message: discord.Message):
        await ctx.defer()
        text = str(message.clean_content)
        if not text:
            return await ctx.respond("You need to provide some text to analyse.")
        await ctx.respond("Analyzing (this may take some time)...")
        result = await self.analyse_text(text)
        if result is None:
            return await ctx.edit(content="Failed to load sentiment analysis module.")
        embed = discord.Embed(title="Sentiment Analysis", color=discord.Colour.embed_background())
        embed.add_field(name="Positive", value="{:.2%}".format(result[0]))
        embed.add_field(name="Neutral", value="{:.2%}".format(result[2]))
        embed.add_field(name="Negative", value="{:.2%}".format(result[1]))
        embed.add_field(name="Compound", value="{:.2%}".format(result[3]))
        embed.url = message.jump_url
        return await ctx.edit(content=None, embed=embed)

    corrupt_file = discord.SlashCommandGroup(
        name="corrupt-file",
        description="Corrupts files.",
    )

    @corrupt_file.command(name="generate")
    async def generate_corrupt_file(self, ctx: discord.ApplicationContext, file_name: str, size_in_megabytes: float):
        """Generates a "corrupted" file."""
        limit_mb = round(ctx.guild.filesize_limit / 1024 / 1024)
        if size_in_megabytes > limit_mb:
            return await ctx.respond(
                f"File size must be less than {limit_mb} MB.\n"
                "Want to corrupt larger files? see https://github.com/EEKIM10/cli-utils#installing-the-right-way"
                " (and then run `ruin <file>`)."
            )
        await ctx.defer()

        size = max(min(int(size_in_megabytes * 1024 * 1024), ctx.guild.filesize_limit), 1)

        file = io.BytesIO()
        file.write(os.urandom(size - 1024))
        file.seek(0)
        return await ctx.respond(file=discord.File(file, file_name))

    @staticmethod
    def do_file_corruption(file: io.BytesIO, passes: int, bound_start: int, bound_end: int):
        for _ in range(passes):
            file.seek(random.randint(bound_start, bound_end))
            file.write(os.urandom(random.randint(128, 2048)))
            file.seek(0)
        return file

    @corrupt_file.command(name="ruin")
    async def ruin_corrupt_file(
        self,
        ctx: discord.ApplicationContext,
        file: discord.Attachment,
        passes: int = 10,
        metadata_safety_boundary: float = 5,
    ):
        """Takes a file and corrupts parts of it"""
        await ctx.defer()
        attachment = file
        if attachment.size > 8388608:
            return await ctx.respond(
                "File is too large. Max size 8mb.\n"
                "Want to corrupt larger files? see https://github.com/EEKIM10/cli-utils#installing-the-right-way"
                " (and then run `ruin <file>`)."
            )
        bound_pct = attachment.size * (0.01 * metadata_safety_boundary)
        bound_start = round(bound_pct)
        bound_end = round(attachment.size - bound_pct)
        await ctx.respond("Downloading file...")
        file = io.BytesIO(await file.read())
        file.seek(0)
        await ctx.edit(content="Corrupting file...")
        file = await asyncio.to_thread(self.do_file_corruption, file, passes, bound_start, bound_end)
        file.seek(0)
        await ctx.edit(content="Uploading file...")
        await ctx.edit(content="Here's your corrupted file!", file=discord.File(file, attachment.filename))

    @commands.command(name="kys", aliases=["kill"])
    @commands.is_owner()
    async def end_your_life(self, ctx: commands.Context):
        await ctx.send(":( okay")
        await self.bot.close()

    @commands.slash_command()
    async def ip(self, ctx: discord.ApplicationContext, detailed: bool = False, secure: bool = True):
        """Gets current IP"""
        if not await self.bot.is_owner(ctx.user):
            return await ctx.respond("Internal IP: 0.0.0.0\nExternal IP: 0.0.0.0")

        await ctx.defer(ephemeral=secure)
        ips = await self.get_interface_ip_addresses()
        root = Tree("IP Addresses")
        internal = root.add("Internal")
        external = root.add("External")
        interfaces = internal.add("Interfaces")
        for interface, addresses in ips.items():
            interface_tree = interfaces.add(interface)
            for address in addresses:
                colour = "green" if address["up"] else "red"
                ip_tree = interface_tree.add(
                    f"[{colour}]" + address["ip"] + ((" (up)" if address["up"] else " (down)") if not detailed else "")
                )
                if detailed:
                    ip_tree.add(f"IF Up: {'yes' if address['up'] else 'no'}")
                    ip_tree.add(f"Netmask: {address['netmask']}")
                    ip_tree.add(f"Broadcast: {address['broadcast']}")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.ipify.org") as resp:
                    external.add(await resp.text())
            except aiohttp.ClientError as e:
                external.add(f" [red]Error: {e}")

        with console.capture() as capture:
            console.print(root)
        text = capture.get()
        paginator = commands.Paginator(prefix="```", suffix="```")
        for line in text.splitlines():
            paginator.add_line(line)
        for page in paginator.pages:
            await ctx.respond(page, ephemeral=secure)

    @commands.slash_command()
    async def dig(
        self,
        ctx: discord.ApplicationContext,
        domain: str,
        _type: discord.Option(
            str,
            name="type",
            default="A",
            choices=[
                "A",
                "AAAA",
                "ANY",
                "AXFR",
                "CNAME",
                "HINFO",
                "LOC",
                "MX",
                "NS",
                "PTR",
                "SOA",
                "SRV",
                "TXT",
            ],
        ),
    ):
        """Looks up a domain name"""
        await ctx.defer()
        if re.search(r"\s+", domain):
            return await ctx.respond("Domain name cannot contain spaces.")
        try:
            response = await asyncresolver.resolve(
                domain,
                _type.upper(),
            )
        except Exception as e:
            return await ctx.respond(f"Error: {e}")
        res = response
        tree = Tree(f"DNS Lookup for {domain}")
        for record in res:
            record_tree = tree.add(f"{record.rdtype.name} Record")
            record_tree.add(f"Name: {res.name}")
            record_tree.add(f"Value: {record.to_text()}")
        with console.capture() as capture:
            console.print(tree)
        text = capture.get()
        paginator = commands.Paginator(prefix="```", suffix="```")
        for line in text.splitlines():
            paginator.add_line(line)
        paginator.add_line(empty=True)
        paginator.add_line(f"Exit code: {0}")
        paginator.add_line(f"DNS Server used: {res.nameserver}")
        for page in paginator.pages:
            await ctx.respond(page)

    @commands.slash_command()
    async def traceroute(
        self,
        ctx: discord.ApplicationContext,
        url: str,
        port: discord.Option(int, description="Port to use", default=None),
        ping_type: discord.Option(
            str,
            name="ping-type",
            description="Type of ping to use. See `traceroute --help`",
            choices=["icmp", "tcp", "udp", "udplite", "dccp", "default"],
            default="default",
        ),
        use_ip_version: discord.Option(
            str, name="ip-version", description="IP version to use.", choices=["ipv4", "ipv6"], default="ipv4"
        ),
        max_ttl: discord.Option(int, name="ttl", description="Max number of hops", default=30),
    ):
        """Performs a traceroute request."""
        await ctx.defer()
        if re.search(r"\s+", url):
            return await ctx.respond("URL cannot contain spaces.")

        args = ["sudo", "-E", "-n", "traceroute"]
        flags = {
            "ping_type": {
                "icmp": "-I",
                "tcp": "-T",
                "udp": "-U",
                "udplite": "-UL",
                "dccp": "-D",
            },
            "use_ip_version": {"ipv4": "-4", "ipv6": "-6"},
        }

        if ping_type != "default":
            args.append(flags["ping_type"][ping_type])
        else:
            args = args[3:]  # removes sudo
        args.append(flags["use_ip_version"][use_ip_version])
        args.append("-m")
        args.append(str(max_ttl))
        if port is not None:
            args.append("-p")
            args.append(str(port))
        args.append(url)
        paginator = commands.Paginator()
        paginator.add_line(f"Running command: {' '.join(args[3 if args[0] == 'sudo' else 0:])}")
        paginator.add_line(empty=True)
        try:
            start = time_ns()
            process = await asyncio.create_subprocess_exec(
                args[0],
                *args[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.wait()
            stdout, stderr = await process.communicate()
            end = time_ns()
            time_taken_in_ms = (end - start) / 1000000
            if stdout:
                for line in stdout.splitlines():
                    paginator.add_line(line.decode())
            if stderr:
                for line in stderr.splitlines():
                    paginator.add_line(line.decode())
            paginator.add_line(empty=True)
            paginator.add_line(f"Exit code: {process.returncode}")
            paginator.add_line(f"Time taken: {time_taken_in_ms:,.1f}ms")
        except Exception as e:
            paginator.add_line(f"Error: {e}")
        for page in paginator.pages:
            await ctx.respond(page)

    @commands.slash_command()
    @commands.max_concurrency(1, commands.BucketType.user)
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def screenshot(
        self,
        ctx: discord.ApplicationContext,
        url: str,
        browser: discord.Option(str, description="Browser to use", choices=["chrome", "firefox"], default="chrome"),
        render_timeout: discord.Option(int, name="render-timeout", description="Timeout for rendering", default=3),
        load_timeout: discord.Option(int, name="load-timeout", description="Timeout for page load", default=60),
        window_height: discord.Option(
            int, name="window-height", description="the height of the window in pixels", default=1920
        ),
        window_width: discord.Option(
            int, name="window-width", description="the width of the window in pixels", default=1080
        ),
        capture_whole_page: discord.Option(
            bool,
            name="capture-full-page",
            description="(firefox only) whether to capture the full page or just the viewport.",
            default=False,
        ),
    ):
        """Takes a screenshot of a URL"""
        if capture_whole_page and browser != "firefox":
            return await ctx.respond("The capture-full-page option is only available for firefox.")
        window_width = max(min(1080 * 6, window_width), 1080 // 6)
        window_height = max(min(1920 * 6, window_height), 1920 // 6)
        await ctx.defer()
        if ctx.user.id == 1019233057519177778 and ctx.me.guild_permissions.moderate_members:
            if ctx.user.communication_disabled_until is None:
                await ctx.user.timeout_for(timedelta(minutes=2), reason="no")
        url = urlparse(url)
        if not url.scheme:
            if "/" in url.path:
                hostname, path = url.path.split("/", 1)
            else:
                hostname = url.path
                path = ""
            url = url._replace(scheme="http", netloc=hostname, path=path)

        friendly_url = textwrap.shorten(url.geturl(), 100)

        await ctx.edit(content=f"Preparing to screenshot <{friendly_url}>... (0%, checking filters)")

        async def blacklist_check() -> bool | str:
            async with aiofiles.open("./assets/domains.txt") as blacklist:
                for ln in await blacklist.readlines():
                    if not ln.strip():
                        continue
                    if re.match(ln.strip(), url.netloc):
                        return "Local blacklist"
            return True

        async def dns_check() -> Optional[bool | str]:
            try:
                # noinspection PyTypeChecker
                for response in await asyncio.to_thread(dns.resolver.resolve, url.hostname, "A"):
                    if response.address == "0.0.0.0":
                        return "DNS blacklist"
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.LifetimeTimeout, AttributeError):
                return "Invalid domain or DNS error"
            return True

        done, pending = await asyncio.wait(
            [
                asyncio.create_task(blacklist_check(), name="local"),
                asyncio.create_task(dns_check(), name="dns"),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        done_tasks = done
        try:
            done = done_tasks.pop()
        except KeyError:
            return await ctx.respond("Something went wrong. Try again?\n")
        result = await done
        if not result:
            return await ctx.edit(
                content="That domain is blacklisted, doesn't exist, or there was no answer from the DNS server."
                f" ({result!r})"
            )

        await asyncio.sleep(1)
        await ctx.edit(content=f"Preparing to screenshot <{friendly_url}>... (16%, checking filters)")
        okay = await (pending or done_tasks).pop()
        if not okay:
            return await ctx.edit(
                content="That domain is blacklisted, doesn't exist, or there was no answer from the DNS server."
                f" ({okay!r})"
            )

        await asyncio.sleep(1)
        await ctx.edit(content=f"Screenshotting {textwrap.shorten(url.geturl(), 100)}... (33%, initializing browser)")
        try:
            async with self.lock:
                screenshot, driver, fetch_time, screenshot_time = await self.screenshot_website(
                    ctx,
                    url.geturl(),
                    browser,
                    render_timeout,
                    load_timeout,
                    window_height,
                    window_width,
                    capture_whole_page,
                )
        except TimeoutError:
            return await ctx.edit(content="Rendering screenshot timed out. Try using a smaller resolution.")
        except WebDriverException as e:
            paginator = commands.Paginator(prefix="```", suffix="```")
            paginator.add_line("WebDriver Error (did you pass extreme or invalid command options?)")
            paginator.add_line("Traceback:", empty=True)
            for line in e.msg.splitlines():
                paginator.add_line(line)
            for page in paginator.pages:
                await ctx.respond(page)
        except Exception as e:
            console.print_exception()
            return await ctx.edit(content=f"Failed: {e}", delete_after=30)
        else:
            await ctx.edit(content=f"Screenshotting <{friendly_url}>... (99%, uploading image)")
            await asyncio.sleep(0.5)
            await ctx.edit(
                content="Here's your screenshot!\n"
                "Details:\n"
                f"\\* Browser: {driver}\n"
                f"\\* Resolution: {window_height}x{window_width} ({window_width*window_height:,} pixels)\n"
                f"\\* URL: <{friendly_url}>\n"
                f"\\* Load time: {fetch_time:.2f}ms\n"
                f"\\* Screenshot render time: {screenshot_time:.2f}ms\n"
                f"\\* Total time: {(fetch_time + screenshot_time):.2f}ms\n" +
                (
                    '* Probability of being scat or something else horrifying: 100%'
                    if ctx.user.id == 1019233057519177778 else ''
                ),
                file=screenshot,
            )

    domains = discord.SlashCommandGroup("domains", "Commands for managing domains")

    @domains.command(name="add")
    async def add_domain(self, ctx: discord.ApplicationContext, domain: str):
        """Adds a domain to the blacklist"""
        await ctx.defer()
        if not await self.bot.is_owner(ctx.user):
            return await ctx.respond("You are not allowed to do that.")
        async with aiofiles.open("./assets/domains.txt", "a") as blacklist:
            await blacklist.write(domain.lower() + "\n")
        await ctx.respond("Added domain to blacklist.")

    @domains.command(name="remove")
    async def remove_domain(self, ctx: discord.ApplicationContext, domain: str):
        """Removes a domain from the blacklist"""
        await ctx.defer()
        if not await self.bot.is_owner(ctx.user):
            return await ctx.respond("You are not allowed to do that.")
        async with aiofiles.open("./assets/domains.txt") as blacklist:
            lines = await blacklist.readlines()
        async with aiofiles.open("./assets/domains.txt", "w") as blacklist:
            for line in lines:
                if line.strip() != domain.lower():
                    await blacklist.write(line)
        await ctx.respond("Removed domain from blacklist.")

    # noinspection PyTypeHints
    @commands.slash_command(name="yt-dl")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def yt_dl(
            self,
            ctx: discord.ApplicationContext,
            url: str,
            video_format: discord.Option(
                description="The format to download the video in.",
                autocomplete=format_autocomplete,
                default=""
            ) = "",
            upload_log: bool = True,
            list_formats: bool = False,
            proxy_mode: discord.Option(
                str,
                choices=[
                    "No Proxy",
                    "Dedicated Proxy",
                    "Random Public Proxy"
                ],
                description="Only use if a download was blocked or 403'd.",
                default="No Proxy",
            ) = "No Proxy",
    ):
        """Downloads a video from <URL> using youtube-dl"""
        use_proxy = ["No Proxy", "Dedicated Proxy", "Random Public Proxy"].index(proxy_mode)
        embed = discord.Embed(
            description="Loading..."
        )
        embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/1101463077586735174.gif?v=1")
        await ctx.defer()

        await ctx.respond(embed=embed)
        if list_formats:
            # Nothing actually downloads here
            try:
                formats = await self.list_formats(url, use_proxy=use_proxy)
            except FileNotFoundError:
                _embed = embed.copy()
                _embed.description = "yt-dlp not found."
                _embed.colour = discord.Colour.red()
                _embed.set_thumbnail(url=discord.Embed.Empty)
                return await ctx.edit(embed=_embed)
            except json.JSONDecodeError:
                _embed = embed.copy()
                _embed.description = "Unable to find formats. You're on your own. Wing it."
                _embed.colour = discord.Colour.red()
                _embed.set_thumbnail(url=discord.Embed.Empty)
                return await ctx.edit(embed=_embed)
            else:
                embeds = []
                for fmt in formats.keys():
                    fs = formats[fmt]["filesize"] or 0.1
                    if fs == float("inf"):
                        fs = 0
                        units = ["B"]
                    else:
                        units = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
                        while fs > 1024:
                            fs /= 1024
                            units.pop(0)
                    embeds.append(
                        discord.Embed(
                            title=fmt,
                            description="- Encoding: {0[vcodec]} + {0[acodec]}\n"
                                        "- Extension: `.{0[ext]}`\n"
                                        "- Resolution: {0[resolution]}\n"
                                        "- Filesize: {1}\n"
                                        "- Protocol: {0[protocol]}\n".format(formats[fmt], f"{round(fs, 2)}{units[0]}"),
                            colour=discord.Colour.blurple()
                        ).add_field(
                            name="Download:",
                            value="{} url:{} video_format:{}".format(
                                self.bot.get_application_command("yt-dl").mention,
                                url,
                                fmt
                            )
                        )
                    )
                _paginator = pages.Paginator(embeds, loop_pages=True)
                await ctx.delete(delay=0.1)
                return await _paginator.respond(ctx.interaction)

        with tempfile.TemporaryDirectory(prefix="jimmy-ytdl-") as tempdir:
            video_format = video_format.lower()
            MAX_SIZE = round(ctx.guild.filesize_limit / 1024 / 1024)
            if MAX_SIZE == 8:
                MAX_SIZE = 25
            options = [
                "--no-colors",
                "--no-playlist",
                "--no-check-certificates",
                "--no-warnings",
                "--newline",
                "--restrict-filenames",
                "--output",
                f"{ctx.user.id}.%(title)s.%(ext)s",
            ]
            if video_format:
                options.extend(["--format", f"({video_format})[filesize<={MAX_SIZE}M]"])
            else:
                options.extend(["--format", f"(bv*+ba/b/ba)[filesize<={MAX_SIZE}M]"])

            if use_proxy == 1 and proxy:
                options.append("--proxy")
                options.append(proxy)
                console.log("yt-dlp using proxy: %r", proxy)
            elif use_proxy == 2 and proxies:
                options.append("--proxy")
                options.append(random.choice(proxies))
                console.log("yt-dlp using random proxy: %r", options[-1])

            _embed = embed.copy()
            _embed.description = "Downloading..."
            _embed.colour = discord.Colour.blurple()
            await ctx.edit(
                embed=_embed,
            )
            try:
                venv = Path.cwd() / "venv" / ("Scripts" if os.name == "nt" else "bin")
                if venv:
                    venv = venv.absolute().resolve()
                    if str(venv) not in os.environ["PATH"]:
                        os.environ["PATH"] += os.pathsep + str(venv)

                process = await asyncio.create_subprocess_exec(
                    "yt-dlp",
                    url,
                    *options,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=Path(tempdir).resolve()
                )
                async with ctx.channel.typing():
                    stdout, stderr = await process.communicate()
                    stdout_log = io.BytesIO(stdout)
                    stdout_log_file = discord.File(stdout_log, filename="stdout.txt")
                    stderr_log = io.BytesIO(stderr)
                    stderr_log_file = discord.File(stderr_log, filename="stderr.txt")
                    await process.wait()
            except FileNotFoundError:
                return await ctx.edit(
                    embed=discord.Embed(
                        description="Downloader not found.",
                        color=discord.Color.red()
                    )
                )

            if process.returncode != 0:
                files = [
                    stdout_log_file,
                    stderr_log_file
                ]
                if b"format is not available" in stderr:
                    formats = await self.list_formats(url)
                    embeds = []
                    for fmt in formats.keys():
                        fs = formats[fmt]["filesize"] or 0.1
                        if fs == float("inf"):
                            fs = 0
                            units = ["B"]
                        else:
                            units = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
                            while fs > 1024:
                                fs /= 1024
                                units.pop(0)
                        embeds.append(
                            discord.Embed(
                                title=fmt,
                                description="- Encoding: {0[vcodec]} + {0[acodec]}\n"
                                            "- Extension: `.{0[ext]}`\n"
                                            "- Resolution: {0[resolution]}\n"
                                            "- Filesize: {1}\n"
                                            "- Protocol: {0[protocol]}\n".format(formats[fmt],
                                                                                 f"{round(fs, 2)}{units[0]}"),
                                colour=discord.Colour.blurple()
                            ).add_field(
                                name="Download:",
                                value="{} url:{} video_format:{}".format(
                                    self.bot.get_application_command("yt-dl").mention,
                                    url,
                                    fmt
                                )
                            )
                        )
                    _paginator = pages.Paginator(embeds, loop_pages=True)
                    await ctx.delete(delay=0.1)
                    return await _paginator.respond(ctx.interaction)
                return await ctx.edit(content=f"Download failed:\n```\n{stderr.decode()}\n```", files=files)

            _embed = embed.copy()
            _embed.description = "Download complete."
            _embed.colour = discord.Colour.green()
            _embed.set_thumbnail(url=discord.Embed.Empty)
            await ctx.edit(embed=_embed)
            files = [
                stdout_log_file,
                stderr_log_file
            ] if upload_log else []
            cum_size = 0
            for file in files:
                n_b = len(file.fp.read())
                file.fp.seek(0)
                if n_b == 0:
                    files.remove(file)
                    continue
                elif n_b >= 1024 * 1024 * 256:
                    data = file.fp.read()
                    compressed = await self.bot.loop.run_in_executor(
                        gzip.compress, data, 9
                    )
                    file.fp.close()
                    file.fp = io.BytesIO(compressed)
                    file.fp.seek(0)
                    file.filename += ".gz"
                    cum_size += len(compressed)
                else:
                    cum_size += n_b

            for file_name in Path(tempdir).glob(f"{ctx.user.id}.*"):
                stat = file_name.stat()
                size_mb = stat.st_size / 1024 / 1024
                if (size_mb * 1024 * 1024 + cum_size) >= (MAX_SIZE - 0.256) * 1024 * 1024:
                    warning = f"File {file_name.name} was too large ({size_mb:,.1f}MB vs {MAX_SIZE:.1f}MB)".encode()
                    _x = io.BytesIO(
                        warning
                    )
                    _x.seek(0)
                    cum_size += len(warning)
                    files.append(discord.File(_x, filename=file_name.name + ".txt"))
                try:
                    video = discord.File(file_name, filename=file_name.name)
                    files.append(video)
                except FileNotFoundError:
                    continue
                else:
                    cum_size += size_mb * 1024 * 1024

            if not files:
                return await ctx.edit(embed=discord.Embed(description="No files found.", color=discord.Colour.red()))
            await ctx.edit(
                embed=discord.Embed(
                    title="Here's your video!",
                    color=discord.Colour.green()
                ),
                files=files
            )

    @commands.slash_command(name="yt-dl-beta")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def yt_dl_2(
            self,
            ctx: discord.ApplicationContext,
            url: discord.Option(
                description="The URL to download.",
                type=str
            ),
            list_formats: bool = False,
            _format: discord.Option(
                name="format",
                description="The format to download.",
                type=str,
                autocomplete=format_autocomplete,
                default=""
            ) = "",
            upload_log: bool = False,
            compress_if_possible: bool = False
    ):
        """Downloads a video using youtube-dl"""
        await ctx.defer()
        formats = await self.list_formats(url)
        if list_formats:
            embeds = []
            for fmt in formats.keys():
                fs = formats[fmt].get("filesize", 0.1) or 0.1
                if fs == float("inf"):
                    fs = 0
                    units = ["B"]
                else:
                    units = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
                    while fs > 1024:
                        fs /= 1024
                        units.pop(0)
                embeds.append(
                    discord.Embed(
                        title=fmt,
                        description="- Encoding: {0[vcodec]} + {0[acodec]}\n"
                                    "- Extension: `.{0[ext]}`\n"
                                    "- Resolution: {0[resolution]}\n"
                                    "- Filesize: {1}\n"
                                    "- Protocol: {0[protocol]}\n".format(formats[fmt],
                                                                         f"{round(fs, 2)}{units[0]}"),
                        colour=discord.Colour.blurple()
                    ).add_field(
                        name="Download:",
                        value="{} url:{} video_format:{}".format(
                            self.bot.get_application_command("yt-dl").mention,
                            url,
                            fmt
                        )
                    )
                )
            _paginator = pages.Paginator(embeds, loop_pages=True)
            await ctx.delete(delay=0.1)
            return await _paginator.respond(ctx.interaction)

        if _format:
            _fmt = _format
            for fmt in formats.keys():
                if formats[fmt]["format"] == _format:
                    _format = fmt
                    break
            else:
                return await ctx.edit(
                    embed=discord.Embed(
                        title="Error",
                        description="Invalid format %r. pass `list-formats:True` to see a list of formats." % _fmt,
                        colour=discord.Colour.red()
                    )
                )

        MAX_SIZE_MB = ctx.guild.filesize_limit / 1024 / 1024
        if MAX_SIZE_MB == 8.0:
            MAX_SIZE_MB = 25.0
        BYTES_REMAINING = (MAX_SIZE_MB - 0.256) * 1024 * 1024
        import yt_dlp

        with tempfile.TemporaryDirectory(prefix="jimmy-ytdl-wat") as tempdir_str:
            tempdir = Path(tempdir_str).resolve()
            stdout = tempdir / "stdout.txt"
            stderr = tempdir / "stderr.txt"

            class Logger:
                def __init__(self):
                    self.stdout = open(stdout, "w+")
                    self.stderr = open(stderr, "w+")

                def __del__(self):
                    self.stdout.close()
                    self.stderr.close()

                def debug(self, msg: str):
                    if msg.startswith("[debug]"):
                        return
                    self.info(msg)

                def info(self, msg: str):
                    self.stdout.write(msg + "\n")
                    self.stdout.flush()

                def warning(self, msg: str):
                    self.stderr.write(msg + "\n")
                    self.stderr.flush()

                def error(self, msg: str):
                    self.stderr.write(msg + "\n")
                    self.stderr.flush()

            logger = Logger()
            paths = {
                target: str(tempdir)
                for target in (
                    "home",
                    "temp",
                )
            }

            with yt_dlp.YoutubeDL(
                    {
                        "windowsfilenames": True,
                        "restrictfilenames": True,
                        "noplaylist": True,
                        "nocheckcertificate": True,
                        "no_color": True,
                        "noprogress": True,
                        "logger": logger,
                        "format": _format or f"(bv*+ba/bv/ba/b)[filesize<={MAX_SIZE_MB}M]",
                        "paths": paths,
                        "outtmpl": f"{ctx.user.id}-%(title)s.%(ext)s"
                    }
            ) as downloader:
                try:
                    await ctx.respond(
                        embed=discord.Embed(title="Downloading...", colour=discord.Colour.blurple())
                    )
                    async with ctx.channel.typing():
                        await self.bot.loop.run_in_executor(None, partial(downloader.download, [url]))
                except yt_dlp.utils.DownloadError as e:
                    return await ctx.edit(
                        embed=discord.Embed(
                            title="Error",
                            description=f"Download failed:\n```\n{e}\n```",
                            colour=discord.Colour.red()
                        )
                    )
                else:
                    embed = discord.Embed(
                        title="Downloaded!",
                        description="",
                        colour=discord.Colour.green()
                    )
                    del logger
                    files = []
                    if upload_log:
                        if (out_size := stdout.stat().st_size):
                            files.append(discord.File(stdout, "stdout.txt"))
                            BYTES_REMAINING -= out_size
                        if (err_size := stderr.stat().st_size):
                            files.append(discord.File(stderr, "stderr.txt"))
                            BYTES_REMAINING -= err_size

                    for file in tempdir.glob(f"{ctx.user.id}-*"):
                        if file.stat().st_size == 0:
                            embed.description += f"\N{warning sign}\ufe0f {file.name} is empty.\n"
                            continue
                        st = file.stat().st_size
                        COMPRESS_FAILED = False
                        if compress_if_possible and file.suffix in (".mp4", ".mkv", ".mov"):
                            await ctx.edit(
                                embed=discord.Embed(
                                    title="Compressing...",
                                    description=str(file),
                                    colour=discord.Colour.blurple()
                                )
                            )
                            target = file.with_name(file.name + '.compressed' + file.suffix)
                            ffmpeg_command = [
                                "ffmpeg",
                                "-i",
                                str(file),
                                "-c",
                                "copy",
                                "-crf",
                                "30",
                                "-preset",
                                "slow",
                                str(target)
                            ]
                            try:
                                await self.bot.loop.run_in_executor(
                                    None, 
                                    partial(
                                        subprocess.run, 
                                        ffmpeg_command,
                                        capture_output=True,
                                        check=True
                                    )
                                )
                            except subprocess.CalledProcessError as e:
                                COMPRESS_FAILED = True
                            else:
                                file = target
                                st = file.stat().st_size
                                if st / 1024 / 1024 <= MAX_SIZE_MB and st < BYTES_REMAINING:
                                    files.append(discord.File(file, file.name))
                                    BYTES_REMAINING -= st
                                    continue
                        if st / 1024 / 1024 >= MAX_SIZE_MB or st >= BYTES_REMAINING:
                            units = ["B", "KB", "MB", "GB", "TB"]
                            st_r = st
                            while st_r > 1024:
                                st_r /= 1024
                                units.pop(0)
                            embed.description += "\N{warning sign}\ufe0f {} is too large to upload ({!s}{}" \
                                                 ", max is {}MB{}).\n".format(
                                                    file.name,
                                                    round(st_r, 2),
                                                    units[0],
                                                    MAX_SIZE_MB,
                                                    ', compressing failed' if COMPRESS_FAILED else ''
                                                 )
                            continue
                        else:
                            files.append(discord.File(file, file.name))
                            BYTES_REMAINING -= st

                    if not files:
                        embed.description += "No files to upload. Directory list:\n%s" % (
                            "\n".join(r'\* ' + f.name for f in tempdir.iterdir())
                        )
                        return await ctx.edit(embed=embed)
                    else:
                        _desc = embed.description
                        embed.description += f"Uploading {len(files)} file(s)..."
                        await ctx.edit(embed=embed)
                        await ctx.channel.trigger_typing()
                        embed.description = _desc
                        await ctx.edit(embed=embed, files=files)
                        await asyncio.sleep(120.0)
                        try:
                            await ctx.edit(embed=None)
                        except discord.NotFound:
                            pass
    
    @commands.slash_command(name="text-to-mp3")
    @commands.cooldown(5, 600, commands.BucketType.user)
    async def text_to_mp3(
        self, 
        ctx: discord.ApplicationContext,
        speed: discord.Option(
            int,
            "The speed of the voice. Default is 150.",
            required=False,
            default=150
        ),
        voice: discord.Option(
            str,
            "The voice to use. Some may cause timeout.",
            autocomplete=discord.utils.basic_autocomplete(VOICES),
            default="default"
        )
    ):
        """Converts text to MP3. 5 uses per 10 minutes."""
        if voice not in VOICES:
            return await ctx.respond("Invalid voice.")
        speed = min(300, max(50, speed))
        _self = self
        _bot = self.bot

        class TextModal(discord.ui.Modal):
            def __init__(self):
                super().__init__(
                    discord.ui.InputText(
                        label="Text",
                        placeholder="Enter text to read",
                        min_length=1,
                        max_length=4000,
                        style=discord.InputTextStyle.long
                    ),
                    title="Convert text to an MP3"
                )

            async def callback(self, interaction: discord.Interaction):
                def _convert(text: str) -> Tuple[BytesIO, int]:
                    tmp_dir = tempfile.gettempdir()
                    target_fn = Path(tmp_dir) / f"jimmy-tts-{ctx.user.id}-{ctx.interaction.id}.mp3"
                    target_fn = str(target_fn)
                    engine = pyttsx3.init()
                    engine.setProperty("voice", voice)
                    engine.setProperty("rate", speed)
                    _io = BytesIO()
                    engine.save_to_file(text, target_fn)
                    engine.runAndWait()
                    last_3_sizes = [-3, -2, -1]
                    no_exists = 0

                    def should_loop():
                        if not os.path.exists(target_fn):
                            nonlocal no_exists
                            assert no_exists < 300, "File does not exist for 5 minutes."
                            no_exists += 1
                            return True
                        
                        stat = os.stat(target_fn)
                        for _result in last_3_sizes:
                            if stat.st_size != _result:
                                return True
                    
                        return False

                    while should_loop():
                        if os.path.exists(target_fn):
                            last_3_sizes.pop(0)
                            last_3_sizes.append(os.stat(target_fn).st_size)
                        sleep(1)

                    with open(target_fn, "rb") as f:
                        x = f.read()
                        _io.write(x)
                    os.remove(target_fn)
                    _io.seek(0)
                    return _io, len(x)

                await interaction.response.defer()
                text_pre = self.children[0].value
                if text_pre.startswith("url:"):
                    _url = text_pre[4:].strip()
                    _msg = await interaction.followup.send("Downloading text...")
                    try:
                        response = await _self.http.get(
                            _url, 
                            headers={"User-Agent": "Mozilla/5.0"}, 
                            follow_redirects=True
                        )
                        if response.status_code != 200:
                            await _msg.edit(content=f"Failed to download text. Status code: {response.status_code}")
                            return

                        ct = response.headers.get("Content-Type", "application/octet-stream")
                        if not ct.startswith("text/plain"):
                            await _msg.edit(content=f"Failed to download text. Content-Type is {ct!r}, not text/plain")
                            return
                        text_pre = response.text
                    except (ConnectionError, httpx.HTTPError, httpx.NetworkError) as e:
                        await _msg.edit(content="Failed to download text. " + str(e))
                        return
                    
                else:
                    _msg = await interaction.followup.send("Converting text to MP3... (0 seconds elapsed)")
                
                async def assurance_task():
                    while True:
                        await asyncio.sleep(5.5)
                        await _msg.edit(
                            content=f"Converting text to MP3... ({time() - start_time:.1f} seconds elapsed)"
                        )

                start_time = time()
                task = _bot.loop.create_task(assurance_task())
                try:
                    mp3, size = await asyncio.wait_for(
                        _bot.loop.run_in_executor(None, _convert, text_pre),
                        timeout=300
                    )
                except asyncio.TimeoutError:
                    task.cancel()
                    await _msg.edit(content="Failed to convert text to MP3 - Timeout. Try shorter/less complex text.")
                    return
                except (Exception, IOError) as e:
                    task.cancel()
                    await _msg.edit(content="failed. " + str(e))
                    raise e
                task.cancel()
                del task
                if size >= ctx.guild.filesize_limit - 1500:
                    await _msg.edit(
                        content=f"MP3 is too large ({size / 1024 / 1024}Mb vs "
                                f"{ctx.guild.filesize_limit / 1024 / 1024}Mb)"
                    )
                    return
                fn = ""
                _words = text_pre.split()
                while len(fn) < 28:
                    try:
                        word = _words.pop(0)
                    except IndexError:
                        break
                    if len(fn) + len(word) + 1 > 28:
                        continue
                    fn += word + "-"
                fn = fn[:-1]
                fn = fn[:28]
                await _msg.edit(
                    content="Here's your MP3!",
                    file=discord.File(mp3, filename=fn + ".mp3")
                )
        
        await ctx.send_modal(TextModal())
    
    @commands.slash_command()
    @commands.cooldown(5, 10, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.user)
    async def quote(self, ctx: discord.ApplicationContext):
        """Generates a random quote"""
        emoji = discord.PartialEmoji(name='loading', animated=True, id=1101463077586735174)

        async def get_quote() -> str | discord.File:
            try:
                response = await self.http.get("https://inspirobot.me/api?generate=true")
            except (ConnectionError, httpx.HTTPError, httpx.NetworkError) as e:
                return "Failed to get quote. " + str(e)
            if response.status_code != 200:
                return f"Failed to get quote. Status code: {response.status_code}"
            url = response.text
            try:
                response = await self.http.get(url)
            except (ConnectionError, httpx.HTTPError, httpx.NetworkError) as e:
                return url
            else:
                if response.status_code != 200:
                    return url
                x = io.BytesIO(response.content)
                x.seek(0)
                return discord.File(x, filename="quote.jpg")

        class GenerateNewView(discord.ui.View):
            def __init__(self):
                super().__init__(
                    timeout=300,
                    disable_on_timeout=True
                )

            async def __aenter__(self):
                self.disable_all_items()
                if self.message:
                    await self.message.edit(view=self)
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                self.enable_all_items()
                if self.message:
                    await self.message.edit(view=self)
                return self

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user == ctx.user and interaction.channel == ctx.channel

            @discord.ui.button(
                label="New Quote",
                style=discord.ButtonStyle.green,
                emoji=discord.PartialEmoji.from_str("\U000023ed\U0000fe0f")
            )
            async def new_quote(self, _, interaction: discord.Interaction):
                await interaction.response.defer(invisible=True)
                async with self:
                    followup = await interaction.followup.send(f"{emoji} Generating quote")
                    new_result = await get_quote()
                    if isinstance(new_result, discord.File):
                        return await followup.edit(content=None, file=new_result, view=GenerateNewView())
                    else:
                        return await followup.edit(content=new_result, view=GenerateNewView())

            @discord.ui.button(
                label="Regenerate",
                style=discord.ButtonStyle.blurple,
                emoji=discord.PartialEmoji.from_str("\U0001f504")
            )
            async def regenerate(self, _, interaction: discord.Interaction):
                await interaction.response.defer(invisible=True)
                async with self:
                    message = await interaction.original_response()
                    if "\U00002b50" in [_reaction.emoji for _reaction in message.reactions]:
                        return await interaction.followup.send(
                            "\N{cross mark} Message is starred and cannot be regenerated. You can press "
                            "'New Quote' to generate a new quote instead.",
                            ephemeral=True
                        )
                    new_result = await get_quote()
                    if isinstance(new_result, discord.File):
                        return await interaction.edit_original_response(file=new_result)
                    else:
                        return await interaction.edit_original_response(content=new_result)

            @discord.ui.button(
                label="Delete",
                style=discord.ButtonStyle.red,
                emoji="\N{wastebasket}\U0000fe0f"
            )
            async def delete(self, _, interaction: discord.Interaction):
                await interaction.response.defer(invisible=True)
                await interaction.delete_original_response()
                self.stop()

        await ctx.defer()
        result = await get_quote()
        if isinstance(result, discord.File):
            return await ctx.respond(file=result, view=GenerateNewView())
        else:
            return await ctx.respond(result, view=GenerateNewView())

    @commands.slash_command()
    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.user)
    async def ocr(
            self,
            ctx: discord.ApplicationContext,
            attachment: discord.Option(
                discord.SlashCommandOptionType.attachment,
                description="Image to perform OCR on",
            )
    ):
        """OCRs an image"""
        await ctx.defer()
        timings: Dict[str, float] = {}
        attachment: discord.Attachment
        with Timer(timings, "download attachment"):
            data = await attachment.read()
            file = io.BytesIO(data)
            file.seek(0)
        with Timer(timings, "Parse image"):
            img = await self.bot.loop.run_in_executor(None, Image.open, file)
        try:
            with Timer(timings, "Run OCR"):
                text = await self.bot.loop.run_in_executor(None, pytesseract.image_to_string, img)
        except pytesseract.TesseractError as e:
            return await ctx.respond(f"Failed to perform OCR: `{e}`")

        if len(text) > 4096:
            with Timer(timings, "Upload text to mystbin"):
                try:
                    response = await self.http.put(
                        "https://api.mystb.in/paste",
                        json={
                            "files": [
                                {
                                    "filename": "ocr.txt",
                                    "content": text
                                }
                            ],
                        }
                    )
                    response.raise_for_status()
                except httpx.HTTPError:
                    return await ctx.respond("OCR content too large to post.")
                else:
                    data = response.json()
                    with Timer(timings, "Respond (URL)"):
                        embed = discord.Embed(
                            description="View on [mystb.in](%s)" % ("https://mystb.in/" + data["id"]),
                            colour=discord.Colour.dark_theme()
                        )
                        await ctx.respond(embed=embed)
        else:
            with Timer(timings, "Respond (File)"):
                out_file = io.BytesIO(text.encode("utf-8", "replace"))
                await ctx.respond(file=discord.File(out_file, filename="ocr.txt"))

        await ctx.edit(
            content="Timings:\n" + "\n".join("%s: %s" % (k.title(), v) for k, v in timings.items()),
        )


def setup(bot):
    bot.add_cog(OtherCog(bot))
