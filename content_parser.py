"""
붙여넣은 전체 텍스트를 플랫폼별 제목+본문으로 자동 분리합니다.
클로드가 [플랫폼:워드프레스] 같은 표시와, 각 섹션 첫 줄에 "제목: ..."을 정확히 써준다는
전제로 파싱합니다.
"""
import re

PLATFORM_ALIASES = {
    "워드프레스": "wordpress",
    "네이버": "naver",
    "네이버블로그": "naver",
    "티스토리": "tistory",
}


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def parse_pasted_content(raw_text: str) -> dict:
    text = _strip_code_fence(raw_text)

    platform_title = {"wordpress": "", "naver": "", "tistory": ""}
    platform_raw = {"wordpress": "", "naver": "", "tistory": ""}

    matches = list(re.finditer(r"\[플랫폼\s*[:：]\s*([^\]]+)\]", text))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        key = PLATFORM_ALIASES.get(name)
        if not key:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end].strip()

        title_match = re.match(r"제목\s*[:：]\s*(.+)", section)
        if title_match:
            platform_title[key] = title_match.group(1).strip()
            body = section[title_match.end():].strip()
        else:
            body = section

        platform_raw[key] = body

    missing = [
        {"wordpress": "워드프레스", "naver": "네이버", "tistory": "티스토리"}[k]
        for k in platform_raw
        if not platform_raw[k] or not platform_title[k]
    ]

    return {
        "platform_title": platform_title,
        "platform_raw": platform_raw,
        "parse_ok": any(platform_raw.values()),
        "missing_platforms": missing,
    }
