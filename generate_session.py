import os
import sys
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

load_dotenv()

async def main():
    print("=" * 60)
    print("  Telegram String Session Generator (Telethon 1.45+)")
    print("=" * 60)

    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()

    if not api_id:
        api_id = input("\nEnter your Telegram API ID: ").strip()
    else:
        print(f"\nUsing TELEGRAM_API_ID from .env: {api_id}")

    if not api_hash:
        api_hash = input("Enter your Telegram API Hash: ").strip()
    else:
        print("Using TELEGRAM_API_HASH from .env")

    phone = input("\nEnter your Phone Number (with country code, e.g. +1234567890): ").strip().replace(" ", "")

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        await client.send_code_request(phone)
        code = input("\nEnter the login code sent to your Telegram app: ").strip().replace(" ", "").replace("-", "")
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            pwd = input("Enter your Two-Step Verification (2FA) Password: ").strip()
            await client.sign_in(password=pwd)

    string_session = client.session.save()
    me = await client.get_me()
    name = getattr(me, 'first_name', '') or getattr(me, 'username', 'User')

    print("\n" + "=" * 65)
    print(f"  SUCCESS! Authenticated as: {name} (ID: {me.id})")
    print("=" * 65)
    print("\nGenerated TELEGRAM_STRING_SESSION string:\n")
    print(string_session)
    print("\n" + "=" * 65)

    print("\nWhich pipeline session would you like to save this as in .env?")
    print("  1 = TELEGRAM_STRING_SESSION_1 (SubsPlease Uploader)")
    print("  2 = TELEGRAM_STRING_SESSION_2 (Tsukihime Sync)")
    print("  3 = TELEGRAM_STRING_SESSION_3 (Internet Archive Sync Log Channel Reader)")
    print("  N = Do not save to .env (just copy the string above)")
    save_opt = input("\nChoice [1/2/3/N]: ").strip().upper()
    if save_opt in ("1", "2", "3"):
        var_name = f"TELEGRAM_STRING_SESSION_{save_opt}"
        env_path = ".env"
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()

        found = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{var_name}="):
                new_lines.append(f"{var_name}={string_session}")
                found = True
            else:
                new_lines.append(line)

        if not found:
            new_lines.append(f"{var_name}={string_session}")

        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")

        print(f"Saved to .env as '{var_name}' successfully!")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
