# -*- coding: utf-8 -*-
"""혜택주머니(longterm_wp) 원고 검사. 파일은 읽기만 한다(아무것도 안 고침).

사용:
  python tools/lt_draft_check.py --week 2026-11-23                 # 그 주 원고 폴더(원고/*.md, _로 시작하는 파일 제외) 전부
  python tools/lt_draft_check.py --week 2026-11-23 --file 원고/01_...md   # 파일 하나(작업 폴더 기준 상대경로 또는 절대경로)

글 정보(역할·묶음·발행 순서)는 06_확정.json → 없으면 05_기획표.md → 둘 다 없으면 원고 안 구분 제목("### 1/3 대표글: 키워드")에서 읽는다.
검사 6가지(글마다 ✔/✖): 글자 수 / 본문 사진 줄 / 피할 표현 / 카테고리 / {{LINK}} 순서 / 표지·메타·FAQ·면책·첫 줄.
종료 코드: 걸린 게 하나라도 있으면 1, 없으면 0."""
import argparse
import glob
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import lt_common as C
from lt_auto_input import CATEGORIES, split_blocks

LEN_RANGE = {"대표": (4000, 6000), "세부": (3600, 4800)}
PHOTO_THRESHOLD = 4000      # 본문 이 글자 수 이상이면 5줄, 미만이면 4줄 (app.py LONGTERM_BODY_IMAGE_* 와 같은 기준)
PHOTO_LONG, PHOTO_SHORT = 5, 4
FAQ_RANGE = (3, 4)          # 11/19 사용자 결정(4차): 3~4쌍. 5쌍 이상은 ✖
COVER1_MAX, COVER2_MAX = 18, 10
META_RANGE = (80, 150)
SUMMARY_NAMES = ("핵심 요약", "한눈에 정리", "신청 전 꼭 볼 것", "이 글의 결론")
AVOID = re.compile(r"갈리|갈린|갈려|갈립|갈릴|갈렸|가르|가른|가릅")
AVOID_OK = re.compile(r"헷갈|갈라지")


def count_chars(text: str) -> int:
    """공백 제외 글자 수 — app._body_char_count 와 같은 규칙(링크 자리표시자·URL·HTML 태그·마크다운 기호는 뺀다)."""
    text = re.sub(r"\{\{(?:LINK|SHOP):.*?\}\}", "", text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(?:#+|>|[-*+])\s", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("|", "")
    return len(re.sub(r"\s+", "", text))


def body_lines(block_a: str):
    """원고지침 11번: 본문에서 뺄 줄을 걷어낸 (본문 텍스트, 마치며 소제목, 본문 ## 목록)."""
    out, heads = [], []
    in_closing = False
    after_faq = False
    closing_head = ""
    for ln in block_a.splitlines():
        s = ln.strip()
        if re.match(r"^\[플랫폼[^\]]*\]", s) or s.startswith("제목:") or s.startswith("최종 확인:"):
            continue
        if re.match(r"^기준일\s", s) or "<small" in s:
            continue
        m = re.match(r"^##\s+(.+?)\s*$", s)
        if m:
            h = m.group(1)
            heads.append(h)
            if in_closing:                      # 마치며가 끝남(다음 ## 섹션)
                in_closing = False
            if after_faq and not closing_head and h != "FAQ":
                closing_head, in_closing = h, True   # FAQ 다음 첫 ## 섹션 = 마치며
                continue
            if h.strip() == "마치며" and not closing_head:
                closing_head, in_closing = h, True
                continue
            if h.strip().upper() == "FAQ":
                after_faq = True
        if in_closing:
            continue
        out.append(ln)
    return "\n".join(out), closing_head, heads


def strip_br(s: str) -> str:
    return re.sub(r"[\[\]]", "", s or "")


def norm(s: str) -> str:
    return re.sub(r"[\s_]+", "", s or "")


# ---------- 글 정보 읽기 ----------
def load_plan(wdir: str):
    """반환: {"posts": [{order, slot, bundle, role, keyword, category, draft}], "source": 이름} 또는 None."""
    j = C.read_json(os.path.join(wdir, "06_확정.json"), None)
    if j and j.get("posts"):
        posts = [{"order": p.get("order"), "slot": p.get("slot", ""), "bundle": p.get("cluster_seed") or p.get("keyword"),
                  "role": p.get("role"), "keyword": p.get("keyword", ""), "category": p.get("category", ""),
                  "draft": p.get("draft", "")} for p in j["posts"]]
        return {"posts": posts, "source": "06_확정.json"}
    path = os.path.join(wdir, "05_기획표.md")
    if os.path.exists(path):
        posts = []
        ym = re.search(r"(\d{4})-\d{2}-\d{2}", os.path.basename(wdir))
        year = ym.group(1) if ym else ""
        for ln in open(path, encoding="utf-8").read().splitlines():
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) < 6 or not cells[0].isdigit():
                continue
            m = re.match(r"(\d+)/(\d+).*?(\d{1,2}:\d{2})", cells[1])
            rm = re.match(r"([A-Za-z☆]+)\S*\s*(대표|세부)", cells[2])
            if not (m and rm):
                continue
            posts.append({"order": int(cells[0]), "slot": f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}T{int(m.group(3).split(':')[0]):02d}:{m.group(3).split(':')[1]}",
                          "bundle": rm.group(1), "role": rm.group(2), "keyword": cells[4], "category": "", "draft": ""})
        if posts:
            return {"posts": posts, "source": "05_기획표.md"}
    return None


def pick_plan_row(plan, path: str, md: str):
    if not plan:
        return None
    base = os.path.basename(path)
    for p in plan["posts"]:
        if p["draft"] and os.path.basename(p["draft"]) == base:
            return p
    m = re.match(r"(\d+)_(\d{8})-(\d{4})_(.+)\.md$", base)
    if m:
        kw = norm(m.group(4))
        for p in plan["posts"]:
            if norm(p["keyword"]) == kw:
                return p
    return None


def role_from_title(md: str):
    m = re.search(r"^#{2,4}\s*\d+/\d+\s*(대표|세부)글", md, flags=re.MULTILINE)
    return m.group(1) if m else None


# ---------- 검사 ----------
def check_one(path: str, plan):
    md = open(path, encoding="utf-8").read()
    a, b = split_blocks(md)
    res = {"file": os.path.basename(path), "items": [], "n": None, "role": None}   # items: (이름, 통과?, 설명)

    def add(name, ok, msg):
        res["items"].append((name, ok, msg))

    if not a:
        add("읽기", False, "코드 블록 A(본문)를 찾지 못함")
        return res
    b = b or ""
    row = pick_plan_row(plan, path, md)
    role = (row or {}).get("role") or role_from_title(md)
    res["role"] = role
    res["row"] = row
    if role not in LEN_RANGE:
        add("역할", False, "대표/세부를 알 수 없음(06_확정.json·05_기획표.md·구분 제목 모두 없음) — 글자 수는 세부 기준으로 봄")
        role = "세부"

    text, closing, heads = body_lines(a)
    n = count_chars(text)
    res["n"] = n
    lo, hi = LEN_RANGE[role]
    add("글자 수", lo <= n <= hi, f"{n:,}자 (공백 제외, {role} {lo:,}~{hi:,}자)")

    # 본문 사진 줄
    want = PHOTO_LONG if n >= PHOTO_THRESHOLD else PHOTO_SHORT
    pairs = re.findall(r'^\s*"?\s*##\s*(.+?)\s*"?\s*(?:→|->)\s*(.+?)\s*$', b, flags=re.MULTILINE)
    heads_set = [h.strip() for h in heads]
    problems = []
    if len(pairs) != want:
        problems.append(f"줄 수 {len(pairs)}개(기대 {want}개)")
    seen = set()
    banned = {"FAQ", "이 주제의 세부 글"} | set(SUMMARY_NAMES)
    if closing:
        banned.add(closing.strip())
    for h, _scene in pairs:
        h = h.strip().strip('"')
        if h not in heads_set:
            problems.append(f"'{h}' 는 본문 ## 줄과 글자가 다름")
        if h in seen:
            problems.append(f"'{h}' 가 두 번 나옴")
        seen.add(h)
        if h in banned or h.upper() == "FAQ" or "마치며" in h:
            problems.append(f"'{h}' 는 FAQ·마치며·요약 블록임")
    add("본문 사진", not problems, f"alt {len(pairs)}줄 / 기대 {want}줄" + ("" if not problems else " — " + "; ".join(problems)))

    # 피할 표현 (제목·본문·메타 전부)
    scan = md
    hits = []
    for m in AVOID.finditer(scan):
        ctx = scan[max(0, m.start() - 6): m.end() + 6].replace("\n", " ")
        if AVOID_OK.search(scan[max(0, m.start() - 2): m.end() + 2]):
            continue
        hits.append(ctx)
    add("피할 표현", not hits, f"{len(hits)}개" + ("" if not hits else " — " + " / ".join(f"…{h}…" for h in hits[:5])))

    # 카테고리
    cm = re.search(r"^\s*카테고리 제안:\s*(.+?)\s*$", b, flags=re.MULTILINE)
    cat = cm.group(1) if cm else ""
    add("카테고리", cat in CATEGORIES, f"'{cat or '없음'}'" + ("" if cat in CATEGORIES else " — 6개 이름 중 하나여야 함"))
    if row and row.get("category") and cat and cat != row["category"]:
        add("카테고리(확정표)", False, f"메타 '{cat}' ≠ 확정표 '{row['category']}'")

    # {{LINK}} 순서
    add("링크 순서", *check_links(a, row, plan))

    # 표지·메타·FAQ·면책·첫 줄
    misc = []
    first = next((ln.strip() for ln in a.splitlines() if ln.strip() and not re.match(r"^\[플랫폼|^제목:", ln.strip())), "")
    if not first.startswith("최종 확인:"):
        misc.append("첫 줄이 '최종 확인:' 이 아님")

    def field(name):
        m = re.search(rf"^\s*{name}:\s*(.+?)\s*$", b, flags=re.MULTILINE)
        return m.group(1) if m else ""
    l1, l2 = field("표지 1줄"), field("표지 2줄")
    for nm, v, mx in (("표지 1줄", l1, COVER1_MAX), ("표지 2줄", l2, COVER2_MAX)):
        L = len(strip_br(v))
        if not v:
            misc.append(f"{nm} 없음")
        elif L > mx:
            misc.append(f"{nm} {L}자(최대 {mx}자)")
    md_desc = field("메타 디스크립션")
    if not (META_RANGE[0] <= len(md_desc) <= META_RANGE[1]):
        misc.append(f"메타 디스크립션 {len(md_desc)}자({META_RANGE[0]}~{META_RANGE[1]}자)")
    faq = re.search(r"^##\s*FAQ\s*$(.*?)(?=^##\s|\Z)", a, flags=re.MULTILINE | re.DOTALL)
    nq = len(re.findall(r"^\s*(?:\*\*)?Q\s*\d*\s*[.:)]", faq.group(1), flags=re.MULTILINE)) if faq else 0
    if not faq:
        misc.append("## FAQ 없음")
    elif not (FAQ_RANGE[0] <= nq <= FAQ_RANGE[1]):
        misc.append(f"FAQ Q. {nq}쌍({FAQ_RANGE[0]}~{FAQ_RANGE[1]}쌍)")
    if "<small" not in a:
        misc.append("면책 <small> 줄 없음")
    add("표지·메타·FAQ·면책", not misc, f"표지 {len(strip_br(l1))}/{len(strip_br(l2))}자 · 메타 {len(md_desc)}자 · FAQ {nq}쌍" + ("" if not misc else " — " + "; ".join(misc)))
    return res


def check_links(a: str, row, plan):
    """{{LINK:kw}} 가 같은 묶음에서 이 글보다 먼저 발행되는 키워드만 가리키는지. 대표글의 '## 이 주제의 세부 글' 목록은 실제 세부만."""
    links = [(m.start(), m.group(1).strip()) for m in re.finditer(r"\{\{LINK:(.+?)\}\}", a)]
    if not links:
        return True, "링크 없음"
    if not (row and plan and plan.get("posts")):
        return True, f"링크 {len(links)}개 — 이 글이 확정표·기획표에 없어 순서는 못 봄"
    me = row
    mates = [p for p in plan["posts"] if p["bundle"] == me["bundle"]]
    key = lambda p: (p["slot"], p["order"] or 0)  # noqa: E731
    sec = re.search(r"^##\s*이 주제의 세부 글\s*$", a, flags=re.MULTILINE)
    sec_start = sec.start() if sec else None
    bad = []
    for pos, kw in links:
        in_list = sec_start is not None and pos > sec_start
        target = next((p for p in mates if norm(p["keyword"]) == norm(kw)), None)
        if target is None:
            bad.append(f"'{kw}' 는 같은 묶음 글이 아님")
        elif in_list and me["role"] == "대표":
            if target["role"] != "세부":
                bad.append(f"'{kw}' 는 세부 글이 아님(세부 글 목록)")
        elif key(target) >= key(me):
            bad.append(f"'{kw}' 는 이 글보다 먼저 발행되지 않음({target['slot'][5:].replace('T', ' ')})")
    return not bad, f"링크 {len(links)}개" + ("" if not bad else " — " + "; ".join(bad))


def main():
    ap = argparse.ArgumentParser(description="혜택주머니 원고 검사(읽기 전용)")
    ap.add_argument("--week", required=True, help="주 시작 월요일 YYYY-MM-DD")
    ap.add_argument("--file", action="append", help="검사할 원고 파일(여러 번 가능). 없으면 그 주 원고 폴더 전부")
    a = ap.parse_args()
    C.check_week(a.week)
    wdir = C.week_dir(a.week)
    if a.file:
        files = [f if os.path.isabs(f) else (os.path.join(wdir, f) if os.path.exists(os.path.join(wdir, f)) else f) for f in a.file]
    else:
        files = sorted(f for f in glob.glob(os.path.join(wdir, "원고", "*.md")) if not os.path.basename(f).startswith("_"))
    if not files:
        sys.exit("검사할 원고 파일이 없습니다.")
    plan = load_plan(wdir)
    print(f"[원고 검사] {a.week} 주 · 글 {len(files)}편 · 글 정보: {plan['source'] if plan else '구분 제목만'}\n")
    fails = 0
    for f in files:
        r = check_one(f, plan)
        bad = [i for i in r["items"] if not i[1]]
        fails += len(bad)
        mark = "✖" if bad else "✔"
        n = f"{r['n']:,}자" if r["n"] is not None else "?"
        print(f"{mark} {r['file']} · {r['role'] or '?'} · {n} · 걸린 항목 {len(bad)}개")
        for name, ok, msg in r["items"]:
            print(f"    {'✔' if ok else '✖'} {name}: {msg}")
        print()
    print("결과: " + ("모두 통과 ✔" if not fails else f"걸린 항목 {fails}개 ✖ — 고쳐 쓰기"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
