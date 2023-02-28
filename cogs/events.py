import random
import textwrap
from pathlib import Path
from typing import Optional, Tuple
import discord
from discord.ext import commands
from utils import Student, get_or_none, console
from config import guilds
try:
    from config import OAUTH_REDIRECT_URI
except ImportError:
    OAUTH_REDIRECT_URI = None


LTR = "\N{black rightwards arrow}\U0000fe0f"
RTL = "\N{leftwards black arrow}\U0000fe0f"


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
            if message.author.id == self.bot.user.id:
                if payload.emoji.name == "\N{wastebasket}\U0000fe0f":
                    await message.delete(delay=0.5)

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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return

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
            # Respond to shronk bot
            if message.author.id == 1063875884274163732 and message.channel.can_send():
                if "pissylicious 💦💦" in message.content:
                    from dns import asyncresolver
                    import httpx
                    response = await asyncresolver.resolve("shronkservz.tk", "A")
                    ip_info_response = await httpx.get(f"http://ip-api.com/json/{response[0].address}")
                    if ip_info_response.status_code == 200:
                        return await message.reply(
                            f"Scattylicious\N{pile of poo}\N{pile of poo}\n"
                            "IP: {0[query]}\n"
                            "ISP: {0[isp]}\n"
                            "Latitude: {0[lat]}\n"
                            "Longitude: {0[lon]}\n".format(
                                ip,
                            )
                        )
                RESPONSES = {
                    "Congratulations!!": "Shut up SHRoNK Bot, nobody loves you.",
                    "You run on a Raspberry Pi... I run on a real server": "At least my server gets action, while yours just sits and collects dust!"
                }
                for k, v in RESPONSES.items():
                    if k in message.content:
                        await message.reply(v, delete_after=10)
                        break
            # Stop responding to any bots
            if message.author.bot is True:
                return
            # Only respond if the message has content...
            if message.content:
                if message.channel.can_send():  # ... and we can send messages
                    if "linux" in message.content.lower() and self.bot.user in message.mentions:
                        console.log(f"Responding to {message.author} with linux copypasta")
                        try:
                            with open("./copypasta.txt", "r") as f:
                                await message.reply(f.read())
                        except FileNotFoundError:
                            await message.reply(
                                "I'd just like to interject for a moment. What you're referring to as Linux, "
                                "is in fact, uh... I don't know, I forgot."
                            )
                    if "carat" in message.content.lower():
                        file = discord.File(Path(__file__).parent.parent / "carat.jpg")
                        await message.reply(file=file)
                    if message.reference is not None and message.reference.cached_message is not None:
                        if message.content.lower().strip() in ("what", "what?"):
                            text = "{0.author.mention} said %r, you deaf sod.".format(
                                message.reference.cached_message
                            )
                            _content = textwrap.shorten(
                                text % message.reference.cached_message.content, width=2000, placeholder="[...]"
                            )
                            await message.reply(_content)
                if message.channel.permissions_for(message.guild.me).add_reactions:
                    if "mpreg" in message.content.lower() or "\U0001fac3" in message.content.lower():
                        try:
                            await message.add_reaction("\U0001fac3")
                        except discord.HTTPException as e:
                            console.log("Failed to add mpreg reaction:", e)
                    if "lupupa" in message.content.lower():
                        try:
                            await message.add_reaction("\U0001fac3")
                        except discord.HTTPException as e:
                            console.log("Failed to add mpreg reaction:", e)

                    is_naus = random.randint(1, 100) == 32
                    if self.bot.user in message.mentions or message.channel.id == 1032974266527907901 or is_naus:
                        T_EMOJI = "\U0001f3f3\U0000fe0f\U0000200d\U000026a7\U0000fe0f"
                        G_EMOJI = "\U0001f3f3\U0000fe0f\U0000200d\U0001f308"
                        N_EMOJI = "\U0001f922"
                        C_EMOJI = "\U0000271d\U0000fe0f"
                        if any((x in message.content.lower() for x in ("trans", T_EMOJI, "femboy"))) or is_naus:
                            try:
                                await message.add_reaction(N_EMOJI)
                            except discord.HTTPException as e:
                                console.log("Failed to add trans reaction:", e)
                        if "gay" in message.content.lower() or G_EMOJI in message.content.lower():
                            try:
                                await message.add_reaction(C_EMOJI)
                            except discord.HTTPException as e:
                                console.log("Failed to add gay reaction:", e)

            if self.bot.user in message.mentions:
                if message.content.startswith(self.bot.user.mention):
                    if message.content.lower().endswith("bot"):
                        pos, neut, neg, _ = await self.analyse_text(message.content)
                        if pos > neg:
                            embed = discord.Embed(description=":D", color=discord.Color.green())
                            embed.set_footer(
                                text=f"Pos: {pos*100:.2f}% | Neutral: {neut*100:.2f}% | Neg: {neg*100:.2f}%"
                            )
                        elif pos == neg:
                            embed = discord.Embed(description=":|", color=discord.Color.greyple())
                            embed.set_footer(
                                text=f"Pos: {pos * 100:.2f}% | Neutral: {neut * 100:.2f}% | Neg: {neg * 100:.2f}%"
                            )
                        else:
                            embed = discord.Embed(description=":(", color=discord.Color.red())
                            embed.set_footer(
                                text=f"Pos: {pos*100:.2f}% | Neutral: {neut*100:.2f}% | Neg: {neg*100:.2f}%"
                            )
                        return await message.reply(embed=embed)
                    if message.content.lower().endswith(
                        (
                            "when is the year of the linux desktop?",
                            "year of the linux desktop?",
                            "year of the linux desktop",
                        )
                    ):
                        date = discord.utils.utcnow()
                        # date = date.replace(year=date.year + 1)
                        return await message.reply(date.strftime("%Y") + " will be the year of the GNU+Linux desktop.")

                    if message.content.lower().endswith("fuck you"):
                        student = await get_or_none(Student, user_id=message.author.id)
                        if student is None:
                            return await message.reply("You aren't even verified...", delete_after=10)
                        elif student.ip_info is None:
                            if OAUTH_REDIRECT_URI:
                                return await message.reply(
                                    f"Let me see who you are, and then we'll talk... <{OAUTH_REDIRECT_URI}>",
                                    delete_after=30
                                )
                            else:
                                return await message.reply(
                                    "I literally don't even know who you are...",
                                    delete_after=10
                                )
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

                            return await message.reply(
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
                                ),
                                delete_after=30
                            )


def setup(bot):
    bot.add_cog(Events(bot))
