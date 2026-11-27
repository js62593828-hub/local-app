#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""다른 원고를 통째로 읽지 않고 겹침을 확인하는 요약 도구 (읽기만, AI·네트워크 0회).

살림노트·혜택주머니 원고 파일(.md)에서 제목, 도입·뼈대 메모, 소제목, 표지 글자·장면,
본문 사진 장면, 경험 문장만 뽑아 글 하나당 몇 줄로 보여 준다.

사용:
  python -X utf8 tools/draft_digest.py pipeline/salim_drafts/2026-11-30
  python -X utf8 tools/draft_digest.py 원고1.md 원고2.md --lead     # --lead: 소제목마다 첫 문장도(같은 묶음 대표글용)
  python -X utf8 tools/draft_digest.py 폴더 --scenes                # 표지·본문 사진 장면도(겹침 확인)
  python -X utf8 tools/draft_digest.py 폴더 --skip 19_             # 이름에 그 글자가 든 파일은 뺌(내 글)
"""
import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def cut(s, n):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n] + "…"


def digest(path, lead, scene):
    text = open(path, encoding="utf-8").read()
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S)
    body = next((b for b in blocks if "[플랫폼:" in b), blocks[0] if blocks else "")
    out = ["= " + os.path.basename(path)]
    m = re.search(r"^제목:\s*(.+)$", body, flags=re.M)
    if m:
        out.append("제목: " + m.group(1).strip())
    memo = text.split("```", 1)[0]
    for ln in memo.split("\n"):
        # 도입 방식·뼈대·요약 이름이 적힌 메모 한 줄(고른 이유 줄은 뺌)
        if re.match(r"\s*-\s*(도입|의도 확인)", ln) and "뼈대" in ln:
            out.append("뼈대: " + cut(ln.lstrip("- "), 120))
            break
    lines = body.split("\n")
    heads = []
    for i, ln in enumerate(lines):
        if not ln.startswith("## "):
            continue
        h = ln[3:].strip()
        if "자주 묻는 질문" in h:
            continue
        if lead:
            first = next((x for x in lines[i + 1:] if x.strip() and not x.startswith(("#", "|", ">", "링크:"))), "")
            first = re.split(r"(?<=[.?!])\s", first.strip())[0]
            heads.append("  - %s :: %s" % (h, cut(first, 90)))
        else:
            heads.append(h)
    if lead:
        out.append("소제목·첫 문장:")
        out += heads
    else:
        out.append("소제목: " + " | ".join(heads))
    cover = []
    for key in ("표지 1줄", "표지 2줄") + (("표지 장면",) if scene else ()):
        mm = next((re.search(r"^%s:\s*(.+)$" % key, b, flags=re.M) for b in blocks
                   if re.search(r"^%s:" % key, b, flags=re.M)), None)
        if mm:
            cover.append("%s=%s" % (key[3:], cut(mm.group(1), 70)))
    if cover:
        out.append("표지: " + " / ".join(cover))
    scenes = []
    for b in blocks if scene else []:
        i = b.find("본문 이미지 alt 텍스트 제안")
        if i < 0:
            continue
        for ln in b[i:].split("\n")[1:]:
            if "→" in ln:
                scenes.append(cut(ln.split("→", 1)[1], 45))
            elif ln.strip().startswith("출처 캡처"):
                break
    if scenes:
        out.append("본문 사진: " + " / ".join(scenes))
    if "salim" in path.replace("\\", "/"):
        try:
            from salim_check import experience_sentences
            exp = experience_sentences(text)
            if exp:
                out.append("경험 문장: " + " / ".join(cut(e, 60) for e in exp))
        except Exception as e:  # noqa: BLE001
            out.append("(경험 문장 못 뽑음: %s)" % e)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="원고 겹침 확인용 요약(읽기만)")
    ap.add_argument("paths", nargs="+", help="원고 파일 또는 폴더")
    ap.add_argument("--lead", action="store_true", help="소제목마다 첫 문장도 보여 줌")
    ap.add_argument("--scenes", action="store_true", help="표지 장면·본문 사진 장면도 보여 줌(혜택주머니)")
    ap.add_argument("--skip", help="파일 이름에 이 글자가 들어 있으면 뺌")
    a = ap.parse_args()
    files = []
    for p in a.paths:
        if os.path.isdir(p):
            files += sorted(f for f in glob.glob(os.path.join(p, "*.md"))
                            if re.match(r"\d", os.path.basename(f)) and not f.endswith(".facts.md"))
        elif os.path.isfile(p):
            files.append(p)
        else:
            print("없는 경로:", p)
    if a.skip:
        files = [f for f in files if a.skip not in os.path.basename(f)]
    if not files:
        print("앞 원고 없음")
        return 0
    for f in files:
        print(digest(f, a.lead, a.scenes))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
