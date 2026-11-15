// 쇼핑커넥트 판매 실적: 엑셀(최근 30일) 저장 + 그래프 날짜별 클릭 환산 + 화면 요약과 합계 대조.
// 매주 사용: OUT 을 '이번 수집의 마지막 주 폴더/raw/' 로 바꾼 새 파일을 만들어 실행. 결과 한 줄에 MATCH / MISMATCH 가 나온다.
const fs = await import("node:fs/promises");
const OUT = "G:/내 드라이브/DataBase/local-app/perf_inbox/2026-10-26/raw/";
const task = await taskSpace("weekly collect");
const page = task.page("p1");
const t0 = Date.now();
await page.goto("https://brandconnect.naver.com/997658497773120/affiliate/sales-dashboard");
await page.waitForLoadState();
await page.waitForTimeout(5000);
let sn = await page.snapshot({ scope: "full_page" });
if (/로그인/.test(sn) && !/로그아웃/.test(sn)) { console.log("LOGIN NEEDED"); throw new Error("login"); }
let L = sn.split("\n").map(l => l.trim());
const period = L.filter(l => /^text "20\d\d-\d\d-\d\d"$/.test(l)).map(l => l.replace(/^text "|"$/g, ""));
const k = L.findIndex(l => l === 'text "클릭 수"' && L[l.length] !== undefined);
const yi = L.findIndex(l => l === 'text "유입 실적"');
const clickIdx = L.findIndex((l, i) => i > yi && l === 'text "클릭 수"');
const numLine = L.slice(clickIdx + 1, clickIdx + 5).find(l => /^text "[\d,]+"$/.test(l));
const screenClicks = Number(numLine.replace(/[^\d]/g, ""));
console.log("period", period, "screen clicks", screenClicks);
// excel icon: the button right after the text "엑셀 다운로드"
const ei = L.findIndex(l => l === 'text "엑셀 다운로드"');
const bref = L[ei + 1].match(/^button \[ref=(\d+)/)[1];
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight / 2));
const dlp = page.waitForEvent("download", { timeout: 25000 }).catch(e => String(e));
await page.click("@" + bref, { label: "download excel file icon" });
const d = await dlp;
if (typeof d === "string") console.log("EXCEL FAILED", d.slice(0, 150));
else { await d.saveAs(OUT + "sales_30d_" + period[1] + ".xlsx"); console.log("excel saved", d.suggestedFilename()); }
// chart: orange (clicks) line -> values by linear fit on left axis ticks
const ch = await page.evaluate(() => {
  const svg = [...document.querySelectorAll("svg")].find(s => s.getBoundingClientRect().width > 1000);
  const sr = svg.getBoundingClientRect();
  const lines = [...svg.querySelectorAll("path")].map(p => ({ s: getComputedStyle(p).stroke, d: p.getAttribute("d") || "" })).filter(p => p.d.length > 60 && p.s !== "none");
  const orange = lines.find(p => /255, 131, 40/.test(p.s));
  const pts = orange ? [...orange.d.matchAll(/[ML]([\d.]+),([\d.]+)/g)].map(m => [Number(m[1]), Number(m[2])]) : [];
  const ticks = [...svg.querySelectorAll("text")].filter(t => /^\d+$/.test(t.textContent.trim())).map(t => { const r = t.getBoundingClientRect(); return { v: Number(t.textContent), x: r.x - sr.x, cy: r.y + r.height / 2 - sr.y }; }).filter(t => t.x < 60);
  const labels = [...svg.querySelectorAll("text")].map(t => t.textContent.trim()).filter(t => /^\d+\. \d+\.$/.test(t));
  return { pts, ticks, labelsShown: labels.length, sy: sr.y };
});
// fit v = a*y + b
const T = ch.ticks; const n = T.length;
const mx = T.reduce((s, t) => s + t.cy, 0) / n, mv = T.reduce((s, t) => s + t.v, 0) / n;
const a = T.reduce((s, t) => s + (t.cy - mx) * (t.v - mv), 0) / T.reduce((s, t) => s + (t.cy - mx) ** 2, 0);
const b = mv - a * mx;
const vals = ch.pts.map(([x, y]) => Math.round(a * y + b));
const start = new Date(period[0] + "T00:00:00Z");
const rows = vals.map((v, i) => { const dt = new Date(start.getTime() + i * 86400000).toISOString().slice(0, 10); return dt + "," + v; });
const sum = vals.reduce((s, v) => s + v, 0);
console.log("chart points", vals.length, "sum", sum, "screen", screenClicks, sum === screenClicks ? "MATCH" : "MISMATCH");
await fs.mkdir(OUT, { recursive: true });
await fs.writeFile(OUT + "clicks_daily_chart.csv", "date,clicks\n" + rows.join("\n") + "\n# chart_sum=" + sum + ",screen_sum=" + screenClicks + ",match=" + (sum === screenClicks) + "\n", "utf8");
console.log("ms", Date.now() - t0);
