#!/usr/bin/env python3
"""
Football transfer news crawler + Claude API translator
+ Pollinations.ai image generator (free, no API key)
+ Instagram Graph API auto-poster.
"""

import os
import re
import json
import hashlib
import datetime
import email.utils
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

import anthropic

REPO_ROOT = Path(__file__).parent.parent
POSTS_DIR = REPO_ROOT / "blog" / "_posts"
IMAGES_DIR = REPO_ROOT / "blog" / "assets" / "images"
SEEN_FILE  = REPO_ROOT / "scripts" / ".seen_ids.json"

MAX_NEW_POSTS = 5
MIN_POSTS = 3
MAX_AGE_HOURS = 24  # 24시간 이내 기사만 처리

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}?width=1024&height=1024&nologo=true&seed={seed}"

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


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2, ensure_ascii=False))


def item_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def parse_pub_date(pub_date_str: str) -> datetime.datetime | None:
    """RSS pubDate 를 UTC aware datetime 으로 파싱."""
    if not pub_date_str:
        return None
    try:
        # email.utils 는 RFC 2822 포맷 (RSS 표준) 파싱 지원
        ts = email.utils.parsedate_to_datetime(pub_date_str)
        return ts.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def is_recent(pub_date_str: str, now_utc: datetime.datetime) -> bool:
    """MAX_AGE_HOURS 이내 기사인지 확인. pubDate 파싱 실패 시 True 로 허용 (안전측)."""
    dt = parse_pub_date(pub_date_str)
    if dt is None:
        return True  # 날짜 모르면 일단 포함
    age = now_utc - dt
    return age.total_seconds() <= MAX_AGE_HOURS * 3600


def fetch_rss(source: dict, now_utc: datetime.datetime) -> list[dict]:
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
        skipped = 0
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            desc  = (item.findtext("description") or "").strip()
            desc  = re.sub(r"<[^>]+>", "", desc).strip()
            pub   = item.findtext("pubDate") or ""
            if not title or not link:
                continue
            if not is_recent(pub, now_utc):
                skipped += 1
                continue
            items.append({
                "source_name": source["name"],
                "source_url":  link,
                "title":       title,
                "description": desc[:800],
                "pub_date":    pub,
            })
        if skipped:
            print(f"    ({skipped}개 오래된 기사 스킵)")
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
    prompt = f"""다음은 해외 축구 이적 뉴스 기사입니다. 아래 지시에 따라 한국어 블로그 포스트와 인스타그램 게시물을 함글 작성해주세요.

원문 제목: {item['title']}
원문 내용: {item['description']}
출처: {item['source_name']}

지시사항:
1. title: 한국어 제목. 선수명/팀명은 한국 팬에게 익숙한 표기 사용.
2. category: 영입 확정 / 이적 협상 / 임대 / 방출/계약만료 / 이적 소문 중 하나.
3. content: 블로그용 200~400자 본문. 사실만 전달, 과장 금지.
4. instagram_caption: 인스타그램용 캡션. 이모지 2~3개 포함, 핵심 내용 3~4줄, 자연스럽고 친근한 말투. 해시태그 제외.
5. image_prompt: 이적 뉴스를 상징하는 축구 장면 영어 프롬프트. 실제 선수 얼굴/이름/유니폼 번호/팀 로고 절대 포함 금지. square composition, vibrant colors, digital art style 명시.

반드시 아래 JSON 형식으로만 응답:
{{"title":"...","category":"...","content":"...","instagram_caption":"...","image_prompt":"..."}}"""
    try:
        message = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,
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


def generate_image(prompt: str, slug: str, date: datetime.datetime) -> tuple[str | None, str | None]:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{date.strftime('%Y%m%d')}-{slug[:50]}.jpg"
    filepath = IMAGES_DIR / filename
    if filepath.exists():
        seed = int(hashlib.md5(slug.encode()).hexdigest(), 16) % 100000
        return str(filepath), POLLINATIONS_URL.format(prompt=urllib.parse.quote(prompt), seed=seed)
    full_prompt = (
        prompt.rstrip(".")
        + ", square 1:1, digital illustration, vibrant colors, "
          "no text overlay, no logos, no player faces, football soccer theme, "
          "cinematic lighting"
    )
    seed = int(hashlib.md5(slug.encode()).hexdigest(), 16) % 100000
    url = POLLINATIONS_URL.format(prompt=urllib.parse.quote(full_prompt), seed=seed)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FootballBot/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            filepath.write_bytes(resp.read())
        print(f"  [IMG] {filename}")
        return str(filepath), url
    except Exception as e:
        print(f"  [WARN] Image generation failed: {e}")
        return None, None


class InstagramPoster:
    BASE = "https://graph.instagram.com/v21.0"

    def __init__(self, user_id: str, access_token: str):
        self.user_id = user_id
        self.token = access_token

    def _post(self, path: str, data: dict) -> dict:
        payload = urllib.parse.urlencode({**data, "access_token": self.token}).encode()
        req = urllib.request.Request(f"{self.BASE}{path}", data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def post_image(self, image_url: str, caption: str) -> str | None:
        try:
            container = self._post(f"/{self.user_id}/media", {"image_url": image_url, "caption": caption})
            creation_id = container.get("id")
            if not creation_id:
                print(f"  [WARN] Instagram: no creation_id — {container}")
                return None
            result = self._post(f"/{self.user_id}/media_publish", {"creation_id": creation_id})
            media_id = result.get("id")
            print(f"  [IG] Posted — media_id: {media_id}")
            return media_id
        except urllib.error.HTTPError as e:
            print(f"  [WARN] Instagram HTTP {e.code}: {e.read().decode()}")
            return None
        except Exception as e:
            print(f"  [WARN] Instagram error: {e}")
            return None


def build_instagram_caption(translated: dict) -> str:
    category = translated.get("category", "이적 소문")
    caption_body = translated.get("instagram_caption", translated.get("content", ""))
    cat_tags = CATEGORY_HASHTAGS.get(category, ["#축구이적"])
    return f"{caption_body}\n\n{' '.join(cat_tags + BASE_HASHTAGS)}"


def write_post(item: dict, translated: dict, image_path: str | None, date: datetime.datetime):
    slug = slugify(translated["title"])
    filename = f"{date.strftime('%Y-%m-%d')}-{slug}.md"
    filepath = POSTS_DIR / filename
    counter = 1
    while filepath.exists():
        filepath = POSTS_DIR / f"{date.strftime('%Y-%m-%d')}-{slug}-{counter}.md"
        counter += 1
    rel_image = ("/blog/assets/images/" + Path(image_path).name) if image_path else None
    image_line = f'image: "{rel_image}"' if rel_image else ""
    image_md   = f"![썸네일]({rel_image})\n\n" if rel_image else ""
    content = f"""---
layout: post
title: "{translated['title'].replace('"', "'")}"
date: {date.strftime('%Y-%m-%d %H:%M:%S')} +0900
category: "{translated['category']}"
source_name: "{item['source_name']}"
source_url: "{item['source_url']}"
{image_line}
---

{image_md}{translated['content']}

---
*원문: [{item['source_name']}]({item['source_url']})*
"""
    filepath.write_text(content, encoding="utf-8")
    print(f"  [POST] {filepath.name}")
    return filepath


def main():
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    ig_user_id    = os.environ.get("INSTAGRAM_USER_ID")
    ig_token      = os.environ.get("INSTAGRAM_ACCESS_TOKEN")

    if not anthropic_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    instagram_enabled = bool(ig_user_id and ig_token)
    if not instagram_enabled:
        print("[INFO] Instagram secrets not set — skipping Instagram posting")

    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    claude    = anthropic.Anthropic(api_key=anthropic_key)
    ig_poster = InstagramPoster(ig_user_id, ig_token) if instagram_enabled else None
    seen      = load_seen()
    now_kst   = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    now_utc   = datetime.datetime.now(datetime.timezone.utc)

    print(f"[{now_kst.strftime('%Y-%m-%d %H:%M')} KST] Fetching RSS feeds (last {MAX_AGE_HOURS}h only)...")

    all_items = []
    for source in RSS_SOURCES:
        items = fetch_rss(source, now_utc)
        transfer_items = [i for i in items if is_transfer_related(i)]
        print(f"  {source['name']}: {len(items)}개 최신, {len(transfer_items)}개 이적 관련")
        all_items.extend(transfer_items)

    new_items = [i for i in all_items if item_id(i["source_url"]) not in seen]
    print(f"\nNew: {len(new_items)}개 (이미 처리 {len(all_items) - len(new_items)}개 스킵)\n")

    if not new_items:
        print("Nothing new. Exiting.")
        return

    posted = 0
    for item in new_items[:MAX_NEW_POSTS]:
        pub = parse_pub_date(item["pub_date"])
        pub_str = pub.astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M") if pub else "?"
        print(f"── [{pub_str} KST] {item['title'][:60]}")

        translated = translate_item(claude, item)
        if not translated:
            continue

        post_time = now_kst - datetime.timedelta(minutes=posted * 3)
        slug = slugify(translated["title"])

        local_path, public_url = None, None
        if translated.get("image_prompt"):
            local_path, public_url = generate_image(translated["image_prompt"], slug, post_time)

        if ig_poster and public_url:
            ig_poster.post_image(public_url, build_instagram_caption(translated))

        write_post(item, translated, local_path, post_time)
        seen.add(item_id(item["source_url"]))
        posted += 1
        print()

    save_seen(seen)
    print(f"Done. {posted}개 포스트 생성.")
    if posted < MIN_POSTS:
        print(f"[WARN] {posted}개 생성 (목표 {MIN_POSTS}개 이상)")


if __name__ == "__main__":
    main()
