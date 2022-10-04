import secrets

import discord

import config
import aiosmtplib as smtp
from email.message import EmailMessage

gmail_cfg = {"addr": "smtp.gmail.com", "username": config.email, "password": config.email_password, "port": 465}
TOKEN_LENGTH = 16


async def send_verification_code(user: discord.User, student_number: str, **kwargs) -> str:
    """Sends a verification code, returning said verification code, to the student."""
    code = secrets.token_hex(TOKEN_LENGTH)
    text = (
        f"Hey {user} ({student_number})! The code to join the Unscrupulous Nonsense is '{code}'.\n\n"
        f"Go back to the #verify channel, and click 'I have a verification code!', and put {code} in the modal"
        f" that pops up\n\n"
        f"If you have any issues getting in, feel free to reply to this email, or DM eek#7574.\n"
        f"~Nex"
    )
    msg = EmailMessage()
    msg["From"] = "B593764@my.leedscitycollege.ac.uk"
    msg["To"] = f"{student_number}@my.leedscitycollege.ac.uk"
    msg["Bcc"] = gmail_cfg["username"]
    msg["Subject"] = "Server Verification"
    msg.set_content(text)

    kwargs.setdefault("hostname", gmail_cfg["addr"])
    kwargs.setdefault("port", gmail_cfg["port"])
    kwargs.setdefault("use_tls", True)
    kwargs.setdefault("username", gmail_cfg["username"])
    kwargs.setdefault("password", gmail_cfg["password"])
    kwargs.setdefault("start_tls", not kwargs["use_tls"])

    assert kwargs["start_tls"] != kwargs["use_tls"]

    await smtp.send(msg, **kwargs)
    return code
