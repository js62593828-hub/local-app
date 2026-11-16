"""
N데스크피커 글 사진 만들기 (Pillow, 비용 0원 · 이미지 생성 AI 호출 없음)

만드는 것
  표지 1장(1080x1080)  : 시안 A 가 기본(B·C 는 옵션) — make_cover(), 메모 [표지] 읽기 parse_cover_memo()
  본문 사진            : 제품 사진 정리 product_photo(), 표 그림 table_image(),
                         카드 card_image(), 캡처 자리 capture_placeholder(),
                         AI 장면 사진 ai_prompt() (프롬프트 글만 만듦, 실제 호출 안 함)
  배치 계획            : body_photo_count(), validate_plan()

규칙
  - 글자는 원고·facts 에 있는 것만 넣는다(이 모듈은 받은 글자를 그리기만 함).
  - 사진은 비율 그대로만 줄인다(늘리기·잘라서 왜곡 금지).
  - 글꼴은 fonts/Pretendard (ExtraBold, Medium).
  - app.py · image_gen.py · shop_formatter.py 와 서로 가져다 쓰지 않는다(독립 모듈).
"""
import json
import os
import re

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SIZE = 1080
MARGIN = 72
JPG_QUALITY = 94
AI_COST_KRW = 9  # 장당 약 9원(예상)

# ── 색 (시안 A 기준) ─────────────────────────────────────────
BG_TOP = (0xF4, 0xF1, 0xEC)
BG_BOT = (0xEC, 0xE8, 0xE1)
IVORY = (0xFB, 0xF9, 0xF5)
INK = (0x22, 0x26, 0x2B)
WHITE = (255, 255, 255)
GOLD = (0xC9, 0xA3, 0x5B)
YELLOW = (0xF2, 0xB5, 0x44)
MUTED = (0x6B, 0x6F, 0x76)
LINE = (0xDD, 0xD7, 0xCC)
SHADOW = (70, 58, 40)

THEMES = {
    "A": {"band": IVORY, "text": INK, "accent": GOLD, "name": "아이보리 띠 · 금색"},
    "B": {"band": INK, "text": WHITE, "accent": GOLD, "name": "먹색 띠 · 금색"},
    "C": {"band": IVORY, "text": INK, "accent": YELLOW, "name": "아이보리 띠 · 노랑"},
}

COVER_TOP_RATIO = 0.64
COVER_TOP_H = round(SIZE * COVER_TOP_RATIO)  # 691
DEFAULT_VARIANT = "A"        # 기본 표지는 시안 A 하나(B·C 는 variant 로 고를 때만)
COVER_PHOTO_RATIO = 0.70     # 제품 사진 영역 가로 비율(표지 폭 대비)
COVER_CAT_PX = 32
COVER_TITLE_MAX, COVER_TITLE_MIN = 88, 56
COVER_TITLE_FILL = 0.85      # 제목 가로 폭 상한(띠 안쪽 너비 대비)
COVER_SUB_PX = 38
COVER_MAX_COLLECT = 3        # 모음형 표지 최대 사진 수

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_BOLD = [os.path.join(_FONT_DIR, "Pretendard-ExtraBold.otf"), "C:\\Windows\\Fonts\\malgunbd.ttf"]
_MEDIUM = [os.path.join(_FONT_DIR, "Pretendard-Medium.otf"), "C:\\Windows\\Fonts\\malgun.ttf"]
_font_cache = {}


def font(bold, size):
    key = (bool(bold), int(size))
    if key not in _font_cache:
        for p in (_BOLD if bold else _MEDIUM):
            if os.path.exists(p):
                _font_cache[key] = ImageFont.truetype(p, int(size))
                break
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


# ── 글자 도우미 ──────────────────────────────────────────────
def text_w(f, text, spacing=0):
    if not text:
        return 0
    return f.getlength(text) + spacing * (len(text) - 1)


def draw_text(d, x, y, text, f, fill, spacing=0, anchor="ls"):
    """baseline(y) 기준 글자 그리기. spacing(자간)이 있으면 글자마다 찍는다."""
    if not spacing:
        d.text((x, y), text, font=f, fill=fill, anchor=anchor)
        return
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill, anchor=anchor)
        x += f.getlength(ch) + spacing


def _pieces(text):
    """띄어쓰기 낱말을 다시 '(' 앞에서 나눈다. 반환: [(조각, 앞에 띄어쓰기 붙임 여부), ...]"""
    out = []
    for word in text.split(" "):
        parts = [p for p in re.split(r"(?=\()", word) if p != ""] or [""]
        for i, p in enumerate(parts):
            out.append((p, i == 0))
    return out


def wrap(text, f, max_w):
    """띄어쓰기('(' 앞 포함) 단위로 줄바꿈. 한 조각이 너무 길면 글자 단위로 자른다."""
    lines, cur = [], ""
    for piece, spaced in _pieces(text):
        trial = piece if not cur else cur + (" " if spaced else "") + piece
        if f.getlength(trial) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        if f.getlength(piece) <= max_w:
            cur = piece
        else:
            chunk = ""
            for ch in piece:
                if f.getlength(chunk + ch) <= max_w:
                    chunk += ch
                else:
                    lines.append(chunk)
                    chunk = ch
            cur = chunk
    if cur:
        lines.append(cur)
    return lines or [""]


def _alpha_layer(canvas, layer_l, color, opacity=1.0):
    """'L' 마스크 layer_l 을 color 로 canvas 위에 opacity 만큼 얹는다."""
    mask = layer_l.point(lambda v: int(v * opacity))
    canvas.paste(Image.new("RGB", canvas.size, color), (0, 0), mask)


def draw_brand(canvas, color, size=22, opacity=0.6, right=MARGIN, bottom=44):
    """'Desk Picker' 작게, 투명도 60%."""
    f = font(True, size)
    txt = "Desk Picker"
    w = text_w(f, txt)
    layer = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(layer).text((canvas.width - right - w, canvas.height - bottom), txt, font=f, fill=255, anchor="ls")
    _alpha_layer(canvas, layer, color, opacity)
    return [round(canvas.width - right - w), canvas.height - bottom - size, canvas.width - right, canvas.height - bottom]


# ── 배경 · 그림자 ────────────────────────────────────────────
def gradient_bg(w, h):
    """#F4F1EC → #ECE8E1 아주 약한 세로 그라데이션."""
    mask = Image.linear_gradient("L").resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), BG_BOT), Image.new("RGB", (w, h), BG_TOP), mask)


def draw_ellipse_shadow(canvas, cx, y, w, h=30, blur=16, opacity=0.30):
    """제품 아래 옅고 넓은 타원 그림자(가운데 x=cx, 위쪽 y)."""
    pad = blur * 3
    layer = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(layer).ellipse([cx - w / 2, y - h / 2, cx + w / 2, y + h / 2], fill=255)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    _alpha_layer(canvas, layer, SHADOW, opacity)


def _rounded_mask(size, radius, scale=4):
    w, h = size
    m = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * scale - 1, h * scale - 1], radius=radius * scale, fill=255)
    return m.resize((w, h), Image.LANCZOS)


# ── 사진 배경 판단 ───────────────────────────────────────────
def is_plain_white_bg(im, ring=8, min_channel=238, need=0.97):
    """사진 테두리 한 바퀴가 거의 흰색이면 True(제품 누끼 사진)."""
    im = im.convert("RGB")
    w, h = im.size
    strips = [im.crop((0, 0, w, ring)), im.crop((0, h - ring, w, h)), im.crop((0, 0, ring, h)), im.crop((w - ring, 0, w, h))]
    total = ok = 0
    for s in strips:
        for px in s.getdata():
            total += 1
            if min(px) >= min_channel:
                ok += 1
    return ok / max(1, total) >= need


def _white_cutout(im):
    """흰 배경 사진 → (제품만 남기고 배경을 비운 RGB, 제품 부분 마스크 L, 제품 영역 box).
    제품 바깥 가장자리만 배경색과 곱해 섞고, 제품 안쪽 흰색은 그대로 둔다."""
    im = im.convert("RGB")
    lvl = im.point(lambda v: min(255, int(v * 255 / 244)))  # JPEG 의 거의-흰색을 흰색으로
    marker = (255, 0, 255)
    work = lvl.copy()
    w, h = work.size
    for pt in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if work.getpixel(pt) != marker and min(work.getpixel(pt)) >= 240:
            ImageDraw.floodfill(work, pt, marker, thresh=14)
    bg = ImageChops.difference(work, Image.new("RGB", work.size, marker)).convert("L").point(lambda v: 0 if v > 8 else 255)
    prod = bg.point(lambda v: 255 - v)  # 제품(배경 아님) = 255
    box = prod.getbbox() or (0, 0, w, h)
    return lvl, prod, box


def _trim_blank_edges(im, max_frac=0.08):
    """사진 가장자리에 붙은 순백 띠(인쇄용 여백)만 잘라낸다. 사진 안쪽은 건드리지 않는다."""
    w, h = im.size
    g = im.convert("L")

    def blank(box):
        c = g.crop(box)
        return c.getextrema()[0] >= 250

    t = 0
    while t < h * max_frac and blank((0, t, w, t + 1)):
        t += 1
    b = 0
    while b < h * max_frac and blank((0, h - 1 - b, w, h - b)):
        b += 1
    l = 0
    while l < w * max_frac and blank((l, 0, l + 1, h)):
        l += 1
    r = 0
    while r < w * max_frac and blank((w - 1 - r, 0, w - r, h)):
        r += 1
    if t or b or l or r:
        return im.crop((l, t, w - r, h - b))
    return im


def _fit(size, box_w, box_h):
    w, h = size
    s = min(box_w / w, box_h / h)
    return max(1, round(w * s)), max(1, round(h * s))


def _white_prepared(src):
    """흰 배경 사진 → (제품 영역만 자른 RGB lvl, 제품 마스크 prod). 흰 배경이 아니면 None."""
    im = Image.open(src).convert("RGB")
    if not is_plain_white_bg(im):
        return None
    lvl, prod, box = _white_cutout(im)
    pad = round(min(im.size) * 0.02)
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad))
    return lvl.crop(box), prod.crop(box)


def _paste_white(canvas, lvl, prod, px, py):
    """제품(배경색과 곱해 섞음) + 제품 아래 타원 그림자. 한 장·모음형이 같은 그림자를 쓴다."""
    nw, nh = lvl.size
    pb = prod.getbbox() or (0, 0, nw, nh)
    bottom = py + pb[3]
    cx = px + nw / 2
    draw_ellipse_shadow(canvas, cx, bottom - 4, (pb[2] - pb[0]) * 0.85, h=max(22, nh * 0.055), blur=15, opacity=0.34)
    region = canvas.crop((px, py, px + nw, py + nh))
    canvas.paste(ImageChops.multiply(lvl, region), (px, py))


def place_product(canvas, src, area, radius=30):
    """사진을 area=(x0,y0,x1,y1) 안 가운데에 비율 그대로 놓고 그림자를 깐다.
    - 흰 배경: 제품만 오려 배경과 섞음 + 제품 아래 타원 그림자
    - 복잡한 배경: 둥근 모서리 사각형 + 아래 타원 그림자
    반환: dict(mode, box)"""
    x0, y0, x1, y1 = area
    aw, ah = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2
    prep = _white_prepared(src)
    if prep is not None:
        lvl, prod = prep
        # 그림자 자리를 위해 높이를 조금 남긴다
        nw, nh = _fit(lvl.size, aw, ah - 36)
        lvl = lvl.resize((nw, nh), Image.LANCZOS)
        prod = prod.resize((nw, nh), Image.LANCZOS)
        px, py = round(cx - nw / 2), round(y0 + (ah - 36 - nh) / 2 + 6)
        _paste_white(canvas, lvl, prod, px, py)
        return {"mode": "white_blend", "box": [px, py, px + nw, py + nh]}
    im = Image.open(src).convert("RGB")
    im = _trim_blank_edges(im)
    nw, nh = _fit(im.size, aw, ah - 28)
    im = im.resize((nw, nh), Image.LANCZOS)
    px, py = round(cx - nw / 2), round(y0 + (ah - 28 - nh) / 2)
    mask = _rounded_mask((nw, nh), radius)
    # 사각 그림자(아주 옅게) + 아래 타원 그림자
    sh = Image.new("L", canvas.size, 0)
    sh.paste(mask, (px, py + 14))
    sh = sh.filter(ImageFilter.GaussianBlur(22))
    _alpha_layer(canvas, sh, SHADOW, 0.16)
    draw_ellipse_shadow(canvas, cx, py + nh + 4, nw * 0.88, h=30, blur=16, opacity=0.22)
    canvas.paste(im, (px, py), mask)
    return {"mode": "rounded_rect", "box": [px, py, px + nw, py + nh]}


def white_bg_photos(paths, limit=COVER_MAX_COLLECT):
    """후보 사진 중 흰 배경(누끼) 사진만 순서대로 최대 limit 개."""
    out = []
    for p in paths:
        if len(out) >= limit:
            break
        if os.path.exists(p) and is_plain_white_bg(Image.open(p)):
            out.append(p)
    return out


def place_collection(canvas, srcs, area, gap=36):
    """흰 배경 제품 사진 2~3개를 나란히(겹치지 않게, 비율 그대로, 같은 그림자).
    높이를 똑같이 맞추고, 가로가 모자라면 같은 비율로 같이 줄인다. 반환: dict(mode, boxes)."""
    x0, y0, x1, y1 = area
    aw, ah = x1 - x0, y1 - y0
    preps = [_white_prepared(s) for s in srcs]
    if any(p is None for p in preps):
        raise ValueError("모음형은 흰 배경 사진만 쓸 수 있어요")
    th = ah - 36
    ratios = [p[0].width / p[0].height for p in preps]
    if sum(r * th for r in ratios) + gap * (len(preps) - 1) > aw:
        th = (aw - gap * (len(preps) - 1)) / sum(ratios)
    th = int(th)
    sizes = [(max(1, round(r * th)), th) for r in ratios]
    total_w = sum(w for w, _ in sizes) + gap * (len(sizes) - 1)
    x = round(x0 + (aw - total_w) / 2)
    py = round(y0 + (ah - 36 - th) / 2 + 6)
    boxes = []
    for (lvl, prod), (nw, nh) in zip(preps, sizes):
        lvl = lvl.resize((nw, nh), Image.LANCZOS)
        prod = prod.resize((nw, nh), Image.LANCZOS)
        _paste_white(canvas, lvl, prod, x, py)
        boxes.append([x, py, x + nw, py + nh])
        x += nw + gap
    return {"mode": "collection", "boxes": boxes, "box": [boxes[0][0], py, boxes[-1][2], py + th]}


# ── ① 표지 ──────────────────────────────────────────────────
def parse_cover_memo(text):
    """원고 메모의 [표지] 블록을 읽는다. 줄마다 '이름: 값'(범주·제목·부제·사진), 빈 줄이나 다음 [..] 에서 끝.
    반환: dict(범주·제목·부제·사진목록) — 블록이 없으면 None. 없는 칸은 키를 넣지 않는다."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "[표지]"), None)
    if start is None:
        return None
    out = {}
    for ln in lines[start + 1:]:
        if not ln.strip() or ln.strip().startswith("["):
            break
        m = re.match(r"\s*(범주|제목|부제|사진)\s*:\s*(.*?)\s*(?:←.*)?$", ln)
        if m and m.group(2):
            out[m.group(1)] = m.group(2)
    if "사진" in out:
        out["사진목록"] = [x.strip() for x in re.split(r"[|,]", out.pop("사진")) if x.strip()]
    return out or None


def fit_title_px(title, max_w, f_max=COVER_TITLE_MAX, f_min=COVER_TITLE_MIN):
    """max_w 를 넘지 않는 가장 큰 제목 글자 크기(f_min~f_max). f_min 에서도 넘으면 (f_min, False)."""
    for px in range(f_max, f_min - 1, -1):
        if text_w(font(True, px), title) <= max_w:
            return px, True
    return f_min, False


def make_cover(photo, out_path, category, title, subtitle, variant=DEFAULT_VARIANT, frame=None):
    """표지 1080x1080. 위 64% 사진 + 아래 36% 단색 띠. 반환: 메모(글자 크기·넘침 여부·쓴 사진 등).
    photo: 사진 경로 하나, 또는 후보 경로 목록. frame="A"(고르는 법) 이고 목록 안에 흰 배경 사진이 2개 이상이면
    2~3개 모음형으로, 아니면 첫 번째 사진 한 장으로 만든다."""
    th = THEMES[variant]
    meta = {"variant": variant, "frame": frame, "category": category, "title": title, "subtitle": subtitle, "warnings": []}
    canvas = Image.new("RGB", (SIZE, SIZE), th["band"])
    canvas.paste(gradient_bg(SIZE, COVER_TOP_H), (0, 0))

    cands = [photo] if isinstance(photo, str) else list(photo)
    pw = round(SIZE * COVER_PHOTO_RATIO)
    area = ((SIZE - pw) // 2, 46, (SIZE - pw) // 2 + pw, COVER_TOP_H - 14)
    whites = white_bg_photos(cands) if frame == "A" and len(cands) > 1 else []
    if len(whites) >= 2:
        meta["photo"] = place_collection(canvas, whites, area)
        meta["photo"]["files"] = whites
    else:
        if frame == "A" and len(cands) > 1:
            meta["warnings"].append("흰 배경 사진이 2개 미만이라 모음형 대신 한 장으로 만듦")
        meta["photo"] = place_product(canvas, cands[0], area)
        meta["photo"]["files"] = [cands[0]]

    d = ImageDraw.Draw(canvas)
    band_top = COVER_TOP_H
    d.line([(0, band_top), (SIZE, band_top)], fill=th["accent"], width=1)  # 강조색 1px 선

    # 글자 크기 맞추기: 제목은 띠 안쪽 너비의 85% 안에서 가장 크게(88~56px)
    inner_w = SIZE - MARGIN * 2
    meta["title_chars"] = len(title)
    if len(title) > 12:
        meta["warnings"].append(f"제목 {len(title)}자(12자 초과)")
    tsize, ok = fit_title_px(title, inner_w * COVER_TITLE_FILL)
    if not ok:
        meta["warnings"].append(f"제목이 {COVER_TITLE_MIN}px 에서도 띠 너비의 85%를 넘음")
    if text_w(font(True, tsize), title) > inner_w:
        meta["warnings"].append("제목이 한 줄에 안 들어감(넘침)")
    meta["title_px"] = tsize
    meta["title_shrunk"] = tsize < COVER_TITLE_MAX
    ssize = COVER_SUB_PX
    while ssize > 32 and text_w(font(False, ssize), subtitle) > inner_w:
        ssize -= 1
    if len(subtitle) > 16:
        meta["warnings"].append(f"부제 {len(subtitle)}자(16자 초과)")
    if text_w(font(False, ssize), subtitle) > inner_w:
        meta["warnings"].append("부제가 32px 에서도 한 줄에 안 들어감(넘침)")
    meta["subtitle_px"] = ssize
    meta["category_px"] = COVER_CAT_PX

    cat_f, title_f, sub_f = font(True, COVER_CAT_PX), font(True, tsize), font(False, ssize)
    # 세로 배치(기준선): 범주 → 제목 → 막대 → 부제. 띠 안에서 가운데 맞춤.
    gaps = (26, 34, 28)  # 범주↔제목, 제목↔막대, 막대↔부제 (글자 위쪽 여백 포함)
    h_cat, h_title, h_sub = COVER_CAT_PX * 0.75, tsize * 0.80, ssize * 0.80
    total = h_cat + gaps[0] + h_title + gaps[1] + 4 + gaps[2] + h_sub
    top = band_top + (SIZE - band_top - total) / 2 - 8
    y = top + h_cat
    draw_text(d, MARGIN, y, category, cat_f, th["accent"], spacing=5)
    y += gaps[0] + h_title
    draw_text(d, MARGIN, y, title, title_f, th["text"])
    y += gaps[1]
    d.rectangle([MARGIN, y, MARGIN + 55, y + 3], fill=th["accent"])  # 가로 56px · 두께 4px
    y += 4 + gaps[2] + h_sub
    draw_text(d, MARGIN, y, subtitle, sub_f, th["text"])
    meta["text_block"] = [MARGIN, round(top), round(MARGIN + max(text_w(title_f, title), text_w(sub_f, subtitle))), round(y)]
    meta["brand_box"] = draw_brand(canvas, th["text"], size=22, opacity=0.6)

    _save(canvas, out_path)
    return meta


def _save(canvas, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if out_path.lower().endswith(".png"):
        canvas.save(out_path)
    else:
        canvas.save(out_path, quality=JPG_QUALITY, subsampling=0)


# ── 본문 ① 제품 사진 정리 ───────────────────────────────────
def product_photo(src, out_path):
    """대표 이미지를 1080x1080 안에 비율 그대로(회백색 여백 · 옅은 그림자). 글자 없음."""
    canvas = gradient_bg(SIZE, SIZE)
    info = place_product(canvas, src, (84, 84, SIZE - 84, SIZE - 84), radius=34)
    _save(canvas, out_path)
    return info


# ── 본문 ② 표 이미지 ────────────────────────────────────────
def parse_md_tables(text):
    """원고의 '[표: ...]' 아래 줄들(칸은 ' | ')을 표로 읽는다. 반환: [rows, ...] (첫 줄 = 머리줄)."""
    tables, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        if lines[i].startswith("[표:"):
            rows = []
            i += 1
            while i < len(lines) and lines[i].strip() and " | " in lines[i]:
                rows.append([c.strip() for c in lines[i].split(" | ")])
                i += 1
            if rows:
                tables.append(rows)
        else:
            i += 1
    return tables


def _header_block(d, kicker, title, accent, max_w, title_px=46):
    """카드·표 위쪽 머리글. 반환: 다음 내용이 시작할 y."""
    y = 104
    if kicker:
        draw_text(d, MARGIN, y, kicker, font(True, 26), accent, spacing=3)
        y += 20
    tf = font(True, title_px)
    for ln in wrap(title, tf, max_w):
        y += title_px * 1.05
        draw_text(d, MARGIN, y, ln, tf, INK)
        y += title_px * 0.2
    y += 22
    d.rectangle([MARGIN, y, MARGIN + 55, y + 3], fill=accent)
    return y + 4


def _cell_font(r_i, c, hf, bf, first_f):
    return hf if r_i == 0 else (first_f if c == 0 else bf)


def _greedy_widths(rows, mins, nat, W, padx, hf, bf, first_f, step=6):
    """남는 폭을 줄바꿈이 가장 많이 줄어드는 칸부터 나눠 준다(표가 너무 길어지지 않게)."""
    ncol = len(mins)
    widths = list(mins)

    def lines_total(ws):
        tot = 0
        for r_i, r in enumerate(rows):
            tot += max(len(wrap(r[c] if c < len(r) else "", _cell_font(r_i, c, hf, bf, first_f), ws[c] - 2 * padx)) for c in range(ncol))
        return tot

    left = W - sum(widths)
    cur = lines_total(widths)
    while left > step:
        best = None  # (효율, 칸, 추가폭)
        for c in range(ncol):
            e = step
            while e <= left:
                trial = list(widths)
                trial[c] += e
                gain = cur - lines_total(trial)
                if gain > 0:
                    eff = gain / e
                    if best is None or eff > best[0]:
                        best = (eff, c, e)
                    break
                e += step
        if best is None:
            break
        _, c, e = best
        widths[c] += e
        left -= e
        cur = lines_total(widths)
    if left > 0:  # 더 줄일 줄바꿈이 없으면 남는 폭은 자연 폭 비율로
        w_nat = sum(nat) or 1
        widths = [w + left * n / w_nat for w, n in zip(widths, nat)]
    return widths


def table_image(rows, out_path, title, kicker="", footnote="", accent=GOLD):
    """마크다운 표 → 그림. 가로 1080, 머리줄 먹색+흰 글자, 줄 사이 가는 선, 칸 넓게.
    정사각(1080)에 안 들어가면 세로를 늘려 최대 1350. 반환: dict(height, font_px, tall, warnings)."""
    W = SIZE - 2 * 56
    info = {"warnings": []}
    rows = [list(r) for r in (rows or []) if r]
    if not rows or not rows[0]:
        info["warnings"].append("표가 비어 있어 그림을 만들지 않음")
        info.update({"skipped": True, "rows": 0, "cols": 0, "height": 0, "font_px": 0, "tall": False})
        return info
    ncol = len(rows[0])
    bad = sum(1 for r in rows if len(r) != ncol)
    if bad:
        info["warnings"].append(f"칸 수가 머리줄({ncol}칸)과 다른 줄 {bad}개: 모자란 칸은 비우고 넘는 칸은 뺌")
        rows = [(r + [""] * ncol)[:ncol] for r in rows]
    for fs in (28, 27, 26, 25, 24, 23, 22):
        hf, bf, first_f = font(True, fs - 1), font(False, fs), font(True, fs)
        padx, pady = 22, 20
        nat = []
        for c in range(ncol):
            m = 0
            for r_i, r in enumerate(rows):
                f = hf if r_i == 0 else (first_f if c == 0 else bf)
                m = max(m, text_w(f, r[c]) if c < len(r) else 0)
            nat.append(m + 2 * padx)
        if sum(nat) <= W:
            widths = [w * W / sum(nat) for w in nat]
        else:
            # 가장 긴 낱말 폭을 최소로 두고, 남는 폭을 자연 폭 비율로 나눈다
            mins = []
            for c in range(ncol):
                m = 0
                for r_i, r in enumerate(rows):
                    f = hf if r_i == 0 else (first_f if c == 0 else bf)
                    for wd, _sp in _pieces(r[c] if c < len(r) else ""):
                        m = max(m, text_w(f, wd))
                mins.append(m + 2 * padx)
            if sum(mins) >= W:
                widths = [w * W / sum(mins) for w in mins]
            else:
                widths = _greedy_widths(rows, mins, nat, W, padx, hf, bf, first_f)
        # 줄바꿈 · 줄 높이
        lh = round(fs * 1.38)
        cells, heights = [], []
        for r_i, r in enumerate(rows):
            row_cells, mx = [], 1
            for c in range(ncol):
                f = hf if r_i == 0 else (first_f if c == 0 else bf)
                ls = wrap(r[c] if c < len(r) else "", f, widths[c] - 2 * padx)
                row_cells.append(ls)
                mx = max(mx, len(ls))
            cells.append(row_cells)
            heights.append(mx * lh + 2 * pady)
        head_block = 104 + 20 + 46 * 1.25 + 22 + 4 + 44  # 머리글이 차지하는 높이(대략)
        foot = 96
        total = round(head_block + sum(heights) + foot)
        if total <= 1350 or fs == 22:
            break
    H = SIZE if total <= SIZE else min(1350, total)
    if total > 1350:
        info["warnings"].append(f"글자 22px 로도 세로 {total}px(1350 초과)")
        H = total
    canvas = Image.new("RGB", (SIZE, H), IVORY)
    d = ImageDraw.Draw(canvas)
    y_head_end = _header_block(d, kicker, title, accent, SIZE - 2 * MARGIN)
    table_h = sum(heights)
    # 내용이 정사각보다 짧으면 위아래 여백을 나눠 가운데로
    avail_top = y_head_end + 44
    avail_bot = H - foot
    y = avail_top + max(0, (avail_bot - avail_top - table_h) / 2 - 10) if total <= SIZE else avail_top
    x0 = 56
    y_top = y
    # 머리줄
    hh = heights[0]
    d.rounded_rectangle([x0, y, x0 + W, y + hh], radius=14, fill=INK, corners=(True, True, False, False))
    cx = x0
    for c in range(ncol):
        ty = y + pady + fs
        for ln in cells[0][c]:
            draw_text(d, cx + padx, ty - 4, ln, hf, WHITE)
            ty += lh
        cx += widths[c]
    y += hh
    for r_i in range(1, len(rows)):
        cx = x0
        for c in range(ncol):
            f = first_f if c == 0 else bf
            ty = y + pady + fs
            for ln in cells[r_i][c]:
                draw_text(d, cx + padx, ty - 4, ln, f, INK if c == 0 else (0x33, 0x37, 0x3D))
                ty += lh
            cx += widths[c]
        y += heights[r_i]
        d.line([(x0, y), (x0 + W, y)], fill=LINE, width=1)  # 줄 사이 가는 선
    if footnote:
        draw_text(d, MARGIN, H - 52, footnote, font(False, 23), MUTED)
    draw_brand(canvas, INK, size=22, opacity=0.6)
    _save(canvas, out_path)
    info.update({"height": H, "font_px": fs, "tall": H > SIZE, "rows": len(rows), "cols": ncol})
    return info


# ── 본문 ③ 카드 ─────────────────────────────────────────────
def card_image(out_path, title, sections, kicker="", footnote="", accent=GOLD):
    """고를 때 볼 기준 카드 · 장단점 요약 카드. 아이보리 바탕, 번호는 강조색.
    sections = [(라벨 또는 '', [(제목줄, 설명줄 또는 ''), ...]), ...]  (원고 문장을 줄여서 받는다)"""
    info = {"warnings": []}
    max_w = SIZE - 2 * MARGIN
    # 머리글 높이 계산(그리지 않고)
    tmp = Image.new("RGB", (SIZE, SIZE), IVORY)
    y_head = _header_block(ImageDraw.Draw(tmp), kicker, title, accent, max_w)
    bottom_limit = SIZE - 104
    chosen = None
    for scale in (1.0, 0.94, 0.88, 0.82, 0.76, 0.7):
        hs, bs, ns = round(38 * scale), round(29 * scale), round(56 * scale)
        text_x = MARGIN + round(100 * scale)
        tw = SIZE - MARGIN - text_x
        pad = round(26 * scale)
        y = y_head + 34
        layout = []
        for label, items in sections:
            if label:
                y += round(14 * scale)
                layout.append(("label", label, y))
                y += round(44 * scale)
            for it_i, (head, body) in enumerate(items):
                hl = wrap(head, font(True, hs), tw)
                bl = wrap(body, font(False, bs), tw) if body else []
                h = pad + len(hl) * round(hs * 1.28) + (len(bl) * round(bs * 1.42) + 6 if bl else 0) + pad
                layout.append(("item", (it_i + 1, hl, bl), y, h))
                y += h
        if y <= bottom_limit:
            chosen = (scale, hs, bs, ns, text_x, pad, layout, y)
            break
    if chosen is None:
        info["warnings"].append("카드 글이 넘쳐 최소 크기로도 안 들어감")
        chosen = (scale, hs, bs, ns, text_x, pad, layout, y)
    scale, hs, bs, ns, text_x, pad, layout, y_end = chosen
    info["scale"] = scale
    info["bottom_y"] = round(y_end)
    canvas = Image.new("RGB", (SIZE, SIZE), IVORY)
    d = ImageDraw.Draw(canvas)
    _header_block(d, kicker, title, accent, max_w)
    for entry in layout:
        if entry[0] == "label":
            _, label, ly = entry
            draw_text(d, MARGIN, ly + round(26 * scale), label, font(True, round(27 * scale)), INK)
            d.line([(MARGIN + text_w(font(True, round(27 * scale)), label) + 18, ly + round(18 * scale)), (SIZE - MARGIN, ly + round(18 * scale))], fill=LINE, width=1)
            continue
        _, (num, hl, bl), iy, ih = entry
        d.line([(MARGIN, iy), (SIZE - MARGIN, iy)], fill=LINE, width=1)
        by = iy + pad + round(hs * 0.95)
        draw_text(d, MARGIN, by + 4, str(num), font(True, ns), accent)  # 번호 = 강조색
        for ln in hl:
            draw_text(d, text_x, by, ln, font(True, hs), INK)
            by += round(hs * 1.28)
        by += 2
        for ln in bl:
            by += 0
            draw_text(d, text_x, by + round(bs * 0.45), ln, font(False, bs), MUTED)
            by += round(bs * 1.42)
    if footnote:
        draw_text(d, MARGIN, SIZE - 52, footnote, font(False, 23), MUTED)
    draw_brand(canvas, INK, size=22, opacity=0.6)
    _save(canvas, out_path)
    return info


# ── 본문 ④ 상세 페이지 캡처 자리(이번에는 빈 자리 표시만) ───────
def capture_placeholder(out_path, number, note, accent=GOLD):
    canvas = Image.new("RGB", (SIZE, SIZE), BG_TOP)
    d = ImageDraw.Draw(canvas)
    x0, y0, x1, y1 = 72, 72, SIZE - 72, SIZE - 72
    dash, gap = 26, 16
    col = (0xB9, 0xB2, 0xA4)
    for x in range(x0, x1, dash + gap):
        d.line([(x, y0), (min(x + dash, x1), y0)], fill=col, width=3)
        d.line([(x, y1), (min(x + dash, x1), y1)], fill=col, width=3)
    for y in range(y0, y1, dash + gap):
        d.line([(x0, y), (x0, min(y + dash, y1))], fill=col, width=3)
        d.line([(x1, y), (x1, min(y + dash, y1))], fill=col, width=3)
    draw_text(d, 112, 150, f"캡처 {number}", font(True, 26), accent, spacing=4)
    big = font(True, 56)
    t = "상세 페이지 캡처 자리"
    draw_text(d, SIZE / 2 - text_w(big, t) / 2, SIZE / 2 - 10, t, big, INK)
    nf = font(False, 31)
    y = SIZE / 2 + 52
    for ln in wrap(note, nf, 780):
        draw_text(d, SIZE / 2 - text_w(nf, ln) / 2, y, ln, nf, MUTED)
        y += 46
    _save(canvas, out_path)
    return {"note": note}


# ── 본문 ⑤ AI 장면 사진 (프롬프트만) ────────────────────────
_AI_STYLE = (
    "photorealistic lifestyle photo, square 1:1, soft natural window light, warm neutral palette "
    "(ivory, beige, light wood), calm and uncluttered, shallow depth of field. "
    "No text, no letters, no logos, no brand names, no watermark, no people's faces."
)
_AI_AVOID = "any heater, appliance or electronic product, readable text, logo, distorted hands, extra fingers"


def ai_prompt(scene, no_product=True):
    """이미지 생성 AI 에 보낼 글만 만든다(호출하지 않음). 반환: dict."""
    return {
        "prompt": f"{scene.strip()} {_AI_STYLE}",
        "avoid": _AI_AVOID if no_product else "readable text, logo, distorted hands",
        "size": "1080x1080",
        "cost_krw": AI_COST_KRW,
        "called": False,
    }


# ── 본문 사진 수 · 배치 계획 ─────────────────────────────────
DISCLOSURE_PREFIX = "이 포스팅은 네이버 쇼핑커넥트"
GUIDE_LINE = re.compile(r"^\[(사진|이미지|표|상품카드|내부링크)\s*:")


def body_chars(post_text):
    """공백 제외 글자 수. 안내 줄([사진:]·[이미지:]·[표:]·[상품카드:]·[내부링크:])과 쇼핑커넥트 안내문만 뺀다.
    그 밖에 '['로 시작하는 문장은 글자 수에 넣는다."""
    keep = [ln for ln in post_text.split("\n") if not GUIDE_LINE.match(ln.strip()) and not ln.startswith(DISCLOSURE_PREFIX)]
    return len(re.sub(r"\s", "", "".join(keep)))


def body_photo_count(post_text):
    """공백 제외 글자 수 ÷ 400 반올림, 5~8장."""
    n = int(body_chars(post_text) / 400 + 0.5)
    return max(5, min(8, n))


FRAMES = {
    "A": {"product": (2, 3), "table": (1, 1), "card": (1, 2), "capture": (0, 1), "ai": (0, 2)},
    "B": {"product": (1, 2), "capture": (1, 2), "table": (1, 1), "card": (1, 1), "ai": (0, 1)},
}
TYPE_KO = {"product": "제품 사진", "table": "표", "card": "카드", "capture": "캡처 자리", "ai": "AI 장면"}


def validate_plan(frame, photos, n_target):
    """틀 A/B 의 장수 범위와 목표 장수를 지켰는지 확인.
    반환: (문제 목록, 종류별 장수 dict) 두 개짜리 튜플. 문제가 없으면 목록은 []."""
    counts = {k: 0 for k in FRAMES[frame]}
    for p in photos:
        counts[p["type"]] = counts.get(p["type"], 0) + 1
    problems = []
    for k, (lo, hi) in FRAMES[frame].items():
        if not lo <= counts.get(k, 0) <= hi:
            problems.append(f"{TYPE_KO[k]} {counts.get(k, 0)}장(허용 {lo}~{hi})")
    if len(photos) != n_target:
        problems.append(f"총 {len(photos)}장(목표 {n_target}장)")
    return problems, counts


def post_headings(post_text):
    return [ln[3:].strip() for ln in post_text.split("\n") if ln.startswith("## ")]
