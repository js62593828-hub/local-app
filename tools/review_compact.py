#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""원본 후기(research/_raw/<상품id>.json)를 AI가 읽기 좋은 짧은 글 파일로 바꾼다 (AI·네트워크 0회).

JSON 모양·중복 후기·반복되는 설문 머리말을 빼고 후기 한 건을 한 줄로 만든다.
원본 JSON 은 고치지 않는다.

사용: python -X utf8 tools/review_compact.py pipeline/candidates_run/<실행폴더> <상품id> [<상품id> ...]
결과: <실행폴더>/research/_raw/<상품id>.reviews.txt
"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MAX_CHARS = 700  # 후기 한 건에서 남기는 글자 수
SURVEY = re.compile(
    r"^상품명:.{0,80}?(?:(?:디자인|사이즈|색상|착용감|무게|크기|소음|성능|사용감|마감|내구성|품질)"
    r"(?:마음에 들어요|아주 마음에 들어요|보통이에요|별로예요|정사이즈예요|생각보다 커요|생각보다 작아요|작아요|커요|적당해요|만족스러워요))+")


def compact(raw):
    seen = set()
    out = ["상품명: %s" % (raw.get("name") or ""),
           "페이지 평점 %s · 리뷰 수 %s · 수집 %s" % (raw.get("rating"), raw.get("review_count"), raw.get("collected_at", ""))]
    if raw.get("ai_summary"):
        s = raw["ai_summary"]
        out.append("페이지 AI 요약: %s" % (" ".join(map(str, s)) if isinstance(s, list) else s))
    total = cut = 0
    for group in (raw.get("reviews") or {}).values():
        rows = []
        for r in group.get("reviews", []):
            text = SURVEY.sub("", (r.get("text") or "").strip())
            text = re.sub(r"\s+", " ", text).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS] + "…(뒤 생략)"
                cut += 1
            rows.append("★%s %s%s %s" % (r.get("rating"), r.get("date", ""),
                                         (" [%s]" % r["tags"]) if r.get("tags") else "", text))
        total += len(rows)
        out.append("")
        out.append("## %s (%d건, 앞 묶음과 같은 후기는 뺌)" % (group.get("sort", ""), len(rows)))
        out += rows
    out.insert(2, "후기 %d건(긴 후기 %d건은 %d자까지)" % (total, cut, MAX_CHARS))
    return "\n".join(out) + "\n", total


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    run = sys.argv[1]
    for pid in sys.argv[2:]:
        src = os.path.join(run, "research", "_raw", pid + ".json")
        if not os.path.isfile(src):
            print(pid, "원본 없음:", src)
            continue
        with open(src, encoding="utf-8") as f:
            text, n = compact(json.load(f))
        dst = os.path.join(run, "research", "_raw", pid + ".reviews.txt")
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        print(pid, "후기 %d건 · %d자 → %d자 · %s" % (n, len(open(src, encoding="utf-8").read()), len(text), dst.replace("\\", "/")))


if __name__ == "__main__":
    main()
