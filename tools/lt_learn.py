# -*- coding: utf-8 -*-
"""성적표(8차): 글 장부 × 일별 실적 → 주간기획/혜택_날짜/01b_성적표.md  (읽기만 하고 새 파일만 쓴다)

기준: '발행 후 14일'과 '28일' 성적(그 기간이 다 지난 글만 — 지난주 글·예약 글은 자동으로 빠짐).
묶어서 비교: 제목 장치·제목 말투·숫자/질문 / 카테고리·대상·의도·뼈대 / 대표·세부 / 시의성 종류(+수명) /
             구글 판정 vs 실제 순위 / 네이버 검색량 구간 vs 구글 노출.
규칙: 클릭률은 같은 순위 구간(1~3·4~7·8~15·16+) 안에서만 견주고, 글 8편 미만인 칸은 '참고'로만 표시한다.
      이 도구는 숫자 표만 쓴다(결론 문장 없음). 비어 있는 칸은 '(미기록)' 묶음으로 따로 센다.
또 '이미 문 앞 키워드'(검색어 노출 20+·순위 8~30인데 제목·키워드로 쓴 글이 없는 것)와 '빗나간 판정',
'갱신 전후 성적'을 표로 보여 준다. 실적학습.md 에 같은 방향 3주 이어짐이 있으면 규칙 제안 후보로 표시만 한다."""
import argparse
import glob
import os
import re
from datetime import date, datetime, timedelta

import lt_common as C
import lt_data as D
import lt_ledger as L
from lt_perf_report import match_all

MIN_N = 8
BANDS = ["1~3위", "4~7위", "8~15위", "16위+"]
VOL_BINS = [("50 미만", 0, 50), ("50~299", 50, 300), ("300~999", 300, 1000), ("1,000~4,999", 1000, 5000), ("5,000+", 5000, 10 ** 12)]


def win(daily, norms, pub, n, end_d):
    """발행일부터 n일(발행일 포함)의 합계. 아직 n일이 안 지났으면 None."""
    s = date.fromisoformat(pub)
    e = s + timedelta(days=n - 1)
    if e > end_d:
        return None
    return D.sum_rows(D.page_rows(daily, norms, s.isoformat(), e.isoformat()))


def life_shares(daily, norms, pub, end_d):
    """28일 노출이 첫 7일 / 8~14일 / 15~28일에 얼마씩 나왔는지(%)."""
    s = date.fromisoformat(pub)
    if s + timedelta(days=27) > end_d:
        return None
    parts = []
    for a, b in ((0, 6), (7, 13), (14, 27)):
        parts.append(D.sum_rows(D.page_rows(daily, norms, (s + timedelta(days=a)).isoformat(), (s + timedelta(days=b)).isoformat()))[0])
    tot = sum(parts)
    return None if tot == 0 else [p / tot * 100 for p in parts]


def group_table(items, keyfn, title, extra_life=False, order=None):
    """items = [{'m': (노출,클릭,클릭률,순위), 'row': 장부줄, 'life': [...]}]. keyfn(row) → 묶음 이름 목록(없으면 미기록)."""
    groups = {}
    for it in items:
        ks = keyfn(it["row"]) or ["(미기록)"]
        for k in ks:
            groups.setdefault(k, []).append(it)
    if not groups or (len(groups) == 1 and "(미기록)" in groups):
        return [f"### {title}", "", f"- 자료 없음: 이 기준으로 적힌 글이 아직 없습니다(성적이 난 글 {len(items)}편 모두 칸이 비어 있음).", ""]
    names = order or sorted(groups, key=lambda k: (k == "(미기록)", -len(groups[k]), k))
    head = "| 묶음 | 글 수 | 표시 | 평균 노출 | 평균 클릭 | 순위(노출 가중) | 전체 클릭률 | " + " | ".join(f"클릭률 {b}" for b in BANDS)
    if extra_life:
        head += " | 노출 나온 때(첫7일/8~14일/15~28일, %)"
    lines = [f"### {title}", "", head + " |", "|" + "---|" * head.count("|")]
    for k in names:
        g = groups.get(k)
        if not g:
            continue
        ms = [x["m"] for x in g]
        imp, clk, ctr, pos = D.sum_rows(ms)
        mark = "참고(8편 미만)" if len(g) < MIN_N else "—"
        cells = []
        for b in BANDS:
            sub = [x["m"] for x in g if D.rank_band(x["m"][3]) == b]
            if sub:
                si, sc, sctr, _ = D.sum_rows(sub)
                cells.append(f"{D.pct(sctr)} (글 {len(sub)}·노출 {si})")
            else:
                cells.append("")
        row = f"| {k} | {len(g)} | {mark} | {imp / len(g):.1f} | {clk / len(g):.2f} | {pos:.1f} | {D.pct(ctr)} | " + " | ".join(cells)
        if extra_life:
            lv = [x["life"] for x in g if x.get("life")]
            row += " | " + (" / ".join(f"{sum(v[i] for v in lv) / len(lv):.0f}" for i in range(3)) + f" (글 {len(lv)})" if lv else "")
        lines.append(row + " |")
    lines.append("")
    return lines


def vol_bin(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return []
    for name, lo, hi in VOL_BINS:
        if lo <= v < hi:
            return [name]
    return []


def doors(qfile, rows):
    """이미 문 앞 키워드: 검색어 노출 20+ · 순위 8~30 인데 제목·키워드로 쓴 글이 없는 것."""
    if not qfile:
        return None, []
    q = C.read_json(qfile, {})
    acc = {}
    for page, query, imp, clk, pos in q.get("rows", []):
        e = acc.setdefault(query, {"imp": 0, "clk": 0, "pos": 0.0, "pages": {}})
        e["imp"] += imp
        e["clk"] += clk
        e["pos"] += pos * imp
        e["pages"][page] = e["pages"].get(page, 0) + imp
    used = [D.key(r.get("제목")) for r in rows] + [D.key(r.get("키워드")) for r in rows]
    out = []
    for query, e in acc.items():
        if e["imp"] < 20:
            continue
        pos = e["pos"] / e["imp"]
        if not (8 <= pos <= 30):
            continue
        k = D.key(query)
        if k and any(k in u for u in used if u):
            continue  # 이미 그 말을 쓴 글이 있음
        top_page = max(e["pages"], key=e["pages"].get)
        out.append((query, e["imp"], e["clk"], pos, top_page))
    out.sort(key=lambda x: -x[1])
    return q.get("range"), out


def renewal_table(daily, rows, art_norms, end_d):
    out = []
    for r in rows:
        for rd in r.get("갱신 날짜") or []:
            R = date.fromisoformat(rd)
            if R + timedelta(days=27) > end_d:
                out.append(f"| {r['제목']} | {rd} | (4주가 아직 안 지남) | | |")
                continue
            b = D.sum_rows(D.page_rows(daily, art_norms.get(r["글 id"], ()), (R - timedelta(days=28)).isoformat(), (R - timedelta(days=1)).isoformat()))
            a = D.sum_rows(D.page_rows(daily, art_norms.get(r["글 id"], ()), R.isoformat(), (R + timedelta(days=27)).isoformat()))
            out.append(f"| {r['제목']} | {rd} | 노출 {b[0]} / 클릭 {b[1]} / 순위 {b[3]:.1f} | 노출 {a[0]} / 클릭 {a[1]} / 순위 {a[3]:.1f} | |")
    return out


def direction_proposals():
    """실적학습.md 맨 위 3개 기록에 같은 '방향:' 태그가 이어지면 규칙 제안 후보로 표시만 한다."""
    if not os.path.isfile(D.LEARN_DOC):
        return []
    txt = open(D.LEARN_DOC, encoding="utf-8").read()
    blocks = re.split(r"(?m)^## ", txt)[1:]
    tags = []
    for b in blocks[:3]:
        m = re.search(r"(?m)^[-*]?\s*방향\s*[:：]\s*(.+)$", b)
        tags.append({t.strip() for t in m.group(1).split(",") if t.strip()} if m else set())
    if len(tags) == 3 and all(tags):
        common = tags[0] & tags[1] & tags[2]
        return sorted(common)
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", required=True)
    ap.add_argument("--out", default="", help="시험용: 이 파일로 쓴다")
    ap.add_argument("--ledger", default="", help="시험용: 장부 파일")
    a = ap.parse_args()
    week = C.check_week(a.week)
    app = C.load_app()
    daily = D.load_daily()
    led = C.read_json(a.ledger or D.LEDGER_PATH, None)
    if not daily["days"] or not led:
        raise SystemExit("perf_daily.json 또는 글장부.json 이 없습니다. lt_perf_report.py · lt_ledger.py 를 먼저 돌리세요.")
    articles, queue = D.load_articles_queue(app)
    art_norms, _m, _u = match_all(daily, None, articles, queue)
    last = D.last_day(daily)
    end_d = date.fromisoformat(last)
    rows = [r for r in led["rows"].values() if r.get("상태") != "합침(retired)"]
    live = [r for r in rows if r.get("상태") == "발행" and r.get("발행 시각")]

    def items_for(n):
        out = []
        for r in live:
            norms = art_norms.get(r["글 id"], ())
            m = win(daily, norms, r["발행 시각"][:10], n, end_d)
            if m is None:
                continue
            out.append({"row": r, "m": m, "life": life_shares(daily, norms, r["발행 시각"][:10], end_d)})
        return out

    md = [f"# 01b 성적표 — {week} 주 (혜택주머니)", "",
          f"- 자료: 일별 실적 {min(daily['days'])} ~ {last} · 글 장부 {len(rows)}편(예약 {sum(1 for r in rows if r.get('상태') == '예약')}편은 성적 기준에서 빠짐) · 만든 시각 {datetime.now():%Y-%m-%d %H:%M}",
          "- 기준: 발행일부터 14일·28일이 **다 지난 글만** 셉니다(지난주 글 제외). 클릭률은 같은 순위 구간 안에서만 비교하세요. 글 8편 미만 칸은 '참고'.",
          "- 순위 구간은 그 글의 평균 순위를 1~3.5·3.5~7.5·7.5~15.5·15.5+ 로 나눈 것입니다. 이 표에는 결론 문장이 없습니다(숫자만).", ""]
    for n in (14, 28):
        its = items_for(n)
        md += [f"## 발행 후 {n}일 성적 — {len(its)}편", ""]
        if not its:
            md += ["(해당 글 없음)", ""]
            continue
        imp, clk, ctr, pos = D.sum_rows([x["m"] for x in its])
        md += [f"- 전체: 노출 {imp} · 클릭 {clk} · 클릭률 {D.pct(ctr)} · 순위 {pos:.1f}", ""]
        dev = lambda r: [c for c in re.split(r"[+＋]", r.get("제목 장치") or "") if c]
        life = (n == 28)
        md += group_table(its, dev, "제목 장치(글자 하나씩 따로 셈 — 한 글이 여러 곳에 들어감)")
        md += group_table(its, lambda r: [r.get("제목 말투")] if r.get("제목 말투") else [], "제목 말투(해요/격식/질문)", order=["해요", "격식", "질문", "(미기록)"])
        md += group_table(its, lambda r: [("숫자 있음" if r.get("제목 숫자") == "예" else "숫자 없음")], "제목에 숫자 유무")
        md += group_table(its, lambda r: [("질문형" if r.get("제목 질문") == "예" else "질문 아님")], "제목 질문 유무")
        # 교차표(8-1): 질문형 × 대표/세부, 제목 말투 × 대표/세부 — 8편 미만 칸은 group_table 이 '참고'로 표시
        rs = lambda r: r.get("대표/세부") or "(미기록)"
        md += group_table(its, lambda r: [("질문형" if r.get("제목 질문") == "예" else "질문 아님") + " × " + rs(r)], "교차표: 질문형 × 대표/세부",
                          order=[a + " × " + b for a in ("질문형", "질문 아님") for b in ("대표", "세부", "(미기록)")])
        md += group_table(its, lambda r: [(r.get("제목 말투") or "(미기록)") + " × " + rs(r)], "교차표: 제목 말투 × 대표/세부",
                          order=[a + " × " + b for a in ("해요", "격식", "질문", "(미기록)") for b in ("대표", "세부", "(미기록)")])
        md += group_table(its, lambda r: [D.norm_cat(r.get("카테고리"))] if r.get("카테고리") else [], "카테고리(옛 이름은 새 이름으로 묶음)" + (" · 수명 포함" if life else ""), extra_life=life)
        md += group_table(its, lambda r: [r.get("대상")] if r.get("대상") else [], "대상")
        md += group_table(its, lambda r: [r.get("의도")] if r.get("의도") else [], "의도")
        md += group_table(its, lambda r: [r.get("뼈대")] if r.get("뼈대") else [], "뼈대")
        md += group_table(its, lambda r: [r.get("대표/세부")] if r.get("대표/세부") else [], "대표/세부" + (" · 수명 포함" if life else ""), extra_life=life)
        md += group_table(its, lambda r: [r.get("시의성 종류")] if r.get("시의성 종류") else [], "시의성 종류" + (" · 수명 포함" if life else ""), extra_life=life)
        md += group_table(its, lambda r: [r.get("구글 판정")] if r.get("구글 판정") else [], "구글 판정 vs 실제 순위")
        md += group_table(its, lambda r: vol_bin(r.get("네이버 검색량")), "네이버 검색량 구간 vs 구글 노출(검색량은 글 만들 때 조회한 값)",
                          order=[b[0] for b in VOL_BINS] + ["(미기록)"])

    # 이미 문 앞 키워드
    qfiles = sorted(glob.glob(os.path.join(D.QUERY_DIR, "*.json")))
    rng, dr = doors(qfiles[-1] if qfiles else None, rows)
    md += ["## 이미 문 앞 키워드 (검색어 노출 20+ · 평균 순위 8~30 · 그 말을 제목·키워드로 쓴 글 없음)", ""]
    if rng is None:
        md += ["(검색어 자료 없음 — lt_perf_report 를 먼저 돌리세요)", ""]
    else:
        md += [f"- 검색어 자료: {rng[0]} ~ {rng[1]} (28일). 쓴 글 판정은 '검색어가 제목·키워드 글자에 그대로 들어 있는가'로 합니다.", "",
               "| 검색어 | 노출 | 클릭 | 평균 순위 | 가장 많이 보인 페이지 |", "|---|---|---|---|---|"]
        md += [f"| {q} | {i} | {c} | {p:.1f} | {D.unquote(pg)} |" for q, i, c, p, pg in dr[:40]] or ["| (없음) | | | | |"]
        md += [""]

    # 빗나간 판정
    its28 = items_for(28)
    miss = []
    for it in its28:
        r, m = it["row"], it["m"]
        v = r.get("구글 판정") or ""
        if m[0] == 0:
            continue
        if v.startswith("🟢") and m[3] > 20:
            miss.append((r, m, "🟢였는데 28일 평균 순위 20위 밖"))
        elif v.startswith("🔴") and m[3] <= 10:
            miss.append((r, m, "🔴였는데 28일 평균 순위 10위 안"))
    md += ["## 빗나간 판정 (구글 판정 기준 손볼 거리)", ""]
    if miss:
        md += ["| 키워드 | 제목 | 판정 | 28일 노출 | 28일 순위 | 어떻게 빗나갔나 |", "|---|---|---|---|---|---|"]
        md += [f"| {r['키워드']} | {r['제목']} | {r['구글 판정']} | {m[0]} | {m[3]:.1f} | {why} |" for r, m, why in miss]
    else:
        n_v = sum(1 for it in its28 if it["row"].get("구글 판정"))
        md += [f"(해당 없음 — 28일이 지난 글 중 구글 판정이 적힌 글 {n_v}편. 구글 판정은 11/23 주 글부터 장부에 있어 28일이 지나는 12/21 이후부터 채워집니다)"]
    md += [""]

    # 갱신 전후
    ren = renewal_table(daily, rows, art_norms, end_d)
    md += ["## 갱신 전후 성적 (갱신 날짜 4주 뒤에 채워짐)", ""]
    md += (["| 글 | 갱신일 | 갱신 전 28일 | 갱신 후 28일 | |", "|---|---|---|---|---|"] + ren) if ren else ["(갱신 기록 없음)"]
    md += [""]

    props = direction_proposals()
    md += ["## 기획지침에 올릴 규칙 제안 후보 (표시만 — 지침은 사용자 확인 뒤에만 고침)", ""]
    md += ([f"- 실적학습.md 최근 3주 기록에 같은 방향이 이어짐: {', '.join(props)}"] if props else ["(해당 없음)"])
    md += [""]

    path = a.out or os.path.join(C.week_dir(week), "01b_성적표.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"만들었습니다: {path}")
    print(f"14일 기준 {len(items_for(14))}편 · 28일 기준 {len(its28)}편 · 문 앞 키워드 {len(dr)}개 · 빗나간 판정 {len(miss)}개")


if __name__ == "__main__":
    main()
