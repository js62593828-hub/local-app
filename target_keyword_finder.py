"""
타깃(성별·연령) 기반 쇼핑 키워드 발굴 — 1단계(조회·보기 전용).

데이터랩 쇼핑인사이트의 "분야별 인기검색어 순위"는 공식 API가 없고, 웹 화면의 내부 주소는
스크립트 요청에 "접근 오류" 페이지를 돌려줘서(차단) 쓰지 않습니다. 그래서 순위는 사용자가 데이터랩
화면의 TOP 목록을 복사해 붙여넣는 방식으로 받고, 그 키워드들에 아래 정보를 붙입니다.
  - 월간 검색량·경쟁도 (기존 naver_api, 묶음 호출)
  - 상위 N개만: 공식 쇼핑인사이트 API(naver_hub_api)의 성별·연령 비율 / 조회한 전체: 검색어트렌드 상승세
  - 경고 표시: 반대 성별 단어 / 키즈·아동
  - 추천 점수·"이미 씀" 판정·추천 세트: 저장된 행으로 화면을 그릴 때마다 annotate()가 계산 (아래 상수로 조정)
큐·글 데이터는 읽기만 하고, 새 캐시 파일(target_keyword_cache.json)에만 씁니다.
"""
import csv
import io
import json
import os
import re
import threading
import time
from datetime import date, timedelta

import math

import config
import naver_api
import naver_hub_api
import serp_check
import v4_board

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "target_keyword_cache.json")
CACHE_TTL_SEC = 24 * 3600
REQUEST_GAP_SEC = 1.0      # 외부 조회 사이 최소 간격
DETAIL_TOP_N = 30          # 성별·연령은 상위 이만큼만 (상승세는 조회한 전체)
MAX_COUNT = 200
DEFAULT_COUNT = 100
AGE_GROUPS = ["10", "20", "30", "40", "50", "60"]
PERIODS = {"1m": ("최근 1개월", 30), "3m": ("최근 3개월", 90)}
DEVICES = {"": "전체", "pc": "PC", "mo": "모바일"}
GENDERS = {"": "전체", "m": "남", "f": "여"}

OPPOSITE_WORDS = {
    "m": ["여성", "여자", "우먼", "레이디스"],
    "f": ["남성", "남자", "맨즈"],
}
KIDS_WORDS = ["키즈", "아동"]

# ---------- 추천 점수 기준값 (숫자는 여기서만 바꿉니다 — 화면은 열 때마다 이 값으로 다시 계산) ----------
VOL_EXCLUDE_BELOW = 100        # 월검색량(PC+모바일) 이 값 미만 = 추천 제외 ("검색량 적음")
VOL_RESERVE_BELOW = 100        # 100 미만 = 제외, 이 값 이상 = 적정 (예전의 100~299 "예비급" 구간은 없앰: target_serp_v1)
VOL_WEAK_ABOVE = 5000          # 5,000 초과~20,000 = 적정이지만 넓은 편 → 약한 감점
VOL_BROAD_ABOVE = 20000        # 이 값 초과 = "너무 넓음 — 좁힌 키워드 권장" (감점, 예비)
WEAK_BROAD_PENALTY = 8         # 5,000~20,000 약한 감점
SCORE_GOOD_BASE = 50           # 적정 구간(100~20,000) 기본점
SCORE_GOOD_LOG_MAX = 25        # 적정 구간 로그 비례 가점의 최대 (100 → 0점, 20,000 → 이 값)
SCORE_RESERVE_TIER = 30        # 예비급(100~299) 기본점
BROAD_PENALTY = 25             # 너무 넓은 키워드 감점
RISE_BONUS_PER_PCT = 0.2       # 상승세 +1%마다 가점
RISE_BONUS_MAX = 15            # 상승세 가점 상한
RISE_DROP_PCT = -20            # 상승세가 이 % 이하면 감점
RISE_DROP_PENALTY = 10
USED_SAME_BLOG_PENALTY = 60    # 같은 블로그에 이미 쓴 키워드 큰 감점 (그 블로그 칸에서는 제외)
RESERVE_TIER_CAN_FILL = False  # (예비급 구간이 없어져 지금은 쓰이지 않음) True면 예비급도 칸을 채울 수 있음

# ---------- 시기 점수 (작년 정점 — serp_check 숫자 기준, target_serp_v1b) ----------
# 경쟁(블로그 문서 수·포화도 등)은 블로그 검색 API를 쓸 수 없어 점수에 넣지 않고, 화면에서 사람이 직접 고릅니다.
PEAK_BONUS_WEEKS = (2, 6)      # 작년 정점까지 2~6주 남음 = 가점
PEAK_BONUS = 8
PEAK_PAST_WEEKS = (-8, -1)     # 정점이 이미 지남(−1~−8주) = 감점 (0~1주 남음은 0점)
PEAK_PAST_PENALTY = 6
BROAD_CAN_FILL = False         # True로 바꾸면 월검색량 2만 초과 키워드도 칸을 채울 수 있음
CATEGORY_MAX_AT_14 = 4         # 한 주 14편일 때 같은 카테고리 최대 편수 (편수가 바뀌면 같은 비율로 늘어남: 20편 → 5)
CAP_BASE_PLAN = 14
CATEGORY_MIN_CAP = 2           # 편수가 적은 블로그(싼세계 5편)도 같은 카테고리 최소 이만큼은 허용
SSANSEGAE_TYPE_TARGET_OLD = {"해결": 0.65, "결정": 0.35}   # 2026-11-02 주 이전 (v4_board.SSAN_SLOTS_FROM 전): 14편 = 해결9·결정5
SSANSEGAE_TYPE_TARGET = {"해결": 0.21, "결정": 0.79}   # 싼세계 목표 비율 (14편 = 해결3·결정11; 결정에는 리뷰 분석 글도 포함). 네이버 비율은 v4_board.NAVER_TYPE_TARGET
# 싼세계 결정 11칸 = 제품 추천 6 + 리뷰 분석 5 (2026-11-02 주부터 — v4_board.SSAN_SLOTS_FROM). 리뷰 분석 글은 네이버 검색량을 참고하지 않습니다
# 2026-11-16 주부터(v4_board.SSAN_SOLVE_FROM): 리뷰 분석은 "해결형 일반글"(쿠팡 링크 없음). 해결 8 = 일반 3 + 리뷰 분석 5, 결정(제품 추천) 6
SSANSEGAE_TYPE_TARGET_SOLVE = {"해결": 0.57, "결정": 0.43}   # 14편 = 해결 8 · 결정 6
SSAN_GENERAL_SLOTS = 3   # 11/16 주부터 해결 칸 안의 일반 해결 칸 수 (리뷰 분석은 SSAN_REVIEW_SLOTS)
REVIEW_WORDS = ("후기", "단점", "장단점", "실사용", "리뷰")   # 싼세계 결정형 키워드에 이 중 하나가 있으면 "리뷰 분석"
SSAN_PRODUCT_SLOTS = 6
SSAN_REVIEW_SLOTS = 5
REVIEW_REASON_TAIL = "리뷰 분석형(네이버 검색량 참고 안 함)"

# 블로그 정보: 같은 블로그의 채널(새 채널 + 옛 숍 채널), 칸 유형, 숍글 유형
BLOGS = {
    "naver": {"label": "N살림노트", "channels": ("naver_tips", "shop_naver"), "types": ("A", "B", "C"), "shop_type": "C"},
    "ssansegae": {"label": "W싼세계", "channels": ("wp_tips", "shop_wp"), "types": ("해결", "결정"), "shop_type": "결정"},
}
OLD_SHOP_CHANNELS = ("shop_naver", "shop_wp")
CHANNEL_FILES = {  # 채널 id → (대기열 파일, 글 파일, 플랫폼)
    "naver_tips": ("content_queue_naver_tips.json", "articles_naver_tips.json", "naver"),
    "shop_naver": ("content_queue_shop_naver.json", "articles_shop_naver.json", "naver"),
    "wp_tips": ("content_queue_wp_tips.json", "articles_wp_tips.json", "wordpress"),
    "shop_wp": ("content_queue_shop_wp.json", "articles_shop_wp.json", "wordpress"),
    "longterm_wp": ("content_queue.json", "articles.json", "wordpress"),
}
BLOG_ALIASES = {"네이버": "naver", "N살림노트": "naver", "naver": "naver", "싼세계": "ssansegae", "W싼세계": "ssansegae", "ssansegae": "ssansegae"}
TYPE_NAMES = dict(v4_board.TYPE_NAMES, **{"해결": "해결형", "결정": "결정형"})

_cache_lock = threading.Lock()
_last_call = [0.0]


# ---------- 캐시 (24시간, 새 파일) ----------

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _cache_get(key: str):
    with _cache_lock:
        entry = _load_cache().get(key)
    if entry and time.time() - entry.get("ts", 0) < CACHE_TTL_SEC:
        return entry.get("value")
    return None


def _cache_put(key: str, value) -> None:
    with _cache_lock:
        cache = _load_cache()
        now = time.time()
        cache = {k: v for k, v in cache.items() if now - v.get("ts", 0) < CACHE_TTL_SEC}
        cache[key] = {"ts": now, "value": value}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)


def _throttle() -> None:
    wait = REQUEST_GAP_SEC - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


# ---------- 붙여넣기 파싱 ----------

_RANK_PREFIX = re.compile(r"^\s*(\d{1,3})\s*(위|\.|\)|:)?\s+")
_SKIP_LINES = {"순위", "인기검색어", "검색어", "클릭량", "키워드", "상승", "하락", "new", "신규", "-", "–"}


def normalize_keyword(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _parse_tag(blog_s: str, type_s: str, cat_s: str) -> dict | None:
    """꼬리표 칸("블로그 | 유형 | 카테고리")을 읽습니다. 모두 비었으면 None. 알 수 없는 값은 빈 문자열로."""
    blog = BLOG_ALIASES.get(re.sub(r"\s+", "", blog_s or "").lower(), "")
    t = re.sub(r"\s+", "", type_s or "")
    t = re.sub(r"^(네이버|싼세계)", "", t)
    typ = ""
    if t.startswith("해결"):
        typ = "해결"
    elif t.startswith("결정"):
        typ = "결정"
    elif t[:1].upper() in ("A", "B", "C") and not re.match(r"[A-Za-z]{2}", t):
        typ = t[:1].upper()
    if typ and not blog:  # 블로그 칸이 비어 있으면 유형으로 짐작 (A·B·C = 네이버, 해결·결정 = 싼세계)
        blog = "ssansegae" if typ in BLOGS["ssansegae"]["types"] else "naver"
    if typ and blog and typ not in BLOGS[blog]["types"]:
        typ = ""  # 블로그와 안 맞는 유형(예: 싼세계 + A)은 버립니다
    cat = (cat_s or "").strip()
    if not (blog or typ or cat):
        return None
    return {"blog": blog, "type": typ, "category": cat}


def parse_pasted_keywords(text: str) -> list[str]:
    """데이터랩 TOP 목록을 복사한 글에서 키워드만 순서대로 뽑습니다 (꼬리표는 무시)."""
    return [e["keyword"] for e in parse_pasted_tagged(text)]


def parse_pasted_tagged(text: str) -> list[dict]:
    """데이터랩 TOP 목록을 복사한 글에서 키워드를 순서대로 뽑습니다. 반환: [{"keyword", "tags": [{"blog","type","category"}]}]
    "1\\n골프웨어", "1 골프웨어", "1위 골프웨어", "골프웨어" 같은 모양을 모두 받습니다.
    줄에 "|"가 있을 때만 "키워드 | 블로그 | 유형 | 카테고리" 꼬리표로 읽습니다 (뒤쪽 칸은 비어도 됨).
    순위 숫자만 있는 줄·머리글·변동 표시는 버리고, 같은 키워드가 다시 나오면 한 행으로 합치면서 꼬리표만 보탭니다."""
    entries, seen = [], {}
    rank_given = False  # 이 줄(또는 바로 앞 줄)에 순위가 따로 있었는지 — 있으면 키워드 앞 숫자는 건드리지 않음
    expected = 1  # 다음에 올 순위 번호 — 순위 숫자가 키워드에 붙어 복사된 경우("1비데")를 가려내는 기준
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = raw.strip().strip("﻿")
        if not line:
            continue
        tag = None
        if "|" in line:  # 꼬리표가 있는 줄만: "키워드 | 블로그 | 유형 | 카테고리"
            parts = [p.strip() for p in line.split("|")]
            line = parts[0]
            tag = _parse_tag(*(parts[1:4] + ["", "", ""])[:3])
            if not line:
                continue
        if line.isdigit():  # 순위 숫자만 있는 줄 → 다음 줄이 키워드이므로 그 줄 앞 숫자는 키워드의 일부
            rank_given = True
            continue
        stripped = _RANK_PREFIX.sub("", line).strip()
        rank_given = rank_given or stripped != line
        line = stripped
        # "1비데"처럼 순위가 붙어 있는 줄: 앞 숫자가 '다음 순위 번호'와 같을 때만 순위로 보고 뗍니다
        # (그래서 "3m필름" 같은 진짜 키워드는 순위 3 자리가 아니면 그대로 둡니다).
        glued = re.match(r"^(\d{1,3})(?=\D)(.+)$", line)
        if glued and not rank_given and int(glued.group(1)) == expected:
            line = glued.group(2).lstrip("위.):").strip()
        if not line or line.isdigit() or line.lower() in _SKIP_LINES or re.fullmatch(r"[▲▼△▽+\-–\d\s]+", line):
            continue
        rank_given = False
        # 탭·쉼표로 칸이 나뉜 복사본이면 첫 칸만
        line = re.split(r"[\t,]", line)[0].strip()
        norm = normalize_keyword(line)
        expected += 1
        if not norm:
            continue
        if norm not in seen:
            seen[norm] = {"keyword": line, "tags": []}
            entries.append(seen[norm])
        if tag and tag not in seen[norm]["tags"]:
            seen[norm]["tags"].append(tag)
    return entries


# ---------- 경고 / "이미 씀" 판정 ----------

def make_warnings(keyword: str, gender: str) -> list[str]:
    """저장되는 경고(성별·키즈). "이미 씀"은 저장하지 않고 화면을 그릴 때 load_used_index()로 새로 판정합니다."""
    warns = []
    if gender in OPPOSITE_WORDS:
        hit = [w for w in OPPOSITE_WORDS[gender] if w in keyword]
        if hit:
            warns.append("반대 성별 단어(" + hit[0] + ")")
    if any(w in keyword for w in KIDS_WORDS):
        warns.append("키즈/아동")
    return warns


def _read_json_file(name: str, default):
    try:
        with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


_STATE_RANK = {"used": 3, "pending": 2, "old_auto": 1}


def load_used_index() -> dict:
    """채널별 대기열·글을 읽기만 해서 {정규화키워드: {"naver"|"ssansegae"|"longterm": {"state", "channels"}}}를 만듭니다.
    state: used(실제 글이 있거나 승인·발행된 항목) / pending(새 채널의 미승인 항목) / old_auto(옛 숍 채널의 미승인 자동 항목).
    같은 블로그 = 네이버(naver_tips+shop_naver) / 싼세계(wp_tips+shop_wp). 혜택주머니(longterm_wp)는 따로 둡니다."""
    blog_of = {ch: b for b, spec in BLOGS.items() for ch in spec["channels"]}
    blog_of["longterm_wp"] = "longterm"
    index: dict = {}
    for ch, (qfile, afile, plat) in CHANNEL_FILES.items():
        queue = _read_json_file(qfile, [])
        arts = _read_json_file(afile, {})
        if not isinstance(queue, list) or not isinstance(arts, dict):
            continue
        for it in queue:
            kw = it.get("keyword") if isinstance(it, dict) else None
            if not kw:
                continue
            art = arts.get(it.get("id")) or {}
            real = bool((art.get("platform_raw") or {}).get(plat) or (art.get("platform_published_url") or {}).get(plat)
                        or (art.get("platform_published_at") or {}).get(plat))
            if it.get("approved") or it.get("published") or real:
                state = "used"
            elif ch in OLD_SHOP_CHANNELS:
                state = "old_auto"
            else:
                state = "pending"
            slot = index.setdefault(normalize_keyword(kw), {}).setdefault(blog_of[ch], {"state": state, "channels": []})
            if _STATE_RANK[state] > _STATE_RANK[slot["state"]]:
                slot["state"] = state
            if ch not in slot["channels"]:
                slot["channels"].append(ch)
    return index


# ---------- 추천 점수 (저장된 행 → 화면을 그릴 때마다 계산. 상수를 바꾸면 바로 반영) ----------

def blog_plan(blog: str, week: str = None) -> tuple[int, dict]:
    """(그 주 목표 편수, {유형: 칸 수}) — 편수는 v4_board.weekly_plan(주차별), 비율은 v4_board·이 파일 값으로 계산합니다 (가장 큰 소수부 순으로 반올림).
    week = 아무 날짜(없으면 이번 주). 유형별 칸 나누기는 🎯·🗓 모두 이 함수 하나를 씁니다."""
    plan = v4_board.weekly_plan(blog, week)
    if blog == "naver":
        ratio = v4_board.NAVER_TYPE_TARGET
    elif v4_board.ssan_solve_active(week):   # 11/16 주부터: 해결 8 · 결정 6
        ratio = SSANSEGAE_TYPE_TARGET_SOLVE
    elif v4_board.week_monday(week) >= v4_board.SSAN_SLOTS_FROM:
        ratio = SSANSEGAE_TYPE_TARGET
    else:
        ratio = SSANSEGAE_TYPE_TARGET_OLD
    raw = {t: plan * r for t, r in ratio.items()}
    slots = {t: int(math.floor(v + 1e-9)) for t, v in raw.items()}
    left = plan - sum(slots.values())
    for t in sorted(raw, key=lambda k: raw[k] - slots[k], reverse=True)[:max(0, left)]:
        slots[t] += 1
    return plan, slots


def blog_caps(blog: str, week: str = None) -> tuple[int, int]:
    """(같은 카테고리 최대 편수, 차량 관리 최대 편수) — 14편 4·1, 20편 5·2, 21편 6·2 (편수에 비례). 카테고리는 최소 CATEGORY_MIN_CAP, 차량 관리는 최소 1."""
    plan, _ = blog_plan(blog, week)
    return max(CATEGORY_MIN_CAP, CATEGORY_MAX_AT_14 * plan // CAP_BASE_PLAN), max(1, plan * v4_board.CAR_MAX_PCT // 100)


def is_review_keyword(keyword: str) -> bool:
    """키워드에 리뷰 분석 단어(REVIEW_WORDS)가 있는지. 싼세계 결정형일 때만 의미가 있습니다."""
    return any(w in (keyword or "") for w in REVIEW_WORDS)


def ssan_split_active(week: str = None) -> bool:
    """싼세계 결정 칸을 제품 추천 6 · 리뷰 분석 5로 나누는 주차인지 (2026-11-02 주부터. 그 전 주차·네이버는 옛 방식)."""
    return v4_board.week_monday(week) >= v4_board.SSAN_SLOTS_FROM


def ssan_review_type(week: str = None) -> str | None:
    """싼세계 리뷰 분석 글이 속하는 유형: 11/16 주부터 "해결", 11/2~11/9 주는 "결정", 그 전 주차는 None(리뷰 분석 구분 없음)."""
    if not ssan_split_active(week):
        return None
    return "해결" if v4_board.ssan_solve_active(week) else "결정"


def is_review_item(item: dict, blog: str, week: str = None) -> bool:
    """큐 항목이 싼세계 '리뷰 분석' 글인지 (싼세계 + 그 주의 리뷰 분석 유형 + 2026-11-02 주부터일 때만 판단).
    리뷰 분석 유형은 11/16 주부터 planned_type "해결", 그 전(11/2~11/9 주)은 "결정" (ssan_review_type).
    항목에 planned_review(true/false)가 있으면 사용자가 직접 고른 그 값을, 없으면 키워드 단어(is_review_keyword)로 판단합니다.
    네이버·그 외 유형·11/2 이전 주차는 언제나 False. 읽기만 합니다."""
    if blog != "ssansegae" or (item or {}).get("planned_type") != ssan_review_type(week):
        return False
    chosen = item.get("planned_review")
    if isinstance(chosen, bool):
        return chosen
    return is_review_keyword(item.get("keyword", ""))


def vol_sum(row: dict):
    vols = [v for v in (row.get("pc"), row.get("mobile")) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(vols) if vols else None


def vol_cell_text(v) -> str:
    """검색광고 API가 '< 10'을 5로 돌려주므로, 5는 "<10"으로 보여 줍니다."""
    if v is None:
        return "-"
    return "<10" if v == 5 else f"{v:,}"


def vol_tier(v) -> tuple[str, str]:
    if v is None:
        return "unknown", "검색량 미조회"
    if v < VOL_EXCLUDE_BELOW:
        return "low", "적음"
    if v <= VOL_WEAK_ABOVE:
        return "good", "적정"
    if v <= VOL_BROAD_ABOVE:
        return "weak", "다소 넓음"
    return "broad", "너무 넓음 — 좁힌 키워드 권장"


def serp_view(serp: dict | None, vol=None) -> dict:
    """작년 정점 숫자(serp_check) → 화면·점수용 값. 없는 칸은 None. 저장하지 않고 매번 계산합니다.
    반환: peak_date, weeks_left, peak_text(예 "11/24주 · 5주 전"), adj(점수 가감), reason(짧은 근거 문구)"""
    serp = serp or {}
    peak_date = serp.get("peak_date")
    wl = serp_check.peak_weeks_left(peak_date)
    adj, reason, peak_text = 0.0, "", "-"
    if wl is not None:
        lab = serp_check.peak_label(peak_date)
        if wl > 0:
            peak_text, reason = f"{lab} · {wl}주 전", f"정점 {wl}주 전"
        elif wl == 0:
            peak_text, reason = f"{lab} · 이번 주", "정점 이번 주"
        else:
            peak_text, reason = f"{lab} · {-wl}주 지남", f"정점 {-wl}주 지남"
        if PEAK_BONUS_WEEKS[0] <= wl <= PEAK_BONUS_WEEKS[1]:
            adj += PEAK_BONUS
        elif PEAK_PAST_WEEKS[0] <= wl <= PEAK_PAST_WEEKS[1]:
            adj -= PEAK_PAST_PENALTY
    return {"peak_date": peak_date, "weeks_left": wl, "peak_text": peak_text, "adj": adj, "reason": reason}


def base_score(v, tier: str, rising) -> float | None:
    if tier == "low":
        return None
    if tier in ("good", "weak"):
        s = SCORE_GOOD_BASE + SCORE_GOOD_LOG_MAX * math.log(v / VOL_RESERVE_BELOW) / math.log(VOL_BROAD_ABOVE / VOL_RESERVE_BELOW)
        if tier == "weak":
            s -= WEAK_BROAD_PENALTY
    elif tier == "broad":
        s = SCORE_GOOD_BASE + SCORE_GOOD_LOG_MAX - BROAD_PENALTY
    else:  # reserve / unknown
        s = SCORE_RESERVE_TIER
    if isinstance(rising, (int, float)):
        if rising > 0:
            s += min(RISE_BONUS_MAX, rising * RISE_BONUS_PER_PCT)
        elif rising <= RISE_DROP_PCT:
            s -= RISE_DROP_PENALTY
    return s


def _used_text(blog: str, st: dict | None) -> tuple[str, str]:
    """(표시 글, 종류 used/gray/info). 칸의 블로그가 아니면 안내만."""
    label = {"naver": "N살림노트", "ssansegae": "W싼세계", "longterm": "W혜택주머니"}.get(blog, blog)
    if not st:
        return "", ""
    if st["state"] == "used":
        return label, "used"
    if st["state"] == "old_auto":
        return f"{label} 옛 자동 후보", "gray"
    return f"{label} 미승인 후보", "gray"


def annotate(rows: list[dict], *, used_index: dict | None = None, week_info: dict | None = None,
             categories=(), aliases: dict | None = None, week: str | None = None, serp_lookup=None) -> dict:
    """저장된 행(rows)으로 추천 점수·상태·이유를 계산합니다 (저장하지 않음, 원본 행은 건드리지 않음).
    week_info = {블로그: {"approved", "types": {유형: 수}, "cats": {카테고리: 수}}} — 이미 승인된 항목은 칸에서 먼저 뺍니다.
    반환: {"rows": [... 점수순 정렬 ...], "config": {블로그: 칸·상한 정보}}"""
    used_index = used_index or {}
    week_info = week_info or {}
    aliases = aliases or {}
    split_on = ssan_split_active(week)  # 싼세계 결정 칸 = 제품 추천 6 · 리뷰 분석 5 (2026-11-02 주부터)
    rev_type = ssan_review_type(week)   # 리뷰 분석이 들어가는 유형: 11/16 주부터 "해결"(일반 3 + 리뷰 분석 5), 그 전은 "결정"
    solve_on = v4_board.ssan_solve_active(week)
    wk = v4_board.week_monday(week)   # 숍글 기준값 계산용 (week가 없으면 이번 주)
    general_label = "일반 해결" if solve_on else "제품 추천"
    out = []
    for order, r in enumerate(rows, start=1):
        a = dict(r)
        a["plan_order"] = order
        v = vol_sum(r)
        tier, tier_label = vol_tier(v)
        rising = r.get("rising_pct")
        norm = normalize_keyword(r.get("keyword", ""))
        used = used_index.get(norm, {})
        # 꼬리표 → 블로그별 배정 (블로그가 있는 꼬리표만 칸 후보)
        assigns = {}
        for t in r.get("tags") or []:
            if t.get("blog") in BLOGS:
                cat = aliases.get(t.get("category") or "", t.get("category") or "")
                a_ = assigns.setdefault(t["blog"], {"blog": t["blog"], "type": "", "category": ""})
                a_["type"] = a_["type"] or t.get("type", "")
                a_["category"] = a_["category"] or (cat if cat in categories else "")
        # 11/16 주부터: 싼세계에서 리뷰 분석 단어가 있는 키워드는 결정이 아니라 해결 칸으로 (유형 칸이 비어 있으면 그대로 미정)
        if solve_on and "ssansegae" in assigns and is_review_keyword(r.get("keyword", "")) and assigns["ssansegae"]["type"] in ("결정", "해결"):
            assigns["ssansegae"]["type"] = "해결"
        cand_blogs = list(assigns) or list(BLOGS)
        all_used = all((used.get(b) or {}).get("state") == "used" for b in cand_blogs)
        score = base_score(v, tier, rising)
        # 리뷰 분석형(싼세계 결정 + 후기·단점…): 네이버 검색량이 적거나 없어도 칸 후보 — 점수는 검색량 100 기본점(상승세 가점은 그대로)
        review_ssan = bool(split_on and (assigns.get("ssansegae") or {}).get("type") == rev_type and is_review_keyword(r.get("keyword", "")))
        if review_ssan and tier in ("low", "unknown"):
            score = base_score(VOL_RESERVE_BELOW, "good", rising)
        # 경쟁·시기 숫자: 행에 저장된 값이 있으면 그것을, 없으면(옛 이력) serp_lookup(캐시만 읽음)에서 가져옵니다
        sv = serp_view(r.get("serp") if isinstance(r.get("serp"), dict) else (serp_lookup(r.get("keyword", "")) if serp_lookup else None), v)
        if score is not None:
            score += sv["adj"]
        if score is not None and all_used:
            score -= USED_SAME_BLOG_PENALTY
        # "이미 씀" 안내 칩
        chips = []
        for b in ("naver", "ssansegae", "longterm"):
            text, kind = _used_text(b, used.get(b))
            if not text:
                continue
            if kind == "used" and b in BLOGS and (b in assigns or all_used):  # 꼬리표 없는 줄은 두 블로그 다 썼을 때만 "이미 씀"
                chips.append({"text": f"{text}에 이미 씀", "kind": "used"})
            elif kind == "used":
                chips.append({"text": f"{text}에 있음", "kind": "info"})
            else:
                chips.append({"text": text, "kind": "gray"})
        # 재료 문구
        vol_text = f"검색량 {v:,}({tier_label})" if v is not None else "검색량 미조회"
        rise_text = ""
        if isinstance(rising, (int, float)):
            rise_text = f"상승 {rising:+.0f}%" if rising > 0 else (f"하락 {rising:.0f}%" if rising <= RISE_DROP_PCT else "")
        a.update(
            vol=v, pc_text=vol_cell_text(r.get("pc")), mobile_text=vol_cell_text(r.get("mobile")),
            tier=tier, tier_label=tier_label, score=round(score, 1) if score is not None else None,
            used_chips=chips, vol_text=vol_text, rise_text=rise_text, sv=sv, serp_text=sv["reason"],
            assign=list(assigns.values()), assign_blogs=list(assigns),
            warnings=[w for w in (r.get("warnings") or []) if w != "이미 씀"],  # 옛 이력에 저장된 "이미 씀"은 버리고 새 판정으로
            status="candidate", reason="", recs={},
        )
        a["review_ssan"] = review_ssan
        a["_used"] = used
        a["_cand_blogs"] = cand_blogs
        out.append(a)

    # ---- 칸 채우기 (블로그별, 점수 높은 순) ----
    config = {}
    naver_c_rec = set()  # 이번 계산에서 네이버 C(결정형)로 추천된 키워드 — 싼세계 결정형과 겹치면 예비
    for blog, spec in BLOGS.items():
        plan, targets = blog_plan(blog, week)
        cap_cat, cap_car = blog_caps(blog, week)
        wi = week_info.get(blog) or {}
        appr_types = wi.get("types") or {}
        appr_total = int(wi.get("approved") or 0)
        cat_counts = dict(wi.get("cats") or {})
        remaining = {t: max(0, targets[t] - int(appr_types.get(t, 0))) for t in targets}
        total_left = max(0, plan - appr_total)
        shop_t = spec["shop_type"]
        shop_n = int(appr_types.get(shop_t, 0))
        split_blog = blog == "ssansegae" and split_on
        general_slots = SSAN_GENERAL_SLOTS if solve_on else SSAN_PRODUCT_SLOTS  # 칸 키 이름은 product_* 그대로 (11/16 주부터는 "일반 해결" 칸 수)
        kind_left = {}  # 싼세계 결정 칸을 나눠 채움: 이미 승인된 결정 항목도 같은 판정(REVIEW_WORDS)으로 각 칸에서 먼저 뺍니다
        kind_rec = {"product": 0, "review": 0}
        if split_blog:
            appr_rev = int(wi.get("review") or 0)
            appr_prod = max(0, int(appr_types.get(rev_type, 0)) - appr_rev)
            kind_left = {"product": max(0, general_slots - appr_prod), "review": max(0, SSAN_REVIEW_SLOTS - appr_rev)}
        chosen = 0
        rec_idx = {t: 0 for t in targets}
        config[blog] = {
            "label": spec["label"], "plan": plan, "types": list(targets), "targets": targets,
            "approved": appr_total, "approved_types": {t: int(appr_types.get(t, 0)) for t in targets},
            "approved_undecided": int(wi.get("undecided") or 0), "approved_cats": dict(wi.get("cats") or {}),
            "cap_cat": cap_cat, "cap_car": cap_car, "car_category": v4_board.CAR_CATEGORY,
            "shop_type": shop_t, "shop_target": v4_board.shop_target_for(blog, wk), "shop_warn": v4_board.shop_warn_for(blog, wk),
            "type_names": {t: TYPE_NAMES[t] for t in targets},
        }
        if split_blog:
            config[blog]["split"] = {
                "type": rev_type, "product_slots": general_slots, "review_slots": SSAN_REVIEW_SLOTS,
                "approved_product": max(0, int(appr_types.get(rev_type, 0)) - appr_rev), "approved_review": appr_rev,
                "review_words": list(REVIEW_WORDS), "general_label": general_label,
            }
        pool = [a for a in out if blog in a["assign_blogs"]]
        pool.sort(key=lambda a: (-(a["score"] if a["score"] is not None else -9999), a["plan_order"]))
        for a in pool:
            asg = next(x for x in a["assign"] if x["blog"] == blog)
            t, cat, label = asg["type"], asg["category"], spec["label"]
            res = {"status": "", "reason": "", "type": t, "category": cat}
            is_rev = blog == "ssansegae" and a["review_ssan"]
            if a["tier"] == "low" and not is_rev:
                res.update(status="excluded", reason=f"{a['vol_text']}")
            elif (a["_used"].get(blog) or {}).get("state") == "used":
                res.update(status="excluded", reason=f"{label}에 이미 씀")
            elif t not in targets:
                res.update(status="candidate", reason="유형 미정 — 칸 배정 안 함")
            elif a["tier"] == "unknown" and not is_rev:
                res.update(status="reserve", reason="검색량 미조회 → 예비")
            elif a["tier"] == "reserve" and not RESERVE_TIER_CAN_FILL:
                res.update(status="reserve", reason=f"{a['vol_text']} → 예비")
            elif a["tier"] == "broad" and not BROAD_CAN_FILL:
                res.update(status="reserve", reason=f"{a['vol_text']} → 예비(좁힌 키워드로 바꾸세요)")
            elif blog == "ssansegae" and t == "결정" and not (is_rev and solve_on) and normalize_keyword(a.get("keyword", "")) in naver_c_rec:
                res.update(status="reserve", reason="네이버 숍글과 같은 제품 → 예비(네이버 먼저 발행, 다음 주 이후 다른 각도로)")
            elif split_blog and t == rev_type and kind_left["review" if is_rev else "product"] <= 0:
                res.update(status="reserve", reason=(f"리뷰 분석 {SSAN_REVIEW_SLOTS}칸 다 참 → 예비" if is_rev else f"{general_label} {general_slots}칸 다 참 → 예비"))
            elif remaining[t] <= 0:
                res.update(status="reserve", reason=f"{t} 칸 다 참 → 예비")
            elif chosen >= total_left:
                res.update(status="reserve", reason=f"{label} 주간 목표 {plan}편 다 참 → 예비")
            elif cat == v4_board.CAR_CATEGORY and cat_counts.get(cat, 0) >= cap_car:
                res.update(status="reserve", reason=f"{cat} {cap_car}편 다 참 → 예비")
            elif cat and cat_counts.get(cat, 0) >= cap_cat:
                res.update(status="reserve", reason=f"{cat} {cap_cat}편 다 참 → 예비")
            elif t == shop_t and (shop_n + 1) * 100 > v4_board.shop_warn_for(blog, wk) * plan:
                res.update(status="reserve", reason=f"숍글 {v4_board.shop_warn_for(blog, wk)}% 초과 → 예비")
            else:
                rec_idx[t] += 1
                chosen += 1
                remaining[t] -= 1
                if cat:
                    cat_counts[cat] = cat_counts.get(cat, 0) + 1
                if t == shop_t:
                    shop_n += 1
                if blog == "naver" and t == "C":
                    naver_c_rec.add(normalize_keyword(a.get("keyword", "")))
                if split_blog and t == rev_type:
                    kind = "review" if is_rev else "product"
                    kind_left[kind] -= 1
                    kind_rec[kind] += 1
                nth = int(appr_types.get(t, 0)) + rec_idx[t]
                res.update(status="rec", reason=f"{TYPE_NAMES[t]} {targets[t]}칸 중 {nth}번째")
            if is_rev and res["status"] in ("rec", "reserve"):
                res["reason"] += " · " + REVIEW_REASON_TAIL
            asg.update(res)
        config[blog]["recommended"] = chosen
        if split_blog:
            config[blog]["split"]["recommended_product"] = kind_rec["product"]
            config[blog]["split"]["recommended_review"] = kind_rec["review"]

    # ---- 행 단위 정리: 상태·이유·정렬 ----
    for a in out:
        asg = a["assign"]
        if a["tier"] == "low" and not a["review_ssan"]:
            a["status"], a["reason"] = "excluded", a["vol_text"]
        elif not asg:
            if all((a["_used"].get(b) or {}).get("state") == "used" for b in BLOGS):
                a["status"], a["reason"] = "excluded", "네이버·싼세계에 이미 씀"
            else:
                a["status"] = "candidate"
                a["reason"] = " · ".join(p for p in (a["vol_text"], a["rise_text"], a["serp_text"], "유형 미정 — 칸 배정 안 함") if p)
        else:
            stats = [x["status"] for x in asg]
            a["status"] = ("rec" if "rec" in stats else "reserve" if "reserve" in stats
                           else "candidate" if "candidate" in stats else "excluded")
            multi = len(asg) > 1
            tails = [(f"{BLOGS[x['blog']]['label']} " if multi and not x["reason"].startswith(BLOGS[x['blog']]['label']) else "") + x["reason"]
                     for x in asg]
            head = [a["vol_text"], a["rise_text"], a["serp_text"]] if a["status"] in ("rec", "reserve", "candidate") else []
            a["reason"] = " · ".join(p for p in head + [" / ".join(tails)] if p)
            a["recs"] = {x["blog"]: x for x in asg if x["status"] == "rec"}
        a["type_label"] = " · ".join(
            f"{BLOGS[x['blog']]['label']} {x['type'] or '미정'}" for x in asg) if asg else "미정"
        del a["_used"], a["_cand_blogs"]
    group = {"rec": 0, "reserve": 0, "candidate": 0, "excluded": 1}
    ranked = sorted(out, key=lambda a: (group[a["status"]], -(a["score"] if a["score"] is not None else -9999), a["plan_order"]))
    for i, a in enumerate(ranked, start=1):
        a["srank"] = i
    return {"rows": ranked, "config": config}


def approval_meta(annotated_row: dict, blog: str) -> dict:
    """승인할 때 대기열 항목에 새로 붙이는 값(planned_category, rec_score, rec_reason). 계산은 서버 쪽에서만."""
    asg = next((x for x in annotated_row.get("assign", []) if x["blog"] == blog), None)
    meta = {
        "planned_category": (asg or {}).get("category", ""),
        "rec_score": annotated_row.get("score"),
        "rec_reason": annotated_row.get("reason", ""),
    }
    return meta


# ---------- 쏠림 지수 ----------

def skew_index(target_rank: int, full_rank: int | None, full_size: int) -> tuple[float, bool]:
    """쏠림 지수 = 전체 순위 ÷ 타깃 순위. 1보다 크면 타깃에서 더 인기.
    전체 순위표에 없으면 '전체 순위표 꼴찌 바로 아래(개수+1위)'로 보고 계산하며, 그 경우 True(= 최소치)를 같이 돌려줍니다."""
    outside = full_rank is None
    fr = full_rank if full_rank is not None else full_size + 1
    return round(fr / target_rank, 2), outside


# ---------- 외부 조회 (캐시 + 간격) ----------

def _period_dates(period: str) -> tuple[str, str]:
    days = PERIODS.get(period, PERIODS["3m"])[1]
    end = date.today() - timedelta(days=1)
    return (end - timedelta(days=days)).isoformat(), end.isoformat()


def fetch_volumes(keywords: list[str]) -> tuple[dict, str | None]:
    """월간 검색량·경쟁도. 5개씩 묶어 한 번에 조회(expand=False). 반환: ({정규화키워드: 정보}, 오류메시지)."""
    result, error = {}, None
    wanted = {normalize_keyword(k) for k in keywords}
    todo = []
    for k in keywords:
        cached = _cache_get("vol|" + normalize_keyword(k))
        if cached is not None:
            result[normalize_keyword(k)] = cached
        else:
            todo.append(k)
    for i in range(0, len(todo), 5):
        chunk = todo[i:i + 5]
        try:
            _throttle()
            rows = naver_api.get_keyword_stats(chunk, expand=False)
        except Exception as e:  # noqa: BLE001 — 한 묶음 실패가 전체를 막지 않게
            error = f"검색량 조회 실패: {type(e).__name__}: {e}"[:200]
            continue
        for r in rows:
            norm = normalize_keyword(r.get("keyword"))
            if norm in wanted and norm not in result:
                info = {"pc": r["pc_count"], "mobile": r["mobile_count"], "competition": r["competition"]}
                result[norm] = info
                _cache_put("vol|" + norm, info)
    return result, error


def fetch_detail(cid: str, keyword: str, period: str, device: str) -> dict:
    """키워드 하나의 성별·연령 비율(캐시). 반환: {"male_pct", "age_pct", "mock", "error"}"""
    start, end = _period_dates(period)
    key = f"det|{cid}|{normalize_keyword(keyword)}|{period}|{device}|{end}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    out = {"male_pct": None, "age_pct": {}, "mock": False, "error": ""}
    try:
        _throttle()
        g = naver_hub_api.get_keyword_gender_ratio(cid, keyword, start, end, device)
        _throttle()
        a = naver_hub_api.get_keyword_age_ratio(cid, keyword, start, end, device)
        out.update(male_pct=g["male_pct"], age_pct=a["age_pct"], mock=g["mock"] or a["mock"])
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:150]
        return out  # 실패는 캐시하지 않음
    if not out["mock"]:  # 목(가짜) 값은 저장하지 않음 — 키를 넣은 뒤 가짜 값이 남지 않게
        _cache_put(key, out)
    return out


def fetch_rising(keywords: list[str]) -> dict:
    """최근 4주 vs 직전 4주 검색 관심도 변화율(%). 진행 중인 주는 뺍니다. 반환: {정규화키워드: % 또는 None}"""
    out = {}
    today = date.today()
    start = (today - timedelta(weeks=10)).isoformat()
    end = today.isoformat()
    todo = []
    for k in keywords:
        cached = _cache_get(f"rise|{normalize_keyword(k)}|{end}")
        if cached is not None:
            out[normalize_keyword(k)] = cached.get("v")
        else:
            todo.append(k)
    for i in range(0, len(todo), 5):
        chunk = todo[i:i + 5]
        try:
            _throttle()
            groups = naver_hub_api.get_search_trend(
                [{"groupName": k, "keywords": [k]} for k in chunk], start, end, "week")
        except Exception:  # noqa: BLE001
            continue
        for g in groups:
            weeks = [d["ratio"] for d in g["data"]][:-1]  # 마지막(진행 중인 주) 제외
            if len(weeks) < 8:
                continue
            recent, prev = sum(weeks[-4:]), sum(weeks[-8:-4])
            val = round((recent / prev - 1) * 100, 1) if prev else None
            norm = normalize_keyword(g["group_name"])
            out[norm] = val
            _cache_put(f"rise|{norm}|{end}", {"v": val})
    return out


# ---------- 전체 조립 ----------

def build_rows(*, target_text: str, full_text: str, cid: str, gender: str, ages: list[str],
               period: str, device: str, count: int, with_external: bool = True) -> dict:
    """붙여넣은 두 순위(타깃/전체)로 표 한 줄씩 만들어 돌려줍니다.
    반환: {"rows": [...], "notes": [화면에 보여줄 안내·실패 이유], "target_total", "full_total"}"""
    count = max(1, min(int(count or DEFAULT_COUNT), MAX_COUNT))
    notes = []
    tagged = parse_pasted_tagged(target_text)[:count]
    target = [e["keyword"] for e in tagged]
    tags_of = {normalize_keyword(e["keyword"]): e["tags"] for e in tagged}
    full = parse_pasted_keywords(full_text)
    if not target:
        return {"rows": [], "notes": ["타깃 순위 칸에서 키워드를 하나도 읽지 못했습니다."], "target_total": 0, "full_total": len(full)}
    if not full:
        notes.append("전체 순위 칸이 비어 있어 쏠림 지수는 계산하지 않았습니다.")
    full_rank = {normalize_keyword(k): i + 1 for i, k in enumerate(full)}

    vols, err = ({}, None)
    rising, details, serp_data = {}, {}, {}
    if with_external:
        vols, err = fetch_volumes(target)
        if err:
            notes.append(err)
        elif not vols:
            notes.append("월간 검색량·경쟁도를 가져오지 못했습니다 (검색광고 API 키가 없는 목 모드이거나 일치하는 키워드가 없음).")
        top = target[:DETAIL_TOP_N]
        if cid:
            if not config.NAVER_HUB_CONFIGURED:
                notes.append("NAVER API HUB 키가 없어 남성 비율·연령 비율은 목(가짜) 값입니다.")
            for k in top:
                details[normalize_keyword(k)] = fetch_detail(cid, k, period, device)
            fails = [d["error"] for d in details.values() if d["error"]]
            if fails:
                notes.append(f"성별·연령 조회 실패 {len(fails)}건 (예: {fails[0]})")
        else:
            notes.append("분야 코드(cid)가 없어 남성 비율·연령 비율은 조회하지 않았습니다.")
        rising = fetch_rising(target)  # 상승세는 조회한 키워드 전체 (성별·연령만 상위 DETAIL_TOP_N개)
        serp_data, serp_notes = serp_check.fetch(target)  # 작년 정점 (실패한 칸은 None)
        if serp_check.configured():  # 키 없음 안내는 화면 위 노란 안내가 대신합니다
            notes.extend(serp_notes)

    rows = []
    for i, kw in enumerate(target, start=1):
        norm = normalize_keyword(kw)
        fr = full_rank.get(norm)
        skew, outside = (skew_index(i, fr, len(full)) if full else (None, False))
        v = vols.get(norm, {})
        d = details.get(norm, {})
        age_pct = d.get("age_pct") or {}
        sel_age = round(sum(age_pct.get(a, 0) for a in ages), 1) if age_pct and ages else None
        rows.append({
            "target_rank": i,
            "keyword": kw,
            "full_rank": fr,
            "full_outside": fr is None and bool(full),
            "skew": skew,
            "skew_is_min": outside,
            "pc": v.get("pc"),
            "mobile": v.get("mobile"),
            "competition": v.get("competition", ""),
            "male_pct": d.get("male_pct"),
            "age_sel_pct": sel_age,
            "rising_pct": rising.get(norm),
            "detail_mock": bool(d.get("mock")),
            "warnings": make_warnings(kw, gender),
            "tags": tags_of.get(norm, []),
            "serp": {"peak_date": (serp_data.get(norm) or {}).get("peak_date")},
        })
    return {"rows": rows, "notes": notes, "target_total": len(target), "full_total": len(full)}


CSV_COLUMNS = [
    ("target_rank", "타깃순위"), ("keyword", "키워드"), ("full_rank", "전체순위"), ("skew", "쏠림지수"),
    ("pc", "월검색량PC"), ("mobile", "월검색량모바일"), ("competition", "광고경쟁도"),
    ("male_pct", "남성비율%"), ("age_sel_pct", "선택연령비율%"), ("rising_pct", "상승세%"), ("warnings", "경고"),
    ("serp_peak", "작년정점"),
]


def rows_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in CSV_COLUMNS])
    for r in rows:
        line = []
        sv = serp_view(r.get("serp"), vol_sum(r))
        serp_cells = {"serp_peak": None if sv["peak_text"] == "-" else sv["peak_text"]}
        for key, _ in CSV_COLUMNS:
            val = serp_cells[key] if key in serp_cells else r.get(key)
            if key == "warnings":
                val = " / ".join(val or [])
            elif key == "full_rank" and val is None and r.get("full_outside"):
                val = "밖"
            line.append("" if val is None else val)
        w.writerow(line)
    return buf.getvalue()


# ---------- 서칭 이력 (새 파일 target_keyword_history.json) ----------

HISTORY_FILE = os.path.join(BASE_DIR, "target_keyword_history.json")
HISTORY_KEEP = 50  # 별표(★) 없는 이력은 최근 이만큼만 남김


def _read_history() -> list[dict]:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _write_history(items: list[dict]) -> None:
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, HISTORY_FILE)


def history_add(form: dict, label: str, result: dict) -> str:
    """조회 한 번을 이력에 남기고 id를 돌려줍니다. 별표 없는 오래된 이력은 HISTORY_KEEP개를 넘으면 지웁니다."""
    import uuid
    from datetime import datetime
    with _cache_lock:
        items = _read_history()
        hid = uuid.uuid4().hex[:10]
        items.insert(0, {
            "id": hid, "ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "label": label,
            "form": form, "result": result, "starred": False, "picked": [],
        })
        keep, plain = [], 0
        for it in items:
            if it.get("starred"):
                keep.append(it)
            else:
                plain += 1
                if plain <= HISTORY_KEEP:
                    keep.append(it)
        _write_history(keep)
    return hid


def history_list() -> list[dict]:
    """목록 화면용 요약(결과 본문 제외)."""
    out = []
    for it in _read_history():
        out.append({
            "id": it["id"], "ts": it["ts"], "label": it.get("label", ""), "starred": it.get("starred", False),
            "count": len(it.get("result", {}).get("rows", [])), "picked": len(it.get("picked", [])),
            "first": [r["keyword"] for r in it.get("result", {}).get("rows", [])[:3]],
        })
    return out


def history_get(hid: str) -> dict | None:
    return next((it for it in _read_history() if it["id"] == hid), None)


def history_update(hid: str, **changes) -> bool:
    with _cache_lock:
        items = _read_history()
        for it in items:
            if it["id"] == hid:
                it.update(changes)
                _write_history(items)
                return True
    return False


def history_delete(hid: str) -> None:
    with _cache_lock:
        _write_history([it for it in _read_history() if it["id"] != hid])


def carry_draft(items: list[dict], hid: str, week: str, approved_kws: dict | None = None) -> dict | None:
    """새 조회(hid)에 draft가 없을 때, 같은 주(week)의 가장 최근 이전 이력 draft에서 겹치는 키워드의 체크를 이어받을 값을 계산합니다 (저장하지 않음).
    items = 이력 전체(최신이 앞). approved_kws = {블로그: [그 주 이미 승인된 키워드]} — 이미 승인된 블로그 체크는 가져오지 않습니다.
    반환: {"draft": {"week","rows","ts","carried"}, "carried": {"id","ts","n","m","k"}} 또는 None (이어받을 이전 이력이 없거나 이어받을 체크가 없음).
    n = 이전 조회의 승인 안 한 체크 키워드 수, m = 그중 이번 목록에도 있어 이어받은 수, k = 이번 목록에 없는 수."""
    from datetime import datetime
    ids = [it.get("id") for it in items]
    if hid not in ids:
        return None
    cur = items[ids.index(hid)]
    if cur.get("draft"):
        return None
    cur_kws = {r.get("keyword") for r in ((cur.get("result") or {}).get("rows") or [])}
    approved = {b: set(v or ()) for b, v in (approved_kws or {}).items()}
    prev = next((it for it in items[ids.index(hid) + 1:]
                 if isinstance(it.get("draft"), dict) and it["draft"].get("week") == week), None)
    if not prev:
        return None
    rows, n, m = {}, 0, 0
    for kw, v in ((prev["draft"].get("rows")) or {}).items():
        if not isinstance(v, dict) or not v.get("blogs"):
            continue
        blogs = {b: s for b, s in v["blogs"].items() if kw not in approved.get(b, set())}
        if not blogs:
            continue  # 그 주에 이미 승인된 키워드는 이어받지 않고 세지도 않습니다
        n += 1
        if kw in cur_kws:
            m += 1
            rows[kw] = {"blogs": blogs, "compete": v.get("compete", ""), "note": v.get("note", "")}
    if not n:
        return None
    info = {"id": prev["id"], "ts": prev.get("ts", ""), "n": n, "m": m, "k": n - m}
    draft = {"week": week, "rows": rows, "ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "carried": info}
    return {"draft": draft, "carried": info}
