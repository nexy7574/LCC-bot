import hashlib
import inspect
import io
import json
import os
import random
import re
import asyncio
import textwrap
import subprocess
import traceback
import warnings
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import discord
import httpx
from discord.ext import commands, pages, tasks
from utils import Student, get_or_none, console
from config import guilds
from utils.db import AccessTokens
try:
    from config import dev
except ImportError:
    dev = False
try:
    from config import OAUTH_REDIRECT_URI
except ImportError:
    OAUTH_REDIRECT_URI = None
try:
    from config import GITHUB_USERNAME
    from config import GITHUB_PASSWORD
except ImportError:
    GITHUB_USERNAME = None
    GITHUB_PASSWORD = None

try:
    from config import SPAM_CHANNEL
except ImportError:
    SPAM_CHANNEL = None


LTR = "\N{black rightwards arrow}\U0000fe0f"
RTL = "\N{leftwards black arrow}\U0000fe0f"


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

    def cog_unload(self):
        self.fetch_discord_atom_feed.cancel()

    # noinspection DuplicatedCode
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

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        channel: Optional[discord.TextChannel] = self.bot.get_channel(payload.channel_id)
        if channel is not None:
            try:
                message: discord.Message = await channel.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            if payload.emoji.name == "\N{wastebasket}\U0000fe0f":
                if message.author.id == self.bot.user.id:
                    await message.delete(delay=0.25)
                elif message.channel.permissions_for(message.guild.me).manage_messages:
                    reactions = 0
                    mod_reactions = 0
                    for reaction in message.reactions:
                        if reaction.emoji == payload.emoji:
                            async for member in reaction.users():
                                if member.id == self.bot.user.id:
                                    continue
                                if member.guild_permissions.manage_messages:
                                    mod_reactions += 1
                                reactions += 1
                    if reactions >= 2 or mod_reactions >= 1:
                        await message.delete(delay=0.1)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild is None or member.guild.id not in guilds:
            return

        student: Optional[Student] = await get_or_none(Student, user_id=member.id)
        if student and student.id:
            role = discord.utils.find(lambda r: r.name.lower() == "verified", member.guild.roles)
            if role and role < member.guild.me.top_role:
                await member.add_roles(role, reason="Verified")

        channel: discord.TextChannel = discord.utils.get(member.guild.text_channels, name="general")
        if channel and channel.can_send():
            await channel.send(
                f"{LTR} {member.mention} (`{member}`, {f'{student.id}' if student else 'pending verification'})"
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.guild is None or member.guild.id not in guilds:
            return

        student: Optional[Student] = await get_or_none(Student, user_id=member.id)
        channel: discord.TextChannel = discord.utils.get(member.guild.text_channels, name="general")
        if channel and channel.can_send():
            await channel.send(
                f"{RTL} {member.mention} (`{member}`, {f'{student.id}' if student else 'pending verification'})"
            )

    async def process_message_for_github_links(self, message: discord.Message):
        RAW_URL = "https://github.com/{repo}/raw/{branch}/{path}"
        _re = re.match(
            r"https://github\.com/(?P<repo>[a-zA-Z0-9-]+/[\w.-]+)/blob/(?P<path>[^#>]+)(\?[^#>]+)?"
            r"(#L(?P<start_line>\d+)(([-~:]|(\.\.))L(?P<end_line>\d+))?)",
            message.content
        )
        if _re:
            branch, path = _re.group("path").split("/", 1)
            _p = Path(path).suffix
            url = RAW_URL.format(
                repo=_re.group("repo"),
                branch=branch,
                path=path
            )
            if all((GITHUB_PASSWORD, GITHUB_USERNAME)):
                auth = (GITHUB_USERNAME, GITHUB_PASSWORD)
            else:
                auth = None
            response = await self.http.get(url, follow_redirects=True, auth=auth)
            if response.status_code == 200:
                ctx = await self.bot.get_context(message)
                lines = response.text.splitlines()
                if _re.group("start_line"):
                    start_line = int(_re.group("start_line")) - 1
                    end_line = int(_re.group("end_line")) if _re.group("end_line") else start_line + 1
                    lines = lines[start_line:end_line]

                paginator = commands.Paginator(prefix="```" + _p[1:], suffix="```", max_size=1000)
                for line in lines:
                    paginator.add_line(line)

                _pages = paginator.pages
                paginator2 = pages.Paginator(_pages, timeout=300)
                # noinspection PyTypeChecker
                await paginator2.send(ctx, reference=message.to_reference())
                if message.channel.permissions_for(message.guild.me).manage_messages:
                    await message.edit(suppress=True)
        else:
            RAW_URL = "https://github.com/{repo}/archive/refs/heads/{branch}.zip"
            _full_re = re.finditer(
                r"https://github\.com/(?P<repo>[a-zA-Z0-9-]+/[\w.-]+)(/tree/(?P<branch>[^#>]+))?\.(git|zip)",
                message.content
            )
            for _match in _full_re:
                repo = _match.group("repo")
                branch = _match.group("branch") or "master"
                url = RAW_URL.format(
                    repo=repo,
                    branch=branch,
                )
                if all((GITHUB_PASSWORD, GITHUB_USERNAME)):
                    auth = (GITHUB_USERNAME, GITHUB_PASSWORD)
                else:
                    auth = None
                async with message.channel.typing():
                    response = await self.http.get(url, follow_redirects=True, auth=auth)
                if response.status_code == 200:
                    content = response.content
                    if len(content) > message.guild.filesize_limit - 1000:
                        continue
                    _io = io.BytesIO(content)
                    fn = f"{repo.replace('/', '-')}-{branch}.zip"
                    await message.reply(file=discord.File(_io, filename=fn))
                    if message.channel.permissions_for(message.guild.me).manage_messages:
                        await message.edit(suppress=True)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member, 
        *_
    ):
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
        def play_voice(_file):
            async def internal():
                if message.author.voice is not None and message.author.voice.channel is not None:
                    voice: discord.VoiceClient | None = None
                    if message.guild.voice_client is not None:
                        # noinspection PyUnresolvedReferences
                        if message.guild.voice_client.is_playing():
                            return
                        try:
                            await _dc(message.guild.voice_client)
                        except discord.HTTPException:
                            pass
                    try:
                        voice = await message.author.voice.channel.connect(timeout=10, reconnect=False)
                    except asyncio.TimeoutError:
                        await message.channel.trigger_typing()
                        await message.reply(
                            "I'd play the song but discord's voice servers are shit.", 
                            file=discord.File(_file)
                        )
                        region = message.author.voice.channel.rtc_region
                        # noinspection PyUnresolvedReferences
                        console.log(
                            "Timed out connecting to voice channel: {0.name} in {0.guild.name} "
                            "(region {1})".format(
                                message.author.voice.channel,
                                region.name if region else "auto (unknown)"
                            )
                        )
                        return
                    
                    if voice.channel != message.author.voice.channel:
                        await voice.move_to(message.author.voice.channel)
                    
                    if message.guild.me.voice.self_mute or message.guild.me.voice.mute:
                        await message.channel.trigger_typing()
                        await message.reply("Unmute me >:(", file=discord.File(_file))
                    else:
                        
                        def after(err):
                            self.bot.loop.create_task(
                                _dc(voice),
                            )
                            if err is not None:
                                console.log(f"Error playing audio: {err}")
                                self.bot.loop.create_task(
                                    message.add_reaction("\N{speaker with cancellation stroke}")
                                )
                            else:
                                self.bot.loop.create_task(
                                    message.remove_reaction("\N{speaker with three sound waves}", self.bot.user)
                                )
                                self.bot.loop.create_task(
                                    message.add_reaction("\N{speaker}")
                                )

                        # noinspection PyTypeChecker
                        src = discord.FFmpegPCMAudio(str(_file.resolve()), stderr=subprocess.DEVNULL)
                        src = discord.PCMVolumeTransformer(src, volume=0.5)
                        voice.play(
                            src,
                            after=after
                        )
                        if message.channel.permissions_for(message.guild.me).add_reactions:
                            await message.add_reaction("\N{speaker with three sound waves}")
                else:
                    await message.channel.trigger_typing()
                    await message.reply(file=discord.File(_file))
            return internal

        async def send_smeg():
            directory = Path.cwd() / "assets" / "smeg"
            if directory:
                choice = random.choice(list(directory.iterdir()))
                _file = discord.File(
                    choice,
                    filename="%s.%s" % (os.urandom(32).hex(), choice.suffix)
                )
                await message.reply(file=_file, delete_after=60)

        async def send_what():
            msg = message.reference.cached_message
            if not msg:
                try:
                    msg = await message.channel.fetch_message(message.reference.message_id)
                except discord.HTTPException:
                    return

            if msg.content.count(f"{self.bot.user.mention} said ") >= 2:
                await message.reply("You really are deaf, aren't you.")
            elif not msg.content:
                await message.reply(
                    "Maybe *I* need to get my hearing checked, I have no idea what {} said.".format(
                        msg.author.mention
                    )
                )
            else:
                text = "{0.author.mention} said '{0.content}', you deaf sod.".format(
                    msg
                )
                _content = textwrap.shorten(
                    text, width=2000, placeholder="[...]"
                )
                await message.reply(_content, allowed_mentions=discord.AllowedMentions.none())

        async def send_fuck_you() -> str:
            student = await get_or_none(AccessTokens, user_id=message.author.id)
            if student.ip_info is None or student.expires >= discord.utils.utcnow().timestamp():
                if OAUTH_REDIRECT_URI:
                    return f"Let me see who you are, and then we'll talk... <{OAUTH_REDIRECT_URI}>"
                else:
                    return "I literally don't even know who you are..."
            else:
                ip = student.ip_info
                is_proxy = ip.get("proxy")
                if is_proxy is None:
                    is_proxy = "?"
                else:
                    is_proxy = "\N{WHITE HEAVY CHECK MARK}" if is_proxy else "\N{CROSS MARK}"

                is_hosting = ip.get("hosting")
                if is_hosting is None:
                    is_hosting = "?"
                else:
                    is_hosting = "\N{WHITE HEAVY CHECK MARK}" if is_hosting else "\N{CROSS MARK}"

                return (
                    "Nice argument, however,\n"
                    "IP: {0[query]}\n"
                    "ISP: {0[isp]}\n"
                    "Latitude: {0[lat]}\n"
                    "Longitude: {0[lon]}\n"
                    "Proxy server: {1}\n"
                    "VPS (or other hosting) provider: {2}\n\n"
                    "\N{smiling face with sunglasses}".format(
                        ip,
                        is_proxy,
                        is_hosting
                    )
                )

        if not message.guild:
            return

        if message.channel.name == "femboy-hole":
            payload = {
                "author": message.author.name,
                "avatar": message.author.display_avatar.with_format("webp").with_size(1024).url,
                "content": message.content,
                "at": message.created_at.timestamp(),
                "attachments": [
                    {
                        "url": a.url,
                        "filename": a.filename,
                        "size": a.size,
                        "width": a.width,
                        "height": a.height,
                        "content_type": a.content_type,
                    }
                    for a in message.attachments
                ]
            }
            if message.author.discriminator != "0":
                payload["author"] += '#%s' % message.author.discriminator
            if message.author != self.bot.user and (payload["content"] or payload["attachments"]):
                await self.bot.bridge_queue.put(payload)

        if message.channel.name == "pinboard":
            if message.type == discord.MessageType.pins_add:
                await message.delete(delay=0.01)
            else:
                try:
                    await message.pin(reason="Automatic pinboard pinning")
                except discord.HTTPException as e:
                    return await message.reply(f"Failed to auto-pin: {e}", delete_after=10)
        elif message.channel.name in ("verify", "timetable") and message.author != self.bot.user:
            if message.channel.permissions_for(message.guild.me).manage_messages:
                await message.delete(delay=1)

        else:
            assets = Path.cwd() / "assets"
            responses: Dict[str | tuple, Dict[str, Any]] = {
                r"ferdi": {
                    "content": "https://ferdi-is.gay/",
                    "delete_after": 15,
                },
                r"\bbee(s)*\b": {
                    "content": "https://ferdi-is.gay/bee",
                },
                r"it just works": {
                    "func": play_voice(assets / "it-just-works.ogg"),
                    "meta": {
                        "check": (assets / "it-just-works.ogg").exists
                    }
                },
                r"^linux$": {
                    "content": lambda: (assets / "copypasta.txt").read_text(),
                    "meta": {
                        "needs_mention": True,
                        "check": (assets / "copypasta.txt").exists
                    }
                },
                r"carat": {
                    "file": discord.File(assets / "carat.jpg"),
                    "delete_after": None,
                    "meta": {
                        "check": (assets / "carat.jpg").exists
                    }
                },
                r"(lupupa|fuck(ed)? the hell out\W*)": {
                    "file": discord.File(assets / "lupupa.jpg"),
                    "meta": {
                        "check": (assets / "lupupa.jpg").exists
                    }
                },
                r"[s5]+(m)+[e3]+[g9]+": {
                    "func": send_smeg,
                    "meta": {
                        "sub": {
                            r"pattern": r"([-_.\s\u200b])+",
                            r"with": ''
                        },
                        "check": (assets / "smeg").exists
                    }
                },
                r"(what|huh)(\?|!)*$": {
                    "func": send_what,
                    "meta": {
                        "check": lambda: message.reference is not None
                    }
                },
                ("year", "linux", "desktop"): {
                    "content": lambda: "%s will be the year of the GNU+Linux desktop." % datetime.now().year,
                    "delete_after": None
                },
                r"fuck you(\W)*": {
                    "content": send_fuck_you,
                    "meta": {
                        "check": lambda: message.content.startswith(self.bot.user.mention)
                    }
                },
                r"mine(ing|d)? (diamonds|away)": {
                    "func": play_voice(assets / "mine-diamonds.opus"),
                    "meta": {
                        "check": (assets / "mine-diamonds.opus").exists
                    }
                },
                r"v[ei]r[mg]in(\sme(d|m[a]?)ia\W*)?(\W\w*\W*)?$": {
                    "content": "Get virgin'd",
                    "file": lambda: discord.File(
                        random.choice(list(Path(assets / 'virgin').iterdir()))
                    ),
                    "meta": {
                        "check": (assets / 'virgin').exists
                    }
                },
                r"richard|(dick\W*$)": {
                    "file": discord.File(assets / "visio.png"),
                    "meta": {
                        "check": (assets / "visio.png").exists
                    }
                },
                r"thank(\syou|s)(,)? jimmy": {
                    "content": "You're welcome, %s!" % message.author.mention,
                },
                r"(ok )?jimmy (we|i) g[eo]t it": {
                    "content": "No need to be so rude! Cunt.",
                },
                r"c(mon|ome on) jimmy": {
                    "content": "IM TRYING"
                },
                r"(bor(r)?is|johnson)": {
                    "file": discord.File(assets / "boris.jpeg")
                }
            }
            # Stop responding to any bots
            if message.author.bot is True:
                return

            # Only respond if the message has content...
            if message.content and message.channel.can_send(discord.Embed, discord.File):
                for key, data in responses.items():
                    meta = data.pop("meta", {})
                    if meta.get("needs_mention"):
                        if not self.bot.user.mention not in message.mentions:
                            continue

                    if meta.get("check"):
                        try:
                            okay = meta["check"]()
                        except (Exception, RuntimeError):
                            traceback.print_exc()
                            okay = False

                        if not okay:
                            continue
                    elif meta.get("checks") and isinstance(meta["checks"], list):
                        for check in meta["checks"]:
                            try:
                                okay = check()
                            except (Exception, RuntimeError):
                                traceback.print_exc()
                                okay = False

                            if not okay:
                                break
                        else:
                            continue

                    if meta.get("sub") is not None and isinstance(meta["sub"], dict):
                        content = re.sub(
                            meta["sub"]["pattern"],
                            meta["sub"]["with"],
                            message.content
                        )
                    else:
                        content = message.content

                    if isinstance(key, str):
                        regex = re.compile(key, re.IGNORECASE)
                        if not regex.search(content):
                            continue
                    elif isinstance(key, tuple):
                        if not all(k in content for k in key):
                            continue

                    if "func" in data:
                        try:
                            if inspect.iscoroutinefunction(data["func"]) or inspect.iscoroutine(data["func"]):
                                await data["func"]()
                                break
                            else:
                                data["func"]()
                                break
                        except (Exception, RuntimeError):
                            traceback.print_exc()
                            continue
                    else:
                        for k, v in data.copy().items():
                            if inspect.iscoroutinefunction(data[k]) or inspect.iscoroutine(data[k]):
                                data[k] = await v()
                            elif callable(v):
                                data[k] = v()
                        data.setdefault("delete_after", 30)
                        await message.channel.trigger_typing()
                        await message.reply(**data)
                        break

                await self.process_message_for_github_links(message)

                T_EMOJI = "\U0001f3f3\U0000fe0f\U0000200d\U000026a7\U0000fe0f"
                G_EMOJI = "\U0001f3f3\U0000fe0f\U0000200d\U0001f308"
                N_EMOJI = "\U0001f922"
                C_EMOJI = "\U0000271d\U0000fe0f"
                reactions = {
                    r"mpreg|lupupa|\U0001fac3": "\U0001fac3",  # mpreg
                    r"(trans(gender)?($|\W+)|%s)" % T_EMOJI: T_EMOJI,  # trans
                    r"gay|%s" % G_EMOJI: G_EMOJI,
                    r"(femboy|trans(gender)?($|\W+))": C_EMOJI
                }
                if message.channel.permissions_for(message.guild.me).add_reactions:
                    is_naus = random.randint(1, 100) == 32
                    for key, value in reactions.items():
                        if re.search(key, message.content, re.IGNORECASE):
                            await message.add_reaction(value)

                    if is_naus:
                        await message.add_reaction(N_EMOJI)

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
        headers = {
            "User-Agent": f"python-httpx/{httpx.__version__} (Like Akregator/5.22.3); syndication"
        }

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
            console.log("Failed to fetch discord atom feed:", e)
            return

        if response.status_code == 304:
            return

        if response.status_code != 200:
            console.log("Failed to fetch discord atom feed:", response.status_code)
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
                    "Resolved": discord.Color.green(),
                    "Investigating": discord.Color.dark_orange(),
                    "Identified": discord.Color.orange(),
                    "Monitoring": discord.Color.blurple(),
                }

                colour = _status.get(content.splitlines()[1].split(" - ")[0], discord.Color.greyple())

                if len(content) > 4096:
                    content = f"[open on discordstatus.com (too large to display)]({entry.link['href']})"

                embed = discord.Embed(
                    title=title,
                    description=content,
                    color=colour,
                    url=entry.link["href"],
                    timestamp=updated
                )
                embed.set_author(
                    name="Discord Status",
                    url="https://discordstatus.com/",
                    icon_url="https://raw.githubusercontent.com/EEKIM10/LCC-bot/"
                             "fe0cb6dd932f9fc2cb0a26433aff8e4cce19279a/assets/discord.png"
                )
                embed.set_footer(
                    text="Published: {} | Updated: {}".format(
                        datetime.fromisoformat(entry.find("published").text).strftime("%Y-%m-%d %H:%M:%S"),
                        updated.strftime("%Y-%m-%d %H:%M:%S")
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
