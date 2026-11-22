"""
숍(쿠팡파트너스·네이버 쇼핑커넥트) 콘텐츠 전용 포맷터.

정책/제도 콘텐츠(seo_formatter.py)와는 다른 레이아웃이 필요합니다 — 목차 대신, 상품별로
"이미지 → 가격 → 설명 → 구매 링크"가 한 덩어리로 붙어있는 카드 형태가 핵심입니다. FAQ는
지원합니다("## 자주 묻는 질문" + Q:/A: 형식) — 구매 전 흔한 걱정거리를 풀어주는 역할과
검색결과 FAQ 리치 스니펫 역할을 같이 합니다.

글쓰기 클로드가 만드는 원문은 이 구조를 기대합니다:

## 상품명
가격: 89,000원
링크: [구매링크]
상품 설명 문단...

## 자주 묻는 질문
Q: 질문 내용
A: 답변 내용
Q: 질문 내용
A: 답변 내용

각 ## 섹션 시작부에 "가격:"·"링크:" 줄이 있으면 "상품 카드"로, 소제목에 "FAQ"나 "자주 묻는
질문"이 들어있으면 FAQ 섹션으로, 그 외에는(인트로 다음의 "총평"처럼) 일반 섹션으로
렌더링합니다. 링크는 발행 전에는 보통 모르니 "[구매링크]" 같은 자리표시자로 남겨두면 되고,
나중에 실제 제휴 링크로 바꿔 끼우면 됩니다.

워드프레스(쿠팡파트너스)와 네이버(쇼핑커넥트)는 구매 유도 방식을 다르게 렌더링합니다.
네이버 블로그 에디터는 커스텀 스타일의 큰 버튼이나 박스를 붙여넣으면 깨지는 경우가 많아서
(실제 테스트로 확인됨), 네이버는 굵게+색깔 텍스트와 실제 줄바꿈만으로 최대한 단순하게
구성합니다.
"""
import html
import itertools
import json
import os
import random
import re
from contextvars import ContextVar
from html.parser import HTMLParser
from urllib.parse import urlparse

from md_blocks import ensure_blank_lines_around_blocks

# 공정거래위원회 "추천·보증 등에 관한 표시·광고 심사지침"에 따른 법정 고지 문구입니다.
# 플랫폼마다 프로그램 이름이 다르므로 문구도 다릅니다. 표지 바로 아래(광고성 링크가
# 나오기 전)에 항상 넣습니다.
DISCLOSURE_TEXT = {
    "wordpress": "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
    "naver": "이 포스팅은 네이버 쇼핑커넥트 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
}
# 네이버는 두 줄(쉼표·💡 없음)로 나눠서 보여 줍니다 (_disclosure_html 참고).
DISCLOSURE_NAVER_LINES = (
    "이 포스팅은 네이버 쇼핑커넥트 활동의 일환으로",
    "이에 따른 일정액의 수수료를 제공받습니다",
)
# 워드프레스(워드프레스 SHOP·워드프레스 일반)도 두 줄(쉼표·💡 없음)로 보여 줍니다 (_wp_disclosure_block 참고).
DISCLOSURE_WORDPRESS_LINES = (
    "이 포스팅은 쿠팡 파트너스 활동의 일환으로",
    "이에 따른 일정액의 수수료를 제공받습니다",
)

# 구매 유도 버튼 문구 — 리서치로 찾은 가격이 실제와 자주 달라서 글쓰기 프롬프트에서 가격을
# 빼기로 했습니다("최저가"라고 해놓고 정작 가격이 안 보이면 어색하므로 "오늘 가격 보기"로
# 순화). 한 곳(여기)만 바꾸면 워드프레스·네이버 전체에 반영됩니다.
CTA_BUTTON_TEXT = {
    "wordpress": "🛒 쿠팡에서 오늘 가격 보기",
    "naver": "→ 오늘 가격 보기",
}


def _disclosure_html(platform: str) -> str:
    text = DISCLOSURE_TEXT.get(platform, DISCLOSURE_TEXT["wordpress"])
    if platform == "naver":
        # 본문과 같은 크기·색, 굵게 없음(규정: 본문보다 작거나 식별이 어렵지 않게). 네이버 에디터가 테두리·박스를
        # 지우므로 박스는 쓰지 않고 <p> 가운데 정렬만 씁니다. 두 줄(쉼표·💡 없음). 문구 아래 빈 줄 1개(NAVER_GAP)도 같이 붙입니다.
        line1, line2 = DISCLOSURE_NAVER_LINES
        return (
            f'<p style="text-align:center;">{line1}<br>{line2}</p>'
            f"{NAVER_GAP}"
        )
    return (
        '<div style="background:#fff7e6; border:1px solid #f0d9a8; border-radius:8px; '
        'padding:14px 18px; margin:16px 0 28px; font-size:14px; color:#7a5c1e; line-height:1.6;">'
        f"💡 {text}</div>"
    )


# ── 네이버 소제목 스타일 (네이버 SHOP 전용 — 워드프레스 SHOP·롱텀에는 쓰이지 않음) ────────────────
# 네이버 스마트에디터는 붙여넣기 때 border·div 박스·<h2>·<hr>을 지우므로, 모양을 "글자 + 인라인 color/font-size/bold"로만 만듭니다.
#   "text"  : ❝ / 소제목(굵게 19px) / ──── 글자 선 / 빈 줄 을 <p> + 인라인 스타일로 만듭니다 (기본값)
#   "quote" : 소제목을 <blockquote>로 감쌉니다 — 네이버 에디터가 붙여넣을 때 자체 "인용구" 컴포넌트로 바꿔주는지 확인용
NAVER_HEADING_MODE = "text"
NAVER_HEADING_SPLIT_LEN = 20      # 소제목이 이 글자 수를 넘으면 쉼표·물음표 기준으로 2줄로 나눔
NAVER_HEADING_LINE_CHARS = 40     # 글자 선(─)의 길이. PC에서 한 줄을 채우는 정도이며, 모바일에서는 줄이 꺾일 수 있어 필요하면 줄이세요
NAVER_RELATED_MAX = 4             # 하단 "같이 보면 좋은 글" 링크 최대 개수
# 네이버 블록(문단·목록·표·소제목) 사이 "빈 줄 1개". 확인해보니 여기서 "<br><br>"를 쓰면 앞뒤로
# </p>가 붙어 있는 상태에서 붙여넣었을 때 빈 줄이 2개가 됩니다 — <br> 2개가 그대로 살아남는 데다
# 문단 경계 자체도 한 줄을 차지하기 때문입니다. 비어있는 문단(<p><br></p>) 하나로 통일하면 1개가 됩니다.
NAVER_GAP = "<p><br></p>"

PRICE_LINE_RE = re.compile(r"^[ \t]*가격[ \t]*[:：][ \t]*(.+)$", re.MULTILINE)
LINK_LINE_RE = re.compile(r"^[ \t]*링크[ \t]*[:：][ \t]*(.+)$", re.MULTILINE)
# 워드프레스 일반: 이 주소로 시작하는 "링크:" 줄이 있는 섹션만 구매 버튼(상품)으로 만듭니다.
COUPANG_LINK_PREFIX = "https://link.coupang.com/"

# ── 워드프레스 SHOP: 상품 끝 "배너 + 쿠팡 HTML 코드" ─────────────────────────────────────
# "링크:" 값에 쿠팡파트너스 HTML 코드(<a><img></a> 또는 <iframe>)가 붙어 올 수 있습니다. 코드가 여러 줄로
# 붙여넣어져도 하나의 값으로 보고, 쿠팡 도메인만 허용합니다(그 밖의 태그·도메인이 섞이면 넣지 않고 경고).
CTA_ALLOWED_DOMAINS = ("link.coupang.com", "coupa.ng", "coupang.com", "coupangcdn.com")
CTA_BANNER_LINK_DOMAINS = ("link.coupang.com", "coupa.ng")  # 배너를 클릭했을 때 이동할 수 있는 쿠팡 링크 도메인
CTA_BANNER_ALT = "쿠팡에서 가격보기"
# 줄머리 "링크:" 바로 뒤에서 시작해 </a> 또는 </iframe>까지(다음 "## 소제목"을 넘지 않는 범위)를 한 덩어리로 봅니다.
_LINK_HTML_BLOCK_RE = re.compile(
    r"^([ \t]*링크[ \t]*[:：][ \t]*)(<(a|iframe)\b(?:(?!\n[ \t]*#{1,6}[ \t]).){0,6000}?</\3[ \t]*>)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
QA_PAIR_RE = re.compile(r"^[ \t]*Q[ \t]*[:：][ \t]*(.+?)\s*\n[ \t]*A[ \t]*[:：][ \t]*(.+?)(?=\n[ \t]*Q[ \t]*[:：]|\Z)", re.MULTILINE | re.DOTALL)
_Q_LINE_RE = re.compile(r"^[ \t]*Q[ \t]*[:：][ \t]*(.+)$")
_A_LINE_RE = re.compile(r"^[ \t]*A[ \t]*[:：][ \t]*(.+)$")
# 내용이 있는 줄 바로 다음 줄이 "Q:"로 시작하면(사이에 빈 줄 없음) 그 줄바꿈(\1)을 잡습니다.
_Q_LINE_NO_BLANK_RE = re.compile(r"(?<=\S)([ \t]*\r?\n)(?=[ \t]*Q[ \t]*[:：])")


def _parse_faq_body(body: str) -> tuple[list[tuple[str, str]], str]:
    """
    FAQ 섹션 본문에서 Q/A 쌍과, 그 뒤에 남는 "마무리 문단"을 분리합니다.
    (예전 QA_PAIR_RE는 마지막 답변을 "다음 Q 또는 문서 끝까지" 통째로 잡아서, FAQ가 마지막
    섹션일 때 마무리 문단·"태그:" 줄까지 전부 마지막 답변에 합쳐지는 문제가 있었습니다.)

    빈 줄 기준으로 문단(block)을 나눈 뒤, 각 Q의 답변은 "실제로 뒤에 Q가 더 남아있는 동안"만
    이어지는 문단들을 계속 받습니다(정상적인 여러 문단 답변 지원). 뒤에 더 이상 Q가 없는데도
    빈 줄 다음에 Q가 아닌 문단이 나오면, 그 문단부터는 답변이 아니라 FAQ 밖의 일반 마무리
    문단으로 보고 분리합니다. Q가 하나도 없으면 전체를 마무리 문단으로 돌려줍니다(qa_pairs=[]).
    """
    # Q:/A: 쌍 사이에 빈 줄이 없는 원고도 Q마다 한 쌍이 되도록, "Q:" 줄 앞에 빈 줄이 없으면 넣어 줍니다.
    body = _Q_LINE_NO_BLANK_RE.sub(r"\1\n", body or "")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
    is_q = [bool(_Q_LINE_RE.match(b.splitlines()[0])) for b in blocks]
    if not any(is_q):
        return [], (body or "").strip()

    n = len(blocks)
    has_q_after = [False] * (n + 1)
    for i in range(n - 1, -1, -1):
        has_q_after[i] = is_q[i] or has_q_after[i + 1]

    pairs: list[tuple[str, str]] = []
    i = 0
    while i < n and not is_q[i]:  # 첫 Q 앞에 엉뚱한 내용이 있으면(거의 없음) 건너뜀
        i += 1
    while i < n:
        lines = blocks[i].splitlines()
        q_text = _Q_LINE_RE.match(lines[0]).group(1).strip()
        first_lines = []
        for l in lines[1:]:
            am = _A_LINE_RE.match(l)
            first_lines.append(am.group(1).strip() if am else l.strip())
        answer_paragraphs = ["\n".join(first_lines).strip()] if first_lines else []
        j = i + 1
        while j < n and not is_q[j] and has_q_after[j]:
            answer_paragraphs.append(blocks[j])
            j += 1
        pairs.append((q_text, "\n\n".join(p for p in answer_paragraphs if p)))
        i = j
        if i < n and not is_q[i]:
            # has_q_after[i]가 False라서 멈춘 것 — 더 이상 Q가 없으므로 나머지는 전부 마무리 문단
            return pairs, "\n\n".join(blocks[i:])
    return pairs, ""


def _faq_answer_paragraphs(answer: str) -> list[str]:
    """FAQ 답변 문자열(문단 사이는 빈 줄)을 문단 목록으로 나눕니다. 보통은 문단 1개뿐입니다."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", answer or "") if p.strip()]
    return paragraphs or [(answer or "").strip()]

# 본문 안의 금액·퍼센트를 굵게+색깔로 강조합니다 (seo_formatter.py의 정책 콘텐츠용 강조와
# 같은 방식). 배경색·테두리처럼 "박스형" 스타일은 네이버 에디터가 잘 지우는 반면, 이런
# 단순한 굵게+색깔 텍스트는 훨씬 잘 살아남습니다.
# 금액은 "숫자 + 원" 하나가 아니라 "69만 8,500원", "74만 9천 원", "1억 2,000만원", "2.35만원",
# "160만~251만원대"처럼 단위(억·만·천·백)와 여러 조각으로 나뉘어 나옵니다. 예전 패턴은 마지막
# "숫자+원"만 잡아서 앞부분이 잘렸습니다 — 조각들을 이어서 "원"(과 뒤의 "대")까지 통째로 잡습니다.
# 앞에 숫자·쉼표·점이 붙어 있으면(=숫자 중간) 시작하지 않고, "1원칙" 같은 단어는 제외합니다.
_AMOUNT = r"\d[\d,]*(?:\.\d+)?"
_PART = rf"{_AMOUNT}(?:\s?[억만천백])?"
_SEQ = rf"{_PART}(?:\s?{_PART})*"
MONEY_PATTERN = re.compile(rf"(?<![\d,.])({_SEQ}(?:\s?[~～\-–]\s?{_SEQ})?\s?원대?)(?!칙|본|래|인|룸)")
# 퍼센트는 "40~60%", "2~3 %", "10-15%"처럼 범위(물결 ~·～, 하이픈 -, 대시 –)도 한 덩어리로 강조합니다 (예전엔 뒤의 60%만 강조됐음).
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?(?:\s?[~～\-–]\s?\d+(?:\.\d+)?)?\s?%)")


# ── 글쓰기 프롬프트의 강조 마크업 ──────────────────────────────────────────────
#   **강조할 문구**              → 문장 안 일부 강조 (보라 글씨 + 노란 형광펜 배경)
#   "> 문장" (앞뒤 빈 줄, 한 줄)  → 전환점 인용구 (청록 큰 글씨)
# 색은 기존 팔레트(금액 빨강 #dc2626, 퍼센트 파랑 #2563eb, 상품·가격 주황 #c8460a/#e8622c, 구매 링크 초록
# #03c75a)와 겹치지 않게 보라·청록을 씁니다.
EMPHASIS_TEXT, EMPHASIS_BG = "#6d28d9", "#fef08a"
QUOTE_TEXT, QUOTE_BG = "#0f766e", "#ccfbf1"
# 네이버 스마트에디터가 background-color 인라인 스타일을 붙여넣기 후에도 유지하는지 아직 확인 전이라 두
# 버전을 만들어 둡니다.  True = (a) 글씨색 + 배경색 + 굵게 / False = (b) 글씨색 + 굵게만.
# 붙여넣어 봤을 때 배경이 지워지면 False로 바꾸고 "플랫폼별 콘텐츠 생성"을 다시 누르면 됩니다.
NAVER_EMPHASIS_BACKGROUND = True

BOLD_PATTERN = re.compile(r"\*\*([^*\n]+?)\*\*")

# 원고의 금액·퍼센트·날짜를 자동으로 굵게 + 색(금액 빨강·퍼센트 파랑·날짜 초록)으로 칠할지 정하는 스위치입니다.
# False(기본) = 숫자는 아무 태그 없이 일반 글자로 두고, 원고에서 **로 표시한 강조만 색이 들어갑니다.
# True = 예전처럼 자동 색칠. 이 파일과 seo_formatter.py(혜택주머니)가 같은 값을 씁니다.
AUTO_NUMBER_COLOR = False


def strip_markup(text: str) -> str:
    """소제목·가격·구조화 데이터처럼 스타일을 입힐 수 없는 곳에서 **와 줄머리 '> '를 제거합니다."""
    return re.sub(r"^\s*>\s?", "", (text or "").replace("**", ""))


def _split_heading_lines(text: str) -> list[str]:
    """소제목이 NAVER_HEADING_SPLIT_LEN자를 넘으면 쉼표·물음표 기준으로 (가운데에 가까운 곳에서) 2줄로 나눕니다.
    구분 기호는 앞 줄 끝에 남깁니다. 기준이 없으면 공백, 그것도 없으면 한 줄 그대로 둡니다."""
    text = text.strip()
    if len(text) <= NAVER_HEADING_SPLIT_LEN:
        return [text]
    mid = len(text) / 2
    for pattern in (r"[,，?？]", r"\s"):
        cuts = [m.end() for m in re.finditer(pattern, text) if 0 < m.end() < len(text) and text[m.end():].strip()]
        if cuts:
            cut = min(cuts, key=lambda c: abs(c - mid))
            first, second = text[:cut].strip(), text[cut:].strip()
            if first and second:
                return [first, second]
    return [text]


def _naver_heading_html(text: str) -> str:
    """네이버용 소제목 (상품·일반·FAQ·총평·비교·관련글 전부 동일). NAVER_HEADING_MODE로 두 방식 중 고릅니다."""
    lines = _split_heading_lines(strip_markup(text))
    if NAVER_HEADING_MODE == "quote":
        inner = "<br>".join(lines)
        return f'<blockquote><strong style="color:#111111; font-size:19px;">{inner}</strong></blockquote>{NAVER_GAP}'
    parts = ['<p style="text-align:left;"><strong style="color:#aaaaaa; font-size:26px; font-weight:bold;">❝</strong></p>']
    parts += [
        f'<p style="text-align:left;"><strong style="color:#111111; font-size:19px; font-weight:bold;">{line}</strong></p>'
        for line in lines
    ]
    parts.append(f'<p style="text-align:left;"><span style="color:#cccccc;">{"─" * NAVER_HEADING_LINE_CHARS}</span></p>')
    parts.append(NAVER_GAP)
    return "".join(parts)


_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def parse_markdown_table(body: str):
    """본문에서 첫 마크다운 표를 찾아 (표 앞 텍스트, 행 목록, 표 뒤 텍스트)를 돌려줍니다. 표가 없으면 None. 첫 행이 머리글입니다."""
    lines = (body or "").splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("|")), None)
    if start is None:
        return None
    end = start
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    rows = []
    for l in (x.strip() for x in lines[start:end]):
        if _TABLE_SEP_RE.match(l):
            continue
        rows.append([c.strip() for c in l.strip("|").split("|")])
    if len(rows) < 2:
        return None
    return "\n".join(lines[:start]).strip(), rows, "\n".join(lines[end:]).strip()


def parse_markdown_tables(body: str):
    """본문 안의 마크다운 표를 전부 찾아 "글 → 표 → 글 → 표 → 글" 순서의 조각 목록으로 돌려줍니다.
    조각은 ("text", 글자) 또는 ("table", 행 목록)이고, 글 조각은 앞뒤 공백을 뺀 값이며 비어 있으면 넣지 않습니다.
    표가 하나도 없으면 None. 표 한 개짜리 글의 앞/뒤 글은 parse_markdown_table과 똑같이 잘립니다.
    (행이 2줄 미만인 '|' 줄 덩어리는 표가 아니라 글로 남깁니다.)"""
    lines = (body or "").splitlines()
    segments: list[tuple[str, object]] = []
    text_start = 0
    found = False
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        rows = []
        for l in (x.strip() for x in lines[i:j]):
            if _TABLE_SEP_RE.match(l):
                continue
            rows.append([c.strip() for c in l.strip("|").split("|")])
        if len(rows) >= 2:
            text = "\n".join(lines[text_start:i]).strip()
            if text:
                segments.append(("text", text))
            segments.append(("table", rows))
            text_start = j
            found = True
        i = j
    if not found:
        return None
    tail = "\n".join(lines[text_start:]).strip()
    if tail:
        segments.append(("text", tail))
    return segments


def _table_segments(table) -> list[tuple[str, object]]:
    """section["table"] 값을 조각 목록으로 맞춥니다. 예전 형식 (앞 글, 행, 뒤 글) 튜플도 받아 줍니다."""
    if not table:
        return []
    if isinstance(table, tuple) and len(table) == 3 and not isinstance(table[0], tuple):
        before, rows, after = table
        segs = []
        if before:
            segs.append(("text", before))
        segs.append(("table", rows))
        if after:
            segs.append(("text", after))
        return segs
    return list(table)


# ── 표 모양 (전 채널 공통: 네이버·싼세계·혜택주머니·생활레이더·OLD) ─────────────────────────────────
# 색을 바꾸려면 아래 두 줄만 고치면 됩니다. 머리줄=진회색 바탕 + 흰 글씨 + 굵게, 첫 열=굵게, 모든 칸 가운데 정렬.
TABLE_HEAD_BG = "#555555"
TABLE_HEAD_COLOR = "#FFFFFF"
TABLE_CELL_ALIGN = "center"
TABLE_CELL_VALIGN = "middle"
TABLE_CELL_PADDING = "8px"
TABLE_CELL_STYLE = f"padding:{TABLE_CELL_PADDING}; text-align:{TABLE_CELL_ALIGN}; vertical-align:{TABLE_CELL_VALIGN};"
TABLE_HEAD_CELL_STYLE = f"{TABLE_CELL_STYLE} background-color:{TABLE_HEAD_BG}; color:{TABLE_HEAD_COLOR}; font-weight:bold;"
TABLE_FIRST_COL_STYLE = f"{TABLE_CELL_STYLE} font-weight:bold;"


def _table_html(rows: list[list[str]], platform: str = "naver") -> str:
    """<table border="1">에 셀 스타일을 인라인으로 입힌 표 (위의 표 모양 상수 참고).
    머리줄(th)은 진회색 바탕·흰 글씨·굵게, 본문 줄의 첫 칸(td)은 굵게, 모든 칸은 가운데 정렬입니다.
    네이버 에디터가 붙여넣을 때 style을 지울 수 있어서, 네이버용 th에는 bgcolor 속성도 같이 넣습니다.
    머리줄 글자는 바탕이 어두우므로 **굵게** 표시만 벗기고 금액·퍼센트 색 강조는 하지 않습니다(흰 글씨 유지).
    워드프레스 발행 없이 클립보드 붙여넣기(티스토리 등)로 나가는 경로에서도 그대로 씁니다 —
    표준 태그라 어디에 붙여넣어도 표 모양은 유지됩니다."""

    head_attrs = f' bgcolor="{TABLE_HEAD_BG}"' if platform == "naver" else ""

    def head_cell(text):
        plain = BOLD_PATTERN.sub(lambda m: m.group(1), text)
        return f'<th{head_attrs} style="{TABLE_HEAD_CELL_STYLE}">{plain}</th>'

    def body_cell(text, first):
        style = TABLE_FIRST_COL_STYLE if first else TABLE_CELL_STYLE
        return f'<td style="{style}">{_emphasize(text, platform)}</td>'

    head, body = rows[0], rows[1:]
    out = ['<table border="1" cellspacing="0" cellpadding="8" style="border-collapse:collapse;">']
    out.append("<tr>" + "".join(head_cell(c) for c in head) + "</tr>")
    for r in body:
        r = (r + [""] * len(head))[: len(head)]
        out.append("<tr>" + "".join(body_cell(c, i == 0) for i, c in enumerate(r)) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _wp_table_block(rows: list[list[str]]) -> str:
    """마크다운 표를 워드프레스 블록으로 만듭니다. 스타일(색·정렬)이 들어간 표를 core/table 블록에 넣으면
    워드프레스 편집기가 "블록에 예상치 못한 콘텐츠가 있습니다" 경고를 낼 수 있습니다 — core/table은 셀의
    style 속성을 저장하지 않아서, 저장된 HTML과 블록이 다시 만든 HTML이 달라 보이기 때문입니다.
    그래서 스타일이 든 표를 core/html 블록(<!-- wp:html -->)으로 감쌉니다(편집기에서는 'HTML 블록'으로 보이고
    경고가 없습니다). <figure class="wp-block-table">는 그대로 유지해서 테마의 표 기본 모양도 받습니다."""
    return _wp_block("html", f'<figure class="wp-block-table">{_table_html(rows, "wordpress")}</figure>')


TAG_LINE_RE = re.compile(r"^[ \t]*태그[ \t]*[:：][ \t]*(.+)$", re.MULTILINE)


def split_tag_line(raw: str) -> tuple[str, list[str]]:
    """원고 끝의 "태그: #태그1 #태그2 …" 줄을 본문에서 빼고, 태그 목록(# 없이)을 함께 돌려줍니다. 줄이 없으면 (원문 그대로, []).
    \\r\\n(윈도우 줄바꿈)은 먼저 \\n으로 통일해서 찾습니다 — 그래야 태그 줄이 파일의 마지막 줄이 아니어도 정규식의
    줄 끝(다음 줄 시작 전)을 정확히 잡습니다."""
    raw = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    matches = list(TAG_LINE_RE.finditer(raw))
    if not matches:
        return raw, []
    last = matches[-1]
    value = last.group(1)
    tags = re.findall(r"#([^\s#,]+)", value) or [t.strip() for t in re.split(r"[,，]", value) if t.strip()]
    cleaned = (raw[: last.start()] + raw[last.end():]).rstrip() + "\n"
    return cleaned, tags


# ── "내 숍 추천 글" 연결 표시 {{SHOP:키워드|안내 문구}} (네이버 일반·워드프레스 일반 전용) ───────────────
# 한 줄 전체가 {{SHOP:…}}인 줄만 인식합니다. build_shop_html(shop_links=…)로 "키워드 → 찾은 글"을 넘겼을 때만 켜지며
# (안 넘기면 이 문법은 아무 처리도 하지 않습니다 — 다른 채널 동작은 그대로).
# 최종본에는 눈에 안 보이는 주석 표시(<!--SHOP_FOUND:…-->, <!--SHOP_MISSING:…-->)가 같이 들어가서, 최종본 화면이
# "연결 결과" 표·경고를 그릴 수 있게 합니다. 발행·복사되는 사본은 strip_shop_markers()로 이 표시를 벗기고,
# 못 찾은 줄은 통째로 지웁니다.
SHOP_LINE_RE = re.compile(r"^[ \t]*\{\{SHOP:(.*?)\}\}[ \t]*$", re.MULTILINE)
SHOP_DEFAULT_PHRASE = "관련 제품 추천 글 보기"
SHOP_BOX_BG = "#f3f4f6"
SHOP_BOX_BORDER = "#e5e7eb"
_shop_links_ctx: ContextVar = ContextVar("shop_links", default=None)
_SHOP_MARKER_RE = re.compile(r"<!--SHOP_(FOUND|MISSING):(.*?)-->", re.DOTALL)
_SHOP_MISSING_STRIP_RE = re.compile(r"(?:<p><br></p>)?<!--SHOP_MISSING:.*?-->\n?", re.DOTALL)
_SHOP_FOUND_STRIP_RE = re.compile(r"<!--SHOP_FOUND:.*?-->\n?", re.DOTALL)


def parse_shop_line(inner: str) -> tuple[str, str]:
    """{{SHOP: 여기 }} 안쪽 → (키워드, 안내 문구). "|문구"가 없거나 비어 있으면 기본 문구."""
    keyword, _, phrase = inner.partition("|")
    return keyword.strip(), phrase.strip() or SHOP_DEFAULT_PHRASE


def find_shop_keywords(raw: str) -> list[str]:
    """원고에서 {{SHOP:…}} 줄의 키워드를 나온 순서대로 (중복 제거)."""
    return list(dict.fromkeys(parse_shop_line(m.group(1))[0] for m in SHOP_LINE_RE.finditer(raw or "")))


def _shop_marker(kind: str, payload: dict) -> str:
    # 주석 안에서 "--"·">"가 깨지지 않도록 JSON을 \u 이스케이프한 형태(ensure_ascii)로 넣습니다.
    text = json.dumps(payload, ensure_ascii=True).replace("-", "\\u002d").replace(">", "\\u003e")
    return f"<!--SHOP_{kind}:{text}-->"


def extract_shop_results(content: str) -> list[dict]:
    """최종본에 든 표시에서 연결 결과를 읽습니다: [{"keyword","found":bool,"title","url","rule"}]"""
    out = []
    for m in _SHOP_MARKER_RE.finditer(content or ""):
        try:
            data = json.loads(m.group(2))
        except ValueError:
            continue
        out.append(
            {
                "keyword": data.get("keyword", ""),
                "found": m.group(1) == "FOUND",
                "title": data.get("title", ""),
                "url": data.get("url", ""),
                "rule": data.get("rule", ""),
            }
        )
    return out


def strip_shop_markers(content: str) -> str:
    """발행·복사용 사본: 못 찾은 {{SHOP:}} 줄(표시)은 통째로 지우고(앞에 붙은 네이버 빈 줄 포함), 찾은 줄의 표시 주석도 벗깁니다."""
    if "<!--SHOP_" not in (content or ""):
        return content
    return _SHOP_FOUND_STRIP_RE.sub("", _SHOP_MISSING_STRIP_RE.sub("", content))


def _shop_link_naver(url: str, phrase: str) -> str:
    """네이버: 연한 배경 박스 안에 "👉 문구" 한 줄(문구 전체가 링크), 왼쪽 정렬. 스마트에디터가 박스(<p> 배경·여백)를 지워도
    글자 배경(span background-color)은 기존 강조처럼 남는 단순 구조입니다."""
    href, label = html.escape(url, quote=True), html.escape(phrase, quote=False)
    return (
        f'<p style="{QUOTE_ALIGN_STYLE} background-color:{SHOP_BOX_BG}; border:1px solid {SHOP_BOX_BORDER}; '
        f'border-radius:8px; padding:12px 16px; margin:8px 0;">'
        f'<span style="background-color:{SHOP_BOX_BG};">👉 <a href="{href}" target="_blank">'
        f'<strong>{label}</strong></a></span></p>'
    )


def _shop_link_wp(url: str, phrase: str) -> str:
    """워드프레스: 연한 회색 박스 + "👉 문구 →" 링크. 같은 창에서 열림. 박스 모양은 <p>의 인라인 스타일이라
    core/paragraph 블록에 넣으면 블록 검증이 깨지므로 wp:html 블록(표와 같은 방식)으로 감쌉니다."""
    href, label = html.escape(url, quote=True), html.escape(phrase, quote=False)
    return _wp_block(
        "html",
        f'<p style="{QUOTE_ALIGN_STYLE} color:#111111; background-color:{SHOP_BOX_BG}; border:1px solid {SHOP_BOX_BORDER}; '
        f'border-radius:8px; padding:14px 18px; margin:12px 0;">👉 <a href="{href}">{label} →</a></p>',
    )


def _shop_block(kind_inner: str, platform: str) -> str:
    """("shop", 키워드|문구) 블록 하나를 해당 플랫폼 HTML로. 못 찾았으면 본문엔 아무것도 안 넣고 표시 주석만 남깁니다."""
    links = _shop_links_ctx.get() or {}
    keyword, phrase = parse_shop_line(kind_inner)
    found = links.get(keyword)
    if not found or not found.get("url"):
        return _shop_marker("MISSING", {"keyword": keyword})
    marker = _shop_marker(
        "FOUND", {"keyword": keyword, "title": found.get("title", ""), "url": found["url"], "rule": found.get("rule", "")}
    )
    body = _shop_link_wp(found["url"], phrase) if platform == "wordpress" else _shop_link_naver(found["url"], phrase)
    return marker + body


def _related_section_html(posts: list[dict]) -> str:
    """하단 "같이 보면 좋은 글": 소제목 + "👉 링크" 한 줄씩. posts = [{"title","url"}]. 없으면 빈 문자열."""
    if not posts:
        return ""
    lines = "<br>".join(f'👉 <a href="{p["url"]}" target="_blank">{p["title"]}</a>' for p in posts)
    return _naver_heading_html("같이 보면 좋은 글") + lines + NAVER_GAP


def _emphasis_tag(inner: str, platform: str) -> str:
    if platform == "naver":
        bg = f" background-color:{EMPHASIS_BG};" if NAVER_EMPHASIS_BACKGROUND else ""
        return f'<strong style="color:{EMPHASIS_TEXT};{bg}">{inner}</strong>'
    return (
        f'<strong style="color:{EMPHASIS_TEXT}; background-color:{EMPHASIS_BG}; '
        f'padding:0 3px; border-radius:3px;">{inner}</strong>'
    )


def _emphasize(text: str, platform: str = "wordpress") -> str:
    if AUTO_NUMBER_COLOR:
        text = MONEY_PATTERN.sub(r'<strong style="color:#dc2626;">\1</strong>', text)
        text = PERCENT_PATTERN.sub(r'<strong style="color:#2563eb;">\1</strong>', text)
    # 금액·퍼센트 강조 뒤에 적용해서, "**최대 235만원**"처럼 겹쳐도 안쪽 빨강과 바깥 형광펜이 둘 다 살아납니다.
    text = BOLD_PATTERN.sub(lambda m: _emphasis_tag(m.group(1), platform), text)
    return text


_UL_ITEM_RE = re.compile(r"^[-*][ \t]+(.+)$")
_OL_ITEM_RE = re.compile(r"^\d+[.)][ \t]+(.+)$")


# "### 소제목"·"#### 소제목" (작은 소제목). "## "(큰 소제목)은 parse_sections가 섹션으로 나누므로 여기엔 오지 않습니다.
_H3_LINE_RE = re.compile(r"^[ \t]*#{3,4}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")


def _h3_text(line: str) -> str | None:
    """작은 소제목 줄이면 ** 와 앞뒤 # 를 뺀 글자를, 아니면 None을 돌려줍니다."""
    m = _H3_LINE_RE.match(line)
    return strip_markup(m.group(1)).strip() if m else None


def _split_h3_block(block: str) -> list[tuple[str, str]]:
    """블록 안에 "### " 줄이 있으면, 그 줄만 따로 떼어 [("h3", 글자) 또는 ("text", 나머지 줄들)] 로 나눕니다.
    빈 줄 없이 다음 문단과 붙어 있어도 소제목 줄만 분리됩니다. "### " 줄이 없으면 [("text", block)] 그대로."""
    lines = block.splitlines()
    if not any(_H3_LINE_RE.match(l) for l in lines):
        return [("text", block)]
    pieces, run = [], []

    def flush():
        text = "\n".join(run).strip()
        if text:
            pieces.append(("text", text))
        run.clear()

    for l in lines:
        h = _h3_text(l)
        if h:
            flush()
            pieces.append(("h3", h))
        else:
            run.append(l)
    flush()
    return pieces


def _drop_h3_lines(text: str) -> str:
    """구조화 데이터(FAQ 스키마)용: "### " 줄을 뺍니다. 그런 줄이 없으면 원문 그대로."""
    lines = (text or "").split("\n")
    if not any(_H3_LINE_RE.match(l) for l in lines):
        return text
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l for l in lines if not _H3_LINE_RE.match(l))).strip()


def _split_paragraphs(text: str) -> list[tuple[str, object]]:
    """
    빈 줄 기준으로 블록을 나누고, 각 블록을 종류별로 분류합니다.
    - 줄 맨 앞이 '### '/'#### '인 줄 → ("h3", 소제목 글자) (같은 블록 안의 다른 줄과 따로 분리)
    - 모든 줄이 '> '로 시작 → ("quote", 인용구 텍스트 한 줄)
    - 모든 줄이 '- '/'* '로 시작 → ("ul", [항목 텍스트, ...])
    - 모든 줄이 '1.'/'1)'로 시작 → ("ol", [항목 텍스트, ...])
    - 그 외 → ("p", 블록 원문)
    인용구·목록 항목 안의 **는 이미 통째로 강조된 것이라 인용구에서는 제거하고, 목록 항목은
    _emphasize가 그대로 처리하도록 남겨둡니다(항목 안에 부분 강조 **가 있을 수 있어서).
    """
    result = []
    pieces = []
    for outer in (b.strip() for b in re.split(r"\n\s*\n", text or "")):
        if outer:
            pieces.extend(_split_h3_block(outer))
    for piece_kind, block in pieces:
        if piece_kind == "h3":
            result.append(("h3", block))
            continue
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        shop = SHOP_LINE_RE.fullmatch(lines[0]) if len(lines) == 1 and _shop_links_ctx.get() is not None else None
        if shop:
            result.append(("shop", shop.group(1)))
        elif all(l.startswith(">") for l in lines):
            result.append(("quote", " ".join(strip_markup(l) for l in lines)))
        elif all(_UL_ITEM_RE.match(l) for l in lines):
            result.append(("ul", [_UL_ITEM_RE.match(l).group(1).strip() for l in lines]))
        elif all(_OL_ITEM_RE.match(l) for l in lines):
            result.append(("ol", [_OL_ITEM_RE.match(l).group(1).strip() for l in lines]))
        else:
            result.append(("p", block))
    return result


def _is_faq_heading(heading: str) -> bool:
    h = heading.replace(" ", "").upper()
    return "FAQ" in h or "자주묻는질문" in h or "자주하는질문" in h


_STRUCT_LINE_RE = re.compile(r"^[ \t]*(?:가격|링크)[ \t]*[:：]")
_QA_LINE_RE = re.compile(r"^[ \t]*[QA][ \t]*[:：]")
_SENTENCE_END_RE = re.compile(r"(?:[.!?…。]|요|다|죠|까|네)$")
_HEADING_MAX_LEN = 45


def _looks_like_heading(line: str) -> bool:
    """문장이 아니라 '이름/제목'처럼 보이는 짧은 한 줄인지 (문장부호·문장 어미로 안 끝나고 45자 이하)."""
    s = line.strip()
    if not s or len(s) > _HEADING_MAX_LEN:
        return False
    if _STRUCT_LINE_RE.match(s) or _QA_LINE_RE.match(s):
        return False
    if s[0] in "#-*•·>":  # 마크다운 소제목·목록·인용은 제외
        return False
    if s.startswith("{{SHOP:"):  # 내 숍 추천 글 연결 줄은 소제목이 아님
        return False
    return not _SENTENCE_END_RE.search(s)


def normalize_raw_headings(raw: str) -> str:
    """
    글쓰기 결과가 규격("## 상품명")을 안 지키고 소제목을 그냥 한 줄로만 적어 오는 경우가 있어서(실제
    글 대부분이 그랬습니다), '## ' 소제목이 하나도 없을 때만 소제목 줄을 찾아 '## '을 붙여줍니다.
    이미 '## '가 하나라도 있으면(=규격대로) 그대로 둡니다.
    소제목으로 보는 줄: (A) 앞이 빈 줄이고 바로 아래에 '가격:' 줄이 오는 한 줄(80자 이하),
    (B) 앞뒤가 빈 줄인 단독 한 줄이고 _looks_like_heading을 만족하는 줄.
    첫 문단(인트로)은 소제목으로 보지 않습니다.
    """
    if not raw or re.search(r"^##[ \t]+", raw, re.MULTILINE):
        return raw
    lines = raw.split("\n")
    out = []
    seen_content = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s and seen_content:
            prev_blank = i == 0 or not lines[i - 1].strip()
            next_blank = i == len(lines) - 1 or not lines[i + 1].strip()
            nxt = next((l for l in lines[i + 1 :] if l.strip()), "")
            is_struct_or_qa = bool(_STRUCT_LINE_RE.match(s) or _QA_LINE_RE.match(s))
            price_below = bool(_STRUCT_LINE_RE.match(nxt)) and not is_struct_or_qa and len(s) <= 80 and s[0] not in "#-*•·>"
            if (prev_blank and price_below) or (prev_blank and next_blank and _looks_like_heading(s)):
                out.append("## " + s)
                continue
        out.append(line)
        if s:
            seen_content = True
    return "\n".join(out)


def collapse_link_html(raw: str) -> str:
    """"링크: <a …>…</a>" / "링크: <iframe …></iframe>" 코드가 여러 줄로 붙여넣어졌으면 한 줄로 합칩니다.
    태그 사이의 줄바꿈만 없애고 속성(href·src 등)은 한 글자도 바꾸지 않습니다. 해당 줄이 없으면 원문 그대로."""

    def _join(m: re.Match) -> str:
        code = re.sub(r">[ \t\r\n]*[\r\n][ \t\r\n]*<", "><", m.group(2))
        code = re.sub(r"[ \t]*[\r\n]+[ \t\r\n]*", " ", code)
        return m.group(1) + code

    return _LINK_HTML_BLOCK_RE.sub(_join, raw or "")


def classify_link_value(link: str) -> str:
    """"링크:" 값의 종류: "html"(쿠팡 코드) / "url"(주소만) / "empty"(비었거나 [쿠팡링크] 같은 자리표시자·주소가 아닌 글)."""
    v = (link or "").strip()
    if re.match(r"<(?:a|iframe)\b", v, re.IGNORECASE):
        return "html"
    if re.match(r"https?://", v, re.IGNORECASE):
        return "url"
    return "empty"


def _host_in(url: str, domains: tuple) -> bool:
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    try:
        p = urlparse(u)
        host = (p.hostname or "").lower()
    except ValueError:
        return False
    return p.scheme in ("http", "https") and any(host == d or host.endswith("." + d) for d in domains)


_CTA_ALLOWED_ATTRS = {
    "href", "src", "target", "rel", "alt", "title", "width", "height", "style", "class", "id", "name",
    "referrerpolicy", "frameborder", "scrolling", "border", "loading", "allowfullscreen", "browsingtopics",
}


class _CoupangCodeChecker(HTMLParser):
    """쿠팡 코드가 안전한지 검사합니다: 태그는 a·img·iframe만, 주소(href·src)는 쿠팡 도메인만, 최상위 요소는 하나만,
    on…(이벤트)·srcdoc·주석 등은 금지. 배너를 걸 쿠팡 링크(link.coupang.com / coupa.ng)도 함께 찾습니다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ok, self.reason, self.root = True, "", ""
        self.depth = 0
        self.banner_href = ""

    def _bad(self, why: str) -> None:
        if self.ok:
            self.ok, self.reason = False, why

    def handle_starttag(self, tag, attrs):
        if tag not in ("a", "img", "iframe"):
            return self._bad(f"허용되지 않는 태그 <{tag}>")
        if self.depth == 0:
            if self.root:
                return self._bad("코드가 두 덩어리 이상입니다")
            self.root = tag
        elif tag in ("a", "iframe"):
            return self._bad("태그가 겹쳐 있습니다")
        for name, val in attrs:
            name, val = (name or "").lower(), val or ""
            if name not in _CTA_ALLOWED_ATTRS and not name.startswith("data-"):
                return self._bad(f"허용되지 않는 속성 {name}")
            if name in ("href", "src") and ("\\" in val or "@" in val.split("?")[0]):
                return self._bad(f"의심스러운 주소({val[:60]})")
            if name == "style" and re.search(r"position|z-index|expression", val, re.IGNORECASE):
                return self._bad("style 안에 허용되지 않는 값")
            if name.startswith("on") or name in ("srcdoc", "formaction"):
                return self._bad(f"허용되지 않는 속성 {name}")
            if name == "style" and re.search(r"url\s*\(|expression|javascript", val, re.IGNORECASE):
                return self._bad("style 안에 허용되지 않는 값")
            if name in ("href", "src"):
                if not _host_in(val, CTA_ALLOWED_DOMAINS):
                    return self._bad(f"쿠팡이 아닌 주소({val[:60]})")
                if tag == "a" and name == "href" and not self.banner_href and _host_in(val, CTA_BANNER_LINK_DOMAINS):
                    self.banner_href = val.strip()
        if tag == "iframe" and not any((n or "").lower() == "src" for n, _ in attrs):
            return self._bad("iframe에 src가 없습니다")
        if tag in ("a", "iframe"):
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ("a", "iframe") and self.depth:
            self.depth -= 1

    def handle_endtag(self, tag):
        if tag in ("a", "iframe") and self.depth:
            self.depth -= 1
        elif tag != "img":
            self._bad(f"짝이 맞지 않는 닫는 태그 </{tag}>")

    def handle_data(self, data):
        if self.depth == 0 and data.strip():
            self._bad("코드 밖에 다른 글자가 섞여 있습니다")

    def handle_comment(self, data):
        self._bad("주석이 섞여 있습니다")

    def handle_decl(self, decl):
        self._bad("선언문이 섞여 있습니다")

    def handle_pi(self, data):
        self._bad("처리 명령이 섞여 있습니다")

    def unknown_decl(self, data):
        self._bad("선언문이 섞여 있습니다")


def analyze_coupang_html(code: str) -> dict:
    """쿠팡 HTML 코드 검사 결과: {"ok", "reason", "banner_href"}. banner_href는 배너에 걸 link.coupang.com(또는 coupa.ng) 주소(없으면 "")."""
    code = (code or "").strip()
    checker = _CoupangCodeChecker()
    # HTMLParser가 놓칠 수 있는 이상한 조각을 한 번 더 걸러냅니다.
    bad_tag = next((t for t in re.findall(r"<\s*/?\s*([a-zA-Z][\w:-]*)", code) if t.lower() not in ("a", "img", "iframe")), None)
    if bad_tag:
        return {"ok": False, "reason": f"허용되지 않는 태그 <{bad_tag}>", "banner_href": ""}
    if "<!" in code or "<?" in code or re.search(r"javascript\s*:", code, re.IGNORECASE):
        return {"ok": False, "reason": "허용되지 않는 내용이 섞여 있습니다", "banner_href": ""}
    try:
        checker.feed(code)
        checker.close()
    except Exception as e:  # noqa: BLE001 — 해석 못 하는 코드는 안전하지 않은 것으로 봅니다
        return {"ok": False, "reason": f"코드를 해석하지 못했습니다({type(e).__name__})", "banner_href": ""}
    if checker.ok and checker.depth != 0:
        checker.ok, checker.reason = False, "닫는 태그(</a>·</iframe>)가 없습니다"
    if checker.ok and not checker.root:
        checker.ok, checker.reason = False, "쿠팡 코드가 비어 있습니다"
    return {"ok": checker.ok, "reason": checker.reason, "banner_href": checker.banner_href if checker.ok else ""}


def _ensure_a_rel(code: str) -> str:
    """<a>에 rel이 아예 없을 때만 rel="nofollow sponsored"를 맨 앞에 추가합니다(다른 속성은 그대로)."""

    def _repl(m: re.Match) -> str:
        tag = m.group(0)
        return tag if re.search(r"\srel\s*=", tag, re.IGNORECASE) else tag[:2] + ' rel="nofollow sponsored"' + tag[2:]

    return re.sub(r"<a\b[^>]*>", _repl, code, flags=re.IGNORECASE)


def cta_link_warnings(raw: str) -> dict:
    """워드프레스 SHOP 최종본 화면 경고용: 구매 링크가 비어 있거나 자리표시자인 상품, 안전하지 않은 쿠팡 코드가 든 상품.
    {"empty": [상품명…], "unsafe": [(상품명, 이유)…]}"""
    empty, unsafe = [], []
    for s in parse_sections(split_tag_line(raw)[0])["sections"]:
        if s["is_faq"]:
            continue
        name = strip_markup(s["heading"])
        if s["is_product"]:
            kind = classify_link_value(s["link"])
            if kind == "empty":
                empty.append(name)
            elif kind == "html":
                info = analyze_coupang_html(s["link"])
                if not info["ok"]:
                    unsafe.append((name, info["reason"]))
        elif re.search(r"^[ \t]*링크[ \t]*[:：][ \t]*$", s["body"], re.MULTILINE):
            empty.append(name)
    return {"empty": empty, "unsafe": unsafe}


def parse_sections(raw: str) -> dict:
    """
    ## 기준으로 섹션을 나누고, 각 섹션을 '상품 카드' / 'FAQ' / '일반 섹션'으로 구분합니다.
    FAQ는 소제목에 "FAQ"나 "자주 묻는 질문"이 들어있으면 인식하고, 본문 안의 "Q: ~"/"A: ~"
    줄 쌍을 질문·답변으로 뽑아냅니다 — 검색결과 FAQ 리치 스니펫에도 쓰이고, 구매 전 흔한
    걱정거리를 미리 풀어줘서 구매 결정에도 도움이 됩니다.
    ('## ' 없이 소제목을 한 줄로만 적어 온 원문은 normalize_raw_headings로 먼저 보정합니다.)
    """
    parts = re.split(r"^##[ \t]+(.+)$", normalize_raw_headings(collapse_link_html(raw or "")), flags=re.MULTILINE)
    intro = ensure_blank_lines_around_blocks(parts[0].strip()) if parts else ""

    sections = []
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = ensure_blank_lines_around_blocks(parts[i + 1] if i + 1 < len(parts) else "")

        if _is_faq_heading(heading):
            qa_pairs, trailing_body = _parse_faq_body(body)
            sections.append(
                {
                    "heading": heading, "body": trailing_body, "price": "", "link": "",
                    "is_product": False, "is_faq": True, "qa_pairs": qa_pairs, "table": None,
                }
            )
            continue

        price_match = PRICE_LINE_RE.search(body)
        link_match = LINK_LINE_RE.search(body)
        price = price_match.group(1).strip() if price_match else ""
        link = link_match.group(1).strip() if link_match else ""

        clean_body = body
        if price_match:
            clean_body = clean_body.replace(price_match.group(0), "")
        if link_match:
            clean_body = clean_body.replace(link_match.group(0), "")

        # 예전엔 "## 한눈에 비교" 소제목일 때만 표를 찾았는데, 글쓰기 규칙이 늘어나면서 다른
        # 소제목("## 상황별로 이렇게 고르세요" 등)에도 비교표가 나올 수 있어서, 소제목 이름과
        # 무관하게 섹션 본문 안에 마크다운 표가 있으면 항상 찾습니다(없으면 그대로 None).
        table = parse_markdown_tables(clean_body)
        sections.append(
            {
                "heading": heading,
                "body": clean_body.strip(),
                "price": price,
                "link": link,
                "is_product": bool(price or link),
                "is_faq": False,
                "qa_pairs": [],
                # 섹션 본문 안 마크다운 표 전부: [("text", 글), ("table", 행 목록), ("text", 글), …] 순서 목록.
                # 표가 없으면 None. (예전엔 첫 표 하나만 (앞 글, 행, 뒤 글) 튜플이었음 — 읽는 곳은 렌더러뿐입니다.)
                "table": table,
            }
        )
    return {"intro": intro, "sections": sections}


# 본문 문단·목록 항목을 양쪽 정렬합니다 (소제목·표·버튼·캡션·인용구는 원래 정렬 유지).
# !important는 워드프레스 테마가 text-align에 자체 !important 리셋을 걸어둔 경우를 대비한 것이고,
# word-break:keep-all은 일부러 안 씁니다 — 한글에서 justify와 keep-all을 같이 쓰면 좁은 화면
# (모바일 360px 등)에서 한 줄에 어절이 적게 들어가는 문단이 단어 사이 간격만 크게 벌어져
# 보이는 부작용이 있어서, keep-all 없이 justify만 쓰는 조합을 씁니다.
JUSTIFY_STYLE = "text-align:justify !important;"
# 인용구는 짧고 큰 글씨 문장이라, justify를 걸면 마지막 줄이 아닌 줄까지 억지로 늘어나면서
# 글자 사이 간격이 크게 벌어져 보입니다. 인용구만 원래 정렬(왼쪽)로 둡니다.
QUOTE_ALIGN_STYLE = "text-align:left !important;"


def _quote_html(text: str) -> str:
    """일반 HTML(티스토리 등 블록이 아닌 경로)용 전환점 인용구."""
    return (
        f'<blockquote style="margin:24px 12px; padding:12px 18px; border-left:4px solid {QUOTE_TEXT}; '
        f'background:{QUOTE_BG};"><p style="margin:0; font-size:22px; font-weight:700; color:{QUOTE_TEXT}; '
        f'{QUOTE_ALIGN_STYLE}">{text}</p></blockquote>'
    )


def _quote_naver(text: str) -> str:
    """네이버: 박스·배경이 지워져도 눈에 띄게 — 앞뒤 줄바꿈 + 큰 글씨 + 다른 색 (font-size 인라인은
    보통 살아남는 걸 확인했습니다). text-align은 <p>의 margin처럼 붙여넣기 후 지워질 수도 있어
    아직 확인 전이라 일단 넣어두고, 실제로 붙여넣어서 남아있는지 확인이 필요합니다."""
    return f'<p style="{QUOTE_ALIGN_STYLE}"><strong style="font-size:20px; color:{QUOTE_TEXT};">{text}</strong></p>'


NAVER_H3_STYLE = "color:#111111; font-size:17px; font-weight:bold;"  # "## "(19px+❝+줄)보다 한 단계 작은 굵은 글씨


def _h3_html(text: str) -> str:
    """일반 HTML(블록이 아닌 경로)용 작은 소제목."""
    return f'<h3 style="margin:20px 0 10px; font-size:18px;">{html.escape(text, quote=False)}</h3>'


def _h3_naver(text: str) -> str:
    """네이버: 태그 구조는 지워지므로 <p> + 인라인 <strong> 스타일(굵게·진한 색·17px, 장식 없음)로 만듭니다."""
    return f'<p style="text-align:left;"><strong style="{NAVER_H3_STYLE}">{html.escape(text, quote=False)}</strong></p>'


def _wp_h3(text: str) -> str:
    """워드프레스: 표준 wp:heading 블록(level 3). 기존 h2 블록(_wp_heading)과 같은 방식입니다."""
    return _wp_block("heading", f'<h3 class="wp-block-heading">{html.escape(text, quote=False)}</h3>', {"level": 3})


def _faq_answer_pieces(answer: str, render_p, render_h3) -> list[str]:
    """FAQ 답변을 문단·작은 소제목 조각으로 렌더링합니다. "A. "는 첫 번째 글 문단 앞에만 붙습니다."""
    pieces, first = [], True
    for para in _faq_answer_paragraphs(answer):
        for kind, text in _split_h3_block(para):
            if kind == "h3":
                pieces.append(render_h3(text))
            else:
                pieces.append(render_p("A. " if first else "", text))
                first = False
    return pieces


def _paragraphs_to_html(text: str) -> str:
    parts = []
    for kind, payload in _split_paragraphs(text):
        if kind == "h3":
            parts.append(_h3_html(payload))
        elif kind == "shop":
            parts.append(_shop_block(payload, "naver"))
        elif kind == "quote":
            parts.append(_quote_html(payload))
        elif kind == "ul":
            items = "".join(f'<li style="{JUSTIFY_STYLE}">{_emphasize(item)}</li>' for item in payload)
            parts.append(f"<ul>{items}</ul>")
        elif kind == "ol":
            items = "".join(f'<li style="{JUSTIFY_STYLE}">{_emphasize(item)}</li>' for item in payload)
            parts.append(f"<ol>{items}</ol>")
        else:
            parts.append(f'<p style="{JUSTIFY_STYLE}">{_emphasize(payload)}</p>')
    return "".join(parts)


def _paragraphs_plain(text: str) -> str:
    """<p>는 넣되(양쪽 정렬을 걸 곳이 필요해서), 문단 사이 간격은 여전히 <br><br>로 직접
    만듭니다 — 네이버 에디터가 <p>의 margin을 지워버리는 걸 예전에 확인했어서, 붙여넣기 후
    간격이 사라지더라도 <br><br>이 최소한의 간격을 보장합니다. text-align이 붙여넣기 후에도
    남아있는지는 실제로 붙여넣어서 확인이 필요합니다.
    목록(ul/ol)은 항목 하나하나를 <p>로 따로 감싸지 않고, 문단 하나 안에서 <br>로만 줄을
    나눕니다 — 항목마다 <p>를 만들면 문단 간격(<br><br>)이 항목 사이에도 끼어서 목록이
    너무 성글어 보였습니다. 목록 앞뒤에는 다른 문단과 똑같이 <br><br> 하나만 둡니다."""
    rendered = []
    for kind, payload in _split_paragraphs(text):
        if kind == "h3":
            rendered.append(_h3_naver(payload))
        elif kind == "shop":
            rendered.append(_shop_block(payload, "naver"))
        elif kind == "quote":
            rendered.append(_quote_naver(payload))
        elif kind == "ul":
            inner = "<br>".join(f'• {_emphasize(item, "naver")}' for item in payload)
            rendered.append(f'<p style="{JUSTIFY_STYLE}">{inner}</p>')
        elif kind == "ol":
            inner = "<br>".join(f'{i + 1}. {_emphasize(item, "naver")}' for i, item in enumerate(payload))
            rendered.append(f'<p style="{JUSTIFY_STYLE}">{inner}</p>')
        else:
            rendered.append(f'<p style="{JUSTIFY_STYLE}">{_emphasize(payload, "naver")}</p>')
    return NAVER_GAP.join(rendered)


def _render_body_generic(body: str, table: list | tuple | None, platform: str) -> str:
    """일반 섹션 본문(문단·목록·인용구)과, 그 안에 마크다운 표가 있으면 표까지 순서대로 렌더링합니다.
    표가 여러 개면 "글 → 표 → 글 → 표 → 글" 순서 그대로입니다. 글 조각은 각각 문단 처리기를 거칩니다."""
    render_text = _paragraphs_plain if platform == "naver" else _paragraphs_to_html
    segments = _table_segments(table)
    if not segments:
        return render_text(body)
    parts = []
    for n, (kind, payload) in enumerate(segments):
        if n and platform == "naver":
            parts.append(NAVER_GAP)
        parts.append(render_text(payload) if kind == "text" else _table_html(payload, platform))
    return "".join(parts)


def _product_card_html(section: dict, image_tag: str, platform: str, drop_empty_cta: bool = False) -> str:
    """drop_empty_cta: 네이버 SHOP 전용. True면 "링크:" 값이 비었거나 [구매링크] 같은 자리표시자일 때(classify_link_value == "empty")
    네이버 "→ 오늘 가격 보기" 링크를 만들지 않습니다(링크는 사용자가 에디터에서 직접 넣음). 워드프레스 카드에는 영향 없음."""
    link_href = section["link"] or "#"

    if platform == "naver":
        # <h2>, <div>, <hr>까지 전부 네이버 에디터가 지워버리는 걸 확인했습니다(실제 테스트
        # 결과). 그래서 태그 구조에 기대는 걸 포기하고, "굵게+색깔 텍스트"와 실제 줄바꿈
        # 문자(<br>)만으로 구성합니다 — 이 두 가지만 실제로 살아남는 걸 확인했습니다.
        # 구분선도 태그(<hr>) 대신 눈에 보이는 기호 텍스트로 만들어서, 스타일이 아니라
        # 글자 자체로 구획을 나눕니다.
        heading_line = _naver_heading_html(section["heading"])
        price_line = (
            f'<strong style="color:#e8622c;">{strip_markup(section["price"])}</strong>{NAVER_GAP}' if section["price"] else ""
        )
        body_text = _render_body_generic(section["body"], section.get("table"), "naver")
        cta = (
            f'<a href="{link_href}" target="_blank" rel="nofollow sponsored noopener" '
            f'style="color:#03c75a; font-weight:700; text-decoration:underline;">{CTA_BUTTON_TEXT["naver"]}</a>'
        )
        cta_part = "" if drop_empty_cta and classify_link_value(section["link"]) == "empty" else cta + NAVER_GAP
        return (
            f"{heading_line}"
            + (f"{image_tag}<br>" if image_tag else "")
            + price_line
            + body_text
            + NAVER_GAP
            + cta_part
            + "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
            + NAVER_GAP
        )

    # 워드프레스는 API로 직접 들어가서 붙여넣기 과정 자체가 없으므로(스타일이 깨질 일이
    # 없음), 원래대로 화려한 카드 스타일을 그대로 씁니다.
    price_badge = (
        f'<span style="display:inline-block; background:#fff0e9; color:#c8460a; '
        f'font-weight:700; padding:4px 12px; border-radius:20px; font-size:15px; '
        f'margin-bottom:12px;">{strip_markup(section["price"])}</span><br>'
        if section["price"]
        else ""
    )
    cta = (
        f'<a href="{link_href}" target="_blank" rel="nofollow sponsored noopener" '
        f'style="display:block; text-align:center; background:#e8622c; color:#ffffff; '
        f'font-weight:700; font-size:16px; padding:14px 0; border-radius:10px; '
        f'text-decoration:none; margin-top:16px;">{CTA_BUTTON_TEXT["wordpress"]}</a>'
    )
    return (
        '<div style="border:1px solid #eee; border-radius:14px; padding:24px; margin:28px 0;">'
        f'<h2 style="margin:0 0 14px; font-size:22px;">{strip_markup(section["heading"])}</h2>'
        f"{image_tag}{price_badge}"
        f'<div style="font-size:15px; line-height:1.8; color:#333;">{_render_body_generic(section["body"], section.get("table"), "wordpress")}</div>'
        f"{cta}"
        "</div>"
    )


def _plain_section_html(section: dict, platform: str, inject: dict | None = None) -> str:
    if platform == "naver":
        # <h2>·왼쪽 테두리 스타일 다 지워지는 걸 확인해서, 굵은 텍스트 + 이모지 + 실제
        # 줄바꿈만으로 구성합니다.
        # inject(본문 사진, 네이버만): {"top": 소제목 바로 아래, "after_first": 첫 문단 뒤}의 <img>. "end"는 호출하는 쪽이 섹션 뒤에 붙입니다.
        heading_line = _naver_heading_html(section["heading"])
        body_html = _render_body_generic(section["body"], section.get("table"), "naver")
        if inject and inject.get("after_first"):
            head, tail = _split_after_units_naver(body_html, section_block_units(section)[0])
            body_html = head + NAVER_GAP + inject["after_first"] + tail if tail else body_html + NAVER_GAP + inject["after_first"]
        return heading_line + ((inject or {}).get("top") or "") + body_html + NAVER_GAP
    return (
        f'<h2 style="border-left:4px solid #e8622c; padding-left:12px; margin:32px 0 14px;">{strip_markup(section["heading"])}</h2>'
        f'<div style="font-size:15px; line-height:1.8; color:#333;">{_render_body_generic(section["body"], section.get("table"), platform)}</div>'
    )


def _faq_section_html(section: dict, platform: str) -> str:
    """FAQ 섹션은 구매 전 흔한 걱정거리(방수 되나요, AS는요 등)를 미리 풀어주는 역할도
    겸하기 때문에, 질문을 눈에 띄게 굵게 처리해서 훑어보기 쉽게 만듭니다."""
    if not section["qa_pairs"]:
        return _plain_section_html(section, platform)

    if platform == "naver":
        heading_line = _naver_heading_html(section["heading"])
        qa_blocks = []
        for q, a in section["qa_pairs"]:
            answer_html = NAVER_GAP.join(
                _faq_answer_pieces(
                    a,
                    lambda prefix, p: f'<p style="{JUSTIFY_STYLE}">{prefix}{_emphasize(p, "naver")}</p>',
                    _h3_naver,
                )
            )
            qa_blocks.append(f'<strong style="color:#111;">Q. {_emphasize(q, "naver")}</strong><br>{answer_html}')
        qa_html = NAVER_GAP.join(qa_blocks)
        # FAQ가 마지막 섹션일 때, 마지막 답변 뒤에 남는 일반 마무리 문단(있으면)을 소제목 없이 이어서 보여줍니다.
        trailing_html = (NAVER_GAP + _render_body_generic(section["body"], None, "naver")) if section["body"] else ""
        return heading_line + qa_html + trailing_html + NAVER_GAP

    qa_items = "".join(
        '<div style="margin-bottom:14px;">'
        f'<p style="font-weight:700; margin:0 0 4px;">Q. {_emphasize(q)}</p>'
        + "".join(
            _faq_answer_pieces(
                a,
                lambda prefix, p: f'<p style="margin:0; color:#333; {JUSTIFY_STYLE}">{prefix}{_emphasize(p)}</p>',
                _h3_html,
            )
        )
        + "</div>"
        for q, a in section["qa_pairs"]
    )
    trailing_html = (
        f'<div style="font-size:15px; line-height:1.8; color:#333;">{_render_body_generic(section["body"], None, platform)}</div>'
        if section["body"]
        else ""
    )
    return f'<h2 style="border-left:4px solid #e8622c; padding-left:12px; margin:32px 0 14px;">{strip_markup(section["heading"])}</h2>{qa_items}{trailing_html}'


def _extract_price_number(price_text: str) -> int:
    """'89,000원대' 같은 문자열에서 숫자만 뽑습니다. 스키마의 offers.price에 쓰는 용도라,
    대략적인 값이면 충분합니다(정확한 정가가 아니어도 구조화 데이터 형식상 문제없음)."""
    digits = re.sub(r"[^\d]", "", price_text or "")
    return int(digits) if digits else 0


def has_coupang_link(raw: str) -> bool:
    """원고 어디에든(도입부·소제목 섹션·FAQ) "링크: https://link.coupang.com/…" 줄이 하나라도 있는지.
    워드프레스 일반에서 대가성 문구·가격 경고를 넣을지 정할 때 씁니다 — 쿠팡 링크가 글에 있으면 위치와
    상관없이 고지 문구가 반드시 들어가야 하므로, 버튼이 만들어지는 섹션만이 아니라 원고 전체를 봅니다."""
    return any(m.group(1).strip().startswith(COUPANG_LINK_PREFIX) for m in LINK_LINE_RE.finditer(raw or ""))


def _pop_intro_links(parsed: dict, prefix: str) -> list[str]:
    """도입부(첫 소제목 앞)에 있는 "링크: <prefix…>" 줄을 도입부 글에서 빼고 그 주소 목록을 돌려줍니다
    (워드프레스 일반: 도입부의 쿠팡 링크 줄을 글자 그대로 두지 않고 구매 버튼으로 바꾸기 위함)."""
    links, kept = [], []
    for line in parsed["intro"].split("\n"):
        m = LINK_LINE_RE.match(line)
        if m and m.group(1).strip().startswith(prefix):
            links.append(m.group(1).strip())
        else:
            kept.append(line)
    parsed["intro"] = "\n".join(kept).strip()
    return links


def build_product_schema(sections: list[dict]) -> str:
    """
    상품 섹션들로 Product/Offer 구조화 데이터(JSON-LD)를 만듭니다. 검색결과에 가격 정보가
    리치 스니펫으로 노출될 가능성을 높여줍니다. 평점(review)은 실제 데이터가 없으니 절대
    지어내지 않습니다 — 없는 리뷰를 꾸며내면 구글 스팸 정책 위반 소지가 있습니다.
    가격이 없는 상품은 아예 건너뜁니다 — Product 스키마는 offers/review/aggregateRating 중
    하나가 반드시 있어야 하는데(구글 요구사항), 이 셋 중 우리가 실제로 만드는 건 가격 기반
    offers뿐이라, 가격이 없으면 셋 다 없는 무효한 Product 스키마가 됩니다. 글쓰기 프롬프트가
    가격을 빼기로 한 뒤로는 이런 글이 정상이므로, 그런 글은 Product 없이 FAQ 스키마만 나갑니다.
    """
    products = [s for s in sections if s.get("is_product") and _extract_price_number(s.get("price", ""))]
    if not products:
        return ""

    items = []
    for s in products:
        item = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": strip_markup(s["heading"]),
            "offers": {
                "@type": "Offer",
                "price": str(_extract_price_number(s["price"])),
                "priceCurrency": "KRW",
                "availability": "https://schema.org/InStock",
            },
        }
        items.append(item)

    return "\n".join(
        f'<script type="application/ld+json">{json.dumps(item, ensure_ascii=False)}</script>' for item in items
    )


def find_price_mentions(raw: str) -> list[str]:
    """
    원고 안에 남아있는 가격 표현("10,000원", "69만 8,500원", "160만원대" 등)이 있는 줄을 찾아
    돌려줍니다. 글쓰기 프롬프트에서 가격을 빼기로 한 뒤에도 실수로 남아있는 걸 잡아내는
    용도입니다 — 자동으로 지우지는 않고, 최종본 화면에서 어디에 남아있는지만 보여줍니다.
    "가격:" 구조 줄이든, 본문 문장이나 표 안에 섞여 있는 가격이든 전부 잡습니다.
    """
    seen = set()
    result = []
    for line in collapse_link_html(raw).splitlines():
        stripped = line.strip()
        if not stripped or stripped in seen:
            continue
        if re.match(r"링크[ \t]*[:：][ \t]*<", stripped):  # 쿠팡 HTML 코드 줄(속성·alt 안의 숫자+원은 본문 가격이 아님)
            continue
        if MONEY_PATTERN.search(stripped):
            seen.add(stripped)
            result.append(stripped)
    return result


def build_faq_schema(sections: list[dict]) -> str:
    """FAQ 섹션의 질문·답변으로 FAQPage 구조화 데이터를 만듭니다."""

    faq_sections = [s for s in sections if s.get("is_faq") and s.get("qa_pairs")]
    if not faq_sections:
        return ""

    main_entity = []
    for s in faq_sections:
        for q, a in s["qa_pairs"]:
            main_entity.append(
                {
                    "@type": "Question",
                    "name": strip_markup(q),
                    "acceptedAnswer": {"@type": "Answer", "text": strip_markup(_drop_h3_lines(a))},
                }
            )
    if not main_entity:
        return ""

    schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": main_entity}
    return f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>'


def gutenberg_image_block(src: str, alt: str, media_id: int | None = None, class_name: str | None = None, caption: str | None = None) -> str:
    """
    표준 core/image 블록. 워드프레스 관리자 편집기에서 이미지 하나하나가 독립 블록이라 마우스로
    드래그해서 순서를 바꿀 수 있습니다. <img>에 style 같은 추가 속성을 넣으면 블록 검증이
    깨지므로 넣지 않습니다. media_id가 있으면 미디어 라이브러리와 연결된 블록(id + wp-image-N
    클래스)으로 만듭니다 — 발행 때 이미지를 올린 뒤 wp_publisher가 이 형태로 다시 만듭니다.
    class_name은 블록의 "추가 CSS 클래스"(figure에 붙음)입니다 — 표지를 구분하는 용도.
    """
    attrs = {"sizeSlug": "large", "linkDestination": "none"}
    figure_class = "wp-block-image size-large"
    if class_name:
        attrs["className"] = class_name
        figure_class += f" {class_name}"
    img_class = ""
    if media_id:
        attrs = {"id": media_id, **attrs}
        img_class = f' class="wp-image-{media_id}"'
    # caption(출처 등)이 있으면 표준 이미지 블록의 캡션(figcaption)으로 넣습니다. 없으면(기본) 예전과 똑같은 마크업입니다.
    figcaption = f'<figcaption class="wp-element-caption">{html.escape(caption, quote=False)}</figcaption>' if caption else ""
    return (
        f"<!-- wp:image {json.dumps(attrs, separators=(',', ':'))} -->\n"
        f'<figure class="{figure_class}"><img src="{src}" alt="{html.escape(alt, quote=True)}"{img_class}/>{figcaption}</figure>\n'
        "<!-- /wp:image -->\n"
    )


def _norm_heading(text: str) -> str:
    """소제목 비교용: 마크다운 기호·공백을 없애고 소문자로."""
    return re.sub(r"\s+", "", strip_markup(text or "")).lower()


def match_external_images(sections: list, external_images: list | None) -> tuple[dict, list]:
    """싼세계 "외부 이미지": 이미지마다 지정한 소제목("heading")과 같은 섹션을 찾아 {섹션 번호: [이미지…]}로 묶습니다
    (같은 소제목이면 올린 순서 그대로). 소제목을 못 찾은 이미지는 두 번째 값(못 넣는 이미지 목록)으로 돌려줍니다."""
    by_idx: dict = {}
    unmatched: list = []
    index_of: dict = {}
    for i, s in enumerate(sections or []):
        index_of.setdefault(_norm_heading(s.get("heading", "")), i)
    for e in external_images or []:
        i = index_of.get(_norm_heading(e.get("heading", ""))) if (e.get("heading") or "").strip() else None
        if i is None or not e.get("path"):
            unmatched.append(e)
        else:
            by_idx.setdefault(i, []).append(e)
    return by_idx, unmatched


# ── 워드프레스 표준 블록(Gutenberg) 출력 ──────────────────────────────────────────
# 블록 주석 없는 순수 HTML을 보내면 편집기가 글 전체를 "클래식" 블록 하나로 읽어서 편집이 불편합니다.
# 그래서 워드프레스용 최종본은 문단·제목·이미지·버튼을 각각 표준 블록으로 감쌉니다.
# 주의: core/paragraph·core/list는 블록 속성에 없는 style을 <p>/<li>에 붙이면 블록 검증이 깨져 편집기가
# "예상치 못한 콘텐츠" 경고를 냅니다(인라인 style은 경고 없는 방법이 아닙니다). 그래서 정렬은 className
# (ssg-justify)으로 주고(사이트 CSS: .ssg-justify, .ssg-justify li { text-align: justify !important; }),
# 꼭 style이 필요한 박스(대가성 문구·링크 박스·표)는 wp:html 블록으로 감쌉니다. 안쪽 <span>/<strong>의
# 인라인 스타일(강조)은 문단 블록 안에서도 검증에 영향이 없어 그대로 둡니다.

def _wp_block(name: str, inner: str, attrs: dict | None = None) -> str:
    attrs_str = f" {json.dumps(attrs, separators=(',', ':'))}" if attrs else ""
    return f"<!-- wp:{name}{attrs_str} -->\n{inner}\n<!-- /wp:{name} -->\n"


WP_JUSTIFY_CLASS = "ssg-justify"


def _wp_justify_paragraph(inner: str, bottom_margin: str | None = None) -> str:
    """양쪽 정렬 본문 문단: style 대신 className으로 (<p class="ssg-justify">). 정렬은 사이트 CSS가 줍니다.
    bottom_margin 을 주면(싼세계 도입부 전용) 문단 아래 여백을 블록 속성과 style 에 같이 넣어, 편집기에서도 안 깨지게 합니다."""
    if not bottom_margin:
        return _wp_block("paragraph", f'<p class="{WP_JUSTIFY_CLASS}">{inner}</p>', {"className": WP_JUSTIFY_CLASS})
    attrs = {"className": WP_JUSTIFY_CLASS, "style": {"spacing": {"margin": {"bottom": bottom_margin}}}}
    return _wp_block("paragraph", f'<p class="{WP_JUSTIFY_CLASS}" style="margin-bottom:{bottom_margin}">{inner}</p>', attrs)


def _wp_quote(text: str) -> str:
    """
    전환점 인용구: 표준 wp:quote 블록 안에 문단을 넣습니다. 글씨 크기·굵기·색·배경은 블록 속성이 아니라
    문단 안쪽 <strong><span style=…> 인라인 스타일로 줍니다(안쪽 span 스타일은 블록 검증에 걸리지 않습니다).
    <p> 자체에는 style을 붙이지 않습니다 — core/paragraph가 모르는 style이라 블록 검증이 깨집니다.
    좌우 들여쓰기·왼쪽 선 같은 인용구 모양은 사이트 테마가 wp:quote에 적용합니다.
    """
    span = (
        f'<strong><span style="font-size:22px; color:{QUOTE_TEXT}; background-color:{QUOTE_BG}; '
        f'padding:2px 8px;">{html.escape(text, quote=False)}</span></strong>'
    )
    paragraph = _wp_block("paragraph", f"<p>{span}</p>").rstrip("\n")
    return _wp_block("quote", f'<blockquote class="wp-block-quote">{paragraph}</blockquote>')


_URL_SCAN_RE = re.compile(r"(<a\b[^>]*>.*?</a>|<[^>]+>)|(https?://[^\s<>\"]+)", re.IGNORECASE | re.DOTALL)
_URL_TRAIL_CHARS = ".,)]*，。）"  # 주소 끝에 붙은 마침표·쉼표·닫는 괄호(와 **강조 기호)는 링크에서 빼고 글자로 남깁니다


def is_reference_heading(heading: str) -> bool:
    """소제목이 "참고 자료"인지 — 앞뒤 공백·이모지·"참고자료" 붙여쓰기도 인정."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", strip_markup(heading or "")) == "참고자료"


def _protect_urls(text: str) -> tuple[str, list[str]]:
    """본문 글자 속 http/https 주소를 클릭 링크(<a href target="_blank" rel="noopener">)로 바꿀 자리표시로 빼 둡니다.
    이미 <a> 안에 있는 주소와 HTML 태그 속 주소는 건드리지 않습니다. nofollow는 붙이지 않습니다(공식 기관 출처)."""
    anchors: list[str] = []

    def repl(m: re.Match) -> str:
        if m.group(1):
            return m.group(0)
        url, trail = m.group(2), ""
        while url and url[-1] in _URL_TRAIL_CHARS:
            trail, url = url[-1] + trail, url[:-1]
        if not re.match(r"https?://[^/\s]", url, re.IGNORECASE):
            return m.group(0)
        safe = html.escape(url, quote=True)
        anchors.append(f'<a href="{safe}" target="_blank" rel="noopener">{safe}</a>')
        return f"{len(anchors) - 1}{trail}"

    return _URL_SCAN_RE.sub(repl, text), anchors


def _emphasize_linked(text: str, platform: str = "wordpress") -> str:
    """_emphasize + 주소 링크화. 주소는 자리표시로 빼 두었다가 강조 처리(금액·퍼센트·**)가 끝난 뒤 되돌려 놓아서,
    주소 안의 숫자·기호가 강조에 걸려 깨지지 않습니다."""
    protected, anchors = _protect_urls(text)
    out = _emphasize(protected, platform)
    return re.sub(r"(\d+)", lambda m: anchors[int(m.group(1))] if int(m.group(1)) < len(anchors) else m.group(0), out)


def _wp_list_block(items: list[str], ordered: bool, emphasize=_emphasize) -> str:
    tag = "ol" if ordered else "ul"
    inner_items = "\n\n".join(
        _wp_block("list-item", f"<li>{emphasize(item)}</li>").rstrip("\n") for item in items
    )
    attrs = {"ordered": True, "className": WP_JUSTIFY_CLASS} if ordered else {"className": WP_JUSTIFY_CLASS}
    return _wp_block("list", f'<{tag} class="wp-block-list {WP_JUSTIFY_CLASS}">{inner_items}</{tag}>', attrs)


def _wp_paragraphs(text: str, link_urls: bool = False, p_margin: str | None = None) -> str:
    """link_urls=True면(소제목이 "참고 자료"인 섹션) 본문 속 http/https 주소를 클릭 링크로 만듭니다.
    섹션 소제목이 아니라 본문 안의 "### 참고 자료"(작은 소제목)여도, 그 아래(다음 작은 소제목 전까지)는 링크로 만듭니다."""
    parts = []
    linking = link_urls
    for kind, payload in _split_paragraphs(text):
        emphasize = _emphasize_linked if linking else _emphasize
        if kind == "h3":
            linking = link_urls or is_reference_heading(payload)
            parts.append(_wp_h3(payload))
        elif kind == "shop":
            parts.append(_shop_block(payload, "wordpress"))
        elif kind == "quote":
            parts.append(_wp_quote(payload))
        elif kind == "ul":
            parts.append(_wp_list_block(payload, ordered=False, emphasize=emphasize))
        elif kind == "ol":
            parts.append(_wp_list_block(payload, ordered=True, emphasize=emphasize))
        else:
            parts.append(_wp_justify_paragraph(emphasize(payload), p_margin))
    return "".join(parts)


def _render_body_wp(body: str, table: list | tuple | None, link_urls: bool = False) -> str:
    """워드프레스(Gutenberg) 섹션 본문을 문단/목록/인용구 블록으로 만들고, 마크다운 표가
    있으면 글 조각 사이사이에 표 블록(_wp_table_block)을 순서대로 끼워 넣습니다(표가 여러 개여도 전부).
    link_urls는 _wp_paragraphs 참고."""
    segments = _table_segments(table)
    if not segments:
        return _wp_paragraphs(body, link_urls)
    parts = []
    for kind, payload in segments:
        parts.append(_wp_paragraphs(payload, link_urls) if kind == "text" else _wp_table_block(payload))
    return "".join(parts)


def _wp_heading(text: str) -> str:
    return _wp_block("heading", f'<h2 class="wp-block-heading">{html.escape(strip_markup(text), quote=False)}</h2>')


def _wp_button(text: str, href: str) -> str:
    button = _wp_block(
        "button",
        '<div class="wp-block-button"><a class="wp-block-button__link has-text-color has-background wp-element-button" '
        f'href="{html.escape(href, quote=True)}" target="_blank" rel="nofollow sponsored noopener" '
        f'style="color:#ffffff;background-color:#e8622c">{text}</a></div>',
        {"style": {"color": {"background": "#e8622c", "text": "#ffffff"}}},
    )
    return _wp_block("buttons", f'<div class="wp-block-buttons">{button.rstrip()}</div>')


def _wp_separator() -> str:
    return _wp_block("separator", '<hr class="wp-block-separator has-alpha-channel-opacity"/>')


def _wp_cover_block(cover_img_tag: str) -> str:
    """app.py가 만든 표지 <img> 태그에서 주소·alt를 뽑아 표지 이미지 블록(className seo-cover-img)으로 바꿉니다."""
    src_match = re.search(r'src="([^"]+)"', cover_img_tag or "")
    if not src_match:
        return ""
    alt_match = re.search(r'alt="([^"]*)"', cover_img_tag)
    return gutenberg_image_block(
        src_match.group(1), html.unescape(alt_match.group(1)) if alt_match else "", class_name="seo-cover-img"
    )


def _wp_cta_banner(banner_url: str, href: str) -> str:
    """싼세계 배너(가운데, 최대 400px, 모바일에서는 화면 폭에 맞게 줄어듦). href가 있으면 쿠팡 링크로 연결합니다
    (수익 추적이 깨지지 않게 noreferrer는 넣지 않고 referrerpolicy="unsafe-url"만 둡니다)."""
    img = (
        f'<img src="{html.escape(banner_url, quote=True)}" alt="{CTA_BANNER_ALT}" '
        'style="display:inline-block;width:100%;max-width:400px;height:auto;border:0;box-shadow:none !important;" />'
    )
    if href:
        img = (
            f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="nofollow sponsored noopener" '
            f'referrerpolicy="unsafe-url">{img}</a>'
        )
    return _wp_block("html", f'<div style="text-align:center;margin:20px 0 8px;">{img}</div>')


def _wp_cta_blocks(link: str, banner_url: str) -> str:
    """상품 끝 구매 영역(배너 주소가 http로 시작할 때). 쿠팡 HTML 코드면 배너 + 코드, 쿠팡 주소면 배너만(클릭 시 그 링크),
    비었거나 자리표시자면 배너(href="#"), 쿠팡이 아닌 주소면 기존 주황 버튼. 배너 주소가 이상하면 전부 기존 버튼.
    안전하지 않은 코드(쿠팡이 아닌 도메인·script 등)는 아무것도 넣지 않습니다(경고는 cta_link_warnings가 화면에 띄움)."""
    banner_ok = (banner_url or "").lower().startswith(("http://", "https://"))
    kind = classify_link_value(link)
    if kind == "html":
        info = analyze_coupang_html(link)
        if not info["ok"]:
            return ""
        banner = _wp_cta_banner(banner_url, info["banner_href"]) if banner_ok else ""
        code = _ensure_a_rel(link.strip())
        return banner + _wp_block("html", f'<div style="text-align:center;margin:0 0 20px;">{code}</div>')
    if kind == "url":
        if banner_ok and _host_in(link, CTA_BANNER_LINK_DOMAINS):
            return _wp_cta_banner(banner_url, link.strip())  # 쿠팡 주소: 배너만(배너를 누르면 그 쿠팡 링크로 이동)
        return _wp_button(CTA_BUTTON_TEXT["wordpress"], link)  # 쿠팡이 아닌 주소(안전장치)·배너 주소 이상: 기존 주황 버튼
    if banner_ok:
        return _wp_cta_banner(banner_url, "#")  # 비었거나 [구매링크] 같은 자리표시자: 배너(링크 경고는 화면에 그대로 뜹니다)
    return _wp_button(CTA_BUTTON_TEXT["wordpress"], link or "#")


# ── 싼세계(wp_tips) 일반글 전용 링크 블록 (2026-11-20) ─────────────────────────────────────────
# 원고 '링크:' 값이 "<일반 링크> ‖카드‖ <쿠팡 링크카드 HTML> ‖별점‖ <별점 줄 글자>" 모양일 때만 쓴다(tools/wp_import.py 가 만듦).
# 이 표시(‖카드‖)가 없는 글은 아무것도 달라지지 않는다. 배너 설정은 tools/wp_banner.json(img_url 이 비면 배너 없음).
WPTIPS_CARD_MARK = " ‖카드‖ "
WPTIPS_STAR_MARK = " ‖별점‖ "
_WPTIPS_BANNER_FILE = os.environ.get("WP_BANNER_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "wp_banner.json")  # 환경 변수는 시험용


def _wptips_banner_cfg() -> dict:
    try:
        with open(_WPTIPS_BANNER_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def split_wptips_link_value(link: str):
    """'링크:' 값 → (일반 링크, 카드 HTML, 별점 줄 글자). ‖카드‖ 표시가 없으면 None."""
    v = (link or "").strip()
    if WPTIPS_CARD_MARK not in v:
        return None
    url, rest = v.split(WPTIPS_CARD_MARK, 1)
    card, _, stars = rest.partition(WPTIPS_STAR_MARK)
    return url.strip(), card.strip(), stars.strip()


def _wptips_card_html(card: str) -> str:
    """링크카드 HTML 을 안전할 때만 돌려준다. iframe 은 src 가 https://coupa.ng/ 로 시작하고 속성이 6개 안일 때만,
    <a> 카드는 쿠팡 검사(analyze_coupang_html)를 통과할 때만(rel 이 없으면 sponsored noopener 만 더함). 아니면 빈 글자."""
    if not card or not analyze_coupang_html(card)["ok"]:
        return ""
    if re.match(r"<iframe\b", card, re.IGNORECASE):
        tag = re.match(r"<iframe\b[^>]*>", card, re.IGNORECASE)
        attrs = [m.lower() for m in re.findall(r"\s([a-zA-Z-]+)\s*=", tag.group(0))] if tag else []
        src = re.search(r'\ssrc\s*=\s*"([^"]*)"', tag.group(0)) if tag else None
        ok_attrs = {"src", "width", "height", "frameborder", "scrolling", "referrerpolicy"}
        if not src or not src.group(1).startswith("https://coupa.ng/") or any(a not in ok_attrs for a in attrs):
            return ""
        return card
    return re.sub(r"<a\b[^>]*>", lambda m: m.group(0) if re.search(r"\srel\s*=", m.group(0), re.IGNORECASE) else m.group(0)[:2] + ' rel="sponsored noopener"' + m.group(0)[2:], card)


def _wptips_link_blocks(link: str):
    """싼세계 일반글 제품 끝: ① 배너(가운데, 일반 링크로) ② 링크카드(가운데) ③ 작은 글씨 별점 줄. 표시가 없으면 None(예전 구매 버튼)."""
    parts = split_wptips_link_value(link)
    if parts is None:
        return None
    url, card, stars = parts
    if not _host_in(url, CTA_BANNER_LINK_DOMAINS):
        return None
    out = ""
    cfg = _wptips_banner_cfg()
    img = str(cfg.get("img_url") or "").strip()
    if img.lower().startswith(("http://", "https://")):
        alt = html.escape(str(cfg.get("alt") or CTA_BANNER_ALT), quote=True)
        width = re.sub(r"[^0-9.%a-z]", "", str(cfg.get("width") or "40%"))
        try:
            minw = int(float(cfg.get("min_width_px") or 240))  # 숫자가 아니면 240
            if minw <= 0:
                minw = 240
        except (TypeError, ValueError, OverflowError):
            minw = 240
        tag = (f'<img src="{html.escape(img, quote=True)}" alt="{alt}" '
               f'style="display:block;margin:0 auto;width:{width};min-width:{minw}px;max-width:100%;height:auto;border:0;box-shadow:none !important;" />')
        out += _wp_block("html", f'<div style="text-align:center;margin:20px 0 4px;"><a href="{html.escape(url, quote=True)}" '
                                 f'target="_blank" rel="sponsored nofollow noopener" referrerpolicy="unsafe-url">{tag}</a></div>')
    code = _wptips_card_html(card)
    if code:
        out += _wp_block("html", f'<div style="text-align:center;margin:0 0 4px;">{code}</div>')
    if stars:
        out += _wp_block("html", f'<p style="text-align:center;font-size:13px;color:#666;margin:4px 0 20px;">{html.escape(stars, quote=False)}</p>')
    if not out:  # 카드가 걸러지고 배너·별점 줄도 없으면: 값 전체(‖카드‖ 표시 포함) 대신 일반 링크만 버튼 주소로
        return _wp_button(CTA_BUTTON_TEXT["wordpress"], url)
    return out


def _wp_product_blocks(section: dict, images: list[dict], cta_banner_url: str | None = None) -> str:
    """cta_banner_url이 None이면 예전 그대로(주황 구매 버튼). 문자열이면(워드프레스 SHOP) 배너 + 쿠팡 코드 방식."""
    parts = [_wp_heading(section["heading"])]
    parts += [gutenberg_image_block(img["path"], img.get("alt") or strip_markup(section["heading"])) for img in images]
    if section["price"]:
        badge = (
            '<span style="display:inline-block; background:#fff0e9; color:#c8460a; font-weight:700; '
            f'padding:4px 12px; border-radius:20px; font-size:15px;">{html.escape(strip_markup(section["price"]), quote=False)}</span>'
        )
        parts.append(_wp_block("paragraph", f"<p>{badge}</p>"))
    parts.append(_render_body_wp(section["body"], section.get("table")))
    wt_block = _wptips_link_blocks(section["link"])  # 싼세계 전용: 원고 링크 값에 ‖카드‖ 표시가 있을 때만(없으면 None → 예전 그대로)
    if wt_block is not None:
        parts.append(wt_block)
    elif cta_banner_url is None:
        parts.append(_wp_button(CTA_BUTTON_TEXT["wordpress"], section["link"] or "#"))
    else:
        parts.append(_wp_cta_blocks(section["link"], cta_banner_url))
    parts.append(_wp_separator())
    return "".join(parts)


def _wp_plain_blocks(section: dict, inject: dict | None = None) -> str:
    # 소제목이 "참고 자료"인 일반 섹션만 본문 속 주소를 클릭 링크로 만듭니다 (워드프레스 출력만)
    # inject(본문 사진): {"top": 소제목 바로 아래, "after_first": 첫 블록 뒤}의 이미지 블록. "end"는 호출하는 쪽이 섹션 뒤에 붙입니다.
    body = _render_body_wp(section["body"], section.get("table"), link_urls=is_reference_heading(section["heading"]))
    if inject and inject.get("after_first"):
        head, tail = _split_after_units_wp(body, section_block_units(section)[0])
        body = head + inject["after_first"] + tail
    return _wp_heading(section["heading"]) + ((inject or {}).get("top") or "") + body


def _wp_faq_blocks(section: dict) -> str:
    if not section["qa_pairs"]:
        return _wp_plain_blocks(section)
    parts = [_wp_heading(section["heading"])]
    for q, a in section["qa_pairs"]:
        parts.append(_wp_block("paragraph", f"<p><strong>Q. {_emphasize(q)}</strong></p>"))
        parts.extend(
            _faq_answer_pieces(
                a,
                lambda prefix, para: _wp_justify_paragraph(f"{prefix}{_emphasize(para)}"),
                _wp_h3,
            )
        )
    # FAQ가 마지막 섹션일 때, 마지막 답변 뒤에 남는 일반 마무리 문단(있으면)을 소제목 없이 이어서 붙입니다.
    if section["body"]:
        parts.append(_render_body_wp(section["body"], None))
    return "".join(parts)


def _body_image_insert_after(n_sections: int, n_images: int) -> set:
    """본문 이미지를 몇 번째 섹션 "뒤"에 넣을지 — 실수 간격으로 계산해서(정수 나눗셈으로 앞쪽에 몰리는
    문제 방지) 섹션 전체에 고르게 퍼뜨립니다. app.py의 _insert_images_into_html과 같은 방식입니다."""
    if not n_images or not n_sections:
        return set()
    step = n_sections / n_images
    return {min(n_sections - 1, int(i * step)) for i in range(n_images)}


# ── 본문 사진 위치 계획 (photo_v2_20261020) ────────────────────────────────────────────
# 예전엔 섹션 수 ÷ 장수로 고른 간격(_body_image_insert_after)에만 넣어서 메모의 "## 소제목"을 무시하고 늘 같은 자리였습니다.
# 지금은 (1) 사진에 소제목이 저장돼 있으면 그 섹션에 넣고, (2) 소제목이 안 맞는 사진은 빈 섹션들에 퍼뜨리되 글 id 시드로 시작 위치를 바꾸고,
# (3) 섹션 안에서 놓이는 자리를 ①소제목 바로 아래(top) ②첫 문단(첫 블록) 뒤(after_first) ③섹션 끝(end) 중에서 사진마다 다르게 배정합니다.
# 시드가 같으면(같은 글을 다시 만들면) 결과도 같습니다. FAQ·상품 카드(링크:)·"세부 글" 섹션에는 넣지 않고, 한 섹션에 2장 이상은 넣지 않습니다
# (섹션이 사진보다 적을 때만 예외). 이 함수들은 계산만 하며 HTML을 만들지 않습니다.
BODY_IMAGE_SLOTS = ("top", "after_first", "end")
BODY_IMAGE_CLASS = "seo-body-img"  # 워드프레스 AI 본문 사진 블록의 className (사이트 "추가 CSS"가 이 클래스로 폭을 줄임)
_BODY_IMAGE_SKIP_HEADING_RE = re.compile(r"세부\s*글")  # 혜택주머니 "이 주제의 세부 글"


def assign_body_image_slots(
    headings: list, eligible: list, units: list, image_headings: list, seed: str = "", avoid_end=None,
) -> dict:
    """섹션 번호 → {자리: [사진 번호…]}를 돌려줍니다.
    headings: 섹션 소제목 글자 목록 / eligible: 사진을 넣어도 되는 섹션인지(True/False) / units: 섹션마다 (첫 블록으로 칠 조각 수, 전체 조각 수) /
    image_headings: 사진별로 붙일 소제목("" = 모름) / avoid_end: "섹션 끝"에 못 넣는 섹션 번호 모음(외부 이미지가 이미 끝에 있을 때)."""
    n_img = len(image_headings)
    if not n_img or not headings:
        return {}
    avoid_end = avoid_end or set()
    elig = [i for i, ok in enumerate(eligible) if ok]
    if not elig:
        return {}
    norm = _norm_heading
    section_of: dict = {}
    taken: set = set()
    for exact in (True, False):  # 정확히 같은 소제목을 먼저 전부 배정하고, 남은 사진만 포함 관계로 찾습니다
        for j, ih in enumerate(image_headings):
            key = norm(ih or "")
            if j in section_of or not key:
                continue
            for i in elig:
                h = norm(headings[i])
                if i in taken or not h:
                    continue
                if (h == key) if exact else (key in h or h in key):
                    section_of[j] = i
                    taken.add(i)
                    break
    rest = [j for j in range(n_img) if j not in section_of]
    if rest:
        free = [i for i in elig if i not in taken]
        multi = [i for i in free if units[i][1] > units[i][0]]  # 문단이 2개 이상이라 ②(첫 문단 뒤)도 쓸 수 있는 섹션
        if len(multi) >= len(rest):
            free = multi  # 충분하면 이런 섹션에 우선 퍼뜨려 사진마다 다른 자리를 만들기 쉽게
        pos_rng = random.Random(f"photo_v2_pos|{seed}")
        pool = free or elig
        start = pos_rng.randrange(len(pool))
        if len(free) >= len(rest):
            step = len(free) / len(rest)
            spots = [free[(start + int(m * step)) % len(free)] for m in range(len(rest))]
        else:  # 빈 섹션이 사진보다 적을 때만 겹칩니다
            spots = list(free) + [elig[(start + m) % len(elig)] for m in range(len(rest) - len(free))]
        for j, sp in zip(rest, sorted(spots)):
            section_of[j] = sp
    slot_order = list(BODY_IMAGE_SLOTS)
    random.Random(f"photo_v2_slot|{seed}").shuffle(slot_order)
    plan: dict = {}
    used_total = {s: 0 for s in BODY_IMAGE_SLOTS}  # 이 글 전체에서 자리별로 몇 장 썼는지(되도록 사진마다 다른 자리가 되게)
    for j in range(n_img):
        i = section_of[j]
        lead, total = units[i]
        used = plan.get(i, {})

        def feasible(s: str) -> bool:
            if s == "after_first":
                return total > lead  # 문단이 1개뿐이면 ②는 못 씀(③으로 대신)
            if s == "end":
                return i not in avoid_end  # 외부 이미지가 이미 섹션 끝에 있으면 ③은 피함
            return True

        want = slot_order[j % len(slot_order)]
        if feasible(want) and want not in used:
            pick = want
        else:
            ok = [s for s in slot_order if feasible(s) and s not in used]
            if ok:  # 못 쓰는 자리면, 이 글에서 가장 덜 쓴 자리로
                pick = min(ok, key=lambda s: (used_total[s], slot_order.index(s)))
            else:
                pick = "end" if feasible("end") else ("top" if "top" not in used else "after_first")
        used_total[pick] += 1
        plan.setdefault(i, {}).setdefault(pick, []).append(j)
    return plan


def section_block_units(section: dict) -> tuple:
    """섹션 본문의 (첫 블록으로 칠 조각 수, 전체 조각 수). 맨 앞의 "### 작은 소제목" 조각은 첫 문단과 한 덩어리로 칩니다."""
    segs = _table_segments(section.get("table"))
    if not segs:
        segs = [("text", section.get("body") or "")]
    total, lead = 0, None
    for kind, payload in segs:
        if kind == "table":
            total += 1
            if lead is None:
                lead = 1
            continue
        pieces = _split_paragraphs(payload)
        total += len(pieces)
        if lead is None and pieces:
            lead = 0
            for k, _p in pieces:
                lead += 1
                if k != "h3":
                    break
    return max(1, lead or 1), total


def plan_body_image_slots(sections: list, images: list, seed: str = "", avoid_end=None, skip=None) -> dict:
    """parse_sections()["sections"]와 본문 사진 목록({"path","alt","heading"})으로 assign_body_image_slots 결과를 만듭니다.
    넣지 않는 섹션: FAQ · 상품 카드(링크:) · "세부 글" · 본문이 빈 섹션(skip(소제목)이 True인 섹션도 뺌). 해당하는 섹션이 하나도 없으면 FAQ·상품만 뺍니다."""
    def _ok(s: dict, need_body: bool) -> bool:
        if s.get("is_faq") or s.get("is_product"):
            return False
        h = strip_markup(s.get("heading", ""))
        if need_body and (_BODY_IMAGE_SKIP_HEADING_RE.search(h) or (skip and skip(h)) or not (s.get("body") or "").strip()):
            return False
        return True

    eligible = [_ok(s, True) for s in sections]
    if not any(eligible):
        eligible = [_ok(s, False) for s in sections]
    return assign_body_image_slots(
        [s.get("heading", "") for s in sections], eligible, [section_block_units(s) for s in sections],
        [(im.get("heading") or "") for im in images], seed, avoid_end,
    )


def _body_image_html_naver(img: dict) -> str:
    return (
        f'<img src="{img["path"]}" alt="{img.get("alt", "")}" '
        f'style="width:100%; max-width:720px; border-radius:12px; display:block; margin:16px auto;"><br>'
    )


_WP_COMMENT_RE = re.compile(r"<!--\s*(/?)wp:[a-z0-9/_-]+\b[^>]*?(/?)-->")


def _split_after_units_naver(body_html: str, n: int) -> tuple:
    """네이버 본문 HTML(조각 사이가 NAVER_GAP)에서 앞 n조각과 나머지로 나눕니다. 나머지는 NAVER_GAP으로 시작합니다. 못 나누면 (전체, "")."""
    pos = -1
    for _ in range(max(1, n)):
        pos = body_html.find(NAVER_GAP, pos + 1)
        if pos < 0:
            return body_html, ""
    return body_html[:pos], body_html[pos:]


def _split_after_units_wp(blocks: str, n: int) -> tuple:
    """워드프레스 블록 문자열에서 앞 n개의 최상위 블록과 나머지로 나눕니다(안쪽에 블록이 든 인용구·목록도 한 개로 셈). 못 나누면 (전체, "")."""
    depth, units = 0, 0
    for m in _WP_COMMENT_RE.finditer(blocks):
        if m.group(1):
            depth -= 1
        elif not m.group(2):
            depth += 1
        if depth <= 0 and (m.group(1) or m.group(2)):
            depth = 0
            units += 1
            if units >= max(1, n):
                end = m.end()
                if blocks[end:end + 1] == "\n":
                    end += 1
                return blocks[:end], blocks[end:]
    return blocks, ""


def _wp_disclosure_block() -> str:
    """워드프레스 대가성 문구: wp:html 블록 + <p> 인라인 스타일(가운데 정렬·얇은 #cb6615 테두리 박스·
    글자색 #cb6615·줄간격 1.6). 박스는 글자 폭에 맞게(width:fit-content, 좌우 여백 32px) 줄이고 margin auto로 가운데에 둡니다.
    글자 크기·굵기는 지정하지 않아 본문과 똑같이 보입니다. 위 16px·아래 28px 여백으로 위(최종 업데이트 줄)·아래(본문)와
    떼어 놓습니다. <p>에 style이 있어 core/paragraph에 넣으면 블록 검증이 깨지므로 wp:html 블록으로 감쌉니다."""
    line1, line2 = DISCLOSURE_WORDPRESS_LINES
    return _wp_block(
        "html",
        '<p style="text-align:center; border:1px solid #cb6615; color:#cb6615; padding:12px 32px; line-height:1.6; '
        'width:fit-content; max-width:100%; box-sizing:border-box; margin:16px auto 28px;">'
        f"{line1}<br>{line2}</p>",
    )


# ---------- 네이버·싼세계·혜택주머니 도입부 문단 나누기 (split_intro=True 일 때만) ----------
# 도입부(첫 소제목 앞 글)가 한 덩어리로 오면 문장 단위로 끊어 최대 3문단으로 묶습니다.
#   문장 수(기준·출처 괄호 줄 제외) 1 → 1문단 / 2~4 → 2문단 / 5 이상 → 3문단. 문단마다 문장 수는 고르게.
# 화면·복사 출력(최종본을 새로 만들 때)에만 적용되고, 원고(raw)·저장된 데이터는 바꾸지 않습니다.
INTRO_MAX_PARAS = 3  # 도입부 최대 문단 수
INTRO_P_MARGIN_WP = "1.2em"  # 싼세계·혜택주머니 도입부 문단 아래 여백
_INTRO_NEW_PARA_WORDS = ("다만", "하지만", "그런데", "그래서")  # 이 말로 시작하는 문장은 (한도 안에서) 새 문단의 첫 문장
_INTRO_CLOSERS = "\"'”’」』"
_INTRO_NOTE_RE = re.compile(r"^[(（][^()（）]*(?:기준|출처)[^()（）]*[)）]$")  # "(2026년 11월 기준, 출처: …)" 한 줄
_INTRO_SKIP_BLOCK_RE = re.compile(r"^(?:[-*>#|]|\d+[.)][ \t]|링크[ \t]*[:：]|\{\{|<)")  # 목록·인용·표·링크·표시 줄은 안 건드림


def _split_sentences(text: str) -> list[str]:
    """문장 끝(. ! ? … 뒤 공백)에서 자릅니다. 숫자 안의 점(2.5)은 뒤에 공백이 없어 안 잘리고,
    괄호 ( ) [ ] 와 <태그> 안에서는 자르지 않습니다."""
    sentences, buf, depth, i, n = [], [], 0, 0, len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch in "([（":
            depth += 1
        elif ch in ")]）":
            depth = max(0, depth - 1)
        elif ch == "<" and i + 1 < n and (text[i + 1].isalpha() or text[i + 1] == "/"):
            j = text.find(">", i)
            if j != -1:
                buf.append(text[i + 1:j + 1])
                i = j
        elif ch in ".!?…" and depth == 0:
            while i + 1 < n and text[i + 1] in ".!?…":
                i += 1
                buf.append(text[i])
            while i + 1 < n and text[i + 1] in _INTRO_CLOSERS:
                i += 1
                buf.append(text[i])
            if i + 1 >= n or text[i + 1].isspace():
                sentences.append("".join(buf).strip())
                buf = []
                while i + 1 < n and text[i + 1].isspace():
                    i += 1
        i += 1
    tail = "".join(buf).strip()
    if tail:
        sentences.append(tail)
    return [s for s in sentences if s]


def _intro_para_count(n: int) -> int:
    """도입부 문장 수 n → 문단 수: 1→1, 2~4→2, 5 이상→3 (INTRO_MAX_PARAS 를 넘지 않음)."""
    if n <= 1:
        return max(n, 0)
    return min(2 if n <= 4 else 3, INTRO_MAX_PARAS)


def _intro_boundaries(sentences: list[str], k: int) -> list[int]:
    """k 문단으로 나눌 때 새 문단이 시작하는 문장 번호들(k-1개). 기본은 고르게(큰 문단이 앞: 5문장 → 2·2·1).
    '다만·하지만·그런데·그래서'로 시작하는 문장이 있으면, 문단 수는 그대로 두고 가능한 한 그 문장에서 새 문단이 시작하도록
    경계를 옮깁니다(한 문단이 고른 크기보다 2문장 넘게 커지지 않는 범위에서만, 안 되면 기본 그대로)."""
    n = len(sentences)
    base, extra = divmod(n, k)
    sizes = [base + (1 if i < extra else 0) for i in range(k)]
    default, pos = [], 0
    for sz in sizes[:-1]:
        pos += sz
        default.append(pos)
    cands = {i for i in range(1, n) if sentences[i].startswith(_INTRO_NEW_PARA_WORDS)}
    if not cands or k < 2:
        return default
    limit = -(-n // k) + 1  # 한 문단 최대 문장 수(고른 크기 + 1)
    best, best_key = default, None
    for combo in itertools.combinations(range(1, n), k - 1):
        cuts = (0,) + combo + (n,)
        lens = [cuts[i + 1] - cuts[i] for i in range(k)]
        if max(lens) > limit:
            continue
        key = (-len(cands.intersection(combo)), sum(abs(a - b) for a, b in zip(lens, sizes)), tuple(-x for x in lens))  # 같으면 큰 문단이 앞
        if best_key is None or key < best_key:
            best, best_key = list(combo), key
    return best


def _group_intro_sentences(sentences: list[str]) -> list[str]:
    """문장들을 최대 3문단으로 묶습니다(규칙은 위 주석). 기준·출처 괄호 문장은 문단 수에 안 세고 맨 끝에 따로 한 줄."""
    notes = [s for s in sentences if _INTRO_NOTE_RE.match(s)]
    body = [s for s in sentences if not _INTRO_NOTE_RE.match(s)]
    if not body:
        return notes
    cuts = [0] + _intro_boundaries(body, _intro_para_count(len(body))) + [len(body)]
    return [" ".join(body[cuts[i]:cuts[i + 1]]) for i in range(len(cuts) - 1)] + notes


def split_intro_paragraphs(intro: str) -> str:
    """도입부 글을 빈 줄로 나뉜 짧은 문단들로 바꿔 돌려줍니다(문단 사이는 빈 줄 한 줄).
    - 이미 문단이 나뉘어 있으면(일반 글 문단이 2개 이상, 기준·출처 줄은 세지 않음) 그대로 둡니다.
    - 목록·인용구·표·소제목·링크·{{…}} 표시가 든 블록은 건드리지 않습니다."""
    text = (intro or "").strip()
    if not text:
        return intro
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    plain = []  # 나눌 수 있는 일반 글 블록 번호(기준·출처 줄만 있는 블록은 제외)
    for idx, b in enumerate(blocks):
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        if any(_INTRO_SKIP_BLOCK_RE.match(l) for l in lines):
            continue
        if all(_INTRO_NOTE_RE.match(l) for l in lines):
            continue
        plain.append(idx)
    if len(plain) != 1:
        return intro  # 이미 나뉨(2개 이상) 또는 나눌 글 없음
    idx = plain[0]
    lines = [l.strip() for l in blocks[idx].splitlines() if l.strip()]
    sentences = []
    for l in lines:
        sentences.extend(_split_sentences(l))
    blocks[idx] = "\n\n".join(_group_intro_sentences(sentences))
    return "\n\n".join(blocks)


def _build_wordpress_blocks(
    title, cover_img_tag, parsed, product_images, bottom_images, disclosure: bool = True, body_images=None,
    intro_links=None, cta_banner_url=None, external_images=None, body_image_seed: str = "", split_intro: bool = False,
) -> str:
    """워드프레스용 최종본: 표지 → 고지문구 → 인트로 → 상품/FAQ/일반 섹션 → 맨 아래 본문 이미지, 전부 표준 블록.
    external_images(싼세계 "외부 이미지")가 있으면 지정한 소제목 섹션 끝(다음 소제목 바로 앞)에 이미지 블록을 넣습니다(캡션이 있으면 figcaption).
    disclosure=False면 고지문구를 빼고, body_images가 있으면(워드프레스 일반) 섹션 사이에 고르게 이미지 블록을 넣습니다.
    intro_links가 있으면(워드프레스 일반 도입부의 쿠팡 링크) 인트로 바로 뒤에 구매 버튼으로 넣습니다."""
    body_parts = []
    product_i = 0
    ext_by_idx, _ext_unmatched = match_external_images(parsed["sections"], external_images)
    # 본문 사진 위치: 소제목에 맞춰 섹션을 고르고, 섹션 안 자리(소제목 아래/첫 문단 뒤/섹션 끝)는 글 id 시드로 사진마다 다르게 배정합니다.
    # 외부 이미지가 이미 섹션 끝에 있는 섹션은 "섹션 끝"을 피합니다(사진끼리 바로 붙지 않게).
    body_plan = plan_body_image_slots(parsed["sections"], body_images or [], body_image_seed, avoid_end=set(ext_by_idx)) if body_images else {}

    def _body_img_block(k: int) -> str:
        return gutenberg_image_block(body_images[k]["path"], body_images[k].get("alt", ""), class_name=BODY_IMAGE_CLASS)

    for idx, section in enumerate(parsed["sections"]):
        slots = body_plan.get(idx) or {}
        if section["is_faq"]:
            body_parts.append(_wp_faq_blocks(section))
        elif section["is_product"]:
            images = product_images[product_i] if product_i < len(product_images) else []
            product_i += 1
            body_parts.append(_wp_product_blocks(section, images, cta_banner_url))
        elif slots:
            body_parts.append(_wp_plain_blocks(section, {s: "".join(_body_img_block(k) for k in slots[s]) for s in ("top", "after_first") if slots.get(s)}))
        else:
            body_parts.append(_wp_plain_blocks(section))
        for ext in ext_by_idx.get(idx, []):
            body_parts.append(gutenberg_image_block(
                ext["path"], ext.get("alt") or strip_markup(section["heading"]), caption=(ext.get("caption") or "").strip() or None
            ))
        for k in slots.get("end", []):
            body_parts.append(_body_img_block(k))
    disclosure = _wp_disclosure_block() if disclosure else ""
    bottom = "".join(gutenberg_image_block(i["path"], i["alt"]) for i in (bottom_images or []))
    # <h1>은 글 제목과 중복이라 발행할 때 wp_publisher가 떼어냅니다 (미리보기 화면에서만 보임).
    return (
        f"<h1>{title}</h1>"
        f"{_wp_cover_block(cover_img_tag)}"
        f"{disclosure}"
        f"{_wp_paragraphs(parsed['intro'], p_margin=INTRO_P_MARGIN_WP if split_intro else None)}"
        f"{''.join(_wp_button(CTA_BUTTON_TEXT['wordpress'], url) for url in (intro_links or []))}"
        f"{''.join(body_parts)}"
        f"{bottom}"
    )


_SWEEP_P_RE = re.compile(r"<p(?:\s[^>]*)?>(.*?)</p>", re.DOTALL)
_SWEEP_LINK_LINE_RE = re.compile(r"^\s*링크\s*[:：]")
_SWEEP_LINE_SPLIT_RE = re.compile(r"\n|<br\s*/?>", re.IGNORECASE)
_SWEEP_SHOP_RE = re.compile(r"[ \t]*\{\{SHOP:.*?\}\}", re.DOTALL)
_SWEEP_BRACKET_LINK_RE = re.compile(r"[ \t]*\[[^\[\]\n]*링크[^\[\]\n]*\]")
_SWEEP_EMPTY_P_RE = re.compile(r"<p(?:\s[^>]*)?>\s*</p>")
_SWEEP_EMPTY_INLINE_RE = re.compile(r"<(strong|b|em|span)(?:\s[^>]*)?>\s*</\1>")


def _sweep_link_placeholders_naver(content: str) -> str:
    """네이버 SHOP 전용 마지막 안전망 — 링크 자리 표시가 어디에 남아 있어도 최종 HTML에서 지웁니다.
    1) {{SHOP:…}} 조각  2) 줄 첫머리가 "링크:"/"링크 :"/"링크："인 줄(문단 안의 줄 포함; http 주소여도 노출하지 않음)
    3) [구매링크]·[쿠팡링크]·[링크] 같은 대괄호 안에 "링크"가 든 글자  4) 그 결과 생긴 빈 문단 정리, 연속 빈 줄(NAVER_GAP)은 1개로, 글 끝 빈 줄은 제거.
    상품 카드의 정상 링크(<a href="http…">→ 오늘 가격 보기</a>)는 <p> 밖이고 "링크:"로 시작하지 않아 그대로 남습니다."""
    if "{{SHOP" not in content and "링크" not in content:
        return content

    def _fix_paragraph(m: re.Match) -> str:
        inner = m.group(1)
        if "링크" not in inner:
            return m.group(0)
        sep_parts = re.split(r"(\n|<br\s*/?>)", inner, flags=re.IGNORECASE)  # 줄과 구분자를 번갈아 담음
        lines, seps = sep_parts[0::2], sep_parts[1::2]
        kept = [(ln, seps[i] if i < len(seps) else "") for i, ln in enumerate(lines) if not _SWEEP_LINK_LINE_RE.match(re.sub(r"<[^>]+>", "", ln))]
        if len(kept) == len(lines):
            return m.group(0)
        if not kept:
            return ""
        # 지운 줄 때문에 구분자가 남거나 모자라지 않게, 남은 줄 사이에만 원래 구분자를 씁니다.
        rebuilt = ""
        for i, (ln, sep) in enumerate(kept):
            rebuilt += ln + (sep if i < len(kept) - 1 and sep else ("<br>" if i < len(kept) - 1 else ""))
        return m.group(0).replace(inner, rebuilt, 1)

    out = _SWEEP_P_RE.sub(_fix_paragraph, content)
    out = _SWEEP_SHOP_RE.sub("", out)
    out = _SWEEP_BRACKET_LINK_RE.sub("", out)
    out = _SWEEP_EMPTY_INLINE_RE.sub("", out)
    out = _SWEEP_EMPTY_P_RE.sub("", out)  # {{SHOP:}}·[구매링크]만 있던 문단 (NAVER_GAP은 <br>이 있어 남음)
    out = re.sub(r"(?:" + re.escape(NAVER_GAP) + r"){2,}", NAVER_GAP, out)
    out = re.sub(r"(?:" + re.escape(NAVER_GAP) + r")+\Z", "", out)
    return out


def _build_shop_html(
    title: str,
    cover_img_tag: str,
    raw: str,
    product_images: list[list[dict]] = None,
    platform: str = "wordpress",
    bottom_images: list[dict] | None = None,
    related_posts: list[dict] | None = None,
    disclosure: bool = True,
    body_images: list[dict] | None = None,
    product_link_prefix: str | None = None,
    wp_cta_banner_url: str | None = None,
    strip_link_placeholders: bool = False,
    external_images: list | None = None,
    body_image_seed: str = "",
    split_intro: bool = False,
) -> str:
    """
    숍 채널용(그리고 이 레이아웃을 재사용하는 "네이버 일반"·"워드프레스 일반" 정보 글용) 최종 HTML을 만듭니다.
    표지 → 법정 고지문구 → 인트로 → (상품 카드 또는 일반 섹션 반복) 순서입니다. product_images는
    상품 섹션 순서대로 매칭되는 "이미지 풀에서 선택된 이미지 목록의 목록"입니다 —
    product_images[0]이 첫 번째 상품 섹션에 쓸 이미지들(0장 이상, 여러 장도 가능)이고, 각 항목은
    {"path", "alt"} 형태입니다. 상품이 아닌 일반 섹션(인트로 다음 총평 등)에는 이미지를 넣지 않습니다.

    disclosure: False면 쿠팡파트너스·쇼핑커넥트 대가성 문구를 안 넣습니다 — 구매 링크가 없는
    "네이버 일반(꿀팁)" 채널처럼 광고가 아닌 글에 씁니다.
    body_images: [{"path","alt"}, ...]가 있으면, 상품 매칭이 아니라 롱텀처럼 섹션(소제목) 사이에
    고르게 나눠 삽입합니다 — "네이버 일반" 채널처럼 상품 슬롯 없이 AI로 만든 본문 이미지를
    쓸 때 사용합니다. product_images/bottom_images와 동시에 쓰지 않습니다. 각 항목의 "heading"(사진이 붙을 소제목, 없으면 "")과
    body_image_seed(글 id)로 사진 위치를 정합니다(plan_body_image_slots 참고) — 같은 시드면 같은 위치입니다.
    product_link_prefix: 주면 "링크:" 줄이 이 주소로 시작하는 섹션만 상품(구매 버튼)으로 봅니다 —
    "워드프레스 일반"에서 쿠팡 링크(COUPANG_LINK_PREFIX)가 있는 섹션에만 버튼을 넣을 때 씁니다.
    wp_cta_banner_url: 주면(워드프레스 SHOP 전용, 워드프레스 출력에만 적용) 상품 끝 구매 버튼 대신 "배너 이미지 + 쿠팡 HTML 코드"를
    넣습니다. None(기본)이면 예전 그대로입니다 — 네이버 SHOP·네이버 일반·워드프레스 일반·롱텀은 이 값을 주지 않습니다.
    strip_link_placeholders: True면(네이버 SHOP 전용, 네이버 출력에만 적용) 링크 자리 표시를 출력에서 뺍니다 — {{SHOP:…}} 줄은 통째로
    지우고, 비었거나 [구매링크] 같은 "링크:" 값이면 "→ 오늘 가격 보기" 링크를 만들지 않습니다(링크는 사용자가 에디터에서 직접 넣음).
    False(기본)면 예전 그대로입니다.

    워드프레스는 API로 직접 들어가므로 화려한 카드 스타일을 그대로 쓰고, 네이버는 실제
    붙여넣기 테스트에서 <h2>·<div>·박스 스타일이 전부 사라지는 걸 확인했기 때문에
    "굵게+색깔 텍스트 + 실제 줄바꿈"만으로 최대한 단순하게 구성합니다.
    """
    raw = (raw or "").replace("\r\n", "\n").replace("\r", "\n")  # 원고 줄바꿈을 먼저 통일(윈도우 \r\n 대비)
    product_images = product_images or []
    if platform in ("naver", "wordpress"):
        # 원고 끝 "태그: #… " 줄은 본문에서 빼고, 네이버는 최종 화면 상단의 "추천 태그" 박스로,
        # 워드프레스는 발행 시 실제 태그로 쓰입니다(app._category_and_tag_for_publish) — 본문에는 그대로 두지 않습니다.
        raw, _ = split_tag_line(raw)
    if strip_link_placeholders and platform == "naver":
        # 한 줄 전체가 {{SHOP:…}}인 줄을 통째로 지웁니다. 문단은 빈 줄 기준으로 나뉘고 빈 블록은 건너뛰므로 NAVER_GAP이 겹치지 않습니다.
        raw = SHOP_LINE_RE.sub("", raw)
    parsed = parse_sections(raw)

    if not disclosure:
        # disclosure=False는 지금은 "네이버 일반(꿀팁)"처럼 구매 링크가 없는 채널에서만 씁니다.
        # 원고에 실수로 "가격:"/"링크:" 형태의 줄이 섞여 들어와도(예: 참고 링크를 그렇게 적은
        # 경우) 상품 카드·구매 버튼·가격 배지가 나가지 않도록 여기서 안전하게 막습니다.
        for section in parsed["sections"]:
            section["is_product"] = False
    intro_links = []
    if disclosure and product_link_prefix:
        for section in parsed["sections"]:
            if section["is_product"] and not section["link"].startswith(product_link_prefix):
                section["is_product"] = False
        intro_links = _pop_intro_links(parsed, product_link_prefix)

    if split_intro:  # 네이버·싼세계만: 도입부를 짧은 문단으로 (화면·복사 출력에만, 원고는 그대로)
        parsed["intro"] = split_intro_paragraphs(parsed["intro"])

    if platform == "wordpress":
        return _build_wordpress_blocks(
            title, cover_img_tag, parsed, product_images, bottom_images, disclosure=disclosure, body_images=body_images,
            intro_links=intro_links, cta_banner_url=wp_cta_banner_url, external_images=external_images,
            body_image_seed=body_image_seed, split_intro=split_intro,
        )

    # body_images가 있으면(상품 슬롯이 없는 정보 글) 소제목·자리(소제목 아래/첫 문단 뒤/섹션 끝)를 글 id 시드로 정합니다(plan_body_image_slots).
    body_plan = plan_body_image_slots(parsed["sections"], body_images or [], body_image_seed) if body_images else {}

    body_parts = []
    product_i = 0
    for idx, section in enumerate(parsed["sections"]):
        slots = body_plan.get(idx) or {}
        if section["is_faq"]:
            body_parts.append(_faq_section_html(section, platform))
        elif section["is_product"]:
            images_for_this = product_images[product_i] if product_i < len(product_images) else []
            image_tag = "".join(
                f'<img src="{img["path"]}" alt="{img.get("alt") or section["heading"]}" '
                f'style="width:100%; max-width:560px; border-radius:12px; display:block; margin:0 auto 12px;">'
                for img in images_for_this
            )
            product_i += 1
            body_parts.append(_product_card_html(section, image_tag, platform, drop_empty_cta=strip_link_placeholders))
        elif slots and platform == "naver":
            body_parts.append(_plain_section_html(section, platform, {
                s: "".join(_body_image_html_naver(body_images[k]) for k in slots[s]) for s in ("top", "after_first") if slots.get(s)
            }))
        else:
            body_parts.append(_plain_section_html(section, platform))

        for k in slots.get("end", []):
            img = body_images[k]
            body_parts.append(
                f'<img src="{img["path"]}" alt="{img.get("alt", "")}" '
                f'style="width:100%; max-width:720px; border-radius:12px; display:block; margin:16px auto;">'
                + ("<br>" if platform == "naver" else "")
            )

    intro_html = _paragraphs_plain(parsed["intro"]) if platform == "naver" else _paragraphs_to_html(parsed["intro"])
    intro_block = f"{intro_html}{NAVER_GAP}" if platform == "naver" and intro_html else intro_html

    # 워드프레스: bottom_images([{"path","alt"}])를 본문 맨 아래에 업로드 순서 그대로 Gutenberg
    # 이미지 블록으로 붙입니다 (소제목별 매칭 없음).
    bottom_html = (
        "".join(gutenberg_image_block(i["path"], i["alt"]) for i in (bottom_images or []))
        if platform == "wordpress"
        else ""
    )

    final_html = (
        f"<h1>{title}</h1>"
        f"{cover_img_tag}"
        f"{_disclosure_html(platform) if disclosure else ''}"
        f"{intro_block}"
        f"{''.join(body_parts)}"
        f"{_related_section_html(related_posts or []) if platform == 'naver' else ''}"
        f"{bottom_html}"
    )
    if strip_link_placeholders and platform == "naver":
        final_html = _sweep_link_placeholders_naver(final_html)
    return final_html


def build_shop_html(
    title: str,
    cover_img_tag: str,
    raw: str,
    product_images: list[list[dict]] = None,
    platform: str = "wordpress",
    bottom_images: list[dict] | None = None,
    related_posts: list[dict] | None = None,
    disclosure: bool = True,
    body_images: list[dict] | None = None,
    product_link_prefix: str | None = None,
    wp_cta_banner_url: str | None = None,
    shop_links: dict | None = None,
    strip_link_placeholders: bool = False,
    external_images: list | None = None,
    body_image_seed: str = "",
    split_intro: bool = False,
) -> str:
    """_build_shop_html(위 설명 참고)에 "내 숍 추천 글" 연결({{SHOP:키워드|문구}}) 처리를 더한 진입점입니다.
    shop_links: {키워드: {"title","url","rule"} 또는 None(못 찾음)} — 네이버 일반·워드프레스 일반이 넘깁니다.
    None(기본)이면 {{SHOP:}} 줄을 전혀 처리하지 않아 다른 채널은 예전과 똑같이 동작합니다."""
    kwargs = dict(
        title=title, cover_img_tag=cover_img_tag, raw=raw, product_images=product_images, platform=platform,
        bottom_images=bottom_images, related_posts=related_posts, disclosure=disclosure, body_images=body_images,
        product_link_prefix=product_link_prefix, wp_cta_banner_url=wp_cta_banner_url,
        strip_link_placeholders=strip_link_placeholders, external_images=external_images, body_image_seed=body_image_seed,
        split_intro=split_intro,
    )
    if shop_links is None:
        return _build_shop_html(**kwargs)
    # {{SHOP:}} 줄이 앞뒤 문단과 붙어 있어도 한 블록으로 분리되도록 앞뒤에 빈 줄을 채웁니다.
    kwargs["raw"] = SHOP_LINE_RE.sub(lambda m: f"\n\n{m.group(0).strip()}\n\n", (raw or "").replace("\r\n", "\n").replace("\r", "\n"))
    token = _shop_links_ctx.set(shop_links)
    try:
        return _build_shop_html(**kwargs)
    finally:
        _shop_links_ctx.reset(token)


def has_link_line(raw: str) -> bool:
    """원고에 "링크:" 줄(직접 제휴 링크)이 하나라도 있는지 — 네이버 일반에서 대가성 문구를 켤지 판단합니다."""
    return bool(LINK_LINE_RE.search(raw or ""))


def has_shop_link(raw: str, platform: str) -> bool:
    """V5 "숍글 자동 판정": 링크가 들어간 글이면 True.
    네이버 = 원고에 "링크:" 줄이 있음 / 워드프레스(싼세계) = 쿠팡 링크("링크: https://link.coupang.com/…") 또는
    쿠팡파트너스 HTML 코드("링크: <a …쿠팡…>" / "<iframe …쿠팡…>")가 있음. 읽기만 합니다."""
    if platform == "naver":
        return has_link_line(raw)
    if has_coupang_link(raw):
        return True
    for m in LINK_LINE_RE.finditer(collapse_link_html(raw or "")):
        v = m.group(1).strip()
        if classify_link_value(v) == "html" and _host_in_any(v, CTA_ALLOWED_DOMAINS):
            return True
    return False


def _host_in_any(code: str, domains: tuple) -> bool:
    """HTML 코드 안에 허용 도메인(쿠팡)이 글자로 들어 있는지 — 감지용(안전 검사는 analyze_coupang_html이 따로 합니다)."""
    low = (code or "").lower()
    return any(d in low for d in domains)

