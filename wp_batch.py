# -*- coding: utf-8 -*-
"""W싼세계(wp_tips) 전용: 최종본 화면의 카테고리·태그 미리 채우기 + '주간 원고 일괄 처리' 화면용 계산.

- 읽기만 한다(앱 데이터·워드프레스에 쓰지 않음). 예외: save_schedule 은 _예약표.md 만 고친다(백업 후).
- 같이 쓰는 코드(app.py)에서는 이 파일의 함수만 부른다. 다른 채널 코드는 건드리지 않는다.
"""
import os
import re
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DRAFT_ROOT = os.path.join(ROOT, "pipeline", "wp_drafts")

# 싼세계 워드프레스 카테고리 3개(11/22 확인: 생활용품 24 · 주방용품 50 · 생활가전 53). 이름이 글자 그대로 같을 때만 미리 채운다.
CATS = {"생활용품": 24, "주방용품": 50, "생활가전": 53}
WEEKDAY = "월화수목금토일"


# ───────────────────────── 1부: 카테고리·태그 ─────────────────────────

def split_tags(text: str, limit: int = 10) -> list:
    """app._split_wp_tags 와 같은 규칙(쉼표 , ， 로 나눔 · 앞뒤 공백·맨 앞 # 제거 · 빈 값·중복 제거 · 최대 10개)."""
    out = []
    for part in re.split(r"[,，]", text or ""):
        name = part.strip().lstrip("#").strip()
        if name and name not in out:
            out.append(name)
        if len(out) >= limit:
            break
    return out


def _grab(text: str, label: str) -> str:
    m = re.search(rf"^[ \t]*{label}[ \t]*[:：][ \t]*(.+)$", text or "", flags=re.M)
    return m.group(1).strip().strip("*").strip() if m else ""


_draft_cache = {}


def _draft_by_title() -> dict:
    """pipeline/wp_drafts/<주>/*.md 원고를 '제목:' 줄로 찾을 수 있게 읽는다(읽기만). 파일이 바뀌면 다시 읽는다."""
    out = {}
    try:
        weeks = sorted(os.listdir(DRAFT_ROOT))
    except OSError:
        return out
    for wk in weeks:
        d = os.path.join(DRAFT_ROOT, wk)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md") or fn.startswith("_") or fn.endswith(".facts.md"):
                continue
            p = os.path.join(d, fn)
            try:
                mt = os.path.getmtime(p)
                if _draft_cache.get(p, (None,))[0] != mt:
                    with open(p, encoding="utf-8") as f:
                        _draft_cache[p] = (mt, f.read())
                text = _draft_cache[p][1]
            except OSError:
                continue
            title = _grab(text, "제목")
            if title:
                out[title] = text
    return out


def meta_view(article: dict) -> dict:
    """한 글의 발행 칸에 미리 채울 카테고리·태그. (읽기만)
    출처: ① 앱에 저장된 메타데이터 메모('카테고리 제안'·'태그 제안') ② 같은 제목의 원고 파일 ③ 태그는 본문 끝 '태그:' 줄(# 뗀 것).
    카테고리는 CATS 3개 중 하나와 글자 그대로 같을 때만 채우고, 아니면 비우고 경고를 단다.
    반환: {category, cat_id, tags[list], tag_text, source, warns[list], info}"""
    memo = (article.get("platform_metadata") or {}).get("wordpress") or ""
    title = (article.get("platform_title") or {}).get("wordpress") or ""
    draft = ""
    cat = _grab(memo, "카테고리 제안")
    tag_raw = _grab(memo, "태그 제안")
    source = "메모" if (cat or tag_raw) else ""
    if not cat or not tag_raw:
        draft = _draft_by_title().get(title.strip(), "")
        if draft:
            if not cat:
                cat = _grab(draft, "카테고리 제안")
                source = source or "원고 파일"
            if not tag_raw:
                tag_raw = _grab(draft, "태그 제안")
                source = source or "원고 파일"
    tags = split_tags(tag_raw)
    if not tags:  # 본문 끝 '태그: #… #…' 줄 (# 뗀 것)
        for text in ((article.get("platform_raw") or {}).get("wordpress") or "", draft):
            line = _grab(text.replace("\r\n", "\n"), "태그")
            found = re.findall(r"#([^\s#,]+)", line) if line else []
            if found:
                tags = split_tags(", ".join(found))
                source = source or "원고 파일"
                break
    warns = []
    if not cat:
        warns.append("카테고리 제안을 못 찾았어요 — 직접 적으세요")
    elif cat not in CATS:
        warns.append(f"카테고리 제안 '{cat}' 이(가) 싼세계 3개(생활용품·주방용품·생활가전)와 달라요 — 비워 둠")
        cat = ""
    if not tags:
        warns.append("태그 제안을 못 찾았어요")
    info = f"원고 메타데이터: 카테고리 {cat or '없음'} · 태그 {len(tags)}개 (미리 채운 것, 발행 버튼을 눌러야 반영)"
    return {"category": cat, "cat_id": CATS.get(cat), "tags": tags, "tag_text": ", ".join(tags),
            "source": source, "warns": warns, "info": info}
