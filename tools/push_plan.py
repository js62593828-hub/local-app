"""확정된 주간 계획을 로컬앱 대기열에 '승인 대기'로 넣는 도구.

기본은 --dry-run(넣을 항목만 보여 줌, 아무것도 안 씀). --apply 일 때만 대기열 파일에 씁니다.
 - 쓰기 전에 대상 대기열 파일을 backup/ 에 복사합니다.
 - 항목은 '추가'만 합니다(기존 항목 수정·삭제 없음).
 - 같은 대기열에 같은 키워드(띄어쓰기 무시)가 이미 있으면 넣지 않고 알려 줍니다.
 - --undo : 직전에 --apply 로 넣은 항목만 되돌립니다(넣은 id 는 pipeline/final/push_log.json 에 기록).
 - --init-products : 제품 링크 틀 파일을 만듭니다(이미 있으면 안 덮어씀).
 - --products-txt : 한 줄에 `키워드 | 제품명 | 링크` 인 글 파일을 읽어 제품 링크 json(--products)을 만듭니다.
   링크 주소로 플랫폼을 가립니다(쿠팡=싼세계, 네이버=네이버). 기존 json 은 먼저 백업합니다.
 - --update-products : 이 도구가 넣은 항목(push_log 의 id)의 plan 칸 안 제품 링크만 바꿉니다
   (기본은 보여 주기만, --apply 일 때 씀. 승인된 항목도 바꿈. 다른 칸·다른 항목은 안 건드림. 쓰기 전 백업).
 - 카테고리·글 유형은 pipeline/final/categories_20261116.json(--categories)이 계획 값보다 우선합니다.
앱 코드(app.py 등)는 import 하지 않습니다.

사용 예:
  python tools/push_plan.py --plan pipeline/final/plan_20261116.json --products pipeline/final/products_20261116.json
  python tools/push_plan.py --plan ... --products ... --apply
  python tools/push_plan.py --undo
"""
import argparse
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join("pipeline", "final", "push_log.json")
# 플랫폼 → 대기열 파일 (앱 TARGET_APPROVE_BLOGS: 네이버=naver_tips, 싼세계=wp_tips)
QUEUES = {"naver": "content_queue_naver_tips.json", "wordpress": "content_queue_wp_tips.json"}
ARTICLES = {"naver": "articles_naver_tips.json", "wordpress": "articles_wp_tips.json"}
LABEL = {"naver": "네이버(naver_tips)", "wordpress": "싼세계(wp_tips)"}
SOURCE = "weekly_plan"
DEFAULT_CATEGORIES = os.path.join("pipeline", "final", "categories_20261116.json")
# main.SHOP_CATEGORIES 와 같은 값 (app 을 import 하지 않으려고 복사. 달라지면 여기만 고칩니다)
APP_CATEGORIES = ["냉난방·계절 대비", "습기·곰팡이·해충", "세탁·의류관리", "욕실·청소", "주방 살림",
                  "수납·정리", "침구·패브릭", "공기·생활가전", "집 수리·안전", "차량 관리"]
COUPANG = re.compile(r"coupang\.com|coupa\.ng", re.I)
NAVER = re.compile(r"(^|[/.])naver\.(com|me)\b|naver\.me", re.I)


def nk(s):
    return "".join((s or "").split())


def monday(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def planned_type(p, cats=None):
    """네이버: 수익글(money)=C 결정형 / 해결 질문(kw_type solve)=A 해결형 / 제철 물건 정보글=B 점검·시즌형 / 그 외 A.
    싼세계: 수익글=결정, 정보글=해결."""
    forced = ((cats or {}).get("types") or {}).get(f"{p['platform']}|{p['keyword']}")
    if forced:
        return forced
    money = p.get("track") == "money"
    if p["platform"] == "wordpress":
        return "결정" if money else "해결"
    if money:
        return "C"
    if p.get("kw_type") == "solve":
        return "A"
    if (p.get("r1") or {}).get("shape") == "제철 물건":
        return "B"
    return "A"


def planned_category(p, cats=None):
    table = (cats or {}).get("categories") or {}
    c = table[p["keyword"]] if p["keyword"] in table else p.get("category")  # 파일에 있으면(빈 값=미정 포함) 파일이 우선
    return c if c in APP_CATEGORIES else ""


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except FileNotFoundError:
        return default
    if not txt.strip():
        raise RuntimeError(f"{path} 파일이 비어 있습니다(드라이브 동기화 중일 수 있음). 잠시 뒤 다시 하세요.")
    return json.loads(txt)


def write_json_atomic(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2)  # 앱과 같은 모양
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def scheduled(plan):
    return [p for p in plan["plans"] if p.get("role") == "scheduled" and p.get("platform") in QUEUES]


def pushable(plan, cats=None):
    """넣을 계획 목록: 일정 글(승인 상태로) + 예비 글(승인 안 함, 발행일 없음).
    예비 글은 계획에 플랫폼이 없어서 categories 파일의 reserve_platforms(키워드→플랫폼)로 정합니다."""
    out = list(scheduled(plan))
    rp = (cats or {}).get("reserve_platforms") or {}
    for p in plan["plans"]:
        if p.get("role") == "reserve" and rp.get(p["keyword"]) in QUEUES:
            q = dict(p)
            q["platform"] = rp[p["keyword"]]
            q["publish_date"] = None
            out.append(q)
    return out


def init_products(plan, path):
    if os.path.exists(path):
        print(f"이미 있음(안 덮어씀): {path}")
        return
    items = [{"platform": p["platform"], "keyword": p["keyword"], "title": p.get("final_title") or "",
              "publish_date": p["publish_date"], "products": []}
             for p in sorted(scheduled(plan), key=lambda x: (x["platform"], x["publish_date"], x["keyword"]))]
    doc = {"설명": "글마다 products 목록(name, url)을 채우세요. 네이버 글엔 네이버 쇼핑커넥트 주소, 싼세계 글엔 쿠팡 주소. "
                  "비워 두면 '제품 링크 없음'으로 표시되고 그대로 넣을 수는 있습니다.",
           "week": plan.get("week", {}).get("start"), "items": items}
    write_json_atomic(path, doc)
    print(f"제품 링크 틀 {len(items)}편 저장: {path}")


def load_products(path):
    if not path:
        return {}, []
    doc = read_json(path, {})
    out, warn = {}, []
    for it in doc.get("items", []):
        out[(it["platform"], nk(it["keyword"]))] = it.get("products") or []
    return out, warn


def check_links(platform, products):
    """(경고 목록). 네이버 글에 쿠팡 주소, 싼세계 글에 네이버 주소, 이름·주소 빠짐."""
    w = []
    for pr in products:
        url = (pr.get("url") or "").strip()
        name = pr.get("name") or "(이름 없음)"
        if not url:
            w.append(f"주소 비어 있음: {name}")
        elif platform == "naver" and COUPANG.search(url):
            w.append(f"네이버 글에 쿠팡 주소: {name}")
        elif platform == "wordpress" and NAVER.search(url):
            w.append(f"싼세계 글에 네이버 주소: {name}")
        if not pr.get("name"):
            w.append(f"제품 이름 비어 있음: {url[:40]}")
    return w


def load_categories(path):
    if not path:
        return {}
    return read_json(path, {}) or {}


def build_items(plan, products, plan_file, cats=None):
    """계획 → 대기열 항목 목록 [(platform, item)] (id 는 apply 때 붙임)."""
    out = []
    for p in sorted(pushable(plan, cats), key=lambda x: (x["platform"], x["publish_date"] or "9999", x["keyword"])):
        approved = p.get("role") == "scheduled"
        week = monday(p["publish_date"]) if p.get("publish_date") else plan["week"]["start"]
        prods = products.get((p["platform"], nk(p["keyword"])), [])
        prods = [{"name": x.get("name", ""), "url": x.get("url", "")} for x in prods]
        warns = check_links(p["platform"], prods)
        r1 = p.get("r1") or {}
        pn = p.get("product_need")
        item = {
            "id": None,
            "week": week,
            "keyword": p["keyword"],
            "score": None,
            "total_search_volume": p.get("monthly_total"),
            "competition": "직접추가",
            "approved": approved,
            "published": False,
            "duplicate_of_past_approval": False,
            "duplicate_of_past_approval_week": None,
            "cluster_seed": p.get("seed") or p["keyword"],
            "cluster_role": "대표",
            "target_platform": None,
            "manually_added": True,
            "source": SOURCE,
            "planned_type": planned_type(p, cats),
            "planned_category": planned_category(p, cats),
            "rec_score": None,
            "rec_reason": (p.get("timing") or {}).get("reason") or "",
            "serp": {"peak_week": None, "weeks_to_peak": r1.get("peak"), "compete": None},
            "plan": {
                "version": 1,
                "plan_file": plan_file,
                "publish_date": p["publish_date"],
                "weekday": p.get("weekday") if p.get("publish_date") else None,
                "role": p.get("role"),
                "track": p.get("track"),
                "final_title": p.get("final_title"),
                "alt_titles": p.get("alt_titles") or [],
                "title_pattern": p.get("title_pattern"),
                "intent": p.get("intent"),
                "search_intent": p.get("search_intent"),
                "outline": p.get("outline") or [],
                "related_keywords": p.get("related_keywords") or [],
                "gaps": p.get("gaps") or [],
                "target_chars": p.get("target_chars"),
                "target_images": p.get("target_images"),
                "products": prods,
                "product_status": f"제품 링크 {len(prods)}개" if prods else "제품 링크 없음",
                "link_warnings": warns,
            },
        }
        if isinstance(pn, dict):  # weekly_plan 이 만든 계획만: 필요 수·찾을 조건·링크 종류 (없는 계획은 plan 칸이 지금과 똑같음)
            item["plan"]["product_need"] = {"need": bool(pn.get("need")), "count": pn.get("count") or 0,
                                            "condition": pn.get("condition") or "", "link_kind": pn.get("link_kind") or ""}
        out.append((p["platform"], item))
    return out


def new_id(week, taken):
    while True:
        i = f"{week}_{uuid.uuid4().hex[:8]}"
        if i not in taken:
            return i


def classify(pairs, queues):
    """넣을 것 / 건너뜀(이미 있음) 로 나눕니다. 같은 plan 으로 이미 넣은 항목은 '이미 넣음'."""
    todo, skipped = [], []
    seen = {pf: {nk(q.get("keyword")): q for q in queues[pf]} for pf in QUEUES}
    for pf, item in pairs:
        ex = seen[pf].get(nk(item["keyword"]))
        if ex:
            same = ex.get("source") == SOURCE and ex.get("week") == item["week"]
            skipped.append({"platform": pf, "keyword": item["keyword"], "reason":
                            ("이미 넣음(같은 주 weekly_plan)" if same else
                             f"같은 키워드가 이미 있음(주 {ex.get('week')}, 승인 {'예' if ex.get('approved') else '아니오'}, id {ex.get('id')})")})
        else:
            todo.append((pf, item))
            seen[pf][nk(item["keyword"])] = item  # 계획 안에서 같은 키워드가 두 번 나오는 경우도 막음
    return todo, skipped


def print_table(todo, skipped, warns):
    print("\n| 대기열 파일 | 발행일 | 키워드 | 승인 여부 | 확정 제목 | 글 유형 | 제품 링크 수 |")
    print("|---|---|---|---|---|---|---|")
    for pf, it in todo:
        pl = it["plan"]
        n = len(pl["products"])
        print(f"| {QUEUES[pf]} | {pl['publish_date'] or '(비움)'} | {it['keyword']} | {'승인' if it['approved'] else '승인 안 함(예비)'} | {pl['final_title']} | "
              f"{it['planned_type']}{'' if it['planned_category'] else ' (카테고리 미정)'} | {n if n else '없음'} |")
    print(f"\n승인 {sum(1 for _, i in todo if i['approved'])}편 · 승인 안 함 {sum(1 for _, i in todo if not i['approved'])}편")
    print(f"넣을 항목 {len(todo)}편 (네이버 {sum(1 for p, _ in todo if p == 'naver')} · 싼세계 {sum(1 for p, _ in todo if p == 'wordpress')})")
    print(f"제품 링크 없음 {sum(1 for _, i in todo if not i['plan']['products'])}편")
    if skipped:
        print("\n건너뜀(넣지 않음):")
        for s in skipped:
            print(f"  - {LABEL[s['platform']]} {s['keyword']}: {s['reason']}")
    for _, i in todo:
        for w in i["plan"]["link_warnings"]:
            warns.append(f"{i['keyword']}: {w}")
    if warns:
        print("\n경고:")
        for w in warns:
            print("  - " + w)


def cmd_push(args):
    plan = read_json(args.plan)
    products, _ = load_products(args.products)
    pairs = build_items(plan, products, args.plan.replace("\\", "/"), load_categories(args.categories))
    warns = []
    keys = {(pf, nk(i["keyword"])) for pf, i in pairs}
    for k in products:
        if k not in keys:
            warns.append(f"제품 파일에만 있고 계획 일정에는 없는 글: {k[0]} {k[1]}")
    queues = {pf: read_json(f, []) for pf, f in QUEUES.items()}
    todo, skipped = classify(pairs, queues)
    print_table(todo, skipped, warns)
    if not args.apply:
        print("\n[dry-run] 아무것도 쓰지 않았습니다. 넣으려면 --apply")
        return 0
    if not todo:
        print("\n넣을 항목이 없습니다.")
        return 0
    # 쓰기: 파일마다 백업 → 다시 읽어 중복 재확인 → 끝에 추가 → 쓰기 → 다시 읽어 확인
    os.makedirs("backup", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run = {"time": datetime.now().isoformat(timespec="seconds"), "plan": args.plan, "files": {}, "backups": {}, "undone": False}
    for pf, fname in QUEUES.items():
        mine = [i for p, i in todo if p == pf]
        if not mine:
            continue
        stem, ext = os.path.splitext(fname)
        bk = os.path.join("backup", f"{stem}.backup_before_push_plan_{stamp}{ext}")
        shutil.copy2(fname, bk)
        fresh = read_json(fname, [])
        have = {nk(q.get("keyword")) for q in fresh}
        taken = {q.get("id") for q in fresh}
        added = []
        for it in mine:
            if nk(it["keyword"]) in have:
                print(f"  (쓰기 직전 같은 키워드 발견, 건너뜀) {it['keyword']}")
                continue
            it["id"] = new_id(it["week"], taken)
            taken.add(it["id"])
            fresh.append(it)
            added.append(it["id"])
        before = len(fresh) - len(added)
        write_json_atomic(fname, fresh)
        chk = read_json(fname, [])
        assert len(chk) == before + len(added), f"{fname} 쓴 뒤 항목 수가 안 맞음"
        run["files"][fname] = added
        run["backups"][fname] = bk
        print(f"쓴 파일 {fname}: +{len(added)} (총 {len(chk)}), 백업 {bk}")
    log = read_json(LOG_PATH, []) or []
    log.append(run)
    write_json_atomic(LOG_PATH, log)
    print(f"넣은 id 기록: {LOG_PATH}  (되돌리기: python tools/push_plan.py --undo)")
    return 0


def parse_products_txt(path, items):
    """txt → {(platform, keyword정규화): [제품…]}, 경고 목록. items = 제품 json 의 items(플랫폼·키워드 틀)."""
    by_kw = {}
    for it in items:
        by_kw.setdefault(nk(it["keyword"]), []).append(it["platform"])
    out, warns = {}, []
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) != 3 or not all(parts):
            warns.append(f"{n}번째 줄 형식이 `키워드 | 제품명 | 링크` 가 아님: {line[:40]}")
            continue
        kw, name, url = parts
        pfs = by_kw.get(nk(kw))
        if not pfs:
            warns.append(f"{n}번째 줄: 계획에 없는 키워드 '{kw}'")
            continue
        by_url = "wordpress" if COUPANG.search(url) else ("naver" if NAVER.search(url) else None)
        if len(pfs) == 1:
            pf = pfs[0]
            if by_url and by_url != pf:
                warns.append(f"{n}번째 줄 '{kw}': {LABEL[pf]} 글인데 {LABEL[by_url]} 주소입니다(그래도 {LABEL[pf]} 글에 넣음)")
        else:
            if not by_url:
                warns.append(f"{n}번째 줄 '{kw}': 두 플랫폼에 다 있는데 주소로 플랫폼을 못 가려 건너뜀(쿠팡/네이버 주소로 적어 주세요)")
                continue
            if by_url not in pfs:
                warns.append(f"{n}번째 줄 '{kw}': 주소 플랫폼({LABEL[by_url]})의 글이 계획에 없어 건너뜀")
                continue
            pf = by_url
        out.setdefault((pf, nk(kw)), []).append({"name": name, "url": url})
    return out, warns


def cmd_products_txt(args):
    if not args.products:
        print("--products (만들 json 경로)가 필요합니다")
        return 2
    doc = read_json(args.products)
    if doc is None:
        print(f"틀 json 이 없습니다. 먼저 --init-products 로 만드세요: {args.products}")
        return 2
    found, warns = parse_products_txt(args.products_txt, doc["items"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, ext = os.path.splitext(args.products)
    bk = f"{stem}.backup_before_products_txt_{stamp}{ext}"
    shutil.copy2(args.products, bk)
    for it in doc["items"]:
        it["products"] = found.get((it["platform"], nk(it["keyword"])), [])
    write_json_atomic(args.products, doc)
    n_with = sum(1 for it in doc["items"] if it["products"])
    print(f"제품 링크 json 저장: {args.products} (제품 있는 글 {n_with}/{len(doc['items'])}편, 제품 {sum(len(v) for v in found.values())}개), 백업 {bk}")
    for w in warns:
        print("  경고: " + w)
    return 0


def cmd_update_products(args):
    products, _ = load_products(args.products)
    if not products:
        print("--products json 에서 제품을 읽지 못했습니다")
        return 2
    log = read_json(LOG_PATH, []) or []
    ids = {}
    for run in log:
        for fname, lst in run.get("files", {}).items():
            ids.setdefault(fname, set()).update(lst)
    if not ids:
        print("넣은 항목 기록(push_log)이 없습니다.")
        return 0
    pf_of = {v: k for k, v in QUEUES.items()}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for fname, idset in ids.items():
        pf = pf_of.get(fname)
        if not pf or not os.path.exists(fname):
            continue
        queue = read_json(fname, [])
        changed = []
        for q in queue:
            if q.get("id") not in idset or not isinstance(q.get("plan"), dict):
                continue
            key = (pf, nk(q.get("keyword")))
            if key not in products:
                continue
            new = [{"name": x.get("name", ""), "url": x.get("url", "")} for x in products[key]]
            if new == q["plan"].get("products"):
                continue
            changed.append((q, new))
        print(f"{fname}: 바뀔 항목 {len(changed)}")
        for q, new in changed:
            print(f"  - {q['keyword']} ({'승인됨' if q.get('approved') else '대기'}): 제품 {len(q['plan'].get('products') or [])}개 → {len(new)}개")
            for w in check_links(pf, new):
                print(f"      경고: {w}")
        if not args.apply or not changed:
            continue
        os.makedirs("backup", exist_ok=True)
        stem, ext = os.path.splitext(fname)
        bk = os.path.join("backup", f"{stem}.backup_before_update_products_{stamp}{ext}")
        shutil.copy2(fname, bk)
        fresh = read_json(fname, [])
        by_id = {q.get("id"): new for q, new in changed}
        n = 0
        for q in fresh:
            if q.get("id") in by_id and isinstance(q.get("plan"), dict):
                prods = by_id[q["id"]]
                q["plan"]["products"] = prods
                q["plan"]["product_status"] = f"제품 링크 {len(prods)}개" if prods else "제품 링크 없음"
                q["plan"]["link_warnings"] = check_links(pf, prods)
                n += 1
        write_json_atomic(fname, fresh)
        print(f"  쓴 항목 {n}, 백업 {bk}")
    if not args.apply:
        print("\n[dry-run] 아무것도 쓰지 않았습니다. 바꾸려면 --apply")
    return 0


def cmd_undo(args):
    log = read_json(LOG_PATH, []) or []
    runs = [r for r in log if not r.get("undone")]
    if not runs:
        print("되돌릴 기록이 없습니다.")
        return 0
    run = runs[-1]
    print(f"되돌릴 기록: {run['time']} ({run['plan']})")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("backup", exist_ok=True)
    pf_of = {v: k for k, v in QUEUES.items()}
    for fname, ids in run["files"].items():
        ids = set(ids)
        queue = read_json(fname, [])
        articles = read_json(ARTICLES[pf_of[fname]], {}) or {}
        keep, removed, refused = [], [], []
        for q in queue:
            if q.get("id") in ids:
                if q.get("published") or q.get("id") in articles:
                    refused.append(q)  # 그 사이 발행됐거나 글이 만들어진 항목은 지우지 않음(승인만 된 것은 뺄 수 있음)
                    keep.append(q)
                else:
                    removed.append(q)
            else:
                keep.append(q)
        stem, ext = os.path.splitext(fname)
        bk = os.path.join("backup", f"{stem}.backup_before_push_undo_{stamp}{ext}")
        shutil.copy2(fname, bk)
        if removed:
            write_json_atomic(fname, keep)
        print(f"{fname}: 뺀 항목 {len(removed)}, 발행·작성돼서 그대로 둔 항목 {len(refused)}, 백업 {bk}")
        for q in refused:
            print(f"  - 그대로 둠: {q['keyword']} (id {q['id']})")
    run["undone"] = True
    write_json_atomic(LOG_PATH, log)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan")
    ap.add_argument("--products")
    ap.add_argument("--apply", action="store_true", help="실제로 대기열에 씀 (기본은 dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="기본값. 보여 주기만 함")
    ap.add_argument("--undo", action="store_true", help="직전에 넣은 항목만 되돌림")
    ap.add_argument("--queue-dir", help="시험용: 대기열·articles 파일이 든 폴더(복사본). 백업·기록도 그 폴더 안에 만듭니다")
    ap.add_argument("--init-products", metavar="PATH", help="제품 링크 틀 파일 만들기(--plan 필요)")
    ap.add_argument("--products-txt", metavar="PATH", help="`키워드 | 제품명 | 링크` txt 로 --products json 만들기")
    ap.add_argument("--update-products", action="store_true", help="넣은 항목의 plan 칸 제품 링크만 --products 로 바꿈(--apply 로 씀)")
    ap.add_argument("--categories", default=DEFAULT_CATEGORIES, help="카테고리·글 유형 덮어쓰기 파일(없으면 계획 값)")
    args = ap.parse_args()
    os.chdir(ROOT)
    if args.categories == DEFAULT_CATEGORIES and not os.path.exists(os.path.join(ROOT, DEFAULT_CATEGORIES)):
        args.categories = None
    for k in ("plan", "products", "init_products", "products_txt", "categories"):
        if getattr(args, k):
            setattr(args, k, os.path.abspath(getattr(args, k)) if args.queue_dir else getattr(args, k))
    if args.queue_dir:
        os.chdir(args.queue_dir)
        os.makedirs(os.path.join("pipeline", "final"), exist_ok=True)
    if args.undo:
        return cmd_undo(args)
    if args.products_txt:
        return cmd_products_txt(args)
    if args.update_products:
        return cmd_update_products(args)
    if not args.plan:
        ap.error("--plan 이 필요합니다")
    if args.init_products:
        init_products(read_json(args.plan), args.init_products)
        return 0
    if args.apply and args.dry_run:
        ap.error("--apply 와 --dry-run 은 같이 못 씁니다")
    return cmd_push(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
