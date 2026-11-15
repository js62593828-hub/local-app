"""
글 속 외부 링크 자동 교체·검사 (모든 블로그 공통).

1) 교체표(link_replacements.json): href의 도메인이 old와 같으면 주소를 통째로 new로 바꿉니다. 링크 글자(앵커 텍스트)는 그대로 둡니다.
   - 사람이 나중에 줄만 추가하면 됩니다: {"old": "옛도메인", "new": "https://새주소", "note": "메모(선택)"}
2) 링크 검사: 글 속 외부 링크를 하나씩 GET으로 열어 보고, 문제가 있으면 이유를 돌려줍니다.
   검사 결과는 메모리(아래 _CACHE)에만 잠깐 두고, 글 json이나 파일에는 저장하지 않습니다.

이 모듈은 글·큐 파일을 읽지도 쓰지도 않습니다 (교체표 json만 읽습니다).
"""
import html as _html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import requests

# ---------- 기준값 (여기만 고치면 됩니다) ----------
CHECK_TIMEOUT_SEC = 8          # 링크 하나를 기다리는 최대 시간
MAX_READ_BYTES = 300_000       # 본문 글자 수를 셀 때 읽는 최대 크기
MIN_BODY_TEXT_CHARS = 80       # 이 글자 수보다 적으면 "빈 화면 의심" (태그·스크립트 뺀 글자 수)
MAX_PAGE_REDIRECTS = 2         # 글자 없이 자바스크립트로 다른 주소로 넘기기만 하는 첫 화면을 따라가는 최대 횟수
MAX_WORKERS = 6               # 동시에 검사하는 링크 수
CACHE_TTL_SEC = 600            # 같은 링크를 다시 검사하지 않고 기억해 두는 시간 (메모리만)
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
# 검사에서 빼는 도메인 (끝이 같으면 제외: coupang.com → link.coupang.com 포함). 제휴 링크는 열 때마다 달라지거나 막혀 있어서입니다.
AFFILIATE_DOMAINS = [
    "coupang.com", "coupa.ng",
    "shopping.naver.com", "smartstore.naver.com", "brand.naver.com", "naver.me",
]
# 내 블로그 주소도 검사하지 않습니다 (내부링크는 앱이 따로 관리). 워드프레스 주소는 config에서 자동으로 더합니다.
OWN_DOMAINS = ["blog.naver.com", "m.blog.naver.com"]

# "빈 화면 의심" 판정만 하지 않는 도메인 (자바스크립트로 그려져서 글자가 적게 보이는 정상 사이트). 404·연결 실패는 그대로 검사합니다.
SKIP_BLANK_CHECK_DOMAINS = [
    "nhis.or.kr", "kinfa.or.kr", "fsc.go.kr",
    "bokjiro.go.kr", "korea.kr", "mss.go.kr", "mogef.go.kr", "insurancesupport.or.kr",
]
# 자동 확인이 막히는 도메인: "연결 실패"가 나도 노란 경고 대신 회색 한 줄(정상일 수 있음)로만 보여 줍니다.
BOT_BLOCKED_DOMAINS = ["hometax.go.kr"]
BOT_BLOCKED_REASON = "자동 확인이 막힌 사이트(정상일 수 있음)"

REPLACEMENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "link_replacements.json")

_HREF_RE = re.compile(r"""(<a\b[^>]*?\bhref\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)
_CACHE: dict[str, tuple[float, dict | None]] = {}


# ---------- 공통 도구 ----------

def _host_of(url: str) -> str:
    """주소에서 도메인만 (소문자, 앞의 www. 뺌). 주소가 아니면 ""."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


_SECOND_LEVEL = {"co", "or", "go", "ne", "re", "pe", "ac", "hs", "ms", "es", "sc", "kg", "com", "net", "org", "gov", "edu"}


def _site_of(url: str) -> str:
    """주소의 "사이트 도메인" (www.·m. 같은 앞부분 뺌: m.bokjiro.go.kr → bokjiro.go.kr). 도착 주소 비교에 씁니다."""
    labels = _host_of(url).split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)


def _norm_old(old: str) -> str:
    """교체표의 old("minwon.moel.go.kr" 또는 "https://minwon.moel.go.kr/…")를 도메인만으로 맞춥니다."""
    old = (old or "").strip()
    if not old:
        return ""
    return _host_of(old if "://" in old else "//" + old)


def load_replacements(path: str | None = None) -> list[dict]:
    """교체표를 읽습니다. 파일이 없거나 깨졌거나 잘못된 줄은 건너뜁니다 (최종본 만들기를 막지 않기 위해)."""
    try:
        with open(path or REPLACEMENTS_FILE, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        old = _norm_old(r.get("old", "")) if isinstance(r, dict) else ""
        new = (r.get("new") or "").strip() if isinstance(r, dict) else ""
        if old and new:
            out.append({"old": old, "new": new, "note": r.get("note", "")})
    return out


def _replacement_for(href: str, table: list[dict]) -> dict | None:
    host = _host_of(_html.unescape(href))
    if not host:
        return None
    return next((r for r in table if r["old"] == host), None)


# ---------- 1) 교체 ----------

_ANCHOR_RE = re.compile(
    r"""(<a\b[^>]*?\bhref\s*=\s*)(["'])(.*?)\2([^>]*>)((?:(?!</a>|<a\b).)*)(</a>)""", re.IGNORECASE | re.DOTALL
)


def _addr_key(s: str) -> str:
    """주소 비교용: 앞의 http(s)://, www., 끝의 / 를 무시하고 소문자로."""
    s = re.sub(r"^https?://", "", (s or "").strip(), flags=re.IGNORECASE)
    s = re.sub(r"^www\.", "", s, flags=re.IGNORECASE)
    return s.rstrip("/").lower()


def _text_is_address(text: str, old_href: str, old_domain: str) -> bool:
    """링크 글자가 옛 주소 그대로인지: 바뀌기 전 href와 같거나(위 비교), old 도메인으로 시작하는 주소 모양(공백 없음)."""
    t = _addr_key(text)
    if not t or re.search(r"\s", t):
        return False
    return t == _addr_key(old_href) or t == old_domain or t.startswith(old_domain + "/") or t.startswith(old_domain + "?")


def apply_replacements(html_text: str, table: list[dict] | None = None) -> tuple[str, list[dict]]:
    """링크(href)의 도메인이 교체표의 old와 같으면 주소를 통째로 new로 바꿉니다.
    링크 글자가 옛 주소 그대로(주소 모양)이면 글자도 new로 바꾸고, 일반 문장 글자는 그대로 둡니다.
    이미 new와 같은 주소(끝 / 차이 무시)는 바꾸지 않으므로 두 번 적용해도 결과가 같습니다.
    반환: (바뀐 HTML, [{"before","after","note","text_changed"}])."""
    table = load_replacements() if table is None else table
    changes: list[dict] = []
    if not html_text or not table:
        return html_text, changes

    def _sub(m):
        old_href = _html.unescape(m.group(3))
        row = _replacement_for(old_href, table)
        if not row or _addr_key(old_href) == _addr_key(row["new"]):
            return m.group(0)
        inner = m.group(5)
        text_changed = False
        if "<" not in inner and _text_is_address(_html.unescape(inner), old_href, row["old"]):
            inner, text_changed = _html.escape(row["new"], quote=False), True
        changes.append({"before": old_href, "after": row["new"], "note": row["note"], "text_changed": text_changed})
        return f"{m.group(1)}{m.group(2)}{_html.escape(row['new'], quote=True)}{m.group(2)}{m.group(4)}{inner}{m.group(6)}"

    return _ANCHOR_RE.sub(_sub, html_text), changes


def find_replaceable(html_text: str, table: list[dict] | None = None) -> list[dict]:
    """바꾸지는 않고, 교체표에 걸리는 링크만 [{"before","after","note"}]로 찾습니다 (이미 만들어진 글 점검용)."""
    _, changes = apply_replacements(html_text, table)
    return changes


# ---------- 2) 검사 ----------

def _own_domains() -> list[str]:
    out = list(OWN_DOMAINS)
    try:
        import config
        for u in (config.WORDPRESS_URL, config.SHOP_WORDPRESS_URL):
            h = _host_of(u or "")
            if h:
                out.append(h)
    except Exception:  # noqa: BLE001 — 설정을 못 읽어도 검사는 계속
        pass
    return out


def _is_excluded(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in AFFILIATE_DOMAINS + _own_domains())


def extract_external_links(html_text: str) -> tuple[list[str], list[str]]:
    """글 속 http(s) 링크를 순서대로, 중복 없이. 반환: (검사할 링크들, 제외 도메인이라 건너뛴 링크들)."""
    check, skipped, seen = [], [], set()
    for m in _HREF_RE.finditer(html_text or ""):
        url = _html.unescape(m.group(3)).strip()
        if not re.match(r"https?://", url, re.IGNORECASE) or url in seen:
            continue
        seen.add(url)
        (skipped if _is_excluded(_host_of(url)) else check).append(url)
    return check, skipped


def _visible_text_len(page_html: str) -> int:
    t = re.sub(r"(?is)<(script|style|noscript|head)\b.*?</\1>", " ", page_html)
    t = re.sub(r"(?s)<!--.*?-->", " ", t)
    t = _html.unescape(re.sub(r"(?s)<[^>]+>", " ", t))
    return len(re.sub(r"\s+", "", t))


def check_link(url: str) -> dict | None:
    """링크 하나 검사. 문제 없으면 None, 문제가 있으면 {"url","reason"}."""
    now = time.time()
    hit = _CACHE.get(url)
    if hit and now - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    result = _check_link_uncached(url)
    _CACHE[url] = (now, result)
    return result


_JS_REDIRECT_HINT_RE = re.compile(r"location\s*(\.href|\.replace|\.assign)?\s*(=|\()|http-equiv\s*=\s*[\"']?refresh", re.IGNORECASE)
_JS_REDIRECT_LITERAL_RE = re.compile(
    r"""(?:window\.|top\.|self\.)?location(?:\.href)?\s*=\s*["']([^"'+]+)["']\s*[;\n<}]"""
    r"""|location\.(?:replace|assign)\(\s*["']([^"'+]+)["']\s*\)""",
    re.IGNORECASE,
)
_META_REFRESH_RE = re.compile(r"""<meta[^>]+http-equiv\s*=\s*["']?refresh[^>]*content\s*=\s*["'][^"']*?url\s*=\s*([^"'>]+)""", re.IGNORECASE)


def _page_redirect(page_html: str, base_url: str) -> tuple[str | None, bool]:
    """글자가 거의 없는 페이지가 자바스크립트·meta로 다른 주소로 넘기는지. 반환: (따라갈 주소 또는 None, 넘기는 코드가 있는지).
    (공공기관 누리집은 첫 화면이 "location.href='main.do'" 한 줄뿐인 경우가 많아 빈 화면으로 오해하지 않기 위해서입니다.)"""
    code = re.sub(r"(?s)/\*.*?\*/|<!--.*?-->", " ", page_html)
    m = _META_REFRESH_RE.search(code) or _JS_REDIRECT_LITERAL_RE.search(code)
    if m:
        target = urljoin(base_url, next(g for g in m.groups() if g).strip())
        if re.match(r"https?://", target, re.IGNORECASE):  # javascript:·tel: 같은 건 따라가지 않음
            return target, True
        return None, True
    return None, bool(_JS_REDIRECT_HINT_RE.search(code))


def _in_domains(site: str, domains: list[str]) -> bool:
    return any(site == d or site.endswith("." + d) for d in domains)


def _check_link_uncached(url: str) -> dict | None:
    orig_host = _site_of(url)  # www.→m. 같은 앞부분 차이는 같은 사이트로 봅니다
    cur = url
    for _hop in range(MAX_PAGE_REDIRECTS + 1):
        try:
            resp = requests.get(
                cur, timeout=CHECK_TIMEOUT_SEC, allow_redirects=True, stream=True,
                headers={"User-Agent": MOBILE_USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"},
            )
        except requests.Timeout:
            return {"url": url, "reason": f"시간 초과 ({CHECK_TIMEOUT_SEC}초 안에 열리지 않음)"}
        except requests.RequestException as e:
            if _in_domains(orig_host, BOT_BLOCKED_DOMAINS):
                return {"url": url, "reason": BOT_BLOCKED_REASON, "soft": True}
            return {"url": url, "reason": f"연결 실패 ({type(e).__name__})"}
        try:
            if resp.status_code >= 400:
                return {"url": url, "reason": f"오류 응답 {resp.status_code}"}
            final_host = _site_of(resp.url)
            if final_host and orig_host and final_host != orig_host:
                return {"url": url, "reason": f"다른 도메인으로 넘어감 → {resp.url}"}
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype and "html" not in ctype:
                return None
            try:
                raw = next(resp.iter_content(MAX_READ_BYTES), b"")
            except requests.RequestException as e:
                return {"url": url, "reason": f"본문을 읽지 못함 ({type(e).__name__})"}
            page = raw.decode(resp.encoding or resp.apparent_encoding or "utf-8", errors="replace")
            n = _visible_text_len(page)
            if n >= MIN_BODY_TEXT_CHARS:
                return None
            target, has_redirect_code = _page_redirect(page, resp.url)
            if target and target != cur:
                cur = target  # 첫 화면이 다른 주소로 넘기기만 하는 페이지 → 따라가서 다시 확인
                continue
            if has_redirect_code or _in_domains(orig_host, SKIP_BLANK_CHECK_DOMAINS):
                return None  # 넘기는 코드는 있는데 목적지를 알 수 없음 / 빈 화면 판정 제외 도메인 → 빈 화면이라고 단정하지 않음
            return {"url": url, "reason": f"빈 화면 의심 (본문 글자 {n}자 < 기준 {MIN_BODY_TEXT_CHARS}자)"}
        finally:
            resp.close()
    return None


def check_links(urls: list[str]) -> list[dict]:
    """여러 링크를 동시에 검사해 문제 있는 것만 [{"url","reason"}]로 (입력 순서 유지)."""
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(check_link, urls))
    return [r for r in results if r]


def inspect_html(html_text: str, table: list[dict] | None = None) -> dict:
    """글 HTML 하나를 점검합니다 (아무것도 바꾸거나 저장하지 않음).
    반환: {"replaceable": [교체표에 걸린 링크], "checked": 검사한 링크 수, "skipped": 제외한 링크 수, "issues": [{"url","reason"}]}"""
    to_check, skipped = extract_external_links(html_text)
    return {
        "replaceable": find_replaceable(html_text, table),
        "checked": len(to_check),
        "skipped": len(skipped),
        "issues": check_links(to_check),
    }
