"""
N데스크피커 사진 모듈 시범 실행 (radar_images.py v1)
  - 시범 원고 3편(pipeline/radar_drafts/trial_1116/)만 읽는다(읽기 전용).
  - 결과는 preview/radar_images_v1/ 에만 쓴다. 앱 데이터는 건드리지 않는다.
  - 이미지 생성 AI·네이버·Claude 호출 0회(AI 사진은 프롬프트 글만 저장).
실행:  python tools/radar_images_trial.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

import radar_images as ri  # noqa: E402

TRIAL = os.path.join(ROOT, "pipeline", "radar_drafts", "trial_1116")
WORK_IMG = os.path.join(TRIAL, "_work", "img")
OUT = os.path.join(ROOT, "preview", "radar_images_v1")

DATE_NOTE = "작성일 2026년 11월 15일 기준 · 가격은 변동 가능"


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def title_of(d):
    for ln in read(os.path.join(TRIAL, d, "title.txt")).split("\n"):
        if ln.startswith("제목:"):
            return ln[3:].strip()
    return d


# 표지에 들어갈 글(범주·제목·부제) — 제목·부제는 원고에 있는 말만
POSTS = {
    "A_발난로_고르는법": {
        "frame": "A",
        "category": "난방",
        "title": "발난로 고르는 법",
        "subtitle": "책상 밑에 둘 때 볼 기준",
        "cover_photo": os.path.join(TRIAL, "A_발난로_고르는법", "img1.jpg"),
    },
    "B_리웨이355": {
        "frame": "B",
        "category": "난방",
        "title": "리웨이 355 파티션히터",
        "subtitle": "무릎 아래를 둘러싸는",
        "cover_photo": os.path.join(TRIAL, "B_리웨이355", "img1.jpg"),
    },
    "C_브라운BC02XW": {
        "frame": "B",
        "category": "책상 소품",
        "title": "브라운 BC02XW 알람시계",
        "subtitle": "책상 위에 둘 작고 조용한",
        "cover_photo": os.path.join(WORK_IMG, "c_bc02xw", "m00.jpg"),  # 흰 배경 제품 사진
    },
}


def body_plan(d, text, tables):
    """글마다 본문 사진 5장 구성. 반환: 사진 목록(번호 순)."""
    if d.startswith("A_"):
        return [
            {"type": "ai", "heading": "발이 시려운 책상, 어떤 히터가 맞을까", "method": "AI",
             "scene": "A pair of feet in warm socks under a wooden office desk on a cold winter morning, low angle view at floor level, empty floor space under the desk, a cardigan hanging on the chair."},
            {"type": "card", "heading": "고르기 전에 볼 기준 네 가지", "method": "앱", "kind": "기준 카드",
             "title": "고르기 전에 볼 기준 네 가지", "kicker": "고르는 기준",
             "sections": [("", [
                 ("종류", "발판형, 다리 감싸는 형, 소형 히터로 나뉘어요"),
                 ("소비전력", "같은 시간을 켰을 때 숫자가 클수록 전기를 더 써요"),
                 ("크기와 놓는 자리", "책상 밑 깊이와 발 놓을 자리를 먼저 재 보세요"),
                 ("안전 표시와 타이머", "켜 두고 잊기 쉬워서 타이머도 볼 만해요"),
             ])]},
            {"type": "table", "heading": "다섯 제품을 종류별로 나란히 놓으면", "method": "앱", "kind": "비교표",
             "rows": tables[0], "title": "다섯 제품을 종류별로 나란히 놓으면", "kicker": "비교표", "footnote": DATE_NOTE},
            {"type": "product", "heading": "다섯 제품을 종류별로 나란히 놓으면", "method": "앱", "kind": "① 발판형 온아워",
             "src": os.path.join(WORK_IMG, "onhour", "m00.jpg")},
            {"type": "product", "heading": "자리와 상황에 따라 고른다면", "method": "앱", "kind": "③ 소형 히터 마카롱",
             "src": os.path.join(WORK_IMG, "macaron", "m02.jpg")},
        ]
    if d.startswith("B_"):
        return [
            {"type": "ai", "heading": "먼저 결론: 책상 밑에서 다리 전체를 감싸고 싶은 분께", "method": "AI",
             "scene": "Knee-level view under a tidy office desk in soft winter afternoon light, a chair pushed in, a folded blanket on the seat, empty floor space under the desk."},
            {"type": "table", "heading": "기본 정보 한눈에", "method": "앱", "kind": "스펙표",
             "rows": tables[0], "title": "기본 정보 한눈에", "kicker": "스펙표", "footnote": DATE_NOTE},
            {"type": "capture", "heading": "기본 정보 한눈에", "method": "캡처",
             "note": "상품 상세 페이지의 크기·온도·타이머 안내 부분"},
            {"type": "product", "heading": "구매자 후기에서 자주 나온 좋은 점", "method": "앱", "kind": "접어 둔 모습",
             "src": os.path.join(TRIAL, "B_리웨이355", "img2.jpg")},
            {"type": "card", "heading": "아쉽다는 말이 나온 부분", "method": "앱", "kind": "장단점 요약 카드",
             "title": "후기에서 나온 말", "kicker": "구매자 후기 50개", "footnote": "최근 후기 50개 기준 · 건수는 대략",
             "sections": [
                 ("좋다는 말", [("따뜻하다는 말 27건", ""), ("타이머를 언급한 글 9건", ""),
                              ("책상 밑·사무실에서 쓰기 좋다는 말 7건", ""), ("은은해서 부담 없다는 말 6건", "")]),
                 ("아쉽다는 말", [("냄새가 났다는 말 4건", "이 중 3건은 시간이 지나면 줄었다고 했어요"),
                               ("높이·폭·접히는 부분·열선 아쉬움 합쳐 4건", ""),
                               ("약 단계는 덜 따뜻하다는 말 1건", "")]),
             ]},
        ]
    return [
        {"type": "product", "heading": "누구에게 맞는 시계일까", "method": "앱", "kind": "책상 위 모습",
         "src": os.path.join(TRIAL, "C_브라운BC02XW", "img1.jpg")},
        {"type": "table", "heading": "기본 정보와 가격", "method": "앱", "kind": "스펙표",
         "rows": tables[0], "title": "기본 정보와 가격", "kicker": "스펙표", "footnote": DATE_NOTE},
        {"type": "capture", "heading": "기본 정보와 가격", "method": "캡처",
         "note": "상품 상세 페이지의 크기·기능 안내 부분"},
        {"type": "capture", "heading": "구매자들이 반복해서 좋다고 한 점", "method": "캡처",
         "note": "구매자 후기의 사이즈 항목(작아요·정사이즈예요·생각보다 커요) 부분"},
        {"type": "card", "heading": "아쉬움은 이런 부분이에요", "method": "앱", "kind": "장단점 요약 카드",
         "title": "후기에서 나온 말", "kicker": "구매자 후기 50개", "footnote": "최근 후기 50개 기준 · 건수는 대략",
         "sections": [
             ("좋다는 말", [("귀엽다는 말 27건", ""), ("디자인이 깔끔하다는 말 6건", ""),
                          ("소리가 조용하다는 말 5건", ""), ("알람 소리가 잘 들린다는 말 5건", "")]),
             ("아쉽다는 말", [("생각보다 작다고 느낀 글 9건", "불만보다 놀람에 가까웠고 별점은 4점이나 5점이었어요"),
                           ("가격이 비싼 편이라는 말 2건", ""),
                           ("소리가 완전히 없지는 않다는 말 1건", "")]),
         ]},
    ]


def main():
    os.makedirs(OUT, exist_ok=True)
    result = {"posts": {}, "ai_total": 0}
    cover_files = {}
    body_files = {}
    for d, spec in POSTS.items():
        text = read(os.path.join(TRIAL, d, "post.txt"))
        tables = ri.parse_md_tables(text)
        folder = os.path.join(OUT, d)
        os.makedirs(folder, exist_ok=True)
        n_target = ri.body_photo_count(text)
        info = {"chars_no_space": ri.body_chars(text), "n_target": n_target, "frame": spec["frame"], "covers": {}, "photos": []}
        # 표지 3안
        cover_files[d] = {}
        for v in "ABC":
            p = os.path.join(folder, f"cover_{v}.jpg")
            meta = ri.make_cover(spec["cover_photo"], p, spec["category"], spec["title"], spec["subtitle"], v)
            info["covers"][v] = meta
            cover_files[d][v] = p
        # 본문 사진
        plan = body_plan(d, text, tables)
        body_files[d] = []
        headings = ri.post_headings(text)
        for i, ph in enumerate(plan, 1):
            ph["no"] = i
            if ph["heading"] not in headings:
                raise SystemExit(f"{d}: 원고에 없는 소제목 '{ph['heading']}'")
            base = f"body_{i:02d}_{ph['type']}"
            entry = {"no": i, "type": ph["type"], "type_ko": ri.TYPE_KO[ph["type"]], "heading": ph["heading"], "method": ph["method"]}
            if ph["type"] == "product":
                f = base + ".jpg"
                entry["info"] = ri.product_photo(ph["src"], os.path.join(folder, f))
                entry["file"], entry["detail"] = f, ph["kind"]
            elif ph["type"] == "table":
                f = base + ".jpg"
                entry["info"] = ri.table_image(ph["rows"], os.path.join(folder, f), ph["title"], ph["kicker"], ph["footnote"])
                entry["file"], entry["detail"] = f, ph["kind"]
            elif ph["type"] == "card":
                f = base + ".jpg"
                entry["info"] = ri.card_image(os.path.join(folder, f), ph["title"], ph["sections"], ph["kicker"], ph.get("footnote", ""))
                entry["file"], entry["detail"] = f, ph["kind"]
            elif ph["type"] == "capture":
                f = base + ".jpg"
                cap_no = sum(1 for e in info["photos"] if e["type"] == "capture") + 1
                entry["info"] = ri.capture_placeholder(os.path.join(folder, f), cap_no, ph["note"])
                entry["file"], entry["detail"] = f, "캡처 자리(빈 자리 표시)"
            elif ph["type"] == "ai":
                entry["file"] = None
                entry["detail"] = "AI 장면(프롬프트만, 호출 안 함)"
                entry["ai"] = ri.ai_prompt(ph["scene"])
            info["photos"].append(entry)
            body_files[d].append((entry, os.path.join(folder, entry["file"]) if entry["file"] else None))
        problems, counts = ri.validate_plan(spec["frame"], info["photos"], n_target)
        info["counts"], info["problems"] = counts, problems
        ai_n = counts.get("ai", 0)
        info["ai_count"], info["ai_cost_krw"] = ai_n, ai_n * ri.AI_COST_KRW
        result["ai_total"] += ai_n
        with open(os.path.join(folder, "layout.json"), "w", encoding="utf-8") as f:
            json.dump({"post": d, "title": title_of(d), "frame": spec["frame"], "body_photo_target": n_target,
                       "cover": {"category": spec["category"], "title": spec["title"], "subtitle": spec["subtitle"]},
                       "photos": [{k: v for k, v in e.items() if k != "info"} for e in info["photos"]]},
                      f, ensure_ascii=False, indent=1)
        ai_list = [{"no": e["no"], "heading": e["heading"], **e["ai"]} for e in info["photos"] if e["type"] == "ai"]
        with open(os.path.join(folder, "ai_prompts.json"), "w", encoding="utf-8") as f:
            json.dump(ai_list, f, ensure_ascii=False, indent=1)
        result["posts"][d] = info

    make_sheets(cover_files, body_files)
    with open(os.path.join(OUT, "_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({d: {"chars": i["chars_no_space"], "target": i["n_target"], "counts": i["counts"],
                            "problems": i["problems"], "ai": i["ai_count"]} for d, i in result["posts"].items()}, ensure_ascii=False, indent=1))


def make_sheets(cover_files, body_files):
    names = list(cover_files)
    F, FB = ri.font(False, 26), ri.font(True, 30)
    # 표지 시안 비교 3x3
    T, gap, left, top = 520, 20, 150, 70
    W = left + 3 * (T + gap) + gap
    H = top + len(names) * (T + gap) + gap
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for ci, v in enumerate("ABC"):
        d.text((left + gap + ci * (T + gap), 36), f"시안 {v} · {ri.THEMES[v]['name']}", font=FB, fill=(30, 30, 30), anchor="ls")
    for ri_, n in enumerate(names):
        y = top + gap + ri_ * (T + gap)
        d.text((12, y + T // 2), n[0] + "편", font=FB, fill=(30, 30, 30), anchor="lm")
        for ci, v in enumerate("ABC"):
            sheet.paste(Image.open(cover_files[n][v]).resize((T, T), Image.LANCZOS), (left + gap + ci * (T + gap), y))
    sheet.save(os.path.join(OUT, "cover_compare.jpg"), quality=92)

    # 전체 모음(시안 A) : 글마다 한 줄 = 표지 + 본문 5장
    t, g = 250, 14
    cols = 6
    sheet = Image.new("RGB", (cols * (t + g) + g, len(names) * (t + g + 64) + g), (240, 238, 234))
    d = ImageDraw.Draw(sheet)
    for r, n in enumerate(names):
        y = g + r * (t + g + 64)
        d.text((g, y + 22), f"{n[0]}편 · {title_of(n)}", font=F, fill=(30, 30, 30), anchor="ls")
        tiles = [("표지(시안 A)", cover_files[n]["A"])] + [(f"{e['no']} {e['type_ko']}", p) for e, p in body_files[n]]
        for c, (label, p) in enumerate(tiles):
            x = g + c * (t + g)
            if p:
                im = Image.open(p).convert("RGB")
                im.thumbnail((t, t), Image.LANCZOS)
                sheet.paste(im, (x + (t - im.width) // 2, y + 34 + (t - im.height) // 2))
            else:
                d.rectangle([x, y + 34, x + t, y + 34 + t], fill=(225, 220, 212))
                d.text((x + t // 2, y + 34 + t // 2), "AI 장면\n(프롬프트만)", font=F, fill=(90, 90, 90), anchor="mm", align="center")
            d.text((x + 4, y + 34 + t + 24), label, font=ri.font(False, 22), fill=(60, 60, 60), anchor="ls")
    sheet.save(os.path.join(OUT, "contact_sheet.jpg"), quality=90)

    # 네이버 글 목록처럼 2열 미리보기(표지 시안 A + 제목)
    cw, gx, gy, m = 504, 24, 30, 24
    rows = (len(names) + 1) // 2
    tf = ri.font(True, 29)
    cell_h = cw + 24 + 2 * 40 + 34
    sheet = Image.new("RGB", (2 * cw + gx + 2 * m, m + rows * (cell_h + gy)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for i, n in enumerate(names):
        x = m + (i % 2) * (cw + gx)
        y = m + (i // 2) * (cell_h + gy)
        sheet.paste(Image.open(cover_files[n]["A"]).resize((cw, cw), Image.LANCZOS), (x, y))
        yy = y + cw + 40
        for ln in ri.wrap(title_of(n), tf, cw)[:2]:
            d.text((x, yy), ln, font=tf, fill=(34, 38, 43), anchor="ls")
            yy += 40
        d.text((x, yy + 6), "데스크피커 · 2026.11.15", font=ri.font(False, 22), fill=(140, 140, 140), anchor="ls")
    sheet.save(os.path.join(OUT, "feed_preview.jpg"), quality=92)


if __name__ == "__main__":
    main()
