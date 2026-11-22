# -*- coding: utf-8 -*-
"""Desk Picker(radar_naver) 사용 제품 장부.

한 제품은 Desk Picker 전체에서 글 하나에만 쓴다(원고지침 v3.4 5장 '제품은 한 번만').
이 파일은 앱에 들어간 원고(articles_radar_naver.json, id 2026-11-23 이상)를 읽어
'어떤 제품을 어느 글에 썼는지'를 pipeline/radar_used_products.json 에 적는다. (앱 json·대기열은 읽기만 한다.)

다시 만들기:  python tools/radar_used_products.py            (원고가 늘면 다시 돌린다)
11/23 주 묶음 예 보기:  python tools/radar_used_products.py --demo

다른 도구가 쓰는 함수: load_ledger() · used_ids() · used_model_keys() · model_key(상품명)
역할: 틀A제품 / 틀B본상품 / 틀B대안
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "pipeline" / "radar_used_products.json"
FIRST_ID = "2026-11-23"
ALIAS_JSON = ROOT / "tools" / "brand_alias.json"

# 용량·크기·출력처럼 모델 코드가 아닌 토큰
_SPEC = re.compile(r"^(\d+(\.\d+)?(mah|wh|w|hz|mm|cm|gb|tb|인치|in|k|v|a|ml|l|kg|g|개|단|색|m|ch)+|\d+x\d+(x\d+)?|\d+)$", re.I)
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9]")
# 모델 코드처럼 보이지만 기능·규격을 가리키는 말(Qi2, 3in1, USB, PD3, IP68 …)
_FEATURE = re.compile(r"^(qi\d?|\d+in\d+|usb\d*|type-?c|pd\d*|ip[x\d]{2,3}|ddr\d|wifi\d?|bt\d(\.\d)?|\d+ch|[a-z]\d{1,2}k|\d+(st|nd|rd|th))$", re.I)
_SELLER = ("삼성공식파트너", "삼성공식", "공식파트너", "공식스토어", "공식판매처", "공식", "정식발매", "국내정품", "정품", "n배송", "내일출발")
_STOP_KO = {"블루투스", "무선", "휴대용", "게이밍", "모니터", "키보드", "마우스", "보조배터리", "스피커", "이어폰", "히터", "전기", "미니",
            "캠핑", "캠핑용", "사무실", "가정용", "충전기", "거치대", "받침대", "팜레스트", "발받침대", "스탠드", "무드등", "크리스마스"}


def _n(s: str) -> str:
    return re.sub(r"[\s\-_·]", "", s or "").lower()


def _brands() -> list[tuple[str, str]]:
    """(정규화한 이름, 대표 이름) 목록. brand_alias.json 의 묶음을 그대로 쓴다."""
    try:
        d = json.loads(ALIAS_JSON.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for g in d.get("groups", []):
        names = [x for x in g if isinstance(x, str) and x.strip()]
        for x in names:
            if len(_n(x)) >= 2:
                out.append((_n(x), names[0]))
    return sorted(out, key=lambda t: -len(t[0]))


def model_key(name: str, hint: str = "") -> str:
    """같은 모델이면 판매처·상품id 가 달라도 같은 값이 나오게 만든 '브랜드|모델' 글자.
    1) 브랜드: brand_alias.json 의 대표 이름(없으면 첫 단어). 2) 모델: 상품명 안의 '글자+숫자' 코드
    (용량·크기·출력·기능 규격 토큰은 뺌, BPD004bt → bpd004, 첫 코드만 씀). 코드가 없으면 브랜드 다음 한글 낱말 2개.
    hint: 원고의 ### 소제목(예: '벨킨 BPD004')이 있으면 그 안의 코드·브랜드를 먼저 쓴다."""
    s = name or ""
    s = re.sub(r"\[[^\]]*\]", " ", s)  # [N배송] 같은 판매처 표시
    plain = s
    for w in _SELLER:
        plain = plain.replace(w, " ")
    nn = _n(plain)
    brand = ""
    pos = 10 ** 6
    for al, canon in _brands():
        i = nn.find(al)
        if 0 <= i < pos or (i == pos and len(al) > 0):
            brand, pos = canon, i
    # 판매처 브랜드(삼성)는 다른 브랜드가 같이 있으면 빼야 하므로, 삼성이 잡혔어도 JBL 등이 있으면 그쪽
    others = [(nn.find(al), canon) for al, canon in _brands() if nn.find(al) >= 0 and canon not in ("삼성", "엘지")]
    if others:
        brand = min(others)[1]
    toks = [t for t in re.split(r"[\s,()/]+", plain) if t]
    htoks = [t for t in re.split(r"[\s,()/]+", hint or "") if t]
    if not brand:
        brand = (htoks or toks or [""])[0]
    codes = []
    for t in (htoks + toks if hint else toks):
        for m in _CODE.findall(t):
            if re.search(r"[A-Za-z]", m) and re.search(r"\d", m) and not _SPEC.match(m) and not _FEATURE.match(m) and not re.fullmatch(r"\d+[A-Za-z]{1,3}", m):
                m = re.sub(r"(?<=\d)(bt)$", "", m, flags=re.I)
                if m.lower() not in [c.lower() for c in codes]:
                    codes.append(m)
    if codes:
        return _n(brand) + "|" + _n(codes[0])
    after, seen = [], False
    for t in (htoks or toks):
        if not seen and _n(t) in (_n(brand), ):
            seen = True
            continue
        if seen and re.search(r"[가-힣]", t) and t not in _STOP_KO and not _SPEC.match(t):
            after.append(t)
        if len(after) == 2:
            break
    if not after and htoks:  # 소제목만 있는 제품(예: '삼성 20000mAh 45W')은 소제목 나머지 전체
        after = [t for t in htoks if _n(t) != _n(brand)]
    return _n(brand) + "|" + "".join(_n(a) for a in after)


def pid_of(url: str) -> str:
    return (url or "").rstrip("/").rsplit("/", 1)[-1]


# ───────────────────────── 장부 읽기(다른 도구용) ─────────────────────────

def load_ledger(path: Path = LEDGER) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"products": []}


def used_ids(led: dict | None = None) -> dict[str, list[dict]]:
    """{상품id: [장부 줄...]}"""
    led = led if led is not None else load_ledger()
    out: dict[str, list[dict]] = {}
    for p in led.get("products", []):
        for pid in [p.get("상품id")] + list(p.get("같은모델_다른상품id") or []):
            if pid:
                out.setdefault(str(pid), []).append(p)
    return out


def used_model_keys(led: dict | None = None) -> dict[str, list[dict]]:
    led = led if led is not None else load_ledger()
    out: dict[str, list[dict]] = {}
    for p in led.get("products", []):
        if p.get("모델키"):
            out.setdefault(p["모델키"], []).append(p)
    return out


def find_used(name: str = "", url: str = "", led: dict | None = None) -> list[dict]:
    """상품 하나(이름·주소)가 장부에 있으면 그 줄들을 돌려준다(상품id 일치 또는 모델키 일치)."""
    led = led if led is not None else load_ledger()
    hit = used_ids(led).get(pid_of(url), []) if url else []
    if not hit and name:
        hit = used_model_keys(led).get(model_key(name), [])
    return hit


# ───────────────────────── 원고 읽기 ─────────────────────────

def _catalog() -> dict[str, dict]:
    """상품명 → {id,url,brand,store}. 모든 radar_naver 실행 폴더의 approved.json + candidates.json."""
    cat: dict[str, dict] = {}
    for fp in sorted(glob.glob(str(ROOT / "pipeline" / "candidates_run" / "*radar_naver*" / "approved.json"))):
        for k in json.loads(Path(fp).read_text(encoding="utf-8")).get("keywords", []):
            for pk in k.get("picks", []):
                if pk.get("name") and pk.get("url"):
                    cat[pk["name"]] = {"id": pid_of(pk["url"]), "url": pk["url"], "store": pk.get("store", ""), "keyword": k.get("keyword", "")}
    return cat


def _all_candidate_items() -> list[dict]:
    items = []
    for fp in sorted(glob.glob(str(ROOT / "pipeline" / "candidates_run" / "*radar_naver*" / "candidates.json"))):
        try:
            d = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for k in d.get("keywords", []):
            for x in k.get("items", []):
                if x.get("name") and x.get("url"):
                    items.append({"name": x["name"], "id": pid_of(x["url"]), "store": x.get("store", ""), "keyword": k.get("keyword", "")})
    return items


def parse_article(raw: str, frame: str) -> dict:
    """원고 본문에서 제품을 찾는다. 반환 {title, products:[{card, heading, role, order}], text}"""
    body = (raw or "").replace("\r\n", "\n").split("\n[메모]")[0]
    lines = body.split("\n")
    title = ""
    m = re.match(r"제목\s*:\s*(.+)", lines[1].strip()) if len(lines) > 1 else None
    if m:
        title = m.group(1).strip()
    prods, heading, in_h3 = [], "", False
    for l in lines:
        if l.startswith("### "):
            heading, in_h3 = l[4:].strip(), True
        elif l.startswith("## "):
            in_h3 = False
        mm = re.match(r"^\[상품카드: ([^\]\n]+)\]$", l.strip())
        if mm:
            prods.append({"card": mm.group(1), "heading": heading if in_h3 else "", "h3": in_h3})
    out = []
    seen = set()
    first_b = True
    for p in prods:
        if p["card"] in seen:  # 틀 B 끝 카드(같은 상품 두 번째)는 장부에 한 줄만
            continue
        seen.add(p["card"])
        if frame == "A":
            role = "틀A제품"
        else:
            role = "틀B본상품" if first_b else "틀B대안"
            first_b = False
        out.append({"card": p["card"], "heading": p["heading"], "role": role})
    return {"title": title, "products": out, "text": body}


def _mention_names(arts: dict[str, dict]) -> dict[str, str]:
    """이름만 나오는 대안을 찾기 위한 '이름 → 모델키' 표. 앱 원고의 ### 소제목을 쓴다."""
    names = {}
    for a in arts.values():
        for p in a["products"]:
            h = p["heading"]
            if h:
                names[h] = model_key(p["card"], h)
    return names


def build(write: bool = True) -> dict:
    q = {x["id"]: x for x in json.loads((ROOT / "content_queue_radar_naver.json").read_text(encoding="utf-8"))}
    app = json.loads((ROOT / "articles_radar_naver.json").read_text(encoding="utf-8"))
    cat = _catalog()
    cand_items = _all_candidate_items()
    arts: dict[str, dict] = {}
    for aid in sorted(app):
        if aid < FIRST_ID or aid not in q:
            continue
        frame = q[aid].get("planned_type") or "B"
        a = parse_article(app[aid].get("raw_paste", ""), frame)
        a["frame"] = frame
        a["date"] = (q[aid].get("plan") or {}).get("date") or (q[aid].get("plan") or {}).get("publish_date") or q[aid].get("week", "")
        arts[aid] = a
    mention = _mention_names(arts)
    rows, warn = [], []
    for aid, a in arts.items():
        card_keys = set()
        for p in a["products"]:
            info = cat.get(p["card"])
            if not info:
                warn.append(f"{aid}: 상품카드 '{p['card'][:30]}' 가 approved.json 에 없음(상품id 비움)")
            key = model_key(p["card"], p["heading"])
            card_keys.add(key)
            rows.append({"상품id": info["id"] if info else "", "모델명": _display(p, key), "모델키": key, "상품명": p["card"],
                         "글id": aid, "글제목": a["title"], "날짜": a["date"], "틀": a["frame"], "역할": p["role"]})
        # 이름만 나온 대안: 같은 글에서 카드로 안 쓴 다른 글의 제품 이름이 본문에 나오면 대안으로 올린다
        if a["frame"] == "B":
            for nm, key in mention.items():
                if key in card_keys or not nm:
                    continue
                if _mentioned(nm, a["text"]):
                    other = next((r for r in rows if r["모델키"] == key), None)
                    rows.append({"상품id": other["상품id"] if other else "", "모델명": nm, "모델키": key,
                                 "상품명": other["상품명"] if other else nm, "글id": aid, "글제목": a["title"], "날짜": a["date"],
                                 "틀": "B", "역할": "틀B대안", "비고": "이름만 나옴(상품카드 없음)"})
    # 이름만 나온 대안(코드 검사): 다른 글 제품의 모델 코드(예: g304)가 본문에 붙여 쓴 글자로 나오면 대안으로 올린다
    codes = {}
    for r in list(rows):
        c = r["모델키"].split("|", 1)[1]
        if c and re.search(r"\d", c) and len(c) >= 4:
            codes.setdefault(r["모델키"], (c, r))
    for aid, a in arts.items():
        have = {r["모델키"] for r in rows if r["글id"] == aid}
        flat = re.sub(r"[\s\-]", "", a["text"]).lower()
        for key, (c, src) in codes.items():
            if key not in have and c in flat:
                rows.append({"상품id": src["상품id"], "모델명": src["모델명"], "모델키": key, "상품명": src["상품명"], "글id": aid,
                             "글제목": a["title"], "날짜": a["date"], "틀": a["frame"], "역할": "틀B대안", "비고": "이름만 나옴(모델 코드가 본문에 있음, 상품카드 없음)"})
                have.add(key)
    # 같은 모델의 다른 상품id(판매처만 다른 경우)
    by_key: dict[str, set[str]] = {}
    for x in cand_items:
        by_key.setdefault(model_key(x["name"]), set()).add(x["id"])
    for r in rows:
        others = sorted(by_key.get(r["모델키"], set()) - {r["상품id"]})
        if others:
            r["같은모델_다른상품id"] = others
    data = {"설명": "Desk Picker 사용 제품 장부. 한 제품은 한 글에만 쓴다(원고지침 v3.4). 다시 만들기: python tools/radar_used_products.py",
            "만든기준": f"articles_radar_naver.json 의 id {FIRST_ID} 이상 원고 {len(arts)}편",
            "상품수": len({(r['상품id'] or r['모델키']) for r in rows}), "모델수": len({r["모델키"] for r in rows}),
            "줄수": len(rows), "경고": warn, "products": rows}
    if write:
        LEDGER.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def _display(p: dict, key: str) -> str:
    """장부에 적는 정규화 모델명(브랜드+모델). 원고 ### 소제목이 있으면 그것, 없으면 상품명에서."""
    return p["heading"] or p["card"][:30]


def _mentioned(name: str, text: str) -> bool:
    return bool(re.search(r"(?<![0-9A-Za-z가-힣])" + re.escape(name) + r"(?![0-9A-Za-z])", text))


def main() -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Desk Picker 사용 제품 장부 만들기")
    ap.add_argument("--demo", action="store_true", help="같은 모델이 판매처만 달라도 묶이는지 11/23 주 예를 보인다")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    d = build(write=not a.no_write)
    print(f"장부 줄 {d['줄수']} · 서로 다른 상품 {d['상품수']} · 서로 다른 모델 {d['모델수']} · 원고 기준: {d['만든기준']}")
    for w in d["경고"]:
        print("경고:", w)
    if a.demo:
        print("\n[같은 모델, 판매처·상품id 만 다른 경우]")
        n = 0
        for r in d["products"]:
            if r.get("같은모델_다른상품id"):
                n += 1
                print(f"- {r['모델명']} (장부 상품id {r['상품id']}) ← 같은 모델 다른 상품id {r['같은모델_다른상품id']}")
        if not n:
            print("- 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
