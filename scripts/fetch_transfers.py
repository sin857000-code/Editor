#!/usr/bin/env python3
"""
Football transfer news crawler + Claude API translator
+ Carousel card image generator (HTML/Playwright, 1080×1350)
+ Instagram carousel auto-poster via GitHub raw URLs.

Flow per post:
  1. RSS fetch & filter (24h window)
  2. Claude: translate → Korean blog content + 5-slide carousel data
  3. Playwright: render 5 HTML slides → PNG (1080×1350)
  4. Write Jekyll _posts/ markdown (blog)
  5. Save .instagram_queue.json (posted by post_instagram_carousel.py after git push)
"""

import os
import re
import json
import hashlib
import datetime
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import email.utils
from pathlib import Path

import anthropic

# ── Config ──────────────────────────────────────────────────────────────────

REPO_ROOT  = Path(__file__).parent.parent
POSTS_DIR  = REPO_ROOT / "blog" / "_posts"
IMAGES_DIR = REPO_ROOT / "blog" / "assets" / "images"
SEEN_FILE  = REPO_ROOT / "scripts" / ".seen_ids.json"
QUEUE_FILE = REPO_ROOT / "scripts" / ".instagram_queue.json"

MAX_NEW_POSTS = 5
MIN_POSTS     = 3
MAX_AGE_HOURS = 24

GITHUB_REPO = os.environ.get("GITHUB_REPO", "sin857000-code/Editor")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

RSS_SOURCES = [
    {"name": "Sky Sports Transfers", "url": "https://www.skysports.com/rss/12040"},
    {"name": "BBC Sport Football",   "url": "https://feeds.bbci.co.uk/sport/football/rss.xml"},
    {"name": "Goal.com Transfers",   "url": "https://www.goal.com/feeds/en/news"},
    {"name": "ESPN FC",              "url": "https://www.espn.com/espn/rss/soccer/news"},
    {"name": "The Guardian Football","url": "https://www.theguardian.com/football/transfers/rss"},
]

TRANSFER_KEYWORDS = [
    "transfer", "signing", "signed", "joins", "move", "deal", "fee",
    "bid", "loan", "permanent", "contract", "agreement", "medical",
    "confirmed", "complete", "here we go", "official", "unveiled",
    "departure", "released", "free agent", "buyout", "clause",
]

CATEGORY_HASHTAGS = {
    "영입 확정":    ["#영입확정", "#이적완료", "#축구이적"],
    "이적 협상":    ["#이적협상", "#이적설", "#축구이적"],
    "임대":         ["#임대이적", "#축구임대", "#축구이적"],
    "방출/계약만료":["#방출", "#계약만료", "#프리에이전트", "#축구이적"],
    "이적 소문":    ["#이적루머", "#이적소문", "#축구이적"],
}

BASE_HASHTAGS = [
    "#축구", "#해외축구", "#이적시장", "#EPL", "#라리가",
    "#분데스리가", "#세리에A", "#리그앙", "#챔피언스리그",
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2, ensure_ascii=False))


def load_queue() -> list:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    return []


def save_queue(queue: list):
    QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False))


def item_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def parse_pub_date(pub_date: str) -> datetime.datetime | None:
    if not pub_date:
        return None
    try:
        return email.utils.parsedate_to_datetime(pub_date)
    except Exception:
        return None


def is_recent(item: dict, now: datetime.datetime) -> bool:
    dt = parse_pub_date(item.get("pub_date", ""))
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    age = (now.astimezone(datetime.timezone.utc) - dt.astimezone(datetime.timezone.utc)).total_seconds() / 3600
    return age <= MAX_AGE_HOURS


def fetch_rss(source: dict) -> list[dict]:
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
            desc  = re.sub(r"<[^>]+>", "", desc).strip()
            pub   = item.findtext("pubDate") or ""
            if title and link:
                items.append({
                    "source_name": source["name"],
                    "source_url":  link,
                    "title":       title,
                    "description": desc[:800],
                    "pub_date":    pub,
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


# ── Claude: translate + carousel data ────────────────────────────────────────

def translate_item(claude: anthropic.Anthropic, item: dict) -> dict | None:
    prompt = f"""당신은 해외 축구를 깊이 아는 한국인 축구 전문 기자입니다. 아래 이적 뉴스를 바탕으로 블로그 포스트와 인스타그램 캐러셀 카드뉴스를 함께 작성하세요.

원문 제목: {item['title']}
원문 내용: {item['description']}
출처: {item['source_name']}

작성 지침:

1. title (제목)
   - 클릭하고 싶은 강렬한 한국어 제목. 숫자·금액·팀명으로 임팩트를 줘도 좋음
   - 예: "€8000만 몸값 검증 완료 — 아스날, 드디어 원하던 그 선수 잡았다"

2. category: 영입 확정 / 이적 협상 / 임대 / 방출/계약만료 / 이적 소문 중 하나

3. content (블로그 본문, 800~1200자)
   [단락1 — 핵심 팩트] 이적료·계약기간·조건 등 숫자 강조.
   [단락2 — 맥락과 의미] 선수 중요성, 팀에게 어떤 의미인지, 최근 성적·역할.
   [단락3 — 전망과 팬 반응] 리그 판도 영향, 팬 입장의 설렘 또는 우려.
   문체: 전문적이지만 친근함. 기자의 시각과 평가가 담긴 글.

4. instagram_caption (인스타그램 캡션)
   - 이모지 3~5개, 5~7줄
   - 훅 첫 줄 → 핵심 → 의미/반응 → 마무리
   - 해시태그 제외

5. slides (캐러셀 카드뉴스 5장 데이터 — 정확한 형식 필수)
   {{
     "cover": {{
       "headline": "이적 핵심을 담은 임팩트 있는 한 줄 (최대 20자)",
       "headline_accent": "headline 중 오렌지로 강조할 단어 하나 (예: 이적 확정)",
       "meta": "팀명 → 팀명 · 이적료 · 계약기간"
     }},
     "fact": {{
       "label": "TRANSFER FACT",
       "title": "이적 기본 정보",
       "facts": [
         {{"key": "이적료", "value": "€XXXX만", "accent": true}},
         {{"key": "계약기간", "value": "X년", "accent": false}},
         {{"key": "출신팀", "value": "팀명", "accent": false}},
         {{"key": "영입팀", "value": "팀명", "accent": false}}
       ]
     }},
     "player": {{
       "label": "WHY THIS PLAYER",
       "title": "왜 이 선수인가",
       "bullets": ["강점 또는 특징 1 (20자 이내)", "강점 또는 특징 2", "강점 또는 특징 3", "강점 또는 특징 4"]
     }},
     "impact": {{
       "label": "IMPACT",
       "title": "이 이적이 바꾸는 것",
       "bullets": ["리그/팀에 미치는 영향 1", "리그/팀에 미치는 영향 2", "팬 반응 또는 전망 3", "기대 포인트 4"]
     }},
     "outro": {{
       "headline": "Outro 마무리 문구 (한 줄, 20자 이내)",
       "sub": "팔로우/저장 유도 한 줄"
     }}
   }}

반드시 아래 JSON 형식으로만 응답 (다른 텍스트 없이):
{{"title":"...","category":"...","content":"...","instagram_caption":"...","slides":{{...}}}}"""

    try:
        message = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2500,
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


# ── Carousel generation ───────────────────────────────────────────────────────

def generate_carousel(slides_data: dict, slug: str, date: datetime.datetime) -> list[Path]:
    """Render 5 HTML slides to PNG. Returns list of saved paths."""
    try:
        from carousel_gen import build_carousel
    except ImportError:
        import sys
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from carousel_gen import build_carousel

    out_dir = IMAGES_DIR / "carousel" / f"{date.strftime('%Y%m%d')}-{slug[:40]}"
    return build_carousel(slides_data, out_dir)


def carousel_raw_urls(slide_paths: list[Path]) -> list[str]:
    """Convert local paths to raw.githubusercontent.com public URLs."""
    urls = []
    for p in slide_paths:
        rel = p.relative_to(REPO_ROOT)
        url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{rel}"
        urls.append(url)
    return urls


# ── Blog post writer ─────────────────────────────────────────────────────────

def write_post(item: dict, translated: dict, slide_paths: list[Path] | None, date: datetime.datetime) -> Path:
    slug = slugify(translated["title"])
    filename = f"{date.strftime('%Y-%m-%d')}-{slug}.md"
    filepath = POSTS_DIR / filename

    counter = 1
    while filepath.exists():
        filepath = POSTS_DIR / f"{date.strftime('%Y-%m-%d')}-{slug}-{counter}.md"
        counter += 1

    # First carousel slide as thumbnail
    thumb_line = ""
    carousel_md = ""
    if slide_paths:
        thumb_rel = "/blog/assets/images/carousel/" + slide_paths[0].parent.name + "/" + slide_paths[0].name
        thumb_line = f'image: "{thumb_rel}"'
        # Embed all carousel slides in the post
        carousel_md = "\n".join(
            f'![슬라이드 {i+1}](/blog/assets/images/carousel/{p.parent.name}/{p.name})'
            for i, p in enumerate(slide_paths)
        ) + "\n\n"

    content = f"""---
layout: post
title: "{translated['title'].replace('"', "'")}"
date: {date.strftime('%Y-%m-%d %H:%M:%S')} +0900
category: "{translated['category']}"
source_name: "{item['source_name']}"
source_url: "{item['source_url']}"
{thumb_line}
---

{carousel_md}{translated['content']}

---
*원문: [{item['source_name']}]({item['source_url']})*
"""
    filepath.write_text(content, encoding="utf-8")
    print(f"  [POST] {filepath.name}")
    return filepath


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    ig_user_id    = os.environ.get("INSTAGRAM_USER_ID")
    ig_token      = os.environ.get("INSTAGRAM_ACCESS_TOKEN")

    if not anthropic_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    instagram_enabled = bool(ig_user_id and ig_token)
    if not instagram_enabled:
        print("[INFO] Instagram secrets not set — carousel URLs will be queued but not posted")

    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    claude = anthropic.Anthropic(api_key=anthropic_key)
    seen   = load_seen()
    queue  = load_queue()
    now    = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))

    print(f"[{now.strftime('%Y-%m-%d %H:%M')} KST] Fetching RSS feeds...")

    all_items = []
    for source in RSS_SOURCES:
        items = fetch_rss(source)
        transfer_items = [i for i in items if is_transfer_related(i) and is_recent(i, now)]
        print(f"  {source['name']}: {len(items)} total, {len(transfer_items)} recent transfer-related")
        all_items.extend(transfer_items)

    new_items = [i for i in all_items if item_id(i["source_url"]) not in seen]
    print(f"\nNew: {len(new_items)} items (skipping {len(all_items) - len(new_items)} seen)\n")

    if not new_items:
        print("Nothing new. Exiting.")
        return

    posted = 0
    for item in new_items[:MAX_NEW_POSTS]:
        print(f"── {item['title'][:70]}")

        translated = translate_item(claude, item)
        if not translated:
            continue

        post_time = now - datetime.timedelta(minutes=posted * 3)
        slug = slugify(translated["title"])

        # Generate carousel slides
        slide_paths = None
        slides_data = translated.get("slides")
        if slides_data:
            try:
                slide_paths = generate_carousel(slides_data, slug, post_time)
            except Exception as e:
                print(f"  [WARN] Carousel generation failed: {e}")

        # Write blog post
        write_post(item, translated, slide_paths, post_time)

        # Queue Instagram carousel (posted after git push in next step)
        if instagram_enabled and slide_paths:
            raw_urls = carousel_raw_urls(slide_paths)
            cat_tags = CATEGORY_HASHTAGS.get(translated.get("category", ""), ["#축구이적"])
            hashtags = " ".join(cat_tags + BASE_HASHTAGS)
            caption  = translated.get("instagram_caption", translated.get("content", ""))
            queue.append({
                "caption":    f"{caption}\n\n{hashtags}",
                "image_urls": raw_urls,
                "ig_user_id": ig_user_id,
                "ig_token":   ig_token,
            })
            print(f"  [IG] Queued carousel ({len(raw_urls)} slides)")

        seen.add(item_id(item["source_url"]))
        posted += 1
        print()

    save_seen(seen)
    save_queue(queue)
    print(f"Done. {posted} post(s) created.")
    if posted < MIN_POSTS:
        print(f"[WARN] Only {posted} posts (target ≥ {MIN_POSTS})")


if __name__ == "__main__":
    main()
