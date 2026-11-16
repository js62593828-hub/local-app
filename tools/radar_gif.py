"""N데스크피커(radar_naver) 상품 이미지 GIF 만들기 (확정판: 기본 프레임 F2 에디토리얼 카드).

앱이 아직 불러오지 않는 독립 도구입니다. 이미지 생성 API 를 쓰지 않고 Pillow 만 씁니다.

사용 예:
  python -I tools/radar_gif.py --src research/_img/<상품id> --out research/_img/<상품id>/<상품id> \
         --genre "모니터·노트북" --name "카멜마운트 SMB35"

  --src      이미지 폴더. 안에 images.json 이 있으면 그 기록으로 장을 고른다(없으면 파일 이름 순서).
  --out      출력 경로(확장자 없이). 만드는 것: <out>.gif, <out>_first.png, <out>_gif_log.json
  --genre    위 줄 오른쪽에 쓰는 갈래 이름. 값이 없으면 그 칸을 비운다(지어내지 않음).
  --name     아래 줄에 쓰는 짧은 이름(14자 안). 값이 없으면 그 칸을 비운다.
  --frame    F2(기본) | F1 | F3 | 프레임 PNG 경로(옆에 같은 이름 .json 이 있으면 읽음)
  --no-dither  디더링 끔(기본은 켬)
  --pick     장을 직접 지정(예: 01_rep,03,04,02,06). 지정하면 자동 고르기를 건너뜀

규격: 정사각 800x800(흰 여백, 자르지 않음), 장당 1.2초, 무한 반복, 4~5장(대표 이미지가 첫 장).
3MB 넘으면 장 수(→4장) 먼저, 그다음 크기(720)를 줄이고 log 에 기록한다.

고르는 기준: images.json 에서 '확인 필요'(제품이 일부만 보이거나 작음)와 '글자 많음' 이미지는 뺀다.
남은 것이 4장보다 적거나 대표 이미지가 빠지면 GIF 를 만들지 않고 log 에 "GIF 없음(...)" 으로 기록한다.
"""
import argparse
import glob
import io
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(ROOT, "fonts", "Pretendard-Medium.otf")
BASE = 800
DURATION_MS = 1200
MIN_FRAMES = 4
MAX_FRAMES = 5
SHRUNK = 720
ACC = (201, 163, 91, 255)   # #C9A35B 강조색
DARK = (34, 34, 34, 255)
DOT_ON, DOT_OFF = "#C9A35B", "#8A8A8A"
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _font(px):
    return ImageFont.truetype(FONT_PATH, px)


def _fit_text(draw, text, max_w, start_px, min_px=16):
    """글자가 칸 너비를 넘으면 글자 크기를 줄여 맞춘다."""
    px = start_px
    while px > min_px and draw.textlength(text, font=_font(px)) > max_w:
        px -= 1
    return _font(px)


# ---------- 프레임 ----------
def frame_f2(genre, name, total):
    """F2 에디토리얼 카드: 위 띠(왼쪽 Desk Picker, 오른쪽 갈래 이름) + 아래 띠(왼쪽 짧은 이름, 오른쪽 장 번호 점)."""
    im = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, BASE, 48], fill=DARK)
    d.rectangle([0, 48, BASE, 51], fill=ACC)
    d.rectangle([0, 752, BASE, BASE], fill=DARK)
    d.rectangle([0, 749, BASE, 752], fill=ACC)
    d.text((28, 25), "Desk Picker", font=_font(24), fill=ACC, anchor="lm")
    if genre:
        d.text((BASE - 28, 25), genre, font=_fit_text(d, genre, 380, 22), fill=(235, 235, 235, 255), anchor="rm")
    r, gap = 7, 26
    dots_w = gap * (total - 1) + 2 * r
    if name:
        d.text((28, 776), name, font=_fit_text(d, name, BASE - 56 - dots_w - 30, 24), fill=(255, 255, 255, 255), anchor="lm")
    spec = {"inner": [0, 56, 800, 744],
            "dots": {"cx": BASE - 28 - r - gap * (total - 1) / 2, "cy": 776, "r": r, "gap": gap, "color": DOT_ON, "off": DOT_OFF}}
    return im, spec


def frame_f1(genre, name, total):
    """F1(옵션): 얇은 테두리 + 아래 띠."""
    im = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 744, BASE, BASE], fill=DARK)
    d.rectangle([0, 0, BASE - 1, BASE - 1], outline=ACC, width=4)
    d.text((28, 772), "Desk Picker", font=_font(24), fill=ACC, anchor="lm")
    return im, {"inner": [12, 12, 788, 732], "dots": {"cx": 700, "cy": 772, "r": 7, "gap": 24, "color": DOT_ON, "off": DOT_OFF}}


def frame_f3(genre, name, total):
    """F3(옵션): 모서리 장식만."""
    im = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m, L, w = 14, 70, 5
    for (cx, cy, sx, sy) in ((m, m, 1, 1), (BASE - m, m, -1, 1), (m, BASE - m, 1, -1), (BASE - m, BASE - m, -1, -1)):
        d.line([(cx, cy), (cx + sx * L, cy)], fill=ACC, width=w)
        d.line([(cx, cy), (cx, cy + sy * L)], fill=ACC, width=w)
    d.text((90, 778), "Desk Picker", font=_font(22), fill=ACC, anchor="lm")
    return im, {"inner": [30, 30, 770, 752], "dots": {"cx": 640, "cy": 778, "r": 6, "gap": 22, "color": DOT_ON, "off": "#AAAAAA"}}


def make_frame(kind, genre, name, total):
    k = (kind or "F2").upper()
    if k == "F2":
        return frame_f2(genre, name, total)
    if k == "F1":
        return frame_f1(genre, name, total)
    if k == "F3":
        return frame_f3(genre, name, total)
    if os.path.exists(kind):  # 프레임 PNG 파일
        spec = {"inner": [0, 0, BASE, BASE], "dots": None}
        side = os.path.splitext(kind)[0] + ".json"
        if os.path.exists(side):
            with open(side, encoding="utf-8") as f:
                spec.update(json.load(f))
        return Image.open(kind).convert("RGBA"), spec
    raise SystemExit(f"프레임을 알 수 없음: {kind} (F2·F1·F3 또는 PNG 경로)")


# ---------- 장 고르기 ----------
def list_images(src):
    return sorted(f for f in glob.glob(os.path.join(src, "*")) if f.lower().endswith(IMG_EXT))


def select_images(src, pick):
    """(고른 파일 목록, 뺀 이유 {파일: 이유}, 중단 사유 또는 None)"""
    files = list_images(src)
    if pick:
        out = []
        for key in pick.split(","):
            hit = [f for f in files if os.path.basename(f).startswith(key.strip())]
            if not hit:
                raise SystemExit(f"이미지를 못 찾음: {key}")
            out.append(hit[0])
        return out, {}, None
    meta_path = os.path.join(src, "images.json")
    if not os.path.exists(meta_path):
        return files[:MAX_FRAMES], {}, None
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    excluded, ok = {}, []
    for m in meta.get("images", []):
        path = os.path.join(src, m["file"])
        if not os.path.exists(path):
            continue
        if m.get("확인 필요"):
            excluded[m["file"]] = "확인 필요(" + (m.get("확인 필요 이유") or "제품이 일부만 보이거나 작음") + ")"
        elif m.get("글자 많음"):
            excluded[m["file"]] = "글자 많음"
        else:
            ok.append(m)
    rep = next((m for m in ok if "rep" in m["file"]), None)
    if rep is None:
        return [], excluded, "GIF 없음(대표 이미지 부적합 또는 없음)"
    rest = [m for m in ok if m is not rep]
    rest.sort(key=lambda m: (bool(m.get("순서 확인 필요")), m["file"]))  # 순서가 흐린 이미지는 뒤로
    chosen = [rep] + rest
    if len(chosen) < MIN_FRAMES:
        return [], excluded, f"GIF 없음(이미지 부족: 쓸 수 있는 것 {len(chosen)}장 < {MIN_FRAMES}장)"
    return [os.path.join(src, m["file"]) for m in chosen[:MAX_FRAMES]], excluded, None


# ---------- 그리기 ----------
def compose(img_path, frame_rgba, spec, size, idx, total):
    k = size / BASE
    inner = [round(v * k) for v in spec["inner"]]
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    iw, ih = inner[2] - inner[0], inner[3] - inner[1]
    im = Image.open(img_path).convert("RGB")
    scale = min(iw / im.width, ih / im.height)  # 자르지 않고 안쪽 영역에 맞춤
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    canvas.paste(im, (inner[0] + (iw - nw) // 2, inner[1] + (ih - nh) // 2))
    fr = frame_rgba if frame_rgba.width == size else frame_rgba.resize((size, size), Image.LANCZOS)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), fr)
    d = spec.get("dots")
    if d:  # 장 번호 점: 지금 장은 채움, 나머지는 속이 빈 동그라미 (4배로 그려 부드럽게)
        ss = 4
        layer = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        r, gap = d["r"] * k * ss, d["gap"] * k * ss
        x0 = d["cx"] * k * ss - gap * (total - 1) / 2
        cy = d["cy"] * k * ss
        on, off = _hex(d["color"]), _hex(d["off"])
        for i in range(total):
            cx = x0 + gap * i
            box = [cx - r, cy - r, cx + r, cy + r]
            if i == idx:
                dr.ellipse(box, fill=on + (255,))
            else:
                dr.ellipse(box, outline=off + (255,), width=max(2, round(1.6 * k * ss)))
        layer = layer.resize((size, size), Image.LANCZOS)
        canvas = Image.alpha_composite(canvas, layer)
    return canvas.convert("RGB")


def to_palette(rgb, dither):
    """장마다 그 장에 맞는 256색 팔레트를 새로 만든다. 디더링은 그 팔레트로 다시 칠할 때 적용."""
    base = rgb.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    if not dither:
        return base
    return rgb.quantize(palette=base, dither=Image.Dither.FLOYDSTEINBERG)


def build(images, frame_kind, genre, name, size, dither, out_path):
    total = len(images)
    frame_rgba, spec = make_frame(frame_kind, genre, name, total)
    frames = [to_palette(compose(p, frame_rgba, spec, size, i, total), dither) for i, p in enumerate(images)]
    # 드라이브 동기화 폴더에서는 저장 직후 파일 크기가 안 맞을 수 있어, 메모리에서 크기를 잰 뒤 쓴다.
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=DURATION_MS,
                   loop=0, optimize=False, disposal=2)
    data = buf.getvalue()
    with open(out_path, "wb") as f:
        f.write(data)
    return len(data)


def make_gif(src, out, frame="F2", genre="", name="", pick="", max_mb=3.0, dither=True):
    """GIF 1개를 만들고 log(dict)를 돌려준다. 못 만들면 log['status'] 가 'GIF 없음(...)'."""
    images, excluded, stop = select_images(src, pick)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    log = {"src": src, "frame": frame, "genre": genre, "name": name, "dither": dither,
           "excluded": excluded, "limit_bytes": int(max_mb * 1024 * 1024)}
    if stop or len(images) < MIN_FRAMES:
        log["status"] = stop or f"GIF 없음(이미지 부족: {len(images)}장)"
        _write_log(out, log)
        return log
    images = images[:MAX_FRAMES]
    limit = log["limit_bytes"]
    # 줄이는 순서: 장 수(5→4) 먼저, 그다음 크기(800→720)
    attempts = [(n, BASE) for n in range(len(images), MIN_FRAMES - 1, -1)] + [(MIN_FRAMES, SHRUNK)]
    tried = []
    path = out + ".gif"
    for n, size in attempts:
        nbytes = build(images[:n], frame, genre, name, size, dither, path)
        tried.append({"frames": n, "size": size, "bytes": nbytes})
        if nbytes <= limit:
            break
    final = tried[-1]
    used = images[:final["frames"]]
    frame_rgba, spec = make_frame(frame, genre, name, final["frames"])
    compose(used[0], frame_rgba, spec, BASE, 0, final["frames"]).save(out + "_first.png")
    log.update({"status": "만듦", "images": [os.path.basename(p) for p in used], "tried": tried, "final": final,
                "within_limit": final["bytes"] <= limit, "reduced": len(tried) > 1, "gif": os.path.basename(path)})
    _write_log(out, log)
    return log


def _write_log(out, log):
    with open(out + "_gif_log.json", "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frame", default="F2")
    ap.add_argument("--genre", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--pick", default="")
    ap.add_argument("--max-mb", type=float, default=3.0)
    ap.add_argument("--no-dither", action="store_true")
    a = ap.parse_args()
    log = make_gif(a.src, a.out, a.frame, a.genre, a.name, a.pick, a.max_mb, not a.no_dither)
    if log["status"] != "만듦":
        print(log["status"])
        return
    fin = log["final"]
    print(f"{log['gif']}: {fin['frames']}장 {fin['size']}px {fin['bytes'] / 1024 / 1024:.2f}MB"
          f"{' (줄임: ' + str(len(log['tried']) - 1) + '단계)' if log['reduced'] else ''}"
          f"{'' if log['within_limit'] else ' [3MB 초과]'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
