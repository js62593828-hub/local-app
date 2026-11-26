"""
생활레이더 본문 카드 그리기 (Pillow, 비용 0원 · API 호출 없음)

카드 한 장 = 메모 `카드:` 블록의 한 줄. 종류는 세 가지입니다.
  전후 : 지금 → 바뀐 뒤 비교   (내용 줄마다 "항목: 지금값 → 바뀐값")
  체크 : 번호 목록             (내용 줄마다 한 문장)
  계산 : 큰 숫자 + 계산식       (내용 = 큰 숫자 ; 계산식 ; 한 줄 설명 ; (선택) 작은 안내)
디자인은 preview/radar_card_mockup_1~3.png 를 그대로 옮긴 것입니다(남색 #0E2142 · 주황 #FF8A1F).
글자가 넘쳐도 글자를 줄이지 않고 warnings에 알리기만 합니다(숫자는 사람이 확인).
"""
import os
import re

from PIL import Image, ImageDraw, ImageFont

SIZE = 1080
NAVY = (14, 33, 66)
NAVY_BOX = (26, 53, 102)
ORANGE = (255, 138, 31)
WHITE = (255, 255, 255)
PALE = (230, 238, 249)
GRAY_BLUE = (174, 185, 209)
GRAY = (122, 134, 158)
INK = (17, 24, 39)
MARGIN = 72
JPG_QUALITY = 85

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_BOLD = [os.path.join(_FONT_DIR, "Pretendard-ExtraBold.otf"), "C:\\Windows\\Fonts\\malgunbd.ttf", "malgunbd.ttf"]
_MEDIUM = [os.path.join(_FONT_DIR, "Pretendard-Medium.otf"), "C:\\Windows\\Fonts\\malgun.ttf", "malgun.ttf"]
BADGE_BY_KIND = {"전후": "달라지는 점", "체크": "신청 전 확인", "계산": "계산 예시"}

_AFTER_RE = re.compile(r"\s*(?:→|->|⇒|=>)\s*")
_DATE_RE = re.compile(r"📅\s*(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def _font(size: int, bold: bool = True):
    for p in (_BOLD if bold else _MEDIUM):
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def find_base_date(raw: str) -> str:
    """원고 첫 줄들의 "📅 2026.10.12 기준 …"에서 기준일을 "YYYY.M.D"로. 없으면 ""."""
    for line in (raw or "").splitlines()[:8]:
        m = _DATE_RE.search(line)
        if m:
            return f"{int(m.group(1))}.{int(m.group(2))}.{int(m.group(3))}"
    return ""


def _w(draw, text, font) -> float:
    return draw.textlength(text, font=font)


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """공백 기준으로 줄바꿈하고, 한 단어가 너무 길면 글자 단위로 끊습니다."""
    lines, cur = [], ""
    for word in text.split(" "):
        trial = word if not cur else cur + " " + word
        if _w(draw, trial, font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        if _w(draw, word, font) <= max_w:
            cur = word
            continue
        piece = ""
        for ch in word:
            if _w(draw, piece + ch, font) <= max_w:
                piece += ch
            else:
                lines.append(piece)
                piece = ch
        cur = piece
    if cur:
        lines.append(cur)
    return lines or [""]


def _wrap_balanced(draw, text: str, font, max_w: int) -> list[str]:
    """줄바꿈 결과가 2줄이면, 두 줄의 길이가 비슷해지는 띄어쓰기 자리에서 나눕니다(제목이 한 줄만 길어 보이지 않게)."""
    lines = _wrap(draw, text, font, max_w)
    if len(lines) != 2:
        return lines
    words = " ".join(lines).split(" ")
    best, best_w = lines, max(_w(draw, ln, font) for ln in lines)
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        w = max(_w(draw, a, font), _w(draw, b, font))
        if w <= max_w and w < best_w:
            best, best_w = [a, b], w
    return best


def _round(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _badge(draw, text, fill, color):
    f = _font(26)
    tw = _w(draw, text, f)
    _round(draw, (MARGIN, 70, MARGIN + tw + 44, 128), 29, fill)
    draw.text((MARGIN + 22, 99), text, font=f, fill=color, anchor="lm")


def _draw_channel_brand() -> bool:
    try:
        import image_gen
        return bool(getattr(image_gen, "DRAW_CHANNEL_BRAND", False))
    except ImportError:
        return False


def _logo(draw, dark_bg: bool):
    f = _font(34)
    text = "생활레이더"
    color = WHITE if dark_bg else NAVY
    tw = _w(draw, text, f)
    right = SIZE - MARGIN + 0
    x = right - tw
    cy = 1000
    draw.text((x, cy), text, font=f, fill=color, anchor="lm")
    cx, base = x - 40, cy + 13   # 레이더 아이콘(직접 그림): 점 + 위쪽 호 3개
    draw.ellipse((cx - 5, base - 5, cx + 5, base + 5), fill=ORANGE)
    for r in (13, 24, 35):
        draw.arc((cx - r, base - r, cx + r, base + r), start=225, end=315, fill=ORANGE, width=5)


def _footer(draw, date_str: str, source: str, dark_bg: bool, warnings: list):
    if date_str and source:
        text = f"{date_str} 기준 · 출처: {source}"
    elif source:
        text = f"출처: {source}"
        warnings.append("기준일을 못 찾았어요(원고 첫 줄 '📅 … 기준')")
    elif date_str:
        text = f"{date_str} 기준"
        warnings.append("출처 기관이 비어 있어요")
    else:
        text = ""
        warnings.append("기준일과 출처 기관이 둘 다 비어 있어요")
    draw.text((MARGIN, 1000), text, font=_font(26, bold=False), fill=GRAY_BLUE if dark_bg else GRAY, anchor="lm")


def _title(draw, text, color, size, y, warnings, max_lines=2):
    f = _font(size)
    lines = _wrap_balanced(draw, text, f, SIZE - 2 * MARGIN)
    if len(lines) > max_lines:
        warnings.append(f"제목이 {len(lines)}줄로 넘쳐요(최대 {max_lines}줄)")
    gap = int(size * 1.28)
    for i, ln in enumerate(lines):
        draw.text((MARGIN, y + i * gap), ln, font=f, fill=color)
    return y + len(lines) * gap


def _split_before_after(item: str):
    label, rest = "", item
    m = re.match(r"^(.*?)[:：]\s*(.+)$", item)
    if m and not _AFTER_RE.search(m.group(1)):
        label, rest = m.group(1).strip(), m.group(2).strip()
    parts = _AFTER_RE.split(rest, maxsplit=1)
    if len(parts) == 2:
        return label, parts[0].strip(), parts[1].strip()
    return label, rest.strip(), ""


def _draw_compare(im, draw, card, warnings):
    _badge(draw, BADGE_BY_KIND["전후"], ORANGE, WHITE)
    _title(draw, card["title"], WHITE, 78, 180, warnings)
    y0, y1 = 395, 865
    _round(draw, (MARGIN, y0, 481, y1), 36, NAVY_BOX)
    _round(draw, (600, y0, 1009, y1), 36, WHITE)
    draw.rectangle((497, 598, 553, 656), fill=ORANGE)
    draw.polygon([(553, 566), (598, 627), (553, 688)], fill=ORANGE)
    draw.text((108, y0 + 52), "지금", font=_font(34), fill=(200, 208, 230), anchor="lm")
    draw.text((636, y0 + 52), "바뀐 뒤", font=_font(34), fill=ORANGE, anchor="lm")
    rows = [_split_before_after(it) for it in card["items"]]
    if len(rows) > 3:
        warnings.append(f"항목이 {len(rows)}개예요(3개까지 칸 안에 들어가요)")
    lab_f, val_f = _font(26, bold=False), _font(46)
    inner_w = 481 - MARGIN - 72
    for i, (label, before, after) in enumerate(rows):
        ly = y0 + 112 + i * 118
        vy = ly + 38
        draw.text((108, ly), label, font=lab_f, fill=GRAY_BLUE)
        draw.text((108, vy), before, font=val_f, fill=WHITE)
        draw.text((636, ly), label, font=lab_f, fill=GRAY)
        draw.text((636, vy), after, font=val_f, fill=ORANGE if i < 2 else INK)
        if _w(draw, before, val_f) > inner_w or _w(draw, after, val_f) > inner_w:
            warnings.append(f"'{label or before}' 줄의 숫자 글자가 칸 폭을 넘쳐요")
        if not after:
            warnings.append(f"'{label or before}' 줄에 → 뒤의 값이 없어요")


def _draw_check(im, draw, card, warnings):
    draw.rectangle((0, 0, SIZE, 16), fill=ORANGE)
    _badge(draw, BADGE_BY_KIND["체크"], NAVY, WHITE)
    _title(draw, card["title"], NAVY, 78, 180, warnings)
    items = card["items"]
    n = len(items)
    if n > 4:
        warnings.append(f"항목이 {n}개예요(4개까지 칸 안에 들어가요)")
    row_h, gap = (140, 24) if n <= 3 else (112, 18)
    f = _font(36)
    text_x = 212
    max_w = 1009 - text_x - 36
    y = 440
    for i, it in enumerate(items):
        _round(draw, (MARGIN, y, 1009, y + row_h), 36, WHITE)
        cx, cy = MARGIN + 70, y + row_h // 2
        draw.ellipse((cx - 40, cy - 40, cx + 40, cy + 40), fill=ORANGE)
        draw.text((cx, cy), str(i + 1), font=_font(40), fill=WHITE, anchor="mm")
        lines = _wrap(draw, it, f, max_w)
        if len(lines) > 2:
            warnings.append(f"{i + 1}번 문장이 {len(lines)}줄로 넘쳐요(최대 2줄)")
        total = len(lines) * 46
        ty = cy - total // 2
        for j, ln in enumerate(lines):
            draw.text((text_x, ty + j * 46), ln, font=f, fill=NAVY)
        y += row_h + gap


def _draw_calc(im, draw, card, warnings):
    draw.rectangle((0, 0, SIZE, 360), fill=NAVY)
    _badge(draw, BADGE_BY_KIND["계산"], ORANGE, WHITE)
    _title(draw, card["title"], WHITE, 64, 160, warnings)
    items = card["items"]
    big = items[0] if items else ""
    bf = _font(215)
    if _w(draw, big, bf) > SIZE - 2 * MARGIN:
        warnings.append("큰 숫자가 칸 폭을 넘쳐요(짧게 줄여 주세요)")
    draw.text((MARGIN + 6, 405), big, font=bf, fill=ORANGE)
    if len(items) > 2 and items[2]:
        sub_lines = _wrap(draw, items[2], _font(40), SIZE - 2 * MARGIN)
        if len(sub_lines) > 1:
            warnings.append("설명 줄이 한 줄을 넘어요")
        draw.text((MARGIN + 6, 690), sub_lines[0], font=_font(40), fill=NAVY, anchor="lm")
    if len(items) > 1 and items[1]:
        _round(draw, (MARGIN, 755, 1009, 940), 36, PALE)
        draw.text((112, 790), "계산식", font=_font(24), fill=GRAY, anchor="lm")
        ff = _font(40)
        flines = _wrap(draw, items[1], ff, 1009 - 112 - 36)
        if len(flines) > 1:
            warnings.append("계산식이 한 줄을 넘어요")
        draw.text((112, 836), flines[0], font=ff, fill=NAVY, anchor="lm")
        if len(items) > 3 and items[3]:
            draw.text((112, 898), items[3], font=_font(24, bold=False), fill=GRAY, anchor="lm")


def render_card(card: dict, date_str: str, out_path: str) -> dict:
    """카드 한 장을 1080×1080 JPG로 저장. 반환: {"path": out_path, "warnings": [...]}"""
    warnings: list = []
    kind = card.get("kind")
    if kind not in BADGE_BY_KIND:
        raise ValueError(f"카드 종류를 알 수 없어요: {kind}")
    dark = kind in ("전후",)
    im = Image.new("RGB", (SIZE, SIZE), NAVY if kind == "전후" else (PALE if kind == "체크" else WHITE))
    draw = ImageDraw.Draw(im)
    if kind == "전후":
        _draw_compare(im, draw, card, warnings)
    elif kind == "체크":
        _draw_check(im, draw, card, warnings)
    else:
        _draw_calc(im, draw, card, warnings)
    _footer(draw, date_str, card.get("source", ""), dark, warnings)
    if _draw_channel_brand():  # 채널 이름·로고: image_gen.DRAW_CHANNEL_BRAND가 False면 그리지 않습니다(되돌리기용으로 _logo는 남김)
        _logo(draw, dark)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    im.save(out_path, "JPEG", quality=JPG_QUALITY, optimize=True)
    return {"path": out_path, "warnings": warnings}
