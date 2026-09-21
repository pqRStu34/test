import os
import sys
import time
import json
import sqlite3
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import urllib.request
import urllib.parse
import urllib.error
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import AuthKeyDuplicatedError

# Load environment variables
load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("yameii_sync")

# Environment Configurations
TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "-1004492582679").strip()

TSUKIHIME_GROUP_ID = int(os.environ.get("TSUKIHIME_GROUP_ID", "12"))
TSUKIHIME_API_BASE = "https://api.tsukihime.org/v1"

DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", "yameii.db"))
SQL_DUMP_PATH = Path(os.environ.get("SQL_DUMP_PATH", "yameii.sql"))
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "./downloads"))

# 2 GB limit in bytes (2000 MB for MTProto bot limit)
TWO_GB_BYTES = 2000 * 1024 * 1024
SIZE_LIMIT_BYTES = int(os.environ.get("SIZE_LIMIT_BYTES", str(TWO_GB_BYTES)))
MIN_SEEDERS = int(os.environ.get("MIN_SEEDERS", "10"))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def validate_environment(dry_run=False):
    """Validates required credentials unless running dry-run mode."""
    if dry_run:
        return
    missing = []
    if not TELEGRAM_API_ID:
        missing.append("TELEGRAM_API_ID")
    if not TELEGRAM_API_HASH:
        missing.append("TELEGRAM_API_HASH")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHANNEL_ID:
        missing.append("TELEGRAM_CHANNEL_ID")

    if missing:
        logger.critical(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def init_database(db_path: Path) -> sqlite3.Connection:
    """Initializes SQLite database and tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            torrent_id INTEGER PRIMARY KEY,
            name TEXT,
            anime_id INTEGER,
            anime_title TEXT,
            anime_english_title TEXT,
            anime_thumbnail TEXT,
            episode_no REAL,
            totalsize INTEGER,
            filecount INTEGER,
            btih TEXT,
            nyaa_id INTEGER,
            nekobt_id INTEGER,
            max_seeders INTEGER,
            primary_filename TEXT,
            primary_file_size INTEGER,
            direct_links TEXT,
            files_json TEXT,
            trackers_json TEXT,
            sync_type TEXT,
            sync_status TEXT,
            telegram_channel_id TEXT,
            telegram_message_id INTEGER,
            synced_at TEXT
        );
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_status ON videos(sync_status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anime_id ON videos(anime_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_btih ON videos(btih);")
    conn.commit()
    return conn


def export_database_to_sql(db_path: Path, sql_path: Path):
    """Dumps SQLite database to a plain SQL file for bot serving and version control."""
    try:
        conn = sqlite3.connect(str(db_path))
        with open(sql_path, "w", encoding="utf-8") as f:
            f.write(f"-- Tsukihime Yameii Sync SQL Database Dump\n")
            f.write(f"-- Exported: {datetime.now(timezone.utc).isoformat()}\n\n")
            for line in conn.iterdump():
                f.write(f"{line}\n")
        conn.close()
        logger.info(f"Exported SQL dump to {sql_path} ({sql_path.stat().st_size} bytes)")
    except Exception as e:
        logger.error(f"Failed to export SQL dump: {e}")


def get_synced_torrent_ids(conn: sqlite3.Connection) -> set:
    """Returns set of torrent IDs that have already been completed or skipped."""
    cursor = conn.cursor()
    cursor.execute("SELECT torrent_id FROM videos WHERE sync_status IN ('COMPLETED', 'SKIPPED')")
    rows = cursor.fetchall()
    return {row[0] for row in rows}


def fetch_group_page(group_id: int, offset: int = 0, limit: int = 100, retries: int = 3) -> dict:
    """Fetches a page of releases for the group (newest to oldest)."""
    url = f"{TSUKIHIME_API_BASE}/groups/{group_id}?offset={offset}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except Exception as e:
            logger.warning(f"Error fetching group page offset {offset} (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
            else:
                raise


def fetch_torrent_details(torrent_id: int, retries: int = 3) -> dict:
    """Fetches detailed torrent information including files, links, and trackers."""
    url = f"{TSUKIHIME_API_BASE}/torrents/{torrent_id}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except Exception as e:
            logger.warning(f"Error fetching torrent {torrent_id} details (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
            else:
                raise


def format_size(size_bytes: int) -> str:
    """Formats bytes into human-readable size string."""
    if not size_bytes or size_bytes <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def extract_max_seeders(trackers: list) -> int:
    """Extracts the maximum seeder count across all trackers."""
    if not trackers:
        return 0
    return max((int(t.get("seeders") or 0) for t in trackers), default=0)


def build_text_message(torrent: dict, details: dict, primary_file: dict) -> str:
    """Constructs a clean Telegram text message with video info and formatted [Magnet] hyperlink."""
    anime = details.get("anime") or torrent.get("anime") or {}
    anime_title = anime.get("english_title") or anime.get("title") or torrent.get("name", "Unknown Anime")
    episode_no = torrent.get("episode_no")
    ep_str = f" - Episode {episode_no}" if episode_no is not None else ""
    torrent_name = torrent.get("name", "")
    filename = primary_file.get("filename") or torrent_name
    size_bytes = primary_file.get("size") or torrent.get("totalsize", 0)
    size_formatted = format_size(size_bytes)
    btih = torrent.get("btih") or details.get("btih", "")
    nyaa_id = torrent.get("nyaa_id") or details.get("nyaa_id")

    links = primary_file.get("links") or {}

    lines = [
        f"🎬 **{anime_title}{ep_str}**",
        "",
        f"📁 **File:** `{filename}`",
        f"📦 **Size:** `{size_formatted}` ({size_bytes:,} bytes)",
        f"🏷 **Release:** `{torrent_name}`",
        "",
        "🔗 **Download Links:**"
    ]

    for host, link in links.items():
        lines.append(f"• [{host}]({link})")

    if btih:
        lines.append(f"• 🧲 [Magnet](magnet:?xt=urn:btih:{btih}&dn={urllib.parse.quote(filename)})")

    if nyaa_id:
        lines.append(f"• [Nyaa.si](https://nyaa.si/view/{nyaa_id})")

    lines.append(f"• [Tsukihime Group Page](https://tsukihime.org/group/{TSUKIHIME_GROUP_ID}-yameii)")

    return "\n".join(lines)


def resolve_buzzheavier_link(url: str, timeout: int = 15) -> str:
    """Attempts to resolve direct BuzzHeavier download URL via HX-Request."""
    clean_url = url.rstrip("/")
    download_url = f"{clean_url}/download"
    req = urllib.request.Request(
        download_url,
        headers={
            "User-Agent": USER_AGENT,
            "HX-Request": "true",
            "Referer": url
        }
    )
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    try:
        resp = opener.open(req, timeout=timeout)
        hx_redirect = resp.headers.get("HX-Redirect")
        if hx_redirect:
            return hx_redirect
        return None
    except Exception as e:
        logger.debug(f"Could not resolve BuzzHeavier link {url}: {e}")
        return None


def download_video_file(torrent: dict, details: dict, primary_file: dict, output_dir: Path) -> Path:
    """
    Attempts to download video file locally using direct mirrors or aria2.
    Returns Path to downloaded file if successful, or None if failed.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = primary_file.get("filename") or f"{torrent.get('id')}.mkv"
    target_path = output_dir / filename

    links = primary_file.get("links") or {}

    # 1. Try BuzzHeavier direct resolution
    if "BuzzHeavier" in links:
        direct_url = resolve_buzzheavier_link(links["BuzzHeavier"])
        if direct_url:
            logger.info(f"Resolved BuzzHeavier direct URL: {direct_url[:80]}...")
            try:
                dl_req = urllib.request.Request(direct_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(dl_req, timeout=120) as resp, open(target_path, "wb") as f:
                    while chunk := resp.read(1024 * 1024):
                        f.write(chunk)
                if target_path.exists() and target_path.stat().st_size > 0:
                    logger.info(f"Successfully downloaded via BuzzHeavier: {target_path.name}")
                    return target_path
            except Exception as e:
                logger.warning(f"BuzzHeavier download stream failed: {e}")
                if target_path.exists():
                    target_path.unlink()

    # 2. Try aria2c if available and Nyaa .torrent / magnet exists
    nyaa_id = torrent.get("nyaa_id") or details.get("nyaa_id")
    btih = torrent.get("btih") or details.get("btih")
    torrent_url = f"https://nyaa.si/download/{nyaa_id}.torrent" if nyaa_id else None

    # Check if aria2c is installed on the runner
    aria2_path = None
    try:
        aria2_check = subprocess.run(["aria2c", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if aria2_check.returncode == 0:
            aria2_path = "aria2c"
    except Exception:
        pass

    if aria2_path and (torrent_url or btih):
        logger.info(f"Attempting download with aria2c for torrent ID {torrent.get('id')}...")
        source_arg = torrent_url if torrent_url else f"magnet:?xt=urn:btih:{btih}"
        cmd = [
            "aria2c",
            "--seed-time=0",
            "--max-connection-per-server=16",
            "--split=16",
            "--summary-interval=10",
            f"--dir={output_dir}",
            f"--timeout=180",
            source_arg
        ]
        try:
            res = subprocess.run(cmd, timeout=300)
            if res.returncode == 0:
                for candidate in output_dir.glob("*"):
                    if candidate.is_file() and candidate.suffix.lower() in [".mkv", ".mp4"]:
                        logger.info(f"Successfully downloaded with aria2c: {candidate.name}")
                        return candidate
        except Exception as e:
            logger.warning(f"aria2c download failed or timed out: {e}")

    logger.warning("Could not download video file locally via mirror or torrent.")
    return None


async def run_yameii_sync(single_video=False, dry_run=False, limit_per_run=1):
    """
    Main sync engine:
    1. Rechecks Tsukihime API starting from offset=0 (newest releases) on every run to prioritize new videos.
    2. Skips already processed releases.
    3. Skips releases with single-digit seeders (< 10).
    4. For releases > 2 GB: sends rich text message with formatted [Magnet] hyperlink and mirror links.
    5. For releases <= 2 GB: downloads and uploads as document (force_document=True) via MTProto.
    6. Stores full details into SQLite (yameii.db) and exports yameii.sql.
    """
    validate_environment(dry_run=dry_run)
    conn = init_database(DATABASE_PATH)
    synced_ids = get_synced_torrent_ids(conn)
    logger.info(f"Loaded {len(synced_ids)} previously processed torrent IDs from {DATABASE_PATH}")

    # Initialize Telegram client
    client = None
    channel = None
    if not dry_run:
        try:
            channel_id = int(TELEGRAM_CHANNEL_ID)
        except ValueError:
            channel_id = TELEGRAM_CHANNEL_ID

        logger.info(f"Connecting to Telegram MTProto session for channel: {channel_id}")
        client = TelegramClient(StringSession(), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        await client.start(bot_token=TELEGRAM_BOT_TOKEN)
        try:
            channel = await client.get_entity(channel_id)
        except Exception as e:
            if isinstance(channel_id, int) and channel_id > 0:
                channel = await client.get_entity(int(f"-100{channel_id}"))
            else:
                logger.critical(f"Failed to resolve channel entity: {e}")
                raise
        logger.info(f"Connected to Telegram channel: {getattr(channel, 'title', channel_id)}")

    processed_in_this_run = 0
    has_more_unprocessed = False

    # Start scanning from offset=0: Always prioritize newly released videos
    offset = 0
    page_limit = 100
    total_group_releases = 0

    logger.info(f"Checking for new releases at offset 0 (newest to oldest)...")

    scan_active = True
    while scan_active:
        page_data = fetch_group_page(TSUKIHIME_GROUP_ID, offset=offset, limit=page_limit)
        total_group_releases = page_data.get("total", 0)
        results = page_data.get("results", [])

        if not results:
            logger.info("No more results returned from Tsukihime API.")
            break

        logger.info(f"Page offset {offset}: received {len(results)} items (Group total: {total_group_releases})")

        for item in results:
            tid = item["id"]
            if tid in synced_ids:
                continue

            # We found an un-synced release! Fetch full details
            logger.info(f"Inspecting candidate un-synced torrent ID {tid}: {item.get('name')}")
            try:
                details = fetch_torrent_details(tid)
            except Exception as e:
                logger.error(f"Failed to fetch details for torrent {tid}: {e}")
                continue

            trackers = details.get("trackers", [])
            max_seeders = extract_max_seeders(trackers)

            # Rule: "if there is single digit seeder skip it"
            if max_seeders < MIN_SEEDERS:
                logger.info(f"Skipping Torrent ID {tid}: single digit seeders ({max_seeders} < {MIN_SEEDERS})")
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO videos (
                        torrent_id, name, anime_id, anime_title, anime_english_title, anime_thumbnail,
                        episode_no, totalsize, filecount, btih, nyaa_id, nekobt_id, max_seeders,
                        direct_links, files_json, trackers_json, sync_type, sync_status,
                        telegram_channel_id, telegram_message_id, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tid,
                    item.get("name"),
                    (item.get("anime") or {}).get("id"),
                    (item.get("anime") or {}).get("title"),
                    (item.get("anime") or {}).get("english_title"),
                    (item.get("anime") or {}).get("thumbnail"),
                    item.get("episode_no"),
                    item.get("totalsize"),
                    item.get("filecount"),
                    item.get("btih"),
                    item.get("nyaa_id"),
                    item.get("nekobt_id"),
                    max_seeders,
                    "{}",
                    json.dumps(details.get("files", [])),
                    json.dumps(trackers),
                    "SKIPPED_SINGLE_DIGIT_SEEDERS",
                    "SKIPPED",
                    str(TELEGRAM_CHANNEL_ID),
                    None,
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                synced_ids.add(tid)
                export_database_to_sql(DATABASE_PATH, SQL_DUMP_PATH)
                continue

            # Torrent has >= 10 seeders: Process workload
            files = details.get("files", [])
            primary_file = files[0] if files else {}
            file_size = primary_file.get("size") or item.get("totalsize", 0)
            file_name = primary_file.get("filename") or item.get("name")
            direct_links = primary_file.get("links") or {}
            btih = item.get("btih") or details.get("btih", "")

            telegram_msg_id = None
            sync_type = "TEXT_LINK"

            # Rule: "if any file size is above 2 gb send in a text messege with the file link instead ."
            is_above_2gb = file_size > SIZE_LIMIT_BYTES

            if is_above_2gb:
                logger.info(f"Torrent {tid} size ({format_size(file_size)}) is above 2 GB limit. Sending text message with links...")
                msg_text = build_text_message(item, details, primary_file)
                if not dry_run:
                    sent_msg = await client.send_message(channel, msg_text, parse_mode="md", link_preview=False)
                    telegram_msg_id = sent_msg.id
                sync_type = "TEXT_LINK"
            else:
                logger.info(f"Torrent {tid} size ({format_size(file_size)}) is <= 2 GB. Attempting download for document upload...")
                downloaded_file = None
                if not dry_run:
                    downloaded_file = download_video_file(item, details, primary_file, DOWNLOAD_DIR)

                if downloaded_file and downloaded_file.exists():
                    logger.info(f"Uploading {downloaded_file.name} ({format_size(downloaded_file.stat().st_size)}) as document to Telegram channel...")

                    def upload_progress(current, total):
                        pct = (current / total) * 100 if total else 0
                        if current == total or int(pct) % 25 == 0:
                            logger.info(f"Uploading {downloaded_file.name}: {current}/{total} bytes ({pct:.1f}%)")

                    anime_info = details.get("anime") or item.get("anime") or {}
                    anime_disp = anime_info.get("english_title") or anime_info.get("title") or item.get("name")
                    
                    caption_parts = [
                        f"🎬 **{anime_disp}**",
                        "",
                        f"📁 `{file_name}`",
                        f"📦 Size: `{format_size(file_size)}`"
                    ]
                    links_row = []
                    if btih:
                        links_row.append(f"🧲 [Magnet](magnet:?xt=urn:btih:{btih}&dn={urllib.parse.quote(file_name)})")
                    links_row.append(f"🔗 [Tsukihime](https://tsukihime.org/group/{TSUKIHIME_GROUP_ID}-yameii)")
                    caption_parts.append(" • ".join(links_row))
                    
                    caption = "\n".join(caption_parts)
                    if len(caption) > 1020:
                        caption = caption[:1017] + "..."

                    try:
                        sent_msg = await client.send_file(
                            channel,
                            file=downloaded_file,
                            caption=caption,
                            parse_mode="md",
                            force_document=True,
                            progress_callback=upload_progress
                        )
                        telegram_msg_id = sent_msg.id
                        sync_type = "DOCUMENT_UPLOAD"
                        logger.info(f"Successfully uploaded document to Telegram! Message ID: {telegram_msg_id}")
                    except Exception as upload_err:
                        logger.error(f"Error uploading document to Telegram: {upload_err}. Falling back to text message with links.")
                        msg_text = build_text_message(item, details, primary_file)
                        sent_msg = await client.send_message(channel, msg_text, parse_mode="md", link_preview=False)
                        telegram_msg_id = sent_msg.id
                        sync_type = "TEXT_LINK"
                    finally:
                        if downloaded_file.exists():
                            downloaded_file.unlink()
                            logger.info(f"Removed local file: {downloaded_file.name}")
                else:
                    logger.info(f"Video file download not available locally. Sending text message with file links...")
                    msg_text = build_text_message(item, details, primary_file)
                    if not dry_run:
                        sent_msg = await client.send_message(channel, msg_text, parse_mode="md", link_preview=False)
                        telegram_msg_id = sent_msg.id
                    sync_type = "TEXT_LINK"

            # Record completed video into SQLite database
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO videos (
                    torrent_id, name, anime_id, anime_title, anime_english_title, anime_thumbnail,
                    episode_no, totalsize, filecount, btih, nyaa_id, nekobt_id, max_seeders,
                    primary_filename, primary_file_size, direct_links, files_json, trackers_json,
                    sync_type, sync_status, telegram_channel_id, telegram_message_id, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tid,
                item.get("name"),
                (item.get("anime") or {}).get("id"),
                (item.get("anime") or {}).get("title"),
                (item.get("anime") or {}).get("english_title"),
                (item.get("anime") or {}).get("thumbnail"),
                item.get("episode_no"),
                item.get("totalsize"),
                item.get("filecount"),
                item.get("btih"),
                item.get("nyaa_id"),
                item.get("nekobt_id"),
                max_seeders,
                file_name,
                file_size,
                json.dumps(direct_links),
                json.dumps(files),
                json.dumps(trackers),
                sync_type,
                "COMPLETED",
                str(TELEGRAM_CHANNEL_ID),
                telegram_msg_id,
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
            synced_ids.add(tid)

            # Export updated SQL database dump
            export_database_to_sql(DATABASE_PATH, SQL_DUMP_PATH)

            processed_in_this_run += 1
            logger.info(f"Workload complete for Torrent ID {tid} (Sync Type: {sync_type}, Msg ID: {telegram_msg_id})")

            if single_video or processed_in_this_run >= limit_per_run:
                logger.info(f"Reached batch limit ({processed_in_this_run}/{limit_per_run}).")
                has_more_unprocessed = True
                scan_active = False
                break

        offset += page_limit
        if offset >= total_group_releases:
            break

    if client:
        await client.disconnect()

    conn.close()

    logger.info(f"--- Run Summary: Processed {processed_in_this_run} releases in this run ---")
    if has_more_unprocessed or (total_group_releases > len(synced_ids)):
        print("RESULT_HAS_MORE_VIDEOS=true")
    else:
        print("RESULT_HAS_MORE_VIDEOS=false")
        logger.info("All eligible videos in Group 12 have been synced!")


def main():
    parser = argparse.ArgumentParser(description="Tsukihime Group 12 (Yameii) to Telegram Sync Engine")
    parser.add_argument("--single-video", action="store_true", help="Process exactly 1 eligible video per run")
    parser.add_argument("--limit", type=int, default=1, help="Number of videos to process in this run (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Test fetch and database indexing without Telegram sending")
    args = parser.parse_args()

    limit = 1 if args.single_video else args.limit

    import asyncio
    try:
        asyncio.run(run_yameii_sync(single_video=args.single_video, dry_run=args.dry_run, limit_per_run=limit))
    except KeyboardInterrupt:
        logger.info("Sync process stopped by user.")


if __name__ == "__main__":
    main()
