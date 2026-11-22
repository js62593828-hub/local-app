"""11/30 주 21칸 제품 후보 풀 (읽기 전용). 결과는 pipeline/stage_productpool_run/20261130/ 에만 저장.
기존 product_candidates 의 읽기 함수만 가져다 쓴다(기존 파일 수정 없음).
단계: python tools/productpool_1130.py collect   (쇼핑커넥트 목록 읽기, 에고라이트)
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_candidates as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# 지금 에고라이트에 로그인된 쇼핑커넥트 계정 주소(기존 도구의 번호와 다름). 이 스크립트 안에서만 바꿔 씀.
pc.BRANDCONNECT = "https://brandconnect.naver.com/1020008901769280/affiliate/products/search"
OUT = pc.ROOT / "pipeline" / "stage_productpool_run" / "20261130"
RAW = OUT / "raw_naver_parts"
TOPN = 40   # 화면에 하나씩 보이게 하는 읽기가 15초 안에 끝나도록(60은 시간 초과)

# 칸 번호 → (틀, 갈래, 제품군, 검색어 목록)
CELLS = {
 "A1": ("A", "책상 정리", "데스크매트", ["데스크매트"]),
 "A2": ("A", "책상 정리", "케이블 정리", ["케이블 정리"]),
 "A3": ("A", "책상 정리", "탁상시계", ["탁상시계"]),
 "A4": ("A", "키보드·마우스", "노트북 거치대", ["노트북 거치대"]),
 "A5": ("A", "충전·멀티탭", "USB 허브·도킹스테이션", ["USB 허브", "도킹스테이션"]),
 "A6": ("A", "의자·쿠션", "등쿠션", ["등쿠션"]),
 "A7": ("A", "계절가전", "USB 미니 가습기", ["USB 미니 가습기"]),
 "B1": ("B", "책상 정리", "모니터 받침대", ["모니터 받침대"]),
 "B2": ("B", "책상 정리", "독서대", ["독서대"]),
 "B3": ("B", "충전·멀티탭", "무선충전 패드", ["무선충전 패드"]),
 "B4": ("B", "이어폰·스피커", "헤드셋 거치대", ["헤드셋 거치대"]),
 "B5": ("B", "이어폰·스피커", "데스크 사운드바", ["사운드바"]),
 "B6": ("B", "이어폰·스피커", "헤드폰", ["헤드폰"]),
 "B7": ("B", "의자·쿠션", "사무용 의자", ["사무용 의자"]),
 "B8": ("B", "계절가전", "머그 워머", ["머그 워머"]),
 "B9": ("B", "계절가전", "전기방석", ["전기방석"]),
 "B10": ("B", "계절가전", "충전식 손난로", ["충전식 손난로"]),
 "B11": ("B", "조명", "책상스탠드", ["책상스탠드"]),
 "B12": ("B", "조명", "책상 하부 LED 스트립", ["LED 스트립"]),
 "B13": ("B", "모니터·노트북", "미니PC", ["미니PC"]),
 "B14": ("B", "모니터·노트북", "태블릿", ["태블릿"]),
}


def all_queries() -> list[str]:
    out = []
    for v in CELLS.values():
        for q in v[3]:
            if q not in out:
                out.append(q)
    return out


def collect(extra: list[str] | None = None) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    qs = all_queries() + [q for q in (extra or []) if q not in all_queries()]
    st = pc.collect_in_batches(qs, lambda k: k, RAW, pc.BATCH_KW,
                               lambda part, tmp: pc._js_naver(part, tmp, TOPN), "naver", "쇼핑커넥트")
    print("상태:", st)
    return {"ok": 0, "lost": 3, "incomplete": 4}[st]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
    try:
        if cmd == "collect":
            rc = collect(sys.argv[2:])
        else:
            rc = 1
    finally:
        try:
            pc._finish_space()
        except Exception:  # noqa: BLE001
            pass
    sys.exit(rc)
