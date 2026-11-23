"""11/30 주 기획용 검색량 조회(읽기). 검색광고 키워드 도구, 결과·호출 수를 stage_plan1130_run/20261123 에 저장."""
from __future__ import annotations
import json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RUN = ROOT / "pipeline" / "stage_plan1130_run" / "20261123"
CACHE = RUN / "volume_cache.json"
CALLS = RUN / "ads_calls.txt"


def vols(queries: list[str]) -> dict:
    import config, naver_api
    assert not config.USE_MOCK, "목업 모드 — 실제 검색량 아님"
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = [q for q in dict.fromkeys(queries) if q not in cache]
    calls = 0
    for i in range(0, len(todo), 5):
        part = todo[i:i + 5]
        rows = naver_api.get_keyword_stats(part, expand=False)
        calls += 1
        by = {re.sub(r"\s", "", r["keyword"]).lower(): r for r in rows}
        for q in part:
            r = by.get(re.sub(r"\s", "", q).lower())
            if r:
                t = r["pc_count"] + r["mobile_count"]
                cache[q] = {"pc": r["pc_count"], "mobile": r["mobile_count"], "total": t, "note": "10 미만" if t <= 10 else ""}
            else:
                cache[q] = {"pc": 0, "mobile": 0, "total": 0, "note": "조회 결과에 없음(10 미만으로 봄)"}
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    prev = int(CALLS.read_text()) if CALLS.exists() else 0
    CALLS.write_text(str(prev + calls), encoding="utf-8")
    return cache


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    c = vols(sys.argv[1:])
    for q in sys.argv[1:]:
        print(q, c[q])
