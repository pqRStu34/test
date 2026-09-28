import os
import sys
import json
import requests
from dotenv import load_dotenv
from pocketbase.client import PocketBaseClient

load_dotenv()

def generate_json_for_anime(anime_id: str, pb_url: str = "http://127.0.0.1:8090"):
    print("=" * 60)
    print(f"  Generating Episode JSON for Anime ID: {anime_id}")
    print("=" * 60)

    pb = PocketBaseClient(base_url=pb_url)
    records = pb.query_by_anime_id(str(anime_id))

    if not records:
        print(f"[!] No episodes found in PocketBase for Anime ID: '{anime_id}'.")
        return None

    print(f"[+] Found {len(records)} sync records in PocketBase.")

    # Aggregate into { episodeNumber, thumbnail, audio: { LANG: { QUALITY: LINK } } }
    ep_map = {}
    for r in records:
        ep_raw = r.get("episode_number") or 1
        ep_num = int(ep_raw) if isinstance(ep_raw, (int, float)) and ep_raw == int(ep_raw) else ep_raw
        if ep_num not in ep_map:
            ep_map[ep_num] = {
                "episodeNumber": ep_num,
                "thumbnail": r.get("thumbnail") or "",
                "audio": {}
            }

        lang = (r.get("language") or "JPN").upper()
        qual = (r.get("quality") or "1080p").lower()
        stream_link = r.get("stream_link") or ""

        if lang not in ep_map[ep_num]["audio"]:
            ep_map[ep_num]["audio"][lang] = {}

        ep_map[ep_num]["audio"][lang][qual] = stream_link

    episodes = [ep_map[k] for k in sorted(ep_map.keys(), key=lambda x: (isinstance(x, (int, float)), x))]
    payload = {"episodes": episodes}

    # Save locally
    out_file = f"{anime_id}.json"
    json_str = json.dumps(payload, indent=2)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(json_str)

    print(f"[v] Saved to '{out_file}' ({len(episodes)} episodes).")

    # Upload to Supabase if configured
    sb_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    sb_key = os.environ.get("SUPABASE_KEY", "")
    sb_bucket = os.environ.get("SUPABASE_BUCKET", "episodes")

    if sb_url and sb_key:
        print(f"[i] Uploading to Supabase Storage ({sb_bucket}/{out_file})...")
        upload_url = f"{sb_url}/storage/v1/object/{sb_bucket}/{out_file}"
        headers = {
            "apikey": sb_key,
            "Authorization": f"Bearer {sb_key}",
            "Content-Type": "application/json",
            "x-upsert": "true"
        }
        try:
            res = requests.post(upload_url, headers=headers, data=json_str.encode("utf-8"), timeout=15)
            if res.status_code == 200:
                print(f"[v] Successfully uploaded to Supabase Storage!")
                print(f"    Public URL: {sb_url}/storage/v1/object/public/{sb_bucket}/{out_file}")
            else:
                print(f"[!] Supabase upload response ({res.status_code}): {res.text}")
        except Exception as e:
            print(f"[!] Supabase upload error: {e}")

    return payload

if __name__ == "__main__":
    if len(sys.argv) < 2:
        anime_id = input("Enter Anime ID (e.g. 5651): ").strip()
    else:
        anime_id = sys.argv[1].strip()

    if anime_id:
        generate_json_for_anime(anime_id)
    else:
        print("Usage: python generate_json.py <anime_id>")
