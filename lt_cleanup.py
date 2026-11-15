"""
혜택주머니 "🧹 묶음 정리" — 5편 묶음을 3편으로 줄이는 작업의 계산·실행 부분.
화면(app.py의 /lt-cleanup)이 이 파일의 함수를 부릅니다. 이 파일은 Flask를 모르고, 저장·재동기화·글 빼기는 app.py가 넘겨 주는
`env`(어댑터)를 통해서만 합니다. 그래서 시험할 때 가짜 워드프레스(mock)와 json 복사본으로 같은 흐름을 그대로 돌릴 수 있습니다.

실행 순서(한 묶음, 고정):
  ① 남는 글 본문 정리 → 저장 → 재동기화   ② 뺄 글 [묶음에서 빼기](retired)   ③ 워드프레스 휴지통(영구 삭제 금지)
  ④ Redirection 플러그인으로 301 생성   ⑤ 뺀 글 주소가 301로 합칠 글에 가는지 확인
어느 단계든 실패하면 거기서 멈추고, 다음 묶음으로 자동으로 넘어가지 않습니다.
"""
import html as _html
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import quote, unquote, urljoin, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "logs", "lt_cleanup_log.jsonl")
PREVIEW_DIR = os.path.join(BASE_DIR, "preview")

# 정리표 (워드프레스 글 번호 기준, 뺄 글 → 합칠 글). 화면의 기본값입니다.
CLEANUP_TABLE = [
    {"name": "최저임금", "rows": [(904, 899), (920, 899)]},
    {"name": "청년미래적금", "rows": [(883, 893), (878, 873)]},
    {"name": "지역가입자 건강보험료", "rows": [(952, 947), (958, 942)]},
    {"name": "양육비 선지급", "rows": [(969, 974), (988, 974)]},
    {"name": "청년일자리도약장려금", "rows": [(807, 791), (799, 791)]},
    {"name": "두루누리", "rows": [(859, 853), (843, 838)]},
    {"name": "연말정산 미리보기", "rows": [(768, 763), (772, 763)]},
    {"name": "국가장학금", "rows": [(750, 742), (754, 742)]},
    {"name": "소상공인 정책자금", "rows": [(669, 665), (673, 665)]},
    {"name": "근로장려금", "rows": [(642, 634), (647, 638)]},
    {"name": "고용보험", "rows": [(623, 595), (619, 615)]},
    {"name": "건강보험 피부양자", "rows": [(698, 694)]},
    {"name": "디딤돌대출", "rows": [(555, 547), (559, 547)]},
    {"name": "청년도약계좌", "rows": [(583, 579)]},
    {"name": "국민취업지원제도", "rows": [(449, 431), (441, 437)]},
    {"name": "소상공인 에너지바우처", "rows": [(390, 382), (386, 378)]},
    {"name": "청년월세지원", "rows": [(329, 321), (333, 321)]},
    {"name": "청년내일저축계좌", "rows": [(238, 230), (242, 234)]},
    {"name": "전세자금대출", "rows": [(183, 179), (187, 170)]},
    {"name": "실업급여", "rows": [(98, 83)]},
]

# 앱 기록에 없는 글의 301 (원본 경로 → 합칠 글 번호의 주소). 그 글 번호가 속한 묶음의 [실행]에서 함께 처리합니다.
EXTRA_REDIRECTS = [
    {"source_path": "/양육비-선지급-한부모면-만-18세-자녀도-받을-수-있어-2/", "target_post_id": 974},
]

GSC_DAYS = 91            # "최근 3개월"
GSC_CACHE_SECONDS = 600  # 같은 서치콘솔 읽기를 10분 동안 다시 쓰기

_P_RE = re.compile(r"[?&]p=(\d+)")


# ─── 주소 비교 ──────────────────────────────────────────────────────────────

def split_url(url: str) -> tuple[str, str]:
    """(호스트(www 제외, 소문자), 경로(퍼센트 해제·소문자·끝 슬래시 제거)). 호스트가 없는 주소는 호스트가 ""."""
    p = urlparse((url or "").strip())
    host = re.sub(r"^www\.", "", (p.netloc or "").lower())
    return host, unquote(p.path or "").lower().rstrip("/")


def same_page(a: str, b: str) -> bool:
    ha, pa = split_url(a)
    hb, pb = split_url(b)
    return bool(pa) and pa == pb and (not ha or not hb or ha == hb)


def source_path_of(url: str) -> str:
    """301 원본 경로: 주소에서 경로만 (퍼센트 해제한 사람이 읽는 형태, 앞뒤 슬래시 유지)."""
    path = unquote(urlparse((url or "").strip()).path or "")
    if not path.startswith("/"):
        path = "/" + path
    return path


def slug_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", name).strip("_")


def preview_filename(name: str) -> str:
    return f"lt_cleanup_preview_{slug_name(name)}.html"


# ─── 로그 ────────────────────────────────────────────────────────────────────

def append_log(entry: dict, path: str | None = None) -> None:
    path = path or LOG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), **entry}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_log(path: str | None = None) -> list[dict]:
    path = path or LOG_PATH
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def last_bundle_results(path: str | None = None) -> dict:
    """{묶음 이름: 가장 최근 'bundle_result' 로그}"""
    out = {}
    for rec in read_log(path):
        if rec.get("step") == "bundle_result" and rec.get("bundle"):
            out[rec["bundle"]] = rec
    return out


# ─── 글 → 번호·주소 ──────────────────────────────────────────────────────────

def post_id_of(art: dict) -> int | None:
    """저장된 글(날것 dict)의 워드프레스 글 번호. 묶음에서 뺀 글은 비우기 전 값(retired.previous)에서 찾습니다."""
    if not isinstance(art, dict):
        return None
    for src in ((art.get("platform_post_id") or {}).get("wordpress"),
                ((art.get("retired") or {}).get("previous") or {}).get("platform_post_id")):
        try:
            if src:
                return int(src)
        except (TypeError, ValueError):
            pass
    for u in ((art.get("platform_published_url") or {}).get("wordpress"),
              ((art.get("retired") or {}).get("previous") or {}).get("platform_published_url")):
        m = _P_RE.search(u or "")
        if m:
            return int(m.group(1))
    return None


def url_of(art: dict) -> str:
    if not isinstance(art, dict):
        return ""
    return ((art.get("platform_published_url") or {}).get("wordpress")
            or ((art.get("retired") or {}).get("previous") or {}).get("platform_published_url") or "")


# ─── 본문 정리 ───────────────────────────────────────────────────────────────

_A_RE = re.compile(r'<a\b[^>]*?\bhref="([^"]*)"[^>]*>(.*?)</a>', re.S | re.I)
_LI_RE = re.compile(r"[ \t]*<li\b[^>]*>(.*?)</li>[ \t]*\n?", re.S | re.I)
_EMPTY_LIST_RE = re.compile(r"<(ul|ol)\b[^>]*>\s*</\1>[ \t]*\n?", re.I)
_H2_BEFORE_RE = re.compile(r'<h2\b[^>]*\bid="(toc-\d+)"[^>]*>.*?</h2>\s*$', re.S | re.I)
_LINK_PH_RE = re.compile(r"\{\{LINK:(.*?)\}\}")


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def _short(s: str, limit: int = 420) -> str:
    """화면에 보여 줄 조각: style 속성을 빼고, 긴 주소는 읽기 좋게 풀고, 너무 길면 줄입니다."""
    s = re.sub(r'\sstyle="[^"]*"', "", s or "")
    s = re.sub(r'href="([^"]*)"', lambda m: 'href="' + unquote(m.group(1)) + '"', s)
    s = re.sub(r"\s*\n\s*", " ", s).strip()
    return s if len(s) <= limit else s[: limit // 2] + " … " + s[-limit // 2:]


def _match(href: str, targets: list[dict]) -> dict | None:
    """href가 가리키는 글이 targets 중 어느 것인지(없으면 None)."""
    if not href or href.startswith("#"):
        return None
    m = _P_RE.search(href)
    for t in targets:
        if m and t.get("post_id") and int(m.group(1)) == int(t["post_id"]):
            return t
        if t.get("url") and same_page(href, t["url"]):
            return t
    return None


def _matches(href: str, targets: list[dict]) -> bool:
    return _match(href, targets) is not None


def clean_body(html_text: str, bad: list[dict], own: dict | None = None) -> tuple[str, list[dict]]:
    """
    남는 글 하나의 본문(platform_final.wordpress)에서 뺀 글로 가는 흔적을 정리합니다. bad = [{"post_id","url"}...], own = 이 글 자신.
      list_line   : 목록(<li>)이 뺀 글 링크 하나뿐이면 그 줄을 지움
      link        : 본문 속 뺀 글 링크 → 링크 주소를 그 글의 '합칠 글' 주소로 바꾸고 글자는 그대로 (합칠 글이 이 글 자신이면 링크만 없앰)
      self_link   : 이 글 자신으로 가는 링크(합칠 글에서 생김) → 링크만 없앰
      empty_list  : 줄을 지워서 목록이 텅 비면, 목록과 바로 위 소제목(목차 줄 포함)을 함께 지움
    반환: (새 본문, 바뀐 곳 목록 [{"kind","label","before","after"}])
    """
    changes: list[dict] = []
    text = html_text or ""
    bad = list(bad or [])

    def _li(m):
        inner = m.group(1)
        anchors = _A_RE.findall(inner)
        if len(anchors) == 1 and _matches(anchors[0][0], bad) and _text(inner) == _text(anchors[0][1]):
            changes.append({"kind": "list_line", "label": "목록의 뺀 글 줄 삭제", "before": _short(m.group(0)), "after": "(줄 삭제)"})
            return ""
        return m.group(0)

    text = _LI_RE.sub(_li, text)

    # 줄을 지워서 텅 빈 목록: 목록 + 바로 위 소제목(+ 목차 줄)까지
    while True:
        m = _EMPTY_LIST_RE.search(text)
        if not m:
            break
        head = _H2_BEFORE_RE.search(text[: m.start()])
        if head:
            toc_id = head.group(1)
            heading = _text(head.group(0))
            cut_from = head.start()
            text = text[:cut_from] + text[m.end():]
            text = re.sub(r'<li\b[^>]*>\s*<a\b[^>]*href="#' + re.escape(toc_id) + r'"[^>]*>.*?</a>\s*</li>[ \t]*\n?', "", text, count=1, flags=re.S)
            changes.append({"kind": "empty_list", "label": "목록이 비어서 소제목과 함께 삭제",
                            "before": f"{heading} + (빈 목록) + 목차 줄", "after": "(삭제)"})
        else:
            text = text[: m.start()] + text[m.end():]
            changes.append({"kind": "empty_list", "label": "목록이 비어서 삭제(바로 위 소제목은 없음 — 확인 필요)",
                            "before": "(빈 목록)", "after": "(삭제)"})

    # 본문 속 링크: 뺀 글 / 자기 자신 → 링크만 없앰
    own_targets = [own] if own and (own.get("url") or own.get("post_id")) else []
    out, pos = [], 0
    for m in _A_RE.finditer(text):
        href, inner = m.group(1), m.group(2)
        kind = label = None
        hit = _match(href, bad)
        new_href = None
        if hit:
            new_href = hit.get("target_url")
            if new_href and not _matches(new_href, own_targets):
                kind, label = "link", "뺀 글 링크 → 합칠 글로 연결 변경"
            else:  # 합칠 글 주소가 이 글 자신이거나 모르면: 링크만 없앰
                kind, label, new_href = "self_link", "자기 자신 링크 → 글자만 남김", None
        elif own_targets and _matches(href, own_targets):
            kind, label = "self_link", "자기 자신 링크 → 글자만 남김"
        if not kind:
            continue
        ctx_a = _text(re.sub(r"^[^<]*>", "", text[max(0, m.start() - 240): m.start()]))[-40:]
        ctx_b = _text(re.sub(r"<[^>]*$", "", text[m.end(): m.end() + 240]))[:40]
        if new_href:
            replaced = m.group(0).replace(f'href="{href}"', f'href="{new_href}"', 1)
            after_txt = _short(replaced, 300)
        else:
            replaced = inner
            after_txt = _text(inner)
        changes.append({"kind": kind, "label": label,
                        "before": f"…{ctx_a} {_short(m.group(0), 300)} {ctx_b}…",
                        "after": f"…{ctx_a} {after_txt} {ctx_b}…"})
        out.append(text[pos:m.start()])
        out.append(replaced)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), changes


def link_placeholders(html_text: str) -> list[str]:
    return [m.strip() for m in _LINK_PH_RE.findall(html_text or "")]


# ─── 묶음 계획 ───────────────────────────────────────────────────────────────

def _member(item: dict, art: dict) -> dict:
    final = ((art.get("platform_final") or {}).get("wordpress") or "")
    return {
        "aid": item["id"], "pid": post_id_of(art), "url": url_of(art),
        "title": ((art.get("platform_title") or {}).get("wordpress") or "").strip() or item.get("keyword") or item["id"],
        "keyword": item.get("keyword") or "", "role": item.get("cluster_role") or "",
        "retired": bool(art.get("retired")), "merged_into": (art.get("retired") or {}).get("merged_into"), "has_body": bool(final.strip()),
        "live": bool((art.get("platform_post_id") or {}).get("wordpress") or (art.get("platform_published_url") or {}).get("wordpress")),
    }


def build_bundle_plan(entry: dict, queue: list[dict], articles: dict, selection: dict | None = None) -> dict:
    """
    정리표 한 묶음의 계획. selection = {뺄 글 번호: 합칠 글 번호 또는 None(체크 해제)} — 없으면 정리표 기본값.
    반환: {"name","seed","week","members","options","rows":[...],"errors":[...],"keepers":[...]}
    rows 항목: {"src_pid","dst_pid","checked","src","dst","done","errors"}
    """
    name = entry["name"]
    pid_item = {}
    for it in queue:
        art = articles.get(it["id"])
        pid = post_id_of(art) if isinstance(art, dict) else None
        if pid:
            pid_item[pid] = (it, art)
    first = pid_item.get(entry["rows"][0][0])
    plan = {"name": name, "seed": "", "week": "", "members": [], "options": [], "rows": [], "errors": [], "keepers": [], "extra_redirects": []}
    if not first:
        plan["errors"].append(f"글 번호 {entry['rows'][0][0]}를 앱 기록에서 찾을 수 없습니다.")
        return plan
    seed, week = first[0].get("cluster_seed"), first[0].get("week")
    plan["seed"], plan["week"] = seed or "", week or ""
    members = []
    for it in queue:
        if it.get("cluster_seed") != seed or it.get("week") != week:
            continue
        art = articles.get(it["id"])
        if not isinstance(art, dict):
            continue
        mem = _member(it, art)
        if mem["pid"] and (mem["live"] or mem["retired"]):
            members.append(mem)
    by_pid = {m["pid"]: m for m in members}
    plan["members"] = members

    # 줄 = 정리표 줄(기본 체크) + 그 묶음의 '대표가 아닌 발행 글' 나머지(기본 체크 해제, 합칠 글 기본값 = 대표글, 대표 없으면 정리표의 중심 글)
    # selection = {뺄 글 번호: (체크 여부, 합칠 글 번호)} — 화면에서 보낸 값. 없으면 기본값.
    selection = selection or {}
    table_src = [s for s, _ in entry["rows"]]
    heads = [m["pid"] for m in members if m["role"] == "대표" and not m["retired"]]
    center = heads[0] if heads else Counter(d for _, d in entry["rows"]).most_common(1)[0][0]
    aid_pid = {m["aid"]: m["pid"] for m in members}
    # 이미 뺀 글 수가 정리표 줄 수 이상이면 완료된 묶음 = 잠금(화면에서 다른 글로 바꿀 수 없음)
    table_done = sum(1 for m in members if m["retired"]) >= len(table_src)
    plan["locked"] = table_done
    candidates = [(s, d, True) for s, d in entry["rows"]]
    for m in members:
        if m["pid"] in table_src or m["role"] == "대표":
            continue
        if m["retired"]:  # 정리표 밖의 글을 대신 뺀 경우: 이어서 하려면 줄이 남아 있어야 합니다(이미 뺌, 합칠 글은 기록된 값)
            candidates.append((m["pid"], aid_pid.get(m["merged_into"]) or center, True))
        else:
            candidates.append((m["pid"], center, False))
    checked_src = set()
    rows = []
    for src_pid, default_dst, default_checked in candidates:
        checked, chosen = selection.get(src_pid, (default_checked, default_dst))
        chosen = chosen or default_dst
        if table_done and not (by_pid.get(src_pid) or {}).get("retired"):
            checked = False  # 완료된 묶음은 바꿀 수 없음(잠금)
        row = {"src_pid": src_pid, "dst_pid": chosen, "checked": bool(checked), "in_table": src_pid in table_src,
               "src": by_pid.get(src_pid), "dst": by_pid.get(chosen), "done": False, "errors": []}
        if row["src"] and row["src"]["retired"]:
            row["done"] = True
        if row["checked"]:
            checked_src.add(src_pid)
        rows.append(row)
    for row in rows:
        if not row["checked"] or row["done"]:
            if row["done"] and row["checked"] and not row["dst"]:
                row["errors"].append("합칠 글을 묶음에서 찾을 수 없습니다.")
            continue
        src, dst, errs = row["src"], row["dst"], row["errors"]
        if not src:
            errs.append(f"뺄 글 {row['src_pid']}번이 이 묶음에 없습니다.")
            continue
        if src["role"] == "대표":
            errs.append("대표글은 뺄 수 없습니다([묶음에서 빼기]가 막습니다).")
        if not src["url"]:
            errs.append("뺄 글의 주소가 없습니다.")
        if not dst:
            errs.append(f"합칠 글 {row['dst_pid']}번이 이 묶음에 없습니다(같은 묶음 글만 고를 수 있어요).")
            continue
        if dst["pid"] == src["pid"]:
            errs.append("뺄 글과 합칠 글이 같습니다.")
        if dst["retired"]:
            errs.append("합칠 글이 이미 묶음에서 빠진 글입니다.")
        if dst["pid"] in checked_src:
            errs.append("합칠 글이 이번에 뺄 글입니다.")
        if not dst["url"]:
            errs.append("합칠 글의 주소가 없습니다.")
    plan["rows"] = rows

    plan["keepers"] = [m for m in members if not m["retired"] and m["pid"] not in checked_src]
    plan["options"] = [m for m in members if not m["retired"]]
    for ex in EXTRA_REDIRECTS:
        tgt = by_pid.get(ex["target_post_id"])
        if tgt and tgt["url"]:
            plan["extra_redirects"].append({"source_path": ex["source_path"], "target_url": tgt["url"], "target_pid": tgt["pid"]})
    if not any(r["checked"] for r in rows):
        plan["errors"].append("체크된 줄이 없습니다.")
    elif len(plan["keepers"]) < 2:
        plan["errors"].append(f"남는 글이 {len(plan['keepers'])}편뿐입니다. 대표 포함 2편 이상 남아야 해서 이 조합은 실행할 수 없어요.")
    for r in rows:
        for e in r["errors"]:
            plan["errors"].append(f"{r['src_pid']}번 → {r['dst_pid']}번: {e}")
    return plan


def redirect_list(plan: dict) -> list[dict]:
    """만들 301 목록 [{"source_path","source_old_url","target_url","kind"}]"""
    out = []
    for r in plan["rows"]:
        if r["checked"] and r["src"] and r["dst"]:
            out.append({"source_path": source_path_of(r["src"]["url"]), "old_url": r["src"]["url"],
                        "target_url": r["dst"]["url"], "kind": "정리표", "src_pid": r["src_pid"]})
    for ex in plan.get("extra_redirects", []):
        out.append({"source_path": ex["source_path"], "old_url": None, "target_url": ex["target_url"], "kind": "추가(앱 기록에 없는 글)", "src_pid": None})
    return out


# ─── 서치콘솔(읽기 전용) ─────────────────────────────────────────────────────

_gsc_cache = {"at": 0.0, "data": None}


def fetch_gsc_metrics(today=None):
    """롱텀 사이트의 페이지별 최근 3개월 (클릭, 노출). 반환: ({정규화 주소: {"clicks","impressions"}}, (시작일, 끝일))
    기존 성과 화면의 읽기 전용 서비스 계정(perf_tracker)을 그대로 씁니다. 실패하면 perf_tracker.GscError."""
    import perf_tracker
    now = time.time()
    if _gsc_cache["data"] and now - _gsc_cache["at"] < GSC_CACHE_SECONDS:
        return _gsc_cache["data"]
    site = perf_tracker.site_urls().get("longterm")
    if not site:
        raise perf_tracker.GscError("롱텀 사이트 주소(GSC_SITE_LONGTERM)가 .env에 없습니다")
    session = perf_tracker._new_session()
    end = (today or datetime.now().date()) - timedelta(days=perf_tracker.DATA_LAG_DAYS)
    start = end - timedelta(days=GSC_DAYS - 1)
    rows = perf_tracker._query_rows(session, site, start.isoformat(), end.isoformat(), ["page"])
    merged = perf_tracker._merge_page_rows(rows)
    data = ({k: {"clicks": v["clicks"], "impressions": v["impressions"]} for k, v in merged.items()}, (start.isoformat(), end.isoformat()))
    _gsc_cache.update(at=now, data=data)
    return data


def attach_metrics(plan: dict, gsc: dict | None) -> None:
    """plan의 members에 clicks/impressions를 붙이고, 줄마다 ⚠ "바꾸기 권장"을 정합니다.
    규칙: 뺄 글의 클릭이 같은 묶음의 남길 글 중 하나보다 많으면 ⚠."""
    import perf_tracker
    for m in plan["members"]:
        k = perf_tracker.normalize_url(m["url"]) if m["url"] else ""
        got = (gsc or {}).get(k)
        m["clicks"] = got["clicks"] if got else (0 if gsc is not None else None)
        m["impressions"] = got["impressions"] if got else (0 if gsc is not None else None)
    keepers = plan["keepers"]
    for r in plan["rows"]:
        r["swap_hint"] = None
        if gsc is None or not r["checked"] or not r["src"]:
            continue
        weaker = [k for k in keepers if (k.get("clicks") or 0) < (r["src"].get("clicks") or 0)]
        if weaker:
            r["swap_hint"] = {"src_clicks": r["src"]["clicks"], "weaker": [{"pid": k["pid"], "title": k["title"], "clicks": k["clicks"]} for k in weaker]}


# ─── 미리보기 ────────────────────────────────────────────────────────────────

def build_preview(plan: dict, env, gsc_fn=None) -> dict:
    """묶음 하나의 미리보기 자료(워드프레스 변경 없음). gsc_fn() → ({주소: {clicks, impressions}}, (시작, 끝))."""
    gsc, gsc_range, gsc_error = None, None, ""
    if gsc_fn:
        try:
            gsc, gsc_range = gsc_fn()
        except Exception as e:  # noqa: BLE001 — 서치콘솔이 안 돼도 본문 미리보기는 보여 줍니다
            gsc_error = f"{type(e).__name__}: {e}"[:200]
    attach_metrics(plan, gsc)

    bad = [{"post_id": r["src"]["pid"], "url": r["src"]["url"], "target_url": (r["dst"] or {}).get("url")} for r in plan["rows"] if r["checked"] and r["src"]]
    bad_pids = {b["post_id"] for b in bad}
    retire_aids = {r["src"]["aid"]: r["src"] for r in plan["rows"] if r["checked"] and r["src"]}
    keepers = []
    for k in plan["keepers"]:
        art = env.get_article(k["aid"])
        body = (art.get("platform_final") or {}).get("wordpress") or ""
        new_body, changes = clean_body(body, bad, {"post_id": k["pid"], "url": k["url"]})
        holds = []
        for kw in link_placeholders(body):
            found = env.match_link(k["aid"], kw)
            hit = [c for c in ([found.get("target_id")] if found.get("status") == "match" else found.get("candidates", [])) if c in retire_aids]
            if hit:
                holds.append({"kw": kw, "status": found.get("status"), "to": [retire_aids[c]["title"] for c in hit]})
        keepers.append({**k, "changes": changes, "holds": holds, "has_body": bool(body.strip()),
                        "changed": new_body != body})
    return {"plan": plan, "gsc_ok": gsc is not None, "gsc_error": gsc_error, "gsc_range": gsc_range,
            "keepers": keepers, "redirects": redirect_list(plan), "bad_pids": sorted(bad_pids)}


# ─── 워드프레스 연결 (이 파일의 쓰기 동작은 휴지통 보내기·301 만들기뿐) ───────────

class WpError(Exception):
    pass


class WpClient:
    """워드프레스 REST + Redirection 플러그인(redirection/v1) 연결. site = {"url","username","app_password"}.
    휴지통 보내기는 DELETE에 force를 붙이지 않습니다(영구 삭제 금지)."""

    TIMEOUT = 45

    def __init__(self, site: dict | None = None):
        import wp_publisher
        self._wp = wp_publisher
        self._site = site

    # --- 내부
    def _site_info(self) -> dict:
        return self._site or self._wp._current_site()

    def _base(self) -> str:
        return (self._site_info().get("url") or "").rstrip("/")

    def _headers(self) -> dict:
        import base64
        s = self._site_info()
        token = base64.b64encode(f"{s['username']}:{s['app_password']}".encode()).decode()
        return {**self._wp.BROWSER_LIKE_HEADERS, "Authorization": f"Basic {token}"}

    def _send(self, method: str, path: str, **kw):
        import requests
        if "force" in (kw.get("params") or {}):
            raise WpError("force 옵션은 쓰지 않습니다(영구 삭제 금지).")
        return requests.request(method, f"{self._base()}{path}", headers=self._headers(), timeout=self.TIMEOUT, **kw)

    @staticmethod
    def _json(resp, what: str):
        try:
            return resp.json()
        except ValueError:
            raise WpError(f"{what}: JSON이 아닌 응답(HTTP {resp.status_code}) — {resp.text[:120]!r}")

    # --- Redirection 플러그인
    def redirection_ready(self) -> tuple[bool, str]:
        """플러그인 설치 여부(공개 /wp-json/ 의 namespaces)와 API 응답(권한 포함)을 GET으로만 확인합니다."""
        import requests
        try:
            r = requests.get(f"{self._base()}/wp-json/", headers=self._wp.BROWSER_LIKE_HEADERS, timeout=self.TIMEOUT)
            ns = (self._json(r, "사이트 정보") or {}).get("namespaces") or []
        except (requests.RequestException, WpError) as e:
            return False, f"워드프레스 정보를 읽지 못했습니다: {type(e).__name__} {e}"[:200]
        if "redirection/v1" not in ns:
            return False, "Redirection 플러그인이 설치·활성화되어 있지 않습니다(redirection/v1 없음)."
        try:
            r = self._send("get", "/wp-json/redirection/v1/redirect", params={"page": 0, "per_page": 5})  # per_page는 5~200만 허용
        except requests.RequestException as e:
            return False, f"Redirection API 요청 실패: {type(e).__name__}"[:200]
        if r.status_code in (401, 403):
            return False, f"Redirection API 권한 없음(HTTP {r.status_code}) — 앱 비밀번호 계정이 관리자 권한인지 확인하세요."
        if not r.ok:
            return False, f"Redirection API 응답 오류(HTTP {r.status_code})"
        return True, "Redirection API 응답 정상"

    def list_redirect_sources(self) -> set[str]:
        """이미 등록된 301 원본 경로들(정규화: 퍼센트 해제·소문자·끝 슬래시 제거)."""
        out, page = set(), 0
        while page < 100:
            r = self._send("get", "/wp-json/redirection/v1/redirect", params={"page": page, "per_page": 200})
            if not r.ok:
                raise WpError(f"301 목록 조회 실패(HTTP {r.status_code})")
            data = self._json(r, "301 목록")
            items = data.get("items") or []
            for it in items:
                out.add(norm_source(it.get("url") or ""))
            if len(items) < 200:
                break
            page += 1
        return out

    def _group_id(self) -> int:
        try:
            r = self._send("get", "/wp-json/redirection/v1/group", params={"page": 0, "per_page": 50})
            if r.ok:
                items = (self._json(r, "그룹 목록") or {}).get("items") or []
                for g in items:  # 플러그인이 '기본'으로 표시한 그룹(보통 id 1 "리디렉션") — '수정된 게시물' 같은 자동 그룹이 아님
                    if g.get("default") and g.get("enabled", True) and g.get("id"):
                        return int(g["id"])
        except Exception:  # noqa: BLE001 — 못 찾으면 기본 그룹 1
            pass
        return 1

    def create_redirect(self, source_path: str, target_url: str) -> dict:
        body = {"url": source_path, "match_type": "url", "action_type": "url", "action_code": 301,
                "action_data": {"url": target_url}, "group_id": self._group_id(), "title": "", "regex": False}
        r = self._send("post", "/wp-json/redirection/v1/redirect", json=body)
        if not r.ok:
            raise WpError(f"301 만들기 실패(HTTP {r.status_code}): {r.text[:200]}")
        return {"status": r.status_code}

    # --- 글
    def trash_post(self, post_id: int) -> dict:
        """휴지통으로 보냅니다(DELETE, force 없음). 이미 휴지통이면 already=True. 반환: {"status": "trash", "already": bool}"""
        import requests
        try:
            r = self._send("delete", f"/wp-json/wp/v2/posts/{int(post_id)}")
        except requests.RequestException as e:
            raise WpError(f"휴지통 요청 실패: {type(e).__name__} {e}"[:200])
        data = None
        try:
            data = r.json()
        except ValueError:
            pass
        code = (data or {}).get("code") if isinstance(data, dict) else None
        if r.status_code == 410 or code == "rest_already_trashed":
            return {"status": "trash", "already": True}
        if r.status_code == 404 or code == "rest_post_invalid_id":
            return {"status": "missing", "already": True}
        if not r.ok:
            raise WpError(f"휴지통 보내기 실패(HTTP {r.status_code}): {r.text[:200]}")
        if (data or {}).get("status") != "trash":
            raise WpError(f"휴지통 확인 실패: 응답 상태가 trash가 아님({(data or {}).get('status')!r})")
        return {"status": "trash", "already": False}

    def check_redirect(self, old_url: str, target_url: str) -> tuple[bool, str]:
        import requests
        try:
            r = requests.get(old_url, headers=self._wp.BROWSER_LIKE_HEADERS, allow_redirects=False, timeout=30)
        except requests.RequestException as e:
            return False, f"요청 실패: {type(e).__name__}"
        loc = urljoin(old_url, r.headers.get("Location", ""))
        if r.status_code == 301 and same_page(loc, target_url):
            return True, f"301 → {unquote(loc)}"
        return False, f"HTTP {r.status_code}" + (f" → {unquote(loc)}" if r.headers.get("Location") else " (이동 없음)")

    def public_status(self, post_ids: list[int]) -> dict:
        """공개 글만 보이는 GET(로그인 없음). {글 번호: status} — 공개 상태가 아니면 목록에 없음."""
        import requests
        r = requests.get(f"{self._base()}/wp-json/wp/v2/posts", headers=self._wp.BROWSER_LIKE_HEADERS, timeout=self.TIMEOUT,
                         params={"include": ",".join(str(i) for i in post_ids), "per_page": 100, "_fields": "id,status,link"})
        return {p["id"]: p.get("status") for p in self._json(r, "공개 글 조회")}


def norm_source(path: str) -> str:
    return unquote((urlparse(path.strip()).path if "://" in path else path.strip().split("?", 1)[0])).lower().rstrip("/")


# ─── 실행 ────────────────────────────────────────────────────────────────────

def _undo_text(stage: str, ctx: dict) -> str:
    backups = ", ".join(ctx.get("backups") or []) or "(백업 없음)"
    if stage == "①":
        return ("워드프레스에 이미 반영된 남는 글은 '뺄 글 링크만 없어진' 상태라 그대로 두어도 안전합니다. 아직 반영 안 된 글은 원래 본문으로 되돌려 두었습니다. "
                "원인을 고친 뒤 같은 묶음을 [실행]하면 이어서 진행합니다. (아직 뺀 글·휴지통·301은 아무것도 바뀌지 않았습니다)")
    if stage == "②":
        return ("앱 기록에서 일부 글이 '뺌' 처리되었을 수 있습니다. 되돌리려면 backup 폴더의 실행 전 백업으로 content_queue.json·articles.json을 덮어쓰세요: "
                f"{backups}. (이 이후에 다른 수정을 했다면 백업으로 덮어쓰기 전에 알려 주세요.) 다시 [실행]하면 이어서 진행합니다.")
    if stage == "③":
        return ("앱에서는 '뺌' 처리됐지만 워드프레스에서 아직 공개인 글이 있을 수 있습니다. 다시 [실행]하면 이어서 휴지통으로 보냅니다. "
                f"휴지통으로 간 글은 워드프레스 관리자 > 글 > 휴지통에서 '복원'할 수 있습니다. 앱 기록 복구용 백업: {backups}")
    return ("뺀 글은 이미 워드프레스 휴지통에 있습니다. 301이 없는 글은 방문자에게 '없는 페이지'로 보이므로 아래 '손으로 넣을 301 목록'을 Redirection 관리 화면(도구 > Redirection)에 직접 넣거나, "
            "원인을 고치고 다시 [실행]하세요(이미 있는 301은 건너뜁니다). 완전히 되돌리려면 휴지통에서 복원하고 앱 기록은 백업으로 복구하세요: " + backups)


def run_bundle(plan: dict, env, wp, log_path: str | None = None) -> dict:
    """묶음 하나 실행. env = 앱 어댑터, wp = WpClient(또는 같은 모양의 가짜). 실패하면 그 자리에서 멈춥니다."""
    name = plan["name"]
    res = {"bundle": name, "ok": False, "stage": "", "steps": [], "message": "", "undo": "", "manual": [], "backups": [], "changed_anything": False}

    def log(step, **kw):
        append_log({"bundle": name, "step": step, **kw}, log_path)

    def add(step, aid=None, pid=None, ok=True, detail="", old_url=None):
        res["steps"].append({"step": step, "aid": aid, "pid": pid, "ok": ok, "detail": detail})
        log(step, article_id=aid, post_id=pid, old_url=old_url, ok=ok, detail=detail)

    def fail(stage, msg):
        res.update(ok=False, stage=stage, message=msg, undo=_undo_text(stage, res), manual=[
            {"source_path": x["source_path"], "target_url": x["target_url"]} for x in redirect_list(plan)])
        log("bundle_result", ok=False, stage=stage, detail=msg)
        return res

    if plan["errors"]:
        return fail("준비", "실행할 수 없습니다 — 아무것도 바뀌지 않았습니다: " + " / ".join(plan["errors"]))

    redirects = redirect_list(plan)
    res["manual"] = [{"source_path": x["source_path"], "target_url": x["target_url"]} for x in redirects]

    # 준비: Redirection이 안 되면 ①~③도 하지 않고 손으로 넣을 목록만 보여 줍니다
    try:
        ready, msg = wp.redirection_ready()
        existing = wp.list_redirect_sources() if ready else set()
    except Exception as e:  # noqa: BLE001
        ready, msg, existing = False, f"{type(e).__name__}: {e}"[:200], set()
    log("preflight", ok=ready, detail=msg)
    if not ready:
        res.update(ok=False, stage="준비", message=f"Redirection(301) 사용 불가 — ①~③은 하지 않았습니다. 이유: {msg}",
                   undo="아무것도 바뀌지 않았습니다. 아래 '손으로 넣을 301 목록'을 직접 넣을 수 있고, 플러그인이 준비되면 다시 실행하세요.")
        log("bundle_result", ok=False, stage="준비", detail=res["message"])
        return res

    # 실행 전 json 자동 백업
    try:
        res["backups"] = env.backup("lt_cleanup")
    except Exception as e:  # noqa: BLE001
        return fail("준비", f"실행 전 백업에 실패해 시작하지 않았습니다(아무것도 안 바뀜): {type(e).__name__}: {e}"[:300])
    log("backup", ok=True, detail=", ".join(res["backups"]))

    rows = [r for r in plan["rows"] if r["checked"]]
    bad = [{"post_id": r["src"]["pid"], "url": r["src"]["url"], "target_url": r["dst"]["url"]} for r in rows]
    bad_pids = {b["post_id"] for b in bad}

    # ① 남는 글 본문 정리 → 저장 → 재동기화
    for k in plan["keepers"]:
        art = env.get_article(k["aid"])
        orig = (art.get("platform_final") or {}).get("wordpress") or ""
        new, changes = clean_body(orig, bad, {"post_id": k["pid"], "url": k["url"]})
        saved = False
        try:
            if new != orig:
                art["platform_final"]["wordpress"] = new
                env.set_article(k["aid"], art)
                saved = True
                res["changed_anything"] = True
            env.resync(k["aid"], bad_pids)
            add("①", k["aid"], k["pid"], True, f"본문 {len(changes)}곳 정리 후 재동기화 완료", old_url=k["url"])
        except Exception as e:  # noqa: BLE001
            if saved:
                art = env.get_article(k["aid"])
                art["platform_final"]["wordpress"] = orig
                env.set_article(k["aid"], art)
            add("①", k["aid"], k["pid"], False, f"{type(e).__name__}: {e}"[:300], old_url=k["url"])
            return fail("①", f"① 남는 글 '{k['title']}'({k['pid']}번) 반영 실패 — {type(e).__name__}: {e}"[:400])

    # ② 뺄 글마다: 번호·주소를 먼저 잡아 두고 → 묶음에서 빼기
    captured = []
    for r in rows:
        src, dst = r["src"], r["dst"]
        art = env.get_article(src["aid"])
        pid, old_url = post_id_of(art), url_of(art)
        if not pid or not old_url:
            add("②", src["aid"], pid, False, "글 번호나 주소를 잡지 못했습니다", old_url=old_url)
            return fail("②", f"② '{src['title']}' 의 글 번호·주소를 잡지 못해 멈췄습니다.")
        captured.append({"aid": src["aid"], "pid": pid, "old_url": old_url, "target_url": dst["url"], "title": src["title"], "target_aid": dst["aid"]})
        log("capture", article_id=src["aid"], post_id=pid, old_url=old_url, target_url=dst["url"], ok=True)
        if src["retired"] or art.get("retired"):
            add("②", src["aid"], pid, True, "이미 묶음에서 뺀 글 — 건너뜀", old_url=old_url)
            continue
        try:
            r2 = env.retire(src["aid"], dst["aid"])
        except Exception as e:  # noqa: BLE001
            add("②", src["aid"], pid, False, f"{type(e).__name__}: {e}"[:300], old_url=old_url)
            return fail("②", f"② '{src['title']}'({pid}번) 묶음에서 빼기 실패 — {e}"[:400])
        if not r2.get("ok"):
            add("②", src["aid"], pid, False, r2.get("reason") or "빼기 실패", old_url=old_url)
            return fail("②", f"② '{src['title']}'({pid}번) 묶음에서 빼기 거절 — {r2.get('reason')}")
        res["changed_anything"] = True
        res.setdefault("backups_retire", []).extend(r2.get("backups") or [])
        add("②", src["aid"], pid, True, f"묶음에서 뺌(합칠 글 {dst['pid']}번 기록)", old_url=old_url)

    # ③ 워드프레스 휴지통
    for c in captured:
        try:
            t = wp.trash_post(c["pid"])
        except Exception as e:  # noqa: BLE001
            add("③", c["aid"], c["pid"], False, f"{type(e).__name__}: {e}"[:300], old_url=c["old_url"])
            return fail("③", f"③ {c['pid']}번 글을 휴지통으로 보내지 못했습니다 — {e}"[:400])
        note = "이미 휴지통" if t.get("already") and t["status"] == "trash" else ("워드프레스에 이미 없음" if t["status"] == "missing" else "휴지통으로 보냄")
        add("③", c["aid"], c["pid"], True, note, old_url=c["old_url"])

    # ④ 301 만들기 (같은 원본이 이미 있으면 건너뜀)
    jobs = [{"aid": c["aid"], "pid": c["pid"], "old_url": c["old_url"], "source_path": source_path_of(c["old_url"]), "target_url": c["target_url"]} for c in captured]
    for ex in plan.get("extra_redirects", []):
        jobs.append({"aid": None, "pid": None, "old_url": None, "source_path": ex["source_path"], "target_url": ex["target_url"]})
    for j in jobs:
        key = norm_source(j["source_path"])
        if key in existing:
            add("④", j["aid"], j["pid"], True, f"이미 있음 — 건너뜀 ({j['source_path']})", old_url=j["old_url"])
            continue
        try:
            wp.create_redirect(j["source_path"], j["target_url"])
            existing.add(key)
        except Exception as e:  # noqa: BLE001
            add("④", j["aid"], j["pid"], False, f"{type(e).__name__}: {e}"[:300], old_url=j["old_url"])
            return fail("④", f"④ 301 만들기 실패({j['source_path']}) — {e}"[:400])
        add("④", j["aid"], j["pid"], True, f"301 만듦 {j['source_path']} → {unquote(j['target_url'])}", old_url=j["old_url"])

    # ⑤ 실제로 요청해서 확인
    bad_checks = []
    for j in jobs:
        check_url = j["old_url"] or (plan_site_base(plan, j) + quote(j["source_path"]))
        ok, detail = wp.check_redirect(check_url, j["target_url"])
        add("⑤", j["aid"], j["pid"], ok, ("✅ " if ok else "❌ ") + detail, old_url=check_url)
        if not ok:
            bad_checks.append(j["source_path"])
    if bad_checks:
        return fail("⑤", "⑤ 301 확인 ❌ — 다음 주소가 합칠 글로 이동하지 않습니다: " + ", ".join(bad_checks))

    res.update(ok=True, stage="완료", message="묶음 정리 완료", undo="")
    log("bundle_result", ok=True, stage="완료", detail=f"뺀 글 {len(captured)}편")
    return res


def plan_site_base(plan: dict, job: dict) -> str:
    """추가 301(앱 기록에 없는 글)을 확인할 때 쓸 사이트 주소(합칠 글 주소의 앞부분)."""
    p = urlparse(job["target_url"])
    return f"{p.scheme}://{p.netloc}"

