"""
키워드 스코어링 로직
점수 = 검색량 점수 × 경쟁도 가중치 × 시즌 가중치

"구체적인 검색 의도가 있는지(has_search_intent)"는 점수 자체에는 반영하지 않고
표시만 해둡니다. 어떤 키워드를 "대표(필러)"로, 어떤 걸 "하위(롱테일)"로 쓸지는
main.py의 클러스터링 단계에서 이 표시를 보고 결정합니다.
"""
import math
import re

COMPETITION_WEIGHT = {
    "낮음": 1.2,   # 경쟁이 낮으면 가산점
    "중간": 1.0,
    "높음": 0.7,   # 경쟁이 높으면 감점 (초반엔 이기기 어려움)
}

# 검색 의도가 뚜렷한 키워드에 붙는 표현들. 이 중 하나라도 포함돼 있으면
# "이 사람이 정확히 뭘 알고 싶어서 검색했는지" 짐작할 수 있어서 하위(롱테일) 키워드로 씁니다.
# 예전엔 "신청조건"·"자격조건"처럼 복합 표현만 인정하고 "조건"·"금액"처럼 단순한 표현은
# 걸러내고 있었는데, 그러면 네이버가 실제로 관련 키워드를 줘도("~조건", "~금액", "~50만원")
# 필터에서 탈락해 하위 키워드가 텅 비는 클러스터가 생겼습니다. 단순 표현들과 숫자(구체적
# 금액·연도 등)도 검색 의도로 인정하도록 넓혔습니다.
INTENT_KEYWORDS = [
    "신청방법", "신청조건", "신청자격", "신청기간", "신청서류", "신청대상", "신청",
    "자격조건", "지원금액", "지원대상", "지급일", "지급시기", "지급방법", "지원금", "지원",
    "계산기", "계산법", "조회방법", "조회", "환급", "환급금",
    "총정리", "정리", "비교", "차이", "장단점", "단점", "장점",
    "후기", "순위", "추천", "안내", "가이드", "필요서류", "준비물",
    "한도", "한도액", "금리", "이자율", "혜택", "가입방법", "가입조건", "가입",
    "해지방법", "얼마", "언제", "누구", "최신정보",
    "조건", "대상", "기간", "금액", "방법", "비용", "가격", "확인", "기준", "자격",
]

# 쇼핑(쿠팡파트너스·쇼핑커넥트) 콘텐츠용 검색 의도 표현. 정책 콘텐츠와는 사람들이 검색하는
# 방식 자체가 달라서("신청방법"이 아니라 "추천"·"순위"·"가성비"를 찾음) 별도 목록을 씁니다.
SHOP_INTENT_KEYWORDS = [
    "추천", "순위", "베스트", "비교", "가성비", "저렴한", "저렴이", "인기", "후기",
    "리뷰", "브랜드", "종류", "차이", "고르는법", "선택법", "가이드", "필수템",
    "아이템", "용품", "세트", "선물", "가격", "가격대", "TOP", "top",
]


def volume_score(total_count: int) -> float:
    """
    검색량이 무작정 높다고 좋은 게 아니라(초반엔 경쟁도 세짐),
    로그 스케일로 완만하게 점수를 매깁니다.
    """
    if total_count <= 0:
        return 0.0
    return math.log10(total_count + 1) * 10


_YEAR_ONLY_RE = re.compile(r"^(19|20)\d{2}년?$")


def has_search_intent(
    keyword: str,
    seed: str = None,
    intent_keywords: list[str] = None,
    total_count: int = None,
    year_min_volume: int = None,
) -> bool:
    """
    이 키워드에 구체적인 검색 의도가 담겨 있는지 확인합니다. 시드(대표 키워드) 그 자체와
    완전히 같으면(수식어가 안 붙었으면) False. intent_keywords를 따로 안 넘기면 기존
    정책/제도 콘텐츠용 목록(INTENT_KEYWORDS)을 쓰고, 쇼핑 콘텐츠에서는 SHOP_INTENT_KEYWORDS를
    넘겨서 씁니다. 수식어에 표에 있는 표현이 있거나, 구체적인 숫자가 붙어 있으면(정책
    콘텐츠 한정) 검색 의도가 뚜렷한 것으로 봅니다.

    주의: 마커 검사는 "시드를 뺀 나머지(수식어)" 부분에서만 합니다. 전체 키워드에서 검사하면,
    시드 자체에 우연히 마커 단어가 들어있을 때(예: 시드 "추석선물세트" 안의 "선물"·"세트") 실제
    수식어가 전혀 다른 것("조회방법" 같은 정책 스타일)이어도 잘못 통과되는 문제가 있었습니다.

    year_min_volume(롱텀만 넘김): 수식어가 "2026"·"2026년"처럼 연도뿐인 경우, 검색량(total_count)이 이 값
    이상일 때만 검색 의도로 인정합니다. 연도만 붙은 키워드는 검색 의도가 뚜렷하지 않은데도 숫자라는 이유로
    통과해서 세부 키워드가 검색량 작은 "○○2026"으로 채워지는 걸 막기 위한 규칙입니다. 안 넘기면(None) 예전 그대로입니다.
    """
    markers = intent_keywords if intent_keywords is not None else INTENT_KEYWORDS
    check_text = keyword
    if seed:
        remainder = keyword.replace(seed.replace(" ", ""), "").strip()
        if not remainder:
            return False
        check_text = remainder
    if any(marker in check_text for marker in markers):
        return True
    if intent_keywords is None:
        # 숫자 기반 검색의도 인정은 정책/제도 콘텐츠에만 적용합니다 (쇼핑은 "2026" 같은
        # 모델명·연식 숫자가 워낙 흔해서 숫자만으로 의도가 뚜렷하다고 보기 어렵습니다).
        if year_min_volume is not None and _YEAR_ONLY_RE.match(check_text.replace(" ", "")):
            return (total_count or 0) >= year_min_volume
        return bool(re.search(r"\d", check_text))
    return False


def score_keyword(
    item: dict,
    seasonal_priority: int,
    seed: str = None,
    intent_keywords: list[str] = None,
    year_min_volume: int = None,
) -> dict:
    """
    item: naver_api.get_keyword_stats()가 반환하는 항목 하나
    seasonal_priority: 1~5, 이 키워드가 속한 시즌 캘린더 항목의 우선순위
    seed: 이 키워드가 어떤 시드에서 나왔는지 (검색 의도 판단용)
    intent_keywords: 검색 의도 판단에 쓸 표현 목록 (안 넘기면 정책 콘텐츠용 기본값)
    """
    v_score = volume_score(item["total_count"])
    comp_weight = COMPETITION_WEIGHT.get(item["competition"], 1.0)
    season_weight = 1 + (seasonal_priority / 5)  # 1.2 ~ 2.0 배

    final_score = round(v_score * comp_weight * season_weight, 2)

    return {
        **item,
        "seasonal_priority": seasonal_priority,
        "has_search_intent": has_search_intent(
            item["keyword"], seed, intent_keywords, total_count=item["total_count"], year_min_volume=year_min_volume
        ),
        "score": final_score,
    }


def rank_keywords(scored_items: list[dict]) -> list[dict]:
    return sorted(scored_items, key=lambda x: x["score"], reverse=True)
