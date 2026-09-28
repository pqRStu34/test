import os
import sys
import time
import argparse
import logging
import subprocess
import shutil
import requests
from typing import Optional, Tuple, List
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from convex import ConvexClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("tsukihime_sync")

CONVEX_URL = os.environ.get("CONVEX_URL", "").strip()
TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_PIPELINE_BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_STRING_SESSION = os.environ.get("TELEGRAM_STRING_SESSION_2", "").strip() or os.environ.get("TELEGRAM_STRING_SESSION", "").strip()
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_TSUKIHIME", "").strip()

TSUKIHIME_GROUP_ID = os.environ.get("TSUKIHIME_GROUP_ID", "12")
TSUKIHIME_API_BASE = "https://api.tsukihime.org/v1"
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "./downloads_th"))
TWO_GB_BYTES = 2000 * 1024 * 1024
MIN_SEEDERS = int(os.environ.get("MIN_SEEDERS", "10"))


def validate_environment():
    missing = []
    if not CONVEX_URL: missing.append("CONVEX_URL")
    if not TELEGRAM_API_ID: missing.append("TELEGRAM_API_ID")
    if not TELEGRAM_API_HASH: missing.append("TELEGRAM_API_HASH")
    if not TELEGRAM_BOT_TOKEN and not TELEGRAM_STRING_SESSION:
        missing.append("TELEGRAM_PIPELINE_BOT_TOKEN (or TELEGRAM_STRING_SESSION_2)")
    if not TELEGRAM_CHANNEL_ID: missing.append("TELEGRAM_CHANNEL_TSUKIHIME")

    if missing:
        logger.critical(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def format_bytes(size_bytes: int) -> str:
    if not size_bytes: return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0: return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def fetch_tsukihime_releases(limit: int = 100, offset: int = 0) -> Tuple[List[dict], int]:
    url = f"{TSUKIHIME_API_BASE}/groups/{TSUKIHIME_GROUP_ID}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, params={"limit": limit, "offset": offset}, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total") or 0
        releases = data.get("results", []) or data.get("releases", []) or data.get("torrents", []) or []
        items = []
        for r in releases:
            name = r.get("name") or r.get("title") or "Unknown"
            anime_meta = r.get("anime") or {}
            category = anime_meta.get("english_title") or anime_meta.get("title") or r.get("anime_title") or "Anime"
            link = r.get("download_url") or r.get("torrent_url") or r.get("magnet") or ""
            if not link and r.get("btih"):
                link = f"magnet:?xt=urn:btih:{r['btih']}&dn={name}"
            size = r.get("totalsize") or r.get("size") or 0
            seeders = r.get("max_seeders") or r.get("seeders") or 0

            if link:
                items.append({
                    "title": name,
                    "category": category,
                    "link": link,
                    "fileSize": size if size > 0 else None,
                    "seeders": seeders
                })
        return items, total
    except Exception as e:
        logger.error(f"Error fetching Tsukihime releases at offset {offset}: {e}")
        return [], 0


def scan_all_tsukihime_releases(convex_client: ConvexClient):
    offset = 0
    page_limit = 100
    total_added = 0
    total_records = 0

    logger.info("Scanning all Tsukihime Group 12 releases into Convex...")
    while True:
        items, total = fetch_tsukihime_releases(limit=page_limit, offset=offset)
        if not items:
            break
        total_records = total
        added = convex_client.mutation("tsukihime:addReleases", {"releases": items})
        total_added += added
        offset += len(items)
        logger.info(f"Ingested offset {offset}/{total_records} (New in batch: {added}, Total new: {total_added})")
        if offset >= total_records or len(items) < page_limit:
            break
        time.sleep(0.3)

    logger.info(f"Scan complete. Total releases in group: {total_records}. Newly added to Convex: {total_added}.")


def download_with_aria2(url_or_magnet: str, output_dir: Path, timeout: int = 1200) -> Optional[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c",
        "--seed-time=0",
        "--max-connection-per-server=16",
        "--split=16",
        "--summary-interval=10",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--dir", str(output_dir),
        url_or_magnet
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        if proc.returncode != 0:
            return None
    except Exception:
        return None

    video_exts = {".mp4", ".mkv", ".webm"}
    files = [f for f in output_dir.glob("**/*") if f.is_file() and f.suffix.lower() in video_exts]
    if files:
        files.sort(key=lambda x: x.stat().st_size, reverse=True)
        return files[0]
    return None


async def run_sync(single_video: bool = False, scan_all: bool = False):
    validate_environment()
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    convex_client = ConvexClient(CONVEX_URL)

    if scan_all:
        scan_all_tsukihime_releases(convex_client)
    else:
        releases, _ = fetch_tsukihime_releases(limit=100, offset=0)
        if releases:
            new_count = convex_client.mutation("tsukihime:addReleases", {"releases": releases})
            logger.info(f"Ingested {new_count} new Tsukihime releases into Convex.")

    pending = convex_client.query("tsukihime:getPendingReleases", {"limit": 50})
    if not pending:
        logger.info("No pending Tsukihime releases to upload.")
        print("RESULT_HAS_MORE_VIDEOS=false")
        return

    try:
        channel_id = int(TELEGRAM_CHANNEL_ID)
    except ValueError:
        channel_id = TELEGRAM_CHANNEL_ID

    if TELEGRAM_BOT_TOKEN:
        client = TelegramClient(StringSession(), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        await client.start(bot_token=TELEGRAM_BOT_TOKEN)
    else:
        client = TelegramClient(StringSession(TELEGRAM_STRING_SESSION), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        await client.start()

    logger.info("Connected to Telegram successfully.")
    target_channel = await client.get_entity(channel_id)

    has_more = False

    for item in pending:
        title = item.get("title", "Untitled")
        category = item.get("category", "")
        link = item.get("link", "")
        file_size = item.get("fileSize") or 0
        seeders = item.get("seeders") or 0

        if seeders and seeders < MIN_SEEDERS:
            logger.info(f"Skipping {title}: seeders {seeders} < {MIN_SEEDERS}")
            convex_client.mutation("tsukihime:updateUploadStatus", {
                "link": link,
                "status": "skipped"
            })
            continue

        if file_size > TWO_GB_BYTES:
            logger.info(f"File size {format_bytes(file_size)} exceeds 2GB. Sending text link...")
            caption = (
                f"🎬 **{title}**\n\n"
                f"📁 **Category**: {category or 'Anime'}\n"
                f"💾 **Size**: {format_bytes(file_size)} (> 2GB Limit)\n\n"
                f"🔗 **Download Link**:\n`{link}`"
            )
            msg = await client.send_message(target_channel, caption)
            convex_client.mutation("tsukihime:updateUploadStatus", {
                "link": link,
                "telegramMessageId": str(msg.id),
                "telegramFileUniqueId": "N/A",
                "status": "uploaded_text"
            })
            if single_video:
                has_more = len(pending) > 1
                break
            continue

        item_dir = DOWNLOAD_DIR / str(int(time.time()))
        downloaded = download_with_aria2(link, item_dir)

        if not downloaded:
            logger.error(f"Download failed for {title}. Skipping without sending text message.")
            shutil.rmtree(item_dir, ignore_errors=True)
            if single_video:
                has_more = len(pending) > 1
                break
            continue

        actual_size = downloaded.stat().st_size
        caption = f"🎬 **{title}**\n💾 Size: {format_bytes(actual_size)}"

        try:
            sent_msg = await client.send_file(target_channel, downloaded, caption=caption, supports_streaming=True)
            file_unique_id = ""
            if sent_msg.media and hasattr(sent_msg.media, "document") and sent_msg.media.document:
                file_unique_id = str(sent_msg.media.document.id)

            convex_client.mutation("tsukihime:updateUploadStatus", {
                "link": link,
                "telegramMessageId": str(sent_msg.id),
                "telegramFileUniqueId": file_unique_id,
                "status": "uploaded_file"
            })
            logger.info(f"Uploaded {title} to Message ID {sent_msg.id}")
        except Exception as e:
            logger.error(f"Error uploading {title}: {e}")
        finally:
            shutil.rmtree(item_dir, ignore_errors=True)

        if single_video:
            has_more = len(pending) > 1
            break

    await client.disconnect()

    if single_video:
        print(f"RESULT_HAS_MORE_VIDEOS={'true' if has_more else 'false'}")


def main():
    parser = argparse.ArgumentParser(description="Tsukihime Sync Worker")
    parser.add_argument("--single-video", action="store_true", help="Process 1 video workload and exit")
    parser.add_argument("--scan-all", action="store_true", help="Scan and ingest all 13,473 releases into Convex")
    args = parser.parse_args()

    import asyncio
    asyncio.run(run_sync(single_video=args.single_video, scan_all=args.scan_all))


if __name__ == "__main__":
    main()
