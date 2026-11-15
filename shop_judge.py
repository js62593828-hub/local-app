"""
숍글 판정 공용 모듈 — 최종본·목록·기획 자료(app.py), 현황판(v4_board.py), 같은 블로그 겹침(blog_overlap.py)이 모두 이 판정 하나를 씁니다.
(읽기 전용 · 어떤 json도 쓰지 않습니다. app을 import하지 않습니다.)

규칙:
  - 옛 숍 채널(shop_naver, shop_wp) 글 = 항상 숍글
  - 네이버(naver_tips)·싼세계(wp_tips) 글 = 글에 저장된 is_shop_post 값이 있으면 그 값, 없으면 링크 자동 판정
      (네이버: 원고의 "링크:" 줄 / 싼세계: 쿠팡 링크·쿠팡파트너스 HTML 코드 — shop_formatter.has_shop_link)
  - 그 밖의 채널(롱텀 등) = 해당 없음(숍글 아님)
"""
import shop_formatter

OLD_SHOP_CHANNELS = ("shop_naver", "shop_wp")  # 옛 숍 채널(OLD): 항상 숍글
UNIFIED_CHANNEL_PLATFORM = {"naver_tips": "naver", "wp_tips": "wordpress"}  # [숍글] 체크를 쓰는 통합 채널 → 그 채널의 플랫폼


def shop_post_state(article: dict, channel_id: str, raw: str) -> dict | None:
    """네이버(naver_tips)·싼세계(wp_tips) 글의 [숍글] 상태. 다른 채널이면 None.
    auto = 원고에 링크가 있는지(네이버: "링크:" 줄 / 싼세계: 쿠팡 링크·쿠팡파트너스 HTML 코드),
    manual = 사용자가 저장한 값(True/False, 저장한 적 없으면 None=자동), effective = 실제로 적용되는 값."""
    platform = UNIFIED_CHANNEL_PLATFORM.get(channel_id)
    if not platform:
        return None
    auto = shop_formatter.has_shop_link(raw or "", platform)
    manual = (article or {}).get("is_shop_post")
    manual = None if manual is None else bool(manual)
    effective = auto if manual is None else manual
    return {
        "platform": platform, "auto": auto, "manual": manual, "effective": effective,
        "mode": "자동 판정" if manual is None else "직접 지정",
        "link_conflict": bool(auto and not effective),  # 링크가 있는데 숍글이 아님 → 대가성 문구 빠짐
    }


def is_shop_article(article: dict, channel_id: str, platform: str | None = None) -> bool:
    """숍글인지(위 규칙). platform을 안 주면 그 채널의 플랫폼(네이버/워드프레스)의 원고를 봅니다."""
    own_platform = UNIFIED_CHANNEL_PLATFORM.get(channel_id)
    if own_platform:
        raw = ((article or {}).get("platform_raw") or {}).get(platform or own_platform) or ""
        state = shop_post_state(article, channel_id, raw)
        return bool(state and state["effective"])
    return channel_id in OLD_SHOP_CHANNELS
