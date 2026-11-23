"""N살림노트(naver_tips) 발행 주소 자동 채우기 — 미리 보기(저장 안 함) → 사람이 [채움만 저장]을 눌렀을 때만 저장.
데스크피커(app.py 의 radar 주소 채우기)와 같은 읽기 방식·같은 제목 정리를 쓰지만, 코드는 이 파일에 따로 둔다(데스크피커 코드는 안 건드림).
블로그: blog.naver.com/beafather (RSS 1회 읽기). 네이버 API 0회."""
import difflib
import html as _html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

BLOG_ID = "beafather"
SINCE = "2026-11-16"   # 대기열 id 날짜가 이 날 이후인 글만
_LINK_RE = re.compile(r"^https?://(?:m\.)?blog\.naver\.com/" + BLOG_ID + r"/(\d+)(?:[/?#].*)?$", re.I)
_LOGNO_RE = re.compile(r"[?&]logNo=(\d+)", re.I)
_BLOG_RE = re.compile(r"^https?://(?:m\.)?blog\.naver\.com/(?:PostView\.naver)?\?[^#]*blogId=" + BLOG_ID, re.I)


def unescape(t):
    """HTML 기호를 풀어 글자로(두 겹이어도 더 안 풀릴 때까지, 최대 3번). 앞뒤 빈칸 제거."""
    t = t or ""
    for _ in range(3):
        u = _html.unescape(t)
        if u == t:
            break
        t = u
    return t.strip()


def norm_title(t):
    """제목 짝짓기용: HTML 기호를 풀고 띄어쓰기·문장부호를 모두 빼고 소문자로(글자·숫자만 남김)."""
    return re.sub(r"[\W_]+", "", unescape(t)).lower()


def post_url(raw):
    """RSS 항목 주소 → https://blog.naver.com/beafather/<글번호> 또는 ''(형식이 다르면 채우지 않음)."""
    raw = (raw or "").strip()
    m = _LINK_RE.match(raw)
    if m:
        return f"https://blog.naver.com/{BLOG_ID}/{m.group(1)}"
    if _BLOG_RE.match(raw):
        n = _LOGNO_RE.search(raw)
        if n:
            return f"https://blog.naver.com/{BLOG_ID}/{n.group(1)}"
    return ""


def parse_rss(content):
    """RSS 바이트 → [{title, link, raw}] (link 는 글 번호 주소 또는 '')."""
    root = ET.fromstring(content)
    items = []
    for node in root.iter("item"):
        title = unescape(node.findtext("title") or "")
        raw = (node.findtext("link") or "").strip()
        if title:
            items.append({"title": title, "link": post_url(raw), "raw": raw})
    return items


def fetch_rss():
    """beafather RSS 를 1회 읽는다(캐시 없음). 반환 [{title, link, raw}] 또는 None(읽기 실패)."""
    import requests
    try:
        resp = requests.get(f"https://rss.blog.naver.com/{BLOG_ID}.xml", timeout=10)
        resp.raise_for_status()
        return parse_rss(resp.content)
    except (requests.RequestException, ET.ParseError, ValueError):
        return None


def plan(rss, queue, articles, since=SINCE):
    """읽기만 해서 계획을 만든다(저장 안 함). 대상: 대기열 id 날짜가 since 이후 + 발행 주소 칸이 빈 글(이미 주소가 있으면 대상 아님).
    결과: 채움(RSS 에 같은 제목 글번호 주소가 정확히 1개) / 못 찾음(0개) / 겹침(2개 이상, 또는 이미 다른 글이 쓰는 주소·두 글이 같은 주소).
    행: {id, keyword, title, date, result, found, ref}."""
    by_title = {}
    for r in rss:
        if r.get("link"):
            by_title.setdefault(norm_title(r["title"]), []).append(r)
    used = {}
    for a in articles.values():
        u = ((a.get("platform_published_url") or {}).get("naver") or "").strip()
        if u:
            used[u] = used.get(u, 0) + 1
    rows, seen = [], set()
    for it in queue:
        aid = it.get("id")
        if aid in seen or str(aid)[:10] < since:
            continue
        seen.add(aid)
        art = articles.get(aid) or {}
        if ((art.get("platform_published_url") or {}).get("naver") or "").strip():
            continue  # 이미 주소가 있는 글은 절대 건드리지 않음
        title = ((art.get("platform_title") or {}).get("naver") or "").strip()
        date = str((it.get("plan") or {}).get("publish_date") or "").strip() or ((art.get("platform_published_at") or {}).get("naver") or "").strip()
        row = {"id": aid, "keyword": it.get("keyword", ""), "title": title, "date": date, "result": "", "found": "", "ref": ""}
        hits = by_title.get(norm_title(title), []) if title else []
        if len(hits) == 1:
            row["result"], row["found"], row["ref"] = "채움", hits[0]["link"], hits[0]["title"]
        elif len(hits) >= 2:
            row["result"], row["ref"] = "겹침", f"같은 제목 {len(hits)}개: " + ", ".join(h["link"] for h in hits)
        else:
            row["result"] = "못 찾음"
            if not title:
                row["ref"] = "앱에 원고(제목)가 없는 글"
            elif rss:
                nt = norm_title(title)
                best = max(rss, key=lambda r: difflib.SequenceMatcher(None, nt, norm_title(r["title"])).ratio())
                row["ref"] = "가장 비슷한 RSS 제목: " + best["title"]
        rows.append(row)
    claimed = {}
    for r in rows:
        if r["result"] == "채움":
            claimed[r["found"]] = claimed.get(r["found"], 0) + 1
    for r in rows:
        if r["result"] == "채움" and (claimed[r["found"]] > 1 or r["found"] in used):
            r["ref"] = f"{r['found']} (이미 다른 글이 쓰는 주소라 채우지 않음)"
            r["found"], r["result"] = "", "겹침"
    return rows


def apply(rows, path, read_text, write_json):
    """'채움' 행만 저장한다. 저장 전에 path(articles_naver_tips.json)를 '.backup_before_urlfill_날짜'로 백업.
    바꾸는 값은 platform_published_url.naver 하나뿐이고, 그 사이 주소가 채워진 글은 건드리지 않는다. 반환 (채운 개수, 백업 경로)."""
    todo = [r for r in rows if r["result"] == "채움"]
    if not todo:
        return 0, ""
    raw = read_text(path)
    data = json.loads(raw)
    base, ext = os.path.splitext(path)
    backup = f"{base}.backup_before_urlfill_{datetime.now():%Y%m%d}{ext}"
    if os.path.exists(backup):
        backup = f"{base}.backup_before_urlfill_{datetime.now():%Y%m%d_%H%M%S}{ext}"
    with open(backup, "w", encoding="utf-8", newline="") as f:
        f.write(raw)
    n = 0
    for r in todo:
        art = data.get(r["id"])
        if not art:
            continue
        urls = art.get("platform_published_url")
        if not isinstance(urls, dict):
            urls = art["platform_published_url"] = {}
        if (urls.get("naver") or "").strip():
            continue
        urls["naver"] = r["found"]
        n += 1
    write_json(path, data)
    return n, backup
