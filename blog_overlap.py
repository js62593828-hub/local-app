"""
같은 블로그 중복·짝 글 확인 — 핵심 단어 기준 (조회 전용, 어떤 json 파일도 쓰지 않습니다).

V4에서 네이버 SHOP(shop_naver)+네이버 일반(naver_tips)는 같은 네이버 블로그,
워드프레스 SHOP(shop_wp)+워드프레스 일반(wp_tips)는 같은 싼세계 사이트입니다. similarity.py는 같은 채널 안에서 "거의 같은 글자"만
잡기 때문에 "문풍지 추천(숍) ↔ 문풍지 붙이는 방법(일반)" 같은 쌍을 못 잡습니다. 이 파일은 그걸 보완하는 추가 기능이고
similarity.py와 🔴🟡 배지는 그대로 둡니다.

핵심 단어: 키워드와 cluster_seed(제목은 안 씀 — 수식어가 많아 오탐이 많음)를 공백·기호로 나누고, 끝의 조사를 떼고,
2글자 이상만 남기고, GENERIC_WORDS("추천·방법…")와 숫자로 시작하는 말은 뺍니다.
판정 (한쪽 단어 집합 vs 다른 쪽):
  🔴 "같은 주제"  : 핵심 단어가 하나라도 똑같음
  🟡 "비슷한 주제": 한쪽 핵심 단어가 다른 쪽 핵심 단어 안에 들어 있음 (예: 김치통 ⊂ 스텐김치통).
                    짧은 쪽이 2글자면 긴 쪽의 "앞부분"일 때만 인정 (패딩 ⊂ 패딩전용 O, 세척 ⊂ 코세척 X)
관계: 숍글↔일반글이면 "짝 글"(내부 링크 후보), 같은 종류끼리(일반↔일반, 숍↔숍)면 "중복 위험"(기존 글 보강 또는 상황을 더 좁히기).
숍글 판정(V5): 공용 판정 shop_judge(최종본·목록·현황판과 같음) — 옛 숍 채널(shop_naver/shop_wp) 글은 항상 숍글, 네이버·싼세계 글은
저장된 is_shop_post 값이 있으면 그 값·없으면 링크 자동 판정. (링크 있는 새 글이 없는 지금 데이터에서는 예전 "채널이 다르면 짝 글" 판정과 결과가 똑같습니다.)
같은 묶음(cluster_seed)끼리는 시드가 같아서 늘 겹치므로, "시드 ↔ 시드"끼리의 비교는 빼고
(키워드 ↔ 키워드, 키워드 ↔ 시드)만 봅니다.
싼세계 채널 글이 네이버 블로그 글과 같은/비슷한 주제면 "🔁 네이버 재활용" 정보도 같이 돌려줍니다.

이 파일은 app을 import하지 않습니다 — 채널 정보(채널 id → label·queue_file·articles_file·platforms)는 호출하는 쪽이 넘깁니다.
"""
import json
import os
import re
from datetime import date, datetime

import shop_judge

# ---------- 기준값 (여기만 고치면 됩니다) ----------
BLOGS = {
    "naver": ["shop_naver", "naver_tips"],
    "ssansegae": ["shop_wp", "wp_tips"],
}
SHOP_CHANNELS = ("shop_naver", "shop_wp")  # 옛 숍 채널 — 글에 is_shop_post 값이 없으면 이 채널 글은 숍글
REUSE_SOURCE_BLOG = "naver"       # 🔁 재활용 정보의 출처 블로그 (싼세계 채널 글에만 표시)
REUSE_TARGET_BLOG = "ssansegae"
REUSE_STALE_DAYS = 7               # 네이버 발행 후 이 일수가 지났으면 "7일 지남"
MAX_SHOWN = 5                      # 배지에서 보여줄 최대 개수

# 핵심 단어에서 뺄 "흔한 말" (나중에 여기만 고치면 됩니다)
GENERIC_WORDS = set(
    """추천 방법 고르는법 고르는 기준 비교 차이 이유 총정리 정리 점검 체크리스트 꺼내기 붙이는 세탁법 보관법 사용법 가지 순서
    원인 해결 vs 종류 종류별 소재 필요할까 제대로 쿠팡 인기 가성비 후기 리뷰 꿀팁 겨울 겨울철 여름 가을 봄 냄새 효과 장단점
    가격 브랜드 신제품 전용""".split()
)
# 조사를 뗄 때 "드라이"가 "드라"가 돼서 "헤어드라이기"와 엉뚱하게 겹치는 걸 막으려고 추가한 말
GENERIC_WORDS |= {"드라이"}
# 키워드가 제목 문장 통째로 들어간 글(일반 채널 등)에서 엉뚱하게 겹쳤던 말 (실제 데이터 시험에서 추가)
GENERIC_WORDS |= {"하나", "하나예요", "고민", "고민된다면"}

PARTICLES = ("으로", "은", "는", "이", "가", "을", "를", "에", "의", "로", "도", "만")  # 긴 것(으로)부터 확인
_SPLIT_RE = re.compile(r"[\W_]+|vs", re.IGNORECASE)

RELATION_PAIR = "짝 글"
RELATION_DUP = "중복 위험"
RELATION_HINT = {
    RELATION_PAIR: "내부 링크 후보",
    RELATION_DUP: "기존 글 보강 또는 상황을 더 좁히기",
}

_ITEMS_CACHE: dict = {}  # {(블로그, 파일 수정시각들): 항목 목록} — 파일이 바뀌면 자동으로 새로 읽음


def blog_of(channel_id: str):
    """이 채널이 속한 블로그 키 ("naver" / "ssansegae"), 롱텀 등 해당 없으면 None."""
    for key, chans in BLOGS.items():
        if channel_id in chans:
            return key
    return None


def is_shop(channel_id: str, is_shop_post=None) -> bool:
    """숍글인지. is_shop_post(글 데이터 값)가 있으면 그 값, 없으면(None) 옛 숍 채널이면 숍글."""
    if is_shop_post is None:
        return channel_id in SHOP_CHANNELS
    return bool(is_shop_post)


# ---------- 핵심 단어 ----------

def _clean(token: str):
    tok = token.strip().lower()
    if not tok or tok[0].isdigit():
        return None
    stem = tok
    for p in PARTICLES:
        if tok.endswith(p) and len(tok) - len(p) >= 2:
            stem = tok[: -len(p)]
            break
    if len(stem) < 2 or tok in GENERIC_WORDS or stem in GENERIC_WORDS:
        return None
    return stem


def core_words(text) -> list:
    """한 문장에서 핵심 단어 목록(중복 없이, 나온 순서대로)."""
    out = []
    for tok in _SPLIT_RE.split(text or ""):
        w = _clean(tok)
        if w and w not in out:
            out.append(w)
    return out


def _word_sets(keyword, seed) -> dict:
    return {"kw": core_words(keyword), "seed": core_words(seed)}


def _contain_pairs(wa: list, wb: list) -> list:
    """(짧은 쪽, 긴 쪽) — 서로 다른 단어인데 짧은 쪽이 긴 쪽 안에 들어 있는 쌍."""
    out = []
    for a in wa:
        for b in wb:
            if a != b and len(a) >= 2 and len(b) >= 2:
                s, l = (a, b) if len(a) <= len(b) else (b, a)
                # 짧은 쪽이 2글자면 긴 쪽의 "앞부분"일 때만(패딩 ⊂ 패딩전용 O, 세척 ⊂ 코세척 X), 3글자 이상은 어디든 포함되면 인정
                ok = l.startswith(s) if len(s) == 2 else s in l
                if ok and (s, l) not in out:
                    out.append((s, l))
    return out


def compare_words(a: dict, b: dict):
    """{"kw":[...], "seed":[...]} 두 개를 비교해 {"level","words","reason"}를 돌려줍니다. 안 겹치면 None.
    시드↔시드 비교는 뺍니다(같은 묶음은 늘 겹치므로)."""
    combos = [(a["kw"], b["kw"]), (a["kw"], b["seed"]), (a["seed"], b["kw"])]
    shared = []
    for wa, wb in combos:
        for w in wa:
            if w in wb and w not in shared:
                shared.append(w)
    if shared:
        return {"level": "red", "words": shared, "reason": "핵심 단어 같음: " + ", ".join(shared)}
    pairs = []
    for wa, wb in combos:
        for p in _contain_pairs(wa, wb):
            if p not in pairs:
                pairs.append(p)
    if pairs:
        return {
            "level": "yellow",
            "words": [f"{s} ⊂ {l}" for s, l in pairs],
            "reason": "비슷한 단어: " + ", ".join(f"{s} ⊂ {l}" for s, l in pairs),
        }
    return None


# ---------- 항목 읽기 ----------

def _read_json(path: str, read_text):
    try:
        text = read_text(path) if read_text else open(path, "r", encoding="utf-8").read()
    except FileNotFoundError:
        return None
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _state(article: dict, platforms: list, today: str) -> dict:
    """저장된 글 데이터에서 상태를 판단합니다 (app._stored_state와 같은 규칙, 라벨만 이 기능용)."""
    best = {"level": "empty", "label": "승인만 (원고 없음)", "date": None}
    rank = {"published": 3, "scheduled": 2, "generated": 1, "empty": 0}
    for p in platforms:
        d = (article.get("platform_published_at") or {}).get(p)
        if d:
            cur = (
                {"level": "scheduled", "label": f"예약 {d}", "date": d}
                if d > today
                else {"level": "published", "label": f"발행 {d}", "date": d}
            )
        elif (article.get("platform_raw") or {}).get(p) or (article.get("platform_title") or {}).get(p):
            cur = {"level": "generated", "label": "작성 중", "date": None}
        else:
            continue
        if rank[cur["level"]] > rank[best["level"]]:
            best = cur
    return best


def load_items(blog_key: str, channels: dict, read_text=None, excluded: dict = None) -> list:
    """그 블로그 두 채널에서 "승인됐거나 원고·제목이 있는" 항목들. channels = {채널id: {label, queue_file, articles_file, platforms}}.
    excluded = {채널id: [글 id...]} — "기타 리뷰"로 표시한 글은 비교 대상에서 뺍니다."""
    excluded = excluded or {}
    chans = [c for c in BLOGS.get(blog_key, []) if c in channels]
    sig = []
    for c in chans:
        for f in (channels[c]["queue_file"], channels[c]["articles_file"]):
            sig.append(os.stat(f).st_mtime_ns if os.path.exists(f) else 0)
    ex_sig = tuple(sorted((c, tuple(sorted(excluded.get(c) or []))) for c in chans))
    key = (blog_key, tuple(chans), tuple(sig), ex_sig)
    if key in _ITEMS_CACHE:
        return _ITEMS_CACHE[key]

    today = datetime.now().strftime("%Y-%m-%d")
    items = []
    for c in chans:
        cfg = channels[c]
        queue = _read_json(cfg["queue_file"], read_text) or []
        articles = _read_json(cfg["articles_file"], read_text) or {}
        platforms = cfg["platforms"]
        skip_ids = set(excluded.get(c) or [])
        for it in queue:
            if it.get("id") in skip_ids:
                continue
            art = articles.get(it.get("id")) or {}
            title = next((t for t in ((art.get("platform_title") or {}).get(p) for p in platforms) if t), "")
            state = _state(art, platforms, today)
            has_text = state["level"] != "empty" or bool(title)
            if not (it.get("approved") or has_text):
                continue  # 승인 안 된 자동 추천 키워드는 비교 대상이 아님
            url = next((u for u in ((art.get("platform_published_url") or {}).get(p) for p in platforms) if u), "")
            items.append(
                {
                    "id": it["id"],
                    "channel": c,
                    "channel_label": cfg.get("label") or c,
                    "keyword": it["keyword"],
                    "cluster_seed": it.get("cluster_seed"),
                    "week": it.get("week"),
                    "title": title,
                    "state": state,
                    "url": url,
                    "words": _word_sets(it["keyword"], it.get("cluster_seed")),
                    "is_shop": shop_judge.is_shop_article(art, c),  # 최종본·목록·현황판과 같은 공용 판정(링크 자동 판정 포함)
                }
            )
    for k in [k for k in _ITEMS_CACHE if k[0] == blog_key and k != key]:
        del _ITEMS_CACHE[k]
    _ITEMS_CACHE[key] = items
    return items


# ---------- 겹침 찾기 ----------

def _entry(target_shop: bool, it: dict, m: dict) -> dict:
    relation = RELATION_DUP if it["is_shop"] == target_shop else RELATION_PAIR
    return {
        "id": it["id"],
        "level": m["level"],
        "relation": relation,
        "hint": RELATION_HINT[relation],
        "channel": it["channel"],
        "channel_label": it["channel_label"],
        "keyword": it["keyword"],
        "title": it["title"],
        "state": it["state"]["label"],
        "week": it["week"],
        "url": it["url"],
        "reason": m["reason"],
        "words": m["words"],
    }


def find_overlaps(target: dict, items: list, sort_by_relation: bool = True) -> list:
    """target = {id(없으면 None), channel, keyword, cluster_seed, is_shop(선택)}. 같은 블로그 항목 중 겹치는 것(🔴 먼저, 같은 종류 먼저).
    target에 is_shop이 없으면(None) 채널로 판단합니다(옛 숍 채널이면 숍글)."""
    tw = _word_sets(target.get("keyword"), target.get("cluster_seed"))
    target_shop = is_shop(target.get("channel"), target.get("is_shop"))
    out = []
    for it in items:
        if target.get("id") and it["id"] == target["id"]:
            continue
        m = compare_words(tw, it["words"])
        if m:
            out.append(_entry(target_shop, it, m))
    if sort_by_relation:
        out.sort(key=lambda e: (e["level"] != "red", e["relation"] != RELATION_DUP))
    else:
        out.sort(key=lambda e: e["level"] != "red")  # 재활용 목록용: 예전처럼 🔴/🟡 단계만으로 정렬
    return out


def _days_since(date_str: str):
    try:
        return (date.today() - datetime.strptime(date_str, "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        return None


def find_reuse(target: dict, naver_items: list) -> list:
    """싼세계 채널 글과 같은/비슷한 주제의 네이버 블로그 글 (최대 MAX_SHOWN개)."""
    if target.get("channel") not in BLOGS[REUSE_TARGET_BLOG]:
        return []
    out = []
    for e in find_overlaps({**target, "id": None, "channel": "__ssansegae__"}, naver_items, sort_by_relation=False):
        item = next((i for i in naver_items if i["id"] == e["id"]), None)
        st = item["state"] if item else {"level": "empty", "label": e["state"], "date": None}
        if st["level"] == "published":
            days = _days_since(st["date"])
            if days is not None:
                text = f"🔁 네이버에 같은 주제 — 발행 {days}일 전 ({'7일 지남' if days >= REUSE_STALE_DAYS else '7일 안 지남'})"
            else:
                text = f"🔁 네이버에 같은 주제 — {st['label']}"
        else:
            text = f"🔁 네이버에 같은 주제 — {st['label']}"
        # 재활용 항목은 "다른 블로그(네이버)의 글"이라 예전부터 늘 짝 글로 표시했습니다 — 숍글 판정과 상관없이 그대로 유지
        out.append({**e, "relation": RELATION_PAIR, "hint": RELATION_HINT[RELATION_PAIR], "text": text})
    return out[:MAX_SHOWN]


def overlap_overview(blog_key: str, targets: list, channels: dict, read_text=None, excluded: dict = None) -> dict:
    """{target_id: {"level","count","matches"[:5],"reuse"[...]}} — 겹침이나 재활용이 있는 항목만 들어 있습니다. 화면 배지용."""
    items = load_items(blog_key, channels, read_text, excluded)
    naver_items = None
    out = {}
    for t in targets:
        ms = find_overlaps(t, items)
        reuse = []
        if blog_key == REUSE_TARGET_BLOG:
            if naver_items is None:
                naver_items = load_items(REUSE_SOURCE_BLOG, channels, read_text, excluded)
            reuse = find_reuse(t, naver_items)
        if ms or reuse:
            out[t["id"]] = {
                "level": ("red" if any(m["level"] == "red" for m in ms) else "yellow") if ms else None,
                "count": len(ms),
                "matches": ms[:MAX_SHOWN],
                "reuse": reuse,
            }
    return out


def overlaps_for_new(blog_key: str, target: dict, channels: dict, read_text=None, excluded: dict = None) -> dict:
    """저장 전 새 키워드 확인용: {"matches": [...전부], "reuse": [...]}."""
    items = load_items(blog_key, channels, read_text, excluded)
    reuse = []
    if blog_key == REUSE_TARGET_BLOG:
        reuse = find_reuse(target, load_items(REUSE_SOURCE_BLOG, channels, read_text, excluded))
    return {"matches": find_overlaps(target, items), "reuse": reuse}


def all_pairs(blog_key: str, channels: dict, read_text=None, excluded: dict = None) -> list:
    """(시험표용) 그 블로그 비교 대상 글끼리 겹치는 쌍 전체. [{a, b, level, relation, reason}]"""
    items = load_items(blog_key, channels, read_text, excluded)
    out = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            m = compare_words(a["words"], b["words"])
            if m:
                rel = RELATION_DUP if a["is_shop"] == b["is_shop"] else RELATION_PAIR
                out.append({"a": a, "b": b, "level": m["level"], "relation": rel, "reason": m["reason"]})
    out.sort(key=lambda p: (p["level"] != "red", p["relation"] != RELATION_DUP))
    return out
