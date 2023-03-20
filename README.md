# LCC Bot
Yeah

## Installing (docker)

see [INSTALL.md](INSTALL.md)

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
