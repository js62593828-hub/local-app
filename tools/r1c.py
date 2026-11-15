# -*- coding: utf-8 -*-
"""R1c: r1b 결과에서 지나친 중복 판정을 바로잡고(플랫폼별 판정), 기존 글이 안 다룬 제철 영역을 새로 찾아 빈자리를 채운다.

운영 파일·앱 데이터는 건드리지 않는다. 결과는 전부 pipeline/stage_r1c_run/ 에만 쓴다.
각 단계 결과는 파일로 저장돼 있어 끊겨도 같은 명령으로 이어 갈 수 있다(저장된 API 값·Claude 판정은 다시 받지 않는다).

  explore  씨앗 연관 키워드 모으기 (검색광고 API, 씨앗마다 1회) → cands.json
  trend    데이터랩 주간 추세 (한도 300회)                        → raw_cache.json
  shape    모양·유형 분류 (Claude haiku 1회)                      → shape.json, type.json
  signals  신호 합치기 + 후보 풀 + 상위 5개 글(블로그 검색)        → pools.json, ranking_r1.json
  judge    플랫폼별 중복 판정 (Claude sonnet 1회)                 → judge_result.json
  fill     판정 통과한 것만 채우기                                 → fill.json
  plan     새로 들어온 글만 기획(본문 설계·제목, 호출 2번)         → plan_step.json
  build    최종 계획·보고표                                        → plan_20261116.json, report.md

사용법: python tools/r1c.py explore | trend | shape | signals | judge | fill | plan | build
"""
import argparse
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
import plan_posts as pp  # noqa: E402
import r1 as r1m  # noqa: E402
import r1b  # noqa: E402
import r1_signals as rs  # noqa: E402
from safe_io import write_text_retry  # noqa: E402

D = BASE / "pipeline" / "stage_r1c_run"
R1 = BASE / "pipeline" / "stage_r1_run"
R1B = BASE / "pipeline" / "stage_r1b_run"
MONDAY = date(2026, 11, 16)
TODAY = date(2026, 11, 14)
# 다른 도구(r1_signals·backtest)의 저장 위치·한도를 이번 실행용으로 바꾼다
rs.RUN = D
bt.RUN = D
bt.TODAY = TODAY
bt.BLOG_LIMIT, bt.TREND_LIMIT = 2000, 300

nospace, jload, jsave, log = bt.nospace, bt.jload, bt.jsave, bt.log

# ---------- 설정 ----------
NAVER_REVIVE = ["창틀바람막이", "실외기실방풍", "화장실동파방지", "김장"]
WP_REVIVE = ["전기난로", "현관방풍비닐"]
NAVER_TARGET, NAVER_RESERVE = 21, 5
WP_INFO, WP_MONEY, WP_RESERVE = 10, 4, 5
PER_DAY = {"naver": 3, "wordpress": 2}

# (씨앗, 연관 키워드가 가져야 할 낱말들) — 낱말이 하나도 없으면 연관 없음으로 버린다
SEEDS = [
    ("실내건조", ["실내건조", "빨래건조", "건조대"]), ("빨래건조대", ["건조대"]), ("빨래안마름", ["빨래", "건조"]),
    ("코트관리", ["코트"]), ("롱패딩", ["롱패딩"]), ("가죽자켓관리", ["가죽"]), ("장갑", ["장갑"]), ("어그부츠", ["어그"]),
    ("수도계량기동파", ["계량기"]),
    ("난방비", ["난방비"]), ("가스비절약", ["가스비", "난방비"]),
    ("제설제", ["제설"]), ("염화칼슘", ["염화칼슘"]), ("눈삽", ["눈삽"]),
    ("워셔액", ["워셔액"]), ("부동액", ["부동액"]), ("윈터타이어", ["윈터타이어", "스노우타이어", "겨울타이어"]),
    ("김서림", ["김서림"]), ("겨울세차", ["세차"]),
    ("전기방석", ["방석"]), ("전기요", ["전기요"]), ("손난로", ["손난로"]), ("온풍기", ["온풍기"]), ("난로", ["난로"]),
    ("수면양말", ["수면양말"]), ("실내슬리퍼", ["슬리퍼"]), ("잠옷", ["잠옷"]),
    ("겨울러그", ["러그"]), ("카펫", ["카펫"]),
    ("방한커튼", ["커튼"]), ("암막커튼", ["커튼"]),
    ("귤보관", ["귤"]), ("무보관", ["무보관", "무저장"]), ("배추보관", ["배추"]), ("시래기", ["시래기"]), ("곶감", ["곶감"]),
    ("젓갈냄새", ["젓갈"]), ("김장고무장갑", ["고무장갑"]),
    ("크리스마스트리", ["트리"]), ("크리스마스장식", ["크리스마스", "장식"]),
    ("대청소", ["대청소"]), ("보온병", ["보온병"]),
]
# 씨앗으로 쓰지 않기로 한 영역(이미 다룬 영역): 연관 키워드에 들어 있으면 뺀다
FORBID = ["방풍", "방풍비닐", "방풍막", "문풍지", "외풍", "웃풍", "우풍", "난방텐트", "온수매트", "전기장판", "동파열선", "동파방지열선", "열선",
          "김치통", "가습기", "결로", "이불"]
PER_SEED_MAX = 12
THEME = {"크리스마스트리": "크리스마스", "크리스마스장식": "크리스마스", "롱패딩": "패딩류", "어그부츠": "어그"}
NAVER_THEME_CAP = 2
EXCLUDED_TYPES = {"local", "service", "buyplace", "offtopic"}


OBJ_PREFIX = sorted(["사무실", "화장실", "욕실", "미니", "가정용", "전기", "휴대용", "충전식", "충전", "남자", "여자", "남성", "여성", "주니어", "어린이", "1인용",
                     "소형", "겨울", "방한", "현관", "거실", "주방", "차량용", "캠핑", "무선", "크리스마스"], key=len, reverse=True)


def norm_obj(o):
    """판정이 준 물건 이름에서 장소·크기·쓰는 사람 꾸밈말을 떼고 비교용으로 만든다(같은 물건을 장소만 바꿔 두 번 뽑지 않게)."""
    global _ALIAS
    if _ALIAS is None:      # 프로젝트의 '같은 물건의 다른 이름' 표(손난로=핫팩, 오일히터=라디에이터 …)도 같이 쓴다
        a = dict(jload(R1B / "index" / "target_alias.json")["alias"])
        _ALIAS = {nospace(k): nospace(v) for k, v in a.items()}
    o = nospace(o)
    for pre in OBJ_PREFIX:
        if o.startswith(pre) and len(o) - len(pre) >= 2:
            o = o[len(pre):]
            break
    return _ALIAS.get(o, o)


_ALIAS = None


def stats():
    return jload(D / "stats.json", {}) or {}


def stat_add(stage, reason, n=1, names=None):
    s = stats()
    st = s.setdefault(stage, {})
    st[reason] = st.get(reason, 0) + n if reason not in st or not isinstance(st[reason], dict) else st[reason]
    jsave(D / "stats.json", s)


def stat_set(stage, reason, n):
    s = stats()
    s.setdefault(stage, {})[reason] = n
    jsave(D / "stats.json", s)


def cfg():
    return rs.cfg()


# ================= 1) explore =================
def cmd_explore(_):
    D.mkdir(parents=True, exist_ok=True)
    raw = rs.raw_cache()
    api = bt.Api()
    plan_kw = {nospace(e["keyword"]) for e in jload(R1 / "plan_20261116.json")["plans"]}
    for hint, _t in SEEDS:
        ns = nospace(hint)
        if ns in raw["expand"]:
            continue
        lst = api.searchad([hint])
        if lst is None:
            log(f"  [실패] 검색광고 {hint}")
            continue
        raw["expand"][ns] = [{"kw": it.get("relKeyword"), "pc": api.cm.to_int(it.get("monthlyPcQcCnt")), "mo": api.cm.to_int(it.get("monthlyMobileQcCnt"))}
                             for it in lst]
        api.save()
        rs.save_raw(raw)
    vmin = cfg()["volume_min"]
    cands, cen = {}, {}
    why = {"씨앗 낱말 없음(연관 없음)": 0, f"검색량 {vmin} 미만": 0, "이미 다룬 영역 낱말": 0, "금지·구매처 낱말": 0, "이미 계획에 있는 키워드": 0,
           "같은 키워드가 다른 씨앗에서 이미 나옴": 0, "씨앗별 상위 12개 밖": 0}
    seen_all = 0
    for hint, toks in SEEDS:
        ns = nospace(hint)
        rows = [x for x in raw["expand"].get(ns, []) if x.get("kw")]
        self_vol = next(((x["pc"] + x["mo"]) for x in rows if nospace(x["kw"]) == ns), None)
        cen[ns] = {"word": hint, "volume": self_vol}
        keep = []
        for x in rows:
            nk, vol = nospace(x["kw"]), x["pc"] + x["mo"]
            seen_all += 1
            if nk == ns:
                continue
            if not any(nospace(t) in nk for t in toks) and ns not in nk:
                why["씨앗 낱말 없음(연관 없음)"] += 1
            elif vol < vmin:
                why[f"검색량 {vmin} 미만"] += 1
            elif any(f in nk for f in FORBID):
                why["이미 다룬 영역 낱말"] += 1
            elif any(w in nk for w in rs.DROP_WORDS + rs.BUY_WORDS):
                why["금지·구매처 낱말"] += 1
            elif nk in plan_kw:
                why["이미 계획에 있는 키워드"] += 1
            elif nk in cands:
                why["같은 키워드가 다른 씨앗에서 이미 나옴"] += 1
            else:
                keep.append((vol, x["kw"], nk))
        keep.sort(key=lambda t: -t[0])
        why["씨앗별 상위 12개 밖"] += max(0, len(keep) - PER_SEED_MAX)
        for vol, kw, nk in keep[:PER_SEED_MAX]:
            cands[nk] = {"keyword": kw, "volume": vol, "src": "expand", "old_grade": "없음", "kw_type": None, "seed": hint, "track": None, "in_ranking": False}
    for ns, v in cen.items():       # 중심 낱말(씨앗) 자체도 신호를 받는다
        if (v["volume"] or 0) >= vmin and ns not in cands and not any(f in ns for f in FORBID):
            cands[ns] = {"keyword": v["word"], "volume": v["volume"], "src": "center", "old_grade": "없음", "kw_type": None, "seed": v["word"],
                         "track": None, "in_ranking": False}
    jsave(D / "cands.json", cands)
    jsave(D / "centers.json", cen)
    s = stats()
    s["explore"] = {"씨앗 수": len(SEEDS), "연관 키워드 총수": seen_all, "후보로 남음": len(cands), "탈락 사유": why}
    jsave(D / "stats.json", s)
    print(f"씨앗 {len(SEEDS)}개 → 연관 키워드 {seen_all}개 → 후보 {len(cands)}개. 탈락: {why}. 사용량 {api.usage}")


# ================= 2) trend =================
def cmd_trend(_):
    rs.cmd_trend(None)


# ================= 3) shape (모양·유형) =================
SHAPE_PROMPT = """아래는 생활 블로그(겨울철 집 살림·생활 정보) 키워드 목록이다. 도구는 쓰지 말고 바로 답해라.
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


def cmd_shape(_):
    cands = jload(D / "cands.json", {})
    raw = rs.raw_cache()
    done = jload(D / "shape.json", {}) or {}
    types = jload(D / "type.json", {}) or {}
    no_trend = [nk for nk in cands if nk not in raw["trend"]]
    todo = [nk for nk in cands if nk in raw["trend"] and nk not in done]
    stat_set("shape", "추세 값 없음(신호 없음)", len(no_trend))
    if not todo:
        print("이미 모양 분류가 끝나 있습니다(Claude 호출 없음).")
        return
    api = bt.Api()
    if api.usage.get("claude", 0) >= 1 and not done:
        pass
    ids = {f"X{k + 1}": nk for k, nk in enumerate(todo)}
    lines = "\n".join(f"{xid} | {cands[nk]['keyword']}" for xid, nk in ids.items())
    api.usage["claude"] += 1
    api.save()
    log(f"[shape] 키워드 {len(todo)}개 → Claude(haiku) 1회")
    env = pp.call_claude(SHAPE_PROMPT.format(lines=lines), "haiku")
    items = pp.extract_json(env.get("result", "")).get("items", [])
    for it in items:
        nk = ids.get(it.get("id"))
        if nk and it.get("shape") in r1m.SHAPES:
            done[nk] = it["shape"]
            types[nk] = {"type": it.get("type"), "money": bool(it.get("money"))}
    jsave(D / "shape.json", done)
    jsave(D / "type.json", types)
    cost = float(env.get("total_cost_usd") or 0)
    api.usage["claude_cost_usd"] = round(float(api.usage.get("claude_cost_usd") or 0) + cost, 4)
    api.save()
    miss = [nk for nk in todo if nk not in done]
    stat_set("shape", "분류 응답에 빠짐", len(miss))
    print(f"모양 분류 {len(done)}/{len(todo)} 저장 (haiku 1회, 비용 ${cost:.3f}, 빠진 {len(miss)}개)")


# ================= 4) signals =================
def build_sig():
    c = cfg()
    cands = jload(D / "cands.json", {})
    raw = rs.raw_cache()
    shapes = jload(D / "shape.json", {}) or {}
    types = jload(D / "type.json", {}) or {}
    cen = jload(D / "centers.json", {}) or {}
    sig = {}
    for nk, v in cands.items():
        s = rs.signals_for(nk, raw, shapes, c)
        s.update({"keyword": v["keyword"], "volume": v["volume"], "old_grade": v["old_grade"], "src": v["src"], "seed": v["seed"], "kw_type": v.get("kw_type"),
                  "type": (types.get(nk) or {}).get("type"), "money": (types.get(nk) or {}).get("money", False), "cw": nospace(v["seed"])})
        sig[nk] = s
    centers = {ns: {**v, **{k: sig[ns][k] for k in ("ratio", "peak", "shape", "from_zero") if ns in sig}} for ns, v in cen.items()}
    return c, cands, sig, centers


def cmd_signals(_):
    c, cands, sig, centers = build_sig()
    reasons = {"이미 다룬 영역 낱말(방풍 등, 탐색 뒤 추가)": 0, "추세 값 없음": 0, "모양 분류 없음": 0, "제외 유형(지역·업체·구매처·무관)": 0}
    usable = {}
    for nk, s in sig.items():
        if any(f in nk for f in FORBID):
            reasons["이미 다룬 영역 낱말(방풍 등, 탐색 뒤 추가)"] += 1
        elif not s["has_trend"]:
            reasons["추세 값 없음"] += 1
        elif not s["shape"]:
            reasons["모양 분류 없음"] += 1
        elif s["type"] in EXCLUDED_TYPES:
            reasons["제외 유형(지역·업체·구매처·무관)"] += 1
        else:
            usable[nk] = s
    # 네이버 풀: 브랜드 아님 + 시기 조건(추세 상승 또는 정점 0~3주 전) + 모양 순위
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
        if per.get(sd, 0) >= 4 or len(nav_pool) >= 45:
            over += 1
            continue
        per[sd] = per.get(sd, 0) + 1
        nav_pool.append(nk)
    nr["씨앗별 4개·전체 45개 한도 밖"] = over
    # 싼세계 정보글: 중심 낱말 검색량 5,000 이상 주제 묶음
    wp = c["wordpress"]
    tops = []
    for ns, v in centers.items():
        if (v.get("volume") or 0) >= wp["topic_volume_min"] and ns in usable:
            tops.append((r1m.topic_score(sig[ns], c), ns))
    tops.sort(key=lambda t: (-t[0], -(centers[t[1]].get("volume") or 0), t[1]))
    topics = [ns for _s, ns in tops]
    small = [ns for ns, v in centers.items() if (v.get("volume") or 0) < wp["topic_volume_min"]]
    wr = {"중심 낱말 검색량 5,000 미만 주제": 0, "브랜드(수익글 쪽으로)": 0, "묶음당 5개 한도 밖": 0}
    wp_pool, cnt = [], {}
    for ns in topics[:8]:
        mem = [nk for nk, s in usable.items() if s["cw"] == ns and s["volume"] >= c["volume_min"]]
        mem.sort(key=lambda nk: (-r1m.naver_score(sig[nk], c), -sig[nk]["volume"], nk))
        for nk in mem:
            if sig[nk]["type"] == "brand" or sig[nk]["shape"] == "브랜드·모델":
                wr["브랜드(수익글 쪽으로)"] += 1
                continue
            if cnt.get(ns, 0) >= 5:
                wr["묶음당 5개 한도 밖"] += 1
                continue
            cnt[ns] = cnt.get(ns, 0) + 1
            wp_pool.append(nk)
    wr["중심 낱말 검색량 5,000 미만 주제"] = sum(1 for nk, s in usable.items() if s["cw"] in small)
    # 싼세계 수익글: 브랜드·모델명 또는 조건(가격대·용도·규격)이 붙은 연관 키워드
    mp = [nk for nk, s in usable.items() if s["money"] or s["type"] == "brand" or s["shape"] == "브랜드·모델"]
    mp.sort(key=lambda nk: (-r1m.naver_score(sig[nk], c), -sig[nk]["volume"], nk))
    money_pool = mp[:18]
    stat_set("signals", "추세·모양 없음 또는 제외 유형", reasons)
    stat_set("signals", "네이버 풀 탈락", nr)
    stat_set("signals", "싼세계 정보 풀 탈락", wr)
    stat_set("signals", "풀 크기", {"네이버": len(nav_pool), "싼세계 정보": len(wp_pool), "싼세계 수익": len(money_pool), "사용 가능 후보": len(usable)})
    keys = list(dict.fromkeys(nav_pool + wp_pool + money_pool))
    jsave(D / "pools.json", {"naver": nav_pool, "wp_info": wp_pool, "wp_money": money_pool, "topics": [{"center": ns, "volume": centers[ns].get("volume"), "score": sc} for sc, ns in tops]})
    jsave(D / "r1c_signals.json", {"signals": sig, "centers": centers})
    print(f"풀: 네이버 {len(nav_pool)} · 싼세계 정보 {len(wp_pool)} · 싼세계 수익 {len(money_pool)} (사용 가능 {len(usable)}개, 상위 글 받을 키워드 {len(keys)}개)")
    print("탈락:", reasons, nr, wr)
    rs.build_ranking(keys, sig, cands, c, centers)


# ================= 5) judge =================
NAVER_SRC = {"naver_blog", "content_queue_naver_tips.json", "content_queue_radar_naver.json", "content_queue_shop_naver.json"}
WP_SRC = {"shop", "content_queue_wp_tips.json", "content_queue_shop_wp.json"}


def existing_by_platform():
    d = jload(BASE / "pipeline" / "post_index.json")["posts"]
    nv, wp = [], []
    seen = set()
    for t, v in d.items():
        s = v.get("source")
        title = " ".join(str(t or "").split())
        if s in NAVER_SRC:
            nv.append({"title": title, "src": s})
        elif s in WP_SRC:
            wp.append({"title": title, "src": s})
    return {"naver": nv, "wordpress": wp}


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


def fixed_entries():
    """사용자와 정한 유지·되살리기 글: {키워드: 항목}과 순서."""
    b = jload(R1B / "plan_20261116.json")["plans"]
    r1plan = {e["keyword"]: e for e in jload(R1 / "plan_20261116.json")["plans"]}
    ps = jload(R1B / "plan_step.json")
    out = []
    for e in b:
        e = dict(e)
        e["_kind"] = "유지"
        out.append(e)
    for kw in NAVER_REVIVE + WP_REVIVE:
        e = dict(r1plan[kw])
        if kw in ps["claude_by_kw"] and kw in ("화장실동파방지", "김장"):      # r1b 에서 받은 기획·제목을 쓴다
            c, tr = ps["claude_by_kw"][kw], ps["title_res"][kw]
            for k_ in ("search_intent", "gaps", "outline", "experience_slots", "beat_top", "target_chars", "target_images"):
                e[k_] = c.get(k_)
            e.update({k_: v_ for k_, v_ in tr.items() if k_ != "retried"})
            if kw in r1b.ANGLE:
                e["angle"] = r1b.ANGLE[kw]
        e["_kind"] = "되살림"
        e["_orig_date"] = e.get("publish_date")
        if e["role"] == "reserve":          # 예비에 있던 글(김장)도 일정으로 되살린다
            e["role"], e["platform"] = "scheduled", ("naver" if e.get("r1_style") != "wordpress" else "wordpress")
        out.append(e)
    return out


def plan_text(e):
    t = e.get("final_title")
    s = f"{t} (키워드: {e['keyword']})"
    if e.get("angle"):
        s += f" — 이번 글의 각도: {e['angle']}"
    return s


def cmd_judge(args):
    out = D / "judge_result.json"
    if out.exists() and not args.force:
        log("[judge] 저장된 판정이 있어 다시 묻지 않음 (--force 로 다시)")
        return
    ex = existing_by_platform()
    log(f"기존 글: 네이버 {len(ex['naver'])}편 · 싼세계 {len(ex['wordpress'])}편")
    alias = jload(R1B / "index" / "target_alias.json")["alias"]
    vg = r1b.vocab_groups(alias)
    pools = jload(D / "pools.json")
    sig = jload(D / "r1c_signals.json")["signals"]
    items, meta = [], {}

    def add(iid, platform, kind, text, keyword, extra_tokens=()):
        _t, hit = r1b.terms_of(keyword, vg)
        toks = set(_t) | {nospace(w) for w in keyword.split() if len(w) >= 2} | {nospace(keyword)} | set(extra_tokens)
        toks = {t for t in toks if len(t) >= 2}
        items.append({"id": iid, "platform": platform, "kind": kind, "text": text, "existing": related(nospace(keyword), toks, ex[platform])})
        meta[iid] = {"keyword": keyword, "platform": platform, "kind": kind}

    for i, e in enumerate(fixed_entries(), 1):
        plat = e["platform"] or ("naver" if e.get("r1_style") != "wordpress" else "wordpress")
        add(f"P{i}", plat, "계획", plan_text(e), e["keyword"])
    for n, nk in enumerate(pools["naver"], 1):
        add(f"N{n}", "naver", "후보", sig[nk]["keyword"], sig[nk]["keyword"], [nospace(sig[nk]["seed"])])
    for n, nk in enumerate(pools["wp_info"], 1):
        add(f"W{n}", "wordpress", "후보", sig[nk]["keyword"], sig[nk]["keyword"], [nospace(sig[nk]["seed"])])
    for n, nk in enumerate(pools["wp_money"], 1):
        add(f"M{n}", "wordpress", "후보", sig[nk]["keyword"], sig[nk]["keyword"], [nospace(sig[nk]["seed"])])
    prompt = JUDGE_PROMPT.format(items=json.dumps(items, ensure_ascii=False, separators=(",", ":")))
    write_text_retry(D / "judge_prompt.txt", prompt)
    log(f"[judge] 항목 {len(items)}개, 질문 {len(prompt)}자 → Claude(sonnet) 1회")
    env, res, err = None, None, None
    for attempt in range(2):          # 일시 실패만 한 번 더(판정 자체를 다시 돌리는 것이 아님)
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
    got = {r.get("id"): r for r in res}
    miss = [it["id"] for it in items if it["id"] not in got]
    u = env.get("usage") or {}
    jsave(out, {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "calls": 1, "cost_usd": env.get("total_cost_usd"),
                "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"), "missing": miss, "meta": meta, "items": items,
                "results": res})
    log(f"[judge] 저장: judge_result.json (비용 ${env.get('total_cost_usd')}, 빠진 항목 {len(miss)}개)")


# ================= 6) fill =================
def cmd_fill(args):
    planned = None
    if getattr(args, "planned_only", False):       # 이미 기획을 받은 글 안에서만 다시 고른다(기획 호출 한도 2번 때문)
        if not (D / "fill_first.json").exists():
            shutil.copy2(D / "fill.json", D / "fill_first.json")
        old = jload(D / "fill_first.json")["picked"]
        planned = {("naver", k) for k in old["naver_sched"] + old["naver_res"]} | {("wordpress", k) for k in old["wp_info"] + old["wp_money"] + old["wp_res"]}
    jr = jload(D / "judge_result.json")
    verdict = {r["id"]: r for r in jr["results"]}
    meta = jr["meta"]
    pools = jload(D / "pools.json")
    sig = jload(D / "r1c_signals.json")["signals"]
    fixed = fixed_entries()
    taken = {"naver": [], "wordpress": []}       # 일정+예비에 들어간 글의 물건 이름
    taken_kw = {"naver": set(), "wordpress": set()}
    fx = {"naver": [], "wordpress": [], "reserve": []}
    info_fixed = money_fixed = 0
    for i, e in enumerate(fixed, 1):
        v = verdict.get(f"P{i}") or {}
        plat = e["platform"] or ("naver" if e.get("r1_style") != "wordpress" else "wordpress")
        key = norm_obj(v.get("object") or e["keyword"])
        taken[plat].append(key)
        taken_kw[plat].add(nospace(e["keyword"]))
        if e["role"] == "reserve":
            fx["reserve"].append(e["keyword"])
        else:
            fx["naver" if plat == "naver" else "wordpress"].append(e["keyword"])
            if plat == "wordpress":
                info_fixed += e.get("track") == "info"
                money_fixed += e.get("track") == "money"
    drops = {"중복 판정": [], "집 살림과 무관": [], "같은 물건이 이미 있음(주 1편)": [], "같은 묶음 3개 한도": [], "같은 주제(씨앗) 네이버 2편 한도": [], "싼세계 예비 주제 1편 한도": [], "기획을 안 받은 후보(기획 호출 2번 한도)": []}
    picked = {"naver_sched": [], "naver_res": [], "wp_info": [], "wp_money": [], "wp_res": []}
    obj_of = {}

    def usable(iid, plat, kw):
        v = verdict.get(iid)
        if not v:
            return False
        if planned is not None and (plat, kw) not in planned:
            drops["기획을 안 받은 후보(기획 호출 2번 한도)"].append(kw)
            return False
        if v.get("unfit"):
            drops["집 살림과 무관"].append(kw)
            return False
        if v.get("verdict") == "중복":
            drops["중복 판정"].append(kw)
            return False
        key = norm_obj(v.get("object") or kw)
        if key in taken[plat] or nospace(kw) in taken_kw[plat]:
            drops["같은 물건이 이미 있음(주 1편)"].append(kw)
            return False
        return True

    def take(iid, plat, nk):
        v = verdict[iid]
        kw = sig[nk]["keyword"]
        taken[plat].append(norm_obj(v.get("object") or kw))
        taken_kw[plat].add(nospace(kw))
        obj_of[kw] = v.get("object")
        return nk

    theme_n = {}
    n_need = NAVER_TARGET - len(fx["naver"])
    n_res_need = NAVER_RESERVE - sum(1 for k in fx["reserve"])
    for n, nk in enumerate(pools["naver"], 1):
        if len(picked["naver_sched"]) >= n_need and len(picked["naver_res"]) >= n_res_need:
            break
        th = THEME.get(sig[nk]["seed"], sig[nk]["seed"])
        if theme_n.get(th, 0) >= NAVER_THEME_CAP:
            drops["같은 주제(씨앗) 네이버 2편 한도"].append(sig[nk]["keyword"])
            continue
        if not usable(f"N{n}", "naver", sig[nk]["keyword"]):
            continue
        theme_n[th] = theme_n.get(th, 0) + 1
        take(f"N{n}", "naver", nk)
        (picked["naver_sched"] if len(picked["naver_sched"]) < n_need else picked["naver_res"]).append(nk)
    info_need, money_need = WP_INFO - info_fixed, WP_MONEY - money_fixed
    per_topic = {}
    for n, nk in enumerate(pools["wp_info"], 1):
        if len(picked["wp_info"]) >= info_need:
            break
        cw = sig[nk]["cw"]
        if per_topic.get(cw, 0) >= 3:
            drops["같은 묶음 3개 한도"].append(sig[nk]["keyword"])
            continue
        if not usable(f"W{n}", "wordpress", sig[nk]["keyword"]):
            continue
        per_topic[cw] = per_topic.get(cw, 0) + 1
        picked["wp_info"].append(take(f"W{n}", "wordpress", nk))
    for n, nk in enumerate(pools["wp_money"], 1):
        if len(picked["wp_money"]) >= money_need:
            break
        if not usable(f"M{n}", "wordpress", sig[nk]["keyword"]):
            continue
        picked["wp_money"].append(take(f"M{n}", "wordpress", nk))
    res_theme = {}
    # 싼세계 예비: 남은 정보 후보 → 남은 수익 후보
    for kind, pre, lst in (("wp_info", "W", pools["wp_info"]), ("wp_money", "M", pools["wp_money"])):
        for n, nk in enumerate(lst, 1):
            if len(picked["wp_res"]) >= WP_RESERVE:
                break
            if nk in picked["wp_info"] or nk in picked["wp_money"] or nk in picked["wp_res"]:
                continue
            th = THEME.get(sig[nk]["seed"], sig[nk]["seed"])
            if res_theme.get(th, 0) >= 1:
                drops["싼세계 예비 주제 1편 한도"].append(sig[nk]["keyword"])
                continue
            if not usable(f"{pre}{n}", "wordpress", sig[nk]["keyword"]):
                continue
            res_theme[th] = res_theme.get(th, 0) + 1
            picked["wp_res"].append(take(f"{pre}{n}", "wordpress", nk))
    # 날짜: 되살린 글은 원래 날짜 우선, 새 글은 그날 한도 안에서 글이 적은 날부터
    days = [(MONDAY + timedelta(days=i)).isoformat() for i in range(7)]
    cnt = {"naver": {d: 0 for d in days}, "wordpress": {d: 0 for d in days}}
    for e in fixed:
        if e["role"] == "scheduled":
            if e["_kind"] == "유지":
                cnt[e["platform"]][e["publish_date"]] += 1
    dates = {}
    for e in fixed:
        if e["_kind"] == "되살림" and e["role"] == "scheduled" or (e["_kind"] == "되살림" and e["keyword"] in NAVER_REVIVE + WP_REVIVE):
            plat = e["platform"] or ("naver" if e.get("r1_style") != "wordpress" else "wordpress")
            od = e.get("_orig_date")
            d = od if od in cnt[plat] and cnt[plat][od] < PER_DAY[plat] else min((d for d in days if cnt[plat][d] < PER_DAY[plat]), key=lambda d: (cnt[plat][d], d))
            cnt[plat][d] += 1
            dates[f"{plat}|{e['keyword']}"] = d
    for plat, lst in (("naver", picked["naver_sched"]), ("wordpress", picked["wp_info"] + picked["wp_money"])):
        for nk in lst:
            d = min((d for d in days if cnt[plat][d] < PER_DAY[plat]), key=lambda d: (cnt[plat][d], d))
            cnt[plat][d] += 1
            dates[f"{plat}|{sig[nk]['keyword']}"] = d
    st = {"fixed": fx, "picked": {k: [sig[nk]["keyword"] for nk in v] for k, v in picked.items()}, "picked_keys": picked, "dates": dates, "object": obj_of,
          "dropped": drops, "shortage": {
              "naver_sched": max(0, n_need - len(picked["naver_sched"])), "naver_reserve": max(0, n_res_need - len(picked["naver_res"])),
              "wp_info": max(0, info_need - len(picked["wp_info"])), "wp_money": max(0, money_need - len(picked["wp_money"])),
              "wp_reserve": max(0, WP_RESERVE - len(picked["wp_res"]))}}
    jsave(D / "fill.json", st)
    s = stats()
    s["fill"] = {k: len(v) for k, v in drops.items()}
    jsave(D / "stats.json", s)
    print("[fill] 새로 채움:", {k: v for k, v in st["picked"].items()})
    print("       날짜:", dates)
    print("       빠진 이유:", {k: len(v) for k, v in drops.items()}, "/ 부족:", st["shortage"])


# ================= 7) plan =================
def new_item(kw, nk, row, sig, role, plat, date_, track, style, w):
    it = {k: row.get(k) for k in ("keyword", "seed", "score", "monthly_total", "saturation", "trend_growth", "trend_steady", "smart_blocks",
                                  "related_keywords", "kw_type", "platform_hint", "fingerprint", "grade", "golden", "r1")}
    it.update(platform=plat, role=role, track=track, r1_style=style, publish_date=date_,
              weekday=pp.WEEKDAYS[date.fromisoformat(date_).weekday()] if date_ else None, found_date="2026-11-14", from_bank=False)
    pp.attach_clicks([it], w["click"])
    it["timing"] = r1m.label_of(row["r1"], w["r1"])
    return it


def new_items_ordered():
    st = jload(D / "fill.json")
    sig = jload(D / "r1c_signals.json")["signals"]
    rk = {r["keyword"]: r for r in jload(D / "ranking_r1.json")["ranking"]}
    w = pp.load_weekly(BASE / "tools" / "filter.json")
    P, dates = st["picked_keys"], st["dates"]
    out = []

    def go(lst, role, plat, track, style):
        for nk in lst:
            kw = sig[nk]["keyword"]
            dd = dates.get(f"{plat}|{kw}") if role == "scheduled" else None
            out.append(new_item(kw, nk, rk[kw], sig, role, plat, dd, track, style, w))
    go(P["naver_sched"], "scheduled", "naver", "info", "naver")
    go(P["wp_info"], "scheduled", "wordpress", "info", "wordpress")
    go(P["wp_money"], "scheduled", "wordpress", "money", "wordpress")
    go(P["naver_res"], "reserve", None, "info", "naver")
    go(P["wp_res"], "reserve", None, "info", "wordpress")
    seen = set()
    for it in out:          # 같은 키워드가 두 플랫폼에 들어간 경우: 제목 저장소가 키워드 단위라 뒤쪽 것은 띄어쓰기를 넣은 이름표로 따로 다룬다(기획 결과는 위치로 연결)
        if it["keyword"] in seen:
            it["_orig"] = it["keyword"]
            it["keyword"] = it["keyword"][:2] + " " + it["keyword"][2:]
        seen.add(it["keyword"])
    return out, w


def ensure_index_copy():
    (D / "index").mkdir(exist_ok=True)
    for f in (R1B / "index").glob("*.json"):
        if not (D / "index" / f.name).exists():
            shutil.copy2(f, D / "index" / f.name)


def cmd_plan(args):
    ensure_index_copy()
    os.environ["LOCALAPP_INDEX_DIR"] = str(D / "index")
    items, w = new_items_ordered()
    tc_path = D / "title_cache_20261116.json"
    if not tc_path.exists():
        shutil.copy2(R1B / "title_cache_20261116.json", tc_path)
        shutil.copy2(R1B / "intent_info_cache.json", D / "intent_info_cache.json")
    title_rules = pp.ttl.load_rules(BASE / "tools" / "filter.json")
    guide = pp.title_guide(title_rules["forbidden_words"], pp.ttl.style_samples(pp.ttl.own_titles()))
    title_rules["r1_wp"] = w["r1"]["wordpress"]
    guide += pp.R1_WP_TITLE_GUIDE.format(words=", ".join(w["r1"]["wordpress"]["title_search_words"][:12]))
    half = (len(items) + 1) // 2
    chunks = [items[:half], items[half:]]
    problems, batches, claude_by_kw = [], [], {}
    for n, chunk in enumerate(chunks, 1):
        if not chunk:
            continue
        pth = D / f"plan_call{n}.json"
        if pth.exists() and not args.force:
            saved = jload(pth)
            for idx, p_ in enumerate(saved["plans"]):
                if idx < len(chunk):
                    p_["keyword"] = chunk[idx]["keyword"]
                claude_by_kw[p_.get("keyword")] = p_
            batches.append(saved["batch"])
            log(f"[plan] 기획 호출 {n}번은 저장된 결과를 씀")
            continue
        log(f"[plan] 기획 호출 {n}번: {len(chunk)}편 ({', '.join(i['keyword'] for i in chunk)})")
        plans, env, probs = pp.plan_batch(pp.build_summary(chunk, title_rules), guide)
        problems += probs
        if env is None:
            raise SystemExit("기획 호출 실패: " + "; ".join(probs))
        u = env.get("usage") or {}
        b = {"batch": n, "keywords": len(chunk), "total_cost_usd": env.get("total_cost_usd"), "num_turns": env.get("num_turns"),
             "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
        batches.append(b)
        for idx, p_ in enumerate(plans):
            if idx < len(chunk) and p_.get("keyword", "").replace(" ", "") == chunk[idx]["keyword"].replace(" ", ""):
                p_["keyword"] = chunk[idx]["keyword"]
            claude_by_kw[p_.get("keyword")] = p_
        jsave(pth, {"plans": plans, "batch": b})
    nb = len(batches)
    title_res = pp.choose_titles(items, claude_by_kw, D, MONDAY, title_rules, guide, batches, problems)
    ok = [b for b in batches if not b.get("failed")]
    jsave(D / "plan_step.json", {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "claude_by_kw": claude_by_kw, "title_res": title_res,
                                 "plan_calls": nb, "batches": batches, "problems": problems,
                                 "usage": {"calls": len(batches), "cost_usd": round(sum(b.get("total_cost_usd") or 0 for b in ok), 4)}})
    log(f"[plan] 저장: plan_step.json (기획 호출 {nb}번, 제목 단계 포함 {len(batches)}회, ${sum(b.get('total_cost_usd') or 0 for b in ok):.4f}, 문제 {len(problems)}건)")
    for pr_ in problems:
        log(f"  [주의] {pr_}")


# ================= 8) build =================
def cmd_build(_):
    st, ps = jload(D / "fill.json"), jload(D / "plan_step.json")
    jr = jload(D / "judge_result.json")
    verdict = {r["id"]: r for r in jr["results"]}
    sig = jload(D / "r1c_signals.json")["signals"]
    sig_by_kw = {s["keyword"]: s for s in sig.values()}
    items, w = new_items_ordered()
    tr, cbk = ps["title_res"], ps["claude_by_kw"]
    ex = existing_by_platform()
    entries = []
    # 1) 유지·되살린 글
    for i, e in enumerate(fixed_entries(), 1):
        e = dict(e)
        v = verdict.get(f"P{i}") or {}
        plat = e["platform"] or ("naver" if e.get("r1_style") != "wordpress" else "wordpress")
        if e["_kind"] == "되살림":
            dk = f"{plat}|{e['keyword']}"
            e["role"], e["platform"] = ("scheduled", plat) if dk in st["dates"] else (e["role"], e["platform"])
            if dk in st["dates"]:
                e["publish_date"] = st["dates"][dk]
                e["weekday"] = pp.WEEKDAYS[date.fromisoformat(e["publish_date"]).weekday()]
        e["_plat"] = plat
        e["judge"] = {k: v.get(k) for k in ("verdict", "object", "closest", "reason")}
        entries.append(e)
    # 2) 새로 들어온 글
    for it in items:
        kw = it["keyword"]
        orig = it.get("_orig") or kw
        e = {k: it.get(k) for k in ("platform", "role", "publish_date", "weekday", "keyword", "seed", "track", "r1", "r1_style", "expected_clicks",
                                    "clicks_basis", "timing", "monthly_total", "saturation", "trend_growth", "trend_steady", "smart_blocks", "related_keywords")}
        e["top_posts"] = pp.top_posts_brief(it)
        e["new_in_r1c"] = True
        c = cbk.get(kw)
        if c:
            for k_ in ("search_intent", "gaps", "outline", "experience_slots", "beat_top", "target_chars", "target_images"):
                e[k_] = c.get(k_)
        e.update({k_: v_ for k_, v_ in tr[kw].items() if k_ != "retried"})
        plat = it["platform"] or ("naver" if it["r1_style"] == "naver" else "wordpress")
        e["_plat"] = plat
        e["keyword"] = orig
        iid = next((k for k, m in jr["meta"].items() if m["keyword"] == orig and m["platform"] == plat and m["kind"] == "후보"), None)
        v = verdict.get(iid) or {}
        e["judge"] = {k: v.get(k) for k in ("verdict", "object", "closest", "reason")}
        entries.append(e)
    # 3) 점검(코드): 같은 플랫폼 같은 키워드·같은 물건, 제목이 같은 플랫폼 기존 글과 글자가 너무 비슷한지
    issues = []
    ex_ns = {p: [(nospace(x["title"]), x["title"]) for x in lst] for p, lst in ex.items()}
    seen = {"naver": {}, "wordpress": {}}
    for e in entries:
        p = e["_plat"]
        ok = norm_obj(e["judge"].get("object") or e["keyword"])
        e["_obj"] = ok
        if ok in seen[p]:
            issues.append(f"같은 물건 두 편({p}): {seen[p][ok]} / {e['keyword']} (물건 '{e['judge'].get('object')}')")
        else:
            seen[p][ok] = e["keyword"]
        best = max(((r1b.sim(e["final_title"], ns), t) for ns, t in ex_ns[p]), default=(0, ""))
        e["title_sim"] = [round(best[0], 2), best[1]]
        if best[0] >= 0.6:
            issues.append(f"제목이 같은 플랫폼 기존 글과 매우 비슷({best[0]:.2f}): {e['final_title']} ≈ {best[1]}")
    sched_n = [e for e in entries if e["role"] == "scheduled" and e["_plat"] == "naver"]
    sched_w = [e for e in entries if e["role"] == "scheduled" and e["_plat"] == "wordpress"]
    res_n = [e for e in entries if e["role"] == "reserve" and e["_plat"] == "naver"]
    res_w = [e for e in entries if e["role"] == "reserve" and e["_plat"] == "wordpress"]
    for e in entries:
        if e["role"] == "reserve":
            e["platform"] = None
    rec = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "week": {"start": "2026-11-16", "end": "2026-11-22"},
           "source": "stage_r1b_run 유지 15+예비 1, 되살린 6, 새 탐색 글", "counts": {"naver": len(sched_n), "wordpress": len(sched_w),
                                                                                  "reserve_naver": len(res_n), "reserve_wordpress": len(res_w)},
           "shortage": st["shortage"], "dropped": {k: v for k, v in st["dropped"].items()}, "issues": issues, "problems": ps["problems"],
           "plans": [{k: v for k, v in e.items() if not k.startswith("_") or k == "_kind"} for e in entries]}
    jsave(D / "plan_20261116.json", rec)
    # 보고표
    pl_name = {"naver": "네이버", "wordpress": "싼세계"}
    order = sorted(entries, key=lambda e: (e["role"] == "reserve", {"naver": 0, "wordpress": 1}[e["_plat"]], e.get("publish_date") or "9", e["keyword"]))
    L = ["| 플랫폼 | 발행일 | 키워드 | 모양 | 검색량 | 추세 비율 | 정점까지 주 | 확정 제목 | 같은 플랫폼의 가장 가까운 기존 글 | 판정 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for e in order:
        sg = e.get("r1") or {}
        vol = e.get("monthly_total") or sg.get("volume")
        tag = {"유지": "", "되살림": " ★되살림"}.get(e.get("_kind"), " ☆새로")
        pl = pl_name[e["_plat"]] + ("(예비)" if e["role"] == "reserve" else "")
        L.append(f"| {pl} | {e.get('publish_date') or '예비'} | {e['keyword']}{tag} | {sg.get('shape') or '-'} | {vol if vol is not None else '-'} | "
                 f"{sg.get('ratio') if sg.get('ratio') is not None else ('상승시작' if sg.get('from_zero') else '-')} | "
                 f"{sg.get('peak') if sg.get('peak') is not None else '-'} | {e['final_title']} | {(e['judge'].get('closest') or '-').replace('|', '/')} | "
                 f"{e['judge'].get('verdict') or '-'} |")
    write_text_retry(D / "report.md", "## 보고표 (★=사용자와 정해 되살린 글, ☆=이번에 새로 탐색해 채운 글)\n\n" + "\n".join(L) + "\n\n## 점검에서 걸린 것\n\n" +
                     ("\n".join("- " + i for i in issues) if issues else "- 없음") + "\n")
    print(f"[build] 네이버 {len(sched_n)} / 싼세계 {len(sched_w)} / 예비 네이버 {len(res_n)} · 싼세계 {len(res_w)} / 점검 걸림 {len(issues)}건")
    for i in issues:
        print("  -", i)
    print("[build] 저장: plan_20261116.json, report.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="R1c")
    ap.add_argument("cmd", choices=["explore", "trend", "shape", "signals", "judge", "fill", "plan", "build"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--planned-only", action="store_true")
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    {"explore": cmd_explore, "trend": cmd_trend, "shape": cmd_shape, "signals": cmd_signals, "judge": cmd_judge, "fill": cmd_fill,
     "plan": cmd_plan, "build": cmd_build}[a.cmd](a)
