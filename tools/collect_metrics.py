# -*- coding: utf-8 -*-
"""키워드 지표 수집기 (네이버 검색광고 + 블로그 검색 + 데이터랩, 읽기 전용, 순수 JSON 출력)

씨앗 키워드마다 연관 키워드를 받아서 아래 지표를 모은다.
  - 월간 검색량 PC / 모바일 / 합계      ← 네이버 검색광고 API (keywordstool)
  - 블로그 문서 수                       ← 블로그 검색 API (허브 또는 개발자센터)
  - 포화도 = 블로그 문서 수 ÷ 월간 검색량 합계
  - 최근 12개월 추세 (월별, 가장 높은 달 = 100) ← 데이터랩 검색어트렌드 API (허브 또는 개발자센터)

키는 .env 에서 읽는다 (config.py). 키 값은 화면·파일 어디에도 출력하지 않는다.
  - 검색광고: NAVER_CUSTOMER_ID / NAVER_API_KEY / NAVER_SECRET_KEY
  - 블로그 검색·데이터랩: 아래 순서로 시도해서 '되는 키'를 자동으로 쓴다.
      1) NAVER_SEARCH_CLIENT_ID / NAVER_SEARCH_CLIENT_SECRET (+ NAVER_SEARCH_KEY_TYPE=hub|devcenter, 기본 hub)
      2) NAVER_HUB_CLIENT_ID / NAVER_HUB_CLIENT_SECRET   (네이버 API 허브)
      3) NAVER_LOGIN_CLIENT_ID / NAVER_LOGIN_CLIENT_SECRET (네이버 개발자센터 애플리케이션)
  - 쓸 수 있는 키가 없거나 그 API 가 꺼져 있으면 해당 칸만 null 로 두고 멈추지 않는다.

앱 장부(articles*.json, content_queue*.json)는 건드리지 않는다.

사용법
  python tools/collect_metrics.py --file seeds.txt --out metrics.json
  python tools/collect_metrics.py 패딩 세탁                       # 씨앗 1개
  python tools/collect_metrics.py --file seeds.txt --max-related 50
"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import config  # noqa: E402,F401  (.env → 환경변수)
import naver_api  # noqa: E402  (검색광고 서명 헤더 재사용)
import requests  # noqa: E402

HUB_BASE = "https://naverapihub.apigw.ntruss.com"
ENDPOINTS = {
    "blog": {"hub": HUB_BASE + "/search/v1/blog",
             "devcenter": "https://openapi.naver.com/v1/search/blog.json"},
    "trend": {"hub": HUB_BASE + "/search-trend/v1/search",
              "devcenter": "https://openapi.naver.com/v1/datalab/search"},
}
TREND_GROUP_MAX = 5      # 데이터랩 요청 1번에 키워드 그룹 최대 5개
CALL_GAP_SEC = 0.15      # API 호출 사이 최소 간격
LOW_VOLUME_VALUE = 5     # 검색광고가 '< 10' 으로 주는 값은 5로 본다 (앱의 naver_api 와 같은 규칙)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


_last = [0.0]


def throttle() -> None:
    wait = CALL_GAP_SEC - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()


# ---------- 키 고르기 ----------

def key_candidates() -> list:
    """[(종류, id, secret), ...] 시도할 순서대로. 비어 있는 키는 뺀다."""
    env = lambda k: os.environ.get(k, "").strip()
    out = []
    cid, sec = env("NAVER_SEARCH_CLIENT_ID"), env("NAVER_SEARCH_CLIENT_SECRET")
    if cid and sec:
        kt = env("NAVER_SEARCH_KEY_TYPE").lower() or "hub"
        out.append(("devcenter" if kt == "devcenter" else "hub", cid, sec))
    for kt, a, b in (("hub", "NAVER_HUB_CLIENT_ID", "NAVER_HUB_CLIENT_SECRET"),
                     ("devcenter", "NAVER_LOGIN_CLIENT_ID", "NAVER_LOGIN_CLIENT_SECRET")):
        if env(a) and env(b) and (kt, env(a), env(b)) not in out:
            out.append((kt, env(a), env(b)))
    return out


def headers_for(kind: str, cid: str, sec: str) -> dict:
    if kind == "hub":
        return {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec}
    return {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec}


class OpenApi:
    """블로그 검색·데이터랩을 '되는 키'로 호출한다. 처음 성공한 키를 기억해서 계속 쓴다."""

    def __init__(self):
        self.cands = key_candidates()
        self.chosen = {}   # api → (종류, id, secret)
        self.dead = {}     # api → 실패 이유 (더 이상 시도 안 함)
        self.errors = {}   # api → 일시 오류로 null 이 된 요청 수

    def call(self, api: str, **req):
        if api in self.dead:
            return None
        order = [self.chosen[api]] if api in self.chosen else self.cands
        reasons = []
        for kind, cid, sec in order:
            throttle()
            method = "POST" if api == "trend" else "GET"
            h = headers_for(kind, cid, sec)
            if method == "POST":
                h["Content-Type"] = "application/json"
            try:
                r = requests.request(method, ENDPOINTS[api][kind], headers=h, timeout=20, **req)
            except requests.RequestException as e:
                reasons.append(f"{kind}: 연결 실패({type(e).__name__})")
                continue
            if r.status_code == 429:  # 잠깐 너무 많이 부름 → 한 번만 쉬고 다시
                time.sleep(1.0)
                try:
                    r = requests.request(method, ENDPOINTS[api][kind], headers=h, timeout=20, **req)
                except requests.RequestException as e:
                    self.errors[api] = self.errors.get(api, 0) + 1
                    log(f"[경고] {api} 다시 시도 중 연결 실패 ({kind}: {type(e).__name__})")
                    return None
            if r.ok:
                self.chosen[api] = (kind, cid, sec)
                return r.json()
            if r.status_code in (401, 403, 404):  # 이 키로는 안 되는 API → 다음 키
                reasons.append(f"{kind}: {r.status_code} {short_error(r)}")
                continue
            # 그 밖의 오류(서버 오류 등)는 이번 요청만 실패로 본다
            self.errors[api] = self.errors.get(api, 0) + 1
            log(f"[경고] {api} 호출 실패 ({kind}: {r.status_code} {short_error(r)})")
            return None
        if api in self.chosen:  # 잘 되던 키가 갑자기 막힘
            self.chosen.pop(api)
        self.dead[api] = " / ".join(reasons) or "쓸 수 있는 키가 .env 에 없음"
        log(f"[안내] {'블로그 검색' if api == 'blog' else '데이터랩'} API 를 쓸 수 없어 해당 칸은 null 로 둡니다: {self.dead[api]}")
        return None

    def status(self, api: str) -> str:
        extra = f", 일시 오류로 비운 요청 {self.errors[api]}건" if self.errors.get(api) else ""
        if api in self.chosen:
            return "ok (" + self.chosen[api][0] + ")" + extra
        if api in self.dead:
            return "unavailable: " + self.dead[api]
        return "not called" + extra


def short_error(r) -> str:
    try:
        j = r.json()
        msg = j.get("errorMessage") or (j.get("error") or {}).get("message") or ""
    except ValueError:
        msg = r.text[:100]
    return str(msg)[:120]


# ---------- 검색광고: 연관 키워드 + 월간 검색량 ----------

def to_int(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").strip()
    return int(s) if s.isdigit() else LOW_VOLUME_VALUE


def to_float(v):
    """클릭수: 숫자는 소수 첫째 자리까지, '< 10' 같은 값은 LOW_VOLUME_VALUE, 값이 없으면 None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 1)
    s = str(v).replace(",", "").strip()
    try:
        return round(float(s), 1)
    except ValueError:
        return float(LOW_VOLUME_VALUE) if s else None


def related_keywords(seed: str) -> list:
    uri = "/keywordstool"
    params = {"hintKeywords": seed.replace(" ", ""), "showDetail": "1"}
    for attempt in range(2):
        throttle()
        r = requests.get(config.NAVER_API_BASE_URL + uri, params=params,
                         headers=naver_api._headers("GET", uri), timeout=20)
        if r.status_code == 429 and attempt == 0:
            time.sleep(1.5)
            continue
        if not r.ok:
            raise RuntimeError(f"검색광고 API 오류 {r.status_code}: {short_error(r)}")
        break
    rows = []
    for item in r.json().get("keywordList", []):
        pc, mo = to_int(item.get("monthlyPcQcCnt")), to_int(item.get("monthlyMobileQcCnt"))
        rows.append({"keyword": item.get("relKeyword", ""), "monthly_pc": pc,
                     "monthly_mobile": mo, "monthly_total": pc + mo,
                     # 황금 키워드 측정용 (판정에는 아직 안 씀): 월평균 클릭수와 경쟁정도
                     "ave_pc_clk": to_float(item.get("monthlyAvePcClkCnt")),
                     "ave_mobile_clk": to_float(item.get("monthlyAveMobileClkCnt")),
                     "comp_idx": item.get("compIdx")})
    return rows


# ---------- 블로그 문서 수 ----------

def blog_docs(api: OpenApi, keyword: str):
    j = api.call("blog", params={"query": keyword, "display": 1})
    if not j or "total" not in j:
        return None
    return int(j["total"])


# ---------- 12개월 추세 ----------

def last_12_months(today: date) -> list:
    y, m = today.year, today.month
    months = []
    for _ in range(12):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append(f"{y:04d}-{m:02d}")
    return months[::-1]  # 오래된 달 → 최근 달 (이번 달은 아직 안 끝나서 제외)


def trends(api: OpenApi, keywords: list, months: list) -> dict:
    """{키워드: [{"month", "ratio"}, ...] 또는 None}. 각 키워드마다 가장 높은 달을 100 으로 맞춘다."""
    y2, m2 = map(int, months[-1].split("-"))
    end_day = (date(y2 + (m2 == 12), m2 % 12 + 1, 1) - date.resolution).isoformat()
    out = {}
    for i in range(0, len(keywords), TREND_GROUP_MAX):
        chunk = keywords[i:i + TREND_GROUP_MAX]
        body = {"startDate": months[0] + "-01", "endDate": end_day, "timeUnit": "month",
                "keywordGroups": [{"groupName": f"g{n}", "keywords": [k]} for n, k in enumerate(chunk)]}
        j = api.call("trend", json=body)
        if j is None:
            for k in chunk:
                out[k] = None
            continue
        by_group = {r.get("title"): r.get("data", []) for r in j.get("results", [])}
        for n, k in enumerate(chunk):
            vals = {d.get("period", "")[:7]: float(d.get("ratio") or 0) for d in by_group.get(f"g{n}", [])}
            series = [vals.get(mo, 0.0) for mo in months]
            top = max(series) if series else 0
            out[k] = [{"month": mo, "ratio": round(v / top * 100, 1) if top else 0.0}
                      for mo, v in zip(months, series)]
    return out


# ---------- 전체 흐름 ----------

def read_seed_file(path: Path) -> list:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    seeds, seen = [], set()
    for line in text.splitlines():
        kw = " ".join(line.split())
        if kw and not kw.startswith("#") and kw not in seen:
            seen.add(kw)
            seeds.append(kw)
    return seeds


def collect_seed(api: OpenApi, seed: str, max_related: int, months: list) -> dict:
    rows = related_keywords(seed)
    seed_norm = seed.replace(" ", "")
    seed_row = next((r for r in rows if r["keyword"].replace(" ", "") == seed_norm), None)
    others = sorted((r for r in rows if r is not seed_row), key=lambda r: r["monthly_total"], reverse=True)
    picked = ([seed_row] if seed_row else []) + others[:max_related]
    log(f"  연관 키워드 {len(rows)}개 중 {len(picked)}개 조회 (씨앗 포함, 검색량 많은 순)")

    for n, r in enumerate(picked, 1):
        r["blog_docs"] = blog_docs(api, r["keyword"])
        total = r["monthly_total"]
        r["saturation"] = round(r["blog_docs"] / total, 2) if r["blog_docs"] is not None and total > 0 else None
        if n % 10 == 0 and "blog" not in api.dead:
            log(f"  문서 수 {n}/{len(picked)}")
    tr = trends(api, [r["keyword"] for r in picked], months)
    for r in picked:
        r["trend_12m"] = tr.get(r["keyword"])
    return {"seed": seed, "related_total": len(rows), "keywords": picked}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="네이버 키워드 지표 수집 (순수 JSON 출력)")
    ap.add_argument("seed", nargs="*", help="씨앗 키워드 1개 (띄어쓰기 그대로 써도 됨)")
    ap.add_argument("--file", help="씨앗 키워드 목록 파일 (한 줄에 하나)")
    ap.add_argument("--out", help="결과를 이 JSON 파일로도 저장")
    ap.add_argument("--max-related", type=int, default=30,
                    help="씨앗마다 조회할 연관 키워드 수 (검색량 많은 순, 기본 30)")
    args = ap.parse_args()

    single = " ".join(args.seed).strip()
    if args.file and single:
        log("씨앗 키워드와 --file 은 함께 쓸 수 없습니다.")
        return 2
    if args.file:
        p = Path(args.file)
        if not p.is_file():
            log(f"[실패] 목록 파일을 찾을 수 없습니다: {p}")
            return 2
        seeds = read_seed_file(p)
    else:
        seeds = [single] if single else []
    if not seeds:
        ap.print_usage(sys.stderr)
        log("씨앗 키워드 또는 --file 을 주세요.")
        return 2
    if config.USE_MOCK:
        log("[실패] 검색광고 API 키(NAVER_CUSTOMER_ID / NAVER_API_KEY / NAVER_SECRET_KEY)가 .env 에 없습니다: "
            + ", ".join(config.MISSING_NAVER_VARS))
        return 2
    if args.max_related < 0:
        log("--max-related 는 0 이상이어야 합니다.")
        return 2

    api = OpenApi()
    months = last_12_months(date.today())
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    results, failed, stopped = [], 0, False
    try:
        for i, seed in enumerate(seeds, 1):
            log(f"[{i}/{len(seeds)}] 씨앗: {seed}")
            try:
                results.append(collect_seed(api, seed, args.max_related, months))
            except Exception as e:
                failed += 1
                log(f"[{i}/{len(seeds)}] 실패: {e}")
                results.append({"seed": seed, "error": str(e)})
    except KeyboardInterrupt:
        log(f"[중단] Ctrl+C 로 멈췄습니다. 지금까지 모은 {len(results)}개만 내보냅니다.")
        stopped = True

    output = {
        "collected_at": started,
        "trend_months": months,
        "api_status": {"search_ad": "ok" if any("keywords" in r for r in results) else "failed",
                       "blog_search": api.status("blog"), "datalab": api.status("trend")},
        "seeds": results,
    }
    text = json.dumps(output, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text + "\n", encoding="utf-8")
            log(f"[저장됨] {out}")
        except OSError as e:
            log(f"[실패] 파일로 저장하지 못했습니다: {e}")
            return 1
    log(f"[완료] 씨앗 성공 {len(results) - failed}개 / 실패 {failed}개")
    if stopped:
        return 130
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
