FROM python:3.11-bullseye

COPY config.py /

RUN apt-get update

RUN apt-get install software-properties-common apt-transport-https wget ca-certificates -y

RUN wget -O- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor | tee /usr/share/keyrings/google-chrome.gpg

RUN echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main' | tee /etc/apt/sources.list.d/google-chrome.list

RUN apt-get update

RUN apt-get install -y \
    build-essential \
    libpq-dev \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-venv \
    firefox-esr \
    google-chrome-stable \
    espeak

COPY requirements.txt /

RUN pip install -r requirements.txt

COPY . /

CMD ["python3", "main.py"]
