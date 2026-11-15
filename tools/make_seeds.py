# -*- coding: utf-8 -*-
"""주간 씨앗 자동 생성: topic.json + 다음 주 계절 + 카테고리 연관 키워드 + 지난 성과 + 기존 발행 키워드
→ claude -p (sonnet, 1단계, 도구 끔) 한 번 → tools/weekly_seeds.txt 덮어쓰기

- 카테고리가 고루 섞이게, 성과 좋은 씨앗 약 60% 유지 · 새 씨앗 약 40%, 총 15~20개.
- 이전 weekly_seeds.txt 는 weekly_seeds.날짜.txt 로 보관한다.
- 고른 이유는 pipeline/seeds_log_날짜.json 에 남긴다.
- 실패하면 weekly_seeds.txt 를 건드리지 않는다 (= 지난주 씨앗을 그대로 사용). 이때도 종료 코드는 0.
앱 장부는 건드리지 않는다. 키 값은 어디에도 출력하지 않는다.

사용법
  python tools/make_seeds.py
  python tools/make_seeds.py --seeds 다른씨앗파일.txt --today 2026-11-13
"""
import argparse
import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import collect_metrics as cm  # noqa: E402  (검색광고 연관 키워드)
from plan_posts import call_claude, extract_json, next_monday, load_weekly, UsageLimitError, LIMIT_EXIT, limit_message  # noqa: E402
from safe_io import write_text_retry  # noqa: E402

BASE = TOOLS.parent
OUT_DIR = BASE / "pipeline"
MODEL, MAX_TURNS = "sonnet", "1"
MIN_SEEDS, WANT_MIN, WANT_MAX = 10, 45, 60          # 3단계: 정보글을 더 넓게 찾도록 기본 60개(정보 42 · 수익 18)
CALENDAR_MAX = 6          # 계절 달력에서 넣는 선발행 정보 씨앗 최대 수 (60개 안에 포함)
CALENDAR_PATH = OUT_DIR / "seasonal_calendar.json"
# 브랜드·제조사 이름: 씨앗에 들어 있으면 뺀다(제품 종류만 씨앗으로 쓴다). 모델명(영문+숫자 2자리 이상: WA-123, AS303)도 뺀다.
BRAND_WORDS = ["삼성", "엘지", "lg", "위닉스", "샤오미", "코웨이", "쿠쿠", "청호", "다이슨", "샤크", "로보락", "드리미", "에코백스", "필립스", "아이닉",
               "신일", "캐리어", "쿠첸", "테팔", "일렉트로룩스", "보쉬", "한일", "대우", "아이리스", "다이소", "이케아", "락앤락", "3m", "블루에어",
               "카카오", "샤오미", "미닉스", "하이얼", "린나이", "경동", "귀뚜라미", "스탠리", "오쏘", "홈키파", "에프킬라", "맥스포스", "암웨이"]
MODEL_RE = re.compile(r"[A-Za-z]+[-]?\d{2,}|\d+[-]?[A-Za-z]{1,3}\d+")
INFO_SHARE = 0.7        # 정보 씨앗 70% · 수익 씨앗 30%
RELATED_PER_CATEGORY = 12
EXISTING_MAX = 250
MONTH_HINT = {1: "한겨울(난방·결로·동파)", 2: "늦겨울(환절기 직전·정리)", 3: "이른 봄(환절기·봄맞이 대청소)",
              4: "봄(꽃가루·미세먼지·옷 정리)", 5: "늦봄·초여름(에어컨 점검·제습 시작)", 6: "초여름·장마 시작(습기·곰팡이·벌레)",
              7: "장마·한여름(제습·에어컨·식중독)", 8: "한여름·무더위(냉방·냄새·벌레)", 9: "초가을(환절기·가을 이불·난방 점검)",
              10: "가을(난방 준비·결로·겨울옷 꺼내기)", 11: "늦가을·초겨울(난방 시작·김장·동파 대비)", 12: "초겨울·한겨울(난방·결로·연말 대청소)"}

PROMPT = """너는 블로그 씨앗 키워드 기획자다. 아래 자료로 다음 주에 쓸 '씨앗'을 만들어라.
씨앗은 나중에 네이버 연관 키워드로 확장되는 출발점이다. 대상(제품·문제)을 가리키는 짧은 말(1~3단어)로 만들고, 핵심단어는 연관 키워드에 반드시 들어가야 할 말(1~2개, 반드시 씨앗 안에 있는 단어)이다.

[주제 범위] {scope}
[카테고리] {categories}
[플랫폼 역할] 네이버 = {naver_role}(단어 예: {naver_words}), 워드프레스 = {wp_role}(단어 예: {wp_words}). 두 역할의 글감이 모두 나올 수 있는 씨앗을 섞어라.
[금지] {forbidden}
[다음 주] {week} — 계절: {season}

[카테고리별 네이버 검색광고 연관 키워드(월간 검색량 순)]
{related}

[지난주 씨앗과 성과] (성과 = 지난 파이프라인에서 그 씨앗에서 나온 키워드의 평균 점수, 높을수록 좋음. '없음'은 성과 데이터 없음)
{performance}

[이미 발행·예약된 글 제목 일부({existing_count}개 중 최근 {existing_shown}개)] — 이와 겹치는 씨앗은 만들지 마라.
{existing}

[요청]
- 씨앗 {want_min}~{want_max}개.
- 카테고리가 고루 섞이게: 10개 카테고리 모두 1개 이상, 한 카테고리는 {max_cat}개를 넘기지 않는다.
- 성과 좋은 지난주 씨앗을 약 60% 유지(kind: "keep", seed 는 지난주 씨앗 글자 그대로), 나머지 약 40%는 새 씨앗(kind: "new"). 지난주 씨앗이 모자라거나 성과 데이터가 없으면 새 씨앗으로 채운다.
- 다음 주 계절에 맞는 소재를 우선하고, 연관 키워드 자료에서 수요가 보이는 것을 활용한다.
- 씨앗끼리 겹치지 않게 한다.
- 이 블로그(네이버·워드프레스 모두)의 글은 '살림하며 겪는 생활 문제를 해결하고, 그 과정에서 필요하면 제품을 추천'하는 글이다. 제품 소개가 중심이 아니다.
- 씨앗은 두 갈래다. 정보 씨앗 약 {info_n}개(70%), 수익 씨앗 약 {money_n}개(30%).
  · 정보 씨앗(track: "info", role: "naver"): '대상 + 문제'를 담은 생활 문제 씨앗.
    (예: 욕실 곰팡이, 배수구 냄새, 창문 결로, 세탁기 냄새, 패딩 세탁, 수도 동파, 이불 보풀, 냄비 탄자국)
  · 수익 씨앗(track: "money", role: "wordpress"): 위 정보 씨앗의 문제를 해결할 때 쓰는 '제품·도구'만. 반드시 정보 씨앗 하나에서 파생시키고
    from_seed 에 그 정보 씨앗을 글자 그대로 적는다. (예: 배수구 냄새 → 배수구 트랩 / 곰팡이 → 곰팡이 제거제 / 세탁기 냄새 → 세탁조 클리너)
    해결과 상관없는 제품(인테리어 소품, 가전 신제품 구경)은 만들지 않는다.
    수익 씨앗은 '제품 종류'만 쓴다. 브랜드·제조사·모델명(삼성, 엘지, 위닉스, 샤오미, 모델 번호 등)이 든 씨앗은 정보 씨앗이든 수익 씨앗이든 절대 만들지 않는다.
    (잘못된 예: 삼성 공기청정기 필터, 엘지 호환 필터 / 올바른 예: 공기청정기 필터 → 단, 정보 씨앗의 문제에서 파생될 때만)
  성과 좋은 지난주 씨앗이라도 이 갈래 규칙을 먼저 지킨다.
{pest_rule}
[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 머리말, 코드블록(```)을 붙이지 않는다. 생각 과정이나 정정 없이 최종 답만 한 번 출력한다. 형식:
{{"seeds": [{{"seed": "씨앗", "core": ["핵심단어"], "category": "카테고리 이름 그대로", "kind": "keep 또는 new", "track": "info 또는 money", "role": "naver 또는 wordpress", "from_seed": "수익 씨앗이면 파생 원본 정보 씨앗, 정보 씨앗이면 빈 문자열", "reason": "고른 이유 한 문장"}}]}}
"""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def norm(s: str) -> str:
    return "".join((s or "").split()).lower()


def has_brand(seed: str) -> bool:
    """브랜드·제조사 이름이나 모델명이 든 씨앗인가 (E: 씨앗은 제품 종류·생활 문제만)."""
    n = norm(seed)
    return any(norm(b) in n for b in BRAND_WORDS) or bool(MODEL_RE.search(seed))


def parse_seed_line(line: str) -> tuple:
    left, _, right = line.partition("|")
    core = [" ".join(w.split()) for w in right.split(",")]
    return " ".join(left.split()), [w for w in core if w]


def read_last_seeds(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            seed, core = parse_seed_line(line)
            if seed:
                out.append((seed, core))
    return out


def performance(last_seeds: list) -> list:
    """지난 ranking.json 에서 씨앗별 성과(평균 점수) 집계. 지난주 씨앗 순서를 유지."""
    try:
        ranking = json.loads((OUT_DIR / "ranking.json").read_text(encoding="utf-8"))["ranking"]
    except (OSError, ValueError, KeyError):
        ranking = []
    agg = {}
    for r in ranking:
        a = agg.setdefault(r.get("seed"), [])
        a.append((r["score"]["total"], r["keyword"]))
    out, seen = [], set()
    for seed, core in last_seeds:
        a = agg.get(seed)
        seen.add(seed)
        out.append({"seed": seed, "core": core,
                    "avg_score": round(sum(s for s, _ in a) / len(a), 1) if a else None,
                    "keywords_scored": len(a) if a else 0,
                    "best": max(a)[1] if a else None})
    for seed, a in agg.items():  # 지난주 씨앗 파일에는 없지만 ranking 에 있던 씨앗도 참고로
        if seed not in seen:
            out.append({"seed": seed, "core": [], "avg_score": round(sum(s for s, _ in a) / len(a), 1),
                        "keywords_scored": len(a), "best": max(a)[1], "note": "지난주 씨앗 파일에는 없음"})
    return out


def related_by_category(categories: list) -> dict:
    """카테고리 단어마다 검색광고 연관 키워드를 받아 검색량 순 상위만 모은다. 실패하면 그 카테고리는 빈 목록."""
    out = {}
    if cm.config.USE_MOCK:
        log("[주의] 검색광고 API 키가 없어 연관 키워드 자료를 건너뜁니다.")
        return {c: [] for c in categories}
    for cat in categories:
        parts = list(dict.fromkeys(p for p in re.split(r"[·,/\s]+", cat) if len(p) >= 2))[:3]
        rows = {}
        for p in parts:
            try:
                for r in cm.related_keywords(p):
                    if r["monthly_total"] >= 300 and norm(r["keyword"]) != norm(p):
                        rows[r["keyword"]] = max(rows.get(r["keyword"], 0), r["monthly_total"])
            except Exception as e:
                log(f"  연관 키워드 조회 실패({p}): {str(e)[:80]}")
        out[cat] = [f"{k}({v})" for k, v in sorted(rows.items(), key=lambda x: x[1], reverse=True)[:RELATED_PER_CATEGORY]]
        log(f"  {cat}: 연관 키워드 {len(out[cat])}개")
    return out


def load_existing_titles() -> tuple:
    try:
        d = json.loads((OUT_DIR / "existing_keywords.json").read_text(encoding="utf-8"))
        items = d.get("items", [])
    except (OSError, ValueError):
        return [], 0
    items = sorted(items, key=lambda i: i.get("date") or "", reverse=True)
    titles = list(dict.fromkeys(i["title"] for i in items if i.get("title")))
    return titles[:EXISTING_MAX], len(titles)


def validate(raw: list, categories: list, forbidden: list, pest_words: list = None, max_pest: int = None) -> tuple:
    seeds, problems, seen = [], [], set()
    pest_n = 0
    pest_words = [norm(x) for x in (pest_words or []) if norm(x)]
    banned = [f for f in forbidden if f != "주제 범위 밖"]
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        seed = " ".join(str(e.get("seed", "")).replace("|", " ").replace(",", " ").split())
        if not seed or norm(seed) in seen:
            continue
        if any(b in seed for b in banned):
            problems.append(f"금지 주제라 제외: {seed}")
            continue
        if has_brand(seed):
            problems.append(f"브랜드·모델명이 든 씨앗이라 제외: {seed}")
            continue
        core = []
        for c in e.get("core") or []:
            c = " ".join(str(c).replace("|", " ").replace(",", " ").split())
            if c and norm(c) in norm(seed):
                core.append(c)
            elif c:
                problems.append(f"{seed}: 핵심단어 '{c}' 가 씨앗에 없어 뺌")
        cat = e.get("category")
        if cat not in categories:
            problems.append(f"{seed}: 카테고리 '{cat}' 가 topic.json 에 없음")
            cat = None
        kind = e.get("kind") if e.get("kind") in ("keep", "new") else "new"
        if max_pest is not None and any(p in norm(seed) for p in pest_words):
            if pest_n >= max_pest:
                problems.append(f"해충 씨앗 한도({max_pest}개) 초과로 제외: {seed}")
                continue
            pest_n += 1
        seen.add(norm(seed))
        track = e.get("track") if e.get("track") in ("info", "money") else ("money" if e.get("role") == "wordpress" else "info")
        role = "wordpress" if track == "money" else "naver"          # 갈래에 맞춰 role 도 맞춘다(수익 → wordpress, 정보 → naver)
        seeds.append({"seed": seed, "core": core[:2], "category": cat, "kind": kind, "track": track, "role": role,
                      "from_seed": " ".join(str(e.get("from_seed") or "").split()) if track == "money" else "",
                      "reason": str(e.get("reason", ""))[:200]})
    infos = {norm(x["seed"]) for x in seeds if x["track"] == "info"}
    kept = []
    for x in seeds:
        if x["track"] == "money" and norm(x["from_seed"]) not in infos:
            problems.append(f"수익 씨앗 '{x['seed']}' 의 파생 원본 '{x['from_seed']}' 이 정보 씨앗 목록에 없어 제외 (수익 씨앗은 정보 씨앗의 문제에서 파생된 제품 종류만)")
            continue
        kept.append(x)
    return kept[:WANT_MAX], problems


def calendar_seeds(monday: date, taken: set, categories: list, path: Path = None) -> tuple:
    """계절 달력(초안)에서 이번 달 항목(6~8주 뒤 성수기가 오는 문제)을 정보 씨앗으로 뽑는다(선발행). → (씨앗 목록, 메모)
    이미 있는 씨앗·브랜드·모델명·카테고리 없는 것은 뺀다. 달력이 없으면 빈 목록(씨앗 만들기는 그대로 진행)."""
    path = path or CALENDAR_PATH
    try:
        cal = json.loads(path.read_text(encoding="utf-8"))
        items = cal["months"][str(monday.month)]
    except (OSError, ValueError, KeyError):
        return [], f"계절 달력({path.name})이 없거나 {monday.month}월 항목이 없어 선발행 씨앗 없음 (tools/make_calendar.py 로 초안을 만들 수 있음)"
    out, seen = [], set(taken)
    for e in items:
        seed = " ".join(str(e.get("seed", "")).split())
        if not seed or norm(seed) in seen or has_brand(seed) or e.get("category") not in categories:
            continue
        seen.add(norm(seed))
        out.append({"seed": seed, "core": [], "category": e["category"], "kind": "calendar", "track": "info", "role": "naver", "from_seed": "",
                    "reason": f"계절 달력{'(초안)' if cal.get('draft') else ''}: {e.get('peak') or '6~8주 뒤'} 성수기 — 선발행"})
        if len(out) >= CALENDAR_MAX:
            break
    return out, f"계절 달력 {monday.month}월 항목 {len(items)}개 중 {len(out)}개를 선발행 정보 씨앗으로 넣음"


def write_log(record: dict, today: date) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"seeds_log_{today.strftime('%Y%m%d')}.json"
    if p.exists():
        p.rename(OUT_DIR / f"seeds_log_{today.strftime('%Y%m%d')}.old_{datetime.now().strftime('%H%M%S')}.json")
    write_text_retry(p, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return p


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="주간 씨앗 자동 생성 (claude -p 1회)")
    ap.add_argument("--topic", default=str(TOOLS / "topic.json"), help="주제 설정 (기본: tools/topic.json)")
    ap.add_argument("--seeds", default=str(TOOLS / "weekly_seeds.txt"), help="덮어쓸 씨앗 파일 (기본: tools/weekly_seeds.txt)")
    ap.add_argument("--today", help="기준일 YYYY-MM-DD (기본: 오늘, 시험용)")
    args = ap.parse_args()

    try:
        topic = json.loads(Path(args.topic).read_text(encoding="utf-8-sig"))
        categories = list(topic["categories"])
        scope, forbidden, roles = topic["scope"], topic["forbidden"], topic["roles"]
        today = date.fromisoformat(args.today) if args.today else date.today()
    except (OSError, ValueError, KeyError) as e:
        log(f"[실패] topic.json 을 읽지 못했습니다: {e}")
        return 2

    pest_words, max_pest = [], None
    try:
        wk = load_weekly(TOOLS / "filter.json")
        pest_words, max_pest = wk["pest_words"], wk["max_pest_seeds"]
    except (OSError, ValueError, KeyError) as e:
        log(f"[주의] filter.json 의 해충 설정을 읽지 못해 해충 씨앗 한도 없이 진행: {e}")
    if max_pest is None:
        pest_rule = ""
    elif max_pest == 0:
        pest_rule = "- 혐오감을 주는 해충·쥐(바퀴벌레, 노래기, 지네, 쥐 등) 씨앗은 만들지 않는다. '습기·곰팡이·해충' 카테고리는 습기·곰팡이·제습 소재로 채운다.\n"
    else:
        pest_rule = (f"- 혐오감을 주는 해충·쥐(바퀴벌레, 노래기, 지네, 쥐 등) 씨앗은 최대 {max_pest}개만. "
                     "'습기·곰팡이·해충' 카테고리는 습기·곰팡이·제습 소재를 우선한다.\n")

    seeds_path = Path(args.seeds)
    monday = next_monday(today)
    sunday = monday + timedelta(days=6)
    season = MONTH_HINT[monday.month] if monday.month == sunday.month else \
        f"{MONTH_HINT[monday.month]} → {MONTH_HINT[sunday.month]}"
    last_seeds = read_last_seeds(seeds_path)
    perf = performance(last_seeds)
    log("카테고리별 연관 키워드 조회 중...")
    related = related_by_category(categories)
    existing, existing_total = load_existing_titles()
    if not existing:
        log("[주의] 기존 발행 키워드 파일이 없습니다 (existing_posts.py 를 먼저 실행하세요).")

    sent = {"week": f"{monday} ~ {sunday}", "season": season, "categories": categories,
            "related_by_category": related, "last_seeds_performance": perf,
            "existing_titles_sent": len(existing), "existing_titles_total": existing_total}
    prompt = PROMPT.format(
        scope=scope, categories=", ".join(categories),
        naver_role=roles["naver"]["name"], naver_words=", ".join(roles["naver"]["words"]),
        wp_role=roles["wordpress"]["name"], wp_words=", ".join(roles["wordpress"]["words"]),
        forbidden=", ".join(forbidden), week=f"{monday} ~ {sunday}", season=season,
        related="\n".join(f"- {c}: {', '.join(v) if v else '(자료 없음)'}" for c, v in related.items()),
        performance="\n".join(
            f"- {p['seed']}" + (f" | 핵심: {', '.join(p['core'])}" if p["core"] else "")
            + f" → 평균 점수 {p['avg_score'] if p['avg_score'] is not None else '없음'}"
            + (f" ({p['keywords_scored']}개 점수, 최고 {p['best']})" if p["keywords_scored"] else "")
            for p in perf) or "- (지난주 씨앗 없음)",
        existing_count=existing_total, existing_shown=len(existing),
        existing="\n".join(f"- {t}" for t in existing) or "- (없음)",
        want_min=WANT_MIN, want_max=WANT_MAX, max_cat=max(3, round(WANT_MAX / 10)), pest_rule=pest_rule,
        info_n=round(WANT_MAX * INFO_SHARE) - CALENDAR_MAX, money_n=WANT_MAX - round(WANT_MAX * INFO_SHARE))

    record = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "status": "failed",
              "model": MODEL, "max_turns": int(MAX_TURNS), "seeds_file": seeds_path.name,
              "sent_to_claude": sent, "usage": None, "problems": [], "seeds": []}

    def fail(msg: str) -> int:
        record["problems"].append(msg)
        record["fallback"] = "지난주 씨앗을 그대로 사용 (weekly_seeds.txt 를 바꾸지 않음)"
        lp = write_log(record, today)
        log(f"[실패] {msg}")
        log(f"[대체] 지난주 씨앗({len(last_seeds)}개)을 그대로 사용합니다. 기록: {lp}")
        return 0 if last_seeds else 1  # 지난주 씨앗도 없으면 다음 단계가 불가능

    log(f"Claude({MODEL}) 를 1번 호출합니다 (씨앗 {WANT_MIN}~{WANT_MAX}개 요청)...")
    try:
        env = call_claude(prompt, MODEL, MAX_TURNS)
    except UsageLimitError as e:
        log(limit_message(e, "씨앗 만들기"))
        return LIMIT_EXIT
    except RuntimeError as e:
        return fail(f"Claude 호출 실패: {str(e).splitlines()[0][:200]}")
    u = env.get("usage") or {}  # 응답을 못 읽어도 이미 쓴 비용은 기록
    record["usage"] = {"total_cost_usd": env.get("total_cost_usd"), "num_turns": env.get("num_turns"),
                       "duration_ms": env.get("duration_ms"),
                       "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
    try:
        raw = extract_json(env.get("result", ""))["seeds"]
    except (ValueError, KeyError, TypeError) as e:
        return fail(f"응답을 읽지 못함 ({type(e).__name__})")
    seeds, problems = validate(raw, categories, forbidden, pest_words, max_pest)
    record["problems"] += problems
    cal, cal_note = calendar_seeds(monday, {norm(x["seed"]) for x in seeds}, categories)
    if cal:     # 정보 씨앗 자리 안에서 합친다 (전체 WANT_MAX 를 넘기지 않게 앞쪽 새 씨앗을 줄임)
        room = max(0, WANT_MAX - len(cal))
        seeds = seeds[:room] + cal
        record["calendar_seeds"] = [c["seed"] for c in cal]
    record["calendar_note"] = cal_note
    log(f"[계절 달력] {cal_note}")
    if len(seeds) < MIN_SEEDS:
        return fail(f"쓸 수 있는 씨앗이 {len(seeds)}개뿐이라 (최소 {MIN_SEEDS}개) 사용하지 않음")
    n_money = sum(1 for s in seeds if s["track"] == "money")
    share = n_money / len(seeds)
    if not 0.2 <= share <= 0.4:
        record["problems"].append(f"수익 씨앗이 {n_money}개({share:.0%})로 기준(약 30%, 20~40%)을 벗어남 — 탐색 단계에서 모자란 갈래 씨앗을 더 넣습니다")
    if not WANT_MIN <= len(seeds) <= WANT_MAX:
        record["problems"].append(f"씨앗 수 {len(seeds)}개 (요청 {WANT_MIN}~{WANT_MAX}개)")

    # 이전 파일 보관 → 덮어쓰기
    archived = None
    if seeds_path.exists():
        archived = seeds_path.with_name(f"{seeds_path.stem}.{today.strftime('%Y%m%d')}{seeds_path.suffix}")
        if archived.exists():
            archived = seeds_path.with_name(
                f"{seeds_path.stem}.{today.strftime('%Y%m%d')}_{datetime.now().strftime('%H%M%S')}{seeds_path.suffix}")
        shutil.copy2(seeds_path, archived)
    lines = [f"# 자동 생성 (make_seeds.py) {record['generated_at']} — 다음 주 {monday} ~ {sunday}",
             "# 형식: 씨앗 | 핵심단어1, 핵심단어2   (이유는 pipeline/seeds_log_날짜.json)"]
    lines += [s["seed"] + (" | " + ", ".join(s["core"]) if s["core"] else "") for s in seeds]
    write_text_retry(seeds_path, "\n".join(lines) + "\n")

    try:     # 선발행 표시용: 이 씨앗에서 나온 글의 timing = '선발행' (plan_posts 가 읽음)
        write_text_retry(OUT_DIR / "calendar_seeds.json", json.dumps({"week": str(monday), "seeds": [c["seed"] for c in cal]}, ensure_ascii=False) + "\n")
    except OSError as e:
        record["problems"].append(f"calendar_seeds.json 저장 실패: {e}")
    cats = {}
    for s in seeds:
        cats[s["category"] or "(없음)"] = cats.get(s["category"] or "(없음)", 0) + 1
    record.update({"status": "ok", "seeds": seeds, "category_counts": cats,
                   "kind_counts": {k: sum(1 for s in seeds if s["kind"] == k) for k in ("keep", "new")},
                   "role_counts": {k: sum(1 for s in seeds if s.get("role") == k) for k in ("naver", "wordpress")},
                   "track_counts": {k: sum(1 for s in seeds if s["track"] == k) for k in ("info", "money")},
                   "archived_previous": archived.name if archived else None})
    lp = write_log(record, today)
    print(json.dumps({"status": "ok", "seeds": [s["seed"] + (" | " + ", ".join(s["core"]) if s["core"] else "") for s in seeds],
                      "category_counts": cats, "kind_counts": record["kind_counts"], "track_counts": record["track_counts"], "usage": record["usage"]},
                     ensure_ascii=False, indent=2))
    log(f"[저장됨] {seeds_path}" + (f" (이전 것은 {archived.name} 로 보관)" if archived else ""))
    log(f"[저장됨] {lp}")
    log(f"[사용량] total_cost_usd = {record['usage']['total_cost_usd']}")
    for pr in record["problems"]:
        log(f"[주의] {pr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
