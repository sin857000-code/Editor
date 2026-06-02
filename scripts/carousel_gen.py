#!/usr/bin/env python3
"""
Generates 5-slide Instagram carousel images from transfer news content.
Design DNA: dark minimalist (#131316 bg, #CF5C3F orange accent) — 1080×1350px
"""

from __future__ import annotations

import html as html_escape_lib
from pathlib import Path
from playwright.sync_api import sync_playwright

SLIDE_W = 1080
SLIDE_H = 1350

DARK_BG  = "#131316"
ORANGE   = "#CF5C3F"
WHITE    = "#FFFFFF"
GRAY     = "rgba(255,255,255,0.42)"
CARD_BG  = "rgba(255,255,255,0.05)"

_FONT_IMPORT = "@import url('https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard.css');"

_BASE_CSS = f"""
{_FONT_IMPORT}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  width: {SLIDE_W}px;
  height: {SLIDE_H}px;
  background: {DARK_BG};
  color: {WHITE};
  font-family: 'Pretendard', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif;
  overflow: hidden;
  position: relative;
}}
.wrap {{
  width: 100%; height: 100%;
  display: flex; flex-direction: column;
  padding: 88px 96px;
}}
.label {{
  font-size: 22px; font-weight: 700;
  letter-spacing: 0.14em;
  color: {ORANGE}; text-transform: uppercase;
}}
.accent-bar {{
  width: 56px; height: 4px;
  background: {ORANGE}; border-radius: 2px;
  margin: 28px 0;
}}
.headline {{
  font-size: 74px; font-weight: 900;
  line-height: 1.15; color: {WHITE};
  word-break: keep-all;
}}
.headline .hl {{ color: {ORANGE}; }}
.subtext {{
  font-size: 34px; font-weight: 400;
  color: {GRAY}; line-height: 1.6;
  word-break: keep-all;
}}
.bullet-list {{
  display: flex; flex-direction: column; gap: 20px;
  margin-top: 16px;
}}
.bullet {{
  display: flex; align-items: flex-start; gap: 20px;
  font-size: 36px; font-weight: 500; line-height: 1.5;
  word-break: keep-all;
}}
.bullet-dot {{
  width: 12px; height: 12px;
  background: {ORANGE}; border-radius: 50%;
  flex-shrink: 0; margin-top: 14px;
}}
.spacer {{ flex: 1; }}
.slide-num {{
  position: absolute; bottom: 80px; right: 96px;
  font-size: 22px; font-weight: 700;
  color: {GRAY}; letter-spacing: 0.08em;
}}
.fact-grid {{
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 24px; margin-top: 32px;
}}
.fact-card {{
  background: {CARD_BG};
  border: 1px solid rgba(207,92,63,0.25);
  border-radius: 16px;
  padding: 36px 32px;
}}
.fact-card .key {{
  font-size: 22px; font-weight: 600;
  color: {GRAY}; letter-spacing: 0.1em; text-transform: uppercase;
  margin-bottom: 12px;
}}
.fact-card .value {{
  font-size: 40px; font-weight: 800;
  color: {WHITE}; line-height: 1.2;
}}
.fact-card .value .hl {{ color: {ORANGE}; }}
.cta-box {{
  border: 2px solid {ORANGE};
  border-radius: 20px;
  padding: 40px 48px;
  margin-top: 40px;
  text-align: center;
}}
.cta-box .cta-text {{
  font-size: 42px; font-weight: 800;
  line-height: 1.4; color: {WHITE};
}}
.cta-box .cta-sub {{
  font-size: 28px; color: {GRAY};
  margin-top: 16px;
}}
"""

def _h(text: str) -> str:
    return html_escape_lib.escape(str(text))


def _slide_html(body_content: str, slide_num: int, total: int = 5) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<style>{_BASE_CSS}</style>
</head>
<body>
{body_content}
<div class="slide-num">{slide_num} / {total}</div>
</body>
</html>"""


def slide_cover(data: dict, slide_num: int = 1) -> str:
    category  = _h(data.get("category", "이적 뉴스"))
    headline  = _h(data.get("headline", ""))
    hl_word   = _h(data.get("headline_accent", ""))
    meta_line = _h(data.get("meta", ""))

    if hl_word and hl_word in headline:
        parts = headline.split(hl_word, 1)
        hl_html = f'{parts[0]}<span class="hl">{hl_word}</span>{parts[1]}'
    else:
        hl_html = headline

    body = f"""
<div class="wrap">
  <div class="label">⚽ 이적 뉴스 · {category}</div>
  <div class="spacer"></div>
  <div class="headline">{hl_html}</div>
  <div class="accent-bar"></div>
  <div class="subtext">{meta_line}</div>
  <div style="height:60px"></div>
</div>"""
    return _slide_html(body, slide_num)


def slide_fact(data: dict, slide_num: int = 2) -> str:
    label  = _h(data.get("label", "FACT"))
    title  = _h(data.get("title", ""))
    facts  = data.get("facts", [])

    cards_html = ""
    for f in facts[:4]:
        val = _h(f.get("value", ""))
        if f.get("accent"):
            val = f'<span class="hl">{val}</span>'
        cards_html += f"""
    <div class="fact-card">
      <div class="key">{_h(f.get("key",""))}</div>
      <div class="value">{val}</div>
    </div>"""

    body = f"""
<div class="wrap">
  <div class="label">{label}</div>
  <div style="height:32px"></div>
  <div class="headline" style="font-size:60px">{title}</div>
  <div class="fact-grid">{cards_html}
  </div>
  <div class="spacer"></div>
</div>"""
    return _slide_html(body, slide_num)


def slide_bullets(data: dict, slide_num: int = 3) -> str:
    label   = _h(data.get("label", ""))
    title   = _h(data.get("title", ""))
    bullets = data.get("bullets", [])

    bullets_html = ""
    for b in bullets[:4]:
        bullets_html += f"""
    <div class="bullet">
      <div class="bullet-dot"></div>
      <div>{_h(b)}</div>
    </div>"""

    body = f"""
<div class="wrap">
  <div class="label">{label}</div>
  <div style="height:32px"></div>
  <div class="headline" style="font-size:58px">{title}</div>
  <div class="accent-bar"></div>
  <div class="bullet-list">{bullets_html}
  </div>
  <div class="spacer"></div>
</div>"""
    return _slide_html(body, slide_num)


def slide_outro(data: dict, slide_num: int = 5) -> str:
    headline = _h(data.get("headline", "다음 이적 소식도 함께해요"))
    sub      = _h(data.get("sub", "저장하고 팔로우하면 매일 업데이트를 먼저 받아요"))

    body = f"""
<div class="wrap">
  <div class="label">FOLLOW &amp; SAVE</div>
  <div class="spacer"></div>
  <div class="headline" style="font-size:64px">{headline}</div>
  <div class="accent-bar"></div>
  <div class="cta-box">
    <div class="cta-text">⚽ 해외 이적 뉴스<br>매일 한국어로</div>
    <div class="cta-sub">{sub}</div>
  </div>
  <div style="height:60px"></div>
</div>"""
    return _slide_html(body, slide_num)


def render_slides(html_list: list[str], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H})
        for i, html in enumerate(html_list, 1):
            page.set_content(html, wait_until="networkidle")
            page.evaluate("() => document.fonts.ready")
            page.wait_for_timeout(600)
            out = output_dir / f"slide-{i:02d}.png"
            page.screenshot(
                path=str(out),
                clip={"x": 0, "y": 0, "width": SLIDE_W, "height": SLIDE_H},
                type="png",
            )
            saved.append(out)
            print(f"  [CAROUSEL] slide-{i:02d}.png")
        browser.close()

    return saved


def build_carousel(slides_data: dict, output_dir: Path) -> list[Path]:
    html_list = [
        slide_cover(slides_data["cover"],    slide_num=1),
        slide_fact(slides_data["fact"],      slide_num=2),
        slide_bullets(slides_data["player"], slide_num=3),
        slide_bullets(slides_data["impact"], slide_num=4),
        slide_outro(slides_data["outro"],    slide_num=5),
    ]
    return render_slides(html_list, output_dir)
