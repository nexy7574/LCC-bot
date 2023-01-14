import datetime
import os

guilds = [994710566612500550]
email = None
email_password = "app-specific email password"
reminders = {
    "1 week": 806400,
    "2 days": 86400 * 2,
    "1 day": 86400,
    "6pm": datetime.time(18, 0, 0, 0),
    "3 hours": 3600 * 3,
}
lupupa_warning = True

dev = bool(int(os.getenv("DEV", "0")))
if dev is False:
    token = "PROD_TOKEN"
else:
    token = "DEV_TOKEN"
