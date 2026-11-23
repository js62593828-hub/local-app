# -*- coding: utf-8 -*-
"""N살림노트 성적표 → 주간기획/살림_<날짜>/01_성적표.md   (판정·결론은 쓰지 않는다. 숫자와 후보만.)

입력(읽기만): 주간기획/_누적_살림/글장부.json(salim_ledger.py 가 만듦) · perf_inbox/*/search_terms.csv · advisor_posts.csv · posts.csv ·
              pipeline/post_index.json
쓰는 파일: 01_성적표.md, 순위_원본.json(블로그 검색 API 결과 저장 — 다시 돌려도 API 를 또 쓰지 않음)
호출: 네이버 블로그 검색 API 만(③ 내 글 순위, 키워드 1개당 1회·display 100). 데이터랩·Claude 호출 없음.
W혜택주머니 도구(lt_*.py)는 import 하지 않는다.

  ① 묶음별 1주째·2주째 조회 가운데값과 편수(8편 미만 칸은 '참고')
  ② 다음 글감 후보(최근 2주 유입 검색어 중 추정 횟수 3 이상이고 우리 글 키워드에 그대로 없는 말)
  ③ 내 글 순위(최근 4주 발행 글 키워드마다 검색 1회, beafather 글의 순위)

사용: python tools/salim_learn.py --no-rank          (③ 은 저장된 순위가 있으면 그것으로, 없으면 빈 표 — API 0회)
      python tools/salim_learn.py --rank-limit 1     (API 1회만: 시험)
      python tools/salim_learn.py                    (③ 까지, API 최대 100회 안에서)"""
import argparse
import csv
import glob
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salim_ledger as L  # noqa: E402  (살림노트 전용 새 도구)

LEDGER = os.path.join(ROOT, "주간기획", "_누적_살림", "글장부.json")
PERF = os.path.join(ROOT, "perf_inbox")
POST_INDEX = os.path.join(ROOT, "pipeline", "post_index.json")
MIN_N = 8
API_LIMIT = 100
BLOG_ID = "beafather"


# ---------- 공통 ----------
def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def med_cell(vals):
    """'가운데값 (n=편수)', 8편 미만이면 '참고 …', 자료 없으면 '-'"""
    vals = [v for v in vals if isnum(v)]
    if not vals:
        return "-"
    m = statistics.median(vals)
    m = int(m) if float(m).is_integer() else round(m, 1)
    return (f"참고 {m} (n={len(vals)})" if len(vals) < MIN_N else f"{m} (n={len(vals)})")


def bucket_volume(v):
    if not isnum(v):
        return None
    return "1천 미만" if v < 1000 else ("1천~1만" if v < 10000 else "1만 이상")


def bucket_ratio(v):
    if not isnum(v):
        return None
    return "1.2 미만" if v < 1.2 else ("1.2~2" if v < 2 else "2 이상")


def bucket_peak(v):
    if not isnum(v):
        return None
    return "정점 지남(음수)" if v < 0 else ("0~3주" if v <= 3 else "4주 이상")


DIMS = [
    ("검색량(발행 때)", lambda r: bucket_volume(r["발행 때 검색량"]), ["1천 미만", "1천~1만", "1만 이상"]),
    ("추세 비율", lambda r: bucket_ratio(r["추세 비율"]), ["1.2 미만", "1.2~2", "2 이상"]),
    ("정점까지 주", lambda r: bucket_peak(r["정점까지 주"]), ["0~3주", "4주 이상", "정점 지남(음수)"]),
    ("유형", lambda r: r["유형 A/B/C"] or None, ["A", "B", "C"]),
    ("제목 틀", lambda r: r["제목 틀"] or None, None),
    ("카테고리", lambda r: r["카테고리"] or None, None),
    ("출처", lambda r: r["출처"] or None, ["weekly_plan", "r1c·target_keywords", "그 전"]),
]


def stats_section(rows):
    out = ["## ① 묶음별 조회수 가운데값", "",
           "- 값 = 가운데값(편수). 8편 미만 칸은 '참고'. 1주째 = 발행한 달력 주(월~일, 발행 요일에 따라 며칠분일 수 있음), 2주째 = 그 다음 주. "
           "2주째 칸은 2주째 주가 이미 끝나 수집된 글만 들어간다(perf_inbox 는 10/12 주~11/16 주).",
           "- 자료가 없는 칸(추세 비율·정점까지 주 등이 옛 글은 비어 있음)은 '값 없음' 줄로 따로 센다. 글 수가 적고 묶음끼리 섞여 있어 '원인'이 아니라 '함께 보이는 차이'다.", ""]
    out += ["| 묶음 | 구분 | 1주째 조회 | 2주째 조회 |", "|---|---|---|---|"]
    out.append(f"| 전체 | 모든 글 | {med_cell([r['1주째 조회수'] for r in rows])} | {med_cell([r['2주째 조회수'] for r in rows])} |")
    for name, fn, order in DIMS:
        groups = defaultdict(list)
        for r in rows:
            groups[fn(r)].append(r)
        keys = list(order or [])
        keys += sorted((k for k in groups if k is not None and k not in keys), key=lambda k: -len(groups[k]))
        for k in keys:
            g = groups.get(k, [])
            if not g:
                continue
            out.append(f"| {name} | {k} | {med_cell([r['1주째 조회수'] for r in g])} | {med_cell([r['2주째 조회수'] for r in g])} |")
        if None in groups:
            g = groups[None]
            out.append(f"| {name} | 값 없음 | {med_cell([r['1주째 조회수'] for r in g])} | {med_cell([r['2주째 조회수'] for r in g])} |")
    return out


# ---------- ② 다음 글감 후보 ----------
def norm(s):
    return re.sub(r"\s+", "", s or "").lower()


def load_recent_terms(weeks):
    tot = defaultdict(int)
    raw = {}
    for w in weeks:
        f = os.path.join(PERF, w, "search_terms.csv")
        if not os.path.isfile(f):
            continue
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if (r.get("구분") or "").strip() != "검색어":
                    continue
                t = (r.get("검색어") or "").strip()
                n = L.num(r.get("추정 횟수")) or 0
                if t:
                    tot[norm(t)] += n
                    raw.setdefault(norm(t), t)
    return tot, raw


def advisor_term_to_posts(rows):
    """advisor_posts.csv 의 글별 상위 3 검색어 → {정규화 검색어: [글 제목]}"""
    by_pid = {L.post_id(r["네이버 주소"]): r for r in rows}
    m = defaultdict(list)
    for pid, lst in L.read_advisor().items():
        r = by_pid.get(pid)
        if not r:
            continue
        for _wk, terms in lst:
            for part in terms.split(" / "):
                t = re.sub(r"\(\d+\)\s*$", "", part).strip()
                if t:
                    m[norm(t)].append(r["발행 제목"] or r["키워드"])
    return m


def near_posts(term, candidates, n=2):
    """검수기준서 5장 ③ 방식: 검색어의 2·3글자 조각이 기존 글(제목+원래 키워드, 공백 뺌)에 몇 개 들어 있나. 조각 2개 이상만."""
    kw = norm(term)
    grams = {kw[i:i + k] for k in (2, 3) for i in range(len(kw) - k + 1)}
    scored = []
    for title, text in candidates:
        s = sum(1 for g in grams if g in text)
        if s >= 2:
            scored.append((s, title))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _s, t in scored[:n]]


def candidates_section(rows):
    recent = sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(PERF, "*", "search_terms.csv")))[-2:]
    tot, raw = load_recent_terms(recent)
    have_kw = {norm(r["키워드"]) for r in rows}
    # 가까운 글 후보: 장부 글(발행 제목+키워드) + post_index 의 살림노트 블로그 글(제목+원래 키워드)
    cand, seen = [], set()
    for r in rows:
        t = r["발행 제목"] or r["키워드"]
        cand.append((t, norm(t + r["키워드"])))
        seen.add(norm(t))
    pi = L.rj(POST_INDEX, {}) or {}
    for t, x in (pi.get("posts") or {}).items():
        if x.get("source") in ("naver_blog", "content_queue_naver_tips.json") and norm(t) not in seen:
            cand.append((t, norm(t + (x.get("target_raw") or ""))))
            seen.add(norm(t))
    adv = advisor_term_to_posts(rows)
    items = [(t, n) for t, n in tot.items() if n >= 3 and t not in have_kw]
    items.sort(key=lambda x: (-x[1], x[0]))
    out = ["## ② 다음 글감 후보 (판정 없음)", "",
           f"- 자료: {', '.join(recent)} 주 search_terms.csv 의 '검색어' 줄(경로 합계, 두 주 합계). 추정 횟수 합 3 이상 · 우리 글 키워드(글장부 {len(rows)}편)에 띄어쓰기만 빼고 그대로 없는 말.",
           "- '들어온 우리 글'은 글별 유입 검색어 자료(advisor_posts.csv, 글당 상위 3개)에 그 말이 있을 때만 적는다. 없으면 '글별 자료 없음'(전체 검색어 표에는 글 정보가 없음).",
           "- '가장 가까운 기존 글'은 검수-판단-기준서 5장 ③ 방식(2·3글자 조각이 겹치는 개수, 2개 이상만)으로 뽑은 후보일 뿐이다. 물건의 다른 이름은 보지 않았다.", "",
           f"후보 {len(items)}개 (전체 검색어 {len(tot)}개 중)", "",
           "| 검색어 | 추정 횟수 | 들어온 우리 글 | 가장 가까운 기존 글 2개 |", "|---|---|---|---|"]
    for t, n in items:
        got = " / ".join(dict.fromkeys(adv.get(t, []))) or "글별 자료 없음"
        near = " / ".join(near_posts(raw[t], cand)) or "겹치는 글 없음"
        out.append(f"| {raw[t]} | {n} | {got} | {near} |")
    return out


# ---------- ③ 내 글 순위 ----------
def key_candidates():
    env = lambda k: os.environ.get(k, "").strip()  # noqa: E731
    out = []
    path = os.path.join(ROOT, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as f:
            for ln in f:
                if "=" in ln and not ln.lstrip().startswith("#"):
                    k, v = ln.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for kind, a, b in (("hub", "NAVER_HUB_CLIENT_ID", "NAVER_HUB_CLIENT_SECRET"),
                       ("devcenter", "NAVER_LOGIN_CLIENT_ID", "NAVER_LOGIN_CLIENT_SECRET")):
        if env(a) and env(b):
            out.append((kind, env(a), env(b)))
    return out


ENDPOINT = {"hub": "https://naverapihub.apigw.ntruss.com/search/v1/blog", "devcenter": "https://openapi.naver.com/v1/search/blog.json"}


class BlogSearch:
    def __init__(self, calls_so_far=0):
        import requests
        self.rq = requests
        self.cands = key_candidates()
        self.chosen = None
        self.calls = calls_so_far

    def search(self, query):
        order = [self.chosen] if self.chosen else self.cands
        last = ""
        for kind, cid, sec in order:
            if self.calls >= API_LIMIT:
                raise RuntimeError("API 100회 한도에 도달해 멈춤")
            hdr = ({"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec} if kind == "hub"
                   else {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec})
            self.calls += 1
            time.sleep(0.15)
            try:
                r = self.rq.get(ENDPOINT[kind], headers=hdr, params={"query": query, "display": 100, "start": 1, "sort": "sim"}, timeout=20)
            except self.rq.RequestException as e:
                last = f"{kind}: {type(e).__name__}"
                continue
            if r.status_code == 200:
                self.chosen = (kind, cid, sec)
                return r.json().get("items") or []
            last = f"{kind}: HTTP {r.status_code}"
        raise RuntimeError("검색 API 실패 — " + last)


def rank_of(items):
    """beafather 글 중 가장 높은 순위(1~100)와 그 글 주소. 없으면 (None, '')."""
    for i, it in enumerate(items, 1):
        link = (it.get("link") or "") + " " + (it.get("bloggerlink") or "")
        if f"blog.naver.com/{BLOG_ID}" in link or f"m.blog.naver.com/{BLOG_ID}" in link:
            return i, it.get("link") or ""
    return None, ""


def rank_section(rows, cache_path, do_rank, limit, today):
    start = (today - timedelta(days=28)).isoformat()
    recent = [r for r in rows if start <= r["발행일"] <= today.isoformat()]
    cache = L.rj(cache_path, {}) or {}
    res = cache.get("results") or {}
    calls = cache.get("calls_total", 0)
    new_calls = 0
    if do_rank:
        bs = BlogSearch(calls)
        todo = [r for r in recent if r["앱 글 키"] not in res]
        for r in todo[:limit]:
            items = bs.search(r["키워드"])
            rk, link = rank_of(items)
            res[r["앱 글 키"]] = {"keyword": r["키워드"], "rank": rk, "link": link, "items": len(items),
                                  "asked_at": datetime.now().isoformat(timespec="seconds")}
        new_calls = bs.calls - calls
        calls = bs.calls
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"calls_total": calls, "results": res}, f, ensure_ascii=False, indent=1)
    perf = L.read_posts_csv()
    cum = {r["앱 글 키"]: sum(perf[w][r["앱 글 키"]]["views"] for w in perf if r["앱 글 키"] in perf[w]) for r in recent}
    asked = [r for r in recent if r["앱 글 키"] in res]
    out = ["## ③ 내 글 순위 (최근 4주 발행 글)", "",
           f"- 대상: 발행일 {start} ~ {today.isoformat()} 글 {len(recent)}편 중 순위를 물어본 글 {len(asked)}편. 키워드(앱에 적힌 말 그대로)로 블로그 검색 1회(display 100, 정확도순), "
           f"beafather 글 중 가장 높은 순위. 없으면 '100위 밖'. 이번 실행 API {new_calls}회 · 누계 {calls}회.",
           "- '이 글인가' = 그 순위 글이 이 글 자신인지(주소 글번호 비교). 아니면 같은 블로그의 다른 글이 먼저 잡힌 것이다.",
           "- 조회수 = 장부 칸(1주째) 과 perf_inbox 에 잡힌 주들의 누계(발행 직후 며칠은 수집 주에 따라 빠질 수 있음).", "",
           "| 키워드 | 발행일 | 내 글 순위 | 이 글인가 | 1주째 조회 | 누계 조회 |", "|---|---|---|---|---|---|"]
    if not asked:
        out.append("| (순위를 아직 안 물어봄) | | | | | |")
    for r in sorted(asked, key=lambda r: (r["발행일"], r["키워드"]), reverse=True):
        x = res[r["앱 글 키"]]
        if x["rank"] is None:
            rk, same = "100위 밖", "-"
        else:
            rk = f"{x['rank']}위"
            same = "예" if L.post_id(x["link"]) == L.post_id(r["네이버 주소"]) else "다른 글"
        v1 = r["1주째 조회수"] if r["1주째 조회수"] != "" else "-"
        out.append(f"| {r['키워드']} | {r['발행일']} | {rk} | {same} | {v1} | {cum[r['앱 글 키']]} |")
    inside = [res[r["앱 글 키"]]["rank"] for r in asked if res[r["앱 글 키"]]["rank"]]
    if asked:
        out += ["", f"요약: 순위를 물어본 {len(asked)}편 중 100위 안 {len(inside)}편(10위 안 {sum(1 for v in inside if v <= 10)}편), 100위 밖 {len(asked) - len(inside)}편."]
    return out, new_calls, calls


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "주간기획", "살림_2026-11-23"))
    ap.add_argument("--no-rank", action="store_true", help="③ 순위 조회를 하지 않는다(API 0회)")
    ap.add_argument("--rank-limit", type=int, default=API_LIMIT, help="이번 실행에서 순위를 물어볼 글 수 상한")
    ap.add_argument("--today", default="")
    a = ap.parse_args()
    today = date.fromisoformat(a.today) if a.today else date.today()
    led = L.rj(LEDGER, None)
    if not led:
        raise SystemExit("글장부.json 이 없습니다. 먼저 python tools/salim_ledger.py 를 돌리세요.")
    rows = led["rows"]
    os.makedirs(a.out_dir, exist_ok=True)
    rank_lines, new_calls, calls = rank_section(rows, os.path.join(a.out_dir, "순위_원본.json"), not a.no_rank, a.rank_limit, today)
    md = ["# N살림노트 성적표", "",
          f"> 만든 날 {today.isoformat()} · 글장부 {len(rows)}편(앱 articles_naver_tips 중 네이버 주소가 있는 글, 숍글 {sum(1 for r in rows if r['숍글 여부'] == '예')}편 포함) · "
          "조회수 자료 perf_inbox 10/12 주~11/16 주. 이 문서는 숫자와 후보만 보여 주고 판정·결론은 쓰지 않는다.", ""]
    md += stats_section(rows) + [""] + candidates_section(rows) + [""] + rank_lines + [""]
    path = os.path.join(a.out_dir, "01_성적표.md")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(md))
    print("성적표:", path, "· 이번 실행 API", new_calls, "회 · 누계", calls, "회")


if __name__ == "__main__":
    main()
