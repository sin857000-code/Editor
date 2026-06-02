#!/usr/bin/env python3
"""
Football transfer news crawler + Claude API translator + DALL-E 3 image generator.
Fetches RSS feeds, filters transfer-related items, translates to Korean,
generates a thumbnail image, and writes Jekyll _posts/ markdown files.
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
from openai import OpenAI

# ── Config ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
POSTS_DIR = REPO_ROOT / "blog" / "_posts"
IMAGES_DIR = REPO_ROOT / "blog" / "assets" / "images"
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
        channel = root.find("channel") or root
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            desc  = (item.findtext("description") or "").strip()
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


def translate_item(claude: anthropic.Anthropic, item: dict) -> dict | None:
    """Use Claude to translate and rewrite item as Korean blog post."""
    prompt = f"""다음은 해외 축구 이적 뉴스 기사입니다. 아래 지시에 따라 한국어 블로그 포스트를 작성해주세요.

원문 제목: {item['title']}
원문 내용: {item['description']}
출처: {item['source_name']}

지시사항:
1. 제목(title): 한국어로 자연스럽게 번역. 선수명/팀명은 한국 축구 팬에게 익숙한 표기 사용.
2. 카테고리(category): 다음 중 하나 선택 → 영입 확정 / 이적 협상 / 임대 / 방출/계약만료 / 이적 소문
3. 본문(content): 200~400자 한국어로 자연스럽게 요약 작성. 사실만 전달하고 과장 금지.
4. image_prompt: DALL-E 3용 영어 프롬프트. 해당 이적 뉴스를 상징하는 축구 장면을 묘사. 실제 선수 얼굴/이름 절대 포함 금지. 예: "A soccer player in a red jersey holding up a new team scarf at a packed stadium, cinematic lighting, sports photography style"

반드시 아래 JSON 형식으로만 응답 (다른 텍스트 없이):
{{"title": "...", "category": "...", "content": "...", "image_prompt": "..."}}"""

    try:
        message = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        return json.loads(match.group())
    except Exception as e:
        print(f"  [WARN] Translation failed: {e}")
        return None


def generate_image(openai_client: OpenAI, prompt: str, slug: str, date: datetime.datetime) -> str | None:
    """Generate image with DALL-E 3, save to assets/images/, return relative path."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # safe filename
    filename = f"{date.strftime('%Y%m%d')}-{slug[:50]}.png"
    filepath = IMAGES_DIR / filename

    # skip if already exists (re-run safety)
    if filepath.exists():
        return f"/blog/assets/images/{filename}"

    # always append style suffix to keep images consistent
    full_prompt = (
        prompt.rstrip(".")
        + ". Digital illustration style, vibrant colors, no text or logos, "
          "no real player faces, football/soccer theme."
    )

    try:
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=full_prompt,
            size="1792x1024",   # wide thumbnail ratio
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url

        # download the image
        req = urllib.request.Request(image_url, headers={"User-Agent": "FootballBot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            filepath.write_bytes(resp.read())

        print(f"  [IMG] Saved {filename}")
        return f"/blog/assets/images/{filename}"

    except Exception as e:
        print(f"  [WARN] Image generation failed: {e}")
        return None


def write_post(item: dict, translated: dict, image_path: str | None, date: datetime.datetime):
    """Write a Jekyll markdown post file."""
    slug = slugify(translated["title"])
    filename = f"{date.strftime('%Y-%m-%d')}-{slug}.md"
    filepath = POSTS_DIR / filename

    counter = 1
    while filepath.exists():
        filepath = POSTS_DIR / f"{date.strftime('%Y-%m-%d')}-{slug}-{counter}.md"
        counter += 1

    image_line = f'image: "{image_path}"' if image_path else ""

    front_matter = f"""---
layout: post
title: "{translated['title'].replace('"', "'")}"
date: {date.strftime('%Y-%m-%d %H:%M:%S')} +0900
category: "{translated['category']}"
source_name: "{item['source_name']}"
source_url: "{item['source_url']}"
{image_line}
---

{f'![썸네일]({image_path})' + chr(10) + chr(10) if image_path else ""}{translated['content']}

---
*원문: [{item['source_name']}]({item['source_url']})*
"""
    filepath.write_text(front_matter, encoding="utf-8")
    print(f"  [OK] {filepath.name}")
    return filepath


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not anthropic_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")
    if not openai_key:
        raise SystemExit("OPENAI_API_KEY not set")

    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    claude = anthropic.Anthropic(api_key=anthropic_key)
    openai_client = OpenAI(api_key=openai_key)
    seen = load_seen()
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))

    print(f"[{now.strftime('%Y-%m-%d %H:%M')} KST] Fetching RSS feeds...")

    all_items = []
    for source in RSS_SOURCES:
        items = fetch_rss(source)
        transfer_items = [i for i in items if is_transfer_related(i)]
        print(f"  {source['name']}: {len(items)} items, {len(transfer_items)} transfer-related")
        all_items.extend(transfer_items)

    new_items = [i for i in all_items if item_id(i["source_url"]) not in seen]
    print(f"\nNew items: {len(new_items)} (skipping {len(all_items) - len(new_items)} seen)")

    if not new_items:
        print("No new items found. Exiting.")
        return

    posted = 0
    for item in new_items[:MAX_NEW_POSTS]:
        print(f"\nTranslating: {item['title'][:70]}...")
        translated = translate_item(claude, item)
        if not translated:
            continue

        post_time = now - datetime.timedelta(minutes=posted * 3)
        slug = slugify(translated["title"])

        # generate image (non-blocking: post is written even if image fails)
        image_path = None
        if translated.get("image_prompt"):
            print(f"  Generating image...")
            image_path = generate_image(openai_client, translated["image_prompt"], slug, post_time)

        write_post(item, translated, image_path, post_time)
        seen.add(item_id(item["source_url"]))
        posted += 1

    save_seen(seen)
    print(f"\nDone. {posted} posts created.")
    if posted < MIN_POSTS:
        print(f"[WARN] Only {posted} posts created (target: {MIN_POSTS}+)")


if __name__ == "__main__":
    main()
