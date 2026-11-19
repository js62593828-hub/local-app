# -*- coding: utf-8 -*-
"""W싼세계(ssansegae.com) 워드프레스 카테고리 정리 (2026-11-19).

  python tools/wp_category_fix.py            # 미리보기: 읽기(GET)만. 백업 파일과 '지금 → 바뀐 뒤' 표를 만든다.
  python tools/wp_category_fix.py --apply    # 적용: 사용자가 미리보기를 확인하고 "적용"이라고 한 뒤에만.

바꾸는 것(이 목록 밖 카테고리는 건드리지 않음):
  a. 50 "주방 살림" → 이름 "주방용품" (슬러그 kitchen 그대로)
  b. 5 "생활가전" 에 든 글에 53 을 넣고 5 를 뺀다 → 5 삭제 → 53 "공기·생활가전" → 이름 "생활가전" (슬러그 home-appliances 그대로)
  c. 부모를 24 "생활용품" 으로: 48, 51, 49, 52, 47, 54, 46
글 제목·본문·주소는 건드리지 않는다(글에는 카테고리 목록만 바꾼다). 앱 데이터 파일은 읽지도 쓰지도 않는다.
"""
import argparse
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
OUT = os.path.join(ROOT, "pipeline", "wp_setup_run", "20261119", "categories")

RENAME = {50: "주방용품"}                    # 번호 → 새 이름
MERGE_FROM, MERGE_TO, MERGE_TO_NEW_NAME = 5, 53, "생활가전"
REPARENT_TO = 24
REPARENT = [48, 51, 49, 52, 47, 54, 46]
TARGETS = [50, MERGE_FROM, MERGE_TO] + REPARENT + [REPARENT_TO]
STATUSES = "publish,future,draft,pending,private"


def setup():
    import config
    import wp_publisher as wp
    wp.set_site_context({"url": config.SHOP_WORDPRESS_URL, "username": config.SHOP_WORDPRESS_USERNAME,
                         "app_password": config.SHOP_WORDPRESS_APP_PASSWORD})
    if "ssansegae.com" not in config.SHOP_WORDPRESS_URL:
        sys.exit("사이트 주소가 ssansegae.com 이 아닙니다: " + config.SHOP_WORDPRESS_URL)
    return wp


def get_all(wp, path, params):
    out, page = [], 1
    while True:
        r = wp._request("get", wp._api_url(path), headers=wp._auth_header(), params={**params, "per_page": 100, "page": page})
        if r.status_code == 400 and page > 1:
            break
        wp._raise_for_status_with_detail(r, path + " 조회")
        rows = r.json()
        out += rows
        if len(rows) < 100:
            break
        page += 1
    return out


def fetch_cats(wp):
    return [{"id": c["id"], "name": c["name"], "slug": c["slug"], "parent": c["parent"], "count": c["count"], "link": c.get("link", "")}
            for c in get_all(wp, "categories", {"hide_empty": "false"})]


def fetch_posts(wp):
    rows = get_all(wp, "posts", {"status": STATUSES, "_fields": "id,link,status,slug,categories,title"})
    return [{"id": p["id"], "link": p["link"], "status": p["status"], "slug": p["slug"], "categories": p["categories"],
             "title": (p["title"] or {}).get("rendered", "")} for p in rows]


def md_table(rows, head):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]


def plan_rows(cats, posts):
    by = {c["id"]: c for c in cats}
    for need in TARGETS:
        if need not in by:
            sys.exit("카테고리 %d 가 없습니다. 이미 바뀌었는지 확인하세요." % need)
    moved = [p for p in posts if MERGE_FROM in p["categories"]]
    rows = []
    for cid, nn in RENAME.items():
        c = by[cid]
        rows.append((cid, "이름", "%s (슬러그 %s, 부모 %s)" % (c["name"], c["slug"], c["parent"]), "%s (슬러그 %s 그대로)" % (nn, c["slug"])))
    c5, c53 = by[MERGE_FROM], by[MERGE_TO]
    rows.append((MERGE_FROM, "글 옮김", "%s 에 글 %d편(%s)" % (c5["name"], len(moved), ", ".join(str(p["id"]) for p in moved) or "없음"),
                 "그 글에 %d 를 넣고 %d 를 뺌" % (MERGE_TO, MERGE_FROM)))
    rows.append((MERGE_FROM, "삭제", "%s (글 수 %d)" % (c5["name"], c5["count"]), "삭제됨"))
    rows.append((MERGE_TO, "이름", "%s (슬러그 %s, 글 수 %d)" % (c53["name"], c53["slug"], c53["count"]),
                 "%s (슬러그 %s 그대로, 글 수 %d)" % (MERGE_TO_NEW_NAME, c53["slug"], c53["count"] + len(moved))))
    for cid in REPARENT:
        c = by[cid]
        pn = by[c["parent"]]["name"] if c["parent"] in by else ("없음(최상위)" if not c["parent"] else str(c["parent"]))
        rows.append((cid, "부모", "%s · 부모 %s" % (c["name"], pn), "%s · 부모 %s(%d)" % (c["name"], by[REPARENT_TO]["name"], REPARENT_TO)))
    return rows, moved


def preview():
    wp = setup()
    os.makedirs(OUT, exist_ok=True)
    cats, posts = fetch_cats(wp), fetch_posts(wp)
    rows, moved = plan_rows(cats, posts)
    in_target = [p for p in posts if set(p["categories"]) & set(TARGETS)]
    backup = {"백업시각": datetime.now().isoformat(timespec="seconds"), "사이트": "ssansegae.com", "카테고리_전체": cats,
              "대상_카테고리_글": [{k: p[k] for k in ("id", "link", "status", "categories")} for p in in_target],
              "글_전체_주소": [{"id": p["id"], "link": p["link"], "status": p["status"]} for p in posts]}
    with open(os.path.join(OUT, "backup_categories.json"), "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
    L = ["# 카테고리 정리 미리보기 (읽기만 했습니다 — 아직 아무것도 바꾸지 않음)", "",
         "글 %d편(발행·예약 등 전부), 카테고리 %d개. 백업: backup_categories.json" % (len(posts), len(cats)), ""]
    L += md_table(rows, ["번호", "무엇을", "지금", "바뀐 뒤"])
    L += ["", "※ 위 표에 없는 카테고리와 모든 글의 제목·본문·주소는 그대로입니다."]
    with open(os.path.join(OUT, "preview.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


def apply():
    wp = setup()
    bpath = os.path.join(OUT, "backup_categories.json")
    if not os.path.exists(bpath):
        sys.exit("백업 파일이 없습니다. 먼저 미리보기를 실행하세요.")
    cats, posts = fetch_cats(wp), fetch_posts(wp)
    old_links = {p["id"]: p["link"] for p in json.load(open(bpath, encoding="utf-8"))["글_전체_주소"]}
    _, moved = plan_rows(cats, posts)
    log = []

    def call(method, path, payload):
        send = wp._request
        r = send(method, wp._api_url(path), retries=0, headers=wp._auth_header(), **({"json": payload} if payload is not None else {}))
        wp._raise_for_status_with_detail(r, "%s %s" % (method, path))
        log.append("%s %s %s → %s" % (method.upper(), path, json.dumps(payload, ensure_ascii=False) if payload else "", r.status_code))
        print(log[-1])
        return r

    # b. 5번 글 → 53 넣고 5 뺌 (카테고리 목록만 바꿈)
    for p in moved:
        new = [c for c in p["categories"] if c != MERGE_FROM]
        if MERGE_TO not in new:
            new.append(MERGE_TO)
        call("post", "posts/%d" % p["id"], {"categories": new})
    # 5번 삭제 (force 필요, 이 시점에 5번에 든 글 없음)
    left = [p for p in fetch_posts(wp) if MERGE_FROM in p["categories"]]
    if left:
        sys.exit("5번에 아직 글이 있어 삭제를 멈춥니다: %s" % [p["id"] for p in left])
    send_del = wp.requests.delete(wp._api_url("categories/%d" % MERGE_FROM), headers=wp._auth_header(), params={"force": "true"}, timeout=45)
    wp._raise_for_status_with_detail(send_del, "categories/5 삭제")
    log.append("DELETE categories/%d force → %s" % (MERGE_FROM, send_del.status_code))
    print(log[-1])
    # a, b 이름 바꾸기 (슬러그는 보내지 않음)
    call("post", "categories/%d" % MERGE_TO, {"name": MERGE_TO_NEW_NAME})
    for cid, nn in RENAME.items():
        call("post", "categories/%d" % cid, {"name": nn})
    # c. 부모 바꾸기
    for cid in REPARENT:
        call("post", "categories/%d" % cid, {"parent": REPARENT_TO})
    # 검증
    cats2, posts2 = fetch_cats(wp), fetch_posts(wp)
    changed = [(p["id"], old_links[p["id"]], p["link"]) for p in posts2 if p["id"] in old_links and old_links[p["id"]] != p["link"]]
    missing = [i for i in old_links if i not in {p["id"] for p in posts2}]
    L = ["# 카테고리 정리 적용 결과", "", "## 카테고리 목록(적용 후)", ""]
    nm = {c["id"]: c["name"] for c in cats2}
    L += md_table([(c["id"], c["name"], c["slug"][:30], nm.get(c["parent"], "없음") if c["parent"] else "없음", c["count"]) for c in sorted(cats2, key=lambda c: c["id"])],
                  ["번호", "이름", "슬러그", "부모", "글 수"])
    L += ["", "## 글 주소 비교 (백업 %d편 ↔ 지금 %d편)" % (len(old_links), len(posts2)), "",
          "- 바뀐 주소: %d개%s" % (len(changed), "" if not changed else " → " + str(changed[:5])),
          "- 사라진 글: %d편" % len(missing), "", "## 실행 기록", ""] + ["- " + x for x in log]
    with open(os.path.join(OUT, "apply_result.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="실제로 바꾼다(사용자 확인 뒤에만)")
    a = ap.parse_args()
    apply() if a.apply else preview()
