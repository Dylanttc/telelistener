# CLAUDE.md — Project Context for AI Assistant

## Overall Goal

This is a personal automation project for Dylan (GitHub: Dylanttc) to reduce noise when hunting for badminton courts to buy in Singapore.

Singapore badminton Telegram groups (e.g. @sgbadmintontelecom) are flooded with hundreds of messages daily — people selling courts, looking for players, asking questions, etc. The goal is to:

1. **Filter** the noise down to only court sale listings that match venues Dylan cares about
2. **Summarize** those listings into a clean, scannable format using AI
3. **Automate** downstream actions (e.g. creating Google Calendar events) when Dylan purchases a court

---

## What Has Been Built

### Core Bot (app.py)
A Telethon userbot (runs on Dylan's personal Telegram account, not a bot account) that:
- Listens to multiple source Telegram groups simultaneously
- Filters messages using keyword matching (venue keywords + selling intent keywords + time detection)
- Deduplicates messages to avoid forwarding the same listing twice
- For each matched message:
  1. Forwards the original message to a private target Telegram group
  2. Calls Gemini AI to extract only the court sale details (ignoring player-seeking parts of the message), then sends a structured summary:
     Venue: Teck Ghee
     Date: Sun 22 Mar
     Time: 7PM - 9PM
     From: John Tan
  3. If Gemini cannot confidently extract the info, only the original message is forwarded (graceful fallback)

### Configuration (config.yml)
All filter keywords are in this file — venues, selling intent keywords, exclusion keywords, and time regex. Edit this file to change bot behaviour without touching code.

### Deployment
- Hosted on Fly.io (Singapore region, sin) running 24/7
- Containerised via Dockerfile
- Environment variables (API keys, session strings) stored as Fly.io secrets

### Dependencies (requirements.txt)
- telethon — Telegram MTProto client
- python-dotenv — loads .env for local development
- PyYAML — parses config.yml
- rapidfuzz — fuzzy string matching (available, not yet used in main flow)
- google-genai — Google Gemini AI SDK (uses gemini-2.5-flash-lite model, free tier)

---

## Development Workflow

IMPORTANT: Dylan works on a company-managed MacBook. Corporate security software (OKG) blocks:
- git push over HTTPS to GitHub
- The flyctl CLI binary

### The correct workflow for making changes:
1. Dylan describes the change in the Claude Code chat
2. Claude edits local files at /Users/dylanteo/Desktop/telelistener-main/
3. Dylan opens the Codespace (expert-orbit-pwp4pgqpgq3rx7x.github.dev) in VS Code via the GitHub Codespaces extension
4. Dylan runs a Python script in the Codespace terminal to apply the same changes to the Codespace files
5. From the Codespace terminal, Dylan runs git push and fly deploy

### Fly.io deployment from Codespace:
- fly deploy        # deploy latest code
- fly logs          # view live logs
- fly secrets set KEY=value   # update environment variables

---

## Environment Variables

Stored as Fly.io secrets in production. For local development, use a .env file (not committed to git).

- API_ID: Telegram API ID from my.telegram.org
- API_HASH: Telegram API hash from my.telegram.org
- PHONE: Phone number of the Telegram account
- SESSION_STRING: Serialized Telethon session for cloud deployment
- SOURCE_CHATS: Comma-separated list of source Telegram group IDs/usernames
- TARGET_CHAT: Private Telegram group to forward matched messages to
- GEMINI_API_KEY: Google Gemini API key from aistudio.google.com (free tier)

---

## Key Files

- app.py: Main bot logic — filtering, Gemini summarization, forwarding
- config.yml: All keywords and filter rules — edit this to change behaviour
- requirements.txt: Python dependencies
- Dockerfile: Container definition for Fly.io
- fly.toml: Fly.io app config (app name: badminton-listener, region: sin)
- generate_session.py: One-time script to generate a Telethon session string for cloud deployment
- .env.example: Template for local environment variables

---

## What We Are Building Next

### Google Calendar Integration
When Dylan purchases a court, he wants to message his own Telegram account (Saved Messages) and have the bot automatically:
1. Parse the message (venue, date, time) using Gemini AI
2. Create a Google Calendar event with:
   - Title (e.g. "Badminton @ Teck Ghee")
   - Location field populated with the full venue address
   - Start and end time
   - A predefined list of email addresses invited as attendees (with email notifications sent)

### Key decisions already researched:
- Telethon: Use events.NewMessage(chats=[me.id]) to listen to Dylan's own Saved Messages
- Google Calendar API: Free, supports location and attendees fields natively, use sendUpdates=all to send invite emails
- Auth: OAuth 2.0 installed app flow (one-time browser login saves a token.json). Service accounts do NOT work for personal Gmail without paid Google Workspace. Token must be stored as a Fly.io secret since the filesystem is ephemeral.
- Venue to Address mapping: Need to build a lookup table in config.yml or a new file mapping venue keywords (e.g. teck ghee) to full Singapore addresses
- New dependencies needed: google-api-python-client, google-auth-httplib2, google-auth-oauthlib
