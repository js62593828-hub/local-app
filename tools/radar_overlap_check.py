# -*- coding: utf-8 -*-
"""Desk Picker 다음 주 계획(plan.json)이 사용 제품 장부와 겹치는지 점검한다. 읽기만 한다(대기열·글 json 수정 없음).
사용: python tools/radar_overlap_check.py pipeline/keyword_run/2026-11-30_radar_naver/plan.json [--out 결과.md]
- 틀 B 모델 키워드: 장부 모델과 같은지(브랜드+코드 또는 코드), 앱에 들어간 원고 본문에 이름·코드가 나오는지
- 같은 브랜드·같은 제품군의 11/23 글(참고)
- 같은 주 안에서 틀 A 검색어와 틀 B 모델(또는 틀 B끼리)이 같은 제품군이라 서로 제품을 가져갈 위험"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radar_used_products as ru

ROOT = ru.ROOT
FAMILY = {  # plan 의 seed(제품 종류) → 제품군
    "미니히터": "난방", "발난로": "난방", "USB손난로": "난방", "전기히터": "난방", "온풍기": "난방",
    "헤드폰": "이어폰·헤드폰", "무선이어폰": "이어폰·헤드폰", "이어폰": "이어폰·헤드폰",
    "키보드": "키보드류", "손목받침대": "키보드류", "팜레스트": "키보드류", "마우스": "키보드류",
    "모니터암": "모니터암", "모니터조명": "모니터조명", "모니터": "모니터",
    "블루투스스피커": "스피커", "보조배터리": "보조배터리", "USB허브": "허브·멀티탭", "멀티탭": "허브·멀티탭", "충전기": "충전기",
    "무드등": "조명", "독서대": "책상 정리", "등쿠션": "의자·쿠션", "발받침대": "의자·쿠션",
}


def fam(seed):
    n = re.sub(r"\s", "", seed or "")
    return next((f for k, f in FAMILY.items() if k in n), "")


def main():
    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(encoding="utf-8")
        except Exception: pass
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    plan = json.load(open(a.plan, encoding="utf-8"))
    led = ru.load_ledger()
    keys = ru.used_model_keys(led)
    app = json.load(open(os.path.join(ROOT, "articles_radar_naver.json"), encoding="utf-8"))
    q = {x["id"]: x for x in json.load(open(os.path.join(ROOT, "content_queue_radar_naver.json"), encoding="utf-8"))}
    texts = {aid: re.sub(r"[\s\-]", "", (v.get("raw_paste") or "").split("[메모]")[0]).lower() for aid, v in app.items() if aid >= ru.FIRST_ID and aid in q}
    title_of = {aid: (q[aid].get("plan") or {}).get("final_title", "") for aid in texts}
    led_brand = {}   # 장부 제품의 브랜드 → [(모델명, 글제목)]
    led_fam = {}     # 제품군 → [글제목]
    for r in led["products"]:
        led_brand.setdefault(r["모델키"].split("|")[0], set()).add(r["모델명"])
    for aid, x in q.items():
        if aid in texts:
            f = fam((x.get("cluster_seed") or "") + x.get("keyword", ""))
            led_fam.setdefault(f, set()).add(x["plan"].get("final_title", ""))
    rows = []
    for p in plan["plans"]:
        r = p["radar"]
        t, kw, seed = r["type"], r["search_query"], p.get("seed", "")
        title = p["final_title"]
        qt = next((x for x in q.values() if (x.get("plan") or {}).get("final_title") == title), None)
        qtitle = (qt or {}).get("plan", {}).get("final_title") or "(대기열에 없음)"
        hit = []
        if t == "B":
            k = ru.model_key(kw)
            if k in keys:
                hit.append(f"장부 모델 {keys[k][0]['모델명']} ← {keys[k][0]['글제목'][:26]}")
            code = k.split("|", 1)[1] if "|" in k else ""
            if code and len(code) >= 3:
                for aid, tx in texts.items():
                    if code in tx and not (keys.get(k) and any(rr["글id"] == aid for rr in keys[k])):
                        hit.append(f"11/23 원고 본문에 '{kw}' 이름 나옴 ← {title_of[aid][:26]}")
        brand = ru.model_key(kw).split("|")[0]
        ref = []
        if brand in led_brand:
            ref.append("같은 브랜드 11/23 모델: " + ", ".join(sorted(led_brand[brand])))
        f = fam(seed)
        if f and f in led_fam:
            ref.append(f"같은 제품군({f}) 11/23 글 {len(led_fam[f])}편")
        rows.append({"date": p["publish_date"], "t": t, "qtitle": qtitle, "kw": kw, "hit": hit, "ref": ref, "fam": f, "seed": seed})
    week = []
    for i, x in enumerate(rows):
        for j, y in enumerate(rows):
            if j <= i or not x["fam"] or x["fam"] != y["fam"]:
                continue
            if x["t"] == "A" and y["t"] == "A":
                continue  # 틀 A 끼리는 같은 제품군이면 이미 키워드 단계에서 걸러지는 대상(참고만)
            kind = "틀 A 검색어 ↔ 틀 B 모델" if x["t"] != y["t"] else "틀 B 모델끼리(대안으로 서로 가져갈 수 있음)"
            week.append((x, y, kind))
    L = ["# 11/30 주 겹침 점검", "", f"- 계획 파일: {a.plan}", f"- 기준: 사용 제품 장부 {led['줄수']}줄(서로 다른 모델 {led['모델수']}개) · 앱 원고 {len(texts)}편 본문", "",
         "## 1. 장부·11/23 글과 겹침", "",
         "| 날짜 | 틀 | 대기열 제목 | 검색어 | 겹치는 모델·11/23 글 제목 | 참고(같은 브랜드·제품군) |", "|---|---|---|---|---|---|"]
    for x in rows:
        L.append(f"| {x['date']} | {x['t']} | {x['qtitle']} | {x['kw']} | {' / '.join(x['hit']) or '겹침 없음'} | {' / '.join(x['ref']) or '-'} |")
    L += ["", f"- 장부·본문과 모델이 겹친 줄: {sum(1 for x in rows if x['hit'])}개 / {len(rows)}개", "",
          "## 2. 같은 주 안에서 같은 제품군이라 겹칠 위험", "",
          "| 날짜(앞) | 앞 글(틀) | 날짜(뒤) | 뒤 글(틀) | 제품군 | 위험 |", "|---|---|---|---|---|---|"]
    for x, y, kind in week:
        L.append(f"| {x['date']} | {x['kw']}({x['t']}) | {y['date']} | {y['kw']}({y['t']}) | {x['fam']} | {kind} |")
    if not week:
        L.append("| - | - | - | - | - | 없음 |")
    out = "\n".join(L)
    print(out)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(out + "\n")


if __name__ == "__main__":
    main()
