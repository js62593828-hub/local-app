"""
공식 출처 캡처 후보 (source_capture_v1)

- 원고 메타데이터 메모의 "출처 캡처:" 블록을 읽고,
- 허용 목록에 있는 공공 사이트 화면에서 "찾을 문구"가 있는 부분을 캡처해 JPG로 저장합니다.

이 파일은 app.py가 후보 칸(최종본 화면 맨 아래)에서만 호출합니다. 본문·복사·발행에는 쓰이지 않습니다.
(캡처 이미지는 글 데이터의 새 칸 source_captures에만 기록됩니다.)
"""
import io
import os
import re
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

# ── 설정 상수 ────────────────────────────────────────────────────────────────
SOURCE_CAPTURE_HIGHLIGHT = False   # True면 찾은 문구 요소에 형광펜(노란 배경)을 입힌 뒤 캡처
SOURCE_CAPTURE_AUTO = False        # True면 최종본을 만들 때 자동 캡처(지금은 안 씀 — 버튼으로만)

# 허용 도메인. 앞에 "."이 있으면 "그 아래 주소 전체"(호스트가 그 글자로 끝나야 함), 없으면 "그 도메인 자체와 하위 도메인".
SOURCE_CAPTURE_ALLOWED_HOSTS = (
    ".go.kr",        # 정부·공공기관 (법제처 law.go.kr, 복지로 bokjiro.go.kr, 고용24 work24.go.kr, 국세청 nts.go.kr …)
    "korea.kr",      # 정책브리핑
    "gov.kr",        # 정부24
    "kosis.kr",      # 국가통계포털
    "nps.or.kr",     # 국민연금공단
    "nhis.or.kr",    # 국민건강보험공단
    "comwel.or.kr",  # 근로복지공단
    "kepco.co.kr",   # 한국전력
)
# 호스트 이름(주소의 도메인 부분)에만 적용하는 차단 단어. 주소 경로에는 적용하지 않습니다(정책브리핑 korea.kr/news/… 통과).
SOURCE_CAPTURE_BLOCKED_HOST_WORDS = (
    "news", "blog", "tistory.com", "naver.com", "daum.net",
    "coupang.com", "11st.co.kr", "gmarket.co.kr", "auction.co.kr", "ssg.com", "wemakeprice.com", "tmon.co.kr",
)
SOURCE_CAPTURE_MAX_ITEMS = 3
SOURCE_CAPTURE_VIEWPORT_W = 1500          # 창 너비 (법령 조문 오른쪽 잘림 방지: 넓게)
SOURCE_CAPTURE_VIEWPORT_H = 1000
SOURCE_CAPTURE_SAVE_MAX_W = 1200          # 저장 이미지 가로 최대
SOURCE_CAPTURE_JPG_QUALITY = 85
SOURCE_CAPTURE_GOTO_TIMEOUT_MS = 30000
SOURCE_CAPTURE_IDLE_TIMEOUT_MS = 15000
SOURCE_CAPTURE_EXTRA_WAIT_MS = 3000
SOURCE_CAPTURE_FIND_RETRIES = 3           # 늦게 뜨는 내용 대비: 못 찾으면 2초 뒤 다시 (최대 이 횟수)
SOURCE_CAPTURE_FOLDER_NAME = "출처캡처"
SOURCE_CAPTURE_PAD_Y = 140                # 찾은 요소 위·아래 여유(px)
SOURCE_CAPTURE_PAD_X = 30
SOURCE_CAPTURE_MAX_CLIP_H = 2000
SOURCE_CAPTURE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# 기관 이름(캡션 자동 생성용). 긴 호스트부터 일치하는 것을 씁니다.
INSTITUTION_NAMES = {
    "korea.kr": "정책브리핑",
    "law.go.kr": "법제처 국가법령정보센터",
    "bokjiro.go.kr": "복지로",
    "work24.go.kr": "고용24",
    "gov.kr": "정부24",
    "nts.go.kr": "국세청",
    "hometax.go.kr": "홈택스",
    "moel.go.kr": "고용노동부",
    "mohw.go.kr": "보건복지부",
    "molit.go.kr": "국토교통부",
    "moef.go.kr": "기획재정부",
    "kca.go.kr": "한국소비자원",
    "consumer.go.kr": "소비자24",
    "kosis.kr": "국가통계포털",
    "nps.or.kr": "국민연금공단",
    "nhis.or.kr": "국민건강보험공단",
    "comwel.or.kr": "근로복지공단",
    "kepco.co.kr": "한국전력",
}

STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"      # 허용 목록 밖 (접속하지 않음)
STATUS_FAILED = "failed"        # 접속 실패·문구 못 찾음·저장 실패
STATUS_INVALID = "invalid"      # 메모 줄에 주소나 찾을 문구가 비어 있음
STATUS_LABELS = {
    STATUS_OK: "캡처 성공",
    STATUS_BLOCKED: "허용 목록 밖",
    STATUS_FAILED: "캡처 실패",
    STATUS_INVALID: "입력 오류",
}

# ── 허용 주소 판정 ───────────────────────────────────────────────────────────

def _host_of(url: str):
    """(호스트, 이유). 호스트가 None이면 이유가 거부 사유입니다."""
    url = (url or "").strip()
    if not url:
        return None, "주소가 비어 있음"
    if re.search(r"[\s\\\x00-\x1f]", url):
        return None, "주소에 공백·역슬래시·제어문자가 있음"
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        netloc = parts.netloc
        host = parts.hostname
    except ValueError:
        return None, "주소 형식이 잘못됨"
    if scheme not in ("http", "https"):
        return None, "http·https 주소만 허용"
    if "@" in netloc:
        return None, "사용자 정보(@)가 들어 있는 주소는 거부"
    if not host:
        return None, "호스트 이름이 없음"
    host = host.lower().rstrip(".")
    try:
        parts.port  # 잘못된 포트면 ValueError
    except ValueError:
        return None, "포트 형식이 잘못됨"
    return host, ""


def _host_matches(host: str, entry: str) -> bool:
    if entry.startswith("."):
        return host.endswith(entry) and len(host) > len(entry)
    return host == entry or host.endswith("." + entry)


def check_url(url: str) -> tuple[bool, str]:
    """허용 목록 판정. (통과 여부, 이유). 호스트 이름만 봅니다(경로·쿼리의 글자는 보지 않음)."""
    host, why = _host_of(url)
    if host is None:
        return False, why
    for word in SOURCE_CAPTURE_BLOCKED_HOST_WORDS:
        if word in host:
            return False, f"차단 단어({word})가 호스트에 있음"
    if any(_host_matches(host, e) for e in SOURCE_CAPTURE_ALLOWED_HOSTS):
        return True, ""
    return False, "허용 목록에 없는 도메인"


def institution_name(url: str) -> str:
    host, _ = _host_of(url)
    if not host:
        return ""
    for key in sorted(INSTITUTION_NAMES, key=len, reverse=True):
        if _host_matches(host, key):
            return INSTITUTION_NAMES[key]
    return host


def format_date(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return f"{dt.year}.{dt.month}.{dt.day}"


def make_caption(inst: str, dt: datetime | None = None) -> str:
    return f"출처: {inst} (확인일 {format_date(dt)})"


# ── 메모 읽기 ────────────────────────────────────────────────────────────────
_BLOCK_HEAD_RE = re.compile(r"^\s*[-*•#>\s]*\**\s*출처\s*캡처\s*\**\s*[:：]\s*\**\s*$")


def _clean(s: str) -> str:
    return (s or "").strip().strip("*").strip().strip("`").strip()


def read_memo(text: str) -> dict:
    """
    메모에서 "출처 캡처:" 블록을 읽습니다.
    반환: {"has_block": bool, "items": [{"heading","url","phrase","caption","error"}…], "warnings": [str…]}
    줄 형식: 추천 소제목 | 주소 | 찾을 문구 | 캡션   (추천 소제목·캡션은 비워도 됨, 최대 3줄)
    """
    out = {"has_block": False, "items": [], "warnings": []}
    lines = (text or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if _BLOCK_HEAD_RE.match(line):
            start = i
            break
    if start is None:
        return out
    out["has_block"] = True
    seen_item = False
    ignored = 0
    for line in lines[start + 1:]:
        raw = line.strip()
        if not raw:
            if seen_item:
                break          # 항목이 시작된 뒤 빈 줄이면 블록 끝
            continue           # 제목 줄과 첫 항목 사이의 빈 줄은 건너뜀
        if "|" not in raw:
            break              # "태그: …" 같은 다음 항목 줄 → 블록 끝
        seen_item = True
        raw = re.sub(r"^[-*•]\s+", "", raw)
        fields = [f for f in raw.split("|")]
        if len(out["items"]) >= SOURCE_CAPTURE_MAX_ITEMS:
            ignored += 1
            continue
        heading = _clean(fields[0]).strip('"“”').strip()
        heading = re.sub(r"^#+\s*", "", heading).strip().strip('"“”').strip()
        url = _clean(fields[1]) if len(fields) > 1 else ""
        url = url.strip("<>").strip()
        m = re.match(r"^\[[^\]]*\]\(([^)]+)\)$", url)   # [글](주소) 형태
        if m:
            url = m.group(1).strip()
        phrase = _clean(fields[2]) if len(fields) > 2 else ""
        caption = _clean("|".join(fields[3:])) if len(fields) > 3 else ""
        error = ""
        if not url and not phrase:
            error = "주소와 찾을 문구가 비어 있음"
        elif not url:
            error = "주소가 비어 있음"
        elif not phrase:
            error = "찾을 문구가 비어 있음"
        out["items"].append({"heading": heading, "url": url, "phrase": phrase, "caption": caption, "error": error})
    if ignored:
        out["warnings"].append(f"출처 캡처는 최대 {SOURCE_CAPTURE_MAX_ITEMS}줄까지만 읽어요. 4번째 줄부터 {ignored}줄은 무시했어요.")
    return out


# ── 문구 비교 (공백·가운뎃점·괄호 차이 무시) ──────────────────────────────────────
_IGNORED_CHARS = " \t\r\n ​　ㆍ·•・‧∙⋅･()[]（）［］"


def normalize_text(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch not in _IGNORED_CHARS).lower()


# 모든 프레임에서 실행: 문구를 포함하는 보이는 요소 중 가장 알맞은 것과 그 본문 영역을 돌려줍니다.
_FIND_JS = """
(args) => {
  const phrase = args.phrase, ign = args.ign;
  const NORM = s => { let o = ''; for (const ch of (s || '')) { if (ign.indexOf(ch) < 0) o += ch; } return o.toLowerCase(); };
  const body = document.body;
  if (!body) return null;
  if (NORM(body.innerText).indexOf(phrase) < 0) return null;
  const SKIP = new Set(['SCRIPT','STYLE','NOSCRIPT','HEAD','HTML','META','LINK','TITLE','TEMPLATE','OPTION','SELECT','TEXTAREA']);
  const vis = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity) > 0.05;
  };
  let best = null, bestKey = null;
  const all = body.querySelectorAll('*');
  for (const el of all) {
    if (SKIP.has(el.tagName)) continue;
    if ((el.textContent || '').length < phrase.length) continue;
    const t = el.innerText;
    if (!t) continue;
    const n = NORM(t);
    if (n.indexOf(phrase) < 0) continue;
    if (!vis(el)) continue;
    const navish = (el.closest('nav,aside,footer,a,button,[role=navigation],[role=menu],[role=button]') ? 1 : 0);
    const starts = n.startsWith(phrase) ? 0 : 1;
    const key = [navish, starts, n.length];
    if (bestKey === null || key[0] < bestKey[0] || (key[0] === bestKey[0] && (key[1] < bestKey[1] ||
        (key[1] === bestKey[1] && key[2] < bestKey[2])))) { best = el; bestKey = key; }
  }
  if (!best) return null;
  // 본문 영역: 찾은 요소의 조상 중 가로 600px 이상인 가장 가까운 요소(없으면 body)
  let c = best.parentElement;
  while (c && c !== document.body && c.getBoundingClientRect().width < 600) c = c.parentElement;
  if (!c) c = document.body;
  return [best, c];
}
"""

_HIDE_FLOAT_JS = """
(keep) => {
  let n = 0;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
    if (keep && el.contains(keep)) continue;
    el.style.setProperty('visibility', 'hidden', 'important'); n++;
  }
  return n;
}
"""

_HIGHLIGHT_JS = "(el) => { el.style.setProperty('background-color', '#fff176', 'important'); }"


# ── 캡처 엔진 ────────────────────────────────────────────────────────────────

def _save_jpg(png_bytes: bytes, dest_path: str) -> tuple[int, int, int]:
    """PNG 바이트 → 가로 최대 1200px JPG(품질 85). (가로, 세로, 파일 크기)"""
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if img.width > SOURCE_CAPTURE_SAVE_MAX_W:
        h = max(1, round(img.height * SOURCE_CAPTURE_SAVE_MAX_W / img.width))
        img = img.resize((SOURCE_CAPTURE_SAVE_MAX_W, h), Image.LANCZOS)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    img.save(dest_path, "JPEG", quality=SOURCE_CAPTURE_JPG_QUALITY, optimize=True)
    return img.width, img.height, os.path.getsize(dest_path)


def _find_in_frames(page, phrase_norm: str):
    """(frame, element_handle, container_handle) 또는 None. 모든 프레임을 훑습니다."""
    for frame in page.frames:
        try:
            h = frame.evaluate_handle(_FIND_JS, {"phrase": phrase_norm, "ign": _IGNORED_CHARS})
        except Exception:  # noqa: BLE001 — 사라진 프레임·접근 불가 프레임
            continue
        try:
            el = h.get_property("0").as_element()
            cont = h.get_property("1").as_element()
        except Exception:  # noqa: BLE001
            el = cont = None
        if el is not None:
            return frame, el, cont or el
    return None


def _capture_one(browser, url: str, phrase: str, dest_path: str) -> dict:
    """한 후보를 캡처해 dest_path에 저장. 반환: {"ok","reason","w","h","bytes"}"""
    phrase_norm = normalize_text(phrase)
    if not phrase_norm:
        return {"ok": False, "reason": "찾을 문구가 비어 있음"}
    ctx = browser.new_context(
        viewport={"width": SOURCE_CAPTURE_VIEWPORT_W, "height": SOURCE_CAPTURE_VIEWPORT_H},
        locale="ko-KR", user_agent=SOURCE_CAPTURE_USER_AGENT,
    )
    state = {"blocked_to": ""}
    try:
        page = ctx.new_page()

        def _route(route, request):
            try:
                if request.is_navigation_request() and request.frame.parent_frame is None:
                    ok, _ = check_url(request.url)
                    if not ok:
                        state["blocked_to"] = request.url
                        route.abort()
                        return
                route.continue_()
            except Exception:  # noqa: BLE001
                try:
                    route.continue_()
                except Exception:  # noqa: BLE001
                    pass

        page.route("**/*", _route)
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=SOURCE_CAPTURE_GOTO_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001
            if state["blocked_to"]:
                return {"ok": False, "reason": "허용 목록 밖 주소로 이동하려 해서 멈춤"}
            msg = str(e)
            if "Timeout" in msg or "timeout" in msg:
                return {"ok": False, "reason": "접속 실패(시간 초과)"}
            return {"ok": False, "reason": "접속 실패: " + msg.splitlines()[0][:80]}
        if state["blocked_to"]:
            return {"ok": False, "reason": "허용 목록 밖 주소로 이동하려 해서 멈춤"}
        if resp is not None and resp.status >= 400:
            return {"ok": False, "reason": f"페이지 오류(HTTP {resp.status})"}
        ok, _ = check_url(page.url)
        if not ok:
            return {"ok": False, "reason": "허용 목록 밖 주소로 이동됨"}
        try:
            page.wait_for_load_state("networkidle", timeout=SOURCE_CAPTURE_IDLE_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 — 끝없이 통신하는 페이지는 그냥 진행
            pass
        page.wait_for_timeout(SOURCE_CAPTURE_EXTRA_WAIT_MS)

        found = None
        for attempt in range(SOURCE_CAPTURE_FIND_RETRIES):
            found = _find_in_frames(page, phrase_norm)
            if found:
                break
            if attempt < SOURCE_CAPTURE_FIND_RETRIES - 1:
                page.wait_for_timeout(2000)
        if not found:
            return {"ok": False, "reason": "화면에서 문구를 못 찾음(첨부파일에만 있거나 로그인이 필요한 화면일 수 있어요)"}
        frame, el, cont = found

        # 떠 있는 요소 숨기기 (찾은 요소를 품은 것은 제외). 프레임 사슬을 따라 각 프레임에서 실행.
        keep_by_frame = {frame: el}
        f = frame
        while f.parent_frame is not None:
            try:
                fe = f.frame_element()
            except Exception:  # noqa: BLE001
                break
            keep_by_frame[f.parent_frame] = fe
            f = f.parent_frame
        for fr in page.frames:
            try:
                fr.evaluate(_HIDE_FLOAT_JS, keep_by_frame.get(fr))
            except Exception:  # noqa: BLE001
                pass
        if SOURCE_CAPTURE_HIGHLIGHT:
            try:
                el.evaluate(_HIGHLIGHT_JS)
            except Exception:  # noqa: BLE001
                pass

        try:
            el.scroll_into_view_if_needed(timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(300)
        eb = el.bounding_box()
        cb = cont.bounding_box() or eb
        if not eb:
            return {"ok": False, "reason": "찾은 요소의 위치를 못 구함"}
        try:
            c_scroll_w = cont.evaluate("c => c.scrollWidth")
        except Exception:  # noqa: BLE001
            c_scroll_w = cb["width"]
        sx, sy = page.evaluate("[window.scrollX, window.scrollY]")
        page_w, page_h = page.evaluate(
            "[Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0),"
            " Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)]"
        )
        # 가로: 본문 영역 좌우 폭 기준(찾은 요소·넘치는 글자까지 포함)
        x0 = min(cb["x"], eb["x"]) + sx - SOURCE_CAPTURE_PAD_X
        x1 = max(cb["x"] + max(cb["width"], c_scroll_w), eb["x"] + eb["width"]) + sx + SOURCE_CAPTURE_PAD_X
        # 세로: 찾은 요소 위아래 여유
        y0 = eb["y"] + sy - SOURCE_CAPTURE_PAD_Y
        y1 = eb["y"] + eb["height"] + sy + SOURCE_CAPTURE_PAD_Y
        if frame.parent_frame is not None:  # 프레임 안 요소면 그 프레임 영역 밖은 자르기
            try:
                fb = frame.frame_element().bounding_box()
            except Exception:  # noqa: BLE001
                fb = None
            if fb:
                x0 = max(x0, fb["x"] + sx)
                x1 = min(x1, fb["x"] + fb["width"] + sx)
                y0 = max(y0, fb["y"] + sy)
                y1 = min(y1, fb["y"] + fb["height"] + sy)
        x0 = max(0.0, x0)
        y0 = max(0.0, y0)
        x1 = min(float(page_w), x1)
        y1 = min(float(page_h), y1)
        if y1 - y0 > SOURCE_CAPTURE_MAX_CLIP_H:
            y1 = y0 + SOURCE_CAPTURE_MAX_CLIP_H
        if x1 - x0 < 50 or y1 - y0 < 30:
            return {"ok": False, "reason": "캡처 범위가 너무 작음"}
        clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
        try:
            png = page.screenshot(clip=clip, full_page=True, type="png")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": "화면 저장 실패: " + str(e).splitlines()[0][:80]}
        try:
            w, h, size = _save_jpg(png, dest_path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": "이미지 저장 실패: " + str(e)[:80]}
        return {"ok": True, "reason": "", "w": w, "h": h, "bytes": size}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "캡처 중 오류: " + str(e).splitlines()[0][:100] if str(e) else "캡처 중 오류"}
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass


def capture_candidates(items: list[dict], article_id: str, channel_folder: str,
                       base_dir: str = os.path.join("static", "generated"),
                       now: datetime | None = None, stats: list | None = None) -> list[dict]:
    """
    items(read_memo의 항목들)를 차례로 처리해 같은 순서의 기록 목록을 돌려줍니다.
    허용 목록 밖·입력 오류는 접속하지 않습니다. 브라우저는 1개만 열어 후보들을 차례로 처리합니다.
    실패한 후보는 이유만 기록하고 이미지 파일을 만들지 않습니다.
    기록 모양: heading·url·phrase·caption·inst·path·status·reason·captured_at
    stats(선택)에는 {"index","elapsed","w","h","bytes"}를 추가합니다(시험용).
    """
    now = now or datetime.now()
    records: list[dict] = []
    todo: list[int] = []
    for i, it in enumerate(items):
        url = it.get("url", "")
        inst = institution_name(url) if url else ""
        rec = {
            "heading": it.get("heading", ""), "url": url, "phrase": it.get("phrase", ""),
            "caption": it.get("caption", "") or (make_caption(inst, now) if inst else ""),
            "inst": inst, "path": "", "status": STATUS_FAILED, "reason": "",
            "captured_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if it.get("error"):
            rec["status"], rec["reason"] = STATUS_INVALID, it["error"]
        else:
            ok, why = check_url(url)
            if not ok:
                rec["status"], rec["reason"] = STATUS_BLOCKED, why
            else:
                todo.append(i)
        records.append(rec)
    if not todo:
        return records

    folder = os.path.join(base_dir, channel_folder or "기타", SOURCE_CAPTURE_FOLDER_NAME, article_id)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        for i in todo:
            records[i]["reason"] = "캡처 도구(playwright)를 불러오지 못함: " + str(e)[:60]
        return records
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for i in todo:
                    rec = records[i]
                    dest = os.path.join(folder, f"cap_{uuid.uuid4().hex[:8]}.jpg")
                    t0 = time.time()
                    res = _capture_one(browser, rec["url"], rec["phrase"], dest)
                    elapsed = time.time() - t0
                    if res["ok"]:
                        rec["status"], rec["reason"] = STATUS_OK, ""
                        rec["path"] = "/" + dest.replace(os.sep, "/")
                    else:
                        rec["status"], rec["reason"] = STATUS_FAILED, res["reason"]
                        if os.path.exists(dest):   # 실패 화면 이미지는 남기지 않음
                            try:
                                os.remove(dest)
                            except OSError:
                                pass
                    if stats is not None:
                        stats.append({"index": i, "elapsed": round(elapsed, 1), "w": res.get("w"), "h": res.get("h"),
                                      "bytes": res.get("bytes"), "ok": res["ok"], "reason": res["reason"]})
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 — 브라우저 실행 자체가 실패
        for i in todo:
            if records[i]["status"] != STATUS_OK:
                records[i]["status"] = STATUS_FAILED
                records[i]["reason"] = "브라우저 실행 실패: " + (str(e).splitlines()[0][:80] if str(e) else "")
    return records
