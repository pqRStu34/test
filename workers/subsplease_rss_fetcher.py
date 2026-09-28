import os
import sys
import time
import re
import logging
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv
from convex import ConvexClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("subsplease_rss")

RSS_URL = "https://subsplease.org/rss/?r=1080"
CONVEX_URL = os.environ.get("CONVEX_URL", "").strip()


def parse_size_in_bytes(size_str: str, link: str) -> int:
    xl_match = re.search(r"[?&]xl=(\d+)", link)
    if xl_match:
        try:
            return int(xl_match.group(1))
        except ValueError:
            pass

    if not size_str:
        return 0

    size_clean = size_str.strip().lower()
    m = re.search(r"([\d.]+)\s*(gib|gb|mib|mb|kib|kb|b)", size_clean)
    if not m:
        return 0

    val = float(m.group(1))
    unit = m.group(2)

    if unit in ("gib", "gb"):
        return int(val * 1024 * 1024 * 1024)
    elif unit in ("mib", "mb"):
        return int(val * 1024 * 1024)
    elif unit in ("kib", "kb"):
        return int(val * 1024)
    return int(val)


def fetch_rss_feed() -> list:
    timestamp = int(time.time())
    url = f"{RSS_URL}&_cb={timestamp}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }

    logger.info(f"Fetching RSS feed from: {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    channel = root.find("channel")
    if channel is None:
        logger.error("Invalid RSS feed: <channel> element not found.")
        return []

    items = []
    ns = {"subsplease": "https://subsplease.org/rss"}

    for item in channel.findall("item"):
        title_el = item.find("title")
        category_el = item.find("category")
        link_el = item.find("link")
        size_el = item.find("subsplease:size", ns)

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        category = category_el.text.strip() if category_el is not None and category_el.text else ""
        link = link_el.text.strip() if link_el is not None and link_el.text else ""
        size_str = size_el.text.strip() if size_el is not None and size_el.text else ""

        if not title or not link:
            continue

        file_size = parse_size_in_bytes(size_str, link)

        items.append({
            "title": title,
            "category": category,
            "link": link,
            "fileSize": file_size if file_size > 0 else None
        })

    logger.info(f"Parsed {len(items)} releases from RSS feed.")
    return items


def main():
    if not CONVEX_URL:
        logger.critical("CONVEX_URL environment variable is not configured.")
        sys.exit(1)

    client = ConvexClient(CONVEX_URL)
    items = fetch_rss_feed()

    if not items:
        logger.warning("No items retrieved from RSS feed.")
        print("RESULT_NEW_RELEASES=0")
        return

    logger.info("Syncing releases to Convex database...")
    added_count = client.mutation("subsplease:addReleases", {"releases": items})
    logger.info(f"Convex sync complete. Newly added releases: {added_count}")
    print(f"RESULT_NEW_RELEASES={added_count}")


if __name__ == "__main__":
    main()
