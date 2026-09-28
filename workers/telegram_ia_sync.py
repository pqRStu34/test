import os
import sys
import time
import re
import hashlib
import logging
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageMediaDocument,
    DocumentAttributeFilename,
    DocumentAttributeVideo
)
import internetarchive as ia

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketbase.client import PocketBaseClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("tg_ia_sync")

TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_STRING_SESSION = os.environ.get("TELEGRAM_STRING_SESSION_3", "").strip() or os.environ.get("TELEGRAM_STRING_SESSION", "").strip()
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_IA_SOURCE", "").strip() or os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()

IA_ACCESS_KEY = os.environ.get("IA_ACCESS_KEY", "").strip()
IA_SECRET_KEY = os.environ.get("IA_SECRET_KEY", "").strip()
IA_ITEM_IDENTIFIER = os.environ.get("IA_ITEM_IDENTIFIER", "").strip()

FILESTREAM_BASE_URL = os.environ.get("FILESTREAM_BASE_URL", "").strip().rstrip("/")
POCKETBASE_URL = os.environ.get("POCKETBASE_URL", "http://127.0.0.1:8090").strip()

DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "./downloads_ia"))
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", str(5 * 3600 + 15 * 60)))

FILENAME_PATTERN = re.compile(
    r'^(?P<anime_id>.+?)_(?P<lang>[a-zA-Z]{2,4})_(?P<quality>\d+p|4k|2160p|360p)_(?P<ep>\d+(?:\.\d+)?)$'
)


def validate_environment():
    missing = []
    if not TELEGRAM_API_ID: missing.append("TELEGRAM_API_ID")
    if not TELEGRAM_API_HASH: missing.append("TELEGRAM_API_HASH")
    if not TELEGRAM_BOT_TOKEN and not TELEGRAM_STRING_SESSION:
        missing.append("TELEGRAM_STRING_SESSION_3 (or TELEGRAM_STRING_SESSION / TELEGRAM_BOT_TOKEN)")
    if not TELEGRAM_CHANNEL_ID: missing.append("TELEGRAM_CHANNEL_IA_SOURCE")
    if not IA_ACCESS_KEY: missing.append("IA_ACCESS_KEY")
    if not IA_SECRET_KEY: missing.append("IA_SECRET_KEY")
    if not IA_ITEM_IDENTIFIER: missing.append("IA_ITEM_IDENTIFIER")
    if not FILESTREAM_BASE_URL: missing.append("FILESTREAM_BASE_URL")

    if missing:
        logger.critical(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def parse_filename_metadata(filename: str) -> dict:
    base_name = filename.rsplit('.', 1)[0]
    m = FILENAME_PATTERN.match(base_name)
    if m:
        ep_raw = m.group('ep')
        return {
            "anime_id": m.group('anime_id'),
            "language": m.group('lang').upper(),
            "quality": m.group('quality').lower(),
            "episode_number": int(ep_raw) if '.' not in ep_raw else float(ep_raw)
        }
    return {
        "anime_id": base_name,
        "language": "JPN",
        "quality": "1080p",
        "episode_number": 1
    }


def compute_filestream_hash(file_name: str, file_size: int, mime_type: str, file_id: int) -> str:
    raw = (
        file_name.encode('utf-8') +
        str(file_size).encode('utf-8') +
        mime_type.encode('utf-8') +
        str(file_id).encode('utf-8')
    )
    return hashlib.md5(raw).hexdigest()[:6]


def fetch_existing_ia_files(item_id: str) -> set:
    url = f"https://archive.org/metadata/{item_id}/files"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            files_data = resp.json().get("result", [])
            existing = set()
            for f in files_data:
                name = str(f.get("name", "")).strip().lower()
                if name:
                    existing.add(name)
                    existing.add(Path(name).name.lower())
                    existing.add(name.replace("\\", "/"))
            return existing
    except Exception as e:
        logger.warning(f"Could not fetch IA metadata: {e}")
    return set()


def upload_to_ia(file_path: Path, message_id: int) -> bool:
    metadata = {
        "mediatype": "movies",
        "collection": "opensource_movies",
        "original_message_id": str(message_id)
    }
    try:
        r = ia.upload(
            identifier=IA_ITEM_IDENTIFIER,
            files=[str(file_path)],
            metadata=metadata,
            access_key=IA_ACCESS_KEY,
            secret_key=IA_SECRET_KEY,
            verbose=False,
            retries=5
        )
        return r and all(resp.status_code == 200 for resp in r)
    except Exception as e:
        logger.error(f"Error uploading to Internet Archive: {e}")
        return False


async def run_sync_loop(single_video: bool = False):
    validate_environment()
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    pb = PocketBaseClient(base_url=POCKETBASE_URL)
    pb.ensure_collection()
    completed_ids = pb.get_completed_message_ids()

    ia_existing_files = fetch_existing_ia_files(IA_ITEM_IDENTIFIER)

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

    video_synced_in_this_run = False
    more_videos_exist = False

    async for message in client.iter_messages(target_channel, reverse=True):
        if time.time() - start_time > MAX_RUNTIME_SECONDS:
            logger.info("Approaching execution limit. Gracefully breaking batch.")
            more_videos_exist = True
            break

        msg_id_str = str(message.id)
        if msg_id_str in completed_ids:
            continue

        if not message.media:
            continue

        doc = getattr(message.media, "document", None)
        if not doc:
            continue

        filename = ""
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break

        if not filename:
            filename = f"video_{message.id}.mp4"

        meta = parse_filename_metadata(filename)
        short_hash = compute_filestream_hash(filename, doc.size, doc.mime_type or "video/mp4", doc.id)
        stream_link = f"{FILESTREAM_BASE_URL}/stream/{message.id}?hash={short_hash}"

        thumbnail_id = ""
        if hasattr(doc, "thumbs") and doc.thumbs:
            thumbnail_id = getattr(doc.thumbs[0], "file_id", "") or f"thumb_{message.id}"

        is_mp4 = filename.lower().endswith(".mp4") or (doc.mime_type or "").lower() == "video/mp4"
        ia_synced = False

        if is_mp4:
            if filename.lower() in ia_existing_files:
                logger.info(f"File '{filename}' already exists on Internet Archive. Skipping upload.")
                ia_synced = True
            else:
                logger.info(f"Downloading MP4 video for Msg {message.id} ({filename})...")
                downloaded_str = await message.download_media(file=DOWNLOAD_DIR)
                if downloaded_str:
                    dl_path = Path(downloaded_str)
                    upload_success = upload_to_ia(dl_path, message.id)
                    if upload_success:
                        ia_synced = True
                        ia_existing_files.add(filename.lower())
                        logger.info(f"Successfully uploaded {filename} to Internet Archive.")
                    if dl_path.exists():
                        dl_path.unlink()
        else:
            logger.info(f"File '{filename}' is non-MP4. Recording in PocketBase only.")

        pb.record_completed_sync({
            "message_id": msg_id_str,
            "file_name": filename,
            "anime_id": meta["anime_id"],
            "language": meta["language"],
            "quality": meta["quality"],
            "episode_number": meta["episode_number"],
            "thumbnail": thumbnail_id,
            "stream_link": stream_link,
            "is_mp4": is_mp4,
            "ia_synced": ia_synced,
            "channel_id": str(channel_id),
            "ia_item": IA_ITEM_IDENTIFIER,
            "file_size": doc.size,
            "synced_at": datetime.now(timezone.utc).isoformat()
        })
        completed_ids.add(msg_id_str)
        video_synced_in_this_run = True

        if single_video:
            more_videos_exist = True
            break

    await client.disconnect()

    if more_videos_exist or video_synced_in_this_run:
        print("RESULT_HAS_MORE_VIDEOS=true")
    else:
        print("RESULT_HAS_MORE_VIDEOS=false")


def main():
    parser = argparse.ArgumentParser(description="Telegram to Internet Archive Worker")
    parser.add_argument("--single-video", action="store_true", help="Process 1 video and exit")
    args = parser.parse_args()

    import asyncio
    asyncio.run(run_sync_loop(single_video=args.single_video))


if __name__ == "__main__":
    main()
