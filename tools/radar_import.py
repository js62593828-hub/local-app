# -*- coding: utf-8 -*-
"""N데스크피커 원고 파일 → radar_naver 대기열 항목의 글 칸(본문·메모)에 넣는 도구.

사용: python -X utf8 tools/radar_import.py pipeline/radar_drafts/<주 시작일> [--apply] [--update]
- 기본은 미리보기(아무것도 고치지 않음). --apply 일 때만 articles_radar_naver.json 에 쓴다(쓰기 전 백업 + SHA-256 기록).
- 원고 파일 이름: <MMDD>_<순번 1~3>_<A|B>_<짧은이름>.md  (순번 1 = A, 순번 2·3 = B). 폴더 이름 = 주 시작일(YYYY-MM-DD).
- 파일 안: 본문(1줄 [플랫폼:네이버], 2줄 제목: …) → "[메모]" 한 줄 → 메모.
- 짝짓기: 본문 2번째 줄 '제목:' 값 = 대기열 항목의 제목(plan.final_title)과 정확히 같은 항목.
- 넣는 곳: 화면에서 손으로 붙일 때(app.py paste_content / save_metadata)와 같은 칸
  platform_raw·platform_title·platform_metadata["naver"] 등. 대기열 파일(content_queue_radar_naver.json)은 읽기만 한다.
- 이미 원고가 있는 항목은 건너뜀. 내용이 다르면 '고친 판 있음' 표시만, --update 를 같이 줄 때만 바꿔 넣는다.
- radar_check 위반이 1개라도 있는 파일은 --apply 해도 넣지 않는다.
앱 코드(app.py)는 import 하지 않는다(content_parser·radar_check 만 사용)."""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import content_parser  # noqa: E402
import metadata_parser  # noqa: E402
import radar_check as rc  # noqa: E402

QUEUE = os.path.join(ROOT, "content_queue_radar_naver.json")
ARTICLES = os.path.join(ROOT, "articles_radar_naver.json")
NAME_RX = re.compile(r"^(\d{4})_([1-3])_([AB])_(.+)\.md$")
SUMMARY_HEADING_RX = re.compile(r"^##[ \t]+바쁜[ \t]*분을[ \t]*위한[ \t]*3줄[ \t]*요약[ \t]*$", re.M)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def norm(t):
    return "\n".join(l.rstrip() for l in (t or "").replace("\r\n", "\n").split("\n")).strip()


def check_name(fn, week_start):
    """파일 이름 검사 → (오류 목록, 경고 목록)"""
    m = NAME_RX.match(fn)
    if not m:
        return [f"파일 이름이 '<MMDD>_<순번 1~3>_<A|B>_<짧은이름>.md' 규칙이 아니에요"], []
    mmdd, seq, ab = m.group(1), int(m.group(2)), m.group(3)
    errs, warns = [], []
    if (seq == 1) != (ab == "A"):
        errs.append(f"순번 {seq} 과 {ab} 가 안 맞아요(순번 1 = A, 순번 2·3 = B)")
    if week_start:
        try:
            d = date(week_start.year, int(mmdd[:2]), int(mmdd[2:]))
            if not week_start <= d <= week_start + timedelta(days=6):
                warns.append(f"날짜 {mmdd} 가 폴더의 주({week_start}~{week_start + timedelta(days=6)}) 밖이에요")
        except ValueError:
            errs.append(f"날짜 {mmdd} 가 올바르지 않아요")
    return errs, warns


def memo_for_store(memo):
    return norm(memo)


def radar_sync(article):
    """app.py _radar_sync 와 같은 일: 메모·원고에서 thread_card(3줄 요약·카드 제목)를 저장."""
    raw = article["platform_raw"].get("naver") or ""
    body = raw
    m = SUMMARY_HEADING_RX.search(body)
    lines = []
    if m:
        rest = body[m.end():]
        nxt = re.search(r"^##[ \t]+", rest, flags=re.M)
        for ln in (rest[:nxt.start()] if nxt else rest).splitlines():
            s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).replace("**", "").strip()
            if s:
                lines.append(s)
    rm = metadata_parser.parse_radar_memo((article.get("platform_metadata") or {}).get("naver") or "")
    if rm["post_type"]:
        article["post_type"] = rm["post_type"]
    article["thread_card"] = {"summary_lines": lines[:3], "card_title": rm["card_title"]}


def apply_to_article(article, body_text, memo_text):
    """app.py paste_content + save_metadata 가 저장하는 칸과 같은 칸을 채운다."""
    raw_paste = body_text.replace("\n", "\r\n")  # 브라우저 폼은 줄바꿈이 \r\n
    parsed = content_parser.parse_pasted_content(raw_paste)
    article["raw_paste"] = raw_paste
    article["last_parse_ok"] = parsed["parse_ok"]
    article["last_parsed_platforms"] = ["naver"] if parsed["platform_raw"]["naver"] else []
    for key in ("platform_title", "platform_raw", "platform_final", "platform_generated_at", "platform_metadata"):
        article.setdefault(key, {})
    article["platform_title"]["naver"] = parsed["platform_title"]["naver"]
    article["platform_raw"]["naver"] = parsed["platform_raw"]["naver"]
    article["platform_generated_at"]["naver"] = datetime.now().isoformat(timespec="seconds")
    article["platform_final"]["naver"] = ""  # 최종본은 ③ 에서 다시 만든다
    article["missing_platforms"] = [] if parsed["platform_raw"]["naver"] and parsed["platform_title"]["naver"] else ["네이버"]
    article["parse_ok"] = any(article["platform_raw"].values())
    article["platform_metadata"]["naver"] = memo_text.replace("\n", "\r\n")
    radar_sync(article)


def main():
    ap = argparse.ArgumentParser(description="N데스크피커 원고 파일 → 대기열 넣기 (기본은 미리보기)")
    ap.add_argument("folder", help="pipeline/radar_drafts/<주 시작일 YYYY-MM-DD>")
    ap.add_argument("--apply", action="store_true", help="실제로 넣기(미리보기가 기본)")
    ap.add_argument("--update", action="store_true", help="이미 원고가 있고 내용이 다른 항목도 바꿔 넣기(--apply 와 함께, 백업 후)")
    ap.add_argument("--queue", default=QUEUE, help="(시험용) 대기열 파일")
    ap.add_argument("--articles", default=ARTICLES, help="(시험용) 글 데이터 파일")
    ap.add_argument("--run", default="", help="상품명을 읽을 실행 폴더 이름(radar_check d 항목)")
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    folder = os.path.abspath(a.folder)
    try:
        week_start = datetime.strptime(os.path.basename(folder.rstrip("\\/")), "%Y-%m-%d").date()
    except ValueError:
        week_start = None
        print("⚠ 폴더 이름이 주 시작일(YYYY-MM-DD)이 아니라서 날짜 범위 검사를 건너뛰어요")
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".md"))
    queue = json.load(open(a.queue, encoding="utf-8"))
    articles = json.load(open(a.articles, encoding="utf-8")) if os.path.exists(a.articles) else {}
    by_title = {}
    for it in queue:
        t = ((it.get("plan") or {}).get("final_title") or it.get("title") or "").strip()
        if t:
            by_title.setdefault(t, []).append(it)

    rows, todo = [], []
    for fn in files:
        text = open(os.path.join(folder, fn), encoding="utf-8").read()
        nerr, nwarn = check_name(fn, week_start)
        body, memo, ferr = rc.split_draft(text)
        title = rc.draft_title(body)
        pair = by_title.get(title, [])
        chk = rc.check_draft(text, a.run)  # 상품카드 이름: 원고의 실행 폴더 approved.json 의 확정 상품 전체와 비교
        viol = chk["violations"] + len(nerr) + (0 if ferr and chk["violations"] >= len(ferr) else 0)
        viol = chk["violations"] + len(nerr)
        row = {"file": fn, "title": title, "id": "", "status": "", "viol": viol, "why": "", "warn": nwarn, "scope": chk.get("d_scope", "")}
        if nerr or ferr:
            row["status"], row["why"] = "형식 오류", " / ".join(nerr + ferr)
        else:
            m = by_title.get(title, [])
            if not m:
                row["status"] = "짝 없음"
            elif len(m) > 1:
                row["status"], row["id"] = "확인 필요", ", ".join(x["id"] for x in m)
                row["why"] = "같은 제목의 대기열 항목이 2개 이상"
            else:
                it = m[0]
                row["id"] = it["id"]
                art = articles.get(it["id"]) or {}
                has = bool((art.get("platform_raw") or {}).get("naver"))
                if not has:
                    row["status"] = "넣을 예정"
                else:
                    parsed = content_parser.parse_pasted_content(body.replace("\n", "\r\n"))
                    same = norm(parsed["platform_raw"]["naver"]) == norm(art["platform_raw"]["naver"]) and \
                        norm(memo) == norm((art.get("platform_metadata") or {}).get("naver"))
                    row["status"] = "이미 있음·같음" if same else "이미 있음·고친 판 있음"
                if viol:
                    row["status"] = "위반으로 보류"
                    row["why"] = f"radar_check 위반 {viol}개라서 넣지 않음 "
                elif row["status"] == "넣을 예정" or (row["status"] == "이미 있음·고친 판 있음" and a.update):
                    todo.append((it["id"], body, memo, row))
        rows.append(row)

    seen = {}
    for r in rows:
        if r["id"] and r["status"] not in ("형식 오류", "확인 필요"):
            seen.setdefault(r["id"], []).append(r)
    for rs in seen.values():
        if len(rs) > 1:
            for r in rs:
                r["status"], r["why"] = "확인 필요", "같은 항목에 원고 파일이 2개 이상: " + ", ".join(x["file"] for x in rs)
    dup_files = {r["file"] for r in rows if r["status"] == "확인 필요"}
    todo = [t for t in todo if t[3]["file"] not in dup_files]
    print(f"폴더: {a.folder} · 원고 파일 {len(files)}개 · 모드: {'넣기(--apply)' if a.apply else '미리보기'}{' + 바꿔 넣기(--update)' if a.update else ''}\n")
    print("| 파일명 | 제목 | 짝지은 항목 id | 상태 | 위반 수 | 비고 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        note = (r["why"] + " " + " ".join("⚠" + w for w in r["warn"]) + (" · 상품카드 " + r["scope"] if r.get("scope") else "")).strip()
        print(f"| {r['file']} | {r['title']} | {r['id']} | {r['status']} | {r['viol']} | {note} |")
    cnt = {}
    for r in rows:
        cnt[r["status"]] = cnt.get(r["status"], 0) + 1
    print("\n합계: " + " · ".join(f"{k} {v}" for k, v in cnt.items()))
    put = [t for t in todo]
    if not a.apply:
        print(f"\n(미리보기) 넣을 파일 {len(put)}개. 아무것도 고치지 않았어요. 넣으려면 --apply 를 붙이세요.")
        return
    if not put:
        print("\n넣을 파일이 없어요. 아무것도 고치지 않았어요.")
        return
    before = sha(a.articles) if os.path.exists(a.articles) else ""
    if os.path.exists(a.articles):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = re.sub(r"\.json$", "", a.articles) + f".backup_before_radar_import_{stamp}.json"
        shutil.copy2(a.articles, bak)
        print(f"\n백업: {bak}\n  SHA-256 전: {before}")
    for aid, body, memo, row in put:
        art = articles.get(aid) or {}
        apply_to_article(art, body, memo)
        articles[aid] = art
    text = json.dumps(articles, ensure_ascii=False, indent=2)
    tmp = a.articles + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, a.articles)
    print(f"넣었어요: {len(put)}개 → {a.articles}\n  SHA-256 후: {sha(a.articles)}")


if __name__ == "__main__":
    main()
