# -*- coding: utf-8 -*-
"""W싼세계(wp_tips) 원고 → 앱 넣기 도구 (tools/lt_auto_input.py 를 싼세계용으로 복제·변경).

사용: python -X utf8 tools/wp_import.py --week 2026-11-23 [--apply]
  기본은 미리보기(아무것도 쓰지 않음). --apply 일 때만 앱 데이터에 쓴다.
원고 폴더 pipeline/wp_drafts/<주시작일>/ 의 각 .md(*.facts.md 제외)마다:
  1 links.json 으로 '링크: [쿠팡링크:상품id]' 를 쿠팡 HTML 로 바꾼 사본을 만든다(<원고폴더>/_converted/ 에 저장, 원본 안 바뀜)
  2 wp_check --final(+research 대조) 을 사본에 돌려 위반 0개인 것만 통과
  3 대기열 항목(제목 = 원고 '제목:' 줄, plan.final_title)을 만들거나 찾고 승인으로 둔 뒤, 본문(코드 [메모] 위)은 붙여넣기 칸,
    메타데이터([메모] 아래)는 워드프레스 메타 칸에 저장
  4 이미지 일괄 생성(표지 1 + 본문 3, 앱 기본값) → 5 최종본 만들기
한 번 실행에서 제목이 같은 원고가 2편 이상이면 모두 '문제 — 제목 중복'으로 건너뛴다(예약표에도 안 넣음).
같은 제목이 이미 대기열에 있고 글(원고)도 들어 있으면 건너뛴다('이미 있음'). 고친 원고 다시 넣기(--update)는 아직 없음.
예약 시각(v1.4 11장): 틀별 기준 시각 C 07:40 · S 12:30 · D 18:40 에서 분 단위 ±10분(끝 분 포함)을 랜덤으로 고른다.
  날짜 = 원고 파일 이름 MMDD(주 월~일 안이어야 함), 시드 = 주차 문자열(다시 돌려도 같은 시각), --reroll 이면 새로.
  같은 주 같은 '시:분' 금지, 앱 wp_tips 글(읽기만)과 <원고폴더>/_예약표.md 에 이미 있는 그 주 시각도 피한다.
  _예약표.md 에 이미 있는 글(제목 같음)은 시각을 그대로 유지한다(--reroll 이면 새로 뽑음).
  예약 날짜·시각은 _예약표.md 를 보고 최종본 화면에서 사람이 입력한다. 앱 데이터에는 예약 관련 값을 아무것도 쓰지 않는다
  (set-published-date 호출·platform_scheduled_time 저장 없음). 표는 미리보기에서도 출력하고 --apply 때 파일로 저장한다.
  시각 뽑는 방식은 tools/lt_schedule_slots.py 를 고치지 않고 필요한 부분만 복제했다.
앱 로직은 새로 짜지 않고 기존 라우트를 app.app.test_client() 로 부른다. 혜택주머니(lt_common·lt_plan)는 쓰지 않는다.
키워드: 원고 옆 <원고이름>.facts.md 첫 줄 '키워드: …', 없으면 메타 '태그 제안' 의 첫 태그.

시험용 옵션: --data-dir 폴더(content_queue_wp_tips.json·articles_wp_tips.json 복사본, 실제 데이터는 안 건드림),
  --mock-images(이미지 가짜 모드, 비용 0), --drafts/--research/--links(경로 직접 지정)."""
import argparse
import json
import os
import random
import re
import shutil
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wp_check  # noqa: E402

CHANNEL = "wp_tips"
COST_COVER_WON = 20
COST_BODY_WON = 15
BODY_IMAGES = 3  # 앱 INFO_BODY_IMAGE_COUNT
BASE_URL = "http://127.0.0.1:5000"
STEPS = ["키워드", "붙여넣기", "메타", "이미지", "최종본"]
BASE_TIMES = {"C": (7, 40), "S": (12, 30), "D": (18, 40)}  # 틀별 기준 시각(v1.4 11장)
JITTER_MIN = 10  # ±10분, 끝 분 포함


def draft_date(fn, week):
    """파일 이름 MMDD 를 그 주(월~일) 안의 날짜로. 주 밖이거나 못 읽으면 None."""
    m = wp_check.NAME_RX.match(fn)
    if not m:
        return None
    mon = date.fromisoformat(week)
    for yr in (mon.year, mon.year + 1):  # 12월 말~1월 초 주 대응
        try:
            d = date(yr, int(m.group(1)[:2]), int(m.group(1)[2:]))
        except ValueError:
            continue
        if mon <= d <= mon + timedelta(days=6):
            return d
    return None


def pick_times(items, week, taken, reroll=False):
    """items: [(fn, frame, date)] 파일 이름 순. 반환 {fn: 'HH:MM'}.
    taken: 그 주에 이미 잡힌 'HH:MM' 집합(같은 주 안 같은 시:분 금지)."""
    out, used = {}, set(taken)
    for fn, frame, _d in items:
        h, mi = BASE_TIMES[frame]
        base = h * 60 + mi
        rng = random.Random(None if reroll else f"{week}|{fn}")
        pool = [t for t in range(base - JITTER_MIN, base + JITTER_MIN + 1) if f"{t // 60:02d}:{t % 60:02d}" not in used]
        if not pool:
            continue
        t = rng.choice(pool)
        out[fn] = f"{t // 60:02d}:{t % 60:02d}"
        used.add(out[fn])
    return out
SCHED_FILE = "_예약표.md"
WEEKDAYS = "월화수목금토일"
SCHED_HEAD = "| 날짜 | 요일 | 시각 | 틀 | 제목 | 대기열 id |"


def read_sched_table(path):
    """_예약표.md 의 데이터 줄 → [{date,time,frame,title,id}]. 파일이 없거나 못 읽으면 빈 목록."""
    rows = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return rows
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 6 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]) and re.fullmatch(r"\d{2}:\d{2}", cells[2]):
            rows.append({"date": cells[0], "time": cells[2], "frame": cells[3], "title": cells[4], "id": cells[5]})
    return rows


def render_sched_table(rows):
    out = [SCHED_HEAD, "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["date"], x["time"])):
        wd = WEEKDAYS[date.fromisoformat(r["date"]).weekday()]
        out.append(f"| {r['date']} | {wd} | {r['time']} | {r['frame']} | {r['title'].replace('|', '/')} | {r['id'] or '-'} |")
    return "\n".join(out)


def write_sched_file(path, week, rows):
    text = (f"# 싼세계 예약표 (주 {week})\n\n예약 날짜·시각은 이 표를 보고 최종본 화면에서 사람이 입력한다. 앱 데이터에는 저장하지 않는다.\n\n"
            + render_sched_table(rows) + "\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


LINK_LINE_RX = re.compile(r"^([ \t]*링크[ \t]*[:：][ \t]*)\[쿠팡링크:([A-Za-z0-9]+)\][ \t]*$", re.M)


def rj(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_app(data_dir):
    os.chdir(ROOT)
    import app as appmod  # noqa: E402
    if data_dir:
        cfg = appmod.CHANNELS[CHANNEL]
        cfg["queue_file"] = os.path.join(data_dir, "content_queue_wp_tips.json")
        cfg["articles_file"] = os.path.join(data_dir, "articles_wp_tips.json")
    return appmod


def backup(path):
    base, ext = os.path.splitext(path)
    dst = f"{base}.backup_before_wp_import_{date.today():%Y%m%d}{ext}"
    if os.path.exists(dst):
        dst = f"{base}.backup_before_wp_import_{datetime.now():%Y%m%d_%H%M%S}{ext}"
    shutil.copy2(path, dst)
    return dst


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


CARD_MARK = " ‖카드‖ "   # shop_formatter.WPTIPS_CARD_MARK 와 같은 글자(앱은 이 표시가 있을 때만 배너·카드·별점 줄을 만든다)
STAR_MARK = " ‖별점‖ "


def star_line(research_dir, pid):
    """research/<상품id>.md 의 별점·리뷰 수·수집일로 별점 줄 글자. 예: '★ 4.75 · 리뷰 5,300개 이상 · 11월 20일 쿠팡 기준'.
    별점·리뷰 수가 모두 없으면 빈 글자(줄을 넣지 않음). 지어내지 않는다."""
    path = os.path.join(research_dir or "", pid + ".md")
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return ""

    def val(label):
        m = re.search(r"^-\s*" + label + r"\s*[:：]\s*(.*)$", text, re.M)
        return m.group(1).strip() if m else ""
    rating = val("별점")
    rv = re.sub(r"[^\d]", "", val("리뷰 수"))
    dm = re.match(r"(\d{4})-(\d{2})-(\d{2})", val("수집일"))
    bits = []
    if re.fullmatch(r"[0-5](?:\.\d+)?", rating):
        bits.append("★ " + rating)
    if rv:
        n = int(rv)
        r = wp_check.round_reviews(n)
        bits.append("리뷰 {:,}개".format(r) + (" 이상" if n >= 100 else ""))
    if not bits:
        return ""
    if dm:
        bits.append("%d월 %d일 쿠팡 기준" % (int(dm.group(2)), int(dm.group(3))))
    return " · ".join(bits)


def convert_links(text, links, research_dir=""):
    """'링크: [쿠팡링크:id]' 줄을 바꾼다. 바꿀 때 links[id] 가
    - {"link": 일반 링크, "html": 카드 HTML} (새 형식) 이면 '링크: <일반 링크> ‖카드‖ <카드> ‖별점‖ <별점 줄>' 한 줄
      (앱이 배너·카드·별점 줄로 풀어 줌. 배너는 tools/wp_banner.json 이 비면 안 나옴)
    - 글자 HTML (옛 형식) 이면 예전처럼 '링크: <HTML>' 한 줄.
    반환: (바뀐 글, 못 찾은 id 목록, 바꾼 수)"""
    missing, n = [], 0

    def rep(m):
        nonlocal n
        pid = m.group(2)
        v = links.get(pid)
        if isinstance(v, dict):
            url, html = (v.get("link") or "").strip(), " ".join((v.get("html") or "").split())
            if not url or not html:
                missing.append(pid)
                return m.group(0)
            n += 1
            stars = star_line(research_dir, pid)
            return m.group(1) + url + CARD_MARK + html + ((STAR_MARK + stars) if stars else "")
        html = (v or "").strip()
        if not html:
            missing.append(pid)
            return m.group(0)
        n += 1
        return m.group(1) + " ".join(html.split())

    return LINK_LINE_RX.sub(rep, text), missing, n


def split_memo(text):
    lines = text.replace("\r\n", "\n").split("\n")
    idx = [i for i, l in enumerate(lines) if l.strip() == wp_check.MEMO_MARK]
    if len(idx) != 1:
        return None, None
    return "\n".join(lines[:idx[0]]).rstrip("\n"), "\n".join(lines[idx[0] + 1:]).strip("\n")


def memo_field(memo, label):
    m = re.search(r"^\s*" + re.escape(label) + r"\s*[:：]\s*(.+)$", memo or "", re.M)
    return m.group(1).strip() if m else ""


def keyword_of(path, memo):
    kw = wp_check.read_keyword(path)
    if kw:
        return kw
    tags = [t.strip().lstrip("#") for t in re.split(r"[,，、]", memo_field(memo, "태그 제안")) if t.strip()]
    return tags[0] if tags else ""


def same_keyword_items(queue, week, keyword):
    """같은 주·같은 키워드의 대기열 항목 목록."""
    return [q for q in queue if q.get("week") == week and (q.get("keyword") or "").strip() == (keyword or "").strip()]


def create_item_direct(app, week, keyword):
    """앱 '키워드 직접 추가' 라우트는 같은 주·같은 키워드가 있으면 새로 안 만들고 옛 항목으로 보내 버린다.
    제목이 다른 글은 옛 항목을 덮어쓰면 안 되므로, 앱의 같은 함수(_make_custom_item·_insert_custom_item)로 새 항목을 직접 만든다. 반환: 새 id"""
    import flask
    with app.app.test_request_context():
        flask.session["channel"] = CHANNEL
        queue = app.load_queue()
        item = app._make_custom_item(keyword, week, keyword, "대표")
        app._insert_custom_item(queue, item)
        app.save_queue(queue)
        return item["id"]


def find_by_title(queue, title):
    return [q for q in queue if ((q.get("plan") or {}).get("final_title") or "").strip() == title]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", required=True, help="월요일 YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true", help="실제로 앱에 넣는다(없으면 미리보기)")
    ap.add_argument("--only", default="", help="이 파일 이름 조각이 들어간 원고만")
    ap.add_argument("--drafts", default="", help="원고 폴더(기본 pipeline/wp_drafts/<주>)")
    ap.add_argument("--research", default="", help="research 폴더(기본 pipeline/wp_inbox/<주>/research)")
    ap.add_argument("--links", default="", help="links.json(기본 pipeline/wp_inbox/<주>/links.json)")
    ap.add_argument("--data-dir", default="", help="시험용: 큐·글 복사본 폴더")
    ap.add_argument("--reroll", action="store_true", help="예약 시각을 새로 뽑는다(기본은 주차 문자열 시드라 다시 돌려도 같음)")
    ap.add_argument("--mock-images", action="store_true", help="시험용: 가짜 이미지(비용 0)")
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.week) or date.fromisoformat(a.week).weekday() != 0:
        sys.exit("--week 는 월요일 YYYY-MM-DD 여야 합니다.")
    week = a.week
    drafts = os.path.abspath(a.drafts or os.path.join(ROOT, "pipeline", "wp_drafts", week))
    research = os.path.abspath(a.research or os.path.join(ROOT, "pipeline", "wp_inbox", week, "research"))
    links = rj(os.path.abspath(a.links or os.path.join(ROOT, "pipeline", "wp_inbox", week, "links.json")), {})
    if not os.path.isdir(drafts):
        sys.exit(f"원고 폴더가 없어요: {drafts}")
    files = sorted(f for f in os.listdir(drafts) if f.lower().endswith(".md") and not f.lower().endswith(".facts.md") and f not in ("_import_result.md", SCHED_FILE))
    if a.only:
        files = [f for f in files if a.only in f]
    conv_dir = os.path.join(drafts, "_converted")

    app = load_app(a.data_dir or None)
    if a.mock_images:
        app.image_gen.USE_MOCK = True
    cfg = app.CHANNELS[CHANNEL]
    qpath, apath = cfg["queue_file"], cfg["articles_file"]
    queue = rj(qpath, [])
    articles = rj(apath, {})
    print(f"[{'적용' if a.apply else '미리보기(아무것도 쓰지 않음)'}] 주차 {week} · 원고 {len(files)}편 · 큐 {qpath} · 글 {apath}")
    if a.data_dir:
        print("※ 시험용 데이터 폴더를 쓰는 중:", a.data_dir)

    plan = []
    for fn in files:
        path = os.path.join(drafts, fn)
        text = open(path, encoding="utf-8").read()
        m = wp_check.NAME_RX.match(fn)
        frame = m.group(3) if m else "?"
        conv, missing, nlink = convert_links(text, links, research)
        body, memo = split_memo(conv)
        title = ""
        if body:
            tm = re.match(r"^제목\s*:\s*(\S.*)$", (body.split("\n") + ["", ""])[1].strip())
            title = tm.group(1).strip() if tm else ""
        category = memo_field(memo, "카테고리 제안")
        keyword = keyword_of(path, memo)
        errs = []
        if missing:
            errs.append("links.json 에 없는 상품id: " + ", ".join(sorted(set(missing))))
        if body is None:
            errs.append("'[메모]' 줄이 정확히 1개가 아님")
        if not title:
            errs.append("제목을 못 읽음")
        if not keyword:
            errs.append("키워드를 못 읽음(.facts.md 첫 줄 '키워드: …' 필요)")
        viol = None
        if not errs:
            os.makedirs(conv_dir, exist_ok=True)
            cpath = os.path.join(conv_dir, fn)
            with open(cpath, "w", encoding="utf-8", newline="\n") as f:
                f.write(conv)
            shutil.copy2(re.sub(r"\.md$", ".facts.md", path), re.sub(r"\.md$", ".facts.md", cpath)) \
                if os.path.isfile(re.sub(r"\.md$", ".facts.md", path)) else None
            V, W, _ = wp_check.check_file(cpath, research if os.path.isdir(research) else "", final=True, keyword=keyword)
            viol = V
            if V:
                errs.append(f"wp_check --final 위반 {len(V)}개: " + "; ".join(f"{v[0]}({v[1]}줄)" for v in V[:4]) + (" …" if len(V) > 4 else ""))
        same = find_by_title(queue, title) if title else []
        existing = same[0] if same else None
        warns = []
        if title and keyword and not existing:  # 제목이 다른데 같은 주·같은 키워드 항목이 이미 있으면 경고(덮어쓰지 않고 새 항목으로 만듦)
            for o in same_keyword_items(queue, week, keyword):
                warns.append("경고: 같은 주·같은 키워드 항목이 이미 있음(%s, 제목 '%s') — 덮어쓰지 않고 새 항목으로 만듦"
                             % (o.get("id"), ((o.get("plan") or {}).get("final_title") or "(제목 없음)")[:30]))
        has_art = bool(existing and (articles.get(existing["id"]) or {}).get("platform_raw", {}).get("wordpress"))
        status = "이미 있음 — 건너뜀" if has_art else ("문제 — 건너뜀" if errs else "넣을 예정")
        plan.append({"fn": fn, "frame": frame, "title": title, "category": category, "nlink": nlink, "keyword": keyword,
                     "body": body, "memo": memo, "errs": errs, "viol": viol, "existing": existing, "status": status, "warns": warns})

    # ── 제목 중복: 한 번 실행에서 제목이 같은 원고가 2편 이상이면 모두 건너뜀(뒤 원고가 앞 원고를 덮어쓰는 것 방지)
    title_files = {}
    for it in plan:
        if it["title"]:
            title_files.setdefault(it["title"], []).append(it["fn"])
    for it in plan:
        fl = title_files.get(it["title"], [])
        if len(fl) >= 2:
            it["dup"] = True
            it["errs"].append("제목 중복 — 같은 제목 원고 " + str(len(fl)) + "편: " + ", ".join(fl))
            it["status"] = "문제 — 제목 중복"

    # ── 예약 날짜·시각 배정 (v1.4 11장) — 앱에는 쓰지 않고 _예약표.md 에만 남긴다
    week_days = {(date.fromisoformat(week) + timedelta(days=i)).isoformat() for i in range(7)}
    sched_path = os.path.join(drafts, SCHED_FILE)
    saved_rows = read_sched_table(sched_path)
    saved_by_key = {(r["title"], r["frame"]): r for r in saved_rows}  # 같은 제목이어도 틀이 다르면 다른 글
    own_ids = {it["existing"]["id"] for it in plan if it["existing"]}
    taken = set()  # 'HH:MM' — 같은 주 안에서 같은 시:분 금지
    for aid_, art_ in articles.items():  # 앱 글에 이미 잡힌 시각(읽기만)
        if aid_ in own_ids or not isinstance(art_, dict):
            continue
        d_ = ((art_.get("platform_published_at") or {}).get("wordpress") or "")[:10]
        t_ = ((art_.get("platform_scheduled_time") or {}).get("wordpress") or "")[:5]
        if d_ in week_days and t_:
            taken.add(t_)
    redo = {(it["title"], it["frame"]) for it in plan if a.reroll and it["status"] == "넣을 예정"}  # 새로 뽑을 글은 표의 옛 시각을 피하지 않음
    for r in saved_rows:
        if r["date"] in week_days and (r["title"], r["frame"]) not in redo:
            taken.add(r["time"])
    # 같은 날 S(단독 추천)가 둘이면 순번이 작은 쪽은 C 시간대(07:30~07:50)를 쓴다(11/20 결정)
    band_of, s_by_day = {}, {}
    for it in plan:
        d0 = draft_date(it["fn"], week)
        if it["frame"] == "S" and d0 is not None:
            s_by_day.setdefault(d0, []).append(it["fn"])
    for d0, fns in s_by_day.items():
        if len(fns) >= 2:
            band_of[sorted(fns)[0]] = "C"
    todo_sched = []
    for it in plan:
        it["sched_date"], it["sched_time"], it["sched_note"] = "", "", ""
        dd = draft_date(it["fn"], week)
        if it["frame"] not in BASE_TIMES or dd is None:
            why = "틀(C|D|S)을 못 읽음" if it["frame"] not in BASE_TIMES else f"파일 이름 날짜가 {week} 주(월~일) 밖이거나 못 읽음"
            it["errs"].append("예약 시각: " + why)
            if it["status"] == "넣을 예정":
                it["status"] = "문제 — 건너뜀"
            continue
        if it.get("dup"):
            continue
        row = saved_by_key.get((it["title"], it["frame"]))
        if it["status"] != "넣을 예정":
            if row:
                it["sched_date"], it["sched_time"], it["sched_note"] = row["date"], row["time"], "표 시각 유지"
            continue
        it["sched_date"] = dd.isoformat()
        if row and not a.reroll:
            it["sched_time"], it["sched_note"] = row["time"], "표 시각 유지"
        else:
            todo_sched.append((it["fn"], band_of.get(it["fn"], it["frame"]), dd))
    picked = pick_times(todo_sched, week, taken, a.reroll)
    for it in plan:
        if it["fn"] in picked:
            it["sched_time"] = picked[it["fn"]]
        elif it["status"] == "넣을 예정" and not it["sched_time"]:
            it["errs"].append("예약 시각: 고를 수 있는 분이 모두 사용 중")
            it["status"] = "문제 — 건너뜀"

    def build_rows(ids=None):
        ids = ids or {}
        mine = [{"date": it["sched_date"], "time": it["sched_time"], "frame": it["frame"], "title": it["title"],
                 "id": ids.get((it["title"], it["frame"])) or (it["existing"] or {}).get("id") or (saved_by_key.get((it["title"], it["frame"])) or {}).get("id") or "-"}
                for it in plan if it["sched_time"] and it["sched_date"]]
        have = {(r["title"], r["frame"]) for r in mine}
        return mine + [r for r in saved_rows if (r["title"], r["frame"]) not in have]

    print("\n| 파일 | 제목 | 틀 | 카테고리 | 링크 수 | 검사 결과 | 상태 |")
    print("|---|---|---|---|---|---|---|")
    for it in plan:
        res = "통과(위반 0)" if it["viol"] == [] and not it["errs"] else ("; ".join(it["errs"]) or "-")
        if it.get("warns"):
            res += " / " + "; ".join(it["warns"])
        print(f"| {it['fn']} | {it['title']} | {it['frame']} | {it['category']} | {it['nlink']} | {res} | {it['status']} |")
    print(f"\n예약표 ({SCHED_FILE}) — 같은 시:분 금지·틀별 기준 ±{JITTER_MIN}분" + "".join(f"\n  · {it['fn']}: {it['sched_note']}" for it in plan if it["sched_note"]))
    print(render_sched_table(build_rows()))
    todo = [it for it in plan if it["status"] == "넣을 예정"]
    cost = len(todo) * (COST_COVER_WON + BODY_IMAGES * COST_BODY_WON)
    print(f"\n넣을 글 {len(todo)}편 · 이미지 표지 {len(todo)}장 + 본문 {len(todo) * BODY_IMAGES}장 · 예상 비용 어림 {cost:,}원"
          f"{' [가짜 이미지 모드: 비용 0]' if a.mock_images else ''} · 예약 날짜·시각은 _예약표.md 를 보고 최종본 화면에서 사람이 입력(앱에는 안 씀)")
    if not a.apply:
        print("\n(미리보기 끝 — 실제로 넣으려면 --apply)")
        return
    if not todo:
        print("\n넣을 원고가 없어요. 아무것도 쓰지 않았어요.")
        return

    before_q = {q["id"]: q for q in queue}
    before_a = set(articles)
    for p in (qpath, apath):
        if os.path.exists(p):
            print("백업:", backup(p))
    client = app.app.test_client()
    client.get(f"/switch-channel/{CHANNEL}")
    results = []
    for it in todo:
        row = {"fn": it["fn"], "title": it["title"], "id": "", "steps": {s: "-" for s in STEPS}, "note": "", "url": ""}
        results.append(row)

        def fail(step, msg):
            row["steps"][step] = "실패"
            row["note"] = f"{step}: {msg}"

        queue = rj(qpath, [])
        q = (find_by_title(queue, it["title"]) or [None])[0]
        if not q:
            if same_keyword_items(queue, week, it["keyword"]):  # 같은 키워드 항목이 있으면 옛 항목을 쓰지 않고 새로 만든다
                new_id = create_item_direct(app, week, it["keyword"])
                queue = rj(qpath, [])
                q = next((x for x in queue if x.get("id") == new_id), None)
                r = None
            else:
                r = client.post("/add-custom-keyword", data={"week": week, "keyword": it["keyword"], "cluster_seed": it["keyword"],
                                                              "role": "대표", "confirm_dup": "1"})
                queue = rj(qpath, [])
                q = next((x for x in queue if x.get("week") == week and x.get("keyword") == it["keyword"]), None)
            if not q:
                fail("키워드", f"직접 추가 실패(응답 {getattr(r, 'status_code', '-')})")
                continue
        aid = q["id"]
        row["id"] = aid
        # 제목 = 원고 제목: (대기열 확정 제목)
        q.setdefault("plan", {})["final_title"] = it["title"]
        q["planned_category"] = it["category"] or q.get("planned_category")
        write_json_atomic(qpath, queue)
        if not q.get("approved"):
            client.post(f"/toggle-approved/{aid}", headers={"X-Requested-With": "fetch"})
            q = next((x for x in rj(qpath, []) if x.get("id") == aid), None)
            if not (q and q.get("approved")):
                fail("키워드", "승인으로 바꾸지 못함")
                continue
        row["steps"]["키워드"] = "성공"
        r = client.post(f"/article/{aid}/paste", data={"raw_paste": it["body"]})
        art = rj(apath, {}).get(aid) or {}
        if not art.get("parse_ok") or not (art.get("platform_raw") or {}).get("wordpress"):
            fail("붙여넣기", f"parse_ok 아님(응답 {r.status_code})")
            continue
        row["steps"]["붙여넣기"] = "성공"
        r = client.post(f"/article/{aid}/save-metadata/wordpress", data={"metadata_text": it["memo"]})
        art = rj(apath, {}).get(aid) or {}
        if not ((art.get("platform_metadata") or {}).get("wordpress") or "").strip():
            fail("메타", f"메타데이터가 저장되지 않음(응답 {r.status_code})")
            continue
        row["steps"]["메타"] = "성공"
        try:
            r = client.post(f"/article/{aid}/gen-images")
        except Exception as e:  # noqa: BLE001
            fail("이미지", f"{type(e).__name__}: {e}"[:150])
            continue
        art = rj(apath, {}).get(aid) or {}
        bi = (art.get("platform_body_images") or {}).get("wordpress") or art.get("body_images") or []
        if not (art.get("covers") or {}).get("wordpress") or not bi:
            fail("이미지", f"표지 또는 본문 이미지가 안 만들어짐(응답 {r.status_code})")
            continue
        row["steps"]["이미지"] = f"성공(본문 {len(bi)}장)"
        r = client.post(f"/article/{aid}/generate-final")
        art = rj(apath, {}).get(aid) or {}
        if not (art.get("platform_final") or {}).get("wordpress"):
            fail("최종본", f"최종본이 비어 있음(응답 {r.status_code})")
            continue
        row["steps"]["최종본"] = "성공"
        row["url"] = f"{BASE_URL}/article/{aid}/final/wordpress"

    lines = ["| 파일 | 대기열·글 id | " + " | ".join(STEPS) + " | 최종본 주소 / 메모 |", "|" + "---|" * (len(STEPS) + 3)]
    for r in results:
        lines.append(f"| {r['fn']} | {r['id']} | " + " | ".join(r["steps"][s] for s in STEPS) + f" | {r['note'] or r['url']} |")
    after_q = {q["id"]: q for q in rj(qpath, [])}
    after_a = rj(apath, {})
    cmp_lines = ["| 구분 | 백업(전) | 적용 후 |", "|---|---|---|",
                 f"| 대기열 항목 수 | {len(before_q)} | {len(after_q)} |", f"| 글 수 | {len(before_a)} | {len(after_a)} |",
                 f"| 새로 생긴 대기열 id | - | {', '.join(sorted(set(after_q) - set(before_q))) or '없음'} |",
                 f"| 새로 생긴 글 id | - | {', '.join(sorted(set(after_a) - before_a)) or '없음'} |",
                 f"| 사라진 id | - | {', '.join(sorted((set(before_q) - set(after_q)) | (before_a - set(after_a)))) or '없음'} |"]
    print("\n" + "\n".join(lines) + "\n\n" + "\n".join(cmp_lines))
    ok = sum(1 for r in results if r["steps"]["최종본"] == "성공")
    print(f"\n성공 {ok}편 / 전체 {len(results)}편")
    ids = {(it["title"], it["frame"]): r["id"] for it, r in zip(todo, results) if r["id"]}
    write_sched_file(sched_path, week, build_rows(ids))
    print("저장:", sched_path)
    out = os.path.join(drafts, "_import_result.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# wp_import 결과 ({datetime.now():%Y-%m-%d %H:%M})\n\n" + "\n".join(lines) + "\n\n" + "\n".join(cmp_lines) + "\n")
    print("저장:", out)


if __name__ == "__main__":
    main()
