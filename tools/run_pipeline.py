# -*- coding: utf-8 -*-
"""키워드 발굴 파이프라인: 지표 수집 → 필터 → 화면 수집 → 채점 (읽기 전용)

  1) collect_metrics 의 함수로 씨앗마다 연관 키워드의 검색량·블로그 문서 수·포화도·12개월 추세를 모은다.
  2) tools/filter.json 의 규칙으로 거른다 (검색량 범위, 씨앗 단어 포함, 제외 단어, 씨앗 안 포화도 하위 N%).
  2-1) 통과한 키워드 글자만 Claude(sonnet, 1회)에 보내 유형(해결형/제품선택형/브랜드모델구매/지역업체/기타)과
       의도 묶음을 받는다. 브랜드모델구매·지역업체는 화면 수집에서 뺀다. (--no-classify 로 건너뜀)
  3) 남은 키워드 중 점수가 높은 순으로 최대 N개를 네이버 화면 수집(naver_serp_collect)한다.
  4) 100점 만점으로 채점해 전체 결과 파일 1개와 top10.json 을 만든다.

같은 키워드는 cache_days(기본 30일) 안에는 다시 수집하지 않는다 (pipeline/cache.json).
앱 장부(articles*.json, content_queue*.json)는 건드리지 않는다. 쓰는 곳은 pipeline/ 폴더뿐.
키 값은 어디에도 출력하지 않는다.

사용법
  python tools/run_pipeline.py --file seeds.txt
  python tools/run_pipeline.py 패딩 세탁
  python tools/run_pipeline.py 패딩 세탁 --max-related 30 --max-serp 3     # 시험용 축소
"""
import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import collect_metrics as cm  # noqa: E402
import keyword_classify as kc  # noqa: E402
from plan_posts import UsageLimitError, LIMIT_EXIT, limit_message  # noqa: E402
from safe_io import write_text_retry  # noqa: E402  (드라이브 동기화 폴더 쓰기 오류 대비)
from naver_serp_collect import collect as serp_collect  # noqa: E402

BASE = TOOLS.parent
DEFAULT_FILTER = TOOLS / "filter.json"
OUT_DIR = BASE / "pipeline"
CACHE_FILE = OUT_DIR / "cache.json"
FIT_FILE = OUT_DIR / "my_fit.json"
SERP_GAP_SEC = 3.0

log = cm.log


# ---------- 설정 ----------

def load_filter(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    need = ["volume_min", "volume_max", "saturation_keep_ratio", "require_seed_word", "exclude_words",
            "max_related_per_seed", "max_serp_keywords", "cache_days", "score_weights",
            "demand_volume_share", "trend_growth_full", "screen_weights", "ad_cap", "shopping_cap"]
    missing = [k for k in need if k not in cfg]
    if missing:
        raise ValueError("filter.json 에 빠진 항목: " + ", ".join(missing))
    cfg.setdefault("seed_word_mode", "any")
    if cfg["seed_word_mode"] not in ("any", "all"):
        raise ValueError('filter.json: seed_word_mode 는 "any"(하나 이상) 또는 "all"(모두) 이어야 합니다.')
    if not (0 < cfg["volume_min"] < cfg["volume_max"]):
        raise ValueError("filter.json: volume_min 은 0 보다 크고 volume_max 보다 작아야 합니다.")
    if not (0 < cfg["saturation_keep_ratio"] <= 1):
        raise ValueError("filter.json: saturation_keep_ratio 는 0 초과 1 이하여야 합니다.")
    if cfg["ad_cap"] <= 0 or cfg["shopping_cap"] <= 0 or cfg["trend_growth_full"] <= 0:
        raise ValueError("filter.json: ad_cap / shopping_cap / trend_growth_full 은 0 보다 커야 합니다.")
    return cfg


def load_fit() -> dict:
    """내 적합도(0~100). pipeline/my_fit.json 에 {"키워드": 점수} 로 적으면 반영. 없으면 전부 0."""
    try:
        data = json.loads(FIT_FILE.read_text(encoding="utf-8-sig"))
        return {" ".join(str(k).split()): float(v) for k, v in data.items() if not str(k).startswith("_")}
    except (OSError, ValueError):
        return {}


# ---------- 캐시 ----------

def load_cache() -> dict:
    try:
        c = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(c, dict):
            c.setdefault("metrics", {})
            c.setdefault("serp", {})
            return c
    except (OSError, ValueError):
        pass
    return {"metrics": {}, "serp": {}}


def save_cache(cache: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_text_retry(CACHE_FILE, json.dumps(cache, ensure_ascii=False))


def fresh(entry, days: float) -> bool:
    return bool(entry) and time.time() - entry.get("ts", 0) < days * 86400


# 캐시 사용 통계: 종류 → [캐시로 해결한 개수, 전체 개수]
CACHE_STATS = {}


def cstat(kind: str, hit: bool) -> None:
    c = CACHE_STATS.setdefault(kind, [0, 0])
    c[1] += 1
    c[0] += 1 if hit else 0


def cstat_snapshot() -> dict:
    return {k: list(v) for k, v in CACHE_STATS.items()}


def cstat_delta(now: dict, before: dict) -> dict:
    out = {}
    for k, (h, t) in now.items():
        h0, t0 = before.get(k, [0, 0])
        if t - t0:
            out[k] = [h - h0, t - t0]
    return out


def cstat_text(d: dict) -> str:
    if not d:
        return "해당 없음"
    hit, tot = sum(v[0] for v in d.values()), sum(v[1] for v in d.values())
    return f"캐시 {hit}/{tot}개 (" + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in d.items()) + ")"


# ---------- 1·2단계: 지표 + 필터 ----------

def norm(s: str) -> str:
    return "".join((s or "").split()).lower()


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def trend_growth(series):
    """최근 3개월 평균이 이전 9개월 평균보다 얼마나 늘었는지(0.5 = +50%). 데이터가 없으면 None."""
    if not series or len(series) < 12:
        return None
    vals = [p["ratio"] for p in series]
    recent, prev = sum(vals[-3:]) / 3, sum(vals[:-3]) / (len(vals) - 3)
    if prev <= 0:
        return 1.0 if recent > 0 else 0.0
    return recent / prev - 1


def parse_seed(line: str) -> tuple:
    """'씨앗 | 핵심단어1, 핵심단어2' → ('씨앗', ['핵심단어1', '핵심단어2']). '|' 가 없으면 핵심 단어는 빈 목록."""
    left, _, right = line.partition("|")
    core = [" ".join(w.split()) for w in right.replace("，", ",").split(",")]
    return " ".join(left.split()), [w for w in core if w]


def trend_steady(series):
    """추세가 꾸준한 정도 0~1 (12개월 변동이 작을수록 1). 데이터가 없으면 None."""
    if not series or len(series) < 12:
        return None
    vals = [p["ratio"] for p in series]
    m = sum(vals) / len(vals)
    if m <= 0:
        return 0.0
    sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
    return round(clamp01(1 - (sd / m) / 0.8), 2)


def metrics_stage(api, seed: str, core: list, cfg: dict, months: list, cache: dict, max_related: int,
                  relax: bool = False, lite: bool = False) -> tuple:
    """relax=True(확장 회차): 씨앗·핵심 단어 포함 규칙을 쓰지 않는다. 주제 판정은 유형 분류의 '주제 범위 밖'이 맡는다.
    lite=True(황금 방식): 블로그 문서 수·12개월 추세를 여기서 부르지 않는다(캐시에 있으면 씀).
      → 발행 수 1차 탈락을 통과한 키워드만 나중에 채운다(enrich_survivors). 블로그 검색·데이터랩 하루 한도를 아끼기 위함.
      포화도 필터도 쓰지 않는다(황금 방식은 saturation_keep_ratio 1.0)."""
    rows = cm.related_keywords(seed)
    seed_words = [norm(w) for w in seed.split() if w.strip()]
    core_words = [norm(w) for w in core]
    excl = [norm(w) for w in cfg["exclude_words"] if str(w).strip()]
    dropped = {"씨앗단어없음": 0, "제외단어": 0, "검색량범위밖": 0}
    cands = []
    for r in rows:
        k = norm(r["keyword"])
        match = any if cfg["seed_word_mode"] == "any" else all
        if relax:
            word_ok = True
        elif core_words:  # 핵심 단어가 있으면 전부 들어간 키워드만 (seed_word_mode 는 무시)
            word_ok = all(w in k for w in core_words)
        else:
            word_ok = not cfg["require_seed_word"] or match(w in k for w in seed_words)
        if not word_ok:
            dropped["씨앗단어없음"] += 1
        elif any(w in k for w in excl):
            dropped["제외단어"] += 1
        elif not (cfg["volume_min"] <= r["monthly_total"] <= cfg["volume_max"]):
            dropped["검색량범위밖"] += 1
        else:
            cands.append(r)
    cands.sort(key=lambda r: r["monthly_total"], reverse=True)
    cands = cands[:max_related]
    log(f"  연관 {len(rows)}개 → 기본 필터 통과 {len(cands)}개 (제외: {dropped})" + ("  [확장: 씨앗 단어 규칙 없음]" if relax else ""))

    days = cfg["cache_days"]
    need_trend = []
    for n, r in enumerate(cands, 1):
        c = cache["metrics"].get(r["keyword"])
        hit_docs = bool(fresh(c, days) and c.get("blog_docs") is not None)
        if not lite:
            cstat("블로그 문서 수", hit_docs)
        if hit_docs:
            r["blog_docs"] = c["blog_docs"]
        else:
            r["blog_docs"] = None if lite else cm.blog_docs(api, r["keyword"])
        hit_tr = bool(fresh(c, days) and c.get("trend_12m") is not None)
        if not lite:
            cstat("12개월 추세", hit_tr)
        if hit_tr:
            r["trend_12m"] = c["trend_12m"]
        elif not lite:
            need_trend.append(r["keyword"])
        if n % 20 == 0:
            log(f"  문서 수 {n}/{len(cands)}")
    if need_trend:
        tr = cm.trends(api, need_trend, months)
        for r in cands:
            if r["keyword"] in tr:
                r["trend_12m"] = tr[r["keyword"]]
    for r in cands:
        t = r["monthly_total"]
        r["saturation"] = round(r["blog_docs"] / t, 2) if r.get("blog_docs") is not None and t > 0 else None
        r.setdefault("trend_12m", None)
        if r.get("blog_docs") is not None or r.get("trend_12m") is not None:
            old = cache["metrics"].get(r["keyword"], {})
            cache["metrics"][r["keyword"]] = {
                "ts": time.time(),
                "blog_docs": r["blog_docs"] if r.get("blog_docs") is not None else old.get("blog_docs"),
                "trend_12m": r["trend_12m"] if r.get("trend_12m") is not None else old.get("trend_12m"),
            }

    # 포화도 순위(1 = 가장 낮음) → 하위 N% 통과
    ranked = sorted((r for r in cands if r["saturation"] is not None), key=lambda r: r["saturation"])
    n = len(ranked)
    keep = max(1, math.ceil(n * cfg["saturation_keep_ratio"])) if n else 0
    for i, r in enumerate(ranked):
        r["saturation_rank"], r["saturation_of"] = i + 1, n
        r["sat_pct"] = i / (n - 1) if n > 1 else 0.0
        r["passed_filter"] = i < keep
        r["filter_note"] = None if i < keep else "포화도 상위(높은 쪽)"
    for r in cands:
        if r["saturation"] is None:
            r["saturation_rank"], r["saturation_of"], r["sat_pct"] = None, n, None
            # 포화도를 하나도 못 구했으면(블로그 검색 불가) 포화도 필터는 건너뛴다
            r["passed_filter"] = n == 0 or lite
            r["filter_note"] = None if (n == 0 or lite) else "포화도 계산 불가"
        if lite:       # 황금 방식: 포화도로 거르지 않는다 (문서 수는 1차 탈락 통과 뒤에 채움)
            r["passed_filter"], r["filter_note"] = True, None
    info = {"seed": seed, "core_words": core, "relaxed": relax, "related_total": len(rows), "dropped": dropped,
            "candidates": len(cands), "saturation_ranked": n, "passed": sum(1 for r in cands if r["passed_filter"])}
    for r in cands:
        r["seed"] = seed
    return cands, info


# ---------- 점수 ----------

def demand_score(r: dict, cfg: dict) -> float:
    w = cfg["score_weights"]["demand"]
    lo, hi = math.log(cfg["volume_min"]), math.log(cfg["volume_max"])
    vol = clamp01((math.log(max(r["monthly_total"], 1)) - lo) / (hi - lo))
    g = trend_growth(r.get("trend_12m"))
    tr = 0.5 if g is None else clamp01(0.5 + g / (2 * cfg["trend_growth_full"]))
    share = clamp01(cfg["demand_volume_share"])
    return w * (share * vol + (1 - share) * tr)


def saturation_score(r: dict, cfg: dict) -> float:
    return cfg["score_weights"]["saturation"] * (1 - r["sat_pct"]) if r.get("sat_pct") is not None else 0.0


def screen_score(serp: dict, cfg: dict) -> float:
    if not serp:
        return 0.0
    sw = cfg["screen_weights"]
    total = sum(sw.values()) or 1
    scale = cfg["score_weights"]["screen"] / total
    parts = {
        "smart_block": 1.0 if serp["smart_blocks"] else 0.0,
        "ad": 1 - min(serp["ad_sections"], cfg["ad_cap"]) / cfg["ad_cap"],
        "shopping": 1 - min(serp["shopping_sections"], cfg["shopping_cap"]) / cfg["shopping_cap"],
        "no_ai_briefing": 0.0 if serp["has_ai_briefing"] else 1.0,
    }
    return sum(sw.get(k, 0) * v for k, v in parts.items()) * scale


def fit_score(r: dict, cfg: dict, fit: dict) -> float:
    return cfg["score_weights"]["fit"] * clamp01(fit.get(r["keyword"], 0.0) / 100)


# ---------- 3단계: 화면 수집 ----------

def summarize_serp(d: dict) -> dict:
    secs = d.get("sections", [])
    return {
        "collected_at": d.get("collected_at"),
        "smart_blocks": d.get("smart_blocks", []),
        "related_keywords": d.get("related_keywords", []),
        "has_ad": d.get("has_ad"), "has_shopping": d.get("has_shopping"), "has_image": d.get("has_image"),
        "has_ai_briefing": d.get("has_ai_briefing"),
        "ai_briefing_source_count": len(d.get("ai_briefing_sources", [])),
        "ad_sections": sum(1 for s in secs if s.get("type") == "광고"),
        "shopping_sections": sum(1 for s in secs if s.get("type") == "쇼핑"),
        # 황금 키워드 측정용 (판정에는 아직 안 씀)
        "blog_feed": d.get("blog_feed"),
        "ai_briefing_has_blog": d.get("ai_briefing_has_blog"),
        "ai_briefing_blog_count": d.get("ai_briefing_blog_count"),
    }


def serp_stage(chosen: list, cfg: dict, cache: dict, golden: bool = False, deadline: float = None) -> list:
    errors, first_live = [], True
    days = cfg["cache_days"]
    try:
        for i, r in enumerate(chosen, 1):
            if deadline and time.time() > deadline:
                log("[중단] 전체 시간 제한이 되어 남은 화면 수집을 건너뜁니다.")
                break
            c = cache["serp"].get(r["keyword"])
            serp_hit = bool(fresh(c, days) and "related_keywords" in c["v"] and (not golden or "blog_feed" in c["v"]))  # 옛 캐시는 다시 수집
            cstat("화면 수집", serp_hit)
            if serp_hit:
                r["serp"], r["serp_cached"] = c["v"], True
                log(f"[화면 {i}/{len(chosen)}] 캐시 사용: {r['keyword']}")
                continue
            if not first_live:
                time.sleep(SERP_GAP_SEC)
            first_live = False
            log(f"[화면 {i}/{len(chosen)}] 수집 중: {r['keyword']}")
            try:
                r["serp"] = summarize_serp(serp_collect(r["keyword"]))
                r["serp_cached"] = False
                cache["serp"][r["keyword"]] = {"ts": time.time(), "v": r["serp"]}
                save_cache(cache)
            except Exception as e:
                r["serp"], r["serp_cached"] = None, False
                msg = (str(e).splitlines() or ["(이유 없음)"])[0]
                errors.append({"keyword": r["keyword"], "error": msg})
                log(f"  실패: {msg}")
    except KeyboardInterrupt:
        log("[중단] Ctrl+C 로 멈췄습니다. 지금까지 모은 것으로 결과를 만듭니다.")
    return errors


# ---------- 2-1단계: 유형 분류 + 의도 묶기 ----------

def classify_stage(passed: list, skip: bool) -> dict:
    """필터 통과 키워드 글자를 Claude 에 한 번 보내 유형·의도 묶음을 받는다.
    각 키워드에 kw_type / platform_hint / intent_group / exclude_reason 을 달아 둔다. 실패하면 분류 없이 진행."""
    for r in passed:
        r["kw_type"], r["platform_hint"], r["intent_group"], r["exclude_reason"] = None, None, None, None
    if skip or not passed:
        return {"status": "skipped"}
    log(f"[유형 분류] 필터 통과 키워드 {len(passed)}개를 {kc.MODEL} 로 한 번 보냅니다 (키워드 글자만)")
    res = kc.classify([r["keyword"] for r in passed])
    if res["status"] != "ok":
        log(f"[주의] 유형 분류 실패 → 분류 없이 진행: {res.get('error')}")
        return res
    group_of = {k: i + 1 for i, g in enumerate(res["groups"]) for k in g}
    for r in passed:
        t = res["types"][r["keyword"]]
        r["kw_type"], r["platform_hint"] = t, kc.PLATFORM_HINT.get(t)
        r["intent_group"] = group_of.get(r["keyword"])
        if t in kc.EXCLUDED_TYPES:
            r["exclude_reason"] = f"{kc.TYPE_KO[t]} 키워드라 화면 수집에서 제외"
    counts = {kc.TYPE_KO[t]: sum(1 for r in passed if r["kw_type"] == t) for t in kc.TYPES}
    log(f"[유형 분류] {counts} / 의도 묶음 {len(res['groups'])}개")
    return res



def annotate_sa(sa: list, skip: bool) -> None:
    """최종 S·A 키워드만 분류 호출에 한 번 더 보내 카테고리·세부 주제·표준 표기를 받는다 (plan_posts 의 분산·표기 통일용).
    같은 호출에서 유형도 다시 받아 구매처 찾기·주제 밖이 섞였으면 kw_type 에 반영한다. 실패하면 값 없이 진행한다."""
    if skip or not sa:
        return
    log(f"[카테고리·표기] S·A {len(sa)}개를 {kc.MODEL} 로 보내 카테고리·세부 주제·표준 표기를 받습니다 (키워드 글자만)")
    res = kc.classify([r["keyword"] for r in sa], with_meta=True)
    if res["status"] != "ok":
        log(f"[주의] 카테고리·표기 분류 실패 → 값 없이 진행: {res.get('error')}")
        return
    for r in sa:
        m = res["meta"].get(r["keyword"]) or {}
        r["kw_category"], r["kw_topic"], r["canonical"] = m.get("category"), m.get("topic"), m.get("canonical")
        r["_meta_done"] = True
        t = res["types"].get(r["keyword"])
        if t in kc.EXCLUDED_TYPES:
            r["kw_type"], r["platform_hint"] = t, None
    log(f"[카테고리·표기] 완료 (표준 표기가 다른 키워드 {sum(1 for r in sa if r.get('canonical') and r['canonical'] != r['keyword'])}개)")


# ---------- 황금 키워드 방식 (등급 판정 + 눈덩이 확장) ----------

class ApiMeter:
    """이번 실행의 네이버 오픈 API 호출 수를 센다 (블로그 검색 하루 25,000회, 데이터랩 하루 1,000회 한도 보호).
    같은 API 가 연달아 실패하면(fail_streak 회) 하루 한도 초과로 본다."""

    def __init__(self, api, fail_streak: int):
        self.calls, self.streak, self.max_streak = {}, {}, fail_streak
        self._orig = api.call
        api.call = self.call

    def call(self, kind: str, **req):
        self.calls[kind] = self.calls.get(kind, 0) + 1
        res = self._orig(kind, **req)
        self.streak[kind] = self.streak.get(kind, 0) + 1 if res is None else 0
        return res

    def n(self, kind: str) -> int:
        return self.calls.get(kind, 0)

    def broken(self, kind: str) -> bool:
        return self.streak.get(kind, 0) >= self.max_streak


def enrich_survivors(api, rows: list, cache: dict, months: list, meter, max_trend: int) -> None:
    """1차 탈락(발행 수·경쟁률)을 통과한 키워드에만 블로그 문서 수·12개월 추세·포화도를 채운다.
    문서 수는 발행 수를 셀 때 받은 total 을 먼저 쓰고, 없을 때만 따로 부른다."""
    need_trend = []
    for r in rows:
        if r.get("blog_docs") is None:
            tot = (r.get("publish") or {}).get("total")
            r["blog_docs"] = int(tot) if tot is not None else cm.blog_docs(api, r["keyword"])
        if r.get("trend_12m") is None:
            need_trend.append(r["keyword"])
    if need_trend:
        if meter.n("trend") + math.ceil(len(need_trend) / cm.TREND_GROUP_MAX) > max_trend or meter.broken("trend"):
            log(f"  [주의] 데이터랩 호출 한도({max_trend}회) 보호: 12개월 추세 {len(need_trend)}개는 비워 둡니다")
        else:
            tr = cm.trends(api, need_trend, months)
            for r in rows:
                if r["keyword"] in tr:
                    r["trend_12m"] = tr[r["keyword"]]
    ranked = sorted((r for r in rows if r.get("blog_docs") is not None and r["monthly_total"] > 0),
                    key=lambda r: r["blog_docs"] / r["monthly_total"])
    for i, r in enumerate(ranked):
        r["saturation"] = round(r["blog_docs"] / r["monthly_total"], 2)
        r["saturation_rank"], r["saturation_of"] = i + 1, len(ranked)
        r["sat_pct"] = i / (len(ranked) - 1) if len(ranked) > 1 else 0.0
    for r in rows:
        if r.get("blog_docs") is not None or r.get("trend_12m") is not None:
            old = cache["metrics"].get(r["keyword"], {})
            cache["metrics"][r["keyword"]] = {
                "ts": time.time(),
                "blog_docs": r["blog_docs"] if r.get("blog_docs") is not None else old.get("blog_docs"),
                "trend_12m": r.get("trend_12m") if r.get("trend_12m") is not None else old.get("trend_12m"),
            }


DEFAULT_SOLVE_SUFFIXES = ["제거", "냄새", "곰팡이", "세탁", "청소", "원인", "하는법", "관리", "보관", "교체주기"]


def need_of(fill: dict) -> dict:
    """배정 가능 수 계산 결과에서 '아직 필요한 것'을 뽑는다.
    naver/wordpress/reserve = 빈자리, info/money = 정보글·수익글 갈래별 모자란 편수(weekly.track_quota 기준: 정보 32 · 수익 13)."""
    if not fill:
        return {"naver": 0, "wordpress": 0, "reserve": 0, "info": 0, "money": 0}
    ts = fill.get("track_short") or {}
    return {"naver": fill["shortage"].get("naver", 0), "wordpress": fill["shortage"].get("wordpress", 0),
            "reserve": fill["shortage"].get("reserve", 0), "info": ts.get("info", 0), "money": ts.get("money", 0)}


def track_of_row(r: dict) -> str:
    """후보의 갈래: 지문의 문제가 '제품 선택(구매)' 이면 money, 그 외 info (지문이 없으면 info)."""
    return r.get("track") or "info"


def seed_priority(r: dict, fill: dict) -> tuple:
    """확장 씨앗으로 쓸 S·A 의 우선순위 (작을수록 먼저).
    자리가 모자란 플랫폼의 유형(네이버는 해결형 기준) → 그 플랫폼에서 아직 한도가 남은 카테고리 → 등급·예상 유입 순."""
    import golden_metrics as gm
    if not fill:
        return (0, 0) + gm.sort_key(r["grade_info"])
    cat = r.get("kw_category")
    need = need_of(fill)
    short_tracks = [t for t in ("info", "money") if need[t] > 0]
    if short_tracks:               # 모자란 갈래(정보·수익)의 S·A 를 먼저
        want = track_of_row(r) in short_tracks
    else:
        want = need["naver"] + need["wordpress"] + need["reserve"] > 0
    room = cat is None or any(fill["cat_counts"][p].get(cat, 0) < fill["cat_max"] for p in ("naver", "wordpress"))
    return (0 if want else 1, 0 if room else 1) + gm.sort_key(r["grade_info"])


def next_seeds(sa_rows: list, used: set, gc: dict, fill: dict = None, pest_words: list = None,
               all_rows: list = None) -> list:
    """S·A 키워드에서 다음 씨앗을 뽑는다.
    기본 3갈래: (1) S·A 키워드 자체(→ 검색광고 연관 키워드를 다시 조회) (2) 화면 연관검색어 (3) 네이버 자동완성.
    정보글 갈래가 모자라면 2갈래를 더한다:
      (4) 정보 변형: 한도가 남은 카테고리의 S·A 대상어 + 문제 단어(제거·냄새·세탁·원인 …) → 정보형 연관 키워드를 끌어옴
      (5) 롱테일: 경쟁이 세서 탈락한 정보형 키워드(검색량 많은 순)의 자동완성 → 경쟁이 덜한 긴 정보형 키워드
    수익글 갈래가 모자라면 1갈래를 더한다:
      (6) 수익 변형: 정보 S·A 의 대상어 + 제품 고르는 말(추천·고르는법·비교·순위) → 제품 선택 키워드를 끌어옴
    갈래마다 같은 몫씩 먼저 뽑고, 모자라면 남는 자리를 다른 갈래에서 채운다. 이미 쓴 씨앗은 뺀다.
    pest_words 가 있으면 해충 키워드와 해충 단어가 들어간 씨앗은 쓰지 않는다 (해충 글 줄이기)."""
    import golden_metrics as gm
    ex = gc["expand"]
    cap = ex["max_new_seeds_per_round"]
    need = need_of(fill)
    solve_mode = bool(fill) and need["info"] > 0
    money_mode = bool(fill) and need["money"] > 0
    pest_words = [norm(x) for x in (pest_words or []) if norm(x)]
    is_pest_text = lambda t: any(p in norm(t) for p in pest_words)
    src = [r for r in sa_rows if not (is_pest_text(r["keyword"]) or is_pest_text(r.get("kw_topic") or ""))]
    if len(src) < len(sa_rows):
        log(f"  해충 키워드 {len(sa_rows) - len(src)}개는 확장 씨앗으로 쓰지 않습니다 (해충 글 줄이기)")
    rows = sorted(src, key=lambda r: seed_priority(r, fill))
    if fill:
        log(f"  아직 필요한 것: 정보글 {need['info']}편 · 수익글 {need['money']}편 / 빈자리 네이버 {need['naver']}·워드프레스 {need['wordpress']}·예비 {need['reserve']}편")
        log(f"  확장 우선 S·A: {', '.join(r['keyword'] for r in rows[:8])}")
    taken = set(used)

    def collect(texts) -> list:
        bucket = []
        for text in texts:
            n = norm(text)
            if n and n not in taken and not is_pest_text(text):
                taken.add(n)
                bucket.append(" ".join(text.split()))
        return bucket

    n_buckets = 3 + (2 if solve_mode else 0) + (1 if money_mode else 0)
    quota = max(1, math.ceil(cap / n_buckets))
    short_tracks = [t for t, on in (("info", solve_mode), ("money", money_mode)) if on]
    if short_tracks:   # 모자란 갈래의 S·A 만 갈래 (1)~(3)에 쓴다
        base = [r for r in rows if track_of_row(r) in short_tracks] or rows
    else:
        base = rows
    own = collect(r["keyword"] for r in base)
    per_row = 4 if solve_mode else 99     # 해결형을 찾을 때는 한 키워드의 연관검색어가 갈래를 독차지하지 않게
    rel = collect(k for r in base for k in ((r.get("serp") or {}).get("related_keywords") or [])[:per_row])
    auto_src = []
    for r in base:
        if len(auto_src) >= cap:
            break
        auto_src += gm.autocomplete(r["keyword"])
    auto = collect(auto_src)
    buckets = [own, rel, auto]
    names = ["S·A 키워드 자체", "화면 연관검색어", "자동완성"]
    if solve_mode:
        suffixes = ex.get("solve_suffixes") or DEFAULT_SOLVE_SUFFIXES
        by_cat = ex.get("solve_suffixes_by_category") or {}
        # (4) 해결형 변형: 네이버에서 한도가 남은 카테고리의 S·A 세부 주제(대상 명사)에 카테고리별 문제 단어를 붙인다
        room_rows = [r for r in rows if track_of_row(r) == "info" and (r.get("kw_category") is None
                     or any(fill["cat_counts"][p].get(r.get("kw_category"), 0) < fill["cat_max"] for p in ("naver", "wordpress")))] or rows
        variants, seen_obj = [], set()
        for i, r in enumerate(room_rows):
            obj = " ".join((r.get("kw_topic") or r.get("canonical") or r["keyword"]).split())
            if norm(obj) in seen_obj:
                continue
            seen_obj.add(norm(obj))
            sl = by_cat.get(r.get("kw_category") or "") or suffixes
            k = len(seen_obj) - 1
            for j in range(2):     # 대상어마다 문제 단어 2개씩 돌려 가며
                suf = sl[(2 * k + j) % len(sl)]
                if norm(suf) not in norm(obj):
                    variants.append(f"{obj} {suf}")
        solv = collect(variants)
        # (5) 롱테일: 경쟁이 세서 탈락한 해결형 키워드(검색량 많은 순)의 자동완성
        heads = sorted((r for r in (all_rows or []) if track_of_row(r) == "info" and r.get("grade") == "탈락"
                        and not is_pest_text(r["keyword"])),
                       key=lambda r: -(r.get("monthly_total") or 0))
        long_src = []
        for r in heads:
            if len(long_src) >= 2 * cap:
                break
            long_src += [t for t in gm.autocomplete(r["keyword"]) if norm(t) != norm(r["keyword"])]
        longt = collect(long_src)
        buckets += [solv, longt]
        names += ["정보 변형(대상어+문제 단어)", "롱테일(경쟁 센 정보형의 자동완성)"]
    if money_mode:
        # (6) 수익 변형: 정보 S·A 의 대상어에 제품 고르는 말을 붙인다 (정보 주제에서 파생된 제품·도구 씨앗)
        info_rows = [r for r in rows if track_of_row(r) == "info"] or rows
        buy_sf, mv, seen_o = ["추천", "고르는법", "비교", "순위"], [], set()
        for r in info_rows:
            obj = " ".join((r.get("kw_topic") or r.get("canonical") or r["keyword"]).split())
            if norm(obj) in seen_o:
                continue
            seen_o.add(norm(obj))
            mv.append(f"{obj} {buy_sf[(len(seen_o) - 1) % len(buy_sf)]}")
        buckets.append(collect(mv))
        names.append("수익 변형(대상어+제품 고르는 말)")

    pick = [b[:quota] for b in buckets]
    left = [b[quota:] for b in buckets]
    room = cap - sum(len(x) for x in pick)
    for i in range(len(buckets)):     # 모자라는 몫은 남은 후보로 채운다
        take = left[i][:max(0, room)]
        pick[i] += take
        room -= len(take)
    out = [(t, []) for bucket in pick for t in bucket]
    log(f"  다음 씨앗 {len(out)}개 (" + " / ".join(f"{n} {len(p)}" for n, p in zip(names, pick)) + ")")
    return out[:cap]


_STATE = {}


def golden_run(seeds, cfg, cache, fit, api, months, started, args, max_related) -> int:
    import golden_metrics as gm
    gc = gm.golden_config(cfg)
    ex = gc["expand"]
    # 황금 방식은 검색량 범위를 golden 에서 따로 읽는다 (경쟁률·등급이 걸러주므로 더 넓게)
    cfg = {**cfg, "volume_min": gc["volume_min"], "volume_max": gc["volume_max"],
           "saturation_keep_ratio": gc["saturation_keep_ratio"]}
    days = cfg["cache_days"]
    t0 = time.time()
    deadline = t0 + ex["max_hours"] * 3600
    gcache = gm.load_cache()
    _STATE["gm"], _STATE["gcache"] = gm, gcache      # 한도로 끊기면 main 이 이 캐시를 저장한다
    all_rows, infos, seen, used = [], [], set(), set()
    groups_all, rounds, serp_errors = [], [], []
    cls_cost, cls_status = 0.0, "skipped"
    analyzed, stop, cur = 0, "S+A 목표 달성", seeds
    today = date.fromisoformat(args.today) if getattr(args, "today", None) else date.today()
    fill_mode = bool(ex.get("stop_on_fill")) and not args.no_classify
    fill, pest_words = None, None
    if fill_mode:
        try:
            import plan_posts as pp
            wk = pp.load_weekly(Path(args.filter))
            pest_words = wk["pest_words"] if wk.get("max_pest_per_platform") is not None else None
            goal = 7 * (wk["posts_per_day"]["naver"] + wk["posts_per_day"]["wordpress"])
            log(f"[황금 키워드] 종료 기준: 배정 가능 수 (실제 기획 규칙 적용 후 네이버 {7 * wk['posts_per_day']['naver']} + "
                f"워드프레스 {7 * wk['posts_per_day']['wordpress']} + 예비 {wk['reserve_count']} = {goal + wk['reserve_count']}편이 다 차면 멈춤, 기준 주 {pp.next_monday(today)} 시작)")
        except Exception as e:
            fill_mode = False
            log(f"[주의] 배정 가능 수 계산 준비 실패 → 예전 기준(S+A {ex['min_sa']}개)으로 진행: {e}")
    if not fill_mode:
        log(f"[황금 키워드] 종료 기준: S+A {ex['min_sa']}개")
    track_quota = (wk.get("track_quota") if fill_mode else None) or {}
    if fill_mode and track_quota:
        ti = sum(v["info"] for v in track_quota.values())
        tm = sum(v["money"] for v in track_quota.values())
        fgf = wk.get("fill_goal_factor", 1.5)
        log(f"[황금 키워드] 갈래별 목표: 월 검색량 {wk.get('schedule_volume_min', 150)} 이상이고 유형·등급을 통과한 후보가 정보글 {math.ceil(ti * fgf)}개(정원 {ti}×{fgf}) · 수익글 {math.ceil(tm * fgf)}개(정원 {tm}×{fgf})가 모이면 멈춤 (한 갈래가 모자라면 다른 갈래로 채우지 않고, 모자란 쪽 씨앗을 다음 회차에 더 넣음)")
    # 지문 색인(1단계): 1차 탈락을 통과한 키워드에 지문(대상|문제)을 붙여 기존 글·같은 묶음을 코드로 가른다
    fpx, post_keys, fp_rep, pair_cap_log = None, set(), {}, []
    if not args.no_classify:
        try:
            import fingerprint as fpm
            import plan_posts as pp
            wk_ = pp.load_weekly(Path(args.filter))
            ek, _ = pp.load_existing(OUT_DIR / "existing_keywords.json")
            mon = pp.next_monday(today)
            prev_kw = []
            for k in range(1, int(wk_.get("avoid_past_weeks", 1)) + 1):
                pf = OUT_DIR / f"plan_{(mon - timedelta(days=7 * k)).strftime('%Y%m%d')}.json"
                try:
                    prev_kw += [p["keyword"] for p in json.loads(pf.read_text(encoding="utf-8")).get("plans", []) if p.get("role") == "scheduled"]
                except (OSError, ValueError, KeyError):
                    pass
            posts_ = fpm.existing_post_list(ek, wk_, prev_kw)
            fpx = fpm.FingerprintIndex()
            fpx.pair_budget = int(wk_.get("max_pair_calls", 30))      # 한 번 실행에서 쌍 확인(Claude) 호출 상한
            fpx.ensure_posts(posts_, log)
            post_keys = {p["key"] for p in posts_}
            log(f"[황금 키워드] 지문 색인: 기존 글·지난 기획 {len(post_keys)}개 (색인 키워드 {len(fpx.keywords)}개) — 기존 글과 같은 지문은 정밀 분석 전에 제외")
        except UsageLimitError:
            raise
        except Exception as e:
            fpx = None
            log(f"[주의] 지문 색인을 준비하지 못해 기존 글 비교 없이 진행합니다: {(str(e).splitlines() or [''])[0][:150]}")
    meter = ApiMeter(api, int(ex.get("api_fail_streak", 15)))
    max_blog, max_trend = int(ex.get("max_blog_calls", 15000)), int(ex.get("max_trend_calls", 800))
    api_stop = None
    log(f"[황금 키워드] API 보호: 이번 실행 블로그 검색 최대 {max_blog:,}회 / 데이터랩 최대 {max_trend:,}회 / 연속 {meter.max_streak}회 실패하면 하루 한도 초과로 보고 멈춤")
    log(f"[황금 키워드] 확장 최대 {ex['max_rounds']}회 / 정밀 분석 최대 {ex['max_analyzed']}개 / 최대 {ex['max_hours']}시간")
    log(f"[황금 키워드] 검색량 범위 {cfg['volume_min']:,}~{cfg['volume_max']:,} / 포화도 필터 비율 {cfg['saturation_keep_ratio']} (filter.json golden)")
    if gc.get("compete_mode") == "title":
        log(f"[황금 키워드] 경쟁 측정: 제목 기준 — 상위 100개 중 제목 일치 {gc['drop_top100_title']}개 이상이면 탈락, "
            f"경쟁률(월 검색량÷30일 제목 일치 글) S {gc['s_rate_min_title']}+ (상위 100 제목 일치 {gc['s_top100_title_max']}개 이하) / A {gc['a_rate_min_title']}+")
    else:
        log(f"[황금 키워드] 경쟁 측정: 예전 방식 — 최근 30일 발행 {gc['drop_posts_30d']:,}+ 탈락, 경쟁률 S {gc['s_rate_min']}+ / A {gc['a_rate_min']}+")

    for rnd in range(ex["max_rounds"] + 1):
        log(f"===== {'처음 씨앗' if rnd == 0 else f'확장 {rnd}회차'}: 씨앗 {len(cur)}개 =====")
        cache_before = cstat_snapshot()
        # 확장 회차: 씨앗 단어 포함 규칙 대신 유형 분류의 '주제 범위 밖' 판정으로 거른다 (분류를 건너뛰면 규칙을 그대로 쓴다)
        relax = rnd > 0 and not args.no_classify
        if rnd > 0:
            log("  (확장 회차: 씨앗 단어 포함 규칙 없이 연관 키워드를 가져오고, 유형 분류에서 '주제 범위 밖'을 걸러냅니다)" if relax
                else "  (--no-classify 라서 씨앗 단어 포함 규칙을 그대로 씁니다)")
        new_rows, seeds_done, dup_n = [], 0, 0
        rstat = {"related": 0, "basic_pass": 0, "dropped": {"씨앗단어없음": 0, "제외단어": 0, "검색량범위밖": 0}}
        for i, (seed, core) in enumerate(cur, 1):
            if time.time() > deadline:
                stop = "전체 시간 제한"
                break
            used.add(norm(seed))
            log(f"[{i}/{len(cur)}] 씨앗: {seed}" + (f"  (핵심 단어: {', '.join(core)})" if core else ""))
            try:
                cands, info = metrics_stage(api, seed, core, cfg, months, cache, max_related, relax=relax, lite=True)
            except Exception as e:
                log(f"  실패: {e}")
                infos.append({"seed": seed, "core_words": core, "round": rnd, "error": str(e)})
                continue
            infos.append({**info, "round": rnd})
            seeds_done += 1
            rstat["related"] += info["related_total"]
            rstat["basic_pass"] += len(cands)
            for k, v in info["dropped"].items():
                rstat["dropped"][k] += v
            for r in cands:
                if r["keyword"] not in seen:
                    seen.add(r["keyword"])
                    r["found_round"] = rnd
                    new_rows.append(r)
                else:
                    dup_n += 1
        save_cache(cache)
        all_rows += new_rows
        for r in new_rows:
            r["score"], r["serp"], r["serp_cached"] = None, None, False
            r["grade"], r["drop_reason"] = None, None
            if not r["passed_filter"]:
                r["drop_reason"] = "기본 필터 미통과: " + (r.get("filter_note") or "조건 밖")
        passed = [r for r in new_rows if r["passed_filter"]]
        sat_drop = len(new_rows) - len(passed)
        for r in passed:
            r["_pre"] = demand_score(r, cfg) + saturation_score(r, cfg) + fit_score(r, cfg, fit)

        # 유형 분류
        cls = classify_stage(passed, args.no_classify)
        cls_status = cls["status"]
        groups_all += cls.get("groups", [])
        cls_cost += ((cls.get("usage") or {}).get("total_cost_usd") or 0)
        if relax and passed and cls["status"] != "ok":
            # 분류가 실패하면 '주제 범위 밖'을 가려낼 수 없으므로, 규칙 없이 가져온 확장 키워드는 쓰지 않는다
            for r in passed:
                r["exclude_reason"] = "유형 분류 실패로 주제 확인 불가 (확장 키워드)"
            log("[주의] 확장 회차 유형 분류가 실패해 이번 회차 키워드는 쓰지 않습니다.")
        for r in passed:
            if r.get("exclude_reason"):
                r["drop_reason"] = r["exclude_reason"]
        eligible = [r for r in passed if r.get("exclude_reason") is None]
        cls_drop = {kc.TYPE_KO[t]: sum(1 for r in passed if r.get("kw_type") == t) for t in kc.EXCLUDED_TYPES}

        # 최근 30일 발행·경쟁률 (API만) → 탈락
        log(f"[발행·경쟁률] {len(eligible)}개 계산 (API만)")
        blog_before = meter.n("blog")
        for r in eligible:
            if time.time() > deadline:
                r["drop_reason"] = "전체 시간 제한으로 미측정"
                continue
            if api_stop is None and meter.broken("blog"):
                api_stop = f"블로그 검색 API 가 연속 {meter.max_streak}회 실패 (하루 한도 초과 의심)"
                log(f"[중단] {api_stop} → 남은 키워드는 측정하지 않습니다")
            elif api_stop is None and meter.n("blog") >= max_blog:
                api_stop = f"이번 실행 블로그 검색 호출 상한({max_blog:,}회) 도달"
                log(f"[중단] {api_stop} → 남은 키워드는 측정하지 않습니다")
            if api_stop:
                r["drop_reason"] = "API 한도 보호로 미측정"
                continue
            r["publish"] = gm.publish_cached(api, r, gcache, days, gc)
            cstat("최근 30일 발행 수", bool(r["publish"].get("from_cache")))
            drop = gm.pre_drop(r["publish"], gc)
            if drop:
                r["drop_reason"] = drop
                measured = r["publish"].get("posts_30d") is not None or r["publish"].get("top100_title") is not None
                r["grade"] = "탈락" if measured else None
        gm.save_cache(gcache)
        # 1차 탈락을 통과한 키워드에만 문서 수·12개월 추세·포화도를 채우고 예비 점수를 다시 계산
        alive = [r for r in eligible if r.get("publish") and not r["drop_reason"]]
        enrich_survivors(api, alive, cache, months, meter, max_trend)
        save_cache(cache)
        for r in alive:
            r["_pre"] = demand_score(r, cfg) + saturation_score(r, cfg) + fit_score(r, cfg, fit)
        log(f"[API 사용] 이번 회차 블로그 검색 {meter.n('blog') - blog_before:,}회 / 누적 블로그 {meter.n('blog'):,}회, 데이터랩 {meter.n('trend'):,}회")
        if fpx is not None:
            cand = [r for r in eligible if r.get("publish") and not r["drop_reason"]]
            try:
                fpx.ensure_keywords([r["keyword"] for r in cand], log)
                fpx.ensure_product([r["keyword"] for r in cand], log)             # 물건 이름이면 갈래 money, 푸는 문제(solves)도 색인에 저장
                fpx.ensure_alias(log)
                kws_ = [r["keyword"] for r in sorted(cand, key=lambda r: -(r.get("monthly_total") or 0))]
                # 탐색 단계: 후보 ↔ 기존 글 쌍만 확인한다(검색량 큰 후보부터, 호출 상한까지). 후보끼리는 지문 3칸이 같은 것만 같은 글로 본다(쌍 확인 없음).
                dups_ = fpm.dup_vs_posts(fpx, kws_, post_keys, log)
                groups_ = fpm.cluster_exact(fpx, [k for k in kws_ if k not in dups_])
            except UsageLimitError:
                raise
            except Exception as e:
                log(f"[주의] 키워드 지문 판정 실패 → 이번 회차는 지문 비교 없이 진행: {(str(e).splitlines() or [''])[0][:150]}")
            else:
                rep_of = {k: g[0] for g in groups_ for k in g[1:]}                  # 검색량이 큰 것이 대표(입력 순서 유지)
                n_ex = n_same = n_cap = 0
                for r in cand:
                    r["fingerprint"] = fpx.fp_of(r["keyword"])
                    r["track"] = fpx.track_of(r["keyword"])        # 갈래(info·money): 지문의 문제가 '제품 선택(구매)' 이면 money
                    fpx.record_meta(r["keyword"], kw_type=r.get("kw_type"))
                    if r["keyword"] in dups_:
                        r["dup_existing"] = dups_[r["keyword"]]
                        r["drop_reason"] = f"기존 글과 같은 글 (지문 {r['fingerprint']}: '{dups_[r['keyword']]}')"
                        n_ex += 1
                    elif r["keyword"] in rep_of:
                        r["drop_reason"] = f"같은 글 묶음의 대표 '{rep_of[r['keyword']]}' 만 분석 (지문 {r['fingerprint']})"
                        n_same += 1
                    elif r["keyword"] in fpx.capped_kw:
                        r["drop_reason"] = f"쌍 확인 호출 상한({fpx.pair_budget}회) 초과로 이번 주 후보에서 뺌 (기존 글과 같은 글인지 확인 못 함)"
                        r["pair_capped"] = True
                        n_cap += 1
                fpx.save()
                log(f"[지문] 1차 통과 {len(cand)}개 → 기존 글과 같은 글 {n_ex}개 제외, 같은 글 묶음의 대표만 남기고 {n_same}개 제외 (검색량 큰 것이 대표)")
                if n_cap:
                    pair_cap_log.append({"round": rnd, "dropped": n_cap, "skipped_pairs": fpx.cap_skipped_pairs,
                                         "keywords": [r["keyword"] for r in cand if r.get("pair_capped")][:200]})
                    log(f"[쌍 확인 상한] 이번 실행 쌍 확인 {fpx.pair_calls}회가 상한 {fpx.pair_budget}회에 닿아 후보 {n_cap}개를 이번 주 후보에서 뺌 "
                        f"(검색량 큰 후보부터 확인함, 묻지 못한 쌍 {fpx.cap_skipped_pairs}개)")
        nd = need_of(fill)
        def want_first(r):     # 정밀 분석 상한이 있으니 모자란 갈래(정보·수익)의 키워드부터 분석
            return 0 if not fill or (nd["info"] == 0 and nd["money"] == 0) or (nd.get(track_of_row(r)) or 0) > 0 else 1
        survivors = sorted((r for r in eligible if r.get("publish") and not r["drop_reason"]),
                           key=lambda r: (want_first(r), -r["_pre"]))
        room = max(0, ex["max_analyzed"] - analyzed)
        batch = survivors[:room]
        for r in survivors[room:]:
            r["drop_reason"] = f"정밀 분석 상한({ex['max_analyzed']}개) 초과로 미분석"
        pre_dropped = sum(1 for r in eligible if r.get("grade") == "탈락")
        log(f"[1차 탈락] 통과 {len(survivors)}개 / 탈락 {pre_dropped}개 → 화면·상위 글 분석 {len(batch)}개")
        funnel = {"new_seeds": len(cur), "seeds_done": seeds_done, "related_total": rstat["related"],
                  "dropped_basic": rstat["dropped"], "basic_pass": rstat["basic_pass"], "duplicates": dup_n,
                  "new_keywords": len(new_rows), "dropped_saturation": sat_drop, "classified": len(passed),
                  "dropped_classify": cls_drop, "first_select_target": len(eligible),
                  "first_select_dropped": pre_dropped, "first_select_passed": len(survivors),
                  "analyze": len(batch)}
        d_ = rstat["dropped"]
        log(f"[{rnd}회차 요약] 새 씨앗 {len(cur)}개(조회 성공 {seeds_done}) → 조회한 연관 키워드 {rstat['related']}개")
        log(f"    필터별 탈락: 씨앗 단어 없음 {d_['씨앗단어없음']} / 제외 단어 {d_['제외단어']} / 검색량 범위 밖 {d_['검색량범위밖']}"
            f" (기본 필터 통과 {rstat['basic_pass']}, 이미 나온 키워드 {dup_n}개 빼면 새 키워드 {len(new_rows)})")
        log(f"    포화도 상위 {sat_drop} / 유형 분류 {'·'.join(f'{k} {v}' for k, v in cls_drop.items())}"
            f" / 발행·경쟁률 탈락 {pre_dropped}")
        log(f"    → 1차 선별(발행·경쟁률) 대상 {len(eligible)}개 → 통과 {len(survivors)}개 → 정밀 분석 대상 {len(batch)}개")

        # 화면 수집 + 상위 글 분석 + 등급
        serp_errors += serp_stage(batch, cfg, cache, True, deadline)
        for i, r in enumerate(batch, 1):
            if not r.get("serp"):
                r["drop_reason"] = "화면 수집 실패 또는 시간 제한으로 미분석"
                continue
            if time.time() > deadline:
                r["drop_reason"] = "전체 시간 제한으로 미분석"
                continue
            log(f"[상위 글 분석 {i}/{len(batch)}] {r['keyword']}")
            try:
                r["golden"] = gm.measure(api, r, r["serp"], gcache, days, publish=r["publish"])
                cstat("상위 글 분석", bool(r["golden"].get("from_cache")))
            except Exception as e:
                r["drop_reason"] = "측정 실패: " + (str(e).splitlines() or [""])[0][:100]
                continue
            tg = trend_growth(r.get("trend_12m"))
            gr = gm.grade_keyword(r["golden"], r["monthly_total"], gc, None if tg is None else round(tg, 2))
            r["grade"], r["drop_reason"], r["grade_info"] = gr["grade"], gr.get("drop_reason"), gr
            r["expected_traffic"] = gr.get("expected_traffic")
            d, sat, sc, f = demand_score(r, cfg), saturation_score(r, cfg), screen_score(r["serp"], cfg), fit_score(r, cfg, fit)
            r["score"] = {"demand": round(d, 1), "saturation": round(sat, 1), "screen": round(sc, 1),
                          "fit": round(f, 1), "total": round(d + sat + sc + f, 1)}
        analyzed += len(batch)

        sa = [r for r in all_rows if r.get("grade") in ("S", "A") and r.get("score")]
        counts = {g: sum(1 for r in all_rows if r.get("grade") == g) for g in ("S", "A", "B", "탈락")}
        fill = None
        if fill_mode and sa and not api_stop:
            # 새로 나온 S·A 에만 카테고리·세부 주제·표준 표기를 받아 두고, 실제 기획 규칙으로 배정 가능 수를 계산한다
            annotate_sa([r for r in sa if not r.get("_meta_done")], False)
            if fpx is not None:     # 분류에서 알게 된 카테고리·표준 표기를 색인에 덧붙임
                for r in sa:
                    fpx.record_meta(r["keyword"], category=r.get("kw_category"), canonical=r.get("canonical"))
            try:
                ranked = sorted(sa, key=lambda r: gm.sort_key(r["grade_info"]))
                fill = pp.simulate_fill([compact(i, r) for i, r in enumerate(ranked, 1)], groups_all,
                                        Path(args.filter), today)
            except Exception as e:
                log(f"[주의] 배정 가능 수 계산 실패 → 이번 회차는 S+A {ex['min_sa']}개 기준으로 판단: {(str(e).splitlines() or [''])[0][:150]}")
        cache_round = cstat_delta(cstat_snapshot(), cache_before)
        rounds.append({"round": rnd, "seeds": [sd for sd, _ in cur], "new_keywords": len(new_rows),
                       "analyzed": len(batch), "grades_total": counts, "sa_total": len(sa),
                       "relaxed": relax, "funnel": funnel, "cache_usage": cache_round,
                       "api_calls": {"blog": meter.n("blog"), "trend": meter.n("trend")},
                       **({"fill": {k: fill[k] for k in ("shortage", "short_total", "track_short", "track_scheduled", "track_candidates", "track_goal", "scheduled", "reserve",
                                                          "cat_counts", "pest_counts", "candidates", "from_bank")}}
                          if fill else {})})
        log(f"[{rnd}회차 캐시] {cstat_text(cache_round)}")
        log(f"[{rnd}회차 결과] 등급 누적 {counts} / S+A {len(sa)}개 / 정밀 분석 누적 {analyzed}개 / 경과 {(time.time() - t0) / 60:.0f}분")
        if fill:
            sh = fill["shortage"]
            log(f"[{rnd}회차 배정 가능 수] 일정 {fill['scheduled']}편 + 예비 {fill['reserve']}편 "
                f"(후보 {fill['candidates']}개, 은행 {fill['from_bank']}개 포함) / 부족: 네이버 {sh['naver']}, 워드프레스 {sh['wordpress']}, 예비 {sh['reserve']}")
            for pl, ko in (("naver", "네이버"), ("wordpress", "워드프레스")):
                cc = fill["cat_counts"][pl]
                log(f"    {ko} 카테고리별 (한도 {fill['cat_max']}): " + (", ".join(f"{c} {n}" for c, n in sorted(cc.items(), key=lambda x: -x[1])) or "없음")
                    + (f" / 해충 {fill['pest_counts'][pl]}편 (한도 {fill['pest_max']})" if fill.get("pest_max") is not None else ""))
            ts = fill["track_short"]
            tc, tg = fill.get("track_candidates") or {}, fill.get("track_goal") or {}
            log(f"    갈래별 통과 후보(월 검색량 하한·유형·등급 통과): 정보글 {tc.get('info')}/{tg.get('info')} · 수익글 {tc.get('money')}/{tg.get('money')} → 부족 정보 {ts['info']} · 수익 {ts['money']} (일정 중 정보 {fill['track_scheduled']['info']} · 수익 {fill['track_scheduled']['money']})")
            if fill["short_total"] == 0 and ts["info"] == 0 and ts["money"] == 0:
                stop = "배정 가능 수 달성 (정보글·수익글 갈래별 목표를 모두 채움)"
                break
        elif not api_stop and len(sa) >= ex["min_sa"]:
            stop = "S+A 목표 달성"
            break
        if api_stop:
            stop = api_stop
            break
        if rnd >= ex["max_rounds"]:
            stop = "최대 확장 횟수"
            break
        if analyzed >= ex["max_analyzed"]:
            stop = "정밀 분석 상한"
            break
        if time.time() > deadline:
            stop = "전체 시간 제한"
            break
        cur = next_seeds(sa, used, gc, fill, pest_words, all_rows)
        if not cur:
            stop = "더 확장할 씨앗 없음"
            break

    for r in all_rows:
        r.pop("_pre", None)
        r.pop("sat_pct", None)
    sa = sorted((r for r in all_rows if r.get("grade") in ("S", "A") and r.get("score")),
                key=lambda r: gm.sort_key(r["grade_info"]))
    annotate_sa([r for r in sa if not r.get("_meta_done")], args.no_classify)
    if fpx is not None:
        for r in sa:
            fpx.record_meta(r["keyword"], category=r.get("kw_category"), canonical=r.get("canonical"))
        fpx.save()
    for r in all_rows:
        r.pop("_meta_done", None)
        r.pop("_dup_checked", None)
    passed_all = [r for r in all_rows if r["passed_filter"]]
    stamp = started.strftime("%Y%m%d_%H%M%S")
    grade_counts = {g: sum(1 for r in all_rows if r.get("grade") == g) for g in ("S", "A", "B", "탈락")}
    grade_counts["미분석"] = sum(1 for r in all_rows if r.get("grade") is None)
    result = {
        "collected_at": started.isoformat(timespec="seconds"),
        "mode": "golden",
        "trend_months": months,
        "filter_used": cfg,
        "api_status": {"blog_search": api.status("blog"), "datalab": api.status("trend")},
        "seeds": infos,
        "golden_run": {"rounds": rounds, "stopped_because": stop, "analyzed": analyzed,
                       "cache_usage": cstat_snapshot(), "volume_range": [cfg["volume_min"], cfg["volume_max"]],
                       "elapsed_minutes": round((time.time() - t0) / 60, 1), "grade_counts": grade_counts,
                       "target_sa": ex["min_sa"], "sa_total": len(sa),
                       "stop_rule": "배정 가능 수" if fill_mode else "S+A 개수", "track_quota": track_quota,
                       "api_calls": {"blog": meter.n("blog"), "trend": meter.n("trend")},
                       "pair_check": {"calls": fpx.pair_calls if fpx else 0, "budget": fpx.pair_budget if fpx else None,
                                      "cap_hit": bool(fpx and fpx.cap_hit), "skipped_pairs": fpx.cap_skipped_pairs if fpx else 0,
                                      "capped": pair_cap_log},
                       "final_fill": ({k: fill[k] for k in ("shortage", "short_total", "track_short", "track_scheduled", "scheduled", "reserve", "cat_counts", "pest_counts")}
                                      if fill else None)},
        "counts": {"candidates": len(all_rows), "passed_filter": len(passed_all),
                   "classify_excluded": sum(1 for r in passed_all if r.get("exclude_reason")),
                   "analyzed": analyzed, "scored": len(sa)},
        "classification": {
            "status": cls_status, "model": kc.MODEL, "max_turns": int(kc.MAX_TURNS),
            "type_counts": {kc.TYPE_KO[t]: sum(1 for r in passed_all if r.get("kw_type") == t) for t in kc.TYPES}
            if cls_status == "ok" else None,
            "usage": {"total_cost_usd": cls_cost},
            "intent_groups": groups_all,
            "excluded": [{"keyword": r["keyword"], "type": kc.TYPE_KO[r["kw_type"]], "reason": r["exclude_reason"]}
                         for r in passed_all if r.get("exclude_reason")],
        },
        "serp_errors": serp_errors,
        "ranking": [compact(i, r) for i, r in enumerate(sa, 1)],
        "keywords": all_rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result_path = OUT_DIR / f"result_{stamp}.json"
    write_text_retry(result_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    top = {"generated_at": result["collected_at"], "result_file": result_path.name,
           "top10": [compact(i, r) for i, r in enumerate(sa[:10], 1)]}
    top_text = json.dumps(top, ensure_ascii=False, indent=2)
    write_text_retry(OUT_DIR / "top10.json", top_text + "\n")
    write_text_retry(OUT_DIR / "ranking.json", json.dumps(
        {"generated_at": result["collected_at"], "result_file": result_path.name,
         "intent_groups": groups_all, "mode": "golden", "ranking": result["ranking"]}, ensure_ascii=False, indent=2) + "\n")
    print(top_text)
    log(f"[저장됨] {result_path}")
    log(f"[캐시 사용 합계] {cstat_text(cstat_snapshot())}")
    log(f"[완료] 후보 {len(all_rows)} → 정밀 분석 {analyzed} → 등급 {grade_counts} / S+A {len(sa)}개 (종료 이유: {stop}, {result['golden_run']['elapsed_minutes']}분)"
        + (f" / 마지막 배정 가능 수: 일정 {fill['scheduled']}편 + 예비 {fill['reserve']}편, 부족 {fill['shortage']}, 갈래별 부족 {fill['track_short']}" if fill else ""))
    log(f"[API 사용 합계] 블로그 검색 {meter.n('blog'):,}회 / 데이터랩 {meter.n('trend'):,}회 (하루 한도: 블로그 25,000 · 데이터랩 1,000, 다른 사용분 포함)")
    if fill and (fill["track_short"]["info"] or fill["track_short"]["money"]):
        log(f"[갈래 부족] 정보글 {fill['track_short']['info']}편 · 수익글 {fill['track_short']['money']}편이 모자란 채로 끝났습니다 (종료 이유: {stop}). 모자란 갈래는 다른 갈래로 채우지 않습니다.")
    return 0


# ---------- 메인 ----------

def compact(rank: int, r: dict) -> dict:
    s, sc = r.get("serp") or {}, r["score"]
    return {
        "rank": rank, "keyword": r["keyword"], "seed": r["seed"], "score": sc,
        "monthly_total": r["monthly_total"], "blog_docs": r["blog_docs"], "saturation": r["saturation"],
        "saturation_rank": f"{r['saturation_rank']}/{r['saturation_of']}" if r.get("saturation_rank") else None,
        "trend_growth": None if trend_growth(r.get("trend_12m")) is None else round(trend_growth(r["trend_12m"]), 2),
        "trend_steady": trend_steady(r.get("trend_12m")),
        "smart_blocks": s.get("smart_blocks"), "related_keywords": s.get("related_keywords"),
        "has_ad": s.get("has_ad"), "has_shopping": s.get("has_shopping"),
        "has_ai_briefing": s.get("has_ai_briefing"),
        "kw_type": r.get("kw_type"), "platform_hint": r.get("platform_hint"),
        **({"kw_category": r.get("kw_category"), "kw_topic": r.get("kw_topic"), "canonical": r.get("canonical")}
           if r.get("kw_category") or r.get("kw_topic") or r.get("canonical") else {}),
        **({"dup_existing": r["dup_existing"]} if r.get("dup_existing") else {}),
        **({"fingerprint": r["fingerprint"]} if r.get("fingerprint") else {}),
        "ave_pc_clk": r.get("ave_pc_clk"), "ave_mobile_clk": r.get("ave_mobile_clk"), "comp_idx": r.get("comp_idx"),
        **({"golden": r["golden"]} if r.get("golden") else {}),
        **({"grade": r["grade"], "expected_traffic": r.get("expected_traffic"), "found_round": r.get("found_round"),
            "grade_info": r.get("grade_info")} if r.get("grade") else {}),
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="키워드 발굴 파이프라인 (지표 → 필터 → 화면 → 채점)")
    ap.add_argument("seed", nargs="*", help="씨앗 키워드 1개")
    ap.add_argument("--file", help="씨앗 키워드 목록 파일 (한 줄에 하나)")
    ap.add_argument("--filter", default=str(DEFAULT_FILTER), help="설정 파일 (기본: tools/filter.json)")
    ap.add_argument("--max-related", type=int, help="씨앗당 연관 키워드 수 (filter.json 값을 이번만 덮어씀)")
    ap.add_argument("--no-classify", action="store_true", help="유형 분류·의도 묶기(Claude 호출)를 건너뜀")
    ap.add_argument("--max-serp", type=int, help="화면 수집 키워드 수 (filter.json 값을 이번만 덮어씀)")
    ap.add_argument("--today", help="기준일 YYYY-MM-DD (확장 종료 판단용 배정 가능 수를 이 날짜의 다음 주 기준으로 계산, 기본: 오늘)")
    ap.add_argument("--golden", action="store_true",
                    help="황금 키워드 방식으로 실행: 발행·경쟁률 → 탈락 → 화면·상위 글 분석 → 등급 → 부족하면 확장 (filter.json golden.enabled=true 면 항상 이 방식)")
    args = ap.parse_args()

    if args.today:
        try:
            date.fromisoformat(args.today)
        except ValueError:
            log("--today 는 YYYY-MM-DD 형식이어야 합니다 (예: 2026-11-13).")
            return 2
    single = " ".join(args.seed).strip()
    if args.file and single:
        log("씨앗 키워드와 --file 은 함께 쓸 수 없습니다.")
        return 2
    if args.file:
        p = Path(args.file)
        if not p.is_file():
            log(f"[실패] 목록 파일을 찾을 수 없습니다: {p}")
            return 2
        seeds = cm.read_seed_file(p)
    else:
        seeds = [single] if single else []
    parsed, seen_seed = [], set()
    for s in seeds:
        seed, core = parse_seed(s)
        if seed and (seed, tuple(core)) not in seen_seed:
            seen_seed.add((seed, tuple(core)))
            parsed.append((seed, core))
    seeds = parsed
    if not seeds:
        ap.print_usage(sys.stderr)
        log("씨앗 키워드 또는 --file 을 주세요.")
        return 2
    try:
        cfg = load_filter(Path(args.filter))
    except (OSError, ValueError) as e:
        log(f"[실패] 설정 파일을 읽지 못했습니다: {e}")
        return 2
    if cm.config.USE_MOCK:
        log("[실패] 검색광고 API 키가 .env 에 없습니다: " + ", ".join(cm.config.MISSING_NAVER_VARS))
        return 2
    max_related = args.max_related if args.max_related is not None else cfg["max_related_per_seed"]
    max_serp = args.max_serp if args.max_serp is not None else cfg["max_serp_keywords"]

    cache, fit = load_cache(), load_fit()
    api = cm.OpenApi()
    months = cm.last_12_months(date.today())
    started = datetime.now().astimezone()

    if args.golden or (cfg.get("golden") or {}).get("enabled"):
        try:
            return golden_run(seeds, cfg, cache, fit, api, months, started, args, max_related)
        except UsageLimitError as e:
            # 진행 저장: 지표·발행·화면·상위 글 캐시와 지문 색인은 이미 단계마다 저장돼 있다. 마지막으로 한 번 더 저장하고 멈춘다.
            save_cache(cache)
            try:
                _STATE["gm"].save_cache(_STATE["gcache"])
            except Exception:
                pass
            try:
                write_text_retry(OUT_DIR / "resume_state.json", json.dumps(
                    {"stopped_at": datetime.now().astimezone().isoformat(timespec="seconds"), "stage": "run_pipeline",
                     "reason": "Claude 사용 한도", "detail": (str(e).splitlines() or [""])[0][:200],
                     "how_to_resume": "같은 명령을 다시 실행 (캐시와 지문 색인을 이어서 씀)"}, ensure_ascii=False, indent=1) + chr(10))
            except OSError:
                pass
            log(limit_message(e, "키워드 탐색"))
            return LIMIT_EXIT

    # 1·2단계
    all_rows, infos, seen = [], [], set()
    for i, (seed, core) in enumerate(seeds, 1):
        log(f"[{i}/{len(seeds)}] 씨앗: {seed}" + (f"  (핵심 단어: {', '.join(core)})" if core else ""))
        try:
            cands, info = metrics_stage(api, seed, core, cfg, months, cache, max_related)
        except Exception as e:
            log(f"  실패: {e}")
            infos.append({"seed": seed, "core_words": core, "error": str(e)})
            continue
        infos.append(info)
        for r in cands:
            if r["keyword"] not in seen:
                seen.add(r["keyword"])
                all_rows.append(r)
    save_cache(cache)

    passed = [r for r in all_rows if r["passed_filter"]]
    for r in all_rows:
        r["score"] = None
        r["serp"], r["serp_cached"] = None, False
    for r in passed:
        r["_pre"] = demand_score(r, cfg) + saturation_score(r, cfg) + fit_score(r, cfg, fit)
    cls = classify_stage(passed, args.no_classify)
    eligible = [r for r in passed if r.get("exclude_reason") is None]
    chosen = sorted(eligible, key=lambda r: r["_pre"], reverse=True)[:max_serp]
    log(f"필터 통과 {len(passed)}개 중 분류 제외 {len(passed) - len(eligible)}개 → 화면 수집 대상 {len(chosen)}개")

    # 3단계
    serp_errors = serp_stage(chosen, cfg, cache)

    # 4단계
    for r in chosen:
        d, s, sc, f = demand_score(r, cfg), saturation_score(r, cfg), screen_score(r["serp"], cfg), fit_score(r, cfg, fit)
        r["score"] = {"demand": round(d, 1), "saturation": round(s, 1), "screen": round(sc, 1),
                      "fit": round(f, 1), "total": round(d + s + sc + f, 1)}
    scored = sorted((r for r in chosen if r["score"] and r["serp"]), key=lambda r: r["score"]["total"], reverse=True)
    for r in all_rows:
        r.pop("_pre", None)
        r.pop("sat_pct", None)

    stamp = started.strftime("%Y%m%d_%H%M%S")
    result = {
        "collected_at": started.isoformat(timespec="seconds"),
        "trend_months": months,
        "filter_used": cfg,
        "api_status": {"blog_search": api.status("blog"), "datalab": api.status("trend")},
        "seeds": infos,
        "counts": {"candidates": len(all_rows), "passed_filter": len(passed),
                   "classify_excluded": len(passed) - len(eligible), "serp_targets": len(chosen), "scored": len(scored)},
        "classification": {
            "status": cls["status"], "model": kc.MODEL, "max_turns": int(kc.MAX_TURNS),
            **({"error": cls["error"]} if "error" in cls else {}),
            "type_counts": {kc.TYPE_KO[t]: sum(1 for r in passed if r.get("kw_type") == t) for t in kc.TYPES}
            if cls["status"] == "ok" else None,
            "usage": cls.get("usage"),
            "rescued_to_choose": cls.get("rescued", []),
            "intent_groups": cls.get("groups", []),
            "excluded": [{"keyword": r["keyword"], "type": kc.TYPE_KO[r["kw_type"]], "reason": r["exclude_reason"]}
                         for r in passed if r.get("exclude_reason")],
        },
        "serp_errors": serp_errors,
        "ranking": [compact(i, r) for i, r in enumerate(scored, 1)],
        "keywords": all_rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result_path = OUT_DIR / f"result_{stamp}.json"
    write_text_retry(result_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    top = {"generated_at": result["collected_at"], "result_file": result_path.name,
           "top10": [compact(i, r) for i, r in enumerate(scored[:10], 1)]}
    top_text = json.dumps(top, ensure_ascii=False, indent=2)
    write_text_retry(OUT_DIR / "top10.json", top_text + "\n")
    # 주간 기획(plan_posts.py)이 쓰는 전체 순위 (화면 수집·채점이 끝난 키워드 전부)
    write_text_retry(OUT_DIR / "ranking.json", json.dumps(
        {"generated_at": result["collected_at"], "result_file": result_path.name,
         "intent_groups": result["classification"]["intent_groups"], "ranking": result["ranking"]},
        ensure_ascii=False, indent=2) + "\n")
    print(top_text)
    log(f"[저장됨] {result_path}")
    log(f"[저장됨] {OUT_DIR / 'top10.json'}")
    log(f"[캐시 사용 합계] {cstat_text(cstat_snapshot())}")
    log(f"[완료] 후보 {len(all_rows)} → 필터 통과 {len(passed)} → 화면 수집 {len(chosen)} → 채점 {len(scored)}"
        + (f" (화면 수집 실패 {len(serp_errors)})" if serp_errors else ""))
    return 1 if serp_errors and not scored else 0


if __name__ == "__main__":
    sys.exit(main())
