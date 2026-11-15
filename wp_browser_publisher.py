"""
워드프레스 발행 — 이미지 업로드는 브라우저로, 글 생성은 REST API로 처리하는 하이브리드 방식.

배경: REST API로 이미지를 업로드하면(대용량 파일 전송) 일부 호스팅사(가비아 등)의 보안
시스템이 의심 트래픽으로 차단하는 경우가 있었습니다. 반면 제목·본문·카테고리·태그·발행일 같은
작은 텍스트(JSON) 요청은 차단되지 않는다는 게 확인됐습니다.

그래서 이미지 업로드만 실제 브라우저(사람이 파일 선택창으로 올리는 것과 동일한 방식)로 처리하고,
나머지는 화면 요소를 찾아 클릭하는 불안정한 방식 대신 REST API(wp_publisher.py)로 처리합니다.
이렇게 하면 워드프레스 관리자 화면의 버튼 위치·문구·테마 간섭에 전혀 영향을 안 받습니다.
"""
import json
import os
import re
import tempfile
from datetime import datetime as _dt

from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import config
import wp_publisher


class WordPressBrowserPublishError(Exception):
    """브라우저 자동화 중 발생한 오류. 사람이 읽을 메시지를 담습니다."""


SCREENSHOT_DIR = os.path.join("static", "generated", "wp_browser_failures")
COMPRESS_QUALITY = 78
COMPRESS_MAX_WIDTH = 1400
LOGIN_WAIT_MS = 60000  # 로그인 결과(성공/오류 문구)가 나타나길 기다리는 최대 시간


def _save_failure_screenshot(page) -> str | None:
    """실패한 순간의 화면을 파일로 저장합니다."""
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        filename = f"failure_{_dt.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(SCREENSHOT_DIR, filename)
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


def _login_diagnostics(page) -> str:
    """로그인 대기 타임아웃 시점의 상태를 한 줄로 요약합니다 (비밀번호 등 민감값은 넣지 않음)."""
    parts = []
    try:
        parts.append(f"현재URL={page.url}")
    except Exception:
        pass
    try:
        parts.append(f"문서상태={page.evaluate('document.readyState')}")
    except Exception:
        pass
    try:
        err = page.locator("#login_error").first
        if err.count():
            parts.append("로그인오류문구=" + err.inner_text(timeout=2000).strip().replace("\n", " ")[:200])
    except Exception:
        pass
    try:
        body = page.inner_text("body", timeout=2000)
        for label, needles in {
            "2단계인증추정": ["인증 코드", "verification code", "two-factor", "2FA", "authenticator"],
            "캡차추정": ["captcha", "캡차", "로봇이 아닙니다", "recaptcha"],
            "잠금추정": ["locked", "잠겼", "too many", "너무 많은", "blocked", "차단"],
        }.items():
            if any(n.lower() in body.lower() for n in needles):
                parts.append(label)
        parts.append(f"대시보드요소={'있음' if page.locator('#wpadminbar').count() else '없음'}")
    except Exception:
        pass
    return " | ".join(parts)


def _compress_to_temp_jpeg(file_path: str) -> str:
    """업로드용으로 임시 폴더에 압축된 JPEG를 만들어 그 경로를 반환합니다 (원본은 안 건드림)."""
    img = Image.open(file_path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    if img.width > COMPRESS_MAX_WIDTH:
        ratio = COMPRESS_MAX_WIDTH / img.width
        img = img.resize((COMPRESS_MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img.save(tmp_path, format="JPEG", quality=COMPRESS_QUALITY, optimize=True)
    return tmp_path


def _upload_single_image(page, local_path: str) -> tuple[dict | None, list[str]]:
    """
    이미 열려있는 업로드 화면(page)에서 이미지 1장을 올리고, 그 업로드 요청 자체의 네트워크
    응답을 가로채서 실제 id/url을 읽어옵니다. 페이지는 이 함수 안에서 새로 열지 않습니다 —
    매번 새로 열면 워드프레스 업로더 스크립트(plupload)가 파일 입력창에 이벤트를 연결하기 전에
    파일을 넣어버려서 업로드 자체가 시작 안 되는 경우가 있었습니다.

    (더 예전 방식은 업로드 후 "최근 미디어 1개 조회" API를 별도로 불러서 그걸 방금 올린 파일이라고
    추측했는데, 이 사이트가 쓰는 Cloudflare + LiteSpeed Cache 때문에 그 조회 자체가 캐싱되어
    옛날 결과가 돌아올 수 있어서 문제였습니다. 지금은 추측 대신 업로드 응답 자체를 직접 읽습니다.)

    반환: (업로드 결과 또는 None, 대기 중 관찰된 모든 응답 URL 목록 — 실패시 진단용)
    """
    upload_result: dict = {}
    seen_responses: list[str] = []
    raw_bodies: list[str] = []

    def _on_response(response):
        url = response.url
        is_upload_related = response.request.method == "POST" and (
            "async-upload.php" in url or "/wp-json/wp/v2/media" in url
        )
        if not is_upload_related:
            return
        seen_responses.append(f"{response.status} {url}")
        try:
            body_text = response.text()
        except Exception as e:
            raw_bodies.append(f"[본문을 읽을 수 없음: {e}]")
            return
        # 응답 본문 앞부분을 그대로 남겨둡니다 — JSON 파싱이 왜 실패하는지(PHP 경고가 섞여
        # 나오는지, 필드 이름이 다른지 등) 다음에 바로 확인할 수 있게 하기 위함입니다.
        raw_bodies.append(body_text[:500])

        stripped = body_text.strip()
        # 이 워드프레스는 구버전(레거시) 업로더 방식으로 응답합니다 — JSON이 아니라
        # 방금 올린 첨부파일의 ID를 순수 숫자 하나로 돌려줍니다 (예: "123"). 이 경우는
        # 추측할 필요가 전혀 없습니다: 이 숫자가 정확히 방금 올린 파일의 ID입니다.
        # (실제 파일 주소는 이 함수 밖에서 이 ID로 REST API를 콕 집어 조회해서 채웁니다.)
        if stripped.isdigit():
            upload_result["id"] = int(stripped)
            return

        # 일부 워드프레스 환경은 PHP 경고/알림(deprecated notice 등)이 JSON 앞에 그대로
        # 출력되어 순수 JSON 파싱이 깨지는 경우가 있습니다. 본문에서 첫 '{' 이후만 잘라서
        # 다시 시도합니다 (관대한 파싱).
        json_start = body_text.find("{")
        if json_start == -1:
            return
        try:
            data = json.loads(body_text[json_start:])
        except Exception:
            return

        def _find_id_url(node, depth=0):
            """id와 url(또는 source_url)이 함께 있는 딕셔너리를 재귀적으로 찾습니다."""
            if depth > 4:
                return None
            if isinstance(node, dict):
                node_id = node.get("id") or node.get("attachment_id") or node.get("ID")
                found_url = node.get("url") or node.get("source_url") or node.get("guid")
                if node_id and found_url:
                    return {"id": node_id, "url": found_url}
                for v in node.values():
                    result = _find_id_url(v, depth + 1)
                    if result:
                        return result
            elif isinstance(node, list):
                for item in node:
                    result = _find_id_url(item, depth + 1)
                    if result:
                        return result
            return None

        found = _find_id_url(data)
        if found:
            upload_result["id"] = found["id"]
            upload_result["url"] = found["url"]
        elif isinstance(data, dict) and data.get("success") is False:
            # 워드프레스가 명시적으로 업로드 실패를 응답한 경우 (예: 용량 초과, 권한 문제 등)
            err_msg = data.get("data")
            raw_bodies.append(f"[명시적 실패 응답] success=false, data={err_msg}")

    page.on("response", _on_response)
    try:
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(local_path)

        # 위 리스너가 첨부파일 ID를 잡을 때까지 기다립니다 (레거시 방식은 id만 먼저 오고,
        # 실제 주소는 아래에서 그 id로 별도 조회합니다).
        waited_ms = 0
        while "id" not in upload_result and waited_ms < 30000:
            page.wait_for_timeout(500)
            waited_ms += 500

        # id는 있는데 url이 아직 없으면(레거시 방식) — "최근 항목"처럼 추측하지 않고,
        # 방금 확인한 그 정확한 id로 콕 집어서 실제 주소를 조회합니다. 다른 이미지와
        # 헷갈릴 여지가 전혀 없습니다.
        if upload_result.get("id") and not upload_result.get("url"):
            try:
                source_url = page.evaluate(
                    """
                    async (mediaId) => {
                        const res = await fetch(`/wp-json/wp/v2/media/${mediaId}`, {
                            credentials: 'include',
                            cache: 'no-store'
                        });
                        if (!res.ok) return null;
                        const data = await res.json();
                        return data.source_url || null;
                    }
                    """,
                    upload_result["id"],
                )
                if source_url:
                    upload_result["url"] = source_url
                else:
                    raw_bodies.append(f"[id={upload_result['id']} 조회 실패] REST API가 source_url을 안 줌")
            except Exception as e:
                raw_bodies.append(f"[id={upload_result.get('id')} 조회 중 오류] {e}")
    finally:
        page.remove_listener("response", _on_response)

    diagnostics = seen_responses[:]
    for i, body in enumerate(raw_bodies[:3]):
        diagnostics.append(f"[응답본문{i+1}] {body}")
    ok = "id" in upload_result and "url" in upload_result
    return (upload_result if ok else None), diagnostics


def _upload_images_via_browser(
    local_srcs: list[str], static_folder: str, headless: bool, step_callback=None
) -> dict:
    """
    실제 브라우저로 로그인한 뒤, 미디어 업로드 화면에서 각 이미지를 진짜 파일 선택창으로 올립니다.
    반환: {로컬주소: {"url": 실제주소, "id": 미디어ID}} 매핑
    """
    def _step(msg):
        if step_callback:
            step_callback(msg)

    if not config.WORDPRESS_BROWSER_CONFIGURED:
        raise WordPressBrowserPublishError(
            f".env에 다음 값을 채워주세요: {', '.join(config.MISSING_WORDPRESS_BROWSER_VARS)}"
        )

    base_url = config.WORDPRESS_URL
    uploaded_map: dict[str, dict] = {}
    temp_files = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=150 if not headless else 0)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            _step("로그인 페이지 접속 중...")
            page.goto(f"{base_url}/wp-login.php", wait_until="domcontentloaded", timeout=60000)
            page.fill("#user_login", config.WORDPRESS_ADMIN_USERNAME)
            page.fill("#user_pass", config.WORDPRESS_ADMIN_PASSWORD)
            _step("로그인 시도 중...")
            page.click("#wp-submit")
            # 성공 판정은 알림판 전체 로딩(load)을 기다리지 않습니다 — 알림판의 무거운 위젯(Site Kit,
            # 사이트 건강 상태, 외부 뉴스 등) 때문에 load가 30초 안에 안 끝나도 로그인은 이미 성공한 상태입니다.
            login_ok = False
            login_error_text = None
            waited_ms = 0
            while waited_ms < LOGIN_WAIT_MS:
                try:
                    current_url = page.url
                    if "/wp-admin" in current_url and "wp-login.php" not in current_url:
                        if page.locator("#wpadminbar, #adminmenu").count():
                            login_ok = True
                            break
                    elif "wp-login.php" in current_url:
                        err = page.locator("#login_error")
                        if err.count():
                            login_error_text = err.first.inner_text(timeout=2000).strip().replace("\n", " ")[:200]
                            break
                except Exception:
                    pass  # 페이지 이동 중에는 요소 조회가 잠깐 실패할 수 있음 — 다음 반복에서 다시 확인
                page.wait_for_timeout(500)
                waited_ms += 500

            if login_error_text:
                raise WordPressBrowserPublishError(
                    f"로그인에 실패했습니다. 화면에 표시된 오류: {login_error_text}\n"
                    "WORDPRESS_ADMIN_USERNAME / WORDPRESS_ADMIN_PASSWORD를 .env에서 확인해주세요."
                )
            if not login_ok:
                diag = _login_diagnostics(page)
                print(f"[워드프레스 자동발행] 로그인 결과 판별 불가 진단: {diag}")
                raise WordPressBrowserPublishError(
                    "로그인 성공 여부를 확인하지 못했습니다 (원인 불명). "
                    f"화면 캡처는 {SCREENSHOT_DIR} 폴더에 저장됩니다.\n(진단) " + diag
                )

            if local_srcs:
                _step(f"이미지 {len(local_srcs)}장 업로드 중...")
                page.goto(f"{base_url}/wp-admin/media-new.php", wait_until="domcontentloaded", timeout=60000)
                # 관리자 화면 전체 로딩(load) 대신, 업로드에 실제로 필요한 파일 입력창만 기다립니다.
                page.wait_for_selector("input[type='file']", state="attached", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except PlaywrightTimeoutError:
                    pass
                # 업로더 스크립트(plupload)가 파일 입력창에 이벤트를 연결할 시간을 확실히 줍니다.
                # (페이지를 열자마자 바로 파일을 넣으면 아직 스크립트가 준비 안 돼서
                # 업로드 요청 자체가 안 나가는 경우가 있었습니다.)
                page.wait_for_timeout(1500)

                for i, src in enumerate(local_srcs, start=1):
                    local_path = os.path.join(static_folder, src.replace("/static/", "", 1))
                    if not os.path.exists(local_path):
                        continue
                    _step(f"이미지 {i}/{len(local_srcs)} 업로드 중...")
                    compressed_path = _compress_to_temp_jpeg(local_path)
                    temp_files.append(compressed_path)

                    upload_result, diagnostics = _upload_single_image(page, compressed_path)
                    if upload_result:
                        uploaded_map[src] = {"url": upload_result["url"], "id": upload_result["id"]}
                        _step(f"이미지 {i}/{len(local_srcs)} 업로드 완료")
                    else:
                        if diagnostics:
                            detail = " (진단정보) " + " | ".join(diagnostics)
                        else:
                            detail = " (업로드 요청 자체가 감지되지 않았습니다 — 파일 입력창을 못 찾았거나 업로더가 준비 안 됐을 수 있습니다)"
                        _step(f"이미지 {i}/{len(local_srcs)} 업로드 응답을 못 받았습니다.{detail} — 재동기화로 다시 시도해주세요.")
                        _save_failure_screenshot(page)

            browser.close()
            return uploaded_map
        except WordPressBrowserPublishError:
            screenshot_path = _save_failure_screenshot(page)
            browser.close()
            raise
        except Exception as e:
            screenshot_path = _save_failure_screenshot(page)
            browser.close()
            msg = f"이미지 업로드 중 예상치 못한 오류: {e}"
            if screenshot_path:
                msg += f"\n화면 캡처: {screenshot_path}"
            raise WordPressBrowserPublishError(msg)
        finally:
            for f in temp_files:
                try:
                    os.remove(f)
                except OSError:
                    pass


def reupload_missing_images(
    html_content: str, static_folder: str = "static", headless: bool = False, step_callback=None
) -> tuple[str, dict]:
    """
    이미 발행된 글의 본문에 아직도 로컬 경로(/static/generated/...)가 남아있으면 — 처음 발행할 때
    그 이미지 업로드만 조용히 실패해서 남아있는 경우 — 다시 로그인해서 그 이미지들만 골라 올리고,
    실제 주소로 치환한 본문을 반환합니다. 남아있는 게 없으면 원본을 그대로 반환합니다.
    반환: (치환된 본문, {로컬주소: 실제주소} 매핑)
    """
    local_srcs = sorted(set(re.findall(r'src="(/static/generated/[^"]+)"', html_content)))
    if not local_srcs:
        return html_content, {}

    uploaded_map = _upload_images_via_browser(local_srcs, static_folder, headless, step_callback)
    uploaded_urls = {}
    for src, info in uploaded_map.items():
        html_content = html_content.replace(f'src="{src}"', f'src="{info["url"]}"')
        uploaded_urls[src] = info["url"]
    return html_content, uploaded_urls


def publish_post(
    title: str,
    html_content: str,
    schema_text: str = "",
    status: str = "publish",  # "publish" | "future"
    date_iso: str | None = None,  # 예약 발행 시 "YYYY-MM-DDTHH:MM" 형식
    category_names: list[str] | None = None,
    tag_names: list[str] | None = None,
    static_folder: str = "static",
    headless: bool = False,
    step_callback=None,
    focus_keyword: str | None = None,
    meta_description: str | None = None,
    strip_cover: bool = False,
    related_priority_post_ids=None,
    cover_fallback: bool = False,
) -> dict:
    """
    이미지 업로드는 브라우저로, 나머지(제목·본문·카테고리·태그·발행일·발행 자체)는 REST API로
    처리합니다. 워드프레스 관리자 화면의 버튼 위치나 테마 간섭에 전혀 영향받지 않습니다.
    strip_cover=True면 표지를 업로드·대표이미지로 지정한 뒤, 본문 사본에서만 뺍니다(wp_publisher.create_post_via_rest).
    반환: {"link": 실제 발행 주소}
    """
    def _step(msg):
        if step_callback:
            step_callback(msg)

    local_srcs = sorted(set(re.findall(r'src="(/static/generated/[^"]+)"', html_content)))

    uploaded_map = _upload_images_via_browser(local_srcs, static_folder, headless, step_callback)

    for src, info in uploaded_map.items():
        html_content = html_content.replace(f'src="{src}"', f'src="{info["url"]}"')

    # 대표 이미지는 표지(cover) 이미지로 지정합니다. 파일명을 알파벳 순으로 정렬한 첫 번째를
    # 쓰던 예전 방식은, "..._body_0.png"가 "..._cover_final.png"보다 알파벳상 앞서서(b < c)
    # 본문 이미지가 대표이미지로 잘못 지정되는 버그가 있었습니다. 파일명에 "cover"가 들어간
    # 이미지를 명시적으로 우선합니다.
    featured_media_id = None
    cover_src = next((s for s in local_srcs if "cover" in s.lower() and s in uploaded_map), None)
    if cover_src:
        featured_media_id = uploaded_map[cover_src]["id"]
    else:
        for src in local_srcs:
            if src in uploaded_map:
                featured_media_id = uploaded_map[src]["id"]
                break

    if cover_fallback and not featured_media_id:
        # 롱텀: 이전 시도에서 이미 올라간 표지 주소가 저장본에 들어 있어 새로 올린 이미지가 없으면 대표이미지가 비게 됩니다.
        # 본문 표지의 실제 주소와 정확히 같은 미디어를 찾아 씁니다(못 찾으면 None 그대로).
        featured_media_id = wp_publisher.find_cover_media_id_in_content(html_content)

    _step("글 발행 중 (REST API)...")
    try:
        result = wp_publisher.create_post_via_rest(
            title=title,
            html_content=html_content,
            schema_text=schema_text,
            status=status,
            date_iso=date_iso,
            category_names=category_names,
            tag_names=tag_names,
            featured_media_id=featured_media_id,
            focus_keyword=focus_keyword,
            meta_description=meta_description,
            strip_cover=strip_cover,
            related_priority_post_ids=related_priority_post_ids,
            ensure_featured=cover_fallback,
        )
    except Exception as e:
        # 이미지는 이미 워드프레스에 올라간 상태입니다. 호출한 쪽(app.py)이 그 주소를 저장해서 다음 시도 때
        # 다시 올리지 않도록, 예외 객체에 업로드 결과를 붙여서 그대로 다시 던집니다 (예외 종류는 그대로).
        e.uploaded_images = {src: info["url"] for src, info in uploaded_map.items()}
        raise
    _step("발행 완료")
    uploaded_urls = {src: info["url"] for src, info in uploaded_map.items()}
    out = {"link": result["link"], "id": result.get("id"), "uploaded_images": uploaded_urls}
    if "featured_media_missing" in result:
        out["featured_media_missing"] = result["featured_media_missing"]
    return out
