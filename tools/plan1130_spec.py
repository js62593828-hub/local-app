"""틀 B 본상품의 스토어 상품 페이지에서 '상품정보 제공고시'·사양 글자를 읽는다(읽기만, 페이지 사이 3~5초).
제목의 궁금증(소음·크기·하중·배터리 등)에 쓸 값이 페이지 글자에 있는지 보려는 용도. 상세 이미지 속 글자는 못 읽음.
사용: python tools/plan1130_spec.py [상품id ...]  → pipeline/stage_plan1130_run/20261123/raw_spec/<id>.json
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_candidates as pc  # noqa: E402
import plan1130_check as ck  # noqa: E402

RAW = ck.RUN / "raw_spec"
SPACE = "plan1130-spec"


def js(prods, out_json):
    o = json.dumps(str(out_json).replace("\\", "/"))
    return f"""
const fs = await import("node:fs/promises");
const sleep = (a, b) => new Promise(r => setTimeout(r, a + Math.random() * (b - a)));
const task = await taskSpace("{SPACE}");
const page = (await task.pages())[0] ?? task.page("p1");
const P = {json.dumps(prods, ensure_ascii=False)};
const res = [];
for (const p of P) {{
  const rec = {{ id: p.id, url: p.url, text: "", imgs: 0, error: "" }};
  await page.goto(p.url, {{timeout: 60000}});
  await page.waitForTimeout(4500);
  if (/nidlogin|captcha|login/.test(await page.url())) {{ rec.error = "LOGIN OR CAPTCHA"; res.push(rec); break; }}
  for (let k = 0; k < 8; k++) {{ await page.mouse.move(600, 500); await page.mouse.wheel(0, 1800); await page.waitForTimeout(900); }}
  const st = await page.evaluate(() => {{
    const btn = [...document.querySelectorAll("button,a")].find(e => /상품정보\s*더보기|펼쳐보기|더보기/.test((e.innerText || "").trim()) && (e.innerText || "").length < 12);
    return {{ text: (document.body.innerText || "").slice(0, 60000), imgs: document.querySelectorAll("img").length }};
  }});
  rec.text = st.text; rec.imgs = st.imgs;
  res.push(rec);
  await fs.writeFile({o}, JSON.stringify({{ res }}), "utf8");
  console.log("SPEC", p.id, st.text.length);
  await sleep(3000, 5000);
}}
await fs.writeFile({o}, JSON.stringify({{ res }}), "utf8");
console.log("DONE");
"""


if __name__ == "__main__":
    ids = sys.argv[1:] or [a for a, b in ck.BPAIR.values()]
    RAW.mkdir(parents=True, exist_ok=True)
    prods = []
    for i in ids:
        f = pc._cache_path(ck.RAW, i)
        d = json.loads(f.read_text(encoding="utf-8"))
        prods.append({"id": i, "url": d["store_final"] or d["store_url"]})
    try:
        pc.collect_in_batches(prods, lambda q: q["id"], RAW, 3, lambda part, tmp: js(part, tmp), "spec", "상세 사양 읽기")
    finally:
        try:
            pc._run_ego(f'const task = await taskSpace("{SPACE}"); await task.finish({{ keep: [] }}); console.log("FINISHED");', "finish")
        except Exception:  # noqa: BLE001
            pass
