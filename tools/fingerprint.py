# -*- coding: utf-8 -*-
"""키워드 지문 색인 (1.5단계): 중복·같은 글 판정을 '지문이 같은가 + 가까운 쌍만 확인'으로 바꾼다.

지문 = 대상|문제|세부   (묶음 = 대상|문제, 앞 두 칸)
  - 대상: 문제가 생기는 큰 물건·장소 (욕실수전 ≠ 싱크대수전). 대상 이름은 target_alias.json 으로 같은 물건의 다른 이름을 하나로 정리한다.
  - 문제: PROBLEMS 목록에서만 고른다. 제품 이름 키워드는 문제를 '제품 선택(구매)' 으로 한다.
  - 세부: 같은 대상·같은 문제 안에서 글을 가르는 것 하나(부위·증상·제품 기능/용도/쓰는 사람·고칠 곳). 가를 게 없으면 '일반'.

판정 순서 (코드)
  ① 지문 3칸이 같으면 같은 글.
  ② 가까운 쌍(묶음이 같고 세부만 다름 / 문제가 같고 대상 이름 한쪽이 다른 쪽을 포함)이면 Claude 에 그 쌍만 물어 확정한다.
  ③ 확정한 쌍은 pair_verdicts.json 에 영구 저장하고 다시 묻지 않는다.
  ④ 그 외는 다른 글.

파일 (pipeline/)
  keyword_index.json / post_index.json : 항목별 {대상(원래 이름), 문제, 세부, 주제, 유형(kw_type) ...} + 계산된 지문
  target_alias.json  : 대상 이름 정리표 {같은 물건의 다른 이름 → 대표 이름}
  pair_verdicts.json : 가까운 쌍 확정 기록

원칙: 한 번 판정한 것은 다시 묻지 않는다 · 호출이 실패하면 그 호출 몫은 아무것도 쓰지 않는다(성공한 묶음은 바로 저장)
      · 사용 한도는 UsageLimitError 로 그대로 올려 보낸다.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_posts as pp  # noqa: E402
from plan_posts import UsageLimitError, call_claude, extract_json, model_for, norm  # noqa: E402
from safe_io import write_text_retry  # noqa: E402
import spelling  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = Path(os.environ.get("LOCALAPP_INDEX_DIR") or BASE / "pipeline")     # 시험 실행은 환경변수로 색인 폴더를 따로 둔다(운영 색인을 건드리지 않음)
KEYWORD_INDEX = OUT_DIR / "keyword_index.json"
POST_INDEX = OUT_DIR / "post_index.json"
TARGET_ALIAS = OUT_DIR / "target_alias.json"
PAIR_VERDICTS = OUT_DIR / "pair_verdicts.json"
BATCH = 60
MODEL = "sonnet"
MAX_TURNS = "1"

PROBLEMS = ["막힘", "냄새", "곰팡이", "물때·얼룩", "교체·설치", "세탁", "청소", "습기·김서림", "결로", "동파", "누수",
            "고장·수리", "보관·정리", "해충 퇴치", "제품 선택(구매)", "정보(원인·종류)", "기타"]
GENERAL = "일반"
PAIR_VERSION = 6        # 쌍 판정 질문(same/cover/diff)을 바꾸면 올린다. 다른 버전의 저장 답은 다시 묻는다(fixed 는 제외).
TYPE_VERSION = 3        # 업체형(service)·주제 밖(offtopic) 등 유형 판정 기준을 바꾸면 올린다.
MONEY_PROBLEM = "제품 선택(구매)"      # 지문의 문제가 이것이면 수익글(money), 그 외 전부 정보글(info)
PROMPT_VERSION = 14      # 지문 만드는 질문·규칙을 바꾸면 올린다. 이전 버전으로 판정된 항목은 다시 판정한다.
# 14판은 해충 약·퇴치제 이름 규칙만 바꿨으므로, 13판 판정 중 해충 약 관련이 아닌 항목은 그대로 쓴다(다시 묻지 않음).
KEEP_VERSIONS = {13}
PEST_RE = re.compile(r"퇴치|살충|방충|해충|(바퀴|벌레|개미|모기|파리|진드기|빈대|쥐|나방|좀|벌|지네|돈벌레)\S*약")


BRAND_EXEMPT = ("단점", "장단점", "후기", "비교")
PS_VERSION = 9          # 제품 여부(product)·해결 문제(solves) 질문을 바꾸면 올린다. (지문 판정과 따로 저장 — 지문은 다시 묻지 않는다)
NO_SOLVE_PROBLEMS = {MONEY_PROBLEM, "정보(원인·종류)", "기타"}      # solves 의 문제로 쓸 수 없는 것(살림 문제가 아님)


class FingerprintError(RuntimeError):
    """지문·쌍 판정 호출 실패(사용 한도 제외). 이 호출 몫은 색인에 쓰지 않았다."""


PROMPT = """너는 생활 살림 블로그의 편집자다. 아래 [항목]마다 '지문'을 정한다. 지문 = 대상|문제|세부.
같은 글이 될 항목은 같은 지문이 되고, 다른 글이 될 항목은 다르게 되어야 한다. 도구는 쓰지 말고 바로 답해라.
항목은 검색 키워드이거나 이미 쓴 글의 제목이다. 제목이면 그 글이 해결하려는 핵심 문제 하나만 본다.

[대상 target] 문제가 생기는 '큰 물건·장소'를 띄어쓰기 없는 명사 하나로 쓴다. 키워드 글자에 나온 물건만 쓴다(없는 물건을 추측해서 쓰지 않는다).
- 장소가 물건을 가르면 장소를 붙인다(욕실수전, 싱크대수전, 욕조수전, 세면대수전은 서로 다른 대상이다). 표기 통일: 씽크대→싱크대, 화장실→욕실, 세무→스웨이드, 자동차·차→차량.
- 부위·부품·일부분(하부장, 배수구, 유리, 모서리, 고무패킹, 세탁조, 호스, 경첩, 배관 같은)은 대상이 아니라 아래 '세부'에 쓰고, 대상은 그것이 속한 큰 물건(싱크대, 욕실, 세탁기)으로 한다.
  단, 따로 사고파는 독립 물건(수전, 샤워기헤드, 환풍기, 제습기, 선반, 세제)은 그 자체가 대상이다.
- 제품 이름 키워드의 대상은 '그 제품 종류'다. 장소나 큰 물건으로 뭉뚱그리지 않는다(스노우체인·워셔액·세차사다리를 모두 '차량'으로 쓰지 않는다).
  뚫는 도구·청소 도구·공구 이름 키워드(코브라, 먼지털이개, 스크래퍼)도 제품이다 → 문제는 '제품 선택(구매)'.
  색상·단수·크기·재질·디자인·수량·브랜드·모델은 이름에서 빼고 제품 종류만 쓴다. 쓰는 장소(차량, 옷장, 욕실, 드럼세탁기용)는 대상에 남긴다(차량제습제 ≠ 옷장제습제).
- 혈흔·기름·커피 같은 특정 얼룩을 빼는 세제·제거제 키워드(생리혈세제, 기름얼룩제거제)는 '세제'가 아니라 얼룩이 묻는 물건(옷, 벽지, 카펫)이 대상, 문제는 '물때·얼룩'이다.
  단 '가루세제, 드럼세탁기세제, 캡슐세제'처럼 세제 자체를 고르는 키워드는 대상 '세제', 문제 '제품 선택(구매)'이다.
- 몸(발·손)에서 나는 냄새나 때는 그것이 배는 물건을 대상으로 한다(발냄새 → 신발). 키워드에 없는 공간(신발장 등)으로 바꾸지 않는다.
- 같은 대상은 [이미 쓰는 대상 이름]에 있으면 그 글자 그대로 쓴다. 없을 때만 새 이름을 만든다.

[문제 problem] 아래 목록에서 정확히 하나만, 목록의 글자 그대로 고른다.
{problems}
- 방법·비용·셀프·하는법·했을때·원인별·순서 같은 덧말은 무시하고, 사람이 풀려는 핵심 문제 하나로 고른다.
- 제품을 고르려는 검색·글(추천, 순위, 비교, 종류, 고르는 법, 가격대, 가성비, 제품 이름만 있는 키워드, 사려는 도구)은 모두 '제품 선택(구매)' 이고 대상은 제품 종류다.
- 스티커·접착제·송진·페인트 자국·워터스팟·찌든 때를 없애는 검색은 '물때·얼룩', 막힌 것을 뚫는 것은 '막힘', 성에·서리·김서림·습기가 차는 것은 '습기·김서림', 벽 속 물방울이 핵심이면 '결로'.
- 해충 약·퇴치제·살충제·퇴치기·트랩 이름 키워드(바퀴벌레퇴치제, 바퀴벌레약추천, 개미약, 모기퇴치기)는 제품 이름이다 → 문제는 '제품 선택(구매)', 대상은 '해충이름+퇴치제'로 통일한다(바퀴벌레약·바퀴벌레퇴치약·바퀴벌레퇴치제 → 바퀴벌레퇴치제, 개미약 → 개미퇴치제, 모기퇴치기 → 모기퇴치기). 세부는 겔·스프레이·훈연제처럼 형태가 글을 가를 때만 쓰고, 아니면 '일반'.
  해충 이름만 있고 없애는 방법을 찾는 키워드(바퀴벌레 없애는법, 개미 퇴치)는 '해충 퇴치'.
- 해충을 없애려는 것은 '해충 퇴치', 원인·종류·특징처럼 알고 싶은 정보는 '정보(원인·종류)', 어디에도 안 맞을 때만 '기타'.

[세부 detail] 같은 대상·같은 문제 안에서 글을 가르는 것 '하나'를 2~6글자로 쓴다. 가를 게 없으면 '일반'.
- 부위·부품(고무패킹, 세탁조, 배수관, 경첩, 호스), 증상(성에, 김서림, 물때), 고칠 곳(지퍼, 구멍, 충전재),
  제품의 기능·용도·쓰는 사람(절수, 수압상승, 아기, 드레스룸, 필터, 캡슐, 손세탁), 소재·종류가 글을 가르는 경우(구스다운, 스웨이드)를 쓴다.
- 색상·단수·크기·브랜드·'홈쇼핑·쇼핑몰·판매처' 같은 파는 곳 표시·'추천/순위/가성비' 같은 덧말은 세부가 아니다(그럴 땐 '일반').
- 원인·확인할 점·해결 수단·순서·시간(결로 때문에, 소재 확인, 식초, 10분 코스, 원인별)은 세부가 아니다. 제목이 그런 말을 곁들여도 글이 '그 대상·문제 전체'를 다루면 '일반'이다.
  세부는 키워드나 글이 '이 부위만, 이 증상만, 이 기능의 제품만, 이 사람용만'처럼 범위를 좁힐 때만 쓴다.
- 세부가 문제 이름과 같은 말(문제가 '물때·얼룩'인데 세부가 '물때', 문제가 '냄새'인데 세부가 '냄새' 등)이면 아무것도 가르지 못하므로 '일반'이다.
- 같은 부위를 가리키는 다른 말(배수관·배수구·배수, 패킹·고무패킹)은 같은 세부 하나로 쓴다.
- 같은 뜻은 [이미 쓰는 세부 이름]이 있으면 그 글자 그대로 쓴다.

[주제 topic] 대상들을 넓게 묶는 주제를 2~6글자 명사 하나로(욕실수전·싱크대수전·샤워기헤드 → 수전, 차량 앞유리·사이드미러 → 차량관리). [이미 쓰는 주제 이름]에 있으면 그대로 쓴다.

[이미 쓰는 대상 이름]
{targets}

[이미 쓰는 세부 이름]
{details}

[이미 쓰는 주제 이름]
{topics}

[항목]
{items}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명, 머리말, 코드블록(```)을 붙이지 않는다. 항목 전부를 빠짐없이, 항목 번호 그대로 적는다. 형식:
{{"items": {{"1": {{"target": "...", "problem": "...", "detail": "...", "topic": "..."}}, "2": {{...}}}}}}
생각 과정이나 정정 없이 최종 답만 한 번 출력한다.
"""

ALIAS_PROMPT = """아래는 블로그 글감의 '대상 이름' 목록이다. 같은 물건을 가리키는 다른 이름을 하나로 묶어라. 도구는 쓰지 말고 바로 답해라.
묶는 경우: 표기 차이(씽크대=싱크대), 줄임말·띄어쓰기, 같은 물건의 다른 말(떨이개=털이개, 샤워기=샤워기헤드, 수도꼭지=수전, 싱크대수도꼭지=싱크대수전), 소재·브랜드·등급·기능 같은 수식어만 붙은 이름은 수식어를 뺀 이름으로
(오리털패딩·명품패딩·구스다운패딩 → 패딩, 수압상승샤워기헤드 → 샤워기헤드), 같은 장소의 같은 종류 물건(욕실수전 = 욕실샤워수전 = 샤워수전).
묶지 않는 경우: 서로 다른 물건(제품 ≠ 그 제품을 쓰는 장소·설비: 수도필터 ≠ 수도, 배관필터 ≠ 배관, 욕실매트 ≠ 욕실, 세면대 ≠ 세면대수전, 욕실수전 ≠ 싱크대수전 ≠ 욕조수전 ≠ 세면대수전, 장소 이름을 빼서 '수전'처럼 넓은 이름 하나로 합치는 것, 차량제습제 ≠ 옷장제습제, 바퀴벌레 ≠ 좀벌레), 장소가 다른 것, 확실하지 않은 것.
대표 이름은 묶음 안에서 가장 흔하고 짧은 이름 하나(목록에 있는 글자 그대로). 대표와 별칭은 모두 [목록]에 있는 이름이어야 한다.
{known}
[목록]
{names}

[출력 규칙]
JSON 한 덩어리만 출력한다. 묶을 게 있는 것만 적는다. 형식: {{"groups": [{{"name": "대표", "aliases": ["별칭1", "별칭2"]}}]}}
묶을 게 없으면 {{"groups": []}}. 설명·코드블록 없이 최종 답만.
"""

PAIR_PROMPT = """너는 생활 살림 블로그의 깐깐한 편집장이다. 아래 [쌍]마다 A와 B의 '검색 범위'를 비교해 다섯 답 중 하나로 답한다. 도구는 쓰지 말고 바로 답해라.
각 쌍에는 [대상·세부]가 붙어 있다. 세부는 같은 대상·같은 문제 안에서 글을 가르는 요소('일반'이면 범위를 좁히지 않은 글)다.
- same: 두 검색의 범위가 같다. 글 하나가 A도 B도 같은 깊이로 만족시킨다. 표기·띄어쓰기·같은 뜻의 다른 말(배수관 = 배수구, 수도꼭지 = 수전)·덧말(방법·비용·추천·순위·셀프)·
  강력한·전문가용·대용량·홈쇼핑·쇼핑몰·브랜드 같은 꾸밈말·파는 곳 표시만 다를 때. 해충 퇴치제를 찾는 검색과 약 추천 검색(바퀴벌레퇴치제 ↔ 바퀴벌레약추천)도 같은 제품을 찾으므로 same.
  '관련 있다', '같은 제품군이다', '같은 문제다'는 same 이 아니다. 범위가 같을 때만 same.
- cover_a: A가 더 넓고, B는 A 안의 한 종류·한 부위·한 조건이다 (A = 수전교체방법, B = 욕실수전교체 / A = 샤워헤드추천, B = 온오프샤워기헤드).
- cover_b: B가 더 넓고, A는 B 안의 한 종류·한 부위·한 조건이다.
- diff: 그 밖. 대상이 다르거나(욕실수전 ↔ 싱크대수전, 신발 냄새 ↔ 텀블러 냄새), 문제가 다르거나(변기 교체 ↔ 변기 막힘, 제품 선택 ↔ 원인 알기),
  서로 다른 종류·부위·조건을 각각 달았거나(가루세제 ↔ 드럼세탁기세제, 지퍼수선 ↔ 구멍수선, 경첩 ↔ 호스교체), 서로 겹치지만 어느 쪽도 다른 쪽을 품지 않을 때.
  부품 하나만 바꾸는 작업과 통째로 바꾸는 작업, 사려는 제품과 원인·방법을 알려는 글도 diff 또는 cover 로 보고 same 으로 하지 않는다.
쌍에서 B가 '글 제목'이고 답이 cover_b 이면, 그 제목에 A의 종류·부위·조건이 실제로 나오는지(동의어 포함: 드레스룸 = 옷장) 본다. 나오면 cover_b_named, 안 나오면 cover_b.
B가 '글 제목'이면 그 제목의 글 전체가 다루는 내용으로 본다. 제목에 여러 요소가 있어도 그 글이 해결하는 핵심으로 판단한다.
- 제목 B 에 붙은 [세부]가 원인·해결 수단·순서·시간(결로, 식초, 10분 코스, 원인별)이면 글의 범위를 좁히는 것이 아니다. 제목이 그 대상·문제 전체를 다루는 글이면 B 는 '넓은 글'이다.
  (예: B = '벽지 곰팡이 제거 후 또 피는 이유는 벽 속 결로예요' 는 벽 곰팡이 전체를 다루는 글이므로, A = 벽에곰팡이·곰팡이벽지제거 는 same.)
- 제목에 나열된 종류·조건('혈흔·기름·커피별', '원룸·욕실·옷장')이나 동의어가 A의 종류와 같으면 그 글이 A도 다루는 것이다: A 가 같은 범위면 same, A 가 그 나열의 한 항목(생리혈 ⊂ 혈흔, 드레스룸 = 옷장)이면 cover_b_named.
- 제품 고르기 글에서 쓰는 장소·상황(원룸, 신혼집, 겨울옷용)은 제품 종류를 좁히지 않는다: 제목에 그런 말이 있어도 그 제품을 고르는 일반 글로 보고 같은 범위(same)로 본다.
  제품의 형태·기능·용도(온오프, 절수, 가루, 드럼세탁기용, 아기용)를 특정한 글만 범위가 좁은 글이다.
- 증상을 해결하는 글(막힘·냄새·곰팡이·물때·얼룩·결로·청소·습기)은, 제목이 그 대상·문제 전체를 다루는 일반 글이면 부위·장소 변형(욕실 유리 물때, 싱크대 하부장 냄새)도 그 글 하나에서 해결되므로
  제목에 그 부위가 안 나와도 A 가 그 안의 한 부위면 cover_b_named 다. 제품 선택(구매)·교체·설치 글은 제목에 그 종류·부위가 실제로(동의어 포함) 나와야 cover_b_named, 안 나오면 cover_b.

[쌍]
{pairs}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명·코드블록 없이. 쌍 전부를 빠짐없이 적는다. 형식: {{"1": "same", "2": "cover_a", "3": "cover_b", "4": "cover_b_named", "5": "diff"}}
"""

PS_PROMPT = """너는 생활 살림 블로그의 편집자다. 아래 [항목]마다 다섯 가지를 답한다. 도구는 쓰지 말고 바로 답해라.
항목은 검색 키워드다. 각 항목에는 [지금 판정]이 붙어 있다(대상|문제).

1) product: 이 키워드가 '사려는 물건·도구의 이름'인가(true/false).
 - true: 스프레이, 탈취제, 제거제, 방지제, 세제, 페인트, 필터, 커버, 트랩, 코브라, 수납장, 청소기처럼 물건 이름으로 끝나거나 물건 이름 그 자체인 키워드(발냄새스프레이, 축구화탈취제, 곰팡이방지페인트, 싱크대코브라, 베란다우수관커버, 세면대트랩).
   '곰팡이'·'냄새' 같은 문제 말이 들어 있어도 물건 이름이면 true 다.
 - false: 문제를 풀려는 행위·방법·증상·질문(신발발냄새제거, 장판곰팡이제거, 싱크대배수구막힘, 수전카트리지교체, 화장실벌레, 차량스티커제거, 청소하는법, 원인, 교체방법, 비용)이면 false. '제거·교체·청소·막힘·냄새' 처럼 행위나 증상으로 끝나면 false.
2) kind: product 가 true 일 때 제품 종류를 띄어쓰기 없는 명사 하나로(발냄새스프레이, 탈취제, 곰팡이방지페인트, 코브라, 우수관커버, 배수트랩). 색상·크기·브랜드·모델은 뺀다. false 면 null.
3) use: product 가 true 일 때 이 물건의 쓰임을 넷 중 하나로(글자 그대로). false 면 null.
 - "home": 집의 물·배수·수전·세탁·청소·냄새·곰팡이·습기·수납·차량 관리에 쓰는 소모품·부품·도구·생활용품 (절수샤워기헤드, 세탁기수전, 욕실필터, 샤워기호스세트, 절수수전, 세면대수전필터, 역류방지밸브, 탈취제, 수납장, 정리함)
 - "material": 자재·구조 재료·규격품 (ㄱ자앵글, 조립식앵글, 앵글 규격, 각파이프)
 - "furniture": 가구·가구 부품·프레임 (책상다리, 철제침대프레임, 소파다리)
 - "display": 진열·업소용·창고용 설비 (아크릴진열장, 진열대, 쇼케이스, 중량렉, 업소용 선반)
4) solves: product 가 true 이고 use 가 "home" 일 때 '이 제품이 푸는 살림 문제'. use 가 home 이 아니면 null.
 - 살림 문제란 집·차·옷·신발 살림에서 사람이 겪는 불편(냄새, 막힘, 곰팡이, 물때·얼룩, 습기, 누수, 동파, 해충, 보관·정리 등)이다.
 - 넓게 본다. use 가 home 이면 solves 는 항상 있다. 물·에너지를 아끼는 제품(절수샤워기헤드, 절수수전)도 물 쓰는 살림의 부품이다.
 - {{"text": "한 줄 설명", "target": "문제가 생기는 대상(띄어쓰기 없는 명사)", "problem": "아래 목록 중 하나"}}
   problem 목록: {problems}  (딱 맞는 게 없으면 가장 가까운 것을 고른다)
   target 은 [이미 쓰는 대상 이름]에 같은 물건이 있으면 그 글자 그대로 쓴다(신발, 욕실, 싱크대, 세탁기, 벽지 등).
   예) 발냄새스프레이 → {{"text": "신발에 밴 발냄새를 없앤다", "target": "신발", "problem": "냄새"}}, 싱크대코브라 → {{"text": "막힌 싱크대 배수구를 뚫는다", "target": "싱크대", "problem": "막힘"}},
   절수샤워기헤드 → {{"text": "샤워 물 사용량을 줄인다", "target": "욕실", "problem": "누수"}}.
5) brand: 키워드에 브랜드(회사) 이름이나 모델명이 들어 있으면 true. 다른 말이 함께 있어도(삼성공기청정기필터교체후리셋, 코웨이필터교체, 벤츠워셔액, 물먹는하마제습제) true 다.
   '드럼세탁기', '수입차', '홀아비냄새'처럼 물건·종류·증상을 가리키는 일반 낱말은 브랜드가 아니라 false 다(드럼세탁기고무패킹청소, 수입차워셔액, 홀아비냄새제거 → false).

[이미 쓰는 대상 이름]
{targets}

[항목]
{items}

[출력 규칙]
JSON 한 덩어리만 출력한다. 설명·코드블록 없이. 항목 전부를 빠짐없이, 항목 번호 그대로. 형식:
{{"items": {{"1": {{"product": true, "kind": "...", "use": "home", "solves": {{"text": "...", "target": "...", "problem": "..."}}, "brand": false}}, "2": {{"product": false, "kind": null, "use": null, "solves": null, "brand": false}}}}}}
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_target(t: str) -> str:
    """대상 이름 정리: 띄어쓰기 제거 + 표기 통일(plan_posts.norm 의 동의어 표)."""
    t = norm(str(t or ""))
    t = t.replace("자동차", "차량").replace("퇴치약", "퇴치제").replace("떨이개", "털이개").replace("세정제", "세제")
    return re.sub(r"[^0-9a-z가-힣]", "", t)


PLACES = ("욕실", "욕조", "싱크대", "세면대", "주방", "베란다", "차량", "세탁기", "변기", "현관", "옷장", "신발장", "드레스룸", "창문", "벽지")


def places_in(name: str) -> set:
    return {p for p in PLACES if p in name}


# 장소·설비 이름: 제품 이름(수도필터)을 이런 이름(수도)으로 합치면 제품과 설비가 한 대상이 되어 버린다.
FACILITIES = PLACES + ("수도", "수도관", "배관", "보일러", "하수구", "배수구", "정화조", "계량기", "창호", "방충망")


def alias_ok(a: str, c: str) -> bool:
    """별칭 a 를 대표 c 로 합쳐도 되는가: 서로 다른 장소가 붙은 이름은 합치지 않고, 장소가 붙은 이름을 장소 없는 짧은 이름(수전)으로 뭉뚱그리지도 않는다."""
    if c in FACILITIES and a != c:       # 대표가 장소·설비 이름이면 다른 이름을 그리로 합치지 않는다 (제품 ≠ 설비)
        return False
    pa, pc = places_in(a), places_in(c)
    if pa and pc and pa != pc:
        return False
    return not (pa and not pc and len(c) <= 2)


def clean_detail(d: str) -> str:
    d = re.sub(r"[^0-9a-z가-힣]", "", norm(str(d or "")).replace("자동차", "차량"))
    return d or GENERAL


def bigrams(s: str) -> set:
    s = norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def pair_key(a: str, b: str) -> str:
    return "\t".join(sorted([a, b]))


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class FingerprintIndex:
    def __init__(self, keyword_path=None, post_path=None, alias_path=None, verdict_path=None, fixed=None):
        self.kw_path = Path(keyword_path or KEYWORD_INDEX)
        self.post_path = Path(post_path or POST_INDEX)
        self.alias_path = Path(alias_path or TARGET_ALIAS)
        self.verdict_path = Path(verdict_path or PAIR_VERDICTS)
        self.keywords = _read(self.kw_path, {}).get("keywords") or {}
        self.posts = _read(self.post_path, {}).get("posts") or {}
        a = _read(self.alias_path, {})
        self.alias = a.get("alias") or {}
        self.alias_seen = set(a.get("seen") or [])
        self.verdicts = _read(self.verdict_path, {}).get("pairs") or {}
        self.calls, self.cost, self.asked = 0, 0.0, 0
        # 정답 세트의 쌍은 운영 쌍 기록(기본 경로)에 고정값(fixed)으로 미리 넣는다. 채점용 기록(경로를 따로 준 경우)에는 넣지 않는다.
        if (verdict_path is None) if fixed is None else fixed:
            self.load_fixed()
        self.pair_calls, self.alias_calls, self.type_calls, self.ps_calls = 0, 0, 0, 0
        # 한 번 실행에서 쌍 확인(Claude) 호출 상한. None 이면 제한 없음. 넘으면 남은 쌍은 묻지 않고 capped_kw 에 키워드를 모은다.
        self.pair_budget = None
        self.cap_hit, self.cap_skipped_pairs, self.capped_kw = False, 0, set()

    # ---------- 항목 접근 ----------

    @staticmethod
    def _cur(e) -> bool:
        if not e:
            return False
        if e.get("ver") == PROMPT_VERSION:
            return True
        return e.get("ver") in KEEP_VERSIONS and not PEST_RE.search(e.get("text") or "")

    def tgt(self, e: dict) -> str:
        raw = spelling.respell(e.get("target_raw") or "")      # 샷시·샤시·새시 같은 표기 흔들림은 같은 대상
        return self.alias.get(raw, raw)

    def group_e(self, e: dict) -> str:
        return f"{self.tgt(e)}|{e['problem']}"

    def fp_e(self, e: dict) -> str:
        return f"{self.group_e(e)}|{e.get('detail') or GENERAL}"

    def entry(self, key: str, kind: str = "k"):
        e = (self.keywords if kind == "k" else self.posts).get(key)
        return e if self._cur(e) else None

    def fp_of(self, keyword: str):
        e = self.entry(keyword)
        return self.fp_e(e) if e else None

    def group_of(self, keyword: str):
        e = self.entry(keyword)
        return self.group_e(e) if e else None

    def target_of(self, keyword: str):
        e = self.entry(keyword)
        return self.tgt(e) if e else None

    def topic_group_of(self, keyword: str):
        e = self.entry(keyword)
        return (e or {}).get("topic")

    @staticmethod
    def is_brand(e: dict, k: str) -> bool:
        """제품 판정이 브랜드·판매처·회사명·모델명 키워드로 본 것(후기·비교·단점 글은 예외)."""
        return bool(e) and e.get("brand") is True and not any(w in k.replace(" ", "") for w in BRAND_EXEMPT)

    def type_of(self, keyword: str):
        e = self.entry(keyword)
        if e and self.is_brand(e, keyword):
            return "brand"          # 브랜드 판정이 유형 판정보다 우선(유형 판정이 나중에 돌아도 덮어쓰지 못함)
        return (e or {}).get("kw_type")

    def track_of(self, keyword: str):
        """갈래: 지문의 문제가 '제품 선택(구매)' 이면 money(수익글), 그 외는 info(정보글). 색인에 없으면 None."""
        e = self.entry(keyword)
        return None if not e else ("money" if (e.get("problem") == MONEY_PROBLEM or e.get("product") is True) else "info")

    def product_of(self, keyword: str):
        e = self.entry(keyword)
        return (e or {}).get("product")

    def solves_of(self, keyword: str):
        """이 제품이 푸는 살림 문제 {"text","target","problem"} 또는 None(문제를 푸는 제품이 아님·아직 판정 안 됨)."""
        e = self.entry(keyword)
        return (e or {}).get("solves")

    def detail_of(self, keyword: str):
        e = self.entry(keyword)
        return (e or {}).get("detail") if e else None

    def problem_of(self, keyword: str):
        e = self.entry(keyword)
        return (e or {}).get("problem") if e else None

    def post_track(self, e: dict) -> str:
        return "money" if e.get("problem") == MONEY_PROBLEM else "info"

    def post_fps(self, keys=None) -> dict:
        """{지문(3칸): [글 제목..]}. keys 를 주면 그 글들만."""
        out = {}
        for k, e in self.posts.items():
            if keys is not None and k not in keys:
                continue
            if self._cur(e):
                out.setdefault(self.fp_e(e), []).append(e.get("title") or k)
        return out

    def record_meta(self, keyword: str, **meta) -> None:
        e = self.keywords.get(keyword)
        if e is None:
            return
        for k, v in meta.items():
            if k == "kw_type" and self.is_brand(e, keyword):
                continue            # 브랜드 판정은 덮어쓰지 않는다
            if v is not None:
                e[k] = v

    def save(self) -> None:
        for d in (self.keywords, self.posts):
            for e in d.values():
                if self._cur(e):
                    e["target"], e["group"], e["fingerprint"] = self.tgt(e), self.group_e(e), self.fp_e(e)
        for path, key, data in ((self.kw_path, "keywords", self.keywords), (self.post_path, "posts", self.posts)):
            write_text_retry(path, json.dumps({"updated_at": now_iso(), "count": len(data), key: data},
                                              ensure_ascii=False, indent=1) + "\n")

    def save_alias(self) -> None:
        write_text_retry(self.alias_path, json.dumps({"updated_at": now_iso(), "alias": self.alias, "seen": sorted(self.alias_seen)},
                                                     ensure_ascii=False, indent=1) + "\n")

    def save_verdicts(self) -> None:
        write_text_retry(self.verdict_path, json.dumps({"updated_at": now_iso(), "count": len(self.verdicts), "pairs": self.verdicts},
                                                       ensure_ascii=False, indent=1) + "\n")

    # ---------- 지문 판정 (새 항목만) ----------

    def _reuse_lists(self, texts: list) -> tuple:
        """이번 묶음의 글자와 비슷한 기존 대상·세부·주제 이름(보기). 글자 2개 조각이 겹치는 순서로."""
        tg, dt, tp = {}, {}, {}
        for d in (self.posts, self.keywords):
            for e in d.values():
                if not self._cur(e):
                    continue
                tg[self.tgt(e)] = tg.get(self.tgt(e), 0) + 1
                if e.get("detail") and e["detail"] != GENERAL:
                    dt[e["detail"]] = dt.get(e["detail"], 0) + 1
                if e.get("topic"):
                    tp[e["topic"]] = tp.get(e["topic"], 0) + 1
        bg = set()
        for t in texts:
            bg |= bigrams(t)

        def pick(names: dict, limit: int) -> list:
            scored = [(-len(bigrams(n) & bg), -c, n) for n, c in names.items() if bigrams(n) & bg]
            return [n for _, _, n in sorted(scored)[:limit]]
        return pick(tg, 60), pick(dt, 40), pick(tp, 30)

    def _call(self, prompt: str, kind: str = "fingerprint"):
        last = "알 수 없음"
        for attempt in range(2):
            try:
                env = call_claude(prompt, model_for(kind), MAX_TURNS)
            except UsageLimitError:
                raise
            except RuntimeError as e:
                last = (str(e).splitlines() or [""])[0][:200]
                continue
            self.calls += 1
            self.cost += env.get("total_cost_usd") or 0
            try:
                return extract_json(env.get("result", ""))
            except (ValueError, TypeError) as e:
                last = "응답을 읽지 못함: " + (str(e).splitlines() or [""])[0][:150]
        raise FingerprintError(last)

    def _ask(self, texts: list) -> list:
        rt, rd, rp = self._reuse_lists(texts)
        prompt = PROMPT.format(problems=" / ".join(PROBLEMS), targets=", ".join(rt) or "(없음)", details=", ".join(rd) or "(없음)",
                               topics=", ".join(rp) or "(없음)", items="\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)))
        last = "알 수 없음"
        for attempt in range(2):
            raw = self._call(prompt)
            try:
                items = raw["items"]
                out, bad = [], []
                for i in range(len(texts)):
                    it = items.get(str(i + 1)) if isinstance(items, dict) else None
                    tgt = clean_target((it or {}).get("target"))
                    prob = str((it or {}).get("problem") or "").strip()
                    if not tgt or prob not in PROBLEMS:
                        bad.append(i + 1)
                    out.append({"target_raw": tgt, "problem": prob, "detail": clean_detail((it or {}).get("detail")),
                                "topic": str((it or {}).get("topic") or "").strip() or None})
                if bad:
                    raise ValueError(f"대상·문제가 빠졌거나 문제가 목록 밖인 항목 {bad[:8]}")
                return out
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                last = "응답 형식 오류: " + (str(e).splitlines() or [""])[0][:150]
        raise FingerprintError(last)

    def _fill(self, store: dict, todo: list, label: str, log=None) -> None:
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            res = self._ask([t for _, t, _ in chunk])
            for (key, text, extra), r in zip(chunk, res):
                old = store.get(key) or {}
                keep = {k: old[k] for k in ("kw_type", "type_ver", "category", "canonical") if old.get(k)}
                store[key] = {**extra, **keep, **r, "ver": PROMPT_VERSION, "judged_at": now_iso()}
            self.asked += len(chunk)
            self.save()
            if log:
                log(f"[지문 색인] {label} {len(chunk)}개 판정 → 저장 ({i + len(chunk)}/{len(todo)})")

    def ensure_posts(self, posts: list, log=None) -> int:
        """posts = [{"key", "text", "source"}]. 색인에 없는(또는 옛 버전) 글만 판정."""
        todo = [(p["key"], p["text"], {"title": p["text"], "source": p.get("source")}) for p in posts
                if not self._cur(self.posts.get(p["key"]))]
        if todo:
            self._fill(self.posts, todo, "기존 글", log)
        return len(todo)

    def ensure_keywords(self, keywords: list, log=None) -> int:
        todo = [(k, k, {"text": k}) for k in dict.fromkeys(keywords) if not self._cur(self.keywords.get(k))]
        if todo:
            self._fill(self.keywords, todo, "키워드", log)
        return len(todo)

    def ensure_product(self, keywords: list, log=None) -> int:
        """키워드가 '사려는 물건 이름'인지(product)·그 제품이 푸는 살림 문제(solves)를 색인에 저장. 아직 안 물은 것만 묻는다(지문은 다시 판정하지 않음).
        product 가 true 인데 지문의 문제가 '제품 선택(구매)'이 아니면 문제를 '제품 선택(구매)'로, 대상을 제품 종류(kind)로 고친다 → 갈래 money."""
        self.ensure_keywords(keywords, log)
        todo = [k for k in dict.fromkeys(keywords) if self.entry(k) and self.keywords[k].get("ps_ver") != PS_VERSION]
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            rt, _, _ = self._reuse_lists(chunk)
            lines = [f"{n + 1}. {k} [지금 판정: {self.tgt(self.keywords[k])}|{self.keywords[k]['problem']}]" for n, k in enumerate(chunk)]
            prompt = PS_PROMPT.format(problems=" / ".join(p for p in PROBLEMS if p not in NO_SOLVE_PROBLEMS),
                                      targets=", ".join(rt) or "(없음)", items="\n".join(lines))
            last, res = "알 수 없음", None
            for attempt in range(2):
                raw = self._call(prompt, "product")
                self.ps_calls += 1
                try:
                    items = raw["items"]
                    res = []
                    for n in range(len(chunk)):
                        it = items.get(str(n + 1)) if isinstance(items, dict) else None
                        if not isinstance(it, dict) or not isinstance(it.get("product"), bool):
                            raise ValueError(f"{n + 1}번 답이 없거나 product 가 true/false 가 아님")
                        res.append(it)
                    break
                except (KeyError, TypeError, ValueError, AttributeError) as e:
                    last, res = "응답 형식 오류: " + (str(e).splitlines() or [""])[0][:120], None
            if res is None:
                raise FingerprintError(last)
            for k, it in zip(chunk, res):
                e = self.keywords[k]
                sv = it.get("solves") if (it["product"] and it.get("use") == "home") else None   # 자재·가구 부품·진열·업소용(use 가 home 이 아님)은 수익글 조건 미달
                if isinstance(sv, dict):
                    pr = str(sv.get("problem") or "").strip()
                    sv = {"text": str(sv.get("text") or "").strip(), "target": clean_target(sv.get("target")) or None,
                          "problem": pr if pr in PROBLEMS and pr not in NO_SOLVE_PROBLEMS else None}
                else:
                    sv = None
                e["product"], e["solves"], e["ps_ver"] = it["product"], sv, PS_VERSION
                e["use"], e["brand"] = it.get("use"), it.get("brand") is True
                if self.is_brand(e, k) and e.get("kw_type") != "brand":
                    e["kw_type_before_brand"], e["kw_type"], e["type_ver"] = e.get("kw_type"), "brand", TYPE_VERSION   # 브랜드·모델명 키워드는 유형 brand 로 제외
                if it["product"] and e["problem"] != MONEY_PROBLEM:
                    kind = clean_target(it.get("kind"))
                    e["problem_before_product"], e["problem"], e["detail"] = e["problem"], MONEY_PROBLEM, GENERAL
                    if kind:
                        e["target_raw_before_product"], e["target_raw"] = e.get("target_raw"), kind
            self.save()
            if log:
                log(f"[지문 색인] 제품 여부·해결 문제 {len(chunk)}개 판정 → 저장 ({i + len(chunk)}/{len(todo)})")
        return len(todo)

    def ensure_types(self, keywords: list, log=None) -> int:
        """업체형(service)·주제 밖(offtopic) 등 유형(kw_type)을 색인에 저장. 색인에 유형이 없는 키워드만 keyword_classify 로 판정."""
        import keyword_classify as kc
        self.ensure_keywords(keywords, log)
        todo = [k for k in dict.fromkeys(keywords) if self.entry(k)
                and (not self.keywords[k].get("kw_type") or self.keywords[k].get("type_ver") != TYPE_VERSION)]
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            for attempt in range(3):          # 응답 형식이 깨지면 이 덩어리만 다시 묻는다
                res = kc.classify(chunk)
                self.type_calls += 1
                self.cost += ((res.get("usage") or {}).get("total_cost_usd") or 0)
                if res["status"] == "ok":
                    break
            if res["status"] != "ok":
                raise FingerprintError("유형 분류 실패: " + str(res.get("error")))
            for k in chunk:
                self.keywords[k]["kw_type"], self.keywords[k]["type_ver"] = res["types"][k], TYPE_VERSION
                if self.is_brand(self.keywords[k], k):
                    self.keywords[k]["kw_type_before_brand"], self.keywords[k]["kw_type"] = res["types"][k], "brand"
            self.save()
            if log:
                log(f"[지문 색인] 유형(업체형·주제 밖 등) {len(chunk)}개 판정 → 저장 ({i + len(chunk)}/{len(todo)})")
        return len(todo)

    # ---------- 대상 이름 정리표 ----------

    def _all_targets(self) -> list:
        return sorted({e["target_raw"] for d in (self.posts, self.keywords) for e in d.values()
                       if self._cur(e) and e.get("target_raw")})

    def ensure_alias(self, log=None) -> int:
        """색인에 있는 대상 이름 중 아직 정리표에서 본 적 없는 것만 Claude 에 묻는다(처음에는 전체). → 새로 본 이름 수"""
        names = self._all_targets()
        new = [n for n in names if n not in self.alias_seen]
        if not new:
            return 0
        canon = sorted(set(self.alias.get(n, n) for n in self.alias_seen))
        if canon and len(new) < len(names):
            bg = set()
            for n in new:
                bg |= bigrams(n)
            known = [c for c in canon if bigrams(c) & bg][:120]
            known_txt = "\n[이미 정리된 대표 이름 (새 이름이 이 중 하나와 같은 물건이면 그 이름을 대표로 쓴다)]\n" + ", ".join(known) + "\n" if known else ""
            ask_names = new + known
        else:
            known_txt, ask_names = "", new
        ask_names = list(dict.fromkeys(ask_names))
        last = "알 수 없음"
        for attempt in range(2):
            raw = self._call(ALIAS_PROMPT.format(known=known_txt, names=", ".join(ask_names)))
            self.alias_calls += 1
            try:
                groups = raw["groups"]
                if not isinstance(groups, list):
                    raise ValueError("groups 가 목록이 아님")
                break
            except (KeyError, TypeError, ValueError) as e:
                last = "응답 형식 오류: " + str(e)[:100]
        else:
            raise FingerprintError(last)
        added = 0
        nameset = set(ask_names)
        for g in groups:
            if not isinstance(g, dict):
                continue
            c = clean_target(g.get("name"))
            for a in g.get("aliases") or []:
                a = clean_target(a)
                if c in nameset and a in nameset and a != c and alias_ok(a, c):
                    self.alias[a] = self.alias.get(c, c)
                    added += 1
        # 연쇄 정리: 대표가 다른 대표의 별칭이 되었으면 끝까지 따라간다
        for a in list(self.alias):
            c, seen = self.alias[a], {a}
            while c in self.alias and c not in seen:
                seen.add(c)
                c = self.alias[c]
            self.alias[a] = c
        self.alias_seen |= set(names)
        self.save_alias()
        self.save()
        if log:
            log(f"[지문 색인] 대상 이름 정리: 새로 본 이름 {len(new)}개, 새 별칭 {added}개 (정리표 별칭 총 {len(self.alias)}개)")
        return len(new)

    # ---------- 쌍 판정 ----------

    def close(self, ea: dict, eb: dict) -> bool:
        """가까운 쌍인가: 묶음이 같고 세부만 다름 / 문제가 같고 대상 이름 한쪽이 다른 쪽을 포함 / 한쪽 세부가 '일반'(앞의 경우에 해당) / 해충 제품↔퇴치."""
        ta, tb = self.tgt(ea), self.tgt(eb)
        if ea["problem"] != eb["problem"]:
            if {ea["problem"], eb["problem"]} != {"제품 선택(구매)", "해충 퇴치"}:
                return False
            short, long_ = sorted((ta, tb), key=len)       # 바퀴벌레 ⊂ 바퀴벌레약: 이름을 어디까지 쓰느냐는 판정마다 흔들리므로 포함도 가까운 쌍
            return ta == tb or (len(short) >= 2 and short in long_)
        if ta == tb:
            return (ea.get("detail") or GENERAL) != (eb.get("detail") or GENERAL)
        short, long_ = sorted((ta, tb), key=len)
        # 대상 이름 한쪽이 다른 쪽을 포함(패딩 ⊂ 구스다운패딩). 한 글자 이름(벽 ⊂ 벽지)은 앞글자가 같을 때만.
        return (len(short) >= 2 and short in long_) or (len(short) == 1 and long_.startswith(short))

    @staticmethod
    def text_of(e: dict, key: str) -> str:
        return e.get("title") or e.get("text") or key

    def load_fixed(self, eval_path=None) -> int:
        """정답 세트(tests/eval_set.json)의 쌍을 고정 답(fixed)으로 넣는다. 모델이 저장해 둔 답보다 고정 답이 이긴다."""
        ev = _read(Path(eval_path or BASE / "tests" / "eval_set.json"), {})
        pairs = [(c["keyword"], c["existing"], c["same"]) for c in (ev.get("dup_existing") or {}).get("cases", [])]
        pairs += [(c["a"], c["b"], c["same"]) for c in (ev.get("same_article") or {}).get("cases", [])]
        n = 0
        for a, b, same in pairs:
            k = pair_key(a, b)
            if (self.verdicts.get(k) or {}).get("fixed") and self.verdicts[k]["same"] == same:
                continue
            self.verdicts[k] = {"v": "same" if same else "diff", "same": bool(same), "a": a, "b": b, "fixed": True, "judged_at": now_iso()}
            n += 1
        if n:
            self.save_verdicts()
        return n

    def verdict_v(self, ta: str, tb: str):
        """저장된 쌍 답 → ta 기준 'same'|'diff'|'cover_a'(ta 가 넓음)|'cover_b'(tb 가 넓음)|'cover_b_named'. 없으면 None."""
        v = self.verdicts.get(pair_key(ta, tb))
        if not v or (not v.get("fixed") and v.get("ver") != PAIR_VERSION):
            return None
        code = v.get("v") or ("same" if v.get("same") else "diff")
        if v.get("a") != ta and code in ("cover_a", "cover_b", "cover_b_named"):      # 물었을 때와 순서가 바뀜
            code = {"cover_a": "cover_b", "cover_b": "cover_a", "cover_b_named": "cover_a"}[code]
        return code

    @staticmethod
    def is_same(code: str, bp: bool) -> bool:
        """같은 글(중복)인가. 후보끼리는 same 만. 기존 글(bp=True)은 same, 또는 기존 글이 넓고 제목에 그 종류·부위가 나오는 경우(cover_b_named)."""
        return code == "same" or (bp and code == "cover_b_named")

    def code_of(self, ea: dict, ta: str, eb: dict, tb: str) -> str:
        """'same'|'diff'|'cover_*'|'ask'(가까운 쌍인데 아직 확정 안 됨)"""
        if self.fp_e(ea) == self.fp_e(eb):
            return "same"
        if not self.close(ea, eb):
            return "diff"
        return self.verdict_v(ta, tb) or "ask"

    def relation(self, ea: dict, ta: str, eb: dict, tb: str, bp: bool = False) -> str:
        """'same' | 'diff' | 'ask'"""
        c = self.code_of(ea, ta, eb, tb)
        return "ask" if c == "ask" else ("same" if self.is_same(c, bp) else "diff")

    def _ask_pairs(self, pairs: list, log=None) -> None:
        """pairs = [(textA, textB, B가 글 제목인가, 메모A, 메모B)]. 60쌍씩 물어 확정 기록을 저장한다."""
        for i in range(0, len(pairs), BATCH):
            if self.pair_budget is not None and self.pair_calls >= self.pair_budget:     # 쌍 확인 호출 상한: 남은 쌍은 묻지 않는다
                self.cap_hit = True
                self.cap_skipped_pairs += len(pairs) - i
                if log:
                    log(f"[쌍 확인 상한] 호출 {self.pair_calls}회(상한 {self.pair_budget}) → 남은 쌍 {len(pairs) - i}개는 이번에 묻지 않음")
                return
            chunk = pairs[i:i + BATCH]
            lines = [f"{n + 1}. A(검색어): {a} [{ma}] | B({'글 제목' if bp else '검색어'}): {b} [{mb}]" for n, (a, b, bp, ma, mb) in enumerate(chunk)]
            last = "알 수 없음"
            for attempt in range(2):
                raw = self._call(PAIR_PROMPT.format(pairs="\n".join(lines)), "pair")
                self.pair_calls += 1
                try:
                    verdicts = [str(raw[str(n + 1)]).strip().lower() for n in range(len(chunk))]
                    if any(v not in ("same", "diff", "cover_a", "cover_b", "cover_b_named") for v in verdicts):
                        raise ValueError("허용한 답이 아닌 것이 있음")
                    break
                except (KeyError, TypeError, ValueError) as e:
                    last = "응답 형식 오류: " + str(e)[:100]
            else:
                raise FingerprintError(last)
            for (a, b, bp, ma, mb), v in zip(chunk, verdicts):
                if v == "cover_b_named" and not bp:
                    v = "cover_b"
                if (self.verdicts.get(pair_key(a, b)) or {}).get("fixed"):
                    continue
                self.verdicts[pair_key(a, b)] = {"v": v, "same": v == "same", "a": a, "b": b, "ver": PAIR_VERSION, "judged_at": now_iso()}
            self.save_verdicts()
            if log:
                log(f"[지문 색인] 가까운 쌍 {len(chunk)}개 확인 → 저장 ({i + len(chunk)}/{len(pairs)})")

    def resolve_codes(self, items: list, log=None) -> list:
        """items = [(entryA, textA, entryB, textB, B가 글 제목인가)] → 각 쌍의 답 코드. 가까운 쌍은 필요하면 한꺼번에 묻는다."""
        codes = [self.code_of(ea, ta, eb, tb) for ea, ta, eb, tb, _ in items]
        ask = {}
        for (ea, ta, eb, tb, bp), c in zip(items, codes):
            if c == "ask":
                ask.setdefault(pair_key(ta, tb), (ta, tb, bp, f"대상 {self.tgt(ea)}·세부 {ea.get('detail') or GENERAL}",
                                                  f"대상 {self.tgt(eb)}·세부 {eb.get('detail') or GENERAL}"))
        if ask:
            self._ask_pairs(list(ask.values()), log)
            codes = [self.code_of(ea, ta, eb, tb) for ea, ta, eb, tb, _ in items]
        return codes

    def resolve(self, items: list, log=None) -> list:
        """→ [같은 글인가(bool)]"""
        return [self.is_same(c, it[4]) for c, it in zip(self.resolve_codes(items, log), items)]


# ---------- 기존 글 목록 / 판정 도우미 ----------

def existing_post_list(existing: list, w: dict, prev_keywords=None) -> list:
    """existing_keywords.json 의 items(+지난 기획 키워드) → 색인에 넣을 글 목록. 인포랩스·지원금 대기열(dedup_skip_sites)은 뺀다."""
    skip = set(w.get("dedup_skip_sites") or [])
    posts, seen = [], set()
    for e in existing:
        if e.get("site") in skip:
            continue
        t = " ".join(str(e.get("title") or e.get("focus_keyword") or "").split())
        key = norm(t)
        if t and key not in seen:
            seen.add(key)
            posts.append({"key": t, "text": t, "source": e.get("site") or e.get("platform")})
    for k in prev_keywords or []:
        t = f"(지난 기획) {k}"
        if norm(k) not in seen:
            seen.add(norm(k))
            posts.append({"key": t, "text": t, "source": "plan"})
    return posts


def dup_vs_posts(idx: FingerprintIndex, keywords: list, post_keys, log=None) -> dict:
    """키워드 → 같은 글(중복)인 기존 글 제목. 지문 3칸 일치 → 같은 글, 가까운 쌍은 same / cover 로 확인, 그 외 다른 글.
    기존 글이 넓고 제목에 그 종류·부위가 나오면(cover_b_named) 중복, 키워드가 더 넓으면 중복이 아니다."""
    posts = [(k, idx.posts[k]) for k in post_keys if idx.entry(k, "p")]
    found, pend = {}, []
    for kw in keywords:
        ek = idx.entry(kw)
        if not ek:
            continue
        fk = idx.fp_e(ek)
        for pk, ep in posts:
            if idx.fp_e(ep) == fk:
                found[kw] = ep.get("title") or pk
                break
        else:
            for pk, ep in posts:
                if idx.close(ek, ep):
                    pend.append((kw, ek, pk, ep))
    if pend:
        items = [(ek, kw, ep, idx.text_of(ep, pk), True) for kw, ek, pk, ep in pend]
        codes = idx.resolve_codes(items, log)
        for (kw, ek, pk, ep), c in zip(pend, codes):
            if c == "ask":                      # 쌍 확인 상한 때문에 못 물은 쌍 → 이 키워드는 확인이 안 끝남
                idx.capped_kw.add(kw)
            elif idx.is_same(c, True) and kw not in found:
                found[kw] = ep.get("title") or pk
        for kw in found:
            idx.capped_kw.discard(kw)           # 이미 같은 글로 판명 난 키워드는 확인이 끝난 것
    return found


def cluster_and_cover(idx: FingerprintIndex, keywords: list, log=None) -> tuple:
    """후보끼리 → (같은 글 묶음 [[대표, 붙은 것..]] 2개 이상만, 포함 관계 [(넓은 것, 좁은 것)]).
    keywords 는 우선순위(검색량 큰 순) 순서로 준다. 묶음은 '직접 same 판정이 난 쌍'으로만 만든다:
    앞에서부터 보며, 이미 대표인 것 중 자기와 직접 same 인 첫 대표에 붙고, 없으면 자기가 대표가 된다(A=B, B=C 여도 A=C 로 잇지 않는다)."""
    ents = [(k, idx.entry(k)) for k in dict.fromkeys(keywords)]
    ents = [(k, e) for k, e in ents if e]
    same_of = {k: set() for k, _ in ents}
    pend = []
    for i in range(len(ents)):
        for j in range(i + 1, len(ents)):
            (ka, ea), (kb, eb) = ents[i], ents[j]
            if idx.fp_e(ea) == idx.fp_e(eb):
                same_of[ka].add(kb)
                same_of[kb].add(ka)
            elif idx.close(ea, eb):
                pend.append((ea, ka, eb, kb, False))
    cover = []
    if pend:
        for (ea, ka, eb, kb, _), c in zip(pend, idx.resolve_codes(pend, log)):
            if c == "same":
                same_of[ka].add(kb)
                same_of[kb].add(ka)
            elif c == "cover_a":
                cover.append((ka, kb))
            elif c in ("cover_b", "cover_b_named"):
                cover.append((kb, ka))
    reps, members = [], {}
    for k, _ in ents:
        rep = next((r for r in reps if r in same_of[k]), None)
        if rep is None:
            reps.append(k)
            members[k] = [k]
        else:
            members[rep].append(k)
    return [m for m in members.values() if len(m) > 1], cover


def cluster_exact(idx: FingerprintIndex, keywords: list) -> list:
    """탐색 단계용(Claude 호출 없음): 지문 3칸이 같은 것만 같은 글로 묶는다. keywords 는 검색량 큰 순 → 첫 번째가 대표. 2개 이상 묶음만."""
    members = {}
    for k in dict.fromkeys(keywords):
        fp = idx.fp_of(k)
        if fp:
            members.setdefault(fp, []).append(k)
    return [m for m in members.values() if len(m) > 1]


def cluster_keywords(idx: FingerprintIndex, keywords: list, log=None) -> list:
    """후보끼리 같은 글인 묶음 [[대표, 붙은 것..]] (2개 이상만). 직접 same 판정만 쓴다(사슬 금지)."""
    return cluster_and_cover(idx, keywords, log)[0]
