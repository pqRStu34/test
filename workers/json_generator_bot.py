import os
import sys
import json
import logging
import argparse
import tempfile
import requests
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# Add parent directory for pocketbase client
sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketbase.client import PocketBaseClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("json_generator_bot")

TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()
BOT_TOKEN = os.environ.get("PERSONAL_BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "").strip()

POCKETBASE_URL = os.environ.get("POCKETBASE_URL", "http://127.0.0.1:8090").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "anime-episodes").strip()


def build_anime_json(anime_id: str, pb_client: PocketBaseClient) -> tuple[dict, int]:
    """Queries PocketBase and constructs standard episode JSON matching 40969.json."""
    records = pb_client.query_by_anime_id(anime_id)
    if not records:
        return {}, 0

    episodes_map = {}
    for r in records:
        ep_num = r.get("episode_number") or 1
        if isinstance(ep_num, float) and ep_num.is_integer():
            ep_num = int(ep_num)

        if ep_num not in episodes_map:
            episodes_map[ep_num] = {
                "episodeNumber": ep_num,
                "thumbnail": r.get("thumbnail") or "",
                "audio": {}
            }

        lang = (r.get("language") or "JPN").upper()
        qual = (r.get("quality") or "1080p").lower()
        stream_link = r.get("stream_link") or ""

        if lang not in episodes_map[ep_num]["audio"]:
            episodes_map[ep_num]["audio"][lang] = {}

        episodes_map[ep_num]["audio"][lang][qual] = stream_link

    sorted_episodes = [episodes_map[k] for k in sorted(episodes_map.keys())]
    return {"episodes": sorted_episodes}, len(sorted_episodes)


def upload_to_supabase(anime_id: str, json_data: dict) -> tuple[bool, str]:
    """Uploads/replaces <anime_id>.json in Supabase Storage with x-upsert: true."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False, "Supabase credentials (SUPABASE_URL, SUPABASE_KEY) are not configured."

    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{anime_id}.json"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "x-upsert": "true"
    }

    try:
        body = json.dumps(json_data, indent=2).encode("utf-8")
        resp = requests.post(url, headers=headers, data=body, timeout=15)
        if resp.status_code in (200, 201):
            return True, f"Successfully uploaded/replaced '{anime_id}.json' in bucket '{SUPABASE_BUCKET}'."
        else:
            return False, f"Supabase Storage error ({resp.status_code}): {resp.text}"
    except Exception as e:
        return False, f"Failed to connect to Supabase: {e}"


def run_cli_generation(anime_id: str):
    """Generates JSON and uploads to Supabase via CLI."""
    pb = PocketBaseClient(base_url=POCKETBASE_URL)
    payload, count = build_anime_json(anime_id, pb)
    if count == 0:
        logger.error(f"No records found for anime ID '{anime_id}' in PocketBase.")
        return

    out_file = Path(f"{anime_id}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Saved {count} episodes to {out_file}.")

    success, msg = upload_to_supabase(anime_id, payload)
    if success:
        logger.info(f"Supabase: {msg}")
    else:
        logger.warning(f"Supabase upload skipped/failed: {msg}")


async def start_telegram_bot():
    """Runs interactive Telegram bot listening for /json <anime_id> commands."""
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH or not BOT_TOKEN:
        logger.critical("TELEGRAM_API_ID, TELEGRAM_API_HASH, and BOT_TOKEN must be configured.")
        sys.exit(1)

    pb = PocketBaseClient(base_url=POCKETBASE_URL)
    bot = TelegramClient(StringSession(), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    logger.info("Personal JSON Generator Bot started.")

    @bot.on(events.NewMessage(pattern=r"^/start"))
    async def handle_start(event):
        await event.reply(
            "👋 **Anime JSON Generator Bot**\n\n"
            "Commands:\n"
            "• `/json <anime_id>` — Generate JSON from PocketBase, receive file, and auto-upload to Supabase.\n"
            "• `/status` — Check PocketBase and Supabase connection status."
        )

    @bot.on(events.NewMessage(pattern=r"^/status"))
    async def handle_status(event):
        pb_ok = pb.ensure_collection()
        sb_configured = bool(SUPABASE_URL and SUPABASE_KEY)
        await event.reply(
            f"📊 **System Status**\n"
            f"• PocketBase ({POCKETBASE_URL}): {'Connected ✅' if pb_ok else 'Unavailable ❌'}\n"
            f"• Supabase Storage ({SUPABASE_BUCKET}): {'Configured ✅' if sb_configured else 'Not Configured ⚠️'}"
        )

    @bot.on(events.NewMessage(pattern=r"^/(?:json|generate)\s*(.*)"))
    async def handle_json(event):
        anime_id = event.pattern_match.group(1).strip()
        if not anime_id:
            await event.reply("⚠️ Usage: `/json <anime_id>` (e.g. `/json 40969` or `/json 63140`)")
            return

        status_msg = await event.reply(f"🔍 Fetching episodes for `{anime_id}` from PocketBase...")
        payload, count = build_anime_json(anime_id, pb)

        if count == 0:
            await status_msg.edit(f"❌ No episodes found in PocketBase for Anime ID: `{anime_id}`.")
            return

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=2)
            tmp_path = Path(tmp.name)

        rename_path = tmp_path.parent / f"{anime_id}.json"
        tmp_path.rename(rename_path)

        # Upload to Supabase Storage
        sb_success, sb_msg = upload_to_supabase(anime_id, payload)
        sb_status_text = "✅ Uploaded to Supabase Storage" if sb_success else f"⚠️ Supabase: {sb_msg}"

        # Send document to user
        caption = (
            f"🎬 **Anime ID**: `{anime_id}`\n"
            f"📦 **Episodes**: {count}\n"
            f"☁️ **Supabase**: {sb_status_text}"
        )
        await event.reply(file=str(rename_path), message=caption)
        await status_msg.delete()

        rename_path.unlink(missing_ok=True)

    await bot.run_until_disconnected()


def main():
    parser = argparse.ArgumentParser(description="JSON Generator & Supabase Sync Bot")
    parser.add_argument("--generate", type=str, help="Generate JSON for specific anime_id and upload via CLI")
    args = parser.parse_args()

    if args.generate:
        run_cli_generation(args.generate)
    else:
        import asyncio
        asyncio.run(start_telegram_bot())


if __name__ == "__main__":
    main()
