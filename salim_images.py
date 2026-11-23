"""N살림노트(naver_tips) 전용 그림 함수 (Pillow, API 호출 0, 비용 0).
radar_images(데스크피커) 의 별점 함수는 쓰지 않고, 살림노트 전용 '별점 상자'를 여기서 따로 그린다."""
import math
import os

# 표지(image_gen.NAVER_COVER_PALETTES)와 같은 강조색 표. 표지 색은 글마다 7색 중 하나(메모의 '표지 색', 없으면 무작위)라
# 어느 색을 썼는지 글에 저장돼 있지 않으므로, 메모에 '표지 색' 이 있으면 그 색을, 없으면 SALIM_STAR_DEFAULT 를 쓴다.
SALIM_STAR_DEFAULT = "머스타드"
BOX_BG = (247, 244, 238)       # 연한 배경
BOX_LINE = (232, 226, 214)     # 상자 테두리
TEXT_MAIN = (34, 38, 43)       # 윗줄 글자
TEXT_SUB = (140, 140, 140)     # 아랫줄 작은 회색
BOX_W = 400
BOX_H = 116
_SS = 3  # 부드럽게 그리기 위해 3배로 그린 뒤 줄임

_FONT_BOLD = os.path.join("fonts", "Pretendard-ExtraBold.otf")   # 표지 큰 글씨와 같은 글꼴
_FONT_MED = os.path.join("fonts", "Pretendard-Medium.otf")


def accent_rgb(color_name=""):
    """표지 강조색 이름(로즈·올리브·머스타드·라벤더·테라코타·블루그레이·틸그린) → (r,g,b), 이름과 표시 글자. 모르면 기본색."""
    try:
        import image_gen
        pal = image_gen.NAVER_COVER_PALETTES
    except Exception:
        pal = {"머스타드": {"accent": (230, 170, 40)}}
    name = (color_name or "").strip()
    if name in pal:
        return tuple(pal[name]["accent"]), name
    d = SALIM_STAR_DEFAULT
    return tuple(pal.get(d, {"accent": (230, 170, 40)})["accent"]), d


def _font(path, size):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


def _star_points(cx, cy, r_out, r_in=None):
    r_in = r_in or r_out * 0.40
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def star_box_texts(rating, reviews, date):
    """별점 상자 글자: ("★ 4.84 · 리뷰 20,118개" 중 ★ 뒤 글자, 아랫줄). rating 은 자료 글자 그대로, reviews 는 쉼표 붙임, date 는 2026-11-19 → 2026.11.19."""
    line1 = f"{rating} · 리뷰 {int(reviews):,}개"
    line2 = str(date).replace("-", ".") + " 기준"
    return line1, line2


def star_box_image(rating, reviews, date, out_path, accent=(230, 170, 40)):
    """별점 상자 그림을 out_path(PNG) 에 저장. 가로 400. 윗줄 '★ 평점 · 리뷰 n개'(별은 강조색), 아랫줄 작은 회색 '날짜 기준'.
    연한 배경 둥근 상자(바깥은 흰색). 반환 out_path."""
    from PIL import Image, ImageDraw
    W, H = BOX_W * _SS, BOX_H * _SS
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    pad = 2 * _SS
    d.rounded_rectangle([pad, pad, W - pad, H - pad], radius=22 * _SS, fill=BOX_BG, outline=BOX_LINE, width=2 * _SS)
    line1, line2 = star_box_texts(rating, reviews, date)
    f1 = _font(_FONT_BOLD, 30 * _SS)
    f2 = _font(_FONT_MED, 17 * _SS)
    star_r = 17 * _SS
    gap = 12 * _SS
    w1 = d.textlength(line1, font=f1)
    total = star_r * 2 + gap + w1
    x0 = (W - total) / 2
    y1 = 38 * _SS  # 윗줄 가운데 높이
    d.polygon(_star_points(x0 + star_r, y1, star_r), fill=accent)
    d.text((x0 + star_r * 2 + gap, y1), line1, font=f1, fill=TEXT_MAIN, anchor="lm")
    d.text((W / 2, 84 * _SS), line2, font=f2, fill=TEXT_SUB, anchor="mm")
    im = im.resize((BOX_W, BOX_H), Image.LANCZOS)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    im.save(out_path, "PNG")
    return out_path
