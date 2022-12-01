import io
import os
from typing import Tuple, Optional

import discord
import aiohttp
import random
from discord.ext import commands


# noinspection DuplicatedCode
class OtherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
        if size_in_megabytes > 8:
            return await ctx.respond("File size must be less than 8 MB.")
        await ctx.defer()
        file = io.BytesIO()
        file.write(os.urandom(int(size_in_megabytes * 1024 * 1024)))
        file.seek(0)
        return await ctx.respond(file=discord.File(file, file_name))

    @corrupt_file.command(name="ruin")
    async def ruin_corrupt_file(
            self,
            ctx: discord.ApplicationContext,
            file: discord.Attachment,
            passes: int = 10,
            metadata_safety_boundary: float = 5
    ):
        """Takes a file and corrupts parts of it"""
        await ctx.defer()
        attachment = file
        if attachment.size > 8388608:
            return await ctx.respond("File is too large. Max size 8mb.")
        bound_pct = attachment.size * (0.01 * metadata_safety_boundary)
        bound_start = round(bound_pct)
        bound_end = round(attachment.size - bound_pct)
        file = io.BytesIO(await file.read())
        file.seek(0)
        for _ in range(passes):
            file.seek(random.randint(bound_start, bound_end))
            file.write(os.urandom(random.randint(128, 2048)))
            file.seek(0)
        file.seek(0)
        return await ctx.respond(file=discord.File(file, attachment.filename))


def setup(bot):
    bot.add_cog(OtherCog(bot))
