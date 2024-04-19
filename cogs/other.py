import asyncio
import glob
import hashlib
import io
import json
import logging
import os
import pathlib
import random
import re
import shutil
import tempfile
import textwrap
import typing
from functools import partial
from pathlib import Path
from time import time
from typing import Dict, Literal, Tuple
from urllib.parse import urlparse

import aiohttp
import config
import discord
import httpx
import openai
import psutil
import pydub
import pytesseract
import pyttsx3
from PIL import Image
from discord.ext import commands
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService

from utils import Timer


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
    logging.error("Failed to load pyttsx3: %r", _pyttsx3_err, exc_info=True)
    pyttsx3 = None
    VOICES = []


async def ollama_stream_reader(response: httpx.Response) -> typing.AsyncGenerator[dict[str, str | int | bool], None]:
    async for chunk in response.aiter_lines():
        # Each line is a JSON string
        try:
            loaded = json.loads(chunk)
            yield loaded
        except json.JSONDecodeError as e:
            logging.warning("Failed to decode chunk %r: %r", chunk, e)
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
        self.transcribe_lock = asyncio.Lock()
        self.http = httpx.AsyncClient()
        self._fmt_cache = {}
        self._fmt_queue = asyncio.Queue()
        self._worker_task = self.bot.loop.create_task(self.cache_population_job())

        self.ollama_locks: dict[discord.Message, asyncio.Event] = {}
        self.context_cache: dict[str, list[int]] = {}
        self.log = logging.getLogger("jimmy.cogs.other")

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

    async def screenshot_website(
        self,
        ctx: discord.ApplicationContext,
        website: str,
        driver: Literal["chrome", "firefox"],
        render_time: int = 10,
        load_timeout: int = 30,
        window_height: int = 2560,
        window_width: int = 1440,
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
                "chrome": ["/usr/bin/chromium", "/usr/bin/google-chrome"],
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
        self.log.info(
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
        self.log.info("Driver '{}' initialised in {} seconds.".format(driver_name, round(end_init - start_init, 2)))

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
        _io.write(data)
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

    @commands.command(name="kys", aliases=["kill"])
    @commands.is_owner()
    async def end_your_life(self, ctx: commands.Context):
        await ctx.send(":( okay")
        await self.bot.close()

    @staticmethod
    async def check_proxy(url: str = "socks5://localhost:1090", *, timeout: float = 3.0):
        async with httpx.AsyncClient(http2=True, timeout=timeout) as client:
            my_ip4 = (await client.get("https://api.ipify.org")).text
            real_ips = [my_ip4]

        # Check the proxy
        async with httpx.AsyncClient(http2=True, proxies=url, timeout=timeout) as client:
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
        return 0

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

    async def _ocr_core(self, attachment: discord.Attachment) -> tuple[dict[str, float], str]:
        timings: dict[str, float] = {}
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
            raise RuntimeError(f"Failed to perform OCR: `{e}`")

        if len(text) >= 1744:
            with Timer() as _t:
                try:
                    file.seek(0)
                    response = await self.http.post(
                        "https://0x0.st",
                        files={
                            "file": ("ocr.txt", io.StringIO(text), "text/plain")
                        },
                    )
                    response.raise_for_status()
                except httpx.HTTPError as e:
                    raise RuntimeError(f"Failed to upload OCR content: `{e}`")
                else:
                    text = "View on [0x0.st](%s)" % response.text.strip()
            timings["Upload text to pastebin"] = _t.total
        return timings, text

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
        attachment: discord.Attachment

        timings, text = await self._ocr_core(attachment)
        embed = discord.Embed(
            description=text,
            colour=discord.Colour.blurple()
        )
        embed.set_image(url=attachment.url)
        with Timer() as _t:
            await ctx.respond(
                embed=embed
            )
        timings["Respond (Text)"] = _t.total

        if timings:
            text = "Timings:\n" + "\n".join("{}: {:.2f}s".format(k.title(), v) for k, v in timings.items())
            await ctx.edit(
                content=text
            )

    @commands.message_command(name="Run OCR")
    async def message_ocr(self, ctx: discord.ApplicationContext, message: discord.Message):
        await ctx.defer()

        embeds = []
        for attachment in message.attachments:
            if attachment.content_type.startswith("image/"):
                timings, text = await self._ocr_core(attachment)
                embed = discord.Embed(
                    title="OCR for " + attachment.filename,
                    description=text,
                    colour=discord.Colour.blurple(),
                    url=message.jump_url
                )
                embed.set_image(url=attachment.url)
                embeds.append(embed)
                if len(embeds) == 25:
                    break

        if not embeds:
            return await ctx.respond(":x: No images found in message.", delete_after=30)
        else:
            return await ctx.respond(embeds=embeds)

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

    @commands.message_command(name="Transcribe")
    async def transcribe_message(self, ctx: discord.ApplicationContext, message: discord.Message):
        class FakeAttachment:
            def __init__(self, *urls: str):
                self.urls = iter(urls)
            
            async def save(self, f):
                async with httpx.AsyncClient() as client:
                    response = None
                    for url in self.urls:
                        try:
                            response: httpx.Response = await client.get(url)
                            response.raise_for_status()
                        except (httpx.HTTPError, ConnectionError) as e:
                            continue
                        async for chunk in response.aiter_bytes():
                            f.write(chunk)
                    else:
                        raise discord.HTTPException(response, "failed to download any of %s" % ", ".join(self.urls))
            
            async def read(self) -> bytes:
                b = io.BytesIO()
                await self.save(b)
                return b.getvalue()

        await ctx.defer()
        async with self.transcribe_lock:
            if not message.attachments and not message.embeds:
                return await ctx.respond("No attachments found.")

            _ft = "wav"
            for attachment in message.attachments:
                if attachment.content_type.startswith(("audio/", "video/")):
                    _ft = attachment.filename.split(".")[-1]
                    break
            else:
                for embed in message.embeds:
                    if embed.type == "video" and embed.video.url.endswith(("mp4", "webm")):
                        _ft = embed.video.url.split(".")[-1]
                        attachment = FakeAttachment(embed.video.proxy_url, embed.video.url)
                        break
                else:
                    return await ctx.respond("No video/audio attachments.")
            if getattr(config, "OPENAI_KEY", None) is None:
                return await ctx.respond("Service unavailable.")
            file_hash = hashlib.sha1(usedforsecurity=False)
            file_hash.update(await attachment.read())
            file_hash = file_hash.hexdigest()

            cache = Path.home() / ".cache" / "lcc-bot" / ("%s-transcript.txt" % file_hash)
            cached = False
            if not cache.exists():
                client = openai.OpenAI(api_key=config.OPENAI_KEY)
                with tempfile.NamedTemporaryFile("wb+", suffix=".mp4") as f:
                    with tempfile.NamedTemporaryFile("wb+", suffix="-" + attachment.filename) as f2:
                        await attachment.save(f2.name)
                        f2.seek(0)
                        seg: pydub.AudioSegment = await asyncio.to_thread(pydub.AudioSegment.from_file, file=f2, format=_ft)
                        seg = seg.set_channels(1)
                        await asyncio.to_thread(seg.export, f.name, format="mp4")
                    f.seek(0)

                    transcript = await asyncio.to_thread(
                        client.audio.transcriptions.create, file=pathlib.Path(f.name), model="whisper-1"
                    )
                    text = transcript.text
                    cache.write_text(text)
            else:
                text = cache.read_text()
                cached = True

            paginator = commands.Paginator("", "", 4096)
            for line in text.splitlines():
                paginator.add_line(textwrap.shorten(line, 4096))
            embeds = list(map(lambda p: discord.Embed(description=p), paginator.pages))
            await ctx.respond(embeds=embeds or [discord.Embed(description="No text found.")])

            if await self.bot.is_owner(ctx.user):
                await ctx.respond(
                    ("Cached response ({})" if cached else "Uncached response ({})").format(file_hash), ephemeral=True
                )


def setup(bot):
    bot.add_cog(OtherCog(bot))
