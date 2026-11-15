# -*- coding: utf-8 -*-
"""재기획 R1 보고표: pipeline/stage_r1_run/plan_20261116.json → report.md (읽기 전용, API·Claude 호출 없음)
열: 플랫폼 | 발행일 | 키워드 | 모양 | 검색량 | 추세 비율 | 정점까지 주 | 옛 황금 등급 | 확정 제목 (싼세계는 주제 묶음 이름도)"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RUN = BASE / "pipeline" / "stage_r1_run"


def fmt(v):
    return "-" if v is None else (f"{v:,}" if isinstance(v, int) else str(v))


def main():
    plan = json.loads((RUN / "plan_20261116.json").read_text(encoding="utf-8"))
    rows = []
    for p in plan["plans"]:
        r = p.get("r1") or {}
        style = p.get("platform") or p.get("r1_style")
        plat = {"naver": "네이버", "wordpress": "싼세계"}.get(p.get("platform"), "예비·" + {"naver": "네이버식", "wordpress": "싼세계식"}.get(p.get("r1_style"), "?"))
        ratio = r.get("ratio")
        ratio_txt = f"{ratio}" if ratio is not None else ("0→상승" if r.get("from_zero") else "-")
        topic = f" [주제 {r.get('cw')}]" if style == "wordpress" and r.get("cw") else ""
        rows.append((plat, p.get("publish_date") or "예비", p["keyword"] + topic, r.get("shape") or "-", fmt(r.get("volume") or p.get("monthly_total")),
                     ratio_txt, fmt(r.get("peak")), r.get("old_grade") or "-", p.get("final_title") or "(제목 없음)"))
    order = {"네이버": 0, "싼세계": 1}
    rows.sort(key=lambda t: (order.get(t[0], 2), t[0], t[1]))
    lines = ["| 플랫폼 | 발행일 | 키워드 | 모양 | 검색량 | 추세 비율 | 정점까지 주 | 옛 황금 등급 | 확정 제목 |", "|---|---|---|---|---|---|---|---|---|"]
    lines += ["| " + " | ".join(str(x).replace("|", "/") for x in t) + " |" for t in rows]
    out = "\n".join(lines) + "\n"
    (RUN / "report.md").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
