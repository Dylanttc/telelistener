# Telelistener 🏸

A personal automation bot that monitors Singapore badminton Telegram groups 24/7, filters out the noise, summarises court sale listings using AI, and automatically creates Google Calendar events when a court is purchased.

---

## The Problem

Badminton courts in Singapore are notoriously difficult to book. Demand far outstrips supply — community centres and school halls are perpetually full, and official booking systems are fiercely competitive. The practical workaround most players rely on is the **resale market**: people who can no longer make their sessions sell their pre-booked courts at cost price via Telegram groups like [@sgbadmintontelecom](https://t.me/sgbadmintontelecom).

The resale supply is actually plentiful — but these groups receive **hundreds of messages daily**, a noisy mix of courts for sale, players recruiting others to join their sessions, questions, and general chatter. Nobody has the capacity to manually monitor this firehose of messages around the clock, which means good courts at good venues get snapped up before most people even see them.

This bot solves that by acting as an always-on personal assistant: it watches the groups 24/7, filters for exactly what matters, and delivers clean summaries to a private Telegram group — with zero manual effort.

---

## What It Does

### 1 — Filters & Archives
Every incoming message is checked against a configurable ruleset (`config.yml`):
- ✅ Must mention a **venue** Dylan cares about (e.g. Teck Ghee, Bishan, Yio Chu Kang)
- ✅ Must contain a **selling-intent keyword** (e.g. "letting go", "transfer", "sale")
- ✅ Must mention a **time slot** (e.g. "7pm", "19:00")
- ❌ Must not contain **exclusion keywords** (e.g. "sold", "reserved", "taken")

Messages that pass are forwarded as-is to a private **archive group** for reference.

### 2 — Summarises with AI
For each matched message, the bot calls Gemini AI to extract only the relevant court sale details — correctly ignoring player-seeking sections and non-listed venues even within the same message. The clean summary is sent to a private **target group**:

```
Venue: Teck Ghee CC
Date: Sat 29 Mar
Time: 7:30PM - 9:30PM
From: John Tan
```

### 3 — Manages Google Calendar via Telegram
When a court is purchased, Dylan sends a confirmation message in the target group. The bot detects this and:
- **Creates** a Google Calendar event (with title, date, start/end time)
- **Invites** a fixed list of attendees via email (with notifications)
- Supports **editing** and **deleting** events via natural language commands, with a 2-step confirmation flow for safety

---

## Architecture

```
[Source group: @sgbadmintontelecom]  ──┐
                                       ├──▶  Bot filters & deduplicates
[Source group: test group]           ──┘           │              │
                                                   │              │
                                          [Archive group]   [Target group]
                                          original msgs     AI summaries only
                                                                   │
                                               Dylan: "@bot confirmed
                                                Teck Ghee 29 Mar 7-9pm"
                                                                   │
                                               Google Calendar event created
                                               + attendees invited via email
```

---

## Key Engineering Details

### Userbot, not a bot account
Telegram bots cannot join or read large public groups unprompted. This project uses a **Telethon userbot** — running on a real Telegram user account — which can listen to any group the account is a member of, including 16,000-member public groups.

### Resilient AI pipeline
Gemini's free tier has daily quota limits, so the summarisation pipeline rotates through **3 Gemini API keys** before giving up. Each key gets a retry on transient 503 errors, and quota exhaustion (429) immediately skips to the next key. If all Gemini keys fail, the bot falls back to **Groq (llama-3.3-70b-versatile)**.

```
Gemini key 1 → Gemini key 2 → Gemini key 3 → Groq → skip summary
```

### Duplicate suppression
Messages are hashed (MD5) and checked against a rolling in-memory set before processing — preventing the same listing from being forwarded twice if it appears in multiple groups or gets re-posted.

### Safety-first calendar edits
Calendar delete and change commands require a **2-step confirmation** (`@bot delete ...` → bot shows what it found → `@bot confirm delete`). The bot also only touches events whose title ends with "Badminton", ensuring it can never accidentally modify unrelated calendar entries.

### Dual-group output
Matched messages go to two separate places:
- **Archive group** — the raw original message, for cross-referencing
- **Target group** — the clean AI summary only, for a noise-free experience

---

## Tech Stack

| Component | Technology |
|---|---|
| Telegram client | [Telethon](https://github.com/LonamiWebs/Telethon) (userbot) |
| AI summarisation | Gemini 2.5-flash-lite (3 keys) + Groq fallback |
| Calendar | Google Calendar API (OAuth2) |
| Hosting | [Fly.io](https://fly.io) — Singapore region (`sin`), 24/7 |
| Configuration | YAML (`config.yml`) — no code changes needed for keyword updates |
| Language | Python 3.11 (fully async via `asyncio`) |

---

## Configuration

All filter keywords live in [`config.yml`](config.yml) — no code changes needed:

```yaml
keywords:
  include:       # venue keywords (at least one must match)
    - "teck ghee"
    - "bishan"
    - "yio chu kang"
    # ...

  exclude:       # message is dropped if any of these appear
    - "sold"
    - "reserved"

intent_keywords: # selling-intent (at least one must match)
  - "letting go"
  - "transfer"
  - "sale"
  # ...

require_time: true   # message must also contain a time slot
```

---

## Environment Variables

Stored as [Fly.io secrets](https://fly.io/docs/apps/secrets/) in production. For local development, copy `.env.example` to `.env`.

| Variable | Description |
|---|---|
| `API_ID` | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Telegram API hash |
| `PHONE` | Phone number of the Telegram account |
| `SESSION_STRING` | Serialised Telethon session for cloud deployment |
| `SOURCE_CHATS` | Comma-separated source group IDs/usernames |
| `TARGET_CHAT` | Private group for AI summaries |
| `ARCHIVE_CHAT` | Group for original forwarded messages |
| `GEMINI_API_KEY` | Primary Gemini key ([aistudio.google.com](https://aistudio.google.com)) |
| `GEMINI_API_KEY_2` | Secondary Gemini key (fallback) |
| `GEMINI_API_KEY_3` | Tertiary Gemini key (fallback) |
| `GROQ_API_KEY` | Groq key ([console.groq.com](https://console.groq.com)) — last resort fallback |
| `GOOGLE_TOKEN` | Serialised Google OAuth2 token for Calendar API |

---

## Project Structure

```
├── app.py               # Main bot logic — filtering, AI summarisation, calendar management
├── config.yml           # All keywords and filter rules
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container definition for Fly.io
├── fly.toml             # Fly.io app config (app: badminton-listener, region: sin)
├── generate_session.py  # One-time script to generate a Telethon session string
└── .env.example         # Template for local environment variables
```

---

## Deployment

The bot runs on Fly.io. From the project root:

```bash
# Deploy latest code
fly deploy

# View live logs
fly logs

# Update a secret
fly secrets set KEY=value
```

---

## Local Development

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your credentials
python app.py
```

On first run you'll be prompted for a Telegram OTP. After that, the session is cached automatically.
