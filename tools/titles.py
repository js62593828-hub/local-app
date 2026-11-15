# -*- coding: utf-8 -*-
"""4단계 제목 규칙 채점 (Claude 없이, 코드만).

글마다 Claude 가 준 제목 후보(title, title_keyword)를 필수 규칙으로 걸러내고, 통과한 후보는 가점 합으로 순위를 매긴다.
기준값은 모두 tools/filter.json 의 title_rules 에 있다 (추정값 — 5단계 실제 클릭률로 보정).

필수(어기면 탈락)
  no_keyword / kw_mismatch / too_long / forbidden / brand / similar_top / similar_own
가점
  keyword_front / mobile_core / number_or_result / problem_hint / season_word / pick_criteria

사용: titles.rank(cands, ctx, rules) → {"ranked": [...], "passed": n, "scores": [...]}
시험: python tools/titles.py --selftest   (tests/title_cases.json 으로 규칙이 위반을 전부 잡는지 확인)
"""
import json
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spelling  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
FILTER = BASE / "tools" / "filter.json"
CASES = BASE / "tests" / "title_cases.json"
SYNONYMS = {"씽크대": "싱크대", "헹거": "행거", "렉": "랙", "쇼파": "소파", "후라이팬": "프라이팬", "악세사리": "액세서리",
            "스웨이드": "세무", "화장실": "욕실", **spelling.SPELL_UNIFY}

FAIL_KO = {"no_keyword": "제목에 키워드 표기가 그대로 없음", "kw_mismatch": "title_keyword 가 키워드와 다름",
           "too_long": "길이 초과", "forbidden": "과장·낚시 금지어", "brand": "브랜드·회사명",
           "similar_top": "상위 5개 글 제목과 거의 같음", "similar_own": "내 기존 글 제목과 거의 같음",
           "no_pick": "수익글인데 고르는 기준(고르는·추천·비교·종류·차이·가격·~용 등)이 제목에 없음",
"off_intent": "검색 의도(intent)에서 벗어난 제목",
           "awkward": "키워드 바로 뒤에 조사·쉼표 없이 명사가 이어져 문장이 어색함", "unnatural": "사람이 쓴 제목처럼 자연스럽지 않다는 검수 판정",
           "no_target": "뜻이 여러 개인 키워드인데 제목에 의도의 대상(예: 음식)이 안 드러남",
           "wp_not_front": "R1 싼세계: 키워드가 제목 맨 앞에 그대로 있지 않음", "wp_mistake": "R1 싼세계: 실수·주의형(손해·헛수고 등) 제목은 쓰지 않음",
           "wp_no_search": "R1 싼세계: 실제 검색하는 말(방법·추천·비교·종류·후기 등)이 제목에 없음"}

_MISTAKE_RE = re.compile(r"(?:하면|면|으면)\s*(?:손해|헛수고|낭패|후회|위험|망해|망쳐|실패)|헛수고|손해|후회|낭패|실수")

# 4.5단계: 제목 틀 6가지 (후보마다 pattern 칸)
PATTERNS = ["순서형", "실수·손해형", "비교형", "상황형", "숫자·결과형", "질문형"]
# 4.6단계: 틀은 코드가 제목 글자를 보고 정한다(Claude 가 적은 pattern 은 참고만). 위에서부터 먼저 맞는 틀.
_UNITS = r"(?:분|시간|원|곳|가지|개|단|회|번|일|초|도|℃|%|배|cm|mm|m|kg|L|평|칸|줄|명)"
_PAT_RE = [
    ("비교형", re.compile(r"vs|VS|차이|비교")),
    ("순서형", re.compile(r"순서|단계|먼저|부터")),
    ("실수·손해형", re.compile(r"실수|후회|손해|안 ?맞아요|실패")),
    ("질문형", re.compile(r"\?\s*$|[가-힣]까요?\s*$")),
    ("상황형", re.compile(r"[가-힣]\s?때(?![가-힣])|했는데|했더니|[가-힣](?:라면|다면)(?![가-힣])|(?:^|\s)후(?![가-힣])")),
    ("숫자·결과형", re.compile(r"\d\s?~?\d*\s?" + _UNITS)),
]
_N_GAJI = re.compile(r"(\d+)\s*가지")


def load_rules(path=None) -> dict:
    p = Path(path) if path else FILTER
    return json.loads(p.read_text(encoding="utf-8-sig"))["title_rules"]


def norm(s: str) -> str:
    t = "".join(re.sub(r"<[^>]+>", "", s or "").split()).lower()
    for a, b in SYNONYMS.items():
        t = t.replace(a, b)
    return t


def similarity(a: str, b: str) -> float:
    """두 제목의 닮은 정도 0~1: 띄어쓰기·대소문자를 없애고 표기를 통일한 뒤 SequenceMatcher 비율 (filter.json similarity_method)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    return 1.0 if na == nb else SequenceMatcher(None, na, nb).ratio()


def max_len_of(platform, rules: dict) -> int:
    ml = rules["max_len"]
    if platform in ml:
        return int(ml[platform])
    return int(min(ml.values()))        # reserve(플랫폼 미정)는 짧은 쪽에 맞춘다


def _brand_hit(title: str, keyword: str, rules: dict):
    t, kw = title.replace(" ", "").lower(), (keyword or "").replace(" ", "").lower()
    for b in rules["brand_words"]:
        bl = b.replace(" ", "").lower()
        if not bl or bl in kw:
            continue            # 키워드 자체에 든 말은 어쩔 수 없다
        if re.fullmatch(r"[a-z0-9]+", bl):
            if re.search(r"(?<![a-z0-9])" + re.escape(bl) + r"(?![a-z0-9])", t):
                return b
        elif bl in t:
            return b
    m = re.search(rules["model_regex"], title.replace(keyword or "", " "))
    return m.group(0) if m else None


def pattern_of(title: str) -> str:
    """제목 틀을 코드로 정한다(같은 제목이면 항상 같은 틀). 비교 > 순서 > 실수·손해 > 질문 > 상황 > 숫자·결과 순으로 먼저 맞는 것.
    아무것도 안 맞으면: 숫자가 있으면 숫자·결과형, 없으면 순서형."""
    t = (title or "").strip()
    for name, rx in _PAT_RE:
        if rx.search(t):
            return name
    return "숫자·결과형" if re.search(r"\d", t) else "순서형"


def n_gaji(title: str):
    """제목의 'N가지' 숫자(문자열), 없으면 None."""
    m = _N_GAJI.search(title or "")
    return m.group(1) if m else None


# ---- 4.6단계: 키워드 바로 뒤가 어색한 제목 거르기 ----
_PARTICLE_END = ("에서", "에는", "에도", "으로", "까지", "부터", "보다", "처럼", "마다", "에", "로", "을", "를", "이", "가", "은", "는", "의", "도", "만", "와", "과")
_NEXT_OK = {"순서", "방법", "비용", "가격", "기준", "후기", "팁", "정리", "체크", "요령", "원인", "시기", "주기", "비교", "차이", "추천", "종류",
            "전", "후", "뒤", "때", "시", "중", "안", "전에", "후에", "뒤에", "전엔", "방식", "과정", "총정리", "가이드", "체크리스트", "단계",
            "먼저", "바로", "꼭", "이렇게", "그대로", "미리", "한", "두", "세", "네", "더", "또", "안전", "수칙", "시간", "횟수", "범위", "한도",
            "해결", "해결법", "예방", "관리", "사용법", "고르기", "선택", "정답", "공식", "순", "잘", "쉽게", "직접", "셀프", "업체", "vs", "VS",
            "후회", "실수", "손해", "고민", "걱정", "그리고", "또는", "및", "대신", "말고", "아니라", "하나", "둘", "셋", "몇", "얼마", "언제", "어디", "무엇", "왜", "어떻게", "이", "그", "저"}


_ACTION_END = ("제거", "교체", "설치", "청소", "방지", "차단", "해결", "보수", "수리", "정리", "관리", "세척", "예방", "사용", "구매", "구입",
               "보관", "시공", "철거", "점검", "확인", "고정", "소독", "퇴치", "보강", "정비", "건조", "탈취", "방수", "단열", "충전", "추천", "비교")


def awkward_after_keyword(title: str, tk: str) -> bool:
    """키워드(title_keyword)가 동작 이름(제거·교체·설치 등)으로 끝나는데 바로 뒤에 쉼표·조사 없이 명사가 이어지면 True (어색한 끼워 넣기).
    키워드에 글자가 바로 붙은 경우(조사·어미: '교체를', '제거는')나 쉼표·문장부호·끝은 정상.
    띄어 쓴 다음 낱말이 허용 목록(순서·방법·비용·전·후 등)이거나, 동사·형용사로 보이는 형태(…ㄴ/ㄹ 받침으로 끝나는 관형형, …요/다/까/면/고/게/지/기)가 아니면 어색."""
    t, k = title or "", (tk or "").strip()
    i = t.find(k) if k else -1
    if i < 0:
        return False
    rest = t[i + len(k):]
    if not rest or not rest.startswith(" "):
        return False
    if not k.endswith(_ACTION_END):
        return False                    # 키워드가 '제거·교체·설치' 같은 동작 이름으로 끝나 문장이 이미 끝난 꼴일 때만 본다 (명사로 끝나면 '곰팡이 + 제거 순서'처럼 이어 쓸 수 있음)
    m = re.match(r"\s+([^\s,.?!~·:;()\[\]'\"]+)", rest)
    if not m:
        return False
    w = m.group(1)
    if not re.fullmatch(r"[가-힣]+", w):
        return False                    # 숫자·영문·기호로 시작하면 명사 이어붙이기가 아님 (예: '3분', 'vs')
    if w in _NEXT_OK or any(w.startswith(x) for x in ("셀프", "직접", "업체", "해", "했", "하", "되", "된", "될", "돼", "됐", "한", "할")):
        return False
    last = w[-1]
    code = ord(last) - 0xAC00
    jong = code % 28 if 0 <= code < 11172 else 0
    if jong in (4, 8):                  # ㄴ, ㄹ 받침: 한·할·볼·쓸·된·잡을… 관형형으로 본다
        return False
    if any(w.endswith(x) for x in ("에서", "에는", "에도", "으로", "까지", "부터", "보다", "처럼", "마다")) or last in "에를로":
        return True                     # 명사 + 조사 (방에서·락스로)
    if last in "요다까면고게지기며죠":
        return False
    return True


def has_pick(title: str, rules: dict) -> bool:
    """수익글 제목의 '고르는 기준' 표현 유무: money_pick_words 또는 '~용'(사용·활용 같은 말 제외)."""
    t = title or ""
    if any(w in t for w in rules.get("money_pick_words", [])):
        return True
    ex = re.escape(rules.get("money_pick_yong_exclude", ""))
    return bool(re.search(r"(?:(?![" + ex + r"])[가-힣])용(?![가-힣])", t)) if ex else bool(re.search(r"[가-힣]용(?![가-힣])", t))


def is_unspaced(title: str, tk: str, rules: dict) -> bool:
    """제목의 키워드 표기가 사람이 읽는 띄어쓰기가 아님: title_keyword 가 제목에 글자 그대로 없거나, 붙여 쓴 4글자 이상 낱말(한 낱말 예외 목록 제외)."""
    tk = (tk or "").strip()
    if not tk:
        return False
    if tk not in (title or ""):
        return True
    return bool(re.fullmatch(r"[가-힣A-Za-z0-9]{4,}", tk)) and tk not in rules.get("single_word_ok", [])


def _ws(s: str) -> str:
    return "".join((s or "").split()).lower()


def check_required(title: str, title_keyword: str, ctx: dict, rules: dict) -> list:
    """필수 규칙을 어긴 항목 코드 목록 (빈 목록 = 통과). ctx: keyword, platform, top_titles, own_titles."""
    fails = []
    title, tk = (title or "").strip(), (title_keyword or "").strip()
    if not tk or _ws(tk) not in _ws(title):          # 키워드 포함은 띄어쓰기를 무시하고 비교
        fails.append("no_keyword")
    if tk and norm(tk) != norm(ctx.get("keyword", "")):
        fails.append("kw_mismatch")
    if len(title) > max_len_of(ctx.get("platform"), rules):
        fails.append("too_long")
    tn = title.replace(" ", "").lower()
    kn = (ctx.get("keyword") or "").replace(" ", "").lower()
    if any(w.replace(" ", "").lower() in tn and w.replace(" ", "").lower() not in kn for w in rules["forbidden_words"]):
        fails.append("forbidden")
    if _brand_hit(title, tk or ctx.get("keyword", ""), rules):
        fails.append("brand")
    lim = float(rules["similarity_max"])
    if any(similarity(title, t) >= lim for t in ctx.get("top_titles") or [] if t):
        fails.append("similar_top")
    if any(similarity(title, t) >= lim for t in ctx.get("own_titles") or [] if t):
        fails.append("similar_own")
    if ctx.get("track") == "money" and not has_pick(title, rules):
        fails.append("no_pick")
    if norm(title) in (ctx.get("off_intent") or ()):
        fails.append("off_intent")
    if awkward_after_keyword(title, tk):
        fails.append("awkward")
    if norm(title) in (ctx.get("unnatural") or ()):
        fails.append("unnatural")
    tw = ctx.get("target_words") or []
    if tw and not any(w in title for w in tw):
        fails.append("no_target")
    r1w = rules.get("r1_wp")
    if r1w and ctx.get("r1_wp"):       # 재기획 R1: 싼세계 제목 규칙(키워드 맨 앞 · 검색하는 말 · 실수·주의형 금지)
        if not _ws(title).startswith(_ws(ctx.get("keyword", ""))):
            fails.append("wp_not_front")
        if _MISTAKE_RE.search(title) or pattern_of(title) == "실수·손해형":
            fails.append("wp_mistake")
        if not any(_ws(sw) in _ws(title.replace(tk, " ") if tk else title) for sw in r1w.get("title_search_words", [])):
            fails.append("wp_no_search")
    return fails


def bonus_of(title: str, title_keyword: str, ctx: dict, rules: dict) -> dict:
    """가점 항목별 점수 (해당하는 것만)."""
    b, w = {}, rules["bonus"]
    title, tk = title.strip(), (title_keyword or "").strip()
    pos = title.find(tk) if tk else -1
    if pos >= 0:
        end = pos + len(tk)
        fr = int(rules["keyword_front_chars"])
        if pos + min(len(tk), fr) <= fr:
            b["keyword_front"] = w["keyword_front"]
        if ctx.get("platform") in ("naver", "reserve", None) and end <= int(rules["mobile_core_chars"]):
            b["mobile_core"] = w["mobile_core"]
    rest = title.replace(tk, " ") if tk else title
    if re.search(r"\d", rest) or any(x in rest for x in rules["result_words"]):
        b["number_or_result"] = w["number_or_result"]
    if any(h in title for h in rules["problem_hints"]):
        b["problem_hint"] = w["problem_hint"]
    if (ctx.get("timing") or "") in ("상승", "선발행") and any(s in title for s in rules["season_words"]):
        b["season_word"] = w["season_word"]
    if is_unspaced(title, tk, rules):          # 띄어쓰기 감점(필수는 아님): 띄어 쓴 후보가 항상 앞선다
        b["unspaced"] = -float(rules.get("unspaced_penalty", 3.0))
    if ctx.get("track") == "money" and any(p in title for p in rules["pick_words"]):
        b["pick_criteria"] = w["pick_criteria"]
    return b


def score_candidate(cand: dict, ctx: dict, rules: dict) -> dict:
    title, tk = cand.get("title") or "", cand.get("title_keyword") or ""
    fails = check_required(title, tk, ctx, rules)
    pat = pattern_of(title)          # 틀은 항상 코드 판정 (Claude 가 적은 pattern 은 참고용으로만 남긴다)
    out = {"title": title, "title_keyword": tk, "pattern": pat, "claude_pattern": cand.get("pattern"), "ok": not fails, "fails": fails}
    if fails:
        out["score"] = None
        out["fail_text"] = [FAIL_KO[f] for f in fails]
    else:
        bonus = bonus_of(title, tk, ctx, rules)
        out["bonus"] = bonus
        out["score"] = round(sum(bonus.values()), 2)
    return out


def rank(cands: list, ctx: dict, rules: dict) -> dict:
    """후보 전체 채점 → 통과한 것을 점수 높은 순(같으면 받은 순서)으로. 같은 제목 중복은 뒤의 것을 뺀다."""
    seen, scores = set(), []
    for c in cands or []:
        if not isinstance(c, dict):
            continue
        key = norm(c.get("title"))
        if not key or key in seen:
            continue
        seen.add(key)
        scores.append(score_candidate(c, ctx, rules))
    passed = sorted((s for s in scores if s["ok"]), key=lambda s: -s["score"])     # sorted 는 안정 정렬 = 같은 점수는 받은 순서
    return {"ranked": passed, "scores": scores, "passed": len(passed)}


def pick_alts(ranked: list, final: dict, n: int = 2) -> list:
    """예비 제목 n개: 확정·서로와 다른 틀을 먼저(점수 순), 모자라면 점수 순으로 채운다."""
    rest = [s for s in ranked if s is not final]
    used, out = {final["pattern"]}, []
    for s in rest:
        if s["pattern"] not in used and len(out) < n:
            out.append(s)
            used.add(s["pattern"])
    for s in rest:
        if s not in out and len(out) < n:
            out.append(s)
    return out


def select_weekly(groups: dict, share: float, n_cap: int = 2) -> dict:
    """주간 확정 제목 선택. groups = {그룹(플랫폼): [(키, 점수순 통과 후보 목록)]}.
    제한 두 가지(그룹 안): ① 같은 틀은 최대 floor(share×편수)(최소 1) ② 같은 'N가지'(예: 4가지)는 최대 n_cap 편.
    처음엔 모두 1등을 고르고, 넘는 쪽은 '점수 손실이 가장 작은 글'부터 제한을 어기지 않는 다음 순위 후보로 바꾼다(같으면 앞 순서).
    → {"pick": {키: 후보 dict|None}, "stuck": {키: [넘친 틀 또는 'N가지']}}  stuck = 바꿀 후보가 없어 새 후보가 필요한 글"""
    pick, stuck = {}, {}

    def feats(c):
        f = [("p", c["pattern"])]
        n = n_gaji(c["title"])
        if n:
            f.append(("n", n))
        return f

    for g, items in groups.items():
        cap = {"p": max(1, int(len(items) * share + 1e-9)), "n": n_cap}
        cur = {k: (r[0] if r else None) for k, r in items}
        rk = dict(items)
        order = {k: i for i, (k, _) in enumerate(items)}
        while True:
            cnt = {}
            for c in cur.values():
                for f in (feats(c) if c else []):
                    cnt[f] = cnt.get(f, 0) + 1
            over = {f for f, n in cnt.items() if n > cap[f[0]]}
            if not over:
                break
            best = None
            for k, c in cur.items():
                if not c or not any(f in over for f in feats(c)):
                    continue
                for x in rk[k]:
                    if x is c or any(cnt.get(f, 0) >= cap[f[0]] for f in feats(x)):
                        continue
                    cost = (round(c["score"] - x["score"], 4), order[k])
                    if best is None or cost < best[0]:
                        best = (cost, k, x)
                    break
            if not best:
                for k, c in cur.items():
                    if c and any(f in over for f in feats(c)):
                        stuck[k] = [(f[1] if f[0] == "p" else f[1] + "가지") for f in feats(c) if f in over]
                break
            cur[best[1]] = best[2]
        pick.update(cur)
    return {"pick": pick, "stuck": stuck}


def own_titles(index_dir=None) -> list:
    """내가 지금까지 쓴 글 제목 (post_index, 이번 기획이 만든 plan 항목은 제외)."""
    d = Path(index_dir or os.environ.get("LOCALAPP_INDEX_DIR") or BASE / "pipeline")
    try:
        posts = json.loads((d / "post_index.json").read_text(encoding="utf-8")).get("posts", {})
    except (OSError, ValueError):
        return []
    return [e.get("title") or k for k, e in posts.items() if e.get("source") != "plan" and (e.get("title") or k)]


def style_samples(titles: list, n: int = 12) -> list:
    """프롬프트에 줄 말투 예시: 제목 목록에서 고르게 n개 (같은 목록이면 항상 같은 결과)."""
    ts = sorted({" ".join(t.split()) for t in titles if t and 15 <= len(t) <= 45})
    if len(ts) <= n:
        return ts
    step = len(ts) / n
    return [ts[int(i * step)] for i in range(n)]


def selftest(cases_path=None) -> int:
    """tests/title_cases.json 시험: 좋은 예는 통과, 규칙 위반 예는 기대한 규칙에서 걸려야 한다. Claude 호출 없음."""
    rules = load_rules()
    data = json.loads(Path(cases_path or CASES).read_text(encoding="utf-8"))
    bad, n = [], 0
    for c in data["cases"]:
        n += 1
        ctx = {"keyword": c["keyword"], "platform": c.get("platform", "naver"), "top_titles": c.get("top_titles") or [],
               "own_titles": c.get("own_titles") or [], "track": c.get("track", "info"), "timing": c.get("timing")}
        got = check_required(c["title"], c["title_keyword"], ctx, rules)
        want = c.get("expect_fail")
        if want is None:
            if got:
                bad.append(f"{c['id']}: 통과해야 하는데 {got} 로 탈락 — {c['title']}")
        elif want not in got:
            bad.append(f"{c['id']}: '{want}' 로 걸려야 하는데 {got or '통과'} — {c['title']}")
    for t, tk, want in data.get("spacing_cases") or []:      # 띄어쓰기 감점 시험
        n += 1
        if is_unspaced(t, tk, rules) != want:
            bad.append(f"띄어쓰기: unspaced={want} 이어야 하는데 반대 — {t} (title_keyword {tk})")
    for t, tk, want in data.get("awkward_cases") or []:      # 키워드 바로 뒤 어색한 문장 시험
        n += 1
        if awkward_after_keyword(t, tk) != want:
            bad.append(f"어색한 문장: awkward={want} 이어야 하는데 반대 — {t}")
    for t, want in data.get("pattern_cases") or []:      # 제목 틀 분류 시험
        n += 1
        if pattern_of(t) != want:
            bad.append(f"틀 분류: '{want}' 이어야 하는데 '{pattern_of(t)}' — {t}")
    print(f"제목 규칙 시험: {n - len(bad)}/{n} 통과")
    for b in bad:
        print("  [실패] " + b)
    return 0 if not bad else 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(selftest(sys.argv[2] if len(sys.argv) > 2 else None) if "--selftest" in sys.argv else 0)
