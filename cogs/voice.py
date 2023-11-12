import asyncio
import functools
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import discord
import httpx
import yt_dlp
from discord.ext import commands


class TransparentQueue(asyncio.Queue):
    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize)
        self._internal_queue = []

    async def put(self, item):
        await super().put(item)
        self._internal_queue.append(item)

    def task_done(self):
        super().task_done()
        self._internal_queue.pop(0)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source: discord.AudioSource, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get("title")
        self.url = data.get("url")

    @property
    def duration(self):
        return self.data.get("duration")

    @classmethod
    async def from_url(cls, ytdl: yt_dlp.YoutubeDL, url, *, loop=None, stream=False):
        ffmpeg_options = {"options": "-vn -b:a 44.1k"}
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if "entries" in data:
            if not data["entries"]:
                # Empty playlist
                return None
            # Takes the first item from a playlist
            data = data["entries"][0]

        filename = data["url"] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)


class VoiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = Path(tempfile.mkdtemp("jimmy-voice-cache-")).resolve()
        self.cache.mkdir(exist_ok=True, parents=True)
        self.ytdl_options = {
            "format": "bestaudio/best",
            "restrictfilenames": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "logtostderr": False,
            "default_search": "auto",
            "outtmpl": "%(title)s.%(ext)s".format(self.cache),
            "keepvideo": False,
            "cachedir": str(self.cache),
            "paths": {
                "home": str(self.cache),
                "temp": str(self.cache),
            },
        }
        self.yt_dl = yt_dlp.YoutubeDL(self.ytdl_options)
        self.queue = TransparentQueue(100)
        self._queue_task = self.bot.loop.create_task(self.queue_task())
        self.song_done = asyncio.Event()

    async def queue_task(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()
        while True:
            ctx, player, inserted_at = await self.queue.get()
            if not ctx.guild.voice_client:
                # no longer playing. clear queue
                del self.queue
                self.queue = TransparentQueue(100)
                continue
            ctx.guild.voice_client.play(player, after=self.after_player(ctx))
            self.song_done.clear()

            embed = discord.Embed(
                description=f"Now playing: [{player.title}]({player.url}), as requested by {ctx.author.mention}, "
                f"{discord.utils.format_dt(inserted_at, 'R')}.",
                color=discord.Color.green(),
            )
            try:
                await ctx.guild.voice_client.channel.send(
                    ctx.author.mention,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                try:
                    await ctx.respond(embed=embed)
                except discord.HTTPException:
                    pass
            self.queue.task_done()
            await self.song_done.wait()

    def cog_unload(self):
        for task in (self._queue_task,):
            task.cancel()
        shutil.rmtree(self.cache)

    def after_player(self, ctx: discord.ApplicationContext):
        def after(e):
            self.song_done.set()
            if e:
                self.bot.loop.create_task(ctx.respond(f"An error occurred while playing the audio: {e}"))

        return after

    async def unblock(self, func, *args, **kwargs):
        call = functools.partial(func, *args, **kwargs)
        return await self.bot.loop.run_in_executor(None, call)

    @commands.slash_command(name="play")
    async def stream(
        self,
        ctx: discord.ApplicationContext,
        url: str,
        volume: float = 100,
    ):
        """Streams a URL using yt-dl"""
        if not ctx.user.voice:
            await ctx.respond("You are not connected to a voice channel.")
            return

        player = await YTDLSource.from_url(self.yt_dl, url, loop=self.bot.loop, stream=True)
        if not player:
            await ctx.respond("Could not extract any audio from the given URL.")

        player.volume = volume / 100
        await self.queue.put((ctx, player, discord.utils.utcnow()))
        embed = discord.Embed(
            description=f"Added [{player.title}]({player.url}) to the queue.",
        )
        await ctx.respond(embed=embed)

    @commands.command(hidden=True)
    async def play(self, ctx: commands.Context, url: str, volume: float = 100):
        """Plays a song by downloading it first."""
        if not ctx.author.voice:
            await ctx.reply("You are not connected to a voice channel.")
            return

        player = await YTDLSource.from_url(self.yt_dl, url, loop=self.bot.loop)
        if not player:
            await ctx.reply("Could not extract any audio from the given URL.")

        player.volume = volume / 100
        await self.queue.put((ctx, player, discord.utils.utcnow()))
        embed = discord.Embed(
            description=f"Added [{player.title}]({player.url}) to the queue.",
        )
        await ctx.reply(embed=embed)

    @commands.slash_command(name="queue")
    async def view_queue(self, ctx: discord.ApplicationContext):
        """Views the current queue"""
        if not ctx.guild.voice_client:
            await ctx.respond("Not connected to a voice channel.")
            return

        if not self.queue._internal_queue and not ctx.guild.voice_client.is_playing():
            await ctx.respond("The queue is empty.")
            return

        embed = discord.Embed(
            title="Queue",
            description="\n".join(
                f"{i+1}. [{x[1].title}]({x[1].url})" for i, x in enumerate(self.queue._internal_queue)
            ),
        )
        if ctx.guild.voice_client.is_playing():
            now_playing = ctx.guild.voice_client.source
            embed.add_field(
                name="Now Playing",
                value=f"[{now_playing.title}]({now_playing.url})",
            )
        await ctx.respond(embed=embed)

    @commands.slash_command(name="volume")
    async def volume(self, ctx: discord.ApplicationContext, volume: float):
        """Changes the player's volume"""
        if not 0 < volume < 101:
            await ctx.respond("Volume must be between 1 and 100.")
            return

        ctx.guild.voice_client.source.volume = volume / 100
        await ctx.respond(f"Changed volume to {volume}%")

    @commands.slash_command(name="stop")
    async def stop(self, ctx: discord.ApplicationContext):
        """Stops and disconnects the bot from voice"""
        if not ctx.guild.voice_client:
            await ctx.respond("Not connected to a voice channel.")
            return

        if ctx.voice_client:
            if ctx.voice_client.is_playing():
                ctx.voice_client.stop()
            await ctx.voice_client.disconnect(force=True)
            await ctx.respond("Disconnected from voice channel.")
        else:
            await ctx.respond("Not connected to a voice channel.")

    @commands.slash_command(name="skip")
    async def skip(self, ctx: discord.ApplicationContext):
        """Skips the current song"""

        class VoteSkipDialog(discord.ui.View):
            def __init__(self):
                super().__init__()
                self.voted = []

            @discord.ui.button(label="Skip", style=discord.ButtonStyle.green)
            async def _skip(self, button: discord.ui.Button, interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                humans = len(ctx.guild.voice_client.channel.members) - len(
                    [x for x in ctx.guild.voice_client.channel.members if x.bot]
                )
                target = humans / 2 if humans > 2 else humans
                if interaction.user.id not in self.voted:
                    self.voted.append(interaction.user.id)
                    if humans <= 1 or target <= 1:
                        ctx.voice_client.stop()
                        self.stop()
                        self.disable_all_items()
                        return await interaction.edit_original_response(view=self, content="Skipped song.")

                    if len(self.voted) >= target:
                        ctx.voice_client.stop()
                        self.stop()
                        self.disable_all_items()
                        return await interaction.edit_original_response(view=self, content="Skipped song.")
                    else:
                        await ctx.respond(
                            f"Voted to skip. %d/%d" % (len(self.voted), target),
                        )
                else:
                    await ctx.respond("You have already voted to skip.")

                self.disable_all_items()
                await interaction.edit_original_response(
                    view=self, content="Vote skip (%d/%d)." % (len(self.voted), target)
                )

        if not ctx.guild.voice_client:
            await ctx.respond("Not connected to a voice channel.")
            return

        if ctx.voice_client.is_playing():
            _humans = len(ctx.guild.voice_client.channel.members) - len(
                [x for x in ctx.guild.voice_client.channel.members if x.bot]
            )
            if _humans > 1:
                _target = _humans / 2 if _humans > 2 else _humans
                diag = VoteSkipDialog()
                diag.voted.append(ctx.user.id)
                await ctx.respond("Vote skip (1/%d)." % _target, view=VoteSkipDialog())
            ctx.voice_client.stop()
            self.song_done.set()
            await ctx.respond("Skipped song.")
        else:
            await ctx.respond("Not playing any music.")

    @commands.command(name="dump-metadata")
    async def dump_metadata(self, ctx: commands.Context, url: str, traverse: str = None):
        """Dumps JSON YT-DLP metadata to a file"""
        content = None
        _params = self.yt_dl.params.copy()
        _params.pop("noplaylist")
        _params.pop("logtostderr")
        _ytdl = yt_dlp.YoutubeDL(_params)
        async with ctx.channel.typing():
            file = io.StringIO()
            data = await self.unblock(_ytdl.extract_info, url) or {}
            data = await self.unblock(_ytdl.sanitize_info, data, remove_private_keys=True)
            if traverse:
                last_key = []
                for key in traverse.split("."):
                    if key in data:
                        data = data[key]
                        last_key.append(key)
                    else:
                        content = "Key %r not found in metadata (got as far as %r)." % (key, ".".join(last_key))
                        break
            json.dump(data, file, indent=4)
            file.seek(0)
        # noinspection PyTypeChecker
        return await ctx.reply(content, file=discord.File(file, filename="metadata.json"))

    @volume.before_invoke
    @play.before_invoke
    @stop.before_invoke
    @stream.before_invoke
    async def before_invoke(self, ctx: discord.ApplicationContext | commands.Context):
        if isinstance(ctx, discord.ApplicationContext):
            await ctx.defer()
            sender = ctx.respond
        else:
            sender = ctx.reply
        if not self.cache.exists():
            self.cache.mkdir()

        if not ctx.guild.voice_client:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await sender("You are not connected to a voice channel.")
                raise commands.CommandError("User not connected to a voice channel.")

    @commands.slash_command(name="boost-audio")
    async def boost_audio(
        self,
        ctx: discord.ApplicationContext,
        file: discord.Attachment,
        level: discord.Option(
            float, "A level (in percentage) of volume (e.g. 150 = 150%)", min_value=0.1, max_value=999.99
        ),
    ):
        """Boosts an audio file's audio level."""
        await ctx.defer()
        if file.size >= (25 * 1024 * 1024):
            return await ctx.respond("File is too large (25MB Max).")

        with tempfile.TemporaryDirectory("jimmy-audio-boost-") as temp_dir_raw:
            temp_dir = Path(temp_dir_raw).resolve()
            _input = temp_dir / file.filename
            output = _input.with_name(_input.name + "-processed.ogg")
            await file.save(_input)

            proc: subprocess.CompletedProcess = await self.bot.loop.run_in_executor(
                None,
                functools.partial(
                    subprocess.run,
                    (
                        "ffmpeg",
                        "-hide_banner",
                        "-i",
                        str(_input),
                        "-c:a libopus",
                        "-b:a 64k",
                        "-af volume=%.2f" % (level / 100),
                        "-vn",
                        str(output),
                    ),
                    capture_output=True,
                ),
            )
            if proc.returncode == 0:
                if output.stat().st_size >= (25 * 1024 * 1024) + len(output.name):
                    return await ctx.respond("I'd love to serve you your boosted file, but its too large.")
                return await ctx.respond(file=discord.File(output))
            else:
                data = {
                    "files": [
                        {"content": proc.stderr.decode() or "empty", "filename": "stderr.txt"},
                        {"content": proc.stdout.decode() or "empty", "filename": "stdout.txt"},
                    ]
                }
                response = await httpx.AsyncClient().put("https://api.mystb.in/paste", json=data)
                if response.status_code == 201:
                    data = response.json()
                    key = "https://mystb.in/" + data["id"]
                else:
                    key = "https://www.youtube.com/watch?v=dgha9S39Y6M&status_code=%d" % response.status_code
                await ctx.respond("Failed ([exit code %d](%s))" % (proc.returncode, key))
                await ctx.edit(embed=None)


def setup(bot):
    bot.add_cog(VoiceCog(bot))
