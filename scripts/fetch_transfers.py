#!/usr/bin/env python3
"""
Football transfer news crawler + Claude API translator + DALL-E 3 image generator
+ Instagram Graph API auto-poster.

Flow per post:
  1. RSS fetch & filter
  2. Claude: translate → Korean title/content/hashtags + DALL-E prompt
  3. DALL-E 3: generate 1024×1024 image (square, optimal for Instagram & blog)
  4. Instagram Graph API: post image + caption using the temporary DALL-E URL
  5. Save image to blog/assets/images/ + write Jekyll _posts/ markdown
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
from pathlib import Path

import anthropic
from openai import OpenAI

# ── Config ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
POSTS_DIR = REPO_ROOT / "blog" / "_posts"
IMAGES_DIR = REPO_ROOT / "blog" / "assets" / "images"
SEEN_FILE  = REPO_ROOT / "scripts" / ".seen_ids.json"

MAX_NEW_POSTS = 5
MIN_POSTS = 3

INSTAGRAM_POST_TO = os.environ.get("INSTAGRAM_POST_TO", "all")  # "all" | "first" | "none"

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


def item_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


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


# ── Claude: translate + generate prompts ─────────────────────────────────────

def translate_item(claude: anthropic.Anthropic, item: dict) -> dict | None:
    prompt = f"""당신은 해외 축구를 깊이 아는 한국인 축구 전문 기자입니다. 아래 이적 뉴스를 바탕으로 한국 축구 팬들이 흥미롭게 읽을 수 있는 블로그 포스트와 인스타그램 게시물을 작성하세요.

원문 제목: {item['title']}
원문 내용: {item['description']}
출처: {item['source_name']}

작성 지침:

1. title (제목)
   - 한국 팬에게 익숙한 선수명/팀명 표기
   - 클릭하고 싶은 강렬한 제목. 숫자·금액·팀명으로 임팩트를 줘도 좋음
   - 예: "€8000만 몸값 검증 완료 — 아스날, 드디어 원하던 그 선수 잡았다"

2. category: 영입 확정 / 이적 협상 / 임대 / 방출/계약만료 / 이적 소문 중 하나

3. content (블로그 본문, 800~1200자)
   구조:
   [단락1 — 핵심 팩트] 이적 사실을 명확하고 생생하게 전달. 이적료·계약기간·조건 등 숫자 강조.
   [단락2 — 맥락과 의미] 이 선수가 왜 중요한지, 해당 팀에게 어떤 의미인지. 최근 시즌 성적·역할·팀의 공백을 구체적으로 설명.
   [단락3 — 전망과 팬 반응] 이 이적이 리그 판도에 미치는 영향, 기대 또는 우려. 팬 입장에서 설레거나 아쉬운 포인트를 짚어줌.

   문체: 전문적이지만 친근함. 축구 팬끼리 이야기하는 느낌. 단순 번역이 아닌 기자의 시각과 평가가 담긴 글.

4. instagram_caption (인스타그램 캡션)
   - 이모지 3~5개로 감정·강도 표현
   - 5~7줄 구성: 훅 첫 줄 → 핵심 내용 → 의미/반응 → 마무리 한 줄
   - 독자가 저장하거나 공유하고 싶을 만큼 압축적이고 감각적으로
   - 해시태그 제외 (별도 추가됨)

5. image_prompt (이미지 생성용 영어 프롬프트)
   - 이적 뉴스의 분위기를 상징하는 축구 장면
   - 실제 선수 얼굴·이름·유니폼 번호·팀 로고 절대 포함 금지
   - square composition, vibrant colors, cinematic lighting 명시

반드시 아래 JSON 형식으로만 응답 (다른 텍스트 없이):
{{"title":"...","category":"...","content":"...","instagram_caption":"...","image_prompt":"..."}}"""

    try:
        message = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
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


# ── DALL-E 3: image generation ───────────────────────────────────────────────

def generate_image(openai_client: OpenAI, prompt: str, slug: str, date: datetime.datetime) -> tuple[str | None, str | None]:
    """
    Returns (local_path, temp_url).
    local_path: saved to blog/assets/images/ (for blog embedding).
    temp_url:   DALL-E CDN URL, valid ~1 hour (used immediately for Instagram).
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{date.strftime('%Y%m%d')}-{slug[:50]}.png"
    filepath = IMAGES_DIR / filename

    full_prompt = (
        prompt.rstrip(".")
        + ". Square composition 1:1, digital illustration, vibrant colors, "
          "no text, no logos, no player faces, football/soccer theme, "
          "cinematic lighting, high quality."
    )

    try:
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=full_prompt,
            size="1024x1024",   # square: optimal for Instagram feed + blog thumbnail
            quality="standard",
            n=1,
        )
        temp_url = response.data[0].url

        req = urllib.request.Request(temp_url, headers={"User-Agent": "FootballBot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            filepath.write_bytes(resp.read())

        print(f"  [IMG] {filename}")
        return str(filepath), temp_url

    except Exception as e:
        print(f"  [WARN] Image generation failed: {e}")
        return None, None


# ── Instagram Graph API ──────────────────────────────────────────────────────

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
        """Create media container then publish. Returns media ID on success."""
        try:
            # Step 1: create container
            container = self._post(
                f"/{self.user_id}/media",
                {"image_url": image_url, "caption": caption},
            )
            creation_id = container.get("id")
            if not creation_id:
                print(f"  [WARN] Instagram: no creation_id — {container}")
                return None

            # Step 2: publish
            result = self._post(
                f"/{self.user_id}/media_publish",
                {"creation_id": creation_id},
            )
            media_id = result.get("id")
            print(f"  [IG] Posted — media_id: {media_id}")
            return media_id

        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  [WARN] Instagram HTTP {e.code}: {body}")
            return None
        except Exception as e:
            print(f"  [WARN] Instagram error: {e}")
            return None


def build_instagram_caption(translated: dict) -> str:
    category = translated.get("category", "이적 소문")
    caption_body = translated.get("instagram_caption", translated.get("content", ""))
    cat_tags = CATEGORY_HASHTAGS.get(category, ["#축구이적"])
    hashtags = " ".join(cat_tags + BASE_HASHTAGS)
    return f"{caption_body}\n\n{hashtags}"


# ── Blog post writer ─────────────────────────────────────────────────────────

def write_post(item: dict, translated: dict, image_path: str | None, date: datetime.datetime):
    slug = slugify(translated["title"])
    filename = f"{date.strftime('%Y-%m-%d')}-{slug}.md"
    filepath = POSTS_DIR / filename

    counter = 1
    while filepath.exists():
        filepath = POSTS_DIR / f"{date.strftime('%Y-%m-%d')}-{slug}-{counter}.md"
        counter += 1

    rel_image = None
    if image_path:
        rel_image = "/blog/assets/images/" + Path(image_path).name

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


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    anthropic_key  = os.environ.get("ANTHROPIC_API_KEY")
    openai_key     = os.environ.get("OPENAI_API_KEY")
    ig_user_id     = os.environ.get("INSTAGRAM_USER_ID")
    ig_token       = os.environ.get("INSTAGRAM_ACCESS_TOKEN")

    if not anthropic_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")
    if not openai_key:
        raise SystemExit("OPENAI_API_KEY not set")

    instagram_enabled = bool(ig_user_id and ig_token)
    if not instagram_enabled:
        print("[INFO] Instagram secrets not set — skipping Instagram posting")

    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    claude        = anthropic.Anthropic(api_key=anthropic_key)
    openai_client = OpenAI(api_key=openai_key)
    ig_poster     = InstagramPoster(ig_user_id, ig_token) if instagram_enabled else None
    seen          = load_seen()
    now           = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))

    print(f"[{now.strftime('%Y-%m-%d %H:%M')} KST] Fetching RSS feeds...")

    all_items = []
    for source in RSS_SOURCES:
        items = fetch_rss(source)
        transfer_items = [i for i in items if is_transfer_related(i)]
        print(f"  {source['name']}: {len(items)} total, {len(transfer_items)} transfer-related")
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

        # 1) Generate image (DALL-E URL valid ~1h — use immediately for Instagram)
        local_path, temp_url = None, None
        if translated.get("image_prompt"):
            local_path, temp_url = generate_image(openai_client, translated["image_prompt"], slug, post_time)

        # 2) Post to Instagram (while temp_url is still valid)
        if ig_poster and temp_url:
            caption = build_instagram_caption(translated)
            ig_poster.post_image(temp_url, caption)

        # 3) Write Jekyll post (image already saved locally)
        write_post(item, translated, local_path, post_time)
        seen.add(item_id(item["source_url"]))
        posted += 1
        print()

    save_seen(seen)
    print(f"Done. {posted} post(s) created.")
    if posted < MIN_POSTS:
        print(f"[WARN] Only {posted} posts (target ≥ {MIN_POSTS})")


if __name__ == "__main__":
    main()
