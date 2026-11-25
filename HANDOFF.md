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

