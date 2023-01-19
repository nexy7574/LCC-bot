# LCC Bot
Yeah

## Installing

```shell
git clone https://github.com/EEKIM10/LCC-Bot.git
cd LCC-Bot
# Now you need to edit your config file
mv config.example.py config.py
$EDITOR config.py
docker build -t lcc-bot:latest .
docker run -d --name lcc-bot lcc-bot:latest
```
The bot will now be running.

### Without docker

```shell
git clone https://github.com/EEKIM10/LCC-Bot.git
cd LCC-Bot
python3 -m venv venv
source venv/bin/activate
pip install -U pip wheel setuptools
pip install -r requirements.txt
mv config.example.py config.py
$EDITOR config.py
python3 main.py
```
