import os
import re
import hashlib
import asyncio
import logging

import yaml
from google import genai
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.network import ConnectionTcpObfuscated

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────────────
def load_config() -> dict:
    with open("config.yml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Duplicate suppression ────────────────────────────────────────────────────
_seen: set[str] = set()
_MAX_SEEN = 500


def _hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


def is_duplicate(text: str) -> bool:
    h = _hash(text)
    if h in _seen:
        return True
    _seen.add(h)
    if len(_seen) > _MAX_SEEN:
        _seen.clear()
    return False


# ── Filter logic ─────────────────────────────────────────────────────────────
def passes_filter(text: str, config: dict) -> tuple[bool, str]:
    """Returns (passed, reason) for logging."""
    if not text or not text.strip():
        return False, "empty message"

    text_lower = text.lower()
    keywords = config.get("keywords", {})

    for kw in keywords.get("exclude", []):
        if kw.lower() in text_lower:
            return False, f"exclude keyword '{kw}'"

    include_kws = keywords.get("include", [])
    if include_kws:
        matched_kw = next((kw for kw in include_kws if kw.lower() in text_lower), None)
        if not matched_kw:
            return False, "no include keyword matched"

    intent_kws = config.get("intent_keywords", [])
    if intent_kws:
        matched_intent = next((kw for kw in intent_kws if kw.lower() in text_lower), None)
        if not matched_intent:
            return False, "no selling intent keyword matched"

    if config.get("require_time", False):
        time_regex = config.get("time_regex", "")
        if time_regex and not re.search(time_regex, text, re.IGNORECASE):
            return False, "no time found"

    return True, "ok"


# ── Gemini summarization ─────────────────────────────────────────────────────
GEMINI_PROMPT = """You are extracting badminton court sale information from a Telegram message in a Singapore group.

The message may contain two types of information:
1. Courts being SOLD / let go / transferred — extract this
2. Courts where the sender is LOOKING FOR players to join — ignore this completely

Extract ONLY the courts being sold/transferred and return in this exact format:
Venue: <venue name>
Date: <date, e.g. "Mon 24 Mar" or "Tomorrow (Tue 25 Mar)">
Time: <start time> - <end time, e.g. "8PM - 10PM">

If multiple courts are being sold, repeat the Date/Time/Venue block for each.
If you cannot confidently identify a court being sold, respond with exactly: UNCLEAR

Message:
{text}"""


async def summarize_with_gemini(text: str, sender_name: str, model) -> str | None:
    prompt = GEMINI_PROMPT.format(text=text)
    try:
        response = await asyncio.to_thread(model.models.generate_content, model="gemini-2.5-flash-lite", contents=prompt)
        result = response.text.strip()
        if result == "UNCLEAR":
            return None
        return f"From: {sender_name}\n{result}"
    except Exception as e:
        log.warning("Gemini summarization failed: %s", e)
        return None


# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    config = load_config()

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    phone = os.getenv("PHONE")
    session_string = os.getenv("SESSION_STRING", "").strip()

    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = None
    if gemini_api_key:
        gemini_model = genai.Client(api_key=gemini_api_key)
        log.info("Gemini summarization enabled")
    else:
        log.warning("GEMINI_API_KEY not set — summarization disabled")

    source_chats = []
    for c in os.getenv("SOURCE_CHATS", "").split(","):
        c = c.strip()
        try:
            source_chats.append(int(c))
        except ValueError:
            source_chats.append(c)

    target_raw = os.getenv("TARGET_CHAT", "").strip()
    try:
        target_chat = int(target_raw)
    except ValueError:
        target_chat = target_raw

    if session_string:
        log.info("Using session string (cloud mode)")
        session = StringSession(session_string)
    else:
        log.info("Using local session file")
        session = "session"

    client = TelegramClient(session, api_id, api_hash, connection=ConnectionTcpObfuscated)
    if session_string:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Session string is invalid or expired — regenerate it with generate_session.py")
    else:
        await client.start(phone=phone)

    await client.get_dialogs()

    source_entities = [await client.get_entity(c) for c in source_chats]
    log.info("Listening on %d source(s): %s", len(source_entities), ", ".join(str(c) for c in source_chats))
    log.info("Forwarding matches to: %s", target_chat)

    @client.on(events.NewMessage(chats=source_entities))
    async def handler(event):
        text = event.message.text or ""

        if is_duplicate(text):
            log.info("SKIP [duplicate] %s", text[:60].replace("\n", " "))
            return

        passed, reason = passes_filter(text, config)
        if not passed:
            log.info("SKIP [%s] %s", reason, text[:60].replace("\n", " "))
            return

        sender = await event.get_sender()
        if sender:
            sender_name = " ".join(filter(None, [getattr(sender, "first_name", ""), getattr(sender, "last_name", "")]))
            if not sender_name and getattr(sender, "username", None):
                sender_name = f"@{sender.username}"
        else:
            sender_name = "Unknown"

        preview = text[:80].replace("\n", " ")
        log.info("MATCH → forwarding: %s", preview)
        try:
            await client.forward_messages(target_chat, event.message)
            log.info("FORWARDED ✓ %s", preview)

            if gemini_model:
                summary = await summarize_with_gemini(text, sender_name, gemini_model)
                if summary:
                    await client.send_message(target_chat, summary)
                    log.info("SUMMARY sent for: %s", preview)
                else:
                    log.info("SUMMARY skipped (UNCLEAR): %s", preview)
        except FloodWaitError as e:
            log.warning("Rate limited by Telegram — waiting %ds", e.seconds)
            await asyncio.sleep(e.seconds)
        except Exception as e:
            log.error("FAILED to forward: %s", e)

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
