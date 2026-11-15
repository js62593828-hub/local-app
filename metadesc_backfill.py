"""
이미 발행된 글의 "요약문(excerpt) + Rank Math 설명 + 포커스 키워드"만 고치는 터미널용 함수.
채널별로 돌릴 수 있습니다: "longterm"(기본), "shop_wp", "wp_tips".

- 본문(content)·이미지·카테고리·태그·날짜·상태는 절대 보내지 않습니다.
- 보내는 것: ① Rank Math 전용 경로(rankmath/v1/updateMeta)로 설명·키워드, ② 워드프레스 기본 경로로 excerpt 하나.
- 데이터 파일(articles*.json, content_queue*.json)은 읽기만 합니다.

사용법 (이 폴더에서):
    python -c "import metadesc_backfill as m; m.backfill_metadesc()"                                   # 롱텀 미리보기만 (기본)
    python -c "import metadesc_backfill as m; m.backfill_metadesc(dry_run=False)"                      # 롱텀 실제 전송
    python -c "import metadesc_backfill as m; m.backfill_metadesc(channel='shop_wp', dry_run=False)"   # 워드프레스 SHOP
"""
import csv
import html as _html
import json
import os
import re
import time

import config
import metadata_parser
import wp_publisher as wp

BASE = os.path.dirname(os.path.abspath(__file__))
PREVIEW_DIR = os.path.join(BASE, "preview")

# 채널별 데이터 파일·사이트·결과 파일 이름. longterm은 사이트 지정 없음(기본 사이트)입니다.
_SHOP_SITE = lambda: {  # noqa: E731
    "url": config.SHOP_WORDPRESS_URL,
    "username": config.SHOP_WORDPRESS_USERNAME,
    "app_password": config.SHOP_WORDPRESS_APP_PASSWORD,
}
CHANNELS = {
    "longterm": {"articles": "articles.json", "queue": "content_queue.json", "site": lambda: None, "suffix": ""},
    "shop_wp": {"articles": "articles_shop_wp.json", "queue": "content_queue_shop_wp.json", "site": _SHOP_SITE, "suffix": "_shop_wp"},
    "wp_tips": {"articles": "articles_wp_tips.json", "queue": "content_queue_wp_tips.json", "site": _SHOP_SITE, "suffix": "_wp_tips"},
}

# 글 ID → 설명 덮어쓰기 목록 (롱텀 전용. articles.json의 메모는 고치지 않고 여기서만 바꿔 보냅니다)
DESCRIPTION_OVERRIDES = {
    170: "전세자금대출이 무엇인지부터 버팀목전세자금 주요 상품, 공통 대상 요건, 한도·금리 차이, 신청 절차까지 처음 알아보는 분을 위해 한 번에 정리했습니다.",
}


def _load_json(name: str):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)


def build_backfill_items(channel: str = "longterm") -> list[dict]:
    """채널의 articles 파일에서 워드프레스 글 ID가 있는 글을 모아 보낼 값을 만듭니다 (읽기 전용)."""
    cfg = CHANNELS[channel]
    articles = _load_json(cfg["articles"])
    queue = {item["id"]: item for item in _load_json(cfg["queue"])}
    items = []
    for article_id, article in articles.items():
        post_id = (article.get("platform_post_id") or {}).get("wordpress")
        if not post_id:
            continue
        description = metadata_parser.parse_metadata_memo(
            (article.get("platform_metadata") or {}).get("wordpress", "")
        ).get("meta_description", "")
        if channel == "longterm":
            description = DESCRIPTION_OVERRIDES.get(int(post_id), description)
        description = wp.clean_rank_math_description(description)
        items.append(
            {
                "article_id": article_id,
                "post_id": int(post_id),
                "title": (article.get("platform_title") or {}).get("wordpress") or (queue.get(article_id) or {}).get("keyword", ""),
                "excerpt": description,
                "rank_math_description": description,
                "rank_math_focus_keyword": wp.clean_focus_keyword((queue.get(article_id) or {}).get("keyword", "")),
                "description_length": len(description),
            }
        )
    return items


def _fetch_saved_values(post_id: int) -> dict:
    """저장된 값을 조회(GET)로 확인합니다: excerpt는 REST, 설명은 공개 페이지의 meta description 태그."""
    response = wp._request(
        "get", wp._api_url(f"posts/{post_id}"), headers=wp._auth_header(),
        params={"context": "edit", "_fields": "id,excerpt,link"},
    )
    wp._raise_for_status_with_detail(response, "저장값 조회")
    data = wp._safe_json(response, "저장값 조회")
    page = wp._request(
        "get", f"{wp._current_site()['url']}/?p={post_id}&_chk={int(time.time())}", headers=wp.BROWSER_LIKE_HEADERS
    )
    match = re.search(r'<meta name="description" content="([^"]*)"', page.text)
    return {
        "excerpt": (data.get("excerpt") or {}).get("raw", ""),
        # 예약글(아직 비공개)은 공개 페이지가 404라 확인할 수 없습니다 → None
        "meta_description": None if page.status_code == 404 else (_html.unescape(match.group(1)) if match else ""),
    }


def _send_and_verify(item: dict) -> tuple[bool, dict]:
    """한 편을 보내고 조회로 확인합니다. 반환: (일치 여부, 확인된 값)."""
    # 1) Rank Math 설명·키워드  2) excerpt (이 저장이 캐시도 비워 주도록 마지막에)
    wp.rank_math_update_meta(
        item["post_id"],
        {"rank_math_description": item["rank_math_description"], "rank_math_focus_keyword": item["rank_math_focus_keyword"]},
    )
    r = wp._request("post", wp._api_url(f"posts/{item['post_id']}"), headers=wp._auth_header(), json={"excerpt": item["excerpt"]})
    wp._raise_for_status_with_detail(r, "요약문 저장")
    saved = _fetch_saved_values(item["post_id"])
    ok = saved["excerpt"] == item["excerpt"] and saved["meta_description"] in (item["rank_math_description"], None)
    return ok, saved


def _merge_csv(path: str, rows: list[dict]) -> list[dict]:
    """기존 결과 CSV가 있으면 이번에 처리한 글 ID만 덮어써서 합칩니다."""
    merged = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            merged = {r["글 ID"]: r for r in csv.DictReader(f)}
    for r in rows:
        merged[str(r["글 ID"])] = {**r, "글 ID": str(r["글 ID"])}
    return sorted(merged.values(), key=lambda r: int(r["글 ID"]))


def backfill_metadesc(dry_run: bool = True, only_ids: list[int] | None = None, channel: str = "longterm") -> list[dict]:
    """
    dry_run=True(기본): 아무것도 보내지 않고 preview/metadesc_backfill_preview{_채널}.json 만 저장합니다.
    dry_run=False: 한 편씩 보내고 매번 조회로 확인합니다. 불일치·오류면 같은 값을 1회 재시도하고, 그래도 안 되면
                   그 글만 "실패"로 기록하고 다음 글로 넘어갑니다 (연속 3편 실패하면 중단). 결과는
                   preview/metadesc_backfill_result{_채널}.csv 에 합쳐 저장합니다.
    only_ids: 글 ID 목록을 주면 그 글만 처리합니다 (예: [441]).
    channel: "longterm"(기본) | "shop_wp" | "wp_tips"
    """
    cfg = CHANNELS[channel]
    items = build_backfill_items(channel)
    if only_ids:
        items = [i for i in items if i["post_id"] in set(only_ids)]
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    if dry_run:
        preview = [
            {
                "글 ID": i["post_id"],
                "제목": i["title"],
                "보낼 excerpt": i["excerpt"],
                "보낼 설명": i["rank_math_description"],
                "보낼 키워드": i["rank_math_focus_keyword"],
                "설명 글자 수": i["description_length"],
            }
            for i in items
        ]
        path = os.path.join(PREVIEW_DIR, f"metadesc_backfill_preview{cfg['suffix']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(preview, f, ensure_ascii=False, indent=2)
        print(f"[미리보기:{channel}] {len(items)}편 → {path} (전송 없음)")
        return preview

    wp.set_site_context(cfg["site"]())
    try:
        wp._require_site_configured()
        results = []
        consecutive_failures = 0
        for i in items:
            row = {"글 ID": i["post_id"], "성공 여부": "실패", "확인된 값": ""}
            for attempt in (1, 2):
                try:
                    if not i["rank_math_description"]:
                        raise wp.WordPressPublishError("메모에서 메타 디스크립션을 못 찾아 건너뜁니다.")
                    ok, saved = _send_and_verify(i)
                    row["확인된 값"] = json.dumps(saved, ensure_ascii=False)
                    row["성공 여부"] = ("성공(예약글·공개 후 페이지 확인 필요)" if saved["meta_description"] is None else "성공") if ok else "불일치"
                except Exception as e:  # noqa: BLE001
                    row["성공 여부"] = "실패"
                    row["확인된 값"] = f"{type(e).__name__}: {e}"[:300]
                    if not i["rank_math_description"]:
                        break
                if row["성공 여부"].startswith("성공"):
                    break
                if attempt == 1:
                    time.sleep(2)
            if row["성공 여부"] == "불일치":
                row["성공 여부"] = "실패"
                row["확인된 값"] = "불일치(재시도 후에도): " + row["확인된 값"]
            results.append(row)
            print(f"[{row['성공 여부']}:{channel}] 글 {i['post_id']}")
            consecutive_failures = 0 if row["성공 여부"].startswith("성공") else consecutive_failures + 1
            if consecutive_failures >= 3:
                print("→ 연속 3편 실패, 중단합니다.")
                break
            time.sleep(1)
    finally:
        wp.set_site_context(None)

    path = os.path.join(PREVIEW_DIR, f"metadesc_backfill_result{cfg['suffix']}.csv")
    merged = _merge_csv(path, results)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["글 ID", "성공 여부", "확인된 값"])
        w.writeheader()
        w.writerows(merged)
    print(f"[결과:{channel}] {path}  (성공 {sum(r['성공 여부'].startswith('성공') for r in merged)}/{len(merged)})")
    return results
