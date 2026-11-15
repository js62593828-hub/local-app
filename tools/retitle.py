# -*- coding: utf-8 -*-
"""제목 단계만 다시 돌리기 (기획 Claude 호출 없음). 이미 만든 plan_날짜.json 의 글 목록으로 plan_posts.choose_titles 를 다시 실행하고
제목 관련 칸(final_title·title_pattern·alt_titles·intent 등)만 갈아 끼운다. 제목 캐시(title_cache_날짜.json)가 다 차 있으면 Claude 호출 0회.

4.6단계 추가: 검색 의도(intent)를 정한 뒤 수익글(track=money)인데 '집에서 쓸 제품을 고르려는 의도'가 아니면 수익글에서 빼고(이번 주 제외, plan.money_swaps 에 사유),
그 자리는 은행(--bank)에 저장된 다음 수익 후보로 채운다(새 탐색 없음). 못 채우면 부족으로 기록.

4.7단계: 의도 판정은 plan_posts 와 같은 저장소(out_dir/intent_info_cache.json)·같은 함수(ask_intent_info)를 쓴다. 전체 실행(plan_posts)은 기획 전에 이미 확인하므로
여기서 빠지는 수익글은 보통 없다(옛 plan 파일을 다시 정리할 때만 쓴다).

사용: python tools/retitle.py <plan 파일> [--bank pipeline/.../golden_bank.json] [--filter tools/filter.json]
환경: LOCALAPP_INDEX_DIR 로 내 기존 글 색인 폴더를 지정하면 그 폴더의 것을 쓴다(시험 폴더용).
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_posts as pp  # noqa: E402
import titles as ttl  # noqa: E402

KEYS = ("final_title", "title_keyword", "title_pattern", "alt_titles", "alt_patterns", "intent", "intent_info", "title_natural",
        "title_scores", "title_candidates")
MAX_ROUNDS = 4


def to_item(p: dict) -> dict:
    return {"keyword": p["keyword"], "platform": p.get("platform"), "track": p.get("track") or "info", "timing": p.get("timing"),
            "related_keywords": p.get("related_keywords"), "pair_with": p.get("pair_with"), "smart_blocks": p.get("smart_blocks"),
            "golden": {"top_posts": {"posts": p.get("top_posts") or []}}}


def row_of(p: dict) -> dict:
    """plan 항목을 한도 계산용 행(은행 행과 같은 칸 이름)으로 바꾼다."""
    fp = (p.get("fingerprint") or "").split("|")
    return {"keyword": p["keyword"], "platform": p.get("platform"), "kw_category": p.get("category"), "kw_topic": p.get("topic"),
            "kw_topic_group": p.get("topic_group"), "kw_detail": p.get("detail"), "kw_target": fp[0] if fp and fp[0] else p.get("topic"),
            "track": p.get("track"), "cover_with": p.get("cover_with") or []}


def find_replacement(plan: dict, bank: list, w: dict, slot: dict, banned: set):
    """은행의 저장된 수익 후보 중 같은 한도(비슷한 키워드·넓고 좁은 관계·카테고리·대상·넓은 주제·세부·수익글 대상·해충)를 지키는 첫 후보.
    순서 = 예상 클릭 큰 순. 일정 자리(slot.platform)가 있으면 그 플랫폼 기준으로 센다. → (은행 행, 사유) 또는 (None, 사유)"""
    st = plan["settings"]
    th = st["similarity_threshold"]
    members = [row_of(p) for p in plan["plans"]]
    names = {m["keyword"] for m in members} | banned
    listed = {u["keyword"]: u for u in plan.get("unscheduled_sa") or []}
    pest_words = w.get("pest_words") or pp.DEFAULT_PEST_WORDS
    plat = slot.get("platform")
    cnt = lambda key, val, plf=None: sum(1 for m in members if val and m.get(key) == val and (plf is None or m.get("platform") == plf))
    cands = sorted((r for r in bank if r.get("track") == "money"), key=lambda r: (-(r.get("expected_clicks") or 0), r["keyword"]))
    why = {}
    for r in cands:
        kw = r["keyword"]
        if kw in names:
            continue
        u = listed.get(kw)
        if not u or u.get("reason_code") not in ("capacity", "cap"):
            why[kw] = "이번 기획에서 이미 제외된 후보"
            continue
        if r.get("kw_type") in pp.EXCLUDED_KW_TYPES or not r.get("solves") or (r.get("monthly_total") or 0) < st["schedule_volume_min"]:
            why[kw] = "수익글 조건 미달"
            continue
        if any(pp.similar(kw, m["keyword"], th) for m in members):
            why[kw] = "배정된 키워드와 거의 같음"
            continue
        if any(m["keyword"] in (r.get("cover_with") or ()) or kw in (m.get("cover_with") or ()) for m in members):
            why[kw] = "배정된 키워드와 넓고 좁은 관계"
            continue
        cat, top, grp = r.get("kw_category"), r.get("kw_topic"), r.get("kw_topic_group")
        det = r.get("kw_detail") if r.get("kw_detail") != "일반" else None
        tgt = r.get("kw_target") or top
        if slot.get("role") == "scheduled" and cat and cnt("kw_category", cat, plat) >= st["max_per_category_per_platform"]:
            why[kw] = "같은 카테고리 한도"
            continue
        if top and cnt("kw_topic", top) >= st["max_per_topic"]:
            why[kw] = "같은 대상 한도"
            continue
        if grp and cnt("kw_topic_group", grp) >= st["max_per_topic_group"]:
            why[kw] = "같은 넓은 주제 한도"
            continue
        if det and cnt("kw_detail", det) >= st["max_per_detail"]:
            why[kw] = "같은 세부 한도"
            continue
        if tgt and sum(1 for m in members if m["track"] == "money" and m["kw_target"] == tgt) >= st["max_money_per_target"]:
            why[kw] = "같은 대상 수익글 한도"
            continue
        if pp.is_pest(r, pest_words) and sum(1 for m in members if pp.is_pest(m, pest_words) and m.get("platform") == plat) >= st["max_pest_per_platform"]:
            why[kw] = "해충 글 한도"
            continue
        return r, "은행의 다음 수익 후보 (예상 클릭 %s)" % r.get("expected_clicks")
    return None, "저장된 수익 후보 중 조건을 지키는 것이 없음"


def new_entry(r: dict, slot: dict, note: str, pair) -> dict:
    return {"platform": slot.get("platform"), "role": slot["role"], "publish_date": slot.get("publish_date"), "weekday": slot.get("weekday"),
            "keyword": r["keyword"], "seed": r.get("seed"), "score": r.get("score"), "fingerprint": r.get("fingerprint"), "kw_type": r.get("kw_type"),
            "platform_hint": r.get("platform_hint"), "track": "money", "category": r.get("kw_category"), "topic": r.get("kw_topic"),
            "topic_group": r.get("kw_topic_group"), "detail": r.get("kw_detail"), "problem": r.get("kw_problem"),
            "variant_spellings": r.get("variant_spellings"), "grade": r.get("grade"), "expected_traffic": r.get("expected_traffic"),
            "found_round": r.get("found_round"), "expected_clicks": r.get("expected_clicks"), "clicks_basis": r.get("clicks_basis"),
            "timing": r.get("timing"), "monthly_total": r.get("monthly_total"), "saturation": r.get("saturation"),
            "trend_growth": r.get("trend_growth"), "trend_steady": r.get("trend_steady"), "smart_blocks": r.get("smart_blocks"),
            "related_keywords": r.get("related_keywords"), "pair_with": pair, "cover_with": r.get("cover_with") or [],
            "search_intent": None, "gaps": [], "outline": [], "experience_slots": [], "beat_top": [], "target_chars": 2500, "target_images": 8,
            "top_posts": [], "swapped_in": note}


def pair_of(r: dict, slot: dict, plan: dict):
    """새로 들어온 수익글의 짝 정보글(같은 주 일정의 정보글 → 이미 쓴 글). 못 찾으면 None."""
    info = [{**row_of(p), "platform": p.get("platform"), "kw_problem": p.get("problem"), "expected_traffic": p.get("expected_traffic")}
            for p in plan["plans"] if p.get("track") != "money" and p.get("role") == "scheduled"]
    try:
        return pp.choose_pair(r, slot.get("platform"), info, pp.pair_posts(), None, allow_plan=True)
    except Exception:
        return None


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--bank")
    ap.add_argument("--filter")
    a = ap.parse_args()
    path = Path(a.plan)
    plan = json.loads(path.read_text(encoding="utf-8"))
    monday = date.fromisoformat(plan["week"]["start"])
    rules = ttl.load_rules(a.filter)
    weekly = json.loads(Path(a.filter or pp.DEFAULT_FILTER).read_text(encoding="utf-8-sig")).get("weekly") or {}
    bank = []
    if a.bank:
        try:
            bank = json.loads(Path(a.bank).read_text(encoding="utf-8")).get("items") or []
        except (OSError, ValueError):
            bank = []
    guide = pp.title_guide(rules["forbidden_words"], ttl.style_samples(ttl.own_titles()))
    batches, problems = [], []
    swaps = list(plan.get("money_swaps") or [])
    banned = {s["removed"] for s in swaps} | {s["replaced_by"] for s in swaps if s.get("replaced_by") and s.get("replacement_rejected")}
    res = {}
    for rnd in range(MAX_ROUNDS):
        problems.clear()
        res = pp.choose_titles([to_item(p) for p in plan["plans"]], {}, path.parent, monday, rules, guide, batches, problems)
        off = [p for p in plan["plans"] if p.get("track") == "money" and (res[p["keyword"]].get("intent_info") or {}).get("buy") is False]
        if not off:
            break
        for p in off:                       # 수익글에서 뺀다 → 이번 주 제외, 그 자리는 은행의 다음 수익 후보로
            slot = {k: p.get(k) for k in ("platform", "role", "publish_date", "weekday")}
            plan["plans"].remove(p)
            sw = {"removed": p["keyword"], "role": p["role"], "platform": p.get("platform"), "intent": res[p["keyword"]].get("intent"),
                  "reason": "수익글 의도 아님: 집에서 쓸 제품을 고르려는 검색이 아님 → 이번 주 제외", "replaced_by": None}
            banned.add(p["keyword"])
            r, why = find_replacement(plan, bank, weekly, slot, banned)
            if r:
                ent = new_entry(r, slot, f"{p['keyword']} 대신 들어옴: {why}", None)
                ent["pair_with"] = pair_of(r, slot, plan)
                tmp = {"keyword": r["keyword"]}                  # 은행 후보는 상위 5개 글이 없음 → 저장된 golden_cache 에서 읽어 붙임(기획 전 단계와 같은 함수)
                pp.attach_cached_top_posts([tmp])
                ent["top_posts"] = pp.top_posts_brief(tmp)
                plan["plans"].append(ent)
                sw.update({"replaced_by": r["keyword"], "replace_note": why})
            else:
                sw["replace_note"] = why
                plan.setdefault("shortage", {})
                plan["shortage"]["money"] = plan["shortage"].get("money", 0) + 1
            swaps.append(sw)
        plan["plans"].sort(key=lambda x: (x.get("role") != "scheduled", x.get("publish_date") or "", 0 if x.get("platform") == "naver" else 1,
                                          0 if x.get("track") == "info" else 1))
    else:
        problems.append("수익글 교체가 %d바퀴 안에 끝나지 않음" % MAX_ROUNDS)
    for p in plan["plans"]:
        p.update({k: v for k, v in res[p["keyword"]].items() if k in KEYS})
    plan["money_swaps"] = swaps
    # 새로 들어온 글이 뒤에서 또 빠졌다면(의도 아님) 교체 기록에 표시
    for s in swaps:
        if s.get("replaced_by") and not any(p["keyword"] == s["replaced_by"] for p in plan["plans"]):
            s["replacement_rejected"] = True
    plan["title_retried"] = [k for k, v in res.items() if v["retried"]]
    plan["title_pattern_counts"] = ({pl: {pat: sum(1 for p in plan["plans"] if p["role"] == "scheduled" and p["platform"] == pl and p.get("title_pattern") == pat)
                                          for pat in ttl.PATTERNS} for pl in ("naver", "wordpress")}
                                    | {"reserve": {pat: sum(1 for p in plan["plans"] if p["role"] == "reserve" and p.get("title_pattern") == pat) for pat in ttl.PATTERNS}})
    sw_prob = [f"수익글 '{s['removed']}' 제외 후 대신 넣을 후보 없음 (수익글 부족)" for s in swaps if not s.get("replaced_by")]
    plan["problems"] = [x for x in plan.get("problems", []) if "제목" not in str(x) and "수익글" not in str(x)] + problems + sw_prob
    plan.setdefault("retitle_runs", []).append({"claude_calls": len(batches), "cost_usd": round(sum(b.get("total_cost_usd") or 0 for b in batches), 4),
                                                "batches": [{k: b.get(k) for k in ("batch", "keywords", "total_cost_usd")} for b in batches]})
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"제목 다시 정함: Claude {len(batches)}회, 비용 ${sum(b.get('total_cost_usd') or 0 for b in batches):.3f}, 문제 {len(problems)}건")
    for s in swaps:
        print(f"  수익글 제외: {s['removed']} → 대신 {s.get('replaced_by') or '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
