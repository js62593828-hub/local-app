"""
숍 채널 키워드 점수 보강 — 검색어트렌드 · 뉴스검색 · 쇼핑인사이트(NAVER API HUB).

기본 점수(scorer.py: 검색광고 키워드도구의 검색량 × 경쟁도 × 시즌 가중치)는 그대로 두고, 그 위에
"요즘 얼마나 뜨고 있나"를 나타내는 보정 배수 세 개를 곱합니다. 숍 채널의 주간 불러오기에서만 쓰이며
(main.build_shopping_weekly_queue), 롱텀(정책) 채널은 이 모듈을 전혀 쓰지 않습니다.

    최종 점수 = 기본 점수 × 트렌드 배수 × 뉴스 배수 × 쇼핑 배수

  1) 트렌드 배수 (검색어트렌드): 이 키워드의 월별 관심도가 최근 2개월 vs 그 앞 6개월에서 얼마나
     올랐나.  R = 최근2개월 평균 / 앞6개월 평균,  배수 = clamp(1 + 0.5×(R-1), 0.85, 1.25)
  2) 뉴스 배수 (뉴스검색): 이 키워드로 "하루에 기사가 몇 건 나오는지"(최신 100건 기준, 최근 7일 안
     기사를 그 기간으로 나눔. 100건이 이틀 안에 몰려 있으면 하루 50건). 하루 15건에서 포화.
     배수 = 1 + 0.15 × min(하루 기사수, 15) / 15   (1.0 ~ 1.15)
  3) 쇼핑 배수 (쇼핑인사이트): 이 시드가 속한 네이버쇼핑 분야(shopping_categories.json)의 클릭
     추이가 같은 방식으로 얼마나 올랐나.  배수 = clamp(1 + 0.4×(R-1), 0.9, 1.2).
     매핑이 없는 시드는 1.0.

진행 중인 달의 값은 누적이 덜 돼서 실제보다 낮게 나오므로, 모멘텀은 "완료된 달"만 씁니다.
어느 신호든 호출이 실패하거나 데이터가 모자라면 그 신호는 배수 1.0으로 두고, 실패 사유를 항목의
signals.errors에 남겨서 화면에 보이게 합니다 (조용히 넘어가지 않음). 결과는 6시간 동안 캐시해서
같은 날 "주간 불러오기"를 여러 번 눌러도 API 호출량이 불어나지 않게 합니다.
아래 상수만 바꾸면 반영 강도를 조절할 수 있습니다.
"""
import json
import os
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import naver_hub_api

# ── 조절용 상수 ──────────────────────────────────────────────
TREND_WEIGHT, TREND_CLAMP = 0.5, (0.85, 1.25)
NEWS_WEIGHT, NEWS_SATURATION, NEWS_DAYS, NEWS_FETCH = 0.15, 15, 7, 100  # SATURATION: 하루 기사 건수(이 이상은 같은 취급)
SHOP_WEIGHT, SHOP_CLAMP = 0.4, (0.9, 1.2)
RECENT_MONTHS, BASELINE_MONTHS = 2, 6
CACHE_TTL_SECONDS = 6 * 3600
CACHE_FILE = "signal_cache.json"
CATEGORY_FILE = "shopping_categories.json"


# ── 캐시 ────────────────────────────────────────────────────
def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass  # 캐시를 못 써도 기능에는 지장 없음


def _cache_get(cache: dict, key: str):
    entry = cache.get(key)
    if entry and time.time() - entry.get("ts", 0) < CACHE_TTL_SECONDS:
        return entry.get("value")
    return None


# ── 계산 ────────────────────────────────────────────────────
def _clamp(value: float, bounds: tuple) -> float:
    return max(bounds[0], min(bounds[1], value))


def momentum(points: list[dict]) -> dict | None:
    """월별 [{"period": "2026-08-01", "ratio": 65.9}, ...]에서 완료된 달만 골라, 최근 2개월 평균 / 앞 6개월 평균."""
    this_month = datetime.now().strftime("%Y-%m")
    complete = [p["ratio"] for p in points if p.get("period", "")[:7] < this_month]
    if len(complete) < RECENT_MONTHS + 3:
        return None  # 비교할 만큼 데이터가 없음
    recent = sum(complete[-RECENT_MONTHS:]) / RECENT_MONTHS
    prior = complete[-(RECENT_MONTHS + BASELINE_MONTHS) : -RECENT_MONTHS]
    base = sum(prior) / len(prior)
    if base <= 0:
        return None
    return {"recent": round(recent, 2), "base": round(base, 2), "ratio": round(recent / base, 3)}


def _date_range() -> tuple[str, str]:
    today = datetime.now().date()
    return (today - timedelta(days=430)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _load_categories() -> dict:
    try:
        with open(CATEGORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"cid_names": {}, "seed_to_cid": {}}


# ── 신호별 조회 (실패는 errors에 모으고 계속 진행) ────────────────
def _fetch_trend_series(keywords: list[str], volume_by_kw: dict, cache: dict, errors: dict) -> dict:
    out, need = {}, []
    for kw in keywords:
        cached = _cache_get(cache, f"trend:{kw}")
        if cached is not None:
            out[kw] = cached
        else:
            need.append(kw)
    # 한 요청에 넣은 키워드끼리 상대값이라, 검색량이 비슷한 것끼리 묶어야 작은 키워드가 0으로 뭉개지지 않습니다.
    need.sort(key=lambda k: volume_by_kw.get(k, 0), reverse=True)
    start, end = _date_range()
    for i in range(0, len(need), 5):
        chunk = need[i : i + 5]
        try:
            results = naver_hub_api.get_search_trend(
                [{"groupName": kw, "keywords": [kw]} for kw in chunk], start, end, "month"
            )
            by_name = {r["group_name"]: r["data"] for r in results}
            for kw in chunk:
                if kw in by_name:
                    out[kw] = by_name[kw]
                    cache[f"trend:{kw}"] = {"ts": time.time(), "value": by_name[kw]}
        except Exception as e:  # noqa: BLE001 — 사유를 그대로 화면에 보이려고 문자열로 저장
            for kw in chunk:
                errors.setdefault(kw, []).append(f"검색어트렌드 실패: {type(e).__name__} {e}"[:160])
        time.sleep(0.15)
    return out


def _fetch_news_rate(kw: str, cache: dict, errors: dict) -> dict | None:
    """
    최신순 기사 NEWS_FETCH건을 가져와 "하루에 기사가 몇 건 나오는지"를 셉니다. 기사 수만 세면 큰 키워드는
    최신 100건이 전부 7일 안이라 다 똑같이 포화되므로, 100건이 다 최근 며칠 안에 몰려 있으면 그 기간으로
    나눠서(예: 100건이 이틀 안 → 하루 50건) 구분합니다. 반환: {"n7": 최근 7일 기사 수, "per_day": 하루 평균}
    """
    cached = _cache_get(cache, f"news2:{kw}")
    if cached is not None:
        return cached
    # 롱텀: longterm_news가 같은 질의(시드=대표 키워드)의 기사 목록을 이미 받아 캐시해 뒀으면 재사용합니다
    # (같은 질의를 또 부르지 않음). 숍 채널에서는 이 캐시가 없으므로 기존 동작 그대로입니다.
    shared = _cache_get(cache, f"newsphr:{kw}")
    if shared is not None:
        items = shared
    else:
        try:
            items = naver_hub_api.search_news(kw, display=NEWS_FETCH, sort="date")
        except Exception as e:  # noqa: BLE001
            errors.setdefault(kw, []).append(f"뉴스검색 실패: {type(e).__name__} {e}"[:160])
            return None
    now = datetime.now().astimezone()
    ages = []  # 각 기사가 몇 일 전인지
    for item in items:
        try:
            ages.append(max(0.0, (now - parsedate_to_datetime(item["pub_date"])).total_seconds() / 86400))
        except (TypeError, ValueError):
            continue
    recent = [a for a in ages if a <= NEWS_DAYS]
    if not ages:
        value = {"n7": 0, "per_day": 0.0}
    else:
        # 가져온 기사 중 가장 오래된 것이 7일보다 최근이면, 그만큼(최소 6시간)만 세서 하루 평균을 냅니다.
        window = min(NEWS_DAYS, max(0.25, max(ages)))
        value = {"n7": len(recent), "per_day": round(len(recent) / window, 2)}
    cache[f"news2:{kw}"] = {"ts": time.time(), "value": value}
    return value


def _fetch_category_series(cids: list[str], names: dict, cache: dict, errors_all: list) -> dict:
    out, need = {}, []
    for cid in cids:
        cached = _cache_get(cache, f"shop:{cid}")
        if cached is not None:
            out[cid] = cached
        else:
            need.append(cid)
    start, end = _date_range()
    for i in range(0, len(need), 3):
        chunk = need[i : i + 3]
        try:
            results = naver_hub_api.get_shopping_category_trend(
                [{"name": names.get(c, c), "param": [c]} for c in chunk], start, end, "month"
            )
            for r in results:
                cid = (r.get("category") or [None])[0]
                if cid and r["data"]:
                    out[cid] = r["data"]
                    cache[f"shop:{cid}"] = {"ts": time.time(), "value": r["data"]}
        except Exception as e:  # noqa: BLE001
            errors_all.append(f"쇼핑인사이트 실패({', '.join(chunk)}): {type(e).__name__} {e}"[:160])
        time.sleep(0.15)
    return out


# ── 진입점 ──────────────────────────────────────────────────
def enrich_clusters(clusters: list[dict], include_shop: bool = True) -> list[dict]:
    """
    클러스터 목록의 대표 키워드(head)마다 신호를 조회해서 점수를 보정합니다. head에는 base_score(보정 전)와
    signals(근거)가 붙습니다. 이 함수 안의 어떤 실패도 주간 불러오기 자체를 막지 않습니다. head가 없는
    클러스터(롱텀에서는 있을 수 있음)는 건너뜁니다.

    include_shop=True(기본, 숍 채널): 트렌드·뉴스·쇼핑 세 신호. 클러스터 점수 = 대표 점수(하위 키워드가
      없으므로)로 덮어쓰고 그 순서로 재정렬해 돌려줍니다.
    include_shop=False(롱텀 채널): 쇼핑인사이트를 아예 조회하지 않고 signals에 shop 키도 넣지 않으며,
      점수 = 기본 × 트렌드 × 뉴스. 롱텀은 세부 키워드 보너스가 있어서 cluster_score를 여기서 건드리지
      않습니다 — 호출하는 쪽(main)이 _cluster_score(head, children)로 다시 계산하고 재정렬합니다.
    """
    heads = [c["head"] for c in clusters if c.get("head")]
    if not heads:
        return clusters

    cache = _load_cache()
    errors: dict[str, list[str]] = {}
    shop_errors: list[str] = []
    keywords = [h["keyword"] for h in heads]
    volume_by_kw = {h["keyword"]: h.get("total_count", 0) for h in heads}

    trend_series = _fetch_trend_series(keywords, volume_by_kw, cache, errors)

    seed_to_cid, cid_names, shop_series = {}, {}, {}
    if include_shop:
        categories = _load_categories()
        seed_to_cid = categories.get("seed_to_cid", {})
        cid_names = categories.get("cid_names", {})
        cids_needed = sorted({seed_to_cid[c["seed"]] for c in clusters if c.get("head") and c["seed"] in seed_to_cid})
        shop_series = _fetch_category_series(cids_needed, cid_names, cache, shop_errors)

    for cluster in clusters:
        head = cluster.get("head")
        if not head:
            continue
        kw = head["keyword"]
        base_score = head["score"]
        signals: dict = {"errors": list(errors.get(kw, [])) + list(cluster.get("child_errors", []))}

        # 1) 검색어트렌드
        mom = momentum(trend_series[kw]) if kw in trend_series else None
        if mom:
            trend_factor = round(_clamp(1 + TREND_WEIGHT * (mom["ratio"] - 1), TREND_CLAMP), 3)
            signals["trend"] = {**mom, "factor": trend_factor}
        else:
            trend_factor = 1.0
            signals["trend"] = {"factor": 1.0, "note": "데이터 부족 또는 조회 실패"}

        # 2) 뉴스검색
        news = _fetch_news_rate(kw, cache, errors)
        signals["errors"] = list(errors.get(kw, [])) + list(cluster.get("child_errors", []))
        if news is not None:
            news_factor = round(1 + NEWS_WEIGHT * min(news["per_day"], NEWS_SATURATION) / NEWS_SATURATION, 3)
            signals["news"] = {**news, "factor": news_factor}
        else:
            news_factor = 1.0
            signals["news"] = {"factor": 1.0, "note": "조회 실패"}

        # 3) 쇼핑인사이트 (include_shop=False면 건너뜀 — signals에 shop 키 자체를 안 넣음)
        cid = seed_to_cid.get(cluster["seed"])
        if not include_shop:
            shop_factor = 1.0
        elif not cid:
            shop_factor = 1.0
            signals["shop"] = {"factor": 1.0, "note": "분야 매핑 없음"}
        else:
            shop_mom = momentum(shop_series[cid]) if cid in shop_series else None
            if shop_mom:
                shop_factor = round(_clamp(1 + SHOP_WEIGHT * (shop_mom["ratio"] - 1), SHOP_CLAMP), 3)
                signals["shop"] = {"cid": cid, "name": cid_names.get(cid, cid), **shop_mom, "factor": shop_factor}
            else:
                shop_factor = 1.0
                signals["shop"] = {"cid": cid, "name": cid_names.get(cid, cid), "factor": 1.0, "note": "데이터 부족 또는 조회 실패"}
                signals["errors"] += [m for m in shop_errors if cid in m]  # 이 분야 조회가 실패한 경우만

        head["base_score"] = base_score
        head["score"] = round(base_score * trend_factor * news_factor * shop_factor, 2)
        head["signals"] = signals
        if include_shop:
            cluster["cluster_score"] = head["score"]  # heads_only에서는 하위 키워드가 없어 대표 점수 = 클러스터 점수

    _save_cache(cache)
    for msg in shop_errors + [m for v in errors.values() for m in v]:
        print(f"[점수 보정 경고] {msg}")
    if include_shop:
        clusters.sort(key=lambda c: c["cluster_score"], reverse=True)
    return clusters
