# -*- coding: utf-8 -*-
"""앱 입력(혜택주머니 longterm_wp): 06_확정.json + 원고/*.md 를 읽어 앱에 넣는다.
앱 로직은 새로 짜지 않고 기존 라우트를 app.app.test_client() 로 부른다(앱을 켜 둘 필요 없음).

기본은 미리보기(아무것도 안 씀, 할 일 목록만 보여 줌). --apply 일 때만 실제로 쓴다.
글마다 순서(--apply):
  1 키워드: approve 면 queue_id 를 승인 상태로(이미 승인이면 그대로), add 면 /add-custom-keyword (confirm_dup=1)
  2 원고 코드 블록 A(본문) → /article/<id>/paste,  B(메타데이터) → /article/<id>/save-metadata/wordpress
  3 붙여넣기 결과 parse_ok 가 아니면 그 글은 멈추고 실패 목록으로
  4 이미지: /article/<id>/gen-images  (비용 발생 — 시작 전에 총 장수·예상 비용을 보여 줌)
  5 /article/<id>/generate-final
  6 articles.json 의 그 글에 lt_plan = {scheduled_at, category, tag} (새 칸만 추가)
  7 결과 표: 순서 / 시각 / 키워드 / 단계별 성공·실패 / 최종본 주소
이미 발행된 글(platform_post_id.wordpress 있음)은 건너뛴다.
이미 앱에 다 들어간 글(같은 주·같은 키워드의 대기열 항목이 있고 원고·메타·최종본·예약 정보가 모두 있음)도 건너뛴다 → 다시 돌려도 중복 없음(--force 로 끄기).
--apply 가 끝나면 그 주 글을 글 장부(글장부.json)에 더하고 09_기획표저장.md 를 만든다(실제 데이터일 때만).

06_확정.json 형식:
{"week": "2026-11-23",
 "posts": [{"order": 1, "slot": "2026-11-23T07:44", "cluster_seed": "대표 키워드", "role": "대표",
            "keyword": "대표 키워드", "action": "approve|add", "queue_id": "있으면", "category": "정부지원금·복지",
            "draft": "원고/01_20261123-0744_대표키워드.md"}]}
draft 경로는 작업 폴더(주간기획/혜택_날짜/) 기준.

시험용 옵션: --data-dir 폴더(content_queue.json·articles.json 복사본을 둔 곳, 실제 데이터는 안 건드림), --mock-images(이미지 가짜 모드)."""
import argparse
import os
import re
from datetime import datetime

import lt_common as C

CATEGORIES = ["정부지원금·복지", "서민금융·정책자금", "보험·연금", "세금·절세", "자격증·국비교육", "생활요금·해지환불"]
COST_COVER_WON = 20  # 표지 1장 어림(원)
COST_BODY_WON = 15   # 본문 사진 1장 어림(원)
BASE_URL = "http://127.0.0.1:5000"
STEPS = ["키워드", "붙여넣기", "메타", "이미지", "최종본", "예약저장"]


def split_blocks(md: str):
    """원고 파일에서 코드 블록을 순서대로 꺼낸다. 반환: (A 본문, B 메타데이터) 없으면 None."""
    blocks = [m.group(1).strip("\n") for m in re.finditer(r"```[^\n]*\n(.*?)\n```", md, flags=re.DOTALL)]
    a = next((b for b in blocks if b.lstrip().startswith("[플랫폼:")), blocks[0] if blocks else None)
    rest = [b for b in blocks if b is not a]
    b = next((x for x in rest if "메타 디스크립션" in x or "카테고리 제안" in x), rest[0] if rest else None)
    return a, b


def validate_post(p, wdir):
    """미리보기·적용 공통 점검. 반환: (문제 목록(멈춤), 주의 목록, A, B)"""
    errs, warns = [], []
    for k in ("order", "slot", "cluster_seed", "role", "keyword", "action", "category", "draft"):
        if p.get(k) in (None, ""):
            errs.append(f"'{k}' 칸이 비어 있음")
    if p.get("action") not in ("approve", "add"):
        errs.append("action 은 approve 또는 add")
    if p.get("action") == "approve" and not p.get("queue_id"):
        errs.append("approve 인데 queue_id 가 없음")
    if p.get("slot") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", p["slot"]):
        errs.append("slot 은 YYYY-MM-DDTHH:MM 모양")
    if p.get("category") and p["category"] not in CATEGORIES:
        warns.append(f"카테고리 '{p['category']}' 는 6개 이름이 아님(새 카테고리가 생길 수 있음)")
    A = B = None
    path = os.path.join(wdir, p.get("draft") or "")
    if not p.get("draft") or not os.path.isfile(path):
        errs.append(f"원고 파일이 없음: {p.get('draft')}")
    else:
        with open(path, encoding="utf-8") as f:
            A, B = split_blocks(f.read())
        if not A:
            errs.append("원고 코드 블록 A(본문)를 못 찾음")
        elif f"제목:" not in A:
            errs.append("코드 블록 A 에 '제목:' 줄이 없음")
        if not B:
            errs.append("원고 코드 블록 B(메타데이터)를 못 찾음")
    return errs, warns, A, B


def find_queue(queue, week, keyword):
    return next((q for q in queue if q.get("week") == week and q.get("keyword") == keyword), None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", required=True, help="월요일 YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true", help="실제로 앱에 넣는다(없으면 미리보기)")
    ap.add_argument("--only", default="", help="이 순서 번호만 처리(예: 1,3)")
    ap.add_argument("--skip-images", action="store_true", help="이미지 생성 단계를 건너뜀(이미 만든 글 다시 돌릴 때)")
    ap.add_argument("--force", action="store_true", help="이미 앱에 다 들어간 글도 건너뛰지 않고 다시 처리한다")
    ap.add_argument("--data-dir", default="", help="시험용: 큐·글 복사본이 있는 폴더")
    ap.add_argument("--mock-images", action="store_true", help="시험용: 이미지를 가짜(비용 없음)로")
    a = ap.parse_args()
    week = C.check_week(a.week)
    wdir = C.week_dir(week)
    conf = C.read_json(os.path.join(wdir, "06_확정.json"))
    if not conf:
        raise SystemExit(f"{wdir}/06_확정.json 이 없습니다.")
    if conf.get("week") != week:
        raise SystemExit(f"06_확정.json 의 week({conf.get('week')}) 와 --week({week}) 가 다릅니다.")
    posts = sorted(conf.get("posts") or [], key=lambda p: p.get("order") or 0)
    if a.only:
        want = {int(x) for x in a.only.split(",") if x.strip()}
        posts = [p for p in posts if p.get("order") in want]

    app = C.load_app(a.data_dir or None)
    if a.mock_images:
        app.image_gen.USE_MOCK = True
    cfg = app.CHANNELS[C.CHANNEL]
    qpath, apath = cfg["queue_file"], cfg["articles_file"]
    queue = C.read_json(qpath, [])
    articles = C.read_json(apath, {})
    mode = "적용" if a.apply else "미리보기(아무것도 쓰지 않음)"
    print(f"[{mode}] 주차 {week} · 글 {len(posts)}편 · 큐 {qpath} · 글 {apath}")
    if a.data_dir:
        print("※ 시험용 데이터 폴더를 쓰는 중:", a.data_dir)

    # ───── 점검과 할 일 목록 ─────
    plan = []
    total_body = 0
    for p in posts:
        errs, warns, A, B = validate_post(p, wdir)
        q = None
        if p.get("action") == "approve":
            q = next((x for x in queue if x.get("id") == p.get("queue_id")), None)
            if not q:
                errs.append(f"queue_id {p.get('queue_id')} 가 대기열에 없음")
            elif q.get("keyword") != p.get("keyword"):
                errs.append(f"queue_id 의 키워드('{q.get('keyword')}')와 다름")
        else:
            q = find_queue(queue, week, p.get("keyword", ""))
        aid = (q or {}).get("id") or (app.engine.make_item_id(p["keyword"], week) if p.get("keyword") else None)
        art = articles.get(aid) or {}
        published = bool(((art.get("platform_post_id") or {}).get("wordpress")))
        n_body = 0
        if A:
            memo = app.metadata_parser.parse_metadata_memo(B or "")
            n_scene = len(memo.get("body_alt_pairs") or [])
            raw_body = re.sub(r"^\[플랫폼:[^\]]*\]\s*\n제목:[^\n]*\n", "", A.lstrip())
            n_body = n_scene if 3 <= n_scene <= 5 else app._longterm_body_image_count(raw_body)
            if not memo.get("category"):
                warns.append("메타데이터에 '카테고리 제안' 이 없음")
            elif memo["category"] != p.get("category"):
                warns.append(f"메타 카테고리 제안('{memo['category']}')과 확정표 카테고리('{p.get('category')}')가 다름")
        already = bool(
            not published and not a.force and q and q.get("week") == week and q.get("keyword") == p.get("keyword")
            and art.get("parse_ok") and (art.get("platform_raw") or {}).get("wordpress")
            and (art.get("platform_metadata") or {}).get("wordpress") and (art.get("platform_final") or {}).get("wordpress")
            and (art.get("lt_plan") or {}).get("scheduled_at"))
        todo = []
        if published:
            todo.append("이미 발행된 글 — 건너뜀")
        elif already:
            todo.append("이미 앱에 다 들어간 글(같은 주·같은 키워드·원고 있음) — 건너뜀")
        else:
            if p.get("action") == "approve":
                todo.append("1 키워드: " + ("이미 승인 — 그대로" if (q or {}).get("approved") else "승인으로 바꿈"))
            else:
                todo.append("1 키워드: " + ("대기열에 이미 있음 — 승인만 확인" if q else "직접 추가(대표/세부 · confirm_dup=1)"))
            todo += ["2 원고 A 붙여넣기 + B 메타데이터 저장", f"3 이미지 일괄 생성(표지 1 + 본문 {n_body}장)", "4 최종본 만들기",
                     f"5 예약 정보 저장 lt_plan = {p.get('slot')} · {p.get('category')} · 태그 '{p.get('cluster_seed')}'"]
        plan.append({"p": p, "aid": aid, "errs": errs, "warns": warns, "A": A, "B": B, "published": published, "already": already, "todo": todo, "n_body": n_body})

    total_body = sum(it["n_body"] for it in plan if not it["published"] and not it["already"])
    for it in plan:
        p = it["p"]
        flag = "✗ 문제" if it["errs"] else ("· 건너뜀" if (it["published"] or it["already"]) else "✓")
        print(f"\n{p.get('order')}. {p.get('slot')} {p.get('keyword')} ({p.get('role')}) [{flag}]")
        for t in it["todo"]:
            print("   -", t)
        for e in it["errs"]:
            print("   ✗", e)
        for w in it["warns"]:
            print("   ⚠", w)
    n_run = sum(1 for it in plan if not it["published"] and not it["already"] and not it["errs"])
    cost = n_run * COST_COVER_WON + total_body * COST_BODY_WON
    print(f"\n이미지: 넣을 글 {n_run}편 · 표지 {n_run}장 + 본문 약 {total_body}장 = 약 {n_run + total_body}장 · 예상 비용 어림 {cost:,}원"
          f"(표지 장당 약 {COST_COVER_WON}원, 본문 장당 약 {COST_BODY_WON}원 · 실제는 달라질 수 있음){' [가짜 이미지 모드: 비용 0]' if a.mock_images else ''}")
    bad = [it for it in plan if it["errs"]]
    if not a.apply:
        print("\n(미리보기 끝 — 실제로 넣으려면 --apply)")
        if bad:
            print(f"문제 있는 글 {len(bad)}편은 --apply 해도 건너뜁니다.")
        return
    if bad:
        print(f"\n문제 있는 글 {len(bad)}편은 건너뛰고 나머지만 진행합니다.")

    # ───── 적용 ─────
    for path in (qpath, apath):
        if os.path.exists(path):
            print("백업:", C.backup_file(path))
    client = C.make_client(app)
    results = []
    for it in plan:
        p, aid = it["p"], it["aid"]
        row = {"order": p.get("order"), "slot": p.get("slot"), "keyword": p.get("keyword"), "id": aid,
               "steps": {s: "-" for s in STEPS}, "note": "", "final_url": ""}
        results.append(row)
        if it["published"]:
            row["note"] = "이미 발행됨 — 건너뜀"
            continue
        if it["already"]:
            row["steps"] = {s_: "이미" for s_ in STEPS}
            row["note"] = "이미 앱에 다 들어간 글 — 건너뜀"
            row["final_url"] = f"{BASE_URL}/article/{aid}/final/wordpress"
            continue
        if it["errs"]:
            row["note"] = "점검 문제로 건너뜀: " + "; ".join(it["errs"])
            continue

        def fail(step, msg):
            row["steps"][step] = "실패"
            row["note"] = f"{step}: {msg}"

        # 1 키워드
        queue = C.read_json(qpath, [])
        q = next((x for x in queue if x.get("id") == p.get("queue_id")), None) if p["action"] == "approve" else find_queue(queue, week, p["keyword"])
        if not q:
            r = client.post("/add-custom-keyword", data={"week": week, "keyword": p["keyword"], "cluster_seed": p["cluster_seed"],
                                                          "role": p["role"], "confirm_dup": "1"})
            queue = C.read_json(qpath, [])
            q = find_queue(queue, week, p["keyword"])
            if not q:
                fail("키워드", f"직접 추가 실패(응답 {r.status_code})")
                continue
        if not q.get("approved"):
            client.post(f"/toggle-approved/{q['id']}", headers={"X-Requested-With": "fetch"})
            queue = C.read_json(qpath, [])
            q = next((x for x in queue if x.get("id") == q["id"]), None)
            if not (q and q.get("approved")):
                fail("키워드", "승인으로 바꾸지 못함")
                continue
        aid = q["id"]
        row["id"] = aid
        row["steps"]["키워드"] = "성공"
        # 2 붙여넣기 + 메타
        r = client.post(f"/article/{aid}/paste", data={"raw_paste": it["A"]})
        art = C.read_json(apath, {}).get(aid) or {}
        if not art.get("parse_ok") or not (art.get("platform_raw") or {}).get("wordpress"):
            fail("붙여넣기", f"parse_ok 아님(응답 {r.status_code}) — 본문 앞 '[플랫폼:워드프레스]' '제목:' 줄 확인")
            continue
        row["steps"]["붙여넣기"] = "성공"
        r = client.post(f"/article/{aid}/save-metadata/wordpress", data={"metadata_text": it["B"]})
        art = C.read_json(apath, {}).get(aid) or {}
        if not ((art.get("platform_metadata") or {}).get("wordpress") or "").strip():
            fail("메타", f"메타데이터가 저장되지 않음(응답 {r.status_code})")
            continue
        row["steps"]["메타"] = "성공"
        # 3 이미지
        if a.skip_images:
            row["steps"]["이미지"] = "건너뜀"
        else:
            try:
                r = client.post(f"/article/{aid}/gen-images")
            except Exception as e:  # noqa: BLE001
                fail("이미지", f"{type(e).__name__}: {e}"[:150])
                continue
            art = C.read_json(apath, {}).get(aid) or {}
            if not (art.get("covers") or {}).get("wordpress") or not art.get("body_images"):
                fail("이미지", f"표지 또는 본문 이미지가 안 만들어짐(응답 {r.status_code})")
                continue
            row["steps"]["이미지"] = f"성공(본문 {len(art['body_images'])}장)"
        # 4 최종본
        r = client.post(f"/article/{aid}/generate-final")
        art = C.read_json(apath, {}).get(aid) or {}
        if not (art.get("platform_final") or {}).get("wordpress"):
            fail("최종본", f"최종본이 비어 있음(응답 {r.status_code})")
            continue
        row["steps"]["최종본"] = "성공"
        # 5 예약 정보 저장(새 칸 lt_plan 만 추가)
        articles = C.read_json(apath, {})
        articles[aid]["lt_plan"] = {"scheduled_at": p["slot"], "category": p["category"], "tag": p["cluster_seed"]}
        app._write_json_atomic(apath, articles)
        row["steps"]["예약저장"] = "성공"
        row["final_url"] = f"{BASE_URL}/article/{aid}/final/wordpress"

    # ───── 결과 표 ─────
    lines = ["| 순서 | 시각 | 키워드 | " + " | ".join(STEPS) + " | 최종본 주소 / 메모 |", "|" + "---|" * (len(STEPS) + 4)]
    for r in results:
        lines.append(f"| {r['order']} | {r['slot']} | {r['keyword']} | " + " | ".join(r["steps"][s] for s in STEPS)
                     + f" | {r['final_url'] or r['note']} |")
    table = "\n".join(lines)
    print("\n" + table)
    ok = sum(1 for r in results if r["steps"]["예약저장"] == "성공")
    skipped = sum(1 for r in results if r["steps"]["예약저장"] == "이미")
    print(f"\n성공 {ok}편 · 이미 들어 있어 건너뜀 {skipped}편 / 전체 {len(results)}편")
    out = os.path.join(a.data_dir or wdir, "07_앱입력결과.md")  # 시험용 데이터 폴더면 결과표도 그 안에
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 07 앱 입력 결과 ({datetime.now():%Y-%m-%d %H:%M})\n\n{table}\n")
    print("저장:", out)
    # 8차: 끝나면 그 주 글을 장부에 더하고 기획표 저장(09)을 만든다. 실패해도 앱 입력 결과는 그대로(메시지만).
    try:
        import lt_ledger
        lpath = os.path.join(a.data_dir, "글장부.json") if a.data_dir else None  # 시험용 데이터 폴더면 장부도 그 안에
        led, n_led = lt_ledger.update_ledger(app, {week}, lpath)
        print(f"글 장부에 더함: {n_led}편 → {lpath or lt_ledger.D.LEDGER_PATH}")
        if not a.data_dir:
            import subprocess
            import sys
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lt_save_plan.py"), "--week", week],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            out_txt = (r.stdout or r.stderr).strip()
            print(out_txt.splitlines()[-1] if out_txt else "기획표 저장 실행됨")
    except Exception as e:  # noqa: BLE001
        print(f"※ 장부·기획표 저장 단계에서 오류(앱 입력 결과에는 영향 없음): {type(e).__name__}: {e}"[:300])


if __name__ == "__main__":
    main()
