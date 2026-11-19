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
    rk = d["radar_kw"]
    off = set(rk.get("theme_off") or [])          # 꺼 둔 갈래(예: 방꾸미기)는 뽑지 않는다
    rk["theme_order"] = [t for t in rk["theme_order"] if t not in off]
    return rk


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


def kind_check(c):
    """틀 B 후보의 제품 종류 확인(filter.json product_kind). 상품명·키워드에 제외 낱말이 있으면 c['kind_exclude']=그룹 이름,
    이동 규칙에 맞으면 갈래·큰 검색어를 옮기고 c['kind_move'] 에 옮긴 기록을 남긴다."""
    pk = RK.get("product_kind") or {}
    text = nk(c["keyword"] + " " + (c.get("product") or ""))
    for grp, words in (pk.get("exclude_groups") or {}).items():
        hit = next((w for w in words if nk(w) in text), None)
        if hit:
            c["kind_exclude"] = f"{grp}('{hit}')"
            return
    prod = nk(c.get("product") or "")
    for rule in pk.get("move_rules") or []:
        if not any(nk(w) in nk(c["seed"]) for w in rule["seed_contains"]):
            continue
        if not any(nk(w) in prod for w in rule["markers"]):
            continue
        if any(nk(w) in prod for w in rule["accessory_words"]):
            continue
        c["kind_move"] = {"kind": rule["kind"], "from_seed": c["seed"], "from_theme": c["theme"], "to_seed": rule["seed"], "to_theme": rule["theme"]}
        c["seed"], c["theme"] = rule["seed"], rule["theme"]
        return


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
        names_by = {}                                       # 같은 모델명으로 수집된 상품명 모음(제품 종류 확인용)
        with open(mfile, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("모델명 종류") == "모델명":
                    names_by.setdefault(nk(r.get("모델명") or ""), []).append((r.get("상품명") or "").strip())
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
                cands[k] = {"keyword": kw, "nk": k, "volume": vol, "seed": sd, "theme": theme_of(sd), "docs": docs, "src": MODEL_SRC,
                            "product": " / ".join(dict.fromkeys(names_by.get(k, [])))}
                kind_check(cands[k])
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
        if c.get("kind_exclude"):
            r["reasons"].append(f"제품 종류 제외: 책상에서 쓰지 않는 물건 — {c['kind_exclude']}")
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
    """radar_rules.md 에서 '## 번호. 이름…' 소제목 한 칸을 소제목 글자로 찾아 돌려준다(번호는 안 봄).
    다음 '## ' 줄 앞까지가 한 칸이고, 코드 상자(```) 안의 '## ' 줄은 소제목으로 안 친다.
    칸을 못 찾으면 빈 값 대신 오류를 내서 멈춘다."""
    lines = RULES_MD.read_text(encoding="utf-8").splitlines()
    in_code, start, out = False, None, []
    for ln in lines:
        if ln.lstrip().startswith("```"):
            in_code = not in_code
        is_head = (not in_code) and ln.startswith("## ")
        if start is not None:
            if is_head:
                break
            out.append(ln)
        elif is_head and re.sub(r"^\d+\.\s*", "", ln[3:].strip()).startswith(name):
            start = ln
            out.append(ln)
    if start is None:
        raise RuntimeError(f"규칙 파일 {RULES_MD.name} 에서 「## … {name}」 칸을 못 찾았어요. 규칙 파일의 소제목을 확인하세요.")
    return "\n".join(out).strip()


def rules_common_bans():
    """문장 쓰는 법 칸의 금지·써본 줄 + 피할 표현 칸(소제목 줄 빼고 전부)."""
    ls = [ln for ln in rules_section("문장 쓰는 법").splitlines() if ("금지" in ln or "써본" in ln)]
    ls += rules_section("피할 표현").splitlines()[1:]
    return "\n".join(l for l in ls if l.strip())


ENDING_HINT = {
    "추천": "'추천' 낱말을 넣고, 구체적인 기준 1~2개를 함께(예: 듀얼 모니터 받침대 추천, 책상 폭·모니터 무게별로 고른 3가지)",
    "질문": "물음표로 끝나고 답이 보이는 구체적인 질문(예: 발받침대, 어느 높이가 오래 앉기 편할까?)",
    "숫자": "'○가지' 처럼 항목 개수를 넣되 무엇을 따지는지 구체적으로(예: 무선 마우스, 무게·소음·배터리 3가지)",
    "확인할 점": "'사기 전'·'구매 전'·'확인할' 중 하나를 넣되 확인할 것을 구체적으로(예: 책상 정리함, 사기 전 서랍 깊이·책상 두께 확인)",
    "비교·차이": "'비교'·'차이'·'다른 점' 중 하나를 넣되 무엇이 다른지 구체적으로(예: 보조배터리 10000 vs 20000, 무게·충전 횟수 차이)",
    "고르는 법·기준": "'고르는 법'·'기준' 중 하나를 넣되 구체적인 기준과 함께(예: 발난로 고르는 법, 소비전력·책상 밑 높이 기준)",
}
ENDING_HINT_B = {
    "궁금한 점 정리": "그 제품의 구체적인 궁금증 1~2개를 '정리'·'한눈에'로 묶는다(예: 카멜마운트 PMA2X 모니터암, 책상 두께·하중 정리)",
    "답이 보이는 질문": "그 제품의 구체적인 궁금증 하나를 물음표 질문으로(예: 로지텍 G304 무선마우스, 배터리 얼마나 갈까?)",
    "살 만할까": "사기 전 망설일 구체적인 점을 넣고 '살 만'·'사도 괜찮'으로 묻는다(예: AULA F108Pro 키보드, 소음 걱정 없이 살 만할까?)",
    "이런 분께 맞아요": "'분께'·'맞는 분'으로 쓰임새를 구체적으로 짚는다(예: JBL FLIP7 블루투스스피커, 캠핑과 책상 둘 다 쓸 분께)",
    "대안과 차이": "'비슷한'·'다른 점'·'차이'·'대안' 중 하나로 비슷한 제품과 다른 점을 짚는다(예: 앤커 A1638 보조배터리, 비슷한 제품과 다른 점)",
}
A_COUNTER = {"A": 0, "B": 0}      # 제목 요청이 몇 번째 글인지(끝맺음을 돌려 배정하는 용도, 호출 사이에도 이어 감)

TITLE_PROMPT = """너는 네이버 블로그 'Desk Picker'(데스크피커)의 제목 담당이다. 도구는 쓰지 말고 바로 답해라.
아래 키워드마다 블로그 글 제목 3안과 search_query 를 만들어라.

{rules_title}

[지킬 것]
{bans}
- 제목은 40자 이내. 키워드의 글자와 순서는 그대로 두고, 띄어쓰기만 자연스럽게 맞춘다(예: 맥세이프보조배터리 → 맥세이프 보조배터리). 키워드가 제목 맨 앞 15자(띄어쓰기 뺀 글자 기준) 안에 들어가야 한다.
- 끝맺음 이름(궁금한 점 정리, 대안과 차이, 이런 분께 맞아요, 살 만할까, 사기 전 확인할 점, 고를 때 볼 3가지 같은 유형 이름)을 글자 그대로 붙이지 마라. 대신 그 글이 답할 구체적인 궁금증 1~2개(소음·배터리·크기·무게·호환·하중·설치·전력 등, 상품명과 상품 종류에서 상품 페이지·구매자 후기로 답할 수 있는 것만)를 넣어서 끝맺음의 뜻이 드러나게 써라.
- 틀 A(고르는 법): 키워드 + 구체적인 궁금증 + 끝맺음. 목록의 '끝맺음' 칸에 적힌 3가지를 1안·2안·3안에 순서대로 하나씩 써라(3안은 서로 끝맺음이 달라야 한다). 끝맺음은 아래 뜻대로 드러내라.
{ending_help}
- 틀 B(모델명): 브랜드 모델명 + 큰 검색어(목록의 '상품 종류' 칸 낱말, 예: 탁상시계·블루투스스피커)를 그대로 제목에 넣고, 쉼표 뒤에 구체적인 궁금증으로 '끝맺음' 칸의 뜻을 드러내라. 끝맺음 칸의 3가지를 1안·2안·3안에 순서대로 하나씩 써라(3안은 서로 끝맺음이 달라야 한다). 목록의 '상품명' 칸으로 이 제품이 실제로 어떤 물건인지 확인하고 그에 맞는 궁금증을 골라라.
{ending_help_b}
- '추천'은 써도 되지만 '직접·제가·써본·솔직·찐·인생' 같은 경험을 꾸미는 말과 함께 쓰지 않는다. 금지 단어(후기, 내돈내산, 써보니, 사용기, 실사용, 직접 써본, 최고, 무조건, 100%, 완벽, 강력 추천, 1위)는 제목에 넣지 않는다.
- 써본 척 표현·효과 단정·모르는 숫자는 쓰지 않는다. 한국어로만 쓴다.
- search_query: 제품 후보를 찾을 때 쓸 쇼핑 검색어. 틀 A는 핵심 물건 2~3단어(띄어쓰기로 구분, 예: "발난로 사무실"), 틀 B는 "브랜드 모델명"(예: "브라운 BC02XW"). 반드시 키워드에 들어 있는 낱말만 써라.
반드시 아래 JSON 한 덩어리로만 답해라(설명 금지).
{{"items": [{{"id": "K1", "titles": ["제목1", "제목2", "제목3"], "search_query": "발난로 사무실"}}]}}

[키워드 목록] (id | 키워드 | 틀 | 상품 종류 | 갈래 | 끝맺음 | 상품명)
{lines}
"""


def assigned_endings(c, want=None):
    """한 글에 3안으로 요청할 끝맺음 3가지(want 가 있으면 그것을 앞에 두고 모자라면 돌려 배정). 틀 A 는 6가지, 틀 B 는 5가지 중에서."""
    t = c["type"]
    order = RK["title_endings_A" if t == "A" else "title_endings_B"]["order"]
    g = A_COUNTER[t]
    A_COUNTER[t] += 1
    out = list(want or [])
    for off in (0, 1, 3, 2, 4, 5):
        e = order[(g + off) % len(order)]
        if len(out) >= 3:
            break
        if e not in out:
            out.append(e)
    return out[:3]


def claude_titles(batch, want=None):
    """batch: 후보 목록. 반환 {nk: {"titles": [...], "search_query": "..."}}, 실패하면 None. 호출 수 상한을 지킨다."""
    rows_ = []
    for i, c in enumerate(batch):
        e = " / ".join(assigned_endings(c, (want or {}).get(c["nk"])))
        rows_.append(f"K{i + 1} | {c['keyword']} | 틀 {c['type']} | {c['seed']} | {c['theme']} | {e} | {(c.get('product') or '-')[:70]}")
    lines = "\n".join(rows_)
    help_ = "\n".join(f"  · {k}: {v}" for k, v in ENDING_HINT.items())
    help_b = "\n".join(f"  · {k}: {v}" for k, v in ENDING_HINT_B.items())
    prompt = TITLE_PROMPT.format(rules_title=rules_section("제목"), bans=rules_common_bans(), ending_help=help_,
                                 ending_help_b=help_b, lines=lines)
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
        sd = c["seed"] if nk(c["seed"]) not in nk(kw) else ""
        base = f"{kw} {sd}".strip()
        tpl_b = {"궁금한 점 정리": f"{base}, 크기·무게 정리", "답이 보이는 질문": f"{base}, 크기는 얼마나 될까?", "살 만할까": f"{base}, 가격 대비 살 만할까?",
                 "이런 분께 맞아요": f"{base}, 책상에서 쓸 분께", "대안과 차이": f"{base}, 비슷한 제품과 다른 점"}
        titles = [tpl_b[e] for e in assigned_endings(c)]
    else:
        tpl = {"추천": f"{kw} 추천, 크기·용도별로 고른 제품", "질문": f"{kw}, 크기는 무엇을 보고 고를까?", "숫자": f"{kw}, 크기·무게·가격 3가지",
               "확인할 점": f"{kw}, 사기 전 크기·무게 확인", "비교·차이": f"{kw} 비교, 크기·무게 차이", "고르는 법·기준": f"{kw} 고르는 법, 크기·무게 기준"}
        titles = [tpl[e] for e in assigned_endings(c)]
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


def ending_of(title, ttype):
    """제목의 끝맺음 이름. 틀 A: 추천·질문·숫자·확인할 점·비교·차이·고르는 법·기준 (번호 순서대로 먼저 맞는 것), 해당 없으면 None. 틀 B: filter.json title_endings_B 의 5가지, 해당 없으면 None."""
    t = " ".join(title.split())
    cfg = RK["title_endings_A"] if ttype == "A" else RK["title_endings_B"]
    for name in (cfg["order"] if ttype == "A" else cfg["match_order"]):
        if any(pat_hit(rx, t) for rx in cfg[name]):
            return name
    return None


def pat_hit(rx, t):
    """끝맺음 패턴 하나가 제목에 맞는가. 정규식 기호(\\ ? $ 등)가 든 패턴은 그대로, 보통 글자 패턴은 띄어쓰기를 무시하고 비교."""
    if re.search(r"[\\\[\]\(\)\?\*\+\$\^\|]", rx):
        return bool(re.search(rx, t, re.I))
    return nk(rx) in nk(t)


def bare_issue(title, kw, seed):
    """유형 이름만 붙은 제목인가(filter.json title_concrete). 키워드·큰 검색어·유형 이름 글귀를 지우고 남은 글자가 min_rest 미만이면 사유 글을 돌려줌."""
    cfg = RK["title_concrete"]
    s = nk(title)
    for w in (nk(kw), nk(seed or "")):
        if w:
            s = s.replace(w, "")
    for w in sorted((nk(x) for x in cfg["bare_phrases"]), key=len, reverse=True):
        s = s.replace(w, "")
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", s)
    if len(s) < cfg["min_rest"]:
        return f"유형 이름만 붙은 제목(구체적인 궁금증 {cfg['min_rest']}글자 이상 필요, 남은 글자 '{s}')"
    return None


def title_issues(title, kw, ttype=None, seed=None):
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
    for w in RK.get("title_recommend_bad", []):
        if nk(w) in nk(t):
            bad.append(f"경험을 꾸미는 말과 함께 쓴 '추천'('{w}')")
    if ttype == "A" and not ending_of(t, "A"):
        bad.append("틀 A 끝맺음 6가지에 해당 안 함")
    if ttype == "B":
        if not ending_of(t, "B"):
            bad.append("틀 B 끝맺음 5가지에 해당 안 함")
        if RK["title_endings_B"].get("seed_in_title") and seed and nk(seed) not in nk(t):
            bad.append(f"큰 검색어 '{seed}' 가 제목에 없음")
    if re.search(r"\d+\s*일\s*(간\s*)?사용", t):
        bad.append("써본 척 표현 '○일 사용'")
    if nk(kw) not in nk(t)[:RK["title_front"]]:        # 띄어쓰기를 무시하고 앞 title_front 글자 안에 키워드(글자·순서 그대로)
        bad.append(f"키워드가 앞 {RK['title_front']}자 안에 없음")
    if ttype in ("A", "B"):
        b = bare_issue(t, kw, seed)
        if b:
            bad.append(b)
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


def choose_title(c, gen, avoid=()):
    """통과안 중 최고점 1개(avoid 에 있는 이미 승인된 제목과 겹치지 않는 안을 먼저 씀). 반환 (title|None, 검사 기록, search_query, 보류 사유|None)"""
    checks = []
    for t in gen.get("titles", []):
        bad = title_issues(t, c["keyword"], c["type"], c.get("seed"))
        checks.append({"title": t, "ok": not bad, "issues": bad, "end": ending_of(t, c["type"]),
                       "points": None if bad else title_points(t, c["keyword"])})
    ok = [x for x in checks if x["ok"]]
    if not checks:
        return None, checks, gen.get("search_query", ""), "제목 생성 안 됨(claude 응답 없음)"
    if not ok:
        return None, checks, gen.get("search_query", ""), "제목 3안 모두 기계 검사 탈락: " + "; ".join(sorted({i for x in checks for i in x["issues"]}))
    free = [x for x in ok if all(sim_ratio(x["title"], a) < RK["similarity"] for a in avoid)] or ok
    best = max(free, key=lambda x: x["points"])
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
    if A.only_from:                                  # 키워드 구성을 고정: 이전 plan 에 있던 키워드만 후보로 둔다(제목만 다시 만드는 용도)
        keep = {nk(x["keyword"]) for x in json.loads(Path(A.only_from).read_text(encoding="utf-8"))["plans"]}
        cand = [r for r in cand if r["nk"] in keep]
        print(f"[titles] --only-from: 이전 plan 키워드 {len(keep)}개 중 후보 {len(cand)}개만 사용")
    by_nk = {r["nk"]: r for r in cand}
    old_q = {}
    if A.only_from:
        old_q = {nk(x["keyword"]): (x.get("radar") or {}).get("search_query", "") for x in json.loads(Path(A.only_from).read_text(encoding="utf-8"))["plans"]}
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
            if pn and pn in by_nk:                                   # 겹치는 짝이 아직 결과가 안 났거나 승인됐으면 이번엔 건너뜀
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
            if not A.no_claude and len(chunk) < RK["title_batch"]:      # 호출 수를 줄이려고 다음 순서 후보의 제목을 미리 함께 받아 둔다(쓰이지 않으면 캐시에만 남음)
                have = {r["nk"] for r in chunk} | set(cache)
                chunk += [r for r in cand if r["nk"] not in have and r["nk"] not in done][:RK["title_batch"] - len(chunk)]
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
            title, checks, q, why = choose_title(r, g, [a["title"] for a in approved])
            if old_q.get(r["nk"]):
                q = old_q[r["nk"]]                  # --only-from: 이전 plan 의 search_query 를 그대로 재사용
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
        if r["status"] in ("후보", "예비") and pn and pn in by_nk and by_nk[pn]["status"] == "승인":
            r["status"] = "탈락"
            r["reasons"].append(f"같은 주 후보 겹침({by_nk[pn]['keyword']} 승인)")
    short = {t: need[t] - ab_n[t] for t in "AB"}
    return approved, held, short, cache


def assign_dates(approved):
    """승인 항목을 7일에 하루 틀 A·B 편수(filter.json ab_day: 지금 A1+B2)로 배정: 같은 날 같은 갈래 금지, 이틀 연속 같은 갈래는 가능한 한 피함(고정 시드 무작위 개선).
    --only-from 이면 이전 plan 의 날짜·순서를 그대로 쓴다."""
    if A.only_from:
        old = {nk(x["keyword"]): (i, x["publish_date"]) for i, x in enumerate(json.loads(Path(A.only_from).read_text(encoding="utf-8"))["plans"])}
        miss = [r["keyword"] for r in approved if r["nk"] not in old]
        if miss:
            stop("dates", "이전 plan 에 없는 키워드가 승인됨: " + ", ".join(miss))
        out = sorted(approved, key=lambda r: old[r["nk"]][0])
        for n, r in enumerate(out, 1):
            d = date.fromisoformat(old[r["nk"]][1])
            r["date"], r["weekday"], r["no"] = d.isoformat(), WEEKDAYS[d.weekday()], n
        return out, 0
    day_kinds = ["A"] * RK["ab_day"]["A"] + ["B"] * RK["ab_day"]["B"]
    per_day = len(day_kinds)
    slots = [(d, k) for d in range(7) for k in day_kinds]
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
                sw = [(per_day * d1 + k, per_day * d2 + k) for k in range(per_day)]
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


# ---------------- 5-2) 끝맺음 분산 (한 주 틀 A·틀 B 따로) ----------------
def _cfg_of(ttype):
    return RK["title_endings_A" if ttype == "A" else "title_endings_B"]


def _opts(r, ttype):
    """그 글의 통과안(끝맺음 이름 붙음). 처음 고른 제목이 맨 앞."""
    seen, out = set(), []
    for x in sorted((x for x in r.get("title_checks", []) if x["ok"]), key=lambda x: (x["title"] != r["title"], -(x["points"] or 0))):
        if x["title"] in seen:
            continue
        seen.add(x["title"])
        out.append({"title": x["title"], "end": x.get("end") or ending_of(x["title"], ttype), "points": x["points"] or 0})
    return out


def _search(As, ttype, fixed_titles, strict):
    """모든 글에 통과안 하나씩 고르는 조합 중 '처음 고른 것에서 바꾸는 글 수'가 가장 적고 점수가 높은 것.
    규칙(숫자는 모두 filter.json): 같은 끝맺음 최대 same_max 편, (틀 A만) '고르는 법' 최대 pick_max 편, 같은 날 같은 틀 글끼리는 서로 다른 끝맺음.
    strict=False 면 규칙을 넘는 선택도 허용하되 넘은 개수가 가장 적은 조합(재생성 대상 찾기용)."""
    cfg = _cfg_of(ttype)
    opts = [_opts(r, ttype) for r in As]
    n = len(As)
    order = sorted(range(n), key=lambda i: (As[i]["date"], len(opts[i])))
    best = [None, None]
    cnt, pick = {}, [0]
    chosen = {}

    def cost_of(viol, chg, pts):
        return (viol, chg, -pts)

    def rec(k, viol, chg, pts):
        if best[0] is not None and (viol, chg) > best[0][:2]:
            return
        if k == n:
            c = cost_of(viol, chg, pts)
            if best[0] is None or c < best[0]:
                best[0], best[1] = c, dict(chosen)
            return
        i = order[k]
        for j, o in enumerate(opts[i]):
            v = 0
            if cnt.get(o["end"], 0) >= cfg["same_max"]:
                v += 1
            ispick = bool(cfg.get("pick_text")) and any(w in o["title"] for w in cfg["pick_text"])
            if ispick and pick[0] >= cfg["pick_max"]:
                v += 1
            for m_, oj in chosen.items():
                if As[m_]["date"] == As[i]["date"] and oj["end"] == o["end"]:
                    v += 1
                if sim_ratio(o["title"], oj["title"]) >= RK["similarity"]:
                    v += 1
            if any(sim_ratio(o["title"], ft) >= RK["similarity"] for ft in fixed_titles):
                v += 1
            if v and strict:
                continue
            chosen[i] = o
            cnt[o["end"]] = cnt.get(o["end"], 0) + 1
            pick[0] += 1 if ispick else 0
            rec(k + 1, viol + v, chg + (0 if j == 0 else 1), pts + o["points"])
            del chosen[i]
            cnt[o["end"]] -= 1
            pick[0] -= 1 if ispick else 0

    rec(0, 0, 0, 0)
    return best[1], best[0], opts


def _distribute_type(sched, ttype):
    As = [r for r in sched if r["type"] == ttype]
    fixed = [r["title"] for r in sched if r["type"] != ttype]
    cfg = _cfg_of(ttype)
    for r in As:
        r["title_first"] = r["title"]
    sol, cost, opts = _search(As, ttype, fixed, True)
    regen = 0
    if sol is None:
        sol_r, cost_r, _ = _search(As, ttype, fixed, False)
        # 규칙을 넘는 글 찾기: 넘친 끝맺음 그룹에서 통과안이 가장 적은 글부터, 같은 날 겹침은 뒤 글
        cnt, bad = {}, []
        for i in sorted(sol_r, key=lambda i: (len(opts[i]), As[i]["date"])):
            cnt.setdefault(sol_r[i]["end"], []).append(i)
        for e, idx in cnt.items():
            for i in idx[cfg["same_max"]:]:
                bad.append(i)
        if cfg.get("pick_text"):
            picks = [i for i in sol_r if any(w in sol_r[i]["title"] for w in cfg["pick_text"])]
            for i in picks[cfg["pick_max"]:]:
                bad.append(i)
        for a in sol_r:
            for b in sol_r:
                if a < b and As[a]["date"] == As[b]["date"] and sol_r[a]["end"] == sol_r[b]["end"]:
                    bad.append(b)
        bad = sorted(set(bad))
        if not bad:
            bad = [i for i in sol_r if sum(1 for x in opts[i]) <= 1][:3] or [min(sol_r, key=lambda i: len(opts[i]))]
        use = {e: sum(1 for o in sol_r.values() if o["end"] == e) for e in cfg["order"]}
        order_less = sorted(cfg["order"], key=lambda e: use[e])
        batch = [As[i] for i in bad]
        want = {As[i]["nk"]: [e for e in order_less if e != sol_r[i]["end"]][:3] for i in bad}
        log(f"  끝맺음 분산(틀 {ttype}): 통과안으로는 규칙을 못 맞춤 → {len(batch)}편만 제목 다시 생성(claude 1회): " + ", ".join(r["keyword"] for r in batch))
        got = claude_titles(batch, want)
        regen = len(batch)
        if got:
            cache = jload(D / "titles_cache.json", {}) or {}
            for r in batch:
                g = got[r["nk"]]
                for t in g.get("titles", []):
                    bad_ = title_issues(t, r["keyword"], ttype, r.get("seed"))
                    r["title_checks"].append({"title": t, "ok": not bad_, "issues": bad_, "end": ending_of(t, ttype),
                                              "points": None if bad_ else title_points(t, r["keyword"]), "regen": True})
                if r["nk"] in cache:
                    cache[r["nk"]]["regen_titles"] = g.get("titles", [])
            jsave(D / "titles_cache.json", cache)
        sol, cost, opts = _search(As, ttype, fixed, True)
        if sol is None:
            lim = f"같은 끝맺음 최대 {cfg['same_max']}편" + (f"·고르는 법 최대 {cfg['pick_max']}편" if cfg.get("pick_text") else "") + "·같은 날 다른 끝맺음"
            stop("titles", f"끝맺음 분산 규칙(틀 {ttype}: {lim})을 맞출 제목 조합이 없음 — 다시 생성할 글: " + ", ".join(r["keyword"] for r in batch))
    changed = []
    for i, r in enumerate(As):
        o = sol[i]
        r["end"] = o["end"]
        if o["title"] != r["title"]:
            changed.append({"keyword": r["keyword"], "date": r["date"], "before": r["title"], "after": o["title"], "end": o["end"]})
            r["title"] = o["title"]
    return changed, regen


def distribute_endings(sched):
    """날짜가 정해진 승인 글의 제목을 틀 A·틀 B 따로 분산 규칙에 맞춘다(틀 A 먼저, 그 제목을 고정하고 틀 B).
    넘으면 그 글의 다른 통과안으로 바꾸고, 안 되는 글만 claude 로 다시 만든다(틀마다 최대 1회). 반환: 바뀐 글 목록, 다시 만든 글 수."""
    ch_a, rg_a = _distribute_type(sched, "A")
    ch_b, rg_b = _distribute_type(sched, "B")
    return ch_a + ch_b, rg_a + rg_b


# ---------------- 6) 결과 ----------------
def build_outputs(rows, approved, held, short, perf, perf_note, seeds_info, drops, bundle):
    sched, plan_cost = assign_dates(approved)
    dist_changed, dist_regen = distribute_endings(sched)
    jsave(D / "ending_distribution.json", {"changed": dist_changed, "regenerated": dist_regen})
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
    cat_warn = []                                      # 큰 카테고리 비중(강제하지 않고 경고만)
    for cat, ths in (RK.get("category_groups") or {}).items():
        lo_hi = (RK.get("category_range") or {}).get(cat)
        if not lo_hi or all(t in (RK.get("theme_off") or []) for t in ths):
            continue
        n = sum(th_cnt.get(t, 0) for t in ths)
        if n < lo_hi[0] or n > lo_hi[1]:
            cat_warn.append(f"{cat} {n}편(목표 {lo_hi[0]}~{lo_hi[1]}편)")
    if cat_warn:
        L.append("# ⚠ 큰 카테고리 비중 범위 밖: " + " · ".join(cat_warn))
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
    L.append("## 끝맺음별 개수")
    L.append("| 틀 | 끝맺음 | 편수 |")
    L.append("|---|---|---|")
    for e in RK["title_endings_A"]["order"]:
        L.append(f"| A | {e} | {sum(1 for r in sched if r['type'] == 'A' and r.get('end') == e)} |")
    for e in RK["title_endings_B"]["order"]:
        L.append(f"| B | {e} | {sum(1 for r in sched if r['type'] == 'B' and r.get('end') == e)} |")
    L.append(f"분산 규칙으로 바뀐 제목 {len(dist_changed)}편 · 다시 생성 {dist_regen}편")
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
    ap.add_argument("--only-from", help="이전 plan.json 경로: 그 plan 의 키워드만 후보로 쓰고 날짜·순서도 그대로 둔다(제목만 다시 만들 때)")
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
