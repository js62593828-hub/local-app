# -*- coding: utf-8 -*-
"""🎯 검색량·상승세: 한 줄에 키워드 하나인 파일을 읽어 네이버 월검색량(PC+모바일)과 상승세를 조회한다.
앱의 target_keyword_finder.normalize_keyword / fetch_volumes / fetch_rising 을 그대로 부른다. 큐·글에는 아무것도 쓰지 않는다.
tier: 대표 기준 1,000+ 이면 '대표가능', 세부 기준 50+ 이면 '세부가능', 그 아래는 '미달' (기획지침 기준)."""
import argparse

import lt_common as C


def tier_of(total):
    if total is None:
        return "조회불가"
    if total >= 1000:
        return "대표가능"
    if total >= 50:
        return "세부가능"
    return "미달"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", required=True, help="한 줄에 키워드 하나인 파일")
    ap.add_argument("--out", required=True, help="결과 JSON 파일")
    a = ap.parse_args()
    C.load_app()  # 작업 폴더·경로 맞춤(.env 읽기)
    import target_keyword_finder as tkf

    kws, seen = [], set()
    with open(a.inp, encoding="utf-8") as f:
        for line in f:
            k = line.strip()
            n = tkf.normalize_keyword(k)
            if k and not k.startswith("#") and n not in seen:
                seen.add(n)
                kws.append(k)
    if not kws:
        raise SystemExit("키워드가 없습니다.")
    vols, err = tkf.fetch_volumes(kws)
    rising = tkf.fetch_rising(kws)
    rows = []
    for k in kws:
        n = tkf.normalize_keyword(k)
        v = vols.get(n)
        row = {"keyword": k, "pc": None, "mobile": None, "total": None, "rising_pct": rising.get(n), "tier": "조회불가"}
        if v:
            row.update(pc=v["pc"], mobile=v["mobile"], total=v["pc"] + v["mobile"])
            row["tier"] = tier_of(row["total"])
        else:
            row["error"] = err or "검색량 정보 없음(검색광고에 없는 표현이거나 조회 실패)"
        rows.append(row)
    C.write_json(a.out, rows)
    print(f"{'키워드':<26}{'PC':>8}{'모바일':>8}{'합계':>8}{'상승세':>9}  등급")
    for r in sorted(rows, key=lambda r: -(r["total"] or -1)):
        rp = "-" if r["rising_pct"] is None else f"{r['rising_pct']:+.1f}%"
        print(f"{r['keyword']:<26}{r['pc'] if r['pc'] is not None else '-':>8}{r['mobile'] if r['mobile'] is not None else '-':>8}"
              f"{r['total'] if r['total'] is not None else '-':>8}{rp:>9}  {r['tier']}" + (f"  ({r['error']})" if r.get("error") else ""))
    print(f"저장: {a.out}")


if __name__ == "__main__":
    main()
