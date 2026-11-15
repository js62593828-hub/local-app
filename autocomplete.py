"""
구글/네이버 자동완성에서 "지금 실제로 사람들이 검색하는" 연관 검색어를 가져옵니다.

이 앱의 핵심은 "내가 정한 규칙"이 아니라 "실제 검색 행동"으로 키워드를 찾는 것이므로,
정해둔 수식어 목록을 기계적으로 붙이는 것보다 이 방식이 원래 목적에 훨씬 가깝습니다.
구글/네이버 둘 다 공식 문서가 있는 API는 아니고, 검색창에 자동완성을 띄워주는 데 쓰는
가벼운 엔드포인트를 그대로 이용하는 것입니다 (전체 검색결과 페이지를 긁어오는 것과 달리
요청 하나가 아주 가볍고, 여러 SEO 도구들이 널리 쓰는 방식입니다). 다만 공식 API가 아니라서
언젠가 형식이 바뀌거나 일시적으로 막힐 수 있습니다 — 그런 경우 조용히 빈 목록을 돌려주고
나머지 키워드 생성 파이프라인은 그대로 계속 진행되도록 만들었습니다 (이 부분이 실패해도
전체 "이번 주 키워드 불러오기"가 통째로 실패하면 안 되기 때문입니다).
"""
import time

import requests

TIMEOUT = 5


def google_autocomplete(query: str) -> list[str]:
    """구글 검색창에 이 검색어를 치면 뜨는 자동완성 목록을 그대로 가져옵니다."""
    try:
        resp = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "hl": "ko", "q": query},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return list(data[1]) if len(data) > 1 else []
    except Exception:
        return []


def naver_autocomplete(query: str) -> list[str]:
    """네이버 검색창에 이 검색어를 치면 뜨는 자동완성 목록을 그대로 가져옵니다."""
    try:
        resp = requests.get(
            "https://ac.search.naver.com/nx/ac",
            params={
                "q": query,
                "con": 0,
                "frm": "nv",
                "ans": 2,
                "r_format": "json",
                "r_enc": "UTF-8",
                "r_unicode": 0,
                "t_koreng": 1,
                "run": 2,
                "rev": 4,
                "q_enc": "UTF-8",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        suggestions = []
        for group in data.get("items", []):
            for entry in group:
                if entry and entry[0]:
                    suggestions.append(entry[0])
        return suggestions
    except Exception:
        return []


def gather_related_queries(seed: str) -> list[str]:
    """
    구글+네이버 자동완성을 합쳐서, 이 시드가 들어간 실제 연관 검색어 후보를 모읍니다.
    시드 그대로, 그리고 시드 뒤에 띄어쓰기를 붙인 것 두 가지로 물어봐서 조금 더 다양한
    자동완성이 나오도록 합니다 (검색엔진마다 "그대로"와 "뒤에 뭔가 더 칠 것 같을 때"의
    자동완성 결과가 다를 수 있기 때문입니다).
    """
    stripped = seed.replace(" ", "")
    raw: list[str] = []
    raw.extend(google_autocomplete(seed))
    time.sleep(0.15)
    raw.extend(naver_autocomplete(seed))
    time.sleep(0.15)
    raw.extend(google_autocomplete(f"{seed} "))
    time.sleep(0.15)
    raw.extend(naver_autocomplete(f"{seed} "))

    seen = set()
    results = []
    for kw in raw:
        kw = kw.strip()
        if not kw or kw in seen:
            continue
        # 시드와 관련 없는 엉뚱한 자동완성(자동완성이 다른 주제로 새는 경우가 가끔 있음)은 제외
        if stripped not in kw.replace(" ", ""):
            continue
        seen.add(kw)
        results.append(kw)
    return results
