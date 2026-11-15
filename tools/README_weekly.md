# 주간 기획 도구 사용법 (tools/weekly_plan.py)
- 씨앗 파일(`seeds_날짜.txt`): 한 줄에 하나(물건·주제 낱말). 선택으로 `씨앗 | 연관 낱말,낱말 | 갈래 이름`도 됨. `#` 줄은 무시.
- 제외 파일(`skip_날짜.txt`): 한 줄에 낱말 하나. 이 낱말이 든 후보는 탐색에서 뺌(이미 충분히 쓴 영역).
- 필수 파일(`must_날짜.txt`, 선택): `플랫폼 | 키워드 | 한 줄 설명`. 플랫폼은 네이버 / 싼세계(수익글 자리는 `싼세계수익`) — **싼세계는 지금 꺼져 있음(`--with-wp` 로 켬)**, 꺼진 동안 싼세계 줄은 건너뜀. 중복 판정은 똑같이 받음.
- ① 기획 (결과: pipeline/stage_w월일_run/ 의 plan_날짜.json, report.md, product_need_날짜.md):
  `python tools/weekly_plan.py --week 2026-11-23 --seeds pipeline/final/seeds_20261123.txt --skip pipeline/final/skip_20261123.txt [--must 파일]`
- ② 확정 (손질 파일 적용 → pipeline/final/ 에 plan·categories·빈 products txt 생성):
  `python tools/weekly_plan.py --finalize --week 2026-11-23 [--edits pipeline/final/edits_20261123.json]`
- ③ 앱 넣기 (먼저 dry-run으로 확인, 맞으면 `--apply`): `python tools/push_plan.py --plan pipeline/final/plan_20261123.json --categories pipeline/final/categories_20261123.json --products pipeline/final/products_20261123.json`
  (제품 링크는 products_날짜.txt 에 `키워드 | 제품명 | 링크`로 적은 뒤 `--products-txt` 로 json 을 만듦.)
- 끊겼을 때: 같은 ①번 명령을 그대로 다시 실행하면 저장된 API 값·Claude 판정은 다시 받지 않고 이어 감. 처음부터 다시 하려면 결과 폴더를 지움.
- 한도: `--blog-limit`(기본 2000), `--trend-limit`(기본 300), `--searchad-limit`(기본 200). 넘으면 멈추고 알림. 일부 단계까지만: `--until fill`. Claude 없이: `--no-claude`.
- 달라지는 값: 편수·기준은 tools/filter.json 의 r1 묶음, 같은 갈래 한도는 `--theme-cap`(기본 4), 싼세계 정보글은 작년 정점이 이번 주인 주제만 제외(`--wp-peak-exclude` 기본 0, -1이면 끔)하고 정점까지 3주 이상 남은 주제에 가점(`--wp-peak-bonus` 기본 20) — **지금은 꺼져 있음(`--with-wp` 로 켬)**.
- 싼세계(W싼세계) 기획: **지금은 꺼져 있음** (filter.json 의 싼세계 편수 0, 예비는 네이버 5). 켜려면 `weekly_plan.py` 와 `check_plan.py` 에 `--with-wp` 를 붙임 → filter.json 의 `wp_on_values`(원래 값: 정보 10·수익 4·예비 합계 10)로 실행. 영구히 켜려면 그 값을 r1.wordpress·weekly 로 옮김. 합격 검사(check_plan.py)의 편수는 filter.json 에서 읽음.
