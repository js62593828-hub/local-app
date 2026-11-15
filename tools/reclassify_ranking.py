# -*- coding: utf-8 -*-
"""이미 만들어진 pipeline/ranking.json 의 S·A 키워드에 카테고리·세부 주제·표준 표기를 붙인다 (분류만 다시, 화면 수집·상위 글 분석은 하지 않음).

run_pipeline.py 가 새로 돌 때는 끝에서 같은 일을 자동으로 하므로, 이 스크립트는 '이미 끝난 ranking.json 에 나중에 붙이고 싶을 때' 쓴다.
같은 분류 호출(keyword_classify.classify, with_meta=True)을 쓰며 유형·의도 묶음도 S·A 기준으로 다시 받는다.
쓰는 곳: ranking.json 한 파일 (고치기 전 백업은 호출한 사람이 만든다).

사용법
  python tools/reclassify_ranking.py
  python tools/reclassify_ranking.py --ranking pipeline/ranking.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyword_classify as kc  # noqa: E402

BASE = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="ranking.json 의 S·A 에 카테고리·세부 주제·표준 표기 붙이기")
    ap.add_argument("--ranking", default=str(BASE / "pipeline" / "ranking.json"))
    args = ap.parse_args()
    path = Path(args.ranking)
    rk = json.loads(path.read_text(encoding="utf-8-sig"))
    sa = [r for r in rk["ranking"] if r.get("grade") in ("S", "A")]
    log(f"[분류 다시] S·A {len(sa)}개를 {kc.MODEL} 로 보냅니다 (키워드 글자만, 화면 수집·상위 글 분석 없음)")
    res = kc.classify([r["keyword"] for r in sa], with_meta=True)
    if res["status"] != "ok":
        log(f"[실패] 분류 실패: {res.get('error')}")
        return 1
    changed = []
    for r in sa:
        m = res["meta"].get(r["keyword"]) or {}
        r["kw_category"], r["kw_topic"], r["canonical"] = m.get("category"), m.get("topic"), m.get("canonical")
        t = res["types"][r["keyword"]]
        if t != r.get("kw_type"):
            changed.append((r["keyword"], r.get("kw_type"), t))
        r["kw_type"], r["platform_hint"] = t, kc.PLATFORM_HINT.get(t)
    # 의도 묶음: 예전 묶음에 이번 S·A 기준 묶음을 더한다 (plan_posts 가 묶음마다 점수 1등만 남김)
    old = rk.get("intent_groups") or []
    rk["intent_groups"] = old + res["groups"]
    rk["meta_classified_at"] = __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
    path.write_text(json.dumps(rk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cost = (res.get("usage") or {}).get("total_cost_usd")
    typo = [(r["keyword"], r["canonical"]) for r in sa if r.get("canonical") and r["canonical"] != r["keyword"]]
    log(f"[분류 다시] 완료 / 비용 {cost} / 유형이 바뀐 키워드 {len(changed)}개 / 표준 표기가 다른 키워드 {len(typo)}개 / 새 묶음 {len(res['groups'])}개")
    for k, a, b in changed:
        log(f"    유형 변경: {k} {a} → {b}")
    for k, c in typo:
        log(f"    표기: {k} → {c}")
    if res.get("meta_missing"):
        log(f"[주의] 메타를 못 받은 키워드 {len(res['meta_missing'])}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
