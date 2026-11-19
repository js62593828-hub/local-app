"""주간 계획 손질 도구: edits JSON 을 원본 계획에 적용해 최종 계획 JSON 으로 저장합니다.
사용: python tools/plan_edits.py --edits pipeline/final/edits_20261116.json --out pipeline/final/plan_20261116.json
앱 데이터는 읽지도 쓰지도 않습니다. 원본 계획은 고치지 않습니다."""
import argparse
import copy
import json
import sys
from collections import Counter
from datetime import datetime

WD = "월화수목금토일"


def nk(s):
    return "".join((s or "").split())


def find(plans, platform, kw, role=None):
    return [p for p in plans if p.get("platform") == platform and nk(p.get("keyword")) == nk(kw)
            and (role is None or p.get("role") == role)]


def apply_edits(plan, edits):
    out = copy.deepcopy(plan)
    plans = out["plans"]
    log, warn = [], []
    for a in edits.get("add_from_plan", []):
        try:
            src = json.load(open(a["source_plan"], encoding="utf-8"))
        except Exception as e:
            warn.append(f"다른 계획 파일을 못 읽음: {a.get('source_plan')} ({e})")
            continue
        hit = find(src.get("plans", []), a["platform"], a["keyword"])
        if len(hit) != 1:
            warn.append(f"가져올 글 못 찾음(찾은 수 {len(hit)}): {a['platform']} {a['keyword']} ← {a['source_plan']}")
            continue
        if find(plans, a["platform"], a["keyword"]):
            warn.append(f"이미 계획에 있어 안 가져옴: {a['platform']} {a['keyword']}")
            continue
        p = copy.deepcopy(hit[0])
        if a.get("publish_date"):
            p["role"] = "scheduled"
            p["publish_date"] = a["publish_date"]
            p["weekday"] = WD[datetime.strptime(a["publish_date"], "%Y-%m-%d").weekday()]
            log.append(f"가져옴(일정): {a['platform']} {a['keyword']} {a['publish_date']} ← {a['source_plan']}")
        elif a.get("role") == "reserve":
            p["role"] = "reserve"
            p["publish_date"] = None
            p["weekday"] = None
            log.append(f"가져옴(예비): {a['platform']} {a['keyword']} ← {a['source_plan']}")
        else:
            warn.append(f"가져오기에 publish_date 도 role:reserve 도 없음: {a['platform']} {a['keyword']}")
            continue
        plans.append(p)
    # 적용 순서: add_from_plan → moves → remove → add_from_reserve → to_reserve → titles → outlines → product_need
    for m in edits.get("moves", []):
        hit = find(plans, m["platform"], m["keyword"], "scheduled")
        if len(hit) != 1:
            warn.append(f"이동 못 함(찾은 수 {len(hit)}): {m['platform']} {m['keyword']}")
            continue
        p = hit[0]
        old = p["publish_date"]
        p["publish_date"] = m["to"]
        p["weekday"] = WD[datetime.strptime(m["to"], "%Y-%m-%d").weekday()]
        log.append(f"이동: {m['platform']} {m['keyword']} {old} → {m['to']}")
    for r in edits.get("remove", []):
        hit = find(plans, r["platform"], r["keyword"], "scheduled")
        if len(hit) != 1:
            warn.append(f"뺄 글 못 찾음(찾은 수 {len(hit)}): {r['platform']} {r['keyword']}")
            continue
        plans.remove(hit[0])
        log.append(f"뺌: {r['platform']} {r['keyword']} ({hit[0]['publish_date']})")
    for a in edits.get("add_from_reserve", []):
        hit = [p for p in plans if p.get("role") == "reserve" and nk(p.get("keyword")) == nk(a["keyword"])]
        if not hit:
            warn.append(f"예비에 없음: {a['keyword']}")
            continue
        p = hit[0]  # 같은 키워드 예비가 둘이면 첫 번째
        p["role"] = "scheduled"
        p["platform"] = a["platform"]
        p["publish_date"] = a["publish_date"]
        p["weekday"] = WD[datetime.strptime(a["publish_date"], "%Y-%m-%d").weekday()]
        log.append(f"예비 → 일정: {a['platform']} {a['keyword']} {a['publish_date']}")
    for r in edits.get("to_reserve", []):
        hit = find(plans, r["platform"], r["keyword"], "scheduled")
        if len(hit) != 1:
            warn.append(f"예비로 못 돌림(찾은 수 {len(hit)}): {r['platform']} {r['keyword']}")
            continue
        p = hit[0]
        old = p["publish_date"]
        p["role"] = "reserve"
        p["publish_date"] = None
        p["weekday"] = None
        log.append(f"일정 → 예비: {r['platform']} {r['keyword']} (원래 {old})")
    for t in edits.get("titles", []):
        hit = find(plans, t["platform"], t["keyword"])
        if len(hit) != 1:
            warn.append(f"제목 바꿀 글 못 찾음(찾은 수 {len(hit)}): {t['platform']} {t['keyword']}")
            continue
        p = hit[0]
        old = p.get("final_title")
        new = t["final_title"]
        alts = p.get("alt_titles")
        swapped = False
        if isinstance(alts, list):
            for i, x in enumerate(alts):
                if x == new or nk(x) == nk(new):
                    alts[i] = old
                    swapped = True
                    break
        p["final_title"] = new
        log.append(f"제목 교체: {t['platform']} {t['keyword']} → {new}" + (" (원래 제목을 대안 제목 자리로)" if swapped else ""))
    for o in edits.get("outlines", []):
        hit = find(plans, o["platform"], o["keyword"])
        if len(hit) != 1:
            warn.append(f"본문 설계 바꿀 글 못 찾음(찾은 수 {len(hit)}): {o['platform']} {o['keyword']}")
            continue
        hit[0]["outline"] = copy.deepcopy(o["outline"])
        log.append(f"본문 설계 교체: {o['platform']} {o['keyword']} ({len(o['outline'])}단락)")
    for n in edits.get("product_need", []):
        hit = find(plans, n["platform"], n["keyword"])
        if len(hit) != 1:
            warn.append(f"제품 필요 바꿀 글 못 찾음(찾은 수 {len(hit)}): {n['platform']} {n['keyword']}")
            continue
        old = hit[0].get("product_need") or {}
        hit[0]["product_need"] = {"need": bool(n["need"]), "count": n["count"], "condition": n["condition"],
                                  "link_kind": "네이버쇼핑" if n["need"] else "필요 없음"}
        log.append(f"제품 필요 교체: {n['platform']} {n['keyword']} need={n['need']} count={n['count']}")
    for i in edits.get("ignore", []):
        if not find(plans, i["platform"], i["keyword"]):
            log.append(f"무시(원래 없음): {i['platform']} {i['keyword']}")
    # 검사
    sched = [p for p in plans if p.get("role") == "scheduled"]
    per = Counter((p["platform"], p["publish_date"]) for p in sched)
    exp = (edits.get("expect_per_day") or {})
    for (pf, d), n in sorted(per.items()):
        if exp.get(pf) and n != exp[pf]:
            warn.append(f"{pf} {d}: {n}편 (기대 {exp[pf]}편)")
    keys = Counter((p["platform"], nk(p["keyword"])) for p in sched)
    for k, n in keys.items():
        if n > 1:
            warn.append(f"같은 플랫폼 같은 키워드 중복: {k}")
    out["edits_applied"] = {"log": log, "warnings": warn}
    out["counts"] = {"scheduled": len(sched), "reserve": sum(1 for p in plans if p.get("role") == "reserve"),
                     "naver": sum(1 for p in sched if p["platform"] == "naver"),
                     "wordpress": sum(1 for p in sched if p["platform"] == "wordpress")}
    return out, log, warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edits", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    edits = json.load(open(a.edits, encoding="utf-8"))
    plan = json.load(open(edits["source_plan"], encoding="utf-8"))
    out, log, warn = apply_edits(plan, edits)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n".join(log)); print("경고:", warn or "없음"); print("저장:", a.out, out["counts"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
