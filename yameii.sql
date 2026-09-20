-- Tsukihime Yameii Sync SQL Database Dump
-- Exported: 2026-09-20T23:23:57.467140+00:00

BEGIN TRANSACTION;
CREATE TABLE videos (
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
CREATE INDEX idx_sync_status ON videos(sync_status);
CREATE INDEX idx_anime_id ON videos(anime_id);
CREATE INDEX idx_btih ON videos(btih);
COMMIT;
