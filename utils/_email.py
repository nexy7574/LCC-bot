import secrets

import discord

import config
import aiosmtplib as smtp
from email.message import EmailMessage

gmail_cfg = {
    "addr": "smtp.gmail.com",
    "username": config.email,
    "password": config.email_password,
    "port": 465
}


async def send_verification_code(
        user: discord.User,
        student_number: str,
        **kwargs
) -> str:
    """Sends a verification code, returning said verification code, to the student."""
    code = secrets.token_hex(16)
    text = f"Hey {user} ({student_number})! The code to join the hi^5 code is '{code}' - use " \
           f"'/verify {code}' in the bot's DMs to continue \N{dancer}\n\n~nex"
    msg = EmailMessage()
    msg["From"] = gmail_cfg["username"]
    msg["To"] = f"{student_number}@my.leedscitycollege.ac.uk"
    msg["Subject"] = "Server Verification"
    msg.set_content(text)

    kwargs.setdefault(
        "hostname", gmail_cfg["addr"]
    )
    kwargs.setdefault(
        "port", gmail_cfg["port"]
    )
    kwargs.setdefault(
        "use_tls", True
    )
    kwargs.setdefault(
        "username", gmail_cfg["username"]
    )
    kwargs.setdefault(
        "password", gmail_cfg["password"]
    )
    kwargs.setdefault(
        "start_tls", not kwargs["use_tls"]
    )

    assert kwargs["start_tls"] != kwargs["use_tls"]

    await smtp.send(
        msg,
        **kwargs
    )
    return code
