import discord
import aiohttp
import random
from datetime import datetime
from discord.ext import commands


class OtherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.slash_command()
    async def xkcd(self, ctx: discord.ApplicationContext, *, number: int = None):
        """Shows an XKCD comic"""
        async with aiohttp.ClientSession() as session:
            if number is None:
                async with session.get("https://c.xkcd.com/random/comic") as response:
                    if response.status != 302:
                        number = random.randint(100, 999)
                    else:
                        number = int(response['location'].split['/'][-2])

            async with session.get("https://xkcd.com/{!s}/info.0.json".format(number)) as response:
                if response.status != 200:
                    return await ctx.respond("Sorry, xkcd.com is unavailable at the moment.")
                data = await response.json()
        embed = discord.Embed(
            title=data["safe_title"],
            description=data['alt'],
            color=discord.Colour.embed_background()
        )
        embed.set_image(url=data['img'])
        return await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(OtherCog(bot))
