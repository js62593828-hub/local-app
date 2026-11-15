"""
숍 워드프레스 전용 "재동기화" — 이미 발행된 글을 저장된 최신 최종본(platform_final)으로 덮어씁니다.

롱텀용 재동기화(app._resync_wordpress_post)는 롱텀 사이트로 브라우저 로그인해서 이미지를 올리는 방식이라
숍 워드프레스에는 쓸 수 없고, 단순히 publish_post처럼 올리면 재동기화할 때마다 이미지가 중복 업로드됩니다.
이 모듈은 REST만 쓰고, "이미 올라간 이미지는 재사용 / 바뀐 것만 업로드"합니다.

- 이미지 비교: 저장본의 로컬 이미지 파일명 vs (1) 워드프레스에 올라가 있는 현재 글 본문의 이미지 (2) 미디어 라이브러리.
  워드프레스가 붙이는 -scaled, -가로x세로, -1 같은 접미사와 확장자(png→jpg 압축)는 무시하고 비교합니다.
- dry_run=True면 조회(GET)만 하고 워드프레스에 아무것도 쓰지 않습니다(저장도 안 함).
- 바꾸지 않는 것: 발행일·상태(예약 포함)·슬러그·카테고리·태그.
"""
import html as _html
import os
import re
import unicodedata
import urllib.parse
from datetime import datetime

import requests

import metadata_parser
import shop_formatter
import wp_publisher as W


class ResyncError(W.WordPressPublishError):
    """재동기화를 중단한 오류. changed=False면 워드프레스 글은 바뀌지 않은 상태입니다."""

    def __init__(self, message: str, changed: bool = False):
        super().__init__(message)
        self.changed = changed


# ---------- 이미지 파일명 비교 ----------

_KEY_SUFFIXES = (re.compile(r"-scaled$"), re.compile(r"-\d+x\d+$"), re.compile(r"-\d+$"))
_SIZE_VARIANT_URL = re.compile(r"-\d+x\d+(\.[A-Za-z0-9]+)$")


def media_key(name_or_url: str) -> str:
    """파일명(또는 주소)에서 확장자와 워드프레스 접미사(-scaled, -가로x세로, -N)를 뺀 비교용 이름."""
    base = urllib.parse.unquote((name_or_url or "").split("?")[0].rsplit("/", 1)[-1])
    stem = unicodedata.normalize("NFC", os.path.splitext(base)[0]).lower()
    changed = True
    while changed:
        changed = False
        for pattern in _KEY_SUFFIXES:
            new = pattern.sub("", stem)
            if new != stem:
                stem, changed = new, True
    return stem


def _full_size_url(url: str) -> str:
    """본문 이미지 주소가 '-768x432.jpg' 같은 축소본이면 원본 주소로 되돌립니다."""
    return _SIZE_VARIANT_URL.sub(r"\1", url)


_IMG_TAG = re.compile(r"<img\b[^>]*>")
_SRC_ATTR = re.compile(r'\bsrc="([^"]+)"')
_WP_IMAGE_CLASS = re.compile(r"wp-image-(\d+)")


def _live_images(live_raw: str) -> dict:
    """현재 워드프레스 본문 안 이미지: {비교용이름: {"url","id"}} (처음 나온 것 우선)."""
    found = {}
    for tag in _IMG_TAG.findall(live_raw or ""):
        src = _SRC_ATTR.search(tag)
        if not src:
            continue
        key = media_key(src.group(1))
        if not key or key in found:
            continue
        m = _WP_IMAGE_CLASS.search(tag)
        found[key] = {"url": _full_size_url(src.group(1)), "id": int(m.group(1)) if m else None}
    return found


def _load_media_index(cache: dict) -> dict:
    """미디어 라이브러리 전체(이미지)를 페이지를 넘기며 조회(GET)해서 {비교용이름: [(id, url), ...]}(최신순)로 만듭니다.
    파일명 검색(search=)은 제목만 보기 때문에(제목을 alt로 바꿔 올림) 못 찾을 수 있어 전체 목록을 받습니다."""
    if "media_index" in cache:
        return cache["media_index"]
    index: dict = {}
    page = 1
    while page <= 60:
        response = W._request(
            "get", W._api_url("media"), headers=W._auth_header(),
            params={"per_page": 100, "page": page, "orderby": "date", "order": "desc", "_fields": "id,source_url,media_type"},
        )
        if response.status_code == 400 and page > 1:  # 마지막 페이지 너머
            break
        W._raise_for_status_with_detail(response, "미디어 라이브러리 조회")
        for item in W._safe_json(response, "미디어 라이브러리 조회"):
            if item.get("media_type") not in (None, "image"):
                continue
            key = media_key(item.get("source_url") or "")
            if key:
                index.setdefault(key, []).append((item["id"], item["source_url"]))
        total_pages = int(response.headers.get("X-WP-TotalPages", page))
        if page >= total_pages:
            break
        page += 1
    cache["media_index"] = index
    return index


_LOCAL_STATIC_SRC = re.compile(r'src="(/static/[^"]*)"')


def leftover_local_srcs(body: str) -> list[str]:
    """워드프레스로 보낼 본문에 아직 남아 있는 로컬 이미지 주소(src="/static/…")를 중복 없이 순서대로 돌려줍니다.
    하나라도 있으면 그대로 보내면 깨진 주소가 실제 글에 덮어써지므로, 보내기 전에 이걸로 막습니다."""
    return list(dict.fromkeys(_LOCAL_STATIC_SRC.findall(body or "")))


def local_images_left_message(srcs: list[str]) -> str:
    names = ", ".join(os.path.basename(urllib.parse.unquote(s)) for s in srcs)
    return f"로컬 이미지 {len(srcs)}장을 워드프레스에서 찾지 못해 재동기화를 멈췄습니다: {names}"


# ---------- 본문 조립 ----------

_OLD_DISCLOSURE = re.compile(
    r"<!-- wp:paragraph -->\s*<p>\s*💡\s*이 포스팅은 쿠팡 파트너스[^<]*</p>\s*<!-- /wp:paragraph -->\n?"
)


def replace_old_disclosure(content: str) -> str:
    """예전 '💡 …' 한 줄 대가성 문구 블록을 새 두 줄 문구 블록으로 바꿉니다(없으면 그대로)."""
    return _OLD_DISCLOSURE.sub(lambda _m: shop_formatter._wp_disclosure_block(), content)


def _apply_image_map(content: str, image_map: dict) -> str:
    """로컬 이미지 주소를 실제 주소로 바꾸고, 이미지 블록에는 미디어 ID를 심습니다(발행 때와 같은 방식)."""
    for src, found in image_map.items():
        if found.get("id"):
            content = W._inject_media_id_into_image_block(content, src, found["url"], found["id"])
        content = content.replace(f'src="{src}"', f'src="{found["url"]}"')
    return content


def _fetch_live_post(post_id: int) -> dict:
    response = W._request(
        "get", W._api_url(f"posts/{post_id}"), headers=W._auth_header(),
        params={"context": "edit", "_fields": "id,status,slug,title,content,featured_media,categories"},
    )
    W._raise_for_status_with_detail(response, "워드프레스 현재 글 조회")
    data = W._safe_json(response, "워드프레스 현재 글 조회")
    raw = (data.get("content") or {}).get("raw")
    if not isinstance(raw, str):
        raise W.WordPressPublishError("현재 글 본문 원본(content.raw)을 가져오지 못했습니다.")
    return data


def compose_body(
    stored_final: str, *, schema_text: str, published_date: str, today: str, strip_cover: bool,
    related_block: str, existing_related: str, outgoing_fn,
) -> str:
    """발행(create_post_via_rest, as_blocks=True)과 같은 규칙으로 워드프레스에 보낼 본문을 만듭니다."""
    html_content = outgoing_fn(stored_final)
    html_content = re.sub(r"^\s*<h1>.*?</h1>", "", html_content, count=1)
    if strip_cover:
        html_content = W._strip_cover_image(html_content)
    html_content = W._inject_update_date(html_content, today, as_blocks=True)
    # 저장본에 '함께 보면 좋은 글'이 이미 끝에 붙어 있으면 떼어내고(wp:html 감싸개 포함) 새로 붙입니다.
    html_content, _ = W._RELATED_BLOCK_AT_END.subn("", html_content, count=1)
    if "함께 보면 좋은 글" in html_content:
        raise ResyncError("저장본에서 기존 '함께 보면 좋은 글' 블록의 위치를 정확히 알아내지 못해 중복을 막기 위해 중단했습니다. 워드프레스 글은 바뀌지 않았습니다.")
    schema_text = W._apply_schema_dates(schema_text, published_date or today, modified_str=today)
    if schema_text:
        html_content += f"\n{W._wrap_html_block(schema_text)}"
    if related_block:
        html_content += W._wrap_html_block(related_block)
    elif existing_related:
        html_content += existing_related  # 새 목록을 못 만들었으면 지금 올라가 있는 블록을 그대로 둡니다
    return html_content


# ---------- 덮어쓰기 안전장치 ----------

_RELATED_CARDS_FREE = W._RELATED_BLOCK_AT_END
_IFRAME_SRC = re.compile(r"<iframe\b[^>]*?\bsrc=\"([^\"]*)\"", re.I)
_LINKED_IMG = re.compile(r"<a\s[^>]*?href=\"([^\"]*)\"[^>]*>\s*<img\b", re.I)
_PLACEHOLDER_PATTERNS = (
    ("[쿠팡링크]/[구매링크] 자리표시", re.compile(r"\[\s*(?:쿠팡|구매)\s*링크\s*\]")),
    ("{{SHOP:…}} 자리표시", re.compile(r"\{\{SHOP:[^}]*\}\}")),
    ('"링크:" 줄', re.compile(r"(?:^|>|\n)\s*링크\s*[:：][^<\n]*")),
)


def _without_related(html_text: str) -> str:
    """끝의 '함께 보면 좋은 글'(카드 안의 링크·썸네일은 비교 대상이 아님)을 뺀 본문."""
    return _RELATED_CARDS_FREE.sub("", html_text or "", count=1)


def evaluate_guard(live_raw: str, outgoing_body: str, stored_text: str | None = None) -> dict:
    """
    덮어쓰기 전 검사(조회한 현재 글 vs 보낼 본문, 쓰기 요청 없음).
    hard: 보낼 본문에 남은 자리표시([쿠팡링크]·[구매링크]·{{SHOP:…}}·"링크:" 줄) — 무조건 막음.
    soft: 현재 글에는 있는데 보낼 본문에는 없는 직접 편집분(쿠팡 iframe·이미지에 걸린 링크·현재 글에만 있는 이미지) — 확인하면 진행.
    각 항목은 {"label","count","items"}.
    """
    live = _without_related(live_raw)
    body = _without_related(outgoing_body)
    hard, soft = [], []

    # 자리표시는 보내기 직전에 일부({{SHOP:}} 등)가 지워지므로, 지우기 전 저장본(stored_text)을 기준으로 찾습니다.
    placeholder_source = _without_related(stored_text) if stored_text is not None else body
    for label, pattern in _PLACEHOLDER_PATTERNS:
        found = [m.group(0).strip(" >\n") for m in pattern.finditer(placeholder_source)]
        if found:
            hard.append({"label": label, "count": len(found), "items": found[:5]})

    live_iframes = _IFRAME_SRC.findall(live)
    lost = len(live_iframes) - len(_IFRAME_SRC.findall(body))
    if lost > 0:
        soft.append({"label": "쿠팡 iframe 코드", "count": lost, "items": live_iframes[:5]})

    live_links = _LINKED_IMG.findall(live)
    lost = len(live_links) - len(_LINKED_IMG.findall(body))
    if lost > 0:
        soft.append({"label": "이미지에 걸린 링크", "count": lost, "items": live_links[:5]})

    body_keys = {media_key(u) for u in re.findall(r"<img\b[^>]*?\bsrc=\"([^\"]+)\"", body)}
    extra = {}
    for u in re.findall(r"<img\b[^>]*?\bsrc=\"([^\"]+)\"", live):
        k = media_key(u)
        if k and k not in body_keys and k not in extra:
            extra[k] = _full_size_url(u)
    if extra:
        soft.append({"label": "현재 글에만 있는 이미지(배너 등)", "count": len(extra), "items": list(extra.values())[:5]})

    return {"hard": hard, "soft": soft}


class ResyncGuardError(ResyncError):
    """덮어쓰기 안전장치에 걸려 워드프레스에 아무것도 보내지 않고 멈춘 경우."""

    def __init__(self, guard: dict):
        self.guard = guard
        lines = ["재동기화를 멈췄습니다. 워드프레스 글은 바뀌지 않았습니다."]
        for title, items in (("무조건 막는 항목(자리표시가 본문에 남아 있음)", guard["hard"]),
                             ("사라지게 되는 직접 편집분", guard["soft"])):
            if items:
                lines.append(f"[{title}]")
                lines += [f"- {i['label']}: {i['count']}개" for i in items]
        super().__init__("\n".join(lines), changed=False)


# ---------- 메인 ----------

def run(
    *, article: dict, keyword: str, post_id: int, static_folder: str, strip_cover: bool,
    outgoing_fn, save_final=None, dry_run: bool = False, today: str | None = None, media_cache: dict | None = None,
    confirm_overwrite: bool = False, related_opts: dict | None = None,
) -> dict:
    """
    article: get_article() 결과(수정하지 않음). save_final(new_final): 이미지 주소가 실제 주소로 바뀐 저장본을 저장하는 콜백
    (dry_run이면 부르지 않음). confirm_overwrite=True면 "직접 편집분이 사라져도 덮어쓰기"를 확인한 것으로 보고 경고(soft)만 넘어갑니다
    (자리표시가 남은 경우(hard)는 항상 막습니다). 반환 dict 키: images, reused, new, content, title, excerpt, meta, rank_math_saved ...
    """
    W._require_site_configured()
    cache = media_cache if media_cache is not None else {}
    today = today or datetime.now().strftime("%Y-%m-%d")
    post_id = int(post_id)
    site_url = (W._current_site().get("url") or "").rstrip("/")

    stored = article["platform_final"].get("wordpress") or ""
    if not stored:
        raise ResyncError("저장된 최종본이 없습니다. 워드프레스 글은 바뀌지 않았습니다.")
    new_stored = replace_old_disclosure(stored)

    try:
        live = _fetch_live_post(post_id)
    except requests.RequestException as e:
        raise ResyncError(f"워드프레스 현재 글을 조회하지 못했습니다: {e}\n워드프레스 글은 바뀌지 않았습니다.") from e
    except W.WordPressPublishError as e:
        raise ResyncError(f"{e}\n워드프레스 글은 바뀌지 않았습니다.") from e
    live_raw = live["content"]["raw"]
    # 끝의 '함께 보면 좋은 글' 카드 썸네일은 다른 글의 표지라 이미지 비교·집계에서 뺍니다.
    live_imgs = _live_images(W._RELATED_BLOCK_AT_END.sub("", live_raw, count=1))

    # ── 1) 이미지 분류: 재사용 / 새 업로드 ──
    local_srcs = sorted(set(re.findall(r'src="(/static/generated/[^"]+)"', new_stored)))
    entries, image_map = [], {}
    for src in local_srcs:
        key = media_key(src)
        local_path = os.path.join(static_folder, src.replace("/static/", "", 1))
        alt_m = re.search(r'<img[^>]*\bsrc="' + re.escape(src) + r'"[^>]*\balt="([^"]*)"', new_stored)
        alt = _html.unescape(alt_m.group(1)) if alt_m else ""
        entry = {"src": src, "filename": os.path.basename(src), "key": key, "alt": alt, "local_path": local_path,
                 "file_exists": os.path.exists(local_path)}
        found = live_imgs.get(key)
        origin = "현재 글 본문"
        if not found:
            try:
                hits = _load_media_index(cache).get(key)
            except (requests.RequestException, W.WordPressPublishError) as e:
                raise ResyncError(f"미디어 라이브러리를 조회하지 못했습니다: {e}\n워드프레스 글은 바뀌지 않았습니다.") from e
            if hits:
                found, origin = {"id": hits[0][0], "url": hits[0][1]}, "미디어 라이브러리"
        if found:
            entry.update(status="reuse", url=found["url"], id=found.get("id"), origin=origin)
            image_map[src] = {"url": found["url"], "id": found.get("id")}
        else:
            entry.update(status="upload", url=None, id=None, origin="")
        entries.append(entry)
    already_real = {
        m for m in re.findall(r'<img\b[^>]*\bsrc="(https?://[^"]+)"', new_stored)
        if site_url and m.startswith(site_url + "/wp-content/uploads/")
    }
    reused = sum(1 for e in entries if e["status"] == "reuse") + len(already_real)
    to_upload = [e for e in entries if e["status"] == "upload"]

    # ── 2) 보낼 값 계산 (조회만) ──
    category_ids = live.get("categories") or []
    related_block = ""
    if related_opts is not None:
        # 싼세계(wp_tips): 발행·"함께 보면 좋은 글 새로고침"과 같은 규칙 (같은 카테고리 먼저 → 모자라면 사이트 최신으로 채움, 기타 리뷰 제외)
        related_block = W.build_related_posts_block(
            category_ids[0] if category_ids else None, exclude_post_id=post_id, **related_opts
        )
    elif category_ids:
        related_block = W.build_related_posts_block(category_ids[0], exclude_post_id=post_id)
    existing = W._RELATED_BLOCK_AT_END.search(live_raw)
    existing_related = existing.group(0) if existing else ""

    meta_desc = W.clean_rank_math_description(
        metadata_parser.parse_metadata_memo(article["platform_metadata"].get("wordpress", "")).get("meta_description") or ""
    )
    focus = W.clean_focus_keyword(keyword)
    meta = {k: v for k, v in {"rank_math_focus_keyword": focus, "rank_math_description": meta_desc}.items() if v}
    title = article["platform_title"].get("wordpress") or keyword
    published_date = (article.get("platform_published_at") or {}).get("wordpress") or today

    def _body(final_text: str) -> str:
        return compose_body(
            final_text, schema_text=article["platform_schema"].get("wordpress", ""), published_date=published_date,
            today=today, strip_cover=strip_cover, related_block=related_block, existing_related=existing_related,
            outgoing_fn=outgoing_fn,
        )

    def _cover_media_id(mapping: dict, final_text: str):
        """대표이미지로 지정할 표지 미디어 ID: 이번에 처리한 표지 → 이미 실제 주소로 바뀐 표지 블록의 ID 순."""
        for src, found in mapping.items():
            if "cover" in src.lower() and found.get("id"):
                return found["id"]
        block = re.search(r'<!-- wp:image (\{[^}]*seo-cover-img[^}]*\}) -->', final_text)
        if block:
            m = re.search(r'"id":(\d+)', block.group(1))
            if m:
                return int(m.group(1))
        tag = re.search(r'<img[^>]*class="[^"]*seo-cover-img[^"]*"[^>]*>', final_text)
        m = tag and _WP_IMAGE_CLASS.search(tag.group(0))
        return int(m.group(1)) if m else None

    live_extra = {
        "live_iframes": live_raw.count("<iframe"),
        "live_figure_links": len(re.findall(r"<figure[^>]*>\s*<a\s", live_raw)),
        "live_images_not_in_stored": sum(
            1 for k in live_imgs if k not in {e["key"] for e in entries}
            and k not in {media_key(u) for u in already_real}
        ),
        "stored_placeholders": new_stored.count("[쿠팡링크]") + new_stored.count("[구매링크]"),
    }

    result = {
        "post_id": post_id, "images": entries, "reused": reused, "new": len(to_upload),
        "missing_files": [e["filename"] for e in to_upload if not e["file_exists"]],
        "unmatched": [e["filename"] for e in to_upload],
        "title": title, "excerpt": meta_desc, "meta": meta, "related_count": related_block.count("<a href"),
        "disclosure_replaced": new_stored != stored, "dry_run": dry_run, **live_extra,
    }

    # ── 2-1) 덮어쓰기 안전장치: 워드프레스에 아무것도 쓰기 전에(이미지 업로드 포함) 먼저 검사합니다 ──
    preview_map = dict(image_map)
    for e in to_upload:
        preview_map[e["src"]] = {"url": f"[새로 업로드 예정: {e['filename']}]", "id": None}
    preview_final = _apply_image_map(new_stored, preview_map)
    preview_body = _body(preview_final)
    guard = evaluate_guard(live_raw, preview_body, stored_text=preview_final)
    result["guard"] = guard

    if dry_run:
        result["content"] = preview_body
        result["cover_media_id"] = _cover_media_id(preview_map, preview_final)
        return result
    if guard["hard"] or (guard["soft"] and not confirm_overwrite):
        raise ResyncGuardError(guard)

    # ── 3) 실제 실행: 새 이미지만 업로드 ──
    def _persist():
        text = _apply_image_map(new_stored, image_map)
        if save_final and text != stored:
            save_final(text)
        return text

    for e in to_upload:
        try:
            if not e["file_exists"]:
                raise W.WordPressPublishError(f"이미지 파일을 찾을 수 없습니다: {e['local_path']}")
            uploaded = W.upload_media(e["local_path"], alt_text=e["alt"] or None, title=e["alt"] or None)
        except Exception as err:  # noqa: BLE001 — 어떤 오류든 본문은 건드리지 않고 중단
            try:
                _persist()  # 지금까지 올라간·재사용한 이미지 주소는 저장해서 다시 눌러도 중복되지 않게 합니다
            except Exception as save_err:  # noqa: BLE001 — 저장 실패가 원래 오류를 가리지 않게
                print(f"[숍 워드프레스 재동기화] 경고 — 이미지 주소 저장 실패: {type(save_err).__name__} {save_err}"[:220])
            raise ResyncError(
                f"이미지 업로드 중 오류 ({e['filename']}): {err}\n"
                f"지금까지 처리된 이미지 {len(image_map)}장의 주소는 저장했습니다 — 다시 눌러도 중복 업로드되지 않습니다.\n"
                "워드프레스 글은 바뀌지 않았습니다."
            ) from err
        image_map[e["src"]] = {"url": uploaded["source_url"], "id": uploaded["id"]}
        e.update(status="uploaded", url=uploaded["source_url"], id=uploaded["id"])

    final_text = _persist()
    result["content"] = body = _body(final_text)
    result["cover_media_id"] = cover_id = _cover_media_id(image_map, final_text)

    # 안전장치: 보낼 본문에 로컬 이미지 주소가 남아 있으면(예: /static/generated/ 밖의 경로) 깨진 주소가 실제 글에 덮어써지므로 멈춥니다.
    left = leftover_local_srcs(body)
    if left:
        raise ResyncError(f"{local_images_left_message(left)}\n워드프레스 글은 바뀌지 않았습니다.")

    # ── 4)글 업데이트 (본문·제목·대표이미지·요약만. 발행일·상태·카테고리·태그는 보내지 않음) ──
    payload = {"content": body}
    live_title = _html.unescape((live.get("title") or {}).get("raw") or "").strip()
    if title.strip() != live_title:
        payload["title"] = title
        if live.get("slug"):
            payload["slug"] = live["slug"]  # 제목이 바뀌어도 주소(슬러그)는 그대로
    if cover_id:
        payload["featured_media"] = cover_id
    if meta_desc:
        payload["excerpt"] = meta_desc
    try:
        response = W._request("post", W._api_url(f"posts/{post_id}"), headers=W._auth_header(), json=payload)
        W._raise_for_status_with_detail(response, "글 업데이트")
    except requests.RequestException as e:
        raise ResyncError(
            f"글 업데이트 요청이 시간 초과(또는 연결 오류)로 끝났습니다: {e}\n"
            "워드프레스에는 반영됐을 수도 있으니 글을 열어 확인해 주세요. 이미지 주소는 저장돼 있어 다시 눌러도 이미지는 중복되지 않습니다.",
            changed=True,
        ) from e
    except W.WordPressPublishError as e:
        raise ResyncError(f"{e}\n워드프레스 글은 바뀌지 않았습니다. (이미지 주소는 저장돼 있어 다시 눌러도 이미지는 중복되지 않습니다.)") from e

    result["rank_math_saved"] = None
    if meta:
        try:
            W.rank_math_update_meta(post_id, meta)
            result["rank_math_saved"] = True
        except Exception as e:  # noqa: BLE001 — 부가 기능: 경고만 남기고 재동기화는 성공으로 둡니다
            result["rank_math_saved"] = False
            print(f"[숍 워드프레스 재동기화] 경고 — Rank Math 설명·키워드 저장 실패 (id={post_id}): {type(e).__name__} {e}"[:260])
    return result
