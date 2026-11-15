# -*- coding: utf-8 -*-
"""황금 키워드 판정용 지표 측정기 (측정만 한다. 등급·배정에는 아직 쓰지 않는다. Claude 호출 없음, 읽기 전용)

키워드마다 아래 지표를 모아 한 덩어리(golden)로 돌려준다.
  1) 월 발행 경쟁률   블로그 검색 API(sort=date, display=100)를 페이지로 넘기며 최근 30일 안 발행 글 수를 센다
                      (최대 1,000개에서 멈춤). 경쟁률 = 월간 검색량 ÷ 최근 30일 발행 수 (클수록 글이 덜 몰림)
  2) 상위 글 분석     네이버 검색 결과의 블로그·인기글 영역 상위 5개 글을 ego-browser 로 열어서 수집
                      (작성일, 제목에 키워드 포함 여부, 이미지 수, 본문 글자 수, 블로그 이름, 인플루언서·공식 표시)
  3) 검색광고 지표    월평균 클릭수(PC/모바일), 경쟁정도(compIdx)   ← collect_metrics.related_keywords 가 가져옴
  4) 화면 위치        블로그·인기글 영역이 위에서 몇 번째 영역인지, AI 브리핑 출처에 블로그가 있는지
                      ← naver_serp_collect.collect 가 가져옴
  5) 선점 지표        데이터랩 주간 데이터로 '작년 같은 시기 이후 8주' 추세

결과는 pipeline/golden_cache.json 에 30일(filter.json 의 cache_days) 동안 저장해 다시 수집하지 않는다.
앱 장부(articles*.json, content_queue*.json)는 건드리지 않는다.

사용법 (시험 측정)
  python tools/golden_metrics.py --result pipeline/result_20261108_184114.json --types-file <분류결과.json> --n 30 --out <저장.json>
"""
import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import collect_metrics as cm  # noqa: E402
from safe_io import write_text_retry  # noqa: E402  (드라이브 동기화 폴더 쓰기 오류 대비)

BASE = TOOLS.parent
GOLDEN_CACHE = BASE / "pipeline" / "golden_cache.json"
MARK = "@@GOLDEN_POSTS@@"
TASK_SPACE_NAME = "golden-top-posts"
RUN_TIMEOUT_SEC = 180
PAGE_SIZE = 100
MAX_ITEMS = 1000          # 블로그 검색 API 는 start 가 최대 1000 이라 1,000개까지만 센다
WINDOW_DAYS = 30
LEAD_WEEKS = 8
TREND_CAP = 5            # 추세 값(올해/작년, 선점 추세)은 이 값까지만 기록

log = cm.log


# ---------- 1) 월 발행 경쟁률 ----------

def publish_stats(api, keyword: str, monthly_total: int, today: date = None) -> dict:
    """최근 30일 안에 발행된 블로그 글 수 (최신순 정렬 결과를 '건너뛰며' 확인해 API 호출을 줄인다).
    1) 첫 페이지(1~100번째)에 30일 밖 글이 있으면 → 그 페이지만으로 끝 (1회)
    2) 아니면 마지막 페이지(901~1000번째)를 본다: 전부 30일 안이면 '1,000 이상' (2회)
    3) 그 사이면 페이지를 반씩 좁혀(이분 탐색) 30일 경계가 있는 페이지를 찾는다 (보통 4~5회)
    예전 방식(앞에서부터 한 장씩, 1,000 이상이면 10회)과 결과는 같고 호출 수만 줄어든다.
    첫 페이지 응답의 total(전체 블로그 문서 수)도 함께 돌려준다(포화도 계산에 재사용)."""
    today = today or date.today()
    cutoff = (today - timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d")
    pages = {}

    def page(idx):        # idx 0 = 1~100번째, 9 = 901~1000번째
        if idx not in pages:
            j = api.call("blog", params={"query": keyword, "display": PAGE_SIZE, "start": 1 + idx * PAGE_SIZE, "sort": "date"})
            pages[idx] = j["items"] if (j and "items" in j) else None
            if idx == 0 and j:
                pages["total"] = j.get("total")
        return pages[idx]

    inside = lambda items: sum(1 for it in items if str(it.get("postdate", "")) >= cutoff)
    full = lambda items: len(items) == PAGE_SIZE and inside(items) == PAGE_SIZE
    fail = {"posts_30d": None, "capped": False, "rate": None, "error": "블로그 검색 API 호출 실패"}
    last = (MAX_ITEMS - 1) // PAGE_SIZE      # 9

    first = page(0)
    if first is None:
        return fail
    capped = False
    if not full(first):
        count = inside(first)
    else:
        tail = page(last)
        if tail is None:
            return fail
        if full(tail):
            count, capped = MAX_ITEMS, True
        else:
            lo, hi = 0, last          # lo 페이지는 전부 30일 안, hi 페이지는 아님
            while hi - lo > 1:
                mid = (lo + hi) // 2
                items = page(mid)
                if items is None:
                    return fail
                if full(items):
                    lo = mid
                else:
                    hi = mid
            count = hi * PAGE_SIZE + inside(page(hi))
    calls = sum(1 for k in pages if k != "total")
    rate = round(monthly_total / count, 2) if count > 0 else None
    return {"posts_30d": count, "capped": capped, "rate": rate, "total": pages.get("total"), "calls": calls}


TAG_RE = re.compile(r"<[^>]+>")


def _nospace(t: str) -> str:
    return "".join(TAG_RE.sub("", t or "").split()).lower()


def title_compete(api, keyword: str, monthly_total: int, gc: dict, today: date = None) -> dict:
    """'제목 기준' 경쟁 측정 (compete_mode = "title").
    블로그 검색은 단어가 본문 어디에 있어도 글을 세서, 흔한 단어로 된 생활 문제 키워드의 경쟁을 크게 부풀린다.
    그래서 '그 키워드를 제목에 넣어 쓴 글'만 경쟁 글로 센다.
      1) 관련도순 상위 100개 중 제목에 키워드가 들어간 글 수(top100_title) — 1회 호출.
         drop_top100_title 이상이면 경쟁이 센 키워드라 여기서 끝.
      2) 최신순으로 최대 title_scan_pages 페이지(기본 5 = 500개)를 보며 최근 30일 글 중 제목 일치 글 수를 센다.
         500개가 모두 30일 안이면 본 기간으로 30일치를 환산한다(title_30d_estimated=True).
    경쟁률(rate) = 월간 검색량 ÷ 30일 제목 일치 글 수 (0개면 1개로 계산)."""
    today = today or date.today()
    cutoff_d = today - timedelta(days=WINDOW_DAYS)
    cutoff = cutoff_d.strftime("%Y%m%d")
    k = _nospace(keyword)
    calls = 0
    j = api.call("blog", params={"query": keyword, "display": PAGE_SIZE, "start": 1, "sort": "sim"})
    calls += 1
    if not j or "items" not in j:
        return {"posts_30d": None, "capped": False, "rate": None, "error": "블로그 검색 API 호출 실패", "calls": calls, "mode": "title"}
    top = j["items"]
    top100 = sum(1 for it in top if k in _nospace(it.get("title")))
    total = j.get("total")
    base = {"mode": "title", "top100_title": top100, "top100_n": len(top), "total": total}
    if top100 >= gc["drop_top100_title"]:
        return {**base, "posts_30d": None, "capped": False, "title_30d": None, "rate": None, "calls": calls,
                "dropped_early": True}
    inside = hit = 0
    oldest, reached, ok = None, False, True
    for idx in range(int(gc["title_scan_pages"])):
        jj = api.call("blog", params={"query": keyword, "display": PAGE_SIZE, "start": 1 + idx * PAGE_SIZE, "sort": "date"})
        calls += 1
        if not jj or "items" not in jj:
            ok = False
            break
        items = jj["items"]
        for it in items:
            pd = str(it.get("postdate", ""))
            if pd >= cutoff:
                inside += 1
                hit += k in _nospace(it.get("title"))
                oldest = pd
            else:
                reached = True
        if reached or len(items) < PAGE_SIZE:
            reached = True
            break
    if not ok and inside == 0:
        return {**base, "posts_30d": None, "capped": False, "rate": None, "error": "블로그 검색 API 호출 실패", "calls": calls}
    estimated = False
    title_30d = float(hit)
    if not reached and oldest:            # 500개가 모두 30일 안: 본 기간으로 30일치 환산
        span = max(1, (today - datetime.strptime(oldest, "%Y%m%d").date()).days)
        title_30d = round(hit * WINDOW_DAYS / span, 1)
        estimated = True
    rate = round(monthly_total / max(1.0, title_30d), 2)
    return {**base, "posts_30d": inside, "capped": not reached, "title_30d": title_30d, "title_30d_estimated": estimated,
            "rate": rate, "calls": calls, **({"partial": True} if not ok else {})}


def publish_for(api, keyword: str, monthly_total: int, gc: dict) -> dict:
    """compete_mode 에 따라 경쟁을 잰다: "title" = 제목 기준(기본), "any" = 예전 방식(본문 어디든 단어가 들어간 글 수)."""
    if gc.get("compete_mode") == "title":
        return title_compete(api, keyword, monthly_total, gc)
    return publish_stats(api, keyword, monthly_total)


# ---------- 5) 선점 지표 (데이터랩 주간) ----------

def lead_stats(api, keyword: str, today: date = None) -> dict:
    """작년 같은 시기(364일 전) 기준: 직전 4주 평균 vs 이후 8주 평균, 그리고 올해 최근 4주 vs 작년 같은 4주.

    seasonal_lift_8w  = 작년 '이후 8주' 평균 ÷ 작년 '직전 4주' 평균   (1보다 크면 작년엔 앞으로 8주간 늘었다)
    yoy_recent_4w     = 올해 최근 4주 평균 ÷ 작년 같은 4주 평균       (1보다 크면 올해가 작년보다 높다)
    """
    today = today or date.today()
    ly = today - timedelta(days=364)
    start, end = ly - timedelta(days=28), today - timedelta(days=1)
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "week",
            "keywordGroups": [{"groupName": "g0", "keywords": [keyword]}]}
    j = api.call("trend", json=body)
    if not j or not j.get("results"):
        return {"seasonal_lift_8w": None, "yoy_recent_4w": None, "error": "데이터랩 호출 실패"}
    pts = []
    for d in j["results"][0].get("data", []):
        try:
            pts.append((date.fromisoformat(d["period"][:10]), float(d.get("ratio") or 0)))
        except (KeyError, ValueError):
            continue

    def avg(a: date, b: date):
        v = [r for p, r in pts if a <= p < b]
        return sum(v) / len(v) if v else None

    base_ly = avg(ly - timedelta(days=28), ly)
    next8_ly = avg(ly, ly + timedelta(days=7 * LEAD_WEEKS))
    recent_ty = avg(today - timedelta(days=28), today)
    div = lambda a, b: round(a / b, 2) if a is not None and b else None
    cap = lambda v: None if v is None else min(v, TREND_CAP)
    lift_raw, yoy_raw = div(next8_ly, base_ly), div(recent_ty, base_ly)
    return {"seasonal_lift_8w": cap(lift_raw), "yoy_recent_4w": cap(yoy_raw),
            "seasonal_lift_8w_raw": lift_raw, "yoy_recent_4w_raw": yoy_raw,
            "ly_prev4w_avg": None if base_ly is None else round(base_ly, 1),
            "ly_next8w_avg": None if next8_ly is None else round(next8_ly, 1),
            "ty_recent4w_avg": None if recent_ty is None else round(recent_ty, 1)}


# ---------- 2) 상위 글 분석 (ego-browser, 스크립트만) ----------

JS_POSTS = r"""
const POSTS = __POSTS_JSON__;
const SPACE_NAME = __SPACE_JSON__;
const MARK = __MARK_JSON__;
const toAscii = (s) => s.replace(/[\u007f-￿]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
const task = await taskSpace(SPACE_NAME);
const out = [];
try {
  const page = task.page("p1");
  for (const p of POSTS) {
    const t0 = Date.now();
    try {
      await page.goto("https://blog.naver.com/PostView.naver?blogId=" + encodeURIComponent(p.blog_id) + "&logNo=" + p.log_no, { timeout: 30000 });
      await page.waitForLoadState();
      const r = await page.evaluate(() => {
        const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
        const q = (s) => document.querySelector(s);
        const body = q(".se-main-container") || q("#postViewArea");
        const text = body ? (body.innerText || "") : "";
        // 이미지: 직접 올린 사진(se-image-resource)만 센다. 쇼핑커넥트 상품 썸네일은 따로 센다.
        const srcOf = (i) => i.getAttribute("data-lazy-src") || i.src;
        const imgs = body ? new Set([...body.querySelectorAll("img.se-image-resource, #postViewArea img")].map(srcOf).filter(Boolean)) : new Set();
        const shop = body ? new Set([...body.querySelectorAll("img[class*='shopping-connect']")].map(srcOf).filter(Boolean)) : new Set();
        return {
          title: clean((q(".se-title-text") || q(".pcol1") || {}).innerText),
          date: clean((q(".se_publishDate") || q(".se_date") || {}).innerText),
          nick: clean((q(".blog_name") || q(".nick") || q("#nickNameArea") || {}).innerText),
          chars: text.replace(/\s/g, "").length,
          images: imgs.size,
          shop_thumbs: shop.size,
          found_body: !!body,
        };
      });
      out.push({ rank: p.rank, ok: r.found_body, ...r, ms: Date.now() - t0 });
    } catch (e) {
      out.push({ rank: p.rank, ok: false, error: String(e && e.message || e).slice(0, 120), ms: Date.now() - t0 });
    }
  }
} finally {
  await task.finish({ keep: [] });
}
console.log(MARK + toAscii(JSON.stringify(out)));
"""


def _ascii_js(js: str) -> bytes:
    """한글이 셸/인코딩 문제로 깨지지 않도록 전부 \\uXXXX 로 바꿔 순수 ASCII 로 보낸다."""
    def esc(ch: str) -> str:
        if ord(ch) < 0x80:
            return ch
        u = ch.encode("utf-16-be")
        return "".join(f"\\u{u[i]:02x}{u[i + 1]:02x}" for i in range(0, len(u), 2))
    return "".join(esc(c) for c in js).encode("ascii")


def open_posts(cards: list) -> list:
    """카드(블로그 글 주소 목록) → 글 페이지에서 읽은 값 목록. 실패한 글은 ok=False 로 남긴다."""
    exe = shutil.which("ego-browser")
    if not exe:
        raise RuntimeError("ego-browser 를 찾을 수 없습니다.")
    posts = [{"rank": c["rank"], "blog_id": c["blog_id"], "log_no": c["log_no"]} for c in cards]
    js = (JS_POSTS.replace("__SPACE_JSON__", json.dumps(TASK_SPACE_NAME)).replace("__MARK_JSON__", json.dumps(MARK))
          .replace("__POSTS_JSON__", json.dumps(posts)))
    try:
        proc = subprocess.run([exe, "nodejs"], input=_ascii_js(js), capture_output=True, timeout=RUN_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"상위 글 열기가 {RUN_TIMEOUT_SEC}초 안에 끝나지 않았습니다.")
    text = proc.stdout.decode("utf-8", "replace") + "\n" + proc.stderr.decode("utf-8", "replace")
    line = next((l for l in text.splitlines() if l.startswith(MARK)), None)
    if line is None:
        raise RuntimeError("상위 글 수집 결과를 받지 못했습니다: " + text[-300:].replace("\n", " "))
    return json.loads(line[len(MARK):])


def parse_post_date(s: str, today: date = None):
    """'2026. 10. 29. 11:28' / '2026.09.08.' / '5시간 전' / '3일 전' / '2주 전' / '1개월 전' → date. 읽지 못하면 None."""
    today = today or date.today()
    s = (s or "").strip()
    m = re.match(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d+)\s*(분|시간|일|주|개월|달|년)\s*전", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"분": 0, "시간": 0, "일": n, "주": 7 * n, "개월": 30 * n, "달": 30 * n, "년": 365 * n}[unit]
        return today - timedelta(days=days)
    return today - timedelta(days=1) if s.startswith("어제") else None


def analyze_top_posts(keyword: str, cards: list, today: date = None) -> dict:
    today = today or date.today()
    t0 = time.time()
    opened = {p["rank"]: p for p in open_posts(cards)} if cards else {}
    kw, kw_ns = keyword, keyword.replace(" ", "")
    posts = []
    for c in cards:
        p = opened.get(c["rank"], {})
        title = p.get("title") or c.get("title") or ""
        d = parse_post_date(p.get("date", ""), today) or parse_post_date(c.get("date_text", ""), today)
        posts.append({
            "rank": c["rank"], "url": c["url"], "blog_name": p.get("nick") or c.get("name"),
            "title": title, "date": d.isoformat() if d else None, "age_days": (today - d).days if d else None,
            "title_has_keyword": kw in title, "title_has_keyword_nospace": kw_ns in title.replace(" ", ""),
            "images": p.get("images"), "shop_thumbs": p.get("shop_thumbs"), "chars": p.get("chars"),
            "influencer": c.get("influencer"), "official_badge": c.get("official_badge"),
            "opened": bool(p.get("ok")), **({"error": p["error"]} if p.get("error") else {}),
        })
    ok = [p for p in posts if p["opened"]]
    nums = lambda k: [p[k] for p in ok if p.get(k) is not None]
    med = lambda v: round(statistics.median(v), 1) if v else None
    summary = {
        "count": len(posts), "opened": len(ok),
        "title_kw_nospace": sum(1 for p in posts if p["title_has_keyword_nospace"]),
        "title_kw_exact": sum(1 for p in posts if p["title_has_keyword"]),
        "median_age_days": med(nums("age_days")), "median_images": med(nums("images")), "median_chars": med(nums("chars")),
        "influencer": sum(1 for p in posts if p["influencer"]), "official": sum(1 for p in posts if p["official_badge"]),
    }
    return {"posts": posts, "summary": summary, "open_seconds": round(time.time() - t0, 1)}


# ---------- 캐시 ----------

def load_cache() -> dict:
    try:
        c = json.loads(GOLDEN_CACHE.read_text(encoding="utf-8"))
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    GOLDEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    write_text_retry(GOLDEN_CACHE, json.dumps(cache, ensure_ascii=False))


# ---------- 한 키워드 측정 ----------

def measure(api, row: dict, serp: dict, cache: dict, cache_days: float = 30, publish: dict = None) -> dict:
    """row: keyword, monthly_total, (ave_pc_clk, ave_mobile_clk, comp_idx) / serp: naver_serp_collect.collect 결과(또는 같은 키를 가진 요약)."""
    kw = row["keyword"]
    c = cache.get(kw)
    if c and time.time() - c.get("ts", 0) < cache_days * 86400:
        return {**c["v"], "from_cache": True}
    feed = (serp or {}).get("blog_feed") or {}
    t0 = time.time()
    v = {
        "publish": publish or publish_stats(api, kw, row["monthly_total"]),
        "searchad": {"ave_pc_clk": row.get("ave_pc_clk"), "ave_mobile_clk": row.get("ave_mobile_clk"),
                     "comp_idx": row.get("comp_idx")},
        "serp_position": {"blog_area_order": feed.get("order"), "blog_area_titled_order": feed.get("titled_order"),
                          "blog_area_y": feed.get("y"), "total_blocks": feed.get("total_blocks"),
                          "has_ai_briefing": (serp or {}).get("has_ai_briefing"),
                          "ai_briefing_has_blog": (serp or {}).get("ai_briefing_has_blog"),
                          "ai_briefing_blog_count": (serp or {}).get("ai_briefing_blog_count")},
        "lead": lead_stats(api, kw),
    }
    try:
        v["top_posts"] = analyze_top_posts(kw, feed.get("cards") or [])
    except Exception as e:
        v["top_posts"] = {"posts": [], "summary": None, "open_seconds": None, "error": (str(e).splitlines() or [""])[0][:150]}
    v["api_seconds"] = round(time.time() - t0 - (v["top_posts"].get("open_seconds") or 0), 1)
    cache[kw] = {"ts": time.time(), "v": v}
    save_cache(cache)
    return {**v, "from_cache": False}



# ---------- 발행 수만 먼저 (API만, 탈락 판정용) ----------

def publish_cached(api, row: dict, cache: dict, cache_days: float = 30, gc: dict = None) -> dict:
    """경쟁(최근 30일 발행 또는 제목 일치 글 수)만 구한다(캐시 30일). 경쟁률은 검색량이 달라질 수 있어 꺼낼 때마다 다시 계산한다.
    gc.compete_mode 가 "title" 이면 제목 기준(캐시 _pubt), 아니면 예전 방식(캐시 _pub)."""
    kw, vol = row["keyword"], row["monthly_total"]
    title_mode = bool(gc) and gc.get("compete_mode") == "title"
    pub = cache.setdefault("_pubt" if title_mode else "_pub", {})
    c = pub.get(kw)
    if c and time.time() - c.get("ts", 0) < cache_days * 86400:
        v = dict(c["v"])
        if title_mode:
            if v.get("title_30d") is not None:
                v["rate"] = round(vol / max(1.0, v["title_30d"]), 2)
        else:
            v["rate"] = round(vol / v["posts_30d"], 2) if v.get("posts_30d") else None
        return {**v, "from_cache": True}
    v = title_compete(api, kw, vol, gc) if title_mode else publish_stats(api, kw, vol)
    if (v.get("posts_30d") is not None or v.get("dropped_early")) and not v.get("partial") and not v.get("error"):
        pub[kw] = {"ts": time.time(), "v": v}
    return {**v, "from_cache": False}


# ---------- 네이버 자동완성 (확장 씨앗용) ----------

def autocomplete(q: str, limit: int = 10) -> list:
    import requests
    try:
        r = requests.get("https://ac.search.naver.com/nx/ac",
                         params={"q": q, "con": "1", "frm": "nv", "ans": "2", "r_format": "json", "r_enc": "UTF-8",
                                 "r_unicode": "0", "t_koreng": "1", "run": "2", "rev": "4", "q_enc": "UTF-8", "st": "100"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        time.sleep(0.3)
        items = r.json().get("items") or [[]]
        return [it[0] for it in items[0] if it and it[0]][:limit]
    except Exception:
        return []


# ---------- 등급 판정 (기준값은 filter.json 의 golden) ----------

DEFAULT_GOLDEN = {
    "enabled": False, "volume_min": 100, "volume_max": 10000, "saturation_keep_ratio": 1.0, "drop_posts_30d": 1000, "drop_rate_below": 2.5, "s_rate_min": 4, "a_rate_min": 2.5,
    "max_title_keyword": 2, "max_influencer": 2, "drop_influencer_min": 4,
    "min_yoy_recent_4w": 0.8, "min_lead_lift": 1.0, "min_ly_base": 5, "trend_cap": 5,
    "title_match": "nospace", "position_field": "blog_area_order",
    "position_weights": [{"max_order": 4, "weight": 1.0}, {"max_order": 6, "weight": 0.8},
                         {"max_order": 8, "weight": 0.6}, {"max_order": 999, "weight": 0.3}],
    "expand": {"min_sa": 45, "max_rounds": 3, "max_analyzed": 250, "max_hours": 2, "max_new_seeds_per_round": 40},
    # 제목 기준 경쟁 측정 (compete_mode = "title")
    "compete_mode": "any", "drop_top100_title": 50, "s_top100_title_max": 30,
    "s_rate_min_title": 50, "a_rate_min_title": 20, "title_scan_pages": 5,
}


def golden_config(cfg: dict) -> dict:
    """filter.json 의 golden 항목을 기본값과 합친다 (없는 항목은 기본값)."""
    g = {**DEFAULT_GOLDEN, **(cfg.get("golden") or {})}
    g["expand"] = {**DEFAULT_GOLDEN["expand"], **((cfg.get("golden") or {}).get("expand") or {})}
    if not (0 < g["volume_min"] < g["volume_max"]):
        raise ValueError("filter.json golden.volume_min 은 0 보다 크고 golden.volume_max 보다 작아야 합니다.")
    if not (0 < g["saturation_keep_ratio"] <= 1):
        raise ValueError("filter.json golden.saturation_keep_ratio 는 0 초과 1 이하여야 합니다.")
    if g["title_match"] not in ("nospace", "exact"):
        raise ValueError('filter.json golden.title_match 는 "nospace" 또는 "exact" 여야 합니다.')
    if g["position_field"] not in ("blog_area_order", "blog_area_titled_order"):
        raise ValueError('filter.json golden.position_field 는 "blog_area_order" 또는 "blog_area_titled_order" 여야 합니다.')
    return g


def rate_mins(gc: dict, pub: dict = None) -> tuple:
    """(S 경쟁률 하한, A 경쟁률 하한). 제목 기준이면 *_title 값을 쓴다."""
    title = (pub.get("mode") == "title") if pub is not None else (gc.get("compete_mode") == "title")
    if title:
        return gc["s_rate_min_title"], gc["a_rate_min_title"]
    return gc["s_rate_min"], gc["a_rate_min"]


def s_level(rate, pub: dict, gc: dict) -> bool:
    """경쟁률이 S 수준인지. 제목 기준이면 상위 100 제목 일치가 s_top100_title_max 이하여야 S."""
    s_min, _ = rate_mins(gc, pub)
    rate_v = float("inf") if rate is None else rate
    if rate_v < s_min:
        return False
    if (pub or {}).get("mode") == "title":
        t = pub.get("top100_title")
        return t is not None and t <= gc["s_top100_title_max"]
    return True


def pre_drop(pub: dict, gc: dict):
    """발행 수·경쟁률만으로 하는 1차 탈락. (탈락 사유 또는 None)"""
    if pub.get("mode") == "title":
        t = pub.get("top100_title")
        if t is not None and t >= gc["drop_top100_title"]:
            return f"상위 100개 중 제목 일치 {t}개 (기준 {gc['drop_top100_title']}개 이상, 경쟁 셈)"
        if pub.get("title_30d") is None:
            return "제목 일치 글 수를 측정하지 못함"
        if pub.get("rate") is not None and pub["rate"] < gc["a_rate_min_title"]:
            return f"제목 기준 경쟁률 {pub['rate']} (기준 {gc['a_rate_min_title']} 미만, 30일 제목 일치 {pub['title_30d']}개)"
        return None
    posts, rate = pub.get("posts_30d"), pub.get("rate")
    if posts is None:
        return "발행 수를 측정하지 못함"
    if pub.get("capped") or posts >= gc["drop_posts_30d"]:
        plus = "+" if pub.get("capped") else ""
        return f"최근 30일 발행 {posts:,}{plus}개 (기준 {gc['drop_posts_30d']:,} 이상)"
    if rate is not None and rate < gc["drop_rate_below"]:
        return f"경쟁률 {rate} (기준 {gc['drop_rate_below']} 미만)"
    return None


def position_weight(order, gc: dict):
    if order is None:
        return None
    for step in sorted(gc["position_weights"], key=lambda x: x["max_order"]):
        if order <= step["max_order"]:
            return step["weight"]
    return gc["position_weights"][-1]["weight"]


def trend_growth_of(series):
    """최근 3개월 평균이 이전 9개월 평균보다 얼마나 늘었는지(0.5 = +50%). run_pipeline.trend_growth 와 같은 계산."""
    if not series or len(series) < 12:
        return None
    vals = [p["ratio"] for p in series]
    recent, prev = sum(vals[-3:]) / 3, sum(vals[:-3]) / (len(vals) - 3)
    if prev <= 0:
        return 1.0 if recent > 0 else 0.0
    return round(recent / prev - 1, 2)


def grade_keyword(g: dict, volume: int, gc: dict, trend_growth=None) -> dict:
    """측정값(golden 덩어리) → 등급(S/A/B/탈락/None), 탈락 사유, 조건별 결과, 예상 유입."""
    drop = pre_drop(g["publish"], gc)
    if drop:
        measured = g["publish"].get("posts_30d") is not None or g["publish"].get("top100_title") is not None
        return {"grade": "탈락" if measured else None, "drop_reason": drop}
    rate = g["publish"]["rate"]
    top = (g.get("top_posts") or {}).get("summary") or {}
    lead = g.get("lead") or {}
    title_n = top.get("title_kw_nospace" if gc["title_match"] == "nospace" else "title_kw_exact")
    inf_n = top.get("influencer")
    if inf_n is not None and inf_n >= gc["drop_influencer_min"]:
        return {"grade": "탈락", "drop_reason": f"상위 5개 중 인플루언서 {inf_n}개 (기준 {gc['drop_influencer_min']}개 이상)"}
    cap = lambda v: None if v is None else min(v, gc["trend_cap"])
    yoy, lift = cap(lead.get("yoy_recent_4w")), cap(lead.get("seasonal_lift_8w"))
    ly_base = lead.get("ly_prev4w_avg")
    base_ok = ly_base is not None and ly_base >= gc["min_ly_base"]    # 작년 같은 4주 비율이 너무 낮으면 올해/작년·선점 추세 둘 다 안 씀
    yoy_used = base_ok and yoy is not None
    lift_used = base_ok and lift is not None
    if yoy_used or lift_used:
        basis = "올해/작년·선점 추세"
        lead_ok = (yoy_used and yoy >= gc["min_yoy_recent_4w"]) or (lift_used and lift >= gc["min_lead_lift"])
    elif trend_growth is not None:   # 둘 다 못 쓰면: 최근 3개월÷이전 9개월 성장이 0 이상(줄지 않음)이면 충족
        basis = f"trend_growth {trend_growth:+.2f} (최근 3개월÷이전 9개월, 0 이상이면 충족)"
        lead_ok = trend_growth >= 0
    else:
        basis = "판단 불가 (추세 값 없음)"
        lead_ok = False
    conds = {
        "title_keyword": {"value": title_n, "ok": title_n is not None and title_n <= gc["max_title_keyword"]},
        "influencer": {"value": inf_n, "ok": inf_n is not None and inf_n <= gc["max_influencer"]},
        "lead": {"yoy_recent_4w": yoy, "seasonal_lift_8w": lift, "ly_prev4w_avg": ly_base, "yoy_used": yoy_used,
                 "lift_used": lift_used, "trend_growth": trend_growth, "basis": basis, "ok": bool(lead_ok)},
    }
    missed = [k for k, c in conds.items() if not c["ok"]]
    if s_level(rate, g["publish"], gc):
        grade = "S" if not missed else ("A" if len(missed) == 1 else "B")
    else:  # A 하한 <= 경쟁률 < S 하한 (또는 제목 기준에서 상위 100 제목 일치가 많음)
        grade = "A" if not missed else "B"
    order = (g.get("serp_position") or {}).get(gc["position_field"])
    w = position_weight(order, gc)
    return {"grade": grade, "drop_reason": None, "conditions": conds, "missed_conditions": missed,
            "position_order": order, "position_weight": w,
            "expected_traffic": None if w is None else round(volume * w)}


def regrade_by_rate(missed: list, rate, gc: dict, pub: dict = None):
    """황금 키워드 은행 재확인용: 3조건 결과(missed)는 그대로 두고 새 경쟁률로만 등급을 다시 매긴다.
    pre_drop 기준(발행 수·경쟁률 미달)은 호출하는 쪽이 먼저 걸러 둔다. → 'S' / 'A' / 'B'"""
    if pub is not None:
        lvl = s_level(rate, pub, gc)
    else:
        lvl = (float("inf") if rate is None else rate) >= rate_mins(gc)[0]
    if lvl:
        return "S" if not missed else ("A" if len(missed) == 1 else "B")
    return "A" if not missed else "B"


GRADE_ORDER = {"S": 0, "A": 1, "B": 2, "탈락": 3}


def sort_key(grade: dict):
    """S 먼저, 같은 등급은 예상 유입 많은 순."""
    return (GRADE_ORDER.get(grade.get("grade"), 9), -(grade.get("expected_traffic") or 0))


# ---------- 시험 측정 (명령줄) ----------

def pick_keywords(result_path: Path, types_path: Path, n: int) -> list:
    d = json.loads(result_path.read_text(encoding="utf-8"))
    types = json.loads(types_path.read_text(encoding="utf-8"))["types"]
    rows = [r for r in d["keywords"] if r.get("passed_filter") and types.get(r["keyword"]) in ("solve", "choose")
            and r.get("score")]
    rows.sort(key=lambda r: r["score"]["total"], reverse=True)
    out = rows[:n]
    for r in out:
        r["kw_type"] = types[r["keyword"]]
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="황금 키워드 지표 시험 측정")
    ap.add_argument("--result")
    ap.add_argument("--types-file", help="keyword_classify 결과 JSON (types 포함)")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", required=True)
    ap.add_argument("--serp-gap", type=float, default=3.0)
    ap.add_argument("--grade-file", help="이미 측정한 결과(JSON)에 등급 판정만 적용 (측정·API 호출 없음)")
    ap.add_argument("--filter", default=str(TOOLS / "filter.json"))
    args = ap.parse_args()
    if args.grade_file:
        cfg = json.loads(Path(args.filter).read_text(encoding="utf-8-sig"))
        gc = golden_config(cfg)
        d = json.loads(Path(args.grade_file).read_text(encoding="utf-8"))
        tg = {}
        if args.result:   # 추세 값(trend_12m)이 들어 있는 파이프라인 결과에서 trend_growth 를 계산
            for k in json.loads(Path(args.result).read_text(encoding="utf-8"))["keywords"]:
                tg[k["keyword"]] = trend_growth_of(k.get("trend_12m"))
        for r in d["results"]:
            if r.get("golden"):
                r["grade"] = grade_keyword(r["golden"], r["monthly_total"], gc, tg.get(r["keyword"]))
        Path(args.out).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"[저장됨] {args.out}")
        return 0
    if not (args.result and args.types_file):
        ap.error("--result 와 --types-file 이 필요합니다 (또는 --grade-file)")

    from naver_serp_collect import collect as serp_collect
    rows = pick_keywords(Path(args.result), Path(args.types_file), args.n)
    log(f"측정 대상 {len(rows)}개 (해결·정보형 {sum(r['kw_type'] == 'solve' for r in rows)}, 제품선택형 {sum(r['kw_type'] == 'choose' for r in rows)})")
    api, cache, results = cm.OpenApi(), load_cache(), []
    t_all = time.time()
    for i, r in enumerate(rows, 1):
        kw = r["keyword"]
        t1 = time.time()
        try:
            ad = next((x for x in cm.related_keywords(kw) if x["keyword"].replace(" ", "") == kw.replace(" ", "")), None)
            if ad:
                r.update({k: ad[k] for k in ("ave_pc_clk", "ave_mobile_clk", "comp_idx")})
            cached = cache.get(kw)
            fresh = bool(cached) and time.time() - cached.get("ts", 0) < 30 * 86400
            serp = None if fresh else serp_collect(kw)
            g = measure(api, r, serp, cache)
            g_err = None
        except Exception as e:
            g, g_err = None, (str(e).splitlines() or [""])[0][:200]
        log(f"[{i}/{len(rows)}] {kw}: {'실패 ' + g_err if g_err else '완료'} ({time.time() - t1:.0f}초)")
        results.append({"keyword": kw, "kw_type": r["kw_type"], "monthly_total": r["monthly_total"],
                        "golden": g, **({"error": g_err} if g_err else {})})
        time.sleep(args.serp_gap)
    out = {"measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "total_seconds": round(time.time() - t_all, 1), "api_status": {"blog_search": api.status("blog"), "datalab": api.status("trend")},
           "results": results}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"[저장됨] {args.out}  (전체 {out['total_seconds']}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
