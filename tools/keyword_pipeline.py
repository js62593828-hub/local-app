# -*- coding: utf-8 -*-
"""주간 키워드·제목 발굴 + 자동 승인 관문 (N데스크피커 radar_naver 용).

  python tools/keyword_pipeline.py --week 2026-11-23                 # 전체 실행(claude 사용)
  python tools/keyword_pipeline.py --week 2026-11-23 --no-claude     # 제목을 규칙 템플릿으로(claude 호출 0)
  python tools/keyword_pipeline.py --week 2026-11-23 --until lookup  # 조회까지만
  python tools/keyword_pipeline.py --week 2026-11-23 --apply         # 결과 plan.json 을 대기열에 넣음(push_plan --channel radar_naver --apply)

기준은 tools/radar_rules.md(Desk Picker 규칙)와 tools/filter.json 의 "radar_kw" 묶음뿐이다. 옛 생활레이더 자료는 읽지 않는다.
실제 데이터(content_queue*.json, articles*.json)는 읽기만 한다. --apply 가 아니면 대기열에 아무것도 쓰지 않는다.
결과: pipeline/keyword_run/<주시작일>_<채널>/ 의 keywords.json · plan.json · report.md · status.json (+ 이어 받기용 raw_cache.json · usage.json · titles_cache.json)
끊겨도 같은 명령을 다시 실행하면 저장된 API 값·claude 제목은 다시 받지 않고 이어 간다(weekly_plan.py 와 같은 방식).
"""
import argparse
import csv
import glob
import json
import math
import os
import random
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BASE = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(BASE))

import backtest as bt  # noqa: E402
import plan_posts as pp  # noqa: E402
import r1_signals as rs  # noqa: E402
import weekly_plan as wp  # noqa: E402  (parse_seeds·read_lines 만 재사용)

nk, jload, jsave, log = bt.nospace, bt.jload, bt.jsave, bt.log

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
RULES_MD = TOOLS / "radar_rules.md"
# 씨앗 출처: 쇼핑커넥트 상품 수집 1차(시험·후보 고르기)·2차(범위 확대) 결과 파일
SEED_SRC = [
    ("1차 상품 수집(시험)", "pipeline/stage_desk_probe_run/products_raw.csv", "검색어"),
    ("1차 상품 수집(후보 고르기)", "pipeline/stage_desk_pick_run/candidates.csv", "검색어"),
    ("2차 상품 수집(범위 확대)", "pipeline/stage_desk_pick2_run/candidates2.csv", "씨앗"),
    ("2차 상품 수집(씨앗 목록)", "pipeline/stage_desk_pick2_run/seeds.csv", "씨앗"),
]
UNDECIDED = "갈래 미정"
STAGES = ["seeds", "lookup", "score", "titles", "gate", "build"]

A = None
D = None
WEEK = None
RK = None
RUN_ID = None
RELATED_SRC = "연관 키워드(검색광고)"
MODEL_SRC = "2차 상품 수집 모델명"
_stop = {"reason": None}


# ---------------- 공통 ----------------
def load_rk():
    d = json.loads((TOOLS / "filter.json").read_text(encoding="utf-8-sig"))
    if "radar_kw" not in d:
        raise SystemExit("tools/filter.json 에 radar_kw 묶음이 없습니다.")
    return d["radar_kw"]


def status(state, stage=None, reason=None, **extra):
    s = {"state": state, "stage": stage, "reason": reason, "run_id": RUN_ID, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    s.update(extra)
    jsave(D / "status.json", s)


def stop(stage, reason):
    """멈춤 알림: 상태를 저장하고 안내를 출력한 뒤 끝낸다(같은 명령으로 이어 가기)."""
    status("멈춤", stage, reason)
    print(f"[멈춤] {stage} 단계: {reason} — 같은 명령으로 이어서 하세요.")
    sys.exit(0)


def usage_get():
    return jload(D / "usage.json", {}) or {}


def claude_count():
    return int(usage_get().get("claude") or 0)


def claude_used(env, ok=True):
    """호출 1회를 기록한다(실패한 호출도 상한에 센다)."""
    u = usage_get()
    u.setdefault("claude_calls", []).append({"model": RK["claude_model"], "ok": ok, "cost_usd": float((env or {}).get("total_cost_usd") or 0)})
    u["claude"] = len(u["claude_calls"])
    for k in ("blog", "trend", "searchad"):
        u.setdefault(k, 0)
    jsave(D / "usage.json", u)


def sim_ratio(a, b):
    na, nb = pp.norm(a), pp.norm(b)
    return 1.0 if na == nb else SequenceMatcher(None, na, nb).ratio()


def overlaps(keyword, title, th):
    """겹침: 비교용으로 정리한 두 글자열이 같거나 비율이 th 이상, 또는 키워드(4자 이상)가 기존 글 제목 안에 들어 있음(plan_posts.split_existing 과 같은 생각)."""
    a, b = pp.norm(keyword), pp.norm(title)
    if a == b:
        return 1.0
    if len(a) >= 4 and a in b:
        return 0.95
    r = SequenceMatcher(None, a, b).ratio()
    return r if r >= th else 0.0


def model_tokens(keyword):
    """영문+숫자가 한 낱말에 같이 있고 단위(5000mah)·규격(usb3) 낱말이 아닌 것."""
    units = sorted(RK["unit_tokens"], key=len, reverse=True)
    ualt = "|".join(re.escape(u) for u in units)
    ualt2 = "|".join(re.escape(u) for u in units if len(u) >= 2)      # 한 글자 단위는 '숫자+단위'(20w)일 때만 단위로 봄(G502·M720 같은 모델명은 살림)
    out = []
    for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\.]*", keyword):
        t = t.strip("-.")
        if not (re.search(r"[A-Za-z]", t) and re.search(r"\d", t)) or len(t) < RK["model_min_len"]:
            continue
        low = t.lower()
        if re.fullmatch(rf"\d+(\.\d+)?({ualt})", low) or re.fullmatch(rf"({ualt2})\d+(\.\d+)?", low):
            continue
        out.append(t)
    return out


def theme_of(word):
    n = nk(word)
    for th in RK["theme_order"]:
        if any(nk(w) in n for w in RK["theme_words"].get(th, [])):
            return th
    return UNDECIDED


def rel(p):
    try:
        return str(Path(p).resolve().relative_to(BASE.resolve())).replace("\\", "/")
    except ValueError:
        return str(p)


# ---------------- 1) 씨앗 ----------------
def read_csv_col(path, col):
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            v = (r.get(col) or "").strip()
            if v:
                out.append(v)
    return out


def perf_posts():
    """perf_inbox/*/posts.csv 중 N데스크피커 글(채널에 '데스크피커' 또는 앱 글 키가 데스크피커 대기열 id)만."""
    rows = []
    q = jload(BASE / "content_queue_radar_naver.json", []) or []
    ids = {x.get("id") for x in q if (x.get("plan") or {}).get("run_id")}
    for f in sorted(glob.glob(str(BASE / "perf_inbox" / "*" / "posts.csv"))):
        wk = Path(f).parent.name
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if "데스크피커" in (r.get("채널") or "") or (r.get("앱 글 키") in ids):
                    r["_week"] = wk
                    rows.append(r)
    return rows


def extra_terms(seed_norms):
    """최근 perf_inbox 의 검색어 중 N데스크피커 글로 들어온 것 가운데 씨앗에 없는 낱말. N데스크피커 글이 없으면 건너뜀."""
    posts = perf_posts()
    if not posts:
        return [], "건너뜀 — perf_inbox 에 N데스크피커 글(채널 '데스크피커')이 한 편도 없음(search_terms.csv 에는 블로그 구분 칸이 없어 다른 블로그 유입과 가를 수 없음)"
    weeks = sorted({p["_week"] for p in posts})[-4:]
    seen, out = set(), []
    for wk in weeks:
        f = BASE / "perf_inbox" / wk / "search_terms.csv"
        if not f.exists():
            continue
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("구분") != "검색어":
                    continue
                t = (r.get("검색어") or "").strip()
                n = nk(t)
                if not t or n in seen or any(n == s or s in n for s in seed_norms):
                    continue
                seen.add(n)
                out.append(t)
    return out, f"{len(out)}개 더함(최근 {len(weeks)}주)"


def stage_seeds():
    path = Path(A.seeds)
    words, srcs = {}, []
    for label, p, col in SEED_SRC:
        fp = BASE / p
        if not fp.exists():
            srcs.append({"label": label, "path": p, "found": False, "words": 0})
            continue
        n = 0
        for v in read_csv_col(fp, col):
            for w in re.split(r"[/,]", v):
                w = w.strip()
                if w and nk(w) not in words:
                    words[nk(w)] = w
                    n += 1
        srcs.append({"label": label, "path": p, "found": True, "words": n})
    if not words:
        raise SystemExit("씨앗 출처 파일(상품 수집 1·2차 결과)을 찾지 못했습니다.")
    by_theme, undecided = {}, []
    for w in sorted(words.values()):
        th = theme_of(w)
        (undecided if th == UNDECIDED else by_theme.setdefault(th, [])).append(w)
    if not path.exists() or A.rebuild_seeds:
        lines = ["# N데스크피커 씨앗 — tools/keyword_pipeline.py 가 쇼핑커넥트 상품 수집 1·2차 결과에서 뽑아 갈래별로 묶음 (씨앗 | 연관 | 갈래)",
                 "# 출처: " + " · ".join(f"{s['path']}" for s in srcs if s["found"]),
                 "# 갈래 이름·기준: tools/radar_rules.md [블로그] 주제 + tools/filter.json radar_kw.theme_words"]
        for th in RK["theme_order"][::-1]:
            for w in by_theme.get(th, []):
                lines.append(f"{w} | {w} | {th}")
        if undecided:
            lines.append("# ---- 갈래 미정(후보에서 뺌) ----")
            lines += [f"# {w} | {w} | {UNDECIDED}" for w in undecided]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    seeds = wp.parse_seeds(str(path))
    seeds = [s for s in seeds if s["theme"] != UNDECIDED]
    extra, extra_note = extra_terms({nk(s["word"]) for s in seeds})
    for t in extra:
        th = theme_of(t)
        if th != UNDECIDED:
            seeds.append({"word": t, "tokens": [t], "theme": th, "extra": True})
    info = {"seeds_file": rel(path), "sources": srcs, "per_theme": {k: len(v) for k, v in by_theme.items()}, "undecided": undecided,
            "extra_terms": extra_note, "seed_count": len(seeds)}
    jsave(D / "seeds_info.json", info)
    print(f"[seeds] 씨앗 {len(seeds)}개 (갈래별 { {k: len(v) for k, v in by_theme.items()} }, 갈래 미정 {len(undecided)}개) / 유입 검색어: {extra_note}")
    return seeds, info


# ---------------- 2) 조회 (weekly_plan.stage_explore 와 같은 저장·한도 방식) ----------------
def stage_lookup(seeds):
    raw = rs.raw_cache()
    raw.setdefault("blog_total", {})
    api = bt.Api()
    for sd in seeds:
        ns = nk(sd["word"])
        if ns in raw["expand"]:
            continue
        if api.usage["searchad"] >= A.searchad_limit:
            stop("lookup", f"검색광고 호출 한도({A.searchad_limit}회)에 닿음")
        lst = api.searchad([sd["word"]])
        if lst is None:
            log(f"  [실패] 검색광고 {sd['word']}")
            continue
        raw["expand"][ns] = [{"kw": it.get("relKeyword"), "pc": api.cm.to_int(it.get("monthlyPcQcCnt")), "mo": api.cm.to_int(it.get("monthlyMobileQcCnt"))} for it in lst]
        api.save()
        rs.save_raw(raw)
    # 후보 만들기: 씨앗 낱말이 든 연관 키워드, 씨앗별 검색량 상위 per_seed_pool 개
    vmin_any, vmax = min(RK["volume_min"], RK["volume_min_B"]), RK["volume_max"]
    excl = [nk(w) for w in RK["exclude_words"]] + [nk(w) for w in rs.DROP_WORDS + rs.BUY_WORDS]
    owner, drops = {}, {"씨앗 낱말 없음": 0, "제외 낱말": 0, "검색량 범위 밖(조회 전)": 0, "씨앗별 상위 한도 밖": 0}
    seed_by_len = sorted(seeds, key=lambda s: -len(nk(s["word"])))
    per_seed = {}
    for sd in seeds:
        ns = nk(sd["word"])
        for x in raw["expand"].get(ns, []):
            if not x.get("kw"):
                continue
            k, vol = nk(x["kw"]), x["pc"] + x["mo"]
            if ns not in k:
                drops["씨앗 낱말 없음"] += 1
                continue
            if any(w in k for w in excl):
                drops["제외 낱말"] += 1
                continue
            if vol < vmin_any or vol > vmax:
                drops["검색량 범위 밖(조회 전)"] += 1
                continue
            best = next(s for s in seed_by_len if nk(s["word"]) in k)       # 가장 긴 씨앗 낱말이 주인
            if nk(best["word"]) != ns:
                continue
            if k not in owner:
                owner[k] = {"keyword": x["kw"], "nk": k, "volume": vol, "seed": sd["word"], "theme": sd["theme"]}
                per_seed.setdefault(ns, []).append(k)
    cands = {}
    for ns, ks in per_seed.items():
        ks.sort(key=lambda k: -owner[k]["volume"])
        for k in ks[:RK["per_seed_pool"]]:
            cands[k] = owner[k]
        drops["씨앗별 상위 한도 밖"] += max(0, len(ks) - RK["per_seed_pool"])
    # 모델명 후보(틀 B): 상품 수집 2차 결과에서 이미 잰 모델명의 월 검색량·문서수를 재사용한다(API 0회). 모델명 모양인 것만.
    mfile = BASE / RK.get("model_source", "pipeline/stage_desk_pick2_run/candidates2.csv")
    mdrop = {"모델명 모양 아님": 0, "검색량·문서수 없음": 0, "이미 있는 키워드": 0}
    nmodel = 0
    use_model = bool(RK.get("use_model_source", True))
    if not use_model:
        log("  모델명 출처 꺼짐(filter.json radar_kw.use_model_source=false)")
    elif not mfile.exists():
        log(f"  [주의] 모델명 출처 파일 없음: {mfile}")
    if use_model and mfile.exists():
        with open(mfile, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("모델명 종류") != "모델명":
                    continue
                kw = " ".join((r.get("모델명") or "").split())
                try:
                    vol, docs = int(float(r.get("검색량") or "")), int(float(r.get("문서 수") or ""))
                except ValueError:
                    mdrop["검색량·문서수 없음"] += 1
                    continue
                if not model_tokens(kw):
                    mdrop["모델명 모양 아님"] += 1
                    continue
                k = nk(kw)
                if k in cands:
                    mdrop["이미 있는 키워드"] += 1
                    continue
                sd = r.get("씨앗") or kw
                cands[k] = {"keyword": kw, "nk": k, "volume": vol, "seed": sd, "theme": theme_of(sd), "docs": docs, "src": MODEL_SRC}
                nmodel += 1
    cands = {k: c for k, c in cands.items() if c["theme"] != UNDECIDED}
    drops["모델명 후보(2차 결과 재사용)"] = {"켜짐": use_model, "더함": nmodel, **mdrop}
    # 블로그 문서 수(블로그 검색 총 문서수) — 저장된 값은 다시 받지 않는다
    todo = [c for k, c in cands.items() if c.get("docs") is None and k not in raw["blog_total"]]
    for i, c in enumerate(todo):
        if api.usage["blog"] >= A.blog_limit:
            api.save()
            rs.save_raw(raw)
            stop("lookup", f"블로그 검색 호출 한도({A.blog_limit}회)에 닿음")
        try:
            j = api.blog(query=c["keyword"], display=1, start=1, sort="sim")
        except bt.Budget:
            api.save()
            rs.save_raw(raw)
            stop("lookup", "블로그 검색 한도")
        if j is None:
            log(f"  [실패] 블로그 검색 {c['keyword']}")
            continue
        raw["blog_total"][c["nk"]] = int(j.get("total") or 0)
        if (i + 1) % 25 == 0:
            api.save()
            rs.save_raw(raw)
    api.save()
    rs.save_raw(raw)
    for k, c in cands.items():
        if c.get("docs") is None:
            c["docs"] = raw["blog_total"].get(k)
            c["src"] = RELATED_SRC
    jsave(D / "cands_raw.json", {"cands": cands, "drops": drops})
    print(f"[lookup] 후보 {len(cands)}개 (조회 전 탈락 {drops}) / 사용량 {api.usage}")
    return cands, drops


# ---------------- 3) 거르기·점수 ----------------
def ledger_titles():
    """기존 장부 제목: articles_radar_naver(플랫폼 제목) + content_queue_radar_naver(확정 제목 또는 키워드). 읽기만."""
    out = []
    a = jload(BASE / "articles_radar_naver.json", {}) or {}
    for key, v in a.items():
        t = ((v.get("platform_title") or {}).get("naver") if isinstance(v.get("platform_title"), dict) else v.get("platform_title")) or ""
        if t:
            out.append({"title": " ".join(str(t).split()), "src": "articles_radar_naver"})
    for q in jload(BASE / "content_queue_radar_naver.json", []) or []:
        if (q.get("plan") or {}).get("run_id") == RUN_ID:
            continue
        t = (q.get("plan") or {}).get("final_title") or q.get("keyword")
        if t:
            out.append({"title": " ".join(str(t).split()), "src": "content_queue_radar_naver"})
    return out


def perf_weights():
    """장부 글별 조회수(perf_inbox 연결 결과)로 갈래별 가중치. 표본 perf_min_samples 편 미만이면 1.0(꺼짐). 계산표를 돌려준다."""
    q = jload(BASE / "content_queue_radar_naver.json", []) or []
    theme_by_id = {x.get("id"): (x.get("plan") or {}).get("갈래") for x in q if (x.get("plan") or {}).get("갈래")}
    views = {}
    for r in perf_posts():
        key = r.get("앱 글 키")
        th = theme_by_id.get(key)
        if not th:
            continue
        try:
            v = float(r.get("조회수") or 0)
        except ValueError:
            continue
        views.setdefault(key, []).append(v)           # 글 한 편의 여러 주 관측은 평균 하나로
    per_theme = {}
    for key, vs in views.items():
        per_theme.setdefault(theme_by_id[key], []).append(sum(vs) / len(vs))
    allv = [x for vs in per_theme.values() for x in vs]
    overall = (sum(allv) / len(allv)) if allv else None
    lo, hi = RK["perf_clamp"]
    table = {}
    for th in RK["theme_order"]:
        vs = per_theme.get(th, [])
        n = len(vs)
        avg = (sum(vs) / n) if n else None
        on = n >= RK["perf_min_samples"] and overall
        w = 1.0
        if on:
            w = max(lo, min(hi, avg / overall)) if overall > 0 else 1.0
        table[th] = {"표본(편)": n, "평균 조회수": round(avg, 2) if avg is not None else None, "전체 평균": round(overall, 2) if overall else None,
                     "켜짐": bool(on), "가중치": round(w, 3), "기본 가중치": RK["theme_weight"].get(th, 1.0)}
    note = "클릭 수는 글별 값이 없어(클릭은 계정 전체 일별 합계뿐) 조회수만 씀"
    return table, note


def stage_score(cands, drops):
    perf, perf_note = perf_weights()
    th_w = {th: RK["theme_weight"].get(th, 1.0) * perf[th]["가중치"] for th in RK["theme_order"]}
    led = ledger_titles()
    rows = []
    for c in cands.values():
        r = dict(c)
        r["models"] = model_tokens(c["keyword"])
        r["type"] = "B" if r["models"] else "A"
        r["buy_words"] = [w for w in RK["buy_words"] if nk(w) in c["nk"]]
        vmin = RK["volume_min_B"] if r["type"] == "B" else RK["volume_min"]
        r["ratio"] = None if c["docs"] is None else round(c["docs"] / max(c["volume"], 1), 3)
        r["reasons"] = []
        if c["docs"] is None:
            r["reasons"].append("문서 수 조회 안 됨")
        if c["volume"] < vmin:
            r["reasons"].append(f"검색량 {vmin} 미만")
        if c["volume"] > RK["volume_max"]:
            r["reasons"].append(f"검색량 {RK['volume_max']} 초과")
        if r["ratio"] is not None and r["ratio"] > RK["ratio_max"]:
            r["reasons"].append(f"문서수/검색량 {r['ratio']} > {RK['ratio_max']}")
        ratio_v = RK["ratio_max"] if r["ratio"] is None else r["ratio"]
        comp = max(RK["ratio_floor_factor"], 1 - ratio_v / RK["ratio_max"])
        intent = RK["model_boost"] if r["models"] else (RK["intent_boost"] if r["buy_words"] else RK["intent_none"])
        r["factors"] = {"경쟁": round(comp, 3), "사려는 뜻": intent, "갈래": round(th_w[c["theme"]], 3)}
        r["score"] = round(c["volume"] * comp * intent * th_w[c["theme"]], 1)
        hit = None
        for L in led:
            o = overlaps(c["keyword"], L["title"], RK["similarity"])
            if o and (not hit or o > hit[0]):
                hit = (o, L)
        if hit:
            r["reasons"].append(f"기존 장부 겹침({hit[1]['src']}: {hit[1]['title'][:30]} / {hit[0]:.2f})")
        r["status"] = "탈락" if r["reasons"] else "후보"
        rows.append(r)
    # 같은 주 후보끼리 겹침(점수 높은 쪽을 남김)
    kept = []
    for r in sorted(rows, key=lambda x: (-x["score"], x["nk"])):
        if r["status"] != "후보":
            continue
        hit = next((k for k in kept if overlaps(r["keyword"], k["keyword"], RK["similarity"]) or overlaps(k["keyword"], r["keyword"], RK["similarity"])), None)
        if hit:
            r["overlap_with"] = hit["nk"]       # 점수가 더 높은 짝이 승인되면 탈락, 보류되면 이 후보가 대신 나감(승인 관문에서 결정)
        else:
            kept.append(r)
    rows.sort(key=lambda x: (-x["score"], x["nk"]))
    print(f"[score] 후보 {sum(1 for r in rows if r['status'] == '후보')}개 / 탈락 {sum(1 for r in rows if r['status'] == '탈락')}개 (조회한 {len(rows)}개 중)")
    return rows, perf, perf_note, led


# ---------------- 4) 제목 ----------------
def rules_section(name):
    txt = RULES_MD.read_text(encoding="utf-8")
    m = re.search(r"^\[" + re.escape(name) + r"[^\]]*\]\s*\n(.*?)(?=^\[|\Z)", txt, re.S | re.M)
    return m.group(0).strip() if m else ""


def rules_common_bans():
    ls = [ln for ln in rules_section("공통").splitlines() if ("금지" in ln or "써본" in ln)]
    return "\n".join(ls)


TITLE_PROMPT = """너는 네이버 블로그 'Desk Picker'(데스크피커)의 제목 담당이다. 도구는 쓰지 말고 바로 답해라.
아래 키워드마다 블로그 글 제목 3안과 search_query 를 만들어라.

{rules_title}

[지킬 것]
{bans}
- 제목은 40자 이내. 키워드(띄어쓰기는 자연스럽게 해도 됨)가 제목 맨 앞 15자 안에 들어가야 한다.
- 틀 A(고르는 법): 키워드 + 고르는 법/기준 + 상황. 틀 B(모델명): 브랜드 모델명 + 제품 종류 + 사람들이 궁금해하는 것.
- 써본 척 표현·효과 단정·모르는 숫자는 쓰지 않는다. 한국어로만 쓴다.
- search_query: 제품 후보를 찾을 때 쓸 쇼핑 검색어. 틀 A는 핵심 물건 2~3단어(띄어쓰기로 구분, 예: "발난로 사무실"), 틀 B는 "브랜드 모델명"(예: "브라운 BC02XW"). 반드시 키워드에 들어 있는 낱말만 써라.
반드시 아래 JSON 한 덩어리로만 답해라(설명 금지).
{{"items": [{{"id": "K1", "titles": ["제목1", "제목2", "제목3"], "search_query": "발난로 사무실"}}]}}

[키워드 목록] (id | 키워드 | 틀 | 상품 종류 | 갈래)
{lines}
"""


def claude_titles(batch):
    """batch: 후보 목록. 반환 {nk: {"titles": [...], "search_query": "..."}}, 실패하면 None. 호출 수 상한을 지킨다."""
    lines = "\n".join(f"K{i + 1} | {c['keyword']} | 틀 {c['type']} | {c['seed']} | {c['theme']}" for i, c in enumerate(batch))
    prompt = TITLE_PROMPT.format(rules_title=rules_section("제목"), bans=rules_common_bans(), lines=lines)
    for attempt in range(2):
        if claude_count() >= min(RK["claude_max_calls"], A.claude_limit):
            stop("titles", f"claude 호출 상한({min(RK['claude_max_calls'], A.claude_limit)}회)에 닿음")
        try:
            env = None
            try:
                env = pp.call_claude(prompt, RK["claude_model"], "1")
            except pp.UsageLimitError:
                claude_used(None, False)
                raise
            except RuntimeError:
                claude_used(None, False)
                raise
            claude_used(env)
            res = pp.extract_json(env.get("result", ""))["items"]
            by = {x["id"]: x for x in res}
            return {c["nk"]: {"titles": [str(t).strip() for t in (by.get(f"K{i + 1}") or {}).get("titles", []) if str(t).strip()],
                              "search_query": " ".join(str((by.get(f"K{i + 1}") or {}).get("search_query") or "").split())}
                    for i, c in enumerate(batch)}
        except pp.UsageLimitError as e:
            stop("titles", f"claude 사용 한도: {str(e)[:100]}")
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            log(f"  claude 실패({type(e).__name__}): {str(e).splitlines()[0][:150]}")
    return None


def template_titles(c):
    """--no-claude: 규칙 템플릿 제목과 규칙 템플릿 search_query."""
    kw = c["keyword"]
    if c["type"] == "B":
        titles = [f"{kw} 크기·가격·사양 한눈에 정리", f"{kw}, 살 때 볼 정보 정리", f"{kw} 구매 전 확인할 점 정리"]
    else:
        titles = [f"{kw} 고르는 법, 사기 전 볼 기준", f"{kw} 고를 때 볼 기준 정리", f"{kw} 비교 기준과 상황별 선택"]
    return {"titles": titles, "search_query": template_query(c)}


def template_query(c):
    k, seed = c["nk"], nk(c["seed"])
    if c["type"] == "B":
        m = c["models"][0]
        brand = re.split(re.escape(m), c["keyword"], flags=re.I)[0].strip()
        return f"{brand} {m}".strip() if brand else ""
    rest = k.replace(seed, "", 1)
    for w in sorted(RK["buy_words"], key=len, reverse=True):
        rest = rest.replace(nk(w), "")
    return f"{c['seed']} {rest}".strip() if len(rest) >= 2 else ""


def title_issues(title, kw):
    t = " ".join(title.split())
    bad = []
    if len(t) > RK["title_max"]:
        bad.append(f"{RK['title_max']}자 초과({len(t)}자)")
    for w in RK["title_forbidden"]:
        if nk(w) in nk(t):
            bad.append(f"금지 표현 '{w}'")
    for w in RK["title_experience"]:
        if nk(w) in nk(t):
            bad.append(f"써본 척 표현 '{w}'")
    if re.search(r"\d+\s*일\s*(간\s*)?사용", t):
        bad.append("써본 척 표현 '○일 사용'")
    if nk(kw) not in nk(t[:RK["title_front"]]):
        bad.append(f"키워드가 앞 {RK['title_front']}자 안에 없음")
    return bad


def title_points(t, kw):
    b, s = RK["title_bonus"], 0
    if nk(t).startswith(nk(kw)):
        s += b["keyword_first"]
    if b["len_lo"] <= len(t) <= b["len_hi"]:
        s += b["len_ok"]
    if any(w in t for w in RK.get("title_pick_words", ["고르는 법", "고르는법", "기준", "비교", "정리", "차이", "볼 것"])):
        s += b["pick_word"]
    if any(w in t for w in RK.get("title_info_words", ["때", "별", "용", "위한", "크기", "가격", "소리", "사양", "종류", "상황"])):
        s += b["situation_or_info"]
    return s


def query_issue(c, q):
    toks = q.split()
    if not q:
        return "search_query 없음"
    if not 2 <= len(toks) <= 3:
        return f"search_query 가 2~3단어가 아님('{q}')"
    for t in toks:
        n = nk(t)
        if n not in c["nk"] and n not in nk(c["seed"]):
            return f"search_query 낱말 '{t}' 가 키워드에 없음"
    if c["type"] == "B" and not any(re.search(r"[A-Za-z]", t) and re.search(r"\d", t) for t in toks):
        return "틀 B search_query 에 모델명이 없음"
    return None


def choose_title(c, gen):
    """통과안 중 최고점 1개. 반환 (title|None, 검사 기록, search_query, 보류 사유|None)"""
    checks = []
    for t in gen.get("titles", []):
        bad = title_issues(t, c["keyword"])
        checks.append({"title": t, "ok": not bad, "issues": bad, "points": None if bad else title_points(t, c["keyword"])})
    ok = [x for x in checks if x["ok"]]
    if not checks:
        return None, checks, gen.get("search_query", ""), "제목 생성 안 됨(claude 응답 없음)"
    if not ok:
        return None, checks, gen.get("search_query", ""), "제목 3안 모두 기계 검사 탈락: " + "; ".join(sorted({i for x in checks for i in x["issues"]}))
    best = max(ok, key=lambda x: x["points"])
    return best["title"], checks, gen.get("search_query", ""), None


# ---------------- 5) 승인 관문 + 배정 ----------------
def theme_min_of(th):
    return int((RK.get("theme_min_by") or {}).get(th, RK.get("theme_min", 2)))


def stage_titles_gate(rows, led):
    """라운드: 점수 높은 후보를 (갈래·씨앗·A:B 한도를 지키며) 골라 제목을 받고, 관문을 통과하면 승인. 모자라면 다음 라운드로 예비를 채운다."""
    cache = jload(D / "titles_cache.json", {}) or {}
    need = dict(RK["ab_week"])
    res_ab = {"A": math.ceil(RK["reserve"] * RK["ab_week"]["A"] / RK["per_week"]), "B": 0}
    res_ab["B"] = RK["reserve"] - res_ab["A"]
    approved, reserve_ok, held = [], [], []
    th_n, seed_n, ab_n = {}, {}, {"A": 0, "B": 0}
    cand = [r for r in rows if r["status"] == "후보"]
    by_nk = {r["nk"]: r for r in cand}
    done = set()
    rounds = 0
    normal_rounds = 0

    def make_pool(min_phase, want):
        """min_phase=True: 갈래 최소(theme_min)에 모자란 갈래의 후보만 점수 순으로. False: 전체 점수 순(갈래 상한 유지)."""
        pool, pth, psd, pcnt = [], dict(th_n), dict(seed_n), {"A": 0, "B": 0}
        for r in cand:
            if r["nk"] in done or ab_n[r["type"]] >= need[r["type"]]:
                continue
            if not min_phase and pcnt[r["type"]] >= want[r["type"]]:
                continue
            pn = r.get("overlap_with")
            if pn:                                   # 겹치는 짝이 아직 결과가 안 났거나 승인됐으면 이번엔 건너뜀
                if pn not in done or by_nk[pn]["status"] == "승인":
                    continue
            if pth.get(r["theme"], 0) >= RK["theme_cap"] or psd.get(r["seed"], 0) >= RK["per_seed_week_max"]:
                continue
            if min_phase and pth.get(r["theme"], 0) >= theme_min_of(r["theme"]):
                continue
            pool.append(r)
            pth[r["theme"]] = pth.get(r["theme"], 0) + 1
            psd[r["seed"]] = psd.get(r["seed"], 0) + 1
            pcnt[r["type"]] += 1
        return pool

    while (ab_n["A"] < need["A"] or ab_n["B"] < need["B"]) and rounds < 14:
        rounds += 1
        pool = make_pool(True, None)                 # 1단계: 갈래마다 최소 편수부터(보류로 빠지면 다음 라운드에서 다시)
        if not pool:
            normal_rounds += 1
            want = {t: (need[t] - ab_n[t]) + (res_ab[t] if normal_rounds == 1 else 2) for t in "AB"}
            pool = make_pool(False, want)            # 2단계: 나머지는 전체 점수 순
        if not pool:
            break
        for i in range(0, len(pool), RK["title_batch"]):
            chunk = [r for r in pool[i:i + RK["title_batch"]] if r["nk"] not in cache]
            if not chunk:
                continue
            if A.no_claude:
                for r in chunk:
                    cache[r["nk"]] = {**template_titles(r), "source": "template"}
            else:
                got = claude_titles(chunk)
                for r in chunk:
                    if got and got[r["nk"]]["titles"]:
                        cache[r["nk"]] = {**got[r["nk"]], "source": "claude"}
            jsave(D / "titles_cache.json", cache)
        for r in pool:                      # 점수 순으로 관문
            done.add(r["nk"])
            g = cache.get(r["nk"])
            if g is None:
                r["title"], r["title_checks"], r["search_query"], r["title_source"] = None, [], "", "claude 실패"
                r["status"], r["hold_reason"] = "보류", "제목 생성 실패(claude 재시도 1회 후)"
                held.append(r)
                continue
            title, checks, q, why = choose_title(r, g)
            r["title_checks"], r["search_query"], r["title_source"] = checks, q, g.get("source")
            if not why:
                why = query_issue(r, q)
            if not why:
                hit = next((a for a in approved if sim_ratio(title, a["title"]) >= RK["similarity"]), None)
                if hit:
                    why = f"같은 주 제목 겹침({hit['title'][:24]})"
            if not why:
                if th_n.get(r["theme"], 0) >= RK["theme_cap"]:
                    why = f"갈래 주간 상한({RK['theme_cap']}편)"
                elif ab_n[r["type"]] >= need[r["type"]]:
                    why = f"A:B 비율(틀 {r['type']} {need[r['type']]}편 가득)"
                elif seed_n.get(r["seed"], 0) >= RK["per_seed_week_max"]:
                    why = f"같은 상품 종류 주 {RK['per_seed_week_max']}편 한도"
            r["title"] = title
            if why and why.startswith(("A:B 비율", "갈래 주간 상한", "같은 상품 종류")):
                r["status"], r["reserve_note"] = "예비", why          # 검사는 통과했지만 자리가 가득 참 → 예비
            elif why:
                r["status"], r["hold_reason"] = "보류", why
                held.append(r)
            elif ab_n[r["type"]] < need[r["type"]]:
                r["status"] = "승인"
                approved.append(r)
                th_n[r["theme"]] = th_n.get(r["theme"], 0) + 1
                seed_n[r["seed"]] = seed_n.get(r["seed"], 0) + 1
                ab_n[r["type"]] += 1
    for r in cand:
        if r["status"] == "후보" and r["nk"] in done:
            r["status"] = "예비"
        pn = r.get("overlap_with")
        if r["status"] in ("후보", "예비") and pn and by_nk[pn]["status"] == "승인":
            r["status"] = "탈락"
            r["reasons"].append(f"같은 주 후보 겹침({by_nk[pn]['keyword']} 승인)")
    short = {t: need[t] - ab_n[t] for t in "AB"}
    return approved, held, short, cache


def assign_dates(approved):
    """승인 항목을 7일에 하루 A2+B1 로 배정: 같은 날 같은 갈래 금지, 이틀 연속 같은 갈래는 가능한 한 피함(고정 시드 무작위 개선)."""
    slots = [(d, k) for d in range(7) for k in ("A", "A", "B")]
    A_pos = [i for i, s in enumerate(slots) if s[1] == "A"]
    B_pos = [i for i, s in enumerate(slots) if s[1] == "B"]
    As = [r for r in approved if r["type"] == "A"]
    Bs = [r for r in approved if r["type"] == "B"]
    rnd = random.Random(20261123)

    def cost(arr):
        c = 0
        by_day = {}
        for i, r in enumerate(arr):
            if r:
                by_day.setdefault(slots[i][0], []).append(r["theme"])
        for d, ths in by_day.items():
            c += 1000 * (len(ths) - len(set(ths)))
            if d + 1 in by_day:
                c += 10 * len(set(ths) & set(by_day[d + 1]))
        return c

    def initial(items, pos):
        pad = items + [None] * (len(pos) - len(items))
        rnd.shuffle(pad)
        return pad
    best, best_c = None, None
    for _ in range(3000):
        arr = [None] * len(slots)
        for i, r in zip(A_pos, initial(As, A_pos)):
            arr[i] = r
        for i, r in zip(B_pos, initial(Bs, B_pos)):
            arr[i] = r
        c = cost(arr)
        for _it in range(300):
            if rnd.random() < 0.15:                   # 하루 통째로 맞바꾸기(갈래 겹침 없이 요일 순서만 바꿈)
                d1, d2 = rnd.sample(range(7), 2)
                sw = [(3 * d1 + k, 3 * d2 + k) for k in range(3)]
            else:
                grp = A_pos if rnd.random() < 0.7 else B_pos
                sw = [tuple(rnd.sample(grp, 2))]
            for i, j in sw:
                arr[i], arr[j] = arr[j], arr[i]
            c2 = cost(arr)
            if c2 <= c:
                c = c2
            else:
                for i, j in sw:
                    arr[i], arr[j] = arr[j], arr[i]
        if best_c is None or c < best_c:
            best, best_c = list(arr), c
        if best_c == 0:
            break
    dups = []
    for d in range(7):
        ths = [best[i]["theme"] for i, s_ in enumerate(slots) if s_[0] == d and best[i]]
        for th in set(ths):
            if ths.count(th) > 1:
                dups.append(f"{(WEEK + timedelta(days=d)).isoformat()} {th}")
    if dups:                                           # 같은 날 같은 갈래 금지: 지키지 못하면 멈춤
        stop("dates", "같은 날 같은 갈래를 피하는 배정을 찾지 못함: " + ", ".join(dups))
    out, no = [], 0
    for i, r in enumerate(best):
        if r:
            no += 1
            d = WEEK + timedelta(days=slots[i][0])
            r["date"], r["weekday"], r["no"] = d.isoformat(), WEEKDAYS[d.weekday()], no
            out.append(r)
    out.sort(key=lambda r: (r["date"], 0 if r["type"] == "A" else 1, r["no"]))
    for n, r in enumerate(out, 1):
        r["no"] = n
    return out, best_c


# ---------------- 6) 결과 ----------------
def build_outputs(rows, approved, held, short, perf, perf_note, seeds_info, drops, bundle):
    sched, plan_cost = assign_dates(approved)
    shortage = short["A"] + short["B"]
    th_cnt = {th: sum(1 for r in sched if r["theme"] == th) for th in RK["theme_order"]}
    th_short = {th: theme_min_of(th) - th_cnt[th] for th in RK["theme_order"] if th_cnt[th] < theme_min_of(th)}
    plans = []
    for r in sched:
        plans.append({
            "platform": "naver", "keyword": r["keyword"], "role": "scheduled", "publish_date": r["date"], "weekday": r["weekday"],
            "final_title": r["title"], "alt_titles": [x["title"] for x in r.get("title_checks", []) if x["ok"] and x["title"] != r["title"]],
            "track": "info", "seed": r["seed"], "monthly_total": r["volume"],
            "product_need": {"need": True, "count": 3 if r["type"] == "A" else 1, "condition": r["search_query"], "link_kind": "네이버 쇼핑커넥트"},
            "radar": {"date": r["date"], "title": r["title"], "type": r["type"], "search_query": r["search_query"], "갈래": r["theme"],
                      "auto_approved": True, "run_id": RUN_ID, "blog_docs": r["docs"], "score": r["score"]},
        })
    plan = {"week": {"start": WEEK.isoformat(), "end": (WEEK + timedelta(days=6)).isoformat()}, "channel": A.channel, "run_id": RUN_ID,
            "made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "shortage": short, "plans": plans}
    jsave(D / "plan.json", plan)
    jsave(D / "keywords.json", {"run_id": RUN_ID, "week": WEEK.isoformat(), "channel": A.channel, "filter": RK, "조회 전 탈락": drops,
                                 "perf_weights": perf, "씨앗": seeds_info, "keywords": [{k: v for k, v in r.items()} for r in rows]})
    usage = usage_get()
    L = []
    if th_short:
        L.append("# ⚠ " + " · ".join(f"갈래 부족: {th} {n}편" for th, n in th_short.items()))
        L.append(f"(갈래마다 최소 {RK.get('theme_min', 2)}편 기준 — 위 갈래는 그만큼 못 채움. 숫자는 모자란 편수)")
        L.append("")
    if shortage:
        miss = ([f"틀 A {short['A']}편"] if short["A"] else []) + ([f"틀 B {short['B']}편"] if short["B"] else [])
        open_th = [f"{th}({th_cnt[th]}/{RK['theme_cap']})" for th in RK["theme_order"] if th_cnt[th] < RK["theme_cap"]]
        L.append(f"# ⚠ 부족 {shortage}편 — {' · '.join(miss)}")
        L.append(f"모자란 편수: {shortage}편 / 아직 자리가 남은 갈래(승인 편수/상한): {', '.join(open_th)}. 승인 가능한 후보와 예비가 바닥남(보류 사유는 아래 목록).")
        L.append("")
    L.append(f"# N데스크피커 주간 키워드 — {WEEK} 주 (run_id {RUN_ID})")
    L.append(f"승인 {len(sched)}편 / 목표 {RK['per_week']}편 · 보류 {len(held)}편 · 예비(승인 안 됨) {sum(1 for r in rows if r['status'] == '예비')}편")
    L.append("")
    L.append("## 날짜별 승인 표")
    L.append("| 날짜 | 번호 | 갈래 | 유형 | 키워드 | 월 검색량 | 문서수 | 제목 | search_query |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in sched:
        L.append(f"| {r['date']}({r['weekday']}) | {r['no']} | {r['theme']} | {r['type']} | {r['keyword']} | {r['volume']:,} | {r['docs']:,} | {r['title']} | {r['search_query']} |")
    L.append("")
    L.append("## 보류 목록과 사유")
    if held:
        L.append("| 키워드 | 갈래 | 유형 | 보류 사유 |")
        L.append("|---|---|---|---|")
        for r in held:
            L.append(f"| {r['keyword']} | {r['theme']} | {r['type']} | {r['hold_reason']} |")
    else:
        L.append("없음")
    L.append("")
    L.append("## 갈래별 편수")
    L.append("| 갈래 | 승인 편수 | 최소 | 상한 |")
    L.append("|---|---|---|---|")
    for th in RK["theme_order"]:
        L.append(f"| {th} | {th_cnt[th]} | {theme_min_of(th)} | {RK['theme_cap']} |")
    L.append("")
    L.append("## 후보 출처별 수")
    L.append(f"모델명 출처(use_model_source): {'켬' if RK.get('use_model_source', True) else '끔'} · 파일 {RK.get('model_source')}")
    L.append("| 출처 | 조회·점수 낸 후보 | 검사 통과(후보) | 승인 |")
    L.append("|---|---|---|---|")
    appr_keys = {r["nk"] for r in sched}
    for src in (RELATED_SRC, MODEL_SRC):
        rs_ = [r for r in rows if r.get("src") == src]
        L.append(f"| {src} | {len(rs_)} | {sum(1 for r in rs_ if not r['reasons'])} | {sum(1 for r in rs_ if r['nk'] in appr_keys)} |")
    L.append("")
    L.append("## 갈래 가중치 (실적 반영 계산표)")
    L.append(f"표본 {RK['perf_min_samples']}편 미만이면 1.0(꺼짐). {perf_note}.")
    L.append("| 갈래 | 표본(편) | 평균 조회수 | 켜짐 | 가중치 |")
    L.append("|---|---|---|---|---|")
    for th in RK["theme_order"]:
        p = perf[th]
        L.append(f"| {th} | {p['표본(편)']} | {p['평균 조회수'] if p['평균 조회수'] is not None else '-'} | {'예' if p['켜짐'] else '아니오(1.0)'} | {p['가중치']} |")
    L.append("")
    L.append(f"사용량: 검색광고 {usage.get('searchad', 0)}회 · 블로그 검색 {usage.get('blog', 0)}회 · claude {usage.get('claude', 0)}회 (이어 받기 누적)")
    (D / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return sched, th_cnt, plan_cost


def run_push(apply):
    cmd = [sys.executable, "-X", "utf8", str(TOOLS / "push_plan.py"), "--channel", A.channel, "--plan", rel(D / "plan.json")]
    if apply:
        cmd.append("--apply")
    print("\n[push_plan] " + ("실제로 넣음(--apply)" if apply else "dry-run(아무것도 쓰지 않음)"))
    subprocess.run(cmd, cwd=str(BASE))


def main():
    global A, D, WEEK, RK, RUN_ID
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", default="radar_naver")
    ap.add_argument("--week", required=True, help="그 주 월요일 날짜 (예: 2026-11-23)")
    ap.add_argument("--seeds", default=str(BASE / "pipeline" / "radar" / "seeds.txt"))
    ap.add_argument("--rebuild-seeds", action="store_true", help="씨앗 파일을 출처 결과 파일에서 다시 만듦")
    ap.add_argument("--out", help="결과 폴더(기본 pipeline/keyword_run/<주시작일>_<채널>)")
    ap.add_argument("--blog-limit", type=int, default=2000)
    ap.add_argument("--searchad-limit", type=int, default=200)
    ap.add_argument("--claude-limit", type=int, default=35, help="claude 호출 상한(filter.json 의 claude_max_calls 보다 크게는 못 씀)")
    ap.add_argument("--no-claude", action="store_true")
    ap.add_argument("--until", choices=STAGES, help="그 단계까지만")
    ap.add_argument("--apply", action="store_true", help="결과를 대기열에 넣음(push_plan --apply). 기본은 dry-run 보여 주기만")
    A = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if A.channel != "radar_naver":
        raise SystemExit("지금은 radar_naver(N데스크피커)만 지원합니다.")
    WEEK = date.fromisoformat(A.week)
    if WEEK.weekday() != 0:
        raise SystemExit("--week 는 월요일 날짜여야 합니다.")
    RK = load_rk()
    A.claude_limit = min(A.claude_limit, RK["claude_max_calls"])
    D = Path(A.out) if A.out else BASE / "pipeline" / "keyword_run" / f"{WEEK.isoformat()}_{A.channel}"
    D.mkdir(parents=True, exist_ok=True)
    RUN_ID = f"kw_{A.channel}_{WEEK:%Y%m%d}"
    rs.RUN = D
    bt.RUN = D
    bt.TODAY = date.today()
    bt.BLOG_LIMIT, bt.TREND_LIMIT = A.blog_limit, 0
    last = STAGES.index(A.until) if A.until else len(STAGES) - 1
    status("진행 중", "seeds")
    seeds, seeds_info = stage_seeds()
    if last < 1:
        status("끝남(일부)", "seeds")
        return
    status("진행 중", "lookup")
    cands, drops = stage_lookup(seeds)
    if last < 2:
        status("끝남(일부)", "lookup")
        return
    status("진행 중", "score")
    rows, perf, perf_note, led = stage_score(cands, drops)
    if last < 3:
        jsave(D / "keywords.json", {"run_id": RUN_ID, "keywords": rows})
        status("끝남(일부)", "score")
        return
    status("진행 중", "titles")
    approved, held, short, cache = stage_titles_gate(rows, led)
    status("진행 중", "build")
    sched, th_cnt, plan_cost = build_outputs(rows, approved, held, short, perf, perf_note, seeds_info, drops, None)
    shortage = short["A"] + short["B"]
    status("끝남", "build", None, approved=len(sched), held=len(held), shortage=shortage, 갈래별=th_cnt, 같은_날_연속_겹침_점수=plan_cost,
           claude_calls=claude_count(), no_claude=bool(A.no_claude))
    print(f"[끝] 승인 {len(sched)}편 · 보류 {len(held)}편 · 부족 {shortage}편 · 결과 {rel(D)}")
    run_push(A.apply)


if __name__ == "__main__":
    main()
