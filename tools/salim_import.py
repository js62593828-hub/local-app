# -*- coding: utf-8 -*-
"""N살림노트(naver_tips) 원고 → 앱 넣기 도구. 앱 코드는 고치지 않고, 앱의 기존 화면 라우트를 test_client 로 부른다.
11/20 에 11/23 주 20편을 넣은 스크립트는 못 찾아서, 11/25 시험(import_trial_20261125/salim_trial.py)과 같은 방식을 쓴다.

단계(--stage):
  text    원고 본문 → /article/<id>/paste, 메타 → /article/<id>/save-metadata/naver
  images  /article/<id>/gen-images        (표지 배경 1장 + 본문 4장, 이미지 API 비용 발생)
  final   /article/<id>/generate-final (업로드 폴더 naver_upload 포함) + /article/<id>/set-published-date/naver (발행 날짜=대기열 발행일)
기본은 '미리 보기': 대기열·articles 를 임시 폴더로 복사하고 이미지는 가짜(USE_MOCK), 외부 접속은 전부 막고 기록한다.
  미리 보기는 --stage 까지 앞 단계를 차례로 모두 돌려 본다(예: --stage final = text → images → final).
--apply 일 때만 실제 파일을 바꾼다. 이때는 --stage 로 고른 한 단계만 한다(앞 단계가 끝나 있어야 함).

사용: python -X utf8 tools/salim_import.py --week 2026-11-30 --stage final            (미리 보기, 19편)
      python -X utf8 tools/salim_import.py --week 2026-11-30 --stage text --apply     (실제)
옵션: --only 01,02,…   --force(이미 들어간 글 다시)   --no-date(발행 날짜 안 넣음)   --no-verify(파일 변경 검사 생략)
      --allow-rss(--apply 최종본 만들 때 네이버 RSS 로 다른 글 주소 채우기를 허용. 기본은 막음)
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import date, datetime

sys.dont_write_bytecode = True  # __pycache__ 를 드라이브에 만들지 않음
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH = "naver_tips"
QFILE, AFILE = "content_queue_naver_tips.json", "articles_naver_tips.json"
STAGES = ("text", "images", "final")
FN_RE = re.compile(r"^(\d{2})_(\d{4})_(\d{4})_([A-Za-z])_(.+)\.md$")  # 번호_MMDD_HHMM_종류_키워드.md
BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.S)
BLOCKED = []  # 미리 보기에서 막은 외부 호출 기록


# ── 외부 접속 막기(미리 보기) ─────────────────────────────────
def _where():
    for fs in reversed(traceback.extract_stack()):
        if fs.filename.startswith(ROOT) and os.path.basename(fs.filename) != "salim_import.py":
            return f"{fs.name} ({os.path.relpath(fs.filename, ROOT)}:{fs.lineno})"
    return "?"


def install_net_guard():
    import http.client
    import socket
    import urllib.request
    import requests

    def rec(kind, target):
        BLOCKED.append({"종류": kind, "대상": str(target)[:200], "부른 곳": _where()})

    def req(self, method, url, *a, **k):
        rec("requests", f"{method} {url}")
        raise requests.exceptions.ConnectionError("미리 보기: 외부 접속 막음")

    def uopen(url, *a, **k):
        rec("urllib", getattr(url, "full_url", url))
        raise OSError("미리 보기: 외부 접속 막음")

    def sconn(self, addr, *a, **k):
        rec("socket.connect", addr)
        raise OSError("미리 보기: 외부 접속 막음")

    def cconn(addr, *a, **k):
        rec("socket.create_connection", addr)
        raise OSError("미리 보기: 외부 접속 막음")

    def gai(host, *a, **k):
        rec("socket.getaddrinfo", host)
        raise OSError("미리 보기: 외부 접속 막음")

    def hconn(self, *a, **k):
        rec("http.client", f"{self.host}:{self.port}")
        raise OSError("미리 보기: 외부 접속 막음")

    requests.sessions.Session.request = req
    urllib.request.urlopen = uopen
    socket.socket.connect = sconn
    socket.socket.connect_ex = lambda self, addr, *a, **k: (rec("socket.connect_ex", addr), 1)[1]
    socket.create_connection = cconn
    socket.getaddrinfo = gai
    http.client.HTTPConnection.connect = hconn


# ── 원고 읽기·매칭 ───────────────────────────────────────────
def _nk(s):
    return re.sub(r"\s+", "", s or "")


def read_draft(path):
    md = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    blocks = [m.group(1) for m in BLOCK_RE.finditer(md)]
    body = next((b for b in blocks if b.lstrip().startswith("[플랫폼:네이버]")), None)
    meta = next((b for b in blocks if b.lstrip().startswith("메타 디스크립션")), None)
    title = ""
    if body:
        m = re.search(r"^제목:[ \t]*(.+)$", body, re.M)
        title = m.group(1).strip() if m else ""
    return body, meta, title


def list_drafts(week, only):
    d = os.path.join(ROOT, "pipeline", "salim_drafts", week)
    rows = []
    for p in sorted(glob.glob(os.path.join(d, "*.md"))):
        m = FN_RE.match(os.path.basename(p))
        if not m:
            continue
        no, mmdd, hhmm, typ, kw = m.groups()
        if only and no not in only:
            continue
        rows.append({"no": no, "mmdd": mmdd, "hm": hhmm[:2] + ":" + hhmm[2:], "type": typ.upper(), "kw": kw, "path": p})
    return rows


def match_queue(queue, week, row):
    hits = [q for q in queue if q.get("week") == week and _nk(q.get("keyword")) == _nk(row["kw"])
            and str(((q.get("plan") or {}).get("publish_date")) or "").replace("-", "")[4:8] == row["mmdd"]]
    return hits


def state_of(a):
    """글 상태: 빈 칸 / 작성 중 / 최종본 / 예약 / 발행 (네이버 칸 기준)."""
    if not a:
        return "빈 칸"
    g = lambda k: ((a.get(k) or {}).get("naver") or "")
    pa, pu = str(g("platform_published_at")), str(g("platform_published_url"))
    if pu or (pa and pa <= date.today().isoformat()):
        return "발행"
    if pa:
        return "예약"
    if g("platform_final"):
        return "최종본"
    if g("platform_raw"):
        return "작성 중"
    return "빈 칸"


def has_images(a):
    return bool(((a.get("covers") or {}).get("naver"))) and len((a.get("platform_body_images") or {}).get("naver") or []) > 0


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def snap(root):
    d = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x != "__pycache__"]
        for f in fn:
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
                d[p] = (st.st_size, int(st.st_mtime))
            except OSError:
                pass
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--stage", choices=STAGES, default="final")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-date", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--allow-rss", action="store_true")
    ap.add_argument("--out", default="", help="미리 보기 임시 폴더(기본: 시스템 임시 폴더에 새로 만듦)")
    args = ap.parse_args()
    only = {x.strip().zfill(2) for x in args.only.split(",") if x.strip()}
    preview = not args.apply
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "tools"))

    real = {k: os.path.join(ROOT, k) for k in (QFILE, AFILE)}
    h0 = {k: sha(v) for k, v in real.items()}
    s0 = {} if (args.no_verify or not preview) else snap(ROOT)
    T = ""
    if preview:
        T = args.out or tempfile.mkdtemp(prefix="salim_import_preview_")
        os.makedirs(T, exist_ok=True)
        install_net_guard()

    drafts = list_drafts(args.week, only)
    if not drafts:
        print("원고 파일이 없습니다:", args.week)
        return 1

    import app as appmod
    ig = appmod.image_gen
    cfg = appmod.CHANNELS[CH]
    if preview:
        cfg["queue_file"], cfg["articles_file"] = (os.path.join(T, QFILE), os.path.join(T, AFILE))
        shutil.copyfile(real[QFILE], cfg["queue_file"])
        shutil.copyfile(real[AFILE], cfg["articles_file"])
        ig.USE_MOCK, ig.OPENAI_API_KEY = True, ""
        safe = os.path.join(T, "generated_test")
        os.makedirs(safe, exist_ok=True)

        def _ctx(article_id, _ig=ig):
            out = os.path.join(safe, article_id)
            os.makedirs(out, exist_ok=True)
            _ig.set_output_context(out)
            return out
        appmod._apply_image_output_context = _ctx
        ig.OUTPUT_DIR = safe
    else:
        if args.stage == "images" and (ig.USE_MOCK or not ig.OPENAI_API_KEY):
            print("멈춤: OPENAI_API_KEY 가 없어 가짜 이미지가 실제 폴더에 들어갑니다. 키를 확인하세요.")
            return 2
        if not args.allow_rss:
            appmod._fetch_naver_rss = lambda: None  # 최종본 만들기 앞의 '다른 글 주소 채우기'(네이버 RSS 읽기·다른 글 저장)를 막음
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        for k, v in real.items():
            b = k[:-5] + f".backup_before_salim_import_{args.stage}_{stamp}.json"
            shutil.copyfile(v, os.path.join(ROOT, b))
            print("백업:", b)
    qpath, apath = cfg["queue_file"], cfg["articles_file"]
    client = appmod.app.test_client()
    client.get(f"/switch-channel/{CH}")
    import salim_upload
    chain = STAGES[: STAGES.index(args.stage) + 1] if preview else (args.stage,)

    def load_a():
        return json.load(open(apath, encoding="utf-8"))

    queue = json.load(open(qpath, encoding="utf-8"))
    rows = []
    for r in drafts:
        row = {"번호": r["no"], "키워드": r["kw"], "종류": r["type"], "파일 시각": r["hm"], "대기열 id": "-", "매칭": "", "상태": "", "처리": "",
               "대기열 확정 제목": "", "들어간 제목": "", "원고 제목": "", "parse_ok": "", "메타 길이": "", "표지·본문": "", "최종본 길이": "",
               "C 상품": "-", "published_at": "", "업로드 폴더 예약 시각": ""}
        rows.append(row)
        try:
            body, meta, dtitle = read_draft(r["path"])
            row["원고 제목"] = dtitle
            hits = match_queue(queue, args.week, r)
            if len(hits) != 1:
                row["매칭"] = f"{len(hits)}개 → 건너뜀"
                row["처리"] = "건너뜀(매칭)"
                continue
            q = hits[0]
            aid = q["id"]
            row["대기열 id"], row["매칭"] = aid, "1개"
            row["대기열 확정 제목"] = (q.get("plan") or {}).get("final_title", "")
            if not body or not meta:
                row["처리"] = "건너뜀(원고 코드 칸 못 찾음)"
                continue
            a0 = load_a().get(aid)
            st0 = state_of(a0)
            row["상태"] = st0
            done = []
            for stg in chain:
                a = load_a().get(aid) or {}
                st = state_of(a)
                n = lambda k: ((a.get(k) or {}).get("naver") or "")
                if st == "발행":
                    done.append(f"{stg}: 건너뜀(발행)")
                    break
                if stg == "text":
                    if (st != "빈 칸" or n("platform_raw") or a.get("raw_paste")) and not args.force:
                        done.append(f"text: 건너뜀({st}, platform_raw 있음)")
                        break
                    c1 = client.post(f"/article/{aid}/paste", data={"raw_paste": body}).status_code
                    c2 = client.post(f"/article/{aid}/save-metadata/{'naver'}", data={"metadata_text": meta}).status_code
                    done.append(f"text {c1}/{c2}")
                elif stg == "images":
                    if not n("platform_raw") or not a.get("parse_ok"):
                        done.append("images: 건너뜀(원고 안 들어감)")
                        break
                    if (has_images(a) or st in ("최종본", "예약")) and not args.force:
                        done.append(f"images: 건너뜀({st}, 이미지 있음)")
                        break
                    c = client.post(f"/article/{aid}/gen-images").status_code
                    done.append(f"images {c}")
                else:
                    if not n("platform_raw") or not has_images(a):
                        done.append("final: 건너뜀(원고·이미지 아직)")
                        break
                    if n("platform_final") and not args.force:
                        done.append(f"final: 건너뜀({st}, 최종본 있음)")
                        break
                    c = client.post(f"/article/{aid}/generate-final").status_code
                    done.append(f"final {c}")
                    if not args.no_date:
                        pd = str((q.get("plan") or {}).get("publish_date") or "")
                        if pd:
                            c = client.post(f"/article/{aid}/set-published-date/naver", data={"published_date": pd, "published_url": ""}).status_code
                            done.append(f"date {c}")
            row["처리"] = " · ".join(done)
            # ── 결과 읽기 ──
            A = load_a()
            a = A.get(aid) or {}
            g = lambda k: ((a.get(k) or {}).get("naver") or "")
            row["들어간 제목"] = g("platform_title")
            row["parse_ok"] = a.get("parse_ok")
            row["메타 길이"] = len(g("platform_metadata"))
            row["표지·본문"] = f"{1 if g('covers') else 0}·{len(g('platform_body_images') or [])}"
            fin = g("platform_final")
            row["최종본 길이"] = len(fin)
            row["published_at"] = g("platform_published_at") or "-"
            row["원고 일치"] = (a.get("raw_paste") or "").replace("\r\n", "\n") == body and g("platform_metadata").replace("\r\n", "\n").strip() == meta.strip()
            row["제목 일치"] = (row["들어간 제목"] == dtitle)
            raw = g("platform_raw")
            if raw:
                plan = salim_upload.plan_products(raw)
                ok = sum(1 for p in plan if p["found"])
                inf = sum(1 for p in plan if p["link"] and p["link"] in fin) if fin else 0
                row["C 상품"] = f"카드 {len(plan)} · links.json 일치 {ok}/{len(plan)}" + (f" · 최종본 안 링크 {inf}/{len(plan)}" if fin else "")
                row["_cards"], row["_ok"] = len(plan), ok
            try:
                il = salim_upload.info_line(q, a)
                row["업로드 폴더 예약 시각"] = (il["when"] or "-") + (f" ({il['note'][:14]}…)" if il.get("note") else "")
            except Exception as e:  # noqa: BLE001
                row["업로드 폴더 예약 시각"] = "읽기 실패 " + type(e).__name__
            base = salim_upload.photo_dir(a)
            row["업로드 폴더 파일수"] = len(os.listdir(os.path.join(base, "naver_upload"))) if base and os.path.isdir(os.path.join(base, "naver_upload")) else 0
        except Exception as e:  # noqa: BLE001
            row["처리"] = "오류 " + type(e).__name__ + ": " + str(e)[:120]
            row["_trace"] = traceback.format_exc()[-600:]

    # ── 요약 출력 ──
    cols = ["번호", "대기열 id", "매칭", "상태", "처리", "대기열 확정 제목", "들어간 제목", "parse_ok", "메타 길이", "표지·본문", "최종본 길이", "C 상품", "published_at", "업로드 폴더 예약 시각"]
    print("\n| " + " | ".join(cols) + " |\n|" + "---|" * len(cols))
    for row in rows:
        print("| " + " | ".join(str(row.get(c, "")).replace("|", "/") for c in cols) + " |")
    errs = [r for r in rows if str(r["처리"]).startswith("오류")]
    print(f"\n편수 {len(rows)} · 오류 {len(errs)}")

    res = {"week": args.week, "stage": args.stage, "preview": preview, "rows": rows, "blocked": BLOCKED}
    if preview:
        h1 = {k: sha(v) for k, v in real.items()}
        res.update(hash_before=h0, hash_after=h1, real_same=(h0 == h1), temp=T)
        print("\n실제 queue·articles 해시 전후 같음:", h0 == h1)
        if not args.no_verify:
            s1 = snap(ROOT)
            ch = [p for p in set(s0) | set(s1) if s0.get(p) != s1.get(p) and not p.startswith(T)]
            res.update(changed_in_drive=ch[:50], n_changed_in_drive=len(ch))
            print("드라이브 안 다른 파일 변경:", len(ch), ch[:10])
        print(f"막은 외부 호출 {len(BLOCKED)}건")
        for b in BLOCKED[:40]:
            print("  ", b)
        with open(os.path.join(T, "salim_import_report.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("결과 파일:", os.path.join(T, "salim_import_report.json"))
    else:
        h1 = {k: sha(v) for k, v in real.items()}
        print("바뀐 파일:", [k for k in h0 if h0[k] != h1[k]])
    return 0 if not errs else 3


if __name__ == "__main__":
    sys.exit(main())
