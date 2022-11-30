import asyncio
import textwrap
from typing import Tuple

import discord
from discord.ext import commands
from utils.db import StarBoardMessage


class StarBoardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    async def generate_starboard_embed(self, message: discord.Message) -> discord.Embed:
        star_count = [x for x in message.reactions if str(x.emoji) == "\N{white medium star}"]
        if not star_count:
            star_count = 0
        else:
            star_count = star_count[0].count
        # noinspection PyUnresolvedReferences
        cap = (message.channel if "thread" in message.channel.type.name else message.guild).member_count * 0.1
        embed = discord.Embed(colour=discord.Colour.gold(), timestamp=message.created_at, description=message.content)
        embed.set_author(
            name=message.author.display_name, url=message.jump_url, icon_url=message.author.display_avatar.url
        )

        if star_count > 5:
            stars = "\N{white medium star}x{:,}".format(star_count)
        else:
            stars = "\N{white medium star}" * star_count
            stars = stars or "\N{no entry sign}"

        embed.add_field(
            name="Info",
            value=f"Star count: {stars}\n"
            f"Channel: {message.channel.mention}\n"
            f"Author: {message.author.mention}\n"
            f"URL: [jump]({message.jump_url})\n"
            f"Sent: {discord.utils.format_dt(message.created_at, 'R')}",
            inline=False,
        )
        if message.edited_at:
            embed.fields[0].value += "\nLast edited: " + discord.utils.format_dt(message.edited_at, "R")

        if message.reference is not None:
            try:
                ref: discord.Message = await self.bot.get_channel(message.reference.channel_id).fetch_message(
                    message.reference.message_id
                )
            except discord.HTTPException:
                pass
            else:
                embed.add_field(
                    name="In reply to",
                    value=f"[Message by {ref.author.display_name}]({ref.jump_url}):\n>>> ",
                    inline=False,
                )
                field = embed.fields[1]
                if not ref.content:
                    embed.fields[1].value = field.value.replace(":\n>>> ", "")
                else:
                    embed.fields[1].value += textwrap.shorten(ref.content, 1024 - len(field.value), placeholder="...")

        if message.attachments:
            for file in message.attachments:
                name = f"Attachment #{message.attachments.index(file)}"
                spoiler = file.is_spoiler()
                if not spoiler and file.url.lower().endswith(("png", "jpeg", "jpg", "gif", "webp")) and not embed.image:
                    embed.set_image(url=file.url)
                elif spoiler:
                    embed.add_field(name=name, value=f"||[{file.filename}]({file.url})||", inline=False)
                else:
                    embed.add_field(name=name, value=f"[{file.filename}]({file.url})", inline=False)

        embed.set_footer(text="Starboard threshold for this message was {:.2f}.".format(cap))
        return embed

    @commands.Cog.listener("on_raw_reaction_add")
    @commands.Cog.listener("on_raw_reaction_remove")
    async def on_star_add(self, payload: discord.RawReactionActionEvent):
        async with self.lock:
            if str(payload.emoji) != "\N{white medium star}":
                return
            message: discord.Message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
            star_count = [x for x in message.reactions if str(x.emoji) == "\N{white medium star}"]
            if not star_count:
                star_count = 0
            else:
                star_count = star_count[0].count
            database: Tuple[StarBoardMessage, bool] = await StarBoardMessage.objects.get_or_create(
                {"channel": payload.channel_id}, id=payload.message_id
            )
            entry, created = database
            if created:
                # noinspection PyUnresolvedReferences
                cap = message.channel if "thread" in message.channel.type.name else message.guild
                if self.bot.intents.members and hasattr(cap, "members"):
                    cap = len([x for x in cap.members if not x.bot]) * 0.1
                else:
                    cap = cap.member_count * 0.1
                if star_count >= cap:
                    channel = discord.utils.get(message.guild.text_channels, name="starboard")
                    if channel and channel.can_send():
                        msg = await channel.send(embed=await self.generate_starboard_embed(message))
                        await entry.update(starboard_message=msg.id)
                else:
                    await entry.delete()
                    return
            else:
                channel = discord.utils.get(message.guild.text_channels, name="starboard")
                if channel and channel.can_send() and entry.starboard_message:
                    try:
                        msg = await channel.fetch_message(entry.starboard_message)
                    except discord.NotFound:
                        msg = await channel.send(embed=await self.generate_starboard_embed(message))
                        await entry.update(starboard_message=msg.id)
                    except discord.HTTPException:
                        pass
                    else:
                        await msg.edit(embed=await self.generate_starboard_embed(message))

    @commands.message_command(name="Starboard Info")
    @discord.guild_only()
    async def get_starboard_info(self, ctx: discord.ApplicationContext, message: discord.Message):
        return await ctx.respond(embed=await self.generate_starboard_embed(message))

    @commands.slash_command(name="threshold")
    @commands.guild_only()
    async def get_threshold(self, ctx: discord.ApplicationContext):
        """Shows you the current starboard threshold"""
        if self.bot.intents.members and hasattr(ctx.channel, "members"):
            cap = len([x for x in ctx.channel.members if not x.bot]) * 0.1
        else:
            cap = ctx.channel.member_count * 0.1
        return await ctx.respond(
            f"Messages currently need {cap:.2f} stars in this channel to be posted to the starboard."
        )


def setup(bot):
    bot.add_cog(StarBoardCog(bot))
