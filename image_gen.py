"""
이미지 생성 모듈 — GPT Image mini (OpenAI) API 사용.
OPENAI_API_KEY 환경변수가 없으면 목(mock) 모드로 자리표시 이미지를 생성합니다 (비용 없음).
모든 이미지는 16:9 비율로 생성/크롭됩니다.
"""
import base64
import datetime
import io
import json
import os
import random
import re
import tempfile
import textwrap
import threading

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
USE_MOCK = not OPENAI_API_KEY

OPENAI_IMAGE_ENDPOINT = "https://api.openai.com/v1/images/generations"
OPENAI_IMAGE_EDIT_ENDPOINT = "https://api.openai.com/v1/images/edits"
# 이미지 생성 모델·품질 (image_model_switch_20261029). gpt-image-1-mini는 2026-12-01 API 종료라 gpt-image-2.5-flare로 바꿨습니다.
# 품질: low(가장 저렴) / medium / high(가장 비쌈). 표지·본문을 따로 조절할 수 있게 둘로 나눠 두었습니다.
IMAGE_MODEL = "gpt-image-2.5-flare"
COVER_IMAGE_QUALITY = "low"  # 표지(대표 이미지) 배경 사진 — 나중에 이 한 줄만 "medium"으로 바꾸면 됩니다
BODY_IMAGE_QUALITY = "low"  # 본문 사진
IMAGE_QUALITY = BODY_IMAGE_QUALITY  # 옛 이름(다른 곳이 깨지지 않게 남김) — 본문 값과 같음
# 이미지 프롬프트 길이 (image_prompt_compact_20261029). flare는 입력 글자에도 요금이 붙어서 같은 뜻을 짧게 씁니다.
# "compact" = 짧은 새 프롬프트(메모 장면 문장 + 짧은 영어 규칙 한 번씩), "legacy" = 예전 프롬프트 그대로(옛 코드는 지우지 않았습니다).
# 메모 장면 문장이 없는 옛 방식 장면(무작위·주제별 장면 목록)은 compact여도 예전 프롬프트를 씁니다. 상품 편집 경로는 해당 없음.
PROMPT_STYLE = "compact"

# GPT Image mini는 1024x1024 / 1024x1536 / 1536x1024 세 가지 크기만 지원해서,
# 가장 넓은 1536x1024로 생성한 뒤 16:9(1536x864)로 중앙 크롭합니다.
GEN_SIZE = "1536x1024"
TARGET_RATIO = (16, 9)

# 표지 AI 사진을 "스크롤을 멈추게 하는" 후킹 사진으로 만들지 여부. False면 예전 표지 프롬프트와 한 글자도 다르지 않습니다.
# 표지 배경 프롬프트(generate_cover_backgrounds 안의 _build_cover_prompt)에만 적용됩니다 — 본문 이미지는 그대로입니다.
COVER_IMPACT_MODE = True
# 숍·꿀팁 4채널(네이버 SHOP·워드프레스 SHOP·네이버 일반·워드프레스 일반) 표지 AI 사진에서 사람 얼굴을 빼는 스위치. True면 후킹을 "표정"이 아니라
# 물건·공간·흔적의 아주 가까운 컷으로 하고, 사람이 필요하면 손·뒷모습·얼굴 흐림만 허용합니다(메모의 '표지 장면:'보다 우선).
# False면 이 기능이 없던 때의 프롬프트와 한 글자도 다르지 않습니다. 롱텀 표지·본문 이미지·레퍼런스 표지에는 적용되지 않습니다.
COVER_NO_FACE = True
# 롱텀 표지 사진만 따로 켜고 끄는 스위치(숍·꿀팁은 위 COVER_IMPACT_MODE를 따름). False면 롱텀 표지 사진은 후킹 스타일 없이
# COVER_IMPACT_MODE=False 때의 예전 롱텀 스타일 문장을 쓰고, 구도 지시는 템플릿(A·B·C)별로 실제 쓰이는 영역에 맞춥니다.
# True로 바꾸면 10/6 후킹 모드 롱텀 프롬프트와 한 글자도 다르지 않습니다.
LT_COVER_IMPACT_MODE = False
# 롱텀 표지 A(숫자 히어로)·B(리포트형)에도 AI 표지 사진을 넣을지 여부. False면 예전 A·B(사진 없음)와 완전히 같게 동작합니다. C는 상관없음.
LT_AB_PHOTO = True
# 혜택주머니(롱텀) 표지를 "한 가지 모양 + 카테고리별 색"으로 통일하는 스위치(lt_cover_unified_20261115).
# True: 새 표지(compose_longterm_cover가 _lt_cover_unified로 그림), 사진 프롬프트에는 '글자 자리 비우기' 구도 문구를 넣지 않음,
#       longterm_cover_style.json에는 새 배정을 기록하지 않음. False: 이 기능이 없던 때와 똑같이 A·B·C(+LT_COVER_COLORS)로 동작.
LT_COVER_UNIFIED = True
# 롱텀(혜택주머니) 표지: 메모의 '표지 장면:'에 사람이 없으면 프롬프트에서 사람을 유도하는 문장을 빼고 "사람 없음" 블록을 넣는 스위치.
# False면 이 기능이 없던 때의 프롬프트와 한 글자도 다르지 않습니다. 장면에 사람 단어가 있으면 켜져 있어도 예전 그대로입니다.
LT_COVER_NO_PERSON_GUARD = True
# 장면 문장에 이 단어(한글은 그대로 포함되면, 일부는 앞뒤 글자를 확인)가 있으면 "사람 있음"으로 봅니다. "모습"만으로는 사람으로 보지 않습니다.
_PERSON_WORDS_KO = [
    "사람", "인물", "직원", "사장", "손님", "고객", "엄마", "아빠", "부모", "학생", "청년", "어르신", "노인", "부부", "가족",
    "남성", "여성", "남자", "여자", "뒷모습", "어깨", "상담사", "민원인", "직장인", "근로자", "할머니", "할아버지", "아기",
    "소년", "소녀", "얼굴", "아내", "남편", "아들", "딸", "자녀", "어머니", "아버지", "어린이", "환자", "주민", "행인", "시민",
    "소비자", "공무원", "아저씨", "아줌마", "실루엣", "손길", "손짓", "손자", "손녀", "머리", "신청인", "담당자",
    "약사", "점원", "상인", "기사",
]
# 일부 단어는 다른 낱말의 일부일 수 있어(아이템·주인공 등) 앞뒤를 따로 확인합니다: 아이, 주인, 손(손이·손과·손으로·손을…), 팔(팔이·팔로…)
_PERSON_REGEX_KO = re.compile(
    r"(?<![가-힣])아이(?!템|콘|보리|디어|패드|폰|스)"
    r"|주인(?!공)"
    r"|손(?:[이과은는도의에만을]|으로|가락|목|바닥)?(?![가-힣])"  # 양손·오른손·빈손처럼 앞에 글자가 붙어도 잡습니다(손해·손잡이는 뒤 글자로 제외)
    r"|(?<![가-힣])팔(?:[이과은는도의에을]|로)?(?![가-힣])"
)
_PERSON_REGEX_EN = re.compile(
    r"\b(?:person|people|persons|human|humans|hand|hands|finger|fingers|arm|arms|man|men|woman|women|boy|girl|child|children|"
    r"worker|workers|customer|customers|staff|owner|couple|family|face|faces|head|foot|feet|eye|eyes|mother|father|kid|kids|baby|senior|elderly|patient|shopper|silhouette)\b",
    re.IGNORECASE,
)


# 사람 단어 바로 뒤에 "없는/없이/없고/없음…"이 오면(예: "사람 없는 버스정류장") 또는 "사진·액자"가 붙으면(예: "가족사진") 그 단어는 세지 않습니다.
_PERSON_ANY = (
    "(?:" + "|".join(re.escape(w) for w in _PERSON_WORDS_KO) + "|" + _PERSON_REGEX_KO.pattern + "|" + _PERSON_REGEX_EN.pattern + ")"
)
_PERSON_NEGATED = re.compile(_PERSON_ANY + r"[이가은는]?\s?(?:(?:하나|한\s?명)\s?)?(?:없는데|없는|없이|없고|없음)", re.IGNORECASE)
_PERSON_PHOTO = re.compile(_PERSON_ANY + r"\s?(?:사진|액자)", re.IGNORECASE)


def _scene_mentions_person(scene_text: str) -> bool:
    """장면 문장에 사람(손·팔·뒷모습 포함) 단어가 있으면 True. 애매하면 True 쪽(= 예전 프롬프트 그대로)으로 기웁니다.
    "사람 없는 …"처럼 사람 단어 바로 뒤에 부정 표현이 붙거나 "가족사진"처럼 사진·액자가 붙은 단어는 세지 않습니다(다른 곳에 사람 단어가 있으면 True)."""
    text = _PERSON_PHOTO.sub(" ", _PERSON_NEGATED.sub(" ", scene_text or ""))
    if any(w in text for w in _PERSON_WORDS_KO):
        return True
    return bool(_PERSON_REGEX_KO.search(text) or _PERSON_REGEX_EN.search(text))

_COVER_IMPACT_STYLE_EN = (
    "High-impact editorial photograph designed to stop someone scrolling: ONE clear main subject; close or "
    "medium-close framing; if a person appears, a strong, readable, natural facial expression (surprise, worry, "
    "frustration, relief) looking at the problem or toward the camera; the problem or key moment shown vividly and "
    "concretely (e.g. the mess, the damage, the discomfort, the moment of relief); bold light-and-shadow contrast "
    "with directional light; vivid but realistic color; shallow depth of field so the subject pops; still "
    "photorealistic, not staged-looking, not AI-plastic."
)
# COVER_NO_FACE=True(숍·꿀팁 4채널)일 때의 후킹 스타일: 표정·카메라 응시 대신 물건·공간·흔적을 아주 가까이 또렷하게 보여줍니다.
_COVER_NO_FACE_STYLE_EN = (
    "High-impact editorial photograph designed to stop someone scrolling: ONE clear main subject; a very close, "
    "sharply detailed view of the problem or key moment shown through objects, spaces, and traces, vivid and "
    "concrete (e.g. condensation running down a window, mold in a corner, tangled cables, wet shoes); bold "
    "light-and-shadow contrast with directional light; vivid but realistic color; shallow depth of field so the "
    "subject pops; still photorealistic, not staged-looking, not AI-plastic."
)
_COVER_NO_FACE_STYLE_KO = (
    "스크롤을 멈추게 하는 임팩트 있는 사진으로 만들어주세요: 주인공은 하나, 문제나 핵심 순간을 물건·공간·흔적으로 아주 "
    "가까이 또렷하고 구체적으로(예: 흘러내리는 결로, 곰팡이 핀 모서리, 엉킨 전선, 젖은 신발), 방향성 있는 빛으로 강한 "
    "명암 대비, 선명하지만 현실적인 색, 주인공이 살아나는 얕은 심도. 그래도 실사처럼 자연스럽게, AI 티 나는 매끈한 느낌은 "
    "피해주세요. "
)
# 얼굴 규칙 — 금지선 근처에 두고, 메모의 '표지 장면:'(MANDATORY SCENE)과 충돌하면 이쪽이 이깁니다.
_COVER_NO_FACE_RULE_EN = (
    "═══ FACE RULE (overrides the MANDATORY SCENE above wherever they conflict) ═══ Prefer shooting with NO people "
    "at all. If a person is needed, show only hands or arms, a back view, an over-the-shoulder view, or just a "
    "partial figure at the edge of the frame. Any face must be unseen, turned away, or out of focus (blurred). "
    "NO face looking at the camera, NO face close-ups, NO exaggerated expressions. If the scene description "
    "describes how a face looks (for example a surprised look), do NOT draw that face — show the situation, the "
    "hands, or the back view instead. ═══ END OF RULE ═══ "
)
_COVER_NO_FACE_RULE_KO = (
    "═══ 얼굴 규칙(위 MANDATORY SCENE과 충돌하면 이 규칙이 우선) ═══ 사람 없이 찍는 것을 우선하세요. 사람이 필요하면 손·팔, "
    "뒷모습, 어깨 너머 시점, 화면 가장자리의 일부만 보여주세요. 얼굴은 보이지 않거나 돌린 상태이거나 흐리게(초점 밖) "
    "처리하세요. 카메라를 보는 얼굴, 얼굴 클로즈업, 과장된 표정은 금지입니다. 장면 문장에 '놀란 표정' 같은 얼굴 묘사가 "
    "있어도 얼굴을 그리지 말고 손·뒷모습·상황으로 바꿔 그리세요. ═══ 규칙 끝 ═══ "
)
# 글자가 덮는 자리를 피하는 구도 지시 — 표지 디자인별. "A" = 숍·꿀팁(시안 A, 아래쪽에 글자), "C" = 롱텀 C(위 470px만 잘라 씀).
_COVER_IMPACT_LAYOUT_EN = {
    "A": (
        "Composition: wide cinematic 16:9. Place the main subject and any face within the upper 55% of the frame; "
        "keep the bottom 40% of the frame simple and slightly darker, because a headline will be placed there. "
    ),
    "B": (
        "Composition: wide cinematic 16:9. Only a horizontal strip across the vertical middle of the frame will be "
        "used, so place the main subject inside that middle strip; keep nothing important in the top 30% or the "
        "bottom 30% of the frame. "
    ),
    # 싼세계(wp_tips) 전용: 아래쪽 45%를 색 띠가 덮습니다. 다른 채널은 위 "A"를 그대로 씁니다.
    "A_BAND": (
        "Composition: wide cinematic 16:9, a full-bleed photo that fills the whole frame from top to bottom edge with a "
        "real scene. Place the main subject (the key object or trace) inside the upper half of the frame, around its "
        "center and inside the central square; the floor, table or wall surface must continue naturally all the way to "
        "the bottom edge. Keep nothing important in the bottom 45%, but do not leave it empty. Do NOT leave any blank, "
        "black, solid-color or empty area, and do not paint a gradient there; no panel, band, strip, box or frame. "
    ),
    "C": (
        "Composition: wide cinematic 16:9. Only the vertical middle band of the frame will be used, so place the "
        "main subject inside that middle band; keep nothing important in the top 25% or the bottom 25% of the frame. "
    ),
}
_COVER_IMPACT_COMMON_EN = (
    "Keep the main subject inside the central 820px of the frame width (roughly the middle 53%), so it still "
    "remains when the image is cropped to a square thumbnail. "
)
_COVER_IMPACT_LAYOUT_KO = {
    "A": "주인공과 얼굴은 화면 위쪽 55% 안에 두고, 화면 아래쪽 40%는 단순하고 약간 어둡게 비워 주세요(제목 글자가 올라갑니다). ",
    "B": "화면의 세로 가운데 가로 띠만 쓰이므로 주인공을 그 가운데 띠 안에 두고, 위쪽 30%와 아래쪽 30%에는 중요한 것을 두지 마세요. ",
    "A_BAND": "사진은 위에서 맨 아래 끝까지 실제 장면으로 꽉 채우세요(full-bleed). 바닥·테이블·벽 같은 실제 표면이 아래 끝까지 자연스럽게 이어져야 합니다. 주인공(핵심 물건·흔적)은 화면 위쪽 절반 안, 가운데 정사각형 안에 두고, 아래쪽에는 중요한 물건을 두지 않되 비워 두지는 마세요. 아래쪽을 검은색·단색·그라데이션으로 칠하거나, 빈 띠·패널·박스·프레임·여백을 그리지 마세요. ",
    "C": "화면의 세로 가운데 띠만 쓰이므로 주인공을 그 가운데 띠 안에 두고, 위쪽 25%와 아래쪽 25%에는 중요한 것을 두지 마세요. ",
}
_COVER_IMPACT_COMMON_KO = "정사각형 썸네일로 잘려도 주인공이 남도록 가로 가운데 약 53%(820px) 안에 두세요. "
_COVER_IMPACT_BANS_EN = (
    "Do NOT depict: blood, wounds, or disgusting/revolting scenes; horror or fear-inducing staging; real people or "
    "faces resembling celebrities; brand logos or trademarks; body-shape or health before/after comparisons; "
    "children in danger or distress; sexually suggestive staging; imitation of dangerous behavior; a person "
    "holding up letters or a board toward the viewer. "
)
_COVER_IMPACT_BANS_KO = (
    "다음은 절대 그리지 마세요: 피·상처·혐오감을 주는 장면, 공포 연출, 실존 인물·유명인을 닮은 얼굴, "
    "브랜드 로고·상표, 몸매·건강 전후 비교, 아이가 위험하거나 괴로운 장면, 선정적 연출, 위험한 행동을 "
    "따라 하게 만드는 장면, 사람이 화면을 향해 글자·판을 들고 있는 구도. "
)


def _lt_photo_band(photo_h: int) -> tuple[int, int, float]:
    """롱텀 표지에서 AI 사진(생성 → 16:9 중앙 크롭 → 폭에 맞춤 → 가운데 photo_h px 자르기)이 실제로 쓰이는 세로 구간.
    _lt_cover_c·_lt_cover_b(_lt_load_photo_fill)의 계산과 같습니다. 반환: (위에서 잘리는 %, 아래에서 잘리는 %, 쓰이는 세로 %)."""
    gen_w, gen_h = (int(v) for v in GEN_SIZE.split("x"))
    if gen_w / gen_h > TARGET_RATIO[0] / TARGET_RATIO[1]:  # _center_crop_16_9와 같은 계산
        src_w, src_h = int(gen_h * TARGET_RATIO[0] / TARGET_RATIO[1]), gen_h
    else:
        src_w, src_h = gen_w, int(gen_w * TARGET_RATIO[1] / TARGET_RATIO[0])
    out_w = COVER_OUTPUT_SIZE[0]
    scale = max(out_w / src_w, photo_h / src_h)
    scaled_h = max(photo_h, round(src_h * scale))
    top = (scaled_h - photo_h) // 2
    return round(100 * top / scaled_h), round(100 * (scaled_h - photo_h - top) / scaled_h), round(100 * photo_h / scaled_h)


def _lt_old_style_layout(template: str, no_person: bool = False) -> tuple[str, str]:
    """LT_COVER_IMPACT_MODE=False일 때의 롱텀 구도 지시(영문, 한글). 예전 '중앙 여백' 대신 템플릿별로 실제 쓰이는 영역에 맞춥니다.
    no_person=True(장면에 사람이 없을 때)면 얼굴·머리 관련 문장을 빼고 C의 구도는 장면 문장을 따르게 합니다."""
    if template == "C" and no_person:
        en = (
            "Composition: wide cinematic 16:9. The whole frame is used as the cover photo. Title text will be added "
            "later over the lower part (about the bottom 45%), so keep that part a simple, bright background where "
            "the scene continues naturally, and keep the main subject within the upper half of the frame. Do NOT "
            "draw any white panel, box, band, strip, frame, translucent overlay or empty card anywhere in the image; "
            "it must be one single natural photograph from top to bottom. "
            "If the MANDATORY SCENE specifies a framing (for example top-down, close-up, "
            "front view), follow that framing; otherwise use a medium-distance framing so the place or setting stays "
            "clearly recognizable. "
        )
        ko = (
            "화면 전체가 표지 사진으로 쓰이며, 아래쪽 약 45%에는 나중에 제목 글자가 올라가니 그 부분은 장면이 자연스럽게 이어지는 "
            "단순하고 밝은 배경으로 두고, 주요 대상(핵심 사물)은 화면 위쪽 절반 안에 두세요. 사진 안에 흰 판·상자·띠·프레임·"
            "반투명 덮개·빈 카드 같은 그래픽 요소는 절대 그리지 마세요. 사진은 위에서 아래까지 하나의 자연스러운 사진이어야 합니다. "
            "장면 문장에 구도(위에서 내려다본, 클로즈업, 정면 등)가 있으면 그 구도를 따르고, 없으면 중간 거리 구도로 "
            "배경 장소가 알아볼 수 있게 보이게 하세요. "
        )
    elif template == "C":
        en = (
            "Composition: wide cinematic 16:9. The whole frame is used as the cover photo. Title text will be added "
            "later over the lower part (about the bottom 45%), so keep that part a simple, bright background where "
            "the scene continues naturally, and keep the main subject, and any face, within the upper half of the "
            "frame. Do NOT draw any white panel, box, band, strip, frame, translucent overlay or empty card anywhere "
            "in the image; it must be one single natural photograph from top to bottom. "
            "Use a medium-distance framing, NOT a close-up, so the place or "
            "setting stays clearly recognizable. If a person appears, the whole head with some headroom above it, "
            "and the face, must fit completely inside the upper half — never cropped. "
        )
        ko = (
            "화면 전체가 표지 사진으로 쓰이며, 아래쪽 약 45%에는 나중에 제목 글자가 올라가니 그 부분은 장면이 자연스럽게 이어지는 "
            "단순하고 밝은 배경으로 두고, 주요 대상(사람 얼굴 등)은 화면 위쪽 절반 안에 두세요. 사진 안에 흰 판·상자·띠·프레임·"
            "반투명 덮개·빈 카드 같은 그래픽 요소는 절대 그리지 마세요. 사진은 위에서 아래까지 하나의 자연스러운 사진이어야 합니다. "
            "너무 가까운 클로즈업이 아니라 중간 거리 구도로, 배경 장소가 알아볼 수 있게 보이게 하세요. 사람이 나오면 머리 위 "
            "여유까지 포함한 머리·얼굴 전체가 위쪽 절반 안에 온전히 들어가야 하며 잘리면 안 됩니다. "
        )
    elif template == "B":
        en = (
            "Composition: wide cinematic 16:9. The whole photo is used as a softly blurred background and an "
            "information card is placed over the center, so show a calm scene across the whole frame; the main "
            "subject does not need to avoid the center. "
        )
        ko = "화면 전체에 차분한 장면을 담으세요. 사진 전체가 흐린 배경으로 쓰이고 가운데에 카드가 올라가므로, 주요 대상은 가운데를 피하지 않아도 됩니다. "
    else:  # A-사진형: 전체 화면을 쓰므로 예전처럼 가운데 여백, 주인공은 위쪽
        en = (
            "Compose with generous empty space in the center-left or center area suitable for a text headline "
            "overlay, wide cinematic 16:9 composition. Place the main subject, and any face, in the upper part of "
            "the frame. "
        )
        if no_person:
            en = en.replace("main subject, and any face, in", "main subject in")
        ko = "제목 글자가 나중에 올라갈 수 있게 화면 중앙 쪽에 여백을 넉넉히 남기고, 주인공은 화면 위쪽에 두세요. "
    return en, ko

# 숍(쿠팡파트너스) 상품 이미지는 정사각형이 실제 쇼핑몰 관행에 더 가깝습니다 (16:9로 억지로
# 자르면 제품이 잘릴 위험이 있음).
PRODUCT_IMAGE_SIZE = "1024x1024"

OUTPUT_DIR = os.path.join("static", "generated")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 새로 만드는 AI 이미지·표지·본문·표지 배경·상품 변형 이미지는 PNG 대신 JPG로 저장합니다(장당 약 1.3MB → 훨씬 작게).
# 품질은 여기 한 곳만 바꾸면 됩니다. 생활레이더(RADAR_COVER_JPG_QUALITY)는 따로 이미 JPG이므로 이 값과 무관합니다.
# 옛 글의 .png 파일은 그대로 두며, 앱은 .jpg를 먼저 찾고 없으면 .png도 찾습니다.
GEN_JPG_QUALITY = 90


def _save_gen_jpg(img: Image.Image, filepath: str) -> None:
    """이미지를 RGB로 바꿔 JPG(GEN_JPG_QUALITY, optimize)로 저장합니다. filepath의 확장자는 .jpg여야 합니다."""
    img.convert("RGB").save(filepath, "JPEG", quality=GEN_JPG_QUALITY, optimize=True)

# ── 채널/글제목별 폴더링 ──────────────────────────────────────────────────
# 기본은 예전처럼 static/generated에 전부 평평하게 저장되지만, app.py가 요청 시작할 때
# set_output_context()로 "static/generated/채널명/글제목" 같은 하위 폴더를 지정해두면
# 이후 이 스레드에서 만드는 이미지들은 전부 그 폴더에 저장됩니다. 예전 글들의 이미지
# 경로(article_id 기반 평평한 구조)는 그대로 유지되어 안 깨집니다 — 새로 만드는 이미지부터
# 새 폴더 구조가 적용됩니다.
_output_dir_local = threading.local()


def set_output_context(path: str | None) -> None:
    """이후 이 스레드에서 부르는 이미지 생성 함수들이 저장할 폴더를 지정합니다.
    None을 넘기면 기본값(OUTPUT_DIR, 평평한 구조)으로 되돌아갑니다."""
    _output_dir_local.path = path


def _current_output_dir() -> str:
    path = getattr(_output_dir_local, "path", None)
    if path:
        os.makedirs(path, exist_ok=True)
        return path
    return OUTPUT_DIR


def slugify_for_folder(text: str, max_len: int = 40) -> str:
    """글 제목·키워드를 폴더명으로 쓸 수 있게 다듬습니다. 한글은 그대로 두고(한글 폴더명은
    윈도우·맥 다 문제없이 지원됨), 폴더명에 못 쓰는 문자만 언더스코어로 바꿉니다."""
    text = (text or "제목없음").strip()
    text = re.sub(r'[\\/:*?"<>|]', "_", text)  # 파일시스템에서 못 쓰는 문자
    text = re.sub(r"\s+", "_", text)
    return text[:max_len].strip("_") or "제목없음"

# ── 장면 다양화 ──────────────────────────────────────────────────────────────
# "글자 금지" 규칙이 너무 촘촘하다 보니(표지판·화면·계산기·돈 전부 금지), AI가 안전하게
# 고를 수 있는 장면이 "빈 종이 + 펜 든 손"으로 계속 수렴하는 문제가 있었습니다. AI의
# "알아서 다양하게"에 맡기지 않고, 매번 코드가 직접 구체적인 장소·인물·구도를 뽑아서
# 프롬프트에 강제로 박아 넣는 방식으로 바꿨습니다 — 이러면 표지 여러 장을 한 번에 만들거나
# 본문 이미지를 여러 장 만들 때도 서로 다른 장면이 나옵니다.
SCENE_SETTINGS = [
    "a bright government office reception counter with a clerk assisting a visitor",
    "a cozy living room where someone relaxes on a sofa with a cup of tea",
    "a modern bank branch lobby with soft natural light",
    "a job center waiting area with several empty chairs",
    "a moving day scene with cardboard boxes and a new apartment door key",
    "a hospital reception and waiting area",
    "a small neighborhood real estate agency storefront",
    "a young couple standing outside looking up at an apartment building",
    "a community center meeting room with round tables",
    "an elderly person's kitchen table with warm afternoon light",
    "a co-working space with large windows and greenery",
    "a parent playing with a toddler on a living room floor",
    "a public plaza outside a government building entrance",
    "a car parked in front of a suburban house, keys in hand",
    "a tax accountant's consultation office with two people talking",
    "a university campus courtyard with a student walking",
    "a cafe corner with warm lighting and a laptop bag on the table",
    "a construction/renovation site of a small apartment being moved into",
    "a pharmacy counter interior",
    "a quiet neighborhood street with residential houses",
]
SCENE_PEOPLE = [
    "a young Korean jobseeker in casual professional attire",
    "a Korean new parent gently holding a baby",
    "an elderly Korean person with a warm expression",
    "a Korean small business owner in an apron",
    "a Korean married couple in their early 30s",
    "a Korean office worker in a blazer",
    "a Korean university student with a backpack",
    "a middle-aged Korean parent",
    "a Korean single-person household resident in their 20s",
    "a Korean couple in their 40s",
]
# 인물은 무조건 한국인 — 예전에는 국적 지정이 없어서 이미지 모델이 서양인·외국인을 자주 그렸습니다.
# 장면 지시문에 넣고, 프롬프트 끝(마지막에 읽는 지시가 더 잘 지켜짐)에도 다시 붙입니다.
KOREAN_ONLY_RULE = (
    "═══ MANDATORY: ALL PEOPLE MUST BE KOREAN ═══ Every person who appears anywhere in this image — main subject, "
    "background passers-by, hands, partial figures, silhouettes — must be ethnically Korean, with natural Korean/East "
    "Asian facial features and black or dark-brown hair, dressed like ordinary people in South Korea, in an everyday "
    "South Korean setting (Korean apartments, streets, offices). NEVER depict Caucasian, Black, South Asian, Middle "
    "Eastern, Latino or any other non-Korean people. "
    "이미지에 등장하는 모든 사람(주인공, 배경 행인, 손, 일부만 보이는 인물, 실루엣 포함)은 반드시 한국인이어야 합니다 — "
    "한국인 특유의 자연스러운 외모와 검은색·짙은 갈색 머리, 한국의 일상적인 옷차림과 한국 일상 공간. "
    "서양인·흑인·동남아·중동·라틴계 등 외국인은 절대 등장시키지 마세요. ═══ END OF RULE ═══"
)
# 롱텀 표지에서 장면에 사람이 없을 때(LT_COVER_NO_PERSON_GUARD) 프롬프트 앞과 끝에 넣는 블록(KOREAN_ONLY_RULE 대신).
_LT_NO_PERSON_BLOCK = (
    "═══ NO PEOPLE ═══ This is a people-free still-life/interior photograph: NO person, NO face, NO hands, NO body "
    "parts, NO silhouettes, NO reflections of people anywhere in the frame. "
    "사람이 한 명도 없는 사물·공간 사진입니다: 사람·얼굴·손·몸 일부·실루엣·비친 모습 모두 금지. ═══ END OF RULE ═══ "
)
SCENE_FRAMING = [
    "a wide establishing shot showing the full scene and setting",
    "a medium shot focused on the person from the waist up with the setting visible behind them",
    "an over-the-shoulder shot looking past the person toward the setting",
    "a candid three-quarter angle shot capturing a natural, unposed moment",
]
# 반복적으로 튀어나오던 그 뻔한 포즈 — 명시적으로 금지합니다.
BANNED_POSE = (
    "Do NOT depict a person sitting at a desk holding a pen over paper, writing or signing "
    "on a blank document, or a close-up of hands with a pen — this exact pose has been "
    "overused and must not appear."
)

# ── 본문 사진 구도 다양화 (photo_v2_20261020) ─────────────────────────────────────
# 본문 사진(표지 제외)이 서로 비슷한 구도로 나오는 문제를 막으려고, 글 한 편의 본문 사진마다 서로 다른 구도를 하나씩 배정합니다.
# 배정은 글 id로 시드를 만든 섞기라서, 같은 글은 다시 만들어도 같은 배정이고 글마다는 다릅니다.
# 메모 장면 문장에 구도가 이미 적혀 있으면 그 구도를 따르도록 프롬프트에 적습니다(메모 구도 우선).
# 글자 0개·한국인·얼굴/사람·1/3 사람·현금 금지·BANNED_POSE 같은 기존 금지선은 이 목록과 무관하게 그대로입니다.
BODY_SHOT_ANGLES = [
    {
        "key": "closeup",
        "en": "an extreme close-up shot that fills the frame with the key object or detail of the scene, with shallow depth of field",
        "ko": "아주 가까운 클로즈업(핵심 사물·디테일이 화면을 가득 채움)",
    },
    {
        "key": "topdown",
        "en": "a top-down overhead shot, the camera looking straight down from above at the scene",
        "ko": "위에서 곧게 내려다본 탑뷰",
    },
    {
        "key": "eye_level",
        "en": "an eye-level, straight-on frontal shot with the camera at eye height facing the scene directly",
        "ko": "눈높이에서 정면으로 본 구도",
    },
    {
        "key": "three_quarter",
        "en": "an oblique side view from a three-quarter angle that shows depth and diagonal lines",
        "ko": "비스듬한 측면 3/4 구도(깊이감과 대각선)",
    },
    {
        "key": "low_angle",
        "en": "a low-angle shot with the camera placed low, near the floor or table surface, looking upward at the scene",
        "ko": "낮은 곳에서 올려다본 구도",
    },
    {
        "key": "wide",
        "en": "a wide shot that shows the whole space and its surroundings, with the subject sitting naturally within the setting",
        "ko": "공간 전체가 보이는 넓은 구도",
    },
    {
        "key": "hands_work",
        "en": "a close shot of the activity being carried out at hand level, focused on the work area and the objects being worked on "
        "(show hands ONLY if the scene description mentions a person or hands; otherwise show the work area with no hands at all)",
        "ko": "작업이 이뤄지는 자리를 가까이서 본 구도(손은 장면에 사람·손이 나올 때만, 아니면 손 없이 작업 공간만)",
    },
    {
        "key": "through_gap",
        "en": "a shot peeking into the scene through a doorway gap or a window, with the door frame or window frame softly blurred in the foreground",
        "ko": "문틈이나 창 너머로 들여다본 구도(앞쪽 문틀·창틀은 흐릿하게)",
    },
]


def assign_body_shot_angles(n: int, seed: str = "") -> list[dict]:
    """한 글의 본문 사진 n장에 BODY_SHOT_ANGLES를 서로 다르게 배정합니다(글 id 시드 섞기 → 같은 글은 늘 같은 배정).
    n이 구도 개수(8)보다 많으면 섞인 순서를 되풀이합니다. 난수 상태(random 모듈)는 건드리지 않습니다."""
    order = list(BODY_SHOT_ANGLES)
    random.Random(f"photo_v2_angles|{seed}").shuffle(order)
    return [order[i % len(order)] for i in range(max(0, n))]


def body_angle_clause(angle: dict | None) -> str:
    """장면 지시문에 덧붙일 구도 문장(영문 + 한글 보조). 메모 장면에 구도가 이미 있으면 메모 구도를 따르게 적습니다."""
    if not angle:
        return ""
    return (
        f"SHOT COMPOSITION FOR THIS IMAGE: {angle['en']}. If the scene description above already specifies a camera "
        f"angle or composition (for example 'seen from above' or 'close-up'), follow the scene's own angle and ignore "
        f"this composition; otherwise use this composition. "
        f"구도: 장면 문장에 구도(예: 위에서 내려다본, 클로즈업)가 이미 있으면 그 구도를 따르고, 없으면 이 구도로 만드세요 — {angle['ko']}. "
    )


def _with_body_angle(scene_directive: str, angle: dict | None) -> str:
    """scene_directive의 맨 끝 한국인 규칙(KOREAN_ONLY_RULE) 바로 앞에 구도 문장을 끼웁니다(끝에서 읽는 규칙 위치는 그대로).
    angle이 없으면 입력을 그대로 돌려줍니다."""
    clause = body_angle_clause(angle)
    if not clause:
        return scene_directive
    if scene_directive.endswith(KOREAN_ONLY_RULE):
        return scene_directive[: -len(KOREAN_ONLY_RULE)] + clause + KOREAN_ONLY_RULE
    return scene_directive + " " + clause


def _random_scene_directive() -> str:
    """이번 이미지에 쓸 장소·인물·구도를 무작위로 하나씩 뽑아서, 프롬프트에 넣을 지시문을 만듭니다.
    숍 채널(situational_only=True인 표지)이 그대로 쓰는 예전 방식입니다 — 주제별 대상 추정을
    적용하지 않고, 인물이 항상 1명 등장합니다. 롱텀은 이제 이 함수 대신 아래 _topic_scene_directive를
    씁니다."""
    setting = random.choice(SCENE_SETTINGS)
    person = random.choice(SCENE_PEOPLE)
    framing = random.choice(SCENE_FRAMING)
    return (
        f"MANDATORY SCENE FOR THIS IMAGE: Set in {setting}. Feature {person} as the main subject. "
        f"Use {framing}. Build the specific scene details (props, activity) around this setting and "
        f"person so it fits the topic naturally. {BANNED_POSE} {KOREAN_ONLY_RULE}"
    )


def _random_scene_directive_no_face(keyword: str = "") -> str:
    """COVER_NO_FACE=True인 숍 표지(메모에 '표지 장면:'이 없을 때)용 장면: 장소 목록(SCENE_SETTINGS)·인물·구도 목록은 쓰지 않고,
    키워드가 필요해지는 한국 일상 공간 하나를 이미지 모델이 직접 고르게 한 뒤 그곳의 문제·불편함의 흔적(물건·공간)을 아주 가까이
    또렷하게 보여줍니다(꿀팁의 '장면 없음' 방식과 같은 접근). 사람은 넣지 않고, 꼭 필요하면 손이나 뒷모습만.
    keyword가 비어 있으면 특정 공간 지정 없이 한국 가정집의 흔한 공간으로 안내합니다. 예전 _random_scene_directive()는 그대로 둡니다."""
    if keyword:
        pick_en = (
            f"Choose ONE everyday Korean indoor or outdoor space where a need for '{keyword}' would naturally arise, "
            f"and set the shot there."
        )
    else:
        pick_en = "Choose ONE everyday Korean home space (for example a living room, kitchen, or entrance) and set the shot there."
    return (
        f"MANDATORY SCENE FOR THIS IMAGE: {pick_en} Show, very close up and sharply detailed, the traces of the "
        f"problem or inconvenience (objects and spaces) that would make someone need this kind of product in that "
        f"space. Do NOT include any person; only if one is truly necessary, show just a hand or a back view. Build "
        f"the specific scene details (props, traces) around that space so it fits the topic naturally. "
        f"{BANNED_POSE} {KOREAN_ONLY_RULE}"
    )


# ── 롱텀 전용: 주제별 대상 추정 + "사람 없는 장면"을 기본값으로 ──────────────────────
# 예전엔 SCENE_PEOPLE에서 주제와 상관없이 아무나 뽑고, 모든 이미지에 사람을 무조건 넣었습니다.
# 그래서 "청년 정책" 글에 중년·노인이 나오거나, 사람이 필요 없는 장면에도 사람이 끼어들었습니다.
# 이제는 (1) 키워드·소제목·alt에서 이 글의 대상을 먼저 추정하고, 대상이 안 잡히면 사람 없는
# 공간·사물 장면을 기본값으로 쓰고, (2) 대상이 잡혀도 이미지 배치(표지 세트/본문 세트) 전체에서
# 최대 1/3까지만 사람을 등장시킵니다. 숍 채널은 건드리지 않습니다 — situational_only=True이고 메모에 '표지 장면:'이 없을 때,
# COVER_NO_FACE=True면 _random_scene_directive_no_face()(키워드 기반 장면, 사람 없음), False면 예전 _random_scene_directive()(위 함수)를 씁니다.
SUBJECT_PROFILES = {
    "youth": {
        "match": re.compile(r"청년|대학생|사회초년생|신혼|첫\s?집"),
        "person_desc": "a Korean person in their early-to-mid 20s",
        "banned_ages": "middle-aged or elderly Korean people (40s and older), or teenagers",
        "person_scenes": [
            "a young Korean adult carrying a moving box into a small studio apartment, shown from behind",
            "a young Korean adult's hand holding a house key in front of a new apartment door",
            "a young Korean adult sitting alone on the floor of a small empty apartment, looking out the "
            "window, shown from behind",
            "a young Korean adult unpacking a moving box in a small one-room apartment, focus on their hands",
        ],
        "no_person_scenes": [
            "a new studio apartment's front door with a shiny key still in the lock, nobody in frame",
            "moving boxes stacked neatly inside a small one-room studio apartment, no people in the shot",
            "an empty one-room apartment with a single bed by the window, afternoon light, no people",
            "a small moving truck loaded with boxes parked outside an apartment building, no people",
            "a compact desk in a small studio apartment with a laptop closed and a mug, no people",
        ],
    },
    "senior": {
        # 국민연금 조기수령·추납, 주택연금, 장애인연금, 기초생활수급자는 일부러 안 넣습니다 —
        # 국민연금·주택연금은 pension_savings 주제로, 장애인연금·기초생활수급자는 energy_welfare
        # 주제로 보내고(민감 주제라 사람 없는 장면만), 대상 나이대 추정에는 안 씁니다.
        "match": re.compile(r"노인|어르신|기초연금|노령|경로"),
        "person_desc": "a Korean person aged 65 or older",
        "banned_ages": "children, teenagers, or adults younger than 55",
        "person_scenes": [
            "an elderly Korean person's hands resting around a warm cup of tea at a kitchen table, face not "
            "the focus",
            "an elderly Korean person walking alone through a quiet neighborhood street, shown from behind",
            "an elderly Korean person sitting alone on a bench in a small park, shown from the side",
        ],
        "no_person_scenes": [
            "a warm, quiet Korean kitchen table with a teapot and two cups, afternoon light, no people",
            "empty chairs in a senior community center meeting room, no people in the shot",
            "a small potted plant on a sunny windowsill in a modest home, no people",
            "a neighborhood senior center's empty waiting bench in soft daylight, no people",
        ],
    },
    "parent": {
        "match": re.compile(r"출산|육아|부모급여|아동|임신|신생아|첫만남|자녀장려금|한부모|아이|자녀"),
        "person_desc": "a Korean parent in their 30s, together with a baby or toddler",
        "banned_ages": "elderly people, or any adult scene without a baby or toddler present",
        "person_scenes": [
            "a Korean parent in their 30s gently holding a sleeping baby, shown from behind or the side, "
            "face turned away from camera",
            "a Korean parent's hands adjusting a blanket over a baby in a crib, focus on the hands",
            "a Korean parent sitting on the floor playing with a toddler in a bright living room, shown from "
            "behind",
        ],
        "no_person_scenes": [
            "an empty baby crib with soft toys arranged neatly, warm nursery light, no people in the shot",
            "a soft baby blanket and small toys laid out on a nursery floor, no people",
            "a warmly lit nursery corner with a rocking chair and a mobile, no people",
            "baby clothes neatly folded on a shelf in a small nursery, no people",
        ],
    },
    "business": {
        "match": re.compile(r"소상공인|자영업|사업자|간이과세자|부가가치세|3\.3%|프리랜서"),
        "person_desc": "a Korean small business owner in their 30s to 50s",
        "banned_ages": "people in their teens or 20s, or elderly people over 65",
        "person_scenes": [
            "a Korean small business owner in their 30s to 50s arranging products on a shelf inside their "
            "small shop, shown from behind",
            "a Korean small business owner's hands wiping down a counter in a small shop, focus on the hands",
            "a Korean small business owner standing in the doorway of their small shop looking outside, "
            "shown from behind",
        ],
        "no_person_scenes": [
            "a cozy small cafe interior with warm pendant lighting and empty chairs, no people",
            "a small shop's wall-mounted heating/cooling unit above empty shelves, no people",
            "neatly arranged products on a small shop's shelf, warm interior lighting, no people",
            "a small shop's front window with soft warm interior lighting, seen from outside, no people",
        ],
    },
    "worker": {
        "match": re.compile(
            r"직장인|근로장려|연말정산|실업급여|구직|국민취업지원|내일배움카드|고용보험|퇴직연금|퇴직소득세"
        ),
        "person_desc": "a Korean office worker or jobseeker in their late 20s to 40s",
        "banned_ages": "teenagers, or elderly people over 65",
        "person_scenes": [
            "a Korean office worker in their late 20s to 40s walking through an office hallway, shown from "
            "behind",
            "a Korean jobseeker sitting alone in a job center waiting area, shown from the side",
            "a Korean office worker's hands closing a laptop at a tidy desk, focus on the hands",
        ],
        "no_person_scenes": [
            "an empty modern office desk with a closed laptop and a small plant, no people in the shot",
            "empty chairs in a job center waiting area, soft daylight, no people",
            "a quiet co-working space with large windows and empty desks, no people",
            "a tidy office cubicle with a jacket hung on the back of the chair, no people in the shot",
        ],
    },
}

# ── 롱텀 전용: "사람" 대신 "주제"로 묶는 장면 목록 (전부 사람 없는 장면) ──────────────────
# SUBJECT_PROFILES 어디에도 안 걸리는 키워드(세금·주거대출·보험·서민금융·에너지복지·연금저축 등)를
# 위해 만든 별도 계층입니다. 대상 나이대를 특정할 수 없는 주제라, 사람은 아예 안 씁니다.
# 장애인연금·기초생활수급자처럼 민감한 주제도 여기(energy_welfare)로 보내 사람 없는 장면만 나가게 합니다.
TOPIC_PROFILES = {
    "housing_loan": {
        "match": re.compile(r"전세|월세|디딤돌|버팀목|주택청약|청약"),
        "no_person_scenes": [
            "a modern apartment complex exterior against a clear sky, no people",
            "a house key resting on a small tray by a front door, no people",
            "moving boxes stacked in a sunlit empty room, no people",
            "a bright empty living room with bare hardwood floors, no people",
        ],
    },
    "insurance_health": {
        "match": re.compile(r"실손보험|보험료|피부양자|의료비"),
        "no_person_scenes": [
            "a pharmacy shelf with neatly arranged unmarked bottles, no visible text, no people",
            "empty chairs in a quiet hospital waiting room, no people",
            "a glass of water and a pill organizer case on a bedside table, no people",
        ],
    },
    "informal_finance_debt": {
        "match": re.compile(r"햇살론|새희망홀씨|소액생계비|신용회복|채무조정"),
        "no_person_scenes": [
            "empty chairs at a quiet bank teller counter, no people",
            "a warm mug of tea on a calm dining table, soft daylight, no people",
            "sunlight streaming through a window onto a quiet room, no people",
        ],
    },
    "energy_welfare": {
        "match": re.compile(r"에너지바우처|긴급복지|문화누리카드|기초생활수급자|장애인연금|추석\s?지원금"),
        "no_person_scenes": [
            "a cozy living room corner beside a warm heater, soft light, no people",
            "a small room with a warm lamp glowing in the evening, no people",
            "a warm home-cooked meal laid out on a dining table, no people",
        ],
    },
    "pension_savings": {
        "match": re.compile(r"국민연금|IRP|ISA|연금저축|주택연금"),
        "no_person_scenes": [
            "a potted plant on a sunny windowsill, no people",
            "an hourglass on a wooden table with soft light, no people",
            "a tree outside a window changing color with the seasons, no people",
            "a quiet, calm living room in soft afternoon light, no people",
        ],
    },
    # 세금 관련 키워드는 "세액공제"·"소득공제"·"비과세"처럼 다른 주제(주거·보험·연금)에도 자주
    # 같이 붙어 나오므로, 더 구체적인 주제들을 먼저 확인한 뒤 마지막 catch-all로 둡니다.
    "tax": {
        "match": re.compile(
            r"종합소득세|종합부동산세|상속세|증여세|양도소득세|양도세|재산세|취득세|자동차세|부가세|"
            r"세액공제|소득공제|비과세"
        ),
        "no_person_scenes": [
            "a closed blank folder resting on a tidy desk, no visible text, no people",
            "a plain sealed envelope sitting on a wooden desk, no visible text, no people",
            "an autumn-themed desk with a warm lamp and a small potted plant, no people",
            "a small model house figurine placed on a clean desk, no people",
        ],
    },
}

# 민감한 주제(장애인·기초생활수급자 등)는 대상 추정 결과와 무관하게 항상 사람 없는 장면만 씁니다.
SENSITIVE_NO_PERSON_PATTERN = re.compile(r"장애인|기초생활수급자|수급자")

# 대상도 주제도 못 정했을 때 쓰는 마지막 공용 "사람 없는" 장면들 — 어떤 주제에도 안 어색하게
# 중립적인 것만 남깁니다(특정 주제 전용 소품은 위 SUBJECT_PROFILES/TOPIC_PROFILES 쪽에 있습니다).
DEFAULT_NO_PERSON_SCENES = [
    "a sunlit empty living room with soft natural light, no people",
    "a small potted plant on a sunny windowsill, no people",
    "a tidy, organized wooden desk with a closed notebook and a pen holder, no people",
    "a warm dining table set with a simple home-cooked meal, no people",
    "a quiet living room with soft afternoon light through sheer curtains, no people",
]


def _estimate_subject(keyword: str, heading: str = "", alt: str = "") -> dict | None:
    """
    키워드·소제목·alt 텍스트를 합쳐서 이 글이 어떤 대상(나이대)을 다루는지 추정합니다.
    어느 규칙에도 안 걸리면 None을 돌려주고, 이때는 _estimate_topic()으로 주제 기준 장면을
    찾거나, 그것도 안 되면 사람이 안 나오는 공용 장면을 씁니다.
    """
    combined = f"{keyword} {heading} {alt}"
    for profile in SUBJECT_PROFILES.values():
        if profile["match"].search(combined):
            return profile
    return None


def _estimate_topic(keyword: str, heading: str = "", alt: str = "") -> dict | None:
    """대상(나이대)이 안 잡힐 때 쓰는 차선책 — 세금/주거대출/보험/서민금융/에너지복지/연금저축 같은
    "주제"만으로 사람 없는 장면을 고릅니다. 어느 규칙에도 안 걸리면 None."""
    combined = f"{keyword} {heading} {alt}"
    for topic in TOPIC_PROFILES.values():
        if topic["match"].search(combined):
            return topic
    return None


def _plan_person_flags(n: int) -> list[bool]:
    """이번 배치(표지 세트 또는 본문 세트) n장 중 최대 n//3장까지만 사람이 등장하도록 미리
    정합니다 — "사람 없는 장면을 기본으로" 하기 위한 상한선입니다. 순서는 섞습니다."""
    max_person = n // 3
    flags = [True] * max_person + [False] * (n - max_person)
    random.shuffle(flags)
    return flags


def _pick_no_person_scene(profile: dict | None, topic: dict | None, used_scenes: set) -> str:
    """
    사람 없는 장면을 고릅니다. 대상(profile)이 잡혔으면 그 대상 전용 장면만, 대상은 없고
    주제(topic)만 잡혔으면 그 주제 전용 장면만 씁니다 — 둘 다 섞어서 고르지 않습니다(청년 글에
    아기 침대가 나오는 것 같은 문제 방지). 그 목록을 이번 배치에서 이미 다 썼을 때만 공용
    장면(DEFAULT_NO_PERSON_SCENES)으로 넘어갑니다.
    """
    tiers = []
    if profile and profile.get("no_person_scenes"):
        tiers.append(profile["no_person_scenes"])
    elif topic and topic.get("no_person_scenes"):
        tiers.append(topic["no_person_scenes"])
    tiers.append(DEFAULT_NO_PERSON_SCENES)

    for candidates in tiers:
        pool = [s for s in candidates if s not in used_scenes]
        if pool:
            scene = random.choice(pool)
            used_scenes.add(scene)
            return scene
    # 아주 큰 배치라 모든 후보를 다 썼으면 공용 장면 중 하나를 재사용합니다.
    scene = random.choice(DEFAULT_NO_PERSON_SCENES)
    used_scenes.add(scene)
    return scene


def _topic_scene_directive(
    keyword: str,
    heading: str = "",
    alt: str = "",
    want_person: bool | None = None,
    used_scenes: set | None = None,
) -> str:
    """
    롱텀 전용: 주제(키워드+소제목+alt)에서 대상(나이대)이나, 대상이 없으면 주제 묶음을 추정해
    그에 맞는 장면을 고르고, _random_scene_directive()와 같은 형태의 "MANDATORY SCENE" 지시문을
    만듭니다. want_person을 안 넘기면 이번 이미지에 사람을 넣을지 1/3 확률로 직접 정합니다 —
    여러 장을 한 번에 만들 때는 호출하는 쪽에서 _plan_person_flags()로 미리 정확한 개수를 정해서
    넘겨주세요. used_scenes를 넘기면 같은 글 안에서 이미 쓴 장면은 되도록 다시 안 씁니다.
    장애인·기초생활수급자 등 민감한 주제는 want_person과 무관하게 항상 사람 없는 장면으로 갑니다.
    """
    profile = _estimate_subject(keyword, heading, alt)
    topic = _estimate_topic(keyword, heading, alt) if not profile else None
    combined = f"{keyword} {heading} {alt}"

    if want_person is None:
        want_person = random.random() < (1 / 3)
    if SENSITIVE_NO_PERSON_PATTERN.search(combined):
        want_person = False
    if used_scenes is None:
        used_scenes = set()

    if profile and want_person and profile["person_scenes"]:
        pool = [s for s in profile["person_scenes"] if s not in used_scenes] or profile["person_scenes"]
        scene = random.choice(pool)
        used_scenes.add(scene)
        framing = random.choice(SCENE_FRAMING)
        return (
            f"MANDATORY SCENE FOR THIS IMAGE: {scene}. Feature {profile['person_desc']} as the main subject "
            f"in this exact setting. Use {framing}. MANDATORY: the person's apparent age must clearly read "
            f"as {profile['person_desc']} — do NOT depict {profile['banned_ages']}. Build the specific scene "
            f"details (props, activity) around this setting and person so it fits the topic naturally. "
            f"{BANNED_POSE} {KOREAN_ONLY_RULE}"
        )

    scene = _pick_no_person_scene(profile, topic, used_scenes)
    return (
        f"MANDATORY SCENE FOR THIS IMAGE: {scene}. This shot must NOT include any person, hand, or human "
        f"figure of any kind, anywhere in the frame, even blurry or in the background — the scene is "
        f"entirely about the place or object itself. Build the specific visual details around this setting "
        f"so it fits the topic naturally. {KOREAN_ONLY_RULE}"
    )

_FONT_CANDIDATES = [
    os.path.join("fonts", "Pretendard-Medium.otf"),
    "malgun.ttf",
    "C:\\Windows\\Fonts\\malgun.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
]

_FONT_CANDIDATES_BOLD = [
    os.path.join("fonts", "Pretendard-ExtraBold.otf"),
    "malgunbd.ttf",
    "C:\\Windows\\Fonts\\malgunbd.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf",
]


def _load_font(size: int):
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _load_bold_font(size: int):
    for path in _FONT_CANDIDATES_BOLD:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _center_crop_16_9(img: Image.Image) -> Image.Image:
    """이미지를 중앙 기준으로 16:9 비율로 잘라냅니다."""
    w, h = img.size
    target_w, target_h = TARGET_RATIO
    # 현재 비율이 16:9보다 넓으면 좌우를, 좁으면 상하를 자름
    if w / h > target_w / target_h:
        new_w = int(h * target_w / target_h)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w * target_h / target_w)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))


def _call_gpt_image(prompt: str, quality: str = BODY_IMAGE_QUALITY) -> Image.Image:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "size": GEN_SIZE,
        "quality": quality,
    }
    resp = requests.post(OPENAI_IMAGE_ENDPOINT, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    b64 = data["data"][0]["b64_json"]
    img_bytes = base64.b64decode(b64)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    return _center_crop_16_9(img)


def _call_gpt_image_edit(
    image_path: str, prompt: str, n: int = 1, size: str = PRODUCT_IMAGE_SIZE, quality: str = BODY_IMAGE_QUALITY
) -> list[Image.Image]:
    """
    실제 제품 사진(레퍼런스)을 입력으로 주고 바리에이션 n장을 한 번에 만듭니다. 순수 텍스트
    생성과 달리 실제 이미지를 기반으로 하기 때문에, 제품의 형태·색상·로고가 원본과 크게
    달라지지 않습니다. 쿠팡파트너스 정책상 "실제 상품과 현저히 다른 AI 이미지"는 허위광고
    소지가 있다고 명시되어 있어서, 상품 이미지는 반드시 이 방식(레퍼런스 기반 편집)을 씁니다
    — 순수 텍스트 생성(_call_gpt_image)은 상품 이미지에 쓰지 않습니다.
    size 기본값은 상품 이미지용 정사각형(PRODUCT_IMAGE_SIZE)이고, 표지용으로 쓸 때는
    호출하는 쪽에서 GEN_SIZE를 넘겨서 16:9 크롭 전 크기로 받습니다.
    """
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    with open(image_path, "rb") as f:
        files = {"image": (os.path.basename(image_path), f, "image/png")}
        data = {
            "model": IMAGE_MODEL,  # 편집 경로의 flare 지원은 미확인(실제 호출 시험 안 함)
            "prompt": prompt,
            "n": str(n),
            "size": size,
            "quality": quality,
        }
        resp = requests.post(OPENAI_IMAGE_EDIT_ENDPOINT, headers=headers, files=files, data=data, timeout=180)
    resp.raise_for_status()
    result = resp.json()
    images = []
    for item in result["data"]:
        img_bytes = base64.b64decode(item["b64_json"])
        images.append(Image.open(io.BytesIO(img_bytes)).convert("RGB"))
    return images


def _build_product_variation_prompt(product_name: str, style: str) -> str:
    """style: "ad"(광고 사진 컨셉) 또는 "use"(실사용 장면)"""
    base = (
        f"This is a real product photo of '{product_name}'. Keep the product itself — its exact shape, "
        f"color, proportions, logos, and any text/labels on it — unchanged from the reference image. "
        f"Do not redesign, distort, or invent product details; the product must remain clearly "
        f"recognizable and accurate to the original. "
    )
    if style == "ad":
        base += (
            "Re-shoot it as a professional advertising-style product photo: place it on a clean, "
            "tasteful surface or subtly complementary background, soft studio-quality lighting, gentle "
            "shadow, shallow depth of field. Commercial catalog photography look, no added text."
        )
    else:
        base += (
            "Show it being naturally used or consumed in a realistic everyday setting — ideally with a "
            "person's hands or a person in frame interacting with it. Candid lifestyle photography look, "
            "natural lighting, genuine and unstaged feeling, no added text. Any person or hands in frame "
            "must be Korean."
        )
    return base


def generate_product_variations(
    reference_image_path: str, product_name: str, article_id: str, slot: int, count: int = 5
) -> list[str]:
    """
    제품 레퍼런스 이미지 1장으로 바리에이션 count장(광고 컨셉·실사용 컨셉을 섞어서)을 만들고
    저장합니다. 저장된 이미지들의 웹 경로 목록을 반환합니다 — 실제 채택은 사용자가 마음에
    드는 걸 고르는 방식입니다(자동으로 하나를 확정하지 않음).
    """
    styles = (["ad", "use"] * ((count // 2) + 1))[:count]
    ad_count = styles.count("ad")
    use_count = styles.count("use")

    if USE_MOCK:
        labeled = [("ad", f"{product_name} 광고컷") for _ in range(ad_count)] + [
            ("use", f"{product_name} 실사용컷") for _ in range(use_count)
        ]
        images = [(style, _mock_image(label)) for style, label in labeled]
    else:
        images = []
        if ad_count:
            prompt = _build_product_variation_prompt(product_name, "ad")
            images += [("ad", img) for img in _call_gpt_image_edit(reference_image_path, prompt, n=ad_count)]
        if use_count:
            prompt = _build_product_variation_prompt(product_name, "use")
            images += [("use", img) for img in _call_gpt_image_edit(reference_image_path, prompt, n=use_count)]

    paths = []
    for i, (style, img) in enumerate(images):
        filename = f"{article_id}_product{slot}_var{i}_{style}.jpg"
        filepath = os.path.join(_current_output_dir(), filename)
        _save_gen_jpg(img, filepath)
        paths.append("/" + filepath.replace(os.sep, "/"))
    return paths


def _mock_image(label: str) -> Image.Image:
    size = (1280, 720)  # 16:9 목 모드 이미지
    color = random.choice([(59, 130, 246), (16, 185, 129), (245, 158, 11), (139, 92, 246), (236, 72, 153)])
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    font = _load_font(16)
    wrapped = textwrap.fill(f"[목 모드] {label}", width=30)
    draw.multiline_text((16, 12), wrapped, fill=(255, 255, 255, 180), font=font)
    return img


def _scene_from_alt_directive(scene_text: str, keyword: str = "", no_person: bool = False) -> str:
    """꿀팁 채널 전용(scene_from_alt=True): 메모의 장면 설명을 그대로 MANDATORY SCENE으로 씁니다.
    정해진 장면 목록(SCENE_SETTINGS·TOPIC_PROFILES 등)은 쓰지 않고, 키워드는 맥락으로만 덧붙입니다.
    사람은 장면 설명에 사람이 언급될 때만 그리고, 그릴 때는 한국인 규칙을 적용합니다.
    no_person=True(롱텀 표지에서 장면에 사람이 없을 때만)면 한국인 규칙을 넣지 않습니다."""
    ctx = f" (Topic context only, do not let it change the scene: {keyword}.)" if keyword else ""
    return (
        f"MANDATORY SCENE FOR THIS IMAGE: {scene_text.strip()}{ctx} Depict exactly this scene, with these "
        f"objects and this setting, and add only small natural details that fit it. "
        + (
            ""
            if no_person
            else f"Include a person ONLY if the scene description above mentions a person or people; if it does not, "
            f"the shot must NOT include any person, hand, or human figure anywhere in the frame. {KOREAN_ONLY_RULE}"
        )
    )


# ── 짧은 프롬프트 (PROMPT_STYLE="compact") ─────────────────────────────────────────────────────────
# 순서: 글자 금지 한 줄 → 장면(메모 한국어 문장 그대로) + 주제 → 스타일 → 구도 → 규칙 묶음(영어, 한 번씩).
_CP_TEXT = "No text, letters, numbers, logos, signs, screens or labels anywhere; no one holding a sign or board."
_CP_PRINTED = "Printed items show only blurred, unreadable marks."
_CP_PERSON = "Any person is Korean; show only hands, back view or a partial figure; no visible faces."
_CP_NO_PERSON = "No people or hands in the frame."
_CP_BANS = (
    "Never depict blood, horror, celebrity look-alikes, brand logos, before/after body comparisons, "
    "children in danger, sexual content or dangerous acts."
)
_CP_CASH = "No cash, banknotes or coins."
_CP_STYLE_BAN = "Photorealistic; not illustration, 3D, CGI, painting, plastic AI look, sepia or washed-out."
_CP_COVER_STYLE = (
    "Sharp, vivid editorial photo that stops the scroll: one clear main subject, close framing, directional light, "
    "shallow depth of field."
)
_CP_BODY_STYLE = "Natural, candid everyday-life photo: soft natural light, realistic colors, clear main subject in sharp focus."
_CP_PRODUCT = (
    "Do not draw any specific product; show the situation or need, any related object only small, blurred, in the background."
)
# 장면에 글자가 생기기 쉬운 물건이 있으면 "흐릿한 표시만" 한 문장을 더합니다.
_CP_PRINTED_WORDS = (
    "종이", "영수증", "달력", "서류", "문서", "포장", "상자", "봉투", "메모", "노트", "책 ", "책을", "책이", "책과", "책들", "도서", "라벨", "고지서", "카드", "신문",
    "잡지", "우편", "편지", "명세서", "용지", "스티커", "포스트잇", "간판", "표지판", "paper", "receipt", "calendar", "package",
    "packaging", "label", "document", "letter", "book",
)
# 장면 문장에 구도 말이 있으면 앱의 구도(BODY_SHOT_ANGLES)를 붙이지 않습니다.
_CP_FRAMING_WORDS = (
    "내려다", "위에서", "클로즈업", "가까이", "정면", "옆에서", "옆모습", "측면", "올려다", "넓게", "탑뷰", "광각", "뒤에서",
    "멀리서", "접사", "확대", "부감", "로우앵글", "하이앵글", "top-down", "overhead", "close-up", "close up", "wide shot",
    "from above", "low angle", "high angle", "macro",
)
_CP_COVER_LAYOUT = {
    "A": (  # 네이버·꿀팁: 주인공 위쪽 55%, 아래 40% 단순(제목 자리), 가운데 정사각 안
        "Wide 16:9. Main subject in the upper 55% of the frame and inside the central square (middle ~55% of the width); "
        "keep the bottom 40% simple and slightly darker for a title."
    ),
    "A_BAND": (  # 싼세계: 주인공 위쪽 절반, 사진은 맨 아래까지 꽉 채움(색 띠는 반투명)
        "Wide 16:9, full-bleed photo filling the whole frame to the bottom edge; floor/table/wall continues naturally. "
        "Main subject in the upper half, near the center and inside the central square; nothing important in the "
        "bottom 45%, but not empty. Do NOT leave any blank, black, solid-color or empty area; no panel, band, strip or frame."
    ),
}


def _extract_scene_text(scene_directive: str) -> str:
    """_scene_from_alt_directive가 만든 지시문에서 메모의 장면 문장만 꺼냅니다. 못 꺼내면(옛 무작위·주제별 장면 등) 빈 문자열."""
    m = re.match(r"MANDATORY SCENE FOR THIS IMAGE: (.*?)(?: \(Topic context only| Depict exactly this scene)", scene_directive or "", re.S)
    return m.group(1).strip() if m else ""


def _scene_has_framing(scene: str) -> bool:
    low = (scene or "").lower()
    return any(w in low for w in _CP_FRAMING_WORDS)


def _compact_prompt(
    scene: str, topic: str, kind: str, layout_en: str = "", angle: dict | None = None, money_ban: bool = False,
    product_rule: bool = False, person_allowed: bool | None = None,
) -> str:
    """짧은 이미지 프롬프트. kind = "cover"(스크롤 멈추는 표지 사진) / "body"(자연스러운 생활 사진).
    layout_en: 표지·채널별 구도 문장. angle: 본문 구도(장면에 구도 말이 없을 때만 붙임). money_ban: 현금 금지(혜택주머니·생활레이더).
    person_allowed: None이면 장면 문장의 사람 단어(_scene_mentions_person)로 판정."""
    scene = (scene or "").strip()
    has_person = _scene_mentions_person(scene) if person_allowed is None else person_allowed
    comp = layout_en
    if angle and not _scene_has_framing(scene):
        comp = (comp + " " if comp else "") + f"Camera: {angle['en']}."
    rules = []
    if any(w in scene.lower() for w in _CP_PRINTED_WORDS):
        rules.append(_CP_PRINTED)
    rules.append(_CP_PERSON if has_person else _CP_NO_PERSON)
    rules.append(_CP_BANS)
    if money_ban:
        rules.append(_CP_CASH)
    if product_rule:
        rules.append(_CP_PRODUCT)
    rules.append(_CP_STYLE_BAN)
    topic_line = f"\nTopic (context only): {topic.strip()}" if (topic or "").strip() else ""
    return (
        f"{_CP_TEXT}\n\n"
        f"Scene (follow exactly, add only small natural details): {scene}{topic_line}\n\n"
        f"Style: {_CP_COVER_STYLE if kind == 'cover' else _CP_BODY_STYLE}\n"
        + (f"Composition: {comp}\n" if comp else "")
        + "\nRules: " + " ".join(rules)
    )


def _build_image_prompt(subject: str, scene_directive: str, neutral: bool = False, memo_scene: bool = False) -> str:
    """neutral=True(꿀팁 채널 전용): 금융 블로그 표현·금융 장면 예시·현금 금지·"같은 클러스터" 문구를 빼고 생활 정보 블로그 톤으로
    씁니다. 글자 금지·실사 스타일·16:9·AI 티 금지 규칙은 그대로입니다. 기본값(False)은 예전과 한 글자도 다르지 않습니다.
    memo_scene=True(롱텀 전용, neutral=False와 함께): 금융 블로그 톤·현금 금지는 그대로 두고, "Scene selection"만
    "주제에 맞게 장면을 직접 생각해 바꾸라" 대신 "메모가 지정한 장면을 그대로 그리고 작은 소품만 더하라"로 바꿉니다."""
    if neutral:
        style_en = (
            "Style: professional editorial/documentary photography, the kind used in lifestyle information blogs "
            "and premium blogs — natural lighting, realistic textures and materials, genuine depth of field, shot "
            "as if captured with a professional DSLR camera. "
        )
        select_en = (
            "Scene selection: Depict exactly the MANDATORY SCENE above and add only small, natural details that "
            "are specific to this topic — always choosing objects that carry NO writing. Do not replace the "
            "specified scene with a different, generic one. "
        )
        money_en = ""
        select_ko = (
            "장면 선택: 위에서 지정한 MANDATORY SCENE(장소·인물·구도)을 그대로 그리고, 이 주제에 맞는 "
            "구체적인 소품이나 행동만 자연스럽게 더해주세요. 지정된 장면을 다른 일반적인 장면으로 바꾸지 마세요. "
        )
        money_ko = "계산기·모니터·휴대폰 화면처럼 숫자가 표시되는 기기는 주요 소재로 쓰지 마세요. "
    else:
        style_en = (
            f"Style: professional editorial/documentary photography, the kind used in finance news articles and "
            f"premium blogs — natural lighting, realistic textures and materials, genuine depth of field, shot as "
            f"if captured with a professional DSLR camera. "
        )
        select_en = (
            f"Scene selection: Think carefully about what is uniquely, specifically relevant to THIS exact topic "
            f"(not a generic finance scene). Consider who is involved (e.g. a young jobseeker, a new parent, an "
            f"elderly person, an office worker, a small business owner), what real-world objects or settings are "
            f"specific to it (e.g. a housing key and moving boxes for a housing subsidy, a hospital reception for "
            f"health insurance, a job center waiting area for unemployment benefits, a workplace scene for a tax "
            f"topic, a bank teller window for a loan) — always choosing objects that carry NO writing — and where "
            f"it would realistically happen. Each image across a set of related articles should look visually "
            f"distinct from the others — vary the people, setting, and objects based on the specific subject, "
            f"rather than repeating the same scene for every topic. "
        )
        money_en = (
            f"Do NOT include any physical currency at all — no banknotes, no bills, no coins, no cash of any kind, "
            f"in any pile, stack, hand, or wallet. Represent the financial benefit through the people, setting, "
            f"and objects instead, never through visible money itself. "
        )
        select_ko = (
            f"장면 선택: 위에서 지정한 MANDATORY SCENE(장소·인물·구도)을 그대로 기반으로 삼아서, 이 주제에 맞는 "
            f"구체적인 소품이나 행동을 자연스럽게 더해주세요. 절대 '돈 세는 손'이나 '책상에서 펜 들고 서류에 "
            f"쓰는 손' 같은 장면으로 되돌아가지 마세요 — 이미 금지된 장면입니다. 같은 클러스터 안의 여러 "
            f"이미지들도 서로 겹치지 않게 다양한 장면으로 표현해주세요. "
        )
        money_ko = (
            f"지폐·동전 등 실제 돈(현금) 이미지는 어떤 형태로도 절대 포함하지 마세요. 계산기·모니터·휴대폰 "
            f"화면처럼 숫자가 표시되는 기기는 주요 소재로 쓰지 마세요. "
        )
        if memo_scene:
            select_en = (
                "Scene selection: Depict exactly the MANDATORY SCENE above and add only small, natural details that "
                "are specific to this topic — always choosing objects that carry NO writing. Do NOT replace the "
                "specified scene with a different one, and do not invent another scene. "
            )
            select_ko = (
                "장면 선택: 위에서 지정한 MANDATORY SCENE(장소·인물·구도)을 그대로 그리고, 이 주제에 맞는 "
                "작은 소품만 자연스럽게 더해주세요. 지정된 장면을 다른 장면으로 바꾸지 마세요. "
            )
    return (
        f"═══ MANDATORY SCENE FOR THIS IMAGE (highest priority, follow exactly) ═══ "
        f"{scene_directive} "
        f"═══ END MANDATORY SCENE ═══ "
        f"═══ HIGHEST PRIORITY RULE, READ THIS FIRST ═══ "
        f"This image must contain ZERO text, letters, numbers, or symbols of any kind. This is the single "
        f"most important rule and overrides every other instruction below. Before finalizing the scene, ask: "
        f"\"does any object in this scene naturally carry writing?\" If yes, remove that object or replace it. "
        f"Specifically NEVER include, even blank or blurry: signs, posters, banners, placards, whiteboards, "
        f"flip charts, presentation boards, handwritten notes, sticky notes, name tags, ID badges, business "
        f"cards, printed timelines, number lines, diagrams with labels, charts with axis labels, calendars "
        f"with visible numbers, computer/phone screens, calculator displays, book covers, or any signage. "
        f"Do NOT depict a person holding up or pointing at a sign, chart, poster, or board of any kind — this "
        f"is a common failure mode where AI tries to render text and produces broken, garbled gibberish "
        f"characters that ruin the image. If you need to imply a timeline, a deadline, or a comparison, do it "
        f"through purely visual/symbolic means (calendar pages without numbers, an hourglass, a clock, a door, "
        f"a path, a plant growing) — never through a labeled chart, graph, or written diagram. "
        f"═══ END OF HIGHEST PRIORITY RULE ═══ "
        f"Now, the photo itself: a high-quality, photorealistic photograph representing this specific topic: "
        f"\"{subject}\". "
        f"{style_en}"
        f"{select_en}"
        f"{money_en}"
        f"Avoid devices with visible numeric displays or screens (calculators, computer monitors, phone "
        f"screens) as the main subject. If a desk/office scene is used, keep any screens fully off or angled "
        f"away from camera. "
        f"Avoid abstract icon-like symbolism or random object collages. "
        f"Color palette: natural, balanced, and tasteful — choose colors that suit the specific subject matter, "
        f"with healthy saturation and good contrast. Do NOT let any single hue dominate the entire image, and "
        f"do NOT make it monochromatic, faded, washed-out, or sepia-toned either. Wide cinematic 16:9 "
        f"composition. "
        f"Absolutely avoid: illustration, vector art, flat design, cartoon, anime, clip art, 3D render, CGI "
        f"look, painting style, dark vignette backgrounds, glowing outlines, muddy or gradient noise "
        f"backgrounds, artificial/plastic/airbrushed AI look. Aim for authentic, slightly imperfect, natural "
        f"photographic realism that doesn't look AI-generated, the kind of real photo licensed from a "
        f"professional stock photography site. "
        f"═══ REMINDER: ZERO TEXT, ZERO SIGNS, ZERO WRITTEN DIAGRAMS ═══ "
        f"위 주제(\"{subject}\")를 표현하는 고품질의 실사 사진(포토리얼리스틱)입니다. "
        f"가장 중요한 규칙(다른 모든 지시보다 우선함): 이 이미지 안에는 글자·숫자·기호가 단 하나도 있으면 "
        f"안 됩니다. 특히 다음은 절대 등장시키지 마세요 — 표지판, 포스터, 배너, 팻말, 화이트보드, 발표용 "
        f"보드, 손글씨 메모, 포스트잇, 명찰, 명함, 인쇄된 타임라인, 숫자가 있는 눈금선, 라벨이 붙은 도표나 "
        f"그래프, 숫자가 보이는 달력, 컴퓨터·휴대폰 화면, 계산기 화면, 책 표지, 간판. **인물이 표지판이나 "
        f"차트, 포스터, 보드 같은 것을 들고 있거나 가리키는 구도는 절대 만들지 마세요** — 이게 AI가 글자를 "
        f"그리려다 깨진 외계어 같은 글자를 만들어내는 가장 흔한 실패 원인입니다. 기간이나 비교, 흐름을 "
        f"표현하고 싶으면 숫자가 적힌 도표 대신, 순수하게 시각적인 상징(숫자 없는 달력 낱장, 모래시계, 시계, "
        f"문, 길, 자라나는 화분 등)으로 표현해주세요. "
        f"{select_ko}"
        f"{money_ko}"
        f"색감은 주제에 자연스럽게 어울리는 균형 잡힌 색으로, 특정 한 가지 색이 화면 전체를 지배하지 않게 "
        f"해주세요. AI가 만든 티가 나는 인공적이고 매끈한 느낌은 피하고, 실제 사진처럼 자연스러운 질감과 "
        f"조명으로 표현해주세요. {KOREAN_ONLY_RULE}"
    )


def generate_body_images(
    prompts: list[str], article_id: str, scenes: list[str] | None = None, scene_keyword: str = "",
    memo_scenes: list[str | None] | None = None, angles: list[dict] | None = None,
) -> list[str]:
    """섹션별 프롬프트 리스트를 받아 16:9 이미지를 생성하고, 웹 경로 리스트를 반환합니다.
    prompt 문자열("키워드 - 소제목/alt")에서 대상을 추정해 장면을 고르고, 이 배치(글 하나의
    본문 이미지 전체) 안에서는 최대 1/3장까지만 사람이 등장하며, 같은 글 안에서 장면이
    겹치지 않도록 이 함수 호출 동안은 used_scenes를 계속 같이 씁니다.
    scenes: 꿀팁 채널 전용 — 이미지마다 쓸 장면 설명(메모 alt 등)을 주면 정해진 장면 목록 대신 그 문장을 그대로
    MANDATORY SCENE으로 쓰고, 금융 표현을 뺀 중립 프롬프트를 씁니다. None(기본)이면 예전 그대로입니다.
    memo_scenes: 롱텀 전용 — 이미지마다 메모의 장면 문장(없는 자리는 None)을 주면, 그 자리는 문장을 그대로 MANDATORY SCENE으로
    쓰고(금융 블로그 톤·현금 금지는 유지, 사람은 문장에 사람이 나올 때만) 1/3 사람 규칙을 적용하지 않습니다. None인 자리는
    예전 방식(정해진 장면 목록)이며, 1/3 규칙은 그런 자리끼리만 셉니다.
    angles: 이미지마다 구도(BODY_SHOT_ANGLES 항목, assign_body_shot_angles 결과)를 주면 장면 지시문에 구도 문장을 덧붙입니다(메모 장면에 구도가
    이미 있으면 메모 구도 우선). None(기본)이면 예전 그대로입니다."""
    paths = []
    if memo_scenes is not None:
        n_topic = sum(1 for s in memo_scenes if not s)
        person_flags_topic = iter(_plan_person_flags(n_topic))
    else:
        person_flags = _plan_person_flags(len(prompts))
    used_scenes: set = set()

    def _angle_at(k: int):
        return angles[k] if angles and k < len(angles) else None

    for i, prompt in enumerate(prompts):
        if memo_scenes is not None:
            if memo_scenes[i]:
                scene_directive = _with_body_angle(_scene_from_alt_directive(memo_scenes[i], scene_keyword), _angle_at(i))
                full_prompt = _build_image_prompt(prompt, scene_directive, memo_scene=True)
                if PROMPT_STYLE == "compact":  # 혜택주머니 본문: 짧은 프롬프트(현금 금지 포함)
                    full_prompt = _compact_prompt(memo_scenes[i], scene_keyword, "body", angle=_angle_at(i), money_ban=True)
            else:
                scene_directive = _with_body_angle(
                    _topic_scene_directive(prompt, want_person=next(person_flags_topic), used_scenes=used_scenes), _angle_at(i)
                )
                full_prompt = _build_image_prompt(prompt, scene_directive)
        elif scenes is not None:
            scene_directive = _with_body_angle(_scene_from_alt_directive(scenes[i], scene_keyword), _angle_at(i))
            full_prompt = _build_image_prompt(prompt, scene_directive, neutral=True)
            if PROMPT_STYLE == "compact" and (scenes[i] or "").strip():  # 정보 채널 본문: 짧은 프롬프트
                full_prompt = _compact_prompt(scenes[i], scene_keyword, "body", angle=_angle_at(i))
        else:
            scene_directive = _with_body_angle(
                _topic_scene_directive(prompt, want_person=person_flags[i], used_scenes=used_scenes), _angle_at(i)
            )
            full_prompt = _build_image_prompt(prompt, scene_directive)
        img = _mock_image(prompt) if USE_MOCK else _call_gpt_image(full_prompt)
        filename = f"{article_id}_body_{i}.jpg"
        filepath = os.path.join(_current_output_dir(), filename)
        _save_gen_jpg(img, filepath)
        paths.append("/" + filepath.replace(os.sep, "/"))
    return paths


_BOTTOM_TOPIC_RE = re.compile(r"(아래쪽|아래|하단)\s*(은|는)")
_BOTTOM_PLAIN_RE = re.compile(r"단순|어둡|어두운|비워|비운|여백")


def _strip_bottom_plain_phrases(scene: str) -> str:
    """싼세계 표지(A_BAND) 전용: 표지 장면 문장에서 "아래쪽은 어두운 ○○로 단순하게" 같은 구절(마침표·쉼표 단위)만 뺍니다.
    사진은 맨 아래까지 꽉 채워야 해서 이 구절이 남아 있으면 사진 AI가 아래를 검게 칠합니다. 저장된 데이터는 바꾸지 않고
    사진 지시문을 만들 때만 걸러 씁니다. 다 빠져 비면 원래 문장을 그대로 돌려줍니다."""
    if not scene:
        return scene
    kept = []
    for tok in re.findall(r"[^.,]+[.,]?\s*", scene):
        if _BOTTOM_TOPIC_RE.search(tok) and _BOTTOM_PLAIN_RE.search(tok):
            continue
        kept.append(tok)
    out = "".join(kept).strip()
    if out.endswith(","):
        out = out[:-1].rstrip() + "."
    return out or scene


def generate_cover_backgrounds(
    keyword: str, count: int, article_id: str, situational_only: bool = False,
    scene_from_alt: bool = False, scene_alt: str | None = None, memo_cover_scene: str | None = None,
    cover_layout: str = "A", lt_cover: bool = False,
) -> list[str]:
    """
    텍스트 없는 16:9 표지 배경 이미지를 count장 생성하고, 내부 파일 경로(합성용) 리스트를
    반환합니다. situational_only=True(숍 채널용)면, 특정 제품의 형태를 AI가 직접 그리지
    않고 "이 제품이 왜 필요한 상황인지"만 보여주는 무드컷으로 만듭니다 — 실제 제품과 AI가
    그린 제품 모양이 달라서 생기는 오인 광고 리스크를 표지 단계에서부터 원천 차단하기
    위함입니다. 실제 제품 모양은 레퍼런스 이미지 기반 바리에이션(별도 기능)에서만
    정확하게 반영됩니다.
    memo_cover_scene: 롱텀 전용 — 메모의 표지 alt 문장이 있으면 그 문장을 표지 배경의 MANDATORY SCENE으로 그대로 씁니다(금융 블로그 톤·
    현금 금지는 유지, 사람은 문장에 나올 때만). None/빈 값이면 예전 롱텀 방식(주제별 장면 목록) 그대로입니다.
    scene_from_alt: 꿀팁 채널 전용 — True면 메모의 표지 alt(scene_alt)를 그대로 MANDATORY SCENE으로 쓰고(없으면 키워드로 어울리는
    장면을 직접 구성하게 함), 정해진 장면 목록과 금융 표현을 쓰지 않습니다. 제목용 중앙 여백 지시는 그대로입니다. 기본값은 예전 그대로.
    숍(situational_only=True)에서 memo_cover_scene(메모의 "표지 장면:")이 있으면 무작위 장면 대신 그 장면을 MANDATORY SCENE으로 씁니다
    ("특정 상품 모양을 그리지 말 것" 규칙은 그대로). 없으면 예전처럼 무작위 장면입니다.
    cover_layout: COVER_IMPACT_MODE일 때 글자가 덮는 자리를 피하는 구도 — "A"(숍·꿀팁 시안 A, 아래쪽에 글자) / "C"(롱텀 C, 위 470px만 씀).
    lt_cover: 롱텀 새 표지(compose_longterm_cover)용 호출일 때만 True. True면 COVER_IMPACT_MODE 대신 LT_COVER_IMPACT_MODE를 따릅니다
    (False일 때 구도 지시는 cover_layout별 _lt_old_style_layout). 숍·꿀팁은 기본값(False)이라 영향이 없습니다.
    """

    def _build_cover_prompt(scene_directive: str, no_person: bool = False) -> str:
        if PROMPT_STYLE == "compact":  # 짧은 프롬프트. 메모 장면 문장을 못 꺼내는 옛 방식 장면은 아래 예전 프롬프트로 갑니다.
            _scene = _extract_scene_text(scene_directive)
            if _scene:
                if lt_cover:  # 혜택주머니: 기존 A/B/C 구도, 사람 판정은 기존 LT_COVER_NO_PERSON_GUARD 동작을 따름
                    # 얼굴 문장(얼굴 전체가 들어가게 등)은 "얼굴 안 보이게" 규칙과 부딪히므로 빼고, A/B/C별 영역 구도만 씁니다.
                    _layout = (
                        _lt_old_style_layout(cover_layout, True)[0] if not LT_COVER_IMPACT_MODE
                        else _COVER_IMPACT_LAYOUT_EN.get(cover_layout, _COVER_IMPACT_LAYOUT_EN["A"])
                    ).removeprefix("Composition: ").replace("MANDATORY SCENE", "scene").strip()
                    _layout += " Keep the main subject within the central 53% of the frame width so it survives a square thumbnail crop."
                    if LT_COVER_UNIFIED:  # 새 표지: 글자 자리 비우기 구도 문구 없이 표지 장면 지시만 씁니다
                        _layout = ""
                    _allowed = None if (LT_COVER_NO_PERSON_GUARD or _scene_mentions_person(_scene)) else True
                else:
                    _layout = _CP_COVER_LAYOUT.get(cover_layout) or _CP_COVER_LAYOUT["A"]
                    _allowed = None
                return _compact_prompt(
                    _scene, keyword, "cover", layout_en=_layout, money_ban=bool(lt_cover),
                    product_rule=bool(situational_only), person_allowed=_allowed,
                )
        # 꿀팁(scene_from_alt)과 숍(situational_only)은 금융 블로그 문구 대신 생활 정보 톤을 씁니다. 롱텀은 예전 금융 톤 그대로입니다.
        if scene_from_alt or situational_only:
            style_en = "premium lifestyle blog covers"
            money_en = "Avoid calculators, monitors, or phone screens as the main subject. "
            end_en = "premium lifestyle blog cover"
            money_ko = "계산기·모니터·휴대폰 화면은 주요 소재로 쓰지 마세요. "
            nogo_ko = "지정된 장면을 다른 일반적인 장면으로 바꾸지 마세요. "
        else:
            style_en = "premium finance blog covers"
            money_en = (
                "Do NOT include any physical currency at all — no banknotes, no bills, no coins, no cash of any kind. "
                "Represent the financial topic through people, setting, and objects instead, never through visible "
                "money. Avoid calculators, monitors, or phone screens as the main subject. "
            )
            if no_person:
                money_en = money_en.replace("through people, setting, and objects", "through the setting and objects")
            end_en = "premium finance blog cover"
            money_ko = (
                "지폐·동전 등 실제 돈 이미지는 어떤 형태로도 절대 포함하지 마세요. "
                "계산기·모니터·휴대폰 화면은 주요 소재로 쓰지 마세요. "
            )
            nogo_ko = (
                "뻔한 '돈+계산기' 조합이나 '책상에서 펜 든 손' "
                "장면으로 되돌아가지 마세요 — 이미 금지된 장면입니다. "
            )
        # 숍·꿀팁 4채널(lt_cover=False)에서만 얼굴을 뺍니다. COVER_NO_FACE=False면 아래 조각들은 전부 예전 문장 그대로입니다.
        no_face = COVER_NO_FACE and (scene_from_alt or situational_only) and not lt_cover
        face_rule_en = _COVER_NO_FACE_RULE_EN if no_face else ""
        face_rule_ko = _COVER_NO_FACE_RULE_KO if no_face else ""
        focus_en = (
            "the environment and the traces of the problem (hands only if the scene includes a person)" if no_face else "a person, their environment, and their mood/problem"
        )
        focus_ko = "환경·문제의 흔적으로만(손은 장면에 사람이 있을 때만)" if no_face else "사람·환경·분위기로만"
        product_rule = (
            (
                "═══ MANDATORY: DO NOT DEPICT ANY SPECIFIC PRODUCT ═══ "
                f"This cover is for a shopping/product-recommendation article about '{keyword}', but you must "
                "NOT draw any specific product, device, gadget, or package as a recognizable object in this "
                "image — AI-generated product shapes often don't match the real product being sold, which can "
                "mislead viewers. Instead, depict the everyday SITUATION or NEED that would make someone search "
                f"for this kind of product — focus entirely on {focus_en} "
                "(e.g., discomfort, mess, inconvenience the product category would solve). If an object from this "
                "category must appear at all, keep it small, out of focus, or only as a vague silhouette in the "
                "background — never the clear subject of the shot. "
                "═══ END OF RULE ═══ "
                "표지에 특정 제품의 구체적인 모양을 그리지 마세요 — AI가 그린 제품 형태가 실제 판매 상품과 "
                f"다를 수 있어 오해를 줄 수 있습니다. 대신 '이런 제품이 왜 필요한 상황인지'를 {focus_ko} "
                "보여주세요(불편함, 답답함, 번거로움 등). 혹시 관련 물건이 화면에 들어가더라도 흐릿하거나 "
                "작게, 배경 실루엣 정도로만 두고 절대 화면의 주인공으로 만들지 마세요."
            )
            if situational_only
            else ""
        )
        if LT_COVER_IMPACT_MODE if lt_cover else COVER_IMPACT_MODE:
            style_block_en = (
                f"Style: {_COVER_NO_FACE_STYLE_EN if no_face else _COVER_IMPACT_STYLE_EN} "
                f"Build the scene from the MANDATORY SCENE above, adding topic-specific props/activity, using "
                f"only objects that carry no writing. "
            )
            layout_en = _COVER_IMPACT_LAYOUT_EN.get(cover_layout, _COVER_IMPACT_LAYOUT_EN['A'])
            layout_ko_base = _COVER_IMPACT_LAYOUT_KO.get(cover_layout, _COVER_IMPACT_LAYOUT_KO['A'])
            if lt_cover and LT_COVER_UNIFIED:  # 새 표지: 구도 문구 없음
                layout_en = layout_ko_base = ""
            if no_face:  # 구도 지시에서 "얼굴" 언급 빼기
                layout_en = layout_en.replace("main subject and any face within", "main subject within")
                layout_ko_base = layout_ko_base.replace("주인공과 얼굴은", "주인공은")
            _unified_lt = bool(lt_cover and LT_COVER_UNIFIED)  # 새 표지: 820px 구도 문구도 넣지 않음
            compose_color_en = (
                f"{layout_en}"
                f"{'' if _unified_lt else _COVER_IMPACT_COMMON_EN}"
                f"{face_rule_en}"
                f"{_COVER_IMPACT_BANS_EN}"
                f"Do NOT make it monochromatic, faded, washed-out, or sepia-toned. "
            )
            feel_en = f"Should feel like a striking, scroll-stopping editorial photograph for a {end_en}."
            layout_ko = (
                f"{layout_ko_base}"
                f"{'' if _unified_lt else _COVER_IMPACT_COMMON_KO}{face_rule_ko}{_COVER_IMPACT_BANS_KO}"
            )
            style_ko = _COVER_NO_FACE_STYLE_KO if no_face else (
                "스크롤을 멈추게 하는 임팩트 있는 사진으로 만들어주세요: 주인공은 하나, 가깝거나 중간 가까운 구도, "
                "사람이 나오면 자연스럽고 뚜렷하게 읽히는 표정(놀람·걱정·답답함·안도), 문제나 핵심 순간을 생생하고 "
                "구체적으로, 방향성 있는 빛으로 강한 명암 대비, 선명하지만 현실적인 색, 주인공이 살아나는 얕은 심도. "
                "그래도 실사처럼 자연스럽게, AI 티 나는 매끈한 느낌은 피해주세요. "
            )
        else:
            style_block_en = (
                f"Style: professional editorial/documentary photography, the kind used on {style_en} — "
                f"natural lighting, realistic textures, genuine depth of field, shot as if with a professional DSLR "
                f"camera. Build the scene from the MANDATORY SCENE above, adding topic-specific props/activity, using "
                f"only objects that carry no writing. "
            )
            if lt_cover:
                # 롱텀(LT_COVER_IMPACT_MODE=False): 예전 롱텀 스타일 문장 + 템플릿별 실제 사용 영역 구도 + 820px 규칙 + 금지선
                lt_layout_en, lt_layout_ko = _lt_old_style_layout(cover_layout, no_person)
                if LT_COVER_UNIFIED:  # 새 표지: 글자 자리 비우기·820px 구도 문구 없이 장면 지시만
                    lt_layout_en = lt_layout_ko = ""
                compose_color_en = (
                    f"{lt_layout_en}"
                    f"{'' if LT_COVER_UNIFIED else _COVER_IMPACT_COMMON_EN}"
                    f"{_COVER_IMPACT_BANS_EN}"
                    f"Color palette: natural, balanced, and tasteful. Do NOT let any single hue dominate the entire image, "
                    f"and do NOT make it monochromatic, faded, washed-out, or sepia-toned. "
                )
                layout_ko = f"{lt_layout_ko}{'' if LT_COVER_UNIFIED else _COVER_IMPACT_COMMON_KO}{_COVER_IMPACT_BANS_KO}"
            else:
                compose_color_en = (
                    f"{face_rule_en}"
                    f"Compose with generous empty space in the center-left or center area suitable for a text headline "
                    f"overlay, wide cinematic 16:9 composition. "
                    f"Color palette: natural, balanced, and tasteful. Do NOT let any single hue dominate the entire image, "
                    f"and do NOT make it monochromatic, faded, washed-out, or sepia-toned. "
                )
                layout_ko = (
                    f"{face_rule_ko}"
                    "제목 글자가 "
                    "나중에 올라갈 수 있게 화면 중앙 쪽에 여백을 넉넉히 남겨주세요. "
                )
            feel_en = f"Should feel like a licensed stock photo for a {end_en}."
            if no_person:
                feel_en = f"Should feel like a high-end editorial still-life / interior photograph for a {end_en}, with no people."
            style_ko = (
                "색감은 주제에 자연스럽게 어울리는 "
                "균형 잡힌 색으로 표현하되 한 가지 색이 화면을 지배하지 않게 해주세요. AI 티 나는 매끈한 느낌은 "
                "피하고 자연스러운 사진 질감으로 표현해주세요. "
            )
        no_person_block = _LT_NO_PERSON_BLOCK if no_person else ""
        scene_ko_label = "장소·사물·구도" if no_person else "장소·인물·구도"
        tail_rule = no_person_block if no_person else KOREAN_ONLY_RULE
        return (
            f"═══ MANDATORY SCENE FOR THIS IMAGE (highest priority, follow exactly) ═══ "
            f"{scene_directive} "
            f"═══ END MANDATORY SCENE ═══ "
            f"{no_person_block}"
            f"{product_rule} "
            f"═══ HIGHEST PRIORITY RULE, READ THIS FIRST ═══ "
            f"This image must contain ZERO text, letters, numbers, or symbols of any kind. This overrides every "
            f"other instruction below. NEVER include, even blank or blurry: signs, posters, banners, placards, "
            f"whiteboards, presentation boards, handwritten notes, printed timelines, number lines, labeled charts "
            f"or diagrams, calendars with visible numbers, computer/phone screens, calculator displays, book "
            f"covers, or any signage. Do NOT depict a person holding up or pointing at a sign, chart, poster, or "
            f"board of any kind — this is the most common failure mode where AI renders broken, garbled gibberish "
            f"text. If you need to imply a timeline or comparison, use purely visual means (a calendar without "
            f"numbers, an hourglass, a clock, a door, a path) — never a labeled chart or written diagram. "
            f"═══ END OF HIGHEST PRIORITY RULE ═══ "
            f"A high-quality, photorealistic photograph for a blog cover image, related to the topic: '{keyword}'. "
            f"{style_block_en}"
            f"{money_en}"
            f"{compose_color_en}"
            f"Absolutely avoid: illustration, vector art, flat design, cartoon, clip art, 3D render, CGI look, "
            f"painting style, dark vignette backgrounds, glowing outlines, muddy or gradient noise backgrounds, "
            f"artificial/plastic/airbrushed AI look. {feel_en} "
            f"═══ REMINDER: ZERO TEXT, ZERO SIGNS, ZERO WRITTEN DIAGRAMS ═══ "
            f"실제 전문 사진작가가 찍은 것 같은 고품질 실사 사진입니다. "
            f"가장 중요한 규칙(다른 모든 지시보다 우선함): 이 이미지 안에는 글자·숫자·기호가 단 하나도 있으면 "
            f"안 됩니다. 표지판, 포스터, 배너, 팻말, 화이트보드, 발표용 보드, 손글씨 메모, 인쇄된 타임라인, "
            f"숫자가 있는 눈금선, 라벨 붙은 도표나 그래프, 숫자 보이는 달력, 화면, 책 표지, 간판을 절대 넣지 "
            f"마세요. **인물이 표지판이나 차트, 포스터, 보드를 들고 있거나 가리키는 구도는 절대 만들지 마세요** "
            f"— 이게 AI가 깨진 외계어 글자를 만들어내는 가장 흔한 원인입니다. "
            f"장면 선택: 위 MANDATORY SCENE({scene_ko_label})을 그대로 기반으로, 이 주제('{keyword}')에 맞는 "
            f"구체적 소품이나 행동을 자연스럽게 더해주세요. {nogo_ko}{money_ko}{layout_ko}"
            f"{style_ko}{tail_rule}"
        )

    paths = []
    if scene_from_alt:
        # 꿀팁 채널: 정해진 장면 목록 없이, 메모의 표지 alt를 장면으로 씁니다(없으면 키워드로 어울리는 장면을 직접 구성).
        if scene_alt and scene_alt.strip():
            if cover_layout == "A_BAND":  # 싼세계: 아래를 어둡게·비우라는 구절은 "맨 아래까지 꽉 채움" 지시와 부딪히므로 뺍니다
                scene_alt = _strip_bottom_plain_phrases(scene_alt)
            scene_directive = _scene_from_alt_directive(scene_alt, keyword)
        else:
            scene_directive = _scene_from_alt_directive(
                f"Compose a natural, realistic everyday scene that fits the topic '{keyword}' as closely as "
                f"possible, using objects and a setting that people actually associate with this topic.",
                "",
            )
        for i in range(count):
            prompt = _build_cover_prompt(scene_directive)
            img = _mock_image(keyword) if USE_MOCK else _call_gpt_image(prompt, COVER_IMAGE_QUALITY)
            filename = f"{article_id}_coverbg_{i}.jpg"
            filepath = os.path.join(_current_output_dir(), filename)
            _save_gen_jpg(img, filepath)
            paths.append(filepath)
    elif situational_only:
        # 숍 채널: 대상 추정은 적용하지 않습니다. 메모에 "표지 장면:"이 있으면 그 장면을 쓰고(제품 모양 금지 규칙은 product_rule이 그대로 유지),
        # 없으면 COVER_NO_FACE=True일 때 키워드 기반의 사람 없는 장면(_random_scene_directive_no_face), False일 때 예전 방식(매번 무작위 인물 1명)입니다.
        memo_shop_scene = (
            _scene_from_alt_directive(memo_cover_scene, keyword) if memo_cover_scene and memo_cover_scene.strip() else ""
        )
        for i in range(count):
            if memo_shop_scene:
                scene_directive = memo_shop_scene
            elif COVER_NO_FACE:  # 얼굴 빼기: 사람 주인공 대신 장소 + 문제의 흔적 클로즈업
                scene_directive = _random_scene_directive_no_face(keyword)
            else:
                scene_directive = _random_scene_directive()
            prompt = _build_cover_prompt(scene_directive)
            img = _mock_image(keyword) if USE_MOCK else _call_gpt_image(prompt, COVER_IMAGE_QUALITY)
            filename = f"{article_id}_coverbg_{i}.jpg"
            filepath = os.path.join(_current_output_dir(), filename)
            _save_gen_jpg(img, filepath)
            paths.append(filepath)
    elif memo_cover_scene and memo_cover_scene.strip():
        # 롱텀(새 방식): 메모의 표지 alt를 장면으로 그대로 씁니다. 사람 1/3 규칙은 적용하지 않습니다.
        # 장면에 사람이 없으면(LT_COVER_NO_PERSON_GUARD) 사람을 유도하는 문장을 빼고 "사람 없음" 블록을 넣습니다.
        no_person = bool(lt_cover and LT_COVER_NO_PERSON_GUARD and not _scene_mentions_person(memo_cover_scene))
        scene_directive = _scene_from_alt_directive(memo_cover_scene, keyword, no_person=no_person)
        for i in range(count):
            prompt = _build_cover_prompt(scene_directive, no_person=no_person)
            img = _mock_image(keyword) if USE_MOCK else _call_gpt_image(prompt, COVER_IMAGE_QUALITY)
            filename = f"{article_id}_coverbg_{i}.jpg"
            filepath = os.path.join(_current_output_dir(), filename)
            _save_gen_jpg(img, filepath)
            paths.append(filepath)
    else:
        # 롱텀: 주제(키워드)에서 대상을 추정하고, 이 표지 세트 안에서는 최대 1/3장까지만
        # 사람이 등장하며, 같은 글의 표지끼리 장면이 겹치지 않게 합니다.
        person_flags = _plan_person_flags(count)
        used_scenes: set = set()
        for i in range(count):
            scene_directive = _topic_scene_directive(keyword, want_person=person_flags[i], used_scenes=used_scenes)
            prompt = _build_cover_prompt(scene_directive)
            img = _mock_image(keyword) if USE_MOCK else _call_gpt_image(prompt, COVER_IMAGE_QUALITY)
            filename = f"{article_id}_coverbg_{i}.jpg"
            filepath = os.path.join(_current_output_dir(), filename)
            _save_gen_jpg(img, filepath)
            paths.append(filepath)
    return paths


def _build_reference_cover_prompt(keyword: str, style: str) -> str:
    """
    상품 레퍼런스 사진 1장을 기반으로 표지를 만들 때 쓰는 프롬프트. style: "desk"(책상 위에
    놓인 모습) 또는 "use"(일상적으로 사용하는 상황). 무드컷(situational_only)과 반대로,
    여기서는 제품 형태를 실제와 다르게 그리면 안 됩니다 — 이게 핵심 차이입니다.
    """
    base = (
        f"This is a real product photo related to '{keyword}'. Keep the product itself — its exact shape, "
        f"color, proportions, logos, and any text/labels on it — completely unchanged from the reference "
        f"image. Do not redesign, distort, resize unnaturally, or invent product details; the product must "
        f"remain clearly recognizable and accurate to the original. "
    )
    if style == "desk":
        base += (
            "Show the product placed naturally on a clean desk or table in a well-lit, tidy everyday "
            "setting, as if arranged for a lifestyle blog cover photo. "
        )
    else:
        base += (
            "Show the product being used naturally in a realistic everyday situation, ideally with a "
            "person's hands or a person in frame interacting with it in a candid, unstaged way. "
        )
    base += (
        "Professional editorial photography look, natural lighting, genuine depth of field, no added text. "
        "Leave generous empty space in the center-left or center area of the frame, suitable for a text "
        "headline overlay to be added later. This image must contain zero text, letters, numbers, signs, "
        "or symbols of any kind anywhere in the frame. "
        f"'{keyword}' 관련 실제 상품 사진입니다. 제품의 형태·색상·비율·로고·표면 문구는 원본 사진과 "
        "완전히 동일하게 유지하고, 다른 모양으로 왜곡하거나 새로 그리지 마세요 — 실제 상품과 다르면 "
        "허위광고가 될 수 있습니다. "
    )
    if style == "desk":
        base += "제품을 깔끔하게 정돈된 책상이나 테이블 위에 자연스럽게 놓인 모습으로 보여주세요. "
    else:
        base += "사람이 실제로 사용하는 자연스러운 일상 장면 속에서 제품을 보여주세요. "
    base += (
        "전문적인 편집 사진 느낌으로, 자연광과 자연스러운 심도로 표현하고 별도의 글자는 넣지 마세요. "
        "나중에 제목 글자를 올릴 수 있도록 화면 중앙~좌측에 여백을 넉넉히 남겨주세요. 이 이미지 안에는 "
        "글자·숫자·기호·간판이 전혀 없어야 합니다. 화면에 사람이나 손이 나오면 반드시 한국인이어야 합니다."
    )
    return base


def generate_cover_from_reference(reference_image_path: str, keyword: str, article_id: str, count: int) -> list[str]:
    """
    상품이 1~2개뿐인 글(VS형)의 표지를, 상품 레퍼런스 사진 기반으로 만듭니다 — 무드컷과
    달리 실제 제품 형태를 유지한 채 "책상 위에 놓인 모습" 또는 "일상 사용 장면"으로
    편집합니다. 상품 바리에이션과 같은 API(_call_gpt_image_edit)를 쓰되, 표지 규격
    (GEN_SIZE)으로 생성한 뒤 16:9로 중앙 크롭해서 저장합니다.
    반환: 내부 파일 경로 리스트 (generate_cover_backgrounds와 동일하게 OS 경로).
    """
    styles = (["desk", "use"] * ((count // 2) + 1))[:count]
    desk_count = styles.count("desk")
    use_count = styles.count("use")

    if USE_MOCK:
        labeled = [("desk", f"{keyword} 책상 위 이미지") for _ in range(desk_count)] + [
            ("use", f"{keyword} 실사용 이미지") for _ in range(use_count)
        ]
        images = [(style, _mock_image(label)) for style, label in labeled]
    else:
        images = []
        if desk_count:
            prompt = _build_reference_cover_prompt(keyword, "desk")
            images += [
                ("desk", img)
                for img in _call_gpt_image_edit(reference_image_path, prompt, n=desk_count, size=GEN_SIZE, quality=COVER_IMAGE_QUALITY)
            ]
        if use_count:
            prompt = _build_reference_cover_prompt(keyword, "use")
            images += [
                ("use", img)
                for img in _call_gpt_image_edit(reference_image_path, prompt, n=use_count, size=GEN_SIZE, quality=COVER_IMAGE_QUALITY)
            ]

    paths = []
    for i, (style, img) in enumerate(images):
        cropped = _center_crop_16_9(img)
        filename = f"{article_id}_coverbg_ref_{i}_{style}.jpg"
        filepath = os.path.join(_current_output_dir(), filename)
        _save_gen_jpg(cropped, filepath)
        paths.append(filepath)
    return paths


def _build_user_cover_prompt(user_prompt: str, keyword: str, no_product: bool, has_reference: bool) -> str:
    """사용자가 직접 쓴 장면 설명을 맨 앞(MANDATORY SCENE)에 두고, 뒤에 고정 규칙을 붙입니다."""
    prompt = (
        "═══ MANDATORY SCENE FOR THIS IMAGE (highest priority, follow exactly) ═══ "
        f"{user_prompt.strip()} "
        "═══ END MANDATORY SCENE ═══ "
    )
    if has_reference:
        prompt += (
            "This is based on the attached real product photo. Keep the product itself — its exact shape, color, "
            "proportions, logos, and any text/labels on it — completely unchanged from the reference image; "
            "do not redesign or invent product details. "
        )
    elif no_product:
        prompt += (
            "═══ MANDATORY: DO NOT DEPICT ANY SPECIFIC PRODUCT ═══ "
            f"This cover is for a shopping/product-recommendation article about '{keyword}', but you must NOT "
            "draw any specific product, device, gadget, or package as a recognizable object in this image — "
            "AI-generated product shapes often don't match the real product being sold, which can mislead "
            "viewers. Depict only the everyday situation, person, environment and mood. If a related object "
            "must appear at all, keep it small, out of focus, or a vague background silhouette — never the "
            "clear subject of the shot. "
            "═══ END OF RULE ═══ "
            "표지에 특정 제품의 구체적인 모양을 그리지 마세요 — 실제 판매 상품과 다르면 오해를 줄 수 있습니다. "
        )
    prompt += (
        "═══ FIXED RULES ═══ "
        "This image must contain ZERO text, letters, numbers, signs, posters, screens, or logos of any kind — "
        "no brand logos anywhere. "
        "A high-quality, photorealistic photograph, natural lighting, realistic textures, shot as if with a "
        "professional camera. Wide cinematic 16:9 composition, with generous empty space in the lower-center "
        "area where a headline text will be overlaid later. "
        "글자·숫자·간판·화면·로고는 전혀 없어야 하며, 실사 사진 느낌의 16:9 넓은 구도로, 나중에 문구가 올라갈 "
        "가운데 아래쪽에 여백을 남겨주세요. "
        f"{KOREAN_ONLY_RULE}"
    )
    return prompt


def generate_cover_from_user_prompt(
    user_prompt: str,
    keyword: str,
    article_id: str,
    count: int,
    no_product: bool,
    reference_path: str | None = None,
) -> list[tuple[str, str]]:
    """
    사용자가 직접 쓴 프롬프트로 표지 배경(글자 없음)을 count장 만듭니다.
    reference_path가 있으면 그 사진을 기준으로 편집(_call_gpt_image_edit), 없으면 텍스트 생성.
    USE_MOCK이면 API 호출 없이 가짜 이미지로 대신합니다.
    반환: [(OS 파일 경로, 실제로 보낸 최종 프롬프트)] 목록.
    """
    final_prompt = _build_user_cover_prompt(user_prompt, keyword, no_product, bool(reference_path))
    count = max(1, count)

    if USE_MOCK:
        images = [_mock_image(f"AI 표지 목업 {i + 1}: {user_prompt.strip()[:40]}") for i in range(count)]
    elif reference_path:
        images = _call_gpt_image_edit(reference_path, final_prompt, n=count, size=GEN_SIZE, quality=COVER_IMAGE_QUALITY)
    else:
        images = [_call_gpt_image(final_prompt, COVER_IMAGE_QUALITY) for _ in range(count)]

    results = []
    for i, img in enumerate(images):
        cropped = _center_crop_16_9(img)
        filename = f"{article_id}_coverbg_user_{i}.jpg"
        filepath = os.path.join(_current_output_dir(), filename)
        _save_gen_jpg(cropped, filepath)
        results.append((filepath, final_prompt))
    return results


def _fit_text_to_lines(draw, text: str, max_width: int, max_lines: int, start_size: int, min_size: int = 36):
    """텍스트가 max_lines 줄 이내에 들어가도록 폰트 크기와 줄바꿈 너비를 자동으로 줄여가며 찾습니다."""
    size = start_size
    while size >= min_size:
        font = _load_bold_font(size)
        for wrap_width in range(25, 3, -1):
            wrapped = textwrap.fill(text, width=wrap_width)
            lines = wrapped.split("\n")
            bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center")
            line_width = bbox[2] - bbox[0]
            if line_width <= max_width and len(lines) <= max_lines:
                return font, wrapped
        size -= 6
    font = _load_bold_font(min_size)
    wrapped = textwrap.fill(text, width=14)
    return font, wrapped


def compose_cover_with_text(base_filepath: str, text: str, output_id: str, font_size: int = 104) -> str:
    """생성된 배경 이미지 위에 반투명 흰 박스 + 텍스트를 합성합니다.
    텍스트는 최대 4줄 안에 들어가도록 폰트 크기를 자동으로 조절합니다."""
    base = Image.open(base_filepath).convert("RGBA")
    w, h = base.size

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    box_top = int(h * 0.22)
    box_bottom = int(h * 0.78)
    draw.rectangle([(0, box_top), (w, box_bottom)], fill=(255, 255, 255, 160))

    max_text_width = int(w * 0.82)
    font, wrapped = _fit_text_to_lines(draw, text, max_text_width, max_lines=4, start_size=font_size)

    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    draw.multiline_text(
        ((w - text_w) / 2, (box_top + box_bottom) / 2 - text_h / 2),
        wrapped,
        font=font,
        fill=(20, 20, 20, 255),
        align="center",
    )

    combined = Image.alpha_composite(base, overlay).convert("RGB")
    filename = f"{output_id}_cover_final.jpg"
    filepath = os.path.join(_current_output_dir(), filename)
    _save_gen_jpg(combined, filepath)
    return "/" + filepath.replace(os.sep, "/")


# ── 표지 시안 A: 사진은 선명하게 두고, 하단 그라데이션 위에 글자를 올리는 방식 ──────────────
# 예전 방식(compose_cover_with_text)은 사진 22~78% 높이에 반투명 흰 띠를 깔고 제목 전체를
# 검정 글씨로 올렸는데, 사진이 바래고 제목이 길면 작은 썸네일(정사각형으로 잘림)에서 글자
# 양 끝이 잘리는 문제가 있었습니다. 시안 A는 배지/1줄/2줄/보조 4개 요소를 하단 그라데이션
# 위에 가운데 정렬로 올립니다. COVER_STYLE로 예전 방식과 언제든 되돌릴 수 있게 남겨둡니다.
COVER_STYLE = "A"  # "A" 또는 "legacy"(예전 흰 띠 방식 — compose_cover_with_text/compose_cover_from_photo)

# 채널별 색 — 여기 한 곳만 바꾸면 됩니다. 그라데이션 색은 세 채널이 같고, 강조색·배지 글자색만 다릅니다.
COVER_STYLE_A_COLORS = {
    "longterm_wp": {"gradient": (11, 31, 58), "accent": (94, 234, 212), "badge_text": (8, 40, 40)},
    "shop_wp": {"gradient": (11, 31, 58), "accent": (255, 214, 0), "badge_text": (20, 20, 20)},
    "shop_naver": {"gradient": (11, 31, 58), "accent": (255, 214, 0), "badge_text": (20, 20, 20)},
    "naver_tips": {"gradient": (11, 31, 58), "accent": (255, 214, 0), "badge_text": (20, 20, 20)},  # 숍과 동일
    "wp_tips": {"gradient": (11, 31, 58), "accent": (255, 214, 0), "badge_text": (20, 20, 20)},  # 네이버 일반과 동일
}
# 롱텀 전용: 배지에 마감 임박 표현이 들어있으면 강조색을 주황으로 바꿔서 더 눈에 띄게 합니다.
COVER_STYLE_A_URGENT_PATTERN = re.compile(r"마감|D-|임박")
COVER_STYLE_A_URGENT_COLORS = {"accent": (251, 146, 60), "badge_text": (42, 16, 0)}

# 숍(shop_wp / shop_naver) 전용 색 묶음 8개 — 표지를 만들 때마다 이 중 하나를 랜덤으로 씁니다.
# (그라데이션색 / 강조색 / 배지글자색). 다른 채널은 위 COVER_STYLE_A_COLORS 그대로입니다.
SHOP_COVER_PALETTES = {
    "미드나잇": {"gradient": (11, 31, 58), "accent": (255, 214, 0), "badge_text": (20, 20, 20)},
    "와인": {"gradient": (74, 14, 30), "accent": (255, 190, 120), "badge_text": (60, 20, 10)},
    "포레스트": {"gradient": (14, 52, 38), "accent": (170, 240, 120), "badge_text": (15, 40, 10)},
    "퍼플": {"gradient": (44, 22, 84), "accent": (255, 140, 200), "badge_text": (60, 10, 40)},
    "차콜": {"gradient": (28, 28, 30), "accent": (255, 140, 40), "badge_text": (30, 15, 0)},
    "딥틸": {"gradient": (6, 60, 72), "accent": (120, 240, 230), "badge_text": (5, 40, 45)},
    "카멜": {"gradient": (66, 40, 22), "accent": (255, 220, 150), "badge_text": (60, 35, 10)},
    "로열블루": {"gradient": (18, 40, 120), "accent": (125, 211, 252), "badge_text": (18, 40, 120)},
}
SHOP_COVER_CHANNELS = ("shop_wp", "shop_naver", "wp_tips", "naver_tips")  # 숍 + 꿀팁 4채널이 직전 색 기억을 같이 씁니다
_recent_shop_palettes: list[str] = []  # 직전에 쓴 색 이름(최대 2개). 앱이 켜져 있는 동안만 메모리에 기억합니다.


def pick_shop_palette(requested: str = "") -> tuple[str, dict]:
    """숍 표지 색 묶음을 고릅니다. requested(메모의 '표지 색')가 8색 이름이면 그 색,
    아니면(비었거나 이름이 틀리면) 직전 2번 쓴 색을 뺀 나머지 중 랜덤. 틀린 이름은 터미널에 경고만 남깁니다."""
    requested = (requested or "").strip()
    name = None
    if requested:
        if requested in SHOP_COVER_PALETTES:
            name = requested
        else:
            print(
                f"[표지 색 경고] '{requested}'은(는) 없는 색 이름입니다. 랜덤으로 고릅니다. "
                f"(가능: {', '.join(SHOP_COVER_PALETTES)})"
            )
    if name is None:
        candidates = [n for n in SHOP_COVER_PALETTES if n not in _recent_shop_palettes]
        name = random.choice(candidates or list(SHOP_COVER_PALETTES))
    _recent_shop_palettes.append(name)
    del _recent_shop_palettes[:-2]
    return name, SHOP_COVER_PALETTES[name]


# 네이버(naver_tips) 전용 7색 — 강조 단어·배지 바탕에만 쓰고, 일반 글씨·보조 문구는 흰색 그대로입니다.
NAVER_COVER_CHANNEL = "naver_tips"
NAVER_COVER_PALETTES = {
    "로즈": {"accent": (214, 96, 104), "badge_text": (255, 255, 255)},
    "올리브": {"accent": (150, 178, 78), "badge_text": (255, 255, 255)},
    "머스타드": {"accent": (230, 170, 40), "badge_text": (50, 32, 0)},
    "라벤더": {"accent": (150, 116, 214), "badge_text": (255, 255, 255)},
    "테라코타": {"accent": (226, 116, 66), "badge_text": (255, 255, 255)},
    "블루그레이": {"accent": (84, 140, 206), "badge_text": (255, 255, 255)},
    "틸그린": {"accent": (56, 166, 138), "badge_text": (255, 255, 255)},
}
_recent_naver_palettes: list[str] = []  # 네이버 전용 직전 색 기억(최대 2개). 숍과 따로 기억합니다.


def pick_naver_palette(requested: str = "") -> tuple[str, dict]:
    """네이버 표지 색을 고릅니다. requested(메모의 '표지 색')가 7색 이름이면 그 색,
    아니면 직전 2번 쓴 색을 뺀 랜덤. 틀린 이름은 터미널 경고만 남깁니다."""
    requested = (requested or "").strip()
    name = None
    if requested:
        if requested in NAVER_COVER_PALETTES:
            name = requested
        else:
            print(
                f"[표지 색 경고] '{requested}'은(는) 없는 색 이름입니다. 랜덤으로 고릅니다. "
                f"(가능: {', '.join(NAVER_COVER_PALETTES)})"
            )
    if name is None:
        candidates = [n for n in NAVER_COVER_PALETTES if n not in _recent_naver_palettes]
        name = random.choice(candidates or list(NAVER_COVER_PALETTES))
    _recent_naver_palettes.append(name)
    del _recent_naver_palettes[:-2]
    return name, NAVER_COVER_PALETTES[name]


COVER_STYLE_A_SUB_COLOR = (225, 225, 225)  # #E1E1E1
COVER_STYLE_A_LINE_COLOR = (255, 255, 255)  # 1줄/2줄 기본 글자색(흰색). 강조 단어만 채널 강조색.

# 숍·꿀팁 4채널(SHOP_COVER_CHANNELS) 전용: 아주 옅은 흰색 그라데이션 + 글씨에 붙는 짧은 그림자.
# (팔레트의 gradient 값은 4채널에서 쓰지 않습니다. 혜택주머니 표지는 이 값들의 영향을 받지 않습니다.)
SHOP_COVER_GRADIENT_COLOR = (255, 255, 255)
SHOP_COVER_GRADIENT_ALPHA = 0.22
SHOP_COVER_SUB_COLOR = (255, 255, 255)  # 4채널 보조 글자색(흰색)
# 그림자: (spread=글씨 알파를 넓히는 MaxFilter 크기(0이면 생략), blur=GaussianBlur 반경, alpha=진하기, dy=아래로 이동 px)
COVER_SHADOW_SOFT = (3, 5, 0.55, 3)    # 얇은 테두리 그림자
COVER_SHADOW_TIGHT = (0, 1.5, 0.85, 3)  # 또렷한 그림자
# 네이버(naver_tips) 전용: 연한 그림자 + 사진 아래 절반만 검정으로 짙어지는 그라데이션
NAVER_COVER_SHADOW_SOFT = (3, 6, 0.30, 2)
NAVER_COVER_SHADOW_TIGHT = (0, 1.5, 0.45, 2)
NAVER_COVER_GRADIENT_COLOR = (0, 0, 0)
NAVER_COVER_GRADIENT_TOP = 0.50    # 이 세로 지점에서 불투명도 0으로 시작
NAVER_COVER_GRADIENT_ALPHA = 0.80  # 맨 아래(100%)에서의 최대 불투명도


def _cover_text_shadow(mask: Image.Image, spread: int, blur: float, alpha: float, dy: int) -> Image.Image:
    """글씨 알파(mask, 'L')로 검정 그림자 레이어(RGBA)를 만듭니다. spread가 0이면 MaxFilter는 생략합니다."""
    a = mask
    if spread and spread > 1:
        a = a.filter(ImageFilter.MaxFilter(spread))
    a = a.filter(ImageFilter.GaussianBlur(blur))
    a = a.point(lambda v: round(v * alpha))
    shifted = Image.new("L", a.size, 0)
    shifted.paste(a, (0, dy))
    layer = Image.new("RGBA", a.size, (0, 0, 0, 0))
    layer.putalpha(shifted)
    return layer

_EMPHASIS_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")


def _split_emphasis_runs(text: str) -> list[tuple[str, bool]]:
    """"글자 [강조]글자"처럼 대괄호로 감싼 부분을 강조 구간으로 표시합니다. 대괄호 자체는
    화면에 안 보이게 제거합니다. 대괄호가 없으면 전체가 강조 아닌 구간 하나입니다."""
    text = text or ""
    runs = []
    pos = 0
    for m in _EMPHASIS_BRACKET_RE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], False))
        runs.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    return runs or [("", False)]


def _runs_width(draw: "ImageDraw.ImageDraw", runs: list[tuple[str, bool]], font) -> float:
    return sum(draw.textlength(t, font=font) for t, _ in runs)


def _fit_runs_font(draw, runs, max_width: int, start_size: int, min_size: int, label: str = ""):
    """runs 전체 폭이 max_width 안에 들어올 때까지 ExtraBold 폰트 크기를 4px씩 줄입니다.
    min_size에서도 안 들어오면 그 크기로 그대로 쓰고 콘솔에 경고를 남깁니다(자동 줄바꿈은 안 함)."""
    size = start_size
    font = _load_bold_font(size)
    while size > min_size and _runs_width(draw, runs, font) > max_width:
        size -= 4
        font = _load_bold_font(size)
    final_width = _runs_width(draw, runs, font)
    if final_width > max_width:
        plain = "".join(t for t, _ in runs)
        print(
            f"[표지 시안A 경고] '{plain}' 텍스트가 최소 크기({min_size}px)에서도 "
            f"가운데 폭 {max_width}px을 넘습니다({final_width:.0f}px). {label}"
        )
    return font


def _draw_centered_runs(draw, runs, font, center_x: float, center_y: float, normal_color, accent_color):
    """runs(부분별 강조 여부가 섞인 텍스트 조각들)를 한 줄로 이어서 가운데 정렬로 그립니다.
    anchor="lm"(왼쪽·세로가운데)으로 그려서 조각마다 세로 위치가 자동으로 맞춰집니다."""
    total_w = _runs_width(draw, runs, font)
    x = center_x - total_w / 2
    for text, is_accent in runs:
        if not text:
            continue
        draw.text((x, center_y), text, font=font, fill=(accent_color if is_accent else normal_color), anchor="lm")
        x += draw.textlength(text, font=font)


def _build_style_a_gradient(
    size: tuple[int, int], color: tuple[int, int, int], max_alpha: float = 0.9,
    top_ratio: float = 0.28, full_ratio: float = 0.83,
) -> Image.Image:
    """세로 top_ratio(기본 28%) 지점부터 full_ratio(기본 83%) 지점까지 불투명도 0→max_alpha(기본 0.9)로
    짙어지고, 그 아래는 max_alpha로 유지되는 수직 그라데이션(RGBA)을 만듭니다. 사진 위에 그대로 alpha_composite하면 됩니다."""
    w, h = size
    top_px = h * top_ratio
    full_px = h * full_ratio
    column = Image.new("L", (1, h), 0)
    for y in range(h):
        if y < top_px:
            alpha = 0.0
        elif y < full_px:
            alpha = (y - top_px) / (full_px - top_px) * max_alpha
        else:
            alpha = max_alpha
        column.putpixel((0, y), round(alpha * 255))
    alpha_mask = column.resize((w, h))
    overlay = Image.new("RGBA", size, color + (0,))
    overlay.putalpha(alpha_mask)
    return overlay


# ── 싼세계(wp_tips) 전용 표지: 이 한 가지 스타일로 고정 ──────────────────────────────────────
# 모양: 아래로 갈수록 진해지는 남색 그라데이션 + 노란 둥근 배지(어두운 글씨) + 흰 큰 글씨 1~2줄(강조 단어만 노랑)
#       + 아래 작은 연회색 보조 문구, 가운데 정렬, 글씨 그림자 없음. 색 8색 랜덤·다른 변형 없음.
# 숫자를 바꾸고 싶으면 아래 SSAN_COVER_* 만 고치면 됩니다. 다른 채널 표지는 이 값을 쓰지 않습니다.
SSAN_BAND_CHANNEL = "wp_tips"
SSAN_COVER_NAVY = (11, 31, 58)           # 그라데이션 남색
SSAN_COVER_ACCENT = (255, 214, 0)        # 배지 바탕·강조 단어 노랑
SSAN_COVER_BADGE_TEXT = (20, 20, 20)     # 배지 글자(어두운 색)
SSAN_COVER_LINE_COLOR = (255, 255, 255)  # 큰 글씨 기본 흰색
SSAN_COVER_SUB_COLOR = (225, 225, 225)   # 보조 문구 연회색
SSAN_COVER_GRADIENT_TOP = 0.28           # 남색이 시작하는 세로 위치(0=맨 위, 1=맨 아래)
SSAN_COVER_GRADIENT_FULL = 0.83          # 남색이 최대로 진해지는 세로 위치
SSAN_COVER_BASE_ALPHA = 0.90             # 기준 사진 모양의 최대 불투명도(옅게 하기 전 값)
SSAN_COVER_LIGHTEN = 0.85                # 기준 값의 85% 로 낮춤(약 15% 옅게). 1.0 이면 기준 그대로
SSAN_COVER_TEXT_ZONE_TOP = 0.50          # 글씨가 놓이는 구역이 시작하는 세로 위치(배지 윗부분부터)
SSAN_COVER_TEXT_ZONE_LIGHTEN = 0.90      # 글씨 구역은 기준의 90%만 낮춤(밝은 사진에서 흰 글씨가 잘 읽히게). 전체와 같게 하려면 0.85
SSAN_COVER_MAX_TEXT_W = 820              # 큰 글씨·보조 최대 폭(정사각 썸네일로 잘려도 글씨가 남게)
SSAN_COVER_BADGE_SIZE = 40               # 배지 글자 크기
SSAN_COVER_BADGE_PAD = (22, 15)          # 배지 안쪽 여백(가로, 세로)
SSAN_COVER_BADGE_RADIUS = 14             # 배지 모서리 둥글기
SSAN_COVER_BADGE_CY = 440                # 배지 세로 중심 = 아래 끝에서 이만큼 위
SSAN_COVER_LINE1_CY = 345                # 1줄 중심(아래 끝에서)
SSAN_COVER_LINE2_CY = 220                # 2줄 중심
SSAN_COVER_SUB_CY = 85                   # 보조 문구 중심
SSAN_COVER_LINE_SIZE = (104, 72)         # 큰 글씨 (시작 크기, 최소 크기)
SSAN_COVER_SUB_SIZE = (38, 28)           # 보조 문구 (시작 크기, 최소 크기)


def _build_ssan_cover_gradient(size: tuple[int, int], lighten: float | None = None,
                               zone_lighten: float | None = None) -> Image.Image:
    """싼세계 표지용 남색 그라데이션(RGBA). 모양(시작·끝 높이)은 그대로 두고 진하기만 lighten 배로 줄입니다.
    글씨 구역(SSAN_COVER_TEXT_ZONE_TOP 아래)은 zone_lighten 배를 씁니다(두 값이 같으면 전체가 한 배율)."""
    w, h = size
    lighten = SSAN_COVER_LIGHTEN if lighten is None else lighten
    zone_lighten = SSAN_COVER_TEXT_ZONE_LIGHTEN if zone_lighten is None else zone_lighten
    top_px, full_px, zone_px = h * SSAN_COVER_GRADIENT_TOP, h * SSAN_COVER_GRADIENT_FULL, h * SSAN_COVER_TEXT_ZONE_TOP
    column = Image.new("L", (1, h), 0)
    for y in range(h):
        if y < top_px:
            base = 0.0
        elif y < full_px:
            base = (y - top_px) / (full_px - top_px) * SSAN_COVER_BASE_ALPHA
        else:
            base = SSAN_COVER_BASE_ALPHA
        factor = lighten if y < zone_px else zone_lighten
        column.putpixel((0, y), round(min(1.0, base * factor) * 255))
    overlay = Image.new("RGBA", size, SSAN_COVER_NAVY + (0,))
    overlay.putalpha(column.resize((w, h)))
    return overlay


def _compose_ssan_band_cover(
    base: Image.Image, badge: str, line1: str, line2: str, sub: str, colors: dict | None = None,
    lighten: float | None = None, zone_lighten: float | None = None,
) -> Image.Image:
    """싼세계 표지 한 장(RGB). base는 이미 COVER_OUTPUT_SIZE로 맞춘 RGB 사진. colors 는 받기만 하고 쓰지 않습니다
    (싼세계는 색이 SSAN_COVER_* 로 고정). lighten/zone_lighten 은 시험용으로만 넘깁니다."""
    w, h = base.size
    img = Image.alpha_composite(base.convert("RGBA"), _build_ssan_cover_gradient((w, h), lighten, zone_lighten))
    draw = ImageDraw.Draw(img)
    center_x = w / 2
    accent = SSAN_COVER_ACCENT
    if sub:
        runs = [(sub, False)]
        font = _fit_runs_font(draw, runs, SSAN_COVER_MAX_TEXT_W, *SSAN_COVER_SUB_SIZE, label="(싼세계 보조)")
        _draw_centered_runs(draw, runs, font, center_x, h - SSAN_COVER_SUB_CY, SSAN_COVER_SUB_COLOR, SSAN_COVER_SUB_COLOR)
    if line2:
        runs = _split_emphasis_runs(line2)
        font = _fit_runs_font(draw, runs, SSAN_COVER_MAX_TEXT_W, *SSAN_COVER_LINE_SIZE, label="(싼세계 2줄)")
        _draw_centered_runs(draw, runs, font, center_x, h - SSAN_COVER_LINE2_CY, SSAN_COVER_LINE_COLOR, accent)
    if line1:
        runs = _split_emphasis_runs(line1)
        font = _fit_runs_font(draw, runs, SSAN_COVER_MAX_TEXT_W, *SSAN_COVER_LINE_SIZE, label="(싼세계 1줄)")
        _draw_centered_runs(draw, runs, font, center_x, h - SSAN_COVER_LINE1_CY, SSAN_COVER_LINE_COLOR, accent)
    if badge:
        badge_font = _load_bold_font(SSAN_COVER_BADGE_SIZE)
        bbox = draw.textbbox((0, 0), badge, font=badge_font)
        pad_x, pad_y = SSAN_COVER_BADGE_PAD
        rect_w, rect_h = (bbox[2] - bbox[0]) + pad_x * 2, (bbox[3] - bbox[1]) + pad_y * 2
        cy = h - SSAN_COVER_BADGE_CY
        draw.rounded_rectangle(
            [center_x - rect_w / 2, cy - rect_h / 2, center_x + rect_w / 2, cy + rect_h / 2],
            radius=SSAN_COVER_BADGE_RADIUS, fill=accent,
        )
        draw.text((center_x, cy), badge, font=badge_font, fill=SSAN_COVER_BADGE_TEXT, anchor="mm")
    return img.convert("RGB")


def compose_cover_style_a(
    base_filepath: str,
    badge: str,
    line1: str,
    line2: str,
    sub: str,
    channel: str,
    output_id: str,
    cover_color: str = "",
) -> str:
    """
    표지 시안 A: 배경 사진은 선명하게 두고, 하단에 짙어지는 그라데이션을 덮은 뒤 배지·1줄·
    2줄·보조 4개 요소를 가운데 정렬로 올립니다. 채널별 색은 COVER_STYLE_A_COLORS에서 가져오고,
    롱텀 표지의 배지에 "마감"/"D-"/"임박"이 있으면 강조색을 주황으로 바꿉니다.
    badge/line1/line2/sub 중 빈 문자열인 건 그리지 않습니다(표지 문구 없는 글 등).
    반환: 웹 경로("/static/...").
    """
    base = Image.open(base_filepath).convert("RGB")
    if base.size != COVER_OUTPUT_SIZE:
        base = base.resize(COVER_OUTPUT_SIZE, Image.LANCZOS)
    w, h = base.size

    if channel == SSAN_BAND_CHANNEL:  # 싼세계: 한 가지 스타일로 고정(색 랜덤 선택·직전 색 기억을 건드리지 않음)
        final_img = _compose_ssan_band_cover(base, badge, line1, line2, sub)
        filepath = os.path.join(_current_output_dir(), f"{output_id}_cover_final.jpg")
        _save_gen_jpg(final_img, filepath)
        return "/" + filepath.replace(os.sep, "/")

    is_naver = channel == NAVER_COVER_CHANNEL
    if is_naver:
        _, colors = pick_naver_palette(cover_color)  # 네이버는 7색 중 랜덤(또는 메모 지정색)
    elif channel in SHOP_COVER_CHANNELS:
        _, colors = pick_shop_palette(cover_color)  # 숍은 8색 중 랜덤(또는 메모 지정색)
    else:
        colors = COVER_STYLE_A_COLORS.get(channel, COVER_STYLE_A_COLORS["shop_wp"])
    accent = colors["accent"]
    badge_text_color = colors["badge_text"]
    if channel == "longterm_wp" and badge and COVER_STYLE_A_URGENT_PATTERN.search(badge):
        accent = COVER_STYLE_A_URGENT_COLORS["accent"]
        badge_text_color = COVER_STYLE_A_URGENT_COLORS["badge_text"]

    is_shop = channel in SHOP_COVER_CHANNELS  # 4채널: 옅은 흰 그라데이션 + 글씨 그림자
    if is_naver:
        gradient = _build_style_a_gradient(
            (w, h), NAVER_COVER_GRADIENT_COLOR, NAVER_COVER_GRADIENT_ALPHA,
            top_ratio=NAVER_COVER_GRADIENT_TOP, full_ratio=1.0,
        )
        sub_color = SHOP_COVER_SUB_COLOR
    elif is_shop:
        gradient = _build_style_a_gradient((w, h), SHOP_COVER_GRADIENT_COLOR, SHOP_COVER_GRADIENT_ALPHA)
        sub_color = SHOP_COVER_SUB_COLOR
    else:
        gradient = _build_style_a_gradient((w, h), colors["gradient"])
        sub_color = COVER_STYLE_A_SUB_COLOR
    composited = Image.alpha_composite(base.convert("RGBA"), gradient)

    center_x = w / 2
    max_text_width = 820

    # 글자 크기·위치는 먼저 한 번만 계산해 두고, (그림자용 알파 마스크 / 실제 색 글씨) 두 번 같은 모양으로 그립니다.
    measure = ImageDraw.Draw(composited)
    items = []  # (종류, 그리기에 필요한 값들)
    if sub:
        sub_runs = [(sub, False)]
        sub_font = _fit_runs_font(measure, sub_runs, max_text_width, start_size=38, min_size=28, label="(보조)")
        items.append(("runs", sub_runs, sub_font, h - 85, sub_color))
    if line2:
        line2_runs = _split_emphasis_runs(line2)
        line2_font = _fit_runs_font(measure, line2_runs, max_text_width, start_size=104, min_size=72, label="(2줄)")
        items.append(("runs", line2_runs, line2_font, h - 220, COVER_STYLE_A_LINE_COLOR))
    if line1:
        line1_runs = _split_emphasis_runs(line1)
        line1_font = _fit_runs_font(measure, line1_runs, max_text_width, start_size=104, min_size=72, label="(1줄)")
        items.append(("runs", line1_runs, line1_font, h - 345, COVER_STYLE_A_LINE_COLOR))
    badge_geom = None
    if badge:
        badge_font = _load_bold_font(40)
        bbox = measure.textbbox((0, 0), badge, font=badge_font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = 22, 15
        rect_w, rect_h = text_w + pad_x * 2, text_h + pad_y * 2
        badge_center_y = h - 440
        rect_left = center_x - rect_w / 2
        rect_top = badge_center_y - rect_h / 2
        badge_geom = (badge_font, badge_center_y, [rect_left, rect_top, rect_left + rect_w, rect_top + rect_h])

    def paint(target_draw, accent_c, badge_text_c, ink=None):
        """ink가 있으면(그림자용 마스크) 모든 요소를 그 값 한 가지로 그립니다."""
        for _, runs, font, cy, color in items:
            _draw_centered_runs(
                target_draw, runs, font, center_x, cy,
                color if ink is None else ink, accent_c if ink is None else ink,
            )
        if badge_geom is not None:
            b_font, b_cy, rect = badge_geom
            target_draw.rounded_rectangle(rect, radius=14, fill=accent_c if ink is None else ink)
            target_draw.text(
                (center_x, b_cy), badge, font=b_font,
                fill=badge_text_c if ink is None else ink, anchor="mm",
            )

    if is_shop and (items or badge_geom is not None):
        # 배지·1줄·2줄·보조를 한 마스크에 그린 뒤, 그 알파로 검정 그림자 2겹을 사진 위에 깝니다.
        mask = Image.new("L", (w, h), 0)
        # 배지 글자는 배지 바탕 위에 올라가므로 마스크에서는 칠을 하나로 합쳐 그립니다.
        paint(ImageDraw.Draw(mask), accent, badge_text_color, ink=255)
        shadows = (NAVER_COVER_SHADOW_SOFT, NAVER_COVER_SHADOW_TIGHT) if is_naver else (COVER_SHADOW_SOFT, COVER_SHADOW_TIGHT)
        for spread, blur, alpha, dy in shadows:
            composited = Image.alpha_composite(composited, _cover_text_shadow(mask, spread, blur, alpha, dy))

    paint(ImageDraw.Draw(composited), accent, badge_text_color)

    final_img = composited.convert("RGB")
    filename = f"{output_id}_cover_final.jpg"
    filepath = os.path.join(_current_output_dir(), filename)
    _save_gen_jpg(final_img, filepath)
    return "/" + filepath.replace(os.sep, "/")


def resolve_cover_text(
    cover_badge: str, cover_line1: str, cover_line2: str, cover_sub: str, title: str
) -> tuple[str, str, str, str, bool]:
    """
    메타데이터 메모에 표지 문구(1줄/2줄)가 없으면 제목으로 대신합니다 — 쉼표가 있으면 앞부분을
    1줄, 뒷부분을 2줄로 나누고, 쉼표가 없으면 전부 2줄에 넣습니다. 이때 배지·보조·강조는 없습니다.
    반환: (badge, line1, line2, sub, used_title_fallback).
    """
    if cover_line1 or cover_line2:
        return cover_badge, cover_line1, cover_line2, cover_sub, False
    title = (title or "").strip()
    if "," in title:
        first, _, rest = title.partition(",")
        return "", first.strip(), rest.strip(), "", True
    return "", "", title, "", True


COVER_OUTPUT_SIZE = (1536, 864)  # GEN_SIZE(1536x1024)를 16:9로 자른 것과 같은 크기 — 롱텀 표지와 글자 크기 비율을 맞춤


COVER_LETTERBOX_COLOR = (255, 255, 255)  # 사진 비율이 16:9가 아닐 때 채우는 여백 색 (흰색)


def _fit_with_letterbox(img: Image.Image, size: tuple[int, int], fill=COVER_LETTERBOX_COLOR) -> Image.Image:
    """
    사진을 자르거나 늘리지 않고, 원래 비율 그대로 size 안에 꽉 차게 맞춘 뒤 가운데에 놓습니다.
    세로·정사각형 사진은 좌우에, 16:9보다 가로로 긴 사진은 위아래에 fill 색 여백이 생깁니다.
    """
    target_w, target_h = size
    scale = min(target_w / img.width, target_h / img.height)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", size, fill)
    canvas.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return canvas


def compose_cover_from_photo(photo_path: str, text: str, output_id: str, font_size: int = 104) -> str:
    """
    업체가 준 실제 사진(임의 비율·해상도)을 자르지 않고 원래 비율 그대로 1536x864 안에 맞춘 뒤
    (남는 좌우 또는 상하는 흰색 여백), 기존 compose_cover_with_text로 제목을 합성합니다.
    AI API 호출 없이 PIL만 씁니다. COVER_STYLE="legacy"일 때 쓰는 예전 방식입니다 — 시안 A는
    compose_cover_from_photo_style_a를 쓰세요.
    반환: 웹 경로 ("/static/...")
    """
    img = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")  # 폰 사진 회전 보정
    img = _fit_with_letterbox(img, COVER_OUTPUT_SIZE)
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        img.save(tmp_path)
        return compose_cover_with_text(tmp_path, text, output_id, font_size)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# 여백 채우기 자동 선택 기준 — 사진 가장자리(위·아래·왼쪽·오른쪽 각 3% 띠)가 거의 한 가지 색이면
# "단색 배경 사진(흰 배경 상품 사진 등)"으로 보고 그 색으로 여백을 채웁니다. 아니면 흐린 배경입니다.
# (표준편차만 쓰면 가장자리에 물결·그림자가 살짝 걸친 흰 배경 사진을 놓치므로, "띠 픽셀 대부분이 대표색과 비슷한가"로 봅니다.)
LETTERBOX_EDGE_STRIP = 0.03          # 가장자리 띠 두께(사진 한 변 길이의 비율)
LETTERBOX_EDGE_TOLERANCE = 25        # 띠의 대표색(중앙값)과 R·G·B 모두 이 값 이내면 "같은 색"으로 셉니다
LETTERBOX_EDGE_MIN_SAME_RATIO = 0.88  # 네 변 모두, 띠 픽셀의 이 비율 이상이 "같은 색"이어야 합니다
LETTERBOX_EDGE_MAX_MEAN_DIFF = 20.0  # 네 변의 대표색끼리 채널별 차이가 이 값 이하여야 "비슷한 색"


def classify_photo_edges(img: Image.Image) -> tuple[bool, tuple[int, int, int], dict]:
    """사진 가장자리를 보고 단색 배경 사진인지 판단합니다. 반환: (단색 여부, 네 변 대표색의 평균, 참고 수치)."""
    w, h = img.size
    tw, th = max(1, round(w * LETTERBOX_EDGE_STRIP)), max(1, round(h * LETTERBOX_EDGE_STRIP))
    boxes = [(0, 0, w, th), (0, h - th, w, h), (0, 0, tw, h), (w - tw, 0, w, h)]
    medians, ratios = [], []
    for box in boxes:
        strip = img.crop(box)
        strip.thumbnail((200, 200), Image.NEAREST)  # 판단용이라 줄여서 봅니다
        get = getattr(strip, "get_flattened_data", None) or strip.getdata
        px = list(get())
        med = tuple(sorted(p[c] for p in px)[len(px) // 2] for c in range(3))
        same = sum(1 for p in px if all(abs(p[c] - med[c]) <= LETTERBOX_EDGE_TOLERANCE for c in range(3)))
        medians.append(med)
        ratios.append(same / len(px))
    max_diff = max(max(m[c] for m in medians) - min(m[c] for m in medians) for c in range(3))
    avg = tuple(round(sum(m[c] for m in medians) / 4) for c in range(3))
    solid = min(ratios) >= LETTERBOX_EDGE_MIN_SAME_RATIO and max_diff <= LETTERBOX_EDGE_MAX_MEAN_DIFF
    return solid, avg, {"min_same_ratio": round(min(ratios), 3), "max_color_diff": round(max_diff, 1)}


def _fit_with_blur_background(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """사진 비율이 16:9가 아닐 때 여백을 채웁니다. 가장자리가 한 가지 색이면(단색 배경 사진) 그 평균색으로,
    아니면(장면 사진) 같은 사진을 size에 꽉 차게 키워 흐리게(GaussianBlur 40) + 25% 어둡게 한 배경을 깔고
    그 위에 원본을 비율 그대로 가운데에 올립니다."""
    target_w, target_h = size
    scale = min(target_w / img.width, target_h / img.height)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    if abs(new_w - target_w) <= 1 and abs(new_h - target_h) <= 1:
        return img.resize(size, Image.LANCZOS)  # 사실상 16:9 — 여백 없음
    solid, edge_color, _ = classify_photo_edges(img)
    if solid:
        bg = Image.new("RGB", size, edge_color)
        bg.paste(img.resize((new_w, new_h), Image.LANCZOS), ((target_w - new_w) // 2, (target_h - new_h) // 2))
        return bg
    fill_scale = max(target_w / img.width, target_h / img.height)
    big = img.resize((max(target_w, round(img.width * fill_scale)), max(target_h, round(img.height * fill_scale))), Image.LANCZOS)
    left, top = (big.width - target_w) // 2, (big.height - target_h) // 2
    bg = big.crop((left, top, left + target_w, top + target_h)).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", size, (0, 0, 0)), 0.25)
    bg.paste(img.resize((new_w, new_h), Image.LANCZOS), ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return bg


def _open_photo_on_white(photo_path: str) -> Image.Image:
    """사진을 열어 폰 회전을 보정하고 RGB로 바꿉니다. 투명 배경(RGBA·LA·투명색 있는 P 모드)이면 투명 부분이
    검정으로 변하지 않도록 먼저 흰색(255,255,255) 바탕 위에 합성합니다."""
    img = ImageOps.exif_transpose(Image.open(photo_path))
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(base, rgba).convert("RGB")
    return img.convert("RGB")


def compose_cover_from_photo_style_a(
    photo_path: str, badge: str, line1: str, line2: str, sub: str, channel: str, output_id: str,
    cover_color: str = "",
) -> str:
    """사진 비율을 유지해 1536x864에 맞춘 뒤(남는 자리는 같은 사진의 흐린·어두운 배경),
    시안 A 템플릿(compose_cover_style_a)으로 문구를 올립니다. 숍 채널의 "업체 사진
    위에 제목 합성" 경로(②-1 이미지 풀)가 씁니다."""
    img = _open_photo_on_white(photo_path)  # 투명 배경 사진은 흰 바탕에 합성
    img = _fit_with_blur_background(img, COVER_OUTPUT_SIZE)
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        img.save(tmp_path)
        return compose_cover_style_a(tmp_path, badge, line1, line2, sub, channel, output_id, cover_color)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ── 롱텀 전용 표지 3종(A 숫자 히어로 / B 리포트형 / C 사진+정보카드) + 묶음(cluster_seed)별 배정 ──────
# 숍·꿀팁 표지(compose_cover_style_a)와는 완전히 별개입니다. 모든 글자는 가운데 820px 안에 넣어서
# 정사각형 썸네일(가운데 864x864)로 잘려도 안 잘립니다.
LT_COVER_COLORS = {  # 이름: (메인색, 강조색)
    "딥그린": ((14, 47, 50), (255, 208, 72)),
    "네이비": ((20, 40, 90), (120, 220, 255)),
    "버건디": ((90, 24, 36), (255, 200, 140)),
    "딥퍼플": ((48, 30, 90), (255, 170, 220)),
    "차콜": ((34, 36, 40), (255, 150, 60)),
    "오션블루": ((16, 70, 140), (255, 230, 120)),
}
LT_COVER_TEMPLATES = ("A", "B", "C")
LT_COVER_STYLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "longterm_cover_style.json")
LT_PAPER_COLOR = (247, 244, 236)
LT_INK_COLOR = (24, 26, 32)
LT_COVER_TEXT_WIDTH = 820
LT_BRAND_TOP = "혜택주머니"
LT_BRAND_FOOTER = "혜택주머니 · 정부지원 가이드"
# 채널 이름 글자와 그 옆 로고·아이콘을 이미지 안에 그릴지 여부(no_brand_lt_b_20261028).
# False면 혜택주머니 A·B 표지의 이름, 생활레이더 표지 제목 박스 위 로고·글자, 생활레이더 글자 카드(radar_cards.py)의 로고,
# 카드뉴스(cardnews_gen.py) 하단 라벨을 그리지 않습니다. True로 바꾸면 예전처럼 그립니다(그리는 코드는 되돌리기용으로 남김).
DRAW_CHANNEL_BRAND = False
# 생활레이더 표지(16:9·옛 4:3·제목만 다시 얹기) 전용: 아이콘 + "생활레이더" 그리기. 위 DRAW_CHANNEL_BRAND와 별개로 이 스위치만 봅니다.
DRAW_RADAR_COVER_BRAND = True


def _lt_mix(c1, c2, t: float):
    """c1에 c2를 t 비율만큼 섞은 색."""
    return tuple(round(a * (1 - t) + b * t) for a, b in zip(c1, c2))


def load_lt_cover_styles() -> dict:
    try:
        with open(LT_COVER_STYLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_lt_cover_styles(data: dict) -> None:
    with open(LT_COVER_STYLE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def choose_lt_cover_style(history: list[dict]) -> dict:
    """새 묶음에 줄 (템플릿, 색)을 고릅니다. history는 지금까지 배정된 값들(오래된 순).
    템플릿은 바로 직전 묶음과 다르게, 색은 직전 2개 묶음과 겹치지 않게 랜덤으로 고릅니다."""
    last_template = history[-1].get("template") if history else None
    recent_colors = [h.get("color") for h in history[-2:]]
    templates = [t for t in LT_COVER_TEMPLATES if t != last_template] or list(LT_COVER_TEMPLATES)
    colors = [c for c in LT_COVER_COLORS if c not in recent_colors] or list(LT_COVER_COLORS)
    return {"template": random.choice(templates), "color": random.choice(colors)}


def get_or_assign_lt_cover_style(key: str) -> dict:
    """묶음(cluster_seed, 없으면 keyword)의 표지 디자인·색을 돌려줍니다. 처음이면 랜덤 배정해서
    longterm_cover_style.json에 저장하고, 이후엔 저장된 값을 계속 씁니다."""
    data = load_lt_cover_styles()
    cur = data.get(key)
    if cur and cur.get("template") in LT_COVER_TEMPLATES and cur.get("color") in LT_COVER_COLORS:
        return cur
    entry = choose_lt_cover_style(list(data.values()))
    entry["assigned_at"] = datetime.date.today().isoformat()
    data[key] = entry
    _save_lt_cover_styles(data)
    return entry


def set_lt_cover_style(key: str, template: str, color: str) -> bool:
    """편집 화면에서 직접 고른 값을 저장합니다(이 파일만 고침). 잘못된 값이면 False."""
    if template not in LT_COVER_TEMPLATES or color not in LT_COVER_COLORS:
        return False
    data = load_lt_cover_styles()
    data[key] = {"template": template, "color": color, "assigned_at": datetime.date.today().isoformat()}
    _save_lt_cover_styles(data)
    return True


def _lt_draw_runs(draw, runs, font, center_x, center_y, normal_color, accent_color, highlight=None):
    """_draw_centered_runs와 같지만, highlight 색이 있으면 강조 구간 글자 아래쪽 절반에 형광펜 사각형을 깝니다."""
    total_w = _runs_width(draw, runs, font)
    x = center_x - total_w / 2
    size = getattr(font, "size", 60)
    for text, is_accent in runs:
        if not text:
            continue
        w = draw.textlength(text, font=font)
        if is_accent and highlight is not None:
            draw.rectangle([x - 4, center_y + size * 0.02, x + w + 4, center_y + size * 0.40], fill=highlight)
        draw.text((x, center_y), text, font=font, fill=(accent_color if is_accent else normal_color), anchor="lm")
        x += w


def _lt_fit(draw, runs, start, minimum, label):
    return _fit_runs_font(draw, runs, LT_COVER_TEXT_WIDTH, start_size=start, min_size=minimum, label=label)


LT_HIGHLIGHT_COLOR = (255, 224, 102)  # B 형광펜: 묶음 색과 상관없이 노란색 고정
LT_URGENT_BADGE_PATTERN = re.compile(r"마감|D-|임박")
LT_URGENT_BADGE_BG = (251, 146, 60)
LT_URGENT_BADGE_TEXT = (42, 16, 0)


def _lt_is_urgent(badge: str) -> bool:
    return bool(badge and LT_URGENT_BADGE_PATTERN.search(badge))


# A-사진형: 사진 위 묶음 메인색 반투명 덮개의 진하기(0~1) = (맨 위, 글자 있는 아래쪽). 아래로 갈수록 진해집니다.
LT_A_PHOTO_OVERLAY = (0.35, 0.82)
LT_B_PHOTO_TOP = 18      # 옛 B-사진형(사용 안 함): 맨 위 묶음색 띠 두께(px)
LT_B_PHOTO_H = 320       # 옛 B-사진형(사용 안 함): 띠 아래 사진 영역 높이(px)
LT_B_PHOTO_FADE_FROM = 160  # 옛 B-사진형(사용 안 함): 사진 영역 안에서 이 높이부터 종이색으로 사라지기 시작
LT_B_CARD_OPACITY = 0.75  # B 정보 카드: 흐린 사진 위에 덮는 흰색의 진하기(0~1). 사진이 없으면 1.0 (예전 0.80 → 0.65 → 0.75)
LT_B_CARD = (348, 92, 1188, 772)  # B 정보 카드 위치(왼쪽 위 x, y, 오른쪽 아래 x, y)
LT_B_CARD_RADIUS = 36
LT_B_CARD_HEAD_H = 86  # 카드 머리띠 높이
# B 카드 가운데 메인 글자 크기 (시작, 최소, 하한) — 예전 값의 120% (1줄 62/44/34, 2줄 112/76/52)
LT_B_LINE1_FONT = (74, 53, 41)
LT_B_LINE2_FONT = (134, 91, 62)
LT_C_WHITE_OPACITY = 0.85  # C: 아래쪽 흰 덮개의 최대 진하기(0~1)
LT_C_FADE = (330, 500)  # C: 흰 덮개가 0 → LT_C_WHITE_OPACITY로 올라가는 구간(y 시작, y 끝)


def _lt_load_photo_fill(bg_path: str, w: int, h: int) -> Image.Image:
    """사진을 w x h를 가득 채우도록 키운 뒤 가운데 기준으로 크롭합니다."""
    photo = Image.open(bg_path).convert("RGB")
    scale = max(w / photo.width, h / photo.height)
    photo = photo.resize((max(w, round(photo.width * scale)), max(h, round(photo.height * scale))), Image.LANCZOS)
    left, top = (photo.width - w) // 2, (photo.height - h) // 2
    return photo.crop((left, top, left + w, top + h))


def _lt_has_photo(bg_path) -> bool:
    return bool(bg_path) and os.path.exists(bg_path)


def _lt_cover_a(w, h, main, accent, badge, line1, line2, sub, bg_path="", overlay=None):
    if LT_AB_PHOTO and _lt_has_photo(bg_path):
        return _lt_cover_a_photo(w, h, main, accent, badge, line1, line2, sub, bg_path, overlay)
    img = Image.new("RGB", (w, h), main)
    band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(band).polygon([(1180, 0), (1536, 0), (1536, 734), (1020, 734)], fill=_lt_mix(main, (255, 255, 255), 0.07) + (255,))
    img = Image.alpha_composite(img.convert("RGBA"), band)
    draw = ImageDraw.Draw(img)
    cx = w / 2
    if DRAW_CHANNEL_BRAND:
        draw.text((cx, 80), LT_BRAND_TOP, font=_load_font(30), fill=_lt_mix(main, (255, 255, 255), 0.7), anchor="mm")
    if badge:
        bfont = _lt_fit(draw, [(badge, False)], 40, 28, "(배지)")
        bb = draw.textbbox((0, 0), badge, font=bfont)
        rw, rh = bb[2] - bb[0] + 48, bb[3] - bb[1] + 32
        urgent = _lt_is_urgent(badge)  # 마감·D-·임박이면 배지만 주황
        draw.rounded_rectangle([cx - rw / 2, 175 - rh / 2, cx + rw / 2, 175 + rh / 2], radius=14,
                               fill=LT_URGENT_BADGE_BG if urgent else accent)
        draw.text((cx, 175), badge, font=bfont, fill=LT_URGENT_BADGE_TEXT if urgent else main, anchor="mm")
    if line1:
        r1 = _split_emphasis_runs(line1)
        _lt_draw_runs(draw, r1, _lt_fit(draw, r1, 64, 44, "(1줄)"), cx, 330, (255, 255, 255), accent)
    if line2:
        r2 = _split_emphasis_runs(line2)
        _lt_draw_runs(draw, r2, _lt_fit(draw, r2, 132, 80, "(2줄)"), cx, 500, (255, 255, 255), accent)
    draw.rectangle([0, h - 130, w, h], fill=(255, 255, 255))
    if sub:
        sr = [(sub, False)]
        draw.text((cx, h - 65), sub, font=_lt_fit(draw, sr, 40, 28, "(보조)"), fill=main, anchor="mm")
    return img.convert("RGB")


def _lt_cover_a_photo(w, h, main, accent, badge, line1, line2, sub, bg_path, overlay=None):
    """A-사진형: 사진을 가득 채우고 묶음 메인색 반투명 덮개(위 옅게 → 글자 있는 아래 진하게)를 씌웁니다.
    사선 띠·브랜드·배지·1줄·2줄·하단 흰 띠+보조는 사진 없는 A와 같은 위치·크기이고, 1·2줄에는 약한 그림자를 넣습니다."""
    a_top, a_bottom = overlay if overlay is not None else LT_A_PHOTO_OVERLAY
    img = _lt_load_photo_fill(bg_path, w, h).convert("RGBA")
    ramp_end = max(1, h - 130)  # 하단 흰 띠 위까지 점점 진해지고, 그 아래는 같은 값
    col = Image.new("L", (1, h))
    col.putdata([round(255 * (a_top + (a_bottom - a_top) * min(1.0, y / ramp_end))) for y in range(h)])
    cover = Image.new("RGBA", (w, h), main + (255,))
    cover.putalpha(col.resize((w, h)))
    img = Image.alpha_composite(img, cover)
    band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(band).polygon([(1180, 0), (1536, 0), (1536, 734), (1020, 734)], fill=(255, 255, 255, 18))
    img = Image.alpha_composite(img, band)
    cx = w / 2
    probe = ImageDraw.Draw(img)
    r1 = _split_emphasis_runs(line1) if line1 else None
    r2 = _split_emphasis_runs(line2) if line2 else None
    f1 = _lt_fit(probe, r1, 64, 44, "(1줄)") if r1 else None
    f2 = _lt_fit(probe, r2, 132, 80, "(2줄)") if r2 else None
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sc = (0, 0, 0, 150)
    if r1:
        _lt_draw_runs(sd, r1, f1, cx, 330 + 4, sc, sc)
    if r2:
        _lt_draw_runs(sd, r2, f2, cx, 500 + 4, sc, sc)
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(6)))
    draw = ImageDraw.Draw(img)
    if DRAW_CHANNEL_BRAND:
        draw.text((cx, 80), LT_BRAND_TOP, font=_load_font(30), fill=_lt_mix(main, (255, 255, 255), 0.7), anchor="mm")
    if badge:
        bfont = _lt_fit(draw, [(badge, False)], 40, 28, "(배지)")
        bb = draw.textbbox((0, 0), badge, font=bfont)
        rw, rh = bb[2] - bb[0] + 48, bb[3] - bb[1] + 32
        urgent = _lt_is_urgent(badge)
        draw.rounded_rectangle([cx - rw / 2, 175 - rh / 2, cx + rw / 2, 175 + rh / 2], radius=14,
                               fill=LT_URGENT_BADGE_BG if urgent else accent)
        draw.text((cx, 175), badge, font=bfont, fill=LT_URGENT_BADGE_TEXT if urgent else main, anchor="mm")
    if r1:
        _lt_draw_runs(draw, r1, f1, cx, 330, (255, 255, 255), accent)
    if r2:
        _lt_draw_runs(draw, r2, f2, cx, 500, (255, 255, 255), accent)
    draw.rectangle([0, h - 130, w, h], fill=(255, 255, 255))
    if sub:
        sr = [(sub, False)]
        draw.text((cx, h - 65), sub, font=_lt_fit(draw, sr, 40, 28, "(보조)"), fill=main, anchor="mm")
    return img.convert("RGB")


def _lt_fit_floor(draw, runs, max_width, start, minimum, floor, label):
    """_fit_runs_font와 똑같이 맞추되, 최소 크기에서도 폭을 넘을 때만 2px씩 floor까지 더 줄입니다.
    floor에서도 넘치면 글자를 자르지 않고 콘솔 경고만 냅니다."""
    size = start
    font = _load_bold_font(size)
    while size > minimum and _runs_width(draw, runs, font) > max_width:
        size -= 4
        font = _load_bold_font(size)
    while size > floor and _runs_width(draw, runs, font) > max_width:
        size = max(floor, size - 2)
        font = _load_bold_font(size)
    width = _runs_width(draw, runs, font)
    if width > max_width:
        plain = "".join(t for t, _ in runs)
        print(f"[롱텀 표지 경고] '{plain}' 텍스트가 하한 크기({floor}px)에서도 폭 {max_width}px을 넘습니다({width:.0f}px). {label}")
    return font


def _lt_rr_mask(size, box, radius, corners=None, scale=3) -> Image.Image:
    """둥근 사각형 모양 마스크('L', 안쪽 255). 3배로 그려 줄여서 가장자리가 매끈합니다. corners=(왼위, 오른위, 오른아래, 왼아래)."""
    w, h = size
    big = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(big).rounded_rectangle([v * scale for v in box], radius=radius * scale, fill=255, corners=corners)
    return big.resize((w, h), Image.LANCZOS)


def _lt_check_circle(img: Image.Image, cx: float, cy: float, d: int, color) -> None:
    """지름 d인 color 동그라미 안에 흰 ✓를 그려 img(RGBA)에 얹습니다(글꼴에 ✓가 없어도 되게 선으로 그림)."""
    s = 4
    layer = Image.new("RGBA", (d * s, d * s), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.ellipse([0, 0, d * s - 1, d * s - 1], fill=color + (255,))
    pts = [(0.27, 0.52), (0.44, 0.69), (0.74, 0.35)]
    ld.line([(x * d * s, y * d * s) for x, y in pts], fill=(255, 255, 255, 255), width=round(d * s * 0.11), joint="curve")
    layer = layer.resize((d, d), Image.LANCZOS)
    img.alpha_composite(layer, (round(cx - d / 2), round(cy - d / 2)))


def _lt_cover_b(w, h, main, accent, badge, line1, line2, sub, category, bg_path=""):
    """B 정보 카드형: 흐린 사진(없으면 묶음 메인색) 배경 위에 흰 유리 느낌 카드를 올립니다.
    카드 안: 머리띠(기준 월·배지) / 1줄 / 2줄 / 구분선 / 체크 항목 3칸 / 브랜드. 모든 글자는 카드 안(폭 760) 안에 둡니다."""
    photo_mode = LT_AB_PHOTO and _lt_has_photo(bg_path)
    x0, y0, x1, y1 = LT_B_CARD
    cx = (x0 + x1) / 2
    if photo_mode:
        photo = _lt_load_photo_fill(bg_path, w, h)
        img = Image.alpha_composite(photo.filter(ImageFilter.GaussianBlur(3)).convert("RGBA"),
                                    Image.new("RGBA", (w, h), main + (110,)))
        glass = photo.filter(ImageFilter.GaussianBlur(14))
        opacity = LT_B_CARD_OPACITY
    else:
        img = Image.new("RGBA", (w, h), main + (255,))
        glass = Image.new("RGB", (w, h), (255, 255, 255))
        opacity = 1.0
    card_mask = _lt_rr_mask((w, h), (x0, y0, x1, y1), LT_B_CARD_RADIUS)
    # 그림자: 카드를 아래로 14px 옮겨 흐리게 만들되, 카드 안쪽에는 비치지 않게 카드 모양을 뺍니다.
    sh = _lt_rr_mask((w, h), (x0, y0 + 14, x1, y1 + 14), LT_B_CARD_RADIUS).point(lambda v: round(v * 110 / 255))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    sh = ImageChops.multiply(sh, ImageChops.invert(card_mask))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow.putalpha(sh)
    img = Image.alpha_composite(img, shadow)
    card = Image.blend(glass, Image.new("RGB", (w, h), (255, 255, 255)), opacity)
    img.paste(card, (0, 0), card_mask)
    # 머리띠: 카드 위쪽 86px, 위쪽 모서리만 둥글게(카드 모양 ∩ 위쪽 띠)
    head_rect = Image.new("L", (w, h), 0)
    ImageDraw.Draw(head_rect).rectangle([0, y0, w, y0 + LT_B_CARD_HEAD_H - 1], fill=255)
    img.paste(Image.new("RGB", (w, h), main), (0, 0), ImageChops.multiply(card_mask, head_rect))
    draw = ImageDraw.Draw(img)
    head_cy = y0 + LT_B_CARD_HEAD_H / 2
    today = datetime.date.today()
    head = f"{category} | " if category else ""
    head += f"{today.year}.{today.month:02d} 기준"
    hfont = _load_bold_font(30)
    draw.text((x0 + 44, head_cy), head, font=hfont, fill=(255, 255, 255), anchor="lm")
    if badge:
        right = x1 - 44
        avail = right - (x0 + 44 + draw.textlength(head, font=hfont) + 28) - 40  # 알약 좌우 여백 20px씩 제외한 글자 폭
        text, size = badge, 28
        bfont = _load_bold_font(size)
        while draw.textlength(text, font=bfont) > avail and size > 20:  # 넘치면 글자를 줄이고
            size -= 2
            bfont = _load_bold_font(size)
        if draw.textlength(text, font=bfont) > avail:  # 그래도 넘치면 줄임표
            while len(text) > 1 and draw.textlength(text + "…", font=bfont) > avail:
                text = text[:-1].rstrip()
            text += "…"
        tw = draw.textlength(text, font=bfont)
        urgent = _lt_is_urgent(badge)
        ph = 52
        draw.rounded_rectangle([right - tw - 40, head_cy - ph / 2, right, head_cy + ph / 2], radius=ph // 2,
                               fill=LT_URGENT_BADGE_BG if urgent else accent)
        draw.text((right - 20, head_cy), text, font=bfont, fill=LT_URGENT_BADGE_TEXT if urgent else main, anchor="rm")
    items = [c.strip() for c in re.split(r"[·・]", sub or "") if c.strip()][:3]
    r1 = _split_emphasis_runs(line1) if line1 else None
    r2 = _split_emphasis_runs(line2) if line2 else None
    f1 = _lt_fit_floor(draw, r1, 760, *LT_B_LINE1_FONT, "(B 1줄)") if r1 else None
    f2 = _lt_fit_floor(draw, r2, 760, *LT_B_LINE2_FONT, "(B 2줄)") if r2 else None
    # 아래쪽 브랜드 줄을 그리지 않으면 글자 묶음(1줄~체크 항목)을 머리띠 아래~카드 바닥 사이 세로 가운데로 옮깁니다.
    # (카드 크기·위치는 그대로. 브랜드를 다시 켜면 옮기지 않고 예전 자리 그대로입니다.)
    dy = 0.0
    if not DRAW_CHANNEL_BRAND:
        if r1:
            group_top = 210 - getattr(f1, "size", 74) / 2
        elif r2:
            group_top = 345 - getattr(f2, "size", 134) / 2
        else:
            group_top = 454
        group_bottom = (540 + 18) if items else 456
        dy = (LT_B_CARD_HEAD_H + (y1 - y0)) / 2 - (group_top + group_bottom) / 2
    if r1:
        _lt_draw_runs(draw, r1, f1, cx, y0 + 210 + dy, LT_INK_COLOR, LT_INK_COLOR, highlight=LT_HIGHLIGHT_COLOR)
    if r2:
        _lt_draw_runs(draw, r2, f2, cx, y0 + 345 + dy, LT_INK_COLOR, main)
    draw.rectangle([x0 + 60, y0 + 455 - 1 + dy, x1 - 60, y0 + 455 + 1 + dy], fill=(226, 228, 234))  # 두께 3
    if items:
        cell_w = (x1 - x0 - 140) / 3  # 카드 좌우 70px 안쪽을 3등분
        circle_d, gap, pad = 36, 12, 8
        size = 32
        while True:
            font = _load_font(size)
            widest = max(draw.textlength(t, font=font) for t in items) + circle_d + gap
            if widest <= cell_w - pad or size <= 24:
                break
            size -= 2
        if widest > cell_w - pad:
            print(f"[롱텀 표지 경고] 체크 항목이 최소 크기에서도 칸 폭({cell_w - pad:.0f}px)을 넘습니다({widest:.0f}px).")
        cy = y0 + 540 + dy
        for i, t in enumerate(items):
            tw = draw.textlength(t, font=font)
            start = x0 + 70 + cell_w * i + (cell_w - (circle_d + gap + tw)) / 2
            _lt_check_circle(img, start + circle_d / 2, cy, circle_d, main)
            draw.text((start + circle_d + gap, cy), t, font=font, fill=LT_INK_COLOR, anchor="lm")
    if DRAW_CHANNEL_BRAND:
        draw.text((cx, y0 + 625), LT_BRAND_FOOTER, font=_load_font(24), fill=(120, 122, 128), anchor="mm")
    return img.convert("RGB")


def _lt_cover_c(w, h, main, accent, badge, line1, line2, bg_path):
    photo_mode = _lt_has_photo(bg_path)
    if photo_mode:
        # 사진을 전체에 깔고, 아래쪽에 흰 덮개를 씌웁니다(LT_C_FADE 구간에서 smoothstep으로 0 → LT_C_WHITE_OPACITY).
        f0, f1 = LT_C_FADE

        def _alpha(y):
            t = min(1.0, max(0.0, (y - f0) / max(1, f1 - f0)))
            return round(255 * LT_C_WHITE_OPACITY * t * t * (3 - 2 * t))

        col = Image.new("L", (1, h))
        col.putdata([_alpha(y) for y in range(h)])
        white = Image.new("RGBA", (w, h), (255, 255, 255, 255))
        white.putalpha(col.resize((w, h)))
        img = Image.alpha_composite(_lt_load_photo_fill(bg_path, w, h).convert("RGBA"), white).convert("RGB")
        badge_y, y1, y2, c1 = f1, 595, 712, (90, 94, 102)
    else:
        img = Image.new("RGB", (w, h), (255, 255, 255))
        photo_h = 470
        ImageDraw.Draw(img).rectangle([0, 0, w, photo_h], fill=main)
        badge_y, y1, y2, c1 = photo_h, 585, 705, (110, 114, 122)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, h - 26, w, h], fill=main)
    cx = w / 2
    if badge:
        bfont = _lt_fit(draw, [(badge, False)], 38, 26, "(배지)")
        bb = draw.textbbox((0, 0), badge, font=bfont)
        rw, rh = bb[2] - bb[0] + 56, bb[3] - bb[1] + 30
        urgent = _lt_is_urgent(badge)  # 마감·D-·임박이면 배지만 주황
        draw.rounded_rectangle([cx - rw / 2, badge_y - rh / 2, cx + rw / 2, badge_y + rh / 2], radius=rh // 2,
                               fill=LT_URGENT_BADGE_BG if urgent else main)
        draw.text((cx, badge_y), badge, font=bfont, fill=LT_URGENT_BADGE_TEXT if urgent else (255, 255, 255), anchor="mm")
    if line1:
        r1 = _split_emphasis_runs(line1)
        _lt_draw_runs(draw, r1, _lt_fit_floor(draw, r1, LT_COVER_TEXT_WIDTH, 56, 40, 32, "(C 1줄)"), cx, y1, c1, main)  # [강조]는 묶음 메인색
    if line2:
        r2 = _split_emphasis_runs(line2)
        _lt_draw_runs(draw, r2, _lt_fit_floor(draw, r2, LT_COVER_TEXT_WIDTH, 104, 68, 52, "(C 2줄)"), cx, y2, LT_INK_COLOR, main)
    return img


# ── 새 통일 표지(LT_COVER_UNIFIED): 사진 가득 + 아래쪽 카테고리색 그라데이션 + 가운데 글자 ───────────────
LT_UNIFIED_DEFAULT_CATEGORY = "정부지원금·복지"
LT_UNIFIED_COLORS = {  # 카테고리 → 띠 메인색 (글자 강조색은 아래 LT_UNIFIED_ACCENTS에서 묶음마다 돌아가며 배정)
    "정부지원금·복지": (34, 58, 48),     # 포레스트
    "서민금융·정책자금": (28, 36, 56),   # 미드나잇
    "보험·연금": (48, 68, 86),           # 슬레이트
    "세금·절세": (86, 38, 46),           # 마르살라
}
LT_UNIFIED_ACCENTS = {  # 글자 강조색 7가지 (이 순서로 묶음마다 돌아감)
    "머스터드 골드": (232, 190, 92),
    "스카이블루": (138, 192, 236),
    "세이지 그린": (140, 208, 176),
    "코랄": (236, 150, 128),
    "샴페인 베이지": (214, 190, 138),
    "웜 그레이": (204, 204, 200),
    "그레이지": (198, 186, 170),
}
LT_COVER_ACCENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "longterm_cover_accent.json")
LT_UNIFIED_SHADE = (0.55, 0.30)  # 중립 그늘: 검정 그라데이션이 0이 되는 높이 비율, 맨 아래(H)에서의 alpha(직선 증가)
LT_UNIFIED_OLD_NAMES = {"세금·연말정산": "세금·절세", "4대보험·연금": "보험·연금"}  # 옛 이름 → 새 이름
LT_UNIFIED_GRADIENT = (0.60, 0.84, 0.52)  # 그라데이션 시작 높이 비율, 가장 진해지는 높이 비율, 최대 진하기
LT_UNIFIED_SHADOW = (220, 7)  # 글자 그림자: 검정 alpha, 가우시안 블러 반경(px)
LT_UNIFIED_URGENT_BG = (196, 112, 74)  # 마감·마감 임박 배지 바탕(테라코타)
LT_UNIFIED_URGENT_BADGES = ("마감", "마감 임박")


def lt_unified_colors(category: str):
    """카테고리 이름 → (정리된 카테고리, 띠 메인색). 비었거나 모르는 이름이면 포레스트로 대신하고 콘솔에 경고 한 줄."""
    name = (category or "").strip()
    name = LT_UNIFIED_OLD_NAMES.get(name, name)
    if name not in LT_UNIFIED_COLORS:
        print(f"[롱텀 표지 경고] 카테고리 '{category}'를 알 수 없어 포레스트({LT_UNIFIED_DEFAULT_CATEGORY}) 색으로 만듭니다.")
        name = LT_UNIFIED_DEFAULT_CATEGORY
    return name, LT_UNIFIED_COLORS[name]


def load_lt_cover_accents(path: str = "") -> dict:
    """묶음 → {"accent": 색 이름, "assigned_at": 날짜}. 파일이 없거나 깨졌으면 빈 값(읽기만)."""
    try:
        with open(path or LT_COVER_ACCENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_lt_cover_accents(data: dict, path: str = "") -> None:
    with open(path or LT_COVER_ACCENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_or_assign_lt_accent(key: str, path: str = "") -> str:
    """묶음의 글자 강조색 이름. 이미 배정됐으면 그대로, 처음이면 '가장 최근에 배정된 묶음'의 다음 색(⑦ 다음은 ①)을 기록하고 돌려줍니다.
    묶음 이름이 비었으면 기록 없이 첫 색(머스터드 골드)."""
    names = list(LT_UNIFIED_ACCENTS)
    if not key:
        return names[0]
    data = load_lt_cover_accents(path)
    cur = data.get(key)
    if isinstance(cur, dict) and cur.get("accent") in LT_UNIFIED_ACCENTS:
        return cur["accent"]
    last = None  # 가장 최근 배정(날짜가 같으면 파일에서 더 뒤에 있는 것)
    for v in data.values():
        if isinstance(v, dict) and v.get("accent") in LT_UNIFIED_ACCENTS:
            if last is None or str(v.get("assigned_at", "")) >= str(last.get("assigned_at", "")):
                last = v
    name = names[(names.index(last["accent"]) + 1) % len(names)] if last else names[0]
    data[key] = {"accent": name, "assigned_at": datetime.date.today().isoformat()}
    _save_lt_cover_accents(data, path)
    return name


def set_lt_accent(key: str, accent: str, path: str = "") -> bool:
    """편집 화면에서 고른 강조색을 저장합니다(longterm_cover_accent.json의 그 묶음만). 잘못된 값이면 False."""
    if not key or accent not in LT_UNIFIED_ACCENTS:
        return False
    data = load_lt_cover_accents(path)
    old = data.get(key) if isinstance(data.get(key), dict) else {}
    data[key] = {"accent": accent, "assigned_at": old.get("assigned_at") or datetime.date.today().isoformat()}
    _save_lt_cover_accents(data, path)
    return True


def _lt_smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _lt_unified_fit_medium(draw, text, max_width, start, minimum):
    """보조 글자(Medium)가 max_width를 넘으면 2px씩 minimum까지 줄입니다."""
    size = start
    font = _load_font(size)
    while size > minimum and draw.textlength(text, font=font) > max_width:
        size -= 2
        font = _load_font(size)
    return font


def _lt_cover_unified(w, h, main, accent, badge, line1, line2, sub, bg_path=""):
    """통일 표지: 사진(없으면 메인색 단색) -> 아래쪽 메인색 그라데이션(0.60H에서 0, 0.84H에서 최대) -> 가운데 정렬 글자(그림자 포함)."""
    if _lt_has_photo(bg_path):
        img = _lt_load_photo_fill(bg_path, w, h).convert("RGBA")
    else:
        img = Image.new("RGBA", (w, h), main + (255,))
    y0, y1, a_max = h * LT_UNIFIED_GRADIENT[0], h * LT_UNIFIED_GRADIENT[1], LT_UNIFIED_GRADIENT[2]
    col = Image.new("L", (1, h))
    col.putdata([round(255 * a_max * _lt_smoothstep((y - y0) / (y1 - y0))) for y in range(h)])
    grad = Image.new("RGBA", (w, h), main + (255,))
    grad.putalpha(col.resize((w, h)))
    img = Image.alpha_composite(img, grad)
    s0, s_max = LT_UNIFIED_SHADE  # 중립 그늘: 검정, 0.55H에서 0 -> 맨 아래에서 s_max (직선)
    ys = h * s0
    scol = Image.new("L", (1, h))
    scol.putdata([round(255 * s_max * max(0.0, min(1.0, (y - ys) / (h - ys)))) for y in range(h)])
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    shade.putalpha(scol.resize((w, h)))
    img = Image.alpha_composite(img, shade)

    cx = w / 2
    probe = ImageDraw.Draw(img)
    white = (255, 255, 255)
    r1 = _split_emphasis_runs(line1) if line1 else None
    r2 = [(t, True) for t, _ in _split_emphasis_runs(line2)] if line2 else None  # 2줄은 줄 전체 강조색(괄호만 뺌)
    f1 = _lt_fit_floor(probe, r1, LT_COVER_TEXT_WIDTH, 62, 40, 30, "(통일 1줄)") if r1 else None
    f2 = _lt_fit_floor(probe, r2, LT_COVER_TEXT_WIDTH, 118, 70, 50, "(통일 2줄)") if r2 else None
    f3 = _lt_unified_fit_medium(probe, sub, LT_COVER_TEXT_WIDTH, 34, 22) if sub else None
    y1c, y2c, y3c = h - 268, h - 170, h - 62

    sh_alpha, sh_blur = LT_UNIFIED_SHADOW
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sc = (0, 0, 0, sh_alpha)
    if r1:
        _lt_draw_runs(sd, r1, f1, cx, y1c, sc, sc)
    if r2:
        _lt_draw_runs(sd, r2, f2, cx, y2c, sc, sc)
    if sub:
        sd.text((cx, y3c), sub, font=f3, fill=sc, anchor="mm")
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(sh_blur)))

    draw = ImageDraw.Draw(img)
    if badge:
        bfont = _lt_fit_floor(draw, [(badge, False)], LT_COVER_TEXT_WIDTH - 44, 32, 24, 20, "(통일 배지)")
        tw = draw.textlength(badge, font=bfont)
        rw, rh, by = tw + 44, 52, h - 345
        urgent = badge.strip() in LT_UNIFIED_URGENT_BADGES
        draw.rounded_rectangle([cx - rw / 2, by - rh / 2, cx + rw / 2, by + rh / 2], radius=10,
                               fill=LT_UNIFIED_URGENT_BG if urgent else accent)
        draw.text((cx, by), badge, font=bfont, fill=white if urgent else main, anchor="mm")
    if r1:
        _lt_draw_runs(draw, r1, f1, cx, y1c, white, accent)
    if r2:
        _lt_draw_runs(draw, r2, f2, cx, y2c, accent, accent)
    if sub:
        subl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(subl).text((cx, y3c), sub, font=f3, fill=white + (225,), anchor="mm")
        img = Image.alpha_composite(img, subl)
    return img.convert("RGB")


def compose_longterm_cover(
    template: str, color: str, badge: str, line1: str, line2: str, sub: str, output_id: str,
    category: str = "", bg_path: str = "", overlay: tuple | None = None, cluster_key: str = "",
    accent_name: str = "",
) -> str:
    """롱텀 표지 3종 중 하나를 1536x864로 그려서 저장합니다. 반환: 웹 경로("/static/...").
    C는 bg_path(위쪽 470px에 쓸 AI 표지 사진)를 씁니다. A·B는 LT_AB_PHOTO=True이고 사진이 있으면 사진형으로,
    사진이 없으면(또는 LT_AB_PHOTO=False) 예전 사진 없는 A·B로 그립니다. overlay는 A-사진형 덮개 진하기(미리보기용, 기본 LT_A_PHOTO_OVERLAY)."""
    w, h = COVER_OUTPUT_SIZE
    if LT_COVER_UNIFIED:  # 새 표지: template·color는 무시하고 카테고리로 색을 정합니다(옛 A·B·C 코드는 아래에 그대로 남아 있음)
        _, main = lt_unified_colors(category)
        accent = LT_UNIFIED_ACCENTS[accent_name or get_or_assign_lt_accent(cluster_key)]
        if not _lt_has_photo(bg_path):
            print("[롱텀 표지 안내] 표지 사진이 없어 메인색 단색 바탕으로 만듭니다.")
        img = _lt_cover_unified(w, h, main, accent, badge, line1, line2, sub, bg_path=bg_path)
        filepath = os.path.join(_current_output_dir(), f"{output_id}_cover_final.jpg")
        _save_gen_jpg(img, filepath)
        return "/" + filepath.replace(os.sep, "/")
    main, accent = LT_COVER_COLORS[color]
    if LT_AB_PHOTO and template in ("A", "B") and not _lt_has_photo(bg_path):
        print(f"[롱텀 표지 안내] 표지 사진이 없어 사진 없는 {template}로 만듭니다.")
    if template == "A":
        img = _lt_cover_a(w, h, main, accent, badge, line1, line2, sub, bg_path=bg_path, overlay=overlay)
    elif template == "B":
        img = _lt_cover_b(w, h, main, accent, badge, line1, line2, sub, category, bg_path=bg_path)
    else:
        img = _lt_cover_c(w, h, main, accent, badge, line1, line2, bg_path)
    filepath = os.path.join(_current_output_dir(), f"{output_id}_cover_final.jpg")
    _save_gen_jpg(img, filepath)
    return "/" + filepath.replace(os.sep, "/")


def normalize_heading(text: str) -> str:
    """소제목 비교용: HTML 태그·**·이모지·문장부호·공백을 모두 없애고(한글·영문·숫자만 남김) 소문자로 바꿉니다."""
    t = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t).casefold()


def select_memo_body_pairs(pairs: list[tuple[str, str]], n: int) -> list[tuple[str, str]]:
    """롱텀: 메모의 (소제목, 장면) 목록에서 쓸 것만 순서를 유지한 채 고릅니다.
    3~4개면 전부, 5개 이상이면 n개를 글 앞·중간·뒤에 고르게(각 구간의 가운데) 고르고, 0~2개면 있는 것만 돌려줍니다(모자란 자리는 호출하는 쪽이 채움).
    (photo_v2_20261020: 본문 사진 +1장에 맞춰 기준을 2~3 → 3~4로 올렸습니다.)"""
    m = len(pairs)
    if 3 <= m <= 4 or m <= 2:
        return list(pairs)
    n = max(1, min(n, m))
    return [pairs[int((i + 0.5) * m / n)] for i in range(n)]


def plan_longterm_body_slots(
    pairs: list[tuple[str, str]], per_platform: int, headings: list[str], keyword: str
) -> list[dict]:
    """롱텀 한 플랫폼의 본문 이미지 자리 계획. 각 자리: {"alt", "scene", "heading"}.
    메모 장면 자리는 scene=장면 문장·alt=장면 문장·heading=그 장면이 적힌 소제목. 모자란 자리는 scene=None(예전 방식: 정해진 장면
    목록)·alt=소제목·heading=""(고르게 배치). 이미 메모 장면이 쓴 소제목은 채움용으로 다시 고르지 않습니다."""
    chosen = select_memo_body_pairs(pairs, per_platform)
    slots = [{"alt": scene, "scene": scene, "heading": h} for h, scene in chosen]
    used = {normalize_heading(h) for h, _ in chosen}
    pool = [h for h in headings if normalize_heading(h) not in used]
    for j in range(max(0, per_platform - len(slots))):
        slots.append({"alt": pool[j] if j < len(pool) else keyword, "scene": None, "heading": ""})
    return slots[:per_platform]


def generate_assets(
    keyword: str,
    headings: list[str],
    platform_titles: dict,
    article_id: str,
    body_image_count: int = 6,
    body_alt_suggestions: list[str] | None = None,
    cover_alt_suggestion: str | None = None,
    situational_cover: bool = False,
    cover_badge: str = "",
    cover_line1: str = "",
    cover_line2: str = "",
    cover_sub: str = "",
    channel: str = "longterm_wp",
    scene_from_alt: bool = False,
    longterm_scene_from_memo: bool = False,
    body_alt_pairs: list[tuple[str, str]] | None = None,
    lt_style: dict | None = None,
    lt_category: str = "",
    lt_cluster_key: str = "",
    cover_color: str = "",
    cover_scene: str | None = None,
    angle_seed: str = "",
) -> dict:
    """본문 이미지 body_image_count장 + 플랫폼별 표지(각 플랫폼 고유 제목 + 랜덤 배정 배경)를 한 번에 생성합니다.
    platform_titles: {"wordpress": "제목1", "naver": "제목2", "tistory": "제목3"} 형태 (내용 있는 것만)
    body_alt_suggestions: 메타데이터 메모에서 뽑은 "소제목별 이미지 설명" 목록이 있으면, 이걸
      이미지 생성 프롬프트와 최종 alt 텍스트로 그대로 씁니다(제목-소제목 조합보다 훨씬 구체적입니다).
      필요한 장수보다 많으면 랜덤으로 골라 쓰고, 적으면 부족한 만큼 소제목 기반으로 채웁니다.
    situational_cover: True(숍 채널용)면 표지에 특정 제품 형태를 그리지 않고 "이 제품이 필요한
      상황"만 보여주는 무드컷으로 만듭니다.
    cover_badge/cover_line1/cover_line2/cover_sub: 메타데이터 메모의 "표지 배지/1줄/2줄/보조"
      (COVER_STYLE="A"일 때만 씀). 1줄·2줄이 둘 다 없으면 플랫폼별 제목으로 대신합니다
      (resolve_cover_text 참고) — 그 경우 cover_title_fallback[플랫폼]이 True가 됩니다.
    channel: 표지 색 팔레트를 고르는 채널 키("longterm_wp"/"shop_wp"/"shop_naver").
    scene_from_alt: True(꿀팁 채널 전용)면 본문 이미지는 메모 alt(모자라면 "키워드 + 소제목")를, 표지는 메모의 표지 alt를 그대로
      장면으로 쓰고 금융 표현을 뺀 중립 프롬프트를 씁니다. False(기본)면 롱텀 방식 그대로입니다.
    longterm_scene_from_memo: True(롱텀 전용)면 메모의 본문 장면(body_alt_pairs, 적힌 순서 그대로)과 표지 alt(cover_alt_suggestion)를
      MANDATORY SCENE으로 그대로 쓰고, 금융 블로그 톤·현금 금지는 유지합니다. 메모 장면이 모자라면 모자란 자리만 예전 방식(소제목 +
      정해진 장면 목록)으로 채웁니다. body_image_count는 플랫폼 전체 합계이고 플랫폼당 장수 = 합계 ÷ 활성 플랫폼 수입니다.
    cover_scene: 메모의 "표지 장면:" — 표지 AI 사진의 장면 전용입니다(alt에는 절대 안 씀). 표지 장면 우선순위: cover_scene → cover_alt_suggestion
      → 예전 방식. 숍은 cover_scene이 있을 때만 무작위 장면 대신 씁니다(표지 alt는 숍에서 장면으로 안 씀, 예전 그대로).
    angle_seed: 본문 사진 구도(BODY_SHOT_ANGLES) 배정에 쓰는 시드(글 id). 비어 있으면 구도 문장을 붙이지 않습니다(예전 그대로).
      플랫폼마다 같은 장수씩 나뉘므로 구도도 플랫폼당 장수 기준으로 배정해 되풀이합니다.
    반환: {"body_images", "body_alts", "body_alt_headings", "covers", "cover_alts", "cover_backgrounds", "cover_title_fallback"}
      body_alt_headings: body_images와 같은 순서로, 그 사진이 붙을 소제목(메모 장면이 적힌 "## 소제목" 글자). 소제목 기반으로 채운 자리·
      롱텀 새 방식이 아닐 때는 "" (= 고르게 배치).
      cover_backgrounds는 나중에 문구만 다시 합성할 때("표지 다시 만들기") 쓸 원본 배경 파일 경로입니다.
    """
    body_image_count = max(0, body_image_count)
    _n_plat_angles = max(1, len([t for t in platform_titles.values() if t]))
    body_angles = (
        assign_body_shot_angles(body_image_count // _n_plat_angles, angle_seed) * _n_plat_angles if angle_seed else None
    )

    if longterm_scene_from_memo:
        # 롱텀(새 방식): 메모 장면을 섞지 않고 적힌 순서대로 쓰고, 사진이 붙을 소제목도 같이 기록합니다.
        n_plat = max(1, len([t for t in platform_titles.values() if t]))
        slots = plan_longterm_body_slots(body_alt_pairs or [], body_image_count // n_plat, headings, keyword) * n_plat
        chosen_alts = [s["alt"] for s in slots]
        body_headings = [s["heading"] for s in slots]
        body_prompts = [f"{keyword} - {alt}" for alt in chosen_alts]
        body_images = generate_body_images(
            body_prompts, article_id=article_id, scene_keyword=keyword, memo_scenes=[s["scene"] for s in slots],
            angles=body_angles,
        )
    else:
        body_headings = None  # 꿀팁(scene_from_alt)에서만 채움: 사진별로 붙일 소제목(메모 장면이 적힌 "## 소제목"), 소제목으로 채운 자리는 ""
        if body_alt_suggestions:
            pool = list(body_alt_suggestions)
            # 메모 "## 소제목" → 장면 줄과 body_alt_suggestions는 같은 줄·같은 순서라, 장면을 섞을 때 소제목도 같이 섞습니다.
            _pair_heads = [h for h, _s in (body_alt_pairs or [])] if len(body_alt_pairs or []) == len(pool) else [""] * len(pool)
            _order = list(range(len(pool)))
            random.shuffle(_order)  # 어느 소제목의 설명이 어느 이미지 자리에 갈지 랜덤으로 매칭
            pool = [pool[k] for k in _order]
            pool_heads = [_pair_heads[k] for k in _order]
            if len(pool) >= body_image_count:
                chosen_alts = pool[:body_image_count]
                body_headings = pool_heads[:body_image_count]
            else:
                # 제안이 모자라면 나머지는 소제목 기반으로 채웁니다.
                fallback = list(headings)
                while len(fallback) < body_image_count - len(pool):
                    fallback.append(keyword)
                chosen_alts = pool + fallback[: body_image_count - len(pool)]
                body_headings = pool_heads + [""] * (body_image_count - len(pool))
            body_prompts = [f"{keyword} - {alt}" for alt in chosen_alts]
        else:
            prompts = list(headings)
            while len(prompts) < body_image_count:
                prompts.append(keyword)
            prompts = prompts[:body_image_count]
            body_prompts = [f"{keyword} - {h}" for h in prompts]
            chosen_alts = prompts  # alt 텍스트 제안이 없으면 소제목 자체를 alt로 사용

        if scene_from_alt:
            # 메모 alt가 있는 자리는 그 문장 그대로, 소제목으로 채운 자리는 "키워드 + 소제목"으로 장면을 만듭니다.
            n_alt = len(body_alt_suggestions or [])
            n_alt_used = min(n_alt, body_image_count)
            body_scenes = [
                alt if j < n_alt_used else f"A realistic everyday scene about '{keyword}' that illustrates the section topic: {alt}."
                for j, alt in enumerate(chosen_alts)
            ]
            body_images = generate_body_images(
                body_prompts, article_id=article_id, scenes=body_scenes, scene_keyword=keyword, angles=body_angles
            )
        else:
            body_images = generate_body_images(body_prompts, article_id=article_id, angles=body_angles)
        if not scene_from_alt or body_headings is None:
            body_headings = [""] * len(chosen_alts)

    active_platforms = [p for p, title in platform_titles.items() if title]
    # 롱텀 새 표지(lt_style = {"template","color"}): A·B는 사진이 필요 없어서 표지 AI 사진을 아예 만들지 않습니다(비용 절감).
    use_lt_cover = bool(lt_style) and channel == "longterm_wp" and COVER_STYLE != "legacy"
    if use_lt_cover and lt_style["template"] != "C" and not LT_AB_PHOTO and not LT_COVER_UNIFIED:
        backgrounds = []
    else:
        # 표지 사진 장면(alt와 별개): "표지 장면" → (꿀팁·롱텀은) 표지 alt → 예전 방식. alt 텍스트는 아래 cover_alts에서 cover_alt_suggestion만 씁니다.
        scene_text = (cover_scene or "").strip() or None
        if longterm_scene_from_memo:
            memo_cover_scene = scene_text or cover_alt_suggestion
        elif situational_cover:
            memo_cover_scene = scene_text
        else:
            memo_cover_scene = None
        lt_tpl = lt_style["template"] if use_lt_cover else ""
        cover_layout = lt_tpl if lt_tpl in ("B", "C") else "A"  # A-사진형은 숍·꿀팁과 같은 "A" 구도
        if channel == SSAN_BAND_CHANNEL and scene_from_alt and not use_lt_cover and COVER_STYLE != "legacy":
            cover_layout = "A_BAND"  # 싼세계: 주인공은 위쪽 절반, 아래 45%는 색 띠가 덮음
        try:
            backgrounds = generate_cover_backgrounds(
                keyword, count=len(active_platforms), article_id=article_id, situational_only=situational_cover,
                scene_from_alt=scene_from_alt, scene_alt=scene_text or cover_alt_suggestion,
                memo_cover_scene=memo_cover_scene,
                cover_layout=cover_layout, lt_cover=use_lt_cover,
            )
        except Exception as e:
            if lt_tpl not in ("A", "B") and not (use_lt_cover and LT_COVER_UNIFIED):
                raise
            # A·B는 사진이 없어도 만들 수 있으니, 사진 생성이 실패하면 사진 없는 A·B로 대신합니다.
            print(f"[롱텀 표지 안내] 표지 사진 생성에 실패해 사진 없는 {lt_tpl}로 만듭니다: {e}")
            backgrounds = []

    shuffled_bg = backgrounds[:] or [""]
    random.shuffle(shuffled_bg)

    covers = {}
    cover_alts = {}
    cover_backgrounds = {}
    cover_title_fallback = {}
    for i, platform in enumerate(active_platforms):
        title = platform_titles[platform]
        bg_path = shuffled_bg[i % len(shuffled_bg)]
        if COVER_STYLE == "legacy":
            covers[platform] = compose_cover_with_text(bg_path, title, output_id=f"{article_id}_{platform}")
            used_fallback = False
        else:
            badge, line1, line2, sub, used_fallback = resolve_cover_text(
                cover_badge, cover_line1, cover_line2, cover_sub, title
            )
            if use_lt_cover:
                covers[platform] = compose_longterm_cover(
                    lt_style["template"], lt_style["color"], badge, line1, line2, sub,
                    output_id=f"{article_id}_{platform}", category=lt_category, bg_path=bg_path,
                    cluster_key=lt_cluster_key,
                )
            else:
                covers[platform] = compose_cover_style_a(
                    bg_path, badge, line1, line2, sub, channel, output_id=f"{article_id}_{platform}",
                    cover_color=cover_color,  # 메모의 "표지 색:" (숍·꿀팁 채널에서만 쓰임)
                )
        cover_alts[platform] = cover_alt_suggestion or f"{keyword} 표지 이미지"
        cover_backgrounds[platform] = bg_path
        cover_title_fallback[platform] = used_fallback

    return {
        "body_images": body_images,
        "body_alts": chosen_alts,
        "body_alt_headings": body_headings,
        "covers": covers,
        "cover_alts": cover_alts,
        "cover_backgrounds": cover_backgrounds,
        "cover_title_fallback": cover_title_fallback,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 생활레이더 AI 사진 (16:9, radar169_20261027 — 옛 4:3은 radar_43_title_20261013) — 기존 함수는 건드리지 않고 아래에 새로 추가한 함수들입니다.
#   · IMAGE_MODEL(gpt-image-2.5-flare) · 표지 COVER_IMAGE_QUALITY / 본문 BODY_IMAGE_QUALITY(둘 다 low) · 1536x1024(가로형) → 가운데 16:9로 자른 1536x864(확대 없음, 다른 채널의 _center_crop_16_9와 같은 방식).
#   · 중요한 것은 가운데 정사각형(x 336~1200px) 안에 둡니다 — 네이버가 썸네일을 정사각으로 잘라도 이상하지 않게.
#   · 옛 4:3(1440x1080) 사진은 그대로 남아 있습니다 — [표지 제목만 다시 얹기]는 사진 비율을 보고 옛 4:3 배치/새 16:9 배치를 고릅니다.
#   · 표지는 글자 없는 사진을 따로 저장한 뒤(…_radar_cover_photo_…), 남색 제목 박스를 얹은 표지(…_radar_cover_…)를 만듭니다(Pillow, 0원).
#   · 금지선: 기존 후킹 금지선(_COVER_IMPACT_BANS) + 얼굴 규칙 + 현금 금지 + 글자·숫자·로고·바코드 0개.
#   · (옛 1:1 함수 _call_gpt_image_square / _mock_square_image는 더 이상 쓰지 않습니다. 이미 만든 1:1 사진은 그대로 둡니다.)
# ══════════════════════════════════════════════════════════════════════════════
RADAR_COVER_GEN_SIZE = "1024x1024"  # (옛 1:1 — 사용 안 함)
RADAR_COVER_OUT_SIZE = 1080  # (옛 1:1 — 사용 안 함)
RADAR_GEN_SIZE_169 = "1536x1024"  # 이미지 모델이 지원하는 가로형(1024x1024 / 1536x1024 / 1024x1536 중)
RADAR_OUT_SIZE_169 = (1536, 864)  # 16:9 최종 크기(1536x1024에서 위아래만 잘라 냄 — 확대 없음)
RADAR_OUT_SIZE_43 = (1440, 1080)  # (옛 4:3 사진/표지 배치용 — 새 사진은 만들지 않음)
RADAR_COVER_JPG_QUALITY = 92
RADAR_COVER_COST_NOTE = "약 20원"  # 장당 화면 안내용 어림값(flare low 표지: 프롬프트가 길어 약 $0.0155)
RADAR_BODY_COST_NOTE = "약 15원"  # 1장 기준 화면 안내용 어림값(flare low 본문: 약 $0.0103)
RADAR_SQUARE_THUMB_BOX = (336, 0, 1200, 864)  # 네이버 정사각 썸네일로 잘릴 때 보이는 가운데 864x864 영역(16:9 사진 기준)

# 표지 제목 박스 (대시보드 목업 B안). 두 가지 배치: 16:9(1536x864, 새 기본) / 4:3(1440x1080, 옛 글의 [표지 제목만 다시 얹기]용)
RADAR_TITLE_BOX_COLOR = (14, 33, 66)  # #0E2142
RADAR_TITLE_BOX_OPACITY = 0.88
RADAR_TITLE_BAR_COLOR = (255, 138, 31)  # #FF8A1F
RADAR_TITLE_LINE_RATIO = 1.25  # 줄 높이 = 글자 크기 × 이 값
RADAR_TITLE_FONT_STEP = 2
RADAR_TITLE_MAX_LINES = 2
RADAR_TITLE_LONG = 18  # 이 글자 수를 넘으면 경고만(그대로 그림)
RADAR_LOGO_TEXT = "생활레이더"
RADAR_LOGO_ORANGE = (255, 138, 31)
# 16:9 배치 — 4:3 값에 화면 높이 비율(864/1080=0.8)을 곱해 줄이고, 박스는 가운데 정사각(x 336~1200) 안쪽에 둡니다.
RADAR_TITLE_LAYOUT_169 = {
    "box_x": (348, 1188),  # 가운데 정사각(336~1200) 안쪽, 좌우 여백 12px
    "radius": 22, "bar_h": 8, "bottom_gap": 80, "pad_x": 32, "pad_y": 38,
    "font_size": 68, "font_min": 52,
    "logo_size": 24, "logo_gap": 18, "icon_d": 24, "icon_gap": 10, "icon_line": 3, "icon_dot": 4,
}
# 4:3 배치 — 옛 글(1440x1080 사진)용으로 예전 값을 그대로 둡니다.
RADAR_TITLE_LAYOUT_43 = {
    "box_x": (230, 1210),  # 가운데 정사각(180~1260) 안쪽, 좌우 여백 50px
    "radius": 28, "bar_h": 10, "bottom_gap": 100, "pad_x": 40, "pad_y": 48,
    "font_size": 84, "font_min": 64,
    "logo_size": 30, "logo_gap": 22, "icon_d": 30, "icon_gap": 12, "icon_line": 4, "icon_dot": 5,
}

_RADAR_TITLE_FONTS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Pretendard-ExtraBold.otf"),
    "C:\\Windows\\Fonts\\malgunbd.ttf",
    "malgunbd.ttf",
]

_RADAR_COVER_EXTRA_BANS_EN = (
    "Also do NOT depict: cash, banknotes or coins of any kind; any readable letters, numbers, logos or barcodes; "
    "a person holding up a sign or board. "
)
_RADAR_COVER_EXTRA_BANS_KO = (
    "추가 금지: 현금·지폐·동전, 읽을 수 있는 글자·숫자·로고·바코드, 판이나 글자를 든 사람. "
    "이미지 안에는 글자·숫자·로고가 하나도 없어야 합니다."
)
# 표지: 정사각 썸네일로 잘려도 괜찮게 주인공을 가운데 정사각(가로 가운데 약 56%) 안에 둡니다.
_RADAR_COVER_FRAME_EN = (
    "Wide 16:9 landscape frame. Keep the main subject and everything important inside the CENTRAL SQUARE of the frame "
    "(the middle ~56% of the width), because the image will also be shown cropped to a square. "
    "The left and right edges should be simple background only. "
)
_RADAR_COVER_FRAME_KO = (
    "가로로 넓은 16:9 사진입니다. 주인공과 중요한 것은 모두 화면 가운데 정사각형(가로 가운데 약 56%) 안에 두세요"
    "(이미지가 정사각형으로 잘려 보이기도 합니다). 좌우 가장자리는 단순한 배경만 두세요. "
)
# 본문 사진: 주인공을 가운데 쪽에 둡니다(정사각 자르기 제한은 없음).
_RADAR_BODY_FRAME_EN = (
    "Wide 16:9 landscape frame. Place the main subject toward the center of the frame, "
    "with simple, uncluttered background toward the left and right edges. "
)
_RADAR_BODY_FRAME_KO = (
    "가로로 넓은 16:9 사진입니다. 주인공을 화면 가운데 쪽에 두고, 좌우 가장자리는 단순한 배경으로 두세요. "
)


def build_radar_cover_prompt(scene: str, topic: str) -> str:
    """생활레이더 표지 프롬프트(영문 본문 + [KO] 보조 문단). scene = 메모의 "표지 장면:" 문장, topic = 글 제목/키워드."""
    scene = (scene or "").strip()
    topic = (topic or "").strip()
    if PROMPT_STYLE == "compact":  # 짧은 프롬프트: 주인공은 가로 가운데 약 55% 안, 아래 40%는 단순(제목 박스)
        return _compact_prompt(
            scene, topic, "cover", money_ban=True,
            layout_en=(
                "Wide 16:9. Main subject inside the central square (middle ~55% of the width), simple background at the "
                "left and right edges; keep the bottom 40% simple and slightly darker for a title box."
            ),
        )
    en = (
        f"═══ MANDATORY SCENE ═══ {scene} ═══ END MANDATORY SCENE ═══ "
        f"═══ HIGHEST PRIORITY RULE ═══ This image must contain ZERO text, letters, numbers, logos or symbols of any "
        f"kind. No signs, posters, labels, screens, calendars with numbers, packaging print or barcodes. "
        f"═══ END OF RULE ═══ "
        f"A high-quality, photorealistic photograph for a news-style lifestyle blog cover, related to the topic: "
        f"'{topic}'. "
        f"Style: {_COVER_NO_FACE_STYLE_EN} "
        f"Composition: {_RADAR_COVER_FRAME_EN}"
        f"Keep the bottom 45% of the frame simple, uncluttered and slightly darker (a title box will be placed there). "
        f"{_COVER_NO_FACE_RULE_EN}"
        f"{_COVER_IMPACT_BANS_EN}"
        f"{_RADAR_COVER_EXTRA_BANS_EN}"
        f"Do NOT make it monochromatic, faded, washed-out, or sepia-toned. "
        f"No illustration, vector art, 3D render or plastic AI look."
    )
    ko = (
        f"[KO] {_RADAR_COVER_FRAME_KO}"
        "화면 아래쪽 45%는 단순하고 약간 어둡게 비워 두세요(제목 박스가 올라갑니다). "
        f"{_COVER_NO_FACE_RULE_KO}{_COVER_IMPACT_BANS_KO}{_RADAR_COVER_EXTRA_BANS_KO}"
    )
    return f"{en}\n\n{ko}"


def build_radar_body_prompt(scene: str, topic: str, angle: dict | None = None) -> str:
    """생활레이더 본문 사진 프롬프트(영문 본문 + [KO] 보조 문단). scene = 메모 "본문 이미지 장면:"의 장면 문장.
    angle: 구도(BODY_SHOT_ANGLES 항목). 주면 "Composition" 뒤에 구도 문장을 붙입니다(메모 장면에 구도가 있으면 메모 우선, 주인공은
    화면 가운데 쪽에 둔다). None(기본)이면 구도 문장 없이 만듭니다."""
    scene = (scene or "").strip()
    topic = (topic or "").strip()
    if PROMPT_STYLE == "compact":  # 짧은 프롬프트: 주인공은 가로 가운데 약 55% 안, 구도는 장면에 구도 말이 없을 때만 붙임
        return _compact_prompt(
            scene, topic, "body", angle=angle, money_ban=True,
            layout_en="Wide 16:9. Main subject inside the central ~55% of the width, simple background at the left and right edges.",
        )
    angle_en = angle_ko = ""
    if angle:
        angle_en = (
            f"Camera angle for this photo: {angle['en']}. If the MANDATORY SCENE above already specifies a camera angle or "
            f"composition (for example 'seen from above' or 'close-up'), follow the scene's own angle and ignore this one. "
            f"Whatever the angle, keep the main subject toward the center of the frame. "
        )
        angle_ko = (
            f"구도: 장면 문장에 구도(예: 위에서 내려다본, 클로즈업)가 이미 있으면 그 구도를 따르고, 없으면 이 구도로 만드세요 — {angle['ko']}. "
            "어떤 구도여도 주인공은 화면 가운데 쪽에 두세요. "
        )
    en = (
        f"═══ MANDATORY SCENE ═══ {scene} ═══ END MANDATORY SCENE ═══ "
        f"═══ HIGHEST PRIORITY RULE ═══ This image must contain ZERO text, letters, numbers, logos or symbols of any "
        f"kind. No signs, posters, labels, screens, calendars with numbers, packaging print or barcodes. "
        f"═══ END OF RULE ═══ "
        f"A high-quality, photorealistic, natural everyday-life photograph for a news-style lifestyle blog article, "
        f"related to the topic: '{topic}'. "
        f"Style: natural, candid everyday-life photograph with soft natural light and realistic colors; the main "
        f"subject is clear and easy to read at a glance; photorealistic, not staged-looking, not AI-plastic. "
        f"Composition: {_RADAR_BODY_FRAME_EN}"
        f"{angle_en}"
        f"The main subject of the scene is clearly visible and in sharp focus, "
        f"shown in a natural, candid, real-life moment. "
        f"{_COVER_NO_FACE_RULE_EN}"
        f"{_COVER_IMPACT_BANS_EN}"
        f"{_RADAR_COVER_EXTRA_BANS_EN}"
        f"Do NOT make it monochromatic, faded, washed-out, or sepia-toned. "
        f"No illustration, vector art, 3D render or plastic AI look."
    )
    ko = (
        f"[KO] {_RADAR_BODY_FRAME_KO}"
        f"{angle_ko}"
        "위 장면의 주인공이 또렷하게 보이는 자연스러운 생활 사진의 한 순간으로 만드세요. "
        f"{_COVER_NO_FACE_RULE_KO}{_COVER_IMPACT_BANS_KO}{_RADAR_COVER_EXTRA_BANS_KO}"
    )
    return f"{en}\n\n{ko}"


def _call_gpt_image_square(prompt: str) -> Image.Image:
    """(옛 1:1 표지용 — 사용 안 함) 정사각형(1024x1024) 생성."""
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": IMAGE_MODEL, "prompt": prompt, "size": RADAR_COVER_GEN_SIZE, "quality": BODY_IMAGE_QUALITY}
    resp = requests.post(OPENAI_IMAGE_ENDPOINT, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    b64 = resp.json()["data"][0]["b64_json"]
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def _mock_square_image(label: str) -> Image.Image:
    """(옛 1:1 표지용 — 사용 안 함)"""
    img = Image.new("RGB", (1024, 1024), random.choice([(59, 130, 246), (16, 185, 129), (245, 158, 11), (139, 92, 246)]))
    ImageDraw.Draw(img).multiline_text(
        (24, 20), textwrap.fill(f"[목 모드] 생활레이더 표지 {label}", width=24), fill=(255, 255, 255), font=_load_font(28)
    )
    return img


def _radar_fit_43(img: Image.Image) -> Image.Image:
    """사진을 가운데 기준 4:3으로 자른 뒤 1440x1080으로 맞춥니다(1536x1024 → 1365x1024 → 1440x1080)."""
    img = img.convert("RGB")
    w, h = img.size
    tw, th = RADAR_OUT_SIZE_43
    if w * th > h * tw:  # 너무 넓다 → 좌우를 자름
        new_w = round(h * tw / th)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif w * th < h * tw:  # 너무 높다 → 위아래를 자름
        new_h = round(w * th / tw)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    if img.size != RADAR_OUT_SIZE_43:
        img = img.resize(RADAR_OUT_SIZE_43, Image.LANCZOS)
    return img


def _radar_fit_169(img: Image.Image) -> Image.Image:
    """사진을 가운데 기준 16:9로 자릅니다(1536x1024 → 1536x864, 확대 없음 — 다른 채널의 _center_crop_16_9와 같은 방식).
    크기가 1536x864가 아닌 사진(다른 크기로 들어온 16:9)만 1536x864로 맞춥니다."""
    img = _center_crop_16_9(img.convert("RGB"))
    if img.size != RADAR_OUT_SIZE_169:
        img = img.resize(RADAR_OUT_SIZE_169, Image.LANCZOS)
    return img


def _call_gpt_image_radar_169(prompt: str, quality: str = BODY_IMAGE_QUALITY) -> Image.Image:
    """생활레이더 전용: 1536x1024로 생성 → 가운데 16:9로 자른 1536x864. (4:3 확대는 하지 않음)"""
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": IMAGE_MODEL, "prompt": prompt, "size": RADAR_GEN_SIZE_169, "quality": quality}
    resp = requests.post(OPENAI_IMAGE_ENDPOINT, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    b64 = resp.json()["data"][0]["b64_json"]
    return _radar_fit_169(Image.open(io.BytesIO(base64.b64decode(b64))))


def _mock_image_169(label: str) -> Image.Image:
    """목 모드용 가짜 16:9 이미지(실제 생성과 같은 1536x1024 → 가운데 16:9 자르기 경로를 거칩니다). API를 부르지 않습니다."""
    base = Image.new("RGB", (1536, 1024), random.choice([(59, 130, 246), (16, 185, 129), (245, 158, 11), (139, 92, 246)]))
    ImageDraw.Draw(base).multiline_text(
        (110, 120), textwrap.fill(f"[목 모드] 생활레이더 {label}", width=24), fill=(255, 255, 255), font=_load_font(36)
    )
    return _radar_fit_169(base)


def _save_radar_jpg(img: Image.Image, filepath: str) -> str:
    img.convert("RGB").save(filepath, "JPEG", quality=RADAR_COVER_JPG_QUALITY, optimize=True)
    return "/" + filepath.replace(os.sep, "/")


def generate_radar_cover(scene: str, topic: str, article_id: str) -> str:
    """표지용 글자 없는 사진 1장(1536x864 JPG, 16:9)을 만들어 {글id}_radar_cover_photo_{8자}.jpg로 저장하고 웹 경로를 돌려줍니다.
    제목 얹기는 compose_radar_cover_title()이 따로 합니다. 파일 이름은 매번 새로(브라우저 캐시 방지).
    USE_MOCK(API 키 없음)이면 가짜 16:9 이미지로 대신합니다 — 이 경우 API를 부르지 않습니다."""
    import uuid as _uuid

    prompt = build_radar_cover_prompt(scene, topic)
    img = _mock_image_169(topic) if USE_MOCK else _call_gpt_image_radar_169(prompt, COVER_IMAGE_QUALITY)
    filepath = os.path.join(_current_output_dir(), f"{article_id}_radar_cover_photo_{_uuid.uuid4().hex[:8]}.jpg")
    return _save_radar_jpg(img, filepath)


def generate_radar_body_image(scene: str, topic: str, article_id: str, n: int, angle: dict | None = None) -> str:
    """본문 사진 1장(1536x864 JPG, 16:9)을 만들어 저장하고 웹 경로("/static/generated/…")를 돌려줍니다.
    USE_MOCK(API 키 없음)이면 가짜 16:9 이미지로 대신합니다 — 이 경우 API를 부르지 않습니다.
    파일 이름은 매번 새로({글id}_radar_body_{n}_{8자}.jpg) — 브라우저 캐시 때문에 옛 사진이 보이는 일을 막습니다."""
    import uuid as _uuid

    prompt = build_radar_body_prompt(scene, topic, angle)
    img = _mock_image_169(f"본문 {n}") if USE_MOCK else _call_gpt_image_radar_169(prompt)
    filepath = os.path.join(_current_output_dir(), f"{article_id}_radar_body_{n}_{_uuid.uuid4().hex[:8]}.jpg")
    return _save_radar_jpg(img, filepath)


# ── 네이버용 본문 사진 (photo_v2_20261020, Pillow 합성 · API 호출 없음 · 0원) ──────────────────────────────
# 네이버 에디터에는 사진을 파일로 올리는데, 올린 사진은 글 폭을 꽉 채웁니다. 본문 사진이 너무 커 보이지 않게, 원본 사진을 가운데 두고
# 좌우에 흰색 여백을 붙인 "네이버용 파일"을 따로 만듭니다 — 전체 가로 = 사진 가로 ÷ NAVER_BODY_IMAGE_RATIO (사진이 전체 폭의 75%).
# 예: 1536×864 → 2048×864, 1440×1080 → 1920×1080. 원본 파일은 그대로 두고, 표지·카드·출처 캡처에는 쓰지 않습니다.
# ※ 2026-10-27(radar169_nv75off) 이 기능은 꺼졌습니다 — 아래 상수·함수는 되돌리기 쉽게 남겨 두었고, 더 이상 호출되지 않습니다.
NAVER_BODY_IMAGE_RATIO = 0.75
NAVER_BODY_PAD_COLOR = (255, 255, 255)
NAVER_BODY_NV75_TAG = "_nv75"


def make_naver_body_image(src_web_path: str, out_dir: str | None = None) -> str:
    """원본 본문 사진(웹 경로 "/static/generated/…jpg")을 가운데 두고 좌우에 흰 여백을 붙인 새 JPG를 원본 옆(같은 폴더, out_dir를 주면 거기)에
    "{원본이름}_nv75_{8자}.jpg"로 저장하고 웹 경로를 돌려줍니다(매번 새 이름 — 브라우저 캐시 방지). 파일이 없으면 FileNotFoundError.
    JPG 품질은 GEN_JPG_QUALITY. out_dir를 준 테스트용 호출은 웹 경로 대신 저장된 파일 경로를 돌려줍니다."""
    import uuid as _uuid

    local = src_web_path.lstrip("/")
    with Image.open(local) as im:
        src = im.convert("RGB")
    w, h = src.size
    total_w = int(round(w / NAVER_BODY_IMAGE_RATIO))
    canvas = Image.new("RGB", (total_w, h), NAVER_BODY_PAD_COLOR)
    canvas.paste(src, ((total_w - w) // 2, 0))
    stem = os.path.splitext(os.path.basename(local))[0]
    name = f"{stem}{NAVER_BODY_NV75_TAG}_{_uuid.uuid4().hex[:8]}.jpg"
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, name)
        _save_gen_jpg(canvas, path)
        return path
    path = os.path.join(os.path.dirname(local), name)
    _save_gen_jpg(canvas, path)
    return "/" + path.replace(os.sep, "/")


# ── 표지에 카드 제목 얹기 (Pillow, 0원) ──────────────────────────────────────────

def _radar_title_font(size: int):
    for p in _RADAR_TITLE_FONTS:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _radar_wrap_title(draw, text: str, font, max_w: int) -> list[str]:
    """띄어쓰기 기준으로 줄을 나눕니다. 2줄이면 두 줄 길이가 비슷해지는 자리에서 나눕니다. 한 단어가 너무 길면 글자 단위로 끊습니다."""
    tl = lambda s: draw.textlength(s, font=font)  # noqa: E731
    text = " ".join((text or "").split())
    if not text:
        return [""]
    if tl(text) <= max_w:
        return [text]
    lines, cur = [], ""
    for word in text.split(" "):
        trial = word if not cur else cur + " " + word
        if tl(trial) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        if tl(word) <= max_w:
            cur = word
            continue
        piece = ""
        for ch in word:
            if tl(piece + ch) <= max_w:
                piece += ch
            else:
                lines.append(piece)
                piece = ch
        cur = piece
    if cur:
        lines.append(cur)
    if len(lines) == 2:
        words = " ".join(lines).split(" ")
        best, best_w = lines, max(tl(ln) for ln in lines)
        for i in range(1, len(words)):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            w = max(tl(a), tl(b))
            if w <= max_w and w < best_w:
                best, best_w = [a, b], w
        return best
    return lines


def _radar_rounded_mask(size: tuple[int, int], radius: int, scale: int = 3) -> Image.Image:
    """모서리가 부드러운 둥근 사각형 마스크(L)."""
    w, h = size
    big = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * scale - 1, h * scale - 1), radius=radius * scale, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def _radar_prepare_cover_photo(photo: Image.Image) -> tuple[Image.Image, dict]:
    """제목을 얹을 사진의 실제 비율을 보고 배치를 고릅니다 — 16:9(가로가 세로의 1.55배 이상)이면 새 배치(1536x864),
    그보다 좁으면(옛 4:3 사진 등) 옛 4:3 배치(1440x1080)를 씁니다. 크기가 표준과 다르면 표준 크기로 맞춥니다."""
    base = photo.convert("RGB")
    w, h = base.size
    if w >= h * 1.55:
        return (base if base.size == RADAR_OUT_SIZE_169 else _radar_fit_169(base)), RADAR_TITLE_LAYOUT_169
    return (base if base.size == RADAR_OUT_SIZE_43 else _radar_fit_43(base)), RADAR_TITLE_LAYOUT_43


def render_radar_cover_title(photo: Image.Image, title: str, out_path: str) -> dict:
    """사진(글자 없음) 위에 남색 제목 박스 + 작은 레이더 로고를 얹어 out_path(JPG)에 저장합니다.
    16:9 사진(1536x864)은 새 배치, 옛 4:3 사진(1440x1080)은 옛 배치를 씁니다(_radar_prepare_cover_photo).
    반환: {"path", "warnings", "lines", "font_size", "box", "logo_box"} — 박스·글자·로고는 가운데 정사각(16:9: x 336~1200, 4:3: 180~1260) 안에 들어갑니다."""
    warnings: list[str] = []
    title = " ".join((title or "").split())
    if len(title) > RADAR_TITLE_LONG:
        warnings.append(f"카드 제목이 {len(title)}자예요(표지에는 {RADAR_TITLE_LONG}자 이내를 권장해요). 그대로 그렸어요.")

    base, L = _radar_prepare_cover_photo(photo)
    W, H = base.size
    meas = ImageDraw.Draw(base)

    x0, x1 = L["box_x"]
    max_w = (x1 - x0) - 2 * L["pad_x"]
    size = L["font_size"]
    while True:
        font = _radar_title_font(size)
        lines = _radar_wrap_title(meas, title, font, max_w)
        if len(lines) <= RADAR_TITLE_MAX_LINES or size <= L["font_min"]:
            break
        size = max(L["font_min"], size - RADAR_TITLE_FONT_STEP)
    if len(lines) > RADAR_TITLE_MAX_LINES:
        warnings.append(f"카드 제목이 {size}px에서도 {len(lines)}줄로 넘쳐요(최대 {RADAR_TITLE_MAX_LINES}줄). 제목을 줄여 주세요.")
    if any(meas.textlength(ln, font=font) > max_w + 0.5 for ln in lines):
        warnings.append("카드 제목의 한 줄이 박스 폭을 넘어요. 제목을 줄여 주세요.")

    line_h = int(size * RADAR_TITLE_LINE_RATIO)
    box_h = len(lines) * line_h + 2 * L["pad_y"]
    y1 = H - L["bottom_gap"]
    y0 = y1 - box_h
    bw = x1 - x0

    # 1) 박스(불투명도 88%) + 윗변 주황 줄 — 둥근 모서리 안쪽으로만
    mask = _radar_rounded_mask((bw, box_h), L["radius"])
    layer = Image.new("RGB", (bw, box_h), RADAR_TITLE_BOX_COLOR)
    alpha = mask.point(lambda v: int(v * RADAR_TITLE_BOX_OPACITY))
    base.paste(layer, (x0, y0), alpha)
    bar_mask = Image.new("L", (bw, box_h), 0)
    ImageDraw.Draw(bar_mask).rectangle((0, 0, bw, L["bar_h"] - 1), fill=255)
    bar_mask = ImageChops.multiply(bar_mask, mask)
    base.paste(Image.new("RGB", (bw, box_h), RADAR_TITLE_BAR_COLOR), (x0, y0), bar_mask)

    # 2) 제목 글자(흰색, 가운데 정렬) — 주황 줄 아래 영역의 한가운데
    draw = ImageDraw.Draw(base)
    area_top = y0 + L["bar_h"]
    block_h = len(lines) * line_h
    ty = area_top + ((y1 - area_top) - block_h) / 2
    cx = (x0 + x1) / 2
    for i, ln in enumerate(lines):
        draw.text((cx, ty + (i + 0.5) * line_h), ln, font=font, fill=(255, 255, 255), anchor="mm")

    # 3) 박스 바로 위 가운데: 작은 레이더 아이콘(주황 원 + 가운데 점) + "생활레이더" — DRAW_RADAR_COVER_BRAND가 False면 그리지 않습니다
    logo_box = None
    if DRAW_RADAR_COVER_BRAND:
        lf = _radar_title_font(L["logo_size"])
        tw = draw.textlength(RADAR_LOGO_TEXT, font=lf)
        icon_d, gap = L["icon_d"], L["icon_gap"]
        gx = cx - (icon_d + gap + tw) / 2
        ly = y0 - L["logo_gap"] - L["logo_size"] / 2
        shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.text((gx + icon_d + gap + 2, ly + 2), RADAR_LOGO_TEXT, font=lf, fill=(0, 0, 0, 170), anchor="lm")
        sd.ellipse((gx + 2, ly - icon_d / 2 + 2, gx + icon_d + 2, ly + icon_d / 2 + 2), outline=(0, 0, 0, 170), width=L["icon_line"])
        shadow = shadow.filter(ImageFilter.GaussianBlur(2))
        base = Image.alpha_composite(base.convert("RGBA"), shadow).convert("RGB")
        draw = ImageDraw.Draw(base)
        draw.text((gx + icon_d + gap, ly), RADAR_LOGO_TEXT, font=lf, fill=(255, 255, 255), anchor="lm")
        icx, icy = gx + icon_d / 2, ly
        draw.ellipse((icx - icon_d / 2, icy - icon_d / 2, icx + icon_d / 2, icy + icon_d / 2), outline=RADAR_LOGO_ORANGE, width=L["icon_line"])
        dot = L["icon_dot"]
        draw.ellipse((icx - dot, icy - dot, icx + dot, icy + dot), fill=RADAR_LOGO_ORANGE)
        logo_box = (round(gx), round(ly - max(icon_d, L["logo_size"]) / 2), round(gx + icon_d + gap + tw), round(ly + max(icon_d, L["logo_size"]) / 2))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    base.save(out_path, "JPEG", quality=RADAR_COVER_JPG_QUALITY, optimize=True)
    return {"path": out_path, "warnings": warnings, "lines": lines, "font_size": size, "box": (x0, y0, x1, y1), "logo_box": logo_box}


def compose_radar_cover_title(photo_path: str, title: str, article_id: str) -> dict:
    """글자 없는 표지 사진(웹 경로 또는 파일 경로)에 제목을 얹어 {글id}_radar_cover_{8자}.jpg로 저장합니다(0원).
    반환: {"path": 웹 경로, "warnings": […], "lines", "font_size"}. 사진 파일을 열 수 없으면 예외를 그대로 올립니다."""
    import uuid as _uuid

    src = photo_path.lstrip("/") if photo_path.startswith("/") else photo_path
    with Image.open(src) as im:
        photo = im.convert("RGB")
    out = os.path.join(_current_output_dir(), f"{article_id}_radar_cover_{_uuid.uuid4().hex[:8]}.jpg")
    res = render_radar_cover_title(photo, title, out)
    res["path"] = "/" + out.replace(os.sep, "/")
    return res
