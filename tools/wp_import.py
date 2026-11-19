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
같은 제목이 이미 대기열에 있고 글(원고)도 들어 있으면 건너뛴다('이미 있음'). 고친 원고 다시 넣기(--update)는 아직 없음.
예약 시각(v1.4 11장): 틀별 기준 시각 C 07:40 · S 12:30 · D 18:40 에서 분 단위 ±10분(끝 분 포함)을 랜덤으로 고른다.
  날짜 = 원고 파일 이름 MMDD(주 월~일 안이어야 함), 시드 = 주차 문자열(다시 돌려도 같은 시각), --reroll 이면 새로.
  같은 주 같은 '시:분' 금지, 이미 wp_tips 글에 잡힌 그 주 시각도 피한다. 이미 사람이 넣은 시각은 덮어쓰지 않는다('기존 시각 유지').
  --apply 때 날짜는 앱의 /article/<id>/set-published-date/wordpress 라우트로, 시각(HH:MM)은 platform_scheduled_time 칸에 직접 저장한다.
  이 칸만 채워서는 발행되지 않는다(워드프레스로 보내는 건 최종본 화면의 발행 버튼). 시각 뽑는 방식은 tools/lt_schedule_slots.py 를 고치지 않고 필요한 부분만 복제했다.
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
STEPS = ["키워드", "붙여넣기", "메타", "이미지", "최종본", "예약"]
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
LINK_LINE_RX = re.compile(r"^([ \t]*링크[ \t]*[:：][ \t]*)\[쿠팡링크:(\d+)\][ \t]*$", re.M)


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


def convert_links(text, links):
    """'링크: [쿠팡링크:id]' 줄을 'links[id]' HTML 한 줄로. 반환: (바뀐 글, 못 찾은 id 목록, 바꾼 수)"""
    missing, n = [], 0

    def rep(m):
        nonlocal n
        html = (links.get(m.group(2)) or "").strip()
        if not html:
            missing.append(m.group(2))
            return m.group(0)
        n += 1
        return m.group(1) + re.sub(r"\s*\n\s*", " ", html)

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
    files = sorted(f for f in os.listdir(drafts) if f.lower().endswith(".md") and not f.lower().endswith(".facts.md") and f != "_import_result.md")
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
        conv, missing, nlink = convert_links(text, links)
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
        has_art = bool(existing and (articles.get(existing["id"]) or {}).get("platform_raw", {}).get("wordpress"))
        status = "이미 있음 — 건너뜀" if has_art else ("문제 — 건너뜀" if errs else "넣을 예정")
        plan.append({"fn": fn, "frame": frame, "title": title, "category": category, "nlink": nlink, "keyword": keyword,
                     "body": body, "memo": memo, "errs": errs, "viol": viol, "existing": existing, "status": status})

    # ── 예약 날짜·시각 배정 (v1.4 11장)
    week_days = {(date.fromisoformat(week) + timedelta(days=i)).isoformat() for i in range(7)}
    own_ids = {it["existing"]["id"] for it in plan if it["existing"]}
    taken = set()  # 'HH:MM' — 같은 주 안에서 같은 시:분 금지
    for aid_, art_ in articles.items():
        if aid_ in own_ids or not isinstance(art_, dict):
            continue
        d_ = ((art_.get("platform_published_at") or {}).get("wordpress") or "")[:10]
        t_ = ((art_.get("platform_scheduled_time") or {}).get("wordpress") or "")[:5]
        if d_ in week_days and t_:
            taken.add(t_)
    todo_sched = []
    for it in plan:
        it["sched_date"], it["sched_time"], it["sched_note"] = "", "", ""
        ex = it["existing"]
        ex_art = (articles.get(ex["id"]) or {}) if ex else {}
        ex_d = ((ex_art.get("platform_published_at") or {}).get("wordpress") or "")[:10]
        ex_t = ((ex_art.get("platform_scheduled_time") or {}).get("wordpress") or "")[:5]
        dd = draft_date(it["fn"], week)
        if it["frame"] not in BASE_TIMES or dd is None:
            why = "틀(C|D|S)을 못 읽음" if it["frame"] not in BASE_TIMES else f"파일 이름 날짜가 {week} 주(월~일) 밖이거나 못 읽음"
            it["errs"].append("예약 시각: " + why)
            if it["status"] == "넣을 예정":
                it["status"] = "문제 — 건너뜀"
            continue
        it["sched_date"] = dd.isoformat()
        if ex_d or ex_t:
            it["sched_date"], it["sched_time"], it["sched_note"] = ex_d or dd.isoformat(), ex_t, "기존 시각 유지"
            if ex_t:
                taken.add(ex_t)
        elif it["status"] == "넣을 예정":
            todo_sched.append((it["fn"], it["frame"], dd))
    picked = pick_times(todo_sched, week, taken, a.reroll)
    for it in plan:
        if it["fn"] in picked:
            it["sched_time"] = picked[it["fn"]]
        elif it["status"] == "넣을 예정" and not it["sched_note"]:
            it["errs"].append("예약 시각: 고를 수 있는 분이 모두 사용 중")
            it["status"] = "문제 — 건너뜀"

    print("\n| 파일 | 제목 | 틀 | 카테고리 | 링크 수 | 검사 결과 | 예약 날짜 | 예약 시각 | 상태 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for it in plan:
        res = "통과(위반 0)" if it["viol"] == [] and not it["errs"] else ("; ".join(it["errs"]) or "-")
        sd = it["sched_date"] or "-"
        st = (it["sched_time"] or "-") + (f" ({it['sched_note']})" if it["sched_note"] else "")
        print(f"| {it['fn']} | {it['title']} | {it['frame']} | {it['category']} | {it['nlink']} | {res} | {sd} | {st} | {it['status']} |")
    todo = [it for it in plan if it["status"] == "넣을 예정"]
    cost = len(todo) * (COST_COVER_WON + BODY_IMAGES * COST_BODY_WON)
    print(f"\n넣을 글 {len(todo)}편 · 이미지 표지 {len(todo)}장 + 본문 {len(todo) * BODY_IMAGES}장 · 예상 비용 어림 {cost:,}원"
          f"{' [가짜 이미지 모드: 비용 0]' if a.mock_images else ''} · 예약 날짜·시각은 --apply 때 칸에만 채움(워드프레스 발행은 최종본 화면 버튼)")
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
        row = {"fn": it["fn"], "title": it["title"], "id": "", "sdate": "", "stime": "", "steps": {s: "-" for s in STEPS}, "note": "", "url": ""}
        results.append(row)

        def fail(step, msg):
            row["steps"][step] = "실패"
            row["note"] = f"{step}: {msg}"

        queue = rj(qpath, [])
        q = (find_by_title(queue, it["title"]) or [None])[0]
        if not q:
            r = client.post("/add-custom-keyword", data={"week": week, "keyword": it["keyword"], "cluster_seed": it["keyword"],
                                                          "role": "대표", "confirm_dup": "1"})
            queue = rj(qpath, [])
            q = next((x for x in queue if x.get("week") == week and x.get("keyword") == it["keyword"]), None)
            if not q:
                fail("키워드", f"직접 추가 실패(응답 {r.status_code})")
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
        row["sdate"], row["stime"] = it["sched_date"], it["sched_time"]
        r = client.post(f"/article/{aid}/set-published-date/wordpress", data={"published_date": it["sched_date"], "published_url": ""})
        all_art = rj(apath, {})
        art = all_art.get(aid) or {}
        if (art.get("platform_published_at") or {}).get("wordpress") != it["sched_date"]:
            fail("예약", f"예약 날짜가 저장되지 않음(응답 {r.status_code})")
            continue
        art.setdefault("platform_scheduled_time", {})["wordpress"] = it["sched_time"]
        all_art[aid] = art
        write_json_atomic(apath, all_art)
        row["steps"]["예약"] = "성공"

    lines = ["| 파일 | 대기열·글 id | " + " | ".join(STEPS) + " | 예약 날짜 | 예약 시각 | 최종본 주소 / 메모 |", "|" + "---|" * (len(STEPS) + 5)]
    for r in results:
        lines.append(f"| {r['fn']} | {r['id']} | " + " | ".join(r["steps"][s] for s in STEPS) + f" | {r['sdate'] or '-'} | {r['stime'] or '-'} | {r['note'] or r['url']} |")
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
    out = os.path.join(drafts, "_import_result.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# wp_import 결과 ({datetime.now():%Y-%m-%d %H:%M})\n\n" + "\n".join(lines) + "\n\n" + "\n".join(cmp_lines) + "\n")
    print("저장:", out)


if __name__ == "__main__":
    main()
