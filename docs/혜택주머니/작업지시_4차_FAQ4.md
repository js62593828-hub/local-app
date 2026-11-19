# 자동 프로젝트 4차 — FAQ 최대 4쌍 · 2026-11-19

> CLAUDE.md 규칙 그대로. 3차가 아직 진행 중이면 끝난 뒤에 한다.

1. 사용자 결정: 혜택주머니 원고의 FAQ는 **3~4쌍(최대 4쌍)**. 규칙 파일(docs/혜택주머니/의 기획지침_자동·원고지침·글뼈대규칙·agent_hyetaek-writer·SKILL_hyetaek-weekly)은 이미 고쳐 두었다.
2. `tools/lt_draft_check.py`의 FAQ 검사 범위를 4~6 → **3~4**로 바꾼다(5쌍 이상이면 ✖).
3. `docs/혜택주머니/agent_hyetaek-writer.md` → `.claude/agents/hyetaek-writer.md`, `docs/혜택주머니/SKILL_hyetaek-weekly.md` → `.claude/skills/hyetaek-weekly/SKILL.md` 다시 복사(두 쪽 같게).
4. 시험: lt_draft_check 를 FAQ 5쌍짜리 가짜 원고 1편에 돌려 ✖가 나오는지 1회.
5. 커밋 + HANDOFF.md 한 줄. 보고는 짧게.
