# Yameii Sync Project (Tsukihime Group 12 to Telegram)

This branch (`Yameii`) contains the automated synchronization worker and GitHub Actions workflow for syncing anime video releases from [Tsukihime Group 12 (Yameii)](https://tsukihime.org/group/12-yameii) to Telegram channel `-1004492582679`.

## Architecture & Features
- **API Source**: `https://api.tsukihime.org/v1/groups/12` (Total ~13,416 releases).
- **Target Telegram Channel**: `-1004492582679`.
- **Order of Execution**: Newest to oldest (scans from `offset=0` before each run to immediately catch newly uploaded episodes).
- **Seeders Rule**: If a release has single-digit seeders (`< 10`), it is skipped.
- **File Size Rule**:
  - **> 2 GB**: Sent as a formatted Telegram text message with video metadata and all mirror links (BuzzHeavier, FileDitch, Gofile, ZeroFS, Nyaa, Magnet).
  - **<= 2 GB**: Downloaded locally and uploaded directly to Telegram via Telethon MTProto. If locker download fails, it gracefully falls back to sending the text message with links.
- **Database Tracking**:
  - `yameii.db`: SQLite database storing all release metadata, file attributes, tracking info, and Telegram message IDs.
  - `yameii.sql`: Plain SQL dump committed to Git so it can be served via bot or external database.
- **Workflow**: `.github/workflows/yameii_sync.yml` runs workloads sequentially and auto-chains via GitHub Actions.
