# Media Automation & Sync Engine

A multi-pipeline media synchronization and JSON generation system designed for automated anime release management and streaming.

---

## Architecture Overview

1. **Pipeline 1: SubsPlease (1080p) to Telegram Channel 1**
   - **Ingestion (`workers/subsplease_rss_fetcher.py`)**: Hourly cron (`.github/workflows/subsplease_rss.yml`) monitoring `https://subsplease.org/rss/?r=1080` with cache-busting. Ingests metadata into **Convex Database**.
   - **Uploader (`workers/subsplease_uploader.py`)**:
     - **> 2GB**: Sends formatted Telegram text message with mirror/magnet link.
     - **<= 2GB**: Downloads via `aria2c` and uploads video to Telegram via MTProto.
     - Updates Convex with `telegramMessageId` and `telegramFileUniqueId`.

2. **Pipeline 2: Tsukihime Group 12 to Telegram Channel 2**
   - **Worker (`workers/tsukihime_sync.py`)**: Syncs releases from `https://api.tsukihime.org/v1/groups/12` to a separate Telegram channel via Convex. Same 2GB file size threshold rules apply.

3. **Pipeline 3: FileStreamBot Log Channel to Internet Archive (PocketBase)**
   - **Worker (`workers/telegram_ia_sync.py`)**:
     - Triggered on ping (`workflow_dispatch`).
     - **Pre-check Protection**: Queries target Internet Archive item first. If the file is already uploaded, skips re-uploading.
     - **MP4-Only Uploads**: Only `.mp4` video files are uploaded to Internet Archive. Non-mp4 files are kept in PocketBase but skipped for IA.
     - **FileStreamBot Stream Link**: Computes exact stream link using TG-FileStreamBot's MD5 algorithm directly from Telegram message attributes without bot-to-bot communication.
     - **PocketBase Persistence**: Runs local PocketBase instance with state persisted on the `pocketbase-data` branch.
     - **6-Hour Execution & Auto-Chaining**: Safely monitors run time and chains to the next workflow run until all videos are processed.

4. **JSON Generator Bot & Supabase Integration**
   - **Telegram Bot & CLI (`workers/json_generator_bot.py`)**:
     - Responds to `/json <anime_id>` in Telegram.
     - Automatically aggregates episodes, multi-audio tracks, and qualities using FileStreamBot stream links.
     - Outputs compact JSON strictly matching the episode schema.
     - Directly uploads/replaces `<anime_id>.json` in **Supabase Storage** with `x-upsert: true`.

---

## Configuration & Secrets

Add these secrets to your GitHub Repository (**Settings > Secrets and variables > Actions**):

| Secret Name | Description | Used By |
|---|---|---|
| `CONVEX_URL` | Convex Cloud deployment URL | Pipeline 1 & 2 |
| `TELEGRAM_API_ID` | Telegram App API ID from my.telegram.org | All Pipelines |
| `TELEGRAM_API_HASH` | Telegram App API Hash from my.telegram.org | All Pipelines |
| `TELEGRAM_PIPELINE_BOT_TOKEN` | Bot token for torrent channel uploads | Pipeline 1 & 2 |
| `TELEGRAM_PERSONAL_BOT_TOKEN` | Bot token for personal JSON generator & D1 | JSON Generator Bot |
| `TELEGRAM_STRING_SESSION_1` | MTProto string session for SubsPlease worker | Pipeline 1 |
| `TELEGRAM_STRING_SESSION_2` | MTProto string session for Tsukihime worker | Pipeline 2 |
| `TELEGRAM_STRING_SESSION_3` | MTProto string session for IA log channel sync | Pipeline 3 |
| `TELEGRAM_CHANNEL_SUBSPLEASE` | Target Telegram channel for SubsPlease | Pipeline 1 |
| `TELEGRAM_CHANNEL_TSUKIHIME` | Target Telegram channel for Tsukihime | Pipeline 2 |
| `TELEGRAM_CHANNEL_IA_SOURCE` | Log channel with FileStreamBot forwarded files | Pipeline 3 |
| `FILESTREAM_BASE_URL` | Stream server base URL | Pipeline 3 |
| `IA_ACCESS_KEY` | Internet Archive S3 Access Key | Pipeline 3 |
| `IA_SECRET_KEY` | Internet Archive S3 Secret Key | Pipeline 3 |
| `IA_ITEM_IDENTIFIER` | Internet Archive target item identifier | Pipeline 3 |
| `SUPABASE_URL` | Supabase Project URL | JSON Bot |
| `SUPABASE_KEY` | Supabase Anon / Service Role Key | JSON Bot |
| `SUPABASE_BUCKET` | Supabase Storage Bucket name (default: `anime-episodes`) | JSON Bot |

---

## Execution

```bash
# Install dependencies
pip install -r requirements.txt

# Deploy Convex backend
npx convex deploy

# Test SubsPlease RSS Ingestion
python workers/subsplease_rss_fetcher.py

# Generate JSON for an anime via CLI
python workers/json_generator_bot.py --generate 63140
```
