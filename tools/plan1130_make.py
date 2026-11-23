"""11/30 주 기획(plan.md·plan.json) 만들기. 읽기 전용: 앱 데이터·대기열·장부는 읽기만."""
from __future__ import annotations
import json, re, sys
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import plan1130_check as ck  # noqa: E402
import radar_used_products as ru  # noqa: E402
RUN = ck.RUN
sys.stdout.reconfigure(encoding="utf-8")
V = json.loads((RUN / "volume_cache.json").read_text(encoding="utf-8"))
P = json.loads((RUN / "check_parsed.json").read_text(encoding="utf-8"))


def vol(q):
    return V[q]["total"]


def nsp(s):
    return re.sub(r"\s", "", s)


# 틀 A 칸별 키워드 후보: (후보, 3제품 모두 다룸 O/X/애매, 메모)
ACAND = {
    "A1": [("데스크매트", "O", ""), ("책상매트", "O", ""), ("데스크패드", "O", ""), ("책상패드", "O", ""), ("가죽데스크매트", "X", "PET 책상커버·양면 매트 제품은 가죽이 아님")],
    "A2": [("전선정리", "O", ""), ("선정리", "O", ""), ("케이블정리", "O", ""), ("케이블홀더", "X", "튜브·정리함 제품은 홀더가 아님"), ("케이블정리용품", "O", "검색량 거의 없음")],
    "A3": [("탁상시계", "O", ""), ("알람시계", "O", "탁상시계보다 검색량 작음"), ("디지털시계", "애매", "카파 T913 은 상품명에 디지털 표시 없음"), ("무소음탁상시계", "O", ""), ("책상시계", "O", "")],
    "A4": [("노트북거치대", "O", ""), ("노트북받침대", "O", ""), ("노트북스탠드", "O", ""), ("맥북거치대", "애매", "맥북 전용 글이 되어 다른 제품이 범위 밖"), ("접이식노트북거치대", "X", "니케 수직 스탠드는 접이식이 아님")],
    "A5": [("usb허브", "O", ""), ("c타입허브", "애매", "넥시는 'USB 3.0 4포트'이고 C타입 표시가 약함"), ("멀티허브", "O", ""), ("usb멀티허브", "O", ""), ("usb c타입허브", "애매", "위와 같음")],
    "A6": [("등받이쿠션", "O", "3개 모두 이름에 등받이 포함"), ("의자허리쿠션", "O", ""), ("허리쿠션", "O", ""), ("등쿠션", "O", "칸 이름. 상품 이름에 '등쿠션'은 없고 카테고리가 등쿠션"), ("사무실허리쿠션", "O", "")],
    "A7": [("미니가습기", "O", ""), ("휴대용가습기", "X", "케어팟은 탁상용"), ("무선가습기", "X", "케어팟 상품명에 무선 없음"), ("탁상가습기", "애매", "프롬비는 휴대용"), ("usb가습기", "X", "세 상품 모두 이름에 USB 없음")],
}

# (날짜, 순번, 칸, 갈래, 키워드, 검색량 조회어, 제목, 끝맺음, 궁금증 확인, 짧은이름, 비고)
D = [
    ("2026-11-30", 1, "A1", "책상 정리", "데스크매트", "데스크매트", "데스크매트 추천, 책상 크기·소재별로 고른 3가지", "추천", "-", "데스크매트", ""),
    ("2026-11-30", 2, "B6", "이어폰·스피커", "필립스 TAH2000", "필립스 TAH2000", "필립스 TAH2000 헤드폰, 오픈형 40mm 소리 어떨까?", "답이 보이는 질문", "오픈형·40mm 드라이버(판매자 사양표) 확인", "필립스TAH2000", ""),
    ("2026-11-30", 3, "B11", "조명", "삼성 LED 스탠드", "삼성 LED 스탠드", "삼성 LED 스탠드 책상스탠드, 공부 책상에 둘 분께", "맞는 분", "값 아닌 용도형", "삼성LED스탠드", "모델 코드 없음(이름: 삼성 눈 건강 데스크 LED 램프)"),
    ("2026-12-01", 1, "A7", "계절가전", "미니 가습기", "미니가습기", "미니 가습기, 사기 전 물통 세척·놓을 자리 확인할 점", "사기 전 확인", "-", "미니가습기", ""),
    ("2026-12-01", 2, "B1", "책상 정리", "데스커 모니터받침대", "데스커 모니터받침대", "데스커 모니터 받침대 수납형, 사이즈 한눈에 정리", "정리", "사이즈 옵션 1000mm 확인(높이는 못 봄)", "데스커모니터받침대", "모델 코드 없음. 제목은 키워드 띄어쓰기만 맞춤"),
    ("2026-12-01", 3, "B14", "모니터·노트북", "아이뮤즈 K11", "아이뮤즈 K11", "아이뮤즈 K11 태블릿, 배터리 하루 버틸까?", "답이 보이는 질문", "배터리 8,100mAh·동영상 최대 17시간(상세 글자) 확인", "아이뮤즈K11", ""),
    ("2026-12-02", 1, "A4", "키보드·마우스", "노트북 거치대", "노트북거치대", "노트북 거치대 3가지, 높이 조절·접이식·세로형", "숫자", "-", "노트북거치대", ""),
    ("2026-12-02", 2, "B9", "계절가전", "아이리버 전기방석", "아이리버 전기방석", "아이리버 전기방석, 사무실 의자에 깔아도 괜찮을까?", "살 만한지", "궁금증 값 확인 필요", "아이리버전기방석", "모델 코드 없음. 앱 대기열 N살림노트 '전기방석2인용'과 같은 종류"),
    ("2026-12-02", 3, "B5", "이어폰·스피커", "제닉스 SB9", "제닉스 SB9", "제닉스 SB9 사운드바, 비슷한 PC 스피커와 다른 점", "대안 비교", "궁금증 값 확인 필요", "제닉스SB9", "장부의 '제닉스 타이탄'은 손목받침대(다른 제품)"),
    ("2026-12-03", 1, "A2", "책상 정리", "전선정리", "전선정리", "전선정리 고르는 법, 튜브·홀더·정리함 쓰임 차이", "고르는 법", "-", "전선정리", ""),
    ("2026-12-03", 2, "B3", "충전·멀티탭", "벨킨 WIZ016", "벨킨 WIZ016", "벨킨 WIZ016 맥세이프 충전기, 아이폰·워치 같이 쓸 분께", "맞는 분", "값 아닌 용도형", "벨킨WIZ016", "검색량 10 미만. 대체 검색어 '벨킨 3in1 충전기' 430"),
    ("2026-12-03", 3, "B7", "의자·쿠션", "린백 LB221HA", "린백 LB221HA", "린백 LB221HA 사무용 의자, 팔걸이·틸팅 각도 정리", "정리", "팔걸이 90도 회전·틸팅 125도(상세 글자) 확인", "린백LB221HA", ""),
    ("2026-12-04", 1, "A5", "충전·멀티탭", "USB 허브", "usb허브", "USB 허브 7in1 vs 6in1 vs 4포트, 차이와 선택 기준", "비교·차이", "-", "USB허브", ""),
    ("2026-12-04", 2, "B2", "책상 정리", "노르잇 PR01A", "노르잇 PR01A", "노르잇 PR01A 독서대, 4kg 책도 버틸까?", "답이 보이는 질문", "궁금증 값 확인 필요 (4kg 하중은 구매자 글에만 있고 상품 정보 표에는 못 봄)", "노르잇PR01A", ""),
    ("2026-12-04", 3, "B10", "계절가전", "애니클리어 HOT3", "애니클리어 HOT3", "애니클리어 HOT3 충전식 손난로, 온도 단계·용량 정리", "정리", "3단계 온도(45·50·55도)·5000mAh(상세 글자) 확인", "애니클리어HOT3", "검색량 10 미만. 앱 대기열 N살림노트 '전기손난로'와 같은 종류"),
    ("2026-12-05", 1, "A6", "의자·쿠션", "등받이 쿠션", "등받이쿠션", "등받이 쿠션, 허리 높이에 안 맞으면 어떻게 고를까?", "답이 보이는 질문", "-", "등받이쿠션", ""),
    ("2026-12-05", 2, "B12", "조명", "티피링크 L900-5", "티피링크 L900-5", "티피링크 L900-5 LED 스트립, 구성 대비 가격은 괜찮을까?", "살 만한지", "5m 길이(상품명) 확인, 그 밖은 확인 필요", "티피링크L900", "Tapo 표기를 빼고 키워드를 15자 안에 맞춤. 검색량 10 미만"),
    ("2026-12-05", 3, "B4", "이어폰·스피커", "니케 헤드폰 거치대", "니케 헤드폰 거치대", "니케 헤드폰 거치대 원목, 알루미늄 제품과 다른 점", "대안 비교", "궁금증 값 확인 필요", "니케헤드폰거치대", "모델 코드 없음. 실제 브랜드는 니케(풀 표의 소니는 호환 표기)"),
    ("2026-12-06", 1, "A3", "책상 정리", "탁상시계", "탁상시계", "탁상시계 추천, 무소음·LED 알람 기준으로 고른 3가지", "추천", "-", "탁상시계", ""),
    ("2026-12-06", 2, "B13", "모니터·노트북", "피코펄스 R7H", "피코펄스 R7H", "피코펄스 R7H 미니PC, 팬 소음은 얼마나 날까?", "답이 보이는 질문", "궁금증 값 확인 필요 (35dB는 구매자 글에만)", "피코펄스R7H", ""),
    ("2026-12-06", 3, "B8", "계절가전", "에어메이드 AMP-300", "에어메이드 AMP-300", "에어메이드 AMP-300 공기청정기, 원룸 책상 옆에 둘 분께", "맞는 분", "값 아닌 용도형", "에어메이드AMP300", "임시(B8 제품은 임시 확정). 검색량 10 미만"),
]
BAN = ["후기", "내돈내산", "써보니", "사용기", "실사용", "직접", "최고", "무조건", "100%", "완벽", "강력 추천", "1위", "갈리", "갈린", "가르", "쓸모", "구조예요", "지점", "정답", "마주하게",
       "궁금한 점 정리", "대안과 차이", "이런 분께 맞아요", "살 만할까", "사기 전 확인할 점", "제가", "솔직", "찐", "인생"]

ids = {}
for c, v in ck.LIST.items():
    ids[c] = {"products": v}
for c, (a, b) in ck.BPAIR.items():
    ids[c] = {"main": a, "alt": b}
rows = []
for (dt, no, cell, gal, kw, vq, title, end, curio, short, note) in D:
    fr = cell[0]
    pos_ok = nsp(kw).lower() in nsp(title[:16]).lower() and len(kw) <= 15
    rows.append(dict(date=dt, no=no, frame=fr, cell=cell, gal=gal, keyword=kw, vol=vol(vq), vol_query=vq, title=title, tlen=len(title), end=end,
                     curio=curio, short=short, note=note, kw_in_15=pos_ok, bans=[w for w in BAN if w in title], file=f"{dt[5:7]}{dt[8:]}_{no}_{fr}_{short}.md"))

res = {}
res["제목 40자 이내"] = all(r["tlen"] <= 40 for r in rows)
res["키워드 앞 15자 안 아닌 칸"] = [r["cell"] for r in rows if not r["kw_in_15"]]
res["금지 단어"] = [(r["cell"], r["bans"]) for r in rows if r["bans"]]
cA = Counter(r["end"] for r in rows if r["frame"] == "A")
cB = Counter(r["end"] for r in rows if r["frame"] == "B")
gb = sum(1 for r in rows if r["frame"] == "A" and "고르는 법" in r["title"])
byday = {}
for r in rows:
    if r["frame"] == "B":
        byday.setdefault(r["date"], []).append(r["end"])
res["A 끝맺음 개수"] = dict(cA)
res["B 끝맺음 개수"] = dict(cB)
res["A 같은 끝맺음 최대2"] = max(cA.values()) <= 2
res["A 고르는 법 최대2"] = gb <= 2
res["B 같은 끝맺음 최대5"] = max(cB.values()) <= 5
res["같은 날 B 2편 끝맺음 다름"] = all(len(set(v)) == len(v) for v in byday.values())

led = ru.load_ledger()
hits, mk = [], {}
for i in ck.all_ids():
    nm = P[i]["name"]
    url = f"https://brandconnect.naver.com/{ck.ACCT}/affiliate/products/{i}"
    h = ru.find_used(nm, url, led)
    if h:
        hits.append((i, nm[:30], h[0].get("글제목", "")))
    k = ru.model_key(nm)
    mk.setdefault(k if ru.strong_key(k) else "pid:" + i, []).append(i)
res["장부 겹침"] = hits
res["같은 모델"] = {k: v for k, v in mk.items() if len(v) > 1}

q = json.loads((ROOT / "content_queue_radar_naver.json").read_text(encoding="utf-8"))
wk = [(it["keyword"], (it.get("plan") or {}).get("final_title")) for it in q if "2026-11-23" <= ((it.get("plan") or {}).get("publish_date") or "")[:10] < "2026-11-30"]
nq = json.loads((ROOT / "content_queue_naver_tips.json").read_text(encoding="utf-8"))
nt = [(it["keyword"], (it.get("plan") or {}).get("final_title")) for it in nq]
ov = []
for r in rows:
    kn = nsp(r["keyword"]).lower()
    for src, lst in (("11/23 주 Desk Picker", wk), ("N살림노트 대기열", nt)):
        for k2, t2 in lst:
            k2n = nsp(k2).lower()
            if k2n == kn or (len(kn) >= 4 and kn in k2n) or (len(k2n) >= 4 and k2n in kn):
                ov.append((r["cell"], r["keyword"], src, k2, t2, "키워드 같음·포함"))
SAME_KIND = {"B9": "전기방석", "B10": "손난로", "A7": "가습기"}
for r in rows:
    w = SAME_KIND.get(r["cell"])
    if w:
        for k2, t2 in nt:
            if w in k2:
                ov.append((r["cell"], r["keyword"], "N살림노트 대기열", k2, t2, "키워드는 다름·같은 종류 글"))
res["겹침"] = ov
res["11/23 주 Desk Picker 제목 수"] = len(wk)
res["N살림노트 대기열 항목 수"] = len(nt)
(RUN / "_make_res.json").write_text(json.dumps({"rows": rows, "res": res}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

plans = []
for r in rows:
    cell = r["cell"]
    prods = ids[cell]["products"] if r["frame"] == "A" else [ids[cell]["main"], ids[cell]["alt"]]
    plans.append({"platform": "naver", "keyword": r["keyword"], "role": "scheduled", "publish_date": r["date"],
                  "final_title": r["title"], "monthly_total": r["vol"],
                  "product_need": {"need": True, "count": 3 if r["frame"] == "A" else 1, "condition": r["keyword"], "link_kind": "네이버 쇼핑커넥트"},
                  "radar": {"date": r["date"], "title": r["title"], "type": r["frame"], "search_query": r["keyword"], "갈래": r["gal"], "auto_approved": True, "run_id": "plan1130_20261123"},
                  "plan1130": {"순번": r["no"], "칸": cell, "파일이름예정": r["file"], "제품id": prods,
                               "역할": ["제품", "제품", "제품"] if r["frame"] == "A" else ["본상품", "대안(임시)" if cell == "B8" else "대안"],
                               "끝맺음": r["end"], "궁금증": r["curio"], "비고": r["note"]}})
(RUN / "plan.json").write_text(json.dumps({"week": {"start": "2026-11-30", "end": "2026-12-06"}, "channel": "radar_naver", "run_id": "plan1130_20261123",
                                           "made_at": "2026-11-23", "설명": "초안. 대기열에 넣지 않음. radar 칸은 push_plan 모양에 맞춤(connect.md).", "plans": plans},
                                          ensure_ascii=False, indent=1), encoding="utf-8")

L = ["# 11/30 주 기획 초안 (21편)", "",
     "- 만든 날 2026-11-23 · 대기열·앱 데이터·링크 발급 변경 없음 · B8 은 탁상 공기청정기로 교체(임시).",
     "- 월간 검색량 = 검색광고 키워드 도구(PC+모바일). 10 이하는 '10 미만'.", "", "## 1. 21편 표", "",
     "| 날짜 | 순번 | 틀 | 칸 | 갈래 | 키워드 | 월간 검색량 | 제목(글자 수) | 끝맺음 | 제품 상품id | 비고 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
for r in rows:
    if r["frame"] == "A":
        pi = " · ".join(ids[r["cell"]]["products"])
    else:
        pi = f"본 {ids[r['cell']]['main']} / 대안 {ids[r['cell']]['alt']}" + (" (임시)" if r["cell"] == "B8" else "")
    vs = f"{r['vol']:,}" + (" (10 미만)" if r["vol"] <= 10 else "")
    c = r["curio"]
    extra = ""
    if "확인 필요" in c:
        extra = "**" + c.replace("궁금증 값 확인 필요", "궁금증 값 확인 필요").strip() + "**"
    elif c not in ("-", "값 아닌 용도형"):
        extra = "궁금증 값: " + c
    note = "; ".join(x for x in [r["note"], extra] if x)
    L.append(f"| {r['date']} | {r['no']} | {r['frame']} | {r['cell']} | {r['gal']} | {r['keyword']} | {vs} | {r['title']} ({r['tlen']}자) | {r['end']} | {pi} | {note} |")
L += ["", "## 2. 틀 A 칸별 키워드 후보", "", "| 칸 | 후보 | 월간 검색량 | 3개 제품 모두 다룸 | 고른 키워드 / 이유 |", "|---|---|---|---|---|"]
for c, lst in ACAND.items():
    best = max((vol(k), k) for k, o, _ in lst if o == "O")[1]
    for k, o, m in lst:
        pick = ("**고름** — 모두 다루는 후보 중 검색량 최대" + (" · " + m if m else "")) if k == best else m
        L.append(f"| {c} | {k} | {vol(k):,} | {o} | {pick} |")
L += ["", "- A1 제품은 가죽·PET 책상커버·양면 매트라 제목에 펠트·코르크 등은 넣지 않음. A5 는 허브 3개만 다루므로 도킹스테이션은 제목에서 뺌.",
      "- A6 '등받이쿠션'은 검색량 최대지만 상품은 모두 허리 쿠션이라, 더 직접적인 '허리쿠션'(4,220)으로 바꿀 수도 있음(사용자 판단).",
      "- A7 '가습기' 단독은 제품군이 너무 넓어 후보에서 뺌.",
      "", "## 3. 틀 B 키워드·검색어", "", "| 칸 | 키워드(브랜드+모델) | 월간 검색량 | 제목에 넣은 큰 검색어 | 큰 검색어 월간 검색량 |", "|---|---|---|---|---|"]
BIG = {"B1": ("모니터 받침대", "모니터받침대"), "B2": ("독서대", "독서대"), "B3": ("맥세이프 충전기", "맥세이프충전기"), "B4": ("헤드폰 거치대", "헤드폰거치대"),
       "B5": ("사운드바", "사운드바"), "B6": ("헤드폰", "헤드폰"), "B7": ("사무용 의자", "사무용의자"), "B8": ("공기청정기", "공기청정기"), "B9": ("전기방석", "전기방석"),
       "B10": ("충전식 손난로", "충전식손난로"), "B11": ("책상스탠드", "책상스탠드"), "B12": ("LED 스트립", "LED스트립"), "B13": ("미니PC", "미니PC"), "B14": ("태블릿", "태블릿")}
for r in rows:
    if r["frame"] == "B":
        t, k = BIG[r["cell"]]
        L.append(f"| {r['cell']} | {r['keyword']} | {r['vol']:,} | {t} | {vol(k):,} |")
L += ["", "- 풀 표 브랜드 칸 정정: B3 본상품은 벨킨(풀 표 '애플'), B4 니케(풀 표 '소니'는 호환 표기), B12 티피링크, B13 피코펄스(대안 '에어5'는 이그닉 제품).",
      "- B3·B4·B8·B10·B12 는 모델명 검색량이 10~15 수준(검색량 부족). 요청대로 글 키워드는 브랜드+모델명으로 둠.",
      "- B11 제목에는 큰 검색어 '책상스탠드'(6,230)를 넣음. B5 큰 검색어는 사운드바(13,810).", "", "## 4. 파일 이름 예정", ""] + [f"- {r['file']}" for r in rows]
(RUN / "plan.md").write_text("\n".join(L), encoding="utf-8")
for k, v in res.items():
    print(k, ":", json.dumps(v, ensure_ascii=False, default=str)[:700])
print([(r["cell"], r["tlen"]) for r in rows])
