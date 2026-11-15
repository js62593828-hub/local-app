"""
숍 채널 세부 키워드 자동 선정 — 대표 키워드(시드)마다 상위 3개.

후보는 두 종류입니다:
  A) 시드를 포함하는 변형 (삼성노트북, 게이밍노트북, 노트북추천 …) — 주간 불러오기에서 이미 받아온 결과 재사용.
  B) 시드를 포함하지 않는 연관 제품·유형 (맥북, 갤럭시북, 캠핑의자 …) — 시드 하나만으로 키워드도구를 다시
     조회해서 가져옵니다. 이 목록에는 "리클라이너쇼파·빙수기계" 같은 엉뚱한 것도 섞여 있어서(API 순서가 관련도순이
     아님), 해당 시드의 최근 뉴스 100건 제목·요약에 실제로 등장하는 것만 통과시킵니다. 뉴스가 적어서(30건 미만)
     이 방법을 못 쓰는 시드는 API 응답 앞쪽 40개만 후보로 씁니다.
공통으로 중고·리퍼·수리·렌탈·산업용처럼 신제품 추천 글에 안 맞는 표현과 검색량 MIN_CHILD_VOLUME 미만은 뺍니다.
남은 후보 중 A(시드 글자 포함)를 먼저, 그 안에서 기본 점수(scorer.py 공식, 시드의 시즌 우선순위 적용)순으로 정렬하고
B는 A가 모자랄 때만 뒤에 붙여 상위 CHILDREN_PER_CLUSTER개를 씁니다. 그 주의 달과 반대 계절 단어(여름/겨울)가 든 후보는 뺍니다.
실제 제품명이 세부 키워드로 나와도 됩니다. 롱텀 채널은 이 모듈을 쓰지 않습니다 (main.build_shopping_weekly_queue만 사용).
"""
import time

import naver_api
import naver_hub_api
import scorer
import signal_enricher as se

CHILDREN_PER_CLUSTER = 3
MIN_CHILD_VOLUME = 300
MAX_CLUSTERS_TO_EXPAND = 24
NEWS_MIN_ARTICLES_FOR_GATE = 30
RELATED_TOP_WHEN_NEWS_SPARSE = 40
RELATED_CACHE_KEEP = 400   # 캐시에는 검색량 상위 이만큼만 저장 (파일이 너무 커지지 않게)

# 신제품 구매 추천 글에 안 맞는 표현 (중고·수리·렌탈·업소용 등)
EXCLUDE_WORDS = [
    "중고", "리퍼", "수리", "렌탈", "렌트", "설치", "무료", "청소업체", "청소대행", "입주청소", "이사청소", "청소알바", "청소업", "폐기", "매입", "도매", "산업용", "공업용",
    "공장용", "업소용", "당근", "파는곳", "판매점", "서비스센터", "고장", "교체", "포맷", "성인",
]


def _related(seed: str, cache: dict, errors: list) -> list[dict]:
    cached = se._cache_get(cache, f"rel:{seed}")
    if cached is not None:
        return cached
    try:
        rows = naver_api.get_related_keywords(seed)
    except Exception as e:  # noqa: BLE001
        errors.append(f"관련 키워드 조회 실패({seed}): {type(e).__name__} {e}"[:160])
        return []
    # API 응답 순서를 rank로 남겨서(앞쪽 40개 대체 기준용), 검색량 상위만 저장
    for i, r in enumerate(rows):
        r["api_rank"] = i
    rows = sorted(rows, key=lambda r: r["total_count"], reverse=True)[:RELATED_CACHE_KEEP]
    cache[f"rel:{seed}"] = {"ts": time.time(), "value": rows}
    return rows


def _news_text(seed: str, cache: dict, errors: list) -> tuple[str, int]:
    """시드의 최신 뉴스 100건 제목+요약을 이어붙인 문자열과 기사 수."""
    cached = se._cache_get(cache, f"newstext:{seed}")
    if cached is not None:
        return cached["text"], cached["n"]
    try:
        items = naver_hub_api.search_news(seed, display=100, sort="date")
    except Exception as e:  # noqa: BLE001
        errors.append(f"뉴스 조회 실패({seed}): {type(e).__name__} {e}"[:160])
        return "", 0
    text = " ".join(f"{i['title']} {i['description']}" for i in items).lower().replace(" ", "")
    value = {"text": text[:40000], "n": len(items)}
    cache[f"newstext:{seed}"] = {"ts": time.time(), "value": value}
    return value["text"], value["n"]


def _is_excluded(keyword: str) -> bool:
    return any(w in keyword for w in EXCLUDE_WORDS)


def _opposite_season_word(month: int | None) -> str | None:
    """그 주의 달에 맞지 않는 계절 단어 (겨울철이면 "여름", 여름철이면 "겨울"). 봄·가을(3·4·9월)은 없음."""
    if month in (10, 11, 12, 1, 2):
        return "여름"
    if month in (5, 6, 7, 8):
        return "겨울"
    return None


def attach_children(clusters: list[dict], scored_items: list[dict], month: int | None = None) -> list[dict]:
    """heads_only 클러스터마다 cluster["children"]에 세부 키워드(상위 CHILDREN_PER_CLUSTER개)를 채웁니다.
    시드 글자가 들어간 변형(A)을 먼저 점수순으로 쓰고, 시드가 안 들어간 연관 제품(B)은 A가 모자랄 때만 뒤에 붙입니다.
    month를 주면 반대 계절 단어가 들어간 세부 키워드는 뺍니다(대표 키워드는 그대로).
    어떤 실패도 주간 불러오기를 막지 않고, 실패 사유는 cluster["child_errors"]에 남깁니다."""
    off_season = _opposite_season_word(month)
    cache = se._load_cache()
    taken = {c["head"]["keyword"] for c in clusters if c.get("head")}
    taken |= {c["seed"].replace(" ", "") for c in clusters}
    # 대표·시드의 일부분인 후보("추석" ⊂ 추석선물세트, "캠핑" ⊂ 캠핑용품, "폰" ⊂ 스마트폰)는 세부가 아니라
    # 더 넓은 상위어라서 뺍니다.
    broader_than = set(taken)
    intent = scorer.SHOP_INTENT_KEYWORDS

    for idx, cluster in enumerate(clusters):
        head = cluster.get("head")
        if not head:
            continue
        seed = cluster["seed"].replace(" ", "")
        errors: list[str] = []
        candidates: dict[str, dict] = {}

        # A) 시드를 포함하는 변형 — 이미 받아온 결과 재사용
        for item in scored_items:
            if item["cluster_seed"] == cluster["seed"] and seed in item["keyword"]:
                candidates[item["keyword"]] = item

        # B) 시드를 포함하지 않는 연관 제품·유형 — 뉴스에 실제로 나오는 것만
        if idx < MAX_CLUSTERS_TO_EXPAND:
            related = _related(seed, cache, errors)
            text, n_articles = _news_text(seed, cache, errors)
            for row in related:
                kw = row["keyword"]
                if not kw or kw in candidates or seed in kw:
                    continue
                if n_articles >= NEWS_MIN_ARTICLES_FOR_GATE:
                    if kw.lower().replace(" ", "") not in text:
                        continue
                elif row.get("api_rank", 999) >= RELATED_TOP_WHEN_NEWS_SPARSE:
                    continue
                candidates[kw] = row
            time.sleep(0.1)

        priority = head.get("seasonal_priority", 3)
        pool = []
        for kw, row in candidates.items():
            if kw in taken or kw == head["keyword"] or any(kw in t for t in broader_than):
                continue
            if row["total_count"] < MIN_CHILD_VOLUME or _is_excluded(kw):
                continue
            if off_season and off_season in kw:
                continue
            scored = scorer.score_keyword(
                {k: row[k] for k in ("keyword", "pc_count", "mobile_count", "total_count", "competition")},
                priority, seed=cluster["seed"], intent_keywords=intent,
            )
            scored["cluster_seed"] = cluster["seed"]
            pool.append(scored)
        # A(시드 글자 포함) 먼저, 그 안에서 점수순 → B는 A가 모자랄 때만 뒤에
        pool.sort(key=lambda i: (seed not in i["keyword"], -i["score"]))
        cluster["children"] = pool[:CHILDREN_PER_CLUSTER]
        taken.update(c["keyword"] for c in cluster["children"])
        if errors:
            cluster["child_errors"] = errors
            for msg in errors:
                print(f"[세부 키워드 경고] {msg}")

    se._save_cache(cache)
    return clusters
