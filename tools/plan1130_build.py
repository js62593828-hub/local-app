"""11/30 주 plan.json → push_plan 이 읽는 모양으로 변환하고, 복사본 대기열로 시험한다 (방법 D).
- 앱 대기열(content_queue_radar_naver.json)·articles·push_log 는 읽기만(시험 전후 해시 비교). 쓰는 곳은 stage_plan1130_run/20261123/ 안의 plan_push.json·queue_copy/ 뿐.
- push_plan.py 는 고치지 않고 그대로 호출한다(--queue-dir 복사본). 실제 --apply 는 이 스크립트가 하지 않는다.
사용:
  python tools/plan1130_build.py convert   # plan.json → plan_push.json (run_id kw_radar_naver_20261130_v2)
  python tools/plan1130_build.py test      # 복사본으로 dry-run → 복사본에만 --apply → 표·검증 저장(push_test.md, push_test.json)
"""
from __future__ import annotations
import hashlib, json, shutil, subprocess, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "pipeline" / "stage_plan1130_run" / "20261123"
PLAN = RUN / "plan.json"
OUT = RUN / "plan_push.json"
COPY = RUN / "queue_copy"
RUN_ID = "kw_radar_naver_20261130_v2"
WD = "월화수목금토일"
REAL = ["content_queue_radar_naver.json", "articles_radar_naver.json", "pipeline/final/push_log_radar_naver.json", "pipeline/final/push_log.json"]
sys.stdout.reconfigure(encoding="utf-8")


def convert() -> dict:
    d = json.loads(PLAN.read_text(encoding="utf-8"))
    plans = []
    for p in d["plans"]:
        y, m, dd = map(int, p["publish_date"].split("-"))
        r = dict(p["radar"])
        r["run_id"] = RUN_ID
        r["title"] = p["final_title"]
        r["search_query"] = p["keyword"]
        plans.append({
            "platform": "naver", "keyword": p["keyword"], "role": "scheduled", "publish_date": p["publish_date"],
            "weekday": WD[date(y, m, dd).weekday()], "track": "info", "final_title": p["final_title"],
            "monthly_total": p["monthly_total"], "product_need": dict(p["product_need"]), "radar": r,
            "plan1130": p["plan1130"],   # push_plan 은 안 읽음(참고용: 칸·제품id·파일이름)
        })
    out = {"week": d["week"], "channel": "radar_naver", "run_id": RUN_ID, "made_at": d["made_at"],
           "설명": "plan.json 변환본(방법 D). push_plan --channel radar_naver --plan 으로 읽는다. 원본: plan.json", "plans": plans}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("변환:", OUT, len(plans), "편, run_id", RUN_ID)
    return out


def snap(base: Path) -> dict:
    s = {}
    for rel in REAL:
        f = base / rel
        if f.exists():
            b = f.read_bytes()
            s[rel] = {"sha256": hashlib.sha256(b).hexdigest()[:16], "size": len(b), "mtime": f.stat().st_mtime,
                      "count": (len(json.loads(b.decode("utf-8"))) if rel.endswith(".json") and b[:1] == b"[" else None)}
        else:
            s[rel] = None
    return s


def run_push(extra: list[str]) -> str:
    cmd = [sys.executable, "-X", "utf8", str(ROOT / "tools" / "push_plan.py"), "--channel", "radar_naver", "--plan", str(OUT), "--queue-dir", str(COPY)] + extra
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    return (r.stdout or "") + (r.stderr or "")


def test() -> None:
    if not OUT.exists():
        convert()
    before = snap(ROOT)
    if COPY.exists():
        shutil.rmtree(COPY)
    COPY.mkdir(parents=True)
    for rel in ("content_queue_radar_naver.json", "articles_radar_naver.json"):
        shutil.copy2(ROOT / rel, COPY / rel)
    n0 = len(json.loads((COPY / "content_queue_radar_naver.json").read_text(encoding="utf-8")))
    dry = run_push([])
    mid = json.loads((COPY / "content_queue_radar_naver.json").read_text(encoding="utf-8"))
    assert len(mid) == n0, "dry-run 인데 복사본이 바뀜"
    app = run_push(["--apply"])
    q = json.loads((COPY / "content_queue_radar_naver.json").read_text(encoding="utf-8"))
    new = [x for x in q if (x.get("plan") or {}).get("run_id") == RUN_ID]
    new.sort(key=lambda x: (x["plan"]["publish_date"], x["plan"]["title"]))
    rows = [{"date": x["plan"]["publish_date"], "type": x["planned_type"], "keyword": x["keyword"], "title": x["plan"]["final_title"],
             "search_query": x["plan"]["search_query"], "갈래": x["plan"]["갈래"], "approved": x["approved"], "product_need": x["plan"].get("product_need")} for x in new]
    after = snap(ROOT)
    changed = [k for k in before if before[k] != after[k]]
    res = {"copy_before": n0, "copy_after": len(q), "added": len(new), "real_changed": changed, "dry_run_output": dry, "apply_output": app, "rows": rows,
           "real_before": before, "real_after": after}
    (RUN / "push_test.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = ["| # | 날짜 | 틀 | 키워드 | 확정 제목 | search_query | 갈래 |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        L.append(f"| {i} | {r['date']} | {r['type']} | {r['keyword']} | {r['title']} | {r['search_query']} | {r['갈래']} |")
    (RUN / "push_test.md").write_text("\n".join(L), encoding="utf-8")
    print("복사본", n0, "→", len(q), "· 새 항목", len(new), "· 실제 파일 변화", changed or "0")
    print("\n".join(L))
    print("--- dry-run 끝부분 ---")
    print("\n".join(dry.strip().splitlines()[-6:]))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "convert"
    if cmd == "convert":
        convert()
    elif cmd == "test":
        test()
    else:
        print(__doc__)
