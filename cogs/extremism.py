# You have been warned - this file is very EXTREME!
import asyncio
import io
from functools import partial
from urllib.parse import parse_qs, urlparse

import blend_modes
import discord
import numpy
import PIL.Image
import PIL.ImageSequence
from discord.ext import commands


def resize_gif(img: PIL.Image.Image, width: int, height: int) -> PIL.Image.Image:
    """Resizes a gif properly"""
    new_frames = []
    for frame in PIL.ImageSequence.Iterator(img):
        frame: PIL.Image.Image = frame.copy()
        frame = frame.resize((width, height))
        new_frames.append(frame)
    _bio = io.BytesIO()
    new_frames[0].save(_bio, "GIF", save_all=True, append_images=new_frames[1:])
    return PIL.Image.open(_bio).convert("RGBA")


def _overlay_images(
    background: PIL.Image.Image, foreground: PIL.Image.Image, mode=blend_modes.overlay, opacity: float = 1.0
) -> PIL.Image.Image:
    background.load()
    foreground.load()
    background = background.convert("RGBA")
    foreground = foreground.convert("RGBA")
    background_img = numpy.array(background)
    background_img_float = background_img.astype(float)
    foreground_img = numpy.array(foreground)
    foreground_img_float = foreground_img.astype(float)

    blended_img_float = mode(background_img_float, foreground_img_float, opacity)

    blended_img = numpy.uint8(blended_img_float)
    return PIL.Image.fromarray(blended_img)


def _overlay_gif(background: PIL.Image.Image, foreground: PIL.Image.Image) -> PIL.Image.Image:
    """Overlays a GIF onto a static background"""
    background = background.convert("RGBA")
    frames = []
    for frame in PIL.ImageSequence.Iterator(foreground):
        bg = background.copy()
        bg.paste(frame, mask=frame)
        frames.append(bg)

    # Save it as a GIF and return it as a PIL image
    _io = io.BytesIO()
    frames[0].save(_io, format="gif", save_all=True, append_images=frames[1:])
    _io.seek(0)
    return PIL.Image.open(_io)


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


def make_circle(img: PIL.Image.Image) -> PIL.Image.Image:
    """Makes an image a circle"""
    # clone the image
    img = img.copy()
    # Create a mask
    mask = PIL.Image.new("L", img.size, 0)
    draw = PIL.ImageDraw.Draw(mask)
    draw.ellipse((0, 0) + img.size, fill=255)
    # Apply the mask
    img.putalpha(mask)
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

    @commands.slash_command(name="decorate")
    async def decorate(
        self,
        ctx: discord.ApplicationContext,
        decoration_url: str,
        user: discord.User = None,
        # animated: bool = True
    ):
        """Decorates an avatar with a decoration."""
        if user is None:
            user = ctx.user

        # Download the image
        await ctx.defer()
        _img_bytes = await user.display_avatar.with_format("png").read()
        img_bio = io.BytesIO(_img_bytes)
        img = PIL.Image.open(img_bio)

        # Parse the URL and get the highest resolution possible
        query = parse_qs(urlparse(decoration_url).query)
        if "size" in query:
            size = int(query["size"][0])
        else:
            size = 640
        size = min(640, max(160, size))

        decoration_url = urlparse(decoration_url)._replace(query="?size={!s}&passthrough=true".format(size)).geturl()

        # Download the decoration
        try:
            _decoration_bytes = await self.bot.http.get_from_cdn(decoration_url)
            decoration_bio = io.BytesIO(_decoration_bytes)
            decoration = PIL.Image.open(decoration_bio)
        except discord.Forbidden:
            return await ctx.respond("Failed to download the decoration (403).")
        except discord.NotFound:
            return await ctx.respond("Failed to download the decoration (404).")

        # Resize the decoration to the avatar size
        # decoration = await asyncio.to_thread(
        #     partial(resize_gif, decoration, img.width, img.height)
        # )
        decoration = decoration.resize((img.width, img.height))

        # Apply the decoration
        new = await asyncio.to_thread(partial(_overlay_gif, img, decoration))

        # Save the image
        img_bytes = io.BytesIO()
        ext = "png"
        new.save(img_bytes, format=ext)
        img_bytes.seek(0)

        # Send the image
        img_bio.seek(0)
        decoration_bio.seek(0)
        files = [
            # discord.File(img_bio, "avatar.png"),
            # discord.File(decoration_bio, "decoration.png"),
            discord.File(img_bytes, filename="decorated." + ext)
        ]
        await ctx.respond(files=files)


def setup(bot):
    bot.add_cog(Extremism(bot))
