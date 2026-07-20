import os
import sys
import time
import logging
import argparse
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename, DocumentAttributeVideo
from telethon.errors import AuthKeyDuplicatedError
import internetarchive as ia

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("tg_ia_sync")

# Environment Configurations
TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
TELEGRAM_STRING_SESSION = os.environ.get("TELEGRAM_STRING_SESSION", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

IA_ACCESS_KEY = os.environ.get("IA_ACCESS_KEY", "")
IA_SECRET_KEY = os.environ.get("IA_SECRET_KEY", "")
IA_ITEM_IDENTIFIER = os.environ.get("IA_ITEM_IDENTIFIER", "")

COMPLETED_TXT_PATH = Path(os.environ.get("COMPLETED_TXT_PATH", "completed_messages.txt"))
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "./downloads"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))
SINGLE_VIDEO = os.environ.get("SINGLE_VIDEO", "false").lower() in ("true", "1", "yes")


def validate_environment():
    """Validates that all required secrets and credentials are configured."""
    missing = []
    if not TELEGRAM_API_ID: missing.append("TELEGRAM_API_ID")
    if not TELEGRAM_API_HASH: missing.append("TELEGRAM_API_HASH")
    if not TELEGRAM_STRING_SESSION: missing.append("TELEGRAM_STRING_SESSION")
    if not TELEGRAM_CHANNEL_ID: missing.append("TELEGRAM_CHANNEL_ID")
    if not IA_ACCESS_KEY: missing.append("IA_ACCESS_KEY")
    if not IA_SECRET_KEY: missing.append("IA_SECRET_KEY")
    if not IA_ITEM_IDENTIFIER: missing.append("IA_ITEM_IDENTIFIER")

    if missing:
        logger.critical(f"Missing required environment secrets: {', '.join(missing)}")
        sys.exit(1)


def load_completed_ids(file_path: Path) -> set:
    completed = set()
    if file_path.exists():
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    msg_id = line.split("|")[0].strip()
                    completed.add(msg_id)
        logger.info(f"Loaded {len(completed)} completed message IDs from {file_path}")
    else:
        logger.info(f"Tracking file {file_path} not found. Creating new one.")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# Telegram to Internet Archive completed message IDs\n")
    return completed


def append_completed_id(file_path: Path, msg_id: int, file_name: str):
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"{msg_id} | {file_name}\n")
        f.flush()
        os.fsync(f.fileno())


def is_mp4_video(message) -> bool:
    if not message.media:
        return False

    if message.video:
        mime = message.video.mime_type or ""
        if mime.lower() == "video/mp4" or not mime:
            return True

    if isinstance(message.media, MessageMediaDocument) and message.media.document:
        doc = message.media.document
        mime = (doc.mime_type or "").lower()
        if mime == "video/mp4":
            return True

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename) and attr.file_name.lower().endswith(".mp4"):
                return True
            if isinstance(attr, DocumentAttributeVideo) and (mime.startswith("video/") or not mime):
                return True

    return False


def upload_to_internet_archive(file_path: Path, message_id: int, message_caption: str):
    metadata = {
        "mediatype": "movies",
        "collection": "opensource_movies",
        "description": f"Source: Telegram Channel {TELEGRAM_CHANNEL_ID}, Message ID: {message_id}.\n{message_caption or ''}".strip(),
        "original_message_id": str(message_id)
    }

    logger.info(f"Uploading {file_path.name} to Internet Archive item '{IA_ITEM_IDENTIFIER}'...")
    
    r = ia.upload(
        identifier=IA_ITEM_IDENTIFIER,
        files=[str(file_path)],
        metadata=metadata,
        access_key=IA_ACCESS_KEY,
        secret_key=IA_SECRET_KEY,
        verbose=False,
        retries=5
    )

    if r and all(resp.status_code == 200 for resp in r):
        logger.info(f"Successfully uploaded {file_path.name} to Internet Archive!")
        return True
    else:
        logger.error(f"Failed to upload {file_path.name} to Internet Archive. Response: {r}")
        return False


async def run_sync_loop(single_video=False, once=False):
    validate_environment()

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    completed_ids = load_completed_ids(COMPLETED_TXT_PATH)

    try:
        channel_id = int(TELEGRAM_CHANNEL_ID)
    except ValueError:
        channel_id = TELEGRAM_CHANNEL_ID

    logger.info(f"Initializing Telegram Client for channel: {channel_id}")
    client = TelegramClient(StringSession(TELEGRAM_STRING_SESSION), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
    
    try:
        await client.start()
    except AuthKeyDuplicatedError:
        logger.critical("TELEGRAM_STRING_SESSION was used simultaneously by another location/run and has been revoked by Telegram!")
        logger.critical("Please re-run `python generate_session.py` locally and update TELEGRAM_STRING_SESSION in GitHub Secrets.")
        sys.exit(1)

    logger.info("Connected to Telegram successfully.")

    channel = await client.get_entity(channel_id)
    logger.info(f"Target Channel: {getattr(channel, 'title', channel_id)}")

    video_synced_in_this_run = False

    logger.info("--- Scanning channel for un-synced MP4 videos ---")
    processed_count = 0

    async for message in client.iter_messages(channel, reverse=True):
        processed_count += 1
        msg_id_str = str(message.id)

        if msg_id_str in completed_ids:
            continue

        if is_mp4_video(message):
            logger.info(f"Found next un-synced MP4 video in Message ID: {message.id}")

            def progress_callback(current, total):
                percent = (current / total) * 100 if total else 0
                if current == total or int(percent) % 25 == 0:
                    logger.info(f"Downloading Msg {message.id}: {current}/{total} bytes ({percent:.1f}%)")

            try:
                downloaded_path_str = await message.download_media(
                    file=DOWNLOAD_DIR,
                    progress_callback=progress_callback
                )

                if not downloaded_path_str:
                    continue

                downloaded_file = Path(downloaded_path_str)

                if downloaded_file.suffix.lower() != ".mp4":
                    new_name = downloaded_file.with_suffix(".mp4")
                    downloaded_file.rename(new_name)
                    downloaded_file = new_name

                caption = message.text or message.message or ""
                upload_success = upload_to_internet_archive(downloaded_file, message.id, caption)

                if upload_success:
                    append_completed_id(COMPLETED_TXT_PATH, message.id, downloaded_file.name)
                    completed_ids.add(msg_id_str)
                    video_synced_in_this_run = True
                    logger.info(f"Marked Message ID {message.id} as complete in {COMPLETED_TXT_PATH}")

                if downloaded_file.exists():
                    downloaded_file.unlink()
                    logger.info(f"Deleted local file: {downloaded_file.name}")

                if single_video or SINGLE_VIDEO:
                    logger.info(f"Single video workload mode enabled. Finished processing Message ID {message.id}.")
                    break

            except AuthKeyDuplicatedError:
                logger.critical("Session duplicated error detected! Exiting cleanly.")
                sys.exit(1)
            except Exception as e:
                logger.error(f"Error processing Message ID {message.id}: {e}", exc_info=True)
                try:
                    for leftover in DOWNLOAD_DIR.glob(f"*{message.id}*"):
                        leftover.unlink()
                except Exception:
                    pass

    await client.disconnect()

    if video_synced_in_this_run:
        print("RESULT_HAS_MORE_VIDEOS=true")
    else:
        print("RESULT_HAS_MORE_VIDEOS=false")
        logger.info("No more un-synced videos found in channel. Work complete!")


def main():
    parser = argparse.ArgumentParser(description="Telegram to Internet Archive Sync Worker")
    parser.add_argument("--single-video", action="store_true", help="Process exactly 1 video per run and exit")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    args = parser.parse_args()

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run_sync_loop(single_video=args.single_video, once=args.once))
    except KeyboardInterrupt:
        logger.info("Worker stopped by user.")


if __name__ == "__main__":
    main()
