# -*- coding: utf-8 -*-
"""W싼세계(wp_tips) 원고 검사 — tools/wp_rules.md 1·2·4·12·14장 중 기계로 셀 수 있는 것을 모두 본다.

사용: python -X utf8 tools/wp_check.py <원고 파일 또는 폴더> [--research <research 폴더>] [--final] [--keyword 키워드] [--out 결과.md]
- 파일 이름: [_test_]<MMDD>_<순번>_<C|D|S>_<짧은이름>.md  (틀 = 파일 이름의 C/D/S). *.facts.md 는 건너뛴다.
- 원고 구조: 본문 → '[메모]' 한 줄 → 메타데이터. 줄 번호는 파일 전체 기준(1부터).
- --research: 받은편지함 research 폴더(<상품id>.md). 주면 본문의 가격·별점·리뷰 수가 research 값과 같은지,
  후기 원문과 같은 15자 이상 문자열이 따옴표 안에 있는지도 본다. 상품은 본문 링크 줄의 [쿠팡링크:상품id] 로 정하고,
  링크가 이미 HTML 이면 폴더의 모든 research 를 후보로 쓴다.
- --final(넣기 전 검사): 위에 더해 '[쿠팡링크' 가 한 군데라도 남아 있으면 위반. 링크 줄은 <a …>…</a> HTML 한 줄이어야 한다.
- 키워드: --keyword 또는 원고 옆 <원고이름>.facts.md 첫 줄 '키워드: …'. 있으면 제목 앞 15자 안에 키워드가 있는지 본다.
- 위반이 1개라도 있으면 종료 코드 1. 경고(사람이 판단)는 위반으로 세지 않는다. 파일만 읽는다(앱 데이터·쓰기 없음).
글자 수 = 공백 제외. 맨 위 두 줄·링크 줄·태그 줄·표 구분줄·마크다운 기호(#, **, >, -, |)는 뺀다."""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANNED_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wp_banned.json")

NAME_RX = re.compile(r"^(?:_test_)?(\d{4})_(\d+)_([CDS])_(.+)\.md$")
RANGES = {"C": (4000, 6000, 7, 9), "D": (3500, 5000, 7, 9), "S": (3500, 4500, 6, 8)}
LINK_COUNT = {"C": (2, 3), "D": (1, 1), "S": (1, 1)}
CATEGORIES = ("생활용품", "주방용품", "생활가전")
META_LABELS = ["메타 디스크립션", "카테고리 제안", "글 유형", "태그 제안", "표지 배지", "표지 1줄", "표지 2줄", "표지 보조",
               "표지 장면", "대표 이미지(표지) alt 텍스트 제안", "본문 이미지 alt 텍스트 제안"]
REVIEW_SECS = ("모은 후기", "최신순", "낮은 별점")  # v1.6: 후기 구역 = "모은 후기 N개"(옛 양식 이름도 읽음)
MEMO_MARK = "[메모]"
DATE_RX = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일")
PRICE_RX = re.compile(r"(\d[\d,]*)\s*원")
LINK_LINE_RX = re.compile(r"^[ \t]*링크[ \t]*[:：]")
PLACEHOLDER_RX = re.compile(r"\[쿠팡링크:(\d+)\]")
HTML_LINK_RX = re.compile(r"<a\b[^\n]*</a>\s*$", re.I)
BASE_LINE_RX = re.compile(r"^\(\d{1,2}월 \d{1,2}일 쿠팡 기준 · 상품 정보와 (?:(구매 후기 [\d,]+개)|필수 표기정보)를 바탕으로 정리\)$")
SEP_ROW_RX = re.compile(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|?\s*$")


def load_rules():
    with open(BANNED_JSON, encoding="utf-8") as f:
        return json.load(f)


# ───────── research 읽기 ─────────
def round_reviews(n):
    """리뷰 수 표기: 앞 두 자리만 남기고 내림(3,214 → 3,200 / 369 → 360 / 10 미만은 그대로)."""
    if n < 100:
        return n
    s = str(n)
    return int(s[:2] + "0" * (len(s) - 2))


def parse_research(path):
    d = {"id": os.path.splitext(os.path.basename(path))[0], "prices": set(), "rating": None, "reviews": None, "reviews_text": [],
         "name": "", "brand": "", "model": "", "review_lines": []}
    text = open(path, encoding="utf-8").read()
    sec = ""
    d["slots"] = set()
    for ln in text.splitlines():
        ms = re.match(r"^-\s*쓸 글\s*[:：]\s*(.*)", ln)
        if ms:  # 새 형식 '쓸 글: 1124 C … / 1123 S …' (날짜 틀). 옛 형식(비교 등)은 비어 있음
            d["slots"] = {(a, b.upper()) for a, b in re.findall(r"(?<!\d)(\d{4})\s*([CSDcsd])(?![A-Za-z])", ms.group(1))}
        h = re.match(r"^##\s+(.+)", ln)
        if h:
            sec = h.group(1).strip()
            continue
        m = re.match(r"^-\s*(상품명|브랜드|모델명|별점|리뷰 수)\s*[:：]\s*(.+)", ln)
        if m and not sec.startswith(REVIEW_SECS):
            k, v = m.group(1), m.group(2).strip()
            if k == "상품명": d["name"] = v
            elif k == "브랜드": d["brand"] = v
            elif k == "모델명": d["model"] = v
            elif k == "별점": d["rating"] = v
            elif k == "리뷰 수":
                n = re.sub(r"[^\d]", "", v)
                d["reviews"] = int(n) if n else None
        if sec.startswith("옵션별 일반 판매가") and ln.strip().startswith("|") and "쓰지" not in ln:
            for pm in PRICE_RX.finditer(ln):
                d["prices"].add(int(pm.group(1).replace(",", "")))
        if sec.startswith(REVIEW_SECS):
            rm = re.match(r"^\s*\d+\.\s*(.+)", ln)
            if rm:
                t = re.sub(r"^\[제외 후보\]\s*", "", rm.group(1)).strip()
                d["review_lines"].append(t)
    return d


def load_research_dir(folder):
    out = {}
    if folder and os.path.isdir(folder):
        for fn in sorted(os.listdir(folder)):
            if fn.lower().endswith(".md") and re.fullmatch(r"\d+\.md", fn):
                out[fn[:-3]] = parse_research(os.path.join(folder, fn))
    return out


# ───────── 원고 분해 ─────────
def split_file(text):
    lines = text.replace("\r\n", "\n").split("\n")
    idx = [i for i, l in enumerate(lines) if l.strip() == MEMO_MARK]
    return lines, idx


def md_strip(s):
    s = re.sub(r"\*\*", "", s)
    s = re.sub(r"^\s*(?:#{1,6}|>|[-*+])\s+", "", s)
    return s.replace("|", "")


def count_chars(body_lines):
    n = 0
    for ln in body_lines:
        if SEP_ROW_RX.match(ln) or LINK_LINE_RX.match(ln) or ln.strip().startswith("태그:"):
            continue
        n += len(re.sub(r"\s+", "", md_strip(ln)))
    return n


def sections_of(body_lines, base=0):
    """## 단위로 나눈다. [(제목 또는 None(도입), 시작 줄 번호(1부터), [줄들])]"""
    secs = [[None, base + 1, []]]
    for i, ln in enumerate(body_lines):
        if re.match(r"^##\s+\S", ln) and not ln.startswith("###"):
            secs.append([re.sub(r"^##\s+", "", ln).strip(), base + i + 1, [(base + i + 1, ln)]])
        else:
            secs[-1][2].append((base + i + 1, ln))
    return secs


def table_blocks(lines_numbered):
    """연속한 표 줄 묶음. [(첫 줄 번호, [(번호, 줄)])]"""
    blocks, cur = [], []
    for no, ln in lines_numbered:
        if ln.strip().startswith("|"):
            cur.append((no, ln))
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def cells(row):
    r = row.strip()
    if r.startswith("|"): r = r[1:]
    if r.endswith("|"): r = r[:-1]
    return [c.strip() for c in r.split("|")]


def is_product_heading(head, research_ids, research):
    """제품 섹션 소제목인지(research 가 있으면 상품명·브랜드·모델명 조각이 들어 있는지로)."""
    if not head:
        return False
    for rid in research_ids:
        r = research.get(rid)
        if not r:
            continue
        for key in (r["model"], r["name"]):
            if key and key in head:
                return True
        if r["brand"] and r["model"] and r["brand"] in head and r["model"] in head:
            return True
    return False


# ───────── 검사 본체 ─────────
def check_text(text, name, frame, research=None, final=False, keyword="", rules=None):
    rules = rules or load_rules()
    research = research or {}
    V, W = [], []  # (항목, 줄, 내용)

    def v(item, line, msg): V.append((item, line, msg))
    def w(item, line, msg): W.append((item, line, msg))

    lines, idx = split_file(text)
    # ── 2. [메모] 줄 1개
    if len(idx) != 1:
        v("[메모] 줄", "-", f"'[메모]' 줄이 {len(idx)}개예요(정확히 1개여야 해요)")
        body, memo, memo_base = lines, [], len(lines)
    else:
        body, memo, memo_base = lines[:idx[0]], lines[idx[0] + 1:], idx[0] + 1
    # ── 1. 맨 위 두 줄
    if not body or body[0].strip() != "[플랫폼:워드프레스]":
        v("맨 위 두 줄", 1, "첫 줄이 '[플랫폼:워드프레스]' 가 아니에요")
    title = ""
    tm = re.match(r"^제목\s*:\s*(\S.*)$", body[1].strip()) if len(body) > 1 else None
    if not tm:
        v("맨 위 두 줄", 2, "둘째 줄이 '제목: …' 이 아니에요")
    else:
        title = tm.group(1).strip()
    if frame not in RANGES:
        v("파일 이름", "-", f"파일 이름이 '<MMDD>_<순번>_<C|D|S>_<짧은이름>.md' 규칙이 아니에요: {name}")
        return V, W, {"chars": 0, "h2": 0, "frame": frame, "title": title}

    body_text_lines = [(i + 1, l) for i, l in enumerate(body)]
    # 링크 줄 안 HTML 이 글자 검사에 섞이지 않게, 글자 검사용 줄은 링크 줄을 비운다
    scan = []  # (줄 번호, 글자)
    for no, ln in enumerate(lines, 1):
        scan.append((no, "" if LINK_LINE_RX.match(ln) else ln))
    body_scan = [(no, ln) for no, ln in scan if no <= len(body)]

    # ── 3. 글자 수·## 개수
    lo, hi, hmin, hmax = RANGES[frame]
    nchar = count_chars(body[2:])
    h2 = [(no, l) for no, l in body_text_lines if re.match(r"^##\s+\S", l) and not l.startswith("###")]
    if not lo <= nchar <= hi:
        v("글자 수", "-", f"틀 {frame} 는 공백 제외 {lo:,}~{hi:,}자인데 {nchar:,}자예요")
    if not hmin <= len(h2) <= hmax:
        v("## 개수", "-", f"틀 {frame} 는 '##' {hmin}~{hmax}개인데 {len(h2)}개예요")
    for no, l in body_text_lines:
        if re.match(r"^#{4,}\s", l) or re.match(r"^#\s", l):
            v("소제목 종류", no, "소제목은 ## · ### 만 써요")

    # ── 링크 · 제품 섹션
    secs = sections_of(body)
    link_ids = [m.group(1) for ln in body for m in [PLACEHOLDER_RX.search(ln)] if m and LINK_LINE_RX.match(ln)]
    nm = NAME_RX.match(name)
    slot = (nm.group(1), nm.group(3)) if nm else None  # 원고 이름의 날짜+틀 = 그 글의 제품을 찾는 열쇠
    by_slot = [i for i, r in research.items() if slot and slot in r.get("slots", ())]
    research_ids = [i for i in link_ids if i in research] or by_slot or list(research)
    link_sections = 0
    product_heads = []
    for head, start, ls in secs:
        link_idx = [(no, l) for no, l in ls if LINK_LINE_RX.match(l)]
        is_faq = bool(head) and head.startswith("자주 묻는 질문")
        is_prod = bool(head) and not is_faq and (
            bool(link_idx) or any(re.match(r"^\s*한 줄 요약\s*[:：]", l) for _, l in ls) or is_product_heading(head, research_ids, research))
        if head is None:
            for no, l in link_idx:
                v("링크 위치", no, "첫 '##' 앞(도입부)에는 '링크:' 줄을 두지 않아요")
            continue
        if is_faq:
            for no, l in link_idx:
                v("링크 위치", no, "'## 자주 묻는 질문' 안에는 '링크:' 줄을 두지 않아요")
            continue
        if research and link_idx and not is_product_heading(head, research_ids, research) and not any(
                re.match(r"^\s*한 줄 요약\s*[:：]", l) for _, l in ls):
            w("링크 위치", link_idx[0][0], f"'## {head}' 가 제품 섹션(상품명·모델명)인지 확인하세요")
        if is_prod:
            product_heads.append(head)
            if len(link_idx) != 1:
                v("링크 줄 수", start, f"제품 섹션 '## {head}' 에 '링크:' 줄이 {len(link_idx)}개예요(1개여야 해요)")
            else:
                link_sections += 1
                last = next((no for no, l in reversed(ls) if l.strip()), None)
                if link_idx[0][0] != last:
                    v("링크 위치", link_idx[0][0], f"'## {head}' 의 '링크:' 줄이 섹션 맨 끝이 아니에요")
        elif link_idx:
            for no, l in link_idx:
                v("링크 위치", no, f"제품 섹션이 아닌 '## {head}' 에 '링크:' 줄이 있어요")
    lo_l, hi_l = LINK_COUNT[frame]
    if not lo_l <= link_sections <= hi_l:
        v("링크 줄 수", "-", f"틀 {frame} 는 '링크:' 가 있는 제품 섹션이 {lo_l}~{hi_l}개인데 {link_sections}개예요")
    for no, ln in enumerate(lines, 1):
        if not LINK_LINE_RX.match(ln):
            if "[쿠팡링크" in ln and not final:
                v("링크 형식", no, "'[쿠팡링크:상품id]' 는 '링크: ' 로 시작하는 줄에만 써요")
            continue
        val = re.sub(r"^[ \t]*링크[ \t]*[:：][ \t]*", "", ln).strip()
        if final:
            if "[쿠팡링크" in val:
                v("[쿠팡링크 남음", no, "넣기 전에 [쿠팡링크:…] 가 HTML 로 바뀌지 않았어요")
            elif not HTML_LINK_RX.search(val) or not val.lower().startswith("<a"):
                v("링크 형식", no, "넣기 전 검사: 링크 값이 <a …>…</a> 한 줄이어야 해요")
        else:
            if not (re.fullmatch(r"\[쿠팡링크:\d+\]", val) or HTML_LINK_RX.search(val)):
                v("링크 형식", no, f"링크 값은 [쿠팡링크:상품id] 여야 해요: {val[:30]}")
    if final:
        for no, ln in enumerate(lines, 1):
            if "[쿠팡링크" in ln and not LINK_LINE_RX.match(ln):
                v("[쿠팡링크 남음", no, "[쿠팡링크 글자가 남아 있어요")

    # ── 가격: 줄, {{SHOP, 네이버, 주소
    for no, ln in scan:
        if re.match(r"^\s*가격\s*[:：]", ln):
            v("가격: 줄", no, "'가격:' 으로 시작하는 줄은 쓰지 않아요(가격은 문장·표 안에)")
        if "{{SHOP" in ln:
            v("{{SHOP", no, "{{SHOP:…}} 는 쓰지 않아요")
        if "네이버" in ln:
            v("네이버", no, "'네이버' 말은 쓰지 않아요(네이버 쇼핑 자료 금지)")
    for no, ln in body_scan:
        if re.search(r"https?://|www\.", ln):
            v("주소", no, "본문에 주소(http…)를 쓰지 않아요")
        if re.search(r"참고\s*자료", ln):
            v("참고 자료", no, "'참고 자료' 목록은 쓰지 않아요")

    # ── 4장 금지·6장 써 본 척 (제목·소제목·본문·표·FAQ·메타 전부)
    for r in rules["forbidden"]:
        for no, ln in scan:
            for m in re.finditer(r["regex"], ln):
                v(r["name"], no, f"'{m.group(0)}' · {ln.strip()[:30]}")
    for r in rules.get("forbidden_warn", []):
        for no, ln in scan:
            for m in re.finditer(r["regex"], ln):
                w(r["name"], no, f"'{m.group(0)}' · {ln.strip()[:30]}")
    for r in rules["sounds_used"]:
        for no, ln in scan:
            for m in re.finditer(r["regex"], ln):
                v(r["name"], no, f"'{m.group(0)}' · {ln.strip()[:30]}")

    # ── 12장 피할 표현
    counts = {r["name"]: [] for r in rules["limits"]}
    for no, raw in scan:
        ln = raw.replace("**", "")
        for r in rules["zero"]:
            for m in re.finditer(r["regex"], ln):
                (v if r["severity"] == "fail" else w)(r["name"], no, f"'{m.group(0)}' · {ln.strip()[:30]} → {r.get('hint', '')}")
        for r in rules["limits"]:
            for m in re.finditer(r["regex"], ln):
                counts[r["name"]].append((no, m.group(0)))
    for r in rules["limits"]:
        hits = counts[r["name"]]
        if len(hits) > r["max"]:
            v("횟수 제한", hits[r["max"]][0], f"'{r['name']}' {len(hits)}번(한 글 {r['max']}번까지) — 줄 {', '.join(str(h[0]) for h in hits)}")
    # 제목 규칙
    if title:
        tl = len(title)
        if not 25 <= tl <= 40:
            v("제목", 2, f"제목은 25~40자인데 {tl}자예요")
        for bw in rules["title_banned"]:
            if bw in title:
                v("제목", 2, f"제목에 쓰지 않는 말 '{bw}'")
        for rx in rules["title_banned_regex"]:
            m = re.search(rx, title)
            if m:
                v("제목", 2, f"제목에 쓰지 않는 표현 '{m.group(0)}'")
        if keyword:
            if re.sub(r"\s+", "", keyword) not in re.sub(r"\s+", "", title)[:15 + title[:15].count(" ")] :
                v("제목", 2, f"제목 앞 15자 안에 키워드 '{keyword}' 가 없어요")

    # ── 표 칸 '없음' 단독
    for blk in table_blocks(body_text_lines):
        for no, row in blk:
            if SEP_ROW_RX.match(row):
                continue
            for c in cells(row):
                if c.replace("**", "").strip() == "없음":
                    v("표 칸 '없음'", no, "표 칸에 '없음'만 쓰지 않아요('표시 없음')")

    # ── 기준일 (가격·별점 문장)
    date_near = lambda i: any(DATE_RX.search(lines[j]) for j in range(max(0, i - 1), min(len(lines), i + 2)))  # noqa: E731
    in_table = set()
    for blk in table_blocks(body_text_lines):
        has_date = any(DATE_RX.search(r) or "기준" in r for _, r in blk)
        prev_no, next_no = blk[0][0] - 2, blk[-1][0]
        near = (prev_no >= 0 and DATE_RX.search(lines[prev_no])) or (next_no < len(lines) and DATE_RX.search(lines[next_no]))
        for no, r in blk:
            in_table.add(no)
            if (PRICE_RX.search(r) or re.search(r"별점\s*\d|리뷰\s*[\d,]+", r)) and not (has_date or near) and not SEP_ROW_RX.match(r):
                v("기준일", no, "표에 가격·별점이 있는데 기준일(몇 월 며칠 쿠팡 기준)이 표 안·바로 앞뒤에 없어요")
    for no, ln in body_scan:
        if no in in_table or ln.lstrip().startswith("#") or no <= 2:
            continue
        if (re.search(r"\d[\d,]*\s*원", ln) or re.search(r"별점\s*[0-9]", ln) or re.search(r"리뷰\s*[\d,]+\s*개", ln)) and not DATE_RX.search(ln):
            # 같은 문단의 앞줄에 기준일이 있으면 통과(문단 = 빈 줄로 끊긴 묶음)
            j = no - 2
            ok = False
            while j >= 0 and lines[j].strip():
                if DATE_RX.search(lines[j]):
                    ok = True
                    break
                j -= 1
            if not ok:
                v("기준일", no, f"가격·별점·리뷰 수 문장에 기준일이 없어요: {ln.strip()[:30]}")
    first_h = h2[0][0] if h2 else len(body) + 1
    intro = "\n".join(lines[2:first_h - 1])
    base_lines = [(first_no, l.strip()) for first_no, l in ((i + 3, x) for i, x in enumerate(lines[2:first_h - 1])) if l.strip()]
    base_hit = [(no, l) for no, l in base_lines if "쿠팡 기준" in l]
    if not base_hit:
        v("기준일 줄", "-", "도입부(첫 '##' 앞)에 '(○월 ○일 쿠팡 기준 · 상품 정보와 … 바탕으로 정리)' 줄이 없어요")
    else:
        bno, btxt = base_hit[-1]
        bm = BASE_LINE_RX.match(btxt)
        if not bm:
            v("기준일 줄", bno, f"기준일 줄 문구가 v1.4 5장 형식이 아니에요: {btxt[:40]}")
        else:
            kind = "후기" if bm.group(1) else "표기"
            if base_lines and base_lines[-1][0] != bno:
                w("기준일 줄", bno, "기준일 줄이 도입의 마지막 줄이 아니에요(도입 바로 다음 줄이어야 해요)")
            if kind == "표기" and frame in ("D", "S"):
                v("기준일 줄", bno, f"{frame} 틀은 '구매 후기 N개' 문구만 허용돼요('필수 표기정보' 문구 위반)")
            if kind == "표기" and frame == "C":
                for no, ln in body_scan:
                    for m in re.finditer(r"후기에서|불만", ln):
                        v("기준일 줄", no, f"후기를 쓰지 않은 C 글인데 본문에 '{m.group(0)}' 이(가) 있어요 · {ln.strip()[:30]}")
    nwarn = len(re.findall(r"수시로\s*바뀌", "\n".join(l for _, l in body_scan)))
    if nwarn != 1:
        v("가격 안내 문장", "-", f"'가격은 수시로 바뀌니 링크에서 오늘 가격을 확인해 보세요' 류 문장이 {nwarn}번이에요(글 전체 1번)")

    # ── research 와 숫자 대조
    if research:
        cand = [research[i] for i in research_ids if i in research] or list(research.values())
        okp = set().union(*[r["prices"] for r in cand]) if cand else set()
        okr = {r["rating"] for r in cand if r["rating"]}
        okv = {round_reviews(r["reviews"]) for r in cand if r["reviews"] is not None}
        okv_raw_small = {r["reviews"] for r in cand if r["reviews"] is not None and r["reviews"] < 100}
        for no, ln in body_scan:
            if no <= 2:
                continue
            for m in PRICE_RX.finditer(ln):
                val = int(m.group(1).replace(",", ""))
                if val >= 100 and val not in okp:
                    v("research 가격", no, f"{val:,}원 은 research 일반 판매가에 없어요(있는 값: {', '.join(f'{x:,}' for x in sorted(okp)) or '없음'})")
            for m in re.finditer(r"별점\s*([0-9](?:\.[0-9]+)?)", ln):
                if m.group(1) not in okr:
                    v("research 별점", no, f"별점 {m.group(1)} 은 research 별점({', '.join(sorted(okr)) or '없음'})과 달라요")
            for m in re.finditer(r"리뷰\s*([\d,]+)\s*개(\s*이상)?", ln):
                n = int(m.group(1).replace(",", ""))
                if n not in okv:
                    v("research 리뷰 수", no, f"리뷰 {n:,}개 는 research 를 내림한 값({', '.join(f'{x:,}' for x in sorted(okv)) or '없음'})과 달라요")
                elif n >= 100 and not m.group(2):
                    v("research 리뷰 수", no, "리뷰 수에는 '이상'을 붙여요(예: 3,200개 이상)")
                elif n < 100 and m.group(2):
                    v("research 리뷰 수", no, "10 미만·두 자리 리뷰 수는 그대로 쓰고 '이상'은 안 붙여요")
        # 표의 별점·리뷰 칸
        for blk in table_blocks(body_text_lines):
            hdr = cells(blk[0][1])
            for ci, hname in enumerate(hdr):
                if "별점" in hname or "리뷰" in hname:
                    for no, row in blk[1:]:
                        if SEP_ROW_RX.match(row):
                            continue
                        cs = cells(row)
                        if ci >= len(cs):
                            continue
                        cell = cs[ci].replace("**", "")
                        if "별점" in hname:
                            for m in re.finditer(r"(?<![\d.])([0-9]\.[0-9]+)(?![\d])", cell):
                                if m.group(1) not in okr:
                                    v("research 별점", no, f"표 별점 {m.group(1)} 이 research({', '.join(sorted(okr))})와 달라요")
                        else:
                            for m in re.finditer(r"([\d,]{2,})\s*개", cell):
                                n = int(m.group(1).replace(",", ""))
                                if n not in okv:
                                    v("research 리뷰 수", no, f"표 리뷰 {n:,}개 가 research 내림값과 달라요")
        # 후기 원문과 같은 15자 이상 문자열이 따옴표 안에
        revs = [re.sub(r"\s+", "", t) for r in cand for t in r["review_lines"]]
        for no, ln in body_scan:
            for qm in re.finditer(r"[\"“‘'](.+?)[\"”’']", ln):
                q = re.sub(r"\s+", "", qm.group(1))
                if len(q) >= 15 and any(q[i:i + 15] in rv for i in range(len(q) - 14) for rv in revs):
                    v("후기 인용", no, f"따옴표 안에 후기 원문과 같은 15자 이상이 있어요: {qm.group(1)[:30]}")
            plain = re.sub(r"\s+", "", re.sub(r"[\"“‘'].+?[\"”’']", "", ln))
            if len(plain) >= 15 and any(plain[i:i + 15] in rv for i in range(len(plain) - 14) for rv in revs):
                w("후기 인용", no, "따옴표 밖에서 후기 원문과 같은 15자 이상 문자열이 있어요(풀어 썼는지 확인)")

    # ── FAQ
    faq = next(((head, start, ls) for head, start, ls in secs if head and head.startswith("자주 묻는 질문")), None)
    if not faq:
        v("FAQ", "-", "'## 자주 묻는 질문' 소제목이 없어요")
    else:
        if faq[0] != "자주 묻는 질문":
            v("FAQ", faq[1], "소제목은 '## 자주 묻는 질문' 글자 그대로여야 해요")
        qs = [(no, l) for no, l in faq[2] if re.match(r"^\s*Q\s*[:：]", l)]
        as_ = [(no, l) for no, l in faq[2] if re.match(r"^\s*A\s*[:：]", l)]
        if len(qs) != len(as_) or not 4 <= len(qs) <= 6:
            v("FAQ", faq[1], f"Q:/A: 가 Q {len(qs)}개·A {len(as_)}개예요(4~6쌍, 짝이 맞아야 해요)")
        else:
            for (qn, _), (an, _) in zip(qs, as_):
                if an <= qn:
                    v("FAQ", qn, "Q: 다음에 A: 가 이어져야 해요")

    # ── 강조(**) 와 인용구
    total = 0
    for head, start, ls in secs:
        n = sum(len(re.findall(r"\*\*[^*\n]+\*\*", l)) for _, l in ls)
        total += n
        if n > 1:
            v("강조", start, f"'{head or '도입부'}' 에 굵게(**)가 {n}개예요(섹션마다 최대 1개)")
    if not 4 <= total <= 6:
        v("강조", "-", f"굵게(**)가 글 전체 {total}개예요(4~6개)")
    nq = sum(1 for _, l in body_text_lines if l.lstrip().startswith("> "))
    if nq > 1:
        v("인용구", "-", f"인용구('> ')가 {nq}개예요(0~1개)")

    # ── 태그 줄
    nb = [(no, l) for no, l in body_text_lines if l.strip()]
    if not nb or not nb[-1][1].startswith("태그:"):
        v("태그 줄", nb[-1][0] if nb else "-", "본문 맨 끝 줄이 '태그:' 줄이 아니에요")
    else:
        ntag = len(re.findall(r"#\S+", nb[-1][1]))
        if ntag != 5:
            v("태그 줄", nb[-1][0], f"태그가 {ntag}개예요(5개)")
        if len(nb) >= 2 and nb[-2][0] != nb[-1][0] - 2:
            v("태그 줄", nb[-1][0], "마무리 인사와 태그 줄 사이는 빈 줄 하나만이어야 해요")

    # ── 2장 메타데이터
    mtxt = {}
    for k, ln in enumerate(memo):
        m = re.match(r"^\s*([^:：\n]+?)\s*[:：]\s*(.*)$", ln)
        if m:
            mtxt.setdefault(m.group(1).strip(), (memo_base + k + 1, m.group(2).strip()))
    for lab in META_LABELS:
        if lab not in mtxt:
            v("메타 빠짐", "-", f"메타데이터 '{lab}' 항목이 없어요")
    def mval(lab): return mtxt.get(lab, (0, ""))[1]
    def mline(lab): return mtxt.get(lab, (0, ""))[0] or "-"
    for lab in META_LABELS:
        if lab in mtxt and lab != "본문 이미지 alt 텍스트 제안" and not mval(lab):
            v("메타 빠짐", mline(lab), f"'{lab}' 값이 비었어요")
    if mval("메타 디스크립션") and not 80 <= len(mval("메타 디스크립션")) <= 150:
        v("메타 길이", mline("메타 디스크립션"), f"메타 디스크립션은 80~150자인데 {len(mval('메타 디스크립션'))}자예요")
    cat = mval("카테고리 제안")
    if cat and cat not in CATEGORIES:
        v("카테고리", mline("카테고리 제안"), f"카테고리 제안 '{cat}' 는 생활용품·주방용품·생활가전 중 하나(글자 그대로)여야 해요")
    if mval("글 유형") and mval("글 유형") != "결정형":
        v("메타 값", mline("글 유형"), "글 유형은 '결정형' 글자 그대로예요")
    tg = [t for t in re.split(r"[,，、]", mval("태그 제안")) if t.strip()]
    if mval("태그 제안") and not 5 <= len(tg) <= 8:
        v("메타 길이", mline("태그 제안"), f"태그 제안은 5~8개인데 {len(tg)}개예요(쉼표로 구분)")
    b = mval("표지 배지")
    if b and not 2 <= len(b) <= 6:
        v("메타 길이", mline("표지 배지"), f"표지 배지는 2~6자인데 {len(b)}자예요")
    for lab in ("표지 1줄", "표지 2줄"):
        val = mval(lab)
        if val:
            if len(re.findall(r"\[[^\]]+\]", val)) != 1:
                v("메타 형식", mline(lab), f"{lab} 에는 [대괄호]가 1개 있어야 해요")
            plain = re.sub(r"[\[\]]", "", val)
            if not 8 <= len(plain) <= 12:
                v("메타 길이", mline(lab), f"{lab} 은 8~12자(대괄호 빼고)인데 {len(plain)}자예요")
    if mval("표지 보조"):
        sub = mval("표지 보조")
        if "·" not in sub or not 15 <= len(sub) <= 22:
            v("메타 길이", mline("표지 보조"), f"표지 보조는 15~22자·가운뎃점(·) 구분이에요(지금 {len(sub)}자)")
    for lab in ("표지 배지", "표지 1줄", "표지 2줄", "표지 보조"):
        if re.search(r"\d[\d,]*\s*원|" + "|".join(["와우", "쿠폰"]), mval(lab)):
            v("표지 글자", mline(lab), "표지 글자에 가격·할인 말을 쓰지 않아요")
    # 본문 이미지 줄 3개
    img_lines = [(memo_base + k + 1, l) for k, l in enumerate(memo) if re.match(r'^\s*"##\s*.+?"\s*(?:→|->)', l)]
    if len(img_lines) != 3:
        v("본문 이미지 줄", mline("본문 이미지 alt 텍스트 제안"), f"'\"## 소제목\" → 장면' 줄이 {len(img_lines)}개예요(3개)")
    heads = {h for h, _, _ in secs if h}
    for no, l in img_lines:
        hm = re.match(r'^\s*"##\s*(.+?)"\s*(?:→|->)\s*(.*)$', l)
        if hm:
            if hm.group(1).strip() not in heads:
                v("본문 이미지 줄", no, f"소제목 '{hm.group(1).strip()}' 가 본문에 없어요")
            elif hm.group(1).strip() in product_heads:
                v("본문 이미지 줄", no, "본문 이미지 소제목은 제품 섹션이 아닌 곳이어야 해요")
            if not hm.group(2).strip():
                v("본문 이미지 줄", no, "장면 설명이 비었어요")
    for no, l in enumerate(lines, 1):
        if no > len(body) and re.search(r'"##', l) and (no, l) not in img_lines:
            v("본문 이미지 줄", no, "'\"## 소제목\" →' 형식은 본문 이미지 줄에만 써요")
    return V, W, {"chars": nchar, "h2": len(h2), "frame": frame, "title": title}


def read_keyword(path):
    facts = re.sub(r"\.md$", ".facts.md", path)
    if os.path.isfile(facts):
        first = open(facts, encoding="utf-8").readline()
        m = re.match(r"^\s*키워드\s*[:：]\s*(.+)$", first)
        if m:
            return m.group(1).strip()
    return ""


def check_file(path, research_dir="", final=False, keyword=""):
    name = os.path.basename(path)
    m = NAME_RX.match(name)
    frame = m.group(3) if m else ""
    text = open(path, encoding="utf-8").read()
    research = load_research_dir(research_dir) if research_dir else {}
    kw = keyword or read_keyword(path)
    V, W, info = check_text(text, name, frame, research, final, kw)
    if not os.path.isfile(re.sub(r"\.md$", ".facts.md", path)):
        W.append(("대조표", "-", f"{name[:-3]}.facts.md 가 없어요"))
    info.update({"name": name, "keyword": kw, "research": bool(research)})
    return V, W, info


def render(path, V, W, info, final):
    out = [f"## {info['name']}  (틀 {info['frame'] or '?'} · 공백 제외 {info['chars']:,}자 · ## {info['h2']}개 · "
           f"{'넣기 전 검사 --final' if final else '일반 검사'} · research 대조 {'함' if info['research'] else '안 함'}"
           f"{' · 키워드 ' + info['keyword'] if info['keyword'] else ''})", ""]
    if V:
        out += ["| 번호 | 항목 | 줄 | 내용 |", "|---|---|---|---|"]
        for i, (it, ln, msg) in enumerate(V, 1):
            out.append(f"| {i} | {it} | {ln} | {msg.replace('|', '/')} |")
    else:
        out.append("위반 0개")
    out.append(f"\n위반 {len(V)}개")
    if W:
        out += ["", "경고(사람이 판단, 위반으로 세지 않음):"]
        for it, ln, msg in W:
            out.append(f"- {it} · 줄 {ln} · {msg}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="W싼세계 원고 검사")
    ap.add_argument("target", help="원고 .md 파일 또는 폴더")
    ap.add_argument("--research", default="", help="research 폴더(<상품id>.md)")
    ap.add_argument("--final", action="store_true", help="넣기 전 검사(링크가 HTML 이어야 하고 [쿠팡링크 가 없어야 함)")
    ap.add_argument("--keyword", default="", help="키워드(없으면 원고 옆 .facts.md 첫 줄)")
    ap.add_argument("--out", default="", help="결과를 이 파일에도 저장")
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    t = os.path.abspath(a.target)
    files = [t] if os.path.isfile(t) else sorted(os.path.join(t, f) for f in os.listdir(t)
                                                  if f.lower().endswith(".md") and not f.lower().endswith(".facts.md"))
    if not files:
        raise SystemExit("검사할 .md 원고가 없어요")
    parts, total = [], 0
    for p in files:
        V, W, info = check_file(p, a.research, a.final, a.keyword)
        total += len(V)
        parts.append(render(p, V, W, info, a.final))
    text = "\n\n".join(parts) + f"\n\n전체 위반 {total}개 · 파일 {len(files)}개"
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
