# You have been warned - this file is very EXTREME!
import discord
import asyncio
import io
import numpy
import blend_modes
from functools import partial
from discord.ext import commands
import PIL.Image
from PIL import Image


def _overlay_images(
    background: PIL.Image.Image, 
    foreground: PIL.Image.Image, 
    mode=blend_modes.overlay,
    opacity: float = 1.0
) -> PIL.Image.Image:
    background = background.convert("RGBA")
    foreground = foreground.convert("RGBA")
    background.load()
    foreground.load()
    background_img = numpy.array(background)
    background_img_float = background_img.astype(float)
    foreground_img = numpy.array(foreground)
    foreground_img_float = foreground_img.astype(float)

    blended_img_float = mode(background_img_float, foreground_img_float, opacity)

    blended_img = numpy.uint8(blended_img_float)
    return PIL.Image.fromarray(blended_img)


def overlay_logo(img: PIL.Image.Image) -> PIL.Image.Image:
    """Overlay the logo on an image."""
    # clone the image
    img = img.copy()
    logo = PIL.Image.open("assets/extreme.png")
    logo.convert("RGBA")
    logo.load()
    logo = logo.resize((1024, 1024))
    img = img.resize((1024, 1024))
    
    img = _overlay_images(img, logo, blend_modes.lighten_only, 1)
    return img


def overlay_purple(img: PIL.Image.Image) -> PIL.Image.Image:
    """Overlay the purple on an image."""
    # purple_overlay_rgb = 0x440099
    purple_overlay_rgba = (68, 0, 153)
    # Create the overlay image
    overlay = PIL.Image.new("RGBA", img.size, purple_overlay_rgba)

    # resize to 1024x1024
    img = img.copy().resize((1024, 1024))
    overlay = overlay.resize((1024, 1024))

    img = _overlay_images(img, overlay)
    return img


def extremify(img: PIL.Image.Image) -> PIL.Image.Image:
    """Apply the EXTREME effect to an image."""
    img = overlay_purple(img)
    img = overlay_logo(img)
    return img


class Extremism(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.slash_command(name="radicalise")
    async def radicalise(self, ctx, image: discord.Attachment = None, user: discord.User = None):
        """Makes an image extremely radical."""
        if image is None:
            if user is None:
                user = ctx.author
            image = user.avatar.with_format("png")
        else:
            if not image.content_type.startswith("image/"):
                await ctx.send("That's not an image!")
                return
        await ctx.defer()
        # Download the image
        _img_bytes = await image.read()
        # Open the image
        img = PIL.Image.open(io.BytesIO(_img_bytes))
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
