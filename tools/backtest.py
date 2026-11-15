# -*- coding: utf-8 -*-
"""과거 글 시험(backtest): 이미 발행한 글의 실적과 키워드 신호를 한 표로 묶는다. (표와 집계만 만든다 — 뽑는 기준은 고치지 않는다)

단계 (같은 명령을 다시 돌리면 저장된 값으로 이어간다. 결과는 pipeline/backtest_run/ 아래에만 쓴다)
  python tools/backtest.py collect    글 목록·성과·목표 키워드 (API 없음)
  python tools/backtest.py api        검색량(검색광고) · 블로그 검색 · 데이터랩 (사용량 한도 있음)
  python tools/backtest.py classify   키워드 모양 분류 (Claude haiku 1회)
  python tools/backtest.py report     backtest_naver.csv · backtest_wp.csv · summary.md
"""
import argparse
import csv
import glob
import json
import re
import statistics
import sys
import urllib.parse as up
from datetime import date, datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BASE = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(BASE))

RUN = BASE / "pipeline" / "backtest_run"
TODAY = date.today()
BLOG_LIMIT, TREND_LIMIT = 600, 80          # 이번 시험의 API 사용 한도
TREND_START = date(2025, 3, 3)             # 데이터랩 주간 자료 시작(작년 같은 주부터 52주를 덮음)
WP_RANGE = (date(2026, 10, 14), date(2026, 11, 10))   # perf_cache 의 최근 4주(cur)

POSTS_DIRS = ["2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02"]

NS_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")


def nospace(t) -> str:
    return NS_RE.sub("", TAG_RE.sub("", str(t or ""))).lower()


def jload(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def jsave(path, obj):
    from safe_io import write_text_retry
    write_text_retry(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def pdate(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------- 1) 글 목록과 성과 ----------------

def load_ledgers():
    """장부: 글 키 → (파일, 글 내용) / 대기열 id → 항목 / post_index"""
    arts = {}
    for f in ["articles_naver_tips.json", "articles_shop_naver.json", "articles_radar_naver.json",
              "articles_shop_wp.json", "articles_wp_tips.json", "articles.json"]:
        for k, v in (jload(BASE / f, {}) or {}).items():
            arts.setdefault(k, (f, v))
    queue = {}
    for f in sorted(glob.glob(str(BASE / "content_queue*.json"))):
        if "backup" in f:
            continue
        for x in jload(f, []) or []:
            queue.setdefault(x.get("id"), (Path(f).name, x))
    pi = (jload(BASE / "pipeline" / "post_index.json", {}) or {}).get("posts", {})
    return arts, queue, pi


def title_like(kw: str, title: str) -> bool:
    return (not kw) or nospace(kw) == nospace(title) or len(kw) >= 20 or "," in kw or "?" in kw


def find_target(key, title, queue, pi):
    """목표 키워드: ① 대기열 키워드(제목처럼 긴 것 제외) ② post_index 의 target_raw. → (키워드, 출처)"""
    q = queue.get(key)
    if q and not title_like(q[1].get("keyword", ""), title):
        return q[1]["keyword"].strip(), "대기열 " + q[0]
    p = pi.get(title)
    if p and p.get("target_raw"):
        return p["target_raw"].strip(), "post_index"
    return None, "목표 키워드 없음"


def collect_naver(arts, queue, pi):
    posts = {}
    weeks = [pdate(d) for d in POSTS_DIRS]
    for wd, w in zip(POSTS_DIRS, weeks):
        with open(BASE / "perf_inbox" / wd / "posts.csv", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                key = r["앱 글 키"]
                p = posts.setdefault(key, {"key": key, "views_by_week": {}, "title": r["제목"]})
                p["title"] = r["제목"]
                p["channel"], p["type"], p["category"] = r["채널"], r["글 유형"], r["카테고리"]
                p["pub"] = r["작성일(네이버)"] or r["발행일(앱)"]
                p["views_by_week"][wd] = int(r["조회수"] or 0)
                p["link_mode"] = r["연결 방식"]
    out = []
    for key, p in posts.items():
        pub = pdate(p["pub"])
        pm = monday_of(pub)
        obs, w = {}, {}
        for wd, wk in zip(POSTS_DIRS, weeks):
            idx = (wk - pm).days // 7 + 1          # 발행 뒤 몇 주째 (1 = 발행한 그 주)
            if idx >= 1:
                obs[idx] = p["views_by_week"].get(wd, 0)
        w1, w2 = obs.get(1), obs.get(2)
        later = [v for i, v in obs.items() if i >= 3]
        total = sum(obs.values()) if obs else None
        best = max(obs.items(), key=lambda kv: (kv[1], -kv[0])) if obs else None
        flag = ""
        if w1 is None:
            flag = "1주째 관측 밖(수집 시작 전 발행)"
        elif w2 is None:
            flag = "2주 미만"
        art = arts.get(key)
        url = ""
        if art:
            url = ((art[1].get("platform_published_url") or {}).get("naver")) or ""
        kw, src = find_target(key, p["title"], queue, pi)
        out.append({
            "key": key, "title": p["title"], "채널": p["channel"], "글유형": p["type"], "카테고리": p["category"],
            "발행일": pub.isoformat(), "발행주": pm.isoformat(), "발행뒤_지난주수": (TODAY - pub).days // 7,
            "url": url, "목표키워드": kw, "목표키워드_출처": src,
            "w1": w1, "w2": w2, "w3이후": sum(later) if later else None, "관측합계": total,
            "관측주수": len(obs), "최고주_조회수": best[1] if best else None, "최고주_발행뒤주": best[0] if best else None,
            "perf2w": (w1 + w2) if (w1 is not None and w2 is not None) else None, "성과_표시": flag,
            "연결방식": p["link_mode"], "장부글": bool(art),
        })
    out.sort(key=lambda x: (x["발행일"], x["key"]))
    return out


def collect_wp(arts, queue, pi):
    perf = jload(BASE / "perf_cache.json", {})
    pages = perf["sites"]["ssansegae"]["pages"]
    by_url = {}
    for f in ["articles_shop_wp.json", "articles_wp_tips.json"]:
        for k, v in (jload(BASE / f, {}) or {}).items():
            u = (v.get("platform_published_url") or {}).get("wordpress")
            if u:
                by_url[up.unquote(u).rstrip("/")] = (f, k, v)
    out, skipped = [], []
    for pg, v in pages.items():
        u = up.unquote(v["url"]).rstrip("/")
        hit = by_url.get(u)
        if not hit:
            skipped.append(u)
            continue
        f, key, art = hit
        title = (art.get("platform_title") or {}).get("wordpress") or ""
        pub = pdate((art.get("platform_published_at") or {}).get("wordpress"))
        kw, src = find_target(key, title, queue, pi)
        cur = v.get("cur") or {}
        qs = sorted(v.get("queries") or [], key=lambda q: (-q.get("clicks", 0), -q.get("impressions", 0)))
        start = max(pub, WP_RANGE[0]) if pub else WP_RANGE[0]
        days = max(0, (WP_RANGE[1] - start).days + 1)
        out.append({
            "key": key, "title": title, "글유형": "싼세계 숍" if f == "articles_shop_wp.json" else "싼세계 팁",
            "발행일": pub.isoformat() if pub else "", "발행뒤_지난주수": (TODAY - pub).days // 7 if pub else None,
            "관측일수": days, "url": v["url"],
            "목표키워드": kw, "목표키워드_출처": src,
            "노출": cur.get("impressions", 0), "클릭": cur.get("clicks", 0), "평균순위": cur.get("position") or None,
            "queries": qs,
        })
    out.sort(key=lambda x: (x["발행일"], x["key"]))
    return out, skipped


def cmd_collect(_):
    RUN.mkdir(parents=True, exist_ok=True)
    arts, queue, pi = load_ledgers()
    nv = collect_naver(arts, queue, pi)
    wp, skipped = collect_wp(arts, queue, pi)
    jsave(RUN / "posts_naver.json", nv)
    jsave(RUN / "posts_wp.json", wp)
    jsave(RUN / "posts_wp_skipped.json", skipped)
    n_nv_no = sum(1 for p in nv if not p["목표키워드"])
    n_wp_no = sum(1 for p in wp if not p["목표키워드"])
    kws = {nospace(p["목표키워드"]) for p in nv + wp if p["목표키워드"]}
    full2 = sum(1 for p in nv if p["perf2w"] is not None)
    print(f"네이버 글 {len(nv)}편 (목표 키워드 없음 {n_nv_no}) / 싼세계 글 {len(wp)}편 (목표 키워드 없음 {n_wp_no}) / 서로 다른 목표 키워드 {len(kws)}개")
    print(f"네이버 2주 성과 계산 가능 {full2}편, 2주 미만 {sum(1 for p in nv if p['성과_표시']=='2주 미만')}편, 1주째 관측 밖 {sum(1 for p in nv if p['성과_표시'].startswith('1주째'))}편")
    print(f"싼세계 제외한 주소(글 아님): {len(skipped)}개")


# ---------------- 2) API: 검색량 · 블로그 검색 · 데이터랩 ----------------

class Budget(Exception):
    pass


class Api:
    """collect_metrics.OpenApi 를 감싸 호출 수를 세고(usage.json 에 누적) 한도를 넘기면 멈춘다."""

    def __init__(self):
        import collect_metrics as cm
        self.cm = cm
        self.api = cm.OpenApi()
        self.usage = jload(RUN / "usage.json", {}) or {}
        for k in ("blog", "trend", "searchad", "claude"):
            self.usage.setdefault(k, 0)

    def save(self):
        jsave(RUN / "usage.json", self.usage)

    def blog(self, **params):
        if self.usage["blog"] >= BLOG_LIMIT:
            raise Budget("블로그 검색 한도")
        self.usage["blog"] += 1
        return self.api.call("blog", params=params)

    def trend(self, body):
        if self.usage["trend"] >= TREND_LIMIT:
            raise Budget("데이터랩 한도")
        self.usage["trend"] += 1
        return self.api.call("trend", json=body)

    def searchad(self, hints):
        import time
        import requests
        import config
        import naver_api
        uri = "/keywordstool"
        params = {"hintKeywords": ",".join(h.replace(" ", "") for h in hints), "showDetail": "1"}
        r = None
        for attempt in range(2):
            self.usage["searchad"] += 1
            self.cm.throttle()
            r = requests.get(config.NAVER_API_BASE_URL + uri, params=params, headers=naver_api._headers("GET", uri), timeout=20)
            if r.status_code == 429 and attempt == 0:
                time.sleep(1.5)
                continue
            break
        if not r.ok:
            return None
        return r.json().get("keywordList", [])


def all_keywords():
    """서로 다른 목표 키워드 (띄어쓰기 없앤 값 → 처음 본 표기)"""
    out = {}
    for f in ("posts_naver.json", "posts_wp.json"):
        for p in jload(RUN / f, []) or []:
            if p["목표키워드"]:
                out.setdefault(nospace(p["목표키워드"]), p["목표키워드"])
    return out


def load_caches():
    gold = jload(BASE / "pipeline" / "golden_cache.json", {}) or {}
    cache = jload(BASE / "pipeline" / "cache.json", {}) or {}
    top = {nospace(t["keyword"]): t for t in (jload(BASE / "pipeline" / "top10.json", {}) or {}).get("top10", [])}
    gold = {nospace(k): v.get("v") for k, v in gold.items() if isinstance(v, dict)}
    serp = {nospace(k): v.get("v") for k, v in (cache.get("serp") or {}).items() if isinstance(v, dict)}
    return gold, serp, top


def vol_row(api, it, src):
    pc, mo = api.cm.to_int(it.get("monthlyPcQcCnt")), api.cm.to_int(it.get("monthlyMobileQcCnt"))
    return {"total": pc + mo, "pc": pc, "mobile": mo, "comp_idx": it.get("compIdx"),
            "ave_pc_clk": api.cm.to_float(it.get("monthlyAvePcClkCnt")),
            "ave_mobile_clk": api.cm.to_float(it.get("monthlyAveMobileClkCnt")), "src": src}


def stage_volume(api, raw, kws, top):
    todo = []
    for nk, kw in kws.items():
        if nk in raw["vol"]:
            continue
        t = top.get(nk)
        if t and t.get("monthly_total") is not None:
            raw["vol"][nk] = {"total": t["monthly_total"], "pc": None, "mobile": None, "comp_idx": t.get("comp_idx"),
                              "ave_pc_clk": t.get("ave_pc_clk"), "ave_mobile_clk": t.get("ave_mobile_clk"), "src": "top10.json"}
        else:
            todo.append(kw)
    for i in range(0, len(todo), 5):
        chunk = todo[i:i + 5]
        lst = api.searchad(chunk)
        singles = []
        if lst is None:                       # 묶음이 실패하면 하나씩
            singles = chunk
        else:
            idx = {nospace(it.get("relKeyword")): it for it in lst}
            for k in chunk:
                it = idx.get(nospace(k))
                raw["vol"][nospace(k)] = vol_row(api, it, "검색광고") if it else {"total": None, "src": "검색광고 결과에 없음"}
        for k in singles:
            lst = api.searchad([k])
            it = {nospace(x.get("relKeyword")): x for x in (lst or [])}.get(nospace(k))
            raw["vol"][nospace(k)] = vol_row(api, it, "검색광고(단건)") if it else {"total": None, "src": "검색광고 실패"}
        api.save()
        jsave(RUN / "raw_cache.json", raw)


LOGNO_RE = re.compile(r"(\d{9,})")


def stage_sim(api, raw, kws):
    """키워드마다 관련도순 상위 100개 1회: 상위 글 날짜(상위 10개)·제목 일치 글 수·내 글 순위에 모두 쓴다."""
    import html
    for nk, kw in kws.items():
        if nk in raw["sim"]:
            continue
        j = api.blog(query=kw, display=100, start=1, sort="sim")
        if not j or "items" not in j:
            continue
        items = []
        for it in j["items"]:
            m = LOGNO_RE.findall(it.get("link", ""))
            items.append({"log": m[-1] if m else "", "title": html.unescape(TAG_RE.sub("", it.get("title", ""))),
                          "date": str(it.get("postdate", ""))})
        raw["sim"][nk] = {"total": j.get("total"), "items": items}
        if len(raw["sim"]) % 10 == 0:
            api.save()
            jsave(RUN / "raw_cache.json", raw)
    api.save()
    jsave(RUN / "raw_cache.json", raw)


def stage_trend(api, raw, kws):
    todo = [nk for nk in kws if nk not in raw["trend"]]
    todo.sort(key=lambda nk: -((raw["vol"].get(nk) or {}).get("total") or 0))   # 비슷한 검색량끼리 묶어 상대값 왜곡을 줄임
    end = (TODAY - timedelta(days=1)).isoformat()
    for i in range(0, len(todo), 5):
        chunk = todo[i:i + 5]
        body = {"startDate": TREND_START.isoformat(), "endDate": end, "timeUnit": "week",
                "keywordGroups": [{"groupName": f"g{n}", "keywords": [kws[nk]]} for n, nk in enumerate(chunk)]}
        j = api.trend(body)
        if not j or not j.get("results"):
            continue
        by = {r.get("title"): r.get("data", []) for r in j["results"]}
        for n, nk in enumerate(chunk):
            raw["trend"][nk] = {d["period"][:10]: float(d.get("ratio") or 0) for d in by.get(f"g{n}", [])}
        api.save()
        jsave(RUN / "raw_cache.json", raw)


def stage_scan(api, raw, kws, gold):
    """제목 일치 글 수(최근 30일): golden_metrics.title_compete 와 같은 방식(최신순 최대 5페이지).
    이미 golden_cache 에 제목 기준 값이 있으면 그걸 쓴다(다시 안 부름)."""
    import html
    import golden_metrics as gm
    gc = gm.golden_config(jload(BASE / "tools" / "filter.json", {}))
    cutoff = (TODAY - timedelta(days=30)).strftime("%Y%m%d")
    order = sorted((nk for nk in kws if nk in raw["sim"] and nk not in raw["scan"]),
                   key=lambda nk: raw["sim"][nk].get("total") or 0)       # 호출이 적게 드는 것부터
    for nk in order:
        kw = kws[nk]
        gpub = ((gold.get(nk) or {}).get("publish")) or {}
        if gpub.get("mode") == "title":
            raw["scan"][nk] = {**gpub, "src": "golden_cache.json"}
            continue
        items = raw["sim"][nk]["items"]
        top100 = sum(1 for it in items if nk in nospace(it["title"]))
        base = {"mode": "title", "top100_title": top100, "top100_n": len(items), "total": raw["sim"][nk].get("total"), "src": "이번 측정"}
        if top100 >= gc["drop_top100_title"]:
            raw["scan"][nk] = {**base, "posts_30d": None, "capped": False, "title_30d": None, "rate": None, "dropped_early": True}
            continue
        inside = hit = 0
        oldest, reached, ok, calls = None, False, True, 0
        for idx in range(int(gc["title_scan_pages"])):
            jj = api.blog(query=kw, display=100, start=1 + idx * 100, sort="date")
            calls += 1
            if not jj or "items" not in jj:
                ok = False
                break
            its = jj["items"]
            for it in its:
                pd = str(it.get("postdate", ""))
                if pd >= cutoff:
                    inside += 1
                    hit += nk in nospace(html.unescape(it.get("title", "")))
                    oldest = pd
                else:
                    reached = True
            if reached or len(its) < 100:
                reached = True
                break
        if not ok and inside == 0:
            continue
        estimated, t30 = False, float(hit)
        if not reached and oldest:
            span = max(1, (TODAY - datetime.strptime(oldest, "%Y%m%d").date()).days)
            t30, estimated = round(hit * 30 / span, 1), True
        vol = (raw["vol"].get(nk) or {}).get("total")
        rate = round(vol / max(1.0, t30), 2) if vol is not None else None
        raw["scan"][nk] = {**base, "posts_30d": inside, "capped": not reached, "title_30d": t30, "title_30d_estimated": estimated,
                           "rate": rate, "calls": calls, **({"partial": True} if not ok else {})}
        if len(raw["scan"]) % 10 == 0:
            api.save()
            jsave(RUN / "raw_cache.json", raw)


def cmd_api(_):
    kws = all_keywords()
    if not kws:
        sys.exit("먼저 collect 를 실행하세요.")
    raw = jload(RUN / "raw_cache.json", None) or {}
    for k in ("vol", "sim", "trend", "scan"):
        raw.setdefault(k, {})
    gold, serp, top = load_caches()
    api = Api()
    stopped = ""
    try:
        stage_volume(api, raw, kws, top)
        stage_sim(api, raw, kws)
        stage_trend(api, raw, kws)
        stage_scan(api, raw, kws, gold)
    except Budget as e:
        stopped = str(e)
    finally:
        api.save()
        jsave(RUN / "raw_cache.json", raw)
    print(f"키워드 {len(kws)}개 — 검색량 {len(raw['vol'])}, 블로그 상위100 {len(raw['sim'])}, 데이터랩 {len(raw['trend'])}, 제목 경쟁 {len(raw['scan'])}")
    print("API 사용량(누적):", api.usage, "| 블로그:", api.api.status("blog"), "| 데이터랩:", api.api.status("trend"))
    if stopped:
        print("한도로 멈춤:", stopped, "— 저장했습니다.")


# ---------------- 3) 키워드 모양 분류 (Claude haiku 1회) ----------------

SHAPES = ["제철 물건", "상시 물건", "급한 증상", "방법·순서", "고르기·비교", "브랜드·모델", "기타"]

CLASSIFY_PROMPT = """아래는 생활 블로그 글 목록이다. 글마다 (목표 키워드, 제목)이 있다. 도구는 쓰지 말고 바로 답해라.
글마다 두 가지를 정해라.
1) shape — 목표 키워드의 모양. 다음 중 정확히 하나:
 - 제철 물건: 계절·시기에 따라 찾는 물건(제습기, 전기장판, 패딩, 가습기 같은 것)
 - 상시 물건: 계절과 상관없이 사는 물건
 - 급한 증상: 지금 막 생긴 문제(막힘, 곰팡이, 냄새, 누수, 고장, 얼룩 같은 것)
 - 방법·순서: 하는 법·절차·점검 순서
 - 고르기·비교: 추천·비교·고르는 기준
 - 브랜드·모델: 특정 브랜드나 모델명이 들어간 것
 - 기타
2) mistake — 제목이 '~하면 ~해요', '~하면 더 ~해요', '~하면 위험해요', '이것만 피하면 돼요'처럼 실수·주의를 짚는 형태이면 true, 아니면 false.
반드시 아래 JSON 한 덩어리로만 답해라(설명 금지).
{{"items": [{{"id": "N1", "shape": "방법·순서", "mistake": false}}]}}

[글 목록]
{lines}
"""


def cmd_classify(_):
    out_path = RUN / "classify.json"
    done = jload(out_path, {}) or {}
    nv = jload(RUN / "posts_naver.json", []) or []
    wp = jload(RUN / "posts_wp.json", []) or []
    units = [("N" + p["key"], p) for p in nv] + [("W" + p["key"], p) for p in wp]
    if all(uid in done for uid, _p in units):
        print("이미 분류가 끝나 있습니다(Claude 호출 없음).")
        return
    ids = {f"X{i + 1}": uid for i, (uid, _p) in enumerate(units)}
    lines = "\n".join(f"{xid} | {p['목표키워드']} | {p['title']}" for xid, (uid, p) in zip(ids, units))
    import plan_posts as pp
    api = Api()
    api.usage["claude"] += 1
    api.save()
    env = pp.call_claude(CLASSIFY_PROMPT.format(lines=lines), "haiku")
    (RUN / "classify_raw.txt").write_text(str(env.get("result", "")), encoding="utf-8")
    items = pp.extract_json(env.get("result", "")).get("items", [])
    got = 0
    for it in items:
        uid = ids.get(it.get("id"))
        if uid and it.get("shape") in SHAPES:
            done[uid] = {"shape": it["shape"], "mistake": bool(it.get("mistake"))}
            got += 1
    jsave(out_path, done)
    api.usage["claude_cost_usd"] = env.get("total_cost_usd")
    api.save()
    print(f"분류 {got}/{len(units)}편 저장 (Claude 1회, 비용 ${env.get('total_cost_usd')})")


# ---------------- 4) 신호 계산 · 표 · 집계 ----------------

def mean_of(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def lead_from_series(ser, today, cap=5):
    """golden_metrics.lead_stats 와 같은 식을 이미 받아 둔 주간 자료로 계산(호출 없음)."""
    pts = [(date.fromisoformat(k), v) for k, v in ser.items()]
    ly = today - timedelta(days=364)

    def avg(a, b):
        v = [r for p, r in pts if a <= p < b]
        return sum(v) / len(v) if v else None

    base_ly = avg(ly - timedelta(days=28), ly)
    next8 = avg(ly, ly + timedelta(days=56))
    recent = avg(today - timedelta(days=28), today)
    div = lambda a, b: round(a / b, 2) if a is not None and b else None
    cp = lambda v: None if v is None else min(v, cap)
    return {"seasonal_lift_8w": cp(div(next8, base_ly)), "yoy_recent_4w": cp(div(recent, base_ly)),
            "ly_prev4w_avg": None if base_ly is None else round(base_ly, 1)}


def season_signals(ser, pub: date):
    """발행 주의 검색 추세(직전 4주 대비)와 작년 같은 시기 정점 주까지 남은 주 수(작년 같은 주부터 52주 안)"""
    if not ser:
        return {"추세비율": None, "추세구분": "판단불가", "정점까지_주": None}
    P = monday_of(pub)
    get = lambda d: ser.get(d.isoformat())
    cur = get(P)
    base_v = [get(P - timedelta(days=7 * k)) for k in (1, 2, 3, 4)]
    base_v = [x for x in base_v if x is not None]
    ratio, cls = None, "판단불가"
    if cur is not None and base_v:
        base = sum(base_v) / len(base_v)
        if base > 0:
            ratio = round(cur / base, 2)
            cls = "상승" if ratio >= 1.15 else ("하락" if ratio <= 0.85 else "유지")
        elif cur > 0:
            cls = "상승"
    ly = P - timedelta(days=364)
    best = None
    for k in range(0, 52):          # 작년 같은 주부터 1년(52주) 안에서 가장 높은 주까지 몇 주 남았나
        v = get(ly + timedelta(days=7 * k))
        if v is None:
            continue
        key = (v, -abs(k))
        if best is None or key > best[0]:
            best = (key, k)
    peak = best[1] if best and best[0][0] > 0 else None
    return {"추세비율": ratio, "추세구분": cls, "정점까지_주": peak}


def screen_values(nk, gold, serp, sim, gc):
    """등급 계산에 쓸 화면 값: golden_cache → cache.json(serp) → (없으면) 블로그 검색 상위 5개로 대신, 인플루언서는 0 으로 가정"""
    g0 = gold.get(nk) or {}
    summ = (g0.get("top_posts") or {}).get("summary")
    sp = g0.get("serp_position")
    if summ and sp:
        return summ, sp.get(gc["position_field"]), "golden_cache"
    bf = (serp.get(nk) or {}).get("blog_feed")
    if bf and bf.get("cards"):
        cards = bf["cards"][:5]
        summ = {"title_kw_nospace": sum(1 for c in cards if nk in nospace(c.get("title"))),
                "title_kw_exact": sum(1 for c in cards if nk in nospace(c.get("title"))),
                "influencer": sum(1 for c in cards if c.get("influencer"))}
        field = "order" if gc["position_field"] == "blog_area_order" else "titled_order"
        return summ, bf.get(field), "cache.json(serp)"
    items = (sim or {}).get("items", [])[:5]
    summ = {"title_kw_nospace": sum(1 for it in items if nk in nospace(it["title"])),
            "title_kw_exact": sum(1 for it in items if nk in nospace(it["title"])), "influencer": 0}
    return summ, None, "화면값 없음(블로그 검색 상위5로 대신·인플루언서 0 가정·순위 없음)"


def keyword_signals(nk, kw, raw, gold, serp, gc, gm):
    vol = raw["vol"].get(nk) or {}
    scan = raw["scan"].get(nk) or {}
    sim = raw["sim"].get(nk) or {}
    ser = raw["trend"].get(nk) or {}
    out = {"vol_pc": vol.get("pc"), "vol_mobile": vol.get("mobile"), "vol": vol.get("total"),
           "top100_title": scan.get("top100_title"), "title_30d": scan.get("title_30d"), "rate": scan.get("rate"),
           "title_30d_est": scan.get("title_30d_estimated")}
    items = sim.get("items", [])[:10]
    ages = []
    for it in items:
        d = pdate(f"{it['date'][:4]}-{it['date'][4:6]}-{it['date'][6:8]}") if len(it.get("date", "")) == 8 else None
        if d:
            ages.append((TODAY - d).days)
    out.update({"top_age_med": statistics.median(ages) if ages else None,
                "top_old_n": sum(1 for a in ages if a > 365) if ages else None,
                "top_new_n": sum(1 for a in ages if a <= 30) if ages else None, "top_n": len(ages)})
    grade, basis, exp, reason = None, "", None, ""
    if vol.get("total") is not None and scan:
        summ, order, basis = screen_values(nk, gold, serp, sim, gc)
        lead = lead_from_series(ser, TODAY) if ser else {}
        tg = None
        try:
            tg = gm.trend_growth_of((jload_cache_metrics().get(nk) or {}).get("trend_12m"))
        except Exception:
            tg = None
        g = {"publish": scan, "top_posts": {"summary": summ}, "lead": lead, "serp_position": {gc["position_field"]: order}}
        r = gm.grade_keyword(g, vol["total"], gc, tg)
        grade, exp, reason = r.get("grade"), r.get("expected_traffic"), r.get("drop_reason") or ""
    out.update({"grade": grade or "없음", "grade_basis": basis, "expected_traffic": exp, "drop_reason": reason})
    return out


_METRICS = {}


def jload_cache_metrics():
    if not _METRICS:
        m = (jload(BASE / "pipeline" / "cache.json", {}) or {}).get("metrics") or {}
        _METRICS.update({nospace(k): v for k, v in m.items() if isinstance(v, dict)})
    return _METRICS


def my_rank(nk, raw, post):
    items = (raw["sim"].get(nk) or {}).get("items")
    if items is None:
        return None
    m = LOGNO_RE.findall(post.get("url") or "")
    log_no = m[-1] if m else None
    t = nospace(post["title"])
    for i, it in enumerate(items, 1):
        if (log_no and it["log"] == log_no) or nospace(it["title"]) == t:
            return i
    return 0          # 0 = 100위 밖


def naver_search_terms():
    rows = []
    for d in POSTS_DIRS:
        try:
            with open(BASE / "perf_inbox" / d / "search_terms.csv", encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get("구분") == "검색어":
                        rows.append((r["검색어"], int(float(r.get("추정 횟수") or 0))))
        except OSError:
            pass
    return rows


def spearman(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 5:
        return None, n

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return "같은 값", n          # 한쪽 값이 모두 같아 순위 상관을 못 구함
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sxx * syy) ** 0.5, n


def bin_of(v, edges, labels):
    """edges: 오름차순 경계(각 구간은 edge 미만). labels 길이 = len(edges)+1"""
    if v is None:
        return "측정 안 함"
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


VOL_LABELS = ["150 미만", "150~500", "500~1,500", "1,500~5,000", "5,000 이상"]
TITLE100_LABELS = ["0~4개", "5~14개", "15~29개", "30~49개", "50개 이상(탈락선)"]
TITLE30_LABELS = ["0~5개", "6~20개", "21~50개", "51개 이상"]
AGE_LABELS = ["30일 이내", "31~180일", "181~365일", "1년 초과"]
PEAK_LABELS = ["0~2주 남음(정점 근처)", "3~8주 남음", "9~20주 남음", "21~35주 남음", "36주 이상(정점이 지난 지 얼마 안 됨)"]
RANK_LABELS = ["1~3위", "4~10위", "11~30위", "31~100위", "100위 밖"]


def peak_bin(v):
    if v is None:
        return "측정 못함"
    return PEAK_LABELS[0] if v <= 2 else PEAK_LABELS[1] if v <= 8 else PEAK_LABELS[2] if v <= 20 else PEAK_LABELS[3] if v <= 35 else PEAK_LABELS[4]


def rank_bin(r):
    if r is None:
        return "측정 못함"
    if r == 0:
        return RANK_LABELS[4]
    return RANK_LABELS[0] if r <= 3 else RANK_LABELS[1] if r <= 10 else RANK_LABELS[2] if r <= 30 else RANK_LABELS[3]


def f1(x):
    return "-" if x is None else (f"{x:,.1f}" if isinstance(x, float) and x != int(x) else f"{int(x):,}")


def md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def group_table(title, rows, keyfn, order, perfs):
    """perfs: [(라벨, 값 함수)]. 편수 5 미만인 칸은 판단 보류."""
    groups = {}
    for r in rows:
        groups.setdefault(keyfn(r), []).append(r)
    cats = [c for c in order if c in groups] + sorted(c for c in groups if c not in order)
    headers = [title, "편수"]
    for lab, _fn in perfs:
        headers += [lab + " 가운데값", lab + " 평균"]
    body = []
    for c in cats:
        g = groups[c]
        row = [c, len(g)]
        for _lab, fn in perfs:
            vals = [fn(r) for r in g if fn(r) is not None]
            if len(vals) < 5:
                row += [f"판단 보류(n={len(vals)})", "-"]
            else:
                row += [f1(round(statistics.median(vals), 1)), f1(round(sum(vals) / len(vals), 1))]
        body.append(row)
    return md_table(headers, body)


def write_csv(path, rows, cols):
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([("" if r.get(c) is None else r.get(c)) for c in cols])
    from safe_io import write_text_retry
    write_text_retry(path, "﻿" + buf.getvalue())


def build_rows():
    import golden_metrics as gm
    gc = gm.golden_config(jload(BASE / "tools" / "filter.json", {}))
    raw = jload(RUN / "raw_cache.json", {})
    gold, serp, _top = load_caches()
    cls = jload(RUN / "classify.json", {}) or {}
    nv = jload(RUN / "posts_naver.json", []) or []
    wp = jload(RUN / "posts_wp.json", []) or []
    terms = naver_search_terms()
    sig = {}
    for p in nv + wp:
        nk = nospace(p["목표키워드"])
        if nk and nk not in sig:
            sig[nk] = keyword_signals(nk, p["목표키워드"], raw, gold, serp, gc, gm)
    nrows, wrows = [], []
    for p in nv:
        nk = nospace(p["목표키워드"])
        s = sig.get(nk, {})
        pub = pdate(p["발행일"])
        se = season_signals(raw["trend"].get(nk) or {}, pub)
        hit = [(q, c) for q, c in terms if nk and nk in nospace(q)]
        c = cls.get("N" + p["key"], {})
        rk = my_rank(nk, raw, p)
        nrows.append({
            **{k: p[k] for k in ("key", "title", "채널", "글유형", "카테고리", "발행일", "발행뒤_지난주수", "목표키워드", "목표키워드_출처")},
            "1주째": p["w1"], "2주째": p["w2"], "3주째이후": p["w3이후"], "관측합계": p["관측합계"], "관측주수": p["관측주수"],
            "최고주_조회수": p["최고주_조회수"], "최고주_발행뒤주": p["최고주_발행뒤주"],
            "2주성과": p["perf2w"], "성과표시": p["성과_표시"] or "2주 성과 계산됨",
            "검색량_PC": s.get("vol_pc"), "검색량_모바일": s.get("vol_mobile"), "검색량_합계": s.get("vol"),
            "상위100중_제목일치": s.get("top100_title"), "30일_제목일치": s.get("title_30d"), "경쟁률": s.get("rate"),
            "황금등급": s.get("grade"), "등급_화면값출처": s.get("grade_basis"), "예상클릭": s.get("expected_traffic"),
            "탈락사유": s.get("drop_reason"),
            "상위글_나이_가운데값(일)": s.get("top_age_med"), "상위글_1년초과(편)": s.get("top_old_n"),
            "상위글_30일이내(편)": s.get("top_new_n"), "상위글_날짜있는편수": s.get("top_n"),
            "내글순위(블로그검색 관련도순)": ("밖" if rk == 0 else rk) if rk is not None else "측정 못함", "_rank": rk,
            "발행주_검색추세비율": se["추세비율"], "추세구분": se["추세구분"], "작년정점까지_남은주": se["정점까지_주"],
            "유입검색어_블로그전체_목표포함_추정횟수": sum(cc for _q, cc in hit),
            "유입검색어_블로그전체_목표포함_상위검색어": max(hit, key=lambda x: x[1])[0] if hit else "",
            "키워드모양": c.get("shape"), "실수주의형": ("예" if c.get("mistake") else "아니오") if c else None,
        })
    for p in wp:
        nk = nospace(p["목표키워드"])
        s = sig.get(nk, {})
        pub = pdate(p["발행일"]) if p["발행일"] else None
        se = season_signals(raw["trend"].get(nk) or {}, pub) if pub else {"추세비율": None, "추세구분": "판단불가", "정점까지_주": None}
        c = cls.get("W" + p["key"], {})
        qs = p["queries"]
        top_q = qs[0] if qs else None
        exact = [q for q in qs if nospace(q["query"]) == nk]
        if not qs:
            cmpw = "검색어 자료 없음"
        elif nospace(top_q["query"]) == nk:
            cmpw = "같음"
        elif nk in nospace(top_q["query"]) or nospace(top_q["query"]) in nk:
            cmpw = "포함"
        else:
            cmpw = "다름"
        tot_imp = sum(q["impressions"] for q in qs)
        wrows.append({
            **{k: p[k] for k in ("key", "title", "글유형", "발행일", "발행뒤_지난주수", "관측일수", "목표키워드", "목표키워드_출처")},
            "4주_노출": p["노출"], "4주_클릭": p["클릭"], "평균순위": p["평균순위"],
            "검색량_PC": s.get("vol_pc"), "검색량_모바일": s.get("vol_mobile"), "검색량_합계": s.get("vol"),
            "상위100중_제목일치": s.get("top100_title"), "30일_제목일치": s.get("title_30d"), "경쟁률": s.get("rate"),
            "황금등급": s.get("grade"), "등급_화면값출처": s.get("grade_basis"), "예상클릭": s.get("expected_traffic"),
            "탈락사유": s.get("drop_reason"),
            "상위글_나이_가운데값(일)": s.get("top_age_med"), "상위글_1년초과(편)": s.get("top_old_n"),
            "상위글_30일이내(편)": s.get("top_new_n"), "상위글_날짜있는편수": s.get("top_n"),
            "발행주_검색추세비율": se["추세비율"], "추세구분": se["추세구분"], "작년정점까지_남은주": se["정점까지_주"],
            "유입검색어_상위": top_q["query"] if top_q else "", "유입검색어_상위_노출": top_q["impressions"] if top_q else None,
            "유입검색어_상위_클릭": top_q["clicks"] if top_q else None, "유입검색어_목표와비교": cmpw,
            "유입검색어_목표와같은_노출": sum(q["impressions"] for q in exact) if qs else None,
            "유입검색어_목표와같은_노출비중(%)": round(100 * sum(q["impressions"] for q in exact) / tot_imp, 1) if tot_imp else None,
            "키워드모양": c.get("shape"), "실수주의형": ("예" if c.get("mistake") else "아니오") if c else None,
        })
    return nrows, wrows, sig


NAVER_COLS = ["key", "title", "채널", "글유형", "카테고리", "발행일", "발행뒤_지난주수", "목표키워드", "목표키워드_출처",
              "1주째", "2주째", "3주째이후", "관측합계", "관측주수", "최고주_조회수", "최고주_발행뒤주", "2주성과", "성과표시",
              "검색량_PC", "검색량_모바일", "검색량_합계", "상위100중_제목일치", "30일_제목일치", "경쟁률", "황금등급", "등급_화면값출처",
              "예상클릭", "탈락사유", "상위글_나이_가운데값(일)", "상위글_1년초과(편)", "상위글_30일이내(편)", "상위글_날짜있는편수",
              "내글순위(블로그검색 관련도순)", "발행주_검색추세비율", "추세구분", "작년정점까지_남은주",
              "유입검색어_블로그전체_목표포함_추정횟수", "유입검색어_블로그전체_목표포함_상위검색어", "키워드모양", "실수주의형"]
WP_COLS = ["key", "title", "글유형", "발행일", "발행뒤_지난주수", "관측일수", "목표키워드", "목표키워드_출처", "4주_노출", "4주_클릭", "평균순위",
           "검색량_PC", "검색량_모바일", "검색량_합계", "상위100중_제목일치", "30일_제목일치", "경쟁률", "황금등급", "등급_화면값출처",
           "예상클릭", "탈락사유", "상위글_나이_가운데값(일)", "상위글_1년초과(편)", "상위글_30일이내(편)", "상위글_날짜있는편수",
           "발행주_검색추세비율", "추세구분", "작년정점까지_남은주", "유입검색어_상위", "유입검색어_상위_노출", "유입검색어_상위_클릭",
           "유입검색어_목표와비교", "유입검색어_목표와같은_노출", "유입검색어_목표와같은_노출비중(%)", "키워드모양", "실수주의형"]


def tables_for(rows, perfs, extra_rank=False, type_key="글유형"):
    out = []
    def add(title, keyfn, order, label):
        out.append(f"**{label}**\n\n" + group_table(title, rows, keyfn, order, perfs))
    add("검색량 구간", lambda r: bin_of(r["검색량_합계"], [150, 500, 1500, 5000], VOL_LABELS), VOL_LABELS, "a) 검색량 구간별")
    add("상위 100개 중 제목 일치", lambda r: bin_of(r["상위100중_제목일치"], [5, 15, 30, 50], TITLE100_LABELS), TITLE100_LABELS, "b-1) 제목 일치 글 수(상위 100개 중)별")
    add("30일 제목 일치", lambda r: bin_of(r["30일_제목일치"], [6, 21, 51], TITLE30_LABELS), TITLE30_LABELS, "b-2) 제목 일치 글 수(최근 30일)별")
    add("상위 글 나이(가운데값)", lambda r: bin_of(r["상위글_나이_가운데값(일)"], [31, 181, 366], AGE_LABELS), AGE_LABELS, "b-3) 블로그 검색 상위 10개 글 나이별")
    add("황금 등급", lambda r: r["황금등급"] or "없음", ["S", "A", "B", "탈락", "없음"], "b-4) 황금 등급별")
    add("키워드 모양", lambda r: r["키워드모양"] or "분류 없음", SHAPES, "c-1) 키워드 모양별")
    add("실수·주의형 제목", lambda r: r["실수주의형"] or "분류 없음", ["예", "아니오"], "c-2) 제목 실수·주의형 여부별")
    add("글 유형", lambda r: r[type_key] or "미기재", [], "c-3) 글 유형별")
    add("발행주 검색 추세", lambda r: r["추세구분"], ["상승", "유지", "하락", "판단불가"], "c-4) 계절 추세별(발행 주 vs 직전 4주, ±15%)")
    add("작년 정점까지", lambda r: peak_bin(r["작년정점까지_남은주"]), PEAK_LABELS, "c-5) 발행 시점(작년 같은 시기 정점 주까지 남은 주)별")
    if extra_rank:
        add("내 글 순위", lambda r: rank_bin(r["_rank"]), RANK_LABELS, "d) 내 글 순위(블로그 검색 관련도순 100위 안)별")
    return out


def top_bottom(rows, perf_fn, perf_label, n=15, extra_cols=(), show_fn=None):
    ranked = [r for r in rows if perf_fn(r) is not None]
    ranked.sort(key=lambda r: (-perf_fn(r), r["발행일"], r["key"]))
    def mk(sub):
        cols = ["제목", "목표 키워드", perf_label, "검색량", "제목일치(상위100)", "제목일치(30일)", "상위글 나이(일)", "등급", "추세", "정점까지(주)", "모양"] + [c[0] for c in extra_cols]
        body = []
        for r in sub:
            body.append([r["title"][:28], r["목표키워드"], (show_fn(r) if show_fn else f1(perf_fn(r))), f1(r["검색량_합계"]), f1(r["상위100중_제목일치"]), f1(r["30일_제목일치"]),
                         f1(r["상위글_나이_가운데값(일)"]), r["황금등급"], r["추세구분"], f1(r["작년정점까지_남은주"]), r["키워드모양"] or "-"]
                        + [c[1](r) for c in extra_cols])
        return md_table(cols, body)
    return mk(ranked[:n]), mk(ranked[-n:][::-1] if len(ranked) >= n else ranked), len(ranked)


def corr_lines(rows, signals, perfs):
    lines = []
    for slabel, sfn in signals:
        parts = []
        for plabel, pfn in perfs:
            rho, n = spearman([sfn(r) for r in rows], [pfn(r) for r in rows])
            if rho == "같은 값":
                parts.append(f"{plabel}: 계산 불가(한쪽 값이 모두 같음, n={n})")
            else:
                parts.append(f"{plabel}: " + (f"ρ={rho:+.2f} (n={n})" if rho is not None else f"판단 보류 (n={n})"))
        lines.append(f"- {slabel} — " + " / ".join(parts))
    return "\n".join(lines)


def cmd_report(_):
    nrows, wrows, sig = build_rows()
    write_csv(RUN / "backtest_naver.csv", nrows, NAVER_COLS)
    write_csv(RUN / "backtest_wp.csv", wrows, WP_COLS)
    usage = jload(RUN / "usage.json", {}) or {}
    raw = jload(RUN / "raw_cache.json", {})
    nv_full = [r for r in nrows if r["2주성과"] is not None]
    nv_short = [r for r in nrows if r["성과표시"] == "2주 미만"]
    nv_out = [r for r in nrows if r["성과표시"].startswith("1주째")]
    L = []
    L.append(f"# 과거 글 시험 결과 ({TODAY.isoformat()} 만듦)\n")
    L.append("## 읽기 전에 (자료 설명)\n")
    L.append(f"- 네이버 글 {len(nrows)}편(발행일 {min(r['발행일'] for r in nrows)} ~ {max(r['발행일'] for r in nrows)}), 싼세계 글 {len(wrows)}편. 목표 키워드를 못 찾은 글: 네이버 {sum(1 for r in nrows if not r['목표키워드'])}편, 싼세계 {sum(1 for r in wrows if not r['목표키워드'])}편. 서로 다른 목표 키워드 {len(sig)}개.")
    L.append(f"- 네이버 성과 = 발행 뒤 2주간 조회수(1주째 + 2주째). 계산된 글 {len(nv_full)}편 / 2주가 아직 안 된 글 {len(nv_short)}편(따로 표시, 표에서 뺌) / 1주째가 수집 시작(10-12) 전이라 2주 합을 못 만드는 글 {len(nv_out)}편(표에서 뺌). 세 경우 모두 CSV 에는 있습니다(성과표시 칸).")
    L.append("- 싼세계 성과 = 최근 4주(10-14~11-10) 노출과 클릭. 이 기간 중에 발행한 글은 관측일수가 짧습니다(CSV 관측일수 칸).")
    L.append("- 키워드 신호(검색량·제목 일치·상위 글 나이·등급·내 글 순위)는 **오늘(" + TODAY.isoformat() + ") 시점의 값**입니다. 발행 당시 값이 아닙니다.")
    L.append("- 내 글 순위·상위 글 나이는 네이버 블로그 검색 API '관련도순' 결과(상위 100개 / 상위 10개) 기준이며, 화면(통합검색) 순위와 다를 수 있습니다.")
    L.append("- 황금 등급·예상 클릭은 `golden_metrics.grade_keyword` 를 그대로 썼습니다. 화면 값(인플루언서 수·블로그 영역 순서)은 golden_cache/cache.json 에 있으면 그것을, 없으면 블로그 검색 상위 5개로 대신하고 인플루언서 0 으로 가정했으며 순서가 없으면 예상 클릭은 비웁니다. 근거 칸: 등급_화면값출처.")
    basis_n = {}
    for r in nrows + wrows:
        basis_n[r["등급_화면값출처"] or "등급 없음"] = basis_n.get(r["등급_화면값출처"] or "등급 없음", 0) + 1
    L.append("  - 글 수 기준 등급 근거: " + ", ".join(f"{k} {v}편" for k, v in basis_n.items()))
    L.append("- 계절 추세 = 데이터랩 주간 값에서 (발행한 주) ÷ (직전 4주 평균), 1.15 이상 상승 / 0.85 이하 하락 / 그 사이 유지. 정점 = 작년 같은 주부터 52주 안에서 가장 높은 주(0 이면 작년 같은 시기가 정점, 숫자가 클수록 정점이 한참 뒤). 데이터랩은 5개 키워드 묶음마다 상대값이라 키워드 사이 크기는 비교하지 않습니다.")
    L.append("- 네이버 '실제 유입 검색어'는 글별 자료가 없어(search_terms.csv 는 블로그 전체) 블로그 전체 검색어 중 목표 키워드가 들어간 것의 추정 횟수로 대신했습니다. 싼세계는 글별 검색어(perf_cache queries)입니다.")
    L.append("- '30일 제목 일치'가 '측정 안 함'인 글은 상위 100개 중 제목 일치가 50개 이상이라(시스템의 탈락선) 더 세지 않은 것입니다. '예상 클릭'은 화면 값(블로그 영역 순서)이 있는 키워드만 계산돼 대부분 비어 있습니다.")
    L.append("- 편수 5 미만인 칸은 '판단 보류'. 순위 상관은 스피어만, 상관 글은 표본 수와 함께.")
    L.append("")

    # ----- 네이버 -----
    L.append("## 네이버 (성과 = 발행 뒤 2주간 조회수)\n")
    perfs_n = [("2주 조회수", lambda r: r["2주성과"])]
    base_n = nv_full
    for t in tables_for(base_n, perfs_n, extra_rank=True):
        L.append(t + "\n")
    L.append("**e) 성과 상위 15편 / 하위 15편 (2주 조회수 기준, 동점은 발행일 순)**\n")
    extra = [("내 글 순위", lambda r: str(r["내글순위(블로그검색 관련도순)"]))]
    top, bot, nn = top_bottom(base_n, lambda r: r["2주성과"], "2주 조회수", extra_cols=extra)
    L.append(f"상위 15편 (대상 {nn}편)\n\n{top}\n")
    L.append(f"하위 15편\n\n{bot}\n")
    sigs_n = [("검색량 합계", lambda r: r["검색량_합계"]), ("상위100중 제목일치", lambda r: r["상위100중_제목일치"]),
              ("30일 제목일치", lambda r: r["30일_제목일치"]), ("경쟁률(검색량÷30일 제목일치)", lambda r: r["경쟁률"]),
              ("상위글 나이 가운데값", lambda r: r["상위글_나이_가운데값(일)"]), ("상위글 1년 초과 편수", lambda r: r["상위글_1년초과(편)"]),
              ("상위글 30일 이내 편수", lambda r: r["상위글_30일이내(편)"]),
              ("내 글 순위(밖=101)", lambda r: None if r["_rank"] is None else (101 if r["_rank"] == 0 else r["_rank"])),
              ("예상 클릭", lambda r: r["예상클릭"]), ("발행주 검색 추세 비율", lambda r: r["발행주_검색추세비율"]),
              ("작년 정점까지 남은 주", lambda r: r["작년정점까지_남은주"]),
              ("발행 뒤 지난 주 수", lambda r: r["발행뒤_지난주수"]),
              ("블로그 전체 유입검색어 중 목표 포함 횟수", lambda r: r["유입검색어_블로그전체_목표포함_추정횟수"])]
    L.append("**f) 신호와 성과의 순위 상관(스피어만)** — 2주 조회수\n")
    L.append(corr_lines(base_n, sigs_n, [("2주 조회수", lambda r: r["2주성과"])]) + "\n")
    L.append("참고: 같은 신호와 '관측 4주 합계'(발행 시점이 서로 달라 거친 비교, 전체 글 대상)\n")
    L.append(corr_lines(nrows, sigs_n[:9], [("관측 4주 합계", lambda r: r["관측합계"])]) + "\n")
    L.append("**유입 검색어와 목표 키워드 (블로그 전체 기준)**\n")
    n_has = sum(1 for r in nrows if r["유입검색어_블로그전체_목표포함_추정횟수"])
    L.append(f"- 목표 키워드가 들어간 유입 검색어가 블로그 전체 검색어(4주)에 있는 글: {n_has}편 / 없는 글: {len(nrows) - n_has}편 (글별 구분 아님)\n")

    # ----- 싼세계 -----
    L.append("## 싼세계 (성과 = 최근 4주 노출·클릭)\n")
    perfs_w = [("4주 노출", lambda r: r["4주_노출"]), ("4주 클릭", lambda r: r["4주_클릭"])]
    for t in tables_for(wrows, perfs_w):
        L.append(t + "\n")
    L.append("**d) 내 글 순위** — 싼세계 글은 네이버 블로그 검색에 안 나오므로 해당 없음. 대신 검색엔진 평균 순위 구간별:\n")
    L.append(group_table("평균 순위(구글 서치콘솔)", wrows, lambda r: bin_of(r["평균순위"], [4, 8, 15], ["1~3.9위", "4~7.9위", "8~14.9위", "15위 이하"]),
                         ["1~3.9위", "4~7.9위", "8~14.9위", "15위 이하"], perfs_w) + "\n")
    L.append("**e) 성과 상위 15편 / 하위 15편 (4주 클릭 우선, 같으면 노출 순)**\n")
    key_fn = lambda r: r["4주_클릭"] * 100000 + r["4주_노출"]
    top, bot, nn = top_bottom(wrows, key_fn, "클릭 / 노출", extra_cols=[("관측일수", lambda r: str(r["관측일수"]))],
                              show_fn=lambda r: f"{r['4주_클릭']} / {r['4주_노출']}")
    L.append(f"상위 15편 (대상 {nn}편)\n\n{top}\n")
    L.append(f"하위 15편\n\n{bot}\n")
    sigs_w = sigs_n[:7] + [("예상 클릭", lambda r: r["예상클릭"]), ("발행주 검색 추세 비율", lambda r: r["발행주_검색추세비율"]),
                           ("작년 정점까지 남은 주", lambda r: r["작년정점까지_남은주"]),
                           ("발행 뒤 지난 주 수", lambda r: r["발행뒤_지난주수"]), ("관측일수", lambda r: r["관측일수"]),
                           ("유입 검색어 중 목표와 같은 노출 비중", lambda r: r["유입검색어_목표와같은_노출비중(%)"])]
    L.append("**f) 신호와 성과의 순위 상관(스피어만)**\n")
    L.append(corr_lines(wrows, sigs_w, perfs_w) + "\n")
    L.append("**유입 검색어와 목표 키워드 (글별)**\n")
    L.append(group_table("가장 많이 들어온 검색어 vs 목표", [r for r in wrows], lambda r: r["유입검색어_목표와비교"],
                         ["같음", "포함", "다름", "검색어 자료 없음"], perfs_w) + "\n")
    L.append("## 사용량\n")
    L.append(f"- 블로그 검색 {usage.get('blog', 0)}/{BLOG_LIMIT}회, 데이터랩 {usage.get('trend', 0)}/{TREND_LIMIT}회, 검색광고(검색량) {usage.get('searchad', 0)}회, Claude(haiku) {usage.get('claude', 0)}회")
    from safe_io import write_text_retry
    write_text_retry(RUN / "summary.md", "\n".join(L) + "\n")
    print(f"저장: {RUN / 'backtest_naver.csv'}, {RUN / 'backtest_wp.csv'}, {RUN / 'summary.md'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["collect", "api", "classify", "report"])
    a = ap.parse_args()
    {"collect": cmd_collect, "api": cmd_api, "classify": cmd_classify, "report": cmd_report}[a.stage](a)


if __name__ == "__main__":
    main()
