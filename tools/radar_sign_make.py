"""N데스크피커 글 끝 서명 그림 만들기 (시안 3 · Shadows Into Light · 금색 밑줄 있음). API 비용 0원.
실행: python tools/radar_sign_make.py [A|B|C, 기본 C] [결 세기 0~1, 기본 0.3] → static/radar/sign_deskpicker.png
그림 판은 가로 1800×세로 135 흰 배경이고, 서명(C 360×108 · 옛 A 120×36 / B 160×48)은 판 오른쪽 끝(오른쪽 여백 36px, 세로 가운데)에
줄이지 않고 그 크기로 바로 그린다(시안 400x120 의 좌표를 서명 가로/400 배).
"""
import os
import sys
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "static", "radar", "fonts", "ShadowsIntoLight.ttf")
OUT = os.path.join(ROOT, "static", "radar", "sign_deskpicker.png")
TEXT = "Desk Picker"
PLATE = (1800, 135)  # 그림 판(흰 배경, 본문 폭에 꽉 차는 크기)
MARGIN_R = 36  # 판 오른쪽 여백
SIGN_SIZES = {"A": (120, 36), "B": (160, 48), "C": (360, 108)}
GRAPHITE = (0x3A, 0x3A, 0x3A)
GOLD = (0xC9, 0xA3, 0x5B)


def _grain(size, blur):
    n = Image.effect_noise(size, 64)
    return n.filter(ImageFilter.GaussianBlur(blur)) if blur else n


def pencil(mask, strength, blur_edge=0.45):
    soft = mask.filter(ImageFilter.GaussianBlur(blur_edge))
    soft = Image.eval(soft, lambda v: min(255, int(v * 1.25)))
    random.seed(1)
    fine = _grain(mask.size, 0.4)
    coarse = _grain(mask.size, 1.2)
    f = Image.eval(fine, lambda v: int(max(0, min(255, 255 - abs(v - 128) * 3.2 * strength * 2))))
    c = Image.eval(coarse, lambda v: int(max(0, min(255, 255 - max(0, v - 128) * 5 * strength))))
    tex = ImageChops.multiply(f, c)
    tex = Image.eval(tex, lambda v: int(150 + v * 105 / 255))
    return ImageChops.multiply(soft, tex)


def draw_sign(sw, sh, strength=0.3):
    """서명만 sw×sh 크기에 바로 그려 투명 바탕 RGBA 로 돌려준다."""
    W, H = sw, sh
    K = W / 400.0
    size = (W, H)
    fsz = int(60 * K)
    font = ImageFont.truetype(FONT, fsz)
    box_w, box_h = 300 * K, 62 * K
    for _ in range(40):
        bb = font.getbbox(TEXT)
        if (bb[2] - bb[0]) <= box_w and (bb[3] - bb[1]) <= box_h:
            break
        fsz -= 1
        font = ImageFont.truetype(FONT, fsz)
    bb = font.getbbox(TEXT)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = (W - tw) / 2 - bb[0]
    y = 22 * K + (box_h - th) / 2 - bb[1]
    tmask = Image.new("L", size, 0)
    ImageDraw.Draw(tmask).text((x, y), TEXT, font=font, fill=255)
    layers = [(pencil(tmask, strength), GRAPHITE)]
    umask = Image.new("L", size, 0)
    ul_w = tw * 0.62
    x0 = (W - ul_w) / 2
    y0 = 96 * K
    rnd = random.Random(33)
    pts = [(x0 + i * ul_w / 24, y0 + rnd.uniform(-0.8, 0.8) * K + (i - 12) * 0.05 * K) for i in range(25)]
    ImageDraw.Draw(umask).line(pts, fill=255, width=max(1, round(3.2 * K * 1.4)), joint="curve")
    layers.append((pencil(umask, strength + 0.15, 0.35), GOLD))
    rgba = Image.new("RGBA", size, (0, 0, 0, 0))
    for alpha, color in layers:
        col = Image.new("RGBA", size, color + (255,))
        col.putalpha(alpha)
        rgba = Image.alpha_composite(rgba, col)
    return rgba


def make(variant="C", strength=0.3, out=OUT):
    sw, sh = SIGN_SIZES[variant]
    sign = draw_sign(sw, sh, strength)
    plate = Image.new("RGBA", PLATE, (255, 255, 255, 255))
    plate.alpha_composite(sign, (PLATE[0] - MARGIN_R - sw, (PLATE[1] - sh) // 2))
    plate.convert("RGB").save(out)
    return out


if __name__ == "__main__":
    v = sys.argv[1].upper() if len(sys.argv) > 1 and sys.argv[1].upper() in SIGN_SIZES else "C"
    s = float(sys.argv[2]) if len(sys.argv) > 2 else 0.3
    print(make(v, s))
