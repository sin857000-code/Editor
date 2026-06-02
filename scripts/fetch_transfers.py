#!/usr/bin/env python3
"""
Football transfer news crawler + Claude API translator.
Fetches RSS feeds, filters transfer-related items, translates to Korean,
and writes Jekyll _posts/ markdown files.
"""

import os
import re
import json
import hashlib
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import anthropic

# ── Config ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
POSTS_DIR = REPO_ROOT / "blog" / "_posts"
SEEN_FILE = REPO_ROOT / "scripts" / ".seen_ids.json"

MAX_NEW_POSTS = 5   # max posts per run (3 runs/day → up to 15/day, usually 3-5)
MIN_POSTS = 3       # warn if fewer than this are found

RSS_SOURCES = [
    {
        "name": "Sky Sports Transfers",
        "url": "https://www.skysports.com/rss/12040",
    },
    {
        "name": "BBC Sport Football",
        "url": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    },
    {
        "name": "Goal.com Transfers",
        "url": "https://www.goal.com/feeds/en/news",
    },
    {
        "name": "ESPN FC",
        "url": "https://www.espn.com/espn/rss/soccer/news",
    },
    {
        "name": "The Guardian Football",
        "url": "https://www.theguardian.com/football/transfers/rss",
    },
]

TRANSFER_KEYWORDS = [
    "transfer", "signing", "signed", "joins", "move", "deal", "fee",
    "bid", "loan", "permanent", "contract", "agreement", "medical",
    "confirmed", "complete", "here we go", "official", "unveiled",
    "departure", "released", "free agent", "buyout", "clause",
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2, ensure_ascii=False))


def item_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def fetch_rss(source: dict) -> list[dict]:
    """Fetch and parse an RSS feed, return list of items."""
    items = []
    try:
        req = urllib.request.Request(
            source["url"],
            headers={"User-Agent": "Mozilla/5.0 (compatible; FootballBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        ns = {}
        channel = root.find("channel") or root
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            desc  = (item.findtext("description") or "").strip()
            # strip HTML tags from description
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            pub   = item.findtext("pubDate") or ""
            if title and link:
                items.append({
                    "source_name": source["name"],
                    "source_url": link,
                    "title": title,
                    "description": desc[:800],
                    "pub_date": pub,
                })
    except Exception as e:
        print(f"  [WARN] {source['name']}: {e}")
    return items


def is_transfer_related(item: dict) -> bool:
    text = (item["title"] + " " + item["description"]).lower()
    return any(kw in text for kw in TRANSFER_KEYWORDS)


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_]+", "-", text.strip())
    return text[:80]


def translate_item(client: anthropic.Anthropic, item: dict) -> dict | None:
    """Use Claude to translate and rewrite item as Korean blog post."""
    prompt = f"""다음은 해외 축구 이적 뉴스 기사입니다. 아래 지시에 따라 한국어 블로그 포스트를 작성해주세요.

원문 제목: {item['title']}
원문 내용: {item['description']}
출처: {item['source_name']}

지시사항:
1. 제목(title): 한국어로 자연스럽게 번역. 선수명/팀명은 한국 축구 팬에게 익숙한 표기 사용.
2. 카테고리(category): 다음 중 하나 선택 → 영입 확정 / 이적 협상 / 임대 / 방출/계약만료 / 이적 소문
3. 본문(content): 200~400자 한국어로 자연스럽게 요약 작성. 사실만 전달하고 과장 금지.

반드시 아래 JSON 형식으로만 응답 (다른 텍스트 없이):
{{"title": "...", "category": "...", "content": "..."}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        # extract JSON even if wrapped in ```
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group())
        return data
    except Exception as e:
        print(f"  [WARN] Translation failed: {e}")
        return None


def write_post(item: dict, translated: dict, date: datetime.datetime):
    """Write a Jekyll markdown post file."""
    slug = slugify(translated["title"])
    filename = f"{date.strftime('%Y-%m-%d')}-{slug}.md"
    filepath = POSTS_DIR / filename

    # avoid duplicate filenames
    counter = 1
    while filepath.exists():
        filepath = POSTS_DIR / f"{date.strftime('%Y-%m-%d')}-{slug}-{counter}.md"
        counter += 1

    front_matter = f"""---
layout: post
title: "{translated['title'].replace('"', "'")}"
date: {date.strftime('%Y-%m-%d %H:%M:%S')} +0900
category: "{translated['category']}"
source_name: "{item['source_name']}"
source_url: "{item['source_url']}"
---

{translated['content']}

---
*원문: [{item['source_name']}]({item['source_url']})*
"""
    filepath.write_text(front_matter, encoding="utf-8")
    print(f"  [OK] {filepath.name}")
    return filepath


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=api_key)
    seen = load_seen()
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))

    print(f"[{now.strftime('%Y-%m-%d %H:%M')} KST] Fetching RSS feeds...")

    all_items = []
    for source in RSS_SOURCES:
        items = fetch_rss(source)
        transfer_items = [i for i in items if is_transfer_related(i)]
        print(f"  {source['name']}: {len(items)} items, {len(transfer_items)} transfer-related")
        all_items.extend(transfer_items)

    # deduplicate by URL
    new_items = [i for i in all_items if item_id(i["source_url"]) not in seen]
    print(f"\nNew items: {len(new_items)} (skipping {len(all_items) - len(new_items)} seen)")

    if not new_items:
        print("No new items found. Exiting.")
        return

    posted = 0
    for item in new_items[:MAX_NEW_POSTS]:
        print(f"\nTranslating: {item['title'][:70]}...")
        translated = translate_item(client, item)
        if not translated:
            continue

        # stagger post times so they don't appear at exactly the same second
        post_time = now - datetime.timedelta(minutes=posted * 3)
        write_post(item, translated, post_time)
        seen.add(item_id(item["source_url"]))
        posted += 1

    save_seen(seen)
    print(f"\nDone. {posted} posts created.")
    if posted < MIN_POSTS:
        print(f"[WARN] Only {posted} posts created (target: {MIN_POSTS}+)")


if __name__ == "__main__":
    main()
