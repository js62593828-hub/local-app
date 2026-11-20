# -*- coding: utf-8 -*-
"""W싼세계(wp_tips) 받은편지함 → research 변환 도구.

사용:  python tools/wp_inbox_build.py --week 2026-11-23

입력:  pipeline/wp_inbox/<주>/_제품표_*.tsv  (v1.6 제품표: 탭 구분, 한 줄 = 제품 하나, 칸 안 줄바꿈은 큰따옴표) 와/또는
       pipeline/wp_inbox/<주>/*.txt  ('_' 로 시작하는 파일(_양식·_주간계획 등)은 제품이 아님. 파일 하나 = 상품 하나)
       _공식사양.json(있으면): {상품id: {"brand","model","items":[{"항목","값","출처","확인일"}],"note"}} — 제조사 공식 사양(research 에 합침)
       '쓸 글: 1124 C, 1123 S' (날짜 틀 목록) 또는 옛 형식(키워드 + 쓸 글: 비교/심층/단독)
       _주간계획.txt: 한 줄 = 글 하나 '날짜 틀 | 키워드 | 카테고리 | 메모' → 글별 준비표 + 키워드 연결
출력(같은 폴더 안, 다시 돌리면 덮어씀):
  research/<상품id>.md   상품 자료 (상품id = 쿠팡 주소 products/ 뒤 숫자)
  links.json             {상품id: {"link": 일반 링크, "html": 링크카드 HTML}}  (둘 다 정상인 것만)
  summary.md             글별 준비표 + 상품별 점검표 + 문제 목록

원칙: 원문에 없는 값은 비워 두고 지어내지 않는다. 쿠팡 사이트에는 접속하지 않는다(파일만 읽음).
앱 데이터(content_queue*·articles*)는 읽지도 쓰지도 않는다.
"""
import argparse
import csv
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
from urllib.parse import urlparse

csv.field_size_limit(10 ** 8)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTIONS = ["상품", "파트너스 링크", "파트너스 HTML", "상품 정보", "필수 표기정보", "최신순 후기", "낮은 별점 후기", "메모"]
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
        self.in_iframe = False

    def _host_ok(self, url):
        u = (url or "").strip()
        if u.startswith("//"):
            u = "https:" + u
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            return False
        h = (p.hostname or "").lower()
        return any(h == d or h.endswith("." + d) for d in ALLOWED_HOSTS)

    IFRAME_ATTRS = {"src", "width", "height", "frameborder", "scrolling", "referrerpolicy"}

    def handle_data(self, data):
        if self.in_iframe and data.strip():
            self.errors.append("iframe 안에 글자가 있음")

    def handle_endtag(self, tag):
        if tag == "iframe":
            self.in_iframe = False

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        if tag == "iframe":  # 쿠팡파트너스 링크카드: src 가 https://coupa.ng/ 이고 속성이 6개 안일 때만
            self.in_iframe = True
            for k, v in attrs:
                k = (k or "").lower()
                if k not in self.IFRAME_ATTRS:
                    self.errors.append("iframe 에 허용 안 된 속성 %s" % k)
                elif k == "src" and not (v or "").startswith("https://coupa.ng/"):
                    self.errors.append("iframe src 가 https://coupa.ng/ 로 시작하지 않음: %s" % (v or "")[:60])
            if "src" not in [(k or "").lower() for k, _ in attrs]:
                self.errors.append("iframe 에 src 없음")
            return
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


PLAIN_HOSTS = ["link.coupang.com", "coupa.ng"]


def check_plain_link(u):
    """일반 링크 검사. 반환 (정상 여부, 문제 문장)."""
    u = (u or "").strip()
    if not u:
        return False, "일반 링크 없음"
    if re.search(r"\s", u):
        return False, "일반 링크에 공백이 있음"
    pr = urlparse(u)
    h = (pr.hostname or "").lower()
    if pr.scheme != "https" or not any(h == d for d in PLAIN_HOSTS):
        return False, "일반 링크 주소가 허용 안 됨: " + u[:50]
    return True, ""


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
    # 열쇠: 같은 날 같은 틀이 하나면 (날짜, 틀), 둘 이상이면 (날짜, 틀, 키워드)
    cnt, seq = {}, {}
    for x in plan:
        cnt[(x["mmdd"], x["frame"])] = cnt.get((x["mmdd"], x["frame"]), 0) + 1
    for x in plan:
        k = (x["mmdd"], x["frame"])
        x["key"] = k if cnt[k] == 1 else k + (norm_kw(x["keyword"]),)
        seq[x["mmdd"]] = seq.get(x["mmdd"], 0) + 1
        x["seq"] = seq[x["mmdd"]]  # 그 날 몇 번째 글(원고 파일 이름 순번)
        x["fname"] = "%s_%d_%s_%s" % (x["mmdd"], x["seq"], x["frame"], norm_kw(x["keyword"]))
    return plan


def norm_kw(s):
    return re.sub(r"\s+", "", s or "")


def resolve_slots(slots, label, plan):
    """제품이 적은 (날짜, 틀) → 계획의 열쇠. 같은 날 같은 틀 글이 둘 이상이면 제품 이름표·계획 메모로 가린다.
    반환: (열쇠 목록, 문제 목록)"""
    out, probs = [], []
    lab = norm_kw(re.sub(r"쓸 글.*$", "", label or "").strip("# "))
    for m, f in slots:
        cand = [x for x in plan if x["mmdd"] == m and x["frame"] == f]
        if len(cand) <= 1:
            k = cand[0]["key"] if cand else (m, f)
        else:
            hit = [x for x in cand if lab and norm_kw(x["keyword"]) and norm_kw(x["keyword"]) in lab]  # 키워드로 먼저 가린다
            if len(hit) != 1:  # 안 가려지면 계획 메모로
                hit = [x for x in cand if lab and (lab in norm_kw(x["memo"]) or (norm_kw(x["keyword"]) and norm_kw(x["keyword"]) in lab))]
            if len(hit) == 1:
                k = hit[0]["key"]
            else:
                probs.append("같은 날 같은 틀 '%s %s' 가 여러 글인데 어느 글인지 못 정함" % (m, f))
                continue
        if k not in out:
            out.append(k)
    return out, probs


def read_exceptions(week_dir):
    """_예외.txt(채팅이 관리) → {"slots": {상품id: {"slots": [(날짜,틀)], "label": 키워드 글자}}, "brand": [(날짜, 틀, 키워드 글자)]}
    '상품id: 날짜 틀 [키워드]' 줄 = 제품표·제품 파일의 '쓸 글'을 덮어씀 / '브랜드예외: 날짜 틀 [키워드]' 줄 = 브랜드 2개 규칙 예외. 파일 없으면 빈 설정."""
    out = {"slots": {}, "brand": []}
    path = os.path.join(week_dir, "_예외.txt")
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        text = f.read().replace("\r\n", "\n")
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^(.+?)\s*[:：]\s*(.+)$", s)
        if not m:
            continue
        head, val = m.group(1).strip(), m.group(2).strip()
        slots = parse_slots(val)
        label = norm_kw(SLOT_RE.sub("", val))
        if head == "브랜드예외":
            out["brand"] += [(a, b, label) for a, b in slots]
        elif val.strip() == "제외":  # '상품id: 제외' = 이 제품은 어느 글에도 쓰지 않음
            out["slots"][head] = {"slots": [], "label": ""}
        elif slots:
            out["slots"][head] = {"slots": slots, "label": SLOT_RE.sub("", val).strip(" ,")}
    return out


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
        "link": (sec.get("파트너스 링크", "").strip().splitlines() or [""])[0].strip(),
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



# ---------- 제품표(TSV) 읽기 (원고지침 v1.6) ----------
KNOWN_BRANDS = ["글라스락", "파쿠젠", "락앤락", "daroom", "듀플렉스", "한경희", "Perfectforyou", "까사베니", "샤오미", "LG전자",
                "삼성전자", "코멧", "탐사", "뽁순이", "일상나눔", "아필코", "홈싹", "시샘", "프롬비"]  # 제품표 상품명에 적힌 이름만
# 핵심 사양 항목: (이름표 단어들, [(항목, 정규식)...]) — 상품명·옵션명·제조사 공식 사양 어디에도 없으면 '사양 없음'
CORE_SPECS = [
    (("가습기",), [("용량", r"\d+(?:\.\d+)?\s*(?:L|ℓ|리터|l)\b|\d+\s*ml"), ("가습량", r"(?:ml|mL|㎖)\s*/\s*h|가습량"),
                  ("소비전력", r"\d+\s*W\b|소비\s*전력"), ("방식", r"가열식|초음파|복합식|자연기화")]),
    (("공기청정기",), [("사용 면적", r"\d+\s*(?:㎡|m2|m²|평)"), ("필터 등급", r"H1[0-4]|E1[0-2]|헤파|HEPA"),
                    ("소음", r"\d+\s*dB|데시벨|소음"), ("소비전력", r"\d+\s*W\b|소비\s*전력")]),
    (("김치통",), [("용량", r"\d+(?:\.\d+)?\s*(?:L|ℓ|리터)\b|\d+\s*ml"), ("소재", r"스테인|스텐|304|316|유리|PP|플라스틱")]),
    (("밀폐용기", "반찬통"), [("용량 구성", r"\d+(?:\.\d+)?\s*(?:L|ℓ|리터|ml)\b|\d+\s*(?:종|개|P|pcs|호)"),
                          ("소재", r"유리|글라스|스테인|스텐|304|316|PP|트라이탄|내열"), ("전자레인지 표시", r"전자레인지"),
                          ("오븐 표시", r"오븐"), ("냉동 표시", r"냉동")]),
    (("문풍지", "틈막이"), [("소재", r"실리콘|폼|스펀지|고무|EVA|PE|부직포|소재"), ("길이", r"\d+\s*(?:m|M|cm|미터)\b|길이"),
                         ("두께", r"\d+\s*(?:mm|cm|T)\b|두께"), ("폭", r"\d+\s*(?:mm|cm)\b|폭|너비")]),
    (("뽁뽁이", "에어캡"), [("겹 수", r"\d+\s*중|\d+\s*겹"), ("폭", r"\d+\s*(?:cm|m)\b|폭"), ("길이", r"\d+\s*(?:m|M)\b|길이"),
                         ("부착 방식", r"물\s*없이|물로|접착|점착|양면|부착")]),
    (("발매트",), [("크기", r"\d+\s*[xX×*]\s*\d+|\d+\s*cm"), ("두께", r"\d+\s*(?:mm|T)\b|두께"), ("소재", r"규조토|소재")]),
    (("압축팩",), [("크기", r"\d+\s*[xX×*]\s*\d+|\d+\s*cm|대형|특대|점보|중형"), ("개수", r"\d+\s*(?:개|매|P)"),
                  ("방식", r"진공|펌프|압축|롤|지퍼|밸브")]),
    (("지퍼백",), [("용량", r"\d+(?:\.\d+)?\s*(?:L|ℓ|리터|ml)\b"), ("내열 온도", r"\d+\s*(?:℃|도|°C)|내열")]),
    (("온풍기", "히터"), [("소비전력", r"\d+\s*W\b|소비\s*전력"), ("안전장치", r"과열|전도|안전|차단|센서")]),
]
NOISE_LINES = re.compile(r"^(?:신고하기|운영정책|갤러리 이미지|더보기|베스트순|최신순|접기|검색어를 입력|상품 리뷰|\d+:\d{2}|\d[\d,]*|"
                         r"\d+명에게 도움이 됐어요|동일한 상품에 대해 작성된 상품 리뷰로.*)$")
DATE_LINE = re.compile(r"^\d{4}\.\d{2}\.\d{2}\s*$")


def tsv_read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    start = next((i for i, r in enumerate(rows) if r and r[0].strip() == "리스트"), None)
    if start is None:
        raise SystemExit("제품표 머리글(리스트 / 제품명 …)을 못 찾음: " + path)
    return [r + [""] * (6 - len(r)) for r in rows[start + 1:] if any(c.strip() for c in r)]


def tsv_date(path):
    """제품표의 수집일 'YYYY-MM-DD'. 파일 이름 _제품표_YYYYMMDD 가 먼저, 없으면 머리글(리스트 줄 위)에 적힌 날짜. 못 찾으면 빈 글자."""
    m = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", os.path.basename(path))
    if not m:
        with open(path, encoding="utf-8-sig", newline="") as f:
            head = " ".join(" ".join(r) for r in list(csv.reader(f, delimiter="	"))[:5])
        m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", head)
    if not m:
        return ""
    try:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return ""


def rating_from_dist(dist):
    """분포 % (최고·좋음·보통·별로·나쁨) → 5·4·3·2·1점 가중 평균, 소수 둘째 자리 반올림('약' 없음)."""
    w = {"최고": 5, "좋음": 4, "보통": 3, "별로": 2, "나쁨": 1}
    tot = sum(dist.get(k, 0) for k in w)
    if not tot:
        return ""
    v = Decimal(sum(w[k] * dist.get(k, 0) for k in w)) / Decimal(tot)
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_stats(cell):
    """별점/후기개수 칸 → (리뷰 수, 만족 문구, 분포 dict, 쿠팡 설문 [(항목, 응답, %)])"""
    lines = [l.strip() for l in cell.replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]
    reviews, satisfied, dist, survey = None, "", {}, []
    i = 0
    while i < len(lines):
        l = lines[i]
        if reviews is None and re.fullmatch(r"[\d,]+", l):
            reviews = to_int(l)
        elif "만족했어요" in l:
            satisfied = l
        elif l in ("최고", "좋음", "보통", "별로", "나쁨") and i + 1 < len(lines) and re.fullmatch(r"\d+%", lines[i + 1]) and l not in dist:
            dist[l] = int(lines[i + 1].rstrip("%"))
            i += 1
        elif satisfied and len(dist) == 5:
            break
        i += 1
    # 설문: 분포 다음 '항목 / 응답 / %' 3줄 묶음(항목 이름이 다시 나오면 상세 목록이라 끝)
    k = next((n for n, l in enumerate(lines) if l == "나쁨"), None)
    if k is not None:
        rest = lines[k + 2:]
        seen = set()
        n = 0
        while n + 2 < len(rest) and re.fullmatch(r"\d+%", rest[n + 2]):
            item = rest[n]
            if item in seen:
                break
            seen.add(item)
            survey.append((item, rest[n + 1], rest[n + 2]))
            n += 3
    return reviews, satisfied, dist, survey


def parse_reviews(cell):
    """후기 칸 → [{date, option, text}] (닉네임·판매자·화면 글자 뺌). 본문이 같으면 1개만."""
    lines = [l.rstrip() for l in cell.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    starts = [i for i in range(len(lines) - 2) if DATE_LINE.match(lines[i + 1]) and lines[i + 2].strip().startswith("판매자")]
    out, seen = [], set()
    for a, i in enumerate(starts):
        j = starts[a + 1] if a + 1 < len(starts) else len(lines)
        blk = lines[i + 3:j]  # 판매자 줄 다음
        blk = [l.strip() for l in blk]
        opt = ""
        for n, l in enumerate(blk):
            if l:
                opt = l
                blk = blk[n + 1:]
                break
        body = [l for l in blk if l and not NOISE_LINES.match(l) and l != opt]
        text = re.sub(r"\s+", " ", " ".join(body)).strip()
        key = re.sub(r"\s+", "", text)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append({"date": lines[i + 1].strip(), "option": opt, "text": text})
    return out


def parse_tsv_products(path, overrides=None):
    prods = []
    collected_on = tsv_date(path)
    overrides = overrides or {}
    for n, r in enumerate(tsv_read(path), 1):
        label_raw, name, url, html, stats, revs = [c.strip() for c in r[:6]]
        slots_text = re.search(r"쓸 글\s*[:：](.*?)(?:\(|$)", label_raw)
        label = re.sub(r"쓸 글.*$", "", label_raw).strip("# ").strip()
        slots = parse_slots(slots_text.group(1) if slots_text else "")
        pid0 = (re.search(r"link\.coupang\.com/a/([A-Za-z0-9]+)", url) or [None, ""])[1]
        if pid0 in overrides:  # _예외.txt 의 '상품id: 날짜 틀 [키워드]' 가 제품표의 '쓸 글'보다 우선
            slots = overrides[pid0]["slots"]
            label = overrides[pid0]["label"] or label
        reviews, satisfied, dist, survey = parse_stats(stats)
        rv = parse_reviews(revs)
        for x in rv:
            x["sponsor"] = is_sponsor(x["text"])
        prods.append(make_tsv_product("제품표 %d줄(%s)" % (n, label), label, name, url, html, slots, collected_on, reviews, satisfied, dist, survey, rv))
    return prods


def make_tsv_product(file, label, name, url, html, slots, collected_on, reviews, satisfied, dist, survey, rv):
    """제품표 한 줄이든 추가 자료의 '새 제품' 이든 같은 모양의 제품 dict 를 만든다."""
    brand = next((b for b in KNOWN_BRANDS if b.lower() in name.lower()), "")
    p = {"file": file, "label": label, "keyword": "", "url": url, "date": collected_on, "types": [],
         "slots": slots, "html": html, "link": url,
         "info": {"name": name, "brand": brand, "model": "", "rating": rating_from_dist(dist), "reviews": reviews, "prices": [],
                  "skipped_prices": []},
         "must": [], "latest": [], "low": [], "memo": "", "tsv": True, "collected": rv, "satisfied": satisfied,
         "dist": dist, "survey": survey, "options": list(dict.fromkeys(x["option"] for x in rv if x["option"]))}
    p["pid"] = (re.search(r"link\.coupang\.com/a/([A-Za-z0-9]+)", url) or [None, ""])[1]
    return p


# ---------- 추가 자료(_추가자료_*.txt) ----------
def _clean_block(text):
    """주석('#' 줄)과 안내 문구('(' 로 시작해 ')' 로 끝나는 줄)를 뺀 글."""
    out = []
    for l in text.replace("\r\n", "\n").split("\n"):
        t = l.strip()
        if t.startswith("#") or (t.startswith("(") and t.endswith(")")):
            continue
        out.append(l.rstrip())
    return "\n".join(out).strip("\n")


def _bracket_parts(body):
    """'[칸 이름]' 줄로 나눈다 → ({칸 이름: 본문}, '[' 앞 머리말)"""
    parts, head, cur = {}, [], None
    for line in body.split("\n"):
        m = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if m:
            cur = m.group(1).strip()
            parts[cur] = []
        elif cur is None:
            head.append(line)
        else:
            parts[cur].append(line)
    return {k: _clean_block("\n".join(v)) for k, v in parts.items()}, _clean_block("\n".join(head))


def read_extra(week_dir):
    """_추가자료_*.txt → {"items": [{"pid","title","must","reviews"}], "new": [dict], "filled": 칸 있는 블록 수, "empty": 빈 블록 수}
    '=== 상품id | 이름 ===' 블록: [필수 표기정보]·[추가 후기] 를 더함 / '=== 새 제품 | 1124 S … ===' 블록: 새 제품 하나.
    칸이 비어 있으면 건너뜀(파일이 없어도 됨)."""
    out = {"items": [], "new": [], "filled": 0, "empty": 0}
    for path in sorted(glob.glob(os.path.join(week_dir, "_추가자료_*.txt"))):
        with open(path, encoding="utf-8-sig") as f:
            sec = split_sections(f.read())
        for title, body in sec.items():
            head_id, _, rest = [x.strip() for x in title.partition("|")]
            parts, head = _bracket_parts(body)
            if head_id == "새 제품":
                link = (parts.get("파트너스 링크", "").splitlines() or [""])[0].strip()
                name = (parts.get("상품명", "").splitlines() or [""])[0].strip()
                if not link and not name:
                    out["empty"] += 1
                    continue
                out["filled"] += 1
                kv = dict(kv_lines(head))
                out["new"].append({"title": rest, "link": link, "html": " ".join(parts.get("파트너스 HTML", "").split()), "name": name,
                                   "stats": parts.get("별점/후기개수", ""), "must": parts.get("필수 표기정보", ""),
                                   "reviews": parts.get("후기", ""), "date": kv.get("수집일", ""), "slots": parse_slots(kv.get("쓸 글", "") or rest),
                                   "page": kv.get("쿠팡 주소", ""), "file": os.path.basename(path)})
            else:
                must, rev = parts.get("필수 표기정보", ""), parts.get("추가 후기", "")
                if not must.strip() and not rev.strip():
                    out["empty"] += 1
                    continue
                out["filled"] += 1
                out["items"].append({"pid": head_id, "title": rest, "must": kv_lines(must), "reviews": split_reviews(rev)})
    return out


def _norm_text(t):
    return re.sub(r"\s+", "", t or "")


def apply_extra(raw, extra, plan):
    """추가 자료를 제품 목록(raw)에 합친다. 반환: 보고용 문장 목록."""
    notes = []
    by_pid = {p.get("pid"): p for p in raw if p.get("tsv")}
    for it in extra["items"]:
        p = by_pid.get(it["pid"])
        if not p:
            notes.append("추가 자료의 상품id %s 는 제품표에 없음 — 건너뜀" % it["pid"])
            continue
        have = {k for k, _ in p["must"]}
        p["must"] += [(k, v) for k, v in it["must"] if k not in have]
        seen = {_norm_text(x["text"]) for x in p["collected"]}
        added = 0
        for t in it["reviews"]:
            if _norm_text(t) in seen:
                continue
            seen.add(_norm_text(t))
            p["collected"].append({"date": "추가 자료", "option": "", "text": t, "sponsor": is_sponsor(t)})
            added += 1
        notes.append("%s: 필수 표기정보 %d개·추가 후기 %d개 더함" % (it["pid"], len(it["must"]), added))
    for nw in extra["new"]:
        reviews, satisfied, dist, survey = parse_stats(nw["stats"])
        rv = parse_reviews(nw["reviews"]) or [{"date": "", "option": "", "text": t} for t in split_reviews(nw["reviews"])]
        for x in rv:
            x["sponsor"] = is_sponsor(x["text"])
        label = re.sub(r"(?<!\d)\d{4}\s*[CSD](?![A-Za-z])", "", nw["title"]).strip()
        p = make_tsv_product("추가 자료 새 제품(%s)" % nw["title"], label, nw["name"], nw["link"], nw["html"], nw["slots"], nw["date"],
                             reviews, satisfied, dist, survey, rv)
        p["must"] = kv_lines(nw["must"])
        if nw["page"]:
            p["memo"] = "쿠팡 상품 화면 주소(사람이 열어 본 것): " + nw["page"]
        raw.append(p)
        notes.append("새 제품 %s: 상품명 '%s' · 후기 %d개" % (nw["title"], nw["name"][:20], len(rv)))
    return notes


def spec_missing(p, official):
    """핵심 사양 중 상품명·옵션명·제조사 공식 어디에도 없는 항목 목록."""
    text = " ".join([p["info"]["name"], p.get("label", "")] + p.get("options", []) +
                    ["%s %s" % (x.get("항목", ""), x.get("값", "")) for x in (official or {}).get("items", [])] +
                    ["%s %s" % (k, v) for k, v in p.get("must", [])])
    key_text = p["info"]["name"] + " " + p.get("label", "")
    for words, items in CORE_SPECS:
        if any(w in key_text for w in words):
            return [name for name, rx in items if not re.search(rx, text, re.I)]
    return []


# ---------- research 파일 ----------
def slot_labels(p, plan):
    """'1124 C 가열식 가습기 추천' 목록. 계획에 없으면 날짜·틀만."""
    kw = {x["key"]: x["keyword"] for x in plan}
    return ["%s %s%s" % (sl[0], sl[1], (" " + kw[sl]) if kw.get(sl) else "") for sl in p["slots"]]


def render_official(off):
    L = ["", "## 제조사 공식 사양", ""]
    if not off or not off.get("items"):
        return L + ["찾지 못함" + (" — " + off["note"] if off and off.get("note") else ""), ""]
    L += ["| 항목 | 값 | 출처 주소 | 확인일 |", "|---|---|---|---|"]
    for x in off["items"]:
        L.append("| %s | %s | %s | %s |" % tuple(str(x.get(k, "")).replace("|", "/") for k in ("항목", "값", "출처", "확인일")))
    if off.get("note"):
        L += ["", off["note"]]
    return L + [""]


def render_research(p, plan=(), official=None):
    i = p["info"]
    L = ["# 상품 자료 %s" % (p["pid"] or "(상품id 없음)"), ""]
    if p.get("tsv"):
        L += ["자료 출처: 제품표 TSV", ""]
    if p["slots"]:
        L += ["- 쓸 글: %s" % " / ".join(slot_labels(p, plan))]
        if p["keyword"]:
            L += ["- 키워드: %s" % p["keyword"]]
    else:
        L += ["- 키워드: %s" % p["keyword"], "- 쓸 글: %s" % " / ".join(p["types"])]
    L += ["- 쿠팡 주소: %s" % p["url"], "- 수집일: %s" % p["date"], ""]
    brand = i["brand"] or (official or {}).get("brand", "")
    model = i["model"] or (official or {}).get("model", "")
    L += ["## 상품", "", "- 상품명: %s" % i["name"], "- 브랜드: %s" % brand, "- 모델명: %s" % model,
          "- 별점: %s" % i["rating"], "- 리뷰 수: %s" % ("" if i["reviews"] is None else "{:,}".format(i["reviews"]))]
    if p.get("tsv"):
        d = p["dist"]
        if d:
            L += ["- 별점 분포: " + " · ".join("%s %d%%" % (k, d[k]) for k in ("최고", "좋음", "보통", "별로", "나쁨") if k in d),
                  "- 별점 계산: 5·4·3·2·1점 가중 평균, 소수 둘째 자리 반올림 = %s" % i["rating"]]
        if p["satisfied"]:
            L += ["- 만족 문구: %s" % p["satisfied"]]
    L += [""]
    if p.get("tsv"):
        L += ["## 쿠팡 설문", ""]
        L += ["- %s / %s / %s" % x for x in p["survey"]] if p["survey"] else ["(제품표에 없음)"]
        L += ["", "## 옵션명", ""]
        L += ["- %s" % o for o in p["options"]] if p["options"] else ["(없음)"]
        L += [""]
    L += ["## 옵션별 일반 판매가", ""]
    if i["prices"]:
        L += ["| 옵션 | 일반 판매가 |", "|---|---|"] + ["| %s | %s |" % (x["option"], x["price"]) for x in i["prices"]]
    else:
        L += ["(가격 자료 없음 — 가격을 쓰지 않음)" if p.get("tsv") else "(원문에 일반 판매가 없음)"]
    if i["skipped_prices"]:
        L += ["", "와우·쿠폰·카드 등 할인가 — **쓰지 않음**:", ""]
        L += ["- %s %s: %s (쓰지 않음)" % (x["option"], x["label"], x["price"]) for x in i["skipped_prices"]]
    L += ["", "## 필수 표기정보 (%d개 항목)" % len(p["must"]), ""]
    if p["must"]:
        L += ["| 항목 | 값 |", "|---|---|"] + ["| %s | %s |" % (k, v.replace("|", "/")) for k, v in p["must"]]
    else:
        L += ["(제품표에 없음)" if p.get("tsv") else "(원문에 없음)"]
    if p.get("tsv"):
        L += render_official(official)
        rv = p["collected"]
        L += ["## 모은 후기 (%d개)" % len(rv), "", "(제품표에 붙은 순서 그대로 · 닉네임 뺌 · 정렬 확인 안 됨 · 괄호는 날짜·옵션)", ""]
        if not rv:
            L += ["(제품표에 없음)"]
        for n, x in enumerate(rv, 1):
            L.append("%d. %s(%s%s) %s" % (n, "[제외 후보] " if x["sponsor"] else "", x["date"],
                                         (" · " + x["option"]) if x["option"] else "", x["text"] or "(본문 없음)"))
    else:
        for title, key in (("최신순 후기", "latest"), ("낮은 별점 후기", "low")):
            revs = p[key]
            L += ["", "## %s (%d개)" % (title, len(revs)), ""]
            if not revs:
                L += ["(원문에 없음)"]
            for n, t in enumerate(revs, 1):
                L.append("%d. %s%s" % (n, "[제외 후보] " if is_sponsor(t) else "", t))
    L += ["", "## 메모", "", p["memo"] or "(없음)", ""]
    return "\n".join(L)


def n_collected(p):
    """뺀 것(체험단 등 제외 후보)·본문 없는 후기를 빼고 센 후기 수. 제품표 제품은 '모은 후기', 옛 양식은 최신순."""
    if p.get("tsv"):
        return sum(1 for x in p["collected"] if not x["sponsor"] and x["text"])
    return sum(1 for t in p["latest"] if not is_sponsor(t))


# ---------- 합치기·점검 ----------
def load_official(week_dir):
    path = os.path.join(week_dir, "_공식사양.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def build(week_dir, plan=(), exc=None, extra=None):
    files = sorted(f for f in glob.glob(os.path.join(week_dir, "*.txt")) if not os.path.basename(f).startswith("_"))
    plan_keys = {x["key"] for x in plan}
    official = load_official(week_dir)
    raw = [parse_file(f) for f in files]
    for tsv in sorted(glob.glob(os.path.join(week_dir, "_제품표_*.tsv"))):
        raw += parse_tsv_products(tsv, (exc or {}).get("slots"))
    if extra and (extra["items"] or extra["new"]):
        for n in apply_extra(raw, extra, plan):
            print("추가 자료 -", n)
    prods = []
    by_pid, no_url = {}, []
    for p in raw:
        p["problems"] = []
        ov = ((exc or {}).get("slots") or {}).get(p["pid"])
        if ov and not p.get("tsv"):
            p["slots"], p["label"] = ov["slots"], ov["label"]
        p["slots"], sp = resolve_slots(p["slots"], p.get("label", ""), plan)
        p["problems"] += sp
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
        off = official.get(p["pid"])
        p["official"] = off
        if off and off.get("brand") and not p["info"]["brand"]:
            p["info"]["brand"] = off["brand"]
        if off and off.get("model") and not p["info"]["model"]:
            p["info"]["model"] = off["model"]
        p["spec_missing"] = spec_missing(p, off) if p.get("tsv") else []
        ok, errs = check_html(p["html"])
        if not ok:
            p["problems"].append("HTML 없음" if errs == ["HTML 없음"] else "링크 오류: " + "; ".join(sorted(set(errs))[:3]))
        pl_ok, pl_msg = check_plain_link(p.get("link", ""))
        if not pl_ok:
            p["problems"].append(pl_msg)
        p["link_ok"] = ok and pl_ok  # 일반 링크 + 링크카드 HTML 둘 다 정상일 때만
        if not p["info"]["name"]:
            p["problems"].append("상품명 없음")
        if not p["types"] and not p["slots"]:
            p["problems"].append("쓸 글 표시 없음(제외 설정이면 정상)" if p["pid"] in ((exc or {}).get("slots") or {}) else "쓸 글 표시 없음")
        if p["slots"] and plan:
            for sl in p["slots"]:
                if sl not in plan_keys:
                    p["problems"].append("계획에 없는 글 '%s'" % " ".join(sl))
        elif p["slots"] and not plan:
            p["problems"].append("_주간계획.txt 가 없어 '쓸 글' 날짜를 확인 못 함")
        if p["slots"]:  # 새 형식: 글마다 키워드는 계획에서
            p["keyword"] = " / ".join(dict.fromkeys(x["keyword"] for x in plan if x["key"] in p["slots"] and x["keyword"]))
        # 제품별 경고(새 형식)
        if len(p["slots"]) >= 4:
            p["problems"].append("경고: 같은 제품이 %d편(3편까지)" % len(p["slots"]))
        if sum(1 for sl in p["slots"] if sl[1] == "S") >= 2:
            p["problems"].append("경고: 같은 제품 S(단독 추천) %d편(한 주에 1편까지)" % sum(1 for sl in p["slots"] if sl[1] == "S"))
        days = {}
        for sl in p["slots"]:
            days.setdefault(sl[0], []).append(sl[1])
        for m, fs in days.items():
            if len(fs) >= 2:
                p["problems"].append("경고: %s 같은 날 두 틀(%s)에 같은 제품" % (m, "·".join(fs)))
        if p.get("tsv"):  # v1.6 제품 경고(막지 않음)
            try:
                if p["info"]["rating"] and float(p["info"]["rating"]) < 4.5:
                    p["problems"].append("경고: 별점 %s (4.5 미만)" % p["info"]["rating"])
            except ValueError:
                pass
            if not p["info"]["brand"]:
                p["problems"].append("경고: 브랜드 불명")
            if p["spec_missing"]:
                p["problems"].append("사양 없음: " + "·".join(p["spec_missing"]))
        else:
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


MIN_REVIEWS = {"S": 10, "D": 15}  # v1.6(11/20 고침): 단독 추천 10개↑ · 심층 리뷰 15개↑(글 있는 후기, 뺀 것 제외)


def brand_excepted(x, exc):
    """주간계획 한 줄 x 가 _예외.txt 의 '브랜드예외' 에 들어 있는지(키워드 글자가 적혀 있으면 그 글만)."""
    for m, f, kw in (exc or {}).get("brand", []):
        if (m, f) == (x["mmdd"], x["frame"]) and (not kw or kw in norm_kw(x["keyword"])):
            return True
    return False


def plan_table(plan, prods, exc=None):
    """주간계획 줄 순서대로 글별 준비 상태 표(마크다운 줄 목록)."""
    L = ["## 글별 준비표", ""]
    if not plan:
        return L + ["(_주간계획.txt 가 없거나 글 줄이 없음)", ""]
    L += ["| 원고 이름 | 키워드 | 들어온 제품 | 모은 후기(센 수/전체) | 준비 상태 | 모자란 것 | 사양 없음 |", "|---|---|---|---|---|---|---|"]
    for x in plan:
        ps = [p for p in prods if x["key"] in p["slots"]]
        names = " / ".join((p["info"]["name"] or p["pid"] or p["file"]) for p in ps)
        miss = []
        nrev = " · ".join(("%d/%d" % (n_collected(p), len(p["collected"]))) if p.get("tsv") else str(n_collected(p)) for p in ps) or "-"
        if not ps:
            state = "제품 없음"
        elif x["frame"] == "C":
            brands = {p["info"]["brand"] for p in ps if p["info"]["brand"]}
            if len(ps) < 2:
                miss.append("제품 %d개(2개 필요)" % len(ps))
            if len(brands) < 2:
                miss.append("브랜드 %d개(2개 필요)" % len(brands))
            if brand_excepted(x, exc) and len(ps) >= 2 and len(brands) < 2:
                miss = [m for m in miss if not m.startswith("브랜드")] + ["브랜드 %d개(예외 설정: 제품명으로 소제목)" % len(brands)]
                state = "준비됨(브랜드 예외)"
            else:
                state = "준비됨" if len(ps) >= 2 and len(brands) >= 2 else "모자람"
        else:
            if len(ps) != 1:
                miss.append("제품 %d개(정확히 1개 필요)" % len(ps))
            if len(ps) == 1 and n_collected(ps[0]) < MIN_REVIEWS[x["frame"]]:
                miss.append("모은 후기 %d개(%d개 필요)" % (n_collected(ps[0]), MIN_REVIEWS[x["frame"]]))
            state = "준비됨" if not miss else "모자람"
        badlink = [p["info"]["name"][:14] for p in ps if not p["link_ok"]]
        if badlink:
            miss.append("링크 모자람(일반 링크·카드 둘 다 필요): " + ", ".join(badlink))
            state = "모자람"
        nospec = " / ".join("%s: %s" % (p["info"]["name"][:14], "·".join(p["spec_missing"])) for p in ps if p["spec_missing"]) or "-"
        cells = [x["fname"], x["keyword"], names, nrev, state, " / ".join(miss) or "없음", nospec]
        L.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    return L + [""]


def write_outputs(week_dir, prods, plan=(), exc=None):
    rdir = os.path.join(week_dir, "research")
    os.makedirs(rdir, exist_ok=True)
    olds = glob.glob(os.path.join(rdir, "*.md"))
    if olds:  # 다시 돌리면 덮어쓰되, 지우기 전에 옛 결과를 research_backup_<시각>/ 로 옮겨 둔다
        bdir = os.path.join(week_dir, "research_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(bdir, exist_ok=True)
        for old in olds:
            shutil.move(old, os.path.join(bdir, os.path.basename(old)))
        print("옛 research %d개 보관:" % len(olds), bdir)
    links = {}
    for p in prods:
        if not p["pid"]:
            continue
        with open(os.path.join(rdir, p["pid"] + ".md"), "w", encoding="utf-8") as f:
            f.write(render_research(p, plan, p.get("official")))
        if p["link_ok"]:
            links[p["pid"]] = {"link": p["link"].strip(), "html": p["html"]}
    with open(os.path.join(week_dir, "links.json"), "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=1)
    L = ["# 받은편지함 변환 결과", ""] + plan_table(plan, prods, exc)
    L += ["## 상품별 점검표", "", "상품 %d개 · 문제(경고 포함) 있는 상품 %d개" % (len(prods), sum(1 for p in prods if p["problems"])), "",
          "| 상품id | 쓸 글 | 상품명 | 브랜드 | 별점 | 리뷰 수 | 모은 후기 수 | 링크 정상 | 문제·경고 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for p in prods:
        i = p["info"]
        nrev = len(p["collected"]) if p.get("tsv") else len(p["latest"])
        cells = [p["pid"] or "(없음)", "·".join(p["types"] + [" ".join(sl) for sl in p["slots"]]), i["name"], i["brand"] or "-", i["rating"],
                 "" if i["reviews"] is None else "{:,}".format(i["reviews"]), nrev,
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
    exc = read_exceptions(week_dir)
    extra = read_extra(week_dir)
    print("추가 자료: 칸이 채워진 블록 %d개 · 비어 있어 건너뜀 %d개" % (extra["filled"], extra["empty"]))
    prods = build(week_dir, plan, exc, extra)
    links = write_outputs(week_dir, prods, plan, exc)
    bad = [p for p in prods if p["problems"]]
    print("상품 %d개 → research %d개, links.json %d개, 문제(경고 포함) 있는 상품 %d개" % (len(prods), sum(1 for p in prods if p["pid"]), len(links), len(bad)))
    for p in bad:
        print(" -", p["pid"] or p["file"], ":", " / ".join(p["problems"]))
    print("저장:", week_dir)


if __name__ == "__main__":
    main()
