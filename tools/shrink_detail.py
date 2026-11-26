"""상품 상세 이미지(research/_detail)를 작게 만들거나, 다 쓴 뒤 비우는 도구. Pillow 만 사용, 비용 0.

  python tools/shrink_detail.py <실행폴더>           # 몇 MB 줄어드는지 보기만(바꾸지 않음)
  python tools/shrink_detail.py <실행폴더> --go      # 받은 직후: 원본을 가로 1000px 이하 JPG 로, 움직이는 GIF 는 첫 장면 JPG 로
  python tools/shrink_detail.py <실행폴더> --clean --go   # 사양 읽기·원고·캡처가 모두 끝난 뒤: 상세 이미지와 잘라 둔 조각 삭제

실행폴더 예: pipeline/candidates_run/20261130_radar_naver_plan
글자 파일(detail_text.txt, options.txt, list.json)은 남깁니다. 지운 원본은 list.json 의 원본주소로 다시 받을 수 있습니다.
"""
import argparse
import io
import os

from PIL import Image

MAX_W = 1000
QUALITY = 82
IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def images(det):
    for dp, _, fn in os.walk(det):
        for f in fn:
            if f.lower().endswith(IMG_EXT):
                yield os.path.join(dp, f)


def shrunk_bytes(path):
    """줄인 JPG 내용. 줄일 필요가 없으면(작아지지 않으면) None."""
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as im:
        im.seek(0)
        rgba = im.convert("RGBA")
    out = Image.new("RGB", rgba.size, (255, 255, 255))
    out.paste(rgba, mask=rgba.getchannel("A"))
    if out.width > MAX_W:
        out = out.resize((MAX_W, max(1, round(out.height * MAX_W / out.width))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=QUALITY, optimize=True, progressive=True)
    # 용량이 20% 넘게 줄 때만 바꾼다(다시 돌려도 같은 파일을 계속 다시 저장하지 않게)
    return buf.getvalue() if buf.tell() < os.path.getsize(path) * 0.8 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    det = os.path.join(a.run, "research", "_detail")
    if not os.path.isdir(det):
        raise SystemExit("멈춤: 상세 이미지 폴더가 없습니다: " + det)
    before = after = changed = 0
    for p in list(images(det)):
        size = os.path.getsize(p)
        before += size
        if a.clean:
            changed += 1
            if a.go:
                os.remove(p)
            continue
        try:
            data = shrunk_bytes(p)
        except Exception as e:  # noqa: BLE001 — 못 읽는 파일은 그대로 두고 알린다
            print("못 읽어 그대로 둠:", p, e)
            after += size
            continue
        dst = os.path.splitext(p)[0] + ".jpg"
        # 같은 번호의 다른 형식(.jpg)이 이미 있으면 덮어쓰지 않는다
        if data is None or (os.path.normcase(dst) != os.path.normcase(p) and os.path.exists(dst)):
            after += size
            continue
        changed += 1
        after += len(data)
        if a.go:
            with open(dst, "wb") as f:
                f.write(data)
            if os.path.normcase(dst) != os.path.normcase(p):
                os.remove(p)
    word = "삭제" if a.clean else "줄임"
    print(f"{word} {changed}개: {before / 1048576:.1f}MB → {after / 1048576:.1f}MB" + ("" if a.go else " (미리보기, --go 를 붙여야 실제로 바뀜)"))


if __name__ == "__main__":
    main()
