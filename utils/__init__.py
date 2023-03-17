from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse

import discord
from discord.ext import commands

from ._email import *
from .db import *
from .console import *
from .views import *

if TYPE_CHECKING:
    from datetime import datetime


class JimmyBanException(discord.CheckFailure):
    def __init__(self, until: "datetime", reason: str):
        super().__init__(reason)
        self.until = until
        self.reason = reason

    def __str__(self):
        ok = discord.utils.format_dt(self.until, "R")
        return (
            f"\N{cross mark} You are not allowed to use commands right now. You will be unbanned {ok}.\n"
            f"Ban reason:\n>>> {self.reason}"
        )

    def __repr__(self):
        return f"<JimmyBanException until={self.until!r} reason={self.reason!r}>"


def simple_embed_paginator(
    lines: list[str], *, assert_ten: bool = False, empty_is_none: bool = True, **kwargs
) -> Optional[list[discord.Embed]]:
    """Paginates x lines into x embeds."""
    if not lines and empty_is_none is True:
        return

    kwargs.setdefault("description", "")
    embeds = [discord.Embed(**kwargs)]
    for line in lines:
        embed = embeds[-1]
        total_length = len(embed)
        description_length = len(embed.description)
        if total_length + len(line) > 6000 or description_length + len(line) > 4096:
            embed = discord.Embed(**kwargs)
            embed.description += line + "\n"
            embeds.append(embed)
        else:
            embed.description += line + "\n"

    if assert_ten:
        assert len(embeds) <= 10, "Too many embeds."
    return embeds


def hyperlink(url: str, *, text: str = None, max_length: int = None) -> str:
    if max_length < len(url):
        raise ValueError(f"Max length ({max_length}) is too low for provided URL ({len(url)}). Hyperlink impossible.")

    fmt = "[{}]({})"
    if text:
        fmt = fmt.format(text, url)
    else:
        parsed = urlparse(url)
        fmt = fmt.format(parsed.hostname, url)

    if len(fmt) > max_length:
        return url
    return fmt


def owner_or_admin():
    async def predicate(ctx: commands.Context):
        if ctx.author.guild_permissions.administrator or await ctx.bot.is_owner(ctx.author):
            return True
        raise commands.MissingPermissions(["administrator"])

    return commands.check(predicate)
