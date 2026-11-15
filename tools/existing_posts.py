# -*- coding: utf-8 -*-
"""기존 발행·예약 글 키워드 수집 (읽기 전용) → pipeline/existing_keywords.json

  - 워드프레스: .env 의 키로 REST API 에서 발행(publish)·예약(future) 글의 제목과 포커스 키워드를 읽는다.
    사이트는 .env 에 설정된 것 전부(WORDPRESS_*, SHOP_WORDPRESS_*).
    ※ Rank Math 포커스 키워드는 보통 REST 에 안 열려 있다. 읽히면 담고, 안 읽히면 제목만 담는다.
  - 네이버: 블로그 RSS(NAVER_BLOG_ID)에서 제목을 읽는다 (RSS 는 최근 글 약 50개).
plan_posts.py 가 이 목록과 겹치는 키워드를 배정에서 뺀다. 키 값은 어디에도 출력하지 않는다.

사용법
  python tools/existing_posts.py
"""
import argparse
import base64
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus, urlparse

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import config  # noqa: E402  (.env → 환경변수)
import requests  # noqa: E402

OUT_FILE = BASE / "pipeline" / "existing_keywords.json"
BROWSER_HEADERS = {  # 일부 호스팅사 보안이 python-requests 기본 User-Agent 를 막는 경우가 있어 브라우저처럼 보이게 한다
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}
FOCUS_META_KEYS = ("rank_math_focus_keyword", "_yoast_wpseo_focuskw")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def get_with_retry(url: str, **kw):
    for attempt in range(3):
        try:
            return requests.get(url, timeout=25, **kw)
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2)


def collect_wordpress(label: str, url: str, user: str, pw: str) -> tuple:
    """(항목 목록, 상태 dict)"""
    host = urlparse(url).netloc
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    headers = {**BROWSER_HEADERS, "Authorization": f"Basic {tok}"}
    items, page, pages = [], 1, 1
    while page <= pages:
        r = get_with_retry(url + "/wp-json/wp/v2/posts", headers=headers, params={
            "status": "publish,future", "per_page": 100, "page": page, "context": "edit",
            "orderby": "date", "order": "desc", "_fields": "id,title,status,date,link,meta"})
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}")
        pages = int(r.headers.get("X-WP-TotalPages") or 1)
        for p in r.json():
            t = p.get("title") or {}
            title = html.unescape((t.get("raw") or t.get("rendered") or "").strip())
            meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
            focus = next((str(meta[k]).strip() for k in FOCUS_META_KEYS if meta.get(k)), None)
            if title:
                items.append({"platform": "wordpress", "site": label, "title": title, "focus_keyword": focus,
                              "status": p.get("status"), "date": p.get("date"), "url": p.get("link")})
        page += 1
    got_focus = sum(1 for i in items if i["focus_keyword"])
    status = {"status": "ok", "host": host, "count": len(items), "focus_keywords_found": got_focus}
    if items and not got_focus:
        status["note"] = "포커스 키워드는 REST 로 읽히지 않아 제목만 수집"
    return items, status


def collect_naver_rss(blog_id: str) -> tuple:
    r = get_with_retry(f"https://rss.blog.naver.com/{blog_id}.xml", headers={"User-Agent": BROWSER_HEADERS["User-Agent"]})
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}")
    root = ET.fromstring(r.content)
    items = []
    for node in root.iter("item"):
        title = html.unescape((node.findtext("title") or "").strip())
        if title:
            items.append({"platform": "naver", "site": "naver_blog", "title": title, "focus_keyword": None,
                          "status": "publish", "date": (node.findtext("pubDate") or "").strip() or None,
                          "url": (node.findtext("link") or "").strip() or None})
    return items, {"status": "ok", "count": len(items), "note": "RSS 는 최근 글만 포함"}


def collect_naver_list(blog_id: str) -> tuple:
    """네이버 블로그 공개 글 목록(로그인 불필요)에서 전체 글 제목을 가져온다. RSS(최근 약 50개)에서 빠진 옛 글용."""
    headers = {"User-Agent": BROWSER_HEADERS["User-Agent"], "Referer": f"https://blog.naver.com/{blog_id}"}
    items, page, total = [], 1, None
    while True:
        r = get_with_retry("https://blog.naver.com/PostTitleListAsync.naver", headers=headers, params={
            "blogId": blog_id, "viewdate": "", "currentPage": page, "categoryNo": 0,
            "parentCategoryNo": "", "countPerPage": 30})
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}")
        try:
            d = json.loads(r.text)
        except ValueError:
            d = json.loads(r.text.replace("\\'", "'"))  # 네이버가 가끔 JSON 에 넣는 잘못된 이스케이프
        if d.get("resultCode") != "S":
            raise RuntimeError("목록 응답이 정상(S)이 아님")
        total = int(str(d.get("totalCount") or 0).replace(",", "") or 0)
        rows = d.get("postList") or []
        for p in rows:
            title = html.unescape(unquote_plus(p.get("title") or "")).strip()
            if title and p.get("logNo"):
                items.append({"platform": "naver", "site": "naver_blog", "title": title, "focus_keyword": None,
                              "status": "publish", "date": unquote_plus(p.get("addDate") or "") or None,
                              "url": f"https://blog.naver.com/{blog_id}/{p['logNo']}"})
        if not rows or page * 30 >= total or page >= 200:
            break
        page += 1
        time.sleep(0.3)
    return items, {"status": "ok", "count": len(items), "total_reported": total,
                   "note": "공개 글 목록(PostTitleListAsync)에서 전체 글 수집"}


def is_published(art: dict) -> bool:
    """글 장부의 한 글이 발행(또는 발행 예약)된 기록이 있는지. published 표시나 platform_published_at 날짜 중 하나라도 있으면 True."""
    for field in ("published", "platform_published_at"):
        v = art.get(field)
        if (any(v.values()) if isinstance(v, dict) else bool(v)):
            return True
    return False


def collect_app_queue() -> tuple:
    """로컬앱 대기열(content_queue*.json)에서 '등록됐지만 아직 발행 안 된' 키워드를 읽는다.
    ※ 앱 장부는 열어서 읽기만 한다(쓰기 없음).
    등록 = 승인(approved)됐거나 직접 추가(manually_added)된 항목. (승인 안 된 주간 후보 목록은 '등록'으로 보지 않는다)
    발행 = 짝이 되는 글 장부(articles*.json)에 발행 기록이 있음. 장부에 글이 없으면 아직 안 쓴 것."""
    items, by_file = [], {}
    for qp in sorted(BASE.glob("content_queue*.json")):
        if "backup" in qp.name:
            continue
        queue = json.loads(qp.read_text(encoding="utf-8"))
        if not isinstance(queue, list):
            continue
        ap = BASE / ("articles" + qp.name[len("content_queue"):])
        try:
            articles = json.loads(ap.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            articles = {}
        n = 0
        for q in queue:
            kw = " ".join(str(q.get("keyword") or "").split())
            if not kw or not (q.get("approved") or q.get("manually_added")):
                continue
            if is_published(articles.get(str(q.get("id"))) or {}):
                continue
            items.append({"platform": "app_queue", "site": qp.name, "title": kw, "focus_keyword": kw,
                          "status": "pending", "date": q.get("week"), "url": None,
                          "queue_id": q.get("id"), "target_platform": q.get("target_platform")})
            n += 1
        by_file[qp.name] = n
    if not by_file:
        raise RuntimeError("대기열 파일(content_queue*.json)이 없음")
    return items, {"status": "ok", "count": len(items), "by_file": by_file,
                   "rule": "승인 또는 직접 추가된 항목 중 글 장부에 발행 기록이 없는 것 (앱 장부는 읽기만 함)"}


def norm_title(t: str) -> str:
    return re.sub(r"\W+", "", t or "").lower()


def item_key(it: dict) -> tuple:
    """같은 글인지 판별하는 열쇠. 네이버는 글 번호, 워드프레스는 사이트+주소, 대기열은 파일+id."""
    if it.get("platform") == "app_queue":
        return ("queue", it.get("site"), str(it.get("queue_id")))
    if it.get("platform") == "naver":
        m = re.search(r"/(\d{8,})", it.get("url") or "")
        return ("naver", m.group(1) if m else norm_title(it.get("title")))
    return ("wp", it.get("site"), it.get("url") or norm_title(it.get("title")))


def merge_items(old_items: list, new_items: list, now: str, old_at: str) -> list:
    """이번에 읽은 글을 앞에 두고, 이번에 안 보인 옛 글도 그대로 남긴다(누적). first_seen/last_seen 기록."""
    old = {item_key(i): i for i in old_items}
    out, seen = [], set()
    for it in new_items:
        k = item_key(it)
        if k in seen:
            continue
        seen.add(k)
        prev = old.get(k)
        it["first_seen"] = (prev.get("first_seen") or old_at) if prev else now
        it["last_seen"] = now
        if prev and not it.get("focus_keyword") and prev.get("focus_keyword"):
            it["focus_keyword"] = prev["focus_keyword"]
        out.append(it)
    for k, prev in old.items():
        if k not in seen:
            prev.setdefault("first_seen", old_at)
            prev.setdefault("last_seen", old_at)
            out.append(prev)
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="기존 발행·예약 글 키워드 수집 (누적 저장)")
    ap.add_argument("--out", default=str(OUT_FILE), help="저장 파일 (기본: pipeline/existing_keywords.json)")
    ap.add_argument("--backfill-naver", action="store_true",
                    help="네이버 공개 글 목록으로 전체 글을 다시 채움 (기본: 처음 한 번만 자동 실행)")
    args = ap.parse_args()

    out = Path(args.out)
    try:
        prev_rec = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev_rec = {}
    old_items = prev_rec.get("items", [])
    old_at = prev_rec.get("collected_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    backfill_state = prev_rec.get("naver_backfill") or {}
    do_backfill = args.backfill_naver or not backfill_state.get("done")

    jobs = []
    if config.WORDPRESS_URL and config.WORDPRESS_USERNAME and config.WORDPRESS_APP_PASSWORD:
        jobs.append(("wordpress:longterm", lambda: collect_wordpress(
            "longterm", config.WORDPRESS_URL, config.WORDPRESS_USERNAME, config.WORDPRESS_APP_PASSWORD)))
    if config.SHOP_WORDPRESS_URL and config.SHOP_WORDPRESS_USERNAME and config.SHOP_WORDPRESS_APP_PASSWORD:
        jobs.append(("wordpress:shop", lambda: collect_wordpress(
            "shop", config.SHOP_WORDPRESS_URL, config.SHOP_WORDPRESS_USERNAME, config.SHOP_WORDPRESS_APP_PASSWORD)))
    if config.NAVER_BLOG_ID:
        if do_backfill:
            jobs.append(("naver_list", lambda: collect_naver_list(config.NAVER_BLOG_ID)))
        jobs.append(("naver_rss", lambda: collect_naver_rss(config.NAVER_BLOG_ID)))
    jobs.append(("app_queue", collect_app_queue))
    if not jobs:
        log("[실패] 수집할 곳이 없습니다 (.env 에 워드프레스·네이버 블로그 설정이 없음).")
        return 1

    all_items, sources = [], {}
    for name, fn in jobs:
        log(f"수집 중: {name}")
        try:
            items, status = fn()
            all_items += items
            sources[name] = status
        except (requests.RequestException, RuntimeError, ET.ParseError, ValueError, OSError) as e:
            sources[name] = {"status": "failed", "error": f"{type(e).__name__}: {str(e)[:120]}"}
            log(f"  실패: {sources[name]['error']}")

    ok = [s for s in sources.values() if s["status"] == "ok"]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    if sources.get("naver_list", {}).get("status") == "ok":
        backfill_state = {"done": True, "at": now, "count": sources["naver_list"]["count"]}
    if sources.get("app_queue", {}).get("status") == "ok":  # 대기열은 누적하지 않고 매번 현재 상태로 교체 (발행되면 빠져야 하므로)
        old_items = [i for i in old_items if i.get("platform") != "app_queue"]
    items = merge_items(old_items, all_items, now, old_at)  # 누적: 이번에 안 보인 옛 글도 남김
    keywords = list(dict.fromkeys(k for it in items for k in (it["title"], it["focus_keyword"]) if k))
    by_platform = {p: sum(1 for i in items if i["platform"] == p) for p in ("naver", "wordpress", "app_queue")}
    record = {"collected_at": now, "naver_backfill": backfill_state,
              "sources": sources, "counts": {"items": len(items), "keywords": len(keywords),
                                             "by_platform": by_platform,
                                             "carried_over_from_before": len(items) - len({item_key(i) for i in all_items})},
              "keywords": keywords, "items": items}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"collected_at": now, "sources": sources, "counts": record["counts"]},
                     ensure_ascii=False, indent=2))
    log(f"[저장됨] {out}")
    log(f"[완료] 누적 {len(items)}개 (네이버 {by_platform['naver']}, 워드프레스 {by_platform['wordpress']}, "
        f"앱 대기열(미발행) {by_platform['app_queue']}), "
        f"키워드(제목+포커스) {len(keywords)}개 — 성공한 곳 {len(ok)}/{len(sources)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
