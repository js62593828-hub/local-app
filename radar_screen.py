"""N데스크피커(radar_naver) 편집기 ② 사진 화면용 보조 함수 (Flask 를 가져다 쓰지 않음, 이미지 생성 API 안 부름).

하는 일: 원고(본문+메모)의 표시를 읽어 화면에 보여 줄 값 만들기 / 후보 사진 찾기 / 고른 사진·GIF 를 글 사진 폴더로 복사 /
표지 만들기 / 고른 결과를 글 사진 폴더 안 photos.json 하나에 저장. 대기열·장부 json 에는 아무것도 쓰지 않는다.
"""
import json
import os
import re
import shutil
from datetime import datetime
import html as _html
from urllib.parse import urlparse, parse_qs

import radar_images
import shop_formatter

RUNS_ROOT = os.path.join("pipeline", "candidates_run")  # 상품 사진·GIF·approved/links.json 이 있는 실행 폴더들
PHOTO_ROOT = os.path.join("static", "generated")  # 글 사진 폴더의 윗 폴더(시험에서는 바꿔 쓴다)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
COVER_NAME = "cover.jpg"
PHOTOS_JSON = "photos.json"


# ── 작은 도구 ────────────────────────────────────────────────
def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json_atomic(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _rel(path):
    return path.replace("\\", "/")


def allowed_roots():
    return [RUNS_ROOT, PHOTO_ROOT]


def is_allowed_file(rel_path):
    """화면에 보여 줄 파일인지(허용된 폴더 안 + 그림 파일). 폴더 밖으로 나가는 주소(..)는 막는다."""
    if not rel_path or not rel_path.lower().endswith(IMG_EXT + (".gif",)):
        return False
    real = os.path.realpath(rel_path)
    if not os.path.isfile(real):
        return False
    for root in allowed_roots():
        r = os.path.realpath(root)
        if real == r or real.startswith(r + os.sep):
            return True
    return False


# ── 상품 찾기 (approved.json · links.json 의 상품명과 정확히 같을 때만 자동) ──
def list_runs():
    if not os.path.isdir(RUNS_ROOT):
        return []
    return sorted((d for d in os.listdir(RUNS_ROOT) if d.endswith("_radar_naver")), reverse=True)


def _id_from_url(url):
    m = re.search(r"/products/(\d+)", url or "")
    return m.group(1) if m else ""


def load_products():
    """반환: (by_name, by_id)
    by_name[상품명] = {"id", "run", "ok": 자동 확정 여부, "note"}  — 상품명이 approved·links 에서 정확히 같을 때만 ok
    by_id[상품id]  = {"id", "run", "name"}  — 상품 고르기 칸 목록용(새 실행 우선)"""
    by_name, by_id = {}, {}
    for run in list_runs():
        base = os.path.join(RUNS_ROOT, run)
        links = _read_json(os.path.join(base, "links.json")) or {}
        appr = _read_json(os.path.join(base, "approved.json")) or {}
        in_links, in_appr = {}, {}
        for it in links.get("items") or []:
            if it.get("name") and it.get("product_id"):
                in_links.setdefault(it["name"], str(it["product_id"]))
        for kw in appr.get("keywords") or []:
            for pk in kw.get("picks") or []:
                pid = _id_from_url(pk.get("url"))
                if pk.get("name") and pid:
                    in_appr.setdefault(pk["name"], pid)
        for name in set(in_links) | set(in_appr):
            a, b = in_links.get(name), in_appr.get(name)
            if name in by_name:
                continue  # 더 새 실행에서 이미 찾음
            if a and b and a != b:
                by_name[name] = {"id": "", "run": run, "ok": False, "note": "links.json 과 approved.json 의 상품 id 가 달라요"}
            else:
                by_name[name] = {"id": a or b, "run": run, "ok": True, "note": "approved·links 상품명과 같음" if (a and b) else ("links.json 에서만 찾음" if a else "approved.json 에서만 찾음")}
            pid = a or b
            if pid and pid not in by_id:
                by_id[pid] = {"id": pid, "run": run, "name": name}
    return by_name, by_id


def _rep_info(pid, run):
    """대표 이미지(img/)와 판정. (경로 or '', 판정 dict or None)"""
    base = os.path.join(RUNS_ROOT, run)
    rb = _read_json(os.path.join(base, "img", "rep_badge.json")) or {}
    j = rb.get(pid)
    if j and j.get("file"):
        p = os.path.join(base, "img", j["file"])
        return (p if os.path.exists(p) else ""), j
    # 판정 칸이 없으면 approved.json 의 순번으로 찾음: k<키워드순번>_r<rank>.jpg (판정 모름)
    appr = _read_json(os.path.join(base, "approved.json")) or {}
    for ki, kw in enumerate(appr.get("keywords") or [], 1):
        for pk in kw.get("picks") or []:
            if _id_from_url(pk.get("url")) == pid:
                p = os.path.join(base, "img", f"k{ki}_r{pk.get('rank')}.jpg")
                return (p if os.path.exists(p) else ""), None
    return "", None


def _img_meta(pid, run):
    d = os.path.join(RUNS_ROOT, run, "research", "_img", pid)
    return d, (_read_json(os.path.join(d, "images.json")) or {})


def product_candidates(pid, run):
    """한 상품의 후보 사진. 제외 규칙(뱃지·글자 많음·확인 필요)에 걸린 것은 빼고 개수만 센다.
    반환: {"items": [{"path","label","group"}], "excluded": 제외한 장수}"""
    items, excluded = [], 0
    rep_path, rep_j = _rep_info(pid, run)
    if rep_path:
        if radar_images._rep_bad(rep_j):
            excluded += 1
        else:
            items.append({"path": _rel(rep_path), "label": os.path.basename(rep_path), "group": "대표 이미지", "kind": "대표", "pid": pid})
    d, meta = _img_meta(pid, run)
    rows = sorted(meta.get("images") or [], key=lambda r: (r.get("순번") or 999, r.get("file", "")))
    for r in rows:
        fp = os.path.join(d, r.get("file", "")) if r.get("file") else ""
        if not fp or not os.path.exists(fp) or not fp.lower().endswith(IMG_EXT):
            continue
        if any(r.get(n) for n in ("뱃지", "글자 많음", "확인 필요", "뱃지 확인 필요")):
            excluded += 1
            continue
        items.append({"path": _rel(fp), "label": r["file"], "group": "캐러셀", "kind": "캐러셀", "pid": pid})
    det = os.path.join(RUNS_ROOT, run, "research", "_detail", pid)
    for sub in ("pieces", "gallery"):
        sd = os.path.join(det, sub)
        if os.path.isdir(sd):
            for fn in sorted(os.listdir(sd)):
                if fn.lower().endswith(IMG_EXT):
                    items.append({"path": _rel(os.path.join(sd, fn)), "label": f"{sub}/{fn}", "group": "상세 캡처", "kind": "상세", "pid": pid})
    return {"items": items, "excluded": excluded}


def product_label(pid, run, name):
    """화면 이름표: images.json 의 짧은 이름이 있으면 그것, 없으면 상품명 앞 20자."""
    _, meta = _img_meta(pid, run)
    short = (meta.get("짧은 이름") or "").strip()
    return short or (name or "")[:20].strip()


_PATH_ID_RE = re.compile(r"/(?:_img|_detail)/(\d+)/")
_NOTE_REP_RE = re.compile(r"img/(k\d+_r\d+\.jpg)")


def slot_product_id(note, groups):
    """메모 칸 설명(note)에서 그 칸의 상품 id 찾기. 못 찾거나 둘 이상이면 ''(지어내지 않음).
    순서: ① 설명 안의 대표 사진 경로(img/k#_r#.jpg) ② _img/<id>/·_detail/<id>/ 경로 ③ 상품명·이름표가 설명 안에 있음 ④ 괄호 안 낱말이 모두 상품명에 있음."""
    note = note or ""
    ids = {g["id"] for g in groups}
    m = _NOTE_REP_RE.search(note)
    if m:
        for g in groups:
            if any(it["kind"] == "대표" and it["label"] == m.group(1) for it in g["items"]):
                return g["id"]
    m = _PATH_ID_RE.search(note)
    if m and m.group(1) in ids:
        return m.group(1)
    hit = [g["id"] for g in groups if (g["name"] and g["name"] in note) or (g.get("label") and len(g["label"]) >= 3 and g["label"] in note)]
    if len(set(hit)) == 1:
        return hit[0]
    pm = re.search(r"\(([^)]+)\)", note)
    if pm:
        toks = pm.group(1).split()
        hit = [g["id"] for g in groups if toks and all(t in (g["name"] or "") for t in toks)]
        if len(set(hit)) == 1:
            return hit[0]
    return ""


def product_gif(pid, run):
    p = os.path.join(RUNS_ROOT, run, "research", "_img", pid, pid + ".gif")
    return p if os.path.exists(p) else ""


# ── 사진 폴더 · photos.json ──────────────────────────────────
def photo_dir(channel_folder, title_slug):
    return os.path.join(PHOTO_ROOT, channel_folder, title_slug)


def load_state(pdir):
    st = _read_json(os.path.join(pdir, PHOTOS_JSON)) or {}
    st.setdefault("cover", "")
    st.setdefault("slots", {})
    st.setdefault("slot_sources", {})
    st.setdefault("gifs", {})
    st.setdefault("product_pick", {})  # 상품명 → 사람이 고른 상품 id
    st.setdefault("cover_pick", [])  # 사람이 고른 표지 사진(후보 폴더 안 경로). 비면 자동으로 고름
    return st


def save_state(pdir, st):
    st["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _write_json_atomic(os.path.join(pdir, PHOTOS_JSON), st)


def _exists(pdir, name):
    return bool(name) and os.path.isfile(os.path.join(pdir, name))


# ── 원고 읽기 ────────────────────────────────────────────────
_SLOT_LINE = re.compile(r"^\s*(\d+)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*$")


def slot_hints(memo):
    """메모 [사진자리] 블록의 '번호 | 종류 | 소제목 | 내용' 줄 → {번호: {kind, h2, note}} (화면 안내용)."""
    out, lines, i = {}, (memo or "").replace("\r\n", "\n").split("\n"), 0
    while i < len(lines):
        if lines[i].strip() == "[사진자리]":
            i += 1
            while i < len(lines) and lines[i].strip():
                m = _SLOT_LINE.match(lines[i])
                if m:
                    out[int(m.group(1))] = {"kind": m.group(2), "h2": m.group(3), "note": m.group(4)}
                i += 1
        else:
            i += 1
    return out


def read_markers(raw, memo):
    return radar_images.parse_photo_markers((raw or "") + "\n\n" + (memo or ""))


def plan_genre(item):
    """그 주 plan.json 에서 이 글(키워드)의 갈래. 없으면 ''(지어내지 않음)."""
    plan = (item or {}).get("plan") or {}
    pf = plan.get("plan_file")
    data = _read_json(pf) if pf else None
    for p in (data or {}).get("plans") or []:
        if p.get("keyword") == (item or {}).get("keyword"):
            return (p.get("radar") or {}).get("갈래") or ""
    return ""


# ── 화면 값 만들기 ───────────────────────────────────────────
COVER_PICK_MAX_A = 3  # 틀 A(모음형) 표지에 고를 수 있는 사진 수(2~3개)


def cover_frame(item):
    """그 글의 틀. 대기열 항목의 planned_type 이 'A' 면 A, 아니면 B(한 장)."""
    return "A" if ((item or {}).get("planned_type") or "").upper() == "A" else "B"


def valid_cover_picks(state):
    """photos.json 에 적힌 사람이 고른 표지 사진 중 지금도 쓸 수 있는 파일만(순서 그대로)."""
    return [p for p in (state or {}).get("cover_pick", []) if is_allowed_file(p)]


def _cover_inputs(card_products, item, state=None):
    """표지에 쓸 값. 큰 제목 = 글의 검색어, 갈래 = 그 주 plan.json, 짧은 이름 = 표지 사진 상품의 images.json.
    state(photos.json)에 사람이 고른 표지 사진이 있으면 자동 고르기 대신 그것을 쓴다(paths 에 모두, pick["path"] 는 첫 장)."""
    plan = (item or {}).get("plan") or {}
    title = (plan.get("search_query") or (item or {}).get("keyword") or "").strip()
    genre = plan_genre(item)
    pick = {"path": None, "no_photo": True, "reason": "원고에 찾은 상품이 없음", "skipped": []}
    short, who = "", ""
    for cp in card_products:
        if not cp["prod"]:
            continue
        pid, run = cp["prod"]["id"], cp["prod"]["run"]
        rep_path, rep_j = _rep_info(pid, run)
        d, meta = _img_meta(pid, run)
        pk = radar_images.pick_cover_photo(rep_path, rep_j, d)
        if not pk["no_photo"]:
            pick, who = pk, cp["name"]
            short = (meta.get("짧은 이름") or "").strip()
            break
        pick = pk
    paths = [pick["path"]] if pick["path"] else []
    picked = valid_cover_picks(state)
    if picked:
        paths = picked
        pick = {"path": picked[0], "no_photo": False, "reason": "직접 고른 사진 %d장" % len(picked), "skipped": []}
        who = ""  # 고른 사진의 상품 이름표는 따로 안 찾음(짧은 이름은 자동 고르기 때 값 그대로)
    return {"title": title, "genre": genre, "short": short, "pick": pick, "product": who, "paths": paths, "frame": cover_frame(item)}


def resolve_cards(cards, state, by_name, by_id):
    """[상품카드: 상품명] 마다 상품 찾기. 사람이 고른 것 > 자동(정확히 같을 때만)."""
    out = []
    for c in cards:
        name = c["name"]
        pick_id = state["product_pick"].get(name)
        prod, status, note = None, "확인 필요", ""
        if pick_id and pick_id in by_id:
            prod, status, note = {"id": pick_id, "run": by_id[pick_id]["run"]}, "고름", "직접 고른 상품: " + by_id[pick_id]["name"]
        elif name in by_name and by_name[name]["ok"]:
            prod, status, note = {"id": by_name[name]["id"], "run": by_name[name]["run"]}, "자동", by_name[name]["note"]
        elif name in by_name:
            note = by_name[name]["note"]
        else:
            note = "approved.json·links.json 에 같은 상품명이 없어요"
        out.append({"name": name, "h2": c.get("h2"), "line": c["line"], "prod": prod, "status": status, "note": note})
    return out


def build_screen(raw, memo, item, pdir):
    """② 화면에 보여 줄 값 전부. 파일은 만들지 않는다(읽기만)."""
    parsed = read_markers(raw, memo)
    markers = parsed["markers"]
    slots = [m for m in markers if m["kind"] == "사진"]
    cards = [m for m in markers if m["kind"] == "상품카드"]
    has_cover_mark = any(m["kind"] == "표지" for m in markers)
    if not (slots or cards or has_cover_mark):
        return {"enabled": False}
    st = load_state(pdir)
    by_name, by_id = load_products()
    cps = resolve_cards(cards, st, by_name, by_id)
    hints = slot_hints(memo)

    groups, seen = [], set()
    for cp in cps:
        if cp["prod"] and cp["prod"]["id"] not in seen:
            seen.add(cp["prod"]["id"])
            cand = product_candidates(cp["prod"]["id"], cp["prod"]["run"])
            groups.append({"name": cp["name"], "id": cp["prod"]["id"], "label": product_label(cp["prod"]["id"], cp["prod"]["run"], cp["name"]),
                           "items": cand["items"], "excluded": cand["excluded"]})
    label_of = {g["id"]: g["label"] for g in groups}
    path_pid = {it["path"]: g["id"] for g in groups for it in g["items"]}
    for g in groups:
        for it in g["items"]:
            it["label_name"] = g["label"]

    warnings, nums = [], [s["number"] for s in slots]
    for n in sorted({x for x in nums if nums.count(x) > 1}):
        warnings.append(f"[사진: {n}] 번호가 원고에 두 번 이상 있어요")
    for n in sorted(set(hints) - set(nums)):
        warnings.append(f"메모 [사진자리] 에는 {n}번이 있는데 본문에 [사진: {n}] 이 없어요")
    for n in sorted(set(nums) - set(hints)):
        if hints:
            warnings.append(f"본문 [사진: {n}] 이 메모 [사진자리] 에 없어요")

    slot_views = []
    for s in slots:
        n = s["number"]
        chosen = st["slots"].get(str(n), "")
        ok = _exists(pdir, chosen)
        h = hints.get(n) or {}
        src = st["slot_sources"].get(str(n), "")
        want = slot_product_id(h.get("note", ""), groups) if h.get("kind") == "제품" else ""
        cpid = path_pid.get(src, "") or (_PATH_ID_RE.search(src).group(1) if _PATH_ID_RE.search(src) else "")
        slot_views.append({
            "want_pid": want, "want_label": label_of.get(want, ""),
            "chosen_pid": cpid if ok else "", "chosen_label": label_of.get(cpid, "") if ok else "",
            "mismatch": bool(ok and want and cpid and cpid != want),
            "number": n, "h2": s.get("h2") or "", "line": s["line"], "kind": h.get("kind", ""), "note": h.get("note", ""),
            "chosen": chosen if ok else "", "chosen_missing": bool(chosen) and not ok,
            "chosen_url": _rel(os.path.join(pdir, chosen)) if ok else "",
            "chosen_src": st["slot_sources"].get(str(n), ""),
        })

    gif_views = []
    for cp in cps:
        gf = st["gifs"].get(cp["name"], "")
        src = product_gif(cp["prod"]["id"], cp["prod"]["run"]) if cp["prod"] else ""
        copied = _exists(pdir, gf)
        gif_views.append({
            **cp, "gif_src": _rel(src) if src else "", "gif_copied": gf if copied else "",
            "gif_copied_url": _rel(os.path.join(pdir, gf)) if copied else "",
        })

    ci = _cover_inputs(cps, item, st)
    cover_ok = _exists(pdir, st["cover"])
    cover = {
        "title": ci["title"], "genre": ci["genre"], "short": ci["short"], "product": ci["product"],
        "photo": _rel(ci["pick"]["path"]) if ci["pick"]["path"] else "", "photo_reason": ci["pick"]["reason"],
        "skipped": ci["pick"]["skipped"], "no_photo": ci["pick"]["no_photo"],
        "frame": ci["frame"], "picks": valid_cover_picks(st), "pick_max": COVER_PICK_MAX_A if ci["frame"] == "A" else 1,
        "used": [_rel(x) for x in ci["paths"]],
        "made": st["cover"] if cover_ok else "", "made_url": _rel(os.path.join(pdir, st["cover"])) if cover_ok else "",
    }

    n_cards_ok = sum(1 for cp in cps if cp["prod"])
    filled = sum(1 for v in slot_views if v["chosen"])
    summary = {
        "slots": len(slot_views), "filled": filled, "recommend": radar_images.body_photo_count(raw or ""),
        "gifs": sum(1 for g in gif_views if g["gif_copied"]), "cards": len(cps),
        "need_check": sum(1 for cp in cps if not cp["prod"]),
        "gif_missing": sum(1 for g in gif_views if g["prod"] and not g["gif_src"]),
        "cards_ok": n_cards_ok,
    }
    all_products = sorted(({"id": i, "name": v["name"]} for i, v in by_id.items()), key=lambda x: x["name"])
    return {
        "enabled": True, "cover": cover, "slots": slot_views, "groups": groups, "gifs": gif_views,
        "summary": summary, "warnings": warnings, "all_products": all_products, "photo_dir": _rel(pdir),
        "tables": parsed["tables"], "badges": badge_views(raw, pdir),
    }


# ── 동작 (눌렀을 때) ─────────────────────────────────────────
def make_cover_file(raw, memo, item, pdir):
    """표지 만들기/다시 만들기 (Pillow, 0원). 결과 cover.jpg 를 글 사진 폴더에 저장하고 photos.json 에 파일 이름을 적는다."""
    markers = read_markers(raw, memo)["markers"]
    cards = [m for m in markers if m["kind"] == "상품카드"]
    st = load_state(pdir)
    by_name, by_id = load_products()
    cps = resolve_cards(cards, st, by_name, by_id)
    ci = _cover_inputs(cps, item, st)
    if not ci["title"]:
        return False, "큰 제목으로 쓸 검색어가 없어요"
    picked = valid_cover_picks(st)
    # 직접 고른 사진: 틀 A 는 모음형(2장 이상일 때), 틀 B 는 한 장. 안 골랐으면 전과 똑같이 자동 한 장.
    photo = picked if (picked and ci["frame"] == "A") else ci["pick"]["path"]
    meta = radar_images.make_cover(photo, os.path.join(pdir, COVER_NAME), ci["genre"], ci["title"], ci["short"], frame=(ci["frame"] if picked else None))
    st["cover"] = COVER_NAME
    st["cover_info"] = {"title": ci["title"], "genre": ci["genre"], "short": ci["short"], "photo": _rel(ci["pick"]["path"] or ""),
                        "photos": [_rel(x) for x in ci["paths"]], "warnings": meta.get("warnings", [])}
    save_state(pdir, st)
    msg = "표지를 만들었어요"
    if meta.get("warnings"):
        msg += " (주의: " + ", ".join(meta["warnings"]) + ")"
    return True, msg


def choose_cover_photo(pdir, item, src):
    """표지 사진 고르기(0원, 파일 복사 없음 — 경로만 photos.json 에 적음).
    틀 B: 누른 사진 한 장으로 바꿈. 틀 A: 누를 때마다 넣고 빼기(최대 3장). src 가 빈 값이면 모두 취소(자동 고르기로 돌아감).
    표지 그림은 [표지 다시 만들기]를 눌러야 바뀐다."""
    st = load_state(pdir)
    if not src:
        st["cover_pick"] = []
        save_state(pdir, st)
        return True, "표지 사진 선택을 모두 취소했어요(자동으로 고름)"
    if not is_allowed_file(src) or not os.path.realpath(src).startswith(os.path.realpath(RUNS_ROOT) + os.sep):
        return False, "고를 수 없는 파일이에요"
    src = _rel(src)
    cur = valid_cover_picks(st)
    if cover_frame(item) == "A":
        if src in cur:
            cur.remove(src)
            msg = "표지 사진에서 뺐어요(%d장)" % len(cur)
        elif len(cur) >= COVER_PICK_MAX_A:
            return False, "틀 A 표지 사진은 %d장까지예요. 하나를 눌러 빼고 다시 고르세요" % COVER_PICK_MAX_A
        else:
            cur.append(src)
            msg = "표지 사진에 넣었어요(%d장)" % len(cur)
    else:
        cur = [src]
        msg = "표지 사진을 %s 로 골랐어요" % os.path.basename(src)
    st["cover_pick"] = cur
    save_state(pdir, st)
    return True, msg + " — [표지 다시 만들기]를 누르면 반영돼요"


def table_slot_map(raw):
    """본문 마크다운 표마다 바로 아래(빈 줄 제외) [사진: n] 이 있으면 n. 반환 [(표 rows, n 또는 None), …] (표 순서대로). 원고는 안 고침."""
    lines = (raw or "").replace(chr(13) + chr(10), chr(10)).split(chr(10))
    out, i, n = [], 0, len(lines)
    while i < n:
        ln = lines[i].strip()
        if radar_images._MARK_COVER.match(ln) or radar_images._MARK_SLOT.match(ln):
            while i < n and lines[i].strip():
                i += 1
            continue
        if ln.startswith("|"):
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            block = lines[i:j]
            if len(block) >= 2 and _is_sep(block[1]):
                rows = [_split_row(block[0])] + [_split_row(x) for x in block[2:]]
                k = j
                while k < n and not lines[k].strip():
                    k += 1
                m = radar_images._MARK_PHOTO.match(lines[k].strip()) if k < n else None
                out.append((rows, int(m.group(1)) if m else None))
                i = j
                continue
        i += 1
    return out


TABLE_FILE_PREFIX = "table"


def make_table_image_file(raw, pdir, number):
    """표 [사진: number] 칸에 쓸 표 그림을 만들어(Pillow, 0원) 글 사진 폴더에 table<번호>.jpg 로 저장하고 photos.json 칸에 적는다."""
    pair = next((rows for rows, n in table_slot_map(strip_notice(raw)) if n == number), None)
    if not pair:
        return False, f"{number}번 [사진: {number}] 바로 위에 마크다운 표가 없어요"
    os.makedirs(pdir, exist_ok=True)
    name = f"{TABLE_FILE_PREFIX}{number}.jpg"
    info = radar_images.table_image(pair, os.path.join(pdir, name), _table_title(raw, number), "")
    if info.get("skipped"):
        return False, "; ".join(info["warnings"])
    st = load_state(pdir)
    old = st["slots"].get(str(number))
    if old and old != name and _exists(pdir, old):
        os.remove(os.path.join(pdir, old))
    st["slots"][str(number)] = name
    st["slot_sources"][str(number)] = "표 그림(앱이 만듦)"
    save_state(pdir, st)
    msg = f"{number}번 칸에 표 그림을 만들었어요(가로 {info['width']}·세로 {info['height']})"
    if info["warnings"]:
        msg += " (주의: " + ", ".join(info["warnings"]) + ")"
    return True, msg


def auto_table_images(raw, memo, pdir):
    """② 화면을 열 때: 메모 [사진자리] 종류가 '표'인 칸에 그림이 없으면 표 그림을 만든다(이미 있으면 그대로 둠). 반환: 만든 번호 목록."""
    hints = slot_hints(memo)
    made = []
    st = load_state(pdir)
    for n, h in sorted(hints.items()):
        if h.get("kind") != "표" or _exists(pdir, st["slots"].get(str(n), "")):
            continue
        try:
            ok, _msg = make_table_image_file(raw, pdir, n)
        except Exception:
            ok = False
        if ok:
            made.append(n)
    return made


def _table_title(raw, number):
    """표 그림 맨 위 제목: 그 표가 속한 ## 소제목(없으면 빈 글자)."""
    h2 = ""
    for ln in (raw or "").replace(chr(13) + chr(10), chr(10)).split(chr(10)):
        if ln.startswith("## "):
            h2 = ln[3:].strip()
        m = radar_images._MARK_PHOTO.match(ln.strip())
        if m and int(m.group(1)) == number:
            return h2
    return h2


def choose_slot(pdir, number, src):
    """칸에 사진 하나 고르기: 글 사진 폴더로 복사(slot<번호>_<원래 이름>). src 가 빈 값이면 선택 취소."""
    st = load_state(pdir)
    key = str(number)
    old = st["slots"].get(key)
    if not src:
        if old and _exists(pdir, old):
            os.remove(os.path.join(pdir, old))
        st["slots"].pop(key, None)
        st["slot_sources"].pop(key, None)
        save_state(pdir, st)
        return True, f"{number}번 칸 선택을 취소했어요"
    if not is_allowed_file(src) or not os.path.realpath(src).startswith(os.path.realpath(RUNS_ROOT) + os.sep):
        return False, "고를 수 없는 파일이에요"
    base = os.path.basename(src)
    dst_name = f"slot{number}_{base}"
    os.makedirs(pdir, exist_ok=True)
    shutil.copyfile(src, os.path.join(pdir, dst_name))
    if old and old != dst_name and _exists(pdir, old):
        os.remove(os.path.join(pdir, old))
    st["slots"][key] = dst_name
    st["slot_sources"][key] = _rel(src)
    save_state(pdir, st)
    return True, f"{number}번 칸에 {base} 를 골랐어요"


def copy_gif(pdir, name, raw, memo):
    """상품 GIF 를 글 사진 폴더로 복사(<상품id>.gif). 상품은 지금 화면과 같은 방법으로 찾는다."""
    markers = read_markers(raw, memo)["markers"]
    st = load_state(pdir)
    by_name, by_id = load_products()
    cps = resolve_cards([m for m in markers if m["kind"] == "상품카드"], st, by_name, by_id)
    cp = next((c for c in cps if c["name"] == name), None)
    if not cp or not cp["prod"]:
        return False, "이 상품은 아직 확인이 필요해요(상품을 먼저 고르세요)"
    src = product_gif(cp["prod"]["id"], cp["prod"]["run"])
    if not src:
        return False, "GIF 가 없어요"
    os.makedirs(pdir, exist_ok=True)
    dst_name = cp["prod"]["id"] + ".gif"
    shutil.copyfile(src, os.path.join(pdir, dst_name))
    st["gifs"][name] = dst_name
    save_state(pdir, st)
    return True, f"GIF 를 가져왔어요({dst_name})"


def pick_product(pdir, name, pid):
    """확인 필요인 상품카드에 상품을 사람이 지정(빈 값이면 지정 취소)."""
    st = load_state(pdir)
    if not pid:
        st["product_pick"].pop(name, None)
        save_state(pdir, st)
        return True, "상품 지정을 취소했어요"
    _, by_id = load_products()
    if pid not in by_id:
        return False, "없는 상품이에요"
    st["product_pick"][name] = pid
    save_state(pdir, st)
    return True, "상품을 지정했어요: " + by_id[pid]["name"]


# ── ③ 최종본 만들기 (radar_naver 전용 · 파일은 만들지 않고 글자(HTML)만 돌려줌) ─────────────
TABLE_BORDER = "#DDD7CC"
TABLE_HEAD_BG = "#F4F1EC"
TABLE_TEXT = "#22262B"
_IMG_STYLE = "width:100%; max-width:720px; border-radius:12px; display:block; margin:16px auto;"
_RD_PLACEHOLDER_RE = re.compile(r"<p[^>]*>\s*\{\{(RDPHOTO|RDGIF|RDTABLE|RDEMPTY|RDLINK|RDREL|RDSUB):(\d+)\}\}\s*</p>")


def has_cover(pdir):
    """③ 버튼 조건: 그 글 사진 폴더 photos.json 에 cover 가 적혀 있고 파일도 있으면 True (장부는 안 봄)."""
    return _exists(pdir, load_state(pdir).get("cover"))


def _photo_url(pdir, name):
    return "/" + _rel(os.path.join(pdir, name))


def _split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_sep(line):
    return bool(re.fullmatch(r"\|?[\s:\-|]+\|?", line.strip())) and "-" in line


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _cell_html(text):
    """표 칸 글자 → HTML. HTML 기호는 안전하게 바꾸고, **글자** 는 굵은 글자로."""
    return _BOLD_RE.sub(lambda m: '<strong style="font-weight:bold;">' + m.group(1) + "</strong>", _html.escape(text))


def radar_table_html(rows):
    """마크다운 표(행 목록, 첫 줄이 머리) → HTML 표(왼쪽 정렬). 표 안 글자는 원고 그대로, **글자** 만 굵게."""
    cell = f"border:1px solid {TABLE_BORDER}; padding:8px; color:{TABLE_TEXT}; text-align:left; vertical-align:middle;"
    cell_body = cell + " font-weight:normal;"  # 칸은 보통 굵기(머리만 굵게). 네이버에서 굵은 글씨가 번지지 않게 분명히 적어 둔다
    head, body = rows[0], rows[1:]
    out = [f'<table border="1" cellspacing="0" cellpadding="8" align="left" style="border-collapse:collapse; border:1px solid {TABLE_BORDER};">']
    out.append("<tr>" + "".join(
        f'<th align="left" bgcolor="{TABLE_HEAD_BG}" style="{cell} background-color:{TABLE_HEAD_BG}; font-weight:bold;">{_cell_html(c)}</th>' for c in head) + "</tr>")
    for r in body:
        r = (r + [""] * len(head))[: len(head)]
        out.append("<tr>" + "".join(f'<td align="left" style="{cell_body}">{_cell_html(c)}</td>' for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


# ── 강조 표시: 본문 **글자** → 굵게, ==글자== → 형광펜(#FFF1B8). shop_formatter 의 ** 처리(보라+노랑)는 이 갈래에서 쓰지 않는다 ──
HIGHLIGHT_BG = "#FFF1B8"  # config RADAR_EM_HL_BG 가 없을 때 기본값
BOLD_COLOR = "#8A6A1F"  # config RADAR_EM_BOLD_COLOR 가 없을 때 기본값
_EM_B0, _EM_B1, _EM_H0, _EM_H1 = "", "", "", ""  # 눈에 안 보이는 임시 표시(build_shop_html 을 지나가게)
_BODY_BOLD_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_BODY_HL_RE = re.compile(r"==([^=\n]+?)==")


def _cfg(name, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _em_tokens():
    """임시 표시 → 실제 HTML. 색은 호출할 때마다 config 에서 읽는다(RADAR_EM_BOLD_COLOR·RADAR_EM_HL_BG)."""
    return {
        _EM_B0: f'<strong style="font-weight:bold; color:{_cfg("RADAR_EM_BOLD_COLOR", BOLD_COLOR)};">', _EM_B1: "</strong>",
        _EM_H0: f'<span style="background-color:{_cfg("RADAR_EM_HL_BG", HIGHLIGHT_BG)}; font-weight:bold;">', _EM_H1: "</span>",
    }


def _skip_emphasis_line(ln):
    t = ln.strip()
    return (not t) or t.startswith("#") or t.startswith("{{") or bool(shop_formatter.TAG_LINE_RE.match(ln))


def _mark_emphasis(body):
    """짝이 맞는 **x** / ==x== 만 임시 표시로 바꿈(짝이 안 맞는 것은 글자 그대로). 소제목(#)·태그 줄·자리표시 줄은 건드리지 않는다."""
    out = []
    for ln in body.split("\n"):
        if not _skip_emphasis_line(ln):
            ln = _BODY_BOLD_RE.sub(lambda m: _EM_B0 + m.group(1) + _EM_B1, ln)
            ln = _BODY_HL_RE.sub(lambda m: _EM_H0 + m.group(1) + _EM_H1, ln)
        out.append(ln)
    return "\n".join(out)


# ── 자동 강조(⑪): 숫자+단위 굵게(금색), 결론 문장 형광펜. 원고 파일은 안 바꾸고 최종본 만들 때만 적용 ──
_AUTO_PREFIX = r"(?:(?:약|최대|최소|합계)\s*)*"
_AUTO_SKIP_SEG_RE = re.compile("(" + _EM_B0 + ".*?" + _EM_B1 + "|" + _EM_H0 + ".*?" + _EM_H1 + r"|https?://\S+|\[[^\]\n]*\]\([^)\n]*\))", re.S)
_HANGUL_UNIT_NOT_AFTER = {"단": "계", "개": "월"}  # "3단계"·"3개월" 은 단위가 아님


def _auto_unit_re():
    units = [u for u in (_cfg("RADAR_AUTO_EM_UNITS", None) or []) if u]
    if not units:
        return None
    units = sorted(set(units), key=lambda u: (-len(u), u))
    alt = "|".join(re.escape(u) for u in units)
    return re.compile(r"(?<![가-힣])" + _AUTO_PREFIX + r"(?<![A-Za-z0-9.,])\d[\d,]*(?:\.\d+)?(?:" + alt + r")(?![A-Za-z0-9])")


def _auto_ok(m):
    t = m.group(0)
    if t.endswith("년"):  # 연도(2024년)·날짜는 강조하지 않음
        num = re.search(r"\d[\d,]*(?:\.\d+)?", t).group(0)
        if len(num) >= 4 and "," not in num and "." not in num:
            return False
    nxt = m.string[m.end():m.end() + 1]
    for u, bad in _HANGUL_UNIT_NOT_AFTER.items():
        if t.endswith(u) and nxt == bad:
            return False
    return True


def _skip_auto_line(ln):
    t = ln.strip()
    return _skip_emphasis_line(ln) or t.startswith("📷") or "naver.me/" in t or t.startswith("|")


def _auto_em_numbers(body, found=None):
    """일반 문단의 "숫자+단위"를 굵게(임시 표시)로 감싼다. found 가 리스트면 (문장 앞 20자, 강조된 글자) 를 담는다. 반환 (본문, 곳 수)."""
    rx = _auto_unit_re()
    if rx is None:
        return body, 0
    out, count = [], 0
    for ln in body.split("\n"):
        if _skip_auto_line(ln):
            out.append(ln)
            continue
        parts = _AUTO_SKIP_SEG_RE.split(ln)
        for i in range(0, len(parts), 2):  # 짝수 칸 = 강조·링크 바깥 글자
            def sub(m, _ln=ln):
                nonlocal count
                if not _auto_ok(m):
                    return m.group(0)
                count += 1
                if found is not None:
                    cut = [x.end() for x in re.finditer(r"(?<=[.?!])\s+", _ln[:_ln.find(m.group(0))])]
                    found.append(((_ln[(cut[-1] if cut else 0):].strip())[:20], m.group(0)))
                return _EM_B0 + m.group(0) + _EM_B1
            parts[i] = rx.sub(sub, parts[i])
        out.append("".join(parts))
    return "\n".join(out), count


def _count_highlights(body):
    """본문 안 ==형광펜== 곳 수(길이와 상관없이). 이미 임시 표시로 바뀐 것과 아직 ==글자== 인 것 모두 센다. 소제목·표·자리표시 줄은 뺌."""
    n = 0
    for ln in body.split("\n"):
        if _skip_emphasis_line(ln):
            continue
        n += len(_BODY_HL_RE.findall(ln)) + len(re.findall(_EM_H0 + "(.*?)" + _EM_H1, ln))
    return n


def _auto_hl_conclusion(body, found=None):
    """원고의 ==강조== 가 config RADAR_AUTO_HL_MIN(기본 2) 곳 미만일 때만 마지막 ## 소제목 아래 첫 문장 전체를 형광펜 임시 표시로 감싼다. 반환 (본문, 1 또는 0)."""
    try:
        need = int(_cfg("RADAR_AUTO_HL_MIN", 2))
    except (TypeError, ValueError):
        need = 2
    if _count_highlights(body) >= need:
        return body, 0
    lines = body.split("\n")
    last = max((i for i, ln in enumerate(lines) if ln.startswith("## ")), default=None)
    if last is None:
        return body, 0
    for j in range(last + 1, len(lines)):
        ln = lines[j]
        if lines[j].startswith("#"):
            break
        if _skip_auto_line(ln):
            continue
        plain = re.sub("[" + _EM_B0 + _EM_B1 + _EM_H0 + _EM_H1 + "]", "", ln)
        sents = split_sentences(plain)
        if not sents:
            return body, 0
        first = sents[0].strip()
        rest = plain[plain.find(first) + len(first):]
        lines[j] = _EM_H0 + first + _EM_H1 + rest
        if found is not None:
            found.append((first[:20], first))
        return "\n".join(lines), 1
    return body, 0


def auto_emphasis(body):
    """결론 형광펜 → 숫자+단위 굵게 순서로 적용. 반환 (본문, 정보 dict). config 로 각각 끌 수 있다."""
    info = {"numbers": 0, "conclusion": 0, "items": []}
    if _cfg("RADAR_AUTO_HL_CONCLUSION", True):
        body, info["conclusion"] = _auto_hl_conclusion(body, info["items"])
    if _cfg("RADAR_AUTO_EM_NUMBERS", True):
        nums = []
        body, info["numbers"] = _auto_em_numbers(body, nums)
        info["items"] += nums
    return body, info


AUTO_EM_MARK = "<!--RD:AUTO_EM:%d,%d-->"  # 최종본 맨 끝의 눈에 안 보이는 표시(③ 화면 안내용: 숫자 n곳, 결론 형광펜 h곳)


def _apply_emphasis(final):
    for k, v in _em_tokens().items():
        final = final.replace(k, v)
    return final


def emphasis_warnings(body):
    """짝이 안 맞는 ** / == 를 찾아 ["강조 표시 짝이 안 맞음: (문장 앞 20자)", …]. 글은 바꾸지 않는다. body = 출처 칸을 뺀 원고."""
    warns = []
    prepared = _prepare_raw(body)[0]
    for ln in prepared.split("\n"):
        if _skip_emphasis_line(ln):
            continue
        covered = [False] * len(ln)
        for rx in (_BODY_BOLD_RE, _BODY_HL_RE):
            for m in rx.finditer(ln):
                for i in range(m.start(), m.end()):
                    covered[i] = True
        pos = next((m.start() for m in re.finditer(r"\*\*|==", ln) if not covered[m.start()]), None)
        if pos is None:
            continue
        cut = [m.end() for m in re.finditer(r"(?<=[.?!])\s+", ln[:pos])]
        sent = ln[(cut[-1] if cut else 0):].strip()
        warns.append("강조 표시 짝이 안 맞음: " + sent[:20])
    return warns


# ── 줄 띄움(모바일에서 읽기 쉽게): 일반 문단을 문장마다 줄바꿈 ──
_HANGUL = re.compile(r"[가-힣]")
_NOTICE_START = "이 포스팅은 네이버 쇼핑커넥트"
_GAP = shop_formatter.NAVER_GAP  # <p><br></p> = 빈 줄 하나
_BODY_P_OPEN = '<p style="text-align:justify !important;">'
_BODY_P_NEW = '<p style="text-align:justify !important; font-weight:normal; color:#222222;">'  # 보통 굵기·진한 회색을 분명히 적음
_BODY_P_RE = re.compile(re.escape(_BODY_P_OPEN) + r"(.*?)</p>", re.S)


def split_sentences(s):
    """HTML 조각 s 를 문장 목록으로. 끝 판단: '?'·'!' 또는 한글 바로 뒤의 '.' 다음에 공백. 괄호 안·강조 표시 안은 자르지 않고,
    숫자 안의 점(4.85)·영문 약어(U.S.)는 한글 뒤 점이 아니라서 자르지 않는다."""
    parts, start, depth, emph = [], 0, 0, 0
    last = prev = ""  # 보이는 글자 중 마지막, 그 앞
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "<":
            j = s.find(">", i)
            i = n if j < 0 else j + 1
            continue
        if ch in "([（":
            depth += 1
        elif ch in ")]）":
            depth = max(0, depth - 1)
        elif ch in (_EM_B0, _EM_H0):
            emph += 1
        elif ch in (_EM_B1, _EM_H1):
            emph = max(0, emph - 1)
        if ch in (_EM_B0, _EM_B1, _EM_H0, _EM_H1):  # 강조 표시 글자는 "마지막 글자"로 치지 않음(강조로 끝나는 문장 뒤에서도 문장을 자름)
            i += 1
            continue
        ends = bool(last) and (last in "?!" or (last == "." and bool(_HANGUL.match(prev))))
        if ch.isspace() and depth == 0 and emph == 0 and ends:
            j = i
            while j < n and s[j].isspace():
                j += 1
            if j < n:
                parts.append(s[start:i])
                start = j
                i = j
                last = prev = ""
                continue
        if not ch.isspace():
            prev, last = last, ch
        i += 1
    parts.append(s[start:])
    return [p for p in parts if p.strip()]


def _break_paragraph(inner, mode):
    if "<br" in inner or inner.startswith(_NOTICE_START):
        return inner  # 목록(이미 줄바꿈 있음)·쇼핑커넥트 안내 문장은 손대지 않음
    sents = split_sentences(inner)
    if len(sents) < 2:
        return inner
    if mode == "two_sentences":
        sents = [" ".join(sents[k:k + 2]) for k in range(0, len(sents), 2)]
    return "<br>".join(sents)


def _fix_blank_lines(final):
    """빈 줄은 한 개만. 📷 줄과 naver.me 줄은 앞뒤에 빈 줄 하나(맨 앞·맨 뒤는 제외)."""
    gap = re.escape(_GAP)
    final = re.sub("(?:" + gap + "){2,}", _GAP, final)
    mark = r'(<p style="[^"]*">(?:📷 [^<]*|https://naver\.me/[^<\s]*)</p>)'
    final = re.sub("(?<=>)(?<!" + gap + ")" + mark, lambda m: _GAP + m.group(1), final)
    final = re.sub(mark + "(?!" + gap + "|$)", lambda m: m.group(1) + _GAP, final)
    return final


_TABLE_OPEN_LEFT = f'<table border="1" cellspacing="0" cellpadding="8" align="left" style="border-collapse:collapse; border:1px solid {TABLE_BORDER};">'
_TABLE_OPEN_CENTER = f'<table border="1" cellspacing="0" cellpadding="8" align="center" style="border-collapse:collapse; border:1px solid {TABLE_BORDER}; margin-left:auto; margin-right:auto;">'


def _center_all(final):
    """가운데 정렬: 문단(<p>) 줄의 왼쪽 정렬을 가운데로, 표 전체를 가운데로. 표 칸(th·td) 안 글자는 그대로 왼쪽."""
    final = final.replace('<p style="text-align:left;', '<p style="text-align:center;')
    return final.replace(_TABLE_OPEN_LEFT, _TABLE_OPEN_CENTER)


def finish_final(final, mode="sentence", align=None):
    """build_shop_html 결과를 radar_naver 용으로 다듬는다: 맨 위 제목(<h1>) 빼기 → 문장마다 줄바꿈(mode) → 본문 문단 보통 굵기 → 정렬(align) → 빈 줄 정리 → 강조 임시 표시를 굵게·형광펜으로.
    align: "center"(가운데, 기본) / "justify"(양쪽 맞춤). None 이면 config RADAR_ALIGN."""
    align = align or _cfg("RADAR_ALIGN", "center")
    final = re.sub(r"^<h1[^>]*>.*?</h1>", "", final, count=1, flags=re.S)
    if mode in ("sentence", "two_sentences"):
        final = _BODY_P_RE.sub(lambda m: _BODY_P_OPEN + _break_paragraph(m.group(1), mode) + "</p>", final)
    final = final.replace(_BODY_P_OPEN, _BODY_P_NEW)
    if align != "justify":
        final = final.replace("text-align:justify !important;", "text-align:center;")
        final = _center_all(final)
    final = _fix_blank_lines(final)
    return _apply_emphasis(final)


_SUB_RE = re.compile(r"^###\s+(\S.*?)\s*$")
_NOTICE_IMG_MARK = "필수표기"


def strip_notice(raw):
    """원고에서 "이 포스팅은 네이버 쇼핑커넥트…"로 시작하는 줄을 뺀다(글자 대신 이미지로 넣기 때문). 빼고 남는 빈 줄은 build_shop_html 이 정리한다."""
    return "\n".join(ln for ln in (raw or "").replace("\r\n", "\n").split("\n") if not ln.strip().startswith(_NOTICE_START))


def _prepare_raw(raw, subs=None, sub_cards=None):
    """본문 → (자리표시를 넣은 본문, 표 목록, 사진 번호 목록, GIF 상품명 목록). 메모([표지]·[사진자리]) 블록은 뺀다.
    "### 글자"(제품 소제목)는 {{RDSUB:n}} 자리표시로 바꾸고 글자는 subs 목록에 담는다(subs 가 None 이면 버림).
    sub_cards 목록을 주면 subs 와 같은 순서로 그 ### 구간(다음 ### 나 ## 전까지)의 첫 [상품카드: 이름] 이름(없으면 None)을 담는다."""
    subs = subs if subs is not None else []
    sub_cards = sub_cards if sub_cards is not None else []
    cur = None
    lines = (raw or "").replace("\r\n", "\n").split("\n")
    out, tables, photos, cards = [], [], [], []
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i].strip()
        if radar_images._MARK_COVER.match(ln) or radar_images._MARK_SLOT.match(ln):
            while i < n and lines[i].strip():
                i += 1
            continue
        m = _SUB_RE.match(ln)
        if m:
            subs.append(m.group(1))
            sub_cards.append(None)
            cur = len(subs) - 1
            out += ["", "{{RDSUB:%d}}" % (len(subs) - 1), ""]
            i += 1
            continue
        m = radar_images._MARK_PHOTO.match(ln)
        if m:
            photos.append(int(m.group(1)))
            out += ["", "{{RDPHOTO:%d}}" % int(m.group(1)), ""]
            i += 1
            continue
        if ln.startswith("## "):
            cur = None  # ## 소제목이 나오면 제품 구간 끝
        m = radar_images._MARK_CARD.match(ln)
        if m:
            if cur is not None and sub_cards[cur] is None:
                sub_cards[cur] = m.group(1)
            cards.append(m.group(1))
            out += ["", "{{RDGIF:%d}}" % (len(cards) - 1), "", "{{RDLINK:%d}}" % (len(cards) - 1), ""]  # GIF 자리는 상품카드(제휴 링크 한 줄) 바로 위
            i += 1
            continue
        if ln.startswith("|"):
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            block = lines[i:j]
            if len(block) >= 2 and _is_sep(block[1]):
                rows = [_split_row(block[0])] + [_split_row(x) for x in block[2:]]
                tables.append(rows)
                out += ["", "{{RDTABLE:%d}}" % (len(tables) - 1), ""]
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), tables, photos, cards


BADGE_FILE_PREFIX = "badge_"
BADGE_UPLOAD_NAME = "별점띠"  # naver_upload 파일 이름 가운데 글자(본문 사진 번호에는 안 셈)
NO_BADGE_MARK = "<!--RD:NO_BADGE:%s-->"  # 별점 띠 값이 없어 못 넣었을 때 최종본 맨 끝의 눈에 안 보이는 표시(③ 화면 노란 경고용)
_RS_RATING = re.compile(r"^-\s*평점\s+(\d+(?:\.\d+)?)", re.M)
_RS_REVIEWS = re.compile(r"^-\s*리뷰 수\s+(\d[\d,]*)", re.M)
_RS_TIME = re.compile(r"^-\s*수집 시각\s*:\s*(\d{4}-\d{2}-\d{2})", re.M)


def research_stats(pid, run):
    """research/<상품id>.md 에서 평점(글자 그대로)·리뷰 수·수집 날짜를 읽는다(읽기만). 반환 (값 dict 또는 None, 빠진 항목 목록/이유)."""
    path = os.path.join(RUNS_ROOT, run, "research", f"{pid}.md")
    try:
        with open(path, encoding="utf-8") as f:
            t = f.read()
    except OSError:
        return None, ["research 파일 없음"]
    m_r, m_n, m_d = _RS_RATING.search(t), _RS_REVIEWS.search(t), _RS_TIME.search(t)
    missing = [k for k, m in (("평점", m_r), ("리뷰 수", m_n), ("수집 시각", m_d)) if not m]
    if missing:
        return None, missing
    rating = m_r.group(1)
    if not 0 <= float(rating) <= 5:
        return None, ["평점 범위 밖(" + rating + ")"]
    return {"rating": rating, "reviews": int(m_n.group(1).replace(",", "")), "date": m_d.group(1)}, []


def badge_info(card_name, st):
    """상품카드 이름 → 별점 띠 값. 반환 {"ok","pid","stats","missing"(이유 목록)}. 읽기만."""
    by_name, by_id = load_products()
    cp = resolve_cards([{"name": card_name, "line": ""}], st, by_name, by_id)[0]
    if not cp["prod"]:
        return {"ok": False, "pid": "", "stats": None, "missing": ["상품 못 찾음(" + cp["note"] + ")"]}
    stats, missing = research_stats(cp["prod"]["id"], cp["prod"]["run"])
    return {"ok": bool(stats), "pid": cp["prod"]["id"], "stats": stats, "missing": missing}


def make_badge_file(pdir, card_name, st=None):
    """상품카드 이름의 별점 띠 그림을 글 사진 폴더에 badge_<상품id>.png 로 만든다(Pillow, 0원). 반환 (파일 경로 또는 '', badge_info 결과)."""
    info = badge_info(card_name, st if st is not None else load_state(pdir))
    if not info["ok"]:
        return "", info
    s = info["stats"]
    out = os.path.join(pdir, f"{BADGE_FILE_PREFIX}{info['pid']}.png")
    radar_images.rating_badge_image(s["rating"], s["reviews"], s["date"], out)
    return out, info


def auto_badge_images(raw, pdir):
    """② 화면을 열 때: 상품카드가 있는 ### 구간마다 별점 띠 그림을 (다시) 만든다. 글 사진 폴더에만 씀. 반환: 만든 개수."""
    subs, sub_cards = [], []
    _prepare_raw(strip_notice(raw), subs, sub_cards)
    st = load_state(pdir)
    n = 0
    for name in sub_cards:
        if name:
            try:
                if make_badge_file(pdir, name, st)[0]:
                    n += 1
            except Exception:
                pass
    return n


def badge_views(raw, pdir):
    """② 화면용: ### 제품 구간마다 별점 띠 상태(읽기만, 파일은 안 만듦). [{sub, card, ok, url, text1, text2, missing}]"""
    subs, sub_cards = [], []
    _prepare_raw(strip_notice(raw), subs, sub_cards)
    st = load_state(pdir)
    out = []
    for sub, name in zip(subs, sub_cards):
        if not name:
            continue
        info = badge_info(name, st)
        v = {"sub": sub, "card": name, "ok": info["ok"], "missing": info["missing"], "url": "", "text1": "", "text2": ""}
        if info["ok"]:
            s = info["stats"]
            v["text1"], v["text2"] = radar_images.badge_texts(s["rating"], s["reviews"], s["date"])
            fn = f"{BADGE_FILE_PREFIX}{info['pid']}.png"
            if _exists(pdir, fn):
                v["url"] = _rel(os.path.join(pdir, fn))
        out.append(v)
    return out


RADAR_BLOG_ID = "guardradar"
_POST_URL_RE = re.compile(r"^https://blog\.naver\.com/guardradar/(\d+)$")
RELATED_TITLE = "함께 보면 좋은 글"
NO_BANNER_MARK = "<!--RD:NO_BANNER:%s-->"  # 배너 이미지를 못 찾았을 때 최종본 맨 끝의 눈에 안 보이는 표시(③ 화면 노란 경고용, 없는 색 이름)
NO_RELATED_MARK = "<!--RD:NO_RELATED-->"  # 붙일 글이 없을 때 최종본에 넣는 눈에 안 보이는 표시(화면 위쪽 안내용)


def normalize_post_url(url):
    """N데스크피커 발행 주소 검사. 반환 (맞춘 주소 or '', 막는 이유 or '').
    받는 형식: blog.naver.com/guardradar/<숫자> (m. 도 가능, 끝 / 나 ?뒤 글자는 버림), PostView...?blogId=guardradar&logNo=<숫자>."""
    u = (url or "").strip()
    if not u:
        return "", "글 주소가 비어 있어요. 발행한 글의 주소(https://blog.naver.com/guardradar/글번호)를 넣어 주세요."
    try:
        pu = urlparse(u)
        host = (pu.hostname or "").lower()
    except ValueError:
        return "", "주소 모양이 이상해요."
    if pu.scheme not in ("http", "https") or host not in ("blog.naver.com", "m.blog.naver.com"):
        return "", "N데스크피커 블로그(blog.naver.com/guardradar) 주소가 아니에요."
    path = pu.path.rstrip("/")
    m = re.fullmatch(r"/" + RADAR_BLOG_ID + r"/(\d+)", path, re.I)
    if m:
        return f"https://blog.naver.com/{RADAR_BLOG_ID}/{m.group(1)}", ""
    if re.fullmatch(r"/PostView\.(naver|nhn)", path, re.I):
        q = {k.lower(): v for k, v in parse_qs(pu.query).items()}
        bid = (q.get("blogid") or [""])[0].lower()
        no = (q.get("logno") or [""])[0]
        if bid != RADAR_BLOG_ID:
            return "", "다른 블로그(blogId=" + (bid or "없음") + ") 주소예요. guardradar 글만 저장할 수 있어요."
        if not no.isdigit():
            return "", "글 번호(logNo)가 없거나 숫자가 아니에요."
        return f"https://blog.naver.com/{RADAR_BLOG_ID}/{no}", ""
    if path == "" or path.lower() == "/" + RADAR_BLOG_ID:
        return "", "블로그 첫 화면 주소예요. 글 번호까지 있는 글 주소를 넣어 주세요."
    if path.lower().startswith("/" + RADAR_BLOG_ID + "/"):
        return "", "글 번호(숫자)가 없는 주소예요."
    return "", "다른 블로그 주소예요. guardradar 글만 저장할 수 있어요."


def load_naver_links():
    """상품 id → 제휴 링크(naver.me). links.json 들에서 새 실행 우선. 링크 주소는 그대로(바꾸지 않음)."""
    out = {}
    for run in list_runs():
        links = _read_json(os.path.join(RUNS_ROOT, run, "links.json")) or {}
        for it in links.get("items") or []:
            pid, nl = str(it.get("product_id") or ""), (it.get("naver_link") or "").strip()
            if pid and nl.startswith("https://naver.me/") and pid not in out:
                out[pid] = nl
    return out


def pick_related(cur_id, cur_genre, queue, articles, since, genre_of=None, limit=2, today=None):
    """"함께 보면 좋은 글" 고르기. 반환 (뽑은 목록 [{id,url,date,genre,same}], 메모 목록).
    고를 수 있는 글: since <= 발행일 <= 오늘 + 주소가 guardradar/<숫자> + 자기 자신 아님. 같은 갈래 최근 1 + 다른 갈래 최근 1,
    한쪽이 없으면 남은 글 중 최근 순으로 채움. 갈래를 못 찾은 글은 "다른 갈래"로 취급하고 메모에 적는다."""
    genre_of = genre_of or plan_genre
    today = today or datetime.now().strftime("%Y-%m-%d")  # 이 PC 의 오늘 날짜. 발행일이 오늘보다 뒤(예약)인 글은 아직 공개 전이라 뺀다
    cands, notes = [], []
    for it in queue or []:
        aid = it.get("id")
        if not aid or aid == cur_id:
            continue
        art = (articles or {}).get(aid) or {}
        d = ((art.get("platform_published_at") or {}).get("naver") or "").strip()
        url = ((art.get("platform_published_url") or {}).get("naver") or "").strip()
        if not d or d < since or d > today or not _POST_URL_RE.match(url):
            continue
        g = genre_of(it) or ""
        if not g:
            notes.append(f"갈래를 못 찾아 '다른 갈래'로 취급: {it.get('keyword')} ({aid})")
        cands.append({"id": aid, "url": url, "date": d, "genre": g, "same": bool(g) and g == (cur_genre or ""), "keyword": it.get("keyword")})
    cands.sort(key=lambda c: (c["date"], c["id"]), reverse=True)
    same = next((c for c in cands if c["same"]), None)
    other = next((c for c in cands if not c["same"]), None)
    picks = [c for c in (same, other) if c]
    for c in cands:
        if len(picks) >= limit:
            break
        if c not in picks:
            picks.append(c)
    return picks[:limit], notes


# ── ③ 네이버 업로드 폴더 (naver_upload) ─────────────────────────
UPLOAD_DIR = "naver_upload"
_FMT_EXT = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}
_UPLOAD_MARK_RE = re.compile(r"📷 ([^<\s]+)</p>")
_UPLOAD_MARK_STYLE = "text-align:center; color:#1565C0; font-weight:bold; font-size:18px;"


def real_ext(path):
    """그림 파일의 실제 형식에 맞는 확장자(.jpg 이름인데 PNG 면 .png). 못 읽으면 원래 확장자."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return _FMT_EXT.get(im.format, os.path.splitext(path)[1].lower())
    except Exception:
        return os.path.splitext(path)[1].lower()


def _safe_part(text):
    """파일 이름에 넣을 글자: 윈도우에서 못 쓰는 글자·공백은 밑줄로."""
    t = re.sub(r'[\\/:*?"<>|\s]+', "_", (text or "").strip()).strip("._")
    return t


def gif_file_label(prod, card_name):
    """GIF 파일 이름에 넣을 짧은 이름: 그 상품 images.json 의 짧은 이름, 없으면 상품명 앞 10자."""
    short = ""
    if prod:
        _, meta = _img_meta(prod["id"], prod["run"])
        short = (meta.get("짧은 이름") or "").strip()
    return _safe_part(short or (card_name or "").strip()[:10])


def _reset_upload_dir(pdir):
    """pdir/naver_upload 를 비우고 돌려준다. 이 폴더 안의 것만 지우고, 폴더가 바로가기(연결)이거나 사진 폴더 밖을 가리키면 아무것도 안 지우고 멈춘다."""
    up = os.path.join(pdir, UPLOAD_DIR)
    want = os.path.join(os.path.realpath(pdir), UPLOAD_DIR)
    if os.path.lexists(up):
        if os.path.islink(up) or os.path.realpath(up) != want or not os.path.isdir(up):
            raise OSError("naver_upload 가 일반 폴더가 아니라서 건드리지 않았어요")
        for name in os.listdir(up):
            fp = os.path.join(up, name)
            if os.path.isdir(fp) and not os.path.islink(fp):
                shutil.rmtree(fp)
            else:
                os.remove(fp)
    else:
        os.makedirs(up)
    return up


def notice_image_path():
    """쇼핑커넥트 필수 표기 이미지 경로(config RADAR_NOTICE_IMAGE). 없으면 같은 이름 뒤에 .jpg 가 한 번 더 붙은 파일(윈도우 숨김 확장자 때문에 흔함)도 찾는다. 못 찾으면 ''."""
    base = _cfg("RADAR_NOTICE_IMAGE", "static/radar/notice_shoppingconnect.jpg")
    for cand in (base, base + ".jpg"):
        if os.path.isfile(cand):
            return cand
    return ""


_BANNER_LABEL = {"white": "흰글씨", "black": "검정글씨"}
_BANNER_MARK = "배너"


def banner_path(color):
    """가격 확인 배너 이미지 경로(static/radar/banner_white.png · banner_black.png). 이름 뒤에 .png 가 한 번 더 붙은 파일도 찾는다. 못 찾으면 ''."""
    base = os.path.join("static", "radar", f"banner_{color}.png")
    for cand in (base, base + ".png"):
        if os.path.isfile(cand):
            return cand
    return ""


def banner_order():
    """배너 색 순서(첫 번째 색, 두 번째 색). config RADAR_BANNER_START 가 black 이면 검정부터."""
    first = "black" if str(_cfg("RADAR_BANNER_START", "white")).strip().lower() == "black" else "white"
    return (first, "white" if first == "black" else "black")


def banner_small_copy(src, dst):
    """배너를 가로 RADAR_BANNER_WIDTH px 로 비율 그대로 줄여 PNG 로 저장(LANCZOS). 원본은 안 바꿈. 이미 그보다 작으면 그대로 복사."""
    from PIL import Image
    try:
        w = int(_cfg("RADAR_BANNER_WIDTH", 430))
    except (TypeError, ValueError):
        w = 430
    with Image.open(src) as im:
        if w <= 0 or im.size[0] <= w:
            shutil.copyfile(src, dst)
            return 1.0
        nh = max(1, round(im.size[1] * w / im.size[0]))
        out = im.convert("RGBA").resize((w, nh), Image.LANCZOS)
    out.save(dst, format="PNG", optimize=True)
    return w / im.size[0]


def radar_sub_html(text):
    """"### 글자" 제품 소제목: 가운데·굵게·17px·#22262B + 아래 금색 짧은 막대(글자 "━" 라서 네이버에 붙여도 남음). 정렬은 finish_final 이 맞춘다."""
    return ('<p style="text-align:left;"><strong style="color:#22262B; font-size:17px; font-weight:bold;">' + _html.escape(text) + "</strong></p>"
            '<p style="text-align:left;"><span style="color:#C9A227; font-size:17px;">━━━━</span></p>')


def _upload_size():
    try:
        return max(1, int(_cfg("RADAR_UPLOAD_SIZE", 800)))
    except (TypeError, ValueError):
        return 800


def _contain(im, size):
    """그림 한 장을 size×size 흰 바탕 가운데에 비율 그대로(자르지 않고) 맞춘다. 반환 RGB 그림."""
    from PIL import Image
    im = im.convert("RGBA")
    w, h = im.size
    sc = min(size / w, size / h)
    nw, nh = max(1, round(w * sc)), max(1, round(h * sc))
    r = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    canvas.paste(r, ((size - nw) // 2, (size - nh) // 2), r.getchannel("A"))
    return canvas


def fit_square_copy(src, dst, size):
    """src 그림을 size×size 로 맞춰 dst 에 저장(원본은 안 바꿈). 이미 size×size 면 그대로 복사.
    JPEG 는 품질 90, PNG 는 PNG, GIF 는 프레임 수·장당 시간·반복 횟수를 그대로 두고 프레임마다 같은 방식으로 맞춘다."""
    from PIL import Image, ImageOps, ImageSequence
    with Image.open(src) as im:
        fmt = im.format
        if im.size == (size, size) and im.getexif().get(274, 1) in (0, 1):  # 방향 표시(EXIF)도 정상일 때만 그대로 복사
            shutil.copyfile(src, dst)
            return
        if fmt == "GIF":
            frames, durs = [], []
            default_dur = im.info.get("duration", 100)
            for fr in ImageSequence.Iterator(im):
                durs.append(fr.info.get("duration", default_dur))
                frames.append(_contain(fr, size))
            kw = {"save_all": True, "append_images": frames[1:], "duration": durs, "optimize": False, "disposal": 1}
            if "loop" in im.info:
                kw["loop"] = im.info["loop"]
            frames[0].save(dst, format="GIF", **kw)
            return
        out = _contain(ImageOps.exif_transpose(im), size)
        if fmt == "PNG":
            out.save(dst, format="PNG", optimize=True)
        elif fmt == "WEBP":
            out.save(dst, format="WEBP", quality=90)
        else:
            out.save(dst, format="JPEG", quality=90, optimize=True)


def _frame_cfg():
    """config 의 본문 사진 프레임 설정. 꺼져 있으면 None, 켜져 있으면 (바깥색, 안쪽여백, 모서리, 칸 바탕색)."""
    try:
        import config
        if not getattr(config, "RADAR_PHOTO_FRAME", False):
            return None
        return (getattr(config, "RADAR_FRAME_BG", "#F4F1EC"), int(getattr(config, "RADAR_FRAME_PAD", 27)),
                int(getattr(config, "RADAR_FRAME_RADIUS", 16)), getattr(config, "RADAR_FRAME_FILL", "#FFFFFF"))
    except Exception:
        return None


def _frame_image(im, size, cfg):
    """그림 한 장을 size×size 프레임(바깥 바탕색·둥근 안쪽 칸·칸 안 contain)에 넣어 RGB 로 돌려준다."""
    from PIL import Image, ImageDraw
    bg, pad, radius, fill = cfg
    inner = size - 2 * pad
    ss = 4  # 둥근 모서리를 부드럽게 하려고 4배 크기로 마스크를 그린 뒤 줄임
    mask = Image.new("L", (inner * ss, inner * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, inner * ss - 1, inner * ss - 1), radius=radius * ss, fill=255)
    mask = mask.resize((inner, inner), Image.LANCZOS)
    tile = _contain(im, inner)  # 흰 바탕 contain → 칸 바탕색으로 다시 칠함
    if fill != "#FFFFFF":
        im2 = im.convert("RGBA")
        w, h = im2.size
        sc = min(inner / w, inner / h)
        nw, nh = max(1, round(w * sc)), max(1, round(h * sc))
        r = im2.resize((nw, nh), Image.LANCZOS)
        tile = Image.new("RGB", (inner, inner), fill)
        tile.paste(r, ((inner - nw) // 2, (inner - nh) // 2), r.getchannel("A"))
    canvas = Image.new("RGB", (size, size), bg)
    canvas.paste(tile, (pad, pad), mask)
    radar_images.draw_frame_border(canvas)  # 안쪽 카드 테두리: GIF 프레임과 같은 값(radar_images.FRAME_*)
    return canvas


def frame_square_copy(src, dst, size, cfg):
    """본문 사진을 프레임(⑩) 씌워 dst 에 저장(원본은 안 바꿈). JPEG 품질 90, PNG 는 PNG."""
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        fmt = im.format
        out = _frame_image(ImageOps.exif_transpose(im), size, cfg)
    if fmt == "PNG":
        out.save(dst, format="PNG", optimize=True)
    elif fmt == "WEBP":
        out.save(dst, format="WEBP", quality=90)
    else:
        out.save(dst, format="JPEG", quality=90, optimize=True)


def notice_text_height(im):
    """필수 표기 이미지 속 글자 줄 높이(윗끝~아랫끝 px). 바탕색과 많이 다른 픽셀이 있는 가로줄 묶음 중 가장 높은 것. 못 재면 0."""
    g = im.convert("L")
    w, h = g.size
    px = g.load()
    bg = max(set(px[x, y] for x in range(0, w, 3) for y in range(0, h, 3)), key=lambda v: sum(1 for x in range(0, w, 3) for y in range(0, h, 3) if px[x, y] == v))
    best = run = 0
    for y in range(h):
        if any(abs(px[x, y] - bg) > 70 for x in range(w)):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def notice_scale(im):
    """줄이는 비율(1 이하). config RADAR_NOTICE_WIDTH(숫자) 우선, 없으면 RADAR_NOTICE_TEXT_PX. 둘 다 없거나 못 재면 1(그대로)."""
    w = _cfg("RADAR_NOTICE_WIDTH", None)
    if isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0:
        return min(1.0, w / im.size[0])
    t = _cfg("RADAR_NOTICE_TEXT_PX", None)
    if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
        th = notice_text_height(im)
        if th > 0:
            return min(1.0, t / th)
    return 1.0


def notice_small_copy(src, dst):
    """필수 표기 이미지를 글자 높이가 RADAR_NOTICE_TEXT_PX 가 되게(가로·세로 같은 비율) 줄여 dst 에 저장(LANCZOS, JPEG 품질 95). 원본은 안 바꿈. 줄일 필요가 없으면 그대로 복사."""
    from PIL import Image
    with Image.open(src) as im:
        sc = notice_scale(im)
        if sc >= 1.0:
            shutil.copyfile(src, dst)
            return 1.0
        nw, nh = max(1, round(im.size[0] * sc)), max(1, round(im.size[1] * sc))
        out = im.convert("RGB").resize((nw, nh), Image.LANCZOS)
    out.save(dst, format="JPEG", quality=95)
    return sc


def _write_upload_folder(pdir, uploads):
    """uploads: [(복사할 원본 경로, 새 이름[, 크기 맞춤 여부])] → naver_upload 를 새로 만들어 복사.
    크기 맞춤(기본 켬)이면 config RADAR_UPLOAD_SIZE(800) 정사각형으로 가공해서 저장, 끄면(필수 표기 이미지) 그대로 복사. 원본은 안 바꿈."""
    up = _reset_upload_dir(pdir)
    size = _upload_size()
    for item in uploads:
        src, name = item[0], item[1]
        resize = item[2] if len(item) > 2 else True
        dst = os.path.join(up, name)
        if resize == "frame" and _frame_cfg():
            frame_square_copy(src, dst, size, _frame_cfg())
        elif resize == "notice":
            notice_small_copy(src, dst)
        elif resize == "banner":
            banner_small_copy(src, dst)
        elif resize:
            fit_square_copy(src, dst, size)
        else:
            shutil.copyfile(src, dst)
    return up


def _fmt_size(n):
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))}KB"


GIF_WARN_BYTES = 3 * 1024 * 1024


def _dims(fp):
    try:
        from PIL import Image
        with Image.open(fp) as im:
            return im.size
    except Exception:
        return (0, 0)


def upload_listing(pdir, final_html):
    """③ 화면용: naver_upload 안 파일 목록(번호·이름·용량)과, 최종본의 📷 자리표시와 맞는지."""
    d = os.path.join(pdir, UPLOAD_DIR)
    files, big_gifs = [], []
    want = _upload_size()
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            fp = os.path.join(d, name)
            if os.path.isfile(fp):
                m = re.match(r"(\d+)_", name)
                w, h = _dims(fp)
                is_banner = _BANNER_MARK in name  # 가격 확인 배너도 800×800 검사 제외
                is_notice = _NOTICE_IMG_MARK in name  # 필수 표기 이미지는 크기 그대로라서 검사 제외
                nbytes = os.path.getsize(fp)
                if name.lower().endswith(".gif") and nbytes > GIF_WARN_BYTES:
                    big_gifs.append(f"{name} ({_fmt_size(nbytes)})")
                files.append({"no": m.group(1) if m else "", "name": name, "size": _fmt_size(nbytes), "url": _photo_url(pdir, UPLOAD_DIR + "/" + name),
                              "dim": f"{w}×{h}", "dim_ok": is_notice or is_banner or (w, h) == (want, want) or (w == want and h > 0),  # 표 그림은 가로 800 이면 세로 자유 "is_notice": is_notice, "is_banner": is_banner,
                              "notice_note": ("(줄임)" if (w, h) != _dims(notice_image_path() or fp) else "(그대로)") if is_notice else ""})
    expected = _UPLOAD_MARK_RE.findall(final_html or "")
    have = {f["name"] for f in files}
    missing = [n for n in expected if n not in have]
    extra = [f["name"] for f in files if f["name"] not in set(expected)]
    return {"dir": os.path.abspath(d), "exists": os.path.isdir(d), "files": files, "expected": expected,
            "missing": missing, "extra": extra, "mismatch": bool(missing or (expected and extra)),
            "size_want": want, "big_gifs": big_gifs, "bad_dims": [f["name"] for f in files if not f["dim_ok"]]}


def build_final_html(raw, title, pdir, related=None, product_links=None, line_break=None):
    """radar_naver 최종본. 반환 (HTML, 정보 dict). shop_formatter.build_shop_html 은 고치지 않고 그대로 불러
    글 모양을 만든 뒤, 자리표시를 사진·GIF·표·제휴 링크로 바꿔 끼운다. 이미지 생성 API·장부 쓰기 없음.
    related: pick_related 가 뽑은 목록(None 이면 안 붙임, 빈 목록이면 "다른 글 없음" 표시), product_links: 상품id→제휴 링크(None 이면 links.json 을 읽음)."""
    st = load_state(pdir)
    subs, sub_cards = [], []  # "### 제품 소제목" 글자들 / 각 구간의 상품카드 이름(없으면 None)
    body, tables, photos, cards = _prepare_raw(strip_notice(raw), subs, sub_cards)  # 쇼핑커넥트 안내 문장은 글자 대신 이미지로 넣으므로 원고에서 뺌
    table_pair = [n for _rows, n in table_slot_map(strip_notice(raw))]  # 표마다 바로 아래 [사진: n] 번호(없으면 None)
    body = _mark_emphasis(body)  # 본문 **굵게**·==형광펜== 임시 표시(소제목·표는 그대로)
    body, _auto = auto_emphasis(body)  # ⑪ 숫자+단위 자동 굵게·결론 문장 자동 형광펜(함께 보면 좋은 글을 붙이기 전에)
    if related:
        ms = list(shop_formatter.TAG_LINE_RE.finditer(body))
        pos = ms[-1].start() if ms else len(body)
        rel_block = "## " + RELATED_TITLE + "\n\n" + "\n\n".join("{{RDREL:%d}}" % i for i in range(len(related)))
        body = body[:pos].rstrip("\n") + "\n\n" + rel_block + "\n\n" + body[pos:]
    # 상품카드 → 제휴 링크 (정확히 같을 때만 자동, 아니면 photos.json product_pick)
    by_name, by_id = load_products()
    cps = resolve_cards([{"name": n, "line": ""} for n in cards], st, by_name, by_id)
    plinks = product_links if product_links is not None else load_naver_links()
    card_links = [plinks.get(cp["prod"]["id"], "") if cp["prod"] else "" for cp in cps]
    # 네이버 편집기는 localhost 사진 주소를 못 가져가므로, 사진 자리에는 "📷 02_사진1.jpg" 한 줄만 넣고
    # 같은 이름의 파일을 글 사진 폴더 안 naver_upload/ 에 본문에 나오는 순서대로 복사해 둔다(사람이 끌어다 놓음).
    uploads = []  # [(원본 경로, 새 파일 이름, 크기 맞춤 여부)]

    def mark(name):
        return f'<p style="{_UPLOAD_MARK_STYLE}">📷 {_html.escape(name)}</p>'

    def add_upload(fname, middle, resize=True, src=None):
        src = src or os.path.join(pdir, fname)
        name = f"{len(uploads) + 1:02d}_{middle}{real_ext(src)}"
        uploads.append((src, name, resize))
        return name

    banner_on = bool(_cfg("RADAR_BANNER", True))
    banner_srcs = {c: banner_path(c) for c in ("white", "black")}
    banner_missing = [c for c, v in banner_srcs.items() if not v] if banner_on else []
    banner_state = {"n": 0}

    cover_tag = ""
    if _exists(pdir, st["cover"]):
        cover_tag = mark(add_upload(st["cover"], "표지"))
    # 쇼핑커넥트 필수 표기: 표지 바로 아래 📷 줄(원고에 안내 문장이 있든 없든 항상). 이미지는 크기 그대로 복사.
    notice_src = notice_image_path()
    if notice_src:
        cover_tag += mark(add_upload("", _NOTICE_IMG_MARK, resize="notice", src=notice_src))
    else:
        cover_tag += '<p style="text-align:center; color:#D32F2F; font-weight:bold; font-size:16px;">필수 표기 이미지 없음 (static/radar/notice_shoppingconnect.jpg)</p>'
    final = shop_formatter.build_shop_html(
        title=title, cover_img_tag=cover_tag, raw=body, product_images=[], platform="naver",
        bottom_images=None, related_posts=None, disclosure=False, body_images=None,
        shop_links=None, strip_link_placeholders=True,
    )
    info = {"slots": len(photos), "empty": [], "gifs": 0, "tables": len(tables), "cards": len(cards), "card_links": 0,
            "card_missing": [], "badges": 0, "badge_missing": [], "related": [r["url"] for r in (related or [])], "subs": len(subs), "notice_image": bool(notice_src), "table_as_image": 0}

    def repl(m):
        kind, k = m.group(1), int(m.group(2))
        if kind == "RDSUB":
            html_sub = radar_sub_html(subs[k])
            card = sub_cards[k] if k < len(sub_cards) else None
            if not card:
                return html_sub
            try:  # 별점 띠: ### 바로 아래(첫 [사진: n] 앞). 값이 하나라도 없으면 넣지 않고 경고만
                bpath, binfo = make_badge_file(pdir, card, st)
            except Exception as e:
                bpath, binfo = "", {"missing": ["그림 만들기 실패: " + str(e)]}
            if not bpath:
                info["badge_missing"].append(f"{subs[k]}: " + ", ".join(binfo["missing"]))
                return html_sub
            info["badges"] += 1
            return html_sub + mark(add_upload("", BADGE_UPLOAD_NAME + str(info["badges"]), resize=False, src=bpath))
        if kind == "RDTABLE":
            n = table_pair[k] if k < len(table_pair) else None
            if n is not None and _exists(pdir, st["slots"].get(str(n), "")):
                info["table_as_image"] += 1
                return ""  # 표 그림이 바로 아래 [사진: n] 자리에 들어가므로 HTML 표는 넣지 않음(두 번 나오지 않게)
            return radar_table_html(tables[k])
        if kind == "RDGIF":
            gf = st["gifs"].get(cards[k], "")
            if _exists(pdir, gf):
                info["gifs"] += 1
                label = gif_file_label(cps[k]["prod"], cards[k])
                return mark(add_upload(gf, "GIF" + ("_" + label if label else "")))
            return ""
        if kind == "RDLINK":
            if card_links[k]:
                info["card_links"] += 1
                bn = ""
                if banner_on and not banner_missing:  # 제휴 링크 바로 위 가격 확인 배너(링크가 있는 카드만, 흰/검정 번갈아)
                    color = banner_order()[banner_state["n"] % 2]
                    banner_state["n"] += 1
                    bn = mark(add_upload("", _BANNER_MARK + "_" + _BANNER_LABEL[color], resize="banner", src=banner_srcs[color]))
                return bn + f'<p style="text-align:left;">{_html.escape(card_links[k], quote=True)}</p>'
            info["card_missing"].append(cards[k])
            return f'<p style="text-align:left; color:#D32F2F; font-weight:bold;">상품카드 링크 없음: {_html.escape(cards[k])}</p>'
        if kind == "RDREL":
            return f'<p style="text-align:left;">{_html.escape(related[k]["url"], quote=True)}</p>'
        chosen = st["slots"].get(str(k), "")
        if _exists(pdir, chosen):
            if os.path.basename(chosen).startswith(TABLE_FILE_PREFIX) and st["slot_sources"].get(str(k)) == "표 그림(앱이 만듦)":
                return mark(add_upload(chosen, f"사진{k}", resize=False))  # 표 그림은 이미 가로 800·테두리 포함: 정사각 틀·크기 맞춤 없이 그대로
            return mark(add_upload(chosen, f"사진{k}", resize="frame"))
        info["empty"].append(k)
        return f'<p style="text-align:center; color:#D32F2F; font-weight:bold; font-size:16px;">사진 {k} 비어 있음</p>'

    final = _RD_PLACEHOLDER_RE.sub(repl, final)
    if line_break is None:
        try:
            import config
            line_break = getattr(config, "RADAR_LINE_BREAK", "sentence")
        except Exception:
            line_break = "sentence"
    final = finish_final(final, line_break)  # 제목 빼기·문장마다 줄바꿈·보통 굵기·가운데 정렬·빈 줄 정리·강조 적용
    info["uploads"] = [u[1] for u in uploads]
    info["upload_error"] = ""
    try:
        _write_upload_folder(pdir, uploads)
    except Exception as e:  # 폴더를 못 만들어도 최종본 글자는 돌려주고, 이유를 알린다
        info["upload_error"] = str(e)
    if related is not None and not related:
        final += NO_RELATED_MARK
    if info["badge_missing"]:  # ③ 화면 노란 경고용 표시(최종본 맨 끝, 눈에 안 보임). "-" "<" ">" 는 표시가 깨지지 않게 바꿈
        final += NO_BADGE_MARK % "|".join(re.sub(r"[-<>]", " ", x) for x in info["badge_missing"])
    info["banners"] = banner_state["n"]
    info["banner_missing"] = banner_missing
    if banner_missing:
        final += NO_BANNER_MARK % ",".join(banner_missing)
    info["photos_in"] = len(photos) - len(info["empty"])
    info["auto_em"] = _auto
    final += AUTO_EM_MARK % (_auto["numbers"], _auto["conclusion"])
    return final, info


_SCREEN_MARK_RE = re.compile(r"<p[^>]*>📷 ([^<\s]+)</p>")


def banner_missing_from(final_html):
    """저장된 최종본 맨 끝 표시에서 못 찾은 배너 색 이름 목록(③ 화면 노란 경고용). 없으면 []."""
    m = re.search(r"<!--RD:NO_BANNER:([^>]*?)-->", final_html or "")
    return [c for c in m.group(1).split(",") if c] if m else []


def badge_missing_from(final_html):
    """저장된 최종본 맨 끝 표시에서 별점 띠를 못 넣은 제품 구간 목록(③ 화면 노란 경고용). 없으면 []."""
    m = re.search(r"<!--RD:NO_BADGE:([^>]*?)-->", final_html or "")
    return [c.strip() for c in m.group(1).split("|") if c.strip()] if m else []


def screen_html(final_html, pdir):
    """③ 화면용 최종본: 📷 줄 자리마다 naver_upload 안 같은 파일을 그림으로 보여 준다(복사용 최종본은 그대로 둠).
    사진·GIF 는 본문 폭 100%(최대 800px), 필수 표기·배너는 원래 픽셀 크기. 그림 아래 작은 회색 글자로 파일 이름.
    파일이 없거나 못 읽으면 빨간 "사진 없음: 파일 이름"."""
    want = _upload_size()

    def sub(m):
        name = _html.unescape(m.group(1))
        fp = os.path.join(pdir, UPLOAD_DIR, name)
        w, h = _dims(fp) if os.path.isfile(fp) else (0, 0)
        if not w:
            return f'<p style="text-align:center; color:#D32F2F; font-weight:bold; font-size:16px;">사진 없음: {_html.escape(name)}</p>'
        if _NOTICE_IMG_MARK in name or _BANNER_MARK in name or BADGE_UPLOAD_NAME in name:
            style = f"width:{w}px; max-width:100%; height:auto; display:block; margin:0 auto;"
        else:
            style = f"width:100%; max-width:{want}px; height:auto; display:block; margin:0 auto;"
        url = _photo_url(pdir, UPLOAD_DIR + "/" + name) + f"?v={int(os.path.getmtime(fp))}"
        warn = ""
        if _NOTICE_IMG_MARK not in name and _BANNER_MARK not in name and BADGE_UPLOAD_NAME not in name and (w, h) != (want, want):
            warn = f' <span style="color:#D32F2F; font-weight:bold;">({w}×{h} — {want}×{want} 아님)</span>'
        return (f'<div style="text-align:center; margin:12px 0;"><img src="{_html.escape(url, quote=True)}" alt="{_html.escape(name, quote=True)}" style="{style}">'
                f'<div style="color:#999999; font-size:12px; margin-top:4px;">{_html.escape(name)}{warn}</div></div>')

    return _SCREEN_MARK_RE.sub(sub, final_html or "")


def count_missing_card_links(final_html):
    """저장된 최종본 안의 "상품카드 링크 없음" 표시 개수(최종본 화면 위쪽 경고용)."""
    return len(re.findall(r"상품카드 링크 없음:", final_html or ""))


def has_no_related_mark(final_html):
    return NO_RELATED_MARK in (final_html or "")


def count_empty_slots(final_html):
    """저장된 최종본 안의 "사진 n 비어 있음" 표시 개수(최종본 화면 위쪽 경고용)."""
    return len(re.findall(r"사진 \d+ 비어 있음", final_html or ""))


# ── ③ 화면 위쪽: 제목 복사 · 카테고리 · 태그 (메모에서 읽기, 읽기만) ─────────────────────────
_META_CAT_RE = re.compile(r"^[ \t]*[-*•]?[ \t]*\**카테고리\**[ \t]*[:：][ \t]*(.+)$", re.M)
_META_TAG_RE = re.compile(r"^[ \t]*[-*•]?[ \t]*\**태그\**[ \t]*[:：][ \t]*(.+)$", re.M)  # "태그 제안:" 같은 줄은 안 읽음
TAG_MAX = 30


def parse_tag_text(text):
    """쉼표·공백·# 로 갈라 # 를 떼고 겹치는 것을 빼고 순서를 지킨 태그 목록."""
    out = []
    for t in re.split(r"[,，\s#]+", text or ""):
        t = t.strip().strip("*")
        if t and t not in out:
            out.append(t)
    return out


def parse_meta_lines(memo):
    """메모에서 "카테고리:" 줄·"태그:" 줄을 읽는다 → (카테고리 글자 또는 '', 태그 목록)."""
    m = _META_CAT_RE.search(memo or "")
    cat = m.group(1).strip().strip("*").strip() if m else ""
    t = _META_TAG_RE.search(memo or "")
    return cat, (parse_tag_text(t.group(1)) if t else [])


def subheading_warnings(raw, memo):
    """제품 소제목(### …)이 빠진 상품 구간을 찾아 ["제품 소제목 없음: (상품명 앞 15자)", …]. 글은 바꾸지 않는다(자동으로 넣지 않음).
    상품 구간 = 메모 [사진자리]에서 종류가 "제품"인 [사진: n] 칸, 또는 바로 앞 제품 사진 없이 나오는 [상품카드: 이름].
    구간 앞(직전 ## 소제목 또는 직전 상품 구간 이후)에 ### 가 하나도 없으면 경고. raw 는 출처 칸을 뺀 원고."""
    hints = slot_hints(memo)
    warns, has_sub, in_prod = [], False, False
    for ln in (raw or "").replace("\r\n", "\n").split("\n"):
        t = ln.strip()
        if t.startswith("## "):
            has_sub, in_prod = False, False
        elif _SUB_RE.match(t):
            has_sub, in_prod = True, False
        else:
            m = radar_images._MARK_PHOTO.match(t)
            if m and (hints.get(int(m.group(1))) or {}).get("kind") == "제품":
                if not has_sub:
                    note = hints[int(m.group(1))].get("note", "")
                    pm = re.search(r"\(([^)]+)\)", note)
                    warns.append("제품 소제목 없음: " + ((pm.group(1) if pm else note) or "사진 %s" % m.group(1))[:15])
                has_sub, in_prod = False, True
                continue
            m = radar_images._MARK_CARD.match(t)
            if m:
                if in_prod:
                    in_prod = False  # 앞 제품 사진과 같은 상품 구간
                    continue
                if not has_sub:
                    warns.append("제품 소제목 없음: " + m.group(1)[:15])
                has_sub = False
    return warns


def avoid_warnings(raw, memo, title=""):
    """피할 표현(tools/radar_banned.json) 경고 줄 목록. 글은 바꾸지 않는다. raw = 출처 칸을 뺀 원고.
    ["피할 표현: 갈려요 — 두 제품 사이에서는 두께와 케이블…", …, "바꿀 말 힌트: …"] (제목 금지 단어·횟수 초과·'지점'도 같이)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("radar_check", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "radar_check.py"))
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    res = rc.check_avoid([("본문", raw or ""), ("메모", memo or "")], [title] if title else [])
    warns, hints = [], []
    for h in res["fails"] + res["warns"] + res["title_fails"]:
        warns.append("피할 표현: %s — %s…  (%s %d줄)" % (h["word"], h["snippet"], h["label"], h["line"]) if h["label"] != "제목"
                     else "제목 금지 단어: %s — %s" % (h["word"], h["snippet"]))
        if h["hint"] and h["hint"] not in hints:
            hints.append(h["hint"])
    for lw in res["limit_warns"]:
        where = ", ".join("%s %d줄" % (h["label"], h["line"]) for h in lw["hits"])
        warns.append("너무 자주 씀: '%s' %d번 (한 글 %d번까지) — %s" % (lw["rule"], lw["count"], lw["max"], where))
    return warns + ["바꿀 말 힌트: " + " / ".join(hints)] if hints else warns


def radar_meta_view(memo, raw, genre, title, category_map=None):
    """③ 화면 위쪽 값. category: 메모 → (없으면) 갈래 표 → (없으면) 빈 글자. tags: 메모의 "태그:" 줄 → 없으면 원고의 "태그:" 줄."""
    if category_map is None:
        try:
            import config
            category_map = getattr(config, "RADAR_CATEGORY_MAP", {}) or {}
        except Exception:
            category_map = {}
    cat, tags = parse_meta_lines(memo)
    cat_src = "메모" if cat else ""
    if not cat and genre and category_map.get(genre):
        cat, cat_src = category_map[genre], "갈래 표(" + genre + ")"
    tag_src = "메모의 태그 줄" if tags else ""
    if not tags:
        tags = parse_tag_text(" ".join(shop_formatter.split_tag_line(raw or "")[1]))
        tag_src = "원고의 태그 줄" if tags else ""
    return {"title": title or "", "category": cat, "category_source": cat_src, "tags": tags, "tag_source": tag_src,
            "tags_over": len(tags) > TAG_MAX, "tag_max": TAG_MAX}
