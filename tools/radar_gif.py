"""N데스크피커(radar_naver) 상품 이미지 GIF 만들기 — 시범 도구.

앱이 아직 불러오지 않는 독립 도구입니다. 이미지 API 를 쓰지 않고 Pillow 만 씁니다.

사용 예:
  python tools/radar_gif.py --src <이미지폴더> --frame <프레임.png> --out <출력경로(확장자 없이)> \
         [--pick 01_rep,03,04,02,06] [--max-mb 3]

프레임 PNG 옆에 같은 이름의 .json 이 있으면 읽습니다(없으면 전체 화면 사용):
  {"inner": [x0, y0, x1, y1],              # 이미지를 넣는 안쪽 영역 (800 기준)
   "dots": {"cx": 400, "cy": 770, "r": 6, "gap": 22, "color": "#C9A35B", "off": "#9A9A9A"}}

만드는 것: <out>_dither.gif, <out>_nodither.gif, <out>_first.png, <out>_log.json
규격: 정사각 800x800(흰 여백, 자르지 않음), 장당 1.2초, 무한 반복, 4~5장, 장마다 256색 팔레트 최적화.
3MB 넘으면 장 수(→4장) 먼저, 그다음 크기(720)를 줄이고 log.json 에 기록합니다.
"""
import argparse
import glob
import io
import json
import os
import sys

from PIL import Image, ImageDraw

BASE = 800
DURATION_MS = 1200
MIN_FRAMES = 4
MAX_FRAMES = 5
SHRUNK = 720


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def load_frame_spec(frame_path):
    spec = {"inner": [0, 0, BASE, BASE], "dots": None}
    side = os.path.splitext(frame_path)[0] + ".json"
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            spec.update(json.load(f))
    return spec


def pick_images(src, pick):
    files = sorted(glob.glob(os.path.join(src, "*.jpg")) + glob.glob(os.path.join(src, "*.png"))
                   + glob.glob(os.path.join(src, "*.jpeg")))
    if not pick:
        return files[:MAX_FRAMES]
    out = []
    for key in pick.split(","):
        key = key.strip()
        hit = [f for f in files if os.path.basename(f).startswith(key)]
        if not hit:
            raise SystemExit(f"이미지를 못 찾음: {key}")
        out.append(hit[0])
    return out


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


def build(images, frame_rgba, spec, size, dither, out_path):
    total = len(images)
    frames = [to_palette(compose(p, frame_rgba, spec, size, i, total), dither) for i, p in enumerate(images)]
    # 드라이브 동기화 폴더에서는 저장 직후 파일 크기가 안 맞을 수 있어, 메모리에서 크기를 잰 뒤 쓴다.
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=DURATION_MS,
                   loop=0, optimize=False, disposal=2)
    data = buf.getvalue()
    with open(out_path, "wb") as f:
        f.write(data)
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pick", default="")
    ap.add_argument("--max-mb", type=float, default=3.0)
    a = ap.parse_args()

    images = pick_images(a.src, a.pick)
    if len(images) < MIN_FRAMES:
        raise SystemExit(f"이미지가 {len(images)}장뿐입니다(최소 {MIN_FRAMES}장 필요).")
    images = images[:MAX_FRAMES]
    spec = load_frame_spec(a.frame)
    frame_rgba = Image.open(a.frame).convert("RGBA")
    limit = int(a.max_mb * 1024 * 1024)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)

    # 줄이는 순서: 장 수(5→4) 먼저, 그다음 크기(800→720)
    attempts = [(n, BASE) for n in range(len(images), MIN_FRAMES - 1, -1)] + [(MIN_FRAMES, SHRUNK)]
    log = {"images": [os.path.basename(p) for p in images], "limit_bytes": limit, "variants": {}}
    for name, dither in (("dither", True), ("nodither", False)):
        path = f"{a.out}_{name}.gif"
        tried = []
        for n, size in attempts:
            nbytes = build(images[:n], frame_rgba, spec, size, dither, path)
            tried.append({"frames": n, "size": size, "bytes": nbytes})
            if nbytes <= limit:
                break
        final = tried[-1]
        log["variants"][name] = {"file": os.path.basename(path), "tried": tried, "final": final,
                                 "within_limit": final["bytes"] <= limit,
                                 "reduced": len(tried) > 1}
    # 첫 장 PNG (800 기준, 디더링 전 원본 합성)
    compose(images[0], frame_rgba, spec, BASE, 0, log["variants"]["dither"]["final"]["frames"]).save(f"{a.out}_first.png")
    with open(f"{a.out}_log.json", "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    for name, v in log["variants"].items():
        fin = v["final"]
        print(f"{name}: {fin['frames']}장 {fin['size']}px {fin['bytes'] / 1024 / 1024:.2f}MB"
              f"{' (줄임: ' + str(len(v['tried']) - 1) + '단계)' if v['reduced'] else ''}"
              f"{'' if v['within_limit'] else ' [3MB 초과]'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
