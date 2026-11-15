"""
SEO 적용 모듈 — API 호출 없이(비용 없이) 규칙 기반으로 처리합니다.
- 마크다운(## 제목, **볼드** 등)을 HTML로 변환
- 금액/퍼센트/날짜 같은 핵심 수치를 자동으로 굵게 + 색상 강조
- '라벨: 값' 형태가 3줄 이상 연속되면 자동으로 표로 변환
"""
import json
import datetime
import re

import markdown as md_lib

from md_blocks import ensure_blank_lines_around_blocks
from shop_formatter import (
    TABLE_HEAD_BG,
    TABLE_HEAD_COLOR,
    TABLE_CELL_STYLE,
    TABLE_HEAD_CELL_STYLE,
    TABLE_FIRST_COL_STYLE,
)
import shop_formatter  # AUTO_NUMBER_COLOR를 실행 때마다 읽기 위해(한 곳에서 정의, 두 파일이 같이 씀)

# 금액은 "숫자 + 원" 하나가 아니라 "69만 8,500원", "74만 9천 원", "1억 2,000만원", "2.35만원",
# "160만~251만원대"처럼 단위(억·만·천·백)와 여러 조각으로 나뉘어 나옵니다. 예전 패턴은 마지막
# "숫자+원"만 잡아서 앞부분이 잘렸습니다 — 조각들을 이어서 "원"(과 뒤의 "대")까지 통째로 잡습니다.
# (shop_formatter.py와 같은 규칙입니다.)
_AMOUNT = r"\d[\d,]*(?:\.\d+)?"
_PART = rf"{_AMOUNT}(?:\s?[억만천백])?"
_SEQ = rf"{_PART}(?:\s?{_PART})*"
MONEY_PATTERN = re.compile(rf"(?<![\d,.])({_SEQ}(?:\s?[~～\-–]\s?{_SEQ})?\s?원대?)(?!칙|본|래|인|룸)")
# 퍼센트는 "40~60%", "2~3 %", "10-15%"처럼 범위(물결 ~·～, 하이픈 -, 대시 –)도 한 덩어리로 강조합니다 (shop_formatter.py와 같은 규칙).
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?(?:\s?[~～\-–]\s?\d+(?:\.\d+)?)?\s?%)")
DATE_PATTERN = re.compile(r"(\d{4}년\s?\d{1,2}월\s?\d{1,2}일|\d{1,2}월\s?\d{1,2}일)")

# ── 글쓰기 프롬프트의 강조 마크업 ──────────────────────────────────────────────
#   **강조할 문구**              → 문장 안 일부 강조 (보라 글씨 + 노란 형광펜 배경)
#   "> 문장" (앞뒤 빈 줄, 한 줄)  → 전환점 인용구 (청록 큰 글씨 + 옅은 청록 배경)
# markdown 라이브러리가 **를 <strong>으로, >를 <blockquote>로 이미 바꿔주므로, 여기서는 그 결과에
# 색·배경 스타일만 입힙니다. 색은 아래 자동 강조 색(금액 빨강 #dc2626, 퍼센트 파랑 #2563eb, 날짜 초록
# #059669)과 겹치지 않게 골랐습니다. (숍 채널 shop_formatter.py와 같은 색·같은 모양입니다.)
EMPHASIS_TEXT, EMPHASIS_BG = "#6d28d9", "#fef08a"
QUOTE_TEXT, QUOTE_BG = "#0f766e", "#ccfbf1"
# 네이버 경로 전용: 스마트에디터가 background-color 인라인 스타일을 유지하는지 확인 전이라 두 버전을 둡니다.
# True = (a) 글씨색 + 배경색 + 굵게 / False = (b) 글씨색 + 굵게만 (배경이 지워지면 이걸로 바꾸고 최종본 재생성)
NAVER_EMPHASIS_BACKGROUND = True

_BARE_STRONG_RE = re.compile(r"<strong>(.*?)</strong>", re.DOTALL)
_BLOCKQUOTE_RE = re.compile(r"<blockquote>\s*(.*?)\s*</blockquote>", re.DOTALL)


def _strip_heading_markup(text: str) -> str:
    """## 소제목 줄 안의 **를 제거합니다 (소제목·목차·카드뉴스 제목에 강조 태그가 섞이지 않게)."""
    return re.sub(r"^(##\s+.*)$", lambda m: m.group(1).replace("**", ""), text, flags=re.MULTILINE)


def _style_quotes(html: str, platform: str) -> str:
    """
    <blockquote>를 전환점 인용구 스타일로 바꿉니다. 안쪽은 <p>가 아니라 <div>로 씁니다 — 카드뉴스가
    <p>/<li>에서 요약 문장을 뽑고, 이미지 삽입이 (소제목이 없을 때) </p>를 위치 기준으로 쓰기 때문에,
    인용구 안에 <p>가 있으면 그 동작이 바뀝니다. 인용구 안의 <strong>은 이미 전체가 강조된 것이라 뺍니다.
    """

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        paragraphs = re.findall(r"<p>(.*?)</p>", inner, flags=re.DOTALL) or [inner]
        text = "<br>".join(re.sub(r"</?strong>", "", p).strip() for p in paragraphs)
        if platform == "naver":
            # 박스·배경이 지워져도 눈에 띄게: 큰 글씨 + 다른 색만 사용 (font-size 인라인은 보통 살아남음)
            # 인용구는 짧고 큰 글씨라 justify를 걸면 글자 사이가 벌어져 보여서 원래 정렬(왼쪽)로 둡니다.
            return f'<p style="text-align:left !important;"><strong style="font-size:20px; color:{QUOTE_TEXT};">{text}</strong></p>'
        if platform == "wordpress":
            # 혜택주머니(워드프레스): 테마가 blockquote에 기울임꼴을 넣으므로 font-style:normal을 blockquote·안쪽 div 둘 다에 넣습니다.
            return (
                '<blockquote style="margin:32px 0 !important; padding:18px 22px; border:0; '
                'background:#f1f5f9; border-radius:10px; font-style:normal;">'
                '<div style="margin:0; font-size:17px; font-weight:600; line-height:1.7; color:#1e293b; '
                f'font-style:normal; text-align:left !important;">{text}</div>'
                "</blockquote>"
            )
        return (
            f'<blockquote style="margin:26px 12px !important; padding:14px 20px; '
            f'border-left:4px solid {QUOTE_TEXT}; background:{QUOTE_BG};">'
            f'<div style="margin:0; font-size:22px; font-weight:700; line-height:1.5; color:{QUOTE_TEXT}; '
            f'text-align:left !important;">{text}</div>'
            "</blockquote>"
        )

    return _BLOCKQUOTE_RE.sub(repl, html)


def _style_emphasis(html: str, platform: str) -> str:
    """스타일 없는 <strong>(= 원문의 **강조**)에 보라 글씨 + 노란 형광펜을 입힙니다. 금액·퍼센트·날짜 자동
    강조(_auto_bold)보다 먼저 실행해야, "**295,200원까지 받을 수 있어요**"처럼 겹칠 때 안쪽 금액 색과 바깥
    형광펜이 둘 다 살아납니다."""

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if platform == "naver":
            bg = f" background-color:{EMPHASIS_BG};" if NAVER_EMPHASIS_BACKGROUND else ""
            return f'<strong style="color:{EMPHASIS_TEXT};{bg}">{inner}</strong>'
        return (
            f'<strong style="color:{EMPHASIS_TEXT}; background-color:{EMPHASIS_BG}; '
            f'padding:0 3px; border-radius:3px;">{inner}</strong>'
        )

    return _BARE_STRONG_RE.sub(repl, html)


def _auto_bold(html: str) -> str:
    # 클래스가 아니라 인라인 style로 직접 색을 심습니다. 이래야 워드프레스/티스토리 에디터에
    # 붙여넣었을 때도(그쪽엔 우리 CSS 클래스가 없으므로) 색상이 그대로 유지됩니다.
    if not shop_formatter.AUTO_NUMBER_COLOR:  # 꺼짐(기본): 숫자는 일반 글자 그대로 (** 강조만 색)
        return html
    html = MONEY_PATTERN.sub(r'<strong style="color:#dc2626;">\1</strong>', html)
    html = PERCENT_PATTERN.sub(r'<strong style="color:#2563eb;">\1</strong>', html)
    html = DATE_PATTERN.sub(r'<strong style="color:#059669;">\1</strong>', html)
    return html


SENTENCE_ENDINGS = ("다.", "요.", "함.", "음.", "까?", "다!", ":", "：")


def _looks_like_list_item(line: str) -> bool:
    """짧고, 문장 종결어미로 안 끝나고, 이미 목록 기호가 없는 줄 — 나열형 항목일 가능성이 높음."""
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "-", "*", "|", ">")):  # ">"(인용구)를 목록 항목으로 오인하지 않게
        return False
    if _never_list(stripped):
        return False
    if len(stripped) > 60:
        return False
    return not stripped.endswith(SENTENCE_ENDINGS)


_NUMBERED_START_RE = re.compile(r"\d+[.)]")   # "1." "2)" — 이미 번호 목록(또는 번호가 붙은 문장)
_QA_START_RE = re.compile(r"[QA]\.")           # FAQ의 "Q." "A."


def _never_list(stripped: str) -> bool:
    """
    이런 줄은 절대 목록("- ")이나 표 행으로 바꾸지 않습니다:
      - "?"/"!"로 끝나는 줄 (질문·감탄 문장이지 나열 항목이 아님)
      - "숫자." 또는 "숫자)"로 시작하는 줄 (이미 번호 목록 — "- 1. …"로 바꾸면 번호가 깨짐)
      - "Q."/"A."로 시작하는 줄 (FAQ)
      - "<"로 시작하는 HTML 줄 (면책 <small> 줄 등)
    """
    return (
        stripped.endswith(("?", "!", "？", "！"))
        or bool(_NUMBERED_START_RE.match(stripped))
        or bool(_QA_START_RE.match(stripped))
        or stripped.startswith("<")
    )


def _auto_table(text: str) -> str:
    """
    '라벨: 값' 형태가 3줄 이상 연속되면 표(마크다운 테이블)로 변환하고, 짧은 나열형 줄은 목록으로 바꿉니다.
    단, 목록/표로 바꾸는 건 "목록처럼 보이는 줄이 빈 줄 없이 2줄 이상 연속될 때"뿐입니다 — 앞뒤가 빈 줄이거나
    다른 문장에 붙어 있는 한 줄짜리는 그냥 문단이라 원문 그대로 둡니다. _never_list에 걸리는 줄도 그대로 둡니다.
    """
    lines = text.split("\n")
    out = []
    buffer = []

    def flush_buffer():
        if len(buffer) >= 3:
            out.append("")
            out.append("| 항목 | 내용 |")
            out.append("|---|---|")
            for label, value in buffer:
                out.append(f"| {label} | {value} |")
            out.append("")
        else:
            for label, value in buffer:
                out.append(f"- {label}: {value}")
        buffer.clear()

    row_pattern = re.compile(r"^\s*[-*]?\s*([^:：]{1,20})[:：]\s*(.+)$")

    def as_row(line: str):
        m = row_pattern.match(line)
        # 내부링크 자리표시자 줄("- {{LINK:키워드}}")은 표 행이 아닙니다 — 첫 콜론이 "{{LINK:"의 콜론이라
        # "{{LINK" | "키워드}}"로 쪼개지면 링크 채우기 정규식(\{\{LINK:(.*?)\}\})이 못 찾으므로, 일반 목록으로 남겨 둡니다.
        # ("라벨: 설명 … {{LINK:X}} …"처럼 값 쪽에 링크가 있는 줄은 쪼개지지 않으므로 예전처럼 표 행입니다.)
        if m and "{{" in m.group(1):
            return None
        # 인용구 줄(">")과, "**핵심: 무게**"처럼 ** 짝이 첫 콜론에서 잘리는 줄은 표 행 후보에서 제외합니다.
        # (row_pattern의 [-*]?가 앞쪽 * 하나를 먹기 때문에, 라벨 그룹이 아니라 원문의 콜론 앞부분을 직접 검사)
        colon_positions = [i for i in (line.find(":"), line.find("：")) if i >= 0]
        label_raw = line[: min(colon_positions)] if colon_positions else ""
        stripped = line.strip()
        if m and not stripped.startswith(("#", ">")) and label_raw.count("**") % 2 == 0 and not _never_list(stripped):
            return (m.group(1).strip(), m.group(2).strip())
        return None

    # 1) 줄마다 종류를 정하고, 2) 목록처럼 보이는 줄(행/짧은 줄)이 연속된 덩어리의 길이를 구합니다.
    rows = [as_row(line) for line in lines]
    cands = [rows[i] is None and _looks_like_list_item(line) for i, line in enumerate(lines)]
    listlike = [rows[i] is not None or cands[i] for i in range(len(lines))]
    run_len = [0] * len(lines)
    i = 0
    while i < len(lines):
        if listlike[i]:
            j = i
            while j < len(lines) and listlike[j]:
                j += 1
            for k in range(i, j):
                run_len[k] = j - i
            i = j
        else:
            i += 1

    for idx, line in enumerate(lines):
        if run_len[idx] < 2:
            # 한 줄짜리(앞뒤가 빈 줄이거나 다른 문장에 붙은 줄)는 목록/표로 바꾸지 않고 원문 그대로
            flush_buffer()
            out.append(line)
        elif rows[idx] is not None:
            buffer.append(rows[idx])
        else:  # 짧은 나열형 줄 (cands)
            flush_buffer()
            out.append(f"- {line.strip()}")
    flush_buffer()
    return "\n".join(out)


NAVER_HEADING_EMOJI = [
    (re.compile(r"대상|자격"), "📋"),
    (re.compile(r"금액|지원금|비용|가격"), "💰"),
    (re.compile(r"신청|방법|절차"), "✍️"),
    (re.compile(r"기간|일정|날짜"), "🗓️"),
    (re.compile(r"faq|자주\s*묻는", re.I), "❓"),
    (re.compile(r"주의|유의|놓치기"), "⚠️"),
]


def _naver_emoji_for(heading: str) -> str:
    for pattern, emoji in NAVER_HEADING_EMOJI:
        if pattern.search(heading):
            return emoji
    return "📌"


def _add_naver_heading_emoji(text: str) -> str:
    """네이버 특유의 캐주얼한 톤을 위해 소제목 앞에 관련 이모지를 붙입니다.
    (네이버 에디터는 구조화 데이터를 지원하지 않아, 대신 체류시간에 유리한 스타일을 강화합니다.)"""

    def repl(m):
        heading = m.group(1)
        emoji = _naver_emoji_for(heading)
        return f"## {emoji} {heading}"

    return re.sub(r"^##\s+(.+)$", repl, text, flags=re.MULTILINE)


# 워드프레스(혜택주머니)용. 네이버·티스토리는 아래 *_LEGACY 값을 그대로 씁니다(출력이 바뀌면 안 됨).
H2_INLINE_STYLE = (
    "font-size:22px; font-weight:700; line-height:1.45; letter-spacing:-0.3px; color:#1e293b; "
    "border-left:3px solid #2563eb; padding-left:12px; margin-top:52px !important; margin-bottom:18px !important;"
)
H2_INLINE_STYLE_LEGACY = "border-left:4px solid #2563eb; padding-left:12px; margin-top:40px !important; margin-bottom:16px !important;"
# text-align:justify로 문단의 오른쪽 끝(우측 라인)도 왼쪽처럼 가지런히 맞춥니다 (마지막 줄은 항상 왼쪽 정렬로 남습니다).
# !important를 붙인 이유: 워드프레스 테마 중 일부가 본문 요소에 text-align !important 리셋을
# 걸어두는 경우가 있어서, 인라인 스타일이 이겨야 하는 곳(정렬)은 전부 !important로 방어합니다.
# (word-break:keep-all은 일부러 안 씁니다 — 한글 justify와 같이 쓰면 좁은 화면에서 어절 사이
# 간격이 큼직하게 벌어지는 부작용이 있어서, 지금처럼 keep-all 없이 justify만 쓰는 조합을 유지합니다.)
P_INLINE_STYLE = "margin:0 0 28px 0 !important; line-height:1.9; text-align:justify !important;"
UL_OL_INLINE_STYLE = "margin:0 0 28px 0 !important; line-height:1.9; text-align:justify !important;"
LI_INLINE_STYLE = "text-align:justify !important; margin-bottom:6px;"
P_INLINE_STYLE_LEGACY = "margin:0 0 22px 0 !important; line-height:1.8; text-align:justify !important;"
UL_OL_INLINE_STYLE_LEGACY = "margin:0 0 22px 0 !important; line-height:1.8; text-align:justify !important;"
LI_INLINE_STYLE_LEGACY = "text-align:justify !important;"
TABLE_INLINE_STYLE = "margin:20px 0 26px 0 !important; width:100%; border-collapse:collapse;"


def _style_headings(html: str, platform: str = "wordpress") -> str:
    """<h2> 태그에 파란 왼쪽 테두리 + 위아래 여백을 인라인 style로 직접 심습니다.
    (붙여넣는 곳에 우리 CSS가 없어도, 워드프레스 테마가 자체 여백을 눌러버려도
    스타일이 그대로 유지되도록 !important를 붙였습니다.)"""
    style = H2_INLINE_STYLE if platform == "wordpress" else H2_INLINE_STYLE_LEGACY
    return re.sub(r"<h2>", f'<h2 style="{style}">', html)


_QA_PARAGRAPH_RE = re.compile(r"<p>\s*Q[.:]?\s*(.+?)\n\s*A[.:]?\s*(.+?)\s*</p>", re.DOTALL)


def _style_faq_qa(html: str) -> str:
    """
    FAQ의 "Q. 질문\nA. 답변" 문단(원문에서 줄바꿈만 있고 빈 줄은 없는 한 쌍)에서 질문(Q.)을 굵게 표시하고,
    질문과 답변 사이에 실제 줄바꿈(<br>)을 넣습니다 — 원문의 개행이 테마에 따라 무시되고 한 줄로 붙어
    보이는 경우가 있어, 태그로 확실히 줄을 나눕니다. "Q:"처럼 마침표 없이 콜론을 쓴 원문도 인식하고,
    표시는 항상 "Q. "/"A. " 형태로 통일합니다. <p> 태그 자체는 그대로 둬서(_style_spacing이 나중에
    스타일을 입힐 수 있게) 다른 문단과 똑같이 여백·정렬이 적용됩니다.
    """

    def repl(m: re.Match) -> str:
        q, a = m.group(1).strip(), m.group(2).strip()
        return f"<p><strong>Q. {q}</strong><br>A. {a}</p>"

    return _QA_PARAGRAPH_RE.sub(repl, html)


def _style_spacing(html: str, platform: str = "wordpress") -> str:
    """<p>/<ul>/<ol>/<table>에 위아래 여백을 인라인 style로 심습니다. 단락과 단락,
    단락과 다음 소제목 사이가 워드프레스 테마 기본값보다 확실히 벌어지도록 합니다.
    <li>에도 양쪽 정렬을 따로 심습니다 — text-align은 자식에게 상속되긴 하지만, 테마가
    li에 별도 규칙을 걸어둔 경우까지 방어하려면 li 자체에 인라인으로 넣는 게 확실합니다."""
    wp = platform == "wordpress"
    p_style = P_INLINE_STYLE if wp else P_INLINE_STYLE_LEGACY
    list_style = UL_OL_INLINE_STYLE if wp else UL_OL_INLINE_STYLE_LEGACY
    li_style = LI_INLINE_STYLE if wp else LI_INLINE_STYLE_LEGACY
    html = re.sub(r"<p>", f'<p style="{p_style}">', html)
    html = re.sub(r"<(ul|ol)>", lambda m: f'<{m.group(1)} style="{list_style}">', html)
    html = re.sub(r"<li>", f'<li style="{li_style}">', html)
    html = re.sub(r"<table>", f'<table style="{TABLE_INLINE_STYLE}">', html)
    return html


_TABLE_BLOCK_RE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
_TABLE_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.DOTALL | re.IGNORECASE)
_TABLE_CELL_TAG_RE = re.compile(r"<(th|td)\b([^>]*)>", re.IGNORECASE)
_CELL_STYLE_ATTR_RE = re.compile(r"""\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_CELL_ALIGN_ATTR_RE = re.compile(r"""\s(?:align|valign|bgcolor)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)
# 셀 스타일에서 우리가 직접 정하는 속성 — markdown이 정렬 표시(:---:) 때문에 먼저 붙인 style 안의 같은 속성은 버립니다.
_CELL_OWN_PROPS = {"padding", "text-align", "vertical-align", "background-color", "color", "font-weight", "border"}
TABLE_CELL_BORDER = "1px solid #808080"  # 셀 테두리(네이버·싼세계 표의 border="1" 선과 같은 회색)
_TH_BLOCK_RE = re.compile(r"(<th\b[^>]*>)(.*?)(</th>)", re.DOTALL | re.IGNORECASE)
_INNER_COLOR_RE = re.compile(r"(?<![-\w])color\s*:\s*[^;\"']+", re.IGNORECASE)
_INNER_BG_RE = re.compile(r"background(?:-color)?\s*:\s*[^;\"']+;?\s*", re.IGNORECASE)


def _force_head_text_white(html: str) -> str:
    """머리줄(th) 안쪽 태그에 붙은 글자색(금액 빨강·퍼센트 파랑 등)을 전부 흰색으로, 형광펜 배경은 없앱니다."""

    def fix(m: re.Match) -> str:
        inner = _INNER_BG_RE.sub("", m.group(2))
        inner = _INNER_COLOR_RE.sub(f"color:{TABLE_HEAD_COLOR}", inner)
        return m.group(1) + inner + m.group(3)

    return _TH_BLOCK_RE.sub(fix, html)


def _table_cell_open_tag(tag: str, attrs: str, style: str, bgcolor: str = "") -> str:
    """<th ...>/<td ...> 열린 태그를 새 style로 다시 만듭니다. 이미 붙은 style·align·bgcolor 속성은 정리하고,
    남는 다른 style 규칙(예: width)과 다른 속성(colspan 등)은 그대로 둡니다."""
    kept = []
    m = _CELL_STYLE_ATTR_RE.search(attrs)
    if m:
        for decl in (m.group(1) if m.group(1) is not None else m.group(2)).split(";"):
            prop = decl.split(":", 1)[0].strip().lower()
            if prop and prop not in _CELL_OWN_PROPS:
                kept.append(decl.strip())
        attrs = attrs[: m.start()] + attrs[m.end():]
    attrs = _CELL_ALIGN_ATTR_RE.sub("", attrs)
    full_style = (style + (" " + "; ".join(kept) + ";" if kept else "")).strip()
    bg = f' bgcolor="{bgcolor}"' if bgcolor else ""
    return f'<{tag}{attrs}{bg} style="{full_style}">'


def _style_tables(html: str, platform: str = "wordpress") -> str:
    """표 모양을 전 채널과 똑같이 맞춥니다: 머리줄(th)=진회색 바탕·흰 글씨·굵게, 각 줄의 첫 td=굵게, 모든 칸 가운데 정렬.
    색·정렬 값은 shop_formatter의 TABLE_* 상수를 그대로 씁니다(한 곳만 고치면 전 채널이 같이 바뀜).
    네이버는 붙여넣을 때 style이 지워질 수 있어 th에 bgcolor 속성도 같이 넣습니다.
    markdown이 정렬 표시(:---:) 때문에 이미 붙인 style/align 속성도 덮어씁니다. _auto_table이 만든 "항목 | 내용" 표도 같습니다."""
    border = f"border:{TABLE_CELL_BORDER};"
    head_style = f"{border} {TABLE_HEAD_CELL_STYLE}"
    first_style = f"{border} {TABLE_FIRST_COL_STYLE}"
    cell_style = f"{border} {TABLE_CELL_STYLE}"
    head_bg = TABLE_HEAD_BG if platform == "naver" else ""

    def style_row(row_match: re.Match) -> str:
        seen_td = False

        def cell(m: re.Match) -> str:
            nonlocal seen_td
            tag, attrs = m.group(1).lower(), m.group(2)
            if tag == "th":
                return _table_cell_open_tag("th", attrs, head_style, head_bg)
            style = cell_style if seen_td else first_style
            seen_td = True
            return _table_cell_open_tag("td", attrs, style)

        return _TABLE_CELL_TAG_RE.sub(cell, row_match.group(0))

    html = _TABLE_BLOCK_RE.sub(lambda t: _TABLE_ROW_RE.sub(style_row, t.group(0)), html)
    return _force_head_text_white(html) if "<th" in html else html


WIDE_TABLE_MIN_COLS = 5  # 이 열 수 이상이면 모바일에서 화면 밖으로 넘칠 수 있는 "넓은 표"
_TABLE_RE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r"<tr\b.*?</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<t[hd]\b([^>]*)>", re.IGNORECASE)
_COLSPAN_RE = re.compile(r"colspan\s*=\s*[\"']?(\d+)", re.IGNORECASE)


def table_column_counts(html: str) -> list[int]:
    """HTML 안의 <table>마다 열 수(첫 줄 기준, colspan 반영)를 차례로 돌려줍니다. 읽기만 하고 HTML은 바꾸지 않습니다."""
    counts = []
    for table in _TABLE_RE.findall(html or ""):
        row = _TR_RE.search(table)
        cols = 0
        for attrs in _CELL_RE.findall(row.group(0) if row else ""):
            span = _COLSPAN_RE.search(attrs)
            cols += int(span.group(1)) if span else 1
        counts.append(cols)
    return counts


def wide_table_max_cols(html: str) -> int:
    """WIDE_TABLE_MIN_COLS열 이상인 표가 있으면 그중 가장 큰 열 수, 없으면 0 (최종본 화면 경고용)."""
    widest = max(table_column_counts(html), default=0)
    return widest if widest >= WIDE_TABLE_MIN_COLS else 0


def apply_seo_formatting(body_raw: str, platform: str = "wordpress") -> str:
    """원문(마크다운 형식 텍스트)을 받아 SEO 스타일이 적용된 HTML로 변환합니다.
    platform에 따라 처리를 달리합니다:
    - wordpress/tistory (구글·다음 타겟): 표준 마크다운 변환만 (구조화 데이터/목차는 별도 함수에서 처리)
    - naver (네이버 타겟): 소제목에 이모지 추가 (네이버는 구조화 데이터를 지원하지 않음)
    """
    text = body_raw
    if platform == "naver":
        text = _add_naver_heading_emoji(text)
    text = _strip_heading_markup(text)
    text = ensure_blank_lines_around_blocks(text)  # 목록·인용구가 앞 문단과 붙어 있어도 "- "가 글자로 남지 않게 빈 줄 보정
    pre_processed = _auto_table(text)
    html = md_lib.markdown(pre_processed, extensions=["tables"])
    html = _style_quotes(html, platform)
    html = _style_emphasis(html, platform)
    html = _auto_bold(html)
    html = _style_faq_qa(html)
    html = _style_headings(html, platform)
    html = _style_spacing(html, platform)
    html = _style_tables(html, platform)
    return html


# ---------- 혜택주머니 도입부 문단 나누기 ----------
# 나누는 규칙(문장 자르기·묶기)은 shop_formatter.split_intro_paragraphs 를 그대로 다시 씁니다(네이버·싼세계와 같음).
# 혜택주머니 원고는 "최종 확인: 날짜 · 출처: …" 줄 → 빈 줄 → 도입부 → 첫 "## " 순서라서,
# 맨 앞 "최종 확인" 줄은 도입부에서 빼고 그대로 둡니다. 원고(raw)·저장 데이터는 바꾸지 않고 화면·복사 출력에만 적용됩니다.
_CONFIRM_LINE_RE = re.compile(r"^[\W_]*최종\s*확인[\s*_]*[:：]\s*(.+)$")  # app.py 의 _LT_CONFIRM_LINE_RE 와 같은 규칙
_FIRST_H2_RE = re.compile(r"(?m)^##(?!#)")


def split_intro_raw(raw: str) -> tuple[str, bool]:
    """혜택주머니 원고에서 첫 소제목(##) 앞 도입부만 짧은 문단으로 나눈 원고를 돌려줍니다.
    반환: (원고, 맨 앞이 "최종 확인" 줄인지). 바뀔 게 없으면 원본 글자 그대로 돌려줍니다.
    "최종 확인" 줄 바로 뒤에 빈 줄 없이 도입부가 붙어 있으면(한 문단으로 그려짐) 안전하게 건드리지 않습니다."""
    text = raw or ""
    m = _FIRST_H2_RE.search(text)
    if not m:
        return raw, False  # 소제목이 없으면 어디까지가 도입부인지 알 수 없음
    head, tail = text[:m.start()].replace("\r\n", "\n").replace("\r", "\n"), text[m.start():]
    lines = head.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None:
        return raw, False
    confirm = ""
    intro_lines = lines
    has_confirm = bool(_CONFIRM_LINE_RE.match(lines[first].strip()))
    if has_confirm:
        confirm = lines[first].strip()
        intro_lines = lines[first + 1:]
        if intro_lines and intro_lines[0].strip():
            return raw, True  # 붙어 있음: 그대로 둠
    intro = "\n".join(intro_lines).strip()
    new_intro = shop_formatter.split_intro_paragraphs(intro).strip()
    if new_intro == intro:
        return raw, has_confirm
    parts = ([confirm] if confirm else []) + [new_intro]
    return "\n\n".join(parts) + "\n\n" + tail, has_confirm


def style_intro_paragraphs(html: str, skip_first: bool = False) -> str:
    """첫 <h2> 앞 본문 문단(<p>)의 아래 여백을 싼세계 도입부와 같은 값(shop_formatter.INTRO_P_MARGIN_WP)으로 바꿉니다.
    skip_first=True면 맨 앞 문단("최종 확인" 줄)은 그대로 둡니다. 목차 상자·소제목 이후 본문은 건드리지 않습니다."""
    m = re.search(r"<h2\b", html)
    if not m:
        return html  # 소제목이 없으면 도입부 범위를 알 수 없음 (split_intro_raw 와 같은 기준)
    head, rest = html[:m.start()], html[m.start():]
    old = f'<p style="{P_INLINE_STYLE}">'
    new = f'<p style="{P_INLINE_STYLE.replace("margin:0 0 28px 0", "margin:0 0 " + shop_formatter.INTRO_P_MARGIN_WP + " 0", 1)}">'
    idx = head.find(old)
    if skip_first and idx != -1:
        cut = idx + len(old)
        head = head[:cut] + head[cut:].replace(old, new)
    else:
        head = head.replace(old, new)
    return head + rest


def _extract_faq_pairs(body_raw: str) -> list[tuple[str, str]]:
    """## FAQ 섹션 아래 Q. ... A. ... 패턴을 추출합니다."""
    section = re.search(r"##\s*FAQ\s*\n(.*?)(?=\n##\s|\Z)", body_raw, re.S | re.I)
    if not section:
        return []
    block = section.group(1)
    pairs = re.findall(r"Q\.?\s*(.+?)\n\s*A\.?\s*(.+?)(?=\n\s*Q\.|\Z)", block, re.S)
    return [(q.strip().replace("**", ""), a.strip().replace("**", "")) for q, a in pairs]  # 스키마 텍스트에 ** 제외


def build_faq_schema(body_raw: str) -> str:
    """구글 AI Overviews·리치 스니펫용 FAQPage 구조화 데이터(JSON-LD)를 생성합니다.
    워드프레스/티스토리의 <head> 또는 커스텀 HTML 블록에 붙여넣는 용도입니다."""
    pairs = _extract_faq_pairs(body_raw)
    if not pairs:
        return ""
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in pairs
        ],
    }
    return f'<script type="application/ld+json">\n{json.dumps(data, ensure_ascii=False, indent=2)}\n</script>'


def build_article_schema(title: str, keyword: str) -> str:
    """구글용 Article 구조화 데이터(JSON-LD)를 생성합니다.
    날짜는 최종본을 만드는 날로 임시로 넣어 두고, 워드프레스 발행 때(wp_publisher.create_post_via_rest)
    실제 공개일(예약일 또는 오늘)로 datePublished·dateModified를 다시 맞춥니다."""
    today = datetime.date.today().isoformat()
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "keywords": keyword,
        "datePublished": today,
        "dateModified": today,
    }
    return f'<script type="application/ld+json">\n{json.dumps(data, ensure_ascii=False, indent=2)}\n</script>'


TOC_COLLAPSE_THRESHOLD = 8  # 소제목이 이 개수 이상이면 목차를 접고 펼칠 수 있는 <details>로 만듦 (처음 상태는 TOC_DEFAULT_OPEN이 결정)
TOC_DEFAULT_OPEN = True  # True: <details open>으로 처음부터 펼침(눌러서 접기) / False: 접힌 채로 시작(눌러서 펼치기)


def build_toc(html: str) -> tuple[str, str]:
    """<h2> 태그들에 id를 부여하고, 그 id로 연결되는 목차(TOC) HTML을 만듭니다.
    반환값: (목차 HTML, id가 부여된 본문 HTML). 소제목이 3개 미만이면 목차를 생략합니다.
    소제목이 TOC_COLLAPSE_THRESHOLD개 이상이면(글쓰기 규칙이 8~12개로 늘어난 뒤부터 흔해짐),
    목차가 첫 화면 절반을 차지하지 않도록 <details>로 접어서 보여줍니다 — 테마 CSS 없이도
    브라우저 기본 동작만으로 펼치고 접을 수 있는 표준 HTML 태그라 어느 워드프레스 테마에서도
    동작합니다."""
    heading_pattern = re.compile(r'<h2(?:\s+style="[^"]*")?>(.*?)</h2>')
    headings = heading_pattern.findall(html)
    if len(headings) < 3:
        return "", html

    new_html = html
    items = []
    for i, h in enumerate(headings):
        anchor = f"toc-{i}"
        items.append(f'<li><a href="#{anchor}">{h}</a></li>')
        new_html = re.sub(
            rf'<h2((?:\s+style="[^"]*")?)>{re.escape(h)}</h2>',
            rf'<h2\1 id="{anchor}">{h}</h2>',
            new_html,
            count=1,
        )

    list_html = "<ul>" + "".join(items) + "</ul>"
    if len(headings) >= TOC_COLLAPSE_THRESHOLD:
        open_attr = " open" if TOC_DEFAULT_OPEN else ""
        toggle_hint = "눌러서 접기" if TOC_DEFAULT_OPEN else "눌러서 펼치기"
        toc_html = (
            f'<details{open_attr} style="background:#f5f5f5; border-radius:8px; padding:16px 20px; margin:20px 0; font-size:14px;">'
            f"<summary style=\"cursor:pointer; font-weight:700;\">이 글의 목차 ({len(headings)}개 — {toggle_hint})</summary>"
            f'<div style="margin-top:10px;">{list_html}</div>'
            "</details>"
        )
    else:
        toc_html = (
            '<div style="background:#f5f5f5; border-radius:8px; padding:16px 20px; margin:20px 0; font-size:14px;">'
            f"<strong>이 글의 목차</strong>{list_html}</div>"
        )
    return toc_html, new_html


def extract_naver_tags(body_raw: str, keyword: str, max_tags: int = 10) -> list[str]:
    """네이버 블로그 하단 '태그' 입력란에 넣을 키워드 목록을 소제목 기반으로 추출합니다."""
    headings = extract_section_headings(body_raw, min_count=0, max_count=10)
    candidates = [keyword] + [h for h in headings if h and h != "핵심 내용"]
    seen = set()
    result = []
    for t in candidates:
        t = t.strip()
        if t and t not in seen and len(t) <= 12:
            seen.add(t)
            result.append(t)
    return result[:max_tags]


def extract_section_headings(body_raw: str, min_count: int = 2, max_count: int = 3) -> list[str]:
    """본문의 ## 소제목들을 추출합니다. 이미지 생성 시 섹션별 프롬프트로 사용됩니다."""
    headings = re.findall(r"^##\s+(.+)$", body_raw, flags=re.MULTILINE)
    headings = [h.strip().replace("**", "") for h in headings]
    if len(headings) < min_count:
        headings = headings + ["핵심 내용"] * (min_count - len(headings))
    return headings[:max_count]
