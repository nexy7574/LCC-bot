import asyncio
import pathlib
import re
import typing

import aiosqlite

import discord
from discord.ext import commands


class McDataBase:
    def __init__(self):
        self.db = pathlib.Path.home() / ".cache" / "lcc-bot" / "McDataBase.db"
        self._conn: typing.Optional[aiosqlite.Connection] = None

    async def init_db(self):
        if self._conn:
            conn = self._conn
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS breaks (
                    user_id INTEGER PRIMARY KEY,
                    since FLOAT NOT NULL
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cooldowns (
                    user_id INTEGER PRIMARY KEY,
                    expires FLOAT NOT NULL
                );
                """
            )

    async def get_break(self, user_id: int) -> typing.Optional[tuple[float]]:
        async with self._conn.execute(
            """
            SELECT since FROM breaks WHERE user_id = ?;
            """,
            (user_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def set_break(self, user_id: int, since: float) -> None:
        await self._conn.execute(
            """
            INSERT INTO breaks (user_id, since) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET since = excluded.since
            """,
            (user_id, since)
        )

    async def remove_break(self, user_id: int) -> None:
        now = discord.utils.utcnow().timestamp()
        await self._conn.execute(
            """
            DELETE FROM breaks WHERE user_id = ?;
            """,
            (user_id,)
        )
        await self.set_cooldown(user_id, now)

    async def get_cooldown(self, user_id: int) -> typing.Optional[tuple[float]]:
        async with self._conn.execute(
            """
            SELECT expires FROM cooldowns WHERE user_id = ?;
            """,
            (user_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def set_cooldown(self, user_id: int, expires: float) -> None:
        await self._conn.execute(
            """
            INSERT INTO cooldowns (user_id, expires) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET expires = excluded.expires;
            """,
            (user_id, expires)
        )

    async def remove_cooldown(self, user_id: int) -> None:
        await self._conn.execute(
            """
            DELETE FROM cooldowns WHERE user_id = ?;
            """,
            (user_id,)
        )

    async def __aenter__(self) -> "McDataBase":
        self._conn = await aiosqlite.connect(self.db)
        await self.init_db()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._conn.commit()
        await self._conn.close()
        self._conn = None


class McDonaldsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        me = message.guild.me if message.guild else self.bot.user
        if not message.channel.permissions_for(me).manage_messages:
            return

        async with self.lock:
            NIGHTMARE_REGEX = re.compile(r"(\|\|.+\|\|)?(?P<username>[a-zA-Z0-9]{2,32}).*")
            if m := NIGHTMARE_REGEX.match(message.content):
                username = m.group(1)
                member = discord.utils.get(message.guild.members, name=username)
                if member:
                    author = member

            async with McDataBase() as db:
                if (last_info := await db.get_break(author.id)) is not None:
                    if message.content.upper() != "MCDONALDS!":
                        await message.delete()
                        if (message.created_at.timestamp() - last_info[0]) > 10:
                            await message.channel.send(
                                f"{message.author.mention} Please say `MCDONALDS!` to end commercial.",
                                delete_after=30
                            )
                            await db.set_break(author.id, message.created_at.timestamp())
                    elif message.author.bot is False:
                        await db.remove_break(author.id)
                        await message.reply(
                            "Thank you. You may now resume your activity.",
                            delete_after=120
                        )

    @commands.user_command(name="Commercial Break")
    @commands.cooldown(2, 60, commands.BucketType.member)
    async def commercial_break(self, ctx: discord.ApplicationContext, member: discord.Member):
        await ctx.defer(ephemeral=True)

        if not ctx.channel.permissions_for(ctx.me).manage_messages:
            return await ctx.respond("I don't have permission to manage messages in this channel.", ephemeral=True)

        if member.bot or member == ctx.user:
            return await ctx.respond("No.", ephemeral=True)

        async with McDataBase() as db:
            if await db.get_break(member.id) is not None:
                await ctx.respond(f"{member.mention} is already in a commercial break.")
                return
            elif (cooldown := await db.get_cooldown(member.id)) is not None:
                expires = cooldown[0] + 300
                if expires > discord.utils.utcnow().timestamp():
                    await ctx.respond(
                        f"{member.mention} is not due another ad break yet. Their next commercial break will start "
                        f"<t:{int(expires)}:R> at the earliest."
                    )
                    return
                else:
                    await db.remove_cooldown(member.id)

            await db.set_break(member.id, discord.utils.utcnow().timestamp())
            await ctx.send(
                f"{member.mention} Commercial break! Please say `MCDONALDS!` to end commercial.\n"
                f"*This commercial break is sponsored by {ctx.user.mention}.*",
                delete_after=300,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False)
            )
            await ctx.respond("Commercial break started.", ephemeral=True)
            await ctx.delete(delay=120)

    @commands.command(name="commercial-break")
    @commands.is_owner()
    async def _force_com_break(self, ctx: commands.Context, *, member: discord.Member):
        """Forces a member to go on commercial break."""
        async with McDataBase() as db:
            await db.set_break(member.id, discord.utils.utcnow().timestamp())
            await ctx.reply(
                f"{member.mention} Commercial break! Please say `MCDONALDS!` to end commercial.\n"
                f"*This commercial break is sponsored by {ctx.author.mention}.*",
                delete_after=300,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False)
            )
            await ctx.message.delete(delay=120)

    @commands.command(name="end-break")
    @commands.is_owner()
    async def _unforce_com_break(self, ctx: commands.Context, *, member: discord.Member):
        """Forces a member to finish their commercial break."""
        async with McDataBase() as db:
            await db.remove_break(member.id)
            await ctx.reply(f"{member.mention} Commercial break ended.", delete_after=10)


def setup(bot):
    bot.add_cog(McDonaldsCog(bot))
