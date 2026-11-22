# -*- coding: utf-8 -*-
"""W혜택주머니(longterm_wp) 전용: '주간 원고 일괄 처리' 화면용 계산 + 일괄 예약 발행.

- 싼세계 wp_batch.py 는 한 글자도 고치지 않고, 같은 규칙 함수·실행기 틀만 가져다 쓴다(복제가 먼저).
- 화면을 열 때는 읽기만 한다. 쓰는 곳은 '변경 저장'뿐: articles.json 의 그 글 lt_plan.scheduled_at 한 칸(쓰기 전 백업) + 주간기획/혜택_<주>/08_예약변경.md 줄 추가.
- 일괄 발행은 새 발행 코드를 만들지 않고 기존 단건 라우트(/article/<id>/publish-to-wordpress)를 최종본 화면 폼과 똑같은 값으로 부른다.
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta

import wp_batch as wb  # 싼세계 쪽 코드: 읽어서 쓰기만 한다(고치지 않음)

ROOT = os.path.dirname(os.path.abspath(__file__))
PLAN_ROOT = os.path.join(ROOT, "주간기획")
CHANNEL = "longterm_wp"
WEEKDAY = wb.WEEKDAY

# 혜택주머니 카테고리 6개(이름이 글자 그대로 같을 때만 보낸다). 옛 이름은 '문제'로 표시한다.
LT_CATS = ["정부지원금·복지", "서민금융·정책자금", "보험·연금", "세금·절세", "자격증·국비교육", "생활요금·해지환불"]
OLD_CATS = {"세금·연말정산": "세금·절세", "4대보험·연금": "보험·연금"}

# 발행 시간대(노란 경고 전용). tools/lt_schedule_slots.py 의 BANDS 값을 읽어 쓴다. 승인 후 full 모드로 바꾸려면 아래 MODE 를 'full' 로.
MODE = "review"


def _bands():
    """[(이름, 시작분, 끝분)] — tools/lt_schedule_slots.py 의 MODES 값(읽기만)."""
    tools = os.path.join(ROOT, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import lt_schedule_slots as S  # noqa: E402
    return [(n, h0 * 60 + m0, h1 * 60 + m1) for _, n, h0, m0, h1, m1 in S.MODES[MODE][0]]


def window_warn(time_s: str) -> str:
    m = re.fullmatch(r"(\d{2}):(\d{2})", time_s or "")
    if not m:
        return ""
    mins = int(m.group(1)) * 60 + int(m.group(2))
    bands = _bands()
    if any(a <= mins <= b for _, a, b in bands):
        return ""
    txt = " / ".join(f"{n} {a // 60:02d}:{a % 60:02d}~{b // 60:02d}:{b % 60:02d}" for n, a, b in bands)
    return f"발행 시간대({txt}) 밖이에요 (막지는 않아요)"


# ───────────────────────── 읽기: 주 표 ─────────────────────────

def _dt(date_s, time_s):
    return wb._dt(date_s, time_s)


def plan_dt(article: dict):
    """lt_plan.scheduled_at → datetime, 없거나 모양이 틀리면 None."""
    p = article.get("lt_plan")
    if not isinstance(p, dict):
        return None
    s = str(p.get("scheduled_at") or "")
    try:
        return datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def week_of(dt: datetime) -> str:
    return (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")


def list_weeks(articles: dict) -> list:
    return sorted({week_of(d) for d in (plan_dt(a) for a in articles.values()) if d})


def default_week(weeks: list, now: datetime) -> str:
    for w in weeks:
        try:
            if now < datetime.strptime(w, "%Y-%m-%d") + timedelta(days=7):
                return w
        except ValueError:
            continue
    return weeks[-1] if weeks else ""


def read_confirmed(week: str, plan_root: str = None) -> dict:
    """주간기획/혜택_<주>/06_확정.json 의 글별 {keyword: {role, cluster_seed}} (읽기만, 없으면 빈 dict)."""
    p = os.path.join(plan_root or PLAN_ROOT, f"혜택_{week}", "06_확정.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {x.get("keyword"): {"role": x.get("role", ""), "seed": x.get("cluster_seed", ""), "slot": x.get("slot", ""),
                               "category": x.get("category", "")} for x in data.get("posts", []) if isinstance(x, dict)}


def plan_view(article: dict) -> dict:
    """최종본 화면 미리 채우기(app._lt_plan_view)와 같은 값: 카테고리·태그는 lt_plan 그대로."""
    p = article.get("lt_plan") or {}
    return {"category": str(p.get("category") or "").strip(), "tag": str(p.get("tag") or "").strip()}


def _cover_ok(article: dict) -> str:
    return wb._cover_ok(article)


def build_week(week: str, queue: list, articles: dict, now: datetime, plan_root: str = None) -> dict:
    """한 주 화면용 표(읽기만). 반환 {week, rows[], summary{}}. 행 순서는 날짜·시각 순."""
    conf = read_confirmed(week, plan_root)
    qmap = {q["id"]: q for q in queue}
    seed_rep = {}  # 묶음(cluster_seed) → 대표글 id 들
    for q in queue:
        if q.get("cluster_role") == "대표" and q.get("cluster_seed"):
            seed_rep.setdefault(q["cluster_seed"], []).append(q["id"])
    rows = []
    for aid, art in articles.items():
        dt = plan_dt(art)
        if not dt or week_of(dt) != week or aid not in qmap:
            continue
        q = qmap[aid]
        c = conf.get(q.get("keyword"), {})
        role = c.get("role") or q.get("cluster_role") or ""
        seed = c.get("seed") or q.get("cluster_seed") or ""
        pv = plan_view(art)
        fin = (art.get("platform_final") or {}).get("wordpress") or ""
        problems = []
        if not fin:
            problems.append("최종본 없음")
        cover = _cover_ok(art)
        if not cover:
            problems.append("표지 없음")
        if not pv["category"]:
            problems.append("카테고리 없음")
        elif pv["category"] in OLD_CATS:
            problems.append(f"옛 카테고리 이름 '{pv['category']}' (지금 이름 '{OLD_CATS[pv['category']]}')")
        elif pv["category"] not in LT_CATS:
            problems.append(f"카테고리 '{pv['category']}' 는 6개 밖이에요")
        if "[확인 필요]" in fin:
            problems.append("본문에 [확인 필요]가 남아 있음")
        state = "발행 전"
        if wb.has_post(art):
            adt = wb.article_dt(art)
            state = "워드프레스 예약됨" if (adt and adt > now) else "발행됨"
        editable = state == "발행 전"
        if state == "발행 전" and problems:
            state = "문제"
        # 같은 묶음 대표글(자기 자신 제외): 앞의 규칙(세부는 대표보다 먼저 못 나감)용
        rep_ids = [r for r in seed_rep.get(seed, []) if r != aid] if role == "세부" else []
        rows.append({
            "id": aid, "aid": aid, "date": dt.strftime("%Y-%m-%d"), "wd": WEEKDAY[dt.weekday()], "time": dt.strftime("%H:%M"),
            "title": ((art.get("platform_title") or {}).get("wordpress") or q.get("keyword") or aid).strip(),
            "keyword": q.get("keyword", ""), "role": role, "seed": seed, "cover": cover,
            "category": pv["category"], "tag_text": pv["tag"], "tag_count": len(wb.split_tags(pv["tag"])),
            "state": state, "problems": problems, "editable": editable, "locked": not editable, "rep_ids": rep_ids,
            "warn": window_warn(dt.strftime("%H:%M")) if editable else "",
        })
    rows.sort(key=lambda r: (r["date"], r["time"]))
    for r in rows:  # 대표글 현재 상태·시각(같은 주 밖의 대표글도 포함)
        reps = []
        for rid in r["rep_ids"]:
            ra = articles.get(rid) or {}
            posted = wb.has_post(ra)
            rdt = wb.article_dt(ra) if posted else plan_dt(ra)
            reps.append({"id": rid, "posted": posted, "dt": rdt})
        r["reps"] = reps
        r["group"] = f"{r['role']} · {r['seed']}" if r["seed"] else r["role"]
    # 06_확정에 있는데 앱 lt_plan 이 없는 글(문제 줄로만 보여 줌)
    have = {r["keyword"] for r in rows}
    for kw, c in conf.items():
        if kw in have:
            continue
        slot = c.get("slot", "")
        d, t = (slot[:10], slot[11:16]) if "T" in slot else ("", "")
        rows.append({"id": f"nolt:{kw}", "aid": "", "date": d, "wd": wb._wd(d) if d else "?", "time": t, "title": kw, "keyword": kw,
                     "role": c.get("role", ""), "seed": c.get("seed", ""), "cover": "", "category": c.get("category", ""), "tag_text": "",
                     "tag_count": 0, "state": "문제", "problems": ["앱에 예약 정보(lt_plan)가 없음"], "editable": False, "locked": True,
                     "rep_ids": [], "reps": [], "warn": "", "group": f"{c.get('role', '')} · {c.get('seed', '')}"})
    by_date, by_cat, by_group, probs = {}, {}, {}, 0
    for r in rows:
        k = f"{r['date'][5:]}({r['wd']})"
        by_date[k] = by_date.get(k, 0) + 1
        by_cat[r["category"] or "(없음)"] = by_cat.get(r["category"] or "(없음)", 0) + 1
        if r["seed"]:
            by_group.setdefault(r["seed"], []).append(r)
        if r["problems"]:
            probs += 1
    groups = []
    for seed, rs in by_group.items():
        rep_t = [f"{x['date']} {x['time']}" for x in rs if x["role"] == "대표"]
        det_t = [f"{x['date']} {x['time']}" for x in rs if x["role"] == "세부"]
        bad = bool(rep_t) and any(d <= min(rep_t) for d in det_t)
        groups.append({"seed": seed, "rep": len(rep_t), "det": len(det_t), "ok": not bad})
    return {"week": week, "rows": rows,
            "summary": {"by_date": by_date, "by_cat": by_cat, "groups": groups, "problems": probs}}


# ───────────────────────── 2부: 날짜·시각 바꾸기 ─────────────────────────

def validate_changes(rows: list, changes: dict, now: datetime, existing: list) -> dict:
    """싼세계와 같은 규칙(wp_batch.validate_changes) + '세부는 대표보다 먼저 못 나감'. 반환 {id: 이유}."""
    base = [{"id": r["id"], "date": r["date"], "time": r["time"]} for r in rows]
    errs = {k: v.replace("싼세계", "혜택주머니") for k, v in wb.validate_changes(base, changes, now, existing).items()}
    state = {r["id"]: (r["date"], r["time"]) for r in rows}
    state.update(changes)
    by_id = {r["id"]: r for r in rows}
    for aid in list(changes):
        if aid in errs:
            continue
        r = by_id.get(aid)
        if not r:
            continue
        mine = _dt(*state[aid])
        if r["role"] == "세부":
            for rep in r["reps"]:
                rdt = rep["dt"]
                if rep["id"] in state:
                    rdt = _dt(*state[rep["id"]])
                if rdt and mine <= rdt:
                    errs[aid] = "같은 묶음 대표글보다 먼저(또는 같은 시각에) 나가요 — 세부글은 대표글 뒤여야 해요"
                    break
        elif r["role"] == "대표":  # 대표를 늦추다가 이미 잡힌 세부글을 앞지르게 되는 경우도 막는다
            for o in rows:
                if o["role"] == "세부" and o["seed"] == r["seed"] and any(x["id"] == aid for x in o["reps"]):
                    odt = _dt(*state[o["id"]])
                    if odt and odt <= mine:
                        errs[aid] = f"이 묶음 세부글('{o['title'][:18]}…')보다 늦게 나가게 돼요 — 대표글이 먼저여야 해요"
                        break
    return errs


def existing_scheduled(articles: dict, now: datetime) -> list:
    return wb.existing_scheduled(articles, now)


def save_changes(week: str, changes: dict, now: datetime, queue: list, load_fn, save_fn, articles_path: str,
                 plan_root: str = None) -> dict:
    """articles.json 의 그 글 lt_plan.scheduled_at '만' 고친다(쓰기 전 백업). 검사에 걸리면 아무것도 저장하지 않는다.
    반환 {ok, errs{id:이유}, backup, changed[id], log}."""
    articles = load_fn()
    data = build_week(week, queue, articles, now, plan_root)
    editable = {r["id"]: r for r in data["rows"] if r["editable"]}
    real = {k: v for k, v in changes.items() if k in editable and (editable[k]["date"], editable[k]["time"]) != v}
    if not real:
        return {"ok": True, "errs": {}, "backup": "", "changed": [], "log": ""}
    errs = validate_changes(data["rows"], real, now, existing_scheduled(articles, now))
    if errs:
        return {"ok": False, "errs": errs, "backup": "", "changed": [], "log": ""}
    d = os.path.dirname(articles_path)
    backup = os.path.join(d, f"articles.backup_before_lt_batch_{now:%Y%m%d_%H%M%S}.json")
    n = 1
    while os.path.exists(backup):
        backup = os.path.join(d, f"articles.backup_before_lt_batch_{now:%Y%m%d_%H%M%S}_{n}.json")
        n += 1
    with open(articles_path, "rb") as f:
        raw = f.read()
    with open(backup, "wb") as f:
        f.write(raw)
    fresh = load_fn()  # 백업 직후 다시 읽어서 그 글 한 칸만 바꾼다
    lines = []
    for aid, (dd, tt) in real.items():
        plan = dict(fresh[aid].get("lt_plan") or {})
        before = plan.get("scheduled_at")
        plan["scheduled_at"] = f"{dd}T{tt}"
        fresh[aid]["lt_plan"] = plan
        r = editable[aid]
        lines.append(f"| {now:%Y-%m-%d %H:%M} | {r['title'].replace('|', '/')} | {before} | {plan['scheduled_at']} |")
    save_fn(fresh)
    log = append_change_log(week, lines, plan_root)
    return {"ok": True, "errs": {}, "backup": backup, "changed": sorted(real), "log": log}


def append_change_log(week: str, lines: list, plan_root: str = None) -> str:
    wk = os.path.join(plan_root or PLAN_ROOT, f"혜택_{week}")
    os.makedirs(wk, exist_ok=True)
    path = os.path.join(wk, "08_예약변경.md")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(f"# 혜택주머니 예약 변경 기록 (주 {week})\n\n| 바꾼 시각 | 글 | 전 | 후 |\n|---|---|---|---|\n")
        f.write("\n".join(lines) + "\n")
    return path


# ───────────────────────── 3부: 일괄 예약 발행 ─────────────────────────

def make_plan(week: str, rows: list, picked_ids: list, now: datetime, wp_cat_names=None) -> dict:
    """고른 글 중 보낼 수 있는 것(targets)과 빠지는 것(excluded+이유). wp_cat_names: 워드프레스에 지금 있는 카테고리 이름 집합(None 이면 확인 못 함 → 모두 제외)."""
    by_id = {r["id"]: r for r in rows}
    cand, excluded = [], []
    for pid in picked_ids:
        r = by_id.get(pid)
        if not r:
            excluded.append({"id": pid, "title": "(표에 없음)", "reason": "표에 없는 글"})
            continue
        why = wb.eligibility(r, now)
        if r["category"] and r["category"] not in LT_CATS:
            pass  # 이미 problems 에 들어 있음
        elif r["category"] in LT_CATS:
            if wp_cat_names is None:
                why.append("워드프레스 카테고리 목록을 못 읽어서 확인 못 함")
            elif r["category"] not in wp_cat_names:
                why.append(f"워드프레스에 '{r['category']}' 카테고리가 없어요(발행하면 새로 만들어져요)")
        if why:
            excluded.append({"id": pid, "title": r["title"], "reason": " · ".join(dict.fromkeys(why))})
        else:
            cand.append(r)
    # 같은 묶음 대표글 조건: 대표가 이미 예약됐거나 이번에 같이 가야 한다 (대표가 빠지면 세부도 연쇄로 빠짐)
    ok_ids = {r["id"] for r in cand}
    changed = True
    while changed:
        changed = False
        for r in list(cand):
            if r["role"] != "세부" or not r["reps"]:
                continue
            mine = _dt(r["date"], r["time"])
            fine = any((rep["posted"] or rep["id"] in ok_ids) for rep in r["reps"])
            late = False
            for rep in r["reps"]:  # 대표글 시각(이번에 같이 가면 표의 시각, 이미 예약됐으면 그 시각)이 세부글보다 늦으면 안 됨
                rr = by_id.get(rep["id"])
                rdt = _dt(rr["date"], rr["time"]) if rr else rep["dt"]
                if rdt and mine and mine <= rdt:
                    late = True
            if late:
                cand.remove(r)
                ok_ids.discard(r["id"])
                excluded.append({"id": r["id"], "title": r["title"], "reason": "같은 묶음 대표글보다 먼저(또는 같은 시각에) 나가요"})
                changed = True
            elif not fine:
                cand.remove(r)
                ok_ids.discard(r["id"])
                excluded.append({"id": r["id"], "title": r["title"], "reason": "같은 묶음 대표글이 아직 예약 전이고 이번에도 안 가요"})
                changed = True
    targets = sorted(({"id": r["id"], "title": r["title"], "date": r["date"], "time": r["time"], "wd": r["wd"],
                       "category": r["category"], "tag_text": r["tag_text"], "role": r["role"],
                       "rep_ids": [x["id"] for x in r["reps"]]} for r in cand),
                     key=lambda t: (t["date"], t["time"]))
    return {"week": week, "targets": targets, "excluded": excluded}


class LtBatchRunner(wb.BatchRunner):
    """싼세계 BatchRunner 와 같은 틀(토큰 한 번만 · 한 번에 하나 · 한 편씩 · 실패 시 멈춤). 결과 파일만 혜택주머니 위치."""

    def _run(self, send_fn, load_article_fn, now_fn, sleep_sec, result_root):
        st = self.state
        try:
            sent_any = False
            for it in st["items"]:
                art = load_article_fn(it["id"])
                if art is None:
                    it["result"] = "건너뜀 — 앱에서 글을 못 찾음"
                    continue
                if wb.has_post(art):
                    it["result"] = "건너뜀 — 이미 워드프레스에 있음"
                    continue
                dt = _dt(it["date"], it["time"])
                if not dt or dt < now_fn() + timedelta(minutes=wb.LEAD_MIN):
                    it["result"] = f"건너뜀 — 예약 시각이 {wb.LEAD_MIN}분 안으로 가까워짐"
                    continue
                if it.get("rep_ids") and not any(wb.has_post(load_article_fn(rid) or {}) for rid in it["rep_ids"]):
                    it["result"] = "건너뜀 — 같은 묶음 대표글이 아직 워드프레스에 예약되지 않음"
                    continue
                if sent_any and sleep_sec:
                    time.sleep(sleep_sec)
                it["result"] = "보내는 중"
                ok, detail, post_id = send_fn(it)
                sent_any = True
                if ok:
                    it["result"] = "예약 완료"
                    it["post_id"] = str(post_id or "")
                else:
                    it["result"] = "실패 — " + (detail or "")[:300]
                    st["status"] = "stopped"
                    for rest in st["items"][st["items"].index(it) + 1:]:
                        rest["result"] = "보내지 않음(앞 글 실패로 멈춤)"
                    break
            if st["status"] == "running":
                st["status"] = "done"
        except Exception as e:  # noqa: BLE001
            st["status"] = "stopped"
            for it in st["items"]:
                if it["result"] in ("대기", "보내는 중"):
                    it["result"] = "보내지 않음/불명 — 오류: " + str(e)[:200]
        finally:
            st["finished"] = now_fn().strftime("%Y-%m-%d %H:%M:%S")
            try:
                append_result_file(st, result_root)
            except OSError:
                pass
            self._run_lock.release()


def append_result_file(st: dict, plan_root: str = None) -> str:
    wk = os.path.join(plan_root or PLAN_ROOT, f"혜택_{st['week']}")
    os.makedirs(wk, exist_ok=True)
    path = os.path.join(wk, "09_일괄발행_결과.md")
    lines = [f"\n## 일괄 예약 발행 {st['started']} ~ {st['finished']} — {'끝' if st['status'] == 'done' else '중간에 멈춤'}", "",
             "| 제목 | 결과 | 워드프레스 글 id | 예약 시각 |", "|---|---|---|---|"]
    for it in st["items"]:
        lines.append(f"| {it['title']} | {it['result']} | {it.get('post_id') or ''} | {it['date']} {it['time']} |")
    for ex in st.get("excluded") or []:
        lines.append(f"| {ex['title']} | 제외 — {ex['reason']} | | |")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(f"# 혜택주머니 일괄 예약 발행 결과 (주 {st['week']})\n")
        f.write("\n".join(lines) + "\n")
    return path


RUNNER = LtBatchRunner()


CAT_CHECK = None  # 시험에서 바꿔 끼우는 자리(워드프레스 카테고리 이름 집합을 돌려주는 함수). 기본은 wp_category_names


def wp_category_names(wp_publisher) -> "set | None":
    """워드프레스에 지금 있는 카테고리 이름(GET 읽기만). 못 읽으면 None."""
    try:
        names, page = set(), 1
        while page <= 5:
            r = wp_publisher._request("get", wp_publisher._api_url(f"categories?per_page=100&page={page}&_fields=id,name"),
                                      headers=wp_publisher._auth_header(), retries=1, timeout=30)
            if r.status_code != 200:
                return None if page == 1 else names
            batch = r.json()
            names |= {c["name"] for c in batch}
            if len(batch) < 100:
                break
            page += 1
        return names
    except Exception:  # noqa: BLE001
        return None


# ───────────────────────── 화면 라우트 (app.py 에서 register_routes 로 한 번만 연결) ─────────────────────────

def register_routes(flask_app, m):
    """m = app 모듈. 이 채널(longterm_wp)에서만 동작하고, 다른 채널에서 열면 첫 화면으로 보낸다."""
    from flask import redirect, render_template, request, url_for

    def _only_lt():
        return m.get_active_channel_id() == CHANNEL

    def _lt_articles():
        """백그라운드 실행 중(요청 밖)에도 쓸 수 있게 파일에서 직접 읽기."""
        try:
            return json.loads(m._read_text_retry(m.CHANNELS[CHANNEL]["articles_file"]))
        except FileNotFoundError:
            return {}

    def _screen(week, errs=None, form_vals=None, notice=""):
        now = datetime.now().replace(second=0, microsecond=0)
        arts = m.load_articles()
        weeks = list_weeks(arts)
        week = week if week in weeks else default_week(weeks, now)
        data = build_week(week, m.load_queue(), arts, now, PLAN_ROOT) if week else {
            "week": "", "rows": [], "summary": {"by_date": {}, "by_cat": {}, "groups": [], "problems": 0}}
        for r in data["rows"]:
            r["pickable"] = r["editable"] and not r["problems"]
            fv = (form_vals or {}).get(r["id"])
            r["in_val"] = f"{fv[0]}T{fv[1]}" if fv else f"{r['date']}T{r['time']}"
            r["err"] = (errs or {}).get(r["id"], "")
            r["ampm"] = "오전" if r["time"] and r["time"] < "12:00" else "오후"
        return render_template("lt_batch.html", data=data, weeks=weeks, week=week, notice=notice, running=RUNNER.running(),
                               now=now.strftime("%Y-%m-%d %H:%M"))

    @flask_app.route("/lt-batch")
    def lt_batch_screen():
        if not _only_lt():
            return redirect(url_for("index"))
        return _screen(request.args.get("week", ""), notice=request.args.get("notice", ""))

    @flask_app.route("/lt-batch/save", methods=["POST"])
    def lt_batch_save():
        """'변경 저장': 그 글 lt_plan.scheduled_at 한 칸만(백업 후). 워드프레스에는 아무것도 보내지 않는다."""
        if not _only_lt():
            return redirect(url_for("index"))
        week = request.form.get("week", "")
        if RUNNER.running():  # 일괄 발행 중에는 앱 데이터를 같이 쓰지 않는다(서로 덮어쓰기 방지)
            return redirect(url_for("lt_batch_screen", week=week, notice="저장하지 않았어요 — 일괄 예약 발행이 진행 중이에요. 끝난 뒤에 저장하세요."))
        now = datetime.now().replace(second=0, microsecond=0)
        arts = m.load_articles()
        if week not in list_weeks(arts):
            return redirect(url_for("lt_batch_screen"))
        data = build_week(week, m.load_queue(), arts, now, PLAN_ROOT)
        changes, form_vals = {}, {}
        for r in data["rows"]:
            v = (request.form.get("dt_" + r["id"]) or "").strip()
            if r["editable"] and v:
                form_vals[r["id"]] = (v[:10], v[11:16])
                changes[r["id"]] = (v[:10], v[11:16])
        res = save_changes(week, changes, now, m.load_queue(), m.load_articles, m.save_articles,
                           m.CHANNELS[CHANNEL]["articles_file"], PLAN_ROOT)
        if not res["ok"]:
            return _screen(week, errs=res["errs"], form_vals=form_vals, notice="저장하지 않았어요 — 빨간 줄을 고쳐 주세요.")
        msg = f"저장했어요 ({len(res['changed'])}편 시각 변경)." if res["changed"] else "바뀐 시각이 없어요."
        return redirect(url_for("lt_batch_screen", week=week, notice=msg))

    @flask_app.route("/lt-batch/confirm", methods=["POST"])
    def lt_batch_confirm():
        """'고른 글 일괄 예약 발행' 첫 단계: 아무것도 보내지 않고(워드프레스 카테고리 읽기만) 대상·제외 이유를 보여 준다."""
        if not _only_lt():
            return redirect(url_for("index"))
        week = request.form.get("week", "")
        now = datetime.now().replace(second=0, microsecond=0)
        arts = m.load_articles()
        if week not in list_weeks(arts):
            return redirect(url_for("lt_batch_screen"))
        data = build_week(week, m.load_queue(), arts, now, PLAN_ROOT)
        plan = make_plan(week, data["rows"], request.form.getlist("pick"), now, (CAT_CHECK or wp_category_names)(m.wp_publisher))
        token = RUNNER.new_token(plan) if plan["targets"] else ""
        return render_template("lt_batch_confirm.html", plan=plan, token=token, week=week, running=RUNNER.running())

    def _send_one(it):
        """기존 단건 발행 라우트를 최종본 화면 폼(lt_pub_fields)과 똑같은 값으로 부른다: 예약 발행 + 그 시각 + 카테고리 + 태그."""
        names = (CAT_CHECK or wp_category_names)(m.wp_publisher)  # 보내기 직전 한 번 더: 워드프레스에 없는 카테고리면 새로 만들어지니 멈춤
        if names is None or it["category"] not in names:
            return False, f"워드프레스에서 카테고리 '{it['category']}' 를 확인하지 못해 보내지 않음", ""
        c = flask_app.test_client()
        with c.session_transaction() as s:
            s["channel"] = CHANNEL
        r = c.post(f"/article/{it['id']}/publish-to-wordpress", data={
            "publish_mode": "future", "scheduled_at": f"{it['date']}T{it['time']}",
            "category": it["category"], "tag": it["tag_text"]})
        loc = r.headers.get("Location", "")
        if r.status_code == 302 and "wp_published=" in loc:
            pid = (((_lt_articles().get(it["id"]) or {}).get("platform_post_id")) or {}).get("wordpress")
            return True, "", pid
        body = re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<style.*?</style>", "", r.get_data(as_text=True)))
        return False, f"HTTP {r.status_code} " + re.sub(r"\s+", " ", body).strip()[:250], ""

    @flask_app.route("/lt-batch/run", methods=["POST"])
    def lt_batch_run():
        """확인 창에서 '예, 예약합니다'를 눌렀을 때만. 토큰은 한 번만 쓸 수 있다."""
        if not _only_lt():
            return redirect(url_for("index"))
        ok, why = RUNNER.start(request.form.get("token", ""), _send_one, lambda i: _lt_articles().get(i), result_root=PLAN_ROOT)
        if not ok:
            return render_template("error.html", message=why), 409
        return redirect(url_for("lt_batch_progress"))

    @flask_app.route("/lt-batch/progress")
    def lt_batch_progress():
        if not _only_lt():
            return redirect(url_for("index"))
        return render_template("lt_batch_progress.html", st=RUNNER.state)
