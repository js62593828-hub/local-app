# -*- coding: utf-8 -*-
"""글 장부(8차): 글 1편 = 1줄 → 주간기획/_누적/글장부.json  (새 파일만 씀. 앱 글·대기열·워드프레스는 읽기만)

칸: 글 id · 주소 · 주차 · 발행 시각 · 묶음 · 대표/세부 · 카테고리 · 대상 · 의도 · 키워드 · 네이버 검색량 · 상승세 ·
    구글 판정 · AI 개요 · 제목 · 제목 장치 · 점수 · 제목 말투 · 뼈대 · 시의성 종류 · 관련 날짜 · 글자 수 · 갱신 날짜
출처: 앱 articles/queue + 그 주 폴더의 03_검색량*.json · 04_구글경쟁*.json · 05_기획표.md · 06_확정.json · 06_확정_제목.json ·
      06_확정_장부.json(대상·시의성 종류·관련 날짜 — 기획할 때 적는 보충 파일, 있으면 씀)
모르는 칸은 비워 둔다(지어내지 않음). 다시 돌려도 중복 없음(id 로 합침). 사람이 적은 '갱신 날짜'는 지우지 않는다.

사용:
  python tools/lt_ledger.py --all                 # 앱에 있는 모든 글(자료 남은 만큼)
  python tools/lt_ledger.py --week 2026-11-23     # 그 주 글만 더하거나 새로 고침
  python tools/lt_ledger.py --renew ID 2026-12-01 # 갱신 날짜 한 줄 적기
  --out 파일  시험용으로 다른 곳에 쓰기"""
import argparse
import glob
import os
import re
import shutil
from datetime import date, datetime

import lt_common as C
import lt_data as D

COLS = ["글 id", "주소", "주차", "발행 시각", "묶음", "대표/세부", "카테고리", "대상", "의도", "키워드", "네이버 검색량", "상승세",
        "구글 판정", "AI 개요", "제목", "제목 장치", "점수", "제목 말투", "뼈대", "시의성 종류", "관련 날짜", "글자 수", "갱신 날짜"]
EXTRA = ["상태", "제목 숫자", "제목 질문", "출처"]  # 장부에 같이 남기는 보조 칸(빈 칸 비율에는 안 넣음)
AUTO_BLANK_OK = {"갱신 날짜"}  # 갱신한 적 없으면 비는 게 정상이라 빈 칸 비율에서 뺀다


def title_tone(t):
    t = (t or "").strip()
    if not t:
        return ""
    if t.endswith("?") or re.search(r"(까요|나요|가요|일까|인가)\W*$", t):
        return "질문"
    if re.search(r"(요|죠)\W*$", t):
        return "해요"
    return "격식"


def week_folder(week):
    p = os.path.join(C.ROOT, "주간기획", f"혜택_{week}")
    return p if os.path.isdir(p) else None


def _table_rows(md, first_col_re):
    rows = []
    for ln in md.splitlines():
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if cells and re.match(first_col_re, cells[0]):
                rows.append(cells)
    return rows


def load_week_sources(week):
    """그 주 폴더의 기획 자료를 모은다. 폴더가 없으면 빈 값."""
    src = {"volume": {}, "google": {}, "titles": {}, "orders": {}, "intent": {}, "target": {}, "extra_b": {}, "extra_p": {}}
    wdir = week_folder(week)
    if not wdir:
        return src
    for f in sorted(glob.glob(os.path.join(wdir, "03_검색량*.json"))):
        for r in C.read_json(f, []) or []:
            if isinstance(r, dict) and r.get("keyword"):
                src["volume"][D.key(r["keyword"])] = r
    for f in sorted(glob.glob(os.path.join(wdir, "04_구글경쟁*.json"))):
        for r in C.read_json(f, []) or []:
            if isinstance(r, dict) and r.get("keyword"):
                src["google"][D.key(r["keyword"])] = r
    conf = C.read_json(os.path.join(wdir, "06_확정.json"), {}) or {}
    for p in conf.get("posts") or []:
        if p.get("order") is not None:
            src["orders"][D.key(p.get("keyword"))] = p
    for t in C.read_json(os.path.join(wdir, "06_확정_제목.json"), []) or []:
        if isinstance(t, dict) and t.get("order") is not None:
            src["titles"][t["order"]] = t
    plan = os.path.join(wdir, "05_기획표.md")
    if os.path.isfile(plan):
        md = open(plan, encoding="utf-8").read()
        for c in _table_rows(md, r"^\d+$"):  # ④ 표: 순 | 발행 | 묶음·역할 | 의도 | 키워드 | ⭐제목 | 장치·점수 | 뼈대·요약이름 | ...
            if len(c) >= 8:
                src["intent"][int(c[0])] = {"의도": c[3], "장치·점수": c[6], "뼈대": c[7]}
        for c in _table_rows(md, r"^[A-Z☆]"):  # ③ 표: 묶음 | 카테고리 | 대상 | 대표 키워드 ...
            if len(c) >= 4 and re.match(r"^[A-Z]\s", c[0]):
                src["target"][c[0][0]] = c[2]
    extra = C.read_json(os.path.join(wdir, "06_확정_장부.json"), {}) or {}
    src["extra_b"] = extra.get("bundles") or {}
    src["extra_p"] = {str(k): v for k, v in (extra.get("posts") or {}).items()}
    return src


def build_row(info, art, q, srcs, today):
    kw = info["keyword"]
    wk = info["week"]
    s = srcs.get(wk) or {}
    kk = D.key(kw)
    post = (s.get("orders") or {}).get(kk) or {}
    order = post.get("order")
    t = (s.get("titles") or {}).get(order) or {}
    bundle_letter = t.get("bundle") or ""
    it = (s.get("intent") or {}).get(order) or {}
    vol = (s.get("volume") or {}).get(kk) or {}
    goog = (s.get("google") or {}).get(kk) or {}
    eb = dict((s.get("extra_b") or {}).get(bundle_letter) or {})
    eb.update((s.get("extra_p") or {}).get(str(order)) or {})
    sources = ["앱 글", "앱 대기열"]
    if vol or goog or t or it:
        sources.append("그 주 폴더")
    # 점수: '장치·점수' 칸의 "B+F+A·6점" 에서 숫자만
    score = ""
    m = re.search(r"(\d+)\s*점", it.get("장치·점수", "") or "")
    if m:
        score = int(m.group(1))
    # 메타 카테고리
    cat = ""
    try:
        import metadata_parser
        cat = metadata_parser.parse_metadata_memo(D.wp(art, "platform_metadata")).get("category") or ""
    except Exception:  # noqa: BLE001
        pass
    cat = D.norm_cat(cat or info["queue_category"] or post.get("category"))
    raw = D.wp(art, "platform_raw")
    chars = ""
    if raw:
        try:
            from lt_draft_check import count_chars
            chars = count_chars(raw)
        except Exception:  # noqa: BLE001
            chars = len(re.sub(r"\s+", "", raw))
    pub_at = (info["pub"] + (" " + info["pub_time"] if info["pub_time"] else "")) if info["pub"] else ""
    try:
        pub_dt = datetime.fromisoformat(f"{info['pub']}T{info['pub_time'] or '00:00'}")
    except ValueError:
        pub_dt = None
    status = "합침(retired)" if info["retired"] else ("예약" if pub_dt and pub_dt > datetime.now() else "발행")
    volume = vol.get("total") if vol else None
    if volume is None:
        volume = q.get("total_search_volume") if q.get("total_search_volume") is not None else ""
    row = {
        "글 id": info["id"], "주소": D.unquote(info["url"]) if info["url"] else "", "주차": wk, "발행 시각": pub_at,
        "묶음": info["cluster"], "대표/세부": info["role"], "카테고리": cat,
        "대상": eb.get("대상") or (s.get("target") or {}).get(bundle_letter, ""),
        "의도": eb.get("의도") or it.get("의도", ""),
        "키워드": kw, "네이버 검색량": volume,
        "상승세": vol.get("rising_pct") if vol and vol.get("rising_pct") is not None else "",
        "구글 판정": goog.get("verdict", ""), "AI 개요": ("있음" if goog.get("ai_overview") else "없음") if goog else "",
        "제목": info["title"], "제목 장치": eb.get("제목 장치") or t.get("device", ""), "점수": eb.get("점수") or score,
        "제목 말투": title_tone(info["title"]), "뼈대": eb.get("뼈대") or t.get("skeleton", ""),
        "시의성 종류": eb.get("시의성 종류", ""), "관련 날짜": eb.get("관련 날짜", ""),
        "글자 수": chars, "갱신 날짜": [],
        "상태": status, "제목 숫자": "예" if re.search(r"\d", info["title"]) else "아니오",
        "제목 질문": "예" if title_tone(info["title"]) == "질문" else "아니오", "출처": sources,
    }
    return row


def is_blank(v):
    return v in ("", None, [])


def merge_row(old, new):
    """새로 만든 줄이 우선. 단 새 값이 비었고 옛 값이 있으면 옛 값을 지키고, 갱신 날짜는 합친다."""
    out = dict(new)
    for k, v in (old or {}).items():
        if k == "갱신 날짜":
            out[k] = sorted(set((old.get(k) or []) + (new.get(k) or [])))
        elif is_blank(out.get(k)) and not is_blank(v):
            out[k] = v
    return out


def collect(app, weeks=None):
    articles, queue = D.load_articles_queue(app)
    today = date.today()
    infos = {aid: D.article_info(aid, art, queue.get(aid) or {}) for aid, art in articles.items()}
    use = {aid: i for aid, i in infos.items() if i["pub"] and (weeks is None or i["week"] in weeks)}
    srcs = {wk: load_week_sources(wk) for wk in {i["week"] for i in use.values()}}
    return {aid: build_row(i, articles[aid], queue.get(aid) or {}, srcs, today) for aid, i in use.items()}


def update_ledger(app, weeks=None, path=None):
    """장부에 글을 더하거나 새로 고친다. 반환: (장부, 이번에 다룬 줄 수)."""
    path = path or D.LEDGER_PATH
    D.ensure_dirs()
    led = C.read_json(path, None) or {"rows": {}}
    rows = led.get("rows") or {}
    new = collect(app, weeks)
    for aid, r in new.items():
        rows[aid] = merge_row(rows.get(aid), r)
    led = {"updated": datetime.now().isoformat(timespec="seconds"), "columns": COLS, "extra_columns": EXTRA,
           "rows": dict(sorted(rows.items(), key=lambda kv: (kv[1].get("발행 시각") or "", kv[0])))}
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    C.write_json(path, led)
    return led, len(new)


def blank_stats(led, week=None):
    rows = [r for r in led["rows"].values() if r.get("상태") != "합침(retired)" and (week is None or r.get("주차") == week)]
    per = {c: sum(1 for r in rows if is_blank(r.get(c))) for c in COLS if c not in AUTO_BLANK_OK}
    total = len(rows) * len(per)
    blank = sum(per.values())
    return len(rows), blank, total, per


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--renew", nargs=2, metavar=("ID", "날짜"))
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    path = a.out or None
    if a.renew:
        led = C.read_json(path or D.LEDGER_PATH, {"rows": {}})
        r = led["rows"].get(a.renew[0])
        if not r:
            raise SystemExit("장부에 없는 글 id 입니다.")
        r["갱신 날짜"] = sorted(set((r.get("갱신 날짜") or []) + [a.renew[1]]))
        shutil.copy2(path or D.LEDGER_PATH, (path or D.LEDGER_PATH) + ".bak")
        C.write_json(path or D.LEDGER_PATH, led)
        print("갱신 날짜를 적었습니다:", a.renew)
        return
    if not a.week and not a.all:
        raise SystemExit("--week 또는 --all 이 필요합니다.")
    app = C.load_app()
    weeks = None if a.all else {C.check_week(a.week)}
    led, n = update_ledger(app, weeks, path)
    print(f"장부 저장: {path or D.LEDGER_PATH} · 이번에 다룬 글 {n}편 · 장부 전체 {len(led['rows'])}편")
    for label, wk in (("이번 범위", None if a.all else next(iter(weeks))), ("전체", None)):
        n_rows, blank, total, per = blank_stats(led, wk)
        if n_rows:
            print(f"빈 칸 비율({label}, 합침 글 제외 {n_rows}편 × {len(per)}칸): {blank}/{total} = {blank / total * 100:.0f}%")
            top = sorted(per.items(), key=lambda kv: -kv[1])[:8]
            print("  가장 많이 빈 칸: " + ", ".join(f"{k} {v}" for k, v in top))


if __name__ == "__main__":
    main()
