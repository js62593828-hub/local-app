# -*- coding: utf-8 -*-
"""혜택주머니 실적 학습 루프 공통 부분(8차). 새 파일만 쓰고 앱 글·대기열은 읽기만 한다.

- 누적 폴더: 주간기획/_누적/  (perf_daily.json · queries/ · 글장부.json · 키워드은행.json · 주소맞춤.json)
- 서치콘솔 주소 ↔ 앱 글 맞추기, 옛 카테고리 이름 묶기, 발행일 읽기, 기간 합계.
"""
import json
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import unquote

import lt_common as C

ACC_DIR = os.environ.get("LT_ACC_DIR") or os.path.join(C.ROOT, "주간기획", "_누적")  # 시험할 땐 LT_ACC_DIR 로 다른 폴더
QUERY_DIR = os.path.join(ACC_DIR, "queries")
DAILY_PATH = os.path.join(ACC_DIR, "perf_daily.json")
LEDGER_PATH = os.path.join(ACC_DIR, "글장부.json")
BANK_PATH = os.path.join(ACC_DIR, "키워드은행.json")
ADDR_PATH = os.path.join(ACC_DIR, "주소맞춤.json")
MANUAL_ADDR_PATH = os.path.join(ACC_DIR, "주소맞춤_수동.json")  # 사람이 직접 적는 보정(있으면 가장 먼저 씀)
LEARN_DOC = os.path.join(C.ROOT, "docs", "혜택주머니", "실적학습.md")

# 옛 카테고리 이름 → 새 이름. 집계할 때만 쓰고 글 데이터는 고치지 않는다.
CAT_ALIAS = {"세금·연말정산": "세금·절세", "4대보험·연금": "보험·연금"}


def norm_cat(c):
    c = (c or "").strip()
    return CAT_ALIAS.get(c, c)


def ensure_dirs():
    os.makedirs(QUERY_DIR, exist_ok=True)


def key(s):
    """한글·영문·숫자만 남긴 비교용 문자열."""
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def norm_url(url):
    """perf_tracker.normalize_url 과 같은 규칙(퍼센트 해제·소문자·쿼리 제거·http·www·끝 슬래시 제거)."""
    s = unquote((url or "").strip()).lower()
    s = s.split("#", 1)[0].split("?", 1)[0]
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.rstrip("/")


# ───────── 앱 글 읽기(읽기만) ─────────

def load_articles_queue(app=None):
    app = app or C.load_app()
    cfg = app.CHANNELS[C.CHANNEL]
    articles = C.read_json(cfg["articles_file"], {})
    queue = C.read_json(cfg["queue_file"], [])
    return articles, {q.get("id"): q for q in queue if isinstance(q, dict)}


def wp(art, field):
    v = art.get(field)
    return (v.get("wordpress") if isinstance(v, dict) else None) or ""


def article_info(aid, art, q):
    """글 한 편의 기본 정보. 발행일은 워드프레스 발행일, 없으면 예약 칸(lt_plan) 날짜."""
    pub_d = wp(art, "platform_published_at")[:10]
    pub_t = wp(art, "platform_scheduled_time")
    plan = art.get("lt_plan") or {}
    if not pub_d and plan.get("scheduled_at"):
        pub_d = plan["scheduled_at"][:10]
        pub_t = pub_t or plan["scheduled_at"][11:16]
    title = wp(art, "platform_title").strip() or q.get("keyword") or aid
    return {
        "id": aid, "url": wp(art, "platform_published_url").strip(), "post_id": wp(art, "platform_post_id"),
        "pub": pub_d or None, "pub_time": pub_t or "", "title": title, "keyword": q.get("keyword") or "",
        "cluster": q.get("cluster_seed") or q.get("keyword") or "", "role": q.get("cluster_role") or q.get("cluster_role") or "",
        "week": q.get("week") or aid[:10], "retired": bool(art.get("retired")),
        "queue_category": q.get("category") or "",
    }


# ───────── 서치콘솔 주소 ↔ 글 맞추기 ─────────

def build_matcher(articles, queue):
    """서치콘솔 주소 하나 → (글 id, 맞춘 방법). 못 맞추면 (None, 이유).
    순서: 수동 보정 → 저장된 주소(퍼센트 인코딩·http·www·끝 슬래시 무시) → 글 번호(/98, ?p=98)
          → 슬러그와 제목 앞부분 일치(워드프레스가 긴 슬러그를 잘라 쓰고, 앱에는 주소가 안 남은 글)."""
    manual = C.read_json(MANUAL_ADDR_PATH, {}) or {}
    by_norm, by_pid, titles = {}, {}, []
    for aid, art in articles.items():
        info = article_info(aid, art, queue.get(aid) or {})
        if info["url"]:
            by_norm[norm_url(info["url"])] = aid
        if info["post_id"]:
            by_pid[str(info["post_id"])] = aid
        t = key(info["title"])
        if t:
            titles.append((t, aid))

    def match(raw):
        n = norm_url(raw)
        if n in manual:
            return manual[n], "수동 보정"
        if n in by_norm:
            return by_norm[n], "저장된 주소"
        path = n.split("/", 1)[1] if "/" in n else ""
        pm = re.fullmatch(r"(\d{1,6})", path)
        if pm and pm.group(1) in by_pid:
            return by_pid[pm.group(1)], "글 번호"
        if re.fullmatch(r"(?:category|tag|page)/.*", path) or path == "":
            return None, "글이 아닌 페이지(홈·분류·목록)"
        if pm:
            return None, "글 번호만 있는 주소(앱에 번호 없음)"
        slug = key(path)
        if len(slug) >= 8:
            cands = sorted({aid for t, aid in titles if t.startswith(slug) or slug.startswith(t)})
            if len(cands) == 1:
                return cands[0], "슬러그·제목 앞부분 일치"
            if len(cands) > 1:
                return None, "후보 여러 개(" + ",".join(cands[:3]) + ")"
        return None, "맞는 글 없음"
    return match


# ───────── 일별 실적 ─────────

def load_daily():
    d = C.read_json(DAILY_PATH, None)
    if not isinstance(d, dict):
        d = {"site": "", "updated": "", "pages": {}, "days": {}}
    d.setdefault("pages", {})
    d.setdefault("days", {})
    return d


def sum_rows(rows):
    """rows = [(노출, 클릭, ..., 순위), ...] (맨 뒤가 순위. 3칸·4칸 모두 가능) → (노출, 클릭, 클릭률, 평균순위(노출 가중))."""
    imp = sum(r[0] for r in rows)
    clk = sum(r[1] for r in rows)
    pos = (sum(r[0] * r[-1] for r in rows) / imp) if imp else 0.0
    return imp, clk, (clk / imp if imp else 0.0), pos


def page_rows(daily, norms, start, end):
    """norms(주소 묶음)의 start~end(포함) 일별 값 목록."""
    out = []
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while d <= e:
        day = daily["days"].get(d.isoformat())
        if day:
            for n in norms:
                v = day.get(n)
                if v:
                    out.append(tuple(v))
        d += timedelta(days=1)
    return out


def last_day(daily):
    return max(daily["days"]) if daily["days"] else None


def rank_band(pos):
    if pos <= 0:
        return None
    if pos <= 3.5:
        return "1~3위"
    if pos <= 7.5:
        return "4~7위"
    if pos <= 15.5:
        return "8~15위"
    return "16위+"


def pct(x):
    return f"{x * 100:.1f}%"
