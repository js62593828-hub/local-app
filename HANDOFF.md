## 완료 (2026-11-22 11/30 주 대기열 21개 뺌)
- push_plan --undo-run-id kw_radar_naver_20261130 로 2026-11-30_ 항목 21개만 뺌(대기열 115→94, 글 json 변화 없음, 11/23 주 항목 그대로). 백업 content_queue_radar_naver·articles_radar_naver .backup_before_remove_1130_20261122.json. keyword_run/2026-11-30_radar_naver 폴더는 유지, README.txt '사용 안 함'.
- 할 일: 없음(11/30 주를 다시 쓰려면 push_plan --apply 로 다시 넣거나 키워드 단계부터 새로).

## 완료 (2026-11-22 Desk Picker 원고지침 v3.4 반영: 제품 한 번만·틀 B 카드 2번·태그 붙여쓰기)
- 규칙 tools/radar_rules.md = docs v3.4(cmp 같음). 장부 pipeline/radar_used_products.json(38줄·27모델, 다시 만들기 python tools/radar_used_products.py). 후보(product_candidates)·키워드(keyword_pipeline)에서 장부 제외, radar_check 에 m/n/o 위반 3가지, 앱 최종본 끝 카드는 배너+링크만(GIF·별점 띠 없음). 11/30 주 겹침표 pipeline/radar_used_run/20261122_11-30_겹침.md(모델 겹침 0, 같은 주 제품군 겹침 위험 9쌍).
- 할 일: 11/23 주 원고는 제품이 서로 겹치고(JBL GO5↔FLIP7 등 12편) 틀 B 끝 카드가 없어 radar_check 위반 48개 — 고치지 않음(사용자 판단). 11/30 주 원고 쓸 때 위 겹침표의 같은 주 위험 쌍 주의.

## 완료 (2026-11-22 혜택주머니 7차: 일괄 처리 화면 선택 버튼)
- /lt-batch 표 위에 '전체 선택'·'일괄 선택 해제'·'고른 글 n편' 줄, 표 머리 전체 체크박스(반쯤 표시), 고른 글 0편이면 발행 버튼 잠김. templates/lt_batch.html 한 파일만, 서버 요청 0. 캡처 pipeline/lt_batch_run/20261122/선택버튼_*.png
- 할 일: 없음.

## 완료 (2026-11-22 싼세계 1070 배너·별점 교체 + 일괄 화면 전체 선택)
- 예약 글 1070 본문(content 만)의 옛 배너·별점 줄 6곳 교체, 상태·날짜·카테고리·태그·slug·표지 그대로. 앱 최종본도 같은 6곳만. 일괄 처리 화면 머리 '전체 선택' 체크박스(0ecaa0b). reviewer 통과. 보고 pipeline/wp_setup_run/20261122_1070_보고.md
- 할 일: 없음(싼세계 배너 교체는 미발송 20편+1070 모두 새 모양).

## 완료 (2026-11-22 싼세계 배너 투명 PNG 교체 + 별점 줄 강조)
- 새 미디어 1071(.png, 투명 유지 확인)·wp_banner.json 교체·별점 줄 강조(shop_formatter)·미발송 20편 최종본 반영. reviewer 통과. 보고 pipeline/wp_setup_run/20261122_배너투명_보고.md
- 할 일: 이미 워드프레스로 보낸 예약 글 1070(2026-11-23_bd57a241)은 옛 .jpg 배너·옛 별점 모양 그대로 — 사용자 판단으로 처리.

## 완료 (2026-11-22 혜택주머니 주간 원고 일괄 처리 화면 6차)
- 메뉴 '📦 주간 원고 일괄 처리'(/lt-batch) 보기·시각 변경 저장·일괄 예약 발행 완성(lt_batch.py). 시험은 임시 사본·가짜 발행, 실제 발행 0회, reviewer 통과. 보고 pipeline/lt_batch_run/20261122_보고.md
- 할 일: 월요일 첫 1편만 일괄 예약 → 워드프레스에서 예약·카테고리·태그 확인 → 나머지. 워드프레스에 '생활요금·해지환불' 카테고리가 아직 없음(그 글은 일괄에서 빠짐).

## 완료 (2026-11-22 싼세계 카테고리·태그 미리 채우기 + 주간 일괄 처리 화면)
- 1부 최종본 카테고리·태그 칸 미리 채움(0a31745), 2·3부 '📦 주간 원고 일괄 처리' 화면과 일괄 예약 발행(a902bb1·84cf884). 시험은 전부 임시 복사본·목업, 실제 발행 0회, reviewer 통과. 보고 pipeline/wp_setup_run/20261122_일괄처리_보고.md
- 할 일: 사용자가 1편만 먼저 일괄 예약 → 워드프레스에서 예약·카테고리·태그 확인 → 나머지. 태그 101종 중 100개는 발행 때 새로 만들어짐.

## 완료 (2026-11-22 Desk Picker 링크 다시 받기 11/23 주 20편)
- 에고라이트 로그인=guardradar(데스크피커), 채널 1020008901769280 으로 새 링크 25개 받음(상품 27개 중 SMB35·AULA F108Pro 2개는 '판매대기·판매중 상품이 아님'으로 발급 불가). 결과 pipeline/stage_relink_run/20261122/relinks.md·relinks.json, 기존 links.json 해시 같음(백업 같은 폴더).
- 할 일: 사용자가 네이버에서 예약 글 링크 교체. issue_links.py·product_candidates.py 는 채널번호 997658497773120 고정+기존 링크 재사용이라 Desk Picker 용으로 쓰면 안 됨(고치지 않음).

## 이어갈 것 (2026-11-22 싼세계 앱수정 v12 1~9단계 끝)
- 배너 올림(미디어 1062)·링크 블록 모양·21편 최종본 배너 반영·inbox 버그·예약 +3시간(11/29 미니 온풍기 15:36)·결과 파일 덧붙이기·예약칸 미리채움·카테고리 정리 적용. reviewer 통과. 보고 pipeline/wp_setup_run/20261122_앱수정_보고.md
- 할 일: research/iPEjUxpELA 필수 표기정보 0개(고친 도구로는 7개) — 1126 S 원고 확인. 발행·예약 버튼은 사용자가 누름.

## 이어갈 것 (2026-11-22 싼세계 보류 2편 마무리): 1128 D·1129 S 앱에 넣음(대기열 122→124, 글 109→111), 보류 없음
- 새 후기 반영해 1128 D 다시 씀(22개 기준)·1129 S 숫자 고침(18개 기준), 둘 다 위반 0. 보고 pipeline/wp_setup_run/20261122_보류2편_보고.md. 예약 시각은 _예약표.md 보고 사람이 입력. 발행은 안 함.
- 할 일: wp_inbox_build 가 같은 상품id 중복을 합칠 때 '필수 표기정보'가 빠지는 문제(이번엔 research 에 백업본을 되돌려 넣어 해결). 1128 D 마지막 수정본은 reviewer 재검수 안 받음.

## 이어갈 것 (2026-11-22 싼세계 자동진행판 2): 0~3단계 끝, 8편 앱에 넣음(대기열 114→122, 글 101→109)
- 1단계 14줄 교체, 2단계 reviewer 지적 반영(위반 0). 보류 2편: 1128_3_D(1위 근거·1127 C와 도입 겹침, 다시 써야 함), 1129_1_S(후기 주제 개수 2건 재집계).
- 보고 pipeline/wp_setup_run/20261121_자동진행_보고.md. 예약 시각은 _예약표.md 보고 사람이 입력. 발행은 안 함.

## 이어갈 것 (2026-11-21 싼세계 보낼것 모음 5차): 0~3단계 끝
- 0·1단계: 'ego채움' 백업=빈 양식(도구로 쿠팡 연 기록 없음). 2단계: 1124_2_S_이유식보관용기·1128_2_S_원룸가습기 위반 0, 앱에 안 넣음(채팅 검수 대기).
- 3단계: 1123 S 문구 3곳 바꿈, 1123 D 제목·키워드(틈막이 문풍지) 고침(백업 _backup_20261121_채팅검수반영/) → --apply: 대기열 112→114, 글 99→101(11/23 12:34 S, 18:50 D). 예약 시각은 _예약표.md 보고 사람이 입력. 남은 8편+새 2편은 채팅 검수 뒤.

## 이어갈 것 (2026-11-21 싼세계 보낼것 모음 4차): 0~2단계 끝, 3단계 원고 1편 완성·3편 자료 보강 요청으로 멈춤
- 1단계: wp_inbox_build 가 같은 상품id 후기를 합침(5f2afbe)·대가성 단어 4개 추가. 2단계 사양: 지퍼백(LWP620NTL, 250*207*78mm, 실리콘, 230℃)만 락앤락몰에서 확인, 이유식 용기·한경희 GF-HAD2290 은 공식 페이지 못 찾음(_공식사양.json).
- 3단계(11/21 추가): 1124 C 가습기 추천은 사용자 결정으로 daroom·듀플렉스 2개만 써서 완성(위반 0, 4,251자, pipeline/wp_drafts/2026-11-23/1124_1_C_가열식가습기추천.md). 남은 보류는 1124 S·1128 S 2편.
- 3단계: 1126 S 지퍼백 원고 위반 0(pipeline/wp_drafts/2026-11-23/1126_2_S_실리콘지퍼백.md). 1124 C·1124 S·1128 S 는 작가가 '자료 보강 요청'(한경희 가습량·소비전력, 이유식 용기 소재·냉동·전자레인지·내열). 앱에는 안 넣음. 다음: 사용자가 사양 자료를 주거나 1124 C 를 제품 2개로 줄일지 결정.

## 이어갈 것 (2026-11-21 싼세계 4단계 끝): 원고 8편 새로 씀(위반 0, 1123 D 문풍지만 제목 키워드 15자 규칙 위반 1), 4편 보류
- 보류: 1124 C(한경희 1분 퀵스타트 자료 없음) · 1124 S(이유식 제품 없음) · 1126 S(지퍼백 후기 3/10) · 1128 S(한경희 후기 0). 앱에는 안 넣음(채팅 검수 뒤). 끌리젠은 _예외.txt 로 제외.

## 이어갈 것 (2026-11-21 싼세계 보낼것 모음 3차): 1~3단계 끝, 4단계는 코드만 끝(추가 자료 13칸 전부 비어 있음)
- 1단계 코드 수정 f2de79e·4단계 읽기 2a81931. 2단계 원고 글자 바꿈 끝(9편 위반 0, 1129 D 제목은 41자라 37자로 줄임, 원본 _backup_20261121_채팅검수반영/). 3단계 앱에 9편 넣음(대기열 103→112, 글 90→99, 백업 *.backup_before_wp_import_20261121.json).
- 다음: 사용자가 _추가자료_20261121.txt 를 채우면 python tools/wp_inbox_build.py --week 2026-11-23 → 막힌 12편 wp-writer 로 다시(넣지 말고 채팅 검수). 예약 시각은 pipeline/wp_drafts/2026-11-23/_예약표.md 를 보고 사람이 입력.

## 진행 중 (2026-11-21 싼세계 보낼것 모음 2차): 5단계 끝 — 원고 9편 작성(검사 위반 0), 12편은 작가가 '자료 보강 요청'으로 멈춤(가습기 6편·미니 온풍기·문풍지·뽁뽁이 3편·발매트 2편·지퍼백·이유식 보관용기). 원고 위치 pipeline/wp_drafts/2026-11-23/.
- 다음: 6단계 `python tools/wp_import.py --week 2026-11-23`(미리보기만, --apply 금지) → 7단계 reviewer → push. 막힌 12편은 사용자가 쿠팡 필수 표기정보를 복사해 research 에 채워 줘야 다시 맡길 수 있음. 영문 상품id 도 wp_check·wp_import 가 읽도록 고침(2576299).

## 완료 (2026-11-20 radar 후속2) 커밋 2f0f130: ① 함께 보면 좋은 글 후보=대기열 id 날짜 2026-11-23 이상 + 글 번호 주소(발행일 칸 안 봄) → 11/20 에 발행한 GO5 가 나옴, 옛 글은 안 나옴(메모리 시험, 최종본도 메모리에서만). ② 주소 채우기: 예정일이 미래여도 RSS 에 같은 제목이 정확히 1개면 '채움'(미리 발행한 글). 실제 RSS 읽기 시험: GO5 앱·RSS 제목/주소 같음, 주소 비운 사본에서 '채움'·주소 일치. 실제 json·업로드 폴더 그대로(해시 같음). 저장 안 함.
- 남은 것: preview/editor_radar_step4·4b 옛 시험은 '미래 날짜 글 제외' 규칙을 기대해 새 규칙에서 실패할 수 있음(코드 아님, 시험 갱신 필요). 예약 중 글에 주소를 수동으로 잘못 넣으면 후보가 될 수 있음.

## 완료 (2026-11-20 radar 마무리) 커밋 d442e2a: ① 주소 채우기 대상: 앱에 발행 날짜가 없으면 대기열 plan.publish_date 로 봄(지난번 0개 원인=날짜 칸이 11/23 이후 42개 중 41개 비어 있었음). 오늘(11/20)은 41개 전부 '예약 중'이라 11/23 이후에 채워짐. ② 사진 제외 단어는 영어 단어 단위(design·cardigan 은 제외 안 됨). ③ RADAR_LETTERBOX_FILL=True. ④ 11/23 주 태그 20편 넣음(태그 칸만 바뀜).
- ④ 주의: radar_import --update 가 최종본(platform_final)·생성시각까지 비워서, 백업 값으로 되돌림(백업 articles_radar_naver.backup_before_tags_20261120.json, 되돌리기 전 상태 .backup_after_import_before_restore_20261120.json). 큐 파일은 안 바뀜.
- reviewer: 큰 문제 없음. 참고: detailimage 처럼 영어가 붙은 파일명은 제외 못 잡음(실제 사진 폴더엔 그런 이름 없음).
- 시험 스크립트 preview/radar_final_20261120/(git 제외 폴더).

## 완료 (2026-11-20 radar 후속): ① 사진 띠 대상=사진 출처로 판단(_detail·pieces·gallery·캡처·표·카드·모름은 제외, RADAR_LETTERBOX_FILL 은 계속 False) ② 주소 채우기는 id 2026-11-23 이후 글만(옛 글 60개 뺌, 맨 위 한 줄 표시) ③ 제목 짝짓기=HTML 기호 풀고 띄어쓰기·문장부호·대소문자 뺀 값, 글번호 없는 RSS 는 '주소 형식 다름'으로 표시 — reviewer 결과는 아래 참고
- 함께 보면 좋은 글: pick_related 가 발행일 >= 2026-11-23 만 고르므로 옛 글은 못 고름(옛 id 인데 발행일이 이후인 글 0개) → 코드 안 고침. 실제 주소 저장·앱 데이터 수정 안 함.

## 완료 (2026-11-20 radar): (가) 11/23 주 데스크피커 태그 · (나) radar 발행 주소 자동 채우기 · (다) 본문 사진 띠 없애기 — reviewer 검수: 문제 없음(사소한 권장 2: 메모 종류가 제품인데 캡처를 고른 경우 켤 때 시안 확인, RSS 글번호 없는 항목은 버림)
- (가) 20편 '태그:' 줄만 띄어쓰기 없이 붙임(개수 10~15, 키워드 붙임 태그 있음). 백업 pipeline/radar_drafts/_backup/2026-11-23_before_tags/(20개). radar_check 위반 0, radar_import --update 미리보기: 바뀔 글 20(태그만). 전후 표 preview/radar_tags_before_after_20261120.md. 앱에 안 넣음(--apply 안 함). 앱은 태그를 빈칸으로도 자름(radar_screen.parse_tag_text).
- (나) app.py 새 길 /radar-url-autofill(미리보기)→/radar-url-autofill/apply(확인 후 저장, 저장 전 articles_radar_naver.backup_before_urlfill_날짜.json). 버튼: 대기열 화면·발행 현황 화면 위쪽 '🔗 글 주소 자동 채우기'(radar_naver 일 때만). 제목 한 글자도 같을 때만, 글 번호 있는 주소·다른 블로그 주소는 안 덮음, 예약 중은 건너뜀. 실제 실행 안 함(가짜 데이터 시험만). 커밋 c7245a2.
- (다) radar_screen.letterbox_plan 등 + config.RADAR_LETTERBOX_FILL=False(꺼 둠). 시안 preview/radar_letterbox_20261120/, 사진 표 preview/radar_letterbox_20261120_table.md, 점검 도구 tools/radar_letterbox_report.py. 커밋 55318e2. 시안 확인 뒤 config 를 True 로 켜면 다음 '③ 최종본 만들기'부터 업로드 폴더가 새 방식으로 다시 만들어짐(11/23 주 업로드 폴더는 아직 안 건드림).

## 진행 중 (2026-11-20): N살림노트 11/23 주 원고 — 7~21번 15편 작성(날짜별 3편 동시), 05번 제목 고침 끝
- 단계: 11/25(7·8·9)·11/26(10·11·12) 끝(검사 ✖0, 8·11번 제품 자료 부족). 11/27(13·14·15) 작성 중. 날짜마다 salim_check --week 2026-11-23 검사 후 이 줄 갱신. 앱 데이터·app.py 안 건드림.
- 다음 명령: python tools/salim_check.py --week 2026-11-23 (끊기면 pipeline/salim_drafts/2026-11-23/ 에 있는 번호 다음부터 이어서)

## 완료 (2026-11-20): N살림노트 작가(salim-writer)·검사 도구(tools/salim_check.py) 만들고 11/23 1~3번 시범 원고 저장, 검사 ✖ 0
- 원고: pipeline/salim_drafts/2026-11-23/ 01_패브릭소파(A)·02_현관매트(C, 링크 확정 목록과 같음)·03_LED줄전구(A). 새 제목 3편 모두 확정 제목에서 바뀜. 검사 도구는 같은 날짜 소제목·경험 장면 겹침도 봄.
- 에이전트 파일은 .claude/agents/salim-writer.md(사본 docs/살림노트/agent_salim-writer.md). 새 에이전트는 다음 세션부터 이름으로 부를 수 있음(이번엔 Opus 범용 에이전트로 대신 씀).
- 남은 것: 4~21번 미작성. 다음은 날짜별 3편 동시 호출(경험 장면 미리 지정). 앱 데이터에는 안 넣음.

## 완료 (2026-11-20): N살림노트 11/23 주 확정 목록 파일 만듦 — Claude outputs/weekly_final_2026-11-23.md (읽기만, 승인 21·날짜별 3편·제품 링크 글 8·빈 칸 0)

## 완료 (2026-11-20): N살림노트 11/23 주 발행일 8편 바꿈(바뀐 항목 8: 대기열 plan.publish_date·weekday + plan_20261123.json 같은 8편)
- 백업: backup/content_queue_naver_tips.backup_before_redate_20261120_093152.json, pipeline/final/plan_20261123.backup_before_redate_20261120.json. 날짜별 3편씩 유지. finalize·push_plan 안 돌림.

## 완료 (2026-11-20): N살림노트 11/23 주 판매글 17편→8편 — 대기열에 9편 제품 비움(바뀐 항목 9, 백업 backup/content_queue_naver_tips.backup_before_update_products_20261120_092114.json)
- 보류 9개 키워드(13줄)는 products_20261123.txt 에서 '# 보류' 주석(백업 .backup_before_cut8_20261120). 링크 있는 승인 글 8/21.

## 완료 (2026-11-20): N살림노트 11/23 주 제품 정리 3단계 — 대기열에 17편 제품 넣음(백업 backup/content_queue_naver_tips.backup_before_update_products_20261120_090305.json)
- 끝낸 것: 남은 링크 5개 발급(발급 5·기존 20·실패 0, links.json 25칸 모두 있음, approved.json 은 48개로 복구), 자료 6개 수집(새 5개+로긴 식기건조대 재수집, 임시 복사본으로 login 판단만 좁힘), products_20261123.txt 25줄(백업 .backup_before_slots_20261120), products_20261123.json 만듦, --update-products 미리 보기(바뀔 17편, 경고 없음).
- 다음 명령(사용자가 "넣어"라고 하면만): python tools/push_plan.py --products pipeline/final/products_20261123.json --update-products --apply
- _keep 폴더는 본 폴더에 없는 파일 0개(지워도 되는지는 사용자가 결정).

## 완료 (2026-11-20): W싼세계 받은편지함 새 양식(날짜+틀 '쓸 글', _주간계획.txt, 글별 준비표)
- wp_inbox_build.py(글별 준비표·'_' 파일 제외·research '쓸 글' 줄), wp_check.py(원고 이름 날짜+틀로 제품 고름), wp-writer.md 안내 추가. 시험: pipeline/wp_inbox/_test_v2/README.md. 실제 폴더 돌림: 제품 0개 → 준비표 6줄 '제품 없음'.
- 다음: 제품 파일을 _양식.txt 복사해 채우기 → 다시 돌리기. wp_import.py 는 wp_check 를 부르므로 수정 없음.

## 회사 PC에서 이어갈 것 (2026-11-19 저녁 마무리, 집 PC 마지막 작업 커밋 b65f281)
- 먼저: 구글 드라이브 동기화 완료 확인 → git fetch → git reset origin/main → git status 깨끗한지 확인. (preview/, 데이터 파일, pipeline 결과는 git 이 아니라 드라이브로 옮겨짐)
- N살림노트 11/23 주: 대기열 넣기 끝(142→168), 링크 20개 발급·자료 19개 수집 끝. 다음은 채팅이 준 "제품 찾기 3단계" 지시문(남은 링크 10개·로긴 재수집·products_20261123.txt·--update-products 미리 보기).
- 결과 폴더: pipeline/candidates_run/20261119_naver_tips(본 폴더)·_2(다시 찾기 결과)·_keep(복사본, 지워도 됨).
- 에고라이트는 회사 PC에서 다시 네이버 로그인 필요.

## 완료 (2026-11-19): N살림노트 11/23 주 제품 찾기 2단계 (멈춤)
- approved.json 25개(상품id 기준)·salim_slots.json 25줄 만듦(pipeline/candidates_run/20261119_naver_tips, 앞서 있던 approved 는 없었음). 링크는 앞 20개만 발급: 발급 20·실패 0 (links.json). 나머지 5개(붙이는거울·원목서랍장·인덕션·일산화탄소경보기 2)는 링크 아직 없음. 제외 방법: approved.json 을 잠깐 20개짜리로 바꿔 실행 후 25개로 복구(approved_full25.json 은 25개 사본).
- 자료·사진: 19개 성공(research/<id>.md, _img/<id>/01_rep.*). 실패 1개: 878085513998688(로긴 식기건조대) — 스토어 주소가 brand.naver.com/loginmall/… 라서 collect_research_ego.mjs 가 'login' 글자만 보고 로그인 풀림으로 착각해 멈춤(로그인은 정상). 도구는 안 고침. 고치려면 정규식을 /nidlogin|\/login(\?|$)/ 처럼 좁히기.
- 다시 찾기: pipeline/final/product_search_20261119_2.tsv(12검색어) → 결과 pipeline/candidates_run/20261119_naver_tips_2. 도구가 결과 폴더 이름을 날짜+채널로 고정해, 첫 폴더를 _keep 으로 바꿔 두고 돌린 뒤 복사로 복원(이름 변경이 '사용 중'으로 막혀 _keep 폴더가 남아 있음 — 백업과 동일 확인, 사용자가 지워도 됨).
- 안 한 것: 대기열·앱 코드·글 데이터. 다음: 사용자가 칸2 후보 고르기 → 나머지 5개 링크 발급 → 글 연결.

## 완료 (2026-11-19): N살림노트 11/23 주 제품 찾기 1단계 — 후보·추천표까지 (멈춤)
- product_candidates.py 에 --pick·--sheet·--extra 추가(커밋 f0cdca1, 기본 동작 불변: 지난 25개 검색어 전후 추천 동일). 시트 pipeline/final/product_search_20261123.tsv(29줄·44칸).
- 결과 pipeline/candidates_run/20261119_naver_tips (salim_picks.md = 칸별 추천+다음 후보 2개). 조건 맞는지는 모두 '확인 필요'.
- 문제 칸 6: 가스누설 경보기·거실 대형 카페트(추천 1개뿐), 셀프 줄눈 키트·욕실 줄눈재·서랍 레일 윤활제·겨울용 워셔액(추천 0). 검색어 바꿔야 함.
- 안 한 것: 링크 발급·자료 수집·앱 코드·대기열 쓰기. 다음: 문제 칸 검색어 정하고 사용자가 추천 확인.

## 완료 (2026-11-19): N살림노트 제품 찾기를 데스크피커 방식으로 — 조사만 (고친 것 없음)
- 보고서: preview/salim_products_probe/report.md. 코드·앱 데이터·대기열·링크 발급 안 건드림.
- 핵심: 후보·발급·자료수집 도구는 이미 naver_tips 를 받음. 막힌 곳은 ① plan 칸에 검색어(search_query) 없음(26편 전부) ② 앱 편집기가 naver_tips 상품 사진 칸을 숨김 ③ 링크가 본문 뒤에 나옴(요청은 사진 바로 아래).
- 추천: 도구 재사용 + 검색어 시트·연결 스크립트(새로) + 앱은 naver_tips 일 때만 켜지는 옵션. 사용자 확인 3가지는 보고서 맨 끝.

## 완료 (2026-11-19): 11/23 주 N살림노트 26편(승인 21+예비 5) 앱 대기열에 넣음
- categories_20261123.json 빈 카테고리 9칸 채움(빈 칸 0, 채우기 전 백업 categories_20261123.backup_before_fill_20261119.json). 넣기 --apply: content_queue_naver_tips.json 142 → 168편.
- 백업: backup/content_queue_naver_tips.backup_before_push_plan_20261119_220108.json. 되돌리기: python tools/push_plan.py --undo (넣은 id 는 pipeline/final/push_log.json).
- 제품 링크는 아직 없음(products_20261123.json 없음). 다음: 제품 찾아 링크 채우기(--products-txt, --update-products).

## 완료 (2026-11-19): 주간 기획 11/23 주 3차 보충 N살림노트 (확정·앱 넣기 안 함)
- 결과 폴더: pipeline/stage_w1123_run3 (2차 stage_w1123_run 은 안 건드림). 입력: pipeline/final/seeds_20261123_r3.txt, skip_20261123_r3.txt (2차분은 _try2 보관).
- 19편 나옴(목표 21, 2편 부족), 예비 0편(5편 부족). 갈래당 4편 한도로 21개 빠짐. 후보 적은 씨앗: 냉동실 1개, 베란다 3개. 설계 다시 받은 글 7편.
- 사용량: 블로그 43·데이터랩 34·검색광고 15, Claude 6회 약 $0.57. 기획 호출 2번.
- 주의: 2차 폴더 글과 겹치는지(예: 생강·조명) 아직 안 맞춰 봄. 확정 전에 확인 필요.

## 완료 (2026-11-19): 주간 기획 11/23 주 2차 N살림노트 (확정·앱 넣기 안 함)
- 결과 폴더: pipeline/stage_w1123_run (1차는 stage_w1123_run_try1 보관). 입력: pipeline/final/must_20261123.txt, seeds_20261123.txt, skip_20261123.txt (1차분은 _try1).
- 21편 나옴(목표 21, 부족 0), 예비 0편(5편 부족). 꼭 넣을 글감 8편 모두 들어감. 설계 다시 받은 글 4편(아기욕조·전기무릎담요·2구가스레인지·인덕션).
- 사용량: 블로그 44·데이터랩 30·검색광고 22, Claude 6회 약 $0.65. 기획 호출 2번.
- 다음: 예비 5편 보충 여부는 사용자가 정함.

## 완료 (2026-11-19): 주간 기획 2026-11-23 주 N살림노트 (확정·앱 넣기 안 함)
- 결과 폴더: pipeline/stage_w1123_run (report.md, plan_20261123.json). 입력: pipeline/final/seeds_20261123.txt, skip_20261123.txt.
- 14편 나옴(목표 21, 7편 부족), 예비 0편(5편 부족). 후보 0개 씨앗: 세탁기동파. 1개뿐: 무보관·대청소·실내건조, 2개: 줄전구.
- 기획 호출 2번, 설계 다시 받은 글 5편. 사용량: 블로그 38·데이터랩 27·검색광고 15, Claude 6회 약 $0.41.
- 다음: 부족분(7+5편) 보충 여부를 사용자가 정함. 씨앗 보강 후 같은 명령 재실행 가능(기획 호출 한도 확인).

## 완료 (2026-11-19): 주간 수집 — N살림노트 2026-11-09 주(11/9~15)
- 받은 것(perf_inbox/2026-11-09/raw/): rank_weekly, referer_weekly, sales_30d_2026-11-18.xlsx, clicks_daily_chart.csv, links_list.csv(80건). 요약 5종은 각 주 폴더에 갱신, perf_inbox/summary_4weeks.md 는 10/19~11/9 4주로 갱신.
- 숫자: 조회수 597(파일 합계와 일치), 클릭 18, 주문 3건(예상 수익 2,833원). 그래프 클릭 합계 81 = 화면 81 일치.
- 앱 코드·앱 데이터는 안 건드림(해시 같음). 합본 4주 제한은 임시 스크립트(scratchpad)로 처리 — weekly_collect_join.py --all 은 모든 주(5주)를 합본에 넣음.
- 다음 수집: 11/16 주(월요일 이후), DAYS 수요일 18.

- 회사 PC 마지막 커밋: fe9093e
- 확인 필요로 남긴 파일: 없음
- 다음 작업은 집 PC

## 완료 (2026-11-19): N살림노트(naver_tips) 본문 사진 3장 → 4장
- app.py: NAVER_TIPS_BODY_IMAGE_COUNT=4 + _info_body_image_count(채널) 추가, gen_images 정보 채널 장수 줄만 교체. INFO_BODY_IMAGE_COUNT=3 그대로(wp_tips 3장), longterm_wp·radar_naver 안 건드림. 채널 판단은 화면 채널(get_active_channel_id) — 글·대기열 데이터가 채널별 파일이라 글 자체에 채널 값이 없음.
- 장수 계산 시험만 했고 사진 생성·앱 데이터 안 건드림. 커밋 cd1cd4c.

## 완료 (2026-11-19): 싼세계 원고지침 v1.5 + wp_import 제목 중복 막기
- tools/wp_rules.md=v1.5(md5 f9f5eeff…)·docs 사본 추가 / wp_import: 한 번 실행에 같은 제목 원고 2편 이상이면 모두 '문제 — 제목 중복'으로 건너뛰고 예약표에서도 뺌(시험 _test/dup_title_check.md)
- CLAUDE.md: '대시보드를 거친다' 뜻의 싼세계 문장이 없어 고친 곳 없음. 실제 앱 데이터·발행 안 건드림.

## 완료 (2026-11-19): wp_import 예약 값을 앱에 안 쓰고 _예약표.md 로
- --apply 가 set-published-date 호출·platform_scheduled_time 저장을 안 함(앱엔 예약 값 0). 시각 뽑기(C 07:40·S 12:30·D 18:40 ±10분, 시드, --reroll, 같은 주 같은 시:분 금지)는 그대로, 결과는 <원고폴더>/_예약표.md(날짜·요일·시각·틀·제목·대기열 id)에 저장, 미리보기에도 같은 표 출력. 다시 돌리면 표의 기존 글 시각 유지·표 시각 피함.
- 시험(_test/drafts_v15, appdata_v15 복사본): 복사본 글의 발행일·예약시각 비어 있음, 표에 C 07:34·D 18:41·S 12:32 모두 범위 안, 실제 content_queue/articles md5 전후 같음. 사람이 최종본 화면에서 표를 보고 직접 입력해야 함.

## 완료 (2026-11-19): 싼세계 원고지침 v1.4 + wp_import 예약 시각
- 1 tools/wp_rules.md=v1.4(md5 dd4ee39f…) · docs 사본 추가 / 2 wp_check: 기준일 줄 형식·틀별 문구·C의 '후기에서/불만'·자료 부족 문장 금지(결과 _test/check_result_v14.md) / 3 wp_import: 예약 날짜·시각 열, --reroll, 기존 시각 유지. 시험은 _test/drafts_v14 + appdata_v14(복사본)
- 알아둘 것: 칸(발행일·예약시각)만 채워서는 발행 안 됨(스케줄러 없음, 워드프레스로는 최종본 화면 버튼). 단 날짜가 미래면 앱 목록엔 '예약됨'으로 보임. 싼세계 최종본 화면 시각 입력칸은 자동으로 안 채워짐(혜택주머니 lt_plan 만 채움).

## 완료 (2026-11-19): 싼세계 원고 흐름 도구
- 만든 것: tools/wp_rules.md(v1.3, 원본과 cmp 같음, docs 사본) · .claude/agents/wp-writer.md(model: claude-opus-5-5, 실제 Opus 5.5 로 돎 확인) · tools/wp_check.py + wp_banned.json · tools/wp_import.py(--week, --apply, 미리보기 기본). 시험 결과: pipeline/wp_inbox/_test/README_시험결과.md
- 다음: 실제 11/23 원고는 받은편지함 research 가 있어야 시작. 예약 시각은 안 넣음(앱 platform_published_at+platform_scheduled_time 칸이 있어 가능하나 이번엔 보고만). --update(고친 원고 다시 넣기) 미구현.

## 진행 중: 싼세계 11/23 주 시작 준비 — 4-1 미리보기에서 멈춤, 사용자 "적용" 대기 (2026-11-19)
- 끝: 1 keywords.md(구글 사이트 종류만 미확인: 구글이 주소를 /goto 로 감싸 읽기 오류, 복사본 tools/wp_google_check.mjs 에 고침, 구글 13/15회 사용) · 2 pipeline/wp_inbox/2026-11-23/_양식.txt · 3 tools/wp_inbox_build.py(시험 통과, _test 폴더로 이동) · 4-1 categories/preview.md + backup_categories.json
- 다음: 사용자가 "적용" → python tools/wp_category_fix.py --apply (그 뒤 앱 재동기화가 옛 카테고리 이름으로 새로 만드는지 코드 확인만, 고치지 않음). 구글 12개 재조회는 한도 초과라 사용자 허락 필요.

## 완료 (2026-11-19): 싼세계 새 방식 조사(읽기만)
- 결과: pipeline/wp_setup_run/20261119/report.md, existing_posts.csv(글 102편: ① 28 · ② 69 · ③ 2 · 확인필요 3). 코드·앱 데이터·워드프레스 수정 없음(REST GET만).
- 다음: report.md 5번 "11/23 시작에 꼭 필요한 작업" 순서대로. 사용자 결정 먼저(① 중 안 맞는 8편 처리, 카테고리 이름, 구글 검색량 출처).

## 완료 (2026-11-19): 혜택주간 11/23 앱 입력 14편 성공(--apply)
- 키워드·원고·메타·이미지(표지 14+본문 69)·최종본·예약정보 모두 성공. 결과표: 주간기획/혜택_2026-11-23/07_앱입력결과.md. 백업 .backup_before_lt1123_apply_20261119.json(content_queue·articles·longterm_cover_accent).
- 발행 버튼은 사용자가 누름(최종본 화면에서 예약 발행). 다시 --apply 하지 말 것(중복 글 생길 수 있음).

## 완료: 혜택주간 2026-11-23, 5차(문체 통일)
- 5차: 작가·SKILL 복사, lt_draft_check 에 해요체 비율(20% 초과 ✖)·본문 확인 표시 검사 추가. 11/23 원고 10편(1,2,3,5,6,7,9,10,11,13) 격식체로 고침, 4·8·12·14는 이미 통과라 그대로. 14편 모두 검사 통과.
- 3번 제목 → '월세 세액공제 조건, 총급여 5,500만 원 이하면 17%예요'. 제목이 기획표와 달라진 글: 2·3·6·11·12·13·14번.
- 계산값 정리 끝(6차): 1번 95% 표 남김+근거 한 줄(국세청 공식 금액 확인)·가정 예시 뺌 / 2번 선정기준 표를 복지부 2026.7.28 보도자료 원문 숫자로 교체·가정 예시 뺌(5·6인 생계급여는 원문 못 찾아 '고시로 확인' 문장) / 7번 금리 4.5%·가정 감면 예시 뺌, 상한표는 새출발기금 누리집 공식 비율만. 14편 검사 모두 통과.

## 완료 (2026-11-19): 4차 FAQ 최대 4쌍 — lt_draft_check FAQ 3~4쌍(5쌍↑ ✖, 5쌍 글로 시험 확인), 작가·SKILL 복사본 docs와 같음.

## 완료 (2026-11-19): 3차 분량·사진·작가
- 본문 사진 4,000자 미만 4장 / 이상 5장(app.py), 메모 장면 3~5줄은 그대로 그 수만큼(app.py·image_gen.py·lt_auto_input.py). 시험: 메모 5줄→5장·최종본 그림 6(표지 포함), 메모 3줄→3장·4. 실제 데이터 SHA 전후 같음.
- 새 도구 tools/lt_draft_check.py(원고 검사, FAQ 3~4쌍 기준=4차 반영). 작가 .claude/agents/hyetaek-writer.md(model: opus), SKILL 다시 복사.
- 11/23 기획표는 그대로(원고 안 씀). 아래 진행 중 줄 그대로 [확인] 대기.

## 진행 중: 혜택주간 2026-11-23, 5단계 기획표에서 멈춤 ([확인] 대기)
- 1~4단계 끝: 주간기획/혜택_2026-11-23/ 에 01_실적분석, 02_조회목록(+2차), 03_검색량(+2차), 04_구글경쟁(+2차), 05_발행칸.json, 05_기획표.md
- 다음: 사용자 [확인] 받으면 06_확정.json 만들고 6단계 원고 → 7단계 lt_auto_input 미리보기 (확인 전에 원고 쓰지 않음)

## 완료 (2026-11-19): 발행 칸 승인 전 14칸
- tools/lt_schedule_slots.py --mode review(기본, 오전 08:21~08:41·오후 16:41~17:14 = 14칸) / --mode full(승인 후 21칸). 사용 중 판정은 칸 시작~끝 시각 안의 예약. SKILL 파일(.claude)은 docs 판과 같게 맞춤. lt_auto_input 은 plan.json 의 slot(YYYY-MM-DDTHH:MM)만 읽어 칸 수와 무관.

## 완료 (2026-11-19): 자동 프로젝트 1차 — 혜택주머니 도구 6개 + 최종본 화면 수정
- 스킬: .claude/skills/hyetaek-weekly/SKILL.md (docs/혜택주머니/ 는 .gitignore 라 git 에 안 올라감 — 드라이브로만 동기화)
- 도구(tools/): lt_perf_report.py(서치콘솔 새로 받기+01_실적분석.md) / lt_export_plan.py / lt_keyword_volume.py / lt_google_check.py(+.mjs, ego lite 실행기 — node 로 직접 못 돌려서 py 가 부름) / lt_schedule_slots.py(21칸, 시드=주차) / lt_auto_input.py(미리보기 기본, --apply 로 넣기, --only·--skip-images) / lt_common.py(공통)
- 앱 수정: view_final 에 _lt_plan_view, templates/final_view.html 혜택주머니 발행 칸(API·브라우저)에 lt_plan 이 있으면 예약 발행 기본·시각·카테고리·태그 미리 채움, 시각 지났으면 빨간 경고+버튼 잠금. lt_plan 없는 글·싼세계 화면은 공백만 다르고 같음
- 시험: 복사본 데이터(임시 폴더)+이미지 가짜 모드로 글 2편(직접추가·승인) 키워드→붙여넣기→메타→이미지→최종본→예약저장 성공. 실제 content_queue/articles SHA 시험 전후 같음. 구글 경쟁 2개(청년미래적금 3차 모집·근로장려금 신청기간) 성공
- 주의: 실제 --apply 하면 longterm_cover_accent.json 에 묶음별 표지 색이 새로 기록됨(앱 원래 동작). 구글 경쟁은 로그인 없이 읽기만, 막히면 그 자리에서 저장 후 멈춤
- 다음: 11/23 주 혜택주간(/hyetaek-weekly) 시작 가능

## 완료 (2026-11-19): N데스크피커 카테고리 이름 적용 + 11/30 주 대기열 넣기
- 적용: 11/23 plan.json 갈래 21개(AD500S=충전·멀티탭), 대기열 plan.갈래 16개(AD500S 는 옛 '충전·전원' 그대로 — 글 없음), radar_import --update --apply 16편(바뀐 칸: 메모 카테고리 줄·platform_generated_at, 최종본은 JBL FLIP7 1편만 비워짐), images.json 6개. 사진 선택·안 넣음·표지·GIF 기록(photos.json)은 안 건드림. 이미 만든 표지(cover.jpg)엔 옛 갈래 글자가 박혀 있으니 [표지 다시 만들기] 필요
- 코드: filter.json theme_order 에서 방꾸미기를 책상 정리 앞으로 + keyword_pipeline.theme_of 가 꺼 둔 갈래에 걸리면 '갈래 미정'(수납장이 책상 정리로 새지 않음). 이어폰·헤드폰 거치대는 원래도 이어폰·스피커. 커밋 c06e525
- 11/30 주: push_plan --apply 21편(날짜당 3편, id 2026-11-30_06066f6b~ede9ae89 해시순, 대기열 94→115). 11/30 순번1 은 처음부터 '소니헤드폰'(바뀐 적 없음)
- 백업(suffix .backup_before_category_20261119): content_queue_radar_naver / articles_radar_naver / 11/23 plan.json / images.json 6개. 그 밖에 articles_radar_naver.backup_before_radar_import_20261119_092114.json, backup/content_queue_radar_naver.backup_before_push_plan_20261119_092324.json
- docs/데스크피커-원고지침_v3.3_20261119.md 로 이름 바꿈(복사본 글자 제거)
- 안 한 것: 링크 발급·제품 후보·keyword_pipeline 재실행·글쓰기·발행. 11/30 report.md·keywords.json 은 옛 값 그대로

## 완료 (2026-11-19): N데스크피커 카테고리 이름 바꾸기 — 미리보기까지만(적용 안 함)
- 한 일: radar_rules.md v3.3(8f03089) / filter.json·config 갈래 9개로(71e175c, 방꾸미기는 theme_off 로 꺼 둠, 큰 카테고리 범위는 report.md 경고만) / 11/30 plan.json 갈래 21개 + 한일 840→2000AI(백업 plan.json.backup_before_category_20261119) / 11/23 원고 20편 카테고리 줄만(백업 pipeline/radar_drafts/_backup/2026-11-23_before_category/)
- 안 한 것: push_plan --apply, radar_import --apply, 대기열·글 데이터 쓰기. radar_check 위반 0, radar_import --update 미리보기 16편 바뀜·4편 같음.
- 다음("적용해" 받으면): radar_import --update --apply + 대기열 plan.갈래 20개 + 11/23 plan.json radar.갈래(함께 보면 좋은 글·표지가 여기서 읽음) 수정, 백업 후.

## 완료 (2026-11-19): 11/30 주 결과 수정 + 원고지침 v3.2 (대기열엔 안 넣음)
- radar_rules.md 를 v3.2 로 교체(a7a8d65, docs 파일과 동일). 고친 것: pipeline/keyword_run/2026-11-30_radar_naver/plan.json (백업 plan.json.backup_before_edit_20261119). 교체 2(이룸→베네폼 허리소프트, AMA2→한일 840)·키워드 1(큐브멀티탭)·제목 3·NUPHY 갈래 입력·날짜 재배치
- push_plan --channel radar_naver dry-run 21편 정상, --apply 안 함. report.md·keywords.json·status.json 은 옛 값 그대로(안 고침). 다음: 사용자 확인 후 push_plan --apply

## 완료 (2026-11-19): N데스크피커 11/30 주 키워드·제목 21편 만들기(대기열엔 안 넣음)
- 실행함: python tools/keyword_pipeline.py --week 2026-11-30 (결과 pipeline/keyword_run/2026-11-30_radar_naver/). 대기열 안 넣음(--apply 안 씀)
- 결과: 승인 21편(A7+B14)·보류 6편, 갈래 부족 '몸편한것' 1편. API 블로그 260·검색광고 37·Claude 3. 다음: 사용자 확인 후 push_plan --apply

## 완료 (2026-11-18): 배너 가로 410 → 455 (config.RADAR_BANNER_WIDTH 한 줄만)
- 근거: 네이버 화면 링크카드 약 428px, 410 배너 녹색 띠 약 386px. JBL FLIP7(1125_2) 복사본(임시 폴더)으로 최종본 시험 → 업로드 폴더 배너 2개 410×62 → 455×69. 앱 데이터·원고 수정 없음
- 이미 만든 최종본은 [최종본 만들기]를 다시 눌러야 반영됨

## 완료 (2026-11-18): 원고 20편 숫자 대조(보고만) · 1123_3 카드 아쉬운 점 3→2 · 앱에 넣음(1123_3만)
- 숫자 대조: 새로 생긴 숫자 모두 research 에 있음(BPD004·99g·10~45mm·200mm·3만 원대), 지워진 숫자는 1124_3 의 1점/2019~2022년/2026년 등. 1126_3·1128_3 숫자는 백업 전부터 있던 값(1126_3 27kg·10~45mm 는 '보강 11/18-2' 다나와 값)
- 1123_3 카드: 아쉬운 점 '뻑뻑하고 소음 나는 바퀴 / 높이 바꿀 때 렌치 필요'. radar_check 위반 0. 앱 글 89→89, 바뀐 id 2026-11-23_e84f268d 하나. 백업 articles_radar_naver.backup_before_update_1123_3_20261118.json
- 이미 만든 1123_3 최종본은 [최종본 만들기]를 다시 눌러야 반영됨

## 완료 (2026-11-18): 원고 20편 옛 단점 문구 정리 → 앱에 넣음(--update --apply)
- 카드 문구를 본문 아쉬운 점에 맞춤(1123_2·1123_3·1125_2·1126_2·1126_3·1127_2·1128_2·1128_3·1129_2·1129_3), 1128_3 소제목 '불량' 제거, 1128_2 대안 고장 문장·1126_3/1127_2 결론 문장 정리. 1127_3 및 A형 카드는 변경 없음. radar_check 위반 0, 제목·상품카드·[사진: n] 백업과 동일.
- 앱 글 89→89, 백업에만 0, 새 id 0, 바뀐 id 20. 대기열 변화 없음. 백업: articles_radar_naver.backup_before_update_cons_20261118.json, content_queue_radar_naver.backup_before_update_cons_20261118.json
- 이미 만든 최종본은 [최종본 만들기]를 다시 눌러야 반영됨

## 완료 (2026-11-18): 배너 410 · 서명 1800×135 판 · 정렬 시험 파일 · 원고지침 v3.1 · 원고 20편 아쉬운 점 고침
- 배너: 업로드 폴더 배너 가로 410(약 410×62, LANCZOS). config.RADAR_BANNER_WIDTH=410, radar_screen.banner_small_copy. 원본 banner_naver_price.png(1798×272)는 그대로, ③ 화면도 410 그대로(본문 폭 약 51%)
- 서명: static/radar/sign_deskpicker.png = 1800×135 흰 판 + 서명 360×108 오른쪽 끝(여백 36). 옛 800×60 은 sign_deskpicker_800x60_old.png. tools/radar_sign_make.py 기본 C. ③ 화면은 판을 본문 폭 꽉 차게 보임. 참고: static/ 은 git 제외
- 정렬 시험: preview/align_test_20261118/align_test.html (A~D 4방식, 문단 양끝·소제목 왼쪽). 네이버에 붙여 넣은 결과를 보고 다음 지시문에서 앱 수정. 메모: 네이버는 붙여 넣은 정렬을 무시함 — align_test 결과 대기
- tools/radar_rules.md v3.1(아쉬운 점 1~2개·대처법, 불량·낮은 평점 묶음·가격 불만 금지, 별점 띠 400×75·표지 800×800)
- 원고 20편(pipeline/radar_drafts/2026-11-23/) 아쉬운 점만 고침, 백업 _backup/2026-11-23_before_cons/. radar_check 위반 0. radar_import --update 미리보기: 20편 모두 '고친 판 있음'(적용 안 함). 대기열·articles 수정 없음
- 남은 일: 메모 [사진자리] 장단점 카드 문구가 본문과 어긋난 글(1123_2·1125_2·1126_2·1126_3·1127_2·1128_2·1128_3·1129_2·1129_3 등), 1128_3 소제목 '불량', 1128_2 대안 구간 '고장 후기' 문장, 1127_2·1126_3 결론 구간 문장 — 지시 범위 밖이라 그대로 둠
- reviewer 생략(radar_naver 전용 파일만). 코드 고치기 전 커밋 f3844c2, 코드 커밋 0326b8f

## 완료 (2026-11-18): "네이버에서 가격 확인하기" 배너 새 파일 하나로 통일 · 가로 폭 맞춤
- 배너: static/radar/banner_naver_price_v3_20261118.png(1798×272, 투명) → static/radar/banner_naver_price.png 로 복사해 씀. 모든 카드에 이 한 파일만(흰·검정 번갈아 없앰: radar_screen.banner_path/banner_order·banner_small_copy 제거, config RADAR_BANNER_START/WIDTH 제거). 옛 banner_white/black.png 는 지우지 않음
- 업로드 폴더에는 크기 줄이지 않고 1798×272 그대로(05_배너_가격확인.png). ③ 화면은 배너를 본문 폭(800, 화면에선 708px)에 맞춰 보임. 앱에는 네이버 링크카드 폭 정보 없음(링크는 주소 글자 줄, 카드는 네이버가 만듦). 크기 경고에서 배너는 계속 뺌
- 서명 미리보기 preview/radar_sign_20261118/preview_pc.png·preview_mobile.png, 캡처 preview/radar_banner_20261118/capture_banner.png, 복사 HTML naver_copy_banner.html (shoot_banner.py, shoot_sign_preview.py). 앱 데이터·원고 수정 없음, reviewer 생략(radar_naver 배너 코드만)
- 참고: 코드 고친 뒤에 커밋함(고치기 전 커밋을 놓침, 시작 때 git 은 깨끗했음)

## 완료 (2026-11-18): 글 끝 서명 가로 800 판(B 160×48) 적용 · 배너 새 시안 4개(앱 적용 안 함)
- 서명: static/radar/sign_deskpicker.png = 800×60 흰 판 + 오른쪽 끝(여백 16)에 시안3(Shadows Into Light, 금색 밑줄) 160×48 직접 그림(B). 120×36 옛 파일은 sign_deskpicker_120x36_old.png. 서명 📷 줄은 가운데 정렬(SIGN_STYLE). 만든 스크립트 tools/radar_sign_make.py [A|B]. 참고: static/ 은 git 제외라 그림 파일은 드라이브로만 동기화됨
- 시안 A(120×36)/B 미리보기·비교: preview/radar_sign_20261118/ (비교_서명800.png), 캡처 capture_sign_800.png, 복사 HTML naver_copy_sign_800.html
- 표지 실제 크기 800×800(1080 아님) → 크기 경고 기준 그대로 둠
- 배너: 지금 파일 static/radar/banner_white.png·banner_black.png (1600×242, N데스크피커 전용, 올릴 때 가로 430으로 줄임). 새 시안 A~D(+Light/Regular 둘 다) preview/radar_banner_20261118/ (비교_배너.png), Pretendard Light·Regular + OFL 는 fonts/. 앱 적용 안 함, 만든 스크립트 tools/radar_banner_mockup.py
- 앱 데이터·원고 수정 없음, reviewer 생략(N데스크피커 전용 파일 + radar_screen 안 서명 줄만)

## 완료 (2026-11-18): 글 끝 서명(시안 3 · Shadows Into Light · 금색 밑줄) 앱에 넣기 · 업로드 크기 경고 종류별 기준
- 서명 그림: static/radar/sign_deskpicker.png (120×36, 흰 배경, 고정 파일) / 만든 스크립트 tools/radar_sign_make.py (120×36 판에 바로 그림, 결 세기 0.3으로 약하게) / 글꼴·라이선스 static/radar/fonts/ShadowsIntoLight.ttf + OFL_ShadowsIntoLight.txt (SIL OFL 1.1)
- 위치: 본문 마지막 문단 → 빈 줄 → 서명(오른쪽) → "함께 보면 좋은 글". ③ 최종본·네이버용 복사엔 📷 NN_서명.png 오른쪽 정렬 한 줄, 업로드 폴더에 같은 이름 파일(본문 사진 번호엔 안 셈, 별점 띠와 같은 방식)
- ② 화면 "글 끝 서명" 칸: [안 넣음]/[다시 넣기](/radar/desk/signskip, radar_screen.set_sign_skip, photos.json 의 skip_sign, 기본 넣음)
- 크기 경고 종류별(radar_screen.size_rule/size_check): 본문 사진·표지 800×800 / 표 그림 가로 800(세로 자유) / 별점 띠 400×75 / GIF·서명·필수 표기·배너는 검사 안 함. 다르면 종류와 기준 크기를 문구에 적어 경고(③ 사진 아래·업로드 목록 위). 옛 코드는 한 줄 주석 안에 is_notice/is_banner 값이 들어가 있던 것도 고침
- 이미 만든 최종본은 [최종본 만들기]를 다시 눌러야 서명이 들어감. 앱 데이터·원고 수정 없음, reviewer 생략(N데스크피커 전용 파일만)
- 캡처 preview/radar_sign_20261118/capture_sign_final.png, 복사 HTML naver_copy_sign.html, 시험 스크립트 shoot_sign.py (복사본 데이터만 사용)

## 완료 (2026-11-18): "함께 보면 좋은 글" 조사(보고만) · 글 끝 서명 "Desk Picker" 시안 3개
- 조사: 앱 코드·데이터 안 바꿈. 글 끝 = 본문 끝 → "함께 보면 좋은 글"(링크 0~2개, 주소 줄만) → 끝(출처는 화면 아래 칸, 쇼핑커넥트 안내는 표지 바로 아래 위쪽). radar_screen.pick_related / config.RADAR_RELATED_SINCE=2026-11-23. 이미 낸 글에 나중 글 링크 채우는 기능 없음(최종본 만들 때만 계산)
- 서명 시안: preview/radar_sign_20261118/ (시안 3 × 밑줄 있음/없음 6장 + _투명 6장 + 비교_한눈에.png + 본문끝_미리보기_시안1.png), 글꼴 fonts/ (Caveat·Nanum Pen Script·Shadows Into Light, 모두 SIL OFL 1.1 + 라이선스 파일). 만든 스크립트 tools/radar_sign_mockup.py. 앱에는 적용 안 함(고르면 다음 작업)

## 완료 (2026-11-18): ③ 별점 띠 그림 50% · ### 제품 소제목 글자 200%
- radar_images.py: 별점 띠 그림 800×150 → 400×75 (별 56→28, 간격·글자도 절반, 작게 새로 그림). radar_screen.py: ### 글자 17px → 34px (굵기·왼쪽·금색 막대 그대로). ❝ 왼쪽 정렬은 앞서 이미 적용돼 있어 건너뜀.
- 별점 띠는 [최종본 만들기] 때 다시 그려져 새 크기로 바뀜(복사본 시험 확인). 이미 만든 글은 최종본을 다시 눌러야 반영
- 금색 막대(약 68px)가 34px 글자보다 짧음 → 길이 조정은 안 함(보고만)
- 캡처 preview/justify_20261118/capture_badge_title.png, 복사 HTML naver_copy_badge_title.html (shoot_badge_title.py)

## 완료 (2026-11-18): ③ 소제목 위 따옴표(❝) 왼쪽 정렬
- radar_screen.py 의 _left_headings 목록에 ❝ 줄 한 줄 추가(radar_naver 최종본 만들 때만 적용, 공유 파일 안 건드림). 본문 양끝·사진·카드·별점 띠 가운데는 그대로. 앱 데이터·원고 수정 없음
- 캡처: preview/justify_20261118/capture_quote_left.png, 복사 HTML: naver_copy_quote_left.html (shoot_quote.py)
- 이미 만든 최종본은 [최종본 만들기]를 다시 눌러야 반영

## 완료 (2026-11-18): 11/23 주 새 원고 4편 앱에 넣기 (radar_import --apply, --update 없이)
- 백업: articles_radar_naver.backup_before_import1123b_20261118.json, content_queue_radar_naver.backup_before_import1123b_20261118.json (radar_import 가 자동 백업 articles_radar_naver.backup_before_radar_import_20261118_211822.json 도 만듦)
- 미리보기 재확인: 새 4편뿐(1126_1 f3d57ac1, 1126_3 37b5083e, 1128_1 ab88ea7f, 1128_2 9244ea99) → --apply
- 결과: 글 수 85 → 89, 백업에만 있는 id 0, 새 id 4(모두 2026-11-23_), 바뀐 id 0

## 완료 (2026-11-18): 원고 넣기 미리보기 · ② GIF '안 넣음' · ③ 양끝 정렬/문단 나눔 · radar_rules v3.0
- 미리보기: radar_check 20편 위반 0. radar_import 미리보기 = 새로 4편(1126_1 발난로 f3d57ac1, 1126_3 PMA2X 37b5083e, 1128_1 무드등 ab88ea7f, 1128_2 툴콘 9244ea99), 16편 이미 있음·같음. 20편 모두 제목·발행 예정일(파일 MMDD) 일치. --apply 는 안 함(사용자가 결정)
- GIF: ② 상품별 GIF 칸마다 [안 넣음]/[다시 넣기](/radar/desk/gifskip, radar_screen.set_gif_skip). photos.json 의 skip_gifs 에만 저장. 안 넣음이면 ③ 최종본·naver_upload 에서 빠지고 GIF 파일은 안 지움. GIF 0개여도 ③ 로 감. 틀 B 본상품만 빼고 대안만 남으면 기존 노란 경고
- 정렬: config RADAR_ALIGN="justify", RADAR_LINE_BREAK="paragraph"(문단 안 <br> 없음, 150자 넘으면 가장 가까운 문장 끝에서 둘로, 30자 미만 조각이면 안 나눔, RADAR_SPLIT_LEN/MIN). 소제목(##·###)과 그 아래 줄·막대는 왼쪽, ❝·사진·표·별점 띠·상품카드는 가운데. 옛 방식은 config 두 값(center, sentence)으로 되돌림. 이미 만든 최종본은 [최종본 만들기]를 다시 눌러야 새 모양
- 시험·캡처: preview/justify_20261118/ (capture_before.png, capture_after.png, naver_copy_*.html, run_test.py 14개 통과). 커밋 73a0de9, reviewer 검수 문제 없음
- radar_rules.md v3.0 커밋 fc4d63d. radar-writer 에이전트 설명엔 옛 줄바꿈 지시가 없어 그대로
- 남은 참고: 150자 넘게 남는 문단 17개(나누면 30자 미만 조각이 생기는 경우). 실제 네이버 편집기에서 양끝 정렬이 유지되는지는 붙여 넣어 봐야 확인 가능

## 완료 (2026-11-18): ② 사진 자리 '안 넣음' · radar_rules v2.9 · 원고 폴더 백업 이동
- 백업: pipeline/radar_drafts/2026-11-23/1128_1_A_크리스마스-무드등.backup_before_price_cell_20261118.md → pipeline/radar_drafts/_backup/2026-11-23/ (지우지 않음). 원고 폴더 20개 모두 이름 규칙에 맞고, 같은 MMDD_순번 중복 없음
- 앱: ② 사진 칸마다 [안 넣음]/[다시 넣기](/radar/desk/skip, radar_screen.set_slot_skip). '안 넣음' 목록은 사진 폴더 photos.json 의 skip_slots 에만 저장(원고·대기열·articles 안 바뀜). 빼면 ③ 최종본과 naver_upload 에서 📷 줄·사진이 빠지고 번호는 이어짐. 표 자리를 빼면 ③ 노란 안내, ③ 에 '본문 사진 n장'. 사진을 하나도 안 골라도 ③ 까지 감(막는 문구 없음). 제품 사진을 빼도 ###·별점 띠·상품카드는 그대로
- 시험: preview/skip_slots_20261118/(복사본 데이터·photos_root, 실제 사진 폴더 안 건드림), 캡처 capture_slots_skip.png · capture_final_skip.png
- tools/radar_rules.md v2.9(맨 위 '원본' 줄·제목·무엇이 바뀌었나·10장 2줄). radar_check 는 사진 장수를 검사하지 않아 그대로
- 벨킨 805575695634997 옛 GIF: 안 지움. GIF 는 ② [GIF 가져오기] 버튼을 눌러야만 들어가고 자동으로 고르는 코드가 없어 손대지 않음
- 대기열·앱 데이터·radar_import 는 안 건드림

## 다음에 고칠 것 (원고 백업 위치)
- 앞으로 원고 백업은 원고 폴더(pipeline/radar_drafts/<날짜>/) 밖 pipeline/radar_drafts/_backup/<날짜>/ 에 만든다(원고 폴더에 다른 파일이 있으면 radar_import 가 그 글을 못 넣을 수 있음)

## 완료 (2026-11-18): 표지 시안 A 기본값 · GIF 만들기 · radar_rules v2.8 · 무드등 가격 칸
- 표지: radar_images.py 기본값을 시안 A(사진 칸 72%·제품 90%·띠 글자 아래 0.75)로. FLIP7(03.jpg) 표지가 preview/radar_cover_v3_20261118/A72_틀B_FLIP7.jpg 와 픽셀 단위로 같음 확인. 이미 만든 cover.jpg 는 안 건드림(11/23 주 글 중 있는 것: 맥세이프 보조배터리(1123_1), JBL FLIP7(1125_2) — static/generated/생활레이더/). 다시 만들려면 앱 [표지 다시 만들기]
- GIF: 11/23 주 원고 상품 중 GIF 없던 것을 research/_img/<id>/ 에 만듦(0원). 자동 판정이 놓친 뱃지는 각 폴더 gif_exclude.json 에 적어 빼고 만듦(FLIP7 01_rep, 오디세이·삼성 보조배터리 01_rep, 엘레센스 01_rep(브랜드 글자), GO5 01_rep 포함). GO5 옛 GIF 는 *.backup_before_gifexclude_20261118.gif 로 남김
- 못 만든 것(사진 부족 4장 미만): 인터픽셀 838097298645856, 툴콘 808791888418528, 듀오백 832388780328226, 신일939 810435176635939, 로지텍 G304 1011857485397408(5장 중 4장에 공식판매점 뱃지·로고). 벨킨 805575695634997 은 옛 GIF 가 있으나 사진 전부에 belkin 로고(+2년무상 A/S 뱃지)라 gif_exclude.json 에 전부 적어 둠 — 옛 GIF 는 안 지움, 쓸지 사용자가 결정
- 앱: ② '상품별 GIF' 칸에 본상품/대안 표시와 [GIF 만들기(0원)] 버튼(radar_screen.make_product_gif, /radar/desk/gifmake), ③ 틀 B 본상품 GIF 없이 대안 GIF 만 있으면 노란 경고(main_gif_warning). 수집은 이제 GIF 기본 켜짐(--no-gif 로 끔), 브라우저 없이 GIF 만: collect_research.py --run <실행> --gif-only --ids <id...>
- tools/radar_rules.md v2.8(맨 위 제목·무엇이 바뀌었나·10장 GIF 줄·11장). 1번째 줄 '원본: ... v2.7' 은 지시대로 안 바꿈
- 무드등 1128_1: 표의 '옵션별 가격은 판매 페이지에서 확인' 칸을 '확인 필요'로(두 옵션 줄 2칸 모두), 백업 *.backup_before_price_cell_20261118.md, radar_check 위반 0
- 대기열·앱 데이터·radar_import 는 안 건드림. reviewer 검수는 안 함(요청 시 가능)

## 완료 (2026-11-18): ② 표지 문제 3가지 — 1·2번 원인 확인, 3번 표지 사진 고르기 칸 추가, 4번 배치 시안 6장(앱 적용 안 함)
- 1번 FLIP7 GIF: research/_img/825399844522912/ 에 825399844522912.gif 가 한 번도 안 만들어짐(수집 때 --gif 안 줌, 장 부족 아님: 10장 모두 판정 통과). 앱은 파일이 없으면 'GIF 없음'. 고치지 않음.
- 2번 표지 뱃지: tools/collect_research.py detect_badge 가 이 뱃지를 못 잡음(뱃지 파란 부분이 잘게 나뉘어 최소 크기 0.6% 미만으로 버려지고, 흰 윗부분은 하늘과 이어져 큰 덩어리가 됨). rep_badge.json 에 뱃지 false 로 기록돼 표지에 그대로 씀. 고치지 않음.
- 3번: radar_screen.py(choose_cover_photo·cover_pick), app.py(/radar/desk/coverpick), templates/article_edit.html. 고른 경로는 photos.json 의 cover_pick 에만 저장. 틀은 대기열 planned_type. 커밋 d2f20cb. 캡처 preview/radar_cover_v3_20261118/capture_cover_pick_FLIP7.png(복사본 폴더에서 시험, 실제 표지 파일·대기열·articles 안 건드림).
- 4번: radar_images.make_cover 에 시안용 옵션 3개(top_ratio·photo_fill·band_shift, 기본 꺼짐, 안 주면 기존 표지와 픽셀 동일 확인). 앱은 안 씀. 그림 6장+비교_한눈에.jpg: preview/radar_cover_v3_20261118/, 만든 스크립트 make_v3.py
- 표지·② 화면 코드(radar_screen·radar_images)는 N데스크피커 전용이라 다른 블로그와 공유 없음, reviewer 검수는 안 함(요청 시 가능)

## 완료 (2026-11-18): 11/23 주 원고 2편(툴콘·무드등) 사실 고침 + 레토 평점 확인
- 레토 상품 페이지 값: 평점 4.89, 리뷰 56건(research 의 '4.8956 / 8956'은 '4.89'+'56건'이 붙어 읽힌 오류). research/867056176088282.md 고침, 백업 *.backup_before_rating_20261118.md. 다른 research 파일은 평점 소수 셋째 자리 이상 없음
- 툴콘 1128_2: 1단 500W 모두 뺌(소비전력은 800W(2단 풍량 조절)만). 무드등 1128_1: 회전목마 지붕 값 뺌(바닥 120·높이 250만), 회전목마 가격은 페이지에서 옵션별 가격을 구분할 수 없어 뺌(표에 '옵션별 가격은 판매 페이지에서 확인'). radar_check 위반 0
- 대기열·앱 데이터·radar_import 는 안 건드림

## 완료 (2026-11-18): 크리스마스 무드등 2개 사양 다시 확인 + 원고 1128_1 씀
- 레토·회전목마 사양은 상세 이미지에 있었음(research/<id>.md 끝 "## 보강 (11/18-4)", 캡처 research/_img/<id>/spec_*_1118.jpg, 백업 *.backup_before_bogang4_20261118.md). 회전목마 '워터볼'은 같은 페이지의 별도 옵션(+6,000원), 승인 상품은 깃발 옵션이라 표에 두 옵션 모두 적음
- 원고 pipeline/radar_drafts/2026-11-23/1128_1_A_크리스마스-무드등.md, radar_check 위반 0. 대기열·앱 데이터·radar_import 는 안 건드림
- 확인 필요: 회전목마 워터볼 지붕 높이(회색 칸 15cm vs SIZE INFO 표 25), 가격(페이지 68,900원 vs 승인 목록 58,900원)

## 다음에 고칠 것
- 자료 수집 때 상세 이미지는 끝까지, 페이지 맨 아래 '상품 정보 고시' 표(글자 칸)까지 읽는다 — 11/18 무드등 2개를 '표기 없음'으로 잘못 판단함

## 완료 (2026-11-18): 보류 글 3편 사양 확인, 카멜 PMA2X·툴콘 TP-800D 원고 씀(주파집은 원고 안 씀)
- 사양 값·출처는 research/<상품id>.md 끝 "## 보강 (11/18-2)", 백업 *.backup_before_bogang2_20261118.md. 카멜 27kg·57형·클램프 10~45mm(다나와), 툴콘 800W·250x142x107(가로·세로·깊이)·730g(다나와), 1단 500W 는 출처 없음(확인 필요)
- 원고: pipeline/radar_drafts/2026-11-23/1126_3_B_카멜-PMA2X.md, 1128_2_B_툴콘-TP-800D.md, radar_check 위반 0. 대기열·앱 데이터·radar_import 는 안 건드림

## 완료 (2026-11-18): 보류 글 2편 중 1편(1126_1 사무실 발난로) 원고 씀, 1편(1128_1 크리스마스 무드등) 자료 부족으로 멈춤
- 링크 새로 발급 3개(오아 864964696315232·신일 810435176635950·회전목마 813029024527988), links.json 갱신. 레토·신일 939 는 원래 있음
- 자료 수집 3개 새로 받음(research·_raw·_img, 상세 이미지 _detail 에 레토·회전목마 포함). 후기 요약(문장, 11/18) 3개 추가. 백업 *.backup_before_bogang3_20261118.md (레토는 보강 줄 추가 뒤 만든 백업이라 보강 줄만 뺀 상태)
- 발난로 원고 pipeline/radar_drafts/2026-11-23/1126_1_A_사무실-발난로.md, radar_check 위반 0. 크기·전력: 신일 939(800W·310x145x375) 상세 이미지, 오아(600W·235x290x210) 다나와. 신일 950(파티션형)은 값 없어 원고에서 뺌
- 무드등은 안 씀: 회전목마·레토 모두 크기·전원 방식 표기 없음(전원은 사진·후기뿐). 자료 보강 요청: 크리스마스 무드등 크기, 전원 방식(건전지/USB). 대기열·앱 데이터·radar_import 는 안 건드림

## 이어갈 것 (2026-11-18): 11/23 주 16편 넣기 완료(11/18, 15 신규 + 1123_1 교체)
- 다음: 사진·최종본(사용자) → 11/23부터 발행. 11/26·11/28 은 1편씩.
- 이번 주 안 쓰는 글(대기열 그대로): 주파집 AD500S, 카멜 PMA2X, 툴콘 TP-800D, 크리스마스 무드등·사무실 발난로(제품 추가 대기)

## 완료 (2026-11-18): 멈춘 글 9편 중 5편 다시 씀, 이번 주 안 쓰는 글 5편(남은 4편 + 범위 밖 주파집)
- 다시 쓴 원고(pipeline/radar_drafts/2026-11-23/): 1126_2 로지텍 G304, 1127_1 보조배터리 20000, 1127_3 오디세이 G5(제목 165Hz로 바꿈), 1129_1 키보드 손목받침대(제목 바꿈), 1129_2 앤커 P31i. 이후 1125_1 책상 발받침대도 써서 16편 모두 앱에 넣음(위 "이어갈 것" 참고)
- 이번 주 안 쓰는 글 5편 (대기열에서는 지우지 않고 그대로 둔다):
  - 주파집 AD500S — 범위 밖
  - 카멜 PMA2X — 하중 값 없음
  - 툴콘 TP-800D — 소비전력·크기 없음
  - 크리스마스 무드등 — 틀 A 인데 확정 상품 1개(제품 체크에서 더 체크하면 쓸 수 있음)
  - 사무실 발난로 — 틀 A 인데 확정 상품 1개(제품 체크에서 더 체크하면 쓸 수 있음)
- 보강(11/18) research 7개 파일 끝에 추가, 백업 *.backup_before_bogang_20261118.md. 대기열 제목 변경 백업 content_queue_radar_naver.backup_before_title_change_20261118.json

## 완료 (2026-11-18): 11/23 주 원고 — 1124_1 다시 쓰기·JBL GO5 문장 고침·3~7일 차 시도(쓴 것 5편, 멈춘 것 9편+1)
- 원고 위치 pipeline/radar_drafts/2026-11-23/ (10편, radar_check 위반 0, radar_import 미리보기 위반 0). 앱 데이터·발행·링크는 안 건드림
- 멈춘 글(자료 부족): 1125_1 책상 발받침대(프렌디아 크기), 1125_3 주파집 AD500S(범위 확인 필요), 1126_1 사무실 발난로(전력·크기), 1126_2 로지텍 G304(무게·배터리 시간), 1126_3 카멜 PMA2X(하중), 1127_1 보조배터리 20000(포켓쉴 출력), 1127_3 오디세이 G5(제목 180Hz↔자료 165Hz), 1128_1 크리스마스 무드등(확정 상품 1개뿐), 1128_2 툴콘 TP-800D(크기·전력), 1129_1 손목받침대(제목 젤 vs 메모리폼 근거 없음), 1129_2 앤커 P31i(배터리 시간)
- 상세 이미지: 18개 상품 research/_img/<상품id>/ 받음(후기 요약 든 research 파일은 건드리지 않는 방식). 원고 2편(E1133·IP2740)의 '캡처 필요' 줄을 실제 파일 경로로 바꿈
- 다음: 멈춘 글 자료 보강(상세 이미지 읽어 detail_values.json 채우기) 또는 제목 확정 후 다시 쓰기. radar_import --apply 는 사용자 결정

## 완료 (2026-11-18): 11/23 주 후기 요약 — 상품 26개 전부 research/<id>.md 에 '후기 요약 (문장, 11/17)' 있음(24개 새로 만듦, 2개는 원래 있음). 원고는 안 씀
- 보류 원인: radar_import 는 대기열 키워드의 picks 로 상품카드를 비교해서, 원고의 '대안' 카드(JBL FLIP7, 카멜 PMA2X)가 위반 처리됨. 고치지 않음

## 완료 (2026-11-18) — N데스크피커 별점 띠 자동 삽입 (원고지침 v2.6)
- 바뀐 것: radar_images.py(별점 띠 그림), radar_screen.py(값 읽기·③ 최종본·업로드 폴더·경고 표시), app.py(② 열 때 띠 만들기·③ 경고 전달), templates/article_edit.html(② 별점 띠 칸)·final_view.html(노란 경고), tools/radar_rules.md(v2.6)
- 앱을 껐다 켜야 반영. 시험 그림 preview/radar_badge_20261118/, 시험 3편 캡처 pipeline/drafts_test_1123/ (실제 데이터·대기열은 안 건드림)

## 다음 작업자가 바로 이어갈 것 (11/17 마무리)
- 오늘 끝난 것: 원고지침 v2.4·v2.5 반영, 시험 3편 다시 쓰기, radar_check 표 칸 검사(k·l) 추가, 대안 후보(alt_candidates)·브랜드 묶음 표 추가
- 하다 만 것: 맥세이프 비교표(확정 상품 3개뿐), 사진 준비·대기열 넣기, 후보 수집 전체 실행은 안 함
- 앱 코드 바뀐 것이 있으니 앱을 껐다 켜야 반영됨. 실제 데이터 파일은 건드리지 않음
- 내일 첫 할 일: 맥세이프 상품 보충 여부 정할 것 → 시험 원고 3편 사진 준비 후 radar_import 미리보기

완료 (11/17): tools/product_candidates.py 에 대안 후보(alt_candidates) 추가 + tools/brand_alias.json(같은 브랜드 묶음 표). 가짜 자료로 조건 시험 통과, 카테고리 읽기 2건 실제 시험 통과. 실제 후보 수집 전체 실행은 안 함

완료 (11/17): 원고지침 v2.4 반영·시험 3편 다시 씀(pipeline/drafts_test_1123/). radar_check 폴더 전체 위반 0. 앱 데이터·radar_import·발행·링크 발급은 안 함
남은 것: 맥세이프 비교표는 확정 상품이 3개뿐이라 3개 제품(지침 4~5개). 사진 준비·대기열 넣기는 아직


## 완료 (2026-11-17) — radar-writer 서브에이전트 + 시험 3편
- 바뀐 것: .claude/agents/radar-writer.md(제목 그대로·파일 이름 규칙), pipeline/drafts_test_1123/ 원고 3편(맥세이프 A, JBL GO5 B, 카멜마운트 SMB35 B). radar_check 위반 0
- 남은 것: JBL GO5 는 방수 자료 없음(제목 제안 있음). 앱 대기열 넣기·사진 준비·발행은 하지 않았음

## 완료 (2026-11-17) — 표 그림 업로드 크기 검사 예외·② 열 때 표 그림 자동 만들기
- 바뀐 것: radar_screen.py(upload_listing 가로 800이면 통과, auto_table_images 새로 추가), app.py(_radar_desk_view 가 ② 열 때 auto_table_images 호출). 글 사진 폴더에만 씀
- 남은 것: 앱 껐다 켜기. radar_import 는 읽기만 보고(고치지 않음)

## 완료 (2026-11-17) — radar_rules.md v2.2 교체·표 그림 ② 연결(가로 800)·표 검사 추가
- 바뀐 것: tools/radar_rules.md(v2.2), radar_images.py(table_image 가로 800·세로 표 길이대로·10행 초과 경고), radar_screen.py(table_slot_map·make_table_image_file, ③에서 표 그림 있으면 HTML 표 생략), app.py·templates/article_edit.html(표 그림 만들기 버튼), tools/radar_check.py(j 항목: 표 개수·표 아래 [사진: n]·10행 경고)
- 남은 것: 앱을 껐다 켜야 새 코드 적용. 실제 글에서 ②의 '표 그림 만들기'를 눌러 보고 ③ 다시 만들기. 옛 naver_upload 는 다시 만들어야 반영

## 완료 (2026-11-17) — radar_rules.md v2.1 교체·제목 단계 규칙 읽기 고침·지점 위반·형광펜 2~3곳
- 바뀐 것: tools/radar_rules.md(v2.1 원문+맨 위 "원본" 한 줄), tools/keyword_pipeline.py(rules_section 이 「문장 쓰는 법·제목·피할 표현」 소제목으로 찾고 못 찾으면 오류), tools/radar_check.py(형광펜 2~3곳), tools/radar_banned.json(지점 → 위반). 앞 주의(공통·제목 칸 없음)는 해결됨
- 남은 것: 표 그림을 ② 사진 단계에 연결(계획만 보고, 코드 안 고침) — 시험 원고 6개 위반 합계 7→11(형광펜 1곳짜리가 새로 걸림)

## 완료 (2026-11-17) — radar_rules.md v2.0 교체·상품카드 검사 범위 좁힘·테두리 2배
- 집 PC 에서 커밋 필요: radar_images.py, radar_screen.py, tools/radar_gif.py, tools/radar_check.py, tools/radar_import.py, tools/radar_rules.md (이 PC 커밋 fb24ea9·a17a62d·058a813). 근거 preview/radar_rules_v2_20261117/verify.md
- radar_import 사용법: ① 미리보기 `python -I tools/radar_import.py pipeline/radar_drafts/<주 시작일>` (아무것도 안 고침) ② 넣기 `… --apply` (새 원고만, 백업 후) ③ 고친 원고 바꾸기 `… --update --apply` (이미 있는 항목, 백업 후)
- 주의: tools/keyword_pipeline.py 가 radar_rules.md 의 [공통]·[제목] 칸을 읽는데 새 파일엔 없음 — 제목 단계 돌리기 전에 확인

## 완료 (2026-11-17) — N데스크피커 ⑫ ③ 화면용/복사용 최종본·형광펜 조건·가격 확인 배너
- 집 PC 에서 커밋 필요: app.py, config.py, radar_screen.py, templates/final_view.html (이 PC 커밋 70081a7). 근거 preview/editor_radar_step12/verify.md
- 남은 것: ③ 다시 눌러 새 방식 적용, 배너 링크는 네이버에서 직접 넣기. 끄기: config RADAR_BANNER=False

## 완료 (2026-11-17) — N데스크피커 ⑪ 필수 표기 14px 줄이기·숫자+단위 자동 굵게·결론 자동 형광펜
- 집 PC 에서 커밋 필요: config.py, radar_screen.py, templates/final_view.html (이 PC 커밋 c6e5932). 근거 preview/editor_radar_step11/verify.md
- 남은 것: compare.html·paste_test.html 눈으로 확인, ③ 다시 눌러 새 방식 적용. 끄기: config RADAR_AUTO_EM_NUMBERS / RADAR_AUTO_HL_CONCLUSION

## 완료 (2026-11-17) — N데스크피커 ⑧ 필수표기 이미지·제품 소제목(###)·강조 글자색/형광펜·가운데 정렬·사진 800×800 통일
- 집 PC 에서 커밋 필요: app.py, config.py, radar_screen.py, templates/final_view.html (이 PC 커밋 5467f71). 근거 preview/editor_radar_step8/verify.md
- 남은 것: 사람이 paste_test.html 로 네이버 붙여넣기 시험, 대상 글 원고에 ### 제품 소제목 넣기, ③ 다시 눌러 새 방식 적용. 필수 표기 이미지는 static/radar/notice_shoppingconnect.jpg.jpg (git 에 안 올라감)

## 완료 (2026-11-17) — N데스크피커 ⑦ 복사: 제목 빼기·문장마다 줄바꿈·굵게/형광펜 강조·카테고리/태그 칸·제목 복사
- 집 PC 에서 커밋 필요: app.py, config.py, radar_screen.py, templates/final_view.html (이 PC 커밋 264794c). 근거 preview/editor_radar_step7/verify.md
- 남은 것: 사람이 paste_test_style.html 로 네이버 붙여넣기 시험(굵기·형광펜), 대상 글 ③ 다시 눌러 새 방식 적용, config RADAR_CATEGORY_MAP 을 실제 카테고리 이름으로

## 완료 (2026-11-17) — ③ 번호 붙은 업로드 폴더(naver_upload)·업로드 폴더 열기·GIF 갈래 plan.json 읽기·붙여넣기 시험 파일
- 집 PC 에서 커밋 필요: app.py, radar_screen.py, templates/final_view.html, tools/radar_gif.py (이 PC 커밋 8a709ce). 근거 preview/editor_radar_step6/verify.md
- 남은 것: 사람이 paste_test_datauri.html 붙여넣기 시험, 실제 글에서 ③ 눌러 naver_upload 만들기

## 완료 (2026-11-17) — ② 사진 화면 상품 이름표·묶음 + 이어맥·벨킨 캐러셀 받기·GIF
- 집 PC 에서 커밋 필요: radar_screen.py, templates/article_edit.html (이 PC 커밋 d94fc1f). 근거 preview/editor_radar_step5/verify.md
- 남은 것: 이어맥·벨킨 짧은 이름 비어 있음, 프레임 B 확인.

## 완료 (2026-11-17) — ④ 보완: 함께 보면 좋은 글 미래 날짜 제외 + 최종본 못 만들면 빨간 안내
- 집 PC 에서 커밋 필요: app.py, radar_screen.py, templates/final_view.html (이 PC 커밋 b32bf93). 근거 preview/editor_radar_step4b/verify.md

## 완료 (2026-11-17) — N데스크피커 ④ 발행 주소 칸 검사·함께 보면 좋은 글·상품카드 링크·표 굵게
- 집 PC 에서 커밋 필요: app.py, radar_screen.py, config.py, templates/final_view.html (이 PC 커밋 8055a69). 근거 preview/editor_radar_step4/verify.md
- 안 한 것: 옛 생활레이더 코드 정리, 실제 발행 글로 붙여넣기 확인. 기준 날짜는 config.py RADAR_RELATED_SINCE.

## 완료 (2026-11-17) — N데스크피커 ③ 최종본 새 방식
- 집 PC 에서 커밋 필요: app.py, radar_screen.py, templates/final_view.html (이 PC 커밋 fce5343). 근거 preview/editor_radar_step3/verify.md
- 안 한 것: 발행 표시·글 주소 칸·함께 보면 좋은 글, [상품카드] 링크 연결.

## 완료 (2026-11-17) — 편집기 ② 사진 화면 radar_naver 새 화면
- 바뀐 파일: app.py(길 5개+화면 값), templates/article_edit.html(radar 블록), radar_screen.py(새). 이 PC 커밋 e825654. 집 PC 에서 커밋 필요: 위 3개 파일. 근거 preview/editor_radar_step2/verify.md
- 안 한 것: ③ 최종본·발행 표시·함께 보면 좋은 글. 새 화면은 표지를 articles 에 안 써서 ③ 버튼 조건(covers.naver)은 다음 단계에서 맞춰야 함.

## 완료 (2026-11-17) — 맥세이프 출시연월 되살림 + 짧은 이름·갈래 채우기 + 표지·GIF 다시 만들기(코드 수정 없음)
- 실제 데이터(research 12개 파일) 바뀜·백업 *.backup_before_fill_20261117.*. 남은 것: 키보드 손목받침대 짧은 이름(모델명 없음). 근거 preview/badge_test4/verify.md

이 PC(TK-X1e) 정션 설정 완료 (C:\local-app-git -> %LOCALAPPDATA%\local-app-git, 2026-11-17)

## 완료 (2026-11-17) 뱃지 4개 추가 이미지 받기 + 뱃지 모아보기 + GIF 이름 버그
- 지금 PC 에서 커밋 필요 없음(이 PC 커밋 278e055·93902d7·10ef39a). 근거 preview/badge_test3/verify.md
- 주의: 맥세이프 보조배터리(821021110570951) research .md 가 바뀜(후기 재수집·출시연월 사라짐). 새로 받을 때는 --refresh-images --images-only 사용

## 완료 (2026-11-17) — 뱃지 판정 실제 반영 + 표지 기준 GIF와 맞춤
- 집 PC 에서 커밋 필요: radar_images.py, tools/collect_research.py (이 PC 커밋 428e4a5). 근거 preview/badge_test2/verify.md

## 완료 (2026-11-17) — 뱃지 있는 상품 이미지 GIF·표지에서 빼기
- 집 PC 에서 커밋 필요: tools/collect_research.py, tools/radar_gif.py, radar_images.py (이 PC 의 %LOCALAPPDATA%\local-app-git 에는 커밋함, C:\local-app-git 연결이 이 PC 에서 끊겨 있음)
- 근거 preview/badge_test/verify.md. 실제 images.json 갱신은 안 함(--rejudge-images 는 사용자가 실행). 편집기 연결 안 함.

## 완료 (2026-11-17) — N데스크피커 GIF 프레임 F2 확정 + 추가 이미지 수집(앱 코드 수정 없음)
- 바뀐 것: tools/radar_gif.py(F2·디더링 기본, 갈래·짧은 이름 글자), tools/collect_research.py·collect_research_ego.mjs(추가 이미지 10장·images.json·짧은 이름·--gif·--refresh-images). 사용: python -I tools/collect_research.py --run <실행> --ids <상품id> --gif (받은 상품은 이미지를 다시 받지 않음)
- 남은 것: 편집기(앱) 연결, 발행 글 주소 칸. 글자 많음·제품 보임은 자동 추정이라 틀릴 수 있어 research/_img/<id>/overrides.json 으로 고침. 근거 preview/radar_gif_final/verify.md

## 완료 (2026-11-17) — N데스크피커 상품 GIF 시범(앱 코드 수정 없음)
- 집 PC 에서 커밋 필요: tools/radar_gif.py(새 파일), preview/radar_gif_test/ (이 PC 의 %LOCALAPPDATA%\local-app-git 에는 커밋함)
- 결과·근거: preview/radar_gif_test/REPORT.md. 앱이 아직 radar_gif.py 를 불러오지 않음

## 완료 (2026-11-16) — 빈 사양 보충(3차): 상세 이미지에서 읽어 17개 중 10개 상품 보충, 벨킨·앤커·AULA 개별 보정. `--fill-detail` 옵션 추가(커밋 60ddf73). 근거 preview/research_1123/verify.md 맨 위 '3차'. 남은 것: 7개 상품은 상세 이미지에 사양 없음

## 완료 (2026-11-16) — 자료 수집 보완(평점 낮은순·공식 페이지 보충) + 26개 전체 수집: 성공 26/실패 0, 데이터 14개 해시 전후 같음. 근거 preview/research_1123/verify.md 맨 위. 결과 pipeline/candidates_run/20261116_radar_naver/research/

## 완료 (2026-11-16) — 제품 후보 도구 보완: 가격 없는 상품 표시 + 검색어 4개씩 나눠 실행·이어가기
- 바뀐 파일: tools/product_candidates.py, HANDOFF.md. 이 PC 저장소에 커밋함(작업 전 e3c0453). 근거 preview/candidates_fix/verify.md
- 같은 명령을 다시 실행하면 이미 받은 검색어는 건너뜀(같은 날짜·채널 폴더의 raw_naver_parts/).

## 완료 (2026-11-16): N데스크피커 11/23 주 대기열 넣기(run_id kw_radar_naver_20261123, 73→94) + 제품 후보 수집(pipeline/candidates_run/20261116_radar_naver). 알려진 문제: product_candidates.py 가격 없는 추천에서 summary 에러(미수정). 근거 preview/radar_push_1123/verify.md

## 완료 (2026-11-16) — N데스크피커 표지 시안 A 확정 + 시범 v2
- 결과: preview/radar_images_v2/ (verify.md · feed_preview.jpg · contact_sheet.jpg · 글별 cover_a.jpg). 다시 만들기: python tools/radar_images_trial.py (비용 0원)

## 완료 (2026-11-16) — N데스크피커 사진 모듈 시범(radar_images v1)
- 결과: preview/radar_images_v1/ (verify.md · cover_compare.jpg · contact_sheet.jpg · feed_preview.jpg · 글별 폴더). 데이터 12개 해시 전후 같음, 유료 API 0회.
- 다시 만들기: python tools/radar_images_trial.py (1초 안팎, 비용 0원). 다음: 표지 시안 고르기 → 원고지침(radar_rules.md)에 표지·사진 자리 적는 법 반영.

## 완료 (2026-11-16) — N데스크피커 제목 규칙 변경(끝맺음 6가지 돌려 쓰기) + 11-23 주 제목만 재생성
- 결과: preview/keyword_titles_mix/verify.md. 키워드·날짜·갈래·유형·search_query 전과 같음, 대기열에는 안 넣음(dry-run 까지). 새 제목 plan.json(옛것은 plan.backup_before_title_mix_20261116.json).
- 되돌리기: git log 의 '제목 끝맺음' 커밋(저장소 %LOCALAPPDATA%/local-app-git).
- 다음: 마음에 들면 python tools/push_plan.py --channel radar_naver --plan pipeline/keyword_run/2026-11-23_radar_naver/plan.json (dry-run) → --apply.

## 완료 (2026-11-16) — 네이버 데스크테리어 레퍼런스 조사(refblog2, 읽기 전용)
- 결과: pipeline/stage_refblog2_run/reference_report.md, preview/refblog2/verify.md. 데이터 12개 해시 전후 같음, 코드 수정 없음.

## 완료 (2026-11-16) — 자동화 3단계: N데스크피커 키워드·제목 발굴(tools/keyword_pipeline.py)
- 결과: preview/keyword_pipeline/verify.md. 결과 폴더 pipeline/keyword_run/2026-11-23_radar_naver/ (승인 21편, 부족 0, 대기열에는 안 넣음). 되돌리기 기준은 git log 의 'keyword_pipeline' 커밋(저장소 %LOCALAPPDATA%/local-app-git, 이 PC 에서 새로 만듦).
- 다음: 마음에 들면 python tools/push_plan.py --channel radar_naver --plan pipeline/keyword_run/2026-11-23_radar_naver/plan.json (dry-run) → --apply.

## 완료 (2026-11-15) — N데스크피커 제품 후보 재시험 + 링크 발급 도구(tools/issue_links.py)
- 결과: preview/issue_links/verify.md. 결과 폴더 pipeline/candidates_run/20261115_radar_naver/ (approved.json 은 시험 저장분 1개, links.json 에 실제 발급 1개). 되돌리기는 git log 의 '링크 발급' 커밋.

## 완료 (2026-11-15) — 제품 체크 도구 (tools/product_candidates.py + 앱 /product-check)
- 결과: preview/candidates/verify.md. 시험 결과 폴더 pipeline/candidates_run/20261115_naver_tips/ (approved.json 은 시험 저장분, 남겨 둠). 되돌리기는 git log 의 '제품 체크' 커밋.

## 완료 (2026-11-15) — 링크·후보 조사(읽기 전용)
- 결과: preview/link_probe/report.md, verify.md. 쇼핑커넥트·쿠팡파트너스 모두 리뷰 수 정렬 불가, 쇼핑커넥트만 리뷰 수·평점 표시. 코드·데이터 수정 없음.

## 진행 중 (2026-11-15) — 정리 1차(cleanup1)
- 단계: 1 백업 140개 archive 이동 → 2 꺼진 기능 걷어내기 → 3 화면 숨김(HIDDEN_FEATURES) → 4 확인. 근거 preview/cleanup_audit_20261115/
- 어디까지: 시작 커밋 'before cleanup1' 만든 직후. 단계마다 커밋. 되돌리기는 git log 의 'cleanup1' 커밋.
- 다음 명령: git log --oneline | head 로 마지막 cleanup1 단계 확인 후 이어서. 끝나면 preview/cleanup1_20261115/verify.md.

## 완료 (2026-11-15) — 로컬앱 개편 3단계 (화면 이름·순서 / 싼세계 기획 끄기 / 문서). 단, 2단계 5) 시험은 막혀서 못 끝냄
- 1단계(커밋 6a3f59a): 화면 이름 W혜택주머니·N살림노트·W싼세계·N데스크피커, 사이드바 순서 변경. 바꾼 파일 app.py, v4_board.py, target_keyword_finder.py(새 이름도 입력으로 받게 별칭 추가), weekly_plan_export.py, perf_tracker.py, templates 여러 개. 안쪽 채널 이름·데이터는 그대로. Flask GET 시험 통과, 외부 접속 0.
- 2단계(커밋 0b19a61): tools/filter.json 싼세계 0편·예비 5(원래 값은 wp_on_values 에 보관), weekly_plan.py --with-wp, check_plan.py 가 편수를 filter.json 에서 읽음, README_weekly.md 표시. 옛 경로 run_weekly.bat → run_weekly.py → plan_posts.py(assign_r1) 는 고치지 않음(같은 filter.json 을 읽음).
- 시험 막힘: 2026-11-23 주는 저장된 자료(캐시)가 모자라 네이버 API 가 필요함 → 외부 접속을 막고 돌려 실제 호출 0회. 결과 폴더 pipeline/stage_wpoff_test_run/ 은 fill 까지만(네이버 15·예비 0, 싼세계 0, 싼세계 판정 대상 0건). 21+5 확인과 check_plan 은 사용자 결정 대기.
- 3단계(커밋은 git log 의 '3단계'): CLAUDE.md 채널 표, SPEC_v2.md 맨 위 한 줄, 이 기록.
- 11/23 주 21+5 시험은 저장 자료 부족으로 못 함 → 11/20 실제 실행에서 확인 (11/16 주 재시험도 네이버 19편·예비 0 까지만, 판정 저장분 한계).


## 완료 (2026-11-15) — 혜택주머니 통일 표지 수정 fix1
- 결과: preview/lt_cover_unified_fix1_20261115/verify.md. 띠=카테고리, 글자 색=묶음별 7색 자동 배정(longterm_cover_accent.json은 실제 표지 만들 때 처음 생김). 실제 데이터 SHA 전후 동일, 수정 전 커밋 cf80c06

## 완료 (2026-11-15) — Desk Picker 시범 원고 3편 (trial_1116, 읽기·파일 저장만)
- 결과: pipeline/radar_drafts/trial_1116/ (A_발난로_고르는법, B_리웨이355, C_브라운BC02XW 각각 post·title·cover·img·facts·link, 검사 결과 check_result.txt/json, 원자료 _work/). 규칙은 tools/radar_rules.md, 검사 스크립트는 tools/radar_check.py. 블로그 글쓰기·발행·앱 대기열·앱 데이터는 안 건드림
- 사용: 쇼핑커넥트 링크 발급 4건(리웨이 355, 온아워, 신일, 브라운 / 한일 840은 이미 발급돼 있어 기존 링크 복사), 네이버 API 0회, Claude 호출 0회(원고는 이 대화에서 직접 작성). 이노크 i92는 오방형 스토브라 제외하고 B를 리웨이 355로 교체

## 완료 (2026-11-15) — 상품 수집 2차(범위 확대, 읽기만)
- 결과: pipeline/stage_desk_pick2_run/ (candidates2·shortlist2·pairs·seeds csv, raw_*.json 25개, 만든 스크립트는 scripts/). 씨앗 25개×200개 읽고 1차와 합쳐 필터 통과 1,306개→옵션 합쳐 1,009개(모델명 734·브랜드+종류 201·없음 74). 숏리스트 217개. 비교 짝 455개는 모두 검색량 '10 미만'. 링크 발급·글쓰기·앱 데이터는 안 건드림
- 사용: 검색광고 375회, 블로그 검색 700회(한도 끝, 브랜드+종류 145개는 미조회), 데이터랩 5회, Claude 2회

## 완료 (2026-11-15) — 데스크테리어 글감 후보 고르기 (읽기만)
- 결과: pipeline/stage_desk_pick_run/ (candidates·shortlist·shortlist_wide·radar_titles, 중간 파일 volumes·blog·claude_raw). 조건 통과 290개→옵션 합쳐 267개, 모델명 없음 47개. 운영 파일·앱 데이터는 안 건드림
- 사용: 검색광고 44회, 블로그 검색 150회(검색량 큰 순, 나머지 70개는 미조회), 데이터랩 0회, Claude 1회

## 완료 (2026-11-15) — 상품 후보 수집 시험(데스크테리어, 읽기만)
- 결과: pipeline/stage_desk_probe_run/ (products_raw.csv·keywords.csv·overlap.csv·raw_*.json·캡처 2장). 쇼핑커넥트 정렬은 '네이버 랭킹 순' 하나뿐이라 검색어마다 랭킹 상위 200개(책상스탠드 300개)를 읽고 후기 많은 순 20개를 골랐음. 링크 발급·운영 데이터·앱 코드 안 건드림
- 사용: 검색광고 12회, 데이터랩 3회, 블로그 검색 0회, Claude 호출 0회

## 완료 (2026-11-15) — 참고 블로그(xwnlmnzsa88592) 읽기 분석
- 결과: pipeline/stage_refblog_run/ (posts_list·blog_info·pattern·posts_detail·search_check). 읽기만 함, 운영 데이터·앱 코드 안 건드림
- 한계: 100편이 모두 같은 날이라 '날짜 다른 글 10편'은 시간대가 다른 글로 대신 고름. 카테고리 목록·전체 글 수는 확인 못함

## 완료 (2026-11-15) — 혜택주머니 표지 한 가지 모양으로 통일 (lt_cover_unified_20261115)
- 바꾼 것: image_gen.py 에 LT_COVER_UNIFIED 스위치(True=새 표지, False=옛 A·B·C), app.py 는 카테고리를 메모에서 읽고 longterm_cover_style.json 에 안 씀. 이미 만든 표지는 '표지 다시 만들기'를 눌러야 반영.
- 시험·결과: preview/lt_cover_unified_20261115/(contact_sheet.jpg, verify.md). 실제 데이터 SHA 전후 같음. 백업: *.backup_before_lt_cover_unified_20261115.py

## 완료 (2026-11-15) — 도입부 문단 나누기 최대 3문단으로 변경 (네이버·싼세계·혜택주머니)
- 바꾼 것: shop_formatter.py 의 나누기 규칙만(문장 1→1문단, 2~4→2, 5↑→3, 다만·하지만·그런데·그래서는 새 문단 첫머리), 싼세계·혜택주머니 문단 아래 여백 1.6em→1.2em. 네이버 빈 줄은 이미 한 줄이라 그대로. 저장된 최종본은 ③을 다시 눌러야 반영.
- 시험·결과: preview/intro_max3_20261115/(compare.html, verify.md). 실제 데이터 SHA 전후 같음, 생활레이더 출력 같음.

## 완료 (2026-11-14) — 혜택주머니(대표·세부) 도입부 문단 나누기
- 바꾼 것: seo_formatter.py 에 split_intro_raw·style_intro_paragraphs 추가(나누는 규칙은 shop_formatter.split_intro_paragraphs 재사용), app.py 에서 longterm_wp 일 때만 호출. 목차·"최종 확인" 줄은 그대로. 저장된 최종본은 ③을 다시 눌러야 반영.
- 시험·결과: preview/hyetaek_intro_split_20261114/(compare.html, verify.md). 실제 데이터 SHA 전후 같음, 네이버·싼세계·생활레이더 출력 같음.

## 완료 (2026-11-14) — 네이버·싼세계 도입부 문단 나누기 (최종본 만들 때 자동)
- 바꾼 것: shop_formatter.py 에 split_intro(기본 꺼짐) + 문장 나누기, app.py 에서 네이버·싼세계만 켬. 싼세계 도입부 문단 아래 여백 1.6em. 이미 나뉜 도입부·옛 숍·생활레이더는 그대로. 저장된 최종본은 ③을 다시 눌러야 반영.
- 시험·결과: preview/intro_split_20261114/(compare.html, verify.md). 실제 데이터 SHA 전후 같음.

## 완료 (2026-11-14) — 싼세계 표지 스타일 고정 + 하단 남색 15% 옅게
- 바꾼 것: image_gen.py 싼세계 표지를 SSAN_COVER_* 상수 한곳으로 고정(남색 그라데이션·노란 배지·흰 글씨, 랜덤 색 없음), 최대 불투명도 0.90→0.765(글씨 구역은 0.81). 새로 만드는 표지부터 적용.
- 백업: image_gen.backup_before_ssan_cover_lock_20261114.py. 시험·결과: preview/ssan_cover_lock_20261114/(compare.html, verify.md). 실제 데이터 SHA 전후 같음.

## 완료 (2026-11-14) — 원고 35개 글쓰기 지시 문장 교체
- 바꾼 것: '[확인 필요]' 문장·'손볼 곳' 문장을 지우고 새 문장(확실한 숫자만, 작업용 글자 남기지 않기, 완성 원고)으로 교체. 새 문장 35/35, '[확인 필요]'·'손볼 곳' 글자 0개.

## 완료 (2026-11-14) — 경험 칸 손질 20편 (원고 파일만 수정)
- 바꾼 것: 경험칸-손질-1116주.md 의 두 줄로 원고 20개의 경험 칸을 통째로 교체. 35개 전체 검사: 경험 칸 소제목이 본문 설계에 없는 곳 0개, 파일에 없는 원고 0개.

## 완료 (2026-11-14) — 주간기획/2026-11-16 폴더 만들기 (복사만, 원본·대기열·plan 안 건드림)
- 만든 것: 주간기획/README.md, 주간기획/2026-11-16/{확정목록.md, 글쓰기방향-1116주.md, products_20261116.txt(복사), 원고/ 35개}. 제품형 네이버 8·싼세계 5. 방향 항목·경험 칸은 35편 모두 찾음.
- 참고: 경험 칸 20편은 plan_20261116.json 의 experience_slots 소제목 이름이 바뀌기 전 이름이라 본문 설계 소제목과 안 맞음(그대로 복사함). 만든 스크립트는 임시 폴더에 있음.

## 완료 (2026-11-14) — 제품에 맞춘 글 손질 7편 + 요점 null 정리 + 제품 링크 13편 26개
- 바꾼 것: 제목 4편(트리오너먼트·미니라디에이터·현관방한커튼·여성패딩, 원래 제목은 예비 제목 맨 앞), 소제목 7편(방향 문서 5장). 11/16 주 요점 null 은 대기열 15곳(네이버 6·싼세계 9)·plan 같이 [] 로. 제품 링크 13편 26개(네이버 8편 16개, 싼세계 5편 10개). 항목 수 그대로(네이버 142·싼세계 103), 승인 21·14 그대로.
- 백업: backup/{content_queue_naver_tips,content_queue_wp_tips}.backup_before_product_fit_20261114_171649.json, backup/plan_20261116.backup_before_product_fit_20261114_171649.json, backup/products_20261116.backup_before_product_fit_20261114_171649.json, backup/{content_queue_naver_tips,content_queue_wp_tips}.backup_before_update_products_20261114_171718.json, pipeline/final/products_20261116.backup_before_products_txt_20261114_171706.json
- 참고: plan_20261116.json 에는 제품 링크를 안 넣음(update-products 가 대기열만 바꿈).

## 완료 (2026-11-14) — 예비 글 2편(안경김서림방지·겨울방한커튼) 손질 + role 을 scheduled 로
- 바꾼 것: 확정 제목·발행일(안경 11/22 일, 커튼 11/21 토)·role(scheduled), 안경은 본문 설계 5개(요점 1개씩), 원래 제목은 예비 제목 맨 앞. 대기열 plan 칸 + plan_20261116.json 같이. 승인 체크·다른 항목은 그대로. 싼세계 오방난로(9443ff31)는 이미 해제 상태라 변경 없음, 네이버 오방난로(58c86787)는 체크 그대로.
- 백업: backup/content_queue_naver_tips.backup_before_reserve_polish_20261114_170341.json, backup/content_queue_wp_tips.backup_before_reserve_polish_20261114_170341.json, backup/plan_20261116.backup_before_reserve_polish_20261114_170341.json

## 완료 (2026-11-14) — 11/16 주 18편 제목·본문 설계 손질 반영
- 바꾼 것: 대기열 두 파일 plan 칸(final_title·alt_titles·outline)과 pipeline/final/plan_20261116.json 에 같이. 제목 11편, 본문 설계 14편(수도꼭지교체 5번 요점 교체, 임산부전기장판 2번·실외기실방풍 4번 요점 1개씩 추가 포함). 원래 제목은 예비 제목 맨 앞에 둠. 항목 수 그대로(네이버 142·싼세계 103·plan 42).
- 백업: backup/content_queue_naver_tips.backup_before_plan_polish_20261114_165342.json, backup/content_queue_wp_tips.backup_before_plan_polish_20261114_165342.json, backup/plan_20261116.backup_before_plan_polish_20261114_165342.json
- 참고: title_pattern·alt_patterns 칸은 안 건드림(새 제목과 안 맞을 수 있음).

## 완료 (2026-11-14) — 11/16 주 글쓰기 자료 모으기 (읽기·검색만)
- 결과: pipeline/stage_write_1116_run/write_brief_20261116.md (35편, 발행일 순). 분류 기록 = 대기열 항목의 planned_type 칸. 네이버 API 0회(계획 top_posts 5개씩 사용), 웹 검색 18회(싼세계 14편; 한도로 끊긴 4회는 재시도). 계획·대기열·발행 글은 안 건드림.

## 완료 (2026-11-14) — 싼세계 정점 기본 0+가점, product_need 를 push_plan·앱에 표시
- weekly_plan.py: --wp-peak-exclude 기본 0, --wp-peak-bonus 기본 20(정점까지 3주 이상 남은 주제 가점), report.md 에 색인 복사본 위치 한 줄. README_weekly.md 고침.
- push_plan.py: 계획에 product_need 가 있으면 대기열 plan 칸에 같이 넣음(없으면 지금과 똑같음). app.py(_plan_view·_plan_md_lines)·templates/article_edit.html: '찾을 제품 조건' 표시(product_need 가 있을 때만). 이미 넣은 11/16 주 항목은 product_need 가 없어 그대로.
- 시험(복사본·읽기만): 대기열 1,627개 항목의 화면·내보내기 결과가 수정 전 app.py 와 글자 그대로 같음.

## 완료 (2026-11-14) — tools/weekly_plan.py 만듦 (r1c 일반화, 시험까지만. 다음 주 실제 기획은 금요일)
- 끝: tools/weekly_plan.py(기획·--finalize), tools/README_weekly.md. r1c.py 는 그대로. 시험은 저장된 캐시로만(네이버 API 0회, Claude 0회), 시험 결과 폴더 pipeline/stage_wtest_run, pipeline/stage_wtest_final. 운영 target_alias.json 에 새 이름표 29개 반영(백업 pipeline/target_alias.backup_before_alias_sync_20261114.json).
- 다음(금요일): 씨앗·제외·필수 파일 만들기 → README_weekly.md 의 명령 ①②③. 물어볼 것은 보고 참고.

## 완료 (2026-11-14) — 대기열 글 4편 본문 설계(search_intent·outline)를 제목에 맞게 교체
- 바꾼 id: 김장 2026-11-16_4d5cf00f, 털슬리퍼 2026-11-16_aae95c94, 오방난로 2026-11-16_58c86787, 남자어그 2026-11-16_8ffe9d43 (content_queue_naver_tips.json 의 plan 칸 + pipeline/final/plan_20261116.json 같은 4편). 다른 칸·다른 항목은 그대로(대기열 142개, plan 42개, 바뀐 항목 각 4개뿐).
- 백업: content_queue_naver_tips.backup_before_outline_fix_20261114_114946.json, pipeline/final/plan_20261116.backup_before_outline_fix_20261114_114946.json

## 완료 (2026-11-14) — 실제 대기열에 넣기 끝 (일정 35편 승인됨 + 예비 7편 승인 안 함)
- 넣음: 네이버 116→142(승인 21·예비 5), 싼세계 87→103(승인 14·예비 2). 백업 backup/content_queue_{naver,wp}_tips.backup_before_push_plan_20261114_114454.json. 넣은 id 는 pipeline/final/push_log.json. 예비는 발행일 비움, 주차는 11/16.
- 되돌리기: python tools/push_plan.py --undo (승인된 것도 뺌, 발행됨·글 만들어짐은 안 뺌). 예비 플랫폼·카테고리는 pipeline/final/categories_20261116.json 의 reserve_platforms·categories.
- 남은 일: 제품 링크는 products_20261116.txt 작성 → --products-txt → --update-products --apply.

## 완료 (2026-11-14) — 앱 넣기 마무리: plan 칸 화면·내보내기 + 제품 링크 붙이기 (실제 넣기는 사용자 "넣어" 후)
- 끝: app.py(_plan_view·_plan_md_lines 추가, _build_weekly_final_md 에 '글 계획' 절, edit_article 에 plan_view), templates/index.html(발행 예정·확정 제목 줄)·article_edit.html(확정 계획 상자). 백업 app.backup_before_plan_view_20261114.py. tools/push_plan.py: 카테고리·유형 파일(pipeline/final/categories_20261116.json)이 계획 값보다 우선, --products-txt(txt→json), --update-products(넣은 항목의 제품 링크만, --apply 로 씀).
- 시험은 전부 복사본: 35편 넣기·화면·2편 승인 후 확정 목록·가짜 링크 3줄 갱신·기존 항목 화면/내보내기 13개 파일 글자 그대로 같음. 실제 대기열·발행 글은 안 건드림.
- 다음: 사용자가 제품 링크를 pipeline/final/products_20261116.txt 에 적으면 --products-txt 로 json 생성. 실제 넣기: python tools/push_plan.py --plan pipeline/final/plan_20261116.json --products pipeline/final/products_20261116.json --apply (되돌리기 --undo). 제품 링크는 넣은 뒤에도 --update-products --apply 로 바꿀 수 있음.

## 완료 (2026-11-14) — 앱 넣기 도구 push_plan (dry-run까지만, 실제 넣기는 사용자 "넣어" 후)
- 끝: tools/push_plan.py(넣기·--undo·--init-products), tools/plan_edits.py(손질 적용), pipeline/final/{edits,plan,products}_20261116.json. 실제 대기열·앱 코드는 안 건드림(복사본으로만 --apply·되돌리기·앱 읽기 시험).
- 실제 넣기 명령: python tools/push_plan.py --plan pipeline/final/plan_20261116.json --products pipeline/final/products_20261116.json --apply (되돌리기: --undo). 넣은 id는 pipeline/final/push_log.json.
- 대기: 사용자 답(물어볼 것: 카테고리·네이버 A/B 구분·예비 넣을지·plan 칸 읽는 앱 수정).

## 완료 (2026-11-14) — 폴더 정리 (용량 줄이기)
- 끝: 1,777개 / 약 1,082MB 지움 (backup 오래된 백업, 코드·장부 백업, pipeline 옛 단계·결과, 옛 pipeline_backup 3개, preview, __pycache__, 발행 21일 초과 글 이미지 475개). 목록은 pipeline/cleanup_plan.md, 지운 기록은 pipeline/cleanup_log_20261114.txt
- 남긴 것: 판단 필요 항목(raw_cache 등 이어하기 캐시, tools 옛 백업, 저장본에 로컬 경로 남은 워드프레스 이미지 49개 등). 묶음마다 앱 불러오기 정상, check_plan 합격 9·불합격 4 (지우기 전과 같음).

## 완료 (2026-11-14) — R1c 플랫폼별 중복 판정 + 제철 새 영역 탐색 (가장 최근)
- 끝: 결과 pipeline/stage_r1c_run/ (plan_20261116.json, report.md=보고표, judge_result.json=판정 저장, fill.json, fill_first.json=처음 채운 결과, stats.json=탈락 사유 수). 운영 파일·앱 데이터 안 건드림. 전체 실행 안 함.
- 결과: 네이버 21/21, 싼세계 14/14, 예비 네이버 5/5 · 싼세계 3/5(부족 2). 되살린 6편(창틀바람막이·실외기실방풍·화장실동파방지·김장·전기난로·현관방풍비닐) + r1b 유지 16편 + 새로 채운 글.
- 사용량: 블로그 검색 89/2,000, 데이터랩 74/300, 검색광고 42회(씨앗별 1회), Claude 판정 1회 $0.24 · 모양 haiku 1회 $0.014 · 기획 호출 2번 $0.35 · 제목 단계 약 $0.51.
- 알림: 싼세계 예비 2편 부족은 기획 호출 2번 한도 때문에 기획 받은 글 안에서만 다시 골랐기 때문(--planned-only). 같은 키워드가 두 플랫폼에 들어간 2개(김장고무장갑·오방난로)는 제목 저장소가 키워드 단위라 싼세계 쪽을 띄어쓴 이름표로 따로 처리.
- 다시 하기: python tools/r1c.py explore→trend→shape→signals→judge→fill (--planned-only)→plan→build (저장된 값·판정은 다시 안 받음).

## 완료 (2026-11-14) — R1 중복 보정(stage_r1b_run) (가장 최근)
- 끝: 교체 적용 후 기존 글 395개와 대조. 결과 pipeline/stage_r1b_run/ (plan_20261116.json, report.md=보고표+판정표, judge_result.json·judge_result2.json·judge_final.json=판정 저장). 운영 파일·앱 데이터·네이버 API 안 건드림(API 0회).
- 결과: 네이버 8/21, 싼세계 7/14, 예비 1. 부족 이유: 후보 41개 중 판정 통과 3개뿐(방풍·난방 물건이 이미 쓴 글과 겹침). 최종 확인에서 중복 6편(화장실동파방지·김장·슈퍼싱글난방텐트·열선센서·우레탄스노우체인·아파트결로) 추가로 뺌.
- Claude: 판정 3번(1차 74개 $0.25, 2차 6개 $0.04, 최종 22개 $0.085), 기획 호출 1번 + 제목 단계 8번(합계 $0.36).
- 다시 하기: python tools/r1b.py prep→judge→judge2→fill→plan→build (저장된 판정은 다시 안 부름, --force).

## 완료 (2026-11-14) — 재기획 R1 (가장 최근)
- 끝: 과거 글 시험 결과로 뽑는 기준을 바꾸고 11/16 주 계획을 다시 만듦. 결과 pipeline/stage_r1_run/ (plan_20261116.json, report.md=보고표, check_plan_20261116.json). 운영 pipeline/*.json·앱 데이터·네이버 쪽 운영 색인은 안 건드림. 전체 실행(run_weekly) 안 함.
- 코드: tools/r1.py(순위 계산), tools/r1_signals.py(후보·신호 수집: cands→expand→trend→shape→build), tools/r1_report.py(보고표), plan_posts.py(assign_r1·r1 모드), titles.py(싼세계 제목 규칙 wp_*), filter.json 'r1' 묶음(enabled=false 로 끄면 예전 방식 그대로; 기존 값 안 지움). r1 은 ranking_r1.json 처럼 r1 신호가 붙은 입력일 때만 켜짐.
- 결과: 네이버 21(15편 추세 상승·정점 0~3주 전, 물건 14편, 상시 급한 증상·방법 6편) + 싼세계 14(정보 10 = 방풍비닐 4·난로 3·전기장판 3, 수익 4) + 예비 9(네이버식 5·싼세계식 4). 예비 1편 모자란 이유: 싼세계 넓은 주제 5편·같은 대상 2편 한도.
- 검사 13개 중 8 합격. 불합격: A1(예비 9)·A2(정원 안 씀)·A8 아님→합격(중간값 2,010)·A5(싼세계 수익글 브랜드 '다이소외풍차단' 허용)·A6(넓은 주제 두 플랫폼 합계)·A12(수익글 하루 1편 규칙 안 씀).
- 알려진 구멍: 새 후보 대부분은 색인에 카테고리가 없어 '같은 카테고리 4편' 한도가 적용되지 않음(A6 카테고리 칸은 빈 값이라 통과로 나옴). 따로 분류해 보니 냉난방·계절 대비가 네이버 11편·싼세계 12편(check_category_meta.json).
- 사용량: 블로그 검색 277/1500, 데이터랩 284/300, 검색광고 40회, Claude haiku 10회(모양, $0.044) + 분류 확인 1회($0.006). 기획 실행 2번: run1 14회 $1.19(+지문·검사 $0.35), run2 14회 $0.69. 미리보기(--ranking-only-preview) 3번은 기획 호출 없음(판정만).
- 다시 만들기: python tools/r1_signals.py build (API 일부) → sh pipeline/stage_r1_run/run_plan.sh runN.

## 완료 (2026-11-14) — 과거 글 시험(backtest)
- 끝: tools/backtest.py (collect → api → classify → report). 결과는 pipeline/backtest_run/ 의 backtest_naver.csv, backtest_wp.csv, summary.md. 코드·운영 파일·앱 데이터는 안 건드림.
- 사용량: 블로그 검색 449/600, 데이터랩 29/80, 검색광고 34회, Claude(haiku) 1회. 값은 raw_cache.json·usage.json 에 저장돼 있어 report 만 다시 돌리면 API 호출 없음.
- 다시 만들기: python tools/backtest.py report (API·Claude 호출 없음).

## ★★★★★★★★★★★★★★★★★★ 2026-11-14 — 4.7단계(수익글 의도 확인을 전체 흐름에 넣기·띄어쓰기 기준) 완료 (가장 최근)
- 1 수익글 의도 확인이 `plan_posts` 흐름 안으로: 자리 배정(최종 검사) 뒤·기획 호출 전에 `money_intent_gate`. 수익글의 상위 5개 글 제목·연관어로 Claude(sonnet, 한 번에)가 '집에서 쓸 제품을 고르려는 의도'인지 판정 → 아니면 후보에서 빼고 다시 배정(최대 3바퀴). 들어온 글도 기획(outline)·상위 5개 글·제목을 똑같이 받음. 은행에서 온 후보는 상위 글이 없어서 `golden_cache.json` 에서 읽어 붙임(`attach_cached_top_posts`, 읽기만). 판정은 `<out_dir>/intent_info_cache.json` 에 저장(키워드당 1번, `ask_intent_info`). `choose_titles`·`retitle.py` 도 같은 저장소·함수 사용. plan.money_swaps 에 사유 기록. 끄려면 filter.json weekly.money_intent_gate=false.
- check_plan A10 에 '일정·예비 모든 글에 outline·top_posts' 추가.
- 2 띄어쓰기: SPACING_PROMPT 에 '사전에 한 낱말로 굳은 말·제품 이름은 붙인다' 와 예시 6개. `SPACING_VER=2` 로 저장된 띄어쓰기를 한 번 다시 받아 바꿈. Claude 가 '한 낱말'로 정한 붙여 쓴 표기(푸른곰팡이 등)는 띄어쓰기 감점 제외(`rules_for`). tests/title_cases.json 에 6개 추가 → `python tools/titles.py --selftest` 56/56.
- 실행 `pipeline/stage47_run/` (`FRESH=1 sh pipeline/stage47_run/run_plan.sh runN` 처음부터, FRESH 없이 하면 저장된 판정 재사용). 시작 은행 = stage45_run/golden_bank.start.json, 색인 = stage45_run/index(stage46 과 같은 것), 제목 캐시 시작본 = stage46 최종본. 합격 검사 13개 중 11 합격(A9·A10·A13 합격, A1·A2 만 불합격 = 정보글 후보 부족, 예전 그대로).
- 확인된 것: 의도 판정은 애매한 키워드(씽크대수전)에서 돌릴 때마다 달랐음 → 저장소가 판정을 고정해야 같은 결과(저장된 판정으로 다시 돌리면 일정·예비·제목 동일). Claude: run1 10회 $0.756, run2 13회 $0.837, 같은 입력 확인용 run3 4회 $0.646(저장된 기획이 성공 후 지워져 기획 호출이 다시 나감 — 기획 재실행 2번 한도를 1번 넘김).
- 백업: pipeline_backup_before_fullrun_20261120/ (13개 운영 파일). 운영 파일·네이버 API 안 건드림. 5단계 시작 안 함. 전체 실행(run_weekly)은 사용자가 직접.

## ★★★★★★★★★★★★★★★★★ 2026-11-13 — 4.6단계(4.5 검수 3가지 수정) 완료 (가장 최근)
- 1 틀 판정을 코드로: `titles.pattern_of` 가 제목 글자만 보고 정함(비교 > 순서 > 실수·손해 > 질문 > 상황 > 숫자·결과, 아무것도 안 맞으면 숫자 있으면 숫자·결과형 아니면 순서형). Claude 가 적은 pattern 은 `claude_pattern` 으로 참고만. A13·주간 틀 배분(30%)도 코드 판정. 같은 'N가지'는 플랫폼별 2편까지(`select_weekly` 가 다음 순위로 교체, check_plan A13 에 포함). 정답 세트 `tests/title_cases.json` 에 틀 4건·어색한 문장(awkward_cases) 추가 → `python tools/titles.py --selftest` 50/50. (옛 케이스 2건 조정: '세면대 설치 셀프로 해도 될까, 먼저…'는 새 우선순위상 순서형, '주방 선반 주방용…'은 쉼표 추가.)
- 2 의도와 갈래: 기획 intent 를 받아 Claude 가 키워드마다 buy(집에서 쓸 제품을 고르려는 의도인가)·ambiguous(뜻이 여러 개)·target_words 를 한 번에 판정(`intent_info`, 캐시 저장). 수익글인데 buy=false 면 `tools/retitle.py --bank` 가 수익글에서 빼고(plan.money_swaps 에 사유) 은행의 저장된 다음 수익 후보로 같은 자리를 채움(같은 한도 규칙 적용, 새 탐색·기획 없음). 이번 결과: 정전기방지패드(주유소 패드 원리) 제외 → 무향섬유유연제. **새로 들어온 글은 본문 설계(outline 등)와 상위 5개 글 정보가 없음** — 제목은 새로 받아 정했고, 본문 설계는 기획을 다시 돌릴 때 채워짐.
- 3 어색한 문장: 코드 규칙 `awkward`(키워드가 제거·교체·설치 같은 동작 이름으로 끝나는데 바로 뒤에 조사·쉼표 없이 명사가 이어지면 탈락), Claude(haiku) 자연스러움 검수 1회(`natural_check`, 판정 저장·재사용, 어색하면 `unnatural` 탈락), 키워드 표기 통일(`canon_entry`: 확정·예비가 같은 표기), 뜻이 여러 개인 키워드는 제목에 target_words 중 하나 필요(`no_target`). check_plan A9 도 이 규칙을 같이 봄.
- 실행: `pipeline/stage46_run/` (stage45_run 의 plan·title_cache 복사, 시작본 plan_start.json·title_cache_start.json). `LOCALAPP_INDEX_DIR=pipeline/stage45_run/index python tools/retitle.py pipeline/stage46_run/plan_20261116.json --bank pipeline/stage46_run/golden_bank.json` → 처음 Claude 13회 $0.28, 같은 시작본으로 다시 돌리면 0회·확정·예비 동일(확인함). 합격 검사 13개 중 11 합격(A9·A13 합격, A1·A2 만 불합격 = 정보글 후보 부족, 예전 그대로).
- 기준값(`_ACTION_END`·`_NEXT_OK` 허용 낱말 등)은 추정값 — 5단계 보정. 운영 파일·네이버 API 안 건드림. 5단계는 시작 안 함.

## ★★★★★★★★★★★★★★★★ 2026-11-13 — 4.5단계(제목 검수 4가지 수정) 완료 (가장 최근)
- 1 틀 쏠림: 제목 후보마다 pattern(순서형·실수손해형·비교형·상황형·숫자결과형·질문형). 주간 선택 `titles.select_weekly`: 같은 틀은 플랫폼별(네이버·워드프레스·예비) 최대 30%, 넘으면 점수 손실 가장 작은 글부터 다른 틀의 다음 순위로 교체, 바꿀 후보가 없는 글만 재요청. 예비 2개는 확정·서로 다른 틀 우선. check_plan A13 추가(13개 중 11 합격, A1·A2 만 불합격 = 정보글 후보 부족, 예전 그대로).
- 2 수익글: 선택 표현(고르는·추천·비교·종류·차이·vs·가격·용량·규격·크기·~용, 사용·활용 같은 말은 제외) 없으면 no_pick 탈락(필수, 후보 전부 적용). 13편 모두 포함. 정답 세트 38/38.
- 3 검색 의도: 기획 프롬프트가 intent 를 같이 받음(plan.intent), 상위 후보를 Claude 가 한 번에 검수해 다른 뜻이면 탈락(off_intent). 정전기방지패드 = 셀프 주유소 정전기 방지 패드의 원리·사용, 푸른곰팡이 = 음식에 핀 푸른곰팡이를 먹어도 되는지·처리.
- 4 띄어쓰기: 키워드 포함 비교는 띄어쓰기 무시. Claude 가 프롬프트·재요청에도 제목에서 붙여 써서(웃풍차단), 붙여 쓴 키워드는 Claude 에게 띄어쓰기만 한 번에 받아(spaced, 캐시 저장) 코드가 제목에 바꿔 끼움(`respace_entry`). 붙여 쓴 확정 제목 12→0. 붙여 쓴 후보는 감점(-3, 필수 아님).
- 실행: 기획 재실행 2번(`pipeline/stage45_run/`, run1 $0.72 6회·run2 $0.57 7회, 중간에 한도로 멈춰 이어서) + 제목 단계만 다시 돌리기 `LOCALAPP_INDEX_DIR=pipeline/stage45_run/index python tools/retitle.py pipeline/stage45_run/plan_20261116.json`(띄어쓰기·intent 2회 $0.06; 캐시가 차 있으면 0회, 두 번 돌려 확정·예비 동일 확인). 제목을 다시 받은 글 12편(수익글 선택 표현·틀 부족).
- 기준값은 filter.json title_rules(money_pick_words, pattern_max_share, unspaced_penalty, single_word_ok) — 모두 추정값, 5단계 보정. 운영 파일·네이버 API 안 건드림. 5단계는 시작 안 함.

## ★★★★★★★★★★★★★★★ 2026-11-13 — 4단계(제목 정하기) 완료 (가장 최근)
- 제목 후보 6개(title, title_keyword)를 기획 호출에 같이 받음(호출 수 그대로). 근거: 상위 5개 제목·연관어·갈래·시기·짝 글·내 제목 말투 12개(`titles.style_samples`). **자동완성은 캐시에 저장돼 있지 않아 못 줌**(있으면 `autocomplete` 칸을 읽도록 해 둠).
- 규칙 채점 `tools/titles.py`(Claude 없이), 기준값은 filter.json `title_rules`(추정값, 5단계 보정). 필수 탈락 7종, 가점 6종. 통과 후보 3개 미만인 글은 모아서 1번만 재요청, 그래도 모자라면 plan.problems.
- plan 에 final_title·alt_titles·title_keyword·title_scores·title_candidates. 받은 후보는 `<out_dir>/title_cache_날짜.json` 에 저장되어 같은 주 재실행 때 재사용(같은 입력 → 같은 제목).
- check_plan A9 = 일정+예비 전부, 확정·예비 모두 필수 규칙 위반 0건. 정답 세트 `tests/title_cases.json`(좋은 5·위반 7종×2) → `python tools/titles.py --selftest` 17/17.
- 기획 재실행 `pipeline/stage4_run/` (`sh pipeline/stage4_run/run_plan.sh runN`, 시작 은행은 stage35 것): 2번 실행 일정·예비·제목 동일. A9 합격(위반 0), 12개 중 10 합격, A1·A2 불합격(네이버 18/21·워드프레스 13/14·예비 3/10, 정보글 후보 부족 그대로). 후보 단계 탈락: 키워드 표기 다름 6, 금지어 1. 제목 재요청 1편(옷곰팡이냄새). Claude 호출 run1 5회 $0.62, run2 4회 $0.59.

## ★★★★★★★★★★★★★★ 2026-11-13 — 3.5단계(두 번째 전체 실행 검수 잔손질 4가지) 완료 (가장 최근)
- 1 브랜드: 제품 판정은 brand 로 봤는데 뒤에 도는 유형 판정이 덮어써서 새던 것. `fingerprint.type_of`/`is_brand`/`ensure_types`/`record_meta` 로 brand 우선. 그레이바스도 brand(욕조·욕실 업체 이름). 정답 세트 brand 12건(5건 추가), 전체 149/149(100%).
- 2 표기 통일: `tools/spelling.py` (샤시·새시→샷시, 우풍→웃풍, 펜트리→팬트리). 사쉐/사셰는 이번 기록에 없어 넣지 않음.
- 3 넓은 주제: 합산 4편 한도는 원래 합산이었고, 이름 흔들림이 문제였음 → `spelling.TOPIC_GROUP_RULES`(외풍·창호, 수전·배관). check_plan A6·SPEC 반영.
- 4 이어하기: `run_weekly` + `pipeline/weekly_state.json`, `--fresh`. 가짜 단계로 시험(한도 멈춤→재실행 시 앞 3단계 건너뜀, 전부 끝나면 처음부터, 다른 기준일은 처음부터).
- 기획 재실행 `pipeline/stage35_run/` (`sh pipeline/stage35_run/run_plan.sh run3` 로 또 가능, 시작 은행은 전체 실행 전 백업): 2번 실행 일정(31)·예비(3) 동일. A3~A8·A10~A12 합격, A1·A2 불합격(정보글 후보 부족: 네이버 정보 12/15, 워드프레스 정보 9/10, 예비 정보 0/7 — 넓은 주제 합산 한도로 후보가 줄어듦, 더 채우려면 정보 씨앗 탐색 필요), A9 는 4단계 몫. 브랜드 0편, 외풍·창호 4편, 수전·배관 4편. Claude 호출: 기획 4회×2, 채점 2회.


## 다음: 사용자가 run_weekly.bat --today 2026-11-13 실행
- 수익글 순위 = 예상 클릭 × 가점(`weekly.pair_bonus`: 같은 주 정보글 짝 1.3 / 이미 쓴 글 짝 1.15 / 없음 1.0, 추정값 — 5단계에서 보정). plan 의 expected_clicks 는 가점 없는 값. 가짜 기획 확인: 수익글 10편 중 짝 1편, 예상 클릭 합 838. (`money_pair_first` 는 그대로 남아 있음)
- 전체 실행 대비 백업: `pipeline_backup_before_fullrun_20261113/` (운영 파일 13개).

## ★★★★★★★★★★★★★ 2026-11-13 — 3단계 3-1(예상 클릭 × 시기, 네이버 기준) 완료 (가장 최근)
- 잔손질: 수익글 조건을 제품 판정의 `use`(home/material/furniture/display)로 갈라 home 만 solves 인정, 브랜드·모델명은 같은 판정의 `brand` 로 유형 brand 제외(`PS_VERSION` 9, 제품 판정 모델 sonnet — haiku 는 solves 4건 틀림). 정답 세트에 solves 12·brand 7건 추가, 전체 142/142(100%).
- 예상 클릭: 식·계수는 SPEC_v2.md 끝 "3단계 3-1 규칙", 계수는 filter.json `expected_clicks`(모두 추정값, 5단계에서 보정). plan 모든 글에 expected_clicks·clicks_basis·timing{label,reason}.
- 순위: 정보·수익 후보를 합쳐 예상 클릭 순으로 자리 배정(한도에 걸리면 큰 쪽이 남음). `weekly.money_pair_first`=false(기본). **true 로 두면 2.5단계 방식(짝 글 있는 수익글 먼저)** — 기본으로 바꾸니 수익글 pair_with 가 10/10 → 1/10 로 줄고 주간 예상 클릭 합은 약 520 → 1,019 로 늘어남. 어느 쪽이 좋을지는 사용자 결정 필요.
- 월 검색량 150 미만은 일정·예비에서 제외(`weekly.schedule_volume_min`), 중간값 800 미만이면 plan.warnings 에 경고. 시기: 상승(선점 추세 ≥1.2)/유지/선발행, 은행 계절 하락은 `bank.revisit_week` 저장 → 그 주가 되면 후보 복귀. 계절 달력 `pipeline/seasonal_calendar.json`(초안, tools/make_calendar.py), make_seeds 가 이번 달 항목 최대 6개를 정보 씨앗(선발행)에 넣고 `calendar_seeds.json` 에 기록.
- 탐색(run_pipeline): 멈춤 = 월 검색량 150 이상·유형·등급·수익글 조건 통과 후보가 정보 48·수익 20(정원×`weekly.fill_goal_factor` 1.5). 씨앗 기본 60개(정보 42·수익 18). 가짜 응답으로 make_seeds·simulate_fill 만 시험(실제 탐색·네이버 API 실행은 안 함).
- 기획 재실행(`pipeline/stage3_run/`): 실제 Claude 기획은 2번(run1·run2, 둘 다 짝 우선 순서 = money_pair_first=true 와 같은 결과) — A10 합격, 150 미만 0편, A1·A2·A8(중간값 610) 불합격(정보글 후보 부족). 순위 규칙을 예상 클릭 순으로 바꾼 뒤에는 **기획 재실행 한도(2번) 때문에 가짜 기획(제목·본문 설계 없음)으로만** 확인: `stage3_run/dry_D1/plan_20261116.json` (일정 25 + 예비 3, 중간값 1,040, A10·A8 합격, 같은 입력 2번 동일). 새 순서의 진짜 기획은 `sh pipeline/stage3_run/run_plan.sh run3`(1번, 약 $0.4~0.5).
- 같은 입력 재현: 같은 은행 파일로 돌리면 일정·예비 동일(dry 2번·run1=dry 확인). 기획이 은행 파일을 갱신하므로 같은 주를 다시 돌리면 은행에서 일정으로 뽑힌 항목이 빠져 일정이 조금 달라지는 것은 원래 동작(`golden_bank.start.json` 이 첫 입력).

## ★★★★★★★★★★★★ 2026-11-13 — 2.5단계(첫 전체 실행 검수 문제 A~E 수정) 완료 (가장 최근)
- A 쌍 확인: 탐색 단계는 '후보↔기존 글'만(후보끼리는 지문 3칸 같은 것만 같은 글, `fingerprint.cluster_exact`). 후보끼리 가까운 쌍 확인은 기획 단계에서 갈래별 목표(일정+예비)×3 이내 상위 후보만. 쌍 확인 호출 상한 `weekly.max_pair_calls`=30(넘으면 검색량 큰 후보부터, 나머지는 이번 주 후보에서 뺌: 탐색은 `pair_check`·로그에 기록, 기획은 은행으로). 가짜 Claude 시험: 이전 방식 14,544쌍/243회 → 탐색 215쌍/4회 + 기획 상위 후보 62쌍/2회(실제 기획에서는 저장된 답 덕에 1회).
- B 세부: 지문 세부가 같은 글 주 2편(`weekly.max_per_detail`, '일반' 제외). 일정·예비 모두, check_plan A6·SPEC_v2 반영.
- C 갈래: `fingerprint.ensure_product` 가 지문과 따로 product(물건 이름인가)·solves 를 색인에 저장(`PS_VERSION`, 지문 판정은 다시 안 함). product 이면 문제를 '제품 선택(구매)'로 고쳐 money. 정답 세트에 track 12건 추가, 전체 123/123(재사용 채점, 새로 판정은 track 12개와 새로 늘어난 기존 글 13개뿐).
- D 수익글: solves 가 null 이면 수익글 후보에서 뺌(`nosolve`), 짝 글은 solves 의 대상·문제와 같은 정보글만, 배정 순서 = 짝이 같은 주 일정에 있음 > 이미 쓴 글과 짝 > 짝 없음, 같은 대상 수익글 주 1편(`weekly.max_money_per_target`).
- E 씨앗: `make_seeds.has_brand`(브랜드·모델명 낱말 + 모델 번호 모양)로 뺌, 수익 씨앗은 파생 원본 정보 씨앗이 없으면 뺌. 가짜 응답 시험만 함(실제 호출 안 함).
- 기획 재실행 결과(`pipeline/stage25_run/`, 색인은 `LOCALAPP_INDEX_DIR=stage25_run/index` 복사본, 운영 파일 안 건드림): 일정 33 + 예비 3 = 36편. A3~A7·A11·A12 합격, A1·A2 불합격 = 정보글 후보 부족(네이버 정보 14/15, 워드프레스 정보 9/10, 예비 정보 0/7, 수익은 목표 다 채움). 같은 입력 2번 돌려 일정·예비·짝 글 동일. A8~A10 은 이전과 같음(3~4단계 몫).
- 확인할 것: solves 판정이 샤워기헤드·수전·욕실필터 같은 제품도 많이 null 로 봄(수익글 후보에서 58개 뺌). 너무 엄격하면 solves 질문(`fingerprint.PS_PROMPT`)을 손봐야 함.
- 실행 방법: `sh pipeline/stage25_run/run_plan.sh <이름>` (기획만, 네이버 API 호출 없음).

## ★★★★★★★★★★★ 2026-11-12 — 2단계(정보·수익 두 갈래) 코드·시험 완료
- 결과: `pipeline/stage2_run/plan_20261116.json`(일정 34편 + 예비 3편 = 37편). 수익글 10편 중 5편에 `pair_with`. 같은 입력으로 다시 돌려도 37편 동일. 기획 호출은 4회씩 3번(이어하기 1번 포함, 약 $0.55~0.60/회).
- 합격 검사: A3~A8·A11·A12 합격, A2 의 수익글(네이버 6·워드프레스 4)은 맞음. **A1·A2 불합격 = 정보글 후보 부족**(네이버 정보 14/15, 예비 정보 3/10; 수익글은 후보가 남아 74개가 '자리 참'으로 못 들어감). 설계상 갈래끼리 채우지 않으므로, 더 채우려면 정보 씨앗으로 탐색(네이버 API)을 더 돌려야 함 — 이번엔 API 호출 금지라 안 함. A9·A10 은 3~4단계 몫.
- 바퀴벌레퇴치제/바퀴벌레약추천: 해충 약·퇴치제 이름은 '제품 선택(구매)' + 대상 '해충+퇴치제' 로 통일(PROMPT_VERSION 14). 13판 판정 중 해충 약과 무관한 것은 그대로 재사용(`KEEP_VERSIONS`, `PEST_RE`). `eval_judge.py --repeat 0` = 저장된 색인을 이어 쓰며 바뀐 항목만 새로 판정. 정답 세트 111/111.
- 모델: 지문·유형·쌍 판정을 haiku 로 새로 채점해도 111/111(비용 sonnet 의 약 1/7) → `filter.json models` 를 haiku 로 변경. 기획은 항상 sonnet. 기존 sonnet 판정은 그대로 두고 새로 묻는 것부터 haiku. (haiku 채점은 새 판정 1회뿐이라 일관성 반복 검사는 안 함. 이전 sonnet 채점 색인은 `pipeline/stage2_run/eval_index_sonnet_backup/`.)
- 운영 색인(`pipeline/keyword_index.json` 등)은 이번 기획으로 해충 항목이 새로 판정돼 갱신됨(시작 전 상태는 `pipeline/stage2_run/backup/`).

# 인수인계 (2026-11-08 작성, 2026-11-10 갱신)

> **2026-11-10 저녁 — 먼저 `SPEC_v2.md` 를 읽으세요.** 목표가 다시 정의됐고(수익글 약 30%, 확정 제목, 시기, 완전 자동화), 앞으로의 작업은 SPEC_v2.md 의 0~6단계를 따른다. 아래 내용은 지금까지의 경과 기록이다.
> 그날 오후 추가분: 15:25 전체 실행이 기록 파일 쓰기 오류(구글 드라이브 동기화, Errno 22)로 중단 → `tools/safe_io.py` 추가(기록은 `%LOCALAPPDATA%\local-app-logs` 에 먼저, 주요 파일 쓰기 재시도), `run_weekly.bat` 줄바꿈 수정. 백업 `*.backup_before_safe_io_20261110.*`. 이후 전체 실행은 아직 안 함.
> 새 파일: `tools/check_plan.py`(합격 검사, 현재 계획 6/11 통과), `tests/eval_set.json`(판정 정답 45건).

## ★★★★★★★★★★ 2026-11-11 밤 — 1.6단계(검수 문제 수정) 완료 (가장 최근)
- 쌍 판정 = same / cover(한쪽이 넓음) / diff. `pair_verdicts.json` 은 새 버전(PAIR_VERSION 6)로 다시 판정(이전 파일 백업 `pair_verdicts.backup_before_stage16_20261111.json`). 정답 세트의 쌍은 운영 쌍 기록에 `fixed` 로 미리 들어감(채점용 기록에는 안 넣음).
- 같은 글 묶음은 '직접 same 판정이 난 쌍'으로만(사슬 금지), 대표 = 검색량 큰 것. 후보끼리 cover 는 같은 주에 함께 안 넣음(`cover_with`). 기존 글이 넓고 제목에 그 종류가 나오면(cover_b_named) 중복, 키워드가 더 넓으면 중복 아님. 증상 해결 글(막힘·냄새 등)의 일반 글은 부위 변형도 덮음.
- 안전망(final_audit)은 제안만: '같은 글'은 쌍 확인, '업체형'은 유형 재판정으로 확정. 제안·재판정 결과는 저장(`pipeline/audit_cache.json`, 색인의 `type_recheck`) → 같은 입력이면 같은 일정. 대상 이름 정리는 장소가 다른 이름(욕실/욕조/싱크대수전)을 합치지 못하게 코드로 막음.
- 업체형 기준 좁힘(`keyword_classify.py`): 수전·샤워기·환풍기·배수관·호스·경첩 교체와 비용·셀프, 변기·세면대 통째 교체는 직접 해결. 유형 판정 버전 `TYPE_VERSION`. 
- `eval_judge.py`: 색인 전체 같은 글 묶음 검사(5개 이상 묶음 출력, false 쌍이 같은 묶음이면 실패), `--only old|holdout2`.
- 정답 세트 105건(기존 77 + holdout2 28): holdout2 처음 27/28, 고친 뒤 새로 2번·재사용 모두 105/105. 기획 재실행 `pipeline/stage16_run/`: A1·A3·A4·A5·A6·A7·A8·A11 합격, 같은 입력 재실행 일정 45편 동일, 안전망이 직접 뺀 것 0개.
- 남은 것: 제목·예상 클릭(A9·A10)은 3~4단계. `pipeline/keyword_index.json` 등 색인은 이제 PROMPT_VERSION 13 기준.

## ★★★★★★★★★ 2026-11-11 — 1.5단계(지문 3칸·대상 정리·가까운 쌍 확인) 완료
- 지문 = 대상|문제|세부, 묶음 = 앞 두 칸. `pipeline/target_alias.json`(대상 이름 정리표), `pair_verdicts.json`(가까운 쌍 확정, 다시 안 물음), `keyword_index.json`·`post_index.json`(색인, 1단계 때 것은 없어져 새로 만듦). 질문을 바꾸면 `fingerprint.PROMPT_VERSION`(지금 10)을 올릴 것.
- 판정: 3칸 같음 → 같은 글 / 가까운 쌍(묶음 같고 세부만 다름, 대상 이름 포함, 한쪽 세부 '일반', 해충 제품↔퇴치)만 Claude 확인 / 그 외 다른 글.
- 주제 한도: 같은 대상 주 2편(plan `topic`=대상 이름), 넓은 주제 주 4편(`topic_group`). 유형(업체형·주제 밖)은 색인 값 사용(`plan_posts.apply_index_types`). `plan_posts.py --out-dir` 추가(시험 결과는 `pipeline/stage15_run/`). `check_plan.py` 는 결과를 계획 파일 옆에 저장.
- 정답 세트 기존 45건 100%, 새 사례(holdout) 32건 처음 26/32(81%) → 규칙 보강 후 100%(새로 2번·재사용 모두 77/77). 보강은 틀린 것을 보고 한 것이라 새 사례 점수는 참고용.
- 기획 재실행(`pipeline/stage15_run/plan_20261116.json`): A1·A3·A4·A5·A6·A7·A8·A11 합격, A2·A9·A10 불합격(2~4단계 몫). 안전망이 8개를 뺌(목표 0~2 미달).
- 남은 것: 업체형 판정이 넓음(수전·변기·세면대 교체/교체비용 등 38개 제외 — 직접 교체 글도 섞였을 수 있어 확인 필요), 안전망이 잡는 '제품 키워드 ↔ 문제 해결 글' 중복(벽지곰팡이제거제 등).

## ★★★★★★★★ 2026-11-10 밤 — 1단계(키워드 지문 색인) 완료
코드는 git 으로 관리(고치기 전 커밋). 새 파일: `tools/fingerprint.py`(지문 색인), `pipeline/keyword_index.json`·`post_index.json`(색인, 지우면 다시 판정). 바뀐 파일: plan_posts·run_pipeline·keyword_classify·make_seeds·run_weekly·check_plan·eval_judge·filter.json·CLAUDE.md·SPEC_v2.md.
- 지문 = 대상|문제(문제는 17개 목록). 중복·같은 글 = 지문 비교(코드). 새 항목만 60개씩 Claude 에 질문, 성공한 묶음마다 저장. 지문 규칙(질문)을 바꾸면 `fingerprint.PROMPT_VERSION` 을 올릴 것(이전 판정은 자동으로 다시 판정).
- 탐색 단계: 1차 탈락 통과 키워드에 지문을 붙여 기존 글과 같은 것 제외 + 같은 지문은 검색량 최대 1개만 정밀 분석. 기획 단계: `semantic_dedup`·`existing_dup_check` 가 색인 기반으로 바뀜. `final_audit` 는 안전망 1회(filter.json passes 1, rounds 1).
- R6: 사용 한도는 `UsageLimitError`, 종료 코드 75. 탐색은 캐시·색인에 저장, 기획은 `pipeline/resume_plan_날짜.json` 에 받은 기획을 저장 → 같은 명령으로 이어서. 가짜 claude 로 확인함.
- 정답 세트(실전 조건, 기존 글 233개 색인): 3항목 모두 100%, 새로 판정 3회 연속 모두 정답. 단, 질문 규칙을 정답 세트를 보며 다듬었으므로 새 사례에서는 이보다 낮을 수 있음.
- 알려진 한계: 지문이 가끔 너무 굵음(예: '벽지얼룩제거제'가 '옷 얼룩 제거제' 글과 같다고 판정, 세탁세제 7종이 한 묶음). 안전망 최종 검사는 여전히 모델 호출이라 실행마다 걸리는 게 조금 다를 수 있음.

## ★★★★★★★ 2026-11-10 오후 2 — 걸러야 할 것을 탐색 단계부터 (가장 최근)
백업: `tools/*.backup_before_strict_20261110.*` (keyword_classify.py, run_pipeline.py, plan_posts.py, filter.json)
- 3번째 재기획 결과: 최종 검사가 업체형·기존 글 중복을 대량으로 잡았지만(1~3회차 18·15·8개), 빠진 자리를 제품형이 채워 해결형 22/35로 하락, 3회차에도 8개 남음(철제선반·샤워헤드 변형).
- 원인: 탐색 단계(배정 가능 수 계산)는 업체형·기존 글 중복을 안 걸러서 '해결형 35편 확보'로 착각하고 확장을 멈춤.
- 조치: ① 유형 분류에 `service`(업체 시공·수리 의뢰) 추가 → 제외 유형(정밀 분석도 안 함) ② 탐색 회차마다 새 S·A 를 기존 글·지난 기획과 비교(`plan_posts.existing_dup_check`, 60개씩 Claude) → `dup_existing` 표시, ranking 에 저장, 배정 가능 수와 기획 규칙 제외에 반영 ③ 최종 검사 최대 6회.
- 시험: 가짜 응답으로 전체 루프·dup_existing 이 ranking→기획 제외까지 흐르는 것 확인. **실제 전체 실행 필요**(run_weekly.bat --today 2026-11-13).

## ★★★★★★ 2026-11-10 오후 — 최종 검사·업체 시공형 제외·상위 글 근거 기획
백업: `tools/*.backup_before_final_audit_20261110.*` (plan_posts.py, filter.json). 사용자 방침: "쓸만한 수준이면 안 됨, 채팅으로 뽑는 것보다 확실히 나아야 함, 타협 금지".
- 재기획 2회 결과: 해결형 34/35까지 올랐지만 '씽크대막혔을때'·'싱크대배수구'가 기존 글 '싱크대 막힘…'과 중복인데 남음(250개 일괄 판정의 놓침).
- **최종 검사**(`final_audit`): 배정된 일정+예비 45개만 Claude 에 따로 보내 하나씩 판정(이미 쓴 글+지난주 기획과 중복 / 후보끼리 중복 / 업체 시공·공사·수리 의뢰형). `final_audit_passes`(2)번 따로 물어 한 번이라도 걸리면 제외 → 빈자리 재배정 → 다시 검사(최대 `final_audit_rounds` 3회). 기준: 같은 대상+같은 문제면 표기(씽크대=싱크대, 화장실=욕실, 세무=스웨이드)·덧말·넓고 좁은 같은 말 무시하고 중복.
- **업체 시공형 제외**: `max_service_per_week`=0 (보일러수리·단열벽지시공·정화조처리 등).
- **표기 통일 비교**: `norm()` 이 SYNONYMS(씽크대→싱크대, 스웨이드→세무, 화장실→욕실 등, `weekly.synonym_map` 추가 가능) 적용 → 글자 비교·표기 묶음에도 반영.
- **기획 고도화**: 정밀 분석 때 연 상위 5개 글(제목·글자 수·이미지 수·경과일)을 기획 요약에 넣고, 기획안에 `beat_top`(상위 글보다 나아질 점), `target_chars`, `target_images`, `top_posts` 추가.
- 시험: 11/10 데이터 + Claude 목업으로 최종 검사 3회 루프·업체형 7개 제외·씽크대막혔을때 제외·새 필드 저장 확인. 실제 실행 아직 → `python tools/plan_posts.py --today 2026-11-13`.

## ★★★★★ 2026-11-10 오전 실행 결과 + 내용 중복 점검 추가
- **11/10 실행**(08:16~11:00, `plan_20261116.json`): 35편 모두 해결형(네이버 21·워드프레스 14), 확장 2회, 블로그 검색 4,095회·데이터랩 451회. 문제: 같은 글이 될 키워드 중복(세무신발세탁=스웨이드신발세탁, 수전 교체 4편, 차량 김서림 4편), 기존 글과 내용 겹침 가능(예: 벽지곰팡이제거제 ↔ 기존 '벽지 곰팡이 제거 후…'), 업체 시공형 키워드(보일러수리·단열벽지시공) 섞임(미조치).
- **내용 중복 점검**(백업 `*.backup_before_dedup_20261110.*`, plan_posts.py·filter.json): 배정 직전 `semantic_dedup()` 이 Claude 1회로 ① 기존 글(existing_keywords.json, 인포랩스·지원금 대기열 제외)과 같은 내용 → 제외(사유 "이미 쓴 글과 내용이 같음") ② 후보끼리 같은 글 묶음 → 1개만 남기고 나머지는 '본문·태그용 변형 표기'로 ③ 주제 묶음 이름 → kw_topic 덮어써서 '같은 주제 주 2편' 한도가 일관되게 적용. 배정 순서 앞쪽 `weekly.semantic_check_max`(150)개만 점검, 범위 밖이 배정되면 problems 에 기록. 실패하면 글자 비교만으로 진행. 11/10 데이터로 Claude 목업 시험 통과.

## ★★★★ 2026-11-10 변경 — 제목 기준 경쟁 측정 + 콘셉트 재설계
백업: `tools/*.backup_before_title_compete_20261110.*` (run_pipeline.py, golden_metrics.py, plan_posts.py, make_seeds.py, filter.json, topic.json)
- **콘셉트(사용자 확인)**: 네이버·싼세계(워드프레스) 모두 "살림하며 겪는 생활 문제를 해결하고, 그 과정에서 필요하면 제품을 추천"하는 글. 제품이 중심 아님. 인포랩스 = 정부지원금 롱테일(별도). 예전 설계(네이버=해결형, 워드프레스=제품 선택형)는 폐기.
- **11/10 시험** (`tools/compete_test.py`, 결과 `pipeline/compete_test_20261110_075718.json`): 해결형 5개는 예전 방식으로 모두 "30일 1,000+"였지만 제목 일치 글은 0~7개(창틀곰팡이청소 0, 신발곰팡이제거 1, 앞유리습기 1). 블로그 검색이 단어를 본문 어디서든 세서 생활 문제 키워드의 경쟁을 부풀린 것이 원인. 상위 100 중 제목 일치가 실제 경쟁을 잘 구분(빨래쉰내제거 84, 세탁실곰팡이제거 62 = 진짜 경쟁).
- **새 경쟁 기준** (`golden.compete_mode`="title", `golden_metrics.title_compete`): ① 관련도순 상위 100 중 제목 일치 ≥ `drop_top100_title`(50) → 탈락(1회 호출) ② 통과 시 최신순 최대 `title_scan_pages`(5) 페이지에서 30일 제목 일치 글 수(500개가 다 30일 안이면 기간 환산) ③ 경쟁률 = 월 검색량 ÷ 30일 제목 일치(0이면 1). A = 경쟁률 ≥ 20, S = 경쟁률 ≥ 50 + 상위 100 제목 일치 ≤ 30 (+ 기존 3조건). 캐시 키 `_pubt`. 은행 재확인도 같은 기준(`publish_for`). "any" 로 두면 예전 방식.
- **배정** (`weekly.assign_mode`="solve_first"): 두 플랫폼 모두 해결형 먼저(남은 자리 비율 큰 쪽부터), 자리가 남을 때만 제품형("해결형 후보가 모자라 제품형으로 채움" 메모). 확장 종료 = 빈자리 0 + 일정 해결형 ≥ `expand.solve_min`(30/35). `simulate_fill` 이 `solve_scheduled`·`total_cap` 반환. 회차당 새 씨앗 40개.
- **씨앗/기획**: make_seeds 는 생활 문제 씨앗 위주(제품 이름 씨앗 최대 3개), topic.json 워드프레스 역할도 생활 문제 해결형으로. 기획 프롬프트에 콘셉트 추가(제품 키워드도 관련 생활 문제 해결 글로 기획).
- **시험**: title_compete 를 가짜 응답으로 확인(빨래쉰내제거 1회 호출로 탈락, 창틀곰팡이청소·아사이불 S 수준), 11/9 ranking 으로 solve_first 배정·plan_posts main(Claude 목업) 정상, golden_run 전체 루프 가짜 응답으로 정상. **실제 실행 아직** → `run_weekly.bat --today 2026-11-13`.

## ★★★ 2026-11-09 저녁 변경 — API 한도 보호 + 해결형 최소 7편
백업: `tools/*.backup_before_api_guard_20261109.*` (run_pipeline.py, golden_metrics.py, filter.json)
- **사건**: 12:28 시작한 실행이 3회차에서 네이버 블로그 검색 하루 한도(25,000) 중 22,000회 도달 알림 → 사용자에게 중단 권고. 데이터랩도 400 오류 반복(하루 1,000회 한도 의심). 2회차까지 해결형 씨앗 120개로 새 해결형 S·A 0개(네이버 해결형 1/21).
- **결론(데이터)**: 해결형 키워드는 대부분 최근 30일 발행 1,000+ 인데 검색량 중간값 450 → 경쟁률 0.45 이하라 기준 완화로도 황금이 안 됨. 사용자 결정 (가): **네이버 해결형 최소 7편**(`golden.expand.naver_solve_min`=7), 나머지 네이버 자리는 제품형 황금으로 채움. 자리는 찼는데 한 회차 동안 새 해결형이 안 나오면 멈춤(`solve_stall_rounds`=1).
- **호출 줄이기**: ① `golden_metrics.publish_stats` 를 첫 페이지 → 마지막 페이지(901~1000) → 이분 탐색으로 변경(결과 동일, 1,000+ 는 10회→2회, 첫 응답의 total 도 반환). 오전 실행 기준 약 7,000→2,300회. ② 황금 방식은 `metrics_stage(lite=True)` 로 블로그 문서 수·12개월 추세를 부르지 않고, 1차 탈락 통과 키워드에만 `enrich_survivors` 로 채움(문서 수는 publish total 재사용) → 회차당 블로그 수백~천 회, 데이터랩 대부분 절약. 포화도 순위는 통과 키워드끼리 계산.
- **한도 보호**(`ApiMeter`): 실행당 블로그 최대 `max_blog_calls`=15,000, 데이터랩 `max_trend_calls`=800, 같은 API 연속 `api_fail_streak`=15회 실패면 하루 한도 초과로 보고 남은 측정 중단 → 확장 종료(지금까지 결과로 기획은 진행). 회차마다 `[API 사용]`, 끝에 `[API 사용 합계]` 로그.
- **시험**: publish_stats 를 예전 방식과 16가지 경우 비교해 결과 일치, golden_run 전체 루프를 가짜 응답으로 정상·한도 초과 두 경우 모두 오류 없이 끝남 확인. 실제 실행은 블로그 한도 초기화(보통 자정) 후에.

## ★★ 2026-11-09 오후 변경 — 네이버를 '해결형'으로 채우기
백업: `tools/*.backup_before_solve_target_20261109.*` (run_pipeline.py, plan_posts.py, make_seeds.py, filter.json, keyword_classify.py)
- **11/9 오전 실제 실행 결과**(plan_20261116.json, 11:24): 35+10 다 참, 해충 네이버 2·워드프레스 0, 81.6분, 확장 2회. 그러나 네이버 21편 중 18편이 제품형(워드프레스 넘침분), 해결형 3편.
- **원인 분석**(result_20261109_095829.json): 필터 통과 1,701개 중 해결형 266개(16%)뿐, 그중 222개(83%)가 최근 30일 발행 1,000+ 로 탈락 → 해결형 S·A 8개. 씨앗이 제품 이름 위주라 제품형만 많이 나옴.
- **종료 기준**: `golden.expand.naver_solve_target=true` → 네이버 21자리를 해결형(platform_hint=naver)으로 채워야 '다 참'. `simulate_fill` 이 `naver_solve`·`solve_short` 를 돌려줌. 한도까지 못 채우면 로그 `[해결형 부족]`, 기획 단계는 예전처럼 제품형으로 메움.
- **확장 씨앗**(해결형 부족 시 5갈래, 회차당 60개): ① 필요한 유형 S·A 자체 ② 화면 연관검색어(키워드당 4개까지) ③ 자동완성 ④ 해결형 변형 = 네이버 한도 남은 카테고리 S·A 의 세부 주제 + 카테고리별 문제 단어(`solve_suffixes_by_category`, 예: 침구→세탁·냄새·보풀) ⑤ 롱테일 = 발행 1,000+ 로 탈락한 해결형 키워드(검색량 순)의 자동완성.
- **정밀 분석 순서**: 아직 필요한 유형(네이버 해결형 / 워드프레스 제품형)부터.
- **씨앗(make_seeds)**: 절반 이상을 해결형 씨앗(대상+문제, role:"naver")으로 요청, 미달이면 seeds_log 에 문제 기록. seeds_log 에 role_counts.
- **분류**: offtopic 예시 추가(수영 수경·낚시·골프·캠핑 장비, 패션·미용, 오토바이, 포스터 제작 등).
- **한도**: 확장 최대 6회, 정밀 분석 450개, 최대 4시간, 회차당 새 씨앗 60개.
- **시험**: 11/9 데이터로 simulate_fill → 네이버 해결형 3/21(부족 18) 확인, next_seeds 5갈래 출력 확인, golden_run 전체 루프를 가짜 응답으로 끝까지 돌려 오류 없음 확인. **실제 실행은 아직** → `run_weekly.bat --today 2026-11-13` 로 확인 (최대 4시간+).

## ★ 2026-11-09 변경 (먼저 읽기) — 확장 탐색 종료 기준 + 해충 글 줄이기
백업: `tools/*.backup_before_fill_expand_20261109.*` (plan_posts.py, run_pipeline.py, make_seeds.py, run_weekly.py, filter.json)
- **새 컴퓨터 점검 완료**(11/9): 파이썬 패키지·ego-browser 0.6.2.7·스킬·claude 명령어·.env 키 문제 없음, 에고라이트 네이버 로그인 완료. `.env` 의 NAVER_SEARCH_* 는 없어도 됨(허브 키로 대체), OPENAI_API_KEY 는 주간 실행에서 안 씀.
- **버전 정리**: 11/8 23:24 plan(지금 `plan_20261116.old_234826.json`)은 35+10 다 채웠지만 규칙 적용 전이라 네이버 21편 중 약 15편이 해충·계절 하락·구매처·오타 포함. 23:45 `plan_20261116.json` 이 규칙 적용판(네이버 9 부족).
- **확장 종료 기준 변경** (`filter.json` golden.expand.stop_on_fill=true): S+A 45개 → 회차마다 S·A 에 카테고리를 붙이고 `plan_posts.simulate_fill()` 로 실제 기획 규칙(구매처·제외 유형·계절 하락·지난주/기존 발행 겹침·의도 중복·표기 통일·카테고리/세부 주제/해충 한도, 은행 포함)을 적용한 배정 가능 수를 계산해 네이버 21 + 워드프레스 14 + 예비 10 이 다 차면 멈춤. 회차별 카테고리·해충 편수를 로그에 남김. 분류를 건너뛰거나 계산 실패 시 예전 min_sa 기준.
- **다음 회차 씨앗**(`run_pipeline.next_seeds` + `seed_priority`): 자리 모자란 플랫폼 유형 → 그 플랫폼에서 한도 남은 카테고리 → 등급 순. 해충 키워드·해충 단어 씨앗은 확장에 안 씀.
- **확장 한도**: 최대 5회, 정밀 분석 350개, 최대 3시간 (예전 3회·250개·2시간). 회차마다 카테고리 분류 Claude 호출 1회 추가.
- **해충 글 줄이기** (`filter.json` weekly): `pest_words` 단어가 키워드·세부 주제에 있으면 해충. `max_pest_per_platform`=2(플랫폼당 주 2편, 예비도 2편, 넘치면 은행 사유 "해충 글 줄이기"), `max_pest_seeds`=1(make_seeds 프롬프트·검증 모두 적용). null=한도 없음, 0=해충 안 씀. plan 에 `pest_counts` 기록.
- `run_weekly.py` 가 `--today` 를 run_pipeline 에도 넘김(run_pipeline 에 `--today` 추가).
- **시험**: 11/8 ranking 으로 simulate_fill → 해충 한도 없으면 26편·네이버 9 부족(실제 plan 과 일치), 한도 2 면 24편·네이버 11 부족(해충 4편). plan_posts main 도 Claude 목업으로 1회 통과. **실제 run_weekly 전체 실행은 아직 안 함** → 다음 할 일: `run_weekly.bat --today 2026-11-13` 실행 후 배정 가능 수 로그·결과 확인.

다른 컴퓨터의 Claude Code에서 이어서 작업하기 위한 정리입니다. 이 폴더(`local-app`)에는 git이 없으니 폴더째 복사하세요.

## 0. 새 컴퓨터에서 먼저 할 일
1. Python 3 설치 후 `pip install -r requirements.txt`, 이어서 `playwright install` (브라우저 발행·화면 수집용).
2. Claude Code CLI 설치·로그인. 기획·분류가 `claude -p` 를 부릅니다 (`where claude` 로 확인).
3. 복사해야 하는 비밀 파일: `.env`(네이버 검색광고·허브 키 등), `gsc_key.json`. 문서에는 키 값을 적지 않았습니다.
4. `CLAUDE.md` 규칙: 코드를 고치기 전 파일마다 백업(`파일명.backup_before_작업명_날짜.확장자`), 실제 데이터 파일은 요청 시만 수정, 발행에 영향 주는 실행은 요청 시만, 비용 드는 API는 목업으로 시험, 보고는 비개발자용으로 짧게.

## 1. 만든 구조: 주간 키워드 발굴·기획 (`run_weekly.bat` → `tools/run_weekly.py`)
순서: `existing_posts.py`(기존 발행 글 키워드) → `make_seeds.py`(씨앗 자동 생성) → `run_pipeline.py`(지표·필터·분류·화면·상위 글 분석·등급) → `plan_posts.py`(다음 주 일정 배정 + Claude 기획). 로그는 `pipeline/log_날짜.txt`(이제 줄 단위 실시간).

- **황금 키워드 방식** (`filter.json` 의 `golden.enabled=true`): 경쟁률 = 월 검색량 ÷ 최근 30일 블로그 발행 수. 발행 1000 이상이거나 경쟁률 2.5 미만이면 탈락. S = 경쟁률 4 이상 + 3조건(상위5 제목 키워드 수, 인플루언서 수, 추세) 충족, A = 중간, 예상 유입 = 검색량 × 노출 위치 가중치.
- **확장 탐색**: S+A 가 45 미만이면 S·A 키워드를 새 씨앗으로(키워드 자체·화면 연관검색어·자동완성) 최대 3회. 확장 회차는 씨앗 단어 규칙 대신 분류의 `offtopic`(주제 범위 밖)으로 거름. 검색량 범위 100~10,000, 포화도 필터 1.0(안 씀) — 둘 다 `golden` 안 설정, 예전 방식은 맨 위 값(300~3000, 0.3) 그대로.
- **분류** (`keyword_classify.py`, Claude 1회 호출): 유형 solve/choose/brand/local/offtopic/**buyplace(구매처 찾기)**/other. 제외 유형 = brand·local·offtopic·buyplace. S·A 에는 `with_meta=True` 로 카테고리(`topic.json` 10개)·세부 주제·표준 표기(canonical)도 받음(`run_pipeline.annotate_sa`, 이미 만든 ranking 에는 `tools/reclassify_ranking.py`).
- **배정** (`plan_posts.py`): 주 네이버 3×7=21, 워드프레스 2×7=14, 예비 10. 해결형→네이버, 제품선택형→워드프레스 먼저, 남는 자리만 반대편. 규칙(모두 `filter.json` 의 `weekly`): 같은 카테고리 플랫폼당 주 4편(`max_per_category_per_platform`), 같은 세부 주제 주 2편(`max_per_topic`), 선점 추세 0.6 미만은 '계절 하락'으로 제외(`min_seasonal_lift`, 작년 데이터 부족하면 건너뜀), 구매처 단어(`buy_place_words`) 제외, 표준 표기가 같으면 같은 의도로 묶고 기획엔 표준 표기, 원래 표기는 plan 의 `variant_spellings`("본문·태그용 변형 표기").
- **황금 키워드 은행** (`pipeline/golden_bank.json`): 일정에 못 들어간 S·A(자리 부족·한도 초과·계절 하락)를 쌓고 다음 주 후보로 사용. 14일 지나면 발행 수·경쟁률만 재계산해 미달이면 제거, 이미 발행된 건 제거. 일정에서 빠진 S·A 는 plan 의 `unscheduled_sa` 에 사유 기록.
- 캐시: `pipeline/cache.json`, `golden_cache.json` (30일). 로그에 회차별·합계 캐시 사용 개수 기록.

주요 파일: `tools/filter.json`(모든 숫자 설정), `tools/topic.json`(주제·카테고리·플랫폼별 우선 단어), `tools/weekly_seeds.txt`(자동 생성 씨앗). 고치기 전 백업은 `tools/*.backup_before_*` 와 `pipeline/*.backup_before_assignq_20261108.json`.

## 2. 최근 실행 결과
**run_weekly (21:37~23:29, 약 1시간 52분, 정상 종료)**: 0회차 연관 키워드 7,284개 → 기본 필터 통과 370 → 1차 선별 33 → 정밀 분석 33 → S+A 24. 확장 1회차 연관 8,603개 → 새 키워드 856 → 1차 선별 108 통과 → S+A 89(S 37, A 52, 목표 45 달성, 확장 1회로 종료). 정밀 분석 141개, 캐시 2,928/6,087건 사용. 주제 범위 밖 23개 걸러짐.

**이후 분류·기획만 재실행** (분류 $0.087, 기획 $0.26): `pipeline/plan_20261116.json` (주간 11/16~11/22)
- 일정 26편(네이버 12, 워드프레스 14), 예비 0. shortage = 네이버 9, 워드프레스 0, 예비 10.
- 빠진 S·A: 계절 하락 21(벌레 계열 대부분), 구매처 1(쥐약파는곳), 브랜드 유형 1(테슬라워셔액), 카테고리·주제 한도 초과 30, 의도 중복 7, 거의 같은 키워드 3. 은행 저장 51(계절 하락 21 + 한도 초과 30).
- 오타 통일 7개: 선반렉→선반랙, 시스템헹거→시스템행거, 이동식헹거→이동식행거, 성애제거→성에제거, 캡술세제→캡슐세제, 경량렉→경량랙, 파렛트렉→파레트랙.
- 같은 날 `--today 2026-11-13` 기준으로 만든 주(11/16 시작)입니다. `plan_20261109.json` 은 예전 16:54 파일을 되돌려 둔 것.

## 3. 남은 할 일 / 확인할 것
- ~~네이버 자리가 9개 빈다~~ → 11/9 확장 종료 기준을 배정 가능 수로 바꿔 대응(위 ★ 참고). 실제 실행으로 확인 필요.
- 계절 하락이 21개(S·A의 약 4분의 1)로 많음. 기준 0.6 이 적절한지, 은행의 계절 하락 키워드를 다음 주 어떻게 다시 볼지(은행 항목의 추세 값은 발견 당시 값 그대로) 검토.
- `run_weekly.py` 실시간 로그(Popen 줄 단위)와 11/9 확장 변경은 실제 주간 실행으로 아직 확인 못 함.
- 실제 `run_pipeline` 으로 `annotate_sa`(카테고리·표기 자동 부여)가 끝까지 도는지는 미확인(별도 스크립트와 목업으로만 확인).
- 분류가 "고양이퇴치제" 같은 경계 키워드를 offtopic 으로 거른 사례가 있어 정확도 점검 필요.
- 은행 14일 재확인 로직은 목업으로만 시험(실제 은행이 14일 지난 뒤 처음 작동).
- 기획 결과를 앱 대기열로 옮기는 흐름은 이번 작업 범위 밖(필요하면 요청).

## 4. 기억해 둔 메모 (Claude 메모리 내용)
- **네이버 API 키 권한**: `.env` 기준. 검색광고 keywordstool 키 됨. 허브 키(NAVER_HUB_*)는 블로그 검색·데이터랩 검색어트렌드·뉴스 모두 됨(2026-11-08 확인). 개발자센터 키(NAVER_LOGIN_*)는 검색·데이터랩 401 "Scopes are Empty"로 안 됨. 11-08 오전 허브 블로그 검색 401은 호출 문제가 아니라 허브 콘솔에서 앱(SHOP-WP-NV)의 API 활성화가 꺼져 있던 것 → 사용자가 켠 뒤 해결. `blog_docs` 가 null 이면 코드보다 먼저 콘솔 활성화 상태를 확인(결과의 `api_status` 에 이유).
- **숍 가격대 분리 보류** (2026-09-29): shop_wp/shop_naver 를 대표 가격 10만원 기준으로 자동 분리하는 기능은 보류. 네이버 쇼핑검색 API 가 2026-07-31 종료, 쿠팡파트너스 API 는 누적 수익 15만원 이상이어야 열리고 시간당 10회 제한이라 설계가 안 맞음. 다시 꺼내면 ① 쿠팡 계정 15만원 초과 여부 ② 사전 캐싱 방식 괜찮은지부터 확인하고, 실제 호출 전 0단계로 비용·가능 여부를 보고해 승인받을 것. (메모리 파일은 `~/.claude/projects/.../memory/` 에 있고 다른 컴퓨터로는 자동 이동하지 않으므로 위 내용을 참고하세요.)
