# -*- coding: utf-8 -*-
"""구글 경쟁 확인 실행기: ego lite(ego-browser)로 tools/lt_google_check.mjs 를 돌린다 (비로그인).
입력: 한 줄에 키워드 하나. 출력 JSON: [{keyword, verdict, top8:[{domain,title,kind}], ai_overview, paa, related, calc_intent}]
구글이 막으면 거기서 멈추고 지금까지 결과를 저장한 뒤 알린다."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MJS = os.path.join(ROOT, "tools", "lt_google_check.mjs")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    exe = shutil.which("ego-browser")
    if not exe:
        sys.exit("ego-browser 를 찾을 수 없습니다.")
    kws, seen = [], set()
    with open(a.inp, encoding="utf-8") as f:
        for line in f:
            k = line.strip()
            if k and not k.startswith("#") and k not in seen:
                seen.add(k)
                kws.append(k)
    if not kws:
        sys.exit("키워드가 없습니다.")
    out = os.path.abspath(a.out)
    job = {"keywords": kws, "out": out.replace("\\", "/")}
    tmp = tempfile.mkdtemp(prefix="lt_google_")
    wrapper = os.path.join(tmp, "run.mjs")
    with open(wrapper, "w", encoding="utf-8") as f:
        f.write("globalThis.__JOB = %s;\nawait import('file:///%s');\n" % (json.dumps(job, ensure_ascii=False), MJS.replace("\\", "/")))
    entry = "await import('file:///%s')" % wrapper.replace("\\", "/")
    r = subprocess.run([exe, "nodejs", "-e", entry], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    print(r.stdout[-1500:])
    if r.returncode != 0:
        print(r.stderr[-1500:])
    try:
        with open(out, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:  # noqa: BLE001
        print("결과 파일이 없습니다.")
        sys.exit(1)
    for x in rows:
        top = " / ".join(f"{t['domain']}({t['kind'][0]})" for t in x["top8"][:8])
        print(f"{x['verdict'] or '?'} {x['keyword']} · AI개요 {'있음' if x['ai_overview'] else '없음'} · 계산기의도 {'예' if x['calc_intent'] else '아니오'}"
              + (f" · 오류: {x['error']}" if x.get("error") else ""))
        print(f"    상위: {top}")
        if x["paa"]:
            print(f"    함께 질문: {' | '.join(x['paa'][:4])}")
        if x["related"]:
            print(f"    관련 검색어: {', '.join(x['related'][:8])}")
    if len(rows) < len(kws):
        print(f"※ {len(rows)}/{len(kws)}개에서 멈췄습니다(구글 봇 확인 또는 오류). 지금까지 결과만 저장했습니다.")
    print(f"저장: {out}")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
