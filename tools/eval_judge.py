# -*- coding: utf-8 -*-
"""판정 정답 세트 채점 (1단계: 지문 색인 방식): tests/eval_set.json 의 정답으로 지금의 판정을 채점한다.

- dup_existing : 키워드와 기존 글의 지문(대상|문제)이 같은가  (fingerprint.py, 코드 비교)
- same_article : 키워드 둘의 지문이 같은가
- service      : 업체 시공·수리 의뢰형인가 (keyword_classify.classify 의 유형 판정)

실전 조건: 기존 글 전체(pipeline/existing_keywords.json 에서 약 250개)를 색인에 넣은 상태에서 채점한다.
정답 세트의 '이미 쓴 글' 제목도 같은 색인에 함께 들어간다.
운영용 색인(pipeline/keyword_index.json, post_index.json)은 건드리지 않는다. 채점용 색인은 pipeline/eval_index/ 에 따로 만든다.

회차마다 채점용 키워드 색인을 비우고 새로 판정한다(= 모델이 같은 입력에 같은 답을 내는지 재는 용도).
마지막에 같은 색인으로 한 번 더 돌려 '한 번 판정한 것은 다시 묻지 않음'(100% 동일)도 확인한다.

사용법
  python tools/eval_judge.py             # 새 판정 2회 + 색인 재사용 1회
  python tools/eval_judge.py --repeat 1
결과: pipeline/eval_judge_YYYYMMDD_HHMMSS.json
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fingerprint as fpm  # noqa: E402
import keyword_classify as kc  # noqa: E402
import plan_posts as pp  # noqa: E402
from safe_io import write_text_retry  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
EVAL = BASE / "tests" / "eval_set.json"
OUT_DIR = BASE / "pipeline"
EVAL_DIR = OUT_DIR / "eval_index"


def real_posts() -> list:
    ex, _ = pp.load_existing(OUT_DIR / "existing_keywords.json")
    w = {"dedup_skip_sites": ["longterm", "content_queue.json"]}
    try:
        w = pp.load_weekly(pp.DEFAULT_FILTER)
    except (OSError, ValueError, KeyError):
        pass
    return fpm.existing_post_list(ex, w, None)


def prepare_base(real: list) -> tuple:
    """실제 기존 글(약 250개)만 판정해 둔 채점용 기반 색인·대상 이름 정리표. 한 번 만들어 두고 회차마다 복사해서 쓴다."""
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    base_p, base_a = EVAL_DIR / "post_base.json", EVAL_DIR / "alias_base.json"
    idx = fpm.FingerprintIndex(EVAL_DIR / "kw_unused.json", base_p, base_a, EVAL_DIR / "verdict_unused.json")
    n = idx.ensure_posts(real, print)
    idx.ensure_alias(print)
    if n:
        print(f"기존 글 {n}개를 새로 판정해 채점용 기반 색인에 저장")
    return base_p, base_a


def group_of(c: dict) -> str:
    return "holdout2" if c.get("holdout2") else ("holdout" if c.get("holdout") else "base")


def pick(cases: list, only: str) -> list:
    """base = 처음 45건, holdout = 1.5단계 새 사례 32건, holdout2 = 1.6단계 새 사례, old = base+holdout(기존 77건)"""
    if only == "all":
        return cases
    if only == "old":
        return [c for c in cases if group_of(c) != "holdout2"]
    return [c for c in cases if group_of(c) == only]


def cluster_check(idx, ev: dict, log=None) -> dict:
    """색인의 모든 키워드로 같은 글 묶음을 만들어(직접 same 판정만, 사슬 금지) 검사한다.
    - 5개 이상인 묶음 목록
    - 정답 세트에서 false 인 쌍(같은 글이 아님)이 같은 묶음에 들어 있으면 실패"""
    kws = [k for k in idx.keywords if idx.entry(k)]
    groups, cover = fpm.cluster_and_cover(idx, kws, log)
    gid = {k: i for i, g in enumerate(groups) for k in g}
    bad = [{"a": c["a"], "b": c["b"], "group": groups[gid[c["a"]]]} for c in ev["same_article"]["cases"]
           if not c["same"] and c["a"] in gid and c["b"] in gid and gid[c["a"]] == gid[c["b"]]]
    return {"keywords": len(kws), "groups": len(groups), "big_groups": [g for g in groups if len(g) >= 5], "false_pairs_in_same_group": bad,
            "cover_pairs": len(cover), "ok": not bad}


def run_once(ev: dict, tag: str, base: tuple, real: list, fresh: bool, only: str) -> dict:
    base_p, base_a = base
    kw_path, post_path = EVAL_DIR / f"kw_{tag}.json", EVAL_DIR / f"post_{tag}.json"
    alias_path, verdict_path = EVAL_DIR / f"alias_{tag}.json", EVAL_DIR / f"verdict_{tag}.json"
    if fresh:
        shutil.copyfile(base_p, post_path)
        shutil.copyfile(base_a, alias_path)
        for pth in (kw_path, verdict_path):
            if pth.exists():
                pth.unlink()
    idx = fpm.FingerprintIndex(kw_path, post_path, alias_path, verdict_path)
    c0, k0 = idx.calls, idx.cost

    dcases = pick(ev["dup_existing"]["cases"], only)
    scases = pick(ev["same_article"]["cases"], only)
    vcases = pick(ev["service"]["cases"], only)
    ocases = [c for c in ev.get("offtopic", {}).get("cases", [])] if only in ("all", "base") else []
    tcases = [c for c in ev.get("track", {}).get("cases", [])] if only in ("all", "base") else []
    scv = [c for c in ev.get("solves", {}).get("cases", [])] if only in ("all", "base") else []
    bcs = [c for c in ev.get("brand", {}).get("cases", [])] if only in ("all", "base") else []
    titles = list(dict.fromkeys(c["existing"] for c in dcases))
    idx.ensure_posts([{"key": t, "text": t, "source": "eval"} for t in titles], print)
    kws = list(dict.fromkeys([c["keyword"] for c in dcases] + [k for c in scases for k in (c["a"], c["b"])]))
    idx.ensure_keywords(kws, print)
    idx.ensure_types([c["keyword"] for c in vcases + ocases], print)
    idx.ensure_alias(print)

    # 1) 이미 쓴 글과 같은 글인가: 키워드 ↔ 정답 세트가 준 기존 글 제목 (지문 → 가까운 쌍 확인)
    res = idx.resolve([(idx.entry(c["keyword"]), c["keyword"], idx.entry(c["existing"], "p"), c["existing"], True) for c in dcases], print)
    d_rows = []
    for c, got in zip(dcases, res):
        ek, ep = idx.entry(c["keyword"]), idx.entry(c["existing"], "p")
        d_rows.append({"keyword": c["keyword"], "existing": c["existing"], "expect_same": c["same"], "got": got, "ok": got == c["same"],
                       "holdout": bool(c.get("holdout")), "group": group_of(c), "fp_keyword": idx.fp_e(ek), "fp_existing": idx.fp_e(ep)})
    # 2) 후보끼리 같은 글인가
    res = idx.resolve([(idx.entry(c["a"]), c["a"], idx.entry(c["b"]), c["b"], False) for c in scases], print)
    s_rows = []
    for c, got in zip(scases, res):
        s_rows.append({"a": c["a"], "b": c["b"], "expect_same": c["same"], "got": got, "ok": got == c["same"],
                       "holdout": bool(c.get("holdout")), "group": group_of(c), "fp_a": idx.fp_of(c["a"]), "fp_b": idx.fp_of(c["b"])})
    cl = cluster_check(idx, ev, print)
    # 3) 업체형인가: 색인의 유형(kw_type) 판정
    idx.ensure_types([c["keyword"] for c in vcases], print)
    v_rows = []
    for c in vcases:
        t = idx.type_of(c["keyword"])
        v_rows.append({"keyword": c["keyword"], "expect_service": c["service"], "got": t == "service", "ok": (t == "service") == c["service"],
                       "holdout": bool(c.get("holdout")), "group": group_of(c), "type": t})
    # 4) 주제 밖인가: 색인의 유형(kw_type)이 offtopic 인가
    idx.ensure_types([c["keyword"] for c in ocases], print)
    o_rows = []
    for c in ocases:
        t = idx.type_of(c["keyword"])
        o_rows.append({"keyword": c["keyword"], "expect_offtopic": c["offtopic"], "got": t == "offtopic", "ok": (t == "offtopic") == c["offtopic"],
                       "group": "base", "type": t})
    # 5) 갈래: 사려는 물건 이름이면 money, 아니면 info (지문 판정과 별도로 받은 product 여부 사용)
    kws_t = [c["keyword"] for c in tcases]
    idx.ensure_product(kws_t, print)
    idx.ensure_alias(print)
    t_rows = []
    for c in tcases:
        got = idx.track_of(c["keyword"])
        t_rows.append({"keyword": c["keyword"], "expect_track": c["track"], "got": got, "ok": got == c["track"], "group": "base",
                       "product": idx.product_of(c["keyword"]), "fp": idx.fp_of(c["keyword"]), "solves": idx.solves_of(c["keyword"])})
    # 6) 수익글 조건(solves): 푸는 살림 문제가 있으면 통과  7) 브랜드·모델명 키워드: 유형 brand
    idx.ensure_product([c["keyword"] for c in scv + bcs], print)
    sv_rows, b_rows = [], []
    for c in scv:
        sv = idx.solves_of(c["keyword"])
        got = bool(sv and sv.get("problem"))
        sv_rows.append({"keyword": c["keyword"], "expect_solves": c["solves"], "got": got, "ok": got == c["solves"], "group": "base",
                        "product": idx.product_of(c["keyword"]), "solves": sv})
    for c in bcs:
        t = idx.type_of(c["keyword"])
        b_rows.append({"keyword": c["keyword"], "expect_brand": c["brand"], "got": t == "brand", "ok": (t == "brand") == c["brand"], "group": "base", "type": t})
    return {"tag": tag, "fresh": fresh, "solves": sv_rows, "brand": b_rows, "dup_existing": d_rows, "same_article": s_rows, "service": v_rows, "offtopic": o_rows, "track": t_rows, "cluster": cl,
            "calls": {"fingerprint": idx.calls - c0 - idx.pair_calls - idx.alias_calls - idx.ps_calls, "pairs": idx.pair_calls,
                      "alias": idx.alias_calls, "types": idx.type_calls, "product": idx.ps_calls},
            "cost": round(idx.cost - k0, 4), "real_posts": len(real), "pair_verdicts": len(idx.verdicts)}


def rate(rows: list) -> str:
    if not rows:
        return "-"
    n = sum(1 for r in rows if r["ok"])
    return f"{n}/{len(rows)} ({n * 100 // len(rows)}%)"


KEYS = [("dup_existing", "이미 쓴 글과 중복"), ("same_article", "후보끼리 같은 글"), ("service", "업체형"), ("offtopic", "주제 밖"), ("track", "갈래(정보·수익)"), ("solves", "수익글 조건(solves)"), ("brand", "브랜드·모델명")]


def compare(a: dict, b: dict) -> dict:
    total = diff = 0
    for k, _ in KEYS:
        for r0, r1 in zip(a.get(k, []), b.get(k, [])):
            total += 1
            diff += (r0["got"] != r1["got"])
    fp_diff = sum(1 for r0, r1 in zip(a["dup_existing"] + a["same_article"], b["dup_existing"] + b["same_article"])
                  if (r0.get("fp_keyword"), r0.get("fp_a")) != (r1.get("fp_keyword"), r1.get("fp_a")))
    return {"total": total, "different_answers": diff, "different_fingerprints": fp_diff}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=2, help="새로 판정하는 회차 수 (2 이상이면 모델 일관성도 잼). 0 이면 저장된 run1 색인을 이어 쓰며 바뀐 규칙에 걸리는 항목만 새로 판정")
    ap.add_argument("--only", choices=["base", "holdout", "holdout2", "old", "all"], default="all",
                    help="채점할 사례: base 45건 / holdout 32건 / holdout2 / old(기존 77건) / 전부")
    args = ap.parse_args()
    ev = json.loads(EVAL.read_text(encoding="utf-8"))
    real = real_posts()
    print(f"실전 조건: 기존 글 {len(real)}개를 색인에 넣고 채점")
    base = prepare_base(real)
    runs = []
    for n in range(args.repeat):
        print(f"\n[{n + 1}/{args.repeat}] 새 판정 채점 중...")
        runs.append(run_once(ev, f"run{n + 1}", base, real, True, args.only))
    print("\n[재사용] 같은 색인으로 다시 채점 (새 질문 없이)...")
    reuse = run_once(ev, "run1", base, real, False, args.only)
    for rr in runs + [reuse]:
        name = f"{rr['tag']} {'새 판정' if rr['fresh'] else '색인 재사용'}"
        print(f"\n== {name} (호출 {rr['calls']}번, ${rr['cost']}) ==")
        for k, label in KEYS:
            parts = []
            for g, gl in (("base", "기존45"), ("holdout", "새사례1"), ("holdout2", "새사례2")):
                rows_ = [r for r in rr[k] if r.get("group") == g]
                if rows_:
                    parts.append(f"{gl} {rate(rows_)}")
            print(f"  {label}: " + " / ".join(parts))
            for row in rr[k]:
                if not row["ok"]:
                    print("     틀림:", {x: y for x, y in row.items() if x != "ok"})
    for rr in runs + [reuse]:
        cl = rr["cluster"]
        print(f"\n[묶음 검사 {rr['tag']} {'새 판정' if rr['fresh'] else '재사용'}] 키워드 {cl['keywords']}개 → 묶음 {cl['groups']}개, 5개 이상 묶음 {len(cl['big_groups'])}개, "
              f"정답이 false 인 쌍이 같은 묶음: {len(cl['false_pairs_in_same_group'])}건 → {'통과' if cl['ok'] else '실패'}")
        for g in cl["big_groups"]:
            print("   5개 이상 묶음:", ", ".join(g))
        for b in cl["false_pairs_in_same_group"]:
            print("   실패:", b)
    cons = {"fresh_vs_fresh": compare(runs[0], runs[1]) if len(runs) > 1 else None,
            "reuse_vs_run1": compare(runs[0], reuse) if runs else None}
    if cons["fresh_vs_fresh"]:
        c = cons["fresh_vs_fresh"]
        print(f"\n일관성(새 판정 1회차 vs 2회차): {c['total']}건 중 답이 다른 것 {c['different_answers']}건, 지문이 달라진 것 {c['different_fingerprints']}건")
    c = cons["reuse_vs_run1"]
    if c:
        print(f"일관성(색인 재사용 vs 1회차): {c['total']}건 중 답이 다른 것 {c['different_answers']}건")
    out = OUT_DIR / f"eval_judge_{datetime.now():%Y%m%d_%H%M%S}.json"
    write_text_retry(out, json.dumps({"runs": runs, "reuse": reuse, "consistency": cons}, ensure_ascii=False, indent=1))
    print("[저장됨]", out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except pp.UsageLimitError as e:
        print(pp.limit_message(e, "정답 세트 채점"))
        sys.exit(pp.LIMIT_EXIT)
