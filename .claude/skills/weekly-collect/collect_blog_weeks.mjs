// weekly download: rank (조회수 순위) + referer (유입분석) for given Monday dates.
// 매주 사용 방법: WEEKS 와 DAYS 만 이번 주 값으로 바꾼 '새 파일'(round-NN.mjs)을 만들어 실행한다. (SKILL.md 참고)
//   DAYS[월요일] = [달력에서 누를 그 주의 수요일 '일' 숫자, 달력을 '이전 달'로 넘길 횟수(앞으로 넘기면 음수)]
//   달력은 마지막 완료 주가 있는 달에서 열린다. 그 달 안의 주면 두 번째 값은 0.
const WEEKS = ["2026-10-12", "2026-10-19", "2026-10-26"];
const DAYS = { "2026-10-12": ["14", 0], "2026-10-19": ["21", 0], "2026-10-26": ["28", 0] };
const OUT = "G:/내 드라이브/DataBase/local-app/perf_inbox/";
const task = await taskSpace("weekly collect");
const page = task.page("p1");
const t0 = Date.now();
async function fsnap() {
  const s = await page.snapshot();
  const L = s.split("\n");
  const st = L.findIndex(l => /^\s*iframe \[ref=/.test(l));
  return await page.snapshot({ scope: "subtree", root: "@" + L[st].match(/ref=(\d+)/)[1] });
}
async function clickT(t, label, pick = "first") {
  const L = (await fsnap()).split("\n").map(l => l.trim());
  const idx = L.map((l, n) => l === 'text "' + t + '"' && /^(anchor|button|radio|menuitemradio)/.test(L[n - 1] || "") ? n : -1).filter(n => n > 0);
  if (!idx.length) throw new Error("not found: " + t);
  const n = pick === "last" ? idx[idx.length - 1] : idx[0];
  await page.click("@" + L[n - 1].match(/ref=(\d+)/)[1], { label });
  await page.waitForTimeout(900);
}
async function periodLabel() {
  const L = (await fsnap()).split("\n").map(l => l.trim());
  const l = L.find(x => /^text "\d{4}\.\d\d\.\d\d\. 주간"/.test(x));
  return l ? l.replace(/^text "|"$/g, "") : "";
}
async function selectWeek(monday, dayInfo) {
  const [day, back] = dayInfo;
  await clickT("달력보기", "open calendar");
  for (let i = 0; i < Math.abs(back); i++) await clickT(back > 0 ? "이전 달" : "다음 달", "change calendar month");
  // calendar day anchors: after the weekday header 토, before 선택1. choose day (first occurrence; 'last' if day>=25)
  const L = (await fsnap()).split("\n").map(l => l.trim());
  const s = L.findIndex(l => l === 'text "토"');
  const e = L.findIndex((l, i) => i > s && l === 'text "선택1"');
  const idx = [];
  for (let i = s; i < e; i++) if (L[i] === 'text "' + day + '"' && /^anchor/.test(L[i - 1])) idx.push(i);
  const n = Number(day) >= 25 ? idx[idx.length - 1] : idx[0];
  await page.click("@" + L[n - 1].match(/ref=(\d+)/)[1], { label: "pick week day " + day });
  await page.waitForTimeout(1200);
  const lab = await periodLabel();
  const want = monday.replace(/-/g, ".") + ". 주간";
  return { lab, ok: lab === want };
}
async function grab(radio, tag, monday) {
  await clickT(radio, "select " + radio);
  const r = await page.evaluate(() => 1);
  await clickT("주간", "select weekly unit");
  const w = await selectWeek(monday, DAYS[monday]);
  console.log(" ", monday, tag, "period label:", w.lab, w.ok ? "OK" : "MISMATCH");
  if (!w.ok) return false;
  const L = (await fsnap()).split("\n").map(l => l.trim());
  const idxs = L.map((l, n) => l === 'text "지표 다운로드"' && /^anchor/.test(L[n - 1]) ? n : -1).filter(n => n > 0);
  const dlp = page.waitForEvent("download", { timeout: 25000 }).catch(e => String(e));
  await page.click("@" + L[idxs[idxs.length - 1] - 1].match(/ref=(\d+)/)[1], { label: "download metrics file" });
  const d = await dlp;
  if (typeof d === "string") { console.log("  no download", d.slice(0, 120)); return false; }
  await d.saveAs(OUT + monday + "/raw/" + tag + "_" + monday + ".xlsx");
  console.log("  saved", tag, monday, "<-", d.suggestedFilename());
  return true;
}
for (const mon of WEEKS) {
  await page.goto("https://admin.blog.naver.com/beafather/stat/download");
  await page.waitForLoadState();
  await page.waitForTimeout(3500);
  const first = (await page.snapshot()).includes("로그인") && !(await page.snapshot()).includes("내 블로그");
  if (first) { console.log("LOGIN NEEDED"); break; }
  await grab("조회수 순위", "rank_weekly", mon);
  await page.goto("https://admin.blog.naver.com/beafather/stat/download");
  await page.waitForLoadState();
  await page.waitForTimeout(3500);
  await grab("유입분석", "referer_weekly", mon);
  console.log("week done", mon, Date.now() - t0);
}
