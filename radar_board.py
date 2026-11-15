"""
생활레이더 현황판 (v4_board.html 맨 아래 섹션용) — 조회만 합니다.
기존 V4 현황판 계산(v4_board.py)에는 섞이지 않도록, 생활레이더 글 데이터만 읽어 따로 계산합니다.
이 모듈은 파일을 읽지 않습니다: 호출하는 쪽(app.py)이 큐·글 데이터를 넘겨 줍니다.
"""
from datetime import datetime, timedelta

import metadata_parser

RADAR_CATEGORIES = ("요금·세금", "지원금", "신제품", "소비자 레이더", "계절 준비", "주간 레이더",
                    "혜택·멤버십", "의료비·건강보험")
RADAR_OTHER_LABEL = "미분류"
RADAR_SAME_DAY_WARN = 3          # 같은 날 이 편수 이상이면 경고
RADAR_START_DATE = None          # "YYYY-MM-DD" — 첫 생활레이더 글 발행 주의 월요일. 블로그 개설 후 입력(None이면 "처음 4주" 경고 끔)
RADAR_EARLY_WEEKS = 4            # "처음 N주" 동안
RADAR_EARLY_CORE = ("요금·세금", "지원금")
RADAR_EARLY_RANGE = (60, 70)     # 위 두 카테고리 합이 이 % 범위를 벗어나면 경고


def classify_category(text: str) -> str:
    """메모의 카테고리 제안 글자를 8개 중 하나로(띄어쓰기·가운뎃점 차이 무시). 못 알아보면 '미분류'."""
    norm = "".join(ch for ch in (text or "") if ch not in " ·ㆍ・/,")
    if not norm:
        return RADAR_OTHER_LABEL
    for cat in RADAR_CATEGORIES:
        key = "".join(ch for ch in cat if ch not in " ·ㆍ・")
        if key in norm or norm in key:
            return cat
    # 느슨한 별칭
    aliases = (("요금", "요금·세금"), ("세금", "요금·세금"), ("지원금", "지원금"), ("신제품", "신제품"),
               ("리콜", "소비자 레이더"), ("사기", "소비자 레이더"), ("소비자", "소비자 레이더"),("계절", "계절 준비"), ("주간", "주간 레이더"),
               ("멤버십", "혜택·멤버십"), ("의료", "의료비·건강보험"), ("건강보험", "의료비·건강보험"))
    for k, cat in aliases:
        if k in norm:
            return cat
    return RADAR_OTHER_LABEL


def _parse_date(s):
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def build(queue: list, articles: dict, week_monday: str, now: datetime | None = None,
          platform: str = "naver", start_date: str | None = None) -> dict:
    """한 주(월~일) 생활레이더 현황. week_monday = 그 주 월요일 "YYYY-MM-DD"."""
    now = now or datetime.now()
    start_date = start_date if start_date is not None else RADAR_START_DATE
    mon = _parse_date(week_monday) or (now - timedelta(days=now.weekday()))
    mon = mon.replace(hour=0, minute=0, second=0, microsecond=0)
    sun = mon + timedelta(days=6)
    today = now.strftime("%Y-%m-%d")

    rows, drafting, seen = [], 0, set()
    all_cat = {c: 0 for c in RADAR_CATEGORIES}
    all_cat[RADAR_OTHER_LABEL] = 0
    total_dated = 0
    for it in queue or []:
        iid = it.get("id")
        if not iid or iid in seen:
            continue
        seen.add(iid)
        art = (articles or {}).get(iid) or {}
        date_str = ((art.get("platform_published_at") or {}).get(platform) or "")[:10]
        raw = (art.get("platform_raw") or {}).get(platform) or ""
        memo = (art.get("platform_metadata") or {}).get(platform) or ""
        d = _parse_date(date_str)
        if not d:
            if raw:
                drafting += 1
            continue
        cat = classify_category(metadata_parser.parse_metadata_memo(memo).get("category", ""))
        all_cat[cat] = all_cat.get(cat, 0) + 1
        total_dated += 1
        if mon <= d <= sun:
            rows.append({
                "date": date_str, "status": "예약" if date_str > today else "발행", "category": cat,
                "title": (art.get("platform_title") or {}).get(platform) or it.get("keyword") or iid,
                "post_type": art.get("post_type") or "",
            })
    rows.sort(key=lambda r: (r["date"], r["title"]))
    published = sum(1 for r in rows if r["status"] == "발행")
    scheduled = len(rows) - published

    week_cat = {c: 0 for c in RADAR_CATEGORIES}
    week_cat[RADAR_OTHER_LABEL] = 0
    for r in rows:
        week_cat[r["category"]] += 1
    n = len(rows)

    def _table(counts, total):
        out = []
        for c in list(RADAR_CATEGORIES) + [RADAR_OTHER_LABEL]:
            if c == RADAR_OTHER_LABEL and not counts.get(c):
                continue
            out.append({"label": c, "count": counts.get(c, 0), "pct": round(100 * counts.get(c, 0) / total) if total else 0})
        return out

    warnings = []
    per_day: dict = {}
    for r in rows:
        per_day[r["date"]] = per_day.get(r["date"], 0) + 1
    for day, cnt in sorted(per_day.items()):
        if cnt >= RADAR_SAME_DAY_WARN:
            warnings.append(f"{day}에 {cnt}편이 몰려 있어요(같은 날 {RADAR_SAME_DAY_WARN}편 이상 경고)")
    early = None
    sd = _parse_date(start_date) if start_date else None
    if sd:
        weeks_since = (mon - sd).days // 7
        if 0 <= weeks_since < RADAR_EARLY_WEEKS:
            core = sum(week_cat[c] for c in RADAR_EARLY_CORE)
            pct = round(100 * core / n) if n else 0
            early = {"week_no": weeks_since + 1, "core": core, "pct": pct, "range": RADAR_EARLY_RANGE}
            if n and not (RADAR_EARLY_RANGE[0] <= pct <= RADAR_EARLY_RANGE[1]):
                warnings.append(
                    f"처음 {RADAR_EARLY_WEEKS}주({weeks_since + 1}주차)는 요금·세금 + 지원금이 "
                    f"{RADAR_EARLY_RANGE[0]}~{RADAR_EARLY_RANGE[1]}%여야 하는데 지금 {pct}%예요"
                )
    return {
        "label": "생활레이더", "week": mon.strftime("%Y-%m-%d"), "sunday": sun.strftime("%Y-%m-%d"),
        "count": n, "published": published, "scheduled": scheduled, "drafting": drafting,
        "cats": _table(week_cat, n), "cats_all": _table(all_cat, total_dated), "total_dated": total_dated,
        "rows": rows, "warnings": warnings, "early": early,
        "start_date": start_date, "same_day_warn": RADAR_SAME_DAY_WARN,
    }
