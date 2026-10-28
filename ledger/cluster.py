"""Deduplication and event clustering.

Items about the same development are grouped so the newspaper writes one
story backed by several reports. Independence is computed separately
(evidence.py): repetition is never mistaken for corroboration."""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache

from . import config, textutil, timeutil

_CAP_RE = re.compile(r"(?<![\w])([A-ZÀ-ÖØ-Ý][\w'’\-]{1,}|[A-Z]{2,}[0-9]*)")
_NOT_ANCHOR = set("""
the a an il lo la le gli un una i in on at for of and to by from with as is are was this that it he she they we
new news live update updates video photo foto watch exclusive breaking analysis opinion review interview
ecco come perche dopo oggi ieri domani ma anche cosi tutti tutto ultima ora mr mrs ms dr sir lord lady
les des du de et ce cette dans pour avec selon apres
der die das und mit von fur nach
""".split())


@lru_cache(maxsize=1)
def alias_map() -> dict[str, str]:
    m = {}
    for grp in config.lexicon()["aliases"]:
        canon = textutil.fold(grp[0])
        for a in grp:
            m[textutil.fold(a)] = canon
    return m


_CALENDAR_WORDS = set(textutil.MONTH_WORDS) | set(textutil.WEEKDAY_WORDS)


_ELISION = re.compile(r"^([A-Za-z]{1,5})['’](.*)$")


def anchors(text: str, include_initial: bool = False, headline_subjects: bool = False) -> set[str]:
    """Proper-noun-like tokens (names, institutions, places, acronyms), folded
    and alias-normalised so 'BCE' and 'ECB' match across languages.
    Sentence-initial capitals and Italian/French elisions (L'inflazione,
    dell'Istat) are handled so ordinary words are not mistaken for names."""
    out = set()
    am = alias_map()
    for sent in re.split(r"(?<=[.!?:;])\s+|\n", text or ""):
        first = re.search(r"\w", sent)
        first_pos = first.start() if first else -1
        for m in _CAP_RE.finditer(sent):
            tok = m.group(1)
            initial = m.start() <= first_pos + 1 and not sent[first_pos:m.start()].strip("\"'“‘«( ")
            el = _ELISION.match(tok)
            if el:
                tok = el.group(2)
                if not tok or not tok[0].isupper():
                    continue  # L'inflazione, D'altra → ordinary words
                initial = False
            f = re.sub(r"'s$", "", textutil.fold(tok).strip("'’-"))
            if len(f) < 3 and not tok.isupper():
                continue
            if f in _NOT_ANCHOR or f in textutil.STOPWORDS or f in _CALENDAR_WORDS:
                continue
            headline_subject = headline_subjects and sent[m.end():m.end() + 1] in (":", ",")  # "Istat: …", "Meloni, …"
            if initial and not include_initial and not headline_subject and not tok.isupper() and f not in am:
                # sentence-initial capital is weak evidence of a name; keep only if known alias
                continue
            out.add(am.get(f, f))
    return out


def key_numbers(text: str) -> set[float]:
    vals = set()
    for n in textutil.extract_numbers(text):
        if n.get("word"):
            continue
        for v in n["values"]:
            if v >= 10 or n["kind"]:
                vals.add(v)
    return vals


class Features:
    __slots__ = ("idx", "lang", "tokens", "anchors", "numbers", "ts", "owner")

    def __init__(self, idx: int, it: dict):
        txt_title = it["title"]
        txt_sum = (it.get("summary") or "")[:400]
        self.idx = idx
        self.lang = it["lang"]
        self.tokens = set(textutil.content_tokens(txt_title + " " + txt_sum)[:60])
        self.anchors = anchors(txt_title + ". " + txt_sum, headline_subjects=True)
        self.numbers = key_numbers(txt_title + " " + txt_sum)
        p = timeutil.parse_iso(it.get("published"))
        self.ts = p.timestamp() if p else None
        self.owner = it.get("agency") or it["owner"]


_SPECIFIC_IDF = [6.0]  # set per run in cluster_items: anchors rarer than ~1.5% of items


def _anchor_overlap(a: Features, b: Features, aidf: dict[str, float]) -> tuple[float, int]:
    shared = a.anchors & b.anchors
    if not shared or not any(aidf.get(x, 99) >= _SPECIFIC_IDF[0] for x in shared):
        return 0.0, 0  # sharing only very common names (Trump, Italy, Wall Street) proves nothing
    w_shared = sum(aidf.get(x, 1.0) for x in shared)
    w_min = min(sum(aidf.get(x, 1.0) for x in a.anchors), sum(aidf.get(x, 1.0) for x in b.anchors))
    return (w_shared / w_min if w_min else 0.0), len(shared)


def similarity(a: Features, b: Features, idf: dict[str, float], aidf: dict[str, float] | None = None) -> float:
    if a.ts and b.ts and abs(a.ts - b.ts) > 40 * 3600:
        return 0.0
    aidf = aidf or {}
    anc, n_shared = _anchor_overlap(a, b, aidf)
    num_j = textutil.jaccard(a.numbers, b.numbers) if a.numbers and b.numbers else 0.0
    if a.lang == b.lang:
        inter = a.tokens & b.tokens
        if a.owner == b.owner and a.lang == b.lang:
            anc = anc * 0.5  # one outlet's different articles share names constantly
        w_inter = sum(idf.get(t, 1.0) for t in inter)
        w_union = sum(idf.get(t, 1.0) for t in a.tokens | b.tokens)
        tok = w_inter / w_union if w_union else 0.0
        if n_shared >= 2 and tok >= 0.10:  # names alone never merge two reports: wording must overlap too
            return max(tok, 0.45 * tok + 0.45 * anc + 0.10 * num_j)
        return tok
    # cross-language: rely on names/institutions and figures; demand more of them
    if n_shared < 3 and not (n_shared >= 2 and num_j > 0):
        return 0.0
    return 0.8 * anc + 0.2 * num_j


def cluster_items(items: list[dict], same_lang_threshold: float = 0.30, cross_lang_threshold: float = 0.42) -> list[list[int]]:
    """Greedy, chaining-resistant clustering. Returns lists of item indices."""
    feats = [Features(i, it) for i, it in enumerate(items)]
    df = Counter(t for f in feats for t in f.tokens)
    n = max(1, len(feats))
    idf = {t: math.log(n / (1 + c)) + 1.0 for t, c in df.items()}
    adf = Counter(a for f in feats for a in f.anchors)
    aidf = {a: math.log(n / (1 + c)) + 1.0 for a, c in adf.items()}
    _SPECIFIC_IDF[0] = math.log(n / (1 + max(5, 0.006 * n))) + 1.0
    # inverted index: anchors and reasonably rare tokens
    index: dict[str, list[int]] = defaultdict(list)
    for f in feats:
        for a in f.anchors:
            index["a:" + a].append(f.idx)
        for t in f.tokens:
            if df[t] <= 40:
                index["t:" + t].append(f.idx)
    order = sorted(range(len(items)), key=lambda i: (items[i].get("_tier", 3), -(feats[i].ts or 0)))
    cluster_of: dict[int, int] = {}
    clusters: list[list[int]] = []
    for i in order:
        f = feats[i]
        cand_clusters: Counter = Counter()
        for key in [*("a:" + a for a in f.anchors), *("t:" + t for t in f.tokens if df[t] <= 40)]:
            for j in index.get(key, ()):
                if j in cluster_of:
                    cand_clusters[cluster_of[j]] += 1
        best, best_sim = None, 0.0
        for cid, _ in cand_clusters.most_common(12):
            members = clusters[cid]
            sims = [similarity(f, feats[j], idf, aidf) for j in members[:6]]
            s = max(sims) if sims else 0.0
            thr = same_lang_threshold if any(feats[j].lang == f.lang for j in members[:6]) else cross_lang_threshold
            # guard against chaining: must also be close to the cluster's first item
            if s >= thr and similarity(f, feats[members[0]], idf, aidf) >= thr * 0.75 and s > best_sim:
                best, best_sim = cid, s
        if best is None:
            cluster_of[i] = len(clusters)
            clusters.append([i])
        else:
            cluster_of[i] = best
            clusters[best].append(i)
    return _refine(clusters, feats, aidf)


def _core_anchors(members: list[int], feats: list[Features], aidf: dict[str, float]) -> set[str]:
    cnt = Counter(a for j in members for a in feats[j].anchors if aidf.get(a, 99) >= _SPECIFIC_IDF[0])
    need = max(2, 0.3 * len(members))
    return {a for a, c in cnt.items() if c >= need}


def _refine(clusters: list[list[int]], feats: list[Features], aidf: dict[str, float]) -> list[list[int]]:
    """1) eject members that share none of the cluster's core names;
    2) merge clusters describing the same event with the same core names."""
    out: list[list[int]] = []
    for members in clusters:
        if len(members) < 4:
            out.append(members)
            continue
        core = _core_anchors(members, feats, aidf)
        if not core:
            out.append(members)
            continue
        keep = [j for j in members if feats[j].anchors & core]
        ejected = [j for j in members if j not in keep]
        out.append(keep or members)
        out.extend([[j] for j in ejected] if keep else [])
    cores = [(_core_anchors(m, feats, aidf) if len(m) >= 3 else set()) for m in out]
    merged = [False] * len(out)
    for i in range(len(out)):
        if merged[i] or len(cores[i]) < 2:
            continue
        for j in range(i + 1, len(out)):
            if merged[j] or len(cores[j]) < 2:
                continue
            inter = cores[i] & cores[j]
            if len(inter) >= 2 and len(inter) / min(len(cores[i]), len(cores[j])) >= 0.67:
                out[i].extend(out[j])
                merged[j] = True
    return [m for k, m in enumerate(out) if not merged[k]]


def cluster_id(item_ids: list[str]) -> str:
    return "c" + hashlib.sha1("|".join(sorted(item_ids)).encode()).hexdigest()[:10]
