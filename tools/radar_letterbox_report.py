"""
N데스크피커 본문 사진 레터박스(빈 띠) 점검 — 읽기 전용 + 시안 저장.
사용: python -X utf8 tools/radar_letterbox_report.py 2026-11-23 [--preview preview/radar_letterbox_20261120]

- 그 주 원고(대기열 id 가 주 시작일로 시작하는 글)의 사진 칸(photos.json slots)마다 종류·띠·처리를 표로 만든다.
- 처리: 확대 채우기 / 그대로 / 많이 잘림 / 제외(종류) — 기준 숫자는 radar_screen.py 의 LB_* 한 곳.
- --preview 를 주면 '확대 채우기' 사진 중 최대 6장을 전_/후_ 로 저장한다(업로드 폴더·앱 데이터는 안 건드림).
- GIF 는 보기만(프레임 띠 확인).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageOps  # noqa: E402

import config  # noqa: E402
import image_gen  # noqa: E402
import radar_screen as rs  # noqa: E402

TARGET = ("제품",)  # + 'AI' 로 시작하는 종류
EXCLUDE_NOTE = {"캡처": "제외(캡처)", "표": "제외(표 그림)", "카드": "제외(카드 그림)"}


def is_target(kind):
    return kind == "제품" or kind.startswith("AI")


def app_band(size):
    w, h = size
    return round((1 - min(w, h) / max(w, h)) * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("week")
    ap.add_argument("--preview", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    q = json.load(open("content_queue_radar_naver.json", encoding="utf-8"))
    arts = json.load(open("articles_radar_naver.json", encoding="utf-8"))
    rows, shots, gifs = [], [], []
    for it in q:
        if not it["id"].startswith(a.week) or it["id"] not in arts:
            continue
        art = arts[it["id"]]
        title = next((t for t in art["platform_title"].values() if t), None) or it["keyword"]
        pdir = rs.photo_dir("생활레이더", image_gen.slugify_for_folder(title))
        st = rs.load_state(pdir)
        hints = rs.slot_hints((art.get("platform_metadata") or {}).get("naver") or "")
        tag = it["keyword"]
        for k, f in sorted(st["slots"].items(), key=lambda x: int(x[0])):
            p = os.path.join(pdir, f)
            if not os.path.isfile(p):
                rows.append((tag, k, "?", "파일 없음", "-", "-"))
                continue
            kind = (hints.get(int(k)) or {}).get("kind", "?")
            with Image.open(p) as im0:
                im = rs._rgb_on_white(ImageOps.exif_transpose(im0))
            ab = app_band(im.size)
            plan = rs.letterbox_plan(im)
            src_b = plan["bands"]
            sb = "/".join(str(x) for x in src_b)
            if not is_target(kind):
                proc = EXCLUDE_NOTE.get(kind, f"제외({kind})")
            else:
                proc = plan["decision"]
            band_txt = []
            if ab >= 2:
                band_txt.append(f"앱이 채운 띠 {ab}%")
            if any(src_b):
                band_txt.append(f"원본 안 띠 {sb}px")
            has = " + ".join(band_txt) if band_txt else "없음"
            if is_target(kind) and plan["decision"] == "그대로" and plan["reason"].startswith("가장자리가 단색"):
                has += " (단색 배경)" if band_txt else " (단색 배경)"
            rows.append((tag, k, kind, has, proc, plan["reason"]))
            if is_target(kind) and plan["decision"] == "확대 채우기" and (ab >= 2 or any(src_b)):
                shots.append((it["id"], tag, k, f, p, plan))
        # GIF 보기만
        up = os.path.join(pdir, "naver_upload")
        if os.path.isdir(up):
            for n in sorted(os.listdir(up)):
                if n.lower().endswith(".gif"):
                    with Image.open(os.path.join(up, n)) as g:
                        fr = rs._rgb_on_white(g.convert("RGBA"))
                        inner = fr.crop((30, 30, fr.width - 30, fr.height - 30))
                        gifs.append((tag, n, g.size, rs._band_sizes(inner)))
    out = ["| 글 | 사진 번호 | 종류 | 띠 있음 | 처리 | 이유 |", "|---|---|---|---|---|---|"]
    out += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |" for r in rows]
    out += ["", "GIF 프레임(첫 장, 안쪽 카드 영역에서 같은 색 띠 두께 위/아래/왼/오른 px):"]
    out += [f"- {t} / {n} {s}: {'/'.join(map(str, b))}" for t, n, s, b in gifs]
    text = "\n".join(out)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text + "\n")
    else:
        print(text)
    if a.preview:
        os.makedirs(a.preview, exist_ok=True)
        cfg = rs._frame_cfg()
        size = rs._upload_size()
        done = 0
        used = set()
        for aid, tag, k, f, p, plan in shots:
            if done >= 6:
                break
            key = tag
            if key in used and len(shots) > 6 and done < 4:
                continue  # 글이 겹치지 않게 먼저 고르기
            used.add(key)
            base = f"{aid[-8:]}_{k}_{os.path.splitext(f)[0]}"
            for pre, fill in (("전", False), ("후", True)):
                with Image.open(p) as im0:
                    out_im = rs._frame_image(ImageOps.exif_transpose(im0), size, cfg, fill)
                out_im.save(os.path.join(a.preview, f"{pre}_{base}.jpg"), quality=90)
            done += 1
        print(f"\n시안 {done}장 저장: {a.preview}", file=sys.stderr)


if __name__ == "__main__":
    main()
