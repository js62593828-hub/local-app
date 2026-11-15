"""
NAVER API HUB (네이버 클라우드 플랫폼) 클라이언트 — 뉴스검색 · 검색어트렌드.

기존 naver_api.py(검색광고 키워드도구, 개발자센터)와는 완전히 별개의 새 플랫폼입니다.
인증 헤더 이름도 다릅니다:
  - 옛 개발자센터: X-Naver-Client-Id / X-Naver-Client-Secret
  - NAVER API HUB: X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY

실제 키가 없으면(config.NAVER_HUB_CONFIGURED == False) 목(mock) 모드로 그럴듯한 가짜
데이터를 돌려줘서, 로직 테스트를 API 키 없이도 할 수 있게 합니다.
"""
import random
import re

import requests

import config

HUB_BASE_URL = "https://naverapihub.apigw.ntruss.com"


def _hub_headers() -> dict:
    return {
        "X-NCP-APIGW-API-KEY-ID": config.NAVER_HUB_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": config.NAVER_HUB_CLIENT_SECRET,
    }


def _strip_tags(text: str) -> str:
    return re.sub(r"</?b>", "", text or "")


def search_news(query: str, display: int = 20, sort: str = "date") -> list[dict]:
    """
    뉴스검색 API. 최근 뉴스 기사 제목·요약·발행일을 돌려줍니다. "이 카테고리에서 지금
    실제로 기사화되는 구체적인 상품명"을 찾는 용도로 씁니다 — 네이버 키워드도구·
    자동완성보다 훨씬 신뢰도 높은 "지금 이슈되는 상품" 탐지 방법입니다.
    """
    if not config.NAVER_HUB_CONFIGURED:
        return _mock_news(query, display)

    resp = requests.get(
        f"{HUB_BASE_URL}/search/v1/news",
        headers=_hub_headers(),
        params={"query": query, "display": display, "sort": sort, "format": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [
        {
            "title": _strip_tags(item.get("title", "")),
            "description": _strip_tags(item.get("description", "")),
            "pub_date": item.get("pubDate", ""),
            "link": item.get("link", ""),
        }
        for item in items
    ]


def get_search_trend(
    keyword_groups: list[dict], start_date: str, end_date: str, time_unit: str = "month"
) -> list[dict]:
    """
    검색어트렌드 API. 여러 키워드 그룹의 상대적 검색 관심도(구간 내 최댓값=100)를
    비교합니다. 절대 검색량은 안 주지만, "이 후보들 중 어느 게 더 관심도가 높은가"를
    비교하는 용도로는 충분합니다 — 뉴스검색으로 찾은 후보들을 최종적으로 추릴 때 씁니다.

    keyword_groups: [{"groupName": "아이폰18", "keywords": ["아이폰18"]}, ...] 최대 5개.
    반환: [{"group_name": "...", "data": [{"period": "2026-09", "ratio": 87.3}, ...]}, ...]
    """
    if not config.NAVER_HUB_CONFIGURED:
        return _mock_search_trend(keyword_groups)

    payload = {
        "startDate": start_date,
        "endDate": end_date,
        "timeUnit": time_unit,
        "keywordGroups": keyword_groups[:5],
    }
    resp = requests.post(
        f"{HUB_BASE_URL}/search-trend/v1/search",
        headers={**_hub_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return [
        {
            "group_name": r.get("title", ""),
            "data": [{"period": d.get("period", ""), "ratio": d.get("ratio", 0)} for d in r.get("data", [])],
        }
        for r in results
    ]


def get_shopping_category_trend(
    categories: list[dict], start_date: str, end_date: str, time_unit: str = "month"
) -> list[dict]:
    """
    쇼핑인사이트 "분야별 트렌드" API (POST /shopping/v1/categories). 네이버쇼핑의 분야(카테고리)별
    클릭 추이를 상대값(구간 내 최댓값=100)으로 돌려줍니다. 절대 클릭 수는 안 주고, 같은 요청에 넣은
    분야끼리만 비교됩니다. 요청 하나에 분야는 최대 3개까지입니다.

    categories: [{"name": "디지털/가전", "param": ["50000003"]}, ...]  (param은 네이버쇼핑 분야 코드 cid)
    반환: [{"group_name": "디지털/가전", "category": ["50000003"], "data": [{"period": "2026-08-01", "ratio": 65.9}, ...]}, ...]
    주의: 진행 중인 달·주의 값은 누적이 덜 된 상태라 실제보다 낮게 나옵니다 (쓰는 쪽에서 제외해야 함).
    """
    if not config.NAVER_HUB_CONFIGURED:
        return _mock_shopping_trend(categories)

    payload = {
        "startDate": start_date,
        "endDate": end_date,
        "timeUnit": time_unit,
        "category": categories[:3],
    }
    resp = requests.post(
        f"{HUB_BASE_URL}/shopping/v1/categories",
        headers={**_hub_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return [
        {
            "group_name": r.get("title", ""),
            "category": r.get("category", []),
            "data": [{"period": d.get("period", ""), "ratio": d.get("ratio", 0)} for d in r.get("data", [])],
        }
        for r in results
    ]


def average_ratio(trend_result: dict) -> float:
    """get_search_trend() 결과 하나(그룹 하나)의 기간별 ratio 평균을 냅니다 —
    후보들을 한 줄로 비교/정렬할 때 씁니다."""
    data = trend_result.get("data", [])
    if not data:
        return 0.0
    return round(sum(d["ratio"] for d in data) / len(data), 2)


# ---------- 목(mock) 모드 ----------

def _mock_news(query: str, display: int) -> list[dict]:
    templates = [
        f"{query} 신제품 출시, 소비자 반응은",
        f"{query} 인기 급상승... 판매량 급증",
        f"올해 {query} 트렌드 정리",
        f"{query} 후기 모아보니",
    ]
    return [
        {
            "title": random.choice(templates),
            "description": f"{query} 관련 목(mock) 기사 요약입니다.",
            "pub_date": "Mon, 01 Sep 2026 00:00:00 +0900",
            "link": "https://example.com/mock-news",
        }
        for _ in range(min(display, 5))
    ]


def _mock_shopping_trend(categories: list[dict]) -> list[dict]:
    return [
        {
            "group_name": c.get("name", ""),
            "category": c.get("param", []),
            "data": [{"period": "2026-09-01", "ratio": round(random.uniform(20, 100), 1)}],
        }
        for c in categories[:3]
    ]


def _mock_search_trend(keyword_groups: list[dict]) -> list[dict]:
    return [
        {
            "group_name": g["groupName"],
            "data": [{"period": "2026-09", "ratio": round(random.uniform(20, 100), 1)}],
        }
        for g in keyword_groups[:5]
    ]


# ---------- 쇼핑인사이트 "분야 내 키워드" 성별·연령 (타깃 키워드 발굴용) ----------

def _keyword_group_ratios(path: str, category: str, keyword: str, start_date: str, end_date: str,
                          device: str = "", extra: dict | None = None) -> list[dict]:
    """/shopping/v1/category/keyword/{gender|age} 공통 호출. 반환: [{"period","group","ratio"}, ...]
    category는 분야 코드(cid) 하나, keyword는 한 번에 하나만 보냅니다. device는 ""(전체)/"pc"/"mo"."""
    payload = {
        "startDate": start_date,
        "endDate": end_date,
        "timeUnit": "month",
        "category": category,
        "keyword": keyword,
    }
    if device:
        payload["device"] = device
    if extra:
        payload.update(extra)
    resp = requests.post(
        f"{HUB_BASE_URL}{path}",
        headers={**_hub_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    rows = []
    for r in resp.json().get("results", []):
        for d in r.get("data", []):
            rows.append({"period": d.get("period", ""), "group": str(d.get("group", "")), "ratio": d.get("ratio", 0) or 0})
    return rows


def get_keyword_gender_ratio(category: str, keyword: str, start_date: str, end_date: str, device: str = "") -> dict:
    """분야 안에서 키워드 하나의 성별 클릭 비율. 반환: {"male_pct": 0~100 또는 None, "mock": bool}.
    API가 주는 값은 기간별 남/여 상대값이라, 기간 전체를 합산해 남성/(남+여)로 계산합니다. 키가 없으면 목."""
    if not config.NAVER_HUB_CONFIGURED:
        rng = random.Random(f"g{category}{keyword}")
        return {"male_pct": round(rng.uniform(15, 85), 1), "mock": True}
    rows = _keyword_group_ratios("/shopping/v1/category/keyword/gender", category, keyword, start_date, end_date, device)
    male = sum(r["ratio"] for r in rows if r["group"] == "m")
    female = sum(r["ratio"] for r in rows if r["group"] == "f")
    total = male + female
    return {"male_pct": round(male / total * 100, 1) if total else None, "mock": False}


def get_keyword_age_ratio(category: str, keyword: str, start_date: str, end_date: str, device: str = "") -> dict:
    """분야 안에서 키워드 하나의 연령대별 클릭 비율. 반환: {"age_pct": {"10":%, "20":%, ...}, "mock": bool}
    (모든 연령대를 합쳐 100%). 선택 연령 비율은 쓰는 쪽에서 합산합니다. 키가 없으면 목."""
    ages = ["10", "20", "30", "40", "50", "60"]
    if not config.NAVER_HUB_CONFIGURED:
        rng = random.Random(f"a{category}{keyword}")
        raw = [rng.random() for _ in ages]
        s = sum(raw)
        return {"age_pct": {a: round(v / s * 100, 1) for a, v in zip(ages, raw)}, "mock": True}
    rows = _keyword_group_ratios("/shopping/v1/category/keyword/age", category, keyword, start_date, end_date, device)
    sums = {a: 0.0 for a in ages}
    for r in rows:
        if r["group"] in sums:
            sums[r["group"]] += r["ratio"]
    total = sum(sums.values())
    return {"age_pct": {a: round(v / total * 100, 1) for a, v in sums.items()} if total else {}, "mock": False}
