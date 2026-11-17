"""제품 후보 수집·추천 도구 (읽기 전용).

쇼핑커넥트(네이버 브랜드커넥트)에서 검색어마다 상위 30개 상품을 모아 추천 3개를 고릅니다.
W싼세계(wp_tips)는 추천 3개를 쿠팡파트너스 상품검색에서 같은 제품인지 확인합니다.

- 에고라이트(ego-browser)로 '보기만' 합니다. 링크 발급/생성 버튼은 누르지 않고, coupang.com 본 사이트는 열지 않습니다.
- 로그인이 풀려 있으면 멈추고 알립니다(아이디·비밀번호 입력 없음). 페이지 사이 3~5초 쉽니다.
- content_queue*.json 은 읽기만 합니다(--from-queue). 쓰지 않습니다.

사용:
  python tools/product_candidates.py --channel naver_tips --keywords "모니터 받침대,제습기"
  python tools/product_candidates.py --channel wp_tips --from-queue

결과: pipeline/candidates_run/<날짜>_<채널>/candidates.json + img/ + summary.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # 윈도우 콘솔에서 한글이 깨지지 않게
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
RUN_ROOT = ROOT / "pipeline" / "candidates_run"
CHANNELS = ("naver_tips", "wp_tips", "radar_naver")
CHANNEL_LABEL = {"naver_tips": "N살림노트", "wp_tips": "W싼세계", "radar_naver": "N데스크피커"}
BRANDCONNECT = "https://brandconnect.naver.com/997658497773120/affiliate/products/search"
PARTNERS = "https://partners.coupang.com/#affiliate/ws/link/0/"
TOP_N = 30          # 검색어마다 모으는 개수(넘치면 멈춤)
RATING_MIN = 4.5
RATING_RELAXED = 4.3
PICK = 3
BATCH_KW = 4        # 쇼핑커넥트: 한 번에 읽는 검색어 수(제한 시간을 넘지 않게 나눠서 실행)
BATCH_CP = 10       # 쿠팡 확인: 한 번에 확인하는 상품 수
NO_PRICE = "가격 없음(표시 안 됨)"
ALT_PICK = 3          # 대안 후보 개수
ALT_RATING_MIN = 4.5  # 대안 평점 기준(느슨하게 하지 않음)
ALT_PRICE_LOW, ALT_PRICE_HIGH = 0.5, 2.0   # 본상품 가격의 0.5~2배(경곗값 포함)
BATCH_CAT = 5         # 카테고리 확인: 한 번에 읽는 상품 수
BRAND_ALIAS_JSON = ROOT / "tools" / "brand_alias.json"


# ───────────────────────── 브라우저 실행 ─────────────────────────

FILTER_JSON = ROOT / "tools" / "filter.json"
DEFAULT_TIMEOUT_MIN = 30


def _timeout_sec() -> float | None:
    """에고라이트 한 번 실행의 제한 시간(초). tools/filter.json 의 candidates_timeout_min(분).
    없거나 이상한 값이면 30분, 0 이면 제한 없음(None)."""
    try:
        v = json.loads(FILTER_JSON.read_text(encoding="utf-8-sig")).get("candidates_timeout_min", DEFAULT_TIMEOUT_MIN)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            v = DEFAULT_TIMEOUT_MIN
    except Exception:  # noqa: BLE001
        v = DEFAULT_TIMEOUT_MIN
    return None if v == 0 else v * 60

def _run_ego(script: str, tag: str) -> str:
    """에고라이트로 .mjs 한 개를 실행하고 출력을 돌려줍니다."""
    d = Path(tempfile.gettempdir()) / "product_candidates_ego"
    d.mkdir(exist_ok=True)
    f = d / f"{tag}_{datetime.now():%H%M%S}.mjs"
    f.write_text(script, encoding="utf-8")
    url = "file:///" + str(f).replace("\\", "/")
    # shell=True: 윈도우에서 ego-browser(.cmd 등)를 찾기 위해
    p = subprocess.run(f'ego-browser nodejs -e "await import(\'{url}\')"', shell=True, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=_timeout_sec())
    return (p.stdout or "") + (p.stderr or "")


def _space_prefix(name: str) -> str:
    """작업 공간을 이름으로 만듭니다(같은 이름이 있으면 재사용하지 않고 새로 만든 번호를 씀)."""
    return f'const task = await taskSpace("{name}");\nconst page = (await task.pages())[0] ?? task.page("p1");\n'


def _js_naver(keywords: list[str], out_json: Path, n: int) -> str:
    return f"""
const fs = await import("node:fs/promises");
const sleep = (a, b) => new Promise(r => setTimeout(r, a + Math.random() * (b - a)));
{_space_prefix("product-candidates-run")}
const KW = {json.dumps(keywords, ensure_ascii=False)};
const res = [];
let lost = false;
for (const kw of KW) {{
  await page.goto("{BRANDCONNECT}?query=" + encodeURIComponent(kw) + "&tab=product", {{timeout: 60000}});
  await page.waitForTimeout(4000);
  if ((await page.url()).includes("nidlogin") || (await page.url()).includes("login")) {{ lost = true; break; }}
  let items = [];
  for (let k = 0; k < 6; k++) {{
    items = await page.evaluate(() => {{
      const out = [];
      for (const l of document.querySelectorAll("li")) {{
        const a = l.querySelector("a[href*='/affiliate/products/']"); if (!a) continue;
        const t = l.innerText || ""; if (!t.includes("수수료")) continue;
        const img = l.querySelector("img");
        out.push({{ t, h: a.href, img: img ? img.src : null }});
      }}
      return out;
    }});
    if (items.length >= {n}) break;
    await page.mouse.move(600, 500); await page.mouse.wheel(0, 2500); await page.waitForTimeout(2500);
  }}
  // 이미지는 화면에 보여야 주소가 채워지므로, 모은 상품을 위에서부터 하나씩 화면에 보이게 한 뒤 다시 읽는다
  items = await page.evaluate(async () => {{
    const lis = [...document.querySelectorAll("li")].filter(l => l.querySelector("a[href*='/affiliate/products/']") && (l.innerText || "").includes("수수료")).slice(0, {n});
    for (const l of lis) {{ l.scrollIntoView({{ block: "center" }}); await new Promise(r => setTimeout(r, 300)); }}
    return lis.map(l => {{ const a = l.querySelector("a[href*='/affiliate/products/']"); const img = l.querySelector("img"); return {{ t: l.innerText || "", h: a.href, img: img ? img.src : null }}; }});
  }});
  const total = await page.evaluate(() => {{ const m = document.body.innerText.match(/총\\s*([\\d,]+)\\s*개/); return m ? m[1] : null; }});
  res.push({{ kw, total, items: items.slice(0, {n}) }});
  await fs.writeFile({json.dumps(str(out_json).replace(chr(92), "/"))}, JSON.stringify({{ lost, res }}), "utf8");
  console.log("NAVER", kw, "총", total, "읽음", items.length);
  await sleep(3000, 5000);
}}
await fs.writeFile({json.dumps(str(out_json).replace(chr(92), "/"))}, JSON.stringify({{ lost, res }}), "utf8");
console.log(lost ? "LOGIN_LOST" : "DONE", "space", task.spaceId);
"""


def _js_coupang(queries: list[dict], out_json: Path) -> str:
    return f"""
const fs = await import("node:fs/promises");
const sleep = (a, b) => new Promise(r => setTimeout(r, a + Math.random() * (b - a)));
{_space_prefix("product-candidates-run")}
const Q = {json.dumps(queries, ensure_ascii=False)};
const res = [];
let lost = false;
for (const q of Q) {{
  await page.goto("{PARTNERS}" + encodeURIComponent(q.query), {{timeout: 60000}});
  await page.waitForTimeout(5000);
  const u = await page.url();
  if (!u.includes("partners.coupang.com") || u.includes("login")) {{ lost = true; break; }}
  const r = await page.evaluate(() => {{
    const head = (document.body.innerText.match(/\\d+\\s*개의 검색결과/) || [null])[0];
    const items = [...document.querySelectorAll(".product-search-list .product-item")].slice(0, 5).map(el => {{
      const img = el.querySelector("img");
      return {{ t: el.innerText, img: img ? img.src : null }};
    }});
    return {{ head, items }};
  }});
  res.push({{ id: q.id, query: q.query, head: r.head, items: r.items }});
  await fs.writeFile({json.dumps(str(out_json).replace(chr(92), "/"))}, JSON.stringify({{ lost, res }}), "utf8");
  console.log("COUPANG", q.query, r.head, r.items.length);
  await sleep(3000, 5000);
}}
await fs.writeFile({json.dumps(str(out_json).replace(chr(92), "/"))}, JSON.stringify({{ lost, res }}), "utf8");
console.log(lost ? "LOGIN_LOST" : "DONE");
"""


def _js_category(products: list[dict], out_json: Path) -> str:
    """상품마다 브랜드커넥트 상품 페이지 → '네이버 쇼핑에서 보기' → 스토어 상품 페이지에서 카테고리 글자만 읽습니다(읽기만)."""
    return f"""
const fs = await import("node:fs/promises");
const sleep = (a, b) => new Promise(r => setTimeout(r, a + Math.random() * (b - a)));
{_space_prefix("product-candidates-run")}
const P = {json.dumps(products, ensure_ascii=False)};
const res = [];
let lost = false;
for (const p of P) {{
  const rec = {{ id: p.id, category: "", error: "" }};
  await page.goto(p.url, {{timeout: 60000}});
  await page.waitForTimeout(3000);
  if ((await page.url()).includes("login")) {{ lost = true; break; }}
  const store = await page.evaluate(() => {{
    const a = [...document.querySelectorAll("a")].find(e => e.innerText.trim().startsWith("네이버 쇼핑에서 보기"));
    return a ? a.href : "";
  }});
  if (!store) {{ rec.error = "스토어 주소 없음"; }}
  else {{
    await sleep(3000, 5000);
    await page.goto(store, {{timeout: 60000}});
    await page.waitForTimeout(4000);
    const u = await page.url();
    if (/nidlogin|captcha|login/.test(u)) {{ rec.error = "LOGIN OR CAPTCHA: " + u; lost = true; }}
    else {{
      rec.category = await page.evaluate(() => {{
        for (const s of document.querySelectorAll("script")) {{
          const m = (s.textContent || "").match(/"wholeCategoryName":"([^"]+)"/);
          if (m) return m[1].replace(/\\\\u002F/g, "/").replace(/\\\\u003E/g, ">");
        }}
        return "";
      }});
      if (!rec.category) rec.error = "카테고리 못 읽음";
    }}
  }}
  res.push(rec);
  await fs.writeFile({json.dumps(str(out_json).replace(chr(92), "/"))}, JSON.stringify({{ lost, res }}), "utf8");
  console.log("CATEGORY", p.id, rec.category || rec.error);
  if (lost) break;
  await sleep(3000, 5000);
}}
await fs.writeFile({json.dumps(str(out_json).replace(chr(92), "/"))}, JSON.stringify({{ lost, res }}), "utf8");
console.log(lost ? "LOGIN_LOST" : "DONE");
"""


def _finish_space() -> None:
    """작업이 끝나면 에고라이트 작업 공간을 닫습니다."""
    _run_ego('const task = await taskSpace("product-candidates-run"); await task.finish({ keep: [] }); console.log("FINISHED");', "finish")


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / (hashlib.md5(key.encode("utf-8")).hexdigest()[:12] + ".json")


def collect_in_batches(todo_all: list, key_of, cache_dir: Path, batch: int, make_js, tag: str, label: str) -> str:
    """항목을 batch 개씩 나눠 따로 실행하고, 항목마다 cache_dir 에 저장합니다.
    이미 저장된 항목은 다시 받지 않으므로, 중간에 끊겨도 같은 명령으로 이어 갑니다.
    결과: 'ok'(모두 받음) / 'lost'(로그인 풀림) / 'incomplete'(시간 초과 등으로 덜 받음)"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    todo = [it for it in todo_all if not _cache_path(cache_dir, key_of(it)).exists()]
    print(f"{label}: 전체 {len(todo_all)}개 중 이미 받은 {len(todo_all) - len(todo)}개는 건너뛰고 {len(todo)}개를 받습니다.")
    status = "ok"
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        tmp = cache_dir / "_batch_tmp.json"
        tmp.unlink(missing_ok=True)
        print(f"  묶음 {i // batch + 1}/{(len(todo) + batch - 1) // batch} ({len(part)}개)")
        timed_out = False
        try:
            print(_run_ego(make_js(part, tmp), tag).strip())
        except subprocess.TimeoutExpired:
            timed_out = True
            print("  제한 시간 초과 — 받은 것까지만 저장하고 멈춥니다. 같은 명령으로 이어 가기(받은 것은 건너뜁니다).")
        lost = False
        if tmp.exists():
            d = json.loads(tmp.read_text(encoding="utf-8"))
            lost = bool(d.get("lost"))
            for r in d.get("res", []):
                k = r["kw"] if "kw" in r else r["id"]
                _cache_path(cache_dir, k).write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
            tmp.unlink(missing_ok=True)
        if lost:
            return "lost"
        if timed_out or any(not _cache_path(cache_dir, key_of(it)).exists() for it in part):
            status = "incomplete"
            break
    return status


# ───────────────────────── 읽어온 글자 정리 ─────────────────────────

def _num(s: str | None) -> int | None:
    m = re.sub(r"[^\d]", "", s or "")
    return int(m) if m else None


def parse_naver_item(raw: dict, rank: int, keyword: str = "") -> dict:
    """목록 한 칸의 글자(줄 단위)에서 상품 정보를 꺼냅니다."""
    lines = [x.strip() for x in raw["t"].split("\n") if x.strip()]
    site_issued = "링크 복사" in lines  # 목록 버튼이 '링크 복사'면 이미 발급된 상품(발급 버튼을 안 눌러도 알 수 있음)
    lines = [x for x in lines if x not in ("링크 발급", "링크 복사")]
    commission = None
    rating = None
    reviews = None
    price = list_price = None
    for i, x in enumerate(lines):
        if x.startswith("수수료"):
            commission = _num(x)
        elif x == "판매가" and i + 1 < len(lines):
            list_price = _num(lines[i + 1])
        elif x == "할인가" and i + 1 < len(lines):
            price = _num(lines[i + 1])
        elif re.fullmatch(r"\d\.\d+|\d", x) and rating is None:
            rating = float(x)
        elif x.startswith("리뷰"):
            reviews = _num(x)
    if price is None:
        price = list_price
    # 이름·스토어: 평점 줄 바로 앞 두 줄(평점이 없으면 맨 끝 두 줄)
    end = len(lines)
    for i, x in enumerate(lines):
        if re.fullmatch(r"\d\.\d+|\d", x) or x.startswith("리뷰"):
            end = i
            break
    body = lines[:end]
    store = body[-1] if len(body) >= 2 else ""
    name = body[-2] if len(body) >= 2 else (body[-1] if body else "")
    img = raw.get("img") or ""
    return {
        "rank": rank, "name": name, "store": store, "brand": brand_or_unknown(name, store, keyword),
        "price": price, "list_price": list_price, "commission": commission,
        "reviews": reviews, "rating": rating,
        "image_url": img, "url": raw["h"], "site_issued": site_issued,
    }


# 브랜드 추정에서 건너뛰는 홍보 말
_BRAND_SKIP = {"신형", "신상", "신상품", "신제품", "최신형", "최신", "무료배송", "당일발송", "당일배송", "정품", "공식", "국내정품",
               "특가", "할인", "인기", "베스트", "대용량", "초특가", "빠른배송", "무료", "배송", "국내발송", "새상품"}
_YEAR_RE = re.compile(r"^\d{1,4}(년형|년|형|ver|v\d*|세대)?$", re.I)


# 브랜드가 아니라 제품 종류를 가리키는 흔한 말(추정한 브랜드가 이 중 하나면 '브랜드 미상')
GENERIC_NOUNS = {"팜레스트", "받침대", "거치대", "스탠드", "패드", "쿠션", "매트", "정리함"}
UNKNOWN_BRAND = "브랜드 미상"


def brand_of(name: str, store: str) -> str:
    """화면에 브랜드 칸이 없어서 상품명에서 추정합니다.
    [ ]·( )·【 】 묶음, 연도·숫자, '신형/무료배송/당일발송/정품' 같은 말을 건너뛰고 첫 단어를 씁니다."""
    s = re.sub(r"[\[\(【][^\]\)】]*[\]\)】]", " ", name or "")
    for tok in s.split():
        t = tok.strip(",.·/|-_")
        if not t or t in _BRAND_SKIP or _YEAR_RE.match(t):
            continue
        return t
    return store or ""


def brand_or_unknown(name: str, store: str, keyword: str = "") -> str:
    """추정한 브랜드가 검색어에 들어 있는 단어이거나 일반 명사 목록에 있으면 '브랜드 미상'."""
    b = brand_of(name, store)
    nb = re.sub(r"\s", "", b)
    nk = re.sub(r"\s", "", keyword or "")
    if (not b or b in GENERIC_NOUNS or b in (keyword or "").split()
            or (len(nb) >= 2 and nb in nk)):
        return UNKNOWN_BRAND
    return b


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_·]", "", (s or "")).lower()


# ───────────────────────── 대안 후보(alt_candidates) ─────────────────────────

_ALIAS_CACHE: dict | None = None


def _load_alias() -> dict:
    """tools/brand_alias.json 을 읽어 {정규화한 이름: 대표 이름} 표로 만듭니다(없거나 깨져 있으면 빈 표)."""
    global _ALIAS_CACHE
    if _ALIAS_CACHE is None:
        table: dict[str, str] = {}
        seller_words: list[str] = []
        seller_brands: set[str] = set()
        try:
            d = json.loads(BRAND_ALIAS_JSON.read_text(encoding="utf-8-sig"))
            for g in d.get("groups", []):
                names = [x for x in g if isinstance(x, str) and x.strip()]
                for n in names:
                    table[_norm(n)] = _norm(names[0])
            seller_words = [_norm(x) for x in d.get("판매처_말", []) if isinstance(x, str)]
            seller_brands = {_norm(x) for x in d.get("판매처_브랜드", []) if isinstance(x, str)}
        except Exception:  # noqa: BLE001
            pass
        _ALIAS_CACHE = {"table": table, "seller_words": sorted(seller_words, key=len, reverse=True),
                        "seller_brands": seller_brands}
    return _ALIAS_CACHE


def canon_brand(name: str, store: str = "", keyword: str = "") -> str:
    """대안 비교용 브랜드. 상품명에서 묶음 표(brand_alias.json)의 이름을 찾아 대표 이름으로 돌려줍니다.
    대소문자·띄어쓰기는 무시, 한글·영문 표기는 같은 브랜드. 표에 없으면 기존 추정 브랜드를 쓰고,
    그것도 못 정하면 빈 글자(브랜드 확인 못 함)."""
    a = _load_alias()
    nn = _norm(name)
    for w in a["seller_words"]:
        nn = nn.replace(w, "")
    found: list[tuple[int, str]] = []
    for alias, canon in a["table"].items():
        if len(alias) < 2:
            continue
        i = nn.find(alias)
        if i >= 0:
            found.append((i, canon))
    groups = {c for _, c in found}
    if len(groups) > 1:
        groups -= a["seller_brands"]
        found = [(i, c) for i, c in found if c in groups]
    if found:
        return min(found)[1]
    b = brand_or_unknown(name, store, keyword)
    return "" if b == UNKNOWN_BRAND else _norm(b)


def lowest_category(whole: str) -> str:
    """'디지털/가전>음향가전>스피커>블루투스스피커' → '블루투스스피커'(가장 아래 단계)."""
    parts = [x.strip() for x in re.split(r"[>＞]", whole or "") if x.strip()]
    return parts[-1] if parts else ""


def alt_prefilter(main: dict, items: list[dict], keyword: str = "") -> tuple[list[dict], dict]:
    """조건 ②③④(브랜드 다름·평점 4.5↑·가격 0.5~2배)를 만족하는 것을 리뷰 많은 순으로 돌려줍니다.
    ① 카테고리는 상품 페이지를 열어야 알 수 있어서 여기서는 보지 않습니다."""
    info = {"main_brand": canon_brand(main["name"], main.get("store", ""), keyword), "brand_unknown": 0,
            "same_brand": 0, "low_rating": 0, "price_out": 0}
    mp = main.get("price")
    pool: list[dict] = []
    for x in items:
        if x["url"] == main["url"]:
            continue
        b = canon_brand(x["name"], x.get("store", ""), keyword)
        if not b:
            info["brand_unknown"] += 1
            continue
        if info["main_brand"] and b == info["main_brand"]:
            info["same_brand"] += 1
            continue
        if x["rating"] is None or x["rating"] < ALT_RATING_MIN:
            info["low_rating"] += 1
            continue
        p = x.get("price")
        if not mp or not p or not (mp * ALT_PRICE_LOW <= p <= mp * ALT_PRICE_HIGH):
            info["price_out"] += 1
            continue
        y = dict(x)
        y["alt_brand"] = b
        pool.append(y)
    pool.sort(key=lambda x: (x["reviews"] or 0), reverse=True)
    return pool, info


def pick_alt(main_cat: str, pool: list[dict], cat_of) -> list[dict]:
    """pool(리뷰 많은 순)에서 ① 가장 아래 카테고리가 본상품과 같은 것만, 같은 브랜드는 1개만, 최대 3개.
    cat_of(x) 는 그 상품의 전체 카테고리 글자를 돌려줍니다(못 읽으면 ''). 3개가 차면 더 읽지 않습니다."""
    out, brands = [], set()
    low = lowest_category(main_cat)
    if not low:
        return out
    for x in pool:
        if x["alt_brand"] in brands:
            continue
        whole = cat_of(x)
        if lowest_category(whole) != low:
            continue
        y = dict(x)
        y["category"] = whole
        y["reason"] = (f"대안 {len(out) + 1}: 카테고리 {low} 같음 · 브랜드 {x['alt_brand']}(본상품과 다름) · "
                       f"평점 {x['rating']} · 리뷰 {(x['reviews'] or 0):,}개 · 가격 {x['price']:,}원")
        out.append(y)
        brands.add(x["alt_brand"])
        if len(out) == ALT_PICK:
            break
    return out


def recommend(items: list[dict]) -> tuple[list[dict], bool]:
    """평점 4.5↑ → 리뷰 많은 순 → 같은 브랜드(또는 같은 스토어) 1개만 → 상위 3개. 모자라면 4.3까지 낮춤."""
    def pick(th: float) -> list[dict]:
        pool = [x for x in items if x["rating"] is not None and x["rating"] >= th]
        pool.sort(key=lambda x: (x["reviews"] or 0), reverse=True)
        out, brands, stores = [], set(), set()
        for x in pool:
            b, s = _norm(x["brand"]), _norm(x["store"])
            if x["brand"] == UNKNOWN_BRAND:  # 미상끼리는 서로 같은 브랜드로 보지 않음(스토어만 봄)
                b = None
            if (b is not None and b in brands) or (s and s in stores):
                continue
            out.append(x)
            if b is not None:
                brands.add(b)
            stores.add(s)
            if len(out) == PICK:
                break
        return out

    rec = pick(RATING_MIN)
    relaxed = False
    if len(rec) < PICK:
        rec2 = pick(RATING_RELAXED)
        if len(rec2) > len(rec):
            rec, relaxed = rec2, True
    result = []
    for k, x in enumerate(rec, 1):
        y = dict(x)
        low = relaxed and (x["rating"] or 0) < RATING_MIN
        y["reason"] = (f"{k}순위: 평점 {x['rating']} · 리뷰 {(x['reviews'] or 0):,}개(네이버 기준) · 브랜드 {x['brand']}"
                       + (" · 기준 낮춤(평점 4.3 이상)" if low else ""))
        result.append(y)
    return result, relaxed


# ───────────────────────── 쿠팡 같은 제품 확인 ─────────────────────────

_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}[A-Za-z0-9]")


def model_of(name: str) -> str | None:
    """상품명에서 모델명(글자+숫자가 섞인 5자 이상 토큰)을 찾습니다."""
    for m in _MODEL_RE.findall(name or ""):
        if re.search(r"[A-Za-z]", m) and re.search(r"\d", m) and not re.fullmatch(r"\d+[A-Za-z]{1,2}", m):
            return m
    return None


def coupang_query(item: dict) -> str:
    return model_of(item["name"]) or " ".join(re.sub(r"[\[\]]", " ", item["name"]).split()[:5])


def judge_coupang(item: dict, cands: list[dict]) -> dict:
    """같음(모델명 일치) / 비슷함(이름 단어가 많이 겹침) / 없음."""
    model = model_of(item["name"])
    nname = _norm(item["name"])
    toks = [t for t in re.split(r"\s+", re.sub(r"[\[\]\(\),]", " ", item["name"])) if len(t) >= 2]
    best = None
    for c in cands:
        title = c["title"]
        nt = _norm(title)
        if model and _norm(model) in nt:
            return {"verdict": "같음", "title": title, "price": c["price"], "image": c["img"],
                    "why": f"모델명 {model} 일치"}
        hit = sum(1 for t in toks if _norm(t) in nt)
        ratio = hit / max(1, len(toks))
        if best is None or ratio > best[0]:
            best = (ratio, c)
    if best and best[0] >= 0.5:
        c = best[1]
        return {"verdict": "비슷함", "title": c["title"], "price": c["price"], "image": c["img"],
                "why": f"이름 단어 {int(best[0] * 100)}% 겹침(모델명으로 확정 못 함)"}
    return {"verdict": "없음", "title": "", "price": None, "image": "", "why": "상위 5개에 같은 제품 없음"}


def parse_coupang(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        lines = [x.strip() for x in it["t"].split("\n") if x.strip()]
        if not lines:
            continue
        title = lines[0]
        prices = [_num(x) for x in lines[1:] if re.search(r"\d[\d,]*원", x)]
        out.append({"title": title, "price": prices[-1] if prices else None, "img": it.get("img") or ""})
    return out


# ───────────────────────── 이미지 ─────────────────────────

def download_image(url: str, dest: Path) -> dict | None:
    """대표 이미지 내려받기(네이버 500px 주소 그대로). 주소를 바꿔 키우지 않습니다."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        from PIL import Image
        import io
        w, h = Image.open(io.BytesIO(data)).size
        dest.write_bytes(data)
        return {"file": f"img/{dest.name}", "width": w, "height": h, "bytes": len(data)}
    except Exception as e:  # noqa: BLE001
        return {"file": "", "error": str(e)[:120]}


# ───────────────────────── 대기열(읽기만) ─────────────────────────

# 이름은 push_plan.py 가 plan 칸에 쓰는 방식(영문 키: search_intent·product_need 의 need/count/condition)에 맞춰 search_query 로 정함. 뒤는 예비 이름.
_QUERY_KEYS = ("search_query", "검색어", "query")


def _plan_query(plan: dict) -> str:
    """plan 칸(또는 plan.product_need)에 '검색어'가 있으면 그것을 돌려줍니다. 조건 문장은 검색어로 쓰지 않습니다."""
    pn = plan.get("product_need")
    for src in ((pn if isinstance(pn, dict) else {}), plan):
        for k in _QUERY_KEYS:
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                return " ".join(v.split())
    return ""


def keywords_from_queue(channel: str) -> tuple[list[str], list[dict]]:
    """대기열을 읽기만 합니다. (검색어 목록, 검색어가 없어 건너뛴 항목 목록)"""
    p = ROOT / f"content_queue_{channel}.json"
    if not p.exists():
        return [], []
    q = json.loads(p.read_text(encoding="utf-8"))
    out, skipped = [], []
    for it in q:
        if it.get("published"):
            continue
        plan = it.get("plan") or {}
        pn = plan.get("product_need")
        if not (isinstance(pn, dict) and pn.get("need")):
            continue
        qy = _plan_query(plan)
        if qy:
            if qy not in out:
                out.append(qy)
        else:
            skipped.append({"id": it.get("id", ""), "keyword": it.get("keyword", ""),
                            "condition": " ".join(str(pn.get("condition") or "").split())})
    return out, skipped


# ───────────────────────── 대안 후보 계산(카테고리는 필요한 만큼만 읽음) ─────────────────────────

def _pid(x: dict) -> str:
    return x["url"].rstrip("/").rsplit("/", 1)[-1]


def build_alt(keyword: str, items: list[dict], rec: list[dict], cat_dir: Path) -> dict:
    """본상품(추천 1순위)과 같은 종류·다른 브랜드 대안을 최대 3개 고릅니다. 조건을 느슨하게 하지 않고 찾은 만큼만 냅니다.
    반환: {"alt_candidates": [...], "alt_note": ""|"대안 없음"|이유, "alt_info": {...}}"""
    if not rec:
        return {"alt_candidates": [], "alt_note": "본상품 추천이 없어 대안을 찾지 않음", "alt_info": {}}
    main = rec[0]
    pool, info = alt_prefilter(main, items, keyword)
    info["pool_after_2_3_4"] = len(pool)
    if not main.get("price"):
        return {"alt_candidates": [], "alt_note": "본상품 가격이 없어 가격 조건(④)을 확인할 수 없음", "alt_info": info}
    if not pool:
        return {"alt_candidates": [], "alt_note": "대안 없음", "alt_info": info}

    def cached(x: dict) -> dict | None:
        p = _cache_path(cat_dir, _pid(x))
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def fetch(prods: list[dict]) -> str:
        pr = [{"id": _pid(x), "url": x["url"]} for x in prods]
        return collect_in_batches(pr, lambda q: q["id"], cat_dir, BATCH_CAT,
                                  lambda part, tmp: _js_category(part, tmp), "category", "카테고리 확인")

    st = fetch([main])
    mc = cached(main)
    if st != "ok" or not mc or not mc.get("category"):
        why = {"lost": "로그인이 풀려 카테고리를 읽지 못함", "incomplete": "카테고리 확인이 덜 끝남(같은 명령으로 이어 가기)"}.get(st, "본상품 카테고리를 읽지 못함")
        return {"alt_candidates": [], "alt_note": why, "alt_info": info}
    info["main_category"] = mc["category"]
    chosen: list[dict] = []
    done = 0
    while done < len(pool):
        done = min(len(pool), done + BATCH_CAT)
        st = fetch(pool[:done])
        if st != "ok":
            info["stopped"] = st
            break
        chosen = pick_alt(mc["category"], pool[:done], lambda x: (cached(x) or {}).get("category", ""))
        if len(chosen) >= ALT_PICK:
            break
    info["category_checked"] = min(done, len(pool))
    note = "" if chosen else "대안 없음"
    if info.get("stopped") and len(chosen) < ALT_PICK:
        note = ("로그인이 풀려 일부만 확인함" if info["stopped"] == "lost" else "카테고리 확인이 덜 끝남(같은 명령으로 이어 가기)") + (f" — 찾은 {len(chosen)}개" if chosen else "")
    return {"alt_candidates": chosen, "alt_note": note, "alt_info": info}


# ───────────────────────── summary.md ─────────────────────────

def write_summary(run: Path, data: dict) -> None:
    L = [f"# 제품 후보 요약 — {data['channel_label']} ({data['channel']})", "",
         f"- 만든 시각: {data['created']}", f"- 검색어: {', '.join(k['keyword'] for k in data['keywords'])}",
         "- 리뷰 수·평점은 모두 네이버 기준입니다. 브랜드(추정)는 화면에 칸이 없어 상품명에서 추정한 값입니다.", ""]
    for k in data["keywords"]:
        L += [f"## 검색어: {k['keyword']}", "",
              f"- 전체 검색 결과 {k.get('total') or '?'}개 중 상위 {len(k['items'])}개를 모음"
              + (" · **기준 낮춤(평점 4.3 이상)**" if k["relaxed"] else ""), "",
              "### 추천 3개", ""]
        if not k["recommended"]:
            L.append("- 조건에 맞는 제품이 없습니다.")
        for r in k["recommended"]:
            pr = f"{r['price']:,}원" if r.get("price") else NO_PRICE
            L.append(f"- **{r['name']}** — {pr} · 수수료 {r['commission']}% · {r['reason']}")
            if r.get("coupang"):
                c = r["coupang"]
                L.append(f"  - 쿠팡: {c['verdict']} ({c['why']})"
                         + (f" · 쿠팡 가격 {c['price']:,}원" if c.get("price") else ""))
        if "alt_candidates" in k:
            L += ["", "### 대안 후보(다른 브랜드·같은 종류)", ""]
            if not k["alt_candidates"]:
                L.append(f"- {k.get('alt_note') or '대안 없음'}")
            for r in k["alt_candidates"]:
                L.append(f"- **{r['name']}** — {r['price']:,}원 · {r['reason']}")
            if k["alt_candidates"] and k.get("alt_note"):
                L.append(f"- 참고: {k['alt_note']}")
        L += ["", "### 상위 30개", "", "| 순위 | 상품명 | 브랜드(추정) | 가격 | 수수료 | 리뷰 | 평점 |", "|---|---|---|---|---|---|---|"]
        for x in k["items"]:
            px = f"{x['price']:,}" if x["price"] else NO_PRICE
            L.append(f"| {x['rank']} | {x['name'].replace('|', '/')} | {x['brand']} | "
                     f"{px} | {x['commission']}% | {(x['reviews'] or 0):,} | {x['rating']} |")
        L.append("")
    sk = data.get("skipped_no_query") or []
    if sk:
        L += ["## 검색어 없음 (건너뜀)", "",
              "plan 칸에 '검색어'가 없어 건너뛴 항목입니다. 조건 문장은 검색어로 쓰지 않았습니다.", ""]
        L += [f"- {x['id']} · {x['keyword']} · 찾을 조건: {x['condition'] or '(없음)'}" for x in sk]
        L.append("")
    (run / "summary.md").write_text("\n".join(L), encoding="utf-8")


# ───────────────────────── 메인 ─────────────────────────

def main() -> int:
    try:
        return _main()
    finally:
        try:
            _finish_space()
        except Exception:  # noqa: BLE001
            pass


def _main() -> int:
    ap = argparse.ArgumentParser(description="제품 후보 수집·추천 (읽기 전용)")
    ap.add_argument("--channel", required=True, choices=CHANNELS)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--keywords", help='쉼표로 구분: "키워드1,키워드2"')
    g.add_argument("--from-queue", action="store_true", help="그 채널 대기열의 '찾을 제품 조건'을 읽기만 해서 검색어로 씀")
    ap.add_argument("--alt-keywords", help='틀 B 검색어(쉼표 구분). 이 검색어에만 대안 후보를 뽑음. 안 쓰면 radar_naver 에서 모델명이 든 검색어에만 뽑음')
    ap.add_argument("--no-alt", action="store_true", help="대안 후보를 뽑지 않음")
    a = ap.parse_args()

    skipped: list[dict] = []
    if a.keywords:
        kws = [k.strip() for k in a.keywords.split(",") if k.strip()]
    else:
        kws, skipped = keywords_from_queue(a.channel)
    run = RUN_ROOT / f"{datetime.now():%Y%m%d}_{a.channel}"
    if not kws:
        print("검색어가 없습니다. (--from-queue 는 plan 칸에 '검색어'가 있는 대기열 항목이 있어야 합니다)")
        if skipped:
            run.mkdir(parents=True, exist_ok=True)
            write_summary(run, {"channel": a.channel, "channel_label": CHANNEL_LABEL[a.channel],
                                "created": datetime.now().strftime("%Y-%m-%d %H:%M"), "keywords": [],
                                "skipped_no_query": skipped})
            print(f"검색어 없음 {len(skipped)}건을 summary.md 에 남겼습니다: {run}")
        return 0
    (run / "img").mkdir(parents=True, exist_ok=True)
    print(f"검색어 {len(kws)}개: {kws}")

    # 1) 쇼핑커넥트
    raw_path = run / "raw_naver.json"
    nav_dir = run / "raw_naver_parts"
    st = collect_in_batches(kws, lambda k: k, nav_dir, BATCH_KW,
                            lambda part, tmp: _js_naver(part, tmp, TOP_N), "naver", "쇼핑커넥트")
    if st == "lost":
        print("멈춤: 쇼핑커넥트 로그인이 풀려 있습니다. 에고라이트에서 직접 로그인한 뒤 같은 명령을 다시 실행하세요(받은 것은 이어서 씁니다).")
        return 3
    if st == "incomplete":
        print("아직 못 받은 검색어가 있습니다. 같은 명령을 다시 실행하면 이어서 받습니다.")
        return 4
    raw = {"lost": False, "res": [json.loads(_cache_path(nav_dir, k).read_text(encoding="utf-8")) for k in kws]}
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    data = {"channel": a.channel, "channel_label": CHANNEL_LABEL[a.channel],
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "keywords" if a.keywords else "queue",
            "criteria": {"top_n": TOP_N, "rating_min": RATING_MIN, "rating_relaxed": RATING_RELAXED, "pick": PICK},
            "keywords": [], "skipped_no_query": skipped}
    if a.alt_keywords:
        alt_kws = {k.strip() for k in a.alt_keywords.split(",") if k.strip()}
    elif a.channel == "radar_naver":
        alt_kws = {k for k in kws if model_of(k)}  # 모델명이 든 검색어 = 틀 B 로 봄
    else:
        alt_kws = set()
    for ki, r in enumerate(raw["res"], 1):
        items = [parse_naver_item(x, i, r["kw"]) for i, x in enumerate(r["items"][:TOP_N], 1)]
        rec, relaxed = recommend(items)
        for x in rec:  # 대표 이미지: 추천 3개만
            x["image"] = download_image(x["image_url"], run / "img" / f"k{ki}_r{x['rank']}.jpg")
        entry = {"keyword": r["kw"], "total": r["total"], "items": items, "recommended": rec, "relaxed": relaxed}
        if not a.no_alt and r["kw"] in alt_kws:
            entry.update(build_alt(r["kw"], items, rec, run / "raw_category_parts"))
        data["keywords"].append(entry)

    # 2) 쿠팡파트너스(W싼세계만)
    if a.channel == "wp_tips":
        qs = [{"id": f"{ki}:{x['rank']}", "query": coupang_query(x)}
              for ki, k in enumerate(data["keywords"], 1) for x in k["recommended"]]
        cp_dir = run / "raw_coupang_parts"
        st = collect_in_batches(qs, lambda q: q["id"], cp_dir, BATCH_CP,
                                lambda part, tmp: _js_coupang(part, tmp), "coupang", "쿠팡 확인")
        if st == "incomplete":
            print("쿠팡 확인이 덜 끝났습니다. 같은 명령을 다시 실행하면 이어서 합니다.")
            return 4
        if st == "lost":
            print("멈춤: 쿠팡파트너스 로그인이 풀렸습니다. 쿠팡 칸은 비워 둡니다.")
        else:
            cp = {q["id"]: json.loads(_cache_path(cp_dir, q["id"]).read_text(encoding="utf-8")) for q in qs}
            (run / "raw_coupang.json").write_text(json.dumps({"lost": False, "res": list(cp.values())}, ensure_ascii=False), encoding="utf-8")
            for ki, k in enumerate(data["keywords"], 1):
                for x in k["recommended"]:
                    r = cp.get(f"{ki}:{x['rank']}")
                    if r:
                        c = judge_coupang(x, parse_coupang(r["items"]))
                        c["query"] = r["query"]
                        c["naver_price"] = x["price"]
                        c["review_note"] = "리뷰 수는 네이버 기준"
                        x["coupang"] = c

    (run / "candidates.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    write_summary(run, data)
    for k in data["keywords"]:
        print(f"[{k['keyword']}] 모음 {len(k['items'])}개 · 추천 {len(k['recommended'])}개 · 기준 낮춤 {'예' if k['relaxed'] else '아니오'}")
    print(f"저장: {run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
