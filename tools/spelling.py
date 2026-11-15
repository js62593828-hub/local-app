# -*- coding: utf-8 -*-
"""표기 통일·넓은 주제 이름 통일 (plan_posts · fingerprint · check_plan 이 같이 쓴다).

- SPELL_UNIFY: 같은 말인데 표기만 다른 것을 하나로 (오른쪽이 대표 표기 = 검색량이 큰 쪽).
- TOPIC_GROUP_RULES: 넓은 주제(topic_group) 이름이 흔들려도(외풍/방풍/단열/창호, 수전/배관/배수) 같은 이름으로 묶는다.
  이름이 names 에 있거나 키워드에 words 중 하나가 들어 있으면 그 묶음 이름으로 바꾼다.
"""
SPELL_UNIFY = {
    "샤시": "샷시", "새시": "샷시",        # 샷시 4,600 > 샤시 1,460 (2026-11-13 실행 기록)
    "우풍": "웃풍",                          # 웃풍 1,240 > 우풍 360
    "펜트리": "팬트리",
}

TOPIC_GROUP_RULES = [
    {"name": "외풍·창호",
     "names": ["외풍", "웃풍", "방풍", "단열", "창호", "방한용품", "샷시", "문풍지", "풍지", "바람막이", "틈막이"],
     "words": ["외풍", "웃풍", "우풍", "방풍", "문풍지", "바람막이", "틈막이", "샷시", "샤시", "새시", "창호", "단열"]},
    {"name": "수전·배관",
     "names": ["수전", "배관", "배수", "수도", "배관도구", "하수구", "배수관"],
     "words": ["수전", "배관", "배수", "수도", "하수구", "트랩", "싱크대호스", "샤워기호스", "배수구"]},
]


def respell(s: str) -> str:
    for a, b in SPELL_UNIFY.items():
        s = s.replace(a, b)
    return s


def canon_topic_group(name, keyword: str = ""):
    """넓은 주제 이름을 통일한 이름으로. 해당 없으면 원래 이름 그대로."""
    kw = "".join((keyword or "").split())
    nm = "".join((name or "").split())
    for rule in TOPIC_GROUP_RULES:
        if nm in rule["names"] or any(w in kw for w in rule["words"]):
            return rule["name"]
    return name
