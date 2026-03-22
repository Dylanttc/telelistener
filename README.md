# Tele Listener — Badminton Court Forwarder

A Telegram bot that monitors Singapore badminton groups and automatically forwards court sale listings to a private group, filtered by venue and selling intent. Matched messages are summarized by Gemini AI into a clean, scannable format.

## What it does

Singapore badminton groups like @sgbadmintontelecom are flooded with hundreds of messages daily. This bot listens to those groups and only forwards messages that:

1. Mention a venue you care about (e.g. Bishan, Teck Ghee, Yio Chu Kang)
2. Are selling or swapping a court (not just looking for players)
3. Contain a time slot (so vague posts are filtered out)

For each matched message, the bot:
- Forwards the original message to your private group
- Sends a Gemini AI summary immediately after, extracting only the court sale details and ignoring any player-seeking parts:

    Venue: Teck Ghee
    Date: Sun 22 Mar
    Time: 7PM - 9PM
    From: John Tan

## Current keywords

View and suggest changes: config.yml

Venues listened to: Deyi, Teck Ghee, Mayflower, Ang Mo Kio, Bishan, Yio Chu Kang, Peirce, Eunoia, Marymount, Serangoon, Thomson, Jing Shan, Townsville, Whitley, Zhonghua, Peicai, Kuo Chuan, Bowen, Beatty

Selling intent keywords: sale, sell, selling, letting go, let go, swap, give away, transfer

Excluded keywords: sold, reserved, taken

## Project structure

- app.py — Main bot logic
- config.yml — Keywords and filter rules (edit this to change behaviour)
- requirements.txt — Python dependencies
- generate_session.py — One-time script to generate a session string for cloud deployment
- Dockerfile — Container config for Fly.io
- fly.toml — Fly.io deployment config (Singapore region)

## Setup (local)

Prerequisites:
- Python 3.12+
- A spare Telegram account (not your main one)
- Telegram API credentials from my.telegram.org
- A Gemini API key from aistudio.google.com (free, no billing required)

Installation:
1. pip install -r requirements.txt
2. cp .env.example .env
3. Edit .env with your credentials (API_ID, API_HASH, PHONE, SOURCE_CHATS, TARGET_CHAT, GEMINI_API_KEY)

Run: py app.py

On first run you will be asked for a Telegram OTP. After that, login is cached automatically.

## Deployment (Fly.io)

The bot runs 24/7 on Fly.io in the Singapore region.
Deploy from the Codespace terminal (corporate security blocks git push and flyctl from local machine).

Deploy after code changes: fly deploy
View live logs: fly logs
Update a secret: fly secrets set GEMINI_API_KEY=...

## Updating keywords

Edit config.yml, then from the Codespace terminal:
  git add config.yml
  git commit -m "Update keywords"
  git push
  fly deploy

## How the filter works

A message is forwarded only if it passes all of these checks in order:

1. Not a duplicate of a recently seen message
2. Does not contain any exclude keyword (sold, reserved, taken)
3. Contains at least one venue keyword (Bishan, Teck Ghee, etc.)
4. Contains at least one selling intent keyword (selling, let go, swap, etc.)
5. Contains a recognisable time slot (e.g. 7pm, 19:00)
6. Gemini AI extracts the court sale details and sends a summary — if Gemini cannot confidently extract the info, only the original message is forwarded
