# -*- coding: utf-8 -*-
"""주간 기획 도구: r1c.py 를 매주 쓸 수 있게 일반화한 것.

  기획:  python tools/weekly_plan.py --week 2026-11-23 --seeds pipeline/final/seeds_20261123.txt --skip pipeline/final/skip_20261123.txt [--must 파일] [--out 폴더]
  확정:  python tools/weekly_plan.py --finalize --week 2026-11-23 [--edits pipeline/final/edits_20261123.json]
  (앱에 넣기는 tools/push_plan.py)

운영 파일·앱 데이터는 건드리지 않는다(예외: 이름표 반영 = 운영 target_alias.json, 백업 후). 결과는 전부 결과 폴더(기본 pipeline/stage_w월일_run/)에만 쓴다.
단계마다 결과를 파일로 저장하므로 끊겨도 같은 명령으로 이어 간다(저장된 API 값·Claude 판정은 다시 받지 않는다).
단계 순서: index(기존 글 목록) → explore(탐색) → trend(데이터랩) → shape(모양, haiku 1회) → signals(신호·후보 풀) → judge(중복 판정, sonnet 1회)
          → fill(채우기) → plan(기획 2번·제목) → check(제목-본문 설계 맞추기) → build(결과물)
--until 단계 : 그 단계까지만 한다.   --no-claude : Claude 를 부르지 않고 저장된 결과만 쓴다(없으면 그 단계는 건너뜀).
"""
import argparse
import copy
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BASE = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(BASE))

import backtest as bt  # noqa: E402
import plan_edits  # noqa: E402
import plan_posts as pp  # noqa: E402
import push_plan  # noqa: E402
import r1 as r1m  # noqa: E402
import r1b  # noqa: E402
import r1_signals as rs  # noqa: E402
from safe_io import write_text_retry  # noqa: E402

nospace, jload, jsave, log = bt.nospace, bt.jload, bt.jsave, bt.log

STAGES = ["index", "explore", "trend", "shape", "signals", "judge", "fill", "plan", "check", "build"]
PER_DAY = {"naver": 3, "wordpress": 2}
PER_SEED_MAX = 12            # 씨앗 하나에서 후보로 남기는 연관 키워드 수
NAV_POOL_MAX, NAV_PER_SEED = 45, 4
WP_TOPICS_MAX, WP_PER_TOPIC_POOL, WP_MONEY_POOL = 8, 5, 18
WP_PER_TOPIC_FILL = 3        # 싼세계 정보글: 한 묶음에서 한 주에 최대
EXCLUDED_TYPES = {"local", "service", "buyplace", "offtopic"}
NAVER_SRC = {"naver_blog", "content_queue_naver_tips.json", "content_queue_radar_naver.json", "content_queue_shop_naver.json", "plan_final_naver"}
WP_SRC = {"shop", "content_queue_wp_tips.json", "content_queue_shop_wp.json", "plan_final_wordpress"}
QUEUE_PLATFORM = {"content_queue_naver_tips.json": "naver", "content_queue_radar_naver.json": "naver", "content_queue_shop_naver.json": "naver",
                  "content_queue_wp_tips.json": "wordpress", "content_queue_shop_wp.json": "wordpress"}
OBJ_PREFIX = sorted(["사무실", "화장실", "욕실", "미니", "가정용", "전기", "휴대용", "충전식", "충전", "남자", "여자", "남성", "여성", "주니어", "어린이", "1인용",
                     "소형", "겨울", "방한", "현관", "거실", "주방", "차량용", "캠핑", "무선", "크리스마스"], key=len, reverse=True)

# ---- 실행 설정(main 에서 채움) ----
A = None          # argparse 결과
D = None          # 결과 폴더
WEEK = None       # 그 주 월요일
TODAY = None
_ALIAS = None


def ymd():
    return WEEK.strftime("%Y%m%d")


# ================= 공통 =================
def read_lines(path):
    if not path:
        return []
    out = []
    for ln in Path(path).read_text(encoding="utf-8-sig").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def parse_seeds(path):
    """한 줄에 하나. 선택 확장: `씨앗 | 연관 낱말,낱말 | 갈래 이름` (낱말을 안 적으면 씨앗 자신, 갈래를 안 적으면 씨앗 자신)."""
    seeds = []
    for ln in read_lines(path):
        parts = [x.strip() for x in ln.split("|")]
        word = parts[0]
        toks = [t.strip() for t in parts[1].split(",") if t.strip()] if len(parts) > 1 and parts[1] else [word]
        theme = parts[2] if len(parts) > 2 and parts[2] else word
        seeds.append({"word": word, "tokens": toks, "theme": theme})
    return seeds


def parse_must(path):
    """한 줄에 `플랫폼 | 키워드 | 한 줄 설명`. 플랫폼: 네이버/naver, 싼세계/wordpress/wp. 플랫폼 칸이나 설명에 '수익'이 있으면 싼세계 수익글 자리."""
    out = []
    for ln in read_lines(path):
        parts = [x.strip() for x in ln.split("|")]
        if len(parts) < 2 or not parts[1]:
            raise SystemExit(f"must 파일 형식 오류(`플랫폼 | 키워드 | 한 줄 설명`): {ln}")
        pf = parts[0].lower()
        plat = "naver" if ("네이버" in pf or "naver" in pf) else ("wordpress" if ("싼세계" in pf or "wp" in pf or "wordpress" in pf or "워드" in pf) else None)
        if not plat:
            raise SystemExit(f"must 파일의 플랫폼을 모르겠음: {ln}")
        note = parts[2] if len(parts) > 2 else ""
        money = plat == "wordpress" and ("수익" in pf or "money" in pf or note.startswith("[수익]"))
        out.append({"platform": plat, "keyword": parts[1], "note": note.replace("[수익]", "").strip(), "track": "money" if money else "info"})
    return out


def wp_on_values() -> dict:
    """filter.json 'wp_on_values'(싼세계를 껐을 때 적어 둔 원래 값). 없으면 빈 dict."""
    d = json.loads((BASE / "tools" / "filter.json").read_text(encoding="utf-8-sig"))
    return d.get("wp_on_values") or {}


def wp_enabled(c) -> bool:
    """싼세계 편수(정보·수익·예비) 합이 0 이면 꺼짐 — 풀 만들기·중복 판정·채우기·보고서에서 싼세계를 건너뛴다."""
    wp = c.get("wordpress") or {}
    return sum(int(wp.get(k) or 0) for k in ("info", "money", "reserve")) > 0


def pools_wp_on() -> bool:
    """이 결과 폴더의 풀을 만들 때 싼세계가 켜져 있었는지(pools.json 의 wp_on). 값이 없는 옛 폴더는 켜짐으로 본다(예전과 같음)."""
    pl = jload(D / "pools.json", {}) or {}
    return bool(pl.get("wp_on", True))


def load_weekly():
    """pp.load_weekly + (--with-wp 이면) 싼세계 편수·예비를 wp_on_values 로 되살림(메모리에서만)."""
    w = pp.load_weekly(BASE / "tools" / "filter.json")
    if getattr(A, "with_wp", False):
        v = wp_on_values()
        if not v:
            raise SystemExit("--with-wp: filter.json 에 wp_on_values 가 없습니다.")
        w["posts_per_day"]["wordpress"] = v["posts_per_day_wordpress"]
        w["reserve_count"] = v["reserve_count"]
        if w.get("track_quota"):
            w["track_quota"]["wordpress"] = dict(v["track_quota_wordpress"])
            w["track_quota"]["reserve"] = dict(v["track_quota_reserve"])
        if w.get("r1"):
            w["r1"]["wordpress"] = {**w["r1"]["wordpress"], **v["r1_wordpress"]}
    return w


def make_cfg():
    c = copy.deepcopy(rs.cfg())
    if getattr(A, "with_wp", False):
        _v = wp_on_values()
        if not _v:
            raise SystemExit("--with-wp: filter.json 에 wp_on_values 가 없습니다.")
        c["wordpress"] = {**c["wordpress"], **_v["r1_wordpress"]}
    last_full = TODAY - timedelta(days=1)       # 데이터랩은 어제까지: 7일이 다 찬 가장 최근 주를 '최근 주'로 쓴다
    m = bt.monday_of(last_full)
    if m + timedelta(days=6) > last_full:
        m -= timedelta(days=7)
    c["plan_monday"], c["recent_monday"] = WEEK.isoformat(), m.isoformat()
    return c


def stats():
    return jload(D / "stats.json", {}) or {}


def stat_set(stage, reason, val):
    s = stats()
    s.setdefault(stage, {})[reason] = val
    jsave(D / "stats.json", s)


def claude_used(stage, model, env):
    u = jload(D / "usage.json", {}) or {}
    u.setdefault("claude_calls", []).append({"stage": stage, "model": model, "cost_usd": float((env or {}).get("total_cost_usd") or 0)})
    u["claude"] = len(u["claude_calls"])
    u["claude_cost_usd"] = round(sum(c["cost_usd"] for c in u["claude_calls"]), 4)
    jsave(D / "usage.json", u)


def new_api():
    return bt.Api()


def norm_obj(o):
    """판정이 준 물건 이름에서 장소·크기·쓰는 사람 꾸밈말을 떼고 비교용으로 만든다(같은 물건을 장소만 바꿔 두 번 뽑지 않게)."""
    global _ALIAS
    if _ALIAS is None:
        a = dict((jload(BASE / "pipeline" / "target_alias.json", {}) or {}).get("alias", {}))
        a.update(dict((jload(D / "index" / "target_alias.json", {}) or {}).get("alias", {})))
        _ALIAS = {nospace(k): nospace(v) for k, v in a.items()}
    o = nospace(o)
    for pre in OBJ_PREFIX:
        if o.startswith(pre) and len(o) - len(pre) >= 2:
            o = o[len(pre):]
            break
    return _ALIAS.get(o, o)


def skip_words():
    return [nospace(w) for w in read_lines(A.skip)]


def is_skipped(nk, skips):
    return any(s in nk for s in skips)


def ensure_index_copy():
    (D / "index").mkdir(parents=True, exist_ok=True)
    for name in ("keyword_index.json", "post_index.json", "target_alias.json", "pair_verdicts.json", "audit_cache.json"):
        src = BASE / "pipeline" / name
        if not (D / "index" / name).exists() and src.exists():
            shutil.copy2(src, D / "index" / name)


def copy_cache():
    """시험용: 저장된 캐시를 결과 폴더로 복사(이미 있는 파일은 안 덮어씀). 네이버 API·Claude 를 다시 부르지 않기 위함."""
    if not A.cache_from:
        return
    src = Path(A.cache_from)
    for name in ("raw_cache.json", "shape.json", "type.json", "judge_result.json"):      # 기획·제목 결과는 글 목록이 달라 복사하지 않는다
        if (src / name).exists() and not (D / name).exists():
            shutil.copy2(src / name, D / name)


# ================= a) 기존 글 목록 =================
def collect_existing(exclude_week=None):
    """이미 쓴 글: 색인(발행·대기·지난 기획) + 앱 대기열 승인 항목 + 이 도구가 넣은 항목(source=weekly_plan) + pipeline/final/plan_*.json 의 지난 확정 글.
    exclude_week 의 글은 뺀다(그 주를 다시 기획할 때 자기 자신과 겹치지 않게). 반환: (플랫폼별 글 목록, 계획에서 이미 쓴 키워드 집합, 개수 요약)"""
    posts = (jload(BASE / "pipeline" / "post_index.json", {}) or {}).get("posts", {})
    ex = {"naver": [], "wordpress": []}
    seen = {"naver": set(), "wordpress": set()}
    new_entries = {}
    plan_kw = set()
    cnt = {"색인": 0, "대기열 승인": 0, "weekly_plan": 0, "확정 계획": 0}

    def add(plat, title, src, kind):
        title = " ".join(str(title or "").split())
        k = nospace(title)
        if not title or k in seen[plat]:
            return False
        seen[plat].add(k)
        ex[plat].append({"title": title, "src": src})
        cnt[kind] += 1
        return True
    for t, v in posts.items():
        s = v.get("source")
        plat = "naver" if s in NAVER_SRC else ("wordpress" if s in WP_SRC else None)
        if plat:
            add(plat, t, s, "색인")
    ex_week = exclude_week.isoformat() if exclude_week else None
    for qp in sorted(BASE.glob("content_queue*.json")):
        plat = QUEUE_PLATFORM.get(qp.name)
        if not plat or "backup" in qp.name:
            continue
        try:
            q = json.loads(qp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for it in q:
            mine = it.get("source") == "weekly_plan"
            if not (it.get("approved") or mine):
                continue
            if ex_week and it.get("week") == ex_week:
                continue
            title = ((it.get("plan") or {}).get("final_title")) or it.get("keyword")
            if add(plat, title, qp.name, "weekly_plan" if mine else "대기열 승인"):
                new_entries[" ".join(str(title).split())] = {"title": " ".join(str(title).split()), "source": qp.name, "target_raw": it.get("keyword")}
            if mine:
                plan_kw.add(nospace(it.get("keyword")))
    for pf in sorted((BASE / "pipeline" / "final").glob("plan_*.json")):
        if "backup" in pf.name:
            continue
        p = jload(pf, {}) or {}
        if not isinstance(p, dict) or not p.get("plans"):
            continue
        if ex_week and ((p.get("week") or {}).get("start") == ex_week):
            continue
        for e in p["plans"]:
            if e.get("role") != "scheduled" or e.get("platform") not in ("naver", "wordpress"):
                continue
            title = e.get("final_title") or e.get("keyword")
            if add(e["platform"], title, "plan_final_" + e["platform"], "확정 계획"):
                new_entries[" ".join(str(title).split())] = {"title": " ".join(str(title).split()), "source": "plan_final_" + e["platform"], "target_raw": e.get("keyword")}
            plan_kw.add(nospace(e.get("keyword")))
    return ex, plan_kw, cnt, new_entries


def stage_index(args=None):
    ensure_index_copy()
    ex, plan_kw, cnt, new_entries = collect_existing(WEEK)
    pi = jload(D / "index" / "post_index.json", {"posts": {}}) or {"posts": {}}
    pi.setdefault("posts", {})
    added = 0
    for t, v in new_entries.items():
        if t not in pi["posts"]:
            pi["posts"][t] = v
            added += 1
    pi["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    pi["count"] = len(pi["posts"])
    jsave(D / "index" / "post_index.json", pi)       # 결과 폴더 안의 복사본만 갱신(운영 색인은 그대로)
    jsave(D / "existing.json", {"made_at": pi["updated_at"], "counts": cnt, "naver": ex["naver"], "wordpress": ex["wordpress"], "plan_keywords": sorted(plan_kw)})
    stat_set("index", "이미 쓴 글", {"네이버": len(ex["naver"]), "싼세계": len(ex["wordpress"]), **cnt, "복사본 색인에 새로 넣음": added})
    print(f"[index] 이미 쓴 글: 네이버 {len(ex['naver'])}편 · 싼세계 {len(ex['wordpress'])}편 ({cnt}), 복사본 색인에 새로 넣은 것 {added}개")


# ================= b) 탐색·추세·모양 =================
def stage_explore(args=None):
    seeds = parse_seeds(A.seeds)
    skips = skip_words()
    seeds = [s for s in seeds if not is_skipped(nospace(s["word"]), skips)]
    if not seeds:
        raise SystemExit("씨앗이 없습니다(--seeds 파일 확인).")
    raw = rs.raw_cache()
    api = new_api()
    for sd in seeds:
        ns = nospace(sd["word"])
        if ns in raw["expand"]:
            continue
        if api.usage["searchad"] >= A.searchad_limit:
            print(f"[멈춤] 검색광고 호출 한도({A.searchad_limit}회)에 닿음 — 같은 명령으로 이어서 하세요.")
            break
        lst = api.searchad([sd["word"]])
        if lst is None:
            log(f"  [실패] 검색광고 {sd['word']}")
            continue
        raw["expand"][ns] = [{"kw": it.get("relKeyword"), "pc": api.cm.to_int(it.get("monthlyPcQcCnt")), "mo": api.cm.to_int(it.get("monthlyMobileQcCnt"))} for it in lst]
        api.save()
        rs.save_raw(raw)
    plan_kw = set(jload(D / "existing.json", {}).get("plan_keywords", []))
    for pth in (A.exclude_plan or []):
        for e in (jload(pth, {}) or {}).get("plans", []):
            plan_kw.add(nospace(e.get("keyword")))
    vmin = make_cfg()["volume_min"]
    cands, cen = {}, {}
    why = {"씨앗 낱말 없음(연관 없음)": 0, f"검색량 {vmin} 미만": 0, "제외 낱말(skip)": 0, "금지·구매처 낱말": 0, "이미 계획에 있는 키워드": 0,
           "같은 키워드가 다른 씨앗에서 이미 나옴": 0, f"씨앗별 상위 {PER_SEED_MAX}개 밖": 0}
    seen_all = 0
    for sd in seeds:
        ns = nospace(sd["word"])
        rows = [x for x in raw["expand"].get(ns, []) if x.get("kw")]
        self_vol = next(((x["pc"] + x["mo"]) for x in rows if nospace(x["kw"]) == ns), None)
        cen[ns] = {"word": sd["word"], "volume": self_vol}
        keep = []
        for x in rows:
            nk, vol = nospace(x["kw"]), x["pc"] + x["mo"]
            seen_all += 1
            if nk == ns:
                continue
            if not any(nospace(t) in nk for t in sd["tokens"]) and ns not in nk:
                why["씨앗 낱말 없음(연관 없음)"] += 1
            elif vol < vmin:
                why[f"검색량 {vmin} 미만"] += 1
            elif is_skipped(nk, skips):
                why["제외 낱말(skip)"] += 1
            elif any(w in nk for w in rs.DROP_WORDS + rs.BUY_WORDS):
                why["금지·구매처 낱말"] += 1
            elif nk in plan_kw:
                why["이미 계획에 있는 키워드"] += 1
            elif nk in cands:
                why["같은 키워드가 다른 씨앗에서 이미 나옴"] += 1
            else:
                keep.append((vol, x["kw"], nk))
        keep.sort(key=lambda t: -t[0])
        why[f"씨앗별 상위 {PER_SEED_MAX}개 밖"] += max(0, len(keep) - PER_SEED_MAX)
        for vol, kw, nk in keep[:PER_SEED_MAX]:
            cands[nk] = {"keyword": kw, "volume": vol, "src": "expand", "old_grade": "없음", "kw_type": None, "seed": sd["word"], "theme": sd["theme"],
                         "track": None, "in_ranking": False}
    for sd in seeds:
        ns = nospace(sd["word"])
        v = cen[ns]
        if (v["volume"] or 0) >= vmin and ns not in cands and not is_skipped(ns, skips):
            cands[ns] = {"keyword": v["word"], "volume": v["volume"], "src": "center", "old_grade": "없음", "kw_type": None, "seed": v["word"],
                         "theme": sd["theme"], "track": None, "in_ranking": False}
    # 꼭 넣을 글감(must): 검색량을 받아 후보에 넣는다(씨앗 탐색과 따로)
    musts = parse_must(A.must) if A.must else []
    for m in musts:
        nk = nospace(m["keyword"])
        if nk not in raw["expand"] and api.usage["searchad"] < A.searchad_limit:
            lst = api.searchad([m["keyword"]])
            if lst is not None:
                raw["expand"][nk] = [{"kw": it.get("relKeyword"), "pc": api.cm.to_int(it.get("monthlyPcQcCnt")), "mo": api.cm.to_int(it.get("monthlyMobileQcCnt"))} for it in lst]
                api.save()
                rs.save_raw(raw)
        row = next((x for x in raw["expand"].get(nk, []) if nospace(x.get("kw")) == nk), None)
        vol = (row["pc"] + row["mo"]) if row else 0
        m["volume"] = vol
        cands[nk] = {"keyword": m["keyword"], "volume": vol, "src": "must", "old_grade": "없음", "kw_type": None, "seed": m["keyword"], "theme": m["keyword"],
                     "track": None, "in_ranking": False}
    jsave(D / "cands.json", cands)
    jsave(D / "centers.json", cen)
    jsave(D / "must.json", musts)
    s = stats()
    s["explore"] = {"씨앗 수": len(seeds), "연관 키워드 총수": seen_all, "후보로 남음": len(cands), "탈락 사유": why}
    jsave(D / "stats.json", s)
    print(f"[explore] 씨앗 {len(seeds)}개 → 연관 키워드 {seen_all}개 → 후보 {len(cands)}개(꼭 넣을 글감 {len(musts)}개 포함). 탈락: {why}. 사용량 {api.usage}")


def stage_trend(args=None):
    rs.cmd_trend(None)


SHAPE_PROMPT = """아래는 생활 블로그(집 살림·생활 정보) 키워드 목록이다. 도구는 쓰지 말고 바로 답해라.
키워드마다 shape, type, money 를 정해라.
[shape] 다음 중 정확히 하나
 - 제철 물건: 계절·시기에 따라 찾는 물건 자체가 대상(전기방석, 손난로, 제설제, 워셔액, 롱패딩 같은 것)
 - 상시 물건: 계절과 상관없이 사는 물건이 대상
 - 급한 증상: 지금 막 생긴 문제(냄새, 고장, 동파, 김서림 같은 것)
 - 방법·순서: 하는 법·절차·점검 순서·관리법
 - 고르기·비교: 추천·비교·고르는 기준·종류
 - 브랜드·모델: 특정 브랜드나 모델명이 들어간 것
 - 기타
[type] 다음 중 정확히 하나
 - ok: 집·생활에서 쓰는 물건이나 방법을 알려는 일반 정보 키워드
 - brand: 브랜드나 모델명이 붙은 키워드
 - local: 지역·매장·장소 찾기(예: 강남 ○○, 근처 ○○)
 - service: 업체·서비스 맡기기(예: 수리 업체, 세탁소 가격, 대여)
 - buyplace: 파는 곳·구매처 찾기
 - offtopic: 집 생활 정보가 아닌 것(스포츠 장소·게임·연예·차량 개조 부품 같은 것)
[money] true/false: 브랜드·모델명이 붙었거나, 조건(가격대·용도·규격·크기·인원)이 붙어서 '무엇을 살까' 비교하는 키워드이면 true, 아니면 false.
반드시 아래 JSON 한 덩어리로만 답해라(설명 금지).
{{"items": [{{"id": "X1", "shape": "방법·순서", "type": "ok", "money": false}}]}}

[키워드 목록]
{lines}
"""


def stage_shape(args=None):
    cands = jload(D / "cands.json", {})
    raw = rs.raw_cache()
    done = jload(D / "shape.json", {}) or {}
    types = jload(D / "type.json", {}) or {}
    no_trend = [nk for nk in cands if nk not in raw["trend"]]
    todo = [nk for nk in cands if nk in raw["trend"] and nk not in done]
    stat_set("shape", "추세 값 없음(신호 없음)", len(no_trend))
    if not todo:
        print("[shape] 이미 모양 분류가 끝나 있습니다(Claude 호출 없음).")
        return
    if A.no_claude:
        stat_set("shape", "모양 분류 안 함(--no-claude)", len(todo))
        print(f"[shape] --no-claude: 모양 분류가 없는 후보 {len(todo)}개는 모양 없음으로 둡니다.")
        return
    ids = {f"X{k + 1}": nk for k, nk in enumerate(todo)}
    lines = "\n".join(f"{xid} | {cands[nk]['keyword']}" for xid, nk in ids.items())
    log(f"[shape] 키워드 {len(todo)}개 → Claude(haiku) 1회")
    env = pp.call_claude(SHAPE_PROMPT.format(lines=lines), "haiku")
    claude_used("shape", "haiku", env)
    for it in pp.extract_json(env.get("result", "")).get("items", []):
        nk = ids.get(it.get("id"))
        if nk and it.get("shape") in r1m.SHAPES:
            done[nk] = it["shape"]
            types[nk] = {"type": it.get("type"), "money": bool(it.get("money"))}
    jsave(D / "shape.json", done)
    jsave(D / "type.json", types)
    stat_set("shape", "분류 응답에 빠짐", len([nk for nk in todo if nk not in done]))
    print(f"[shape] 모양 분류 {len(done)}/{len(todo)} 저장")


# ================= signals =================
def build_sig():
    c = make_cfg()
    cands = jload(D / "cands.json", {})
    raw = rs.raw_cache()
    shapes = jload(D / "shape.json", {}) or {}
    types = jload(D / "type.json", {}) or {}
    cen = jload(D / "centers.json", {}) or {}
    sig = {}
    for nk, v in cands.items():
        s = rs.signals_for(nk, raw, shapes, c)
        s.update({"keyword": v["keyword"], "volume": v["volume"], "old_grade": v["old_grade"], "src": v["src"], "seed": v["seed"], "theme": v.get("theme") or v["seed"],
                  "kw_type": v.get("kw_type"), "type": (types.get(nk) or {}).get("type"), "money": (types.get(nk) or {}).get("money", False),
                  "cw": nospace(v["seed"])})
        sig[nk] = s
    centers = {ns: {**v, **{k: sig[ns][k] for k in ("ratio", "peak", "shape", "from_zero") if ns in sig}} for ns, v in cen.items()}
    return c, cands, sig, centers


def stage_signals(args=None):
    c, cands, sig, centers = build_sig()
    skips = skip_words()
    musts = jload(D / "must.json", []) or []
    must_nk = {nospace(m["keyword"]) for m in musts}
    reasons = {"제외 낱말(skip, 탐색 뒤 추가)": 0, "추세 값 없음": 0, "모양 분류 없음": 0, "제외 유형(지역·업체·구매처·무관)": 0}
    usable = {}
    for nk, s in sig.items():
        if nk in must_nk:
            continue
        if is_skipped(nk, skips):
            reasons["제외 낱말(skip, 탐색 뒤 추가)"] += 1
        elif not s["has_trend"]:
            reasons["추세 값 없음"] += 1
        elif not s["shape"]:
            reasons["모양 분류 없음"] += 1
        elif s["type"] in EXCLUDED_TYPES:
            reasons["제외 유형(지역·업체·구매처·무관)"] += 1
        else:
            usable[nk] = s
    ok_shapes = ("제철 물건", "상시 물건", "고르기·비교", "급한 증상", "방법·순서")
    nav, nr = [], {"브랜드(네이버 제외)": 0, "모양이 물건·방법이 아님(브랜드·기타)": 0, "시기 조건 아님": 0}
    for nk, s in usable.items():
        if s["type"] == "brand" or s["shape"] == "브랜드·모델":
            nr["브랜드(네이버 제외)"] += 1
        elif s["shape"] not in ok_shapes:
            nr["모양이 물건·방법이 아님(브랜드·기타)"] += 1
        elif not r1m.is_timed(s, c):
            nr["시기 조건 아님"] += 1
        else:
            nav.append(nk)
    nav.sort(key=lambda nk: (-r1m.naver_score(sig[nk], c), -(sig[nk].get("ratio") or 0), -sig[nk]["volume"], nk))
    per, nav_pool, over = {}, [], 0
    for nk in nav:
        sd = sig[nk]["seed"]
        if per.get(sd, 0) >= NAV_PER_SEED or len(nav_pool) >= NAV_POOL_MAX:
            over += 1
            continue
        per[sd] = per.get(sd, 0) + 1
        nav_pool.append(nk)
    nr[f"씨앗별 {NAV_PER_SEED}개·전체 {NAV_POOL_MAX}개 한도 밖"] = over
    wp = c["wordpress"]
    WP_ON = wp_enabled(c)         # 싼세계 편수 합이 0 이면 싼세계 풀(정보·수익)을 만들지 않는다
    tops = []
    for ns, v in centers.items():
        if (v.get("volume") or 0) >= wp["topic_volume_min"] and ns in usable:
            tops.append((r1m.topic_score(sig[ns], c), ns))
    tops.sort(key=lambda t: (-t[0], -(centers[t[1]].get("volume") or 0), t[1]))
    pk_ex = A.wp_peak_exclude        # 중심 낱말의 작년 정점까지 0~N주인 주제는 싼세계 정보글에서 제외(-1 이면 끔)
    tops = sorted(((sc + (A.wp_peak_bonus if (centers[ns].get("peak") or -1) >= 3 else 0), ns) for sc, ns in tops),
                  key=lambda t: (-t[0], -(centers[t[1]].get("volume") or 0), t[1]))      # 정점까지 3주 이상 남은 주제에 가점
    ex_topics = [ns for _s, ns in tops if pk_ex >= 0 and centers[ns].get("peak") is not None and centers[ns]["peak"] <= pk_ex]
    topics = [ns for _s, ns in tops if ns not in ex_topics]
    small = [ns for ns, v in centers.items() if (v.get("volume") or 0) < wp["topic_volume_min"]]
    wr = {f"작년 정점까지 0~{pk_ex}주라 뺀 주제 수": len(ex_topics), f"중심 낱말 검색량 {wp['topic_volume_min']:,} 미만 주제": 0, "브랜드(수익글 쪽으로)": 0, f"묶음당 {WP_PER_TOPIC_POOL}개 한도 밖": 0}
    wp_pool, cnt = [], {}
    for ns in (topics[:WP_TOPICS_MAX] if WP_ON else []):
        mem = [nk for nk, s in usable.items() if s["cw"] == ns and s["volume"] >= c["volume_min"]]
        mem.sort(key=lambda nk: (-r1m.naver_score(sig[nk], c), -sig[nk]["volume"], nk))
        for nk in mem:
            if sig[nk]["type"] == "brand" or sig[nk]["shape"] == "브랜드·모델":
                wr["브랜드(수익글 쪽으로)"] += 1
                continue
            if cnt.get(ns, 0) >= WP_PER_TOPIC_POOL:
                wr[f"묶음당 {WP_PER_TOPIC_POOL}개 한도 밖"] += 1
                continue
            cnt[ns] = cnt.get(ns, 0) + 1
            wp_pool.append(nk)
    wr[f"중심 낱말 검색량 {wp['topic_volume_min']:,} 미만 주제"] = sum(1 for nk, s in usable.items() if s["cw"] in small)
    mp = [nk for nk, s in usable.items() if s["money"] or s["type"] == "brand" or s["shape"] == "브랜드·모델"]
    mp.sort(key=lambda nk: (-r1m.naver_score(sig[nk], c), -sig[nk]["volume"], nk))
    money_pool = mp[:WP_MONEY_POOL] if WP_ON else []
    stat_set("signals", "추세·모양 없음 또는 제외 유형", reasons)
    stat_set("signals", "네이버 풀 탈락", nr)
    if WP_ON:
        stat_set("signals", "싼세계 정보 풀 탈락", wr)
        stat_set("signals", "풀 크기", {"네이버": len(nav_pool), "싼세계 정보": len(wp_pool), "싼세계 수익": len(money_pool), "사용 가능 후보": len(usable)})
    else:
        stat_set("signals", "풀 크기", {"네이버": len(nav_pool), "사용 가능 후보": len(usable)})
    must_pool = [{**m, "nk": nospace(m["keyword"])} for m in musts]
    keys = list(dict.fromkeys(nav_pool + wp_pool + money_pool + [m["nk"] for m in must_pool]))
    jsave(D / "pools.json", {"naver": nav_pool, "wp_info": wp_pool, "wp_money": money_pool, "must": must_pool, "wp_on": WP_ON,
                             "topics": [{"center": ns, "volume": centers[ns].get("volume"), "score": sc} for sc, ns in tops]})
    jsave(D / "r1c_signals.json", {"signals": sig, "centers": centers})
    if WP_ON:
        print(f"[signals] 풀: 네이버 {len(nav_pool)} · 싼세계 정보 {len(wp_pool)} · 싼세계 수익 {len(money_pool)} · 꼭 넣을 글감 {len(must_pool)} (사용 가능 {len(usable)}개)")
        print("  탈락:", reasons, nr, wr)
    else:
        print(f"[signals] 풀: 네이버 {len(nav_pool)} · 꼭 넣을 글감 {len(must_pool)} (사용 가능 {len(usable)}개) — 싼세계는 꺼져 있어 풀을 만들지 않음(--with-wp 로 켬)")
        print("  탈락:", reasons, nr)
    build_ranking(keys, sig, cands, c, centers)


def build_ranking(keys, sig, cands, c, centers):
    """기획 입력(ranking_r1.json). 상위 5개 글은 golden_cache 에 있으면 그것, 없으면 블로그 검색 상위 5개(제목·날짜)로 대신한다(본문은 안 엶)."""
    import html
    gc = jload(BASE / "pipeline" / "golden_cache.json", {}) or {}
    raw = rs.raw_cache()
    api = new_api()
    rows, cw_pool = [], {}
    for nk in keys:
        cw_pool.setdefault(sig[nk].get("cw") or "", []).append(cands[nk]["keyword"])
    n_api = n_cache = 0
    for nk in keys:
        v, s = cands[nk], sig[nk]
        kw = v["keyword"]
        base = {"keyword": kw, "seed": v.get("seed"), "score": {"demand": 0, "saturation": 0, "screen": 0, "fit": 0, "total": 0}, "monthly_total": v["volume"],
                "blog_docs": None, "saturation": None, "trend_growth": None, "trend_steady": None, "smart_blocks": [],
                "related_keywords": [k for k in cw_pool.get(s.get("cw") or "", []) if k != kw][:12], "kw_type": v.get("kw_type"), "platform_hint": None,
                "fingerprint": None, "track": None, "grade": None, "old_grade_text": v["old_grade"], "r1_src": v["src"]}
        g = ((gc.get(kw) or {}).get("v") or {})
        tp = g.get("top_posts")
        if tp and tp.get("posts"):
            base["golden"] = {"top_posts": tp}
            n_cache += 1
        else:
            sim = raw["sim"].get(nk)
            if not sim:
                try:
                    j = api.blog(query=kw, display=100, start=1, sort="sim")
                except bt.Budget:
                    j = None
                if j and "items" in j:
                    sim = {"total": j.get("total"), "items": [{"title": html.unescape(bt.TAG_RE.sub("", it.get("title", ""))), "date": str(it.get("postdate", "")),
                                                                 "blog": it.get("bloggername", "")} for it in j["items"][:100]]}
                    raw["sim"][nk] = sim
                    n_api += 1
                    if n_api % 20 == 0:
                        api.save()
                        rs.save_raw(raw)
            if sim:
                posts = []
                for r_, it in enumerate(sim["items"][:5], 1):
                    ds = str(it["date"])
                    d = bt.pdate(ds if "-" in ds else (ds[:4] + "-" + ds[4:6] + "-" + ds[6:8]))
                    posts.append({"rank": r_, "title": it["title"], "blog_name": it.get("blog"), "date": d.isoformat() if d else None,
                                  "age_days": (TODAY - d).days if d else None, "chars": None, "images": None, "influencer": False, "official_badge": False, "opened": False})
                base["golden"] = {"top_posts": {"posts": posts, "source": "블로그 검색 API 상위 5개(본문 안 엶)"}}
        base["r1"] = {k: s.get(k) for k in ("ratio", "peak", "shape", "from_zero", "cw", "volume", "old_grade")}
        cwv = centers.get(s.get("cw") or "") or {}
        base["r1"]["cw_volume"] = cwv.get("volume")
        base["r1"]["cw_sig"] = {k: cwv.get(k) for k in ("ratio", "peak", "shape", "from_zero")}
        rows.append(base)
    api.save()
    rs.save_raw(raw)
    jsave(D / "ranking_r1.json", {"generated_at": TODAY.isoformat(), "mode": "golden", "r1": True, "ranking": rows})
    print(f"  ranking_r1.json {len(rows)}행 저장 (상위 글: 캐시 {n_cache} · 블로그 검색 새로 {n_api}). 사용량 {api.usage}")


# ================= c) 중복 판정 =================
JUDGE_PROMPT = """너는 생활 살림 블로그의 편집장이다. 새로 쓸 글이 같은 플랫폼에 이미 쓴 글과 겹치는지 판정한다. 도구는 쓰지 말고 바로 답해라.

[판정 기준]
- 같은 플랫폼의 글(existing)과만 비교한다. existing 은 그 항목의 플랫폼(naver=네이버 블로그, wordpress=싼세계)에 이미 있는 글 제목이다. 다른 플랫폼에 같은 주제가 있는 것은 중복이 아니다.
- 같은 물건(또는 같은 문제)을 같은 질문(고르기·붙이기·없애기·설치하기·전기요금·보관법·세탁법 같은 것)으로 다루면 '중복'.
- 물건이 같아도 질문이 다르면 '각도 다름'. 장소나 쓰는 사람이 다르고, 그 말로 따로 검색할 만하면 '각도 다름'.
- 애매하면 중복으로 보지 마라. 제목만 봐서 같은 질문이라고 확실할 때만 '중복'이다.
- 같은 물건의 다른 이름도 같은 물건이다(방풍막=방풍비닐, 전기요=전기장판 등).
- 항목끼리는 비교하지 않는다(계획 안의 겹침은 따로 처리한다).

[입력] 항목마다 id, platform, 종류(계획=이미 확정된 글, 후보=아직 제목 없는 키워드), text, existing 목록이 있다.
- 계획 종류의 text 는 확정 제목이다. 각도 설명이 붙어 있으면 그 각도 기준으로 판정한다.
- 후보 종류의 text 는 검색 키워드다. 그 키워드로 검색하는 사람의 일반적인 질문을 기준으로 판정한다.

[출력] 항목마다 한 줄. JSON 만 출력한다. 항목 순서와 id 를 그대로 지킨다.
{{"results": [{{"id": "N1", "verdict": "중복 또는 각도 다름 또는 겹침 없음", "object": "이 글이 다루는 구체적인 물건(물건이 없으면 문제) 이름 한 낱말", "closest": "가장 가까운 existing 제목 하나(제목 그대로, 정말 없으면 빈 문자열)", "reason": "한 줄(30자 안팎)", "unfit": false}}]}}
- verdict: existing 중 하나라도 같은 질문이면 '중복', 겹치는 글은 있지만 질문이 다 다르면 '각도 다름', 비슷한 글이 아예 없으면 '겹침 없음'.
- object: 같은 물건은 모든 항목에서 똑같은 이름으로 적어라(예: 핫팩 파우치와 핫팩은 다른 물건이면 각각 따로, 같은 물건의 다른 표기는 하나로). 띄어쓰기 없이 적는다.
- unfit: 집 살림·생활과 무관하거나 지역 업체·구매처 찾기이면 true.

[항목]
{items}
"""


def related(text_ns, tokens, ex, k=6, extra=3):
    cand = []
    for x in ex:
        ns = nospace(x["title"])
        direct = any(t in ns for t in tokens if len(t) >= 2)
        cand.append((direct, r1b.sim(text_ns, ns), x))
    d = sorted([c for c in cand if c[0]], key=lambda c: -c[1])[:k]
    o = sorted([c for c in cand if not c[0]], key=lambda c: -c[1])[:extra]
    out, seen = [], set()
    for _d, _s, x in d + o:
        if x["title"] not in seen:
            seen.add(x["title"])
            out.append(x["title"])
    return out


def sync_alias():
    """결과 폴더 복사본 target_alias 에만 있는 이름표를 운영 target_alias.json 에 더한다(운영 파일은 백업 후, 새 이름표만 추가·기존 값은 안 바꿈)."""
    op = BASE / "pipeline" / "target_alias.json"
    cp = D / "index" / "target_alias.json"
    if not (op.exists() and cp.exists()):
        return 0
    o, cdat = jload(op), jload(cp)
    add = {k: v for k, v in (cdat.get("alias") or {}).items() if k not in o["alias"]}
    if not add:
        return 0
    shutil.copy2(op, BASE / "pipeline" / f"target_alias.backup_before_alias_sync_{datetime.now():%Y%m%d_%H%M%S}.json")
    o["alias"].update(add)
    o["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    jsave(op, o)
    return len(add)


def judge_items_wanted():
    ex = jload(D / "existing.json")
    pools = jload(D / "pools.json")
    sig = jload(D / "r1c_signals.json")["signals"]
    want = []
    wp_on = bool(pools.get("wp_on", True))     # 값이 없는 옛 풀은 켜짐(예전과 같음)
    for nk in pools["naver"]:
        want.append(("naver", "N", sig[nk]["keyword"], sig[nk]["keyword"], sig[nk]["seed"]))
    for nk in (pools["wp_info"] if wp_on else []):
        want.append(("wordpress", "W", sig[nk]["keyword"], sig[nk]["keyword"], sig[nk]["seed"]))
    for nk in (pools["wp_money"] if wp_on else []):
        want.append(("wordpress", "M", sig[nk]["keyword"], sig[nk]["keyword"], sig[nk]["seed"]))
    for m in pools["must"]:
        if not wp_on and m["platform"] == "wordpress":
            continue                            # 싼세계가 꺼져 있으면 싼세계 꼭 넣을 글감도 판정하지 않음
        txt = m["keyword"] + (f" — 이번 글의 각도: {m['note']}" if m.get("note") else "")
        want.append((m["platform"], "X", m["keyword"], txt, m["keyword"]))
    return ex, want


def verdict_map():
    """저장된 판정 → {(플랫폼, 키워드): 결과}. r1c 가 저장한 판정 파일도 같은 모양이라 읽는다."""
    jr = jload(D / "judge_result.json")
    if not jr:
        return {}
    res = {r.get("id"): r for r in jr.get("results", [])}
    out = {}
    for iid, m in (jr.get("meta") or {}).items():
        if iid in res:
            out[(m["platform"], nospace(m["keyword"]))] = res[iid]
    return out


def stage_judge(args=None):
    n_alias = sync_alias()
    if n_alias:
        print(f"[judge] 운영 target_alias.json 에 새 이름표 {n_alias}개 반영(백업함)")
    ex, want = judge_items_wanted()
    V = verdict_map()
    todo = [w for w in want if (w[0], nospace(w[2])) not in V]
    if not todo:
        print(f"[judge] 저장된 판정이 모두 있어 다시 묻지 않음({len(want)}개)")
        return
    if A.no_claude:
        stat_set("judge", "판정 안 함(--no-claude)", len(todo))
        print(f"[judge] --no-claude: 판정이 없는 항목 {len(todo)}개는 건너뜀")
        return
    alias = jload(D / "index" / "target_alias.json")["alias"]
    vg = r1b.vocab_groups(alias)
    jr = jload(D / "judge_result.json") or {"calls": 0, "meta": {}, "items": [], "results": []}
    items, meta = [], {}
    base_n = len(jr["meta"])
    for i, (plat, pre, kw, text, seed) in enumerate(todo, 1):
        _t, _hit = r1b.terms_of(kw, vg)
        toks = set(_t) | {nospace(w) for w in kw.split() if len(w) >= 2} | {nospace(kw)} | {nospace(seed)}
        toks = {t for t in toks if len(t) >= 2}
        iid = f"K{base_n + i}"
        items.append({"id": iid, "platform": plat, "kind": "후보", "text": text, "existing": related(nospace(kw), toks, ex[plat])})
        meta[iid] = {"keyword": kw, "platform": plat, "kind": "후보"}
    prompt = JUDGE_PROMPT.format(items=json.dumps(items, ensure_ascii=False, separators=(",", ":")))
    write_text_retry(D / "judge_prompt.txt", prompt)
    log(f"[judge] 항목 {len(items)}개, 질문 {len(prompt)}자 → Claude(sonnet) 1회")
    env = res = err = None
    for attempt in range(2):
        try:
            env = pp.call_claude(prompt, "sonnet", "1")
            res = pp.extract_json(env.get("result", ""))["results"]
            break
        except pp.UsageLimitError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            err = e
            log(f"  실패({type(e).__name__}): {str(e).splitlines()[0][:150]}")
    if res is None:
        raise SystemExit(f"판정 호출 실패: {err}")
    claude_used("judge", "sonnet", env)
    got = {r.get("id") for r in res}
    jr["calls"] = int(jr.get("calls") or 0) + 1
    jr["made_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    jr["cost_usd"] = round(float(jr.get("cost_usd") or 0) + float(env.get("total_cost_usd") or 0), 4)
    jr["meta"].update(meta)
    jr["items"] += items
    jr["results"] += res
    jr["missing"] = [it["id"] for it in items if it["id"] not in got]
    jsave(D / "judge_result.json", jr)
    log(f"[judge] 저장: judge_result.json (빠진 항목 {len(jr['missing'])}개)")


# ================= d) 채우기 =================
def day_list():
    return [(WEEK + timedelta(days=i)).isoformat() for i in range(7)]


def peak_key(e):
    p = (e.get("r1") or {}).get("peak")
    r = (e.get("r1") or {}).get("ratio")
    return (99 if p is None else p, -(r or 0))


def assign_days(picks):
    """네이버 하루 3편·싼세계 하루 2편. 이번 주가 정점인 글(작년 정점까지 0주)을 앞 날짜에 둔다. 싼세계는 수익글을 날짜에 고르게 섞는다."""
    days = day_list()
    sched = [p for p in picks if p["role"] == "scheduled"]
    nv = sorted([p for p in sched if p["platform"] == "naver"], key=peak_key)
    wi = sorted([p for p in sched if p["platform"] == "wordpress" and p["track"] == "info"], key=peak_key)
    wm = sorted([p for p in sched if p["platform"] == "wordpress" and p["track"] == "money"], key=peak_key)
    order = []
    if wm:
        step = (len(wi) + len(wm)) / len(wm)
        pos = {int(step * (k + 0.5)) for k in range(len(wm))}
        ii = mi = 0
        for i in range(len(wi) + len(wm)):
            if (i in pos and mi < len(wm)) or ii >= len(wi):
                order.append(wm[mi])
                mi += 1
            else:
                order.append(wi[ii])
                ii += 1
    else:
        order = wi
    for plat, lst in (("naver", nv), ("wordpress", order)):
        for i, p in enumerate(lst):
            p["publish_date"] = days[min(i // PER_DAY[plat], 6)]


def stage_fill(args=None):
    V = verdict_map()
    c = make_cfg()
    pools = jload(D / "pools.json")
    sig = jload(D / "r1c_signals.json")["signals"]
    if not V:
        print("[fill] 저장된 중복 판정이 없어 채우기를 건너뜁니다(Claude 판정이 먼저 필요).")
        return
    nvc, wpc = c["naver"], c["wordpress"]
    NAV_N, NAV_R, WP_I, WP_M, WP_R = nvc["count"], nvc["reserve"], wpc["info"], wpc["money"], wpc["reserve"]
    taken = {"naver": [], "wordpress": []}
    taken_kw = {"naver": set(), "wordpress": set()}
    drops = {"중복 판정": [], "집 살림과 무관": [], "같은 물건이 이미 있음(주 1편)": [], f"같은 갈래 네이버 {A.theme_cap}편 한도": [],
             f"같은 묶음 {WP_PER_TOPIC_FILL}개 한도": [], "싼세계 예비 갈래 1편 한도": [],
             "판정 없음": [], "꼭 넣을 글감 중복·무관": []}
    picks = []

    def verdict(plat, kw):
        return V.get((plat, nospace(kw)))

    def judged(plat, kw, drop_key=None):
        v = verdict(plat, kw)
        if not v:
            drops["판정 없음"].append(kw)
            return None
        if v.get("unfit"):
            drops[drop_key or "집 살림과 무관"].append(kw)
            return None
        if v.get("verdict") == "중복":
            drops[drop_key or "중복 판정"].append(kw)
            return None
        key = norm_obj(v.get("object") or kw)
        if key in taken[plat] or nospace(kw) in taken_kw[plat]:
            drops["같은 물건이 이미 있음(주 1편)"].append(kw)
            return None
        return key

    def take(plat, kw, key):
        taken[plat].append(key)
        taken_kw[plat].add(nospace(kw))

    def mk(nk, plat, role, track, style, **kw_):
        s = sig[nk]
        return {"nk": nk, "keyword": s["keyword"], "platform": plat, "role": role, "track": track, "style": style, "theme": s.get("theme") or s["seed"],
                "topic": s.get("cw"), "r1": {k: s.get(k) for k in ("ratio", "peak", "shape", "from_zero")}, "must": False, "note": None, **kw_}

    # 꼭 넣을 글감: 먼저 자리를 잡는다(중복 판정은 똑같이 받는다)
    cnt = {"nv": 0, "wi": 0, "wm": 0}
    wp_on = bool(pools.get("wp_on", True))
    if not wp_on:
        drops["싼세계 꺼짐(--with-wp 로 켬)"] = []
    for m in pools["must"]:
        nk = m["nk"]
        if not wp_on and m["platform"] == "wordpress":
            drops["싼세계 꺼짐(--with-wp 로 켬)"].append(m["keyword"])
            continue
        if nk not in sig:
            drops["꼭 넣을 글감 중복·무관"].append(m["keyword"] + "(신호 없음)")
            continue
        key = judged(m["platform"], m["keyword"], "꼭 넣을 글감 중복·무관")
        v = verdict(m["platform"], m["keyword"])
        if key is None and v is not None:
            continue
        if key is None:
            key = norm_obj(m["keyword"])
        take(m["platform"], m["keyword"], key)
        p = mk(nk, m["platform"], "scheduled", m["track"] if m["platform"] == "wordpress" else "info", "naver" if m["platform"] == "naver" else "wordpress",
               must=True, note=m.get("note") or None, judged=bool(v))
        picks.append(p)
        if m["platform"] == "naver":
            cnt["nv"] += 1
        else:
            cnt["wi" if p["track"] == "info" else "wm"] += 1
    nav_need, nav_res_need = max(0, NAV_N - cnt["nv"]), NAV_R
    theme_n = {}
    n_s = n_r = 0
    for nk in pools["naver"]:
        if n_s >= nav_need and n_r >= nav_res_need:
            break
        th = sig[nk].get("theme") or sig[nk]["seed"]
        if theme_n.get(th, 0) >= A.theme_cap:
            drops[f"같은 갈래 네이버 {A.theme_cap}편 한도"].append(sig[nk]["keyword"])
            continue
        key = judged("naver", sig[nk]["keyword"])
        if key is None:
            continue
        theme_n[th] = theme_n.get(th, 0) + 1
        take("naver", sig[nk]["keyword"], key)
        role = "scheduled" if n_s < nav_need else "reserve"
        n_s += role == "scheduled"
        n_r += role == "reserve"
        picks.append(mk(nk, "naver", role, "info", "naver"))
    info_need, money_need = max(0, WP_I - cnt["wi"]), max(0, WP_M - cnt["wm"])
    per_topic, n_i = {}, 0
    for nk in pools["wp_info"]:
        if n_i >= info_need:
            break
        cw = sig[nk]["cw"]
        if per_topic.get(cw, 0) >= WP_PER_TOPIC_FILL:
            drops[f"같은 묶음 {WP_PER_TOPIC_FILL}개 한도"].append(sig[nk]["keyword"])
            continue
        key = judged("wordpress", sig[nk]["keyword"])
        if key is None:
            continue
        per_topic[cw] = per_topic.get(cw, 0) + 1
        take("wordpress", sig[nk]["keyword"], key)
        picks.append(mk(nk, "wordpress", "scheduled", "info", "wordpress"))
        n_i += 1
    n_m = 0
    for nk in pools["wp_money"]:
        if n_m >= money_need:
            break
        key = judged("wordpress", sig[nk]["keyword"])
        if key is None:
            continue
        take("wordpress", sig[nk]["keyword"], key)
        picks.append(mk(nk, "wordpress", "scheduled", "money", "wordpress", note=wpc.get("money_note")))
        n_m += 1
    have = {p["nk"] for p in picks}
    res_theme, n_wr = {}, 0
    for lst, track in ((pools["wp_info"], "info"), (pools["wp_money"], "money")):
        for nk in lst:
            if n_wr >= WP_R:
                break
            if nk in have:
                continue
            th = sig[nk].get("theme") or sig[nk]["seed"]
            if res_theme.get(th, 0) >= 1:
                drops["싼세계 예비 갈래 1편 한도"].append(sig[nk]["keyword"])
                continue
            key = judged("wordpress", sig[nk]["keyword"])
            if key is None:
                continue
            res_theme[th] = res_theme.get(th, 0) + 1
            take("wordpress", sig[nk]["keyword"], key)
            picks.append(mk(nk, "wordpress", "reserve", track, "wordpress", note=wpc.get("money_note") if track == "money" else None))
            have.add(nk)
            n_wr += 1
    assign_days(picks)
    cnt_now = lambda plat, role, track=None: sum(1 for p in picks if p["platform"] == plat and p["role"] == role and (track is None or p["track"] == track))
    shortage = {"naver_sched": max(0, NAV_N - cnt_now("naver", "scheduled")), "naver_reserve": max(0, NAV_R - cnt_now("naver", "reserve")),
                "wp_info": max(0, WP_I - cnt_now("wordpress", "scheduled", "info")), "wp_money": max(0, WP_M - cnt_now("wordpress", "scheduled", "money")),
                "wp_reserve": max(0, WP_R - cnt_now("wordpress", "reserve"))}
    jsave(D / "fill.json", {"picks": picks, "dropped": drops, "shortage": shortage,
                            "targets": {"naver": NAV_N, "naver_reserve": NAV_R, "wp_info": WP_I, "wp_money": WP_M, "wp_reserve": WP_R}})
    s = stats()
    s["fill"] = {k: len(v) for k, v in drops.items()}
    jsave(D / "stats.json", s)
    if wp_on:
        print("[fill] 채움: 네이버 일정", cnt_now("naver", "scheduled"), "예비", cnt_now("naver", "reserve"), "· 싼세계 정보", cnt_now("wordpress", "scheduled", "info"),
              "수익", cnt_now("wordpress", "scheduled", "money"), "예비", cnt_now("wordpress", "reserve"))
    else:
        print("[fill] 채움: 네이버 일정", cnt_now("naver", "scheduled"), "예비", cnt_now("naver", "reserve"), "· 싼세계 꺼짐")
    print("  빠진 이유:", {k: len(v) for k, v in drops.items()}, "/ 부족:", shortage)


# ================= e·f) 기획·제목·제품 필요·카테고리 =================
EXTRA_GUIDE = """
[추가 요청 — 모든 plan 항목에 아래 두 칸도 넣어라]
- product_need: {{"need": true 또는 false, "count": 글에서 소개·링크할 제품 수(0~5 정수), "condition": "찾을 조건 한 줄(예: 세탁기에 돌려도 되는 2만 원 이하 제품)"}}.
  제품이 필요 없는 글(순수 방법·정보)은 need false, count 0, condition 은 빈 문자열. 제품명·가격·링크는 절대 만들지 마라.
- category: 다음 10개 중 이 글에 맞는 것 하나(맞는 것이 없으면 빈 문자열): {cats}
"""


def new_item(p, rk, w):
    row = rk[p["keyword"]]
    it = {k: row.get(k) for k in ("keyword", "seed", "score", "monthly_total", "saturation", "trend_growth", "trend_steady", "smart_blocks", "related_keywords",
                                  "kw_type", "platform_hint", "fingerprint", "grade", "golden", "r1")}
    d = p.get("publish_date")
    it.update(platform=p["platform"], role=p["role"], track=p["track"], r1_style=p["style"], publish_date=d,
              weekday=pp.WEEKDAYS[date.fromisoformat(d).weekday()] if d else None, found_date=TODAY.isoformat(), from_bank=False)
    pp.attach_clicks([it], w["click"])
    it["timing"] = r1m.label_of(row["r1"], w["r1"])
    notes = []
    if p.get("note"):
        notes.append(("이번 글의 각도(반드시 지킬 것): " if p.get("must") else "") + p["note"])
    if p["platform"] == "wordpress" and p["role"] == "scheduled" and p["track"] == "info":
        mates = [q["keyword"] for q in A_PICKS if q is not p and q["platform"] == "wordpress" and q.get("topic") == p.get("topic")]
        notes.append("같은 묶음의 다른 글과는 장소가 아니라 '질문'(고르기·방법·비교·관리·후기 등)이 달라야 한다." + (f" 같은 묶음 글: {', '.join(mates)}" if mates else ""))
    if notes:
        it["r1_note"] = " / ".join(notes)
    return it


A_PICKS = []


def items_ordered(fill, rk, w):
    global A_PICKS
    A_PICKS = fill["picks"]
    order = {("naver", "scheduled"): 0, ("wordpress", "scheduled"): 1, ("naver", "reserve"): 2, ("wordpress", "reserve"): 3}
    picks = sorted(fill["picks"], key=lambda p: (order[(p["platform"], p["role"])], p.get("publish_date") or "9", p["keyword"]))
    out, seen = [], set()
    for p in picks:
        it = new_item(p, rk, w)
        if it["keyword"] in seen:         # 같은 키워드가 두 플랫폼에 들어간 경우: 제목 저장소가 키워드 단위라 뒤쪽 것은 띄어쓰기를 넣은 이름표로 따로 다룬다
            it["_orig"] = it["keyword"]
            it["keyword"] = it["keyword"][:2] + " " + it["keyword"][2:]
        seen.add(it["keyword"])
        out.append(it)
    return out


def stage_plan(args=None):
    if (D / "plan_step.json").exists() and not A.force:
        print("[plan] 저장된 기획(plan_step.json)이 있어 다시 받지 않음")
        return
    fill = jload(D / "fill.json")
    if not fill:
        print("[plan] fill.json 이 없어 기획을 건너뜁니다.")
        return
    rk = {r["keyword"]: r for r in jload(D / "ranking_r1.json")["ranking"]}
    w = load_weekly()
    items = items_ordered(fill, rk, w)
    os.environ["LOCALAPP_INDEX_DIR"] = str(D / "index")
    title_rules = pp.ttl.load_rules(BASE / "tools" / "filter.json")
    guide = pp.title_guide(title_rules["forbidden_words"], pp.ttl.style_samples(pp.ttl.own_titles()))
    title_rules["r1_wp"] = w["r1"]["wordpress"]
    guide += pp.R1_WP_TITLE_GUIDE.format(words=", ".join(w["r1"]["wordpress"]["title_search_words"][:12]))
    guide += EXTRA_GUIDE.format(cats=", ".join(push_plan.APP_CATEGORIES))
    half = (len(items) + 1) // 2
    chunks = [items[:half], items[half:]]
    problems, batches, claude_by_kw = [], [], {}
    for n, chunk in enumerate(chunks, 1):
        if not chunk:
            continue
        pth = D / f"plan_call{n}.json"
        if pth.exists() and not A.force:
            saved = jload(pth)
            for idx, p_ in enumerate(saved["plans"]):
                if idx < len(chunk):
                    p_["keyword"] = chunk[idx]["keyword"]
                claude_by_kw[p_.get("keyword")] = p_
            batches.append(saved["batch"])
            log(f"[plan] 기획 호출 {n}번은 저장된 결과를 씀")
            continue
        if A.no_claude:
            print(f"[plan] --no-claude: 저장된 기획 호출 {n}번 결과가 없어 기획을 건너뜁니다.")
            return
        log(f"[plan] 기획 호출 {n}번: {len(chunk)}편")
        plans, env, probs = pp.plan_batch(pp.build_summary(chunk, title_rules), guide)
        problems += probs
        if env is None:
            raise SystemExit("기획 호출 실패: " + "; ".join(probs))
        claude_used("plan", "sonnet", env)
        u = env.get("usage") or {}
        b = {"batch": n, "keywords": len(chunk), "total_cost_usd": env.get("total_cost_usd"), "num_turns": env.get("num_turns"),
             "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
        batches.append(b)
        for idx, p_ in enumerate(plans):
            if idx < len(chunk) and p_.get("keyword", "").replace(" ", "") == chunk[idx]["keyword"].replace(" ", ""):
                p_["keyword"] = chunk[idx]["keyword"]
            claude_by_kw[p_.get("keyword")] = p_
        jsave(pth, {"plans": plans, "batch": b})
    if A.no_claude and not (D / f"title_cache_{ymd()}.json").exists():
        print("[plan] --no-claude: 저장된 제목 결과가 없어 제목 단계를 건너뜁니다.")
        return
    title_res = pp.choose_titles(items, claude_by_kw, D, WEEK, title_rules, guide, batches, problems)
    ok = [b for b in batches if not b.get("failed")]
    jsave(D / "plan_step.json", {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "claude_by_kw": claude_by_kw, "title_res": title_res,
                                 "plan_calls": len([b for b in batches if b.get("batch") in (1, 2)]), "batches": batches, "problems": problems,
                                 "usage": {"calls": len(batches), "cost_usd": round(sum(b.get("total_cost_usd") or 0 for b in ok), 4)}})
    log(f"[plan] 저장: plan_step.json (제목 단계 포함 {len(batches)}회, 문제 {len(problems)}건)")
    for pr_ in problems:
        log(f"  [주의] {pr_}")


# ================= g) 제목과 본문 설계 맞추기 =================
CHECK_PROMPT = """너는 블로그 편집장이다. 글마다 '확정 제목이 독자에게 약속하는 질문'에 본문 설계(소제목과 요점)와 검색 의도가 실제로 답하는지 검사한다. 도구는 쓰지 말고 바로 답해라.
- ok: 소제목들이 제목이 묻는 질문에 차례로 답한다.
- mismatch: 설계가 제목과 다른 질문(예: 제목은 '땀이 차는데 괜찮을까'인데 설계는 사이즈 고르기)에 답하거나, 제목이 약속한 핵심 내용이 소제목에 없다.
애매하면 ok. JSON 한 덩어리만 출력한다.
{{"results": [{{"id": "C1", "verdict": "ok 또는 mismatch", "reason": "한 줄(30자 안팎)"}}]}}

[글 목록]
{items}
"""

REDO_PROMPT = """너는 블로그 기획자다. 아래 글들은 본문 설계가 확정 제목과 맞지 않는다. 글마다 '제목이 묻는 질문에 답하는' 본문 설계와 검색 의도를 새로 써라. 도구는 쓰지 말고 바로 답해라.
- search_intent: 검색하는 사람이 알고 싶은 것 한 문장('~알고 싶다'로 끝냄). 제목의 질문과 같은 것이어야 한다.
- outline: 소제목 4~5개. 각 항목 {{"heading": 소제목, "points": [요점 1~3개]}}. 소제목은 제목의 질문에 답하는 순서로 이어진다.
- product_need: {{"need": true/false, "count": 0~5 정수, "condition": "찾을 조건 한 줄", }} — 새 설계에 필요한 제품만. 제품명·가격·링크는 만들지 마라. 필요 없으면 need false, count 0, condition ''.
- 새로 만든 숫자·경험을 지어내지 마라. 확정 제목은 바꾸지 않는다.
JSON 한 덩어리만 출력한다.
{{"results": [{{"id": "C1", "search_intent": "...", "outline": [{{"heading": "...", "points": ["..."]}}], "product_need": {{"need": true, "count": 2, "condition": "..."}}}}]}}

[글 목록]
{items}
"""


def entry_brief(i, e):
    return {"id": f"C{i}", "platform": e["_plat"], "keyword": e["keyword"], "final_title": e["final_title"], "search_intent": e.get("search_intent"),
            "outline": e.get("outline") or [], "track": e.get("track"), "product_need": e.get("product_need")}


def assemble_entries():
    """계획 입력 + 기획 결과 → 글 항목 목록(점검·결과물 전 단계)."""
    fill, ps = jload(D / "fill.json"), jload(D / "plan_step.json")
    rk = {r["keyword"]: r for r in jload(D / "ranking_r1.json")["ranking"]}
    w = load_weekly()
    items = items_ordered(fill, rk, w)
    tr, cbk = ps["title_res"], ps["claude_by_kw"]
    V = verdict_map()
    ex = jload(D / "existing.json")
    ex_ns = {p: [(nospace(x["title"]), x["title"]) for x in ex[p]] for p in ("naver", "wordpress")}
    entries = []
    for it in items:
        kw = it["keyword"]
        orig = it.get("_orig") or kw
        e = {k: it.get(k) for k in ("platform", "role", "publish_date", "weekday", "keyword", "seed", "track", "r1", "r1_style", "expected_clicks", "clicks_basis",
                                    "timing", "monthly_total", "saturation", "trend_growth", "trend_steady", "smart_blocks", "related_keywords")}
        e["top_posts"] = pp.top_posts_brief(it)
        c = cbk.get(kw) or {}
        for k_ in ("search_intent", "gaps", "outline", "experience_slots", "beat_top", "target_chars", "target_images"):
            e[k_] = c.get(k_)
        e.update({k_: v_ for k_, v_ in (tr.get(kw) or {}).items() if k_ != "retried"})
        e["keyword"] = orig
        plat = it["platform"]
        e["_plat"] = plat
        v = V.get((plat, nospace(orig))) or {}
        e["judge"] = {k: v.get(k) for k in ("verdict", "object", "closest", "reason")}
        pn = c.get("product_need") if isinstance(c.get("product_need"), dict) else {}
        e["product_need"] = norm_need(pn, plat)
        cat = (c.get("category") or "").strip()
        e["category"] = cat if cat in push_plan.APP_CATEGORIES else ""
        best = max(((r1b.sim(e.get("final_title") or "", ns), t) for ns, t in ex_ns[plat]), default=(0, ""))
        e["title_sim"] = [round(best[0], 2), best[1]]
        e["planned_type"] = push_plan.planned_type({"platform": plat, "keyword": orig, "track": e["track"], "kw_type": None, "r1": e["r1"]})
        entries.append(e)
    return entries


def norm_need(pn, plat):
    try:
        cnt = int(pn.get("count") or 0)
    except (TypeError, ValueError):
        cnt = 0
    need = bool(pn.get("need")) and cnt > 0
    kind = "필요 없음" if not need else ("네이버쇼핑" if plat == "naver" else "쿠팡파트너스")
    return {"need": need, "count": cnt if need else 0, "condition": " ".join(str(pn.get("condition") or "").split()) if need else "", "link_kind": kind}


def valid_outline(o):
    return isinstance(o, list) and 4 <= len(o) <= 5 and all(isinstance(x, dict) and x.get("heading") and isinstance(x.get("points"), list) and x["points"] for x in o)


def ask(prompt, model, stage):
    for attempt in range(2):
        try:
            env = pp.call_claude(prompt, model, "1")
            res = pp.extract_json(env.get("result", ""))["results"]
            claude_used(stage, model, env)
            return res
        except pp.UsageLimitError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            log(f"  실패({type(e).__name__}): {str(e).splitlines()[0][:150]}")
    return None


def stage_check(args=None):
    if not (D / "plan_step.json").exists():
        print("[check] 기획 결과(plan_step.json)가 없어 건너뜁니다.")
        return
    if (D / "check_result.json").exists() and not A.force:
        print("[check] 저장된 검사 결과가 있어 다시 묻지 않음")
        return
    if A.no_claude:
        print("[check] --no-claude: 제목-본문 설계 검사를 건너뜁니다(검사 안 함으로 표시).")
        return
    entries = assemble_entries()
    ids = {f"C{i}": e for i, e in enumerate(entries, 1)}
    briefs = [entry_brief(i, e) for i, e in enumerate(entries, 1)]
    res = ask(CHECK_PROMPT.format(items=json.dumps(briefs, ensure_ascii=False, separators=(",", ":"))), "sonnet", "check")
    if res is None:
        raise SystemExit("제목-본문 설계 검사 호출 실패")
    verdict = {r.get("id"): r for r in res}
    bad = [i for i in ids if (verdict.get(i) or {}).get("verdict") == "mismatch"]
    redone, failed = {}, []
    if bad:
        res2 = ask(REDO_PROMPT.format(items=json.dumps([b for b in briefs if b["id"] in bad], ensure_ascii=False, separators=(",", ":"))), "sonnet", "check_redo")
        for r in (res2 or []):
            if r.get("id") in bad and valid_outline(r.get("outline")) and str(r.get("search_intent") or "").strip():
                redone[r["id"]] = {"search_intent": " ".join(str(r["search_intent"]).split()), "outline": [{"heading": x["heading"], "points": [str(p) for p in x["points"]]} for x in r["outline"]],
                                   "product_need": norm_need(r.get("product_need") or {}, ids[r["id"]]["_plat"])}
        failed = [i for i in bad if i not in redone]
    jsave(D / "check_result.json", {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "calls": 1 + (1 if bad else 0),
                                    "by_key": {f"{ids[i]['_plat']}|{ids[i]['keyword']}": {"verdict": (verdict.get(i) or {}).get("verdict"), "reason": (verdict.get(i) or {}).get("reason")} for i in ids},
                                    "redone": {f"{ids[i]['_plat']}|{ids[i]['keyword']}": v for i, v in redone.items()},
                                    "redo_failed": [f"{ids[i]['_plat']}|{ids[i]['keyword']}" for i in failed]})
    print(f"[check] 검사 {len(ids)}편 → 어긋남 {len(bad)}편, 설계 다시 받음 {len(redone)}편, 다시 받기 실패 {len(failed)}편")


# ================= i) 결과물 =================
def stage_build(args=None):
    if not (D / "plan_step.json").exists():
        print("[build] 기획 결과(plan_step.json)가 없어 결과물(plan·report)을 만들지 않습니다(--no-claude 이거나 앞 단계 미완).")
        return
    fill = jload(D / "fill.json")
    ps = jload(D / "plan_step.json")
    chk = jload(D / "check_result.json", {}) or {}
    entries = assemble_entries()
    redone_list = []
    for e in entries:
        k = f"{e['_plat']}|{e['keyword']}"
        e["check"] = (chk.get("by_key") or {}).get(k) or {"verdict": "검사 안 함", "reason": ""}
        if k in (chk.get("redone") or {}):
            r = chk["redone"][k]
            e["search_intent"], e["outline"], e["product_need"] = r["search_intent"], r["outline"], r["product_need"]
            e["outline_redone"] = True
            redone_list.append(f"{e['_plat']} {e['keyword']}")
    issues = []
    seen = {"naver": {}, "wordpress": {}}
    for e in entries:
        p = e["_plat"]
        ok = norm_obj(e["judge"].get("object") or e["keyword"])
        if ok in seen[p] and e["role"] == "scheduled":
            issues.append(f"같은 물건 두 편({p}): {seen[p][ok]} / {e['keyword']} (물건 '{e['judge'].get('object')}')")
        elif e["role"] == "scheduled":
            seen[p][ok] = e["keyword"]
        if e["title_sim"][0] >= 0.6:
            issues.append(f"제목이 같은 플랫폼 기존 글과 매우 비슷({e['title_sim'][0]:.2f}): {e.get('final_title')} ≈ {e['title_sim'][1]}")
        if not valid_outline(e.get("outline")):
            issues.append(f"본문 설계 소제목 수/형식 이상: {p} {e['keyword']}")
        if e["check"]["verdict"] == "mismatch" and not e.get("outline_redone"):
            issues.append(f"제목-본문 설계가 어긋난 채 남음(다시 받기 실패): {p} {e['keyword']}")
    for f_ in (chk.get("redo_failed") or []):
        pass
    sched_n = [e for e in entries if e["role"] == "scheduled" and e["_plat"] == "naver"]
    sched_w = [e for e in entries if e["role"] == "scheduled" and e["_plat"] == "wordpress"]
    res_n = [e for e in entries if e["role"] == "reserve" and e["_plat"] == "naver"]
    res_w = [e for e in entries if e["role"] == "reserve" and e["_plat"] == "wordpress"]
    rec = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "week": {"start": WEEK.isoformat(), "end": (WEEK + timedelta(days=6)).isoformat()}, "source": f"weekly_plan.py ({A.seeds})",
           "counts": {"naver": len(sched_n), "wordpress": len(sched_w), "reserve_naver": len(res_n), "reserve_wordpress": len(res_w)},
           "shortage": fill["shortage"], "dropped": fill["dropped"], "issues": issues, "problems": ps["problems"], "outline_redone": redone_list,
           "plans": [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]}
    jsave(D / f"plan_{ymd()}.json", rec)
    write_report(entries, rec, redone_list, issues)
    write_product_need(entries)
    if pools_wp_on():
        print(f"[build] 네이버 {len(sched_n)} / 싼세계 {len(sched_w)} / 예비 네이버 {len(res_n)} · 싼세계 {len(res_w)} / 점검 걸림 {len(issues)}건 / 설계 다시 받음 {len(redone_list)}편")
    else:
        print(f"[build] 네이버 {len(sched_n)} / 예비 네이버 {len(res_n)} (싼세계 꺼짐) / 점검 걸림 {len(issues)}건 / 설계 다시 받음 {len(redone_list)}편")
    for i in issues:
        print("  -", i)
    print(f"[build] 저장: plan_{ymd()}.json, report.md, product_need_{ymd()}.md")


def write_report(entries, rec, redone, issues):
    pl_name = {"naver": "네이버", "wordpress": "싼세계"}
    order = sorted(entries, key=lambda e: (e["role"] == "reserve", {"naver": 0, "wordpress": 1}[e["_plat"]], e.get("publish_date") or "9", e["keyword"]))
    L = ["| 플랫폼 | 발행일 | 키워드 | 모양 | 검색량 | 추세 비율 | 정점까지 주 | 확정 제목 | 같은 플랫폼의 가장 가까운 기존 글 | 판정 | 제품 필요 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in order:
        sg = e.get("r1") or {}
        pn = e["product_need"]
        need = f"{pn['count']}개 ({pn['link_kind']})" if pn["need"] else "필요 없음"
        L.append(f"| {pl_name[e['_plat']]}{'(예비)' if e['role'] == 'reserve' else ''} | {e.get('publish_date') or '예비'} | {e['keyword']} | {sg.get('shape') or '-'} | "
                 f"{e.get('monthly_total') if e.get('monthly_total') is not None else '-'} | "
                 f"{sg.get('ratio') if sg.get('ratio') is not None else ('상승시작' if sg.get('from_zero') else '-')} | {sg.get('peak') if sg.get('peak') is not None else '-'} | "
                 f"{e.get('final_title')} | {(e['judge'].get('closest') or '-').replace('|', '/')} | {e['judge'].get('verdict') or '-'} | {need} |")
    c = rec["counts"]
    wp_on = pools_wp_on()
    st = stats()
    u = jload(D / "usage.json", {}) or {}
    by_stage = {}
    for cl in u.get("claude_calls", []):
        by_stage[cl["stage"]] = by_stage.get(cl["stage"], 0) + 1
    out = [f"# 주간 기획 보고 — {WEEK.isoformat()} 주", "", f"- 기존 글 색인 복사본(운영 색인은 그대로): {D / 'index' / 'post_index.json'}", "", "## 계획표", "", *L, "",
           "## 편수와 부족분", "",
           (f"- 네이버 일정 {c['naver']}편 (목표 {rec['shortage'] and (c['naver'] + rec['shortage']['naver_sched'])}), 싼세계 일정 {c['wordpress']}편, 예비 네이버 {c['reserve_naver']}편·싼세계 {c['reserve_wordpress']}편" if wp_on
            else f"- 네이버 일정 {c['naver']}편 (목표 {rec['shortage'] and (c['naver'] + rec['shortage']['naver_sched'])}), 예비 네이버 {c['reserve_naver']}편 (싼세계는 꺼져 있음)"),
           f"- 부족: {rec['shortage']}", "",
           "## 후보 수와 탈락 사유", "", "```", json.dumps(st, ensure_ascii=False, indent=1), "```", "",
           "## 채우기에서 빠진 글", "", *[f"- {k}: {len(v)}개" for k, v in rec["dropped"].items()], "",
           "## 제목-본문 설계를 다시 받은 글", "", *([f"- {x}" for x in redone] or ["- 없음"]), "",
           "## 점검에서 걸린 것", "", *([f"- {i}" for i in issues] or ["- 없음"]), "",
           "## API·Claude 사용량", "",
           f"- 네이버 API: 블로그 검색 {u.get('blog', 0)}회 · 데이터랩 {u.get('trend', 0)}회 · 검색광고 {u.get('searchad', 0)}회",
           f"- Claude: {u.get('claude', 0)}회 (단계별 {by_stage}), 비용 약 ${u.get('claude_cost_usd', 0)} (제목 단계 안의 호출은 plan 의 batches 에 따로 기록)", ""]
    write_text_retry(D / "report.md", "\n".join(out))


def write_product_need(entries):
    L = [f"# 제품 필요 목록 — {WEEK.isoformat()} 주", "", "제품명·가격·링크는 사람이 찾아 `products_%s.txt` 에 적습니다(`키워드 | 제품명 | 링크`)." % ymd(), ""]
    _wp_on = pools_wp_on()
    for plat, name, kind in (("naver", "네이버 글 (링크: 네이버쇼핑)", "네이버쇼핑"), ("wordpress", "싼세계 글 (링크: 쿠팡파트너스)", "쿠팡파트너스")):
        if plat == "wordpress" and not _wp_on:
            continue            # 싼세계가 꺼져 있으면 싼세계 줄은 내지 않는다
        for role, label in (("scheduled", "일정"), ("reserve", "예비")):
            rows = sorted([e for e in entries if e["_plat"] == plat and e["role"] == role and e["product_need"]["need"]], key=lambda e: (e.get("publish_date") or "9", e["keyword"]))
            if not rows:
                continue
            L += [f"## {name} — {label}", "", "| 발행일 | 키워드 | 필요 수 | 찾을 조건 |", "|---|---|---|---|"]
            L += [f"| {e.get('publish_date') or '예비'} | {e['keyword']} | {e['product_need']['count']} | {e['product_need']['condition']} |" for e in rows]
            L.append("")
    none = [e["keyword"] for e in entries if not e["product_need"]["need"] and e["role"] == "scheduled"]
    L += ["## 제품이 필요 없는 일정 글", "", ", ".join(none) or "없음", ""]
    write_text_retry(D / f"product_need_{ymd()}.md", "\n".join(L))


# ================= 확정 =================
def cmd_finalize():
    src = D / f"plan_{ymd()}.json"
    plan = jload(src)
    if not plan:
        raise SystemExit(f"결과 폴더에 계획 파일이 없습니다: {src}")
    edits = jload(A.edits, {}) if A.edits else {}
    out, log_, warn = plan_edits.apply_edits(plan, edits)
    fdir = Path(A.final_dir)
    fdir.mkdir(parents=True, exist_ok=True)
    targets = {"plan": fdir / f"plan_{ymd()}.json", "cat": fdir / f"categories_{ymd()}.json", "prod": fdir / f"products_{ymd()}.txt"}
    for t in targets.values():
        if t.exists():
            shutil.copy2(t, t.with_name(t.stem + f".backup_before_finalize_{datetime.now():%Y%m%d_%H%M%S}" + t.suffix))
    cats = {}
    rp = {}
    for p in out["plans"]:
        cats.setdefault(p["keyword"], p.get("category") or "")
        if p.get("role") == "reserve" and p.get("platform") in push_plan.QUEUES:
            rp[p["keyword"]] = p["platform"]
    jsave_indent(targets["plan"], out)
    jsave_indent(targets["cat"], {"설명": "push_plan.py 가 읽는 값. 카테고리(키워드→카테고리, 빈 값=미정)와 글 유형 덮어쓰기(플랫폼|키워드→유형)는 계획 파일의 값보다 이 파일이 우선합니다. weekly_plan.py --finalize 가 만듦.",
                                  "categories": cats, "types": {}, "reserve_platforms": rp})
    if not targets["prod"].exists():
        write_text_retry(targets["prod"], "# 한 줄에 `키워드 | 제품명 | 링크` (쿠팡 링크=싼세계, 네이버 링크=네이버). 제품 필요 목록은 product_need_%s.md 참고.\n" % ymd())
    sched = [p for p in out["plans"] if p.get("role") == "scheduled"]
    nv, wp = sum(p["platform"] == "naver" for p in sched), sum(p["platform"] == "wordpress" for p in sched)
    print("\n".join(log_))
    print("경고:", warn or "없음")
    print(f"[finalize] 일정 {len(sched)}편 (네이버 {nv} · 싼세계 {wp}), 예비 {len(out['plans']) - len(sched)}편 → {fdir}")
    print("  다음: python tools/push_plan.py --plan %s --categories %s --products <제품 json> (먼저 dry-run)" % (targets["plan"], targets["cat"]))


def jsave_indent(path, obj):
    write_text_retry(path, json.dumps(obj, ensure_ascii=False, indent=2))


# ================= main =================
def main():
    global A, D, WEEK, TODAY
    ap = argparse.ArgumentParser(description="주간 기획 도구 (r1c 일반화)")
    ap.add_argument("--week", required=True, help="그 주 월요일 날짜 (예: 2026-11-23)")
    ap.add_argument("--seeds")
    ap.add_argument("--skip")
    ap.add_argument("--must")
    ap.add_argument("--out", help="결과 폴더 (기본 pipeline/stage_w월일_run)")
    ap.add_argument("--blog-limit", type=int, default=2000)
    ap.add_argument("--trend-limit", type=int, default=300)
    ap.add_argument("--searchad-limit", type=int, default=200)
    ap.add_argument("--wp-peak-exclude", type=int, default=0, help="싼세계 정보글: 작년 정점까지 0~N주인 주제 제외(기본 0=이번 주가 정점인 주제만, -1=끔)")
    ap.add_argument("--wp-peak-bonus", type=int, default=20, help="싼세계 정보글 주제 순위: 정점까지 3주 이상 남은 주제 가점(0=끔)")
    ap.add_argument("--theme-cap", type=int, default=4, help="같은 갈래 물건 네이버 최대 편수")
    ap.add_argument("--with-wp", action="store_true", help="싼세계(W싼세계) 기획 켜기: filter.json 의 wp_on_values 값으로 싼세계 편수를 되살려 실행(기본은 꺼짐=편수 0)")
    ap.add_argument("--no-claude", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--until", choices=STAGES)
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--edits")
    ap.add_argument("--final-dir", default=str(BASE / "pipeline" / "final"))
    ap.add_argument("--today", help="시험용: 오늘 날짜 고정(YYYY-MM-DD)")
    ap.add_argument("--cache-from", help="시험용: 저장된 캐시 폴더(예: pipeline/stage_r1c_run)")
    ap.add_argument("--exclude-plan", action="append", help="이 계획 파일의 키워드는 후보에서 뺀다(여러 번 쓸 수 있음)")
    A = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    WEEK = date.fromisoformat(A.week)
    if WEEK.weekday() != 0:
        raise SystemExit("--week 는 월요일 날짜여야 합니다.")
    TODAY = date.fromisoformat(A.today) if A.today else date.today()
    D = Path(A.out) if A.out else BASE / "pipeline" / f"stage_w{WEEK:%m%d}_run"
    D.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPP_INDEX_DIR"] = str(D / "index")
    # 다른 도구(r1_signals·backtest)의 저장 위치·한도·날짜를 이번 실행용으로 바꾼다
    rs.RUN = D
    bt.RUN = D
    bt.TODAY = TODAY
    bt.TREND_START = WEEK - timedelta(days=364)
    bt.BLOG_LIMIT, bt.TREND_LIMIT = A.blog_limit, A.trend_limit
    if A.finalize:
        cmd_finalize()
        return
    if not A.seeds:
        raise SystemExit("--seeds 파일이 필요합니다.")
    copy_cache()
    fns = {"index": stage_index, "explore": stage_explore, "trend": stage_trend, "shape": stage_shape, "signals": stage_signals, "judge": stage_judge,
           "fill": stage_fill, "plan": stage_plan, "check": stage_check, "build": stage_build}
    last = STAGES.index(A.until) if A.until else len(STAGES) - 1
    for st in STAGES[:last + 1]:
        fns[st]()


if __name__ == "__main__":
    main()
