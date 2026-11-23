"""N살림노트(naver_tips) 전용: 네이버 올리기 편하게(번호 붙은 업로드 폴더·📷 자리표시·상품 블록·최종본 맨 위 정보 줄).
radar_screen·radar_images(데스크피커 전용)는 도움 함수를 부르기만 하고 그 파일 안의 코드는 바꾸지 않는다.
앱 데이터(articles*.json·content_queue*.json)는 읽기만 한다 — 저장은 app.py 의 기존 흐름(set_article)이 한다.
이미지 생성 API·Claude 호출·네이버 접속은 없다(주소 채우기의 RSS 읽기는 salim_urlfill.py)."""
import glob
import html as _html
import json
import os
import re
import shutil
from datetime import datetime
from urllib.parse import quote

import shop_formatter

CANDIDATES_ROOT = os.path.join("pipeline", "candidates_run")
UPLOAD_DIR = "naver_upload"
SIDECAR = "salim_upload.json"            # 사진 폴더 안: 번호·이름·원본 경로 목록(최종본 화면·다운로드 목록용)
BANNER_ORIG = os.path.join("static", "radar", "banner_naver_price.png")   # 데스크피커 배너(원본은 그대로 둠)
BANNER_COPY = os.path.join("static", "salim", "banner_naver_price.png")   # 살림노트가 쓰는 복사본
PRODUCT_PHOTO_SIZE = 800
TIME_DOC = os.path.join("docs", "살림노트", "원고작업지시_Claude-Code.md")  # 3장 표: 번호·발행 날짜·시각·키워드
OVERRIDE_DIR = None  # 시험용: 지정하면 업로드 폴더·별점 그림을 이 폴더에만 만든다(글 사진 폴더는 안 건드림)

_MARK_STYLE = "text-align:center; color:#1565C0; font-weight:bold; font-size:18px;"
_TOKEN_RE = re.compile(r'<img\b[^>]*>(?:<br>)?|<p>\{\{SM([PSB]):(\d+)\}\}</p>')
_MARK_RE = re.compile(r"<p[^>]*>📷 ([^<\s]+)</p>")
WARN_MARK = "<!--SM:WARN:%s-->"
INFO_MARK = "<!--SM:INFO:%s-->"


# ── 사진 폴더 ─────────────────────────────────────────────
def photo_dir(article):
    """이 글의 사진 폴더(상대 경로, 슬래시 없이 시작). 표지·본문 사진 경로에서 알아낸다. 없으면 ''."""
    cands = [((article.get("covers") or {}).get("naver") or "")]
    cands += list((article.get("platform_body_images") or {}).get("naver") or [])
    for c in cands:
        if c:
            return os.path.dirname(c.lstrip("/"))
    return ""


def upload_base(article):
    return OVERRIDE_DIR or photo_dir(article)


# ── 상품 찾기 ─────────────────────────────────────────────
def _links_items():
    """pipeline/candidates_run/*naver_tips*/links.json 의 (실행 폴더 이름, 항목) 목록. 읽기만."""
    out = []
    for p in sorted(glob.glob(os.path.join(CANDIDATES_ROOT, "*naver_tips*", "links.json"))):
        folder = os.path.basename(os.path.dirname(p))
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        for it in (d.get("items") or []) if isinstance(d, dict) else []:
            out.append((folder, it))
    return out


def find_product(link, items=None):
    """원고 '링크:' 주소를 links.json 의 naver_link 와 글자 그대로 맞춰 상품을 찾는다. 반환 {pid, folder, name} 또는 None.
    같은 주소가 여러 실행 폴더에 있으면 research 파일이 있는 폴더를 앞세운다."""
    link = (link or "").strip()
    if not link:
        return None
    hits = []
    for folder, it in (items if items is not None else _links_items()):
        if (it.get("naver_link") or "").strip() == link and it.get("product_id"):
            hits.append({"pid": str(it["product_id"]), "folder": folder, "name": it.get("name", "")})
    for h in hits:
        if os.path.isfile(os.path.join(CANDIDATES_ROOT, h["folder"], "research", h["pid"] + ".md")):
            return h
    return hits[0] if hits else None


def rep_photo(pid, folder):
    """대표 사진: research/_img/<상품id>/ 의 2번째 사진(02.*), 없으면 01_rep.*. 없으면 ''."""
    d = os.path.join(CANDIDATES_ROOT, folder, "research", "_img", pid)
    for pat in ("02.*", "01_rep.*"):
        for p in sorted(glob.glob(os.path.join(d, pat))):
            if os.path.splitext(p)[1].lower() in (".jpg", ".jpeg", ".png", ".webp"):
                return p
    return ""


def stats_for(pid, folder):
    """research/<상품id>.md 의 평점·리뷰 수·수집 날짜(radar_screen.research_stats 를 그대로 부름). 반환 (값 dict 또는 None, 빠진 항목)."""
    import radar_screen
    return radar_screen.research_stats(pid, folder)


def cover_color(article):
    """메모의 '표지 색: 이름' 값(없으면 '')."""
    memo = (article.get("platform_metadata") or {}).get("naver") or ""
    m = re.search(r"^[ \t]*[-*•]?[ \t]*\**표지 색\**[ \t]*[:：][ \t]*(\S+)", memo, re.M)
    return m.group(1).strip() if m else ""


def ensure_banner():
    """static/salim/banner_naver_price.png 가 없으면 데스크피커 배너를 복사해 만든다(원본은 그대로). 반환 경로 또는 ''."""
    if os.path.isfile(BANNER_COPY):
        return BANNER_COPY
    if not os.path.isfile(BANNER_ORIG):
        return ""
    os.makedirs(os.path.dirname(BANNER_COPY), exist_ok=True)
    shutil.copyfile(BANNER_ORIG, BANNER_COPY)
    return BANNER_COPY


# ── 상품 블록 계획 ───────────────────────────────────────────
def plan_products(raw):
    """원고의 '링크:' 가 있는 ## 섹션(상품 섹션, shop_formatter.parse_sections 와 같은 순서)마다 계획 한 줄.
    [{n, heading, link, found(bool), pid, folder, name, photo, stats, warns[]}] — 파일은 만들지 않는다."""
    sections = [s for s in shop_formatter.parse_sections(raw)["sections"] if s["is_product"]]
    items = _links_items()
    out = []
    for n, s in enumerate(sections, 1):
        link = (s.get("link") or "").strip()
        row = {"n": n, "heading": s["heading"], "link": link, "found": False, "pid": "", "folder": "", "name": "",
               "photo": "", "stats": None, "warns": []}
        prod = find_product(link, items)
        if not prod:
            row["warns"].append(f"{n}번째 상품: 자료 없음")
        else:
            row.update(found=True, pid=prod["pid"], folder=prod["folder"], name=prod["name"])
            row["photo"] = rep_photo(prod["pid"], prod["folder"])
            if not row["photo"]:
                row["warns"].append(f"{n}번째 상품: 대표 사진 없음")
            try:
                stats, missing = stats_for(prod["pid"], prod["folder"])
            except Exception as e:  # noqa: BLE001
                stats, missing = None, ["읽기 실패: " + str(e)]
            if stats:
                row["stats"] = stats
            else:
                row["warns"].append(f"{n}번째 상품: 별점 상자 못 넣음 (" + ", ".join(missing) + " 없음)")
        out.append(row)
    return out


def blocks_for(plan, banner_ok):
    """shop_formatter(salim_blocks) 에 넘길 상품별 HTML: [📷 대표 사진] [📷 별점 상자] [📷 배너] 링크 줄 + 빈 줄. 번호는 finalize 가 매긴다."""
    gap = shop_formatter.NAVER_GAP
    blocks = []
    for row in plan:
        n = row["n"] - 1
        html = ""
        if row["photo"]:
            html += "<p>{{SMP:%d}}</p>" % n
        if row["stats"]:
            html += "<p>{{SMS:%d}}</p>" % n
        if row["link"] and shop_formatter.classify_link_value(row["link"]) != "empty":
            if banner_ok:
                html += "<p>{{SMB:%d}}</p>" % n
            html += f'<p style="text-align:left;">{_html.escape(row["link"], quote=True)}</p>'
        else:
            row["warns"].append(f"{row['n']}번째 상품: 링크 값이 비어 있어요")
        blocks.append(html + gap)
    return blocks


# ── 번호 매기기 + 업로드 폴더 ──────────────────────────────────
def _ext_of(path):
    try:
        import radar_screen
        return radar_screen.real_ext(path) or os.path.splitext(path)[1].lower() or ".jpg"
    except Exception:
        return os.path.splitext(path)[1].lower() or ".jpg"


def _mark(name):
    return f'<p style="{_MARK_STYLE}">📷 {_html.escape(name)}</p>'


def finalize(final, article, plan, banner_ok=True):
    """최종본 HTML 의 사진 자리(표지·본문 <img>, 상품 대표 사진·별점·배너 자리)를 나오는 순서대로 번호 매겨 '📷 NN_이름.jpg' 파란 한 줄로 바꾸고,
    naver_upload 폴더를 만든다. 반환 (최종본 HTML, info dict). 최종본 글자는 사진 자리 외에는 바꾸지 않는다.
    info: uploads[{name, src, kind}], warns[], folder, error."""
    cover = ((article.get("covers") or {}).get("naver") or "")
    body_set = set((article.get("platform_body_images") or {}).get("naver") or [])
    uploads, warns, state = [], [], {"body": 0}
    base = upload_base(article)

    def add(kind, src, label):
        if kind == "star":
            ext = ".png"
        else:
            ext = _ext_of(src) if (src and os.path.isfile(src.lstrip("/"))) else (os.path.splitext(src)[1] or ".jpg")
        name = f"{len(uploads) + 1:02d}_{label}{ext}"
        uploads.append({"name": name, "src": src, "kind": kind})
        return name

    def sub(m):
        if m.group(0).startswith("<img"):
            tag = m.group(0)
            sm = re.search(r'\bsrc="([^"]+)"', tag)
            src = _html.unescape(sm.group(1)) if sm else ""
            if "seo-cover-img" in tag and src and src == cover:
                return _mark(add("photo", src, "표지"))
            if src and src in body_set:
                state["body"] += 1
                return _mark(add("photo", src, f"본문{state['body']}"))
            return m.group(0)  # 알 수 없는 그림은 그대로 둔다
        kind, i = m.group(1), int(m.group(2))
        if kind == "P":
            return _mark(add("product", plan[i]["photo"], f"상품{i + 1}"))
        if kind == "S":
            nm = _mark(add("star", "", f"별점{i + 1}"))
            uploads[-1]["plan"] = i
            return nm
        return _mark(add("banner", BANNER_COPY, f"배너{i + 1}"))

    final = _TOKEN_RE.sub(sub, final)  # 번호만 먼저 매기고, 별점 그림 등 파일은 아래 _write_folder 에서 만든다
    info = {"uploads": uploads, "warns": warns, "folder": base, "error": ""}
    for row in plan:
        warns.extend(row["warns"])
    if not base:
        info["error"] = "사진 폴더를 알 수 없어 업로드 폴더를 만들지 못했어요(표지·본문 사진 경로가 없음)"
        return final, info
    try:
        _write_folder(uploads, base, plan, article, warns)
    except Exception as e:  # noqa: BLE001 — 폴더를 못 만들어도 최종본 글자는 돌려주고 이유를 알린다
        info["error"] = str(e)
    return final, info


def _write_folder(uploads, base, plan, article, warns):
    import radar_screen
    import salim_images
    os.makedirs(base, exist_ok=True)
    up = radar_screen._reset_upload_dir(base)  # 이 폴더 안의 것만 지우고, 바로가기·폴더 밖이면 멈춤
    accent, _ = salim_images.accent_rgb(cover_color(article))
    side = []
    for u in uploads:
        dst = os.path.join(up, u["name"])
        src = (u["src"] or "").lstrip("/") if u["kind"] != "banner" else u["src"]
        kind = u["kind"]
        if kind == "star":
            row = plan[u["plan"]]
            s = row["stats"]
            png = os.path.join(base, f"salim_star_{row['pid']}.png")  # 별점 상자 그림은 글 사진 폴더에만 저장
            salim_images.star_box_image(s["rating"], s["reviews"], s["date"], png, accent)
            src = png
            shutil.copyfile(png, dst)
        elif kind == "banner":
            radar_screen.banner_small_copy(src, dst)
        elif kind == "product":
            radar_screen.fit_square_copy(src, dst, PRODUCT_PHOTO_SIZE)
        else:
            if not os.path.isfile(src):
                warns.append(f"사진 파일 없음: {u['name']} ({u['src']})")
                continue
            shutil.copyfile(src, dst)
        side.append({"name": u["name"], "src": src.replace("\\", "/"), "kind": kind})
    with open(os.path.join(base, SIDECAR), "w", encoding="utf-8") as f:
        json.dump({"uploads": side, "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=1)


def append_marks(final, info, n_products):
    """최종본 끝에 눈에 안 보이는 표시(노란 경고·상품 수)를 붙인다. '-' '<' '>' 는 표시가 깨지지 않게 바꾼다."""
    warns = list(info["warns"])
    if info.get("error"):
        warns.append("업로드 폴더(naver_upload): " + info["error"])
    if warns:
        final += WARN_MARK % "|".join(re.sub(r"[-<>]", " ", w) for w in warns)
    final += INFO_MARK % f"products={n_products}"
    return final


def marks_from(final):
    """저장된 최종본 끝 표시 → (경고 목록, 상품 수 또는 None)."""
    m = re.search(r"<!--SM:WARN:([^>]*?)-->", final or "")
    warns = [w.strip() for w in m.group(1).split("|") if w.strip()] if m else []
    p = re.search(r"<!--SM:INFO:products=(\d+)-->", final or "")
    return warns, (int(p.group(1)) if p else None)


# ── 최종본 화면: 📷 줄 자리에 실제 사진 보여 주기 ────────────────────
def screen_html(final, base):
    """📷 줄 자리마다 naver_upload 안 같은 파일을 그림으로 보여 준다(복사용 최종본은 그대로). 파일이 없으면 빨간 '사진 없음'."""
    def sub(m):
        name = _html.unescape(m.group(1))
        fp = os.path.join(base, UPLOAD_DIR, name) if base else ""
        if not fp or not os.path.isfile(fp):
            return f'<p style="text-align:center; color:#D32F2F; font-weight:bold; font-size:16px;">사진 없음: {_html.escape(name)}</p>'
        url = "/" + quote(fp.replace("\\", "/").lstrip("/")) + f"?v={int(os.path.getmtime(fp))}"
        if "별점" in name or "배너" in name:
            style = "max-width:100%; height:auto; display:block; margin:0 auto;"
        else:
            style = "width:100%; max-width:720px; height:auto; display:block; margin:0 auto;"
        return (f'<div style="text-align:center; margin:12px 0;"><img src="{_html.escape(url, quote=True)}" alt="{_html.escape(name, quote=True)}" style="{style}">'
                f'<div style="color:#999999; font-size:12px; margin-top:4px;">{_html.escape(name)}</div></div>')
    return _MARK_RE.sub(sub, final or "")


def listing(base):
    """사진 폴더의 salim_upload.json 에서 [{name, path(웹 주소), ok}] 목록(최종본 화면 [이미지 다운로드] 용). 없으면 None."""
    if not base:
        return None
    p = os.path.join(base, SIDECAR)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    out = []
    for u in d.get("uploads") or []:
        fp = os.path.join(base, UPLOAD_DIR, u["name"])
        out.append({"label": u["name"].rsplit(".", 1)[0], "name": u["name"], "path": "/" + quote(fp.replace("\\", "/").lstrip("/")), "ok": os.path.isfile(fp)})
    return out or None


# ── 업로드 폴더 열기(탐색기) ────────────────────────────────────
def open_target(article):
    """업로드 폴더의 절대 경로와 안내 글. 반환 (경로 또는 '', 안내). static 폴더 안만 허용."""
    base = photo_dir(article)
    if not base:
        return "", "먼저 플랫폼별 콘텐츠 생성을 눌러 주세요"
    target = os.path.abspath(os.path.join(base, UPLOAD_DIR))
    root = os.path.realpath("static")
    real = os.path.realpath(target)
    if not (real == root or real.startswith(root + os.sep)):
        return "", "static 폴더 안의 폴더만 열 수 있어요"
    if not os.path.isdir(target):
        return "", "먼저 플랫폼별 콘텐츠 생성을 눌러 주세요"
    return target, ""


# ── 최종본 맨 위 정보 줄 ────────────────────────────────────────
_WEEKDAY = "월화수목금토일"


def _norm_kw(s):
    return re.sub(r"\s+", "", s or "")


def load_time_table(path=TIME_DOC):
    """원고작업지시 3장 표에서 {(MM/DD, 키워드): HH:MM} 과 {MM/DD: [HH:MM 정렬 목록]}. 못 읽으면 빈 값."""
    by_kw, by_day = {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if len(cells) < 6 or not cells[0].isdigit():
                    continue
                m = re.match(r"(\d{1,2}/\d{1,2})\s+(\d{1,2}:\d{2})", cells[1])
                if not m:
                    continue
                day, hm = m.group(1), m.group(2).zfill(5)
                by_kw[(day, _norm_kw(cells[5]))] = hm
                by_day.setdefault(day, []).append(hm)
    except OSError:
        pass
    for k in by_day:
        by_day[k] = sorted(by_day[k])
    return by_kw, by_day


def info_line(item, article, table=None):
    """최종본 맨 위 정보: {when(큰 글씨), title, tags_comma, tags_hash, note}. 시각을 못 찾으면 날짜만 쓰고 note 에 이유."""
    by_kw, by_day = table if table is not None else load_time_table()
    date = str(((item or {}).get("plan") or {}).get("publish_date") or "").strip() or ((article.get("platform_published_at") or {}).get("naver") or "").strip()
    note, when = "", ""
    if date:
        try:
            d = datetime.strptime(date[:10], "%Y-%m-%d")
            day = f"{d.month}/{d.day}"
            hm = by_kw.get((day, _norm_kw((item or {}).get("keyword"))))
            if not hm:
                hm = (article.get("platform_scheduled_time") or {}).get("naver") or ""
            head = f"{d.month}/{d.day}({_WEEKDAY[d.weekday()]})"
            if hm:
                nth = (by_day.get(day) or []).index(hm) + 1 if hm in (by_day.get(day) or []) else 0
                when = f"{head} {hm}" + (f" · 그날 {nth}번째" if nth else "")
            else:
                when = head
                note = "시각 정보 없음(글 데이터에 예약 시각 칸이 비어 있고, 원고작업지시 3장 표에서도 못 찾음)"
        except ValueError:
            when = date
    else:
        note = "발행 예정일 없음"
    title = ((article.get("platform_title") or {}).get("naver") or "").strip()
    raw = (article.get("platform_raw") or {}).get("naver") or ""
    tagline = ""
    ms = list(shop_formatter.TAG_LINE_RE.finditer(raw.replace("\r\n", "\n")))
    if ms:
        tagline = ms[-1].group(1).strip()
    toks = [t for t in re.split(r"[,，\s#]+", tagline) if t]
    return {"when": when, "title": title, "tags_comma": ", ".join(toks), "tags_hash": " ".join("#" + t for t in toks), "tags_raw": tagline, "note": note}
