"""11/30 주 고른 제품 상태 확인(읽기만). 쇼핑커넥트 상품 페이지 + 스토어 상품 페이지의 글자만 읽는다.
링크 발급·저장·설정 변경 없음. 페이지 사이 3~5초. 결과: pipeline/stage_plan1130_run/20261123/raw_check/
사용: python tools/plan1130_check.py collect [상품id ...]   |   python tools/plan1130_check.py parse
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_candidates as pc  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ACCT = "1020008901769280"   # 지금 에고라이트에 로그인된 쇼핑커넥트 계정 번호(이 스크립트 안에서만 씀)
RUN = pc.ROOT / "pipeline" / "stage_plan1130_run" / "20261123"
RAW = RUN / "raw_check"
SPACE = "plan1130-check"

# 칸 → (역할, 상품id)
LIST = {
 "A1": ["820714940529475", "858016061854080", "914916314912801"],
 "A2": ["806191541716608", "805516356620971", "806191541716614"],
 "A3": ["805836325536833", "808766946916292", "917791240257696"],
 "A4": ["812624461340970", "807663363959969", "977145678947840"],
 "A5": ["805575695639073", "808731499075232", "860052015299489"],
 "A6": ["805893238514761", "805893238514753", "805557444813385"],
 "A7": ["867056176067793", "805561056915938", "862090451189664"],
}
BPAIR = {
 "B1": ("996928541510400", "807713784634591"), "B2": ("818630322806176", "1006161513429280"),
 "B3": ("805575695630906", "809860230081122"), "B4": ("807663363959968", "815783814180770"),
 "B5": ("806180748465730", "806245643568725"), "B6": ("857545851175232", "821021110558656"),
 "B7": ("912485125882336", "981464956684736"), "B8": ("926521207753440", "903883307311584"),
 "B9": ("867502592110016", "891493667256096"), "B10": ("825992709660456", "882709459477952"),
 "B11": ("877364439700832", "907817818009408"), "B12": ("952103962427968", "810462961500069"),
 "B13": ("872136159294688", "889431798034080"), "B14": ("808319099715875", "911370844986656"),
}
SPARE = {"B5": "865731462354244", "B12": "807665419164934"}   # 필요할 때만 읽는 교체 후보


def all_ids() -> list[str]:
    out = [i for v in LIST.values() for i in v]
    for a, b in BPAIR.values():
        out += [a, b]
    return out


def js_check(prods: list[dict], out_json: Path) -> str:
    o = json.dumps(str(out_json).replace("\\", "/"))
    return f"""
const fs = await import("node:fs/promises");
const sleep = (a, b) => new Promise(r => setTimeout(r, a + Math.random() * (b - a)));
const task = await taskSpace("{SPACE}");
const page = (await task.pages())[0] ?? task.page("p1");
const P = {json.dumps(prods, ensure_ascii=False)};
const res = [];
let lost = false;
for (const p of P) {{
  const rec = {{ id: p.id, bc_url: "", bc_text: "", has_issue_btn: false, store_url: "", store_final: "", st_text: "", category: "", error: "" }};
  await page.goto("https://brandconnect.naver.com/{ACCT}/affiliate/products/" + p.id, {{timeout: 60000}});
  await page.waitForTimeout(3500);
  rec.bc_url = await page.url();
  if (/login/.test(rec.bc_url)) {{ lost = true; break; }}
  const bc = await page.evaluate(() => {{
    const a = [...document.querySelectorAll("a")].find(e => e.innerText.trim().startsWith("네이버 쇼핑에서 보기"));
    const btns = [...document.querySelectorAll("button")].map(b => b.innerText.trim()).filter(Boolean);
    return {{ text: (document.body.innerText || "").slice(0, 1400), store: a ? a.href : "", btns }};
  }});
  rec.bc_text = bc.text; rec.store_url = bc.store; rec.has_issue_btn = bc.btns.some(b => b.includes("링크 발급"));
  if (bc.store) {{
    await sleep(3000, 5000);
    await page.goto(bc.store, {{timeout: 60000}});
    await page.waitForTimeout(4500);
    rec.store_final = await page.url();
    if (/nidlogin|captcha|login/.test(rec.store_final)) {{ rec.error = "LOGIN OR CAPTCHA"; lost = true; }}
    else {{
      const st = await page.evaluate(() => {{
        let cat = "";
        for (const s of document.querySelectorAll("script")) {{
          const m = (s.textContent || "").match(/"wholeCategoryName":"([^"]+)"/);
          if (m) {{ cat = m[1].replace(/\\u002F/g, "/").replace(/\\u003E/g, ">"); break; }}
        }}
        return {{ cat, text: (document.body.innerText || "").slice(0, 3500) }};
      }});
      rec.category = st.cat; rec.st_text = st.text;
    }}
  }} else {{ rec.error = "스토어 주소 없음"; }}
  res.push(rec);
  await fs.writeFile({o}, JSON.stringify({{ lost, res }}), "utf8");
  console.log("CHECK", p.id, rec.has_issue_btn ? "발급버튼O" : "발급버튼X", rec.category || rec.error);
  if (lost) break;
  await sleep(3000, 5000);
}}
await fs.writeFile({o}, JSON.stringify({{ lost, res }}), "utf8");
console.log(lost ? "LOGIN_LOST" : "DONE");
"""


def collect(ids: list[str]) -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    prods = [{"id": i} for i in ids]
    st = pc.collect_in_batches(prods, lambda q: q["id"], RAW, 4, lambda part, tmp: js_check(part, tmp), "check", "상품 상태 확인")
    print("상태:", st)
    return 0 if st == "ok" else 1


def finish() -> None:
    pc._run_ego(f'const task = await taskSpace("{SPACE}"); await task.finish({{ keep: [] }}); console.log("FINISHED");', "finish")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
    rc = 0
    try:
        if cmd == "collect":
            rc = collect(sys.argv[2:] or all_ids())
    finally:
        try:
            finish()
        except Exception:  # noqa: BLE001
            pass
    sys.exit(rc)
