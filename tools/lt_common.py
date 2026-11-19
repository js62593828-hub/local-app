# -*- coding: utf-8 -*-
"""혜택주머니 자동 도구 공통 부분 (채널 longterm_wp 전용).

- 앱 로직은 새로 짜지 않고 app.py 의 함수·라우트를 그대로 부른다.
- 시험용: 환경변수 LT_DATA_DIR 이 있으면 그 폴더의 content_queue.json / articles.json 을 쓴다(실제 데이터는 안 건드림).
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNEL = "longterm_wp"
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def check_week(week: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", week or ""):
        sys.exit("--week 는 YYYY-MM-DD 모양이어야 합니다.")
    if date.fromisoformat(week).weekday() != 0:
        sys.exit(f"--week {week} 는 월요일이 아닙니다.")
    return week


def week_dir(week: str) -> str:
    d = os.path.join(ROOT, "주간기획", f"혜택_{week}")
    os.makedirs(d, exist_ok=True)
    return d


def load_app(data_dir: str | None = None):
    """app 모듈을 불러온다. data_dir(또는 LT_DATA_DIR)가 있으면 큐·글 파일 위치만 그쪽으로 돌린다."""
    os.chdir(ROOT)
    import app as appmod  # noqa: E402
    data_dir = data_dir or os.environ.get("LT_DATA_DIR")
    if data_dir:
        cfg = appmod.CHANNELS[CHANNEL]
        cfg["queue_file"] = os.path.join(data_dir, "content_queue.json")
        cfg["articles_file"] = os.path.join(data_dir, "articles.json")
    return appmod


def make_client(appmod):
    """앱을 켜지 않고 쓰는 시험 클라이언트. 먼저 /switch-channel/longterm_wp 로 세션 채널을 맞춘다."""
    client = appmod.app.test_client()
    client.get(f"/switch-channel/{CHANNEL}")
    return client


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def backup_file(path: str, tag: str = "lt_auto") -> str:
    """파일명.backup_before_lt_auto_날짜.확장자 백업(이미 있으면 시각까지 붙임)."""
    import shutil
    base, ext = os.path.splitext(path)
    stamp = date.today().strftime("%Y%m%d")
    dst = f"{base}.backup_before_{tag}_{stamp}{ext}"
    if os.path.exists(dst):
        dst = f"{base}.backup_before_{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    shutil.copy2(path, dst)
    return dst
