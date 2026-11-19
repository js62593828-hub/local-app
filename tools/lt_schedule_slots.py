# -*- coding: utf-8 -*-
"""발행 칸 21개: 그 주 월~일, 하루 3칸(아침 07:39~07:51 / 점심 12:20~12:41 / 저녁 17:41~17:55, 끝 분 포함).
칸마다 분을 랜덤으로 고르되 같은 주에 같은 '시:분'이 두 번 나오지 않게 한다. 시드는 주차 문자열(다시 돌려도 같은 결과), --reroll 이면 새로.
이미 그 주에 예약된 혜택주머니 글(articles.json 의 발행일·예약시각, 워드프레스 예약글)이 있으면 그 칸은 '사용 중'으로 표시한다.
결과: 주간기획/혜택_날짜/05_발행칸.json  (읽기만 하고 앱 데이터는 쓰지 않는다)"""
import argparse
import os
import random
from datetime import date, datetime, timedelta

import lt_common as C

BANDS = [("am", "아침", 7, 39, 51), ("noon", "점심", 12, 20, 41), ("pm", "저녁", 17, 41, 55)]
DAYS_KO = "월화수목금토일"


def make_slots(week: str, reroll: bool = False) -> list[dict]:
    rng = random.Random(None if reroll else week)
    monday = date.fromisoformat(week)
    slots, taken = [], set()
    for d in range(7):
        day = monday + timedelta(days=d)
        for key, label, hh, m0, m1 in BANDS:
            pool = [m for m in range(m0, m1 + 1) if (hh, m) not in taken]
            m = rng.choice(pool)
            taken.add((hh, m))
            slots.append({"slot_id": f"{day:%m%d}-{key}", "date": day.isoformat(), "weekday": DAYS_KO[d],
                          "time": f"{hh:02d}:{m:02d}", "label": label, "used_by": None})
    return slots


def used_times(app, week: str) -> dict:
    """그 주에 이미 잡힌 발행 시각 {'YYYY-MM-DD HH:MM': 글 설명}"""
    monday = date.fromisoformat(week)
    days = {(monday + timedelta(days=i)).isoformat() for i in range(7)}
    out = {}
    articles = C.read_json(app.CHANNELS[C.CHANNEL]["articles_file"], {})
    queue = {q.get("id"): q for q in C.read_json(app.CHANNELS[C.CHANNEL]["queue_file"], []) if isinstance(q, dict)}
    for aid, art in articles.items():
        d = ((art.get("platform_published_at") or {}).get("wordpress") or "")[:10]
        t = ((art.get("platform_scheduled_time") or {}).get("wordpress") or "")[:5]
        if d in days and t:
            out[f"{d} {t}"] = (queue.get(aid) or {}).get("keyword") or aid
    return out


def wp_future(app) -> tuple[dict, str]:
    """워드프레스의 예약(future) 글 시각 {'YYYY-MM-DD HH:MM': 제목}. 못 읽으면 ({}, 이유)."""
    try:
        wp = app.wp_publisher
        if not wp.current_site_configured():
            return {}, "워드프레스 접속 정보 없음"
        r = wp._request("get", wp._api_url("posts?status=future&per_page=100&_fields=id,date,title"),
                        headers=wp._auth_header(), retries=0, timeout=20)
        if r.status_code != 200:
            return {}, f"응답 {r.status_code}"
        return {p["date"][:16].replace("T", " "): (p.get("title") or {}).get("rendered", "") for p in r.json()}, ""
    except Exception as e:  # noqa: BLE001
        return {}, f"{type(e).__name__}"[:60]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", required=True, help="월요일 YYYY-MM-DD")
    ap.add_argument("--reroll", action="store_true", help="시드를 새로 잡아 다른 분으로 다시 뽑는다")
    ap.add_argument("--no-wp", action="store_true", help="워드프레스 예약글 조회를 건너뛴다")
    a = ap.parse_args()
    week = C.check_week(a.week)
    app = C.load_app()
    slots = make_slots(week, a.reroll)
    used = used_times(app, week)
    note = ""
    if not a.no_wp:
        wpf, why = wp_future(app)
        used.update({k: v for k, v in wpf.items() if k not in used})
        note = f"워드프레스 예약글 {len(wpf)}개 확인" if not why else f"워드프레스 예약글은 못 읽음({why})"
    # 이미 잡힌 시각이 칸의 시각대(시)와 같으면 그 칸을 '사용 중'으로 본다(시각 정확히 같지 않아도 같은 날 같은 시간대).
    for s in slots:
        for k, v in used.items():
            kd, kt = k.split(" ")
            if kd == s["date"] and kt[:2] == s["time"][:2]:
                s["used_by"] = f"{v} ({kt})"
                break
    path = os.path.join(C.week_dir(week), "05_발행칸.json")
    C.write_json(path, slots)
    print(f"칸 {len(slots)}개 만듦, 사용 중 {sum(1 for s in slots if s['used_by'])}개. {note}")
    for s in slots:
        print(f"{s['slot_id']}  {s['date']}({s['weekday']}) {s['time']}  {s['label']}" + (f"  ← 사용 중: {s['used_by']}" if s["used_by"] else ""))
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
