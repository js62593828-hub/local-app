"""
V4 글 유형·카테고리 읽기 + 주간 현황판 (조회 전용 — 어떤 json 파일도 쓰지 않습니다).

- 글 유형: 메모(platform_metadata)의 "글 유형:" 줄(metadata_parser.content_type) → A(해결형)/B(점검·시즌형)/C(결정형)
  없으면 숍 채널은 "C(추정)", 일반 채널은 "유형 미기재".
- 카테고리: 메모의 "카테고리 제안". 블로그 카테고리 목록(main.SHOP_CATEGORIES) 중 하나면 그대로, 아니면 "⚠ 카테고리 목록 밖: (원래 값)", 없으면 "미기재".
  예전 이름(main.CATEGORY_ALIASES: "난방·겨울 대비" 등)은 새 이름으로 간주합니다 — 표시·집계만, 저장된 json은 고치지 않습니다.
- 현황판: 한 주(월~일)의 블로그별 발행·예약 편수, 유형·카테고리별 편수, 날짜별 목록과 경고.
  "기타 리뷰"로 표시한 글(v4_excluded.json)은 집계에서 빼고 맨 아래에 개수만 보여줍니다.

app을 import하지 않습니다 — 채널 정보(채널 id → label·queue_file·articles_file·platforms)와 카테고리 목록·예전 이름 별칭표는 호출하는 쪽이 넘깁니다.
"""
import json
import os
from datetime import datetime, timedelta

import metadata_parser
import shop_judge

# ---------- 기준값 (나중에 숫자만 바꾸면 됩니다) ----------
CAR_CATEGORY = "차량 관리"         # 이 카테고리가 그 주 전체의 CAR_MAX_PCT%를 넘으면 노란 경고 한 줄
CAR_MAX_PCT = 10
NAVER_WEEKLY_PLAN = 14           # 네이버 주당 편수 — "10/19 이전" 값. 직접 쓰지 말고 weekly_plan(블로그, 주 월요일)을 쓰세요
SSANSEGAE_WEEKLY_PLAN = 5         # 싼세계 주당 편수 — 10/19 이전 값
LONGTERM_WEEKLY_PLAN = 10         # 롱텀(혜택주머니) 주당 편수 — 10/19 이전 값 (편수만 표시)
# 주차별 목표 표: (적용 시작 월요일, {블로그: 편수}) — 날짜만 추가하면 그 주부터 새 숫자. 목록에 없는 블로그는 위 기본값, 시작일 전 주차는 위 기본값.
WEEKLY_PLAN_SCHEDULE = [
    ("2026-10-19", {"naver": 21, "ssansegae": 14, "longterm": 20}),
    ("2026-11-09", {"longterm": 14}),   # 혜택주머니 주 14편(하루 2칸 × 7일). 이 줄의 날짜만 바꾸면 시작 주가 바뀝니다
]
NAVER_TYPE_TARGET = {"A": 0.40, "B": 0.30, "C": 0.30}   # 네이버 목표 비율 (A 해결형 / B 점검·시즌형 / C 결정형)
TYPE_DIFF_WARN_PP = 10            # 목표와의 차이가 이 %p를 넘으면 노랑
MAX_PER_DAY = 3                   # 하루 이 편수를 넘으면 경고
TYPE_NAMES = {"A": "A 해결형", "B": "B 점검·시즌형", "C": "C 결정형"}
SHOP_CHANNELS = ("shop_naver", "shop_wp")  # 옛 숍 채널: 글에 is_shop_post 값이 없으면 이 채널 글은 숍글, 유형이 없으면 "C(추정)"
SHOP_RATIO_TARGET = 30            # 숍글(링크가 들어간 글) 비율 목표 % (블로그별, 그 주 발행·예약 글 기준)
SHOP_RATIO_WARN = 40              # 이 %를 넘으면 노란 경고
SHOP_SAME_DAY_WARN = 2            # 같은 블로그에서 같은 날 숍글이 이 편수 이상이면 경고 (발행·예약일 기준)
# 블로그별 숍글 기준값 (위 3개 이름은 네이버 값으로 남겨 둠). 싼세계는 숍글 중심(주 14편 중 결정형 11편)이라 따로 둡니다.
SHOP_RATIO_TARGET_BY_BLOG = {"naver": SHOP_RATIO_TARGET, "ssansegae": 80}
SHOP_RATIO_WARN_BY_BLOG = {"naver": SHOP_RATIO_WARN, "ssansegae": 85}
SAME_TYPE_DAY_WARN_BY_BLOG = {"naver": 2, "ssansegae": 3}   # 같은 날 같은 유형이 이 편수 이상이면 경고 (싼세계는 결정형 하루 2편이 기본)
SHOP_SAME_DAY_WARN_BY_BLOG = {"naver": SHOP_SAME_DAY_WARN, "ssansegae": 3}   # 싼세계는 하루 숍글 2편까지 정상, 3편부터 경고
# 싼세계 리뷰 분석을 해결형 일반글로 옮긴 주(SSAN_SOLVE_FROM)부터는 숍글(결정 6편 ≈ 43%) 기준이 낮아집니다. 그 전 주차는 위 값 그대로.
SSAN_SOLVE_FROM = "2026-11-16"   # 이 날짜(월요일) 주부터: 해결 8 · 결정 6, 리뷰 분석 = 해결형 일반글(쿠팡 링크 없음)
SSAN_SOLVE_SHOP_TARGET = 43
SSAN_SOLVE_SHOP_WARN = 50


def ssan_solve_active(week: str = None) -> bool:
    """싼세계 '리뷰 분석 = 해결형' 규칙이 적용되는 주차인지 (2026-11-16 주부터)."""
    return week_monday(week) >= SSAN_SOLVE_FROM


def shop_target_for(blog: str, week: str = None) -> int:
    if blog == "ssansegae" and week is not None and ssan_solve_active(week):
        return SSAN_SOLVE_SHOP_TARGET
    return SHOP_RATIO_TARGET_BY_BLOG.get(blog, SHOP_RATIO_TARGET)


def shop_warn_for(blog: str, week: str = None) -> int:
    if blog == "ssansegae" and week is not None and ssan_solve_active(week):
        return SSAN_SOLVE_SHOP_WARN
    return SHOP_RATIO_WARN_BY_BLOG.get(blog, SHOP_RATIO_WARN)


def shop_same_day_warn_for(blog: str) -> int:
    return SHOP_SAME_DAY_WARN_BY_BLOG.get(blog, SHOP_SAME_DAY_WARN)


BLOG_PLANS = {  # 주당 편수는 weekly_plan(블로그, 주)로 주차마다 따로 계산합니다
    "naver": {"label": "N살림노트", "channels": ["shop_naver", "naver_tips"], "target": NAVER_TYPE_TARGET},
    "ssansegae": {"label": "W싼세계", "channels": ["shop_wp", "wp_tips"], "target": None},
}
LONGTERM_CHANNEL = "longterm_wp"
V4_CHANNELS = [c for b in BLOG_PLANS.values() for c in b["channels"]]

# ---------- 발행 시간표 (10/19 주부터 · 숫자·글자만 바꾸면 됩니다 · "보여 주기"만 하고 예약 시간을 자동으로 넣지 않습니다) ----------
PUBLISH_SLOTS_FROM = "2026-10-19"      # 이 날짜(월요일) 주부터 시간표를 보여 줌. 그 전 주차는 시간표 없음
HOLIDAYS = ["2026-12-25", "2027-01-01"]  # 공휴일은 "일요일" 칸을 씀 (날짜 YYYY-MM-DD를 추가하세요)
PUBLISH_SLOT_NOTE = "각 시간은 ±20분 안에서 발행합니다."
WEEKDAY_KO = "월화수목금토일"
# 블로그별 요일(0=월 … 6=일) → [(시간, 권장 유형)]. 혜택주머니는 유형 없음("")
_NAVER_MON = [("07:00", "A"), ("12:00", "C"), ("20:00", "A")]
_NAVER_TUE_FRI = [("07:00", "B"), ("12:00", "C"), ("20:00", "A")]
_SSAN_WEEKDAY = [("08:00", "해결"), ("19:00", "결정")]      # ~2026-10-26 주 (이전 주차는 그대로 보임)
_SSAN_WEEKEND = [("10:00", "해결"), ("15:00", "해결")]
SSAN_SLOTS_FROM = "2026-11-02"   # 이 날짜(월요일) 주부터 싼세계는 숍글 중심 칸 (결정 11 · 해결 3)
_SSAN_WEEKDAY_V2 = [("08:00", "결정"), ("19:00", "결정")]
_SSAN_SAT_V2 = [("10:00", "해결"), ("15:00", "결정")]
_SSAN_SUN_V2 = [("10:00", "해결"), ("15:00", "해결")]   # 일요일·공휴일
_SSAN_WEEKDAY_V3 = [("08:00", "결정"), ("19:00", "해결")]   # 11/16 주부터 (토·일은 V2와 같음): 해결 8 · 결정 6
_LT_WEEKDAY = [("07:30", ""), ("11:30", ""), ("17:30", "")]
LT_SLOTS_V2_FROM = "2026-11-09"   # 이 날짜(월요일) 주부터 혜택주머니는 주 14편 칸 (매일 07:30 · 17:30)
_LT_WEEKDAY_V2 = [("07:30", ""), ("17:30", "")]
_LT_SAT_V2 = [("07:30", ""), ("17:30", "")]
_LT_SUN_V2 = [("07:30", ""), ("17:30", "")]      # 일요일·공휴일
PUBLISH_SLOTS = {
    "naver": {0: _NAVER_MON, 1: _NAVER_TUE_FRI, 2: _NAVER_TUE_FRI, 3: _NAVER_TUE_FRI, 4: _NAVER_TUE_FRI,
              5: [("09:30", "B"), ("14:00", "A"), ("20:30", "C")], 6: [("09:30", "B"), ("14:00", "A"), ("20:30", "A")]},
    "ssansegae": {0: _SSAN_WEEKDAY, 1: _SSAN_WEEKDAY, 2: _SSAN_WEEKDAY, 3: _SSAN_WEEKDAY, 4: _SSAN_WEEKDAY,
                  5: _SSAN_WEEKEND, 6: _SSAN_WEEKEND},
    "longterm": {0: _LT_WEEKDAY, 1: _LT_WEEKDAY, 2: _LT_WEEKDAY, 3: _LT_WEEKDAY, 4: _LT_WEEKDAY,
                 5: [("10:00", ""), ("16:00", "")], 6: [("10:00", ""), ("15:00", ""), ("20:00", "")]},
}
SLOT_BLOG_LABELS = {"naver": "N살림노트", "ssansegae": "W싼세계", "longterm": "W혜택주머니"}


def _plan_base() -> dict:
    return {"naver": NAVER_WEEKLY_PLAN, "ssansegae": SSANSEGAE_WEEKLY_PLAN, "longterm": LONGTERM_WEEKLY_PLAN}


def weekly_plan(blog: str, week: str = None) -> int:
    """그 주(아무 날짜나 월요일)의 주당 목표 편수. blog = "naver" / "ssansegae" / "longterm".
    WEEKLY_PLAN_SCHEDULE에서 시작일이 그 주 월요일 이하인 가장 늦은 줄의 값, 없으면 기본 상수(10/19 이전 값)."""
    mon = week_monday(week)
    value = _plan_base()[blog]
    for start, plans in sorted(WEEKLY_PLAN_SCHEDULE):
        if start <= mon and blog in plans:
            value = plans[blog]
    return value


def publish_slots_for_day(blog: str, day: str) -> list:
    """그 날짜의 [(시간, 권장 유형)]. 공휴일(HOLIDAYS)은 일요일 칸. 시간표 시작일 전 주차면 빈 목록."""
    if week_monday(day) < PUBLISH_SLOTS_FROM:
        return []
    wd = 6 if day in HOLIDAYS else datetime.strptime(day, "%Y-%m-%d").weekday()
    if blog == "ssansegae" and week_monday(day) >= SSAN_SLOTS_FROM:
        if wd < 5 and ssan_solve_active(day):
            return list(_SSAN_WEEKDAY_V3)
        return list(_SSAN_WEEKDAY_V2 if wd < 5 else (_SSAN_SAT_V2 if wd == 5 else _SSAN_SUN_V2))
    if blog == "longterm" and week_monday(day) >= LT_SLOTS_V2_FROM:
        return list(_LT_WEEKDAY_V2 if wd < 5 else (_LT_SAT_V2 if wd == 5 else _LT_SUN_V2))
    return list(PUBLISH_SLOTS[blog][wd])


def slot_text(slots: list) -> str:
    """[("07:00","B"),...] → "07:00 B · 12:00 C · …" (유형이 없으면 시간만)"""
    return " · ".join(f"{t} {k}".strip() for t, k in slots)


def publish_schedule(week: str) -> list:
    """보여 줄 주의 월~일 7줄 [{date, weekday, holiday, slots: {블로그: [(시간,유형)]}}]. 시간표 시작일 전 주차면 빈 목록."""
    mon = week_monday(week)
    if mon < PUBLISH_SLOTS_FROM:
        return []
    out = []
    for i in range(7):
        day = shift_days(mon, i)
        out.append({
            "date": day, "weekday": WEEKDAY_KO[i], "holiday": day in HOLIDAYS,
            "slots": {b: publish_slots_for_day(b, day) for b in PUBLISH_SLOTS},
        })
    return out


# ---------- 유형·카테고리 읽기 ----------

def memo_text_for(article: dict, platforms: list) -> str:
    """그 채널 플랫폼의 메모를 우선, 없으면 아무 메모나."""
    meta = (article or {}).get("platform_metadata") or {}
    for p in platforms:
        if meta.get(p):
            return meta[p]
    return next((v for v in meta.values() if v), "")


def is_shop_article(article: dict, channel_id: str) -> bool:
    """숍글인지 — 최종본·목록·겹침과 같은 공용 판정(shop_judge): 옛 숍 채널은 항상 숍글, 네이버·싼세계는 저장된
    is_shop_post 값이 있으면 그 값·없으면 링크 자동 판정, 그 밖은 일반."""
    return shop_judge.is_shop_article(article, channel_id)


def classify(memo_text: str, channel_id: str, categories: list, aliases: dict = None, is_shop: bool = None) -> dict:
    """{type_key("A"/"B"/"C"/""), type_label, estimated, category_key(목록의 이름 / "__out__" / "__none__"), category_label}
    aliases = {예전 이름: 새 이름} — 메모가 예전 이름이면 새 이름으로 간주 (읽기만, 메모는 안 바꿈).
    is_shop: 숍글 여부. None이면 채널로 판단(옛 숍 채널이면 숍글) — 유형이 안 적힌 숍글은 "C(추정)"."""
    parsed = metadata_parser.parse_metadata_memo(memo_text or "")
    ctype = parsed.get("content_type") or ""
    estimated = False
    if is_shop is None:
        is_shop = channel_id in SHOP_CHANNELS
    if ctype:
        type_key, type_label = ctype, TYPE_NAMES[ctype]
    elif is_shop:
        type_key, type_label, estimated = "C", "C(추정)", True
    else:
        type_key, type_label = "", "유형 미기재"
    raw = (parsed.get("category") or "").strip()
    raw = (aliases or {}).get(raw, raw)
    if raw in categories:
        cat_key, cat_label = raw, raw
    elif raw:
        cat_key, cat_label = "__out__", f"⚠ 카테고리 목록 밖: {raw}"
    else:
        cat_key, cat_label = "__none__", "미기재"
    return {
        "type_key": type_key, "type_label": type_label, "estimated": estimated,
        "category_key": cat_key, "category_label": cat_label,
    }


# ---------- 읽기 도우미 ----------

def _read_json(path: str, read_text, default):
    try:
        text = read_text(path) if read_text else open(path, "r", encoding="utf-8").read()
    except FileNotFoundError:
        return default
    if not text or not text.strip():
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def week_monday(day: str = None) -> str:
    """아무 날짜(YYYY-MM-DD)가 속한 주의 월요일. 잘못된 값이면 이번 주."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d") if day else datetime.now()
    except (TypeError, ValueError):
        d = datetime.now()
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def shift_days(day: str, delta: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")


def shift_week(monday: str, delta: int) -> str:
    return (datetime.strptime(monday, "%Y-%m-%d") + timedelta(weeks=delta)).strftime("%Y-%m-%d")


def _channel_articles(cfg: dict, read_text, excluded_ids: set, categories: list, channel_id: str, mon: str, sun: str, today: str, aliases: dict = None):
    """한 채널의 글을 (이번 주 발행·예약 행, 작성 중 개수, 제외된 개수)로."""
    queue = _read_json(cfg["queue_file"], read_text, [])
    articles = _read_json(cfg["articles_file"], read_text, {})
    platforms = cfg["platforms"]
    rows, drafting, excluded_n = [], 0, 0
    seen = set()
    for it in queue:
        iid = it.get("id")
        if not iid or iid in seen:
            continue
        seen.add(iid)
        art = articles.get(iid) or {}
        if iid in excluded_ids:
            excluded_n += 1
            continue
        date = next((d for d in ((art.get("platform_published_at") or {}).get(p) for p in platforms) if d), "")
        title = next((t for t in ((art.get("platform_title") or {}).get(p) for p in platforms) if t), "")
        has_text = any((art.get("platform_raw") or {}).get(p) for p in platforms) or bool(title)
        if date:
            if mon <= date <= sun:
                shop = is_shop_article(art, channel_id)
                info = classify(memo_text_for(art, platforms), channel_id, categories, aliases, is_shop=shop)
                # 싼세계 리뷰 분석 글 표시용 (보여 주기만 — 집계·경고에는 쓰지 않음). 순환 import를 피하려고 여기서 불러옵니다.
                review = False
                if channel_id == "wp_tips":
                    import target_keyword_finder
                    review = target_keyword_finder.is_review_item(it, "ssansegae", it.get("week"))
                rows.append({
                    "id": iid, "channel": channel_id, "channel_label": cfg.get("label") or channel_id, "is_shop": shop, "review": review,
                    "date": date, "status": "예약" if date > today else "발행",
                    "title": title or it.get("keyword", ""), "keyword": it.get("keyword", ""), **info,
                })
        elif has_text:
            drafting += 1
    return rows, drafting, excluded_n


def _sunday(mon: str) -> str:
    return (datetime.strptime(mon, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")


def _warnings(rows: list, blog: str = "naver") -> list:
    out = []
    car = sum(1 for r in rows if r["category_key"] == CAR_CATEGORY)
    if rows and car * 100 > len(rows) * CAR_MAX_PCT:
        out.append(f"{CAR_CATEGORY} {car}/{len(rows)}편({round(car / len(rows) * 100)}%) — {CAR_MAX_PCT}% 이내 권장")
    by_day = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    for day in sorted(by_day):
        rs = by_day[day]
        short = day[5:]
        if len(rs) > MAX_PER_DAY:
            out.append(f"{short}: 하루 {len(rs)}편 ({MAX_PER_DAY}편 초과)")
        types = {}
        cats = {}
        for r in rs:
            if r["type_key"]:
                types[r["type_key"]] = types.get(r["type_key"], 0) + 1
            if r["category_key"] not in ("__none__", "__out__"):
                cats[r["category_label"]] = cats.get(r["category_label"], 0) + 1
        shop_n = sum(1 for r in rs if r.get("is_shop"))
        same_day = shop_same_day_warn_for(blog)
        if shop_n >= same_day:
            out.append(f"{short}: 같은 날 숍글 {shop_n}편 (숍글은 하루 {same_day - 1}편까지 권장)")
        same_type = SAME_TYPE_DAY_WARN_BY_BLOG.get(blog, 2)
        out += [f"{short}: 같은 유형({k}) {n}편" for k, n in types.items() if n >= same_type]
        out += [f"{short}: 같은 카테고리({k}) {n}편" for k, n in cats.items() if n >= 2]
    return out


def build_board(week: str, channels: dict, categories: list, excluded: dict = None, read_text=None, aliases: dict = None) -> dict:
    """week = 월요일 날짜. 반환: {"week","sunday","blogs":[...],"longterm":{...}}"""
    excluded = excluded or {}
    mon = week_monday(week)
    sun = _sunday(mon)
    today = datetime.now().strftime("%Y-%m-%d")
    blogs = []
    for key, spec in BLOG_PLANS.items():
        rows, drafting, excluded_n = [], 0, 0
        for cid in spec["channels"]:
            if cid not in channels:
                continue
            r, d, e = _channel_articles(channels[cid], read_text, set(excluded.get(cid) or []), categories, cid, mon, sun, today, aliases)
            rows += r
            drafting += d
            excluded_n += e
        rows.sort(key=lambda r: (r["date"], r["channel"]))
        total = len(rows)
        type_rows = []
        for k in ("A", "B", "C", ""):
            n = sum(1 for r in rows if r["type_key"] == k)
            est = sum(1 for r in rows if r["type_key"] == k and r["estimated"])
            pct = round(n / total * 100) if total else 0
            target = spec["target"].get(k) if spec["target"] else None
            diff = (pct - round(target * 100)) if target is not None else None
            type_rows.append({
                "key": k, "label": TYPE_NAMES.get(k, "유형 미기재"), "count": n, "estimated": est, "pct": pct,
                "target_pct": round(target * 100) if target is not None else None, "diff": diff,
                "warn": bool(total and diff is not None and abs(diff) > TYPE_DIFF_WARN_PP),
            })
        shop_count = sum(1 for r in rows if r.get("is_shop"))
        shop_pct = round(shop_count / total * 100) if total else 0
        shop_target, shop_warn = shop_target_for(key, mon), shop_warn_for(key, mon)
        shop_over = bool(total and shop_count * 100 > total * shop_warn)  # 정확한 비율로 비교 (반올림 전)
        warnings = _warnings(rows, key)
        if shop_over:
            warnings.insert(0, f"숍글 {shop_count}/{total}편({shop_pct}%) — {shop_warn}% 초과 (목표 {shop_target}%)")
        cat_rows = [{"label": c, "count": sum(1 for r in rows if r["category_key"] == c)} for c in categories]
        cat_rows.append({"label": "미기재", "count": sum(1 for r in rows if r["category_key"] == "__none__")})
        cat_rows.append({"label": "카테고리 목록 밖", "count": sum(1 for r in rows if r["category_key"] == "__out__")})
        blogs.append({
            "key": key, "label": spec["label"], "plan": weekly_plan(key, mon), "count": total,
            "published": sum(1 for r in rows if r["status"] == "발행"), "scheduled": sum(1 for r in rows if r["status"] == "예약"),
            "drafting": drafting, "excluded": excluded_n, "types": type_rows, "cats": cat_rows,
            "rows": rows, "warnings": warnings, "has_target": spec["target"] is not None,
            "shop": {"count": shop_count, "pct": shop_pct, "target_pct": shop_target, "warn_pct": shop_warn, "over": shop_over},
        })
    lt = {"label": "롱텀", "plan": weekly_plan("longterm", mon), "count": 0, "published": 0, "scheduled": 0, "drafting": 0}
    lt_rows = []
    if LONGTERM_CHANNEL in channels:
        r, d, _ = _channel_articles(channels[LONGTERM_CHANNEL], read_text, set(), categories, LONGTERM_CHANNEL, mon, sun, today, aliases)
        lt_rows = r
        lt.update({
            "count": len(r), "published": sum(1 for x in r if x["status"] == "발행"),
            "scheduled": sum(1 for x in r if x["status"] == "예약"), "drafting": d,
        })
    # 📅 발행 시간표 (보여 주기만) — 날짜별 발행·예약 편수는 위에서 이미 모은 행을 그대로 재사용
    booked = {b["key"]: _count_by_day(b["rows"]) for b in blogs}
    booked["longterm"] = _count_by_day(lt_rows)
    review_by_day = {b["key"]: _count_by_day([r for r in b["rows"] if r.get("review")]) for b in blogs}  # 리뷰 분석 글 수 (표시용)
    schedule = []
    for day in publish_schedule(mon):
        cells = {}
        for b, slots in day["slots"].items():
            cells[b] = {"text": slot_text(slots), "total": len(slots), "booked": booked.get(b, {}).get(day["date"], 0),
                        "review": review_by_day.get(b, {}).get(day["date"], 0)}
        schedule.append({"date": day["date"], "weekday": day["weekday"], "holiday": day["holiday"], "cells": cells})
    return {
        "week": mon, "sunday": sun, "prev_week": shift_week(mon, -1), "next_week": shift_week(mon, 1),
        "blogs": blogs, "longterm": lt, "excluded_total": sum(b["excluded"] for b in blogs),
        "type_target_warn_pp": TYPE_DIFF_WARN_PP, "max_per_day": MAX_PER_DAY,
        "schedule": schedule, "schedule_note": PUBLISH_SLOT_NOTE,
    }


def _count_by_day(rows: list) -> dict:
    out = {}
    for r in rows:
        out[r["date"][:10]] = out.get(r["date"][:10], 0) + 1
    return out
