import discord
import os
import httpx
from datetime import datetime, timedelta, timezone
from hashlib import sha512
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from utils.db import Student, get_or_none

try:
    from config import OAUTH_ID, OAUTH_SECRET, OAUTH_REDIRECT_URI
except ImportError:
    OAUTH_ID = OAUTH_SECRET = OAUTH_REDIRECT_URI = None

OAUTH_ENABLED = OAUTH_ID and OAUTH_SECRET and OAUTH_REDIRECT_URI

app = FastAPI()
app.state.bot = None
app.state.states = set()
app.state.http = httpx.Client()


@app.middleware("http")
async def check_bot_instanced(request, call_next):
    if not request.app.state.bot:
        return JSONResponse(
            status_code=503,
            content={"message": "Not ready."}
        )
    return await call_next(request)


@app.get("/ping")
def ping():
    bot_started = app.state.bot.started_at - datetime.now(tz=timezone.utc)
    return {
        "ping": "pong", 
        "online": app.state.bot.is_ready(), 
        "latency": app.state.bot.latency,
        "uptime": bot_started
    }

@app.get("/auth")
async def authenticate(req: Request, code: str = None, state: str = None):
    if not (code and state) or state not in app.state.states:
        value = os.urandom(3).hex()
        assert value not in app.state.states, "Generated a state that already exists."
        app.state.states.add(value)
        return RedirectResponse(
            discord.utils.oauth_url(
                OAUTH_ID,
                redirect_uri=OAUTH_REDIRECT_URI,
                scopes=('identify',)
            ) + f"&state={value}",
            status_code=301
        )
    else:
        app.state.states.discard(state)
        # First, we need to do the auth code flow
        response = app.state.http.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": OAUTH_ID,
                "client_secret": OAUTH_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
            }
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=response.text
            )
        data = response.json()
        access_token = data["access_token"]
        
        # Now we can generate a token
        token = sha512(access_token.encode()).hexdigest()

        # Now we can get the user's info
        response = app.state.http.get(
            "https://discord.com/api/users/@me",
            headers={
                "Authorization": "Bearer " + data["access_token"]
            }
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=response.text
            )
    
        user = response.json()

        # Now we need to fetch the student from the database
        student = await get_or_none(Student, discord_id=user["id"])
        if not student:
            raise HTTPException(
                status_code=404,
                detail="Student not found. Please run /verify first."
            )
        
        # Now send a request to https://ip-api.com/json/{ip}?fields=17136
        response = app.state.http.get(
            f"https://ip-api.com/json/{req.client.host}?fields=17136"
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=response.text
            )
        data = response.json()
        if data["status"] != "success":
            raise HTTPException(
                status_code=500,
                detail="Failed to get IP data."
            )
        
        # Now we can update the student entry with this data
        await student.update(ip_info=data, access_token_hash=token)

        # And set it as a cookie
        response = RedirectResponse(
            "/",
            status_code=307,
            headers={
                "Cache-Control": "max-age=86400"
            }
        )
        # set the cookie for at most 86400 seconds - expire after that
        response.set_cookie(
            "token",
            token,
            max_age=86400,
            same_site="strict",
            httponly=True,
        )
        return response
