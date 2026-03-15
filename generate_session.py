"""
Run this ONCE locally to generate a SESSION_STRING for cloud deployment.
Copy the printed string into Render's environment variables.
"""
import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

async def main():
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    phone = os.getenv("PHONE")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        await client.start(phone=phone)
        print("\n" + "=" * 60)
        print("YOUR SESSION STRING (copy this into Render):")
        print("=" * 60)
        print(client.session.save())
        print("=" * 60 + "\n")

asyncio.run(main())
