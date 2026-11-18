"""N데스크피커 '네이버에서 가격 확인하기' 배너 새 시안 (시안 전용 · 앱에 적용 안 함 · API 비용 0원).
실행: python -I tools/radar_banner_mockup.py → preview/radar_banner_20261118/
현재 배너(banner_white.png 1600×242): 둥근 사각형 1600×176(모서리 약 30) + 아래 가운데 꼬리(밑변 736~863, 끝 y=240).
"""
import os, sys, math
from PIL import Image, ImageDraw, ImageFont
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "preview", "radar_banner_20261118")
FONTS = os.path.join(OUT, "fonts")
W, H, RH, R = 1600, 242, 176, 30
TAIL_L, TAIL_R, TAIL_Y = 736, 864, 241
SS = 4
GOLD = (0xC9, 0xA3, 0x5B); INK = (0x22, 0x26, 0x2B); IVORY = (0xFB, 0xF9, 0xF5); WHITE = (255, 255, 255)
NAVER = (5, 172, 79)  # 현재 배너 녹색 #05AC4F
BORDER = 8
TEXT = "네이버에서 가격 확인하기"

def shape(inset):
    """말풍선(둥근 사각형+꼬리) 마스크를 SS배로 그림. inset=테두리 안쪽으로 줄인 값."""
    m = Image.new("L", (W * SS, H * SS), 0); d = ImageDraw.Draw(m)
    b = inset
    d.rounded_rectangle([b * SS, b * SS, (W - b) * SS - 1, (RH - b) * SS - 1], radius=max(1, R - b) * SS, fill=255)
    cx = (TAIL_L + TAIL_R) / 2; half = (TAIL_R - TAIL_L) / 2
    k = b * math.sqrt(2)
    y0 = RH - b - 1
    hw0 = (TAIL_Y - y0) - k if b else (TAIL_Y - y0)
    hw0 = min(hw0, half + (RH - y0)) if b == 0 else hw0
    pts = [(cx - hw0, y0), (cx + hw0, y0), (cx, TAIL_Y - k)] if b else [(cx - half - 1, RH - 8), (cx + half + 1, RH - 8), (cx, TAIL_Y)]
    d.polygon([(x * SS, y * SS) for x, y in pts], fill=255)
    return m.resize((W, H), Image.LANCZOS)

def bubble(fill, line):
    outer, inner = shape(0), shape(BORDER)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    o = Image.new("RGBA", (W, H), line + (255,)); o.putalpha(outer); img.alpha_composite(o)
    i = Image.new("RGBA", (W, H), fill + (255,)); i.putalpha(inner); img.alpha_composite(i)
    return img

def text_layer(img, fontfile, color, arrow=False, size=100):
    f = ImageFont.truetype(os.path.join(FONTS, fontfile), size)
    d = ImageDraw.Draw(img)
    bb = d.textbbox((0, 0), TEXT, font=f)
    tw = bb[2] - bb[0]
    aw = 0
    if arrow: aw = 28 + 90
    x0 = (W - (tw + aw)) / 2
    cy = RH / 2
    d.text((x0 - bb[0], cy - (bb[1] + bb[3]) / 2 + 2), TEXT, font=f, fill=color + (255,))
    if arrow:  # 글자 끝 작은 금색 →
        ax = x0 + tw + 34; ay = cy + 2
        lay = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0)); ld = ImageDraw.Draw(lay)
        L = 70; hh = 20; wd = 7
        ld.line([(ax * SS, ay * SS), ((ax + L) * SS, ay * SS)], fill=GOLD + (255,), width=wd * SS)
        ld.line([((ax + L - hh) * SS, (ay - hh) * SS), ((ax + L) * SS, ay * SS), ((ax + L - hh) * SS, (ay + hh) * SS)], fill=GOLD + (255,), width=wd * SS, joint="curve")
        for px, py in [(ax, ay), (ax + L - hh, ay - hh), (ax + L - hh, ay + hh), (ax + L, ay)]:  # 끝 둥글게
            ld.ellipse([(px - wd / 2) * SS, (py - wd / 2) * SS, (px + wd / 2) * SS, (py + wd / 2) * SS], fill=GOLD + (255,))
        img.alpha_composite(lay.resize((W, H), Image.LANCZOS))
    return img

def make(weight):
    ff = f"Pretendard-{weight}.otf"
    out = {}
    out["A"] = text_layer(bubble(IVORY, GOLD), ff, INK)
    out["B"] = text_layer(bubble(WHITE, GOLD), ff, NAVER)
    out["C"] = text_layer(bubble(WHITE, GOLD), ff, INK, arrow=True)
    return out

if __name__ == "__main__":
    for wt in ("Light", "Regular"):
        sub = os.path.join(OUT, wt); os.makedirs(sub, exist_ok=True)
        for k, im in make(wt).items():
            im.save(os.path.join(sub, f"배너_{k}_{wt}.png"))
    Image.open(os.path.join(ROOT, "static", "radar", "banner_white.png")).convert("RGBA").save(os.path.join(OUT, "배너_D_지금것.png"))
    print("ok")
