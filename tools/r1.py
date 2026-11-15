# -*- coding: utf-8 -*-
"""재기획 R1: 과거 글 시험(pipeline/backtest_run/summary.md) 결과로 바꾼 '뽑는 기준'. (순수 계산만 — API·Claude 호출 없음)

filter.json 의 'r1' 묶음에서 설정을 읽는다(enabled=false 면 plan_posts 는 예전 방식 그대로). 기존 값은 지우지 않는다.

신호(후보마다): 월 검색량 / 데이터랩 추세 비율(최근 주 ÷ 직전 4주 평균) / 작년 같은 시기 기준 정점까지 남은 주 / 키워드 모양
"""
import re
from datetime import date, timedelta

SHAPES = ["제철 물건", "상시 물건", "급한 증상", "방법·순서", "고르기·비교", "브랜드·모델", "기타"]
NS_RE = re.compile(r"\s+")


def nospace(t) -> str:
    return NS_RE.sub("", str(t or "")).lower()


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_signals(ser: dict, plan_monday: date, recent_monday: date) -> dict:
    """데이터랩 주간 값(ser: {월요일 ISO: 값})에서 추세 비율과 정점까지 남은 주.
    추세 비율 = 최근 주 ÷ 직전 4주 평균 (직전 4주 평균이 0 이고 최근 주가 있으면 None 이지만 '상승 시작'으로 따로 표시).
    정점까지 = 작년 같은 주(계획 주 - 364일)부터 52주 안에서 가장 높은 주까지 몇 주 남았나(0 = 작년 같은 시기가 정점)."""
    if not ser:
        return {"ratio": None, "peak": None, "from_zero": False}
    get = lambda d: ser.get(d.isoformat())
    cur = get(recent_monday)
    base_v = [get(recent_monday - timedelta(days=7 * k)) for k in (1, 2, 3, 4)]
    base_v = [x for x in base_v if x is not None]
    ratio, from_zero = None, False
    if cur is not None and base_v:
        base = sum(base_v) / len(base_v)
        if base > 0:
            ratio = round(cur / base, 2)
        elif cur > 0:
            from_zero = True
    ly = plan_monday - timedelta(days=364)
    best = None
    for k in range(0, 52):
        v = get(ly + timedelta(days=7 * k))
        if v is None:
            continue
        key = (v, -k)
        if best is None or key > best[0]:
            best = (key, k)
    peak = best[1] if best and best[0][0] > 0 else None
    return {"ratio": ratio, "peak": peak, "from_zero": from_zero}


def is_up(sig: dict, cfg: dict) -> bool:
    r = sig.get("ratio")
    return bool(sig.get("from_zero")) or (r is not None and r >= cfg["trend_up_ratio"])


def near_peak(sig: dict, cfg: dict) -> bool:
    p = sig.get("peak")
    lo, hi = cfg["peak_best"]
    return p is not None and lo <= p <= hi


def is_timed(sig: dict, cfg: dict) -> bool:
    """'추세 상승 또는 정점 0~3주 전'"""
    return is_up(sig, cfg) or near_peak(sig, cfg)


def naver_score(sig: dict, cfg: dict) -> float:
    """네이버 순위 = 제철 물건 여부 × 시기. 검색량은 쓰지 않는다(150 이상이면 같은 것으로 본다). 황금 등급·제목 일치 수도 쓰지 않는다."""
    w = cfg["naver"]["weights"]
    s = 0.0
    shape = sig.get("shape")
    if shape == "제철 물건":
        s += w["seasonal"]
    elif shape == "상시 물건":
        s += w["evergreen_object"]
    elif shape == "고르기·비교":
        s += w["pick"]
    if near_peak(sig, cfg):
        s += w["peak_best"]
    elif sig.get("peak") is not None and 4 <= sig["peak"] <= 8:
        s += w["peak_next"]
    if is_up(sig, cfg):
        s += w["trend_up"]
        r = sig.get("ratio")
        if r is not None:
            s += min(max(r - cfg["trend_up_ratio"], 0.0), 1.0) * w["trend_up_scale"]
    elif sig.get("ratio") is not None and sig["ratio"] >= 0.85:
        s += w["trend_flat"]
    return round(s, 3)


def topic_score(sig: dict, cfg: dict) -> float:  # cfg = filter.json 의 r1 묶음 전체
    """싼세계 정보글 주제(중심 낱말) 순위: 제철 물건 우선, 정점까지 2~8주 또는 추세 상승."""
    w = cfg["wordpress"]["topic_weights"]
    s = w["seasonal"] if sig.get("shape") == "제철 물건" else 0.0
    lo, hi = cfg["wordpress"]["peak_window"]
    if sig.get("peak") is not None and lo <= sig["peak"] <= hi:
        s += w["peak_window"]
    if is_up(sig, cfg):
        s += w["trend_up"]
    return s


def r1_cfg(cfg_all: dict) -> dict:
    r = (cfg_all or {}).get("r1") or {}
    return r if r.get("enabled") else {}


def label_of(sig: dict, cfg: dict) -> dict:
    """plan 의 timing 칸(label/reason)을 R1 신호로 만든다 — 제목의 계절 말 가점(label 이 상승·선발행일 때)과 A10 근거 칸에 쓴다."""
    ratio, peak = sig.get("ratio"), sig.get("peak")
    txt = f"검색 추세 비율 {ratio if ratio is not None else '값 없음'}" + (" (직전 4주가 0이라 상승 시작)" if sig.get("from_zero") else "") + \
          f", 작년 같은 시기 정점까지 {peak if peak is not None else '값 없음'}주"
    if is_timed(sig, cfg):
        return {"label": "상승" if is_up(sig, cfg) else "선발행", "reason": txt}
    return {"label": "유지", "reason": txt}
