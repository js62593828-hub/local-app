# -*- coding: utf-8 -*-
"""키워드 유형 분류 + 의도 묶기 (Claude 한 번 호출, 키워드 글자만 보냄)

run_pipeline.py 가 화면 수집 전에 부른다. 한 번의 호출로 두 가지를 받는다.
  1) 유형: 해결·정보형 / 제품선택형 / 브랜드모델구매 / 지역업체 / 주제 범위 밖 / 기타
  2) 의도 묶음: 같은 글이 될 키워드끼리의 묶음 (plan_posts.py 가 묶음마다 점수 1등만 남긴다)

호출이 실패하면 status='failed' 로 돌려주고, 호출한 쪽은 분류 없이 그대로 진행한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_posts import call_claude, extract_json, buy_words, has_word, model_for, UsageLimitError  # noqa: E402

MODEL = "sonnet"
MAX_TURNS = "1"

TYPES = ["solve", "choose", "brand", "local", "offtopic", "buyplace", "service", "other"]
TYPE_KO = {"solve": "해결·정보형", "choose": "제품선택형", "brand": "브랜드모델구매", "local": "지역업체",
           "offtopic": "주제 범위 밖", "buyplace": "구매처 찾기", "service": "업체 시공·수리 의뢰", "other": "기타"}
# 네이버/워드프레스 후보 표시. 브랜드모델구매·지역업체·주제 범위 밖은 화면 수집에서 빠진다.
PLATFORM_HINT = {"solve": "naver", "choose": "wordpress"}
EXCLUDED_TYPES = ("brand", "local", "offtopic", "buyplace", "service")
BRAND_RESCUE_WORDS = ["단점", "장단점", "후기", "비교"]

PROMPT = """아래는 블로그 글감 후보 키워드 목록이다. 키워드마다 유형을 분류하고, 같은 글이 될 키워드끼리 묶어라.
도구는 쓰지 말고 바로 답해라.

[유형] 아래 코드 중 하나만 쓴다.
- solve: 해결·정보형. 문제 해결·방법뿐 아니라 원인, 종류, 특징, 습성, 서식지, 주기, 차이처럼 정보를 찾는 검색
  (예: 곰팡이 제거, 패딩 세탁법, 바퀴벌레 서식지, 독일바퀴벌레 특징, 에어컨 필터 교체 주기, 원인)
  단, 제품을 사려고 '종류'를 보는 검색(예: 선반 종류, 행거 고르는 법)은 choose 다.
- choose: 제품선택형. 단점·장단점·제품 종류·고르는 법·비교 등 제품을 고르기 위한 검색
- brand: 브랜드모델구매. 특정 브랜드나 모델명을 직접 찾는 검색 (사려는 사람의 검색)
- local: 지역업체. 지역명 + 업체 (예: 강남 세탁소)
- offtopic: 주제 범위 밖. 이 블로그의 주제({scope})와 상관없는 키워드.
  확장 탐색에서 연관 키워드가 엉뚱한 분야로 번질 때 걸러내는 용도다. 금지 분야: {forbidden}.
  집 안 살림·청소·세탁·수납·집 관리·차량 관리와 직접 관련 없는 취미·스포츠·레저 용품(예: 수영 수경, 낚시, 골프, 캠핑 장비),
  패션·미용, 오토바이, 디자인·제작 서비스(예: 포스터 제작) 등은 offtopic 이다.
  취미·운동 장비를 넣어 두는 정리·보관·거치용품(캠핑렉, 덤벨거치대, 릴보관함, 골프백보관)도 취미 용품이 주인공이라 offtopic 이다.
  단, 신발·옷·몸에 배는 냄새와 습기(발냄새, 신발 냄새, 신발제습제, 축구화 탈취제, 운동복 땀냄새)는 집 살림이다 — 신발·옷의 탈취·제습·세탁은 offtopic 이 아니다
  (취미 용품 '자체'를 고르는 게 아니라 신발·옷 관리 문제이기 때문. 예: 발냄새없애는법식초 → solve, 축구화탈취제 → choose, 신발제습제 → choose).
  주제 안이면 offtopic 이 아니다. 위 예처럼 분명히 벗어난 것이 아니고 애매하면 offtopic 으로 하지 말고 다른 유형으로 분류한다.
- service: 업체 시공·수리 의뢰. 개인이 직접 할 수 없는 공사·시공·방역·수리 의뢰를 찾거나 그 견적·비용을 알아보는 검색만 service 다
  (예: 배관공사, 아파트배관공사, 온수배관교체(집 전체 배관), 단열시공, 단열벽지시공, 탄성코트, 정화조, 보일러수리, 보일러교체, 바퀴벌레방역비용, 방역, 소독, 리폼, 싱크대리폼, 싱크대공사,
  싱크대교체비용(싱크대 상판·장을 통째로), 매립욕조, 욕조보수, 화장실리모델링, 수도계량기교체).
  아래는 개인이 직접 하는 일이므로 service 가 아니라 solve 다. 그 '비용·방법·셀프' 키워드도 solve 다(교체비용 = 교체와 같은 글):
  수전·샤워기·샤워기헤드·환풍기·변기 부속·배수관·배수구·호스·경첩·싱크대 수도·세탁기 배관(호스) 같은 부품·부속의 교체·설치·수리,
  변기·세면대를 통째로 바꾸거나 설치하는 키워드(셀프와 업체 비용을 견주는 글이 되므로 solve), 실리콘 재시공 셀프.
- buyplace: 구매처 찾기. 어디서 파는지·어느 매장인지 찾는 검색 (예: ○○ 파는곳, 구매처, 판매처, 매장). 글감으로 쓰지 않는다.
- other: 정말 분류가 안 되는 것만. 정보를 찾는 검색이면 other 가 아니라 solve 다.
브랜드 키워드라도 '단점·후기·비교'가 붙으면 choose 로 분류한다.

[의도 묶기] 아주 엄격하게, 같은 글이 될 키워드끼리만 묶어라.
묶는 기준: A로 검색한 사람이 B를 위해 쓴 글을 읽고 완전히 만족하고, 두 글의 제목이 거의 같게 나올 때만 묶는다.
- 대상(패딩/이불), 방법(물세탁/드라이클리닝), 부위(구멍/지퍼), 브랜드가 다르면 묶지 않는다.
- 애매하면 묶지 않는다.
- 같은 뜻의 다른 표현(구스=거위털, 덕다운=오리털 등)은 묶어도 된다.

{meta_rules}
[키워드]
{keywords}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 머리말, 코드블록(```)을 붙이지 않는다. 형식:
{{"types": {{"키워드A": "solve", "키워드B": "choose"}}, "groups": [["키워드A", "키워드C"], ["키워드D", "키워드E", "키워드F"]]{meta_format}}}
- types 에는 위 목록의 모든 키워드를 빠짐없이, 목록에 있는 값 그대로 적는다.
- groups 한 묶음에는 키워드 2개 이상. 어느 묶음에도 속하지 않는 키워드는 적지 않는다. 한 키워드는 한 묶음에만 넣는다.
- 묶을 것이 없으면 "groups": []
- 생각 과정이나 정정 없이, 최종 답 JSON 한 덩어리만 한 번 출력한다.
"""


META_RULES = """
[카테고리·세부 주제·표준 표기] 키워드마다 아래 세 가지도 적는다.
- category: 아래 카테고리 중 가장 가까운 1개 (목록의 글자 그대로): {categories}
- topic: 세부 주제. 이 키워드가 다루는 대상을 2~6글자 명사 하나로 (예: 바퀴벌레, 행거, 식기건조대, 곰팡이). 같은 대상이면 반드시 같은 글자로 쓴다.
- canonical: 표준 표기. 흔한 오타·잘못된 표기(예: 헹거→행거)를 바로잡은 키워드. 띄어쓰기는 바꾸지 말고 철자만 고친다. 이미 맞으면 키워드 그대로 적는다. 뜻이 달라지게 고치지 않는다.
"""
META_FORMAT = ', "meta": {"키워드A": {"category": "...", "topic": "...", "canonical": "..."}}'
META_CHUNK = 45      # 메타까지 받을 때는 한 번에 보내는 키워드 수를 줄인다 (응답이 길어져 시간이 넘지 않게)

TOPIC_FILE = Path(__file__).resolve().parent / "topic.json"


def load_scope() -> dict:
    """topic.json 에서 블로그 주제 범위를 읽는다. 못 읽으면 기본 문구."""
    try:
        t = json.loads(TOPIC_FILE.read_text(encoding="utf-8-sig"))
        cats = [str(c) for c in t.get("categories", [])]
        scope = str(t.get("scope") or "생활 살림·집 관리")
        if cats:
            scope += " — 세부: " + ", ".join(cats)
        forbidden = ", ".join(str(x) for x in t.get("forbidden", []) if x != "주제 범위 밖") or "의료, 금융, 연예"
        return {"scope": scope, "forbidden": forbidden, "categories": cats}
    except (OSError, ValueError):
        return {"scope": "생활 살림·집 관리", "forbidden": "의료, 금융, 연예", "categories": []}


def _first_line(e: Exception, n: int) -> str:
    return (str(e).splitlines() or [type(e).__name__])[0][:n]


def classify(keywords: list, scope: dict = None, with_meta: bool = False) -> dict:
    """키워드 글자 목록 → {status, model, max_turns, types{kw:code}, rescued[], groups[[kw..]], usage, error?, meta{kw:{category,topic,canonical}}}
    with_meta=True 면 카테고리·세부 주제·표준 표기도 같은 호출에서 받는다 (키워드가 많으면 META_CHUNK 개씩 나눠 호출)."""
    kws_all = list(dict.fromkeys(keywords))
    if not with_meta or len(kws_all) <= META_CHUNK:
        return _classify_once(kws_all, scope, with_meta)
    parts = []
    for i in range(0, len(kws_all), META_CHUNK):
        part = _classify_once(kws_all[i:i + META_CHUNK], scope, True)
        if part["status"] != "ok":      # 응답이 깨졌을 수 있으니 이 덩어리만 한 번 더 시도
            part = _classify_once(kws_all[i:i + META_CHUNK], scope, True)
        parts.append(part)
    out = {"status": "ok", "model": model_for("type"), "max_turns": int(MAX_TURNS), "types": {}, "rescued": [], "groups": [], "usage": None, "meta": {}}
    cost = 0
    for part in parts:
        if part["status"] != "ok":
            return part
        for k in ("types", "meta"):
            out[k].update(part.get(k) or {})
        out["rescued"] += part["rescued"]
        out["groups"] += part["groups"]
        cost += ((part.get("usage") or {}).get("total_cost_usd") or 0)
    out["usage"] = {"total_cost_usd": cost}
    return out


def _classify_once(keywords: list, scope: dict = None, with_meta: bool = False) -> dict:
    out = {"status": "skipped", "model": model_for("type"), "max_turns": int(MAX_TURNS),
           "types": {}, "rescued": [], "groups": [], "usage": None}
    kws = list(dict.fromkeys(keywords))
    if not kws:
        return out
    try:
        sc = scope or load_scope()
        cats = sc.get("categories") or []
        meta_rules = META_RULES.format(categories=", ".join(cats) or "(없음: 자유롭게)") if with_meta else ""
        env = call_claude(PROMPT.format(keywords=json.dumps(kws, ensure_ascii=False),
                                        scope=sc["scope"], forbidden=sc["forbidden"],
                                        meta_rules=meta_rules, meta_format=META_FORMAT if with_meta else ""), model_for("type"), MAX_TURNS)
    except UsageLimitError:
        raise       # 사용 한도: 호출한 쪽이 진행을 저장하고 멈춘다
    except RuntimeError as e:
        out["status"], out["error"] = "failed", _first_line(e, 200)
        return out
    u = env.get("usage") or {}  # 응답을 못 읽더라도 이미 쓴 비용은 기록
    out["usage"] = {"total_cost_usd": env.get("total_cost_usd"), "num_turns": env.get("num_turns"),
                    "duration_ms": env.get("duration_ms"),
                    "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
    try:
        data = extract_json(env.get("result", ""))
        raw_types, raw_groups = data["types"], data.get("groups", [])
        if not isinstance(raw_types, dict) or not isinstance(raw_groups, list):
            raise ValueError("types/groups 형식이 다름")
    except (ValueError, KeyError, TypeError) as e:
        out["status"], out["error"] = "failed", "응답을 읽지 못함: " + _first_line(e, 150)
        return out

    known, missing, buyw = set(kws), [], buy_words()
    for k in kws:
        t = raw_types.get(k)
        if t not in TYPES:
            missing.append(k)
            t = "other"  # 빠졌거나 이상한 값은 '기타'(제외하지 않음)
        if has_word(k, buyw):
            t = "buyplace"  # 규칙 보강: 파는곳·구매처·매장 같은 구매처 찾기는 글감에서 제외
        if t == "brand" and any(w in k.replace(" ", "") for w in BRAND_RESCUE_WORDS):
            t = "choose"  # 규칙 보강: 브랜드 + 단점/후기/비교 → 제품선택형
            out["rescued"].append(k)
        out["types"][k] = t
    if missing:
        out["missing_types"] = missing
    seen = set()
    for g in raw_groups:
        g = [k for k in (g if isinstance(g, list) else []) if k in known and k not in seen]
        g = list(dict.fromkeys(g))
        if len(g) >= 2:
            seen.update(g)
            out["groups"].append(g)
    if with_meta:
        raw_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        cat_ok = set(sc.get("categories") or [])
        out["meta"] = {}
        for k in kws:
            m = raw_meta.get(k) if isinstance(raw_meta.get(k), dict) else {}
            cat = str(m.get("category") or "").strip()
            canon = " ".join(str(m.get("canonical") or "").split())
            out["meta"][k] = {"category": cat if (not cat_ok or cat in cat_ok) else None,
                              "topic": str(m.get("topic") or "").strip() or None,
                              "canonical": canon or k}
        out["meta_missing"] = [k for k in kws if k not in raw_meta]
    out["status"] = "ok"
    return out
