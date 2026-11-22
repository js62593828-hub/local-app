"""
로컬 웹 대시보드 — 더블클릭으로 실행하면 브라우저가 자동으로 열립니다.
"""
import difflib
import html
import json
import io
import os
import re
import tempfile
import threading
import time
import uuid
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime, timedelta

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from PIL import Image, ImageOps
import requests
from urllib.parse import parse_qs, urlparse
from werkzeug.exceptions import HTTPException

import blog_overlap
import config
import content_parser
import image_gen
import link_check
import lt_cleanup
import main as engine
import metadata_parser
import perf_tracker
import seo_formatter
import shop_formatter
import shop_judge
import similarity
import wp_browser_publisher
import wp_publisher
import radar_board
import radar_cards
import radar_screen
import shop_wp_resync
import source_capture
import v4_board
import weekly_plan_export

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "local-app-single-user-dev-key")


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


@app.errorhandler(RuntimeError)
def handle_runtime_error(e):
    return render_template("error.html", message=str(e)), 500


@app.errorhandler(wp_publisher.WordPressPublishError)
def handle_wordpress_publish_error(e):
    return render_template("error.html", message=str(e)), 500


@app.errorhandler(wp_browser_publisher.WordPressBrowserPublishError)
def handle_wordpress_browser_publish_error(e):
    return render_template("error.html", message=str(e)), 500


@app.errorhandler(requests.RequestException)
def handle_api_error(e):
    # 이 핸들러는 네이버 API든 워드프레스 API든, requests 라이브러리로 호출하는 모든 외부 API
    # 오류를 공통으로 받습니다. 어느 쪽에서 났는지 특정할 수 없으니 문구를 한쪽으로 단정하지 않습니다.
    return render_template(
        "error.html",
        message=(
            "외부 API 호출 중 오류가 발생했습니다.\n\n"
            f"{e}\n\n"
            "· 키워드 조회 중이었다면: API 키(NAVER_CUSTOMER_ID / NAVER_API_KEY / NAVER_SECRET_KEY)가 정확한지, "
            "네이버 검색광고 사이트에서 API 사용 신청이 승인됐는지 확인해보세요.\n"
            "· 워드프레스 발행 중이었다면: .env의 WORDPRESS_URL / WORDPRESS_USERNAME / WORDPRESS_APP_PASSWORD가 "
            "정확한지, 워드프레스 사이트가 정상적으로 응답하는지 확인해보세요.\n"
            "· 둘 다 아니라면 인터넷 연결 상태를 확인해보세요."
        ),
    ), 500


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    # 404 같은 Flask 기본 HTTP 오류는 500으로 바꾸지 않고 원래 상태코드 그대로 돌려줍니다.
    if isinstance(e, HTTPException):
        return e
    return render_template("error.html", message=f"예상하지 못한 오류가 발생했습니다:\n\n{e}"), 500


@app.route("/favicon.ico")
def favicon():
    # 브라우저가 자동으로 요청하는 아이콘 — 파일 없이 빈 응답(204)만 돌려줍니다.
    return "", 204


CHANNELS = {
    "longterm_wp": {
        "label": "W혜택주머니",
        "short_label": "W혜택주머니",
        "image_folder": "롱텀블로그WP",
        "queue_file": "content_queue.json",
        "articles_file": "articles.json",
        "platforms": ["wordpress"],
        "queue_builder": "policy",
        "ready": True,
    },
    "wp_tips": {
        "label": "W싼세계",
        "short_label": "W싼세계",
        "image_folder": "워드프레스일반",
        "queue_file": "content_queue_wp_tips.json",
        "articles_file": "articles_wp_tips.json",
        "platforms": ["wordpress"],
        "queue_builder": "wp_tips",  # 네이버 일반과 같은 정보 글(AI 표지 1장 + 본문 2장)이지만, 발행은 워드프레스 SHOP과
        # 같은 사이트·방식(REST·블록 형식)입니다. 원고에 쿠팡 링크 줄이 있을 때만 대가성 문구·구매 버튼이 들어갑니다.
        "ready": True,
        "note": "싼세계 살림 글(일반·숍글). 쿠팡 링크가 있으면 숍글(쿠팡파트너스 문구·배너). 외부 이미지 추가 가능.",
        "wordpress_site": {
            "url": config.SHOP_WORDPRESS_URL,
            "username": config.SHOP_WORDPRESS_USERNAME,
            "app_password": config.SHOP_WORDPRESS_APP_PASSWORD,
        },
    },
    "shop_wp": {
        "label": "워드프레스 SHOP (OLD)",
        "short_label": "워드프레스 SHOP (OLD)",
        "archived": True,  # V5: OLD버전 묶음으로 보관 — 선택·열람·편집·재동기화는 그대로 가능 (hidden과 다름)
        "image_folder": "워드프레스SHOP",
        "queue_file": "content_queue_shop_wp.json",
        "articles_file": "articles_shop_wp.json",
        "platforms": ["wordpress"],
        "queue_builder": "shopping",
        "ready": True,
        "note": "V4: 싼세계 살림 결정형 글. 상품마다 '링크: [쿠팡링크]'를 발행 전 실제 링크·HTML로 교체.",
        "wordpress_site": {
            "url": config.SHOP_WORDPRESS_URL,
            "username": config.SHOP_WORDPRESS_USERNAME,
            "app_password": config.SHOP_WORDPRESS_APP_PASSWORD,
        },
    },
    "naver_tips": {
        "label": "N살림노트",
        "short_label": "N살림노트",
        "image_folder": "네이버일반",
        "queue_file": "content_queue_naver_tips.json",
        "articles_file": "articles_naver_tips.json",
        "platforms": ["naver"],
        "queue_builder": "naver_tips",  # 상품·구매 링크 없는 순수 정보 글. "shopping"과는 다른 값이라
        # 기존 숍 채널 분기(== "shopping")에는 안 걸리고, generate_final 쪽에서만 "shopping"과
        # 묶어서 shop_formatter의 네이버 경로(소제목·인용구·목록·표 스타일)를 재사용합니다.
        "ready": True,
        "note": "같은 네이버 블로그의 살림 글(일반·숍글). 링크가 있으면 숍글(쇼핑커넥트 문구). 발행은 수동 붙여넣기.",
    },
    "shop_naver": {
        "label": "네이버 SHOP (OLD)",
        "short_label": "네이버 SHOP (OLD)",
        "archived": True,  # V5: OLD버전 묶음으로 보관 — 선택·열람·편집·재동기화는 그대로 가능 (hidden과 다름)
        "image_folder": "네이버SHOP",
        "queue_file": "content_queue_shop_naver.json",
        "articles_file": "articles_shop_naver.json",
        "platforms": ["naver"],
        "queue_builder": "shopping",
        "ready": True,
        "note": "V4: 같은 네이버 블로그(네이버 일반과 공유)의 결정형 글. 발행은 수동 붙여넣기. 링크는 원고의 '링크:' 1곳 + 에디터에서 추가 1~3곳.",
    },
    "radar_naver": {
        "label": "N데스크피커",
        "short_label": "N데스크피커",
        "image_folder": "생활레이더",
        "queue_file": "content_queue_radar_naver.json",
        "articles_file": "articles_radar_naver.json",
        "platforms": ["naver"],
        "queue_builder": "radar",  # 새 값 — "shopping"·"naver_tips"·"wp_tips" 분기에 안 걸립니다. 생활레이더 전용 분기는 RADAR_CHANNELS로만 탑니다.
        "ready": True,
        "note": "새 네이버 블로그(속보·계산·실천·정리). 숍글·대가성·하단 추천 없음. 표지·본문 16:9 사진 + 글자 카드(1:1).",
    },
    "tistory": {
        "label": "티스토리",
        "short_label": "티스토리",
        "image_folder": "티스토리",
        "queue_file": "content_queue_tistory.json",
        "articles_file": "articles_tistory.json",
        "platforms": ["tistory"],
        "hidden": True,  # 화면(사이드바)에서만 숨김
        "queue_builder": "policy",
        "ready": False,  # 소재·방향 정해지면 채울 예정
    },
}
DEFAULT_CHANNEL = "longterm_wp"
# 채널 이름을 바꾸기 전에 쓰던 이미지 폴더 이름 (기존 폴더는 그대로 두고, 이미지 복구에서만 함께 찾습니다)
LEGACY_IMAGE_FOLDERS = {"longterm_wp": "롱텀WP", "wp_tips": "일반워드프레스", "shop_wp": "숍WP",
                        "naver_tips": "일반네이버", "shop_naver": "숍네이버"}


def get_active_channel_id() -> str:
    """지금 화면에서 보고 있는 채널이 뭔지 확인합니다 (세션에 저장되어 있고, 상단 전환
    버튼으로 바꿀 수 있습니다). 잘못된 값이 들어있으면 기본 채널(롱텀 워드프레스)로 돌아갑니다."""
    ch = session.get("channel", DEFAULT_CHANNEL)
    if ch not in CHANNELS or CHANNELS[ch].get("hidden"):
        ch = DEFAULT_CHANNEL  # 잘못된 값이거나 화면에서 숨긴 채널(티스토리 등)이면 기본 채널로
    return ch


def get_channel_config() -> dict:
    return CHANNELS[get_active_channel_id()]


def get_active_platforms() -> list[str]:
    """
    지금 채널에서 실제로 쓰는 플랫폼만 돌려줍니다 (예: 롱텀 워드프레스 채널은 워드프레스만).
    데이터 모델(PLATFORMS, get_article의 기본값)은 예전처럼 3개 플랫폼 자리를 다 갖고
    있지만, 화면에 실제로 보여줄 때는 이 채널별 목록만 씁니다 — 안 쓰는 플랫폼 칸이
    편집기에 계속 "아직 안 채워짐"으로 떠서 헷갈리는 걸 막기 위함입니다.
    """
    return get_channel_config()["platforms"]


# AI 이미지(표지 1장 + 본문 2장)를 쓰는 일반(정보글) 채널. 이미지 생성·본문 이미지 배치는 이 목록으로 판단합니다.
# (네이버 복사 버튼·네이버용 "같이 보면 좋은 글" 같은 네이버 전용 동작은 여기에 묶지 않고 naver_tips로만 확인합니다.)
AI_INFO_IMAGE_CHANNELS = ("naver_tips", "wp_tips")
# 키워드 추천 없이 "새 클러스터 만들기"로 직접 추가만 하고, 붙여넣기·최종본도 채널 자기 플랫폼만 다루는 채널
MANUAL_KEYWORD_CHANNELS = ("naver_tips", "wp_tips", "radar_naver")
# 생활레이더(새 네이버 블로그) 전용 동작은 이 목록을 확인하는 새 분기로만 넣습니다 — 기존 채널 분기는 그대로 둡니다.
RADAR_CHANNELS = ("radar_naver",)
# 워드프레스 SHOP과 같은 워드프레스 사이트·발행 방식(REST 발행, 블록 형식, 미디어 alt)을 쓰는 채널
SHOP_WP_SITE_CHANNELS = ("shop_wp", "wp_tips")


def is_ai_info_channel(channel_id: str | None = None) -> bool:
    """AI 이미지를 쓰는 일반(정보글) 채널인지 (네이버 일반·워드프레스 일반)."""
    return (channel_id or get_active_channel_id()) in AI_INFO_IMAGE_CHANNELS


@app.route("/switch-channel/<channel_id>")
def switch_channel(channel_id):
    """상단 채널 전환 버튼. 큐·글 데이터가 채널별로 완전히 분리되어 있어서, 전환하면
    그 채널의 키워드 큐·발행 이력만 보이게 됩니다."""
    if channel_id in CHANNELS:
        # 숨긴 채널(티스토리 등)은 선택을 무시하고 기본 채널로
        session["channel"] = DEFAULT_CHANNEL if CHANNELS[channel_id].get("hidden") else channel_id
    week = request.args.get("week", "")  # 선택 사항: 그 주차 화면으로 바로 이동 (없으면 예전과 같음)
    return redirect(url_for("index", week=week) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", week) else url_for("index"))


def get_active_wordpress_url() -> str:
    """지금 채널이 쓰는 워드프레스 사이트 주소. 채널에 별도 사이트가 지정 안 되어 있으면
    기본(config.WORDPRESS_URL, 롱텀 블로그)을 씁니다."""
    site = get_channel_config().get("wordpress_site")
    return site["url"] if site else config.WORDPRESS_URL


@app.route("/shop-news-suggestions")
def shop_news_suggestions():
    """
    "🔍 최신 뉴스 참고" 버튼용 API. 이 대표 키워드(카테고리)로 최근 뉴스를 찾아서
    돌려줍니다 — 자동으로 세부 키워드를 만드는 게 아니라, 사람이 읽고 "이번 주엔 이걸
    다뤄볼까" 판단할 때 참고하는 자료입니다.
    """
    seed = request.args.get("seed", "").strip()
    if not seed:
        return {"news": []}
    news = engine.get_trending_news_for_seed(seed, count=5)
    return {"news": news}


@app.before_request
def _apply_channel_wordpress_site():
    """
    매 요청 시작할 때, 지금 채널에 맞는 워드프레스 사이트 접속 정보로 wp_publisher를
    맞춰둡니다. 이렇게 해두면 발행 관련 라우트 하나하나에서 신경 쓸 필요 없이, 이 요청
    안에서 wp_publisher.py의 발행 함수를 부르면 자동으로 맞는 사이트로 나갑니다.
    """
    channel_config = CHANNELS.get(get_active_channel_id())
    wp_publisher.set_site_context(channel_config.get("wordpress_site") if channel_config else None)

PLATFORMS = ["wordpress", "naver", "tistory"]
PLATFORM_LABELS = {
    "wordpress": "워드프레스",
    "naver": "네이버 블로그",
    "tistory": "티스토리",
}


@app.context_processor
def _inject_sidebar_context():
    """
    사이드바(.sidebar)가 모든 화면에서 쓰는 값들을 기본으로 넣어줍니다. 라우트가 render_template에
    같은 이름을 직접 넘기면 그 값이 우선하므로, 기존 화면(index 등)의 동작은 그대로입니다.
    """
    mode = "목(mock) 모드 — 가짜 데이터 사용 중" if config.USE_MOCK else "실제 네이버 API 연동 중"
    if config.USE_MOCK and config.MISSING_NAVER_VARS:
        mode += f" (비어있는 값: {', '.join(config.MISSING_NAVER_VARS)})"
    return {
        "mode": mode,
        "hidden_features": set(getattr(config, "HIDDEN_FEATURES", ())),  # 화면에서 숨길 기능(config.HIDDEN_FEATURES)
        "channels": CHANNELS,
        "active_channel_id": get_active_channel_id(),
        "is_ai_info_channel": is_ai_info_channel(),
        "is_old_channel": bool(CHANNELS[get_active_channel_id()].get("archived")),  # OLD버전(보관) 채널 화면인지
        "platforms": get_active_platforms(),
        "platform_labels": PLATFORM_LABELS,
    }
PLATFORM_SHORT_LABELS = {
    "wordpress": "워",
    "naver": "네",
    "tistory": "티",
}

WEEKLY_QUEUE_SIZE = 60  # 주간 키워드 큐 최대 개수 (이 값만 바꾸면 전체에 반영됩니다)
SHOP_WEEKLY_QUEUE_SIZE = 48  # 숍 채널(queue_builder == "shopping")의 주간 키워드 큐 최대 개수 (롱텀은 위 값 그대로). 10개 × 2묶음 × 2칸 = 40 + 제철 여유 8
# 롱텀 채널 본문 이미지 개수(플랫폼당)는 원고 길이로 정합니다: 공백 제외 글자 수가 기준 이상이면
# LONG장, 미만이면 SHORT장 (마크다운 기호·{{LINK:}}·URL·HTML 태그는 글자 수에서 뺌 — _body_char_count 참고).
# 숍 채널(0장)·네이버/싼세계 정보 채널(고정 INFO_BODY_IMAGE_COUNT장)는 gen_images에서 따로 정합니다.
# (photo_v2_20261020: 본문 사진 +1장 — 롱텀 2/3 → 3/4, 정보 채널 2 → 3, 생활레이더 2 → 3)
LONGTERM_BODY_IMAGE_CHAR_THRESHOLD = 4000
LONGTERM_BODY_IMAGES_LONG = 5
LONGTERM_BODY_IMAGES_SHORT = 4
INFO_BODY_IMAGE_COUNT = 3  # 네이버(naver_tips)·싼세계(wp_tips) 본문 AI 사진 장수(플랫폼당)
NAVER_TIPS_BODY_IMAGE_COUNT = 4  # N살림노트(naver_tips)만 4장(photo4_naver_tips_20261119). wp_tips 는 위 INFO_BODY_IMAGE_COUNT 그대로


def _info_body_image_count(channel_id: str) -> int:
    """정보 채널(naver_tips·wp_tips)의 본문 사진 장수(플랫폼당). naver_tips 만 4장, 나머지는 INFO_BODY_IMAGE_COUNT."""
    return NAVER_TIPS_BODY_IMAGE_COUNT if channel_id == "naver_tips" else INFO_BODY_IMAGE_COUNT
NV75_CHANNELS = ("naver_tips",)  # 본문 사진마다 "네이버용 75% 여백 파일"을 함께 만드는 채널(생활레이더는 RADAR_CHANNELS)

# 워드프레스로 "보내는 사본"에서 본문 맨 앞 표지(seo-cover-img)를 뺄지 채널별로 정합니다 (True = 뺌).
# 테마가 대표이미지(같은 표지)를 글 위에 이미 보여 주면 같은 사진이 두 번 보이므로 뺍니다.
# 앱 저장본(platform_final)·최종 미리보기에는 표지가 그대로 남습니다. 목록에 없는 채널은 False.
WP_STRIP_BODY_COVER = {
    "longterm_wp": False,  # 혜택주머니 테마는 대표이미지를 글 위에 표시하지 않음(2026-10-06 확인) → 본문에 표지를 넣어야 보임
    "shop_wp": False,  # 숍 사이트 테마 확인 후 결정
}


def _wp_strip_cover() -> bool:
    channel_id = get_active_channel_id()
    if channel_id == "wp_tips":
        channel_id = "shop_wp"  # 워드프레스 일반은 워드프레스 SHOP과 같은 사이트·테마라 숍 설정을 그대로 따릅니다
    return WP_STRIP_BODY_COVER.get(channel_id, False)


# ---------- 콘텐츠 큐 (1단계, 주차 단위) ----------

# 구글 드라이브 동기화가 파일을 잠깐 잡고 있을 때(Errno 22·13·32·33) 잠시 뒤 다시 시도합니다.
_FILE_LOCK_ERRNOS = {22, 13, 32, 33}
_FILE_RETRY_DELAYS = (0.5, 1, 2)  # 첫 시도 + 재시도 3번 = 최대 4번


def _is_file_locked_error(e: OSError) -> bool:
    return isinstance(e, PermissionError) or e.errno in _FILE_LOCK_ERRNOS


def _read_text_retry(path: str) -> str:
    """json 데이터 파일을 텍스트로 읽습니다. 잠김 계열 오류면 0.5→1→2초 간격으로 재시도하고, 끝까지 실패하면 그 오류를 그대로 냅니다.
    (파일 없음 등 잠김이 아닌 오류는 재시도 없이 바로 냅니다.)"""
    for delay in (*_FILE_RETRY_DELAYS, None):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            if delay is None or not _is_file_locked_error(e):
                raise
            time.sleep(delay)


def _write_json_atomic(path: str, data) -> None:
    """json 데이터 파일을 안전하게 씁니다: 같은 폴더의 .tmp에 먼저 쓰고 flush·fsync 후 os.replace로 바꿔치기.
    replace가 잠김 오류면 재시도하고, 끝까지 실패하면 .tmp를 지우고 오류를 냅니다(원본은 그대로)."""
    text = json.dumps(data, ensure_ascii=False, indent=2)  # 먼저 변환 — 변환 실패 시 파일을 건드리지 않음
    tmp = f"{path}.tmp"
    try:
        for delay in (*_FILE_RETRY_DELAYS, None):
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
                return
            except OSError as e:
                if delay is None or not _is_file_locked_error(e):
                    raise
                time.sleep(delay)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_queue() -> list[dict]:
    queue_file = get_channel_config()["queue_file"]
    try:
        content = _read_text_retry(queue_file)
    except FileNotFoundError:
        return []

    if not content.strip():
        raise RuntimeError(
            f"{queue_file} 파일이 비어있습니다. 구글드라이브 동기화가 아직 끝나지 않았을 수 있습니다. "
            "몇 초 기다렸다가 새로고침해보세요."
        )
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"{queue_file} 파일을 읽는 중 오류가 발생했습니다. 구글드라이브 동기화가 아직 끝나지 않았을 수 있습니다. "
            "몇 초 기다렸다가 새로고침해보세요."
        )


def save_queue(queue: list[dict]) -> None:
    queue_file = get_channel_config()["queue_file"]
    _write_json_atomic(queue_file, queue)


def find_queue_item(queue: list[dict], article_id: str) -> dict | None:
    return next((item for item in queue if item.get("id") == article_id), None)


def load_articles() -> dict:
    articles_file = get_channel_config()["articles_file"]
    try:
        return json.loads(_read_text_retry(articles_file))
    except FileNotFoundError:
        return {}


def save_articles(articles: dict) -> None:
    articles_file = get_channel_config()["articles_file"]
    _write_json_atomic(articles_file, articles)


def group_by_cluster(items: list[dict]) -> list[dict]:
    """
    같은 cluster_seed가 연달아 나오는 항목들을 하나의 섹션으로 묶습니다.
    (cluster_seed가 없는 옛날 데이터는 묶지 않고 그대로 보여줍니다.)
    """
    sections = []
    last_seed = object()  # 어떤 실제 값과도 안 같은 값으로 시작
    for item in items:
        seed = item.get("cluster_seed")
        if seed is None or seed != last_seed:
            sections.append({"seed": seed, "entries": []})
            last_seed = seed
        sections[-1]["entries"].append(item)
    return sections


# ---------- 비슷한 키워드·글 확인 (조회 전용 — 큐/글 데이터는 건드리지 않습니다) ----------

_SIM_STATE_RANK = {"published": 3, "scheduled": 2, "generated": 1, "empty": 0}
_SIM_CACHE: dict = {}  # {(채널, 범위, 큐 파일 수정시각, 글 파일 수정시각): 계산 결과} — 파일이 바뀌면 자동으로 새로 계산


def _stored_state(article: dict, platforms: list[str]) -> dict:
    """저장된 글 데이터(get_article 기본값 없이 원본 dict)에서 상태를 판단합니다. platform_status와 같은 규칙입니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    best = {"level": "empty", "label": "미생성", "date": None}
    for p in platforms:
        date_str = (article.get("platform_published_at") or {}).get(p)
        if date_str:
            cur = (
                {"level": "scheduled", "label": f"예약 {date_str}", "date": date_str}
                if date_str > today
                else {"level": "published", "label": "발행완료", "date": date_str}
            )
        elif (article.get("platform_raw") or {}).get(p):
            cur = {"level": "generated", "label": "생성만 완료", "date": None}
        else:
            cur = {"level": "empty", "label": "미생성", "date": None}
        if _SIM_STATE_RANK[cur["level"]] > _SIM_STATE_RANK[best["level"]]:
            best = cur
    return best


def _sim_items(queue: list[dict], articles_data: dict, platforms: list[str] | None = None) -> list[dict]:
    platforms = platforms or get_active_platforms()
    items = []
    for it in queue:
        a = articles_data.get(it["id"]) or {}
        state = _stored_state(a, platforms)
        titles = [t for t in ((a.get("platform_title") or {}).get(p) for p in platforms) if t]
        items.append(
            {
                "id": it["id"],
                "keyword": it["keyword"],
                "cluster_seed": it.get("cluster_seed"),
                "week": it["week"],
                "titles": titles,
                "state": state,
                "approved_flag": bool(it.get("approved")),
                # 비교 대상: 승인됐거나 글이 생성된 것
                "counts": bool(it.get("approved")) or state["level"] != "empty",
            }
        )
    return items


def _is_cluster_sibling(a: dict, b: dict) -> bool:
    """같은 주차 + 같은 cluster_seed(묶음)인 두 항목 — 대표·세부 형제라서 서로 겹침 배지를 주지 않습니다."""
    return bool(a.get("cluster_seed")) and a.get("cluster_seed") == b.get("cluster_seed") and a.get("week") == b.get("week")


def _sim_detail(item: dict, match: dict) -> dict:
    """화면에 보여줄 한 줄 정보: 키워드·제목·주차·상태·이동 링크."""
    return {
        "id": item["id"],
        "level": match["level"],
        "reason": match["reason"],
        "keyword": item["keyword"],
        "title": item["titles"][0] if item["titles"] else "",
        "week": item["week"],
        "state": item["state"]["label"],
        # 편집기를 쓸 수 있는(승인된) 항목은 편집기로, 아니면 그 주차 화면의 해당 줄로 이동
        "link": (
            f"/article/{item['id']}/edit"
            if item["approved_flag"] or item["state"]["level"] != "empty"
            else f"/?week={item['week']}&added={item['id']}"
        ),
    }


def get_similarity(queue: list[dict], articles_data: dict, week: str | None = None) -> dict:
    """
    {id: {"level": "red"|"yellow", "count": N, "matches": [상세 최대 5개]}} — 겹치는 항목만 들어 있습니다.
    week가 있으면 그 주차의 모든 후보에 배지를 달되(비교 대상 = 승인·생성된 항목만, 같은 주 미승인 후보는 비교 안 함),
    없으면 승인·생성된 항목끼리 비교합니다(발행 현황용). 두 경우 모두 같은 주차·같은 묶음(cluster_seed) 형제끼리는
    겹침으로 치지 않습니다. 같은 채널·같은 파일 상태면 결과를 재사용합니다.
    """
    cfg = get_channel_config()
    sig = tuple(
        os.stat(f).st_mtime_ns if os.path.exists(f) else 0 for f in (cfg["queue_file"], cfg["articles_file"])
    )
    key = (get_active_channel_id(), week or "*") + sig
    if key in _SIM_CACHE:
        return _SIM_CACHE[key]

    items = _sim_items(queue, articles_data)
    by_id = {i["id"]: i for i in items}
    pool = [i for i in items if i["counts"]]
    if week:
        # 배지를 받는 대상은 그 주 후보 전체, 비교 대상은 과거에 승인·생성된 항목만 (같은 주 미승인 후보끼리는 비교하지 않음)
        targets = [i for i in items if i["week"] == week]
        universe = pool
    else:
        targets, universe = pool, pool

    found = similarity.find_similar(targets, universe)
    result = {}
    for tid, r in found.items():
        # 같은 주차·같은 묶음(cluster_seed) 형제끼리는 서로 겹침으로 치지 않음
        r = {k: [m for m in v if not _is_cluster_sibling(by_id[tid], by_id[m["id"]])] for k, v in r.items()}
        level = "red" if r["red"] else ("yellow" if r["yellow"] else None)
        if not level:
            continue
        result[tid] = {
            "level": level,
            "count": len(r[level]),
            "matches": [_sim_detail(by_id[m["id"]], m) for m in similarity.top_matches(r)],
        }
    # 오래된 결과가 쌓이지 않게 같은 채널의 이전 파일 상태 결과는 버립니다.
    for k in [k for k in _SIM_CACHE if k[:2] == key[:2] and k != key]:
        del _SIM_CACHE[k]
    _SIM_CACHE[key] = result
    return result


# ---------- 같은 블로그 중복·짝 글 확인 (blog_overlap.py — 조회 전용, 어떤 json도 쓰지 않습니다) ----------

def _blog_channels() -> dict:
    """blog_overlap에 넘길 채널 정보 (숍·네이버 일반/워드프레스 4개 채널만 의미가 있고, 롱텀은 해당 없음)."""
    return {
        cid: {
            "label": CHANNELS[cid].get("short_label") or CHANNELS[cid]["label"],
            "queue_file": CHANNELS[cid]["queue_file"],
            "articles_file": CHANNELS[cid]["articles_file"],
            "platforms": CHANNELS[cid]["platforms"],
        }
        for chans in blog_overlap.BLOGS.values()
        for cid in chans
        if cid in CHANNELS
    }


V4_EXCLUDED_FILE = "v4_excluded.json"  # {채널id: [글 id...]} — "기타 리뷰"로 표시한 글. 없으면 빈 것으로 시작 (이 파일만 새로 씁니다)


def _load_v4_excluded() -> dict:
    """"기타 리뷰"로 표시한 글 목록. 파일이 없거나 깨졌으면 빈 목록."""
    out = {c: [] for c in v4_board.V4_CHANNELS}
    try:
        data = json.loads(_read_text_retry(V4_EXCLUDED_FILE) or "{}")
    except (FileNotFoundError, ValueError):
        return out
    if isinstance(data, dict):
        for c in out:
            ids = data.get(c)
            if isinstance(ids, list):
                out[c] = [i for i in ids if isinstance(i, str)]
    return out


def _v4_excluded_wp_post_ids() -> set[int]:
    """"기타 리뷰"로 표시한 워드프레스 SHOP·워드프레스 일반 글의 워드프레스 글 ID들 — 워드프레스 일반 "함께 보면 좋은 글"에서 뺄 글. 읽기만 합니다."""
    excluded = _load_v4_excluded()
    out: set[int] = set()
    for cid in ("shop_wp", "wp_tips"):
        ids = set(excluded.get(cid) or [])
        if not ids:
            continue
        _, arts = _read_channel_data(cid)
        for aid in ids:
            pid = ((arts.get(aid) or {}).get("platform_post_id") or {}).get("wordpress")
            try:
                if pid:
                    out.add(int(pid))
            except (TypeError, ValueError):
                continue
    return out


def _v4_categories() -> list[str]:
    return list(engine.SHOP_CATEGORIES)


def _v4_aliases() -> dict:
    """예전 카테고리 이름 → 새 이름 (표시·집계 전용; 저장된 글 메모는 안 고칩니다)."""
    return dict(engine.CATEGORY_ALIASES)


def _v4_channel_info() -> dict:
    """v4_board에 넘길 채널 정보 (4개 채널 + 롱텀)."""
    out = _blog_channels()
    cfg = CHANNELS["longterm_wp"]
    out["longterm_wp"] = {
        "label": cfg.get("short_label") or cfg["label"], "queue_file": cfg["queue_file"],
        "articles_file": cfg["articles_file"], "platforms": cfg["platforms"],
    }
    return out


def _v4_tag(article: dict, channel_id: str, item_id: str, excluded: dict) -> dict:
    """글 하나의 유형·카테고리·기타 리뷰 표시 (조회만). article은 저장된 글 dict."""
    info = v4_board.classify(
        v4_board.memo_text_for(article, CHANNELS[channel_id]["platforms"]), channel_id, _v4_categories(), _v4_aliases(),
        is_shop=shop_judge.is_shop_article(article, channel_id),  # 목록·현황판과 같은 숍글 판정 ("유형 없는 숍글 = C(추정)")
    )
    info["excluded"] = item_id in (excluded.get(channel_id) or [])
    return info


@app.route("/article/<article_id>/toggle-excluded", methods=["POST"])
def toggle_v4_excluded(article_id):
    """글 편집 화면의 "기타 리뷰로 표시 / 해제" — v4_excluded.json에만 저장합니다. 이미 발행된 글에는 어떤 요청도 보내지 않습니다."""
    cid = get_active_channel_id()
    if cid not in v4_board.V4_CHANNELS or not find_queue_item(load_queue(), article_id):
        return redirect(url_for("edit_article", article_id=article_id))
    data = _load_v4_excluded()
    ids = data[cid]
    if article_id in ids:
        ids.remove(article_id)
    else:
        ids.append(article_id)
    _write_json_atomic(V4_EXCLUDED_FILE, data)
    return redirect(url_for("edit_article", article_id=article_id))


@app.route("/v4-board")
def v4_board_view():
    board = v4_board.build_board(
        v4_board.week_monday(request.args.get("week")), _v4_channel_info(), _v4_categories(),
        _load_v4_excluded(), _read_text_retry, _v4_aliases(),
    )
    radar = None
    try:  # 생활레이더 섹션은 별도 계산(기존 카드 계산과 섞이지 않음). 실패해도 기존 현황판은 그대로 보입니다.
        r_queue, r_articles = _read_channel_data("radar_naver")
        radar = radar_board.build(r_queue, r_articles, board["week"], datetime.now())
    except Exception:  # noqa: BLE001
        radar = None
    return render_template("v4_board.html", board=board, radar=radar)


def _blog_overlap_targets(items: list[dict]) -> list[dict]:
    cid = get_active_channel_id()
    arts = load_articles()  # 이미 있는 항목은 숍글 판정(링크 자동 판정 포함)을 같이 넘겨서, 겹침 관계(짝 글/중복 위험)가 목록과 같게 나오게 함
    return [
        {"id": i["id"], "channel": cid, "keyword": i["keyword"], "cluster_seed": i.get("cluster_seed"),
         "is_shop": shop_judge.is_shop_article(arts.get(i["id"]) or {}, cid)}
        for i in items
    ]


def _blog_overlap_for_week(items: list[dict]) -> dict | None:
    """주간 승인 화면 배지용 {id: {...}}. 블로그에 속하지 않는 채널(롱텀 등)이면 None → 화면에 아무것도 안 나옵니다."""
    blog = blog_overlap.blog_of(get_active_channel_id())
    if not blog:
        return None
    return blog_overlap.overlap_overview(
        blog, _blog_overlap_targets(items), _blog_channels(), _read_text_retry, _load_v4_excluded()
    )


def _blog_overlap_for_one(
    keyword: str, cluster_seed: str | None, item_id: str | None = None, channel_id: str | None = None
) -> dict | None:
    """키워드 하나(저장 전 새 키워드 또는 기존 글)의 겹침 {"matches": [...], "reuse": [...]} — 롱텀 등은 None.
    channel_id가 없으면 지금 선택된 채널 기준."""
    channel_id = channel_id or get_active_channel_id()
    blog = blog_overlap.blog_of(channel_id)
    if not blog:
        return None
    target = {"id": item_id, "channel": channel_id, "keyword": keyword, "cluster_seed": cluster_seed}
    if item_id:  # 이미 있는 글이면 그 글의 숍글 판정을 같이 넘김 (저장 전 새 키워드는 예전처럼 채널로 판단)
        _, arts = _read_channel_data(channel_id)
        target["is_shop"] = shop_judge.is_shop_article(arts.get(item_id) or {}, channel_id)
    return blog_overlap.overlaps_for_new(blog, target, _blog_channels(), _read_text_retry, _load_v4_excluded())


SEED_SELECTION_FILE = "seed_selection_longterm.json"  # {주차: 그 주에 롱텀 불러오기가 고른 시드 요약} — 대시보드 상단 한 줄용
# 숍 채널은 채널별 파일(seed_selection_shop_naver.json / seed_selection_shop_wp.json)에 같은 형식으로 저장합니다.
SHOP_SEED_SELECTION_FILES = {
    "shop_wp": "seed_selection_shop_wp.json",
    "shop_naver": "seed_selection_shop_naver.json",
}


def _seed_selection_file_for(channel_id: str) -> str:
    return SHOP_SEED_SELECTION_FILES.get(channel_id, SEED_SELECTION_FILE)


def _queue_size_for(channel_config: dict) -> int:
    """채널별 주간 큐 크기 — 숍은 SHOP_WEEKLY_QUEUE_SIZE(48), 나머지는 WEEKLY_QUEUE_SIZE(60)."""
    return SHOP_WEEKLY_QUEUE_SIZE if channel_config.get("queue_builder") == "shopping" else WEEKLY_QUEUE_SIZE


def _compute_seed_last_used(queue: list[dict], articles_data: dict, platforms: list[str], week: str) -> dict[str, str]:
    """시드별로 마지막에 승인·글 생성된 주차 (이번 주보다 이전 주차만 — 같은 주에 다시 불러와도 시드 선택이 같게 하려고)."""
    seed_last_used: dict[str, str] = {}
    for item in queue:
        seed = item.get("cluster_seed")
        if not seed or item["week"] >= week:
            continue
        if item.get("approved") or _stored_state(articles_data.get(item["id"]) or {}, platforms)["level"] != "empty":
            seed_last_used[seed] = max(seed_last_used.get(seed, ""), item["week"])
    return seed_last_used


def _count_seed_items(items: list[dict]) -> dict[str, int]:
    """{시드: 항목 수} — 이번 주에 이미 승인돼 유지 중인 항목으로 시드별 칸 수 제한에 씁니다."""
    counts: dict[str, int] = {}
    for item in items:
        if item.get("cluster_seed"):
            counts[item["cluster_seed"]] = counts.get(item["cluster_seed"], 0) + 1
    return counts


def _save_seed_selection(week: str, selection: dict | None, path: str = SEED_SELECTION_FILE) -> None:
    """주간 불러오기에서 고른 시드 요약(월별/상시 시드 개수와 목록)을 주차별로 저장합니다. 화면 표시용이라 실패해도 무시합니다."""
    if not selection:
        return
    try:
        try:
            data = json.loads(_read_text_retry(path))
        except (FileNotFoundError, ValueError):
            data = {}
        data[week] = {
            "monthly": selection["monthly"],
            "evergreen": selection["evergreen"],
            "month": selection["month"],
            "saved_at": now_iso(),
        }
        _write_json_atomic(path, data)
    except OSError:
        pass


def _seed_summary_line(week: str, path: str = SEED_SELECTION_FILE) -> str | None:
    """"이번 주 시드: 월별 N개 + 상시 N개(카테고리별 개수)" — 그 주에 불러온 기록이 없으면 None."""
    try:
        entry = json.loads(_read_text_retry(path)).get(week)
    except (FileNotFoundError, ValueError):
        return None
    if not entry:
        return None
    ev = entry["evergreen"]
    parts = " · ".join(f"{cat} {len(names)}" for cat, names in ev.items())
    return f"이번 주 시드: 월별 {len(entry['monthly'])}개 + 상시 {sum(len(v) for v in ev.values())}개 ({parts})"


def find_red_matches_for_new_keyword(
    queue: list[dict], articles_data: dict, week: str, keyword: str, cluster_seed: str, platforms: list[str] | None = None
) -> list[dict]:
    """키워드를 저장하기 전에, 이미 있는 글과 '거의 같음(🔴)'인 것만 찾아 돌려줍니다. platforms가 없으면 지금 선택된 채널 기준."""
    items = _sim_items(queue, articles_data, platforms)
    by_id = {i["id"]: i for i in items}
    # 비교 대상 = 승인·생성된 항목만. 추가하려는 묶음과 같은 주·같은 묶음 형제는 뺍니다.
    target = {"id": "__new__", "keyword": keyword, "cluster_seed": cluster_seed, "titles": [], "week": week}
    universe = [i for i in items if i["counts"] and not _is_cluster_sibling(target, i)]
    r = similarity.find_similar([target], universe)["__new__"]
    out = [_sim_detail(by_id[m["id"]], m) for m in r["red"][: similarity.MAX_SHOWN]]
    # 같은 주 대기열에 (정규화하면) 같은 키워드의 미승인 후보가 이미 있으면 그 줄을 보여 주고 승인하도록 안내
    norm = similarity.normalize(keyword)
    shown = {d["id"] for d in out}
    for i in items:
        if i["week"] == week and not i["counts"] and i["id"] not in shown and similarity.normalize(i["keyword"]) == norm:
            out.insert(0, _sim_detail(i, {"level": "red", "reason": "이미 이번 주 대기열에 있음 → 그 줄을 승인하세요"}))
    return out


@app.route("/")
def index():
    active_channel_id = get_active_channel_id()
    channel_config = CHANNELS[active_channel_id]
    if not channel_config["ready"]:
        return render_template(
            "channel_placeholder.html",
            channels=CHANNELS,
            active_channel_id=active_channel_id,
            channel_label=channel_config["label"],
        )

    queue = load_queue()
    current_week = request.args.get("week") or engine.get_week_label()

    all_weeks = sorted({item["week"] for item in queue}, reverse=True)
    if current_week not in all_weeks:
        all_weeks = sorted(all_weeks + [current_week], reverse=True)

    week_items = [item for item in queue if item["week"] == current_week]

    # "과거 승인" 배지는 저장해둔 값이 아니라, 화면을 열 때마다 매번 새로 계산합니다.
    # (저장된 값만 썼다면, 지난주 승인을 취소해도 이번 주 배지가 그대로 남는 문제가 있었습니다.)
    approved_history: dict[str, str] = {}
    for item in sorted(queue, key=lambda i: i["week"]):
        if item["approved"]:
            approved_history[item["keyword"]] = item["week"]
    for item in week_items:
        prev_week = approved_history.get(item["keyword"])
        is_duplicate = prev_week is not None and prev_week != current_week
        item["duplicate_of_past_approval"] = is_duplicate
        item["duplicate_of_past_approval_week"] = prev_week if is_duplicate else None

    # 플랫폼별 상태(미작성/작성됨/발행됨)를 미리 계산해서 목록에 배지로 보여줍니다 —
    # "이번 주 담당 플랫폼" 드롭다운은 계획용 표시일 뿐, 실제로 어떤 플랫폼에 내용이
    # 있는지/발행됐는지는 이 배지로 따로 확인합니다. (articles.json은 한 번만 읽어서
    # 키워드 개수만큼 파일을 반복해서 읽지 않도록 합니다.)
    articles_data = load_articles()
    for item in week_items:
        stored = articles_data.get(item["id"], {})
        platform_raw = stored.get("platform_raw") or {}
        platform_published_url = stored.get("platform_published_url") or {}
        item["platform_status"] = {}
        for p in PLATFORMS:
            if platform_published_url.get(p):
                item["platform_status"][p] = "published"
            elif platform_raw.get(p):
                item["platform_status"][p] = "filled"
            else:
                item["platform_status"][p] = "empty"

    approved_count = sum(1 for i in week_items if i["approved"])
    sections = group_by_cluster(week_items)
    # 화면 표시 순서만: 승인된 항목이 하나라도 있는 묶음을 앞으로 (sorted는 안정 정렬이라 각 그룹 안 점수 순서는 유지)
    sections = sorted(sections, key=lambda s: not any(e.get("approved") for e in s["entries"]))
    sim = get_similarity(queue, articles_data, week=current_week)
    is_longterm = active_channel_id == "longterm_wp"
    is_shop_seed_channel = channel_config.get("queue_builder") == "shopping"
    if is_longterm:
        seed_categories = engine.seed_category_map(engine.load_seasonal_calendar())
    elif is_shop_seed_channel:
        seed_categories = engine.seed_category_map(engine.load_seasonal_calendar("shopping_calendar.json"))
    else:
        seed_categories = {}
    seed_summary = (
        _seed_summary_line(current_week, _seed_selection_file_for(active_channel_id))
        if (is_longterm or is_shop_seed_channel) else None
    )
    fill_count = len(_fill_link_targets()["targets"]) if is_longterm else None
    v4_tags = {}
    if active_channel_id in v4_board.V4_CHANNELS:
        # 원고가 있는 항목(또는 "기타 리뷰"로 표시한 항목)에만 글 유형·카테고리 배지를 보여줍니다 (조회만).
        ex = _load_v4_excluded()
        for item in week_items:
            stored = articles_data.get(item["id"]) or {}
            has_raw = any((stored.get("platform_raw") or {}).get(p) for p in channel_config["platforms"])
            tag = _v4_tag(stored, active_channel_id, item["id"], ex)
            if has_raw or tag["excluded"]:
                v4_tags[item["id"]] = tag
    blog_ov = _blog_overlap_for_week(week_items)
    blog_pair_note = None
    if blog_ov and request.args.get("paired") and request.args.get("added") in blog_ov:
        pairs = [m for m in blog_ov[request.args["added"]]["matches"] if m["relation"] == blog_overlap.RELATION_PAIR]
        if pairs:
            blog_pair_note = ", ".join(f"{m['keyword']} ({m['channel_label']})" for m in pairs[:blog_overlap.MAX_SHOWN])

    # 타깃 키워드로 승인한 싼세계 줄: "네이버 발행 후, 다른 각도" + 짝 네이버 글이 아직 발행 전이면 "네이버 먼저 발행하세요" (조회만)
    pair_meta: dict = {}
    angle_items = [i for i in week_items if i.get("angle_note")]
    if angle_items:
        naver_queue, naver_articles = _read_channel_data("naver_tips")
        now_dt = datetime.now()
        for i in angle_items:
            ref = i.get("pair_ref") or {}
            nid = ref.get("id") or next(
                (q["id"] for q in naver_queue if q.get("week") == (ref.get("week") or i["week"]) and q.get("keyword") == (ref.get("keyword") or i["keyword"])),
                None,
            )
            published = bool(nid) and _is_public_by(naver_articles.get(nid) or {}, "naver", now_dt)
            pair_meta[i["id"]] = {"note": i["angle_note"], "naver_first": not published}

    # 네이버·싼세계 채널 맨 위 한 줄: 이 주 승인 개수와 유형별 개수 (조회만 — 혜택주머니·OLD 채널에는 나오지 않음)
    planned_line = None
    review_ids: set = set()  # 싼세계 리뷰 분석 글 id (11/2 이후 주차만 채워짐)
    if active_channel_id in ("naver_tips", "wp_tips") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", current_week):
        blog_key = "naver" if active_channel_id == "naver_tips" else "ssansegae"
        wi = _target_week_info(current_week)[blog_key]
        import target_keyword_finder as tkf
        plan_total, plan_slots = tkf.blog_plan(blog_key, current_week)
        planned_line = {
            "approved": wi["approved"], "plan": wi["target"], "undecided": wi["undecided"],
            "over": wi["approved"] > wi["target"], "short": wi["approved"] < wi["target"],
            "types": [{"name": t, "count": wi["types"][t], "target": plan_slots[t]} for t in tkf.BLOGS[blog_key]["types"]],
        }
        if blog_key == "ssansegae" and tkf.ssan_split_active(current_week):  # 11/2 이후 싼세계 (보여 주기만)
            # 11/2~11/9 주: 결정 = 제품 추천 6 + 리뷰 분석 5 / 11/16 주부터: 해결 = 일반 3 + 리뷰 분석 5 (결정 6은 따로)
            rtype = tkf.ssan_review_type(current_week)
            solve = v4_board.ssan_solve_active(current_week)
            gen_slots = tkf.SSAN_GENERAL_SLOTS if solve else tkf.SSAN_PRODUCT_SLOTS
            n_dec, n_rev = wi["types"][rtype], wi["review"]
            planned_line["split"] = {
                "type": rtype, "total_label": rtype, "general_label": "일반" if solve else "제품 추천",
                "decision": n_dec, "product": n_dec - n_rev, "review": n_rev,
                "product_slots": gen_slots, "review_slots": tkf.SSAN_REVIEW_SLOTS,
                "product_over": (n_dec - n_rev) > gen_slots, "review_over": n_rev > tkf.SSAN_REVIEW_SLOTS,
                "product_short": (n_dec - n_rev) < gen_slots, "review_short": n_rev < tkf.SSAN_REVIEW_SLOTS,
            }
        if blog_key == "ssansegae":
            review_ids = {i["id"] for i in week_items if tkf.is_review_item(i, "ssansegae", current_week)}

    naver_mode = "목(mock) 모드 — 가짜 데이터 사용 중" if config.USE_MOCK else "실제 네이버 API 연동 중"
    if config.USE_MOCK and config.MISSING_NAVER_VARS:
        naver_mode += f" (비어있는 값: {', '.join(config.MISSING_NAVER_VARS)})"
    return render_template(
        "index.html",
        queue=week_items,
        sections=sections,
        mode=naver_mode,
        current_week=current_week,
        prev_week=engine.shift_week(current_week, -1),
        next_week=engine.shift_week(current_week, 1),
        all_weeks=all_weeks,
        approved_count=approved_count,
        weekly_queue_size=_queue_size_for(channel_config),
        platform_labels=PLATFORM_LABELS,
        platform_short_labels=PLATFORM_SHORT_LABELS,
        platforms=get_active_platforms(),
        todays_tasks=get_todays_manual_tasks(),
        stale_articles=get_stale_articles(),
        added_id=request.args.get("added"),
        cluster_seeds=sorted({i["cluster_seed"] for i in week_items if i.get("cluster_seed")}),
        channels=CHANNELS,
        active_channel_id=active_channel_id,
        channel_note=channel_config.get("note"),
        is_shopping_channel=channel_config.get("queue_builder") == "shopping",
        sim=sim,
        seed_categories=seed_categories,
        seed_summary=seed_summary,
        blog_ov=blog_ov,
        blog_pair_note=blog_pair_note,
        pair_meta=pair_meta,
        planned_line=planned_line,
        review_ids=review_ids,
        v4_tags=v4_tags,
        fill_count=fill_count,
        exported=request.args.get("exported"),
        exported_tips=request.args.get("exported_tips"),
        lt_plan_exported=request.args.get("lt_plan"),
        plan_terms=_load_plan_terms(),
        weekly_plan_range=weekly_plan_export.week_range_label(current_week) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", current_week) else "",
    )


@app.route("/generate", methods=["POST"])
def generate():
    week = request.form.get("week") or engine.get_week_label()
    if get_active_channel_id() in MANUAL_KEYWORD_CHANNELS:
        # 네이버 일반(꿀팁)·워드프레스 일반은 키워드 추천 엔진이 없습니다 — "새 클러스터 만들기"로 직접 추가만 합니다.
        return redirect(url_for("index", week=week))
    queue = load_queue()

    approved_items = [item for item in queue if item["week"] == week and item["approved"]]
    other_weeks_items = [item for item in queue if item["week"] != week]
    approved_keywords = {item["keyword"] for item in approved_items}

    # 전체 주차를 통틀어 "키워드 -> 가장 최근에 승인했던 주차" 매핑을 만듭니다.
    # (오래된 주차부터 순서대로 덮어써서 가장 최근 주차가 남도록 함)
    approved_history: dict[str, str] = {}
    retired_ids = _retired_ids()  # 묶음에서 뺀 글도 '이미 다룬 주제'로 셉니다(같은 키워드를 다시 기획하지 않도록)
    for item in sorted(queue, key=lambda i: i["week"]):
        if item["approved"] or item["id"] in retired_ids:
            approved_history[item["keyword"]] = item["week"]

    remaining_slots = max(0, _queue_size_for(get_channel_config()) - len(approved_items))
    # 채널마다 키워드를 뽑는 엔진 자체가 다릅니다 — 정책/제도 콘텐츠는 seasonal_calendar.json +
    # 정책용 검색의도로, 쇼핑 콘텐츠는 shopping_calendar.json + 쇼핑용 검색의도(추천·비교·후기
    # 등)로 뽑습니다. 둘 다 같은 클러스터링·실검증 엔진을 재사용하고, 시드/의도 판단만 다릅니다.
    if get_channel_config()["queue_builder"] == "shopping":
        # 숍도 롱텀처럼 상시 시드를 카테고리별로 돌려 씁니다 (이번 주보다 이전 주차 이력 기준)
        seed_last_used = _compute_seed_last_used(queue, load_articles(), get_active_platforms(), week)
        new_items = engine.build_shopping_weekly_queue(
            top_n=remaining_slots,
            week=week,
            exclude_keywords=approved_keywords,
            approved_history=approved_history,
            seed_last_used=seed_last_used,
            existing_seed_counts=_count_seed_items(approved_items),
        )
        _save_seed_selection(
            week, engine.LAST_RUN_INFO.get("seed_selection"), _seed_selection_file_for(get_active_channel_id())
        )
    else:
        # 롱텀 다양성 규칙에 쓸 이력: ① 시드별로 마지막에 승인·글 생성된 주차, ② 이번 주에 이미 승인돼 유지 중인
        # 시드별 항목 수(클러스터 칸 수 제한용)
        seed_last_used = _compute_seed_last_used(queue, load_articles(), get_active_platforms(), week)
        existing_seed_counts = _count_seed_items(approved_items)

        new_items = engine.build_longterm_weekly_queue(
            top_n=remaining_slots,
            week=week,
            exclude_keywords=approved_keywords,
            approved_history=approved_history,
            seed_last_used=seed_last_used,
            existing_seed_counts=existing_seed_counts,
        )
        _save_seed_selection(week, engine.LAST_RUN_INFO.get("seed_selection"))

    updated_queue = other_weeks_items + approved_items + new_items
    save_queue(updated_queue)
    return redirect(url_for("index", week=week))


@app.route("/set-cluster-platform", methods=["POST"])
def set_cluster_platform():
    """
    클러스터(같은 week + cluster_seed) 전체에 '이번 주 담당 플랫폼'을 지정합니다.
    이건 다른 플랫폼을 막는 게 아니라, 이번 주에 어디부터 쓸지 계획해두는 표시일 뿐입니다.
    나중에 다른 플랫폼용으로도 얼마든지 이어서 쓸 수 있습니다.
    """
    week = request.form.get("week")
    cluster_seed = request.form.get("cluster_seed")
    platform = request.form.get("platform") or ""

    queue = load_queue()
    for item in queue:
        if item.get("week") == week and item.get("cluster_seed") == cluster_seed:
            item["target_platform"] = platform if platform in PLATFORMS else None
    save_queue(queue)

    # 대시보드에서 fetch()로 부를 때는 화면 전체를 새로고침하지 않고 그 자리에서 바로
    # 반영합니다 (스크롤 위치가 맨 위로 튀는 문제 방지). 직접 이 주소로 접속한 경우를 위해
    # 일반 폼 제출(리다이렉트)도 계속 지원합니다.
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": True, "platform": platform}
    return redirect(request.referrer or url_for("index", week=week))


@app.route("/set-planned-type", methods=["POST"])
def set_planned_type():
    """네이버·싼세계 주간 승인 화면에서 항목 1개의 계획 유형(planned_type)만 바꿉니다. ""은 미정.
    허용 값은 target_keyword_finder.BLOGS[블로그]["types"]. 다른 채널·허용 안 되는 값은 저장하지 않고 오류를 돌려줍니다."""
    import target_keyword_finder as tkf
    channel_id = get_active_channel_id()
    blog = {ch: b for b, ch, _ in TARGET_APPROVE_BLOGS}.get(channel_id)
    article_id = request.form.get("article_id") or ""
    ptype = request.form.get("planned_type") or ""
    is_fetch = request.headers.get("X-Requested-With") == "fetch"

    def fail(msg, code):
        if is_fetch:
            return {"ok": False, "error": msg}, code
        return redirect(request.referrer or url_for("index"))

    if not blog:
        return fail("N살림노트·W싼세계 채널에서만 바꿀 수 있습니다.", 400)
    if ptype and ptype not in tkf.BLOGS[blog]["types"]:
        return fail("이 채널에서 쓸 수 없는 유형입니다.", 400)

    queue = load_queue()
    item = find_queue_item(queue, article_id)
    if not item:
        return fail("항목을 찾을 수 없습니다.", 404)
    item["planned_type"] = ptype
    # 싼세계 11/2 이후 주차: "리뷰 분석"(planned_review=true)·"결정형/일반 해결형"(false)을 직접 고른 값으로 저장.
    # 리뷰 분석이 들어가는 유형(ssan_review_type: 11/2~11/9 주 = 결정, 11/16 주부터 = 해결)이 아니면 지웁니다.
    # 화면이 planned_review를 보냈을 때만 건드립니다(11/2 이전 주차·네이버는 칸을 만들지 않음).
    pr = request.form.get("planned_review")
    if blog == "ssansegae" and tkf.ssan_split_active(item.get("week")):
        if ptype != tkf.ssan_review_type(item.get("week")):
            item.pop("planned_review", None)
        elif pr in ("1", "0"):
            item["planned_review"] = (pr == "1")
    save_queue(queue)

    if is_fetch:
        wi = _target_week_info(item["week"])[blog]
        return {"ok": True, "planned_type": ptype, "is_review": tkf.is_review_item(item, blog, item.get("week")),
                "counts": {"types": wi["types"], "undecided": wi["undecided"], "approved": wi["approved"], "review": wi["review"]}}
    return redirect(request.referrer or url_for("index", week=item.get("week")))


@app.route("/set-serp", methods=["POST"])
def set_serp():
    """네이버·싼세계 주간 승인 화면에서 항목 1개의 경쟁(serp.compete)·검색 확인 메모(serp_note)만 저장합니다.
    serp 안의 다른 값(peak_week·weeks_to_peak)과 다른 칸은 건드리지 않습니다. 폼에 없는 칸은 바꾸지 않습니다."""
    channel_id = get_active_channel_id()
    is_fetch = request.headers.get("X-Requested-With") == "fetch"

    def fail(msg, code):
        return ({"ok": False, "error": msg}, code) if is_fetch else redirect(request.referrer or url_for("index"))

    if channel_id not in {ch for _, ch, _ in TARGET_APPROVE_BLOGS}:
        return fail("N살림노트·W싼세계 채널에서만 바꿀 수 있습니다.", 400)
    compete = request.form.get("compete")
    if compete is not None and compete not in TARGET_COMPETE_VALUES + ("",):
        return fail("알 수 없는 경쟁 값입니다.", 400)
    queue = load_queue()
    item = find_queue_item(queue, request.form.get("article_id") or "")
    if not item:
        return fail("항목을 찾을 수 없습니다.", 404)
    if compete is not None:
        sp = dict(item.get("serp") or {})
        sp.setdefault("peak_week", None)
        sp.setdefault("weeks_to_peak", None)
        sp["compete"] = compete or None
        item["serp"] = sp
    if "serp_note" in request.form:
        note = re.sub(r"\s+", " ", request.form.get("serp_note") or "").strip()[:40]
        if note:
            item["serp_note"] = note
        else:
            item.pop("serp_note", None)
    save_queue(queue)
    if is_fetch:
        return {"ok": True, "compete": (item.get("serp") or {}).get("compete") or "", "serp_note": item.get("serp_note", "")}
    return redirect(request.referrer or url_for("index", week=item.get("week")))


@app.route("/toggle-approved/<article_id>", methods=["GET", "POST"])
def toggle_approved(article_id):
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    approved = False
    if item:
        item["approved"] = not item["approved"]
        approved = item["approved"]
    save_queue(queue)

    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": True, "approved": approved}
    return redirect(request.referrer or url_for("index"))


def _make_custom_item(keyword: str, week: str, cluster_seed: str, role: str, **overrides) -> dict:
    """직접 추가한 키워드의 대기열 항목(승인된 상태). 타깃 키워드 승인도 이 함수를 써서, overrides로 값만 바꿉니다."""
    item = {
        "id": engine.make_item_id(keyword, week),
        "week": week,
        "keyword": keyword,
        "score": None,
        "total_search_volume": None,
        "competition": "직접추가",
        "approved": True,
        "published": False,
        "duplicate_of_past_approval": False,
        "duplicate_of_past_approval_week": None,
        "cluster_seed": cluster_seed,
        "cluster_role": role,
        "target_platform": None,
        "manually_added": True,
    }
    item.update(overrides)
    return item


def _insert_custom_item(queue: list[dict], new_item: dict) -> None:
    """같은 클러스터의 마지막 항목 바로 뒤에 끼워 넣어서, 화면에서 그 클러스터 묶음 안에
    같이 보이게 합니다 (그냥 맨 끝에 붙이면 같은 클러스터인데 화면 맨 아래 따로 떨어져 보임)."""
    insert_at = len(queue)
    for i in range(len(queue) - 1, -1, -1):
        if queue[i]["week"] == new_item["week"] and queue[i].get("cluster_seed") == new_item.get("cluster_seed"):
            insert_at = i + 1
            break
    queue.insert(insert_at, new_item)


def _red_checks_for_new_keyword(
    channel_id: str, queue: list[dict], articles: dict, week: str, keyword: str, cluster_seed: str
) -> tuple[list[dict], list[dict]]:
    """저장 전 🔴 확인: (이미 있는 글과 거의 같음, 같은 블로그의 중복 위험 🔴). 채널 id를 받아서 어느 채널이든 쓸 수 있습니다.
    같은 블로그의 "짝 글"은 막지 않습니다."""
    red_matches = find_red_matches_for_new_keyword(
        queue, articles, week, keyword, cluster_seed, platforms=CHANNELS[channel_id]["platforms"]
    )
    blog_one = _blog_overlap_for_one(keyword, cluster_seed, channel_id=channel_id)
    blog_dups = [
        m for m in (blog_one or {}).get("matches", [])
        if m["relation"] == blog_overlap.RELATION_DUP and m["level"] == "red"
    ]
    return red_matches, blog_dups


@app.route("/add-custom-keyword", methods=["POST"])
def add_custom_keyword():
    """
    리서치 클로드(또는 직접 찾은)가 알려준, 아직 로컬앱 큐에 없는 키워드를 즉시 추가합니다.
    네이버 API로 뽑은 게 아니라서 점수/검색량 데이터는 없고, 대신 "직접추가" 표시가 붙습니다.
    추가되는 즉시 승인된 상태로 들어가서, 바로 편집기로 넘어가 글을 붙여넣을 수 있습니다 —
    "추가 → 승인" 두 단계를 따로 거칠 필요가 없게 만든 것입니다.
    """
    week = request.form.get("week", "").strip()
    keyword = request.form.get("keyword", "").strip()
    cluster_seed_input = request.form.get("cluster_seed", "").strip()
    cluster_seed = cluster_seed_input or keyword
    role = request.form.get("role", "세부")
    if role not in ("대표", "세부"):
        role = "세부"
    if not cluster_seed_input:
        role = "대표"  # 클러스터를 새로 만드는 것이므로, 그 클러스터의 대표가 되는 게 맞습니다.

    if not week or not keyword:
        return redirect(url_for("index", week=week))

    queue = load_queue()

    # 같은 주차에 이미 똑같은 키워드가 있으면 새로 안 만들고, 있던 항목으로 그냥 이동합니다.
    existing = next((i for i in queue if i["week"] == week and i["keyword"] == keyword), None)
    if existing:
        if get_active_channel_id() in RADAR_CHANNELS:  # 생활레이더: 새 글 시작 → 바로 편집 화면
            return redirect(url_for("edit_article", article_id=existing["id"]))
        return redirect(url_for("index", week=week, added=existing["id"]))

    # 저장 전에 이미 있는 글과 '거의 같은지(🔴)' 확인합니다. 있으면 저장하지 않고 목록을 보여준 뒤,
    # 사용자가 "그래도 추가"를 눌렀을 때(confirm_dup=1)만 아래로 계속 진행합니다.
    if request.form.get("confirm_dup") != "1":
        red_matches, blog_dups = _red_checks_for_new_keyword(get_active_channel_id(), queue, load_articles(), week, keyword, cluster_seed)
        if red_matches or blog_dups:
            return render_template(
                "similar_confirm.html",
                week=week,
                keyword=keyword,
                cluster_seed_input=cluster_seed_input,
                role=role,
                kind=request.form.get("kind", ""),
                matches=red_matches,
                blog_matches=blog_dups,
            )

    overrides = {}
    if get_active_channel_id() == "wp_tips" and request.form.get("kind") in ("해결", "결정", "review"):
        # 싼세계 직접 추가에서 유형을 고른 경우만: "review"(리뷰 분석)는 planned_review=true (11/2 이후 주차만)
        # 유형은 11/2~11/9 주는 결정, 11/16 주부터는 해결(일반글)로 저장합니다.
        import target_keyword_finder as tkf
        kind = request.form.get("kind")
        if kind != "review" or tkf.ssan_split_active(week):
            rtype = tkf.ssan_review_type(week)
            overrides["planned_type"] = (rtype if kind == "review" else kind)
            if tkf.ssan_split_active(week) and overrides["planned_type"] == rtype:
                overrides["planned_review"] = (kind == "review")  # 리뷰 분석 = true, 그 유형의 일반(결정형/일반 해결형) = false
    new_item = _make_custom_item(keyword, week, cluster_seed, role, **overrides)
    _insert_custom_item(queue, new_item)
    save_queue(queue)

    if get_active_channel_id() in RADAR_CHANNELS:  # 생활레이더: 새 글 시작 → 바로 편집 화면
        return redirect(url_for("edit_article", article_id=new_item["id"]))

    # 같은 블로그에 "짝 글"이 있으면 막지 않고, 추가 후 대시보드 상단에 한 줄로 안내합니다(조회 전용).
    paired = any(
        m["relation"] == blog_overlap.RELATION_PAIR
        for m in (_blog_overlap_for_one(keyword, cluster_seed, new_item["id"]) or {}).get("matches", [])
    )
    if paired:
        return redirect(url_for("index", week=week, added=new_item["id"], paired=1))
    return redirect(url_for("index", week=week, added=new_item["id"]))


# ---------- 글 편집기 (2단계) ----------

def get_article(article_id: str) -> dict:
    defaults = {
        "raw_paste": "",
        "platform_title": {p: "" for p in PLATFORMS},
        "platform_raw": {p: "" for p in PLATFORMS},
        "platform_final": {p: "" for p in PLATFORMS},
        "published": {p: False for p in PLATFORMS},
        "platform_generated_at": {p: None for p in PLATFORMS},  # 이 플랫폼용으로 처음 콘텐츠를 붙여넣은 시각
        "platform_published_at": {p: None for p in PLATFORMS},  # 이 플랫폼에 발행 체크한 시각
        "platform_scheduled_time": {p: None for p in PLATFORMS},  # 예약발행 시 "HH:MM" (날짜는 published_at에 있음)
        "platform_published_url": {p: None for p in PLATFORMS},  # 실제 발행된 글 주소 (내부링크 자동 치환용)
        "platform_post_id": {p: None for p in PLATFORMS},  # 실제 발행된 글의 ID (나중에 재동기화용)
        "platform_metadata": {p: "" for p in PLATFORMS},  # 메타디스크립션/카테고리/태그/alt텍스트 등 참고용 메모
        "platform_schema": {p: "" for p in PLATFORMS},  # 구조화 데이터(JSON-LD) — 본문과 분리해서 따로 복사하도록
        "body_images": [],
        "body_image_alts": [],  # body_images와 순서를 맞춘 alt 텍스트 (메타데이터 메모에서 자동으로 채움)
        "platform_body_images": {p: [] for p in PLATFORMS},
        "platform_body_image_alts": {p: [] for p in PLATFORMS},
        "platform_body_image_headings": {p: [] for p in PLATFORMS},  # 롱텀·네이버·싼세계: 사진별로 붙일 소제목(없으면 ""=고르게). 이 필드가 없는 옛 글은 예전 배치
        "platform_body_images_nv75": {p: [] for p in PLATFORMS},  # 네이버(naver_tips): platform_body_images와 같은 순서의 "네이버용 75% 여백 파일" 경로("" = 없음)
        "covers": {p: "" for p in PLATFORMS},
        "cover_alts": {p: "" for p in PLATFORMS},  # 표지 이미지 alt 텍스트
        "cover_backgrounds": {p: "" for p in PLATFORMS},  # 표지 시안A용 원본 배경 파일 경로("표지 다시 만들기"에 씀)
        "cover_title_fallback": {p: False for p in PLATFORMS},  # 표지 문구가 없어서 제목으로 대신했는지
        "parse_ok": None,               # 지금까지 하나라도 플랫폼이 채워졌는지 (누적)
        "last_parse_ok": None,          # 방금 붙여넣기 시도가 뭔가 인식은 했는지
        "last_parsed_platforms": [],    # 방금 붙여넣기로 새로 채워진 플랫폼들
        "missing_platforms": [],
        "cardnews_images": {p: [] for p in PLATFORMS},  # 카드뉴스(인스타/쓰레드용) 이미지 경로 목록
        "cardnews_caption": {p: "" for p in PLATFORMS},  # 카드뉴스와 함께 올릴 본문 캡션
        "cardnews_topic_tag": {p: "" for p in PLATFORMS},  # 쓰레드 주제 태그 추천(글당 1개)
        # 숍 채널 전용 — 업로드/생성 이미지를 한 곳에 모아두고 "사용하기"로 선택.
        # body: 워드프레스 SHOP 본문 이미지(업로드 순서 그대로 글 맨 아래에 삽입, 전부 사용)
        "shop_image_pool": {"cover": [], "products": {}, "body": []},
        "manual_promo": {      # 수동으로 진행하는 홍보 작업(인스타/쓰레드)의 예정 시각·완료 여부
            "instagram": {"scheduled_at": "", "done": False},
            "threads": {"scheduled_at": "", "done": False},
        },
    }
    articles = load_articles()
    stored = articles.get(article_id, {})
    merged = defaults.copy()
    merged.update(stored)
    merged["platform_title"] = {**defaults["platform_title"], **stored.get("platform_title", {})}
    merged["platform_raw"] = {**defaults["platform_raw"], **stored.get("platform_raw", {})}
    merged["platform_final"] = {**defaults["platform_final"], **stored.get("platform_final", {})}
    merged["published"] = {**defaults["published"], **stored.get("published", {})}
    merged["platform_generated_at"] = {**defaults["platform_generated_at"], **stored.get("platform_generated_at", {})}
    merged["platform_published_at"] = {**defaults["platform_published_at"], **stored.get("platform_published_at", {})}
    merged["platform_scheduled_time"] = {**defaults["platform_scheduled_time"], **stored.get("platform_scheduled_time", {})}
    merged["platform_published_url"] = {**defaults["platform_published_url"], **stored.get("platform_published_url", {})}
    merged["platform_post_id"] = {**defaults["platform_post_id"], **stored.get("platform_post_id", {})}
    merged["platform_metadata"] = {**defaults["platform_metadata"], **stored.get("platform_metadata", {})}
    merged["platform_schema"] = {**defaults["platform_schema"], **stored.get("platform_schema", {})}
    merged["covers"] = {**defaults["covers"], **stored.get("covers", {})}
    merged["cover_alts"] = {**defaults["cover_alts"], **stored.get("cover_alts", {})}
    merged["cover_backgrounds"] = {**defaults["cover_backgrounds"], **stored.get("cover_backgrounds", {})}
    merged["cover_title_fallback"] = {**defaults["cover_title_fallback"], **stored.get("cover_title_fallback", {})}
    merged["body_image_alts"] = stored.get("body_image_alts") or defaults["body_image_alts"]
    merged["platform_body_images"] = {**defaults["platform_body_images"], **stored.get("platform_body_images", {})}
    merged["platform_body_image_alts"] = {**defaults["platform_body_image_alts"], **stored.get("platform_body_image_alts", {})}
    merged["platform_body_image_headings"] = {**defaults["platform_body_image_headings"], **stored.get("platform_body_image_headings", {})}
    merged["platform_body_images_nv75"] = {**defaults["platform_body_images_nv75"], **stored.get("platform_body_images_nv75", {})}
    merged["cardnews_images"] = {**defaults["cardnews_images"], **stored.get("cardnews_images", {})}
    merged["cardnews_caption"] = {**defaults["cardnews_caption"], **stored.get("cardnews_caption", {})}
    merged["cardnews_topic_tag"] = {**defaults["cardnews_topic_tag"], **stored.get("cardnews_topic_tag", {})}
    merged["shop_image_pool"] = stored.get("shop_image_pool") or defaults["shop_image_pool"]
    merged["shop_image_pool"].setdefault("cover", [])
    merged["shop_image_pool"].setdefault("products", {})
    merged["shop_image_pool"].setdefault("body", [])
    stored_manual_promo = stored.get("manual_promo", {})
    merged["manual_promo"] = {
        ch: {**defaults["manual_promo"][ch], **stored_manual_promo.get(ch, {})}
        for ch in defaults["manual_promo"]
    }
    return merged


def set_article(article_id: str, data: dict) -> None:
    articles = load_articles()
    articles[article_id] = data
    save_articles(articles)


@app.route("/article/<article_id>/edit")
def edit_article(article_id):
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    if not item:
        return redirect(url_for("index"))

    article = get_article(article_id)
    checklist = {
        "기본자료 붙여넣기": bool(article["parse_ok"]),
        "이미지 생성": bool(article["body_images"] and any(article["covers"].values())),
        "플랫폼별 콘텐츠 생성": bool(any(article["platform_final"].values())),
    }
    if get_active_channel_id() in RADAR_CHANNELS:  # 생활레이더: 이미지는 표지 1장(16:9) + 카드(선택)
        checklist["이미지 생성"] = bool(article["covers"].get("naver"))

    # "③ 플랫폼별 콘텐츠 생성" 버튼 활성화 조건 — 롱텀(정책) 채널은 본문 이미지 일괄
    # 생성(body_images)을 쓰지만, 숍 채널은 이미지를 "②-1 이미지 풀"(shop_image_pool)에서
    # 사람이 직접 골라 쓰므로 body_images가 항상 비어 있습니다(gen_images가 숍 채널이면
    # body_images를 안 채우고 건너뜀). 그래서 채널별로 다른 필드를 봐야 합니다.
    # 상품 이미지는 선택사항입니다 — shop_formatter.py는 상품 섹션에 이미지가 없어도
    # 텍스트 카드로 정상 렌더링하므로, 표지 이미지 1장만 확정되면 발행 가능해야 합니다.
    if get_channel_config().get("queue_builder") == "shopping":
        can_generate_final = any(e.get("selected") for e in article["shop_image_pool"]["cover"])
    elif get_active_channel_id() in RADAR_CHANNELS:
        # N데스크피커: 장부(covers.naver) 대신 그 글 사진 폴더 photos.json 에 표지(cover)가 있으면 켜짐(장부엔 안 씀)
        _rctx = _radar_desk_context(article_id)
        can_generate_final = bool(_rctx and radar_screen.has_cover(_rctx[2]))
    else:
        can_generate_final = bool(article["body_images"])

    # 워드프레스 예약/발행 날짜 + 시각을 합쳐서, "인스타/쓰레드도 이 시각으로 맞추기"
    # 버튼에 바로 채워 넣을 수 있게 합니다 (datetime-local 입력칸 형식: YYYY-MM-DDTHH:MM).
    wp_date = article["platform_published_at"].get("wordpress")
    wp_time = article["platform_scheduled_time"].get("wordpress") or "09:00"
    wp_scheduled_datetime = f"{wp_date}T{wp_time}" if wp_date else ""

    return render_template(
        "article_edit.html",
        idx=article_id,
        keyword=item["keyword"],
        article=article,
        checklist=checklist,
        can_generate_final=can_generate_final,
        platforms=get_active_platforms(),
        platform_labels=PLATFORM_LABELS,
        platform_statuses={p: platform_status(article, p) for p in get_active_platforms()},
        meta_seo={p: _meta_description_seo_check(article["platform_metadata"][p]) for p in get_active_platforms()},
        raw_check={
            p: _raw_check(article["platform_raw"][p])
            for p in get_active_platforms() if article["platform_raw"].get(p)
        } if get_active_channel_id() in RAW_CHECK_CHANNELS else {},
        target_platform=item.get("target_platform"),
        plan_view=_plan_view(item),
        image_mode="목(mock) 모드" if image_gen.USE_MOCK else "실제 GPT Image mini API 연동",
        wp_scheduled_datetime=wp_scheduled_datetime,
        is_shopping_channel=get_channel_config().get("queue_builder") == "shopping",
        # 표지 이미지 풀(업로드·합성)은 숍 채널뿐 아니라 네이버 일반(꿀팁)·워드프레스 일반에서도 씁니다
        # (직접 찍은 사진으로 표지를 만들 때용) — 다만 상품 이미지 풀은 숍 채널 전용입니다.
        show_image_pool=get_channel_config().get("queue_builder") == "shopping" or is_ai_info_channel(),
        lt_cover=_lt_cover_context(item) if get_active_channel_id() == "longterm_wp" else None,
        radar=_radar_info(article) if get_active_channel_id() in RADAR_CHANNELS else None,
        desk=_radar_desk_view(article_id) if get_active_channel_id() in RADAR_CHANNELS else None,  # N데스크피커 ② 새 화면 값
        desk_msg=request.args.get("desk_msg", ""),
        # 네이버(naver_tips) 본문 사진 칸: 미리보기는 원본 사진(네이버용 75% 파일 기능은 radar169_nv75off_20261027에 꺼짐)
        naver_body=_naver_body_info(article) if get_active_channel_id() in NV75_CHANNELS else None,
        # 싼세계 "외부 이미지 추가" 칸 (다른 채널은 빈 값)
        ext_images=_ext_view(article) if get_active_channel_id() in EXTERNAL_IMAGE_CHANNELS else [],
        ext_headings=[s["heading"] for s in _ext_sections(article)] if get_active_channel_id() in EXTERNAL_IMAGE_CHANNELS else [],
        ext_msg=request.args.get("ext_msg", ""),
        shop_ai_msg=session.pop("shop_ai_cover_msg", ""),
        autofill_warn=session.pop("naver_autofill_warn", ""),
        memo_warn=session.pop("memo_paste_warn", ""),
        shop_bulk_msg=session.pop("shop_bulk_msg", ""),
        product_slots=_shop_product_slots(article),
        bad_naver_url=_bad_naver_url_warning(article),
        blog_box=_blog_overlap_for_one(item["keyword"], item.get("cluster_seed"), item["id"]),
        v4_info=(
            _v4_tag(load_articles().get(article_id) or {}, get_active_channel_id(), article_id, _load_v4_excluded())
            if get_active_channel_id() in v4_board.V4_CHANNELS else None
        ),
    )


@app.route("/article/<article_id>/paste", methods=["POST"])
def paste_content(article_id):
    raw = request.form.get("raw_paste", "")
    parsed = content_parser.parse_pasted_content(raw)

    article = get_article(article_id)
    article["raw_paste"] = raw
    article["last_parse_ok"] = parsed["parse_ok"]

    # 네이버 일반(꿀팁)는 워드프레스·티스토리로 올릴 일이 없으므로, 붙여넣은 글에 다른 플랫폼 섹션이 섞여 있어도
    # 이 채널이 쓰는 플랫폼(네이버)만 저장합니다. 워드프레스 일반도 같은 방식으로 워드프레스만 저장합니다.
    # (다른 채널은 예전처럼 인식된 플랫폼을 전부 반영)
    own_platform_only = get_active_channel_id() in MANUAL_KEYWORD_CHANNELS
    allowed_platforms = get_active_platforms() if own_platform_only else PLATFORMS
    touched_platforms = [p for p in PLATFORMS if p in allowed_platforms and parsed["platform_raw"].get(p)]
    article["last_parsed_platforms"] = touched_platforms

    # 이번에 새로 인식된 플랫폼만 갱신합니다. 이미 완료해둔 다른 플랫폼 내용은 건드리지 않습니다
    # (예: 워드프레스를 완성해둔 뒤 몇 달 후 네이버만 새로 붙여넣어도 워드프레스는 그대로 남습니다).
    for p in touched_platforms:
        article["platform_title"][p] = parsed["platform_title"][p]
        article["platform_raw"][p] = parsed["platform_raw"][p]
        article["platform_generated_at"][p] = now_iso()
        # 표지/본문 이미지는 여기서 더 이상 지우지 않습니다. 예전엔 붙여넣을 때마다 자동으로
        # 비워버려서, 같은 글을 길게 늘려서 다시 붙여넣기만 해도 이미지가 통째로 사라지는
        # 버그가 있었습니다 — 본문만 고친 경우엔 기존 이미지를 그대로 재사용하는 게 맞고,
        # 완전히 새 주제로 바뀌어서 이미지도 새로 만들고 싶으면 "이미지 일괄 생성"을
        # 직접 눌러서 명시적으로 새로 만들면 됩니다. 최종본(platform_final)은 어차피
        # "최종본 다시 생성"을 누르면 새로 만들어지므로 미리 비워둡니다.
        article["platform_final"][p] = ""

    article["missing_platforms"] = (
        [PLATFORM_LABELS[p] for p in allowed_platforms if p not in touched_platforms and not article["platform_raw"].get(p)]
        if own_platform_only
        else parsed["missing_platforms"]
    )
    article["parse_ok"] = any(article["platform_raw"].values())  # 지금까지 누적으로 하나라도 채워졌으면 True

    if get_active_channel_id() in RADAR_CHANNELS:
        _radar_sync(article)  # 생활레이더: 글 유형·쓰레드 카드 자리(thread_card) 저장

    set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id))


def _body_image_count_for_headings(heading_count: int) -> int:
    """
    소제목(H2) 개수에 비례해서 본문 이미지 개수를 정합니다. 예전엔 무조건 플랫폼당 2장
    고정이라, 글이 짧든 길든(H2가 3개든 8개든) 이미지 배치 패턴이 항상 똑같아 보이는
    문제가 있었습니다 — 글 구조를 다양화하는 프롬프트 지침(2026-09)과 맞물려서, 이미지
    개수도 실제 글 길이에 맞게 자연스럽게 달라지게 합니다.
    """
    if heading_count <= 3:
        return 1
    if heading_count <= 5:
        return 2
    if heading_count <= 7:
        return 3
    return 4


def _body_char_count(raw: str) -> int:
    """원고의 공백 제외 글자 수. {{LINK:…}}·{{SHOP:…}} 자리표시자·URL·HTML 태그·마크다운 기호(#, **, >, 목록 기호, 표 구분자)는 뺍니다."""
    text = re.sub(r"\{\{(?:LINK|SHOP):.*?\}\}", "", raw or "")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*$", "", text, flags=re.MULTILINE)  # 표 구분줄 |---|---|
    text = re.sub(r"^\s*(?:#+|>|[-*+])\s", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("|", "")
    return len(re.sub(r"\s+", "", text))


def _longterm_body_image_count(raw: str) -> int:
    """롱텀 본문 이미지 개수(플랫폼당) — 원고 길이 기준 (위쪽 LONGTERM_BODY_IMAGE_* 상수)."""
    if _body_char_count(raw) >= LONGTERM_BODY_IMAGE_CHAR_THRESHOLD:
        return LONGTERM_BODY_IMAGES_LONG
    return LONGTERM_BODY_IMAGES_SHORT


def _lt_cover_context(item: dict) -> dict:
    """편집 화면의 '이 묶음 표지' 선택 칸용 값(읽기만 합니다 — 배정은 표지를 처음 만들 때 이뤄집니다)."""
    key = _lt_style_key(item)
    if image_gen.LT_COVER_UNIFIED:  # 새 통일 표지: 띠 = 카테고리, 글자 색 = 묶음별 7색
        cat_raw = (item or {}).get("category") or ""
        try:
            memo = next((v for v in get_article(item["id"])["platform_metadata"].values() if v), "") if item.get("id") else ""
            cat_raw = metadata_parser.parse_metadata_memo(memo).get("category") or cat_raw
        except Exception:
            pass
        cur = image_gen.load_lt_cover_accents().get(key)
        return {
            "unified": True,
            "key": key,
            "category": image_gen.LT_UNIFIED_OLD_NAMES.get((cat_raw or "").strip(), (cat_raw or "").strip()) or image_gen.LT_UNIFIED_DEFAULT_CATEGORY,
            "accents": list(image_gen.LT_UNIFIED_ACCENTS),
            "accent": cur.get("accent") if isinstance(cur, dict) else None,
            "msg": session.pop("lt_cover_msg", ""),
        }
    return {
        "unified": False,
        "key": key,
        "style": image_gen.load_lt_cover_styles().get(key),
        "templates": image_gen.LT_COVER_TEMPLATES,
        "colors": list(image_gen.LT_COVER_COLORS),
        "msg": session.pop("lt_cover_msg", ""),
    }


def _lt_style_key(item: dict | None, fallback_keyword: str = "") -> str:
    """롱텀 표지 묶음 키: cluster_seed, 없으면 keyword."""
    item = item or {}
    return item.get("cluster_seed") or item.get("keyword") or fallback_keyword


@app.route("/article/<article_id>/gen-images", methods=["POST"])
def gen_images(article_id):
    if get_active_channel_id() in RADAR_CHANNELS:
        # 생활레이더는 표지 1장(16:9)·카드 전용 버튼을 씁니다 — 일반 이미지 일괄 생성(16:9, 비용 발생)은 직접 호출로도 막습니다.
        return redirect(url_for("edit_article", article_id=article_id))
    if get_active_channel_id() in ("shop_naver", "shop_wp"):
        # 숍 채널(네이버·워드프레스)은 AI 이미지 생성을 쓰지 않습니다 (업로드한 사진 + 제목 합성만).
        return redirect(url_for("edit_article", article_id=article_id))
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    article = get_article(article_id)
    if not article["parse_ok"]:
        return redirect(url_for("edit_article", article_id=article_id))

    _apply_image_output_context(article_id)

    keyword = item["keyword"] if item else next((t for t in article["platform_title"].values() if t), "")
    reference_raw = article["platform_raw"].get("wordpress") or next(
        (v for v in article["platform_raw"].values() if v), ""
    )

    active_platforms = [p for p in PLATFORMS if article["platform_title"].get(p)]
    n_active = len(active_platforms) or 1
    is_shopping = get_channel_config().get("queue_builder") == "shopping"
    is_info_channel = is_ai_info_channel()
    is_longterm = get_active_channel_id() == "longterm_wp"
    lt_cover_on = is_longterm and not is_shopping and not is_info_channel and image_gen.COVER_STYLE != "legacy"

    # 저장해둔 메타데이터 메모(있는 플랫폼 아무거나)에서 alt 텍스트 제안을 뽑아서, 소제목
    # 기반 추측 대신 이걸 이미지 생성 프롬프트와 실제 alt 텍스트로 씁니다.
    memo_text = next((v for v in article["platform_metadata"].values() if v), "")
    parsed_memo = metadata_parser.parse_metadata_memo(memo_text)

    # 숍 채널은 본문 이미지를 "②-1 이미지 풀"(업로드/레퍼런스 바리에이션)에서만 쓰므로,
    # 여기서 옛날 방식(소제목 기반 텍스트 생성)으로 본문 이미지를 만드는 건 낭비입니다.
    # 표지만 생성하고 본문 이미지 생성은 건너뜁니다.
    if is_shopping:
        total_body_images = 0
        images_per_platform = 0
    elif is_info_channel:
        # 네이버 일반(꿀팁)·워드프레스 일반은 가벼운 정보 글이라 소제목 수와 무관하게 고정 INFO_BODY_IMAGE_COUNT장입니다.
        images_per_platform = _info_body_image_count(get_active_channel_id())
        total_body_images = images_per_platform * n_active
    elif is_longterm:
        # 롱텀은 메모의 본문 장면이 3~5개면 그 개수만큼, 아니면 원고 길이(공백 제외 글자 수)로 정합니다 —
        # 기준·장수는 파일 위쪽 LONGTERM_BODY_IMAGE_* 상수.
        n_memo_scenes = len(parsed_memo["body_alt_pairs"])
        if 3 <= n_memo_scenes <= 5:
            images_per_platform = n_memo_scenes
        else:
            images_per_platform = _longterm_body_image_count(reference_raw)
        total_body_images = images_per_platform * n_active
    else:
        # 실제 소제목(##) 개수를 먼저 세고, 거기에 비례해서 이미지 개수를 정합니다(고정 2장이 아님).
        raw_heading_count = len(re.findall(r"^##\s+(.+)$", reference_raw, flags=re.MULTILINE))
        images_per_platform = _body_image_count_for_headings(raw_heading_count)
        total_body_images = images_per_platform * n_active

    headings = (
        seo_formatter.extract_section_headings(reference_raw, min_count=total_body_images, max_count=total_body_images)
        if total_body_images
        else []
    )

    result = image_gen.generate_assets(
        keyword=keyword,
        headings=headings,
        platform_titles=article["platform_title"],
        article_id=article_id,
        body_image_count=total_body_images,
        body_alt_suggestions=parsed_memo["body_alts"] or None,
        cover_alt_suggestion=parsed_memo["cover_alt"] or None,
        situational_cover=is_shopping,
        cover_badge=parsed_memo["cover_badge"],
        cover_line1=parsed_memo["cover_line1"],
        cover_line2=parsed_memo["cover_line2"],
        cover_sub=parsed_memo["cover_sub"],
        channel=get_active_channel_id(),
        # 꿀팁 두 채널(네이버 일반·워드프레스 일반)만: 메모의 장면 설명(alt)을 이미지 장면으로 그대로 씁니다.
        scene_from_alt=is_info_channel,
        # 롱텀(워드프레스)만: 메모의 표지 alt·본문 장면을 순서대로 그대로 이미지 장면으로 씁니다.
        longterm_scene_from_memo=is_longterm and not is_shopping and not is_info_channel,
        body_alt_pairs=parsed_memo["body_alt_pairs"],
        # 롱텀만: 묶음(cluster_seed)별로 배정된 새 표지 디자인(A/B/C)·색. 처음이면 여기서 랜덤 배정해 저장합니다.
        # (LT_COVER_UNIFIED 켜짐이면 묶음별 배정을 만들지도 기록하지도 않습니다 — 카테고리로 색이 정해짐)
        lt_style=(
            ({"template": "U", "color": ""} if image_gen.LT_COVER_UNIFIED
             else image_gen.get_or_assign_lt_cover_style(_lt_style_key(item, keyword)))
            if lt_cover_on else None
        ),
        # 통일 표지의 색은 원고 메모 "카테고리 제안"(parsed_memo["category"]) 우선, 없으면 대기열 항목의 카테고리
        lt_category=parsed_memo.get("category") or (item or {}).get("category") or "",
        lt_cluster_key=_lt_style_key(item, keyword),
        cover_color=parsed_memo.get("cover_color", ""),
        # 표지 AI 사진의 장면 전용("표지 장면:"). 표지 alt(위 cover_alt_suggestion)와 별개라 alt에는 안 들어갑니다.
        cover_scene=parsed_memo.get("cover_scene") or None,
        # 본문 사진 구도 배정 시드(글 id): 같은 글은 다시 만들어도 같은 배정, 글마다는 다름.
        angle_seed=article_id,
    )
    article["covers"] = {**article["covers"], **result["covers"]}
    article["cover_alts"] = {**article["cover_alts"], **result["cover_alts"]}
    article["cover_backgrounds"] = {**article["cover_backgrounds"], **result["cover_backgrounds"]}
    article["cover_title_fallback"] = {**article["cover_title_fallback"], **result["cover_title_fallback"]}

    if is_shopping:
        # 숍 채널은 본문 이미지를 platform_body_images에 배정하지 않습니다(이미지 풀 전용).
        set_article(article_id, article)
        return redirect(url_for("edit_article", article_id=article_id))

    article["body_images"] = result["body_images"]
    article["body_image_alts"] = result["body_alts"]

    # 플랫폼당 images_per_platform장씩 배정합니다 (alt 텍스트도 같은 순서로 같이 자릅니다).
    images = result["body_images"]
    alts = result["body_alts"]
    platform_body_images = {}
    platform_body_image_alts = {}
    platform_body_image_headings = {}
    headings_per_image = result.get("body_alt_headings") or []
    for i, p in enumerate(active_platforms):
        start = i * images_per_platform
        end = start + images_per_platform
        platform_body_images[p] = images[start:end]
        platform_body_image_alts[p] = alts[start:end]
        platform_body_image_headings[p] = headings_per_image[start:end]
    article["platform_body_images"] = {**article["platform_body_images"], **platform_body_images}
    article["platform_body_image_alts"] = {**article.get("platform_body_image_alts", {}), **platform_body_image_alts}
    if is_longterm or is_info_channel:
        # 롱텀·네이버·싼세계: 사진별 소제목을 저장합니다(최종본에서 각 사진을 그 소제목 섹션에 붙이는 데 씀).
        article["platform_body_image_headings"] = {
            **article.get("platform_body_image_headings", {}), **platform_body_image_headings
        }
    # (radar169_nv75off_20261027) 네이버용 75% 여백 파일(_nv75)은 더 이상 만들지 않습니다.
    # 되돌리려면: NV75_CHANNELS 채널에서 _make_nv75_safe(path)로 platform_body_images_nv75를 채우던 이전 블록을 백업에서 복원하세요.
    # 기존 글의 platform_body_images_nv75 기록은 그대로 두고 읽지 않습니다.

    set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id))


def _apply_image_output_context(article_id: str) -> str:
    """
    이 글의 채널+제목에 맞는 하위 폴더("static/generated/채널명/글제목")로 이미지 저장
    위치를 맞추고, 그 경로를 돌려줍니다. 이후 이 요청 안에서 image_gen.py의 생성/저장
    함수를 부르면 전부 이 폴더에 저장됩니다.
    """
    article = get_article(article_id)
    _cfg = get_channel_config()
    channel_folder = _cfg.get("image_folder") or (_cfg.get("short_label") or "기타").replace(" ", "")
    title = next((t for t in article["platform_title"].values() if t), None)
    if not title:
        queue = load_queue()
        item = find_queue_item(queue, article_id)
        title = item["keyword"] if item else article_id
    slug = image_gen.slugify_for_folder(title)
    output_dir = os.path.join("static", "generated", channel_folder, slug)
    image_gen.set_output_context(output_dir)
    return output_dir


@app.route("/article/<article_id>/recover-images/<platform>", methods=["POST"])
def recover_images(article_id, platform):
    """
    (예전 붙여넣기 버그로) 표지/본문 이미지 참조가 지워졌지만, 실제 이미지 파일 자체는
    static/generated 폴더에 그대로 남아있는 경우 파일을 다시 찾아서 연결만 복구합니다.
    API를 다시 부르지 않으므로 비용이 들지 않습니다. 채널별 폴더 구조가 생기기 전
    (평평한 구조)에 만들어진 옛 글도 여전히 찾을 수 있도록, 새 폴더와 옛 평평한 경로를
    둘 다 확인합니다.
    """
    if platform not in PLATFORMS:
        return redirect(url_for("edit_article", article_id=article_id))

    article = get_article(article_id)
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    keyword = item["keyword"] if item else ""
    recovered = []

    new_dir = _apply_image_output_context(article_id)
    candidate_dirs = [new_dir]  # 새 폴더 먼저
    legacy = LEGACY_IMAGE_FOLDERS.get(get_active_channel_id())
    if legacy:  # 채널 이름을 바꾸기 전(옛 폴더 이름)에 저장된 이미지도 찾도록
        candidate_dirs.append(os.path.join("static", "generated", legacy, os.path.basename(new_dir)))
    candidate_dirs.append(image_gen.OUTPUT_DIR)  # 못 찾으면 옛 평평한 구조도 확인

    def _find_existing(filename: str) -> str | None:
        """새 이미지는 .jpg, 옛 이미지는 .png — 확장자를 뺀 이름을 받아 .jpg를 먼저, 없으면 .png를 찾습니다."""
        for ext in (".jpg", ".png"):
            for d in candidate_dirs:
                candidate = os.path.join(d, filename + ext)
                if os.path.exists(candidate):
                    return candidate
        return None

    cover_path = _find_existing(f"{article_id}_{platform}_cover_final")
    if cover_path and not article["covers"].get(platform):
        article["covers"][platform] = "/" + cover_path.replace(os.sep, "/")
        if not article["cover_alts"].get(platform):
            article["cover_alts"][platform] = f"{keyword} 표지 이미지"
        recovered.append("표지")

    if not article["platform_body_images"].get(platform):
        found_body = []
        i = 0
        while True:
            body_path = _find_existing(f"{article_id}_body_{i}")
            if not body_path:
                break
            found_body.append("/" + body_path.replace(os.sep, "/"))
            i += 1
        if found_body:
            article["platform_body_images"][platform] = found_body
            if not article["platform_body_image_alts"].get(platform):
                article["platform_body_image_alts"][platform] = [f"{keyword} 관련 이미지"] * len(found_body)
            recovered.append(f"본문 이미지 {len(found_body)}장")

    if recovered:
        set_article(article_id, article)

    return redirect(url_for("edit_article", article_id=article_id))


@app.route("/article/<article_id>/cover-style", methods=["POST"])
def save_lt_cover_style(article_id):
    """롱텀만: 이 글이 속한 묶음의 표지 디자인(A/B/C)·색을 longterm_cover_style.json에 저장합니다.
    이미 만든 표지는 그대로이고, "표지 다시 만들기"를 눌러야 반영됩니다."""
    if get_active_channel_id() == "longterm_wp":
        item = find_queue_item(load_queue(), article_id)
        if item and image_gen.LT_COVER_UNIFIED:  # 새 통일 표지: 이 묶음의 글자 색만 저장
            image_gen.set_lt_accent(_lt_style_key(item), request.form.get("accent", ""))
        elif item:
            image_gen.set_lt_cover_style(
                _lt_style_key(item), request.form.get("template", ""), request.form.get("color", "")
            )
    return redirect(url_for("edit_article", article_id=article_id))


@app.route("/article/<article_id>/cover/redo/<platform>", methods=["POST"])
def redo_cover_style_a(article_id, platform):
    """
    🎨 표지 다시 만들기: AI 이미지를 새로 만들지 않고, 이미 생성해둔 표지 배경
    (article["cover_backgrounds"], generate_assets가 표지 만들 때 저장해둔 원본)에 방금
    고친 메타데이터 메모의 표지 문구(배지/1줄/2줄/보조)만 새로 얹습니다. 새 파일로 만들고,
    기존 표지 자리를 이 새 파일로 교체합니다 — 비용 없음.
    """
    if platform not in PLATFORMS:
        return redirect(url_for("edit_article", article_id=article_id))

    article = get_article(article_id)
    bg_path = article["cover_backgrounds"].get(platform)

    if get_active_channel_id() == "longterm_wp" and image_gen.COVER_STYLE != "legacy":
        # 롱텀 새 표지: A·B는 배경 없이 바로 다시 그리고, C인데 저장된 사진이 없으면 A로 대신 만듭니다.
        _apply_image_output_context(article_id)
        item = find_queue_item(load_queue(), article_id)
        if image_gen.LT_COVER_UNIFIED:  # 새 표지: 묶음별 배정을 만들지도 기록하지도 않음
            style = {"template": "U", "color": ""}
        else:
            style = image_gen.get_or_assign_lt_cover_style(_lt_style_key(item, article["platform_title"].get(platform) or ""))
        template = style["template"]
        has_bg = bool(bg_path) and os.path.exists(bg_path)
        if template == "C" and not has_bg:
            template = "A"
            session["lt_cover_msg"] = "사진이 없어 A로 만들었어요"
        memo_text = next((v for v in article["platform_metadata"].values() if v), "")
        parsed_memo = metadata_parser.parse_metadata_memo(memo_text)
        title = article["platform_title"].get(platform) or ""
        badge, line1, line2, sub, used_fallback = image_gen.resolve_cover_text(
            parsed_memo["cover_badge"], parsed_memo["cover_line1"], parsed_memo["cover_line2"],
            parsed_memo["cover_sub"], title,
        )
        article["covers"][platform] = image_gen.compose_longterm_cover(
            template, style["color"], badge, line1, line2, sub,
            output_id=f"{article_id}_{platform}_{uuid.uuid4().hex[:6]}",
            category=parsed_memo.get("category") or (item or {}).get("category") or "",
            bg_path=bg_path if has_bg else "",
            cluster_key=_lt_style_key(item, article["platform_title"].get(platform) or ""),
        )
        article["cover_title_fallback"][platform] = used_fallback
        if not article["cover_alts"].get(platform):
            article["cover_alts"][platform] = f"{title or article_id} 표지 이미지"
        set_article(article_id, article)
        return redirect(url_for("edit_article", article_id=article_id))

    if not bg_path or not os.path.exists(bg_path):
        # 배경 원본을 못 찾으면(예전 글이라 안 남아있는 경우 등) 아무것도 안 하고 돌아갑니다.
        return redirect(url_for("edit_article", article_id=article_id))

    _apply_image_output_context(article_id)
    memo_text = next((v for v in article["platform_metadata"].values() if v), "")
    parsed_memo = metadata_parser.parse_metadata_memo(memo_text)
    title = article["platform_title"].get(platform) or ""

    badge, line1, line2, sub, used_fallback = image_gen.resolve_cover_text(
        parsed_memo["cover_badge"], parsed_memo["cover_line1"], parsed_memo["cover_line2"],
        parsed_memo["cover_sub"], title,
    )
    new_cover_path = image_gen.compose_cover_style_a(
        bg_path, badge, line1, line2, sub, get_active_channel_id(),
        output_id=f"{article_id}_{platform}_{uuid.uuid4().hex[:6]}",
        cover_color=parsed_memo.get("cover_color", ""),
    )
    article["covers"][platform] = new_cover_path
    article["cover_title_fallback"][platform] = used_fallback
    if not article["cover_alts"].get(platform):
        article["cover_alts"][platform] = f"{title or article_id} 표지 이미지"
    set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id))


# ---------- 숍(쿠팡파트너스·쇼핑커넥트) 이미지 풀: 업로드/생성 후보를 모아두고 "사용하기"로 선택 ----------

def _pool_add(article: dict, group: str, slot: int | None, path: str, source: str, alt: str = "") -> dict:
    """이미지 풀에 후보 하나를 추가합니다. group: 'cover', 'body'(워드프레스 SHOP 본문) 또는 'products'. slot: products일 때만 필요."""
    entry = {"id": uuid.uuid4().hex[:8], "path": path, "alt": alt, "source": source, "selected": False}
    if group == "cover":
        article["shop_image_pool"]["cover"].append(entry)
    elif group == "body":
        article["shop_image_pool"]["body"].append(entry)
    else:
        article["shop_image_pool"]["products"].setdefault(str(slot), []).append(entry)
    return entry


def _save_uploaded_image(article_id: str, file, tag: str) -> str:
    ext = os.path.splitext(file.filename)[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    filename = f"{article_id}_{tag}_{uuid.uuid4().hex[:6]}{ext}"
    filepath = os.path.join(image_gen._current_output_dir(), filename)
    file.save(filepath)
    return "/" + filepath.replace(os.sep, "/")


@app.route("/article/<article_id>/shop-pool/cover/upload", methods=["POST"])
def shop_pool_cover_upload(article_id):
    """업체가 준 실제 표지 사진을 그대로 풀에 추가합니다 (생성 없이 원본 그대로)."""
    _apply_image_output_context(article_id)
    file = request.files.get("image")
    if file and file.filename:
        article = get_article(article_id)
        path = _save_uploaded_image(article_id, file, "cover_upload")
        _pool_add(article, "cover", None, path, source="upload", alt="업로드한 표지 이미지")
        set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")


@app.route("/article/<article_id>/shop-pool/cover/compose", methods=["POST"])
def shop_pool_cover_compose(article_id):
    """
    업체가 준 실제 사진 위에 글 제목을 합성한 표지를 만들어 풀에 추가합니다 (AI 호출 없음, 순수 PIL).
    사진은 새로 업로드하거나(image), 풀에 이미 있는 업로드 사진(source_image_id)을 고를 수 있습니다.
    """
    _apply_image_output_context(article_id)
    article = get_article(article_id)
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    title = next(
        (article["platform_title"].get(p) for p in get_active_platforms() if article["platform_title"].get(p)), ""
    ) or (item["keyword"] if item else "")

    photo_path = None
    file = request.files.get("image")
    if file and file.filename:
        photo_path = _save_uploaded_image(article_id, file, "cover_photo").lstrip("/")
    else:
        source_id = request.form.get("source_image_id", "")
        src = next(
            (e for e in article["shop_image_pool"]["cover"]
             if e["id"] == source_id and e.get("source") in ("upload", "ai_bg")),
            None,
        )
        if src:
            photo_path = src["path"].lstrip("/")

    # "🎨 표지 다시 만들기"(파일도 안 올리고 특정 사진도 안 고른 경우): 지금 사용 중인 업로드
    # 사진, 없으면 가장 최근에 올린 업로드 사진을 자동으로 골라서 문구만 새로 얹습니다.
    if not photo_path:
        uploads = [e for e in article["shop_image_pool"]["cover"] if e.get("source") == "upload"]
        auto = next((e for e in uploads if e.get("selected")), None) or (uploads[-1] if uploads else None)
        if auto:
            photo_path = auto["path"].lstrip("/")

    if photo_path and title:
        if image_gen.COVER_STYLE == "legacy":
            cover_path = image_gen.compose_cover_from_photo(
                photo_path, title, output_id=f"{article_id}_{uuid.uuid4().hex[:6]}"
            )
        else:
            memo_text = next((v for v in article["platform_metadata"].values() if v), "")
            parsed_memo = metadata_parser.parse_metadata_memo(memo_text)
            badge, line1, line2, sub, _ = image_gen.resolve_cover_text(
                parsed_memo["cover_badge"], parsed_memo["cover_line1"], parsed_memo["cover_line2"],
                parsed_memo["cover_sub"], title,
            )
            cover_path = image_gen.compose_cover_from_photo_style_a(
                photo_path, badge, line1, line2, sub, get_active_channel_id(),
                output_id=f"{article_id}_{uuid.uuid4().hex[:6]}",
                cover_color=parsed_memo.get("cover_color", ""),
            )
        _pool_add(article, "cover", None, cover_path, source="composed", alt=f"{title} 표지 이미지")
        set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")


@app.route("/article/<article_id>/shop-pool/cover/ai-generate", methods=["POST"])
def shop_pool_cover_ai_generate(article_id):
    """
    네이버 SHOP·워드프레스 SHOP 전용: 사용자가 직접 쓴 프롬프트로 AI 배경을 만들고, 그 위에 시안 A 문구를 얹은
    표지까지 풀에 추가합니다 (배경은 source="ai_bg", 문구 합성본은 source="ai_cover").
    자동 "사용하기" 선택은 하지 않습니다.
    """
    back = redirect(url_for("edit_article", article_id=article_id) + "#shop-images")
    if get_active_channel_id() not in ("shop_naver", "shop_wp"):
        return back
    user_prompt = (request.form.get("prompt") or "").strip()
    if not user_prompt:
        session["shop_ai_cover_msg"] = "프롬프트를 입력해 주세요. 비어 있으면 AI 표지를 만들지 않습니다."
        return back

    _apply_image_output_context(article_id)
    article = get_article(article_id)
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    title = next(
        (article["platform_title"].get(p) for p in get_active_platforms() if article["platform_title"].get(p)), ""
    ) or (item["keyword"] if item else "")
    keyword = item["keyword"] if item else title
    try:
        count = max(1, min(3, int(request.form.get("count", 1))))
    except ValueError:
        count = 1

    from_upload = bool(request.form.get("use_upload_ref"))
    no_product = bool(request.form.get("no_product"))
    reference_path = None
    if from_upload:
        product0_pool = article["shop_image_pool"]["products"].get("0", [])
        uploaded_ref = next((e for e in product0_pool if e.get("source") == "upload"), None)
        if uploaded_ref:
            reference_path = uploaded_ref["path"].lstrip("/")
    if reference_path:
        no_product = False  # 사진 기준이면 "제품 모양 그리지 않기"는 무시

    article["shop_cover_ai_prompt"] = user_prompt

    results = image_gen.generate_cover_from_user_prompt(
        user_prompt, keyword, f"{article_id}_{uuid.uuid4().hex[:4]}", count, no_product, reference_path
    )

    memo_text = next((v for v in article["platform_metadata"].values() if v), "")
    parsed_memo = metadata_parser.parse_metadata_memo(memo_text)
    badge, line1, line2, sub, _ = image_gen.resolve_cover_text(
        parsed_memo["cover_badge"], parsed_memo["cover_line1"], parsed_memo["cover_line2"],
        parsed_memo["cover_sub"], title,
    )
    for bg_path, final_prompt in results:
        bg_web = "/" + bg_path.replace(os.sep, "/")
        bg_entry = _pool_add(article, "cover", None, bg_web, source="ai_bg", alt=f"{keyword} 관련 상황 이미지")
        bg_entry["prompt"] = final_prompt
        cover_web = image_gen.compose_cover_style_a(
            bg_path, badge, line1, line2, sub, get_active_channel_id(),
            output_id=f"{article_id}_{uuid.uuid4().hex[:6]}",
            cover_color=parsed_memo.get("cover_color", ""),
        )
        cover_entry = _pool_add(article, "cover", None, cover_web, source="ai_cover", alt=f"{title} 표지 이미지")
        cover_entry["prompt"] = final_prompt
    set_article(article_id, article)
    return back


def _clean_alt_from_filename(filename: str, fallback: str = "") -> str:
    """'LG_그램_16_프로.jpg' → 'LG 그램 16 프로' (확장자 제거, _·- → 공백, 연속 공백 정리)."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", stem)).strip() or fallback


def _save_body_image_as_jpg(article_id: str, file, n: int) -> str:
    """
    어떤 포맷(png/webp/avif 등)으로 올려도 JPG로 저장합니다. 폰 사진 회전 보정 + RGB 변환을 먼저 하고,
    리사이즈·압축은 워드프레스 발행이 쓰는 wp_publisher._compress_for_upload(품질 82, 폭 1600)를 그대로 재사용합니다.
    """
    img = ImageOps.exif_transpose(Image.open(file.stream)).convert("RGB")
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        img.save(tmp_path)
        data, _, mime_type = wp_publisher._compress_for_upload(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    if mime_type != "image/jpeg":
        raise ValueError("JPG 변환에 실패했습니다")
    filepath = os.path.join(image_gen._current_output_dir(), f"{article_id}_body{n}_{uuid.uuid4().hex[:6]}.jpg")
    with open(filepath, "wb") as f:
        f.write(data)
    return "/" + filepath.replace(os.sep, "/")


@app.route("/article/<article_id>/shop-pool/body/upload", methods=["POST"])
def shop_pool_body_upload(article_id):
    """워드프레스 SHOP 본문 이미지: 여러 장 업로드 → 전부 JPG로 변환, alt는 원본 파일명에서 자동 생성.
    글 맨 아래에 업로드 순서 그대로 들어갑니다."""
    _apply_image_output_context(article_id)
    article = get_article(article_id)
    base_n = len(article["shop_image_pool"]["body"])
    files = [f for f in request.files.getlist("images") if f and f.filename]
    for i, file in enumerate(files):
        path = _save_body_image_as_jpg(article_id, file, base_n + i + 1)
        _pool_add(article, "body", None, path, source="upload", alt=_clean_alt_from_filename(file.filename, "이미지"))
    set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")


@app.route("/article/<article_id>/shop-pool/product/<int:slot>/upload", methods=["POST"])
def shop_pool_product_upload(article_id, slot):
    """업체가 준 실제 제품 사진을 그대로 풀에 추가합니다 (생성 없이 원본 그대로 사용 가능)."""
    _apply_image_output_context(article_id)
    file = request.files.get("image")
    if file and file.filename:
        article = get_article(article_id)
        path = _save_uploaded_image(article_id, file, f"product{slot}_upload")
        _pool_add(article, "products", slot, path, source="upload", alt="업로드한 제품 이미지")
        set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")


@app.route("/article/<article_id>/shop-pool/product/<int:slot>/generate", methods=["POST"])
def shop_pool_product_generate(article_id, slot):
    """
    레퍼런스 이미지를 업로드하면서 동시에 바리에이션을 생성합니다. 업로드한 원본도
    풀에 같이 들어가서, 바리에이션이 마음에 안 들면 원본을 그대로 쓸 수도 있습니다.
    """
    if get_active_channel_id() in ("shop_naver", "shop_wp"):
        return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")
    _apply_image_output_context(article_id)
    file = request.files.get("ref_image")
    if not file or not file.filename:
        return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")

    article = get_article(article_id)
    ref_path = _save_uploaded_image(article_id, file, f"product{slot}_ref")
    product_name = request.form.get("product_name", "").strip() or f"상품{slot + 1}"
    _pool_add(article, "products", slot, ref_path, source="upload", alt=f"{product_name} 원본 사진")

    try:
        count = max(1, min(5, int(request.form.get("count", 5))))
    except ValueError:
        count = 5

    variations = image_gen.generate_product_variations(
        reference_image_path=ref_path.lstrip("/"),
        product_name=product_name,
        article_id=article_id,
        slot=slot,
        count=count,
    )
    for v_path in variations:
        _pool_add(article, "products", slot, v_path, source="generated", alt=f"{product_name} 이미지")

    set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")


def _shop_product_names(article: dict) -> list[str]:
    """원고(붙여넣은 글)의 상품 섹션 이름 목록. 원고가 없거나 상품이 0개면 빈 목록."""
    raw = next((v for v in article["platform_raw"].values() if v), "")
    if not raw:
        return []
    try:
        sections = shop_formatter.parse_sections(raw)["sections"]
    except Exception:
        return []
    return [shop_formatter.strip_markup(s["heading"]) for s in sections if s["is_product"]]


def _shop_product_slots(article: dict) -> list[dict]:
    """②-1 상품 칸 목록. 상품 수만큼(없으면 6칸) + 그보다 뒤 칸에 이미 사진이 있으면 그 칸도(extra=True)."""
    names = _shop_product_names(article)
    n = len(names) or 6
    slots = [{"slot": i, "name": names[i] if i < len(names) else "", "extra": False} for i in range(n)]
    for key in sorted((k for k in article["shop_image_pool"]["products"] if k.isdigit()), key=int):
        if int(key) >= n and article["shop_image_pool"]["products"][key]:
            slots.append({"slot": int(key), "name": "", "extra": True})
    return slots


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


_NUM_PREFIX_RE = re.compile(r"^\s*(\d+)(?:[\s_\-.]|$)")  # 숫자 뒤에 구분 기호(_ - . 공백)가 있어야 번호로 인정
_NUM_STRIP_RE = re.compile(r"^\s*\d+[\s_\-.]*")


@app.route("/article/<article_id>/shop-pool/products/bulk-upload", methods=["POST"])
def shop_pool_products_bulk_upload(article_id):
    """
    네이버 SHOP 전용: 상품 사진을 여러 장 한 번에 올려 상품 칸에 자동 배정합니다 (전부 "사용함"으로).
    ① 파일 이름이 숫자로 시작하면 그 번호의 상품 칸(1 → slot "0"), 범위 밖이면 ②로.
    ② 번호가 없으면 파일 이름순으로 사진이 없는 칸부터 차례로, 빈 칸이 없으면 앞에서부터 다시 돌며 추가.
    """
    back = redirect(url_for("edit_article", article_id=article_id) + "#shop-images")
    if get_active_channel_id() != "shop_naver":
        return back
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        session["shop_bulk_msg"] = "올릴 사진을 선택해 주세요."
        return back

    _apply_image_output_context(article_id)
    article = get_article(article_id)
    n = len(_shop_product_names(article)) or 6
    products = article["shop_image_pool"]["products"]

    numbered, unnumbered = [], []
    for f in files:
        m = _NUM_PREFIX_RE.match(f.filename)
        if m and 1 <= int(m.group(1)) <= n:
            numbered.append((int(m.group(1)) - 1, f))
        else:
            unnumbered.append(f)
    unnumbered.sort(key=lambda f: _natural_key(f.filename))
    numbered.sort(key=lambda sf: (sf[0], _natural_key(sf[1].filename)))
    numbered_ids = {id(f) for _, f in numbered}

    assignments = list(numbered)
    filled = {s for s, _ in numbered} | {i for i in range(n) if products.get(str(i))}
    empty_slots = [i for i in range(n) if i not in filled]
    cycle = 0
    for f in unnumbered:
        if empty_slots:
            slot = empty_slots.pop(0)
        else:
            slot = cycle % n
            cycle += 1
        assignments.append((slot, f))

    counts: dict[int, int] = {}
    for slot, f in assignments:
        path = _save_uploaded_image(article_id, f, f"product{slot}_upload")
        stem, ext = os.path.splitext(os.path.basename(f.filename))
        if id(f) in numbered_ids:
            stem = _NUM_STRIP_RE.sub("", stem, count=1)  # 번호로 배정된 파일만 앞 번호를 뗌
        alt = _clean_alt_from_filename(stem + ext, f"상품{slot + 1} 이미지") if stem.strip() else f"상품{slot + 1} 이미지"
        entry = _pool_add(article, "products", slot, path, source="upload", alt=alt)
        entry["selected"] = True
        counts[slot] = counts.get(slot, 0) + 1
    set_article(article_id, article)
    detail = ", ".join(f"상품{s + 1} {c}장" for s, c in sorted(counts.items()))
    session["shop_bulk_msg"] = f"{len(assignments)}장 업로드: {detail}"
    return back


@app.route("/article/<article_id>/shop-pool/products/move", methods=["POST"])
def shop_pool_products_move(article_id):
    """네이버 SHOP 전용: 상품 사진 한 장을 다른 상품 칸으로 옮깁니다 (사용함 상태 유지)."""
    back = redirect(url_for("edit_article", article_id=article_id) + "#shop-images")
    if get_active_channel_id() != "shop_naver":
        return back
    image_id = request.form.get("image_id", "")
    from_slot = request.form.get("from_slot", "")
    to_slot = request.form.get("to_slot", "")
    if not (from_slot.isdigit() and to_slot.isdigit()):
        return back
    from_slot, to_slot = str(int(from_slot)), str(int(to_slot))
    if from_slot == to_slot:
        return back
    article = get_article(article_id)
    n = len(_shop_product_names(article)) or 6
    if int(to_slot) >= n:
        return back
    products = article["shop_image_pool"]["products"]
    entry = next((e for e in products.get(from_slot, []) if e["id"] == image_id), None)
    if entry:
        products[from_slot] = [e for e in products[from_slot] if e["id"] != image_id]
        products.setdefault(to_slot, []).append(entry)
        set_article(article_id, article)
    return back


@app.route("/article/<article_id>/shop-pool/toggle", methods=["POST"])
def shop_pool_toggle(article_id):
    """풀 안의 이미지 하나를 "사용하기" 켜고 끕니다. 여러 장을 동시에 선택할 수 있습니다."""
    group = request.form.get("group", "")
    slot = request.form.get("slot", "")
    image_id = request.form.get("image_id", "")

    article = get_article(article_id)
    pool = article["shop_image_pool"]["cover"] if group == "cover" else article["shop_image_pool"]["products"].get(slot, [])
    for entry in pool:
        if entry["id"] == image_id:
            entry["selected"] = not entry["selected"]
            break
    set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")


@app.route("/article/<article_id>/shop-pool/delete", methods=["POST"])
def shop_pool_delete(article_id):
    """풀에서 이미지 후보 하나를 뺍니다 (마음에 안 드는 생성 결과 정리용)."""
    group = request.form.get("group", "")
    slot = request.form.get("slot", "")
    image_id = request.form.get("image_id", "")

    article = get_article(article_id)
    if group == "cover":
        article["shop_image_pool"]["cover"] = [e for e in article["shop_image_pool"]["cover"] if e["id"] != image_id]
    elif group == "body":
        article["shop_image_pool"]["body"] = [e for e in article["shop_image_pool"]["body"] if e["id"] != image_id]
    else:
        products = article["shop_image_pool"]["products"]
        if slot in products:
            products[slot] = [e for e in products[slot] if e["id"] != image_id]
    set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id) + "#shop-images")


def _match_images_to_h2(segments: list[str], image_headings: list[str]) -> dict[int, int]:
    """롱텀: 사진(순서 번호) → 붙일 </h2> 자리(번호) 매칭. 소제목이 적힌 사진만 대상이고, 한 소제목엔 한 장만 붙습니다.
    비교는 HTML 태그·**·이모지·문장부호·공백을 무시하고, 정확히 같은 소제목이 없으면 한쪽이 다른 쪽을 포함하는 소제목을 찾습니다.
    못 찾은 사진은 결과에 없습니다(호출하는 쪽이 고르게 배치)."""
    h2_texts = []
    for seg in segments[:-1]:
        m = re.search(r"<h2[^>]*>(.*)$", seg, flags=re.DOTALL)
        h2_texts.append(image_gen.normalize_heading(m.group(1)) if m else "")
    taken: set[int] = set()
    matched: dict[int, int] = {}
    # 정확히 같은 소제목을 먼저 전부 배정하고, 남은 사진만 포함 관계로 찾습니다(포함 비교가 정확 일치 자리를 빼앗지 않도록).
    for pass_exact in (True, False):
        for img_idx, heading in enumerate(image_headings):
            key = image_gen.normalize_heading(heading)
            if img_idx in matched or not key:
                continue
            for a_idx, text in enumerate(h2_texts):
                if a_idx in taken or not text:
                    continue
                if (text == key) if pass_exact else (key in text or text in key):
                    matched[img_idx] = a_idx
                    taken.add(a_idx)
                    break
    return matched


_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>")
_HTML_VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "wbr"}


def _html_top_blocks(content: str) -> list[tuple[int, int, str]]:
    """HTML 조각 안의 최상위 블록(<p>·<ul>·<table>·<h3> …)의 (시작, 끝, 태그 이름) 목록. 안쪽에 든 태그(강조·링크·목록 항목 등)는 따로 세지 않습니다."""
    spans, depth, start = [], 0, 0
    for m in _HTML_TAG_RE.finditer(content):
        tag = m.group(2).lower()
        if tag in _HTML_VOID_TAGS or m.group(3):
            if depth == 0:
                spans.append((m.start(), m.end(), tag))
            continue
        if not m.group(1):
            if depth == 0:
                start = m.start()
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                spans.append((start, m.end(), tag))
    return spans


def _place_images_in_h2_sections(segments: list[str], img_tag, n_images: int, image_headings: list[str] | None, seed: str) -> str | None:
    """</h2>로 나눈 조각(segments)의 섹션마다 본문 사진을 ①소제목 바로 아래 ②첫 블록 뒤 ③섹션 끝 중 한 자리에 넣어 다시 이어 붙입니다.
    섹션 고르기·자리 배정은 shop_formatter.assign_body_image_slots(소제목 매칭 → 남는 사진은 글 id 시드로 빈 섹션에 퍼뜨림 → 자리 섞기).
    FAQ·"세부 글"·본문이 빈 섹션에는 넣지 않습니다. 넣을 섹션이 하나도 없으면 None(호출하는 쪽이 예전 방식으로)."""
    total = len(segments) - 1
    heads, contents, tails = [], [], []
    for k in range(total):
        m = re.search(r"<h2[^>]*>(.*)$", segments[k], flags=re.DOTALL)
        heads.append(image_gen.normalize_heading(m.group(1)) if m else "")
        seg = segments[k + 1]
        cut = seg.rfind("<h2") if k + 1 < total else -1
        contents.append(seg[:cut] if cut >= 0 else seg)
        tails.append(seg[cut:] if cut >= 0 else "")
    spans = [_html_top_blocks(c) for c in contents]
    eligible = [
        bool(spans[k]) and not any(w in heads[k] for w in ("faq", "자주묻는", "세부글")) for k in range(total)
    ]
    units = []
    for sp in spans:
        lead = 0
        for s in sp:
            lead += 1
            if s[2] != "h3":
                break
        units.append((max(1, lead), len(sp)))
    heads_for_images = [image_gen.normalize_heading(h) for h in (image_headings or [""] * n_images)]
    plan = shop_formatter.assign_body_image_slots(heads, eligible, units, heads_for_images, seed)
    if not plan:
        return None
    out = segments[0]
    for k in range(total):
        content = contents[k]
        inserts = []
        for slot, idxs in plan.get(k, {}).items():
            pos = 0 if slot == "top" else (spans[k][units[k][0] - 1][1] if slot == "after_first" else spans[k][-1][1])
            inserts.append((pos, "".join(img_tag(i) for i in idxs)))
        for pos, piece in sorted(inserts, key=lambda x: -x[0]):
            content = content[:pos] + piece + content[pos:]
        out += "</h2>" + content + tails[k]
    return out


def _insert_images_into_html(
    html: str, image_paths: list[str], image_alts: list[str] | None = None, image_headings: list[str] | None = None,
    seed: str = "",
) -> str:
    """image_headings(롱텀): 사진별로 붙일 소제목 목록(image_paths와 같은 순서, 모르는 사진은 ""). 소제목이 맞는 사진은 그 섹션에, 나머지는
    빈 섹션들에 퍼뜨립니다(한 섹션에 2장이 몰리지 않게). 섹션 안에서는 ①소제목 바로 아래 ②첫 블록 뒤 ③섹션 끝을 사진마다 다르게 배정합니다.
    seed(글 id)가 같으면 결과도 같습니다(photo_v2_20261020). <h2>가 없는 글은 예전 방식(</p> 뒤에 고르게) 그대로."""
    if not image_paths or not html:
        return html
    alts = image_alts or []
    while len(alts) < len(image_paths):
        alts.append("")

    def _img_tag(idx):
        return f'<img class="seo-body-img" src="{image_paths[idx]}" alt="{alts[idx]}">'

    anchor = "</h2>"
    if html.count(anchor) == 0:
        anchor = "</p>"
    segments = html.split(anchor)
    total_anchors = len(segments) - 1
    if total_anchors == 0:
        return html + "".join(_img_tag(i) for i in range(len(image_paths)))
    if anchor == "</h2>":
        heads_ok = image_headings if (image_headings and len(image_headings) == len(image_paths)) else None
        placed = _place_images_in_h2_sections(segments, _img_tag, len(image_paths), heads_ok, seed)
        if placed is not None:
            return placed
    # 정수 나눗셈(total_anchors // len(image_paths))은 나머지가 남으면 0이나 1로 뭉개져서,
    # 예를 들어 소제목 10개에 이미지 6장이면 step=1이 되어 앞쪽 6개 소제목에만 몰리고 뒤쪽
    # 4개엔 이미지가 하나도 안 붙는 문제가 있었습니다. 실수 간격으로 계산해서 전체 소제목
    # 구간에 고르게 퍼지도록 합니다.
    step = total_anchors / len(image_paths)
    insert_after = {min(total_anchors - 1, int(i * step)) for i in range(len(image_paths))}
    rebuilt = segments[0]
    img_idx = 0
    for i, seg in enumerate(segments[1:]):
        rebuilt += anchor
        if i in insert_after and img_idx < len(image_paths):
            rebuilt += _img_tag(img_idx)
            img_idx += 1
        rebuilt += seg
    return rebuilt


_NAVER_POST_URL_RE = re.compile(r"^https?://(?:m\.)?blog\.naver\.com/(?:[^/?#]+/\d+|[^#]*[?&]logNo=\d+)", re.IGNORECASE)


def _is_naver_post_url(url: str) -> bool:
    """네이버 블로그 "글 주소"인지 (blog.naver.com/아이디/글번호 또는 ?logNo=글번호). 블로그 첫 화면 주소는 False."""
    return bool(_NAVER_POST_URL_RE.match((url or "").strip()))


def _bad_naver_url_warning(article: dict) -> str:
    """발행 주소 칸에 글 번호 없는 네이버 주소(블로그 첫 화면 등)가 저장돼 있으면 그 주소를, 아니면 빈 문자열."""
    url = ((article.get("platform_published_url") or {}).get("naver") or "").strip()
    return url if url and not _is_naver_post_url(url) else ""


# ---------- 네이버 글 주소 자동 채우기 (예약 발행 글이 공개된 뒤 RSS에서 실제 글 주소를 찾아 채움) ----------
NAVER_RSS_CACHE_SECONDS = 600
_NAVER_RSS_CACHE = {"at": 0.0, "items": None}
_NAVER_RSS_LINK_RE = re.compile(r"^https?://(?:m\.)?blog\.naver\.com/([^/?#]+)/(\d+)", re.IGNORECASE)
NAVER_AUTOFILL_CHANNELS = ("naver_tips", "shop_naver")


def _fetch_naver_rss() -> list[dict] | None:
    """네이버 블로그 RSS의 [{title, link}] (link는 https://blog.naver.com/아이디/글번호 형태). 읽기 실패·형식 오류면 None. 10분 캐시."""
    cache = _NAVER_RSS_CACHE
    if cache["items"] is not None and time.time() - cache["at"] < NAVER_RSS_CACHE_SECONDS:
        return cache["items"]
    try:
        resp = requests.get(f"https://rss.blog.naver.com/{config.NAVER_BLOG_ID}.xml", timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for node in root.iter("item"):
            title = (node.findtext("title") or "").strip()
            m = _NAVER_RSS_LINK_RE.match((node.findtext("link") or "").strip())
            if title and m:
                items.append({"title": title, "link": f"https://blog.naver.com/{m.group(1)}/{m.group(2)}"})
        if not items:
            return None
    except (requests.RequestException, ET.ParseError, ValueError):
        return None
    cache["at"], cache["items"] = time.time(), items
    return items


def _norm_title_for_match(title: str) -> str:
    """제목 비교용: HTML 엔티티를 풀고, 공백·문장부호·따옴표를 모두 지운 소문자."""
    return re.sub(r"[\W_]+", "", html.unescape(title or "")).lower()


def _naver_autofill_plan(rss: list[dict], channel_ids=NAVER_AUTOFILL_CHANNELS, only_article_id: str | None = None, now: datetime | None = None) -> list[dict]:
    """
    채널별 데이터를 읽기만 해서 "무엇을 어떻게 채울지" 계획을 만듭니다 (저장은 안 함).
    대상: 네이버 발행 주소가 비었거나 글 주소가 아닌 글 중 발행(예약) 날짜가 오늘 이하인 글. 이미 글 번호가 있는 주소는 대상이 아닙니다.
    결과: 채움(제목이 RSS에서 정확히 1개 일치) / 못 찾음(없음·여러 개) / 예약 중(아직 공개 시각 전).
    """
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    by_norm: dict[str, list[dict]] = {}
    for r in rss:
        by_norm.setdefault(_norm_title_for_match(r["title"]), []).append(r)
    rows = []
    for channel_id in channel_ids:
        ch_queue, ch_articles = _read_channel_data(channel_id)
        seen = set()
        for it in ch_queue:
            aid = it.get("id")
            if aid in seen or (only_article_id and aid != only_article_id):
                continue
            seen.add(aid)
            art = ch_articles.get(aid) or {}
            url = ((art.get("platform_published_url") or {}).get("naver") or "").strip()
            date = ((art.get("platform_published_at") or {}).get("naver") or "").strip()
            if _is_naver_post_url(url) or not date:
                continue  # 이미 올바른 글 주소가 있거나, 발행(예약) 날짜가 없는 글은 대상이 아닙니다
            title = ((art.get("platform_title") or {}).get("naver") or "").strip()
            row = {"channel_id": channel_id, "channel": CHANNELS[channel_id]["short_label"], "id": aid, "keyword": it.get("keyword", ""),
                   "title": title, "date": date, "current": url, "found": "", "result": "", "ref": ""}
            dt = _stored_publish_dt(art, "naver")
            if date[:10] > today or (dt and dt > now):
                row["result"] = "예약 중"
                rows.append(row)
                continue
            hits = by_norm.get(_norm_title_for_match(title), []) if title else []
            if len(hits) == 1:
                row["found"], row["result"] = hits[0]["link"], "채움"
            else:
                row["result"] = "못 찾음"
                if hits:
                    row["ref"] = hits[0]["title"] + f" (같은 제목 {len(hits)}개)"
                elif title:
                    nt = _norm_title_for_match(title)
                    best = max(rss, key=lambda r: difflib.SequenceMatcher(None, nt, _norm_title_for_match(r["title"])).ratio())
                    row["ref"] = best["title"]
            rows.append(row)
    # 같은 RSS 글이 두 글에 겹쳐 배정되거나, 이미 다른 글이 쓰는 주소면 잘못 채울 위험이 있어 "못 찾음"으로 돌립니다.
    used = {}
    for channel_id in NAVER_AUTOFILL_CHANNELS:
        _, ch_articles = _read_channel_data(channel_id)
        for a in ch_articles.values():
            u = ((a.get("platform_published_url") or {}).get("naver") or "").strip()
            if _is_naver_post_url(u):
                used[u] = used.get(u, 0) + 1
    claimed: dict[str, int] = {}
    for r in rows:
        if r["result"] == "채움":
            claimed[r["found"]] = claimed.get(r["found"], 0) + 1
    for r in rows:
        if r["result"] == "채움" and (claimed[r["found"]] > 1 or r["found"] in used):
            r["ref"] = f"{r['found']} (이미 다른 글이 쓰는 주소라 채우지 않음)"
            r["found"], r["result"] = "", "못 찾음"
    return rows


def _apply_naver_autofill(rows: list[dict]) -> int:
    """계획의 "채움" 행만 저장합니다. 바꾸는 값은 platform_published_url.naver 하나뿐 (파일을 방금 다시 읽어 그 키만 고침). 반환: 채운 개수."""
    count = 0
    for channel_id in NAVER_AUTOFILL_CHANNELS:
        todo = [r for r in rows if r["channel_id"] == channel_id and r["result"] == "채움"]
        if not todo:
            continue
        path = CHANNELS[channel_id]["articles_file"]
        data = json.loads(_read_text_retry(path))
        for r in todo:
            art = data.get(r["id"])
            if not art:
                continue
            urls = art.get("platform_published_url")
            if not isinstance(urls, dict):
                urls = art["platform_published_url"] = {}
            if _is_naver_post_url(urls.get("naver") or ""):
                continue  # 그 사이 올바른 주소가 들어갔다면 덮어쓰지 않음
            urls["naver"] = r["found"]
            count += 1
        _write_json_atomic(path, data)
    return count


def _naver_autofill_response(only_article_id: str | None, back_url: str):
    rss = _fetch_naver_rss()
    if rss is None:
        return render_template("naver_url_autofill.html", rss_failed=True, rows=[], filled=0, back_url=back_url, only=bool(only_article_id))
    rows = _naver_autofill_plan(rss, only_article_id=only_article_id)
    filled = _apply_naver_autofill(rows)
    return render_template("naver_url_autofill.html", rss_failed=False, rows=rows, filled=filled, back_url=back_url, only=bool(only_article_id))


def _naver_autofill_before_final() -> str:
    """
    네이버 최종본을 만들기 직전, 공개된 글의 비어 있는 글 주소를 한 번 채웁니다 (예약 글이 하단 추천 후보에 들어오게).
    기존 _naver_autofill_plan/_apply_naver_autofill 그대로이고 RSS는 10분 캐시를 씁니다. 바뀌는 값은 platform_published_url.naver 하나뿐.
    RSS를 못 읽거나 중간에 오류가 나도 최종본 만들기는 계속 진행하도록, 화면에 보일 안내 문구만 돌려줍니다 ("" = 문제 없음).
    """
    try:
        rss = _fetch_naver_rss()
        if rss is None:
            return "네이버 RSS를 읽지 못해 글 주소 자동 채우기는 건너뛰었어요. 최종본은 그대로 만들었지만, 새로 공개된 글이 하단 추천에서 빠졌을 수 있어요."
        _apply_naver_autofill(_naver_autofill_plan(rss))
    except Exception as e:  # noqa: BLE001 — 부가 단계라 실패해도 최종본 만들기를 막지 않음
        return f"네이버 글 주소 자동 채우기 중 오류가 나 건너뛰었어요 ({type(e).__name__}). 최종본은 그대로 만들었어요."
    return ""


@app.route("/naver-url-autofill", methods=["POST"])
def naver_url_autofill_all():
    """"🔗 네이버 글 주소 자동 채우기" — 네이버 일반·네이버 SHOP 두 채널 전체 대상."""
    if get_active_channel_id() not in NAVER_AUTOFILL_CHANNELS:
        return redirect(url_for("index"))
    back = url_for("status") if request.form.get("back") == "status" else url_for("index")
    return _naver_autofill_response(None, back)


@app.route("/article/<article_id>/naver-url-autofill", methods=["POST"])
def naver_url_autofill_one(article_id):
    """최종본 화면의 같은 버튼 — 이 글만 대상 (지금 보고 있는 채널의 글)."""
    channel_id = get_active_channel_id()
    if channel_id not in NAVER_AUTOFILL_CHANNELS:
        return redirect(url_for("index"))
    rss = _fetch_naver_rss()
    back = f"/article/{article_id}/final/naver"
    if rss is None:
        return render_template("naver_url_autofill.html", rss_failed=True, rows=[], filled=0, back_url=back, only=True)
    rows = _naver_autofill_plan(rss, channel_ids=(channel_id,), only_article_id=article_id)
    filled = _apply_naver_autofill(rows)
    return render_template("naver_url_autofill.html", rss_failed=False, rows=rows, filled=filled, back_url=back, only=True)


# ---------- N데스크피커(radar_naver) 글 주소 자동 채우기 ----------
# naver_tips 와 따로 만든 복사본입니다(기존 함수·동작은 그대로). 블로그는 guardradar, 대상은 radar_naver 글만.
# 흐름: ① 버튼 → 미리보기(저장 안 함) → ② [확인하고 저장]을 눌렀을 때만 저장(저장 전 백업).
RADAR_AUTOFILL_CHANNEL = "radar_naver"
_RADAR_RSS_CACHE = {"at": 0.0, "items": None}


def _radar_unescape(t: str) -> str:
    """HTML 기호를 풀어 글자로(&amp;middot; 처럼 두 겹이어도 더 안 풀릴 때까지, 최대 3번). 앞뒤 빈칸 제거."""
    t = t or ""
    for _ in range(3):
        u = html.unescape(t)
        if u == t:
            break
        t = u
    return t.strip()


def _radar_norm_title(t: str) -> str:
    """제목 짝짓기용: HTML 기호를 풀고 띄어쓰기·문장부호(·, ?, ! 등)를 모두 뺀 뒤 소문자로. 글자·숫자만 남습니다."""
    return re.sub(r"[\W_]+", "", _radar_unescape(t)).lower()


def _fetch_radar_rss() -> list[dict] | None:
    """guardradar RSS의 [{title, link, raw}] — title 은 HTML 기호(&amp; &#39; &middot; 등)를 푼 글자, link 는 글 번호까지 있는 주소
    (글 번호가 없는 항목은 link="" 로 남기고 raw 에 원래 주소). 읽기 실패·형식 오류면 None, 올라온 글이 0개면 [].
    저장 전 확인용이라 캐시 없이 매번 새로 읽습니다."""
    try:
        resp = requests.get(f"https://rss.blog.naver.com/{radar_screen.RADAR_BLOG_ID}.xml", timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for node in root.iter("item"):
            title = _radar_unescape(node.findtext("title") or "")
            raw = (node.findtext("link") or "").strip()
            fixed, err = radar_screen.normalize_post_url(raw)
            if title:
                items.append({"title": title, "link": fixed if (fixed and not err) else "", "raw": raw})
    except (requests.RequestException, ET.ParseError, ValueError):
        return None
    return items


def _radar_url_is_fillable(url: str) -> bool:
    """채움 대상 주소인지: 비었거나, guardradar 블로그 첫 화면(글 번호 없음)뿐인 경우만 True.
    글 번호가 있는 주소·다른 블로그 주소는 이미 주소가 있는 것으로 보고 덮어쓰지 않습니다."""
    u = (url or "").strip()
    if not u:
        return True
    try:
        pu = urlparse(u)
        host = (pu.hostname or "").lower()
    except ValueError:
        return False
    return host in ("blog.naver.com", "m.blog.naver.com") and pu.path.rstrip("/").lower() == "/" + radar_screen.RADAR_BLOG_ID and not parse_qs(pu.query).get("logNo")


def _radar_autofill_plan(rss: list[dict], now: datetime | None = None, data: tuple | None = None) -> tuple[list[dict], int]:
    """radar_naver 글만 읽기만 해서 계획을 만듭니다(저장 안 함). 반환 (행 목록, 옛 생활 글이라 뺀 개수).
    대기열 id 가 RADAR_RELATED_SINCE(2026-11-23) 이전인 옛 생활 글은 대상에서 뺍니다. 제목은 HTML 기호를 풀고 띄어쓰기·문장부호·대소문자를 뺀 값이
    같고 글 번호 있는 RSS 글이 정확히 1개일 때만 '채움'.
    결과: 채움 / 못 찾음(없음·여러 개·다른 글과 겹침, 가장 비슷한 제목을 참고로 표시) / 예약 중(아직 공개 전, 오류 아님)."""
    now = now or datetime.now()
    queue, articles = data if data is not None else _read_channel_data(RADAR_AUTOFILL_CHANNEL)
    today = now.strftime("%Y-%m-%d")
    since = str(getattr(config, "RADAR_RELATED_SINCE", "2026-11-23"))
    by_title: dict[str, list[dict]] = {}
    for r in rss:
        if r.get("link"):
            by_title.setdefault(_radar_norm_title(r["title"]), []).append(r)
    rows, seen, old_skipped = [], set(), 0
    for it in queue:
        aid = it.get("id")
        if aid in seen:
            continue
        seen.add(aid)
        art = articles.get(aid) or {}
        url = ((art.get("platform_published_url") or {}).get("naver") or "").strip()
        # 발행 날짜: 앱에 '발행 완료'를 안 눌러도(네이버 예약발행) 대상이 되도록, 앱 날짜가 없으면 대기열 plan.publish_date 로 대신 봅니다.
        date = ((art.get("platform_published_at") or {}).get("naver") or "").strip() or str((it.get("plan") or {}).get("publish_date") or "").strip()
        if not _radar_url_is_fillable(url):
            continue  # 이미 글 주소가 있는 글은 대상이 아님
        if str(aid)[:10] < since:
            old_skipped += 1  # 옛 생활 글(Desk Picker 이전)은 목록에서 뺌
            continue
        if not date:
            continue  # 발행 예정일을 알 수 없는 글
        title = ((art.get("platform_title") or {}).get("naver") or "").strip()
        row = {"id": aid, "keyword": it.get("keyword", ""), "title": title, "date": date, "current": url, "found": "", "result": "", "ref": "", "rss_title": ""}
        dt = _publish_dt(date, ((art.get("platform_scheduled_time") or {}).get("naver")))
        hits0 = by_title.get(_radar_norm_title(title), []) if title else []
        if (date[:10] > today or (dt and dt > now)) and len(hits0) != 1:
            row["result"] = "예약 중"  # 아직 공개 전. 단, RSS 에 같은 제목 글이 이미 있으면(미리 발행한 글) 공개된 것이라 아래에서 짝짓기
        elif not art:
            row["result"], row["ref"] = "못 찾음", "앱에 원고가 없는 글(쓰지 않는 글)"
        else:
            hits = by_title.get(_radar_norm_title(title), []) if title else []
            if len(hits) == 1:
                row["found"], row["result"], row["rss_title"] = hits[0]["link"], "채움", hits[0]["title"]
            else:
                row["result"] = "못 찾음"
                if hits:
                    row["ref"] = hits[0]["title"] + f" (같은 제목 {len(hits)}개)"
                elif title and rss:
                    nt = _radar_norm_title(title)
                    row["ref"] = max(rss, key=lambda r: difflib.SequenceMatcher(None, nt, _radar_norm_title(r["title"])).ratio())["title"]
        rows.append(row)
    # 이미 다른 글이 쓰는 주소이거나 두 글이 같은 주소를 받으려 하면 채우지 않습니다.
    used = {}
    for a in articles.values():
        u = ((a.get("platform_published_url") or {}).get("naver") or "").strip()
        if u and not _radar_url_is_fillable(u):
            used[u] = used.get(u, 0) + 1
    claimed: dict[str, int] = {}
    for r in rows:
        if r["result"] == "채움":
            claimed[r["found"]] = claimed.get(r["found"], 0) + 1
    for r in rows:
        if r["result"] == "채움" and (claimed[r["found"]] > 1 or r["found"] in used):
            r["ref"] = f"{r['found']} (이미 다른 글이 쓰는 주소라 채우지 않음)"
            r["found"], r["result"], r["rss_title"] = "", "못 찾음", ""
    return rows, old_skipped


def _radar_apply_autofill(rows: list[dict], path: str | None = None) -> tuple[int, str]:
    """'채움' 행만 저장합니다. 저장 전에 articles_radar_naver 를 '.backup_before_urlfill_날짜' 로 백업. 바꾸는 값은 platform_published_url.naver 뿐이고,
    그 사이 주소가 채워졌으면(채움 대상이 아니면) 건드리지 않습니다. 반환: (채운 개수, 백업 파일 경로)."""
    todo = [r for r in rows if r["result"] == "채움"]
    if not todo:
        return 0, ""
    path = path or CHANNELS[RADAR_AUTOFILL_CHANNEL]["articles_file"]
    raw = _read_text_retry(path)
    data = json.loads(raw)
    base, ext = os.path.splitext(path)
    backup = f"{base}.backup_before_urlfill_{datetime.now():%Y%m%d}{ext}"
    if os.path.exists(backup):
        backup = f"{base}.backup_before_urlfill_{datetime.now():%Y%m%d_%H%M%S}{ext}"
    with open(backup, "w", encoding="utf-8", newline="") as f:
        f.write(raw)
    count = 0
    for r in todo:
        art = data.get(r["id"])
        if not art:
            continue
        urls = art.get("platform_published_url")
        if not isinstance(urls, dict):
            urls = art["platform_published_url"] = {}
        if not _radar_url_is_fillable(urls.get("naver") or ""):
            continue
        urls["naver"] = r["found"]
        count += 1
    _write_json_atomic(path, data)
    return count, backup


@app.route("/radar-url-autofill", methods=["POST"])
def radar_url_autofill_preview():
    """"🔗 글 주소 자동 채우기" — 미리보기만(아무것도 저장하지 않음). N데스크피커 전용."""
    if get_active_channel_id() != RADAR_AUTOFILL_CHANNEL:
        return redirect(url_for("index"))
    back = url_for("status") if request.form.get("back") == "status" else url_for("index")
    rss = _fetch_radar_rss()
    rows, old_skipped = ([], 0) if rss is None else _radar_autofill_plan(rss)
    bad_rss = [r for r in (rss or []) if not r.get("link")]
    return render_template("radar_url_autofill.html", rss_failed=rss is None, rows=rows, saved=None, back_url=back, old_skipped=old_skipped, bad_rss=bad_rss)


@app.route("/radar-url-autofill/apply", methods=["POST"])
def radar_url_autofill_apply():
    """미리보기에서 [확인하고 저장]을 눌렀을 때만 저장. 화면에서 넘어온 값(글 id|주소)을 믿지 않고 다시 계산해, 둘 다 같은 행만 저장합니다."""
    if get_active_channel_id() != RADAR_AUTOFILL_CHANNEL:
        return redirect(url_for("index"))
    back = url_for("status") if request.form.get("back") == "status" else url_for("index")
    rss = _fetch_radar_rss()
    if rss is None:
        return render_template("radar_url_autofill.html", rss_failed=True, rows=[], saved=None, back_url=back)
    approved = set(request.form.getlist("pair"))
    rows, old_skipped = _radar_autofill_plan(rss)
    for r in rows:
        if r["result"] == "채움" and f"{r['id']}|{r['found']}" not in approved:
            r["result"], r["ref"] = "못 찾음", "미리보기와 달라져 채우지 않음(다시 눌러 확인해 주세요)"
    count, backup = _radar_apply_autofill(rows)
    return render_template("radar_url_autofill.html", rss_failed=False, rows=rows, saved={"count": count, "backup": os.path.basename(backup)}, back_url=back, old_skipped=old_skipped, bad_rss=[r for r in rss if not r.get("link")])


NAVER_RELATED_CHANNELS = ("shop_naver", "naver_tips")  # 같은 네이버 블로그의 두 채널 — 하단 추천 후보는 둘 다에서 고릅니다
NAVER_RELATED_FRESH_DAYS = 14   # "새 글 자리" 후보: 최근 이 일수 안에 공개된 글
NAVER_RELATED_FRESH_SLOTS = 1   # 하단 추천 칸 중 "새 글 자리" 개수 (마지막 칸부터)
NAVER_RELATED_FRESH_CHANNEL = "naver_tips"  # "새 글 자리" 후보로 쓰는 채널 (네이버 SHOP(OLD) 글은 후보 제외)
_NAVER_RELATED_LINK_RE = re.compile(r'👉 <a href="([^"]+)"[^>]*>(.*?)</a>')


def _naver_final_related_items(final_html: str) -> list[tuple[str, str]]:
    """저장된 네이버 최종본의 하단 "같이 보면 좋은 글" 안에 들어간 [(글 주소, 제목)] (순서대로). 읽기만 합니다."""
    marker = "같이 보면 좋은 글"
    pos = (final_html or "").rfind(marker)
    return _NAVER_RELATED_LINK_RE.findall(final_html[pos:]) if pos >= 0 else []


def _naver_final_related_urls(final_html: str) -> list[str]:
    return [u for u, _ in _naver_final_related_items(final_html)]


def _naver_related_appearances() -> dict[tuple[str, str], list[str]]:
    """{(채널, 글 id): [그 글 최종본 하단에 들어간 글 주소들]} — 두 네이버 채널의 저장된 최종본을 읽기만 해서 만듭니다."""
    out: dict[tuple[str, str], list[str]] = {}
    for channel_id in NAVER_RELATED_CHANNELS:
        _, ch_articles = _read_channel_data(channel_id)
        for aid, art in ch_articles.items():
            urls = _naver_final_related_urls((art.get("platform_final") or {}).get("naver") or "")
            if urls:
                out[(channel_id, aid)] = urls
    return out


def _naver_related_ranked(
    current_channel: str, article_id: str, keyword: str, cluster_seed: str | None, memo_text: str, limit: int,
    fresh_slots: int = 0, extra_shown: dict | None = None,
) -> list[dict]:
    """
    네이버 하단 "같이 보면 좋은 글" 후보 (네이버 SHOP·네이버 일반 공통 규칙). 네이버 SHOP + 네이버 일반 두 채널에서
    이미 공개됐고 네이버 글 주소가 있는 다른 글 최대 limit개. 순서:
      ① blog_overlap 기준 이 글과 겹치는 글 — 🔴 먼저, 그다음 🟡 (같은 단계에서는 숍↔일반 "짝 글"이 같은 채널 글보다 앞)
      ② 같은 카테고리 (메모 "카테고리 제안"이 카테고리 목록 중 같은 것; 예전 이름은 새 이름으로 간주)
      ③ 최신 발행순
    "기타 리뷰"로 표시한 글, 아직 공개 전(예약)인 글, 네이버 글 주소가 아닌 글은 뺍니다.
    fresh_slots(기본 0 = 위 규칙 그대로)가 1 이상이면, limit칸 중 마지막 fresh_slots칸은 "새 글 자리"입니다:
    앞 칸에 안 들어간 네이버 일반(NAVER_RELATED_FRESH_CHANNEL) 글 중 최근 NAVER_RELATED_FRESH_DAYS일 안에 공개된 글을
    (네이버 SHOP(OLD) 글은 제외), 다른 글 최종본 하단에 나온 횟수가 적은 순(같으면 같은 카테고리 → 최신 발행순)으로 채웁니다.
    extra_shown({글 주소: 추가 횟수})은 교체 목록이 메모리에서만 횟수를 더해 쓸 때 넘깁니다. 해당 글이 없으면 위 규칙대로 채웁니다.
    반환: [{"title","url","channel","reason"}] (reason은 순위 이유 — 화면·최종본에는 title·url만 씁니다)
    """
    platform = "naver"
    now = datetime.now()
    excluded = _load_v4_excluded()
    categories = _v4_categories()
    aliases = _v4_aliases()
    my_cat = v4_board.classify(memo_text, current_channel, categories, aliases)["category_key"]
    my_cat = my_cat if my_cat in categories else ""
    overlap: dict = {}
    try:
        items = blog_overlap.load_items("naver", _blog_channels(), _read_text_retry, excluded)
        target = {"id": article_id, "channel": current_channel, "keyword": keyword, "cluster_seed": cluster_seed}
        for m in blog_overlap.find_overlaps(target, items):
            overlap[(m["channel"], m["id"])] = (
                0 if m["level"] == "red" else 1,
                0 if m["relation"] == blog_overlap.RELATION_PAIR else 1,
                m,
            )
    except (OSError, ValueError, KeyError):
        overlap = {}  # 겹침 계산은 순서를 고르는 부가 기능 — 실패해도 ②③ 순서로 계속합니다

    cands, seen_urls = [], set()
    for channel_id in NAVER_RELATED_CHANNELS:
        skip_ids = set(excluded.get(channel_id) or [])
        ch_queue, ch_articles = _read_channel_data(channel_id)
        for it in ch_queue:
            if (channel_id == current_channel and it.get("id") == article_id) or it.get("id") in skip_ids:
                continue
            art = ch_articles.get(it.get("id")) or {}
            url = (art.get("platform_published_url") or {}).get(platform)
            if not url or url in seen_urls:
                continue
            if not _is_public_by(art, platform, now) or not _is_naver_post_url(url):
                continue  # 아직 공개 전(예약)이거나 글 주소가 아닌 글은 후보에서 뺍니다
            seen_urls.add(url)
            cand_cat = v4_board.classify(
                v4_board.memo_text_for(art, CHANNELS[channel_id]["platforms"]), channel_id, categories, aliases
            )["category_key"]
            same_category = bool(my_cat) and cand_cat == my_cat
            ov = overlap.get((channel_id, it.get("id")))
            if ov:
                reason = (
                    ("🔴 같은 주제" if ov[0] == 0 else "🟡 비슷한 주제")
                    + (" · 짝 글" if ov[1] == 0 else " · 같은 채널 글")
                    + f" ({ov[2]['reason'].split(': ', 1)[-1]})"
                    + (" + 같은 카테고리" if same_category else "")
                )
                rank = (ov[0], ov[1])
            else:
                reason = "같은 카테고리" if same_category else "최신 발행순"
                rank = (2, 0)
            cands.append({
                "rank": rank, "same_category": same_category,
                "published_at": (art.get("platform_published_at") or {}).get(platform) or "",
                "title": (art.get("platform_title") or {}).get(platform) or it.get("keyword", ""),
                "url": url, "channel": channel_id, "reason": reason,
                "published_dt": _stored_publish_dt(art, platform),
            })
    cands.sort(key=lambda c: c["published_at"], reverse=True)       # ③ 최신 발행 먼저 (안정 정렬이라 아래 기준 안에서 유지됨)
    cands.sort(key=lambda c: (c["rank"], not c["same_category"]))  # ① 겹침(🔴→🟡, 짝 글 먼저) → ② 같은 카테고리
    chosen = cands[:limit]
    if fresh_slots > 0 and limit > 0:
        fresh_slots = min(fresh_slots, limit)
        head, rest = cands[: limit - fresh_slots], cands[limit - fresh_slots :]
        since = now - timedelta(days=NAVER_RELATED_FRESH_DAYS)
        # 새 글 자리 후보는 네이버 일반(naver_tips)의 최근 글만 — 네이버 SHOP(OLD) 글은 제외 (앞 칸 규칙은 그대로)
        pool = [c for c in rest if c["channel"] == NAVER_RELATED_FRESH_CHANNEL and c["published_dt"] and since <= c["published_dt"] <= now]
        if pool:
            shown = Counter(extra_shown or {})
            for key, urls in _naver_related_appearances().items():
                if key == (current_channel, article_id):
                    continue  # 지금 새로 만드는 글 자신의 (옛) 최종본은 세지 않습니다
                shown.update(set(urls))
            # 나온 횟수가 같으면 ① 이 글과 같은 카테고리 ② 최신 발행순
            pool.sort(key=lambda c: (shown[c["url"]], not c["same_category"], -c["published_dt"].timestamp()))
            fresh = pool[:fresh_slots]
            for c in fresh:
                c["reason"] = f"🆕 새 글 자리 (최근 {NAVER_RELATED_FRESH_DAYS}일 공개 · 다른 글 하단에 {shown[c['url']]}회 나옴) · {c['reason']}"
            fresh_urls = {c["url"] for c in fresh}
            chosen = head + fresh
            chosen += [c for c in rest if c["url"] not in fresh_urls][: limit - len(chosen)]  # 새 글이 모자라면 기존 순서로 채움
    return [{k: c[k] for k in ("title", "url", "channel", "reason")} for c in chosen]


def _shop_related_naver_posts(queue: list[dict], articles_raw: dict, article_id: str, article: dict, limit: int) -> list[dict]:
    """
    네이버 SHOP 하단 "같이 보면 좋은 글" 후보 — 네이버 SHOP + 네이버 일반의 공개된 네이버 글 (규칙은 _naver_related_ranked 참고).
    """
    me = find_queue_item(queue, article_id) or {}
    ranked = _naver_related_ranked(
        "shop_naver", article_id, me.get("keyword", ""), me.get("cluster_seed"),
        article["platform_metadata"].get("naver") or "", limit, fresh_slots=NAVER_RELATED_FRESH_SLOTS,
    )
    return [{"title": r["title"], "url": r["url"]} for r in ranked]


def _text_similarity(a: str, b: str) -> float:
    """아주 단순한 문자열 유사도(2글자 바이그램 자카드) — 형태소 분석 없이도 "노트북 추천"과
    "노트북 고르는 법"처럼 겹치는 부분이 있으면 값이 커집니다. "같이 보면 좋은 글" 후보를
    제목·키워드 유사도로 줄 세우는 데만 씁니다."""
    def bigrams(s: str) -> set:
        s = re.sub(r"\s+", "", s or "")
        return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()

    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


TIPS_RELATED_SIMILARITY_MIN = 0.3  # 이 값 이상이면 "비슷한 글", 미만이면 "비슷하지 않음" (우연히 글자 하나 겹친 글이 앞서는 것 방지)


def _naver_tips_related_posts(article_id: str, article: dict, keyword: str, limit: int) -> list[dict]:
    """
    네이버 일반 하단 "같이 보면 좋은 글" 후보 — 네이버 SHOP과 같은 규칙(_naver_related_ranked)입니다:
    ① blog_overlap 겹침(🔴→🟡, 짝 글 먼저) ② 같은 카테고리 ③ 최신 발행순.
    """
    tips_queue, _ = _read_channel_data("naver_tips")
    me = find_queue_item(tips_queue, article_id) or {}
    ranked = _naver_related_ranked(
        "naver_tips", article_id, keyword, me.get("cluster_seed"), article["platform_metadata"].get("naver") or "", limit,
        fresh_slots=NAVER_RELATED_FRESH_SLOTS,
    )
    return [{"title": r["title"], "url": r["url"]} for r in ranked]


def _naver_related_swap_rows(now: datetime | None = None) -> list[dict]:
    """
    "🔁 하단 추천 교체 목록" — 최근 NAVER_RELATED_FRESH_DAYS일 안에 공개된 네이버 글(두 네이버 채널)마다,
    지금 규칙으로 다시 고른 추천 4개와 저장된 최종본 하단의 추천 4개를 견줘서 달라지는 글만 돌려줍니다.
    읽기만 합니다 (최종본·json은 바꾸지 않음). 새로 들어오는 글이 짝 글이거나 🔴/🟡 겹침이면 priority=True로 맨 위.
    """
    now = now or datetime.now()
    since = now - timedelta(days=NAVER_RELATED_FRESH_DAYS)
    targets = []
    for channel_id in NAVER_RELATED_CHANNELS:
        ch_queue, ch_articles = _read_channel_data(channel_id)
        seen = set()
        for it in ch_queue:
            aid = it.get("id")
            if aid in seen:
                continue
            seen.add(aid)
            art = ch_articles.get(aid) or {}
            url = ((art.get("platform_published_url") or {}).get("naver") or "").strip()
            dt = _stored_publish_dt(art, "naver")
            final = (art.get("platform_final") or {}).get("naver") or ""
            if final and _is_naver_post_url(url) and dt and since <= dt <= now:
                targets.append((channel_id, aid, it, art, url, dt, final))

    def _compute(order: list | None) -> list[dict]:
        """targets를 order(없으면 그대로) 순서로 한 글씩 계산. 앞 글에서 새 글 자리로 새로 들어온 글은 나온 횟수를 메모리에서 +1 해서
        다음 글 계산에 반영합니다(한 글이 몰리지 않게) — 저장은 하지 않습니다."""
        extra: Counter = Counter()
        items = targets if order is None else sorted(targets, key=lambda t: order.index((t[0], t[1])))
        rows = []
        for channel_id, aid, it, art, url, dt, final in items:
            old_items = _naver_final_related_items(final)
            ranked = _naver_related_ranked(
                channel_id, aid, it.get("keyword", ""), it.get("cluster_seed"),
                (art.get("platform_metadata") or {}).get("naver") or "", shop_formatter.NAVER_RELATED_MAX,
                fresh_slots=NAVER_RELATED_FRESH_SLOTS, extra_shown=extra,
            )
            old_urls = {u for u, _ in old_items}
            new_urls = {r["url"] for r in ranked}
            if old_urls == new_urls:
                continue
            entering = [r for r in ranked if r["url"] not in old_urls]
            for r in entering:
                if r["reason"].startswith("🆕"):
                    extra[r["url"]] += 1
            leaving = [(u, html.unescape(t)) for u, t in old_items if u not in new_urls]
            rows.append({
                "channel": CHANNELS[channel_id]["short_label"], "channel_id": channel_id, "id": aid,
                "title": (art.get("platform_title") or {}).get("naver") or it.get("keyword", ""),
                "url": url, "published": f"{dt:%Y-%m-%d %H:%M}", "dt": dt,
                "old": [(u, html.unescape(t)) for u, t in old_items],
                "new": [{"url": r["url"], "title": r["title"], "reason": r["reason"]} for r in ranked],
                "entering": entering, "leaving": leaving,
                "has_old_block": bool(old_items),
                "priority": any("🔴" in r["reason"] or "🟡" in r["reason"] for r in entering),
                "block_html": shop_formatter._related_section_html([{"title": r["title"], "url": r["url"]} for r in ranked]),
            })
        rows.sort(key=lambda r: r["dt"], reverse=True)
        rows.sort(key=lambda r: not r["priority"])  # 안정 정렬 — 우선 글이 맨 위, 그 안에서는 최신 발행순
        return rows

    # 1차: 화면에 보일 순서(우선 글 → 최신순)를 정하고, 2차: 그 순서대로 위에서부터 다시 계산
    first = _compute(None)
    shown_order = [(r["channel_id"], r["id"]) for r in first]
    rest = [(t[0], t[1]) for t in sorted(targets, key=lambda t: t[5], reverse=True) if (t[0], t[1]) not in set(shown_order)]
    return _compute(shown_order + rest)

@app.route("/related-swap")
def related_swap():
    """네이버 "🔁 하단 추천 교체 목록" (읽기 전용 — 아무것도 저장하지 않음)."""
    return render_template("related_swap.html", rows=_naver_related_swap_rows(), days=NAVER_RELATED_FRESH_DAYS)


# ---------- 꿀팁 채널 → 같은 플랫폼의 "내 숍 추천 글" 연결 ({{SHOP:키워드|문구}}) ----------
# 롱텀의 {{LINK:}} 흐름(클러스터 전용)과는 별개입니다. 짝 채널 데이터는 읽기만 합니다.
TIPS_SHOP_PARTNER = {
    "naver_tips": ("shop_naver", "naver"),       # 네이버 일반 → 네이버 SHOP(OLD)에서 발행된 글
    "wp_tips": ("shop_wp", "wordpress"),         # 워드프레스 일반 → 워드프레스 SHOP(OLD)(같은 사이트)에서 발행된 글
}
# V5: 짝 글 후보 = 옛 숍 채널 공개 글 + 같은 블로그(통합 채널)의 새 숍글(is_shop_post, 공개된 글)
BLOG_SHOP_SOURCES = {
    "naver": (("shop_naver", "naver", False), ("naver_tips", "naver", True)),          # (채널, 플랫폼, 숍글만 고르기)
    "ssansegae": (("shop_wp", "wordpress", False), ("wp_tips", "wordpress", True)),
}
TIPS_SHOP_SIMILARITY_MIN = 0.4  # 3단계(비슷한 글): 키워드↔(키워드·제목) 글자 2-gram 자카드가 이 값 이상일 때만 짝으로 인정


def _read_channel_data(channel_id: str) -> tuple[list[dict], dict]:
    """다른 채널의 큐·글 데이터를 읽기만 합니다 (파일이 없거나 깨졌으면 빈 값)."""
    ch = CHANNELS[channel_id]
    try:
        ch_queue = json.loads(_read_text_retry(ch["queue_file"]) or "[]")
    except (FileNotFoundError, json.JSONDecodeError):
        ch_queue = []
    try:
        ch_articles = json.loads(_read_text_retry(ch["articles_file"]) or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        ch_articles = {}
    return ch_queue, ch_articles


def _public_posts(
    channel_id: str, platform: str, now: datetime, shop_only: bool = False, exclude_id: str | None = None
) -> list[dict]:
    """채널에서 "지금 공개된" 글(주소가 있고 발행·예약 시각이 지남; 예약 중·미발행 제외). 같은 주소는 한 번만.
    shop_only=True면 통합 채널(네이버·싼세계)의 새 숍글(_shop_post_state의 effective)만, exclude_id면 그 글은 뺍니다."""
    ch_queue, ch_articles = _read_channel_data(channel_id)
    out, seen_urls = [], set()
    for it in ch_queue:
        art = ch_articles.get(it.get("id")) or {}
        if exclude_id and it.get("id") == exclude_id:
            continue
        if not _is_public_by(art, platform, now):
            continue
        if shop_only:
            state = _shop_post_state(art, channel_id, (art.get("platform_raw") or {}).get(platform) or "")
            if not (state and state["effective"]):
                continue
        url = art["platform_published_url"][platform]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        out.append(
            {
                "keyword": it.get("keyword", ""),
                "title": (art.get("platform_title") or {}).get(platform) or "",
                "url": url,
                "published_dt": _stored_publish_dt(art, platform) or datetime.min,
                "published_at": (art.get("platform_published_at") or {}).get(platform) or "",
                "category": metadata_parser.parse_metadata_memo((art.get("platform_metadata") or {}).get(platform) or "").get("category") or "",
            }
        )
    return out


def _match_shop_post(keyword: str, posts: list[dict]) -> dict | None:
    """
    {{SHOP:키워드}}의 짝 찾기. ① 큐 키워드와 정규화 일치(공백·태그 무시, 소문자) → ② 제목에 키워드 포함 →
    ③ similarity.py의 글자 2-gram 자카드로 가장 비슷한 글(TIPS_SHOP_SIMILARITY_MIN 이상일 때만).
    한 단계에서 여러 개면 가장 최근 발행 글. 못 찾으면 None. 반환: {"title","url","rule"}
    """
    nk = _norm_link_text(keyword)
    if not nk:
        return None

    def newest(cands):
        return max(cands, key=lambda p: p["published_dt"])

    def shape(p, rule):
        return {"title": p["title"] or p["keyword"], "url": p["url"], "rule": rule}

    exact = [p for p in posts if _norm_link_text(p["keyword"]) == nk]
    if exact:
        return shape(newest(exact), "키워드 일치")
    contains = [p for p in posts if p["title"] and nk in _norm_link_text(p["title"])]
    if contains:
        return shape(newest(contains), "제목에 포함")
    kb = similarity.bigrams(similarity.normalize(keyword))
    scored = []
    for p in posts:
        score = max(
            similarity.jaccard(kb, similarity.bigrams(similarity.normalize(p["keyword"]))),
            similarity.jaccard(kb, similarity.bigrams(similarity.normalize(p["title"]))),
        )
        if score >= TIPS_SHOP_SIMILARITY_MIN:
            scored.append((score, p))
    if scored:
        top = max(s for s, _ in scored)
        best = newest([p for s, p in scored if s == top])
        return shape(best, f"비슷한 글 (유사도 {top:.2f})")
    return None


def _shop_partner_posts(channel_id: str, now: datetime, exclude_id: str | None = None) -> list[dict]:
    """일반글의 {{SHOP:}} 짝 후보 = 옛 숍 채널 공개 글 + 같은 블로그의 새 숍글(공개된 글). 자기 자신(exclude_id)은 뺍니다."""
    partner = TIPS_SHOP_PARTNER.get(channel_id)
    if not partner:
        return []
    blog = "naver" if channel_id == "naver_tips" else "ssansegae"
    posts, seen_urls = [], set()
    for ch, platform, shop_only in BLOG_SHOP_SOURCES[blog]:
        for p in _public_posts(ch, platform, now, shop_only=shop_only, exclude_id=exclude_id if ch == channel_id else None):
            if p["url"] not in seen_urls:
                seen_urls.add(p["url"])
                posts.append(p)
    return posts


def _tips_shop_links(raw: str, channel_id: str, article_id: str | None = None) -> dict | None:
    """원고의 {{SHOP:}} 키워드마다 짝 글을 찾아 {키워드: {"title","url","rule"} 또는 None}으로. {{SHOP:}}가 없으면 None."""
    keywords = shop_formatter.find_shop_keywords(raw)
    if not keywords or channel_id not in TIPS_SHOP_PARTNER:
        return None
    posts = _shop_partner_posts(channel_id, datetime.now(), exclude_id=article_id)
    return {kw: _match_shop_post(kw, posts) for kw in keywords}


SHOP_POSTS_EXPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Claude outputs", "shop_posts_list.md")


def _md_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


@app.route("/export-shop-posts", methods=["POST"])
def export_shop_posts():
    """
    "📋 추천 글 목록 내보내기" — 옛 숍 글(네이버 SHOP (OLD)·워드프레스 SHOP (OLD))과 네이버·싼세계의 새 숍글 중
    지금 공개된 글을 마크다운 표로 모아 "Claude outputs/shop_posts_list.md"에 덮어씁니다. 데이터(json)는 읽기만 하고 고치지 않습니다.
    """
    now = datetime.now()
    lines = [
        "# 내 숍 추천 글 목록",
        "",
        f"만든 시각: {now:%Y-%m-%d %H:%M} · 지금 공개된 글만 (예약 중·미발행 제외) · 채널별 발행일 최신순",
        "",
        "| 채널 | 키워드 | 제목 | 주소 | 발행일 | 카테고리 |",
        "|---|---|---|---|---|---|",
    ]
    total = 0
    sources = BLOG_SHOP_SOURCES["naver"] + BLOG_SHOP_SOURCES["ssansegae"]
    new_urls = {p["url"] for ch, plat, shop_only in sources if shop_only for p in _public_posts(ch, plat, now, shop_only=True)}
    for channel_id, platform, shop_only in sources:
        posts = sorted(_public_posts(channel_id, platform, now, shop_only=shop_only), key=lambda p: p["published_dt"], reverse=True)
        if not shop_only:
            posts = [p for p in posts if p["url"] not in new_urls]  # 같은 주소가 새 채널에도 있으면 새 채널 쪽 1줄만 남깁니다
        for p in posts:
            lines.append(
                "| " + " | ".join(
                    _md_cell(x)
                    for x in (CHANNELS[channel_id]["short_label"], p["keyword"], p["title"], p["url"], p["published_at"], _v4_aliases().get(p["category"], p["category"]))
                ) + " |"
            )
        total += len(posts)
    os.makedirs(os.path.dirname(SHOP_POSTS_EXPORT_PATH), exist_ok=True)
    with open(SHOP_POSTS_EXPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    tips_total = _export_tips_posts(now)
    _export_blog_posts(now)  # 블로그별 목록(naver_blog_posts.md · ssansegae_posts.md)도 같이 만듭니다
    return redirect(url_for("index", exported=total, exported_tips=tips_total))


TIPS_POSTS_EXPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Claude outputs", "tips_posts_list.md")


def _export_tips_posts(now: datetime) -> int:
    """
    꿀팁(naver_tips, wp_tips) 글 목록을 "Claude outputs/tips_posts_list.md"에 덮어씁니다 — 이미 발행·예약된 글과 작성 중인 초안.
    (기획하는 쪽에서 이미 다룬 주제와 겹치는 걸 걸러내는 용도.) json은 읽기만 합니다.
    """
    lines = [
        "# 꿀팁 글 목록",
        "",
        f"만든 시각: {now:%Y-%m-%d %H:%M} · 발행·예약된 글과 작성 중인 초안 · 채널별 발행일 최신순 (작성 중은 맨 아래)",
        "",
        "| 채널 | 키워드 | 제목 | 상태 | 숍글 | 발행일 | 연결한 숍 글 키워드 | 주소 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    total = 0
    for channel_id, platform in (("naver_tips", "naver"), ("wp_tips", "wordpress")):
        ch_queue, ch_articles = _read_channel_data(channel_id)
        rows, seen = [], set()
        for it in ch_queue:
            if it.get("id") in seen:
                continue
            seen.add(it.get("id"))
            art = ch_articles.get(it.get("id")) or {}
            url = (art.get("platform_published_url") or {}).get(platform) or ""
            date = (art.get("platform_published_at") or {}).get(platform) or ""
            raw = (art.get("platform_raw") or {}).get(platform) or ""
            title = (art.get("platform_title") or {}).get(platform) or ""
            if url or date:
                dt = _stored_publish_dt(art, platform)
                status = f"예약 {dt.month}월{dt.day}일" if dt and dt > now else "공개"
            elif raw or title:
                dt, status = None, "작성 중"
            else:
                continue  # 원고도 발행 정보도 없는 빈 키워드
            rows.append((dt or datetime.min, [
                CHANNELS[channel_id]["short_label"], it.get("keyword", ""), title, status,
                "숍글" if _is_shop_article(art, channel_id, platform) else "일반", date,
                ", ".join(shop_formatter.find_shop_keywords(raw)) or "없음", url,
            ]))
        rows.sort(key=lambda r: r[0], reverse=True)
        lines += ["| " + " | ".join(_md_cell(x) for x in r[1]) + " |" for r in rows]
        total += len(rows)
    with open(TIPS_POSTS_EXPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return total


_EXPORT_DIR = os.path.dirname(SHOP_POSTS_EXPORT_PATH)
BLOG_POSTS_EXPORTS = {
    "naver": {
        "title": f"{CHANNELS['naver_tips']['label']} 글 목록 (네이버 SHOP + 네이버 일반)",
        "path": os.path.join(_EXPORT_DIR, "naver_blog_posts.md"),
        "channels": (("shop_naver", "naver"), ("naver_tips", "naver")),
    },
    "ssansegae": {
        "title": f"{CHANNELS['wp_tips']['label']} 글 목록 (워드프레스 SHOP + 워드프레스 일반)",
        "path": os.path.join(_EXPORT_DIR, "ssansegae_posts.md"),
        "channels": (("shop_wp", "wordpress"), ("wp_tips", "wordpress")),
    },
}
_BLOG_EXPORT_HEADER = "| 채널 | 유형 | 카테고리 | 키워드 | 제목 | 상태 | 발행일 | 주소 |"


def _export_blog_posts(now: datetime) -> dict:
    """
    블로그별(네이버 / 싼세계) 글 목록을 "Claude outputs/naver_blog_posts.md"·"ssansegae_posts.md"에 덮어씁니다 — 글쓰기 채팅에
    첨부해서 중복 확인·내부 링크(A·B → C)에 쓰는 용도. 카테고리 목록 순서대로 묶고 그 안에서 발행일 최신순, "기타 리뷰" 글은 맨 아래 별도 표.
    json은 읽기만 합니다. 반환: {블로그: 글 수}
    """
    categories = _v4_categories()
    aliases = _v4_aliases()
    excluded = _load_v4_excluded()
    totals = {}
    for key, spec in BLOG_POSTS_EXPORTS.items():
        rows, ex_rows = [], []
        for channel_id, platform in spec["channels"]:
            ch_queue, ch_articles = _read_channel_data(channel_id)
            skip_ids = set(excluded.get(channel_id) or [])
            seen = set()
            for it in ch_queue:
                iid = it.get("id")
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                art = ch_articles.get(iid) or {}
                url = (art.get("platform_published_url") or {}).get(platform) or ""
                date = (art.get("platform_published_at") or {}).get(platform) or ""
                raw = (art.get("platform_raw") or {}).get(platform) or ""
                title = (art.get("platform_title") or {}).get(platform) or ""
                if url or date:
                    dt = _stored_publish_dt(art, platform)
                    status = f"예약 {dt.month}월{dt.day}일" if dt and dt > now else "공개"
                elif raw or title:
                    dt, status = None, "작성 중"
                else:
                    continue  # 원고도 발행 정보도 없는 빈 키워드
                is_shop = _is_shop_article(art, channel_id, platform)
                info = v4_board.classify(v4_board.memo_text_for(art, [platform]), channel_id, categories, aliases, is_shop=is_shop)
                type_cell = "C(추정)" if info["estimated"] else (info["type_key"] or "미기재")
                if info["category_key"] == "__out__":
                    cat_key, cat_cell = ("out", info["category_label"].split(": ", 1)[-1]), "⚠ " + info["category_label"].split(": ", 1)[-1]
                elif info["category_key"] == "__none__":
                    cat_key, cat_cell = ("none", ""), "미기재"
                else:
                    cat_key, cat_cell = ("cat", info["category_key"]), info["category_key"]
                row = {
                    "cat_key": cat_key, "cat_cell": cat_cell, "dt": dt or datetime.min, "has_dt": dt is not None,
                    "cells": ["숍" if is_shop else "일반", type_cell, cat_cell,
                              it.get("keyword", ""), title, status, date, url],
                    "url": url, "from_new": (channel_id, platform, True) in BLOG_SHOP_SOURCES[key],
                }
                (ex_rows if iid in skip_ids else rows).append(row)
        # 같은 주소가 옛 숍 채널과 새 채널에 모두 있으면 새 채널 쪽 1줄만 남깁니다
        new_urls = {r["url"] for r in rows + ex_rows if r["from_new"] and r["url"]}
        rows = [r for r in rows if r["from_new"] or not r["url"] or r["url"] not in new_urls]
        ex_rows = [r for r in ex_rows if r["from_new"] or not r["url"] or r["url"] not in new_urls]

        def group_order(k):
            kind, name = k
            return (categories.index(name), "") if kind == "cat" else ((len(categories), name) if kind == "out" else (len(categories) + 1, ""))

        groups: dict = {}
        for r in rows:
            groups.setdefault(r["cat_key"], []).append(r)
        summary = [f"{c} {len(groups.get(('cat', c), []))}" for c in categories]
        out_n = sum(len(v) for k, v in groups.items() if k[0] == "out")
        summary += [f"⚠ 카테고리 목록 밖 {out_n}", f"미기재 {len(groups.get(('none', ''), []))}", f"기타 리뷰 {len(ex_rows)}(제외)"]

        def table(rs):
            # 발행·예약일 최신순, 날짜 없는 글(작성 중)은 맨 아래
            rs = sorted(rs, key=lambda r: (r["has_dt"], r["dt"]), reverse=True)
            return [_BLOG_EXPORT_HEADER, "|---|---|---|---|---|---|---|---|"] + [
                "| " + " | ".join(_md_cell(x) for x in r["cells"]) + " |" for r in rs
            ]

        lines = [
            f"# {spec['title']}",
            "",
            f"- 만든 시각: {now:%Y-%m-%d %H:%M} (발행·예약된 글과 작성 중인 초안 / 카테고리별로 묶고 발행일 최신순)",
            "- 글쓰기 채팅에 첨부해서 중복 확인·내부 링크(A·B → C)에 쓰세요",
            "- 카테고리별 편수: " + " · ".join(summary),
        ]
        for k in sorted(groups, key=group_order):
            label = k[1] if k[0] == "cat" else ("⚠ " + k[1] if k[0] == "out" else "미기재")
            lines += ["", f"## {label} ({len(groups[k])}편)", ""] + table(groups[k])
        if ex_rows:
            lines += ["", f"## 기타 리뷰 — 내부 링크·중복 확인에서 제외 ({len(ex_rows)}편)", ""] + table(ex_rows)
        os.makedirs(_EXPORT_DIR, exist_ok=True)
        with open(spec["path"], "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        totals[key] = len(rows) + len(ex_rows)
    return totals


# ---------- 롱텀 기획 자료 내보내기 ("롱텀기획" Claude 프로젝트에 첨부할 파일 1개) ----------
LONGTERM_PLAN_EXPORT_PATH = os.path.join(_EXPORT_DIR, "longterm_plan_input.md")
LT_PLAN_STALE_DAYS = 60                 # "최종 확인"이 이 일수보다 오래됐으면 갱신 참고 표 (가)
LT_PLAN_RANK_MIN, LT_PLAN_RANK_MAX = 8, 30  # 노출은 있는데 평균 순위가 이 범위면 갱신 참고 표 (나)
LT_PLAN_RECENT_WEEKS = 8
LT_PLAN_NO_CONFIRM_DAYS = 30            # "최종 확인"이 없는 글은 발행 후 이 일수 이상 지나야 갱신 참고 표 (가)에 넣음
LT_PLAN_CATEGORY_SITE_NAMES = {"세금·절세": "세금·연말정산", "보험·연금": "4대보험·연금"}  # 옛 카테고리 이름 → 사이트 이름 (파일 표시만)


def _lt_site_cat(name):
    return LT_PLAN_CATEGORY_SITE_NAMES.get(name, name) if name else name


def _lt_readable_url(url: str) -> str:
    """파일에 보여 줄 때만 퍼센트 인코딩을 풉니다(저장된 주소는 그대로)."""
    from urllib.parse import unquote
    return unquote(url or "")


_LT_CONFIRM_LINE_RE =re.compile(r"^[\W_]*최종\s*확인[\s*_]*[:：]\s*(.+)$")
_LT_CONFIRM_DATE_RE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")


def _lt_confirm_date(raw: str):
    """원고 첫 줄이 "최종 확인: …"이면 (날짜 또는 None, 그 줄의 글자). 그 줄이 아니면 (None, "")."""
    first = next((ln.strip() for ln in (raw or "").splitlines() if ln.strip()), "")
    m = _LT_CONFIRM_LINE_RE.match(first)
    if not m:
        return None, ""
    d = _LT_CONFIRM_DATE_RE.search(m.group(1))
    if d:
        try:
            return datetime(int(d.group(1)), int(d.group(2)), int(d.group(3))).date(), m.group(1).strip()
        except ValueError:
            pass
    return None, m.group(1).strip()


def _lt_plan_articles(now: datetime) -> list[dict]:
    """롱텀 글(원고·발행 정보가 있는 것)을 읽기만 해서 한 줄씩 정리합니다. 성과는 perf_cache.json에 이미 저장된 값만 씁니다(목업 캐시는 제외)."""
    ch_queue, ch_articles = _read_channel_data("longterm_wp")
    qmap = {}
    for it in ch_queue:
        if it.get("id") and it["id"] not in qmap:
            qmap[it["id"]] = it
    seed_cats = engine.seed_category_map(engine.load_seasonal_calendar(os.path.join(os.path.dirname(os.path.abspath(__file__)), "seasonal_calendar.json")))
    cache = perf_tracker.load_cache()
    pages = {}
    if cache and cache.get("mode") != "mock":
        pages = (cache["sites"].get("longterm") or {}).get("pages", {})
    rows = []
    for iid in list(qmap) + [a for a in ch_articles if a not in qmap]:
        art = ch_articles.get(iid)
        if not isinstance(art, dict):
            continue
        it = qmap.get(iid, {})
        url = (art.get("platform_published_url") or {}).get("wordpress") or ""
        date = (art.get("platform_published_at") or {}).get("wordpress") or ""
        raw = (art.get("platform_raw") or {}).get("wordpress") or ""
        title = (art.get("platform_title") or {}).get("wordpress") or ""
        if art.get("retired"):
            # 묶음에서 뺀 글(워드프레스는 휴지통+301): 이미 다룬 주제로 계속 잡히게 목록에 남기되 발행·갱신 표에는 넣지 않습니다.
            rt = art["retired"]
            prev = rt.get("previous") or {}
            url, date, dt = prev.get("platform_published_url") or "", prev.get("platform_published_at") or "", None
            status = "합침"
        elif url or date:
            dt = _stored_publish_dt(art, "wordpress")
            status = "예약" if dt and dt > now else "발행"
        elif raw or title:
            dt, status = None, "초안"
        else:
            continue  # 원고도 발행 정보도 없는 빈 키워드
        seed = it.get("cluster_seed") or it.get("keyword") or "(대기열에 없는 글)"
        cat = _lt_site_cat(metadata_parser.parse_metadata_memo((art.get("platform_metadata") or {}).get("wordpress") or "").get("category")
                           or it.get("category") or seed_cats.get(seed) or "")
        confirm, confirm_text = _lt_confirm_date(raw)
        perf = None
        page = pages.get(perf_tracker.normalize_url(url)) if url else None
        if page:
            perf = page["cur"]
        rows.append({"id": iid, "seed": seed, "category": cat, "keyword": it.get("keyword", ""), "title": title, "status": status,
                     "dt": dt, "date": date, "url": url, "confirm": confirm, "confirm_text": confirm_text, "perf": perf})
    return rows


def _build_longterm_plan_md(week: str, now: datetime) -> tuple[str, dict]:
    """롱텀 기획 자료(마크다운)를 만들어 (글자, {"posts": 글 수, "queue": 이번 주 대기열 수})로 돌려줍니다. 파일은 쓰지 않고 json도 고치지 않습니다."""
    ch_queue, _ = _read_channel_data("longterm_wp")
    seed_cats = {k: _lt_site_cat(v) for k, v in engine.seed_category_map(
        engine.load_seasonal_calendar(os.path.join(os.path.dirname(os.path.abspath(__file__)), "seasonal_calendar.json"))).items()}
    arts = _lt_plan_articles(now)
    cache = perf_tracker.load_cache()
    perf_note = "저장된 서치콘솔 값 없음(성과 칸은 빈칸)"
    if cache and cache.get("mode") == "mock":
        perf_note = "저장된 성과가 가짜(목업) 값이라 쓰지 않음(성과 칸은 빈칸)"
    elif cache:
        cur = (cache.get("ranges") or {}).get("cur") or ["?", "?"]
        perf_note = f"perf_cache.json 최근 28일 {cur[0]} ~ {cur[1]} (가져온 시각 {cache.get('fetched_at', '?')})"
    counts = {s: sum(1 for a in arts if a["status"] == s) for s in ("발행", "예약", "초안", "합침")}

    lines = [
        "# 롱텀 기획 자료",
        "",
        f"- 만든 시각: {now:%Y-%m-%d %H:%M}",
        f"- 기준 주차: {week} 주차 (화면에서 보고 있던 주차)",
        f"- 롱텀 글 총 {len(arts)}편 — 발행 {counts['발행']} · 예약 {counts['예약']} · 초안 {counts['초안']}" + (f" · 합침 {counts['합침']}(대표글로 합쳐 워드프레스에서 뺀 글 — 이미 다룬 주제이니 같은 키워드를 다시 기획하지 마세요)" if counts["합침"] else ""),
        f"- 성과 출처: {perf_note}",
        "- 카테고리: 원고 메모의 \"카테고리 제안\" (없으면 대기열·시드 달력의 카테고리)",
        "- 옛 카테고리 이름은 사이트 이름으로 바꿔 표시함",
        "- 읽기 전용 자료입니다 — 판단 없이 저장된 값만 모았어요.",
        "",
    ]
    lines += weekly_plan_export.render_schedule_md(v4_board.week_monday(week), ("longterm",))  # ⓪ 발행 시간표 (10/19 주부터)
    lines += [
        f"## ② {week} 주차 롱텀 대기열",
        "",
    ]

    # ② 이번 주 대기열
    week_items = [i for i in ch_queue if i.get("week") == week]
    if not week_items:
        lines.append("아직 불러오기 안 함")
    else:
        groups: dict = {}
        for i in week_items:
            groups.setdefault(i.get("cluster_seed") or i.get("keyword") or "(묶음 없음)", []).append(i)
        for seed, items in groups.items():
            cat = _lt_site_cat(next((i.get("category") for i in items if i.get("category")), "")) or seed_cats.get(seed, "")
            lines += [f"### 📌 {_md_cell(seed)} ({_md_cell(cat) or '카테고리 미기재'})", "",
                      "| 카테고리 | 키워드 | 역할 | 월검색량 | 경쟁도 | 점수 | 승인 |", "|---|---|---|---|---|---|---|"]
            for i in items:
                lines.append("| " + " | ".join(_md_cell("" if x is None else str(x)) for x in (
                    _lt_site_cat(i.get("category")) or seed_cats.get(seed, ""), i.get("keyword", ""), i.get("cluster_role") or "",
                    i.get("total_search_volume"), i.get("competition") or "", i.get("score"),
                    "승인" if i.get("approved") else "미승인")) + " |")
            lines.append("")

    # ③ 롱텀 글 전체
    lines += ["", "## ③ 롱텀 글 전체 목록 (묶음별 · 묶음은 가장 최근 발행·예약일 순, 묶음 안은 오래된 글부터)", ""]
    by_seed: dict = {}
    for a in arts:
        by_seed.setdefault(a["seed"], []).append(a)

    def newest(rows_):
        return max((r["dt"] for r in rows_ if r["dt"]), default=datetime.min)

    def seed_cat(rows_):
        cats = [r["category"] for r in rows_ if r["category"]]
        return max(sorted(set(cats)), key=cats.count) if cats else ""

    def perf_cells(r):
        p = r["perf"]
        if not p:
            return ["", "", ""]
        return [p["impressions"], p["clicks"], f"{p['position']:.1f}" if p["impressions"] else ""]

    for seed in sorted(by_seed, key=lambda s: newest(by_seed[s]), reverse=True):
        rs = sorted(by_seed[seed], key=lambda r: r["dt"] or datetime.max)
        lines += [f"### 📌 {_md_cell(seed)} ({_md_cell(seed_cat(rs)) or '카테고리 미기재'} · {len(rs)}편)", "",
                  "| 카테고리 | 키워드 | 제목 | 상태 | 발행·예약일 | 글 주소 | 최종 확인 | 노출(28일) | 클릭(28일) | 평균 순위 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rs:
            lines.append("| " + " | ".join(_md_cell(str(x)) for x in (
                r["category"], r["keyword"], r["title"], r["status"], r["date"], _lt_readable_url(r["url"]),
                r["confirm"].isoformat() if r["confirm"] else "", *perf_cells(r))) + " |")
        lines.append("")

    # ④ 요약
    lines += ["", "## ④ 요약", "", "### 카테고리별 묶음·글 수", "", "| 카테고리 | 묶음 수 | 글 수 |", "|---|---|---|"]
    cat_stat: dict = {}
    for seed, rs in by_seed.items():
        c = seed_cat(rs) or "카테고리 미기재"
        st = cat_stat.setdefault(c, [0, 0])
        st[0] += 1
        st[1] += len(rs)
    for c in sorted(cat_stat, key=lambda c: (-cat_stat[c][1], c)):
        lines.append(f"| {_md_cell(c)} | {cat_stat[c][0]} | {cat_stat[c][1]} |")
    lines += ["", f"### 최근 {LT_PLAN_RECENT_WEEKS}주 묶음 목록 (대기열 기준 · {week} 주차까지)", "",
              "| 주차 | 카테고리 | 묶음(시드) | 대표 키워드 | 승인/전체 |", "|---|---|---|---|---|"]
    for w in sorted({i["week"] for i in ch_queue if i.get("week") and i["week"] <= week}, reverse=True)[:LT_PLAN_RECENT_WEEKS]:
        wg: dict = {}
        for i in ch_queue:
            if i.get("week") == w:
                wg.setdefault(i.get("cluster_seed") or i.get("keyword") or "(묶음 없음)", []).append(i)
        for seed, items in wg.items():
            head = next((i.get("keyword") for i in items if i.get("cluster_role") == "대표" and i.get("keyword")), "") or seed
            cat = _lt_site_cat(next((i.get("category") for i in items if i.get("category")), "")) or seed_cats.get(seed, "")
            lines.append("| " + " | ".join(_md_cell(str(x)) for x in (
                w, cat, seed, head, f"{sum(1 for i in items if i.get('approved'))}/{len(items)}")) + " |")

    # ⑤ 갱신 참고 (목록만)
    published = sorted((a for a in arts if a["status"] == "발행"), key=lambda a: a["dt"] or datetime.min)
    today = now.date()
    stale_rows, recent_no_confirm = [], 0
    for a in published:
        if a["confirm"] is not None:
            if (today - a["confirm"]).days > LT_PLAN_STALE_DAYS:
                why = f"조건 ①: 최종 확인 {a['confirm'].isoformat()} ({(today - a['confirm']).days}일 전, {LT_PLAN_STALE_DAYS}일 초과)"
            else:
                continue
        else:
            no_date = "\"최종 확인\" 날짜를 못 읽음" if a["confirm_text"] else "\"최종 확인\" 없음"
            if a["dt"] is None:
                why = f"조건 ②: {no_date}, 발행일 불명"
            elif (today - a["dt"].date()).days >= LT_PLAN_NO_CONFIRM_DAYS:
                why = f"조건 ②: {no_date}, 발행 후 {(today - a['dt'].date()).days}일 경과({LT_PLAN_NO_CONFIRM_DAYS}일 이상)"
            else:
                recent_no_confirm += 1
                continue
        stale_rows.append("| " + " | ".join(_md_cell(str(x)) for x in (
            a["category"], a["keyword"], a["title"], a["date"], _lt_readable_url(a["url"]), why)) + " |")
    lines += ["", "## ⑤ 갱신 참고 표 (발행된 글 중 · 판단 없이 목록만)", "",
              f"### (가) \"최종 확인\"이 {LT_PLAN_STALE_DAYS}일 넘은 글 / \"최종 확인\" 없이 발행 {LT_PLAN_NO_CONFIRM_DAYS}일 이상 지난 글", "",
              f"최종 확인 없는 최근 글 {recent_no_confirm}편({LT_PLAN_NO_CONFIRM_DAYS}일 미만)은 제외", "",
              "| 카테고리 | 키워드 | 제목 | 발행일 | 글 주소 | 이유 |", "|---|---|---|---|---|---|"] + stale_rows
    lines += ["", f"### (나) 노출은 있는데 평균 순위가 {LT_PLAN_RANK_MIN}~{LT_PLAN_RANK_MAX}위인 글", "",
              "| 카테고리 | 키워드 | 제목 | 발행일 | 글 주소 | 노출(28일) | 클릭(28일) | 평균 순위 | 이유 |", "|---|---|---|---|---|---|---|---|---|"]
    for a in published:
        p = a["perf"]
        if p and p["impressions"] > 0 and LT_PLAN_RANK_MIN <= p["position"] <= LT_PLAN_RANK_MAX:
            lines.append("| " + " | ".join(_md_cell(str(x)) for x in (
                a["category"], a["keyword"], a["title"], a["date"], _lt_readable_url(a["url"]), p["impressions"], p["clicks"], f"{p['position']:.1f}",
                f"노출 {p['impressions']}회, 평균 {p['position']:.1f}위")) + " |")
    return "\n".join(lines) + "\n", {"posts": len(arts), "queue": len(week_items)}


def _export_longterm_plan(week: str, now: datetime, path: str = LONGTERM_PLAN_EXPORT_PATH) -> dict:
    """롱텀 기획 자료를 path에 덮어씁니다(기본: Claude outputs/longterm_plan_input.md). json은 읽기만 합니다."""
    text, info = _build_longterm_plan_md(week, now)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return info


@app.route("/export-longterm-plan", methods=["POST"])
def export_longterm_plan():
    """"📋 롱텀 기획 자료 내보내기" — 롱텀 대기열·글·성과를 읽기만 해서 Claude outputs/longterm_plan_input.md 한 파일로 저장합니다."""
    week = request.form.get("week") or engine.get_week_label()
    info = _export_longterm_plan(week, datetime.now())
    return redirect(url_for("index", week=week, lt_plan=info["posts"]))


# ---------- 주간 기획 자료 만들기 ("🗓 주간 기획 자료 만들기" — 살림 + 롱텀 md를 버튼 하나로) ----------
SALIM_PLAN_EXPORT_PATH = os.path.join(_EXPORT_DIR, "salim_plan_input.md")
PLAN_TERMS_FILE = "plan_search_terms.json"  # ⑦ 실제 검색어 마지막 입력값 (새 파일 — 다음에 칸에 미리 채움)


def _load_plan_terms() -> dict:
    try:
        with open(PLAN_TERMS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {"creator": str(d.get("creator") or ""), "myblog": str(d.get("myblog") or "")} if isinstance(d, dict) else {"creator": "", "myblog": ""}
    except (OSError, ValueError):
        return {"creator": "", "myblog": ""}
# V5: 옛 숍 채널(shop_naver·shop_wp)의 주간 대기열은 더 이상 이 버튼이 만들거나 저장하지 않습니다 (롱텀만).
# 숍글(C 결정형) 후보는 저장 없이 계산만 해서 기획 자료 ③-2에 싣습니다 (_plan_shop_candidates).
WEEKLY_PLAN_QUEUE_CHANNELS = (("longterm_wp", CHANNELS["longterm_wp"]["label"]),)
PLAN_APPROVED_BLOGS = (  # (블로그 이름, 새 채널, 새 채널 이름, 옛 숍 채널, 옛 채널 이름, 플랫폼) — ③ 승인된 키워드 표
    (CHANNELS["naver_tips"]["label"], "naver_tips", CHANNELS["naver_tips"]["label"], "shop_naver", "네이버 SHOP (OLD)", "naver"),
    (CHANNELS["wp_tips"]["label"], "wp_tips", CHANNELS["wp_tips"]["label"], "shop_wp", "워드프레스 SHOP (OLD)", "wordpress"),
)


def _strict_channel_queue(channel_id: str) -> list[dict]:
    """채널 대기열을 저장 전에 안전하게 읽습니다 — 파일이 없으면 [], 비었거나 깨졌으면 오류(그 상태로 저장해 덮어쓰지 않으려고)."""
    path = CHANNELS[channel_id]["queue_file"]
    try:
        content = _read_text_retry(path)
    except FileNotFoundError:
        return []
    if not content.strip():
        raise RuntimeError(f"{path} 파일이 비어있습니다. 구글드라이브 동기화가 끝나지 않았을 수 있어요. 잠시 후 다시 눌러보세요.")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise RuntimeError(f"{path} 파일을 읽는 중 오류가 났습니다. 구글드라이브 동기화가 끝나지 않았을 수 있어요. 잠시 후 다시 눌러보세요.")


def _prepare_week_queue(channel_id: str, week: str, persist: bool) -> tuple[list[dict], str]:
    """그 주차 대기열을 돌려줍니다. 이미 있으면 그대로(다시 만들거나 덮어쓰지 않음), 없으면 "이번 주 키워드 새로 불러오기"(generate)와
    같은 함수로 만듭니다. persist=False(미리보기)면 메모리에서만 계산하고 대기열·시드 선택 파일에 저장하지 않습니다."""
    cfg = CHANNELS[channel_id]
    queue = _strict_channel_queue(channel_id)
    existing = [i for i in queue if i.get("week") == week]
    if existing:
        return existing, "저장된 대기열 사용 (다시 만들지 않음)"
    _, articles = _read_channel_data(channel_id)
    approved_history: dict[str, str] = {}
    retired_ids = {aid for aid, a in articles.items() if isinstance(a, dict) and a.get("retired")}  # 묶음에서 뺀 글도 '이미 다룬 주제'
    for item in sorted(queue, key=lambda i: i["week"]):
        if item.get("approved") or item.get("id") in retired_ids:
            approved_history[item["keyword"]] = item["week"]
    seed_last_used = _compute_seed_last_used(queue, articles, cfg["platforms"], week)
    size = _queue_size_for(cfg)
    if cfg.get("queue_builder") == "shopping":
        new_items = engine.build_shopping_weekly_queue(
            top_n=size, week=week, exclude_keywords=set(), approved_history=approved_history,
            seed_last_used=seed_last_used, existing_seed_counts={},
        )
        seed_path = _seed_selection_file_for(channel_id)
    else:
        new_items = engine.build_longterm_weekly_queue(
            top_n=size, week=week, exclude_keywords=set(), approved_history=approved_history,
            seed_last_used=seed_last_used, existing_seed_counts={},
        )
        seed_path = SEED_SELECTION_FILE
    if not persist:
        return new_items, "새로 계산함 — 메모리에서만(저장 안 함, 미리보기)"
    _save_seed_selection(week, engine.LAST_RUN_INFO.get("seed_selection"), seed_path)
    fresh = _strict_channel_queue(channel_id)  # 계산하는 동안 대기열이 바뀌었을 수 있어 저장 직전에 다시 읽어 합칩니다
    if any(i.get("week") == week for i in fresh):
        return [i for i in fresh if i.get("week") == week], "저장된 대기열 사용 (계산 중 다른 곳에서 만들어짐)"
    _write_json_atomic(cfg["queue_file"], fresh + new_items)
    return new_items, "대기열이 없어 새로 불러와 저장함"


def _plan_shop_candidates(week: str) -> dict:
    """③-2 숍글(C 결정형) 후보 — 옛 숍 자동 대기열과 같은 엔진(build_shopping_weekly_queue)을 메모리에서만 돌립니다. 어떤 json에도 쓰지 않습니다
    (대기열·시드 선택 파일은 건드리지 않고, 신호 캐시(signal_cache.json)도 읽기만 하고 저장은 막습니다).
    돌려쓰기·중복 기준(approved_history, seed_last_used)은 옛 숍 큐 + 새 채널(네이버·싼세계)의 승인 항목·글을 합쳐서 계산합니다."""
    import signal_enricher
    approved_history: dict[str, str] = {}
    seed_last_used: dict[str, str] = {}
    for cid in ("shop_naver", "shop_wp", "naver_tips", "wp_tips"):
        queue = _strict_channel_queue(cid)  # 깨진 파일이면 오류 → 호출한 쪽이 경고로 처리
        _, articles = _read_channel_data(cid)
        for item in queue:
            if item.get("approved") and item.get("keyword") and item.get("week"):
                approved_history[item["keyword"]] = max(approved_history.get(item["keyword"], ""), item["week"])
        for seed, w in _compute_seed_last_used(queue, articles, CHANNELS[cid]["platforms"], week).items():
            seed_last_used[seed] = max(seed_last_used.get(seed, ""), w)
    original_save = signal_enricher._save_cache
    signal_enricher._save_cache = lambda cache: None  # 신호 캐시에 쓰지 못하게 (읽기는 그대로)
    try:
        items = engine.build_shopping_weekly_queue(
            top_n=SHOP_WEEKLY_QUEUE_SIZE, week=week, exclude_keywords=set(), approved_history=approved_history,
            seed_last_used=seed_last_used, existing_seed_counts={},
        )
    finally:
        signal_enricher._save_cache = original_save
    seeds = {i.get("cluster_seed") or i.get("keyword") for i in items}
    return {
        "items": items, "total_clusters": len(seeds),
        "status": f"새로 계산함 — 메모리에서만(저장 안 함) · 키워드 {len(items)}개 / 묶음 {len(seeds)}개",
    }


def _plan_post_state(art: dict, platform: str, now: datetime) -> str:
    """미작성 / 작성 중 / 예약 M/D / 발행 — 기획 자료 ③ 표용."""
    url = (art.get("platform_published_url") or {}).get(platform) or ""
    date = (art.get("platform_published_at") or {}).get(platform) or ""
    if url or date:
        dt = _stored_publish_dt(art, platform)
        return f"예약 {dt.month}/{dt.day}" if dt and dt > now else "발행"
    if (art.get("platform_raw") or {}).get(platform) or (art.get("platform_title") or {}).get(platform):
        return "작성 중"
    return "미작성"


def _plan_approved_rows(week: str, now: datetime) -> list[dict]:
    """③ 승인된 키워드 — 이번 주·지난주에 승인한 네이버/싼세계 키워드(새 채널 + 옛 숍 채널). 읽기만 합니다."""
    weeks = {week, v4_board.shift_week(week, -1)}
    rows = []
    for order, (blog, ch, ch_label, old, old_label, platform) in enumerate(PLAN_APPROVED_BLOGS):
        for cid, label, is_old in ((ch, ch_label, 0), (old, old_label, 1)):
            queue, articles = _read_channel_data(cid)
            seen = set()
            for it in queue:
                if it.get("week") not in weeks or not it.get("approved") or it.get("id") in seen:
                    continue
                seen.add(it.get("id"))
                art = articles.get(it.get("id")) or {}
                rows.append({
                    "week": it["week"], "blog": blog, "blog_order": order, "channel_label": label, "old": is_old,
                    "keyword": it.get("keyword", ""), "target": it.get("source") == "target_keywords",
                    "angle": it.get("angle_note") or "", "shop": _is_shop_article(art, cid, platform),
                    "volume": it.get("total_search_volume") if it.get("total_search_volume") is not None else "",
                    "state": _plan_post_state(art, platform, now),
                })
    return rows


def _plan_blog_posts(now: datetime, categories: list, aliases: dict) -> dict:
    """⑥ 블로그별 기존 글 목록(간단판)용 행. 글을 고르는 규칙은 _export_blog_posts와 같고(발행·예약·작성 중, 기타 리뷰는 개수만), 주소 칸만 뺍니다."""
    excluded = _load_v4_excluded()
    out = {}
    for key, spec in BLOG_POSTS_EXPORTS.items():
        rows, ex_n = [], 0
        for channel_id, platform in spec["channels"]:
            ch_queue, ch_articles = _read_channel_data(channel_id)
            skip_ids = set(excluded.get(channel_id) or [])
            seen = set()
            for it in ch_queue:
                iid = it.get("id")
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                art = ch_articles.get(iid) or {}
                url = (art.get("platform_published_url") or {}).get(platform) or ""
                date = (art.get("platform_published_at") or {}).get(platform) or ""
                raw = (art.get("platform_raw") or {}).get(platform) or ""
                title = (art.get("platform_title") or {}).get(platform) or ""
                if url or date:
                    dt = _stored_publish_dt(art, platform)
                    date_cell = f"{date} (예약)" if dt and dt > now else date
                elif raw or title:
                    dt, date_cell = None, "작성 중"
                else:
                    continue
                if iid in skip_ids:
                    ex_n += 1
                    continue
                is_shop = _is_shop_article(art, channel_id, platform)
                info = v4_board.classify(v4_board.memo_text_for(art, [platform]), channel_id, categories, aliases, is_shop=is_shop)
                type_cell = "C(추정)" if info["estimated"] else (info["type_key"] or "미기재")
                cat = {"__none__": "미기재"}.get(info["category_key"]) or (
                    "⚠ " + info["category_label"].split(": ", 1)[-1] if info["category_key"] == "__out__" else info["category_key"])
                rows.append({"cat": cat, "dt": dt, "cells": ["숍" if is_shop else "일반", type_cell, it.get("keyword", ""), title, date_cell]})
        out[key] = {"title": spec["title"], "rows": rows, "excluded": ex_n}
    return out


def _export_longterm_plan_for_week(week: str, now: datetime, path: str, memory_queue: list | None) -> dict:
    """기존 _export_longterm_plan을 그대로 부릅니다. 롱텀 대기열이 아직 파일에 없고 메모리에만 있을 때(미리보기)는
    그 동안만 롱텀 대기열 읽기에 메모리 항목을 얹어서 같은 함수로 만듭니다(형식 변경 없음)."""
    if not memory_queue:
        return _export_longterm_plan(week, now, path)
    original = _read_channel_data

    def with_memory(channel_id):
        q, a = original(channel_id)
        return (q + memory_queue, a) if channel_id == "longterm_wp" else (q, a)

    globals()["_read_channel_data"] = with_memory
    try:
        return _export_longterm_plan(week, now, path)
    finally:
        globals()["_read_channel_data"] = original


def _build_weekly_plan(week: str, now: datetime, *, persist: bool = True,
                       salim_path: str = SALIM_PLAN_EXPORT_PATH, longterm_path: str = LONGTERM_PLAN_EXPORT_PATH,
                       search_terms: dict | None = None) -> dict:
    """"🗓 주간 기획 자료 만들기": 대기열 준비 → 살림 기획 자료(salim_plan_input.md) → 롱텀 기획 자료(longterm_plan_input.md).
    persist=False면 대기열·시드 선택·신호/타깃 캐시를 어디에도 저장하지 않습니다(미리보기: 파일 경로를 preview/로 넘겨서 부름)."""
    import signal_enricher
    started = time.time()
    week = v4_board.week_monday(week)
    categories, aliases = _v4_categories(), _v4_aliases()
    calendar = engine.load_seasonal_calendar("shopping_calendar.json")
    seed_cats = engine.seed_category_map(calendar)
    warnings: list[str] = []
    counter = weekly_plan_export.CallCounter()
    original_cache_file = signal_enricher.CACHE_FILE
    if not persist:
        signal_enricher.CACHE_FILE = os.devnull  # 대기열 계산이 신호 캐시(signal_cache.json)에 쓰지 못하게
    try:
        with counter:
            # [2] 대기열 준비
            queues, memory_longterm = {}, None
            for cid, label in WEEKLY_PLAN_QUEUE_CHANNELS:
                try:
                    items, status = _prepare_week_queue(cid, week, persist)
                except Exception as e:  # noqa: BLE001 — 한 채널 실패가 나머지 자료 만들기를 막지 않게
                    items, status = [], f"실패: {type(e).__name__}: {e}"[:200]
                    warnings.append(f"{label} 대기열 준비 실패 — {status}")
                queues[cid] = {"label": label, "items": items, "status": status}
                if cid == "longterm_wp" and status.startswith("새로 계산함"):
                    memory_longterm = items
            # [3] 살림 기획 자료 — ③-2 숍글 후보는 저장 없이 계산만 (옛 숍 대기열은 더 이상 만들지 않음)
            try:
                shop_cands = _plan_shop_candidates(week)
            except Exception as e:  # noqa: BLE001 — 숍글 후보 계산 실패가 나머지 자료 만들기를 막지 않게
                shop_cands = {"items": [], "total_clusters": 0, "status": f"실패: {type(e).__name__}: {e}"[:200]}
                warnings.append(f"숍글 후보 계산 실패 — {shop_cands['status']}")
            cand = weekly_plan_export.collect_candidates(calendar, week)
            picked = cand["picked"]
            warnings += cand["errors"]
            if config.USE_MOCK:
                warnings.append("네이버 검색광고 키가 없어(목 모드) 일반글 후보가 비어 있습니다.")
            weekly_plan_export.attach_rising(picked, persist_cache=persist)
            excluded = _load_v4_excluded()
            blog_items = {b: blog_overlap.load_items(b, _blog_channels(), _read_text_retry, excluded) for b in blog_overlap.BLOGS}
            weekly_plan_export.attach_overlaps(picked, blog_items)
            weekly_plan_export.attach_shop_overlaps(shop_cands["items"], blog_items)
        boards = []
        for w in [v4_board.shift_week(week, -n) for n in range(weekly_plan_export.RECENT_WEEKS, 0, -1)]:
            boards.append((w, v4_board.build_board(w, _v4_channel_info(), categories, excluded, _read_text_retry, aliases)))
        approved_rows = _plan_approved_rows(week, now)
        notes = [f"숍글 후보: {shop_cands['status']}", f"외부 호출: {counter.summary()}"]
        text, meta = weekly_plan_export.build_salim_md({
            "week": week, "now": now, "categories": categories, "calendar": calendar, "seed_cats": seed_cats,
            "approved_rows": approved_rows, "shop_candidates": shop_cands, "picked": picked,
            "boards": boards, "blog_posts": _plan_blog_posts(now, categories, aliases), "notes": notes,
            "search_terms": search_terms or {},
        })
        if persist and search_terms is not None:  # 이번에 붙여넣은 내용을 새 파일에 저장 (다음에 칸에 미리 채움)
            _write_json_atomic(PLAN_TERMS_FILE, {"creator": search_terms.get("creator_text", ""), "myblog": search_terms.get("myblog_text", "")})
        os.makedirs(os.path.dirname(salim_path), exist_ok=True)
        with open(salim_path, "w", encoding="utf-8") as f:
            f.write(text)
        # [4] 롱텀 기획 자료 — 기존 "롱텀 기획 자료 내보내기"와 같은 함수
        lt_info = _export_longterm_plan_for_week(week, now, longterm_path, memory_longterm)
    finally:
        signal_enricher.CACHE_FILE = original_cache_file
    if meta["post_cutoff"]:
        warnings.append(f"파일이 {weekly_plan_export.MAX_FILE_BYTES // 1024}KB를 넘어 기존 글 목록을 최근 {weekly_plan_export.FALLBACK_POST_WEEKS}주로 줄였습니다.")
    stats = weekly_plan_export.candidate_stats(picked, categories)
    return {
        "week": week, "range": weekly_plan_export.week_range_label(week), "salim_path": salim_path, "longterm_path": longterm_path,
        "salim_bytes": meta["bytes"], "longterm_bytes": os.path.getsize(longterm_path), "post_cutoff": meta["post_cutoff"],
        "queues": {cid: {"label": q["label"], "count": len(q["items"]), "status": q["status"]} for cid, q in queues.items()},
        "shop_candidates": len(shop_cands["items"]), "shop_candidate_clusters": shop_cands["total_clusters"],
        "approved_rows": len(approved_rows),
        "stats": stats, "candidates": len(picked), "collect": cand["stats"], "api_total": counter.total, "api_summary": counter.summary(),
        "rising_checked": min(len(picked), weekly_plan_export.RISING_TOP_N), "longterm_posts": lt_info["posts"], "longterm_queue": lt_info["queue"],
        "warnings": warnings, "dropped": cand["dropped"], "picked": picked, "elapsed": round(time.time() - started, 1),
    }


@app.route("/weekly-plan-export", methods=["POST"])
def weekly_plan_export_route():
    """"🗓 주간 기획 자료 만들기" — 화면에 띄운 주차 기준으로 살림·롱텀 기획 자료 md 2개를 만들고 결과를 화면에 보여줍니다."""
    terms = {"creator_text": request.form.get("terms_creator", ""), "myblog_text": request.form.get("terms_myblog", "")}
    terms["creator"] = weekly_plan_export.clean_search_terms(terms["creator_text"])
    terms["myblog"] = weekly_plan_export.clean_search_terms(terms["myblog_text"])
    info = _build_weekly_plan(request.form.get("week") or engine.get_week_label(), datetime.now(), search_terms=terms)
    return render_template("weekly_plan_result.html", info=info)


# ---------- 확정 계획(plan 칸) 보기 — tools/push_plan.py 로 넣은 항목만 plan 칸이 있습니다. 읽기만 합니다 ----------
def _plan_view(it: dict) -> dict | None:
    """대기열 항목의 plan 칸을 화면·내보내기용으로 정리합니다. plan 칸이 없거나 확정 제목이 없으면 None(= 지금과 똑같이 보임)."""
    pl = it.get("plan")
    if not isinstance(pl, dict) or not pl.get("final_title"):
        return None
    outline = []
    for sec in pl.get("outline") or []:
        if isinstance(sec, dict):
            outline.append({"heading": str(sec.get("heading") or ""), "points": [str(x) for x in (sec.get("points") or [])]})
        elif sec:
            outline.append({"heading": str(sec), "points": []})
    products = [{"name": str(x.get("name") or ""), "url": str(x.get("url") or "")}
                for x in (pl.get("products") or []) if isinstance(x, dict) and x.get("url")]
    date = str(pl.get("publish_date") or "")
    wd = str(pl.get("weekday") or "")
    pn = pl.get("product_need")
    need = None  # product_need 가 없는 항목(11/16 주 등)은 None = 화면에 아무것도 더 안 보임
    if isinstance(pn, dict):
        if pn.get("need") is False or not pn.get("count"):
            need = "필요 없음"
        else:
            need = f"{pn.get('count')}개 · {' '.join(str(pn.get('condition') or '').split()) or '조건 없음'}" + (f" · 링크: {pn['link_kind']}" if pn.get("link_kind") else "")
    return {
        "publish": f"{date}({wd})" if date and wd else date,
        "reserve": not date,
        "final_title": str(pl["final_title"]),
        "alt_titles": [str(x) for x in (pl.get("alt_titles") or [])][:2],
        "search_intent": str(pl.get("search_intent") or ""),
        "outline": outline,
        "products": products,
        "product_need": need,
    }


def _plan_md_lines(it: dict, head: str) -> list[str]:
    """주간 확정 목록에 넣는 글 한 편의 계획 블록. plan 칸이 없으면 빈 목록."""
    v = _plan_view(it)
    if not v:
        return []
    one = lambda x: " ".join(str(x).split())  # noqa: E731 — 줄바꿈 정리
    lines = [f"#### {head}", "",
             f"- 발행 예정일: {v['publish'] or '-'}",
             f"- 확정 제목: {one(v['final_title'])}",
             f"- 예비 제목 2개: " + (" / ".join(one(t) for t in v["alt_titles"]) or "-"),
             f"- 검색 의도: {one(v['search_intent']) or '-'}",
             "- 본문 설계(소제목):"]
    for n, sec in enumerate(v["outline"], 1):
        pts = " · ".join(one(p) for p in sec["points"])
        lines.append(f"  {n}. {one(sec['heading'])}" + (f" — {pts}" if pts else ""))
    if not v["outline"]:
        lines.append("  (없음)")
    if v.get("product_need"):
        lines.append(f"- 찾을 제품 조건: {one(v['product_need'])}")
    if v["products"]:
        lines.append("- 제품 링크:")
        lines += [f"  - {one(p['name']) or '(이름 없음)'}: {p['url']}" for p in v["products"]]
    else:
        lines.append("- 제품 링크: 제품 링크 없음")
    lines.append("")
    return lines


# ---------- 주간 확정 목록 내보내기 ("📤 주간 확정 목록 내보내기" — 승인 화면의 최종 상태를 md 한 파일로) ----------
def _wf_cell(x) -> str:
    """표 칸 글자 정리 — "|"는 "/"로, 줄바꿈은 공백으로."""
    return ("" if x is None else str(x)).replace("|", "/").replace("\r", " ").replace("\n", " ").strip() or "-"


def _wf_row(cells) -> str:
    return "| " + " | ".join(_wf_cell(c) for c in cells) + " |"


def _build_weekly_final_md(week: str, now: datetime) -> str:
    """주간 확정 목록 마크다운 글자를 만들어 돌려줍니다. json은 읽기만 하고 어떤 것도 저장·호출하지 않습니다."""
    import target_keyword_finder as tkf
    week = v4_board.week_monday(week)
    state = {}  # 블로그 키 → {"ch_rows": [(채널, 항목, 글)], "reserve": [...]}
    for blog, ch, _label in TARGET_APPROVE_BLOGS:
        old = TARGET_OLD_CHANNELS[blog]
        platform = "naver" if blog == "naver" else "wordpress"
        approved, reserve, seen = [], [], set()
        for cid in (ch, old):
            queue, articles = _read_channel_data(cid)
            for it in queue:
                if it.get("week") != week or it.get("id") in seen:
                    continue
                if it.get("approved"):
                    seen.add(it.get("id"))
                    approved.append((cid, it, articles.get(it.get("id")) or {}))
                elif cid == ch:
                    reserve.append(it)
        state[blog] = {"platform": platform, "approved": approved, "reserve": reserve}

    naver_queue, naver_articles = _read_channel_data("naver_tips")

    def pair_cell(it: dict) -> str:
        ref = it.get("pair_ref")
        if not ref:
            return "-"
        nid = ref.get("id")
        if not nid:
            nid = next((q.get("id") for q in naver_queue
                        if q.get("week") == (ref.get("week") or it.get("week")) and q.get("keyword") == (ref.get("keyword") or it.get("keyword"))), None)
        st = _plan_post_state(naver_articles.get(nid) or {}, "naver", now) if nid else "찾을 수 없음"
        return f"{ref.get('keyword') or it.get('keyword', '')} ({ref.get('week') or '-'}, {st})"

    def comp_cell(it: dict) -> str:
        c = it.get("competition")
        return "직접 추가" if c == "직접추가" else (c or "-")

    lines = [
        f"[주간 확정 목록] 주차 {week} ({weekly_plan_export.week_range_label(week)})",
        f"만든 시각 {now:%Y-%m-%d %H:%M} · 앱 주간 승인 화면의 승인된 키워드 기준",
        "", "## 요약", "",
    ]
    for blog, _ch, label in TARGET_APPROVE_BLOGS:
        spec = tkf.BLOGS[blog]
        rows = state[blog]["approved"]
        counts = Counter(it.get("planned_type") for _, it, _ in rows)
        undecided = sum(1 for _, it, _ in rows if it.get("planned_type") not in spec["types"])
        type_text = " · ".join(f"{t} {counts.get(t, 0)}" for t in spec["types"])
        line = f"- {label}: 확정 {len(rows)} / 목표 {v4_board.weekly_plan(blog, week)} · {type_text} · 미정 {undecided} · 숍글 예정 {counts.get(spec['shop_type'], 0)}"
        if blog == "ssansegae":
            n_rev = sum(1 for _, it, _ in rows if tkf.is_review_item(it, blog, week))
            if v4_board.ssan_solve_active(week):  # 11/16 주부터: 리뷰 분석은 해결형 일반글
                line += f" (일반 해결 {counts.get('해결', 0) - n_rev} · 리뷰 분석 {n_rev} · 제품 추천 {counts.get('결정', 0)})"
            else:
                line += f" (제품 추천 {counts.get('결정', 0) - n_rev} · 리뷰 분석 {n_rev})"
        lines.append(line)
    lines.append("")
    wf_items = _overlap_items()
    schedule = weekly_plan_export.render_schedule_md(week, ("naver", "ssansegae"))
    lines += schedule or ["## ⓪ 발행 시간표", "", "시간표 없음(10/19 이전 주차)", ""]

    for blog, ch, label in TARGET_APPROVE_BLOGS:
        spec, st = tkf.BLOGS[blog], state[blog]
        prefix = "N" if blog == "naver" else "S"
        head = ["번호", "키워드", "유형", "카테고리", "숍글 예정", "월검색량", "경쟁도", "작년 정점", "경쟁", "검색 확인", "추천 이유", "글 상태"]
        head += ["글 id"] if blog == "naver" else ["짝 네이버 글", "각도 메모", "글 id"]
        head += ["기존 글"]  # 맨 끝에만 추가 (기획 채팅이 읽는 앞쪽 칸 순서·이름은 그대로)
        if blog == "ssansegae":
            head += ["형식"]  # 싼세계만: ③ 리뷰 분석 / 제품 추천(결정) / -(일반 해결형). 11/16 주부터 리뷰 분석의 유형 칸은 "해결"
        lines += [f"## 확정 키워드 — {label}", "",
                  "기존 글 = 앱이 단어로 찾은 같은 블로그 비슷한 글(🔴🟡 중복 위험 / 🔗 짝 글), 오탐 있음", "",
                  "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for n, (cid, it, art) in enumerate(st["approved"], 1):
            ptype = it.get("planned_type") or "미정"
            vol = it.get("total_search_volume")
            cells = [f"{prefix}{n}", it.get("keyword", ""), ptype, it.get("planned_category") or "미정",
                     "예" if ptype == spec["shop_type"] else "-", "-" if vol is None else vol, comp_cell(it), *_serp_cells(it),
                     it.get("rec_reason") or "-", _plan_post_state(art, st["platform"], now)]
            cells += [] if blog == "naver" else [pair_cell(it), it.get("angle_note") or "-"]
            ovl = _overlap_text(_overlap_find(wf_items, blog, it.get("keyword", ""), it.get("planned_type"), item_id=it.get("id"), channel=cid))
            tail = [it.get("id", ""), ovl]
            if blog == "ssansegae":
                tail.append("③ 리뷰 분석" if tkf.is_review_item(it, blog, week) else ("제품 추천" if ptype == "결정" else "-"))
            lines.append(_wf_row(cells + tail))
        if not st["approved"]:
            lines.append("| (없음) |" + " |" * (len(head) - 1))
        lines.append("")
        plan_lines = []  # plan 칸이 있는 확정 글만 (없으면 이 부분은 통째로 안 나옴)
        for n, (cid, it, art) in enumerate(st["approved"], 1):
            plan_lines += _plan_md_lines(it, f"{prefix}{n} {it.get('keyword', '')}")
        if plan_lines:
            lines += [f"### 글 계획 — {label}", ""] + plan_lines

    lines += ["## 예비 (이 주 화면에 있지만 승인 안 함)", ""]
    any_reserve = False
    for blog, ch, label in TARGET_APPROVE_BLOGS:
        res = state[blog]["reserve"]
        if not res:
            lines += [f"### {label}", "", "없음", ""]
            continue
        any_reserve = True
        lines += [f"### {label}", "", "| 키워드 | 블로그 | 유형 | 카테고리 | 월검색량 | 작년 정점 | 경쟁 | 검색 확인 |", "|---|---|---|---|---|---|---|---|"]
        for it in res:
            vol = it.get("total_search_volume")
            lines.append(_wf_row([it.get("keyword", ""), label, it.get("planned_type") or "미정",
                                  it.get("planned_category") or "미정", "-" if vol is None else vol, *_serp_cells(it)]))
        lines.append("")

    lines += ["## 부록: 블로그별 기존 글 목록 (내부 링크·중복 확인용)", ""]
    posts = _plan_blog_posts(now, _v4_categories(), _v4_aliases())
    for key, spec in posts.items():
        rows = sorted(spec["rows"], key=lambda r: r["dt"] or datetime.max, reverse=True)
        lines += [f"### {spec['title']}", "", "| 숍/일반 | 유형 | 키워드 | 제목 | 발행일 |", "|---|---|---|---|---|"]
        lines += [_wf_row(r["cells"]) for r in rows]
        lines += ["", f"- 기타 리뷰로 제외한 글: {spec['excluded']}개", ""]
    return "\n".join(lines) + "\n"


@app.route("/export-weekly-final", methods=["POST"])
def export_weekly_final():
    """"📤 주간 확정 목록 내보내기" — 지금 보는 주차의 승인된 키워드(네이버·싼세계)와 발행 시간표를 Claude outputs/weekly_final_<월요일>.md로
    저장(같은 주차면 덮어쓰기)하고 바로 내려받게 합니다. json은 읽기만 합니다."""
    raw_week = request.form.get("week") or engine.get_week_label()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_week):
        return "주차 형식이 올바르지 않습니다.", 400
    week = v4_board.week_monday(raw_week)
    text = _build_weekly_final_md(week, datetime.now())
    path = os.path.join(_EXPORT_DIR, f"weekly_final_{week}.md")
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return send_file(path, mimetype="text/markdown", as_attachment=True, download_name=os.path.basename(path))


SHOP_POST_CHANNELS = shop_judge.UNIFIED_CHANNEL_PLATFORM  # V5 [숍글] 체크를 쓰는 통합 채널 → 그 채널의 플랫폼 (naver_tips: naver, wp_tips: wordpress)


def _shop_post_state(article: dict, channel_id: str, raw: str) -> dict | None:
    """네이버(naver_tips)·싼세계(wp_tips) 글의 [숍글] 상태(shop_judge.shop_post_state와 같음). 다른 채널이면 None."""
    return shop_judge.shop_post_state(article, channel_id, raw)


def _is_shop_article(article: dict, channel_id: str, platform: str) -> bool:
    """목록·기획 자료에서 쓰는 숍글 판정. 현황판·겹침도 같은 판정(shop_judge.is_shop_article)을 씁니다.
    네이버·싼세계는 최종본 [숍글] 표시와 같은 기준(직접 지정 값 우선, 없으면 링크 자동 판정), 옛 숍 채널(OLD)은 항상 숍글, 그 밖은 일반."""
    return shop_judge.is_shop_article(article, channel_id, platform)


@app.route("/article/<article_id>/set-shop-post", methods=["POST"])
def set_shop_post(article_id):
    """최종본 화면의 [숍글] 체크 저장 — 이 글의 is_shop_post 값만 저장합니다(최종본 재생성·발행·재동기화는 하지 않음).
    mode: "shop"(숍글) / "general"(일반글) / "auto"(저장값 지움 → 자동 판정). 링크가 있는데 일반글로 바꾸려면 confirm_unlink=1이 필요합니다."""
    channel_id = get_active_channel_id()
    platform = SHOP_POST_CHANNELS.get(channel_id)
    if not platform or not find_queue_item(load_queue(), article_id):
        return redirect(url_for("edit_article", article_id=article_id))
    final_url = url_for("view_final", article_id=article_id, platform=platform)
    article = get_article(article_id)
    mode = request.form.get("mode", "")
    if mode not in ("shop", "general", "auto"):
        return redirect(final_url)
    state = _shop_post_state(article, channel_id, article["platform_raw"].get(platform) or "")
    if mode == "general" and state["auto"] and request.form.get("confirm_unlink") != "1":
        return redirect(url_for("view_final", article_id=article_id, platform=platform, shop_msg="need_confirm"))
    if mode == "auto":
        article.pop("is_shop_post", None)
    else:
        article["is_shop_post"] = (mode == "shop")
    set_article(article_id, article)
    return redirect(url_for("view_final", article_id=article_id, platform=platform, shop_msg="saved"))


# ---------- 싼세계 "외부 이미지 추가" (사용자가 고른 이미지를 지정한 소제목 섹션 끝에 넣기) ----------
# 네이버는 에디터에서 직접 넣으므로 붙이지 않습니다. 글 데이터의 새 칸 external_images는 사용자가 추가할 때만 생깁니다.
EXTERNAL_IMAGE_CHANNELS = ("wp_tips",)
EXTERNAL_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _external_images(article: dict) -> list[dict]:
    return [e for e in ((article or {}).get("external_images") or []) if isinstance(e, dict) and e.get("path")]


def _ext_sections(article: dict) -> list[dict]:
    """최종본을 만들 때와 같은 방식으로 나눈 싼세계 원고의 섹션들 (소제목 목록·못 찾음 검사용)."""
    raw = shop_formatter.split_tag_line(article["platform_raw"].get("wordpress") or "")[0]
    return shop_formatter.parse_sections(raw)["sections"]


def _save_external_image(article_id: str, file) -> str:
    """외부 이미지를 JPG로 저장하고 경로("/static/generated/…")를 돌려줍니다. 크기 줄이기·압축은 워드프레스 발행이 쓰는
    wp_publisher._compress_for_upload(품질 82, 가로 최대 1600px)를 그대로 재사용합니다. 투명 배경은 흰색으로 채웁니다.
    저장 위치: static/generated/<채널 image_folder>/외부이미지/<글 id>/ (발행 때 기존 이미지 업로드 경로가 /static/generated/ 아래를 찾음)."""
    img = ImageOps.exif_transpose(Image.open(file.stream))
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        img.save(tmp_path)
        data, _, mime_type = wp_publisher._compress_for_upload(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    if mime_type != "image/jpeg":
        raise ValueError("JPG 변환에 실패했습니다")
    channel_folder = get_channel_config().get("image_folder") or "기타"
    folder = os.path.join("static", "generated", channel_folder, "외부이미지", article_id)
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, f"ext_{uuid.uuid4().hex[:8]}.jpg")
    with open(filepath, "wb") as f:
        f.write(data)
    return "/" + filepath.replace(os.sep, "/")


def _ext_view(article: dict) -> list[dict]:
    """편집 화면용: 외부 이미지 목록 + 지금 원고에서 그 소제목을 찾을 수 있는지(found)."""
    heads = {shop_formatter._norm_heading(s["heading"]) for s in _ext_sections(article)}
    return [{**e, "found": shop_formatter._norm_heading(e.get("heading", "")) in heads} for e in _external_images(article)]


@app.route("/article/<article_id>/external-image/add", methods=["POST"])
def external_image_add(article_id):
    """싼세계 글에 외부 이미지 1장 추가 — 파일 + 넣을 소제목(원고의 ## 소제목 중) + 설명(alt, 비우면 소제목) + 출처 캡션(선택)."""
    edit_url = url_for("edit_article", article_id=article_id)
    if get_active_channel_id() not in EXTERNAL_IMAGE_CHANNELS or not find_queue_item(load_queue(), article_id):
        return redirect(edit_url)

    def back(msg):
        return redirect(url_for("edit_article", article_id=article_id, ext_msg=msg) + "#external-images")

    file = request.files.get("image")
    heading = request.form.get("heading", "").strip()
    if not file or not file.filename:
        return back("no_file")
    if os.path.splitext(file.filename)[1].lower() not in EXTERNAL_IMAGE_EXTS:
        return back("bad_type")
    article = get_article(article_id)
    sections = _ext_sections(article)
    match = next((s["heading"] for s in sections if shop_formatter._norm_heading(s["heading"]) == shop_formatter._norm_heading(heading)), None)
    if not heading or match is None:
        return back("no_heading")
    try:
        path = _save_external_image(article_id, file)
    except Exception:  # noqa: BLE001 — 이미지가 아니거나 깨진 파일
        return back("bad_image")
    entry = {
        "path": path,
        "alt": request.form.get("alt", "").strip() or shop_formatter.strip_markup(match),
        "caption": request.form.get("caption", "").strip(),
        "heading": match,
        "added_at": now_iso(),
    }
    article["external_images"] = _external_images(article) + [entry]
    set_article(article_id, article)
    return back("added")


@app.route("/article/<article_id>/external-image/delete", methods=["POST"])
def external_image_delete(article_id):
    """외부 이미지 1장을 목록에서 뺍니다(저장된 이미지 파일은 지우지 않음). 최종본은 '플랫폼별 콘텐츠 생성'을 다시 눌러야 반영됩니다."""
    if get_active_channel_id() not in EXTERNAL_IMAGE_CHANNELS or not find_queue_item(load_queue(), article_id):
        return redirect(url_for("edit_article", article_id=article_id))
    article = get_article(article_id)
    images = _external_images(article)
    try:
        i = int(request.form.get("index", ""))
    except ValueError:
        i = -1
    if 0 <= i < len(images):
        images.pop(i)
        if images:
            article["external_images"] = images
        else:
            article.pop("external_images", None)
        set_article(article_id, article)
    return redirect(url_for("edit_article", article_id=article_id, ext_msg="deleted") + "#external-images")


# ---------- 📎 출처 캡처 후보 (source_capture_v1) ----------
# 메모의 "출처 캡처:" 블록을 읽어 공공 사이트 화면을 캡처한 후보 이미지를 글 데이터의 새 칸 source_captures에만 둡니다.
# 후보 이미지는 최종본 HTML·복사·발행·재동기화·카드뉴스에 들어가지 않습니다(싼세계에서 사용자가 "넣기"한 것만 기존 external_images 경로로).
# 채널별 넣기 방식 표: "insert" = 소제목 아래 넣기(기존 외부 이미지 목록에 추가), "download" = 이미지 다운로드 + 캡션 복사.
# 이 표에 없는 채널(OLD 채널 등)에는 후보 칸을 보여 주지 않습니다. 생활레이더(radar_naver)는 나중에 "download"로 한 줄 추가.
SOURCE_CAPTURE_MODES = {"naver_tips": "download", "wp_tips": "insert", "longterm_wp": "download", "radar_naver": "download"}
SOURCE_CAPTURE_MSGS = {
    "done": "출처 캡처를 만들었어요. 결과는 아래 후보마다 확인하세요.",
    "no_block": "메모에 출처 캡처: 블록이 없어요.",
    "bad_index": "그 줄을 메모에서 찾지 못했어요. 메모를 확인해 주세요.",
    "inserted": "이 소제목 아래 넣기를 저장했어요. ③ 플랫폼별 콘텐츠 생성을 다시 눌러야 최종본에 반영돼요.",
    "no_heading": "넣을 소제목을 원고의 ## 소제목 중에서 골라 주세요.",
    "bad_item": "넣을 수 있는 후보가 아니에요(캡처 성공한 후보만 넣을 수 있어요).",
    "already": "이미 넣은 후보예요.",
    "inserted_skip": "이미 넣은 후보는 다시 캡처하지 않아요(넣은 이미지는 그대로예요).",
}


def _source_captures(article: dict) -> list[dict]:
    return [e for e in ((article or {}).get("source_captures") or []) if isinstance(e, dict)]


def _sc_channel_platform(channel_id: str) -> str:
    return CHANNELS[channel_id]["platforms"][0]


def _sc_remove_unused_files(old: list[dict], article: dict, keep: list[dict]) -> None:
    """바뀌어 쓰지 않게 된 후보 파일 지우기 — 출처캡처 폴더 안의 파일이면서 외부 이미지·남는 후보가 쓰지 않을 때만."""
    used = {e.get("path") for e in _external_images(article)} | {e.get("path") for e in keep}
    for e in old:
        p = e.get("path") or ""
        if p and p not in used and f"/{source_capture.SOURCE_CAPTURE_FOLDER_NAME}/" in p:
            try:
                os.remove(p.lstrip("/"))
            except OSError:
                pass


def _sc_box(article: dict, channel_id: str, platform: str) -> dict | None:
    """최종본 화면 맨 아래 후보 칸에 보여 줄 값. 표에 없는 채널이면 None(칸을 안 보여 줌)."""
    mode = SOURCE_CAPTURE_MODES.get(channel_id)
    if not mode or platform != _sc_channel_platform(channel_id):
        return None
    memo = source_capture.read_memo(article["platform_metadata"].get(platform) or "")
    headings: list[str] = []
    if mode == "insert":
        try:
            headings = [shop_formatter.strip_markup(s["heading"]) for s in _ext_sections(article)]
        except Exception:  # noqa: BLE001 — 원고가 비어 있거나 읽을 수 없으면 소제목 목록만 비움
            headings = []
    ext_paths = {e["path"] for e in _external_images(article)}
    norm = {shop_formatter._norm_heading(h): h for h in headings}
    items = []
    for i, e in enumerate(_source_captures(article)):
        path = e.get("path") or ""
        items.append({
            **e,
            "index": i,
            "status_label": source_capture.STATUS_LABELS.get(e.get("status"), e.get("status") or ""),
            "inserted": bool(path) and path in ext_paths,
            "file_ok": bool(path) and os.path.exists(path.lstrip("/")),
            "recommended": norm.get(shop_formatter._norm_heading(e.get("heading", "")), "") if e.get("heading") else "",
        })
    return {"mode": mode, "cands": items, "headings": headings, "has_block": memo["has_block"],
            "warnings": memo["warnings"], "memo_count": len(memo["items"]), "platform": platform}


def _sc_guard(article_id: str, platform: str):
    """후보 칸 라우트 공통 검사: 채널이 표에 있고, 글이 있고, 그 채널 플랫폼일 때만 통과."""
    ch = get_active_channel_id()
    if ch not in SOURCE_CAPTURE_MODES or platform != _sc_channel_platform(ch) or not find_queue_item(load_queue(), article_id):
        return False
    return True


@app.route("/article/<article_id>/source-capture/<platform>/run", methods=["POST"])
def source_capture_run(article_id, platform):
    """[출처 캡처 만들기] / 줄마다 [다시 캡처]. 메모의 출처 캡처: 블록을 읽어 캡처합니다(이미지 생성·발행·재동기화와 무관)."""
    if not _sc_guard(article_id, platform):
        return redirect(url_for("edit_article", article_id=article_id))

    def back(msg):
        return redirect(url_for("view_final", article_id=article_id, platform=platform, sc_msg=msg) + "#source-capture")

    article = get_article(article_id)
    memo = source_capture.read_memo(article["platform_metadata"].get(platform) or "")
    if not memo["has_block"] or not memo["items"]:
        return back("no_block")
    only = request.form.get("index", "").strip()
    items = memo["items"]
    idx = None
    if only != "":
        try:
            idx = int(only)
        except ValueError:
            idx = -1
        if not (0 <= idx < len(items)):
            return back("bad_index")
        items = [items[idx]]
    folder = CHANNELS[get_active_channel_id()].get("image_folder") or "기타"
    old = _source_captures(article)
    ext_paths = {e["path"] for e in _external_images(article)}

    def _same_line(o: dict | None, it: dict) -> bool:
        return bool(o) and o.get("url") == it["url"] and o.get("phrase") == it["phrase"]

    if idx is not None:
        o = old[idx] if idx < len(old) else None
        if o and (o.get("path") or "") in ext_paths and _same_line(o, items[0]):
            return back("inserted_skip")  # 이미 넣은 후보는 다시 캡처하지 않음(이미지 파일도 그대로)
    # 전체 다시 캡처: 같은 줄이고 이미 넣은 후보는 건너뜁니다(이미지 파일을 지우지 않음).
    skip_idx = set()
    if idx is None:
        for i, it in enumerate(items):
            o = old[i] if i < len(old) else None
            if o and (o.get("path") or "") in ext_paths and _same_line(o, it):
                skip_idx.add(i)
    todo = [it for i, it in enumerate(items) if i not in skip_idx]
    try:
        todo_records = source_capture.capture_candidates(todo, article_id, folder) if todo else []
    except Exception as e:  # noqa: BLE001 — 캡처가 어떻게 실패해도 화면은 멈추지 않음
        now = now_iso()
        todo_records = [{"heading": it["heading"], "url": it["url"], "phrase": it["phrase"], "caption": it["caption"], "inst": "",
                         "path": "", "status": "failed", "reason": f"캡처 중 오류: {e}"[:120], "captured_at": now} for it in todo]
    article = get_article(article_id)  # 캡처하는 동안 바뀐 내용을 덮어쓰지 않도록 다시 읽음
    old = _source_captures(article)
    if idx is None:
        new, k = [], 0
        for i, it in enumerate(items):
            o = old[i] if i < len(old) else None
            if i in skip_idx and o:
                new.append(o)
                continue
            r = todo_records[k]
            k += 1
            # 새 결과가 "실패"이고(허용 목록 밖·입력 오류는 제외) 같은 줄의 이전 결과가 성공이면 이전 성공 후보를 그대로 둡니다.
            if (r.get("status") == "failed" and o and o.get("status") == "ok" and _same_line(o, it)
                    and o.get("path") and os.path.exists(o["path"].lstrip("/"))):
                kept = dict(o)
                kept["retry_note"] = f"이번 다시 캡처는 실패({r.get('reason', '')}) — 이전 캡처 유지"
                new.append(kept)
            else:
                new.append(r)
    else:
        new = list(old)
        if idx < len(new):
            new[idx] = todo_records[0]
        else:
            new.append(todo_records[0])
    article["source_captures"] = new
    set_article(article_id, article)
    _sc_remove_unused_files(old, article, new)
    return back("done")


@app.route("/article/<article_id>/source-capture/<platform>/insert", methods=["POST"])
def source_capture_insert(article_id, platform):
    """싼세계: 캡처 후보를 고른 소제목 아래 외부 이미지(external_images)로 추가합니다. 최종본 반영은 ③ 플랫폼별 콘텐츠 생성을 다시 눌러서."""
    if not _sc_guard(article_id, platform) or SOURCE_CAPTURE_MODES.get(get_active_channel_id()) != "insert":
        return redirect(url_for("edit_article", article_id=article_id))

    def back(msg):
        return redirect(url_for("view_final", article_id=article_id, platform=platform, sc_msg=msg) + "#source-capture")

    article = get_article(article_id)
    caps = _source_captures(article)
    try:
        i = int(request.form.get("index", ""))
    except ValueError:
        i = -1
    if not (0 <= i < len(caps)):
        return back("bad_item")
    cap = caps[i]
    path = cap.get("path") or ""
    if cap.get("status") != "ok" or not path or not os.path.exists(path.lstrip("/")):
        return back("bad_item")
    if path in {e["path"] for e in _external_images(article)}:
        return back("already")
    heading = request.form.get("heading", "").strip()
    match = next((s["heading"] for s in _ext_sections(article)
                  if shop_formatter._norm_heading(s["heading"]) == shop_formatter._norm_heading(heading)), None)
    if not heading or match is None:
        return back("no_heading")
    entry = {
        "path": path,
        "alt": request.form.get("alt", "").strip() or shop_formatter.strip_markup(match),
        "caption": request.form.get("caption", "").strip() or cap.get("caption", ""),
        "heading": match,
        "added_at": now_iso(),
    }
    article["external_images"] = _external_images(article) + [entry]
    set_article(article_id, article)
    return back("inserted")


# ---------- 생활레이더 (radar_naver) ----------
# 새 네이버 블로그(속보·계산·실천·정리). 네이버용 변환 경로를 재사용하되 대가성·숍 링크·하단 추천은 끕니다.
# 글 데이터의 새 칸: post_type(글 유형 글자 그대로) · thread_card({summary_lines, card_title}) · radar_cards(본문 카드 목록).
# 표지는 기존 칸(covers["naver"]·cover_alts["naver"])을 그대로 씁니다.
RADAR_SUMMARY_HEADING_RE = re.compile(r"^##[ \t]+바쁜[ \t]*분을[ \t]*위한[ \t]*3줄[ \t]*요약[ \t]*$", re.MULTILINE)
RADAR_TABLE_MAX_COLS = 3  # 표가 이 칸 수를 넘으면 편집 화면에 노란 경고만(그대로 출력)
RADAR_MSGS = {
    "cover_done": "표지를 만들었어요(16:9, 1536×864, 카드 제목 얹음). 최종본에는 ③을 다시 눌러야 반영돼요.",
    "cover_fail": "표지를 만들지 못했어요(이미지 생성 오류). 기존 표지는 그대로예요. 잠시 뒤 다시 눌러 주세요.",
    "cover_title_fail": "표지 사진은 만들었지만 제목을 얹지 못했어요. [표지 제목만 다시 얹기 (0원)]를 눌러 주세요.",
    "retitle_done": "표지에 카드 제목을 다시 얹었어요(0원). 최종본에는 ③을 다시 눌러야 반영돼요.",
    "retitle_fail": "표지에 제목을 얹지 못했어요(사진 파일 오류). 기존 표지는 그대로예요.",
    "no_cover_photo": "글자 없는 표지 사진이 저장돼 있지 않아요(옛 1:1 표지예요). [표지만 다시 (약 20원)]로 새로 만들어 주세요.",
    "warn_title_fallback": "카드 제목이 없어 글 제목을 표지에 넣었어요.",
    "warn_no_title": "카드 제목도 글 제목도 없어 표지에 제목을 얹지 못했어요(사진만 표지로 저장).",
    "warn_title_overflow": "카드 제목이 길어 2줄에 다 들어가지 않아요(글자를 최소 크기까지 줄였어요). 메모의 카드 제목을 줄여 주세요.",
    "no_scene": "메모에 표지 장면: 줄이 없어서 표지를 만들지 않았어요(이미지 생성을 호출하지 않았어요).",
    "no_raw": "먼저 ①에서 원고를 붙여넣어 주세요.",
    "cards_done": "카드를 그렸어요. 최종본에는 ③을 다시 눌러야 반영돼요.",
    "no_cards": "메모에 카드: 블록이 없거나 쓸 수 있는 줄이 없어요.",
    # 본문 사진(radar_v2_20261012)
    "body_done": "본문 사진을 만들었어요. 최종본에는 ③을 다시 눌러야 반영돼요.",
    "body1_fail": "본문 사진 1을 만들지 못했어요(이미지 생성 오류). 기존 사진·데이터는 그대로예요. 잠시 뒤 다시 눌러 주세요.",
    "body2_fail": "본문 사진 2를 만들지 못했어요(이미지 생성 오류). 기존 사진·데이터는 그대로예요. 잠시 뒤 다시 눌러 주세요.",
    "body3_fail": "본문 사진 3을 만들지 못했어요(이미지 생성 오류). 기존 사진·데이터는 그대로예요. 잠시 뒤 다시 눌러 주세요.",
    # 네이버용 75% 사진(photo_v2_20261020) — radar169_nv75off_20261027에 기능이 꺼져 더 이상 안 쓰는 안내문(되돌리기용으로 남김)
    "nv75_done":"네이버용 75% 사진을 만들었어요(0원, API 호출 없음). 최종본 화면 미리보기와 [이미지 다운로드]에 적용돼요.",
    "no_body_photo": "본문 사진이 아직 없어요. 먼저 본문 사진을 만들어 주세요(네이버용 파일은 만들지 않았어요).",
    "no_nv75_file": "본문 사진 파일을 찾을 수 없어 네이버용 파일을 만들지 못했어요(정리 도구가 옮겼을 수 있어요). 기존 데이터는 그대로예요.",
    "no_body_scene": "메모에 본문 이미지 장면 줄(\"## 소제목\" → 장면)이 없어서 본문 사진을 만들지 않았어요(이미지 생성을 호출하지 않았어요).",
}
RADAR_BODY_MAX = 3  # 본문 사진 장수(메모의 앞 3줄만 사용)
RADAR_BODY_COST_NOTE = "약 45원"  # 본문 사진 3장(장당 약 15원, 가로형 16:9)
RADAR_ALL_COST_NOTE = "약 65원"  # 표지 1(약 20원) + 본문 3(약 45원)


def _radar_summary_lines(raw: str) -> list[str]:
    """원고의 "## 바쁜 분을 위한 3줄 요약" 섹션에서 1~3줄을 뽑습니다(목록 기호 제거)."""
    body = shop_formatter.split_tag_line(raw or "")[0]
    m = RADAR_SUMMARY_HEADING_RE.search(body)
    if not m:
        return []
    rest = body[m.end():]
    nxt = re.search(r"^##[ \t]+", rest, flags=re.MULTILINE)
    block = rest[:nxt.start()] if nxt else rest
    lines = []
    for ln in block.splitlines():
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).replace("**", "").strip()
        if s:
            lines.append(s)
    return lines[:3]


def _radar_sync(article: dict) -> None:
    """메모·원고에서 글 유형(post_type)과 쓰레드 카드 자리(thread_card)를 글 데이터에 저장합니다(카드뉴스를 만들지는 않음)."""
    p = "naver"
    memo_text = article["platform_metadata"].get(p) or ""
    raw = article["platform_raw"].get(p) or ""
    if not memo_text and not raw:
        return
    memo = metadata_parser.parse_radar_memo(memo_text)
    if memo["post_type"]:
        article["post_type"] = memo["post_type"]
    article["thread_card"] = {"summary_lines": _radar_summary_lines(raw), "card_title": memo["card_title"]}


def _radar_table_warnings(raw: str) -> list[str]:
    out, seen = [], set()
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 2 and not re.fullmatch(r"\|[\s:|-]+\|", s):
            cols = len(s.strip("|").split("|"))
            if cols > RADAR_TABLE_MAX_COLS and cols not in seen:
                seen.add(cols)
                out.append(f"표가 {cols}칸이에요(모바일에서는 {RADAR_TABLE_MAX_COLS}칸까지 권장). 그대로 출력돼요: {s[:40]}")
    return out


def _radar_info(article: dict) -> dict:
    """편집 화면 ② 이미지 칸용 값: 표지 장면·카드 정의·경고 목록."""
    p = "naver"
    memo_text = article["platform_metadata"].get(p) or ""
    rm = metadata_parser.parse_radar_memo(memo_text)
    pm = metadata_parser.parse_metadata_memo(memo_text)
    raw = article["platform_raw"].get(p) or ""
    warns = list(rm["warnings"]) + _radar_table_warnings(raw)
    for c in rm["cards"]:
        if c["error"]:
            warns.append(f"카드 '{c['title'] or c['kind']}': {c['error']}")
    cards = article.get("radar_cards") or []
    for c in cards:
        for w in c.get("warnings") or []:
            warns.append(f"카드 '{c.get('title', '')}': {w}")
    if raw and not radar_cards.find_base_date(raw):
        warns.append("원고 첫 줄에서 '📅 … 기준'을 못 찾았어요(카드 아래 기준일이 비어요).")
    if memo_text and not rm["card_title"] and (article["platform_title"].get(p) or "").strip():
        warns.append("카드 제목이 없어 글 제목을 표지에 넣었어요(메모에 \"카드 제목:\" 줄을 넣으면 그 제목이 표지에 들어가요).")
    # 본문 사진: 메모의 "## 소제목" → 장면 줄(앞 RADAR_BODY_MAX줄만 사용) + 이미 만든 사진
    body_pairs, body_extra = _radar_body_scenes(article)
    if len(body_pairs) < RADAR_BODY_MAX:
        warns.append(f"메모의 본문 이미지 장면이 {len(body_pairs)}줄이에요(\"## 소제목\" → 장면 형식 {RADAR_BODY_MAX}줄이 필요해요). 있는 만큼만 만들어요.")
    if body_extra:
        warns.append(f"메모의 본문 이미지 장면이 {len(body_pairs) + body_extra}줄이에요. 앞 {RADAR_BODY_MAX}줄만 쓰고 나머지는 무시해요.")
    raw_heads = {shop_formatter._norm_heading(h) for h in _radar_raw_headings(raw)}
    body_imgs = []
    for e in article.get("radar_body_images") or []:
        if not e.get("path"):
            continue
        h = (e.get("heading") or "").strip()
        body_imgs.append({
            **e, "place": h if h and shop_formatter._norm_heading(h) in raw_heads else "",
            "preview": e["path"],  # 옛 글에 path_nv75 기록이 있어도 읽지 않고 원본 사진을 씁니다
        })
    if len(body_imgs) < RADAR_BODY_MAX:
        warns.append(f"본문 사진 {len(body_imgs)}/{RADAR_BODY_MAX}장")
    msgs = []
    for code in (request.args.get("radar_msg", "") or "").split(","):
        text = RADAR_MSGS.get(code.strip(), "")
        if text:
            c = code.strip()
            msgs.append({"text": text, "warn": c.endswith("_fail") or c.startswith("no_") or c.startswith("warn_")})
    return {
        "post_type": article.get("post_type") or rm["post_type"], "scene": pm["cover_scene"], "cover": article["covers"].get(p) or "",
        "card_defs": rm["cards"], "cards": cards, "warnings": warns, "has_memo": bool(memo_text), "has_raw": bool(raw),
        "cost_note": image_gen.RADAR_COVER_COST_NOTE, "thread_card": article.get("thread_card") or {},
        "cover_photo": article.get("radar_cover_photo") or "", "cover_title": article.get("radar_cover_title") or "",
        "cover_photo_ok": bool(article.get("radar_cover_photo")) and os.path.exists((article.get("radar_cover_photo") or "").lstrip("/")),
        "msg": " ".join(m["text"] for m in msgs), "msgs": msgs,
        "body_pairs": [{"heading": h, "scene": s} for h, s in body_pairs], "body_images": body_imgs,
        "body_cost_note": RADAR_BODY_COST_NOTE, "all_cost_note": RADAR_ALL_COST_NOTE,
        "body_max": RADAR_BODY_MAX, "total_images": RADAR_BODY_MAX + 1,
    }


def _radar_body_scenes(article: dict) -> tuple[list[tuple[str, str]], int]:
    """메모의 "## 원고 소제목" → 장면 줄에서 앞 RADAR_BODY_MAX줄만 돌려줍니다(남는 줄 수도 함께). 새 파서 없이 기존 body_alt_pairs를 씁니다."""
    pm = metadata_parser.parse_metadata_memo(article["platform_metadata"].get("naver") or "")
    pairs = [(h, s) for h, s in pm.get("body_alt_pairs") or [] if (s or "").strip()]
    return pairs[:RADAR_BODY_MAX], max(0, len(pairs) - RADAR_BODY_MAX)


def _radar_raw_headings(raw: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^##[ \t]+(.+?)[ \t\r]*$", (raw or ""), flags=re.MULTILINE)]


def _radar_downloads(article: dict) -> list[dict]:
    """최종본 화면의 [이미지 다운로드] 목록: 표지(제목 있음) → 본문 사진들(원본) → 카드 → 표지 사진만(글자 없음, 쓰레드용, 있을 때만).
    복사·발행 본문과 무관한 화면용 값입니다."""
    out = []
    cover = article["covers"].get("naver") or ""
    if cover:
        label = "표지(제목 있음)" if article.get("radar_cover_title") else "표지"
        out.append({"label": label, "path": cover, "name": "생활레이더_표지.jpg", "ok": os.path.exists(cover.lstrip("/"))})
    for i, b in enumerate([b for b in (article.get("radar_body_images") or []) if b.get("path")], start=1):
        out.append({"label": f"본문 사진 {i}", "path": b["path"], "name": f"생활레이더_본문{i}.jpg", "ok": os.path.exists(b["path"].lstrip("/"))})
    for i, c in enumerate(article.get("radar_cards") or [], start=1):
        if c.get("path"):
            out.append({"label": f"카드 {i} · {c.get('title', '')}", "path": c["path"], "name": f"생활레이더_카드{i}.jpg",
                        "ok": os.path.exists(c["path"].lstrip("/"))})
    photo = article.get("radar_cover_photo") or ""
    if photo and photo != cover:  # 제목을 얹은 표지와 같은 파일이면(제목이 없던 경우) 중복으로 올리지 않음
        out.append({"label": "표지 사진만(글자 없음, 쓰레드용)", "path": photo, "name": "생활레이더_표지_사진만.jpg",
                    "ok": os.path.exists(photo.lstrip("/"))})
    return out


def _radar_insert_markers(raw: str, cards: list[dict], marker: str = "RADARCARD") -> tuple[str, list[tuple[int, dict]]]:
    """항목(카드·본문 사진)마다 들어갈 소제목 섹션 끝에 {{marker:n}} 줄을 넣습니다.
    소제목을 지정한 항목은 그 소제목 끝에, 지정이 없으면 "## 바쁜 분을 위한 3줄 요약" 다음 소제목들(FAQ·출처 제외)에 고르게."""
    if not cards or not raw:
        return raw, []
    lines = raw.replace("\r\n", "\n").split("\n")
    heads = [(i, m.group(1).strip()) for i, ln in enumerate(lines) if (m := re.match(r"^##[ \t]+(.+)$", ln))]
    if not heads:
        return raw, []
    norm = shop_formatter._norm_heading
    is_summary = [bool(RADAR_SUMMARY_HEADING_RE.match(lines[i])) for i, _ in heads]
    is_faq = [("FAQ" in h.upper() or "자주 묻는 질문" in h) for _, h in heads]
    is_src = [h.strip() == "출처" for _, h in heads] if marker != "RADARCARD" else [False] * len(heads)
    cand = [j for j in range(len(heads)) if not is_summary[j] and not is_faq[j] and not is_src[j]]
    if not cand:
        cand = [j for j in range(len(heads)) if is_summary[j]] or [len(heads) - 1]
    unassigned = [k for k, c in enumerate(cards) if not (c.get("heading") or "").strip()
                  or not any(norm(h) == norm(c["heading"]) for _, h in heads)]
    target: dict = {}
    for k, c in enumerate(cards):
        if k in unassigned:
            continue
        target[k] = next(j for j, (_, h) in enumerate(heads) if norm(h) == norm(c["heading"]))
    for pos, k in enumerate(unassigned):
        target[k] = cand[min(len(cand) - 1, int((pos + 0.5) * len(cand) / len(unassigned)))]

    def _section_end(j: int) -> int:
        """섹션 j의 마지막 내용 줄 바로 뒤 위치(다음 소제목·태그 줄·끝 앞의 빈 줄은 건너뜀)."""
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        if j + 1 >= len(heads):
            for i in range(len(lines) - 1, heads[j][0], -1):
                if lines[i].strip() and re.match(r"^\s*태그\s*[:：]", lines[i]):
                    end = i
                    break
                if lines[i].strip():
                    break
        while end - 1 > heads[j][0] and not lines[end - 1].strip():
            end -= 1
        return end

    inserts = []  # (줄 위치, 순서, 마커 번호)
    for k in range(len(cards)):
        inserts.append((_section_end(target[k]), k, k + 1))
    for pos, order, n in sorted(inserts, key=lambda x: (-x[0], -x[1])):
        lines[pos:pos] = ["", "{{%s:%d}}" % (marker, n), ""]
    return "\n".join(lines), [(k + 1, c) for k, c in enumerate(cards)]


def _radar_insert_card_markers(raw: str, cards: list[dict]) -> tuple[str, list[tuple[int, dict]]]:
    """카드 자리 표시({{RADARCARD:n}}) 넣기 — 기존 동작 그대로(공통 로직은 _radar_insert_markers)."""
    return _radar_insert_markers(raw, cards, "RADARCARD")


RADAR_SOURCE_HEADING_RE = re.compile(r"^##[ \t]+출처$")
_RADAR_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _radar_split_sources(raw: str) -> tuple[str, list[dict]]:
    """원고의 "## 출처" 소제목(앞뒤 공백 무시)과 그 아래 연속된 "- " 목록 줄을 빼고, 빼낸 줄을 [{text, url}]로 돌려줍니다.
    목록 줄이 하나도 없거나 "## 출처"가 없으면 원고를 그대로 돌려줍니다. 그 아래 문장(댓글 질문·마무리)은 그대로 남깁니다."""
    if not raw or "출처" not in raw:
        return raw, []
    lines = raw.split("\n")
    for i, ln in enumerate(lines):
        if not RADAR_SOURCE_HEADING_RE.match(ln.strip()):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():  # 소제목과 목록 사이의 빈 줄은 허용
            j += 1
        k = j
        items = []
        while k < len(lines) and lines[k].strip().startswith("- "):
            items.append(lines[k].strip()[2:].strip())
            k += 1
        if not items:
            return raw, []
        # 목록 뒤 빈 줄 하나를 같이 지워 빈 줄이 겹치지 않게 함(앞 줄도 빈 줄일 때만)
        end = k
        if end < len(lines) and not lines[end].strip() and (i == 0 or not lines[i - 1].strip()):
            end += 1
        out = lines[:i] + lines[end:]
        sources = []
        for t in items:
            m = _RADAR_URL_RE.search(t)
            sources.append({"text": t, "url": m.group(0).rstrip(".,;") if m else ""})
        return "\n".join(out), sources
    return raw, []


def _radar_insert_photo_markers(raw: str, photos: list[dict], seed: str = "") -> tuple[str, list[tuple[int, dict]]]:
    """본문 사진마다 {{RADARPHOTO:n}} 줄을 원고에 넣습니다(photo_v2_20261020). 사진의 소제목(heading)이 원고 소제목과 맞으면 그 섹션에, 안 맞으면
    빈 섹션들에 글 id 시드로 퍼뜨리고, 섹션 안 자리는 ①소제목 바로 아래 ②첫 문단 뒤 ③섹션 끝을 사진마다 다르게 배정합니다.
    넣지 않는 섹션: "바쁜 분을 위한 3줄 요약"·"출처"·FAQ·본문이 빈 섹션. 카드({{RADARCARD}})는 따로 _radar_insert_card_markers가 섹션 끝에 넣습니다."""
    if not photos or not raw:
        return raw, []
    lines = raw.replace("\r\n", "\n").split("\n")
    heads = [(i, m.group(1).strip()) for i, ln in enumerate(lines) if (m := re.match(r"^##[ \t]+(.+)$", ln))]
    if not heads:
        return raw, []

    def _section_end(j: int) -> int:
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        if j + 1 >= len(heads):
            for i in range(len(lines) - 1, heads[j][0], -1):
                if lines[i].strip() and re.match(r"^\s*태그\s*[:：]", lines[i]):
                    end = i
                    break
                if lines[i].strip():
                    break
        while end - 1 > heads[j][0] and not lines[end - 1].strip():
            end -= 1
        return end

    def _chunks(j: int) -> list[tuple[int, int]]:
        """섹션 j 본문의 블록(빈 줄로 나뉜 덩어리)의 (시작 줄, 끝 줄 다음) 목록."""
        out, i, end = [], heads[j][0] + 1, _section_end(j)
        while i < end:
            if not lines[i].strip():
                i += 1
                continue
            s = i
            while i < end and lines[i].strip():
                i += 1
            out.append((s, i))
        return out

    chunks = [_chunks(j) for j in range(len(heads))]
    is_summary = [bool(RADAR_SUMMARY_HEADING_RE.match(lines[i])) for i, _ in heads]
    is_skip = [
        is_summary[j] or ("FAQ" in h.upper() or "자주 묻는 질문" in h) or h.strip() == "출처" or not chunks[j]
        for j, (_, h) in enumerate(heads)
    ]
    eligible = [not x for x in is_skip]
    if not any(eligible):
        eligible = [not x for x in is_summary] if not all(is_summary) else [True] * len(heads)
    units = []
    for ch in chunks:
        lead = 0
        for s, e in ch:
            lead += 1
            if not (e - s == 1 and re.match(r"^#{3,4}[ \t]+", lines[s])):
                break
        units.append((max(1, lead), len(ch)))
    plan = shop_formatter.assign_body_image_slots(
        [h for _, h in heads], eligible, units, [(p.get("heading") or "") for p in photos], seed
    )
    inserts = []  # (줄 위치, 순서, 마커 번호)
    for j, slots in plan.items():
        for slot, idxs in slots.items():
            if slot == "top":
                pos = heads[j][0] + 1
            elif slot == "after_first" and chunks[j]:
                pos = chunks[j][min(units[j][0], len(chunks[j])) - 1][1]
            else:
                pos = _section_end(j)
            for k in idxs:
                inserts.append((pos, k, k + 1))
    if not inserts:
        return raw, []
    for pos, order, n in sorted(inserts, key=lambda x: (-x[0], -x[1])):
        lines[pos:pos] = ["", "{{RADARPHOTO:%d}}" % n, ""]
    return "\n".join(lines), [(k + 1, p) for k, p in enumerate(photos)]


def _radar_build_final(article: dict, raw: str, title: str, cover_img_tag: str, seed: str = "") -> str:
    """생활레이더 최종본(네이버용). disclosure=False · shop_links=None · related_posts=None · strip_link_placeholders=True.
    "## 출처" 소제목+목록은 본문(복사 영역)에서 빼고 article["radar_sources"]에 저장합니다(최종본 화면 아래 칸에서 따로 보여 줌).
    seed(글 id): 본문 사진 위치(섹션·자리) 배정 시드 — 같은 글은 다시 만들어도 같은 위치."""
    raw, sources = _radar_split_sources(raw)
    article["radar_sources"] = sources
    photos = [b for b in (article.get("radar_body_images") or []) if b.get("path")]
    cards = [c for c in (article.get("radar_cards") or []) if c.get("path")]
    raw1, pmarkers = _radar_insert_photo_markers(raw, photos, seed)  # 사진 먼저 → 같은 소제목 끝에서 카드는 사진 다음
    raw2, markers = _radar_insert_card_markers(raw1, cards)
    final = shop_formatter.build_shop_html(
        title=title, cover_img_tag=cover_img_tag, raw=raw2, product_images=[], platform="naver",
        bottom_images=None, related_posts=None, disclosure=False, body_images=None,
        shop_links=None, strip_link_placeholders=True,
    )
    for tag, mk in (("RADARPHOTO", pmarkers), ("RADARCARD", markers)):
        for n, c in mk:
            img = (
                f'<img src="{html.escape(c["path"], quote=True)}" alt="{html.escape(c.get("alt") or c.get("title") or "", quote=True)}" '
                f'style="width:100%; max-width:720px; border-radius:12px; display:block; margin:16px auto;"><br>'
            )
            final = re.sub(r"<p[^>]*>\s*\{\{%s:%d\}\}\s*</p>" % (tag, n), lambda _m, _img=img: _img, final)
    return re.sub(r"<p[^>]*>\s*\{\{RADAR(?:CARD|PHOTO):\d+\}\}\s*</p>", "", final)  # 안전장치: 남은 표시는 지움


def _radar_guard(article_id: str) -> bool:
    return get_active_channel_id() in RADAR_CHANNELS and bool(find_queue_item(load_queue(), article_id))


def _radar_card_title(article: dict) -> tuple[str, list[str]]:
    """표지에 얹을 제목: 메모의 "카드 제목:" → 비어 있으면 글 제목(platform_title.naver). (제목, 안내 코드들)을 돌려줍니다."""
    rm = metadata_parser.parse_radar_memo(article["platform_metadata"].get("naver") or "")
    title = (rm["card_title"] or "").strip()
    if title:
        return title, []
    fallback = (article["platform_title"].get("naver") or "").strip()
    return (fallback, ["warn_title_fallback"]) if fallback else ("", ["warn_no_title"])


def _radar_apply_cover(article_id: str, cover_path: str | None, photo_path: str, title: str, alt: str = "") -> list[str]:
    """표지 저장(생성 중 바뀐 내용을 덮어쓰지 않게 다시 읽음). covers.naver = 제목 얹은 표지(지금처럼),
    radar_cover_photo = 글자 없는 사진, radar_cover_title = 얹은 글자. cover_path가 None이면 covers.naver·제목은 그대로 둡니다.
    쓰지 않게 된 옛 파일 후보 경로(표지·사진)를 돌려줍니다."""
    fresh = get_article(article_id)
    old = [fresh["covers"].get("naver") or "", fresh.get("radar_cover_photo") or ""]
    if cover_path is not None:
        fresh["covers"]["naver"] = cover_path
        fresh["radar_cover_title"] = title
        if alt:
            fresh["cover_alts"]["naver"] = alt
    fresh["radar_cover_photo"] = photo_path
    fresh["platform_final"]["naver"] = fresh["platform_final"].get("naver", "")  # 최종본은 ③을 다시 눌러야 반영
    set_article(article_id, fresh)
    return old


def _radar_remove_cover_files(article_id: str, old_paths: list[str], keep: list[str]) -> None:
    """이 라우트가 만든 파일 이름 규칙({글id}_radar_cover_…)의 옛 파일만 지웁니다. 옛 1:1 표지({글id}_radar_cover.jpg)는 그대로 둡니다."""
    prefix = f"{article_id}_radar_cover_"
    for pth in old_paths:
        if not pth or pth in keep or not os.path.basename(pth).startswith(prefix):
            continue
        try:
            os.remove(pth.lstrip("/"))
        except OSError:
            pass


def _radar_make_cover(article_id: str, scene: str, topic: str, alt: str) -> list[str]:
    """표지 만들기 공통: 16:9 사진 생성(API) → 글자 없는 사진 저장 → 카드 제목 얹은 표지 저장(0원). 안내 코드들을 돌려줍니다."""
    try:
        photo = image_gen.generate_radar_cover(scene, topic, article_id)
    except Exception:  # noqa: BLE001 — API 오류 등: 데이터는 그대로 두고 안내만
        return ["cover_fail"]
    title, codes = _radar_card_title(get_article(article_id))
    if not title:
        old = _radar_apply_cover(article_id, photo, photo, "", alt)  # 얹을 제목이 없으면 사진을 그대로 표지로
        _radar_remove_cover_files(article_id, old, [photo])
        return ["cover_done"] + codes
    try:
        res = image_gen.compose_radar_cover_title(photo, title, article_id)
    except Exception:  # noqa: BLE001 — 사진은 이미 만들었으니 저장해 두고, 제목만 다시 얹게 안내
        _radar_apply_cover(article_id, None, photo, "")
        return ["cover_title_fail"]
    old = _radar_apply_cover(article_id, res["path"], photo, title, alt)
    _radar_remove_cover_files(article_id, old, [res["path"], photo])
    if len(res["lines"]) > image_gen.RADAR_TITLE_MAX_LINES:
        codes.append("warn_title_overflow")
    return ["cover_done"] + codes


# ── N데스크피커 ② 사진 화면(새 방식): 원고의 [사진: 번호]·[상품카드: 상품명]·[표지] 를 읽어 보여 주고, 고른 결과를 글 사진 폴더의 photos.json 에 저장 ──
# 계산·복사는 radar_screen.py 가 하고, 여기는 화면 값 전달과 버튼 길만 이어 줍니다(0원, 이미지 생성 API 안 부름, 대기열·장부 json 에는 안 씀).
def _radar_desk_context(article_id: str):
    """(글, 대기열 항목, 사진 폴더) — radar 채널이 아니거나 글이 없으면 None."""
    if get_active_channel_id() not in RADAR_CHANNELS:
        return None
    item = find_queue_item(load_queue(), article_id)
    if not item:
        return None
    article = get_article(article_id)
    title = next((t for t in article["platform_title"].values() if t), None) or item["keyword"]
    cfg = get_channel_config()
    folder = cfg.get("image_folder") or "생활레이더"
    pdir = radar_screen.photo_dir(folder, image_gen.slugify_for_folder(title))
    return article, item, pdir


def _radar_related_for(article_id: str, item: dict):
    """"함께 보면 좋은 글" 두 개 고르기(읽기만). 반환 (뽑은 목록, 메모 목록). 기준 날짜는 config.RADAR_RELATED_SINCE."""
    queue, articles = _read_channel_data("radar_naver")
    return radar_screen.pick_related(
        article_id, radar_screen.plan_genre(item), queue, articles, getattr(config, "RADAR_RELATED_SINCE", "2026-11-23")
    )


def _radar_desk_view(article_id: str):
    ctx = _radar_desk_context(article_id)
    if not ctx:
        return None
    article, item, pdir = ctx
    radar_screen.auto_table_images(article["platform_raw"].get("naver") or "", article["platform_metadata"].get("naver") or "", pdir)  # 표 칸에 그림이 없으면 자동으로 만듦(글 사진 폴더에만 씀)
    radar_screen.auto_badge_images(article["platform_raw"].get("naver") or "", pdir)  # ### 아래 별점 띠 그림 만들기(글 사진 폴더에만 씀)
    return radar_screen.build_screen(article["platform_raw"].get("naver") or "", article["platform_metadata"].get("naver") or "", item, pdir)


def _radar_upload_view(article_id: str, final_html: str):
    """③ 화면의 업로드 폴더 목록(읽기만). 글을 못 찾으면 None."""
    ctx = _radar_desk_context(article_id)
    return radar_screen.upload_listing(ctx[2], final_html) if ctx else None


def _radar_desk_back(article_id: str, ok: bool, msg: str):
    return redirect(url_for("edit_article", article_id=article_id, desk_msg=("" if ok else "!") + msg) + "#radar-images")


@app.route("/radar/file")
def radar_desk_file():
    """② 화면 미리보기용: 후보 사진 폴더·글 사진 폴더 안의 그림 파일만 보내 줍니다(그 밖의 경로는 404)."""
    p = request.args.get("p", "")
    if get_active_channel_id() not in RADAR_CHANNELS or not radar_screen.is_allowed_file(p):
        return ("", 404)
    return send_file(os.path.abspath(p))


@app.route("/article/<article_id>/radar/open-upload-folder", methods=["POST"])
def radar_open_upload_folder(article_id):
    """③ 화면 [업로드 폴더 열기]: 윈도우 탐색기로 글 사진 폴더 안 naver_upload 를 연다(폴더 위치는 글에서 계산, 화면 입력은 안 씀)."""
    ctx = _radar_desk_context(article_id)
    if not ctx:
        return redirect(url_for("view_final", article_id=article_id, platform="naver"))
    target = os.path.abspath(os.path.join(ctx[2], radar_screen.UPLOAD_DIR))
    if not os.path.isdir(target):
        msg = "업로드 폴더가 아직 없어요. ③ 최종본 만들기를 먼저 눌러 주세요"
    elif not hasattr(os, "startfile"):
        msg = "이 컴퓨터에서는 폴더를 열 수 없어요: " + target
    else:
        try:
            os.startfile(target)
            msg = ""
        except OSError as e:
            msg = "폴더를 열지 못했어요: " + str(e)
    return redirect(url_for("view_final", article_id=article_id, platform="naver", upload_msg=msg))


@app.route("/article/<article_id>/radar/desk/cover", methods=["POST"])
def radar_desk_cover(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx:
        return redirect(url_for("index"))
    article, item, pdir = ctx
    ok, msg = radar_screen.make_cover_file(article["platform_raw"].get("naver") or "", article["platform_metadata"].get("naver") or "", item, pdir)
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/coverpick", methods=["POST"])
def radar_desk_coverpick(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx:
        return redirect(url_for("index"))
    ok, msg = radar_screen.choose_cover_photo(ctx[2], ctx[1], request.form.get("src", ""))
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/slot", methods=["POST"])
def radar_desk_slot(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx or not (request.form.get("slot") or "").isdigit():
        return redirect(url_for("index"))
    ok, msg = radar_screen.choose_slot(ctx[2], int(request.form["slot"]), request.form.get("src", ""))
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/skip", methods=["POST"])
def radar_desk_skip(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx or not (request.form.get("slot") or "").isdigit():
        return redirect(url_for("index"))
    ok, msg = radar_screen.set_slot_skip(ctx[2], int(request.form["slot"]), request.form.get("skip") == "1")
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/table", methods=["POST"])
def radar_desk_table(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx or not (request.form.get("slot") or "").isdigit():
        return redirect(url_for("index"))
    article, _item, pdir = ctx
    ok, msg = radar_screen.make_table_image_file(article["platform_raw"].get("naver") or "", pdir, int(request.form["slot"]))
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/gif", methods=["POST"])
def radar_desk_gif(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx:
        return redirect(url_for("index"))
    article, _item, pdir = ctx
    ok, msg = radar_screen.copy_gif(pdir, request.form.get("name", ""), article["platform_raw"].get("naver") or "", article["platform_metadata"].get("naver") or "")
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/gifskip", methods=["POST"])
def radar_desk_gifskip(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx or not request.form.get("name"):
        return redirect(url_for("index"))
    ok, msg = radar_screen.set_gif_skip(ctx[2], request.form["name"], request.form.get("skip") == "1")
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/signskip", methods=["POST"])
def radar_desk_signskip(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx:
        return redirect(url_for("index"))
    ok, msg = radar_screen.set_sign_skip(ctx[2], request.form.get("skip") == "1")
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/gifmake", methods=["POST"])
def radar_desk_gifmake(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx:
        return redirect(url_for("index"))
    article, _item, pdir = ctx
    ok, msg = radar_screen.make_product_gif(pdir, request.form.get("name", ""), article["platform_raw"].get("naver") or "", article["platform_metadata"].get("naver") or "")
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/desk/product", methods=["POST"])
def radar_desk_product(article_id):
    ctx = _radar_desk_context(article_id)
    if not ctx:
        return redirect(url_for("index"))
    ok, msg = radar_screen.pick_product(ctx[2], request.form.get("name", ""), request.form.get("product_id", ""))
    return _radar_desk_back(article_id, ok, msg)


@app.route("/article/<article_id>/radar/cover", methods=["POST"])
def radar_make_cover(article_id):
    """[표지만 다시] — 메모의 표지 장면: 으로 16:9 사진 1장(약 20원) + 카드 제목 얹기(0원). 장면이 없으면 API를 부르지 않고 안내만 합니다."""
    if not _radar_guard(article_id):
        return redirect(url_for("edit_article", article_id=article_id))

    def back(msg):
        return redirect(url_for("edit_article", article_id=article_id, radar_msg=msg) + "#radar-images")

    article = get_article(article_id)
    pm = metadata_parser.parse_metadata_memo(article["platform_metadata"].get("naver") or "")
    scene = (pm.get("cover_scene") or "").strip()
    if not scene:
        return back("no_scene")
    item = find_queue_item(load_queue(), article_id)
    topic = article["platform_title"].get("naver") or (item or {}).get("keyword") or ""
    _apply_image_output_context(article_id)
    return back(",".join(_radar_make_cover(article_id, scene, topic, pm.get("cover_alt") or topic or "표지")))


@app.route("/article/<article_id>/radar/retitle", methods=["POST"])
def radar_retitle_cover(article_id):
    """[표지 제목만 다시 얹기 (0원)] — 저장된 글자 없는 사진에 지금 메모의 카드 제목을 다시 얹습니다. API 호출 없음."""
    if not _radar_guard(article_id):
        return redirect(url_for("edit_article", article_id=article_id))

    def back(msg):
        return redirect(url_for("edit_article", article_id=article_id, radar_msg=msg) + "#radar-images")

    article = get_article(article_id)
    photo = article.get("radar_cover_photo") or ""
    if not photo or not os.path.exists(photo.lstrip("/")):
        return back("no_cover_photo")
    title, codes = _radar_card_title(article)
    if not title:
        return back("warn_no_title")
    _apply_image_output_context(article_id)
    try:
        res = image_gen.compose_radar_cover_title(photo, title, article_id)
    except Exception:  # noqa: BLE001
        return back("retitle_fail")
    old = _radar_apply_cover(article_id, res["path"], photo, title)
    _radar_remove_cover_files(article_id, old, [res["path"], photo])
    if len(res["lines"]) > image_gen.RADAR_TITLE_MAX_LINES:
        codes.append("warn_title_overflow")
    return back(",".join(["retitle_done"] + codes))


@app.route("/article/<article_id>/radar/cards", methods=["POST"])
def radar_make_cards(article_id):
    """[카드 그리기] — 메모 카드: 블록(최대 2줄)을 Pillow로 1080×1080 JPG 카드로 그립니다(0원)."""
    if not _radar_guard(article_id):
        return redirect(url_for("edit_article", article_id=article_id))

    def back(msg):
        return redirect(url_for("edit_article", article_id=article_id, radar_msg=msg) + "#radar-images")

    article = get_article(article_id)
    raw = article["platform_raw"].get("naver") or ""
    if not raw:
        return back("no_raw")
    rm = metadata_parser.parse_radar_memo(article["platform_metadata"].get("naver") or "")
    defs = [c for c in rm["cards"] if not c["error"]]
    if not defs:
        return back("no_cards")
    date_str = radar_cards.find_base_date(raw)
    out_dir = _apply_image_output_context(article_id)
    entries = []
    for i, c in enumerate(defs, start=1):
        path = os.path.join(out_dir, f"{article_id}_radar_card_{i}_{uuid.uuid4().hex[:8]}.jpg")
        res = radar_cards.render_card(c, date_str, path)
        entries.append({
            "path": "/" + path.replace(os.sep, "/"), "kind": c["kind"], "title": c["title"], "alt": c["title"],
            "heading": c.get("heading", ""), "source": c.get("source", ""), "warnings": res["warnings"],
            "drawn_at": now_iso(),
        })
    article = get_article(article_id)
    old = article.get("radar_cards") or []
    article["radar_cards"] = entries
    set_article(article_id, article)
    keep = {e["path"] for e in entries}
    for e in old:  # 바뀌어 쓰지 않는 옛 카드 파일만 지움(이 라우트가 만든 파일 이름 규칙의 파일만)
        pth = e.get("path") or ""
        if pth and pth not in keep and "_radar_card_" in pth:
            try:
                os.remove(pth.lstrip("/"))
            except OSError:
                pass
    return back("cards_done")


def _radar_make_body_images(article_id: str, article: dict, topic: str) -> tuple[list[dict] | None, list[str]]:
    """메모의 본문 이미지 장면(앞 RADAR_BODY_MAX줄)으로 본문 사진을 만듭니다. (새 목록 | None, 안내 코드들)을 돌려줍니다.
    장면이 없으면 None(이미지 생성을 호출하지 않음). 한 장이 실패하면 그 자리는 이미 있던 사진을 그대로 둡니다.
    사진마다 글 id 시드로 배정한 구도(BODY_SHOT_ANGLES)를 프롬프트에 붙입니다. 네이버용 75% 여백 파일(path_nv75)은 더 이상 만들지 않습니다."""
    pairs, _extra = _radar_body_scenes(article)
    if not pairs:
        return None, ["no_body_scene"]
    old = [e for e in (article.get("radar_body_images") or [])]
    angles = image_gen.assign_body_shot_angles(len(pairs), article_id)
    slots, codes, made = [], [], 0
    for i, (heading, scene) in enumerate(pairs, start=1):
        try:
            path = image_gen.generate_radar_body_image(scene, topic, article_id, i, angle=angles[i - 1])
        except Exception:  # noqa: BLE001 — API 오류 등: 그 장만 실패 안내, 기존 사진은 그대로
            codes.append(f"body{i}_fail")
            slots.append(old[i - 1] if i - 1 < len(old) and old[i - 1].get("path") else None)
            continue
        made += 1
        slots.append({
            "path": path, "alt": scene, "heading": heading, "scene": scene, "made_at": now_iso(),
            "angle": angles[i - 1]["key"],
        })
    if made:
        codes.insert(0, "body_done")
    return [e for e in slots if e], codes


def _radar_apply_body_images(article_id: str, entries: list[dict]) -> list[str]:
    """새 본문 사진 목록을 저장합니다(생성 중 바뀐 내용을 덮어쓰지 않게 다시 읽음). 쓰지 않게 된 옛 _radar_body_ 파일 경로를 돌려줍니다."""
    article = get_article(article_id)
    old = article.get("radar_body_images") or []
    article["radar_body_images"] = entries
    set_article(article_id, article)
    keep = {e["path"] for e in entries} | {e.get("path_nv75") for e in entries if e.get("path_nv75")}
    return [
        pth for e in old for pth in (e.get("path"), e.get("path_nv75"))
        if pth and pth not in keep and "_radar_body_" in pth  # 바뀌어 쓰지 않는 옛 본문 사진·네이버용 파일만
    ]


def _radar_remove_files(paths: list[str]) -> None:
    for pth in paths:  # 이 라우트가 만든 파일 이름 규칙(_radar_body_)의 옛 파일만 지움
        try:
            os.remove(pth.lstrip("/"))
        except OSError:
            pass


def _make_nv75_safe(path: str) -> str:
    """원본 본문 사진(웹 경로)으로 "네이버용 75% 여백 파일"을 만들어 웹 경로를 돌려줍니다. 원본이 없거나 실패하면 ""(오류를 내지 않음). 0원."""
    if not path or not os.path.exists(path.lstrip("/")):
        return ""
    try:
        return image_gen.make_naver_body_image(path)
    except Exception:  # noqa: BLE001 — 합성 실패는 조용히 "없음" 처리(원본 사진은 그대로 쓰임)
        return ""


def _remove_nv75_files(paths: list[str], keep: set | None = None) -> None:
    """바뀌어 쓰지 않는 옛 네이버용 파일("_nv75"가 이름에 든 것)만 지웁니다. 원본 사진은 절대 지우지 않습니다."""
    for pth in paths:
        if not pth or (keep and pth in keep) or "_nv75" not in os.path.basename(pth):
            continue
        try:
            os.remove(pth.lstrip("/"))
        except OSError:
            pass


def _nv75_swap_map(article: dict) -> dict:
    """{원본 본문 사진 경로: 네이버용 파일 경로} — 파일이 실제로 있는 것만. 네이버용 파일이 없는 옛 글은 빈 값이라 원본 그대로 쓰입니다."""
    m = {}
    for e in article.get("radar_body_images") or []:
        if e.get("path") and e.get("path_nv75"):
            m[e["path"]] = e["path_nv75"]
    nv = article.get("platform_body_images_nv75") or {}
    for p, orig in (article.get("platform_body_images") or {}).items():
        for a, b in zip(orig, nv.get(p) or []):
            if a and b:
                m[a] = b
    return {a: b for a, b in m.items() if os.path.exists(b.lstrip("/"))}


def _nv75_preview(content: str, article: dict) -> str:
    """최종본 화면 미리보기용: 본문 사진의 주소를 네이버용 75% 파일로 바꿔 보여 줍니다(저장된 최종본은 그대로, 화면에만 적용)."""
    for a, b in _nv75_swap_map(article).items():
        content = content.replace(f'src="{a}"', f'src="{b}"').replace(f'src="{html.escape(a, quote=True)}"', f'src="{html.escape(b, quote=True)}"')
    return content


def _naver_body_info(article: dict) -> dict:
    """네이버(naver_tips) 편집 화면 ② 칸의 본문 사진 목록(미리보기는 원본 사진 — 옛 글에 platform_body_images_nv75 기록이 있어도 읽지 않음)."""
    paths = article["platform_body_images"].get("naver") or []
    photos = [{"path": pth, "preview": pth, "ok": os.path.exists(pth.lstrip("/"))} for pth in paths]
    return {"photos": photos, "msg": ""}


def _naver_body_downloads(article: dict) -> list[dict]:
    """네이버(naver_tips) 최종본 화면의 [이미지 다운로드] 목록: 본문 사진(원본)."""
    out = []
    for i, ph in enumerate(_naver_body_info(article)["photos"], start=1):
        out.append({"label": f"본문 사진 {i}", "path": ph["path"], "name": f"네이버_본문{i}.jpg", "ok": ph["ok"]})
    return out


NV75_MSGS = {
    "done": "네이버용 75% 사진을 만들었어요(0원, API 호출 없음). 최종본 화면 미리보기와 [이미지 다운로드]에 적용돼요.",
    "none": "본문 사진이 아직 없어요. 먼저 ②에서 이미지를 만들어 주세요(네이버용 파일은 만들지 않았어요).",
    "nofile": "본문 사진 파일을 찾을 수 없어 네이버용 파일을 만들지 못했어요(정리 도구가 옮겼을 수 있어요). 기존 데이터는 그대로예요.",
}


@app.route("/article/<article_id>/radar/body", methods=["POST"])
def radar_make_body(article_id):
    """[본문 사진만 다시] — 메모의 본문 이미지 장면 앞 RADAR_BODY_MAX줄로 16:9 사진 최대 RADAR_BODY_MAX장(장당 약 15원)."""
    if not _radar_guard(article_id):
        return redirect(url_for("edit_article", article_id=article_id))

    article = get_article(article_id)
    item = find_queue_item(load_queue(), article_id)
    topic = article["platform_title"].get("naver") or (item or {}).get("keyword") or ""
    _apply_image_output_context(article_id)
    entries, codes = _radar_make_body_images(article_id, article, topic)
    if entries is not None:
        _radar_remove_files(_radar_apply_body_images(article_id, entries))
    return redirect(url_for("edit_article", article_id=article_id, radar_msg=",".join(codes)) + "#radar-images")


@app.route("/article/<article_id>/radar/images", methods=["POST"])
def radar_make_images(article_id):
    """[이미지 4장 만들기] — 표지 1 + 본문 RADAR_BODY_MAX장(모두 16:9)을 한 번에(약 65원). 장면이 없는 쪽은 건너뛰고 안내만 합니다."""
    if not _radar_guard(article_id):
        return redirect(url_for("edit_article", article_id=article_id))

    article = get_article(article_id)
    pm = metadata_parser.parse_metadata_memo(article["platform_metadata"].get("naver") or "")
    item = find_queue_item(load_queue(), article_id)
    topic = article["platform_title"].get("naver") or (item or {}).get("keyword") or ""
    _apply_image_output_context(article_id)
    codes: list[str] = []

    scene = (pm.get("cover_scene") or "").strip()
    if not scene:
        codes.append("no_scene")
    else:
        codes.extend(_radar_make_cover(article_id, scene, topic, pm.get("cover_alt") or topic or "표지"))

    entries, body_codes = _radar_make_body_images(article_id, get_article(article_id), topic)
    codes.extend(body_codes)
    if entries is not None:
        _radar_remove_files(_radar_apply_body_images(article_id, entries))
    return redirect(url_for("edit_article", article_id=article_id, radar_msg=",".join(codes)) + "#radar-images")


@app.route("/article/<article_id>/generate-final", methods=["POST"])
def generate_final(article_id):
    if get_active_channel_id() in NAVER_AUTOFILL_CHANNELS:
        # 네이버 일반·SHOP(OLD): 추천 글을 고르기 전에 공개된 글의 주소를 한 번 채움 (아래에서 글을 읽기 전에 해야 이 글의 주소 변경이 덮어써지지 않음)
        autofill_warn = _naver_autofill_before_final()
        if autofill_warn:
            session["naver_autofill_warn"] = autofill_warn
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    article = get_article(article_id)
    keyword = item["keyword"] if item else ""

    # 네이버 일반(꿀팁)는 자기 플랫폼(네이버)만 최종본을 만듭니다 — 옛 데이터에 워드프레스 원고가 남아 있어도
    # 워드프레스용 최종본(쿠팡 파트너스 대가성 문구 포함)이 눈에 안 보이게 만들어지지 않도록.
    # 워드프레스 일반도 같은 방식으로 자기 플랫폼(워드프레스)만 만듭니다.
    final_platforms = get_active_platforms() if get_active_channel_id() in MANUAL_KEYWORD_CHANNELS else PLATFORMS
    radar_final_fail = ""  # N데스크피커: 최종본을 못 만든 이유(조용히 넘어가지 않고 화면에 알림)
    for p in final_platforms:
        raw = article["platform_raw"].get(p, "")
        title = article["platform_title"].get(p, "")
        if not raw or not title:
            if get_active_channel_id() in RADAR_CHANNELS and p == "naver":
                radar_final_fail = "원고 또는 제목이 비어 있어요"
            continue

        cover_path = article["covers"].get(p, "")
        cover_alt = article["cover_alts"].get(p) or "표지"
        cover_img_tag = f'<img class="seo-cover-img" src="{cover_path}" alt="{cover_alt}">' if cover_path else ""

        if get_active_channel_id() in RADAR_CHANNELS:
            # 생활레이더: 네이버용 변환 경로를 재사용하되 대가성·숍 링크·하단 추천은 전부 끔 (기존 채널 분기와 섞이지 않는 새 가지)
            # N데스크피커 새 방식: 사진 폴더 photos.json(표지·사진 칸·GIF)과 원고 표시를 읽어 최종본을 만듭니다(radar_screen.build_final_html).
            _raw_body, _sources = _radar_split_sources(raw)
            article["radar_sources"] = _sources
            _rctx = _radar_desk_context(article_id)
            if not _rctx:
                radar_final_fail = "대기열에서 이 글을 찾지 못해 사진 폴더 정보를 얻지 못했어요" if not item else "사진 폴더 정보를 얻지 못했어요"
                continue
            _related, _ = _radar_related_for(article_id, _rctx[1])
            article["platform_final"][p], _final_info = radar_screen.build_final_html(_raw_body, title, _rctx[2], related=_related)
            if _final_info.get("upload_error"):
                session["radar_upload_err"] = "업로드 폴더(naver_upload)를 만들지 못했어요: " + _final_info["upload_error"]
            else:
                session.pop("radar_upload_err", None)
            _radar_sync(article)
            continue

        platform_images = article["platform_body_images"].get(p) or article["body_images"]
        platform_image_alts = article["platform_body_image_alts"].get(p) or article["body_image_alts"]

        is_naver_tips = get_active_channel_id() == "naver_tips"
        is_wp_tips = get_active_channel_id() == "wp_tips"
        # 워드프레스 일반: 원고 어디에든 쿠팡 링크 줄("링크: https://link.coupang.com/…")이 있으면 쿠팡 대가성 문구를
        # 넣고, 그 링크가 있는 섹션(과 도입부)에만 구매 버튼을 만듭니다. 없으면 네이버 일반처럼 순수 정보 글입니다.
        wp_tips_has_coupang = is_wp_tips and shop_formatter.has_coupang_link(raw)
        # V5: 네이버·싼세계는 글마다 [숍글] 여부(링크가 들어간 글)로 갈립니다 — 값이 없으면 링크 유무로 자동 판정.
        shop_state = _shop_post_state(article, get_active_channel_id(), raw)
        is_shop_post = bool(shop_state and shop_state["effective"])
        if get_channel_config().get("queue_builder") in ("shopping", "naver_tips", "wp_tips"):
            # 숍(쿠팡파트너스·쇼핑커넥트) 채널은 정책 콘텐츠와 완전히 다른 레이아웃을 씁니다 —
            # 목차 대신 법정 고지문구 + 상품별 이미지·가격·구매 버튼이 한 덩어리인 카드
            # 형태입니다. FAQ는 지원하되("## 자주 묻는 질문" + Q:/A: 형식), 정책 콘텐츠와
            # 달리 목차는 안 씁니다. 이미지는 "②-1 이미지 풀"에서 "사용하기" 체크된 것만,
            # 상품 섹션 순서대로 매칭해서 씁니다 (한 상품에 여러 장도 가능).
            # 네이버 일반(꿀팁)는 상품·구매링크가 없는 순수 정보 글이라, 같은 shop_formatter
            # 네이버 레이아웃(소제목·인용구·목록·표·강조·양쪽정렬·추천태그)만 재사용하고
            # 상품 카드·대가성 문구는 자연히 안 나갑니다(원고에 "가격:"/"링크:" 줄 자체가 없음).
            parsed_sections = shop_formatter.parse_sections(raw)["sections"]
            product_images = []
            bottom_images = None
            if get_active_channel_id() == "shop_wp":
                # 워드프레스 SHOP: 소제목/상품별 매칭 없이, "본문 이미지" 풀의 이미지 전부를 글 맨 아래에
                # 업로드 순서 그대로 Gutenberg 이미지 블록으로 붙입니다 (0장이어도 됨).
                bottom_images = [{"path": e["path"], "alt": e["alt"]} for e in article["shop_image_pool"]["body"]]
            else:
                slot = 0
                for section in parsed_sections:
                    if section["is_product"]:
                        pool_slot = article["shop_image_pool"]["products"].get(str(slot), [])
                        selected = [{"path": e["path"], "alt": e["alt"]} for e in pool_slot if e.get("selected")]
                        product_images.append(selected)
                        slot += 1

            # 구조화 데이터(Product + FAQPage)는 본문에 안 섞고 따로 저장합니다 — 정책
            # 콘텐츠와 같은 방식입니다 (붙여넣기 시 <script>가 브라우저 보안으로 사라지는
            # 문제 방지). 워드프레스·티스토리만 API로 직접 넣을 수 있어서 적용합니다.
            if p in ("wordpress", "tistory"):
                # 워드프레스 일반은 정보 글이라 FAQ 스키마만 씁니다 (Product 없음).
                product_schema = "" if (is_wp_tips and not is_shop_post) else shop_formatter.build_product_schema(parsed_sections)
                faq_schema = shop_formatter.build_faq_schema(parsed_sections)
                article["platform_schema"][p] = "\n".join(s for s in (product_schema, faq_schema) if s)

            selected_cover = next((e for e in article["shop_image_pool"]["cover"] if e.get("selected")), None)
            if selected_cover:
                cover_img_tag = (
                    f'<img class="seo-cover-img" src="{selected_cover["path"]}" '
                    f'alt="{selected_cover.get("alt") or "표지"}">'
                )
            # 표지 풀에서 고른 게 없으면, 기존 방식대로 ②번에서 자동 생성해둔 표지를 그대로 씁니다.

            body_images_param = None
            if is_ai_info_channel() and platform_images:
                # 일반(정보글) 채널: AI 본문 이미지를 소제목(섹션) 사이에 고르게 넣습니다.
                _heads = (article.get("platform_body_image_headings") or {}).get(p) or []
                body_images_param = [
                    {
                        "path": path, "alt": platform_image_alts[i] if i < len(platform_image_alts) else "",
                        "heading": _heads[i] if i < len(_heads) else "",
                    }
                    for i, path in enumerate(platform_images)
                ]
            if is_naver_tips:
                related_posts = _naver_tips_related_posts(article_id, article, keyword, shop_formatter.NAVER_RELATED_MAX)
            elif is_wp_tips:
                # 워드프레스 "관련 글"은 발행할 때 wp_publisher가 카테고리 기반 블록으로 붙입니다(워드프레스 SHOP과 같음).
                related_posts = None
            else:
                related_posts = (
                    _shop_related_naver_posts(queue, load_articles(), article_id, article, shop_formatter.NAVER_RELATED_MAX)
                    if p == "naver"
                    else None
                )
            build_raw = raw
            if is_wp_tips and is_shop_post:
                build_raw = shop_formatter.SHOP_LINE_RE.sub("", raw)  # 숍글에는 {{SHOP:}} 짝 글 연결 줄을 쓰지 않음(남은 줄은 지움)
            final = shop_formatter.build_shop_html(
                title=title,
                cover_img_tag=cover_img_tag,
                raw=build_raw,
                product_images=product_images,
                platform=p,
                bottom_images=bottom_images,
                related_posts=related_posts,
                # 네이버·싼세계(V5): [숍글]이면 대가성 문구를 넣고, 일반글이면 넣지 않습니다(값이 없으면 링크 유무로 자동 판정).
                # 옛 숍 채널(OLD)은 예전 그대로 항상 넣습니다.
                disclosure=is_shop_post if (is_naver_tips or is_wp_tips) else True,
                # 네이버·싼세계만: 도입부를 짧은 문단으로 나눔(화면·복사 출력에만 — 원고·저장 데이터는 그대로). 옛 숍 채널·생활레이더는 안 함.
                split_intro=bool(is_naver_tips or is_wp_tips),
                # 일반글만: {{SHOP:}} 짝 글 연결. 숍글은 연결하지 않습니다(네이버 숍글은 아래 strip으로 {{SHOP:}} 줄이 지워집니다).
                shop_links=_tips_shop_links(raw, get_active_channel_id(), article_id) if ((is_naver_tips or is_wp_tips) and not is_shop_post) else None,
                body_images=body_images_param,
                body_image_seed=article_id,  # 본문 사진 위치(섹션·자리) 시드: 같은 글은 다시 만들어도 같은 위치
                # 싼세계 일반글만: 쿠팡 링크로 시작하는 섹션만 상품(구매 버튼). 숍글은 "링크:" 섹션 전부가 상품(워드프레스 SHOP 방식)입니다.
                product_link_prefix=shop_formatter.COUPANG_LINK_PREFIX if (is_wp_tips and not is_shop_post) else None,
                # 워드프레스 SHOP(OLD)과 싼세계 숍글: 상품 끝 구매 버튼 대신 "배너 + 쿠팡 HTML 코드" (그 밖에는 None = 예전 그대로)
                wp_cta_banner_url=config.SHOP_WP_CTA_BANNER_URL if (get_active_channel_id() == "shop_wp" or (is_wp_tips and is_shop_post)) else None,
                # 네이버 SHOP(OLD)과 네이버 숍글: 링크는 에디터에서 직접 넣으므로 {{SHOP:}} 줄·빈 [구매링크] 버튼을 출력에서 뺍니다.
                strip_link_placeholders=(get_active_channel_id() == "shop_naver" or (is_naver_tips and is_shop_post)),
                # 싼세계만: 사용자가 추가한 외부 이미지를 지정한 소제목 섹션 끝에 넣음 (없으면 None = 예전 그대로)
                external_images=(_external_images(article) or None) if (is_wp_tips and p == "wordpress") else None,
            )
            article["platform_final"][p] = final
            continue

        # 혜택주머니만(대표·세부 공통): 도입부(첫 ## 앞, "최종 확인" 줄 제외)를 짧은 문단으로 나눔 — 화면·복사 출력에만, 원고·저장 데이터는 그대로.
        # 생활레이더·네이버·싼세계는 위에서 따로 처리되고, 이 줄은 지나지 않습니다.
        is_lt_intro = get_active_channel_id() == "longterm_wp"
        html_raw, lt_has_confirm = seo_formatter.split_intro_raw(raw) if is_lt_intro else (raw, False)
        html = seo_formatter.apply_seo_formatting(html_raw, platform=p)
        if is_lt_intro:
            html = seo_formatter.style_intro_paragraphs(html, skip_first=lt_has_confirm)
        # 롱텀만: 사진별 소제목 정보(platform_body_image_headings)가 저장돼 있으면 그 소제목 밑에 붙입니다. 없으면(옛 글) 예전 배치.
        platform_image_headings = None
        if get_active_channel_id() == "longterm_wp" and article["platform_body_images"].get(p):
            platform_image_headings = (article.get("platform_body_image_headings") or {}).get(p) or None
        html = _insert_images_into_html(html, platform_images, platform_image_alts, platform_image_headings, seed=article_id)

        if p in ("wordpress", "tistory"):
            toc_html, html = seo_formatter.build_toc(html)
            faq_schema = seo_formatter.build_faq_schema(raw)
            article_schema = seo_formatter.build_article_schema(title, keyword)
            # 스키마(구조화 데이터)는 본문에 섞지 않고 따로 저장합니다.
            # <script> 태그를 본문 HTML 안에 그대로 넣으면, 붙여넣기 할 때 브라우저가 보안상
            # 걸러내서 사라지거나, 화면에 스크립트로 인식돼 아예 안 보이는 문제가 있었습니다.
            article["platform_schema"][p] = "\n".join(s for s in (article_schema, faq_schema) if s)
            final = f"<h1>{title}</h1>{cover_img_tag}{toc_html}{html}"
        else:  # naver
            tags = seo_formatter.extract_naver_tags(raw, keyword)
            tags_html = (
                '<div style="background:#f5f5f5; border-radius:8px; padding:14px 18px; margin-top:20px; font-size:14px; color:#2563eb;">'
                '<strong style="color:#111;">추천 태그(하단 태그 입력란에 붙여넣으세요):</strong> '
                + " ".join(f"#{t}" for t in tags)
                + "</div>"
                if tags
                else ""
            )
            final = f"<h1>{title}</h1>{cover_img_tag}{html}{tags_html}"

        article["platform_final"][p] = final

    # 옛 링크 자동 교체(link_replacements.json): 이번에 새로 만든 최종본에만 적용합니다. 바뀐 내용은 메모리에만 두고 최종본 화면에 안내합니다.
    link_table = link_check.load_replacements()
    for p in final_platforms:
        built = article["platform_final"].get(p) or ""
        # 원고·제목이 있어 이번에 실제로 다시 만든 플랫폼만 (결과가 이전과 같아도 교체는 적용)
        if built and article["platform_raw"].get(p) and article["platform_title"].get(p):
            built, changes = link_check.apply_replacements(built, link_table)
            article["platform_final"][p] = built
            _LINK_REPLACED[(get_active_channel_id(), article_id, p)] = changes

    set_article(article_id, article)
    if get_active_channel_id() in RADAR_CHANNELS:
        if radar_final_fail:
            session["radar_final_err"] = "최종본을 만들지 못했습니다: " + radar_final_fail
            return redirect(url_for("edit_article", article_id=article_id, desk_msg="!최종본을 만들지 못했습니다: " + radar_final_fail) + "#radar-images")
        session.pop("radar_final_err", None)
    return redirect(url_for("edit_article", article_id=article_id))


# 최종본을 만들 때 교체표로 바꾼 링크 {(채널, 글 id, 플랫폼): [{"before","after","note"}]} — 메모리에만 둡니다 (앱을 다시 켜면 사라짐).
_LINK_REPLACED: dict = {}


@app.route("/article/<article_id>/link-check/<platform>")
def link_check_result(article_id, platform):
    """최종본 화면의 "링크 검사" 칸이 부르는 곳. 글 속 외부 링크를 열어 보고 결과만 돌려줍니다 (아무것도 저장하지 않음)."""
    article = get_article(article_id)
    content = shop_formatter.strip_shop_markers(article["platform_final"].get(platform) or "")
    return jsonify(link_check.inspect_html(content))


# ---------- 수동 홍보 작업 예약 체크리스트 (인스타/쓰레드) ----------
# (네이버 지식인 자동화는 계정 정지 위험이 커서 로컬앱에서 완전히 제외했습니다 — 손으로 직접 하는 게 안전합니다.)

MANUAL_PROMO_CHANNELS = {"instagram": "인스타그램", "threads": "쓰레드"}


@app.route("/article/<article_id>/manual-promo/save", methods=["POST"])
def save_manual_promo(article_id):
    """
    인스타/쓰레드처럼 수동으로 진행하는 홍보 작업의 '예정 시각'을 저장합니다.
    워드프레스 예약 시각과 맞추고 싶을 때 쓰는 용도라, 자동으로 뭔가 실행하지는 않고
    대시보드 '오늘 할 일'에 뜨게 하는 메모입니다.
    """
    article = get_article(article_id)
    for ch in MANUAL_PROMO_CHANNELS:
        val = request.form.get(f"scheduled_{ch}", "").strip()
        article["manual_promo"][ch]["scheduled_at"] = val
    set_article(article_id, article)
    return redirect(request.referrer or (url_for("edit_article", article_id=article_id) + "#manual-promo"))


@app.route("/article/<article_id>/manual-promo/toggle-done/<channel>", methods=["POST"])
def toggle_manual_promo_done(article_id, channel):
    article = get_article(article_id)
    new_state = False
    if channel in article.get("manual_promo", {}):
        article["manual_promo"][channel]["done"] = not article["manual_promo"][channel]["done"]
        new_state = article["manual_promo"][channel]["done"]
        set_article(article_id, article)

    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": True, "done": new_state}
    return redirect(request.referrer or (url_for("edit_article", article_id=article_id) + "#manual-promo"))


def get_todays_manual_tasks() -> list[dict]:
    """오늘 날짜이거나 이미 지난 날짜인데 아직 '완료' 체크가 안 된 수동 홍보 작업을 모두 모읍니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    articles = load_articles()
    queue = load_queue()
    tasks = []
    for article_id, data in articles.items():
        item = find_queue_item(queue, article_id)
        keyword = item["keyword"] if item else article_id
        for ch, info in (data.get("manual_promo") or {}).items():
            scheduled = (info or {}).get("scheduled_at", "")
            if not scheduled or (info or {}).get("done"):
                continue
            scheduled_date = scheduled[:10]
            if scheduled_date <= today:
                tasks.append(
                    {
                        "article_id": article_id,
                        "keyword": keyword,
                        "channel": ch,
                        "channel_label": MANUAL_PROMO_CHANNELS.get(ch, ch),
                        "scheduled_at": scheduled,
                        "overdue": scheduled_date < today,
                    }
                )
    tasks.sort(key=lambda t: t["scheduled_at"])
    return tasks


STALE_ARTICLE_MONTHS = 6  # 이 개월 수보다 오래된 글을 재검토 대상으로 알려줍니다


def get_stale_articles() -> list[dict]:
    """
    발행된 지 오래된(기본 6개월) 글을 찾아서 재검토 대상으로 모읍니다. 정부지원금·세금·복지
    정보는 매년 금액·조건이 바뀌는 경우가 많아서, 오래된 글을 그대로 방치하면 오래된 정보를
    보여주게 될 뿐 아니라 "방치된 콘텐츠"라는 신호도 됩니다. 주기적으로 다시 열어서 최신
    수치로 갱신하고 재동기화하면(최종 업데이트 날짜도 자동으로 갱신됨) 관리되는 콘텐츠라는
    신호를 계속 유지할 수 있습니다.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    threshold = (datetime.now() - timedelta(days=STALE_ARTICLE_MONTHS * 30)).strftime("%Y-%m-%d")
    articles = load_articles()
    queue = load_queue()
    stale = []
    for article_id, data in articles.items():
        item = find_queue_item(queue, article_id)
        keyword = item["keyword"] if item else article_id
        for p in PLATFORMS:
            pub_date = (data.get("platform_published_at") or {}).get(p)
            if not pub_date or pub_date > today:
                continue  # 아직 발행 전이거나 예약 중(미래)인 건 대상 아님
            if pub_date > threshold:
                continue  # 아직 기준 기간만큼 안 지남
            stale.append(
                {
                    "article_id": article_id,
                    "keyword": keyword,
                    "platform": p,
                    "platform_label": PLATFORM_LABELS[p],
                    "published_at": pub_date,
                }
            )
    stale.sort(key=lambda x: x["published_at"])
    return stale


@app.route("/article/<article_id>/set-published-date/<platform>", methods=["POST"])
def set_published_date(article_id, platform):
    """
    발행 여부를 체크박스가 아니라 '날짜'로 직접 관리합니다.
    - 빈 값: 미발행
    - 오늘보다 미래 날짜: 예약됨 (예약발행 걸어두고 그 날 다시 들어올 필요 없음)
    - 오늘이거나 과거 날짜: 발행완료
    같이 입력받은 실제 발행 주소(URL)는 같은 클러스터 내 다른 글의 {{LINK:키워드}} 자리표시자를
    자동으로 치환할 때 사용됩니다.
    """
    date_value = request.form.get("published_date", "").strip()
    url_value = request.form.get("published_url", "").strip()
    article = get_article(article_id)
    if platform in PLATFORMS:
        # N데스크피커(radar_naver) 네이버 글 주소: 바뀔 때만 형식 검사(이미 저장된 주소는 그대로 둠). 맞으면 정해진 모양으로 맞춰 저장.
        if get_active_channel_id() in RADAR_CHANNELS and platform == "naver":
            stored_url = (article["platform_published_url"].get(platform) or "").strip()
            stored_date = article["platform_published_at"].get(platform)
            if url_value != stored_url:
                fixed, err = radar_screen.normalize_post_url(url_value)
                if err and not (not url_value and not date_value):  # 날짜도 주소도 비우는 것(발행 표시 취소)만 허용
                    session["radar_pub_err"] = "발행 표시를 저장하지 않았어요 — " + err
                    return redirect(request.referrer or url_for("edit_article", article_id=article_id))
                url_value = fixed
            elif date_value and not url_value and not stored_date:
                session["radar_pub_err"] = "발행 표시를 저장하지 않았어요 — 글 주소가 비어 있어요. 발행한 글의 주소(https://blog.naver.com/guardradar/글번호)를 넣어 주세요."
                return redirect(request.referrer or url_for("edit_article", article_id=article_id))
        article["platform_published_at"][platform] = date_value or None
        article["platform_published_url"][platform] = url_value or None
    set_article(article_id, article)
    return redirect(request.referrer or url_for("edit_article", article_id=article_id))


def platform_status(article: dict, platform: str) -> dict:
    """이 플랫폼의 상태를 하나로 판단합니다: 미생성 / 생성만 완료 / 예약됨 / 발행완료."""
    date_str = article["platform_published_at"].get(platform)
    if date_str:
        today = datetime.now().strftime("%Y-%m-%d")
        if date_str > today:
            return {"level": "scheduled", "status": f"예약됨 ({date_str})", "date": date_str}
        return {"level": "published", "status": "발행완료", "date": date_str}
    if article["platform_raw"].get(platform):
        return {"level": "generated", "status": "생성만 완료", "date": article["platform_generated_at"].get(platform)}
    return {"level": "empty", "status": "미생성", "date": None}


def _meta_description_seo_check(memo_text: str) -> dict:
    """
    저장된 메타데이터 메모에서 메타 디스크립션을 뽑아, 구글 검색결과 스니펫 기준으로 길이가
    적정한지 확인합니다 (구글은 보통 155자 안팎에서 자르고, 너무 짧으면 본문에서 다른 문장을
    임의로 골라 쓰는 경향이 있습니다). 화면에 바로 보여줄 수 있는 상태값을 반환합니다.
    """
    desc = metadata_parser.parse_metadata_memo(memo_text).get("meta_description", "")
    length = len(desc)
    if not desc:
        return {"length": 0, "status": "none", "note": "메타 디스크립션이 아직 없습니다."}
    if length < 70:
        return {"length": length, "status": "short", "note": f"{length}자 — 다소 짧습니다. 너무 짧으면 구글이 본문에서 다른 문장을 대신 가져다 쓸 수 있어요 (권장 120~155자)."}
    if length <= 155:
        return {"length": length, "status": "good", "note": f"{length}자 — 적정 범위입니다 (권장 120~155자)."}
    return {"length": length, "status": "long", "note": f"{length}자 — 다소 깁니다. 구글이 뒷부분을 잘라버릴 수 있어요 (권장 120~155자 이하)."}


RAW_CHECK_CHANNELS = ("naver_tips", "wp_tips")  # 원고 점검 표시 대상(싼세계·네이버) — 다른 채널은 분량 기준이 다릅니다
RAW_CHECK_TARGET_CHARS = 3000
RAW_CHECK_RED_CHARS = 2200
_RAW_CHECK_SKIP_LINE_RE = re.compile(r"^[ \t]*(?:\[플랫폼[ \t]*[:：]|제목[ \t]*[:：]|태그[ \t]*[:：]|링크[ \t]*[:：])")


def _raw_check(raw: str) -> dict:
    """
    붙여넣은 원고의 분량(공백 제외 글자 수)과 FAQ 나뉨을 점검합니다 — 화면 표시용이라 저장·발행은 막지 않습니다.
    글자 수 범위: [플랫폼:…]·제목:·태그:·링크: 줄을 뺀 본문 전체(FAQ 포함).
    level: ok / warn(노랑) / bad(빨강).
    """
    raw = raw or ""
    body_lines = [l for l in shop_formatter.collapse_link_html(raw).splitlines() if not _RAW_CHECK_SKIP_LINE_RE.match(l)]
    chars = len(re.sub(r"\s+", "", "\n".join(body_lines)))
    if chars < RAW_CHECK_RED_CHARS:
        chars_level = "bad"
    elif chars < RAW_CHECK_TARGET_CHARS:
        chars_level = "warn"
    else:
        chars_level = "ok"

    # FAQ: "## 자주 묻는 질문" 섹션 안의 Q: 줄 수 vs 앱이 나눈 쌍 수
    q_count = 0
    parts = re.split(r"^##[ \t]+(.+)$", shop_formatter.normalize_raw_headings(shop_formatter.collapse_link_html(raw)), flags=re.MULTILINE)
    has_faq = False
    for i in range(1, len(parts), 2):
        if shop_formatter._is_faq_heading(parts[i].strip()):
            has_faq = True
            body = parts[i + 1] if i + 1 < len(parts) else ""
            q_count += sum(1 for l in body.splitlines() if shop_formatter._Q_LINE_RE.match(l))
    pair_count = sum(
        len(s.get("qa_pairs") or []) for s in shop_formatter.parse_sections(raw)["sections"] if s.get("is_faq")
    )
    if not has_faq:
        faq_level = "warn"
    elif q_count != pair_count:
        faq_level = "bad"
    else:
        faq_level = "ok"
    return {
        "chars": chars, "chars_level": chars_level, "short_by": max(0, RAW_CHECK_TARGET_CHARS - chars),
        "has_faq": has_faq, "q_count": q_count, "pair_count": pair_count, "faq_level": faq_level,
    }


def _infer_wordpress_post_id(article: dict) -> int | None:
    """
    저장된 워드프레스 게시물 ID가 없어도, 저장된 발행 주소에 워드프레스 기본 고유주소
    형태인 '?p=123' 같은 숫자가 들어있으면 거기서 직접 뽑아냅니다. (게시물 ID 저장이
    어떤 이유로 누락됐어도, 이미 저장해둔 주소만 있으면 재동기화를 계속 쓸 수 있게 하는
    안전장치입니다. 고유주소 구조가 '/글제목/' 형태라면 이 방법으로는 못 찾습니다.)
    """
    post_id = article.get("platform_post_id", {}).get("wordpress")
    if post_id:
        return post_id
    url = article.get("platform_published_url", {}).get("wordpress") or ""
    m = re.search(r"[?&]p=(\d+)", url)
    if m:
        return int(m.group(1))
    return None


def _already_published_message(article: dict, platform: str) -> str:
    """이미 발행된(워드프레스 글 번호나 주소가 있는) 글이거나 '묶음에서 뺀(retired)' 글이면 발행을 막는 안내 문구, 아니면 ""
    (새 글 발행 흐름에는 영향 없음). 수정은 재동기화로 하고, 새 글 발행 버튼은 중복 글을 만들기 때문에 막습니다."""
    if article.get("retired"):
        return "묶음에서 뺀 글입니다. 새로 발행할 수 없습니다. (이미 발행된 글입니다. 수정은 재동기화로 하세요)"
    if (article.get("platform_post_id") or {}).get(platform) or (article.get("platform_published_url") or {}).get(platform):
        return "이미 발행된 글입니다. 수정은 재동기화로 하세요."
    return ""


# ---------- 혜택주머니 "묶음에서 빼기" (한 번에 한 글만) ----------
BACKUP_DIR = "backup"  # 이 폴더 안의 backup 폴더 (이미 쓰고 있는 곳)


def _retired_ids() -> set[str]:
    """지금 채널에서 '묶음에서 뺀(retired)' 글 id들 — 다시 기획되지 않게 '이미 다룬 주제'로 셀 때 씁니다. 읽기만 합니다."""
    try:
        return {aid for aid, a in load_articles().items() if isinstance(a, dict) and a.get("retired")}
    except Exception:  # noqa: BLE001 — 부가 정보라 읽기 실패해도 큐 만들기를 막지 않음
        return set()


def _retire_backup(tag: str) -> list[str]:
    """바꾸기 전에 대기열·글 json을 backup 폴더에 복사합니다. 반환: 만든 백업 파일 경로들."""
    import shutil
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cfg = get_channel_config()
    out = []
    for f in (cfg["queue_file"], cfg["articles_file"]):
        base, ext = os.path.splitext(os.path.basename(f))
        dst = os.path.join(BACKUP_DIR, f"{base}.backup_before_retire_{tag}_{stamp}{ext}")
        shutil.copy2(f, dst)
        out.append(dst)
    return out


def retire_article_from_bundle(article_id: str, today: datetime | None = None, merge_into_id: str | None = None) -> dict:
    """
    혜택주머니 글 하나를 묶음에서 뺍니다(워드프레스 휴지통·301은 사용자가 직접 함). 순서: 백업 → 글 json → 대기열 json.
      ① 대기열 approved=false  ② 글의 platform_*.wordpress 중 주소·발행일·글 번호를 비움(null)
      ③ 비우기 전 값·합칠 대표글 id·날짜를 글의 'retired' 칸에 남김
    반환: {"ok": bool, "reason": str, "merged_into": id, "backups": [...]}. 이미 뺀 글·미발행 글·대표글이면 아무것도 바꾸지 않습니다.
    merge_into_id(묶음 정리 화면 전용): 합칠 글을 같은 묶음의 이 글로 직접 지정합니다(세부 글이 합쳐질 곳이 대표가 아니거나 대표가 없는 묶음).
    안 주면 예전처럼 같은 묶음의 대표글로 기록합니다.
    """
    today = today or datetime.now()
    if get_active_channel_id() != "longterm_wp":
        return {"ok": False, "reason": "혜택주머니 글에서만 쓸 수 있습니다."}
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    articles = load_articles()
    art = articles.get(article_id)
    if not item or not isinstance(art, dict):
        return {"ok": False, "reason": "글을 찾을 수 없습니다."}
    if art.get("retired"):
        return {"ok": False, "reason": "이미 묶음에서 뺀 글입니다."}
    if item.get("cluster_role") == "대표":
        return {"ok": False, "reason": "대표글은 뺄 수 없습니다. 대표글은 세부 글이 합쳐질 글입니다."}
    prev = {
        "platform_post_id": (art.get("platform_post_id") or {}).get("wordpress"),
        "platform_published_url": (art.get("platform_published_url") or {}).get("wordpress"),
        "platform_published_at": (art.get("platform_published_at") or {}).get("wordpress"),
        "platform_scheduled_time": (art.get("platform_scheduled_time") or {}).get("wordpress"),
    }
    if not (prev["platform_post_id"] or prev["platform_published_url"] or prev["platform_published_at"]):
        return {"ok": False, "reason": "발행된 글이 아닙니다(글 번호·주소·발행일이 모두 비어 있음)."}
    if merge_into_id:
        head = find_queue_item(queue, merge_into_id)
        if (not head or head["id"] == article_id or head.get("week") != item.get("week")
                or head.get("cluster_seed") != item.get("cluster_seed")):
            return {"ok": False, "reason": "합칠 글이 같은 묶음의 다른 글이 아닙니다."}
    else:
        head = next((i for i in queue if i.get("week") == item.get("week") and i.get("cluster_seed") == item.get("cluster_seed")
                     and i.get("cluster_role") == "대표" and i["id"] != article_id), None)

    backups = _retire_backup(article_id)
    original_art = json.loads(json.dumps(art))
    original_approved = item.get("approved")
    try:
        art["retired"] = {
            "retired_at": today.strftime("%Y-%m-%d"),
            "merged_into": head["id"] if head else None,
            "merged_into_keyword": head["keyword"] if head else None,
            "previous": prev,
            "previous_approved": original_approved,
        }
        for key in ("platform_published_url", "platform_published_at", "platform_post_id"):
            if not isinstance(art.get(key), dict):
                art[key] = {}
            art[key]["wordpress"] = None
        articles[article_id] = art
        save_articles(articles)
        item["approved"] = False
        save_queue(queue)
    except Exception:
        articles[article_id] = original_art  # 중간에 실패하면 글 쪽을 되돌립니다(대기열은 마지막에 저장하므로 그대로)
        save_articles(articles)
        item["approved"] = original_approved
        raise
    return {"ok": True, "reason": "", "merged_into": head["id"] if head else None, "backups": backups}


@app.route("/article/<article_id>/retire-from-bundle", methods=["POST"])
def retire_from_bundle(article_id):
    """글 화면 [묶음에서 빼기] 버튼 — 한 번에 한 글만."""
    r = retire_article_from_bundle(article_id)
    return redirect(url_for(
        "view_final", article_id=article_id, platform="wordpress",
        retire_ok="1" if r["ok"] else "0", retire_msg=r.get("reason") or "", retire_head=r.get("merged_into") or "",
    ))


# ---------- 혜택주머니 "🧹 묶음 정리" (5편 묶음 → 3편, 계산·실행은 lt_cleanup.py) ----------
_lt_cleanup_lock = threading.Lock()  # 같은 시각에 두 번 실행되는 것(더블클릭)을 막습니다


@app.template_filter("lt_unquote")
def _lt_unquote_filter(url):
    from urllib.parse import unquote
    return unquote(url or "")


class _LtCleanupEnv:
    """lt_cleanup이 앱 기능을 부르는 통로. 앱 함수를 부를 때마다 이름으로 찾기 때문에, 시험에서는 json 복사본·가짜 재동기화로 바꿔 끼운 앱 함수가 그대로 쓰입니다."""

    def get_article(self, article_id):
        return get_article(article_id)

    def set_article(self, article_id, data):
        set_article(article_id, data)

    def backup(self, tag):
        return _retire_backup(tag)

    def resync(self, article_id, exclude_post_ids):
        # 브라우저를 띄우지 않는 방식(allow_browser_upload=False): 로컬 이미지가 남아 있으면 오류로 멈춥니다
        return _resync_wordpress_post(article_id, allow_browser_upload=False, exclude_post_ids=set(exclude_post_ids))

    def retire(self, article_id, merge_into_id):
        return retire_article_from_bundle(article_id, merge_into_id=merge_into_id)

    def match_link(self, article_id, keyword):
        """아직 안 채운 {{LINK:keyword}}가 어느 글과 짝이 될지(읽기만)."""
        queue, raw = load_queue(), load_articles()
        item = find_queue_item(queue, article_id)
        if not item:
            return {"status": "none", "target_id": None, "candidates": []}
        found = _match_link_target(keyword, _link_siblings(queue, raw, item.get("cluster_seed"), article_id, "wordpress"))
        return {"status": found["status"], "target_id": found["sibling"]["id"] if found["status"] == "match" else None,
                "candidates": [c["id"] for c in found["candidates"]]}


def _lt_wp_client():
    return lt_cleanup.WpClient()


def _lt_gsc_fetch():
    return lt_cleanup.fetch_gsc_metrics()


def _lt_cleanup_enter():
    """이 화면은 혜택주머니 전용 — 다른 채널에서 들어와도 혜택주머니로 맞추고, 워드프레스 접속 정보도 혜택주머니 것으로 되돌립니다."""
    if session.get("channel") != "longterm_wp":
        session["channel"] = "longterm_wp"
    wp_publisher.set_site_context(None)


def _lt_selection(entry: dict) -> dict:
    """화면에서 보낸 줄별 선택 {뺄 글 번호: (체크 여부, 합칠 글 번호)}. 줄 번호는 숨은 칸 row_<번호>로 받습니다."""
    sel = {}
    for key in request.form:
        m = re.fullmatch(r"row_(\d+)", key)
        if not m:
            continue
        src = int(m.group(1))
        try:
            dst = int(request.form.get(f"to_{src}") or 0) or None
        except ValueError:
            dst = None
        sel[src] = (bool(request.form.get(f"use_{src}")), dst)
    return sel


def _lt_cleanup_render(selections=None, preview=None, preview_file="", run=None, flash=""):
    queue, articles = load_queue(), load_articles()
    results = lt_cleanup.last_bundle_results()
    bundles = []
    for entry in lt_cleanup.CLEANUP_TABLE:
        plan = lt_cleanup.build_bundle_plan(entry, queue, articles, (selections or {}).get(entry["name"]))
        if preview and preview["plan"]["name"] == entry["name"]:
            plan = preview["plan"]  # 미리보기에서 가져온 서치콘솔 클릭·노출을 표에도 보여 줍니다
        rows = [r for r in plan["rows"] if r["in_table"]]  # 진행 표의 편수는 정리표 줄만 셉니다(화면에서 추가로 고른 줄은 제외)
        done = min(len(rows), sum(1 for r in plan["rows"] if r["done"]))
        last = results.get(entry["name"])
        all_done = bool(rows) and done == len(rows)
        if last and not last.get("ok"):
            status = "error"  # 가장 최근 실행이 중간에 멈춤 — 다시 [실행]하면 이어서 진행
        elif all_done:
            status = "done"
        else:
            status = "wait"
        bundles.append({"name": entry["name"], "plan": plan, "done": done, "total": len(rows), "status": status, "last": last})
    summary = {
        "done": sum(1 for b in bundles if b["status"] == "done"),
        "wait": sum(1 for b in bundles if b["status"] == "wait"),
        "error": sum(1 for b in bundles if b["status"] == "error"),
        "remaining": sum(b["total"] - b["done"] for b in bundles),
        "total": sum(b["total"] for b in bundles),
        "bundles": len(bundles),
    }
    return render_template("lt_cleanup.html", bundles=bundles, summary=summary, preview=preview, preview_file=preview_file,
                           run=run, flash=flash, open_name=(preview["plan"]["name"] if preview else (run or {}).get("bundle")))


@app.route("/lt-cleanup")
def lt_cleanup_page():
    _lt_cleanup_enter()
    return _lt_cleanup_render()


def _lt_entry_from_form():
    name = request.form.get("bundle", "")
    return next((e for e in lt_cleanup.CLEANUP_TABLE if e["name"] == name), None)


@app.route("/lt-cleanup/preview", methods=["POST"])
def lt_cleanup_preview():
    """[미리보기] — 묶음 하나. 서치콘솔 읽기 + 저장 본문을 계산만 해서 보여 주고 preview 폴더에 html로 저장합니다(워드프레스·json 변경 없음)."""
    _lt_cleanup_enter()
    entry = _lt_entry_from_form()
    if not entry:
        return _lt_cleanup_render(flash="묶음을 찾을 수 없습니다.")
    sel = _lt_selection(entry)
    plan = lt_cleanup.build_bundle_plan(entry, load_queue(), load_articles(), sel)
    pv = lt_cleanup.build_preview(plan, _LtCleanupEnv(), gsc_fn=_lt_gsc_fetch)
    os.makedirs(lt_cleanup.PREVIEW_DIR, exist_ok=True)
    path = os.path.join(lt_cleanup.PREVIEW_DIR, lt_cleanup.preview_filename(entry["name"]))
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_template("lt_cleanup_preview_file.html", pv=pv, saved_at=now_iso()))
    return _lt_cleanup_render(selections={entry["name"]: sel}, preview=pv, preview_file=path)


@app.route("/lt-cleanup/run", methods=["POST"])
def lt_cleanup_run():
    """[실행] — 묶음 하나(① 본문 정리·재동기화 → ② 묶음에서 빼기 → ③ 휴지통 → ④ 301 → ⑤ 확인). 확인 창에서 [확인]을 눌러야만 실행됩니다."""
    _lt_cleanup_enter()
    entry = _lt_entry_from_form()
    if not entry:
        return _lt_cleanup_render(flash="묶음을 찾을 수 없습니다.")
    sel = _lt_selection(entry)
    if request.form.get("confirmed") != "1":
        return _lt_cleanup_render(selections={entry["name"]: sel}, flash="확인 창에서 [확인]을 누르지 않아 실행하지 않았습니다.")
    if not _lt_cleanup_lock.acquire(blocking=False):
        return _lt_cleanup_render(selections={entry["name"]: sel}, flash="다른 묶음이 실행 중입니다. 끝난 뒤에 다시 눌러 주세요.")
    try:
        plan = lt_cleanup.build_bundle_plan(entry, load_queue(), load_articles(), sel)
        result = lt_cleanup.run_bundle(plan, _LtCleanupEnv(), _lt_wp_client())
    finally:
        _lt_cleanup_lock.release()
    return _lt_cleanup_render(selections={entry["name"]: sel}, run=result)


DEFAULT_PUBLISH_TIME = "09:00"  # 예약 시각이 저장돼 있지 않은 글의 발행 시각으로 가정하는 값 (발행일 비교용)


def _publish_dt(date_str: str | None, time_str: str | None) -> datetime | None:
    """platform_published_at("YYYY-MM-DD") + platform_scheduled_time("HH:MM")을 합친 발행(예약) 시각. 날짜가 없으면 None."""
    if not date_str:
        return None
    try:
        return datetime.strptime(f"{date_str[:10]} {(time_str or DEFAULT_PUBLISH_TIME)[:5]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _stored_publish_dt(article: dict, platform: str) -> datetime | None:
    return _publish_dt(
        (article.get("platform_published_at") or {}).get(platform),
        (article.get("platform_scheduled_time") or {}).get(platform),
    )


def _link_cutoff(article: dict, platform: str) -> datetime:
    """
    이 글에 내부링크를 걸 때 "그 시점에 이미 공개돼 있는 형제글"을 가르는 기준 시각.
    예약글이면 이 글이 공개되는 시각, 이미 공개된 글(또는 발행일 미정)이면 지금입니다 — 이미 나간 글을 나중에
    다시 채울 때는 "지금 공개돼 있는" 형제글까지 걸 수 있습니다.
    """
    own = _stored_publish_dt(article, platform)
    now = datetime.now()
    return max(own, now) if own else now


def _is_public_by(article: dict, platform: str, cutoff: datetime) -> bool:
    """형제글의 주소가 있고, 발행(예약) 시각이 cutoff 이하(=그 시점에 공개돼 있음)인지. 발행일이 없는데 주소만 있으면(수동 입력) 공개된 것으로 봅니다."""
    if not (article.get("platform_published_url") or {}).get(platform):
        return False
    dt = _stored_publish_dt(article, platform)
    return dt is None or dt <= cutoff


_P_LINK_RE = re.compile(r'href="https?://[^"]*?[?&]p=(\d+)"')


def _canonicalize_p_links(content: str, url_by_post_id: dict) -> tuple[str, int]:
    """본문 안의 href=".../?p=숫자" 링크 중 정식 주소를 아는 글의 것만 정식 주소로 바꿉니다. 반환: (새 본문, 바꾼 개수)"""
    count = 0

    def _sub(m):
        nonlocal count
        url = url_by_post_id.get(int(m.group(1)))
        if not url:
            return m.group(0)
        count += 1
        return f'href="{url}"'

    return _P_LINK_RE.sub(_sub, content), count


def _norm_link_text(text: str) -> str:
    """{{LINK:…}} 문구·키워드·제목을 비교하기 위한 정규화: HTML 태그(강조 등)와 모든 공백을 없애고 소문자로."""
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", text or "")).lower()


def _comma_front(text: str) -> str:
    return re.split(r"[,，]", text or "", maxsplit=1)[0]


def _link_siblings(queue: list[dict], articles_raw: dict, seed, exclude_id: str, platform: str, exclude_post_ids=None) -> list[dict]:
    """
    같은 클러스터에서 {{LINK:}}의 짝이 될 수 있는 "실제 글"들. 글이 있는(내용·주소·발행일) 항목만 —
    승인만 하고 글을 안 쓴 키워드나 후보 키워드까지 넣으면, 같은 키워드의 빈 항목이 진짜 글보다 먼저 짝으로 잡히거나
    진짜 글과 후보로 겹쳐서 "여러 개"가 돼 버립니다.
    exclude_post_ids(묶음 정리 전용): 이 워드프레스 글 번호의 글은 짝 후보에서 뺍니다(곧 뺄 글로 새 링크가 걸리지 않게). 기본값이면 예전과 같습니다.
    """
    out = []
    for it in queue:
        if it.get("cluster_seed") != seed or it["id"] == exclude_id:
            continue
        if not it.get("approved"):
            continue  # 미승인 글은 짝 후보에서 뺌(같은 키워드의 승인·공개본과 겹쳐 "후보 여러 개"가 되는 것을 막음)
        art = articles_raw.get(it["id"]) or {}
        if exclude_post_ids and _lt_post_id_of(art) in exclude_post_ids:
            continue
        has_article = bool(
            (art.get("platform_raw") or {}).get(platform)
            or (art.get("platform_final") or {}).get(platform)
            or (art.get("platform_published_url") or {}).get(platform)
            or (art.get("platform_published_at") or {}).get(platform)
        )
        if not has_article:
            continue
        out.append(
            {
                "id": it["id"],
                "keyword": it["keyword"],
                "title": (art.get("platform_title") or {}).get(platform) or "",
                "article": art,
            }
        )
    return out


def _match_link_target(x: str, siblings: list[dict]) -> dict:
    """
    {{LINK:X}}와 짝이 되는 형제글을 순서대로 찾습니다 (공백·태그 무시):
      1) 큐 키워드와 정확히 일치  2) 큐 키워드의 쉼표 앞부분과 일치  3) 글 제목의 쉼표 앞부분과 일치
      4) 큐 키워드 또는 글 제목이 X로 시작  5) 큐 키워드 또는 글 제목에 X가 포함
    어느 단계든 그 단계에서 후보가 정확히 1개면 짝으로 정하고, 2개 이상이면 링크를 걸지 않도록 "ambiguous"를 돌려줍니다.
    반환: {"status": "match"|"ambiguous"|"none", "sibling"(match일 때), "candidates", "rule"(몇 번째 단계)}
    """
    nx = _norm_link_text(x)
    if not nx:
        return {"status": "none", "candidates": [], "rule": None}
    rules = [
        lambda s: _norm_link_text(s["keyword"]) == nx,
        lambda s: _norm_link_text(_comma_front(s["keyword"])) == nx,
        lambda s: bool(s["title"]) and _norm_link_text(_comma_front(s["title"])) == nx,
        lambda s: _norm_link_text(s["keyword"]).startswith(nx) or (bool(s["title"]) and _norm_link_text(s["title"]).startswith(nx)),
        lambda s: nx in _norm_link_text(s["keyword"]) or (bool(s["title"]) and nx in _norm_link_text(s["title"])),
    ]
    for number, rule in enumerate(rules, 1):
        cands = [s for s in siblings if rule(s)]
        if len(cands) == 1:
            return {"status": "match", "sibling": cands[0], "candidates": cands, "rule": number}
        if len(cands) > 1:
            return {"status": "ambiguous", "candidates": cands, "rule": number}
    return {"status": "none", "candidates": [], "rule": None}


def _public_url_by_post_id(queue: list[dict], articles_raw: dict, platform: str, cutoff: datetime, exclude_id: str | None = None) -> dict:
    """{워드프레스 글 ID: 정식 주소} — cutoff 시점에 이미 공개된 글만. "?p=ID" 링크를 정식 주소로 바꿀 때 씁니다
    (아직 예약 중인 글을 가리키는 ?p= 링크는 바꾸지 않고 그대로 둡니다)."""
    out = {}
    for other in queue:
        if other["id"] == exclude_id:
            continue
        art = articles_raw.get(other["id"]) or {}
        url = (art.get("platform_published_url") or {}).get(platform)
        pid = (art.get("platform_post_id") or {}).get(platform)
        if url and pid and "?p=" not in url and _is_public_by(art, platform, cutoff):
            out[int(pid)] = url
    return out


def _resolve_internal_links(article_id: str, platform: str, cutoff: datetime | None = None, exclude_post_ids=None) -> tuple[int, int]:
    """
    이 글의 platform_final 안에 남아있는 {{LINK:키워드}} 자리표시자를, 같은 클러스터의 형제글과 짝지어(_match_link_target)
    그 형제글이 "cutoff 시점에 이미 공개돼 있는" 경우에만 실제 주소로 치환합니다. 짝이 없거나 후보가 여러 개거나 아직
    공개 전이면 {{LINK:}}로 남겨둡니다. cutoff를 안 넘기면 _link_cutoff 기준.
    본문에 이미 들어있는 "?p=숫자" 링크는 "이미 공개된 글"을 가리키는 것만 정식 주소로 바꿉니다.
    반환: (치환된 개수, 아직 못 채운 개수)
    """
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    article = get_article(article_id)
    if not item or platform not in PLATFORMS:
        return 0, 0
    cutoff = cutoff or _link_cutoff(article, platform)

    articles_raw = load_articles()  # 한 번만 읽습니다 (큐 항목마다 파일을 다시 읽지 않도록)
    siblings = _link_siblings(queue, articles_raw, item.get("cluster_seed"), article_id, platform, exclude_post_ids=exclude_post_ids)
    url_by_post_id = _public_url_by_post_id(queue, articles_raw, platform, cutoff, exclude_id=article_id)

    content = article["platform_final"].get(platform, "")
    resolved_count = 0
    remaining_count = 0

    def _replace(match):
        nonlocal resolved_count, remaining_count
        kw = match.group(1).strip()
        found = _match_link_target(kw, siblings)
        if found["status"] == "match" and _is_public_by(found["sibling"]["article"], platform, cutoff):
            resolved_count += 1
            return f'<a href="{found["sibling"]["article"]["platform_published_url"][platform]}">{kw}</a>'
        remaining_count += 1
        return match.group(0)

    new_content = re.sub(r"\{\{LINK:(.*?)\}\}", _replace, content)
    new_content, _ = _canonicalize_p_links(new_content, url_by_post_id)
    if new_content != content:
        article["platform_final"][platform] = new_content
        set_article(article_id, article)
    return resolved_count, remaining_count


def _publish_cutoff_from_form() -> datetime:
    """발행 폼 기준으로 이 글이 공개되는 시각: 예약이면 예약 시각, 즉시발행이면 지금."""
    now = datetime.now()
    if request.form.get("publish_mode") == "future":
        scheduled = _publish_dt(request.form.get("scheduled_at", "")[:10], request.form.get("scheduled_at", "")[11:16])
        if scheduled:
            return max(scheduled, now)
    return now


def _canonical_url_for(post_id, fallback_link: str | None) -> str | None:
    """발행·재동기화 뒤 저장할 정식 주소. REST 조회가 안 되면 응답에서 받은 주소로 대체합니다(글이 이미 만들어진 뒤라 실패해도 흐름은 계속)."""
    if post_id:
        try:
            url = wp_publisher.get_canonical_url(int(post_id))
            if url:
                return url
        except Exception as e:  # noqa: BLE001 — 주소 정리는 부가 기능이라 실패해도 발행/재동기화 결과는 유지
            print(f"[워드프레스] 경고 — 정식 주소 조회 실패, 응답 주소를 그대로 저장합니다: {type(e).__name__} {e}"[:220])
    return fallback_link


def _strip_unresolved_link_placeholders(content: str) -> str:
    """
    발행 직전, 아직 짝을 못 찾은 {{LINK:키워드}} 자리표시자를 중괄호만 벗겨서 평범한 텍스트로
    보이게 합니다 (독자한테 깨진 문법처럼 보이는 걸 방지). 저장된 platform_final 자체는 이
    함수로 안 건드립니다 — 나중에 형제글이 나와서 재동기화할 때 계속 찾을 수 있도록, 원본에는
    {{LINK:...}} 형태를 그대로 남겨둡니다. 이건 "밖으로 나가는 사본"에만 적용합니다.
    """
    # 워드프레스 일반의 {{SHOP:}} 연결: 못 찾은 줄은 통째로 지우고, 최종본 화면용 표시 주석도 벗깁니다.
    return shop_formatter.strip_shop_markers(re.sub(r"\{\{LINK:(.*?)\}\}", r"\1", content))


def _split_wp_tags(tag_text: str, limit: int = 10) -> list[str]:
    """워드프레스용 태그 칸 문자열을 쉼표(, ，)로 나눠 태그 목록으로 만듭니다.
    앞뒤 공백·맨 앞 # 제거, 빈 값·중복 제거, 최대 limit개. 태그 안의 띄어쓰기는 그대로 둡니다."""
    result: list[str] = []
    for part in re.split(r"[,，]", tag_text or ""):
        name = part.strip().lstrip("#").strip()
        if name and name not in result:
            result.append(name)
        if len(result) >= limit:
            break
    return result


def _category_and_tag_for_publish(article: dict, platform: str, form_category: str, form_tag: str) -> tuple[str, str]:
    """
    발행 폼에서 카테고리·태그를 직접 입력 안 하고 비워두면, 저장해둔 메타데이터 메모의
    "카테고리 제안"/"태그 제안"을 자동으로 씁니다 — 매번 직접 타이핑 안 해도 되게.
    """
    if form_category and form_tag:
        return form_category, form_tag
    memo_text = article["platform_metadata"].get(platform) or next(
        (v for v in article["platform_metadata"].values() if v), ""
    )
    parsed = metadata_parser.parse_metadata_memo(memo_text)
    category = form_category or parsed["category"]
    tag = form_tag or parsed["tag"]
    if not tag and platform == "wordpress" and get_active_channel_id() in ("shop_wp", "wp_tips"):
        # 메타데이터 메모에 "태그 제안"이 없으면, 원고 끝의 "태그: #…" 줄에서 뽑은 태그를 대신 씁니다(# 없이).
        _, raw_tags = shop_formatter.split_tag_line(article["platform_raw"].get("wordpress") or "")
        if raw_tags:
            tag = ", ".join(raw_tags)
    return category, tag


@app.route("/article/<article_id>/publish-to-wordpress", methods=["POST"])
def publish_to_wordpress(article_id):
    """
    최종본을 복사·붙여넣기 하지 않고, 워드프레스 REST API로 바로 발행합니다.
    성공하면 실제 주소(URL)와 발행일이 자동으로 저장됩니다 (직접 입력할 필요 없음).
    """
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    article = get_article(article_id)
    if not item or not article["platform_final"].get("wordpress"):
        return redirect(url_for("edit_article", article_id=article_id))

    blocked = _already_published_message(article, "wordpress")
    if blocked:
        return render_template("error.html", message=blocked), 409

    # 발행 직전에 내부링크를 한 번 더 자동으로 치환합니다 — 그 사이 형제글이 먼저 발행돼서
    # URL이 새로 생겼을 수도 있으니, "수동으로 먼저 버튼 눌러야 함" 없이 항상 최신 상태로 나갑니다.
    _resolve_internal_links(article_id, "wordpress", cutoff=_publish_cutoff_from_form())
    article = get_article(article_id)  # 방금 바뀐 내용 다시 불러오기

    publish_mode = request.form.get("publish_mode", "publish")  # "publish" | "future"
    scheduled_at = request.form.get("scheduled_at", "").strip()  # "YYYY-MM-DDTHH:MM" (예약 시)
    category_input = request.form.get("category", "").strip()
    tag_input = request.form.get("tag", "").strip()
    category_input, tag_input = _category_and_tag_for_publish(article, "wordpress", category_input, tag_input)

    status = "future" if publish_mode == "future" and scheduled_at else "publish"
    date_iso = scheduled_at if status == "future" else None

    is_longterm_publish = get_active_channel_id() == "longterm_wp"
    try:
        result = wp_publisher.publish_post(
            title=article["platform_title"].get("wordpress") or item["keyword"],
            html_content=_strip_unresolved_link_placeholders(article["platform_final"]["wordpress"]),
            schema_text=article["platform_schema"].get("wordpress", ""),
            status=status,
            date_iso=date_iso,
            category_names=[category_input] if category_input else None,
            tag_names=_split_wp_tags(tag_input) or None,
            focus_keyword=item["keyword"],
            meta_description=metadata_parser.parse_metadata_memo(article["platform_metadata"].get("wordpress", "")).get("meta_description") or None,
            # 숍 채널(+ 같은 사이트·방식인 워드프레스 일반): 이미지의 alt를 워드프레스 미디어 alt_text·title로 같이 넣고, 본문 이미지 블록에 미디어 ID를 심습니다.
            set_media_meta=get_channel_config().get("queue_builder") == "shopping" or get_active_channel_id() in SHOP_WP_SITE_CHANNELS,
            # 숍 채널(+ 워드프레스 일반): 본문·표지·버튼·스키마 등을 전부 표준 블록으로 보내서, 편집기가 통째로 클래식 블록 하나로 읽지 않게 합니다.
            as_blocks=get_channel_config().get("queue_builder") == "shopping" or get_active_channel_id() in SHOP_WP_SITE_CHANNELS,
            strip_cover=_wp_strip_cover(),
            # 워드프레스 일반(wp_tips) 채널만: "함께 보면 좋은 글"을 ① 이 글 카테고리의 최신 공개 글 ② 모자라면 사이트 전체 최신 글로 채움
            # ③ "기타 리뷰"로 표시한 글(워드프레스 SHOP·워드프레스 일반)은 뺌. (숍·롱텀은 같은 카테고리 기준 그대로)
            related_site_fill=get_active_channel_id() == "wp_tips",
            related_exclude_post_ids=_v4_excluded_wp_post_ids() if get_active_channel_id() == "wp_tips" else None,
            # 싼세계 예약 발행: 공개 시각이 이 글보다 이른 예약 글도 후보에 포함 (정식 주소를 아는 글만; 즉시 발행이면 해당 없음)
            related_future_before=(
                _publish_dt(scheduled_at[:10], scheduled_at[11:16]) if (get_active_channel_id() == "wp_tips" and status == "future") else None
            ),
            related_future_urls=_wp_future_canonical_urls() if (get_active_channel_id() == "wp_tips" and status == "future") else None,
            # 롱텀만: ① 같은 묶음(cluster_seed)의 이미 공개된 롱텀 글 먼저(자기 자신·예약 중 제외, 최신 발행순) ② 모자라면 같은 카테고리 최신 글
            related_priority_post_ids=(
                _longterm_same_cluster_post_ids(article_id, item.get("cluster_seed")) if is_longterm_publish else None
            ),
            # 롱텀만: 이미 올라간 표지로 다시 발행해도 대표이미지가 비지 않게 보완 (다른 채널은 예전 그대로)
            cover_fallback=is_longterm_publish,
        )
    except Exception as e:  # noqa: BLE001
        uploaded = getattr(e, "uploaded_images", None)
        if not (is_longterm_publish and uploaded):
            raise  # 롱텀이 아니거나 올라간 이미지가 없으면 예전과 똑같이 오류를 그대로 냅니다
        # 롱텀만: 이미지는 올라갔는데 글 만들기에서 난 오류 — 올라간 이미지 주소는 저장해서 다시 시도할 때 또 올리지 않게 합니다.
        for local_src, real_url in uploaded.items():
            article["platform_final"]["wordpress"] = article["platform_final"]["wordpress"].replace(
                f'src="{local_src}"', f'src="{real_url}"'
            )
        set_article(article_id, article)
        raise

    # 성공하면 발행일/URL/게시물ID를 자동으로 채웁니다 (직접 입력하던 걸 대신 해줌)
    published_date = (date_iso.split("T")[0] if date_iso else datetime.now().strftime("%Y-%m-%d"))
    article["platform_published_at"]["wordpress"] = published_date
    # 시(:분)도 따로 저장해둡니다 — 인스타/쓰레드처럼 수동으로 하는 홍보 작업을
    # "워드프레스랑 같은 시각으로 맞추기" 버튼에서 바로 가져다 쓰기 위함입니다.
    if date_iso and "T" in date_iso:
        article["platform_scheduled_time"]["wordpress"] = date_iso.split("T")[1][:5]
    else:
        article["platform_scheduled_time"]["wordpress"] = datetime.now().strftime("%H:%M")
    # REST가 예약글에 돌려주는 link는 "?p=ID"라서, 정식 주소(고유주소 + 슬러그)를 조회해서 저장합니다.
    article["platform_published_url"]["wordpress"] = _canonical_url_for(result.get("id"), result["link"])
    article["platform_post_id"]["wordpress"] = result.get("id")
    if is_longterm_publish:
        # 롱텀만: 실제로 올라간 이미지 주소를 저장본에 반영합니다(브라우저 경로와 같음) — 이래야 재동기화 때 이미지를 다시 올리지 않습니다.
        for local_src, real_url in (result.get("uploaded_images") or {}).items():
            article["platform_final"]["wordpress"] = article["platform_final"]["wordpress"].replace(
                f'src="{local_src}"', f'src="{real_url}"'
            )
    set_article(article_id, article)

    return redirect(url_for(
        "view_final", article_id=article_id, platform="wordpress", wp_published=result["link"],
        wp_featured_missing=1 if result.get("featured_media_missing") else None,
    ))


@app.route("/article/<article_id>/publish-to-wordpress-browser", methods=["POST"])
def publish_to_wordpress_browser(article_id):
    """
    REST API가 호스팅사 보안 시스템에 막힐 때의 대안 — 실제 브라우저를 띄워서
    사람이 로그인하고 글을 쓰는 것과 동일한 방식으로 발행합니다.
    """
    if get_active_channel_id() in SHOP_WP_SITE_CHANNELS:
        # 이 경로는 롱텀 사이트(config.WORDPRESS_URL)로 로그인해서 올립니다 — 워드프레스 SHOP(와 같은 사이트를 쓰는
        # 워드프레스 일반) 글이 롱텀 사이트로 잘못 나가지 않도록 막습니다 (이 채널들은 REST 발행을 씁니다).
        return redirect(url_for("view_final", article_id=article_id, platform="wordpress"))
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    article = get_article(article_id)
    if not item or not article["platform_final"].get("wordpress"):
        return redirect(url_for("edit_article", article_id=article_id))

    blocked = _already_published_message(article, "wordpress")
    if blocked:
        return render_template("error.html", message=blocked), 409

    # 발행 직전에 내부링크를 한 번 더 자동으로 치환합니다 (이 글이 공개되는 시점에 이미 공개돼 있는 형제글에만 링크를 겁니다).
    _resolve_internal_links(article_id, "wordpress", cutoff=_publish_cutoff_from_form())
    article = get_article(article_id)

    publish_mode = request.form.get("publish_mode", "publish")
    scheduled_at = request.form.get("scheduled_at", "").strip()
    category_input = request.form.get("category", "").strip()
    tag_input = request.form.get("tag", "").strip()
    category_input, tag_input = _category_and_tag_for_publish(article, "wordpress", category_input, tag_input)

    status = "future" if publish_mode == "future" and scheduled_at else "publish"
    date_iso = scheduled_at if status == "future" else None

    try:
        result = wp_browser_publisher.publish_post(
            title=article["platform_title"].get("wordpress") or item["keyword"],
            html_content=_strip_unresolved_link_placeholders(article["platform_final"]["wordpress"]),
            schema_text=article["platform_schema"].get("wordpress", ""),
            status=status,
            date_iso=date_iso,
            category_names=[category_input] if category_input else None,
            tag_names=_split_wp_tags(tag_input) or None,
            headless=False,  # 화면에 실제로 보이게 (신뢰 확인용으로 요청하신 옵션)
            step_callback=lambda msg: print(f"[워드프레스 자동발행] {msg}"),
            focus_keyword=item["keyword"],
            meta_description=metadata_parser.parse_metadata_memo(article["platform_metadata"].get("wordpress", "")).get("meta_description") or None,
            strip_cover=_wp_strip_cover(),
            related_priority_post_ids=(
                _longterm_same_cluster_post_ids(article_id, item.get("cluster_seed"))
                if get_active_channel_id() == "longterm_wp" else None
            ),
            cover_fallback=get_active_channel_id() == "longterm_wp",  # 롱텀만: 대표이미지 빠짐 방지 보완
        )
    except Exception as e:  # noqa: BLE001
        uploaded = getattr(e, "uploaded_images", None)
        if uploaded is None:
            raise  # 이미지 업로드가 끝나기 전에 난 오류는 글이 만들어졌을 수 없으니 기존 오류 처리 그대로
        # 이미지 업로드 뒤에 난 오류: 글은 이미 만들어졌을 수도 있습니다(응답만 늦은 경우 등). 올라간 이미지 주소는
        # 저장해서 다시 시도할 때 이미지를 또 올리지 않게 하고, 중복 발행 위험을 화면에 알립니다.
        for local_src, real_url in uploaded.items():
            article["platform_final"]["wordpress"] = article["platform_final"]["wordpress"].replace(
                f'src="{local_src}"', f'src="{real_url}"'
            )
        set_article(article_id, article)
        return render_template(
            "error.html",
            message=(
                f"{e}\n\n"
                "⚠ 워드프레스 관리자에서 예약글이 생겼는지 먼저 확인하세요. 다시 누르면 중복될 수 있습니다.\n"
                f"(이미지 {len(uploaded)}장은 이미 업로드되어 주소를 저장해 두었습니다 — 다시 시도해도 이미지는 또 올라가지 않습니다.)"
            ),
        ), 500

    published_date = (date_iso.split("T")[0] if date_iso else datetime.now().strftime("%Y-%m-%d"))
    article["platform_published_at"]["wordpress"] = published_date
    if date_iso and "T" in date_iso:
        article["platform_scheduled_time"]["wordpress"] = date_iso.split("T")[1][:5]
    else:
        article["platform_scheduled_time"]["wordpress"] = datetime.now().strftime("%H:%M")
    if result.get("link"):
        article["platform_published_url"]["wordpress"] = _canonical_url_for(result.get("id"), result["link"])
    if result.get("id"):
        article["platform_post_id"]["wordpress"] = result["id"]
    # 실제로 업로드에 성공한 이미지 주소는 저장 데이터에도 반영합니다 — 이래야 다음 재동기화 때
    # 이미 성공한 이미지까지 매번 다시 올리려고 시도하지 않고, 진짜 실패한 것만 골라 처리합니다.
    for local_src, real_url in result.get("uploaded_images", {}).items():
        article["platform_final"]["wordpress"] = article["platform_final"]["wordpress"].replace(
            f'src="{local_src}"', f'src="{real_url}"'
        )
    set_article(article_id, article)

    return redirect(
        url_for(
            "view_final", article_id=article_id, platform="wordpress",
            wp_published=result.get("link") or "발행 완료(주소는 직접 확인 후 입력해주세요)",
            wp_featured_missing=1 if result.get("featured_media_missing") else None,
        )
    )


@app.route("/article/<article_id>/resolve-links/<platform>", methods=["POST"])
def resolve_links(article_id, platform):
    """
    이 글의 platform_final 안에 남아있는 {{LINK:키워드}} 자리표시자를,
    같은 클러스터(cluster_seed) + 같은 플랫폼에 실제로 발행된 다른 글의 주소로 자동 치환합니다.
    아직 발행 안 된(URL이 없는) 키워드는 그대로 남겨두고, 몇 개를 못 채웠는지 알려줍니다.
    """
    resolved_count, remaining_count = _resolve_internal_links(article_id, platform)
    return redirect(url_for("view_final", article_id=article_id, platform=platform, resolved=resolved_count, remaining=remaining_count))


def _match_local_images_in_library(final_html: str, cache: dict | None = None) -> list[dict]:
    """저장본에 남은 로컬 이미지(/static/generated/...)마다, 워드프레스 미디어 라이브러리에서 같은 파일을 찾습니다(조회=GET만).
    파일명 비교는 숍 재동기화와 같습니다: 확장자·-scaled·-가로x세로·-1 같은 접미사는 무시(shop_wp_resync.media_key).
    반환: [{"src", "key", "hits": [(미디어ID, 주소), ...]}] — hits는 최신순(같은 파일이 여러 번 올라가 있을 수 있음)."""
    srcs = sorted(set(re.findall(r'src="(/static/generated/[^"]+)"', final_html or "")))
    if not srcs:
        return []
    index = shop_wp_resync._load_media_index(cache if cache is not None else {})
    return [{"src": s, "key": shop_wp_resync.media_key(s), "hits": index.get(shop_wp_resync.media_key(s)) or []} for s in srcs]


def _resolve_local_images_via_rest(article_id: str) -> dict:
    """롱텀 재동기화용: 저장본에 로컬 이미지 경로가 남아 있으면, 브라우저 대신 REST로 처리합니다.
    1) 미디어 라이브러리에 같은 파일이 이미 있으면 그 주소를 재사용 2) 없는 것만 wp_publisher.upload_media(REST)로 업로드.
    성공한 것은 저장본(platform_final)의 src를 실제 주소로 바꿔 저장해서 다음엔 다시 찾지 않게 합니다.
    못 한 것(조회·업로드 실패, 로컬 파일 없음)은 그대로 남겨 두고(브라우저 예비 경로가 처리), 반환값에 기록합니다.
    반환: {"reused": N, "uploaded": N, "left": [남은 로컬 주소...]}"""
    article = get_article(article_id)
    final = article["platform_final"]["wordpress"]
    summary = {"reused": 0, "uploaded": 0, "left": []}
    try:
        matches = _match_local_images_in_library(final)
    except (requests.RequestException, wp_publisher.WordPressPublishError) as e:
        print(f"[워드프레스 재동기화] 미디어 라이브러리 조회 실패 — REST 재사용·업로드를 건너뜁니다: {type(e).__name__} {e}"[:220])
        summary["left"] = sorted(set(re.findall(r'src="(/static/generated/[^"]+)"', final)))
        return summary
    for m in matches:
        if m["hits"]:
            real_url = m["hits"][0][1]
            summary["reused"] += 1
            print(f"[워드프레스 재동기화] 이미지 재사용(미디어 {m['hits'][0][0]}): {os.path.basename(m['src'])}")
        else:
            local_path = os.path.join(app.static_folder, m["src"].replace("/static/", "", 1))
            if not os.path.exists(local_path):
                summary["left"].append(m["src"])
                continue
            try:
                uploaded = wp_publisher.upload_media(local_path)
            except (requests.RequestException, wp_publisher.WordPressPublishError) as e:
                print(f"[워드프레스 재동기화] REST 업로드 실패({os.path.basename(m['src'])}): {type(e).__name__} {e}"[:220])
                summary["left"].append(m["src"])
                continue
            real_url = uploaded["source_url"]
            summary["uploaded"] += 1
            print(f"[워드프레스 재동기화] 이미지 REST 업로드(미디어 {uploaded['id']}): {os.path.basename(m['src'])}")
        final = final.replace(f'src="{m["src"]}"', f'src="{real_url}"')
    if final != article["platform_final"]["wordpress"]:
        article["platform_final"]["wordpress"] = final
        set_article(article_id, article)
    return summary


def _longterm_same_cluster_post_ids(article_id: str, cluster_seed: str | None, limit: int = 5, exclude_post_ids=None) -> list[int]:
    """
    롱텀 하단 "함께 보면 좋은 글" ① — 같은 cluster_seed(같은 묶음)의 이미 공개된 롱텀 글(자기 자신·예약 중 제외)의
    워드프레스 글 ID를 최신 발행순으로 (최대 limit개). 지금 보고 있는 채널(롱텀)의 저장된 데이터만 읽고 워드프레스에는 요청하지 않습니다.
    """
    if not cluster_seed:
        return []
    queue, articles, now = load_queue(), load_articles(), datetime.now()
    rows = []
    for it in queue:
        if it.get("id") == article_id or it.get("cluster_seed") != cluster_seed:
            continue
        art = articles.get(it["id"]) or {}
        if art.get("retired") or not _is_public_by(art, "wordpress", now):
            continue  # 묶음에서 뺀 글, 아직 공개 전(예약)이거나 주소가 없는 글은 뺍니다
        try:
            post_id = int((art.get("platform_post_id") or {}).get("wordpress") or 0)
        except (TypeError, ValueError):
            post_id = 0
        if post_id and not (exclude_post_ids and post_id in exclude_post_ids):
            rows.append((_stored_publish_dt(art, "wordpress") or datetime.min, post_id))
    rows.sort(key=lambda r: r[0], reverse=True)  # 최신 발행순
    return list(dict.fromkeys(pid for _, pid in rows))[:limit]


def _resync_wordpress_post(article_id: str, cutoff: datetime | None = None, allow_browser_upload: bool = True, exclude_post_ids=None) -> tuple[int, int]:
    """
    이미 발행된(또는 예약된) 워드프레스 글을 최신 상태로 다시 올립니다: 이미지 정리 → 내부링크 채우기 → 본문 업데이트(REST)
    → 대표이미지 확인 → 정식 주소 저장. 반환: (채운 링크 수, 아직 못 채운 링크 수).
    allow_browser_upload=False면 로컬 이미지가 남아 있을 때 브라우저를 띄우지 않고 오류를 냅니다(일괄 처리용).
    exclude_post_ids(묶음 정리 전용): 이 글 번호의 글은 새 내부링크·"함께 보면 좋은 글"에 넣지 않습니다. 기본값이면 예전과 같습니다.
    """
    article = get_article(article_id)
    post_id = _infer_wordpress_post_id(article)
    if not post_id:
        raise wp_publisher.WordPressPublishError(
            "이 글의 워드프레스 게시물 ID를 찾을 수 없습니다 (자동발행 기능으로 발행했거나, "
            "저장된 주소에 '?p=숫자' 형태의 고유주소가 있는 글만 재동기화가 가능합니다)."
        )
    # 추론으로 얻은 ID라면(원래 저장이 안 돼 있었다면), 다음부터는 바로 쓸 수 있게 지금 저장해둡니다.
    if not article.get("platform_post_id", {}).get("wordpress"):
        article["platform_post_id"]["wordpress"] = post_id
        set_article(article_id, article)

    queue = load_queue()
    item = find_queue_item(queue, article_id)

    # 로컬 경로로 남아 있는 이미지는 먼저 REST로 처리합니다: 워드프레스 미디어 라이브러리에 같은 파일이 있으면 재사용하고,
    # 없는 것만 REST로 업로드합니다(브라우저로 전부 다시 올려 미디어가 중복되는 것을 막음). 처리한 주소는 저장본에 반영됩니다.
    if 'src="/static/generated/' in article["platform_final"]["wordpress"]:
        _resolve_local_images_via_rest(article_id)
        article = get_article(article_id)

    # REST로 못 처리한 이미지(업로드 실패 등)만 예비 경로로: 다시 로그인해서 그것만 골라 올립니다
    # (이미 성공한 이미지는 저장 데이터에 실제 주소가 반영되어 있으므로 다시 시도하지 않습니다).
    if not allow_browser_upload and 'src="/static/' in article["platform_final"]["wordpress"]:
        raise wp_publisher.WordPressPublishError("본문에 아직 로컬 이미지 경로가 남아 있어, 글마다 개별 재동기화로 처리해야 합니다.")
    fixed_content, newly_uploaded = wp_browser_publisher.reupload_missing_images(
        article["platform_final"]["wordpress"],
        headless=False,
        step_callback=lambda msg: print(f"[워드프레스 재동기화] {msg}"),
    )
    if newly_uploaded:
        article["platform_final"]["wordpress"] = fixed_content
        set_article(article_id, article)

    resolved_count, remaining_count = _resolve_internal_links(article_id, "wordpress", cutoff=cutoff, exclude_post_ids=exclude_post_ids)
    article = get_article(article_id)

    # "함께 보면 좋은 글" 블록은 로컬 메모가 아니라, 워드프레스에 이미 지정된 실제 카테고리를
    # 직접 조회해서 씁니다 — 메모를 안 채워뒀어도 항상 정확하게 다시 만들어집니다.
    category_ids = wp_publisher.get_post_category_ids(post_id)
    category_input = None
    if not category_ids:
        category_input, _ = _category_and_tag_for_publish(article, "wordpress", "", "")

    outgoing_html = _strip_unresolved_link_placeholders(article["platform_final"]["wordpress"])
    # 안전장치: 보낼 본문에 로컬 이미지 주소(/static/…)가 남아 있으면(업로드·재사용을 못 한 이미지) 깨진 주소가
    # 실제 글에 덮어써지므로, 워드프레스에 아무것도 보내지 않고 멈춥니다.
    left_local = shop_wp_resync.leftover_local_srcs(outgoing_html)
    if left_local:
        raise wp_publisher.WordPressPublishError(shop_wp_resync.local_images_left_message(left_local))

    result = wp_publisher.update_post_via_rest(
        post_id=post_id,
        html_content=outgoing_html,
        schema_text=article["platform_schema"].get("wordpress", ""),
        focus_keyword=item["keyword"] if item else None,
        category_ids=category_ids or None,
        category_names=[category_input] if category_input else None,
        strip_cover=_wp_strip_cover(),
        # 롱텀 재동기화: 하단 "함께 보면 좋은 글"도 같은 규칙(같은 묶음 먼저, 모자라면 같은 카테고리). 다른 채널은 그대로.
        related_priority_post_ids=(
            _longterm_same_cluster_post_ids(article_id, (item or {}).get("cluster_seed"), exclude_post_ids=exclude_post_ids)
            if get_active_channel_id() == "longterm_wp" else None
        ),
        related_exclude_post_ids=exclude_post_ids,
    )

    # 정식 주소도 이때 함께 정리해 저장합니다 (예전에 "?p=ID"로 저장된 글도 재동기화 한 번으로 바로잡힙니다).
    canonical = _canonical_url_for(post_id, result.get("link"))
    if canonical and canonical != article["platform_published_url"].get("wordpress"):
        article = get_article(article_id)
        article["platform_published_url"]["wordpress"] = canonical
        set_article(article_id, article)

    # 대표이미지도 다시 확인합니다 — 예전에 본문 이미지가 대표이미지로 잘못 지정되던 버그가
    # 있었는데, 저장된 본문 안에는 실제 표지 이미지의 진짜 주소가 이미 들어있으므로
    # (업로드 성공 시 로컬 저장본도 함께 갱신됨), 그 주소로 미디어 라이브러리에서 역으로
    # 찾아서 대표이미지를 표지로 다시 맞춥니다.
    cover_match = re.search(
        r'<img class="seo-cover-img" src="([^"]+)"', article["platform_final"]["wordpress"]
    )
    if cover_match:
        cover_url = cover_match.group(1)
        if not cover_url.startswith("/static/"):  # 아직 업로드 전(로컬 경로)이면 건너뜁니다.
            cover_media_id = wp_publisher.find_media_id_by_source_url(cover_url)
            if cover_media_id:
                print(f"[워드프레스 재동기화] 대표이미지를 표지 이미지(id={cover_media_id})로 다시 지정")
                try:
                    wp_publisher.set_featured_media(post_id, cover_media_id)
                except Exception as e:  # noqa: BLE001 — 대표이미지 재지정은 부가 기능이라 실패해도 재동기화는 끝까지 진행
                    print(f"[워드프레스 재동기화] 경고 — 대표이미지 재지정 실패, 건너뜁니다: {type(e).__name__} {e}"[:220])
            else:
                print(f"[워드프레스 재동기화] 표지 이미지를 미디어 라이브러리에서 못 찾음: {cover_url}")
    return resolved_count, remaining_count


@app.route("/article/<article_id>/resync-wordpress", methods=["POST"])
def resync_wordpress(article_id):
    """
    이미 발행이 끝난 글을, 나중에 형제글이 추가로 발행돼서 새로 생긴 내부링크까지 마저 채워서
    다시 업데이트합니다. 예약발행처럼 형제글들이 며칠에 걸쳐 순차적으로 나갈 때, 매번 이전 글을
    수동으로 다시 여는 대신 이 버튼 하나로 최신 상태로 맞출 수 있습니다.
    """
    if get_active_channel_id() in SHOP_WP_SITE_CHANNELS:
        # 재동기화의 이미지 재업로드 단계가 롱텀 사이트로 로그인하는 브라우저 자동화라서, 워드프레스 SHOP(와 같은 사이트를
        # 쓰는 워드프레스 일반)에서는 막습니다.
        return redirect(url_for("view_final", article_id=article_id, platform="wordpress"))
    resolved_count, remaining_count = _resync_wordpress_post(article_id)
    return redirect(
        url_for("view_final", article_id=article_id, platform="wordpress", resolved=resolved_count, remaining=remaining_count)
    )


def _lt_post_id_of(stored_article: dict):
    """저장된 글 데이터(날것 dict)에서 워드프레스 글 ID(없으면 주소의 ?p=ID에서 추론)를 돌려줍니다."""
    return _infer_wordpress_post_id({
        "platform_post_id": stored_article.get("platform_post_id") or {},
        "platform_published_url": stored_article.get("platform_published_url") or {},
    })


def _lt_cover_restore_targets() -> list[tuple[str, int]]:
    """롱텀 글 중 워드프레스 글 ID가 있는 것 전부 [(article_id, post_id)] — 표지 복구 일괄 버튼의 대상(실제로 빠졌는지는 글마다 조회해서 판단)."""
    out = []
    for aid, stored in load_articles().items():
        if stored.get("retired"):
            continue  # 묶음에서 뺀 글은 워드프레스에서 휴지통으로 보냈으므로 대상 아님
        pid = _lt_post_id_of(stored)
        if pid:
            out.append((aid, int(pid)))
    return out


def _restore_cover_for(article_id: str, post_id: int, alt_hint: str | None, dry_run: bool = False) -> dict:
    """글 하나의 표지 복구(wp_publisher.restore_cover_in_post)를 부르고, 실패해도 예외 대신 skipped로 돌려줍니다(일괄 처리가 멈추지 않게)."""
    try:
        return wp_publisher.restore_cover_in_post(post_id, alt_hint=alt_hint, dry_run=dry_run)
    except (requests.RequestException, wp_publisher.WordPressPublishError) as e:
        return {"status": "skipped", "reason": f"{type(e).__name__}: {e}"[:200], "featured_media": 0, "src": "", "alt": "", "new_content": ""}


@app.route("/article/<article_id>/restore-lt-cover", methods=["POST"])
def restore_lt_cover(article_id):
    """롱텀만: 이 글의 워드프레스 본문 맨 위에 표지(seo-cover-img)가 없으면 대표이미지로 다시 끼워 넣습니다(content만 업데이트, 이미 있으면 아무것도 안 함)."""
    final_url = url_for("view_final", article_id=article_id, platform="wordpress")
    if get_active_channel_id() != "longterm_wp":
        return redirect(final_url)
    article = get_article(article_id)
    post_id = _infer_wordpress_post_id(article)
    if not post_id:
        return redirect(url_for("view_final", article_id=article_id, platform="wordpress", cr_one="skipped", cr_reason="워드프레스 글 ID를 찾을 수 없음"))
    r = _restore_cover_for(article_id, int(post_id), (article.get("cover_alts") or {}).get("wordpress") or None)
    return redirect(url_for("view_final", article_id=article_id, platform="wordpress", cr_one=r["status"], cr_reason=r.get("reason") or ""))


@app.route("/restore-lt-covers", methods=["POST"])
def restore_lt_covers():
    """롱텀만: 표지 빠진 글 전체 복구. 대상 글마다 restore_cover_in_post를 부르고 "복구 N / 이미 있음 N / 건너뜀 N(이유)"를 보여 줍니다."""
    back = request.form.get("back") or ""
    if get_active_channel_id() != "longterm_wp":
        return redirect(url_for("index"))
    stored_all = load_articles()
    restored = already = 0
    skipped: list[str] = []
    for aid, post_id in _lt_cover_restore_targets():
        alt_hint = ((stored_all.get(aid) or {}).get("cover_alts") or {}).get("wordpress") or None
        r = _restore_cover_for(aid, post_id, alt_hint)
        if r["status"] == "restored":
            restored += 1
        elif r["status"] == "already":
            already += 1
        else:
            skipped.append(f"{post_id}: {r.get('reason') or '알 수 없음'}")
    detail = " / ".join(skipped)[:700]
    target = back if back.startswith("/article/") else url_for("index")
    sep = "&" if "?" in target else "?"
    from urllib.parse import quote
    return redirect(f"{target}{sep}cr_ok={restored}&cr_already={already}&cr_skip={len(skipped)}&cr_detail={quote(detail)}")


@app.route("/article/<article_id>/resync-shop-wordpress", methods=["POST"])
def resync_shop_wordpress(article_id):
    """
    워드프레스 SHOP (OLD)·싼세계(wp_tips) 재동기화: 발행 후 사진·내용을 바꿨을 때 이미 올라간 글을 저장된 최신 최종본으로 덮어씁니다.
    이미 올라간 이미지는 재사용하고 바뀐 것만 REST로 업로드합니다(shop_wp_resync.py). 롱텀 재동기화(브라우저 로그인 방식)와는 별개입니다.
    두 채널 모두 같은 덮어쓰기 안전장치(확인 화면)를 거치며, 사용자가 버튼을 눌렀을 때만 실행됩니다.
    """
    final_url = url_for("view_final", article_id=article_id, platform="wordpress")
    if get_active_channel_id() not in SHOP_WP_SITE_CHANNELS:
        return redirect(final_url)
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    article = get_article(article_id)
    post_id = (article.get("platform_post_id") or {}).get("wordpress")
    if not item or not post_id or not article["platform_final"].get("wordpress"):
        return redirect(final_url)

    def _save_final(new_final):
        # 이 글의 platform_final["wordpress"]만 바꿉니다(다른 필드·다른 글은 그대로).
        current = get_article(article_id)
        current["platform_final"]["wordpress"] = new_final
        set_article(article_id, current)

    try:
        result = shop_wp_resync.run(
            article=article,
            keyword=item["keyword"],
            post_id=post_id,
            static_folder=app.static_folder,
            strip_cover=_wp_strip_cover(),
            outgoing_fn=_strip_unresolved_link_placeholders,
            save_final=_save_final,
            # 덮어쓰기 안전장치: 경고 화면의 "사라지는 것을 확인했고 그래도 덮어쓰기" 체크박스를 체크하고 다시 실행했을 때만 넘어갑니다.
            confirm_overwrite=request.form.get("confirm_overwrite") == "1",
            # 싼세계: "함께 보면 좋은 글"을 발행·새로고침과 같은 규칙으로 (워드프레스 SHOP (OLD)는 None = 예전 그대로)
            related_opts=(
                {"exclude_post_ids": _v4_excluded_wp_post_ids(), "site_fill": True, **_wp_tips_future_opts(article)}
                if get_active_channel_id() == "wp_tips" else None
            ),
        )
    except shop_wp_resync.ResyncGuardError as e:
        return render_template("shop_wp_resync_guard.html", guard=e.guard, article_id=article_id, keyword=item["keyword"]), 409
    except shop_wp_resync.ResyncError as e:
        return render_template("error.html", message=f"{CHANNELS[get_active_channel_id()]['label']} 재동기화 실패:\n{e}"), 500
    return redirect(
        url_for(
            "view_final", article_id=article_id, platform="wordpress",
            resync_reused=result["reused"], resync_new=result["new"],
            resync_rm_warn=1 if result.get("rank_math_saved") is False else None,
        )
    )


def _wp_future_canonical_urls() -> dict[int, str]:
    """{워드프레스 글 ID: 정식 주소} — 같은 사이트를 쓰는 싼세계·워드프레스 SHOP(OLD)의 저장 데이터에서 읽기만 합니다.
    예약 글은 REST가 "?p=ID" 주소를 줘서 쓸 수 없으니, 발행 때 저장해 둔 정식 주소만 씁니다. ?p= 주소가 저장된 글은 뺍니다."""
    out: dict[int, str] = {}
    for channel_id in SHOP_WP_SITE_CHANNELS:
        _, arts = _read_channel_data(channel_id)
        for a in arts.values():
            pid = (a.get("platform_post_id") or {}).get("wordpress")
            url = ((a.get("platform_published_url") or {}).get("wordpress") or "").strip()
            if pid and url and not re.search(r"[?&]p=\d+", url):
                try:
                    out[int(pid)] = url
                except (TypeError, ValueError):
                    continue
    return out


def _wp_tips_future_opts(article: dict) -> dict:
    """싼세계 "함께 보면 좋은 글"에 예약 글을 넣기 위한 옵션({future_before, future_urls}). 이 글이 아직 공개 전(예약)일 때만 해당 — 아니면 {}."""
    dt = _stored_publish_dt(article, "wordpress")
    if not dt or dt <= datetime.now():
        return {}
    urls = _wp_future_canonical_urls()
    return {"future_before": dt, "future_urls": urls} if urls else {}


@app.route("/article/<article_id>/refresh-wp-tips-related", methods=["POST"])
def refresh_wp_tips_related(article_id):
    """
    워드프레스 일반(wp_tips) 채널의 이미 발행된 글에서 "함께 보면 좋은 글" 블록만 새로 고칩니다 — 이 글 카테고리의 최신 공개 글
    먼저, 5개가 안 되면 사이트 전체 최신 글로 채우고, "기타 리뷰" 글은 뺍니다 (발행할 때와 같은 규칙). 사용자가 버튼을 눌렀을 때만 실행됩니다.
    워드프레스에 올라간 본문의 그 블록만 교체하며, 로컬 원고 데이터(platform_final 등)는 건드리지 않습니다.
    (이미지 재업로드·브라우저 로그인·내부링크 채우기는 하지 않습니다 — 재동기화 함수를 쓰지 않는 별도 경로.)
    """
    if get_active_channel_id() != "wp_tips":
        return redirect(url_for("view_final", article_id=article_id, platform="wordpress"))
    article = get_article(article_id)
    post_id = (article.get("platform_post_id") or {}).get("wordpress")
    if not post_id:
        return redirect(url_for("view_final", article_id=article_id, platform="wordpress"))
    try:
        wp_publisher.refresh_related_posts_block(
            int(post_id), category_first=True, exclude_post_ids=_v4_excluded_wp_post_ids(), **_wp_tips_future_opts(article)
        )
    except Exception as e:  # noqa: BLE001 — 조회·생성 실패 시 업데이트 없이 오류만 보여 줍니다
        return render_template("error.html", message=f"함께 보면 좋은 글 새로고침 실패 (워드프레스 글은 바뀌지 않았습니다):\n{e}"), 500
    return redirect(url_for("view_final", article_id=article_id, platform="wordpress", tips_related_refreshed=1))


WP_TIPS_RELATED_BATCH_DAYS = 14  # 싼세계 "함께 보면 좋은 글" 일괄 새로고침 대상: 최근 이 일수 안에 공개됐거나 앞으로 예약된 글


def _wp_tips_related_batch_targets(now: datetime | None = None) -> list[dict]:
    """일괄 새로고침 대상 — 싼세계 글 중 워드프레스 글 ID가 있고, 공개(예약) 시각이 최근 14일 이후인 글 (예약 중인 글 포함). 읽기만 합니다."""
    now = now or datetime.now()
    since = now - timedelta(days=WP_TIPS_RELATED_BATCH_DAYS)
    ch_queue, ch_articles = _read_channel_data("wp_tips")
    out, seen = [], set()
    for it in ch_queue:
        aid = it.get("id")
        if aid in seen:
            continue
        seen.add(aid)
        art = ch_articles.get(aid) or {}
        pid = (art.get("platform_post_id") or {}).get("wordpress")
        dt = _stored_publish_dt(art, "wordpress")
        if not pid or not dt or dt < since:
            continue
        out.append({
            "id": aid, "post_id": int(pid), "keyword": it.get("keyword", ""),
            "title": (art.get("platform_title") or {}).get("wordpress") or it.get("keyword", ""),
            "dt": dt, "published": f"{dt:%Y-%m-%d %H:%M}", "scheduled": dt > now,
            "url": (art.get("platform_published_url") or {}).get("wordpress") or "",
            "future_opts": _wp_tips_future_opts(art),
        })
    out.sort(key=lambda t: t["dt"])
    return out


def _wp_tips_related_batch_run(only_ids: set[str] | None, apply: bool) -> list[dict]:
    """대상 글마다 refresh_related_posts_block을 부릅니다 (apply=False면 dry-run, True면 바뀌는 글만 실제 업데이트). 글 하나가 실패해도 나머지는 계속."""
    excluded = _v4_excluded_wp_post_ids()
    rows = []
    for t in _wp_tips_related_batch_targets():
        if only_ids is not None and t["id"] not in only_ids:
            continue
        row = {k: t[k] for k in ("id", "post_id", "title", "published", "scheduled", "url")}
        try:
            res = wp_publisher.refresh_related_posts_block(
                t["post_id"], dry_run=not apply, only_if_changed=apply, category_first=True,
                exclude_post_ids=excluded, **t["future_opts"],
            )
            row.update(ok=True, error="", changed=res["changed"], old=res["old_links"], new=res["new_links"])
        except Exception as e:  # noqa: BLE001 — 조회·생성 실패한 글은 바뀌지 않았다고 표시하고 다음 글로
            row.update(ok=False, error=str(e)[:300], changed=False, old=[], new=[])
        rows.append(row)
    return rows


@app.route("/wp-tips-related-batch")
def wp_tips_related_batch_preview():
    """싼세계 "함께 보면 좋은 글" 일괄 새로고침 — 확인 화면. 워드프레스는 읽기만 하고 아무것도 바꾸지 않습니다 (바뀔 글·전후 추천 제목 표시)."""
    if get_active_channel_id() != "wp_tips":
        return redirect(url_for("index"))
    return render_template("wp_related_batch.html", rows=_wp_tips_related_batch_run(None, apply=False), done=False, days=WP_TIPS_RELATED_BATCH_DAYS)


@app.route("/wp-tips-related-batch/run", methods=["POST"])
def wp_tips_related_batch_run():
    """확인 화면에서 [실행]을 눌렀을 때만: 화면에 보였던 글 중 지금도 추천이 달라지는 글만 '함께 보면 좋은 글' 블록을 교체합니다 (본문은 그대로)."""
    if get_active_channel_id() != "wp_tips":
        return redirect(url_for("index"))
    ids = {i for i in request.form.getlist("ids") if i}
    rows = _wp_tips_related_batch_run(ids, apply=True) if ids else []
    return render_template("wp_related_batch.html", rows=rows, done=True, days=WP_TIPS_RELATED_BATCH_DAYS)


# ---------- 내부링크 채우기 (이미 공개된 글의 남은 {{LINK:}}를, 그 사이 공개된 형제글로 채움) ----------

FILL_BATCH_SIZE = 10  # "내부링크 채우기"를 한 번에 재동기화하는 최대 글 수 (워드프레스 서버가 느린 편이라 나눠서 처리)


def _fill_link_targets(now: datetime | None = None) -> dict:
    """
    롱텀에서 "지금 공개된" 글 중 손볼 것이 있는 글을 발행일 오래된 순으로 골라냅니다. 이유는 두 가지입니다:
      - 링크 채움: 남은 {{LINK:X}}의 짝(형제글)이 지금 공개돼 있음
      - ?p= 교체: 본문에 href=".../?p=숫자" 링크가 남아 있고, 그 링크가 가리키는 글이 지금 공개돼 있어 정식 주소로 바꿀 수 있음
        (예약 중인 글을 가리키는 ?p= 링크는 바꾸지 않고 그대로 둡니다)
    이 함수는 로컬 데이터(저장된 본문)만 읽고 워드프레스에는 아무 요청도 보내지 않습니다.
    반환: {"targets": [...], "blocked": [로컬 이미지가 남아 일괄 처리에서 빠지는 글]}
    """
    now = now or datetime.now()
    platform = "wordpress"
    queue = load_queue()
    articles = load_articles()
    public_map = _public_url_by_post_id(queue, articles, platform, now)
    targets, blocked, seen = [], [], set()
    for it in queue:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        if not it.get("approved"):
            continue  # 미승인 글은 대상에서 뺌(같은 키워드의 승인본이 따로 공개돼 있을 수 있고, 옛 발행 기록이 남은 죽은 글일 수 있음)
        art = articles.get(it["id"]) or {}
        if not _is_public_by(art, platform, now):
            continue  # 이 글 자체가 아직 공개 전이면(예약 중) 대상이 아님
        final = (art.get("platform_final") or {}).get(platform) or ""
        siblings = None
        fills, waiting = [], []
        for x in dict.fromkeys(m.strip() for m in re.findall(r"\{\{LINK:(.*?)\}\}", final)):
            if siblings is None:
                siblings = _link_siblings(queue, articles, it.get("cluster_seed"), it["id"], platform)
            found = _match_link_target(x, siblings)
            label = _norm_link_text(x) and re.sub(r"<[^>]+>", "", x)
            if found["status"] == "ambiguous":
                waiting.append(f"{label} (후보 여러 개: " + " / ".join(c["keyword"][:24] for c in found["candidates"][:4]) + ")")
            elif found["status"] == "none":
                waiting.append(f"{label} (짝이 되는 글 없음)")
            else:
                sib = found["sibling"]
                if _is_public_by(sib["article"], platform, now):
                    fills.append({"x": label, "to": sib["keyword"], "rule": found["rule"]})
                elif not (sib["article"].get("platform_published_url") or {}).get(platform):
                    waiting.append(f"{label} → {sib['keyword'][:24]} (아직 발행 전)")
                else:
                    dt = _stored_publish_dt(sib["article"], platform)
                    waiting.append(f"{label} → {sib['keyword'][:24]} (예약 {dt:%Y-%m-%d %H:%M}, 아직 공개 전)" if dt else f"{label} (공개 전)")
        p_ids = [int(n) for n in _P_LINK_RE.findall(final)]
        p_replace = sum(1 for n in p_ids if n in public_map)
        p_keep = len(p_ids) - p_replace  # 예약 중인 글 등을 가리켜 그대로 두는 ?p= 링크
        if not fills and not p_replace:
            continue
        reasons = []
        if fills:
            reasons.append(f"링크 {len(fills)}개 채움")
        if p_replace:
            reasons.append(f"?p= 링크 {p_replace}개 교체")
        dt = _stored_publish_dt(art, platform)
        entry = {
            "id": it["id"],
            "keyword": it["keyword"],
            "published": f"{dt:%Y-%m-%d %H:%M}" if dt else "-",
            "sort_key": dt or datetime.min,
            "fills": fills,
            "waiting": waiting,
            "p_replace": p_replace,
            "p_keep": p_keep,
            "reason": " + ".join(reasons),
        }
        (blocked if 'src="/static/' in final else targets).append(entry)
    targets.sort(key=lambda e: e["sort_key"])
    blocked.sort(key=lambda e: e["sort_key"])
    return {"targets": targets, "blocked": blocked}


@app.route("/fill-internal-links")
def fill_internal_links_preview():
    """대시보드 "내부링크 채우기" — 대상 목록만 보여줍니다 (워드프레스에 아무것도 보내지 않음)."""
    if get_active_channel_id() != "longterm_wp":
        return redirect(url_for("index"))
    return render_template("fill_links.html", plan=_fill_link_targets(), results=None, batch=FILL_BATCH_SIZE, skip="")


@app.route("/fill-internal-links/run", methods=["POST"])
def fill_internal_links_run():
    """대상 글을 발행일 오래된 순으로 최대 FILL_BATCH_SIZE편씩 재동기화하고, 글별 결과를 보여줍니다."""
    if get_active_channel_id() != "longterm_wp":
        return redirect(url_for("index"))
    now = datetime.now()
    skip = {s for s in request.form.get("skip", "").split(",") if s}  # 앞선 실행에서 실패해서 건너뛰는 글
    plan = _fill_link_targets(now)
    todo = [t for t in plan["targets"] if t["id"] not in skip][:FILL_BATCH_SIZE]
    results = []
    for t in todo:
        before_article = get_article(t["id"])
        original_final = before_article["platform_final"]["wordpress"]
        p_before = len(_P_LINK_RE.findall(original_final))
        try:
            resolved, remaining = _resync_wordpress_post(t["id"], cutoff=now, allow_browser_upload=False)
            p_after = len(_P_LINK_RE.findall(get_article(t["id"])["platform_final"]["wordpress"]))
            results.append({**t, "ok": True, "resolved": resolved, "remaining": remaining, "p_replaced": p_before - p_after, "error": ""})
        except Exception as e:  # noqa: BLE001 — 한 글의 실패가 나머지 처리를 막지 않게
            # 워드프레스 업데이트가 실패했는데 저장본만 바뀐 채로 남으면 다음 미리보기에서 대상이 사라지므로 되돌립니다.
            restore = get_article(t["id"])
            restore["platform_final"]["wordpress"] = original_final
            set_article(t["id"], restore)
            err_text = f"{type(e).__name__}: {e}"
            if "rest_post_invalid_id" in err_text:
                err_text = "워드프레스에 이 글이 없습니다(삭제됨). 발행 기록 정리가 필요합니다. (" + err_text[:120] + ")"
            results.append({**t, "ok": False, "resolved": 0, "remaining": None, "p_replaced": 0, "error": err_text[:300]})
            skip.add(t["id"])
    fresh = _fill_link_targets()
    left = [t for t in fresh["targets"] if t["id"] not in skip]
    return render_template(
        "fill_links.html", plan={**fresh, "targets": left}, results=results, batch=FILL_BATCH_SIZE, skip=",".join(sorted(skip)),
        failed_skipped=len(skip),
    )


MEMO_PASTE_WARNING = "⚠ 메타데이터 메모에 본문이 들어간 것 같아요. 원고 채팅의 두 번째 코드 블록(메타 디스크립션으로 시작)을 붙여넣어 주세요."


def _memo_looks_like_body(text, radar: bool = False):
    """메타데이터 메모 칸에 원고 본문이 잘못 붙은 것 같은지 확인합니다 (빈 칸은 해당 없음).
    radar=True(생활레이더)면 "카테고리 제안"·"카드:" 블록도 메모 신호로 인정합니다(기존 채널 조건은 그대로)."""
    text = (text or "").strip()
    if not text:
        return False
    if text.split("\n", 1)[0].lstrip().startswith("[플랫폼:"):
        return True
    signals = ("표지 1줄", "메타 디스크립션", "표지 장면") + (("카테고리 제안", "카드:") if radar else ())
    return not any(k in text for k in signals)


@app.route("/article/<article_id>/save-metadata/<platform>", methods=["POST"])
def save_metadata(article_id, platform):
    """
    메타 디스크립션·카테고리·태그·alt텍스트 제안 등을 참고용 메모로 저장합니다.
    articles.json에 저장되므로 앱을 껐다 켜도, 다른 컴퓨터(구글드라이브 동기화)에서도 그대로 남습니다.
    """
    metadata_text = request.form.get("metadata_text", "")
    if _memo_looks_like_body(metadata_text, radar=get_active_channel_id() in RADAR_CHANNELS):
        session["memo_paste_warn"] = MEMO_PASTE_WARNING  # 경고만 — 저장은 막지 않음
    article = get_article(article_id)
    if platform in PLATFORMS:
        article["platform_metadata"][platform] = metadata_text
    if get_active_channel_id() in RADAR_CHANNELS:
        _radar_sync(article)  # 생활레이더: 글 유형(post_type)·카드 제목·3줄 요약(thread_card) 저장
    set_article(article_id, article)
    return redirect(request.referrer or url_for("view_final", article_id=article_id, platform=platform))


def _lt_plan_view(article: dict) -> dict | None:
    """article["lt_plan"] = {"scheduled_at": "YYYY-MM-DDTHH:MM", "category", "tag"} (tools/lt_auto_input.py 가 저장)를 화면용으로 풀어 줍니다.
    없거나 시각 모양이 틀리면 None. past=True 면 예약 시각이 지금보다 이전."""
    plan = article.get("lt_plan")
    if not isinstance(plan, dict):
        return None
    try:
        dt = datetime.strptime(str(plan.get("scheduled_at") or "")[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    category = str(plan.get("category") or "").strip()
    tag = str(plan.get("tag") or "").strip()
    label = f"{dt.month}월 {dt.day}일({'월화수목금토일'[dt.weekday()]}) {dt:%H:%M}" + (f" · {category}" if category else "")
    return {"scheduled_at": dt.strftime("%Y-%m-%dT%H:%M"), "category": category, "tag": tag, "label": label, "past": dt < datetime.now()}


def _wp_tips_sched_prefill(article_id: str) -> dict | None:
    """싼세계(wp_tips) 최종본 화면용: pipeline/wp_drafts/<주>/_예약표.md 에서 이 글(대기열 id)의 예약 날짜·시각을 읽기만 한다.
    입력칸 미리 채우기 전용 — 아무것도 저장하지 않는다(화면을 열어도 앱 데이터가 안 바뀜). 없으면 None."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline", "wp_drafts")
    try:
        weeks = sorted(os.listdir(base), reverse=True)
    except OSError:
        return None
    for wk in weeks:
        path = os.path.join(base, wk, "_예약표.md")
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for ln in lines:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) >= 6 and cells[5] == article_id and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]) and re.fullmatch(r"\d{2}:\d{2}", cells[2]):
                try:
                    dt = datetime.strptime(f"{cells[0]}T{cells[2]}", "%Y-%m-%dT%H:%M")
                except ValueError:
                    return None
                return {"scheduled_at": dt.strftime("%Y-%m-%dT%H:%M"),
                        "label": f"{dt.month}월 {dt.day}일({'월화수목금토일'[dt.weekday()]}) {dt:%H:%M}", "past": dt < datetime.now()}
    return None


@app.route("/article/<article_id>/final/<platform>")
def view_final(article_id, platform):
    queue = load_queue()
    item = find_queue_item(queue, article_id)
    article = get_article(article_id)
    if not item or platform not in PLATFORMS or not article["platform_final"].get(platform):
        return redirect(url_for("edit_article", article_id=article_id))

    content = article["platform_final"][platform]
    # (radar169_nv75off_20261027) 네이버용 75% 파일로 바꿔 보여 주던 _nv75_preview 호출은 껐습니다 — 원본 사진을 그대로 보여 줍니다.
    pending_links = len(set(re.findall(r"\{\{LINK:(.*?)\}\}", content))) if "{{LINK:" in content else 0
    # 꿀팁 채널의 "내 숍 추천 글" 연결 결과(표) — 복사·발행되는 사본(outgoing_content)에서는 못 찾은 줄이 빠집니다.
    shop_results = shop_formatter.extract_shop_results(content)
    outgoing_content = shop_formatter.strip_shop_markers(content)

    # 숍 채널: 글쓰기 프롬프트에서 가격을 빼기로 한 뒤에도 원고에 가격 표현이 실수로 남아있는지
    # 확인합니다. 자동으로 지우지 않고 알려만 줍니다 — 실제로 가격을 표기해야 하는 예전 방식
    # 글일 수도 있으니 사람이 보고 판단합니다.
    # 워드프레스 일반은 원고에 쿠팡 링크 줄이 있을 때만(=쿠팡 글일 때만) 같은 확인을 합니다.
    raw_for_price = article["platform_raw"].get(platform) or ""
    # V5: 네이버·싼세계 글의 [숍글] 상태 (그 채널의 플랫폼 화면에서만)
    shop_post = _shop_post_state(article, get_active_channel_id(), raw_for_price)
    if shop_post and shop_post["platform"] != platform:
        shop_post = None
    shop_post_effective = bool(shop_post and shop_post["effective"])

    # 싼세계 외부 이미지: 최종본에 몇 장 들어갔는지, 소제목을 못 찾아 못 넣은 이미지(노란 경고), 아직 반영 전인 이미지
    ext_info = None
    if get_active_channel_id() in EXTERNAL_IMAGE_CHANNELS and platform == "wordpress":
        ext_imgs = _external_images(article)
        if ext_imgs:
            final_html = article["platform_final"].get("wordpress") or ""
            _, ext_unmatched = shop_formatter.match_external_images(_ext_sections(article), ext_imgs)
            unmatched_paths = {e["path"] for e in ext_unmatched}
            miss: dict = {}
            for e in ext_unmatched:
                miss[e.get("heading") or "(소제목 없음)"] = miss.get(e.get("heading") or "(소제목 없음)", 0) + 1
            ext_info = {
                "total": len(ext_imgs),
                "included": sum(1 for e in ext_imgs if e["path"] in final_html),
                "missing": list(miss.items()),
                "pending": sum(1 for e in ext_imgs if e["path"] not in final_html and e["path"] not in unmatched_paths),
            }

    price_mentions = (
        shop_formatter.find_price_mentions(raw_for_price)
        if get_channel_config().get("queue_builder") == "shopping"
        or (get_active_channel_id() == "wp_tips" and shop_formatter.has_coupang_link(raw_for_price))
        or shop_post_effective
        else []
    )

    # 워드프레스 SHOP(OLD)·싼세계 숍글: 구매 링크가 비었거나 자리표시자([쿠팡링크])인 상품, 안전하지 않은 쿠팡 코드가 든 상품, 배너 주소 미설정을 알립니다.
    cta_warnings = None
    if (get_active_channel_id() == "shop_wp" or (shop_post_effective and platform == "wordpress")) and platform == "wordpress":
        cta_warnings = shop_formatter.cta_link_warnings(raw_for_price)
        # 2026-10-15 배너 사용 중단: 배너 주소가 비어 있는 것이 정상 설정이므로 "배너 미설정" 경고는 항상 끕니다.
        cta_warnings["banner_missing"] = False
        if not (cta_warnings["empty"] or cta_warnings["unsafe"] or cta_warnings["banner_missing"]):
            cta_warnings = None

    # 워드프레스 3채널(롱텀·숍·일반): 본문 표가 5열 이상이면 모바일에서 화면 밖으로 넘칠 수 있어 화면에만 경고합니다.
    # (템플릿에서만 보여 주므로 복사·발행되는 본문(content/outgoing_content)에는 들어가지 않습니다.)
    wide_table_cols = 0
    if platform == "wordpress" and get_active_channel_id() in ("longterm_wp", "shop_wp", "wp_tips"):
        wide_table_cols = seo_formatter.wide_table_max_cols(content)

    # 워드프레스는 한글 고유주소(슬러그)라 SNS 등에 그냥 복사하면 %EC%9C%A1... 처럼 인코딩된
    # 긴 주소로 붙여넣어지는 경우가 있습니다. SEO용 정식 주소는 그대로 두고, 공유용으로 쓸 수
    # 있는 짧은 링크(워드프레스에 플러그인 없이 기본 내장된 "?p=글ID" 형태)를 따로 보여줍니다.
    short_link = None
    if platform == "wordpress":
        post_id = _infer_wordpress_post_id(article)
        wp_url = get_active_wordpress_url()
        if post_id and wp_url:
            short_link = f"{wp_url}/?p={post_id}"

    # 네이버 SHOP·네이버 일반: 원고 끝 "태그: #… " 줄(본문에서는 빠짐)을 최종 화면 상단 "추천 태그" 박스로 보여줍니다. 줄이 없으면 메타데이터 메모의 "태그 제안"(최대 5개).
    recommended_tags, tags_source = [], ""
    if platform == "naver" and get_active_channel_id() in ("shop_naver", "naver_tips"):  # radar_naver 는 아래 radar_meta 칸(태그별 복사)을 씁니다
        _, recommended_tags = shop_formatter.split_tag_line(article["platform_raw"].get("naver") or "")
        tags_source = "원고의 태그 줄"
        if not recommended_tags:
            memo = metadata_parser.parse_metadata_memo(article["platform_metadata"].get("naver") or "")
            recommended_tags = [t.strip().lstrip("#").replace(" ", "") for t in re.split(r"[,，]", memo.get("tag") or "") if t.strip()][:5]
            tags_source = "메타데이터 메모의 태그 제안"

    # 네이버 일반(naver_tips)·워드프레스 일반(wp_tips) 전용: 에디터에서 제휴 링크를 직접 넣었을 때 붙여 넣을 대가성 문구 복사 칸.
    # 문구·모양은 숍 버전(shop_formatter의 _disclosure_html / _wp_disclosure_block)에서 <p> 부분을 그대로 가져옵니다.
    disclosure_copy = None
    active_channel = get_active_channel_id()
    if (active_channel, platform) in (("naver_tips", "naver"), ("wp_tips", "wordpress")) and not shop_post_effective:
        # (숍글이면 대가성 문구가 본문에 이미 들어가므로 이 복사 칸은 일반글일 때만 보여 줍니다)
        is_naver_box = platform == "naver"
        shop_html = shop_formatter._disclosure_html("naver") if is_naver_box else shop_formatter._wp_disclosure_block()
        p_match = re.search(r"<p\b.*?</p>", shop_html, flags=re.DOTALL)
        lines = shop_formatter.DISCLOSURE_NAVER_LINES if is_naver_box else shop_formatter.DISCLOSURE_WORDPRESS_LINES
        if p_match:
            disclosure_copy = {
                "kind": platform,
                "html": p_match.group(0),
                "plain": "\n".join(lines),
                # 원고의 "링크:" 줄 때문에 앱이 이미 본문에 문구를 넣었는지: 저장된 최종본에 같은 문구가 있는지로 확인합니다.
                "already": lines[0] in content,
            }

    # N데스크피커 ③ 화면 위쪽: 제목 복사·카테고리·태그별 복사·강조 표시 경고 (읽기만, 아무것도 저장하지 않음)
    radar_meta = None
    if get_active_channel_id() in RADAR_CHANNELS and platform == "naver":
        _raw_all = article["platform_raw"].get("naver") or ""
        radar_meta = radar_screen.radar_meta_view(
            article["platform_metadata"].get("naver") or "", _raw_all, radar_screen.plan_genre(item),
            article["platform_title"].get("naver") or "",
        )
        radar_meta["emphasis_warns"] = radar_screen.emphasis_warnings(_radar_split_sources(_raw_all)[0])
        radar_meta["sub_warns"] = radar_screen.subheading_warnings(_radar_split_sources(_raw_all)[0], article["platform_metadata"].get("naver") or "")  # 제품 소제목(###) 빠진 상품 구간
        _gctx = _radar_desk_context(article_id)
        radar_meta["gif_warns"] = [w for w in [radar_screen.main_gif_warning(_raw_all, article["platform_metadata"].get("naver") or "", _gctx[1], _gctx[2]) if _gctx else ""] if w]  # 틀 B 본상품 GIF 없이 대안 GIF 만
        radar_meta["avoid_warns"] = radar_screen.avoid_warnings(_radar_split_sources(_raw_all)[0], article["platform_metadata"].get("naver") or "", article["platform_title"].get("naver") or "")  # 피할 표현(⑨)

    # N데스크피커 ③ 화면용 최종본(⑫): 📷 줄 자리에 naver_upload 안 같은 파일을 그림으로 보여 줌. 복사용(outgoing_content)은 📷 줄 그대로.
    radar_screen_content, radar_banner_missing, radar_badge_missing = None, [], []
    if get_active_channel_id() in RADAR_CHANNELS and platform == "naver":
        _sctx = _radar_desk_context(article_id)
        if _sctx:
            radar_screen_content = radar_screen.screen_html(content, _sctx[2])
        radar_banner_missing = radar_screen.banner_missing_from(content)
        radar_badge_missing = radar_screen.badge_missing_from(content)

    # 혜택주머니(longterm_wp) 자동 입력 글: 발행 칸(API·브라우저)에 예약 시각·카테고리·태그를 미리 채웁니다. lt_plan 이 없으면 None(화면 그대로).
    lt_plan_view = _lt_plan_view(article) if (platform == "wordpress" and active_channel == "longterm_wp") else None
    # 싼세계(wp_tips): _예약표.md 의 이 글 날짜·시각을 '예약 발행' 입력칸에만 미리 채웁니다(읽기만 — 저장·발행 안 함).
    wp_sched_view = _wp_tips_sched_prefill(article_id) if (platform == "wordpress" and active_channel == "wp_tips") else None

    return render_template(
        "final_view.html",
        idx=article_id,
        platform=platform,
        lt_plan_view=lt_plan_view,
        wp_sched_view=wp_sched_view,
        radar_meta=radar_meta,
        radar_screen_content=radar_screen_content,
        radar_banner_missing=radar_banner_missing,
        radar_badge_missing=radar_badge_missing,
        shop_post=shop_post,
        ext_info=ext_info,
        shop_msg=request.args.get("shop_msg", ""),
        disclosure_copy=disclosure_copy,
        # 롱텀 발행된 글의 "표지 다시 넣기" 일괄 버튼 확인창에 보여 줄 대상 편수(워드프레스 글 ID가 있는 롱텀 글 수)
        lt_cover_targets=len(_lt_cover_restore_targets()) if (platform == "wordpress" and active_channel == "longterm_wp") else 0,
        recommended_tags=recommended_tags,
        tags_source=tags_source,
        platform_label=PLATFORM_LABELS[platform],
        keyword=item["keyword"],
        content=content,
        outgoing_content=outgoing_content,
        bad_naver_url=_bad_naver_url_warning(article) if platform == "naver" else "",
        shop_results=shop_results,
        published_date=article["platform_published_at"].get(platform) or "",
        published_url=article["platform_published_url"].get(platform) or "",
        short_link=short_link,
        metadata_text=article["platform_metadata"].get(platform) or "",
        schema_text=article["platform_schema"].get(platform) or "",
        pub_status=platform_status(article, platform),
        pending_links=pending_links,
        price_mentions=price_mentions,
        cta_warnings=cta_warnings,
        wide_table_cols=wide_table_cols,
        link_replaced=_LINK_REPLACED.get((get_active_channel_id(), article_id, platform)) or [],
        cover_title_fallback=article["cover_title_fallback"].get(platform, False),
        memo_warn=session.pop("memo_paste_warn", ""),
        memo_warn_text=MEMO_PASTE_WARNING,
        resolved=request.args.get("resolved"),
        remaining=request.args.get("remaining"),
        wordpress_configured=wp_publisher.current_site_configured(),
        missing_wordpress_vars=(
            config.MISSING_SHOP_WORDPRESS_VARS
            if get_channel_config().get("wordpress_site")
            else config.MISSING_WORDPRESS_VARS
        ),
        wordpress_browser_configured=config.WORDPRESS_BROWSER_CONFIGURED,
        missing_wordpress_browser_vars=config.MISSING_WORDPRESS_BROWSER_VARS,
        naver_blog_browser_configured=config.NAVER_BLOG_BROWSER_CONFIGURED,
        missing_naver_blog_browser_vars=config.MISSING_NAVER_BLOG_BROWSER_VARS,
        wp_published=request.args.get("wp_published"),
        wp_featured_missing=bool(request.args.get("wp_featured_missing")),
        has_wordpress_post_id=bool(_infer_wordpress_post_id(article)),
        # 혜택주머니 "묶음에서 빼기": 이미 뺀 글이면 그 기록, 아니면 (발행된 세부 글이면) 버튼 표시
        lt_retired=article.get("retired") if (platform == "wordpress" and active_channel == "longterm_wp") else None,
        lt_can_retire=(
            platform == "wordpress" and active_channel == "longterm_wp" and not article.get("retired")
            and item.get("cluster_role") != "대표"
            and bool(_infer_wordpress_post_id(article) or (article.get("platform_published_url") or {}).get("wordpress"))
        ),
        retire_ok=request.args.get("retire_ok"),
        retire_msg=request.args.get("retire_msg", ""),
        has_wp_tips_post_id=bool((article.get("platform_post_id") or {}).get("wordpress")),
        tips_related_refreshed=request.args.get("tips_related_refreshed"),
        has_shop_wp_post_id=bool((article.get("platform_post_id") or {}).get("wordpress")),
        resync_reused=request.args.get("resync_reused"),
        resync_new=request.args.get("resync_new"),
        resync_rm_warn=request.args.get("resync_rm_warn"),
        meta_seo=_meta_description_seo_check(article["platform_metadata"].get(platform) or ""),
        sc_box=_sc_box(article, get_active_channel_id(), platform),
        radar_downloads=_radar_downloads(article) if get_active_channel_id() in RADAR_CHANNELS else None,
        body_downloads=_naver_body_downloads(article) if (get_active_channel_id() in NV75_CHANNELS and platform == "naver") else None,
        radar_sources=(article.get("radar_sources") or []) if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else None,
        radar_table_skipped=radar_screen.table_skipped_from(content) if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else None,
        radar_photo_count=radar_screen.photos_count_from(content) if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else None,
        radar_empty_slots=radar_screen.count_empty_slots(content) if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else None,
        radar_missing_links=radar_screen.count_missing_card_links(content) if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else None,
        radar_no_related=radar_screen.has_no_related_mark(content) if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else None,
        radar_upload=_radar_upload_view(article_id, content) if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else None,
        radar_upload_err=(session.pop("radar_upload_err", "") if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else "") or request.args.get("upload_msg", ""),
        radar_pub_err=session.pop("radar_pub_err", ""),
        radar_final_err=session.pop("radar_final_err", "") if (get_active_channel_id() in RADAR_CHANNELS and platform == "naver") else "",
        sc_msg=SOURCE_CAPTURE_MSGS.get(request.args.get("sc_msg", ""), ""),
    )


@app.route("/article/<article_id>/tag-volumes", methods=["POST"])
def tag_volumes(article_id):
    """최종본 "추천 태그" 박스의 [검색량 확인] 버튼용. 화면에만 보여 주며 아무것도 저장하지 않습니다(캐시 파일 제외)."""
    import naver_api
    import target_keyword_finder as tkf
    if not find_queue_item(load_queue(), article_id):
        return jsonify({"error": "글을 찾을 수 없습니다"}), 404
    body = request.get_json(silent=True) or {}
    tags = []
    for t in (body.get("tags") or [])[:30]:
        t = str(t).strip().lstrip("#").replace(" ", "")
        if t and t not in tags:
            tags.append(t)
    if not tags:
        return jsonify({"error": "태그가 없습니다"}), 400

    errors = []
    vols, err = tkf.fetch_volumes(tags)
    if err:
        errors.append(err)
    totals = []  # 태그 순서대로 PC+모바일 합계 (정보 없으면 None)
    for t in tags:
        v = vols.get(tkf.normalize_keyword(t))
        totals.append((v["pc"] + v["mobile"]) if v else None)

    # 추가 후보: 대표(첫 번째) 태그의 연관 키워드 — 이미 있는 태그 제외, 월 합계 50 이상, 검색량 순 최대 8개
    related = []
    ckey = "rel|" + tkf.normalize_keyword(tags[0])
    rows = tkf._cache_get(ckey)
    if rows is None:
        try:
            tkf._throttle()
            rows = [
                {"keyword": r.get("keyword") or "", "total": int(r.get("total_count") or 0)}
                for r in naver_api.get_related_keywords(tags[0])
                if int(r.get("total_count") or 0) >= 50
            ]
            if rows:
                tkf._cache_put(ckey, rows)
        except Exception as e:  # noqa: BLE001 — 추가 후보 실패가 검색량 결과를 막지 않게
            rows = []
            errors.append(f"추가 후보 조회 실패: {type(e).__name__}: {e}"[:200])
    have = {tkf.normalize_keyword(t) for t in tags}
    seen = set()
    for r in sorted(rows, key=lambda x: -x["total"]):
        norm = tkf.normalize_keyword(r["keyword"])
        if not norm or norm in have or norm in seen or r["total"] < 50:
            continue
        seen.add(norm)
        related.append({"keyword": r["keyword"].replace(" ", ""), "total": r["total"]})
        if len(related) >= 8:
            break
    return jsonify({"tags": tags, "totals": totals, "related": related, "error": " / ".join(errors)})


# ---------- 발행 현황 (주차 상관없이 전체를 가로지르는 화면) ----------


@app.route("/status")
def status():
    queue = load_queue()
    approved_items = [item for item in queue if item.get("approved")]
    # 같은 키워드가 여러 주차에 걸쳐 중복 승인된 경우를 대비해 id로 중복 제거
    seen_ids = set()
    rows = []
    v4_excluded = _load_v4_excluded()
    is_v4_channel = get_active_channel_id() in v4_board.V4_CHANNELS
    for item in approved_items:
        if item["id"] in seen_ids:
            continue
        seen_ids.add(item["id"])
        article = get_article(item["id"])
        rows.append(
            {
                "v4": _v4_tag(article, get_active_channel_id(), item["id"], v4_excluded) if is_v4_channel else None,
                "id": item["id"],
                "keyword": item["keyword"],
                "cluster_seed": item.get("cluster_seed") or item["keyword"],
                "cluster_role": item.get("cluster_role"),
                "week": item["week"],
                "platforms": {p: platform_status(article, p) for p in get_active_platforms()},
                "titles": {p: article["platform_title"].get(p) or "" for p in get_active_platforms()},
            }
        )

    # 행마다: 대표 상태(플랫폼 중 가장 진행된 것), 발행(예약)일, 월 분류, 검색용 문자열
    for r in rows:
        best = max(r["platforms"].values(), key=lambda c: _SIM_STATE_RANK[c["level"]], default=None)
        r["level"] = best["level"] if best else "empty"
        dates = sorted(c["date"] for c in r["platforms"].values() if c["level"] in ("published", "scheduled") and c["date"])
        r["date"] = dates[-1] if dates else ""
        # 발행일이 없는 글은 주차(월요일 날짜) 기준의 월로 분류합니다.
        r["month"] = (r["date"] or r["week"])[:7]
        r["month_from_week"] = not r["date"]
        r["search_key"] = similarity.normalize(r["keyword"] + " " + " ".join(t for t in r["titles"].values() if t))

    all_months = sorted({r["month"] for r in rows}, reverse=True)

    month = request.args.get("month", "")
    if month not in all_months:
        month = ""
    query = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all")
    if status_filter not in ("all", "published", "scheduled", "generated", "empty"):
        status_filter = "all"
    sort_mode = request.args.get("sort", "cluster")
    if sort_mode not in ("cluster", "newest", "oldest"):
        sort_mode = "cluster"

    total_count = len(rows)
    if month:
        rows = [r for r in rows if r["month"] == month]
    norm_q = similarity.normalize(query)
    if norm_q:
        rows = [r for r in rows if norm_q in r["search_key"]]
    if status_filter != "all":
        rows = [r for r in rows if r["level"] == status_filter]

    filter_mode = request.args.get("filter", "all")  # 예전 주소(?filter=incomplete)도 계속 동작
    if filter_mode == "incomplete":
        rows = [r for r in rows if any(c["level"] == "empty" for c in r["platforms"].values())]

    if sort_mode == "cluster":
        # 클러스터별로 묶어서 보여주되, 클러스터 안에서는 승인 순서(대표 먼저) 유지
        rows.sort(key=lambda r: (r["cluster_seed"], 0 if r["cluster_role"] == "대표" else 1))
    else:
        # 발행일순은 묶음을 풀고 평평하게. 발행일이 없는 글은 (양쪽 정렬 모두) 뒤쪽에 주차순으로 둡니다.
        rev = sort_mode == "newest"
        dated = sorted((r for r in rows if r["date"]), key=lambda r: (r["date"], r["week"]), reverse=rev)
        undated = sorted((r for r in rows if not r["date"]), key=lambda r: r["week"], reverse=rev)
        rows = dated + undated

    return render_template(
        "status.html",
        rows=rows,
        platforms=get_active_platforms(),
        platform_labels=PLATFORM_LABELS,
        filter_mode=filter_mode,
        all_months=all_months,
        f_month=month,
        f_q=query,
        f_status=status_filter,
        f_sort=sort_mode,
        total_count=total_count,
        sim=get_similarity(queue, load_articles()),
    )


# ---------- 플랫폼별 개별 발행 이력 페이지 ----------

@app.route("/platform/<platform>")
def platform_history(platform):
    if platform not in get_active_platforms():
        return redirect(url_for("status"))

    queue = load_queue()
    approved_items = [item for item in queue if item.get("approved")]
    seen_ids = set()

    published_entries = []
    for item in approved_items:
        if item["id"] in seen_ids:
            continue
        seen_ids.add(item["id"])
        article = get_article(item["id"])
        st = platform_status(article, platform)
        if st["level"] != "published":
            continue  # 예약 중이거나 아직 발행 전인 건 이 이력 페이지에는 안 보여줌
        published_entries.append(
            {
                "id": item["id"],
                "keyword": item["keyword"],
                "title": article["platform_title"].get(platform) or "",
                "cluster_seed": item.get("cluster_seed") or item["keyword"],
                "cluster_role": item.get("cluster_role"),
                "published_at": article["platform_published_at"].get(platform) or "",
            }
        )

    total = len(published_entries)

    # 클러스터(유사 키워드)별로 묶기
    groups: dict[str, list[dict]] = {}
    for entry in published_entries:
        groups.setdefault(entry["cluster_seed"], []).append(entry)

    group_list = []
    for seed, entries in groups.items():
        entries.sort(key=lambda e: e["published_at"], reverse=True)
        latest = entries[0]["published_at"] if entries else ""
        count = len(entries)
        group_list.append(
            {
                "seed": seed,
                "entries": entries,
                "count": count,
                "percentage": round(count / total * 100) if total else 0,
                "latest": latest,
            }
        )

    # 가장 최근에 뭔가 발행된 클러스터가 위로 오도록 정렬 (발행일 최신순)
    group_list.sort(key=lambda g: g["latest"], reverse=True)

    return render_template(
        "platform_history.html",
        platform=platform,
        platform_label=PLATFORM_LABELS[platform],
        groups=group_list,
        total=total,
    )


def _target_cat1_options() -> list[tuple[str, str]]:
    """타깃 키워드 발굴의 분야 1단계 목록 (shopping_categories.json의 최상위 분야)."""
    try:
        with open("shopping_categories.json", "r", encoding="utf-8") as f:
            names = json.load(f).get("cid_names", {})
    except (OSError, ValueError):
        names = {}
    return list(names.items())


@app.route("/target-keywords", methods=["GET", "POST"])
def target_keywords():
    """타깃(성별·연령) 키워드 발굴 — 보기 전용. 큐·글·발행에는 아무것도 쓰지 않습니다."""
    import target_keyword_finder as tkf

    cat1_options = _target_cat1_options()
    form = request.form
    # GET으로 처음 열 때: 가장 최근 이력의 조회 조건을 기본값으로 (읽기만 함, 실패하면 조용히 기본값)
    last = {}
    if request.method != "POST":
        try:
            recent = tkf.history_list()
            last = ((tkf.history_get(recent[0]["id"]) or {}).get("form") or {}) if recent else {}
            if not isinstance(last, dict):
                last = {}
        except Exception:
            last = {}
    cat1_ids = [c for c, _ in cat1_options]
    default_cat1 = cat1_ids[0] if cat1_ids else ""
    if request.method != "POST":
        if "50000008" in cat1_ids:
            default_cat1 = "50000008"
        # 이력 값이 문자열·목록이 아니면(손상된 데이터) 무시하고 기본값 사용
        last = {k: v for k, v in last.items() if isinstance(v, (str, int, list)) and (k != "ages" or all(isinstance(a, str) for a in v))}
        for k in ("cat1", "cid", "gender", "period", "device"):
            if not isinstance(last.get(k), str):
                last.pop(k, None)
        last_ages = last.get("ages")
        default_ages = [a for a in last_ages if a in tkf.AGE_GROUPS] if isinstance(last_ages, list) and last else ["40", "50"]
        cat1_ok = last.get("cat1") in cat1_ids
        if cat1_ok:
            default_cat1 = last["cat1"]
        defaults = {
            "cat2": str(last.get("cat2") or "").strip() if cat1_ok else "",
            "cat3": str(last.get("cat3") or "").strip() if cat1_ok else "",
            "cid": re.sub(r"\D", "", str(last.get("cid") or "")) if cat1_ok else "",
            "gender": last.get("gender") if last.get("gender") in tkf.GENDERS else "",
            "period": last.get("period") if last.get("period") in tkf.PERIODS else "3m",
            "device": last.get("device") if last.get("device") in tkf.DEVICES else "",
        }
    else:
        default_ages = ["40", "50"]
        defaults = {"cat2": "", "cat3": "", "cid": "", "gender": "", "period": "3m", "device": ""}
    f = {
        "cat1": form.get("cat1") or default_cat1,
        "cat2": form.get("cat2", defaults["cat2"]).strip(),
        "cat3": form.get("cat3", defaults["cat3"]).strip(),
        "cid": re.sub(r"\D", "", form.get("cid", defaults["cid"])),
        "gender": form.get("gender", defaults["gender"]) if form.get("gender", defaults["gender"]) in tkf.GENDERS else "",
        "period": form.get("period", defaults["period"]) if form.get("period", defaults["period"]) in tkf.PERIODS else "3m",
        "device": form.get("device", defaults["device"]) if form.get("device", defaults["device"]) in tkf.DEVICES else "",
        "ages": [a for a in form.getlist("ages") if a in tkf.AGE_GROUPS] if request.method == "POST" else default_ages,
        "target_text": form.get("target_text", ""),
        "full_text": form.get("full_text", ""),
    }
    try:
        f["count"] = max(1, min(int(form.get("count") or last.get("count") or tkf.DEFAULT_COUNT), tkf.MAX_COUNT))
    except (ValueError, TypeError):
        f["count"] = tkf.DEFAULT_COUNT

    result, label = None, ""
    if request.method == "POST":
        cid = f["cid"] or f["cat1"]
        cat1_name = dict(cat1_options).get(f["cat1"], "")
        label = " > ".join(p for p in (cat1_name, f["cat2"], f["cat3"]) if p)
        label += f" · {tkf.GENDERS[f['gender']]} · {'·'.join(a + '대' for a in f['ages']) or '전 연령'} · {tkf.PERIODS[f['period']][0]} · {tkf.DEVICES[f['device']]}"
        result = tkf.build_rows(
            target_text=f["target_text"], full_text=f["full_text"], cid=cid, gender=f["gender"],
            ages=f["ages"], period=f["period"], device=f["device"], count=f["count"],
        )
        if result["rows"]:
            hid = tkf.history_add(f, label, result)
            return redirect(url_for("target_keywords_history_open", hid=hid, fresh=1))
    return _render_target_keywords(f, result, label, cat1_options, "", [])


def _target_annotate(rows: list[dict], week_info: dict, week: str | None = None) -> dict:
    """저장된 행에 추천 점수·상태·이유를 매깁니다 (화면을 그릴 때마다 계산, 저장 없음 — 읽기만). week = 칸 수·상한을 계산할 주차."""
    import target_keyword_finder as tkf
    import serp_check
    return tkf.annotate(rows, used_index=tkf.load_used_index(), week_info=week_info,
                        categories=_v4_categories(), aliases=_v4_aliases(), week=week,
                        serp_lookup=serp_check.cached_lookup())  # 옛 이력은 serp_cache.json(7일)에서 읽기만 함


COPY_RESERVE_MAX = 8  # 기획 채팅용 목록에 싣는 예비(☆) 행 수 — 블로그별 최대

# ---------- 🎯·📤 "기존 글" 칸 (표시만 — blog_overlap의 단어 겹침 계산을 그대로 쓰고, 점수·추천·칸 채우기에는 영향 없음. 저장 없음) ----------
OVERLAP_SHOW_MAX = 3
OVERLAP_CHANNEL = {"naver": "naver_tips", "ssansegae": "wp_tips"}


def _overlap_items() -> dict:
    """{블로그: 비교 대상 항목} — 같은 블로그의 승인됐거나 원고·제목이 있는 항목 (v4_excluded 제외). blog_overlap이 파일 수정 시각으로 캐시합니다."""
    excluded = _load_v4_excluded()
    return {b: blog_overlap.load_items(b, _blog_channels(), _read_text_retry, excluded) for b in blog_overlap.BLOGS}


def _overlap_find(items_by_blog: dict, blog: str, keyword: str, ptype: str, item_id=None, channel=None) -> list:
    """한 키워드(블로그 하나 기준)와 겹치는 기존 글. 꼬리표 유형이 그 블로그의 숍글 유형(네이버 C·싼세계 결정)이면 숍글로 봅니다. item_id = 자기 자신 제외용."""
    import target_keyword_finder as tkf
    target = {"id": item_id, "channel": channel or OVERLAP_CHANNEL[blog], "keyword": keyword, "cluster_seed": None,
              "is_shop": ptype == tkf.BLOGS[blog]["shop_type"]}
    return blog_overlap.find_overlaps(target, items_by_blog.get(blog, []))


def _overlap_state_short(label: str) -> str:
    """"발행 2026-10-20" → "발행 10/20", "승인만 (원고 없음)" → "승인만"."""
    m = re.match(r"(발행|예약) \d{4}-(\d{2})-(\d{2})", label or "")
    if m:
        return f"{m.group(1)} {int(m.group(2))}/{int(m.group(3))}"
    return "승인만" if (label or "").startswith("승인만") else (label or "")


def _overlap_view(e: dict) -> dict:
    """겹침 한 건 → 화면용 {icon, text, ...}. 🔴/🟡 = 중복 위험(같은 종류), 🔗 = 숍↔일반 짝 글."""
    pair = e["relation"] == blog_overlap.RELATION_PAIR
    icon = "🔗" if pair else ("🔴" if e["level"] == "red" else "🟡")
    name = (e.get("title") or e.get("keyword") or "").strip()
    name = name[:20] + ("…" if len(name) > 20 else "")
    state = _overlap_state_short(e.get("state", ""))
    return {"icon": icon, "name": name, "state": state, "text": f"{icon} {name} ({state})"}


def _overlap_text(entries: list) -> str:
    """"🟡 울 니트가 줄어드는… (발행 10/20); 🔗 … 외 2개" — 최대 OVERLAP_SHOW_MAX개, 없으면 "-"."""
    if not entries:
        return "-"
    views = [_overlap_view(e)["text"] for e in entries[:OVERLAP_SHOW_MAX]]
    more = len(entries) - OVERLAP_SHOW_MAX
    return "; ".join(views) + (f" 외 {more}개" if more > 0 else "")


def _target_overlap_view(ann_rows: list, items_by_blog: dict | None = None) -> tuple[dict, dict]:
    """🎯 결과 표용: ({키워드: [{blog, label, shown:[{icon,text}], more}]}, {"rows","red","yellow","link"}). 줄마다 가장 센 표시 하나로 세어 합이 rows와 같습니다 (🔴 > 🟡 > 🔗)."""
    import target_keyword_finder as tkf
    items_by_blog = items_by_blog if items_by_blog is not None else _overlap_items()
    view, counts = {}, {"red": 0, "yellow": 0, "link": 0}
    for r in ann_rows:
        per, top = [], 3
        for asg in r.get("assign") or []:
            ents = _overlap_find(items_by_blog, asg["blog"], r["keyword"], asg.get("type", ""))
            if not ents:
                continue
            vs = [_overlap_view(e) for e in ents]
            per.append({"blog": asg["blog"], "label": tkf.BLOGS[asg["blog"]]["label"], "shown": vs[:OVERLAP_SHOW_MAX],
                        "more": max(0, len(vs) - OVERLAP_SHOW_MAX)})
            for v in vs:
                top = min(top, {"🔴": 0, "🟡": 1, "🔗": 2}[v["icon"]])
        if per:
            view[r["keyword"]] = per
            counts[("red", "yellow", "link")[top]] += 1
    return view, dict(counts, rows=len(view))


def _target_copy_text(hid: str, week: str, held: set | None = None) -> str:
    """기획 채팅에 붙여넣을 승인 목록 글 (읽기만 합니다). 승인됨 = 그 주 두 블로그(+같은 블로그의 옛 숍 채널) 승인 항목 전체,
    예비 = 이 이력의 ☆예비 행 중 점수 높은 순 블로그별 최대 COPY_RESERVE_MAX개. held = 🔴 보류한 (블로그, 키워드) — 목록에서 뺍니다."""
    import target_keyword_finder as tkf
    held = held or set()
    hist = tkf.history_get(hid) or {}
    hist_rows = {r["keyword"]: r for r in ((hist.get("result") or {}).get("rows") or [])}
    info = _target_week_info(week)
    ann = _target_annotate(list(hist_rows.values()), info, week)["rows"]

    def clean(v) -> str:
        return re.sub(r"\s+", " ", str(v)).replace("|", "/").strip()

    items_by_blog = _overlap_items()

    def fmt(kw, blog_label, ptype, cat, vol, rise, peak, compete, note, reason, existing="-") -> str:
        # 키워드 | 블로그 | 유형 | 카테고리 | 월검색량 | 상승세 | 작년 정점 | 경쟁 | 검색 확인 | 추천 이유 | 기존 글
        vol_s = "-" if vol is None else f"{vol:,}".replace(",", "")
        rise_s = "-" if not isinstance(rise, (int, float)) else f"{rise:+.0f}%"
        return " | ".join(clean(x) for x in (kw, blog_label, ptype or "미정", cat or "미정", vol_s, rise_s, peak or "-", compete or "-", note or "-", reason or "-", existing or "-"))

    lines = [f"[승인 목록] 주차 {week}", "", "## 승인됨"]
    approved_kws: set = set()
    for blog, ch, label in TARGET_APPROVE_BLOGS:
        for cid in (ch, TARGET_OLD_CHANNELS[blog]):
            queue, _ = _read_channel_data(cid)
            for i in queue:
                if i.get("week") != week or not i.get("approved"):
                    continue
                kw = i.get("keyword", "")
                approved_kws.add((blog, kw))
                row = hist_rows.get(kw) or {}
                vol = tkf.vol_sum(row) if row else i.get("total_search_volume")
                if vol is None:
                    vol = i.get("total_search_volume")
                peak, comp, note = _serp_cells(i)
                ovl = _overlap_text(_overlap_find(items_by_blog, blog, kw, i.get("planned_type"), item_id=i.get("id"), channel=cid))
                lines.append(fmt(kw, label, i.get("planned_type"), i.get("planned_category"), vol, row.get("rising_pct"), peak, comp, note, i.get("rec_reason"), ovl))
    lines += ["", "## 예비(승인 안 함)"]
    for blog, ch, label in TARGET_APPROVE_BLOGS:
        picked = []
        for r in ann:  # ann은 점수순 (제외 행은 뒤)
            asg = next((x for x in r["assign"] if x["blog"] == blog and x["status"] == "reserve"), None)
            if asg and (blog, r["keyword"]) not in approved_kws and (blog, r["keyword"]) not in held:
                picked.append((r, asg))
        picked.sort(key=lambda p: -(p[0]["score"] if p[0]["score"] is not None else -9999))
        for r, asg in picked[:COPY_RESERVE_MAX]:
            lines.append(fmt(r["keyword"], label, asg["type"], asg["category"], r["vol"], r.get("rising_pct"),
                             (r.get("sv") or {}).get("peak_text"), "-", "-", r["reason"],
                             _overlap_text(_overlap_find(items_by_blog, blog, r["keyword"], asg["type"]))))

    def agg(blog, names) -> str:
        wi = info[blog]
        parts = "·".join(f"{n} {wi['types'][n]}" for n in names) + f"·미정 {wi['undecided']}"
        return f"{wi['label']} 승인 {wi['approved']}/목표 {wi['target']} ({parts})"

    lines += ["", agg("naver", ("A", "B", "C")) + " · " + agg("ssansegae", ("해결", "결정"))]
    return "\n".join(lines)


def _render_target_keywords(f, result, label, cat1_options, hid, picked, saved_at="", draft=None):
    import target_keyword_finder as tkf
    import serp_check
    next_week = engine.shift_week(engine.get_week_label(), 1)
    has_rows = bool(result and result.get("rows"))
    week_info = _target_week_info(next_week) if (hid and has_rows) else {}
    rec = _target_annotate(result["rows"], week_info, next_week) if has_rows else None
    ovl, ovl_sum = _target_overlap_view(rec["rows"]) if rec else ({}, None)  # "기존 글" 칸 — 화면을 그릴 때마다 계산, 표시만
    carry = None  # 새 조회가 같은 주 이전 조회의 체크를 이어받았다는 안내
    if hid and has_rows and isinstance(draft, dict) and isinstance(draft.get("carried"), dict):
        c = draft["carried"]
        try:
            wk = datetime.strptime(draft.get("week", ""), "%Y-%m-%d")
            week_md = f"{wk.month}/{wk.day}"
        except ValueError:
            week_md = draft.get("week", "")
        carry = dict(c, week_md=week_md, hm=str(c.get("ts", ""))[11:16])
    return render_template(
        "target_keywords.html", f=f, result=result, label=label, cat1_options=cat1_options,
        genders=tkf.GENDERS, periods=tkf.PERIODS, devices=tkf.DEVICES, age_groups=tkf.AGE_GROUPS,
        default_count=tkf.DEFAULT_COUNT, max_count=tkf.MAX_COUNT, detail_top_n=tkf.DETAIL_TOP_N,
        hid=hid, picked=picked, saved_at=saved_at,
        # 주간 키워드 승인 영역(결과 표가 있고 저장된 이력일 때만 화면에 나옵니다) — 여기서는 읽기만 합니다
        this_week=engine.get_week_label(), next_week=next_week,
        week_info=week_info,
        draft=draft if (hid and has_rows) else None,  # 작업 중 자동 저장본 — 화면에서 체크·유형·경쟁·메모·주차를 복원 (읽기만)
        approved_kws=_target_approved_kws(next_week) if (hid and has_rows) else {},  # 그 주 이미 승인된 키워드 — "✓ 승인됨" 표시용
        copy_text=_target_copy_text(hid, next_week) if (hid and has_rows) else "",
        ovl=ovl, ovl_sum=ovl_sum, carry=carry,
        rec=rec, blogs=tkf.BLOGS, rec_consts={
            "excl": tkf.VOL_EXCLUDE_BELOW, "reserve": tkf.VOL_RESERVE_BELOW, "weak": tkf.VOL_WEAK_ABOVE, "broad": tkf.VOL_BROAD_ABOVE,
        },
        serp_ok=serp_check.configured(),
    )


@app.route("/target-keywords/history")
def target_keywords_history():
    """서칭 이력 목록 — 언제 어떤 조건·키워드로 조회했는지."""
    import target_keyword_finder as tkf
    return render_template("target_keywords_history.html", items=tkf.history_list())


@app.route("/target-keywords/history/<hid>")
def target_keywords_history_open(hid):
    """저장된 조회 결과를 그대로 다시 엽니다 (외부 조회 없음)."""
    import target_keyword_finder as tkf
    it = tkf.history_get(hid)
    if not it:
        return redirect(url_for("target_keywords_history"))
    f = it["form"]
    f.setdefault("count", tkf.DEFAULT_COUNT)
    draft = it.get("draft")
    if not draft and (it.get("result") or {}).get("rows"):
        # 새 조회로 새 이력이 생겨 처음 열 때: 같은 주(다음 주) 이전 조회의 승인 안 한 체크를 이어받아 draft 칸에만 저장 (읽기·계산은 carry_draft)
        wk = engine.shift_week(engine.get_week_label(), 1)
        got = tkf.carry_draft(tkf._read_history(), hid, wk, _target_approved_kws(wk))
        if got and tkf.history_update(hid, draft=got["draft"]):
            draft = got["draft"]
    return _render_target_keywords(f, it["result"], it["label"], _target_cat1_options(), hid,
                                   it.get("picked", []), saved_at=it["ts"], draft=draft)


@app.route("/target-keywords/history/<hid>/draft", methods=["POST"])
def target_keywords_history_draft(hid):
    """작업 중 체크 상태(승인 체크·유형·경쟁·검색 확인 메모·주차)를 이 이력의 draft 칸에만 저장합니다.
    대기열·글·발행에는 아무것도 쓰지 않습니다. 이상한 값은 걸러 냅니다."""
    import target_keyword_finder as tkf
    it = tkf.history_get(hid)
    data = request.get_json(silent=True)
    if not it or not isinstance(data, dict):
        return {"ok": False}, 400
    week = _target_week(data.get("week") if isinstance(data.get("week"), str) else None)
    if not week:
        return {"ok": False, "error": "week"}, 400
    known = {r.get("keyword") for r in ((it.get("result") or {}).get("rows") or [])}
    raw_rows = data.get("rows") if isinstance(data.get("rows"), dict) else {}
    rows: dict = {}
    for kw, v in raw_rows.items():
        if kw not in known or not isinstance(v, dict):
            continue  # 이 이력에 없는 키워드는 버립니다
        blogs: dict = {}
        raw_blogs = v.get("blogs") if isinstance(v.get("blogs"), dict) else {}
        for b, spec in raw_blogs.items():
            if b not in tkf.BLOGS or not isinstance(spec, dict) or not spec.get("on"):
                continue
            t = spec.get("type")
            blogs[b] = {"on": True, "type": t if t in tkf.BLOGS[b]["types"] else ""}
        compete = v.get("compete") if v.get("compete") in TARGET_COMPETE_VALUES else ""
        note = re.sub(r"\s+", " ", str(v.get("note") or "")).strip()[:40]
        if blogs or compete or note:
            rows[kw] = {"blogs": blogs, "compete": compete, "note": note}
    draft = {"week": week, "rows": rows, "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
    prev_draft = it.get("draft") if isinstance(it.get("draft"), dict) else {}
    if isinstance(prev_draft.get("carried"), dict) and prev_draft.get("week") == week:
        draft["carried"] = prev_draft["carried"]  # "이전 조회에서 이어받음" 안내는 계속 보이게 유지
    ok = tkf.history_update(hid, draft=draft)  # draft 칸만 바꿉니다 (form·result·picked·starred는 그대로)
    return {"ok": ok, "ts": draft["ts"], "count": len(rows)}


@app.route("/target-keywords/history/<hid>/pick", methods=["POST"])
def target_keywords_history_pick(hid):
    """표에서 체크한 키워드만 이 이력에 '선택 저장'합니다."""
    import target_keyword_finder as tkf
    data = request.get_json(silent=True) or {}
    picked = [str(k) for k in data.get("picked", [])][:500]
    return {"ok": tkf.history_update(hid, picked=picked), "count": len(picked)}


@app.route("/target-keywords/history/<hid>/star", methods=["POST"])
def target_keywords_history_star(hid):
    """★ 표시한 이력은 오래돼도 자동 삭제되지 않습니다."""
    import target_keyword_finder as tkf
    it = tkf.history_get(hid)
    if it:
        tkf.history_update(hid, starred=not it.get("starred", False))
    return redirect(request.referrer or url_for("target_keywords_history"))


@app.route("/target-keywords/history/<hid>/delete", methods=["POST"])
def target_keywords_history_delete(hid):
    import target_keyword_finder as tkf
    tkf.history_delete(hid)
    return redirect(url_for("target_keywords_history"))


# ---------- 🎯 타깃 키워드 → 주간 키워드로 승인 (V5: 네이버 / 싼세계) ----------
# 결과 표에서 키워드마다 [네이버] [싼세계]를 고르고 "주간 키워드로 승인"을 누르면, 그 블로그(naver_tips / wp_tips)의
# 해당 주차 대기열에 승인된 항목으로 들어갑니다. 버튼을 누르기 전에는 아무것도 저장하지 않습니다. 이력 파일에는 쓰지 않습니다.
TARGET_APPROVE_BLOGS = (  # (블로그 키, 채널 id, 화면 이름) — 주당 목표 편수는 v4_board.weekly_plan(블로그, 주차)로 주차마다 계산
    ("naver", "naver_tips", CHANNELS["naver_tips"]["label"]),
    ("ssansegae", "wp_tips", CHANNELS["wp_tips"]["label"]),
)
TARGET_ANGLE_NOTE = "네이버 발행 후, 다른 각도"


def _target_week(raw: str | None) -> str | None:
    """YYYY-MM-DD 날짜를 그 주 월요일 라벨로. 형식이 틀리면 None."""
    try:
        return engine.get_week_label(datetime.strptime((raw or "").strip(), "%Y-%m-%d"))
    except ValueError:
        return None


TARGET_OLD_CHANNELS = {"naver": "shop_naver", "ssansegae": "shop_wp"}  # 같은 블로그의 옛 숍 채널 — "이미 승인" 수에 더해 셉니다 (현황판 목표와 같은 기준)


def _target_week_info(week: str) -> dict:
    """{블로그키: {"approved": 그 주 이미 승인된 수(새 채널 + 같은 블로그의 옛 숍 채널), "target": 목표, "label": 이름,
    "types": {유형: 승인 수}, "undecided": planned_type이 없는 승인 수, "cats": {planned_category: 승인 수}}} — 읽기만 합니다."""
    import target_keyword_finder as tkf
    out = {}
    for blog, ch, label in TARGET_APPROVE_BLOGS:
        plan = v4_board.weekly_plan(blog, week)
        approved, undecided, review = 0, 0, 0
        types = {t: 0 for t in tkf.BLOGS[blog]["types"]}
        cats: dict[str, int] = {}
        for cid in (ch, TARGET_OLD_CHANNELS[blog]):
            queue, _ = _read_channel_data(cid)
            for i in queue:
                if i.get("week") != week or not i.get("approved"):
                    continue
                approved += 1
                if i.get("planned_type") in types:
                    types[i["planned_type"]] += 1
                    if tkf.is_review_item(i, blog, week):
                        review += 1  # 싼세계 리뷰 분석 글 (11/16 주부터 해결 안: 일반 3 · 리뷰 분석 5 / 그 전 결정 안: 제품 추천 6 · 리뷰 분석 5)
                else:
                    undecided += 1
                pc = i.get("planned_category")
                if isinstance(pc, str) and pc:
                    cats[pc] = cats.get(pc, 0) + 1
        out[blog] = {"label": label, "target": plan, "approved": approved, "types": types, "undecided": undecided, "cats": cats, "review": review}
    return out


def _target_approved_kws(week: str) -> dict:
    """{블로그키: [그 주 이미 승인된 키워드]} — _target_week_info와 같은 기준(새 채널 + 옛 숍 채널). 읽기만 합니다."""
    out = {}
    for blog, ch, _label in TARGET_APPROVE_BLOGS:
        kws: set = set()
        for cid in (ch, TARGET_OLD_CHANNELS[blog]):
            queue, _ = _read_channel_data(cid)
            for i in queue:
                if i.get("week") == week and i.get("approved") and i.get("keyword"):
                    kws.add(i["keyword"])
        out[blog] = sorted(kws)
    return out


@app.route("/target-keywords/history/<hid>/rec")
def target_keywords_rec(hid):
    """주차를 바꿀 때 추천 배지·이유·집계 기준값을 다시 받아 가는 조회 전용 주소 (저장 없음)."""
    import target_keyword_finder as tkf
    week = _target_week(request.args.get("week"))
    it = tkf.history_get(hid)
    if not week or not it:
        return {"ok": False}, 400
    info = _target_week_info(week)
    ann = _target_annotate((it.get("result") or {}).get("rows") or [], info, week)
    rows = {r["keyword"]: {
        "status": r["status"], "reason": r["reason"], "score": r["score"], "srank": r["srank"],
        "recs": {b: {"type": x["type"], "category": x["category"]} for b, x in r["recs"].items()},
    } for r in ann["rows"]}
    return {"ok": True, "week": week, "info": info, "config": ann["config"], "rows": rows, "copy_text": _target_copy_text(hid, week),
            "approved_kws": _target_approved_kws(week)}


def _target_row_item_fields(row: dict, hid: str) -> dict:
    vols = [v for v in (row.get("pc"), row.get("mobile")) if isinstance(v, (int, float))]
    return {
        "total_search_volume": sum(vols) if vols else None,
        "competition": row.get("competition") or "타깃발굴",
        "source": "target_keywords",
        "target_hid": hid,
    }


def _target_approve(hid: str, week: str, selected: dict[str, list[str]], skip_red: set | None = None,
                    pair_keywords: set | None = None, ptypes: dict | None = None, serp_notes: dict | None = None,
                    serp_compete: dict | None = None) -> dict:
    """선택한 키워드를 블로그별 대기열에 승인 상태로 넣습니다.
    selected = {"naver": [키워드...], "ssansegae": [...]}. skip_red = {(블로그, 키워드)} — "그래도 추가"로 🔴 확인을 건너뛸 것.
    pair_keywords = 같은 키워드를 네이버에도 선택했던 것(싼세계 "그래도 추가" 때 각도 표시를 이어 붙이려고).
    같은 주에 이미 있는 키워드는 건너뛰고, 🔴(거의 같은 글)은 저장하지 않고 보류로 돌려줍니다.
    ptypes = {블로그: {키워드: 고른 유형}} — 화면에서 고른 계획 유형. 새로 만드는 대기열 항목에만 planned_type·planned_category·rec_score·rec_reason을 붙입니다(기존 항목은 건드리지 않음).
    저장 직전에 대기열을 다시 읽어 합칩니다(그 사이 바뀐 것을 덮어쓰지 않음). 이력 파일에는 쓰지 않습니다."""
    import target_keyword_finder as tkf
    skip_red = skip_red or set()
    ptypes = ptypes or {}
    hist = tkf.history_get(hid) or {}
    rows = {r["keyword"]: r for r in ((hist.get("result") or {}).get("rows") or [])}
    ann_by_kw = {r["keyword"]: r for r in _target_annotate(list(rows.values()), _target_week_info(week), week)["rows"]}
    naver_kws = set(selected.get("naver") or []) | set(pair_keywords or ())
    results: dict = {}
    naver_ids: dict[str, str | None] = {}
    for blog, ch, label in TARGET_APPROVE_BLOGS:
        res = {"blog": blog, "channel": ch, "label": label, "added": [], "exists": [], "held": [], "unknown": []}
        results[blog] = res
        kws = list(dict.fromkeys(selected.get(blog) or []))
        if not kws:
            continue
        queue = _strict_channel_queue(ch)
        working = list(queue)
        _, articles = _read_channel_data(ch)
        to_add: list[dict] = []
        for kw in kws:
            row = rows.get(kw)
            if not row:
                res["unknown"].append(kw)  # 이력에 없는 키워드(화면과 다르게 전송된 값)는 저장하지 않습니다
                continue
            existing = next((i for i in working if i.get("week") == week and i.get("keyword") == kw), None)
            if existing:
                res["exists"].append({"keyword": kw, "id": existing["id"], "approved": bool(existing.get("approved"))})
                if blog == "naver":
                    naver_ids[kw] = existing["id"]
                continue
            ann = ann_by_kw.get(kw) or {}
            asg = next((x for x in ann.get("assign", []) if x["blog"] == blog), None)
            ptype = (ptypes.get(blog) or {}).get(kw)
            if ptype not in tkf.BLOGS[blog]["types"]:
                ptype = (asg or {}).get("type", "")  # 고른 값이 없거나 이상하면 꼬리표 유형(없으면 미정)
            note = re.sub(r"\s+", " ", str((serp_notes or {}).get(kw) or "")).strip()[:40]  # "검색 확인" 메모(선택, 40자)
            compete = (serp_compete or {}).get(kw)
            if compete not in TARGET_COMPETE_VALUES:
                compete = ""
            if (blog, kw) not in skip_red:
                red_matches, blog_dups = _red_checks_for_new_keyword(ch, working, articles, week, kw, kw)
                if red_matches or blog_dups:
                    res["held"].append({"keyword": kw, "matches": red_matches, "blog_matches": blog_dups,
                                        "with_naver": blog == "ssansegae" and kw in naver_kws, "planned_type": ptype,
                                        "serp_note": note, "serp_compete": compete})  # "그래도 추가" 때 다시 넘겨 저장
                    continue
            extra = _target_row_item_fields(row, hid)
            extra["planned_type"] = ptype
            extra.update(tkf.approval_meta(ann, blog) if ann else {"planned_category": "", "rec_score": None, "rec_reason": ""})
            if note:
                extra["serp_note"] = note
            sv = ann.get("sv") or {}
            if sv.get("peak_date") or compete:  # 새 칸 serp (정점 + 사람이 고른 경쟁). 기존 칸은 건드리지 않음
                extra["serp"] = {"peak_week": sv.get("peak_date"), "weeks_to_peak": sv.get("weeks_left"), "compete": compete or None}
            if blog == "ssansegae" and kw in naver_kws:
                nid = naver_ids.get(kw)
                if nid is None:  # 이번 실행에서 못 얻었으면(보류 후 "그래도 추가" 등) 네이버 대기열에서 찾아봅니다
                    nq, _ = _read_channel_data("naver_tips")
                    nid = next((i["id"] for i in nq if i.get("week") == week and i.get("keyword") == kw), None)
                extra["angle_note"] = TARGET_ANGLE_NOTE
                extra["pair_ref"] = {"channel": "naver_tips", "id": nid, "keyword": kw, "week": week}
            item = _make_custom_item(kw, week, kw, "대표", **extra)
            _insert_custom_item(working, item)
            to_add.append(item)
            if blog == "naver":
                naver_ids[kw] = item["id"]
        if to_add:
            fresh = _strict_channel_queue(ch)  # 저장 직전에 다시 읽어서 합칩니다
            for item in to_add:
                dup = next((i for i in fresh if i.get("week") == week and i.get("keyword") == item["keyword"]), None)
                if dup:
                    res["exists"].append({"keyword": item["keyword"], "id": dup["id"], "approved": bool(dup.get("approved"))})
                    if blog == "naver":
                        naver_ids[item["keyword"]] = dup["id"]
                    continue
                _insert_custom_item(fresh, item)
                res["added"].append({"keyword": item["keyword"], "id": item["id"], "angle": bool(item.get("angle_note"))})
            if res["added"]:
                _write_json_atomic(CHANNELS[ch]["queue_file"], fresh)
    return results


def _render_target_approve_result(hid: str, week: str, results: dict):
    held = {(b, h["keyword"]) for b, r in results.items() for h in r.get("held", [])}
    return render_template("target_approve_result.html", hid=hid, week=week, results=results, angle_note=TARGET_ANGLE_NOTE,
                           copy_text=_target_copy_text(hid, week, held))


@app.route("/target-keywords/approve", methods=["POST"])
def target_keywords_approve():
    """"주간 키워드로 승인" — 체크한 키워드를 블로그별 주간 대기열에 승인 상태로 넣고 결과를 보여줍니다."""
    hid = request.form.get("hid", "")
    week = _target_week(request.form.get("week"))
    selected = {"naver": request.form.getlist("naver"), "ssansegae": request.form.getlist("ssansegae")}
    if not hid or not week or not (selected["naver"] or selected["ssansegae"]):
        return redirect(url_for("target_keywords_history_open", hid=hid) if hid else url_for("target_keywords_history"))
    return _render_target_approve_result(hid, week, _target_approve(hid, week, selected, ptypes=_target_parse_ptypes(request.form.get("ptypes", "")),
                                                                    serp_notes=_target_parse_serp_notes(request.form.get("serp_notes", "")),
                                                                    serp_compete=_target_parse_serp_notes(request.form.get("serp_compete", ""))))


TARGET_COMPETE_VALUES = ("green", "yellow", "red")  # 경쟁 직접 선택: 🟢 해볼 만함 / 🟡 보통 / 🔴 어려움
COMPETE_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def _serp_cells(item: dict) -> tuple[str, str, str]:
    """대기열 항목의 새 칸 serp·serp_note → (작년 정점 글, 경쟁 아이콘, 검색 확인 메모). 없으면 "-". 읽기만 합니다."""
    import target_keyword_finder as tkf
    sp = item.get("serp") if isinstance(item.get("serp"), dict) else {}
    peak = tkf.serp_view({"peak_date": sp.get("peak_week")})["peak_text"] if sp.get("peak_week") else "-"
    return peak, COMPETE_ICON.get(sp.get("compete"), "-"), (item.get("serp_note") or "-")


def _target_parse_serp_notes(raw: str) -> dict:
    """화면의 "검색 확인" 메모 {키워드: 글} (JSON 글). 모양이 이상하면 빈 값."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _target_parse_ptypes(raw: str) -> dict:
    """화면에서 고른 계획 유형 {블로그: {키워드: 유형}} (JSON 글). 모양이 이상하면 빈 값 — 유효한 유형인지는 _target_approve가 다시 확인합니다."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {b: {str(k): str(v) for k, v in m.items()} for b, m in data.items() if isinstance(m, dict)}


@app.route("/target-keywords/approve-one", methods=["POST"])
def target_keywords_approve_one():
    """결과 화면에서 🔴 보류한 키워드 한 줄을 "그래도 추가" — 🔴 확인만 건너뛰고 나머지(같은 주 중복 확인 등)는 그대로입니다."""
    hid = request.form.get("hid", "")
    week = _target_week(request.form.get("week"))
    blog = request.form.get("blog", "")
    keyword = request.form.get("keyword", "")
    if blog not in {b[0] for b in TARGET_APPROVE_BLOGS} or not (hid and week and keyword):
        return redirect(url_for("target_keywords_history"))
    pair = {keyword} if request.form.get("with_naver") == "1" and blog == "ssansegae" else set()
    results = _target_approve(hid, week, {blog: [keyword]}, skip_red={(blog, keyword)}, pair_keywords=pair,
                              ptypes={blog: {keyword: request.form.get("planned_type", "")}},
                              serp_notes={keyword: request.form.get("serp_note", "")},
                              serp_compete={keyword: request.form.get("serp_compete", "")})
    return _render_target_approve_result(hid, week, results)


@app.route("/target-keywords/approve-bulk", methods=["POST"])
def target_keywords_approve_bulk():
    """결과 화면의 🔴 보류 카드를 한 번에 "그래도 추가" (같은 화면에서 JS가 호출, JSON 응답).
    요청: {hid, week, items:[{blog, keyword, with_naver, planned_type, serp_note, serp_compete}], still_held:[{blog, keyword}]}
    키워드마다 따로 저장하므로 일부가 실패해도 나머지는 그대로 저장됩니다. 같은 주·같은 블로그에 이미 있으면 저장하지 않고 "exists"."""
    data = request.get_json(silent=True) or {}
    hid = str(data.get("hid") or "")
    week = _target_week(data.get("week"))
    items = data.get("items")
    blogs = {b[0] for b in TARGET_APPROVE_BLOGS}
    if not (hid and week and isinstance(items, list) and items):
        return {"ok": False, "error": "추가할 키워드가 없습니다."}, 400
    out = []
    seen = set()
    for it in items[:100]:
        if not isinstance(it, dict):
            continue
        blog = str(it.get("blog") or "")
        kw = str(it.get("keyword") or "")
        if blog not in blogs or not kw or (blog, kw) in seen:
            out.append({"blog": blog, "keyword": kw, "status": "failed", "message": "잘못된 요청(블로그·키워드 확인 필요)"})
            continue
        seen.add((blog, kw))
        pair = {kw} if it.get("with_naver") and blog == "ssansegae" else set()
        try:
            res = _target_approve(hid, week, {blog: [kw]}, skip_red={(blog, kw)}, pair_keywords=pair,
                                  ptypes={blog: {kw: str(it.get("planned_type") or "")}},
                                  serp_notes={kw: str(it.get("serp_note") or "")},
                                  serp_compete={kw: str(it.get("serp_compete") or "")})[blog]
        except Exception as e:  # 한 개가 실패해도 나머지는 계속
            out.append({"blog": blog, "keyword": kw, "status": "failed", "message": f"저장 실패: {e}"})
            continue
        if res["added"]:
            out.append({"blog": blog, "keyword": kw, "status": "added", "message": "✅ 추가됨"})
        elif res["exists"]:
            appr = bool(res["exists"][0].get("approved"))  # 승인 전이면 화면에서 다른 문구로 표시
            out.append({"blog": blog, "keyword": kw, "status": "exists", "approved": appr,
                        "message": "이미 추가됨" if appr else "이미 대기열에 있음 (승인 전)"})
        elif res["unknown"]:
            out.append({"blog": blog, "keyword": kw, "status": "failed", "message": "이 이력에 없는 키워드라 저장하지 않았습니다."})
        else:
            out.append({"blog": blog, "keyword": kw, "status": "failed", "message": "저장되지 않았습니다."})
    held = set()
    for h in data.get("still_held") or []:
        if isinstance(h, dict) and h.get("blog") in blogs and h.get("keyword"):
            held.add((str(h["blog"]), str(h["keyword"])))
    try:
        copy_text = _target_copy_text(hid, week, held)
    except Exception:
        copy_text = None  # 목록 갱신만 못 할 뿐 저장 결과에는 영향 없음
    return {"ok": True, "results": out, "copy_text": copy_text}


# ---------- 📈 성과 루프 1단계: 서치콘솔 성과 보기 (읽기 전용) ----------

def _perf_specs() -> dict:
    """성과 화면이 읽는 워드프레스 3채널의 파일 이름(읽기만 합니다)."""
    return {
        ch: {"articles_file": CHANNELS[ch]["articles_file"], "queue_file": CHANNELS[ch]["queue_file"]}
        for ch, _ in perf_tracker.TABS
    }


def _perf_tab(raw: str) -> str:
    return raw if raw in dict(perf_tracker.TABS) else perf_tracker.TABS[0][0]


def _render_performance(tab: str, test_result=None, force_mock: bool = False):
    articles = perf_tracker.load_all_articles(_perf_specs())
    cache = None if force_mock else perf_tracker.load_cache()
    if force_mock or not perf_tracker.key_available() or (cache is None and session.get("perf_failed")):
        mode = "mock"
        cache = perf_tracker.build_mock_cache(articles)  # 가짜 숫자 — 파일에 저장하지 않습니다
    elif cache is None:
        mode = "empty"  # 키는 있는데 아직 한 번도 안 가져옴
    else:
        mode = "real"
    index = perf_tracker.build_mock_index(articles) if mode == "mock" else perf_tracker.load_index()  # 목업 결과는 저장하지 않습니다
    view = perf_tracker.build_view(cache, articles, index=index, requests=perf_tracker.load_requests()) if mode != "empty" else None
    flash = session.pop("perf_msg", None)
    return render_template(
        "performance.html", tab=tab, tabs=perf_tracker.TABS, mode=mode, view=view, cache=cache, test_result=test_result,
        flash=flash, force_mock=force_mock, key_found=perf_tracker.key_available(), tag_hints=perf_tracker.TAG_HINTS,
        site_labels=perf_tracker.SITE_LABELS, channel_site=perf_tracker.CHANNEL_SITE, T=perf_tracker,
    )


@app.route("/performance")
def performance():
    return _render_performance(_perf_tab(request.args.get("tab", "")), force_mock=request.args.get("mock") == "1")


@app.route("/performance/fetch", methods=["POST"])
def performance_fetch():
    """"지금 가져오기" — 서치콘솔에서 읽기만 하고, 결과를 perf_cache.json / perf_history.json에 저장합니다."""
    ok, msg = perf_tracker.fetch_all(_perf_specs())
    session["perf_msg"] = {"ok": ok, "text": msg}
    session["perf_failed"] = not ok
    return redirect(url_for("performance", tab=_perf_tab(request.form.get("tab", ""))))


@app.route("/performance/index-check", methods=["POST"])
def performance_index_check():
    """"색인 상태 확인" — 서치콘솔 URL 검사 API로 조회만 하고, 결과를 perf_index.json에 저장합니다."""
    ok, msg = perf_tracker.run_index_check(_perf_specs())
    session["perf_msg"] = {"ok": ok, "text": msg}
    return redirect(url_for("performance", tab=_perf_tab(request.form.get("tab", ""))))


@app.route("/performance/index-request", methods=["POST"])
def performance_index_request():
    """[요청함 ✓] 켜기/끄기 — 화면 표시용 기록만 perf_index_requests.json에 저장합니다(서치콘솔에는 아무것도 보내지 않습니다)."""
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    key = str(data.get("key", ""))
    ch, _, aid = key.partition(":")
    arts = perf_tracker.load_all_articles(_perf_specs()).get(ch, [])
    if not any(a["id"] == aid for a in arts):
        return jsonify({"ok": False, "error": "알 수 없는 글입니다"}), 400
    return jsonify({"ok": True, "requested_at": perf_tracker.set_request(key, bool(data.get("on")))})


@app.route("/performance/test", methods=["POST"])
def performance_test():
    """연결 테스트 — 사이트 주소 형식·페이지 수·글 연결 수만 확인합니다(파일 저장 없음)."""
    return _render_performance(_perf_tab(request.form.get("tab", "")), test_result=perf_tracker.test_connection(_perf_specs()))


# ───────── 제품 체크 (tools/product_candidates.py 결과를 보고 고르는 화면) ─────────
_PC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline", "candidates_run")


def _pc_runs() -> list[str]:
    """candidates.json 이 있는 결과 폴더 이름, 최신순."""
    if not os.path.isdir(_PC_ROOT):
        return []
    names = [n for n in os.listdir(_PC_ROOT) if os.path.isfile(os.path.join(_PC_ROOT, n, "candidates.json"))]
    names.sort(key=lambda n: os.path.getmtime(os.path.join(_PC_ROOT, n, "candidates.json")), reverse=True)
    return names


def _pc_safe_run(name: str) -> str | None:
    return name if name in _pc_runs() else None


@app.route("/product-check")
def product_check():
    runs = _pc_runs()
    run = _pc_safe_run(request.args.get("run", "")) or (runs[0] if runs else None)
    data = approved = None
    links = {}
    if run:
        with open(os.path.join(_PC_ROOT, run, "candidates.json"), encoding="utf-8") as f:
            data = json.load(f)
        lp = os.path.join(_PC_ROOT, run, "links.json")  # tools/issue_links.py 가 만든 결과(읽기만)
        if os.path.isfile(lp):
            with open(lp, encoding="utf-8") as f:
                links = {x.get("product_id"): x for x in json.load(f).get("items", []) if x.get("product_id")}
        ap = os.path.join(_PC_ROOT, run, "approved.json")
        if os.path.isfile(ap):
            with open(ap, encoding="utf-8") as f:
                approved = json.load(f)
    return render_template("product_check.html", runs=runs, run=run, data=data, approved=approved, links=links)


@app.route("/product-check/img/<run>/<path:fname>")
def product_check_img(run, fname):
    if not _pc_safe_run(run) or not re.fullmatch(r"img/[\w\-]+\.(jpg|png|jpeg|webp)", fname):
        return ("", 404)
    return send_file(os.path.join(_PC_ROOT, run, fname))


@app.route("/product-check/save", methods=["POST"])
def product_check_save():
    """[확정 저장] — 같은 폴더의 approved.json 에만 저장합니다(대기열·글 파일은 건드리지 않음)."""
    body = request.get_json(silent=True) or {}
    run = _pc_safe_run(str(body.get("run", "")))
    if not run:
        return jsonify({"ok": False, "error": "알 수 없는 결과 폴더입니다"}), 400
    with open(os.path.join(_PC_ROOT, run, "candidates.json"), encoding="utf-8") as f:
        data = json.load(f)
    by_kw = {k["keyword"]: k for k in data["keywords"]}
    out = []
    for sel in body.get("picks", []):
        k = by_kw.get(str(sel.get("keyword", "")))
        if not k:
            continue
        ranks = [int(r) for r in sel.get("ranks", []) if str(r).isdigit()]
        rec = {x["rank"]: x for x in k["recommended"]}
        chosen = []
        for r in ranks:
            item = next((x for x in k["items"] if x["rank"] == r), None)
            if item:
                chosen.append({**item, **({"coupang": rec[r]["coupang"]} if r in rec and rec[r].get("coupang") else {}),
                               "from_recommended": r in rec})
        out.append({"keyword": k["keyword"], "picks": chosen})
    payload = {"approved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "channel": data["channel"], "run": run, "keywords": out}
    with open(os.path.join(_PC_ROOT, run, "approved.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return jsonify({"ok": True, "saved_at": payload["approved_at"], "count": sum(len(k["picks"]) for k in out)})


def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000")


if __name__ == "__main__":
    threading.Timer(1.2, open_browser).start()
    app.run(debug=False, port=5000)
