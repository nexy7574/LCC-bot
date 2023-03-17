The webserver for the bot will do the following:
* Handle uptime pings
* Provide authentication
* Replace the old email verification code system (replace it with a one-click link rather than a clunky code)
* Store some data for use in eastereggs like the "Nice argument" doxx response
* provide a basic API for some system related information

The server will be located at `127.0.0.1:3762` with the intention of being accessed via reverse-proxy (namely @ https://droplet.nexy7574.co.uk/jimmy/).

Discord auth will require OAUTH_ID, OAUTH_SECRET, and OAUTH_REDIRECT_URI to be set in the `config.py` file.

The web server will auto-start, unless `NO_WEB=True` is set in the `config.py` file.