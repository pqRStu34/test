"""
Helper script to generate a Telegram StringSession.
Run this script locally to log into your Telegram account once and get your TELEGRAM_STRING_SESSION.
The session string will be printed to the screen AND saved to `session_string.txt`.
"""

import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

def main():
    print("=== Telegram StringSession Generator ===")
    
    api_id = os.environ.get("TELEGRAM_API_ID") or input("Enter TELEGRAM_API_ID: ").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH") or input("Enter TELEGRAM_API_HASH: ").strip()
    
    if not api_id or not api_hash:
        print("Error: API ID and API Hash are required.")
        return

    try:
        api_id = int(api_id)
    except ValueError:
        print("Error: TELEGRAM_API_ID must be an integer.")
        return

    print("\nConnecting to Telegram... You will be prompted for your phone number and login OTP.")
    
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_string = client.session.save()
        
        # Save to local file so it's not lost if terminal closes
        with open("session_string.txt", "w", encoding="utf-8") as f:
            f.write(session_string)
            
        print("\n" + "="*60)
        print("YOUR TELEGRAM_STRING_SESSION (Keep this secret!):")
        print("="*60)
        print(session_string)
        print("="*60)
        print("\nSaved to file: session_string.txt")
        print("Copy the session string above into your GitHub Repository Secrets as TELEGRAM_STRING_SESSION.")
        
        input("\nPress Enter to exit...")

if __name__ == "__main__":
    main()
