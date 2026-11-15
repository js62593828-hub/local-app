"""
워드프레스 REST API 자동발행 모듈.

- 애플리케이션 비밀번호(Application Password)로 인증합니다 (플러그인 불필요, 워드프레스 기본 내장 기능).
- 로컬에서 생성된 이미지(표지·본문)를 워드프레스 미디어 라이브러리에 업로드하고,
  본문 안의 로컬 이미지 주소를 업로드된 실제 주소로 바꿔치기합니다.
- 카테고리·태그는 이름으로 넘기면 됩니다 (이미 있으면 그걸 쓰고, 없으면 새로 만듭니다).
- 구조화 데이터(JSON-LD 스키마)는 API로 직접 넣으면 브라우저 붙여넣기와 달리 그대로 저장됩니다
  (복사·붙여넣기 때 사라지던 문제가 API 경로에서는 발생하지 않습니다).
"""
import base64
import html as _html
import io
import json
import mimetypes
import os
import re
import threading
import time
from datetime import datetime

import requests
from PIL import Image

import config
import shop_formatter

_UPDATE_DATE_PATTERN = re.compile(r'<p class="content-update-date"[^>]*>.*?</p>\s*', re.DOTALL)
# 블록 형식(as_blocks)으로 넣은 "최종 업데이트" 줄 — 블록 주석까지 통째로 매칭해서 지웁니다.
_UPDATE_DATE_BLOCK_PATTERN = re.compile(
    r'<!-- wp:paragraph \{"className":"content-update-date"\} -->.*?<!-- /wp:paragraph -->\s*', re.DOTALL
)
# 표지 이미지 블록(className seo-cover-img) — "최종 업데이트" 줄을 이 블록 바로 뒤에 넣습니다.
_COVER_BLOCK_PATTERN = re.compile(r'<!-- wp:image \{[^}]*seo-cover-img[^}]*\} -->.*?<!-- /wp:image -->\n?', re.DOTALL)


_COVER_IMG_PATTERN = re.compile(r'<img class="seo-cover-img"[^>]*>')


def _strip_cover_image(html_content: str) -> str:
    """
    워드프레스로 보내는 사본에서 본문 맨 앞의 표지(seo-cover-img)를 뺍니다 — 테마가 대표이미지(같은 표지)를
    글 위에 이미 보여 줘서 같은 사진이 두 번 보이는 것을 막습니다. 블록 형식이면 wp:image 블록 통째로 뺍니다.
    앱 저장본(platform_final)은 건드리지 않습니다(대표이미지 찾기에 계속 씀).
    """
    html_content = _COVER_BLOCK_PATTERN.sub("", html_content)
    return _COVER_IMG_PATTERN.sub("", html_content)


def _wrap_html_block(content: str) -> str:
    """스크립트·관련 글처럼 문단/제목이 아닌 원본 HTML은 "사용자 지정 HTML" 블록으로 감쌉니다 (클래식 블록으로 안 묶이게)."""
    return f"<!-- wp:html -->\n{content}\n<!-- /wp:html -->\n"

# ── 여러 워드프레스 사이트 지원 ──────────────────────────────────────────────
# 원래는 config.py의 워드프레스 접속 정보(롱텀 블로그 하나)만 썼는데, 이제 채널마다
# (워드프레스 SHOP 등) 완전히 다른 워드프레스 사이트에 발행해야 합니다. 매 함수마다 site
# 파라미터를 끌고 다니면 실수로 빠뜨리기 쉬워서, 대신 "지금 이 요청에서 쓸 사이트"를
# 요청 시작할 때 한 번 지정해두면 이 파일의 모든 발행 함수가 자동으로 그 사이트를 씁니다.
# threading.local을 쓰는 이유는, 나중에 앱이 동시에 여러 요청을 처리하게 되더라도 요청끼리
# 사이트 정보가 서로 안 섞이게 하기 위함입니다.
_site_local = threading.local()


def set_site_context(site: dict | None) -> None:
    """
    이후 이 스레드에서 부르는 발행 함수들이 쓸 워드프레스 사이트를 지정합니다.
    site는 {"url", "username", "app_password"} 키를 가진 dict. None을 넘기면 기본값
    (config.py의 WORDPRESS_* — 롱텀 블로그)으로 되돌아갑니다. app.py가 채널을 바꿀 때마다
    요청 맨 앞에서 이걸 호출해서 그 채널의 사이트로 맞춰줍니다.
    """
    _site_local.site = site


def _current_site() -> dict:
    site = getattr(_site_local, "site", None)
    if site:
        return site
    return {
        "url": config.WORDPRESS_URL,
        "username": config.WORDPRESS_USERNAME,
        "app_password": config.WORDPRESS_APP_PASSWORD,
    }


def current_site_configured() -> bool:
    """지금 지정된 사이트의 접속 정보(주소·아이디·앱 비밀번호)가 다 채워져 있는지 확인합니다."""
    site = _current_site()
    return bool(site.get("url") and site.get("username") and site.get("app_password"))


def _require_site_configured() -> None:
    if current_site_configured():
        return
    site = _current_site()
    missing = [
        name
        for name, value in [("URL", site.get("url")), ("USERNAME", site.get("username")), ("APP_PASSWORD", site.get("app_password"))]
        if not value
    ]
    raise WordPressPublishError(f"워드프레스 연동 정보가 없습니다. 이 채널의 .env 값을 확인해주세요 (빠진 항목: {', '.join(missing)}).")


_LD_JSON_RE = re.compile(r'(<script type="application/ld\+json">)(.*?)(</script>)', re.DOTALL)


def _apply_schema_dates(schema_text: str, date_str: str, modified_str: str | None = None) -> str:
    """
    구조화 데이터(JSON-LD) 중 Article 스키마의 datePublished·dateModified를 date_str(YYYY-MM-DD)로 맞춥니다.
    modified_str을 주면 dateModified만 그 날짜로 합니다(재동기화: 공개일은 그대로, 수정일은 오늘).
    스키마는 "플랫폼별 콘텐츠 생성"을 누른 날 만들어져 저장되므로, 발행할 때 실제 공개일로 덮어씁니다.
    Article이 아닌 스키마(FAQPage·Product 등)와 JSON으로 읽을 수 없는 블록은 그대로 둡니다.
    """
    if not schema_text:
        return schema_text

    def repl(m: re.Match) -> str:
        try:
            data = json.loads(m.group(2))
        except ValueError:
            return m.group(0)
        if not isinstance(data, dict) or data.get("@type") != "Article":
            return m.group(0)
        data["datePublished"] = date_str
        data["dateModified"] = modified_str or date_str
        return f"{m.group(1)}\n{json.dumps(data, ensure_ascii=False, indent=2)}\n{m.group(3)}"

    return _LD_JSON_RE.sub(repl, schema_text)


def _inject_update_date(html_content: str, date_str: str, as_blocks: bool = False) -> str:
    """
    본문 상단에 "최종 업데이트: YYYY-MM-DD"를 실제로 눈에 보이게 넣습니다. 발행/재동기화할
    때마다 이 날짜가 갱신되어, 구글과 독자 모두에게 "발행만 하고 방치한 글이 아니라 꾸준히
    관리되는 콘텐츠"라는 신호를 줍니다. 이미 있던 문구는 지우고 새로 넣어서, 재동기화를 여러
    번 해도 문구가 중복으로 쌓이지 않게 합니다.
    as_blocks=True(숍 채널)면 이 줄을 wp:paragraph 블록으로, 표지 블록 바로 뒤에 넣습니다.
    """
    html_content = _UPDATE_DATE_BLOCK_PATTERN.sub("", html_content)  # 블록 형식이 아니면 아무 일도 안 함
    html_content = _UPDATE_DATE_PATTERN.sub("", html_content)
    if as_blocks:
        update_block = (
            '<!-- wp:paragraph {"className":"content-update-date"} -->\n'
            '<p class="content-update-date"><span style="color:#767676; font-size:13px;">'
            f"📅 최종 업데이트: {date_str}</span></p>\n"
            "<!-- /wp:paragraph -->\n"
        )
        cover_block = _COVER_BLOCK_PATTERN.search(html_content)
        if cover_block:
            return html_content[: cover_block.end()] + update_block + html_content[cover_block.end():]
        return update_block + html_content
    update_line = (
        '<p class="content-update-date" style="color:#767676; font-size:13px; margin:0 0 20px;">'
        f"📅 최종 업데이트: {date_str}</p>"
    )
    cover_match = re.search(r'<img class="seo-cover-img"[^>]*>', html_content)
    if cover_match:
        insert_at = cover_match.end()
        return html_content[:insert_at] + update_line + html_content[insert_at:]
    return update_line + html_content

# 일부 호스팅사(가비아 등)의 기본 보안 시스템이, 파이썬 requests 라이브러리가 기본으로 붙이는
# User-Agent("python-requests/2.x")를 보고 "프로그램이 보낸 의심스러운 요청"으로 자동 차단하는
# 경우가 있습니다. 일반 브라우저처럼 보이게 헤더를 붙여서 이런 차단을 피합니다.
BROWSER_LIKE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class WordPressPublishError(Exception):
    """워드프레스 API 호출 중 발생한 오류. 사람이 읽을 메시지를 담습니다."""


def _safe_json(response: requests.Response, context: str) -> dict:
    """
    response.json()을 안전하게 호출합니다. 상태코드는 정상(2xx)인데 본문이 비어있거나
    JSON이 아닌 경우(네이버 API 때와 같은 유형의 순간적인 문제)가 가끔 있어서, 그럴 때
    막연한 파이썬 예외("Expecting value...") 대신 원인을 알 수 있는 메시지로 실패시킵니다.
    """
    try:
        return response.json()
    except ValueError:
        snippet = response.text[:400].strip()
        looks_like_html = snippet.lstrip().lower().startswith(("<!doctype", "<html"))
        hint = (
            "워드프레스가 API 응답 대신 일반 웹페이지(HTML)를 돌려준 것 같습니다 — "
            "로그인 페이지, 보안 프로그램의 차단/안내 페이지, 또는 REST API 경로 자체가 "
            "막혀있을 때 이런 일이 생깁니다."
            if looks_like_html
            else "보통 일시적인 네트워크 문제입니다 — 잠시 후 다시 시도해보세요."
        )
        raise WordPressPublishError(
            f"{context} — 워드프레스가 빈 응답(또는 JSON이 아닌 응답)을 반환했습니다.\n"
            f"응답 상태코드: {response.status_code}, 본문 길이: {len(response.text)}자.\n"
            f"실제 응답 내용(앞부분): {snippet}\n\n"
            f"{hint}"
        )


JPEG_QUALITY = 82
MAX_UPLOAD_WIDTH = 1600  # 이보다 크면 줄여서 용량을 더 줄입니다

# 413(용량 초과) 오류가 나면, 이 순서대로 점점 더 세게 압축해서 재시도합니다.
COMPRESSION_FALLBACKS = [
    (82, 1600),  # 기본값
    (65, 1280),
    (50, 1000),
    (35, 800),
]


def _compress_for_upload(file_path: str, quality: int = JPEG_QUALITY, max_width: int = MAX_UPLOAD_WIDTH) -> tuple[bytes, str, str]:
    """
    업로드 직전에 이미지를 JPEG로 재압축합니다. 원본 PNG는 그대로 두고(로컬앱 자체 미리보기용),
    워드프레스로 보내는 것만 용량을 줄여서 "413 Request Entity Too Large"(호스팅사 업로드 용량 제한)를 피합니다.
    반환: (압축된 바이트, 새 파일명, MIME 타입)
    """
    try:
        img = Image.open(file_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        new_filename = os.path.splitext(os.path.basename(file_path))[0] + ".jpg"
        return buffer.getvalue(), new_filename, "image/jpeg"
    except Exception:
        # 혹시 압축이 실패하면(손상된 파일 등) 원본 그대로 업로드 시도
        with open(file_path, "rb") as f:
            raw = f.read()
        filename = os.path.basename(file_path)
        mime_type = mimetypes.guess_type(filename)[0] or "image/png"
        return raw, filename, mime_type


def _auth_header() -> dict:
    site = _current_site()
    token = base64.b64encode(f"{site['username']}:{site['app_password']}".encode()).decode()
    return {**BROWSER_LIKE_HEADERS, "Authorization": f"Basic {token}"}


def _api_url(path: str) -> str:
    site = _current_site()
    return f"{site['url']}/wp-json/wp/v2/{path.lstrip('/')}"


REQUEST_TIMEOUT = 45   # 요청 하나의 제한 시간(초) — 롱텀 호스팅이 간헐적으로 5~15초 넘게 늦게 응답합니다
REQUEST_RETRIES = 2    # 시간 초과·연결 오류일 때 재시도 횟수 (처음 시도 포함 최대 3번)
REQUEST_RETRY_WAIT = 3  # 재시도 전에 쉬는 시간(초)


def _request(method: str, url: str, *, retries: int = REQUEST_RETRIES, timeout: int = REQUEST_TIMEOUT, **kwargs):
    """
    이 파일의 모든 워드프레스 요청이 거치는 공용 GET/POST 헬퍼. 시간 초과나 연결 오류가 나면
    REQUEST_RETRY_WAIT초 쉬었다가 최대 retries번 다시 시도합니다 (HTTP 4xx/5xx 응답은 재시도하지 않고 그대로 돌려줍니다).
    retries=0이면 재시도 없이 한 번만 보냅니다 — 글 생성(POST posts)처럼 서버에서는 이미 처리됐을 수 있어
    다시 보내면 중복될 수 있는 요청에 씁니다.
    """
    send = requests.get if method.lower() == "get" else requests.post
    attempt = 0
    while True:
        try:
            return send(url, timeout=timeout, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt >= retries:
                raise
            attempt += 1
            print(f"[워드프레스] {type(e).__name__} — {REQUEST_RETRY_WAIT}초 뒤 재시도 ({attempt}/{retries}): {url.split('/wp-json/')[-1][:60]}")
            time.sleep(REQUEST_RETRY_WAIT)


def _raise_for_status_with_detail(response: requests.Response, context: str) -> None:
    if response.ok:
        return
    try:
        detail = response.json()
    except ValueError:
        detail = response.text[:500]
    raise WordPressPublishError(f"{context} 중 오류 (HTTP {response.status_code})\n워드프레스 응답: {detail}")


def upload_media(file_path: str, alt_text: str | None = None, title: str | None = None) -> dict:
    """로컬 이미지 파일 하나를 워드프레스 미디어 라이브러리에 업로드합니다.
    업로드 직전 JPEG로 압축해서 용량 제한(413 오류)에 안 걸리게 하고,
    그래도 413이 나면 더 세게 압축해서 자동으로 재시도합니다 (COMPRESSION_FALLBACKS 순서대로).
    alt_text/title을 넘기면 업로드 직후 그 미디어의 대체텍스트·제목으로 지정합니다.
    반환: {"id": 미디어ID, "source_url": 실제 업로드된 이미지 주소}
    """
    if not os.path.exists(file_path):
        raise WordPressPublishError(f"이미지 파일을 찾을 수 없습니다: {file_path}")

    last_error = None
    for quality, max_width in COMPRESSION_FALLBACKS:
        file_bytes, filename, mime_type = _compress_for_upload(file_path, quality=quality, max_width=max_width)

        headers = {
            **_auth_header(),
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": mime_type,
        }
        # 업로드는 재시도해도 같은 파일이 한 번 더 올라갈 뿐(미디어 라이브러리에 중복)이고 발행이 끊기는 것보다 낫습니다.
        response = _request("post", _api_url("media"), headers=headers, data=file_bytes)

        if response.ok:
            data = _safe_json(response, f"이미지 업로드({filename})")
            if alt_text or title:
                # 업로드 후 alt_text·title 지정 (실패해도 이미지는 이미 올라갔으니 발행은 계속하고 로그만 남김)
                try:
                    meta_response = _request(
                        "post",
                        _api_url(f"media/{data['id']}"),
                        headers=_auth_header(),
                        json={k: v for k, v in {"alt_text": alt_text, "title": title}.items() if v},
                    )
                    _raise_for_status_with_detail(meta_response, "미디어 alt/title 지정")
                except (WordPressPublishError, requests.RequestException) as e:
                    print(f"[워드프레스] 경고 — alt/title 지정 실패(이미지는 업로드됨): {e}")
            return {"id": data["id"], "source_url": data["source_url"]}

        if response.status_code != 413:
            # 용량 문제가 아닌 다른 오류(인증 실패 등)는 재시도해도 소용없으니 바로 에러
            _raise_for_status_with_detail(response, f"이미지 업로드({filename})")

        last_error = response  # 413이면 다음 단계(더 강한 압축)로 재시도

    _raise_for_status_with_detail(
        last_error, f"이미지 업로드 — 가장 강하게 압축(품질 {COMPRESSION_FALLBACKS[-1][0]}%)해도 용량 초과"
    )


def resolve_term_ids(names: list[str], taxonomy: str) -> list[int]:
    """카테고리(taxonomy='categories') 또는 태그(taxonomy='tags') 이름을 워드프레스 ID로 바꿔줍니다.
    이미 있으면 그 ID를 쓰고, 없으면 새로 만듭니다."""
    ids = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        search_resp = _request("get", _api_url(taxonomy), headers=_auth_header(), params={"search": name})
        _raise_for_status_with_detail(search_resp, f"{taxonomy} 조회")
        matches = [t for t in _safe_json(search_resp, f"{taxonomy} 조회") if t["name"] == name]
        if matches:
            ids.append(matches[0]["id"])
            continue

        create_resp = _request("post", _api_url(taxonomy), headers=_auth_header(), json={"name": name})
        if create_resp.status_code == 400:
            # 첫 시도가 시간 초과됐지만 서버에서는 이미 만들어졌다면, 재시도는 "이미 있음(term_exists)" 400을 받습니다.
            # 그 응답에 기존 항목의 ID가 들어 있으므로 그대로 씁니다.
            try:
                body = create_resp.json()
                if body.get("code") == "term_exists" and body.get("data", {}).get("term_id"):
                    ids.append(body["data"]["term_id"])
                    continue
            except ValueError:
                pass
        _raise_for_status_with_detail(create_resp, f"{taxonomy} 새로 만들기('{name}')")
        ids.append(_safe_json(create_resp, f"{taxonomy} 새로 만들기('{name}')")["id"])
    return ids


def build_related_posts_block(
    category_id: int | None,
    exclude_post_id: int | None = None,
    limit: int = 5,
    any_category: bool = False,
    exclude_post_ids=None,
    site_fill: bool = False,
    priority_post_ids=None,
    future_before: datetime | None = None,
    future_urls: dict | None = None,
) -> str:
    """
    같은 카테고리의 최신 글 목록(최대 limit개)을 실시간으로 조회해서, 글 하단에 붙일 수 있는
    "관련 글" HTML 블록을 만듭니다. 클러스터 형제글과 달리 이미 발행되어 있는 글만 대상이라
    "아직 상대방이 안 나와서 링크가 비는" 문제가 없습니다. 각 글의 대표이미지(썸네일)도 함께
    가져와서 카드 형태로 보여줍니다.
    카테고리에 글이 하나도 없으면(첫 글인 경우) 빈 문자열을 반환합니다.
    any_category=True면 카테고리 조건 없이 사이트 전체의 최신 공개 글을 씁니다(워드프레스 일반 꿀팁 채널용).
    exclude_post_ids: 이 워드프레스 글 ID들은 후보에서 뺍니다("기타 리뷰"로 표시한 글).
    site_fill=True(워드프레스 일반 꿀팁 채널): 이 글 카테고리(category_id)의 최신 공개 글을 먼저 쓰고, limit개가 안 되면
    사이트 전체 최신 공개 글로 중복 없이 채웁니다(category_id가 없으면 사이트 전체만).
    priority_post_ids(롱텀): 이 워드프레스 글 ID들(이미 공개된 같은 묶음 글, 최신 발행순)을 맨 앞에 쓰고, limit개가 안 되면
    같은 카테고리 최신 공개 글로 중복 없이 채웁니다.
    future_before(워드프레스 일반=싼세계): 이 글의 공개 시각. 주면 예약(future) 글 중 공개 시각이 이보다 이른 글도 후보에 넣습니다.
    예약 글의 링크는 REST가 "?p=ID" 주소를 주므로 쓰지 않고, future_urls({워드프레스 글 ID: 정식 주소})에서 찾은 정식 주소만 씁니다
    (정식 주소를 모르는 예약 글은 뺍니다). 정렬·같은 카테고리 먼저→사이트 최신 채우기 규칙은 그대로입니다.
    exclude_post_ids·site_fill·priority_post_ids·future_before 기본값일 때의 동작은 예전과 완전히 같습니다.
    """
    if not category_id and not any_category and not site_fill and not priority_post_ids:
        return ""
    skip = set(exclude_post_ids or ())
    if exclude_post_id is not None:
        skip.add(exclude_post_id)
    use_category = bool(category_id) and not any_category
    future_on = future_before is not None and bool(future_urls)  # 정식 주소를 아는 예약 글이 하나도 없으면 예전과 똑같이 조회

    def _usable_future(p: dict):
        """예약 글이면 (공개 시각이 future_before보다 이르고 정식 주소를 알 때만) 정식 주소로 바꾼 사본, 아니면 None. 공개 글은 그대로."""
        if p.get("status") != "future":
            return p
        url = (future_urls or {}).get(p.get("id"))
        if not url or re.search(r"[?&]p=\d+", url):
            return None
        try:
            when = datetime.fromisoformat(str(p.get("date") or ""))
        except ValueError:
            return None
        return {**p, "link": url} if when < future_before else None

    def _fetch(with_category: bool, extra: dict | None = None, allow_future: bool = True):
        """조회 결과 목록. 실패하면 None (경고만 출력)."""
        with_future = future_on and allow_future

        def _once(statuses: str, per_page: int):
            params = {
                **({"categories": category_id} if with_category else {}),
                "orderby": "date",
                "order": "desc",
                "per_page": min(100, per_page),
                "status": statuses,
                "_embed": "wp:featuredmedia",  # 대표이미지도 이 한 번의 조회로 같이 받아옵니다.
                **(extra or {}),
            }
            response = _request("get", _api_url("posts"), headers=_auth_header(), params=params)
            if not response.ok:
                print(f"[워드프레스] 경고 — 관련 글 조회 응답 오류(HTTP {response.status_code}), 관련 글 없이 계속합니다.")
                return None
            return _safe_json(response, "관련 글 조회")

        base = limit + 1 + len(exclude_post_ids or ())  # 제외할 글이 있으면 그만큼 더 받아 옵니다
        # 관련 글은 부가 기능입니다 — 시간 초과·응답 오류가 나도 경고만 출력하고 빈 값을 돌려줘서 발행/재동기화가 계속되게 합니다.
        try:
            if with_future:
                got = _once("publish,future", base + 40)  # 예약 글 중 이 글보다 늦은 글은 걸러내므로 넉넉히 받아 옵니다
                if got is None:
                    got = _once("publish", base)  # 예약 글 조회가 막히면 예전처럼 공개 글만
                    return got
                return [q for q in (_usable_future(p) for p in got) if q is not None]
            return _once("publish", base)
        except Exception as e:  # noqa: BLE001
            print(f"[워드프레스] 경고 — 관련 글 조회 실패, 관련 글 없이 계속합니다: {type(e).__name__} {e}"[:220])
            return None

    priority: list = []
    if priority_post_ids:
        # 롱텀: 같은 묶음의 이미 공개된 글을 먼저 (넘겨 받은 순서 = 최신 발행순 그대로). 조회가 실패해도 아래 카테고리 글로 계속합니다.
        ids = [i for i in dict.fromkeys(priority_post_ids) if i not in skip][:limit]
        if ids:
            got = _fetch(False, {"include": ",".join(str(i) for i in ids), "orderby": "include", "per_page": len(ids)}, allow_future=False) or []
            order = {pid: n for n, pid in enumerate(ids)}
            priority = sorted((p for p in got if p["id"] in order), key=lambda p: order[p["id"]])[:limit]

    if priority_post_ids and not priority and not (use_category or any_category or site_fill):
        return ""  # 카테고리 조건이 없는데 같은 묶음 글도 못 가져왔으면 사이트 전체 글로 채우지 않고 블록 없이 진행합니다
    if priority and (len(priority) >= limit or not (use_category or any_category or site_fill)):
        posts = priority  # 같은 묶음 글만으로 채워졌거나, 채울 카테고리 조건이 없음
    else:
        posts = _fetch(use_category)
        if posts is None:
            if not priority:
                return ""
            posts = []
        taken = {p["id"] for p in priority}
        posts = priority + [p for p in posts if p["id"] not in skip and p["id"] not in taken]
        posts = posts[:limit]
    if site_fill and use_category and len(posts) < limit:
        more = _fetch(False)  # 카테고리 글이 모자라면 사이트 전체 최신 글로 채웁니다 (실패해도 카테고리 글은 그대로 씁니다)
        seen = {p["id"] for p in posts}
        for p in more or []:
            if len(posts) >= limit:
                break
            if p["id"] not in skip and p["id"] not in seen:
                posts.append(p)
                seen.add(p["id"])
    if not posts:
        return ""

    def _thumbnail_url(post: dict) -> str | None:
        try:
            media = post.get("_embedded", {}).get("wp:featuredmedia", [{}])[0]
            sizes = media.get("media_details", {}).get("sizes", {})
            for size_key in ("medium_large", "medium", "large", "full", "thumbnail"):
                if size_key in sizes:
                    return sizes[size_key]["source_url"]
            return media.get("source_url")
        except (IndexError, KeyError, AttributeError):
            return None

    cards = []
    for p in posts:
        title = p["title"]["rendered"]
        link = p["link"]
        thumb = _thumbnail_url(p)
        # 표지 이미지 안에 제목 글자가 박혀 있으므로, 그 글자가 실제로 읽힐 만큼 큼직하게
        # 보여줍니다 (16:9 비율 유지, 자르지 않음).
        img_html = (
            f'<img src="{thumb}" alt="{title}" style="width:220px; height:124px; object-fit:cover; '
            'border-radius:8px; flex-shrink:0;">'
            if thumb
            else '<div style="width:220px; height:124px; border-radius:8px; background:#e5e5e5; flex-shrink:0;"></div>'
        )
        cards.append(
            f'<a href="{link}" style="display:flex; gap:18px; align-items:center; text-decoration:none; '
            'color:#111827; padding:14px 0; border-bottom:1px solid #e5e5e5;">'
            f"{img_html}"
            f'<span style="font-size:16px; font-weight:700; line-height:1.5;">{title}</span>'
            "</a>"
        )
    # 마지막 카드는 구분선을 뺍니다.
    if cards:
        cards[-1] = cards[-1].replace(" border-bottom:1px solid #e5e5e5;", "")

    return (
        '<div style="background:#f5f5f5; border-radius:8px; padding:18px 20px 6px; margin:30px 0 0;">'
        '<strong style="display:block; margin-bottom:4px;">함께 보면 좋은 글</strong>'
        f'<div>{"".join(cards)}</div>'
        "</div>"
    )


def get_post_category_ids(post_id: int) -> list[int]:
    """
    이 글에 워드프레스가 실제로 갖고 있는 카테고리 ID를 직접 조회합니다. 재동기화처럼
    "지금 진짜 상태" 기준으로 다시 만들어야 하는 작업에 씁니다. (로컬에 저장해둔 메타데이터
    메모의 카테고리 값에만 의존하면, 그 메모를 안 채워뒀을 때 조용히 아무것도 안 하고
    건너뛰는 문제가 있었습니다 — 이미 워드프레스에 카테고리가 지정돼 있어도 상관없이요.)
    """
    response = _request("get", _api_url(f"posts/{post_id}"), headers=_auth_header(), params={"_fields": "categories"})
    if not response.ok:
        return []
    data = _safe_json(response, "카테고리 조회")
    return data.get("categories") or []


def find_media_id_by_source_url(url: str, exact_only: bool = False) -> int | None:
    """
    이미 업로드된 미디어를 실제 파일 주소로 역으로 찾습니다. 재동기화 때 대표이미지를
    표지 이미지로 다시 맞추는 용도입니다 (파일명으로 검색해서, 정확히 같은 주소인 것을 고릅니다).
    exact_only=True면 source_url이 정확히 같은 것만 인정하고, 비슷한 첫 결과로 대신하지 않습니다(없으면 None).
    """
    if not url:
        return None
    filename = url.rsplit("/", 1)[-1]
    name_without_ext = filename.rsplit(".", 1)[0]
    # 대표이미지 재지정은 부가 기능입니다 — 조회가 실패해도 경고만 출력하고 None을 돌려줘서 재동기화가 계속되게 합니다.
    try:
        response = _request(
            "get", _api_url("media"), headers=_auth_header(), params={"search": name_without_ext, "per_page": 5}
        )
        if not response.ok:
            print(f"[워드프레스] 경고 — 미디어 검색 응답 오류(HTTP {response.status_code}), 대표이미지 재지정을 건너뜁니다.")
            return None
        results = _safe_json(response, "미디어 검색")
    except Exception as e:  # noqa: BLE001
        print(f"[워드프레스] 경고 — 미디어 검색 실패, 대표이미지 재지정을 건너뜁니다: {type(e).__name__} {e}"[:220])
        return None
    for item in results:
        if item.get("source_url") == url:
            return item["id"]
    if exact_only:
        return None
    return results[0]["id"] if results else None


_COVER_IMG_TAG_RE = re.compile(r'<img\b[^>]*\bclass="[^"]*\bseo-cover-img\b[^"]*"[^>]*>')
# 블록 형식(as_blocks)은 className이 wp:image 블록 주석·figure에 붙고 <img>에는 없습니다.
_COVER_BLOCK_IMG_RE = re.compile(r'<!-- wp:image \{[^}]*seo-cover-img[^}]*\} -->\s*<figure[^>]*>\s*(<img\b[^>]*>)')


def find_cover_media_id_in_content(html_content: str) -> int | None:
    """
    본문의 표지 <img class="seo-cover-img" src="실제주소">를 찾아, 그 주소와 정확히 같은 워드프레스 미디어의 ID를 돌려줍니다.
    아직 로컬 주소(/static/...)이거나, 못 찾거나, 정확히 같은 주소가 없으면 None (비슷한 첫 결과로 대신하지 않습니다).
    이미 올라간 표지 주소가 저장본에 들어 있어 "새로 업로드한 이미지"가 없는 재시도 때 대표이미지를 복구하는 용도입니다.
    """
    m = _COVER_IMG_TAG_RE.search(html_content or "") or _COVER_BLOCK_IMG_RE.search(html_content or "")
    if not m:
        return None
    tag = m.group(1) if m.lastindex else m.group(0)
    src = re.search(r'\bsrc="([^"]+)"', tag)
    if not src or not src.group(1).startswith(("http://", "https://")):
        return None
    return find_media_id_by_source_url(_html.unescape(src.group(1)), exact_only=True)


def set_featured_media(post_id: int, media_id: int) -> None:
    """이 글의 대표이미지(featured_media)를 지정합니다."""
    response = _request("post", _api_url(f"posts/{post_id}"), headers=_auth_header(), json={"featured_media": media_id})
    _raise_for_status_with_detail(response, "대표이미지 변경")


def _inject_media_id_into_image_block(html_content: str, old_src: str, new_src: str, media_id: int) -> str:
    """
    본문의 Gutenberg 이미지 블록(<!-- wp:image {...} --> + <figure><img src=old_src alt=.../>)을 찾아,
    블록 속성에 "id"를, <img>에 "wp-image-N" 클래스를 넣고 주소를 업로드된 실제 주소로 바꿉니다.
    (표지처럼 className이 붙은 블록도 그대로 처리됩니다. 해당 블록이 없으면 아무 일도 안 함.)
    """
    pattern = re.compile(
        r'<!-- wp:image (\{[^}]*\}) -->(\n<figure class="[^"]*"><img src=")' + re.escape(old_src) + r'(" alt="[^"]*")/>'
    )

    def _repl(m: re.Match) -> str:
        attrs = {"id": media_id, **json.loads(m.group(1))}
        return (
            f'<!-- wp:image {json.dumps(attrs, separators=(",", ":"))} -->{m.group(2)}{new_src}{m.group(3)} '
            f'class="wp-image-{media_id}"/>'
        )

    return pattern.sub(_repl, html_content)


def _replace_local_image_srcs(
    html_content: str, static_folder: str, set_media_meta: bool = False, collect: dict | None = None
) -> tuple[str, str | None]:
    """본문 안의 로컬 이미지 주소(/static/generated/...)를 찾아 전부 워드프레스에 업로드하고,
    본문 안 주소를 업로드된 실제 주소로 치환합니다.
    set_media_meta=True(숍 채널)면 각 <img>의 alt를 미디어의 alt_text·title로 같이 지정하고,
    본문의 Gutenberg 이미지 블록에는 미디어 ID를 심어서 미디어 라이브러리와 연결합니다.
    collect를 넘기면(dict) 올라간 이미지마다 {로컬주소: 실제주소}를 거기에 채워 둡니다(롱텀이 저장본에 실제 주소를 반영하는 데 씀).
    반환: (치환된 본문, 대표이미지로 쓸 표지 이미지의 워드프레스 미디어 ID 또는 None)
    """
    local_srcs = sorted(set(re.findall(r'src="(/static/generated/[^"]+)"', html_content)))
    first_uploaded_id = None
    cover_media_id = None

    for src in local_srcs:
        local_path = os.path.join(static_folder, src.replace("/static/", "", 1))
        alt = ""
        if set_media_meta:
            m = re.search(r'<img[^>]*\bsrc="' + re.escape(src) + r'"[^>]*\balt="([^"]*)"', html_content)
            alt = _html.unescape(m.group(1)) if m else ""
        uploaded = upload_media(local_path, alt_text=alt or None, title=alt or None)
        if set_media_meta:
            html_content = _inject_media_id_into_image_block(html_content, src, uploaded["source_url"], uploaded["id"])
        html_content = html_content.replace(f'src="{src}"', f'src="{uploaded["source_url"]}"')
        if collect is not None:
            collect[src] = uploaded["source_url"]
        if first_uploaded_id is None:
            first_uploaded_id = uploaded["id"]
        if cover_media_id is None and "cover" in src.lower():
            cover_media_id = uploaded["id"]

    # 대표 이미지는 표지(cover) 이미지를 우선합니다. 파일명을 알파벳 순으로 정렬한 첫 번째를
    # 그냥 쓰면, "..._body_0.png"가 "..._cover_final.png"보다 알파벳상 앞서서(b < c) 본문
    # 이미지가 대표이미지로 잘못 지정되는 버그가 있었습니다.
    return html_content, (cover_media_id or first_uploaded_id)


def uses_rank_math_route() -> bool:
    """
    Rank Math 메타(rank_math_description·rank_math_focus_keyword)는 롱텀·숍·워드프레스 일반 모두 워드프레스 기본 REST
    (posts의 meta)로 열려 있지 않아서, 보내도 조용히 버려집니다. 그래서 모든 사이트에서 Rank Math 전용 경로
    (rankmath/v1/updateMeta)로 따로 저장합니다.
    """
    return True


_PERCENT_PAIR = re.compile(r"%[^\s%]+%")
_PERCENT_BEFORE_TEXT = re.compile(r"%(?![\s)\]}.,!?;:·]|$)")


def clean_rank_math_description(text: str | None) -> str:
    """
    Rank Math는 설명 속 "%…%" 모양을 자기 치환 변수로 보고 지워 버립니다 ("50%초과~100%이하" → "50이하").
    "%" 바로 뒤에 공백(또는 닫는 괄호·문장부호)이 아닌 글자가 오면 공백 하나를 넣어 막습니다 ("50% 초과~100% 이하").
    excerpt에도 같은 문장을 보내서 두 값이 같게 유지합니다.
    공백 없이 붙은 "%…%" 짝이 있는 문장에서만 고칩니다 ("100%에서 50% 이하"처럼 안전한 문장은 "100% 에서"로 쪼개지 않게 그대로 둡니다).
    """
    text = text or ""
    if not _PERCENT_PAIR.search(text):
        return text
    return _PERCENT_BEFORE_TEXT.sub("% ", text)


def clean_focus_keyword(keyword: str | None) -> str:
    """포커스 키워드 정리: 쉼표(, 또는 ，)가 있으면 앞부분만 쓰고 앞뒤 공백을 지웁니다. (롱텀의 Rank Math 경로 전용)"""
    return re.split(r"[,，]", keyword or "", maxsplit=1)[0].strip()


def rank_math_update_meta(post_id: int, meta: dict) -> None:
    """Rank Math 전용 경로로 글 하나의 SEO 메타(설명·포커스 키워드 등)를 저장합니다. 본문·다른 필드는 건드리지 않습니다."""
    url = f"{_current_site()['url']}/wp-json/rankmath/v1/updateMeta"
    response = _request(
        "post", url, headers=_auth_header(), json={"objectType": "post", "objectID": int(post_id), "meta": meta}
    )
    _raise_for_status_with_detail(response, "Rank Math 메타 저장")


def create_post_via_rest(
    title: str,
    html_content: str,
    schema_text: str = "",
    status: str = "publish",
    date_iso: str | None = None,
    category_names: list[str] | None = None,
    tag_names: list[str] | None = None,
    featured_media_id: int | None = None,
    focus_keyword: str | None = None,
    meta_description: str | None = None,
    as_blocks: bool = False,
    strip_cover: bool = False,
    related_any_category: bool = False,
    related_site_fill: bool = False,
    related_exclude_post_ids=None,
    related_priority_post_ids=None,
    related_future_before: datetime | None = None,
    related_future_urls: dict | None = None,
    ensure_featured: bool = False,
) -> dict:
    """
    이미지 업로드는 이미 끝난 상태(본문 안 이미지 주소가 전부 실제 워드프레스 주소로
    바뀌어 있는 상태)라고 가정하고, 글 생성만 REST API로 처리합니다.

    이렇게 나눈 이유: 예전에 REST API가 막혔던 건 "대용량 이미지 파일 업로드" 요청이었고
    (호스팅사 보안 시스템이 큰 파일 업로드를 의심 트래픽으로 차단), 제목·본문·카테고리·
    태그·발행일 같은 작은 텍스트(JSON) 요청까지 막혔는지는 실제로 확인된 적이 없었습니다.
    그래서 이미지 업로드만 브라우저로 처리하고(이미 검증된 방식), 나머지는 화면 요소를
    찾아 클릭하는 불안정한 방식 대신 이 함수로 안정적으로 처리합니다.

    focus_keyword를 넘기면 Rank Math의 "포커스 키워드" 메타 필드도 같이 채웁니다
    (워드프레스 글 목록에 "키워드: 미설정"으로 뜨던 것을 자동으로 채워줍니다). 다만 이건
    Rank Math가 이 메타 필드를 REST API로 열어뒀을 때만 실제로 반영됩니다 — 버전에 따라
    안 먹힐 수 있으니, 적용 안 되면 수동으로 입력해주셔야 할 수도 있습니다.

    strip_cover=True면 본문의 표지 이미지를 빼고 보냅니다(대표이미지 featured_media_id는 그대로 지정).
    "최종 업데이트" 줄은 표지가 빠진 자리, 즉 본문 맨 위에 들어갑니다.

    반환: {"id": 글ID, "link": 실제 발행 주소, "status": 최종 상태}
    """
    _require_site_configured()

    # 본문 맨 앞의 <h1>제목</h1>은 뺍니다 — API로 넘기는 title 필드가 이미 화면에 제목을 표시해주므로,
    # 그대로 두면 제목이 두 번(테마가 그리는 것 + 본문 속 h1) 보이게 됩니다.
    html_content = re.sub(r"^\s*<h1>.*?</h1>", "", html_content, count=1)
    if strip_cover:
        html_content = _strip_cover_image(html_content)

    # "최종 업데이트"를 본문에 실제로 보이게 넣습니다 — 발행만 하고 방치한 글이 아니라 관리되는
    # 콘텐츠라는 신호를 구글과 독자에게 줍니다. 예약 발행이면 실제로 공개되는 날(예약일)을, 아니면 오늘을
    # 씁니다 (예전엔 항상 오늘이라, 9/22에 올려 9/27로 예약한 글에 "2026-09-22"가 찍히는 문제가 있었습니다).
    effective_date = date_iso[:10] if date_iso else datetime.now().strftime("%Y-%m-%d")
    html_content = _inject_update_date(html_content, effective_date, as_blocks=as_blocks)
    # 구조화 데이터(Article)의 datePublished/dateModified도 같은 날짜로 맞춥니다.
    schema_text = _apply_schema_dates(schema_text, effective_date)

    # as_blocks(숍 채널)면 스키마·관련 글 같은 원본 HTML을 "사용자 지정 HTML" 블록으로 감싸서,
    # 블록 밖에 남은 HTML이 클래식 블록으로 묶이지 않게 합니다.
    if schema_text:
        html_content += f"\n{_wrap_html_block(schema_text) if as_blocks else schema_text}"

    payload = {
        "title": title,
        "content": html_content,
        "status": status,
    }
    if date_iso:
        # 브라우저 날짜 입력칸(datetime-local)에서 오는 값은 "YYYY-MM-DDTHH:MM"처럼
        # 초(seconds)가 빠진 형태인데, 워드프레스 REST API는 초까지 있어야 유효한 날짜로
        # 인식합니다("유효하지 않은 날짜" 400 오류의 원인이었습니다). 없으면 ":00"을 채웁니다.
        if len(date_iso) == 16:  # "YYYY-MM-DDTHH:MM"
            date_iso = date_iso + ":00"
        payload["date"] = date_iso
    if featured_media_id:
        payload["featured_media"] = featured_media_id
    category_ids = resolve_term_ids(category_names, "categories") if category_names else []
    if category_ids:
        payload["categories"] = category_ids
    if category_ids or related_any_category or related_site_fill or related_priority_post_ids:
        # 같은 카테고리의 최신 글 5개를 "함께 보면 좋은 글"로 자동으로 붙입니다.
        # 이미 발행된 글만 대상이라 클러스터 형제글과 달리 항상 바로 채워집니다.
        # related_any_category=True면 카테고리와 상관없이 사이트 전체 최신 5개입니다.
        # related_site_fill=True(워드프레스 일반 채널)면 같은 카테고리 최신 글 먼저, 모자라면 사이트 전체 최신 글로 채우고
        # related_exclude_post_ids("기타 리뷰" 글의 워드프레스 ID)는 뺍니다.
        # "관련 글"은 부가 기능이라, 조회가 시간 초과되거나 실패해도 발행을 막지 않고 관련 글 없이 진행합니다.
        try:
            related_block = build_related_posts_block(
                category_ids[0] if category_ids else None,
                any_category=related_any_category,
                exclude_post_ids=related_exclude_post_ids,
                site_fill=related_site_fill,
                priority_post_ids=related_priority_post_ids,
                future_before=related_future_before,
                future_urls=related_future_urls,
            )
        except Exception as e:  # noqa: BLE001 — 시간 초과·응답 오류 등 무엇이든 발행을 막지 않게
            print(f"[워드프레스] 경고 — 관련 글 조회 실패, 관련 글 없이 발행을 계속합니다: {type(e).__name__} {e}"[:220])
            related_block = ""
        if related_block:
            html_content += _wrap_html_block(related_block) if as_blocks else related_block
            payload["content"] = html_content
    if tag_names:
        payload["tags"] = resolve_term_ids(tag_names, "tags")

    meta = {}
    if meta_description:
        meta_description = clean_rank_math_description(meta_description)
    if focus_keyword:
        meta["rank_math_focus_keyword"] = focus_keyword
    if meta_description:
        meta["rank_math_description"] = meta_description
        # 요약(excerpt)을 따로 안 보내면 목록 카드 요약에 본문 첫 줄("📅 최종 업데이트…")이 보입니다.
        # 메타 디스크립션이 있을 때만 그걸 요약으로 씁니다 (없으면 예전처럼 비워 둠).
        payload["excerpt"] = meta_description
    rank_math_route = uses_rank_math_route()
    if rank_math_route and meta.get("rank_math_focus_keyword"):
        meta["rank_math_focus_keyword"] = clean_focus_keyword(meta["rank_math_focus_keyword"])
    if meta and not rank_math_route:
        payload["meta"] = meta

    try:
        # 글 생성은 자동 재시도하지 않습니다(retries=0): 서버에서는 이미 만들어졌는데 응답만 늦은 것일 수 있어서,
        # 같은 요청을 다시 보내면 글이 중복됩니다. 대신 아래에서 같은 제목의 글이 생겼는지 조회합니다.
        response = _request("post", _api_url("posts"), retries=0, headers=_auth_header(), json=payload)
    except (requests.Timeout, requests.ConnectionError) as request_error:
        existing = _find_recent_post_by_title(title)
        if existing:
            print(f"[워드프레스] 글 발행 응답은 실패했지만({type(request_error).__name__}) 같은 제목의 글이 이미 생성돼 있어 성공으로 처리합니다 (id={existing['id']}).")
            return existing
        raise WordPressPublishError(
            "글 발행 요청이 시간 초과(또는 연결 오류)로 실패했고, 같은 제목의 글이 생겼는지 확인했지만 찾지 못했습니다.\n"
            "워드프레스 관리자에서 글이 생겼는지 먼저 확인한 뒤(예약글·임시글 포함), 없을 때만 다시 시도하세요. "
            "다시 누르면 글이 중복될 수 있습니다."
        ) from request_error
    _raise_for_status_with_detail(response, "글 발행")
    data = _safe_json(response, "글 발행")
    result = {"id": data["id"], "link": data["link"], "status": data["status"]}
    if ensure_featured:
        # 롱텀: 응답의 대표이미지가 비어 있으면(featured_media=0) 표지 미디어 ID를 알 때 한 번만 다시 지정합니다.
        # 그래도 비어 있으면 호출한 쪽이 최종본 화면에 경고를 띄울 수 있게 featured_media_missing=True로 알립니다.
        current = data.get("featured_media") or 0
        if not current and featured_media_id:
            try:
                set_featured_media(data["id"], featured_media_id)
                current = featured_media_id
            except Exception as e:  # noqa: BLE001 — 글은 이미 만들어졌으니 발행은 성공으로 두고 경고만 남깁니다
                print(f"[워드프레스] 경고 — 대표이미지 다시 지정 실패 (id={data['id']}): {type(e).__name__} {e}"[:220])
        result["featured_media"] = current
        result["featured_media_missing"] = not current
    if rank_math_route and meta:
        # 글은 이미 만들어졌으므로, Rank Math 저장이 실패해도 발행은 성공으로 두고 경고만 남깁니다.
        try:
            rank_math_update_meta(data["id"], meta)
            result["rank_math_saved"] = True
        except Exception as e:  # noqa: BLE001
            result["rank_math_saved"] = False
            print(f"[워드프레스] 경고 — 글은 발행됐지만 Rank Math 설명·키워드 저장 실패 (id={data['id']}): {type(e).__name__} {e}"[:260])
    return result


_UNSUPPORTED_PERMALINK_TAG = re.compile(r"%(?!postname%|pagename%)[a-z_0-9]+%")


def canonical_url_from_post(data: dict) -> str | None:
    """
    REST 응답(context=edit)에서 이 글의 정식 주소를 만듭니다.
      - 이미 공개된 글: link가 정식 주소(고유주소 형태)이므로 그대로 씁니다 (link가 "?p=ID" 형태면 아래 방식으로 넘어감)
      - 예약·임시 글: REST의 link는 "?p=ID"라서 못 쓰고, permalink_template("…/%postname%/") + slug
        (아직 slug가 비어 있으면 generated_slug)로 발행 후 주소를 미리 만듭니다.
    고유주소 구조에 %category%·%year% 같은 다른 태그가 섞여 있으면 정확히 못 만드므로 None을 돌려줍니다.
    """
    link = data.get("link") or ""
    if data.get("status") == "publish" and link and "?p=" not in link and "?page_id=" not in link:
        return link
    template = data.get("permalink_template") or ""
    slug = data.get("slug") or data.get("generated_slug") or ""
    if not template or not slug or _UNSUPPORTED_PERMALINK_TAG.search(template):
        return None
    return template.replace("%postname%", slug).replace("%pagename%", slug)


def get_canonical_url(post_id: int) -> str | None:
    """이 글의 정식 주소를 REST(context=edit) 조회(GET)로 알아냅니다. 못 알아내면 None (호출한 쪽이 기존 주소로 대체)."""
    fields = "id,status,link,slug,generated_slug,permalink_template"
    response = _request("get", _api_url(f"posts/{post_id}"), headers=_auth_header(), params={"context": "edit", "_fields": fields})
    if not response.ok:
        return None
    return canonical_url_from_post(_safe_json(response, "정식 주소 조회"))


def _find_recent_post_by_title(title: str, within_minutes: int = 15) -> dict | None:
    """
    같은 제목의 글(예약·공개·임시)이 최근에 생겼는지 REST로 한 번 조회합니다. 있으면 {"id","link","status"},
    없거나 이 조회도 실패하면 None. 발행 요청이 시간 초과됐을 때 중복 발행을 막는 용도입니다.
    """
    try:
        response = _request(
            "get",
            _api_url("posts"),
            headers=_auth_header(),
            params={"search": title, "status": "publish,future,draft,pending", "per_page": 10,
                    "orderby": "date", "order": "desc", "context": "edit"},
        )
        if not response.ok:
            return None
        now = datetime.utcnow()
        for post in _safe_json(response, "최근 글 확인"):
            raw_title = (post.get("title") or {}).get("raw") or (post.get("title") or {}).get("rendered", "")
            if _html.unescape(raw_title).strip() != title.strip():
                continue
            try:
                modified = datetime.strptime(post["modified_gmt"][:19], "%Y-%m-%dT%H:%M:%S")
            except (KeyError, ValueError):
                continue
            if (now - modified).total_seconds() <= within_minutes * 60:
                return {"id": post["id"], "link": post["link"], "status": post["status"], "recovered": True}
    except Exception:  # noqa: BLE001 — 확인용 조회 실패는 무시하고 원래 시간 초과 오류로 처리
        return None
    return None



def update_post_via_rest(
    post_id: int,
    html_content: str,
    schema_text: str = "",
    focus_keyword: str | None = None,
    category_names: list[str] | None = None,
    category_ids: list[int] | None = None,
    strip_cover: bool = False,
    related_priority_post_ids=None,
    related_exclude_post_ids=None,
) -> dict:
    """
    이미 발행된 글의 본문을 업데이트합니다. (related_exclude_post_ids: "함께 보면 좋은 글"에서 뺄 글 번호들 — 묶음 정리 전용, 기본값이면 예전과 같음) 형제글이 나중에 추가로 발행돼서 내부링크가
    새로 채워졌을 때, 이미 나간 글을 다시 열어서 최신 상태로 맞추는 용도입니다.
    category_ids(또는 category_names)를 넘기면 "함께 보면 좋은 글" 블록도 최신 상태로
    다시 만들어서 붙입니다 (그 사이 같은 카테고리에 새 글이 늘었을 수 있으므로).
    category_ids가 있으면 이름 검색을 거치지 않고 그대로 씁니다 — 재동기화처럼 워드프레스에
    이미 지정된 카테고리를 그대로 재사용할 때 씁니다.
    strip_cover=True면 본문의 표지 이미지를 빼고 보냅니다(create_post_via_rest와 같은 규칙 — 재동기화 때 다시 들어가지 않게).
    반환: {"id": 글ID, "link": 실제 발행 주소}
    """
    _require_site_configured()

    html_content = re.sub(r"^\s*<h1>.*?</h1>", "", html_content, count=1)
    if strip_cover:
        html_content = _strip_cover_image(html_content)
    # 재동기화할 때마다 "최종 업데이트" 날짜를 오늘 날짜로 새로 갱신합니다 — 이게 사실 재동기화의
    # 숨은 장점입니다: 재동기화를 누를 때마다 이 글이 "관리되고 있다"는 신호가 실제로 남습니다.
    html_content = _inject_update_date(html_content, datetime.now().strftime("%Y-%m-%d"))
    # 예전에 붙였던 "함께 보면 좋은 글" 블록이 이미 본문 끝에 있으면, 새로 만든 것과 중복되지
    # 않도록 먼저 떼어내고 다시 붙입니다. (블록 스타일이 나중에 바뀌어도 계속 인식하도록
    # 정확한 style 문자열이 아니라 구조로만 매칭합니다.)
    html_content = re.sub(
        r'<div style="[^"]*">\s*<strong[^>]*>함께 보면 좋은 글</strong>.*?</div>\s*$',
        "",
        html_content,
        flags=re.DOTALL,
    )
    if schema_text:
        html_content += f"\n{schema_text}"

    resolved_category_ids = category_ids or (resolve_term_ids(category_names, "categories") if category_names else [])
    if resolved_category_ids or related_priority_post_ids:
        # related_priority_post_ids(롱텀 재동기화): 같은 묶음의 이미 공개된 글을 먼저, 모자라면 같은 카테고리 최신 글 (없으면 예전과 같음)
        related_block = build_related_posts_block(
            resolved_category_ids[0] if resolved_category_ids else None,
            exclude_post_id=post_id,
            priority_post_ids=related_priority_post_ids,
            exclude_post_ids=related_exclude_post_ids,
        )
        if related_block:
            html_content += related_block

    payload = {"content": html_content}
    if focus_keyword:
        payload["meta"] = {"rank_math_focus_keyword": focus_keyword}

    # 같은 글의 본문을 덮어쓰는 요청이라 다시 보내도 중복되지 않습니다 (재시도 허용).
    response = _request("post", _api_url(f"posts/{post_id}"), headers=_auth_header(), json=payload)
    _raise_for_status_with_detail(response, "글 업데이트")
    data = _safe_json(response, "글 업데이트")
    return {"id": data["id"], "link": data["link"]}


def publish_post(
    title: str,
    html_content: str,
    schema_text: str = "",
    status: str = "publish",  # "publish" | "future" | "draft"
    date_iso: str | None = None,  # 예약 발행 시 "2026-09-05T09:00:00" 형식 (사이트 시간대 기준)
    category_names: list[str] | None = None,
    tag_names: list[str] | None = None,
    static_folder: str = "static",
    focus_keyword: str | None = None,
    meta_description: str | None = None,
    set_media_meta: bool = False,
    as_blocks: bool = False,
    strip_cover: bool = False,
    related_any_category: bool = False,
    related_site_fill: bool = False,
    related_exclude_post_ids=None,
    related_priority_post_ids=None,
    related_future_before: datetime | None = None,
    related_future_urls: dict | None = None,
    cover_fallback: bool = False,
) -> dict:
    """워드프레스에 실제로 글을 만듭니다 (즉시발행 또는 예약발행). 이미지 업로드까지 REST API로
    한 번에 처리합니다 — 호스팅사가 이미지 업로드 요청을 차단하지 않는 경우에만 쓰세요.
    (그런 경우엔 wp_browser_publisher.py 쪽을 대신 쓰세요.)
    반환: {"id": 글ID, "link": 실제 발행 주소, "status": 최종 상태, "uploaded_images": {로컬주소: 실제주소}}
    글 만들기 단계에서 실패하면, 이미 올라간 이미지 매핑을 예외의 uploaded_images 속성에 붙여서 다시 던집니다(저장본에 반영할 수 있게).
    """
    _require_site_configured()

    # 1) 본문 안의 로컬 이미지를 전부 업로드하고 주소를 실제 워드프레스 주소로 치환
    uploaded_images: dict = {}
    try:
        content, featured_media_id = _replace_local_image_srcs(html_content, static_folder, set_media_meta, collect=uploaded_images)
    except Exception as e:  # noqa: BLE001 — 일부만 올라간 채로 실패해도, 올라간 것은 호출한 쪽이 기록할 수 있게
        e.uploaded_images = dict(uploaded_images)
        raise

    if cover_fallback and not featured_media_id:
        # 롱텀: 이전 시도에서 이미 올라간 표지 주소가 저장본에 들어 있으면 새로 업로드한 이미지가 없어 대표이미지가 비게 됩니다.
        # 본문의 표지 실제 주소와 정확히 같은 미디어를 찾아 대표이미지로 씁니다(조회 실패·없음이면 None 그대로).
        featured_media_id = find_cover_media_id_in_content(content)

    try:
        result = create_post_via_rest(
            title=title,
            html_content=content,
            schema_text=schema_text,
            status=status,
            date_iso=date_iso,
            category_names=category_names,
            tag_names=tag_names,
            featured_media_id=featured_media_id,
            focus_keyword=focus_keyword,
            meta_description=meta_description,
            as_blocks=as_blocks,
            strip_cover=strip_cover,
            related_any_category=related_any_category,
            related_site_fill=related_site_fill,
            related_exclude_post_ids=related_exclude_post_ids,
            related_priority_post_ids=related_priority_post_ids,
            related_future_before=related_future_before,
            related_future_urls=related_future_urls,
            ensure_featured=cover_fallback,
        )
    except Exception as e:  # noqa: BLE001
        e.uploaded_images = dict(uploaded_images)
        raise
    result["uploaded_images"] = uploaded_images
    return result


# ── 롱텀 표지 복구: 본문 맨 위에 표지(seo-cover-img)가 빠진 글에 표지만 다시 끼워 넣습니다 ──────────────────

def restore_cover_in_post(post_id: int, alt_hint: str | None = None, dry_run: bool = False) -> dict:
    """
    워드프레스 글 하나의 현재 본문(GET ?context=edit 의 content.raw)에 표지 이미지(seo-cover-img)가 없으면,
    대표이미지(featured_media)의 원본 주소로 <img class="seo-cover-img" src alt>를 본문 맨 앞에 끼워 넣고 content만 업데이트합니다.
    - 이미 seo-cover-img가 있으면 아무것도 보내지 않습니다(여러 번 실행해도 안전).
    - 대표이미지가 없거나 주소를 못 알아내면 건너뜁니다(이유를 돌려줌).
    - 제목·발행일·상태·예약 시간·슬러그·카테고리·태그·대표이미지·본문의 나머지는 보내지 않거나(content만 전송) 한 글자도 바꾸지 않습니다.
      맨 앞에 끼우므로 기존 "최종 업데이트" 줄은 표지 바로 아래에 오게 됩니다.
    alt_hint(앱에 저장된 표지 alt)가 있으면 그걸, 없으면 미디어의 alt_text를 씁니다.
    dry_run=True면 아무것도 전송하지 않고 같은 정보(+ new_content)만 돌려줍니다.
    반환: {"status": "restored"|"already"|"skipped"|"would_restore", "reason", "featured_media", "src", "alt", "new_content"}
    """
    _require_site_configured()
    response = _request(
        "get", _api_url(f"posts/{post_id}"), headers=_auth_header(),
        params={"context": "edit", "_fields": "id,content,featured_media"},
    )
    _raise_for_status_with_detail(response, "글 본문 조회")
    data = _safe_json(response, "글 본문 조회")
    raw = (data.get("content") or {}).get("raw")
    if not isinstance(raw, str):
        raise WordPressPublishError("현재 글 본문 원본(content.raw)을 가져오지 못했습니다.")
    featured = data.get("featured_media") or 0
    info = {"status": "", "reason": "", "featured_media": featured, "src": "", "alt": "", "new_content": ""}
    if "seo-cover-img" in raw:
        info.update(status="already", reason="본문에 이미 표지가 있음")
        return info
    if not featured:
        info.update(status="skipped", reason="대표이미지가 없음")
        return info
    media_resp = _request(
        "get", _api_url(f"media/{featured}"), headers=_auth_header(), params={"_fields": "id,source_url,alt_text"}
    )
    if not media_resp.ok:
        info.update(status="skipped", reason=f"대표이미지(미디어 {featured}) 조회 실패(HTTP {media_resp.status_code})")
        return info
    media = _safe_json(media_resp, "대표이미지 조회")
    src = media.get("source_url") or ""
    if not src:
        info.update(status="skipped", reason=f"대표이미지(미디어 {featured})의 주소를 알 수 없음")
        return info
    alt = (alt_hint or "").strip() or (media.get("alt_text") or "").strip()
    img = f'<img class="seo-cover-img" src="{src}" alt="{_html.escape(alt, quote=True)}">'
    info.update(src=src, alt=alt, new_content=img + raw)
    if dry_run:
        info["status"] = "would_restore"
        return info
    # content만 보냅니다(다른 필드는 전혀 보내지 않음). 같은 글 본문을 덮어쓰는 요청이라 재시도해도 중복되지 않습니다.
    update = _request("post", _api_url(f"posts/{post_id}"), headers=_auth_header(), json={"content": info["new_content"]})
    _raise_for_status_with_detail(update, "표지 다시 넣기(글 업데이트)")
    info["status"] = "restored"
    return info


# 본문 맨 끝의 "함께 보면 좋은 글" 블록(<!-- wp:html --> 감싸개가 있으면 함께)을 찾는 패턴.
_RELATED_BLOCK_AT_END = re.compile(
    r'(?:<!-- wp:html -->\s*)?<div style="[^"]*">\s*<strong[^>]*>함께 보면 좋은 글</strong>.*?</div>\s*'
    r'(?:<!-- /wp:html -->\s*)?$',
    flags=re.DOTALL,
)


def build_refreshed_related_content(raw_content: str, new_block: str) -> tuple[str, int]:
    """
    본문 끝의 기존 "함께 보면 좋은 글" 블록만 떼어내고 새 블록(wp:html 감싸개 포함)을 붙인 본문을 돌려줍니다.
    반환: (새 본문, 떼어낸 기존 블록 개수 0 또는 1). 그 앞의 본문은 한 글자도 바꾸지 않습니다.
    떼어낸 뒤에도 "함께 보면 좋은 글" 글자가 남아 있으면(끝이 아닌 곳에 있는 등) 중복을 막기 위해 오류로 처리합니다.
    """
    stripped, removed = _RELATED_BLOCK_AT_END.subn("", raw_content, count=1)
    if "함께 보면 좋은 글" in stripped:
        raise WordPressPublishError(
            "본문에서 기존 '함께 보면 좋은 글' 블록의 위치를 정확히 알아내지 못했습니다(끝부분이 아닌 곳에 있음). "
            "중복을 막기 위해 업데이트하지 않았습니다."
        )
    return stripped + _wrap_html_block(new_block), removed


def related_block_links(block_or_content: str) -> list[tuple[str, str]]:
    """"함께 보면 좋은 글" 블록(또는 그 블록이 끝에 붙은 본문)에서 [(링크, 제목)] 순서대로. 블록이 없으면 빈 목록."""
    m = _RELATED_BLOCK_AT_END.search(block_or_content or "")
    if not m:
        return []
    return [
        (href, _html.unescape(re.sub(r"<[^>]+>", "", inner)).strip())
        for href, inner in re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', m.group(0), flags=re.DOTALL)
    ]


def refresh_related_posts_block(
    post_id: int, dry_run: bool = False, category_first: bool = False, exclude_post_ids=None,
    future_before: datetime | None = None, future_urls: dict | None = None, only_if_changed: bool = False,
) -> dict:
    """
    이미 발행된 글의 "함께 보면 좋은 글"만 사이트 전체 최신 5개(자기 자신 제외)로 바꿉니다.
    category_first=True(워드프레스 일반 규칙)면 이 글 카테고리의 최신 공개 글 먼저, 모자라면 사이트 전체 최신 글로 채우고
    exclude_post_ids(기타 리뷰 글의 워드프레스 ID)는 뺍니다. 기본값이면 예전과 똑같이 사이트 전체 최신 5개입니다.
    GET posts/{id}?context=edit 로 본문 원본(content.raw)을 가져와 끝의 기존 블록만 교체하고, content만 POST합니다.
    다른 필드(제목·카테고리·태그·대표이미지·메타)는 보내지 않습니다. 조회·블록 생성이 실패하면 업데이트하지 않고 오류를 냅니다.
    dry_run=True면 아무것도 전송하지 않고 {"raw","block","after","removed"}만 돌려줍니다.
    future_before·future_urls(싼세계): build_related_posts_block 설명 참고 — 예약 글 포함 규칙.
    결과에는 항상 "old_links"·"new_links"([(링크, 제목)])와 "changed"(추천 링크 목록이 달라지는지)가 들어갑니다.
    only_if_changed=True면 추천 링크가 그대로일 때는 전송하지 않습니다(바뀌는 글만 업데이트).
    """
    _require_site_configured()
    response = _request(
        "get", _api_url(f"posts/{post_id}"), headers=_auth_header(), params={"context": "edit", "_fields": "id,content"}
    )
    _raise_for_status_with_detail(response, "글 본문 조회")
    data = _safe_json(response, "글 본문 조회")
    raw = (data.get("content") or {}).get("raw")
    if not isinstance(raw, str) or not raw:
        raise WordPressPublishError("글 본문 원본(content.raw)을 가져오지 못해 업데이트하지 않았습니다.")

    if category_first:
        category_ids = get_post_category_ids(post_id)  # 이 글이 워드프레스에서 실제로 갖고 있는 카테고리
        block = build_related_posts_block(
            category_ids[0] if category_ids else None,
            exclude_post_id=post_id,
            exclude_post_ids=exclude_post_ids,
            site_fill=True,
            future_before=future_before,
            future_urls=future_urls,
        )
    else:
        block = build_related_posts_block(None, exclude_post_id=post_id, any_category=True)
    if not block:
        raise WordPressPublishError("새 '함께 보면 좋은 글' 목록을 만들지 못해(조회 실패 또는 글 없음) 업데이트하지 않았습니다.")
    after, removed = build_refreshed_related_content(raw, block)
    old_links = related_block_links(raw)
    new_links = related_block_links(block)
    result = {
        "raw": raw, "block": block, "after": after, "removed": removed,
        "old_links": old_links, "new_links": new_links,
        "changed": [h for h, _ in old_links] != [h for h, _ in new_links],
    }
    if dry_run or (only_if_changed and not result["changed"]):
        return result

    update = _request("post", _api_url(f"posts/{post_id}"), headers=_auth_header(), json={"content": after})
    _raise_for_status_with_detail(update, "글 업데이트(함께 보면 좋은 글 새로고침)")
    return result
