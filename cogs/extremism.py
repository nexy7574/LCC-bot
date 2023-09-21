# You have been warned - this file is very EXTREME!
import discord
import asyncio
from functools import partial
from discord.ext import commands
import PIL.Image


def overlay_logo(img: PIL.Image.Image) -> PIL.Image.Image:
    """Overlay the logo on an image."""
    logo = PIL.Image.open("assets/extreme.png", "RGBA")
    logo = logo.resize((1024, 1024))
    img = img.resize((1024, 1024))
    # Use alpha overlay to merge the two
    final = PIL.Image.new("RGBA", img.size)
    final = Image.alpha_composite(final, img)
    final = Image.alpha_composite(final, logo)
    return final


def overlay_purple(img: PIL.Image.Image) -> PIL.Image.Image:
    """Overlay the purple on an image."""
    # purple_overlay_rgb = 0x440099
    purple_overlay_rgba = (68, 0, 153, 0.5)
    # Create the overlay image
    overlay = PIL.Image.new("RGBA", img.size, purple_overlay_rgba)

    # resize to 1024x1024
    img = img.resize((1024, 1024))
    overlay = overlay.resize((1024, 1024))

    # Use alpha overlay to merge the two
    final = PIL.Image.new("RGBA", img.size)
    final = Image.alpha_composite(final, img)
    final = Image.alpha_composite(final, overlay)
    return final


def extremify(img: PIL.Image.Image) -> PIL.Image.Image:
    """Apply the EXTREME effect to an image."""
    img = overlay_logo(img)
    img = overlay_purple(img)
    return img


class Extremism(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.slash_command(name="radicalise")
    async def radicalise(self, ctx, image: discord.Attachment):
        """Makes an image extremely radical."""
        if not image.content_type.startswith("image/"):
            await ctx.send("That's not an image!")
            return
        await ctx.defer()
        # Download the image
        _img_bytes = await image.read()
        # Open the image
        img = PIL.Image.open(io.BytesIO(_img_bytes), "RGBA")
        # Apply the EXTREME effect
        img = await asyncio.to_thread(extremify, img)
        # Save the image
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        # Send the image
        await ctx.respond(file=discord.File(img_bytes, filename="extreme.png"))


def setup(bot):
    bot.add_cog(Extremism(bot))
