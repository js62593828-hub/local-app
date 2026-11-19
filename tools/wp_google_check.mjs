// 구글 경쟁 확인 - ego lite(비로그인) 쪽. 직접 실행하지 말고 `python tools/wp_google_check.py --in 파일 --out 파일` 로 부른다
// (그 파일이 globalThis.__JOB = {keywords, out} 을 채워서 이 파일을 불러 준다).
// 읽기만 한다: 구글 검색 결과 화면을 열어 보고 글자만 읽는다. 키워드 사이 5~10초 쉰다.
// 구글이 봇 확인 화면을 띄우면 거기서 멈추고 지금까지 결과를 저장한다. 끝나면 창을 닫는다.
const fs = await import("node:fs/promises");
const JOB = globalThis.__JOB;
const task = await taskSpace("wp google check");
const page = task.page("p1");
const wait = ms => page.waitForTimeout(ms);

// ───── 도메인 분류표 (여기만 고치면 된다) ─────
// 큰 곳: 정부·공공 / 대형 언론 / 포털 / 은행
const BIG_SUFFIX = [".go.kr", ".gov.kr", ".mil.kr", ".re.kr"];
const BIG_DOMAINS = [
  // 정부·공공·공단·공사
  "korea.kr", "gov.kr", "nhis.or.kr", "nps.or.kr", "comwel.or.kr", "kosaf.go.kr", "hf.go.kr", "khug.or.kr", "kinfa.or.kr", "semas.or.kr",
  "sbiz.or.kr", "kosmes.or.kr", "kised.or.kr", "work.go.kr", "wage.go.kr", "hikorea.go.kr", "kodit.co.kr", "kibo.or.kr", "lh.or.kr",
  "kepco.co.kr", "korail.com", "kwater.or.kr", "kbiz.or.kr", "kofia.or.kr", "nhfund.or.kr",
  // 대형 언론
  "chosun.com", "joongang.co.kr", "donga.com", "hani.co.kr", "khan.co.kr", "mk.co.kr", "hankyung.com", "sedaily.com", "yna.co.kr", "news1.kr",
  "newsis.com", "kbs.co.kr", "mbc.co.kr", "imbc.com", "sbs.co.kr", "ytn.co.kr", "jtbc.co.kr", "mt.co.kr", "edaily.co.kr", "fnnews.com",
  "heraldcorp.com", "etnews.com", "seoul.co.kr", "hankookilbo.com", "segye.com", "munhwa.com", "kmib.co.kr", "nocutnews.co.kr", "ohmynews.com",
  "asiae.co.kr", "moneys.co.kr", "dt.co.kr", "ajunews.com", "inews24.com", "newspim.com", "bloter.net", "zdnet.co.kr",
  // 포털·대형 플랫폼
  "naver.com", "daum.net", "google.com", "youtube.com", "kakao.com", "namu.wiki", "wikipedia.org", "nate.com",
  // 은행·금융사
  "kbstar.com", "shinhan.com", "wooribank.com", "hanabank.com", "nhbank.com", "ibk.co.kr", "kakaobank.com", "kbanknow.com", "citibank.co.kr",
  "busanbank.co.kr", "knbank.co.kr", "dgb.co.kr", "kjbank.com", "jbbank.co.kr", "suhyup-bank.com", "kfcc.co.kr", "nonghyup.com", "kdb.co.kr",
  "tossbank.com", "samsunglife.com", "hanwhalife.com", "kyobo.co.kr", "samsungfire.com", "hi.co.kr",
];
// 작은 곳으로 확정할 블로그·개인 플랫폼 (naver.com 아래라도 블로그·카페는 여기가 먼저 적용된다)
const SMALL_DOMAINS = [
  "tistory.com", "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "m.cafe.naver.com", "brunch.co.kr", "velog.io", "medium.com", "wordpress.com",
  "blogspot.com", "blog.daum.net", "toss.im", "blog.toss.im", "oopy.io", "notion.site", "postype.com",
];
// 목록에 없는 도메인은 '작은 곳'(작은 사이트·개인 도메인)으로 본다.
const CALC_WORDS = ["계산기", "calculator", "calc."];

function hostOf(href) { try { return new URL(href).hostname.toLowerCase().replace(/^www\./, ""); } catch { return ""; } }
function matches(host, dom) { return host === dom || host.endsWith("." + dom); }
function kindOf(host) {
  if (SMALL_DOMAINS.some(d => matches(host, d))) return "작은 곳";
  if (BIG_SUFFIX.some(s => host.endsWith(s))) return "큰 곳";
  if (BIG_DOMAINS.some(d => matches(host, d))) return "큰 곳";
  return "작은 곳";
}
function verdictOf(top8) {
  const small = top8.filter(r => r.kind === "작은 곳").length;
  const big = top8.filter(r => r.kind === "큰 곳").length;
  if (small >= 3) return "🟢";
  if (big >= 6) return "🔴";
  return "🟡";
}

async function readSerp() {
  return await page.evaluate(() => {
    const body = document.body ? document.body.innerText : "";
    const blocked = /\/sorry\//.test(location.href) || /비정상적인 트래픽|unusual traffic|로봇이 아님|recaptcha/i.test(body.slice(0, 3000));
    const out = { blocked, url: location.href, results: [], ai: false, paa: [], related: [] };
    if (blocked) return out;
    // 상위 결과: 제목(h3)이 들어 있는 링크, 같은 주소 중복 제거
    const seen = new Set();
    const root = document.querySelector("#search") || document.body;
    for (const h of root.querySelectorAll("a h3")) {
      const a = h.closest("a");
      let href = a && a.href;
      if (!href || seen.has(href)) continue;
      seen.add(href);
      // 구글이 주소를 /goto?url=... 로 감싸므로, 화면에 보이는 주소(cite)에서 사이트를 읽는다
      const blk = a.closest("div[data-hveid]") || a.parentElement.parentElement;
      const cite = ((blk && blk.querySelector("cite")) || {}).textContent || "";
      const m = cite.trim().match(/^(?:https?:\/\/)?([^\s\/›]+)/);
      if (m && /^https?:\/\/(www\.)?google\.com\/goto/.test(href)) href = "https://" + m[1] + "/";
      out.results.push({ href, title: h.innerText.trim(), cite: cite.trim().slice(0, 120) });
      if (out.results.length >= 10) break;
    }
    // AI 개요
    out.ai = [...document.querySelectorAll("h1,h2,h3,div[role=heading],span")].some(e => e.children.length === 0 && /^(AI 개요|AI Overview)$/.test((e.textContent || "").trim()));
    // 다른 사용자도 함께 질문함
    const q = new Set();
    for (const e of document.querySelectorAll("[data-q]")) { const t = (e.getAttribute("data-q") || "").trim(); if (t) q.add(t); }
    if (!q.size) {
      const head = [...document.querySelectorAll("div[role=heading],h2,h3,span")].find(e => /다른 사용자도 함께 질문함|People also ask/.test(e.textContent || "") && e.children.length <= 1);
      if (head) {
        const box = head.closest("div[data-initq], div[jscontroller]") || head.parentElement;
        for (const e of box.querySelectorAll("div[role=button] span, div[role=heading] span, [aria-expanded] span")) {
          const t = (e.textContent || "").trim();
          if (t.length > 4 && t.length < 80) q.add(t);
        }
      }
    }
    out.paa = [...q].slice(0, 8);
    // 관련 검색어
    const rel = new Set();
    const bot = document.querySelector("#botstuff") || document.body;
    for (const a of bot.querySelectorAll("a[href*='/search?']")) {
      let u; try { u = new URL(a.href); } catch { continue; }
      const k = u.searchParams.get("q"); const t = (a.innerText || "").trim().replace(/\s+/g, " ");
      if (k && t && t.length < 40 && t.replace(/\s/g, "") === k.replace(/\s/g, "")) rel.add(k);
    }
    out.related = [...rel].slice(0, 12);
    return out;
  });
}

const results = [];
let stopped = "";
for (let i = 0; i < JOB.keywords.length; i++) {
  const kw = JOB.keywords[i];
  const url = "https://www.google.com/search?q=" + encodeURIComponent(kw) + "&hl=ko&gl=kr";
  try {
    await page.goto(url, { timeout: 45000 });
    await wait(2500);
    const s = await readSerp();
    if (s.blocked) { stopped = "구글이 봇 확인 화면을 띄웠습니다 (키워드 '" + kw + "' 에서 멈춤)"; break; }
    const top8 = s.results.map(r => ({ domain: hostOf(r.href), title: r.title, kind: kindOf(hostOf(r.href)) }));
    const calcN = top8.filter(r => CALC_WORDS.some(w => (r.domain + " " + r.title).toLowerCase().includes(w))).length;
    const rec = { keyword: kw, verdict: verdictOf(top8), top8, ai_overview: s.ai, paa: s.paa, related: s.related, calc_intent: calcN >= 3 };
    if (!top8.length) rec.error = "결과를 읽지 못함(화면 모양이 다르거나 로딩 실패)";
    results.push(rec);
  } catch (e) {
    results.push({ keyword: kw, verdict: "", top8: [], ai_overview: false, paa: [], related: [], calc_intent: false, error: String((e && e.message) || e).slice(0, 200) });
  }
  await fs.writeFile(JOB.out, JSON.stringify(results, null, 2), "utf-8");
  if (i < JOB.keywords.length - 1) await wait(5000 + Math.floor(Math.random() * 5001));
}
await fs.writeFile(JOB.out, JSON.stringify(results, null, 2), "utf-8");
console.log(JSON.stringify({ done: results.length, total: JOB.keywords.length, stopped }));
await task.finish({ keep: [] });
