"""
static/generated 이미지 정리 도구 (앱 버튼 없음 — 터미널에서만 실행).

세 가지 일을 합니다. 기본 실행은 전부 "보고서만" 만들고, 파일·데이터는 건드리지 않습니다.

  1) 저장본 주소 바꿔 두기 (--relink)
       발행된 워드프레스 글(혜택주머니 / 싼세계 / 워드프레스 SHOP (OLD))의 저장본(platform_final["wordpress"])에 남은
       /static/generated/ 경로를, 워드프레스 미디어 라이브러리에서 같은 파일을 찾아(조회 GET만) 실제 주소로 바꿉니다.
       --apply를 붙였을 때만 articles*.json에 저장합니다(저장 전에 백업). 못 찾은 것은 그대로 두고 "보류"로 표시합니다.
       ※ 워드프레스에는 조회(GET)만 합니다. 업로드·글 수정은 하지 않습니다.

  2) 정리 목록 보고서 (기본)
       static/generated 아래 모든 파일을 A~E 다섯 묶음으로 나눠 Claude outputs/image_cleanup_report.html 로 보여 줍니다.
         A 어느 글에도 연결 안 된 파일(실패 캡처 포함)      → 옮기기 대상
         B 미발행 글 / 예약일이 아직 안 지난 글의 이미지      → 항상 보존
         C 네이버·네이버 SHOP (OLD) 글, 발행 후 --days일 초과 → 옮기기 대상
         D 워드프레스 글, 발행 후 --days일 초과, 저장본에 그 파일의 로컬 경로가 없는 것 → 옮기기 대상
         E 그 밖(보류)

  3) 옮기기 (--apply --move-to 폴더)
       A·C·D만 지정한 폴더로 옮기고(원래 폴더 구조 유지, 삭제 없음) image_cleanup_log_날짜.json 을 남깁니다.
       되돌리기: --restore 로그파일

예)
  python image_cleanup.py                                  # 정리 목록 보고서만
  python image_cleanup.py --relink                         # 주소 바꾸기 보고서만(워드프레스 조회 GET)
  python image_cleanup.py --relink --apply                 # 찾은 것만 저장
  python image_cleanup.py --apply --move-to C:\\local-app-image-archive
  python image_cleanup.py --restore "Claude outputs\\image_cleanup_log_20261011.json"
테스트용 옵션: --root(데이터·이미지 폴더를 사본으로 바꿈), --now YYYY-MM-DD(기준 날짜)
"""
import argparse
import html
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PUBLISH_TIME = "09:00"  # app.py와 같은 가정: 예약 시각이 없으면 09:00
FAILURE_DIRS = ("naver_browser_failures", "wp_browser_failures")

# app.CHANNELS 중 이미지가 있는 채널만 옮겨 적었습니다 (앱을 불러오지 않고도 보고서를 만들기 위함).
# kind: "wordpress"(워드프레스 블로그) / "naver"(네이버 블로그). 티스토리는 데이터 파일이 아직 없습니다.
CHANNELS = [
    {"id": "longterm_wp", "label": "혜택주머니", "articles": "articles.json", "queue": "content_queue.json", "kind": "wordpress"},
    {"id": "wp_tips", "label": "싼세계", "articles": "articles_wp_tips.json", "queue": "content_queue_wp_tips.json", "kind": "wordpress"},
    {"id": "shop_wp", "label": "워드프레스 SHOP (OLD)", "articles": "articles_shop_wp.json", "queue": "content_queue_shop_wp.json", "kind": "wordpress"},
    {"id": "naver_tips", "label": "네이버", "articles": "articles_naver_tips.json", "queue": "content_queue_naver_tips.json", "kind": "naver"},
    {"id": "shop_naver", "label": "네이버 SHOP (OLD)", "articles": "articles_shop_naver.json", "queue": "content_queue_shop_naver.json", "kind": "naver"},
    {"id": "radar_naver", "label": "생활레이더", "articles": "articles_radar_naver.json", "queue": "content_queue_radar_naver.json", "kind": "naver"},
]
RELINK_CHANNELS = ("longterm_wp", "wp_tips", "shop_wp")  # --relink 대상
BUNDLES = {
    "A": "어느 글에도 연결 안 된 파일 (실패 캡처 포함)",
    "B": "미발행 글 / 예약일이 아직 안 지난 글의 이미지 (항상 보존)",
    "C": "네이버·네이버 SHOP (OLD) — 발행일이 기준 일수를 넘게 지난 글의 이미지",
    "D": "워드프레스 블로그 — 발행일이 기준 일수를 넘게 지난 글의 이미지 (저장본에 로컬 경로 없음)",
    "E": "그 밖 (보류)",
}
MOVE_BUNDLES = ("A", "C", "D")


# ---------- 공통 ----------

def _norm(text: str) -> str:
    """경로 비교용: 역슬래시→슬래시, 유니코드 NFC, 소문자."""
    return unicodedata.normalize("NFC", text.replace("\\", "/")).casefold()


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json_atomic(path: str, data) -> None:
    """app.py의 _write_json_atomic과 같은 형식(indent=2, 한글 그대로)으로 안전하게 씁니다."""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.1f}"  # 1MB = 100만 바이트 (탐색기·"690MB" 같은 어림값과 맞춤)


def _backup(path: str, label: str, stamp: str) -> str:
    """파일명.backup_before_<작업명>_<날짜>.확장자 로 같은 폴더에 백업합니다. 이미 있고 내용이 같으면 그대로 두고, 다르면 시각을 붙입니다."""
    base, ext = os.path.splitext(path)
    target = f"{base}.backup_before_{label}_{stamp}{ext}"
    if os.path.exists(target):
        with open(path, "rb") as a, open(target, "rb") as b:
            if a.read() == b.read():
                return target
        target = f"{base}.backup_before_{label}_{stamp}_{datetime.now().strftime('%H%M%S')}{ext}"
    shutil.copy2(path, target)
    return target


def _publish_dt(date_str, time_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(f"{str(date_str)[:10]} {(time_str or DEFAULT_PUBLISH_TIME)[:5]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _blob(obj) -> str:
    """객체 안의 모든 문자열을 경로 비교용으로 정규화해 하나로 합칩니다(퍼센트 인코딩된 주소도 풀어서 함께 넣음)."""
    parts = []
    for s in _iter_strings(obj):
        if "generated" not in s.lower():
            continue
        parts.append(_norm(s))
        if "%" in s:
            parts.append(_norm(urllib.parse.unquote(s)))
    return "\n".join(parts)


def _title_of(article: dict, platform: str, queue_titles: dict, aid: str) -> str:
    t = (article.get("platform_title") or {}).get(platform) or queue_titles.get(aid) or aid
    return t.strip().splitlines()[0][:120] if t else aid


# ---------- 디스크·데이터 읽기 ----------

def scan_disk(static_gen: str) -> list[dict]:
    files = []
    for dp, _dn, fn in os.walk(static_gen):
        for name in fn:
            full = os.path.join(dp, name)
            if name.lower().endswith(".tmp") or name.lower() in ("desktop.ini", "thumbs.db"):
                continue  # 구글 드라이브·윈도우 보조 파일은 정리 대상이 아님
            rel = os.path.relpath(full, static_gen).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            files.append({"rel": rel, "full": full, "size": size,
                          "needle": "static/generated/" + _norm(rel)})
    files.sort(key=lambda f: f["rel"])
    return files


def load_articles(root: str) -> list[dict]:
    """채널별 글 데이터를 읽어 [{channel, aid, title, state, ...}]로 만듭니다(읽기 전용)."""
    out = []
    for ch in CHANNELS:
        path = os.path.join(root, ch["articles"])
        if not os.path.exists(path):
            continue
        data = _read_json(path)
        qpath = os.path.join(root, ch["queue"])
        queue_titles = {}
        if os.path.exists(qpath):
            try:
                queue_titles = {it.get("id"): it.get("keyword") for it in _read_json(qpath) if it.get("id")}
            except (json.JSONDecodeError, OSError, AttributeError):
                queue_titles = {}
        plat = "wordpress" if ch["kind"] == "wordpress" else "naver"
        for aid, art in data.items():
            url = (art.get("platform_published_url") or {}).get(plat) or ""
            at = (art.get("platform_published_at") or {}).get(plat)
            tm = (art.get("platform_scheduled_time") or {}).get(plat)
            final = (art.get("platform_final") or {}).get(plat) or ""
            out.append({
                "channel": ch, "aid": aid, "platform": plat,
                "title": _title_of(art, plat, queue_titles, aid),
                "url": url, "published_at": at, "dt": _publish_dt(at, tm),
                "blob": _blob(art),          # 이 글 데이터 전체(표지·본문·배경·이미지 풀·카드뉴스 …)
                "final_blob": _blob(final),  # 저장본(최종본) 본문만
            })
    return out


# ---------- 분류 ----------

def classify(files: list[dict], articles: list[dict], days: int, now: datetime) -> dict:
    """각 파일에 bundle(A~E)·reason·owners를 붙이고 {묶음: [파일...]}을 돌려줍니다."""
    today = now.date()
    for f in files:
        f["owners"] = []
        f["reasons"] = []
    for art in articles:
        for f in files:
            if f["needle"] in art["blob"]:
                f["owners"].append(art)

    def owner_state(art, f):
        """('B'|'C'|'D'|'E', 이유)"""
        kind = art["channel"]["kind"]
        if not art["url"]:
            return "B", "미발행 글"
        dt = art["dt"]
        if dt is None:
            return "E", "발행일을 알 수 없음"
        if dt > now:
            return "B", f"예약일({dt:%Y-%m-%d %H:%M})이 아직 안 지남"
        age = (today - dt.date()).days
        if age <= days:
            return "E", f"발행 후 {age}일 (기준 {days}일 이내)"
        if kind == "naver":
            return "C", f"발행 후 {age}일"
        if f["needle"] in art["final_blob"]:
            return "E", f"발행 후 {age}일이지만 저장본에 로컬 경로가 남아 있음 (먼저 --relink 필요)"
        return "D", f"발행 후 {age}일, 저장본에 로컬 경로 없음"

    groups = defaultdict(list)
    for f in files:
        top = f["rel"].split("/")[0]
        if top in FAILURE_DIRS:
            f["bundle"], f["reasons"] = "A", ["실패 캡처"]
        elif not f["owners"]:
            f["bundle"], f["reasons"] = "A", ["어느 글에도 연결 안 됨"]
        else:
            states = [owner_state(a, f) for a in f["owners"]]
            kinds = {s for s, _ in states}
            if "B" in kinds:
                f["bundle"] = "B"
            elif kinds == {"C"} or kinds == {"D"}:
                f["bundle"] = kinds.pop()
            else:
                f["bundle"] = "E"
            f["reasons"] = sorted({r for s, r in states})
        groups[f["bundle"]].append(f)
    return groups


# ---------- 보고서(HTML) ----------

def _bundle_summary_rows(groups) -> list[tuple]:
    return [(b, BUNDLES[b], len(groups.get(b, [])), sum(f["size"] for f in groups.get(b, []))) for b in "ABCDE"]


def write_report(path: str, groups: dict, files: list[dict], days: int, now: datetime, root: str) -> None:
    esc = html.escape
    total_n, total_sz = len(files), sum(f["size"] for f in files)
    rows = _bundle_summary_rows(groups)
    out = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>이미지 정리 보고서</title>",
        "<style>body{font-family:'Malgun Gothic',sans-serif;margin:24px;color:#222;line-height:1.5}"
        "table{border-collapse:collapse;margin:12px 0}td,th{border:1px solid #ccc;padding:6px 10px;text-align:left}"
        "th{background:#f2f2f2}.num{text-align:right}details{margin:6px 0 6px 12px}summary{cursor:pointer}"
        ".sz{color:#666}h2{margin-top:32px;border-bottom:2px solid #ddd}.note{background:#fff8e1;padding:10px 14px;border-radius:6px}"
        "code{background:#f5f5f5;padding:1px 4px}</style></head><body>",
        "<h1>이미지 정리 보고서</h1>",
        f"<p>만든 시각: {now:%Y-%m-%d %H:%M} · 기준 일수: <b>{days}일</b> · 대상 폴더: <code>{esc(os.path.join(root, 'static', 'generated'))}</code></p>",
        "<p class='note'>이 보고서는 <b>읽기만</b> 해서 만들었습니다 — 파일을 옮기거나 지우지 않았고, 워드프레스에도 접속하지 않았습니다.</p>",
        f"<h2>요약 — 전체 {total_n}개 · {_mb(total_sz)} MB</h2>",
        "<table><tr><th>묶음</th><th>설명</th><th>개수</th><th>MB</th><th>옮기기</th></tr>",
    ]
    for b, desc, n, sz in rows:
        out.append(f"<tr><td><b>{b}</b></td><td>{esc(desc)}</td><td class='num'>{n}</td><td class='num'>{_mb(sz)}</td>"
                   f"<td>{'옮김 대상' if b in MOVE_BUNDLES else '옮기지 않음'}</td></tr>")
    move_n = sum(len(groups.get(b, [])) for b in MOVE_BUNDLES)
    move_sz = sum(f["size"] for b in MOVE_BUNDLES for f in groups.get(b, []))
    out.append(f"<tr><td colspan='2'><b>A+C+D (옮김 대상 합계)</b></td><td class='num'>{move_n}</td><td class='num'>{_mb(move_sz)}</td><td></td></tr></table>")

    reasons = Counter()
    for f in groups.get("E", []):
        for r in f["reasons"]:
            reasons[re.sub(r"\d+일", "N일", r)] += 1
    if reasons:
        out.append("<h3>E(보류) 이유별 개수</h3><table><tr><th>이유</th><th>파일 수</th></tr>")
        out += [f"<tr><td>{esc(r)}</td><td class='num'>{n}</td></tr>" for r, n in reasons.most_common()]
        out.append("</table>")

    for b in "ABCDE":
        fl = groups.get(b, [])
        out.append(f"<h2>{b}. {esc(BUNDLES[b])} — {len(fl)}개 · {_mb(sum(f['size'] for f in fl))} MB</h2>")
        if b in ("C", "D"):
            out.append("<p class='note'>안내: 옮기면 앱 편집 화면의 미리보기와 '표지 다시 만들기'는 쓸 수 없게 됩니다. "
                       "블로그에 올라간 글은 그대로입니다. 필요하면 --restore 로 되돌릴 수 있습니다.</p>")
        if not fl:
            out.append("<p>없음</p>")
            continue
        if b == "A":
            by_dir = defaultdict(list)
            for f in fl:
                by_dir[f["rel"].rsplit("/", 1)[0] if "/" in f["rel"] else "(static/generated 바로 아래)"].append(f)
            for d, items in sorted(by_dir.items()):
                out.append(f"<details><summary>{esc(d)} — {len(items)}개 · {_mb(sum(i['size'] for i in items))} MB</summary><ul>")
                out += [f"<li>{esc(i['rel'].rsplit('/', 1)[-1])} <span class='sz'>({i['size'] / 1024:.0f} KB · {esc(', '.join(i['reasons']))})</span></li>" for i in items]
                out.append("</ul></details>")
            continue
        # 블로그 → 글 제목별 (한 파일이 여러 글에 걸리면 각 글에 모두 표시)
        by_blog = defaultdict(lambda: defaultdict(list))
        for f in fl:
            for a in f["owners"]:
                by_blog[a["channel"]["label"]][(a["aid"], a["title"], a["published_at"] or "미발행")].append(f)
        for blog, arts in by_blog.items():
            n = len({f["rel"] for items in arts.values() for f in items})
            out.append(f"<details open><summary><b>{esc(blog)}</b> — 글 {len(arts)}개 · 파일 {n}개</summary>")
            for (aid, title, pub), items in sorted(arts.items(), key=lambda kv: kv[0][2]):
                out.append(f"<details><summary>{esc(title)} <span class='sz'>(발행 {esc(str(pub))} · {len(items)}개 · "
                           f"{_mb(sum(i['size'] for i in items))} MB)</span></summary><ul>")
                out += [f"<li>{esc(i['rel'])} <span class='sz'>({i['size'] / 1024:.0f} KB"
                        f"{' · ' + esc(', '.join(i['reasons'])) if b == 'E' else ''})</span></li>" for i in items]
                out.append("</ul></details>")
            out.append("</details>")
    out.append("</body></html>")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def print_summary(groups, files) -> None:
    print(f"전체 {len(files)}개 {_mb(sum(f['size'] for f in files))}MB")
    for b, desc, n, sz in _bundle_summary_rows(groups):
        print(f"  {b}: {n:4d}개 {_mb(sz):>8}MB  {desc}")
    move_n = sum(len(groups.get(b, [])) for b in MOVE_BUNDLES)
    move_sz = sum(f["size"] for b in MOVE_BUNDLES for f in groups.get(b, []))
    print(f"  A+C+D(옮김 대상): {move_n}개 {_mb(move_sz)}MB")


# ---------- 옮기기 / 되돌리기 ----------

def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.normcase(os.path.realpath(child)), os.path.normcase(os.path.realpath(parent))
    return child == parent or child.startswith(parent + os.sep)


def move_files(groups: dict, root: str, move_to: str, log_dir: str, stamp: str) -> str:
    static_gen = os.path.join(root, "static", "generated")
    if _inside(move_to, root):
        raise SystemExit(f"오류: 옮길 폴더가 작업 폴더 안입니다. 구글 드라이브 밖 폴더를 지정해 주세요: {move_to}")
    if "내 드라이브" in os.path.realpath(move_to) or "my drive" in os.path.realpath(move_to).lower():
        raise SystemExit(f"오류: 옮길 폴더가 구글 드라이브 안으로 보입니다. 드라이브 밖 폴더를 지정해 주세요: {move_to}")
    # 모르는 articles*.json(예: 나중에 생긴 티스토리 데이터)이 있으면, 그 글의 이미지가 "연결 안 됨"으로 잘못 분류될 수 있어 멈춥니다.
    known = {c["articles"] for c in CHANNELS}
    unknown = sorted(n for n in os.listdir(root) if re.fullmatch(r"articles.*\.json", n) and ".backup_before_" not in n and n not in known)
    if unknown:
        raise SystemExit(f"오류: 이 도구가 모르는 글 데이터 파일이 있어 옮기기를 멈췄습니다(이미지가 연결 안 된 것으로 오판할 수 있음): {', '.join(unknown)}")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"image_cleanup_log_{stamp}.json")
    n = 2
    while os.path.exists(log_path):  # 같은 날 두 번째 실행이 앞 로그를 덮어쓰지 않게
        log_path = os.path.join(log_dir, f"image_cleanup_log_{stamp}_{n}.json")
        n += 1
    moved, skipped = [], []
    log = {"created": datetime.now().isoformat(timespec="seconds"), "root": root, "move_to": move_to,
           "moved": moved, "skipped": skipped}

    def _save_log():
        with open(log_path, "w", encoding="utf-8") as fh:
            json.dump(log, fh, ensure_ascii=False, indent=2)

    try:
        for b in MOVE_BUNDLES:
            for f in groups.get(b, []):
                src = f["full"]
                dst = os.path.join(move_to, "static", "generated", *f["rel"].split("/"))
                if os.path.exists(dst):
                    skipped.append({"rel": f["rel"], "bundle": b, "reason": "옮길 자리에 같은 이름의 파일이 이미 있음"})
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                if os.path.getsize(dst) != os.path.getsize(src):
                    os.remove(dst)
                    skipped.append({"rel": f["rel"], "bundle": b, "reason": "복사본 크기가 달라 중단(원본 그대로)"})
                    continue
                os.remove(src)
                moved.append({"rel": f["rel"], "bundle": b, "size": f["size"], "from": src, "to": dst})
                if len(moved) % 25 == 0:
                    _save_log()  # 중간에 멈춰도 --restore 할 수 있게 주기적으로 저장
    finally:
        _save_log()  # 오류·중단이어도 지금까지 옮긴 목록은 반드시 남김
    shutil.copy2(log_path, os.path.join(move_to, os.path.basename(log_path)))
    print(f"옮김 {len(moved)}개 {_mb(sum(m['size'] for m in moved))}MB / 건너뜀 {len(skipped)}개")
    print(f"로그: {log_path}")
    return log_path


def restore_files(log_path: str) -> None:
    log = _read_json(log_path)
    back, missing, conflict = 0, 0, 0
    for m in log["moved"]:
        if not os.path.exists(m["to"]):
            missing += 1
            continue
        if os.path.exists(m["from"]):
            conflict += 1  # 원래 자리에 이미 파일이 있으면 덮어쓰지 않습니다
            continue
        os.makedirs(os.path.dirname(m["from"]), exist_ok=True)
        shutil.copy2(m["to"], m["from"])
        if os.path.getsize(m["from"]) != os.path.getsize(m["to"]):
            os.remove(m["from"])
            conflict += 1
            continue
        os.remove(m["to"])
        back += 1
    print(f"되돌림 {back}개 / 보관 폴더에 없음 {missing}개 / 원래 자리에 이미 파일이 있어 건너뜀 {conflict}개")


# ---------- 저장본 주소 바꿔 두기 (--relink) ----------

def relink(root: str, apply: bool, report_path: str, stamp: str) -> None:
    """발행된 워드프레스 글의 저장본에 남은 /static/generated/ 주소를 미디어 라이브러리(GET)에서 찾아 실제 주소로 바꿉니다."""
    os.chdir(PROJECT_DIR)  # app.py(와 config)는 실행 위치 기준 상대경로를 씁니다
    sys.path.insert(0, PROJECT_DIR)
    import app as webapp  # noqa: E402 — 조회 함수(_match_local_images_in_library)와 사이트 접속 정보를 앱 것 그대로 씁니다
    import wp_publisher  # noqa: E402

    caches: dict = {}  # 사이트(주소)별 미디어 라이브러리 목록 캐시
    changed_by_file: dict = {}
    rows = []  # 보고서용
    for ch in CHANNELS:
        if ch["id"] not in RELINK_CHANNELS:
            continue
        path = os.path.join(root, ch["articles"])
        if not os.path.exists(path):
            continue
        site = webapp.CHANNELS[ch["id"]].get("wordpress_site")
        wp_publisher.set_site_context(site)
        cache = caches.setdefault((site or {}).get("url") or "default", {})
        inject_id = ch["id"] in webapp.SHOP_WP_SITE_CHANNELS  # 숍 계열은 재동기화와 같이 이미지 블록에 미디어 ID도 심습니다
        data = _read_json(path)
        queue_titles = {}
        qpath = os.path.join(root, ch["queue"])
        if os.path.exists(qpath):
            queue_titles = {it.get("id"): it.get("keyword") for it in _read_json(qpath) if it.get("id")}
        for aid, art in data.items():
            if not (art.get("platform_published_url") or {}).get("wordpress"):
                continue
            final = (art.get("platform_final") or {}).get("wordpress") or ""
            if "/static/generated/" not in final:
                continue
            matches = webapp._match_local_images_in_library(final, cache)  # 조회(GET)만
            found = {m["src"]: m["hits"][0] for m in matches if m["hits"]}
            left = [m["src"] for m in matches if not m["hits"]]
            rows.append({"channel": ch["label"], "aid": aid, "title": _title_of(art, "wordpress", queue_titles, aid),
                         "found": {s: h[1] for s, h in found.items()}, "left": left})
            if found:
                changed_by_file.setdefault(ch["articles"], {})[aid] = {s: (h[0], h[1]) for s, h in found.items()}

    def _apply_map(final: str, mapping: dict, inject: bool) -> str:
        for src, (media_id, url) in mapping.items():
            if inject:
                final = wp_publisher._inject_media_id_into_image_block(final, src, url, media_id)
            final = final.replace(f'src="{src}"', f'src="{url}"')
        return final

    n_art = sum(1 for r in rows if r["found"])
    n_img = sum(len(r["found"]) for r in rows)
    n_left = sum(len(r["left"]) for r in rows)
    print(f"저장본에 로컬 경로가 남은 발행 글 {len(rows)}개 중 → 바뀔 글 {n_art}개 / 바뀔 이미지 {n_img}장 / 못 찾아 보류 {n_left}장")

    if apply:
        for fname, per_article in changed_by_file.items():
            ch = next(c for c in CHANNELS if c["articles"] == fname)
            path = os.path.join(root, fname)
            print(f"백업: {_backup(path, 'image_cleanup', stamp)}")
            fresh = _read_json(path)  # 저장 직전에 다시 읽어, 그 사이 앱에서 바뀐 다른 내용은 건드리지 않습니다
            inject = ch["id"] in webapp.SHOP_WP_SITE_CHANNELS
            for aid, mapping in per_article.items():
                art = fresh.get(aid)
                if not art:
                    continue
                cur = (art.get("platform_final") or {}).get("wordpress") or ""
                new = _apply_map(cur, mapping, inject)
                if new != cur:
                    art["platform_final"]["wordpress"] = new
            _write_json_atomic(path, fresh)
            print(f"저장: {path} (글 {len(per_article)}개)")
    else:
        print("(보고서만 — 저장하지 않았습니다. 저장하려면 --apply)")

    _write_relink_report(report_path, rows, apply)
    print(f"보고서: {report_path}")
    for r in rows:
        for s in r["left"]:
            print(f"  보류: [{r['channel']}] {r['title'][:40]} — {s}")


def _write_relink_report(path: str, rows: list[dict], applied: bool) -> None:
    esc = html.escape
    n_art = sum(1 for r in rows if r["found"])
    out = ["<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>저장본 주소 바꾸기 보고서</title>"
           "<style>body{font-family:'Malgun Gothic',sans-serif;margin:24px}table{border-collapse:collapse}"
           "td,th{border:1px solid #ccc;padding:5px 9px;text-align:left;font-size:13px}th{background:#f2f2f2}.hold{color:#b00}</style></head><body>",
           "<h1>저장본 주소 바꾸기 보고서</h1>",
           f"<p>{'<b>저장함</b>' if applied else '<b>보고서만 (저장 안 함)</b>'} · 바뀐(바뀔) 글 {n_art}개 · 이미지 "
           f"{sum(len(r['found']) for r in rows)}장 · 보류 {sum(len(r['left']) for r in rows)}장 · 워드프레스에는 조회(GET)만 했습니다.</p>",
           "<table><tr><th>블로그</th><th>글</th><th>바꾼 이미지</th><th>보류(못 찾음)</th></tr>"]
    for r in rows:
        out.append(f"<tr><td>{esc(r['channel'])}</td><td>{esc(r['title'])}</td><td>{len(r['found'])}</td>"
                   f"<td class='hold'>{'<br>'.join(esc(s) for s in r['left']) or ''}</td></tr>")
    out.append("</table></body></html>")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


# ---------- 실행 ----------

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="static/generated 이미지 정리 (기본 실행 = 보고서만)")
    ap.add_argument("--days", type=int, default=30, help="발행 후 이 일수가 넘게 지난 글을 C·D 대상으로 (기본 30)")
    ap.add_argument("--relink", action="store_true", help="저장본의 로컬 이미지 주소를 워드프레스 미디어 라이브러리(조회만)에서 찾아 바꿈")
    ap.add_argument("--apply", action="store_true", help="실제로 저장/이동 (없으면 보고서만)")
    ap.add_argument("--move-to", metavar="폴더", help="--apply와 함께: A·C·D 파일을 이 폴더로 옮김(구글 드라이브 밖)")
    ap.add_argument("--restore", metavar="로그파일", help="옮긴 파일을 원래 자리로 되돌림")
    ap.add_argument("--root", default=PROJECT_DIR, help="(테스트용) articles*.json과 static/generated가 있는 폴더")
    ap.add_argument("--now", help="(테스트용) 기준 날짜 YYYY-MM-DD")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    now = datetime.strptime(args.now, "%Y-%m-%d").replace(hour=12) if args.now else datetime.now()
    stamp = now.strftime("%Y%m%d")
    out_dir = os.path.join(root, "Claude outputs")

    if args.restore:
        restore_files(args.restore)
        return 0
    if args.relink:
        if args.move_to:
            ap.error("--relink 와 --move-to 는 함께 쓸 수 없습니다 (먼저 주소 바꾸기, 그다음 옮기기).")
        relink(root, args.apply, os.path.join(out_dir, "image_cleanup_relink_report.html"), stamp)
        return 0
    if args.apply and not args.move_to:
        ap.error("--apply 는 --relink(주소 저장) 또는 --move-to 폴더(파일 옮기기)와 함께 써야 합니다.")
    if args.move_to and not args.apply:
        ap.error("--move-to 는 --apply 와 함께 써야 실제로 옮깁니다 (--apply 없이는 보고서만 만듭니다).")

    files = scan_disk(os.path.join(root, "static", "generated"))
    articles = load_articles(root)
    groups = classify(files, articles, args.days, now)
    report = os.path.join(out_dir, "image_cleanup_report.html")
    write_report(report, groups, files, args.days, now, root)
    print_summary(groups, files)
    print(f"보고서: {report}")
    if args.apply and args.move_to:
        move_files(groups, root, os.path.abspath(args.move_to), out_dir, stamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
