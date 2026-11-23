"""11/30 주 B8 칸을 머그워머 → 탁상 공기청정기로 바꾼 제품 풀 (읽기 전용).
결과: pipeline/stage_productpool_run/20261130/pool.md 의 B8 구간만 교체 + pipeline/stage_plan1130_run/20261123/ 에 작업 기록.
기존 도구 파일은 고치지 않고 productpool_1130 의 읽기 함수만 가져다 쓴다.
단계: collect(검색 목록 읽기) → select(카테고리 확인) → final(검색량·표 저장)
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import productpool_1130 as pp  # noqa: E402  (BRANDCONNECT 를 1020008… 로 바꿔 둔 모듈)
import product_candidates as pc  # noqa: E402
import radar_used_products as ru  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

assert "1020008" in pc.BRANDCONNECT
RUN = pc.ROOT / "pipeline" / "stage_plan1130_run" / "20261123"
RAW = RUN / "raw_b8_naver"
CAT = RUN / "raw_b8_category"
POOL_DIR = pc.ROOT / "pipeline" / "stage_productpool_run" / "20261130"
QUERIES = ["탁상 공기청정기", "미니 공기청정기", "소형 공기청정기", "책상 공기청정기", "휴대용 공기청정기",
           "USB 공기청정기", "개인용 공기청정기", "데스크 공기청정기"]
INC = r"공기\s?청정기"
SMALL = r"탁상|미니|소형|휴대|USB|usb|책상|데스크|개인|컴팩트|포터블|무선|충전|슬림|테이블"
EXC = r"벽걸이|거실|대형|대용량|\d+\s?평|평형|업소|사무실용|차량|자동차|카\s?|목걸이|웨어러블|마스크|필터\s?(만|교체|세트|호환)|호환\s?필터|교체\s?필터|가습|제습|살균기|탈취|정수|냉장고|화장실|신발|옷장"
CATOK = r"공기청정|청정기|공기정화"


def load(kw):
    f = pc._cache_path(RAW, kw)
    d = json.loads(f.read_text(encoding="utf-8"))
    its = [pc.parse_naver_item(x, i, d["kw"]) for i, x in enumerate(d["items"], 1)]
    for x in its:
        x["pid"] = pc._pid(x)
        x["brand_label"] = pp.brand_label(x["name"], x["store"], kw)
        x["canon"] = pc.canon_brand(x["name"], x["store"], kw) or pc._norm(x["brand_label"])
        x["model"] = pp.model_label(x["name"], x["brand_label"])
        x["mkey"] = ru.model_key(x["name"])
        x["query"] = kw
    return its, d.get("total")


def pool_items():
    seen, out, why = set(), [], {"종류·크기 아님(이름)": 0, "해외 전용": 0, "평점 4.5 미만/없음": 0}
    for q in QUERIES:
        its, _ = load(q)
        for x in its:
            if x["pid"] in seen:
                continue
            seen.add(x["pid"])
            n = x["name"]
            if not re.search(INC, n) or not re.search(SMALL, n) or re.search(EXC, n):
                why["종류·크기 아님(이름)"] += 1
                continue
            if re.search(pp.OVERSEAS, n, re.I):
                why["해외 전용"] += 1
                continue
            if x["rating"] is None or x["rating"] < 4.5:
                why["평점 4.5 미만/없음"] += 1
                continue
            out.append(x)
    out.sort(key=lambda x: -(x["reviews"] or 0))
    return out, why


def cats():
    out = {}
    if CAT.exists():
        for f in CAT.glob("*.json"):
            if f.name.startswith("_"):
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
            out[str(d["id"])] = d.get("category", "")
    return out


def select(fetch=True):
    items, why = pool_items()
    led = ru.load_ledger()
    blocked = pc.radar_blocked(led)
    ok, out = pc.split_blocked(items, blocked)
    why["장부 제외"] = len(out)
    # 21칸 중 다른 칸에 이미 둔 모델은 빼야 하므로 기존 풀(pool.json)의 상품id·모델키를 제외
    pj = json.loads((POOL_DIR / "pool.json").read_text(encoding="utf-8"))
    used_pid = {r["상품id"] for c, v in pj["cells"].items() if c != "B8" for r in v["후보"] + v["대안"]}
    ok = [x for x in ok if x["pid"] not in used_pid]
    C = cats()
    keep = []
    for x in ok:
        # 후보는 위에서부터 카테고리를 읽어 확인 — 아직 못 읽은 것은 앞쪽 30개만 읽음
        keep.append(x)
    keep = pp.dedup_models(keep)
    # 카테고리 확인 대상: 앞 14개
    todo = [{"id": x["pid"], "url": x["url"]} for x in keep[:14] if x["pid"] not in C]
    if fetch and todo:
        print("카테고리 확인", len(todo), "개")
        st = pc.collect_in_batches(todo, lambda q: q["id"], CAT, pc.BATCH_CAT,
                                   lambda part, tmp: pc._js_category(part, tmp), "category", "카테고리 확인")
        print("상태:", st)
        C = cats()
    good, bad = [], []
    for x in keep:
        w = C.get(x["pid"])
        if not w:
            continue
        g = pp.cat_group_1123(w)
        if g:
            bad.append((x["pid"], "11/23 주 제품군 " + g)); continue
        if not re.search(CATOK, w):
            bad.append((x["pid"], "카테고리 불일치: " + w)); continue
        good.append(x)
    return keep, good, bad, C, why


# 본상품 후보별 '모델명 검색어'(상품명에 모델 코드가 없으면 브랜드+상품 종류)
QMAP = {"926521207753440": "에어메이드 AMP-300", "870591215308129": "퓨리팟 L1",
        "951362751932928": "벤딕트 공기청정기", "1012929141686688": "샤오미 스마트 공기청정기 4"}
MAIN_IDS = ["926521207753440", "870591215308129", "951362751932928", "1012929141686688"]
ALT_IDS = ["818401632435584", "929054548591776", "903883307311584"]


def final():
    import plan1130_vol as pv
    keep, good, bad, C, why = select(False)
    byid = {x["pid"]: x for x in good}
    V = pv.vols(list(QMAP.values()))
    L = ["## B8 · 탁상 공기청정기 (틀 B · 계절가전)", "",
         "- 2026-11-23 교체: 머그워머 → 탁상 공기청정기(옛 표는 pool_B8_mugwarmer_old.md). 출처: 쇼핑커넥트 검색 목록(읽기만) · 검색어 8개 · 상품 페이지 카테고리 확인.",
         "- 기준: 판매 중(목록 기준, 상품 페이지 재확인은 plan 폴더 check.md), 평점 4.5↑, 리뷰 많은 순, 카테고리 '공기청정기', 이름에 탁상·미니·소형 등 작은 것을 뜻하는 말이 있고 거실·대형·벽걸이·차량용·필터만 판매 제외, 장부·다른 20칸 제품 제외.",
         "- 주의: 진짜 '책상 위' 전용(탁상·책상·데스크)이라고 이름에 적힌 것은 퓨리팟 L1 뿐. 나머지는 '미니·소형(원룸용)'이라 크기는 상품 페이지에서 확인 필요.", ""]
    head = ["칸", "상품id", "브랜드", "모델명", "상품명", "실제 종류", "가격", "평점", "리뷰 수", "판매처", "모델명 월간 검색량", "비고"]
    L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for role, ids in (("본상품", MAIN_IDS), ("대안", ALT_IDS)):
        for pid in ids:
            x = byid[pid]
            kind = pc.lowest_category(C[pid])
            nm = x["name"].replace("|", "/")[:60]
            hasc = bool(re.search(r"[A-Za-z]+-?\d", x["model"]))
            if role == "본상품":
                q = QMAP[pid]; v = V[q]
                vcol = f"{v['total']:,} (검색어: {q})"
                extra = ["본상품"] + ([v["note"]] if v["note"] else []) + ([] if hasc else ["모델 코드 없음(검색어는 브랜드+종류)"])
            else:
                vcol = "-"; extra = ["대안(다른 브랜드)"] + ([] if hasc else ["모델 코드 없음"])
            if pid == "951362751932928":
                extra.append("H13은 필터 등급이지 모델 코드 아님")
            row = ["B8", pid, x["brand_label"], x["model"], nm, kind, pc_won(x["price"]), str(x["rating"]), f"{x['reviews']:,}", x["store"], vcol, " · ".join(extra)]
            L.append("| " + " | ".join(r.replace("|", "/") for r in row) + " |")
    L += ["", "- 뺀 것: 콤마 LOV01-2106(벽걸이형, 리뷰 345 — 책상 위 아님), 이름에 크기·종류 말이 없거나 평점 4.5 미만 다수. 같은 필립스의 AC0950(리뷰 108)은 대안 브랜드가 겹쳐 뺌.", ""]
    sec = "\n".join(L)
    pm = (POOL_DIR / "pool.md").read_text(encoding="utf-8")
    old = POOL_DIR / "pool_B8_mugwarmer_old.md"
    m = re.search(r"## B8 · .*?(?=\n## B9 )", pm, re.S)
    assert m
    if not old.exists():
        old.write_text("# (보관) 옛 B8 머그워머 표 — 2026-11-23 교체 전\n\n" + m.group(0).strip() + "\n", encoding="utf-8")
    (POOL_DIR / "pool.md").write_text(pm[:m.start()] + sec + "\n" + pm[m.end():], encoding="utf-8")
    print("pool.md B8 교체 완료")


def pc_won(v):
    return f"{v:,}원" if v else "가격 표시 안 됨"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
    try:
        if cmd == "collect":
            RUN.mkdir(parents=True, exist_ok=True)
            st = pc.collect_in_batches(QUERIES, lambda k: k, RAW, pc.BATCH_KW,
                                       lambda part, tmp: pc._js_naver(part, tmp, pp.TOPN), "naver", "쇼핑커넥트")
            print("상태:", st)
        elif cmd == "final":
            final()
        elif cmd == "select":
            keep, good, bad, C, why = select(True)
            print("이름 통과", len(keep), "카테고리 통과", len(good), "뺌", bad, why)
            for x in good[:12]:
                print(x["pid"], x["brand_label"], x["model"], x["name"][:50], x["rating"], x["reviews"], C.get(x["pid"]))
    finally:
        try:
            pc._finish_space()
        except Exception:  # noqa: BLE001
            pass
