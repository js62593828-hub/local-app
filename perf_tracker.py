"""
성과 루프 1단계 — 구글 서치콘솔(GSC)에서 글별 성과를 읽어와 진단 태그를 붙입니다.

- 읽기 전용입니다. 서치콘솔·워드프레스에 쓰기/수정 요청은 절대 보내지 않습니다(webmasters.readonly 권한만 사용).
- articles*.json / content_queue*.json 은 읽기만 합니다. 새로 쓰는 파일은 perf_cache.json, perf_history.json, perf_index.json(색인 상태) 뿐입니다.
- 색인 상태 확인은 URL 검사 API로 글마다 1번씩 "조회"만 합니다(색인 요청·쓰기 없음).
- 키 파일(gsc_key.json)은 인증에만 쓰고, 내용을 화면·로그·보고 어디에도 출력하지 않습니다.
- 가져오기는 사용자가 "지금 가져오기" 버튼을 누를 때만 일어납니다(자동 실행 없음).
- 키 파일이 없거나 연결이 안 되면 화면은 가짜 숫자(목업)로 보여 주고, 목업 숫자는 파일에 저장하지 않습니다.
"""
import datetime
import hashlib
import json
import math
import os
import random
import re
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

import config

# ════════════════════════════════════════════════════════════════════════════
# 진단 기준값 — 여기 숫자만 바꾸면 화면의 진단 태그 기준이 바뀝니다.
# ════════════════════════════════════════════════════════════════════════════
WINDOW_DAYS = 28                 # 비교 기간 길이(최근 28일 vs 그 전 28일)
DATA_LAG_DAYS = 2                # 서치콘솔 데이터는 2~3일 늦게 확정되므로 오늘-2일을 기간 끝으로 씁니다
TOP_QUERIES = 5                  # 페이지별로 저장하는 상위 검색어 수(화면에는 3개 표시)

NEW_DAYS = 14                    # 🆕 판단 보류: 발행 후 이 일수 미만
TITLE_MIN_IMPRESSIONS = 100      # 🔤 제목·표지 교체 후보: 노출이 이 값 이상인데
TITLE_CTR_RATIO = 0.5            #    CTR이 그 채널 평균의 이 비율 미만
BODY_RANK_MIN = 5                # 🔧 본문 보강 후보: 평균순위가 이 범위(5~15위)이고
BODY_RANK_MAX = 15
BODY_MIN_IMPRESSIONS = 50        #    노출이 이 값 이상
DEAD_DAYS = 21                   # 💤 노출 거의 없음: 발행 후 이 일수 이상인데
DEAD_MAX_IMPRESSIONS = 20        #    노출이 이 값 미만
STAR_TOP_RATIO = 0.2             # ⭐ 잘 되는 글: 그 채널 CTR 상위 20%
STAR_MIN_IMPRESSIONS = 50        #    (노출이 이 값 이상인 글만 후보)

TAG_NEW = "🆕 판단 보류"
TAG_TITLE = "🔤 제목·표지 교체 후보"
TAG_BODY = "🔧 본문 보강 후보"
TAG_DEAD = "💤 노출 거의 없음"
TAG_STAR = "⭐ 잘 되는 글"
TAG_HINTS = {
    TAG_NEW: f"발행 {NEW_DAYS}일 미만 — 아직 판단하지 않습니다",
    TAG_TITLE: f"노출 {TITLE_MIN_IMPRESSIONS} 이상인데 CTR이 채널 평균의 {int(TITLE_CTR_RATIO * 100)}% 미만 — 제목·표지를 바꿔 보세요",
    TAG_BODY: f"평균순위 {BODY_RANK_MIN}~{BODY_RANK_MAX}위, 노출 {BODY_MIN_IMPRESSIONS} 이상 — 본문을 보강하면 올라갈 수 있어요",
    TAG_DEAD: f"발행 {DEAD_DAYS}일 이상인데 노출 {DEAD_MAX_IMPRESSIONS} 미만 — 키워드를 다시 검토하세요",
    TAG_STAR: f"채널 CTR 상위 {int(STAR_TOP_RATIO * 100)}% (노출 {STAR_MIN_IMPRESSIONS} 이상)",
}

# ════════════════════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "perf_cache.json")
HISTORY_FILE = os.path.join(BASE_DIR, "perf_history.json")

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API_BASE = "https://www.googleapis.com/webmasters/v3/sites"

# 화면 탭 순서와, 채널이 속한 서치콘솔 사이트(워드프레스 SHOP·워드프레스 일반은 같은 사이트)
TABS = [("longterm_wp", "W혜택주머니"), ("shop_wp", "워드프레스 SHOP (OLD)"), ("wp_tips", "W싼세계")]
CHANNEL_SITE = {"longterm_wp": "longterm", "shop_wp": "ssansegae", "wp_tips": "ssansegae"}
SITE_LABELS = {"longterm": "W혜택주머니 사이트", "ssansegae": "W싼세계 사이트(W싼세계 + 워드프레스 SHOP (OLD))"}


def site_urls() -> dict:
    """.env에서 읽은 서치콘솔 사이트 주소. 값이 비어 있으면 그 사이트는 건너뜁니다."""
    return {
        "longterm": (getattr(config, "GSC_SITE_LONGTERM", "") or "").strip(),
        "ssansegae": (getattr(config, "GSC_SITE_SSANSEGAE", "") or "").strip(),
    }


# ─── 주소 맞추기 ────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """서치콘솔 주소와 앱에 저장된 주소를 같은 모양으로 맞춥니다.
    순서: 퍼센트 디코딩 → 소문자 → #이후 제거 → 쿼리(?) 제거 → http/https 제거 → www 제거 → 끝 슬래시 제거."""
    s = unquote((url or "").strip()).lower()
    s = s.split("#", 1)[0].split("?", 1)[0]
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.rstrip("/")


# ─── 파일 읽기/쓰기 ─────────────────────────────────────────────────────────

def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json_atomic(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_cache():
    data = _read_json(CACHE_FILE, None)
    return data if isinstance(data, dict) and data.get("sites") else None


def load_history() -> list:
    data = _read_json(HISTORY_FILE, [])
    return data if isinstance(data, list) else []


# ─── 앱 글 목록 (읽기만) ────────────────────────────────────────────────────

def load_channel_articles(channel: str, spec: dict) -> list[dict]:
    """채널의 글 중 워드프레스 주소가 저장된 글만 돌려줍니다. spec = {"articles_file", "queue_file"}."""
    articles = _read_json(spec["articles_file"], {})
    queue = _read_json(spec["queue_file"], [])
    qmap = {q.get("id"): q for q in queue if isinstance(q, dict)}
    out = []
    for aid, art in articles.items():
        if not isinstance(art, dict):
            continue
        url = ((art.get("platform_published_url") or {}).get("wordpress") or "").strip()
        if not url:
            continue
        q = qmap.get(aid) or {}
        title = ((art.get("platform_title") or {}).get("wordpress") or "").strip() or q.get("keyword") or aid
        pub = ((art.get("platform_published_at") or {}).get("wordpress") or "").strip()
        out.append({
            "id": aid, "channel": channel, "title": title, "url": url, "norm": normalize_url(url),
            "published_at": pub[:10] or None, "keyword": q.get("keyword") or "",
            "cluster_seed": q.get("cluster_seed") or q.get("keyword") or "",
        })
    return out


def load_all_articles(channel_specs: dict) -> dict:
    return {ch: load_channel_articles(ch, channel_specs[ch]) for ch, _ in TABS if ch in channel_specs}


# ─── 서치콘솔 연결 (읽기 전용) ──────────────────────────────────────────────

class GscError(Exception):
    """사용자에게 그대로 보여 줘도 안전한 짧은 오류 메시지만 담습니다(키 내용 없음)."""


def _key_path() -> str:
    p = getattr(config, "GSC_KEY_FILE", "") or "gsc_key.json"
    return p if os.path.isabs(p) else os.path.join(BASE_DIR, p)


def key_available() -> bool:
    return os.path.isfile(_key_path())


def _safe_message(exc: Exception) -> str:
    return f"{type(exc).__name__}"


def _new_session():
    """서비스 계정 키로 읽기 전용 세션을 만듭니다. 키 내용은 읽어서 인증에만 씁니다."""
    if not key_available():
        raise GscError("키 파일(gsc_key.json)이 없습니다")
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        raise GscError("google-auth 패키지가 설치되어 있지 않습니다 (pip install google-auth)")
    try:
        creds = service_account.Credentials.from_service_account_file(_key_path(), scopes=[SCOPE])
        return AuthorizedSession(creds)
    except Exception as e:  # 키 파일 형식 오류 등 — 내용은 절대 출력하지 않고 종류만 알립니다
        raise GscError(f"키 파일을 읽지 못했습니다 ({_safe_message(e)})")


def _check(resp):
    if resp.status_code == 200:
        return resp.json()
    reason = ""
    try:
        reason = (resp.json().get("error") or {}).get("message", "")[:160]
    except Exception:
        pass
    raise GscError(f"서치콘솔 응답 오류 {resp.status_code}: {reason}".strip(": "))


def _http_get(session, url):
    try:
        return _check(session.get(url, timeout=60))
    except GscError:
        raise
    except Exception as e:
        raise GscError(f"서치콘솔에 연결하지 못했습니다 ({_safe_message(e)})")


def _query_rows(session, site: str, start: str, end: str, dims: list[str]) -> list[dict]:
    """searchAnalytics.query (읽기). 25000행씩 이어서 받습니다(최대 10번)."""
    url = f"{API_BASE}/{quote(site, safe='')}/searchAnalytics/query"
    rows, start_row = [], 0
    for _ in range(10):
        body = {"startDate": start, "endDate": end, "dimensions": dims, "rowLimit": 25000, "startRow": start_row,
                "type": "web"}
        try:
            data = _check(session.post(url, json=body, timeout=90))
        except GscError:
            raise
        except Exception as e:
            raise GscError(f"서치콘솔에 연결하지 못했습니다 ({_safe_message(e)})")
        got = data.get("rows") or []
        rows.extend(got)
        if len(got) < 25000:
            break
        start_row += 25000
    return rows


def list_accessible_sites(session) -> list[dict]:
    data = _http_get(session, API_BASE)
    return [{"site": e.get("siteUrl", ""), "level": e.get("permissionLevel", "")} for e in data.get("siteEntry", [])]


def date_ranges(today: datetime.date | None = None) -> dict:
    end = (today or datetime.date.today()) - datetime.timedelta(days=DATA_LAG_DAYS)
    cur_start = end - datetime.timedelta(days=WINDOW_DAYS - 1)
    prev_end = cur_start - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=WINDOW_DAYS - 1)
    f = lambda d: d.isoformat()
    return {"cur": [f(cur_start), f(end)], "prev": [f(prev_start), f(prev_end)]}


def _merge_page_rows(rows: list[dict]) -> dict:
    """http/https·www·끝 슬래시만 다른 같은 페이지를 합칩니다(순위는 노출 가중 평균)."""
    acc = {}
    for r in rows:
        url = r["keys"][0]
        k = normalize_url(url)
        e = acc.setdefault(k, {"url": url, "impressions": 0, "clicks": 0, "_pos": 0.0})
        imp = int(r.get("impressions", 0))
        e["impressions"] += imp
        e["clicks"] += int(r.get("clicks", 0))
        e["_pos"] += float(r.get("position", 0)) * imp
    out = {}
    for k, e in acc.items():
        imp = e["impressions"]
        out[k] = {"url": e["url"], "impressions": imp, "clicks": e["clicks"],
                  "ctr": (e["clicks"] / imp) if imp else 0.0, "position": round(e["_pos"] / imp, 2) if imp else 0.0}
    return out


def _top_queries(rows: list[dict]) -> dict:
    acc = {}
    for r in rows:
        page, query = r["keys"][0], r["keys"][1]
        d = acc.setdefault(normalize_url(page), {})
        e = d.setdefault(query, {"query": query, "impressions": 0, "clicks": 0})
        e["impressions"] += int(r.get("impressions", 0))
        e["clicks"] += int(r.get("clicks", 0))
    return {k: sorted(v.values(), key=lambda x: (-x["impressions"], -x["clicks"]))[:TOP_QUERIES] for k, v in acc.items()}


def fetch_site(session, site: str, ranges: dict) -> dict:
    cur = _merge_page_rows(_query_rows(session, site, *ranges["cur"], ["page"]))
    prev = _merge_page_rows(_query_rows(session, site, *ranges["prev"], ["page"]))
    queries = _top_queries(_query_rows(session, site, *ranges["cur"], ["page", "query"]))
    empty = {"impressions": 0, "clicks": 0, "ctr": 0.0, "position": 0.0}
    pages = {}
    for k in set(cur) | set(prev):
        base = cur.get(k) or prev.get(k)
        pages[k] = {"url": base["url"], "cur": {m: (cur[k][m] if k in cur else empty[m]) for m in empty},
                    "prev": {m: (prev[k][m] if k in prev else empty[m]) for m in empty},
                    "queries": queries.get(k, [])}
    return {"site": site, "pages": pages}


def fetch_all(channel_specs: dict) -> tuple[bool, str]:
    """사용자가 "지금 가져오기"를 눌렀을 때만 호출됩니다. 성공하면 perf_cache.json을 새로 쓰고
    perf_history.json에 오늘 요약을 덧붙입니다. 반환: (성공 여부, 안내 문구)."""
    sites = {k: v for k, v in site_urls().items() if v}
    if not sites:
        return False, "사이트 주소(GSC_SITE_LONGTERM / GSC_SITE_SSANSEGAE)가 .env에 없습니다"
    try:
        session = _new_session()
        ranges = date_ranges()
        data = {k: fetch_site(session, site, ranges) for k, site in sites.items()}
    except GscError as e:
        return False, str(e)
    cache = {"mode": "real", "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
             "ranges": ranges, "sites": data}
    _write_json_atomic(CACHE_FILE, cache)
    view = build_view(cache, load_all_articles(channel_specs))
    _append_history(cache, view)
    n_pages = sum(len(s["pages"]) for s in data.values())
    return True, f"가져왔습니다 — 서치콘솔 페이지 {n_pages}개 ({ranges['cur'][0]} ~ {ranges['cur'][1]})"


def _append_history(cache: dict, view: dict) -> None:
    """가져올 때마다 채널별 요약(+글별 노출·클릭·순위 스냅샷)을 날짜별로 덧붙입니다 — 나중에 개선 전후 비교용."""
    entry = {"date": datetime.date.today().isoformat(), "fetched_at": cache["fetched_at"], "ranges": cache["ranges"],
             "channels": {}, "articles": {}}
    for ch, cv in view["channels"].items():
        s = cv["summary"]
        entry["channels"][ch] = {k: s[k] for k in ("articles", "with_data", "impressions", "clicks", "ctr", "position")}
        entry["articles"].update({f"{ch}:{r['id']}": [r["impressions"], r["clicks"], r["position"]] for r in cv["rows"]})
    entry["off_app"] = {k: {"pages": len(v), "impressions": sum(p["impressions"] for p in v),
                            "clicks": sum(p["clicks"] for p in v)} for k, v in view["off_app"].items()}
    hist = load_history()
    hist.append(entry)
    _write_json_atomic(HISTORY_FILE, hist)


# ─── 연결 테스트 ────────────────────────────────────────────────────────────

def _host_of(site: str) -> str:
    if site.startswith("sc-domain:"):
        host = site[len("sc-domain:"):]
    else:
        host = urlparse(site).netloc or site
    return re.sub(r"^www\.", "", host.strip().lower().rstrip("/"))


def test_connection(channel_specs: dict) -> dict:
    """키 파일·사이트 형식을 확인합니다(읽기만, 파일 저장 없음). 반환값은 화면에 그대로 보여 줘도 안전합니다."""
    result = {"key_found": key_available(), "error": "", "sites": []}
    if not result["key_found"]:
        result["error"] = "키 없음 — 목업 모드로만 확인할 수 있습니다"
        return result
    try:
        session = _new_session()
        accessible = list_accessible_sites(session)
        ranges = date_ranges()
        articles = load_all_articles(channel_specs)
        for key, configured in site_urls().items():
            entry = {"key": key, "label": SITE_LABELS[key], "configured": configured, "format_ok": False,
                     "suggest": "", "pages": None, "linked": None, "unlinked": None, "unlinked_examples": [],
                     "note": ""}
            result["sites"].append(entry)
            if not configured:
                entry["note"] = ".env에 사이트 주소가 비어 있습니다"
                continue
            names = [a["site"] for a in accessible]
            if configured in names:
                entry["format_ok"] = True
                use = configured
            else:
                same_host = [n for n in names if _host_of(n) == _host_of(configured)]
                if same_host:
                    use = same_host[0]
                    entry["suggest"] = use
                    entry["note"] = f"이 형식은 맞지 않습니다. 이 서비스 계정이 가진 형식은 '{use}' 입니다 (.env를 이걸로 바꾸세요)"
                else:
                    entry["note"] = "이 서비스 계정에 이 사이트의 권한이 없습니다(서치콘솔 '사용자 및 권한'에 서비스 계정 추가 필요)"
                    entry["accessible"] = names
                    continue
            rows = _query_rows(session, use, *ranges["cur"], ["page"])
            pages = _merge_page_rows(rows)
            mine = {a["norm"] for ch, lst in articles.items() if CHANNEL_SITE[ch] == key for a in lst}
            matched = [k for k in pages if k in mine]
            unlinked = [pages[k]["url"] for k in pages if k not in mine]
            entry.update(pages=len(pages), linked=len(matched), unlinked=len(unlinked),
                         unlinked_examples=[unquote(u) for u in unlinked[:5]])
    except GscError as e:
        result["error"] = str(e)
    return result


# ─── 색인 상태 확인 (서치콘솔 URL 검사 API, 조회만) ─────────────────────────

INDEX_FILE = os.path.join(BASE_DIR, "perf_index.json")
INSPECT_API = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
INDEX_MIN_AGE_DAYS = 0           # 발행일이 오늘 이하인 글(= 이미 공개된 글)만 확인. 예약돼 아직 공개 전인 글은 날짜가 미래라 자동 제외
INDEX_MAX_PER_RUN = 120          # 버튼 한 번에 확인하는 최대 글 수
INDEX_SLEEP_SEC = 0.5            # 호출 사이 간격(초)

IDX_OK, IDX_CRAWLED, IDX_FOUND, IDX_UNKNOWN, IDX_OTHER = "ok", "crawled", "found", "unknown", "other"
IDX_ICONS = {IDX_OK: "✅", IDX_CRAWLED: "⏳", IDX_FOUND: "🔍", IDX_UNKNOWN: "❓", IDX_OTHER: "⚠"}
IDX_LABELS = {IDX_OK: "색인됨", IDX_CRAWLED: "크롤링됨-색인 안 됨", IDX_FOUND: "발견됨-아직 크롤링 안 됨",
              IDX_UNKNOWN: "구글이 모르는 주소", IDX_OTHER: "그 밖의 문제"}


class IndexStop(GscError):
    """한도 초과·권한 오류처럼 거기서 멈춰야 하는 경우. 메시지는 화면에 그대로 보여 줍니다."""


def load_index() -> dict:
    data = _read_json(INDEX_FILE, {})
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def index_targets(articles_by_channel: dict, today: datetime.date | None = None) -> list[dict]:
    """색인 확인 대상: 이미 공개된 글(발행일이 오늘 이하). 날짜 없는 글·예약 글은 제외."""
    today = today or datetime.date.today()
    out = []
    for ch, _label in TABS:
        for a in articles_by_channel.get(ch, []):
            age = _days_since(a.get("published_at"), today)
            if age is not None and age >= INDEX_MIN_AGE_DAYS:
                out.append({"key": f"{ch}:{a['id']}", "channel": ch, "id": a["id"], "url": a["url"],
                            "site": CHANNEL_SITE[ch], "title": a["title"], "published_at": a["published_at"]})
    return out


def inspect_url(session, site: str, url: str) -> dict:
    """URL 검사 API를 글 1개에 대해 1번 호출합니다(조회만). 저장할 필드만 골라 돌려줍니다."""
    try:
        resp = session.post(INSPECT_API, json={"inspectionUrl": url, "siteUrl": site, "languageCode": "en-US"}, timeout=60)
    except Exception as e:
        raise GscError(f"서치콘솔에 연결하지 못했습니다 ({_safe_message(e)})")
    if resp.status_code != 200:
        reason = ""
        try:
            reason = (resp.json().get("error") or {}).get("message", "")[:160]
        except Exception:
            pass
        if resp.status_code == 429 or (resp.status_code == 403 and "quota" in reason.lower()):
            raise IndexStop("서치콘솔 하루 검사 한도에 도달했어요. 내일 다시 눌러 주세요.")
        if resp.status_code == 403:
            raise IndexStop("권한이 부족해요. 서치콘솔에서 로봇 계정 권한을 '전체'로 올려야 할 수 있어요.")
        if resp.status_code == 401:
            raise IndexStop("서치콘솔 인증에 실패했어요. 키 파일(gsc_key.json)을 확인해 주세요.")
        raise GscError(f"응답 오류 {resp.status_code}: {reason}".strip(": "))
    st = ((resp.json().get("inspectionResult") or {}).get("indexStatusResult")) or {}
    return {"verdict": st.get("verdict"), "coverageState": st.get("coverageState"),
            "lastCrawlTime": st.get("lastCrawlTime"), "googleCanonical": st.get("googleCanonical"),
            "userCanonical": st.get("userCanonical"), "robotsTxtState": st.get("robotsTxtState"),
            "pageFetchState": st.get("pageFetchState")}


SRC_CACHE, SRC_STD, SRC_RETRY = "①", "②", "재시도"


def standard_url(url: str) -> str:
    """주소의 경로를 퍼센트 디코딩한 뒤 표준 형식(대문자 %XX)으로 다시 인코딩합니다. 스킴·도메인·끝 슬래시는 그대로."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, quote(unquote(p.path), safe="/:@!$&'()*+,;=-._~"), p.query, p.fragment))


def pick_inspect_url(target: dict, cache: dict | None) -> tuple[str, str]:
    """URL 검사에 보낼 주소를 고릅니다. ① perf_cache.json에 같은 글의 서치콘솔 주소가 있으면 그대로,
    ② 없으면 저장된 주소를 표준 형식으로 다시 인코딩. 반환: (주소, ①/②)."""
    pages = ((cache or {}).get("sites", {}).get(target["site"]) or {}).get("pages", {})
    hit = pages.get(normalize_url(target["url"]))
    if hit and hit.get("url"):
        return hit["url"], SRC_CACHE
    return standard_url(target["url"]), SRC_STD


def _is_unknown(res: dict) -> bool:
    return "unknown to google" in (res.get("coverageState") or "").lower()


def inspect_with_fallback(session, site: str, target: dict, cache: dict | None, sleep) -> dict:
    """위 순서로 조회하고, ②로 조회했는데 '모르는 주소'면 저장된 원래 주소로 한 번 더 조회해 더 나은 결과를 씁니다."""
    url, src = pick_inspect_url(target, cache)
    res = inspect_url(session, site, url)
    if src == SRC_STD and _is_unknown(res) and url != target["url"]:
        sleep(INDEX_SLEEP_SEC)
        try:
            again = inspect_url(session, site, target["url"])
        except GscError:
            again = res  # 재시도만 실패(한도·권한 포함)해도 첫 결과는 살립니다. 한도·권한 오류는 다음 글 호출에서 다시 만나 멈춥니다
        if not _is_unknown(again):
            res, url, src = again, target["url"], SRC_RETRY
    return {**res, "inspected_url": url, "url_source": src}


def run_index_check(channel_specs: dict, today: datetime.date | None = None, sleep=None) -> tuple[bool, str]:
    """사용자가 "색인 상태 확인"을 눌렀을 때만 호출됩니다. 아직 안 본 글 → 오래전에 본 글 순으로 최대 INDEX_MAX_PER_RUN개를
    검사하고 perf_index.json에 저장합니다. 반환: (성공 여부, 안내 문구)."""
    import time
    sleep = sleep or time.sleep
    today = today or datetime.date.today()
    sites = site_urls()
    done = load_index()
    cache = load_cache()  # 읽기만 — ①번 주소 찾기용
    targets = index_targets(load_all_articles(channel_specs), today)
    skipped_site = [t for t in targets if not sites.get(t["site"])]
    targets = [t for t in targets if sites.get(t["site"])]
    if not targets:
        return False, ("확인할 글이 없어요 (공개된 글이 없거나 .env의 사이트 주소가 비어 있어요)")
    targets.sort(key=lambda t: ((done.get(t["key"]) or {}).get("checked_at") or "", t["published_at"] or ""))
    batch, rest = targets[:INDEX_MAX_PER_RUN], max(0, len(targets) - INDEX_MAX_PER_RUN)
    try:
        session = _new_session()
    except GscError as e:
        return False, str(e)
    n_ok, n_fail, stop_msg = 0, 0, ""
    for i, t in enumerate(batch):
        if i:
            sleep(INDEX_SLEEP_SEC)
        try:
            res = inspect_with_fallback(session, sites[t["site"]], t, cache, sleep)
        except IndexStop as e:
            stop_msg = str(e)
            break
        except GscError:
            n_fail += 1  # 이 글만 못 본 것 — 이전 결과는 그대로 두고 다음 글로 넘어갑니다
            continue
        done[t["key"]] = {"checked_at": today.isoformat(), "url": t["url"], **res}
        n_ok += 1
    if n_ok:
        _write_json_atomic(INDEX_FILE, {"updated_at": datetime.datetime.now().isoformat(timespec="seconds"), "items": done})
    parts = [f"{n_ok}개 글의 색인 상태를 확인했어요"]
    if n_fail:
        parts.append(f"{n_fail}개는 확인하지 못했어요(주소가 사이트와 안 맞는 경우 등)")
    if rest and not stop_msg:
        parts.append(f"한 번에 최대 {INDEX_MAX_PER_RUN}개라서 {rest}개가 남았어요 — 한 번 더 누르면 이어서 확인해요")
    if skipped_site:
        parts.append(f"사이트 주소가 .env에 없어서 {len(skipped_site)}개는 건너뛰었어요")
    if stop_msg:
        parts.append("중간에 멈췄어요: " + stop_msg)
    return (not stop_msg), " · ".join(parts)


def classify_index(entry: dict | None, article_url: str) -> dict | None:
    """저장된 검사 결과 1건을 화면용으로 풀어 줍니다. 결과가 없으면 None."""
    if not entry:
        return None
    verdict = (entry.get("verdict") or "").upper()
    cov = (entry.get("coverageState") or "").strip()
    low = cov.lower()
    if verdict == "PASS":
        kind = IDX_OK
    elif "crawled" in low and "not indexed" in low:
        kind = IDX_CRAWLED
    elif "discovered" in low and "not indexed" in low:
        kind = IDX_FOUND
    elif "unknown to google" in low:
        kind = IDX_UNKNOWN
    else:
        kind = IDX_OTHER
    g = entry.get("googleCanonical")
    canon_diff = bool(g) and normalize_url(g) != normalize_url(article_url)
    crawl = (entry.get("lastCrawlTime") or "")[:10]
    return {
        "kind": kind, "icon": IDX_ICONS[kind], "label": IDX_LABELS[kind] if kind != IDX_OTHER else (cov or "상태 알 수 없음"),
        "coverage": cov, "checked_at": entry.get("checked_at") or "", "last_crawl": crawl,
        "canonical_diff": canon_diff, "google_canonical": g or "", "user_canonical": entry.get("userCanonical") or "",
        "robots_blocked": (entry.get("robotsTxtState") or "").upper() == "DISALLOWED",
        "fetch_state": entry.get("pageFetchState") or "",
        "inspected_url": entry.get("inspected_url") or entry.get("url") or article_url,
    }


INDEX_RETRY_DAYS = 7             # 요청한 지 이 일수가 지나도 ✅가 아니면 "다시 요청해 볼 만함"으로 목록 위로 올립니다
INDEX_BATCH_COPY = 10            # "아직 요청 안 한 주소 N개 복사" 버튼의 개수(사이트당 하루 요청 한도 안내와 같은 값)
REQ_FILE = os.path.join(BASE_DIR, "perf_index_requests.json")


def load_requests() -> dict:
    """{ "채널:글id": "YYYY-MM-DD" } — 사용자가 서치콘솔에서 색인 요청을 했다고 표시한 글(화면 표시용 기록)."""
    data = _read_json(REQ_FILE, {})
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def set_request(key: str, on: bool, today: datetime.date | None = None) -> str | None:
    """[요청함 ✓] 켜기(오늘 날짜 저장)/끄기(기록 삭제). 쓰는 파일은 perf_index_requests.json 하나뿐입니다."""
    today = today or datetime.date.today()
    data = load_requests()
    if on:
        data[key] = today.isoformat()
    else:
        data.pop(key, None)
    _write_json_atomic(REQ_FILE, data)
    return data.get(key)


def copy_url_for(entry: dict | None, article: dict, site_key: str, cache: dict | None) -> str:
    """복사할 주소: 검사 때 실제로 조회한 주소(inspected_url, 구글이 아는 대문자 %XX 형식). 없으면 서치콘솔 기록 주소 → 저장 주소 재인코딩."""
    if entry and entry.get("inspected_url"):
        return entry["inspected_url"]
    return pick_inspect_url({"site": site_key, "url": article["url"]}, cache)[0]

def build_mock_index(articles_by_channel: dict, today: datetime.date | None = None) -> dict:
    """화면 확인용 가짜 색인 결과(파일에 저장하지 않습니다). 모양은 실제 perf_index.json 항목과 같습니다."""
    today = today or datetime.date.today()
    samples = [
        ("PASS", "Submitted and indexed", "SUCCESSFUL", "PASS"), ("PASS", "Indexed, not submitted in sitemap", "SUCCESSFUL", "PASS"),
        ("PASS", "Submitted and indexed", "SUCCESSFUL", "CANON"), ("NEUTRAL", "Crawled - currently not indexed", "SUCCESSFUL", "PASS"),
        ("NEUTRAL", "Discovered - currently not indexed", None, None), ("NEUTRAL", "URL is unknown to Google", None, None),
        ("FAIL", "Blocked by robots.txt", "BLOCKED_ROBOTS_TXT", "PASS"),
    ]
    out = {}
    for t in index_targets(articles_by_channel, today):
        rnd = random.Random(int(hashlib.md5(t["url"].encode("utf-8")).hexdigest()[:8], 16))
        v, cov, fetch, canon = rnd.choice(samples[:3] * 2 + samples[3:])
        crawl = (today - datetime.timedelta(days=rnd.randint(1, 20))).isoformat() + "T03:10:00Z" if cov and "unknown" not in cov and "Discovered" not in cov else None
        out[t["key"]] = {"checked_at": today.isoformat(), "url": t["url"], "verdict": v, "coverageState": cov,
                         "lastCrawlTime": crawl, "pageFetchState": fetch,
                         "googleCanonical": (t["url"].rstrip("/") + "-old/") if canon == "CANON" else (t["url"] if crawl else None),
                         "userCanonical": t["url"] if crawl else None,
                         "robotsTxtState": "DISALLOWED" if fetch == "BLOCKED_ROBOTS_TXT" else ("ALLOWED" if crawl else None)}
    return out


# ─── 진단 ───────────────────────────────────────────────────────────────────

def _days_since(date_str: str | None, today: datetime.date) -> int | None:
    if not date_str:
        return None
    try:
        return (today - datetime.date.fromisoformat(date_str[:10])).days
    except ValueError:
        return None


def diagnose(rows: list[dict], today: datetime.date | None = None) -> float:
    """rows(한 채널의 글들)에 진단 태그를 붙입니다(row["tags"]). 반환: 그 채널 평균 CTR(전체 클릭÷전체 노출).
    row에 필요한 값: impressions, clicks, ctr, position, published_at."""
    today = today or datetime.date.today()
    total_imp = sum(r["impressions"] for r in rows)
    avg_ctr = (sum(r["clicks"] for r in rows) / total_imp) if total_imp else 0.0
    for r in rows:
        r["tags"] = []
        r["age_days"] = _days_since(r.get("published_at"), today)
    star_pool = []
    for r in rows:
        age, imp = r["age_days"], r["impressions"]
        if age is not None and age < NEW_DAYS:
            r["tags"].append(TAG_NEW)
            continue
        if imp >= TITLE_MIN_IMPRESSIONS and avg_ctr > 0 and r["ctr"] < avg_ctr * TITLE_CTR_RATIO:
            r["tags"].append(TAG_TITLE)
        if BODY_RANK_MIN <= r["position"] <= BODY_RANK_MAX and imp >= BODY_MIN_IMPRESSIONS:
            r["tags"].append(TAG_BODY)
        if age is not None and age >= DEAD_DAYS and imp < DEAD_MAX_IMPRESSIONS:
            r["tags"].append(TAG_DEAD)
        if imp >= STAR_MIN_IMPRESSIONS:
            star_pool.append(r)
    if star_pool:
        star_pool.sort(key=lambda x: -x["ctr"])
        cutoff = star_pool[max(1, math.ceil(len(star_pool) * STAR_TOP_RATIO)) - 1]["ctr"]
        for r in star_pool:
            if r["ctr"] >= cutoff and r["ctr"] > 0:
                r["tags"].append(TAG_STAR)
    return avg_ctr


# ─── 제목 틀 분류 ───────────────────────────────────────────────────────────

TITLE_PATTERNS = ["쉼표형 (키워드, ~)", "질문형", "숫자 포함", "그 외"]


def classify_title(title: str) -> list[str]:
    """제목을 틀로 분류합니다(여러 개 해당 가능, 아무것도 아니면 '그 외')."""
    t = (title or "").strip()
    found = []
    if re.match(r"^[^,，]+[,，]\s*\S", t):
        found.append(TITLE_PATTERNS[0])
    if "?" in t or "？" in t or re.search(r"(까요|일까|나요|할까|뭘까|어떻게|왜)", t):
        found.append(TITLE_PATTERNS[1])
    if re.search(r"\d", t):
        found.append(TITLE_PATTERNS[2])
    return found or [TITLE_PATTERNS[3]]


def pattern_table(rows: list[dict]) -> list[dict]:
    """틀별 글 수 · 평균 CTR(전체 클릭÷전체 노출) · 평균 순위(노출 가중). 노출 없는 글은 글 수에만 셉니다."""
    out = []
    for name in TITLE_PATTERNS:
        sel = [r for r in rows if name in classify_title(r["title"])]
        with_data = [r for r in sel if r["impressions"] > 0]
        imp = sum(r["impressions"] for r in with_data)
        out.append({
            "name": name, "count": len(sel),
            "ctr": (sum(r["clicks"] for r in with_data) / imp) if imp else None,
            "position": (sum(r["position"] * r["impressions"] for r in with_data) / imp) if imp else None,
        })
    return out


# ─── 화면용 데이터 만들기 ───────────────────────────────────────────────────

def _change(cur, prev) -> dict:
    def pct(a, b):
        return None if not b else (a - b) / b * 100
    return {
        "impressions_pct": pct(cur["impressions"], prev["impressions"]),
        "clicks_pct": pct(cur["clicks"], prev["clicks"]),
        "clicks_diff": cur["clicks"] - prev["clicks"],
        "ctr_diff": (cur["ctr"] - prev["ctr"]) * 100 if prev["impressions"] and cur["impressions"] else None,
        # 순위는 숫자가 작을수록 좋으므로 (지난 순위 - 지금 순위) = 양수면 좋아진 것
        "position_gain": (prev["position"] - cur["position"]) if prev["position"] and cur["position"] else None,
    }


def build_view(cache: dict, articles_by_channel: dict, today: datetime.date | None = None, index: dict | None = None,
               requests: dict | None = None) -> dict:
    """캐시(서치콘솔 숫자)와 앱 글 목록을 URL로 맞춰서 화면에 보여 줄 표를 만듭니다.
    index = 색인 확인 결과(선택), requests = "요청함" 표시 기록(선택)."""
    index = index or {}
    is_mock = cache.get("mode") == "mock"
    requests = {} if is_mock else (requests or {})  # 목업에는 요청함 표시를 쓰지 않습니다
    tab_labels = dict(TABS)
    site_items = {"longterm": [], "ssansegae": []}
    today = today or datetime.date.today()
    zero = {"impressions": 0, "clicks": 0, "ctr": 0.0, "position": 0.0}
    used = {"longterm": set(), "ssansegae": set()}
    channels = {}
    for ch, _label in TABS:
        site_key = CHANNEL_SITE[ch]
        pages = (cache["sites"].get(site_key) or {}).get("pages", {})
        rows = []
        for a in articles_by_channel.get(ch, []):
            p = pages.get(a["norm"])
            if p:
                used[site_key].add(a["norm"])
            cur, prev = (p["cur"], p["prev"]) if p else (zero, zero)
            rows.append({
                "id": a["id"], "title": a["title"], "url": a["url"], "published_at": a["published_at"],
                "cluster_seed": a["cluster_seed"], "has_data": bool(p),
                "impressions": cur["impressions"], "clicks": cur["clicks"], "ctr": cur["ctr"], "position": cur["position"],
                "prev": prev, "change": _change(cur, prev),
                "queries": (p or {}).get("queries", []),
            })
        avg_ctr = diagnose(rows, today)
        idx_counts = {k: 0 for k in IDX_ICONS}
        canon_diff_n = 0
        for r in rows:
            key = f"{ch}:{r['id']}"
            entry = index.get(key)
            r["index"] = classify_index(entry, r["url"])
            r["index_eligible"] = r["age_days"] is not None and r["age_days"] >= INDEX_MIN_AGE_DAYS  # 이미 공개된 글
            r["key"] = key
            # 색인 요청 대상 = 이미 공개된 글 중 ✅ 색인됨이 아닌 글(확인 전 글 포함, 🆕 새 글도 포함)
            r["need"] = r["index_eligible"] and not (r["index"] and r["index"]["kind"] == IDX_OK)
            r["req_state"], r["req_at"] = None, requests.get(key)
            if r["need"]:
                r["copy_url"] = copy_url_for(entry, {"url": r["url"]}, site_key, cache)
                r["show_url"] = unquote(r["copy_url"])
                if r["req_at"]:
                    days = _days_since(r["req_at"], today)
                    r["req_state"] = "retry" if (days is not None and days >= INDEX_RETRY_DAYS) else "done"
                else:
                    r["req_state"] = "pending"
                ix = r["index"]
                site_items[site_key].append({
                    "key": key, "channel": tab_labels[ch], "title": r["title"], "published_at": r["published_at"],
                    "icon": ix["icon"] if ix else "◻", "label": ix["label"] if ix else "확인 전", "unchecked": ix is None,
                    "is_new": r["age_days"] is not None and r["age_days"] < NEW_DAYS,
                    "show_url": r["show_url"], "copy_url": r["copy_url"], "state": r["req_state"], "req_at": r["req_at"],
                })
            if r["index"]:
                idx_counts[r["index"]["kind"]] += 1
                canon_diff_n += r["index"]["canonical_diff"]
        total_imp = sum(r["impressions"] for r in rows)
        total_clk = sum(r["clicks"] for r in rows)
        with_pos = [r for r in rows if r["impressions"] > 0]
        summary = {
            "articles": len(rows), "with_data": len(with_pos), "impressions": total_imp, "clicks": total_clk,
            "ctr": avg_ctr,
            "position": (sum(r["position"] * r["impressions"] for r in with_pos) / total_imp) if total_imp else 0.0,
            "index_counts": idx_counts, "index_checked": sum(idx_counts.values()), "index_ok": idx_counts[IDX_OK],
            "index_canon_diff": canon_diff_n, "index_eligible": sum(1 for r in rows if r["index_eligible"]),
        }
        clusters = []
        if ch == "longterm_wp":
            groups = {}
            for r in rows:
                g = groups.setdefault(r["cluster_seed"] or "(없음)", {"seed": r["cluster_seed"] or "(없음)", "rows": [],
                                                                    "impressions": 0, "clicks": 0})
                g["rows"].append(r)
                g["impressions"] += r["impressions"]
                g["clicks"] += r["clicks"]
            for g in groups.values():
                g["ctr"] = (g["clicks"] / g["impressions"]) if g["impressions"] else 0.0
            clusters = sorted(groups.values(), key=lambda g: (-g["impressions"], g["seed"]))
        channels[ch] = {"rows": rows, "summary": summary, "patterns": pattern_table(rows), "clusters": clusters}
    off_app = {}
    for key in ("longterm", "ssansegae"):
        pages = (cache["sites"].get(key) or {}).get("pages", {})
        lst = [{"url": unquote(p["url"]), **p["cur"]} for k, p in pages.items() if k not in used[key] and p["cur"]["impressions"] > 0]
        off_app[key] = sorted(lst, key=lambda x: -x["impressions"])
    index_sites = {}
    for key, items in site_items.items():
        # 아직 요청 안 한 글(다시 요청해 볼 만한 글 포함) 먼저, 그 안에서는 발행일 오래된 순. 요청함은 아래로.
        items.sort(key=lambda x: (x["state"] == "done", x["published_at"] or "9999", x["title"]))
        pending = [x for x in items if x["state"] != "done"]
        index_sites[key] = {
            "rows": items, "total": len(items), "pending": len(pending),
            "unchecked": sum(x["unchecked"] for x in items), "new": sum(x["is_new"] for x in items),
            "copy_batch": "\n".join(x["copy_url"] for x in pending[:INDEX_BATCH_COPY]),
            "copy_batch_n": min(len(pending), INDEX_BATCH_COPY),
        }
    return {"channels": channels, "off_app": off_app, "index_sites": index_sites, "can_copy": not is_mock}


# ─── 목업(가짜 숫자) ────────────────────────────────────────────────────────

def build_mock_cache(articles_by_channel: dict, today: datetime.date | None = None) -> dict:
    """실제 글 제목·URL에 가짜 숫자만 붙인 캐시. 화면 확인용이며 파일에는 저장하지 않습니다."""
    today = today or datetime.date.today()
    ranges = date_ranges(today)
    sites = {"longterm": {"site": "(목업)", "pages": {}}, "ssansegae": {"site": "(목업)", "pages": {}}}
    suffixes = ["", " 후기", " 방법", " 추천", " 비교"]
    for ch, lst in articles_by_channel.items():
        for a in lst:
            rnd = random.Random(int(hashlib.md5(a["norm"].encode("utf-8")).hexdigest()[:8], 16))
            imp = 0 if rnd.random() < 0.15 else int(10 ** rnd.uniform(1.0, 3.4))
            ctr = rnd.uniform(0.003, 0.09)
            clk = int(imp * ctr)
            pos = round(rnd.uniform(2.5, 45), 2)
            f = rnd.uniform(0.5, 1.6)
            pimp = int(imp * f)
            pclk = int(pimp * rnd.uniform(0.003, 0.09))
            kw = a["keyword"] or a["title"]
            qs, remain = [], imp
            for s in rnd.sample(suffixes, 5):
                q_imp = int(remain * rnd.uniform(0.2, 0.5))
                remain -= q_imp
                qs.append({"query": kw + s, "impressions": q_imp, "clicks": int(q_imp * ctr)})
            sites[CHANNEL_SITE[ch]]["pages"][a["norm"]] = {
                "url": a["url"],
                "cur": {"impressions": imp, "clicks": clk, "ctr": (clk / imp) if imp else 0.0, "position": pos if imp else 0.0},
                "prev": {"impressions": pimp, "clicks": pclk, "ctr": (pclk / pimp) if pimp else 0.0, "position": round(pos + rnd.uniform(-4, 4), 2) if pimp else 0.0},
                "queries": sorted(qs, key=lambda x: -x["impressions"]),
            }
    off = {"longterm": ["https://info-labs.co.kr/", "https://info-labs.co.kr/about/"],
           "ssansegae": ["https://ssansegae.com/", "https://old-blog.tistory.com/123", "https://old-blog.tistory.com/77"]}
    for key, urls in off.items():
        for i, u in enumerate(urls):
            imp = 40 + i * 25
            sites[key]["pages"][normalize_url(u)] = {
                "url": u, "cur": {"impressions": imp, "clicks": i, "ctr": i / imp, "position": 9.5 + i},
                "prev": {"impressions": imp, "clicks": 0, "ctr": 0.0, "position": 10.0}, "queries": []}
    return {"mode": "mock", "fetched_at": "", "ranges": ranges, "sites": sites}
