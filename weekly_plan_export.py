"""
주간 기획 자료 만들기 — 살림 기획 자료(salim_plan_input.md)용 계산·글 만들기. (app을 import하지 않습니다)

흐름: 시드(shopping_calendar.json) → 검색광고 연관 키워드(시드당 1회, shop_children와 같은 호출) → 문제·관리 단어 필터
      → 카테고리별 검색량 상위 선정 → 상승세(데이터랩, 상위 30개) → 기존 글과 겹침(blog_overlap) → 마크다운.
이 파일은 어떤 json도 쓰지 않습니다. 대기열 준비·파일 쓰기·글 목록 읽기는 app.py(_build_weekly_plan)가 맡습니다.

고칠 때는 아래 "기준값"만 보면 됩니다.
"""
import contextlib
import math
import re
import time
from datetime import datetime, timedelta

import requests

import blog_overlap
import main as engine
import shop_children
import signal_enricher as se
import v4_board

# ---------- 기준값 (나중에 여기만 고치면 됩니다) ----------
# 일반글 후보로 남길 "문제·관리 단어" — 시드 글자를 뺀 나머지 부분에 이 중 하나가 있어야 합니다.
PROBLEM_WORDS_A = [  # A 해결형
    "원인", "방법", "없애는", "제거", "냄새", "막힘", "안될때", "안됨", "고장", "해결", "얼룩", "곰팡이",
    "녹", "물때", "벌레", "소음", "누수", "결로", "세탁법", "청소법", "손질법", "보관법",
]
PROBLEM_WORDS_B = [  # B 점검·시즌형
    "관리", "주기", "교체", "점검", "시기", "준비", "정리", "체크리스트", "겨울철", "여름철", "환절기", "장마철",
]
# A·B 단어가 같이 들어 있을 때, A 쪽이 이 "그냥 방법" 단어뿐이면 B(점검·시즌형)로 봅니다 ("관리방법" → B)
GENERIC_HOWTO_WORDS = {"방법", "세탁법", "청소법", "손질법", "보관법"}

# 문제 단어처럼 보이지만 실제로는 다른 뜻·상품 특징으로 쓰이는 말 — 문제 단어 판정 전에 이 말들은 지우고 봅니다
# (예: 녹색커튼의 "녹", 물에녹는물티슈의 "녹", 무소음가습기·저소음가습기의 "소음", 냄새안나는음식물처리기의 "냄새")
NOT_A_PROBLEM_PATTERNS = ["녹색", "녹는", "녹차", "녹음", "녹아", "녹말", "무소음", "저소음", "안끼는", "안생기는", "안나는", "냄새좋은", "교체형", "교체용"]

# 빼는 말: 숍 세부 키워드와 같은 제외 단어(청소업체·청소대행·입주청소·이사청소·청소알바·청소업 등). 단, 위 문제 단어와 겹치는
# "고장"·"교체"는 일반글에서는 쓰는 단어라 빼지 않습니다.
GENERAL_EXCLUDE_WORDS = [w for w in shop_children.EXCLUDE_WORDS if not any(w in p or p in w for p in PROBLEM_WORDS_A + PROBLEM_WORDS_B)]
# 구매 단어 (이건 숍(결정형) 영역이라 일반글 후보에서 뺍니다)
PURCHASE_WORDS = [
    "가격", "최저가", "추천순위", "순위", "top", "쿠팡", "할인", "후기", "리뷰", "구매", "구입", "쿠폰", "특가", "세일",
    "추천", "비교", "가성비", "인기", "베스트", "브랜드", "사는곳",
]
# 반대 계절: 기존 숍 규칙(shop_children._opposite_season_word) + 장마(5~8월 밖이면 뺌)
RAINY_WORDS = ["장마"]
RAINY_MONTHS = (5, 6, 7, 8)

# 시드 글자 포함 규칙: "exact" = 키워드에 시드가 통째로 들어 있어야 함(기본). "stem" = 시드의 끝 한 글자를 뺀 앞부분
# (곰팡이제거제 → 곰팡이제거, 제습제 → 제습)만 들어 있어도 인정 — 후보가 적은 카테고리를 늘리는 완화 옵션(기본은 꺼 둠)
SEED_MATCH_MODE = "exact"

# 문제·상황 시드: 제품 이름이 아니라 "사람들이 겪는 문제"에서 출발하는 시드 (카테고리 → 시드 목록). 여기만 고치면 됩니다.
PROBLEM_SEEDS = {
    "습기·곰팡이·해충": ["결로", "곰팡이", "벽지곰팡이", "실리콘곰팡이", "옷장냄새", "초파리", "날파리", "바퀴벌레", "실내습도"],
    "세탁·의류관리": ["수건냄새", "빨래냄새", "니트보풀", "니트세탁", "흰옷누렇게", "세탁조곰팡이", "패딩세탁", "옷얼룩"],
    "욕실·청소": ["배수구냄새", "하수구냄새", "변기막힘", "세면대막힘", "욕실물때", "타일줄눈", "화장실냄새", "거울물때"],
    "주방 살림": ["싱크대냄새", "싱크대막힘", "프라이팬코팅", "냄비탄자국", "도마곰팡이", "냉장고냄새", "전자레인지청소", "김치냄새"],
    "수납·정리": ["옷장정리", "겨울옷정리", "이불보관", "냉장고정리", "신발장냄새", "계절옷정리"],
    "침구·패브릭": ["이불세탁", "베개세탁", "매트리스청소", "집먼지진드기", "커튼세탁", "러그세탁",
             "겨울이불세탁", "이불건조", "이불냄새", "베개냄새", "매트리스곰팡이"],
    "공기·생활가전": ["가습기청소", "가습기세척", "실내공기", "환기", "로봇청소기냄새", "공기청정기필터"],
    "냉난방·계절 대비": ["외풍", "난방비", "보일러에러", "보일러온도", "수도동파", "창문단열", "온수매트관리",
             "난방비절약", "외풍차단", "보일러외출", "온수매트세척", "전기장판세탁", "뽁뽁이"],
    "집 수리·안전": ["수도꼭지누수", "변기물샘", "실리콘교체", "방충망", "문고리", "콘센트", "화재예방"],
    "차량 관리": ["차량냄새", "김서림", "배터리방전", "성에제거", "겨울차량점검"],
}
PROBLEM_SEED_BATCH = 5                # 문제 시드는 검색광고 연관 키워드를 이만큼씩 묶어 한 번에 조회 (호출 수 절약)

# 제품형 판정: 키워드가 제품 시드 이름(제품 시드 = 달력 시드 중 아래 예외를 뺀 것)으로 끝나거나, 이 제품 명사로 끝나면 "사려는 검색"
# → 일반글 후보에서 빼고 "숍 참고"로 보냅니다. 제품 이름 "뒤에" 문제 단어가 붙은 것(음식물처리기냄새)은 끝이 달라서 그대로 남습니다.
PRODUCT_NOUN_ENDINGS = ("제", "기", "필터", "매트", "커버", "용기", "통", "스퀴지", "트랩", "시트", "토퍼",
                        "함", "트레이", "약", "살충제", "퇴치기", "정리대")

# 문제 시드 출신 키워드에 한해 문제 단어로 인정하는 행동 단어 (A 해결형 / B 점검·시즌형)
PROBLEM_SEED_EXTRA_A = ["세탁", "청소", "세척", "퇴치", "차단", "단열"]
PROBLEM_SEED_EXTRA_B = ["보관", "건조"]

# 지역·업체 키워드(출장·견적을 찾는 검색)는 일반글에서 제외. 지역명은 키워드가 그 이름으로 "시작"할 때, 업체 말은 어디에 있든 제외
REGION_WORDS = ["서울", "인천", "부산", "대구", "대전", "광주", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
                "수원", "성남", "고양", "용인", "부천", "안산", "안양", "김포", "파주", "화성", "평택", "남양주", "의정부", "시흥", "하남", "광명",
                "청주", "천안", "전주", "창원", "김해", "포항", "구미", "진주", "원주", "춘천", "강릉", "목포", "여수", "순천"]
REGION_NOT_PREFIX = ("고양이",)  # 지역명처럼 보이지만 다른 말인 것 (고양이 ≠ 고양시)
BUSINESS_WORDS = ["업체", "출장", "24시", "견적"]
REGION_BUSINESS_WORDS = BUSINESS_WORDS + REGION_WORDS
NOT_PRODUCT_SEEDS = {"곰팡이방지", "결로방지", "싱크대정리"}  # 시드 이름이지만 제품이 아니라 문제·행동인 것
NOT_PRODUCT_ENDINGS = ("하기", "보기", "시키기", "넣기", "쓰기", "만들기", "절약", "예약", "요약", "계약", "약속", "취약", "공약", "협약", "심함", "축축함")  # "기"로 끝나도 제품이 아닌 말 (청소하기 등)
PRODUCT_REF_TOP = 10                  # ④ 아래 "숍 참고" 표에 보여줄 개수

# 브랜드·모델 단어: 들어 있어도 빼지 않고 비고에 "브랜드·모델"만 표시. 2글자 이하 단어는 띄어쓰기 단위로 앞부분이 같을 때만 인정(레이 ↔ 스프레이 혼동 방지)
CAR_MODEL_WORDS = ["셀토스", "쏘렌토", "아반떼", "그랜저", "투싼", "스포티지", "카니발", "모닝", "레이",
                   "K3", "K5", "K7", "K8", "K9", "쏘나타", "싼타페", "팰리세이드", "니로", "코나", "캐스퍼", "QM6", "토레스", "G80"]
BRAND_MODEL_WORDS = ["쿠쿠", "코웨이", "청호", "SK", "LG", "삼성", "다이슨", "한일", "경동나비엔", "귀뚜라미", "린나이", "다이소"] + CAR_MODEL_WORDS
# 차량 카테고리 보정: "자동차·차량" 이 들어가거나, "차"가 한 단어로 따로 있고 뒤에 이런 부품·상황 말이 오면 시드와 관계없이 차량 관리로
CAR_WORDS = ["자동차", "차량"]
CAR_PART_WORDS = ["에어컨", "히터", "필터", "와이퍼", "배터리", "타이어", "엔진", "김서림", "성에", "냄새", "방향제", "세차"]

PER_CATEGORY_MAX = 8                  # 카테고리별 최대 후보 수
CATEGORY_MAX_OVERRIDE = {"차량 관리": 4}
TOTAL_MAX = 80                        # 전체 최대 후보 수
PER_SEED_MAX = 3                      # 같은 시드에서 나온 후보는 최대 이만큼 (None이면 제한 없음)
REFERENCE_BELOW = 100                 # 월 검색량이 이 값 미만이면 "참고"로 표시만
NARROW_ABOVE = 20000                  # 월 검색량이 이 값 초과면 "좁히기 필요"
RISING_TOP_N = 30                     # 상승세는 검색량 상위 이만큼만
RELATED_GAP_SEC = 0.25                # 검색광고 호출 사이 간격
MAX_FILE_BYTES = 60 * 1024            # 파일 크기 목표
FALLBACK_POST_WEEKS = 12              # 파일이 크면 ⑥을 이 주 수의 글로 줄임
RECENT_WEEKS = 4                      # ⑤ 최근 현황 주 수
PLAN_RATIO = 1.5                      # 기획 개수 = 목표의 150%
OVERLAP_TITLE_MAX = 28                # 겹침 칸에서 보여줄 기존 글 제목 길이

OVERLAP_BLOGS = (("naver", "naver_tips"), ("ssansegae", "wp_tips"))  # (블로그, 일반 후보가 들어갈 채널)


# ---------- 호출 수 세기 ----------

class CallCounter:
    """with 안에서 requests로 나간 외부 호출을 호스트별로 셉니다 (로컬 호출은 안 셈)."""

    def __init__(self):
        self.by_host: dict = {}

    def __enter__(self):
        self._orig = requests.sessions.Session.request
        counter = self

        def counted(session, method, url, *args, **kwargs):
            host = re.sub(r"^https?://", "", str(url)).split("/")[0]
            counter.by_host[host] = counter.by_host.get(host, 0) + 1
            return counter._orig(session, method, url, *args, **kwargs)

        requests.sessions.Session.request = counted
        return self

    def __exit__(self, *exc):
        requests.sessions.Session.request = self._orig
        return False

    @property
    def total(self) -> int:
        return sum(self.by_host.values())

    def summary(self) -> str:
        names = {"api.naver.com": "검색광고 API", "naverapihub.apigw.ntruss.com": "네이버 API HUB(뉴스·데이터랩)"}
        parts = [f"{names.get(h, h)} {n}회" for h, n in sorted(self.by_host.items())]
        return f"총 {self.total}회 (" + ", ".join(parts) + ")" if parts else "0회"


# ---------- 주차·목표 ----------

def week_range_label(week: str) -> str:
    """"2026-10-12" → "10/12~10/18" (월~일)"""
    mon = datetime.strptime(week, "%Y-%m-%d")
    sun = mon + timedelta(days=6)
    return f"{mon.month}/{mon.day}~{sun.month}/{sun.day}"


def weekly_targets(week: str = None) -> dict:
    """그 주(없으면 이번 주)의 목표 편수와 기획 개수(목표의 150%, 올림). 편수·유형 칸 나누기는 🎯와 같은 tkf.blog_plan()을 씁니다."""
    import target_keyword_finder as tkf  # 늦게 불러옴 (tkf가 v4_board를 쓰므로 순환을 피함)
    n_plan, split = tkf.blog_plan("naver", week)
    s_plan, s_split = tkf.blog_plan("ssansegae", week)
    return {
        "naver": n_plan, "naver_split": split,
        "naver_plan": math.ceil(n_plan * PLAN_RATIO), "naver_plan_split": {k: math.ceil(v * PLAN_RATIO) for k, v in split.items()},
        "ssansegae": s_plan, "ssansegae_split": s_split, "ssansegae_plan": math.ceil(s_plan * PLAN_RATIO),
    }


def render_schedule_md(week: str, blogs: tuple) -> list:
    """"⓪ 발행 시간표" 마크다운 줄. blogs = ("naver","ssansegae") 또는 ("longterm",). 시간표 시작일 전 주차면 빈 목록(섹션 없음).
    보여 주기만 합니다 — 예약 시간을 자동으로 넣지 않습니다."""
    days = v4_board.publish_schedule(week)
    if not days:
        return []
    names = v4_board.SLOT_BLOG_LABELS
    lines = [f"## ⓪ 발행 시간표 ({week_range_label(week)})", "",
             f"- {v4_board.PUBLISH_SLOT_NOTE} 공휴일은 일요일 칸을 씁니다."
             + ("" if blogs == ("longterm",) else " 시간 뒤 글자는 권장 유형(참고용)입니다."), "",
             "| 날짜 | " + " | ".join(names[b] for b in blogs) + " |", "|---|" + "---|" * len(blogs)]
    for d in days:
        label = f"{d['date']}({d['weekday']})" + (" 공휴일" if d["holiday"] else "")
        lines.append(_row([label] + [v4_board.slot_text(d["slots"][b]) for b in blogs]))
    return lines + [""]


# ---------- 시드 ----------

def week_seed_tables(calendar: dict, week: str) -> dict:
    """{"month", "monthly":[{seed,priority,category}], "adjacent":[...], "adjacent_months":[prev,next], "evergreen":{카테고리:[시드...]}}"""
    month = datetime.strptime(week, "%Y-%m-%d").month
    this = [dict(s) for s in calendar.get(str(month), [])]
    this_names = {s["seed"] for s in this}
    both = engine.get_seed_keywords_for_month(calendar, month, include_adjacent=True, include_evergreen=False)
    adjacent = [s for s in both if s["seed"] not in this_names]
    ever: dict = {}
    for s in calendar.get("evergreen", []):
        ever.setdefault(s.get("category") or "기타", []).append(s["seed"])
    return {
        "month": month, "monthly": this, "adjacent": adjacent,
        "adjacent_months": [12 if month == 1 else month - 1, 1 if month == 12 else month + 1], "evergreen": ever,
    }


def all_seeds(calendar: dict, week: str) -> list:
    """일반글 후보의 출발 시드 전체(시즌 + 앞뒤 달 + 상시). [{seed, category}] — 같은 시드는 한 번만."""
    month = datetime.strptime(week, "%Y-%m-%d").month
    seeds = engine.get_seed_keywords_for_month(calendar, month, include_adjacent=True, include_evergreen=True)
    out, seen = [], set()
    for s in seeds:
        name = s["seed"].replace(" ", "")
        if name in seen or not s.get("category"):
            continue
        seen.add(name)
        out.append({"seed": s["seed"], "category": s["category"]})
    return out


# ---------- 일반글 후보 ----------

def _offseason_words(month: int) -> list:
    words = []
    w = shop_children._opposite_season_word(month)
    if w:
        words.append(w)
    if month not in RAINY_MONTHS:
        words += RAINY_WORDS
    return words


def problem_seeds_list() -> list:
    """[{seed, category}] — PROBLEM_SEEDS 평탄화 (같은 시드는 한 번만)."""
    out, seen = [], set()
    for cat, names in PROBLEM_SEEDS.items():
        for n in names:
            nn = n.replace(" ", "")
            if nn not in seen:
                seen.add(nn)
                out.append({"seed": nn, "category": cat})
    return out


def is_product_like(kwn: str, product_seeds) -> bool:
    """키워드가 제품 시드 이름이나 제품 명사로 "끝나면" True (사려는 검색). 제품 이름 뒤에 문제 단어가 붙은 건 False."""
    if kwn.endswith(NOT_PRODUCT_ENDINGS) or kwn.endswith(tuple(PROBLEM_WORDS_A + PROBLEM_WORDS_B)):  # 끝이 문제·관리 단어면(교체주기·점검시기) 제품이 아님
        return False
    if any(kwn.endswith(s) for s in product_seeds):
        return True
    return kwn.endswith(PRODUCT_NOUN_ENDINGS)


def seed_has_problem_word(seedn: str) -> bool:
    """(문제 시드용) 시드 글자 안에 문제·관리 단어나 행동 단어(세탁·청소 등)가 이미 들어 있는지."""
    return any(w in seedn for w in PROBLEM_WORDS_A + PROBLEM_WORDS_B + PROBLEM_SEED_EXTRA_A + PROBLEM_SEED_EXTRA_B)


def _short_word_in_token(wl: str, token: str) -> bool:
    """2글자 이하 단어: 띄어쓰기 단위가 그 단어와 같거나, 단어 뒤에 부품·문제 말이 바로 붙을 때만 (레이스커튼·스프레이는 제외)."""
    if token == wl:
        return True
    return token.startswith(wl) and token[len(wl):].startswith(tuple(CAR_PART_WORDS + PROBLEM_WORDS_A + PROBLEM_WORDS_B))


def brand_model_hit(kw: str):
    """키워드에 들어 있는 브랜드·모델 단어(없으면 None). 2글자 이하 단어는 띄어쓰기 단위 앞부분이 같을 때만."""
    low = kw.lower()
    tokens = low.split()
    for w in BRAND_MODEL_WORDS:
        wl = w.lower()
        if len(wl) <= 2:  # 차종은 부품 말이 붙어야 인정(레이스커튼 방지), 브랜드(쿠쿠·LG·SK)는 앞부분만 같으면 인정
            if any((_short_word_in_token(wl, t) if w in CAR_MODEL_WORDS else t.startswith(wl)) for t in tokens):
                return w
        elif wl in low.replace(" ", ""):
            return w
    return None


def is_car_keyword(kw: str) -> bool:
    kwn = kw.replace(" ", "")
    if any(w in kwn for w in CAR_WORDS):
        return True
    toks = kw.lower().split()
    for w in CAR_MODEL_WORDS:
        wl = w.lower()
        if any((_short_word_in_token(wl, t) if len(wl) <= 2 else wl in t) for t in toks):
            return True
    # "차" 가 한 단어로 따로 있고(차 에어컨 / 차에어컨) 뒤에 부품·상황 말이 오는 경우
    return bool(re.search(r"(?:^|\s)차\s*(?:" + "|".join(CAR_PART_WORDS) + ")", kw))


def classify_keyword(kw: str, seed: str, month: int, origin: str = "product", product_seeds=()):
    """키워드 하나를 판정. 반환: (유형 "A"/"B"/None, 빠진 이유 또는 "", 문제·관리 단어가 있었는지)
    유형이 None이고 이유가 ""이면 문제·관리 단어가 없어서 조용히 버린 것.
    origin="problem"(문제 시드 출신)이고 시드 자체에 문제 단어가 있으면, 남은 부분이 아니라 키워드 전체에서 문제 단어를 찾고
    (시드 자체도 후보), 단어를 못 찾으면 A로 봅니다."""
    kwn = kw.replace(" ", "")
    seedn = seed.replace(" ", "")
    rem = kwn.replace(seedn, "")
    for p in NOT_A_PROBLEM_PATTERNS:
        rem = rem.replace(p, "")
    seed_clean = seedn
    for p in NOT_A_PROBLEM_PATTERNS:
        seed_clean = seed_clean.replace(p, "")
    seed_problem = origin == "problem" and seed_has_problem_word(seed_clean)
    scan = rem + seed_clean if seed_problem else rem
    a_hits = [w for w in PROBLEM_WORDS_A if w in scan]
    b_hits = [w for w in PROBLEM_WORDS_B if w in scan]
    if origin == "problem":  # 문제 시드 출신은 세탁·청소 같은 행동 단어도 문제 단어로 인정(시드에 들어 있어도 키워드 전체에서 찾음)
        full = kwn
        for p in NOT_A_PROBLEM_PATTERNS:
            full = full.replace(p, "")
        a_hits += [w for w in PROBLEM_SEED_EXTRA_A if w in full]
        b_hits += [w for w in PROBLEM_SEED_EXTRA_B if w in full]
    had = bool(a_hits or b_hits) or seed_problem
    low = kwn.lower()
    kwn_biz = kwn.replace("24시간", "")  # "24시간 환기"는 업체가 아님
    for w in REGION_BUSINESS_WORDS:
        if (w in kwn_biz) if w in BUSINESS_WORDS else (kwn.startswith(w) and not kwn.startswith(REGION_NOT_PREFIX)):
            return None, f"지역·업체({w})", had
    for w in GENERAL_EXCLUDE_WORDS:
        if w in kwn:
            return None, f"제외 단어({w})", had
    for w in PURCHASE_WORDS:
        if w in low:
            return None, f"구매 단어({w})", had
    for w in _offseason_words(month):
        if w in kwn:
            return None, f"반대 계절({w})", had
    if not had:
        return None, "", False
    if is_product_like(kwn, product_seeds):
        return None, "제품형", True
    if not (a_hits or b_hits):
        return "A", "", True
    if a_hits and b_hits:
        kind = "B" if set(a_hits) <= GENERIC_HOWTO_WORDS else "A"
    else:
        kind = "B" if b_hits else "A"
    return kind, "", True


def _seed_needle(seedn: str, kwn: str, mode: str):
    """키워드에 들어 있는 시드 글자(통째 또는 stem). 없으면 None."""
    if seedn in kwn:
        return seedn
    if mode == "stem" and len(seedn) >= 3 and seedn[:-1] in kwn:
        return seedn[:-1]
    return None


class CandList(list):
    """고른 후보 목록. 일반 list처럼 쓰되, 제품형으로 빠진 것(product_like)과 출처 수 같은 부가 정보를 함께 들고 다닙니다
    (app.py를 고치지 않고 build_salim_md까지 전달하려는 용도)."""
    product_like: list = []
    excluded_product: int = 0


def _fetch_problem_batches(seeds: list, batch_fn, errors: list, log=None) -> dict:
    """문제 시드를 PROBLEM_SEED_BATCH개씩 묶어 연관 키워드를 받습니다. 반환: {묶음 번호: (시드들, 행 목록)}"""
    out = {}
    for i in range(0, len(seeds), PROBLEM_SEED_BATCH):
        group = seeds[i:i + PROBLEM_SEED_BATCH]
        before = len(errors)
        rows = batch_fn(group, errors)
        out[i] = (group, rows)
        if len(errors) == before:
            time.sleep(RELATED_GAP_SEC)
        if log:
            log(f"문제 시드 연관 키워드 조회 {min(i + PROBLEM_SEED_BATCH, len(seeds))}/{len(seeds)}")
    return out


def _default_batch_fn(group: list, errors: list) -> list:
    """시드 여러 개를 쉼표로 묶어 검색광고 키워드도구를 한 번만 부릅니다(hintKeywords 최대 5개). 캐시에 쓰지 않습니다."""
    import naver_api
    try:
        return naver_api.get_related_keywords(",".join(group))
    except Exception as e:  # noqa: BLE001
        errors.append(f"관련 키워드 조회 실패({'·'.join(group)}): {type(e).__name__} {e}"[:160])
        return []


def collect_candidates(calendar: dict, week: str, related_fn=None, log=None, seed_match: str = None, batch_fn=None) -> dict:
    """제품 시드(시드마다 연관 키워드 1회, shop_children._related 재사용) + 문제 시드(5개씩 묶어 조회)에서 연관 키워드를 받아
    필터 → 카테고리별 선정. 반환: {"picked":[후보 dict], "dropped":[{keyword,reason,volume}], "stats":{...}, "errors":[...]}
    (뒤 단계에서 상승세·겹침을 붙임). picked는 CandList(제품형 제외 목록 product_like 포함).
    related_fn(seed) -> 제품 시드 연관 키워드 행. batch_fn(시드들, errors) -> 문제 시드 묶음 연관 키워드 행(테스트용으로 바꿔 끼움)."""
    month = datetime.strptime(week, "%Y-%m-%d").month
    seeds = all_seeds(calendar, week)
    prob_seeds = problem_seeds_list()
    cat_of = {s["seed"].replace(" ", ""): s["category"] for s in seeds}
    prob_cat_of = {s["seed"]: s["category"] for s in prob_seeds}
    product_seeds = {s["seed"].replace(" ", "") for s in seeds} - NOT_PRODUCT_SEEDS
    errors: list = []
    if related_fn is None:
        cache = se._load_cache()  # 읽기만 — 이 함수는 캐시를 저장하지 않습니다

        def related_fn(seed):
            before = len(errors)
            rows = shop_children._related(seed.replace(" ", ""), cache, errors)
            if len(errors) == before:
                time.sleep(RELATED_GAP_SEC)
            return rows

    mode = seed_match or SEED_MATCH_MODE
    best: dict = {}

    def offer(r, seed, origin, cat):
        kw = (r.get("keyword") or "").strip()
        kwn = kw.replace(" ", "")
        if not kw:
            return
        needle = _seed_needle(seed, kwn, mode if origin == "product" else "exact")
        if not needle:
            return
        if kwn == seed and not (origin == "problem" and seed_has_problem_word(seed)):
            return
        cur = best.get(kwn)
        # 더 긴(구체적인) 시드가 주인, 길이가 같으면 문제 시드가 주인
        if cur is None or len(needle) > len(cur["needle"]) or (len(needle) == len(cur["needle"]) and origin == "problem" and cur["origin"] != "problem"):
            best[kwn] = {"keyword": kw, "seed": seed, "needle": needle, "volume": r["total_count"], "competition": r.get("competition", ""),
                         "pc": r.get("pc_count"), "mobile": r.get("mobile_count"), "origin": origin, "base_category": cat}

    fetched = 0
    for s in seeds:
        seed = s["seed"].replace(" ", "")
        rows = related_fn(s["seed"])
        fetched += 1
        if log and fetched % 20 == 0:
            log(f"연관 키워드 조회 {fetched}/{len(seeds)}")
        for r in rows:
            offer(r, seed, "product", cat_of.get(seed, ""))

    batch_calls = 0
    batches = _fetch_problem_batches([s["seed"] for s in prob_seeds], batch_fn or _default_batch_fn, errors, log)
    for group, rows in batches.values():
        batch_calls += 1
        for r in rows:  # 묶음 안의 시드들 중 키워드에 글자가 들어 있는 시드와 짝지음(offer가 가장 긴 시드를 고름)
            for seed in group:
                offer(r, seed, "problem", prob_cat_of[seed])

    kept, dropped, product_like = [], [], []
    for kwn, c in best.items():
        kind, why, had = classify_keyword(c["keyword"], c["needle"], month, c["origin"], product_seeds)
        if kind:
            car = is_car_keyword(c["keyword"])
            c.update(type=kind, category="차량 관리" if car else c["base_category"], brand=brand_model_hit(c["keyword"]), moved_to_car=car and c["base_category"] != "차량 관리")
            kept.append(c)
        elif why == "제품형":
            product_like.append({"keyword": c["keyword"], "seed": c["seed"], "volume": c["volume"], "origin": c["origin"],
                                 "category": "차량 관리" if is_car_keyword(c["keyword"]) else c["base_category"]})
        elif why:  # 제외·구매·반대 계절 때문에 빠진 것 (had=True면 문제·관리 단어까지 있어서 그것 때문에만 빠진 것)
            dropped.append({"keyword": c["keyword"], "reason": why, "volume": c["volume"], "seed": c["seed"], "had_problem_word": had})

    picked = CandList(_pick(kept))
    picked.product_like = sorted(product_like, key=lambda d: -d["volume"])
    picked.excluded_product = len(product_like)
    return {
        "picked": picked, "dropped": sorted(dropped, key=lambda d: -d["volume"]),
        "stats": {"seeds": len(seeds), "problem_seeds": len(prob_seeds), "related_calls": fetched, "problem_batch_calls": batch_calls,
                  "after_filter": len(kept), "dropped": len(dropped), "product_like": len(product_like),
                  "from_product": sum(1 for c in picked if c["origin"] == "product"), "from_problem": sum(1 for c in picked if c["origin"] == "problem")},
        "errors": errors,
    }


def _pick(kept: list) -> list:
    """카테고리별 선정: 문제 시드 출신을 검색량순으로 먼저 절반(cap의 반)까지 채우고, 남은 칸은 출신 관계없이 검색량순.
    같은 시드는 PER_SEED_MAX개까지 → 전체 TOTAL_MAX개까지."""
    by_cat: dict = {}
    for c in sorted(kept, key=lambda c: -c["volume"]):
        by_cat.setdefault(c["category"], []).append(c)
    picked = []
    for cat, cs in by_cat.items():
        cap = CATEGORY_MAX_OVERRIDE.get(cat, PER_CATEGORY_MAX)
        per_seed: dict = {}
        chosen: list = []

        def take(c):
            if PER_SEED_MAX and per_seed.get(c["seed"], 0) >= PER_SEED_MAX:
                return
            per_seed[c["seed"]] = per_seed.get(c["seed"], 0) + 1
            chosen.append(c)

        for c in cs:
            if len(chosen) >= cap // 2:
                break
            if c["origin"] == "problem":
                take(c)
        for c in cs:
            if len(chosen) >= cap:
                break
            if c not in chosen:
                take(c)
        picked += chosen
    if len(picked) > TOTAL_MAX:
        picked = sorted(picked, key=lambda c: -c["volume"])[:TOTAL_MAX]
    for c in picked:
        c["reference"] = c["volume"] < REFERENCE_BELOW
        c["narrow"] = c["volume"] > NARROW_ABOVE
    return picked


def attach_rising(picked: list, persist_cache: bool = True) -> int:
    """검색량 상위 RISING_TOP_N개에 상승세%를 붙입니다(기존 타깃 키워드 발굴의 fetch_rising). 반환: 조회한 키워드 수.
    persist_cache=False면 타깃 키워드 캐시 파일에 쓰지 않습니다(미리보기용)."""
    import target_keyword_finder as tkf
    top = sorted(picked, key=lambda c: -c["volume"])[:RISING_TOP_N]
    if not top:
        return 0
    with contextlib.ExitStack() as stack:
        if not persist_cache:
            orig_put, orig_get = tkf._cache_put, tkf._cache_get
            tkf._cache_put = lambda *a, **k: None
            tkf._cache_get = lambda *a, **k: None
            stack.callback(lambda: (setattr(tkf, "_cache_put", orig_put), setattr(tkf, "_cache_get", orig_get)))
        rising = tkf.fetch_rising([c["keyword"] for c in top])
    for c in top:
        c["rising"] = rising.get(tkf.normalize_keyword(c["keyword"]))
    return len(top)


def _short(text: str, n: int = OVERLAP_TITLE_MAX) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def attach_overlaps(picked: list, blog_items: dict) -> None:
    """각 후보에 겹침을 붙입니다. blog_items = {"naver": [...], "ssansegae": [...]} (blog_overlap.load_items 결과).
    c["overlap"] = {블로그: {"dup_level": "red"/"yellow"/None, "dup": [제목...], "pair": [키워드...], "reuse": "🔁 …" 또는 ""}}
    같은 일반 채널의 겹침만 "중복"(🔴🟡), 숍 글과의 겹침은 "짝 글"(내부 링크 후보)로 따로 둡니다."""
    for c in picked:
        out = {}
        for blog, channel in OVERLAP_BLOGS:
            items = blog_items.get(blog) or []
            target = {"id": None, "channel": channel, "keyword": c["keyword"], "cluster_seed": c["seed"]}
            ms = blog_overlap.find_overlaps(target, items)
            dups = [m for m in ms if m["relation"] == blog_overlap.RELATION_DUP]
            pairs = [m for m in ms if m["relation"] == blog_overlap.RELATION_PAIR]
            reuse = ""
            if blog == blog_overlap.REUSE_TARGET_BLOG:
                naver_items = blog_items.get(blog_overlap.REUSE_SOURCE_BLOG) or []
                r = blog_overlap.find_reuse(target, naver_items)
                if r:
                    days = re.search(r"발행 (\d+)일 전", r[0]["text"])
                    when = f"{days.group(1)}일 전" if days else r[0]["state"]
                    reuse = f"🔁 네이버 같은 주제·{when} ({_short(r[0]['title'] or r[0]['keyword'], 20)})"
            out[blog] = {
                "dup_level": ("red" if any(m["level"] == "red" for m in dups) else "yellow") if dups else None,
                "dup": [_short(m["title"] or m["keyword"]) for m in dups[:2]],
                "pair": [m["keyword"] for m in pairs[:2]],
                "reuse": reuse,
            }
        c["overlap"] = out
        c["has_red"] = any(v["dup_level"] == "red" for v in out.values())


# ---------- 마크다운 ----------

def _cell(x) -> str:
    return ("" if x is None else str(x)).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _row(cells) -> str:
    return "| " + " | ".join(_cell(x) for x in cells) + " |"


def _overlap_cell(o: dict) -> str:
    parts = []
    if o["dup_level"]:
        parts.append(("🔴 같은 주제: " if o["dup_level"] == "red" else "🟡 비슷한 주제: ") + " / ".join(o["dup"]))
    if o["pair"]:
        parts.append("🔗 숍 짝: " + ", ".join(o["pair"]))
    if o["reuse"]:
        parts.append(o["reuse"])
    return " · ".join(parts)


def _candidate_note(c: dict) -> str:
    notes = []
    if c.get("reference"):
        notes.append("참고(100 미만)")
    if c.get("narrow"):
        notes.append("좁히기 필요")
    if c.get("brand"):
        notes.append(f"브랜드·모델({c['brand']})")
    if c.get("moved_to_car"):
        notes.append("차량 관리로 보정")
    return ", ".join(notes)


def candidate_stats(picked: list, categories: list) -> dict:
    def band(v):
        return "100 미만" if v < 100 else ("100~300" if v < 300 else ("300~2만" if v <= 20000 else "2만 초과"))
    bands = {"100 미만": 0, "100~300": 0, "300~2만": 0, "2만 초과": 0}
    for c in picked:
        bands[band(c["volume"])] += 1
    by_cat = {cat: {"A": sum(1 for c in picked if c["category"] == cat and c["type"] == "A"),
                    "B": sum(1 for c in picked if c["category"] == cat and c["type"] == "B")} for cat in categories}
    def count(blog, level):
        return sum(1 for c in picked if c["overlap"][blog]["dup_level"] == level)
    return {
        "total": len(picked), "A": sum(1 for c in picked if c["type"] == "A"), "B": sum(1 for c in picked if c["type"] == "B"),
        "by_category": by_cat, "bands": bands,
        "red": {b: count(b, "red") for b, _ in OVERLAP_BLOGS}, "yellow": {b: count(b, "yellow") for b, _ in OVERLAP_BLOGS},
        "red_any": sum(1 for c in picked if c["has_red"]),
        "yellow_only": sum(1 for c in picked if not c["has_red"] and any(v["dup_level"] == "yellow" for v in c["overlap"].values())),
        "reuse": sum(1 for c in picked if c["overlap"]["ssansegae"]["reuse"]),
    }


def _render_seeds(tables: dict, used_seeds: set) -> list:
    month = tables["month"]
    star = lambda s: f"{s} ★" if s.replace(" ", "") in used_seeds else s
    lines = ["## ② 이번 주 시드 (shopping_calendar.json)", "",
             f"- ★ = 이번 주 숍글 후보(③-2)가 실제로 쓴 시드. 일반글 후보(④)는 ★와 관계없이 아래 시드 전부에서 출발합니다.", "",
             f"### 시즌 시드 — {month}월", "", "| 시드 | 카테고리 | 우선순위 |", "|---|---|---|"]
    lines += [_row([star(s["seed"]), s.get("category"), s.get("priority")]) for s in tables["monthly"]]
    a, b = tables["adjacent_months"]
    lines += ["", f"### 앞뒤 달 시드 — {a}월·{b}월 (우선순위 1단계 낮춰 함께 씀)", "", "| 시드 | 카테고리 | 우선순위 |", "|---|---|---|"]
    lines += [_row([star(s["seed"]), s.get("category"), s.get("priority")]) for s in tables["adjacent"]]
    lines += ["", "### 상시 시드 (카테고리별)", ""]
    for cat, names in tables["evergreen"].items():
        lines.append(f"- **{_cell(cat)}** ({len(names)}개): " + ", ".join(star(n) for n in names))
    return lines


SHOP_CANDIDATE_MAX_CLUSTERS = 24  # ③-2 숍글 후보 표에 싣는 최대 묶음 수 (너무 길어지는 걸 막음)


def _shop_overlap_cell(o: dict) -> str:
    parts = []
    for blog, label in (("naver", "N살림노트"), ("ssansegae", "W싼세계")):
        v = (o or {}).get(blog) or {}
        if v.get("dup_level"):
            parts.append(f"{label} " + ("🔴 " if v["dup_level"] == "red" else "🟡 ") + " / ".join(v.get("dup") or []))
    return " · ".join(parts)


def attach_shop_overlaps(items: list, blog_items: dict) -> None:
    """숍글 후보 각 항목에 "최근 쓴 글 겹침"을 붙입니다(it["overlap"] = {블로그: {"dup_level","dup"}}).
    후보를 숍글로 보고(옛 숍 채널 기준) 같은 블로그의 다른 숍글과 겹치면 중복(🔴🟡)으로 셉니다. blog_items는 blog_overlap.load_items 결과."""
    channels = {"naver": "shop_naver", "ssansegae": "shop_wp"}
    for it in items:
        out = {}
        for blog, channel in channels.items():
            target = {"id": None, "channel": channel, "keyword": it.get("keyword"), "cluster_seed": it.get("cluster_seed")}
            ms = blog_overlap.find_overlaps(target, blog_items.get(blog) or [])
            dups = [m for m in ms if m["relation"] == blog_overlap.RELATION_DUP]
            out[blog] = {
                "dup_level": ("red" if any(m["level"] == "red" for m in dups) else "yellow") if dups else None,
                "dup": [_short(m["title"] or m["keyword"]) for m in dups[:2]],
            }
        it["overlap"] = out


def _render_approved(rows: list, week: str, prev_week: str) -> list:
    """③ 승인된 키워드 — 이번 주·지난주 네이버/싼세계 승인 키워드(새 채널 + 옛 숍 채널)와 글 상태. 지난주 기획 대비 실제를 보는 표."""
    lines = [
        "## ③ 승인된 키워드 — 이번 주·지난주 (네이버 · 싼세계)", "",
        f"- 이번 주({week_range_label(week)})와 지난주({week_range_label(prev_week)})에 승인한 키워드입니다. 구분의 (OLD)는 옛 숍 채널입니다.",
        "- 🎯 = 타깃 키워드 발굴 화면에서 승인 / 각도 = 같은 키워드를 네이버와 싼세계에 함께 승인한 표시 / 글 상태 = 미작성 · 작성 중 · 예약 · 발행",
        "",
    ]
    for label, w in (("이번 주", week), ("지난주", prev_week)):
        rs = [r for r in rows if r["week"] == w]
        lines += [f"### {label} ({week_range_label(w)}) — {len(rs)}개", ""]
        if not rs:
            lines += ["승인된 키워드 없음", ""]
            continue
        lines += ["| 블로그 | 구분 | 키워드 | 🎯 | 각도 | 숍글 | 월검색량 | 글 상태 |", "|---|---|---|---|---|---|---|---|"]
        for r in sorted(rs, key=lambda x: (x["blog_order"], x["old"], x["keyword"])):
            lines.append(_row([r["blog"], r["channel_label"], r["keyword"], "🎯" if r["target"] else "", r["angle"], "숍글" if r["shop"] else "",
                               r["volume"], r["state"]]))
        lines.append("")
    return lines


def _render_shop_candidates(cands: dict, seed_cats: dict) -> list:
    """③-2 숍글 후보 — 결정형(C). 저장 없이 계산만 한 후보(어떤 대기열에도 넣지 않음). 마음에 드는 키워드를 골라 🎯 타깃 키워드 화면이나 직접 추가로 승인."""
    items, status, total = cands["items"], cands["status"], cands["total_clusters"]
    lines = ["## ③-2 숍글 후보 — 결정형(C) (저장하지 않고 계산만 한 후보)", "",
             f"- {status}", "- 어떤 대기열에도 저장되지 않았습니다. 쓸 키워드는 직접 추가 또는 🎯 타깃 키워드 화면에서 승인해야 대기열에 들어갑니다.",
             "- 돌려쓰기·중복 기준은 옛 숍 큐와 새 채널(네이버·싼세계)의 승인 항목·글을 합쳐서 계산했습니다 (새 흐름에서 쓴 키워드·시드는 뒤로 밀림).",
             "- 겹침 = 같은 블로그에 이미 쓴 숍글과 핵심 단어가 겹치는지 (🔴 같은 주제 / 🟡 비슷한 주제)", ""]
    if not items:
        return lines + ["후보 없음", ""]

    def seed_of(it):
        return it.get("cluster_seed") or it.get("keyword")

    seeds = list(dict.fromkeys(seed_of(it) for it in items))
    shown_seeds = set(seeds[:SHOP_CANDIDATE_MAX_CLUSTERS])
    shown = [it for it in items if seed_of(it) in shown_seeds]
    if total > len(shown_seeds):
        lines += [f"- 묶음 {total}개 중 앞의 {len(shown_seeds)}개 묶음만 실었습니다 ({len(shown)}개 키워드)", ""]
    cats = {}
    for it in shown:
        cats.setdefault(it.get("category") or seed_cats.get(seed_of(it), "") or "기타", []).append(it)
    for cat, its in cats.items():
        lines += [f"### {cat} ({len(its)}개)", "",
                  "| 묶음 | 역할 | 키워드 | 월검색량(PC+모바일) | 경쟁도 | 점수 | 이전 승인 | 최근 쓴 글 겹침 |", "|---|---|---|---|---|---|---|---|"]
        for it in its:
            prev = it.get("duplicate_of_past_approval_week") if it.get("duplicate_of_past_approval") else ""
            lines.append(_row([seed_of(it), it.get("cluster_role") or "", it.get("keyword"), it.get("total_search_volume"), it.get("competition") or "",
                               it.get("score"), prev, _shop_overlap_cell(it.get("overlap"))]))
        lines.append("")
    return lines


def origin_summary(picked: list) -> str:
    return (f"일반글 후보 {len(picked)}개 = 제품 시드 출신 {sum(1 for c in picked if c.get('origin') == 'product')} · "
            f"문제 시드 출신 {sum(1 for c in picked if c.get('origin') == 'problem')} · 제품형 제외 {getattr(picked, 'excluded_product', 0)}")


def _render_candidates(picked: list, categories: list, targets: dict) -> list:
    st = candidate_stats(picked, categories)
    n = targets
    lines = [
        "## ④ 일반글 후보 — 해결형(A) · 점검형(B)", "",
        "- " + origin_summary(picked),
        f"- 후보 {st['total']}개 (A {st['A']} · B {st['B']}). 카테고리별 검색량 상위 최대 {PER_CATEGORY_MAX}개(차량 관리 {CATEGORY_MAX_OVERRIDE['차량 관리']}개), 전체 최대 {TOTAL_MAX}개, 같은 시드는 최대 {PER_SEED_MAX}개",
        f"- 필요한 개수: 네이버 일반 {n['naver_plan']}편 안팎(A {n['naver_plan_split']['A']}·B {n['naver_plan_split']['B']}), 싼세계 일반 해결형 {n['ssansegae_plan']}편 안팎 중 고르는 데 쓰세요",
        "- 유형(A/B)은 키워드에 붙은 단어로 추정한 값입니다. 월 100 미만은 \"참고\", 2만 초과는 \"좁히기 필요\"",
        "- 겹침: 🔴 같은 주제 / 🟡 비슷한 주제 = **같은 블로그의 일반 글** 중 핵심 단어가 겹치는 글(제목 표시). 🔗 숍 짝 = 같은 블로그 숍 글(내부 링크 후보). 🔁 = 네이버에 이미 같은 주제 글이 있어 싼세계에서 재활용 가능",
        "- 🔴가 하나라도 있는 후보는 맨 아래 \"같은 주제\" 표로 따로 모았습니다 (제외하거나 기존 글 보강·상황 좁히기 검토)",
        "",
    ]
    head = ["유형", "키워드", "시드", "월검색량", "경쟁도", "상승세%", "비고", "네이버 일반 겹침", "싼세계 일반 겹침"]
    sep = "|" + "---|" * len(head)

    def line(c, with_cat=False):
        rise = c.get("rising")
        cells = [c["type"], c["keyword"], c["seed"], f"{c['volume']:,}", c["competition"], "" if rise is None else f"{rise:+.1f}",
                 _candidate_note(c), _overlap_cell(c["overlap"]["naver"]), _overlap_cell(c["overlap"]["ssansegae"])]
        return _row(([c["category"]] if with_cat else []) + cells)

    clean = [c for c in picked if not c["has_red"]]
    for cat in categories:
        cs = sorted((c for c in clean if c["category"] == cat), key=lambda c: -c["volume"])
        lines += [f"### {cat} ({len(cs)}개)", ""]
        if not cs:
            lines += ["후보 없음", ""]
            continue
        lines += ["| " + " | ".join(head) + " |", sep] + [line(c) for c in cs] + [""]
    reds = sorted((c for c in picked if c["has_red"]), key=lambda c: (categories.index(c["category"]) if c["category"] in categories else 99, -c["volume"]))
    lines += [f"### 🔴 같은 주제 — 이미 같은 일반 글이 있음 ({len(reds)}개)", ""]
    if reds:
        lines += ["| 카테고리 | " + " | ".join(head) + " |", "|---|" + "---|" * len(head)] + [line(c, True) for c in reds]
    else:
        lines.append("없음")
    lines.append("")
    # 제품형이라 일반글에서 뺀 키워드 — 결정형(C) 기획 참고용
    ref = getattr(picked, "product_like", [])[:PRODUCT_REF_TOP]
    lines += [f"### 🛒 숍 참고(제품형으로 빠진 것) — 검색량 상위 {len(ref)}개 / 전체 {getattr(picked, 'excluded_product', 0)}개", "",
              "- 제품 이름으로 끝나서 \"사려는 검색\"으로 보고 일반글 후보에서 뺐습니다. 결정형(C) 기획 참고용.", ""]
    if ref:
        lines += ["| 키워드 | 시드 | 카테고리 | 월검색량 |", "|---|---|---|---|"]
        lines += [_row([d["keyword"], d["seed"], d["category"], f"{d['volume']:,}"]) for d in ref]
    else:
        lines.append("없음")
    lines.append("")
    return lines


def _render_recent(boards: list) -> list:
    """boards = [(월요일, build_board 결과)] 오래된 주 → 최근 주 순서."""
    lines = ["## ⑤ 최근 현황 (블로그별 · 기타 리뷰 제외)", "",
             "- 편수는 그 주(월~일)에 발행·예약된 글 기준. 유형 칸은 메모의 \"글 유형\" (숍 글에 유형이 없으면 C로 추정)", ""]
    for key, label in (("naver", "N살림노트"), ("ssansegae", "W싼세계")):
        blogs = [(w, next(b for b in bd["blogs"] if b["key"] == key)) for w, bd in boards]
        cat_labels = [c["label"] for c in blogs[0][1]["cats"]] if blogs else []
        plan = blogs[0][1]["plan"] if blogs else ""
        lines += [f"### {label} (주당 목표 {plan}편)", "", "| 주차 | 발행 | 예약 | A 해결형 | B 점검·시즌형 | C 결정형 | 유형 미기재 | 카테고리 분포 |", "|---|---|---|---|---|---|---|---|"]
        totals = {k: 0 for k in ("A", "B", "C", "")}
        cat_tot: dict = {}
        for w, b in blogs:
            tcount = {t["key"]: t["count"] for t in b["types"]}
            for k in totals:
                totals[k] += tcount.get(k, 0)
            for c in b["cats"]:
                cat_tot[c["label"]] = cat_tot.get(c["label"], 0) + c["count"]
            cats = " · ".join(f"{c['label']} {c['count']}" for c in b["cats"] if c["count"]) or "-"
            lines.append(_row([week_range_label(w), b["published"], b["scheduled"], tcount.get("A", 0), tcount.get("B", 0), tcount.get("C", 0), tcount.get("", 0), cats]))
        n_all = sum(totals.values()) or 1
        lines += ["", f"- 합계: A {totals['A']}({round(totals['A'] / n_all * 100)}%) · B {totals['B']}({round(totals['B'] / n_all * 100)}%) · "
                      f"C {totals['C']}({round(totals['C'] / n_all * 100)}%) · 미기재 {totals['']}",
                  "- 카테고리 합계: " + " · ".join(f"{k} {cat_tot.get(k, 0)}" for k in cat_labels if k in cat_tot) + "", ""]
    return lines


def render_posts(blog_posts: dict, categories: list, since: datetime = None) -> list:
    """⑥ 블로그별 기존 글 목록(간단판). since가 있으면 그보다 오래된 발행·예약 글은 뺍니다(작성 중은 유지)."""
    lines = ["## ⑥ 블로그별 기존 글 목록 (간단판 — 카테고리별, 최신순)", ""]
    for key in ("naver", "ssansegae"):
        bp = blog_posts[key]
        rows = [r for r in bp["rows"] if since is None or r["dt"] is None or r["dt"] >= since]
        total_note = "" if len(rows) == len(bp["rows"]) else " / 전체 " + str(len(bp["rows"])) + "편"
        lines += [f"### {bp['title']} ({len(rows)}편{total_note}) · 기타 리뷰 {bp['excluded']}편(목록 제외)", ""]
        order = list(categories) + sorted({r["cat"] for r in rows if r["cat"] not in categories})
        for cat in order:
            rs = sorted((r for r in rows if r["cat"] == cat), key=lambda r: (r["dt"] is not None, r["dt"] or datetime.min), reverse=True)
            if not rs:
                continue
            lines += [f"#### {cat} ({len(rs)}편)", "", "| 채널 | 유형 | 키워드 | 제목 | 발행일 |", "|---|---|---|---|---|"]
            lines += [_row(r["cells"]) for r in rs]
            lines.append("")
    return lines


def clean_search_terms(text: str) -> list:
    """붙여넣은 검색어(줄마다 하나) → 정리한 목록. 끝의 % 숫자(탭으로 나뉜 칸·괄호 포함)는 지우고, 빈 줄·중복은 뺍니다(처음 나온 것만 남김)."""
    out, seen = [], set()
    pct = re.compile(r"^\(?\s*[\d.,]+\s*%\s*\)?$")
    for raw in (text or "").replace("\r", "\n").split("\n"):
        cells = [c.strip() for c in raw.split("\t")]
        line = " ".join(c for c in cells if c and not pct.match(c))
        line = re.sub(r"\s*\(?\s*[\d.,]+\s*%\s*\)?\s*$", "", line).strip()
        key = re.sub(r"\s+", "", line).lower()
        if line and key not in seen:
            seen.add(key)
            out.append(line)
    return out


def render_search_terms_md(terms: dict) -> list:
    """## ⑦ 실제 검색어 섹션 (파일 맨 끝). 입력이 없으면 "(이번 주 입력 없음)"."""
    terms = terms or {}
    lines = ["## ⑦ 실제 검색어", ""]
    for title, key in (("⑦-1 크리에이터 어드바이저 주제별 인기 유입 검색어", "creator"), ("⑦-2 내 블로그 지난주 유입 검색어", "myblog")):
        items = terms.get(key) or []
        lines += [f"### {title}", ""]
        lines += [f"- {t}" for t in items] if items else ["(이번 주 입력 없음)"]
        lines.append("")
    return lines


def build_salim_md(ctx: dict) -> tuple:
    """살림 기획 자료 마크다운을 만들어 (글자, 요약 dict)로 돌려줍니다. 파일은 쓰지 않습니다.
    ctx: week, now, categories, calendar, seed_cats, approved_rows(③ 승인된 키워드), shop_candidates(③-2 숍글 후보), cand(collect_candidates+붙임 결과의 picked), boards, blog_posts, api_summary, notes"""
    week, now, categories = ctx["week"], ctx["now"], ctx["categories"]
    t = weekly_targets(week)
    sp = t["naver_split"]
    used = {(it.get("cluster_seed") or "").replace(" ", "") for it in ctx["shop_candidates"]["items"]}
    tables = week_seed_tables(ctx["calendar"], week)
    since = None
    sections = {
        "seeds": _render_seeds(tables, used),
        "shop": _render_approved(ctx["approved_rows"], week, v4_board.shift_week(week, -1)) + _render_shop_candidates(ctx["shop_candidates"], ctx["seed_cats"]),
        "cand": _render_candidates(ctx["picked"], categories, t),
        "recent": _render_recent(ctx["boards"]),
    }

    def assemble(since):
        head = [
            "# 살림 기획 자료", "",
            f"- 만든 시각: {now:%Y-%m-%d %H:%M}",
            f"- 대상 주차: {week_range_label(week)} (월요일 {week})",
            f"- 이번 주 목표 편수: 네이버 블로그 {t['naver']}편 (A {sp['A']} · B {sp['B']} · C {sp['C']}) · 싼세계 {t['ssansegae']}편",
            f"- 기획 개수 기준: 목표의 {int(PLAN_RATIO * 100)}% — 네이버 {t['naver_plan']}편 (A {t['naver_plan_split']['A']} · B {t['naver_plan_split']['B']} · C {t['naver_plan_split']['C']}), 싼세계 {t['ssansegae_plan']}편",
            "- 카테고리 10개: " + " · ".join(categories),
        ]
        head.append("- " + origin_summary(ctx["picked"]))
        head += [f"- {n}" for n in ctx.get("notes", [])]
        if since is not None:
            head.append(f"- ⚠ 파일이 {MAX_FILE_BYTES // 1024}KB를 넘어, ⑥ 기존 글 목록은 최근 {FALLBACK_POST_WEEKS}주({since:%Y-%m-%d} 이후) 글만 실었습니다 (작성 중 글은 전부)")
        head += ["- 읽기 전용 자료입니다 — 저장된 값과 검색량만 모았고 판단은 Claude가 합니다.", ""]
        head += render_schedule_md(week, ("naver", "ssansegae"))  # ⓪ 발행 시간표 (10/19 주부터, 그 전 주차는 없음)
        tail = ["## 📌 안내", "", "후보에 없는 주제가 필요하면 Claude가 추가 제안 가능 — 검색량은 🎯타깃 키워드 발굴로 확인(선택).", ""]
        return "\n".join(head + sections["seeds"] + [""] + sections["shop"] + sections["cand"] + sections["recent"]
                         + render_posts(ctx["blog_posts"], categories, since) + tail
                         + render_search_terms_md(ctx.get("search_terms"))) + "\n"

    text = assemble(None)
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        since = datetime.strptime(week, "%Y-%m-%d") - timedelta(weeks=FALLBACK_POST_WEEKS)
        text = assemble(since)
    return text, {"bytes": len(text.encode("utf-8")), "post_cutoff": since.strftime("%Y-%m-%d") if since else None}
