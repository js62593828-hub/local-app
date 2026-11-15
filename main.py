"""
키워드/시즌 캘린더 엔진 — 메인 실행 파일

실행 방법:
    python main.py                  # 이번 달 기준으로 실행, 상위 10개 출력
    python main.py --month 12       # 특정 월을 지정해서 실행 (테스트용)
    python main.py --top 20         # 상위 몇 개를 뽑을지 지정
    python main.py --days 7         # 다음 며칠치 콘텐츠 큐를 만들지 지정

출력: content_queue.json 파일로 저장 + 화면에 요약 출력
"""
import argparse
import functools
import json
import random
import uuid
from datetime import datetime, timedelta

import autocomplete
import config
import naver_api
import naver_hub_api
import longterm_news
import scorer
import shop_children
import signal_enricher

# ── 조절용 상수 (여기만 고치면 전체에 반영됩니다) ─────────────────────
# 공통(정책·쇼핑 둘 다)
MAX_CHILDREN_PER_CLUSTER = 4  # 대표 키워드 하나당 하위(롱테일) 키워드를 몇 개까지 붙일지
MIN_CHILDREN_TARGET = 3       # 이 개수 밑으로 떨어지면 추가 조회로 채워봅니다 (필러-클러스터 전략상 3~5개 권장)

# 롱텀(정책) 채널 전용 (아래 EVERGREEN_PER_CATEGORY·CATEGORIES는 롱텀 기본값 — 숍은 SHOP_* 상수를 따로 씁니다)
EVERGREEN_PER_CATEGORY = 6        # 상시(evergreen) 시드를 매주 카테고리별로 몇 개씩만 뽑을지
SEED_COOLDOWN_WEEKS = 4           # 최근 이 주 수 안에 승인·글 생성된 시드는 상시 시드 뽑기에서 뒤로 미룸
LONGTERM_MAX_CHILDREN = 2         # 클러스터당 세부 키워드 최대 개수 (뉴스 보장 1자리 포함) — 묶음은 대표 1 + 세부 2 = 3편 (2026-11-02, 3→2)
MIN_CLUSTERS_PER_CATEGORY = 3     # 큐를 채울 때 카테고리별로 먼저 확보할 최소 클러스터 수
MAX_CLUSTER_SLOTS = 1 + LONGTERM_MAX_CHILDREN  # 한 시드 클러스터가 큐에서 차지할 수 있는 최대 칸 (대표 1 + 세부)
MIN_CHILD_VOLUME_LONGTERM = 50    # 이 검색량 미만인 세부 키워드는 버림 (대표 키워드는 그대로). 연도만 붙은 "○○2026"도 이 값 이상이어야 통과
BANNED_CHILD_WORDS = [            # 이런 커뮤니티/플랫폼 이름이 붙은 세부 키워드는 제외 (롱텀 전용). "계산기"·"비교"는 글 주제로 안 쓰기로 해서 함께 제외
    "디시", "갤러리", "나무위키", "유튜브", "블로그", "카페", "커뮤니티", "인스타", "뽐뿌", "클리앙",
    "계산기", "비교",
]
CATEGORIES = ["세금·연말정산", "4대보험·연금", "정부지원금·복지", "서민금융·정책자금",
              "자격증·국비교육", "생활요금·해지환불"]  # 워드프레스(블로그) 카테고리명과 동일

# 숍 채널 전용 — 상시(evergreen) 시드 돌려쓰기 (select_longterm_seeds를 카테고리·개수만 바꿔서 같이 씁니다)
SHOP_CATEGORIES = [
    "냉난방·계절 대비", "습기·곰팡이·해충", "세탁·의류관리", "욕실·청소", "주방 살림",
    "수납·정리", "침구·패브릭", "공기·생활가전", "집 수리·안전", "차량 관리",
]
# 예전 이름 → 새 이름 (이미 쓴 글 메모의 "카테고리 제안"이 예전 이름이어도 새 이름으로 간주 — 표시·집계 전용, json은 안 고침)
CATEGORY_ALIASES = {"난방·겨울 대비": "냉난방·계절 대비", "습기·결로·곰팡이": "습기·곰팡이·해충"}
SHOP_EVERGREEN_PER_CATEGORY = 3   # 숍은 상시 시드를 매주 카테고리별로 몇 개씩 뽑을지
SHOP_MIN_CLUSTERS_PER_CATEGORY = 2  # 숍 대기열을 채울 때 카테고리별로 먼저 확보할 최소 묶음 수
SHOP_MAX_CLUSTER_SLOTS = 2          # 숍 묶음 하나가 대기열에서 차지할 최대 칸 (대표 1 + 세부 1)

# 가장 최근 build_weekly_queue 실행의 진단 정보 (화면·모의 실행 검증용, 저장은 호출하는 쪽이 결정)
LAST_RUN_INFO: dict = {}


def make_item_id(keyword: str, week: str) -> str:
    """키워드+주차+짧은 랜덤값으로 고유 ID를 만듭니다."""
    return f"{week}_{uuid.uuid4().hex[:8]}"


def get_week_label(date: datetime = None) -> str:
    """해당 날짜가 속한 주(월요일 기준)의 라벨을 YYYY-MM-DD 형식으로 반환합니다."""
    date = date or datetime.now()
    monday = date - timedelta(days=date.weekday())
    return monday.strftime("%Y-%m-%d")


def shift_week(week: str, delta: int) -> str:
    """주차 라벨을 delta주만큼 앞/뒤로 이동시킵니다."""
    monday = datetime.strptime(week, "%Y-%m-%d")
    return get_week_label(monday + timedelta(weeks=delta))


def load_seasonal_calendar(path: str = "seasonal_calendar.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_seed_keywords_for_month(
    calendar: dict, month: int, include_adjacent: bool = True, include_evergreen: bool = True
) -> list[dict]:
    """
    해당 월의 시드 키워드에 더해, 범위를 넓히기 위해:
    - 전달/다음달 시드도 우선순위를 살짝 낮춰서 함께 포함 (시즌 전환기 키워드 확보)
    - 'evergreen'(상시 유효한 생활금융 키워드)도 매달 함께 포함 (include_evergreen=False면 제외 —
      롱텀은 상시 시드를 select_longterm_seeds에서 카테고리별로 돌려가며 따로 뽑습니다)
    같은 시드가 여러 곳에 있으면 가장 먼저 발견된(가장 우선순위 높은) 것만 유지합니다.
    """
    seeds = [dict(s) for s in calendar.get(str(month), [])]
    seen = {s["seed"] for s in seeds}

    if include_adjacent:
        prev_month = 12 if month == 1 else month - 1
        next_month = 1 if month == 12 else month + 1
        for adj_month in (prev_month, next_month):
            for s in calendar.get(str(adj_month), []):
                if s["seed"] in seen:
                    continue
                seen.add(s["seed"])
                # 인접 월 시드는 이번 달 시드보다 한 단계 낮은 우선순위로 포함
                seeds.append({"seed": s["seed"], "priority": max(1, s["priority"] - 1), "category": s.get("category")})

    if include_evergreen:
        for s in calendar.get("evergreen", []):
            if s["seed"] in seen:
                continue
            seen.add(s["seed"])
            seeds.append(dict(s))

    return seeds


def seed_category_map(calendar: dict) -> dict:
    """{시드: 카테고리} — 월별·상시 시드 전부. category가 없는 시드(숍 캘린더 등)는 들어가지 않습니다."""
    out = {}
    for key, value in calendar.items():
        if key.startswith("_") or not isinstance(value, list):
            continue
        for s in value:
            if s.get("category"):
                out.setdefault(s["seed"], s["category"])
    return out


def select_longterm_seeds(
    calendar: dict,
    month: int,
    week: str,
    seed_last_used: dict = None,
    per_category: int = EVERGREEN_PER_CATEGORY,
    categories: list = CATEGORIES,
) -> dict:
    """
    롱텀 이번 주 시드를 고릅니다. 숍도 같이 씀(인자로 카테고리·개수 지정).
      - 월별 시드(이번 달 + 인접 달)는 로테이션 없이 항상 포함
      - 상시(evergreen) 시드는 카테고리별로 EVERGREEN_PER_CATEGORY개만. 우선순위는
        ① 최근 SEED_COOLDOWN_WEEKS주 안에 승인·글 생성된 시드가 아닌 것 → ② 마지막으로 쓴 지 오래된 것(한 번도 안 쓴 게 가장 먼저)
        → ③ 같은 순위끼리는 주차 문자열을 시드로 한 랜덤 섞기(같은 주에는 다시 불러와도 같은 결과).
    seed_last_used: {시드: 마지막으로 승인·글 생성된 주차 "YYYY-MM-DD"} — app.py가 이번 주보다 이전 주차 데이터로만 만들어 넘깁니다.
    반환: {"seeds": [시드 dict...], "monthly": [시드명...], "evergreen": {카테고리: [시드명...]}, "deferred": {카테고리: [뒤로 밀린 시드명...]}}
    """
    seed_last_used = seed_last_used or {}
    monthly = get_seed_keywords_for_month(calendar, month, include_evergreen=False)
    taken = {s["seed"] for s in monthly}

    pool: dict[str, list[dict]] = {}
    for s in calendar.get("evergreen", []):
        if s["seed"] in taken:
            continue  # 월별 시드로 이미 들어간 시드는 상시 풀에서 뺍니다
        pool.setdefault(s.get("category") or "기타", []).append(s)

    chosen, evergreen_names, deferred = [], {}, {}
    for cat in list(categories) + [c for c in pool if c not in categories]:
        items = list(pool.get(cat, []))
        if not items:
            continue
        random.Random(f"{week}|{cat}").shuffle(items)  # 같은 순위끼리의 순서 (sorted는 안정 정렬)

        def rank(s):
            last = seed_last_used.get(s["seed"])
            recent = last is not None and _weeks_between(last, week) <= SEED_COOLDOWN_WEEKS
            return (1 if recent else 0, last or "")  # 최근 쓴 건 뒤로, 그다음 오래 안 쓴(빈 문자열=한 번도 안 씀) 것 먼저

        items.sort(key=rank)
        picked, rest = items[:per_category], items[per_category:]
        chosen.extend(dict(s) for s in picked)
        evergreen_names[cat] = [s["seed"] for s in picked]
        deferred[cat] = [s["seed"] for s in rest]

    return {
        "seeds": monthly + chosen,
        "monthly": [s["seed"] for s in monthly],
        "evergreen": evergreen_names,
        "deferred": deferred,
    }


def _match_seed(keyword: str, seed_words: list[str], longest: bool = False):
    """이 키워드가 어떤 시드에서 나왔는지. longest=True면 여러 시드가 맞을 때 가장 긴(구체적인) 시드를 고릅니다
    (예: "버팀목전세자금대출"은 "전세자금대출"과 "버팀목전세자금대출" 둘 다 포함하지만 뒤의 것이 맞음).
    기본값(False)은 예전처럼 목록에서 먼저 맞는 시드 — 숍 채널 동작은 그대로입니다."""
    hits = [s for s in seed_words if s.replace(" ", "") in keyword]
    if not hits:
        return None
    return max(hits, key=lambda s: len(s.replace(" ", ""))) if longest else hits[0]


def _child_ok(item: dict, quality: dict | None) -> bool:
    """롱텀 세부 키워드 품질 기준: 검색량 하한 + 커뮤니티/플랫폼 이름 제외. quality가 None이면 항상 통과(숍)."""
    if quality is None:
        return True
    if item["total_count"] < quality["min_volume"]:
        return False
    return not any(w in item["keyword"] for w in quality["banned"])


def _longterm_quality() -> dict:
    return {"min_volume": MIN_CHILD_VOLUME_LONGTERM, "banned": BANNED_CHILD_WORDS}


def _get_scored_items(
    month: int,
    week: str = None,
    calendar_path: str = "seasonal_calendar.json",
    intent_keywords: list[str] = None,
    seeds: list[dict] = None,
    longest_match: bool = False,
    quality: dict = None,
) -> list[dict]:
    """
    이 달의 시드로 네이버 API를 조회하고, 각 결과가 어떤 시드에서 나왔는지(cluster_seed)까지
    붙여서 돌려줍니다. 어떤 시드와도 연결되지 않는 결과(2차 확장이 곁길로 샌 경우 등)는
    클러스터를 만들 수 없으므로 여기서 걸러냅니다.
    seeds를 넘기면(롱텀: select_longterm_seeds 결과) 캘린더에서 다시 고르지 않고 그 시드를 그대로 씁니다.
    """
    if seeds is None:
        calendar = load_seasonal_calendar(calendar_path)
        seeds = get_seed_keywords_for_month(calendar, month)

    if not seeds:
        print(f"[경고] {month}월에 해당하는 시드 키워드가 {calendar_path}에 없습니다.")
        return []

    seed_words = [s["seed"] for s in seeds]
    priority_map = {s["seed"]: s["priority"] for s in seeds}
    year_min = quality["min_volume"] if quality else None

    # week를 넘기면(주간 큐 생성 시) 목 모드/2차 확장 결과가 주차마다 달라집니다.
    raw_stats = naver_api.get_keyword_stats(seed_words, variation_key=week)

    scored = []
    matched_seeds = set()
    for item in raw_stats:
        # 이 키워드가 어떤 시드에서 나왔는지 찾아서 그 시즌 우선순위 + 클러스터를 정합니다.
        # (naver_api.py가 API로 보낼 때 공백을 제거하므로, 매칭도 공백을 무시하고 비교)
        matched_seed = _match_seed(item["keyword"], seed_words, longest_match)
        if matched_seed is None:
            continue  # 어떤 시드와도 안 이어지면 클러스터를 만들 수 없어서 제외
        matched_seeds.add(matched_seed)
        priority = priority_map.get(matched_seed, 3)
        scored_item = scorer.score_keyword(
            item, priority, seed=matched_seed, intent_keywords=intent_keywords, year_min_volume=year_min
        )
        scored_item["cluster_seed"] = matched_seed
        scored.append(scored_item)

    LAST_RUN_INFO["seeds_without_results"] = [s for s in seed_words if s not in matched_seeds]
    return scored


def build_ranked_list(month: int, week: str = None, calendar_path: str = "seasonal_calendar.json") -> list[dict]:
    """(CLI 테스트용) 클러스터 없이 그냥 점수순 평평한 목록."""
    scored = _get_scored_items(month, week, calendar_path)
    return scorer.rank_keywords(scored)


# (MAX_CHILDREN_PER_CLUSTER, MIN_CHILDREN_TARGET 상수는 파일 맨 위 "조절용 상수" 구역으로 옮겼습니다.)

# 네이버의 "관련 키워드 추천"이 이런 조합을 알아서 잘 안 던져주는 경우가 많아서, 사람들이
# 실제로 많이 검색하는 수식어 조합을 우리가 먼저 만들어서 직접 검색량이 있는지 확인합니다.
# 연도를 붙이면 "최신 정보"라는 느낌도 주고, 내년엔 연도만 바꿔서 같은 클러스터를 다시 쓸 수도
# 있습니다. (구글 자동완성과 네이버 실제 검색 데이터가 다를 수 있어서, 여기 만든 조합도 실제
# 검색량이 0으로 나오면 채택하지 않습니다 — 무조건 다 넣는 게 아니라 진짜 검색되는 것만 씁니다.)
CHILD_SUFFIX_CANDIDATES = [
    "신청방법", "신청기간", "신청조건", "신청자격", "지원금액", "조건", "대상",
    "금액", "얼마", "총정리", "후기", "조회방법",  # ("계산기"는 롱텀에서 안 쓰기로 해서 뺌)
]

# 쇼핑 콘텐츠용 수식어 조합 (정책 콘텐츠와는 완전히 다른 검색 패턴)
SHOP_CHILD_SUFFIX_CANDIDATES = [
    "추천", "순위", "베스트", "비교", "가성비", "후기", "리뷰", "종류", "고르는법", "브랜드",
]


def _build_candidate_children(seed: str, year: int, suffixes: list[str] = None) -> list[str]:
    """시드 + 흔한 검색의도 수식어(연도 포함)를 조합해서, 실제 검색량이 있는지 확인해볼
    후보 목록을 만듭니다. 만드는 것 자체는 채택을 보장하지 않고, 이후 실제 검색량이 있는
    것만 하위 키워드로 채택됩니다."""
    stripped = seed.replace(" ", "")
    suffixes = suffixes if suffixes is not None else CHILD_SUFFIX_CANDIDATES
    candidates = [f"{stripped}{suffix}" for suffix in suffixes]
    candidates.append(f"{stripped}{year}")
    return candidates


def _cluster_score(head: dict | None, children: list[dict]) -> float:
    """클러스터 정렬용 점수. 대표 점수를 기준으로 삼고, 하위 키워드들이 많고 좋을수록 가산."""
    if head:
        anchor = head["score"]
        bonus_items = children
    elif children:
        anchor = children[0]["score"]
        bonus_items = children[1:]
    else:
        return 0.0
    bonus = sum(c["score"] for c in bonus_items) * 0.35
    return round(anchor + bonus, 2)


def build_clusters(
    scored_items: list[dict], heads_only: bool = False, quality: dict = None, max_children: int = None
) -> list[dict]:
    """
    시드(대표 키워드)별로 결과를 묶습니다.
    각 클러스터: {"seed", "head"(대표 키워드 항목, 없을 수 있음), "children"(하위 키워드들), "cluster_score"}

    heads_only=True면: 시드 자체가 관련키워드 결과에 정확히 안 나오는 경우가 흔해서(네이버
    API가 "관련 키워드"를 돌려주는 거지 시드 그 자체를 다시 돌려주진 않는 경우가 많음),
    시드와 정확히 일치하는 항목이 없으면 그 시드 아래에서 점수가 가장 높은 항목을 대표로
    대신 씁니다 — 이렇게 안 하면 "대표만 보여달라"고 했을 때 결과가 통째로 사라집니다.
    """
    groups: dict[str, list[dict]] = {}
    for item in scored_items:
        groups.setdefault(item["cluster_seed"], []).append(item)

    clusters = []
    for seed, items in groups.items():
        stripped_seed = seed.replace(" ", "")
        head_candidates = [i for i in items if i["keyword"] == stripped_seed]
        head = max(head_candidates, key=lambda i: i["score"]) if head_candidates else None

        if heads_only:
            if head is None and items:
                # 시드 그 자체와 정확히 일치하는 항목이 없으면, 이 시드 아래에서 가장
                # 점수가 높은 실제 키워드를 대표로 대신 씁니다.
                head = max(items, key=lambda i: i["score"])
            if head is None:
                continue
            clusters.append({"seed": seed, "head": head, "children": [], "cluster_score": _cluster_score(head, [])})
            continue

        child_pool = [
            i for i in items if i is not head and i.get("has_search_intent") and _child_ok(i, quality)
        ]
        children = sorted(child_pool, key=lambda i: i["score"], reverse=True)[: max_children or MAX_CHILDREN_PER_CLUSTER]

        if head is None and not children:
            continue  # 대표도 없고 의도 뚜렷한 하위 키워드도 없으면 클러스터로 쓸 게 없음

        clusters.append(
            {
                "seed": seed,
                "head": head,
                "children": children,
                "cluster_score": _cluster_score(head, children),
            }
        )

    clusters.sort(key=lambda c: c["cluster_score"], reverse=True)
    return clusters


def _query_candidates_for_volume(
    cluster: dict,
    candidates: list[str],
    week_tag: str,
    existing_keywords: set,
    intent_keywords: list[str] = None,
    cap: bool = True,
    quality: dict = None,
    max_children: int = None,
) -> None:
    """후보 키워드 문자열 목록의 실제 네이버 검색량을 확인해서, 검색 기록이 있고
    검색 의도가 뚜렷한 것만 그 클러스터의 하위 키워드로 채택합니다. cap=True(기본값)면
    클러스터가 이미 꽉 찬 순간 더 안 보고 멈춥니다(부족한 클러스터를 "채우는" 용도).
    cap=False면 꽉 찼어도 일단 다 넣어보고, 호출한 쪽에서 점수 기준으로 다시 추려냅니다
    (이미 채워진 클러스터라도 "더 좋은 후보"가 있으면 기존 것보다 끼워 넣기 위한 용도 —
    예: 쇼핑 콘텐츠에서 지금 뜨는 구체적 신제품을 일반적인 하위 키워드보다 우선하고 싶을 때)."""
    if not candidates:
        return
    try:
        stats = naver_api.get_keyword_stats(candidates, variation_key=week_tag)
    except Exception:
        return  # 이 클러스터는 그냥 있는 만큼만 쓰고 넘어감 (전체 생성이 실패하면 안 되므로)

    limit = max_children or MAX_CHILDREN_PER_CLUSTER
    year_min = quality["min_volume"] if quality else None
    for item in stats:
        if cap and len(cluster["children"]) >= limit:
            break
        if item["keyword"] in existing_keywords:
            continue
        if cluster["seed"].replace(" ", "") not in item["keyword"]:
            continue
        if item["total_count"] <= 0:
            continue  # 네이버에 실제 검색 기록이 없으면(0이면) 채택하지 않음
        if not _child_ok(item, quality):
            continue
        if not scorer.has_search_intent(
            item["keyword"], cluster["seed"], intent_keywords, total_count=item["total_count"], year_min_volume=year_min
        ):
            continue

        priority = (
            cluster["head"]["seasonal_priority"]
            if cluster["head"]
            else (cluster["children"][0]["seasonal_priority"] if cluster["children"] else 3)
        )
        scored_item = scorer.score_keyword(
            item, priority, seed=cluster["seed"], intent_keywords=intent_keywords, year_min_volume=year_min
        )
        scored_item["cluster_seed"] = cluster["seed"]
        cluster["children"].append(scored_item)
        existing_keywords.add(item["keyword"])


def _backfill_underfilled_children(
    clusters: list[dict],
    week: str,
    intent_keywords: list[str] = None,
    child_suffix_candidates: list[str] = None,
    quality: dict = None,
    max_children: int = None,
    longest_match: bool = False,
) -> list[dict]:
    """
    하위 키워드가 MIN_CHILDREN_TARGET(기본 3개)에 못 미치는 클러스터를 채웁니다. 순서대로
    시도하되, 어느 단계든 "실제로 검색 기록이 있는 것만" 채택하는 원칙은 동일합니다:

      1) 네이버 키워드도구에 시드를 다시 한번 물어봐서 관련 키워드 추가 조회
      2) 그래도 부족하면, 구글/네이버 자동완성에서 "지금 실제로 사람들이 검색하는" 진짜
         연관 검색어를 가져와서, 그 후보들의 실제 네이버 검색량을 확인한 뒤 채택합니다.
         이게 핵심 방식입니다 — 미리 정해둔 수식어를 기계적으로 붙이는 게 아니라, 실제
         검색 행동(자동완성)에서 출발해서 검증합니다.
      3) 그래도도 부족하면 마지막 안전장치로, 흔히 쓰이는 수식어 조합(+연도)의 검색량을
         확인해서 채웁니다. 이건 어디까지나 최후의 수단이고, 이것도 실제 검색량이 있는
         것만 채택합니다.
    """
    limit = max_children or MAX_CHILDREN_PER_CLUSTER
    year_min = quality["min_volume"] if quality else None
    underfilled = [c for c in clusters if len(c["children"]) < min(MIN_CHILDREN_TARGET, limit)]
    if not underfilled:
        return clusters

    existing_keywords = {
        item["keyword"]
        for c in clusters
        for item in (([c["head"]] if c["head"] else []) + c["children"])
    }

    # 1) 시드 재조회
    extra_seeds = [c["seed"] for c in underfilled]
    extra_stats = naver_api.get_keyword_stats(extra_seeds, variation_key=f"{week}-backfill")
    by_seed = {c["seed"]: c for c in underfilled}
    for item in extra_stats:
        matched_seed = _match_seed(item["keyword"], extra_seeds, longest_match)
        if matched_seed is None or item["keyword"] in existing_keywords:
            continue
        cluster = by_seed[matched_seed]
        if len(cluster["children"]) >= limit:
            continue
        if not _child_ok(item, quality):
            continue
        if not scorer.has_search_intent(
            item["keyword"], matched_seed, intent_keywords, total_count=item["total_count"], year_min_volume=year_min
        ):
            continue
        priority = (
            cluster["head"]["seasonal_priority"]
            if cluster["head"]
            else (cluster["children"][0]["seasonal_priority"] if cluster["children"] else 3)
        )
        scored_item = scorer.score_keyword(
            item, priority, seed=matched_seed, intent_keywords=intent_keywords, year_min_volume=year_min
        )
        scored_item["cluster_seed"] = matched_seed
        cluster["children"].append(scored_item)
        existing_keywords.add(item["keyword"])

    # 2) 구글/네이버 자동완성 기반 실제 연관 검색어 — 핵심 방식
    still_underfilled = [c for c in underfilled if len(c["children"]) < min(MIN_CHILDREN_TARGET, limit)]
    for cluster in still_underfilled:
        live_candidates = autocomplete.gather_related_queries(cluster["seed"])
        _query_candidates_for_volume(
            cluster, live_candidates, f"{week}-autocomplete-{cluster['seed']}", existing_keywords, intent_keywords,
            quality=quality, max_children=limit,
        )

    # 3) 그래도 부족하면 마지막 안전장치 (흔한 수식어 조합 + 연도)
    still_underfilled = [c for c in underfilled if len(c["children"]) < min(MIN_CHILDREN_TARGET, limit)]
    year = datetime.now().year
    for cluster in still_underfilled:
        fallback_candidates = _build_candidate_children(cluster["seed"], year, child_suffix_candidates)
        _query_candidates_for_volume(
            cluster, fallback_candidates, f"{week}-fallback-{cluster['seed']}", existing_keywords, intent_keywords,
            quality=quality, max_children=limit,
        )

    for c in underfilled:
        c["children"].sort(key=lambda i: i["score"], reverse=True)
        c["children"] = c["children"][:limit]
        c["cluster_score"] = _cluster_score(c["head"], c["children"])

    clusters.sort(key=lambda c: c["cluster_score"], reverse=True)
    return clusters


def _weeks_between(week_a: str, week_b: str) -> int:
    """두 주차 라벨("YYYY-MM-DD") 사이의 주(week) 차이를 구합니다. 파싱 실패 시 아주 큰 값을
    돌려줘서(=충분히 오래된 것으로 취급) 안전하게 동작하도록 합니다."""
    try:
        d_a = datetime.strptime(week_a, "%Y-%m-%d")
        d_b = datetime.strptime(week_b, "%Y-%m-%d")
        return abs((d_b - d_a).days) // 7
    except (ValueError, TypeError):
        return 9999


def _autocomplete_expand_all_clusters(clusters: list[dict], week: str, intent_keywords: list[str] = None) -> list[dict]:
    """
    이미 충분히 채워진 클러스터라도(부족한 것만이 아니라) 전부 자동완성으로 한 번 더
    확인합니다. 쇼핑 콘텐츠는 신제품·인기 모델처럼 몇 주 단위로 빠르게 바뀌는 대상이
    많아서, 네이버 키워드도구가 이미 충분한 하위 키워드를 줬더라도 "지금 실제로 많이
    검색되는 구체적인 이름"(예: 특정 제품 모델명)을 놓치기 쉽습니다. 새로 찾은 후보는
    무조건 추가되는 게 아니라 기존 하위 키워드들과 점수로 경쟁해서, 진짜 검색량이 높은
    것만 최종적으로 살아남습니다.
    """
    existing_keywords = {
        item["keyword"]
        for c in clusters
        for item in (([c["head"]] if c["head"] else []) + c["children"])
    }
    for cluster in clusters:
        live_candidates = autocomplete.gather_related_queries(cluster["seed"])
        _query_candidates_for_volume(
            cluster,
            live_candidates,
            f"{week}-trend-{cluster['seed']}",
            existing_keywords,
            intent_keywords,
            cap=False,  # 이미 꽉 찬 클러스터라도 일단 넣어보고, 아래에서 점수로 다시 추림
        )

    for c in clusters:
        c["children"].sort(key=lambda i: i["score"], reverse=True)
        c["children"] = c["children"][:MAX_CHILDREN_PER_CLUSTER]
        c["cluster_score"] = _cluster_score(c["head"], c["children"])

    clusters.sort(key=lambda c: c["cluster_score"], reverse=True)
    return clusters


def _trim_children(clusters: list[dict], cap: int, quality: dict = None) -> list[dict]:
    """세부 키워드를 cap개로 자릅니다. 뉴스 근거가 붙은(뉴스 보장) 항목은 되도록 남기고, 나머지 중 점수가 낮은 것부터 뺍니다.
    품질 기준(quality)에 안 맞는 것도 여기서 한 번 더 걸러냅니다. 대표도 세부도 없어진 클러스터는 뺍니다."""
    kept = []
    for c in clusters:
        children = [ch for ch in c["children"] if _child_ok(ch, quality)]
        while len(children) > cap:
            removable = [ch for ch in children if "news_evidence" not in ch] or children
            children.remove(min(removable, key=lambda ch: ch["score"]))
        children.sort(key=lambda ch: ch["score"], reverse=True)
        c["children"] = children
        c["cluster_score"] = _cluster_score(c["head"], children)
        if c["head"] or children:
            kept.append(c)
    kept.sort(key=lambda c: c["cluster_score"], reverse=True)
    return kept


def _cluster_entries(
    cluster: dict,
    week: str,
    exclude_keywords: set,
    approved_history: dict,
    duplicate_cooldown_weeks: int,
    category: str | None = None,
    slot_cap: int | None = None,
) -> list[dict]:
    """한 클러스터가 큐에 넣을 항목들(대표 → 세부 순). 이번 주 이미 승인된 키워드와 최근 승인 이력 키워드는 뺍니다."""
    entries = []
    # heads_only 클러스터도 children_builder가 세부 키워드를 채웠으면 함께 보여줍니다 (안 채웠으면 빈 목록이라 기존과 동일).
    members = ([cluster["head"]] if cluster["head"] else []) + cluster["children"]
    for member in members:
        if slot_cap is not None and len(entries) >= slot_cap:
            break
        keyword = member["keyword"]
        if keyword in exclude_keywords:
            continue
        prev_week = approved_history.get(keyword)
        if prev_week is not None and _weeks_between(prev_week, week) < duplicate_cooldown_weeks:
            continue  # 최근(기본 8주 이내)에 이미 썼던 키워드라 후보에서 뺍니다.
        entry = {
            "id": make_item_id(keyword, week),
            "week": week,
            "keyword": keyword,
            "score": member["score"],
            "total_search_volume": member["total_count"],
            "competition": member["competition"],
            "approved": False,
            "published": False,
            "duplicate_of_past_approval": prev_week is not None,
            "duplicate_of_past_approval_week": prev_week,
            "cluster_seed": cluster["seed"],
            "cluster_role": "대표" if member is cluster["head"] else "세부",
            "target_platform": None,  # 이번 주 계획용 표시일 뿐, 다른 플랫폼도 나중에 얼마든지 가능
        }
        if category:
            entry["category"] = category  # 롱텀: 워드프레스 카테고리명 (주간 승인 화면 클러스터 헤더 태그용)
        if member.get("signals") is not None:  # 점수 보정 근거 (신호 보정을 안 쓴 큐 항목에는 이 키가 붙지 않음)
            entry["base_score"] = member.get("base_score")
            entry["signals"] = member["signals"]
        if member.get("news_evidence") is not None:  # 롱텀: 이 키워드가 최근 기사에서 나온 근거
            entry["news_evidence"] = member["news_evidence"]
        entries.append(entry)
    return entries


def build_weekly_queue(
    top_n: int,
    week: str,
    exclude_keywords: set = None,
    approved_history: dict = None,
    duplicate_cooldown_weeks: int = 8,
    calendar_path: str = "seasonal_calendar.json",
    intent_keywords: list[str] = None,
    child_suffix_candidates: list[str] = None,
    always_autocomplete_expand: bool = False,
    heads_only: bool = False,
    signal_enricher=None,
    children_builder=None,
    longterm_opts: dict = None,
) -> list[dict]:
    """
    이번 주 기준으로 top_n개(app.py의 WEEKLY_QUEUE_SIZE 값)의 키워드 후보를 만듭니다.

    대표 키워드(필러, 예: "에너지바우처")와 그 하위 키워드(구체적 의도가 담긴 롱테일,
    예: "에너지바우처 신청방법")를 같은 클러스터로 묶어서, 점수가 높은 클러스터부터
    순서대로 채웁니다. 대시보드에는 묶음으로 보이지만, 승인/발행 체크는 항목별로 개별입니다.

    - exclude_keywords: 이번 주에 이미 승인되어 유지 중인 키워드. 이 목록에 있으면 새로 안 만듭니다.
    - approved_history: {키워드: 가장 최근 승인했던 주차} 형태의 전체 기간 승인 이력.
    - duplicate_cooldown_weeks: 과거에 승인했던 키워드라도, 이 주 수보다 더 오래 전이면 다시 후보로
      보여줍니다(그 사이 제도가 바뀌었을 수 있어 "새로 갱신해서 쓸 가치"가 있다고 보는 것). 이
      기간 안에 이미 썼던 키워드는 애초에 후보에서 제외합니다 — 예전에는 제외하지 않고 그냥
      "⚠ 이전 승인" 표시만 남겼었는데, 그러면 네이버가 돌려주는 관련 키워드 자체가 주마다 크게
      안 바뀌는 특성상 매주 거의 같은 키워드로 60개가 채워지는 문제가 있었습니다.
    - calendar_path / intent_keywords / child_suffix_candidates: 쇼핑 채널처럼 다른 시드
      캘린더·다른 검색의도 판단 기준을 쓰고 싶을 때 넘깁니다 (기본값은 정책/제도 콘텐츠용).
    - always_autocomplete_expand: True면 이미 충분한 클러스터도 자동완성으로 한 번 더
      확인합니다(쇼핑 채널의 "지금 뜨는 구체적 상품" 발굴용).
    - heads_only: True면 세부 키워드를 아예 자동으로 안 만듭니다(백필·자동완성 확장 전부
      건너뜀) — 대표 키워드만 보여주고, 세부는 사람이 직접 판단해서 추가하는 게 더 나은
      경우(쇼핑 콘텐츠처럼 특정 신제품 여부를 사람이 확인해야 하는 경우)에 씁니다.
    - signal_enricher: 클러스터 목록을 받아 대표 키워드 점수를 보정해서 돌려주는 함수(숍 채널의
      트렌드·뉴스·쇼핑인사이트 보정, signal_enricher.py). None이면(롱텀 기본값) 아무것도 안 합니다.
    - children_builder: (clusters, scored_items)를 받아 각 클러스터의 세부 키워드(children)를 채워서
      돌려주는 함수(숍 채널의 세부 키워드 상위 3개 자동 선정, shop_children.py). None이면 아무것도 안 합니다.
    - longterm_opts: 롱텀 전용 다양성·품질 규칙 묶음 (숍도 씀: quality·max_children만 빼고 전부 — 숍 세부 키워드 품질 기준은 shop_children 그대로). 키:
        month(시드를 고를 월), seeds(이미 고른 시드 목록), quality(세부 키워드 품질 기준), max_children(세부 상한),
        longest_match(가장 긴 시드로 귀속), category_map({시드: 카테고리}), min_clusters_per_category,
        max_cluster_slots(한 클러스터가 차지할 최대 칸), existing_seed_counts({시드: 이번 주 이미 승인돼 유지 중인 개수})
    """
    exclude_keywords = exclude_keywords or set()
    approved_history = approved_history or {}
    today = datetime.now()
    opts = longterm_opts or {}
    quality = opts.get("quality")
    cap = opts.get("max_children")
    longest = bool(opts.get("longest_match"))
    LAST_RUN_INFO.clear()

    scored_items = _get_scored_items(
        opts.get("month") or today.month,
        week=week,
        calendar_path=calendar_path,
        intent_keywords=intent_keywords,
        seeds=opts.get("seeds"),
        longest_match=longest,
        quality=quality,
    )
    clusters = build_clusters(scored_items, heads_only=heads_only, quality=quality, max_children=cap)
    LAST_RUN_INFO["built"] = {c["seed"]: (c["head"]["total_count"] if c["head"] else None) for c in clusters}
    if not heads_only:
        clusters = _backfill_underfilled_children(
            clusters,
            week,
            intent_keywords=intent_keywords,
            child_suffix_candidates=child_suffix_candidates,
            quality=quality,
            max_children=cap,
            longest_match=longest,
        )
        if always_autocomplete_expand:
            clusters = _autocomplete_expand_all_clusters(clusters, week, intent_keywords=intent_keywords)
    if children_builder is not None:
        clusters = children_builder(clusters, scored_items)
    if cap is not None:
        clusters = _trim_children(clusters, cap, quality)  # 뉴스 보장 1자리까지 포함해서 세부 cap개
    if signal_enricher is not None:
        clusters = signal_enricher(clusters)

    category_map = opts.get("category_map")
    seed_counts = opts.get("existing_seed_counts") or {}
    slot_max = opts.get("max_cluster_slots")

    def category_of(cluster):
        return category_map.get(cluster["seed"]) if category_map else None

    if category_map is None:
        # 숍(그리고 롱텀 옵션이 없는 호출): 예전과 똑같이 점수순 클러스터를 top_n칸까지 채웁니다.
        queue = []
        for cluster in clusters:
            if len(queue) >= top_n:
                break
            entries = _cluster_entries(cluster, week, exclude_keywords, approved_history, duplicate_cooldown_weeks)
            queue.extend(entries[: top_n - len(queue)])
        return queue

    # 롱텀: ① 한 클러스터 칸 수 제한 ② 카테고리별 최소 클러스터 먼저 확보 ③ 나머지를 점수순으로 채움
    entries_by = {}
    for cluster in clusters:
        remaining_slots = (slot_max - seed_counts.get(cluster["seed"], 0)) if slot_max else None
        if remaining_slots is not None and remaining_slots <= 0:
            continue  # 이번 주 이미 승인돼 유지 중인 항목만으로 이 시드의 칸이 다 찼음
        entries = _cluster_entries(
            cluster, week, exclude_keywords, approved_history, duplicate_cooldown_weeks,
            category=category_of(cluster), slot_cap=remaining_slots,
        )
        if entries:
            entries_by[id(cluster)] = entries
    usable = [c for c in clusters if id(c) in entries_by]  # 이미 점수순

    guaranteed = set()
    for cat in opts.get("categories", CATEGORIES):  # 롱텀은 키를 안 넘겨서 CATEGORIES 그대로, 숍은 SHOP_CATEGORIES
        in_cat = [c for c in usable if category_of(c) == cat]
        guaranteed.update(id(c) for c in in_cat[: opts.get("min_clusters_per_category", MIN_CLUSTERS_PER_CATEGORY)])
    sequence = [c for c in usable if id(c) in guaranteed] + [c for c in usable if id(c) not in guaranteed]

    picked, total = [], 0
    for cluster in sequence:
        if total >= top_n:
            break
        take = entries_by[id(cluster)][: top_n - total]
        picked.append((cluster, take))
        total += len(take)
    picked.sort(key=lambda pair: pair[0]["cluster_score"], reverse=True)  # 화면에서는 클러스터 점수순으로 보여줍니다

    LAST_RUN_INFO["clusters"] = [
        {
            "seed": c["seed"],
            "category": category_of(c),
            "guaranteed": id(c) in guaranteed,
            "cluster_score": c["cluster_score"],
            "head_volume": c["head"]["total_count"] if c.get("head") else None,
            "children": [ch["keyword"] for ch in c["children"]],
            "count_in_queue": len(take),
        }
        for c, take in picked
    ]
    LAST_RUN_INFO["clusters_not_picked"] = [c["seed"] for c in usable if id(c) not in {id(p[0]) for p in picked}]
    LAST_RUN_INFO["clusters_without_entries"] = [c["seed"] for c in clusters if id(c) not in entries_by]
    return [entry for _, take in picked for entry in take]


def _longterm_enrich(clusters: list[dict]) -> list[dict]:
    """
    롱텀 대표 키워드 점수 보정 (기본 × 트렌드 × 뉴스, 쇼핑 배수 없음). 롱텀은 세부 키워드 점수가 클러스터
    점수에 더해지므로(_cluster_score), 보정 뒤에 모든 클러스터의 점수를 다시 계산하고 재정렬합니다.
    HUB 키가 없으면(목 모드) 가짜 트렌드·뉴스로 점수가 바뀌지 않도록 아무것도 안 합니다.
    """
    if not config.NAVER_HUB_CONFIGURED:
        return clusters
    clusters = signal_enricher.enrich_clusters(clusters, include_shop=False)
    for c in clusters:
        c["cluster_score"] = _cluster_score(c["head"], c["children"])
    clusters.sort(key=lambda c: c["cluster_score"], reverse=True)
    return clusters


def build_longterm_weekly_queue(
    top_n: int,
    week: str,
    exclude_keywords: set = None,
    approved_history: dict = None,
    duplicate_cooldown_weeks: int = 8,
    seed_last_used: dict = None,
    existing_seed_counts: dict = None,
    calendar_path: str = "seasonal_calendar.json",
) -> list[dict]:
    """
    롱텀(정책) 주간 큐. build_weekly_queue의 기본 동작(시드 캘린더, 클러스터, 세부 키워드 백필)에 더해:
      1) 시드별 최신 뉴스에서 "지금 기사화되는 각도"를 세부 키워드 후보로 뽑아 실제 검색량을 확인한 것 중
         1개를 클러스터마다 보장 (longterm_news.py)
      2) 대표 키워드 점수를 트렌드·뉴스로 보정 (쇼핑인사이트는 쓰지 않음)
      3) 다양성: 상시 시드를 카테고리별로 돌려 뽑고(select_longterm_seeds), 클러스터당 세부 LONGTERM_MAX_CHILDREN개,
         카테고리별 MIN_CLUSTERS_PER_CATEGORY개 클러스터를 먼저 확보한 뒤 나머지를 점수순으로 채움, 한 클러스터 최대 MAX_CLUSTER_SLOTS칸
      4) 품질: 세부 키워드는 검색량 MIN_CHILD_VOLUME_LONGTERM 미만·커뮤니티/플랫폼 이름 포함이면 제외, 연도만 붙은 것도 같은 검색량 기준
    숍의 build_shopping_weekly_queue와 대칭입니다(숍은 이 규칙들을 하나도 쓰지 않음). HUB 키가 없으면 뉴스·점수보정은 아무것도 안 합니다.

    seed_last_used: {시드: 마지막으로 승인·글 생성된 주차} (이번 주보다 이전 주차만), existing_seed_counts: {시드: 이번 주에 이미
    승인돼 유지 중인 항목 수} — 둘 다 app.py가 큐/글 데이터로 만들어 넘깁니다. 이번 주 시드 선택 결과는 LAST_RUN_INFO["seed_selection"]에 남습니다.
    """
    calendar = load_seasonal_calendar(calendar_path)
    month = datetime.strptime(week, "%Y-%m-%d").month  # 그 주(월요일 기준)의 달 — 지난/다음 주를 미리 불러와도 그 달 시드를 씁니다
    selection = select_longterm_seeds(calendar, month, week, seed_last_used)
    quality = _longterm_quality()

    queue = build_weekly_queue(
        top_n=top_n,
        week=week,
        exclude_keywords=exclude_keywords,
        approved_history=approved_history,
        duplicate_cooldown_weeks=duplicate_cooldown_weeks,
        children_builder=functools.partial(
            longterm_news.attach_news_children,
            week=week,
            max_children=LONGTERM_MAX_CHILDREN,
            child_ok=lambda stat: _child_ok(stat, quality),
            min_volume=MIN_CHILD_VOLUME_LONGTERM,
        ),
        signal_enricher=_longterm_enrich,
        longterm_opts={
            "month": month,
            "seeds": selection["seeds"],
            "quality": quality,
            "max_children": LONGTERM_MAX_CHILDREN,
            "longest_match": True,
            "category_map": seed_category_map(calendar),
            "min_clusters_per_category": MIN_CLUSTERS_PER_CATEGORY,
            "max_cluster_slots": MAX_CLUSTER_SLOTS,
            "existing_seed_counts": existing_seed_counts or {},
        },
    )
    LAST_RUN_INFO["seed_selection"] = {**selection, "week": week, "month": month}
    return queue


def build_shopping_weekly_queue(
    top_n: int,
    week: str,
    exclude_keywords: set = None,
    approved_history: dict = None,
    duplicate_cooldown_weeks: int = 8,
    seed_last_used: dict = None,
    existing_seed_counts: dict = None,
) -> list[dict]:
    """
    build_weekly_queue()와 완전히 같은 엔진을 쓰지만, 시드는 shopping_calendar.json(이 달에
    잘 팔리는 상품 카테고리)에서, 검색의도 판단은 SHOP_INTENT_KEYWORDS(추천·순위·비교 등
    쇼핑 특유의 표현)로 가져옵니다. 정책 콘텐츠 큐와 완전히 같은 방식(클러스터링, 실제
    검색량 검증)으로 동작하되, 쇼핑 콘텐츠에 맞는 시드·의도 판단만 갈아 끼운 것입니다.

    시드는 월별 시드(그 주가 속한 달 + 인접 달) + 상시(evergreen) 시드를 카테고리별 SHOP_EVERGREEN_PER_CATEGORY개씩
    돌려 뽑은 것(select_longterm_seeds 재사용)입니다. seed_last_used는 롱텀과 같은 형식이고, 선택 결과는
    LAST_RUN_INFO["seed_selection"]에 남습니다.
    대기열은 롱텀처럼 카테고리(SHOP_CATEGORIES)별로 묶음 SHOP_MIN_CLUSTERS_PER_CATEGORY개를 먼저 확보하고, 묶음당
    최대 SHOP_MAX_CLUSTER_SLOTS칸으로 제한한 뒤 나머지를 점수순으로 채웁니다. existing_seed_counts는 롱텀과 같은 의미입니다.
    """
    calendar = load_seasonal_calendar("shopping_calendar.json")
    month = datetime.strptime(week, "%Y-%m-%d").month  # 그 주(월요일 기준)의 달
    selection = select_longterm_seeds(
        calendar, month, week, seed_last_used,
        per_category=SHOP_EVERGREEN_PER_CATEGORY, categories=SHOP_CATEGORIES,
    )
    queue = build_weekly_queue(
        top_n=top_n,
        week=week,
        exclude_keywords=exclude_keywords,
        approved_history=approved_history,
        duplicate_cooldown_weeks=duplicate_cooldown_weeks,
        calendar_path="shopping_calendar.json",
        intent_keywords=scorer.SHOP_INTENT_KEYWORDS,
        child_suffix_candidates=SHOP_CHILD_SUFFIX_CANDIDATES,
        heads_only=True,
        signal_enricher=signal_enricher.enrich_clusters,
        children_builder=functools.partial(shop_children.attach_children, month=month),
        longterm_opts={
            "month": month,
            "seeds": selection["seeds"],
            "longest_match": True,
            "category_map": seed_category_map(calendar),
            "categories": SHOP_CATEGORIES,
            "min_clusters_per_category": SHOP_MIN_CLUSTERS_PER_CATEGORY,
            "max_cluster_slots": SHOP_MAX_CLUSTER_SLOTS,
            "existing_seed_counts": existing_seed_counts or {},
        },
    )
    LAST_RUN_INFO["seed_selection"] = {**selection, "week": week, "month": month}
    return queue


def get_trending_news_for_seed(seed: str, count: int = 5) -> list[dict]:
    """
    이 대표 키워드(카테고리)와 관련해서 최근 어떤 게 기사화되고 있는지 보여줍니다.
    자동으로 세부 키워드를 만들어주는 게 아니라, 사람이 "이 중에 뭘 다룰지" 판단할 때
    참고할 실제 뉴스 목록을 주는 용도입니다 (세부 키워드는 여전히 직접 추가하는 게
    원칙 — 이건 그 판단을 돕는 참고자료입니다).
    """
    try:
        news = naver_hub_api.search_news(f"{seed} 신제품", display=count)
    except Exception:
        return []
    return news


def main():
    parser = argparse.ArgumentParser(description="키워드/시즌 캘린더 엔진")
    parser.add_argument("--month", type=int, default=None, help="테스트용: 특정 월 지정 (1-12)")
    parser.add_argument("--top", type=int, default=10, help="상위 몇 개를 뽑을지")
    parser.add_argument("--days", type=int, default=7, help="며칠치 콘텐츠 큐를 만들지")
    args = parser.parse_args()

    mode_label = "[목(mock) 모드 — 실제 API 키가 설정되지 않아 가짜 데이터 사용 중]" if config.USE_MOCK else "[실제 네이버 API 연동 모드]"
    print(mode_label)
    print("-" * 60)

    if args.month:
        ranked = build_ranked_list(args.month)
        print(f"{args.month}월 기준 상위 {args.top}개 키워드:\n")
        for i, item in enumerate(ranked[: args.top], 1):
            print(f"{i:2d}. {item['keyword']:<25} 점수:{item['score']:>7}  "
                  f"검색량:{item['total_count']:>6}  경쟁도:{item['competition']}")
    else:
        week = get_week_label()
        queue = build_weekly_queue(top_n=args.top, week=week)
        print(f"이번 주({week}) 콘텐츠 큐 {len(queue)}개:\n")
        for item in queue:
            print(f"{item['keyword']:<25} "
                  f"(점수 {item['score']}, 검색량 {item['total_search_volume']}, "
                  f"경쟁도 {item['competition']})")

        print("\n※ 이 실행은 파일을 저장하지 않습니다. 대기열은 앱의 '불러오기' 버튼으로만 바뀝니다.")


if __name__ == "__main__":
    main()
