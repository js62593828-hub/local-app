"""
비슷한 키워드·글 찾기 — 키워드와 글 제목이 이미 쓴 글과 겹치는지 미리 확인합니다.

화면(주간 승인·발행 현황·키워드 직접 추가)에서 쓰는 조회 전용 기능이라 큐/글 데이터는 건드리지 않습니다.
비교 항목은 dict 하나씩이고, 이 파일이 쓰는 키는 다음과 같습니다:
    id, keyword, cluster_seed, titles(글 제목 문자열 목록, 없으면 빈 목록)

판정 기준은 파일 맨 위 상수만 바꾸면 전체에 반영됩니다.
  🔴 "거의 같음"  : 키워드가 같음 / 한쪽이 다른 쪽을 포함하고 길이 차이가 작음 / 글자 2-gram 자카드가 높음
  🟡 "같은 주제"  : 같은 cluster_seed이면서, 시드를 뺀 수식어끼리 2-gram이 겹침
"""
import re

# ---------- 기준값 (여기만 고치면 됩니다) ----------
RED_CONTAIN_MAX_LEN_DIFF = 2   # 🔴 한쪽이 다른 쪽을 포함할 때, 정규화한 길이 차이가 이 값 이하이면 "거의 같음"
RED_JACCARD_MIN = 0.6          # 🔴 키워드끼리 / 제목끼리 글자 2-gram 자카드 유사도가 이 값 이상이면 "거의 같음"
YELLOW_MIN_SHARED_BIGRAMS = 1  # 🟡 같은 cluster_seed에서 시드를 뺀 수식어끼리 겹치는 2-gram이 이 개수 이상이면 "같은 주제"
MAX_SHOWN = 5                  # 배지에 마우스를 올렸을 때 보여줄 최대 개수

_STRIP_RE = re.compile(r"[\W_]+", re.UNICODE)  # 공백·특수문자(한글/영문/숫자 제외)


def normalize(text) -> str:
    return _STRIP_RE.sub("", (text or "")).lower()


def bigrams(norm: str) -> set:
    if len(norm) < 2:
        return {norm} if norm else set()
    return {norm[i:i + 2] for i in range(len(norm) - 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _red_reason(a: str, b: str, kind: str):
    """정규화된 두 문자열이 🔴 기준에 걸리면 (점수, 이유)를, 아니면 None을 돌려줍니다."""
    if not a or not b:
        return None
    if a == b:
        return 1.0, f"{kind}가 같음"
    if (a in b or b in a) and abs(len(a) - len(b)) <= RED_CONTAIN_MAX_LEN_DIFF:
        return 0.95, f"{kind} 포함 (길이 차이 {abs(len(a) - len(b))})"
    j = jaccard(bigrams(a), bigrams(b))
    if j >= RED_JACCARD_MIN:
        return round(j, 2), f"{kind} 2-gram 유사도 {j:.2f}"
    return None


def _remainder(norm_kw: str, norm_seed: str) -> str:
    """키워드에서 시드를 한 번 빼고 남은 수식어. 시드가 안 들어 있으면 키워드 전체를 씁니다."""
    if norm_seed and norm_seed in norm_kw:
        return norm_kw.replace(norm_seed, "", 1)
    return norm_kw


def _features(item: dict) -> dict:
    kw = normalize(item.get("keyword"))
    seed = normalize(item.get("cluster_seed") or item.get("keyword"))
    return {
        "kw": kw,
        "seed": seed,
        "rest": _remainder(kw, seed),
        "titles": [t for t in (normalize(t) for t in (item.get("titles") or [])) if t],
    }


def compare(a: dict, b: dict, fa: dict, fb: dict):
    """두 항목을 비교해 {"level": "red"|"yellow", "score", "reason"}를 돌려줍니다. 안 겹치면 None."""
    best = None
    r = _red_reason(fa["kw"], fb["kw"], "키워드")
    if r:
        best = r
    for ta in fa["titles"]:
        for tb in fb["titles"]:
            r = _red_reason(ta, tb, "제목")
            if r and (best is None or r[0] > best[0]):
                best = r
    if best:
        return {"level": "red", "score": best[0], "reason": best[1]}

    if fa["seed"] and fa["seed"] == fb["seed"]:
        shared = bigrams(fa["rest"]) & bigrams(fb["rest"])
        if len(shared) >= YELLOW_MIN_SHARED_BIGRAMS:
            score = round(jaccard(bigrams(fa["rest"]), bigrams(fb["rest"])), 2)
            return {"level": "yellow", "score": score, "reason": "같은 주제 (겹치는 수식어: " + ", ".join(sorted(shared)) + ")"}
    return None


def find_similar(targets: list, universe: list) -> dict:
    """
    targets 각 항목에 대해 universe(비교 대상 전체) 중 겹치는 항목을 찾습니다. 자기 자신(id 같음)은 제외.
    반환: {target_id: {"red": [매치...], "yellow": [매치...]}} — 매치는 {"id", "level", "score", "reason"}이고
    점수 높은 순으로 정렬돼 있습니다. 겹치는 게 없는 항목도 빈 목록으로 들어 있습니다.
    """
    feats = {it["id"]: _features(it) for it in universe}
    for it in targets:
        feats.setdefault(it["id"], _features(it))
    out = {}
    for t in targets:
        res = {"red": [], "yellow": []}
        ft = feats[t["id"]]
        for u in universe:
            if u["id"] == t["id"]:
                continue
            m = compare(t, u, ft, feats[u["id"]])
            if m:
                m["id"] = u["id"]
                res[m["level"]].append(m)
        for k in res:
            res[k].sort(key=lambda m: -m["score"])
        out[t["id"]] = res
    return out


def top_matches(result: dict, limit: int = MAX_SHOWN) -> list:
    """🔴 먼저, 그다음 🟡 순으로 최대 limit개."""
    return (result["red"] + result["yellow"])[:limit]
