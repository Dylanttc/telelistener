# Tele Listener — Badminton Court Forwarder

A Telegram bot that monitors a badminton group and automatically forwards court sale listings to a private group, filtered by venue and selling intent.

## What it does

Singapore badminton groups like [@sgbadmintontelecom](https://t.me/sgbadmintontelecom) are flooded with hundreds of messages daily. This bot listens to those groups and only forwards messages that:

1. Mention a **venue you care about** (e.g. Bishan, Teck Ghee, Yio Chu Kang)
2. Are **selling or swapping** a court (not just looking for players)
3. Contain a **time slot** (so vague posts are filtered out)

Matched messages are forwarded to a private Telegram group.

## Current keywords

View and suggest changes: [`config.yml`](config.yml)

**Venues listened to:** Deyi, Teck Ghee, Mayflower, Ang Mo Kio, Bishan, Yio Chu Kang, Peirce

**Selling intent keywords:** sale, sell, selling, letting go, let go, swap, give away, transfer

**Excluded keywords:** sold, reserved, taken

## Project structure

```
├── app.py              # Main bot logic
├── config.yml          # Keywords and filter rules — edit this to change behaviour
├── requirements.txt    # Python dependencies
├── generate_session.py # One-time script to generate a session string for cloud deployment
├── Dockerfile          # Container config for Fly.io
└── fly.toml            # Fly.io deployment config (Singapore region)
```

## Setup (local)

### Prerequisites
- Python 3.12+
- A spare Telegram account (not your main one)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)

### Installation

```bash
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your credentials:
```
API_ID=your_api_id
API_HASH=your_api_hash
PHONE=+65xxxxxxxx
SOURCE_CHATS=@sgbadmintontelecom
TARGET_CHAT=-your_group_id
```

### Run

```bash
py app.py
```

On first run you'll be asked for a Telegram OTP. After that, login is cached automatically.

## Deployment (Fly.io)

The bot runs 24/7 on [Fly.io](https://fly.io) in the Singapore region.

```bash
# Deploy after code changes
fly deploy

# View live logs
fly logs

# Update a secret
fly secrets set SESSION_STRING=...
```

## Updating keywords

Edit [`config.yml`](config.yml) and redeploy:

```bash
git add config.yml
git commit -m "Update keywords"
git push
fly deploy
```

## How the filter works

A message is forwarded only if it passes **all** of these checks (in order):

1. Not a duplicate of a recently seen message
2. Does **not** contain any exclude keyword (sold, reserved, taken)
3. Contains **at least one** venue keyword (Bishan, Teck Ghee, etc.)
4. Contains **at least one** selling intent keyword (selling, let go, swap, etc.)
5. Contains a recognisable time slot (e.g. 7pm, 19:00)
