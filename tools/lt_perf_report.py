# -*- coding: utf-8 -*-
"""실적 분석: 서치콘솔을 새로 받아(앱 /performance/fetch 와 같은 함수) 주간기획/혜택_날짜/01_실적분석.md 를 만든다.
숫자 표만 쓰고 판단 문장은 쓰지 않는다. 받기에 실패하면 기존 perf_cache.json 으로 계속하고 맨 위에 자료 날짜를 적는다."""
import argparse
import os
from datetime import datetime, date

from urllib.parse import unquote

import lt_common as C


def pct(x):
    return f"{x * 100:.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", required=True, help="월요일 YYYY-MM-DD")
    ap.add_argument("--no-fetch", action="store_true", help="서치콘솔을 새로 받지 않고 저장본으로만 만든다")
    a = ap.parse_args()
    week = C.check_week(a.week)
    out_dir = C.week_dir(week)
    app = C.load_app()
    pt = app.perf_tracker

    fetch_note = "서치콘솔을 새로 받지 않았습니다(--no-fetch)."
    if not a.no_fetch:
        try:
            ok, msg = pt.fetch_all(app._perf_specs())
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"{type(e).__name__}: {e}"[:200]
        fetch_note = ("새로 받음 — " if ok else "받기 실패, 저장본으로 계속 — ") + msg
    cache = pt.load_cache()
    if not cache:
        raise SystemExit("perf_cache.json 이 없고 새로 받기도 실패했습니다.")
    fetched = cache.get("fetched_at", "?")
    rng = cache.get("ranges", {})

    site = (cache.get("sites") or {}).get("longterm") or {}
    pages = site.get("pages") or {}

    articles = C.read_json(app.CHANNELS[C.CHANNEL]["articles_file"], {})
    queue = C.read_json(app.CHANNELS[C.CHANNEL]["queue_file"], [])
    qmap = {q.get("id"): q for q in queue if isinstance(q, dict)}
    import metadata_parser
    now = datetime.now()

    rows = []
    for aid, art in articles.items():
        url = ((art.get("platform_published_url") or {}).get("wordpress") or "").strip()
        if not url:
            continue
        q = qmap.get(aid) or {}
        pub_d = ((art.get("platform_published_at") or {}).get("wordpress") or "")[:10]
        pub_t = (art.get("platform_scheduled_time") or {}).get("wordpress")
        dt = app._publish_dt(pub_d, pub_t)
        scheduled = bool(dt and dt > now)
        memo = (art.get("platform_metadata") or {}).get("wordpress") or ""
        try:
            cat = metadata_parser.parse_metadata_memo(memo).get("category") or ""
        except Exception:  # noqa: BLE001
            cat = ""
        cat = cat or q.get("category") or ""
        title = ((art.get("platform_title") or {}).get("wordpress") or "").strip() or q.get("keyword") or aid
        pg = pages.get(pt.normalize_url(url)) or {}
        cur = pg.get("cur") or {"impressions": 0, "clicks": 0, "ctr": 0.0, "position": 0.0}
        prev = pg.get("prev") or {"impressions": 0, "clicks": 0, "ctr": 0.0, "position": 0.0}
        age = (now.date() - date.fromisoformat(pub_d)).days if pub_d else None
        rows.append({
            "id": aid, "title": title, "url": url, "pub": pub_d, "age": age, "scheduled": scheduled,
            "cluster": q.get("cluster_seed") or q.get("keyword") or "", "role": q.get("cluster_role") or "",
            "cat": cat, "cur": cur, "prev": prev, "queries": [x["query"] for x in (pg.get("queries") or [])[:3]],
        })

    def tot(key, f):
        return sum(p[key][f] for p in pages.values())
    ci, cc = tot("cur", "impressions"), tot("cur", "clicks")
    pi, pc = tot("prev", "impressions"), tot("prev", "clicks")

    live = [r for r in rows if not r["scheduled"]]
    sched = [r for r in rows if r["scheduled"]]

    def line(r):
        c = r["cur"]
        return (f"| {r['title']} | {unquote(r['url'])} | {r['pub'] or '-'} | {r['cluster']} | {r['role'] or '-'} | {r['cat'] or '-'} | "
                f"{c['impressions']} | {c['clicks']} | {pct(c['ctr'])} | {c['position']:.1f} | {', '.join(r['queries']) or '-'} |")
    head = ("| 제목 | 주소 | 발행일 | 묶음 | 역할 | 카테고리 | 노출 | 클릭 | 클릭률 | 순위 | 상위 검색어 3개 |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|")

    def table(title, items, note=""):
        s = [f"## {title} ({len(items)}편)"]
        if note:
            s.append(note)
        s.append("")
        if items:
            s += [head] + [line(r) for r in items]
        else:
            s.append("(해당 없음)")
        return "\n".join(s) + "\n"

    top = sorted(live, key=lambda r: (-r["cur"]["clicks"], -r["cur"]["impressions"]))[:10]
    low_ctr = sorted([r for r in live if r["cur"]["impressions"] >= 100 and r["cur"]["ctr"] < 0.02],
                     key=lambda r: -r["cur"]["impressions"])
    mid_rank = sorted([r for r in live if r["cur"]["impressions"] > 0 and 8 <= r["cur"]["position"] <= 30],
                      key=lambda r: r["cur"]["position"])
    zero = sorted([r for r in live if r["age"] is not None and r["age"] >= 14 and r["cur"]["impressions"] == 0],
                  key=lambda r: -r["age"])

    md = [f"# 01 실적 분석 — {week} 주 (혜택주머니)", "",
          f"- 자료 날짜: 서치콘솔 저장 시각 {fetched} / 이번 28일 {' ~ '.join(rng.get('cur', []))} / 이전 28일 {' ~ '.join(rng.get('prev', []))}",
          f"- 받기 결과: {fetch_note}",
          f"- 만든 시각: {now.strftime('%Y-%m-%d %H:%M')} · 발행 글 {len(live)}편 · 예약 중 {len(sched)}편(갱신 후보에서 뺌)", "",
          "## 전체 합계 (이번 28일 vs 이전 28일)", "",
          "| 구분 | 노출 | 클릭 | 클릭률 |", "|---|---|---|---|",
          f"| 이번 28일 | {ci} | {cc} | {pct(cc / ci) if ci else '-'} |",
          f"| 이전 28일 | {pi} | {pc} | {pct(pc / pi) if pi else '-'} |", "",
          table("잘된 글 상위 10 (클릭순)", top),
          table("노출 100+ 인데 클릭률 2% 미만", low_ctr),
          table("순위 8~30위", mid_rank),
          table("발행 14일 지났는데 노출 0", zero)]
    if sched:
        md.append(table("(참고) 예약 중 — 아직 공개 전", sorted(sched, key=lambda r: r["pub"])))
    path = os.path.join(out_dir, "01_실적분석.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"만들었습니다: {path}")
    print(f"받기: {fetch_note}")
    print(f"합계 노출 {ci}(이전 {pi}) · 클릭 {cc}(이전 {pc}) · 발행 {len(live)}편 · 예약 {len(sched)}편")


if __name__ == "__main__":
    main()
