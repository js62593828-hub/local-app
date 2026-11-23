# -*- coding: utf-8 -*-
"""9단계 기획표 저장(8차): 작업폴더/09_기획표저장.md 를 만들고, 확정 묶음의 정책·날짜를 시즌달력 월별 파일에 한 줄씩 더한다.

읽는 것: 06_확정.json · 06_확정_제목.json · 06_확정_장부.json · 05_기획표.md(③ 묶음표) · 글장부.json
쓰는 것(새 파일·덧붙임만): 작업폴더/09_기획표저장.md · docs/혜택주머니/시즌달력_월별/MM월.md (없는 달은 빈 틀로 만듦)
다시 돌려도 같은 줄은 중복으로 더하지 않는다(정책 이름 + 해 기준). 앱 데이터·워드프레스는 안 건드린다.

사용: python tools/lt_save_plan.py --week 2026-11-23"""
import argparse
import os
import re
from datetime import date, datetime, timedelta

import lt_common as C
import lt_data as D
import lt_ledger as L

CAL_DIR = os.path.join(C.ROOT, "docs", "혜택주머니", "시즌달력_월별")
CAL_HEAD = ("| 날짜 | 정책(묶음) | 대상 | 시의성 종류 | 그 해 쓴 글(주차·대표 제목) | 그 해 성적(발행 후 28일 노출/클릭) | 기록 주 |\n"
            "|---|---|---|---|---|---|---|\n")


def month_file(m):
    return os.path.join(CAL_DIR, f"{m:02d}월.md")


def ensure_month_file(m):
    os.makedirs(CAL_DIR, exist_ok=True)
    p = month_file(m)
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {m}월 시즌 달력 — 혜택주머니 (내년 같은 시기 참고)\n\n"
                    "> lt_save_plan.py 가 매주 확정 묶음의 '관련 날짜'가 속한 달 파일에 한 줄씩 덧붙입니다. "
                    "성적 칸은 28일이 지난 뒤 사람이 또는 다음 학습 기록에서 채웁니다.\n\n" + CAL_HEAD)
    return p


def add_calendar_line(related, policy, target, kind, post_title, week):
    """관련 날짜가 속한 달 파일에 한 줄. 반환: 새로 더했으면 True."""
    try:
        d = date.fromisoformat(related)
    except (TypeError, ValueError):
        return False
    p = ensure_month_file(d.month)
    txt = open(p, encoding="utf-8").read()
    tag = f"{d.year}|{D.key(policy)}"
    if f"<!--{tag}-->" in txt:
        return False
    line = f"| {related} | {policy} | {target} | {kind} | {week} · {post_title} | (28일 뒤 채움) | {week} | <!--{tag}-->\n"
    with open(p, "a", encoding="utf-8") as f:
        if not txt.endswith("\n"):
            f.write("\n")
        f.write(line)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", required=True)
    ap.add_argument("--no-calendar", action="store_true", help="시즌달력 월별 파일은 건드리지 않는다(시험용)")
    ap.add_argument("--out", default="", help="시험용: 09_기획표저장.md 를 이 파일로 쓴다")
    a = ap.parse_args()
    week = C.check_week(a.week)
    wdir = C.week_dir(week)
    conf = C.read_json(os.path.join(wdir, "06_확정.json"), {}) or {}
    posts = sorted(conf.get("posts") or [], key=lambda p: p.get("order") or 0)
    if not posts:
        raise SystemExit("06_확정.json 이 없습니다.")
    titles = {t["order"]: t for t in (C.read_json(os.path.join(wdir, "06_확정_제목.json"), []) or []) if isinstance(t, dict)}
    src = L.load_week_sources(week)
    led = C.read_json(D.LEDGER_PATH, {"rows": {}})
    rows = {r["키워드"]: r for r in led["rows"].values() if r.get("주차") == week}

    # 묶음 = cluster_seed. 묶음 기호(A~F)는 06_확정_제목.json 의 bundle
    bundles = {}
    for p in posts:
        t = titles.get(p["order"], {})
        b = bundles.setdefault(p["cluster_seed"], {"letter": t.get("bundle", ""), "posts": []})
        b["posts"].append((p, t))

    md = [f"# 09 기획표 저장 — {week} 주 (혜택주머니)", "",
          f"- 만든 시각: {datetime.now():%Y-%m-%d %H:%M} · 이 파일은 다음 주 1단계(실적 분석)의 입력입니다. 숫자·제목은 확정본(06_확정*.json)과 글 장부에서 옮겼습니다.", "",
          "## 묶음표", "",
          "| 묶음 | 기호 | 카테고리 | 대상 | 시의성 종류 | 관련 날짜 | 글 수 | 발행 기간 |", "|---|---|---|---|---|---|---|---|"]
    for seed, b in bundles.items():
        ps = [p for p, _ in b["posts"]]
        rs = [rows.get(p["keyword"]) or {} for p in ps]
        first = rs[0] if rs else {}
        slots = sorted(p["slot"] for p in ps)
        md.append(f"| {seed} | {b['letter']} | {D.norm_cat(ps[0].get('category'))} | {first.get('대상', '')} | {first.get('시의성 종류', '')} | "
                  f"{first.get('관련 날짜', '')} | {len(ps)} | {slots[0][:10]} ~ {slots[-1][:10]} |")
    md += ["", "## 글마다 (발행 시각 · 제목 장치 · 뼈대)", "",
           "| 순 | 발행 시각 | 묶음·역할 | 키워드 | 제목 | 제목 장치 | 점수 | 뼈대 | 의도 | 글자 수 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for p in posts:
        r = rows.get(p["keyword"]) or {}
        md.append(f"| {p['order']} | {p['slot'].replace('T', ' ')} | {p['cluster_seed']}·{p['role']} | {p['keyword']} | {r.get('제목', titles.get(p['order'], {}).get('title_draft', ''))} | "
                  f"{r.get('제목 장치', '')} | {r.get('점수', '')} | {r.get('뼈대', '')} | {r.get('의도', '')} | {r.get('글자 수', '')} |")
    # 갱신 결과 / 다음 갱신 시점
    renew = [r for r in led["rows"].values() if r.get("갱신 날짜")]
    md += ["", "## 갱신 결과", ""]
    wk_dir_upd = os.path.join(wdir, "갱신")
    if os.path.isdir(wk_dir_upd) and os.listdir(wk_dir_upd):
        md.append("- 수정본 폴더: `작업폴더/갱신/` — " + ", ".join(sorted(os.listdir(wk_dir_upd))[:10]))
    else:
        md.append("- 이 주에 만든 갱신 수정본 없음(`작업폴더/갱신/` 비어 있음).")
    for r in renew:
        md.append(f"- 장부 갱신 기록: {r['제목']} — {', '.join(r['갱신 날짜'])}")
    md += ["", "## 다음 갱신 시점·성적 확인 시점", "",
           "| 묶음 | 마지막 글 발행 | 14일 성적 확인 | 28일 성적 확인(갱신 판단) | 시의성 지난 뒤 점검 |", "|---|---|---|---|---|"]
    for seed, b in bundles.items():
        ps = [p for p, _ in b["posts"]]
        last = max(p["slot"][:10] for p in ps)
        ld = date.fromisoformat(last)
        rel = (rows.get(ps[0]["keyword"]) or {}).get("관련 날짜", "")
        after = ""
        try:
            after = (date.fromisoformat(rel) + timedelta(days=3)).isoformat() if rel else ""
        except ValueError:
            pass
        md.append(f"| {seed} | {last} | {(ld + timedelta(days=13)).isoformat()} | {(ld + timedelta(days=27)).isoformat()} | {after or '-'} |")
    # 시즌달력
    added = []
    if not a.no_calendar:
        for m in (1, 2, 3):
            ensure_month_file(m)  # 1~3월 빈 틀(없으면)
        for seed, b in bundles.items():
            ps = [p for p, _ in b["posts"]]
            r0 = rows.get(ps[0]["keyword"]) or {}
            rel = r0.get("관련 날짜")
            if rel:
                new = add_calendar_line(rel, seed, r0.get("대상", ""), r0.get("시의성 종류", ""), r0.get("제목", ps[0]["keyword"]), week)
                added.append(f"{rel} {seed}" + ("" if new else " (이미 있던 줄)"))
    md += ["", "## 시즌달력 월별 파일에 더한 줄", ""]
    md += ([f"- {x} → `docs/혜택주머니/시즌달력_월별/`" for x in added] if added else ["- 더한 줄 없음(관련 날짜가 비어 있음)."])
    path = a.out or os.path.join(wdir, "09_기획표저장.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"만들었습니다: {path} · 시즌달력 기록 {len(added)}줄({sum(1 for x in added if "이미" not in x)}줄 새로)")


if __name__ == "__main__":
    main()
