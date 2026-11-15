"""
키워드별 작년 검색 정점 조회 — 🎯 타깃 키워드 발굴용 (작업명 target_serp_v1 → v1b).

  - 데이터랩 검색어트렌드 API(주 단위, 작년 1/1~12/31): 작년 정점 주(peak_date = 그 주 시작일)
  ※ 네이버 블로그 검색 API(문서 수·최근 글·내 글 순위)는 이 계정에서 쓸 수 없어 v1b에서 뺐습니다. 경쟁은 화면에서 사람이 직접 고릅니다.
  저장하는 것은 날짜 하나뿐입니다. 결과는 새 파일 serp_cache.json에만 7일간 캐시합니다.

.env 키 이름: NAVER_SEARCH_CLIENT_ID / NAVER_SEARCH_CLIENT_SECRET / NAVER_SEARCH_KEY_TYPE("hub" 또는 "devcenter", 기본 "hub").
NAVER_SEARCH_* 가 비어 있으면 기존 키를 읽습니다 (hub → NAVER_HUB_CLIENT_ID/SECRET, devcenter → NAVER_LOGIN_CLIENT_ID/SECRET).
키가 없거나 호출이 실패하면 그 칸만 None(화면에는 "-")이고 오류로 멈추지 않습니다. 키 값은 어디에도 출력하지 않습니다.
"""
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta

import requests

import config  # .env를 환경변수로 읽어 들이는 효과 때문에 먼저 불러옵니다

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.environ.get("SERP_CACHE_FILE") or os.path.join(BASE_DIR, "serp_cache.json")  # 환경변수는 확인 실행에서 사본을 쓰려는 용도
CACHE_TTL_SEC = 7 * 24 * 3600
MAX_PER_RUN = 200            # 한 번 조회에 최대 키워드 수 (하루 한도: 트렌드 1,000)
CALL_GAP_SEC = 0.15          # 호출 사이 간격
TREND_GROUP_MAX = 5          # 트렌드 요청당 키워드 그룹 수

HUB_BASE = "https://naverapihub.apigw.ntruss.com"
HUB_TREND_PATH = "/search-trend/v1/search"
DEV_TREND_URL = "https://openapi.naver.com/v1/datalab/search"

_lock = threading.Lock()
_last_call = [0.0]


def _norm(k: str) -> str:
    return re.sub(r"\s+", "", k or "").lower()


# ---------- 키 ----------

def _key_info() -> tuple[str, str, str]:
    """(키 종류, id, secret). 값은 호출할 때마다 환경변수에서 읽습니다."""
    ktype = (os.environ.get("NAVER_SEARCH_KEY_TYPE") or "hub").strip().lower()
    if ktype not in ("hub", "devcenter"):
        ktype = "hub"
    cid = os.environ.get("NAVER_SEARCH_CLIENT_ID", "").strip()
    sec = os.environ.get("NAVER_SEARCH_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        fb = ("NAVER_HUB_CLIENT_ID", "NAVER_HUB_CLIENT_SECRET") if ktype == "hub" else ("NAVER_LOGIN_CLIENT_ID", "NAVER_LOGIN_CLIENT_SECRET")
        cid, sec = os.environ.get(fb[0], "").strip(), os.environ.get(fb[1], "").strip()
    return ktype, cid, sec


def configured() -> bool:
    _, cid, sec = _key_info()
    return bool(cid and sec)


def key_type() -> str:
    return _key_info()[0]


def _headers() -> dict:
    ktype, cid, sec = _key_info()
    h = ({"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec} if ktype == "hub"
         else {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec})
    h["Content-Type"] = "application/json"
    return h


# ---------- 캐시 (새 파일 serp_cache.json, 7일) ----------

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    now = time.time()
    cache = {k: v for k, v in cache.items() if k.startswith("t|") and now - v.get("ts", 0) < CACHE_TTL_SEC}  # 옛 블로그 검색 항목(b|…)은 버림
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def _fresh(entry) -> bool:
    return bool(entry) and time.time() - entry.get("ts", 0) < CACHE_TTL_SEC


def _combine(norm: str, cache: dict) -> dict:
    t = cache.get("t|" + norm)
    tv = t["v"] if _fresh(t) else {}
    return {"peak_date": tv.get("peak_date"), "has_trend": bool(tv)}


def cached_lookup():
    """캐시만 읽는 조회 함수(네트워크 없음): f(키워드) → {"peak_date": … 또는 None}. 화면을 그릴 때 한 번 만들어 씁니다."""
    cache = _load_cache()
    return lambda kw: _combine(_norm(kw), cache)


# ---------- 호출 ----------

def _throttle() -> None:
    wait = CALL_GAP_SEC - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _trend_peaks(chunk: list[str]) -> dict:
    """{정규화키워드: 작년 정점 주 시작일(YYYY-MM-DD) 또는 None}. 데이터가 전혀 없으면 None."""
    ly = date.today().year - 1
    payload = {
        "startDate": f"{ly}-01-01", "endDate": f"{ly}-12-31", "timeUnit": "week",
        "keywordGroups": [{"groupName": k, "keywords": [k]} for k in chunk],
    }
    url = HUB_BASE + HUB_TREND_PATH if _key_info()[0] == "hub" else DEV_TREND_URL
    resp = requests.post(url, headers=_headers(), json=payload, timeout=20)
    resp.raise_for_status()
    out = {}
    for r in resp.json().get("results", []):
        data = [(d.get("period", ""), d.get("ratio", 0) or 0) for d in r.get("data", [])]
        best = max(data, key=lambda x: x[1], default=None)
        out[_norm(r.get("title", ""))] = best[0] if best and best[1] > 0 else None
    return out


def fetch(keywords: list[str]) -> tuple[dict, list[str]]:
    """키워드들의 작년 정점을 조회합니다(캐시 우선, 최대 MAX_PER_RUN개). 반환: ({정규화키워드: {"peak_date"}}, 안내 문구 목록).
    키가 없으면 캐시에 있는 것만 돌려주고, 호출 실패 칸은 None입니다."""
    notes: list[str] = []
    uniq = list(dict.fromkeys(k for k in keywords if _norm(k)))
    if len(uniq) > MAX_PER_RUN:
        notes.append(f"한 번에 {MAX_PER_RUN}개까지만 조회해 나머지 {len(uniq) - MAX_PER_RUN}개는 비워 두었어요.")
        uniq = uniq[:MAX_PER_RUN]
    with _lock:
        cache = _load_cache()
        if configured():
            todo = [k for k in uniq if not _fresh(cache.get("t|" + _norm(k)))]
            fails, first_err = 0, ""
            for i in range(0, len(todo), TREND_GROUP_MAX):
                chunk = todo[i:i + TREND_GROUP_MAX]
                try:
                    _throttle()
                    peaks = _trend_peaks(chunk)
                    for k in chunk:
                        cache["t|" + _norm(k)] = {"ts": time.time(), "v": {"peak_date": peaks.get(_norm(k))}}
                except Exception as e:  # noqa: BLE001 — 한 묶음 실패가 전체를 막지 않게
                    fails += len(chunk)
                    first_err = first_err or _short_err(e)
                    if _is_quota(e):
                        break
            try:
                _save_cache(cache)
            except OSError:
                pass
            if fails:
                notes.append(f"작년 정점 조회 실패 {fails}건 (해당 칸은 '-') — {first_err}")
        else:
            notes.append("네이버 검색 API 키가 없어 작년 정점을 비워 두었어요.")
    return {_norm(k): _combine(_norm(k), cache) for k in uniq}, notes


def _short_err(e: Exception) -> str:
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


def _is_quota(e: Exception) -> bool:
    return isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (401, 403, 429)


# ---------- 계산 도우미 (저장하지 않고 화면·점수에서 매번 계산) ----------

def peak_weeks_left(peak_date, today: date | None = None):
    """작년 정점 주가 올해 같은 주(+52주)까지 몇 주 남았는지. 지났으면 음수. 정점이 없으면 None."""
    if not peak_date:
        return None
    try:
        p = datetime.strptime(peak_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = today or date.today()
    this_monday = today - timedelta(days=today.weekday())
    return ((p + timedelta(weeks=52)) - this_monday).days // 7


def peak_label(peak_date) -> str:
    """'11/24주' 같은 짧은 표기. 없으면 '-'."""
    if not peak_date:
        return "-"
    try:
        p = datetime.strptime(peak_date[:10], "%Y-%m-%d")
    except ValueError:
        return "-"
    return f"{p.month}/{p.day}주"
