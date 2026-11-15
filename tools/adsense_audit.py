# -*- coding: utf-8 -*-
"""애드센스 준비 점검 (읽기 전용). 공개 주소에 GET 요청만 보냅니다.
실행: python tools/adsense_audit.py
결과: preview/adsense_audit_20261105.html / .md
- 워드프레스: GET만. 로그인·인증정보 사용 없음.
- 앱 json(content_queue.json, articles.json)은 혜택주머니 '묶음' 정보를 읽기만 합니다(저장 금지).
"""
import sys, os, re, json, time, html, itertools, urllib.request, urllib.error, urllib.parse
from html.parser import HTMLParser
from datetime import datetime, date
from collections import defaultdict, Counter

TODAY = date(2026, 11, 5)
STAMP = "20261105"
SITES = [
    {"key": "혜택주머니", "base": "https://info-labs.co.kr", "bundle": True, "pub": "ca-pub-1413902309828575"},
    {"key": "싼세계", "base": "https://ssansegae.com", "bundle": False, "pub": None},
]
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "preview")
UA = "Mozilla/5.0 (compatible; adsense-readonly-audit/1.0)"
LAST = [0.0]
CACHE = {}
TOTALS = {}
SUFFIX = "_v2"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(NoRedirect)


def get(url, timeout=25):
    """GET 한 번(리다이렉트 따라가지 않음). 요청 사이 1초, 실패 시 같은 주소 최대 2번 재시도.
    반환: dict(status, headers, body, error)"""
    if url in CACHE:
        return CACHE[url]
    res = {"status": None, "headers": {}, "body": "", "error": None}
    for attempt in range(3):
        wait = 1.0 - (time.time() - LAST[0])
        if wait > 0:
            time.sleep(wait)
        LAST[0] = time.time()
        try:
            req = urllib.request.Request(urllib.parse.quote(url, safe=":/?&=%#@+,;~!$'()*[]"), method="GET",
                                         headers={"User-Agent": UA, "Accept-Language": "ko"})
            try:
                r = _opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as e:
                r = e
            raw = r.read()
            res["status"] = r.status if hasattr(r, "status") else r.code
            res["headers"] = {k.lower(): v for k, v in r.headers.items()}
            cs = re.search(r"charset=([\w-]+)", res["headers"].get("content-type", ""))
            res["body"] = raw.decode(cs.group(1) if cs else "utf-8", errors="replace")
            res["error"] = None
            if res["status"] < 500:
                break
        except Exception as e:  # 네트워크 오류 → 재시도
            res["error"] = type(e).__name__ + ": " + str(e)[:80]
    CACHE[url] = res
    return res


def get_json_all(base, route, params=""):
    """공개 REST API를 페이지 끝까지 GET. 실패하면 (None, 사유)."""
    out, page = [], 1
    while True:
        r = get(f"{base}/wp-json/wp/v2/{route}?per_page=100&page={page}{params}")
        if r["status"] == 400 and page > 1:
            break
        if r["status"] != 200:  # 일부만 읽었으면 None(=UNKNOWN 처리)
            return None, f"{route} {page}쪽 응답 {r['status']} {r['error'] or ''}"
        try:
            data = json.loads(r["body"])
        except Exception:
            return None, f"{route} JSON 해석 실패"
        out += data
        tp = int(r["headers"].get("x-wp-totalpages", "1") or 1)
        total = int(r["headers"].get("x-wp-total", "0") or 0)
        if page >= tp:
            break
        page += 1
    if total and len(out) != total:  # 서버가 말한 전체 수와 다르면 일부만 읽은 것
        return None, f"{route} {len(out)}/{total}개만 읽음"
    TOTALS[(base, route)] = (len(out), total or len(out))
    return out, None


# ---------- HTML 도우미 ----------
def strip_tags(h):
    h = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", h or "")
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def text_len(h):
    return len(re.sub(r"\s", "", strip_tags(h)))


def h2_list(h):
    return [strip_tags(x) for x in re.findall(r"(?is)<h2[^>]*>(.*?)</h2>", h or "")]


def links(h):
    return [html.unescape(x) for x in re.findall(r'(?is)<a\s[^>]*?href=["\']([^"\']+)["\']', h or "")]


def norm(s):
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def bigrams(s):
    s = norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def jacc(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def faq_questions(h):
    txt = strip_tags(h)
    qs = set(re.findall(r"(?:Q\d*[.:)]?\s*)([^?？]{4,80}[?？])", txt))
    for m in re.findall(r"(?is)<(?:h3|h4|summary|dt)[^>]*>(.*?)</(?:h3|h4|summary|dt)>", h or ""):
        t = strip_tags(m)
        if t.endswith(("?", "？")):
            qs.add(t)
    return [q.strip() for q in qs]


def paragraphs(h):
    ps = [strip_tags(x) for x in re.findall(r"(?is)<p[^>]*>(.*?)</p>", h or "")]
    return [p for p in ps if len(p) >= 30]


def similarity(a, b):
    """H2 / FAQ / 문단 유사도(0~1). 문단은 글자 2-gram 집합 비교(글 전체 문단 합쳐서)."""
    h2a, h2b = {norm(x) for x in a["h2"]}, {norm(x) for x in b["h2"]}
    fa, fb = {norm(x) for x in a["faq"]}, {norm(x) for x in b["faq"]}
    s_h2 = jacc(bigram_set(a["h2"]), bigram_set(b["h2"]))
    s_faq = jacc(bigram_set(a["faq"]), bigram_set(b["faq"])) if (fa and fb) else 0.0
    s_p = jacc(a["pg"], b["pg"])
    parts = [(s_h2, 0.4), (s_p, 0.4)] + ([(s_faq, 0.2)] if (fa and fb) else [])
    w = sum(x[1] for x in parts)
    score = sum(x[0] * x[1] for x in parts) / w
    same_h2 = [x for x in a["h2"] if norm(x) in h2b and norm(x)]
    same_q = [x for x in a["faq"] if norm(x) in fb and norm(x)]
    # 거의 같은(80%↑) 제목 항목도 예시에 포함
    if not same_h2:
        for x in a["h2"]:
            for y in b["h2"]:
                if jacc(bigrams(x), bigrams(y)) >= 0.8:
                    same_h2.append(f"{x} ≈ {y}")
                    break
    return score, s_h2, s_faq, s_p, same_h2[:3], same_q[:3]


def bigram_set(items):
    s = set()
    for x in items:
        s |= bigrams(x)
    return s


# ---------- 날짜 ----------
DATE_RE = re.compile(r"(?:(20\d{2})\s*[년.\-/]\s*)?(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?")
KW = ("까지", "마감", "접수", "신청기간", "종료")


def stale_dates(text, ref_year=2026):
    found = []
    for m in DATE_RE.finditer(text):
        y = int(m.group(1)) if m.group(1) else None
        mo, d = int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        ctx = text[max(0, m.start() - 25): m.end() + 25]
        if not any(k in ctx for k in KW):
            continue
        before = text[max(0, m.start() - 20): m.start()]
        after = text[m.end(): m.end() + 8]
        if re.search(r"(최종\s*(확인|업데이트|수정)|작성일|작성|기준일?|수정일?|발행일?|게시일?)\s*[:：]?\s*$", before):
            continue  # '최종 확인/업데이트/작성일' 줄의 날짜는 마감일이 아님
        if re.match(r"\s*(부터|~|∼|-|–)", after):
            continue  # '~부터 ~까지' 기간 설명의 시작일
        if y is not None and y < 2026:
            continue  # '2025년' 등 과거임을 밝힌 날짜
        if re.search(r"작년|지난해|재작년|전년|지난\s*(해|달|분기)|지난번", text[max(0, m.start() - 40): m.end() + 40]):
            continue  # 과거를 설명하는 문장
        if y is None:
            y = ref_year  # 연도 없는 날짜는 2026년으로 가정(근거 한 줄에 표시)
        try:
            dt = date(y, mo, d)
        except ValueError:
            continue
        if dt < TODAY:
            found.append((dt.isoformat(), text[max(0, m.start() - 40): m.end() + 40].strip()))
    return found


def parse_dt(s):
    try:
        return datetime.fromisoformat(s[:19]).date()
    except Exception:
        return None


GOV = re.compile(r"(?:^|\.)(go\.kr|or\.kr|re\.kr|mil\.kr|gov|gov\.kr|ac\.kr|korea\.kr)$")


def is_official(url, own_host):
    h = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    if not h or h.endswith(own_host):
        return False
    return bool(re.search(r"(\.|^)(go\.kr|or\.kr|re\.kr|ac\.kr|mil\.kr)$", h)) or h.endswith(".gov") or h in ("gov.kr",)


# ---------- 앱 json (읽기 전용) ----------
def load_bundles():
    """혜택주머니 묶음: {글 주소(정규화): 묶음이름}. 읽기만 하고 저장하지 않음."""
    try:
        q = json.load(open(os.path.join(ROOT, "content_queue.json"), encoding="utf-8"))
        a = json.load(open(os.path.join(ROOT, "articles.json"), encoding="utf-8"))
    except Exception as e:
        return {}, f"앱 json 읽기 실패: {type(e).__name__}", {}
    m, rep = {}, {}
    for it in q:
        art = a.get(it.get("id"))
        u = ((art or {}).get("platform_published_url") or {}).get("wordpress")
        seed = it.get("cluster_seed")
        if art and u and seed:
            m[urllib.parse.unquote(u).rstrip("/")] = seed
        if seed and it.get("cluster_role") == "대표":  # 묶음 대표글(발행 안 됐으면 키워드만)
            rep[seed] = {"url": urllib.parse.unquote(u).rstrip("/") if u else "",
                         "title": ((art or {}).get("platform_title") or {}).get("wordpress") or it.get("keyword", "")}
    return m, None, rep


# ---------- 사이트 점검 ----------
def audit(site):
    base, R = site["base"], {}
    host = urllib.parse.urlparse(base).netloc.lower()
    R["site"], R["base"], R["checks"], R["notes"] = site["key"], base, [], []

    def chk(no, name, status, evidence, rows=None, extra=None, head=None):
        R["checks"].append({"no": no, "name": name, "status": status, "evidence": evidence, "rows": rows or [],
                            "extra": extra or [], "head": head})

    bm, berr, brep = ({}, None, {})
    if site["bundle"]:
        bm, berr, brep = load_bundles()

    # 1. 목록
    posts, e1 = get_json_all(base, "posts", "&status=publish&_fields=id,link,title,content,date,modified,categories,tags")
    pages, e2 = get_json_all(base, "pages", "&_fields=id,link,title,content,date,modified")
    cats, e3 = get_json_all(base, "categories", "&_fields=id,name,count,link,slug")
    tags, e4 = get_json_all(base, "tags", "&_fields=id,name,count,link,slug")
    catname = {c["id"]: c["name"] for c in (cats or [])}
    arts = []
    for kind, lst in (("글", posts or []), ("페이지", pages or [])):
        for p in lst:
            c = p.get("content", {}).get("rendered", "")
            faq = faq_questions(c)
            arts.append({
                "kind": kind, "id": p["id"], "url": p["link"], "title": strip_tags(p["title"]["rendered"]),
                "chars": text_len(c), "h2": h2_list(c), "date": p["date"][:10], "mod": p["modified"][:10],
                "cats": [catname.get(i, str(i)) for i in p.get("categories", [])], "catids": p.get("categories", []),
                "html": c, "text": strip_tags(c), "faq": faq,
                "pg": set().union(*[bigrams(x) for x in paragraphs(c)]) if paragraphs(c) else set()})
    R["arts"] = arts
    if posts is None or pages is None:
        chk(1, "공개 글·페이지 목록", "UNKNOWN", f"목록을 다 못 읽음: {e1 or ''} {e2 or ''}")
    else:
        tp_, tg_ = TOTALS.get((base, "posts"), (len(posts), len(posts))), TOTALS.get((base, "pages"), (len(pages), len(pages)))
        chk(1, "공개 글·페이지 목록", "PASS", f"글 {tp_[0]}/{tp_[1]}개, 페이지 {tg_[0]}/{tg_[1]}개 수집(읽은 수/서버가 말한 전체 수)", [[a["kind"], a["title"], a["url"], a["chars"], " / ".join(a["h2"]), a["date"], a["mod"], ", ".join(a["cats"])] for a in arts])
    posts_only = [a for a in arts if a["kind"] == "글"]

    # 2. 3000자 미만
    if posts is None:
        chk(2, "3,000자 미만 글", "UNKNOWN", "글 목록이 없어 검사 못 함")
    else:
        sh = sorted([a for a in posts_only if a["chars"] < 3000], key=lambda a: a["chars"])
        if site["bundle"] and not berr:
            title_by_url = {urllib.parse.unquote(a["url"]).rstrip("/"): a["title"] for a in arts}
            def rep_of(a):
                seed = bm.get(urllib.parse.unquote(a["url"]).rstrip("/"))
                if not seed:
                    return "(묶음 못 찾음)"
                r_ = brep.get(seed)
                return f"[{seed}] " + (title_by_url.get(r_["url"], r_["title"]) if r_ else "(대표글 정보 없음)")
            rows2 = [[a["title"], a["url"], a["chars"], rep_of(a)] for a in sh]
            h2h = ["제목", "주소", "글자수", "같은 묶음 대표글"]
        else:
            rows2, h2h = [[a["title"], a["url"], a["chars"]] for a in sh], None
        chk(2, "3,000자 미만 글", "FAIL" if sh else "PASS", f"3,000자 미만 {len(sh)}개 / 전체 글 {len(posts_only)}개", rows2, head=h2h)

    # 3. 겹침
    groups = defaultdict(list)
    if site["bundle"]:
        err = berr
        if err or not bm:
            chk(3, "묶음/카테고리 내 겹침", "UNKNOWN", err or "앱 json에서 묶음 정보를 못 찾음")
            groups = None
        else:
            for a in posts_only:
                b = bm.get(urllib.parse.unquote(a["url"]).rstrip("/"))
                if b:
                    groups[b].append(a)
            R["notes"].append(f"묶음에 연결된 글 {sum(len(v) for v in groups.values())}/{len(posts_only)}개 (연결 못 한 글은 겹침 비교에서 제외)")
    else:
        for a in posts_only:
            for c in a["cats"][:1]:  # 대표(첫) 카테고리 기준
                groups[c].append(a)
    if groups is not None:
        if posts is None:
            chk(3, "묶음/카테고리 내 겹침", "UNKNOWN", "글 목록이 없어 검사 못 함")
        else:
            pairs = []
            for g, lst in groups.items():
                for a, b in itertools.combinations(lst, 2):
                    sc, s1, s2, s3, sh2, sq = similarity(a, b)
                    pairs.append((sc, g, a, b, s1, s2, s3, sh2, sq))
            pairs.sort(key=lambda x: -x[0])
            top = pairs[:20]
            hi = [p for p in pairs if p[0] >= 0.5]
            chk(3, "묶음/카테고리 내 겹침", "FAIL" if hi else "PASS",
                f"비교 {len(pairs)}쌍, 유사도 0.5 이상 {len(hi)}쌍 (점수=H2 40%·문단 40%·FAQ 20%)",
                [[f"{p[0]:.2f}", p[1], p[2]["title"], p[2]["url"], p[3]["title"], p[3]["url"],
                  f"H2 {p[4]:.2f}/FAQ {p[5]:.2f}/문단 {p[6]:.2f}", " | ".join(p[7]) or "-", " | ".join(p[8]) or "-"] for p in top])

    # 4. 지난 정보
    if posts is None:
        chk(4, "지난 날짜 표현", "UNKNOWN", "글 목록이 없어 검사 못 함")
    else:
        rows = []
        for a in posts_only:
            sd = stale_dates(a["text"])
            if sd:
                rows.append([a["title"], a["url"], len(sd), "; ".join(f"{d}: …{c}…" for d, c in sd[:3])])
        R["notes"].append("날짜에 연도가 없으면 2026년으로 가정. 제외: '최종 확인/업데이트/작성일' 줄 날짜, '~부터 ~까지'의 시작일, 2025년 등 명시된 과거, 작년/지난해 문장")
        chk(4, "지난 날짜 표현(오늘 2026-11-05 기준)", "FAIL" if rows else "PASS", f"의심 글 {len(rows)}개/글 {len(posts_only)}개 (남은 문장은 앞뒤 40자 표시)", rows)

    # 5. 최종 확인
    if posts is None:
        chk(5, "'최종 확인' 날짜 ↔ 수정일", "UNKNOWN", "글 목록이 없어 검사 못 함")
    else:
        rows, nofind, cleanup = [], [], 0
        for a in posts_only:
            m = re.search(r"최종\s*확인[^0-9]{0,15}(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})", a["text"][:600])
            if not m:
                nofind.append([a["title"], a["url"]])
                continue
            try:
                cd = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            md = parse_dt(a["mod"])
            diff = abs((md - cd).days) if md else None
            if diff is not None and diff > 7:
                flag = "정리로 수정일만 바뀐 것으로 보임" if a["mod"] == "2026-11-03" else ""
                cleanup += bool(flag)
                rows.append([a["title"], a["url"], cd.isoformat(), a["mod"], diff, flag])
        st = "FAIL" if rows else ("UNKNOWN" if len(nofind) == len(posts_only) else "PASS")
        chk(5, "'최종 확인' 날짜 ↔ 수정일", st,
            f"7일 초과 차이 {len(rows)}개 (수정일 2026-11-03 묶음 정리로 보이는 글 {cleanup}개 + 그 외 {len(rows) - cleanup}개), "
            f"'최종 확인' 표기를 못 찾은 글 {len(nofind)}개/{len(posts_only)}개", rows,
            extra=[("'최종 확인' 표기를 못 찾은 글 전체", ["제목", "주소"], nofind)] if nofind else None)

    # 6. 공식 출처
    if posts is None:
        chk(6, "공식 출처 링크 0~1개", "UNKNOWN", "글 목록이 없어 검사 못 함")
    else:
        rows = []
        for a in posts_only:
            off = sorted({urllib.parse.urlparse(u).netloc.lower() for u in links(a["html"]) if is_official(u, host)})
            a["official"] = off
            if len(off) <= 1:
                rows.append([a["title"], a["url"], len(off), ", ".join(off) or "-"])
        if site["bundle"]:
            st6 = "FAIL" if rows else "PASS"
        else:  # 싼세계: 쇼핑·살림 글은 공공 링크가 필수가 아니라 참고로만 표시
            st6 = "참고" if rows else "PASS"
        chk(6, "공식 출처 링크 0~1개(서로 다른 공공 도메인 수)", st6, f"해당 글 {len(rows)}개/{len(posts_only)}개" + ("" if site["bundle"] else " (참고용: 필수 아님)"), rows)

    # 7. 내부 링크
    if posts is None:
        chk(7, "내부 링크 응답·함께 보면 좋은 글", "UNKNOWN", "글 목록이 없어 검사 못 함")
    else:
        byurl = {urllib.parse.unquote(a["url"]).rstrip("/"): a for a in arts}
        linkset = {}
        rel_total = rel_cross = 0
        for a in posts_only:
            ls = []
            for u in links(a["html"]):
                u = urllib.parse.urljoin(a["url"], u)
                if urllib.parse.urlparse(u).netloc.lower() == host and "/wp-" not in u and not u.endswith(("#",)):
                    ls.append(u.split("#")[0])
            a["internal"] = sorted(set(ls))
            # 함께 보면 좋은 글 블록
            m = re.search(r"(?is)함께\s*보면\s*좋은\s*글(.*?)(?:<h2|$)", a["html"])
            a["related"] = sorted({urllib.parse.urljoin(a["url"], u).split("#")[0] for u in links(m.group(1))}) if m else []
            for u in a["related"]:
                t = byurl.get(urllib.parse.unquote(u).rstrip("/"))
                if t and t["kind"] == "글":
                    rel_total += 1
                    if not (set(t["catids"]) & set(a["catids"])):
                        rel_cross += 1
            for u in a["internal"]:
                linkset.setdefault(u, []).append(a)
        MAXURL = 600
        urls = sorted(linkset)[:MAXURL]
        trunc7 = len(linkset) > MAXURL
        if trunc7:
            R["notes"].append(f"내부 링크 {len(linkset)}개 중 {MAXURL}개만 확인")
        bad, chain, neterr, redir = [], [], [], []
        for u in urls:
            hops, cur, st = 0, u, None
            while hops < 6:
                r = get(cur)
                st = r["status"]
                if st in (301, 302, 307, 308) and r["headers"].get("location"):
                    hops += 1
                    cur = urllib.parse.urljoin(cur, r["headers"]["location"])
                else:
                    break
            if hops >= 1:  # 링크 고칠 때 쓰는 표: 어느 글에 있는지 → 도착 주소
                for x in linkset[u]:
                    redir.append([x["title"], x["url"], u, cur, hops])
            if st is None:
                neterr.append(u)
                bad.append([u, "오류", ", ".join(x["title"] for x in linkset[u][:2])])
            elif st >= 400:
                bad.append([u, st, ", ".join(x["title"] for x in linkset[u][:2])])
            elif hops >= 1 and hops > 2:
                chain.append([u, hops, ", ".join(x["title"] for x in linkset[u][:2])])
            elif hops >= 1:
                bad.append([u, f"301 {hops}번", ", ".join(x["title"] for x in linkset[u][:2])])
        ratio = f"{rel_cross}/{rel_total} ({rel_cross * 100 // rel_total}%)" if rel_total else "해당 없음(링크 0)"
        status = "FAIL" if (chain or any(str(b[1]).isdigit() for b in bad)) else "PASS"
        if status == "PASS" and (neterr or trunc7):
            status = "UNKNOWN"  # 일부만 확인했거나 네트워크 오류가 있으면 PASS 금지
        chk(7, "내부 링크 응답·함께 보면 좋은 글", status,
            f"확인 {len(urls)}/{len(linkset)}개: 404등 오류·301 {len(bad)}개(그중 네트워크 오류 {len(neterr)}개는 따로 셈), 301 3번 이상 연쇄 {len(chain)}개. '함께 보면 좋은 글' 중 다른 카테고리 비율 {ratio}",
            [["오류/301"] + b for b in bad] + [["연쇄"] + c for c in chain],
            extra=[("301 링크: 어느 글에 있는지 → 301 도착 주소", ["글 제목", "글 주소", "링크(옛 주소)", "301 도착 주소", "301 횟수"], redir)] if redir else None)

    # 8. 카테고리·태그
    if cats is None or tags is None:
        chk(8, "카테고리·태그 페이지", "UNKNOWN", f"목록 못 읽음: {e3 or ''} {e4 or ''}")
    else:
        zero = [c for c in cats if c["count"] == 0]
        one = [t for t in tags if t["count"] == 1]
        rows, noidx = [], 0
        resp_fail = 0
        trunc8 = len(cats) > 150 or len(tags) > 150
        for kind, lst in (("카테고리", cats), ("태그", tags)):
            for t in lst[:150]:
                r = get(t["link"])
                if r["status"] != 200:
                    resp_fail += 1
                robots = re.search(r'(?is)<meta[^>]+name=["\']robots["\'][^>]*content=["\']([^"\']+)', r["body"])
                rb = robots.group(1) if robots else "(없음)"
                if "noindex" in rb.lower():
                    noidx += 1
                rows.append([kind, t["name"], t["link"], t["count"], r["status"], rb])
        if trunc8:
            R["notes"].append("카테고리·태그 페이지는 각 150개까지만 robots 확인")
        st8 = "FAIL" if zero else ("UNKNOWN" if (resp_fail or trunc8) else "PASS")
        chk(8, "카테고리·태그 페이지", st8,
            f"글 0개 카테고리 {len(zero)}개, 글 1개 태그 {len(one)}개/{len(tags)}개, noindex 페이지 {noidx}개, 응답 200 아님 {resp_fail}개, 확인 {len(rows)}/{len(cats) + len(tags)}개", rows)

    # 9. sitemap
    rb0 = get(base + "/robots.txt")
    robots_maps = re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", rb0["body"]) if rb0["status"] == 200 else []
    if robots_maps:
        smurl, from_robots = robots_maps[0], True
    else:
        smurl, from_robots = base + "/sitemap_index.xml", False
    sm = get(smurl)
    if sm["status"] != 200:
        cand = [base + p for p in ("/sitemap.xml", "/wp-sitemap.xml", "/sitemap_index.xml") if base + p != smurl]
        alts = [f"{p.replace(base, '')}→{get(p)['status']}" for p in cand]
        why = ("robots.txt가 가리키는 사이트맵이 " if from_robots else "robots.txt에 Sitemap 줄이 없어 기본 주소(sitemap_index.xml)를 봤고 ") + f"{sm['status']}"
        st_ = "UNKNOWN" if (sm["status"] is None or rb0["status"] is None) else "FAIL"
        chk(9, "사이트맵 주소 상태", st_,
            f"{why} 응답 ({smurl}) {sm['error'] or ''}. 다른 주소 확인: {', '.join(alts)}")
    else:
        if not from_robots:
            R["notes"].append("robots.txt에 Sitemap 줄이 없어 sitemap_index.xml을 기준으로 확인")
        subs = re.findall(r"<loc>([^<]+)</loc>", sm["body"])
        locs, subfail = [], 0
        for s in subs:
            r = get(s)
            if r["status"] == 200:
                locs += re.findall(r"<loc>([^<]+)</loc>", r["body"])
            else:
                subfail += 1
        locs = [u for u in dict.fromkeys(locs) if not re.search(r"\.(jpe?g|png|webp|gif)$", u, re.I)]
        MAXS = 400
        bad = []
        for u in locs[:MAXS]:
            r = get(u)
            if r["status"] != 200:
                bad.append([u, r["status"] if r["status"] else "오류"])
        if len(locs) > MAXS:
            R["notes"].append(f"사이트맵 주소 {len(locs)}개 중 {MAXS}개만 확인")
        st9 = "FAIL" if bad else ("UNKNOWN" if (subfail or len(locs) > MAXS or not locs) else "PASS")
        chk(9, "사이트맵 주소 상태", st9, f"하위 사이트맵 {len(subs)}개(못 읽음 {subfail}개), 주소 {len(locs)}개 중 확인 {min(len(locs), MAXS)}개, 200 아님 {len(bad)}개", bad)

    # 10. robots / ads.txt / 기본 페이지
    rb = get(base + "/robots.txt")
    chk("10a", "robots.txt", "PASS" if rb["status"] == 200 else ("UNKNOWN" if rb["status"] is None else "FAIL"),
        f"응답 {rb['status']}", [[rb["body"][:1500]]] if rb["status"] == 200 else [])
    ad = get(base + "/ads.txt")
    ctype = ad["headers"].get("content-type", "")
    PUBNUM = "1413902309828575"
    has_pub_ads = ("pub-" + PUBNUM) in ad["body"]
    # 혜택주머니는 pub 번호가 있어야 PASS. 싼세계는 번호 포함 여부를 기록만(다른 계정일 수 있음)
    okad = ad["status"] == 200 and "text/plain" in ctype and "google.com" in ad["body"] and (has_pub_ads or not site["bundle"])
    chk("10b", "ads.txt", "PASS" if okad else ("UNKNOWN" if ad["status"] is None else "FAIL"),
        f"응답 {ad['status']}, Content-Type {ctype or '-'}, 내용 {len(ad['body'])}자, pub-{PUBNUM} 포함: {'예' if has_pub_ads else '아니오'}",
        [[ad["body"][:500]]] if ad["status"] == 200 else [])
    home = get(base + "/")
    homehtml = home["body"]
    def find_page(words):
        for a in arts:
            if a["kind"] == "페이지" and any(w in a["title"] for w in words):
                return a
        return None
    pg_defs = [("홈", base + "/"), ("소개", find_page(["소개", "About"])), ("문의", find_page(["문의", "Contact", "연락"])),
               ("개인정보", find_page(["개인정보", "Privacy"]))]
    rows, missing = [], []
    for nm, t in pg_defs:
        if t is None:
            rows.append([nm, "(페이지 목록에서 못 찾음)", "없음"])
            missing.append(nm)
            continue
        u = t if isinstance(t, str) else t["url"]
        r = get(u)
        rows.append([nm, u, r["status"] if r["status"] else "오류"])
        if r["status"] != 200:
            missing.append(nm)
    st10c = "UNKNOWN" if pages is None else ("FAIL" if missing else "PASS")
    chk("10c", "홈·소개·문의·개인정보 페이지 응답", st10c, "200 아님/없음: " + (", ".join(missing) or "없음") + (" (페이지 목록을 못 읽어 UNKNOWN)" if pages is None else ""), rows)

    # 11. 개인정보 문자열
    pv = find_page(["개인정보", "Privacy"])
    if pv is None or (pv and not pv["text"]):
        chk(11, "개인정보 페이지 문자열", "UNKNOWN", "개인정보 페이지를 못 찾아 검사 못 함")
    else:
        t = pv["text"]
        bad_terms = {"제안된 텍스트": "제안된 텍스트" in t, "쿠팡": "쿠팡" in t, "[ 자리표시": bool(re.search(r"\[[^\]\n]{1,30}\]", t)),
                     "'[' 로 시작하는 줄": bool(re.search(r"(?m)^\s*\[", pv["html"] and strip_tags(pv["html"]))) }
        good = {"애널리틱스/Analytics": bool(re.search("애널리틱스|Analytics", t, re.I)), "애드센스/AdSense": bool(re.search("애드센스|AdSense", t, re.I)),
                "광고 설정(adssettings) 링크": "adssettings" in pv["html"], "시행일 표현": bool(re.search(r"시행\s*일|시행합니다|적용\s*일|시행일", t))}
        issues = [k for k, v in bad_terms.items() if v]
        R["notes"].append("개인정보 '[' 검사: 본문에 대괄호 자리표시가 있는지 보는 것이라 일반 대괄호 표기도 걸릴 수 있음")
        # 요건 기반: 나쁜 문자열 있으면 FAIL, 필수 항목 빠지면 FAIL
        miss = [k for k, v in good.items() if not v]
        chk(11, "개인정보 페이지 문자열", "FAIL" if (issues or miss) else "PASS",
            f"나쁜 문자열: {', '.join(issues) or '없음'} / 빠진 항목: {', '.join(miss) or '없음'}",
            [["있으면 안 됨", k, "발견" if v else "없음"] for k, v in bad_terms.items()] + [["있어야 함", k, "있음" if v else "없음"] for k, v in good.items()])

    # 12. 애드센스 코드
    if home["status"] != 200 or not homehtml:
        chk(12, "홈 HTML 애드센스 코드", "UNKNOWN", f"홈 응답 {home['status']}")
    else:
        has_pub = bool(site["pub"] and site["pub"] in homehtml)
        has_js = "adsbygoogle" in homehtml or "pagead2.googlesyndication.com" in homehtml
        if site["bundle"]:
            chk(12, "홈 HTML 애드센스 코드", "PASS" if (has_pub or has_js) else "FAIL",
                f"ca-pub 발견: {'예' if has_pub else '아니오'}, adsbygoogle/스크립트 발견: {'예' if has_js else '아니오'}"
                + ("" if (has_pub or has_js) else " (HTML 글자 검색만 함 — 플러그인/JS로 나중에 넣는 코드는 못 봄)"))
        else:
            chk(12, "홈 HTML 애드센스 코드(기록만)", "N/A", f"adsbygoogle/스크립트 발견: {'예' if has_js else '아니오'}")

    # 13. 메뉴
    if home["status"] != 200 or not homehtml:
        chk(13, "메뉴의 카테고리 링크", "UNKNOWN", f"홈 응답 {home['status']}")
    else:
        navs = re.findall(r"(?is)<nav\b.*?</nav>", homehtml)
        nl = [u for n in navs for u in links(n)]
        catlinks = [u for u in nl if "/category/" in urllib.parse.unquote(u) or any(c["link"].rstrip("/") == u.rstrip("/") for c in (cats or []))]
        chk(13, "메뉴의 카테고리 링크", "PASS" if catlinks else "FAIL", f"nav {len(navs)}개, 링크 {len(nl)}개 중 카테고리 링크 {len(set(catlinks))}개")
    return R


# ---------- 출력 ----------
ORDER = {"FAIL": 0, "UNKNOWN": 1, "참고": 2, "PASS": 3, "N/A": 4}
COLOR = {"FAIL": "#c62828", "UNKNOWN": "#ef6c00", "참고": "#1565c0", "PASS": "#2e7d32", "N/A": "#757575"}
HEAD = {1: ["구분", "제목", "주소", "글자수", "H2", "발행", "수정", "카테고리"], 2: ["제목", "주소", "글자수"],
        3: ["유사도", "묶음/카테고리", "글 A", "A 주소", "글 B", "B 주소", "세부", "겹치는 H2", "겹치는 질문"],
        4: ["제목", "주소", "건수", "예시"], 5: ["제목", "주소", "최종 확인", "수정일", "차이(일)", "비고"], 6: ["제목", "주소", "공공 도메인 수", "도메인"],
        7: ["종류", "링크", "응답/횟수", "들어 있는 글"], 8: ["구분", "이름", "주소", "글 수", "응답", "robots"],
        9: ["주소", "응답"], "10c": ["페이지", "주소", "응답"], 11: ["구분", "항목", "결과"]}


def cell(v, col=None):
    s = str(v)
    if s.startswith("http"):
        return f'<a href="{html.escape(s)}">{html.escape(urllib.parse.unquote(s))}</a>'
    return html.escape(s)


def write_html(results, path):
    e = html.escape
    o = ['<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>애드센스 준비 점검 2026-11-05</title><style>'
         'body{font-family:"Malgun Gothic",sans-serif;margin:24px;color:#222}table{border-collapse:collapse;margin:8px 0 20px;font-size:13px}'
         'td,th{border:1px solid #ccc;padding:4px 8px;vertical-align:top}th{background:#f3f3f3}.b{color:#fff;padding:1px 8px;border-radius:8px;font-weight:bold}'
         'pre{background:#f7f7f7;padding:8px;white-space:pre-wrap}.warn{background:#fff3cd;padding:8px;border:1px solid #e0c36a}</style></head><body>',
         '<p class="warn"><b>※ 이 보고서는 자동 검사 결과입니다. 사람이 검토한 것도, 구글 애드센스 심사 결과도 아닙니다.</b></p>',
         f"<h1>애드센스 준비 점검 · 기준일 2026-11-05 (읽기 전용)</h1>"]
    for R in results:
        cs = sorted(R["checks"], key=lambda c: (ORDER[c["status"]], str(c["no"])))
        cnt = Counter(c["status"] for c in cs)
        o.append(f'<h2>{e(R["site"])} — <a href="{R["base"]}">{R["base"]}</a></h2><p>' + " · ".join(f"{k} {cnt[k]}" for k in ORDER if cnt[k]) + "</p>")
        for n in R["notes"]:
            o.append(f"<p>※ {e(n)}</p>")
        o.append("<table><tr><th>항목</th><th>결과</th><th>근거</th></tr>")
        for c in cs:
            o.append(f'<tr><td>{c["no"]}. {e(c["name"])}</td><td><span class="b" style="background:{COLOR[c["status"]]}">{c["status"]}</span></td><td>{e(c["evidence"])}</td></tr>')
        o.append("</table>")
        def table(hd, rows, link_title):
            t = ["<table>" + (("<tr>" + "".join(f"<th>{e(h)}</th>" for h in hd) + "</tr>") if hd else "")]
            for r in rows:
                cells, i = [], 0
                while i < len(r):  # 제목 셀은 바로 뒤 주소 셀로 링크 걸기
                    nxt = str(r[i + 1]) if i + 1 < len(r) else ""
                    if link_title and nxt.startswith("http") and not str(r[i]).startswith("http") and i in (0, 1):
                        cells.append(f'<td><a href="{e(nxt)}">{e(str(r[i]))}</a></td><td>{cell(nxt)}</td>')
                        i += 2
                    else:
                        cells.append(f"<td>{cell(r[i])}</td>")
                        i += 1
                t.append("<tr>" + "".join(cells) + "</tr>")
            t.append("</table>")
            return "".join(t)

        for c in cs:
            if not c["rows"] and not c.get("extra"):
                continue
            o.append(f'<details {"open" if c["status"]=="FAIL" and len(c["rows"])<=25 else ""}><summary><b>{c["no"]}. {e(c["name"])}</b> — 상세 {len(c["rows"])}건</summary>')
            if c["no"] in ("10a", "10b"):
                o.append(f'<pre>{e(c["rows"][0][0])}</pre>')
            elif c["rows"]:
                o.append(table(c.get("head") or HEAD.get(c["no"]), c["rows"], c["no"] in (1, 2, 4, 5, 6)))
            for title, hd, rws in c.get("extra") or []:
                o.append(f"<h4>{e(title)} — {len(rws)}건</h4>" + table(hd, rws, c["no"] == 5))
            o.append("</details>")
    o.append("</body></html>")
    open(path, "w", encoding="utf-8").write("\n".join(o))


def write_md(results, path):
    L = ["※ 자동 검사 결과이며 사람 검토나 구글 심사 결과가 아닙니다", "",
         "# 애드센스 준비 점검 v2 (2026-11-05, 읽기 전용)", ""]
    for R in results:
        cs = sorted(R["checks"], key=lambda c: (ORDER[c["status"]], str(c["no"])))
        L += [f"## {R['site']} ({R['base']})", ""] + [f"- 참고: {n}" for n in R["notes"]] + ["", "| 항목 | 결과 | 근거 |", "|---|---|---|"]
        for c in cs:
            L.append(f"| {c['no']}. {c['name']} | {c['status']} | {c['evidence'].replace('|', '/')} |")
        L += ["", f"### FAIL 목록 상위 20 ({R['site']})", ""]
        n = 0
        for c in cs:
            if c["status"] != "FAIL":
                continue
            L.append(f"**{c['no']}. {c['name']}** — {c['evidence']}")
            for r in c["rows"][:5]:
                L.append("  - " + " · ".join(str(x).replace("\n", " ")[:90] for x in r[:4]))
            for title, hd, rws in c.get("extra") or []:
                L.append(f"  - ({title}: {len(rws)}건, 전체는 html 참고)")
            n += 1
            if n >= 20:
                break
        if n == 0:
            L.append("- FAIL 없음")
        L.append("")
    open(path, "w", encoding="utf-8").write("\n".join(L))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    os.makedirs(OUT, exist_ok=True)
    results = []
    for s in SITES:
        print(f"[{s['key']}] 점검 시작 {s['base']}", flush=True)
        try:
            results.append(audit(s))
        except Exception as e:  # 한 사이트 실패가 다른 사이트를 막지 않게
            print("  오류:", type(e).__name__, e)
            results.append({"site": s["key"], "base": s["base"], "notes": [f"점검 중단: {type(e).__name__}: {e}"], "arts": [],
                            "checks": [{"no": 0, "name": "점검 실행", "status": "UNKNOWN", "evidence": f"중단: {type(e).__name__}", "rows": []}]})
        print(f"[{s['key']}] 완료", flush=True)
    hp, mp = os.path.join(OUT, f"adsense_audit_{STAMP}{SUFFIX}.html"), os.path.join(OUT, f"adsense_audit_{STAMP}{SUFFIX}.md")
    if os.path.exists(hp) or os.path.exists(mp):  # 기존 결과 보호: 덮어쓰지 않고 번호를 붙임
        n = 2
        while os.path.exists(os.path.join(OUT, f"adsense_audit_{STAMP}{SUFFIX}_{n}.html")):
            n += 1
        hp, mp = (os.path.join(OUT, f"adsense_audit_{STAMP}{SUFFIX}_{n}.{x}") for x in ("html", "md"))
    write_html(results, hp)
    write_md(results, mp)
    for R in results:
        print(R["site"], dict(Counter(c["status"] for c in R["checks"])))
    print("저장:", hp, mp)


if __name__ == "__main__":
    main()
