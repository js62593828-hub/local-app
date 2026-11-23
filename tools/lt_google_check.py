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
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import lt_bank as B  # noqa: E402
import lt_data as D  # noqa: E402
MJS = os.path.join(ROOT, "tools", "lt_google_check.mjs")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-bank", action="store_true", help="키워드 은행을 안 보고 전부 새로 확인한다")
    ap.add_argument("--week", default="", help="은행에 적을 주차(없으면 --out 경로의 혜택_날짜에서 읽음)")
    a = ap.parse_args()
    kws, seen = [], set()
    with open(a.inp, encoding="utf-8") as f:
        for line in f:
            k = line.strip()
            if k and not k.startswith("#") and k not in seen:
                seen.add(k)
                kws.append(k)
    if not kws:
        sys.exit("키워드가 없습니다.")
    # 8차: 30일 안에 구글을 확인한 키워드는 은행 값을 쓰고 다시 확인하지 않는다
    out = os.path.abspath(a.out)
    today_s = date.today().isoformat()
    bank = B.load()
    cached, todo = {}, []
    for k in kws:
        e = bank["keywords"].get(D.key(k)) if not a.no_bank else None
        g = (e or {}).get("google")
        if g and g.get("row") and B.is_fresh(g.get("checked")):
            cached[k] = dict(g["row"], keyword=k, bank_date=g.get("checked"))
        else:
            todo.append(k)
    print(f"키워드 {len(kws)}개 · 은행 값 사용 {len(cached)}개(30일 안에 확인) · 새로 확인 {len(todo)}개")
    new_rows, rc = [], 0
    if todo:
        exe = shutil.which("ego-browser")
        if not exe:
            sys.exit("ego-browser 를 찾을 수 없습니다.")
        part = out + ".part"
        job = {"keywords": todo, "out": part.replace("\\", "/")}
        tmp = tempfile.mkdtemp(prefix="lt_google_")
        wrapper = os.path.join(tmp, "run.mjs")
        with open(wrapper, "w", encoding="utf-8") as f:
            f.write("globalThis.__JOB = %s;\nawait import('file:///%s');\n" % (json.dumps(job, ensure_ascii=False), MJS.replace("\\", "/")))
        entry = "await import('file:///%s')" % wrapper.replace("\\", "/")
        r = subprocess.run([exe, "nodejs", "-e", entry], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        rc = r.returncode
        print(r.stdout[-1500:])
        if rc != 0:
            print(r.stderr[-1500:])
        try:
            with open(part, encoding="utf-8") as f:
                new_rows = json.load(f)
        except Exception:  # noqa: BLE001
            new_rows = []
            print("새로 확인한 결과 파일이 없습니다.")
        if new_rows and not a.no_bank:
            B.record_google(bank, new_rows, today_s, a.week or B.week_of_path(out))
            B.save(bank)
        try:
            os.remove(part)
        except OSError:
            pass
    fresh = {x["keyword"]: x for x in new_rows}
    rows = [cached.get(k) or fresh[k] for k in kws if (k in cached or k in fresh)]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    if not rows:
        print("결과가 없습니다.")
        sys.exit(1)
    for x in rows:
        top = " / ".join(f"{t['domain']}({t['kind'][0]})" for t in x["top8"][:8])
        print(f"{x['verdict'] or '?'} {x['keyword']} · AI개요 {'있음' if x['ai_overview'] else '없음'} · 계산기의도 {'예' if x['calc_intent'] else '아니오'}"
              + (f" · 오류: {x['error']}" if x.get("error") else "") + (f" · [은행 {x['bank_date']}]" if x.get("bank_date") else ""))
        print(f"    상위: {top}")
        if x["paa"]:
            print(f"    함께 질문: {' | '.join(x['paa'][:4])}")
        if x["related"]:
            print(f"    관련 검색어: {', '.join(x['related'][:8])}")
    if len(rows) < len(kws):
        print(f"※ {len(rows)}/{len(kws)}개에서 멈췄습니다(구글 봇 확인 또는 오류). 지금까지 결과만 저장했습니다.")
    print(f"저장: {out}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
