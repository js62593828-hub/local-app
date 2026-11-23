"""11/30 주 plan 고치기(2026-11-23 2차). 새 스크립트 — 기존 plan1130_*.py 는 고치지 않는다. 읽기 + plan.md·plan.json 덮어쓰기만.
- 고치기 전 판은 stage_plan1130_run/20261123/_before_20261123b/ 에 한 번만 복사.
- B8 새 제품 두 개(샤오미 4 컴팩트·필립스 AC0650)는 raw_check 에 읽어 둔 글자에서 정리(parsed_b8new.json).
- 앱 대기열·장부·articles 는 읽기만. 검색광고는 volume_cache.json 에 있는 값만 쓴다(새 호출 0).
사용: python tools/plan1130_fix.py
"""
from __future__ import annotations
import json, re, shutil, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import product_candidates as pc  # noqa: E402
import radar_used_products as ru  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
RUN = ROOT / "pipeline" / "stage_plan1130_run" / "20261123"
ACCT = "1020008901769280"
V = json.loads((RUN / "volume_cache.json").read_text(encoding="utf-8"))
NEW_B8 = ("1012929141686688", "818401632435584")   # 샤오미 스마트 공기청정기 4 컴팩트 / 필립스 AC0650
BAN = ["후기", "내돈내산", "써보니", "사용기", "실사용", "직접", "최고", "무조건", "100%", "완벽", "강력 추천", "1위", "갈리", "갈린", "가르", "쓸모", "구조예요", "지점", "정답", "마주하게",
       "궁금한 점 정리", "대안과 차이", "이런 분께 맞아요", "살 만할까", "사기 전 확인할 점", "제가", "솔직", "찐", "인생"]


def nsp(s):
    return re.sub(r"\s", "", s)


# 칸 → 바꿀 값. 키 없는 항목은 그대로. vq = 검색량 조회어(volume_cache 키)
EDITS = {
    "A6": dict(keyword="의자허리쿠션", vq="의자허리쿠션", title="의자 허리쿠션, 허리 높이에 맞는 건 어떻게 고를까?", short="의자허리쿠션",
               note="키워드 등받이쿠션→의자허리쿠션(사용자 지정, 상품이 모두 허리 쿠션)"),
    "A7": dict(title="미니 가습기, 사기 전 물통 세척·놓을 자리 확인"),
    "B6": dict(title="필립스 TAH2000 헤드폰, 오픈형이라 사무실에서 써도 될까?"),
    "B11": dict(title="삼성 LED 스탠드 책상용, 밝기·색온도 조절 정리", end="정리", curio="밝기·색온도·각도 조절(상품명 글자) 확인; 단계 수는 자료 수집 때 확인"),
    "B3": dict(keyword="벨킨 3in1 충전기", vq="벨킨 3in1 충전기", title="벨킨 3in1 충전기 WIZ016, 아이폰·워치 같이 쓸 분께", short="벨킨3in1충전기",
               note="키워드 벨킨 WIZ016(검색량 10 미만)→벨킨 3in1 충전기(430). 모델 코드 WIZ016kr 는 상품 사양표에서 확인"),
    "B2": dict(title="노르잇 PR01A 독서대, 높이·각도 몇 단계 조절될까?", curio="자료 수집 때 값 확인: 높이·각도 단계"),
    "B12": dict(title="티피링크 L900-5 LED 스트립, 책상 밑 5m 붙여 쓸 분께", end="맞는 분", curio="5m 길이(상품명) 확인, 그 밖은 자료 수집 때 확인"),
    "B4": dict(title="니케 헤드폰 거치대 원목, 접이식 거치대와 다른 점", curio="자료 수집 때 값 확인: 비교 값"),
    "B13": dict(title="피코펄스 R7H 미니PC, 16GB·256GB로 사무용 충분할까?", curio="자료 수집 때 값 확인: 사양(16GB/256GB 는 상품명 글자)"),
    "B8": dict(keyword="샤오미 스마트 공기청정기 4", vq="샤오미 스마트 공기청정기 4", title="샤오미 스마트 공기청정기 4 컴팩트, 원룸 책상 옆에 둘 분께",
               short="샤오미공기청정기4", products=list(NEW_B8), roles=["본상품", "대안"],
               note="B8 제품 교체(에어메이드 AMP-300→샤오미 4 컴팩트, 대안 에디슨→필립스 AC0650). 2026-11-23 상품 페이지 판매 중 확인. 이름에 모델 코드 없음"),
    "B1": dict(curio="자료 수집 때 값 확인: 높이·깊이"),
    "B9": dict(curio="자료 수집 때 값 확인: 크기·전원"),
    "B5": dict(curio="자료 수집 때 값 확인: 비교 값"),
}
BIG = {"B1": ("모니터 받침대", "모니터받침대"), "B2": ("독서대", "독서대"), "B3": (None, None), "B4": ("헤드폰 거치대", "헤드폰거치대"),
       "B5": ("사운드바", "사운드바"), "B6": ("헤드폰", "헤드폰"), "B7": ("사무용 의자", "사무용의자"), "B8": ("공기청정기", "공기청정기"), "B9": ("전기방석", "전기방석"),
       "B10": ("충전식 손난로", "충전식손난로"), "B11": (None, None), "B12": ("LED 스트립", "LED스트립"), "B13": ("미니PC", "미니PC"), "B14": ("태블릿", "태블릿")}


def parse_new_b8() -> dict:
    out = {}
    for i in NEW_B8:
        d = json.loads(pc._cache_path(RUN / "raw_check", i).read_text(encoding="utf-8"))
        L = [x.strip() for x in d["bc_text"].split("\n") if x.strip()]
        k = L.index("상품 정보")
        store, name = L[k + 1], L[k + 2]
        rating = L[k + 3]
        reviews = L[k + 4].replace("리뷰", "").strip()
        price = ""
        if "할인가" in L:
            j = L.index("할인가")
            price = next((x for x in L[j + 1:j + 8] if re.fullmatch(r"[\d,]+", x)), "")   # '할인가' 뒤 첫 금액은 할인율%라 숫자만 있는 것
            nums = [x for x in L[j + 1:j + 8] if re.fullmatch(r"[\d,]{5,}", x)]
            price = nums[0] if nums else ""
        else:
            j = L.index("판매가")
            price = L[j + 1]
        out[i] = {"store": store, "name": name, "rating": rating, "reviews": reviews, "price": price,
                  "cat": d["category"], "issue": bool(d["has_issue_btn"]), "store_final": d["store_final"]}
    (RUN / "parsed_b8new.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> None:
    before = RUN / "_before_20261123b"
    if not before.exists():
        before.mkdir()
        for f in ("plan.md", "plan.json"):
            shutil.copy2(RUN / f, before / f)
    # 처음 판(고치기 전)을 항상 기준으로 다시 만든다 → 여러 번 돌려도 같은 결과
    plan = json.loads((before / "plan.json").read_text(encoding="utf-8"))
    P = json.loads((RUN / "check_parsed.json").read_text(encoding="utf-8"))
    P.update(parse_new_b8())

    rows = []
    for p in plan["plans"]:
        m = p["plan1130"]
        cell = m["칸"]
        e = EDITS.get(cell, {})
        title = e.get("title", p["final_title"])
        kw = e.get("keyword", p["keyword"])
        gal = p["radar"]["갈래"]
        fr = p["radar"]["type"]
        if "vq" in e:
            vol = V[e["vq"]]["total"]
        else:
            vol = p["monthly_total"]
        end = e.get("end", m["끝맺음"])
        curio = e.get("curio", m["궁금증"])
        note = e.get("note", m["비고"])
        short = e.get("short") or re.sub(r"^\d+_\d_[AB]_|\.md$", "", m["파일이름예정"])
        prods = e.get("products", m["제품id"])
        roles = e.get("roles", m["역할"])
        dt = p["publish_date"]
        rows.append(dict(date=dt, no=m["순번"], frame=fr, cell=cell, gal=gal, keyword=kw, vol=vol, title=title, tlen=len(title), end=end, curio=curio, note=note,
                         short=short, prods=prods, roles=roles, file=f"{dt[5:7]}{dt[8:]}_{m['순번']}_{fr}_{short}.md"))

    # ── 다시 검사 ──
    res = {}
    res["제목 40자 이내"] = (all(r["tlen"] <= 40 for r in rows), max(r["tlen"] for r in rows))
    res["키워드 앞 15자"] = [r["cell"] for r in rows if not (nsp(r["keyword"]).lower() in nsp(r["title"][:16]).lower() and len(r["keyword"]) <= 15)]
    res["금지 단어"] = [(r["cell"], [w for w in BAN if w in r["title"]]) for r in rows if any(w in r["title"] for w in BAN)]
    cA = Counter(r["end"] for r in rows if r["frame"] == "A")
    cB = Counter(r["end"] for r in rows if r["frame"] == "B")
    gb = sum(1 for r in rows if r["frame"] == "A" and "고르는 법" in r["title"])
    byday = {}
    for r in rows:
        if r["frame"] == "B":
            byday.setdefault(r["date"], []).append(r["end"])
    res["A 끝맺음"] = dict(cA)
    res["B 끝맺음"] = dict(cB)
    res["A 같은 끝맺음 ≤2"] = max(cA.values()) <= 2
    res["A 고르는 법 ≤2"] = gb <= 2
    res["B 같은 끝맺음 ≤5"] = max(cB.values()) <= 5
    res["같은 날 B 끝맺음 다름"] = all(len(set(v)) == len(v) for v in byday.values())
    led = ru.load_ledger()
    hits, mk = [], {}
    ids = [i for r in rows for i in r["prods"]]
    for i in ids:
        nm = P[i]["name"]
        h = ru.find_used(nm, f"https://brandconnect.naver.com/{ACCT}/affiliate/products/{i}", led)
        if h:
            hits.append((i, nm[:30], h[0].get("글제목", "")))
        k = ru.model_key(nm)
        mk.setdefault(k if ru.strong_key(k) else "pid:" + i, []).append(i)
    res["장부 겹침"] = hits
    res["같은 모델"] = {k: v for k, v in mk.items() if len(v) > 1}
    res["제품 수"] = len(ids)
    res["제품 id 중복"] = [k for k, v in Counter(ids).items() if v > 1]
    # 참고: 같은 키워드가 다른 대기열에 있는지(읽기만)
    nq = json.loads((ROOT / "content_queue_naver_tips.json").read_text(encoding="utf-8"))
    rq = json.loads((ROOT / "content_queue_radar_naver.json").read_text(encoding="utf-8"))
    others = [(it["keyword"], "N살림노트") for it in nq] + [(it["keyword"], "Desk Picker 대기열") for it in rq]
    ov = []
    for r in rows:
        kn = nsp(r["keyword"]).lower()
        for k2, src in others:
            k2n = nsp(k2).lower()
            if k2n == kn or (len(kn) >= 4 and kn in k2n) or (len(k2n) >= 4 and k2n in kn):
                ov.append((r["cell"], r["keyword"], src, k2))
    res["키워드 겹침(참고)"] = ov
    (RUN / "_fix_res.json").write_text(json.dumps({"rows": rows, "res": res}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # ── plan.json ──
    for p, r in zip(plan["plans"], rows):
        p["keyword"] = r["keyword"]
        p["final_title"] = r["title"]
        p["monthly_total"] = r["vol"]
        p["product_need"]["condition"] = r["keyword"]
        p["radar"]["title"] = r["title"]
        p["radar"]["search_query"] = r["keyword"]
        m = p["plan1130"]
        m["파일이름예정"] = r["file"]
        m["제품id"] = r["prods"]
        m["역할"] = r["roles"]
        m["끝맺음"] = r["end"]
        m["궁금증"] = r["curio"]
        m["비고"] = r["note"]
    plan["made_at"] = "2026-11-23"
    plan["설명"] = "2차 고침(2026-11-23b). 대기열에 넣지 않음. radar 칸은 push_plan 모양(connect.md). 고치기 전 판은 _before_20261123b/."
    (RUN / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── plan.md ──
    old = (before / "plan.md").read_text(encoding="utf-8")
    L = ["# 11/30 주 기획 (21편) — 2차 고침", "",
         "- 고친 날 2026-11-23 · 대기열·앱 데이터·링크 발급 변경 없음 · B8 은 샤오미 스마트 공기청정기 4 컴팩트로 교체(판매 중 확인).",
         "- 월간 검색량 = 검색광고 키워드 도구(PC+모바일). 10 이하는 '10 미만'. 고치기 전 판: _before_20261123b/plan.md", "", "## 1. 21편 표", "",
         "| 날짜 | 순번 | 틀 | 칸 | 갈래 | 키워드 | 월간 검색량 | 제목(글자 수) | 끝맺음 | 제품 상품id | 비고 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["frame"] == "A":
            pi = " · ".join(r["prods"])
        else:
            pi = f"본 {r['prods'][0]} / 대안 {r['prods'][1]}"
        vs = f"{r['vol']:,}" + (" (10 미만)" if r["vol"] <= 10 else "")
        c = r["curio"]
        extra = ""
        if "확인 필요" in c or "자료 수집 때" in c:
            extra = "**" + c.strip() + "**"
        elif c not in ("-", "값 아닌 용도형"):
            extra = "궁금증 값: " + c
        note = "; ".join(x for x in [r["note"], extra] if x)
        L.append(f"| {r['date']} | {r['no']} | {r['frame']} | {r['cell']} | {r['gal']} | {r['keyword']} | {vs} | {r['title']} ({r['tlen']}자) | {r['end']} | {pi} | {note} |")
    sec2 = re.search(r"## 2\. 틀 A 칸별 키워드 후보.*?(?=\n## 3\. )", old, re.S).group(0).rstrip().split("\n")
    out2 = []
    for ln in sec2:
        if ln.startswith("| A6 | 등받이쿠션 |"):
            ln = "| A6 | 등받이쿠션 | 20,590 | O | 검색량 최대지만 상품이 모두 허리 쿠션이라 사용자가 바꿈 |"
        elif ln.startswith("| A6 | 의자허리쿠션 |"):
            ln = "| A6 | 의자허리쿠션 | 4,280 | O | **고름** — 사용자 지정(허리 쿠션 제품에 더 맞는 이름) |"
        elif ln.startswith("- A6 '등받이쿠션'"):
            ln = "- A6 는 사용자가 키워드를 '의자허리쿠션'(4,280)으로 정함. '등받이쿠션'(20,590)은 검색량이 크지만 3개 상품이 모두 허리 쿠션."
        out2.append(ln)
    L += [""] + out2 + ["", "## 3. 틀 B 키워드·검색어", "", "| 칸 | 키워드 | 월간 검색량 | 제목에 넣은 큰 검색어 | 큰 검색어 월간 검색량 |", "|---|---|---|---|---|"]
    for r in rows:
        if r["frame"] == "B":
            t, k = BIG[r["cell"]]
            if t:
                L.append(f"| {r['cell']} | {r['keyword']} | {r['vol']:,} | {t} | {V[k]['total']:,} |")
            else:
                L.append(f"| {r['cell']} | {r['keyword']} | {r['vol']:,} | (없음 — 제목에 큰 검색어 안 넣음) | - |")
    L += ["", "- B3 키워드는 모델명(검색량 10 미만) 대신 '벨킨 3in1 충전기'(430)로 바꿈. B8 키워드 '샤오미 스마트 공기청정기 4'(190, 정확히 15자).",
          "- B11 제목은 '책상용'이 큰 검색어 자리를 대신함(검색량 조회 안 함). B3·B4·B10·B12 는 검색량이 작음.",
          "- 칸 비고의 '자료 수집 때 값 확인'(B1 높이·깊이, B9 크기·전원, B2 높이·각도 단계, B13 사양, B5·B4 비교 값)은 원고 쓰기 전 상품 페이지에서 값을 읽어 확인한다는 뜻.",
          "", "## 4. 파일 이름 예정", ""] + [f"- {r['file']}" for r in rows]
    (RUN / "plan.md").write_text("\n".join(L), encoding="utf-8")
    for k, v in res.items():
        print(k, ":", json.dumps(v, ensure_ascii=False, default=str)[:600])


if __name__ == "__main__":
    main()
