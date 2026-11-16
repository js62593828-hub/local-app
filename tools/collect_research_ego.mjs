// 상품 자료 수집 - 브라우저(ego lite) 쪽. tools/collect_research.py 가 globalThis.__JOB 을 채워서 불러 쓴다.
// 읽기만 한다: 상품 페이지 열기, 리뷰 창 열기, 정렬·쪽 넘김(보기 조작)만. 구매·찜·장바구니·링크 복사 버튼은 누르지 않는다.
// 제휴 링크(naver.me)는 열지 않는다(클릭 수에 잡히므로). links.json 의 product_url(브랜드커넥트 상품 정보)에서 '네이버 쇼핑에서 보기' 주소로 간다.
const fs = await import("node:fs/promises");
const JOB = globalThis.__JOB;
const task = await taskSpace("research collect");
const page = task.page("p1");
const wait = ms => page.waitForTimeout(ms);
const num = s => { const m = String(s || "").replace(/,/g, "").match(/\d+(?:\.\d+)?/); return m ? Number(m[0]) : null; };

// 리뷰 창 안의 후기를 읽어 목록으로 만든다
async function readReviews() {
  return await page.evaluate(() => {
    const D = document.querySelector("[role=dialog]");
    if (!D) return null;
    const out = [];
    for (const li of D.querySelectorAll("li")) {
      const t = li.innerText || "";
      if (!/\d{2}\.\d{2}\.\d{2}\./.test(t) || !/리뷰가 도움이 되었나요/.test(t)) continue;
      const lines = t.split("\n").map(s => s.trim());
      const first = lines[0] || "";
      const rating = Number((first.match(/^[1-5]/) || [])[0] || 0);
      const tags = first.replace(/^[1-5]/, "");
      const dm = (lines[1] || "").match(/(\d{2}\.\d{2}\.\d{2}\.)/);
      const date = dm ? dm[1] : "";
      let opt = "";
      const body = [];
      let stop = false;
      for (const l of lines.slice(2)) {
        if (stop) break;
        if (/^(색상|옵션|구성|사이즈|용량|종류)\s*:/.test(l) && !opt && !body.length) { opt = l; continue; }
        if (/^(더보기|이미지 펼치기|사진\/비디오 수|비디오|판매자|리뷰가 도움이 되었나요\?)$/.test(l)) { if (l.startsWith("리뷰가") || l === "판매자") stop = true; continue; }
        if (/^\d+$/.test(l) && body.length === 0) continue;
        if (l) body.push(l);
      }
      const hm = t.match(/도움돼요\s*(\d+)/);
      out.push({ rating, tags, date, option: opt, text: body.join(" "), helpful: hm ? Number(hm[1]) : 0 });
    }
    return out;
  });
}
// 정렬: '랭킹순' 같은 현재 정렬 버튼을 눌러 목록을 펼친 뒤 '<이름>정렬하기' 선택 버튼을 누른다(보기 조작)
async function setSort(name) {
  const open = await page.evaluate(() => {
    const D = document.querySelector("[role=dialog]");
    const b = D && [...D.querySelectorAll("button")].find(e => /^(랭킹순|최신순|평점 높은순|평점 낮은순)$/.test(e.textContent.trim()));
    if (!b) return false;
    b.click(); return true;
  });
  if (!open) return false;
  await wait(700);
  const ok = await page.evaluate(n => {
    const D = document.querySelector("[role=dialog]");
    const b = D && [...D.querySelectorAll("button")].find(e => e.textContent.trim() === n + "정렬하기");
    if (!b) return false;
    b.click(); return true;
  }, name);
  await wait(2500);
  return ok;
}
// 후기는 아래로 스크롤하면 20개씩 더 불러온다
async function scrollMore() {
  await page.evaluate(() => {
    const D = document.querySelector("[role=dialog]");
    const sc = [D, ...D.querySelectorAll("*")].find(e => e.scrollHeight > e.clientHeight + 200 && /auto|scroll/.test(getComputedStyle(e).overflowY));
    if (sc) sc.scrollTop = sc.scrollHeight;
  });
  await wait(2200);
}
async function collectSorted(sortName, want) {
  const ok = await setSort(sortName);
  if (!ok) return { sort: sortName, error: "정렬 선택 실패", reviews: [] };
  await page.evaluate(() => { const D = document.querySelector("[role=dialog]"); const sc = [D, ...D.querySelectorAll("*")].find(e => e.scrollHeight > e.clientHeight + 200 && /auto|scroll/.test(getComputedStyle(e).overflowY)); if (sc) sc.scrollTop = 0; });
  await wait(600);
  let rows = [];
  for (let i = 0; i < 4; i++) {
    rows = (await readReviews()) || [];
    if (rows.length >= want) break;
    await scrollMore();
  }
  const seen = new Set(), all = [];
  for (const r of rows) { const k = r.date + "|" + r.text.slice(0, 40); if (!seen.has(k)) { seen.add(k); all.push(r); } }
  return { sort: sortName, reviews: all.slice(0, want) };
}

const results = [];
let firstItem = true;
for (const it of JOB.items) {
  if (!firstItem) await wait(3000 + Math.floor(Math.random() * 2000)); // 페이지 사이 3~5초
  firstItem = false;
  const rec = { product_id: it.product_id, name: it.name, page_url: it.product_url, error: null };
  try {
    // 1) 브랜드커넥트 상품 정보(가격·평점·리뷰 수·수수료, 스토어 주소)
    await page.goto(it.product_url, { timeout: 45000 });
    await wait(3500);
    const cur = await page.url();
    if (/nidlogin|login/.test(cur)) { rec.error = "LOGIN NEEDED"; results.push(rec); break; }
    const bc = await page.evaluate(() => {
      const a = [...document.querySelectorAll("a")].find(e => e.innerText.trim().startsWith("네이버 쇼핑에서 보기"));
      return { store_url: a ? a.href : "", text: document.body.innerText.slice(0, 1500) };
    });
    rec.store_url_from_page = bc.store_url;
    if (!bc.store_url) { rec.error = "스토어 주소 없음"; results.push(rec); continue; }
    // 2) 스토어 상품 페이지
    await page.goto(bc.store_url, { timeout: 45000 });
    await wait(5000);
    const url2 = await page.url();
    if (/nidlogin|captcha|login/.test(url2)) { rec.error = "LOGIN OR CAPTCHA NEEDED: " + url2; results.push(rec); break; }
    rec.final_url = url2;
    rec.collected_at = new Date(Date.now() + 9 * 3600e3).toISOString().replace("T", " ").slice(0, 19);
    const top = await page.evaluate(() => {
      const t = document.body.innerText;
      const g = re => { const m = t.match(re); return m ? m[1] : ""; };
      const imgs = [...document.querySelectorAll("img")].filter(i => i.alt === "대표이미지").map(i => i.currentSrc || i.src);
      return {
        title: document.title,
        h: g(/\n([^\n]+)\n평점\n/),
        rating: g(/평점\n([\d.]+)/),
        rating_6m: g(/최근 6개월 ([\d.]+)/),
        review_count: g(/\)?([\d,]+)건 리뷰/),
        list_price: g(/할인 전 가격\n([\d,]+)원/),
        price: g(/상품 가격\n([\d,]+)원/) || g(/\n([\d,]+)원\n배송비/),
        main_image: imgs[0] || "",
      };
    });
    Object.assign(rec, { page_name: top.h, page_title: top.title, rating: num(top.rating), rating_6m: num(top.rating_6m), review_count: num(top.review_count), price: num(top.price), list_price: num(top.list_price), main_image_url: top.main_image });
    // 2b) 추가 이미지: 이미 연 이 페이지의 넘겨 보는 목록(캐러셀)에서 원본 주소로 받는다(페이지를 더 열지 않음)
    if (it.img_dir && !it.skip_images) {
      try {
        const car = await page.evaluate(() => [...document.querySelectorAll("img")].filter(i => i.alt === "대표이미지").map(i => {
          const r = i.getBoundingClientRect();
          return { url: i.getAttribute("data-src") || i.currentSrc || i.src, x: Math.round(r.x) };
        }));
        const strip = u => u.replace(/\?.*$/, "");
        const seen = new Set(), list = [];
        for (const c of car) { const u = strip(c.url); if (u && !seen.has(u)) { seen.add(u); list.push({ url: u, x: c.x }); } }
        rec.carousel_total = list.length;
        // 대표 이미지(승인 목록 주소와 같은 것)를 첫 장으로. 그보다 앞에 있던 것은 목록 끝(한 바퀴 돌아온 칸)일 수 있어 '순서 확인 필요'
        let ri = it.rep_hint ? list.findIndex(c => c.url.includes(it.rep_hint)) : -1;
        rec.rep_found_in_carousel = ri >= 0;
        if (ri < 0) ri = 0;
        const ordered = [...list.slice(ri).map(c => ({ ...c, order_uncertain: false })), ...list.slice(0, ri).map(c => ({ ...c, order_uncertain: true }))].slice(0, JOB.max_images || 10);
        await fs.mkdir(it.img_dir, { recursive: true });
        rec.images = [];
        let n = 0;
        for (const c of ordered) {
          n += 1;
          const row = { n, url: c.url, x: c.x, order_uncertain: c.order_uncertain };
          try {
            const res = await fetch(c.url);
            if (!res.ok) throw new Error("HTTP " + res.status);
            const ct = res.headers.get("content-type") || "";
            const ext = /png/.test(ct) ? "png" : /webp/.test(ct) ? "webp" : /gif/.test(ct) ? "gif" : "jpg";
            const buf = Buffer.from(await res.arrayBuffer());
            row.file = (n === 1 ? "01_rep" : String(n).padStart(2, "0")) + "." + ext;
            await fs.writeFile(it.img_dir + "/" + row.file, buf);
            row.bytes = buf.length; row.type = ct;
          } catch (e) { row.error = String(e.message).slice(0, 120); }
          rec.images.push(row);
        }
      } catch (e) { rec.images_error = String(e.message).slice(0, 160); }
    }
    // 3) 상품정보 제공고시(사양 표)
    await page.evaluate(() => { const b = [...document.querySelectorAll("button")].find(e => e.innerText.trim() === "상품정보 제공고시"); if (b) b.scrollIntoView({ block: "center" }); });
    await wait(600);
    try { await page.click('text="상품정보 제공고시"', { label: "expand product notice" }); } catch (e) { rec.notice_error = String(e.message).slice(0, 120); }
    await wait(1500);
    // 제공고시는 '항목 이름 줄 / 값 줄(여러 줄일 수 있음)' 순서라, 줄 그대로 저장하고 항목 나누기는 파이썬이 한다
    rec.notice_lines = await page.evaluate(() => {
      const D = document.querySelector("[role=dialog]");
      if (!D) return [];
      const L = D.innerText.split("\n").map(s => s.trim()).filter(Boolean);
      const end = L.indexOf("닫기");
      return (end >= 0 ? L.slice(0, end) : L).filter(l => l !== "상품정보 제공고시");
    });
    await page.evaluate(() => { const b = [...document.querySelectorAll("[role=dialog] button")].find(e => e.innerText.trim() === "닫기"); if (b) b.click(); });
    await wait(900);
    // 4) 대표 이미지 (필요할 때만 저장)
    if (it.save_image_to && rec.main_image_url) {
      try {
        // page.fetch 는 브라우저 CORS 에 막히므로 노드의 fetch 로 받는다. 원본 그대로 저장(가공 없음)
        const res = await fetch(rec.main_image_url.replace(/\?type=.*$/, ""));
        if (!res.ok) throw new Error("HTTP " + res.status);
        const buf = Buffer.from(await res.arrayBuffer());
        await fs.mkdir(it.save_image_to.replace(/[\/][^\/]*$/, ""), { recursive: true });
        await fs.writeFile(it.save_image_to, buf);
        rec.image_saved = it.save_image_to; rec.image_type = res.headers.get("content-type"); rec.image_bytes = buf.length;
      } catch (e) { rec.image_error = String(e.message).slice(0, 160); }
    }
    // 5) 리뷰: 리뷰 영역 → 전체보기(창) → 랭킹순 N개 + 최신순 N개 + 평점 낮은순 N개
    await page.evaluate(() => { const R = document.getElementById("REVIEW"); if (R) R.scrollIntoView(); });
    await wait(1800);
    rec.ai_summary = await page.evaluate(() => { const R = document.getElementById("REVIEW"); if (!R) return []; const t = R.innerText; const m = t.match(/분석했어요!\n\n([\s\S]*?)\n\+\d+/); return m ? [...new Set(m[1].split("\n").map(s => s.trim()).filter(Boolean))] : []; });
    const opened = await page.evaluate(() => { const R = document.getElementById("REVIEW"); const b = R && [...R.querySelectorAll("a,button")].find(e => e.innerText.trim() === "리뷰 전체보기"); if (!b) return false; b.click(); return true; });
    await wait(2800);
    if (!opened) { rec.reviews = { error: "리뷰 전체보기 없음" }; }
    else {
      const rk = await collectSorted("랭킹순", JOB.per_sort);
      const rc = await collectSorted("최신순", JOB.per_sort);
      const rl = await collectSorted("평점 낮은순", JOB.per_sort);
      rec.reviews = { ranking: rk, recent: rc, lowest: rl };
      await page.evaluate(() => { const b = [...document.querySelectorAll("[role=dialog] button")].find(e => e.innerText.trim() === "닫기"); if (b) b.click(); });
      await wait(800);
    }
  } catch (e) {
    rec.error = String(e && e.message || e).slice(0, 300);
  }
  await fs.mkdir(JOB.raw_dir, { recursive: true });
  await fs.writeFile(JOB.raw_dir + "/" + it.product_id + ".json", JSON.stringify(rec, null, 1), "utf8");
  console.log("DONE", it.product_id, rec.error || "ok", "ranking", rec.reviews?.ranking?.reviews?.length, "recent", rec.reviews?.recent?.reviews?.length, "lowest", rec.reviews?.lowest?.reviews?.length);
  results.push(rec);
  if (rec.error && /LOGIN/.test(rec.error)) break;
}
await task.finish({ keep: [] });
