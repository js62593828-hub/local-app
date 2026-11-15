"""
마크다운 블록(목록·인용구) 앞뒤에 빈 줄을 자동으로 넣어주는 공용 도우미.

원고에서 목록("- 항목", "1. 항목")이나 인용구("> 문장")가 앞 문단과 빈 줄 없이 바로 붙어 있으면, 마크다운은 그걸
목록이 아니라 문단 속 글자로 읽어서 "- "가 화면에 글자 그대로 남습니다. 글쓰기 결과가 빈 줄을 빼먹어도 화면이
깨지지 않도록, 포맷터(seo_formatter.py·shop_formatter.py)가 변환하기 전에 이 함수로 빈 줄을 채워 넣습니다.
"""
import re

_UL_RE = re.compile(r"^[ \t]*[-*+][ \t]+\S")
_HR_RE = re.compile(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$")   # "---", "***" 같은 가로줄은 목록이 아님
_OL_RE = re.compile(r"^[ \t]*\d{1,2}[.)][ \t]+\S")            # "2026. 9. 1" 같은 날짜는 제외(최대 두 자리 번호)
_QUOTE_RE = re.compile(r"^[ \t]*>")


def _kind(line: str) -> str:
    if not line.strip():
        return "blank"
    if _HR_RE.match(line):
        return "text"
    if _UL_RE.match(line):
        return "ul"
    if _OL_RE.match(line):
        return "ol"
    if _QUOTE_RE.match(line):
        return "quote"
    return "text"


def ensure_blank_lines_around_blocks(text: str) -> str:
    """
    목록(ul/ol)·인용구가 문단과 맞닿는 곳(앞뒤 모두)에 빈 줄을 하나 넣습니다. 이미 빈 줄이 있으면 그대로 두고,
    바꿀 게 없으면 원문을 그대로(줄바꿈 형식까지) 돌려줍니다.
      - 번호 목록은 번호 줄이 2개 이상 연달아 있을 때만 목록으로 봅니다(문장 속 "1. " 하나를 오인하지 않게).
      - 목록 항목 아래에 들여쓰기로 이어지는 줄은 그 항목의 연장으로 봅니다.
    """
    if not text or "\n" not in text:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    kinds = [_kind(l) for l in lines]

    # 번호 목록 한 줄짜리는 그냥 문단 글자로 취급
    i = 0
    while i < len(kinds):
        if kinds[i] == "ol":
            j = i
            while j < len(kinds) and kinds[j] == "ol":
                j += 1
            if j - i < 2:
                kinds[i] = "text"
            i = j
        else:
            i += 1

    # 목록 바로 아래 들여쓴 이어지는 줄은 목록의 일부
    for i in range(1, len(lines)):
        if kinds[i] == "text" and kinds[i - 1] in ("ul", "ol") and lines[i][:1] in (" ", "\t"):
            kinds[i] = kinds[i - 1]

    out, changed = [], False
    for i, line in enumerate(lines):
        if i > 0 and kinds[i] != "blank" and kinds[i - 1] != "blank" and kinds[i] != kinds[i - 1]:
            if kinds[i] in ("ul", "ol", "quote") or kinds[i - 1] in ("ul", "ol", "quote"):
                out.append("")
                changed = True
        out.append(line)
    return newline.join(out) if changed else text
