"""확정한 상품의 제휴 링크 발급 도구.

approved.json(앱 '🧾 제품 체크'에서 [확정 저장]한 파일)에 있는 상품에만 링크를 발급합니다.
- 쇼핑커넥트: 상품 페이지에서 '링크 발급' → '링크 복사'로 받은 링크를 받은 그대로 저장합니다(주소를 바꾸지 않음).
  이미 발급된 상품(옛 결과 파일 / 같은 폴더 links.json / 사이트에 '링크 복사'만 있는 상품)은 다시 발급하지 않고 기존 링크를 씁니다.
- 쿠팡: 채널이 wp_tips 이고 쿠팡 판정이 '같음'인 상품만 partners.coupang.com 에서 링크 생성(아직 실제로 돌려 본 적 없음).
- approved.json 은 읽기만 합니다. content_queue*.json·articles*.json 은 건드리지 않습니다. 발행하지 않습니다.
- 에고라이트로 사용자 로그인 상태를 씁니다. 아이디·비밀번호는 입력하지 않으며, 로그인이 풀려 있으면 멈춥니다.
- 페이지 사이 3~5초 쉽니다. 한 번 실행에 발급은 최대 20개. coupang.com 본 사이트는 열지 않습니다.

사용:
  python tools/issue_links.py --run pipeline/candidates_run/20261115_radar_naver            # 기본: --dry-run (버튼 직전까지만)
  python tools/issue_links.py --run pipeline/candidates_run/20261115_radar_naver --execute  # 실제 발급

결과: 같은 폴더 links.json (--dry-run 은 links_dryrun.json)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_candidates as pc  # noqa: E402  (에고라이트 실행·쿠팡 주소 상수를 같이 씀)

ROOT = Path(__file__).resolve().parent.parent
BC_PRODUCT = "https://brandconnect.naver.com/997658497773120/affiliate/products/"
BC_PRODUCT_RADAR = "https://brandconnect.naver.com/1020008901769280/affiliate/products/"  # radar_naver(guardradar) 채널 전용
MAX_ISSUE = 20
SPACE = "issue-links-run"
PID_RE = re.compile(r"/affiliate/products/(\d+)")


def pid_of(url: str) -> str | None:
    m = PID_RE.search(url or "")
    return m.group(1) if m else None


# ───────────────────────── 기존 링크(읽기만) ─────────────────────────

def existing_links(run_dir: Path) -> dict[str, dict]:
    """이미 발급된 쇼핑커넥트 링크를 {상품번호: {link, source}} 로 모읍니다. 파일은 읽기만 합니다.
    1) pipeline/candidates_run/*/links.json 의 발급·기존 항목
    2) pipeline/radar_drafts/*/_work/links_AB.json (+ 같은 폴더 raw_<태그>.json 에서 상품번호)
    3) pipeline/radar_drafts/*/*/link.txt 에 한 줄만 있는 글 폴더(+ _work/raw_c_*.json 상품번호)
    """
    out: dict[str, dict] = {}
    for lj in sorted((ROOT / "pipeline" / "candidates_run").glob("*/links.json")):
        try:
            d = json.loads(lj.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for it in d.get("items", []):
            if it.get("naver_link") and it.get("status") in ("발급", "기존") and it.get("product_id"):
                out.setdefault(it["product_id"], {"link": it["naver_link"], "source": f"{lj.parent.name}/links.json"})
    for work in sorted((ROOT / "pipeline" / "radar_drafts").glob("*/_work")):
        la = work / "links_AB.json"
        if la.exists():
            try:
                d = json.loads(la.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                d = {}
            for tag, v in d.items():
                raw = work / f"raw_{tag}.json"
                links = v.get("link") or []
                if not raw.exists() or not links:
                    continue
                pid = pid_of(raw.read_text(encoding="utf-8"))
                if pid:
                    out.setdefault(pid, {"link": links[-1], "source": f"{work.parent.name}/_work/links_AB.json"})
        # 글 폴더 link.txt 가 한 줄(링크 1개)인 경우: 같은 작업 폴더의 raw_c_*.json 과 짝지음
        for raw in work.glob("raw_c_*.json"):
            pid = pid_of(raw.read_text(encoding="utf-8"))
            for lt in work.parent.glob("C_*/link.txt"):
                m = re.findall(r"https://naver\.me/\S+", lt.read_text(encoding="utf-8"))
                if pid and len(m) == 1:
                    out.setdefault(pid, {"link": m[0], "source": f"{work.parent.name}/{lt.parent.name}/link.txt"})
    return out


# ───────────────────────── 브라우저 스크립트 ─────────────────────────

def _js_naver(targets: list[dict], out_json: Path, mode: str) -> str:
    """mode: 'peek'(버튼 직전까지만 보기) / 'issue'(발급·복사)."""
    return f"""
const fs = await import("node:fs/promises");
const sleep = (a, b) => new Promise(r => setTimeout(r, a + Math.random() * (b - a)));
const task = await taskSpace("{SPACE}");
const page = (await task.pages())[0] ?? task.page("p1");
const T = {json.dumps(targets, ensure_ascii=False)};
const MODE = "{mode}";
const MAXI = {MAX_ISSUE};
const res = [];
let lost = false, issued = 0;
for (let i = 0; i < T.length; i++) {{
  const t = T[i];
  if (i > 0) await sleep(3000, 5000);
  await page.goto("{BC_PRODUCT}" + t.id, {{timeout: 60000}});
  await page.waitForTimeout(4000);
  const u = await page.url();
  if (u.includes("login")) {{ lost = true; break; }}
  if (!u.includes("/affiliate/products/" + t.id)) {{ res.push({{ id: t.id, r: "url-mismatch", url: u }}); continue; }}
  const btns = await page.evaluate(() => [...document.querySelectorAll("button")].map(b => b.innerText.trim()).filter(x => /링크/.test(x)));
  const hasIssue = btns.includes("링크 발급"), hasCopy = btns.includes("링크 복사");
  if (MODE === "peek") {{ res.push({{ id: t.id, r: "peek", hasIssue, hasCopy, btns }}); continue; }}
  let r = "none";
  await page.evaluate(() => {{
    window.__copied = [];
    const orig = navigator.clipboard.writeText.bind(navigator.clipboard);
    navigator.clipboard.writeText = (x) => {{ window.__copied.push(x); return orig(x).catch(() => {{}}); }};
  }});
  if (hasIssue) {{
    if (issued >= MAXI) {{ res.push({{ id: t.id, r: "limit", hasIssue, hasCopy }}); continue; }}
    await page.evaluate(() => {{ const b = [...document.querySelectorAll("button")].find(b => b.innerText.trim() === "링크 발급"); b.scrollIntoView({{block: "center"}}); b.click(); }});
    issued++; r = "issued";
    await page.waitForTimeout(3000);
  }} else if (hasCopy) {{ r = "copy-only"; }}
  else {{ res.push({{ id: t.id, r: "no-button", btns }}); continue; }}
  const c = await page.evaluate(() => {{ const b = [...document.querySelectorAll("button")].find(b => b.innerText.trim() === "링크 복사"); if (!b) return "no-copy-button"; b.click(); return "clicked"; }});
  await page.waitForTimeout(1500);
  const link = await page.evaluate(() => window.__copied);
  res.push({{ id: t.id, r, c, link, hasIssue, hasCopy }});
  console.log("NAVER", t.id, r, c, JSON.stringify(link));
}}
await fs.writeFile({json.dumps(str(out_json).replace(chr(92), "/"))}, JSON.stringify({{ lost, res }}), "utf8");
console.log(lost ? "LOGIN_LOST" : "DONE");
"""


def _js_coupang(targets: list[dict], out_json: Path, mode: str) -> str:
    """쿠팡파트너스 링크 생성(코드만 — 이번 시험에서는 실행하지 않음, 화면 흐름은 아직 확인 안 됨)."""
    return f"""
const fs = await import("node:fs/promises");
const sleep = (a, b) => new Promise(r => setTimeout(r, a + Math.random() * (b - a)));
const task = await taskSpace("{SPACE}");
const page = (await task.pages())[0] ?? task.page("p1");
const T = {json.dumps(targets, ensure_ascii=False)};
const MODE = "{mode}";
const res = [];
let lost = false;
for (let i = 0; i < T.length; i++) {{
  const t = T[i];
  if (i > 0) await sleep(3000, 5000);
  await page.goto("{pc.PARTNERS}" + encodeURIComponent(t.query), {{timeout: 60000}});
  await page.waitForTimeout(5000);
  const u = await page.url();
  if (!u.includes("partners.coupang.com") || u.includes("login")) {{ lost = true; break; }}
  if (MODE === "peek") {{ res.push({{ id: t.id, r: "peek" }}); continue; }}
  const ok = await page.evaluate((title) => {{
    const cards = [...document.querySelectorAll(".product-search-list .product-item")];
    const el = cards.find(c => (c.innerText || "").trim().startsWith(title));
    if (!el) return "no-card";
    const cb = el.querySelector("input[type=checkbox]"); if (!cb) return "no-checkbox";
    cb.click(); return "checked";
  }}, t.title);
  if (ok !== "checked") {{ res.push({{ id: t.id, r: ok }}); continue; }}
  await page.evaluate(() => {{ const b = [...document.querySelectorAll("button")].find(b => b.innerText.trim() === "링크 생성"); if (b) b.click(); }});
  await page.waitForTimeout(4000);
  const link = await page.evaluate(() => {{
    const txt = document.body.innerText + " " + [...document.querySelectorAll("input,textarea")].map(x => x.value).join(" ");
    const m = txt.match(/https:\\/\\/link\\.coupang\\.com\\/\\S+/);
    return m ? m[0] : null;
  }});
  res.push({{ id: t.id, r: link ? "created" : "no-link", link }});
  console.log("COUPANG", t.id, link);
}}
await fs.writeFile({json.dumps(str(out_json).replace(chr(92), "/"))}, JSON.stringify({{ lost, res }}), "utf8");
console.log(lost ? "LOGIN_LOST" : "DONE");
"""


def _ego(script: str, tag: str) -> str:
    """에고라이트 실행. 실패해도 예외로 끝나지 않고 빈 글자를 돌려줘 links.json 이 저장되게 합니다."""
    try:
        return pc._run_ego(script, tag)
    except Exception as e:  # noqa: BLE001
        return f"에고라이트 실행 실패: {str(e)[:150]}"


def _finish() -> None:
    try:
        pc._run_ego(f'const task = await taskSpace("{SPACE}"); await task.finish({{ keep: [] }}); console.log("FINISHED");', "finish")
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────── 메인 ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="확정 상품 링크 발급 (approved.json 의 상품만)")
    ap.add_argument("--run", required=True, help="pipeline/candidates_run/<날짜>_<채널>")
    ap.add_argument("--dry-run", action="store_true", help="버튼 직전까지만(기본값)")
    ap.add_argument("--execute", action="store_true", help="실제 발급(이 옵션이 있어야만 버튼을 누름)")
    a = ap.parse_args()
    execute = a.execute and not a.dry_run

    run = Path(a.run)
    if not run.is_absolute():
        run = ROOT / run
    run = run.resolve()
    if run.parent != (ROOT / "pipeline" / "candidates_run").resolve():
        print("멈춤: --run 은 pipeline/candidates_run/ 아래 폴더여야 합니다.")
        return 1
    ap_path = run / "approved.json"
    if not ap_path.exists():
        print("멈춤: approved.json 이 없습니다. 앱 '🧾 제품 체크'에서 [확정 저장]을 먼저 하세요.")
        return 1
    approved = json.loads(ap_path.read_text(encoding="utf-8"))
    channel = approved.get("channel", "")
    if channel == "radar_naver":  # 다른 채널은 옛 번호 그대로
        global BC_PRODUCT
        BC_PRODUCT = BC_PRODUCT_RADAR

    # approved.json 의 상품만 대상(상품번호 기준 중복 제거)
    items: list[dict] = []
    seen: set[str] = set()
    for k in approved.get("keywords", []):
        for p in k.get("picks", []):
            pid = pid_of(p.get("url", ""))
            if not pid:
                items.append({"product_id": "", "keyword": k["keyword"], "rank": p.get("rank"), "name": p.get("name", ""),
                              "product_url": p.get("url", ""), "naver_link": "", "coupang_link": "", "issued_at": "",
                              "status": "건너뜀", "reason": "상품 주소에서 상품번호를 못 찾음", "_p": p})
                continue
            if pid in seen:
                continue
            seen.add(pid)
            items.append({"product_id": pid, "keyword": k["keyword"], "rank": p.get("rank"), "name": p.get("name", ""),
                          "product_url": p.get("url", ""), "naver_link": "", "coupang_link": "", "issued_at": "",
                          "status": "", "reason": "", "_p": p})
    print(f"확정 상품 {len(items)}개 · 채널 {channel} · {'실제 발급' if execute else '--dry-run(버튼 안 누름)'}")

    # 1) 기존 링크
    ex = existing_links(run)
    todo = []
    for it in items:
        if it["status"]:
            continue
        if it["product_id"] in ex:
            it["naver_link"] = ex[it["product_id"]]["link"]
            it["status"], it["reason"] = "기존", f"이미 발급됨({ex[it['product_id']]['source']}) — 다시 발급하지 않음"
        else:
            todo.append(it)
    print(f"기존 링크 사용 {sum(1 for i in items if i['status'] == '기존')}개 · 새로 확인할 상품 {len(todo)}개")

    # 2) 쇼핑커넥트 (새로 확인할 상품만 브라우저로)
    lost = False
    if todo:
        out_json = run / ("_issue_raw.json" if execute else "_peek_raw.json")
        if out_json.exists():
            out_json.unlink()
        targets = [{"id": i["product_id"]} for i in todo]
        o = _ego(_js_naver(targets, out_json, "issue" if execute else "peek"), "issue_naver")
        print(o.strip())
        raw = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {"lost": False, "res": []}
        lost = bool(raw["lost"])
        by_id = {r["id"]: r for r in raw["res"]}
        for it in todo:
            r = by_id.get(it["product_id"])
            if not r:
                it["status"], it["reason"] = ("건너뜀", "로그인이 풀려 멈춤") if lost else ("실패", "결과를 받지 못함")
            elif r["r"] == "url-mismatch":
                it["status"], it["reason"] = "실패", f"상품 페이지가 아닌 곳으로 이동: {r.get('url')}"
            elif r["r"] == "no-button":
                it["status"], it["reason"] = "실패", f"링크 버튼이 없음(보이는 버튼: {r.get('btns')})"
            elif r["r"] == "limit":
                it["status"], it["reason"] = "건너뜀", f"한 번 실행 발급 한도 {MAX_ISSUE}개 초과"
            elif r["r"] == "peek":
                it["status"] = "건너뜀"
                it["reason"] = ("dry-run: '링크 발급' 버튼 확인됨 — 실제 실행 시 발급 예정" if r["hasIssue"]
                                else "dry-run: 이미 발급돼 있음('링크 복사'만 있음) — 실제 실행 시 기존 링크 복사" if r["hasCopy"]
                                else f"dry-run: 링크 버튼 없음({r['btns']})")
            else:  # issued / copy-only
                links = sorted(set(r.get("link") or []))
                if len(links) != 1 or not links[0].startswith("http"):
                    it["status"], it["reason"] = "실패", f"복사한 링크를 읽지 못함(읽은 값 {links})"
                else:
                    it["naver_link"] = links[0]  # 받은 그대로
                    if r["r"] == "issued":
                        it["status"], it["issued_at"] = "발급", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        it["reason"] = "링크 발급 후 복사"
                    else:
                        it["status"], it["reason"] = "기존", "사이트에 이미 발급돼 있어 '링크 복사'만 함"
    if lost:
        print("멈춤: 쇼핑커넥트 로그인이 풀려 있습니다. 에고라이트에서 직접 로그인한 뒤 다시 실행하세요.")

    # 3) 쿠팡 (wp_tips + '같음'만)
    cp_note = ""
    if channel == "wp_tips":
        cp_items = [i for i in items if (i["_p"].get("coupang") or {}).get("verdict") == "같음"]
        if cp_items and not lost:
            out_json = run / ("_issue_coupang_raw.json" if execute else "_peek_coupang_raw.json")
            if out_json.exists():
                out_json.unlink()
            targets = [{"id": i["product_id"], "query": i["_p"]["coupang"].get("query") or pc.coupang_query(i["_p"]),
                        "title": i["_p"]["coupang"]["title"]} for i in cp_items]
            o = _ego(_js_coupang(targets, out_json, "issue" if execute else "peek"), "issue_coupang")
            print(o.strip())
            raw = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {"lost": False, "res": []}
            by_id = {r["id"]: r for r in raw["res"]}
            if raw["lost"]:
                print("멈춤: 쿠팡파트너스 로그인이 풀려 있습니다. 쿠팡 링크는 만들지 않았습니다.")
            for i in cp_items:
                r = by_id.get(i["product_id"])
                if r and r["r"] == "created":
                    i["coupang_link"] = r["link"]
                elif execute:
                    i["reason"] += f" / 쿠팡 링크 실패: {r['r'] if r else ('로그인 풀림' if raw['lost'] else '결과 없음')}"
            cp_note = f"쿠팡 대상 {len(cp_items)}개"
    else:
        cp_note = "쿠팡은 wp_tips 채널만(이 채널은 건너뜀)"
    print(cp_note)
    _finish()

    # 4) 저장 (approved.json 은 고치지 않음)
    for it in items:
        it.pop("_p", None)
    out = {"run": run.name, "channel": channel, "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "executed": execute, "items": items}
    dest = run / ("links.json" if execute else "links_dryrun.json")
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    cnt = {s: sum(1 for i in items if i["status"] == s) for s in ("발급", "기존", "건너뜀", "실패")}
    print(f"결과 {cnt} → {dest}")
    return 3 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
