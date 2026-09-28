import os
import sys
import time
import argparse
import logging
import subprocess
import shutil
from typing import Optional
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
logger = logging.getLogger("subsplease_uploader")

CONVEX_URL = os.environ.get("CONVEX_URL", "").strip()
TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_PIPELINE_BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_STRING_SESSION = os.environ.get("TELEGRAM_STRING_SESSION_1", "").strip() or os.environ.get("TELEGRAM_STRING_SESSION", "").strip()
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_SUBSPLEASE", "").strip()

DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "./downloads_sp"))
TWO_GB_BYTES = 2000 * 1024 * 1024


def validate_environment():
    missing = []
    if not CONVEX_URL: missing.append("CONVEX_URL")
    if not TELEGRAM_API_ID: missing.append("TELEGRAM_API_ID")
    if not TELEGRAM_API_HASH: missing.append("TELEGRAM_API_HASH")
    if not TELEGRAM_BOT_TOKEN and not TELEGRAM_STRING_SESSION:
        missing.append("TELEGRAM_PIPELINE_BOT_TOKEN (or TELEGRAM_STRING_SESSION_1)")
    if not TELEGRAM_CHANNEL_ID: missing.append("TELEGRAM_CHANNEL_SUBSPLEASE")

    if missing:
        logger.critical(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def format_bytes(size_bytes: int) -> str:
    if not size_bytes:
        return "Unknown size"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def download_with_aria2(magnet_or_url: str, output_dir: Path, timeout_seconds: int = 1200) -> Optional[Path]:
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
        magnet_or_url
    ]
    logger.info("Starting download via aria2c...")
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout_seconds)
        if proc.returncode != 0:
            logger.error(f"aria2c failed with return code {proc.returncode}:\n{proc.stdout}")
            return None
    except subprocess.TimeoutExpired:
        logger.error(f"Download timed out after {timeout_seconds}s")
        return None
    except Exception as e:
        logger.error(f"Failed to execute aria2c: {e}")
        return None

    video_extensions = {".mp4", ".mkv", ".webm", ".avi"}
    candidates = [f for f in output_dir.glob("**/*") if f.is_file() and f.suffix.lower() in video_extensions]
    if candidates:
        candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
        return candidates[0]

    return None


async def run_uploader(single_video: bool = False):
    validate_environment()
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    convex_client = ConvexClient(CONVEX_URL)
    pending = convex_client.query("subsplease:getPendingReleases", {"limit": 50})

    if not pending:
        logger.info("No pending SubsPlease releases to upload.")
        print("RESULT_HAS_MORE_VIDEOS=false")
        return

    logger.info(f"Found {len(pending)} pending SubsPlease releases.")

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

    processed_count = 0
    has_more = False

    for item in pending:
        title = item.get("title", "Untitled")
        category = item.get("category", "")
        link = item.get("link", "")
        file_size = item.get("fileSize") or 0

        logger.info(f"Processing: {title} | Size: {format_bytes(file_size)}")

        if file_size > TWO_GB_BYTES:
            logger.info(f"File size exceeds 2GB limit ({format_bytes(file_size)}). Sending text message...")
            caption = (
                f"🎬 **{title}**\n\n"
                f"📁 **Category**: {category or 'Anime'}\n"
                f"💾 **Size**: {format_bytes(file_size)} (> 2GB Limit)\n\n"
                f"🔗 **Download Link**:\n`{link}`"
            )
            msg = await client.send_message(target_channel, caption)
            convex_client.mutation("subsplease:updateUploadStatus", {
                "link": link,
                "telegramMessageId": str(msg.id),
                "telegramFileUniqueId": "N/A",
                "status": "uploaded_text"
            })
            logger.info(f"Successfully posted text message for {title} (Message ID: {msg.id})")
            processed_count += 1
            if single_video:
                has_more = len(pending) > 1
                break
            continue

        item_dl_dir = DOWNLOAD_DIR / str(int(time.time()))
        downloaded_file = download_with_aria2(link, item_dl_dir)

        if not downloaded_file:
            logger.error(f"Download failed for {title}. Skipping without sending text message.")
            shutil.rmtree(item_dl_dir, ignore_errors=True)
            if single_video:
                has_more = len(pending) > 1
                break
            continue

        actual_size = downloaded_file.stat().st_size
        if actual_size > TWO_GB_BYTES:
            logger.info(f"Downloaded file exceeds 2GB ({format_bytes(actual_size)}). Sending text message.")
            caption = (
                f"🎬 **{title}**\n\n"
                f"📁 **Category**: {category or 'Anime'}\n"
                f"💾 **Size**: {format_bytes(actual_size)} (> 2GB Limit)\n\n"
                f"🔗 **Download Link**:\n`{link}`"
            )
            msg = await client.send_message(target_channel, caption)
            convex_client.mutation("subsplease:updateUploadStatus", {
                "link": link,
                "telegramMessageId": str(msg.id),
                "telegramFileUniqueId": "N/A",
                "status": "uploaded_text"
            })
            shutil.rmtree(item_dl_dir, ignore_errors=True)
            processed_count += 1
            if single_video:
                has_more = len(pending) > 1
                break
            continue

        logger.info(f"Uploading {downloaded_file.name} ({format_bytes(actual_size)}) to Telegram...")
        caption = f"🎬 **{title}**\n💾 Size: {format_bytes(actual_size)}"

        def progress_cb(current, total):
            pct = (current / total) * 100 if total else 0
            if current == total or int(pct) % 25 == 0:
                logger.info(f"Uploading: {current}/{total} bytes ({pct:.1f}%)")

        try:
            sent_msg = await client.send_file(
                target_channel,
                downloaded_file,
                caption=caption,
                supports_streaming=True,
                progress_callback=progress_cb
            )
            file_unique_id = ""
            if sent_msg.media and hasattr(sent_msg.media, "document") and sent_msg.media.document:
                file_unique_id = str(sent_msg.media.document.id)

            convex_client.mutation("subsplease:updateUploadStatus", {
                "link": link,
                "telegramMessageId": str(sent_msg.id),
                "telegramFileUniqueId": file_unique_id,
                "status": "uploaded_file"
            })
            logger.info(f"Uploaded file {title} to Message ID {sent_msg.id}")
            processed_count += 1
        except Exception as e:
            logger.error(f"Failed to upload {title} to Telegram: {e}")
        finally:
            shutil.rmtree(item_dl_dir, ignore_errors=True)

        if single_video:
            has_more = len(pending) > 1
            break

    await client.disconnect()

    if single_video:
        print(f"RESULT_HAS_MORE_VIDEOS={'true' if has_more else 'false'}")
    else:
        print(f"RESULT_PROCESSED_COUNT={processed_count}")


def main():
    parser = argparse.ArgumentParser(description="SubsPlease Uploader Worker")
    parser.add_argument("--single-video", action="store_true", help="Process 1 video workload and exit")
    args = parser.parse_args()

    import asyncio
    asyncio.run(run_uploader(single_video=args.single_video))


if __name__ == "__main__":
    main()
