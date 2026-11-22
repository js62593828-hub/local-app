# -*- coding: utf-8 -*-
"""Desk Picker 원고 검사 (tools/radar_rules.md 규칙 기준).
사용: python tools/radar_check.py <글 폴더> [<글 폴더> ...] [--run <실행폴더 이름>] [--frame A|B] [--keyword 키워드] [--out <저장 폴더>]
- 글 폴더 = post.txt·facts.json·title.txt·link.txt 가 들어 있는 폴더 하나. 여러 개를 주면 소제목 겹침도 같이 본다.
- 틀(A/B)은 facts.json 의 post 값, 키워드는 facts.json 의 keyword 값을 쓴다(--frame, --keyword 로 바꿀 수 있음).
- '출처 없는 숫자' 검사에서 [사진: n] 표시와 상품명 안 숫자(approved.json·links.json 의 상품명과 일치하는 부분)는 뺀다.
  상품명 목록은 --run 의 실행폴더(pipeline/candidates_run/<실행>/)에서 읽고, 안 주면 facts.json 출처에 적힌 실행 폴더를 찾는다.
- 결과는 각 글 폴더의 check_result.json, check_result.txt (또는 --out 폴더 아래 <글 폴더 이름>/) 로 저장한다. 파일만 읽는다(앱 데이터·운영 파일 안 건드림).
- 피할 표현(tools/radar_banned.json): 글 폴더 검사에 함께 돌고, 0개여야 하는 말이 걸리면 종료 코드 1, 횟수 초과는 경고만 한다.
  글 폴더 없이 원고 파일만 검사: python -X utf8 tools/radar_check.py --text 원고.txt [--memo 메모.txt] [--title "제목"] [--save 결과.txt]
- 원고 파일(본문 + "[메모]" 줄 + 메모) 하나 또는 그 파일들이 든 폴더: python tools/radar_check.py 원고.md | 폴더 [--run 실행폴더이름]
  (점검 항목 a~j 별 위반 수를 보여 준다. 위반이 1개라도 있으면 종료 코드 1.)
- 사진·표지 파일이 폴더에 없으면 그 항목은 '파일 없음(미확인)'으로 두고 실패로 세지 않는다."""
import argparse, json, os, re, sys
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radar_used_products as ru  # 사용 제품 장부(원고지침 v3.4 '제품은 한 번만')

NOTICE = "이 포스팅은 네이버 쇼핑커넥트 활동의 일환으로, 판매 시 수수료를 제공받습니다."
BANNED = ["최고", "무조건", "100%", "완벽", "강력 추천", "1위", "써보니", "써 보니", "직접 사용", "내돈내산", "써봤", "직접 써 보고", "직접 써보고", "바탕으로 정리했어요"]
BANNED_RX = [r"(사용|써본|솔직|직접|체험)\s?후기"]
RANGES = {"A": (1800, 2500, 5, 7), "B": (1300, 1800, 4, 6), "C": (1300, 1800, 4, 6)}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTO_MARK = re.compile(r"^\[사진: ?\d+\]$", re.M)
PLACEHOLDER = re.compile(r"^\[(사진|상품카드|내부링크): [^\n]*\]$", re.M)
NUM = re.compile(r"\d+(?:[.,]\d+)*")


# ── 피할 표현 규칙 (tools/radar_banned.json 한 곳에서 읽음 — radar_screen.py ③ 화면 경고도 같은 함수를 쓴다) ──
BANNED_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "radar_banned.json")


def load_avoid_rules():
    return json.load(open(BANNED_JSON, encoding="utf-8"))


def _sentence_head(line, pos, n=30):
    """줄 안에서 pos 가 들어 있는 문장의 앞 n자."""
    cut = [m.end() for m in re.finditer(r"(?<=[.?!])\s+", line[:pos])]
    return line[(cut[-1] if cut else 0):].strip()[:n]


def _word_at(line, m):
    """걸린 말을 어절 끝까지 늘려 보여 준다(예: 갈 + 려요)."""
    e = m.end()
    while e < len(line) and "가" <= line[e] <= "힣" and e - m.start() < 8:
        e += 1
    return line[m.start():e]


def check_avoid(sections, titles=None, rules=None):
    """sections = [(이름, 글자)] (예: ("본문", 원고), ("메모", 메모)). 글은 바꾸지 않는다.
    돌려주는 값: {"fails": [...], "warns": [...], "limit_warns": [...], "title_fails": [...]}
    각 hit = {rule, label, line, word, snippet, hint}. 줄 번호는 이름별로 1부터. **·== 강조 표시는 지우고 찾는다."""
    rules = rules or load_avoid_rules()
    out = {"fails": [], "warns": [], "limit_warns": [], "title_fails": []}
    counts = {r["name"]: [] for r in rules["limits"]}
    for label, text in sections:
        for no, raw_ln in enumerate((text or "").replace("\r\n", "\n").split("\n"), 1):
            ln = raw_ln.replace("**", "").replace("==", "")
            for r in rules["zero"]:
                for m in re.finditer(r["regex"], ln):
                    hit = {"rule": r["name"], "label": label, "line": no, "word": _word_at(ln, m),
                           "snippet": _sentence_head(ln, m.start()), "hint": r.get("hint", "")}
                    out["fails" if r["severity"] == "fail" else "warns"].append(hit)
            for r in rules["limits"]:
                for m in re.finditer(r["regex"], ln):
                    counts[r["name"]].append({"rule": r["name"], "label": label, "line": no, "word": m.group(0),
                                              "snippet": _sentence_head(ln, m.start()), "hint": ""})
    for r in rules["limits"]:
        hs = counts[r["name"]]
        if len(hs) > r["max"]:
            out["limit_warns"].append({"rule": r["name"], "count": len(hs), "max": r["max"], "hits": hs})
    for t in titles or []:
        for w in rules["title_banned"]:
            if w in t:
                out["title_fails"].append({"rule": "제목 금지 단어", "label": "제목", "line": 1, "word": w, "snippet": t[:30], "hint": ""})
    return out


def _hit_text(h):
    return f"{h['label']} {h['line']}줄 · {h['word']} · {h['snippet']}"


def avoid_lines(res):
    """check_avoid 결과 → 사람이 읽는 줄 목록."""
    L = []
    for h in res["fails"] + res["title_fails"]:
        L.append("실패 · " + _hit_text(h))
    for h in res["warns"]:
        L.append("경고(사람이 판단) · " + _hit_text(h))
    for lw in res["limit_warns"]:
        L.append(f"경고 · '{lw['rule']}' {lw['count']}번 (한 글 {lw['max']}번까지)")
        for h in lw["hits"]:
            L.append("    " + _hit_text(h))
    return L


def tokens(text):
    return {t.replace(",", "") for t in NUM.findall(text)}


def product_names(run_dir):
    """approved.json·links.json 의 상품명 목록(긴 이름부터)."""
    names = set()
    if run_dir:
        for fn in ("approved.json", "links.json"):
            fp = os.path.join(run_dir, fn)
            if not os.path.exists(fp):
                continue
            d = json.load(open(fp, encoding="utf-8"))
            if fn == "approved.json":
                for kw in d.get("keywords", []):
                    for p in kw.get("picks", []):
                        names.add(p.get("name", ""))
            else:
                for it in d.get("items", []):
                    names.add(it.get("name", ""))
    return sorted((n for n in names if n), key=len, reverse=True)


def find_run_dir(folder, facts, run_arg):
    if run_arg:
        return run_arg if os.path.isdir(run_arg) else os.path.join(ROOT, "pipeline", "candidates_run", run_arg)
    for f in facts.get("facts", []):
        m = re.search(r"candidates_run/([^/\s]+)/", f.get("source", ""))
        if m and os.path.isdir(os.path.join(ROOT, "pipeline", "candidates_run", m.group(1))):
            return os.path.join(ROOT, "pipeline", "candidates_run", m.group(1))
    return ""


def check_post(folder, key, keyword=None, run_arg=""):
    res = {}
    post = open(os.path.join(folder, "post.txt"), encoding="utf-8").read()
    facts = json.load(open(os.path.join(folder, "facts.json"), encoding="utf-8"))
    lo, hi, hmin, hmax = RANGES[key]
    keyword = keyword or facts.get("keyword")
    # 1. 제목 (키워드를 알면 첫 제목에 글자 그대로 들어 있는지도 본다)
    tl = [l.split(":", 1)[1].strip() for l in open(os.path.join(folder, "title.txt"), encoding="utf-8").read().splitlines() if ":" in l]
    res["title_len"] = [len(t) for t in tl]
    res["title_ok"] = all(len(t) <= 40 for t in tl) and len(tl) == 3 and (keyword is None or (bool(tl) and keyword in tl[0]))
    res["keyword"] = keyword
    res["keyword_warn"] = "" if keyword else "키워드 없음: facts.json 에도 --keyword 에도 없어 제목 속 키워드 검사를 하지 못함"
    res["titles"] = tl
    # 2. 글자 수 (자리 표시 줄 제외)
    body = PLACEHOLDER.sub("", post)
    with_sp, no_sp = len(body), len(re.sub(r"\s", "", body))
    res["chars_with_space"], res["chars_no_space"] = with_sp, no_sp
    res["length_ok"] = lo <= no_sp <= hi and lo <= with_sp <= hi
    # 3. 금지 표현
    hits = [w for w in BANNED if w in post] + [m.group(0) for rx in BANNED_RX for m in re.finditer(rx, post)]
    res["banned_hits"] = hits
    res["banned_ok"] = not hits
    # 4. 숫자 출처: [사진: n] 표시와 상품명 안 숫자는 뺀다
    allowed = set()
    for f in facts["facts"]:
        allowed |= tokens(f["text"] + " " + f.get("note", ""))
    scan = PHOTO_MARK.sub("", post)
    names = product_names(find_run_dir(folder, facts, run_arg))
    for n in names:
        scan = scan.replace(n, "")
    missing = sorted(tokens(scan) - allowed)
    res["numbers_missing"] = missing
    res["numbers_ok"] = not missing
    res["numbers_note"] = "[사진: n] 표시 제외, 상품명 %d개 일치 부분 제외" % len(names)
    # 5. 대가성 문구 맨 위
    first = next(l for l in post.splitlines() if l.strip())
    res["notice_ok"] = first.strip() == NOTICE
    # 6. 이미지: [사진: 번호] 의 번호로 폴더 안 사진 파일(이름에 그 번호가 들어간 이미지)을 찾는다. 파일이 없으면 미확인(None)
    nums = re.findall(r"^\[사진: ?(\d+)\]$", post, re.M)
    files = [f for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg", ".jpeg")) and not f.lower().startswith("cover")]
    sizes = {}
    for n in nums:
        hit = next((f for f in sorted(files) if re.search(r"(?<!\d)0*%s(?!\d)" % n, f)), None)
        sizes["사진 " + n] = list(Image.open(os.path.join(folder, hit)).size) if hit else None
    res["image_sizes"] = sizes
    if not sizes or all(v is None for v in sizes.values()):
        res["images_ok"] = None  # 사진 파일이 아직 없음(미확인)
    else:
        res["images_ok"] = all(v == [1080, 1080] for v in sizes.values())
    # 7. 표지 Desk Picker 표기 (메타 + 오른쪽 아래 영역에 글씨 픽셀이 실제로 있는지). 표지 파일이 없으면 미확인(None)
    mp, cp = os.path.join(folder, "cover_meta.json"), os.path.join(folder, "cover.png")
    if os.path.exists(mp) and os.path.exists(cp):
        meta = json.load(open(mp, encoding="utf-8"))
        x0, y0, x1, y1 = meta["brand_box"]
        cov = Image.open(cp).convert("L")
        lum = list(cov.crop((x0, y0, x1, y1)).getdata())
        contrast = max(lum) - min(lum)
        res["cover_brand"] = {"text": meta.get("brand_text"), "box": meta["brand_box"], "contrast": contrast}
        res["cover_ok"] = meta.get("brand_text") == "Desk Picker" and x0 > 540 and y0 > 940 and contrast >= 40
    else:
        res["cover_brand"] = None
        res["cover_ok"] = None
    # 소제목
    heads = re.findall(r"^## (.+)$", post, re.M)
    res["headings"] = heads
    res["headings_ok"] = hmin <= len(heads) <= hmax
    # 가격 표기
    bad_price = []
    for line in post.splitlines():
        for m in re.finditer(r"약 [\d,]+원", line):
            after = line[m.end(): m.end() + 8]
            if " | " in line:
                continue
            if "(변동 가능)" not in after:
                bad_price.append(line.strip()[:40])
    res["price_ok"] = ("작성일 기준" in post) and not bad_price
    res["price_bad"] = bad_price
    # 상품카드·링크
    cards = re.findall(r"\[상품카드: [^\]]+\]", post)
    res["cards"] = len(cards)
    res["cards_ok"] = (2 <= len(cards) <= 3) if key == "A" else len(cards) == 1
    links = [l for l in open(os.path.join(folder, "link.txt"), encoding="utf-8").read().splitlines() if "https://naver.me/" in l]
    res["links"] = len(links)
    return res


def ok_text(v):
    return "미확인(파일 없음)" if v is None else str(v)


def line_of(name, r):
    warn = f"⚠ {r['keyword_warn']} | " if r.get("keyword_warn") else ""
    return (f"[{name}] {warn}제목 {r['title_len']} ok={r['title_ok']} | 글자(공백제외/포함) {r['chars_no_space']}/{r['chars_with_space']} ok={r['length_ok']} | "
            f"금지표현 {r['banned_hits']} ok={r['banned_ok']} | 숫자 누락 {r['numbers_missing']} ok={r['numbers_ok']} | 대가성 ok={r['notice_ok']} | "
            f"이미지 ok={ok_text(r['images_ok'])} | 표지 ok={ok_text(r['cover_ok'])} | 소제목 {len(r['headings'])}개 ok={r['headings_ok']} | "
            f"가격표기 ok={r['price_ok']} | 상품카드 {r['cards']}개 ok={r['cards_ok']} | 링크 {r['links']}개")


# ══ 원고 파일 검사 (본문 + "[메모]" 줄 + 메모 한 파일) — 점검 항목 a~j ══
MEMO_MARK = "[메모]"
TITLE_BANNED = ["후기", "내돈내산", "써보니", "사용기", "실사용", "직접 써본", "최고", "무조건", "100%", "완벽", "강력 추천", "1위"]
OLD_MARKS = ["[이미지:", "[표:", "[내부링크:", "함께 보면 좋은 글"]
ITEM_NAMES = {
    "avoid": "피할 표현(radar_banned.json)", "a": "a 리뷰·후기 개수 숫자", "b": "b 형광펜 2~3곳", "c": "c 소제목 다음 [사진]",
    "d": "d 상품카드 이름 = approved.json", "e": "e 사진 번호·장수", "f": "f 옛 표시·안내 문구", "g": "g 제목 길이·금지 단어",
    "h": "h 카테고리·태그", "i": "i 표지 글자 수", "j": "j 사진자리 = 본문 사진",
    "k": "k 표 칸 '없음'(→ 표시 없음)", "l": "l 못 찾았다·적혀 있지 않다 류 말투",
    "m": "m 제품 한 번만(장부·같은 폴더, v3.4)", "n": "n 틀 B 본상품 카드 2개(v3.4)", "o": "o 태그 띄어쓰기 금지(v3.4)",
}
NOT_FOUND_WORDS = ["찾지 못했", "적혀 있지 않", "말하기 어려워요"]


def split_draft(text):
    """원고 파일 글자 → (본문, 메모, 형식 오류 목록). '[메모]' 줄은 정확히 1개여야 한다."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    idx = [i for i, l in enumerate(lines) if l.strip() == MEMO_MARK]
    errs = []
    if len(idx) != 1:
        errs.append(f"'[메모]' 줄이 {len(idx)}개예요(정확히 1개여야 해요)")
        return "\n".join(lines), "", errs
    body, memo = "\n".join(lines[:idx[0]]), "\n".join(lines[idx[0] + 1:])
    first = body.split("\n")[:2] + ["", ""]
    if first[0].strip() != "[플랫폼:네이버]":
        errs.append("본문 1번째 줄이 '[플랫폼:네이버]' 가 아니에요")
    if not re.match(r"제목\s*:\s*\S", first[1].strip()):
        errs.append("본문 2번째 줄이 '제목: …' 이 아니에요")
    return body, memo, errs


def draft_title(body):
    ls = body.split("\n")
    m = re.match(r"제목\s*:\s*(.+)", ls[1].strip()) if len(ls) > 1 else None
    return m.group(1).strip() if m else ""


def _kwnorm(t):
    return re.sub(r"\s+", "", t or "").lower()


RUN_IN_TEXT = re.compile(r"candidates_run[/\\]([^/\\\s|]+)")


def run_dirs_from_text(text):
    """원고(메모 [사진자리])에 적힌 이미지 경로 'candidates_run/<실행 폴더>/...' 에서 실행 폴더 이름들을 찾는다(나온 순서, 중복 없음)."""
    out = []
    for m in RUN_IN_TEXT.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def radar_product_scope(run_arg="", text=""):
    """상품카드 이름 비교 범위(원고지침 v2.6). 돌려주는 값: (상품명 집합 또는 None, 범위 설명 글).
    - 기준: 그 원고에 쓰인 실행 폴더 approved.json 안의 모든 확정 상품명(키워드 1개로 좁히지 않는다).
    - 실행 폴더는 run_arg(폴더 이름 또는 경로)가 있으면 그것, 없으면 text 의 이미지 경로에서 찾는다.
    - None 은 실행 폴더를 모르거나 approved.json 이 없다는 뜻(= 카드 검사 못 함)."""
    base = os.path.join(ROOT, "pipeline", "candidates_run")
    if run_arg:
        dirs = [run_arg if os.path.isdir(run_arg) else os.path.join(base, run_arg)]
    else:
        dirs = [os.path.join(base, n) for n in run_dirs_from_text(text)]
    if not dirs:
        return None, "실행 폴더를 알 수 없음(--run 도 없고 [사진자리] 이미지 경로에도 없음)"
    names, used, missing = set(), [], []
    for d in dirs:
        label = os.path.basename(d.rstrip("/\\"))
        fp = os.path.join(d, "approved.json")
        if not os.path.exists(fp):
            missing.append(label)
            continue
        kws = json.load(open(fp, encoding="utf-8")).get("keywords", [])
        names |= {pk["name"] for k in kws for pk in k.get("picks", []) if pk.get("name")}
        used.append(label)
    if missing or not used:
        return None, "approved.json 없음: " + ", ".join(missing)
    return names, f"범위: 실행 폴더 {', '.join(used)} 의 확정 상품 전체 {len(names)}개"


def radar_product_names(run_arg="", text=""):
    """(옛 이름 유지) 상품명 집합만. 알 수 없으면 None."""
    return radar_product_scope(run_arg, text)[0]


def _body_chars(body):
    t = "\n".join(body.split("\n")[2:])
    t = PLACEHOLDER.sub("", t)
    t = re.sub(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*$", "", t, flags=re.M)
    t = re.sub(r"^#{1,6}\s", "", t, flags=re.M).replace("**", "").replace("==", "").replace("|", "")
    return len(re.sub(r"\s+", "", t))


def _memo_block(memo, name):
    """'[이름]' 줄 다음부터 빈 줄 전(또는 다음 '[…]' 블록 전)까지의 줄 목록. 블록이 없으면 None."""
    ls = memo.split("\n")
    for i, l in enumerate(ls):
        if l.strip() == f"[{name}]":
            out = []
            for x in ls[i + 1:]:
                if not x.strip() or re.match(r"^\[[^\]]+\]$", x.strip()):
                    break
                out.append(x.strip())
            return out
    return None


def _queue_frame(title):
    """대기열(content_queue_radar_naver.json)에서 제목이 같은 글의 틀(A/B). 못 찾으면 ''. 읽기만 한다."""
    try:
        q = json.load(open(os.path.join(ROOT, "content_queue_radar_naver.json"), encoding="utf-8"))
    except Exception:
        return ""
    t = re.sub(r"\s+", " ", title or "").strip()
    for x in q:
        pl = x.get("plan") or {}
        if t and t in {re.sub(r"\s+", " ", str(v)).strip() for v in (pl.get("final_title"), pl.get("title"))} and x.get("planned_type") in ("A", "B"):
            return x["planned_type"]
    return ""


def frame_of(title, frame_arg="", fname=""):
    """틀 A/B: --frame 이 있으면 그것, 없으면 파일 이름의 _A_/_B_, 그것도 없으면 대기열에서 같은 제목을 찾는다. 모르면 ''."""
    f = (frame_arg or "").strip().upper()
    if f in ("A", "B"):
        return f
    m = re.search(r"_([AB])_", os.path.basename(fname or ""))
    return m.group(1) if m else _queue_frame(title)


def draft_products(text):
    """원고 한 편의 제품들: [(상품명, 소제목, 모델키 목록)]. 상품카드 기준(같은 카드 두 번은 한 제품)."""
    out = []
    for p in ru.parse_article(text, "A")["products"]:
        out.append((p["card"], p["heading"], sorted({ru.model_key(p["card"], p["heading"]), ru.model_key(p["card"])})))
    return out


def ledger_conflicts(title, products, led=None):
    """장부에서 '다른 글'(제목이 다른 글)에 이미 쓴 제품을 찾는다. 반환: 위반 글 목록."""
    led = led if led is not None else ru.load_ledger()
    same = re.sub(r"\s+", " ", title or "").strip()
    out = []
    for card, head, keys in products:
        for r in led.get("products", []):
            if re.sub(r"\s+", " ", r.get("글제목", "")).strip() == same:
                continue  # 이 글 자신
            if r.get("상품명") == card or set(keys) & ({r.get("모델키")} | set(r.get("모델키목록") or [])):
                out.append(f"제품 중복 · {head or card[:20]} ← 장부 글 '{r.get('글제목', '')[:28]}'({r.get('날짜', '')}, {r.get('역할', '')})")
                break
    return out


def b_card_problems(plain):
    """틀 B 본상품 상품카드가 정확히 2개인지: ① 기본 정보 ### 구간 안, ② 마지막 ## 구간 맨 끝 줄. 문제 목록을 돌려준다."""
    lines = plain.split("\n")
    cards, in_h3 = [], False
    for i, l in enumerate(lines):
        if l.startswith("### "):
            in_h3 = True
        elif l.startswith("## "):
            in_h3 = False
        m = re.match(r"^\[상품카드: (.+)\]$", l.strip())
        if m:
            cards.append((i, m.group(1), in_h3))
    main = next((c for c in cards if c[2]), None)
    if not main:
        return ["① 기본 정보 ### 구간 안에 본상품 상품카드가 없어요"]
    out = []
    n = sum(1 for c in cards if c[1] == main[1])
    if n != 2:
        out.append(f"본상품 상품카드 {n}개(정확히 2개여야 해요) · {main[1][:30]}")
    last = max((i for i, l in enumerate(lines) if l.strip()), default=-1)
    lc = next((c for c in cards if c[0] == last), None)
    if not lc or lc[1] != main[1] or lc[2]:
        out.append("② 글 마지막 ## 구간 맨 끝 줄이 본상품 상품카드가 아니에요")
    return out


def check_draft(text, run_arg="", frame="", fname=""):
    """원고 파일 하나 검사. 돌려주는 값: {"title","format_errors","items":{키:{"viol":[...],"warn":[...]}},"violations","warnings"}"""
    body, memo, ferr = split_draft(text)
    title = draft_title(body)
    it = {k: {"viol": [], "warn": []} for k in ITEM_NAMES}
    res = {"title": title, "format_errors": ferr, "items": it}
    if sum(1 for l in text.replace("\r\n", "\n").split("\n") if l.strip() == MEMO_MARK) != 1:
        res["violations"], res["warnings"] = len(ferr), 0
        return res
    blines = body.split("\n")
    plain = "\n".join(blines[2:])  # 제목 줄 뺀 본문
    # 기존: 금지어·횟수 상한 (제목 금지 단어는 g 에서 따로 센다)
    av = check_avoid([("본문", body), ("메모", memo)], [])
    for h in av["fails"]:
        it["avoid"]["viol"].append(_hit_text(h))
    for h in av["warns"]:
        it["avoid"]["warn"].append(_hit_text(h))
    for lw in av["limit_warns"]:
        it["avoid"]["warn"].append(f"'{lw['rule']}' {lw['count']}번 (한 글 {lw['max']}번까지)")
    # a. 리뷰·후기 개수 숫자
    for no, ln in enumerate(plain.split("\n"), 3):
        if re.search(r"(리뷰|후기|구매평)[^\n|]{0,12}?\d[\d,]*\s*(개|건)", ln) or re.search(r"\d\.\d+\s*\(\s*[\d,]+\s*(개|건)?\s*\)", ln):
            it["a"]["viol"].append(f"{no}줄 · {ln.strip()[:40]}")
    rows = [l for l in plain.split("\n") if l.strip().startswith("|")]
    for hi, h in enumerate(rows):
        cells = [c.strip() for c in h.strip().strip("|").split("|")]
        col = next((i for i, c in enumerate(cells) if re.search(r"리뷰\s*수|평점\s*\(\s*리뷰", c)), None)
        if col is None:
            continue
        for r in rows[hi + 1:]:
            if re.fullmatch(r"\|[\s:|-]+\|", r.strip()):
                continue
            cs = [c.strip() for c in r.strip().strip("|").split("|")]
            if len(cs) <= col:
                break
            if re.search(r"\d", cs[col]):
                it["a"]["viol"].append(f"표 '{cells[col]}' 칸에 숫자 · {cs[col][:20]}")
        break
    # b. 형광펜
    hl = re.findall(r"==[^=\n]+==", plain)
    if not 2 <= len(hl) <= 3:
        it["b"]["viol"].append(f"형광펜 {len(hl)}곳(2~3곳이어야 해요)")
    # c. 소제목 다음 사진
    for i, l in enumerate(blines):
        if re.match(r"^#{4,}\s", l):
            it["c"]["viol"].append(f"{i+1}줄 · '####' 이상 소제목 · {l.strip()[:30]}")
        elif l.startswith("### "):
            nxt = next((x.strip() for x in blines[i + 1:] if x.strip()), "")
            if not re.fullmatch(r"\[사진: ?\d+\]", nxt):
                it["c"]["viol"].append(f"{i+1}줄 · '{l.strip()[:30]}' 다음 줄이 [사진: n] 이 아니에요")
    # d. 상품카드 이름
    cards = re.findall(r"^\[상품카드: ([^\]\n]+)\]$", plain, re.M)
    if cards:
        names, scope = radar_product_scope(run_arg, memo)
        res["d_scope"] = scope
        if names is None:
            it["d"]["warn"].append("⚠ 카드 검사 못 함 · " + scope + " → --run <실행 폴더> 를 주세요")
        else:
            for c in cards:
                if c not in names:
                    it["d"]["viol"].append(f"approved.json 상품명과 달라요 · {c[:40]}")
    # e. 사진 번호
    nums = [int(n) for n in re.findall(r"^\[사진: ?(\d+)\]$", plain, re.M)]
    if nums != list(range(1, len(nums) + 1)):
        it["e"]["viol"].append(f"사진 번호가 1부터 빠짐없이 이어지지 않아요 · {nums}")
    want = min(8, max(5, int(_body_chars(body) / 400 + 0.5)))
    if len(nums) != want:
        it["e"]["warn"].append(f"본문 사진 {len(nums)}장 · 글자 수 기준 {want}장(글자 {_body_chars(body)}자)")
    # f. 옛 표시
    for m in OLD_MARKS:
        if m in body or m in memo:
            it["f"]["viol"].append(f"옛 표시 '{m}'")
    if NOTICE in body or "쇼핑커넥트" in body:
        it["f"]["viol"].append("쇼핑커넥트 안내 문장")
    # g. 제목
    if len(title) > 40:
        it["g"]["viol"].append(f"제목 {len(title)}자(40자 이내)")
    for w in TITLE_BANNED:
        if w in title:
            it["g"]["viol"].append(f"제목 금지 단어 '{w}'")
    # h. 카테고리·태그
    cat = re.search(r"^\s*카테고리\s*:\s*(\S.*)$", memo, re.M)
    tag = re.search(r"^\s*태그\s*:\s*(\S.*)$", memo, re.M)
    if not cat:
        it["h"]["viol"].append("'카테고리:' 줄이 없어요")
    if not tag:
        it["h"]["viol"].append("'태그:' 줄이 없어요")
    else:
        v = tag.group(1)
        tags = [t for t in re.split(r"\s*,\s*", v) if t.strip("# ")] if "," in v else [t for t in re.split(r"[#\s]+", v) if t]
        if not 10 <= len(tags) <= 15:
            it["h"]["viol"].append(f"태그 {len(tags)}개(10~15개여야 해요)")
    # i. 표지
    cov = _memo_block(memo, "표지")
    if cov is None:
        it["i"]["viol"].append("[표지] 칸이 없어요")
    else:
        kv = {}
        for l in cov:
            if ":" in l:
                k, v = l.split(":", 1)
                kv[k.strip()] = v.strip()
        for key, lim in (("제목", 12), ("부제", 16)):
            if key not in kv:
                it["i"]["viol"].append(f"[표지] '{key}:' 가 없어요")
            elif len(kv[key]) > lim:
                it["i"]["viol"].append(f"[표지] {key} {len(kv[key])}자({lim}자 이내)")
    # j. 사진자리
    ps = _memo_block(memo, "사진자리")
    if ps is None:
        it["j"]["viol"].append("[사진자리] 칸이 없어요")
    else:
        slot = []
        heads = {re.sub(r"\s+", " ", h).strip() for h in re.findall(r"^#{2,3}\s+(.+)$", plain, re.M)}
        for l in ps:
            cs = [c.strip() for c in l.split("|")]
            if cs and re.fullmatch(r"\d+", cs[0]):
                slot.append(int(cs[0]))
                if len(cs) > 2 and cs[2] and re.sub(r"\s+", " ", cs[2]) not in heads:
                    it["j"]["warn"].append(f"사진 {cs[0]} 넣을 소제목 '{cs[2][:20]}' 가 본문 ##/### 와 달라요")
        if sorted(slot) != sorted(nums):
            it["j"]["viol"].append(f"[사진자리] 번호 {sorted(slot)} ≠ 본문 [사진: n] 번호 {sorted(nums)}")
    # j2. 표: 본문 마크다운 표 개수 = [사진자리] 종류 '표' 개수, 표 바로 아래(빈 줄 제외)는 [사진: n], 표 행 10개 초과는 경고
    pl = plain.split(chr(10))
    tbl = []  # (시작 줄번호, 데이터 행 수, 바로 아래 줄)
    i = 0
    while i < len(pl):
        if pl[i].strip().startswith("|"):
            j = i
            while j < len(pl) and pl[j].strip().startswith("|"):
                j += 1
            blk = [x.strip() for x in pl[i:j]]
            if len(blk) >= 2 and re.fullmatch(r"\|[\s:\-|]+\|?", blk[1]) and "-" in blk[1]:
                k = j
                while k < len(pl) and not pl[k].strip():
                    k += 1
                tbl.append((i + 3, len(blk) - 2, pl[k].strip() if k < len(pl) else ""))
            i = j
        else:
            i += 1
    if ps is not None:
        n_kind = sum(1 for l in ps if len([c.strip() for c in l.split("|")]) > 1
                     and re.fullmatch(r"\d+", l.split("|")[0].strip()) and l.split("|")[1].strip() == "표")
        if n_kind != len(tbl):
            it["j"]["viol"].append(f"본문 표 {len(tbl)}개 ≠ [사진자리] 종류 '표' {n_kind}개")
    for no, nrows, below in tbl:
        if not re.fullmatch(r"\[사진\s*:\s*\d+\s*\]", below):
            it["j"]["viol"].append(f"{no}줄 표 바로 아래 줄이 [사진: n] 이 아니에요 · '{below[:20]}'")
        if nrows > 10:
            it["j"]["warn"].append(f"{no}줄 표 행이 {nrows}개(10개 초과) — 두 표로 나누는 것을 권해요")
    # k. 표 칸 값이 정확히 "없음"
    for no, ln in enumerate(pl, 3):
        s = ln.strip()
        if s.startswith("|") and not re.fullmatch(r"\|[\s:\-|]+\|?", s):
            for c in s.strip("|").split("|"):
                if c.strip() == "없음":
                    it["k"]["viol"].append(f'{no}줄 · 표 칸이 "없음" — "없음" 대신 "표시 없음"으로')
    # l. 단정 못 하는 말투
    for no, ln in enumerate(pl, 3):
        for w in NOT_FOUND_WORDS:
            if w in ln:
                it["l"]["viol"].append(f"{no}줄 · '{w}' · {ln.strip()[:40]}")
    # m. 제품 한 번만(v3.4): 장부의 다른 글에 이미 나온 제품 (같은 폴더 다른 원고는 run_drafts 에서 더함)
    prods = draft_products(text)
    res["products"] = prods
    it["m"]["viol"] += ledger_conflicts(title, prods)
    # n. 틀 B 본상품 상품카드 2개(v3.4)
    fr = frame_of(title, frame, fname)
    res["frame"] = fr
    if fr == "B":
        it["n"]["viol"] += b_card_problems(plain)
    elif not fr:
        it["n"]["warn"].append("틀(A/B)을 몰라서 본상품 카드 2개 검사를 못 했어요 → --frame A|B 를 주세요")
    # o. 태그 띄어쓰기 금지(v3.4)
    if tag:
        v = tag.group(1)
        if "," in v:
            bad = [t.strip() for t in v.split(",") if re.search(r"\s", t.strip())]
        else:
            bad = [v.strip()] if re.search(r"\s", v.strip()) else []
        for t in bad:
            it["o"]["viol"].append(f"태그에 띄어쓰기 · {t[:40]}")
    res["violations"] = len(ferr) + sum(len(v["viol"]) for v in it.values())
    res["warnings"] = sum(len(v["warn"]) for v in it.values())
    return res


def draft_report(name, r):
    L = [f"■ {name} · 제목: {r['title'] or '(없음)'}"]
    for e in r["format_errors"]:
        L.append(f"  형식 오류 · {e}")
    for k, nm in ITEM_NAMES.items():
        v = r["items"][k]
        L.append(f"  {nm}: 위반 {len(v['viol'])} · 경고 {len(v['warn'])}")
        if k == "d" and r.get("d_scope"):
            L.append("      " + r["d_scope"])
        L += ["      위반 · " + x for x in v["viol"]] + ["      경고 · " + x for x in v["warn"]]
    L.append(f"  → 위반 {r['violations']}개 · 경고 {r['warnings']}개")
    return L


def run_drafts(paths, run_arg="", frame=""):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(".md"))
        else:
            files.append(p)
    tot = 0
    results = [(fp, check_draft(open(fp, encoding="utf-8").read(), run_arg, frame, fp)) for fp in files]
    # m(같은 폴더 다른 원고): 두 원고에 같은 제품이 있으면 양쪽에 위반으로 적는다
    for fp, r in results:
        for card, head, keys in r.get("products", []):
            for fp2, r2 in results:
                if fp2 != fp and any(c2 == card or set(k2) & set(keys) for c2, h2, k2 in r2.get("products", [])):
                    if any(v.startswith(f"제품 중복 · {head or card[:20]} ←") for v in r["items"]["m"]["viol"]):
                        break  # 장부에서 이미 잡힌 제품은 한 번만 센다
                    r["items"]["m"]["viol"].append(f"제품 중복 · {head or card[:20]} ← 같은 폴더 원고 {os.path.basename(fp2)}")
                    r["violations"] += 1
                    break
    for fp, r in results:
        print("\n".join(draft_report(os.path.basename(fp), r)))
        tot += r["violations"]
    print(f"\n결과: 파일 {len(files)}개 · 위반 합계 {tot}개 ({'실패' if tot else '통과'})")
    return 1 if tot else 0


def _is_draft_arg(p):
    """원고 파일(.md) 이거나, post.txt 없이 *.md 만 든 폴더면 원고 입력. post.txt 가 있는 글 폴더는 예전 방식."""
    if os.path.isfile(p):
        return True
    return os.path.isdir(p) and not os.path.exists(os.path.join(p, "post.txt")) and any(f.lower().endswith(".md") for f in os.listdir(p))


def main():
    ap = argparse.ArgumentParser(description="Desk Picker 원고 검사")
    ap.add_argument("folders", nargs="*", help="글 폴더 (여러 개 가능) 또는 원고 파일(.md)·원고 파일 폴더")
    ap.add_argument("--text", default="", help="원고 파일 하나만 피할 표현 검사(글 폴더 검사는 안 함)")
    ap.add_argument("--memo", default="", help="--text 와 같이: 메모 파일(메모도 검사)")
    ap.add_argument("--title", action="append", default=[], help="--text 와 같이: 제목(여러 번 가능)")
    ap.add_argument("--save", default="", help="--text 결과를 저장할 파일(UTF-8)")
    ap.add_argument("--run", default="", help="상품명 목록을 읽을 실행 폴더 이름 또는 경로(원고 검사: 없으면 [사진자리] 이미지 경로에서 찾고, 그래도 모르면 카드 검사 못 함 경고)")
    ap.add_argument("--frame", default="", help="틀 A 또는 B (기본: facts.json 의 post)")
    ap.add_argument("--keyword", default="", help="키워드 (기본: facts.json 의 keyword)")
    ap.add_argument("--out", default="", help="결과를 저장할 폴더(기본: 각 글 폴더)")
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):  # -X utf8 없이 실행해도 한글이 깨지지 않게
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    drafts = [f for f in a.folders if _is_draft_arg(f)]
    if drafts:
        a.folders = [f for f in a.folders if f not in drafts]
        code = run_drafts(drafts, a.run, a.frame)
        if not a.folders:
            sys.exit(code)
    if a.text:
        secs = [("본문", open(a.text, encoding="utf-8").read())]
        if a.memo:
            secs.append(("메모", open(a.memo, encoding="utf-8").read()))
        res = check_avoid(secs, a.title)
        lines = avoid_lines(res)
        bad = len(res["fails"]) + len(res["title_fails"])
        lines.append(f"결과: {'실패' if bad else '통과'} (0개여야 하는 말 {bad}곳, 사람이 판단할 곳 {len(res['warns'])}곳, 횟수 초과 {len(res['limit_warns'])}종류)")
        print("\n".join(lines))
        if a.save:
            open(a.save, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
        sys.exit(1 if bad else 0)
    if not a.folders:
        ap.error("글 폴더 또는 --text 가 필요해요")
    results = {}
    for f in a.folders:
        f = os.path.abspath(f)
        facts = json.load(open(os.path.join(f, "facts.json"), encoding="utf-8"))
        key = (a.frame or facts.get("post") or "B").upper()
        results[f] = check_post(f, key, a.keyword or None, a.run)
        results[f]["frame"] = key
    allh = [h for r in results.values() for h in r["headings"]]
    dup = sorted({h for h in allh if allh.count(h) > 1}) if len(results) > 1 else []
    for f, r in results.items():
        out = {"post": r, "headings_overlap": dup, "headings_overlap_ok": not dup, "folders_checked": [os.path.basename(x) for x in results]}
        d = os.path.join(a.out, os.path.basename(f)) if a.out else f
        os.makedirs(d, exist_ok=True)
        json.dump(out, open(os.path.join(d, "check_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        txt = [line_of(os.path.basename(f), r)]
        av = check_avoid([("본문", open(os.path.join(f, "post.txt"), encoding="utf-8").read())], r["titles"])
        r["avoid"] = av
        out["avoid"] = av
        txt += ["피할 표현: " + x for x in avoid_lines(av)] or ["피할 표현: 걸린 것 없음"]
        if len(results) > 1:
            txt.append(f"소제목 겹침 {dup} ok={not dup}")
        open(os.path.join(d, "check_result.txt"), "w", encoding="utf-8").write("\n".join(txt))
        print("\n".join(txt))
    if len(results) > 1:
        print(f"소제목 겹침 {dup} ok={not dup}")
    if any(r["avoid"]["fails"] or r["avoid"]["title_fails"] for r in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
