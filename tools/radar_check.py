# -*- coding: utf-8 -*-
"""Desk Picker 원고 검사 (tools/radar_rules.md 규칙 기준).
사용: python tools/radar_check.py <글 폴더> [<글 폴더> ...] [--run <실행폴더 이름>] [--frame A|B] [--keyword 키워드] [--out <저장 폴더>]
- 글 폴더 = post.txt·facts.json·title.txt·link.txt 가 들어 있는 폴더 하나. 여러 개를 주면 소제목 겹침도 같이 본다.
- 틀(A/B)은 facts.json 의 post 값, 키워드는 facts.json 의 keyword 값을 쓴다(--frame, --keyword 로 바꿀 수 있음).
- '출처 없는 숫자' 검사에서 [사진: n] 표시와 상품명 안 숫자(approved.json·links.json 의 상품명과 일치하는 부분)는 뺀다.
  상품명 목록은 --run 의 실행폴더(pipeline/candidates_run/<실행>/)에서 읽고, 안 주면 facts.json 출처에 적힌 실행 폴더를 찾는다.
- 결과는 각 글 폴더의 check_result.json, check_result.txt (또는 --out 폴더 아래 <글 폴더 이름>/) 로 저장한다. 파일만 읽는다(앱 데이터·운영 파일 안 건드림).
- 사진·표지 파일이 폴더에 없으면 그 항목은 '파일 없음(미확인)'으로 두고 실패로 세지 않는다."""
import argparse, json, os, re, sys
from PIL import Image

NOTICE = "이 포스팅은 네이버 쇼핑커넥트 활동의 일환으로, 판매 시 수수료를 제공받습니다."
BANNED = ["최고", "무조건", "100%", "완벽", "강력 추천", "1위", "써보니", "써 보니", "직접 사용", "내돈내산", "써봤", "직접 써 보고", "직접 써보고", "바탕으로 정리했어요"]
BANNED_RX = [r"(사용|써본|솔직|직접|체험)\s?후기"]
RANGES = {"A": (1800, 2500, 5, 7), "B": (1300, 1800, 4, 6), "C": (1300, 1800, 4, 6)}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTO_MARK = re.compile(r"^\[사진: ?\d+\]$", re.M)
PLACEHOLDER = re.compile(r"^\[(사진|상품카드|내부링크): [^\n]*\]$", re.M)
NUM = re.compile(r"\d+(?:[.,]\d+)*")


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


def main():
    ap = argparse.ArgumentParser(description="Desk Picker 원고 검사")
    ap.add_argument("folders", nargs="+", help="글 폴더 (여러 개 가능)")
    ap.add_argument("--run", default="", help="상품명 목록을 읽을 실행 폴더 이름(없으면 facts.json 출처에서 찾음)")
    ap.add_argument("--frame", default="", help="틀 A 또는 B (기본: facts.json 의 post)")
    ap.add_argument("--keyword", default="", help="키워드 (기본: facts.json 의 keyword)")
    ap.add_argument("--out", default="", help="결과를 저장할 폴더(기본: 각 글 폴더)")
    a = ap.parse_args()
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
        if len(results) > 1:
            txt.append(f"소제목 겹침 {dup} ok={not dup}")
        open(os.path.join(d, "check_result.txt"), "w", encoding="utf-8").write("\n".join(txt))
        print(line_of(os.path.basename(f), r))
    if len(results) > 1:
        print(f"소제목 겹침 {dup} ok={not dup}")


if __name__ == "__main__":
    main()
