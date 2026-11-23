# -*- coding: utf-8 -*-
"""N살림노트(naver_tips) 글 장부: 글 1편 = 1줄 → 주간기획/_누적_살림/글장부.json + 글장부.csv

다시 돌리면 덮어쓰는 '파생 파일'이다. 앱 데이터(articles·대기열)·perf_inbox 는 읽기만 한다.
네이버·API·Claude 호출 없음. W혜택주머니 도구(lt_*.py)는 import 하지 않는다(살림노트 전용으로 새로 씀).
제목 틀만 tools/titles.py 의 pattern_of(제목 글자로 판정)를 그대로 쓴다.

칸: 앱 글 키 · 네이버 주소 · 발행일 · 키워드 · 확정 제목 · (발행 제목) · 제목 틀 · 유형 · 카테고리 · 갈래 · 출처 ·
    발행 때 검색량 · 추세 비율 · 정점까지 주 · 숍글 여부 · 1주째 · 2주째 · 3~4주째 조회수 · 유입 검색어
값이 없는 칸은 비운다(지어내지 않는다).

조회수 규칙: perf_inbox/<주 월요일>/posts.csv 의 '발행 뒤 몇 주째'(발행 주 = 1주째, 달력 주 기준 — 발행 요일에 따라
1주째는 며칠분일 수 있다). 3~4주째는 3주째·4주째 자료가 둘 다 있을 때만 합계를 적는다.

사용: python tools/salim_ledger.py            (기본 위치에 씀)
      python tools/salim_ledger.py --out-dir 시험폴더   (시험용)"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import titles  # noqa: E402  (pattern_of 만 사용)

OUT_DIR = os.path.join(ROOT, "주간기획", "_누적_살림")
ARTICLES = os.path.join(ROOT, "articles_naver_tips.json")
QUEUE = os.path.join(ROOT, "content_queue_naver_tips.json")
SHOP_ARTICLES = os.path.join(ROOT, "articles_shop_naver.json")
PERF = os.path.join(ROOT, "perf_inbox")
PLAN_GLOB = os.path.join(ROOT, "pipeline", "final", "plan_*.json")

COLS = ["앱 글 키", "네이버 주소", "발행일", "키워드", "확정 제목", "발행 제목", "제목 틀", "유형 A/B/C", "카테고리", "갈래(cluster_seed)",
        "출처", "발행 때 검색량", "추세 비율", "정점까지 주", "숍글 여부", "1주째 조회수", "2주째 조회수", "3~4주째 조회수",
        "유입 검색어 상위(어드바이저, 글당 최대 3, 있는 주만)", "상태"]


def rj(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def monday_of(d):
    return d - timedelta(days=d.weekday())


def num(s):
    s = str(s or "").replace(",", "").strip()
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


def post_id(url):
    m = re.search(r"/(\d{6,})\b", url or "")
    return m.group(1) if m else ""


def read_posts_csv():
    """{주 월요일: {앱 글 키: {views, week_n, channel, type, category}}}"""
    out = {}
    for f in sorted(glob.glob(os.path.join(PERF, "*", "posts.csv"))):
        wk = os.path.basename(os.path.dirname(f))
        rows = {}
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                k = (r.get("앱 글 키") or "").strip()
                if k:
                    rows[k] = {"views": num(r.get("조회수")) or 0, "week_n": num(r.get("발행 뒤 몇 주째")), "channel": r.get("채널"),
                               "type": r.get("글 유형"), "category": r.get("카테고리")}
        out[wk] = rows
    return out


def read_advisor():
    """{글번호: [(주, '검색어(횟수) / ...')]} — advisor_posts.csv 가 있는 주만"""
    out = {}
    for f in sorted(glob.glob(os.path.join(PERF, "*", "advisor_posts.csv"))):
        wk = os.path.basename(os.path.dirname(f))
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                pid = post_id(r.get("주소"))
                terms = ""
                for k, v in r.items():
                    if k and k.startswith("유입 검색어"):
                        terms = (v or "").strip()
                if pid and terms:
                    out.setdefault(pid, []).append((wk, terms))
    return out


def read_plans():
    """pipeline/final/plan_*.json 의 글별 r1(추세 비율·정점·검색량) → {(주, 키워드): r1}, {(주, 키워드): 확정 제목}"""
    r1s, tit = {}, {}
    for f in sorted(glob.glob(PLAN_GLOB)):
        if "backup" in os.path.basename(f):
            continue
        d = rj(f, {}) or {}
        wk = (d.get("week") or {}).get("start") or ""
        for p in d.get("plans") or []:
            kw = (p.get("keyword") or "").replace(" ", "")
            if p.get("r1"):
                r1s[(wk, kw)] = p["r1"]
            if p.get("final_title"):
                tit[(wk, kw)] = p["final_title"]
    return r1s, tit


def parse_reason(reason):
    """rec_reason 에서 '검색 추세 비율 X' · '정점까지 N주' 만 읽는다(다른 말투는 비움)."""
    ratio = peak = None
    m = re.search(r"검색 추세 비율\s*(-?\d+(?:\.\d+)?)", reason or "")
    if m:
        ratio = float(m.group(1))
    m = re.search(r"정점까지\s*(-?\d+)\s*주", reason or "")
    if m:
        peak = int(m.group(1))
    return ratio, peak


def source_label(src):
    if src == "weekly_plan":
        return "weekly_plan"
    if src == "target_keywords":
        return "r1c·target_keywords"
    return "그 전"


def build():
    arts = rj(ARTICLES, {}) or {}
    queue = {q["id"]: q for q in (rj(QUEUE, []) or [])}
    shop_arts = rj(SHOP_ARTICLES, {}) or {}
    perf = read_posts_csv()
    advisor = read_advisor()
    r1s, plan_titles = read_plans()
    weeks = sorted(perf)
    today = date.today()
    rows = []
    for aid, art in arts.items():
        url = (art.get("platform_published_url") or {}).get("naver") or ""
        if not url:
            continue
        pub = (art.get("platform_published_at") or {}).get("naver") or ""
        q = queue.get(aid) or {}
        plan = q.get("plan") or {}
        kw = q.get("keyword") or ""
        qweek = q.get("week") or ""
        ratio, peak = parse_reason(q.get("rec_reason"))
        r1 = r1s.get((qweek, kw.replace(" ", ""))) or {}
        if ratio is None and r1.get("ratio") is not None:
            ratio = r1["ratio"]
        if peak is None:
            serp = q.get("serp") or {}
            if serp.get("weeks_to_peak") is not None:
                peak = serp["weeks_to_peak"]
            elif r1.get("peak") is not None:
                peak = r1["peak"]
        volume = q.get("total_search_volume")
        if volume is None and r1.get("volume") is not None:
            volume = r1["volume"]
        pub_title = ((art.get("platform_title") or {}).get("naver") or "").strip()
        fixed = plan.get("final_title") or plan_titles.get((qweek, kw.replace(" ", ""))) or ""
        # 조회수: 발행 주(달력 주 월요일) 기준 n주째
        try:
            pd_ = date.fromisoformat(pub)
        except ValueError:
            pd_ = None
        wk_views = {}
        if pd_:
            pm = monday_of(pd_)
            for wk in weeks:
                n = (date.fromisoformat(wk) - pm).days // 7 + 1
                r = perf[wk].get(aid)
                if r is not None and n >= 1:
                    wk_views[n] = r["views"]
        v1, v2 = wk_views.get(1), wk_views.get(2)
        v34 = wk_views[3] + wk_views[4] if (3 in wk_views and 4 in wk_views) else None
        # 유형·카테고리
        ptype = (q.get("planned_type") or "").strip()
        cat = q.get("planned_category") or ""
        for wk in reversed(weeks):
            r = perf[wk].get(aid)
            if r:
                if not ptype:
                    m = re.match(r"^([ABC])\s", r.get("type") or "")
                    ptype = m.group(1) if m else ""
                if not cat:
                    cat = r.get("category") or ""
                break
        pid = post_id(url)
        adv = " | ".join(f"{wk}: {t}" for wk, t in advisor.get(pid, []))
        status = "예약(발행일이 오늘 이후)" if pd_ and pd_ > today else "발행"
        rows.append({
            "앱 글 키": aid, "네이버 주소": url, "발행일": pub, "키워드": kw, "확정 제목": fixed, "발행 제목": pub_title,
            "제목 틀": titles.pattern_of(pub_title) if pub_title else "", "유형 A/B/C": ptype, "카테고리": cat,
            "갈래(cluster_seed)": q.get("cluster_seed") or "", "출처": source_label(q.get("source")),
            "발행 때 검색량": volume if volume is not None else "", "추세 비율": ratio if ratio is not None else "",
            "정점까지 주": peak if peak is not None else "", "숍글 여부": "예" if art.get("is_shop_post") else "아니오",
            "1주째 조회수": v1 if v1 is not None else "", "2주째 조회수": v2 if v2 is not None else "",
            "3~4주째 조회수": v34 if v34 is not None else "", COLS[18]: adv, "상태": status,
            "_주": qweek, "_계획역할": plan.get("role") or "",
        })
    rows.sort(key=lambda r: (r["발행일"], r["앱 글 키"]))
    # 주별 조회 합계(장부 글 + 옛 숍 네이버 파일 글)
    totals = {}
    for wk in weeks:
        mine = sum(v["views"] for k, v in perf[wk].items() if k in arts)
        shop = sum(v["views"] for k, v in perf[wk].items() if k in shop_arts)
        other = sum(v["views"] for k, v in perf[wk].items() if k not in arts and k not in shop_arts)
        totals[wk] = {"장부 글(naver_tips) 조회": mine, "옛 숍 네이버 파일 글 조회": shop, "어느 파일에도 없는 키 조회": other,
                      "posts.csv 전체 조회": sum(v["views"] for v in perf[wk].values())}
    return rows, totals


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=OUT_DIR)
    a = ap.parse_args()
    rows, totals = build()
    os.makedirs(a.out_dir, exist_ok=True)
    pub_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    meta = {"만든 시각": datetime.now().isoformat(timespec="seconds"), "편수": len(rows), "칸": COLS, "주별 조회 합계": totals,
            "메모": ["다시 돌리면 덮어쓰는 파생 파일(앱 데이터는 읽기만).",
                     "발행 제목 = 앱 articles 의 네이버 제목. 제목 틀은 이 제목을 titles.pattern_of 로 판정.",
                     "추세 비율·정점까지 주: 대기열 rec_reason '검색 추세 비율 …, 정점까지 …주' → plan json r1 → serp 순. 다른 말투 rec_reason 은 비움.",
                     "유입 검색어: perf_inbox/*/advisor_posts.csv(글당 상위 3개만 들어 있음)가 있는 주만.",
                     "조회수 1주째 = 발행한 달력 주(월~일). perf_inbox 가 10/12 주부터라 그 전 주는 비어 있음."]}
    with open(os.path.join(a.out_dir, "글장부.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "rows": pub_rows}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(a.out_dir, "글장부.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(pub_rows)
    print(f"글장부 {len(rows)}편 → {a.out_dir}")
    for wk in sorted(totals):
        print(wk, totals[wk])


if __name__ == "__main__":
    main()
