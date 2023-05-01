import shutil
import asyncio
import discord
import yt_dlp
import tempfile
from pathlib import Path
from discord.ext import commands


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source: discord.AudioSource, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get("title")
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, ytdl: yt_dlp.YoutubeDL, url, *, loop=None, stream=False):
        ffmpeg_options = {"options": "-vn -b:a 44.1k"}
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=not stream)
        )

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
        self.ytdl_options = {
            "format": "bestaudio/best",
            "outtmpl": "%(title)s.%(ext)s",
            "restrictfilenames": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "logtostderr": False,
            "default_search": "auto",
        }
        self.cache = Path(tempfile.mkdtemp("jimmy-voice-cache-")).resolve()
        self.yt_dl = yt_dlp.YoutubeDL(self.ytdl_options)

    def cog_unload(self):
        shutil.rmtree(self.cache)

    def after_player(self, ctx: discord.ApplicationContext):
        def after(e):
            if e:
                self.bot.loop.create_task(
                    ctx.respond(
                        f"An error occurred while playing the audio: {e}"
                    )
                )
        return after

    async def unblock(self, func, *args, **kwargs):
        return await self.bot.loop.run_in_executor(None, func, *args, **kwargs)

    @commands.slash_command(name="play")
    async def play(self, ctx: discord.ApplicationContext, url: str, volume: float = 50):
        """Streams a URL using yt-dl"""
        if not ctx.user.voice:
            await ctx.respond("You are not connected to a voice channel.")
            return

        if ctx.voice_client.is_playing():
            await ctx.respond("Already playing audio. Use %s first." % self.bot.get_application_command("stop").mention)
            return

        player = await YTDLSource.from_url(self.yt_dl, url, loop=self.bot.loop, stream=True)
        if not player:
            await ctx.respond("Could not extract any audio from the given URL.")
        ctx.guild.voice_client.play(player, after=self.after_player(ctx))
        ctx.guild.voice_client.source.volume = min(100.0, max(1.0, volume / 100))
        embed = discord.Embed(
            description=f"Playing [{player.title}]({player.url})",
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
                # members = ctx.voice_client.channel.members
                # bots = [m for m in members if m.bot]
                # if len(bots) == len(members):
                #     pass
                # else:
                #     humans = len(members) - len(bots)
                #     if humans > 1:
                #
                ctx.voice_client.stop()
            await ctx.voice_client.disconnect(force=True)
            await ctx.respond("Disconnected from voice channel.")
        else:
            await ctx.respond("Not connected to a voice channel.")

    async def cog_before_invoke(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not self.cache.exists():
            self.cache.mkdir()

        if not ctx.guild.voice_client:
            if ctx.user.voice:
                await ctx.user.voice.channel.connect()
            else:
                await ctx.respond("You are not connected to a voice channel.")
                raise commands.CommandError("User not connected to a voice channel.")


def setup(bot):
    bot.add_cog(VoiceCog(bot))
