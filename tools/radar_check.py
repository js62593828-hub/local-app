# -*- coding: utf-8 -*-
"""Desk Picker 원고 검사 (tools/radar_rules.md 규칙 기준). 사용: python tools/radar_check.py <시범 폴더>
결과는 <시범 폴더>/check_result.json, check_result.txt 로 저장한다. 파일만 읽는다(앱 데이터·운영 파일 안 건드림)."""
import json, os, re, sys
from PIL import Image

NOTICE = "이 포스팅은 네이버 쇼핑커넥트 활동의 일환으로, 판매 시 수수료를 제공받습니다."
BANNED = ["최고", "무조건", "100%", "완벽", "강력 추천", "1위", "써보니", "써 보니", "직접 사용", "내돈내산", "써봤", "직접 써 보고", "직접 써보고", "바탕으로 정리했어요"]
BANNED_RX = [r"(사용|써본|솔직|직접|체험)\s?후기"]
RANGES = {"A": (1800, 2500, 5, 7), "B": (1300, 1800, 4, 6), "C": (1300, 1800, 4, 6)}
KEYWORDS = {"A": "발난로", "B": "리웨이 355", "C": "브라운 BC02XW"}
PLACEHOLDER = re.compile(r"^\[(이미지|상품카드|표|링크)[^\n]*\]$", re.M)
NUM = re.compile(r"\d+(?:[.,]\d+)*")


def tokens(text):
    return {t.replace(",", "") for t in NUM.findall(text)}


def check_post(folder, key):
    res = {}
    post = open(os.path.join(folder, "post.txt"), encoding="utf-8").read()
    facts = json.load(open(os.path.join(folder, "facts.json"), encoding="utf-8"))
    lo, hi, hmin, hmax = RANGES[key]
    # 1. 제목
    tl = [l.split(":", 1)[1].strip() for l in open(os.path.join(folder, "title.txt"), encoding="utf-8").read().splitlines() if ":" in l]
    res["title_len"] = [len(t) for t in tl]
    res["title_ok"] = all(len(t) <= 40 for t in tl) and len(tl) == 3 and KEYWORDS[key] in tl[0]
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
    # 4. 숫자 출처
    allowed = set()
    for f in facts["facts"]:
        allowed |= tokens(f["text"] + " " + f.get("note", ""))
    missing = sorted(tokens(post) - allowed)
    res["numbers_missing"] = missing
    res["numbers_ok"] = not missing
    # 5. 대가성 문구 맨 위
    first = next(l for l in post.splitlines() if l.strip())
    res["notice_ok"] = first.strip() == NOTICE
    # 6. 이미지
    imgs = re.findall(r"\[이미지: ([^\]]+)\]", post)
    sizes = {}
    for im in imgs:
        p = os.path.join(folder, im)
        sizes[im] = list(Image.open(p).size) if os.path.exists(p) else None
    res["image_sizes"] = sizes
    res["images_ok"] = bool(sizes) and all(v == [1080, 1080] for v in sizes.values())
    # 7. 표지 Desk Picker 표기 (메타 + 오른쪽 아래 영역에 글씨 픽셀이 실제로 있는지)
    meta = json.load(open(os.path.join(folder, "cover_meta.json"), encoding="utf-8"))
    x0, y0, x1, y1 = meta["brand_box"]
    cov = Image.open(os.path.join(folder, "cover.png")).convert("L")
    reg = cov.crop((x0, y0, x1, y1))
    lum = list(reg.getdata())
    contrast = max(lum) - min(lum)
    res["cover_brand"] = {"text": meta.get("brand_text"), "box": meta["brand_box"], "contrast": contrast}
    res["cover_ok"] = meta.get("brand_text") == "Desk Picker" and x0 > 540 and y0 > 940 and contrast >= 40
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


def main(root):
    out = {}
    folders = {"A": "A_발난로_고르는법", "B": "B_리웨이355", "C": "C_브라운BC02XW"}
    for k, f in folders.items():
        out[k] = check_post(os.path.join(root, f), k)
    allh = [h for k in out for h in out[k]["headings"]]
    dup = sorted({h for h in allh if allh.count(h) > 1})
    out["headings_overlap"] = dup
    out["headings_overlap_ok"] = not dup
    json.dump(out, open(os.path.join(root, "check_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lines = []
    for k in "ABC":
        r = out[k]
        lines.append(f"[{k}] 제목 {r['title_len']} ok={r['title_ok']} | 글자(공백제외/포함) {r['chars_no_space']}/{r['chars_with_space']} ok={r['length_ok']} | 금지표현 {r['banned_hits']} ok={r['banned_ok']} | 숫자 누락 {r['numbers_missing']} ok={r['numbers_ok']} | 대가성 ok={r['notice_ok']} | 이미지 ok={r['images_ok']} | 표지 ok={r['cover_ok']} | 소제목 {len(r['headings'])}개 ok={r['headings_ok']} | 가격표기 ok={r['price_ok']} | 상품카드 {r['cards']}개 ok={r['cards_ok']} | 링크 {r['links']}개")
    lines.append(f"소제목 겹침 {out['headings_overlap']} ok={out['headings_overlap_ok']}")
    open(os.path.join(root, "check_result.txt"), "w", encoding="utf-8").write("\n".join(lines))
    print("saved")


if __name__ == "__main__":
    main(sys.argv[1])
