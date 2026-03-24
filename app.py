import os
import re
import json
import hashlib
import asyncio
import logging
from datetime import datetime

import yaml
from google import genai
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.network import ConnectionTcpObfuscated
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

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

# ── Pending calendar operations (confirmation flow) ───────────────────────────
# {chat_id: {"action": "delete"|"change", "event_id": str, "event_summary": str,
#             "updated": dict|None, "updated_summary": str|None}}
_pending_ops: dict = {}


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


# ── Gemini summarization ──────────────────────────────────────────────────────
GEMINI_PROMPT = """\
You are extracting badminton court sale information from a Telegram message in a Singapore group.

You are ONLY interested in courts at these specific venues: {venues}

Today's date is {today}.

The message may contain multiple types of information. Your rules:
1. Courts being SOLD / let go / transferred AT ONE OF THE LISTED VENUES ABOVE — extract this
2. Courts at venues NOT in the list above — ignore completely
3. Courts where the sender is LOOKING FOR players to join — ignore completely

Extract ONLY the courts being sold/transferred at the listed venues and return in this exact format:
Venue: <venue name>
Date: <date as DD Mon YYYY, e.g. "24 Mar 2026">
Time: <start time> - <end time, e.g. "8PM - 10PM">

If multiple courts at the listed venues are being sold, repeat the Venue/Date/Time block for each.
If you cannot confidently identify a court being sold at the listed venues, respond with exactly: UNCLEAR

Message:
{text}"""


def add_day_to_dates(summary: str) -> str:
    """Parse Date: lines from Gemini output and prepend the correct weekday using Python datetime."""
    def replace_date(match):
        date_str = match.group(1).strip()
        try:
            dt = datetime.strptime(date_str, "%d %b %Y")
            return "Date: " + dt.strftime("%a %d %b")
        except ValueError:
            return match.group(0)
    return re.sub(r"^Date: (.+)$", replace_date, summary, flags=re.MULTILINE)


async def summarize_with_gemini(text: str, sender_name: str, model, venues: list[str]) -> str | None:
    """Returns a formatted summary string, or None if extraction failed."""
    venues_str = ", ".join(venues) if venues else "any venue"
    today = datetime.now().strftime("%A, %d %B %Y")
    prompt = GEMINI_PROMPT.format(venues=venues_str, today=today, text=text)
    try:
        response = await asyncio.to_thread(
            model.models.generate_content,
            model="gemini-2.5-flash-lite",
            contents=prompt
        )
        result = response.text.strip()
        if result == "UNCLEAR":
            return None
        result = add_day_to_dates(result)
        return f"{result}\nFrom: {sender_name}"
    except Exception as e:
        log.warning("Gemini summarization failed: %s", e)
        return None


# ── Gemini booking parser ─────────────────────────────────────────────────────
BOOKING_PROMPT = """\
Extract badminton court booking information from this Telegram message.

Message: {text}

Today's date is {today}. The timezone is Asia/Singapore (UTC+8).

Return ONLY a JSON object with no extra text:
{{
  "venue": "venue name, properly capitalized (e.g. Teck Ghee, Deyi Sec, Bishan)",
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM"
}}

Rules:
- If only a start time is given, assume a 2-hour session for end_time
- Interpret dates relative to today
- If you cannot extract the information, return exactly: UNCLEAR"""


async def parse_booking_message(text: str, model) -> dict | None:
    """Returns parsed booking dict or None if extraction failed."""
    today = datetime.now().strftime("%A, %d %B %Y")
    prompt = BOOKING_PROMPT.format(text=text, today=today)
    try:
        response = await asyncio.to_thread(
            model.models.generate_content,
            model="gemini-2.5-flash-lite",
            contents=prompt
        )
        result = response.text.strip()
        if result == "UNCLEAR":
            return None
        # Strip markdown code fences if present
        result = re.sub(r"^```(?:json)?\n?", "", result)
        result = re.sub(r"\n?```$", "", result)
        return json.loads(result)
    except Exception as e:
        log.warning("Booking parse failed: %s", e)
        return None


# ── Gemini delete parser ──────────────────────────────────────────────────────
DELETE_PROMPT = """\
Extract the Google Calendar event to delete from this message.

Message: {text}
Today's date is {today}. Timezone: Asia/Singapore (UTC+8).

Return ONLY a JSON object with no extra text:
{{
  "venue": "venue name, properly capitalized (e.g. Teck Ghee, Deyi Sec, Bishan)",
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM"
}}

Rules:
- Interpret dates relative to today
- If you cannot extract the information, return exactly: UNCLEAR"""


async def parse_delete_command(text: str, model) -> dict | None:
    """Returns {venue, date, start_time} or None if extraction failed."""
    today = datetime.now().strftime("%A, %d %B %Y")
    prompt = DELETE_PROMPT.format(text=text, today=today)
    try:
        response = await asyncio.to_thread(
            model.models.generate_content,
            model="gemini-2.5-flash-lite",
            contents=prompt
        )
        result = response.text.strip()
        if result == "UNCLEAR":
            return None
        result = re.sub(r"^```(?:json)?\n?", "", result)
        result = re.sub(r"\n?```$", "", result)
        return json.loads(result)
    except Exception as e:
        log.warning("Delete parse failed: %s", e)
        return None


# ── Gemini change parser ──────────────────────────────────────────────────────
CHANGE_PROMPT = """\
Extract the original and updated event details from this calendar change request.

Message: {text}
Today's date is {today}. Timezone: Asia/Singapore (UTC+8).

Return ONLY a JSON object with no extra text:
{{
  "original": {{
    "venue": "original venue name, properly capitalized",
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM"
  }},
  "updated": {{
    "venue": "new venue (same as original if not changed)",
    "date": "YYYY-MM-DD (same as original if not changed)",
    "start_time": "HH:MM",
    "end_time": "HH:MM"
  }}
}}

Rules:
- Interpret dates relative to today
- If venue or date are not mentioned in the change, keep the same as original
- If end_time is not specified, assume a 2-hour session from the new start_time
- If you cannot extract the information, return exactly: UNCLEAR"""


async def parse_change_command(text: str, model) -> dict | None:
    """Returns {original: {...}, updated: {...}} or None if extraction failed."""
    today = datetime.now().strftime("%A, %d %B %Y")
    prompt = CHANGE_PROMPT.format(text=text, today=today)
    try:
        response = await asyncio.to_thread(
            model.models.generate_content,
            model="gemini-2.5-flash-lite",
            contents=prompt
        )
        result = response.text.strip()
        if result == "UNCLEAR":
            return None
        result = re.sub(r"^```(?:json)?\n?", "", result)
        result = re.sub(r"\n?```$", "", result)
        return json.loads(result)
    except Exception as e:
        log.warning("Change parse failed: %s", e)
        return None


# ── Google Calendar ───────────────────────────────────────────────────────────
CALENDAR_ATTENDEES = ["dylanttc95@gmail.com"]


def get_calendar_service():
    token_json = os.getenv("GOOGLE_TOKEN", "").strip()
    if not token_json:
        raise ValueError("GOOGLE_TOKEN not set")
    creds = Credentials.from_authorized_user_info(
        json.loads(token_json),
        ["https://www.googleapis.com/auth/calendar.events"]
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("calendar", "v3", credentials=creds)


async def find_calendar_event(service, venue: str, date: str, start_time: str) -> dict | None:
    """Search for a bot-created calendar event by venue, date and start time.
    Only matches events whose title ends with 'Badminton' to avoid touching other calendar events."""
    time_min = f"{date}T00:00:00+08:00"
    time_max = f"{date}T23:59:59+08:00"
    result = await asyncio.to_thread(
        lambda: service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            q=venue,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
    )
    events = result.get("items", [])
    # Safety filter: only touch bot-created events (title always ends with "Badminton")
    badminton_events = [ev for ev in events if ev.get("summary", "").endswith("Badminton")]
    # Prefer exact start time match
    for ev in badminton_events:
        if f"T{start_time}:" in ev["start"].get("dateTime", ""):
            return ev
    return badminton_events[0] if badminton_events else None


async def edit_calendar_event(service, event_id: str, updated: dict) -> bool:
    """Update an existing calendar event with new details."""
    venue = updated["venue"]
    date = updated["date"]
    start_time = updated["start_time"]
    end_time = updated["end_time"]
    start_dt = datetime.strptime(start_time, "%H:%M")
    title = f"{venue} {start_dt.strftime('%-I%p')} Badminton"
    patch_body = {
        "summary": title,
        "start": {"dateTime": f"{date}T{start_time}:00+08:00", "timeZone": "Asia/Singapore"},
        "end": {"dateTime": f"{date}T{end_time}:00+08:00", "timeZone": "Asia/Singapore"},
    }
    await asyncio.to_thread(
        lambda: service.events().patch(
            calendarId="primary",
            eventId=event_id,
            sendUpdates="all",
            body=patch_body
        ).execute()
    )
    return True


async def delete_calendar_event(service, event_id: str) -> bool:
    """Delete a calendar event by ID."""
    await asyncio.to_thread(
        lambda: service.events().delete(
            calendarId="primary",
            eventId=event_id,
            sendUpdates="all"
        ).execute()
    )
    return True


async def create_calendar_event(parsed: dict) -> bool:
    venue = parsed["venue"]
    date = parsed["date"]
    start_time = parsed["start_time"]
    end_time = parsed["end_time"]

    start_dt = datetime.strptime(start_time, "%H:%M")
    title = f"{venue} {start_dt.strftime('%-I%p')} Badminton"

    event = {
        "summary": title,
        "start": {
            "dateTime": f"{date}T{start_time}:00+08:00",
            "timeZone": "Asia/Singapore",
        },
        "end": {
            "dateTime": f"{date}T{end_time}:00+08:00",
            "timeZone": "Asia/Singapore",
        },
        "attendees": [{"email": e} for e in CALENDAR_ATTENDEES],
    }

    service = await asyncio.to_thread(get_calendar_service)
    result = await asyncio.to_thread(
        lambda: service.events().insert(
            calendarId="primary",
            sendUpdates="all",
            body=event
        ).execute()
    )
    log.info("Calendar event created: %s", result.get("htmlLink"))
    return True


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
        log.info("Gemini enabled")
    else:
        log.warning("GEMINI_API_KEY not set — Gemini disabled")

    # Convert numeric IDs to int so Telethon can resolve private groups correctly
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

    me = await client.get_me()
    bot_username = (me.username or "").lower()
    log.info("Logged in as: @%s (id=%s)", bot_username, me.id)

    source_entities = [await client.get_entity(c) for c in source_chats]
    target_entity = await client.get_entity(target_chat)
    log.info("Listening on %d source(s): %s", len(source_entities), ", ".join(str(c) for c in source_chats))
    log.info("Forwarding matches to: %s", target_chat)
    log.info("Listening for calendar commands in: %s", target_chat)

    # ── Court sale forwarder ──────────────────────────────────────────────────
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
                include_kws = config.get("keywords", {}).get("include", [])
                summary = await summarize_with_gemini(text, sender_name, gemini_model, include_kws)
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

    # ── Calendar commands (create / edit / delete) ───────────────────────────
    @client.on(events.NewMessage(chats=[target_entity]))
    async def calendar_handler(event):
        text = event.message.text or ""

        # Log every message arriving in the target group for visibility
        log.info("TARGET MSG [mentioned=%s]: %s", event.message.mentioned, text[:80].replace("\n", " "))

        # Accept both proper Telegram mention entities and plain-text @username
        is_mentioned = event.message.mentioned or (bot_username and f"@{bot_username}" in text.lower())
        if not is_mentioned:
            log.info("TARGET SKIP [not mentioned]")
            return

        text_lower = text.lower()
        if not any(kw in text_lower for kw in ("confirmed", "confirm", "delete", "change")):
            log.info("TARGET SKIP [no keyword]: %s", text[:60].replace("\n", " "))
            return

        log.info("CALENDAR trigger: %s", text[:60].replace("\n", " "))

        if not gemini_model:
            log.warning("Gemini not configured — cannot process calendar command")
            return

        chat_id = event.chat_id

        # ── Confirm delete ────────────────────────────────────────────────
        if "confirm delete" in text_lower:
            op = _pending_ops.pop(chat_id, None)
            if not op or op["action"] != "delete":
                await event.reply("No pending delete to confirm. Please send the delete command first.")
                return
            try:
                service = await asyncio.to_thread(get_calendar_service)
                await delete_calendar_event(service, op["event_id"])
                await event.reply(f"Done, I've deleted: {op['event_summary']}.")
                log.info("Calendar event deleted: %s", op["event_summary"])
            except Exception as e:
                log.error("Failed to delete calendar event: %s", e)
                await event.reply("Sorry, something went wrong while deleting the event.")

        # ── Confirm change ────────────────────────────────────────────────
        elif "confirm change" in text_lower:
            op = _pending_ops.pop(chat_id, None)
            if not op or op["action"] != "change":
                await event.reply("No pending change to confirm. Please send the change command first.")
                return
            try:
                service = await asyncio.to_thread(get_calendar_service)
                await edit_calendar_event(service, op["event_id"], op["updated"])
                await event.reply(f"Done, I've updated the event to: {op['updated_summary']}.")
                log.info("Calendar event updated to: %s", op["updated_summary"])
            except Exception as e:
                log.error("Failed to edit calendar event: %s", e)
                await event.reply("Sorry, something went wrong while updating the event.")

        # ── Create ────────────────────────────────────────────────────────
        elif "confirmed" in text_lower:
            parsed = await parse_booking_message(text, gemini_model)
            if not parsed:
                log.warning("Could not parse booking from: %s", text[:60])
                return
            try:
                await create_calendar_event(parsed)
                await event.reply("Okay, I've created a Google Calendar event.")
                log.info("Calendar event created and confirmation sent")
            except Exception as e:
                log.error("Failed to create calendar event: %s", e)

        # ── Initiate delete (step 1 of 2) ─────────────────────────────────
        elif "delete" in text_lower:
            parsed = await parse_delete_command(text, gemini_model)
            if not parsed:
                await event.reply("Sorry, I couldn't understand which event to delete. Please specify the venue, date and time.")
                return
            try:
                service = await asyncio.to_thread(get_calendar_service)
                ev = await find_calendar_event(service, parsed["venue"], parsed["date"], parsed["start_time"])
                if not ev:
                    await event.reply(f"Sorry, I couldn't find a Badminton calendar event for {parsed['venue']} on {parsed['date']} at {parsed['start_time']}.")
                    return
                _pending_ops[chat_id] = {
                    "action": "delete",
                    "event_id": ev["id"],
                    "event_summary": ev.get("summary", "the event"),
                    "updated": None,
                    "updated_summary": None,
                }
                await event.reply(
                    f"Found: {ev.get('summary')}\n"
                    f"📅 {ev['start'].get('dateTime', '')[:10]}, "
                    f"{ev['start'].get('dateTime', '')[11:16]} – {ev['end'].get('dateTime', '')[11:16]}\n\n"
                    f"Reply @bot confirm delete to delete it, or ignore to cancel."
                )
            except Exception as e:
                log.error("Failed to find event for delete: %s", e)
                await event.reply("Sorry, something went wrong while looking up the event.")

        # ── Initiate change (step 1 of 2) ─────────────────────────────────
        elif "change" in text_lower:
            parsed = await parse_change_command(text, gemini_model)
            if not parsed:
                await event.reply("Sorry, I couldn't understand the change. Please specify the original event and the new details.")
                return
            try:
                service = await asyncio.to_thread(get_calendar_service)
                orig = parsed["original"]
                ev = await find_calendar_event(service, orig["venue"], orig["date"], orig["start_time"])
                if not ev:
                    await event.reply(f"Sorry, I couldn't find a Badminton calendar event for {orig['venue']} on {orig['date']} at {orig['start_time']}.")
                    return
                upd = parsed["updated"]
                upd_dt = datetime.strptime(upd["start_time"], "%H:%M")
                updated_summary = f"{upd['venue']} {upd_dt.strftime('%-I%p')} Badminton"
                _pending_ops[chat_id] = {
                    "action": "change",
                    "event_id": ev["id"],
                    "event_summary": ev.get("summary", "the event"),
                    "updated": upd,
                    "updated_summary": updated_summary,
                }
                await event.reply(
                    f"Found: {ev.get('summary')}\n"
                    f"📅 {ev['start'].get('dateTime', '')[:10]}, "
                    f"{ev['start'].get('dateTime', '')[11:16]} – {ev['end'].get('dateTime', '')[11:16]}\n\n"
                    f"→ New: {updated_summary}\n"
                    f"📅 {upd['date']}, {upd['start_time']} – {upd['end_time']}\n\n"
                    f"Reply @bot confirm change to apply, or ignore to cancel."
                )
            except Exception as e:
                log.error("Failed to find event for change: %s", e)
                await event.reply("Sorry, something went wrong while looking up the event.")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
