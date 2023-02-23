import secrets

import discord

import config
import aiosmtplib as smtp
from email.message import EmailMessage

gmail_cfg = {"addr": "smtp.gmail.com", "username": config.email, "password": config.email_password, "port": 465}
TOKEN_LENGTH = 16


class _FakeUser:
    def __init__(self):
        with open("/etc/dictionaries-common/words") as file:
            names = file.readlines()
            names = [x.strip() for x in names if not x.strip().endswith("'s")]
        self.names = names

    def __str__(self):
        import random

        return f"{random.choice(self.names)}#{str(random.randint(1, 9999)).zfill(4)}"


async def send_verification_code(user: discord.User, student_number: str, **kwargs) -> str:
    """Sends a verification code, returning said verification code, to the student."""
    code = secrets.token_hex(TOKEN_LENGTH)
    text = (
        f"Hey {user} ({student_number})! The code to join Unscrupulous Nonsense is '{code}'.\n\n"
        f"Go back to the #verify channel, and click 'I have a verification code!', and put {code} in the modal"
        f" that pops up\n\n"
        f"If you have any issues getting in, feel free to reply to this email, or DM eek#7574.\n"
        f"~Nex\n\n\n"
        f"(P.S you can now go to http://droplet.nexy7574.co.uk/jimmy/verify/{code} instead)"
    )
    msg = EmailMessage()
    msg["From"] = msg["bcc"] = "B593764@my.leedscitycollege.ac.uk"
    msg["To"] = f"{student_number}@my.leedscitycollege.ac.uk"
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
