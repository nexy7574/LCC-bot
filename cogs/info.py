import asyncio
import datetime
import os
import sys
import time
from typing import List

import discord
import humanize
import psutil
from functools import partial
from discord.ext import commands
from pathlib import Path
try:
    import fanshim
    import apa102
    import RPi.GPIO as GPIO
except ImportError as e:
    print("Raspberry Pi libraries not found.", e, file=sys.stderr)
    fanshim = GPIO = apa102 = None


class InfoCog(commands.Cog):
    EMOJIS = {
        "CPU": "\N{brain}",
        "RAM": "\N{ram}",
        "SWAP": "\N{swan}",
        "DISK": "\N{minidisc}",
        "NETWORK": "\N{satellite antenna}",
        "SENSORS": "\N{thermometer}",
        "UPTIME": "\N{alarm clock}",
        "ON": "\N{large green circle}",
        "OFF": "\N{large red circle}",
    }

    def __init__(self, bot):
        self.bot = bot

    async def run_subcommand(self, *args: str):
        """Runs a command in a shell in the background, asynchronously, returning status, stdout, and stderr."""
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            loop=self.bot.loop,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")

    async def unblock(self, function: callable, *args, **kwargs):
        """Runs a function in the background, asynchronously, returning the result."""
        return await self.bot.loop.run_in_executor(None, partial(function, *args, **kwargs))

    @staticmethod
    def bar_fill(
            filled: int,
            total: int,
            bar_width: int = 10,
            char: str = "\N{black large square}",
            unfilled_char: str = "\N{white large square}"
    ):
        """Returns a progress bar with the given length and fill character."""
        if filled == 0:
            filled = 0.01
        if total == 0:
            total = 0.01
        percent = filled / total
        to_fill = int(bar_width * percent)
        return f"{char * to_fill}{unfilled_char * (bar_width - to_fill)}"

    @commands.slash_command(name="system-info")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def system_info(self, ctx: discord.ApplicationContext):
        """Gather statistics on the current host."""
        bar_emojis = {
            75: "\N{large yellow square}",
            80: "\N{large orange square}",
            90: "\N{large red square}",
        }
        await ctx.defer()
        root_drive = Path(__file__).root
        temperature = fans = {}
        binary = os.name != "nt"

        # Gather statistics
        start = time.time()
        cpu: List[float] = await self.unblock(psutil.cpu_percent, interval=1.0, percpu=True)
        ram = await self.unblock(psutil.virtual_memory)
        swap = await self.unblock(psutil.swap_memory)
        disk = await self.unblock(psutil.disk_usage, root_drive)
        network = await self.unblock(psutil.net_io_counters)
        if getattr(psutil, "sensors_temperatures", None):
            temperature = await self.unblock(psutil.sensors_temperatures)
        if getattr(psutil, "sensors_fans", None):
            fans = await self.unblock(psutil.sensors_fans)
        uptime = datetime.datetime.fromtimestamp(await self.unblock(psutil.boot_time), datetime.timezone.utc)
        end = time.time()

        embed = discord.Embed(
            title="System Statistics",
            description=f"Collected in {humanize.precisedelta(datetime.timedelta(seconds=end - start))}.",
            color=discord.Color.blurple(),
        )

        # Format statistics
        per_core = "\n".join(f"{i}: {c:.2f}%" for i, c in enumerate(cpu))
        total_cpu = sum(cpu)
        pct = total_cpu / len(cpu)
        cpu_bar_emoji = "\N{large green square}"
        for threshold, emoji in bar_emojis.items():
            if pct >= threshold:
                cpu_bar_emoji = emoji

        bar = self.bar_fill(sum(cpu), len(cpu) * 100, 16, cpu_bar_emoji, "\u2581")
        embed.add_field(
            name=f"{self.EMOJIS['CPU']} CPU",
            value=f"**Usage:** {sum(cpu):.2f}%\n"
                  f"**Cores:** {len(cpu)}\n"
                  f"**Usage Per Core:**\n{per_core}\n"
                  f"{bar}",
            inline=False,
        )
        if "coretemp" in temperature:
            embed.add_field(
                name=f"{self.EMOJIS['SENSORS']} Temperature (coretemp)",
                value="\n".join(f"{s.label}: {s.current:.2f}°C" for s in temperature["coretemp"]),
                inline=True,
            )
        elif "acpitz" in temperature:
            embed.add_field(
                name=f"{self.EMOJIS['SENSORS']} Temperature (acpitz)",
                value="\n".join(f"{s.label}: {s.current:.2f}°C" for s in temperature["acpitz"]),
                inline=True,
            )
        elif "cpu_thermal" in temperature:
            embed.add_field(
                name=f"{self.EMOJIS['SENSORS']} Temperature (cpu_thermal)",
                value="\n".join(f"{s.label}: {s.current:.2f}°C" for s in temperature["cpu_thermal"]),
                inline=True,
            )

        if fans:
            embed.add_field(
                name=f"{self.EMOJIS['SENSORS']} Fans",
                value="\n".join(f"{s.label}: {s.current:.2f} RPM" for s in fans),
                inline=True,
            )
        if fanshim:
            # PiMoroni's fanshim by default uses pin 18 for control
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(18, GPIO.IN)
            fan_active = bool(GPIO.input(18))
            # LED = apa102.APA102(1, 15, 14, None)
            # Get LED colour as a tuple of (r, g, b)
            # LED_colour = LED.get_pixel_colour(0)
            # Convert to hex
            # LED_colour = "%02x%02x%02x" % LED_colour
            LED_colour = "unknown"
            fan_state = f"{self.EMOJIS['OFF']} Inactive"
            if fan_active:
                fan_state = f"{self.EMOJIS['ON']} Active"
            embed.add_field(
                name=f"{self.EMOJIS['SENSORS']} Fan",
                value=f"{fan_state} (LED: #{LED_colour})",
                inline=True,
            )

        embed.add_field(
            name=f"{self.EMOJIS['RAM']} RAM",
            value=f"**Usage:** {ram.percent}%\n"
                  f"**Total:** {humanize.naturalsize(ram.total, binary=binary)}\n"
                  f"**Available:** {humanize.naturalsize(ram.available, binary=binary)}",
            inline=False,
        )
        embed.add_field(
            name=f"{self.EMOJIS['SWAP']} Swap",
            value=f"**Usage:** {swap.percent}%\n"
                  f"**Total:** {humanize.naturalsize(swap.total, binary=binary)}\n"
                  f"**Free:** {humanize.naturalsize(swap.free, binary=binary)}\n"
                  f"**Used:** {humanize.naturalsize(swap.used, binary=binary)}",
            inline=True,
        )

        embed.add_field(
            name=f"{self.EMOJIS['DISK']} Disk ({root_drive})",
            value=f"**Usage:** {disk.percent}%\n"
                  f"**Total:** {humanize.naturalsize(disk.total, binary=binary)}\n"
                  f"**Free:** {humanize.naturalsize(disk.free, binary=binary)}\n"
                  f"**Used:** {humanize.naturalsize(disk.used, binary=binary)}",
            inline=False,
        )
        embed.add_field(
            name=f"{self.EMOJIS['NETWORK']} Network",
            value=f"**Sent:** {humanize.naturalsize(network.bytes_sent, binary=binary)}\n"
                  f"**Received:** {humanize.naturalsize(network.bytes_recv, binary=binary)}",
            inline=True,
        )
        embed.add_field(
            name=f"{self.EMOJIS['UPTIME']} Uptime",
            value=f"Booted {discord.utils.format_dt(uptime, 'R')}"
                  f"({humanize.precisedelta(datetime.datetime.now(datetime.timezone.utc) - uptime)})",
            inline=False,
        )

        await ctx.edit(embed=embed)


def setup(bot):
    bot.add_cog(InfoCog(bot))
