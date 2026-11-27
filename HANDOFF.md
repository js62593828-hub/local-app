## 완료 (2026-11-27, PC: 설성목장): 전 채널 토큰 줄이기
- 원인(11/08~27 이 PC 기록): 살림노트 원고 1편 약 100만(데스크피커 12만), 검사 도구 소스·다른 원고 통째 읽기·머릿속 초안, 150k 넘는 긴 대화, 상세 이미지를 일반 에이전트가 읽음.
- 적용: 새 에이전트 fact-checker(sonnet, 사실 확인 메모) → 작가는 메모만 보고 씀 / 새 도구 tools/draft_digest.py(원고 겹침 요약), tools/review_compact.py(후기 압축) / 작가 4종·후기 요약·혜택주간 스킬에 '토큰 아끼기' / CLAUDE.md 규칙 / .claude/settings.json(자동 압축 200k, 명령 출력 2만 자) / collect_research.py 는 새로 받는 큰 상품 사진을 가로 1600 JPG 로 저장.
- 아직 실제 원고로는 안 돌려 봄. 다음 살림노트 1편에서 토큰·품질을 전과 비교할 것. 설정·에이전트는 새 대화부터 적용.
- 사용자 승인 대기: 11/23 주 조사 사진 삭제(약 390~420MB, 11/29 발행 확인 뒤) / 남길 조사 사진 다시 압축(약 170MB, 원본 덮어씀) / push.

## 완료 (2026-11-27, PC: 설성목장): 용량 정리(5.4GB → 1.75GB) + 이미지 저장 용량 줄이기
- 지움(약 3.7GB): 11/30 주 데스크피커 상세 이미지 원본·조각, preview/ 시험 파일, 시험용 데이터 복사본, 옛 기획 시험 캐시, naver_tips_keep 의 중복 사진, 옛 데이터 백업(종류별 최신 1개 남김), __pycache__. 발행용 사진(static/generated)·상품 사진(_img)·글 데이터는 그대로.
- 남긴 상세 이미지: '캡처' 자리가 있는 11/30 주 글 13편의 상품 원본(GIF 는 지정 번호만)과 원고가 적은 조각 7개. 캡처·발행이 끝나면 `python tools/shrink_detail.py pipeline/candidates_run/20261130_radar_naver_plan --clean --go` (사용자에게 먼저 묻기).
- 설정: AI 그림 JPG 90→82, 레이더 표지·카드 92→85, 표·제품 94→88, 업로드 폴더 90→85, 600KB 넘는 사진은 칸에 고를 때 가로 1600 JPG 로. 새 도구 tools/shrink_detail.py, CLAUDE.md '이미지 용량 규칙'. 앱은 다시 켜야 적용.
- git: 이 PC 에만 커밋, push 안 함(다른 PC 의 미push 커밋 때문). 작업 전 8763d11 은 드라이브로 넘어온 다른 PC 수정분.

## 이어갈 것 (2026-11-26): W싼세계 11/30 주 기획 2차안 — 사용자 제품 고르기 대기
- 사용자 지시: 제품 하나 = 글 하나(다시 쓰지 않음), 지난주·기존 글과 소재 겹침 금지. tools/wp_rules.md v1.8 과 wp-writer.md 에 반영(docs 사본 있음). W싼세계 프로젝트의 지침 원본은 사용자가 바꿔야 함. wp_inbox_build 의 재사용 경고는 옛 기준(3편까지) 그대로.
- 만든 것: pipeline/wp_plan_run/20261130/기획표.md(21편·제품 28개), pipeline/wp_inbox/2026-11-30/_주간계획.txt·_빈양식_제품표.tsv(1차안은 _1차안/). wp_inbox_build 확인: 21줄 읽힘.
- 다음: 사용자가 제품표 채움(11/30 분 4개 먼저, 파일 이름 _제품표_수집날짜.tsv) → 7번째 칸 필수 표기정보는 _추가자료 로 옮기고 새 브랜드는 _공식사양.json → python tools/wp_inbox_build.py --week 2026-11-30 → D 7줄 키워드를 모델명으로 → wp-writer(재사용 금지 알림).
- 안 한 것: 앱 대기열·글 데이터·코드, 구글 경쟁 확인, push. git reset 하지 말 것(origin 에 안 올라간 커밋 있음).

## 이어갈 것 (2026-11-25, 지시문 ⑨ 완료): 링크 읽기 고침 + 제목 2개 + 11/30 주 21편 앱 넣기(--apply) 끝
- radar_screen.py: list_runs 가 _radar_naver_plan 폴더도 읽음(폴더 날짜 최신 우선), load_naver_links 는 상태 '실패' 건너뜀. 11/30 주 상품카드 60개 모두 링크 있음(plan links.json 과 같음), 11/23 주 38개 전후 같음. 커밋 34ae50a.
- 제목 바꿈: 1205_3 → '니케 헤드폰 거치대 원목, 회전 걸이형과 다른 점', 1206_1 → '탁상시계 추천, 표시 방식·전원 방식 기준으로 고른 3가지'(세 제품 research 에 모두 표시 방식·전원 방식 근거). 원고 3편 고침(니케·탁상시계·USB허브 1204_1 옵션 문장). plan.md 도 같이 고침, plan.json·plan_push.json 은 안 고침(옛 제목 남음).
- radar_import --apply 21편 넣음(articles_radar_naver.json 89→110, 다른 항목 변화 없음, 대기열 JSON 변화 없음). 백업: *.backup_before_radar_import_apply_20261125, *.backup_before_title2_20261125, pipeline/radar_drafts/_backup/before_title2_20261125/
- 남은 일: 앱 화면에서 21편 최종본 만들기·확인 → 사용자가 네이버 예약 발행. 링크 발급·push 는 안 함.

## 완료 (2026-11-25, 지시문 ⑧): 11/30 주 21편 독립 검수 + radar_import 미리보기
- 서브에이전트 6개로 17편 숫자 대조(값 불일치 0, 표현·근거 없는 문장 수정). radar_check 위반 0(경고 5: 사진 6장·글자 수 4편, 노르잇 1569자). 미리보기 21편 모두 대기열 짝지음.
- 앱(radar_screen.py)은 이름이 _radar_naver 로 끝나는 폴더의 links.json 만 읽음 → 20261130_radar_naver_plan 은 안 읽힘(사용자 결정 필요). 안 한 것: apply, 링크 발급, push, 커밋.

## 완료 (2026-11-25, 지시문 ⑦): 대기열 제목 3개 + 원고 4편 수정 + 원고 16편
- 대기열 제목 3개 바꿈(11/30 B6 필립스, 12/4 A5 USB 허브, 12/4 B2 노르잇). 백업: content_queue_radar_naver.backup_before_title3_20261125.json, pipeline/stage_plan1130_run/20261123/plan.backup_before_title3_20261125.md. plan.md 표 제목·글자 수도 같이 바꿈.
- 원고 21편(pipeline/radar_drafts/2026-11-30/) radar_check 위반 0·경고 0. 1201_2 데스커 받침대도 사용자 요청으로 11/25 작성(깊이 220mm 사용자 확인값).
- 사용자 확정(11/25): 데스커 받침대 깊이(앞뒤 폭) 220mm, 서랍 폭과 같고 전체 옵션 공통. 1201_2 원고는 이 값으로 작성 완료(research 파일은 안 고침).
- 안 한 것: --apply(radar_import·push_plan), 링크 발급, 블로그 글쓰기, push.

## 최신 (2026-11-25 집 PC): origin HANDOFF 합치기 — 남은 일만 (출처: docs/archive/2026-11/HANDOFF_origin_20261125.md, 1,099줄 통째로 보관)
- N살림노트 11/30 주 19편: 앱 넣기 끝(text·images·final 오류 0). 남은 일 = 사용자가 네이버에서 예약 발행. 업로드 폴더 '예약 시각'은 원고 파일 이름에만 있음(origin 최신 커밋에 예약 시각 표 있음).
- 최종본 경고 1건: 02 눈삽 별점 상자(평점 5.04). RSS 로 주소가 채워진 다른 글 1건(2026-11-23_abbf7757 원목 서랍장).
- 실제 넣기 명령·백업 위치는 보관 파일 맨 위 두 항목 참고. 그 외 origin 기록은 모두 끝난 일이고 HANDOFF_old.md 에도 있음.

## 진행 중 (2026-11-25 집 PC, 지시문 ⑥): git 정리 — reset origin/main(mixed) 직전 기록
- 로컬 main 에서 빠지는 커밋(origin 에 없음): cb1d49e 원고지침 v3.7 원본 그대로 덮어쓰기 / fde4202 토큰 줄이기: radar-writer 읽을 범위 한정, spec-reader 신설 / 88e9fa9 토큰 줄이기: 원고지침 v3.7, HANDOFF 최신 3개만 / 14c4d78 작업 전 정리 커밋: 미커밋 수정 일괄 (모두 [PC: TK-X1E])
- 로컬 최근 8: cb1d49e fde4202 88e9fa9 14c4d78 762c449 d07d774 3addef2 3873049
- origin 최근 8: 5c92a85 303c792 bcfe45b 28cb99d 27a89aa 479d4af 46d597a a8c444e (N살림노트 19편 앱 넣기 등)
- 다음: git reset origin/main → status·diff 확인 → 문제 없으면 add -A, 커밋, push.

## 완료 (2026-11-25 TK-X1E): 토큰 줄이기
- 원고지침 v3.7(기록·중복 삭제, radar_check 4편 전후 같음), CLAUDE.md·에이전트 정의 축소, 사양·요약 sonnet(spec-reader 신설). 결과·전후 표: pipeline/stage_token_run/20261125/report.md
- HANDOFF 옛 기록은 docs/archive/2026-11/HANDOFF_old.md 로 옮김(N살림노트 salim_import 실제 넣기 명령 포함).
- 안 한 것: 원고·research·앱 데이터 수정, apply, 링크 발급, 서브에이전트 호출.

## 진행 중 (2026-11-25): 지시문 ④ 사양 읽기 + 원고 (기록 갱신)
- 완료: 사양 읽기 14개(11/30·12/1 상품 + 채팅 5개) + 12/2 일부. 원고 4편 radar_check 위반 0(1130_1, 1130_3, 1201_1, 1201_3, pipeline/radar_drafts/2026-11-30/).
- 보류: 1130_2 필립스(상세 이미지엔 '오픈형' 없고 스펙 표는 밀폐형 → 제목 변경 필요), 1201_2 데스커 받침대(깊이 못 찾음, 590mm 옵션 치수 없음).
- 완료 (2026-11-25 집 PC, 지시문 ⑤): 남은 5개 사양 읽기 끝 → 48개 모두 '사양 (상세 이미지 확인, 11/25)' 단락 있음. 새로 읽은 5개: 805516356620971, 805557444813385, 808766946916292(조각이 너무 작아 숫자 못 읽음·옵션마다 제품 다름), 882709459477952, 917791240257696.
- 이 날 git 은 reset·commit·push 모두 안 함(회사 PC 커밋 3개가 이 PC에 없고 origin 과 갈라져 있음 — 정리는 따로). research/·_detail 은 커밋 안 함.

## 완료 (2026-11-25): 11/30 주 데스크피커 상세 이미지·글자·옵션 받기 48개 (지시문 ③)
- 위치: pipeline/candidates_run/20261130_radar_naver_plan/research/_detail/<상품id>/ (NN.확장자, detail_text.txt, options.txt, list.json). 데스커 공식 페이지분은 996928541510400/official/. 808731499075232 는 판매대기라 제외.
- 안 한 것: research/*.md·대기열·앱 데이터 수정, 이미지 내용 읽기, apply, push, 링크 발급. 옵션 파일의 배송지 줄은 지움.

