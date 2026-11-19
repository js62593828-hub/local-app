# -*- coding: utf-8 -*-
"""W싼세계(wp_tips) 받은편지함 → research 변환 도구.

사용:  python tools/wp_inbox_build.py --week 2026-11-23

입력:  pipeline/wp_inbox/<주>/*.txt  ('_' 로 시작하는 파일(_양식·_주간계획 등)은 제품이 아님. 파일 하나 = 상품 하나)
       '쓸 글: 1124 C, 1123 S' (날짜 틀 목록) 또는 옛 형식(키워드 + 쓸 글: 비교/심층/단독)
       _주간계획.txt: 한 줄 = 글 하나 '날짜 틀 | 키워드 | 카테고리 | 메모' → 글별 준비표 + 키워드 연결
출력(같은 폴더 안, 다시 돌리면 덮어씀):
  research/<상품id>.md   상품 자료 (상품id = 쿠팡 주소 products/ 뒤 숫자)
  links.json             {상품id: 파트너스 HTML}  (허용된 것만)
  summary.md             글별 준비표 + 상품별 점검표 + 문제 목록

원칙: 원문에 없는 값은 비워 두고 지어내지 않는다. 쿠팡 사이트에는 접속하지 않는다(파일만 읽음).
앱 데이터(content_queue*·articles*)는 읽지도 쓰지도 않는다.
"""
import argparse
import glob
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTIONS = ["상품", "파트너스 HTML", "상품 정보", "필수 표기정보", "최신순 후기", "낮은 별점 후기", "메모"]
POST_TYPES = ["비교", "심층", "단독"]
ALLOWED_HOSTS = ["link.coupang.com", "coupa.ng", "coupang.com", "coupangcdn.com"]
SPONSOR_WORDS = ["체험단", "제공받", "제공 받", "무상", "협찬", "지원받", "원고료", "광고", "서포터즈", "체험 후기"]
DISCOUNT_WORDS = ["와우", "쿠폰", "카드", "할인가", "즉시할인", "청구할인", "적립", "최대 혜택", "혜택가"]


# ---------- 읽기 ----------
def split_sections(text):
    """=== 제목 === 줄로 나눈다. 반환: {제목: 본문}"""
    out, cur = {}, None
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^\s*===\s*(.+?)\s*===\s*$", line)
        if m:
            cur = m.group(1).strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip("\n") for k, v in out.items()}


def kv_lines(body):
    """'항목: 값' / '항목<TAB>값' / '항목 | 값' 줄을 (항목, 값) 로."""
    rows = []
    for line in body.split("\n"):
        s = line.strip().strip("|").strip()
        if not s:
            continue
        m = re.match(r"^(.+?)\s*(?:\t+|\s\|\s|[:：])\s*(.*)$", s)
        if m:
            rows.append((m.group(1).strip(), m.group(2).strip()))
    return rows


def get_field(body, *names):
    for k, v in kv_lines(body):
        if k.replace(" ", "") in [n.replace(" ", "") for n in names]:
            return v
    return ""


def split_reviews(body):
    """후기 목록 나누기: 번호(1. / 1) / [1])로 시작하는 줄 기준 → 없으면 빈 줄 기준 → 없으면 줄마다."""
    body = body.strip()
    if not body:
        return []
    lines = body.split("\n")
    num_re = re.compile(r"^\s*(?:\[\d+\]|\d+[.)])\s+")
    if sum(1 for l in lines if num_re.match(l)) >= 2:
        items, cur = [], None
        for l in lines:
            if num_re.match(l):
                if cur is not None:
                    items.append(cur)
                cur = num_re.sub("", l, count=1).strip()
            elif cur is not None and l.strip():
                cur += " " + l.strip()
        if cur is not None:
            items.append(cur)
        return [x for x in items if x]
    chunks = [c.strip() for c in re.split(r"\n\s*\n", body) if c.strip()]
    if len(chunks) >= 2:
        return [re.sub(r"\s*\n\s*", " ", c) for c in chunks]
    return [l.strip() for l in lines if l.strip()]


# ---------- 상품 정보 ----------
def to_int(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{3,})\s*원")


def parse_info(body):
    info = {"name": "", "brand": "", "model": "", "rating": "", "reviews": None, "prices": [], "skipped_prices": []}
    info["name"] = get_field(body, "상품명", "제품명", "이름")
    info["brand"] = get_field(body, "브랜드", "제조사")
    info["model"] = get_field(body, "모델명", "모델", "모델번호")
    m = re.search(r"(?:별점|평점)\s*[:：]?\s*([0-5](?:\.\d+)?)", body) or re.search(r"([0-5]\.\d)\s*점", body)
    if m:
        info["rating"] = m.group(1)
    m = re.search(r"(?:리뷰|상품평|후기)\s*(?:수|개수)?\s*[:：]?\s*\(?([\d,]+)\)?\s*개?", body) or re.search(r"([\d,]+)\s*개\s*(?:상품평|리뷰)", body)
    if m:
        info["reviews"] = to_int(m.group(1))
    option = ""
    for line in body.split("\n"):
        s = line.strip()
        if not s:
            continue
        mo = re.match(r"^(?:옵션|선택|구성|규격|용량|색상)\s*[:：]\s*(.+)$", s)
        if mo and not PRICE_RE.search(mo.group(1)):
            option = mo.group(1).strip()
            continue
        mp = PRICE_RE.search(s)
        if not mp:
            continue
        price = mp.group(1) + "원"
        label = s[:mp.start()].strip(" :：-·|\t")
        full = s
        if any(w in full for w in DISCOUNT_WORDS):
            info["skipped_prices"].append({"option": option or label, "label": label or full[:20], "price": price})
            continue
        # 같은 줄에 '옵션 / 일반 판매가: 값' 형태면 앞부분을 옵션으로 본다
        opt = option
        lab = label
        if "/" in lab:
            a, b = lab.rsplit("/", 1)
            opt, lab = a.strip() or option, b.strip()
        if re.search(r"판매가|정가|가격|일반", lab) or not lab:
            info["prices"].append({"option": opt or "(옵션 표시 없음)", "price": price})
        else:
            info["prices"].append({"option": lab if not opt else opt, "price": price})
    return info


# ---------- 파트너스 HTML 검사 ----------
class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors = []
        self.tags = 0

    def _host_ok(self, url):
        u = (url or "").strip()
        if u.startswith("//"):
            u = "https:" + u
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            return False
        h = (p.hostname or "").lower()
        return any(h == d or h.endswith("." + d) for d in ALLOWED_HOSTS)

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        if tag not in ("a", "img"):
            self.errors.append("허용 안 된 태그 <%s>" % tag)
            return
        for k, v in attrs:
            k = (k or "").lower()
            if k.startswith("on"):
                self.errors.append("%s 속성 있음" % k)
            elif k == "style" and re.search(r"url\(|expression", v or "", re.I):
                self.errors.append("style 안에 url()/expression")
            elif (tag == "a" and k == "href") or (tag == "img" and k == "src"):
                if not self._host_ok(v):
                    self.errors.append("허용 안 된 주소 %s=%s" % (k, (v or "")[:60]))
        names = [(k or "").lower() for k, _ in attrs]
        if tag == "a" and "href" not in names:
            self.errors.append("<a> 에 href 없음")
        if tag == "img" and "src" not in names:
            self.errors.append("<img> 에 src 없음")

    handle_startendtag = handle_starttag


def check_html(html):
    """반환: (정상 여부, 오류 목록)"""
    h = (html or "").strip()
    if not h:
        return False, ["HTML 없음"]
    c = _Checker()
    try:
        c.feed(h)
        c.close()
    except Exception as e:  # noqa: BLE001
        return False, ["HTML 읽기 실패: %s" % e]
    if c.tags == 0:
        return False, ["<a>·<img> 태그가 없음"]
    return (not c.errors), c.errors


# ---------- 상품 하나 읽기 ----------
def product_id(url):
    m = re.search(r"products/(\d+)", url or "")
    return m.group(1) if m else ""


def post_types(s):
    """옛 형식 '비교/심층/단독' 이름만 읽는다(날짜+틀은 parse_slots)."""
    return [t for t in POST_TYPES if t in (s or "")]


SLOT_RE = re.compile(r"(?<!\d)(\d{4})\s*([CSD])(?![A-Za-z])", re.I)


def parse_slots(s):
    """'1124 C, 1123 S' → [('1124','C'), ('1123','S')] (쉼표·줄바꿈 모두 허용, 중복 뺌)."""
    out = []
    for m in SLOT_RE.finditer(s or ""):
        k = (m.group(1), m.group(2).upper())
        if k not in out:
            out.append(k)
    return out


def read_plan(week_dir):
    """_주간계획.txt → [{'mmdd','frame','keyword','category','memo'}] (줄 순서). 파일 없으면 []."""
    path = os.path.join(week_dir, "_주간계획.txt")
    if not os.path.isfile(path):
        return []
    plan = []
    with open(path, encoding="utf-8-sig") as f:
        text = f.read().replace("\r\n", "\n")
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [x.strip() for x in s.split("|")]
        slots = parse_slots(parts[0])
        if len(slots) != 1:
            continue
        plan.append({"mmdd": slots[0][0], "frame": slots[0][1], "keyword": parts[1] if len(parts) > 1 else "",
                     "category": parts[2] if len(parts) > 2 else "", "memo": parts[3] if len(parts) > 3 else ""})
    return plan


def parse_file(path):
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    sec = split_sections(text)
    head = sec.get("상품", "")
    d = {
        "file": os.path.basename(path),
        "keyword": get_field(head, "키워드"),
        "url": get_field(head, "쿠팡 주소", "쿠팡주소", "주소"),
        "date": get_field(head, "수집일"),
        "types": post_types(get_field(head, "쓸 글", "쓸글")),
        "slots": parse_slots(get_field(head, "쓸 글", "쓸글")),
        "html": sec.get("파트너스 HTML", "").strip(),
        "info": parse_info(sec.get("상품 정보", "")),
        "must": kv_lines(sec.get("필수 표기정보", "")),
        "latest": split_reviews(sec.get("최신순 후기", "")),
        "low": split_reviews(sec.get("낮은 별점 후기", "")),
        "memo": sec.get("메모", "").strip(),
    }
    d["pid"] = product_id(d["url"])
    return d


def is_sponsor(t):
    return any(w in t for w in SPONSOR_WORDS)


# ---------- research 파일 ----------
def slot_labels(p, plan):
    """'1124 C 가열식 가습기 추천' 목록. 계획에 없으면 날짜·틀만."""
    kw = {(x["mmdd"], x["frame"]): x["keyword"] for x in plan}
    return ["%s %s%s" % (m, f, (" " + kw[(m, f)]) if kw.get((m, f)) else "") for m, f in p["slots"]]


def render_research(p, plan=()):
    i = p["info"]
    L = ["# 상품 자료 %s" % (p["pid"] or "(상품id 없음)"), ""]
    if p["slots"]:
        L += ["- 쓸 글: %s" % " / ".join(slot_labels(p, plan))]
        if p["keyword"]:
            L += ["- 키워드: %s" % p["keyword"]]
    else:
        L += ["- 키워드: %s" % p["keyword"], "- 쓸 글: %s" % " / ".join(p["types"])]
    L += ["- 쿠팡 주소: %s" % p["url"], "- 수집일: %s" % p["date"], ""]
    L += ["## 상품", "", "- 상품명: %s" % i["name"], "- 브랜드: %s" % i["brand"], "- 모델명: %s" % i["model"],
          "- 별점: %s" % i["rating"], "- 리뷰 수: %s" % ("" if i["reviews"] is None else "{:,}".format(i["reviews"])), ""]
    L += ["## 옵션별 일반 판매가", ""]
    if i["prices"]:
        L += ["| 옵션 | 일반 판매가 |", "|---|---|"] + ["| %s | %s |" % (x["option"], x["price"]) for x in i["prices"]]
    else:
        L += ["(원문에 일반 판매가 없음)"]
    if i["skipped_prices"]:
        L += ["", "와우·쿠폰·카드 등 할인가 — **쓰지 않음**:", ""]
        L += ["- %s %s: %s (쓰지 않음)" % (x["option"], x["label"], x["price"]) for x in i["skipped_prices"]]
    L += ["", "## 필수 표기정보 (%d개 항목)" % len(p["must"]), ""]
    if p["must"]:
        L += ["| 항목 | 값 |", "|---|---|"] + ["| %s | %s |" % (k, v.replace("|", "/")) for k, v in p["must"]]
    else:
        L += ["(원문에 없음)"]
    for title, key in (("최신순 후기", "latest"), ("낮은 별점 후기", "low")):
        revs = p[key]
        L += ["", "## %s (%d개)" % (title, len(revs)), ""]
        if not revs:
            L += ["(원문에 없음)"]
        for n, t in enumerate(revs, 1):
            L.append("%d. %s%s" % (n, "[제외 후보] " if is_sponsor(t) else "", t))
    L += ["", "## 메모", "", p["memo"] or "(없음)", ""]
    return "\n".join(L)


# ---------- 합치기·점검 ----------
def build(week_dir, plan=()):
    files = sorted(f for f in glob.glob(os.path.join(week_dir, "*.txt")) if not os.path.basename(f).startswith("_"))
    plan_keys = {(x["mmdd"], x["frame"]) for x in plan}
    prods, problems_global = [], []
    by_pid, no_url = {}, []
    for f in files:
        p = parse_file(f)
        p["problems"] = []
        if not p["pid"]:
            p["problems"].append("주소 없음")
            no_url.append(p)
            continue
        if p["pid"] in by_pid:
            first = by_pid[p["pid"]]
            for t in p["types"]:
                if t not in first["types"]:
                    first["types"].append(t)
            for sl in p["slots"]:
                if sl not in first["slots"]:
                    first["slots"].append(sl)
            first["problems"].append("같은 상품id 중복(%s, 쓸 글 합침)" % p["file"])
            continue
        by_pid[p["pid"]] = p
        prods.append(p)
    prods += no_url
    for p in prods:
        ok, errs = check_html(p["html"])
        p["link_ok"] = ok
        if not ok:
            p["problems"].append("HTML 없음" if errs == ["HTML 없음"] else "링크 오류: " + "; ".join(sorted(set(errs))[:3]))
        if not p["info"]["name"]:
            p["problems"].append("상품명 없음")
        if not p["types"] and not p["slots"]:
            p["problems"].append("쓸 글 표시 없음")
        if p["slots"] and plan:
            for m, f in p["slots"]:
                if (m, f) not in plan_keys:
                    p["problems"].append("계획에 없는 글 '%s %s'" % (m, f))
        elif p["slots"] and not plan:
            p["problems"].append("_주간계획.txt 가 없어 '쓸 글' 날짜를 확인 못 함")
        if p["slots"]:  # 새 형식: 글마다 키워드는 계획에서
            p["keyword"] = " / ".join(dict.fromkeys(
                x["keyword"] for x in plan if (x["mmdd"], x["frame"]) in p["slots"] and x["keyword"]))
        # 제품별 경고(새 형식)
        if len(p["slots"]) >= 4:
            p["problems"].append("경고: 같은 제품이 %d편(3편까지)" % len(p["slots"]))
        if sum(1 for _, f in p["slots"] if f == "S") >= 2:
            p["problems"].append("경고: 같은 제품 S(단독 추천) %d편(한 주에 1편까지)" % sum(1 for _, f in p["slots"] if f == "S"))
        days = {}
        for m, f in p["slots"]:
            days.setdefault(m, []).append(f)
        for m, fs in days.items():
            if len(fs) >= 2:
                p["problems"].append("경고: %s 같은 날 두 틀(%s)에 같은 제품" % (m, "·".join(fs)))
        n_latest, n_low = len(p["latest"]), len(p["low"])
        if "심층" in p["types"] and (n_latest < 20 or n_low < 10):
            p["problems"].append("'심층'인데 최신순 %d개(20↑ 필요)·낮은 별점 %d개(10↑ 필요)" % (n_latest, n_low))
        if "단독" in p["types"] and n_latest < 20:
            p["problems"].append("'단독'인데 최신순 %d개(20↑ 필요)" % n_latest)
    # 비교: 키워드별 상품 2개 미만
    cmp_count = {}
    for p in prods:
        if "비교" in p["types"]:
            cmp_count.setdefault(p["keyword"], []).append(p)
    for kw, lst in cmp_count.items():
        if len(lst) < 2:
            for p in lst:
                p["problems"].append("'비교' 키워드 '%s' 상품이 %d개(2개↑ 필요)" % (kw, len(lst)))
    return prods


def plan_table(plan, prods):
    """주간계획 줄 순서대로 글별 준비 상태 표(마크다운 줄 목록)."""
    L = ["## 글별 준비표", ""]
    if not plan:
        return L + ["(_주간계획.txt 가 없거나 글 줄이 없음)", ""]
    L += ["| 날짜 | 틀 | 키워드 | 들어온 제품 | 준비 상태 | 모자란 것 |", "|---|---|---|---|---|---|"]
    for x in plan:
        key = (x["mmdd"], x["frame"])
        ps = [p for p in prods if key in p["slots"]]
        names = " / ".join((p["info"]["name"] or p["pid"] or p["file"]) for p in ps)
        miss = []
        if not ps:
            state = "제품 없음"
        elif x["frame"] == "C":
            brands = {p["info"]["brand"] for p in ps if p["info"]["brand"]}
            if len(ps) < 2:
                miss.append("제품 %d개(2개 필요)" % len(ps))
            if len(brands) < 2:
                miss.append("브랜드 %d개(2개 필요)" % len(brands))
            nrev = [len(p["latest"]) for p in ps]
            if any(nrev):
                miss.append("(참고) 후기 " + "·".join(str(n) for n in nrev) + "개")
            state = "준비됨" if len(ps) >= 2 and len(brands) >= 2 else "모자람"
            if state == "준비됨":
                miss = miss  # 후기 개수 참고만
        else:
            if len(ps) != 1:
                miss.append("제품 %d개(정확히 1개 필요)" % len(ps))
            if len(ps) == 1:
                p = ps[0]
                n_ok = sum(1 for t in p["latest"] if not is_sponsor(t))
                if n_ok < 20:
                    miss.append("최신순 후기 %d개(체험단 뺀 수, 20개 필요)" % n_ok)
                if x["frame"] == "D" and len(p["low"]) < 10:
                    miss.append("낮은 별점 %d개(10개 필요)" % len(p["low"]))
            ok = len(ps) == 1 and not [m for m in miss if not m.startswith("(참고)")]
            state = "준비됨" if ok else "모자람"
        cells = [x["mmdd"], x["frame"], x["keyword"], names, state, " / ".join(miss) or "없음"]
        L.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    return L + [""]


def write_outputs(week_dir, prods, plan=()):
    rdir = os.path.join(week_dir, "research")
    os.makedirs(rdir, exist_ok=True)
    for old in glob.glob(os.path.join(rdir, "*.md")):  # 다시 돌리면 덮어씀(옛 결과가 남지 않게)
        os.remove(old)
    links = {}
    for p in prods:
        if not p["pid"]:
            continue
        with open(os.path.join(rdir, p["pid"] + ".md"), "w", encoding="utf-8") as f:
            f.write(render_research(p, plan))
        if p["link_ok"]:
            links[p["pid"]] = p["html"]
    with open(os.path.join(week_dir, "links.json"), "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=1)
    L = ["# 받은편지함 변환 결과", ""] + plan_table(plan, prods)
    L += ["## 상품별 점검표", "", "상품 %d개 · 문제 있는 상품 %d개" % (len(prods), sum(1 for p in prods if p["problems"])), "",
         "| 상품id | 키워드 | 쓸 글 | 상품명 | 가격 있음 | 별점 | 리뷰 수 | 표기정보 항목 수 | 최신순 후기 수 | 낮은 별점 후기 수 | 링크 정상 | 문제 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in prods:
        i = p["info"]
        cells = [p["pid"] or "(없음)", p["keyword"], "·".join(p["types"] + ["%s %s" % sl for sl in p["slots"]]), i["name"], "예" if i["prices"] else "아니오", i["rating"],
                 "" if i["reviews"] is None else "{:,}".format(i["reviews"]), len(p["must"]), len(p["latest"]), len(p["low"]),
                 "예" if p["link_ok"] else "아니오", " / ".join(p["problems"]) or "없음"]
        L.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    L.append("")
    with open(os.path.join(week_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return links


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", required=True, help="주 시작일 (예: 2026-11-23)")
    ap.add_argument("--dir", help="받은편지함 폴더를 직접 지정(시험용). 기본: pipeline/wp_inbox/<주>")
    a = ap.parse_args()
    week_dir = a.dir or os.path.join(ROOT, "pipeline", "wp_inbox", a.week)
    if not os.path.isdir(week_dir):
        sys.exit("폴더가 없습니다: %s" % week_dir)
    plan = read_plan(week_dir)
    prods = build(week_dir, plan)
    links = write_outputs(week_dir, prods, plan)
    bad = [p for p in prods if p["problems"]]
    print("상품 %d개 → research %d개, links.json %d개, 문제 있는 상품 %d개" % (len(prods), sum(1 for p in prods if p["pid"]), len(links), len(bad)))
    for p in bad:
        print(" -", p["pid"] or p["file"], ":", " / ".join(p["problems"]))
    print("저장:", week_dir)


if __name__ == "__main__":
    main()
