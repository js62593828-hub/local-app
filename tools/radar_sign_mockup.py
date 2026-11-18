"""N데스크피커 글 끝 서명 그림 시안 만들기 (시안 전용 · 앱 코드/데이터 안 건드림 · API 비용 0원).
실행: python tools/radar_sign_mockup.py   → preview/radar_sign_20261118/ 에 PNG 저장
글자 "Desk Picker" · 400x120 · 흰 배경(+투명 PNG) · 짙은 흑연색(#3A3A3A) 연필 질감 · 선택 금색(#C9A35B) 연필 밑줄.
"""
import os
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "preview", "radar_sign_20261118")
FONTS = os.path.join(OUT, "fonts")
TEXT = "Desk Picker"
W, H, S = 400, 120, 4  # 최종 크기, 확대 배율(확대해서 그린 뒤 줄이면 선이 곱게 나옴)
GRAPHITE = (0x3A, 0x3A, 0x3A)
GOLD = (0xC9, 0xA3, 0x5B)

# (번호, 이름, 파일, 라이선스, 글자 크기 배율)
SPECS = [
    (1, "Caveat", "Caveat.ttf", "SIL OFL 1.1", 1.00),
    (2, "Nanum Pen Script", "NanumPenScript.ttf", "SIL OFL 1.1", 1.00),
    (3, "Shadows Into Light", "ShadowsIntoLight.ttf", "SIL OFL 1.1", 1.00),
]


def _grain(size, seed, blur):
    """연필 결: 가우스 잡음을 살짝 흐려 0~1 값 지도로."""
    random.seed(seed)
    n = Image.effect_noise(size, 64)  # 평균 128
    if blur:
        n = n.filter(ImageFilter.GaussianBlur(blur))
    return n


def pencil(mask, seed, strength=0.55):
    """글자/선 마스크(L)에 연필 질감 입히기: 끝 살짝 흐림 + 결(밝은 얼룩)로 군데군데 연해짐."""
    soft = mask.filter(ImageFilter.GaussianBlur(S * 0.55))  # 끝이 살짝 흐린 선
    soft = Image.eval(soft, lambda v: min(255, int(v * 1.25)))
    fine = _grain(mask.size, seed, S * 0.25)  # 고운 결
    coarse = _grain(mask.size, seed + 7, S * 1.4)  # 큰 얼룩
    # 결 값(128 중심)을 0.. 1 로: 어두운 곳은 연필이 덜 묻은 것
    def tooth(v):
        return max(0, min(255, int(255 - (255 - v) * 0)))  # 자리 표시(아래 합성에서 직접 계산)
    f = Image.eval(fine, lambda v: int(max(0, min(255, 255 - abs(v - 128) * 3.2 * strength * 2))))
    c = Image.eval(coarse, lambda v: int(max(0, min(255, 255 - max(0, v - 128) * 5 * strength))))
    tex = ImageChops.multiply(f, c)
    tex = Image.eval(tex, lambda v: int(150 + v * 105 / 255))  # 최소 진하기 150 (너무 비지 않게)
    return ImageChops.multiply(soft, tex)


def make_sign(font_file, scale, underline, seed):
    """반환 (흰 배경 RGB 이미지, 투명 RGBA 이미지) 400x120."""
    big = (W * S, H * S)
    font_size = int(60 * S * scale)
    font = ImageFont.truetype(os.path.join(FONTS, font_file), font_size)
    if "Caveat" in font_file:
        try:
            font.set_variation_by_axes([600])  # 굵기(wght) 600: 연필로 눌러 쓴 느낌
        except Exception:
            pass
    # 글자 크기를 폭 300px 안에 맞춤
    for _ in range(40):
        bb = font.getbbox(TEXT)
        if (bb[2] - bb[0]) <= 300 * S and (bb[3] - bb[1]) <= (62 if underline else 72) * S:
            break
        font_size = int(font_size * 0.95)
        font = ImageFont.truetype(os.path.join(FONTS, font_file), font_size)
        if "Caveat" in font_file:
            try:
                font.set_variation_by_axes([600])
            except Exception:
                pass
    bb = font.getbbox(TEXT)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = (big[0] - tw) // 2 - bb[0]
    top = (22 if underline else 24) * S
    y = top + ((62 if underline else 72) * S - th) // 2 - bb[1]
    tmask = Image.new("L", big, 0)
    ImageDraw.Draw(tmask).text((x, y), TEXT, font=font, fill=255)
    # 글자를 아주 살짝 굵게(연필 심 두께)
    tmask = tmask.filter(ImageFilter.MaxFilter(3))
    tpen = pencil(tmask, seed)

    layers = [(tpen, GRAPHITE)]
    if underline:
        umask = Image.new("L", big, 0)
        d = ImageDraw.Draw(umask)
        ul_w = int(tw * 0.62)
        x0 = (big[0] - ul_w) // 2
        y0 = 96 * S
        rnd = random.Random(seed)
        pts = [(x0 + i * ul_w / 24, y0 + rnd.uniform(-0.8, 0.8) * S + (i - 12) * 0.05 * S) for i in range(25)]
        d.line(pts, fill=255, width=int(3.2 * S), joint="curve")
        layers.append((pencil(umask, seed + 99, strength=0.7), GOLD))

    # 합성: 투명 판 (RGBA) 위에 색마다 알파로 얹기
    rgba = Image.new("RGBA", big, (0, 0, 0, 0))
    for alpha, color in layers:
        col = Image.new("RGBA", big, color + (255,))
        col.putalpha(alpha)
        rgba = Image.alpha_composite(rgba, col)
    rgba = rgba.resize((W, H), Image.LANCZOS)
    white = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    white = Image.alpha_composite(white, rgba).convert("RGB")
    return white, rgba


def _kfont(size):
    for p in (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\malgunbd.ttf"):
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main():
    os.makedirs(OUT, exist_ok=True)
    made = {}
    for num, name, ff, lic, sc in SPECS:
        for ul in (True, False):
            white, rgba = make_sign(ff, sc, ul, seed=num * 11)
            tag = "밑줄있음" if ul else "밑줄없음"
            base = f"시안{num}_{name.replace(' ', '')}_{tag}"
            white.save(os.path.join(OUT, base + ".png"))
            rgba.save(os.path.join(OUT, base + "_투명.png"))
            made[(num, ul)] = white
    # 비교_한눈에.png : 가로 시안 3 × 세로(밑줄 있음/없음) 2
    cw, ch, pad, head = W, H, 24, 64
    sheet = Image.new("RGB", (pad + 3 * (cw + pad), head + 2 * (ch + 52) + pad), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    f1, f2 = _kfont(20), _kfont(14)
    for ci, (num, name, ff, lic, sc) in enumerate(SPECS):
        x = pad + ci * (cw + pad)
        d.text((x, 14), f"시안 {num} · {name}", font=f1, fill=(40, 40, 40))
        d.text((x, 40), lic, font=f2, fill=(110, 110, 110))
        for ri, ul in enumerate((True, False)):
            y = head + ri * (ch + 52)
            sheet.paste(made[(num, ul)], (x, y))
            d.rectangle([x, y, x + cw - 1, y + ch - 1], outline=(215, 215, 215))
            d.text((x, y + ch + 6), "밑줄 있음" if ul else "밑줄 없음", font=f2, fill=(110, 110, 110))
    sheet.save(os.path.join(OUT, "비교_한눈에.png"))
    # 본문 끝 문단 + 서명(시안 1 기준) 미리보기: 네이버 본문 폭 약 700px 흰 바탕
    pw, ph = 720, 380
    prev = Image.new("RGB", (pw, ph), (255, 255, 255))
    d = ImageDraw.Draw(prev)
    fb = _kfont(17)
    lines = [
        "폰에 붙인 채 가볍게 쓰려면 벨킨 BPD004, 구성과 가격을 보면 이어맥 팬톤,",
        "유선 충전 속도와 얇기를 보면 앤커 맥고 A1664가 맞아요.",
        "",
        "무게와 충전 속도 중 무엇이 더 중요한지 먼저 정하면",
        "세 제품 중 하나로 좁히기 쉬워요.",
    ]
    y = 36
    for ln in lines:
        d.text((40, y), ln, font=fb, fill=(34, 34, 34))
        y += 32
    prev.paste(made[(1, True)], ((pw - W) // 2, y + 24))
    d.text((12, ph - 22), "(본문 끝 문단 + 서명 시안 1 · 밑줄 있음 / 미리보기용, 실제 글엔 안 들어감)", font=_kfont(12), fill=(150, 150, 150))
    prev.save(os.path.join(OUT, "본문끝_미리보기_시안1.png"))
    print("완료:", OUT)


if __name__ == "__main__":
    main()
