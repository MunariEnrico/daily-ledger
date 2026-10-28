"""Corrections and withdrawals. Published text is never silently replaced: the
previous version is kept on the story (shown to readers), a correction note is
added in both languages, the corrected text must pass the same publication
gate, and every action is appended to data/corrections.jsonl."""
from __future__ import annotations

import copy

from . import store, timeutil, validate


class CorrectionRejected(Exception):
    def __init__(self, report: validate.Report):
        super().__init__("corrected text failed verification:\n" + report.summary())
        self.report = report


def correct(date_str: str, story_id: str, draft: dict, note_en: str, note_it: str, new_evidence: list[dict] | None = None) -> dict:
    """draft: {"claims": [...], "legal_status": ..., "en": {...}, "it": {...}} in writer format.
    new_evidence: optional extra evidence records (e.g. the official document proving the error)."""
    ed = store.load_edition(date_str)
    if not ed or story_id not in ed["stories"]:
        raise KeyError(f"story {story_id} not found in edition {date_str}")
    s = ed["stories"][story_id]
    evidence = copy.deepcopy(s.get("evidence", [])) + (new_evidence or [])
    meta = {"verification": s.get("verification"), "access": s.get("access"), "topics": s.get("topics"),
            "primary_topic": s.get("primary_topic"), "conflicts": s.get("conflicts")}
    rep = validate.validate(draft, evidence, meta, "story" if s["kind"] != "analysis" else "analysis")
    if not rep.passed:
        raise CorrectionRejected(rep)
    now = timeutil.iso(timeutil.now_utc())
    s.setdefault("versions", []).append({"replaced_at": now, "reason": note_en, "content": s["content"],
                                          "claims": s.get("claims", []), "validation": s.get("validation")})
    good = rep.supported_claims  # type: ignore[attr-defined]
    s["content"] = {l: draft[l] for l in ("en", "it")}
    s["claims"] = [{"id": k, "type": c.get("type", "fact"), "source_ids": c["source_ids"], "support": c["support"],
                    "text": {"en": c.get("en", ""), "it": c.get("it", "")}, "status": "verified"} for k, c in good.items()]
    s["evidence"] = evidence
    s["kind"] = "article" if s["kind"] == "brief" else s["kind"]
    s["validation"] = rep.to_dict()
    s.setdefault("corrections", []).append({"at": now, "note": {"en": note_en, "it": note_it}})
    store.save_edition(date_str, ed)
    store.append_correction({"date": date_str, "story_id": story_id, "action": "correction",
                             "note": {"en": note_en, "it": note_it}, "languages_checked": ["en", "it"]})
    return s


def withdraw(date_str: str, story_id: str, note_en: str, note_it: str) -> dict:
    ed = store.load_edition(date_str)
    if not ed or story_id not in ed["stories"]:
        raise KeyError(f"story {story_id} not found in edition {date_str}")
    s = ed["stories"][story_id]
    now = timeutil.iso(timeutil.now_utc())
    s["withdrawn"] = {"en": f"Withdrawn on {now[:10]}: {note_en}", "it": f"Ritirata il {now[:10]}: {note_it}"}
    s.setdefault("corrections", []).append({"at": now, "note": {"en": note_en, "it": note_it}, "withdrawn": True})
    store.save_edition(date_str, ed)
    store.append_correction({"date": date_str, "story_id": story_id, "action": "withdrawal",
                             "note": {"en": note_en, "it": note_it}})
    return s
