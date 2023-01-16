import asyncio
import io
import os
import random
import re
import textwrap
import traceback

import dns.resolver
import aiofiles
from time import time
from typing import Literal
from typing import Tuple, Optional, Dict
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import discord
import psutil
from discord.ext import commands
from rich.tree import Tree
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
# from selenium.webdriver.ie

from utils import console


# noinspection DuplicatedCode
class OtherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def screenshot_website(
        self,
        ctx: discord.ApplicationContext,
        website: str,
        driver: Literal["chrome", "firefox"],
        render_time: int = 10,
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
                "chrome": [
                    "/usr/bin/chromium",
                    "/usr/bin/chrome",
                    "/usr/bin/chrome-browser",
                    "/usr/bin/google-chrome"
                ],
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
        console.log(
            "Driver '{}' initialised in {} seconds.".format(
                driver_name, round(end_init - start_init, 2)
            )
        )

        async def _edit(content: str):
            self.bot.loop.create_task(ctx.interaction.edit_original_response(content=content))

        await _edit(content=f"Screenshotting <{friendly_url}>... (49%)")
        await _blocking(driver.set_page_load_timeout, render_time)
        start = time()
        await _blocking(driver.get, website)
        end = time()
        get_time = round((end - start) * 1000)
        await _edit(content=f"Screenshotting <{friendly_url}>... (66%)")
        await asyncio.sleep(render_time)
        await _edit(content=f"Screenshotting <{friendly_url}>... (83%)")
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
    async def screenshot(
        self,
        ctx: discord.ApplicationContext,
        url: str,
        browser: discord.Option(str, description="Browser to use", choices=["chrome", "firefox"], default="firefox"),
        render_timeout: discord.Option(int, name="render-timeout", description="Timeout for rendering", default=10),
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
        )
    ):
        """Takes a screenshot of a URL"""
        window_width = max(min(1080 * 6, window_width), 1080 // 6)
        window_height = max(min(1920 * 6, window_height), 1920 // 6)
        await ctx.defer()
        # if ctx.user.id == 1019233057519177778:
        #     if getattr(self.bot, "ALLOW_MATTHEW", False) is False:
        #         return await ctx.respond("No.")
        url = urlparse(url)
        if not url.scheme:
            if "/" in url.path:
                hostname, path = url.path.split("/", 1)
            else:
                hostname = url.path
                path = ""
            url = url._replace(scheme="http", netloc=hostname, path=path)

        friendly_url = textwrap.shorten(url.geturl(), 100)

        await ctx.edit(content=f"Preparing to screenshot <{friendly_url}>... (0%)")

        async def blacklist_check() -> bool | str:
            async with aiofiles.open("domains.txt") as blacklist:
                for line in await blacklist.readlines():
                    if not line.strip():
                        continue
                    if re.match(line.strip(), url.netloc):
                        return "Local blacklist"
                        # return await ctx.edit(content="That domain is blacklisted.")
            return True

        async def dns_check() -> Optional[bool | str]:
            try:
                for response in await asyncio.to_thread(dns.resolver.resolve, url.hostname, "A"):
                    if response.address == "0.0.0.0":
                        return "DNS blacklist"
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.LifetimeTimeout, AttributeError):
                return
            else:
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
        if result is not True:
            return await ctx.edit(
                content="That domain is blacklisted, doesn't exist, or there was no answer from the DNS server."
                f" ({result!r})"
            )

        await asyncio.sleep(1)
        await ctx.edit(content=f"Preparing to screenshot <{friendly_url}>... (16%)")
        okay = await (pending or done_tasks).pop()
        if okay is not True:
            return await ctx.edit(
                content="That domain is blacklisted, doesn't exist, or there was no answer from the DNS server."
                f" ({result!r})"
            )

        await asyncio.sleep(1)
        await ctx.edit(content=f"Screenshotting {textwrap.shorten(url.geturl(), 100)}... (33%)")
        try:
            screenshot, driver, fetch_time, screenshot_time = await self.screenshot_website(
                ctx, url.geturl(), browser, render_timeout, window_height, window_width, capture_whole_page
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
            await ctx.edit(content=f"Screenshotting <{friendly_url}>... (99%)")
            await asyncio.sleep(0.5)
            await ctx.edit(
                content="Here's your screenshot!\n"
                        "Details:\n"
                        f"\\* Browser: {driver}\n"
                        f"\\* Resolution: {window_height}x{window_width} ({window_width*window_height:,} pixels)\n"
                        f"\\* URL: <{friendly_url}>\n"
                        f"\\* Load time: {fetch_time:.2f}ms\n"
                        f"\\* Screenshot render time: {screenshot_time:.2f}ms\n",
                file=screenshot
            )

    domains = discord.SlashCommandGroup("domains", "Commands for managing domains")

    @domains.command(name="add")
    async def add_domain(self, ctx: discord.ApplicationContext, domain: str):
        """Adds a domain to the blacklist"""
        await ctx.defer()
        if not await self.bot.is_owner(ctx.user):
            return await ctx.respond("You are not allowed to do that.")
        async with aiofiles.open("domains.txt", "a") as blacklist:
            await blacklist.write(domain.lower() + "\n")
        await ctx.respond("Added domain to blacklist.")

    @domains.command(name="remove")
    async def remove_domain(self, ctx: discord.ApplicationContext, domain: str):
        """Removes a domain from the blacklist"""
        await ctx.defer()
        if not await self.bot.is_owner(ctx.user):
            return await ctx.respond("You are not allowed to do that.")
        async with aiofiles.open("domains.txt") as blacklist:
            lines = await blacklist.readlines()
        async with aiofiles.open("domains.txt", "w") as blacklist:
            for line in lines:
                if line.strip() != domain.lower():
                    await blacklist.write(line)
        await ctx.respond("Removed domain from blacklist.")


def setup(bot):
    bot.add_cog(OtherCog(bot))
