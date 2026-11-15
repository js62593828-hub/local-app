"""
롱텀(정책) 채널 세부 키워드 — 뉴스 기반 발굴.

시드(대표 키워드)의 최신 뉴스 제목 100건에서 "지금 기사화되는 각도"를 뽑아 세부 키워드 후보로 만듭니다.
  예) 시드 "청년월세지원" + 뉴스 제목 "청년 월세 지원 2차 신청, 10월 1일부터"
      → 후보 "청년월세지원2차", "청년월세지원2차신청"
후보는 그냥 쓰지 않고 네이버 키워드도구로 **실제 검색량을 확인한 것만** 채택합니다. 채택된 뉴스 후보 중
1개는 클러스터마다 자리를 보장하고(세부 키워드가 이미 꽉 찼으면 점수가 가장 낮은 기존 것을 밀어냄),
나머지는 버려서 목록이 뉴스로 도배되지 않게 합니다. 기존 세부 키워드가 뉴스 구절과 일치하면 그 항목에도
"기사 N건" 근거를 붙입니다(추가 호출 없음).

main.build_longterm_weekly_queue만 씁니다 (숍 채널은 shop_children.py를 쓰며 이 모듈을 안 씁니다).
HUB 키가 없거나 검색광고 API가 목(mock) 모드면 가짜 데이터가 세부 키워드로 들어가지 않도록 아무것도 하지 않습니다.
"""
import html
import re
import time

import config
import naver_api
import naver_hub_api
import scorer
import signal_enricher as se

# ── 조절용 상수 ──────────────────────────────────────────────
NEWS_PHRASE_MIN_ARTICLES = 2     # 이 개수 이상의 기사에 나온 구절만 검증 대상
NEWS_PHRASE_TOP_N = 10           # 기사 수 상위 이만큼만 검색량을 확인 (키워드도구 호출량과 직결)
NEWS_CHILD_MIN_VOLUME = 30       # 월간 검색량이 이 이상이어야 채택
NEWS_FETCH = 100                 # 시드당 가져올 최신 기사 수
MAX_PHRASE_LEN = 30              # 후보 길이 상한 (공백 제거 후)

# 토큰 끝의 조사 (긴 것부터 검사)
PARTICLES = ["에서", "으로", "까지", "부터", "은", "는", "이", "가", "을", "를", "의", "에", "도", "로", "과", "와", "만"]
_PARTICLES_SORTED = sorted(PARTICLES, key=len, reverse=True)
STOPWORDS = {"관련", "등", "및", "위해", "위한", "대한", "대해", "통해", "따른", "따라", "이번", "올해", "오늘", "속보", "단독", "종합"}
DELIMITERS = ",.·…'\"‘’“”()[]<>:|/!?~-–—「」『』"

_DELIM_CLASS = re.escape(DELIMITERS)
_TOKEN_RE = rf"[^\s{_DELIM_CLASS}]+"


# ── 구절 추출 (순수 함수 — 오프라인 단위 테스트 가능) ───────────────────────
def _clean_token(token: str) -> str | None:
    """조사를 떼고, 1글자 이하거나 불용어면 None. ("을" 같은 조사만 남은 토큰도 None)
    단어 일부가 조사처럼 생긴 경우(물가·지도)는 조사를 떼면 1글자가 되므로 원형을 유지합니다."""
    if not token or token in PARTICLES:
        return None
    stripped = token
    for p in _PARTICLES_SORTED:
        if token.endswith(p):
            stripped = token[: -len(p)]
            break
    if len(stripped) < 2:
        stripped = token if len(token) >= 2 else ""
    if len(stripped) < 2 or stripped in STOPWORDS:
        return None
    return stripped


def _seed_regex(seed: str) -> re.Pattern:
    """시드의 공백을 뺀 글자 사이에 공백을 허용 ("청년월세지원" → 청\\s*년\\s*월\\s*세\\s*지\\s*원)."""
    chars = [re.escape(c) for c in re.sub(r"\s+", "", seed)]
    return re.compile(r"\s*".join(chars), re.IGNORECASE)


def extract_phrases(seed: str, items: list[dict]) -> dict[str, dict]:
    """
    기사 목록(각 {"title", "link"})에서 후보 구절을 뽑습니다.
    반환: {후보(공백 없음): {"articles": 나온 기사 수, "title": 대표 예시 제목, "link": 예시 링크}}
      - 매칭 바로 뒤(공백만 사이에 둔) 토큰 1개 → 시드+토큰1, 2개 → 시드+토큰1+토큰2
        (바로 뒤 첫 토큰이 조사라서 버려지면 뒤쪽 후보는 만들지 않음)
      - 매칭 바로 앞 토큰 1개 → 앞토큰+시드
    같은 기사 안의 중복은 1건으로 셉니다.
    """
    seed_compact = re.sub(r"\s+", "", seed)
    pattern = _seed_regex(seed)
    found: dict[str, dict] = {}

    for item in items:
        title = html.unescape(item.get("title", ""))
        cands_in_article: set[str] = set()
        for m in pattern.finditer(title):
            after, before = title[m.end():], title[: m.start()]

            t1_match = re.match(rf"\s*({_TOKEN_RE})", after)
            if t1_match:
                t1 = _clean_token(t1_match.group(1))
                if t1:
                    cands_in_article.add(seed_compact + t1)
                    t2_match = re.match(rf"\s*({_TOKEN_RE})", after[t1_match.end():])
                    t2 = _clean_token(t2_match.group(1)) if t2_match else None
                    if t2:
                        cands_in_article.add(seed_compact + t1 + t2)

            p_match = re.search(rf"({_TOKEN_RE})\s*$", before)
            if p_match:
                p1 = _clean_token(p_match.group(1))
                if p1:
                    cands_in_article.add(p1 + seed_compact)

        for cand in cands_in_article:
            if cand == seed_compact or len(cand) > MAX_PHRASE_LEN:
                continue
            entry = found.setdefault(cand, {"articles": 0, "title": title, "link": item.get("link", "")})
            entry["articles"] += 1
    return found


# ── 뉴스 조회 / 검증 ─────────────────────────────────────────
def _news_items(seed: str, cache: dict, errors: list) -> list[dict]:
    key = f"newsphr:{seed}"
    cached = se._cache_get(cache, key)
    if cached is not None:
        return cached
    try:
        raw = naver_hub_api.search_news(seed, display=NEWS_FETCH, sort="date")
    except Exception as e:  # noqa: BLE001
        errors.append(f"뉴스 조회 실패({seed}): {type(e).__name__} {e}"[:160])
        return []
    items = [{"title": i["title"], "link": i["link"], "pub_date": i["pub_date"]} for i in raw]
    cache[key] = {"ts": time.time(), "value": items}
    return items


def _verify_volumes(candidates: list[str], week, seed: str, errors: list) -> dict[str, dict]:
    """후보와 정확히 같은 키워드의 검색량만 돌려줍니다 (응답에 섞여 오는 관련 키워드는 무시)."""
    try:
        stats = naver_api.get_keyword_stats(candidates, variation_key=f"{week or ''}-news-{seed}", expand=False)
    except Exception as e:  # noqa: BLE001
        errors.append(f"검색량 확인 실패({seed}): {type(e).__name__} {e}"[:160])
        return {}
    wanted = set(candidates)
    return {s["keyword"]: s for s in stats if s["keyword"] in wanted}


def attach_news_children(
    clusters: list[dict],
    scored_items: list[dict],
    week: str = None,
    max_children: int = 4,
    child_ok=None,
    min_volume: int = None,
) -> list[dict]:
    """
    각 클러스터의 시드로 뉴스 구절 후보를 만들고, 검색량이 확인된 것 중 1개를 세부 키워드로 보장합니다.
    어떤 실패도 주간 불러오기를 막지 않고, 사유는 cluster["child_errors"]와 콘솔([뉴스 세부 경고])에 남깁니다.
    cluster_score 재계산은 호출하는 쪽(main)이 합니다 (main을 import하면 순환 import가 되므로).
    """
    if not config.NAVER_HUB_CONFIGURED or config.USE_MOCK:
        return clusters  # 목 모드: 가짜 뉴스·가짜 검색량이 세부 키워드로 들어가면 안 됨

    cache = se._load_cache()
    taken_global = set()
    for c in clusters:
        if c.get("head"):
            taken_global.add(c["head"]["keyword"])
        taken_global.update(ch["keyword"] for ch in c["children"])

    for cluster in clusters:
        seed = cluster["seed"]
        seed_compact = re.sub(r"\s+", "", seed)
        errors: list[str] = []
        head, children = cluster.get("head"), cluster["children"]
        try:
            items = _news_items(seed, cache, errors)
            phrases = extract_phrases(seed, items)
            n_extracted = len(phrases)

            # 7) 기존 세부 키워드에 뉴스 근거 붙이기 (추가 호출 없음)
            for ch in children:
                hit = phrases.get(ch["keyword"])
                if hit and hit["articles"] >= NEWS_PHRASE_MIN_ARTICLES:
                    ch["news_evidence"] = {"articles": hit["articles"], "title": hit["title"], "link": hit["link"]}

            # 4) 1차 필터: 기사 수 조건 + 이미 목록에 있는 키워드 제외 → 기사 수 상위 N개
            pool = [
                (cand, info) for cand, info in phrases.items()
                if info["articles"] >= NEWS_PHRASE_MIN_ARTICLES and cand not in taken_global
            ]
            pool.sort(key=lambda kv: (-kv[1]["articles"], len(kv[0])))
            pool = pool[:NEWS_PHRASE_TOP_N]
            n_first = len(pool)

            passed: list[dict] = []
            if pool:
                # 5) 검색량 검증
                verified = _verify_volumes([c for c, _ in pool], week, seed, errors)
                priority = (
                    head["seasonal_priority"] if head
                    else (children[0]["seasonal_priority"] if children else 3)
                )
                for cand, info in pool:
                    stat = verified.get(cand)
                    if not stat or stat["total_count"] < max(NEWS_CHILD_MIN_VOLUME, min_volume or 0):
                        continue
                    if child_ok is not None and not child_ok(stat):
                        continue  # 롱텀 품질 기준(커뮤니티/플랫폼 이름 제외 등)
                    scored = scorer.score_keyword(stat, priority, seed=seed)   # 뉴스 후보엔 검색의도 필터를 안 씀
                    scored["cluster_seed"] = seed
                    scored["news_evidence"] = {"articles": info["articles"], "title": info["title"], "link": info["link"]}
                    passed.append(scored)
                time.sleep(0.1)

            # 6) 자리 보장 1개: 기사 수 최다, 같으면 점수 높은 것
            adopted, displaced = None, None
            if passed:
                adopted = max(passed, key=lambda s: (s["news_evidence"]["articles"], s["score"]))
                same = next((ch for ch in children if ch["keyword"] == adopted["keyword"]), None)
                if same is not None:
                    same["news_evidence"] = adopted["news_evidence"]
                    adopted = None
                elif len(children) < max_children:
                    children.append(adopted)
                else:
                    # 이미 뉴스 근거가 붙은 항목은 되도록 남기고, 그 밖에서 점수가 가장 낮은 것을 뺍니다.
                    candidates_out = [ch for ch in children if "news_evidence" not in ch] or children
                    displaced = min(candidates_out, key=lambda ch: ch["score"])
                    children.remove(displaced)
                    children.append(adopted)
                    taken_global.discard(displaced["keyword"])
                if adopted is not None:
                    taken_global.add(adopted["keyword"])

            # 8) 점수순 정렬 (뉴스 보장 항목은 목록에 그대로 남음 — 여기서는 자르지 않음)
            children.sort(key=lambda ch: ch["score"], reverse=True)
            cluster["news_displaced"] = displaced["keyword"] if displaced else None

            # 10) 진단 로그
            if adopted:
                ev = adopted["news_evidence"]
                adopt_txt = f"{adopted['keyword']}(기사 {ev['articles']}건, 검색량 {adopted['total_count']})"
                if displaced:
                    adopt_txt += f" ← 밀려난 기존 세부: {displaced['keyword']}(점수 {displaced['score']})"
            else:
                adopt_txt = "없음"
            print(f"[뉴스 세부] {seed}: 기사 {len(items)}건 → 추출 후보 {n_extracted}개 → 1차 통과 {n_first}개 → "
                  f"검색량 통과 {len(passed)}개 → 채택: {adopt_txt}")
        except Exception as e:  # noqa: BLE001 — 한 클러스터의 실패가 전체를 막지 않게
            errors.append(f"뉴스 세부 키워드 처리 실패({seed}): {type(e).__name__} {e}"[:160])

        if errors:
            cluster.setdefault("child_errors", []).extend(errors)
            for msg in errors:
                print(f"[뉴스 세부 경고] {msg}")

    se._save_cache(cache)
    return clusters
