// 발급 링크 관리 목록(10개씩 6쪽 등)을 links_list.csv 로 저장. 읽기만 한다(링크 발급·복사 버튼 안 누름).
// 매주 사용: OUT 을 판매 실적 파일과 같은 폴더(마지막 주 폴더/raw/)로 바꾼 새 파일을 만들어 실행.
// 판매 실적 화면 다음에 이어서 실행하면 메뉴 이동이 자동으로 된다. 단독 실행이면 바로 위 메뉴 클릭 구간이 작동한다.
const fs = await import("node:fs/promises");
const OUT = "G:/내 드라이브/DataBase/local-app/perf_inbox/2026-10-26/raw/";
const task = await taskSpace("weekly collect");
const page = task.page("p1");
const t0 = Date.now();
if (!(await page.url()).includes("products-link")) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.click("loc=href:/997658497773120/affiliate/products-link", { label: "open issued links menu" });
  await page.waitForTimeout(4500);
}
function parseRows(sn) {
  const L = sn.split("\n").map(l => l.trim());
  const starts = L.map((l, i) => /^anchor \[ref=\d+, loc=href:\/997658497773120\/affiliate\/products\/(\d+)/.test(l) ? i : -1).filter(i => i >= 0);
  const rows = [];
  for (let s = 0; s < starts.length; s++) {
    const seg = L.slice(starts[s], s + 1 < starts.length ? starts[s + 1] : starts[s] + 60);
    const id = seg[0].match(/products\/(\d+)/)[1];
    const texts = seg.filter(l => /^text "/.test(l)).map(l => l.replace(/^text "|"$/g, ""));
    const di = texts.findIndex(t => /^\d{4}\.\d\d\.\d\d$/.test(t));
    rows.push({ id, name: texts[0], store: texts[1], status: texts[di - 1], issued: texts[di], profit: texts[di - 2], fee: (texts.slice(2, di).join("|").match(/(\d+)\|%/) || [])[1] || "" });
  }
  return rows;
}
const all = [];
const seen = new Set();
for (let p = 1; p <= 8; p++) {
  let sn = await page.snapshot({ scope: "full_page" });
  let L = sn.split("\n").map(l => l.trim());
  if (p > 1) {
    const i = L.findIndex((l, n) => l === 'text "' + p + '"' && /^button \[ref=/.test(L[n - 1] || ""));
    if (i < 0) break;
    await page.click("@" + L[i - 1].match(/ref=(\d+)/)[1], { label: "go to list page " + p });
    await page.waitForTimeout(1800);
    sn = await page.snapshot({ scope: "full_page" });
  }
  const rows = parseRows(sn);
  let added = 0;
  for (const r of rows) { const key = r.id + "|" + r.issued; if (!seen.has(key)) { seen.add(key); all.push(r); added++; } }
  console.log("page", p, "rows", rows.length, "added", added);
  if (!added) break;
}
const q = s => '"' + String(s ?? "").replace(/"/g, '""') + '"';
const csv = "상품ID,상품명,스토어,수수료(%),예상 수익,진행 상태,링크 발급일\n" + all.map(r => [r.id, q(r.name), q(r.store), r.fee, q(r.profit), r.status, r.issued].join(",")).join("\n") + "\n";
await fs.writeFile(OUT + "links_list.csv", "\ufeff" + csv, "utf8");
console.log("total", all.length, "ms", Date.now() - t0);
