# -*- coding: utf-8 -*-
"""W싼세계(wp_tips) 전용: 최종본 화면의 카테고리·태그 미리 채우기 + '주간 원고 일괄 처리' 화면용 계산.

- 읽기만 한다(앱 데이터·워드프레스에 쓰지 않음). 예외: save_schedule 은 _예약표.md 만 고친다(백업 후).
- 같이 쓰는 코드(app.py)에서는 이 파일의 함수만 부른다. 다른 채널 코드는 건드리지 않는다.
"""
import os
import re
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DRAFT_ROOT = os.path.join(ROOT, "pipeline", "wp_drafts")

# 싼세계 워드프레스 카테고리 3개(11/22 확인: 생활용품 24 · 주방용품 50 · 생활가전 53). 이름이 글자 그대로 같을 때만 미리 채운다.
CATS = {"생활용품": 24, "주방용품": 50, "생활가전": 53}
WEEKDAY = "월화수목금토일"


# ───────────────────────── 1부: 카테고리·태그 ─────────────────────────

def split_tags(text: str, limit: int = 10) -> list:
    """app._split_wp_tags 와 같은 규칙(쉼표 , ， 로 나눔 · 앞뒤 공백·맨 앞 # 제거 · 빈 값·중복 제거 · 최대 10개)."""
    out = []
    for part in re.split(r"[,，]", text or ""):
        name = part.strip().lstrip("#").strip()
        if name and name not in out:
            out.append(name)
        if len(out) >= limit:
            break
    return out


def _grab(text: str, label: str) -> str:
    m = re.search(rf"^[ \t]*{label}[ \t]*[:：][ \t]*(.+)$", text or "", flags=re.M)
    return m.group(1).strip().strip("*").strip() if m else ""


_draft_cache = {}


def _draft_by_title() -> dict:
    """pipeline/wp_drafts/<주>/*.md 원고를 '제목:' 줄로 찾을 수 있게 읽는다(읽기만). 파일이 바뀌면 다시 읽는다."""
    out = {}
    try:
        weeks = sorted(os.listdir(DRAFT_ROOT))
    except OSError:
        return out
    for wk in weeks:
        d = os.path.join(DRAFT_ROOT, wk)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md") or fn.startswith("_") or fn.endswith(".facts.md"):
                continue
            p = os.path.join(d, fn)
            try:
                mt = os.path.getmtime(p)
                if _draft_cache.get(p, (None,))[0] != mt:
                    with open(p, encoding="utf-8") as f:
                        _draft_cache[p] = (mt, f.read())
                text = _draft_cache[p][1]
            except OSError:
                continue
            title = _grab(text, "제목")
            if title:
                out[title] = text
    return out


def meta_view(article: dict) -> dict:
    """한 글의 발행 칸에 미리 채울 카테고리·태그. (읽기만)
    출처: ① 앱에 저장된 메타데이터 메모('카테고리 제안'·'태그 제안') ② 같은 제목의 원고 파일 ③ 태그는 본문 끝 '태그:' 줄(# 뗀 것).
    카테고리는 CATS 3개 중 하나와 글자 그대로 같을 때만 채우고, 아니면 비우고 경고를 단다.
    반환: {category, cat_id, tags[list], tag_text, source, warns[list], info}"""
    memo = (article.get("platform_metadata") or {}).get("wordpress") or ""
    title = (article.get("platform_title") or {}).get("wordpress") or ""
    draft = ""
    cat = _grab(memo, "카테고리 제안")
    tag_raw = _grab(memo, "태그 제안")
    source = "메모" if (cat or tag_raw) else ""
    if not cat or not tag_raw:
        draft = _draft_by_title().get(title.strip(), "")
        if draft:
            if not cat:
                cat = _grab(draft, "카테고리 제안")
                source = source or "원고 파일"
            if not tag_raw:
                tag_raw = _grab(draft, "태그 제안")
                source = source or "원고 파일"
    tags = split_tags(tag_raw)
    if not tags:  # 본문 끝 '태그: #… #…' 줄 (# 뗀 것)
        for text in ((article.get("platform_raw") or {}).get("wordpress") or "", draft):
            line = _grab(text.replace("\r\n", "\n"), "태그")
            found = re.findall(r"#([^\s#,]+)", line) if line else []
            if found:
                tags = split_tags(", ".join(found))
                source = source or "원고 파일"
                break
    warns = []
    if not cat:
        warns.append("카테고리 제안을 못 찾았어요 — 직접 적으세요")
    elif cat not in CATS:
        warns.append(f"카테고리 제안 '{cat}' 이(가) 싼세계 3개(생활용품·주방용품·생활가전)와 달라요 — 비워 둠")
        cat = ""
    if not tags:
        warns.append("태그 제안을 못 찾았어요")
    info = f"원고 메타데이터: 카테고리 {cat or '없음'} · 태그 {len(tags)}개 (미리 채운 것, 발행 버튼을 눌러야 반영)"
    return {"category": cat, "cat_id": CATS.get(cat), "tags": tags, "tag_text": ", ".join(tags),
            "source": source, "warns": warns, "info": info}


# ───────────────────────── 2부: 주간 원고 일괄 처리(보기 + 날짜·시각 바꾸기) ─────────────────────────

SCHED_FILE = "_예약표.md"
SCHED_HEAD = "| 날짜 | 요일 | 시각 | 틀 | 제목 | 대기열 id |"
BASE_TIMES = {"C": (7, 40), "S": (12, 30), "D": (18, 40)}  # 원고지침 11장: 틀별 기준 시각
JITTER_MIN = 10
SECOND_SHIFT_MIN = 180   # 같은 날 같은 틀 두 번째 글은 기준 +3시간
MIN_GAP_MIN = 60         # 같은 날 두 글 사이 최소 간격
LEAD_MIN = 30            # 일괄 예약은 지금보다 30분 이상 뒤만


def list_weeks(root: str = None) -> list:
    root = root or DRAFT_ROOT
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [n for n in names if os.path.isfile(os.path.join(root, n, SCHED_FILE))]


def default_week(weeks: list, now: datetime) -> str:
    """가장 가까운 앞으로의 주: 아직 끝나지 않은 첫 주(월요일+7일 전). 없으면 마지막 주."""
    for w in weeks:
        try:
            end = datetime.strptime(w, "%Y-%m-%d") + timedelta(days=7)
        except ValueError:
            continue
        if now < end:
            return w
    return weeks[-1] if weeks else ""


def read_sched(path: str) -> list:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return rows
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 6 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]) and re.fullmatch(r"\d{2}:\d{2}", cells[2]):
            rows.append({"date": cells[0], "time": cells[2], "frame": cells[3], "title": cells[4], "id": cells[5]})
    return rows


def _dt(date_s: str, time_s: str):
    try:
        return datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def _wd(date_s: str) -> str:
    d = _dt(date_s, "00:00")
    return WEEKDAY[d.weekday()] if d else "?"


def article_dt(article: dict):
    """앱에 저장된 그 글의 워드프레스 예약·발행 시각(읽기만). 없으면 None."""
    d = (article.get("platform_published_at") or {}).get("wordpress")
    t = (article.get("platform_scheduled_time") or {}).get("wordpress")
    return _dt(str(d)[:10], (t or "00:00")[:5]) if d else None


def has_post(article: dict) -> bool:
    return bool((article.get("platform_post_id") or {}).get("wordpress")
                or (article.get("platform_published_url") or {}).get("wordpress")
                or (article.get("platform_published_at") or {}).get("wordpress"))


def _cover_ok(article: dict) -> str:
    cv = ((article.get("covers") or {}).get("wordpress") or "").strip()
    if not cv:
        return ""
    return cv if os.path.isfile(os.path.join(ROOT, cv.lstrip("/").replace("/", os.sep))) else ""


def build_week(week: str, queue: list, articles: dict, now: datetime, root: str = None) -> dict:
    """한 주 화면용 표. 읽기만. 반환 {week, rows[], summary{}}.
    행 순서는 날짜·시각 순. 'editable' = 발행 전(워드프레스에 글이 아직 없음)인 글."""
    root = root or DRAFT_ROOT
    sched = read_sched(os.path.join(root, week, SCHED_FILE))
    qids = {q["id"] for q in queue}
    by_title = {}
    for q in queue:
        t = ((articles.get(q["id"]) or {}).get("platform_title") or {}).get("wordpress") or ""
        if t.strip():
            by_title.setdefault(t.strip(), []).append(q["id"])
    rows = []
    for s in sorted(sched, key=lambda x: (x["date"], x["time"])):
        aid = s["id"] if s["id"] in qids else ""
        if not aid and len(by_title.get(s["title"], [])) == 1:
            aid = by_title[s["title"]][0]
        art = articles.get(aid) if aid else None
        problems = []
        meta = {"category": "", "tag_text": "", "tags": [], "warns": []}
        cover = ""
        state = "발행 전"
        if not art:
            problems.append("짝 없음(앱 대기열에 이 글이 없음)")
        else:
            meta = meta_view(art)
            if not (art.get("platform_final") or {}).get("wordpress"):
                problems.append("최종본 없음")
            cover = _cover_ok(art)
            if not cover:
                problems.append("표지 없음")
            if not meta["category"]:
                problems.append("카테고리 없음")
            if "[쿠팡링크" in ((art.get("platform_final") or {}).get("wordpress") or ""):
                problems.append("[쿠팡링크 가 남아 있음")
            if has_post(art):
                adt = article_dt(art)
                state = "워드프레스 예약됨" if (adt and adt > now) else "발행됨"
        editable = bool(art) and state == "발행 전"
        if state == "발행 전" and problems:
            state = "문제"
        rows.append({
            "id": aid or s["id"], "aid": aid, "date": s["date"], "wd": _wd(s["date"]), "time": s["time"], "frame": s["frame"],
            "title": s["title"], "cover": cover, "category": meta["category"], "tag_text": meta["tag_text"],
            "tag_count": len(meta["tags"]), "state": state, "problems": problems,
            "editable": editable, "locked": not editable,
        })
    by_date, by_cat, probs = {}, {}, 0
    for r in rows:
        r["warn"] = window_warning(r["frame"], r["time"])
        k = f"{r['date'][5:]}({r['wd']})"
        by_date[k] = by_date.get(k, 0) + 1
        c = r["category"] or "(없음)"
        by_cat[c] = by_cat.get(c, 0) + 1
        if r["problems"]:
            probs += 1
    return {"week": week, "rows": rows, "summary": {"by_date": by_date, "by_cat": by_cat, "problems": probs}}


def window_warning(frame: str, time_s: str) -> str:
    """기준 시각 ±10분 밖이면 노란 경고 문구(막지 않음). 두 번째 글(+3시간)도 허용."""
    base = BASE_TIMES.get(frame)
    m = re.fullmatch(r"(\d{2}):(\d{2})", time_s or "")
    if not base or not m:
        return ""
    mins = int(m.group(1)) * 60 + int(m.group(2))
    b = base[0] * 60 + base[1]
    for shift in (0, SECOND_SHIFT_MIN):
        if abs(mins - (b + shift)) <= JITTER_MIN:
            return ""
    return f"{frame}틀 기준 {base[0]:02d}:{base[1]:02d}(두 번째 글이면 +3시간) ±{JITTER_MIN}분 밖이에요"


def existing_scheduled(articles: dict, now: datetime) -> list:
    """앱에 저장된 싼세계 글의 예약·발행 시각 [(aid, datetime)] (워드프레스에 글이 있는 것만)."""
    out = []
    for aid, a in articles.items():
        if has_post(a):
            dt = article_dt(a)
            if dt:
                out.append((aid, dt))
    return out


def validate_changes(rows: list, changes: dict, now: datetime, existing: list) -> dict:
    """rows [{id,date,time}] 지금 표, changes {id: (date, time)} 고친 글만. 반환 {id: 이유}. 비어 있으면 저장해도 됨.
    규칙(원고지침 11장): 지난 시각 금지 · 같은 주 같은 '시:분' 금지 · 같은 날 두 글 60분 이상 · 이미 싼세계에 예약된 시각과 겹침 금지."""
    errs = {}
    state = {r["id"]: (r["date"], r["time"]) for r in rows}
    state.update(changes)
    row_ids = {r["id"] for r in rows}
    for aid, (d, t) in changes.items():
        dt = _dt(d, t)
        if not dt:
            errs[aid] = "날짜·시각 모양이 틀렸어요"
            continue
        if dt < now:
            errs[aid] = "지난 시각이에요"
            continue
        others = [(oid, v) for oid, v in state.items() if oid != aid]
        if any(ot == t for _, (od, ot) in others):
            errs[aid] = f"같은 주에 같은 '시:분'({t})이 이미 있어요"
            continue
        if any(od == d and _dt(od, ot) and abs((_dt(od, ot) - dt).total_seconds()) < MIN_GAP_MIN * 60 for _, (od, ot) in others):
            errs[aid] = f"같은 날 다른 글과 {MIN_GAP_MIN}분 안이에요"
            continue
        if any(x != aid and x not in row_ids and edt == dt for x, edt in existing):
            errs[aid] = "이미 싼세계에 예약된 시각과 겹쳐요"
    return errs


def _render_table(rows: list) -> str:
    out = [SCHED_HEAD, "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["date"], x["time"])):
        out.append(f"| {r['date']} | {_wd(r['date'])} | {r['time']} | {r['frame']} | {r['title'].replace('|', '/')} | {r['id'] or '-'} |")
    return "\n".join(out)


def rewrite_sched_text(text: str, rows: list) -> str:
    """원래 파일의 표 앞뒤 글은 그대로 두고 표만 새로 쓴다."""
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("|")]
    if not idx:
        return text
    head, tail = lines[: idx[0]], lines[idx[-1] + 1:]
    return "\n".join(head + _render_table(rows).split("\n") + tail) + "\n"


def save_schedule(week: str, changes: dict, now: datetime, existing: list, root: str = None) -> dict:
    """_예약표.md 만 고친다(쓰기 전 `_예약표.backup_<날짜시각>.md` 백업). 앱 데이터는 쓰지 않는다.
    반환 {ok, errs{id:이유}, backup, changed[id]}. 검사에 걸리면 아무것도 저장하지 않는다."""
    root = root or DRAFT_ROOT
    path = os.path.join(root, week, SCHED_FILE)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    rows = read_sched(path)
    cur = {r["id"]: (r["date"], r["time"]) for r in rows}
    real = {k: v for k, v in changes.items() if k in cur and cur[k] != v}
    if not real:
        return {"ok": True, "errs": {}, "backup": "", "changed": []}
    errs = validate_changes([{"id": r["id"], "date": r["date"], "time": r["time"]} for r in rows], real, now, existing)
    if errs:
        return {"ok": False, "errs": errs, "backup": "", "changed": []}
    backup = os.path.join(root, week, f"_예약표.backup_{now:%Y%m%d_%H%M%S}.md")
    n = 1
    while os.path.exists(backup):
        backup = os.path.join(root, week, f"_예약표.backup_{now:%Y%m%d_%H%M%S}_{n}.md")
        n += 1
    with open(backup, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    for r in rows:
        if r["id"] in real:
            r["date"], r["time"] = real[r["id"]]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(rewrite_sched_text(text, rows))
    os.replace(tmp, path)
    return {"ok": True, "errs": {}, "backup": backup, "changed": sorted(real)}


# ───────────────────────── 3부: 일괄 예약 발행(기존 단건 발행 라우트를 그대로 부름) ─────────────────────────

import json
import threading
import time
import uuid


def eligibility(row: dict, now: datetime) -> list:
    """일괄 예약 발행 대상 조건. 빈 목록이면 보낼 수 있음. 하나라도 걸리면 이유 목록."""
    why = []
    if row.get("state") in ("워드프레스 예약됨", "발행됨"):
        why.append("이미 " + row["state"])
    elif row.get("state") == "문제" or row.get("problems"):
        why.extend(row.get("problems") or ["문제 있음"])
    if not row.get("editable"):
        if not why:
            why.append("발행 전 상태가 아니에요")
    dt = _dt(row.get("date"), row.get("time"))
    if not dt:
        why.append("예약 시각을 못 읽음")
    elif dt < now + timedelta(minutes=LEAD_MIN):
        why.append(f"예약 시각이 지금보다 {LEAD_MIN}분 이상 뒤가 아니에요")
    return why


def make_plan(week: str, rows: list, picked_ids: list, now: datetime) -> dict:
    by_id = {r["id"]: r for r in rows}
    targets, excluded = [], []
    for pid in picked_ids:
        r = by_id.get(pid)
        if not r:
            excluded.append({"id": pid, "title": "(표에 없음)", "reason": "표에 없는 글"})
            continue
        why = eligibility(r, now)
        if why:
            excluded.append({"id": pid, "title": r["title"], "reason": " · ".join(why)})
        else:
            targets.append({"id": r["id"], "title": r["title"], "date": r["date"], "time": r["time"], "wd": r["wd"],
                            "category": r["category"], "tag_text": r["tag_text"]})
    targets.sort(key=lambda t: (t["date"], t["time"]))
    return {"week": week, "targets": targets, "excluded": excluded}


class BatchRunner:
    """일괄 발행 한 번에 하나만. 확인 창에서 받은 토큰은 한 번만 쓸 수 있다(연타·새로고침으로 두 번 못 보냄)."""

    def __init__(self):
        self._tok_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self.tokens = {}
        self.state = None

    def new_token(self, plan: dict) -> str:
        tok = uuid.uuid4().hex
        with self._tok_lock:
            self.tokens[tok] = plan
        return tok

    def running(self) -> bool:
        return bool(self.state and self.state.get("status") == "running")

    def start(self, token: str, send_fn, load_article_fn, now_fn=datetime.now, sleep_sec: float = 5.0, result_root: str = None, wait: bool = False):
        with self._tok_lock:
            plan = self.tokens.pop(token, None)
        if plan is None:
            return False, "이미 진행했거나 만료된 확인이에요. 화면을 새로 열어 다시 고르세요."
        if not self._run_lock.acquire(blocking=False):
            return False, "다른 일괄 예약 발행이 진행 중이에요."
        self.state = {"status": "running", "week": plan["week"], "started": now_fn().strftime("%Y-%m-%d %H:%M:%S"), "finished": "",
                      "excluded": plan["excluded"],
                      "items": [dict(t, result="대기", post_id="") for t in plan["targets"]]}
        th = threading.Thread(target=self._run, args=(send_fn, load_article_fn, now_fn, sleep_sec, result_root), daemon=True)
        th.start()
        if wait:
            th.join()
        return True, ""

    def _run(self, send_fn, load_article_fn, now_fn, sleep_sec, result_root):
        st = self.state
        try:
            sent_any = False
            for it in st["items"]:
                art = load_article_fn(it["id"])
                if art is None:
                    it["result"] = "건너뜀 — 앱에서 글을 못 찾음"
                    continue
                if has_post(art):  # 보내기 직전 상태 다시 확인(이미 예약·발행된 글은 두 번 보내지 않음)
                    it["result"] = "건너뜀 — 이미 워드프레스에 있음"
                    continue
                dt = _dt(it["date"], it["time"])
                if not dt or dt < now_fn() + timedelta(minutes=LEAD_MIN):
                    it["result"] = f"건너뜀 — 예약 시각이 {LEAD_MIN}분 안으로 가까워짐"
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
        except Exception as e:  # noqa: BLE001 — 예상 못 한 오류도 멈추고 기록
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


def append_result_file(st: dict, root: str = None) -> str:
    root = root or DRAFT_ROOT
    path = os.path.join(root, st["week"], "_일괄발행_결과.md")
    lines = [f"\n## 일괄 예약 발행 {st['started']} ~ {st['finished']} — {'끝' if st['status'] == 'done' else '중간에 멈춤'}", "",
             "| 제목 | 결과 | 워드프레스 글 id | 예약 시각 |", "|---|---|---|---|"]
    for it in st["items"]:
        lines.append(f"| {it['title']} | {it['result']} | {it.get('post_id') or ''} | {it['date']} {it['time']} |")
    for ex in st.get("excluded") or []:
        lines.append(f"| {ex['title']} | 제외 — {ex['reason']} | | |")
    new_file = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if new_file:
            f.write(f"# 싼세계 일괄 예약 발행 결과 (주 {st['week']})\n")
        f.write("\n".join(lines) + "\n")
    return path


RUNNER = BatchRunner()


# ───────────────────────── 화면 라우트 (app.py 에서 register_routes 로 한 번만 연결) ─────────────────────────

def register_routes(flask_app, m):
    """m = app 모듈. 이 채널(wp_tips)에서만 동작하고, 다른 채널에서 열면 첫 화면으로 보낸다."""
    from flask import redirect, render_template, request, url_for

    def _only_wp_tips():
        return m.get_active_channel_id() == "wp_tips"

    def _wp_articles():
        """백그라운드 실행 중(요청 밖)에도 쓸 수 있게 파일에서 직접 읽기."""
        try:
            return json.loads(m._read_text_retry(m.CHANNELS["wp_tips"]["articles_file"]))
        except FileNotFoundError:
            return {}

    def _screen(week, errs=None, form_vals=None, notice=""):
        now = datetime.now().replace(second=0, microsecond=0)
        weeks = list_weeks()
        week = week if week in weeks else default_week(weeks, now)
        data = build_week(week, m.load_queue(), m.load_articles(), now) if week else {"week": "", "rows": [], "summary": {"by_date": {}, "by_cat": {}, "problems": 0}}
        for r in data["rows"]:
            r["pickable"] = r["editable"] and not r["problems"]
            fv = (form_vals or {}).get(r["id"])
            r["in_val"] = f"{fv[0]}T{fv[1]}" if fv else f"{r['date']}T{r['time']}"
            r["err"] = (errs or {}).get(r["id"], "")
        return render_template("wp_batch.html", data=data, weeks=weeks, week=week, notice=notice, running=RUNNER.running(),
                               now=now.strftime("%Y-%m-%d %H:%M"))

    @flask_app.route("/wp-batch")
    def wp_batch_screen():
        if not _only_wp_tips():
            return redirect(url_for("index"))
        return _screen(request.args.get("week", ""), notice=request.args.get("notice", ""))

    @flask_app.route("/wp-batch/save", methods=["POST"])
    def wp_batch_save():
        """'변경 저장': _예약표.md 만 고친다(앱 데이터·워드프레스에는 쓰지 않음)."""
        if not _only_wp_tips():
            return redirect(url_for("index"))
        week = request.form.get("week", "")
        if week not in list_weeks():
            return redirect(url_for("wp_batch_screen"))
        now = datetime.now().replace(second=0, microsecond=0)
        data = build_week(week, m.load_queue(), m.load_articles(), now)
        editable = {r["id"] for r in data["rows"] if r["editable"]}
        changes, form_vals = {}, {}
        for r in data["rows"]:
            v = (request.form.get("dt_" + r["id"]) or "").strip()
            if r["id"] in editable and v:
                d, t = v[:10], v[11:16]
                form_vals[r["id"]] = (d, t)
                changes[r["id"]] = (d, t)
        res = save_schedule(week, changes, now, existing_scheduled(m.load_articles(), now))
        if not res["ok"]:
            return _screen(week, errs=res["errs"], form_vals=form_vals, notice="저장하지 않았어요 — 빨간 줄을 고쳐 주세요.")
        msg = f"저장했어요 ({len(res['changed'])}편 시각 변경)." if res["changed"] else "바뀐 시각이 없어요."
        return redirect(url_for("wp_batch_screen", week=week, notice=msg))

    @flask_app.route("/wp-batch/confirm", methods=["POST"])
    def wp_batch_confirm():
        """'고른 글 일괄 예약 발행' 첫 단계: 아무것도 보내지 않고 대상 목록·제외 이유만 보여 준다."""
        if not _only_wp_tips():
            return redirect(url_for("index"))
        week = request.form.get("week", "")
        if week not in list_weeks():
            return redirect(url_for("wp_batch_screen"))
        now = datetime.now().replace(second=0, microsecond=0)
        data = build_week(week, m.load_queue(), m.load_articles(), now)
        plan = make_plan(week, data["rows"], request.form.getlist("pick"), now)
        token = RUNNER.new_token(plan) if plan["targets"] else ""
        return render_template("wp_batch_confirm.html", plan=plan, token=token, week=week, running=RUNNER.running())

    def _send_one(it):
        """기존 단건 발행 라우트(/article/<id>/publish-to-wordpress)를 최종본 화면 폼과 똑같은 내용으로 부른다."""
        c = flask_app.test_client()
        with c.session_transaction() as s:
            s["channel"] = "wp_tips"
        r = c.post(f"/article/{it['id']}/publish-to-wordpress", data={
            "publish_mode": "future", "scheduled_at": f"{it['date']}T{it['time']}",
            "category": it["category"], "tag": it["tag_text"]})
        loc = r.headers.get("Location", "")
        if r.status_code == 302 and "wp_published=" in loc:
            pid = (((_wp_articles().get(it["id"]) or {}).get("platform_post_id")) or {}).get("wordpress")
            return True, "", pid
        body = re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<style.*?</style>", "", r.get_data(as_text=True)))
        return False, f"HTTP {r.status_code} " + re.sub(r"\s+", " ", body).strip()[:250], ""

    @flask_app.route("/wp-batch/run", methods=["POST"])
    def wp_batch_run():
        """확인 창에서 '예, 예약합니다'를 눌렀을 때만. 토큰은 한 번만 쓸 수 있다."""
        if not _only_wp_tips():
            return redirect(url_for("index"))
        ok, why = RUNNER.start(request.form.get("token", ""), _send_one, lambda i: _wp_articles().get(i))
        if not ok:
            return render_template("error.html", message=why), 409
        return redirect(url_for("wp_batch_progress"))

    @flask_app.route("/wp-batch/progress")
    def wp_batch_progress():
        if not _only_wp_tips():
            return redirect(url_for("index"))
        return render_template("wp_batch_progress.html", st=RUNNER.state)
