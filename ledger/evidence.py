"""Evidence records, independence (triangulation) and conflict detection."""
from __future__ import annotations

import re
from collections import defaultdict

from . import config, textutil

ACCESS_RANK = {"official_document": 4, "full_text": 3, "headline_snippet": 2, "headline_only": 1}


def origin_groups(items: list[dict]) -> list[list[dict]]:
    """Group items that are NOT independent of each other:
    - same ownership group (Repubblica + La Stampa, VeneziaToday + MilanoToday…)
    - same wire agency credited in the text ("(ANSA)", "Reuters"…)
    - near-identical wording (syndicated copy, even if uncredited)."""
    def key(it):
        return it.get("agency") or it["owner"]

    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        groups[key(it)].append(it)
    keys = list(groups)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    toks = {id(it): set(textutil.content_tokens(it["title"] + " " + it.get("summary", ""))) for it in items}
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            for a in groups[ka]:
                for b in groups[kb]:
                    ta, tb = toks[id(a)], toks[id(b)]
                    same_title = textutil.squash(a["title"]) == textutil.squash(b["title"])
                    copy = len(ta) >= 12 and len(tb) >= 12 and textutil.jaccard(ta, tb) >= 0.7
                    if same_title or copy:
                        parent[find(kb)] = find(ka)
    merged: dict[str, list[dict]] = defaultdict(list)
    for k in keys:
        merged[find(k)].extend(groups[k])
    return list(merged.values())


def verification_status(items: list[dict]) -> tuple[str, int, list[str]]:
    srcs = config.sources()
    groups = origin_groups(items)
    n_indep = len(groups)
    has_official = any(srcs[it["source_id"]].official for it in items if it["source_id"] in srcs)
    labels = sorted({(g[0].get("agency") or g[0]["owner"]) for g in groups})
    if has_official and n_indep >= 2:
        status = "official_corroborated"
    elif has_official:
        status = "official"
    elif n_indep >= 2:
        status = "corroborated"
    else:
        status = "single_source"
    return status, n_indep, labels


# ───────────────────────────────────────────── conflicts

_COUNT_NOUNS = {
    "dead": "dead", "deaths": "dead", "killed": "dead", "died": "dead", "morti": "dead", "morto": "dead", "vittime": "dead",
    "victims": "dead", "morts": "dead", "tote": "dead",
    "injured": "injured", "wounded": "injured", "feriti": "injured", "blesses": "injured", "verletzte": "injured",
    "people": "people", "persone": "people", "personnes": "people", "menschen": "people",
    "jobs": "jobs", "posti": "jobs", "emplois": "jobs", "workers": "workers", "lavoratori": "workers",
    "employees": "employees", "dipendenti": "employees", "salaries": "employees",
    "migrants": "migrants", "migranti": "migrants", "arrests": "arrests", "arresti": "arrests", "arrestati": "arrests",
}


def _counted(text: str) -> dict[str, set[float]]:
    out: dict[str, set[float]] = defaultdict(set)
    f = textutil.fold(text)
    for m in re.finditer(r"(\d[\d.,]*)\s+(?:\w+\s+)?(" + "|".join(_COUNT_NOUNS) + r")\b", f):
        for v in textutil._interpretations(m.group(1).rstrip(".,"), None):
            out[_COUNT_NOUNS[m.group(2)]].add(v)
    for m in re.finditer(r"(\d[\d.,]*)\s?(%|per cent|percent|per cento)", f):
        pass  # percentages are too context-dependent to compare automatically
    return out


def detect_conflicts(items: list[dict]) -> list[dict]:
    """Different independent sources reporting different counts for the same
    countable thing (deaths, injured, jobs…). Returned conflicts are passed to
    the writer, which must report both figures with attribution, and are
    enforced by the publication gate."""
    srcs = config.sources()
    per: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for it in items:
        for noun, vals in _counted(it["title"] + ". " + it.get("summary", "") + " " + it.get("text", "")[:3000]).items():
            if len(vals) == 1:
                per[noun].append((next(iter(vals)), it))
    conflicts = []
    for noun, obs in per.items():
        values = {v for v, _ in obs}
        if len(values) < 2:
            continue
        by_val: dict[float, list[dict]] = defaultdict(list)
        for v, it in obs:
            by_val[v].append(it)
        origins = {v: {(i.get("agency") or i["owner"]) for i in its} for v, its in by_val.items()}
        # only a conflict if the differing values come from different origins
        all_origins = set().union(*origins.values())
        if len(all_origins) < 2:
            continue
        official_vals = sorted({v for v, its in by_val.items() if any(srcs.get(i["source_id"]) and srcs[i["source_id"]].official for i in its)})
        conflicts.append({
            "measure": noun,
            "values": [{"value": v, "sources": sorted({i["source_name"] for i in its}),
                        "official": v in official_vals} for v, its in sorted(by_val.items())],
            "official_values": official_vals,
        })
    return conflicts


# ───────────────────────────────────────────── evidence pack


def passage(it: dict, max_chars: int = 2400) -> str:
    body = it.get("summary", "")
    if it.get("text"):
        body = (body + " " + it["text"]).strip()
    return (it["title"] + ". " + body)[:max_chars].strip()


def build_evidence(items: list[dict], max_items: int = 8) -> list[dict]:
    """Choose the evidence set for a story: primary/official documents first,
    then one representative per independent origin, best access level first."""
    srcs = config.sources()
    groups = origin_groups(items)

    def rank(it):
        s = srcs.get(it["source_id"])
        return (0 if s and s.official else 1, -ACCESS_RANK.get(it["access_level"], 0),
                s.tier if s else 9, -(len(it.get("summary", "")) + len(it.get("text", ""))))

    reps = [sorted(g, key=rank)[0] for g in groups]
    reps.sort(key=rank)
    chosen = reps[:max_items]
    # fill remaining slots with extra items (same origin) only if they add text
    if len(chosen) < max_items:
        rest = sorted((it for it in items if it not in chosen), key=rank)
        chosen += rest[: max_items - len(chosen)]
    ev = []
    for n, it in enumerate(chosen, 1):
        s = srcs.get(it["source_id"])
        ev.append({
            "eid": f"e{n}", "item_id": it["id"], "source_id": it["source_id"], "source_name": it["source_name"],
            "source_type": s.type if s else "unknown", "official": bool(s and s.official),
            "origin": it.get("agency") or it["owner"], "title": it["title"], "url": it["url"],
            "published": it.get("published"), "date_quality": it.get("date_quality"),
            "retrieved_at": it["retrieved_at"], "access_level": it["access_level"], "lang": it["lang"],
            "passage": passage(it), "paywall": bool(s and s.paywall), "url_check": None,
        })
    return ev


def access_summary(evidence: list[dict]) -> str:
    best = max((ACCESS_RANK.get(e["access_level"], 0) for e in evidence), default=0)
    return {4: "official_document", 3: "full_text", 2: "headline_snippet", 1: "headline_only"}.get(best, "none")
