# -*- coding: utf-8 -*-
"""주간 원고 기획: pipeline/ranking.json → 다음 주 월~일 발행 일정 배정 → claude -p (10개씩) → pipeline/plan_다음주월요일.json

1) 실행일 기준 '다음 주 월요일~일요일' 일정을 만든다 (일요일에 돌리면 내일이 월요일).
2) tools/filter.json 의 weekly.posts_per_day (플랫폼별 하루 편수)만큼 키워드를 배정한다.
   - 1차 배정: 해결·정보형 S·A 는 네이버, 제품선택형 S·A 는 워드프레스에 먼저 배정
   - 2차 배정: 남는 자리만 반대편(또는 분류 없음)에서 채운다
   - 일정에서 빠진 S·A 는 사유를 plan 파일의 unscheduled_sa 와 로그에 남긴다
   - 일정에 못 들어간 S·A 는 pipeline/golden_bank.json(황금 키워드 은행)에 쌓고, 다음 주에 새 S·A 와 함께 후보로 쓴다
     (발견 후 14일이 지난 은행 키워드는 최근 30일 발행·경쟁률만 다시 계산해 기준 미달이면 뺀다. 이미 발행·대기열에 있으면 뺀다)
   - 같은 키워드·거의 같은 키워드는 두 플랫폼에 겹치지 않게, 지난주 기획과도 겹치지 않게 한다.
   - 예비(reserve_count)도 함께 기획해 둔다 (일정은 비워 둠).
3) 의도 묶기·유형 분류는 run_pipeline.py 에서 이미 끝난 결과(ranking.json)를 쓴다. 여기서는 그 호출을 하지 않는다.
4) Claude 에는 키워드별 요약(검색량·포화도·스마트블록 제목·관련 검색어·점수·플랫폼)만 10개씩 나눠 보낸다. 상위 글 본문은 보내지 않는다.
5) 결과: pipeline/plan_YYYYMMDD.json (YYYYMMDD = 다음 주 월요일). 호출별·전체 사용량(total_cost_usd)을 함께 기록한다.

앱 장부는 건드리지 않는다. 쓰는 곳은 pipeline/ 폴더뿐.

사용법
  python tools/plan_posts.py
  python tools/plan_posts.py --filter 설정.json --today 2026-11-13
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_io import write_text_retry  # noqa: E402  (드라이브 동기화 폴더 쓰기 오류 대비)
import spelling  # noqa: E402  (표기 통일·넓은 주제 이름 통일)
import titles as ttl  # noqa: E402  (4단계 제목 규칙 채점)

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "pipeline"
DEFAULT_RANKING = OUT_DIR / "ranking.json"
DEFAULT_BANK = OUT_DIR / "golden_bank.json"
BANK_RECHECK_DAYS = 14
DEFAULT_FILTER = BASE / "tools" / "filter.json"
MODEL = "sonnet"
MAX_TURNS = "3"
TIMEOUT_SEC = 300
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
PLATFORM_KO = {"naver": "네이버", "wordpress": "워드프레스"}
DEFAULT_BUY_WORDS = ["파는곳", "구매처", "구입처", "판매처", "매장", "어디서사", "살수있는곳"]
# 혐오감을 주는 해충 키워드를 가려내는 단어 (filter.json weekly.pest_words 가 없을 때 기본값, 띄어쓰기 무시)
DEFAULT_PEST_WORDS = ["바퀴", "벌레", "노래기", "지네", "거미", "말벌", "땅벌", "벌집", "빈대", "베드버그", "개미",
                      "날파리", "초파리", "파리약", "바구미", "권연", "모기", "진드기", "나방", "그리마", "쥐", "연막탄",
                      "살충", "구더기", "해충"]
# 일정에 쓰지 않는 유형 (run_pipeline 화면 수집에서도 빠지는 유형과 같다)
EXCLUDED_KW_TYPES = {"brand": "브랜드모델구매", "local": "지역업체", "offtopic": "주제 범위 밖", "buyplace": "구매처 찾기",
                     "service": "업체 시공·수리 의뢰"}

PROMPT = """너는 블로그 글 기획자다. 아래는 키워드별 '요약'이다. 상위 글의 본문은 주지 않았으니, 본 적 없는 글을 단정하지 말고
검색 화면 구성(스마트블록 제목, 관련 검색어)과 수치에서 추정해라. 도구는 쓰지 말고 바로 답해라.
글의 콘셉트: 살림하면서 겪는 생활 문제를 해결해 주는 글이다. 해결 과정에서 도움이 되는 제품이 있으면 자연스럽게 추천하되, 제품 소개가 중심이 되지 않게 한다.
키워드가 제품 이름이면 그 제품과 관련해 사람들이 겪는 문제(고르는 기준, 관리·세탁·보관, 흔한 불편)를 해결하는 글로 기획한다.
요약의 track 이 info 면 정보글이다: 문제를 해결하는 데 집중하고 제품은 꼭 필요한 곳에서만 짧게 언급한다.
track 이 money 면 수익글(제품을 고르는 글)이다: 본문 흐름을 "문제 → 해결 → 그때 쓸 제품"으로 짠다. 먼저 독자가 겪는 문제와 해결 방법을 보여 주고, 그 해결에 필요한 제품·도구를 고르는 기준과 추천으로 이어 간다.
수익글에 pair_with 가 있으면 그 글(type 이 plan 이면 같은 주에 올릴 keyword 글, post 이면 이미 쓴 title 글)이 같은 문제를 다룬 정보글이다. 수익글의 첫 소제목들에서 그 글이 다룬 문제와 해결을 짧게 이어 받고, outline 의 points 에 "짝 글 링크를 넣을 자리"를 한 줄 적어라. pair_with 가 없으면 문제 설명부터 스스로 짧게 한다.
요약에 note 가 있으면 그 지시를 따라 기획한다(예: 특정 제품을 깊게 분석하는 글).
platform 이 naver 면 모바일에서 읽기 쉬운 짧은 소제목, wordpress 면 검색 유입을 노린 정보형 구성, reserve 면 어느 쪽에도 쓸 수 있게 만들어라.

[제목 쓰는 법]
{title_guide}

[요약]
{summary}

[요청]
키워드마다 아래를 만들어라.
- intent: 요약의 top_posts(상위 5개 글 제목)를 보고 이 키워드의 '주된 뜻'을 한 줄로 못 박은 것 (예: 푸른곰팡이 = 음식에 핀 곰팡이인지, 벽에 핀 곰팡이인지). 제목 후보는 전부 이 뜻 안에서만 쓴다. top_posts 가 없으면 related_keywords·smart_block_titles 로 정한다.
- search_intent: 검색하는 사람이 알고 싶은 것 (한두 문장)
- gaps: 상위 글이 놓쳤을 것으로 추정되는 빈칸 2~4개 (문자열 목록)
- title_candidates: 제목 후보 정확히 6개. 각 항목은 {{"title": 제목, "pattern": 틀 이름, "title_keyword": 제목에 그대로 들어간 키워드 표기}}. 6개는 아래 [제목 쓰는 법]의 틀 6가지를 하나씩 서로 다르게 쓴다.
- outline: 소제목 4~5개. 각 항목은 {{"heading": 소제목, "points": [다룰 내용 1~3개]}}
- experience_slots: 내 경험이 들어가야 할 자리 1~3개. 각 항목은 {{"after_heading": 어느 소제목 다음인지, "note": 어떤 경험을 쓰면 되는지}}
- beat_top: 요약의 top_posts(현재 검색 상위 글 제목·글자 수·이미지 수·작성 후 지난 날)를 보고, 그 글들보다 나아지려면 무엇을 더 해야 하는지 2~3개
  (예: 상위 글이 업체 홍보뿐이면 '직접 해결 순서', 상위 글이 오래됐으면 '최신 제품·가격'. top_posts 가 없으면 빈 목록)
- target_chars: 목표 본문 글자 수(상위 글 글자 수 중간값보다 20% 이상 많게, 상위 글 정보가 없으면 2500)
- target_images: 목표 이미지 수(상위 글 이미지 수 중간값 이상, 정보가 없으면 8)

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 머리말, 코드블록(```)을 붙이지 않는다. 형식:
{{"plans": [{{"keyword": "...", "intent": "...", "search_intent": "...", "gaps": ["..."], "title_candidates": [{{"title": "...", "pattern": "...", "title_keyword": "..."}}],
"outline": [{{"heading": "...", "points": ["..."]}}], "experience_slots": [{{"after_heading": "...", "note": "..."}}],
"beat_top": ["..."], "target_chars": 3000, "target_images": 10}}]}}
keyword 는 요약에 있는 값을 그대로 쓰고, 요약의 순서를 지켜라.
"""


R1_WP_TITLE_GUIDE = """
- 【싼세계(wordpress) 글, 또는 요약에 r1 이 붙은 예비 글의 제목 — 재기획 R1】 제목은 키워드를 맨 앞에 글자 그대로(띄어쓰기 포함) 두고, 바로 뒤에 사람들이 실제 검색하는 말({words} 등)을 넣는다. '~하면 손해', '헛수고', '실수', '후회' 같은 실수·주의형 제목은 쓰지 않는다(실수·손해형 틀은 쓰지 않고, 6개 중 한 틀은 두 번 써도 된다). 수익글은 특정 제품을 깊게 분석하는 글(후기·장단점·비교)답게 쓴다.
"""

TITLE_GUIDE = """- 이 블로그는 살면서 겪는 살림 문제를 직접 해결하는 글이다. 수익글(track=money)은 그 문제를 풀 때 쓰는 제품을 '고르는' 글이다.
- 제목 후보 6개는 아래 틀 6가지를 하나씩, 서로 다르게 쓴다. 후보마다 pattern 칸에 틀 이름을 그대로 적는다. '~순서', '흔한 실수 N가지', '~부터 확인' 같은 말투를 여러 후보에 되풀이하지 마라.
  · 순서형: 해결 순서·방법·단계를 앞세움 (예: ○○ 순서, 철거부터 마감까지)
  · 실수·손해형: 하기 쉬운 실수·후회·손해를 앞세움
  · 비교형: A vs B, 두 가지·여러 종류를 견줌 (예: 셀프 vs 업체)
  · 상황형: 겪는 상황에서 시작함 (예: ~할 때, ~했는데 ~라면)
  · 숫자·결과형: 시간·비용·횟수·결과 숫자를 앞세움 (예: 15분이면, 하루 두 번)
  · 질문형: 독자가 품은 물음으로 시작함 (예: ~해도 될까, 왜 ~일까)
- 수익글(track=money)은 제목에 '고르는 기준'이 드러나야 한다(필수). 고르는·고르기·추천·비교·종류·차이·vs·가격·용량·규격·크기·~용 같은 선택 표현이 후보마다 하나 이상 들어가야 한다. 확인·점검·교체 순서만 있는 제목은 수익글에서 탈락한다.
- 검색 의도: 제목은 intent(그 키워드의 주된 뜻)에서 벗어나면 안 된다. 같은 말이 다른 뜻으로도 쓰이면(예: 음식 곰팡이와 벽 곰팡이) 한 가지 뜻으로만 쓴다.
- 길이: 제목은 max_title_chars 글자(공백 포함) 이하. 네이버는 앞 28자 안에 핵심이 들어가야 모바일에서 안 잘린다.
- 금지: 과장·낚시 표현({forbidden}), 브랜드·회사·모델명, 상위 글(top_posts 제목)과 비슷한 문장. 근거 없는 숫자나 경험을 지어내지 마라(숫자는 상식적인 시간·온도·횟수·순서처럼 말할 수 있는 것만).
- 근거로 쓸 것: top_posts 제목(비슷하지 않게 차별화), autocomplete·related_keywords(사람들이 실제 검색하는 표현), track, timing(시기가 상승·선발행이면 계절 말을 자연스럽게), pair_with.
- 말투 예시(내가 지금까지 쓴 제목):
{samples}
"""


def title_guide(forbidden: list, samples: list) -> str:
    return TITLE_GUIDE.format(forbidden=", ".join(forbidden[:14]), samples="\n".join("  · " + s for s in samples))


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------- 설정 ----------

def load_weekly(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    w = cfg.get("weekly")
    if not isinstance(w, dict):
        raise ValueError("filter.json 에 'weekly' 항목이 없습니다.")
    ppd = w.get("posts_per_day") or {}
    for p in ("naver", "wordpress"):
        if not isinstance(ppd.get(p), int) or ppd[p] < 0:
            raise ValueError(f"weekly.posts_per_day.{p} 는 0 이상의 정수여야 합니다.")
    if ppd["naver"] + ppd["wordpress"] == 0:
        raise ValueError("weekly.posts_per_day 합계가 0 입니다.")
    w.setdefault("reserve_count", 10)
    w.setdefault("claude_batch_size", 10)
    w.setdefault("similarity_threshold", 0.75)
    w.setdefault("avoid_past_weeks", 1)
    w.setdefault("max_per_category_per_platform", 4)     # 같은 카테고리는 플랫폼당 주 N편까지
    w.setdefault("max_per_topic", 2)                     # 같은 대상(정리된 대상 이름)은 주 N편까지 (두 플랫폼 합계) — 1.5단계부터 넓은 주제가 아니라 대상 기준
    w.setdefault("max_per_topic_group", 4)               # 넓은 주제(topic_group: 수전·세제·욕실용품 등)는 주 N편까지
    w.setdefault("max_per_detail", 2)                    # 지문의 세부(부위·종류 낱말: 트랩, 필터 …)가 같은 글은 주 N편까지 ('일반' 제외, 두 플랫폼 합계)
    w.setdefault("max_money_per_target", 1)              # 같은 대상의 수익글은 주 N편까지
    w.setdefault("max_pair_calls", 30)                   # 한 번 실행에서 가까운 쌍 확인(Claude) 호출 상한 (넘으면 검색량 큰 후보부터 확인하고 나머지는 이번 주 후보에서 뺌)
    w.setdefault("plan_pair_top_factor", 3)              # 기획 단계에서 후보끼리 가까운 쌍을 확인할 상위 후보 수 = 갈래별 목표(일정+예비)의 이 배수
    w.setdefault("min_seasonal_lift", 0.6)               # 선점 추세(작년 앞으로 8주÷직전 4주)가 이 값 미만이면 이번 주 제외
    w.setdefault("schedule_volume_min", 150)             # 일정·예비에는 월 검색량이 이 값 미만인 키워드를 넣지 않는다 (모자라면 채우지 않고 모자란 수를 보고)
    w.setdefault("fill_goal_factor", 1.5)                # 탐색 멈춤 목표 = 갈래별 정원(일정+예비)의 이 배수만큼 '통과 후보'(월 검색량 하한·유형·등급 통과)가 모이면 멈춤
    w.setdefault("pair_bonus", {"plan": 1.3, "post": 1.15, "none": 1.0})   # 수익글 순위 가점(추정값, 5단계에서 보정): 같은 주 정보글과 짝 / 이미 쓴 글과 짝 / 짝 없음
    w.setdefault("money_pair_first", False)              # true = 수익글을 '짝 글이 같은 주 일정에 있음'이 먼저(2.5단계 방식), false = 예상 클릭 순(3단계 기본, 짝은 같을 때만)
    w.setdefault("schedule_median_warn", 800)            # 일정 전체 월 검색량 중간값이 이 값 미만이면 기록에 경고
    w["click"] = click_cfg(cfg)
    r1c = cfg.get("r1") or {}
    w["r1"] = r1c if r1c.get("enabled") else None        # 재기획 R1(filter.json 'r1'): enabled 일 때만 새 뽑기 기준을 쓴다
    w.setdefault("assign_mode", "split")                 # split = 해결형→네이버·제품형→워드프레스 / solve_first = 두 플랫폼 모두 해결형 먼저
    if w["assign_mode"] not in ("split", "solve_first"):
        raise ValueError('weekly.assign_mode 는 "split" 또는 "solve_first" 여야 합니다.')
    tq = w.get("track_quota")        # 갈래별 편수 {"naver": {"info": 15, "money": 6}, "wordpress": {...}, "reserve": {...}} (SPEC_v2 2장 표)
    if tq is not None:
        try:
            tq = {p: {t: int(tq[p][t]) for t in ("info", "money")} for p in ("naver", "wordpress", "reserve")}
        except (KeyError, TypeError, ValueError):
            raise ValueError('weekly.track_quota 는 {"naver": {"info": 15, "money": 6}, "wordpress": {...}, "reserve": {...}} 모양이어야 합니다.')
        if any(v < 0 for d in tq.values() for v in d.values()):
            raise ValueError("weekly.track_quota 는 0 이상의 정수여야 합니다.")
        if sum(tq["naver"].values()) != 7 * ppd["naver"] or sum(tq["wordpress"].values()) != 7 * ppd["wordpress"] or sum(tq["reserve"].values()) != w["reserve_count"]:
            raise ValueError("weekly.track_quota 합계가 posts_per_day×7 · reserve_count 와 다릅니다.")
        w["track_quota"] = tq
    SYNONYMS.update({str(k): str(v) for k, v in (w.get("synonym_map") or {}).items() if str(k).strip()})
    w.setdefault("pest_words", DEFAULT_PEST_WORDS)       # 해충 키워드 판별 단어
    w.setdefault("max_pest_per_platform", None)          # 해충 키워드는 플랫폼당 주 N편까지 (None = 한도 없음)
    w.setdefault("max_pest_seeds", None)                 # 주간 씨앗 중 해충 씨앗 최대 수 (None = 한도 없음)
    for k in ("max_pest_per_platform", "max_pest_seeds"):
        if w[k] is not None and (not isinstance(w[k], int) or w[k] < 0):
            raise ValueError(f"weekly.{k} 는 0 이상의 정수(또는 null)여야 합니다.")
    if w["max_per_category_per_platform"] < 1 or w["max_per_topic"] < 1 or w["max_per_topic_group"] < 1 or not (w["min_seasonal_lift"] is None or w["min_seasonal_lift"] >= 0):
        raise ValueError("weekly 의 max_per_category_per_platform / max_per_topic / min_seasonal_lift 값을 확인하세요.")
    if w["reserve_count"] < 0 or w["claude_batch_size"] < 1 or not (0 < w["similarity_threshold"] <= 1):
        raise ValueError("weekly 의 reserve_count / claude_batch_size / similarity_threshold 값을 확인하세요.")
    return w


def buy_words(path=None) -> list:
    """구매처 찾기 키워드를 가려내는 단어 (filter.json weekly.buy_place_words, 없으면 기본값). 띄어쓰기는 무시."""
    try:
        cfg = json.loads(Path(path or DEFAULT_FILTER).read_text(encoding="utf-8-sig"))
        ws = (cfg.get("weekly") or {}).get("buy_place_words")
    except (OSError, ValueError, AttributeError):
        ws = None
    return [str(x) for x in (ws or DEFAULT_BUY_WORDS) if str(x).strip()]


def is_pest(r: dict, words: list) -> bool:
    """키워드나 세부 주제에 해충 단어가 들어 있으면 True (띄어쓰기 무시)."""
    if not words:
        return False
    return has_word(r.get("keyword") or "", words) or has_word(r.get("kw_topic") or "", words)


def rule_exclude(rows: list, w: dict, bw: list, golden_mode: bool, monday: date = None) -> tuple:
    """배정 전에 규칙으로 뺄 키워드를 고른다 → (남은 후보, {키워드: {"code","text"}}).
    구매처 찾기·제외 유형 → 버림, 계절 하락 → (호출한 쪽에서) 은행에 보관."""
    ok_rows, why = [], {}
    due = lambda r: monday is not None and bool((r.get("bank") or {}).get("revisit_week")) and (r["bank"]["revisit_week"] <= monday.isoformat())
    for r in rows:
        kw, t = r["keyword"], r.get("kw_type")
        if t == "buyplace" or has_word(kw, bw):
            why[kw] = {"code": "buyplace", "text": "구매처 찾기 키워드(파는곳·구매처·매장 등)라 제외"}
        elif t in EXCLUDED_KW_TYPES and not (t == "brand" and w.get("r1")):     # R1: 브랜드·모델명은 싼세계 수익글에만 쓴다(네이버 쪽은 배정 단계에서 뺌)
            why[kw] = {"code": "type", "text": f"유형이 '{EXCLUDED_KW_TYPES[t]}'라 제외"}
        elif r.get("dup_existing"):
            why[kw] = {"code": "published", "text": f"이미 쓴 글과 같은 글 ('{r['dup_existing']}') — 탐색 단계 점검"}
        elif (r.get("monthly_total") or 0) < w.get("schedule_volume_min", 0):
            why[kw] = {"code": "lowvol", "text": f"월 검색량 {r.get('monthly_total')} — 일정·예비에는 {w['schedule_volume_min']} 미만을 넣지 않음"}
        elif golden_mode and not w.get("r1") and not due(r) and seasonal_decline(r, w["min_seasonal_lift"]):
            why[kw] = {"code": "season", "text": seasonal_decline(r, w["min_seasonal_lift"])}
        else:
            if due(r):
                r["revisit_due"] = True      # 은행에서 쉬던 키워드가 다시 오르는 주가 되어 후보로 복귀 (계절 하락 규칙을 건너뜀)
            ok_rows.append(r)
    return ok_rows, why


CLICK_DEFAULT = {"weeks_per_month": 4.3, "season": {"min": 0.5, "max": 2.0, "default": 1.0},
                 "entry": {"tiers": [{"max_top100_title": 5, "max_title_30d": 5, "value": 1.0}, {"max_top100_title": 15, "max_title_30d": 20, "value": 0.7},
                                     {"max_top100_title": 30, "max_title_30d": 60, "value": 0.4}, {"value": 0.2}],
                           "default": 0.5, "grade_fallback": {"S": 0.7, "A": 0.4}},
                 "ctr": {"base": 0.3, "position_default": 0.7, "ai_briefing": 0.75, "ai_briefing_blog": 0.9,
                         "ad_click": {"high_ratio": 0.04, "high_factor": 1.15, "low_ratio": 0.008, "low_factor": 0.85}},
                 "rise_min": 1.2}
POSITION_DEFAULT = [{"max_order": 4, "weight": 1.0}, {"max_order": 6, "weight": 0.8}, {"max_order": 8, "weight": 0.6}, {"max_order": 999, "weight": 0.3}]


def click_cfg(cfg: dict) -> dict:
    """filter.json 의 expected_clicks 를 기본값과 합친다. (모든 계수는 추정값 — 5단계에서 보정)"""
    ec = cfg.get("expected_clicks") or {}
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in CLICK_DEFAULT.items()}
    for k, v in ec.items():
        if k.startswith("_"):
            continue
        out[k] = {**out[k], **v} if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    out["position_weights"] = (cfg.get("golden") or {}).get("position_weights") or POSITION_DEFAULT
    return out


def seasonal_lift_of(r: dict):
    """선점 추세(작년 '이후 8주'÷'직전 4주'). 작년 같은 시기 데이터가 모자라 못 쓰면 None."""
    lead = ((r.get("grade_info") or {}).get("conditions") or {}).get("lead")
    if lead:
        return lead.get("seasonal_lift_8w") if lead.get("lift_used") else None
    g = (r.get("golden") or {}).get("lead") or {}
    base = g.get("ly_prev4w_avg")
    return g.get("seasonal_lift_8w") if (base is not None and base >= 5) else None


def attach_clicks(rows: list, cc: dict) -> None:
    """각 후보에 주간 예상 클릭(expected_clicks)과 계산에 쓴 값(clicks_basis)을 단다. Claude·API 호출 없음.
    주간 예상 클릭 = (월 검색량 ÷ 4.3) × 계절 배수 × 진입 가능성 × 클릭률 — 모든 계수는 추정값(5단계에서 보정)."""
    for r in rows:
        vol = int(r.get("monthly_total") or 0)
        old = r.get("clicks_basis") if isinstance(r.get("clicks_basis"), dict) else {}
        lift = seasonal_lift_of(r)
        sc = cc["season"]
        season = sc["default"] if lift is None else round(min(max(lift, sc["min"]), sc["max"]), 2)
        g = r.get("golden") or {}
        pub, sp = g.get("publish") or {}, g.get("serp_position") or {}
        top, t30 = pub.get("top100_title"), pub.get("title_30d")
        if top is None:
            rc = (r.get("bank") or {}).get("recheck") or {}
            top, t30 = rc.get("top100_title"), rc.get("title_30d")
        if top is None and old.get("top100_title") is not None:
            top, t30 = old.get("top100_title"), old.get("title_30d")
        en = cc["entry"]
        if top is not None:
            entry, entry_src = en["default"], "경쟁 기본값"
            for tier in en["tiers"]:
                if top <= tier.get("max_top100_title", 10 ** 9) and (t30 is None or t30 <= tier.get("max_title_30d", 10 ** 9)):
                    entry, entry_src = tier["value"], f"상위100 제목일치 {top}·30일 제목일치 {t30}"
                    break
        elif r.get("grade") in en["grade_fallback"]:
            entry, entry_src = en["grade_fallback"][r["grade"]], f"경쟁 값 없음 → 등급 {r['grade']} 대용"
        else:
            entry, entry_src = en["default"], "경쟁 값 없음 → 기본값"
        ct = cc["ctr"]
        order = sp.get("blog_area_order")
        if order is None:
            order = (r.get("grade_info") or {}).get("position_order")
        if order is None:
            order = old.get("blog_area_order")
        pw = None
        if order is not None:
            steps = sorted(cc["position_weights"], key=lambda x: x["max_order"])
            pw = next((st["weight"] for st in steps if order <= st["max_order"]), steps[-1]["weight"])
        pos_w = ct["position_default"] if pw is None else pw
        ai = sp.get("has_ai_briefing") if sp else r.get("has_ai_briefing")
        if ai is None:
            ai = old.get("ai_briefing")
        ai_f = 1.0 if not ai else (ct["ai_briefing_blog"] if sp.get("ai_briefing_has_blog") else ct["ai_briefing"])
        pc, mb = r.get("ave_pc_clk"), r.get("ave_mobile_clk")
        ad_ratio = None if (pc is None and mb is None) or not vol else round(((pc or 0) + (mb or 0)) / vol, 4)
        ad = ct["ad_click"]
        ad_f = 1.0 if ad_ratio is None else (ad["high_factor"] if ad_ratio >= ad["high_ratio"] else (ad["low_factor"] if ad_ratio <= ad["low_ratio"] else 1.0))
        ctr = round(ct["base"] * pos_w * ai_f * ad_f, 4)
        weekly = vol / cc["weeks_per_month"]
        r["expected_clicks"] = round(weekly * season * entry * ctr, 1)
        r["clicks_basis"] = {"monthly_total": vol, "weekly_base": round(weekly, 1), "season_lift": lift, "season": season,
                             "entry": entry, "entry_source": entry_src, "top100_title": top, "title_30d": t30,
                             "blog_area_order": order, "position_weight": pos_w, "ai_briefing": bool(ai), "ai_factor": ai_f,
                             "ad_click_ratio": ad_ratio, "ad_factor": ad_f, "ctr": ctr,
                             "note": "모든 계수는 추정값(5단계에서 실제 순위·유입으로 보정)"}


def revisit_week_for(series, monday: date):
    """작년 추세가 다시 오르기 시작하는 주(월요일 날짜 문자열). series = 캐시의 데이터랩 12개월 [{"month","ratio"}] (가장 오래된 달 = 작년 이번 달).
    12개월 중 가장 낮은 달 이후 처음으로 25% 이상 오르는 달의 '한 달 전' 첫 주. 못 구하면 None."""
    try:
        ratios = [float(p["ratio"]) for p in series]
    except (KeyError, TypeError, ValueError):
        return None
    if len(ratios) < 12:
        return None
    t = min(range(len(ratios)), key=lambda i: (ratios[i], i))
    for k in range(t + 1, len(ratios)):
        if ratios[k] >= max(ratios[t], 1.0) * 1.25 and ratios[k] > ratios[k - 1]:
            m0 = monday.month - 1 + (k - 1)
            first = date(monday.year + m0 // 12, m0 % 12 + 1, 1)
            if first <= monday:
                first = monday
            return (first - timedelta(days=first.weekday())).isoformat()
    return None


def timing_of(r: dict, cc: dict, calendar_seeds: set) -> dict:
    """시기 표시: 선발행(계절 달력 씨앗) / 상승(선점 추세 ≥ rise_min) / 유지."""
    lift = seasonal_lift_of(r)
    if r.get("seed") and r.get("seed") in calendar_seeds:
        return {"label": "선발행", "reason": "계절 달력에서 6~8주 뒤 성수기가 오는 문제라 미리 넣은 씨앗에서 나온 글"}
    if lift is not None and lift >= cc["rise_min"]:
        return {"label": "상승", "reason": f"선점 추세 {lift:.2f} (작년 앞으로 8주÷직전 4주 ≥ {cc['rise_min']}) — 앞으로 검색이 오르는 시기"}
    if r.get("revisit_due"):
        return {"label": "유지", "reason": f"은행에서 쉬다가 작년 추세가 다시 오르는 주({(r.get('bank') or {}).get('revisit_week')})가 되어 다시 후보로 들어옴"}
    if lift is None:
        return {"label": "유지", "reason": "작년 같은 시기 값이 모자라 추세를 못 씀 — 꾸준한 주제로 봄"}
    return {"label": "유지", "reason": f"선점 추세 {lift:.2f} — 앞으로 8주 검색량이 크게 변하지 않음"}


def attach_timing(rows: list, cc: dict, calendar_seeds: set) -> None:
    for r in rows:
        r["timing"] = timing_of(r, cc, calendar_seeds)


def load_calendar_seeds(*dirs) -> set:
    """계절 달력에서 만든 씨앗 목록(make_seeds 가 calendar_seeds.json 에 적어 둠). 앞쪽 폴더에 있으면 그것을 쓴다."""
    for d in dirs:
        try:
            return set(json.loads((Path(d) / "calendar_seeds.json").read_text(encoding="utf-8")).get("seeds") or [])
        except (OSError, ValueError, AttributeError):
            continue
    return set()


_TREND_CACHE = {}


def trend_series(keyword: str) -> list:
    """캐시(pipeline/cache.json)에 저장된 데이터랩 12개월 값. 읽기만 한다(API 호출 없음)."""
    if not _TREND_CACHE:
        try:
            _TREND_CACHE.update((json.loads((OUT_DIR / "cache.json").read_text(encoding="utf-8")).get("metrics") or {}))
        except (OSError, ValueError):
            _TREND_CACHE["_none"] = {}
    return (_TREND_CACHE.get(keyword) or {}).get("trend_12m") or []


def seasonal_decline(r: dict, min_lift) -> str:
    """선점 추세(작년 '이후 8주'÷'직전 4주')가 min_lift 미만이면 사유 글, 아니면(또는 값을 못 쓰면) None.
    작년 같은 시기 데이터가 부족해 선점 추세를 못 쓴 키워드(lift_used 가 아님)는 이 규칙을 건너뛴다."""
    if not min_lift:
        return None
    lift = seasonal_lift_of(r)
    if lift is None or lift >= min_lift:
        return None
    return f"계절 하락: 선점 추세(작년 앞으로 8주÷직전 4주) {lift:.2f} (기준 {min_lift} 미만)"


def merge_spelling(pool: list) -> tuple:
    """표준 표기(canonical)가 같은 키워드는 같은 의도로 묶어 점수·등급이 가장 높은 1개만 남긴다.
    남긴 것은 표준 표기 키워드로 바꾸고, 원래 표기(오타 포함)는 original_keyword / variant_spellings 에 적는다.
    → (새 후보, {제외된 원래 키워드: 사유}, [{canonical, keep, members}])"""
    groups, pos = {}, {id(r): i for i, r in enumerate(pool)}
    for r in pool:
        canon = spelling.respell(" ".join((r.get("canonical") or r["keyword"]).split()))   # 샷시·샤시·새시 같은 표기 흔들림은 대표 표기로
        groups.setdefault(norm(canon), []).append((canon, r))
    out, dropped, merged = [], {}, []
    rank = lambda r: (r.get("expected_clicks") or 0, r.get("expected_traffic") or 0, r["score"]["total"])
    for members in groups.values():
        members.sort(key=lambda m: rank(m[1]), reverse=True)
        canon, best = members[0]
        variants = sorted({m[1]["keyword"] for m in members if norm(m[1]["keyword"]) != norm(canon)})
        row = best
        if norm(best["keyword"]) != norm(canon) or variants:
            row = {**best, "keyword": canon, "original_keyword": best["keyword"], "spelling_variants": variants}
            if norm(best["keyword"]) == norm(canon):
                row.pop("original_keyword")
        out.append((pos[id(best)], row))
        for _, m in members[1:]:
            dropped[m["keyword"]] = f"표준 표기('{canon}')가 같아 같은 의도로 묶음 (대표: '{best['keyword']}')"
        if len(members) > 1 or row is not best:
            merged.append({"canonical": canon, "keep": best["keyword"], "members": [m[1]["keyword"] for m in members]})
    out = [row for _, row in sorted(out, key=lambda x: x[0])]   # 입력 순서 유지
    return out, dropped, merged


# ---------- 일정·배정 ----------

def next_monday(today: date) -> date:
    return today + timedelta(days=7 - today.weekday())


# 비교할 때만 쓰는 표기 통일(키워드 글자 자체는 바꾸지 않음). filter.json weekly.synonym_map 으로 더할 수 있다.
SYNONYMS = {"씽크대": "싱크대", "헹거": "행거", "렉": "랙", "쇼파": "소파", "후라이팬": "프라이팬", "악세사리": "액세서리",
            "스웨이드": "세무", "화장실": "욕실", **spelling.SPELL_UNIFY}


def norm(s: str) -> str:
    t = "".join((s or "").split()).lower()
    for a, b in SYNONYMS.items():
        t = t.replace(a, b)
    return t


def similar(a: str, b: str, th: float) -> bool:
    na, nb = norm(a), norm(b)
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= th


def load_topic(path: Path) -> dict:
    """topic.json 에서 플랫폼별 우선 단어를 읽는다 → {"naver": [...], "wordpress": [...]}"""
    t = json.loads(path.read_text(encoding="utf-8-sig"))
    roles = {p: [str(x) for x in t["roles"][p]["words"] if str(x).strip()] for p in ("naver", "wordpress")}
    if not roles["naver"] or not roles["wordpress"]:
        raise ValueError("topic.json 의 roles.naver / roles.wordpress 에 words 가 필요합니다.")
    return roles


def has_word(keyword: str, words: list) -> bool:
    k = norm(keyword)
    return any(norm(x) in k for x in words)


def load_existing(path: Path) -> tuple:
    """pipeline/existing_keywords.json → (항목 목록, 수집 시각). 파일이 없으면 ([], None)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d.get("items", []), d.get("collected_at")
    except (OSError, ValueError):
        return [], None


def split_existing(pool: list, existing: list, th: float) -> tuple:
    """이미 발행·예약된 글과 겹치는 키워드를 걸러낸다 → (남은 후보, 제외 기록).
    겹침 = 포커스 키워드와 거의 같거나, 키워드(4글자 이상)가 기존 글 제목 안에 들어 있음(띄어쓰기 무시)."""
    keep, dropped = [], []
    for r in pool:
        nk, hit = norm(r["keyword"]), None
        for e in existing:
            fk, title = e.get("focus_keyword"), e.get("title") or ""
            if (fk and similar(r["keyword"], fk, th)) or (len(nk) >= 4 and nk in norm(title)) \
                    or similar(r["keyword"], title, th):
                hit = e
                break
        if hit:
            dropped.append({"keyword": r["keyword"], "matched_title": hit.get("title"),
                            "platform": hit.get("platform"), "site": hit.get("site")})
        else:
            keep.append(r)
    return keep, dropped


def pick(sorted_pool: list, count: int, used: list, th: float) -> list:
    """우선순위 순으로 훑으며 count 개를 고른다. 이미 쓴 것·거의 같은 것은 건너뛴다."""
    got = []
    for r in sorted_pool:
        if len(got) >= count:
            break
        if any(similar(r["keyword"], u["keyword"], th) for u in used + got):
            continue
        got.append(r)
    return got


def split_overlap(ranking: list, prev_keywords: list, th: float) -> tuple:
    """지난 기획과 거의 같은 키워드를 걸러낸다 → (남은 후보, 제외된 후보)."""
    pool, overlap = [], []
    for r in ranking:
        (overlap if any(similar(r["keyword"], p, th) for p in prev_keywords) else pool).append(r)
    return pool, overlap


def apply_intent_groups(pool: list, groups: list) -> dict:
    """run_pipeline 이 결과에 적어 둔 의도 묶음(ranking.json 의 intent_groups)을 적용한다.
    묶음마다 후보 안에서 점수가 가장 높은 1개만 남긴다. Claude 호출은 하지 않는다."""
    out = {"status": "ok", "groups": [], "excluded": [], "pool": pool}
    by_kw = {r["keyword"]: r for r in pool}
    drop = set()
    for g in groups:
        g = [k for k in g if k in by_kw]  # 화면 수집·겹침 제외로 후보에 없는 키워드는 무시
        if len(g) < 2:
            continue
        keep = max(g, key=lambda k: by_kw[k]["score"]["total"])
        out["groups"].append({"keep": keep, "members": g})
        for k in g:
            if k != keep:
                drop.add(k)
                out["excluded"].append({"keyword": k, "kept": keep, "score": by_kw[k]["score"]["total"],
                                        "kept_score": by_kw[keep]["score"]["total"],
                                        "reason": "의도 중복으로 제외"})
    out["pool"] = [r for r in pool if r["keyword"] not in drop]
    return out


def assign(pool: list, w: dict, monday: date, roles: dict) -> tuple:
    """일정 배정 → (items, shortage, reasons).
    1차: 해결·정보형(platform_hint=naver)은 네이버, 제품선택형(wordpress)은 워드프레스에 먼저 넣는다.
    2차: 남는 자리만 채운다 — 분류가 없거나 기타인 키워드는 자리가 더 많이 남은 쪽, 반대편 유형은 그 반대편 자리에.
    3차: 그래도 남은 후보는 예비(reserve_count)로.
    reasons = {키워드: {"code": "similar" | "capacity" | "reserve", "text": 사유}}  (일정에 못 들어간 후보만)"""
    if w.get("r1"):
        return assign_r1(pool, w, monday, roles)
    if w.get("track_quota"):
        return assign_tracks(pool, w, monday, roles)
    th = w["similarity_threshold"]
    ppd = w["posts_per_day"]
    cap = {"naver": 7 * ppd["naver"], "wordpress": 7 * ppd["wordpress"]}
    total = lambda r: r["score"]["total"]
    gkey = lambda r: (2 if r.get("grade") == "S" else (1 if r.get("grade") == "A" else 0), r.get("expected_traffic") or 0)
    sort_key = {
        # 네이버: 등급·예상 유입 → 해결형 단어 포함 → 화면 기회 점수
        "naver": lambda r: (gkey(r), has_word(r["keyword"], roles["naver"]), r["score"]["screen"], total(r)),
        # 워드프레스: 등급·예상 유입 → 제품 선택형 단어 포함 → 추세가 꾸준한 순
        "wordpress": lambda r: (gkey(r), has_word(r["keyword"], roles["wordpress"]),
                                0.5 if r.get("trend_steady") is None else r["trend_steady"], total(r)),
    }
    rows = {"naver": [], "wordpress": []}
    notes = {}                     # id(row) → 배정 메모
    used, reasons, placed = [], {}, set()
    cat_max, top_max, grp_max = w["max_per_category_per_platform"], w["max_per_topic"], w["max_per_topic_group"]
    cat_n, top_n, grp_n, capblock = {}, {}, {}, {}      # 카테고리(플랫폼별)·대상·넓은 주제 편수, 한도에 막힌 키워드의 사유
    pest_max, pest_words = w.get("max_pest_per_platform"), w.get("pest_words") or []
    pest_n = {}                              # 플랫폼별 해충 키워드 편수

    def take(r, platform: str, note: str = None) -> bool:
        """r 를 platform 에 넣는다. 이미 넣은 키워드와 거의 같으면 넣지 않고 사유를 적는다."""
        for u in used:
            if similar(r["keyword"], u["keyword"], th):
                reasons[r["keyword"]] = {"code": "similar", "text": f"이미 배정된 '{u['keyword']}'와 거의 같은 키워드라 제외"}
                return False
        cw = r.get("cover_with") or ()
        for u in used:
            if u["keyword"] in cw:      # 후보끼리 포함 관계(넓은 것 ⊃ 좁은 것): 같은 주에 함께 넣지 않고 나머지는 다음 주 후보(은행)로
                capblock[r["keyword"]] = f"'{u['keyword']}'와 넓고 좁은 관계(같은 주에 함께 넣지 않음)"
                return False
        cat, top, grp = r.get("kw_category"), r.get("kw_topic"), r.get("kw_topic_group")
        if cat and cat_n.get((platform, cat), 0) >= cat_max:
            capblock[r["keyword"]] = f"같은 카테고리 '{cat}'가 {PLATFORM_KO[platform]}에 이미 {cat_max}편 (주당 한도)"
            return False
        if top and top_n.get(top, 0) >= top_max:
            capblock[r["keyword"]] = f"같은 대상 '{top}'이 이미 {top_max}편 (주당 한도)"
            return False
        if grp and grp_n.get(grp, 0) >= grp_max:
            capblock[r["keyword"]] = f"같은 넓은 주제 '{grp}'가 이미 {grp_max}편 (주당 한도)"
            return False
        pest = pest_max is not None and is_pest(r, pest_words)
        if pest and pest_n.get(platform, 0) >= pest_max:
            capblock[r["keyword"]] = f"해충 글 줄이기: {PLATFORM_KO[platform]}에 해충 글이 이미 {pest_max}편 (주당 한도)"
            return False
        if pest:
            pest_n[platform] = pest_n.get(platform, 0) + 1
        if cat:
            cat_n[(platform, cat)] = cat_n.get((platform, cat), 0) + 1
        if top:
            top_n[top] = top_n.get(top, 0) + 1
        if grp:
            grp_n[grp] = grp_n.get(grp, 0) + 1
        rows[platform].append(r)
        used.append(r)
        placed.add(id(r))
        if note:
            notes[id(r)] = note
        return True

    if w.get("assign_mode") == "solve_first":
        # 콘셉트: 두 플랫폼 모두 '생활 문제 해결 + 필요하면 제품 추천' 글. 해결형을 먼저 두 플랫폼에 나눠 넣고,
        # 자리가 남을 때만 제품형으로 채운다. 플랫폼은 남은 자리 비율이 더 큰 쪽부터(한도에 막히면 반대편).
        PLAT_KO2 = {"naver": "네이버", "wordpress": "워드프레스"}
        solve = sorted((r for r in pool if r.get("platform_hint") == "naver"),
                       key=lambda r: gkey(r) + (has_word(r["keyword"], roles["naver"]), total(r)), reverse=True)
        others = sorted((r for r in pool if r.get("platform_hint") != "naver"),
                        key=lambda r: gkey(r) + (total(r),), reverse=True)
        for r in solve + others:
            room = {p: cap[p] - len(rows[p]) for p in rows}
            if room["naver"] <= 0 and room["wordpress"] <= 0:
                break
            order = sorted((p for p in rows if room[p] > 0), key=lambda p: (-room[p] / max(1, cap[p]), p != "naver"))
            note = None if r.get("platform_hint") == "naver" else "해결형 후보가 모자라 제품형으로 채움"
            for target in order:
                if take(r, target, note) or r["keyword"] in reasons:
                    break

    # 1차: 유형에 맞는 플랫폼
    for platform in ("naver", "wordpress") if w.get("assign_mode") != "solve_first" else ():
        mine = sorted((r for r in pool if r.get("platform_hint") == platform), key=sort_key[platform], reverse=True)
        for r in mine:
            if len(rows[platform]) >= cap[platform]:
                break
            take(r, platform)

    # 2차: 남는 자리 채우기 (등급·예상 유입이 높은 후보부터)
    PLAT_KO = {"naver": "네이버", "wordpress": "워드프레스"}
    other = {"naver": "wordpress", "wordpress": "naver"}
    rest = sorted((r for r in pool if id(r) not in placed and r["keyword"] not in reasons),
                  key=lambda r: gkey(r) + (total(r),), reverse=True) if w.get("assign_mode") != "solve_first" else []
    for r in rest:
        room = {p: cap[p] - len(rows[p]) for p in rows}
        hint = r.get("platform_hint")
        if hint in rows:
            order = [hint, other[hint]]
        else:
            order = sorted(rows, key=lambda p: -room[p])      # 분류 없음: 자리가 더 많이 남은 쪽 (같으면 네이버)
        for target in order:
            if room[target] <= 0:
                continue
            note = None
            if hint in rows and target != hint:
                note = f"{kc_ko(hint)}이지만 {PLAT_KO[hint]} 자리가 다 차서 {PLAT_KO[target]}의 남는 자리에 배정"
            elif hint not in rows:
                note = "분류가 없거나 기타 유형이라 남는 자리에 배정"
            if take(r, target, note) or r["keyword"] in reasons:   # 한도에 막히면 반대편 플랫폼도 시도
                break

    # 3차: 예비
    left = sorted((r for r in pool if id(r) not in placed and r["keyword"] not in reasons),
                  key=lambda r: gkey(r) + (total(r),), reverse=True)
    reserve, reserve_pest = [], 0
    for r in left:
        if len(reserve) >= w["reserve_count"]:
            break
        if r["keyword"] in capblock:
            continue
        if pest_max is not None and is_pest(r, pest_words):
            if reserve_pest >= pest_max:      # 예비에도 해충 글은 한도만큼만
                capblock[r["keyword"]] = f"해충 글 줄이기: 예비에도 해충 글이 이미 {pest_max}편"
                continue
            reserve_pest += 1
        if any(similar(r["keyword"], u["keyword"], th) for u in used + reserve):
            reasons[r["keyword"]] = {"code": "similar", "text": "배정된 키워드와 거의 같아 예비에서도 제외"}
            continue
        if any(u["keyword"] in (r.get("cover_with") or ()) for u in used + reserve):
            capblock[r["keyword"]] = "배정된 키워드와 넓고 좁은 관계라 예비에서도 제외 (같은 주에 함께 넣지 않음)"
            continue
        reserve.append(r)
    for r in reserve:
        reasons[r["keyword"]] = {"code": "reserve", "text": "일정 자리(네이버·워드프레스 주간 편수)가 다 차서 예비로 배정"}
    for r in pool:
        if id(r) not in placed and r["keyword"] not in reasons and r["keyword"] in capblock:
            reasons[r["keyword"]] = {"code": "cap", "text": capblock[r["keyword"]]}
        elif id(r) not in placed and r["keyword"] not in reasons:
            reasons[r["keyword"]] = {"code": "capacity", "text":
                                     f"일정 자리가 다 참 (네이버 {len(rows['naver'])}/{cap['naver']}, 워드프레스 {len(rows['wordpress'])}/{cap['wordpress']}), 예비 {w['reserve_count']}개도 이미 참"}

    items = []
    for platform, plist, per_day in (("naver", rows["naver"], ppd["naver"]), ("wordpress", rows["wordpress"], ppd["wordpress"])):
        for i, r in enumerate(plist):
            d = monday + timedelta(days=i % 7)  # 우선순위 높은 것부터 월·화·수… 순으로 퍼뜨린다
            items.append({**r, "platform": platform, "role": "scheduled", "assign_note": notes.get(id(r)),
                          "publish_date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()]})
    items.sort(key=lambda x: (x["publish_date"], 0 if x["platform"] == "naver" else 1))
    for r in reserve:
        items.append({**r, "platform": None, "role": "reserve", "publish_date": None, "weekday": None})
    shortage = {"naver": cap["naver"] - len(rows["naver"]), "wordpress": cap["wordpress"] - len(rows["wordpress"]),
                "reserve": w["reserve_count"] - len(reserve)}
    return items, shortage, reasons


# ---------- 2단계: 정보·수익 두 갈래 배정 ----------

def source_platform(src):
    """기존 글 출처(색인의 source) → 플랫폼. 알 수 없으면 None."""
    s = str(src or "")
    if "naver" in s:
        return "naver"
    if s == "shop" or "wp" in s or "wordpress" in s:
        return "wordpress"
    return None


def pair_posts(idx=None) -> list:
    """짝 글 후보인 '이미 쓴 정보글' 목록(색인의 기존 글 중 제품 선택이 아닌 것, 지난 기획 제외)."""
    import fingerprint as fpm
    idx = idx or fpm.FingerprintIndex()
    out = []
    for key, e in sorted(idx.posts.items()):
        if not idx._cur(e) or e.get("source") == "plan" or idx.post_track(e) == "money":
            continue
        out.append({"title": e.get("title") or key, "platform": source_platform(e.get("source")),
                    "target": idx.tgt(e), "group": e.get("topic"), "problem": e.get("problem")})
    return out


def annotate_index_fields(rows: list, idx=None) -> None:
    """후보 행에 색인의 갈래(track)·대상 이름·넓은 주제를 달아 둔다(색인에 없으면 건드리지 않는다). Claude 호출 없음."""
    import fingerprint as fpm
    idx = idx or fpm.FingerprintIndex()
    for r in rows:
        if idx.type_of(r["keyword"]) == "brand":
            r["kw_type"] = "brand"          # 브랜드·모델명 키워드 (제품 판정에서 정해짐) → 규칙 제외 유형
        t = idx.track_of(r["keyword"])
        if t:
            r["track"] = t
            tg = idx.target_of(r["keyword"])
            if tg:
                r["kw_target"] = r["kw_topic"] = tg
            if idx.topic_group_of(r["keyword"]):
                r["kw_topic_group"] = spelling.canon_topic_group(idx.topic_group_of(r["keyword"]), r["keyword"])
            r["kw_detail"], r["kw_problem"] = idx.detail_of(r["keyword"]), idx.problem_of(r["keyword"])
            r["product"], r["solves"] = idx.product_of(r["keyword"]), idx.solves_of(r["keyword"])


def target_match(a: str, b: str) -> bool:
    """같은 대상인가: 이름이 같거나, 한쪽이 다른 쪽을 포함(두 글자 이상). 장소가 다른 이름(욕실 ↔ 싱크대)은 서로 포함하지 않으므로 자연히 다르다."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 2 and short in long_


def solves_of_row(r: dict):
    """수익글 r 이 푸는 문제 (대상, 문제). 아직 판정 안 됐거나 푸는 문제가 없으면 None."""
    sv = r.get("solves")
    if isinstance(sv, dict) and sv.get("target") and sv.get("problem"):
        return sv["target"], sv["problem"]
    return None


def choose_pair(r: dict, platform, sched_info: list, posts: list, used: dict = None, allow_plan: bool = True):
    """수익글 r 의 짝 정보글. r 이 푸는 문제(solves: 대상·문제)와 '같은 대상·같은 문제'인 정보글일 때만 연결한다(넓은 주제만 같은 것은 연결하지 않음).
    ① 같은 주 일정의 정보글 ② 이미 쓴 글. 같은 플랫폼을 먼저(다른 플랫폼에는 내부 링크를 걸 수 없으므로), 같은 플랫폼 안에서는 일정 → 기존 글.
    platform 이 None 이면 플랫폼은 따지지 않는다. sched_info 의 각 항목은 'platform' 필드를 가진 정보글 행.
    → {"type": "plan", "keyword": ...} | {"type": "post", "title": ...} | None"""
    sv = solves_of_row(r)
    if not sv:
        return None
    tgt, prob = sv
    used = used or {}
    cands = []
    if allow_plan:
        for i in sched_info:
            if i.get("kw_problem") == prob and target_match(tgt, i.get("kw_target") or i.get("kw_topic")):
                cross = 0 if platform is None or i.get("platform") == platform else 1
                cands.append(((cross, 0, used.get(i["keyword"], 0), -(i.get("expected_traffic") or 0), i["keyword"]),
                              {"type": "plan", "keyword": i["keyword"], "platform": i.get("platform"), "cross_platform": bool(cross)}))
    for p in posts:
        if p.get("problem") == prob and target_match(tgt, p["target"]):
            cross = 0 if platform is None or p["platform"] == platform else 1
            cands.append(((cross, 1, 0, 0, p["title"]),
                          {"type": "post", "title": p["title"], "platform": p["platform"], "cross_platform": bool(cross)}))
    return min(cands, key=lambda c: c[0])[1] if cands else None


def spread_days(n: int) -> list:
    """한 플랫폼의 수익글 n 편이 놓일 요일(0=월). 하루 1편, 요일에 고르게(간격이 최대가 되게)."""
    if n <= 0:
        return []
    if n == 1:
        return [2]
    if n >= 7:
        return list(range(7))
    return [round(i * 6 / (n - 1)) for i in range(n)]


def schedule_dates(rows: dict, ppd: dict, plan_info: dict, fallback_pair) -> dict:
    """일정 요일 정하기. rows[플랫폼] = {"info": [...], "money": [...]} (우선순위 순). → {id(행): 요일 번호(0=월)}
    수익글은 하루 1편(spread_days). plan_info[id(수익글)] = 짝으로 잡은 같은 주 정보글 행(없으면 None):
    그 정보글을 수익글과 같은 날이거나 앞 날에 둔다. 날짜를 맞출 수 없으면 짝을 기존 글 짝(fallback_pair)으로 바꾼다."""
    plat_of = {id(r): p for p in rows for t in ("info", "money") for r in rows[p][t]}
    day, money_day, slots = {}, {}, {}
    for pl in rows:
        md = spread_days(len(rows[pl]["money"]))
        # 같은 주 정보글 짝이 없는 수익글이 이른 요일, 있는 것이 늦은 요일(그 앞에 정보글을 둘 자리가 있도록)
        order = [r for r in rows[pl]["money"] if plan_info.get(id(r)) is None] + [r for r in rows[pl]["money"] if plan_info.get(id(r)) is not None]
        for r, d in zip(order, md):
            day[id(r)] = money_day[id(r)] = d
        cap = {d: ppd[pl] - (1 if d in md else 0) for d in range(7)}
        slots[pl] = [d for k in range(ppd[pl]) for d in range(7) if k < cap[d]]
    placed = set()

    def take_slot(pl, max_day=6):
        for j, d in enumerate(slots[pl]):
            if d <= max_day:
                return slots[pl].pop(j)
        return None

    for pl in rows:       # 짝이 있는 수익글을 요일 순으로: 짝 정보글을 그 날 이전 자리에 먼저 둔다
        for m in sorted((r for r in rows[pl]["money"] if plan_info.get(id(r)) is not None), key=lambda r: money_day[id(r)]):
            info = plan_info[id(m)]
            if id(info) in placed:
                ok = day[id(info)] <= money_day[id(m)]
            else:
                d = take_slot(plat_of[id(info)], money_day[id(m)])
                ok = d is not None
                if ok:
                    day[id(info)] = d
                    placed.add(id(info))
            if not ok:
                plan_info[id(m)] = None
                m["pair_with"] = fallback_pair(m, pl)
    for pl in rows:
        for r in rows[pl]["info"]:
            if id(r) not in placed:
                d = take_slot(pl)
                if d is not None:
                    day[id(r)] = d
                    placed.add(id(r))
    return day


def assign_tracks(pool: list, w: dict, monday: date, roles: dict) -> tuple:
    """일정 배정(2단계): 갈래(정보 info / 수익 money)별 정원대로 채운다. → (items, shortage, reasons)
    1) 정보글을 플랫폼의 남은 자리 비율대로 → 2) 수익글 후보를 (등급, 짝 글 있음, 예상 유입) 순으로 → 3) 예비를 갈래별로.
    한 갈래가 모자라면 다른 갈래로 채우지 않는다(shortage 에 갈래별로 적는다). 한도(같은 대상·넓은 주제·카테고리·해충·넓고 좁은 관계)는 그대로.
    수익글 날짜는 플랫폼마다 하루 1편까지 요일에 고르게(schedule_dates)."""
    th, ppd, q = w["similarity_threshold"], w["posts_per_day"], w["track_quota"]
    PL = ("naver", "wordpress")
    total = lambda r: r["score"]["total"]
    tier = lambda r: 2 if r.get("grade") == "S" else (1 if r.get("grade") == "A" else 0)
    traffic = lambda r: r.get("expected_clicks") or 0      # 갈래 안의 순위 = 주간 예상 클릭 (등급 S·A 는 통과 조건으로만 씀)
    rows = {p: {"info": [], "money": []} for p in PL}
    used, reasons, placed = [], {}, set()
    cat_max, top_max, grp_max = w["max_per_category_per_platform"], w["max_per_topic"], w["max_per_topic_group"]
    cat_n, top_n, grp_n, capblock = {}, {}, {}, {}
    det_max, money_tgt_max = w.get("max_per_detail"), w.get("max_money_per_target")
    det_n, money_tgt_n = {}, {}
    pest_max, pest_words = w.get("max_pest_per_platform"), w.get("pest_words") or []
    pest_n = {}
    posts = pair_posts()

    def take(r, platform: str, tr: str) -> bool:
        for u in used:
            if similar(r["keyword"], u["keyword"], th):
                reasons[r["keyword"]] = {"code": "similar", "text": f"이미 배정된 '{u['keyword']}'와 거의 같은 키워드라 제외"}
                return False
        cw = r.get("cover_with") or ()
        for u in used:
            if u["keyword"] in cw:
                capblock[r["keyword"]] = f"'{u['keyword']}'와 넓고 좁은 관계(같은 주에 함께 넣지 않음)"
                return False
        cat, top, grp = r.get("kw_category"), r.get("kw_topic"), r.get("kw_topic_group")
        if cat and cat_n.get((platform, cat), 0) >= cat_max:
            capblock[r["keyword"]] = f"같은 카테고리 '{cat}'가 {PLATFORM_KO[platform]}에 이미 {cat_max}편 (주당 한도)"
            return False
        if top and top_n.get(top, 0) >= top_max:
            capblock[r["keyword"]] = f"같은 대상 '{top}'이 이미 {top_max}편 (주당 한도)"
            return False
        if grp and grp_n.get(grp, 0) >= grp_max:
            capblock[r["keyword"]] = f"같은 넓은 주제 '{grp}'가 이미 {grp_max}편 (주당 한도)"
            return False
        det = r.get("kw_detail")
        det = det if det and det != "일반" else None
        if det and det_max and det_n.get(det, 0) >= det_max:
            capblock[r["keyword"]] = f"같은 세부 '{det}' 글이 이미 {det_max}편 (주당 한도)"
            return False
        mt = (r.get("kw_target") or r.get("kw_topic")) if tr == "money" else None
        if mt and money_tgt_max and money_tgt_n.get(mt, 0) >= money_tgt_max:
            capblock[r["keyword"]] = f"같은 대상 '{mt}'의 수익글이 이미 {money_tgt_max}편 (주당 한도)"
            return False
        pest = pest_max is not None and is_pest(r, pest_words)
        if pest and pest_n.get(platform, 0) >= pest_max:
            capblock[r["keyword"]] = f"해충 글 줄이기: {PLATFORM_KO[platform]}에 해충 글이 이미 {pest_max}편 (주당 한도)"
            return False
        if pest:
            pest_n[platform] = pest_n.get(platform, 0) + 1
        for dct, key in ((cat_n, (platform, cat) if cat else None), (top_n, top), (grp_n, grp), (det_n, det), (money_tgt_n, mt)):
            if key:
                dct[key] = dct.get(key, 0) + 1
        rows[platform][tr].append(r)
        used.append(r)
        placed.add(id(r))
        return True

    def fill_track(cands: list, tr: str) -> None:
        for r in cands:
            room = {p: q[p][tr] - len(rows[p][tr]) for p in PL}
            if room["naver"] <= 0 and room["wordpress"] <= 0:
                break
            order = sorted((p for p in PL if room[p] > 0), key=lambda p: (-room[p] / max(1, q[p][tr]), p != "naver"))
            for target in order:
                if take(r, target, tr) or r["keyword"] in reasons:
                    break

    info_pool = sorted((r for r in pool if r.get("track") != "money"),
                       key=lambda r: (traffic(r), has_word(r["keyword"], roles["naver"]), total(r)), reverse=True)
    money_all = [r for r in pool if r.get("track") == "money"]
    # 수익글은 '살림 문제를 풀 때 쓰는 제품'이어야 한다: 푸는 문제(solves)가 없다고 판정된 것(자재·가구 부품 등)은 후보에서 뺀다.
    # (아직 판정 안 된 것 = 'solves' 키가 없고 product 도 없는 것은 그대로 둔다)
    nosolve = [r for r in money_all if "product" in r and r.get("product") is not None and not r.get("solves")]
    for r in nosolve:
        reasons[r["keyword"]] = {"code": "nosolve", "text": "수익글 조건 미달: 살림 문제를 푸는 제품이 아님(자재·가구 부품 등, solves 없음)"}
    money_pool = [r for r in money_all if r not in nosolve]
    def pair_rank(r):
        if choose_pair(r, None, sched_info, posts, allow_plan=True) is None:
            return 0
        return 2 if choose_pair(r, None, sched_info, [], allow_plan=True) is not None else 1
    if w.get("money_pair_first"):
        # 2.5단계 방식: 정보글을 먼저 채우고, 수익글은 '짝 글이 같은 주 일정에 있음(2) > 이미 쓴 글과 짝(1) > 짝 없음(0)' 먼저, 그다음 예상 클릭
        fill_track(info_pool, "info")
        sched_info = [{**r, "platform": p} for p in PL for r in rows[p]["info"]]
        prank = {r["keyword"]: pair_rank(r) for r in money_pool}
        money_sorted = sorted(money_pool, key=lambda r: (prank[r["keyword"]], traffic(r), total(r)), reverse=True)
        fill_track(money_sorted, "money")
    else:
        # 3단계 기본: 정보·수익 후보를 합쳐 예상 클릭이 큰 순서로 자리를 잡는다 → 같은 대상·주제 한도에 걸리면 예상 클릭이 큰 쪽이 남는다
        # (갈래별 정원은 그대로 — 갈래마다 자리가 차면 그 갈래 후보는 건너뜀). 짝 글은 자리가 정해진 뒤에 고른다.
        # 수익글은 '예상 클릭 × 가점'으로 순위를 매긴다(가점: weekly.pair_bonus — 같은 주 정보글과 짝 / 이미 쓴 글과 짝 / 짝 없음).
        # 같은 주 정보글 = 정보글 정원 안에 들 만한 상위 정보글 후보로 미리 어림한다. plan 의 expected_clicks 는 가점 없는 값 그대로.
        pb = w["pair_bonus"]
        info_quota = sum(q[p]["info"] for p in PL)
        near_info = [{**r, "platform": "naver"} for r in info_pool[:info_quota]]
        def bonus_of(r):
            if choose_pair(r, None, near_info, [], allow_plan=True) is not None:
                return pb["plan"]
            return pb["post"] if choose_pair(r, None, [], posts, allow_plan=True) is not None else pb["none"]
        rank_pts = {r["keyword"]: traffic(r) * bonus_of(r) for r in money_pool}
        score_of = lambda r: rank_pts[r["keyword"]] if r.get("track") == "money" and r["keyword"] in rank_pts else traffic(r)
        mixed = sorted(info_pool + money_pool, key=lambda r: (score_of(r), total(r)), reverse=True)
        for r in mixed:
            fill_track([r], "money" if r.get("track") == "money" else "info")
        sched_info = [{**r, "platform": p} for p in PL for r in rows[p]["info"]]
        prank = {r["keyword"]: pair_rank(r) for r in money_pool}
        money_sorted = sorted(money_pool, key=lambda r: (score_of(r), prank[r["keyword"]], total(r)), reverse=True)

    reserve = {"info": [], "money": []}                             # 3) 예비 (갈래별)
    reserve_pest = 0
    for tr, src in (("info", info_pool), ("money", money_sorted)):
        for r in src:
            if len(reserve[tr]) >= q["reserve"][tr]:
                break
            if id(r) in placed or r["keyword"] in reasons or r["keyword"] in capblock:
                continue
            if pest_max is not None and is_pest(r, pest_words):
                if reserve_pest >= pest_max:
                    capblock[r["keyword"]] = f"해충 글 줄이기: 예비에도 해충 글이 이미 {pest_max}편"
                    continue
                reserve_pest += 1
            allr = used + reserve["info"] + reserve["money"]
            rdet = r.get("kw_detail")
            if rdet and rdet != "일반" and det_max and sum(1 for u in allr if u.get("kw_detail") == rdet) >= det_max:
                capblock[r["keyword"]] = f"같은 세부 '{rdet}' 글이 일정·예비에 이미 {det_max}편 (주당 한도)"
                continue
            rmt = (r.get("kw_target") or r.get("kw_topic")) if tr == "money" else None
            if rmt and money_tgt_max and sum(1 for u in allr if u.get("track") == "money" and (u.get("kw_target") or u.get("kw_topic")) == rmt) >= money_tgt_max:
                capblock[r["keyword"]] = f"같은 대상 '{rmt}'의 수익글이 일정·예비에 이미 {money_tgt_max}편 (주당 한도)"
                continue
            if any(similar(r["keyword"], u["keyword"], th) for u in allr):
                reasons[r["keyword"]] = {"code": "similar", "text": "배정된 키워드와 거의 같아 예비에서도 제외"}
                continue
            if any(u["keyword"] in (r.get("cover_with") or ()) for u in allr):
                capblock[r["keyword"]] = "배정된 키워드와 넓고 좁은 관계라 예비에서도 제외 (같은 주에 함께 넣지 않음)"
                continue
            reserve[tr].append(r)
    for tr in reserve:
        for r in reserve[tr]:
            reasons[r["keyword"]] = {"code": "reserve", "text": f"일정 자리가 다 차서 예비({'정보' if tr == 'info' else '수익'}글)로 배정"}
    for r in pool:
        if id(r) not in placed and r["keyword"] not in reasons and r["keyword"] in capblock:
            reasons[r["keyword"]] = {"code": "cap", "text": capblock[r["keyword"]]}
        elif id(r) not in placed and r["keyword"] not in reasons:
            reasons[r["keyword"]] = {"code": "capacity", "text": f"{'수익' if r.get('track') == 'money' else '정보'}글 일정·예비 자리가 이미 참"}

    # 짝 글: 같은 주 일정의 정보글(같은 플랫폼 먼저) → 이미 쓴 글. 같은 주 정보글과 짝이면 정보글을 먼저(같은 날이거나 앞 날) 발행.
    info_row = {(p, r["keyword"]): r for p in PL for r in rows[p]["info"]}
    plan_info, used_cnt = {}, {}
    for p in PL:
        for m in rows[p]["money"]:
            pw = choose_pair(m, p, sched_info, posts, used_cnt)
            m["pair_with"] = pw
            plan_info[id(m)] = None
            if pw and pw["type"] == "plan":
                used_cnt[pw["keyword"]] = used_cnt.get(pw["keyword"], 0) + 1
                plan_info[id(m)] = info_row[(pw["platform"], pw["keyword"])]
    fallback = lambda m, p: choose_pair(m, p, [], posts, None, allow_plan=False)
    day = schedule_dates(rows, ppd, plan_info, fallback)
    items = []
    for p in PL:
        for tr in ("info", "money"):
            for r in rows[p][tr]:
                d = monday + timedelta(days=day.get(id(r), 0))
                items.append({**r, "platform": p, "role": "scheduled", "track": tr, "assign_note": None,
                              "publish_date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()]})
    items.sort(key=lambda x: (x["publish_date"], 0 if x["platform"] == "naver" else 1, 0 if x["track"] == "info" else 1))
    for tr in ("info", "money"):
        for r in reserve[tr]:
            extra = {"pair_with": choose_pair(r, None, sched_info, posts)} if tr == "money" else {}
            items.append({**r, "platform": None, "role": "reserve", "track": tr, "publish_date": None, "weekday": None, **extra})
    tracks = {p: {t: q[p][t] - len(rows[p][t]) for t in ("info", "money")} for p in PL}
    tracks["reserve"] = {t: q["reserve"][t] - len(reserve[t]) for t in ("info", "money")}
    shortage = {"naver": sum(tracks["naver"].values()), "wordpress": sum(tracks["wordpress"].values()),
                "reserve": sum(tracks["reserve"].values()), "tracks": tracks,
                "info": sum(v["info"] for v in tracks.values()), "money": sum(v["money"] for v in tracks.values())}
    return items, shortage, reasons


# ---------- 재기획 R1: 과거 글 시험 결과로 바꾼 뽑는 기준 (filter.json 'r1', enabled 일 때만) ----------

R1_LAST = {}      # 마지막 assign_r1 결과 요약(plan 파일의 r1 칸에 기록)


def assign_r1(pool: list, w: dict, monday: date, roles: dict) -> tuple:
    """R1 배정 → (items, shortage, reasons). 네이버 21편(제철 물건 × 시기 순) + 싼세계 14편(정보 10 = 중심 낱말 주제 묶음 3개 이내, 수익 4) + 예비(네이버식 5·싼세계식 5).
    한도(같은 대상 2편·해충 2편·세부 2편·같은 카테고리·넓은 주제·넓고 좁은 관계·비슷한 키워드 겹침)는 그대로 적용한다.
    넓은 주제는 플랫폼별로 센다(네이버 4편, 싼세계 5편). 정보 15·수익 6 정원과 수익글 하루 1편 규칙은 쓰지 않는다."""
    import r1 as r1m
    c, nv, wp = w["r1"], w["r1"]["naver"], w["r1"]["wordpress"]
    th = w["similarity_threshold"]
    PL = ("naver", "wordpress")
    sig = lambda r: (r.get("r1") or {})
    nscore = lambda r: r1m.naver_score(sig(r), c)
    ordkey = lambda r: (-nscore(r), -(sig(r).get("ratio") or 0), r["keyword"])
    cat_max = {"naver": w["max_per_category_per_platform"], "wordpress": wp.get("max_per_category", w["max_per_category_per_platform"])}
    grp_max = {"naver": w["max_per_topic_group"], "wordpress": wp.get("max_per_topic_group", w["max_per_topic_group"])}
    top_max, det_max, money_tgt_max = w["max_per_topic"], w.get("max_per_detail"), w.get("max_money_per_target")
    pest_max, pest_words = w.get("max_pest_per_platform"), w.get("pest_words") or []
    st = {"used": [], "rows": {p: [] for p in PL}, "rsv": {p: [] for p in PL}, "cat": {}, "grp": {}, "top": {}, "det": {}, "mt": {}, "pest": {}}
    reasons, capblock = {}, {}
    posts = pair_posts()

    def snap():
        return {k: (list(v) if isinstance(v, list) else {kk: (list(vv) if isinstance(vv, list) else vv) for kk, vv in v.items()}) for k, v in st.items()}

    def restore(sn):
        for k, v in sn.items():
            st[k] = list(v) if isinstance(v, list) else {kk: (list(vv) if isinstance(vv, list) else vv) for kk, vv in v.items()}
        # rows / rsv 는 {플랫폼: 목록} 모양이라 위 복원으로 충분하다

    def take(r, platform: str, tr: str, reserve: bool = False) -> bool:
        kw = r["keyword"]
        for u in st["used"]:
            if similar(kw, u["keyword"], th):
                reasons[kw] = {"code": "similar", "text": f"이미 배정된 '{u['keyword']}'와 거의 같은 키워드라 제외"}
                return False
        cw = r.get("cover_with") or ()
        for u in st["used"]:
            if u["keyword"] in cw:
                capblock[kw] = f"'{u['keyword']}'와 넓고 좁은 관계(같은 주에 함께 넣지 않음)"
                return False
        cat, top, grp = r.get("kw_category"), r.get("kw_topic"), r.get("kw_topic_group")
        if cat and st["cat"].get((platform, cat), 0) >= cat_max[platform]:
            capblock[kw] = f"같은 카테고리 '{cat}'가 {PLATFORM_KO[platform]}에 이미 {cat_max[platform]}편 (주당 한도)"
            return False
        if top and st["top"].get(top, 0) >= top_max:
            capblock[kw] = f"같은 대상 '{top}'이 이미 {top_max}편 (주당 한도)"
            return False
        if grp and st["grp"].get((platform, grp), 0) >= grp_max[platform]:
            capblock[kw] = f"같은 넓은 주제 '{grp}'가 {PLATFORM_KO[platform]}에 이미 {grp_max[platform]}편 (주당 한도)"
            return False
        det = r.get("kw_detail")
        det = det if det and det != "일반" else None
        if det and det_max and st["det"].get(det, 0) >= det_max:
            capblock[kw] = f"같은 세부 '{det}' 글이 이미 {det_max}편 (주당 한도)"
            return False
        mt = (r.get("kw_target") or r.get("kw_topic")) if tr == "money" else None
        if mt and money_tgt_max and st["mt"].get(mt, 0) >= money_tgt_max:
            capblock[kw] = f"같은 대상 '{mt}'의 수익글이 이미 {money_tgt_max}편 (주당 한도)"
            return False
        pest = pest_max is not None and is_pest(r, pest_words)
        if pest and st["pest"].get(platform, 0) >= pest_max:
            capblock[kw] = f"해충 글 줄이기: {PLATFORM_KO[platform]}에 해충 글이 이미 {pest_max}편 (주당 한도)"
            return False
        if pest:
            st["pest"][platform] = st["pest"].get(platform, 0) + 1
        for d, key in ((st["cat"], (platform, cat) if cat else None), (st["top"], top), (st["grp"], (platform, grp) if grp else None),
                       (st["det"], det), (st["mt"], mt)):
            if key:
                d[key] = d.get(key, 0) + 1
        (st["rsv"] if reserve else st["rows"])[platform].append(r)
        st["used"].append(r)
        r["_r1_track"] = tr
        return True

    is_money = lambda r: r.get("track") == "money"
    placed = lambda r: any(r is u for u in st["used"])

    def money_ok(r):
        """수익글 후보: '살림 문제를 푸는 제품'(solves)이어야 하지만, 브랜드·모델명 키워드는 제품 자체를 분석하는 글이라 solves 를 따지지 않는다(싼세계만)."""
        if not is_money(r):
            return False
        if r.get("kw_type") == "brand":
            return bool(wp.get("money_allow_brand", True))
        if "product" in r and r.get("product") is not None and not r.get("solves"):
            reasons[r["keyword"]] = {"code": "nosolve", "text": "수익글 조건 미달: 살림 문제를 푸는 제품이 아님(자재·가구 부품 등, solves 없음)"}
            return False
        return True

    brand = lambda r: r.get("kw_type") == "brand"
    # ---- 싼세계 정보 10 (중심 낱말 주제 묶음) ----
    wp_info_pool = [r for r in pool if sig(r).get("cw") and (sig(r).get("cw_volume") or 0) >= wp["topic_volume_min"]
                    and not brand(r)]
    by_topic = {}
    for r in wp_info_pool:
        by_topic.setdefault(sig(r)["cw"], []).append(r)
    tscore = lambda cwk: (-r1m.topic_score(sig(by_topic[cwk][0]).get("cw_sig") or {}, c),
                          -(sig(by_topic[cwk][0]).get("cw_volume") or 0), cwk)
    topics = sorted(by_topic, key=tscore)
    lo, hi = wp["per_topic"]
    chosen, topic_rows = [], {}
    for cwk in topics:
        if len(chosen) >= wp["max_topics"]:
            break
        sn = snap()
        got = []
        for r in sorted(by_topic[cwk], key=ordkey):
            if len(got) >= lo:
                break
            if r["keyword"] in reasons or placed(r):
                continue
            if take(r, "wordpress", "info"):
                got.append(r)
        if len(got) >= lo:
            chosen.append(cwk)
            topic_rows[cwk] = got
        else:
            restore(sn)             # 3편을 못 채우는 주제는 쓰지 않는다
    # 남은 자리(정보 10 - 채운 수)는 주제 순서대로 한 편씩 더(주제당 최대 5편)
    want_info = wp["info"]
    while sum(len(v) for v in topic_rows.values()) < want_info and chosen:
        grew = False
        for cwk in chosen:
            if sum(len(v) for v in topic_rows.values()) >= want_info or len(topic_rows[cwk]) >= hi:
                continue
            for r in sorted(by_topic[cwk], key=ordkey):
                if placed(r) or r["keyword"] in reasons:
                    continue
                if take(r, "wordpress", "info"):
                    topic_rows[cwk].append(r)
                    grew = True
                    break
        if not grew:
            break
    # ---- 싼세계 수익 4 ----
    n_money = 0
    for r in sorted((r for r in pool if not placed(r) and money_ok(r)), key=ordkey):
        if n_money >= wp["money"]:
            break
        if take(r, "wordpress", "money"):
            n_money += 1
    # ---- 네이버 21 ----
    nav_pool = [r for r in pool if not placed(r) and not brand(r) and r["keyword"] not in reasons]
    obj = lambda r: sig(r).get("shape") in nv["object_shapes"]
    timed = lambda r: r1m.is_timed(sig(r), c)
    eg = lambda r: sig(r).get("shape") in nv["evergreen_shapes"]
    nrows = st["rows"]["naver"]
    nav_track = lambda r: "money" if (is_money(r) and (r.get("solves") or "product" not in r or r.get("product") is None)) else "info"

    def fill(cands, until, note=None):
        for r in sorted(cands, key=ordkey):
            if until():
                break
            if placed(r) or r["keyword"] in reasons:
                continue
            if take(r, "naver", nav_track(r)) and note:
                r["_r1_note"] = note

    cnt_obj = lambda: sum(1 for r in nrows if obj(r) and timed(r))
    cnt_timed = lambda: sum(1 for r in nrows if timed(r))
    full = lambda: len(nrows) >= nv["count"]
    fill([r for r in nav_pool if obj(r) and timed(r)], lambda: cnt_obj() >= nv["min_object"] or full())
    fill([r for r in nav_pool if timed(r)], lambda: cnt_timed() >= nv["min_timed"] or full())
    fill([r for r in nav_pool if eg(r) and not timed(r)], full)
    fill([r for r in nav_pool if eg(r)], full)
    fill([r for r in nav_pool if timed(r)], full, "상시 급한 증상·방법 후보가 모자라 시기 조건에 맞는 글로 채움")
    fill(nav_pool, full, "조건에 맞는 후보가 모자라 점수 순으로 채움")
    # ---- 예비: 네이버식 5 · 싼세계식 5 ----
    rest = lambda: [r for r in pool if not placed(r) and r["keyword"] not in reasons]
    for r in sorted((r for r in rest() if not brand(r)), key=ordkey):
        if len(st["rsv"]["naver"]) >= nv["reserve"]:
            break
        take(r, "naver", nav_track(r), reserve=True)
    wp_res = [r for r in rest() if sig(r).get("cw") in chosen and not brand(r)]
    other_topic = [r for r in rest() if sig(r).get("cw") in by_topic and sig(r).get("cw") not in chosen and not brand(r)]
    other_topic.sort(key=lambda r: (topics.index(sig(r)["cw"]), ordkey(r)))
    wp_res = sorted(wp_res, key=ordkey) + other_topic + sorted((r for r in rest() if money_ok(r) and all(r is not x for x in wp_res + other_topic)), key=ordkey)
    for r in wp_res:
        if len(st["rsv"]["wordpress"]) >= wp["reserve"]:
            break
        if placed(r):
            continue
        in_topic = sig(r).get("cw") in by_topic and not brand(r)
        take(r, "wordpress", "info" if in_topic else ("money" if money_ok(r) else "info"), reserve=True)

    # ---- 날짜(하루 3편·2편, 우선순위 순으로 월·화·수… / 싼세계는 주제를 돌려 가며) ----
    wp_order = []
    mx = max([len(v) for v in topic_rows.values()] or [0])
    for k in range(mx):
        for cwk in chosen:
            if k < len(topic_rows[cwk]):
                wp_order.append(topic_rows[cwk][k])
    wp_order += [r for r in st["rows"]["wordpress"] if all(r is not x for x in wp_order)]
    order = {"naver": sorted(nrows, key=ordkey), "wordpress": wp_order}
    sched_info = [{**r, "platform": p} for p in PL for r in st["rows"][p] if r.get("_r1_track") == "info"]
    used_cnt = {}
    items = []
    for p in PL:
        for i, r in enumerate(order[p]):
            d = monday + timedelta(days=i % 7)
            tr = r["_r1_track"]
            extra = {}
            if tr == "money":
                pw = choose_pair(r, p, sched_info, posts, used_cnt)
                extra["pair_with"] = pw
                if pw and pw["type"] == "plan":
                    used_cnt[pw["keyword"]] = used_cnt.get(pw["keyword"], 0) + 1
            items.append({**r, "platform": p, "role": "scheduled", "track": tr, "assign_note": r.get("_r1_note"),
                          "publish_date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], **extra,
                          **({"r1_note": wp["money_note"]} if p == "wordpress" and tr == "money" else {})})
    items.sort(key=lambda x: (x["publish_date"], 0 if x["platform"] == "naver" else 1))
    for p in PL:
        for r in st["rsv"][p]:
            tr = r["_r1_track"]
            extra = {"pair_with": choose_pair(r, None, sched_info, posts)} if tr == "money" else {}
            items.append({**r, "platform": None, "role": "reserve", "track": tr, "publish_date": None, "weekday": None, "r1_style": p, **extra,
                          **({"r1_note": wp["money_note"]} if p == "wordpress" and tr == "money" else {})})
    in_rsv = [u for p in PL for u in st["rsv"][p]]
    for r in pool:
        kw = r["keyword"]
        if placed(r):
            if any(r is u for u in in_rsv):
                reasons[kw] = {"code": "reserve", "text": "일정 자리가 다 차서 예비로 배정"}
        elif kw not in reasons and kw in capblock:
            reasons[kw] = {"code": "cap", "text": capblock[kw]}
        elif kw not in reasons:
            reasons[kw] = {"code": "capacity", "text": "R1 순위에서 일정·예비 자리 안에 들지 못함"}
    wrows = st["rows"]["wordpress"]
    info_n = sum(1 for r in wrows if r["_r1_track"] == "info")
    sh = {"naver": nv["count"] - len(nrows), "wordpress": wp["info"] + wp["money"] - len(wrows),
          "reserve": nv["reserve"] + wp["reserve"] - len(in_rsv)}
    sh["tracks"] = {"naver": {"info": 0, "money": 0}, "wordpress": {"info": wp["info"] - info_n, "money": wp["money"] - (len(wrows) - info_n)},
                    "reserve": {"info": 0, "money": 0}}
    sh["info"] = sh["tracks"]["wordpress"]["info"]
    sh["money"] = sh["tracks"]["wordpress"]["money"]
    R1_LAST.clear()
    R1_LAST.update({
        "naver": {"count": len(nrows), "timed": cnt_timed(), "object_timed": cnt_obj(), "object_any": sum(1 for r in nrows if obj(r)),
                  "evergreen": sum(1 for r in nrows if eg(r) and not timed(r))},
        "wordpress": {"info": info_n, "money": len(wrows) - info_n,
                      "topics": [{"center": k, "count": len(topic_rows[k]), "volume": sig(topic_rows[k][0]).get("cw_volume"),
                                  "keywords": [r["keyword"] for r in topic_rows[k]]} for k in chosen],
                      "topic_candidates": len(by_topic)},
        "reserve": {"naver": len(st["rsv"]["naver"]), "wordpress": len(st["rsv"]["wordpress"])}})
    return items, sh, reasons


def _count(plans: list, platform: str) -> dict:
    c = {}
    for p in plans:
        if p["role"] == "scheduled" and p["platform"] == platform:
            c[p.get("category") or "(없음)"] = c.get(p.get("category") or "(없음)", 0) + 1
    return c


def _topic_count(plans: list, field: str = "topic") -> dict:
    c = {}
    for p in plans:
        if p["role"] == "scheduled":
            c[p.get(field) or "(없음)"] = c.get(p.get(field) or "(없음)", 0) + 1
    return c


def kc_ko(hint: str) -> str:
    return {"naver": "해결·정보형", "wordpress": "제품선택형"}.get(hint, "기타")


# ---------- 황금 키워드 은행 ----------

BANK_DROP_KEYS = ("golden",)     # 은행에는 무거운 상위 글 원자료는 넣지 않는다


def load_bank(path: Path) -> list:
    try:
        d = json.loads(path.read_text(encoding="utf-8-sig"))
        return [x for x in d.get("items", []) if isinstance(x, dict) and x.get("keyword") and x.get("score")]
    except (OSError, ValueError, AttributeError):
        return []


def save_bank(path: Path, items: list, today: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.stem + ".prev" + path.suffix))   # 직전 파일 1개 보관
    write_text_retry(path, json.dumps({"updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                       "today": today.isoformat(), "count": len(items), "items": items},
                                      ensure_ascii=False, indent=2) + "\n")


def to_bank_item(r: dict, found_date: str, reason: str, today: date, old_meta: dict = None) -> dict:
    """ranking 한 줄(또는 은행에서 꺼낸 줄) → 은행 항목. 발견일·재확인일·사유를 bank 항목에 적는다."""
    item = {k: v for k, v in r.items() if k not in BANK_DROP_KEYS and k not in ("platform", "role", "publish_date", "weekday",
                                                                              "assign_note", "from_bank")}
    meta = dict(old_meta or {})
    meta.setdefault("found_date", found_date)
    meta.setdefault("last_checked", meta["found_date"])
    meta["reason"] = reason
    meta["times_left_out"] = int(meta.get("times_left_out", 0)) + 1
    meta["last_left_out"] = today.isoformat()
    item["bank"] = meta
    return item


def days_since(iso: str, today: date) -> int:
    try:
        return (today - date.fromisoformat(str(iso)[:10])).days
    except ValueError:
        return 10 ** 6


def refresh_bank(items: list, today: date, gc: dict, publish_fn, recheck_days: int = BANK_RECHECK_DAYS) -> tuple:
    """은행 키워드 중 발견(또는 마지막 재확인) 후 recheck_days 일이 지난 것만 최근 30일 발행·경쟁률을 다시 계산한다.
    publish_fn(keyword, monthly_total) → {posts_30d, rate, capped, ...}. 기준 미달이면 뺀다.
    → (남은 항목, 뺀 기록 [{keyword, reason}], 재확인한 개수)"""
    import golden_metrics as gm
    kept, removed, checked = [], [], 0
    for it in items:
        meta = it.setdefault("bank", {})
        if days_since(meta.get("last_checked") or meta.get("found_date"), today) < recheck_days:
            kept.append(it)
            continue
        checked += 1
        try:
            pub = publish_fn(it["keyword"], it.get("monthly_total") or 0)
        except Exception as e:        # API 오류: 판단할 수 없으니 이번에는 그대로 둔다
            meta["recheck_error"] = (str(e).splitlines() or [""])[0][:100]
            kept.append(it)
            continue
        drop = gm.pre_drop(pub, gc)
        if drop:
            removed.append({"keyword": it["keyword"], "reason": "재확인 탈락: " + drop})
            continue
        missed = (it.get("grade_info") or {}).get("missed_conditions") or []
        new_grade = gm.regrade_by_rate(missed, pub.get("rate"), gc, pub)
        if new_grade == "B":
            removed.append({"keyword": it["keyword"], "reason": f"재확인: 경쟁률 {pub.get('rate')} 로 S·A 기준 미달"})
            continue
        meta.update({"last_checked": today.isoformat(), "recheck": {"posts_30d": pub.get("posts_30d"), "title_30d": pub.get("title_30d"),
                                                                     "top100_title": pub.get("top100_title"), "rate": pub.get("rate"),
                                                                     "prev_grade": it.get("grade"), "grade": new_grade}})
        meta.pop("recheck_error", None)
        it["grade"] = new_grade
        if isinstance(it.get("grade_info"), dict):
            it["grade_info"]["grade"] = new_grade
        kept.append(it)
    return kept, removed, checked


def real_publish_fn(gc: dict = None):
    """실제 블로그 검색 API 로 경쟁을 잰다 (은행 재확인용, compete_mode 에 따라 제목 기준/예전 방식). 쓸 때만 API 객체를 만든다."""
    import collect_metrics as cm
    import golden_metrics as gm
    api = cm.OpenApi()
    return lambda kw, vol: gm.publish_for(api, kw, vol, gc or {})


# ---------- Claude ----------

def top_posts_brief(it: dict) -> list:
    """정밀 분석 때 연 검색 상위 5개 글의 제목·분량(본문은 없음). 기획에서 '상위 글보다 나은 점'을 정하는 근거."""
    posts = (((it.get("golden") or {}).get("top_posts") or {}).get("posts")) or []
    return [{"title": p.get("title"), "chars": p.get("chars"), "images": p.get("images"), "age_days": p.get("age_days"),
             "influencer": p.get("influencer"), "official": p.get("official_badge")} for p in posts[:5] if p.get("title")]


def build_summary(items: list, rules: dict = None) -> list:
    """Claude 에 넘길 것만 추린다. 본문 같은 건 애초에 파일에 없다. rules 가 있으면 제목 근거(길이 한도·시기·자동완성)도 넣는다."""
    return [{
        **({"max_title_chars": ttl.max_len_of(it["platform"], rules),
            "timing": f"{(it.get('timing') or {}).get('label')} — {(it.get('timing') or {}).get('reason')}"} if rules else {}),
        **({"autocomplete": (it.get("autocomplete") or [])[:15]} if it.get("autocomplete") else {}),
        "keyword": it["keyword"],
        "platform": it["platform"] or "reserve",
        "monthly_search": it.get("monthly_total"),
        "saturation": it.get("saturation"),
        "smart_block_titles": it.get("smart_blocks") or [],
        "related_keywords": (it.get("related_keywords") or [])[:20],
        "variant_spellings": it.get("spelling_variants") or [],
        "track": it.get("track") or "info",
        **({"pair_with": it.get("pair_with")} if it.get("track") == "money" else {}),
        **({"note": it["r1_note"]} if it.get("r1_note") else {}),
        "top_posts": top_posts_brief(it),
        "score": (it.get("score") or {}).get("total"),
    } for it in items]


def extract_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        # 설명이 섞이거나 JSON 이 여러 덩어리로 온 경우: 읽을 수 있는 덩어리 중 마지막(정정본)을 쓴다
        dec, found, i = json.JSONDecoder(), [], t.find("{")
        while i != -1:
            try:
                obj, end = dec.raw_decode(t, i)
                if isinstance(obj, dict):
                    found.append(obj)
                i = t.find("{", end)
            except ValueError:
                i = t.find("{", i + 1)
        if found:
            return found[-1]
        raise


class UsageLimitError(RuntimeError):
    """Claude 사용 한도(5시간 한도 등)에 걸려 호출이 거절됨. 일반 호출 실패와 구별해서 다룬다 (진행 저장 후 멈춤)."""


LIMIT_RE = re.compile(r"hit your .{0,30}limit|(?:5-hour|weekly|usage|monthly|session) limit|limit reached|사용 한도", re.I)


_MODELS = {}


def model_for(kind: str) -> str:
    """판정 호출의 모델: 환경변수 LOCALAPP_MODEL_<KIND>(시험용) → filter.json 의 models.<kind> → sonnet.
    kind = fingerprint(지문·대상 이름 정리) | type(유형) | pair(쌍 판정). 기획(글 설계) 호출은 이 함수를 쓰지 않고 항상 MODEL(sonnet)."""
    import os
    env = os.environ.get("LOCALAPP_MODEL_" + kind.upper())
    if env:
        return env
    if not _MODELS:
        try:
            _MODELS.update((json.loads(DEFAULT_FILTER.read_text(encoding="utf-8-sig")).get("models") or {}))
        except (OSError, ValueError):
            _MODELS["_loaded"] = True
    return str(_MODELS.get(kind) or MODEL)


def call_claude(prompt: str, model: str = MODEL, max_turns: str = MAX_TURNS) -> dict:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude 명령어를 찾을 수 없습니다. Claude Code 가 설치되어 있는지 확인하세요.")
    cmd = [exe, "-p", "--model", model, "--max-turns", max_turns, "--output-format", "json",
           "--tools", "", "--no-session-persistence"]
    # 프로젝트 지침(CLAUDE.md)·메모리가 섞이지 않도록 빈 임시 폴더에서 실행한다. 입력은 표준입력(한글 안전).
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True,
                                  timeout=TIMEOUT_SEC, cwd=tmp)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{TIMEOUT_SEC}초가 지나도 응답이 없어 중단했습니다.")
    out = proc.stdout.decode("utf-8", "replace").strip()
    err = proc.stderr.decode("utf-8", "replace").strip()
    try:
        env = json.loads(out)
    except ValueError:
        if LIMIT_RE.search(out + " " + err):
            raise UsageLimitError((err or out)[-300:])
        raise RuntimeError("claude 응답을 읽지 못했습니다.\n" + (err or out)[-800:])
    if isinstance(env, list):
        env = next((e for e in reversed(env) if isinstance(e, dict) and e.get("type") == "result"), {})
    if env.get("is_error") or proc.returncode != 0:
        if LIMIT_RE.search(str(env.get("result") or "") + " " + err):
            raise UsageLimitError(str(env.get("result") or err)[:300])
        raise RuntimeError(f"claude 호출 실패 ({env.get('subtype') or proc.returncode}): "
                           + str(env.get("result") or err)[:500])
    return env


def audit_titles(existing: list, w: dict, prev_keywords: list = None) -> list:
    """중복 비교에 쓸 '이미 쓴 글' 목록: 기존 글 제목·대기열 키워드(dedup_skip_sites 제외) + 지난 기획 키워드."""
    skip_sites = set(w.get("dedup_skip_sites") or [])
    titles, seen_t = [], set()
    for e in existing:
        if e.get("site") in skip_sites:
            continue
        t = " ".join(str(e.get("title") or e.get("focus_keyword") or "").split())
        if t and norm(t) not in seen_t:
            seen_t.add(norm(t))
            titles.append(t)
    for k in prev_keywords or []:
        t = f"(지난주 기획) {k}"
        if norm(k) not in seen_t:
            seen_t.add(norm(k))
            titles.append(t)
    return titles


AUDIT_PROMPT = """너는 생활 살림 블로그의 깐깐한 편집장이다. 이 블로그는 살림하며 겪는 생활 문제를 직접 해결하는 방법을 알려 주고, 필요하면 제품을 추천한다.
아래 [이번 주 후보]는 다음 주에 발행할 글의 키워드다. 하나씩 빠짐없이 검사해라. 도구는 쓰지 말고 바로 답해라.

[이미 쓴 글]
{existing}

[이번 주 후보]
{candidates}

[검사 기준]
A. dup_existing — 이미 쓴 글과 같은 글이 되는가?
   같은 대상 + 같은 문제면 중복이다. 아래 차이는 모두 무시하고 같은 것으로 본다:
   띄어쓰기, 맞춤법·표기(씽크대=싱크대, 화장실=욕실, 세무=스웨이드), '~했을때/~할때/방법/하는법/비용/셀프/제거/해결' 같은 덧말,
   더 넓거나 좁은 같은 말(싱크대 배수구 막힘 ⊂ 싱크대 막힘, 벽에 곰팡이 ⊂ 벽지 곰팡이).
   예: 이미 '싱크대 막힘 원인별로 뚫는 법' 글이 있으면 씽크대막혔을때·싱크대배수구막힘·싱크대배수구는 모두 중복이다.
   대상이 다르면(신발 냄새 ↔ 텀블러 냄새, 차량 스티커 ↔ 차량 성에) 중복이 아니다. 문제가 다르면(변기 교체 ↔ 변기 막힘) 중복이 아니다.
B. dup_of — 이번 주 후보끼리 같은 글이 되는가? 위 A 와 같은 기준. 같으면 목록에서 더 앞에 있는 후보 번호를 적는다.
C. service — 개인이 직접 할 수 없는 공사·시공·방역·수리 의뢰가 검색 목적인가?
   (예: 배관공사, 단열시공, 탄성코트, 정화조, 보일러수리, 방역, 리폼, 매립욕조 → true.
    수전·샤워기·환풍기·배수관·호스·경첩 같은 부품 교체와 그 비용·방법·셀프 키워드, 변기·세면대를 통째로 바꾸는 키워드는 직접 해결 글이므로 false)

후보마다 판정을 적은 뒤, 각 판정이 기준에 맞는지 다시 한 번 확인하고 최종 답만 출력한다.

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 머리말, 코드블록(```)을 붙이지 않는다. 후보 전부를 순서대로 적는다. 형식:
{{"items": [{{"id": "K1", "dup_existing": "E번호 또는 null", "dup_of": "K번호 또는 null", "service": true 또는 false}}]}}
"""


def existing_dup_check(keywords: list, existing: list, w: dict = None, prev_keywords: list = None, index=None) -> dict:
    """후보 키워드가 이미 쓴 글과 같은 글인지 '지문이 같은가'로 판정한다 (1단계: 색인 기반, 코드 비교).
    색인에 없는 키워드·기존 글만 Claude 에 60개씩 묻고, 이미 판정한 것은 다시 묻지 않는다.
    → {"dup": {kw: 기존 글 제목}, "calls", "cost", "judged"}  (사용 한도 → UsageLimitError, 그 밖의 실패 → fingerprint.FingerprintError)"""
    import fingerprint as fpm
    idx = index or fpm.FingerprintIndex()
    c0, k0 = idx.calls, idx.cost
    posts = fpm.existing_post_list(existing, w or {}, prev_keywords)
    judged = idx.ensure_posts(posts) + idx.ensure_keywords(keywords)
    idx.ensure_alias()
    dup = fpm.dup_vs_posts(idx, keywords, {p["key"] for p in posts})
    return {"dup": dup, "calls": idx.calls - c0, "cost": round(idx.cost - k0, 4), "judged": judged, "pair_calls": idx.pair_calls}


def final_audit(items: list, titles: list, w: dict) -> dict:
    """배정이 끝난 일정+예비(약 45개)만 따로 Claude 에 보내 중복·업체 시공형을 한 번 더 검사한다.
    후보 수가 적어 250개를 한꺼번에 볼 때보다 꼼꼼하다. final_audit_passes 번(기본 2) 따로 물어 하나라도 걸리면 뺀다.
    → {"status", "flags": {kw: {"code", "text"}}, "calls", "cost"}"""
    kws = [i["keyword"] for i in items]
    import hashlib
    ckey = hashlib.sha1(json.dumps([kws, titles, int(w.get("final_audit_passes", 1))], ensure_ascii=False).encode("utf-8")).hexdigest()
    cache_path = Path(os.environ.get("LOCALAPP_INDEX_DIR") or OUT_DIR) / "audit_cache.json"
    try:
        acache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        acache = {}
    if ckey in acache:        # 같은 후보·같은 기존 글이면 안전망 제안도 같게(한 번 정한 것은 다시 묻지 않음)
        return {"status": "ok", "flags": acache[ckey]["flags"], "calls": 0, "cost": 0.0, "cached": True}
    cand = "\n".join(f"K{n + 1}. {k}" for n, k in enumerate(kws))
    ex = "\n".join(f"E{n + 1}. {t}" for n, t in enumerate(titles)) or "(없음)"
    flags, calls, cost = {}, 0, 0.0
    for _ in range(int(w.get("final_audit_passes", 1))):
        env = call_claude(AUDIT_PROMPT.format(existing=ex, candidates=cand), MODEL, MAX_TURNS)
        calls += 1
        cost += env.get("total_cost_usd") or 0
        raw = extract_json(env.get("result", ""))
        for it in raw.get("items") or []:
            m = re.match(r"K?(\d+)", str(it.get("id", "")).strip())
            if not m or not (1 <= int(m.group(1)) <= len(kws)):
                continue
            kw = kws[int(m.group(1)) - 1]
            if kw in flags:
                continue
            de = re.match(r"E?(\d+)", str(it.get("dup_existing") or "").strip())
            do = re.match(r"K?(\d+)", str(it.get("dup_of") or "").strip())
            if de and 1 <= int(de.group(1)) <= len(titles):
                flags[kw] = {"code": "published", "text": f"이미 쓴 글과 같은 글 ('{titles[int(de.group(1)) - 1]}') — 최종 검사",
                             "title": titles[int(de.group(1)) - 1]}
            elif do and 1 <= int(do.group(1)) <= len(kws) and kws[int(do.group(1)) - 1] != kw:
                flags[kw] = {"code": "intent", "text": f"'{kws[int(do.group(1)) - 1]}'와 같은 글 — 최종 검사", "dup_of": kws[int(do.group(1)) - 1]}
            elif it.get("service") is True:
                flags[kw] = {"code": "service", "text": "업체 시공·공사·수리 의뢰형 검색 — 최종 검사"}
    # 후보끼리 서로를 가리키면(A→B, B→A) 둘 다 빠지지 않게 앞쪽 하나는 살린다
    for kw, f in list(flags.items()):
        other = f.get("dup_of")
        if other and other in flags and flags[other].get("dup_of") == kw:
            keep = min(kw, other, key=kws.index)
            flags.pop(keep, None)
    acache[ckey] = {"flags": flags}
    write_text_retry(cache_path, json.dumps(acache, ensure_ascii=False))
    return {"status": "ok", "flags": flags, "calls": calls, "cost": round(cost, 4)}


def review_flags(flags: dict, log_fn=None) -> tuple:
    """안전망(final_audit)의 의견은 직접 반영하지 않는다. '같은 글' 의견은 쌍 확인(same/cover/diff)으로, '업체형' 의견은 유형 판정으로 다시 묻고,
    이미 저장된 쌍 답(고정값 포함)이 있으면 그 답이 이긴다. → (확정된 flags, {키워드: 기각 사유}, {"pair_calls", "type_calls"})"""
    import fingerprint as fpm
    import keyword_classify as kc
    idx = fpm.FingerprintIndex()
    c0 = idx.pair_calls
    kept, over, type_calls = {}, {}, 0
    items, meta = [], []
    for kw, f in flags.items():
        if f["code"] == "service":
            continue
        ea = idx.entry(kw)
        if f["code"] == "published":
            t = str(f.get("title") or "")
            key = "(지난 기획) " + t[len("(지난주 기획) "):] if t.startswith("(지난주 기획) ") else t
            eb, tb, bp = idx.entry(key, "p"), idx.text_of(idx.posts.get(key) or {}, key), True
        else:
            eb = idx.entry(f.get("dup_of") or "")
            tb, bp = f.get("dup_of") or "", False
        if not ea or not eb:
            over[kw] = "색인에 없어 쌍 확인을 못 함 — 안전망 의견은 반영하지 않음"
            continue
        items.append((ea, kw, eb, tb, bp))
        meta.append(kw)
    if items:
        for kw, same in zip(meta, idx.resolve(items, log_fn)):
            if same:
                kept[kw] = {**flags[kw], "text": flags[kw]["text"] + " → 쌍 확인으로 확정"}
            else:
                over[kw] = "쌍 확인 결과 같은 글이 아님(또는 저장된 답이 다름) — 안전망 의견 기각"
    svc = [kw for kw, f in flags.items() if f["code"] == "service"]
    known, todo = {}, []
    for kw in svc:       # 한 번 다시 판정한 결과는 색인에 저장해 두고 다시 묻지 않는다
        rc = ((idx.keywords.get(kw) or {}).get("type_recheck") or {})
        if rc.get("ver") == fpm.TYPE_VERSION:
            known[kw] = rc["type"]
        else:
            todo.append(kw)
    if todo:
        res = kc.classify(todo)
        type_calls += 1
        if res.get("status") == "ok":
            for kw in todo:
                known[kw] = res["types"].get(kw)
                if idx.keywords.get(kw) is not None:
                    idx.keywords[kw]["type_recheck"] = {"type": known[kw], "ver": fpm.TYPE_VERSION}
                    if known[kw] == "service":
                        idx.keywords[kw]["kw_type"] = "service"
            idx.save()
    for kw in svc:
        if known.get(kw) == "service":
            kept[kw] = {**flags[kw], "text": flags[kw]["text"] + " → 유형 판정으로 확정"}
        else:
            over[kw] = "유형 판정 결과 업체형이 아님 — 안전망 의견 기각"
    return kept, over, {"pair_calls": idx.pair_calls - c0, "type_calls": type_calls}


def semantic_dedup(pool: list, existing: list, w: dict, roles: dict) -> dict:
    """색인 기반 중복 점검 (1단계). 후보 전부에 지문을 붙이고(새 것만 Claude 에 60개씩), 코드로 비교한다.
    (1) 기존 글과 지문이 같은 후보 (2) 후보끼리 지문이 같은 묶음 (3) 주제 이름(색인에 기록된 것).
    각 후보 행에 'fingerprint' 를 달아 둔다.
    → {"status", "dup_existing": {kw: title}, "same_article": [[kw..]], "topic": {kw: name}, "checked", "checked_keywords", "existing_titles", "usage"}"""
    import fingerprint as fpm
    cands = [r["keyword"] for r in sorted(pool, key=lambda r: -(r.get("monthly_total") or 0))]      # 검색량 큰 순 = 묶음의 대표 순서
    if not cands:
        return {"status": "skipped", "dup_existing": {}, "same_article": [], "topic": {}, "checked": 0}
    idx = fpm.FingerprintIndex()
    idx.pair_budget = int(w.get("max_pair_calls", 30))      # 한 번 실행에서 쌍 확인(Claude) 호출 상한
    posts = fpm.existing_post_list(existing, w, w.get("_prev_keywords"))
    n_posts = idx.ensure_posts(posts, log)
    n_kw = idx.ensure_keywords(cands, log)
    n_ps = idx.ensure_product(cands, log)     # 물건 이름이면 갈래 money, 푸는 살림 문제(solves)도 색인에 저장 (안 물은 것만)
    idx.ensure_alias(log)                     # 대상 이름 정리표 (새 대상 이름이 있을 때만 Claude 에 묻는다)
    dup = fpm.dup_vs_posts(idx, cands, {p["key"] for p in posts}, log)
    # 후보끼리 가까운 쌍 확인은 배정 대상이 될 상위 후보에만: 갈래별 목표(일정+예비)의 plan_pair_top_factor 배. 나머지는 지문 3칸이 같은 것만 같은 글로 본다.
    rest = [k for k in cands if k not in dup]
    tq = w.get("track_quota") or {}
    factor = int(w.get("plan_pair_top_factor", 3))
    by_kw = {r["keyword"]: r for r in pool}
    tier = lambda r: 2 if r.get("grade") == "S" else (1 if r.get("grade") == "A" else 0)
    top = set()
    for tr in ("info", "money"):
        goal = sum(v.get(tr, 0) for v in tq.values()) if tq else len(rest)
        rows_tr = [by_kw[k] for k in rest if (idx.track_of(k) or "info") == tr
                   and not (tr == "money" and idx.product_of(k) is not None and not idx.solves_of(k))]
        rows_tr.sort(key=lambda r: (r.get("expected_clicks") or 0, r["score"]["total"]), reverse=True)
        top |= {r["keyword"] for r in rows_tr[:goal * factor]}
    top_list = [k for k in rest if k in top]
    pc0 = idx.pair_calls
    groups, cover = fpm.cluster_and_cover(idx, top_list, log)
    for g in fpm.cluster_exact(idx, rest):
        if not any(set(g) <= set(h) for h in groups):
            groups.append(g)
    capped = sorted(idx.capped_kw)
    cover_of = {}
    for wide, narrow in cover:
        cover_of.setdefault(wide, []).append(narrow)
        cover_of.setdefault(narrow, []).append(wide)
    topic = {k: spelling.canon_topic_group(idx.topic_group_of(k), k) for k in cands if idx.topic_group_of(k)}     # 넓은 주제 (topic_group)
    for r in pool:
        r["fingerprint"] = idx.fp_of(r["keyword"])
        r["cover_with"] = cover_of.get(r["keyword"], [])
        tg = idx.target_of(r["keyword"])
        if idx.track_of(r["keyword"]):
            r["track"] = idx.track_of(r["keyword"])     # 갈래: 지문의 문제가 '제품 선택(구매)' 이면 수익글(money), 그 외 정보글(info)
        if tg:
            r["kw_target"] = r["kw_topic"] = tg     # '같은 주제 N편' 한도는 정리된 대상 이름 기준
        if idx.topic_group_of(r["keyword"]):
            r["kw_topic_group"] = spelling.canon_topic_group(idx.topic_group_of(r["keyword"]), r["keyword"])
        r["kw_detail"], r["kw_problem"] = idx.detail_of(r["keyword"]), idx.problem_of(r["keyword"])
        r["product"], r["solves"] = idx.product_of(r["keyword"]), idx.solves_of(r["keyword"])
    return {"status": "ok", "dup_existing": dup, "same_article": groups, "topic": topic, "checked": len(cands),
            "checked_keywords": cands, "existing_titles": len(posts),
            "judged_new": {"posts": n_posts, "keywords": n_kw, "product": n_ps},
            "pair_calls": idx.pair_calls, "alias_calls": idx.alias_calls, "ps_calls": idx.ps_calls, "cover": cover,
            "pair_top": {"top_candidates": len(top_list), "rest": len(rest) - len(top_list), "pair_calls_candidates": idx.pair_calls - pc0,
                         "budget": idx.pair_budget, "cap_hit": idx.cap_hit, "skipped_pairs": idx.cap_skipped_pairs},
            "capped": capped,
            "usage": {"total_cost_usd": round(idx.cost, 4), "calls": idx.calls}}


def apply_index_types(rows: list) -> dict:
    """유형(업체형 service·주제 밖 offtopic 등)을 색인 값으로 맞춘다. ranking 의 옛 kw_type 은 kw_type_old 로 남긴다.
    색인에 유형이 없는 후보는 여기서(기획 전에) 판정해 색인에 저장한다.
    → {"judged", "changed": [{keyword, old, new}], "calls", "cost"}"""
    import fingerprint as fpm
    idx = fpm.FingerprintIndex()
    c0, k0 = idx.calls + idx.type_calls, idx.cost
    idx.ensure_product([r["keyword"] for r in rows], log)     # 브랜드·모델명 키워드는 여기서 유형 brand 로 정해진다 (안 물은 것만 질문)
    judged = idx.ensure_types([r["keyword"] for r in rows], log)
    changed = []
    for r in rows:
        t = idx.type_of(r["keyword"])
        if t and t != r.get("kw_type"):
            changed.append({"keyword": r["keyword"], "old": r.get("kw_type"), "new": t})
            r["kw_type_old"], r["kw_type"] = r.get("kw_type"), t
            if t in EXCLUDED_KW_TYPES:
                r["platform_hint"] = None
    return {"status": "ok", "judged": judged, "changed": changed,
            "calls": idx.calls + idx.type_calls - c0, "cost": round(idx.cost - k0, 4)}


def plan_batch(summary: list, guide: str = "") -> tuple:
    """(plans 목록, 사용량 env, 문제 목록). 일시 실패는 한 번 다시 시도."""
    last_err = None
    for attempt in range(2):
        try:
            env = call_claude(PROMPT.format(summary=json.dumps(summary, ensure_ascii=False, indent=1), title_guide=guide))
            plans = extract_json(env.get("result", ""))["plans"]
            break
        except UsageLimitError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            last_err = e
            log(f"  호출 실패({type(e).__name__}){' → 한 번 더 시도' if attempt == 0 else ''}: {str(e).splitlines()[0][:120]}")
            time.sleep(2)
    else:
        return None, None, [f"Claude 호출 실패: {str(last_err).splitlines()[0][:200]}"]
    problems = []
    got, want = [p.get("keyword") for p in plans], [s["keyword"] for s in summary]
    if got != want:
        problems.append(f"키워드 목록/순서가 다름: 요청 {len(want)}개, 응답 {len(got)}개")
    for p in plans:
        if len(p.get("title_candidates") or []) != 6:
            problems.append(f"{p.get('keyword')}: 제목 후보가 6개가 아님")
        if not 4 <= len(p.get("outline", [])) <= 5:
            problems.append(f"{p.get('keyword')}: 소제목이 4~5개가 아님")
    return plans, env, problems


# ---------- 4단계: 제목 고르기 ----------

TITLE_RETRY_PROMPT = """너는 블로그 제목 작가다. 아래 글들은 제목 후보가 규칙 검사나 한 주 틀 배분에서 모자라다. 글마다 새 후보를 써라. 도구는 쓰지 말고 바로 답해라.

[제목 쓰는 법]
{title_guide}

[글 목록]
- intent: 이 키워드의 주된 뜻. 새 후보는 이 뜻 안에서만 쓴다.
- want_patterns: 이번에 쓸 틀. 이 틀마다 후보를 하나씩, 각각 pattern 칸에 그 이름을 적어라 (없으면 6가지 틀 전부).
- rejected: 이미 탈락한 제목과 이유 — 같은 실수를 피하고 비슷한 제목도 쓰지 마라.
- have: 이미 가진 통과 후보 제목 — 같거나 비슷하게 쓰지 마라.
- intent 가 비어 있으면 top_posts·related_keywords·smart_block_titles 를 보고 이 키워드의 주된 뜻을 한 줄로 정해 plans 항목의 intent 칸에 적어라(비어 있지 않으면 쓰지 않는다).
- avoid: 이번 주 다른 글과 겹쳐 쓰면 안 되는 말(예: '4가지') — 후보 제목에 쓰지 마라.
- must_include_one_of: 뜻이 여러 개인 키워드라 후보 제목마다 이 낱말 중 하나가 들어가야 한다.
- 키워드는 제목에서 쉼표나 조사('~를·~는·~이') 없이 바로 명사를 붙이지 마라(나쁜 예: '○○ 제거 방에서 먼저 확인할 5곳'). 키워드 뒤에는 조사나 쉼표를 붙여라.
{summary}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 머리말, 코드블록(```)을 붙이지 않는다. 형식:
{{"plans": [{{"keyword": "...", "intent": "...(비어 있을 때만)", "title_candidates": [{{"title": "...", "pattern": "...", "title_keyword": "..."}}]}}]}}
keyword 는 목록에 있는 값을 그대로 쓰고, 목록의 순서를 지켜라.
"""

INTENT_CHECK_PROMPT = """너는 검색 의도 검수자다. 글마다 intent(그 키워드의 주된 뜻)와 제목 목록이 있다.
intent 와 다른 뜻으로 읽히는 제목(예: intent 가 벽 곰팡이인데 음식 곰팡이로 읽힘)의 번호를 off 에 적어라. 애매하면 통과시켜라(off 에 넣지 않는다). 도구는 쓰지 말고 바로 답해라.

{summary}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 머리말, 코드블록(```)을 붙이지 않는다. 형식:
{{"checks": [{{"keyword": "...", "off": [번호, ...]}}]}}
keyword 는 목록에 있는 값을 그대로 쓰고, 목록의 순서를 지켜라. off 가 없으면 빈 목록.
"""


INTENT_INFO_PROMPT = """너는 검색 의도 분석가다. 키워드마다 상위 글 제목, 연관어, (있으면) intent(주된 뜻)가 있다. intent 가 없으면 상위 글 제목과 연관어로 주된 뜻을 스스로 파악해라. 도구는 쓰지 말고 바로 답해라.
각 키워드에 세 가지를 판정해라.
- buy: 이 키워드로 검색하는 사람이 '집에서 쓸 제품(물건)을 고르려는' 의도이면 true. 주유소·공공장소 같은 집 밖 설비의 원리, 단순 설명·정보, 자재·부품, 업체 의뢰, 음식·몸 관련이 중심이면 false.
- ambiguous: 이 키워드가 서로 다른 뜻으로 검색될 수 있으면 true (예: 푸른곰팡이 = 음식에 핀 곰팡이 / 벽에 핀 곰팡이). 뜻이 하나로 분명하면 false.
- target_words: ambiguous 가 true 일 때만, 제목에 들어가면 intent 의 대상이 드러나는 낱말 2~5개(예: 푸른곰팡이가 음식 쪽이면 ["음식","반찬","김치","빵"]). ambiguous 가 false 면 빈 목록.

{summary}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 코드블록(```)을 붙이지 않는다. 형식:
{{"infos": [{{"keyword": "...", "buy": true, "ambiguous": false, "target_words": []}}]}}
keyword 는 목록에 있는 값을 그대로 쓰고, 목록의 순서를 지켜라.
"""

NATURAL_PROMPT = """너는 블로그 제목 편집자다. 아래 제목들이 '사람이 쓴 제목처럼 자연스러운가'를 검수해라. 도구는 쓰지 말고 바로 답해라.
어색한 제목(bad): 키워드를 억지로 끼워 넣어 문장이 매끄럽지 않거나, 말이 안 통하거나, 낱말을 이어 붙인 듯한 제목.
  어색한 예: '방문 틈새막이 틈 높이 재면 3분', '전등 스위치 교체 비용 vs 셀프', '푸른 곰팡이 생겼을 때 순서', '홀아비 냄새 제거 방에서 먼저 확인할 5곳'
자연스러운 예: '텀블러 냄새 제거, 몸통보다 뚜껑 고무패킹부터 보세요', '김치냉장고 청소는 김장 전에 해야 묵은 냄새가 빠져요', '보일러 동파 방지는 외출 때 끄지 말고 10~15℃로 두세요'
조금 투박해도 뜻이 분명하고 읽히면 통과시켜라. 어색한 제목의 번호만 bad 에 적는다.

{summary}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 코드블록(```)을 붙이지 않는다. 형식:
{{"checks": [{{"keyword": "...", "bad": [번호, ...]}}]}}
keyword 는 목록에 있는 값을 그대로 쓰고, 목록의 순서를 지켜라. bad 가 없으면 빈 목록.
"""


SPACING_VER = 2     # 띄어쓰기 기준이 바뀌면 올린다 → 저장된 띄어쓰기를 한 번 다시 받는다

SPACING_PROMPT = """다음은 한국어 키워드다. 사람이 읽는 자연스러운 띄어쓰기로 바꿔라(글자는 바꾸지 말고 띄어쓰기만). 도구는 쓰지 말고 바로 답해라.
원칙: 사전에 한 낱말로 굳은 말과 제품 이름은 붙여 쓴다. 낱말을 모두 낱개로 쪼개지 마라. 서로 다른 낱말·구(대상 + 동작, 꾸미는 말 + 제품)만 띄운다. 한 낱말이면 그대로 둔다.
예: 웃풍차단 → 웃풍 차단, 벽선반설치 → 벽 선반 설치, 팬트리수납장 → 팬트리 수납장, 프라이팬 → 프라이팬,
    무향섬유유연제 → 무향 섬유유연제, 드럼세탁기고무패킹청소 → 드럼세탁기 고무패킹 청소, 화재감지기설치 → 화재감지기 설치,
    헤파필터공기청정기 → 헤파필터 공기청정기, 푸른곰팡이 → 푸른곰팡이, 곰팡이제거제 → 곰팡이제거제
(틀린 예: 무향 섬유 유연제, 드럼 세탁기 고무 패킹, 화재 감지기, 헤파 필터, 푸른 곰팡이 — 한 낱말·제품명을 쪼갠 것)
{keywords}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 코드블록(```)을 붙이지 않는다. 형식:
{{"plans": [{{"keyword": "...", "spaced": "..."}}]}}
"""


INTENT_STORE_NAME = "intent_info_cache.json"
GOLDEN_CACHE_PATH = BASE / "pipeline" / "golden_cache.json"


def load_intent_store(out_dir: Path) -> dict:
    """키워드별 의도 판정(buy·ambiguous·target_words) 저장소. 같은 키워드는 다시 묻지 않는다."""
    try:
        return json.loads((Path(out_dir) / INTENT_STORE_NAME).read_text(encoding="utf-8")).get("infos") or {}
    except (OSError, ValueError):
        return {}


def save_intent_store(out_dir: Path, store: dict) -> None:
    write_text_retry(Path(out_dir) / INTENT_STORE_NAME, json.dumps({"infos": store}, ensure_ascii=False, indent=1) + "\n")


def attach_cached_top_posts(items: list) -> int:
    """은행에서 온 후보는 상위 5개 글(golden)이 없다 → 이미 저장된 golden_cache.json 에서 읽어 붙인다(읽기만, 새로 수집 안 함). → 붙인 개수"""
    need = [it for it in items if not (((it.get("golden") or {}).get("top_posts") or {}).get("posts"))]
    if not need:
        return 0
    try:
        gc = json.loads(GOLDEN_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    for it in need:
        for k in {it["keyword"], it.get("original_keyword")} - {None}:
            tp = (((gc.get(k) or {}).get("v") or {}).get("top_posts") or {})
            if tp.get("posts"):
                it["golden"] = {**(it.get("golden") or {}), "top_posts": tp}
                n += 1
                break
    return n


def ask_intent_info(units: list, store: dict, out_dir: Path, problems: list, batches: list) -> int:
    """units = [{"keyword", "intent"(없어도 됨), "top_posts":[제목…], "related_keywords":[…]}] 중 저장소에 없는 것만 Claude 에게 한 번에(40개씩) 묻고 저장한다.
    제목 단계(choose_titles)와 기획 전 수익글 확인(money_intent_gate)이 같이 쓴다. → 새로 받은 개수"""
    todo = [u for u in units if u["keyword"] not in store]
    done = 0
    for i in range(0, len(todo), 40):
        chunk = todo[i:i + 40]
        got = _ask_json(INTENT_INFO_PROMPT.format(summary=json.dumps(chunk, ensure_ascii=False, indent=1)), "infos", problems,
                        "intent_info", batches, len(chunk), "sonnet")
        if got is None:
            continue
        for u in chunk:
            g = got.get(u["keyword"]) or {}
            tw = [str(w).strip() for w in (g.get("target_words") or []) if str(w).strip()] if g.get("ambiguous") else []
            store[u["keyword"]] = {"buy": bool(g.get("buy", True)), "ambiguous": bool(g.get("ambiguous")), "target_words": tw}
            done += 1
    if done:
        save_intent_store(out_dir, store)
    return done


def money_intent_gate(items: list, out_dir: Path, problems: list, batches: list, max_rounds: int = 3):
    """기획(본문 설계) 호출 전에 수익글의 검색 의도를 확인한다. 키워드의 상위 글 제목·연관어로 '집에서 쓸 제품을 고르려는 의도'인지 판정,
    아니면 호출하는 쪽이 후보에서 빼고 다시 배정한다. 이 함수는 판정만 한다. → (판정 저장소, 의도 아닌 수익글 키워드 목록)"""
    store = load_intent_store(out_dir)
    attach_cached_top_posts(items)
    units = [{"keyword": it["keyword"], "top_posts": [p["title"] for p in top_posts_brief(it)][:5],
              "related_keywords": (it.get("related_keywords") or [])[:10]} for it in items if it.get("track") == "money"]
    ask_intent_info(units, store, out_dir, problems, batches)
    off = [u["keyword"] for u in units if (store.get(u["keyword"]) or {}).get("buy") is False]
    return store, off


def respace_entry(e: dict, kw: str) -> None:
    """캐시 항목의 후보를 사람이 읽는 띄어쓰기로 고친다(e['spaced'] 가 있을 때): 제목 속 붙여 쓴 키워드를 spaced 로 바꾸고 title_keyword 도 맞춘다.
    intent_ok 의 제목 키도 같이 바꿔 검수 결과를 잃지 않는다. 같은 입력이면 같은 결과."""
    sp = e.get("spaced")
    if not sp or "".join(sp.split()) != "".join(kw.split()) or sp == kw:
        return
    mp = {}
    for c in e["candidates"]:
        forms = {kw} | ({c["title_keyword"]} if c.get("title_keyword") and " " not in c["title_keyword"] and c["title_keyword"] == kw else set())
        if c.get("title_keyword") == sp:
            continue
        t = c["title"]
        for f in sorted(forms, key=len, reverse=True):
            t = t.replace(f, sp)
        t = " ".join(t.split())
        if sp in t:
            mp[c["title"]] = t
            c["title"], c["title_keyword"] = t, sp
    e["intent_ok"] = {mp.get(k, k): v for k, v in (e.get("intent_ok") or {}).items()}


def canon_entry(e: dict, kw: str) -> None:
    """후보 제목의 키워드 표기를 한 가지로 통일한다(화재감지기/화재 감지기 섞임 방지): 표기 = 띄어쓰기 결과(spaced) 또는 키워드 그대로.
    title_keyword 가 다른 표기인 후보는 제목 속 그 표기를 통일 표기로 바꾼다. 확정·예비 제목이 모두 같은 표기를 쓰게 된다."""
    sp = e.get("spaced")
    flat = lambda x: "".join((x or "").split())
    c = sp if sp and flat(sp) == flat(kw) else kw
    mp = {}
    for cand in e["candidates"]:
        tk = cand.get("title_keyword") or ""
        if not tk or tk == c or flat(tk) != flat(c) or tk not in cand["title"]:
            continue
        t = " ".join(cand["title"].replace(tk, c).split())
        mp[cand["title"]] = t
        cand["title"], cand["title_keyword"] = t, c
    if mp:
        for key in ("intent_ok", "natural_ok"):
            if e.get(key):
                e[key] = {mp.get(k, k): v for k, v in e[key].items()}


def norm_cands(raw) -> list:
    """Claude 가 준 후보를 [{title, title_keyword, pattern}] 로 정리 (옛 형식인 문자열 목록도 받는다). pattern 은 6가지 틀 이름일 때만 남긴다."""
    out = []
    for c in raw or []:
        if isinstance(c, str):
            c = {"title": c, "title_keyword": ""}
        if isinstance(c, dict) and str(c.get("title") or "").strip():
            d = {"title": " ".join(str(c["title"]).split()), "title_keyword": " ".join(str(c.get("title_keyword") or "").split())}
            if c.get("pattern") in ttl.PATTERNS:
                d["pattern"] = c["pattern"]
            out.append(d)
    return out


def title_ctx(it: dict, own: list, entry: dict = None) -> dict:
    off = {ttl.norm(t) for t, ok in ((entry or {}).get("intent_ok") or {}).items() if not ok}
    bad = {ttl.norm(t) for t, ok in ((entry or {}).get("natural_ok") or {}).items() if not ok}
    return {"keyword": it["keyword"], "platform": it.get("platform") or "reserve", "track": it.get("track") or "info",
            "timing": (it.get("timing") or {}).get("label"), "off_intent": off, "unnatural": bad,
            "target_words": ((entry or {}).get("intent_info") or {}).get("target_words") or [],
            "top_titles": [p["title"] for p in top_posts_brief(it)], "own_titles": own,
            "r1_wp": bool(it.get("r1") and (it.get("platform") == "wordpress" or it.get("r1_style") == "wordpress"))}


def _ask_json(prompt: str, key: str, problems: list, label: str, batches: list, n: int, model: str = MODEL):
    """Claude 한 번 호출(실패하면 한 번 더) → {keyword: 항목}. 실패하면 None. 사용량은 batches 에 기록."""
    env, got = None, None
    for attempt in range(2):
        try:
            env = call_claude(prompt, model)
            got = {p.get("keyword"): p for p in extract_json(env.get("result", ""))[key]}
            break
        except UsageLimitError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            log(f"  {label} 실패({type(e).__name__}){' → 한 번 더 시도' if attempt == 0 else ''}: {str(e).splitlines()[0][:120]}")
            env, got = None, None
    if env is None:
        problems.append(f"{label} 호출 실패")
        return None
    u = env.get("usage") or {}
    batches.append({"batch": label, "keywords": n, "total_cost_usd": env.get("total_cost_usd"), "num_turns": env.get("num_turns"),
                    "duration_ms": env.get("duration_ms"), "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")})
    return got


def choose_titles(items: list, claude_by_kw: dict, out_dir: Path, monday: date, rules: dict, guide: str,
                  batches: list, problems: list) -> dict:
    """글마다 제목 후보를 규칙으로 채점해 확정 1·예비 2를 정한다.
    ① 후보 채점(필수 규칙: 수익글 선택 표현·키워드·길이·금지어·브랜드·비슷한 제목·검색 의도) ② 상위 후보가 intent 에서 벗어났는지 Claude 가 한 번에 검수
    ③ 한 주 확정 제목에서 같은 틀은 플랫폼별 최대 30% (코드: select_weekly) ④ 후보가 모자라거나 바꿀 후보가 없는 글만 모아 1번 재요청(글마다 최대 2회).
    받은 후보·intent·검수 결과는 out_dir/title_cache_날짜.json 에 저장 → 같은 주 재실행은 같은 제목.
    → {keyword: {"final_title", "title_keyword", "title_pattern", "alt_titles", "alt_patterns", "intent", "title_scores", "title_candidates", "retried"}}"""
    cache_path = out_dir / f"title_cache_{monday.strftime('%Y%m%d')}.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")).get("titles") or {}
    except (OSError, ValueError):
        cache = {}

    def save():
        write_text_retry(cache_path, json.dumps({"week": monday.isoformat(), "titles": cache}, ensure_ascii=False, indent=1) + "\n")

    own = ttl.own_titles()
    need = int(rules["min_pass"])
    share = float(rules.get("pattern_max_share", 0.3))
    max_retries = int(rules.get("max_retries", 2))
    by_kw = {it["keyword"]: it for it in items}
    for it in items:                 # 후보·intent 는 캐시가 우선(같은 입력 → 같은 제목). 4단계 캐시에는 새 후보(pattern 포함)와 intent 만 합쳐 넣는다.
        kw, pc = it["keyword"], claude_by_kw.get(it["keyword"]) or {}
        new = norm_cands(pc.get("title_candidates"))
        e = cache.get(kw)
        if e is None:
            e = cache[kw] = {"candidates": new, "retries": 0, "v45": True}
        else:
            e.setdefault("retries", 1 if e.pop("retried", False) else 0)
            if not e.get("v45"):
                have = {ttl.norm(c["title"]) for c in e["candidates"]}
                e["candidates"] += [c for c in new if ttl.norm(c["title"]) not in have]
                e["v45"] = True
        if not e.get("intent") and str(pc.get("intent") or "").strip():
            e["intent"] = " ".join(str(pc["intent"]).split())
        e.setdefault("intent_ok", {})
    sp_need = [kw for kw, e in cache.items() if kw in by_kw and e.get("spaced_ver") != SPACING_VER and not re.search(r"\s", kw)
               and len(kw) >= 4 and kw not in rules.get("single_word_ok", [])]
    if sp_need:          # 붙여 쓴 키워드는 Claude 가 사람이 읽는 띄어쓰기를 한 번에 정해 주고, 제목은 코드가 바꿔 끼운다(Claude 는 제목에서 붙여 쓰는 버릇이 안 고쳐짐)
        got = _ask_json(SPACING_PROMPT.format(keywords=chr(10).join(sp_need)), "plans", problems, "spacing", batches, len(sp_need))
        for kw in sp_need:
            if got is None:
                continue
            new_sp = " ".join(str((got or {}).get(kw, {}).get("spaced") or "").split())
            if new_sp and "".join(new_sp.split()) == "".join(kw.split()):
                cache[kw]["spaced_prev"] = cache[kw].get("spaced")
                cache[kw]["spaced"] = new_sp
            cache[kw]["spaced_ver"] = SPACING_VER
    for kw, e in cache.items():
        if kw in by_kw:
            respace_entry(e, kw)
            canon_entry(e, kw)
    save()

    def rules_for(kw):
        """Claude 가 '이 키워드는 한 낱말(제품 이름)'이라고 정한 붙여 쓴 표기는 띄어쓰기 감점에서 제외한다."""
        e = cache[kw]
        sp = e.get("spaced")
        if e.get("spaced_ver") == SPACING_VER and sp and not re.search(r"\s", sp):
            return {**rules, "single_word_ok": list(rules.get("single_word_ok", [])) + [sp]}
        return rules
    def rank_all():
        return {kw: ttl.rank(cache[kw]["candidates"], title_ctx(by_kw[kw], own, cache[kw]), rules_for(kw)) for kw in by_kw}

    def intent_check(ranked):
        """통과 상위 6개 중 아직 검수 안 한 제목만 모아 한 번에 Claude 로 검수. → 새로 검수한 글 수"""
        units = []
        for kw, r in ranked.items():
            e = cache[kw]
            titles = [x["title"] for x in r["ranked"][:6] if x["title"] not in e["intent_ok"]]
            if titles and e.get("intent"):
                units.append((kw, titles))
        done = 0
        for i in range(0, len(units), 20):
            chunk = units[i:i + 20]
            summ = [{"keyword": kw, "intent": cache[kw]["intent"], "top_posts": [p["title"] for p in top_posts_brief(by_kw[kw])][:5],
                     "titles": [f"{n + 1}. {t}" for n, t in enumerate(ts)]} for kw, ts in chunk]
            got = _ask_json(INTENT_CHECK_PROMPT.format(summary=json.dumps(summ, ensure_ascii=False, indent=1)), "checks", problems,
                            "intent_check", batches, len(chunk))
            if got is None:
                continue
            for kw, ts in chunk:
                off = {int(n) for n in (got.get(kw) or {}).get("off") or [] if str(n).isdigit()}
                for n, t in enumerate(ts):
                    cache[kw]["intent_ok"][t] = (n + 1) not in off
                done += 1
        if done:
            save()
        return done

    store = load_intent_store(out_dir)
    for kw in by_kw:                  # 저장소의 판정을 먼저 쓴다(같은 키워드는 다시 묻지 않음). 반대로 옛 제목 캐시의 판정은 저장소로 옮긴다.
        e = cache[kw]
        if e.get("intent_info") is None and kw in store:
            e["intent_info"] = dict(store[kw])
        elif e.get("intent_info") is not None and kw not in store:
            store[kw] = dict(e["intent_info"])
    def info_check():
        """키워드마다 의도 정보(집에서 쓸 제품을 고르는 의도인지·뜻이 여러 개인지·대상 낱말)를 한 번에 받아 저장. → 새로 받은 글 수"""
        units = [{"keyword": kw, "intent": cache[kw]["intent"], "top_posts": [p["title"] for p in top_posts_brief(by_kw[kw])][:5],
                  "related_keywords": (by_kw[kw].get("related_keywords") or [])[:10]}
                 for kw in by_kw if cache[kw].get("intent") and cache[kw].get("intent_info") is None]
        done = ask_intent_info(units, store, out_dir, problems, batches)
        for u in units:
            if u["keyword"] in store:
                cache[u["keyword"]]["intent_info"] = dict(store[u["keyword"]])
        if done:
            save()
        return done
    def natural_check(ranked):
        """통과한 상위 6개 제목(확정·예비 후보 전부) 중 아직 검수 안 한 것을 모아 Claude(haiku)에게 한 번에: 사람이 쓴 제목처럼 자연스러운가. 판정은 캐시에 저장."""
        units = []
        for kw, r in ranked.items():
            e = cache[kw]
            titles = [x["title"] for x in r["ranked"][:6] if x["title"] not in e.setdefault("natural_ok", {})]
            if titles:
                units.append((kw, titles))
        done = 0
        for i in range(0, len(units), 40):
            chunk = units[i:i + 40]
            summ = [{"keyword": kw, "titles": [f"{n + 1}. {t}" for n, t in enumerate(ts)]} for kw, ts in chunk]
            got = _ask_json(NATURAL_PROMPT.format(summary=json.dumps(summ, ensure_ascii=False, indent=1)), "checks", problems,
                            "natural_check", batches, len(chunk), "haiku")
            if got is None:
                continue
            for kw, ts in chunk:
                bad = {int(n) for n in (got.get(kw) or {}).get("bad") or [] if str(n).isdigit()}
                for n, t in enumerate(ts):
                    cache[kw]["natural_ok"][t] = (n + 1) not in bad
                done += 1
        if done:
            save()
        return done

    def settle():
        """의도 정보 → 순위 → 의도 검수 → 자연스러움 검수를 새 판정이 안 나올 때까지(최대 4바퀴) 되풀이. 판정은 저장돼 다음엔 호출 0회."""
        info_check()
        ranked = rank_all()
        for _ in range(4):
            if not (intent_check(ranked) + natural_check(ranked)):
                break
            ranked = rank_all()
        return ranked

    def select(ranked):
        groups = {}
        for it in items:
            groups.setdefault(it.get("platform") or "reserve", []).append((it["keyword"], ranked[it["keyword"]]["ranked"]))
        return ttl.select_weekly(groups, share)

    ranked = settle()
    sel = select(ranked)
    need_new = {}
    for it in items:
        kw, r = it["keyword"], ranked[it["keyword"]]
        if cache[kw]["retries"] >= max_retries:
            continue
        if r["passed"] < need:
            need_new[kw] = list(ttl.PATTERNS)
        elif kw in sel["stuck"]:
            have = {x["pattern"] for x in r["ranked"]}
            want = [p for p in ttl.PATTERNS if p not in have and p not in sel["stuck"][kw]]
            need_new[kw] = want or [p for p in ttl.PATTERNS if p not in sel["stuck"][kw]]
        if kw in need_new and title_ctx(by_kw[kw], [], None)["r1_wp"]:
            need_new[kw] = [p for p in need_new[kw] if p != "실수·손해형"]      # R1: 싼세계는 실수·주의형 제목을 쓰지 않는다
            if not need_new[kw]:
                need_new.pop(kw)
    if need_new:        # 모자란 글만 모아 한 번 (최대 한 번 호출)
        log(f"[제목 재요청] 후보가 모자라거나 틀을 바꿀 후보가 없는 글 {len(need_new)}편")
        summ = []
        for kw, want in need_new.items():
            it = by_kw[kw]
            summ.append({"keyword": kw, "platform": it.get("platform") or "reserve", "track": it.get("track") or "info",
                         "intent": cache[kw].get("intent"), "want_patterns": want,
                         "max_title_chars": ttl.max_len_of(it.get("platform"), rules),
                         "timing": f"{(it.get('timing') or {}).get('label')} — {(it.get('timing') or {}).get('reason')}",
                         "top_posts": [p["title"] for p in top_posts_brief(it)], "related_keywords": (it.get("related_keywords") or [])[:12],
                         **({"pair_with": it.get("pair_with"), "must_include": "고르는·고르기·추천·비교·종류·차이·vs·가격·용량·규격·크기·~용 중 하나"}
                            if it.get("track") == "money" else {}),
                         "have": [x["title"] for x in ranked[kw]["ranked"]][:6],
                         **({"smart_block_titles": it["smart_blocks"]} if it.get("smart_blocks") else {}),
                         **({"avoid": sel["stuck"][kw]} if kw in sel["stuck"] else {}),
                         **({"must_include_one_of": (cache[kw].get("intent_info") or {}).get("target_words")}
                            if (cache[kw].get("intent_info") or {}).get("target_words") else {}),
                         "rejected": [{"title": x["title"], "why": x.get("fail_text")} for x in ranked[kw]["scores"] if not x["ok"]][:8]})
        got = _ask_json(TITLE_RETRY_PROMPT.format(title_guide=guide, summary=json.dumps(summ, ensure_ascii=False, indent=1)),
                        "plans", problems, "title_retry", batches, len(summ))
        if got is not None:
            for kw in need_new:
                cache[kw]["candidates"] += norm_cands((got.get(kw) or {}).get("title_candidates"))
                if not cache[kw].get("intent") and str((got.get(kw) or {}).get("intent") or "").strip():
                    cache[kw]["intent"] = " ".join(str(got[kw]["intent"]).split())
                cache[kw]["retries"] += 1
                cache[kw]["retried_v45"] = True
                respace_entry(cache[kw], kw)
                canon_entry(cache[kw], kw)
            save()
            ranked = settle()
            sel = select(ranked)
    res = {}
    for it in items:
        kw, r = it["keyword"], ranked[it["keyword"]]
        final = sel["pick"].get(kw)
        if len(r["ranked"]) < need:
            problems.append(f"{kw}: 제목 필수 규칙 통과 후보 {len(r['ranked'])}개 (예비 포함 {need}개 필요, {need - len(r['ranked'])}개 모자람)")
        if kw in sel["stuck"]:
            problems.append(f"{kw}: 제목 틀·'N가지' '{', '.join(sel['stuck'][kw])}' 이 한도를 넘는데 바꿀 후보가 없음")
        alts = ttl.pick_alts(r["ranked"], final, 2) if final else []
        res[kw] = {"final_title": final["title"] if final else None, "title_keyword": final["title_keyword"] if final else None,
                   "title_pattern": final["pattern"] if final else None,
                   "alt_titles": [x["title"] for x in alts], "alt_patterns": [x["pattern"] for x in alts],
                   "intent": cache[kw].get("intent"), "intent_info": cache[kw].get("intent_info"),
                   "title_natural": cache[kw].get("natural_ok") or {}, "title_scores": r["scores"],
                   "title_candidates": cache[kw]["candidates"], "retried": bool(cache[kw].get("retried_v45"))}
    return res


# ---------- 메인 ----------

def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="주간 원고 기획 (ranking.json → 일정 배정 → claude -p 10개씩)")
    ap.add_argument("--ranking", default=str(DEFAULT_RANKING), help="입력 파일 (기본: pipeline/ranking.json)")
    ap.add_argument("--filter", default=str(DEFAULT_FILTER), help="설정 파일 (기본: tools/filter.json)")
    ap.add_argument("--topic", default=str(BASE / "tools" / "topic.json"), help="주제 설정 (기본: tools/topic.json)")
    ap.add_argument("--existing", default=str(OUT_DIR / "existing_keywords.json"),
                    help="기존 발행 키워드 (기본: pipeline/existing_keywords.json)")
    ap.add_argument("--today", help="기준일 YYYY-MM-DD (기본: 오늘, 시험용)")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="계획·이어 하기 파일을 쓸 폴더 (기본: pipeline/). 시험은 pipeline/stage15_run 처럼 따로 지정")
    ap.add_argument("--bank", default=str(DEFAULT_BANK), help="황금 키워드 은행 (기본: pipeline/golden_bank.json)")
    ap.add_argument("--ranking-only-preview", action="store_true", help="시험용: 배정·의도 확인까지만 하고(기획 호출 전) 미리보기 파일을 쓰고 끝낸다")
    args = ap.parse_args()

    try:
        w = load_weekly(Path(args.filter))
        roles = load_topic(Path(args.topic))
    except (OSError, ValueError, KeyError) as e:
        log(f"[실패] 설정 파일을 읽지 못했습니다: {e}")
        return 2
    try:
        rk = json.loads(Path(args.ranking).read_text(encoding="utf-8-sig"))
        ranking, intent_groups = rk["ranking"], rk.get("intent_groups")
        today = date.fromisoformat(args.today) if args.today else date.today()
    except (OSError, ValueError, KeyError) as e:
        log(f"[실패] 입력을 읽지 못했습니다: {e}")
        return 2
    golden_mode = rk.get("mode") == "golden" or any(r.get("grade") for r in ranking)
    found_date = str(rk.get("generated_at") or today.isoformat())[:10]
    new_sa = 0
    if w.get("r1") and not rk.get("r1"):
        w["r1"] = None          # R1 기준은 R1 신호가 붙은 입력(ranking_r1.json)일 때만 쓴다
    r1on = bool(w.get("r1"))
    if r1on:
        w["semantic_check_max"] = max(int(w.get("semantic_check_max", 250)), 700)    # R1 은 추린 후보 전부를 내용 중복 점검
        for r in ranking:
            r["from_bank"], r["found_date"] = False, found_date
        new_sa = len(ranking)
        log(f"[R1] 황금 등급과 상관없이 R1 후보 {len(ranking)}개를 모두 후보로 씁니다 (옛 등급은 참고 칸)")
    elif golden_mode:  # 황금 키워드 방식으로 만든 ranking: S·A 만 기획한다 (B 이하로 채우지 않음)
        ranking = [r for r in ranking if r.get("grade") in ("S", "A")]
        new_sa = len(ranking)
        for r in ranking:
            r["from_bank"], r["found_date"] = False, found_date
        log(f"[황금 등급] S·A 만 기획합니다: {len(ranking)}개 (S {sum(1 for r in ranking if r['grade'] == 'S')}, A {sum(1 for r in ranking if r['grade'] == 'A')})")
    if not ranking and not golden_mode:
        log("[실패] 기획할 키워드가 없습니다 (ranking 이 비어 있음). 파이프라인 결과를 확인하세요.")
        return 2

    monday = next_monday(today)
    sunday = monday + timedelta(days=6)
    prev_keywords = []
    for k in range(1, int(w["avoid_past_weeks"]) + 1):
        pf = OUT_DIR / f"plan_{(monday - timedelta(days=7 * k)).strftime('%Y%m%d')}.json"
        try:
            prev = json.loads(pf.read_text(encoding="utf-8"))
            prev_keywords += [p["keyword"] for p in prev.get("plans", []) if p.get("role") == "scheduled"]
        except (OSError, ValueError, KeyError):
            pass
    log(f"기획 주간: {monday} ~ {sunday} / 지난 기획 키워드 {len(prev_keywords)}개와 겹치지 않게 배정")

    problems = []
    existing, existing_at = load_existing(Path(args.existing))
    th = w["similarity_threshold"]

    # 황금 키워드 은행: 이미 발행된 것 제거 → 14일 지난 것 재확인 → 새 S·A 와 함께 후보로
    bank_path = Path(args.bank)
    bank_info = {"file": bank_path.name, "loaded": 0, "removed": [], "rechecked": 0, "in_pool": 0, "saved": None}
    bank_rows = []
    if golden_mode and not r1on:
        bank_items = load_bank(bank_path)
        bank_info["loaded"] = len(bank_items)
        new_kw = {r["keyword"] for r in ranking}
        dup_bank = [b for b in bank_items if b["keyword"] in new_kw]
        bank_items = [b for b in bank_items if b["keyword"] not in new_kw]
        if existing:
            bank_items, pub_dropped = split_existing(bank_items, existing, th)
            bank_info["removed"] += [{"keyword": d["keyword"], "reason": f"이미 발행·대기열에 있음 ('{d.get('matched_title')}')"}
                                     for d in pub_dropped]
        stale = [b for b in bank_items
                 if days_since((b.get("bank") or {}).get("last_checked") or (b.get("bank") or {}).get("found_date"), today) >= BANK_RECHECK_DAYS]
        if stale:
            try:
                import golden_metrics as gm
                gc = gm.golden_config(json.loads(Path(args.filter).read_text(encoding="utf-8-sig")))
                bank_items, removed, checked = refresh_bank(bank_items, today, gc, real_publish_fn(gc))
                bank_info["removed"] += removed
                bank_info["rechecked"] = checked
            except Exception as e:      # 재확인을 못 하면 판단 없이 그대로 쓰고 문제로 기록
                problems.append("은행 재확인 실패(그대로 사용): " + (str(e).splitlines() or [""])[0][:150])
                log(f"[주의] {problems[-1]}")
        for b in bank_items:
            b["from_bank"] = True
            b["found_date"] = (b.get("bank") or {}).get("found_date")
        bank_rows = bank_items
        bank_info["in_pool"] = len(bank_rows)
        log(f"[황금 은행] 저장돼 있던 {bank_info['loaded']}개 → 새 S·A 와 같은 키워드 {len(dup_bank)}개(새 것 사용) / "
            f"이미 발행·재확인 탈락으로 제거 {len(bank_info['removed'])}개 / 14일 지나 재확인 {bank_info['rechecked']}개 → 후보로 사용 {len(bank_rows)}개")
        for rm in bank_info["removed"]:
            log(f"    은행에서 제거: {rm['keyword']} — {rm['reason']}")
        ranking = ranking + bank_rows

    type_info = {"status": "skipped"}
    if golden_mode and w.get("semantic_dedup", True):
        # 유형은 ranking 의 옛 값이 아니라 색인 값을 쓴다 (색인에 없는 후보는 여기서 판정)
        log(f"[유형 색인] 후보 {len(ranking)}개의 유형(업체형·주제 밖 등)을 색인 기준으로 맞춥니다 (색인에 없는 것만 판정)")
        try:
            type_info = apply_index_types(ranking)
            log(f"[유형 색인] 새로 판정 {type_info['judged']}개, ranking 의 옛 유형과 달라진 것 {len(type_info['changed'])}개")
            for c in type_info["changed"]:
                if c["new"] in EXCLUDED_KW_TYPES:
                    log(f"    {c['keyword']}: {c['old']} → {c['new']} (제외 유형)")
        except UsageLimitError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            type_info = {"status": "failed", "error": (str(e).splitlines() or [""])[0][:200]}
            problems.append("색인 유형 판정 실패(ranking 의 옛 유형 사용): " + type_info["error"])
            log(f"[주의] {problems[-1]}")
    attach_clicks(ranking, w["click"])                # 주간 예상 클릭·계산에 쓴 값 (API·Claude 호출 없음)
    sa_all = list(ranking) if golden_mode else []     # 일정에서 빠진 S·A 사유 기록용
    excluded_why = {}                                 # 키워드 → {"code","text"}
    # 규칙으로 먼저 제외: 구매처 찾기·제외 유형 → 버림, 계절 하락 → 은행에 보관
    bw = buy_words(args.filter)
    ok_rows, rule_why = rule_exclude(ranking, w, bw, golden_mode, monday)
    excluded_why.update(rule_why)
    cal_seeds = load_calendar_seeds(Path(args.out_dir), OUT_DIR)
    attach_timing(ranking, w["click"], cal_seeds)      # 시기: 선발행 / 상승 / 유지
    if r1on:
        import r1 as r1m
        for r in ranking:       # R1: 시기 표시는 데이터랩 주간 신호(추세 비율·정점까지 주)로
            if r.get("r1"):
                r["timing"] = r1m.label_of(r["r1"], w["r1"])
    n_rule = {c: sum(1 for v in excluded_why.values() if v["code"] == c) for c in ("buyplace", "type", "season", "lowvol")}
    log(f"[규칙 제외] 구매처 찾기 {n_rule['buyplace']}개 / 제외 유형 {n_rule['type']}개 / 월 검색량 {w['schedule_volume_min']} 미만 {n_rule['lowvol']}개 / 계절 하락 {n_rule['season']}개 (계절 하락은 은행에 보관)")
    n_due = sum(1 for r in ok_rows if r.get("revisit_due"))
    if n_due:
        log(f"[은행 복귀] 작년 추세가 다시 오르는 주(revisit_week)가 되어 후보로 다시 들어온 은행 키워드 {n_due}개")
    for kw, v in excluded_why.items():
        log(f"    {kw} — {v['text']}")
    if golden_mode and not any((r.get("grade_info") or {}).get("conditions") for r in ranking):
        problems.append("선점 추세 정보가 없어 계절 하락 규칙을 건너뜀")
    if golden_mode and not any(r.get("kw_category") for r in ranking):
        problems.append("카테고리·세부 주제가 없어 분산 한도를 건너뜀 (분류를 메타 포함으로 다시 실행)")
        log(f"[주의] {problems[-1]}")
    pool, overlap = split_overlap(ok_rows, prev_keywords, th)
    for r in overlap:
        excluded_why[r["keyword"]] = {"code": "past", "text": "지난주 기획 키워드와 같거나 거의 같음"}
    if existing:
        pool, overlap_existing = split_existing(pool, existing, th)
        log(f"기존 발행·예약 글 {len(existing)}개(수집 {existing_at})와 겹쳐 제외: {len(overlap_existing)}개")
        for d in overlap_existing:
            excluded_why[d["keyword"]] = {"code": "published", "text": f"이미 발행·대기열에 있는 글과 겹침 ('{d.get('matched_title')}')"}
    else:
        overlap_existing = []
        problems.append("기존 발행 키워드 파일이 없어 겹침 검사를 건너뜀 (existing_posts.py 를 먼저 실행)")
        log(f"[주의] {problems[-1]}")
    if intent_groups is None:  # 분류 없이 만든(또는 옛) ranking.json
        problems.append("ranking.json 에 의도 묶음이 없어 의도 중복 제외를 건너뜀 (run_pipeline 을 분류 포함으로 실행)")
        log(f"[주의] {problems[-1]}")
        intent = {"status": "skipped", "groups": [], "excluded": [], "pool": pool}
    else:
        intent = apply_intent_groups(pool, intent_groups)
        pool = intent["pool"]
        for ex_ in intent["excluded"]:
            excluded_why[ex_["keyword"]] = {"code": "intent", "text": f"의도 중복으로 제외 (같은 글: '{ex_['kept']}')"}
        log(f"[의도 묶기] (분류 단계 결과 사용) 묶음 {len(intent['groups'])}개, 의도 중복으로 제외 {len(intent['excluded'])}개")
    rename = {}
    if golden_mode:     # 표준 표기가 같은 키워드는 같은 의도로 묶고, 기획에는 표준 표기를 쓴다
        pool, sp_dropped, sp_merged = merge_spelling(pool)
        for kw, text in sp_dropped.items():
            excluded_why[kw] = {"code": "spelling", "text": text}
        rename = {r["original_keyword"]: r["keyword"] for r in pool if r.get("original_keyword")}
        log(f"[표준 표기] 같은 표기끼리 묶음 {len(sp_merged)}건, 표기 통일로 제외 {len(sp_dropped)}개, 표준 표기로 바꾼 키워드 {len(rename)}개")
        for k, v in rename.items():
            log(f"    {k} → {v}")
    else:
        sp_merged = []
    dedup = {"status": "skipped"}
    w["_prev_keywords"] = prev_keywords
    if golden_mode and w.get("semantic_dedup", True) and pool:
        # 글자 비교로 못 잡는 중복(기존 글과 같은 내용, 동의어·표기 차이)을 Claude 로 한 번 더 걸러낸다
        log(f"[지문 색인 중복 점검] 후보 {len(pool)}개와 기존 글에 지문(대상|문제)을 붙여 코드로 비교합니다 (색인에 없는 것만 {MODEL} 에 60개씩 질문)")
        try:
            dedup = semantic_dedup(pool, existing, w, roles)
        except UsageLimitError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            dedup = {"status": "failed", "error": (str(e).splitlines() or [""])[0][:200]}
            problems.append("지문 색인 중복 점검 실패(글자 비교만 적용): " + dedup["error"])
            log(f"[주의] {problems[-1]}")
        if dedup.get("status") == "ok":
            by_kw = {r["keyword"]: r for r in pool}
            drop = set()
            def mark(r, code, text):
                for k in {r["keyword"], r.get("original_keyword")} - {None}:
                    excluded_why[k] = {"code": code, "text": text}
            for k, title in dedup["dup_existing"].items():
                drop.add(k)
                mark(by_kw[k], "published", f"이미 쓴 글과 내용이 같음 ('{title}')")
            for k in dedup.get("capped") or []:      # 쌍 확인 상한 때문에 기존 글과 같은 글인지 확인 못 한 후보: 이번 주 후보에서 빼고 은행에 보관
                if k in by_kw and k not in drop:
                    drop.add(k)
                    mark(by_kw[k], "cap", f"쌍 확인 호출 상한({w.get('max_pair_calls', 30)}회) 초과로 이번 주 후보에서 뺌 (기존 글과 같은 글인지 확인 못 함)")
            gk = lambda r: ((1 if r.get("platform_hint") == "naver" else 0),
                            2 if r.get("grade") == "S" else (1 if r.get("grade") == "A" else 0),
                            r.get("expected_traffic") or 0, r["score"]["total"])
            for g in dedup["same_article"]:
                keep = g[0]       # 묶음의 대표 = 검색량이 가장 큰 것 (대표와 직접 같은 글 판정이 난 것만 붙어 있음)
                for k in g:
                    if k != keep:
                        drop.add(k)
                        mark(by_kw[k], "intent", f"'{keep}'와 같은 글이 되는 키워드 (동의어·표기 차이)")
                        by_kw[keep].setdefault("spelling_variants", [])
                        if k not in by_kw[keep]["spelling_variants"]:
                            by_kw[keep]["spelling_variants"].append(k)
            pool = [r for r in pool if r["keyword"] not in drop]
            log(f"[내용 중복 점검] 기존 글과 같은 내용 {len(dedup['dup_existing'])}개, 같은 글 묶음 {len(dedup['same_article'])}개"
                f"(제외 {sum(len(g) - 1 for g in dedup['same_article'])}개), 넓은 주제 {len(dedup['topic'])}개 / 가까운 쌍 확인 Claude {dedup.get('pair_calls')}회, 대상 이름 정리 {dedup.get('alias_calls')}회 / 비용 ${(dedup.get('usage') or {}).get('total_cost_usd')}")
            for k, title in dedup["dup_existing"].items():
                log(f"    기존 글과 중복: {k} ← '{title}'")
            for g in dedup["same_article"]:
                log(f"    같은 글: {' = '.join(g)}")
    items, shortage, left_out = assign(pool, w, monday, roles)
    audit = {"status": "skipped", "rounds": []}
    if golden_mode and w.get("final_audit", True) and items:
        # 최종 검사: 배정된 일정+예비만 다시 꼼꼼히 본다 → 걸린 것은 빼고 다시 배정 → 새로 들어온 것까지 다시 검사 (최대 N회)
        titles = audit_titles(existing, w, prev_keywords)
        service_left = int(w.get("max_service_per_week", 0))
        audit["status"] = "ok"
        for rnd in range(int(w.get("final_audit_rounds", 1))):
            try:
                res = final_audit(items, titles, w)
            except UsageLimitError:
                raise
            except (RuntimeError, ValueError, KeyError, TypeError) as e:
                audit["status"] = "failed"
                problems.append("최종 검사 실패: " + (str(e).splitlines() or [""])[0][:200])
                log(f"[주의] {problems[-1]}")
                break
            proposed = dict(res["flags"])
            try:
                flags, overruled, rv = review_flags(proposed, log)
            except UsageLimitError:
                raise
            except (RuntimeError, ValueError, KeyError, TypeError) as e:
                flags, overruled, rv = {}, {k: "확인 호출 실패로 반영하지 않음" for k in proposed}, {"pair_calls": 0, "type_calls": 0}
                problems.append("안전망 의견 재확인 실패(안전망은 아무것도 빼지 않음): " + (str(e).splitlines() or [""])[0][:150])
                log(f"[주의] {problems[-1]}")
            log(f"[최종 검사] 안전망 제안 {len(proposed)}개 → 쌍 확인·유형 판정으로 확정 {len(flags)}개, 기각 {len(overruled)}개 (확인 호출 {rv['pair_calls'] + rv['type_calls']}회)")
            for k, why in overruled.items():
                log(f"    기각: {k} — {why}")
            for kw in [k for k, f in flags.items() if f["code"] == "service"]:
                if service_left > 0:          # 업체 시공형을 주에 몇 편 허용하면 앞쪽부터 살린다
                    service_left -= 1
                    flags.pop(kw)
            audit["rounds"].append({"round": rnd + 1, "checked": len(items), "proposed": {k: f["text"] for k, f in proposed.items()},
                                    "flagged": {k: f["text"] for k, f in flags.items()}, "overruled": overruled,
                                    "directly_removed_by_audit": 0, "review_calls": rv,
                                    "calls": res["calls"], "cost": res["cost"]})
            log(f"[최종 검사 {rnd + 1}회차] 일정+예비 {len(items)}개 검사 → 걸림 {len(flags)}개 (비용 ${res['cost']})")
            for k, f in flags.items():
                log(f"    {k} — {f['text']}")
            if not flags:
                break
            by_kw = {r["keyword"]: r for r in pool}
            for k, f in flags.items():
                r = by_kw.get(k) or {"keyword": k}
                for kk in {k, r.get("original_keyword")} - {None}:
                    excluded_why[kk] = {"code": f["code"], "text": f["text"]}
            pool = [r for r in pool if r["keyword"] not in flags]
            items, shortage, left_out = assign(pool, w, monday, roles)
        else:
            n_last = len(audit["rounds"][-1]["flagged"]) if audit["rounds"] else 0
            audit["note"] = (f"안전망(최종 검사 {w.get('final_audit_rounds', 1)}회)이 마지막에 {n_last}개를 빼고 빈자리를 채움 "
                             "— 새로 들어온 키워드는 색인 판정만 거침(최종 검사는 다시 안 함)")
            log(f"[최종 검사] {audit['note']}")
    if dedup.get("status") == "ok":
        checked_set = set(dedup.get("checked_keywords") or [])
        unchecked = [i["keyword"] for i in items if i["keyword"] not in checked_set]
        if unchecked:
            problems.append(f"내용 중복 점검 범위({dedup['checked']}개) 밖에서 배정된 키워드 {len(unchecked)}개: {', '.join(unchecked[:10])} "
                            "(weekly.semantic_check_max 를 늘리면 함께 점검)")
            log(f"[주의] {problems[-1]}")
    # 수익글 의도 확인(기획 호출 전): 수익글인데 '집에서 쓸 제품을 고르려는 의도'가 아니면 후보에서 빼고 다시 배정 → 들어온 글도 기획·상위 5개 글·제목을 똑같이 받는다
    gate_batches, money_swaps = [], []
    if golden_mode and items and w.get("money_intent_gate", True):
        for rnd in range(3):
            before_money = [i["keyword"] for i in items if i.get("track") == "money"]
            try:
                _, off = money_intent_gate(items, Path(args.out_dir), problems, gate_batches)
            except UsageLimitError:
                raise
            except (RuntimeError, ValueError, KeyError, TypeError) as e:
                problems.append("수익글 의도 확인 실패(확인 없이 진행): " + (str(e).splitlines() or [""])[0][:150])
                log(f"[주의] {problems[-1]}")
                break
            log(f"[수익글 의도 확인 {rnd + 1}회차] 수익글 {len(before_money)}편 확인 → 의도 아님 {len(off)}편")
            if not off:
                break
            by_kw = {r["keyword"]: r for r in pool}
            for k in off:
                r = by_kw.get(k) or {"keyword": k}
                for kk in {k, r.get("original_keyword")} - {None}:
                    excluded_why[kk] = {"code": "not_buy", "text": "수익글 의도 아님: 집에서 쓸 제품을 고르려는 검색이 아님"}
                log(f"    제외: {k}")
            pool = [r for r in pool if r["keyword"] not in off]
            items, shortage, left_out = assign(pool, w, monday, roles)
            came = [i["keyword"] for i in items if i.get("track") == "money" and i["keyword"] not in before_money]
            for n_, k in enumerate(off):
                money_swaps.append({"removed": k, "reason": "수익글 의도 아님: 집에서 쓸 제품을 고르려는 검색이 아님 → 이번 주 제외",
                                    "replaced_by": came[n_] if n_ < len(came) else None, "round": rnd + 1})
        else:
            problems.append("수익글 의도 확인이 3바퀴 안에 끝나지 않음 (마지막 배정에 의도 아닌 수익글이 남았을 수 있음)")
    if args.ranking_only_preview:
        prev = [{"platform": i.get("platform") or ("예비·" + str(i.get("r1_style"))), "role": i["role"], "track": i.get("track"), "keyword": i["keyword"],
                 "shape": (i.get("r1") or {}).get("shape"), "cw": (i.get("r1") or {}).get("cw")} for i in items]
        Path(args.out_dir).mkdir(parents=True, exist_ok=True)
        write_text_retry(Path(args.out_dir) / "assign_preview.json", json.dumps({"items": prev, "r1": R1_LAST, "shortage": shortage,
                                                                          "reasons": {k: v.get("text") for k, v in {**excluded_why, **left_out}.items()}}, ensure_ascii=False, indent=1) + "\n")
        log(f"[미리보기] 배정 {len(items)}개 저장 → assign_preview.json (기획 호출 없이 끝)")
        return 0
    if not items and not golden_mode:
        log("[실패] 배정된 키워드가 없습니다.")
        return 2
    if golden_mode and not items:
        problems.append("S·A 키워드가 하나도 없어 기획이 비어 있음 (shortage 에 부족분 기록)")
    for name in ("naver", "wordpress", "reserve"):
        n = shortage[name]
        if n > 0:
            tr = (shortage.get("tracks") or {}).get(name)
            detail = f" (정보 {tr['info']} · 수익 {tr['money']})" if tr else ""
            log(f"[주의] {name} 후보가 {n}개 모자랍니다{detail} (화면 수집 수나 필터를 넓히세요).")

    # 일정에서 빠진 S·A: 반드시 사유를 남기고, 자리가 모자라 밀린 것만 은행에 쌓는다
    scheduled_kw = {i["keyword"] for i in items if i["role"] == "scheduled"} | \
                   {i["original_keyword"] for i in items if i["role"] == "scheduled" and i.get("original_keyword")}
    unscheduled, bank_new = [], []
    for r in sa_all:
        kw = r["keyword"]
        if kw in scheduled_kw:
            continue
        why = excluded_why.get(kw) or left_out.get(rename.get(kw, kw)) or {"code": "unknown", "text": "사유 기록 없음(배정 단계에서 빠짐)"}
        keep = why["code"] in ("capacity", "reserve", "cap", "season")   # 자리 부족·한도 초과·계절 하락만 은행에 (중복·이미 발행·구매처 등은 쌓지 않음)
        unscheduled.append({"keyword": kw, "grade": r.get("grade"), "expected_traffic": r.get("expected_traffic"),
                            "expected_clicks": r.get("expected_clicks"), "monthly_total": r.get("monthly_total"),
                            "platform_hint": r.get("platform_hint"), "track": r.get("track"), "from_bank": bool(r.get("from_bank")),
                            "reason": why["text"], "reason_code": why["code"], "saved_to_bank": keep})
        if keep:
            bi = to_bank_item(r, r.get("found_date") or found_date, why["text"], today, r.get("bank"))
            if why["code"] == "season":     # 계절 하락: 작년 추세가 다시 오르기 시작하는 주에 다시 후보로 (캐시의 데이터랩 12개월 값으로 계산)
                rw = revisit_week_for(trend_series(kw), monday)
                bi["bank"]["revisit_week"] = rw
                if rw is None:
                    bi["bank"].pop("revisit_week")
                    bi["bank"]["revisit_note"] = "캐시에 데이터랩 값이 없거나 다시 오르는 달이 안 보여 revisit_week 를 못 구함"
            bank_new.append(bi)
    if golden_mode:
        log(f"[일정에서 빠진 S·A] {len(unscheduled)}개 (S·A 후보 {len(sa_all)}개 중 일정 {len(sa_all) - len(unscheduled)}개)")
        for u in unscheduled:
            log(f"    {u['grade']} {u['keyword']} (주간 예상 클릭 {u['expected_clicks']}) — {u['reason']}"
                + (" → 은행 저장" if u["saved_to_bank"] else ""))
        try:
            save_bank(bank_path, bank_new, today)
            bank_info["saved"] = len(bank_new)
            log(f"[황금 은행] {len(bank_new)}개 저장 → {bank_path.name} (다음 주 후보로 사용)")
        except OSError as e:
            problems.append(f"황금 은행 저장 실패: {e}")
            log(f"[주의] {problems[-1]}")

    # Claude 를 10개씩 나눠 호출 → 전체 사용량 합산
    size = int(w["claude_batch_size"])
    title_rules = ttl.load_rules(args.filter) if getattr(args, "filter", None) else ttl.load_rules()
    guide = title_guide(title_rules["forbidden_words"], ttl.style_samples(ttl.own_titles()))
    if r1on:
        title_rules["r1_wp"] = w["r1"]["wordpress"]
        guide += R1_WP_TITLE_GUIDE.format(words=", ".join(w["r1"]["wordpress"]["title_search_words"][:12]))
    batches, claude_by_kw = list(gate_batches), {}
    started = datetime.now().astimezone()
    chunks = []
    # 이어 하기: 이미 받은 기획은 pipeline/resume_plan_날짜.json 에 키워드별로 저장돼 있다 (사용 한도로 끊겼을 때 같은 명령으로 이어서)
    out_dir = Path(args.out_dir)
    prog_path = out_dir / f"resume_plan_{monday.strftime('%Y%m%d')}.json"
    try:
        saved_plans = json.loads(prog_path.read_text(encoding="utf-8")).get("plans") or {}
    except (OSError, ValueError):
        saved_plans = {}
    reused = [it for it in items if it["keyword"] in saved_plans]
    for it in reused:
        claude_by_kw.setdefault(it["keyword"], saved_plans[it["keyword"]])
    if reused:
        log(f"[Claude] 이미 받아 둔 기획 {len(reused)}개 재사용 (이어 하기) — 없는 {len(items) - len(reused)}개만 새로 요청")
        batches.append({"batch": "resumed", "keywords": len(reused), "total_cost_usd": 0, "num_turns": 0, "duration_ms": 0,
                        "input_tokens": 0, "output_tokens": 0, "resumed": True})
    missing = [it for it in items if it["keyword"] not in saved_plans]
    chunks = [missing[i:i + size] for i in range(0, len(missing), size)]
    for n, chunk in enumerate(chunks, 1):
        log(f"[Claude {n}/{len(chunks)}] 키워드 {len(chunk)}개 기획 요청 (모델 {MODEL})")
        plans, env, probs = plan_batch(build_summary(chunk, title_rules), guide)
        problems += probs
        if env is None:
            batches.append({"batch": n, "keywords": len(chunk), "failed": True})
            continue
        u = env.get("usage") or {}
        batches.append({"batch": n, "keywords": len(chunk), "total_cost_usd": env.get("total_cost_usd"),
                        "num_turns": env.get("num_turns"), "duration_ms": env.get("duration_ms"),
                        "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")})
        for p in plans:
            claude_by_kw.setdefault(p.get("keyword"), p)
            saved_plans[p.get("keyword")] = p
        write_text_retry(prog_path, json.dumps({"week": monday.isoformat(), "plans": saved_plans}, ensure_ascii=False) + "\n")

    title_res = choose_titles(items, claude_by_kw, out_dir, monday, title_rules, guide, batches, problems)

    ok = [b for b in batches if not b.get("failed")]
    usage = {
        "total_cost_usd": sum(b["total_cost_usd"] or 0 for b in ok),
        "calls": len(batches), "failed_calls": len(batches) - len(ok),
        "input_tokens": sum(b["input_tokens"] or 0 for b in ok),
        "output_tokens": sum(b["output_tokens"] or 0 for b in ok),
        "duration_ms": sum(b["duration_ms"] or 0 for b in ok),
        "batches": batches,
    }

    plans_out = []
    for it in items:
        c = claude_by_kw.get(it["keyword"]) or {}
        plans_out.append({
            "platform": it["platform"], "role": it["role"], "publish_date": it["publish_date"], "weekday": it["weekday"],
            "keyword": it["keyword"], "seed": it.get("seed"), "score": it["score"],
            "fingerprint": it.get("fingerprint"),
            "kw_type": it.get("kw_type"), "platform_hint": it.get("platform_hint"),
            "track": it.get("track") or "info",
            **({"pair_with": it.get("pair_with")} if it.get("track") == "money" else {}),
            **({"assign_note": it["assign_note"]} if it.get("assign_note") else {}),
            "category": it.get("kw_category"), "topic": it.get("kw_topic"), "topic_group": it.get("kw_topic_group"),
            "detail": it.get("kw_detail"), "problem": it.get("kw_problem"),
            **({"solves": it.get("solves")} if it.get("track") == "money" else {}),
            **({"original_keyword": it.get("original_keyword")} if it.get("original_keyword") else {}),
            **({"variant_spellings": it["spelling_variants"], "variant_note": "본문·태그용 변형 표기"} if it.get("spelling_variants") else {}),
            **({"from_bank": True, "found_date": it.get("found_date")} if it.get("from_bank") else {}),
            **({"grade": it.get("grade"), "expected_traffic": it.get("expected_traffic"), "found_round": it.get("found_round")}
               if it.get("grade") else {}),
            **({"r1": it.get("r1"), "r1_style": it.get("r1_style") or it.get("platform")} if it.get("r1") else {}),
            "expected_clicks": it.get("expected_clicks"), "clicks_basis": it.get("clicks_basis"), "timing": it.get("timing"),
            "monthly_total": it.get("monthly_total"), "saturation": it.get("saturation"),
            "trend_growth": it.get("trend_growth"), "trend_steady": it.get("trend_steady"),
            "smart_blocks": it.get("smart_blocks"), "related_keywords": it.get("related_keywords"),
            "search_intent": c.get("search_intent"), "gaps": c.get("gaps"),
            **{k: v for k, v in title_res[it["keyword"]].items() if k != "retried"},
            "outline": c.get("outline"),
            "experience_slots": c.get("experience_slots"),
            "beat_top": c.get("beat_top"), "target_chars": c.get("target_chars"), "target_images": c.get("target_images"),
            "top_posts": top_posts_brief(it),
        })
        if not c:
            problems.append(f"{it['keyword']}: Claude 기획이 비어 있음")

    warnings = []
    sched_vols = sorted(p.get("monthly_total") or 0 for p in plans_out if p["role"] == "scheduled")
    sched_median = (sched_vols[len(sched_vols) // 2] if len(sched_vols) % 2 else (sched_vols[len(sched_vols) // 2 - 1] + sched_vols[len(sched_vols) // 2]) / 2) if sched_vols else 0
    if sched_vols and sched_median < w["schedule_median_warn"]:
        warnings.append(f"일정 전체 월 검색량 중간값 {sched_median:.0f} — 기준 {w['schedule_median_warn']} 미만 (억지로 맞추지 않음)")
        log(f"[경고] {warnings[-1]}")
    short_vol = [p["keyword"] for p in plans_out if (p.get("monthly_total") or 0) < w["schedule_volume_min"]]
    if short_vol:
        problems.append(f"월 검색량 {w['schedule_volume_min']} 미만이 일정·예비에 들어감: {', '.join(short_vol[:5])}")
    record = {
        "generated_at": started.isoformat(timespec="seconds"),
        "week": {"start": monday.isoformat(), "end": sunday.isoformat()},
        "model": MODEL, "max_turns": int(MAX_TURNS),
        "title_rules": {k: title_rules[k] for k in ("candidate_count", "min_pass", "max_len", "similarity_method", "similarity_max", "bonus")} | {"note": "추정값(5단계 실제 클릭률로 보정)"},
        "title_retried": [k for k, v in title_res.items() if v["retried"]],
        "title_pattern_counts": ({pl: {pat: sum(1 for p in plans_out if p["role"] == "scheduled" and p["platform"] == pl and p.get("title_pattern") == pat)
                                       for pat in ttl.PATTERNS} for pl in ("naver", "wordpress")}
                                 | {"reserve": {pat: sum(1 for p in plans_out if p["role"] == "reserve" and p.get("title_pattern") == pat)
                                                for pat in ttl.PATTERNS}}),
        "settings": {"posts_per_day": w["posts_per_day"], "reserve_count": w["reserve_count"],
                     "claude_batch_size": size, "similarity_threshold": w["similarity_threshold"],
                     "max_per_category_per_platform": w["max_per_category_per_platform"], "max_per_topic": w["max_per_topic"],
                     "max_per_topic_group": w["max_per_topic_group"], "track_quota": w.get("track_quota"),
                     "max_per_detail": w.get("max_per_detail"), "max_money_per_target": w.get("max_money_per_target"),
                     "max_pair_calls": w.get("max_pair_calls"),
                     "min_seasonal_lift": w["min_seasonal_lift"], "schedule_volume_min": w["schedule_volume_min"], "schedule_median_warn": w["schedule_median_warn"],
                     "expected_clicks": {k: v for k, v in w["click"].items() if k != "position_weights"}, "max_pest_per_platform": w["max_pest_per_platform"], "assign_mode": w["assign_mode"]},
        "usage": usage,
        "counts": {"candidates_in_ranking": len(ranking), "new_sa": new_sa, "from_bank": len(bank_rows), "scheduled": sum(1 for i in items if i["role"] == "scheduled"),
                   "reserve": sum(1 for i in items if i["role"] == "reserve")},
        "shortage": shortage,
        "golden_only_sa": golden_mode and not r1on,
        **({"r1": {"config": w["r1"], **R1_LAST}} if r1on else {}),
        "excluded_overlap_with_past": [r["keyword"] for r in overlap],
        "existing_keywords": {"file": Path(args.existing).name, "collected_at": existing_at, "count": len(existing)},
        "excluded_overlap_with_existing": overlap_existing,
        "intent_dedup": {"status": intent["status"], "source": "ranking.json (run_pipeline 분류 단계)",
                         "groups": intent["groups"]},
        "excluded_intent_duplicates": intent["excluded"],
        "unscheduled_sa": unscheduled,
        "rule_excluded": {c: [u["keyword"] for u in unscheduled if u["reason_code"] == c]
                          for c in ("season", "buyplace", "type", "spelling", "cap", "lowvol")},
        "spelling_merged": sp_merged,
        "final_audit": audit,
        "money_swaps": money_swaps,
        "index_types": {k: v for k, v in type_info.items() if k != "changed"} | {"changed": len(type_info.get("changed") or [])},
        "semantic_dedup": {k: dedup.get(k) for k in ("status", "error", "checked", "existing_titles", "dup_existing", "same_article", "cover", "pair_calls", "alias_calls", "ps_calls", "pair_top", "capped", "judged_new", "usage")
                           if k in dedup},
        "category_counts": {pl: _count(plans_out, pl) for pl in ("naver", "wordpress")},
        "topic_counts": _topic_count(plans_out),
        "topic_group_counts": _topic_count(plans_out, "topic_group"),
        "pest_counts": {pl: sum(1 for p in plans_out if p["role"] == "scheduled" and p["platform"] == pl
                                and is_pest({"keyword": p["keyword"], "kw_topic": p.get("topic")}, w["pest_words"]))
                        for pl in ("naver", "wordpress")},
        "golden_bank": bank_info,
        "warnings": warnings,
        "volume_median_scheduled": sched_median,
        "problems": problems,
        "schedule": [{"date": p["publish_date"], "weekday": p["weekday"],
                      "platform": PLATFORM_KO.get(p["platform"]), "keyword": p["keyword"], "category": p.get("category")}
                     for p in plans_out if p["role"] == "scheduled"],
        "plans": plans_out,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"plan_{monday.strftime('%Y%m%d')}.json"
    if out.exists():  # 같은 주에 다시 돌리면 이전 파일은 이름을 바꿔 보관
        out.rename(out_dir / f"plan_{monday.strftime('%Y%m%d')}.old_{datetime.now().strftime('%H%M%S')}.json")
    text = json.dumps(record, ensure_ascii=False, indent=2)
    write_text_retry(out, text + "\n")
    try:
        prog_path.unlink()          # 기획이 끝까지 저장됐으니 이어 하기용 파일은 지운다
    except OSError:
        pass
    print(text)
    log(f"[저장됨] {out}")
    log(f"[사용량] 호출 {usage['calls']}회, total_cost_usd = {usage['total_cost_usd']:.4f}")
    for pr in problems:
        log(f"[주의] {pr}")
    return 1 if usage["failed_calls"] else 0


# ---------- 확장 탐색용: 배정 가능 수 미리 계산 (run_pipeline 이 회차마다 부른다) ----------

def simulate_fill(ranking: list, intent_groups, filter_path=None, today: date = None,
                  topic_path=None, bank_path=None, existing_path=None) -> dict:
    """지금까지 나온 S·A(+ 황금 은행)로 실제 기획과 같은 규칙(구매처·제외 유형·계절 하락·지난주·기존 발행 겹침·
    의도 중복·표기 통일·카테고리/세부 주제/해충 한도)을 적용해 일정에 몇 편이 들어가는지 계산한다.
    Claude 호출·파일 쓰기는 하지 않는다. 은행 14일 재확인(API)도 하지 않는다.
    → {"shortage", "short_total", "solve_scheduled", "total_cap", "naver_solve", "solve_short", "scheduled", "reserve", "cat_counts", "cat_max",
       "pest_counts", "pest_max", "candidates"}  (solve_short = 네이버 21자리 중 해결형으로 못 채운 수)"""
    filter_path = Path(filter_path or DEFAULT_FILTER)
    w = load_weekly(filter_path)
    roles = load_topic(Path(topic_path or BASE / "tools" / "topic.json"))
    today = today or date.today()
    monday = next_monday(today)
    th = w["similarity_threshold"]
    rows = [dict(r) for r in ranking if r.get("grade") in ("S", "A")]
    new_kw = {r["keyword"] for r in rows}
    bank = [b for b in load_bank(Path(bank_path or DEFAULT_BANK)) if b["keyword"] not in new_kw]
    rows += bank
    existing, _ = load_existing(Path(existing_path or OUT_DIR / "existing_keywords.json"))
    prev_keywords = []
    for k in range(1, int(w["avoid_past_weeks"]) + 1):
        pf = OUT_DIR / f"plan_{(monday - timedelta(days=7 * k)).strftime('%Y%m%d')}.json"
        try:
            prev_keywords += [p["keyword"] for p in json.loads(pf.read_text(encoding="utf-8")).get("plans", [])
                              if p.get("role") == "scheduled"]
        except (OSError, ValueError, KeyError):
            pass
    attach_clicks(rows, w["click"])
    annotate_index_fields(rows)       # 브랜드 여부(유형 brand)·갈래를 색인에서 읽음 (Claude 호출 없음)
    pool, _ = rule_exclude(rows, w, buy_words(filter_path), True, monday)
    pool, _ = split_overlap(pool, prev_keywords, th)
    if existing:
        pool, _ = split_existing(pool, existing, th)
    if intent_groups:
        pool = apply_intent_groups(pool, intent_groups)["pool"]
    pool, _, _ = merge_spelling(pool)
    annotate_index_fields(pool)       # 갈래(track)·대상 이름·넓은 주제를 색인에서 읽음 (Claude 호출 없음)
    items, shortage, _ = assign(pool, w, monday, roles)
    cat_counts = {"naver": {}, "wordpress": {}}
    pest_counts = {"naver": 0, "wordpress": 0}
    naver_solve = 0                      # 네이버 자리 중 해결·정보형(platform_hint=naver)으로 채운 편수
    solve_scheduled = 0                  # 두 플랫폼 일정 전체 중 해결형 편수
    for it in items:
        if it["role"] != "scheduled":
            continue
        if it.get("platform_hint") == "naver":
            solve_scheduled += 1
            if it["platform"] == "naver":
                naver_solve += 1
        c = it.get("kw_category") or "(없음)"
        cat_counts[it["platform"]][c] = cat_counts[it["platform"]].get(c, 0) + 1
        if is_pest(it, w["pest_words"]):
            pest_counts[it["platform"]] += 1
    naver_cap = 7 * w["posts_per_day"]["naver"]
    total_cap = naver_cap + 7 * w["posts_per_day"]["wordpress"]
    track_short = {"info": shortage.get("info", 0), "money": shortage.get("money", 0)}
    # 탐색 멈춤 기준: '채워진 수' = 월 검색량 하한(schedule_volume_min)·유형·등급(S·A)·수익글 조건을 통과한 후보 수. 목표 = 갈래별 정원 × fill_goal_factor
    qual = [r for r in pool if not (r.get("track") == "money" and r.get("product") is not None and not r.get("solves"))]
    track_cand = {t: sum(1 for r in qual if (r.get("track") or "info") == t) for t in ("info", "money")}
    tq_ = w.get("track_quota")
    track_goal = {t: math.ceil(sum(tq_[p][t] for p in ("naver", "wordpress", "reserve")) * w["fill_goal_factor"]) for t in ("info", "money")} if tq_ else None
    short_total = sum(max(0, shortage[k]) for k in ("naver", "wordpress", "reserve"))
    if track_goal:
        track_short = {t: max(0, track_goal[t] - track_cand[t]) for t in ("info", "money")}
        short_total = sum(track_short.values())
    track_sched = {t: sum(1 for i in items if i["role"] == "scheduled" and (i.get("track") or "info") == t) for t in ("info", "money")}
    return {"shortage": shortage, "short_total": short_total,
            "track_short": track_short, "track_scheduled": track_sched, "track_candidates": track_cand, "track_goal": track_goal,
            "solve_scheduled": solve_scheduled, "total_cap": total_cap, "assign_mode": w["assign_mode"],
            "naver_solve": naver_solve, "naver_cap": naver_cap, "solve_short": max(0, naver_cap - naver_solve),
            "scheduled": sum(1 for i in items if i["role"] == "scheduled"),
            "reserve": sum(1 for i in items if i["role"] == "reserve"),
            "cat_counts": cat_counts, "cat_max": w["max_per_category_per_platform"],
            "pest_counts": pest_counts, "pest_max": w["max_pest_per_platform"],
            "pest_words": w["pest_words"], "candidates": len(rows), "from_bank": len(bank)}


LIMIT_EXIT = 75     # 사용 한도로 중단 (일반 실패와 구별되는 종료 코드)


def limit_message(e: Exception, what: str) -> str:
    return (f"[한도로 중단] Claude 사용 한도에 걸려 {what} 중에 멈췄습니다: {(str(e).splitlines() or [''])[0][:160]}\n"
            "    지금까지의 진행은 저장했습니다. 한도가 풀린 뒤 같은 명령을 다시 실행하면 이어서 갑니다.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except UsageLimitError as e:
        log(limit_message(e, "글 기획"))
        sys.exit(LIMIT_EXIT)
