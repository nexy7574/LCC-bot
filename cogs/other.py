import asyncio
import io
import os
import random
import re
from time import sleep as time_sleep
from typing import Literal
from typing import Tuple, Optional, Dict
from pathlib import Path

import aiohttp
import discord
import psutil
from discord.ext import commands
from rich.tree import Tree
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService

from utils import console


# noinspection DuplicatedCode
class OtherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    async def screenshot_website(ctx: discord.ApplicationContext, website: str, driver: Literal['chrome', 'firefox'], render_time: int = 10) -> discord.File:
        if not Path("/usr/bin/firefox").exists():
            driver = 'chrome'
        if not Path("/usr/bin/geckodriver").exists():
            driver = 'chrome'

        if driver == 'chrome':
            options = ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920x1080")
            options.add_argument("--disable-extensions")
            options.add_argument("--incognito")
            for opt in ("chrome", "chromium"):
                if Path(f"/usr/bin/{opt}").exists():
                    options.binary_location = f"/usr/bin/{opt}"
                    break
            else:
                options.binary_location = "/usr/bin/chromium"
            service = ChromeService("/usr/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
        else:
            options = FirefoxOptions()
            options.add_argument('--headless')
            options.add_argument("--private-window")
            options.add_argument("--safe-mode")
            options.add_argument("--new-instance")
            for opt in ("firefox", "firefox-esr"):
                if Path(f"/usr/bin/{opt}").exists():
                    options.binary_location = f"/usr/bin/{opt}"
                    break
            else:
                options.binary_location = "/usr/bin/firefox"
            service = FirefoxService("/usr/bin/geckodriver")
            driver = webdriver.Firefox(service=service, options=options)

        await ctx.edit(content="Loading website...")
        await asyncio.to_thread(driver.get, website)
        await ctx.edit(content=f"Waiting {render_time:,} seconds to render...")
        await asyncio.sleep(render_time)
        await ctx.edit(content="Taking screenshot...")
        domain = re.sub(r"https?://", "", website)
        data = await asyncio.to_thread(driver.get_screenshot_as_png)
        _io = io.BytesIO()
        _io.write(data)
        _io.seek(0)
        driver.quit()
        return discord.File(_io, f"{domain}.png")

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
                            "speed": stats[key].speed
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
            return await ctx.respond("Internal IP: 0.0.0.0\n"
                                     "External IP: 0.0.0.0")

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
            browser: discord.Option(
                str,
                description="Browser to use",
                choices=[
                    "chrome",
                    "firefox"
                ],
                default="chrome"
            ),
            render_timeout: int = 10
    ):
        """Takes a screenshot of a URL"""
        await ctx.defer()
        if ctx.user.id == 1019233057519177778:
            if getattr(self.bot, "ALLOW_MATTHEW", False) is False:
                return await ctx.respond("No.")

        if "2girls1cup.ca" in url:
            return await ctx.respond("No.")

        if not url.startswith("http"):
            url = "https://" + url

        await ctx.respond("Taking screenshot...")
        try:
            screenshot = await self.screenshot_website(
                ctx,
                url,
                browser,
                render_timeout
            )
        except Exception as e:
            console.print_exception()
            return await ctx.edit(content=f"Error: {e}")
        else:
            await ctx.edit(content="Here's your screenshot!", file=screenshot)

def setup(bot):
    bot.add_cog(OtherCog(bot))
