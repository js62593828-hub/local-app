#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""N살림노트 원고 검사 도구 (읽기만 한다. 파일을 고치지 않는다).

사용: python tools/salim_check.py --week 2026-11-23
      python tools/salim_check.py --file pipeline/salim_drafts/2026-11-23/01_....md
기준 문서: docs/살림노트/ (원고지침_네이버_AB·C V5.2·글뼈대규칙 V2.2·태그규칙). 기준 숫자는 아래 STD·HOME 표 한 곳.
홈판 글인지는 원고 ⓪ 의 '홈판 글: 예/아니오' 줄을 읽는다(없으면 ✖).
"""
import argparse
import glob
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

META_NAMES = [
    "메타 디스크립션", "카테고리 제안", "글 유형", "태그 제안", "표지 배지",
    "표지 1줄", "표지 2줄", "표지 보조", "표지 장면",
    "대표 이미지(표지) alt 텍스트 제안", "본문 이미지 alt 텍스트 제안",
]

# 원고지침_네이버_AB.md [피할 표현] 6번: 0개여야 하는 것
ZERO_PATTERNS = [
    ("갈리/가르 계열", r"갈리|갈린|갈려|갈립|갈릴|갈렸|가르(?!치|쳐|침)|가른|가릅"),
    ("점이 가장 큰", r"점이 가장 큰"),
    ("쓸모", r"쓸모"),
    ("구조예요/구조입니다", r"구조예요|구조입니다"),
    ("지점", r"지점"),
    ("정답", r"정답"),
]
# 횟수 제한 (이름, 정규식, 최대)
LIMIT_PATTERNS = [
    ("핵심", r"핵심", 1),
    ("셈", r"인 셈|셈이에요|셈입니다|셈이죠|셈이다", 1),
    ("것이 좋습니다/게 좋아요", r"것이 좋습니다|게 좋아요", 2),
    ("에 따라 달라", r"에 따라 달라", 2),
]
PRICE_PATTERNS = [r"\d[\d,]*\s*원", r"만\s*원대", r"\d+\s*%\s*할인"]
# 경험 금지어: 글 전체에서 0개여야 하는 것 (원고지침 AB [운영자 경험] 6번·[금지])
EXP_BAN = ["전원주택", "단독주택", "시골집", "마당", "텃밭", "정화조", "써 보니", "써봤"]
EXP_BAN_PATTERNS = [("등유 보일러", r"등유\s*보일러"), ("예전에 아파트 살 때", r"예전에\s*아파트\s*살\s*때")]
# 키워드 자체가 이 말일 수 있어(예: 제설) 경험 문장(④-1)에서만 0개
EXP_BAN_EXPERIENCE_ONLY = ["제설"]

# 요약 블록 소제목 이름 (글뼈대규칙 3장)
SUMMARY_NAMES = ["먼저 답부터 드릴게요", "급하신 분은 이것만 보세요", "이것만 기억해 두세요", "한 줄씩 먼저 정리할게요"]

# ===== 기준 숫자: 여기 한 곳에서만 고친다 =====
# 원고지침_네이버_AB·C V5.2 + 글뼈대규칙 V2.2 (2026-11-23). 문서가 바뀌면 이 표만 맞춘다.
STD = {  # 일반 글 (A·B·C)
    "chars": (4000, 4600), "bold": (8, 12), "quote": (2, 3), "faq": (3, 4),
    "heads": {"A": (5, 7), "B": (5, 7), "C": (5, 8)},
}
HOME = {  # 홈판 실험 글 (AB [홈판 실험 글] 블록)
    "chars": (2500, 3200), "bold": (5, 8), "quote": (1, 2), "faq": (3, 3), "heads": (0, 4),
}
PARA_MAX = 80    # 문단 하나 공백 제외 글자 수 상한
PHOTOS = 4       # 본문 사진 장수(고를 섹션이 모자라면 있는 만큼)


def code_blocks(text):
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S)


def find_blocks(text):
    body = meta = None
    for b in code_blocks(text):
        if body is None and "[플랫폼:네이버]" in b:
            body = b
        elif meta is None and "메타 디스크립션:" in b:
            meta = b
    return body, meta


def confirmed_links(week):
    """확정 목록에서 키워드별 제품 링크(https://naver.me/...)를 모은다."""
    path = os.path.join(ROOT, "Claude outputs", "weekly_final_%s.md" % week)
    out = {}
    if not os.path.exists(path):
        return out, path
    cur = None
    for line in open(path, encoding="utf-8"):
        m = re.match(r"####\s+N\d+\s+(\S+)", line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
            continue
        if line.startswith("## ") or line.startswith("### "):
            cur = None
        if cur:
            for u in re.findall(r"https?://\S+", line):
                out[cur].append(u.rstrip(")，,"))
    return out, path


def home_flag(text):
    """⓪ 구역(코드 블록 밖)의 '홈판 글: 예/아니오' 줄을 읽는다. 없으면 None."""
    outside = re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.S)
    m = re.search(r"^\s*[-*]?\s*홈판 글:\s*(예|아니오)", outside, flags=re.M)
    return None if not m else (m.group(1) == "예")


def long_paragraphs(lines):
    """문단(이어진 줄 묶음) 중 공백 제외 PARA_MAX 자를 넘는 것. 제목·표·목록·인용구·Q:·링크: 줄은 뺀다."""
    out, cur = [], []

    def flush():
        if cur:
            s = re.sub(r"\s+", "", re.sub(r"^A:\s*", "", "".join(x.strip() for x in cur)).replace("**", ""))
            if len(s) > PARA_MAX:
                out.append((len(s), s[:20]))
        cur.clear()

    for ln in lines:
        s = ln.strip()
        if not s:
            flush()
            continue
        if re.match(r"^(#|\||>|[-*]\s|\d+\.\s|Q:|링크:|\{\{SHOP:|---)", s):
            flush()
            continue
        cur.append(s)
    flush()
    return out


def strip_md(line):
    line = line.replace("**", "")
    line = re.sub(r"^\s*(#+\s*|>\s*|[-*]\s+)", "", line)
    return line


def body_parts(body):
    """본문 코드 블록을 줄 단위로 나눈다. (제목 줄 이후 본문, 태그 줄, 마무리 인사 문단 분리)"""
    lines = body.split("\n")
    # 첫 줄 [플랫폼:네이버], 제목: 줄 제거
    rest = []
    title = ""
    for ln in lines:
        if ln.strip() == "[플랫폼:네이버]":
            continue
        if ln.startswith("제목:") and not title:
            title = ln[3:].strip()
            continue
        rest.append(ln)
    tag_line = ""
    for i in range(len(rest) - 1, -1, -1):
        if rest[i].strip().startswith("태그:"):
            tag_line = rest[i].strip()
            rest = rest[:i]
            break
    # 마지막 문단(마무리 인사) 분리
    while rest and not rest[-1].strip():
        rest.pop()
    greet = []
    while rest and rest[-1].strip():
        greet.insert(0, rest.pop())
    return title, rest, greet, tag_line


def experience_sentences(text):
    """코드 블록 밖 '④-1' 구역에서 경험 문장(글머리 줄)을 뽑는다."""
    out = []
    on = False
    skip = ("피할 표현", "전원주택", "실제 경험과 다르면", "다음 글 예고", "예고")
    for ln in text.split(chr(10)):
        if "④-1" in ln:
            on = True
            continue
        if on and re.match(r"^\s*(⑤|⑥|④\s|\[스레드\]|---|#)", ln):
            break
        if on and ln.strip().startswith(("-", "*", ">", "\"", "“")):
            t = ln.strip().lstrip("-*> ").strip()
            if t and not any(k in t for k in skip):
                out.append(t)
    return out


def grams(t):
    t = re.sub(r"\s+", "", t)
    return {t[i:i + 3] for i in range(len(t) - 2)}


def check_day_overlap(infos):
    """같은 발행 날짜(MMDD) 글끼리: 같은 소제목 문구, 겹치는 경험 장면. 반환 {파일명: [메시지]}"""
    out = {}
    names = sorted(infos)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if infos[a]["day"] != infos[b]["day"]:
                continue
            same = set(infos[a].get("heads", [])) & set(infos[b].get("heads", []))
            for h in same:
                for x in (a, b):
                    out.setdefault(x, []).append("같은 날짜 소제목 겹침 '%s' (%s ↔ %s)" % (h, a[:2], b[:2]) )
            for ea in infos[a].get("exp", []):
                for eb in infos[b].get("exp", []):
                    ga, gb = grams(ea), grams(eb)
                    if ga and gb and len(ga & gb) / float(len(ga | gb)) >= 0.35:
                        for x in (a, b):
                            out.setdefault(x, []).append("경험 장면 겹침 (%s ↔ %s): '%s'" % (a[:2], b[:2], ea[:30]))
    return out


def check_file(path, links_by_kw, info=None):
    info = info if info is not None else {}
    name = os.path.basename(path)
    text = open(path, encoding="utf-8").read()
    res = []   # (항목, 결과문자열, 위반여부)

    def add(item, msg, ok):
        res.append((item, msg, not ok))

    m = re.match(r"(\d+)_(\d{4})_(\d{4})_([ABC])_(.+)\.md$", name)
    typ = m.group(4) if m else "?"
    kw = m.group(5) if m else ""
    if not m:
        add("파일 이름", "NN_MMDD_HHMM_유형_키워드.md 형식 아님", False)

    body, meta = find_blocks(text)
    if body is None:
        add("본문 코드 블록", "[플랫폼:네이버] 로 시작하는 코드 블록 없음", False)
        return name, typ, res
    if meta is None:
        add("메타 코드 블록", "'메타 디스크립션:' 이 든 코드 블록 없음", False)
    meta = meta or ""

    title, content, greet, tag_line = body_parts(body)
    heads = [l.strip() for l in content if l.startswith("## ")]

    # 유형·기준 고르기: 제품 링크가 있는 글(확정 목록 링크 또는 파일 이름 C)은 C, 홈판 글은 홈판 기준
    home = home_flag(text)
    has_conf_link = bool(links_by_kw.get(kw))
    if has_conf_link and typ != "C":
        add("유형", "확정 목록에 제품 링크가 있는데 파일 이름 유형이 %s (C 로 써야 함)" % typ, False)
    eff = "C" if (typ == "C" or has_conf_link) else typ
    if home is None:
        add("홈판 표시", "홈판 표시 없음 (⓪ 에 '홈판 글: 예/아니오' 줄이 없음, 일반 기준으로 검사)", False)
    crit = HOME if home else STD
    info["home"] = bool(home)

    # 1) 분량
    counted = []
    for ln in content:
        s = ln.strip()
        if s.startswith("링크:") or s.startswith("{{SHOP:"):
            continue
        counted.append(strip_md(ln))
    n = len(re.sub(r"\s+", "", "".join(counted)))
    lo, hi = crit["chars"]
    add("분량(공백 제외)", "%d자 (%s~%s)" % (n, format(lo, ","), format(hi, ",")), lo <= n <= hi)

    # 2) 소제목 수
    main_heads = []
    for i, ln in enumerate(content):
        if not ln.startswith("## "):
            continue
        if "자주 묻는 질문" in ln:
            continue
        nxt = ""
        for j in range(i + 1, len(content)):
            if content[j].strip():
                nxt = content[j].strip()
                break
        if not main_heads and (nxt.startswith("- ✅") or ln[3:].strip() in SUMMARY_NAMES):
            continue  # 요약 블록 소제목
        main_heads.append(ln.strip())
    hlo, hhi = crit["heads"] if home else crit["heads"].get(eff, (5, 7))
    add("소제목 수", "본문 %d개 (요약·FAQ 제외, 전체 ## %d개) / %s" % (
        len(main_heads), len(heads), ("최대 %d" % hhi) if home else "%d~%d" % (hlo, hhi)),
        hlo <= len(main_heads) <= hhi)

    # 3) FAQ
    fi = None
    for i, ln in enumerate(content):
        if ln.strip() == "## 자주 묻는 질문":
            fi = i
    if fi is None:
        add("FAQ", "'## 자주 묻는 질문' 소제목 없음", False)
    else:
        faq = []
        for ln in content[fi + 1:]:
            if ln.startswith("## "):
                break
            faq.append(ln)
        q = sum(1 for l in faq if l.startswith("Q:"))
        a = sum(1 for l in faq if l.startswith("A:"))
        flo, fhi = crit["faq"]
        add("FAQ", "Q %d · A %d (%s쌍)" % (q, a, ("%d" % flo) if flo == fhi else "%d~%d" % (flo, fhi)),
            q == a and flo <= q <= fhi)

    # 4) 태그
    tags = re.findall(r"#\S+", tag_line)
    add("태그 수", "%d개 (10~12)" % len(tags), tag_line.startswith("태그:") and 10 <= len(tags) <= 12)

    # 5) 피할 표현 (본문+메타 전체, 제목 포함)
    allt = title + "\n" + "\n".join(content) + "\n" + "\n".join(greet) + "\n" + tag_line + "\n" + meta
    allt_z = allt.replace("헷갈", "헷XX")
    bad = []
    for label, pat in ZERO_PATTERNS:
        for mm in re.finditer(pat, allt_z):
            s = max(0, mm.start() - 8)
            bad.append("%s '%s'" % (label, allt_z[s:mm.end() + 8].replace("\n", " ")))
    add("피할 표현 0개", "0개" if not bad else "; ".join(bad), not bad)

    # 6) 횟수 제한 말
    over = []
    cnts = []
    for label, pat, mx in LIMIT_PATTERNS:
        c = len(re.findall(pat, allt))
        cnts.append("%s %d/%d" % (label, c, mx))
        if c > mx:
            over.append("%s %d번(최대 %d)" % (label, c, mx))
    cnts.append("통해 %d(참고)" % len(re.findall(r"통해", allt)))
    add("횟수 제한 말", ("초과: " + "; ".join(over)) if over else " · ".join(cnts), not over)

    # 7) 인용구·굵게
    # 제품 섹션(소제목 바로 다음 줄이 링크:)의 인용구는 개수에 넣지 않는다(C 지침)
    product_idx = set()
    cur_prod = False
    for i, ln in enumerate(content):
        if ln.startswith("## "):
            nxt = next((content[j].strip() for j in range(i + 1, len(content)) if content[j].strip()), "")
            cur_prod = nxt.startswith("링크:")
        if cur_prod:
            product_idx.add(i)
    quotes = 0
    prev = False
    for i, ln in enumerate(content):
        is_q = ln.startswith("> ") and i not in product_idx
        if is_q and not prev:
            quotes += 1
        prev = is_q
    bolds = len(re.findall(r"\*\*[^*\n]+\*\*", "\n".join(content)))
    qlo, qhi = crit["quote"]
    blo, bhi = crit["bold"]
    add("인용구", "%d개 (%d~%d)" % (quotes, qlo, qhi), qlo <= quotes <= qhi)
    add("굵게", "%d개 (%d~%d)" % (bolds, blo, bhi), blo <= bolds <= bhi)

    # 문단 80자
    longp = long_paragraphs(content + [""] + greet)
    add("80자 넘는 문단", "%d개 (0, 상한 %d자)%s" % (len(longp), PARA_MAX,
        (": " + "; ".join("%d자 '%s…'" % x for x in longp[:5])) if longp else ""), not longp)

    # 8) 본문 속 http
    https = re.findall(r"https?://\S+", "\n".join(l for l in content + greet if not l.strip().startswith("링크:")))
    add("본문 http", "0개" if not https else "%d개: %s" % (len(https), https[0]), not https)

    # 9) 링크 줄
    link_idx = [i for i, l in enumerate(content) if l.strip().startswith("링크:")]
    misplaced = [content[i].strip() for i in link_idx if not (i > 0 and content[i - 1].startswith("## "))]
    if home:
        add("링크 줄", "홈판 글에 %d개 (0이어야 함)" % len(link_idx), not link_idx)
    elif eff in ("A", "B"):
        add("링크 줄", "A·B 에 %d개 (0이어야 함)" % len(link_idx), not link_idx)
    else:
        add("링크 줄 위치", "%d개, 소제목 바로 다음 줄 아님 %d개" % (len(link_idx), len(misplaced)),
            bool(link_idx) and not misplaced)
        conf = links_by_kw.get(kw, [])
        used = [content[i].strip()[3:].strip() for i in link_idx]
        if not conf:
            add("C 링크=확정 목록", "확정 목록에서 '%s' 링크를 못 찾음" % kw, False)
        else:
            missing = [u for u in conf if u not in used]
            extra = [u for u in used if u not in conf]
            add("C 링크=확정 목록", "일치" if not missing and not extra else
                "빠짐 %s / 다른 링크 %s" % (missing, extra), not missing and not extra)

    # 10) 가격
    prices = []
    for pat in PRICE_PATTERNS:
        prices += [mm.group(0) for mm in re.finditer(pat, allt)]
    add("가격 패턴", "0개" if not prices else "%d개: %s" % (len(prices), ", ".join(prices[:5])), not prices)

    # 11) 경험 금지어
    hit = [w for w in EXP_BAN if w in allt]
    hit += [lab for lab, pat in EXP_BAN_PATTERNS if re.search(pat, allt)]
    exp_sents = experience_sentences(text)
    hit += ["%s(경험 문장)" % w for w in EXP_BAN_EXPERIENCE_ONLY if any(w in s for s in exp_sents)]
    add("경험 금지어", ("0개 (경험 문장 %d개)" % len(exp_sents)) if not hit else "있음: " + ", ".join(hit), not hit)

    # 12) 메타 항목 이름
    miss = [n_ for n_ in META_NAMES if (n_ + ":") not in meta]
    add("메타 항목 이름", "모두 있음" if not miss else "없음: " + ", ".join(miss), not miss)

    # 13) 표지 1줄·2줄
    for key in ("표지 1줄", "표지 2줄"):
        mm = re.search(r"^" + re.escape(key) + r":\s*(.*)$", meta, flags=re.M)
        if not mm:
            add(key, "없음", False)
            continue
        val = mm.group(1).strip()
        br = len(re.findall(r"\[[^\]]+\]", val))
        plain = re.sub(r"[\[\]]", "", val)
        ok = len(plain) <= 12 and br == 1
        add(key, "'%s' 길이 %d(≤12, 대괄호 뺀 글자)·대괄호 %d개(1)" % (val, len(plain), br), ok)

    # 14) 본문 이미지 alt
    ai = meta.find("본문 이미지 alt 텍스트 제안:")
    if ai >= 0:
        alts = []
        for ln in meta[ai:].split("\n")[1:]:
            mm = re.match(r'^\s*["“]?(##\s*.+?)["”]?\s*→', ln)
            if mm:
                alts.append(mm.group(1).strip())
            elif ln.strip().startswith("출처 캡처:"):
                break
        bad = [a for a in alts if a not in heads]
        # 고를 수 있는 섹션(제품 섹션·요약·FAQ 제외)이 PHOTOS 보다 적으면 있는 만큼
        prod_heads = {content[i].strip() for i in product_idx if content[i].startswith("## ")}
        n_pick = len([h for h in main_heads if h not in prod_heads])
        want = min(PHOTOS, n_pick)
        add("본문 사진(alt 줄)", "%d장(%d), 원고 ## 와 다른 것 %d개%s" %
            (len(alts), want, len(bad), (": " + "; ".join(bad)) if bad else ""),
            len(alts) == want and not bad)
    info["heads"] = [h[3:].strip() for h in heads if "자주 묻는 질문" not in h]
    info["exp"] = experience_sentences(text)
    return name, typ, res


def main():
    ap = argparse.ArgumentParser(description="N살림노트 원고 검사(읽기만)")
    ap.add_argument("--week", help="예: 2026-11-23")
    ap.add_argument("--file", help="파일 하나만 검사")
    a = ap.parse_args()
    if not a.week and not a.file:
        ap.error("--week 또는 --file 이 필요합니다")
    if a.file:
        files = [a.file]
        week = a.week or re.sub(r".*salim_drafts[\\/](\d{4}-\d{2}-\d{2}).*", r"\1", a.file.replace("\\", "/"))
    else:
        week = a.week
        folder = os.path.join(ROOT, "pipeline", "salim_drafts", week)
        files = sorted(f for f in glob.glob(os.path.join(folder, "*.md"))
                       if re.match(r"\d+_\d{4}_\d{4}_[ABC]_", os.path.basename(f)))
    if not files:
        print("검사할 원고가 없습니다.")
        return 1
    links, lpath = confirmed_links(week)
    homes = []
    for f in files:
        try:
            if home_flag(open(f, encoding="utf-8").read()):
                mh = re.match(r"\d+_\d{4}_\d{4}_[ABC]_(.+)\.md$", os.path.basename(f))
                homes.append(mh.group(1) if mh else os.path.basename(f))
        except OSError:
            pass
    print("홈판 글 %d편%s" % (len(homes), ("(" + ", ".join(homes) + ")") if homes else ""))
    if not links:
        print("(참고) 확정 목록 링크를 읽지 못했습니다: %s" % lpath)

    summary = []
    infos = {}
    results = []
    for f in files:
        info = {}
        name, typ, res = check_file(f, links, info)
        mm = re.match(r"\d+_(\d{4})_", name)
        info["day"] = mm.group(1) if mm else ""
        infos[name] = info
        results.append((name, typ, res))
    overlap = check_day_overlap(infos)
    for name, typ, res in results:
        for msg in overlap.get(name, []):
            res.append(("같은 날짜 겹침", msg, True))
        f = name
        print("\n### %s (유형 %s)" % (name, typ))
        print("| 항목 | 결과 | 판정 |")
        print("|---|---|---|")
        for item, msg, viol in res:
            print("| %s | %s | %s |" % (item, msg.replace("|", "/"), "✖" if viol else "✔"))
        viols = [(i, m) for i, m, v in res if v]
        for i, m in viols:
            print("✖ %s: %s" % (i, m))
        summary.append((name, len(viols)))
    print("\n### 요약")
    print("| 파일 | 위반(✖) |")
    print("|---|---|")
    for n, c in summary:
        print("| %s | %d |" % (n, c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
