# -*- coding: utf-8 -*-
"""주간 수집 2단계: perf_inbox/<주 시작일>/raw 를 csv 로 바꾸고 앱 장부와 읽기만 해서 연결한다.

- 앱 장부(articles_naver_tips.json, articles_shop_naver.json)는 '읽기만' 한다 (쓰기 없음).
- 결과는 perf_inbox/ 아래에만 쓴다.

사용법
  python tools/weekly_collect_join.py --all            # perf_inbox 안의 모든 주 + summary_4weeks.md
  python tools/weekly_collect_join.py --week 2026-10-26   # 한 주만 (지난주 비교용으로 앞 주 raw 가 있으면 함께 읽음)
"""
import argparse
import csv
import difflib
import json
import re
import statistics
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
INBOX = BASE / "perf_inbox"
LEDGERS = [
    ("articles_naver_tips.json", "네이버 살림"),
    ("articles_shop_naver.json", "옛 숍 네이버"),
]
MIN_SAMPLE = 5  # 표본이 이보다 적으면 "판단 보류"


# ----------------------------------------------------------------- xlsx 읽기 (표준 라이브러리만)
def read_xlsx_rows(path):
    """첫 시트를 [[셀문자열, ...], ...] 로 돌려준다. 빈 칸은 ''."""
    import html

    def unesc(s):
        return html.unescape(s)
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        t = z.read("xl/sharedStrings.xml").decode("utf-8")
        for si in re.findall(r"<si>(.*?)</si>", t, re.S):
            ss.append(unesc("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))))
    sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", n))[0]
    t = z.read(sheet).decode("utf-8")
    rows = []
    for row in re.findall(r"<row[ >].*?</row>", t, re.S):
        cells = {}
        for m in re.finditer(r"<c ([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
            attr, body = m.group(1), m.group(2) or ""
            ref = re.search(r'r="([A-Z]+)\d+"', attr)
            col = 0
            for ch in ref.group(1):
                col = col * 26 + ord(ch) - 64
            typ = re.search(r't="(\w+)"', attr)
            typ = typ.group(1) if typ else ""
            if typ == "s":
                v = re.search(r"<v>(.*?)</v>", body, re.S)
                val = ss[int(v.group(1))] if v else ""
            elif typ == "inlineStr":
                v = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
                val = unesc(v.group(1)) if v else ""
            else:
                v = re.search(r"<v>(.*?)</v>", body, re.S)
                val = unesc(v.group(1)) if v else ""
            cells[col] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(1, max(cells) + 1)])
        else:
            rows.append([])
    return rows


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------- 도우미
def norm(s):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", (s or "")).lower()


def to_int(s, default=0):
    try:
        return int(float(str(s).replace(",", "")))
    except Exception:
        return default


def monday_of(d):
    return d - timedelta(days=d.weekday())


def parse_date(s):
    m = re.search(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})", s or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


STOP = {"법", "방법", "하는", "하는법", "하는방법", "추천", "후기", "비교", "가격", "이유", "원인", "뭐", "어떻게",
        "있는", "없는", "좋은", "그", "것", "는", "이", "가", "의", "에", "을", "를", "도", "로", "와", "과", "총정리"}
SYN = {"씽크대": "싱크대", "씽크": "싱크", "쏭크대": "싱크대"}


def tokens(s):
    s = s or ""
    for a, b in SYN.items():
        s = s.replace(a, b)
    out = []
    for t in re.findall(r"[0-9A-Za-z가-힣]+", s):
        t = t.lower()
        if len(t) >= 2 and t not in STOP:
            out.append(t)
    return out


def tok_match(tok, text_norm):
    """토큰이 글 글자(공백 뺀 것)에 들어 있나. 4자 이상 토큰은 앞 3글자만 맞아도 인정(코세척기~코세척)."""
    if tok in text_norm:
        return True
    return len(tok) >= 4 and tok[:3] in text_norm


# ----------------------------------------------------------------- 앱 장부 읽기
def load_ledger():
    arts = []
    for fn, chan in LEDGERS:
        p = BASE / fn
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        for key, v in d.items():
            nav = lambda f: ((v.get(f) or {}).get("naver") or "")
            title = nav("platform_title")
            meta = nav("platform_metadata")
            typ = re.search(r"글\s*유형\s*[:：]\s*(.+)", meta)
            cat = re.search(r"카테고리\s*제안\s*[:：]\s*(.+)", meta)
            pub = parse_date((v.get("platform_published_at") or {}).get("naver"))
            url = (v.get("platform_published_url") or {}).get("naver") or ""
            body = nav("platform_final") + " " + nav("platform_raw")
            arts.append({
                "key": key, "channel": chan, "title": title, "ntitle": norm(title),
                "type": (typ.group(1).strip() if typ else "미기재"),
                "category": (cat.group(1).strip() if cat else ""),
                "pub": pub, "url": url,
                "logno": (re.search(r"/(\d{9,})$", url) or [None, ""])[1],
                "text_norm": norm(title + " " + re.sub(r"<[^>]+>", " ", body)),
                "title_norm": norm(title),
            })
    return arts


def match_post(title, written, arts):
    """네이버 통계 글 제목 -> 앱 글. (글, 방식) 반환."""
    nt = norm(title)
    for a in arts:
        if a["ntitle"] and a["ntitle"] == nt:
            return a, "제목일치"
    best, br = None, 0.0
    for a in arts:
        r = difflib.SequenceMatcher(None, nt, a["ntitle"]).ratio()
        if r > br:
            best, br = a, r
    if best and br >= 0.85:
        if written and best["pub"] and abs((written - best["pub"]).days) > 5:
            return None, "미연결"
        return best, "비슷한 제목(%.2f)" % br
    return None, "미연결"


# ----------------------------------------------------------------- 한 주 읽기
def read_rank(path):
    rows = read_xlsx_rows(path)
    hi = next(i for i, r in enumerate(rows) if r[:3] == ["순위", "제목", "조회수"])
    out = []
    for r in rows[hi + 1:]:
        if len(r) >= 3 and r[0].strip().isdigit():
            out.append({"rank": int(r[0]), "title": r[1], "views": to_int(r[2]),
                        "written": parse_date(r[3]) if len(r) > 3 else None})
    return out


def read_referer(path):
    rows = read_xlsx_rows(path)
    hi = next(i for i, r in enumerate(rows) if r[:1] == ["유입경로"])
    out = []
    for r in rows[hi + 1:]:
        if len(r) >= 4 and r[2].strip():
            out.append({"channel": r[0], "channel_pct": r[1], "term": r[2], "pct": float(r[3] or 0)})
    return out


def read_sales(path):
    rows = read_xlsx_rows(path)
    hi = next(i for i, r in enumerate(rows) if r[:1] == ["주문일시"])
    out = []
    for r in rows[hi + 1:]:
        if len(r) >= 10 and r[0].strip():
            out.append({"date": parse_date(r[0]), "cancel": r[1], "status": r[2], "store": r[3], "product": r[4],
                        "ptype": r[5], "option": r[6], "qty": to_int(r[7]), "amount": to_int(r[8]),
                        "profit": to_int(r[9]), "kind": r[10] if len(r) > 10 else ""})
    return out


def find_commerce_dir():
    cands = sorted(p for p in INBOX.glob("*/raw") if list(p.glob("sales_30d_*.xlsx")))
    return cands[-1] if cands else None


def build_week(monday, arts, commerce):
    raw = INBOX / monday.isoformat() / "raw"
    rk = raw / ("rank_weekly_%s.xlsx" % monday.isoformat())
    rf = raw / ("referer_weekly_%s.xlsx" % monday.isoformat())
    if not rk.exists():
        return None
    sunday = monday + timedelta(days=6)
    res = {"monday": monday, "posts": [], "terms": [], "orders": [], "clicks": []}
    ranked = read_rank(rk)
    res["xlsx_views_total"] = sum(p["views"] for p in ranked)
    seen_keys = set()
    for p in ranked:
        a, how = match_post(p["title"], p["written"], arts)
        eff = p["written"] or (a["pub"] if a else None)
        weeks_since = ((monday - monday_of(eff)).days // 7 + 1) if eff else ""
        if a:
            seen_keys.add(a["key"])
        res["posts"].append({
            "week": monday.isoformat(), "rank": p["rank"], "title": p["title"], "views": p["views"],
            "key": a["key"] if a else "미연결", "channel": a["channel"] if a else "",
            "type": a["type"] if a else "", "category": a["category"] if a else "",
            "pub": a["pub"].isoformat() if a and a["pub"] else "",
            "written": p["written"].isoformat() if p["written"] else "",
            "weeks_since": weeks_since, "how": how, "_art": a,
        })
    # 장부에는 있는데 이 주 순위에 없는 글(조회 0) — 평균을 정직하게 내기 위해 추가
    for a in arts:
        if a["key"] in seen_keys or not a["pub"] or a["pub"] > sunday:
            continue
        res["posts"].append({
            "week": monday.isoformat(), "rank": "", "title": a["title"], "views": 0,
            "key": a["key"], "channel": a["channel"], "type": a["type"], "category": a["category"],
            "pub": a["pub"].isoformat(), "written": "",
            "weeks_since": (monday - monday_of(a["pub"])).days // 7 + 1, "how": "장부 전용(조회 0)", "_art": a,
        })
    # 검색어
    if rf.exists():
        ref = read_referer(rf)
        pcts = [r["pct"] for r in ref if r["pct"] > 0]
        unit = min(pcts) if pcts else 1
        for r in ref:
            r["count"] = round(r["pct"] / unit) if unit else 0
        res["terms"] = ref
    # 주문 / 클릭
    if commerce:
        sales_files = sorted(commerce.glob("sales_30d_*.xlsx"))
        links_file = commerce / "links_list.csv"
        links = read_csv(links_file) if links_file.exists() else []
        link_by_name = {norm(l["상품명"]): l for l in links}
        if sales_files:
            for s in read_sales(sales_files[-1]):
                if s["date"] and monday <= s["date"] <= sunday:
                    lk = link_by_name.get(norm(s["product"]))
                    art, how = link_order_to_article(s["product"], lk, s["date"], arts)
                    res["orders"].append({
                        "week": monday.isoformat(), "date": s["date"].isoformat(), "store": s["store"],
                        "product": s["product"], "qty": s["qty"], "amount": s["amount"], "profit": s["profit"],
                        "kind": s["kind"], "status": s["status"],
                        "link_product_id": lk["상품ID"] if lk else "미연결",
                        "link_issued": lk["링크 발급일"] if lk else "",
                        "art_key": art["key"] if art else "미연결",
                        "art_title": art["title"] if art else "", "how": how,
                    })
        cf = commerce / "clicks_daily_chart.csv"
        if cf.exists():
            for line in cf.read_text(encoding="utf-8").splitlines():
                m = re.match(r"(\d{4}-\d\d-\d\d),(\d+)$", line.strip())
                if m and monday <= date.fromisoformat(m.group(1)) <= sunday:
                    res["clicks"].append((m.group(1), int(m.group(2))))
    return res


def link_order_to_article(product, link, order_date, arts):
    """주문 상품 -> 앱 글. 링크 주소/상품 ID 가 글에 있으면 '링크일치', 아니면 상품명 낱말로 '추정'."""
    pid = link["상품ID"] if link else ""
    if pid:
        for a in arts:
            if pid in a["text_norm"] or pid in a["url"]:
                return a, "링크일치"
    toks = tokens(product)
    toks = [t for t in toks if not re.fullmatch(r"\d+[a-z가-힣]{0,2}", t)]
    best, bs = None, 0.0
    for a in arts:
        if a["pub"] and a["pub"] > order_date:
            continue  # 주문보다 늦게 발행된 글은 후보 제외
        hit_title = [t for t in toks if tok_match(t, a["title_norm"])]
        hit_all = [t for t in toks if tok_match(t, a["text_norm"])]
        if not hit_title:
            continue
        score = len(hit_title) * 2 + len(hit_all) * 0.5
        if score > bs:
            best, bs = a, score
    if best and bs >= 3:
        return best, "상품명 추정"
    return None, "미연결"


# ----------------------------------------------------------------- 요약 계산
def stat_cells(vals):
    if len(vals) < MIN_SAMPLE:
        return "판단 보류(%d편)" % len(vals), "-", "-"
    return str(len(vals)), "%.1f" % statistics.mean(vals), "%.1f" % statistics.median(vals)


def type_table(posts, filt):
    by = defaultdict(list)
    for p in posts:
        if p["type"] == "" or not filt(p):
            continue
        by[p["type"]].append(p["views"])
    lines = ["| 글 유형 | 편수 | 평균 조회수 | 가운데값 |", "|---|---|---|---|"]
    allv = []
    for t in sorted(by):
        n, a, m = stat_cells(by[t])
        lines.append("| %s | %s | %s | %s |" % (t, n, a, m))
        allv += by[t]
    n, a, m = stat_cells(allv)
    lines.append("| **전체** | %s | %s | %s |" % (n, a, m))
    return lines


def is_real_term(term):
    """'기타' 합계 줄이나 사이트 주소(http...)는 검색어가 아니므로 묶음에서 뺀다."""
    t = (term or "").strip()
    return bool(t) and t != "기타" and not re.match(r"https?://", t)


def group_terms(terms):
    cnt = defaultdict(int)
    for t in terms:
        for tk in set(tokens(t["term"])):
            cnt[tk] += t["count"]
    groups = defaultdict(lambda: {"terms": [], "count": 0})
    for t in terms:
        tks = [x for x in set(tokens(t["term"]))]
        multi = [x for x in tks if sum(1 for u in terms if x in set(tokens(u["term"]))) >= 2]
        key = max(multi, key=lambda x: (cnt[x], len(x))) if multi else "(단독) " + t["term"]
        g = groups[key]
        g["terms"].append(t["term"])
        g["count"] += t["count"]
    return sorted(groups.items(), key=lambda kv: -kv[1]["count"])


def week_summary(res, prev, arts):
    mon = res["monday"]
    posts = res["posts"]
    ranked = [p for p in posts if p["how"] != "장부 전용(조회 0)"]
    L = []
    L.append("# 주간 수집 요약 — %s ~ %s" % (mon, mon + timedelta(days=6)))
    L.append("")
    L.append("## 1. 주 합계 (지난주 대비)")
    tot = sum(p["views"] for p in ranked)
    L.append("")
    L.append("| 항목 | 이번 주 | 지난주 | 증감 |")
    L.append("|---|---|---|---|")
    ptot = sum(p["views"] for p in prev["posts"] if p["how"] != "장부 전용(조회 0)") if prev else None
    L.append("| 블로그 조회수 합계 | %d | %s | %s |" % (tot, ptot if prev else "-", ("%+d" % (tot - ptot)) if prev else "-"))
    pn = len([p for p in prev["posts"] if p["how"] != "장부 전용(조회 0)"]) if prev else None
    L.append("| 조회 1 이상 글 수 | %d | %s | %s |" % (len(ranked), pn if prev else "-", ("%+d" % (len(ranked) - pn)) if prev else "-"))
    L.append("| 네이버 화면 합계와 비교 | 읽은 파일 합계 %d | | %s |" % (res["xlsx_views_total"], "일치" if res["xlsx_views_total"] == tot else "**불일치**"))
    if res["terms"]:
        L.append("| 검색어 수(파일) | %d | %s | |" % (len(res["terms"]), len(prev["terms"]) if prev and prev["terms"] else "-"))
    ck = sum(c for _, c in res["clicks"]) if res["clicks"] else None
    pk = sum(c for _, c in prev["clicks"]) if prev and prev["clicks"] else None
    L.append("| 쇼핑커넥트 클릭 | %s | %s | %s |" % (ck if ck is not None else "-", pk if pk is not None else "-",
                                                  ("%+d" % (ck - pk)) if ck is not None and pk is not None else "-"))
    oc = len([o for o in res["orders"] if o["qty"] > 0])
    L.append("| 쇼핑커넥트 주문(건) | %d | %s | |" % (oc, len([o for o in prev["orders"] if o["qty"] > 0]) if prev else "-"))
    L.append("| 쇼핑커넥트 예상 수익(원) | %d | %s | |" % (sum(o["profit"] for o in res["orders"]), sum(o["profit"] for o in prev["orders"]) if prev else "-"))
    L.append("")
    L.append("## 2. 글 유형별 (편수·평균·가운데값) — 표본 %d편 미만은 판단 보류" % MIN_SAMPLE)
    L.append("")
    L.append("조회가 없던 장부 글(조회 0)도 편수에 넣었습니다. 장부에 '글 유형:'이 없는 글은 '미기재'.")
    L.append("")
    L.append("**전체 (이 주까지 발행된 장부 글)**")
    L.append("")
    L += type_table(posts, lambda p: p["key"] != "미연결")
    L.append("")
    L.append("**발행 첫 주 글만**")
    L.append("")
    L += type_table(posts, lambda p: p["key"] != "미연결" and p["weeks_since"] == 1)
    L.append("")
    L.append("**발행 2주째 글만**")
    L.append("")
    L += type_table(posts, lambda p: p["key"] != "미연결" and p["weeks_since"] == 2)
    L.append("")
    L.append("## 3. 조회수가 오른·내린 글 상위 5 (지난주 대비)")
    L.append("")
    if prev:
        pv = {norm(p["title"]): p["views"] for p in prev["posts"]}
        diffs = []
        for p in ranked:
            before = pv.get(norm(p["title"]), 0)
            diffs.append((p["views"] - before, p, before))
        up = sorted([d for d in diffs if d[0] > 0], key=lambda x: -x[0])[:5]
        down = sorted([d for d in diffs if d[0] < 0], key=lambda x: x[0])[:5]
        L.append("오른 글")
        L.append("")
        L.append("| 제목 | 지난주 | 이번 주 | 증감 |")
        L.append("|---|---|---|---|")
        for d, p, b in up:
            L.append("| %s | %d | %d | %+d |" % (p["title"], b, p["views"], d))
        if not up:
            L.append("| (없음) | | | |")
        L.append("")
        L.append("내린 글")
        L.append("")
        L.append("| 제목 | 지난주 | 이번 주 | 증감 |")
        L.append("|---|---|---|---|")
        for d, p, b in down:
            L.append("| %s | %d | %d | %+d |" % (p["title"], b, p["views"], d))
        gone = [p for p in prev["posts"] if p["how"] != "장부 전용(조회 0)" and norm(p["title"]) not in {norm(q["title"]) for q in ranked}][:3]
        for p in gone:
            L.append("| %s | %d | 0(순위 밖) | %+d |" % (p["title"], p["views"], -p["views"]))
        if not down and not gone:
            L.append("| (없음) | | | |")
    else:
        L.append("지난주 자료가 없어 비교할 수 없습니다.")
    L.append("")
    L.append("## 4. 구매가 난 글")
    L.append("")
    if res["orders"]:
        L.append("| 주문일 | 상품 | 수량 | 금액 | 예상 수익 | 연결된 글 | 연결 방식 |")
        L.append("|---|---|---|---|---|---|---|")
        for o in res["orders"]:
            L.append("| %s | %s | %d | %d | %d | %s | %s |" % (o["date"], o["product"][:40], o["qty"], o["amount"], o["profit"],
                                                            o["art_title"] or "미연결", o["how"]))
        L.append("")
        L.append("연결 방식이 '상품명 추정'이면 글 속 구매링크가 비어 있어(장부에 [구매링크] 자리표시만 있음) 상품명 낱말로 맞춘 추정입니다.")
    else:
        L.append("이 주에는 주문이 없습니다.")
    L.append("")
    L.append("## 5. 검색어 묶음 상위 10 (횟수는 비율÷최소비율로 추정)")
    L.append("")
    real_terms = [t for t in res["terms"] if is_real_term(t["term"])]
    if real_terms:
        by_title = [(p["title"], p["views"]) for p in ranked if p["key"] != "미연결"]
        L.append("| 묶음 낱말 | 검색어 수 | 추정 횟수 | 대표 검색어 | 들어온 것으로 보이는 우리 글 |")
        L.append("|---|---|---|---|---|")
        for key, g in group_terms(real_terms)[:10]:
            tk = key.replace("(단독) ", "")
            ts = tokens(tk) if key.startswith("(단독)") else [key]
            cand = [(v, t) for t, v in by_title if any(tok_match(x, norm(t)) for x in ts)]
            cand.sort(reverse=True)
            titles = " / ".join("%s(%d)" % (t, v) for v, t in cand[:2]) or "(찾지 못함)"
            L.append("| %s | %d | %d | %s | %s |" % (key, len(g["terms"]), g["count"], ", ".join(g["terms"][:3]), titles))
        L.append("")
        L.append("같은 낱말이 둘 이상의 검색어에 들어 있으면 한 묶음으로 봤습니다(씽크대=싱크대). 글 연결은 제목에 같은 낱말이 있는 글을 조회수 순으로 붙인 추정입니다.")
    else:
        L.append("검색어 파일이 없습니다.")
    L.append("")
    L.append("## 6. 미연결 목록")
    L.append("")
    un = [p for p in ranked if p["key"] == "미연결"]
    L.append("**앱 장부에서 못 찾은 네이버 글 (%d편, 조회수 %d)**" % (len(un), sum(p["views"] for p in un)))
    L.append("")
    if un:
        L.append("| 순위 | 제목 | 조회수 | 작성일 |")
        L.append("|---|---|---|---|")
        for p in un:
            L.append("| %s | %s | %d | %s |" % (p["rank"], p["title"], p["views"], p["written"]))
    L.append("")
    uo = [o for o in res["orders"] if o["art_key"] == "미연결"]
    if not un:
        L.append("(없음)")
        L.append("")
    L.append("**글을 못 찾은 주문 (%d건)**" % len(uo))
    L.append("")
    for o in uo:
        L.append("- %s %s" % (o["date"], o["product"]))
    if not uo:
        L.append("(없음)")
    L.append("")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------- 파일 쓰기
def write_week(res, prev, arts):
    d = INBOX / res["monday"].isoformat()
    hdr = ["주", "순위", "제목", "조회수", "앱 글 키", "채널", "글 유형", "카테고리", "발행일(앱)", "작성일(네이버)", "발행 뒤 몇 주째", "연결 방식"]
    write_csv(d / "posts.csv", hdr, [[p["week"], p["rank"], p["title"], p["views"], p["key"], p["channel"], p["type"],
                                       p["category"], p["pub"], p["written"], p["weeks_since"], p["how"]] for p in res["posts"]])
    write_csv(d / "search_terms.csv", ["주", "유입경로", "검색어", "비율(%)", "추정 횟수", "구분"],
              [[res["monday"], t["channel"], t["term"], t["pct"], t["count"],
                "검색어" if is_real_term(t["term"]) else ("기타 합계" if t["term"].strip() == "기타" else "사이트 주소")]
               for t in res["terms"]])
    write_csv(d / "orders.csv", ["주", "주문일", "스토어", "상품명", "수량", "판매금액", "예상 수익", "실적 유형", "주문상태",
                                 "발급링크 상품ID", "링크 발급일", "앱 글 키", "앱 글 제목", "연결 방식"],
              [[o["week"], o["date"], o["store"], o["product"], o["qty"], o["amount"], o["profit"], o["kind"], o["status"],
                o["link_product_id"], o["link_issued"], o["art_key"], o["art_title"], o["how"]] for o in res["orders"]])
    write_csv(d / "clicks_daily.csv", ["날짜", "클릭 수(그래프 환산)"], res["clicks"])
    (d / "summary.md").write_text(week_summary(res, prev, arts), encoding="utf-8")


def summary_multi(results):
    L = ["# 주간 수집 합본 요약 (%d주)" % len(results), ""]
    L.append("대상 주: " + ", ".join(str(r["monday"]) for r in results))
    L.append("")
    L.append("## 주별 합계")
    L.append("")
    L.append("| 주 시작 | 블로그 조회수 | 조회 1 이상 글 | 파일 합계와 일치 | 클릭 | 주문(건) | 예상 수익(원) |")
    L.append("|---|---|---|---|---|---|---|")
    for r in results:
        ranked = [p for p in r["posts"] if p["how"] != "장부 전용(조회 0)"]
        tot = sum(p["views"] for p in ranked)
        L.append("| %s | %d | %d | %s | %s | %d | %d |" % (
            r["monday"], tot, len(ranked), "일치" if tot == r["xlsx_views_total"] else "**불일치**",
            sum(c for _, c in r["clicks"]) if r["clicks"] else "-",
            len([o for o in r["orders"] if o["qty"] > 0]), sum(o["profit"] for o in r["orders"])))
    allp = [p for r in results for p in r["posts"] if p["key"] != "미연결"]
    L.append("")
    L.append("## 글 유형별 — 모든 주의 (글×주) 관측 합침, 조회 0 포함")
    L.append("")
    L += type_table(allp, lambda p: True)
    L.append("")
    L.append("## 글 유형별 — 발행 첫 주 기준 (글이 발행된 그 주의 조회수)")
    L.append("")
    L += type_table(allp, lambda p: p["weeks_since"] == 1)
    L.append("")
    L.append("## 글 유형별 — 발행 2주째 기준")
    L.append("")
    L += type_table(allp, lambda p: p["weeks_since"] == 2)
    L.append("")
    L.append("## 글 유형별 — 발행 3주째 이후")
    L.append("")
    L += type_table(allp, lambda p: isinstance(p["weeks_since"], int) and p["weeks_since"] >= 3)
    L.append("")
    L.append("## 유형별 × 발행 주차 평균 조회수 (편수)")
    L.append("")
    types = sorted({p["type"] for p in allp if p["type"]})
    L.append("| 글 유형 | 1주째 | 2주째 | 3주째 이후 |")
    L.append("|---|---|---|---|")
    for t in types:
        cells = []
        for f in (lambda w: w == 1, lambda w: w == 2, lambda w: isinstance(w, int) and w >= 3):
            v = [p["views"] for p in allp if p["type"] == t and f(p["weeks_since"])]
            cells.append(("%.1f (%d편)" % (statistics.mean(v), len(v))) if len(v) >= MIN_SAMPLE else "판단 보류(%d편)" % len(v))
        L.append("| %s | %s |" % (t, " | ".join(cells)))
    L.append("")
    un = sum(1 for r in results for p in r["posts"] if p["key"] == "미연결")
    L.append("## 참고")
    L.append("")
    L.append("- 같은 글이 여러 주에 나오면 주마다 한 번씩 셉니다(글×주).")
    L.append("- '발행 첫 주'는 네이버 작성일(없으면 앱 발행일)이 속한 주를 1주째로 계산했습니다.")
    L.append("- 앱 장부에서 못 찾은 글×주 합계: %d (각 주 summary.md 6번 참고)." % un)
    L.append("- 표본이 %d편 미만인 칸은 '판단 보류'입니다." % MIN_SAMPLE)
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--week", help="주 시작일(월요일) YYYY-MM-DD")
    a = ap.parse_args()
    if not (a.all or a.week):
        ap.error("--all 또는 --week 필요")
    arts = load_ledger()
    commerce = find_commerce_dir()
    mondays = sorted(date.fromisoformat(p.parent.name) for p in INBOX.glob("*/raw") if re.fullmatch(r"\d{4}-\d\d-\d\d", p.parent.name))
    targets = mondays if a.all else [date.fromisoformat(a.week)]
    cache = {}
    def get(m):
        if m not in cache:
            cache[m] = build_week(m, arts, commerce)
        return cache[m]
    results = []
    for m in targets:
        res = get(m)
        if not res:
            print("raw 없음:", m)
            continue
        write_week(res, get(m - timedelta(days=7)), arts)
        ranked = [p for p in res["posts"] if p["how"] != "장부 전용(조회 0)"]
        tot = sum(p["views"] for p in ranked)
        print(m, "글", len(ranked), "조회수 합계", tot, "파일 합계", res["xlsx_views_total"], "일치" if tot == res["xlsx_views_total"] else "불일치",
              "| 미연결", sum(1 for p in ranked if p["key"] == "미연결"), "| 주문", len(res["orders"]), "| 검색어", len(res["terms"]))
        results.append(res)
    if a.all and results:
        (INBOX / "summary_4weeks.md").write_text(summary_multi(results), encoding="utf-8")
        print("summary_4weeks.md 작성 (%d주)" % len(results))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
