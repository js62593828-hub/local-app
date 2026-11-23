# -*- coding: utf-8 -*-
"""키워드 은행(8차): 3·4단계에서 조회한 키워드를 날짜와 함께 쌓는다 → 주간기획/_누적/키워드은행.json (새 파일만 씀)

칸: 키워드 · 처음 본 날 · 검색량(PC·모바일·합계·상승세·등급·조회일) · 구글(판정·AI 개요·상위 모양·조회일·원본 줄) · 썼는지(글 id) · 본 주차
30일 안에 조회한 키워드는 다시 조회하지 않고 은행 값을 쓴다(lt_keyword_volume.py · lt_google_check.py 가 은행을 먼저 본다).

사용:
  python tools/lt_bank.py --import-weeks            # 주간기획/혜택_*/ 의 03_검색량*.json · 04_구글경쟁*.json 을 은행에 채움(이미 있으면 더 새로운 날짜만 교체)
  python tools/lt_bank.py --refresh-used            # 앱 글(제목·키워드)을 보고 '썼는지' 칸 새로 계산
  python tools/lt_bank.py --candidates --week 2026-11-30   # '다시 볼 후보' 표를 작업폴더/02a_다시볼후보.md 로
  --bank 파일  시험용으로 다른 은행 파일 쓰기"""
import argparse
import glob
import os
import re
import shutil
from datetime import date, datetime, timedelta

import lt_common as C
import lt_data as D

FRESH_DAYS = 30


def load(path=None):
    b = C.read_json(path or D.BANK_PATH, None)
    if not isinstance(b, dict) or "keywords" not in b:
        b = {"updated": "", "fresh_days": FRESH_DAYS, "keywords": {}}
    return b


def save(bank, path=None):
    path = path or D.BANK_PATH
    D.ensure_dirs()
    bank["updated"] = datetime.now().isoformat(timespec="seconds")
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    C.write_json(path, bank)


def is_fresh(checked, today=None, days=FRESH_DAYS):
    if not checked:
        return False
    today = today or date.today()
    try:
        return (today - date.fromisoformat(checked[:10])).days <= days
    except ValueError:
        return False


def entry(bank, kw, today_s):
    k = D.key(kw)
    e = bank["keywords"].setdefault(k, {"keyword": kw, "first_seen": today_s, "weeks": [], "volume": None, "google": None, "used": None})
    return e


def google_shape(row):
    """구글 한 줄에서 '상위 모양'만 요약: 큰 곳·작은 곳 개수, 도메인 몇 개."""
    top = row.get("top8") or []
    kinds = {}
    for t in top:
        kinds[t.get("kind", "?")] = kinds.get(t.get("kind", "?"), 0) + 1
    return {"kinds": kinds, "domains": [t.get("domain") for t in top][:8]}


def record_volume(bank, rows, checked, week=None):
    for r in rows:
        if r.get("total") is None and r.get("tier") == "조회불가":
            continue
        e = entry(bank, r["keyword"], checked)
        if e["volume"] and (e["volume"].get("checked") or "") > checked:
            continue  # 은행 값이 더 새로우면 그대로
        e["volume"] = {"pc": r.get("pc"), "mobile": r.get("mobile"), "total": r.get("total"),
                       "rising_pct": r.get("rising_pct"), "tier": r.get("tier"), "checked": checked}
        if week and week not in e["weeks"]:
            e["weeks"].append(week)


def record_google(bank, rows, checked, week=None):
    for r in rows:
        if not r.get("verdict") and r.get("error"):
            continue
        e = entry(bank, r["keyword"], checked)
        if e["google"] and (e["google"].get("checked") or "") > checked:
            continue
        e["google"] = {"verdict": r.get("verdict"), "ai_overview": bool(r.get("ai_overview")), "shape": google_shape(r),
                       "checked": checked, "row": {k: v for k, v in r.items() if k != "bank_date"}}
        if week and week not in e["weeks"]:
            e["weeks"].append(week)


def week_of_path(p):
    m = re.search(r"혜택_(\d{4}-\d{2}-\d{2})", p.replace("\\", "/"))
    return m.group(1) if m else None


def import_weeks(bank):
    n_v = n_g = 0
    for wdir in sorted(glob.glob(os.path.join(C.ROOT, "주간기획", "혜택_*"))):
        wk = week_of_path(wdir)
        for f in sorted(glob.glob(os.path.join(wdir, "03_검색량*.json"))):
            rows = C.read_json(f, []) or []
            d = date.fromtimestamp(os.path.getmtime(f)).isoformat()
            record_volume(bank, [r for r in rows if isinstance(r, dict)], d, wk)
            n_v += len(rows)
        for f in sorted(glob.glob(os.path.join(wdir, "04_구글경쟁*.json"))):
            rows = C.read_json(f, []) or []
            d = date.fromtimestamp(os.path.getmtime(f)).isoformat()
            record_google(bank, [r for r in rows if isinstance(r, dict)], d, wk)
            n_g += len(rows)
    return n_v, n_g


def refresh_used(bank, app=None):
    app = app or C.load_app()
    articles, queue = D.load_articles_queue(app)
    texts = []
    for aid, art in articles.items():
        i = D.article_info(aid, art, queue.get(aid) or {})
        if i["pub"] and not i["retired"]:
            texts.append((aid, i["week"], D.key(i["title"]), D.key(i["keyword"])))
    n = 0
    for k, e in bank["keywords"].items():
        hit = next(((aid, wk) for aid, wk, t, kw in texts if k and (k in t or k in kw or (kw and kw == k))), None)
        e["used"] = {"article_id": hit[0], "week": hit[1]} if hit else None
        n += bool(hit)
    return n


def candidates(bank, min_volume=50):
    """안 쓴 좋은 키워드: 검색량 등급이 세부 이상, 구글이 🔴가 아님(조회 안 했으면 '구글 미조회'로 표시), 아직 쓴 글 없음."""
    out = []
    for e in bank["keywords"].values():
        if e.get("used"):
            continue
        v = e.get("volume") or {}
        if (v.get("total") or 0) < min_volume:
            continue
        g = e.get("google") or {}
        verdict = g.get("verdict") or ""
        if verdict.startswith("🔴"):
            continue
        out.append((e, v, g))
    out.sort(key=lambda x: (-(1 if (x[2].get("verdict") or "").startswith("🟢") else 0), -(x[1].get("total") or 0)))
    return out


def write_candidates(bank, path, week):
    cs = candidates(bank)
    lines = [f"# 02a 키워드 은행 '다시 볼 후보' — {week} 주", "",
             f"- 은행 {len(bank['keywords'])}개 중 아직 글로 안 쓴 것 · 검색량 50+ · 구글 🔴 제외. 숫자는 '조회일' 기준이라 오래됐으면 다시 봐야 합니다(30일이 지나면 3·4단계에서 새로 조회됨).", "",
             "| 키워드 | 검색량 | 상승세 | 등급 | 구글 | AI 개요 | 상위 모양 | 처음 본 날 | 조회일(검색량/구글) |", "|---|---|---|---|---|---|---|---|---|"]
    for e, v, g in cs[:60]:
        shape = g.get("shape") or {}
        sh = ", ".join(f"{k} {n}" for k, n in (shape.get("kinds") or {}).items())
        rp = "" if v.get("rising_pct") is None else f"{v['rising_pct']:+.0f}%"
        lines.append(f"| {e['keyword']} | {v.get('total')} | {rp} | {v.get('tier') or ''} | {g.get('verdict') or '미조회'} | "
                     f"{('있음' if g.get('ai_overview') else '없음') if g else ''} | {sh} | {e.get('first_seen')} | {v.get('checked') or ''} / {g.get('checked') or ''} |")
    if not cs:
        lines.append("| (없음) | | | | | | | | |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(cs)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import-weeks", action="store_true")
    ap.add_argument("--refresh-used", action="store_true")
    ap.add_argument("--candidates", action="store_true")
    ap.add_argument("--week", default="")
    ap.add_argument("--bank", default="")
    a = ap.parse_args()
    bank = load(a.bank or None)
    if a.import_weeks:
        n_v, n_g = import_weeks(bank)
        print(f"가져옴: 검색량 {n_v}줄 · 구글 {n_g}줄 → 은행 {len(bank['keywords'])}개")
    if a.refresh_used or a.import_weeks or a.candidates:
        n = refresh_used(bank)
        print(f"쓴 키워드 표시: {n}개 / {len(bank['keywords'])}개")
    save(bank, a.bank or None)
    if a.candidates:
        week = C.check_week(a.week) if a.week else None
        if not week:
            raise SystemExit("--candidates 는 --week 가 필요합니다.")
        path = os.path.join(C.week_dir(week), "02a_다시볼후보.md")
        n = write_candidates(bank, path, week)
        print(f"다시 볼 후보 {n}개 → {path}")


if __name__ == "__main__":
    main()
