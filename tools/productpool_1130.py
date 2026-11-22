"""11/30 주 21칸 제품 후보 풀 (읽기 전용). 결과는 pipeline/stage_productpool_run/20261130/ 에만 저장.
기존 product_candidates 의 읽기 함수만 가져다 쓴다(기존 파일 수정 없음).
단계: python tools/productpool_1130.py collect   (쇼핑커넥트 목록 읽기, 에고라이트)
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_candidates as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# 지금 에고라이트에 로그인된 쇼핑커넥트 계정 주소(기존 도구의 번호와 다름). 이 스크립트 안에서만 바꿔 씀.
pc.BRANDCONNECT = "https://brandconnect.naver.com/1020008901769280/affiliate/products/search"
OUT = pc.ROOT / "pipeline" / "stage_productpool_run" / "20261130"
RAW = OUT / "raw_naver_parts"
TOPN = 40   # 화면에 하나씩 보이게 하는 읽기가 15초 안에 끝나도록(60은 시간 초과)

# 칸 번호 → (틀, 갈래, 제품군, 검색어 목록)
CELLS = {
 "A1": ("A", "책상 정리", "데스크매트", ["데스크매트"]),
 "A2": ("A", "책상 정리", "케이블 정리", ["케이블 정리"]),
 "A3": ("A", "책상 정리", "탁상시계", ["탁상시계"]),
 "A4": ("A", "키보드·마우스", "노트북 거치대", ["노트북 거치대", "노트북 스탠드", "노트북 받침대"]),
 "A5": ("A", "충전·멀티탭", "USB 허브·도킹스테이션", ["USB 허브", "도킹스테이션"]),
 "A6": ("A", "의자·쿠션", "등쿠션", ["등쿠션"]),
 "A7": ("A", "계절가전", "USB 미니 가습기", ["USB 미니 가습기", "미니 가습기", "무선 가습기", "휴대용 가습기", "탁상 가습기"]),
 "B1": ("B", "책상 정리", "모니터 받침대", ["모니터 받침대"]),
 "B2": ("B", "책상 정리", "독서대", ["독서대"]),
 "B3": ("B", "충전·멀티탭", "무선충전 패드", ["무선충전 패드"]),
 "B4": ("B", "이어폰·스피커", "헤드셋 거치대", ["헤드셋 거치대"]),
 "B5": ("B", "이어폰·스피커", "데스크 사운드바", ["사운드바", "데스크 사운드바", "PC 사운드바", "컴퓨터 사운드바", "미니 사운드바"]),
 "B6": ("B", "이어폰·스피커", "헤드폰", ["헤드폰"]),
 "B7": ("B", "의자·쿠션", "사무용 의자", ["사무용 의자"]),
 "B8": ("B", "계절가전", "머그 워머", ["머그 워머", "컵워머", "머그컵 워머", "컵 보온 받침대", "보온 컵받침", "컵 히터", "컵 데우기", "머그 보온기"]),
 "B9": ("B", "계절가전", "전기방석", ["전기방석", "온열방석", "USB 방석 히터"]),
 "B10": ("B", "계절가전", "충전식 손난로", ["충전식 손난로"]),
 "B11": ("B", "조명", "책상스탠드", ["책상스탠드"]),
 "B12": ("B", "조명", "책상 하부 LED 스트립", ["LED 스트립"]),
 "B13": ("B", "모니터·노트북", "미니PC", ["미니PC", "미니PC 인텔", "미니PC 라이젠", "베어본 미니PC", "맥미니", "미니 컴퓨터", "초소형 PC", "미니PC 사무용", "미니PC 게이밍", "미니PC N100"]),
 "B14": ("B", "모니터·노트북", "태블릿", ["태블릿"]),
}


def all_queries() -> list[str]:
    out = []
    for v in CELLS.values():
        for q in v[3]:
            if q not in out:
                out.append(q)
    return out



# ───────────────────────── 선별 ─────────────────────────
import re, glob  # noqa: E402
import radar_used_products as ru  # noqa: E402

# 칸별 '이름에 이 말이 있어야 그 칸 제품군'(inc) / '이 말이 있으면 다른 종류'(exc)
KIND = {
 "A1": (r"데스크\s?매트|데스크\s?패드|책상\s?매트|책상\s?패드|장패드|데스크\s?플레이트", r"방석|의자|차량|발매트|현관"),
 "A2": (r"케이블\s?(정리|홀더|클립|타이|슬리브|트레이|몰딩|고정|벨크로|스티커|박스|정돈)|선\s?정리|전선\s?(정리|몰딩|커버)|전선정리|코드\s?정리", r"충전\s?케이블\s?\d|꽂이"),
 "A3": (r"탁상\s?시계|알람\s?시계|디지털\s?시계|LED\s?시계|무소음\s?시계|스탠드\s?시계|전자\s?시계|테이블\s?시계|미니\s?시계|책상\s?시계", r"벽시계|스마트워치|손목|워치|밴드"),
 "A4": (r"노트북\s?(거치대|스탠드|받침|받침대)", r"쿨링패드|쿨러|차량|침대|테이블|이동식"),
 "A5": (r"USB\s?허브|멀티\s?허브|도킹\s?스테이션|도킹스테이션|C타입\s?허브|USB-?C\s?허브|USB\s?-?C\s?허브|\b독\s?스테이션", r"스마트홈|케이스|가방|파우치|젠더|케이블만"),
 "A6": (r"등\s?쿠션|등받이\s?쿠션|허리\s?쿠션|요추\s?쿠션|럼버\s?서포트|등받이", r"차량|자동차|전동|안마|마사지|캠핑|바닥|좌식|유모차|커버만|방석\s?세트"),
 "A7": (r"가습기", r"필터|교체|세정|청소|스틱|오일|대용량\s?[3-9]|\d{1,2}L"),
 "B1": (r"모니터\s?(받침|스탠드|선반|올림|거치|대\b)|데스크\s?선반|책상\s?선반", r"모니터\s?암|듀얼\s?암|벽걸이|TV\s?받침|브라켓|마운트|클램프"),
 "B2": (r"독서대|북\s?스탠드|책\s?받침|북스탠드|책\s?거치", r"악보|보면대"),
 "B3": (r"무선\s?충전\s?(패드|기|독|스탠드|거치|판)|맥세이프|Qi2?\s?(무선\s?)?충전|충전\s?패드|무선충전", r"보조배터리|차량|자동차|케이블\s?\d|링\b|케이스\s?만|어댑터\s?만|손난로"),
 "B4": (r"(헤드셋|헤드폰|이어폰)\s?(거치대|스탠드|걸이|행거|홀더|받침)|(헤드셋|헤드폰)\s?스탠드", r"이어패드|쿠션\s?커버"),
 "B5": (r"사운드\s?바", r"브라켓|마운트|거치대|벽걸이"),
 "B6": (r"헤드폰|헤드셋|오버이어", r"거치대|스탠드|행거|걸이|케이스|파우치|이어패드|이어\s?쿠션|커버|어댑터|케이블|이어폰|버즈|에어팟|캐리|보관|홀더|교체"),
 "B7": (r"(사무|책상|컴퓨터|메쉬|오피스|학생|공부|게이밍|PC|서재|사무용)\s?(의자|체어)|오피스\s?체어|사무의자|메쉬의자", r"커버|쿠션만|바퀴만|캐스터|발받침|팔걸이만|매트|유아|아기|캠핑|접이식\s?낚시|높이조절\s?발판"),
 "B8": (r"워머|보온\s?(코스터|받침|컵받침)|컵\s?히터|컵\s?보온|머그\s?히터", r"텀블러(?!.*워머)|스프|요리|전골"),
 "B9": (r"전기\s?방석|온열\s?방석|열선\s?방석|발열\s?방석|히팅\s?(방석|패드)|전기\s?(요|매트|장판)", r"커버만|전기요|전기\s?장판|침대|전기매트|캠핑|차량|발열\s?조끼"),
 "B10": (r"손난로|핫팩|손\s?온열|손\s?히터", r"일회용|붙이는|파스|일회|박스\s?\d{2,}|찜질팩\s?\d"),
 "B11": (r"스탠드|데스크\s?램프|독서등|책상\s?(조명|등)|LED\s?램프|학습\s?(조명|스탠드)|무선\s?램프", r"거치대|태블릿|노트북|핸드폰|휴대폰|스마트폰|모니터|독서대|마이크|암\b|헤드|우산|옷|거울|무드|캠핑|랜턴|무선\s?청소기|조명\s?받침|클립\s?형\s?거치"),
 "B12": (r"스트립|줄조명|LED\s?바|라이트\s?바|라인\s?조명|라이트스트립|LED\s?테이프|네온\s?플렉스", r"차량|자동차|네온사인|트리|크리스마스|전구\s?\d|캠핑|오토바이|선풍기|가습기"),
 "B13": (r"미니\s?PC|미니\s?피씨|미니\s?컴퓨터|베어본|\bNUC\b|미니\s?데스크탑|미니\s?워크스테이션", r"케이스|거치|브라켓|마운트|쿨러|어댑터|케이블|파워|주변|속발열|스티커|청소|스탠드|선반"),
 "B14": (r"태블릿|갤럭시\s?탭|아이패드|샤오미\s?패드|패드\s?(프로|에어|미니)|탭\s?[AS]\d", r"케이스|필름|거치대|스탠드|펜슬|펜\b|키보드|파우치|보호|스크린|강화|커버|충전|어댑터|키즈커버|액정|케이블|터치펜|스타일러스|젠더|독\b|거치|바운서|가방|백팩|슬리브|교환권|중고|리퍼|렌탈"),
}
# 칸별로 요구하는 추가 힌트(이름에 이 중 하나가 있어야 함)
NEED = {
 "A7": r"USB|미니|소형|휴대|무선|탁상|충전|캔|컵|텀블러|데스크|머그|usb|책상|차량",
 "B5": r"데스크|PC|컴퓨터|모니터|책상|미니|소형|USB|게이밍|컴팩트|2\.0|2ch|블루투스|2채널",
 "B10": r"충전|보조배터리|USB|무선|전자|배터리|마그네틱|양면|rechargeable",
 "B11": r"책상|데스크|학습|독서|LED|무선|충전|조명|램프|스탠드\s?(조명|등|라이트)|무드|터치|밝기|색온도|3단|눈\s?건강|공부|리모컨|클립",
}
OVERSEAS = r"해외\s?직구|직구|110\s?V|해외\s?전용|해외용|미국형|일본형|유럽형|영국형|UK\s?플러그|US\s?플러그|EU\s?플러그|\bUS\b|\bUK\b|일본\s?정품|미국\s?정품|돼지코\s?필요"
# 11/23 주 제품군 15개 — 이름이 아니라 '가장 아래 카테고리'로 분류되는 상품을 뺌(제품군 → 카테고리 글자 패턴)
G1123 = {
 "블루투스스피커": r"블루투스\s?스피커", "보조배터리": r"보조\s?배터리", "모니터암": r"모니터\s?암", "모니터조명": r"모니터\s?(조명|라이트)|스크린\s?바",
 "모니터": r"^모니터$|^(일반|게이밍|와이드|LED|LCD|터치)?\s?모니터$|^(PC)?\s?모니터$", "키보드": r"^(?!.*(패드|받침|커버|스킨|청소)).*키보드", "멀티탭": r"멀티탭|멀티\s?콘센트|전기\s?콘센트",
 "발받침대": r"발\s?받침|풋레스트", "무선마우스": r"^(무선|게이밍|블루투스|유선|버티컬|트랙볼)?\s?마우스$", "발난로": r"발\s?(난로|온열|히터)", "무선이어폰": r"이어폰|이어버드|무선\s?이어",
 "무드등": r"무드\s?등|수면등|취침등", "미니히터": r"미니\s?히터|전기\s?히터|온풍기|팬\s?히터|전기\s?난로|히터", "고속충전기": r"^(?!.*무선).*(충전기|어댑터)|고속충전", "손목받침대": r"손목\s?받침|팜레스트|손목\s?보호",
}
PRICE_LO, PRICE_HI = 0.5, 2.0


def cat_group_1123(whole: str) -> str:
    """전체 카테고리 글자의 가장 아래 단계(또는 아래 두 단계)가 11/23 주 제품군이면 그 이름."""
    parts = [x.strip() for x in re.split(r"[>＞]", whole or "") if x.strip()]
    if not parts:
        return ""
    low = parts[-1]
    for g, pat in G1123.items():
        if re.search(pat, low):
            return g
    return ""


def brand_label(name: str, store: str, kw: str) -> str:
    b = pc.canon_brand(name, store, kw)
    if b:
        # 대표 이름이 정규화된 영문이면 상품명에서 실제 표기를 찾음
        m = re.search(re.escape(b), re.sub(r"[\s\-_·]", "", name).lower())
        for w in re.split(r"[\s\[\]()]+", name):
            if w and pc._norm(w) == b:
                return w
        return b
    s = re.sub(r"(공식\s?(몰|스토어|샵|판매처)?|스토어|샵|몰|코리아|스토리)\s*$", "", store or "").strip()
    return s or pc.UNKNOWN_BRAND


def model_label(name: str, brand: str) -> str:
    m = pc.model_of(name)
    if m:
        return m
    k = ru.model_key(name)
    tail = k.split("|", 1)[1] if "|" in k else ""
    return tail or "(모델명 미상)"


def load_items(kw: str) -> list[dict]:
    f = pc._cache_path(RAW, kw)
    d = json.loads(f.read_text(encoding="utf-8"))
    its = [pc.parse_naver_item(x, i, d["kw"]) for i, x in enumerate(d["items"], 1)]
    for x in its:
        x["pid"] = pc._pid(x)
        x["brand_label"] = brand_label(x["name"], x["store"], kw)
        x["canon"] = pc.canon_brand(x["name"], x["store"], kw) or pc._norm(x["brand_label"])
        x["model"] = model_label(x["name"], x["brand_label"])
        x["mkey"] = ru.model_key(x["name"])
        x["query"] = kw
    return its


def cell_items(cell: str) -> tuple[list[dict], dict]:
    """칸의 검색어들에서 모은 상품 중 이름으로 그 칸 제품군인 것만. (목록, 뺀 이유 개수)"""
    inc, exc = KIND[cell]
    seen, out, why = set(), [], {"종류 아님(이름)": 0, "해외 전용": 0, "평점 4.5 미만/없음": 0, "중복 상품": 0}
    for q in CELLS[cell][3]:
        for x in load_items(q):
            if x["pid"] in seen:
                continue
            seen.add(x["pid"])
            n = x["name"]
            if not re.search(inc, n, re.I) or re.search(exc, n, re.I) or (cell in NEED and not re.search(NEED[cell], n, re.I)):
                why["종류 아님(이름)"] += 1
                continue
            if re.search(OVERSEAS, n, re.I):
                why["해외 전용"] += 1
                continue
            if x["rating"] is None or x["rating"] < 4.5:
                why["평점 4.5 미만/없음"] += 1
                continue
            out.append(x)
    out.sort(key=lambda x: -(x["reviews"] or 0))
    return out, why




# ───────────────────────── 칸별 카테고리 허용(첫 실행 뒤 catdump 로 보고 정함) ─────────────────────────
CATOK: dict[str, str] = {   # 첫 실행에서 읽은 카테고리를 보고 정한 '칸별 허용 카테고리 글자'
 "A1": r"패드|매트", "A2": r"케이블|정리|전선", "A3": r"시계", "A4": r"노트북", "A5": r"허브|도킹", "A6": r"쿠션",
 "A7": r"가습기", "B1": r"받침|선반|스탠드|거치", "B2": r"독서대|북스탠드|스탠드", "B3": r"무선충전|충전", "B4": r"거치|스탠드|걸이|헤드",
 "B5": r"스피커|사운드바", "B6": r"헤드폰|헤드셋", "B7": r"의자", "B8": r"워머|머그|보온|가열|히터|온열", "B9": r"방석|온열",
 "B10": r"손난로|핫팩|온열", "B11": r"스탠드|조명|램프|LED", "B12": r"조명|스트립|LED", "B13": r"PC|컴퓨터|베어본", "B14": r"태블릿",
}
CAT_DIR = OUT / "raw_category_parts"
LOG = {"ads_calls": 0, "cat_pages": 0}


def load_cats() -> dict[str, str]:
    out = {}
    if CAT_DIR.exists():
        for f in CAT_DIR.glob("*.json"):
            if f.name.startswith("_"):
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
            out[str(d["id"])] = d.get("category", "")
    return out


def cat_bad(cell: str, pid: str, cats: dict[str, str]) -> str:
    """카테고리를 읽었는데 이 칸에 안 맞으면 이유, 맞거나 못 읽었으면 ''."""
    whole = cats.get(pid)
    if not whole:
        return ""
    g = cat_group_1123(whole)
    # 무선충전 패드는 네이버 카테고리가 그냥 '충전기'라 고속충전기와 카테고리로 못 가름 → 이름(무선충전)으로 이미 가렸음
    if g == "고속충전기" and cell == "B3":
        g = ""
    if g:
        return f"11/23 주 제품군({g})"
    if cell in CATOK and not re.search(CATOK[cell], whole):
        return "카테고리 불일치"
    return ""


def dedup_models(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for x in items:   # 리뷰 많은 순으로 들어옴 → 같은 모델은 리뷰 많은 것 하나만
        k = x["mkey"] if ru.strong_key(x["mkey"]) else x["mkey"] + "|" + x["pid"] if not x["mkey"] else x["mkey"]
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def build_pools(banned: set[str], cats: dict[str, str]):
    """칸마다 후보 풀(장부·교차 칸 중복·카테고리 불량 제외). 반환 {칸: {'pool': [...], 'info': {...}}}"""
    led = ru.load_ledger()
    blocked = pc.radar_blocked(led)
    pools = {}
    for c in CELLS:
        items, why = cell_items(c)
        ok, out = pc.split_blocked(items, blocked)
        why["장부 제외"] = len(out)
        ok2 = []
        for x in ok:
            if x["pid"] in banned:
                continue
            r = cat_bad(c, x["pid"], cats)
            if r:
                why[r] = why.get(r, 0) + 1
                continue
            ok2.append(x)
        pools[c] = {"pool": dedup_models(ok2), "info": why}
    # 같은 모델은 21칸 중 한 칸에만: 리뷰가 더 많은 칸에 둠
    best: dict[str, tuple[int, str]] = {}
    for c, v in pools.items():
        for x in v["pool"]:
            k = x["mkey"] if ru.strong_key(x["mkey"]) else "pid:" + x["pid"]
            if k not in best or (x["reviews"] or 0) > best[k][0]:
                best[k] = (x["reviews"] or 0, c)
    for c, v in pools.items():
        keep = []
        for x in v["pool"]:
            k = x["mkey"] if ru.strong_key(x["mkey"]) else "pid:" + x["pid"]
            if best[k][1] == c:
                keep.append(x)
            else:
                v["info"]["다른 칸에 둠"] = v["info"].get("다른 칸에 둠", 0) + 1
        v["pool"] = keep
    return pools


def pick_cell(cell: str, pool: list[dict]) -> dict:
    frame = CELLS[cell][0]
    if frame == "A":
        main = pool[:7]
        names = lambda L: {x["canon"] for x in L if x["canon"] and x["brand_label"] != pc.UNKNOWN_BRAND}
        i = 7
        while len(names(main)) < 3 and i < len(pool):   # 브랜드 3개 미만이면 다른 브랜드 것으로 아래쪽을 바꿈
            if pool[i]["canon"] not in names(main) and pool[i]["brand_label"] != pc.UNKNOWN_BRAND:
                main = main[:-1] + [pool[i]]
            i += 1
        return {"main": main, "alt": []}
    main = pool[:4]
    mb = {x["canon"] for x in main}
    alt, ab = [], set()
    for x in pool[4:]:
        if x["canon"] in mb or x["canon"] in ab or x["brand_label"] == pc.UNKNOWN_BRAND:
            continue
        alt.append(x)
        ab.add(x["canon"])
        if len(alt) == 3:
            break
    return {"main": main, "alt": alt}


def need_cats(sel: dict) -> list[dict]:
    out = []
    for c, v in sel.items():
        for x in v["main"] + v["alt"]:
            out.append({"id": x["pid"], "url": x["url"]})
    return out


def fetch_cats(prods: list[dict]) -> str:
    return pc.collect_in_batches(prods, lambda q: q["id"], CAT_DIR, pc.BATCH_CAT,
                                 lambda part, tmp: pc._js_category(part, tmp), "category", "카테고리 확인")


def select_loop(max_rounds: int = 8, fetch: bool = True):
    banned: set[str] = set()
    for rnd in range(1, max_rounds + 1):
        cats = load_cats()
        pools = build_pools(banned, cats)
        sel = {c: pick_cell(c, v["pool"]) for c, v in pools.items()}
        if not fetch:
            return pools, sel, cats
        todo = [p for p in need_cats(sel) if p["id"] not in cats]
        print(f"[선별 {rnd}회차] 카테고리 확인 필요 {len(todo)}개")
        if not todo:
            return pools, sel, cats
        st = fetch_cats(todo)
        if st != "ok":
            print("카테고리 확인 중단:", st)
            return pools, sel, load_cats()
    return pools, sel, load_cats()


def catdump() -> None:
    pools, sel, cats = select_loop(fetch=False)
    from collections import Counter
    for c in CELLS:
        cnt = Counter(pc.lowest_category(cats[x["pid"]]) for x in sel[c]["main"] + sel[c]["alt"] if cats.get(x["pid"]))
        print(c, CELLS[c][2], dict(cnt), "| 풀", len(pools[c]["pool"]))




# ───────────────────────── 검색량(검색광고 키워드 도구, 모델명 검색어) ─────────────────────────
VOL_CACHE = OUT / "volume_cache.json"
VOL_MIN = 30


def qlabel(x: dict) -> str:
    m = x["model"]
    if m.startswith("("):
        toks = [t for t in re.split(r"\s+", re.sub(r"\[[^\]]*\]", " ", x["name"])) if t and t != x["brand_label"]]
        return f'{x["brand_label"]} {" ".join(toks[:2])}'.strip()
    return f'{x["brand_label"]} {m}'.strip()


def fetch_volumes(queries: list[str]) -> dict[str, dict]:
    sys.path.insert(0, str(pc.ROOT))
    import naver_api  # noqa: E402
    cache = json.loads(VOL_CACHE.read_text(encoding="utf-8")) if VOL_CACHE.exists() else {}
    todo = [q for q in dict.fromkeys(queries) if q not in cache]
    calls = 0
    for i in range(0, len(todo), 5):
        part = todo[i:i + 5]
        rows = naver_api.get_keyword_stats(part, expand=False)
        calls += 1
        by = {re.sub(r"\s", "", r["keyword"]).lower(): r for r in rows}
        for q in part:
            r = by.get(re.sub(r"\s", "", q).lower())
            if r:
                tot = r["pc_count"] + r["mobile_count"]
                cache[q] = {"pc": r["pc_count"], "mobile": r["mobile_count"], "total": tot,
                            "note": "10 미만" if tot <= 10 else ""}
            else:
                cache[q] = {"pc": 0, "mobile": 0, "total": 0, "note": "조회 결과에 없음(10 미만으로 봄)"}
        VOL_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    ads_log = OUT / "ads_calls.txt"
    prev = int(ads_log.read_text()) if ads_log.exists() else 0
    ads_log.write_text(str(prev + calls), encoding="utf-8")
    return cache


# ───────────────────────── 최종 파일 ─────────────────────────

def won(v) -> str:
    return f"{v:,}원" if v else "가격 표시 안 됨"


def row_of(cell: str, x: dict, cats: dict, role: str, vol: dict | None) -> dict:
    whole = cats.get(x["pid"], "")
    r = {"칸": cell, "역할": role, "상품id": x["pid"], "브랜드": x["brand_label"], "모델명": x["model"], "상품명": x["name"],
         "실제 종류": pc.lowest_category(whole) if whole else "확인 못 함(이름 기준)", "전체 카테고리": whole,
         "가격": x["price"], "평점": x["rating"], "리뷰 수": x["reviews"], "판매처": x["store"], "검색어": x["query"],
         "주소": x["url"], "모델키": x["mkey"]}
    if vol is not None:
        r["모델명 검색어"] = qlabel(x)
        r["월간 검색량"] = vol["total"]
        r["검색량 비고"] = vol["note"]
    return r


def final() -> None:
    pools, sel, cats = select_loop(fetch=False)
    led = ru.load_ledger()
    bq = [qlabel(x) for c in CELLS if CELLS[c][0] == "B" for x in sel[c]["main"]]
    vols = fetch_volumes(bq)
    out_cells = {}
    for c, (frame, gal, grp, qs) in CELLS.items():
        m, a = sel[c]["main"], sel[c]["alt"]
        rows = [row_of(c, x, cats, "본상품" if frame == "B" else "후보", vols[qlabel(x)] if frame == "B" else None) for x in m]
        arows = [row_of(c, x, cats, "대안", None) for x in a]
        note = []
        if frame == "A":
            nb = len({x["canon"] for x in m if x["brand_label"] != pc.UNKNOWN_BRAND})
            if len(m) < 6 or nb < 3:
                note.append(f"후보 부족: {c}·{len(m)}개(브랜드 {nb}개)")
        else:
            if len(m) < 3 or len(a) < 2:
                note.append(f"후보 부족: {c}·본상품 {len(m)}개·대안 {len(a)}개")
            if m and all(vols[qlabel(x)]["total"] < VOL_MIN for x in m):
                note.append("검색량 부족")
        out_cells[c] = {"틀": frame, "갈래": gal, "제품군": grp, "검색어": qs, "후보": rows, "대안": arows, "메모": note,
                        "뺀 개수": pools[c]["info"], "풀 크기": len(pools[c]["pool"])}
    (OUT / "pool.json").write_text(json.dumps({"기준일": "2026-11-22", "대상 주": "2026-11-30", "cells": out_cells},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    write_pool_md(out_cells)
    write_check_md(out_cells, led, cats)
    print("저장:", OUT)


def write_pool_md(cells: dict) -> None:
    L = ["# 11/30 주 제품 후보 풀 (21칸)", "",
         "- 만든 날: 2026-11-22 · 출처: 네이버 쇼핑커넥트 검색 목록(읽기만) · 평점·리뷰 수는 네이버 기준",
         "- 실제 종류 = 상품 스토어 페이지의 가장 아래 카테고리. '확인 못 함'이면 이름으로만 판단.",
         "- 기준: 평점 4.5 이상, 리뷰 많은 순, 해외 전용·장부 모델·11/23 주 제품군 제외, 한 모델은 한 칸에만.", ""]
    for c, v in cells.items():
        isb = v["틀"] == "B"
        L += [f"## {c} · {v['제품군']} (틀 {v['틀']} · {v['갈래']})", ""]
        if v["메모"]:
            L += [f"- **{' / '.join(v['메모'])}**", ""]
        head = ["칸", "상품id", "브랜드", "모델명", "상품명", "실제 종류", "가격", "평점", "리뷰 수", "판매처"] \
            + (["모델명 월간 검색량"] if isb else []) + ["비고"]
        L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for r in v["후보"] + v["대안"]:
            nm = r["상품명"].replace("|", "/")[:60]
            extra = []
            if r["역할"] == "대안":
                extra.append("대안(다른 브랜드)")
            elif isb:
                extra.append("본상품")
            if isb and r["역할"] == "본상품" and r.get("검색량 비고"):
                extra.append(r["검색량 비고"])
            if isb and r["역할"] == "본상품" and not re.search(r"\d", r["모델명"]):
                extra.append("모델 코드 없음(검색어는 브랜드+이름 앞부분)")
            vcol = []
            if isb:
                vcol = [f"{r['월간 검색량']:,} (검색어: {r['모델명 검색어']})" if r["역할"] == "본상품" else "-"]
            row = [c, r["상품id"], r["브랜드"], r["모델명"], nm, r["실제 종류"], won(r["가격"]), str(r["평점"]),
                   f"{r['리뷰 수']:,}", r["판매처"]] + vcol + [" · ".join(extra)]
            L.append("| " + " | ".join(x.replace("|", "/") for x in row) + " |")
        L.append("")
    (OUT / "pool.md").write_text("\n".join(L), encoding="utf-8")


def write_check_md(cells: dict, led: dict, cats: dict) -> None:
    from collections import defaultdict
    allr = [r for v in cells.values() for r in v["후보"] + v["대안"]]
    L = ["# 11/30 주 제품 풀 검수표", "", "## 1. 21칸 표 · 후보 부족", "", "| 칸 | 표 있음 | 후보 부족/메모 |", "|---|---|---|"]
    for c, v in cells.items():
        L.append(f"| {c} {v['제품군']} | {'O' if v['후보'] else 'X'} | {' / '.join(v['메모']) or '-'} |")
    L += ["", "## 2. 칸별 기준 충족 (O/X)", "",
          "| 칸 | 후보(본상품) 수 | 브랜드 수 | 기준 | 대안 수 | 대안 기준(2개↑) | 비고 |", "|---|---|---|---|---|---|---|"]
    for c, v in cells.items():
        m, a = v["후보"], v["대안"]
        nb = len({r["브랜드"] for r in m if r["브랜드"] != pc.UNKNOWN_BRAND})
        if v["틀"] == "A":
            ok = "O" if len(m) >= 6 and nb >= 3 else "X"
            L.append(f"| {c} | {len(m)} | {nb} | 6개↑·브랜드 3↑: {ok} | - | - | |")
        else:
            ok = "O" if len(m) >= 3 else "X"
            ok2 = "O" if len(a) >= 2 else "X"
            L.append(f"| {c} | {len(m)} | {nb} | 본상품 3개↑: {ok} | {len(a)} | {ok2} | 대안 3개: {'O' if len(a) >= 3 else 'X'} |")
    hits = []
    for r in allr:
        h = ru.find_used(r["상품명"], r["주소"], led)
        if h:
            hits.append((r["칸"], r["상품명"][:30], h[0].get("글제목", "")))
    L += ["", "## 3. 사용 제품 장부 대조", "",
          f"- 대조한 장부: pipeline/radar_used_products.json · 장부 줄 {led.get('줄수', len(led.get('products', [])))}개 (제품 {len(led.get('products', []))}개, 모델 {led.get('모델수', '?')}개)",
          f"- 대조한 후보 {len(allr)}개 중 장부와 겹친 모델: **{len(hits)}개**"]
    L += [f"  - {h}" for h in hits]
    seen = defaultdict(list)
    for r in allr:
        k = r["모델키"] if ru.strong_key(r["모델키"]) else "pid:" + r["상품id"]
        seen[k].append(r["칸"])
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    L += ["", "## 4. 칸끼리 같은 모델", "", f"- 같은 모델이 둘 이상의 칸(또는 한 칸에 두 번)에 있는 경우: **{len(dup)}개**"]
    L += [f"  - {k}: {v}" for k, v in dup.items()]
    L += ["", "## 5. B칸 검색량 (본상품 후보 모델명 검색어, PC+모바일 월간)", "",
          "| 칸 | 최고 검색량 | 30 이상 개수 | 판정 |", "|---|---|---|---|"]
    for c, v in cells.items():
        if v["틀"] != "B":
            continue
        t = [r["월간 검색량"] for r in v["후보"]]
        L.append(f"| {c} {v['제품군']} | {max(t) if t else 0:,} | {sum(1 for x in t if x >= VOL_MIN)}/{len(t)} | "
                 f"{'검색량 부족' if not t or max(t) < VOL_MIN else 'O'} |")
    bad = []
    for r in allr:
        g = cat_group_1123(r["전체 카테고리"])
        if g == "고속충전기" and r["칸"] == "B3":
            g = ""
        if g:
            bad.append((r["칸"], r["상품명"][:30], g))
    unk = [r["칸"] + ":" + r["상품id"] for r in allr if not r["전체 카테고리"]]
    L += ["", "## 6. 11/23 주 제품군 15개로 분류되는 상품", "",
          f"- 카테고리로 분류된 상품: **{len(bad)}개**" + ("" if not bad else " " + str(bad)),
          "- B3(무선충전 패드)는 네이버 카테고리가 그냥 '충전기'라 고속충전기와 카테고리로 못 가려서, 이름이 무선충전 패드·무선충전기인 것만 넣음.",
          f"- 카테고리를 못 읽어 이름으로만 판단한 상품: {len(unk)}개" + (f" ({', '.join(unk)})" if unk else "")]
    ads = int((OUT / "ads_calls.txt").read_text()) if (OUT / "ads_calls.txt").exists() else 0
    L += ["", "## 사용량", "",
          f"- 네이버 쇼핑 검색 API: 3회(시험 호출, 404 — 종료된 API라 못 씀) · 검색광고 키워드 도구: {ads}회 · Claude 하위 호출(에이전트): 0회",
          "- 쇼핑커넥트(에고라이트, 읽기만): 검색 목록 41개 검색어 + 상품 페이지 카테고리 확인",
          "- 앱 데이터·대기열·사용 제품 장부·링크 발급·글쓰기·발행: 건드리지 않음"]
    (OUT / "check.md").write_text("\n".join(L), encoding="utf-8")



def collect(extra: list[str] | None = None) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    qs = all_queries() + [q for q in (extra or []) if q not in all_queries()]
    st = pc.collect_in_batches(qs, lambda k: k, RAW, pc.BATCH_KW,
                               lambda part, tmp: pc._js_naver(part, tmp, TOPN), "naver", "쇼핑커넥트")
    print("상태:", st)
    return {"ok": 0, "lost": 3, "incomplete": 4}[st]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
    try:
        if cmd == "collect":
            rc = collect(sys.argv[2:])
        elif cmd == "select":      # 선별 + 카테고리 확인(읽기만)
            select_loop()
            catdump()
            rc = 0
        elif cmd == "final":
            final()
            rc = 0
        elif cmd == "catdump":
            catdump()
            rc = 0
        else:
            rc = 1
    finally:
        try:
            pc._finish_space()
        except Exception:  # noqa: BLE001
            pass
    sys.exit(rc)
