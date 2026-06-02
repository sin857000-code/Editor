#!/usr/bin/env python3
"""
Reads .instagram_queue.json and posts each entry as an Instagram carousel.
Run this AFTER git push so that raw.githubusercontent.com URLs are live.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

QUEUE_FILE = Path(__file__).parent / ".instagram_queue.json"
IG_BASE    = "https://graph.instagram.com/v21.0"


def _ig_post(path: str, data: dict, token: str) -> dict:
    payload = urllib.parse.urlencode({**data, "access_token": token}).encode()
    req = urllib.request.Request(f"{IG_BASE}{path}", data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def post_carousel(ig_user_id: str, ig_token: str, image_urls: list[str], caption: str) -> str | None:
    container_ids = []

    for url in image_urls:
        try:
            res = _ig_post(
                f"/{ig_user_id}/media",
                {"image_url": url, "media_type": "CAROUSEL_ITEM"},
                ig_token,
            )
            cid = res.get("id")
            if cid:
                container_ids.append(cid)
                print(f"  [IG] Container created: {cid}")
            else:
                print(f"  [WARN] No container id: {res}")
        except urllib.error.HTTPError as e:
            print(f"  [WARN] HTTP {e.code}: {e.read().decode()[:200]}")
        except Exception as e:
            print(f"  [WARN] {e}")

    if not container_ids:
        print("  [WARN] No carousel containers created — skipping publish")
        return None

    try:
        carousel = _ig_post(
            f"/{ig_user_id}/media",
            {
                "media_type": "CAROUSEL",
                "children":   ",".join(container_ids),
                "caption":    caption,
            },
            ig_token,
        )
        carousel_id = carousel.get("id")
        if not carousel_id:
            print(f"  [WARN] No carousel id: {carousel}")
            return None
        print(f"  [IG] Carousel container: {carousel_id}")
    except Exception as e:
        print(f"  [WARN] Carousel container failed: {e}")
        return None

    time.sleep(3)

    try:
        result = _ig_post(
            f"/{ig_user_id}/media_publish",
            {"creation_id": carousel_id},
            ig_token,
        )
        media_id = result.get("id")
        print(f"  [IG] Published! media_id: {media_id}")
        return media_id
    except Exception as e:
        print(f"  [WARN] Publish failed: {e}")
        return None


def main():
    if not QUEUE_FILE.exists():
        print("No queue file found. Nothing to post.")
        return

    queue = json.loads(QUEUE_FILE.read_text())
    if not queue:
        print("Queue is empty. Nothing to post.")
        return

    print(f"[Instagram Carousel] Processing {len(queue)} queued post(s)...\n")
    failed = []

    for entry in queue:
        ig_user_id  = entry["ig_user_id"]
        ig_token    = entry["ig_token"]
        image_urls  = entry["image_urls"]
        caption     = entry["caption"]

        print(f"── {caption[:60]}...")
        print(f"   {len(image_urls)} slides")

        media_id = post_carousel(ig_user_id, ig_token, image_urls, caption)
        if not media_id:
            failed.append(entry)
        print()

    QUEUE_FILE.write_text(json.dumps(failed, indent=2, ensure_ascii=False))

    if failed:
        print(f"[WARN] {len(failed)} post(s) failed and remain in queue.")
    else:
        print("All posts published successfully.")


if __name__ == "__main__":
    main()
