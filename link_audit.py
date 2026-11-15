"""
이미 발행된 글의 링크 점검 (보고서 만들기).

  python link_audit.py                 → 기본: 보고서만 (preview/link_audit_report.md). 글·json·워드프레스는 건드리지 않습니다.
  python link_audit.py --no-network    → 보고서만, 인터넷 검사 없이 교체표에 걸리는 링크만 빠르게 찾습니다.
  python link_audit.py --apply         → (조심!) 워드프레스 채널(혜택주머니·싼세계)의 교체표 링크를 앱의 재동기화로 고쳐 보냅니다.
                                          보내기 전 각 글 본문을 backup/ 폴더에 저장합니다. 네이버·생활레이더는 자동 수정하지 않고
                                          보고서에 "직접 수정"으로 표시합니다.

보고서는 저장된 최종본(platform_final) 기준입니다. 글 json은 읽기만 합니다 (--apply 때만 앱 함수가 해당 글을 저장).
"""
import argparse
import json
import os
import sys
from datetime import datetime

import link_check

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(HERE, "preview", "link_audit_report.md")
BACKUP_ROOT = os.path.join(HERE, "backup")

# (채널 id, 화면 이름, 글 파일, 큐 파일, 플랫폼, 자동 수정 가능 여부) — app.py의 CHANNELS와 같은 값입니다.
AUDIT_CHANNELS = [
    ("longterm_wp", "혜택주머니", "articles.json", "content_queue.json", "wordpress", True),
    ("wp_tips", "싼세계", "articles_wp_tips.json", "content_queue_wp_tips.json", "wordpress", True),
    ("shop_wp", "워드프레스 SHOP (OLD)", "articles_shop_wp.json", "content_queue_shop_wp.json", "wordpress", False),
    ("naver_tips", "네이버", "articles_naver_tips.json", "content_queue_naver_tips.json", "naver", False),
    ("shop_naver", "네이버 SHOP (OLD)", "articles_shop_naver.json", "content_queue_shop_naver.json", "naver", False),
    ("radar_naver", "생활레이더", "articles_radar_naver.json", "content_queue_radar_naver.json", "naver", False),
]


def _read_json(name, default):
    try:
        with open(os.path.join(HERE, name), encoding="utf-8") as f:  # 읽기 전용
            return json.load(f)
    except (OSError, ValueError):
        return default


def collect_posts():
    """발행된 글 [{channel, label, id, title, url, html, platform, auto}] — json은 읽기만 합니다."""
    posts = []
    for ch_id, label, art_file, queue_file, platform, auto in AUDIT_CHANNELS:
        articles = _read_json(art_file, {})
        keywords = {q.get("id"): q.get("keyword", "") for q in _read_json(queue_file, []) if isinstance(q, dict)}
        for aid, a in articles.items():
            url = ((a.get("platform_published_url") or {}).get(platform) or "").strip()
            published = bool((a.get("published") or {}).get(platform))
            html = (a.get("platform_final") or {}).get(platform) or ""
            if not html or not (url or published):
                continue
            title = (a.get("platform_title") or {}).get(platform) or keywords.get(aid) or aid
            posts.append({"channel": ch_id, "label": label, "id": aid, "title": title, "url": url or "(주소 없음)",
                          "html": html, "platform": platform, "auto": auto})
    return posts


def audit(posts, network=True):
    """글마다 점검 결과를 붙여 돌려줍니다: post["replaceable"], post["issues"]."""
    table = link_check.load_replacements()
    # 같은 링크는 한 번만 열어 보도록 먼저 모아서 동시에 검사합니다 (결과는 link_check 안의 메모리 기억에 남음).
    if network:
        all_urls, seen = [], set()
        for p in posts:
            for u in link_check.extract_external_links(p["html"])[0]:
                if u not in seen:
                    seen.add(u)
                    all_urls.append(u)
        print(f"검사할 서로 다른 외부 링크 {len(all_urls)}개 (글 {len(posts)}편)…")
        link_check.check_links(all_urls)
    for p in posts:
        p["replaceable"] = link_check.find_replaceable(p["html"], table)
        p["issues"], p["soft"] = [], []  # soft = 자동 확인이 막힌 사이트(정상일 수 있음): 경고 수에 넣지 않고 참고로만 적음
        if network:
            found = [r for r in (link_check.check_link(u) for u in link_check.extract_external_links(p["html"])[0]) if r]
            p["issues"] = [r for r in found if not r.get("soft")]
            p["soft"] = [r for r in found if r.get("soft")]
    return posts


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def write_report(posts, network):
    rows = []  # (채널, 제목, 발행 주소, 문제 링크, 이유, 처리)
    for p in posts:
        todo_replace = "--apply로 자동 수정 가능" if p["auto"] else "직접 수정"
        for c in p["replaceable"]:
            rows.append((p["label"], p["title"], p["url"], c["before"], f"교체표에 걸림 → {c['after']}", todo_replace))
        for i in p["issues"]:
            rows.append((p["label"], p["title"], p["url"], i["url"], i["reason"], "직접 확인"))
    n_replace = sum(1 for p in posts for _ in p["replaceable"])
    n_issue = sum(1 for p in posts for _ in p["issues"])
    n_posts_hit = sum(1 for p in posts if p["replaceable"] or p["issues"])
    lines = [
        "# 발행 글 링크 점검 보고서",
        "",
        f"- 만든 시각: {datetime.now():%Y-%m-%d %H:%M}",
        f"- 점검한 발행 글: {len(posts)}편 (저장된 최종본 기준, 인터넷 검사: {'함' if network else '안 함(--no-network)'})",
        f"- 문제가 있는 글: {n_posts_hit}편 / 교체표에 걸린 링크 {n_replace}개 / 검사에서 걸린 링크 {n_issue}개",
        "- 이 보고서를 만들 때 글·json·워드프레스는 바꾸지 않았습니다.",
        "",
    ]
    for label in dict.fromkeys(p["label"] for p in posts):
        sub = [r for r in rows if r[0] == label]
        total = sum(1 for p in posts if p["label"] == label)
        lines += [f"## {label} (발행 글 {total}편 중 문제 {len({(r[1], r[2]) for r in sub})}편)", ""]
        if not sub:
            lines += ["문제 없음", ""]
            continue
        lines += ["| 채널 | 글 제목 | 발행 주소 | 문제 링크 | 이유 | 처리 |", "|---|---|---|---|---|---|"]
        lines += ["| " + " | ".join(_cell(x) for x in r) + " |" for r in sub]
        lines.append("")
    targets = [p for p in posts if p["auto"] and p["replaceable"]]
    manual = [p for p in posts if not p["auto"] and p["replaceable"]]
    lines += ["## --apply 때 고쳐질 글 (워드프레스 채널)", ""]
    if targets:
        lines += ["| 채널 | 글 제목 | 고쳐질 링크 |", "|---|---|---|"]
        lines += [f"| {_cell(p['label'])} | {_cell(p['title'])} | " + _cell(" / ".join(f"{c['before']} → {c['after']}" for c in p["replaceable"])) + " |" for p in targets]
    else:
        lines.append("없음")
    lines += ["", "## 직접 수정해야 할 글 (네이버·생활레이더·OLD 채널)", ""]
    if manual:
        lines += ["| 채널 | 글 제목 | 바꿀 링크 |", "|---|---|---|"]
        lines += [f"| {_cell(p['label'])} | {_cell(p['title'])} | " + _cell(" / ".join(f"{c['before']} → {c['after']}" for c in p["replaceable"])) + " |" for p in manual]
    else:
        lines.append("없음")
    soft_rows = [(p["label"], p["title"], i["url"]) for p in posts for i in p.get("soft", [])]
    lines += ["", f"## 참고: 자동 확인이 막힌 사이트 {len(soft_rows)}건 (정상일 수 있어 경고 수에 넣지 않음)", ""]
    if soft_rows:
        lines += ["| 채널 | 글 제목 | 링크 |", "|---|---|---|"] + [f"| {_cell(a)} | {_cell(b)} | {_cell(c)} |" for a, b, c in soft_rows]
    else:
        lines.append("없음")
    lines.append("")
    lines += [
        "## 읽는 법",
        "",
        "1. **교체표에 걸림**: link_replacements.json의 옛 주소가 글에 남아 있다는 뜻입니다. 워드프레스(혜택주머니·싼세계)는 `--apply`로 고칠 수 있고, 네이버·생활레이더는 직접 고쳐야 합니다.",
        "2. **그 밖의 이유**(오류 응답·연결 실패·다른 도메인으로 넘어감·빈 화면 의심)는 사람이 링크를 열어 보고 판단합니다. 일부 사이트는 자동 검사를 막아서 실제로는 열리는데도 걸릴 수 있습니다.",
        "3. 쿠팡·네이버 쇼핑 등 제휴 링크와 내 블로그 주소는 검사에서 제외했습니다.",
        "",
    ]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return n_replace, n_issue, n_posts_hit


# ---------- --apply (워드프레스만) ----------
# 순서: 본문 백업 → 워드프레스 전송(재동기화) → 성공한 글만 글 json 저장. 실패한 글은 json을 그대로 둡니다.

class _AppOps:
    """실제 앱(app.py)을 쓰는 연결부. 테스트에서는 같은 모양의 가짜로 바꿔 끼웁니다."""

    def __init__(self, appmod, channel):
        self.appmod, self.channel = appmod, channel

    def get_article(self, aid):
        return self.appmod.get_article(aid)

    def set_article(self, aid, article):
        self.appmod.set_article(aid, article)

    def post_id(self, article):
        return self.appmod._infer_wordpress_post_id(article)

    def live_content(self, post_id):
        import shop_wp_resync
        return shop_wp_resync._fetch_live_post(int(post_id))["content"]["raw"]

    def staged(self, aid, article):
        """보내는 동안 이 글의 저장(set_article)을 파일이 아니라 메모리에만 하도록 잠깐 바꿉니다 (재동기화가 이 글을 읽고 쓰는 곳 전부)."""
        from contextlib import contextmanager
        appmod = self.appmod

        @contextmanager
        def _cm():
            holder = {"article": article}
            real_get, real_set = appmod.get_article, appmod.set_article

            def fake_get(a):
                import copy
                return copy.deepcopy(holder["article"]) if a == aid else real_get(a)

            def fake_set(a, data):
                if a == aid:
                    holder["article"] = data
                else:
                    real_set(a, data)

            appmod.get_article, appmod.set_article = fake_get, fake_set
            try:
                yield holder
            finally:
                appmod.get_article, appmod.set_article = real_get, real_set
        return _cm()

    def send(self, aid):
        route = {"longterm_wp": "resync-wordpress", "wp_tips": "resync-shop-wordpress"}[self.channel]
        client = self.appmod.app.test_client()
        with client.session_transaction() as s:
            s["channel"] = self.channel
        r = client.post(f"/article/{aid}/{route}")
        return r.status_code in (301, 302, 303), f"응답 {r.status_code}"


def apply_one(p, ops, backup_dir):
    """글 하나: 백업 → 전송 → 성공하면 json 저장. 반환: (성공 여부, 메모)."""
    import copy
    aid = p["id"]
    article = ops.get_article(aid)
    post_id = ops.post_id(article)
    if not post_id:
        return False, "워드프레스 글 ID 없음"
    base = os.path.join(backup_dir, f"{p['channel']}_{aid}")
    stored = article["platform_final"]["wordpress"]
    with open(base + "_stored_final.html", "w", encoding="utf-8") as f:
        f.write(stored)
    try:
        live = ops.live_content(post_id)
    except Exception as e:  # noqa: BLE001 — 지금 올라간 본문을 못 받으면 보내지 않습니다
        return False, f"현재 워드프레스 본문 백업 실패 ({type(e).__name__})"
    with open(base + "_live.html", "w", encoding="utf-8") as f:
        f.write(live)

    new_article = copy.deepcopy(article)
    new_article["platform_final"]["wordpress"], _ = link_check.apply_replacements(stored)
    with ops.staged(aid, new_article) as holder:
        try:
            ok, msg = ops.send(aid)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"전송 오류 ({type(e).__name__}: {e})"[:200]
        final_article = holder["article"]
    if ok:
        # 전송 확인: 워드프레스의 현재 본문을 다시 읽어 새 링크가 있고 옛 링크가 없을 때만 성공으로 봅니다.
        try:
            now_live = ops.live_content(post_id)
        except Exception as e:  # noqa: BLE001
            return False, f"실패(전송 확인 안 됨: 현재 본문을 다시 읽지 못함 {type(e).__name__})"
        wanted = {c["after"] for c in link_check.find_replaceable(stored)}
        still_old = link_check.find_replaceable(now_live)
        missing = [a for a in wanted if f'href="{a}"' not in now_live and f"href='{a}'" not in now_live]
        if still_old or missing:
            return False, "실패(전송 확인 안 됨: 워드프레스 본문에 새 링크가 없거나 옛 링크가 남아 있음)"
        ops.set_article(aid, final_article)  # 성공이 확인된 글만 저장
        return True, msg + " / 워드프레스 본문에서 새 링크 확인"
    return ok, msg


def apply_fixes(posts, assume_yes=False, ops_factory=None, backup_root=None):
    targets = [p for p in posts if p["auto"] and p["replaceable"] and p["url"] != "(주소 없음)"]
    manual = [p for p in posts if not p["auto"] and p["replaceable"]]
    print(f"자동 수정 대상 {len(targets)}편 (워드프레스) / 직접 수정 {len(manual)}편 (네이버·생활레이더 등, 보고서 참고)")
    if not targets:
        return []
    if not assume_yes and input("워드프레스 글을 실제로 고쳐 보냅니다. 계속할까요? (yes 입력) ").strip().lower() != "yes":
        print("취소했습니다.")
        return []

    backup_dir = os.path.join(backup_root or BACKUP_ROOT, f"link_audit_{datetime.now():%Y%m%d}_apply_{datetime.now():%H%M%S}")
    os.makedirs(backup_dir, exist_ok=True)
    results = []
    if ops_factory is None:
        from flask import session
        import app as appmod

        def ops_factory(channel):
            return _AppOps(appmod, channel)
        ctx = lambda ch: _channel_context(appmod, session, ch)  # noqa: E731
    else:
        from contextlib import nullcontext
        ctx = lambda ch: nullcontext()  # noqa: E731
    for p in targets:
        with ctx(p["channel"]):
            ok, msg = apply_one(p, ops_factory(p["channel"]), backup_dir)
        results.append({"label": p["label"], "title": p["title"], "ok": ok, "msg": msg})
        print(f"- {'완료' if ok else '실패'}: [{p['label']}] {p['title']} ({msg})")
    print(f"본문 백업 위치: {backup_dir}")
    _append_apply_result(results)
    return results


def _channel_context(appmod, session, channel):
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        with appmod.app.test_request_context("/"):
            session["channel"] = channel
            appmod.app.preprocess_request()  # 채널에 맞는 워드프레스 사이트로 맞춤
            yield
    return _cm()


def _append_apply_result(results, report_path=None):
    """--apply 결과(성공/실패)를 보고서 끝에 덧붙입니다. 실패한 글은 json을 바꾸지 않았다는 뜻입니다."""
    lines = ["", f"## --apply 결과 ({datetime.now():%Y-%m-%d %H:%M})", "", "| 채널 | 글 제목 | 결과 | 메모 |", "|---|---|---|---|"]
    lines += [f"| {_cell(r['label'])} | {_cell(r['title'])} | {'성공(json 저장)' if r['ok'] else '실패(json 그대로)'} | {_cell(r['msg'])} |" for r in results]
    with open(report_path or REPORT_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="발행 글 링크 점검 (기본: 보고서만)")
    ap.add_argument("--apply", action="store_true", help="워드프레스 채널의 교체표 링크를 재동기화로 고쳐 보냄 (기본 꺼짐)")
    ap.add_argument("--no-network", action="store_true", help="인터넷 검사 없이 교체표에 걸리는 링크만 찾음")
    ap.add_argument("--yes", action="store_true", help="--apply 확인 질문 생략")
    ap.add_argument("--report", help="보고서 저장 위치 (기본: preview/link_audit_report.md)")
    args = ap.parse_args()
    if args.report:
        global REPORT_PATH
        REPORT_PATH = os.path.abspath(args.report)

    posts = audit(collect_posts(), network=not args.no_network)
    n_replace, n_issue, n_posts = write_report(posts, network=not args.no_network)
    print(f"보고서 저장: {REPORT_PATH}")
    print(f"발행 글 {len(posts)}편 중 문제 글 {n_posts}편 (교체표 {n_replace}개 / 검사 {n_issue}개)")
    if args.apply:
        apply_fixes(posts, assume_yes=args.yes)


if __name__ == "__main__":
    sys.exit(main())
