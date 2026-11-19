# -*- coding: utf-8 -*-
"""기획 자료 내보내기: 앱의 _export_longterm_plan 을 그대로 불러 Claude outputs/longterm_plan_input.md 를 만든다(읽기 전용)."""
import argparse
from datetime import datetime

import lt_common as C


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", required=True, help="월요일 YYYY-MM-DD")
    a = ap.parse_args()
    week = C.check_week(a.week)
    app = C.load_app()
    info = app._export_longterm_plan(week, datetime.now())
    print(f"만들었습니다: {app.LONGTERM_PLAN_EXPORT_PATH}")
    print(f"글 {info.get('posts')}편 정리 (주차 {week})")


if __name__ == "__main__":
    main()
