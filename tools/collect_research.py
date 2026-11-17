# -*- coding: utf-8 -*-
"""상품 자료 수집 (읽기 전용). 사용:
  python -I tools/collect_research.py --run 20261116_radar_naver [--ids 상품id ...] [--per-sort 25] [--expect expect.json] [--parse-only]

- 대상: pipeline/candidates_run/<실행>/approved.json 의 상품. 상품 주소는 같은 폴더 links.json 의 product_url(브랜드커넥트 상품 정보) 사용.
  제휴 링크(naver.me)는 열지 않는다(클릭 수에 잡히므로). 구매·찜·장바구니·링크 복사 버튼도 누르지 않는다.
- 저장: <실행>/research/<상품id>.json, .md (원본 후기 글은 research/_raw/<상품id>.json 에만 둔다. 요약 파일에는 원문을 옮기지 않음)
- 이미지: <실행>/img/k<키워드번호>_r<순위>.jpg 가 있으면 경로만 적고, 없으면 대표 이미지 1장을 img/<상품id>.jpg 로 저장(가공 없음).
- 브라우저 동작은 tools/collect_research_ego.mjs (ego lite) 가 한다. 로그인 화면이 나오면 멈추고 알린다.
- 앱 데이터(content_queue*·articles*)·links.json 은 읽지도 고치지도 않는다(links.json 은 읽기만).
- 추가 이미지: 이미 여는 상품 페이지의 넘겨 보는 목록(캐러셀)에서 최대 10장을 research/_img/<상품id>/ 에 01_rep, 02… 로 저장하고
  images.json 에 크기·글자 많음·제품 보임·확인 필요를 적는다(자동 추정 + 같은 폴더 overrides.json 으로 사람이 고친 판정 덮어쓰기).
  이미 받은 상품은 다시 받지 않는다(--refresh-images 때만). 판정만 다시 하려면 --rejudge-images(받지 않음). 뱃지(판매처 뱃지·로고)는 images.json 의 `뱃지`·`뱃지 확인 필요` 칸. --gif 를 주면 수집 뒤 tools/radar_gif.py 로 <상품id>.gif 도 만든다.
- research/<상품id>.md 에 `짧은 이름:`(브랜드+모델명 14자 안, 못 만들면 비우고 '확인 필요') 칸이 있다.
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile
from collections import Counter, OrderedDict, deque
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EGO_MJS = os.path.join(ROOT, "tools", "collect_research_ego.mjs")
GIF_PY = os.path.join(ROOT, "tools", "radar_gif.py")
MAX_IMAGES = 10


def jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def jsave(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


# ---------- 대상 고르기 ----------
def pick_targets(run_dir, ids):
    appr = jload(os.path.join(run_dir, "approved.json"))
    links = jload(os.path.join(run_dir, "links.json"))
    by_id = {it["product_id"]: it for it in links["items"]}
    out = []
    for ki, kw in enumerate(appr["keywords"], 1):
        for p in kw["picks"]:
            pid = p["url"].rstrip("/").split("/")[-1]
            if ids and pid not in ids:
                continue
            lk = by_id.get(pid)
            if not lk:
                print("links.json 에 없음:", pid, p["name"])
                continue
            img_dir = os.path.join(run_dir, "img")
            have = os.path.join(img_dir, "k%d_r%d.jpg" % (ki, p["rank"]))
            out.append({
                "product_id": pid, "keyword": kw["keyword"], "kw_index": ki, "rank": p["rank"],
                "name": p["name"], "product_url": lk["product_url"], "approved": p,
                "existing_image": have if os.path.exists(have) else "",
                "save_image_to": "" if os.path.exists(have) else os.path.join(img_dir, pid + ".jpg").replace("\\", "/"),
                "img_dir": os.path.join(run_dir, "research", "_img", pid).replace("\\", "/"),
                "rep_hint": rep_hint(p.get("image_url", "")),
            })
    return out


def rep_hint(image_url):
    """승인 목록의 대표 이미지 주소에서 원본 경로(20260708_43/…/….jpg)만 뽑는다. 캐러셀에서 대표 이미지를 찾는 데 쓴다."""
    u = unquote(image_url or "")
    m = re.search(r"pstatic\.net/([^\"?&]+?\.(?:jpg|jpeg|png|webp|gif))", u, re.I)
    return m.group(1) if m else ""


# ---------- 브라우저 실행 ----------
def run_ego(items, raw_dir, per_sort):
    exe = shutil.which("ego-browser")
    if not exe:
        sys.exit("ego-browser 를 찾을 수 없습니다.")
    job = {"items": [{k: it[k] for k in ("product_id", "name", "product_url", "save_image_to", "img_dir", "rep_hint", "skip_images")} for it in items],
           "raw_dir": raw_dir.replace("\\", "/"), "per_sort": per_sort, "max_images": MAX_IMAGES}
    tmp = tempfile.mkdtemp(prefix="collect_research_")
    wrapper = os.path.join(tmp, "run.mjs")
    with open(wrapper, "w", encoding="utf-8") as f:
        f.write("globalThis.__JOB = %s;\nawait import('file:///%s');\n" % (json.dumps(job, ensure_ascii=False), EGO_MJS.replace("\\", "/")))
    entry = "await import('file:///%s')" % wrapper.replace("\\", "/")
    r = subprocess.run([exe, "nodejs", "-e", entry], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    print(r.stdout[-3000:])
    if r.returncode != 0:
        print(r.stderr[-1500:])
    return r.returncode


# ---------- 사양 뽑기 (페이지에 있는 것만, 항목별 출처) ----------
def to_float(s):
    return float(s.replace(",", ""))


NOTICE_LABELS = ["품명 / 모델명", "품명", "모델명", "KC 인증정보", "법에 의한 인증·허가 등을 받았음을 확인할 수 있는 경우 그에 대한 사항", "정격전압", "소비전력", "동일 모델의 출시연월", "에너지소비효율등급",
                 "제조자(사)", "제조국", "크기", "무게", "주요 사양", "품질보증기준", "A/S 책임자와 전화번호", "A/S 책임자", "A/S 전화번호", "재화등의 배송방법에 관한 정보"]


def parse_notice(raw):
    """제공고시 줄 목록 → {항목: 값}. 알려진 항목 이름 줄을 기준으로 나누고, 배송·환불 안내 앞에서 끝낸다."""
    out = OrderedDict()
    key = None
    for line in raw.get("notice_lines", []):
        norm = re.sub(r"\s+", " ", line)
        if norm.startswith("법에 의한 인증"):
            norm = "법에 의한 인증·허가 등을 받았음을 확인할 수 있는 경우 그에 대한 사항"
        if norm in NOTICE_LABELS:
            if norm.startswith("재화등의 배송방법"):
                break
            key = norm
            out[key] = ""
        elif key:
            out[key] = (out[key] + " " + line).strip()
    return out


def extract_specs(raw):
    notice = parse_notice(raw)
    name = raw.get("page_name") or raw.get("name") or ""
    S = []

    def add(item, value, src, num=None):
        if re.fullmatch(r"\s*(상품\s*상세\s*(설명)?\s*참조|상세\s*(설명|페이지)?\s*(참조|에 표시)|상세정보 확인)\s*(/\s*(상품\s*상세\s*참조))?\s*", value):
            return  # 제공고시에 값이 없고 상세 이미지를 보라는 표시: 사양으로 치지 않음
        S.append({"항목": item, "값": value, "출처": src, **({"숫자": num} if num is not None else {})})

    for k in notice:
        if k.startswith("품명"):
            add("품명/모델명", notice[k], "상품정보 제공고시")
    mah = re.search(r"([\d,]+)\s*mAh", " ".join(notice.values()) + " " + name)
    if mah:
        src = "상품정보 제공고시(정격전압)" if re.search(r"mAh", " ".join(v for k, v in notice.items() if "정격" in k)) else "상품명"
        add("용량", mah.group(1) + "mAh", src, to_float(mah.group(1)))
    for k, v in notice.items():
        if k.startswith("정격"):
            add("정격", v, "상품정보 제공고시")
        elif k.startswith("크기"):
            nums = re.findall(r"\d+(?:\.\d+)?", v)
            add("크기", v, "상품정보 제공고시")
            if len(nums) == 3:
                add("두께(크기의 마지막 값, 가로·세로·두께 순서 가정)", nums[-1] + "mm", "상품정보 제공고시(크기)", float(nums[-1]))
        elif k.startswith("무게"):
            w = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g)", v)
            g = (float(w.group(1)) * (1000 if w.group(2) == "kg" else 1)) if w else None
            add("무게", v, "상품정보 제공고시", g)
        elif k.startswith("소비전력"):
            add("소비전력", v, "상품정보 제공고시")
        elif k.startswith("KC"):
            add("KC 인증정보", v, "상품정보 제공고시")
        elif "출시연월" in k:
            add("출시연월", v, "상품정보 제공고시")
    main = next((v for k, v in notice.items() if k.startswith("주요 사양")), "")
    if main:
        add("주요 사양(판매자 표시)", main, "상품정보 제공고시")
    text = main  # 무선·유선 구분은 판매자 '주요 사양' 문장에서만 읽는다(상품명 숫자는 무선·유선을 구분할 수 없음)
    m = re.search(r"(?:무선[^,.]{0,12}?(\d+(?:\.\d+)?)\s*W)|(?:(\d+(?:\.\d+)?)\s*W\s*(?:마그네틱\s*)?무선)", text)
    if m:
        v = m.group(1) or m.group(2)
        add("무선 출력", v + "W", "상품정보 제공고시(주요 사양)", float(v))
    m = re.search(r"(?:유선[^,.]{0,12}?(\d+(?:\.\d+)?)\s*W)|(?:(\d+(?:\.\d+)?)\s*W\s*(?:고속\s*)?유선)", text)
    if m:
        v = m.group(1) or m.group(2)
        add("유선 출력", v + "W", "상품정보 제공고시(주요 사양)", float(v))
    if not any(s["항목"] == "유선 출력" for s in S):
        m = re.search(r"(\d+(?:\.\d+)?)\s*W", name)
        if m:
            add("출력 표기(상품명, 무선·유선 구분 안 됨)", m.group(0), "상품명", float(m.group(1)))
    return S


# ---------- 제조사 공식 페이지 보충 ----------
# 네이버 제공고시에 값이 없거나 '상품상세참조' 인 항목만 공식 페이지에서 찾는다. 못 찾으면 '없음' 으로 두고 지어내지 않는다.
import html as _html
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def fetch_official(pid, urls, raw_dir, refetch=False):
    """공식 페이지 글자를 받아 _raw/official_<id>.json 에 저장(이미 있으면 그대로 사용). 읽기만 한다."""
    path = os.path.join(raw_dir, "official_%s.json" % pid)
    if os.path.exists(path) and not refetch:
        return jload(path)
    pages = []
    for u in urls:
        rec = {"url": u, "text": "", "error": ""}
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read(3_000_000)
                ctype = r.headers.get_content_charset() or "utf-8"
            t = data.decode(ctype, errors="replace")
            t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", t)
            t = re.sub(r"(?s)<[^>]+>", "\n", t)
            t = _html.unescape(t)
            t = re.sub(r"[ \t\r\f\v\u00a0]+", " ", t)
            rec["text"] = re.sub(r"\n\s*", "\n", t).strip()[:300000]
        except Exception as e:
            rec["error"] = str(e)[:160]
        pages.append(rec)
    out = {"pages": pages}
    jsave(path, out)
    return out


def _num(x):
    return float(x.replace(",", ""))


def official_find(text, kind):
    """공식 페이지 글자에서 항목 하나를 찾는다. 반환: (값 글자, 숫자 또는 None) 또는 None"""
    if kind == "무게":
        m = re.search(r"(?:무게|중량|Weight)[^\n\d]{0,12}(?:약\s*)?(\d[\d,.]*)\s*(kg|g|㎏|㎎)\b", text, re.I) or re.search(r"(?:무게|중량|Weight)\s*\n\s*(?:약\s*)?(\d[\d,.]*)\s*(kg|g)\b", text, re.I)
        if m:
            g = _num(m.group(1)) * (1000 if m.group(2).lower() in ("kg", "㎏") else 1)
            return (m.group(0).replace("\n", " ").strip(), g)
    elif kind == "크기":
        m = re.search(r"(?:크기|사이즈|치수|Dimensions?|Size)[^\n\d]{0,30}\n?\s*((?:\d+(?:\.\d+)?\s*(?:mm|cm|inch(?:es)?|in)?\s*[xX×*]\s*){2}\d+(?:\.\d+)?\s*(?:±\s*\d+\s*)?(?:mm|cm|inch(?:es)?))", text, re.I)
        if m:
            return (m.group(0).replace("\n", " ").strip(), None)
    elif kind == "용량":
        m = re.search(r"(\d[\d,]*)\s*mAh", text)
        if m:
            return (m.group(0), _num(m.group(1)))
    elif kind == "무선 출력":
        m = re.search(r"(?:무선[^\n.,]{0,14}?(\d+(?:\.\d+)?)\s*W)|(?:(\d+(?:\.\d+)?)\s*W\s*(?:마그네틱\s*)?무선)|(?:Wireless\s*(?:Output|Charging\s*Output|Power)\s*[:：(]?\s*(?:Up\s*to\s*)?(\d+(?:\.\d+)?)\s*W)", text, re.I)
        if m:
            return (m.group(0), float(next(g for g in m.groups() if g)))
    elif kind == "유선 출력":
        m = re.search(r"(?:유선[^\n.,]{0,14}?(\d+(?:\.\d+)?)\s*W)|(?:(\d+(?:\.\d+)?)\s*W\s*(?:고속\s*)?유선)|(?:USB-?C\s*(?:Output|출력)[^\n]{0,12}?(\d+(?:\.\d+)?)\s*W)|(?:Wired\s*Output\s*[:：(]?\s*(?:Up\s*to\s*)?(\d+(?:\.\d+)?)\s*W)", text, re.I)
        if m:
            return (m.group(0), float(next(g for g in m.groups() if g)))
    return None


def needed_items(raw, specs):
    """이 상품에서 꼭 있어야 할 사양 항목. 크기·무게는 모든 상품, 보조배터리류는 용량·출력도."""
    name = raw.get("page_name") or raw.get("name") or ""
    need = ["크기", "무게"]
    battery = bool(re.search(r"보조\s*배터리|mAh", name))
    if battery:
        need += ["용량"]
    if re.search(r"무선", name) and re.search(r"보조\s*배터리|mAh|충전기|충전\s*패드", name):
        need += ["무선 출력"]
    if battery and re.search(r"유선|PD|\d+\s*W", name):
        need += ["유선 출력"]
    return need


def merge_official(specs, raw, official, naver_url):
    """네이버 사양(출처 표시 추가) + 공식 페이지 보충 + '없음' 표시. 반환: (사양 목록, 공식에서 보충한 항목 수, 없음 항목 목록)"""
    out = []
    for sp in specs:
        sp = dict(sp)
        sp["출처 구분"] = "네이버"
        sp["출처 URL"] = naver_url
        out.append(sp)
    have = lambda k: any(sp["항목"].startswith(k) and sp["값"] != "없음" for sp in out)
    added, missing = 0, []
    for k in needed_items(raw, specs):
        if have(k):
            continue
        found = None
        for pg in (official or {}).get("pages", []):
            if pg.get("text"):
                f = official_find(pg["text"], k)
                if f:
                    found = (f, pg["url"])
                    break
        if found:
            (txt, num), url = found
            e = {"항목": k, "값": txt, "출처": "제조사 공식 페이지", "출처 구분": "공식", "출처 URL": url}
            if num is not None:
                e["숫자"] = num
            out.append(e)
            added += 1
        else:
            tried = [pg["url"] for pg in (official or {}).get("pages", [])]
            out.append({"항목": k, "값": "없음", "출처": "네이버 제공고시와 공식 페이지 모두에서 찾지 못함" if tried else "네이버 제공고시에 없고 공식 페이지 주소도 못 찾음", "출처 구분": "없음", "출처 URL": ",".join(tried)})
            missing.append(k)
    return out, added, missing


# ---------- 후기 주제 세기 ----------
NEG_BLOCK = r"(?<!안 )"
TOPICS = [
    ("무게·크기(휴대)", r"가볍|가벼|(무게|크기)[^.!?]{0,8}(부담 ?(없|안)|괜찮|적당)|휴대(하기|성)[^.!?]{0,8}(좋|편)|작고|작아서|아담|슬림|얇", r"무겁(?!겠)|무거워|묵직|두껍|두꺼|부피[^.!?]{0,6}(크|아쉽)|생각보다 (크|커)",
     "가볍고 작아서 들고 다니기 편하다는 말", "무겁거나 두껍다는 아쉬움"),
    ("자석(맥세이프 부착)", r"자력|자석|자성|착 ?붙|잘 ?붙|짱짱|단단하게 ?붙", r"잘 ?안 ?붙|(자력|자석|자성)[^.!?]{0,8}(약|아쉽|부족)|처졌|처지는|떨어졌", "자석이 단단해 폰에 잘 붙는다는 말", "자력이 약하거나 처진다는 말"),
    ("거치대(스탠드)", r"거치|스탠드|세워", r"(스탠드|거치)[^.!?]{0,10}(약|불안|아쉽|헐거|흔들|부러|짧)", "접이식 스탠드로 세워 볼 수 있어 편하다는 말", "스탠드가 불안하거나 아쉽다는 말"),
    ("케이블 내장·구성", r"케이블[^.!?]{0,12}(내장|일체|포함|따로|필요 ?없|안 ?챙|편)|일체형|빌트인|선 ?(안|따로)[^.!?]{0,4}(챙|필요)", r"케이블[^.!?]{0,8}(짧|아쉽|불편|뻣뻣|끊)", "선을 따로 챙기지 않아도 돼 편하다는 말", "내장 케이블이 짧거나 불편하다는 말"),
    ("충전 속도", r"충전[^.!?]{0,8}(빠르|빨라|빨리|잘 ?돼|잘 ?되|쑥쑥)|(빠른|고속) ?충전|충전이 빠", r"충전[^.!?]{0,8}(느리|느려|오래 ?걸|더디)|느린 충전", "충전이 빠르고 잘 된다는 말", "충전이 느리다는 말"),
    ("용량·사용 시간", r"용량[^.!?]{0,8}(넉넉|충분|커|좋)|하루[^.!?]{0,8}(충분|거뜬|버)|든든", r"용량[^.!?]{0,8}(부족|아쉽|작)|금방 (닳|방전)|생각보다[^.!?]{0,6}(적|빨리)", "용량이 넉넉해 하루 쓰기 충분하다는 말", "용량이 기대보다 적다는 말"),
    ("잔량 표시", r"잔량|디스플레이|퍼센트|%\s*표시", r"잔량[^.!?]{0,8}(부정확|안 ?맞|아쉽|안 ?보)", "남은 배터리를 한눈에 볼 수 있어 좋다는 말", "잔량 표시가 아쉽다는 말"),
    ("발열", r"(발열|열)[^.!?]{0,8}(없|적|크지 않|심하지 않|거의 없|안 ?나)", r"뜨거|뜨겁|발열이? ?(있|좀|심|조금|많)", "발열이 크지 않다는 말", "쓰다 보면 뜨거워진다는 말"),
    ("디자인·색상", r"예쁘|이쁘|이뻐|귀엽|디자인|색상|색감|컬러|깔끔|세련", r"색[^.!?]{0,8}(다르|차이|아쉽)|디자인[^.!?]{0,6}(별로|아쉽)|촌스", "디자인과 색이 예쁘다는 말", "색이나 디자인이 기대와 다르다는 말"),
    ("가격·가성비", r"가성비|가격[^.!?]{0,6}(좋|합리|저렴|괜찮)|저렴", r"가격[^.!?]{0,6}(비싸|부담|아쉽)|비싼", "가격 대비 만족한다는 말", "가격이 부담스럽다는 말"),
    ("불량·고장", r"(?!x)x", r"불량|고장|작동 ?(안|불)|먹통|안 ?켜|충전이 ?안 ?(돼|되)", "", "불량·고장·작동 문제를 겪었다는 말"),
    ("배송·포장", r"배송[^.!?]{0,6}(빠르|빨라|빨리|하루)|포장[^.!?]{0,6}(꼼꼼|잘|깔끔)", r"배송[^.!?]{0,6}(느리|늦|지연)|파손|찌그러", "배송이 빠르고 포장이 좋다는 말", "배송 지연이나 파손 이야기"),
    ("호환성", r"호환|갤럭시[^.!?]{0,10}(도|된|가능)|C ?타입|라이트닝|8핀", r"호환[^.!?]{0,6}(안|아쉽)|케이스[^.!?]{0,8}(안 ?붙|두꺼워서)", "여러 기종·단자와 함께 쓸 수 있다는 말", "기종이나 케이스에 따라 잘 안 맞는다는 말"),
]


def neg_hit(sent, nrx):
    """부정 낱말이 있어도 뒤에 '않/없/안'이 바로 붙으면(무겁지 않고, 발열 없고) 단점으로 세지 않는다."""
    for m in re.finditer(nrx, sent):
        tail = sent[m.end(): m.end() + 8]
        head = sent[max(0, m.start() - 3): m.start()]
        if re.match(r"\s*[은는이가도]?\s*(나|지|진|지도|게)?\s*(않|안|없|못하지)", tail) or re.search(r"안\s*$", head):
            continue
        return True
    return False


def count_topics(rows):
    pos = {t[0]: 0 for t in TOPICS}
    neg = {t[0]: 0 for t in TOPICS}
    for r in rows:
        sents = re.split(r"[.!?~\n]+|다만|그런데|하지만|근데", r["text"])
        for name, prx, nrx, *_ in TOPICS:
            p = n_ = False
            for s in sents:
                hit = neg_hit(s, nrx)
                if hit:
                    n_ = True
                if re.search(prx, s) and not hit:
                    p = True
            pos[name] += p
            neg[name] += n_
    return pos, neg


def analyze_reviews(raw):
    rv = raw.get("reviews") or {}
    seen, rows, low_rows = set(), [], []
    for key in ("ranking", "recent", "lowest"):  # 같은 후기는 한 번만 셈(앞 정렬이 먼저)
        for r in (rv.get(key) or {}).get("reviews", []):
            k = (r["date"], r["text"][:40])
            if k in seen:
                continue
            seen.add(k)
            rows.append(r)
    lseen = set()
    for r in (rv.get("lowest") or {}).get("reviews", []):  # 낮은순 표본(자기 안에서만 중복 제외): 단점 요약 우선 자료
        k = (r["date"], r["text"][:40])
        if k not in lseen:
            lseen.add(k)
            low_rows.append(r)
    n = len(rows)
    pos, neg = count_topics(rows)
    _, neg_low = count_topics(low_rows)
    pros, cons = [], []
    for name, prx, nrx, pline, nline in TOPICS:
        if pos[name] and pline:
            pros.append({"주제": name, "언급 수": pos[name], "의역": pline})
        if (neg[name] or neg_low[name]) and nline:
            cons.append({"주제": name, "낮은순 표본 언급 수": neg_low[name], "언급 수": neg[name], "의역": nline})
    pros.sort(key=lambda x: -x["언급 수"])
    cons.sort(key=lambda x: (-x["낮은순 표본 언급 수"], -x["언급 수"]))  # 낮은순 표본에서 많이 나온 단점부터
    dist = {}
    for r in rows:
        dist[str(r["rating"])] = dist.get(str(r["rating"]), 0) + 1
    low = [r for r in rows if r["rating"] and r["rating"] <= 3]
    return {
        "표본": {"읽은 후기 수(중복 제외)": n,
                "랭킹순(도움순 성격)": len(((rv.get("ranking") or {}).get("reviews") or [])),
                "최신순": len(((rv.get("recent") or {}).get("reviews") or [])),
                "평점 낮은순": len(low_rows),
                "별점 분포(표본)": dist,
                "표본 날짜 범위": (min([r["date"] for r in rows], default=""), max([r["date"] for r in rows], default="")),
                "주의": "네이버 '랭킹순'을 도움순으로 봄. 주제 수는 낱말 규칙으로 센 대략값. 한 후기에서 같은 주제는 1번만 셈. 단점은 '평점 낮은순' 표본을 우선으로 정렬함."},
        "장점": pros, "단점": cons,
        "낮은 별점(3점 이하) 후기 수": len(low),
        "판매자 AI 리뷰요약 제목(페이지 표시)": raw.get("ai_summary", [])[:15],
    }


# ---------- 최종 파일 ----------
def build(item, raw, expect, official=None):
    approved = item["approved"]
    specs = extract_specs(raw) if not raw.get("error") else []
    naver_url = raw.get("final_url") or raw.get("store_url_from_page", "")
    official_added, official_missing = 0, []
    if not raw.get("error"):
        specs, official_added, official_missing = merge_official(specs, raw, official, naver_url)
    rev = analyze_reviews(raw) if not raw.get("error") else {}
    img = item["existing_image"] or (raw.get("image_saved") or "")
    out = OrderedDict()
    out["상품id"] = item["product_id"]
    out["상품명"] = raw.get("page_name") or item["name"]
    out["승인 목록 상품명"] = item["name"]
    out["짧은 이름"] = make_short_name(item.get("keyword", ""), raw.get("page_name") or item["name"])
    out["페이지 URL"] = {"브랜드커넥트 상품 정보": item["product_url"], "네이버 쇼핑 상품 페이지": raw.get("final_url") or raw.get("store_url_from_page", "")}
    out["수집 시각"] = raw.get("collected_at", "")
    out["수집 결과"] = "실패: " + raw["error"] if raw.get("error") else "성공"
    out["가격(수집 시점)"] = {"판매가": raw.get("price"), "할인 전 가격": raw.get("list_price"), "승인 목록 값": approved.get("price"),
                       "다름": (raw.get("price") is not None and raw.get("price") != approved.get("price"))}
    out["평점"] = {"전체": raw.get("rating"), "최근 6개월": raw.get("rating_6m"), "승인 목록 값": approved.get("rating")}
    out["리뷰 수"] = {"수집 시점": raw.get("review_count"), "승인 목록 값": approved.get("reviews")}
    out["사양"] = specs
    out["사양 보충 요약"] = {"공식 페이지에서 보충한 항목 수": official_added, "없음 항목": official_missing, "공식 페이지 주소": [pg["url"] for pg in (official or {}).get("pages", [])]}
    out["후기 요약"] = rev
    kind = "기존 이미지 사용" if item["existing_image"] else ("새로 저장" if raw.get("image_saved") else "없음")
    out["이미지"] = {"상태": kind, "경로": img.replace("\\", "/"), "대표 이미지 주소": raw.get("main_image_url", ""), "받은 파일 형식": raw.get("image_type", "")}
    if item["product_id"] in expect:
        out["verify"] = verify_expect(specs, expect[item["product_id"]])
    return out


def verify_expect(specs, exp):
    def val(item_start):
        for s in specs:
            if s["항목"].startswith(item_start) and "숫자" in s:
                return s["숫자"]
        return None
    mapping = {"무게_g": "무게", "두께_mm": "두께", "무선_W": "무선 출력", "유선_W": "유선 출력", "용량_mAh": "용량"}
    res = []
    for k, v in exp.items():
        got = val(mapping[k]) if k in mapping else None
        res.append({"항목": k, "원고 값": v, "네이버 페이지 값": got, "같음": (got is not None and abs(got - v) < 0.01)})
    return res


def _short_line(o):
    sn = o.get("짧은 이름")
    if sn is None:  # 이 칸이 생기기 전에 만든 옛 파일
        return "- 짧은 이름: (옛 파일 — 다시 수집하면 채워짐)"
    if sn.get("값"):
        return "- 짧은 이름: %s  〈%s〉" % (sn["값"], sn.get("출처", ""))
    return "- 짧은 이름:\n  - 확인 필요: " + sn.get("확인 필요", "")


def to_md(o):
    L = ["> ※ 후기 장점·단점 개수는 낱말 규칙으로 센 대략값입니다(원고에 정확한 숫자로 쓰지 말 것).", "", "# " + o["상품명"], "", "- 상품id: " + o["상품id"], _short_line(o), "- 수집 시각: " + o["수집 시각"], "- 수집 결과: " + o["수집 결과"],
         "- 네이버 쇼핑 페이지: " + o["페이지 URL"]["네이버 쇼핑 상품 페이지"],
         "- 브랜드커넥트 상품 정보: " + o["페이지 URL"]["브랜드커넥트 상품 정보"], ""]
    p = o["가격(수집 시점)"]
    L += ["## 가격·평점·리뷰 수", "- 판매가 %s원 (할인 전 %s원) — 승인 목록 값 %s원%s" % (p["판매가"], p["할인 전 가격"], p["승인 목록 값"], " ← 다름" if p["다름"] else ""),
          "- 평점 %s (최근 6개월 %s) — 승인 목록 값 %s" % (o["평점"]["전체"], o["평점"]["최근 6개월"], o["평점"]["승인 목록 값"]),
          "- 리뷰 수 %s — 승인 목록 값 %s" % (o["리뷰 수"]["수집 시점"], o["리뷰 수"]["승인 목록 값"]), "", "## 사양 (페이지에 있는 것만)"]
    for s in o["사양"]:
        L.append("- %s: %s  〈출처: %s%s〉" % (s["항목"], s["값"], s.get("출처 구분", s["출처"]), (" · " + s["출처 URL"]) if s.get("출처 URL") else "") + ("" if s.get("출처 구분") in (None, "없음") else " (%s)" % s["출처"]))
        if s.get("확인 필요"):
            L.append("  - 확인 필요: " + s["확인 필요"])
        if s.get("보정 전 값"):
            L.append("  - 보정 전 값: %s 〈%s〉" % (s["보정 전 값"].get("값"), s["보정 전 값"].get("출처", "")))
    r = o["후기 요약"]
    if r:
        sm = r["표본"]
        L += ["", "## 후기 요약", "- 읽은 후기 %s개 (랭킹순 %s + 최신순 %s + 평점 낮은순 %s, 겹치는 후기는 한 번만) / 별점 분포 %s / 날짜 %s ~ %s" % (
            sm["읽은 후기 수(중복 제외)"], sm["랭킹순(도움순 성격)"], sm["최신순"], sm["평점 낮은순"], sm["별점 분포(표본)"], *sm["표본 날짜 범위"]),
            "- " + sm["주의"], "", "장점"]
        L += ["- %s (%s개): %s" % (x["주제"], x["언급 수"], x["의역"]) for x in r["장점"]] or ["- (없음)"]
        L += ["", "단점"]
        L += ["- %s (낮은순 표본 %s개 / 전체 %s개): %s" % (x["주제"], x["낮은순 표본 언급 수"], x["언급 수"], x["의역"]) for x in r["단점"]] or ["- (뚜렷한 단점 언급 없음)"]
        L += ["", "- 3점 이하 후기: %s개" % r["낮은 별점(3점 이하) 후기 수"]]
        if r["판매자 AI 리뷰요약 제목(페이지 표시)"]:
            L.append("- 페이지의 AI 리뷰요약 제목: " + ", ".join(r["판매자 AI 리뷰요약 제목(페이지 표시)"]))
    L += ["", "## 이미지", "- %s: %s" % (o["이미지"]["상태"], o["이미지"]["경로"] or "-")]
    if "verify" in o:
        L += ["", "## verify (원고 값 ↔ 네이버 페이지 값)"]
        L += ["- %s: 원고 %s / 페이지 %s → %s" % (v["항목"], v["원고 값"], v["네이버 페이지 값"], "같음" if v["같음"] else "다름 또는 없음") for v in o["verify"]]
    return "\n".join(L) + "\n"


# ---------- 추가 이미지 기록 (images.json) ----------
def detect_badge(im):
    """판매처 뱃지·로고 상자 자동 추정. 반환 (뱃지, 확인 필요, 근거).
    이미지 모서리·위아래 띠 안에서, 한 색으로 칠해진 작은 덩어리가 둘레와 뚜렷이 다르고(대비), 안에 글자·그림이 있으면 뱃지/로고로 본다.
    덩어리가 너무 얇거나 작아 확신이 없으면 '확인 필요'. 아무것도 없으면 깨끗함. 글자만 있고 상자가 없는 워터마크는 못 잡는다(overrides.json 으로 고친다)."""
    sm = im.convert("RGB")
    sm.thumbnail((200, 200))
    W, H = sm.size
    px = sm.load()
    N = W * H

    def diff(a, b):
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))

    flat = [[False] * W for _ in range(H)]
    for y in range(H - 1):
        for x in range(W - 1):
            p = px[x, y]
            flat[y][x] = diff(p, px[x + 1, y]) <= 14 and diff(p, px[x, y + 1]) <= 14
    seen = [[False] * W for _ in range(H)]
    strong, weak = [], []
    for y0 in range(H):
        for x0 in range(W):
            if not flat[y0][x0] or seen[y0][x0]:
                continue
            q = deque([(x0, y0)])
            seen[y0][x0] = True
            bx0 = bx1 = x0
            by0 = by1 = y0
            area = 0
            while q:
                x, y = q.popleft()
                area += 1
                bx0, bx1, by0, by1 = min(bx0, x), max(bx1, x), min(by0, y), max(by1, y)
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < W and 0 <= ny < H and flat[ny][nx] and not seen[ny][nx] and diff(px[x, y], px[nx, ny]) <= 14:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
            if area < 0.006 * N or area > 0.20 * N or bw > 0.45 * W or bh > 0.30 * H or area / (bw * bh) < 0.6:
                continue
            cy = (by0 + by1) / 2
            if not (cy < 0.22 * H or cy > 0.78 * H) or min(bx0, W - 1 - bx1, by0, H - 1 - by1) > 0.10 * min(W, H):
                continue
            col = px[x0, y0]
            ring = [px[x, y] for x in range(max(0, bx0 - 3), min(W, bx1 + 4)) for y in (by0 - 3, by1 + 3) if 0 <= y < H]
            ring += [px[x, y] for y in range(max(0, by0 - 3), min(H, by1 + 4)) for x in (bx0 - 3, bx1 + 3) if 0 <= x < W]
            if not ring:
                continue
            contrast = sum(diff(col, r) for r in ring) / len(ring)
            ring_share = sum(1 for r in ring if diff(col, r) >= 40) / len(ring)
            if ring_share < 0.85 or contrast < 100:
                continue
            inner = sum(1 for y in range(by0, by1) for x in range(bx0, bx1) if not flat[y][x]) / max(1, bw * bh)
            box = "(%d%%,%d%%)~(%d%%,%d%%)" % (100 * bx0 // W, 100 * by0 // H, 100 * bx1 // W, 100 * by1 // H)
            if bw >= 0.07 * W and bh >= 0.07 * H and inner >= 0.04:
                strong.append(box)
            else:
                weak.append(box)
    if strong:
        return True, False, "모서리·띠 안에 글자·로고가 든 상자 %d개 %s(자동 추정)" % (len(strong), strong[0])
    if weak:
        return False, True, "모서리·띠 안에 뱃지 비슷한 작은 덩어리 %s(자동 추정)" % weak[0]
    return False, False, "모서리·띠 안에 뱃지·로고 상자 없음(자동 추정)"


def analyze_image(path):
    """Pillow 만으로 하는 거친 자동 추정. 사진 속 제품이 보이는지는 배경이 한 색일 때만 판단하고, 아니면 '판정 불가'로 둔다.
    정확하지 않으므로 같은 폴더 overrides.json 으로 사람이 고친 판정이 우선한다."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    w, h = im.size
    sm = im.copy()
    sm.thumbnail((320, 320))
    W, H = sm.size
    px = list(sm.getdata())
    n = len(px)
    # 배경 색: 가장 많은 색 덩어리
    bucket = Counter((r // 24, g // 24, b // 24) for r, g, b in px)
    bk, cnt = bucket.most_common(1)[0]
    bg_share = cnt / n
    members = [p for p in px if (p[0] // 24, p[1] // 24, p[2] // 24) == bk]
    bg = tuple(sum(c[i] for c in members) / len(members) for i in range(3))
    flat = bg_share >= 0.40
    res = {"width": w, "height": h, "bytes": os.path.getsize(path), "배경 한 색 비율": round(bg_share, 2)}
    # 글자 많음: 배경이 한 색 계열인 이미지에서, 가장자리(글자 획)가 있는 칸이 많으면
    gray = sm.convert("L")
    gp = list(gray.getdata())
    edge = [0] * n
    for y in range(H - 1):
        for x in range(W - 1):
            i = y * W + x
            if abs(gp[i] - gp[i + 1]) + abs(gp[i] - gp[i + W]) > 70:
                edge[i] = 1
    cell = 16
    cells = texty = 0
    for cy in range(0, H - cell + 1, cell):
        for cx in range(0, W - cell + 1, cell):
            e = sum(edge[(cy + dy) * W + cx + dx] for dy in range(cell) for dx in range(cell)) / (cell * cell)
            cells += 1
            if 0.05 <= e <= 0.60:
                texty += 1
    tshare = texty / max(1, cells)
    res["글자 칸 비율"] = round(tshare, 2)
    res["글자 많음"] = bool(bg_share >= 0.30 and tshare >= 0.40)
    res["글자 많음 근거"] = "자동 추정(가장자리 밀도)"
    res["뱃지"], res["뱃지 확인 필요"], res["뱃지 근거"] = detect_badge(im)
    # 제품 보임: 배경이 한 색일 때만
    if flat:
        xs, ys, ex = [], [], 0
        for i, p in enumerate(px):
            if max(abs(p[0] - bg[0]), abs(p[1] - bg[1]), abs(p[2] - bg[2])) > 40:
                xs.append(i % W)
                ys.append(i // W)
        if xs:
            frac = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1) / (W * H)
            side = lambda cond: sum(1 for k in range(len(xs)) if cond(xs[k], ys[k]))
            touch = sum(1 for c, L in ((side(lambda x, y: x <= 1), H), (side(lambda x, y: x >= W - 2), H),
                                       (side(lambda x, y: y <= 1), W), (side(lambda x, y: y >= H - 2), W)) if c >= 0.05 * L)
            res["제품 영역 비율"] = round(frac, 2)
            if frac < 0.12:
                res.update({"제품 보임": "작음", "확인 필요": True, "확인 필요 이유": "흰 배경에서 제품이 작음(자동 추정)"})
            elif touch >= 3:
                res.update({"제품 보임": "일부만 보일 수 있음", "확인 필요": True, "확인 필요 이유": "제품이 화면 가장자리 3면 이상에 닿음(자동 추정)"})
            else:
                res.update({"제품 보임": "보임(자동 추정)", "확인 필요": False})
        else:
            res.update({"제품 보임": "판정 불가(빈 화면)", "확인 필요": True, "확인 필요 이유": "배경 말고 보이는 것이 없음"})
    else:
        res.update({"제품 보임": "판정 불가(배경이 복잡한 사진, 자동으로는 못 봄)", "확인 필요": False})
    return res


def write_images_json(item, raw, genre, short, out_dir=None):
    """수집 결과(raw['images'])로 research/_img/<상품id>/images.json 을 만든다. overrides.json 이 있으면 덮어쓴다."""
    d = item["img_dir"]
    rows = []
    ov = {}
    ovp = os.path.join(d, "overrides.json")
    if os.path.exists(ovp):
        ov = jload(ovp)
    for r in raw.get("images", []):
        row = OrderedDict([("순번", r["n"]), ("file", r.get("file", "")), ("원본 주소", r["url"]), ("순서 확인 필요", bool(r.get("order_uncertain")))])
        if r.get("error") or not r.get("file"):
            row["받기 실패"] = r.get("error", "파일 없음")
            row["확인 필요"] = True
            row["확인 필요 이유"] = "받기 실패"
            rows.append(row)
            continue
        row.update(analyze_image(os.path.join(d, r["file"])))
        row["판정 방법"] = "자동 추정"
        fix = (ov.get("by_url") or {}).get(r["url"]) or (ov.get("by_file") or {}).get(r["file"])
        if fix:
            row.update(fix)
            if "뱃지" in fix:  # 사람이 뱃지 값을 정했으면 자동 추정의 '뱃지 확인 필요'는 접는다
                row["뱃지 확인 필요"] = False
                row["뱃지 근거"] = "사람이 지정(overrides.json)"
            row["판정 방법"] = "사람이 지정(overrides.json)"
        rows.append(row)
    out = OrderedDict([("상품id", item["product_id"]), ("갈래", genre or ""), ("짧은 이름", short.get("값", "")),
                       ("캐러셀 이미지 수", raw.get("carousel_total")), ("받은 이미지 수", sum(1 for r in rows if r.get("file") and not r.get("받기 실패"))),
                       ("대표 이미지를 목록에서 찾음", raw.get("rep_found_in_carousel")), ("수집 시각", raw.get("collected_at", "")),
                       ("주의", "뱃지·글자 많음·제품 보임은 자동 추정이라 틀릴 수 있음. 고치려면 overrides.json 에 적는다(뱃지: true/false)."), ("images", rows)])
    jsave(os.path.join(out_dir or d, "images.json"), out)
    return out


# ---------- 이미지 판정만 다시 (--rejudge-images) ----------
def rejudge_images(items, run_dir, out_dir=""):
    """이미 받은 이미지는 그대로 두고 판정(뱃지·글자 많음·제품 보임)만 다시 한다. 브라우저·네트워크 안 씀.
    - research/_img/<상품id>/images.json 을 새 판정으로 다시 쓴다(overrides.json 반영). 같은 폴더에 *.backup_before_rejudge_<날짜>.json 백업을 남긴다.
    - 후보 img/ 의 대표 이미지(k<번호>_r<순위>.jpg 또는 <상품id>.jpg)도 같은 기준으로 판정해 img/rep_badge.json 에 남긴다.
    out_dir 를 주면 원본 대신 그 폴더(<out_dir>/_img/<상품id>/images.json, <out_dir>/rep_badge.json)에만 쓴다(시험용). 반환: 한 줄 요약 목록."""
    from datetime import date
    lines = []
    reps = OrderedDict()
    for it in items:
        pid = it["product_id"]
        d = it["img_dir"]
        rep = it["existing_image"] or (it["save_image_to"] if it["save_image_to"] and os.path.exists(it["save_image_to"]) else "")
        if rep and os.path.exists(rep):
            r = analyze_image(rep)
            r = OrderedDict((k, r[k]) for k in ("width", "height", "글자 많음", "뱃지", "뱃지 확인 필요", "뱃지 근거", "확인 필요") if k in r)
            r["file"] = os.path.basename(rep)
            reps[pid] = r
        ip = os.path.join(d, "images.json")
        if not os.path.exists(ip):
            lines.append("%s 추가 이미지 폴더 없음 — 판정 건너뜀" % pid)
            continue
        old = jload(ip)
        raw = {"images": [{"n": r.get("순번"), "file": r.get("file", ""), "url": r.get("원본 주소", ""), "order_uncertain": r.get("순서 확인 필요"),
                           "error": r.get("받기 실패", "")} for r in old.get("images", [])],
               "carousel_total": old.get("캐러셀 이미지 수"), "rep_found_in_carousel": old.get("대표 이미지를 목록에서 찾음"), "collected_at": old.get("수집 시각", "")}
        tgt = os.path.join(out_dir, "_img", pid) if out_dir else d
        if not out_dir:
            bk = os.path.join(d, "images.backup_before_rejudge_%s.json" % date.today().strftime("%Y%m%d"))
            if not os.path.exists(bk):
                shutil.copyfile(ip, bk)
        ij = write_images_json(it, raw, old.get("갈래", ""), {"값": old.get("짧은 이름", "")}, out_dir=tgt)
        bad = [r["file"] for r in ij["images"] if r.get("뱃지")]
        sus = [r["file"] for r in ij["images"] if r.get("뱃지 확인 필요")]
        lines.append("%s 다시 판정 %d장: 뱃지 %s / 뱃지 확인 필요 %s" % (pid, len(ij["images"]), bad or "없음", sus or "없음"))
    jsave(os.path.join(out_dir or os.path.join(run_dir, "img"), "rep_badge.json"), reps)
    return lines


def load_genre_map():
    """pipeline/keyword_run/*_radar_naver/plan.json 에서 {검색어: 갈래}. 최신 계획이 우선."""
    m = {}
    for pp in sorted(glob.glob(os.path.join(ROOT, "pipeline", "keyword_run", "*_radar_naver", "plan.json")), reverse=True):
        try:
            for pl in jload(pp).get("plans", []):
                g = (pl.get("radar") or {}).get("갈래")
                if g and pl.get("keyword") and pl["keyword"] not in m:
                    m[pl["keyword"]] = g
        except Exception:
            pass
    return m


# ---------- 짧은 이름 (브랜드 + 모델명, 14자 안) ----------
SKIP_BRAND = {"삼성공식파트너", "공식", "정품", "국내정품", "신제품", "[N배송]", "[내일출발]", "공식판매처", "공식스토어"}


def _is_model(tok):
    t = tok.strip("()[],")
    if not re.search(r"\d", t) or not re.search(r"[A-Za-z]", t) or len(t) < 2:
        return False
    if re.fullmatch(r"\d+(\.\d+)?[A-Za-z가-힣]+", t):  # 10000mAh, 45W, 180Hz 같은 수치는 모델명이 아니다
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-\+\.]*", t))


def make_short_name(keyword, page_name):
    """{'값','출처'} 또는 {'값':'','확인 필요':이유}. 지어내지 않고, 검색어·상품명에 있는 글자만 쓴다."""
    kw = (keyword or "").strip()
    toks = kw.split()
    if len(toks) >= 2 and any(_is_model(t) for t in toks) and len(kw) <= 14:
        return {"값": kw, "출처": "검색어"}
    toks = (page_name or "").split()
    for i, t in enumerate(toks):
        if _is_model(t):
            j = i - 1
            while j >= 0 and (toks[j] in SKIP_BRAND or len(toks[j]) < 2 or toks[j].startswith("[")):
                j -= 1
            cand = (toks[j] + " " + t) if j >= 0 else t
            if len(cand) <= 14:
                return {"값": cand, "출처": "상품명(브랜드 추정)" if j >= 0 else "상품명(모델명만)"}
            if len(t) <= 14:
                return {"값": t, "출처": "상품명(모델명만)"}
            break
    return {"값": "", "확인 필요": "브랜드+모델명 14자 안으로 만들 수 없음"}


# ---------- GIF 만들기 (--gif) ----------
def run_gif(item, genre, short):
    d = item["img_dir"]
    if not os.path.exists(os.path.join(d, "images.json")):
        return "GIF 없음(images.json 없음)"
    cmd = [sys.executable, "-I", GIF_PY, "--src", d, "--out", os.path.join(d, item["product_id"]), "--genre", genre or "", "--name", short.get("값", "")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    return (r.stdout.strip() or r.stderr.strip()[-300:])


# ---------- 상세 이미지에서 읽은 사양 보충 (--fill-detail) ----------
def _split_combo(raw, key_a, key_b):
    """네이버 제공고시에 '크기, 무게' 처럼 두 항목이 한 줄로 묶여 있으면 '값 / 값' 을 나눠 돌려준다."""
    lines = raw.get("notice_lines") or []
    for i, l in enumerate(lines):
        if l.replace(" ", "") == (key_a + "," + key_b) and i + 1 < len(lines) and "/" in lines[i + 1]:
            a, b = [x.strip() for x in lines[i + 1].split("/", 1)]
            if a and b and "상세참조" not in a + b:
                return {key_a: a, key_b: b}
    return {}


def fill_detail(rdir, ids):
    """research/detail_values.json (사람이 상세 이미지를 눈으로 읽어 적은 값)으로 '없음' 항목만 채운다. 이미 값이 있으면 덮어쓰지 않는다.
    detail_values.json 모양: {상품id: {"채움": {항목: {"값","출처","이미지","숫자"?,"확인 필요"?}}, "보정": {항목: {같은 모양}}}}
    '보정' 은 사용자가 요청한 개별 보정(값이 이미 있어도 교체, 원래 값은 '이전 값' 으로 남김). 코드는 값을 지어내지 않는다."""
    vp = os.path.join(rdir, "detail_values.json")
    vals = jload(vp) if os.path.exists(vp) else {}
    report = {}
    for jp in sorted(os.listdir(rdir)):
        if not re.fullmatch(r"\d+\.json", jp):
            continue
        pid = jp[:-5]
        if ids and pid not in ids:
            continue
        path = os.path.join(rdir, jp)
        o = jload(path)
        raw_p = os.path.join(rdir, "_raw", jp)
        raw = jload(raw_p) if os.path.exists(raw_p) else {}
        specs = o["사양"]
        filled, left, corrected = [], [], []
        combo = _split_combo(raw, "크기", "무게")
        for sp in specs:
            if sp["값"] != "없음":
                continue
            k = sp["항목"]
            v = (vals.get(pid, {}).get("채움") or {}).get(k)
            if k in combo and not v:
                sp.update({"값": combo[k], "출처": "상품정보 제공고시('크기, 무게' 묶음 항목)", "출처 구분": "네이버", "출처 URL": o["페이지 URL"]["네이버 쇼핑 상품 페이지"]})
                sp.pop("숫자", None)
                filled.append(k)
            elif v:
                sp["값"] = v["값"]
                sp["출처"] = "네이버 상세이미지 " + v["이미지"]
                sp["출처 구분"] = "네이버 상세이미지"
                sp["출처 URL"] = o["페이지 URL"]["네이버 쇼핑 상품 페이지"]
                for extra in ("숫자", "확인 필요"):
                    if extra in v:
                        sp[extra] = v[extra]
                    else:
                        sp.pop(extra, None)
                (filled if v["값"] not in ("없음", "확인 필요") else left).append(k)
            else:
                left.append(k)
        for k, v in (vals.get(pid, {}).get("보정") or {}).items():
            tgt = next((sp for sp in specs if sp["항목"] == k), None)
            if tgt is None:
                tgt = {"항목": k}
                specs.append(tgt)
            if tgt.get("보정 전 값") is None:
                tgt["보정 전 값"] = {x: tgt.get(x) for x in ("값", "출처", "출처 URL") if x in tgt}
            for x, y in v.items():
                tgt[x] = y
            corrected.append(k)
        o["사양 보충 요약"]["상세이미지 보충 항목"] = filled
        o["사양 보충 요약"]["보정 항목"] = corrected
        o["사양 보충 요약"]["없음·확인 필요로 남은 항목"] = [sp["항목"] for sp in specs if sp["값"] in ("없음", "확인 필요")]
        o["사양 보충 요약"]["없음 항목"] = o["사양 보충 요약"]["없음·확인 필요로 남은 항목"]
        jsave(path, o)
        with open(os.path.join(rdir, pid + ".md"), "w", encoding="utf-8") as f:
            f.write(to_md(o))
        if filled or corrected or left:
            report[pid] = {"채움": filled, "보정": corrected, "남음": o["사양 보충 요약"]["없음·확인 필요로 남은 항목"]}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="실행 폴더 이름(또는 경로)")
    ap.add_argument("--ids", nargs="*", default=[])
    ap.add_argument("--per-sort", type=int, default=25, help="정렬(랭킹순·최신순)마다 읽을 후기 수")
    ap.add_argument("--expect", default="", help="원고 값 대조용 json {상품id: {무게_g, 두께_mm, 무선_W, 유선_W}} (기본: <실행>/research/expect.json)")
    ap.add_argument("--official", default="", help="공식 페이지 주소 json {상품id: [주소,...]} (기본: <실행>/research/official.json). 있으면 받아서 부족한 사양만 보충")
    ap.add_argument("--refetch-official", action="store_true", help="공식 페이지를 다시 받음(기본은 _raw/official_<id>.json 재사용)")
    ap.add_argument("--fill-detail", action="store_true", help="브라우저·수집 없이, research/detail_values.json(상세 이미지를 읽어 적은 값)으로 '없음' 사양만 채우고 .md 를 다시 만듦")
    ap.add_argument("--gif", action="store_true", help="수집 뒤 tools/radar_gif.py 로 research/_img/<상품id>/<상품id>.gif 를 만든다(기본 꺼짐)")
    ap.add_argument("--refresh-images", action="store_true", help="이미 받은 추가 이미지도 다시 받는다(기본은 받은 상품은 건너뜀)")
    ap.add_argument("--rejudge-images", action="store_true", help="브라우저·다운로드 없이, 이미 받은 이미지의 판정(뱃지·글자 많음·제품 보임)만 다시 하고 images.json 갱신(백업 남김). 후보 img/ 대표 이미지는 img/rep_badge.json 에 기록")
    ap.add_argument("--judge-out", default="", help="--rejudge-images 결과를 원본 대신 이 폴더에만 쓴다(시험용)")
    ap.add_argument("--parse-only", action="store_true", help="브라우저 없이 research/_raw 로 요약 파일만 다시 만듦")
    a = ap.parse_args()
    run_dir = a.run if os.path.isdir(a.run) else os.path.join(ROOT, "pipeline", "candidates_run", a.run)
    rdir = os.path.join(run_dir, "research")
    raw_dir = os.path.join(rdir, "_raw")
    os.makedirs(raw_dir, exist_ok=True)
    if a.fill_detail:
        rep = fill_detail(rdir, set(a.ids))
        for pid, r in rep.items():
            print(pid, "채움", r["채움"], "보정", r["보정"], "남음", r["남음"])
        return
    items = pick_targets(run_dir, set(a.ids))
    if not items:
        sys.exit("대상 상품이 없습니다.")
    print("대상 %d개: %s" % (len(items), [i["product_id"] for i in items]))
    if a.rejudge_images:
        for ln in rejudge_images(items, run_dir, a.judge_out):
            print(ln)
        return
    for it in items:
        done = os.path.exists(os.path.join(it["img_dir"], "images.json"))
        it["skip_images"] = bool(done and not a.refresh_images)
        if done and a.refresh_images and not a.parse_only:  # 옛 이미지 파일만 지움(overrides.json 은 남김)
            for f in glob.glob(os.path.join(it["img_dir"], "*")):
                if os.path.basename(f) != "overrides.json" and os.path.isfile(f):
                    os.remove(f)
    if not a.parse_only:
        for it in items:  # 옛 수집 파일이 성공으로 잘못 세어지지 않도록, 이번에 다시 받을 상품의 옛 파일은 지운다
            old = os.path.join(raw_dir, it["product_id"] + ".json")
            if os.path.exists(old):
                os.remove(old)
        run_ego(items, raw_dir, a.per_sort)
    exp_path = a.expect or os.path.join(rdir, "expect.json")
    expect = jload(exp_path) if os.path.exists(exp_path) else {}
    off_path = a.official or os.path.join(rdir, "official.json")
    off_urls = jload(off_path) if os.path.exists(off_path) else {}
    ok = fail = 0
    genre_map = load_genre_map()
    for it in items:
        rp = os.path.join(raw_dir, it["product_id"] + ".json")
        raw = jload(rp) if os.path.exists(rp) else {"error": "수집 파일 없음"}
        official = fetch_official(it["product_id"], off_urls[it["product_id"]], raw_dir, a.refetch_official) if off_urls.get(it["product_id"]) and not raw.get("error") else None
        o = build(it, raw, expect, official)
        jsave(os.path.join(rdir, it["product_id"] + ".json"), o)
        with open(os.path.join(rdir, it["product_id"] + ".md"), "w", encoding="utf-8") as f:
            f.write(to_md(o))
        if raw.get("error"):
            fail += 1
        else:
            ok += 1
        print(it["product_id"], o["수집 결과"], "| 이미지:", o["이미지"]["상태"], "| 짧은 이름:", o["짧은 이름"].get("값") or "(비움: 확인 필요)")
        genre = genre_map.get(it.get("keyword", ""), "")
        if raw.get("images") and not it["skip_images"]:
            ij = write_images_json(it, raw, genre, o["짧은 이름"])
            print("  추가 이미지: 받음 %s장 (목록 %s장)" % (ij["받은 이미지 수"], ij["캐러셀 이미지 수"]))
        elif it["skip_images"]:
            print("  추가 이미지: 이미 받음 — 건너뜀")
        elif not raw.get("error"):
            print("  추가 이미지: 못 받음", raw.get("images_error", ""))
        if a.gif and not raw.get("error"):
            print("  GIF:", run_gif(it, genre, o["짧은 이름"]))
    print("성공 %d / 실패 %d" % (ok, fail))


if __name__ == "__main__":
    main()
