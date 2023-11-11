import asyncio
import functools
import glob
import io
import json
import typing

import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import traceback
from functools import partial
from io import BytesIO
from pathlib import Path
from time import sleep, time, time_ns
from typing import Dict, Literal, Optional, Tuple
from urllib.parse import urlparse

import aiofiles
import aiohttp
import discord
import dns.resolver
import httpx
import psutil
import pytesseract
import pyttsx3
from discord.ext import commands
from dns import asyncresolver
from PIL import Image
from rich.tree import Tree
from rich import print
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService

from utils import Timer, console

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

try:
    _engine = pyttsx3.init()
    # noinspection PyTypeChecker
    VOICES = [x.id for x in _engine.getProperty("voices")]
    del _engine
except Exception as _pyttsx3_err:
    print("Failed to load pyttsx3: %s" % _pyttsx3_err, file=sys.stderr)
    pyttsx3 = None
    VOICES = []


# class OllamaStreamReader:
#     def __init__(self, response: httpx.Response):
#         self.response = response
#         self.stream = response.aiter_bytes(1)
#         self._buffer = b""
#
#     async def __aiter__(self):
#         return self
#
#     async def __anext__(self) -> dict[str, str | int | bool]:
#         if self.response.is_stream_consumed:
#             raise StopAsyncIteration
#         self._buffer = b""
#         while not self._buffer.endswith(b"}\n"):
#             async for char in self.stream:
#                 self._buffer += char
#
#         return json.loads(self._buffer.decode("utf-8", "replace"))


async def ollama_stream_reader(response: httpx.Response) -> typing.AsyncGenerator[
    dict[str, str | int | bool], None
]:
    print("Starting to iterate over ollama response %r..." % response, file=sys.stderr)
    async for chunk in response.aiter_bytes():
        # Each chunk is a JSON string
        try:
            loaded = json.loads(chunk.strip().decode("utf-8", "replace"))
            print("Loaded chunk: %r" % loaded)
            yield loaded
        except json.JSONDecodeError as e:
            print("Failed to decode chunk %r: %r" % (chunk, e), file=sys.stderr)
            pass


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

        self.ollama_locks: dict[discord.Message, asyncio.Event] = {}

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
                    "cookiefile": Path(__file__).parent.parent / "jimmy-cookies.txt",
                }
            ) as downloader:
                try:
                    info = await self.bot.loop.run_in_executor(
                        None, partial(downloader.extract_info, url, download=False)
                    )
                except yt_dlp.utils.DownloadError:
                    return {}
                info = downloader.sanitize_info(info)
                new = {
                    fmt["format_id"]: {
                        "id": fmt["format_id"],
                        "ext": fmt["ext"],
                        "protocol": fmt["protocol"],
                        "acodec": fmt.get("acodec", "?"),
                        "vcodec": fmt.get("vcodec", "?"),
                        "resolution": fmt.get("resolution", "?x?"),
                        "filesize": fmt.get("filesize", float("inf")),
                        "format": fmt.get("format", "?"),
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
        # if ctx.user.id == 1019233057519177778 and ctx.me.guild_permissions.moderate_members:
        #     if ctx.user.communication_disabled_until is None:
        #         await ctx.user.timeout_for(timedelta(minutes=2), reason="no")
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

        await ctx.edit(content=f"Preparing to screenshot <{friendly_url}>... (16%, checking filters)")
        okay = await (pending or done_tasks).pop()
        if not okay:
            return await ctx.edit(
                content="That domain is blacklisted, doesn't exist, or there was no answer from the DNS server."
                f" ({okay!r})"
            )

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
            await ctx.edit(
                content="Here's your screenshot!\n"
                "Details:\n"
                f"* Browser: {driver}\n"
                f"* Resolution: {window_height}x{window_width} ({window_width*window_height:,} pixels)\n"
                f"* URL: <{friendly_url}>\n"
                f"* Load time: {fetch_time:.2f}ms\n"
                f"* Screenshot render time: {screenshot_time:.2f}ms\n"
                f"* Total time: {(fetch_time + screenshot_time):.2f}ms\n"
                + (
                    "* Probability of being scat or something else horrifying: 100%"
                    if ctx.user.id == 1019233057519177778
                    else ""
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

    @staticmethod
    async def check_proxy(url: str = "socks5://localhost:1090"):
        client = httpx.AsyncClient(http2=True, timeout=3)
        my_ip4 = (await client.get("https://api.ipify.org")).text
        real_ips = [my_ip4]
        await client.aclose()

        # Check the proxy
        client = httpx.AsyncClient(http2=True, proxies=url, timeout=3)
        try:
            response = await client.get(
                "https://1.1.1.1/cdn-cgi/trace",
            )
            response.raise_for_status()
            for line in response.text.splitlines():
                if line.startswith("ip"):
                    if any(x in line for x in real_ips):
                        return 1
        except (httpx.TransportError, httpx.HTTPStatusError):
            return 2
        await client.aclose()
        return 0

    @commands.slash_command(name="yt-dl")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def yt_dl_2(
        self,
        ctx: discord.ApplicationContext,
        url: discord.Option(description="The URL to download.", type=str),
        _format: discord.Option(
            name="format", description="The format to download.", type=str, autocomplete=format_autocomplete, default=""
        ) = "",
        extract_audio: bool = False,
        cookies_txt: discord.Attachment = None,
        disable_filesize_buffer: bool = False,
    ):
        """Downloads a video using youtube-dl"""
        cookies = io.StringIO()
        cookies.seek(0)

        await ctx.defer()
        from urllib.parse import parse_qs

        MAX_SIZE_MB = 20
        REAL_MAX_SIZE_MB = 25
        if disable_filesize_buffer is False:
            MAX_SIZE_MB *= 0.8
        BYTES_REMAINING = (MAX_SIZE_MB - 0.256) * 1024 * 1024
        import yt_dlp

        with tempfile.TemporaryDirectory(prefix="jimmy-ytdl-") as tempdir_str:
            tempdir = Path(tempdir_str).resolve()
            stdout = tempdir / "stdout.txt"
            stderr = tempdir / "stderr.txt"

            default_cookies_txt = Path.cwd() / "jimmy-cookies.txt"
            real_cookies_txt = tempdir / "cookies.txt"
            if cookies_txt is not None:
                await cookies_txt.save(fp=real_cookies_txt)
            else:
                default_cookies_txt.touch()
                shutil.copy(default_cookies_txt, real_cookies_txt)

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

            args = {
                "windowsfilenames": True,
                "restrictfilenames": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "no_color": True,
                "noprogress": True,
                "logger": logger,
                "format": _format or None,
                "paths": paths,
                "outtmpl": f"{ctx.user.id}-%(title).50s.%(ext)s",
                "trim_file_name": 128,
                "extract_audio": extract_audio,
                "format_sort": [
                    "vcodec:h264",
                    "acodec:aac",
                    "vcodec:vp9",
                    "acodec:opus",
                    "acodec:vorbis",
                    "vcodec:vp8",
                    "ext",
                ],
                "merge_output_format": "webm/mp4/mov/flv/avi/ogg/m4a/wav/mp3/opus/mka/mkv",
                "source_address": "0.0.0.0",
                "cookiefile": str(real_cookies_txt.resolve().absolute()),
                "concurrent_fragment_downloads": 4
            }
            description = ""
            proxy_url = "socks5://localhost:1090"
            try:
                proxy_down = await self.check_proxy("socks5://localhost:1090")
                if proxy_down > 0:
                    if proxy_down == 1:
                        description += ":warning: (SHRoNK) Proxy check leaked IP - trying backup proxy.\n"
                    elif proxy_down == 2:
                        description += ":warning: (SHRoNK) Proxy connection failed - trying backup proxy.\n"
                    else:
                        description += ":warning: (SHRoNK) Unknown proxy error - trying backup proxy.\n"

                    proxy_down = await self.check_proxy("socks5://localhost:1080")
                    if proxy_down > 0:
                        if proxy_down == 1:
                            description += ":warning: (NexBox) Proxy check leaked IP..\n"
                        elif proxy_down == 2:
                            description += ":warning: (NexBox) Proxy connection failed.\n"
                        else:
                            description += ":warning: (NexBox) Unknown proxy error.\n"
                        proxy_url = None
                    else:
                        proxy_url = "socks5://localhost:1080"
                        description += "\N{white heavy check mark} Using fallback NexBox proxy."
                else:
                    description += "\N{white heavy check mark} Using the SHRoNK proxy."
            except Exception as e:
                traceback.print_exc()
                description += f":warning: Failed to check proxy (`{e}`). Going unproxied."
            if proxy_url:
                args["proxy"] = proxy_url
            if extract_audio:
                args["postprocessors"] = [
                    {"key": "FFmpegExtractAudio", "preferredquality": "24", "preferredcodec": "opus"}
                ]
                args["format"] = args["format"] or f"(ba/b)[filesize<={MAX_SIZE_MB}M]/ba/b"

            if args["format"] is None:
                args["format"] = f"(bv+ba/b)[vcodec!=h265][vcodec!=av01][filesize<={MAX_SIZE_MB}M]/b"

            with yt_dlp.YoutubeDL(args) as downloader:
                try:
                    extracted_info = await asyncio.to_thread(downloader.extract_info, url, download=False)
                except yt_dlp.utils.DownloadError:
                    title = "error"
                    thumbnail_url = webpage_url = discord.Embed.Empty
                else:
                    title = extracted_info.get("title", url)
                    title = textwrap.shorten(title, 100)
                    thumbnail_url = extracted_info.get("thumbnail") or discord.Embed.Empty
                    webpage_url = extracted_info.get("webpage_url") or discord.Embed.Empty

                    chosen_format = extracted_info.get("format")
                    chosen_format_id = extracted_info.get("format_id")
                    final_extension = extracted_info.get("ext")
                    format_note = extracted_info.get("format_note", "%s (%s)" % (chosen_format, chosen_format_id))
                    resolution = extracted_info.get("resolution")
                    fps = extracted_info.get("fps")
                    vcodec = extracted_info.get("vcodec")
                    acodec = extracted_info.get("acodec")

                    lines = []
                    if chosen_format and chosen_format_id:
                        lines.append(
                            "* Chosen format: `%s` (`%s`)" % (chosen_format, chosen_format_id),
                        )
                    if format_note:
                        lines.append(
                            "* Format note: %r" % format_note
                        )
                    if final_extension:
                        lines.append(
                            "* File extension: " + final_extension
                        )
                    if resolution:
                        _s = resolution
                        if fps:
                            _s += " @ %s FPS" % fps
                        lines.append(
                            "* Resolution: " + _s
                        )
                    if vcodec or acodec:
                        lines.append("%s+%s" % (vcodec or "N/A", acodec or "N/A"))

                    if lines:
                        description += "\n"
                        description += "\n".join(lines)

                try:
                    embed = discord.Embed(
                        title="Downloading %r..." % title,
                        description=description,
                        colour=discord.Colour.blurple(),
                        url=webpage_url,
                    )
                    embed.set_thumbnail(url=thumbnail_url)
                    await ctx.respond(embed=embed)
                    await asyncio.to_thread(partial(downloader.download, [url]))
                except yt_dlp.utils.DownloadError as e:
                    traceback.print_exc()
                    return await ctx.edit(
                        embed=discord.Embed(
                            title="Error",
                            description=f"Download failed:\n```\n{e}\n```",
                            colour=discord.Colour.red(),
                            url=webpage_url,
                        ),
                        delete_after=60,
                    )
                else:
                    parsed_qs = parse_qs(url)
                    if "t" in parsed_qs and parsed_qs["t"] and parsed_qs["t"][0].isdigit():
                        # Assume is timestamp
                        timestamp = round(float(parsed_qs["t"][0]))
                        end_timestamp = None
                        if len(parsed_qs["t"]) >= 2:
                            end_timestamp = round(float(parsed_qs["t"][1]))
                            if end_timestamp < timestamp:
                                end_timestamp, timestamp = reversed((end_timestamp, timestamp))
                        _end = "to %s" % end_timestamp if len(parsed_qs["t"]) == 2 else "onward"
                        embed = discord.Embed(
                            title="Trimming...",
                            description=f"Trimming from {timestamp} seconds {_end}.\nThis may take a while.",
                            colour=discord.Colour.blurple(),
                        )
                        await ctx.edit(embed=embed)
                        for file in tempdir.glob("%s-*" % ctx.user.id):
                            try:
                                bak = file.with_name(file.name + "-" + os.urandom(4).hex())
                                shutil.copy(str(file), str(bak))
                                minutes, seconds = divmod(timestamp, 60)
                                hours, minutes = divmod(minutes, 60)
                                _args = [
                                    "ffmpeg",
                                    "-i",
                                    str(bak),
                                    "-ss",
                                    "{!s}:{!s}:{!s}".format(*map(round, (hours, minutes, seconds))),
                                    "-y",
                                    "-c",
                                    "copy",
                                    str(file),
                                ]
                                if end_timestamp is not None:
                                    minutes, seconds = divmod(end_timestamp, 60)
                                    hours, minutes = divmod(minutes, 60)
                                    _args.insert(5, "-to")
                                    _args.insert(6, "{!s}:{!s}:{!s}".format(*map(round, (hours, minutes, seconds))))

                                await self.bot.loop.run_in_executor(
                                    None, partial(subprocess.run, _args, check=True, capture_output=True)
                                )
                                bak.unlink(True)
                            except subprocess.CalledProcessError as e:
                                traceback.print_exc()
                                return await ctx.edit(
                                    embed=discord.Embed(
                                        title="Error",
                                        description=f"Trimming failed:\n```\n{e}\n```",
                                        colour=discord.Colour.red(),
                                    ),
                                    delete_after=30,
                                )

                    embed = discord.Embed(
                        title="Downloaded %r!" % title, description="", colour=discord.Colour.green(), url=webpage_url
                    )
                    embed.set_thumbnail(url=thumbnail_url)
                    del logger
                    files = []

                    for file in tempdir.glob(f"{ctx.user.id}-*"):
                        if file.stat().st_size == 0:
                            embed.description += f"\N{warning sign}\ufe0f {file.name} is empty.\n"
                            continue
                        st = file.stat().st_size
                        if st / 1024 / 1024 >= REAL_MAX_SIZE_MB:
                            units = ["B", "KB", "MB", "GB", "TB"]
                            st_r = st
                            while st_r > 1024:
                                st_r /= 1024
                                units.pop(0)
                            embed.description += (
                                "\N{warning sign}\ufe0f {} is too large to upload ({!s}{}"
                                ", max is {}MB).\n".format(
                                    file.name,
                                    round(st_r, 2),
                                    units[0],
                                    REAL_MAX_SIZE_MB,
                                )
                            )
                            continue
                        else:
                            files.append(discord.File(file, file.name))
                            BYTES_REMAINING -= st

                    if not files:
                        embed.description += "No files to upload. Directory list:\n%s" % (
                            "\n".join(r"\* " + f.name for f in tempdir.iterdir())
                        )
                        return await ctx.edit(embed=embed)
                    else:
                        _desc = embed.description
                        embed.description += f"Uploading {len(files)} file(s):\n%s" % (
                            "\n".join("* `%s`" % f.filename for f in files)
                        )
                        await ctx.edit(embed=embed)
                        await ctx.channel.trigger_typing()
                        embed.description = _desc
                        start = time()
                        await ctx.edit(embed=embed, files=files)
                        end = time()
                        if (end - start) < 10:
                            await ctx.respond("*clearing typing*", delete_after=0.01)

                        async def bgtask():
                            await asyncio.sleep(120.0)
                            try:
                                await ctx.edit(embed=None)
                            except discord.NotFound:
                                pass

                        self.bot.loop.create_task(bgtask())

    @commands.slash_command(name="text-to-mp3")
    @commands.cooldown(5, 600, commands.BucketType.user)
    async def text_to_mp3(
        self,
        ctx: discord.ApplicationContext,
        speed: discord.Option(int, "The speed of the voice. Default is 150.", required=False, default=150),
        voice: discord.Option(
            str,
            "The voice to use. Some may cause timeout.",
            autocomplete=discord.utils.basic_autocomplete(VOICES),
            default="default",
        ),
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
                        style=discord.InputTextStyle.long,
                    ),
                    title="Convert text to an MP3",
                )

            async def callback(self, interaction: discord.Interaction):
                def _convert(text: str) -> Tuple[BytesIO, int]:
                    assert pyttsx3
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
                            _url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
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
                    mp3, size = await asyncio.wait_for(_bot.loop.run_in_executor(None, _convert, text_pre), timeout=600)
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
                await _msg.edit(content="Here's your MP3!", file=discord.File(mp3, filename=fn + ".mp3"))

        await ctx.send_modal(TextModal())

    @commands.slash_command()
    @commands.cooldown(5, 10, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.user)
    async def quote(self, ctx: discord.ApplicationContext):
        """Generates a random quote"""
        emoji = discord.PartialEmoji(name="loading", animated=True, id=1101463077586735174)

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
                super().__init__(timeout=300, disable_on_timeout=True)

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
                emoji=discord.PartialEmoji.from_str("\U000023ed\U0000fe0f"),
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
                label="Regenerate", style=discord.ButtonStyle.blurple, emoji=discord.PartialEmoji.from_str("\U0001f504")
            )
            async def regenerate(self, _, interaction: discord.Interaction):
                await interaction.response.defer(invisible=True)
                async with self:
                    message = await interaction.original_response()
                    if "\U00002b50" in [_reaction.emoji for _reaction in message.reactions]:
                        return await interaction.followup.send(
                            "\N{cross mark} Message is starred and cannot be regenerated. You can press "
                            "'New Quote' to generate a new quote instead.",
                            ephemeral=True,
                        )
                    new_result = await get_quote()
                    if isinstance(new_result, discord.File):
                        return await interaction.edit_original_response(file=new_result)
                    else:
                        return await interaction.edit_original_response(content=new_result)

            @discord.ui.button(label="Delete", style=discord.ButtonStyle.red, emoji="\N{wastebasket}\U0000fe0f")
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
        ),
    ):
        """OCRs an image"""
        await ctx.defer()
        timings: Dict[str, float] = {}
        attachment: discord.Attachment
        with Timer() as _t:
            data = await attachment.read()
            file = io.BytesIO(data)
            file.seek(0)
            timings["Download attachment"] = _t.total
        with Timer() as _t:
            img = await self.bot.loop.run_in_executor(None, Image.open, file)
            timings["Parse image"] = _t.total
        try:
            with Timer() as _t:
                text = await self.bot.loop.run_in_executor(None, pytesseract.image_to_string, img)
                timings["Perform OCR"] = _t.total
        except pytesseract.TesseractError as e:
            return await ctx.respond(f"Failed to perform OCR: `{e}`")

        if len(text) > 4096:
            with Timer() as _t:
                try:
                    response = await self.http.put(
                        "https://api.mystb.in/paste",
                        json={
                            "files": [{"filename": "ocr.txt", "content": text}],
                        },
                    )
                    response.raise_for_status()
                except httpx.HTTPError:
                    return await ctx.respond("OCR content too large to post.")
                else:
                    data = response.json()
                    with Timer(timings, "Respond (URL)"):
                        embed = discord.Embed(
                            description="View on [mystb.in](%s)" % ("https://mystb.in/" + data["id"]),
                            colour=discord.Colour.dark_theme(),
                        )
                        await ctx.respond(embed=embed)
            timings["Upload text to mystbin"] = _t.total
        elif len(text) <= 1500:
            with Timer() as _t:
                await ctx.respond(embed=discord.Embed(description=text))
            timings["Respond (Text)"] = _t.total
        else:
            with Timer() as _t:
                out_file = io.BytesIO(text.encode("utf-8", "replace"))
                await ctx.respond(file=discord.File(out_file, filename="ocr.txt"))
            timings["Respond (File)"] = _t.total

        if timings:
            text = "Timings:\n" + "\n".join("{}: {:.2f}s".format(k.title(), v) for k, v in timings.items())
            await ctx.edit(
                content=text,
            )

    @commands.message_command(name="Convert Image to GIF")
    async def convert_image_to_gif(self, ctx: discord.ApplicationContext, message: discord.Message):
        await ctx.defer()
        for attachment in message.attachments:
            if attachment.content_type.startswith("image/"):
                break
        else:
            return await ctx.respond("No image found.")
        image = attachment
        image: discord.Attachment
        with tempfile.TemporaryFile("wb+") as f:
            await image.save(f)
            f.seek(0)
            img = await self.bot.loop.run_in_executor(None, Image.open, f)
            if img.format.upper() not in ("PNG", "JPEG", "WEBP", "HEIF", "BMP", "TIFF"):
                return await ctx.respond("Image must be PNG, JPEG, WEBP, or HEIF.")

            with tempfile.TemporaryFile("wb+") as f2:
                caller = partial(img.save, f2, format="GIF")
                await self.bot.loop.run_in_executor(None, caller)
                f2.seek(0)
                try:
                    await ctx.respond(file=discord.File(f2, filename="image.gif"))
                except discord.HTTPException as e:
                    if e.code == 40005:
                        return await ctx.respond("Image is too large.")
                    return await ctx.respond(f"Failed to upload: `{e}`")
                try:
                    f2.seek(0)
                    await ctx.user.send(file=discord.File(f2, filename="image.gif"))
                except discord.Forbidden:
                    return await ctx.respond("Unable to mirror to your DM - am I blocked?", ephemeral=True)

    @commands.slash_command()
    @commands.cooldown(1, 180, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.user)
    async def sherlock(
        self, ctx: discord.ApplicationContext, username: str, search_nsfw: bool = False, use_tor: bool = False
    ):
        """Sherlocks a username."""
        # git clone https://github.com/sherlock-project/sherlock.git && cd sherlock && docker build -t sherlock .

        if re.search(r"\s", username) is not None:
            return await ctx.respond("Username cannot contain spaces.")

        async def background_task():
            chars = ["|", "/", "-", "\\"]
            n = 0
            # Every 5 seconds update the embed to show that the command is still running
            while True:
                await asyncio.sleep(2.5)
                elapsed = time() - start_time
                embed = discord.Embed(
                    title="Sherlocking username %s" % chars[n % 4],
                    description=f"Elapsed: {elapsed:.0f}s",
                    colour=discord.Colour.dark_theme(),
                )
                await ctx.edit(embed=embed)
                n += 1

        await ctx.defer()
        # output results to a temporary directory
        tempdir = Path("./tmp/sherlock").resolve()
        tempdir.mkdir(parents=True, exist_ok=True)
        command = [
            "docker",
            "run",
            "--rm",
            "-t",
            "-v",
            f"{tempdir}:/opt/sherlock/results",
            "sherlock",
            "--folderoutput",
            "/opt/sherlock/results",
            "--print-found",
            "--csv",
        ]
        if search_nsfw:
            command.append("--nsfw")
        if use_tor:
            command.append("--tor")
        # Output to result.csv
        # Username to search for
        command.append(username)
        # Run the command
        start_time = time()
        result = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await ctx.respond(embed=discord.Embed(title="Starting..."))
        task = asyncio.create_task(background_task())
        # Wait for it to finish
        stdout, stderr = await result.communicate()
        await result.wait()
        task.cancel()
        # wait for task to exit
        try:
            await task
        except asyncio.CancelledError:
            pass
        # If it errored, send the error
        if result.returncode != 0:
            shutil.rmtree(tempdir, ignore_errors=True)
            return await ctx.edit(
                embed=discord.Embed(
                    title="Error",
                    description=f"```ansi\n{stderr.decode()[:4000]}```",
                    colour=discord.Colour.red(),
                )
            )
        # If it didn't error, send the results
        stdout = stdout.decode()
        if len(stdout) > 4000:
            paginator = commands.Paginator("```ansi", max_size=4000)
            for line in stdout.splitlines():
                paginator.add_line(line)
            desc = paginator.pages[0]
            title = "Results (truncated)"
        else:
            desc = f"```ansi\n{stdout}```"
            title = "Results"
        files = list(map(discord.File, glob.glob(f"{tempdir}/*")))
        await ctx.edit(
            files=files,
            embed=discord.Embed(
                title=title,
                description=desc,
                colour=discord.Colour.green(),
            ),
        )
        shutil.rmtree(tempdir, ignore_errors=True)

    @commands.slash_command()
    @discord.guild_only()
    async def opusinate(self, ctx: discord.ApplicationContext, file: discord.Attachment, size_mb: float = 8):
        """Converts the given file into opus with the given size."""

        def humanise(v: int) -> str:
            units = ["B", "KB", "MB", "GB", "TB", "PB", "EB"]
            while v > 1024:
                v /= 1024
                units.pop(0)
            n = round(v, 2) if v % 1 else v
            return "%s%s" % (n, units[0])

        await ctx.defer()
        size_bytes = size_mb * 1024 * 1024
        max_size = ctx.guild.filesize_limit if ctx.guild else 8 * 1024 * 1024
        share = False
        if os.path.exists("/mnt/vol/share/droplet.secret"):
            share = True

        if size_bytes > max_size or share is False or (share is True and size_mb >= 250):
            return await ctx.respond(":x: Max file size is %dMB" % round(max_size / 1024 / 1024))

        ct, suffix = file.content_type.split("/")
        if ct not in ("audio", "video"):
            return await ctx.respond(":x: Only audio or video please.")
        with tempfile.NamedTemporaryFile(suffix="." + suffix) as raw_file:
            location = Path(raw_file.name)
            location.write_bytes(await file.read(use_cached=False))

            process = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-of",
                "json",
                "-show_entries",
                "format=duration,bit_rate,channels",
                "-show_streams",
                "-select_streams",
                "a",  # select audio-nly
                str(location),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                return await ctx.respond(
                    ":x: Error gathering metadata.\n```\n%s\n```" % discord.utils.escape_markdown(stderr.decode())
                )

            metadata = json.loads(stdout.decode())
            try:
                stream = metadata["streams"].pop()
            except IndexError:
                return await ctx.respond(":x: No audio streams to transcode.")
            duration = float(metadata["format"]["duration"])
            bit_rate = math.floor(int(metadata["format"]["bit_rate"]) / 1024)
            channels = int(stream["channels"])
            codec = stream["codec_name"]

            target_bitrate = math.floor((size_mb * 8192) / duration)
            if target_bitrate <= 0:
                return await ctx.respond(
                    ":x: Target size too small (would've had a negative bitrate of %d)" % target_bitrate
                )
            br_ceiling = 255 * channels
            end_br = min(bit_rate, target_bitrate, br_ceiling)

            with tempfile.NamedTemporaryFile(suffix=".ogg", prefix=file.filename) as output_file:
                command = [
                    "ffmpeg",
                    "-i",
                    str(location),
                    "-v",
                    "error",
                    "-vn",
                    "-sn",
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "%sK" % end_br,
                    "-y",
                    output_file.name,
                ]
                process = await asyncio.create_subprocess_exec(
                    command[0], *command[1:], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    return await ctx.respond(
                        ":x: There was an error while transcoding:\n```\n%s\n```"
                        % discord.utils.escape_markdown(stderr.decode())
                    )

                output_location = Path(output_file.name)
                stat = output_location.stat()
                content = (
                    "\N{white heavy check mark} Transcoded from %r to opus @ %dkbps.\n\n"
                    "* Source: %dKbps\n* Target: %dKbps\n* Ceiling: %dKbps\n* Calculated: %dKbps\n"
                    "* Duration: %.1f seconds\n* Input size: %s\n* Output size: %s\n* Difference: %s"
                    " (%dKbps)"
                ) % (
                    codec,
                    end_br,
                    bit_rate,
                    target_bitrate,
                    br_ceiling,
                    end_br,
                    duration,
                    humanise(file.size),
                    humanise(stat.st_size),
                    humanise(file.size - stat.st_size),
                    bit_rate - end_br,
                )
                if stat.st_size <= max_size or share is False:
                    if stat.st_size >= (size_bytes - 100):
                        return await ctx.respond(":x: File was too large.")
                    return await ctx.respond(content, file=discord.File(output_location))
                else:
                    share_location = Path("/mnt/vol/share/tmp/") / output_location.name
                    share_location.touch(0o755)
                    await self.bot.loop.run_in_executor(
                        None, functools.partial(shutil.copy, output_location, share_location)
                    )
                    return await ctx.respond(
                        "%s\n* [Download](https://droplet.nexy7574.co.uk/share/tmp/%s)"
                        % (content, output_location.name)
                    )

    class OllamaKillSwitchView(discord.ui.View):
        def __init__(self, ctx: commands.Context, msg: discord.Message):
            super().__init__(timeout=None)
            self.ctx = ctx
            self.msg = msg

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            return interaction.user == self.ctx.author and interaction.channel == self.ctx.channel

        @discord.ui.button(
            label="Abort",
            style=discord.ButtonStyle.red,
            emoji="\N{wastebasket}",
        )
        async def abort_button(self, _, interaction: discord.Interaction):
            await interaction.response.defer()
            if self.msg in self.ctx.command.cog.ollama_locks:
                self.ctx.command.cog.ollama_locks[self.msg].set()
                self.disable_all_items()
                await interaction.edit_original_response(view=self)
                self.stop()

    @commands.command(
        usage="[model:<name:tag>] [server:<ip[:port]>] <query>"
    )
    @commands.is_owner()
    @commands.max_concurrency(1, wait=True)
    async def ollama(self, ctx: commands.Context, *, query: str):
        """:3"""
        if query.startswith("model:"):
            model, query = query.split(" ", 1)
            model = model[6:].casefold()
            try:
                _name, _tag = model.split(":", 1)
            except ValueError:
                model += ":latest"
        else:
            model = "orca-mini"

        if query.startswith("server:"):
            host, query = query.split(" ", 1)
            host = host[7:]
            try:
                host, port = host.split(":", 1)
                int(port)
            except ValueError:
                host += ":11434"
        else:
            try_hosts = [
                "127.0.0.1:11434",  # Localhost
                "100.106.34.86:11434",  # Laptop
                "100.66.187.46:11434",  # optiplex
                "100.116.242.161:11434"  # Raspberry Pi
            ]
            async with httpx.AsyncClient(follow_redirects=True) as client:
                for host in try_hosts:
                    try:
                        response = await client.get(
                            f"http://{host}/api/tags",
                        )
                        response.raise_for_status()
                    except (httpx.TransportError, httpx.NetworkError, httpx.HTTPStatusError):
                        continue
                    else:
                        break
                else:
                    return await ctx.reply(":x: No servers available.")

        msg = await ctx.reply(f"Preparing [{model!r}](http://{host}) <a:loading:1101463077586735174>")
        async with httpx.AsyncClient(base_url=f"http://{host}/api", follow_redirects=True) as client:
            # get models
            try:
                response = await client.post("/show", json={"name": model})
            except httpx.TransportError as e:
                return await msg.edit(content="Failed to connect to Ollama: `%s`" % e)
            if response.status_code == 404:
                await msg.edit(content=f"Downloading model {model}, please wait.")
                async with ctx.channel.typing():
                    async with client.stream(
                        "POST",
                        "/pull",
                        json={"name": model, "stream": True},
                        timeout=None
                    ) as response:
                        if response.status_code != 200:
                            error = await response.aread()
                            return await msg.edit(content="Failed to download model: `%s`" % error.decode())
                        async for chunk in ollama_stream_reader(response):
                            print(chunk)
                            if "total" in chunk and "completed" in chunk:
                                completed = chunk["completed"] or 1  # avoid division by zero
                                total = chunk["total"] or 1
                                percent = round(completed / total * 100)
                                if percent == 100 and completed != total:
                                    percent = round(completed / total * 100, 2)
                                if not percent % 10 or percent >= 90.5:
                                    await msg.edit(content=f"`{chunk['status']}` - {percent}%")
                            else:
                                await msg.edit(content=f"`{chunk['status']}`")
                await msg.edit(content=f"Downloaded model {model}.")
                while (await client.post("/show", json={"name": model})).status_code != 200:
                    await asyncio.sleep(5)
            elif response.status_code != 200:
                error = await response.aread()
                return await msg.edit(content="Failed to get model: `%s`" % error.decode())

            output = discord.Embed(
                title=f"{model} says:",
                description="",
                colour=discord.Colour.blurple(),
                timestamp=discord.utils.utcnow()
            )
            output.set_footer(text="Powered by Ollama")
            await msg.edit(embed=output)
            async with ctx.channel.typing():
                async with client.stream(
                    "POST",
                    "/generate",
                    json={
                        "model": model,
                        "prompt": query,
                        "format": "json",
                        "system": "You are a discord bot called Jimmy Saville. "
                                  "Be helpful and make sure your response is safe for work, "
                                  "and is less than 3500 characters. Ensure a brief and quick response.",
                        "stream": True
                    },
                    timeout=None
                ) as response:
                    if response.status_code != 200:
                        error = await response.aread()
                        return await msg.edit(content="Failed to generate text: `%s`" % error.decode())
                    self.ollama_locks[msg] = asyncio.Event()
                    view = self.OllamaKillSwitchView(ctx, msg)
                    await msg.edit(view=view)
                    async for chunk in ollama_stream_reader(response):
                        if "done" not in chunk.keys() or "response" not in chunk.keys():
                            continue
                        else:
                            content = "Response is still being generated..."
                            if chunk["done"] is True:
                                content = None
                            output.description += chunk["response"]
                            last_edit = msg.edited_at.timestamp() if msg.edited_at else msg.created_at.timestamp()
                            if (time() - last_edit) >= 5 or chunk["done"] is True:
                                await msg.edit(content=content, embed=output, view=view)
                            if self.ollama_locks[msg].is_set():
                                return await msg.edit(content="Aborted.", embed=output, view=None)
                            if len(output.description) >= 4000:
                                output.add_field(
                                    name="Aborting early",
                                    value="Output exceeded 4000 characters."
                                )
                                break

                    def get_time_spent(nanoseconds: int) -> str:
                        hours, minutes, seconds = 0, 0, 0
                        seconds = nanoseconds / 1e9
                        if seconds >= 60:
                            minutes, seconds = divmod(seconds, 60)
                        if minutes >= 60:
                            hours, minutes = divmod(minutes, 60)

                        result = []
                        if seconds:
                            if seconds != 1:
                                label = "seconds"
                            else:
                                label = "second"
                            result.append(f"{round(seconds)} {label}")
                        if minutes:
                            if minutes != 1:
                                label = "minutes"
                            else:
                                label = "minute"
                            result.append(f"{round(minutes)} {label}")
                        if hours:
                            if hours != 1:
                                label = "hours"
                            else:
                                label = "hour"
                            result.append(f"{round(hours)} {label}")
                        return ", ".join(reversed(result))

                    total_time_spent = get_time_spent(chunk["total_duration"])
                    eval_time_spent = get_time_spent(chunk["eval_duration"])
                    tokens_per_second = chunk["eval_count"] / chunk["eval_duration"]
                    output.add_field(
                        name="Timings",
                        value="Total: {}\nEval: {} ({:,.2f}/s)".format(
                            total_time_spent,
                            eval_time_spent,
                            tokens_per_second
                        ),
                    )
                    await msg.edit(content=None, embed=output, view=None)
                    self.ollama_locks.pop(msg, None)


def setup(bot):
    bot.add_cog(OtherCog(bot))
