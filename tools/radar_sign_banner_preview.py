"""서명 800판·배너 새 시안 미리보기 만들기 (시안 전용 · 앱 코드/데이터 안 건드림 · API 비용 0원).
실행: python -I tools/radar_sign_banner_preview.py sign|banner
"""
import os, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
MALGUN = "C:/Windows/Fonts/malgun.ttf"
PARA1 = "그래서 휴대용 스피커를 처음 사려는 분께는 FLIP7을 먼저 권합니다. 방수와 배터리, 소리 균형이 한 번에 맞는 제품은 많지 않았습니다."
PARA2 = "가격은 시기마다 달라지니 아래에서 지금 값을 확인해 보세요. 읽어 주셔서 감사합니다."

def kfont(sz): return ImageFont.truetype(MALGUN, sz)

def wrap(draw, text, font, width):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > width:
            lines.append(cur); cur = ch.lstrip()
        else: cur += ch
    return lines + ([cur] if cur else [])

def page(body_w, parts, label, pad=24):
    """parts: 문단 글자(str) 또는 PIL 이미지(본문 폭 안에서 폭이 body_w 보다 크면 body_w 로 줄임). 흰 바탕 미리보기."""
    f = kfont(16); d0 = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    items = []
    for p in parts:
        if isinstance(p, str):
            items.append(("t", wrap(d0, p, f, body_w)))
        else:
            im = p.convert("RGBA")
            if im.width > body_w:
                im = im.resize((body_w, round(im.height * body_w / im.width)), Image.LANCZOS)
            items.append(("i", im))
    h = pad * 2 + 22
    for k, v in items: h += (len(v) * 26 + 18) if k == "t" else (v.height + 18)
    cv = Image.new("RGB", (body_w + pad * 2, h), "white"); d = ImageDraw.Draw(cv)
    y = pad
    for k, v in items:
        if k == "t":
            for ln in v: d.text((pad, y), ln, font=f, fill=(34, 38, 43)); y += 26
            y += 18
        else:
            x = pad + (body_w - v.width) // 2 if SIGN_CENTER else pad
            cv.paste(v, (pad if k == "i" and v.width >= body_w else x, y), v); y += v.height + 18
    d.text((pad, h - 22), label, font=kfont(11), fill=(150, 150, 150))
    d.rectangle([0, 0, cv.width - 1, cv.height - 1], outline=(225, 225, 225))
    return cv

SIGN_CENTER = True  # 가운데 놓기(폭이 본문 폭과 같으면 상관없음, 배너는 가운데)

def contact(cvs, labels, out, gap=24, title=""):
    hh = max(c.height for c in cvs); ww = sum(c.width for c in cvs) + gap * (len(cvs) + 1)
    sheet = Image.new("RGB", (ww, hh + 70), (240, 240, 240)); d = ImageDraw.Draw(sheet)
    x = gap
    for c, l in zip(cvs, labels):
        sheet.paste(c, (x, 50)); d.text((x, 16), l, font=kfont(16), fill=(30, 30, 30)); x += c.width + gap
    d.text((gap, hh + 54), title, font=kfont(11), fill=(110, 110, 110))
    if out: sheet.save(out)
    return sheet

def do_sign():
    import radar_sign_make as m
    out = os.path.join(ROOT, "preview", "radar_sign_20261118"); pcs, phs, lab = [], [], []
    for v in "AB":
        sw, sh = m.SIGN_SIZES[v]
        plate = Image.open(os.path.join(out, f"시안3_서명800_{v}_{sw}x{sh}.png"))
        t = f"시안{v}: 판 800×60 · 서명 {sw}×{sh}"
        pc = page(800, [PARA1, PARA2, plate], t + " · PC(본문 폭 800)")
        ph = page(360, [PARA1, PARA2, plate], t + " · 휴대폰(본문 폭 360)")
        pc.save(os.path.join(out, f"미리보기_서명{v}_PC.png")); ph.save(os.path.join(out, f"미리보기_서명{v}_휴대폰.png"))
        pcs.append(pc); phs.append(ph); lab.append(t)
    top = contact(pcs, [lab[0] + " PC", lab[1] + " PC"], None)
    bot = contact(phs, [lab[0] + " 휴대폰", lab[1] + " 휴대폰"], None)
    W = max(top.width, bot.width); sheet = Image.new("RGB", (W, top.height + bot.height), (240, 240, 240))
    sheet.paste(top, (0, 0)); sheet.paste(bot, (0, top.height)); sheet.save(os.path.join(out, "비교_서명800.png"))

if __name__ == "__main__":
    {"sign": do_sign}.get(sys.argv[1], lambda: None)()
