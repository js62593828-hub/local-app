# -*- coding: utf-8 -*-
"""싼세계(wp_tips) 전용: 배너 PNG 를 변환·압축 없이 워드프레스 미디어에 새 파일로 올린다.
사용: python tools/wp_banner_upload.py <PNG 경로> <올릴 파일 이름> <alt>   (실제로 1개 올림, 기존 미디어는 안 건드림)
접속 정보는 앱의 싼세계 채널(SHOP_WORDPRESS_*)과 같은 곳. 값은 출력하지 않는다."""
import json, os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
import config
import wp_publisher


def upload_raw(path: str, filename: str, alt: str) -> dict:
    wp_publisher.set_site_context({
        "url": config.SHOP_WORDPRESS_URL,
        "username": config.SHOP_WORDPRESS_USERNAME,
        "app_password": config.SHOP_WORDPRESS_APP_PASSWORD,
    })
    if not wp_publisher.current_site_configured():
        raise SystemExit("싼세계 워드프레스 접속 정보(SHOP_WORDPRESS_URL/USERNAME/APP_PASSWORD)가 비어 있음")
    with open(path, "rb") as f:
        raw = f.read()  # 바이트를 그대로(변환·압축·리사이즈 없음)
    headers = {**wp_publisher._auth_header(),
               "Content-Disposition": f'attachment; filename="{filename}"',
               "Content-Type": "image/png"}
    r = wp_publisher._request("post", wp_publisher._api_url("media"), headers=headers, data=raw, retries=0)
    wp_publisher._raise_for_status_with_detail(r, f"배너 업로드({filename})")
    data = r.json()
    m = wp_publisher._request("post", wp_publisher._api_url(f"media/{data['id']}"), headers=wp_publisher._auth_header(),
                              json={"alt_text": alt}, retries=0)
    wp_publisher._raise_for_status_with_detail(m, "배너 alt 지정")
    return {"id": data["id"], "source_url": data["source_url"], "mime_type": data.get("mime_type")}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    print(json.dumps(upload_raw(*sys.argv[1:]), ensure_ascii=False))
