"""Story production: evidence → draft (LLM) → gate → repair → publish or fall
back to an attributed headline brief. Nothing reaches a reader without
passing validate.validate()."""
from __future__ import annotations

import copy
import hashlib
import json
import logging

from . import config, evidence as evmod, prompts, textutil, timeutil, validate
from .llm import BudgetExhausted, LLMError

log = logging.getLogger("ledger.writer")

LANG_NAMES = {"en": "English", "it": "Italian", "fr": "French", "de": "German", "lb": "Luxembourgish", "pt": "Portuguese"}


def story_id(date_str: str, cluster_id: str) -> str:
    return "s" + hashlib.sha1(f"{date_str}|{cluster_id}".encode()).hexdigest()[:10]


def labels_for(meta: dict, kind: str, access: str) -> list[str]:
    if kind == "brief":
        # a brief shows one attributed headline; it earns no verification label
        return ["brief"] + (["developing"] if meta.get("updates") else [])
    labels = []
    v = meta["verification"]
    if v.startswith("official"):
        labels.append("official_source")
    if v in ("corroborated", "official_corroborated"):
        labels.append("corroborated")
    if v == "single_source":
        labels.append("single_source")
    if access in ("headline_snippet", "headline_only"):
        labels.append("snippet_based")
    if meta.get("conflicts"):
        labels.append("conflicting_reports")
    if meta.get("updates"):
        labels.append("developing")
    if kind == "brief":
        labels.append("brief")
    if meta.get("cities"):
        labels.append("local_impact")
    return labels


def make_brief(meta: dict, evidence: list[dict], reason: str) -> dict:
    """Extractive fallback: the original headline, attributed and linked, in
    both editions. Deterministic — it cannot contain invented content."""
    lead = evidence[0]
    lang = lead["lang"]
    content = {}
    for out in ("en", "it"):
        content[out] = {
            "headline": lead["title"], "headline_lang": lang,
            "subheadline": "", "body": [], "why_it_matters": [], "what_next": [], "key_facts": [],
        }
    return {"kind": "brief", "content": content, "claims": [], "legal_status": "none", "brief_reason": reason}


def _attach(draft: dict, rep: validate.Report, evidence: list[dict]) -> dict:
    """Keep only cited evidence; record per-claim support in the evidence record."""
    good = rep.supported_claims  # type: ignore[attr-defined]
    cited = {e for c in good.values() for e in c["source_ids"]}
    ev_out = [e for e in evidence if e["eid"] in cited]
    claims = []
    for cid, c in good.items():
        claims.append({"id": cid, "type": c.get("type", "fact"), "source_ids": c["source_ids"],
                       "support": c["support"], "text": {"en": c.get("en", ""), "it": c.get("it", "")},
                       "status": "verified" if len({evidence_origin(evidence, e) for e in c["source_ids"]}) > 1
                       or any(_ev(evidence, e)["official"] for e in c["source_ids"]) else "reported"})
    return {"kind": "article", "claims": claims, "evidence": ev_out, "legal_status": draft.get("legal_status", "none"),
            "content": {lang: draft[lang] for lang in ("en", "it")}}


def _ev(evidence, eid):
    return next(e for e in evidence if e["eid"] == eid)


def evidence_origin(evidence, eid):
    return _ev(evidence, eid)["origin"]


class Writer:
    def __init__(self, llm, audit: list | None = None):
        self.llm = llm
        self.st = config.settings()
        self.audit = audit if audit is not None else []
        self.metrics = {"drafts": 0, "published_articles": 0, "fallback_briefs": 0, "repairs": 0,
                        "language_regenerations": 0, "unsupported_claims_detected": 0, "unsupported_claims_blocked": 0,
                        "attribution_failures": 0, "numerical_inconsistencies": 0, "validation_failures": 0,
                        "llm_errors": 0}

    # ------------------------------------------------------------------ story
    def write_story(self, cand: dict, date_str: str) -> dict:
        meta = cand["meta"]
        evidence = cand["evidence"]
        access = evmod.access_summary(evidence)
        meta = {**meta, "access": access, "date": date_str}
        base = {"id": story_id(date_str, cand["cluster_id"]), "cluster_id": cand["cluster_id"], "date": date_str,
                "primary_topic": meta.get("primary_topic"), "topics": meta.get("topics", []),
                "subtopics": meta.get("subtopics", []), "countries": meta.get("countries", []),
                "cities": meta.get("cities", []), "verification": meta["verification"],
                "independent_origins": meta["origins"], "n_independent": meta["n_independent"],
                "score": cand["score"], "conflicts": meta.get("conflicts", []), "access": access,
                "created_at": timeutil.iso(timeutil.now_utc()), "versions": [], "corrections": [],
                "updates": meta.get("updates"), "all_reports": cand.get("all_reports", [])}
        draft, rep, model = None, None, None
        reason = ""
        if self.llm is not None and getattr(self.llm, "available", False):
            try:
                draft, rep, model = self._draft_with_repair(evidence, meta)
            except BudgetExhausted as ex:
                reason = f"AI budget exhausted: {ex}"
            except LLMError as ex:
                self.metrics["llm_errors"] += 1
                reason = f"AI unavailable: {ex}"
        else:
            reason = "AI drafting disabled or not configured"
        if draft is not None and rep is not None and rep.passed:
            out = _attach(draft, rep, evidence)
            self.metrics["published_articles"] += 1
            # re-derive independence from the evidence actually cited
            origins = sorted({e["origin"] for e in out["evidence"]})
            has_off = any(e["official"] for e in out["evidence"])
            n = len(origins)
            out_ver = ("official_corroborated" if has_off and n >= 2 else "official" if has_off
                       else "corroborated" if n >= 2 else "single_source")
            base.update(out, verification=out_ver, independent_origins=origins, n_independent=n,
                        generated_by=f"llm:{model}", validation=rep.to_dict())
            base["access"] = evmod.access_summary(out["evidence"])
        else:
            if rep is not None and not rep.passed:
                reason = "failed verification: " + "; ".join(e["code"] for e in rep.errors[:6])
            brief = make_brief(meta, evidence, reason)
            self.metrics["fallback_briefs"] += 1
            base.update(brief, evidence=evidence[:1], generated_by="extractive",
                        validation=(rep.to_dict() if rep else {"passed": True, "errors": [], "warnings": [],
                                                               "metrics": {}}))
            base["validation"]["brief_reason"] = reason
            # the brief itself is one attributed, linked headline: single-source by construction
            base["n_independent"] = 1
        base["labels"] = labels_for({**meta, "verification": base["verification"]}, base["kind"], base["access"])
        return base

    def _record(self, stage: str, meta: dict, draft, rep: validate.Report | None, model: str | None):
        self.audit.append({"at": timeutil.iso(timeutil.now_utc()), "stage": stage, "cluster": meta.get("cluster_id"),
                           "model": model, "draft": draft, "validation": rep.to_dict() if rep else None})

    def _count(self, rep: validate.Report):
        m = rep.metrics
        unsup = sum(m.get(k, 0) for k in ("unsupported_claim", "unsupported_number", "unsupported_date",
                                          "unsupported_quote", "unsupported_name", "unsupported_causation",
                                          "legal_status_upgrade", "presumption_of_innocence", "claim_without_source"))
        self.metrics["unsupported_claims_detected"] += unsup
        self.metrics["attribution_failures"] += m.get("attribution_missing", 0)
        self.metrics["numerical_inconsistencies"] += m.get("unsupported_number", 0) + m.get("parity_numbers", 0)
        if not rep.passed:
            self.metrics["validation_failures"] += 1
        return unsup

    def _draft_with_repair(self, evidence, meta, kind: str = "story"):
        system = prompts.story_system(self.st) if kind == "story" else prompts.ANALYSIS_SYSTEM
        user = prompts.story_user(evidence, meta, meta.get("conflicts") or [], self.st["llm"]["max_input_chars"])
        draft, model = self.llm.chat_json(system, user)
        self.metrics["drafts"] += 1
        if draft.get("abstain"):
            self._record("abstain", meta, draft, None, model)
            return None, None, model
        rep = validate.validate(draft, evidence, meta, kind)
        unsup = self._count(rep)
        self._record("draft", meta, draft, rep, model)
        if rep.passed:
            return draft, rep, model
        # repair round
        self.metrics["repairs"] += 1
        repair = prompts.REPAIR_PREFIX.format(problems=rep.summary(), draft=json.dumps(draft, ensure_ascii=False)[:9000])
        draft2, model = self.llm.chat_json(system, user + "\n\n" + repair)
        rep2 = validate.validate(draft2, evidence, meta, kind)
        self._count(rep2)
        self._record("repair", meta, draft2, rep2, model)
        if rep2.passed:
            return draft2, rep2, model
        # one language failing alone → regenerate only that language
        failing = rep2.langs_failing() - {"both"}
        if len(failing) == 1 and "both" not in rep2.langs_failing() and kind == "story":
            lang = failing.pop()
            other = "it" if lang == "en" else "en"
            self.metrics["language_regenerations"] += 1
            msg = prompts.ONE_LANGUAGE_PREFIX.format(
                other=LANG_NAMES[other], lang=lang, lang_name=LANG_NAMES[lang],
                problems="; ".join(e["msg"] for e in rep2.errors if e["lang"] == lang)[:1500],
                claims=json.dumps(draft2.get("claims"), ensure_ascii=False)[:6000],
                other_obj=json.dumps(draft2.get(other), ensure_ascii=False)[:5000])
            part, model = self.llm.chat_json(system, user + "\n\n" + msg)
            if isinstance(part.get(lang), dict):
                draft3 = copy.deepcopy(draft2)
                draft3[lang] = part[lang]
                rep3 = validate.validate(draft3, evidence, meta, kind)
                self._count(rep3)
                self._record("language_regeneration", meta, draft3, rep3, model)
                if rep3.passed:
                    return draft3, rep3, model
                rep2 = rep3
        self.metrics["unsupported_claims_blocked"] += unsup
        return draft2, rep2, model

    # --------------------------------------------------------------- analysis
    def write_analysis(self, theme: dict, date_str: str) -> dict | None:
        if self.llm is None or not getattr(self.llm, "available", False):
            return None
        meta = {**theme["meta"], "date": date_str, "access": evmod.access_summary(theme["evidence"])}
        try:
            draft, rep, model = self._draft_with_repair(theme["evidence"], meta, kind="analysis")
        except LLMError:
            return None
        if not draft or not rep or not rep.passed:
            return None
        out = _attach(draft, rep, theme["evidence"])
        return {**out, "id": story_id(date_str, "bp-" + theme["theme_id"]), "kind": "analysis", "date": date_str,
                "theme": theme["label"], "primary_topic": meta.get("primary_topic"), "topics": meta.get("topics", []),
                "countries": meta.get("countries", []), "cities": [], "verification": "analysis",
                "independent_origins": sorted({e["origin"] for e in out["evidence"]}),
                "n_independent": len({e["origin"] for e in out["evidence"]}),
                "generated_by": f"llm:{model}", "validation": rep.to_dict(), "labels": ["analysis"],
                "created_at": timeutil.iso(timeutil.now_utc()), "related": theme.get("related", []),
                "versions": [], "corrections": [], "score": theme.get("score", 0), "access": meta["access"]}
