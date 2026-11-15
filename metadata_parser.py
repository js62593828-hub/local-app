"""
글쓰기 시작프롬프트 v5의 "📎 메타데이터" 섹션(사람이 저장해둔 메모 텍스트)을 파싱해서
- 메타 디스크립션
- 카테고리 제안 / 태그 제안
- 대표 이미지(표지) alt 텍스트 제안
- 본문 이미지 alt 텍스트 제안 목록 (소제목별)
을 구조화된 값으로 뽑아냅니다. 형식이 살짝 달라도(공백, 줄바꿈 차이 등) 최대한 관대하게
파싱하고, 못 찾은 항목은 빈 값으로 둡니다 — 이 파싱이 실패해도 전체 발행 흐름은 계속되어야 하므로.
"""
import re


def _content_type(value: str) -> str:
    """"글 유형:" 줄의 값 → "A"/"B"/"C"/"". 예: "A 해결형", "B 점검·시즌형", "C 결정형", "해결형", "결정형"."""
    if not value:
        return ""
    m = re.match(r"\s*([ABCabc])(?![A-Za-z])", value)
    if m:
        return m.group(1).upper()
    if "해결" in value:
        return "A"
    if "점검" in value or "시즌" in value:
        return "B"
    if "결정" in value:
        return "C"
    return ""


def parse_metadata_memo(text: str) -> dict:
    """
    반환 형식:
    {
        "meta_description": str,
        "category": str,
        "tag": str,
        "cover_alt": str,
        "body_alts": [str, ...],  # 소제목별 이미지 설명, 순서 무관(랜덤 매칭용)
        "cover_badge": str,   # 표지 시안 A용 — 아래 4개는 없으면 빈 문자열
        "cover_line1": str,   # [대괄호] 단어는 강조색으로 표시할 부분 (image_gen.py가 처리)
        "cover_line2": str,
        "cover_sub": str,
        "body_alt_pairs": [(소제목, 장면), ...],  # body_alts와 같은 순서(메모에 적힌 순서) — 롱텀이 장면을 소제목 밑에 붙일 때 씀
    }
    """
    result = {
        "meta_description": "",
        "category": "",
        "tag": "",
        "cover_alt": "",
        "body_alts": [],
        "cover_badge": "",
        "cover_line1": "",
        "cover_line2": "",
        "cover_sub": "",
        "cover_color": "",  # 숍 표지 색 이름(예: "와인"). 없으면 빈 문자열 → 랜덤
        "cover_scene": "",  # 표지 AI 사진의 장면 문장("표지 장면:"). alt에는 쓰지 않음. 없으면 빈 문자열
        "body_alt_pairs": [],
        "content_type": "",  # 메모의 "글 유형:" 줄 → "A"(해결형) / "B"(점검·시즌형) / "C"(결정형). 없거나 못 알아보면 빈 문자열
    }
    if not text:
        return result

    def _grab(label_patterns: list[str]) -> str:
        for label in label_patterns:
            m = re.search(rf"{label}\s*[:：]\s*(.+)", text)
            if m:
                return m.group(1).strip().strip("*").strip()
        return ""

    result["meta_description"] = _grab(["메타\\s*디스크립션", "메타\\s*설명"])
    result["category"] = _grab(["카테고리\\s*제안", "카테고리"])
    result["tag"] = _grab(["태그\\s*제안", "태그"])
    result["content_type"] = _content_type(_grab([r"글\s*유형"]))
    result["cover_alt"] = _grab([r"대표\s*이미지\(?표지\)?\s*alt\s*텍스트\s*제안", r"표지\s*alt\s*텍스트\s*제안"])

    # 표지 시안 A용 문구 4줄. 기존 항목들과 같은 방식으로 관대하게 파싱합니다.
    result["cover_badge"] = _grab([r"표지\s*배지"])
    result["cover_line1"] = _grab([r"표지\s*1\s*줄"])
    result["cover_line2"] = _grab([r"표지\s*2\s*줄"])
    result["cover_sub"] = _grab([r"표지\s*보조"])
    result["cover_color"] = _grab([r"표지\s*색(?:상)?"])
    # 표지 AI 사진의 장면 문장(alt와 별개). "표지 장면:" 항목이 없으면 빈 문자열.
    result["cover_scene"] = _grab([r"표지\s*장면"])

    # 본문 이미지 alt 텍스트 제안: "## 소제목" → 설명  형태의 줄들을 전부 뽑습니다.
    body_alts = re.findall(r'"##[^"]*"\s*(?:→|->)\s*(.+)', text)
    result["body_alts"] = [b.strip() for b in body_alts if b.strip()]

    # 롱텀용: 같은 줄들을 (소제목, 장면) 쌍으로도 뽑습니다. body_alts와 같은 줄·같은 순서(빈 장면 줄은 제외)입니다.
    pairs = re.findall(r'"##([^"]*)"\s*(?:→|->)\s*(.+)', text)
    result["body_alt_pairs"] = [(h.strip(), s.strip()) for h, s in pairs if s.strip()]

    return result


# ── 생활레이더 전용 메모 항목 (parse_metadata_memo의 결과는 바꾸지 않고, 이 함수만 따로 읽습니다) ──────────────
RADAR_POST_TYPES = ("속보형", "계산형", "실천형", "정리글")
RADAR_CARD_KINDS = ("전후", "체크", "계산")
RADAR_CARD_TITLE_MAX = 18   # 쓰레드 카드 제목 권장 글자 수 (넘으면 경고만)
RADAR_CARD_MAX = 2          # 본문 카드 블록 줄 수 (3번째 줄부터 무시 + 경고)


def parse_radar_memo(text: str) -> dict:
    """
    생활레이더 메모의 새 항목만 읽습니다.
      글 유형: 속보형|계산형|실천형|정리글   → post_type(글자 그대로, 앞뒤 공백·** 만 제거)
      카드 제목: …(18자 이내 권장)           → card_title (넘으면 warnings에 경고만)
      카드:  (아래 줄마다 "종류 | 제목 | 내용(;로 구분) | 출처 기관[ | 넣을 소제목]", 최대 2줄)
    반환: {"post_type","post_type_known","card_title","cards":[{"kind","title","items","source","heading","error"}],"warnings":[…]}
    """
    out = {"post_type": "", "post_type_known": True, "card_title": "", "cards": [], "warnings": []}
    if not text:
        return out

    def _val(pattern: str) -> str:
        m = re.search(pattern, text, flags=re.MULTILINE)
        return m.group(1).strip().strip("*").strip() if m else ""

    out["post_type"] = _val(r"^[ \t]*[-*•]?[ \t]*\**글[ \t]*유형\**[ \t]*[:：][ \t]*(.+)$")
    if out["post_type"] and out["post_type"] not in RADAR_POST_TYPES:
        out["post_type_known"] = False
        out["warnings"].append(f"글 유형 '{out['post_type']}'은(는) 속보형·계산형·실천형·정리글 중 하나가 아니에요(그대로 저장은 해요).")
    out["card_title"] = _val(r"^[ \t]*[-*•]?[ \t]*\**카드[ \t]*제목\**[ \t]*[:：][ \t]*(.+)$")
    if len(out["card_title"]) > RADAR_CARD_TITLE_MAX:
        out["warnings"].append(
            f"카드 제목이 {len(out['card_title'])}자예요(권장 {RADAR_CARD_TITLE_MAX}자 이내). 그대로 저장해요."
        )

    lines = text.splitlines()
    head = re.compile(r"^[ \t]*[-*•#>\s]*\**카드\**[ \t]*[:：][ \t]*$")
    start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
    if start is not None:
        seen = False
        ignored = 0
        for ln in lines[start + 1:]:
            raw = ln.strip()
            if not raw:
                if seen:
                    break
                continue
            if "|" not in raw:
                break
            seen = True
            if len(out["cards"]) >= RADAR_CARD_MAX:
                ignored += 1
                continue
            raw = re.sub(r"^[-*•]\s+", "", raw)
            f = [x.strip().strip("*").strip() for x in raw.split("|")]
            kind = f[0] if f else ""
            title = f[1] if len(f) > 1 else ""
            content = f[2] if len(f) > 2 else ""
            source = f[3] if len(f) > 3 else ""
            heading = f[4] if len(f) > 4 else ""
            items = [s.strip() for s in re.split(r"[;；]", content) if s.strip()]
            error = ""
            if kind not in RADAR_CARD_KINDS:
                error = f"카드 종류 '{kind}'을(를) 알 수 없어요(전후·체크·계산 중 하나)"
            elif not title:
                error = "카드 제목이 비어 있어요"
            elif not items:
                error = "카드 내용이 비어 있어요"
            out["cards"].append({"kind": kind, "title": title, "items": items, "source": source,
                                 "heading": re.sub(r"^#+\s*", "", heading).strip(), "error": error})
        if ignored:
            out["warnings"].append(f"카드는 최대 {RADAR_CARD_MAX}줄까지만 읽어요. 3번째 줄부터 {ignored}줄은 무시했어요.")
    return out