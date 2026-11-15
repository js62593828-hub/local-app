# -*- coding: utf-8 -*-
"""재기획 R1: 후보 모으기 + 신호 수집 (결과는 pipeline/stage_r1_run/ 에만 쓴다. 운영 파일·앱 데이터는 건드리지 않는다)

단계 (같은 명령을 다시 돌리면 저장된 값으로 이어간다)
  python tools/r1_signals.py cands    11/13 전체 실행 후보 전부(result_20261113_183702.json) → 후보 목록 (API 없음)
  python tools/r1_signals.py expand   제철 씨앗 연관 키워드 넓히기 (검색광고 API: 검색량)
  python tools/r1_signals.py trend    데이터랩 주간 추세 (한도 300회)
  python tools/r1_signals.py shape    키워드 모양 묶음 판정 (Claude haiku, 저장 재사용)
  python tools/r1_signals.py build    신호 합치기 + 뽑을 후보 추리기 + 상위 5개 글(블로그 검색, 한도 1,500회) → ranking_r1.json, r1_signals.json
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BASE = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(BASE))

import backtest as bt  # noqa: E402  (Api 래퍼: 호출 수 세기·한도, 검색광고·데이터랩 호출 재사용)
import r1  # noqa: E402

RUN = BASE / "pipeline" / "stage_r1_run"
RESULT = BASE / "pipeline" / "result_20261113_183702.json"
RANKING = BASE / "pipeline" / "ranking.json"
TODAY = date(2026, 11, 14)
bt.RUN = RUN
bt.TODAY = TODAY
bt.BLOG_LIMIT, bt.TREND_LIMIT = 1500, 300
EXCLUDED = {"local", "service", "offtopic", "buyplace", "other"}      # 제외 유형 (브랜드는 싼세계 수익글에 쓸 수 있어 남김)
DROP_WORDS = ["쿠팡", "중고", "무료", "알바", "채용", "구인"]
BUY_WORDS = ["파는곳", "구매처", "구입처", "판매처", "매장", "어디서사", "살수있는곳"]

nospace, jload, jsave, log = bt.nospace, bt.jload, bt.jsave, bt.log


def cfg() -> dict:
    d = json.loads((TOOLS / "filter.json").read_text(encoding="utf-8-sig"))
    return d["r1"]


def raw_cache() -> dict:
    raw = jload(RUN / "raw_cache.json", {}) or {}
    for k in ("vol", "sim", "trend", "expand"):
        raw.setdefault(k, {})
    return raw


def save_raw(raw):
    jsave(RUN / "raw_cache.json", raw)


# ---------------- 1) 후보 ----------------

def cmd_cands(_):
    RUN.mkdir(parents=True, exist_ok=True)
    res = json.loads(RESULT.read_text(encoding="utf-8"))
    rk = {r["keyword"]: r for r in json.loads(RANKING.read_text(encoding="utf-8"))["ranking"]}
    out, why = {}, {"type": 0, "lowvol": 0, "word": 0, "dup": 0}
    for x in res["keywords"]:
        kw, t, vol = x["keyword"], x.get("kw_type"), x.get("monthly_total") or 0
        nk = nospace(kw)
        if t in EXCLUDED:
            why["type"] += 1
        elif vol < cfg()["volume_min"]:
            why["lowvol"] += 1
        elif any(w in nk for w in DROP_WORDS) or any(w in nk for w in BUY_WORDS):
            why["word"] += 1
        elif nk not in out:
            out[nk] = {"keyword": kw, "volume": vol, "src": "result", "old_grade": x.get("grade") or "없음", "kw_type": t,
                       "seed": x.get("seed"), "track": x.get("track"), "in_ranking": kw in rk}
    # 이미 쓴 글과 겹치는 것은 신호를 받기 전에 뺀다(기획 단계가 같은 판정을 한 번 더 한다)
    import plan_posts as pp
    existing, _at = pp.load_existing(BASE / "pipeline" / "existing_keywords.json")
    rows = [{"keyword": v["keyword"]} for v in out.values()]
    keep, dropped = pp.split_existing(rows, existing, 0.75)
    dropset = {nospace(d["keyword"]) for d in dropped}
    for nk in dropset:
        out.pop(nk, None)
    why["dup"] = len(dropset)
    jsave(RUN / "cands.json", out)
    print(f"후보 {len(out)}개 저장 (뺀 것: 제외 유형 {why['type']} · 검색량 {cfg()['volume_min']} 미만 {why['lowvol']} · 금지·구매처 낱말 {why['word']} · 기존 글 중복 {why['dup']})")


# ---------------- 2) 제철 보강 ----------------

def cmd_expand(_):
    c = cfg()
    cands = jload(RUN / "cands.json", {})
    raw = raw_cache()
    api = bt.Api()
    seeds = list(c["seasonal_seeds"])
    # 실적 상위 글의 대상(시험 결과)도 씨앗으로
    for s in ("문풍지", "단열뽁뽁이", "전기장판 멀티탭", "패딩세탁", "구스이불세탁", "양모이불세탁", "수도꼭지 물샘", "김장 준비물"):
        if s not in seeds:
            seeds.append(s)
    cen = {}
    for s in seeds:
        ns = nospace(s)
        if ns in raw["expand"]:
            continue
        lst = api.searchad([s])
        if lst is None:
            continue
        raw["expand"][ns] = [{"kw": it.get("relKeyword"), "pc": api.cm.to_int(it.get("monthlyPcQcCnt")), "mo": api.cm.to_int(it.get("monthlyMobileQcCnt"))}
                             for it in lst]
        api.save()
        save_raw(raw)
    added = 0
    per_seed_max = 10
    for s in seeds:
        ns = nospace(s)
        rows = [x for x in raw["expand"].get(ns, []) if x.get("kw")]
        self_vol = next(((x["pc"] + x["mo"]) for x in rows if nospace(x["kw"]) == ns), None)
        cen[ns] = {"word": s, "volume": self_vol}
        rows = [x for x in rows if ns in nospace(x["kw"]) and (x["pc"] + x["mo"]) >= c["volume_min"] and nospace(x["kw"]) != ns]
        rows.sort(key=lambda x: -(x["pc"] + x["mo"]))
        n = 0
        for x in rows:
            nk = nospace(x["kw"])
            if nk in cands or any(w in nk for w in DROP_WORDS + BUY_WORDS):
                continue
            cands[nk] = {"keyword": x["kw"], "volume": x["pc"] + x["mo"], "src": "expand", "old_grade": "없음", "kw_type": None,
                         "seed": s, "track": None, "in_ranking": False}
            n += 1
            added += 1
            if n >= per_seed_max:
                break
    # 중심 낱말(씨앗) 자체도 신호를 받는다
    for ns, v in cen.items():
        if v["volume"] and ns not in cands:
            cands[ns] = {"keyword": v["word"], "volume": v["volume"], "src": "center", "old_grade": "없음", "kw_type": None,
                         "seed": v["word"], "track": None, "in_ranking": False}
    jsave(RUN / "cands.json", cands)
    jsave(RUN / "centers.json", cen)
    print(f"제철 보강: 씨앗 {len(seeds)}개 → 새 후보 {added}개 (전체 {len(cands)}개). 사용량 {api.usage}")


# ---------------- 3) 데이터랩 ----------------

def cmd_trend(_):
    cands = jload(RUN / "cands.json", {})
    raw = raw_cache()
    api = bt.Api()
    for nk, v in cands.items():
        raw["vol"].setdefault(nk, {"total": v["volume"]})
    kws = {nk: v["keyword"] for nk, v in cands.items()}
    left = bt.TREND_LIMIT - api.usage["trend"]
    todo = [nk for nk in kws if nk not in raw["trend"]]
    # 우선순위: 중심 낱말·제철 보강 → 물건 모양(choose) → 나머지, 같은 묶음 안은 검색량 큰 순 (5개씩 묶어 부르므로 크기가 비슷한 것끼리)
    pr = lambda nk: (0 if cands[nk]["src"] in ("center", "expand") else (1 if cands[nk].get("kw_type") != "brand" else 2), -cands[nk]["volume"])
    todo.sort(key=pr)
    cap = left * 5
    if len(todo) > cap:
        print(f"[주의] 남은 데이터랩 호출 {left}회로 {cap}개까지만 가능 — 우선순위 낮은 {len(todo) - cap}개는 신호 없음으로 남깁니다")
        todo = todo[:cap]
    todo.sort(key=lambda nk: -cands[nk]["volume"])
    end = (TODAY - bt.timedelta(days=1)).isoformat()
    n = 0
    try:
        for i in range(0, len(todo), 5):
            chunk = todo[i:i + 5]
            body = {"startDate": bt.TREND_START.isoformat(), "endDate": end, "timeUnit": "week",
                    "keywordGroups": [{"groupName": f"g{k}", "keywords": [kws[nk]]} for k, nk in enumerate(chunk)]}
            j = api.trend(body)
            if not j or not j.get("results"):
                continue
            by = {r.get("title"): r.get("data", []) for r in j["results"]}
            for k, nk in enumerate(chunk):
                raw["trend"][nk] = {d["period"][:10]: float(d.get("ratio") or 0) for d in by.get(f"g{k}", [])}
            n += 1
            if n % 10 == 0:
                api.save()
                save_raw(raw)
    except bt.Budget as e:
        print("[멈춤]", e)
    api.save()
    save_raw(raw)
    print(f"데이터랩 {n}회 받음. 누적 사용량 {api.usage}, 추세 값 있는 후보 {len(raw['trend'])}/{len(cands)}")


# ---------------- 4) 모양 ----------------

SHAPE_PROMPT = """아래는 생활 블로그 키워드 목록이다. 도구는 쓰지 말고 바로 답해라.
키워드마다 모양(shape)을 다음 중 정확히 하나로 정해라.
 - 제철 물건: 계절·시기에 따라 찾는 물건 자체가 대상(전기장판, 온수매트, 방풍비닐, 난방텐트, 가습기, 패딩, 겨울이불, 제습기 같은 것)
 - 상시 물건: 계절과 상관없이 사는 물건이 대상
 - 급한 증상: 지금 막 생긴 문제(막힘, 곰팡이, 냄새, 누수, 고장, 결로, 동파 같은 것)
 - 방법·순서: 하는 법·절차·점검 순서·관리법
 - 고르기·비교: 추천·비교·고르는 기준·종류
 - 브랜드·모델: 특정 브랜드나 모델명이 들어간 것
 - 기타
반드시 아래 JSON 한 덩어리로만 답해라(설명 금지).
{{"items": [{{"id": "X1", "shape": "방법·순서"}}]}}

[키워드 목록]
{lines}
"""


def cmd_shape(_):
    cands = jload(RUN / "cands.json", {})
    raw = raw_cache()
    done = jload(RUN / "shape.json", {}) or {}
    todo = [nk for nk in cands if nk in raw["trend"] and nk not in done]
    if not todo:
        print("이미 모양 판정이 끝나 있습니다(Claude 호출 없음).")
        return
    import plan_posts as pp
    api = bt.Api()
    cost = 0.0
    for i in range(0, len(todo), 150):
        chunk = todo[i:i + 150]
        ids = {f"X{k + 1}": nk for k, nk in enumerate(chunk)}
        lines = "\n".join(f"{xid} | {cands[nk]['keyword']}" for xid, nk in ids.items())
        api.usage["claude"] += 1
        api.save()
        env = pp.call_claude(SHAPE_PROMPT.format(lines=lines), "haiku")
        cost += float(env.get("total_cost_usd") or 0)
        items = pp.extract_json(env.get("result", "")).get("items", [])
        for it in items:
            nk = ids.get(it.get("id"))
            if nk and it.get("shape") in r1.SHAPES:
                done[nk] = it["shape"]
        jsave(RUN / "shape.json", done)
    api.usage["claude_cost_usd"] = round(float(api.usage.get("claude_cost_usd") or 0) + cost, 4)
    api.save()
    print(f"모양 판정 {len(done)}/{len(cands)} 저장 (haiku {-(-len(todo) // 150)}회, 비용 ${cost:.3f})")


# ---------------- 5) 합치기 ----------------

def signals_for(nk, raw, shapes, c):
    ser = raw["trend"].get(nk)
    s = r1.week_signals(ser, date.fromisoformat(c["plan_monday"]), date.fromisoformat(c["recent_monday"]))
    s["shape"] = shapes.get(nk)
    s["has_trend"] = bool(ser)
    return s


def cmd_build(_):
    c = cfg()
    cands = jload(RUN / "cands.json", {})
    raw = raw_cache()
    shapes = jload(RUN / "shape.json", {}) or {}
    cen = jload(RUN / "centers.json", {}) or {}
    sig = {}
    for nk, v in cands.items():
        s = signals_for(nk, raw, shapes, c)
        s.update({"keyword": v["keyword"], "volume": v["volume"], "old_grade": v["old_grade"], "src": v["src"], "seed": v["seed"], "kw_type": v.get("kw_type")})
        sig[nk] = s
    # 중심 낱말(토픽) 목록: 이름이 후보 키워드에 들어 있는 가장 긴 중심 낱말
    centers = {ns: {**v, **{k: sig[ns][k] for k in ("ratio", "peak", "shape", "from_zero") if ns in sig}} for ns, v in cen.items()}
    for ns, v in centers.items():
        if v.get("volume") is None and ns in sig:
            v["volume"] = sig[ns]["volume"]
    cw_names = sorted(centers, key=lambda k: -len(k))
    for nk, s in sig.items():
        s["cw"] = next((ns for ns in cw_names if ns in nk), None)
    jsave(RUN / "r1_signals.json", {"made": TODAY.isoformat(), "plan_monday": c["plan_monday"], "recent_monday": c["recent_monday"],
                                   "signals": sig, "centers": centers})
    have = sum(1 for s in sig.values() if s["has_trend"])
    print(f"신호 합치기: 후보 {len(sig)}개 중 추세 있음 {have}개, 모양 있음 {sum(1 for s in sig.values() if s['shape'])}개")
    # ---- 뽑을 후보 추리기(기획 입력) ----
    short = shortlist(sig, centers, c)
    print(f"추린 후보 {len(short['all'])}개 (네이버식 {len(short['naver'])} · 싼세계 정보 {len(short['wp_info'])} · 싼세계 수익 {len(short['wp_money'])})")
    jsave(RUN / "shortlist.json", {k: v for k, v in short.items() if k != "topics_detail"} | {"topics": short["topics_detail"]})
    build_ranking(short["all"], sig, cands, c, centers)


def shortlist(sig, centers, c):
    nv, wp = c["naver"], c["wordpress"]
    vmin = c["volume_min"]
    items = [(nk, s) for nk, s in sig.items() if s["has_trend"] and s["volume"] >= vmin]
    # 네이버식: 브랜드 제외, 점수 순 (같은 점수는 추세 비율 큰 순, 마지막은 이름)
    nav = [(nk, s) for nk, s in items if s.get("kw_type") != "brand"]
    nav.sort(key=lambda t: (-r1.naver_score(t[1], c), -(t[1].get("ratio") or 0), t[0]))
    timed = [nk for nk, s in nav if r1.is_timed(s, c)]
    ever = [nk for nk, s in nav if s.get("shape") in nv["evergreen_shapes"] and not r1.is_timed(s, c)]      # 시기 조건이 아닌 상시 급한 증상·방법
    ever_t = [nk for nk, s in nav if s.get("shape") in nv["evergreen_shapes"] and r1.is_timed(s, c)]
    nav_keep = timed[:90] + ever[:60] + [nk for nk in ever_t if nk not in timed[:90]][:20]
    # 싼세계 정보글: 중심 낱말 ≥ 5000, 점수 순으로 주제 후보 8개까지, 그 주제의 키워드들
    tops = []
    for ns, v in centers.items():
        if (v.get("volume") or 0) >= wp["topic_volume_min"] and ns in sig:
            tops.append((r1.topic_score(sig[ns], c), ns))
    tops.sort(key=lambda t: (-t[0], -(centers[t[1]].get("volume") or 0), t[1]))
    topic_names = [ns for _sc, ns in tops[:16]]
    wp_info = [nk for nk, s in items if s.get("cw") in topic_names and s.get("kw_type") != "brand"]
    wp_info.sort(key=lambda nk: (topic_names.index(sig[nk]["cw"]), -r1.naver_score(sig[nk], c), nk))
    per_cap, cnt, kept = 24, {}, []
    for nk in wp_info:        # 주제마다 최대 24개(주제가 한 곳에 쏠려 후보가 부풀지 않게)
        cw_ = sig[nk]["cw"]
        if cnt.get(cw_, 0) < per_cap:
            cnt[cw_] = cnt.get(cw_, 0) + 1
            kept.append(nk)
    wp_info = kept
    # 싼세계 수익글: 브랜드 포함, 모든 후보에서 시기·모양 점수 순 (조건·브랜드·모델명 허용)
    money_pool = [(nk, s) for nk, s in items if (s.get("kw_type") == "brand" or s.get("shape") in ("브랜드·모델", "고르기·비교", "제철 물건", "상시 물건"))]
    money_pool.sort(key=lambda t: (-r1.naver_score(t[1], c), -t[1]["volume"], t[0]))
    wp_money = [nk for nk, _s in money_pool[:70]]
    allk = []
    for nk in nav_keep + wp_info[:260] + wp_money:
        if nk not in allk:
            allk.append(nk)
    return {"all": allk, "naver": nav_keep, "wp_info": wp_info[:260], "wp_money": wp_money,
            "topics_detail": [{"center": ns, "volume": centers[ns].get("volume"), "score": sc} for sc, ns in tops[:16]]}


def build_ranking(keys, sig, cands, c, centers):
    """기획 입력(ranking_r1.json): 11/13 ranking.json 의 풍부한 행은 그대로 쓰고, 없는 행은 기본값으로 만든다.
    상위 5개 글은 golden_cache 에 있으면 그것을, 없으면 블로그 검색 상위 5개(제목·날짜)로 대신한다(본문은 안 엶)."""
    import html
    rk = {r["keyword"]: r for r in json.loads(RANKING.read_text(encoding="utf-8"))["ranking"]}
    gc = jload(BASE / "pipeline" / "golden_cache.json", {}) or {}
    res = json.loads(RESULT.read_text(encoding="utf-8"))
    rrow = {x["keyword"]: x for x in res["keywords"]}
    raw = raw_cache()
    api = bt.Api()
    rows = []
    cw_pool = {}
    for nk in keys:
        cw_pool.setdefault(sig[nk].get("cw") or "", []).append(cands[nk]["keyword"])
    n_api, n_cache = 0, 0
    for nk in keys:
        v, s = cands[nk], sig[nk]
        kw = v["keyword"]
        base = dict(rk.get(kw) or {})
        x = rrow.get(kw) or {}
        if not base:
            base = {"keyword": kw, "seed": v.get("seed"), "score": {"demand": 0, "saturation": 0, "screen": 0, "fit": 0, "total": 0},
                    "monthly_total": v["volume"], "blog_docs": x.get("blog_docs"), "saturation": x.get("saturation"),
                    "trend_growth": None, "trend_steady": None, "smart_blocks": [], "related_keywords": [],
                    "kw_type": x.get("kw_type") or v.get("kw_type"), "platform_hint": x.get("platform_hint"),
                    "fingerprint": x.get("fingerprint"), "track": x.get("track")}
            if x.get("serp") and (x["serp"] or {}).get("smart_blocks"):
                base["smart_blocks"] = x["serp"]["smart_blocks"]
        if not base.get("related_keywords"):
            base["related_keywords"] = [k for k in cw_pool.get(s.get("cw") or "", []) if k != kw][:12]
        base["grade"] = v["old_grade"] if v["old_grade"] != "없음" else None
        base["old_grade_text"] = v["old_grade"]
        base["r1_src"] = v["src"]
        # 상위 5개 글
        g = ((gc.get(kw) or {}).get("v") or {})
        tp = g.get("top_posts") or ((base.get("golden") or {}).get("top_posts"))
        if tp and tp.get("posts"):
            base["golden"] = {**(base.get("golden") or {}), "top_posts": tp}
            n_cache += 1
        else:
            sim = raw["sim"].get(nk)
            if not sim:
                try:
                    j = api.blog(query=kw, display=100, start=1, sort="sim")
                except bt.Budget:
                    j = None
                if j and "items" in j:
                    sim = {"total": j.get("total"), "items": [{"title": html.unescape(bt.TAG_RE.sub("", it.get("title", ""))),
                                                                 "date": str(it.get("postdate", "")), "blog": it.get("bloggername", "")}
                                                                for it in j["items"][:100]]}
                    raw["sim"][nk] = sim
                    n_api += 1
                    if n_api % 20 == 0:
                        api.save()
                        save_raw(raw)
            if sim:
                posts = []
                for r_, it in enumerate(sim["items"][:5], 1):
                    d = bt.pdate(it.get("date") if "-" in str(it.get("date")) else (str(it["date"])[:4] + "-" + str(it["date"])[4:6] + "-" + str(it["date"])[6:8]))
                    age = (TODAY - d).days if d else None
                    posts.append({"rank": r_, "title": it["title"], "blog_name": it.get("blog"), "date": d.isoformat() if d else None,
                                  "age_days": age, "chars": None, "images": None, "influencer": False, "official_badge": False, "opened": False})
                base["golden"] = {**(base.get("golden") or {}), "top_posts": {"posts": posts, "source": "블로그 검색 API 상위 5개(본문 안 엶)"}}
        base["r1"] = {k: s.get(k) for k in ("ratio", "peak", "shape", "from_zero", "cw", "volume", "old_grade")}
        cwv = centers.get(s.get("cw") or "") or {}
        base["r1"]["cw_volume"] = cwv.get("volume")
        base["r1"]["cw_sig"] = {k: cwv.get(k) for k in ("ratio", "peak", "shape", "from_zero")}
        rows.append(base)
    api.save()
    save_raw(raw)
    out = {"generated_at": TODAY.isoformat(), "result_file": RESULT.name, "mode": "golden", "r1": True,
           "intent_groups": json.loads(RANKING.read_text(encoding="utf-8")).get("intent_groups"), "ranking": rows}
    jsave(RUN / "ranking_r1.json", out)
    print(f"ranking_r1.json {len(rows)}행 저장 (상위 글: 캐시 {n_cache} · 블로그 검색 새로 {n_api}). 사용량 {api.usage}")


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    cmds = {"cands": cmd_cands, "expand": cmd_expand, "trend": cmd_trend, "shape": cmd_shape, "build": cmd_build}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        return 2
    cmds[sys.argv[1]](None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
