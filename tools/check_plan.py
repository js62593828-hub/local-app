# -*- coding: utf-8 -*-
"""주간 계획 합격 검사 (읽기 전용): pipeline/plan_YYYYMMDD.json 이 SPEC_v2.md 의 합격 기준을 지키는지 자동으로 본다.

사람이 눈으로 확인하던 것을 규칙으로 옮긴 것이다. API·Claude 호출 없음. 계획 파일과 기존 글 목록만 읽는다.
결과: 화면에 항목별 합격/불합격, pipeline/check_<계획파일이름>.json 저장, 전부 합격이면 종료 코드 0 / 아니면 1.

사용법
  python tools/check_plan.py                          # 가장 최근 plan_*.json (old·backup 제외)
  python tools/check_plan.py pipeline/plan_20261116.json
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spelling  # noqa: E402
import titles as titles_mod  # noqa: E402

TOOLS = Path(__file__).resolve().parent
BASE = TOOLS.parent
OUT_DIR = BASE / "pipeline"

# ---- 합격 기준 (SPEC_v2.md 2장과 같은 값. 바꿀 때는 두 곳을 함께 고친다) ----
# 편수는 tools/filter.json 의 weekly(track_quota·reserve_count)에서 읽는다 (예전 고정값 21/14/10 · 수익 6/4).
# 싼세계(워드프레스)를 껐으면 워드프레스는 0편. 켜서 검사하려면 --with-wp (filter.json 의 wp_on_values 값 사용).
def load_quotas(with_wp: bool = False):
    d = json.loads((TOOLS / "filter.json").read_text(encoding="utf-8-sig"))
    tq = {k: dict(v) for k, v in d["weekly"]["track_quota"].items()}
    reserve = int(d["weekly"]["reserve_count"])
    if with_wp:
        v = d.get("wp_on_values")
        if not v:
            raise SystemExit("--with-wp: filter.json 에 wp_on_values 가 없습니다.")
        tq["wordpress"] = dict(v["track_quota_wordpress"])
        reserve = int(v["reserve_count"])
    quota = {pl: int(tq[pl]["info"]) + int(tq[pl]["money"]) for pl in ("naver", "wordpress")}
    money = {pl: int(tq[pl]["money"]) for pl in ("naver", "wordpress")}
    return quota, reserve, money


QUOTA, RESERVE, MONEY_QUOTA = load_quotas()               # 수익글 편수(정확히), 나머지는 정보글
MAX_MONEY_PER_DAY = 1                                     # 플랫폼마다 하루 수익글 1편까지 (A12)
EXCLUDED_TYPES = {"brand", "local", "offtopic", "buyplace", "service"}
MAX_PER_CATEGORY_PER_PLATFORM = 4
MAX_PER_TOPIC = 2             # 같은 대상(topic 칸 = 정리된 대상 이름) 주 2편
MAX_PER_TOPIC_GROUP = 4       # 넓은 주제(topic_group 칸) 주 4편
MAX_PER_DETAIL = 2            # 지문의 세부(detail 칸: 트랩·필터 같은 부위·종류 낱말)가 같은 글 주 2편 (세부 '일반' 제외)
MAX_PEST_PER_PLATFORM = 2
MIN_VOLUME = 150            # 일정에 든 키워드의 월 검색량 하한
MIN_MEDIAN_VOLUME = 800     # 일정 전체 월 검색량 중간값 하한
TITLE_MAX_LEN = {"naver": 40, "wordpress": 60}
SYNONYMS = {"씽크대": "싱크대", "헹거": "행거", "렉": "랙", "스웨이드": "세무", "화장실": "욕실", **spelling.SPELL_UNIFY}
PEST_WORDS = ["바퀴", "벌레", "노래기", "지네", "거미", "말벌", "땅벌", "벌집", "빈대", "베드버그", "개미", "날파리", "초파리",
              "파리약", "바구미", "권연", "모기", "진드기", "나방", "그리마", "쥐", "연막탄", "살충", "구더기", "해충"]


def norm(s: str) -> str:
    t = "".join(re.sub(r"<[^>]+>", "", s or "").split()).lower()
    for a, b in SYNONYMS.items():
        t = t.replace(a, b)
    return t


def latest_plan() -> Path:
    cands = [p for p in OUT_DIR.glob("plan_*.json") if ".old_" not in p.name and "backup" not in p.name]
    if not cands:
        raise SystemExit("pipeline 폴더에 plan_*.json 이 없습니다.")
    return max(cands, key=lambda p: p.stat().st_mtime)


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return 0 if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def track_of(p: dict) -> str:
    """정보글/수익글 구분: 계획 글의 track 필드(info/money). 없으면 정보글로 본다."""
    return "money" if p.get("track") == "money" else "info"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    global QUOTA, RESERVE, MONEY_QUOTA
    args = [a for a in sys.argv[1:] if a != "--with-wp"]
    if "--with-wp" in sys.argv[1:]:
        QUOTA, RESERVE, MONEY_QUOTA = load_quotas(with_wp=True)
    path = Path(args[0]) if args else latest_plan()
    plan = json.loads(path.read_text(encoding="utf-8"))
    plans = plan.get("plans", [])
    sched = [p for p in plans if p.get("role") == "scheduled"]
    reserve = [p for p in plans if p.get("role") == "reserve"]
    try:
        existing = json.loads((OUT_DIR / "existing_keywords.json").read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        existing = []
    skip_sites = {"longterm", "content_queue.json"}        # 인포랩스·지원금 대기열은 주제가 달라 비교하지 않는다
    titles = [e.get("title") or e.get("focus_keyword") or "" for e in existing if e.get("site") not in skip_sites]

    results = []

    def check(code: str, name: str, ok: bool, detail: str = "") -> None:
        results.append({"code": code, "name": name, "ok": bool(ok), "detail": detail})

    # A1 편수
    n = {pl: sum(1 for p in sched if p.get("platform") == pl) for pl in QUOTA}
    check("A1", f"편수 (네이버 {QUOTA['naver']} · 워드프레스 {QUOTA['wordpress']} · 예비 {RESERVE})",
          n == QUOTA and len(reserve) == RESERVE, f"네이버 {n['naver']} · 워드프레스 {n['wordpress']} · 예비 {len(reserve)}")

    # A2 수익글 비중
    money = {pl: [p["keyword"] for p in sched if p.get("platform") == pl and track_of(p) == "money"] for pl in QUOTA}
    info_n = {pl: sum(1 for p in sched if p.get("platform") == pl and track_of(p) == "info") for pl in QUOTA}
    ok = all(len(money[pl]) == MONEY_QUOTA[pl] and info_n[pl] == QUOTA[pl] - MONEY_QUOTA[pl] for pl in QUOTA)
    check("A2", f"갈래별 편수 (네이버 정보 {QUOTA['naver'] - MONEY_QUOTA['naver']} + 수익 {MONEY_QUOTA['naver']} · 워드프레스 정보 {QUOTA['wordpress'] - MONEY_QUOTA['wordpress']} + 수익 {MONEY_QUOTA['wordpress']}, 정확히)", ok,
          f"네이버 정보 {info_n['naver']}·수익 {len(money['naver'])} · 워드프레스 정보 {info_n['wordpress']}·수익 {len(money['wordpress'])}")

    # A3 기존 글과 중복 (글자 기준: 표기 통일 후 키워드가 기존 제목 안에 들어 있거나 같음. 지문 색인이 생기면 지문 기준으로 바꾼다)
    nt = [norm(t) for t in titles if t]
    # 지문 기준(1단계): pipeline/post_index.json 의 기존 글 지문과 계획 글의 지문이 같으면 중복
    try:
        pidx = json.loads((OUT_DIR / "post_index.json").read_text(encoding="utf-8")).get("posts", {})
    except (OSError, ValueError):
        pidx = {}
    now_titles = {" ".join(t.split()) for t in titles if t}
    post_fp = {}
    for key, e in pidx.items():
        if e.get("fingerprint") and (key in now_titles or e.get("source") == "plan"):
            post_fp.setdefault(e["fingerprint"], e.get("title") or key)
    dup = []
    for p in sched + reserve:
        k = norm(p["keyword"])
        hit = next((t for t, n_ in zip(titles, nt) if len(k) >= 4 and k in n_), None)
        fp_hit = post_fp.get(p.get("fingerprint")) if p.get("fingerprint") else None
        if p.get("dup_existing") or hit or fp_hit:
            dup.append(f"{p['keyword']} ← {p.get('dup_existing') or fp_hit or hit}")
    check("A3", "기존 글과 중복 0건", not dup, "; ".join(dup[:8]))

    # A4 계획 안 중복 (같은 키워드 · 표기만 다른 키워드 · 같은 지문)
    seen, same = {}, []
    for p in sched + reserve:
        key = p.get("fingerprint") or norm(p["keyword"])
        if key in seen:
            same.append(f"{p['keyword']} = {seen[key]}")
        seen[key] = p["keyword"]
    check("A4", "계획 안 같은 글 0건", not same, "; ".join(same[:8]))

    # A5 제외 유형
    bad = [f"{p['keyword']}({p.get('kw_type')})" for p in sched + reserve if p.get("kw_type") in EXCLUDED_TYPES]
    check("A5", "제외 유형(브랜드·지역·주제 밖·구매처·업체 시공) 0건", not bad, ", ".join(bad[:8]))

    # A6 주제 쏠림
    over = []
    for pl in QUOTA:
        c = {}
        for p in sched:
            if p.get("platform") == pl:
                c[p.get("category") or "(없음)"] = c.get(p.get("category") or "(없음)", 0) + 1
        over += [f"{pl} {k} {v}편" for k, v in c.items() if k != "(없음)" and v > MAX_PER_CATEGORY_PER_PLATFORM]
    t = {}
    for p in sched:
        if p.get("topic"):
            t[p["topic"]] = t.get(p["topic"], 0) + 1
    over += [f"대상 '{k}' {v}편" for k, v in t.items() if v > MAX_PER_TOPIC]
    g = {}
    for p in sched:
        if p.get("topic_group"):
            gn = spelling.canon_topic_group(p["topic_group"], p.get("keyword"))   # 이름이 흔들려도 같은 묶음으로 센다
            g[gn] = g.get(gn, 0) + 1
    over += [f"넓은 주제 '{k}' {v}편" for k, v in g.items() if v > MAX_PER_TOPIC_GROUP]
    d = {}
    for p in sched:
        if p.get("detail") and p["detail"] != "일반":
            d[p["detail"]] = d.get(p["detail"], 0) + 1
    over += [f"세부 '{k}' {v}편" for k, v in d.items() if v > MAX_PER_DETAIL]
    check("A6", f"쏠림 없음 (카테고리 플랫폼당 {MAX_PER_CATEGORY_PER_PLATFORM}편 · 같은 대상 {MAX_PER_TOPIC}편 · 넓은 주제 {MAX_PER_TOPIC_GROUP}편 · 같은 세부 {MAX_PER_DETAIL}편 이하)", not over, ", ".join(over[:8]))

    # A7 해충
    pest = {pl: [p["keyword"] for p in sched if p.get("platform") == pl
                 and any(w in norm(p["keyword"]) or w in norm(p.get("topic") or "") for w in PEST_WORDS)] for pl in QUOTA}
    check("A7", f"해충 글 플랫폼당 {MAX_PEST_PER_PLATFORM}편 이하", all(len(v) <= MAX_PEST_PER_PLATFORM for v in pest.values()),
          f"네이버 {len(pest['naver'])} · 워드프레스 {len(pest['wordpress'])}")

    # A8 검색량
    vols = [p.get("monthly_total") or 0 for p in sched]
    low = [f"{p['keyword']}({p.get('monthly_total')})" for p in sched if (p.get("monthly_total") or 0) < MIN_VOLUME]
    check("A8", f"검색량 (모두 {MIN_VOLUME} 이상 · 중간값 {MIN_MEDIAN_VOLUME} 이상)", not low and median(vols) >= MIN_MEDIAN_VOLUME,
          f"중간값 {median(vols):.0f}" + (" / 미달: " + ", ".join(low[:8]) if low else ""))

    # A9 제목: 확정 제목 1개 + 예비 2개가 모두 필수 규칙(키워드 그대로 포함·길이·금지어·브랜드 없음·상위 글/내 글과 0.7 미만)을 지킴
    #    일정 + 예비 전부 검사. 예비(reserve)는 플랫폼이 정해지지 않아 짧은 쪽(네이버 40자)으로 본다.
    rules = titles_mod.load_rules()
    own = titles_mod.own_titles()
    bad_t, viol = [], 0
    for p in sched + reserve:
        ft = p.get("final_title")
        alts = p.get("alt_titles") or []
        if not ft:
            bad_t.append(f"{p['keyword']}: 확정 제목 없음")
            continue
        if len(alts) < 2:
            bad_t.append(f"{p['keyword']}: 예비 제목 {len(alts)}개 (2개 필요)")
        tk_of = {s.get("title"): s.get("title_keyword") for s in p.get("title_scores") or []}
        ctx = {"keyword": p["keyword"], "platform": p.get("platform") or "reserve", "own_titles": own, "track": track_of(p),
               "top_titles": [x.get("title") for x in p.get("top_posts") or []]}
        nat = p.get("title_natural") or {}
        ctx["unnatural"] = {titles_mod.norm(t) for t, ok in nat.items() if not ok}
        ctx["target_words"] = (p.get("intent_info") or {}).get("target_words") or []
        for label, tt in [("확정", ft)] + [("예비", a) for a in alts]:
            fails = titles_mod.check_required(tt, tk_of.get(tt) or p.get("title_keyword") or "", ctx, rules)
            if fails:
                viol += 1
                bad_t.append(f"{p['keyword']} {label} '{tt[:20]}…': " + ", ".join(titles_mod.FAIL_KO[f] for f in fails))
    check("A9", "제목 (확정 1 + 예비 2 · 필수 규칙 위반 0건)", not bad_t,
          ("규칙 위반 %d건 · " % viol if viol else "") + "; ".join(bad_t[:6]) + (f" 외 {len(bad_t) - 6}건" if len(bad_t) > 6 else ""))

    # A10 근거 항목: 예상 클릭 · 시기 표시
    miss = [p["keyword"] for p in sched
            if not isinstance(p.get("expected_clicks"), (int, float)) or not p.get("clicks_basis")
            or (p.get("timing") or {}).get("label") not in ("상승", "유지", "선발행") or not (p.get("timing") or {}).get("reason")]
    for p in sched + reserve:       # 4.7: 일정·예비 모든 글에 본문 설계(outline)와 상위 5개 글(top_posts)이 있음 (교체로 들어온 글도)
        lack = [n for n, v in (("outline", p.get("outline")), ("top_posts", p.get("top_posts"))) if not v]
        if lack:
            miss.append(f"{p['keyword']}({'·'.join(lack)} 없음)")
    check("A10", "근거 (예상 클릭·계산에 쓴 값 · 시기 label/reason · 일정·예비 모두 outline·top_posts) 모두 있음", not miss, f"{len(miss)}편 누락: " + ", ".join(miss[:5]) if miss else "")

    # A11 마지막 검사에서 남은 문제
    probs = plan.get("problems") or []
    check("A11", "계획 파일에 남은 문제 0건", not probs, "; ".join(str(x)[:80] for x in probs[:3]))

    # A12 수익글 분산: 플랫폼마다 하루 수익글 1편 이하
    crowd = []
    for pl in QUOTA:
        per_day = {}
        for p in sched:
            if p.get("platform") == pl and track_of(p) == "money":
                per_day.setdefault(p.get("publish_date"), []).append(p["keyword"])
        crowd += [f"{pl} {d}: {', '.join(v)}" for d, v in sorted(per_day.items(), key=lambda x: str(x[0])) if len(v) > MAX_MONEY_PER_DAY]
    check("A12", f"수익글 분산 (플랫폼별 하루 {MAX_MONEY_PER_DAY}편 이하)", not crowd, "; ".join(crowd[:4]))

    # A13 제목 틀 쏠림 없음: 한 주 확정 제목에서 같은 틀은 플랫폼별(일정 네이버·워드프레스, 예비) 최대 30%. 틀은 plan 의 title_pattern 칸
    share = float(rules.get("pattern_max_share", 0.3))
    skew = []
    for label, grp in (("네이버", [p for p in sched if p.get("platform") == "naver"]),
                       ("워드프레스", [p for p in sched if p.get("platform") == "wordpress"]), ("예비", reserve)):
        cap = max(1, int(len(grp) * share + 1e-9))
        cnt = {}
        for p in grp:
            pat = titles_mod.pattern_of(p.get("final_title") or "") if p.get("final_title") else "(제목 없음)"      # 틀은 코드 판정
            cnt[pat] = cnt.get(pat, 0) + 1
        skew += [f"{label} '{k}' {v}편 (한도 {cap})" for k, v in cnt.items() if v > cap]
        ng = {}
        for p in grp:
            n = titles_mod.n_gaji(p.get("final_title") or "")
            if n:
                ng[n] = ng.get(n, 0) + 1
        skew += [f"{label} '{k}가지' {v}편 (한도 2)" for k, v in ng.items() if v > 2]
    check("A13", f"제목 틀 쏠림 없음 (코드 판정 틀, 같은 틀 플랫폼별 {int(share * 100)}% 이하 · 같은 N가지 2편 이하)", not skew, "; ".join(skew[:6]))

    passed = sum(1 for r in results if r["ok"])
    print(f"합격 검사: {path.name} — {passed}/{len(results)} 통과")
    for r in results:
        print(f"  [{'합격' if r['ok'] else '불합격'}] {r['code']} {r['name']}" + (f" — {r['detail']}" if r["detail"] else ""))
    out = path.parent / f"check_{path.stem}.json"      # 계획 파일 옆에 저장 (시험 폴더의 계획이 운영 검사 결과를 덮지 않게)
    try:
        out.write_text(json.dumps({"plan": path.name, "passed": passed, "total": len(results), "results": results},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[저장됨] {out}")
    except OSError as e:
        print(f"[주의] 결과 저장 실패: {e}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
