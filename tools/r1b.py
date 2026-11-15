# -*- coding: utf-8 -*-
"""R1 중복 보정 (재기획 R1b): stage_r1_run/plan_20261116.json 의 중복을 걸러내고 빈자리를 다시 채운다.

운영 파일·앱 데이터는 건드리지 않는다. 결과는 전부 pipeline/stage_r1b_run/ 에만 쓴다. 네이버 API 는 쓰지 않는다(후보 신호는 stage_r1_run 에 저장된 값).

단계 (각 단계 결과는 파일로 저장돼 있어 끊겨도 같은 명령으로 이어 갈 수 있다)
  prep   교체 적용 + 대상 이름표(alias) 보강 + 기존 글 전체 모으기 + 후보 풀 만들기 + 판정 질문 만들기   (Claude·API 없음)
  judge  Claude(sonnet) 한 번으로 '중복/각도 다름' 판정 → judge_result.json 저장 (있으면 다시 안 부름, --force 로 다시)
  fill   판정을 통과한 것만 빈자리에 R1 순으로 채움 → fill.json                                          (Claude·API 없음)
  plan   새로 들어온 글·각도를 바꾼 글만 기획(본문 설계·제목) 호출 1번 + 제목 고르기                       (Claude)
  build  최종 계획 plan_20261116.json · 판정표·보고표 만들기                                               (Claude·API 없음)

사용법: python tools/r1b.py prep | judge | fill | plan | build
"""
import argparse
import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_posts as pp  # noqa: E402
import r1 as r1m  # noqa: E402
from safe_io import write_text_retry  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "pipeline" / "stage_r1_run"
D = BASE / "pipeline" / "stage_r1b_run"
MONDAY = date(2026, 11, 16)

# ---------- 사용자와 정한 교체 ----------
NAVER_REMOVE = ["방정리", "장난감정리", "책상정리", "창문방풍막", "수도열선", "김장김치통", "창문실리콘곰팡이", "방풍테이프", "가습기"]
NAVER_PROMOTE = ["김장", "패딩냄새", "현관문결로"]            # 예비 → 일정 (순서대로 빈 일정에 들어감)
WP_REMOVE = ["거실방풍비닐", "침대전기장판"]
WP_PROMOTE = ["핫팩파우치", "직물스노우체인"]
ANGLE_V1 = {   # 1차 판정에 쓴 각도 설명(기록용)
    "슈퍼싱글난방텐트": "침대 치수(슈퍼싱글 침대 크기에 맞는 텐트 규격 재기)와 설치 방법 중심. 난방텐트 고르는 법·전기요금·비교 중심 글은 이미 있으니 피한다.",
    "화장실동파방지": "변기·욕실 배관(세면대·변기 급수관, 욕실 벽 속 배관)이 어는 것을 막는 내용만. 수도계량기·보일러·열선 일반 동파 방지는 이미 쓴 글이 있으니 다루지 않는다.",
    "현관문결로": "현관문 단열(문틀 틈·문 표면 결로 줄이기)과 도어락(번호키·배터리가 차가워 생기는 문제) 중심. 집 전체 결로·곰팡이 일반론은 이미 쓴 글이 있으니 피한다.",
}
ANGLE = {   # 2차 판정·기획 호출에 쓰는 구체화한 각도 (사용자와 정한 방향 그대로, '고르기'가 아닌 질문으로 좁힘)
    "슈퍼싱글난방텐트": "슈퍼싱글 침대(너비 약 110~120cm) 위에 난방텐트를 직접 세우는 설치 순서(폴대 조립·고정·전선 정리·환기)와 침대 치수에 맞는 텐트 규격 맞추기. '어떤 난방텐트를 고를까'(종류 비교·고르는 기준)는 다루지 않는다.",
    "화장실동파방지": "변기 수조·변기 급수 호스·세면대 아래 배관, 이 세 곳만 한파 전날 하는 보온·물 빼기 순서와 이미 얼었을 때 변기 해동법. 보온재·열선·보일러·수도계량기 일반 동파 방지는 다루지 않는다.",
    "현관문결로": "현관문 안쪽에 물이 맺혀 도어락(번호키) 배터리·터치 반응이 먹통이 될 때의 점검과 조치(건조·배터리 교체·문틀 틈 확인). 문풍지·단열재 고르기나 집 전체 결로·곰팡이 일반론은 다루지 않는다.",
}
TITLE_REDO = {   # 싼세계: 제목을 '전기요금 계산'이 아닌 예비(대체) 제목으로
    "사무실발난로": "전기요금 계산", "1인용전기장판": "전기요금 계산", "임산부전기장판": "전기요금 계산"}
RESTRICTED_GROUPS = ["방풍비닐", "온수매트", "전기장판", "동파방지열선", "난방텐트", "김치통", "가습기", "결로", "이불", "패딩"]    # 이미 쓴 글의 대상

# 같은 물건의 다른 이름 {다른 이름: 대표 이름} — 운영 target_alias.json 은 그대로 두고 stage_r1b_run/index 의 복사본에만 넣는다
ALIAS_ADD = {
    "방풍막": "방풍비닐", "방한비닐": "방풍비닐", "창문비닐": "방풍비닐",
    "수도열선": "동파방지열선", "수도동파방지열선": "동파방지열선", "동파방지히터": "동파방지열선",
    "틈새막이": "외풍막이", "방풍테이프": "외풍막이", "문풍지": "외풍막이",       # 바람막이→외풍막이 는 이미 있음
    "웃풍차단": "외풍차단", "우풍차단": "외풍차단", "우풍방지": "외풍차단",
    "방한텐트": "난방텐트", "침대텐트": "난방텐트",
    "전기요": "전기장판",
    "김장통": "김치통", "김치용기": "김치통",
    "손난로": "핫팩", "다운점퍼": "패딩", "에어캡": "단열뽁뽁이",
    "창틈이": "외풍막이", "창틈막이": "외풍막이",       # 창틈이 = 창틈막이 오타 표기(상위 글이 전부 창문 틈막이·바람막이)
    "오일히터": "라디에이터", "열풍기": "온풍기", "팬히터": "온풍기", "성에": "결로",
}
# 낱말 그룹(대표 → 낱말들)에 더 넣을 말: 글자 안에 이 낱말이 있으면 그 물건으로 본다
EXTRA_VOCAB = ["방풍비닐", "난방텐트", "전기장판", "온수매트", "핫팩", "난로", "온풍기", "라디에이터", "가습기", "제습기", "결로", "곰팡이", "동파",
               "열선", "김장", "김치통", "이불", "패딩", "바람막이", "단열", "뽁뽁이", "스노우체인", "외풍", "히터", "커튼", "보온", "배관", "문풍지",
               "도어락", "현관문", "샷시", "창문", "베란다", "실외기"]
OBJ_ALIAS_REP = {"핫팩": "핫팩"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def nospace(t):
    return re.sub(r"\s+", "", str(t or "")).lower()


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8-sig"))


def jsave(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    write_text_retry(Path(p), json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


# ---------- 낱말 그룹 ----------
def load_alias():
    a = dict(jload(SRC / "index" / "target_alias.json").get("alias") or {})
    a.update(ALIAS_ADD)
    return a


def groups_from_alias(alias):
    """대표 이름 → {대표 + 그 대표를 가리키는 이름들} (낱말은 공백 없이)"""
    g = {}
    for k, v in alias.items():
        g.setdefault(nospace(v), {nospace(v)}).add(nospace(k))
    return g


def vocab_groups(alias):
    """글자 속에서 찾을 낱말 → 같은 물건으로 보는 낱말 집합"""
    g = groups_from_alias(alias)
    out = {}
    for rep, mem in g.items():
        for m in mem:
            out[m] = set(mem)
    for v in EXTRA_VOCAB:
        out.setdefault(nospace(v), {nospace(v)})
    return out


def terms_of(text, vg):
    """글자(공백 없앰)에 들어 있는 낱말의 그룹들을 합친 집합. 긴 낱말부터 보고, 다른 낱말 안에 든 짧은 낱말은 건너뛴다."""
    t = nospace(text)
    found, hit = set(), []
    for w in sorted(vg, key=len, reverse=True):
        if len(w) >= 2 and w in t and not any(w in h for h in hit):
            hit.append(w)
            found |= vg[w]
    return found, hit


def bigrams(t):
    t = nospace(t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def sim(a, b):
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / len(A | B) if A and B else 0.0


# ---------- 기존 글 전체 ----------
def existing_titles():
    """pipeline/post_index.json 의 글(발행·대기·지난 기획) + pipeline/existing_keywords.json 의 발행·예약·대기 글. 제목(공백 없앤 것)으로 겹침 제거."""
    seen, out = {}, []

    def add(title, src):
        title = " ".join(str(title or "").split())
        if not title:
            return
        k = nospace(re.sub(r"^\(지난 기획\)\s*", "", title))
        if k in seen:
            return
        seen[k] = 1
        out.append({"title": title, "src": src})
    for t, v in jload(BASE / "pipeline" / "post_index.json")["posts"].items():
        add(t, "색인:" + str(v.get("source")))
    try:
        for i in jload(BASE / "pipeline" / "existing_keywords.json")["items"]:
            add(i.get("title"), f"{i.get('platform')}:{i.get('status')}")
    except (OSError, ValueError, KeyError):
        pass
    return out


# ---------- 계획 읽기·교체 적용 ----------
def apply_swaps(plan):
    """교체 적용 → (naver 일정 목록, wp 일정 목록, 예비 목록, 빠진 목록). 항목은 plan['plans'] 의 dict 를 복사한 것."""
    plans = [dict(e) for e in plan["plans"]]
    by = {e["keyword"]: e for e in plans}
    for k in NAVER_REMOVE + NAVER_PROMOTE + WP_REMOVE + WP_PROMOTE + list(ANGLE):
        if k not in by:
            raise SystemExit(f"계획에 없는 키워드: {k}")
    removed = []
    freed_n = [(e["publish_date"], e["keyword"]) for e in plans if e["role"] == "scheduled" and e["platform"] == "naver" and e["keyword"] in NAVER_REMOVE]
    freed_w = [(e["publish_date"], e["keyword"]) for e in plans if e["role"] == "scheduled" and e["platform"] == "wordpress" and e["keyword"] in WP_REMOVE]
    keep = [e for e in plans if e["keyword"] not in NAVER_REMOVE + WP_REMOVE]
    removed = [e for e in plans if e["keyword"] in NAVER_REMOVE + WP_REMOVE]
    for k, (dt, _old) in zip(NAVER_PROMOTE, freed_n):
        e = by[k]
        e["role"], e["platform"], e["publish_date"] = "scheduled", "naver", dt
        e["weekday"] = pp.WEEKDAYS[date.fromisoformat(dt).weekday()]
        e["promoted_from_reserve"] = True
    for k, (dt, _old) in zip(WP_PROMOTE, freed_w):
        e = by[k]
        e["role"], e["platform"], e["publish_date"] = "scheduled", "wordpress", dt
        e["weekday"] = pp.WEEKDAYS[date.fromisoformat(dt).weekday()]
        e["promoted_from_reserve"] = True
    freed_left = {"naver": freed_n[len(NAVER_PROMOTE):], "wordpress": freed_w[len(WP_PROMOTE):]}
    return keep, removed, freed_left


def entry_label(e, i):
    pl = {"naver": "네", "wordpress": "싼"}.get(e["platform"], "예비")
    return f"P{i}"


def plan_text(e):
    """판정에 보여 줄 글 내용: 확정 제목이 있으면 제목(키워드), 각도 바꾼 글은 키워드 + 각도"""
    if e["keyword"] in ANGLE:
        return f"{e['keyword']} — 이번 글의 각도: {ANGLE[e['keyword']]}"
    t = e.get("final_title")
    return f"{t} (키워드: {e['keyword']})" if t else e["keyword"]


# ---------- 후보 풀 ----------
def build_pool(plan_kw, removed_kw, vg, existing, cfg):
    rk = jload(SRC / "ranking_r1.json")["ranking"]
    sl = jload(SRC / "shortlist.json")
    wp_info, wp_money = set(sl.get("wp_info") or []), set(sl.get("wp_money") or [])
    ex_nospace = [nospace(x["title"]) for x in existing]
    excl = set(plan_kw) | set(removed_kw)
    rows = []
    for r in rk:
        kw = r["keyword"]
        if kw in excl or not r.get("r1"):
            continue
        sg = r["r1"]
        if (r.get("monthly_total") or 0) < cfg["volume_min"]:
            continue
        if sg.get("shape") != "제철 물건" or not r1m.is_timed(sg, cfg):
            continue
        if any(w in nospace(kw) for w in map(nospace, pp.DEFAULT_BUY_WORDS)):
            continue
        found, hit = terms_of(kw, vg)
        rows.append({"row": r, "keyword": kw, "score": r1m.naver_score(sg, cfg), "volume": r.get("monthly_total") or 0,
                     "group": sorted(hit)[:1][0] if hit else None, "terms": found, "wp_info": kw in wp_info, "wp_money": kw in wp_money})
    rows.sort(key=lambda x: (-x["score"], -x["volume"]))
    restricted_members = set()
    for g in RESTRICTED_GROUPS:
        restricted_members |= vg.get(nospace(g), {nospace(g)})
    used_words = set()          # 기존 글(전체)에 이미 쓰인 '대상 낱말'
    for m in restricted_members:
        if any(m in x for x in ex_nospace):
            used_words.add(m)
    pool, per_group = [], {}
    for x in rows:
        f = nospace(x["keyword"])
        hit_restricted = [m for m in restricted_members if m in f]
        x["restricted"] = bool(hit_restricted)
        if hit_restricted and any(m in used_words for m in hit_restricted):
            x["skip"] = "이미 쓴 대상과 검색하는 말이 같음(" + ",".join(hit_restricted) + ")"
            continue
        g = x["group"] or f
        if per_group.get(g, 0) >= 2:
            x["skip"] = "같은 대상 후보가 이미 2개"
            continue
        per_group[g] = per_group.get(g, 0) + 1
        pool.append(x)
    return pool, rows


# ---------- prep ----------
def cmd_prep(args):
    D.mkdir(parents=True, exist_ok=True)
    plan = jload(SRC / "plan_20261116.json")
    cfg = jload(BASE / "tools" / "filter.json")["r1"]
    # 1) 대상 이름표 (복사본에만)
    (D / "index").mkdir(exist_ok=True)
    alias = load_alias()
    jsave(D / "index" / "target_alias.json", {"updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "alias": alias,
                                              "_note": "stage_r1_run 의 표에 R1b 보강(ALIAS_ADD)을 더한 복사본. 운영 pipeline/target_alias.json 은 그대로."})
    vg = vocab_groups(alias)
    # 2) 교체 적용
    keep, removed, freed_left = apply_swaps(plan)
    sched_n = sorted([e for e in keep if e["role"] == "scheduled" and e["platform"] == "naver"], key=lambda e: (e["publish_date"], e["keyword"]))
    sched_w = sorted([e for e in keep if e["role"] == "scheduled" and e["platform"] == "wordpress"], key=lambda e: (e["publish_date"], e["keyword"]))
    reserve = [e for e in keep if e["role"] == "reserve"]
    ordered = sched_n + sched_w + reserve
    existing = existing_titles()
    log(f"교체 후: 네이버 일정 {len(sched_n)} · 싼세계 일정 {len(sched_w)} · 예비 {len(reserve)} / 빈 자리 네이버 {len(freed_left['naver'])}, 싼세계 {len(freed_left['wordpress'])} / 기존 글 {len(existing)}개")
    # 3) 후보 풀
    plan_kw = [e["keyword"] for e in plan["plans"]]
    pool, all_rows = build_pool(plan_kw, [e["keyword"] for e in removed], vg, existing, cfg)
    pool_n = [x for x in pool][:48]
    log(f"후보 풀 {len(pool_n)}개 (R1 기준 통과 {len(all_rows)}개 중, 이미 쓴 대상·같은 대상 3번째 이후 제외)")
    # 4) 판정 입력
    ex_ns = [(nospace(re.sub(r"^\(지난 기획\)\s*", "", x["title"])), x) for x in existing]

    def related(text_kw, terms, k=8):
        """대상 낱말이 겹치는 기존 글(많이 겹치는 순) + 글자가 비슷한 글 몇 개"""
        cand = []
        for ns, x in ex_ns:
            s = sim(text_kw, ns)
            direct = any(t in ns for t in terms) if terms else False
            cand.append((direct, s, x))
        d = sorted([c for c in cand if c[0]], key=lambda c: -c[1])[:k]
        o = sorted([c for c in cand if not c[0]], key=lambda c: -c[1])[:2]
        seen, out = set(), []
        for _, s, x in d + o:
            if x["title"] not in seen:
                seen.add(x["title"])
                out.append(x["title"])
        return out

    items = []
    for i, e in enumerate(ordered, 1):
        terms, hit = terms_of(e["keyword"] + " " + (ANGLE.get(e["keyword"]) and e["keyword"] or ""), vg)
        peers = []
        for j, o in enumerate(ordered, 1):
            if o is e:
                continue
            ot, _ = terms_of(o["keyword"], vg)
            if terms & ot:
                peers.append({"id": f"P{j}", "text": plan_text(o)})
        items.append({"id": f"P{i}", "kind": "계획", "keyword": e["keyword"], "text": plan_text(e), "angle_changed": e["keyword"] in ANGLE,
                      "platform": e.get("platform") or "예비", "existing": related(nospace(e["keyword"]), terms), "plan_peers": peers})
    for n, x in enumerate(pool_n, 1):
        terms = x["terms"]
        peers = []
        for j, o in enumerate(ordered, 1):
            ot, _ = terms_of(o["keyword"], vg)
            if terms & ot:
                peers.append({"id": f"P{j}", "text": plan_text(o)})
        items.append({"id": f"C{n}", "kind": "후보", "keyword": x["keyword"], "text": x["keyword"], "restricted_object": x["restricted"],
                      "existing": related(nospace(x["keyword"]), terms), "plan_peers": peers})
    jsave(D / "prep.json", {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "removed": [e["keyword"] for e in removed], "freed_left": freed_left,
                            "keep_order": [e["keyword"] for e in ordered],
                            "pool": [{"id": f"C{n}", "keyword": x["keyword"], "score": x["score"], "volume": x["volume"], "group": x["group"],
                                      "restricted": x["restricted"], "wp_info": x["wp_info"], "wp_money": x["wp_money"]} for n, x in enumerate(pool_n, 1)],
                            "pool_skipped": [{"keyword": x["keyword"], "why": x["skip"]} for x in all_rows if x.get("skip")],
                            "existing_count": len(existing), "items": items})
    jsave(D / "plan_after_swaps.json", {"plans": keep})
    log("[prep] 저장: prep.json, plan_after_swaps.json, index/target_alias.json")


# ---------- judge ----------
JUDGE_PROMPT = """너는 생활 살림 블로그(네이버 블로그·워드프레스 두 곳)의 깐깐한 편집장이다. 새로 쓸 글이 이미 쓴 글과 겹치는지 판정한다. 도구는 쓰지 말고 바로 답해라.

[판정 기준] 같은 물건(또는 같은 문제)을 같은 질문(고르기·붙이기·없애기·설치하기·전기요금 같은 것)으로 다루면 '중복'. 물건이 같아도 질문이 다르면 '각도 다름'. 애매하면 '중복'으로 본다.
- 같은 물건의 다른 이름도 같은 물건이다(방풍막=방풍비닐, 수도열선=동파방지열선, 바람막이=틈새막이=방풍테이프=문풍지 등).
- 다른 장소·크기·재질·색만 바꾼 같은 질문(예: 거실 방풍비닐 고르기 vs 현관 방풍비닐 고르기)은 중복이다.
- 같은 문제를 푸는 다른 물건이라도 독자가 같은 질문으로 찾아오면 중복이다.

[입력] 항목마다 id, 종류, 내용(text), 겹칠 수 있는 '기존 글 제목' 목록(existing), 같은 계획 안의 비슷한 글(plan_peers: id 와 내용)이 있다.
- 종류가 '계획'이면 이미 일정·예비에 있는 글이고, text 는 확정 제목이다. 각도를 바꾼 글(angle_changed)은 text 의 '이번 글의 각도' 기준으로 판정한다.
- 종류가 '후보'이면 아직 제목이 없다. text 는 검색 키워드이며, 그 키워드로 검색하는 사람의 일반적인 질문을 기준으로 판정한다. restricted_object=true 이면 이미 많이 쓴 물건이므로 질문까지 분명히 달라야 '각도 다름'이다.
- plan_peers 와도 비교한다. 단 '계획' 종류끼리 겹치면 번호가 큰 쪽(뒤쪽 id)에만 '중복'을 매기고 번호가 작은 쪽은 매기지 않는다. '후보'는 plan_peers 와 겹치면 '중복'이다. 후보끼리는 비교하지 않는다.
- existing 안에 '(지난 기획)' 이 붙은 것은 지난주에 기획만 하고 아직 안 쓴 글이다. 기존 글처럼 비교한다.

[출력] 항목마다 한 줄. JSON 만 출력한다. 항목 순서와 id 를 그대로 지킨다.
{{"results": [{{"id": "P1", "keyword": "키워드", "verdict": "중복 또는 각도 다름 또는 겹침 없음", "closest": "가장 가까운 글 제목 하나(existing 또는 plan_peers 의 글, 제목 그대로. 정말 없으면 빈 문자열)", "closest_kind": "기존 또는 계획 또는 없음", "reason": "한 줄(40자 안팎)", "unfit": false}}]}}
- verdict 는 겹치는 글이 하나라도 같은 질문이면 '중복', 겹치는 글은 있지만 질문이 다 다르면 '각도 다름', 비슷한 글이 아예 없으면 '겹침 없음'.
- unfit 은 브랜드·모델명 구매, 지역 업체, 구매처 찾기, 집 살림과 무관한 주제이면 true, 아니면 false.

[항목]
{items}
"""


def cmd_judge(args):
    pr = jload(D / "prep.json")
    out = D / "judge_result.json"
    if out.exists() and not args.force:
        log("[judge] 저장된 판정이 있어 다시 묻지 않음 (--force 로 다시)")
        return
    items = [{k: v for k, v in it.items() if k in ("id", "kind", "text", "angle_changed", "restricted_object", "existing", "plan_peers")} for it in pr["items"]]
    prompt = JUDGE_PROMPT.format(items=json.dumps(items, ensure_ascii=False, separators=(",", ":")))
    write_text_retry(D / "judge_prompt.txt", prompt)
    log(f"[judge] 항목 {len(items)}개, 질문 {len(prompt)}자 → Claude(sonnet) 1회")
    env, res, err = None, None, None
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
    got = {r.get("id"): r for r in res}
    miss = [it["id"] for it in items if it["id"] not in got]
    u = env.get("usage") or {}
    jsave(out, {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "calls": 1, "cost_usd": env.get("total_cost_usd"),
                "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"), "missing": miss, "results": res})
    log(f"[judge] 저장: judge_result.json (비용 ${env.get('total_cost_usd')}, 빠진 항목 {len(miss)}개)")


def cmd_judge2(args):
    """1차 판정에서 중복으로 나왔지만 판정이 바뀔 수 있는 6편만 다시 판정(Claude 1번): 각도를 구체화한 3편 + 앞 번호 글이 중복으로 빠져 영향받은 3편."""
    pr = jload(D / "prep.json")
    r1 = {r["id"]: r for r in jload(D / "judge_result.json")["results"]}
    out = D / "judge_result2.json"
    if out.exists() and not args.force:
        log("[judge2] 저장된 판정이 있어 다시 묻지 않음")
        return
    byid = {it["id"]: it for it in pr["items"]}
    re_ids = [i for i, it in byid.items() if it["kind"] == "계획" and (it["angle_changed"] or i in ("P14", "P23", "P29"))]
    alive = {i for i, it in byid.items() if it["kind"] == "계획" and r1[i]["verdict"] != "중복" and i not in re_ids}
    items = []
    for i in re_ids:
        it = byid[i]
        txt = f"{it['keyword']} — 이번 글의 각도: {ANGLE[it['keyword']]}" if it["angle_changed"] else it["text"]
        peers = [p for p in it["plan_peers"] if p["id"] in alive or p["id"] in re_ids and p["id"] < i]
        items.append({"id": i, "kind": "계획", "text": txt, "angle_changed": it["angle_changed"], "existing": it["existing"], "plan_peers": peers})
    prompt = JUDGE_PROMPT.format(items=json.dumps(items, ensure_ascii=False, separators=(",", ":")))
    prompt = prompt.replace("[항목]\n", "[추가 안내] 이번 항목들은 1차 판정 뒤 다시 보는 것이다. plan_peers 에 있는 글은 이미 확정된 글(또는 앞 번호의 재판정 대상)이다.\n\n[항목]\n")
    write_text_retry(D / "judge_prompt2.txt", prompt)
    log(f"[judge2] 항목 {len(items)}개 → Claude(sonnet) 1회")
    env = pp.call_claude(prompt, "sonnet", "1")
    res = pp.extract_json(env.get("result", ""))["results"]
    u = env.get("usage") or {}
    jsave(out, {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "calls": 1, "cost_usd": env.get("total_cost_usd"),
                "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"), "ids": re_ids, "results": res})
    log(f"[judge2] 저장: judge_result2.json (비용 ${env.get('total_cost_usd')})")


# ---------- fill ----------
def verdicts():
    """키워드 id → 판정. 2차 판정이 있는 항목은 2차를 쓴다."""
    r = {x["id"]: dict(x, round=1) for x in jload(D / "judge_result.json")["results"]}
    p2 = D / "judge_result2.json"
    if p2.exists():
        for x in jload(p2)["results"]:
            r[x["id"]] = dict(x, round=2)
    return r


# 판정 뒤에 눈으로 확인해 뺀 후보(근거는 후보 상위 5개 글 제목)
MANUAL_DROP = {
    "창틈이": "창틈막이의 오타 표기라 '바람막이=틈새막이' 와 같은 물건 → 창틀바람막이 판정(기존 글 '문풍지 추천…'과 중복)과 같음. 별칭표에 추가",
    "열선필름": "상위 5개 글이 전부 자동차 썬팅·열선유리 글이라 집 살림 글이 아님",
}


def cmd_fill(args):
    pr = jload(D / "prep.json")
    v = verdicts()
    keep = {e["keyword"]: e for e in jload(D / "plan_after_swaps.json")["plans"]}
    ids = {it["keyword"]: it["id"] for it in pr["items"]}
    surv, dropped = [], []
    for kw in pr["keep_order"]:
        r = v[ids[kw]]
        (dropped if r["verdict"] == "중복" or r.get("unfit") else surv).append(kw)
    naver = [k for k in surv if keep[k]["role"] == "scheduled" and keep[k]["platform"] == "naver"]
    wp = [k for k in surv if keep[k]["role"] == "scheduled" and keep[k]["platform"] == "wordpress"]
    reserve = [k for k in surv if keep[k]["role"] == "reserve"]
    # 빈자리 채우기: 후보 풀을 R1 순서대로, 판정 통과한 것만. 같은 대상 낱말은 최대 2개.
    need_n = 21 - len(naver)
    fills, per_group = [], {}
    for c in pr["pool"]:
        r = v[c["id"]]
        if len(fills) >= need_n:
            break
        if r["verdict"] == "중복" or r.get("unfit") or c["keyword"] in MANUAL_DROP:
            continue
        g = c["group"] or c["keyword"]
        if per_group.get(g, 0) >= 2:
            continue
        per_group[g] = per_group.get(g, 0) + 1
        fills.append(c["keyword"])
    # 날짜: 살아남은 글은 날짜 그대로, 새 글은 하루 3편 한도 안에서 글이 적은 날부터
    days = [(MONDAY + timedelta(days=i)).isoformat() for i in range(7)]
    cnt = {d: sum(1 for k in naver if keep[k]["publish_date"] == d) for d in days}
    fill_date = {}
    for k in fills:
        d = min((d for d in days if cnt[d] < 3), key=lambda d: (cnt[d], d))
        cnt[d] += 1
        fill_date[k] = d
    st = {"naver": naver + fills, "wordpress": wp, "reserve": reserve, "fills": fills, "fill_date": fill_date, "dropped_dup": dropped, "dropped_manual": MANUAL_DROP,
          "removed_user": pr["removed"], "angle": list(ANGLE), "need_plan": fills + [k for k in ANGLE if k in surv],
          "shortage": {"naver": 21 - len(naver) - len(fills), "wordpress": 14 - len(wp), "reserve_naver": 5 - sum(1 for k in reserve if keep[k].get("r1_style") != "wordpress"),
                       "reserve_wordpress": 4 - sum(1 for k in reserve if keep[k].get("r1_style") == "wordpress")}}
    jsave(D / "fill.json", st)
    log(f"[fill] 네이버 {len(naver)}+새로 {len(fills)} / 싼세계 {len(wp)} / 예비 {len(reserve)} / 중복으로 뺀 {len(dropped)}편: {', '.join(dropped)}")
    log(f"       새로 채운 것: {', '.join(fills)} / 부족: {st['shortage']} / 기획 받을 글: {', '.join(st['need_plan'])}")


# ---------- plan ----------
def item_from_entry(e):
    """계획 항목 → build_summary·choose_titles 가 읽는 모양(옛 기획에 저장된 상위 글 제목·신호를 그대로 쓴다)"""
    posts = [{"title": p.get("title"), "chars": p.get("chars"), "images": p.get("images"), "age_days": p.get("age_days"),
              "influencer": p.get("influencer"), "official_badge": p.get("official")} for p in (e.get("top_posts") or [])]
    it = {k: e.get(k) for k in ("keyword", "platform", "role", "track", "timing", "related_keywords", "smart_blocks", "r1", "r1_style",
                                "monthly_total", "saturation", "score", "pair_with", "publish_date", "weekday", "seed", "fingerprint",
                                "kw_type", "platform_hint", "problem", "detail", "topic", "topic_group", "category", "grade",
                                "expected_clicks", "clicks_basis", "trend_growth", "trend_steady", "solves")}
    it["spelling_variants"] = e.get("variant_spellings")
    it["golden"] = {"top_posts": {"posts": posts}}
    return it


def ensure_index_copy():
    """색인 폴더 복사본(stage_r1b_run/index)에 빠진 파일을 stage_r1_run/index 에서 복사 (읽기 전용 복사, 운영 색인은 안 건드림)"""
    (D / "index").mkdir(exist_ok=True)
    for f in (SRC / "index").glob("*.json"):
        if not (D / "index" / f.name).exists():
            shutil.copy2(f, D / "index" / f.name)
    a = jload(D / "index" / "target_alias.json")
    a["alias"].update(ALIAS_ADD)
    jsave(D / "index" / "target_alias.json", a)


NEW_NOTE = {"열선센서": "집 수도·배관 동파방지열선에 다는 온도 센서(과열방지 센서형 열선) 중심. 자동차 열선핸들·전방센서·열선 시공 업체 글은 다루지 않는다."}


def cmd_plan(args):
    import os
    ensure_index_copy()
    os.environ["LOCALAPP_INDEX_DIR"] = str(D / "index")
    st = jload(D / "fill.json")
    pr = jload(D / "prep.json")
    byid = {it["keyword"]: it for it in pr["items"]}
    keep = {e["keyword"]: e for e in jload(D / "plan_after_swaps.json")["plans"]}
    w = pp.load_weekly(BASE / "tools" / "filter.json")
    cfg = w["r1"]
    rk = {r["keyword"]: r for r in jload(SRC / "ranking_r1.json")["ranking"]}
    out_path = D / "plan_step.json"
    if out_path.exists() and not args.force:
        log("[plan] 저장된 기획이 있어 다시 호출하지 않음 (--force 로 다시)")
        return
    items = []
    for kw in st["naver"] + st["wordpress"] + st["reserve"]:
        if kw in keep:
            e = keep[kw]
            it = item_from_entry(e)
        else:
            r = dict(rk[kw])
            it = {k: r.get(k) for k in ("keyword", "seed", "score", "monthly_total", "saturation", "trend_growth", "trend_steady", "smart_blocks",
                                        "related_keywords", "kw_type", "platform_hint", "fingerprint", "track", "grade", "golden", "r1")}
            it.update(platform="naver", role="scheduled", track="info", r1_style="naver", publish_date=st["fill_date"][kw],
                      weekday=pp.WEEKDAYS[date.fromisoformat(st["fill_date"][kw]).weekday()], found_date="2026-11-14", from_bank=False)
            pp.attach_clicks([it], w["click"])
            it["timing"] = r1m.label_of(r["r1"], cfg)
        if kw in ANGLE or kw in NEW_NOTE:
            avoid = byid[kw]["existing"][:8] + [p["text"] for p in byid[kw].get("plan_peers", [])][:4]
            it["r1_note"] = ("이번 글의 각도(반드시 지킬 것): " + (ANGLE.get(kw) or NEW_NOTE[kw]) + " / 이미 쓴 글·계획의 글(제목과 질문이 겹치지 않게 피할 것): " + " | ".join(avoid))
        items.append(it)
    need = st["need_plan"]
    # 각도를 바꾼 글은 저장돼 있던 제목 후보·의도 판정을 지워 새로 받는다
    tc_path = D / "title_cache_20261116.json"
    if not tc_path.exists():
        shutil.copy2(SRC / "title_cache_20261116.json", tc_path)
        shutil.copy2(SRC / "intent_info_cache.json", D / "intent_info_cache.json")
        tc = jload(tc_path)
        for k in ANGLE:
            tc["titles"].pop(k, None)
        jsave(tc_path, tc)
        ic = jload(D / "intent_info_cache.json")
        for k in ANGLE:
            ic.pop(k, None)
        jsave(D / "intent_info_cache.json", ic)
    title_rules = pp.ttl.load_rules(BASE / "tools" / "filter.json")
    guide = pp.title_guide(title_rules["forbidden_words"], pp.ttl.style_samples(pp.ttl.own_titles()))
    title_rules["r1_wp"] = w["r1"]["wordpress"]
    guide += pp.R1_WP_TITLE_GUIDE.format(words=", ".join(w["r1"]["wordpress"]["title_search_words"][:12]))
    problems, batches, claude_by_kw = [], [], {}
    chunk = [it for it in items if it["keyword"] in need]
    log(f"[plan] 기획 호출 1번: 새로 들어온 글·각도 바꾼 글 {len(chunk)}편 ({', '.join(i['keyword'] for i in chunk)})")
    plans, env, probs = pp.plan_batch(pp.build_summary(chunk, title_rules), guide)
    problems += probs
    if env is None:
        raise SystemExit("기획 호출 실패: " + "; ".join(probs))
    u = env.get("usage") or {}
    batches.append({"batch": 1, "keywords": len(chunk), "total_cost_usd": env.get("total_cost_usd"), "num_turns": env.get("num_turns"),
                    "duration_ms": env.get("duration_ms"), "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")})
    for p_ in plans:
        claude_by_kw[p_.get("keyword")] = p_
    jsave(D / "plan_call_raw.json", {"plans": plans})
    title_res = pp.choose_titles(items, claude_by_kw, D, MONDAY, title_rules, guide, batches, problems)
    ok = [b for b in batches if not b.get("failed")]
    jsave(out_path, {"made_at": datetime.now().astimezone().isoformat(timespec="seconds"), "claude_by_kw": claude_by_kw, "title_res": title_res,
                     "batches": batches, "problems": problems,
                     "usage": {"calls": len(batches), "cost_usd": round(sum(b.get("total_cost_usd") or 0 for b in ok), 4)}})
    log(f"[plan] 저장: plan_step.json (호출 {len(batches)}회, ${sum(b.get('total_cost_usd') or 0 for b in ok):.4f}, 문제 {len(problems)}건)")
    for pr_ in problems:
        log(f"  [주의] {pr_}")


# ---------- build ----------
def related_titles(kw_ns, terms, ex_ns, k=8):
    cand = [(any(t in ns for t in terms) if terms else False, sim(kw_ns, ns), x) for ns, x in ex_ns]
    d = sorted([c for c in cand if c[0]], key=lambda c: -c[1])[:k]
    o = sorted([c for c in cand if not c[0]], key=lambda c: -c[1])[:2]
    seen, out = set(), []
    for _, _s, x in d + o:
        if x["title"] not in seen:
            seen.add(x["title"])
            out.append(x["title"])
    return out


def cmd_build(args):
    ensure_index_copy()
    st, ps = jload(D / "fill.json"), jload(D / "plan_step.json")
    keep = {e["keyword"]: e for e in jload(D / "plan_after_swaps.json")["plans"]}
    w = pp.load_weekly(BASE / "tools" / "filter.json")
    rk = {r["keyword"]: r for r in jload(SRC / "ranking_r1.json")["ranking"]}
    tr, cbk = ps["title_res"], ps["claude_by_kw"]
    entries = []
    for role_key, plat in (("naver", "naver"), ("wordpress", "wordpress"), ("reserve", None)):
        for kw in st[role_key]:
            if kw in keep:
                e = dict(keep[kw])
            else:
                r = rk[kw]
                it = {k: r.get(k) for k in ("keyword", "seed", "score", "monthly_total", "saturation", "trend_growth", "trend_steady",
                                            "smart_blocks", "related_keywords", "kw_type", "platform_hint", "fingerprint", "grade", "golden", "r1")}
                pp.attach_clicks([it], w["click"])
                dt = st["fill_date"][kw]
                e = {"platform": "naver", "role": "scheduled", "publish_date": dt,
                     "weekday": pp.WEEKDAYS[date.fromisoformat(dt).weekday()], "keyword": kw, "seed": r.get("seed"), "score": r.get("score"),
                     "fingerprint": None, "kw_type": None, "platform_hint": None, "track": "info", "r1": r["r1"], "r1_style": "naver",
                     "expected_clicks": it.get("expected_clicks"), "clicks_basis": it.get("clicks_basis"),
                     "timing": r1m.label_of(r["r1"], w["r1"]), "monthly_total": r.get("monthly_total"), "saturation": r.get("saturation"),
                     "trend_growth": r.get("trend_growth"), "trend_steady": r.get("trend_steady"), "smart_blocks": r.get("smart_blocks"),
                     "related_keywords": r.get("related_keywords"), "new_in_r1b": True, "top_posts": pp.top_posts_brief(r)}
            e["platform"], e["role"] = plat, ("reserve" if plat is None else "scheduled")
            if plat is None:
                e["publish_date"] = e["weekday"] = None
            c = cbk.get(kw)
            if c:
                for k_ in ("search_intent", "gaps", "outline", "experience_slots", "beat_top", "target_chars", "target_images"):
                    e[k_] = c.get(k_)
                if kw in ANGLE:
                    e["angle"] = ANGLE[kw]
            e.update({k_: v_ for k_, v_ in tr[kw].items() if k_ != "retried"})
            entries.append(e)
    # 싼세계 제목 교체: '전기요금 계산'이 아닌 예비 제목으로
    redo = []
    bad = lambda t: bool(re.search("전기요금|전기세", t))
    for e in entries:
        if e["keyword"] in TITLE_REDO and e.get("final_title"):
            options = [t for t in list(e["alt_titles"]) + [c["title"] for c in e["title_scores"] if c.get("ok")] if not bad(t)]
            if bad(e["final_title"]) and options:
                old, new = e["final_title"], options[0]
                e["final_title"] = new
                sc = next((c for c in e["title_scores"] if c["title"] == new), {})
                e["title_keyword"], e["title_pattern"] = sc.get("title_keyword", e["title_keyword"]), sc.get("pattern", e["title_pattern"])
                rest = [t for t in dict.fromkeys(options) if t != new][:2]
                e["alt_titles"] = rest
                e["alt_patterns"] = [next((c.get("pattern") for c in e["title_scores"] if c["title"] == t), None) for t in rest]
                redo.append({"keyword": e["keyword"], "old": old, "new": new})
            else:
                redo.append({"keyword": e["keyword"], "old": e["final_title"], "new": e["final_title"], "note": "원래 제목이 전기요금 계산이 아니라 그대로 둠"})
    jsave(D / "title_redo.json", redo)
    # 최종 확인 판정(Claude 1번): 최종 제목 기준으로 기존 글 전체·계획 안 글끼리 다시 대조
    alias = jload(D / "index" / "target_alias.json")["alias"]
    vg = vocab_groups(alias)
    existing = existing_titles()
    ex_ns = [(nospace(re.sub(r"^\(지난 기획\)\s*", "", x["title"])), x) for x in existing]
    for i, e in enumerate(entries, 1):
        e["_id"] = f"F{i}"
    items = []
    for e in entries:
        terms, _h = terms_of(e["keyword"] + " " + (e.get("final_title") or ""), vg)
        peers = []
        for o in entries:
            if o is e:
                continue
            ot, _h2 = terms_of(o["keyword"] + " " + (o.get("final_title") or ""), vg)
            if terms & ot:
                peers.append({"id": o["_id"], "text": f"{o.get('final_title')} (키워드: {o['keyword']})"})
        items.append({"id": e["_id"], "kind": "계획", "text": f"{e.get('final_title')} (키워드: {e['keyword']})", "angle_changed": False,
                      "existing": related_titles(nospace(e["keyword"]), terms, ex_ns), "plan_peers": peers})
    fj = D / "judge_final.json"
    if not fj.exists() or args.force:
        prompt = JUDGE_PROMPT.format(items=json.dumps(items, ensure_ascii=False, separators=(",", ":")))
        write_text_retry(D / "judge_prompt_final.txt", prompt)
        log(f"[최종 확인] 항목 {len(items)}개 -> Claude(sonnet) 1회")
        env = pp.call_claude(prompt, "sonnet", "1")
        res = pp.extract_json(env.get("result", ""))["results"]
        jsave(fj, {"calls": 1, "cost_usd": env.get("total_cost_usd"), "results": res})
    fr = {r["id"]: r for r in jload(fj)["results"]}
    final, dropped = [], []
    for e in entries:
        r = fr.get(e["_id"]) or {}
        e["final_check"] = {k: r.get(k) for k in ("verdict", "closest", "closest_kind", "reason")}
        (dropped if r.get("verdict") == "중복" or r.get("unfit") else final).append(e)
    for e in entries:
        e.pop("_id", None)
    sched_n = [e for e in final if e["role"] == "scheduled" and e["platform"] == "naver"]
    sched_w = [e for e in final if e["role"] == "scheduled" and e["platform"] == "wordpress"]
    res_ = [e for e in final if e["role"] == "reserve"]
    rec = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "week": {"start": "2026-11-16", "end": "2026-11-22"},
           "source": "stage_r1_run/plan_20261116.json 에서 R1b 중복 보정", "counts": {"naver": len(sched_n), "wordpress": len(sched_w), "reserve": len(res_)},
           "dropped_in_final_check": [{"keyword": e["keyword"], "check": e["final_check"]} for e in dropped],
           "removed_by_user_choice": st["removed_user"], "dropped_by_judge": st["dropped_dup"], "dropped_manual": st["dropped_manual"],
           "shortage": {"naver": 21 - len(sched_n), "wordpress": 14 - len(sched_w), "reserve": 9 - len(res_)},
           "title_redo": redo, "problems": ps["problems"], "plans": final}
    jsave(D / "plan_20261116.json", rec)
    log(f"[build] 네이버 {len(sched_n)} / 싼세계 {len(sched_w)} / 예비 {len(res_)} / 최종 확인에서 뺀 {len(dropped)}편")
    L = ["| 계획 키워드 | 겹치는 기존 글 제목 | 판정 | 이유 |", "|---|---|---|---|"]
    for e in entries:
        c = e["final_check"]
        L.append(f"| {e['keyword']} | {(c.get('closest') or '-').replace('|', '/')} | {c.get('verdict')} | {(c.get('reason') or '').replace('|', '/')} |")
    jt = ["| 플랫폼 | 발행일 | 키워드 | 추세 비율 | 정점까지 주 | 확정 제목 | 가장 가까운 기존 글 제목 |", "|---|---|---|---|---|---|---|"]
    order = sorted(final, key=lambda e: ({"naver": 0, "wordpress": 1}.get(e["platform"], 2), e["publish_date"] or "9", e["keyword"]))
    for e in order:
        sg = e.get("r1") or {}
        pl = {"naver": "네이버", "wordpress": "싼세계"}.get(e["platform"], "예비")
        jt.append(f"| {pl} | {e['publish_date'] or '예비'} | {e['keyword']} | {sg.get('ratio') if sg.get('ratio') is not None else '-'} | "
                  f"{sg.get('peak') if sg.get('peak') is not None else '-'} | {e['final_title']} | {(e['final_check'].get('closest') or '-').replace('|', '/')} |")
    write_text_retry(D / "report.md", "## 보고표\n\n" + "\n".join(jt) + "\n\n## 판정표(최종 제목 기준)\n\n" + "\n".join(L) + "\n")
    log("[build] 저장: report.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="R1 중복 보정")
    ap.add_argument("cmd", choices=["prep", "judge", "judge2", "fill", "plan", "build"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    {"prep": cmd_prep, "judge": cmd_judge, "judge2": cmd_judge2, "fill": cmd_fill, "plan": cmd_plan, "build": cmd_build}.get(a.cmd, lambda x: sys.exit("아직 안 만든 단계"))(a)
