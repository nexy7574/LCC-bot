# auto-configuration for docker that pulls from environment variables
# This format is very limited and environment variables were ditched very early on in favor of a config file
# Do feel free to overwrite this file and re-build the docker image - this is effectively a stub.

import datetime
import os

# A few defaults that can't be set in the environment
reminders = {
    "1 week": 806400,
    "2 days": 86400 * 2,
    "1 day": 86400,
    "6pm": datetime.time(18, 0, 0, 0),
    "3 hours": 3600 * 3,
}
CONNECT_MODE = 0  # this cannot be changed because who's debugging using the docker container
dev = 0

if os.getenv("GUILDS") is not None:
    guilds = [int(x) for x in os.getenv("GUILDS").split(",")]
else:
    guilds = []

email = os.getenv("EMAIL")
email_password = os.getenv("EMAIL_PASSWORD")

lupupa_warning = bool(int(os.getenv("LUPUPA_WARNING", "1")))

OAUTH_ID = os.getenv("OAUTH_ID")
OAUTH_SECRET = os.getenv("OAUTH_SECRET")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI")

HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.getenv("HTTP_PORT", "3762"))

WEB_SERVER = bool(int(os.getenv("WEB_SERVER", "1")))

assert os.getenv("token"), "$token environment variable not set"
token = os.environ["token"]
