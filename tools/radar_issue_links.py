"""Desk Picker(radar_naver) 전용 링크 발급 도구.

issue_links.py 를 가져다 쓰되(그 파일은 고치지 않음), 쇼핑커넥트 계정 번호는 이 스크립트 안에서만
1020008901769280 으로 바꿔 씁니다(productpool_1130.py 방식). 다른 채널(N살림노트 등)에는 영향이 없습니다.

안전장치
- 발급 직전에 에고라이트 화면의 브랜드커넥트 채널 번호를 읽어 1020008901769280 이 아니면 멈춥니다.
- 옛 링크 재사용은 1020008901769280 채널로 발급된 것만. 그 밖(997658… 로 발급된 것, 출처를 모르는 것)은 새로 발급 대상.
- approved.json 에 있는 상품만, 한 번에 최대 20개(--limit). 기본은 dry-run, --execute 일 때만 발급.
- 결과 links.json 은 같은 run 폴더에 쓰고, 기존 파일이 있으면 덮어쓰기 전에 백업합니다.
- approved.json·대기열·articles 는 읽기만/건드리지 않음. 아이디·비밀번호는 입력하지 않음(로그인 풀리면 멈춤).

사용:
  python tools/radar_issue_links.py --run pipeline/candidates_run/20261130_radar_naver_plan            # dry-run
  python tools/radar_issue_links.py --run pipeline/candidates_run/20261130_radar_naver_plan --execute  # 실제 발급
시험용(run 폴더를 안 건드림):
  --approved <가짜 approved.json 경로> --out-dir <결과 폴더>   (dry-run 전용)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import issue_links as il  # noqa: E402

RADAR_ACCOUNT = "1020008901769280"
ROOT = il.ROOT
# 이 스크립트 안에서만 바꿔 씀(il 모듈 변수 — issue_links.py 파일은 그대로)
il.BC_PRODUCT = f"https://brandconnect.naver.com/{RADAR_ACCOUNT}/affiliate/products/"
ACC_RE = re.compile(r"brandconnect\.naver\.com/(\d+)/")


def account_of(url: str) -> str:
    m = ACC_RE.search(url or "")
    return m.group(1) if m else ""


# ───────────────────────── 화면의 채널 번호 읽기(읽기만) ─────────────────────────

def read_screen_channel() -> tuple[str, str]:
    """에고라이트로 브랜드커넥트 첫 화면을 열어 주소의 채널 번호를 읽습니다. (채널번호, 메모)"""
    out = ROOT / "pipeline" / "stage_radarlinks_run" / "_channel_raw.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    js = f"""
const fs = await import("node:fs/promises");
const task = await taskSpace("{il.SPACE}");
const page = (await task.pages())[0] ?? task.page("p1");
await page.goto("https://brandconnect.naver.com/", {{timeout: 60000}});
await page.waitForTimeout(4000);
const u = await page.url();
const links = await page.evaluate(() => [...document.querySelectorAll("a[href]")].map(a => a.href).filter(h => /brandconnect\\.naver\\.com\\/\\d+\\//.test(h)).slice(0, 10));
await fs.writeFile({json.dumps(str(out).replace(chr(92), "/"))}, JSON.stringify({{ url: u, links }}), "utf8");
console.log("READ", u);
"""
    il._ego(js, "read_channel")
    if not out.exists():
        return "", "화면을 읽지 못함(에고라이트 실행 실패)"
    d = json.loads(out.read_text(encoding="utf-8"))
    if "login" in d["url"]:
        return "", f"로그인이 풀려 있음({d['url']})"
    accs = [account_of(d["url"])] + [account_of(h) for h in d["links"]]
    accs = [a for a in accs if a]
    if not accs:
        return "", f"주소에서 채널 번호를 못 찾음({d['url']})"
    uniq = sorted(set(accs))
    if len(uniq) > 1:
        return "", f"화면에 채널 번호가 여러 개 보임({uniq})"
    return uniq[0], f"화면 주소 {d['url']}"


# ───────────────────────── 옛 링크: 이 채널로 발급된 것만 ─────────────────────────

def radar_existing_links() -> dict[str, dict]:
    """candidates_run/*/links.json 중 product_url 의 채널 번호가 1020008901769280 인 발급·기존 항목만.
    (다른 번호이거나 번호를 알 수 없는 링크 — radar_drafts 의 옛 링크 등 — 는 재사용하지 않습니다.)"""
    out: dict[str, dict] = {}
    for lj in sorted((ROOT / "pipeline" / "candidates_run").glob("*/links.json")):
        try:
            d = json.loads(lj.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for it in d.get("items", []):
            if it.get("naver_link") and it.get("status") in ("발급", "기존") and it.get("product_id"):
                acc = account_of(it.get("product_url", ""))
                if acc == RADAR_ACCOUNT:
                    out.setdefault(it["product_id"], {"link": it["naver_link"], "channel": acc, "source": f"{lj.parent.name}/links.json"})
    return out


# ───────────────────────── 메인 ─────────────────────────

def load_items(approved: dict) -> list[dict]:
    items, seen = [], set()
    for k in approved.get("keywords", []):
        for p in k.get("picks", []):
            pid = il.pid_of(p.get("url", ""))
            base = {"keyword": k["keyword"], "rank": p.get("rank"), "name": p.get("name", ""), "product_url": p.get("url", ""),
                    "naver_link": "", "coupang_link": "", "issued_at": "", "status": "", "reason": "", "account": RADAR_ACCOUNT}
            if not pid:
                items.append({**base, "product_id": "", "status": "건너뜀", "reason": "상품 주소에서 상품번호를 못 찾음"})
                continue
            if pid in seen:
                continue
            seen.add(pid)
            items.append({**base, "product_id": pid})
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Desk Picker 전용 링크 발급(계정 1020008901769280)")
    ap.add_argument("--run", required=True, help="pipeline/candidates_run/<날짜>_radar_naver…")
    ap.add_argument("--limit", type=int, default=20, help="한 번에 최대 상품 수(기본·최대 20)")
    ap.add_argument("--execute", action="store_true", help="실제 발급(이 옵션이 있어야만 버튼을 누름)")
    ap.add_argument("--approved", help="시험용: approved.json 대신 읽을 파일(dry-run 전용)")
    ap.add_argument("--out-dir", help="시험용: 결과를 쓸 폴더(dry-run 전용, run 폴더에 쓰지 않음)")
    ap.add_argument("--no-peek", action="store_true", help="dry-run 에서 상품 화면을 열지 않고 표만 만듦")
    a = ap.parse_args()
    execute = a.execute
    limit = max(1, min(a.limit, il.MAX_ISSUE))
    if execute and (a.approved or a.out_dir):
        print("멈춤: --approved / --out-dir 은 dry-run 시험 전용입니다.")
        return 1

    run = Path(a.run)
    run = (run if run.is_absolute() else ROOT / run).resolve()
    if run.parent != (ROOT / "pipeline" / "candidates_run").resolve():
        print("멈춤: --run 은 pipeline/candidates_run/ 아래 폴더여야 합니다.")
        return 1
    ap_path = Path(a.approved).resolve() if a.approved else run / "approved.json"
    if not ap_path.exists():
        print("멈춤: approved.json 이 없습니다. 앱 '🧾 제품 체크'에서 [확정 저장]을 먼저 하세요.")
        return 1
    approved = json.loads(ap_path.read_text(encoding="utf-8"))
    channel = approved.get("channel", "")
    if channel != "radar_naver":
        print(f"멈춤: 이 도구는 radar_naver 전용입니다(approved.json 채널: {channel}).")
        return 1
    work = Path(a.out_dir).resolve() if a.out_dir else run
    work.mkdir(parents=True, exist_ok=True)

    items = load_items(approved)
    print(f"확정 상품 {len(items)}개 · 채널 {channel} · 계정 {RADAR_ACCOUNT} · {'실제 발급' if execute else 'dry-run(버튼 안 누름)'} · 한도 {limit}")

    # 1) 이 채널의 옛 링크만 재사용
    ex = radar_existing_links()
    for it in items:
        if it["status"]:
            continue
        if it["product_id"] in ex:
            e = ex[it["product_id"]]
            it["naver_link"], it["status"] = e["link"], "기존"
            it["reason"] = f"{RADAR_ACCOUNT} 채널로 발급된 링크 재사용({e['source']})"
            it["reuse_channel"] = e["channel"]
    todo = [i for i in items if not i["status"]]
    print(f"재사용 {sum(1 for i in items if i['status'] == '기존')}개 · 새로 확인/발급 대상 {len(todo)}개")

    # 2) 발급 직전 화면의 채널 번호 확인
    screen_ch, note = "", ""
    if todo and not (not execute and a.no_peek):
        screen_ch, note = read_screen_channel()
        print(f"화면 채널 번호: {screen_ch or '(못 읽음)'} — {note}")
        if screen_ch != RADAR_ACCOUNT:
            print(f"멈춤: 화면 채널 번호가 {RADAR_ACCOUNT} 이 아닙니다. 상품 페이지를 열지 않았습니다.")
            _write(work, run, items, channel, execute, screen_ch, note, a, aborted=True)
            return 2

    # 3) 쇼핑커넥트: 새로 확인할 상품만(execute 면 최대 limit 개)
    lost = False
    if todo and not (not execute and a.no_peek):
        batch = todo if not execute else todo[:limit]  # dry-run 은 전부 보기(읽기만), 발급은 한도까지
        for it in (todo[limit:] if execute else []):
            it["status"], it["reason"] = "건너뜀", f"한 번 실행 한도 {limit}개 초과 — 다음 실행에서"
        out_json = work / ("_issue_raw.json" if execute else "_peek_raw.json")
        if out_json.exists():
            out_json.unlink()
        o = il._ego(il._js_naver([{"id": i["product_id"]} for i in batch], out_json, "issue" if execute else "peek"), "radar_issue")
        print(o.strip())
        raw = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {"lost": False, "res": []}
        lost = bool(raw["lost"])
        by_id = {r["id"]: r for r in raw["res"]}
        for it in batch:
            r = by_id.get(it["product_id"])
            if not r:
                it["status"], it["reason"] = ("건너뜀", "로그인이 풀려 멈춤") if lost else ("실패", "결과를 받지 못함")
            elif r["r"] == "url-mismatch":
                it["status"], it["reason"] = "실패", f"상품 페이지가 아닌 곳으로 이동: {r.get('url')}"
            elif r["r"] == "no-button":
                it["status"], it["reason"] = "실패", f"링크 버튼이 없음(보이는 버튼: {r.get('btns')})"
            elif r["r"] == "limit":
                it["status"], it["reason"] = "건너뜀", f"발급 한도 {il.MAX_ISSUE}개 초과"
            elif r["r"] == "peek":
                it["status"] = "건너뜀"
                if r["hasIssue"]:
                    it["plan"] = "새로 발급"
                    it["reason"] = "dry-run: '링크 발급' 버튼 확인됨 — 실제 실행 시 발급 예정"
                elif r["hasCopy"]:
                    it["plan"] = "사이트에 발급돼 있음(링크 복사만)"
                    it["reuse_channel"] = RADAR_ACCOUNT  # 이 채널 화면에서 본 것이므로 이 채널 발급분
                    it["reason"] = "dry-run: 이 채널 화면에 '링크 복사'만 있음 — 실제 실행 시 기존 링크 복사"
                else:
                    it["reason"] = f"dry-run: 링크 버튼 없음({r['btns']})"
            else:
                links = sorted(set(r.get("link") or []))
                if len(links) != 1 or not links[0].startswith("http"):
                    it["status"], it["reason"] = "실패", f"복사한 링크를 읽지 못함(읽은 값 {links})"
                else:
                    it["naver_link"] = links[0]
                    if r["r"] == "issued":
                        it["status"], it["issued_at"] = "발급", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        it["reason"] = "링크 발급 후 복사"
                    else:
                        it["status"], it["reason"] = "기존", "사이트에 이미 발급돼 있어 '링크 복사'만 함"
        il._finish()
    if lost:
        print("멈춤: 쇼핑커넥트 로그인이 풀려 있습니다. 에고라이트에서 직접 로그인한 뒤 다시 실행하세요.")

    dest = _write(work, run, items, channel, execute, screen_ch, note, a)
    cnt = {s: sum(1 for i in items if i["status"] == s) for s in ("발급", "기존", "건너뜀", "실패")}
    print(f"결과 {cnt} → {dest}")
    return 3 if lost else 0


def _write(work: Path, run: Path, items: list[dict], channel: str, execute: bool, screen_ch: str, note: str,
           a, aborted: bool = False) -> Path:
    out = {"run": run.name, "channel": channel, "account": RADAR_ACCOUNT, "screen_channel": screen_ch, "screen_note": note,
           "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "executed": execute and not aborted, "aborted": aborted,
           "items": items}
    dest = work / ("links.json" if execute else "links_dryrun.json")
    if dest.exists():  # 덮어쓰기 전에 백업
        bk = dest.with_name(f"{dest.stem}.backup_before_radar_issue_{datetime.now():%Y%m%d_%H%M%S}{dest.suffix}")
        shutil.copy2(dest, bk)
        print(f"기존 {dest.name} 백업 → {bk.name}")
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return dest


if __name__ == "__main__":
    sys.exit(main())
