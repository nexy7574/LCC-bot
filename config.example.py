# Configuration for LCC-Bot resides here.
# This file is not tracked by git, so you can safely change it without worrying about it being overwritten or
# accidentally committing your changes.

# Please note that removing any of the variables here may cause errors - if you remove a variable, you should
# test the bot to make sure it still works. If you removed a variable that's required, you will get an ImportError.
#
# Note that sometimes new config options are added down the line - you should occasionally check
# https://github.com/EEKIM10/LCC-bot/blob/master/config.example.py to see if there are any changes or new options.

import datetime
import os
import discord

# The IDs of guilds the bot should be in; used to determine where to make slash commands
# If you put multiple IDs in here, the first one should be your "primary" server.
guilds = [994710566612500550]

# Email & email password for the email verification system
email = None
email_password = "app-specific email password"

# Reminder timings for the assignments' system. This doesn't really need changing.
reminders = {
    "1 week": 806400,
    "2 days": 86400 * 2,
    "1 day": 86400,
    "6pm": datetime.time(18, 0, 0, 0),
    "3 hours": 3600 * 3,
}

# Toggles whether the bot will show the lupupa warning status on thursdays.
lupupa_warning = True

# Here are variables used by the web server for authentication.
# If not all of these are provided, web-auth is disabled.
# This also means that web-based verification is also disabled.
# Incorrect credentials will cause errors, indirectly disabling the web functionality.
# Set all of these to `none` if you don't want to use the web functionality.
OAUTH_ID = None  # The user ID of your bot. Must be a string.
OAUTH_SECRET = "my_secret"  # The oauth secret.
OAUTH_REDIRECT_URI = "http://127.0.0.1:3762/auth"  # The full redirect URI registered on your oauth page.

# Here you can change where the web server points.
# You should not change this as you should only permit access to the web server via web proxy.
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 3762

# You can also just fully turn the web server off
WEB_SERVER = True  # change this to False to disable it

# Or change uvicorn settings (see: https://www.uvicorn.org/settings/)
# Note that passing `host` or `port` will raise an error, as those are configured above.
UVICORN_CONFIG = {
    "log_level": "error",
    "access_log": False,
    "lifespan": "off"
}

# Only change this if you want to test changes to the bot without sending too much traffic to discord.
# Connect modes:
# * 0: Operate as normal
# * 1: Exit as soon as the bot is ready
# * 2: Exit before making the websocket connection to discord
CONNECT_MODE = 0

# Toggles dev mode based on the environment variable `DEV`. You can set this to anything here, as long as it
# can evaluate to a boolean.
dev = bool(int(os.getenv("DEV", "0")))
if dev is False:
    # This is the token that will be used if dev mode is disabled.
    token = "PROD_TOKEN"
else:
    # This is the token that will be used for development mode. You should specify this if you have a separate test bot
    # to avoid creating duplicate slash commands or exceeding rate limits.
    token = "DEV_TOKEN"


# You can also set intents here if you want to disable some of them. By default, the bot will enable all intents.
intents = discord.Intents.all()
