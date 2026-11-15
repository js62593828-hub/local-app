"""
네이버 검색광고 API 클라이언트
- 실제 키가 설정되어 있으면 진짜 API를 호출합니다.
- 키가 없으면(목 모드) 그럴듯한 가짜 데이터를 돌려줘서, 로직 테스트를 API 키 없이도 할 수 있게 합니다.

공식 문서 기준 인증 방식:
  헤더에 X-Timestamp, X-API-KEY, X-Customer, X-Signature 를 담아 보냅니다.
  서명(X-Signature)은 "{timestamp}.{method}.{uri}" 문자열을 비밀키로 HMAC-SHA256 서명한 뒤 base64 인코딩한 값입니다.
"""
import base64
import hashlib
import hmac
import random
import time

import requests

import config


def _generate_signature(timestamp: str, method: str, uri: str, secret_key: str) -> str:
    message = f"{timestamp}.{method}.{uri}"
    signing_key = bytes(secret_key, "utf-8")
    hashed = hmac.new(signing_key, bytes(message, "utf-8"), hashlib.sha256)
    return base64.b64encode(hashed.digest()).decode()


def _headers(method: str, uri: str) -> dict:
    timestamp = str(round(time.time() * 1000))
    signature = _generate_signature(timestamp, method, uri, config.NAVER_SECRET_KEY)
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": timestamp,
        "X-API-KEY": config.NAVER_API_KEY,
        "X-Customer": config.NAVER_CUSTOMER_ID,
        "X-Signature": signature,
    }


def get_keyword_stats(seed_keywords: list[str], variation_key: str = None, expand: bool = True) -> list[dict]:
    """
    시드 키워드 리스트를 네이버 검색광고 키워드도구 API(RelKwdStat)에 넣어
    관련 키워드 + PC/모바일 월간 검색량 + 경쟁정도를 받아옵니다.

    범위를 넓히기 위해 실제 API 모드에서는 1차로 받은 관련 키워드 중 일부를
    다시 시드로 넣어 2차 조회를 한 번 더 하고 합쳐서 반환합니다(2-hop 확장).

    variation_key: 보통 주차 라벨(예: "2026-08-24")을 넘겨줍니다.
      목(mock) 모드에서 결과가 매주 달라지게 하는 용도로만 쓰입니다.
      None으로 두면(예: --month 테스트) 예전처럼 고정된 결과를 재현합니다.

    expand: False로 두면 2차 확장(1차 결과 상위 키워드를 다시 시드로 넣는 추가 조회)을 건너뜁니다.
      "이 키워드들의 검색량만" 확인하고 싶을 때(예: 뉴스에서 뽑은 후보 검증) 호출 수를 줄이는 용도입니다.

    반환 형식 (실제/목 모드 동일):
    [
        {
            "keyword": "청년도약계좌 신청",
            "pc_count": 1200,
            "mobile_count": 8400,
            "total_count": 9600,
            "competition": "낮음" | "중간" | "높음",
        },
        ...
    ]
    """
    if config.USE_MOCK:
        return _mock_keyword_stats(seed_keywords, variation_key)

    # 네이버 API 제약: hintKeywords의 각 키워드에는 공백이 들어가면 "파라미터가 유효하지 않다"며
    # 요청 전체가 거부됩니다(에러코드 11001). seasonal_calendar.json에는 사람이 읽기 편하도록
    # "종합소득세 신고"처럼 띄어쓰기를 넣어둔 시드가 있으므로, API로 보내기 직전에만 공백을 제거합니다.
    seed_keywords = [kw.replace(" ", "") for kw in seed_keywords if kw and kw.strip()]

    uri = "/keywordstool"
    method = "GET"
    MAX_HINT_KEYWORDS = 5  # 네이버 키워드도구 API 제한: hintKeywords는 한 번에 최대 5개까지

    def _call(keywords: list[str], _retry: bool = True) -> list[dict]:
        params = {"hintKeywords": ",".join(keywords), "showDetail": "1"}
        headers = _headers(method, uri)
        response = requests.get(
            config.NAVER_API_BASE_URL + uri, params=params, headers=headers, timeout=10
        )
        if not response.ok:
            # 네이버가 실제로 보낸 오류 사유(예: 인증 실패, 서명 오류 등)를 그대로 보여줍니다.
            # raise_for_status()의 기본 메시지만으로는 원인을 알 수 없어서 이렇게 바꿨습니다.
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise requests.HTTPError(
                f"네이버 API {response.status_code} 오류 (요청 키워드: {', '.join(keywords)})\n"
                f"네이버 응답 내용: {detail}",
                response=response,
            )

        # 상태코드는 정상(200)인데 본문이 비어있거나 JSON이 아닌 경우가 가끔 있습니다
        # (네이버 쪽 순간적인 응답 지연·네트워크 문제로 추정). 한 번은 자동으로 재시도하고,
        # 그래도 안 되면 원인을 명확히 알 수 있는 메시지로 실패시킵니다.
        try:
            data = response.json()
        except ValueError:
            if _retry:
                time.sleep(0.5)
                return _call(keywords, _retry=False)
            raise requests.HTTPError(
                f"네이버 API가 빈 응답(또는 JSON이 아닌 응답)을 반환했습니다 (요청 키워드: {', '.join(keywords)}).\n"
                f"응답 상태코드: {response.status_code}, 본문 길이: {len(response.text)}자.\n"
                f"보통 일시적인 네트워크 문제입니다 — 잠시 후 '키워드 새로 불러오기'를 다시 눌러보세요.",
                response=response,
            )

        parsed = []
        for item in data.get("keywordList", []):
            pc = _to_int(item.get("monthlyPcQcCnt"))
            mobile = _to_int(item.get("monthlyMobileQcCnt"))
            parsed.append(
                {
                    "keyword": item.get("relKeyword"),
                    "pc_count": pc,
                    "mobile_count": mobile,
                    "total_count": pc + mobile,
                    "competition": item.get("compIdx", "중간"),
                }
            )
        return parsed

    def _call_in_batches(keywords: list[str]) -> list[dict]:
        """hintKeywords 5개 제한 때문에, 시드가 5개를 넘으면 5개씩 나눠서 호출 후 합칩니다."""
        merged = []
        seen_kw = set()
        for i in range(0, len(keywords), MAX_HINT_KEYWORDS):
            chunk = keywords[i : i + MAX_HINT_KEYWORDS]
            for item in _call(chunk):
                if item["keyword"] in seen_kw:
                    continue
                seen_kw.add(item["keyword"])
                merged.append(item)
            if i + MAX_HINT_KEYWORDS < len(keywords):
                time.sleep(0.2)  # 배치 호출 간 최소 간격
        return merged

    results = _call_in_batches(seed_keywords)
    seen = {r["keyword"] for r in results}

    # 2차 확장: 1차 결과 중 검색량 상위 키워드 일부를 새 시드로 삼아 한 번 더 조회
    # (같은 시드만 계속 쓰면 주마다 결과가 거의 똑같아지는 문제를 완화)
    second_level_seeds = [
        r["keyword"].replace(" ", "")
        for r in sorted(results, key=lambda x: x["total_count"], reverse=True)
        if r["keyword"] not in seed_keywords
    ][:MAX_HINT_KEYWORDS]

    if expand and second_level_seeds:
        try:
            time.sleep(0.2)  # API 호출 간 최소 간격
            extra = _call(second_level_seeds)
            for item in extra:
                if item["keyword"] in seen:
                    continue
                seen.add(item["keyword"])
                results.append(item)
        except requests.RequestException:
            pass  # 2차 확장은 실패해도 1차 결과만으로 계속 진행

    return results


def get_related_keywords(seed: str) -> list[dict]:
    """
    시드 하나만으로 키워드도구를 한 번 조회해서 관련 키워드 전체(get_keyword_stats와 같은 형식)를
    돌려줍니다. get_keyword_stats는 여러 시드를 묶어 조회하고 2차 확장까지 해서 "이 키워드가 어느
    시드에서 나왔는지"를 알 수 없는데, 이 함수는 시드 하나당 한 번만 부르므로 결과 전부가 그 시드의
    관련 키워드입니다 (시드를 포함하지 않는 연관 제품·유형까지 포함). 목 모드에서는 빈 목록.
    """
    if config.USE_MOCK:
        return []
    uri = "/keywordstool"
    response = requests.get(
        config.NAVER_API_BASE_URL + uri,
        params={"hintKeywords": seed.replace(" ", ""), "showDetail": "1"},
        headers=_headers("GET", uri),
        timeout=10,
    )
    response.raise_for_status()
    parsed = []
    for item in response.json().get("keywordList", []):
        pc = _to_int(item.get("monthlyPcQcCnt"))
        mobile = _to_int(item.get("monthlyMobileQcCnt"))
        parsed.append(
            {
                "keyword": item.get("relKeyword"),
                "pc_count": pc,
                "mobile_count": mobile,
                "total_count": pc + mobile,
                "competition": item.get("compIdx", "중간"),
            }
        )
    return parsed


def _to_int(value) -> int:
    """네이버 API는 검색량이 매우 적으면 '< 10' 같은 문자열을 반환하기도 해서 안전하게 변환합니다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 5  # '< 10' 등은 대략적인 낮은 값으로 처리


def _mock_keyword_stats(seed_keywords: list[str], variation_key: str = None) -> list[dict]:
    """
    API 키가 없을 때 사용하는 가짜 데이터 생성기 (테스트/개발용).

    variation_key가 주어지면 그 값으로 시드를 삼아서(예: 주차 라벨), 주차마다
    결과가 달라지게 합니다. variation_key가 없으면(--month 테스트 등) 42로 고정해서
    예전처럼 재현 가능한 결과를 냅니다.
    전역 random.seed() 대신 전용 random.Random() 인스턴스를 써서 다른 코드의
    난수 상태에 영향을 주지 않습니다.
    """
    rng = random.Random(variation_key if variation_key else 42)
    results = []
    suffixes = [
        "신청방법", "자격조건", "지원금액", "신청기간", "후기",
        "신청서류", "계산기", "조회방법", "지급일", "환급",
        "총정리", "비교", "최신정보", "한도",
    ]
    for seed in seed_keywords:
        for suffix in suffixes:
            keyword = f"{seed} {suffix}"
            pc = rng.randint(100, 3000)
            mobile = rng.randint(500, 15000)
            comp = rng.choice(["낮음", "중간", "높음"])
            results.append(
                {
                    "keyword": keyword,
                    "pc_count": pc,
                    "mobile_count": mobile,
                    "total_count": pc + mobile,
                    "competition": comp,
                }
            )
    return results
