# -*- coding: utf-8 -*-
"""🎯 검색량·상승세: 한 줄에 키워드 하나인 파일을 읽어 네이버 월검색량(PC+모바일)과 상승세를 조회한다.
앱의 target_keyword_finder.normalize_keyword / fetch_volumes / fetch_rising 을 그대로 부른다. 큐·글에는 아무것도 쓰지 않는다.
tier: 대표 기준 1,000+ 이면 '대표가능', 세부 기준 50+ 이면 '세부가능', 그 아래는 '미달' (기획지침 기준)."""
import argparse
from datetime import date

import lt_bank as B
import lt_common as C
import lt_data as D


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
    ap.add_argument("--no-bank", action="store_true", help="키워드 은행을 안 보고 전부 새로 조회한다")
    ap.add_argument("--week", default="", help="은행에 적을 주차(없으면 --out 경로의 혜택_날짜에서 읽음)")
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
    # 8차: 30일 안에 조회한 키워드는 은행 값을 쓰고 다시 조회하지 않는다
    today_s = date.today().isoformat()
    bank = B.load()
    cached, todo = {}, []
    for k in kws:
        e = bank["keywords"].get(D.key(k)) if not a.no_bank else None
        v = (e or {}).get("volume")
        if v and B.is_fresh(v.get("checked")):
            cached[k] = {"keyword": k, "pc": v.get("pc"), "mobile": v.get("mobile"), "total": v.get("total"),
                         "rising_pct": v.get("rising_pct"), "tier": v.get("tier"), "bank_date": v.get("checked")}
        else:
            todo.append(k)
    fresh_rows = {}
    if todo:
        vols, err = tkf.fetch_volumes(todo)
        rising = tkf.fetch_rising(todo)
        for k in todo:
            n = tkf.normalize_keyword(k)
            v = vols.get(n)
            row = {"keyword": k, "pc": None, "mobile": None, "total": None, "rising_pct": rising.get(n), "tier": "조회불가"}
            if v:
                row.update(pc=v["pc"], mobile=v["mobile"], total=v["pc"] + v["mobile"])
                row["tier"] = tier_of(row["total"])
            else:
                row["error"] = err or "검색량 정보 없음(검색광고에 없는 표현이거나 조회 실패)"
            fresh_rows[k] = row
    rows = [cached.get(k) or fresh_rows[k] for k in kws]
    if fresh_rows and not a.no_bank:
        B.record_volume(bank, list(fresh_rows.values()), today_s, a.week or B.week_of_path(a.out))
        B.save(bank)
    print(f"키워드 {len(kws)}개 · 은행 값 사용 {len(cached)}개(30일 안에 조회) · 새로 조회 {len(todo)}개")
    C.write_json(a.out, rows)
    print(f"{'키워드':<26}{'PC':>8}{'모바일':>8}{'합계':>8}{'상승세':>9}  등급")
    for r in sorted(rows, key=lambda r: -(r["total"] or -1)):
        rp = "-" if r["rising_pct"] is None else f"{r['rising_pct']:+.1f}%"
        print(f"{r['keyword']:<26}{r['pc'] if r['pc'] is not None else '-':>8}{r['mobile'] if r['mobile'] is not None else '-':>8}"
              f"{r['total'] if r['total'] is not None else '-':>8}{rp:>9}  {r['tier']}" + (f"  ({r['error']})" if r.get("error") else "") + (f"  [은행 {r['bank_date']}]" if r.get("bank_date") else ""))
    print(f"저장: {a.out}")


if __name__ == "__main__":
    main()
