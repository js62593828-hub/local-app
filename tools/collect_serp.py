# -*- coding: utf-8 -*-
"""네이버 검색결과 구조 수집기 (Claude 없이 단독 실행, 읽기 전용)

ego lite 브라우저(ego-browser 명령)로 네이버를 검색해서 결과를 '순수 JSON'으로 내보낸다.
  - 화면(표준출력)에는 JSON 만 나온다. 진행 상황·오류 안내는 표준오류로 따로 나간다.
  - 실제 수집 로직은 tools/naver_serp_collect.py 의 collect() 를 그대로 쓴다 (그 파일은 수정하지 않음).
  - 앱 장부(articles*.json, content_queue*.json)는 건드리지 않는다.

사용법
  python tools/collect_serp.py 캠핑 의자 추천                      # 키워드 1개 → JSON 객체 1개
  python tools/collect_serp.py --file keywords.txt                 # 목록 파일 → JSON 배열
  python tools/collect_serp.py --file keywords.txt --out result.json
  python tools/collect_serp.py --file keywords.txt --delay 5 --detail

키워드 목록 파일: 한 줄에 키워드 하나. 빈 줄과 # 으로 시작하는 줄은 무시.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naver_serp_collect import collect  # noqa: E402

BASIC_FIELDS = ["keyword", "collected_at", "smart_blocks", "related_keywords",
                "has_ad", "has_shopping", "has_image",
                "has_ai_briefing", "ai_briefing_sources"]
DETAIL_FIELDS = ["sections", "url"]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def read_keyword_file(path: Path) -> list:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")  # 메모장에서 'ANSI'로 저장한 경우
    keywords, seen = [], set()
    for line in text.splitlines():
        kw = " ".join(line.split())
        if not kw or kw.startswith("#"):
            continue
        if kw in seen:
            log(f"[건너뜀] 중복 키워드: {kw}")
            continue
        seen.add(kw)
        keywords.append(kw)
    return keywords


def collect_one(keyword: str, detail: bool) -> dict:
    from datetime import datetime
    try:
        data = collect(keyword)
    except Exception as e:
        return {"keyword": keyword,
                "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "error": str(e)}
    for line in data.get("ego_notice", []):
        log(f"[안내] {line}")
    fields = BASIC_FIELDS + (DETAIL_FIELDS if detail else [])
    return {k: data[k] for k in fields if k in data}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="네이버 검색결과 구조 수집 (순수 JSON 출력)")
    ap.add_argument("keyword", nargs="*", help="검색할 키워드 1개 (띄어쓰기 그대로 써도 됨)")
    ap.add_argument("--file", help="키워드 목록 파일 (한 줄에 하나, UTF-8 권장)")
    ap.add_argument("--out", help="결과를 이 JSON 파일로도 저장")
    ap.add_argument("--delay", type=float, default=3.0, help="키워드 사이 쉬는 시간(초), 기본 3")
    ap.add_argument("--detail", action="store_true", help="영역 전체 목록(sections)과 주소(url)도 포함")
    args = ap.parse_args()

    single = " ".join(args.keyword).strip()
    if args.file and single:
        log("키워드와 --file 은 함께 쓸 수 없습니다. 둘 중 하나만 주세요.")
        return 2
    if args.file:
        path = Path(args.file)
        if not path.is_file():
            log(f"[실패] 목록 파일을 찾을 수 없습니다: {path}")
            return 2
        keywords = read_keyword_file(path)
        if not keywords:
            log("[실패] 목록 파일에 키워드가 없습니다.")
            return 2
    elif single:
        keywords = [single]
    else:
        ap.print_usage(sys.stderr)
        log("키워드 또는 --file 을 주세요.")
        return 2

    results = []
    stopped = False
    try:
        for i, kw in enumerate(keywords, 1):
            if i > 1 and args.delay > 0:
                time.sleep(args.delay)
            log(f"[{i}/{len(keywords)}] 수집 중: {kw}")
            item = collect_one(kw, args.detail)
            if "error" in item:
                first = (item["error"].splitlines() or ["(이유 없음)"])[0]
                log(f"[{i}/{len(keywords)}] 실패: {first}")
            results.append(item)
    except KeyboardInterrupt:
        stopped = True
        log(f"[중단] Ctrl+C 로 멈췄습니다. 지금까지 모은 {len(results)}개만 내보냅니다.")
    if not results:
        return 130 if stopped else 1

    output = results if args.file else results[0]
    text = json.dumps(output, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text + "\n", encoding="utf-8")
            log(f"[저장됨] {out}")
        except OSError as e:
            log(f"[실패] 파일로 저장하지 못했습니다: {e}")
            return 1

    failed = sum(1 for r in results if "error" in r)
    log(f"[완료] 성공 {len(results) - failed}개 / 실패 {failed}개")
    if stopped:
        return 130
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
