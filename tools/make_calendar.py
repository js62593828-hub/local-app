# -*- coding: utf-8 -*-
"""계절 달력(초안) 만들기: 월별로 '6~8주 뒤 성수기가 오는 살림 문제' 목록을 Claude 1회 호출로 만든다.
→ pipeline/seasonal_calendar.json  ("draft": true = 초안. 사람이 고쳐 쓰면 draft 를 false 로)

- make_seeds.py 가 이 달력에서 이번 달 항목을 정보 씨앗에 넣는다(선발행). 그 씨앗에서 나온 글의 timing 은 '선발행'.
- 달력의 '월'은 글을 쓰는 달이다. 예) 6월 항목 = 6~8주 뒤인 7월 말~8월에 검색이 오르는 문제.
- 씨앗은 '대상 + 문제'(1~3단어), 브랜드·모델명 금지(make_seeds.has_brand 로 한 번 더 거른다).
- 이미 파일이 있으면 덮어쓰지 않는다(--force 로만). 앱 장부·운영 파일은 건드리지 않는다.

사용법
  python tools/make_calendar.py            # pipeline/seasonal_calendar.json 없을 때만 1회 호출
  python tools/make_calendar.py --out 다른경로.json --force
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from plan_posts import call_claude, extract_json, UsageLimitError, LIMIT_EXIT, limit_message  # noqa: E402
from safe_io import write_text_retry  # noqa: E402

BASE = TOOLS.parent
DEFAULT_OUT = BASE / "pipeline" / "seasonal_calendar.json"
MODEL, MAX_TURNS = "sonnet", "1"
PER_MONTH = 8

PROMPT = """너는 생활 살림 블로그 편집자다. 한국의 계절·생활 흐름에 맞춰 '계절 달력'을 만든다. 도구는 쓰지 말고 바로 답해라.

[주제 범위] {scope}
[카테고리] {categories}

[할 일]
1월부터 12월까지 각 월마다 살림 문제를 {n}개씩 적는다.
- 그 월(글을 쓰는 달)로부터 6~8주 뒤에 네이버 검색이 오르는(성수기가 오는) 살림 문제만 쓴다.
  예) 6월 항목: 7월 말~8월에 오르는 문제(에어컨 냄새, 제습, 초파리, 여름 이불 세탁). 11월 항목: 12월 말~1월에 오르는 문제(결로, 수도 동파, 외풍).
- 씨앗은 '대상 + 문제' 1~3단어(예: 창문 결로, 에어컨 냄새, 수도 동파). 제품 이름이 아니라 문제다.
- 브랜드·제조사·모델명은 절대 쓰지 않는다. 금지 분야({forbidden})는 쓰지 않는다.
- category 는 위 카테고리 중 하나를 글자 그대로. peak 는 성수기 시기를 짧게(예: "12월 말~1월").
- 같은 월 안에서 카테고리가 고루 섞이게, 같은 대상을 반복하지 않는다.

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 코드블록(```) 없이. 형식:
{{"months": {{"1": [{{"seed": "씨앗", "category": "카테고리", "peak": "성수기 시기"}}], "2": [...], ... "12": [...]}}}}
"""


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="계절 달력(초안) 만들기 (claude -p 1회)")
    ap.add_argument("--topic", default=str(TOOLS / "topic.json"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--force", action="store_true", help="이미 있어도 새로 만듦 (기존 파일은 .old 로 보관)")
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"[건너뜀] 이미 있음: {out} (새로 만들려면 --force)", file=sys.stderr)
        return 0
    topic = json.loads(Path(args.topic).read_text(encoding="utf-8-sig"))
    cats = list(topic["categories"])
    prompt = PROMPT.format(scope=topic["scope"], categories=", ".join(cats), forbidden=", ".join(f for f in topic["forbidden"] if f != "주제 범위 밖"), n=PER_MONTH)
    print(f"Claude({MODEL}) 를 1번 호출합니다...", file=sys.stderr)
    try:
        env = call_claude(prompt, MODEL, MAX_TURNS)
    except UsageLimitError as e:
        print(limit_message(e, "계절 달력 만들기"), file=sys.stderr)
        return LIMIT_EXIT
    except RuntimeError as e:
        print(f"[실패] Claude 호출 실패: {str(e).splitlines()[0][:200]}", file=sys.stderr)
        return 1
    try:
        raw = extract_json(env.get("result", ""))["months"]
    except (ValueError, KeyError, TypeError) as e:
        print(f"[실패] 응답을 읽지 못함 ({type(e).__name__})", file=sys.stderr)
        return 1
    from make_seeds import has_brand, norm
    months, problems = {}, []
    for m in range(1, 13):
        items, seen = [], set()
        for e in (raw.get(str(m)) or []):
            if not isinstance(e, dict):
                continue
            seed = " ".join(str(e.get("seed", "")).replace("|", " ").replace(",", " ").split())
            cat = e.get("category")
            if not seed or norm(seed) in seen:
                continue
            if has_brand(seed):
                problems.append(f"{m}월 '{seed}': 브랜드·모델명이라 뺌")
                continue
            if cat not in cats:
                problems.append(f"{m}월 '{seed}': 카테고리 '{cat}' 가 topic.json 에 없어 뺌")
                continue
            seen.add(norm(seed))
            items.append({"seed": seed, "category": cat, "peak": str(e.get("peak") or "")[:40]})
        months[str(m)] = items
    u = env.get("usage") or {}
    rec = {"draft": True, "note": "초안(Claude 1회 호출로 만듦). 고쳐 쓰면 draft 를 false 로. 글을 쓰는 달 기준 6~8주 뒤 성수기가 오는 살림 문제.",
           "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "model": MODEL,
           "usage": {"total_cost_usd": env.get("total_cost_usd"), "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")},
           "problems": problems, "months": months}
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.rename(out.with_name(out.stem + f".old_{datetime.now():%H%M%S}" + out.suffix))
    write_text_retry(out, json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    print(f"[저장됨] {out} — 월별 개수 " + ", ".join(f"{m}월 {len(v)}" for m, v in months.items()), file=sys.stderr)
    for p in problems:
        print("[주의]", p, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
