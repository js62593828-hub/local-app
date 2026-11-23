# -*- coding: utf-8 -*-
"""실적 분석(8차 개편): 서치콘솔을 읽기만 해서 일별 실적을 쌓고, 주간기획/혜택_날짜/01_실적분석.md 를 만든다.

받는 것(모두 읽기):
  - page×date  일별 → 주간기획/_누적/perf_daily.json  (날짜별로 합침. 같은 날짜는 새 값으로 교체, 덮어쓰기 없음. 첫 실행 땐 받을 수 있는 과거 전부)
  - page×query 28일 전부 → 주간기획/_누적/queries/<주 월요일>.json  (주마다 1개)
  - (예전처럼) 앱 성과 화면용 28일 저장본 perf_cache.json 도 새로 받는다. 끄려면 --skip-app-cache.
01_실적분석.md 는 숫자 표만 쓴다(판단 문장 없음). 글 데이터(articles·queue)와 워드프레스는 읽기만 한다.
시험할 때는 환경변수 LT_ACC_DIR 로 누적 폴더를 다른 곳으로 돌릴 수 있다."""
import argparse
import os
from datetime import date, datetime, timedelta
from urllib.parse import unquote

import lt_common as C
import lt_data as D


def fetch_daily_and_queries(pt, week, daily, first_days_back=480):
    """서치콘솔에서 일별·검색어 자료를 받아 daily 에 합친다. 반환: (daily, 메모, 검색어 파일 경로)."""
    site = pt.site_urls().get("longterm")
    if not site:
        raise SystemExit("GSC_SITE_LONGTERM 이 .env 에 없습니다.")
    session = pt._new_session()  # 읽기 전용 세션
    end = date.today() - timedelta(days=pt.DATA_LAG_DAYS)
    last = D.last_day(daily)
    if last:
        start = max(date.fromisoformat(last) - timedelta(days=10), end - timedelta(days=first_days_back))
        mode = f"이어 받기(마지막 저장일 {last} 10일 전부터)"
    else:
        start = end - timedelta(days=first_days_back)
        mode = "첫 실행: 받을 수 있는 과거 전부"
    rows = pt._query_rows(session, site, start.isoformat(), end.isoformat(), ["page", "date"])
    new_days = {}
    for r in rows:
        url, day = r["keys"][0], r["keys"][1]
        n = D.norm_url(url)
        daily["pages"].setdefault(n, url)
        e = new_days.setdefault(day, {}).setdefault(n, [0, 0, 0.0])
        imp = int(r.get("impressions", 0))
        e[0] += imp
        e[1] += int(r.get("clicks", 0))
        e[2] += float(r.get("position", 0)) * imp  # 합친 뒤 노출로 나눔
    for day, pages in new_days.items():
        for n, e in pages.items():
            e[2] = round(e[2] / e[0], 2) if e[0] else 0.0
        daily["days"][day] = pages  # 같은 날짜는 새 값으로 교체
    daily["site"] = site
    daily["updated"] = datetime.now().isoformat(timespec="seconds")
    q_start = end - timedelta(days=pt.WINDOW_DAYS - 1)
    qrows = pt._query_rows(session, site, q_start.isoformat(), end.isoformat(), ["page", "query"])
    D.ensure_dirs()
    qpath = os.path.join(D.QUERY_DIR, f"{week}.json")
    C.write_json(qpath, {
        "week": week, "site": site, "range": [q_start.isoformat(), end.isoformat()],
        "fetched_at": daily["updated"], "columns": ["page", "query", "impressions", "clicks", "position"],
        "rows": [[r["keys"][0], r["keys"][1], int(r.get("impressions", 0)), int(r.get("clicks", 0)),
                  round(float(r.get("position", 0)), 2)] for r in qrows],
    })
    memo = (f"일별 {mode} · 받은 기간 {start} ~ {end} · 일별 줄 {len(rows)}개 · 날짜 {len(new_days)}일 · "
            f"검색어 28일 {len(qrows)}줄")
    return daily, memo, qpath


def save_daily(daily):
    D.ensure_dirs()
    if os.path.exists(D.DAILY_PATH):
        import shutil
        shutil.copy2(D.DAILY_PATH, D.DAILY_PATH + ".bak")  # 바로 전 판 하나만 남김(덮어쓰기 사고 방지)
    C.write_json(D.DAILY_PATH, daily)


def match_all(daily, cache_pages, articles, queue):
    """모든 서치콘솔 주소를 글에 맞춘다. 반환: (글→주소 묶음, 맞춤 결과 목록, 못 맞춘 목록)."""
    matcher = D.build_matcher(articles, queue)
    raw = dict(daily["pages"])
    for n, p in (cache_pages or {}).items():
        raw.setdefault(n, p.get("url") or n)
    art_norms, matched, unmatched = {}, {}, []
    imp28 = {}
    last = D.last_day(daily)
    if last:
        s = (date.fromisoformat(last) - timedelta(days=27)).isoformat()
        for day, pages in daily["days"].items():
            if s <= day <= last:
                for n, v in pages.items():
                    imp28[n] = imp28.get(n, 0) + v[0]
    for n, url in raw.items():
        aid, how = matcher(url)
        if aid:
            art_norms.setdefault(aid, set()).add(n)
            matched[n] = {"id": aid, "how": how}
        else:
            unmatched.append({"url": unquote(url), "norm": n, "impressions_28d": imp28.get(n, 0), "reason": how})
    unmatched.sort(key=lambda x: -x["impressions_28d"])
    return art_norms, matched, unmatched


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", required=True, help="월요일 YYYY-MM-DD")
    ap.add_argument("--no-fetch", action="store_true", help="서치콘솔을 새로 받지 않고 저장본으로만 만든다")
    ap.add_argument("--skip-app-cache", action="store_true", help="앱 성과 화면용 28일 저장본(perf_cache.json)은 새로 받지 않는다")
    ap.add_argument("--out", default="", help="시험용: 01_실적분석.md 를 이 파일로 쓴다")
    a = ap.parse_args()
    week = C.check_week(a.week)
    out_dir = C.week_dir(week)
    app = C.load_app()
    pt = app.perf_tracker
    D.ensure_dirs()
    daily = D.load_daily()

    notes = []
    if a.no_fetch:
        notes.append("서치콘솔을 새로 받지 않았습니다(--no-fetch).")
    else:
        try:
            daily, memo, qpath = fetch_daily_and_queries(pt, week, daily)
            save_daily(daily)
            notes.append("새로 받음 — " + memo)
            notes.append("검색어 전체 저장: " + (os.path.relpath(qpath, C.ROOT) if os.path.splitdrive(qpath)[0] == os.path.splitdrive(C.ROOT)[0] else qpath))
        except Exception as e:  # noqa: BLE001
            notes.append(f"일별 받기 실패, 저장본으로 계속 — {type(e).__name__}: {e}"[:220])
        if not a.skip_app_cache:
            try:
                ok, msg = pt.fetch_all(app._perf_specs())
                notes.append(("앱 28일 저장본 새로 받음 — " if ok else "앱 28일 저장본 받기 실패 — ") + msg)
            except Exception as e:  # noqa: BLE001
                notes.append(f"앱 28일 저장본 받기 실패 — {type(e).__name__}: {e}"[:200])
    if not daily["days"]:
        raise SystemExit("perf_daily.json 이 비어 있고 새로 받기도 실패했습니다.")

    cache = pt.load_cache() or {}
    cpages = ((cache.get("sites") or {}).get("longterm") or {}).get("pages") or {}
    articles, queue = D.load_articles_queue(app)
    art_norms, matched, unmatched = match_all(daily, cpages, articles, queue)

    # 주소 맞춤 결과 저장(새 파일). 저장된 주소 그대로인 것은 줄이고, 고친 것·못 맞춘 것만 남긴다.
    fixed = {n: v for n, v in matched.items() if v["how"] != "저장된 주소"}
    C.write_json(D.ADDR_PATH, {"updated": datetime.now().isoformat(timespec="seconds"),
                              "matched_exact": sum(1 for v in matched.values() if v["how"] == "저장된 주소"),
                              "fixed": fixed, "unmatched": unmatched})

    last = D.last_day(daily)
    first = min(daily["days"])
    now = datetime.now()
    infos = {aid: D.article_info(aid, art, queue.get(aid) or {}) for aid, art in articles.items()}

    def window(aid, s, e):
        return D.sum_rows(D.page_rows(daily, art_norms.get(aid, ()), s, e))

    # ① 최근 7일 vs 그 전 7일
    end_d = date.fromisoformat(last)
    c0, c1 = (end_d - timedelta(days=6)).isoformat(), last
    p0, p1 = (end_d - timedelta(days=13)).isoformat(), (end_d - timedelta(days=7)).isoformat()
    all_norms = list(daily["pages"])
    app_norms = [n for ns in art_norms.values() for n in ns]
    def tot(norms, s, e):
        return D.sum_rows(D.page_rows(daily, norms, s, e))
    t_cur, t_prev = tot(all_norms, c0, c1), tot(all_norms, p0, p1)
    a_cur, a_prev = tot(app_norms, c0, c1), tot(app_norms, p0, p1)

    def trow(label, c, p):
        return f"| {label} | {c[0]} ({p[0]}) | {c[1]} ({p[1]}) | {D.pct(c[2])} ({D.pct(p[2])}) | {c[3]:.1f} ({p[3]:.1f}) |"

    # ② 글마다 발행 후 7·14·28일
    live = []
    for aid, i in infos.items():
        if not i["pub"] or i["retired"]:
            continue
        pub_d = date.fromisoformat(i["pub"])
        if pub_d > end_d + timedelta(days=2):  # 아직 공개 전(예약)
            continue
        live.append(i)
    live.sort(key=lambda i: (i["pub"], i["id"]))

    def cell(i, n):
        s = date.fromisoformat(i["pub"])
        e = s + timedelta(days=n - 1)
        if e > end_d:
            return "", ""
        imp, clk, _ctr, pos = window(i["id"], s.isoformat(), e.isoformat())
        return f"{imp} / {clk}", (f"{pos:.1f}" if imp else "-")

    ml = ["| 글 | 발행일 | 묶음·역할 | 7일 노출/클릭 | 7일 순위 | 14일 노출/클릭 | 14일 순위 | 28일 노출/클릭 | 28일 순위 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i in live:
        c7, r7 = cell(i, 7)
        c14, r14 = cell(i, 14)
        c28, r28 = cell(i, 28)
        ml.append(f"| {i['title']} | {i['pub']} | {i['cluster']}·{i['role'] or '-'} | {c7} | {r7} | {c14} | {r14} | {c28} | {r28} |")

    # ③ 지난주 발행 글: 색인 여부·첫 노출일만
    mon = date.fromisoformat(week)
    lw0, lw1 = (mon - timedelta(days=7)).isoformat(), (mon - timedelta(days=1)).isoformat()
    idx = pt.load_index()
    lw = []
    for i in live:
        if lw0 <= i["pub"] <= lw1:
            ent = idx.get(f"{C.CHANNEL}:{i['id']}")
            ci = pt.classify_index(ent, i["url"]) if (ent and i["url"]) else None
            first_imp = ""
            for day in sorted(daily["days"]):
                if day >= i["pub"] and any((daily["days"][day].get(n) or [0])[0] > 0 for n in art_norms.get(i["id"], ())):
                    first_imp = day
                    break
            lw.append(f"| {i['title']} | {i['pub']} | {(ci['icon'] + ' ' + ci['label'] + ' (확인 ' + (ci['checked_at'][:10] or '?') + ')') if ci else '확인 전'} | {first_imp or '아직 없음'} |")

    # 기존 표(28일 기준) — 글마다 맞춘 주소로 계산
    def win28(aid):
        e28 = end_d
        cur = D.sum_rows(D.page_rows(daily, art_norms.get(aid, ()), (e28 - timedelta(days=27)).isoformat(), e28.isoformat()))
        return cur

    rows = []
    for i in live:
        cur = win28(i["id"])
        art = articles[i["id"]]
        memo = wp(art, "platform_metadata")
        cat = ""
        try:
            import metadata_parser
            cat = metadata_parser.parse_metadata_memo(memo).get("category") or ""
        except Exception:  # noqa: BLE001
            pass
        cat = D.norm_cat(cat or i["queue_category"])
        qs = []
        for n in art_norms.get(i["id"], ()):
            qs += [x["query"] for x in (cpages.get(n, {}).get("queries") or [])[:3]]
        age = (now.date() - date.fromisoformat(i["pub"])).days
        rows.append({**i, "cur": cur, "cat": cat, "age": age, "queries": qs[:3]})

    def line(r):
        c = r["cur"]
        return (f"| {r['title']} | {unquote(r['url']) or '(앱에 주소 없음)'} | {r['pub']} | {r['cluster']} | {r['role'] or '-'} | {r['cat'] or '-'} | "
                f"{c[0]} | {c[1]} | {D.pct(c[2])} | {c[3]:.1f} | {', '.join(r['queries']) or '-'} |")
    head = ("| 제목 | 주소 | 발행일 | 묶음 | 역할 | 카테고리 | 노출 | 클릭 | 클릭률 | 순위 | 상위 검색어 3개 |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|")

    def table(title, items, note=""):
        s = [f"## {title} ({len(items)}편)"]
        if note:
            s.append(note)
        s.append("")
        s += ([head] + [line(r) for r in items]) if items else ["(해당 없음)"]
        return "\n".join(s) + "\n"

    top = sorted(rows, key=lambda r: (-r["cur"][1], -r["cur"][0]))[:10]
    low_ctr = sorted([r for r in rows if r["cur"][0] >= 100 and r["cur"][2] < 0.02], key=lambda r: -r["cur"][0])
    mid_rank = sorted([r for r in rows if r["cur"][0] > 0 and 8 <= r["cur"][3] <= 30], key=lambda r: r["cur"][3])
    zero = sorted([r for r in rows if r["age"] >= 14 and r["cur"][0] == 0], key=lambda r: -r["age"])

    off_imp = sum(u["impressions_28d"] for u in unmatched)
    md = [f"# 01 실적 분석 — {week} 주 (혜택주머니)", "",
          f"- 자료 날짜: 일별 저장본 {first} ~ {last} (서치콘솔은 2~3일 늦게 확정돼 마지막 이틀은 다음에 새 값으로 바뀜) · 저장 {daily.get('updated', '?')}",
          *[f"- 받기 결과: {n}" for n in notes],
          f"- 만든 시각: {now.strftime('%Y-%m-%d %H:%M')} · 공개된 글 {len(live)}편(합쳐서 뺀 글 제외) · 예약 중인 글은 표에서 뺌",
          f"- 서치콘솔 주소 맞추기: 글에 맞춘 주소 {len(matched)}개(이 중 주소를 고쳐 맞춘 것 {len(fixed)}개) · 못 맞춘 주소 {len(unmatched)}개(28일 노출 {off_imp}) → `주간기획/_누적/주소맞춤.json`", "",
          f"## ① 최근 7일 vs 그 전 7일 ({c0}~{c1} vs {p0}~{p1}) — 괄호는 그 전 7일", "",
          "| 구분 | 노출 | 클릭 | 클릭률 | 평균 순위 |", "|---|---|---|---|---|",
          trow("사이트 전체", t_cur, t_prev), trow("앱 글만(주소 맞춘 글)", a_cur, a_prev), "",
          "## ② 글마다 발행 후 7·14·28일 (노출/클릭, 평균 순위) — 아직 안 된 칸은 비움", "",
          *ml, "",
          f"## ③ 지난주({lw0}~{lw1}) 발행 글 — 색인 여부·첫 노출일만 (⚠ 성적 판단 금지: 발행 7일도 안 됨)", "",
          "| 글 | 발행일 | 색인(저장된 검사 결과) | 첫 노출일 |", "|---|---|---|---|",
          *(lw or ["| (없음) | | | |"]), "",
          f"## (참고) 28일 합계 — 앱 글 {len(rows)}편", ""]
    c28 = D.sum_rows([r["cur"] for r in rows])
    md += [f"- 노출 {c28[0]} · 클릭 {c28[1]} · 클릭률 {D.pct(c28[2])} · 평균 순위 {c28[3]:.1f}", "",
           table("잘된 글 상위 10 (클릭순, 28일)", top),
           table("노출 100+ 인데 클릭률 2% 미만", low_ctr),
           table("순위 8~30위", mid_rank),
           table("발행 14일 지났는데 노출 0", zero)]
    path = a.out or os.path.join(out_dir, "01_실적분석.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"만들었습니다: {path}")
    for n in notes:
        print("받기:", n)
    print(f"일별 자료 {first} ~ {last} · 합계(7일) 노출 {t_cur[0]}(그 전 {t_prev[0]}) 클릭 {t_cur[1]}({t_prev[1]}) · 공개 글 {len(live)}편")
    print(f"주소 맞춤: 고친 것 {len(fixed)}개 · 못 맞춘 것 {len(unmatched)}개 (28일 노출 {off_imp})")


def wp(art, field):
    return D.wp(art, field)


if __name__ == "__main__":
    main()
