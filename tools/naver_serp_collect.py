# -*- coding: utf-8 -*-
"""네이버 통합검색 결과 구조 수집 (ego lite 브라우저 사용, 읽기 전용)

키워드 하나를 네이버에서 검색해서 아래 내용을 JSON 으로 저장한다.
  - 스마트블록 제목 목록
  - 관련 검색어
  - 광고 / 쇼핑 / 이미지 영역이 있는지
  - 화면에 나온 모든 영역 (순서, 제목, 종류) ← 분류가 맞는지 직접 확인용
  - 수집 시각

앱 장부(articles*.json, content_queue*.json)는 건드리지 않는다.
결과는 serp_collect/ 폴더에만 쓴다.

사용법
  python tools/naver_serp_collect.py 캠핑 의자 추천
  python tools/naver_serp_collect.py "캠핑 의자 추천" --out C:/temp/result.json
  python tools/naver_serp_collect.py "캠핑 의자 추천" --no-save     # 화면에만 출력
  python tools/naver_serp_collect.py                              # 키워드를 물어봄
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "serp_collect"
MARK = "@@SERP_RESULT@@"
TASK_SPACE_NAME = "naver-serp-collect"
RUN_TIMEOUT_SEC = 180

# 브라우저 안에서 돌릴 스크립트. __KEYWORD_JSON__ 자리에 키워드가 들어간다.
JS_TEMPLATE = r"""
const KEYWORD = __KEYWORD_JSON__;
const SPACE_NAME = __SPACE_JSON__;
const MARK = __MARK_JSON__;
const toAscii = (s) => s.replace(/[\u007f-\uffff]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
const task = await taskSpace(SPACE_NAME);
let result;
try {
  const page = task.page("p1");
  const url = "https://search.naver.com/search.naver?where=nexearch&query=" + encodeURIComponent(KEYWORD);
  await page.goto(url, { timeout: 30000 });
  await page.waitForLoadState();
  // 아래쪽 영역은 스크롤해야 불러오므로 끝까지 천천히 내린다.
  await page.evaluate(async () => {
    let last = -1;
    for (let i = 0; i < 25; i++) {
      window.scrollBy(0, 1000);
      await new Promise((r) => setTimeout(r, 400));
      const y = window.scrollY;
      if (y === last) break;
      last = y;
    }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(1000);
  // AI 브리핑: 접혀 있으면 펼치고, 출처 글 링크가 늦게 그려지므로 나타날 때까지 잠시 기다린다.
  const AI_ROOT = `(() => {
    const byId = document.querySelector("[data-block-id^='ai-briefing']");
    if (byId) return byId;
    const h = [...document.querySelectorAll("#main_pack h2")].find((x) => /^AI 브리핑/.test((x.innerText || "").trim()));
    return h ? h.closest(".sc_new") : null;
  })()`;
  const hasAi = await page.evaluate(`!!${AI_ROOT}`);
  if (hasAi) {
    await page.evaluate(`(() => {
      const ai = ${AI_ROOT};
      const b = [...ai.querySelectorAll("button, a")].find((x) => /펼쳐서 더보기/.test((x.innerText || "").replace(/\\s+/g, " ")));
      if (b) b.click();
    })()`);
    try {
      await page.waitForFunction(`(() => {
        const ai = ${AI_ROOT};
        return !!ai && [...ai.querySelectorAll("a[href]")].some((a) => /^https?:/.test(a.href) && !/ader\\.naver\\.com|search\\.naver\\.com/.test(a.href));
      })()`, undefined, { timeout: 8000 });
    } catch (e) { /* 출처가 없는 브리핑일 수 있음 → 빈 목록으로 진행 */ }
    await page.waitForTimeout(500);
  }
  result = await page.evaluate(() => {
    const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
    const main = document.querySelector("#main_pack") || document.body;
    const aiRoot = document.querySelector("[data-block-id^='ai-briefing']");
    const FIXED = ["블로그", "카페", "동영상", "뉴스", "지식iN", "웹사이트", "인플루언서",
      "지식백과", "어학사전", "클립", "네이버 클립", "관련질문", "인기글", "웹문서", "사이트", "도서", "학술정보"];
    const RELATED = /^(관련 검색어|연관 검색어|함께 많이 찾는)/;
    const blocks = [...main.querySelectorAll(".sc_new")].filter((el) => !el.parentElement.closest(".sc_new"));
    const sections = [];
    const related = [];
    const seen = new Set();
    const titleOf = (el) => {
      const h = el.querySelector("h2") || el.querySelector("h3");
      if (h) return clean(h.innerText);
      // 제목 태그가 없으면 '눈에 보이는' 첫 글자를 제목으로 쓴다 (숨김 안내문 '새 창 열림' 등 제외)
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const t = clean(n.textContent);
        const p = n.parentElement;
        if (!t || !p || p.closest(".blind")) continue;
        const r = p.getBoundingClientRect();
        if (r.width <= 2 || r.height <= 2) continue;
        return t.slice(0, 40);
      }
      return "";
    };
    const typeOf = (el, title) => {
      const cls = (el.className || "").toString();
      if (el === aiRoot || /^AI 브리핑/.test(title)) return "AI브리핑";
      if (/ad_section/.test(cls) || /관련 광고|파워링크|파워컨텐츠|브랜드검색|브랜드 콘텐츠/.test(title)) return "광고";
      if (/_root_shp|_root_shs/.test(cls) || /가격비교|플러스 스토어|쇼핑/.test(title)) return "쇼핑";
      if (/^이미지/.test(title)) return "이미지";
      if (RELATED.test(title)) return "관련검색어";
      if (FIXED.some((f) => title === f || title.startsWith(f + " "))) return "기본영역";
      return "스마트블록";
    };
    for (const el of blocks) {
      const title = titleOf(el);
      if (!title || seen.has(title)) continue;
      // 제목(h2)이 없는 묶음은 글 한 건 단위인 경우가 많아 건너뛴다. 단, 관련 검색어 영역은 예외.
      if (!el.querySelector("h2") && !RELATED.test(title)) continue;
      seen.add(title);
      const type = typeOf(el, title);
      sections.push({ order: sections.length + 1, title, type });
      if (type === "관련검색어") {
        for (const a of el.querySelectorAll("a[href*='query=']")) {
          // 첫 줄만 쓴다 (뒤에 붙는 '많이 찾는' 같은 배지 문구 제외)
          const t = clean((a.innerText || "").split("\n").find((x) => x.trim()) || "");
          if (t && t.length <= 40 && !/더보기|^관련 검색어/.test(t) && !related.includes(t)) related.push(t);
        }
      }
    }
    // 블록 밖에 따로 있는 제목도 놓치지 않게 '기타'로 남긴다.
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      return r.width > 2 && r.height > 2 && !el.closest(".blind");
    };
    for (const h of main.querySelectorAll("h2")) {
      if (h.closest(".sc_new") || !visible(h)) continue;
      const title = clean(h.innerText);
      if (!title || seen.has(title)) continue;
      seen.add(title);
      sections.push({ order: sections.length + 1, title, type: "기타" });
    }
    const has = (t) => sections.some((s) => s.type === t);
    // AI 브리핑이 인용한 출처 글 (광고·네이버 검색 내부 링크 제외, 같은 주소는 한 번만)
    const aiSources = [];
    const aiBlock = aiRoot || blocks.find((el) => /^AI 브리핑/.test(titleOf(el)));
    if (aiBlock) {
      const LABEL = /^(새 창 열림|네이버 블로그|네이버 카페|네이버 포스트|네이버 지식iN|지식iN|블로그|카페|포스트|유튜브|YouTube)$/;
      const urls = new Set();
      for (const a of aiBlock.querySelectorAll("a[href]")) {
        if (!/^https?:/.test(a.href) || /ader\.naver\.com|search\.naver\.com/.test(a.href)) continue;
        const u = new URL(a.href);
        const url = u.origin + u.pathname;  // 카페 링크 뒤의 긴 추적용 꼬리표 제거
        if (urls.has(url)) continue;
        // 링크 안 글자 조각 중 작성자·사이트 이름·숨김 안내를 빼고 가장 긴 것을 글 제목으로 본다.
        const parts = [...a.querySelectorAll("*")]
          .filter((e) => e.children.length === 0 && !e.closest(".blind"))
          .map((e) => clean(e.textContent))
          .filter((t) => t && !LABEL.test(t));
        const title = parts.sort((x, y) => y.length - x.length)[0]
          || clean(a.innerText).replace(/\s*새 창 열림$/, "");
        if (!title) continue;
        urls.add(url);
        aiSources.push({ title, url });
      }
    }
    // 블로그·인기글 피드: 이 화면에는 '블로그/인기글' 제목이 따로 없고, 제목 없는 글 카드가 이어서 나온다.
    // 영역 위치(몇 번째 영역인지)와 위에서부터 글 5개의 카드 정보를 모은다. (옛 화면의 '블로그/인기글' 제목 영역도 처리)
    const POST_RE = /blog\.naver\.com\/([^\/?#]+)\/(\d+)/;
    const shown = blocks.filter((el) => { const r = el.getBoundingClientRect(); return r.width > 2 && r.height > 2; });
    const cards = [];
    const cardSeen = new Set();
    let feedIdx = -1;
    shown.forEach((el, i) => {
      if (cards.length >= 5) return;
      const hasH2 = !!el.querySelector("h2");
      if (hasH2 && !/^(블로그|인기글)( |$)/.test(titleOf(el))) return;
      const links = [...el.querySelectorAll("a[href]")].filter((x) => POST_RE.test(x.href));
      if (!links.length) return;
      if (feedIdx < 0) feedIdx = i;
      const unit = hasH2 ? links : [links[0]];
      for (const a of unit) {
        const m = a.href.match(POST_RE);
        if (cardSeen.has(m[2]) || cards.length >= 5) continue;
        cardSeen.add(m[2]);
        const root = hasH2 ? (a.closest("li") || el) : el;
        const strip = (t) => clean(t).replace(/\s*새 창 열림$/, "");
        const head = root.querySelector(".sds-comps-profile-info-title");
        const profA = root.querySelector(".sds-comps-profile-source a");
        const sub = root.querySelector(".sds-comps-profile-info-subtexts");
        const titleA = [...root.querySelectorAll("a[href]")].find((x) => x.href.indexOf("/" + m[2]) >= 0 && !x.closest(".sds-comps-profile") && strip(x.innerText));
        cards.push({
          rank: cards.length + 1, url: "https://blog.naver.com/" + m[1] + "/" + m[2], blog_id: m[1], log_no: m[2],
          name: head ? strip(head.innerText) : "", date_text: sub ? strip(sub.innerText) : "",
          title: titleA ? strip(titleA.innerText) : strip(a.innerText),
          influencer: !!(profA && /in\.naver\.com/.test(profA.href)),
          official_badge: !!(head && head.querySelector("svg")),
        });
      }
    });
    const titledBefore = feedIdx < 0 ? 0 : shown.slice(0, feedIdx).filter((el) => el.querySelector("h2") || RELATED.test(titleOf(el))).length;
    const blogFeed = {
      order: feedIdx < 0 ? null : feedIdx + 1,
      titled_order: feedIdx < 0 ? null : titledBefore + 1,
      y: feedIdx < 0 ? null : Math.round(shown[feedIdx].getBoundingClientRect().top + window.scrollY),
      total_blocks: shown.length,
      cards,
    };
    return {
      smart_blocks: sections.filter((s) => s.type === "스마트블록").map((s) => s.title),
      related_keywords: related,
      has_ai_briefing: !!aiBlock,
      ai_briefing_sources: aiSources,
      has_ad: has("광고"),
      has_shopping: has("쇼핑"),
      has_image: has("이미지"),
      sections,
      blog_feed: blogFeed,
      url: location.href,
    };
  });
} finally {
  await task.finish({ keep: [] });
}
console.log(MARK + toAscii(JSON.stringify(result)));
"""


def build_script(keyword: str) -> bytes:
    # 키워드는 맨 마지막에 넣는다 (키워드 안의 글자가 다른 자리표시로 오인되지 않게)
    js = (JS_TEMPLATE
          .replace("__SPACE_JSON__", json.dumps(TASK_SPACE_NAME))
          .replace("__MARK_JSON__", json.dumps(MARK))
          .replace("__KEYWORD_JSON__", json.dumps(keyword)))
    # 한글이 셸/인코딩 문제로 깨지지 않도록 전부 \uXXXX 로 바꿔 순수 ASCII 로 보낸다.
    def esc(ch: str) -> str:
        if ord(ch) < 0x80:
            return ch
        units = ch.encode("utf-16-be")  # 이모지 등은 두 조각(서로게이트)으로 나눈다
        return "".join(f"\\u{units[i]:02x}{units[i + 1]:02x}" for i in range(0, len(units), 2))
    return "".join(esc(c) for c in js).encode("ascii")


BLOG_HOSTS = ("blog.naver.com", "m.blog.naver.com", "in.naver.com", "tistory.com", "brunch.co.kr")


def ai_blog_info(sources: list) -> dict:
    """AI 브리핑 출처 중 블로그(네이버 블로그·인플루언서·티스토리·브런치) 개수."""
    n = 0
    for src in sources:
        host = urlparse(src.get("url") or "").netloc.lower()
        if any(host == h or host.endswith("." + h) for h in BLOG_HOSTS):
            n += 1
    return {"ai_briefing_blog_count": n, "ai_briefing_has_blog": n > 0}


def collect(keyword: str) -> dict:
    exe = shutil.which("ego-browser")
    if not exe:
        raise RuntimeError("ego-browser 를 찾을 수 없습니다. ego lite 가 설치되어 있는지 확인하세요.")
    started = datetime.now().astimezone()
    try:
        proc = subprocess.run([exe, "nodejs"], input=build_script(keyword),
                              capture_output=True, timeout=RUN_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{RUN_TIMEOUT_SEC}초가 지나도 끝나지 않아 중단했습니다. "
                           "ego lite 에 'naver-serp-collect' 작업 창이 남아 있으면 직접 닫고 다시 실행하세요.")
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    # ego-browser 는 console.log 를 오류 통로(stderr)로 내보내므로 양쪽 모두에서 찾는다.
    line = next((l for l in (out + "\n" + err).splitlines() if l.startswith(MARK)), None)
    if line is None:
        raise RuntimeError("수집 결과를 받지 못했습니다.\n--- 출력 ---\n" + out[-2000:] + "\n--- 오류 ---\n" + err[-2000:])
    data = json.loads(line[len(MARK):])
    notice = [l for l in (out + "\n" + err).splitlines() if "[ego-browser:notice]" in l]
    return {
        "keyword": keyword,
        "collected_at": started.isoformat(timespec="seconds"),
        "smart_blocks": data["smart_blocks"],
        "related_keywords": data["related_keywords"],
        "has_ad": data["has_ad"],
        "has_shopping": data["has_shopping"],
        "has_image": data["has_image"],
        "has_ai_briefing": data["has_ai_briefing"],
        "ai_briefing_sources": data["ai_briefing_sources"],
        "sections": data["sections"],
        "blog_feed": data.get("blog_feed"),
        **ai_blog_info(data["ai_briefing_sources"]),
        "url": data["url"],
        **({"ego_notice": notice} if notice else {}),
    }


def default_out_path(keyword: str, when: str) -> Path:
    stamp = datetime.fromisoformat(when).strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", keyword).strip("_")[:40] or "keyword"
    return OUT_DIR / f"{stamp}_{safe}.json"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="네이버 검색결과 구조(스마트블록 등) 수집")
    ap.add_argument("keyword", nargs="*", help="검색할 키워드 (띄어쓰기 그대로 써도 됨)")
    ap.add_argument("--out", help="저장할 JSON 파일 경로 (기본: serp_collect/날짜_키워드.json)")
    ap.add_argument("--no-save", action="store_true", help="파일로 저장하지 않고 화면에만 출력")
    args = ap.parse_args()

    keyword = " ".join(args.keyword).strip()
    if not keyword:
        try:
            keyword = input("검색할 키워드를 입력하세요: ").strip()
        except EOFError:
            keyword = ""
    if not keyword:
        print("키워드가 없습니다.", file=sys.stderr)
        return 2

    try:
        result = collect(keyword)
    except Exception as e:  # 사용자에게 보여줄 쉬운 오류 메시지
        print(f"[실패] {e}", file=sys.stderr)
        return 1

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if not args.no_save:
        path = Path(args.out) if args.out else default_out_path(keyword, result["collected_at"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(f"\n[저장됨] {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
