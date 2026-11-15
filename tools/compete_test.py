# -*- coding: utf-8 -*-
"""경쟁 측정 방식 비교 시험 (읽기 전용, Claude 호출 없음, 블로그 검색 API 약 100~150회)

지금 방식은 '최근 30일 동안 블로그 검색에 걸리는 글 수'로 경쟁을 잰다. 네이버 블로그 검색은 단어가 본문 어디에 있어도
글을 세기 때문에, 흔한 단어(곰팡이·제거·냄새)로 된 생활 문제 키워드는 경쟁이 실제보다 세 보일 수 있다.
이 시험은 키워드마다 세 가지 숫자를 나란히 보여준다.

  A. 지금 방식     최근 30일 블로그 검색 결과 글 수 (최대 1,000)
  B. 제목 일치     그 30일 글 중 '제목에 키워드가 들어간 글' 수 (띄어쓰기 무시) = 실제로 그 키워드를 노린 글
  C. 상위 100 제목  관련도순 상위 100개 글 중 제목에 키워드가 들어간 글 수 (검색 1페이지 경쟁자 추정)

결과는 화면에 표로, pipeline/compete_test_날짜시각.json 에 저장한다. 앱 장부·캐시는 건드리지 않는다.

사용법
  python tools/compete_test.py                      # 기본 시험 키워드 10개 (해결형 5 + 제품형 5)
  python tools/compete_test.py 세탁실곰팡이제거 광목이불   # 키워드 직접 지정
"""
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import collect_metrics as cm  # noqa: E402

BASE = TOOLS.parent
OUT_DIR = BASE / "pipeline"
PAGE, MAX_ITEMS, WINDOW = 100, 1000, 30
# 11/9 오전 실행에서: 해결형인데 '최근 30일 1,000+'로 탈락한 것 5개 + 제품형인데 통과한 것 5개
DEFAULT = ["세탁실곰팡이제거", "신발곰팡이제거", "창틀곰팡이청소", "앞유리습기", "빨래쉰내제거",
           "광목이불", "아사이불", "바지걸이행거", "패밀리침대커버", "세차밀대"]
TAG = re.compile(r"<[^>]+>")


def nospace(s: str) -> str:
    return "".join(TAG.sub("", s or "").split()).lower()


def measure(api, kw: str, cutoff: str) -> dict:
    k = nospace(kw)
    inside = title_hit = calls = 0
    capped = False
    for start in range(1, MAX_ITEMS + 1, PAGE):
        j = api.call("blog", params={"query": kw, "display": PAGE, "start": start, "sort": "date"})
        calls += 1
        if not j or "items" not in j:
            return {"keyword": kw, "error": "블로그 검색 실패", "calls": calls}
        items = j["items"]
        recent = [it for it in items if str(it.get("postdate", "")) >= cutoff]
        inside += len(recent)
        title_hit += sum(1 for it in recent if k in nospace(it.get("title")))
        if len(recent) < len(items) or len(items) < PAGE:
            break
        if start + PAGE > MAX_ITEMS:
            capped = True
    j = api.call("blog", params={"query": kw, "display": PAGE, "start": 1, "sort": "sim"})
    calls += 1
    top = (j or {}).get("items") or []
    top_hit = sum(1 for it in top if k in nospace(it.get("title")))
    return {"keyword": kw, "posts_30d_any": inside, "capped": capped, "posts_30d_title": title_hit,
            "top100_title": top_hit, "top100_n": len(top), "total_docs": (j or {}).get("total"), "calls": calls}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    kws = [" ".join(a.split()) for a in sys.argv[1:] if a.strip()] or DEFAULT
    api = cm.OpenApi()
    cutoff = (date.today() - timedelta(days=WINDOW)).strftime("%Y%m%d")
    vols = {}
    try:      # 월 검색량(검색광고 API)도 붙여서 경쟁률을 비교
        for kw in kws:
            rows = cm.related_keywords(kw)
            hit = next((r for r in rows if nospace(r["keyword"]) == nospace(kw)), None)
            vols[kw] = hit["monthly_total"] if hit else None
    except Exception as e:
        print(f"[주의] 검색량 조회 실패: {e}", file=sys.stderr)
    out = []
    for kw in kws:
        r = measure(api, kw, cutoff)
        v = vols.get(kw)
        r["monthly_total"] = v
        if v and r.get("posts_30d_any"):
            r["rate_any"] = round(v / r["posts_30d_any"], 2)
        if v is not None and "posts_30d_title" in r:
            r["rate_title"] = round(v / max(1, r["posts_30d_title"]), 2)
        out.append(r)
        print(f"{kw}: 검색량 {v} / A 지금 방식 {r.get('posts_30d_any')}{'+' if r.get('capped') else ''}"
              f" / B 제목 일치 {r.get('posts_30d_title')} / C 상위100 제목 {r.get('top100_title')}"
              f" / 경쟁률 A {r.get('rate_any')} → B {r.get('rate_title')} (호출 {r.get('calls')}회)", file=sys.stderr)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"compete_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps({"measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                             "cutoff": cutoff, "results": out,
                             "api_calls": sum(r.get("calls", 0) for r in out)}, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    print(f"[저장됨] {p} (블로그 검색 {sum(r.get('calls', 0) for r in out)}회)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
