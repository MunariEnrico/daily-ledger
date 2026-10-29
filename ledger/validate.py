"""THE PUBLICATION GATE — deterministic, code-level verification.

A draft (from the model, or hand-written) is published only if every checkable
atom can be traced to the evidence pack:

  claims      each claim cites real evidence ids and quotes a verbatim span that
              actually occurs in that evidence
  numbers     every figure in a paragraph occurs in the evidence its claims cite
  dates       every calendar date / weekday occurs in that evidence (or is the
              source's own publication date)
  quotations  every quoted passage occurs verbatim in the cited evidence
  names       every proper name occurs in the cited evidence (alias-aware)
  causation   causal wording only where the evidence itself states a cause
  legal       no upgrade of a proposal into an approved/in-force measure
  crime       no guilt wording without a conviction in evidence; hedging when
              the evidence describes arrests/charges/investigations
  attribution single-source stories must attribute
  access      snippet-only evidence ⇒ short story
  conflicts   conflicting figures are reported together (official figure kept)
  URLs        the model may not write links (sources are attached from evidence)
  parity      Italian and English carry identical numbers, dates, claims, names

This cannot make hallucination impossible (a sentence can still mis-state a
relationship between supported atoms); it makes unsupported specifics — the
most damaging kind — fail closed. Results are recorded in the audit trail."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from . import cluster, config, textutil, timeutil

STORY_SECTIONS = ["body", "why_it_matters", "what_next", "key_facts"]
ANALYSIS_SECTIONS = ["current_situation", "evidence", "interpretations", "implications", "uncertainties", "watch"]
HEDGED_SECTIONS = {"why_it_matters", "implications"}

URL_RE = re.compile(r"(https?://|www\.)\S+", re.I)

# Words seen in lower case in the day's news corpus are ordinary words, not
# names, even when capitalised (title-case headlines, "il Consiglio").
COMMON_WORDS: set[str] = set()


def _base_common_words() -> set[str]:
    try:
        with open(config.CONFIG_DIR / "common_words.txt", encoding="utf-8") as fh:
            return {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
    except FileNotFoundError:
        return set()


def set_common_words(words) -> None:
    COMMON_WORDS.clear()
    COMMON_WORDS.update(_base_common_words())
    COMMON_WORDS.update(words)


def common_words_from(texts) -> set[str]:
    cnt: Counter = Counter()
    for t in texts:
        for m in re.finditer(r"(?<![.!?:]\s)(?<!^)\b([a-zà-ÿ][a-zà-ÿ'’-]{2,})\b", t or ""):
            cnt[textutil.fold(m.group(1))] += 1
    return {w for w, c in cnt.items() if c >= 2}


def names_in(text: str) -> set[str]:
    return {a for a in cluster.anchors(text) if a not in COMMON_WORDS}


class Report:
    def __init__(self):
        self.errors: list[dict] = []
        self.warnings: list[dict] = []
        self.metrics: Counter = Counter()

    def err(self, code: str, lang: str, where: str, msg: str):
        self.errors.append({"code": code, "lang": lang, "where": where, "msg": msg})
        self.metrics[code] += 1

    def warn(self, code: str, lang: str, where: str, msg: str):
        self.warnings.append({"code": code, "lang": lang, "where": where, "msg": msg})

    @property
    def passed(self) -> bool:
        return not self.errors

    def langs_failing(self) -> set[str]:
        return {e["lang"] for e in self.errors}

    def to_dict(self) -> dict:
        return {"passed": self.passed, "errors": self.errors, "warnings": self.warnings, "metrics": dict(self.metrics)}

    def summary(self, limit: int = 25) -> str:
        return "\n".join(f"- [{e['lang']}] {e['where']}: {e['msg']}" for e in self.errors[:limit])


# ───────────────────────────────────────────── evidence views


class EvidenceView:
    def __init__(self, evidence: list[dict]):
        self.by_id = {e["eid"]: e for e in evidence}
        self._cache: dict[tuple, dict] = {}
        self.am = cluster.alias_map()
        self.alias_groups = config.lexicon()["aliases"]

    def view(self, eids) -> dict:
        key = tuple(sorted(set(eids)))
        if key in self._cache:
            return self._cache[key]
        evs = [self.by_id[i] for i in key if i in self.by_id]
        text = " \n ".join(e["passage"] for e in evs)
        folded = textutil.fold(text)
        nums = textutil.extract_numbers(text)
        dates = textutil.extract_dates(text)
        weekdays = textutil.extract_weekdays(text)
        tz = timeutil.tz()
        for e in evs:
            p = timeutil.parse_iso(e.get("published"))
            if p:
                loc = p.astimezone(tz)
                dates.add((loc.day, loc.month))
                weekdays.add(loc.weekday())
                for v in (loc.day, loc.month, loc.year):
                    nums.append({"raw": str(v), "values": {float(v)}, "kind": ""})
        words = set(textutil.words(text))
        for e in evs:  # source names may be named in attribution
            words |= set(textutil.words(e["source_name"]))
        allowed = set(words) | {self.am.get(w, w) for w in words}
        for grp in self.alias_groups:
            if any(re.search(r"\b" + re.escape(textutil.fold(a)) + r"\b", folded) for a in grp):
                for a in grp:
                    allowed |= set(textutil.words(a))
                    allowed.add(self.am.get(textutil.fold(a), textutil.fold(a)))
        v = {
            "evs": evs, "text": text, "folded": folded, "squashed": textutil.squash(text),
            "squashed_each": {e["eid"]: textutil.squash(e["passage"]) for e in evs},
            "numbers": nums, "dates": dates, "weekdays": weekdays, "names": allowed,
            "causal": bool(textutil.markers_in(folded, textutil.CAUSAL)),
            "guilt": set(textutil.markers_in(folded, textutil.GUILT)),
            "accusation": bool(textutil.markers_in(folded, textutil.ACCUSATION)),
            "legal": textutil.legal_statuses(text),
        }
        self._cache[key] = v
        return v


# ───────────────────────────────────────────── atom checks


def _check_atoms(text: str, lang: str, where: str, ev: dict, rep: Report, strict_names: bool, section: str = ""):
    if not text:
        return
    if URL_RE.search(text):
        rep.err("url_in_text", lang, where, "links may not be written into the text")
    for n in textutil.extract_numbers(text, lang):
        if not textutil.number_supported(n, ev["numbers"]):
            rep.err("unsupported_number", lang, where, f"figure '{n['raw']}' not found in the cited evidence")
    for d in textutil.extract_dates(text) - ev["dates"]:
        rep.err("unsupported_date", lang, where, f"date {d[0]}/{d[1]} not found in the cited evidence")
    for wd in textutil.extract_weekdays(text) - ev["weekdays"]:
        rep.err("unsupported_date", lang, where, f"weekday #{wd} not supported by the cited evidence")
    for q in textutil.extract_quotes(text):
        if not textutil.span_in(q, ev["squashed"]):
            rep.err("unsupported_quote", lang, where, f"quotation not verbatim in evidence: “{q[:60]}”")
    f = textutil.fold(text)
    causal = textutil.markers_in(f, textutil.CAUSAL)
    if causal and not ev["causal"]:
        rep.err("unsupported_causation", lang, where,
                f"causal wording ({', '.join(causal[:3])}) but the evidence states no cause")
    guilt = set(textutil.markers_in(f, textutil.GUILT))
    if guilt - ev["guilt"]:
        rep.err("presumption_of_innocence", lang, where,
                f"guilt wording ({', '.join(sorted(guilt - ev['guilt']))}) not supported by evidence of a conviction")
    asserted = textutil.legal_statuses(text) & {"approved", "in_force", "published", "judgment"}
    if asserted - ev["legal"]:
        rep.err("legal_status_upgrade", lang, where,
                f"asserts {', '.join(sorted(asserted - ev['legal']))} but evidence does not")
    if section in HEDGED_SECTIONS and not textutil.markers_in(f, textutil.FORECAST_HEDGE) and textutil.word_count(text) > 12:
        rep.warn("analysis_unhedged", lang, where, "analysis paragraph without conditional wording")
    for a in names_in(text):
        if a not in ev["names"] and a not in textutil.STOPWORDS:
            (rep.err if strict_names else rep.warn)("unsupported_name", lang, where,
                                                      f"name '{a}' does not occur in the cited evidence")


def _paragraphs(obj: dict, sections: list[str]):
    for sec in sections:
        val = obj.get(sec) or []
        if not isinstance(val, list):
            yield sec, 0, {"text": str(val), "claims": []}
            continue
        for i, p in enumerate(val):
            if isinstance(p, str):
                p = {"text": p, "claims": []}
            yield sec, i, p


# ───────────────────────────────────────────── main gate


def validate(draft: dict, evidence: list[dict], meta: dict, kind: str = "story") -> Report:
    st = config.settings()
    strict_names = st["verification"].get("strict_names", True)
    rep = Report()
    EV = EvidenceView(evidence)
    sections = STORY_SECTIONS if kind == "story" else ANALYSIS_SECTIONS
    all_eids = list(EV.by_id)
    # 1 ── claims
    claims = {c.get("id"): c for c in (draft.get("claims") or []) if isinstance(c, dict) and c.get("id")}
    if not claims:
        rep.err("no_claims", "both", "claims", "draft contains no evidence-backed claims")
    good: dict[str, dict] = {}
    for cid, c in claims.items():
        ids = [i for i in (c.get("source_ids") or []) if i in EV.by_id]
        if not ids:
            rep.err("claim_without_source", "both", cid, "claim cites no valid evidence id")
            continue
        support = (c.get("support") or "").strip()
        ev = EV.view(ids)
        if textutil.word_count(support) < 4 or not any(textutil.span_in(support, ev["squashed_each"][i]) for i in ids):
            rep.err("unsupported_claim", "both", cid, f"support excerpt not found verbatim in {', '.join(ids)}")
            continue
        n0 = len(rep.errors)
        for lang in ("en", "it"):
            _check_atoms(c.get(lang, ""), lang, f"claim {cid}", ev, rep, strict_names)
        if len(rep.errors) == n0:
            good[cid] = {**c, "source_ids": ids}
    union_eids = sorted({i for c in good.values() for i in c["source_ids"]}) or all_eids
    # 2 ── per language
    st_ed = st["editorial"]
    for lang in ("en", "it"):
        obj = draft.get(lang)
        if not isinstance(obj, dict):
            rep.err("missing_language", lang, lang, "language version missing")
            continue
        if not (obj.get("headline") or "").strip():
            rep.err("missing_field", lang, "headline", "headline missing")
        ev_all = EV.view(union_eids)
        # headline words may also come from this version's own body (title-case
        # headlines capitalise ordinary words; the body itself is checked strictly)
        body_words = set(textutil.words(" ".join(p.get("text", "") for _, _, p in _paragraphs(obj, sections))))
        ev_head = dict(ev_all, names=ev_all["names"] | body_words)
        for fld in ("headline", "subheadline"):
            _check_atoms(obj.get(fld, ""), lang, fld, ev_head, rep, strict_names)
        lead = sections[0]
        if not obj.get(lead):
            rep.err("missing_field", lang, lead, f"{lead} missing")
        for sec, i, p in _paragraphs(obj, sections):
            where = f"{sec}[{i}]"
            refs = [r for r in (p.get("claims") or []) if isinstance(r, str)]
            if not refs:
                rep.err("paragraph_without_claims", lang, where, "paragraph cites no claim")
                continue
            bad = [r for r in refs if r not in good]
            if bad:
                rep.err("paragraph_cites_unsupported_claim", lang, where, f"cites unsupported/unknown claims {bad}")
                continue
            eids = sorted({e for r in refs for e in good[r]["source_ids"]})
            _check_atoms(p.get("text", ""), lang, where, EV.view(eids), rep, strict_names, section=sec)
        body_text = " ".join(p.get("text", "") for _, _, p in _paragraphs(obj, [lead]))
        bf = textutil.fold(body_text)
        # attribution
        if meta.get("verification") == "single_source":
            names = {textutil.fold(e["source_name"]).split(" ·")[0] for e in evidence}
            if not (textutil.markers_in(bf, textutil.ATTRIBUTION) or any(n and n in bf for n in names)):
                rep.err("attribution_missing", lang, lead, "single-source story must attribute its claims")
        # access level → length
        if meta.get("access") in ("headline_snippet", "headline_only"):
            cap = int(st_ed["snippet_only_max_words"] * 1.15)
            if textutil.word_count(body_text) > cap:
                rep.err("too_long_for_access", lang, lead,
                        f"{textutil.word_count(body_text)} words written from headlines/snippets only (max {cap})")
        # crime hedging
        evall = EV.view(all_eids)
        if ("crime" in (meta.get("topics") or []) or meta.get("primary_topic") == "crime") and evall["accusation"] \
                and not evall["guilt"] and not textutil.markers_in(bf, textutil.HEDGE):
            rep.err("presumption_of_innocence", lang, lead,
                    "evidence describes arrests/charges/investigation; text must make clear guilt is not established")
        # conflicts
        all_text = " ".join([obj.get("headline", ""), obj.get("subheadline", "")] +
                            [p.get("text", "") for _, _, p in _paragraphs(obj, sections)])
        vals = {v for n in textutil.extract_numbers(all_text, lang) for v in n["values"]}
        for cf_ in meta.get("conflicts") or []:
            all_vals = [x["value"] for x in cf_["values"]]
            present = [v for v in all_vals if v in vals]
            if not present:
                continue
            if cf_.get("official_values"):
                if not all(v in vals for v in cf_["official_values"]):
                    rep.err("conflict_official_missing", lang, "conflict",
                            f"{cf_['measure']}: uses a disputed figure but omits the official one {cf_['official_values']}")
            elif len(present) < len(all_vals):
                rep.err("conflict_one_sided", lang, "conflict",
                        f"{cf_['measure']}: sources report {all_vals}; text gives only {present}")
    # legal status field
    ls = (draft.get("legal_status") or "none").lower()
    if ls in ("approved", "in_force", "published", "judgment") and ls not in EV.view(all_eids)["legal"]:
        # the label is ours to set: downgrade it instead of rejecting the story
        # (assertions in the TEXT are still blocked above)
        rep.warn("legal_status_downgraded", "both", "legal_status", f"legal_status '{ls}' not supported; shown as unclear")
        draft["legal_status"] = "unknown"
    # 3 ── bilingual parity
    if isinstance(draft.get("en"), dict) and isinstance(draft.get("it"), dict):
        parity(draft, sections, rep, strict_names)
    rep.supported_claims = good  # type: ignore[attr-defined]
    return rep


def _lang_text(obj: dict, sections: list[str]) -> str:
    return " ".join([obj.get("headline", ""), obj.get("subheadline", "")] +
                    [p.get("text", "") for _, _, p in _paragraphs(obj, sections)])


def _claim_refs(obj: dict, sections: list[str]) -> set[str]:
    return {r for _, _, p in _paragraphs(obj, sections) for r in (p.get("claims") or [])}


def parity(draft: dict, sections: list[str], rep: Report, strict_names: bool = True):
    en, it = draft["en"], draft["it"]
    t_en, t_it = _lang_text(en, sections), _lang_text(it, sections)
    # multiset comparison: every figure appears as many times in each language
    # (a bare number may carry its unit in only one language: "3 per cent" vs "3%")
    n_en = Counter(v for _, v in textutil.canonical_numbers(t_en, "en"))
    n_it = Counter(v for _, v in textutil.canonical_numbers(t_it, "it"))
    if n_en != n_it:
        rep.err("parity_numbers", "both", "parity",
                f"figures differ — EN only: {sorted((n_en - n_it).elements())[:6]}, IT only: {sorted((n_it - n_en).elements())[:6]}")
    if textutil.extract_dates(t_en) != textutil.extract_dates(t_it):
        rep.err("parity_dates", "both", "parity", "dates differ between Italian and English")
    if textutil.extract_weekdays(t_en) != textutil.extract_weekdays(t_it):
        rep.err("parity_dates", "both", "parity", "weekdays differ between Italian and English")
    if _claim_refs(en, sections) != _claim_refs(it, sections):
        rep.err("parity_claims", "both", "parity", "the two versions cite different claims (facts differ)")
    a_en, a_it = names_in(t_en), names_in(t_it)
    head = names_in(en.get("headline", "")) | names_in(it.get("headline", ""))
    only = (a_en - a_it) | (a_it - a_en)
    key = {a for a in only if a in head or textutil.fold(t_en).count(a) + textutil.fold(t_it).count(a) >= 3}
    if key:
        # names are already checked against the evidence in each language; a
        # name present in one version only is a consistency warning, not a fact error
        rep.warn("parity_names", "both", "parity",
                                                 f"names appear in only one language: {sorted(key)[:6]}")
    elif only:
        rep.warn("parity_names_minor", "both", "parity", f"minor name differences: {sorted(only)[:6]}")


def check_market_observation(obs: dict, edition_date, stale_days: dict) -> list[str]:
    """Market-data gate: every observation needs provider, instrument, timestamp,
    and must be fresh enough to be shown as the latest value."""
    problems = []
    for k in ("provider", "instrument", "date", "value", "frequency"):
        if obs.get(k) in (None, ""):
            problems.append(f"missing {k}")
    if obs.get("date"):
        d = datetime.fromisoformat(str(obs["date"])[:10]).date()
        if obs.get("frequency") == "monthly":
            age = (edition_date - d).days - 31
            limit = stale_days.get("monthly", 75) - 31
        else:
            age = (edition_date - d).days
            limit = stale_days.get("daily", 4)
        if age > limit:
            problems.append(f"stale: observation dated {d}, {age} days old (limit {limit})")
        if d > edition_date:
            problems.append("observation dated in the future")
    return problems
