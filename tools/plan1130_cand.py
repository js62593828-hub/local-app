"""11/30 주 plan(고친 판)의 상품id 로 🧾 제품 체크용 candidates.json 만들기 (방법 A).
- 쇼핑커넥트 접속 0회: 이미 읽어 둔 파일만 쓴다(plan.json, check_parsed.json·parsed_b8new.json·raw_check/ = 오늘 상품 페이지 글자,
  stage_productpool_run/20261130/raw_naver_parts + raw_b8_naver = 검색 목록의 대표 사진 주소).
- 쓰는 곳: pipeline/candidates_run/20261130_radar_naver_plan/ 하나(candidates.json·summary.md·img/). 다른 candidates_run 폴더는 안 건드림.
- approved.json·links.json 은 만들지 않는다. 사진 내려받기는 pc.download_image(네이버 500px 주소 그대로).
- 틀 A: 제품 3개(recommended 3). 틀 B: 본상품 1 + 대안 1(recommended 2, role 로 구분). items = recommended 와 같은 목록(화면이 rank 로 찾음).
사용: python tools/plan1130_cand.py
"""
from __future__ import annotations
import glob, json, re, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import product_candidates as pc  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
RUN = ROOT / "pipeline" / "stage_plan1130_run" / "20261123"
OUT = ROOT / "pipeline" / "candidates_run" / "20261130_radar_naver_plan"
ACCT = "1020008901769280"   # 지금 로그인된 쇼핑커넥트 계정(이 스크립트 안에서만 씀). 화면의 '상품 보기' 주소에만 쓰이고 링크 발급은 안 함
IDMAP = RUN / "idmap_cache.json"


def num(s):
    m = re.sub(r"[^\d]", "", str(s or ""))
    return int(m) if m else None


def search_index(need: set[str]) -> dict:
    """상품id → 검색 목록 한 칸(이미지 주소·브랜드·수수료·정가). 느린 드라이브라 결과를 idmap_cache.json 에 저장."""
    if IDMAP.exists():
        c = json.loads(IDMAP.read_text(encoding="utf-8"))
        if need <= set(c):
            return c
    found = {}
    files = glob.glob(str(ROOT / "pipeline/stage_productpool_run/20261130/**/*.json"), recursive=True) + glob.glob(str(RUN / "raw_b8_naver/*.json"))
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(d, dict) or "items" not in d:
            continue
        for i, x in enumerate(d["items"], 1):
            try:
                it = pc.parse_naver_item(x, i, d.get("kw", ""))
            except Exception:  # noqa: BLE001
                continue
            pid = pc._pid(it)
            if pid in need and pid not in found:
                found[pid] = {k: it.get(k) for k in ("image_url", "brand", "list_price", "commission", "store", "name", "price", "rating", "reviews")}
    IDMAP.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    return found


def main() -> int:
    plan = json.loads((RUN / "plan.json").read_text(encoding="utf-8"))
    P = json.loads((RUN / "check_parsed.json").read_text(encoding="utf-8"))
    P.update(json.loads((RUN / "parsed_b8new.json").read_text(encoding="utf-8")))
    need = {i for p in plan["plans"] for i in p["plan1130"]["제품id"]}
    idx = search_index(need)
    missing = sorted(need - set(idx))
    if missing:
        print("검색 목록에서 못 찾은 상품(사진 없음):", missing)
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    data = {"channel": "radar_naver", "channel_label": "N데스크피커", "created": datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "plan1130",
            "criteria": {"top_n": 3, "rating_min": 4.5, "rating_relaxed": 4.3, "pick": 3},
            "note": "11/30 주 plan(고친 판)의 상품id 로 만든 후보. 대기열과 따로 움직임. 틀 A 제품 3개, 틀 B 본상품 1 + 대안 1(role).",
            "keywords": [], "skipped_no_query": []}
    nprod = nimg = 0
    for ki, p in enumerate(plan["plans"], 1):
        m = p["plan1130"]
        ids, roles = m["제품id"], m["역할"]
        items = []
        for rank, (pid, role) in enumerate(zip(ids, roles), 1):
            raw = json.loads(pc._cache_path(RUN / "raw_check", pid).read_text(encoding="utf-8"))
            lines = [x.strip() for x in raw["bc_text"].split("\n") if x.strip()]
            comm = next((num(x) for x in lines if x.startswith("수수료")), None)
            lp = lines[lines.index("판매가") + 1] if "판매가" in lines else ""
            s = idx.get(pid, {})
            pr = P[pid]
            name, store = pr["name"], pr["store"]
            rating = float(pr["rating"])
            reviews = num(pr["reviews"])
            price = num(pr["price"])
            kind = {"제품": f"틀 A 제품 {rank}/3", "본상품": "틀 B 본상품", "대안": "틀 B 대안", "대안(임시)": "틀 B 대안"}[role]
            it = {"rank": rank, "name": name, "store": store, "brand": s.get("brand") or pc.brand_or_unknown(name, store, p["keyword"]),
                  "price": price, "list_price": num(lp) or s.get("list_price"), "commission": comm if comm is not None else s.get("commission"),
                  "reviews": reviews, "rating": rating, "image_url": s.get("image_url") or "",
                  "url": f"https://brandconnect.naver.com/{ACCT}/affiliate/products/{pid}", "site_issued": False,
                  "reason": f"plan 확정 · {kind} · 평점 {rating} · 리뷰 {reviews:,}개(네이버 기준)", "role": role, "product_id": pid}
            it["image"] = pc.download_image(it["image_url"], OUT / "img" / f"k{ki}_r{rank}.jpg")
            nprod += 1
            nimg += 1 if it["image"] and it["image"].get("file") else 0
            items.append(it)
        data["keywords"].append({"keyword": p["keyword"], "total": len(items), "items": items, "recommended": items, "relaxed": False,
                                 "plan_cell": m["칸"], "frame": p["radar"]["type"], "publish_date": p["publish_date"], "title": p["final_title"]})
    (OUT / "candidates.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    pc.write_summary(OUT, data)
    kwa = sum(1 for k in data["keywords"] if k["frame"] == "A")
    print(f"저장: {OUT}\n키워드 {len(data['keywords'])}개 · 제품 {nprod}개 (틀 A {sum(len(k['items']) for k in data['keywords'] if k['frame']=='A')} + 틀 B {sum(len(k['items']) for k in data['keywords'] if k['frame']=='B')}) · 사진 {nimg}장 · 사진 못 받은 것 {nprod - nimg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
