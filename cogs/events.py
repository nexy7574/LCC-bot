import asyncio
import hashlib
import inspect
import io
import json
import logging
import os
import random
import re
import subprocess
import textwrap
import traceback
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import discord
import httpx
import pydantic
from bs4 import BeautifulSoup
from discord.ext import commands, pages, tasks

from config import guilds

try:
    from config import dev
except ImportError:
    dev = False
try:
    from config import OAUTH_REDIRECT_URI
except ImportError:
    OAUTH_REDIRECT_URI = None
try:
    from config import GITHUB_PASSWORD, GITHUB_USERNAME
except ImportError:
    GITHUB_USERNAME = None
    GITHUB_PASSWORD = None

try:
    from config import SPAM_CHANNEL
except ImportError:
    SPAM_CHANNEL = None


LTR = "\N{black rightwards arrow}\U0000fe0f"
RTL = "\N{leftwards black arrow}\U0000fe0f"


class MessagePayload(pydantic.BaseModel):
    class MessageAttachmentPayload(pydantic.BaseModel):
        url: str
        proxy_url: str
        filename: str
        size: int
        width: Optional[int] = None
        height: Optional[int] = None
        content_type: str

    event_type: str = "create"
    message_id: int
    author: str
    is_automated: bool = False
    avatar: str
    content: str
    clean_content: str
    at: float
    attachments: list[MessageAttachmentPayload] = []
    reply_to: Optional["MessagePayload"] = None


async def _dc(client: discord.VoiceClient | None):
    if client is None:
        return
    if client.is_playing():
        client.stop()
    try:
        await client.disconnect(force=True)
    finally:
        # client.cleanup()
        pass


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.http = httpx.AsyncClient()
        if not hasattr(self.bot, "bridge_queue") or self.bot.bridge_queue.empty():
            self.bot.bridge_queue = asyncio.Queue()
        self.fetch_discord_atom_feed.start()
        self.bridge_health = False
        self.log = logging.getLogger("jimmy.cogs.events")

    def cog_unload(self):
        self.fetch_discord_atom_feed.cancel()

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        channel: Optional[discord.TextChannel] = self.bot.get_channel(payload.channel_id)
        if channel is not None:
            try:
                message: discord.Message = await channel.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            if payload.emoji.name == "\N{wastebasket}\U0000fe0f":
                if message.author.bot:
                    await message.delete(delay=0.25)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild is None or member.guild.id not in guilds:
            return

        channel: discord.TextChannel = discord.utils.get(member.guild.text_channels, name="general")
        if channel and channel.can_send():
            await channel.send(
                f"{LTR} {member.mention}"
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.guild is None or member.guild.id not in guilds:
            return

        channel: discord.TextChannel = discord.utils.get(member.guild.text_channels, name="general")
        if channel and channel.can_send():
            await channel.send(
                f"{RTL} {member.mention}"
            )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, *_):
        me_voice = member.guild.me.voice
        if me_voice is None or me_voice.channel is None or member.guild.voice_client is None:
            return

        channel = me_voice.channel
        members = [m for m in channel.members if not m.bot]
        if len(members) == 0:
            # We are the only one in the channel
            await _dc(member.guild.voice_client)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return

        if message.channel.name == "femboy-hole":
            def generate_payload(_message: discord.Message) -> MessagePayload:
                _payload = MessagePayload(
                    message_id=_message.id,
                    author=_message.author.display_name,
                    is_automated=_message.author.bot or _message.author.system,
                    avatar=_message.author.display_avatar.with_static_format("webp").with_size(512).url,
                    content=_message.content or "",
                    clean_content=str(_message.clean_content or ""),
                    at=_message.created_at.timestamp(),
                )
                for attachment in _message.attachments:
                    _payload.attachments.append(
                        MessagePayload.MessageAttachmentPayload(
                            url=attachment.url,
                            filename=attachment.filename,
                            proxy_url=attachment.proxy_url,
                            size=attachment.size,
                            width=attachment.width,
                            height=attachment.height,
                            content_type=attachment.content_type,
                        )
                    )
                if _message.reference is not None and _message.reference.cached_message:
                    try:
                        _payload.reply_to = generate_payload(_message.reference.cached_message)
                    except RecursionError:
                        _payload.reply_to = None
                        logging.warning("Failed to generate reply payload for message %s", _message.id, exc_info=True)
                return _payload

            payload = generate_payload(message)
            if message.author != self.bot.user and (payload.content or payload.attachments):
                await self.bot.bridge_queue.put(payload.model_dump())

        if message.channel.name in ("verify", "timetable") and message.author != self.bot.user:
            if message.channel.permissions_for(message.guild.me).manage_messages:
                await message.delete(delay=1)

        if message.content:
            assets = Path.cwd() / "assets"
            words = re.split(r"\s+", message.content)
            words = tuple(map(str.lower, words))
            if "lupupa" in words and (file := assets / "lupupa.jpg").exists():
                await message.reply(file=discord.File(file), delete_after=60)
            elif any(word in words for word in ("fedora", "nix", "nixos")) and (file := assets / "fedora.jpg").exists():
                await message.reply(file=discord.File(file), delete_after=60)
            elif "carat" in words and (file := assets / "carat.jpg").exists():
                await message.reply(file=discord.File(file), delete_after=60)
            elif "boris" in words and (file := assets / "boris.jpg").exists():
                await message.reply(file=discord.File(file), delete_after=60)
            elif "twitter" in words or "vxtwitter" in words:
                new_words = []
                for word in words:
                    if word.lower() == "twitter":
                        new_words.append("~~%s~~ **X**" % word)
                    else:
                        new_words.append(word)
                new_content = " ".join(new_words)
                if len(new_content) > 2000:
                    new_words = []
                    for word in words:
                        if word.lower() == "twitter":
                            new_words.append("**X**")
                        else:
                            new_words.append(word)
                    new_content = " ".join(new_words)
                new_content = new_content.replace("vxtwitter", "fixupx")
                await message.reply(new_content, delete_after=300)

    @commands.Cog.listener("on_message_edit")
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.author.system:
            return
        if before.channel.name == "femboy-hole":
            if before.content != after.content:
                _payload = MessagePayload(
                    message_id=before.id,
                    author=after.author.display_name,
                    is_automated=after.author.bot or after.author.system,
                    avatar=after.author.display_avatar.with_static_format("webp").with_size(512).url,
                    content=after.content or "",
                    clean_content=str(after.clean_content or ""),
                    at=(after.edited_at or after.created_at).timestamp(),
                    event_type="edit"
                )
                await self.bot.bridge_queue.put(_payload.model_dump())

    @commands.Cog.listener("on_message_delete")
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or message.author.system:
            return
        if message.channel.name == "femboy-hole":
            _payload = MessagePayload(
                message_id=message.id,
                author=message.author.display_name,
                is_automated=message.author.bot or message.author.system,
                avatar=message.author.display_avatar.with_static_format("webp").with_size(512).url,
                content=message.content or "",
                clean_content=str(message.clean_content or ""),
                at=message.created_at.timestamp(),
                event_type="redact"
            )
            await self.bot.bridge_queue.put(_payload.model_dump())

    @tasks.loop(minutes=10)
    async def fetch_discord_atom_feed(self):
        if not SPAM_CHANNEL:
            return
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()

        channel = self.bot.get_channel(SPAM_CHANNEL)
        if channel is None or not channel.can_send(discord.Embed()):
            warnings.warn("Cannot send to spam channel, disabling feed fetcher")
            return
        headers = {"User-Agent": f"python-httpx/{httpx.__version__} (Like Akregator/5.22.3); syndication"}

        file = Path.home() / ".cache" / "lcc-bot" / "discord.atom"
        if not file.exists():
            file.parent.mkdir(parents=True, exist_ok=True)
            last_modified = discord.utils.utcnow()
            if dev:
                last_modified = last_modified.replace(day=1, month=last_modified.month - 1)
        else:
            # calculate the sha256 hash of the file, returning the first 32 characters
            # this is used to check if the file has changed
            _hash = hashlib.sha256()
            with file.open("rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    _hash.update(chunk)
            _hash = _hash.hexdigest()[:32]
            headers["If-None-Match"] = f'W/"{_hash}"'
            last_modified = datetime.fromtimestamp(file.stat().st_mtime, tz=timezone.utc)

        try:
            response = await self.http.get("https://discordstatus.com/history.atom", headers=headers)
        except httpx.HTTPError as e:
            self.log.error("Failed to fetch discord atom feed: %r", e, exc_info=e)
            return

        if response.status_code == 304:
            return

        if response.status_code != 200:
            self.log.error("Failed to fetch discord atom feed: HTTP/%s", response.status_code)
            return

        with file.open("wb") as f:
            f.write(response.content)

        incidents_file = Path.home() / ".cache" / "lcc-bot" / "history.json"
        if not incidents_file.exists():
            incidents_file.parent.mkdir(parents=True, exist_ok=True)
            incidents = {}
        else:
            with incidents_file.open("r") as f:
                incidents = json.load(f)

        soup = BeautifulSoup(response.content, "lxml-xml")
        for entry in soup.find_all("entry"):
            published_tag = entry.find("published")
            updated_tag = entry.find("updated") or published_tag
            published = datetime.fromisoformat(published_tag.text)
            updated = datetime.fromisoformat(updated_tag.text)
            if updated > last_modified:
                title = entry.title.text
                content = ""
                soup2 = BeautifulSoup(entry.content.text, "html.parser")
                sep = os.urandom(16).hex()
                for br in soup2.find_all("br"):
                    br.replace_with(sep)
                for _tag in soup2.find_all("p"):
                    text = _tag.get_text()
                    date, _content = text.split(sep, 1)
                    _content = _content.replace(sep, "\n")
                    date = re.sub(r"\s{2,}", " ", date)
                    try:
                        date = datetime.strptime(date, "%b %d, %H:%M PDT")
                        offset = -7
                    except ValueError:
                        date = datetime.strptime(date, "%b %d, %H:%M PST")
                        offset = -8
                    date = date.replace(year=updated.year, tzinfo=timezone(timedelta(hours=offset)))
                    content += f"[{discord.utils.format_dt(date)}]\n> "
                    content += "\n> ".join(_content.splitlines())
                    content += "\n\n"

                _status = {
                    "resolved": discord.Color.green(),
                    "investigating": discord.Color.dark_orange(),
                    "identified": discord.Color.orange(),
                    "monitoring": discord.Color.blurple(),
                }

                colour = _status.get(content.splitlines()[1].split(" - ")[0].lower(), discord.Color.greyple())

                if len(content) > 4096:
                    content = f"[open on discordstatus.com (too large to display)]({entry.link['href']})"

                embed = discord.Embed(
                    title=title, description=content, color=colour, url=entry.link["href"], timestamp=updated
                )
                embed.set_author(
                    name="Discord Status",
                    url="https://discordstatus.com/",
                    icon_url="https://raw.githubusercontent.com/EEKIM10/LCC-bot/"
                    "fe0cb6dd932f9fc2cb0a26433aff8e4cce19279a/assets/discord.png",
                )
                embed.set_footer(
                    text="Published: {} | Updated: {}".format(
                        datetime.fromisoformat(entry.find("published").text).strftime("%Y-%m-%d %H:%M:%S"),
                        updated.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                )

                if entry.id.text not in incidents:
                    msg = await channel.send(embed=embed)
                    incidents[entry.id.text] = msg.id
                else:
                    try:
                        msg = await channel.fetch_message(incidents[entry.id.text])
                        await msg.edit(embed=embed)
                    except discord.HTTPException:
                        msg = await channel.send(embed=embed)
                        incidents[entry.id.text] = msg.id

        with incidents_file.open("w") as f:
            json.dump(incidents, f, separators=(",", ":"))


def setup(bot):
    bot.add_cog(Events(bot))
