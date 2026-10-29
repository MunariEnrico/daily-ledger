"""The 20 required test scenarios (spec §23). Each test states the expected
behaviour in its docstring and asserts it against the real implementation."""
import copy
import json
import smtplib
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import INFLATION_EVIDENCE, META, ev, good_draft, make_item
from ledger import (config, corrections, evidence as evmod, ingest, mailer, pipeline, site, store, textutil,
                    timeutil, validate, writer)
from ledger.llm import LLMError, ScriptedLLM


def codes(rep):
    return {e["code"] for e in rep.errors}


def cand(evidence, verification="official_corroborated", topics=("economics",), conflicts=None, n=2):
    return {"cluster_id": "ctest", "evidence": evidence, "score": 70.0,
            "meta": {"cluster_id": "ctest", "primary_topic": topics[0], "topics": list(topics), "subtopics": [],
                     "countries": ["IT"], "cities": [], "verification": verification, "n_independent": n,
                     "origins": ["istat", "gmg"], "conflicts": conflicts or [], "updates": None}}


# 1 ─────────────────────────────────────────────────────────────────────
def test_01_headline_accessible_but_not_full_text():
    """Only a headline+snippet is available: a long article implying the full
    text was read is rejected; a short, labelled story passes."""
    e = [ev("e1", "repubblica", "Manovra, il governo valuta un taglio dell'Irpef. La bozza prevede un'aliquota del 33% "
            "per i redditi fino a 50.000 euro.", lang="it", access="headline_snippet")]
    meta = {"verification": "single_source", "access": "headline_snippet", "topics": ["tax"], "conflicts": []}
    claim = {"id": "c1", "type": "reported", "source_ids": ["e1"],
             "support": "La bozza prevede un'aliquota del 33% per i redditi fino a 50.000 euro",
             "en": "The draft provides for a 33% rate on incomes up to 50,000 euros.",
             "it": "La bozza prevede un'aliquota del 33% per i redditi fino a 50.000 euro."}
    long_en = " ".join(["According to la Repubblica, the draft provides for a 33% rate on incomes up to 50,000 euros."] * 9)
    long_it = " ".join(["Secondo la Repubblica, la bozza prevede un'aliquota del 33% per i redditi fino a 50.000 euro."] * 9)
    d = {"claims": [claim], "legal_status": "draft",
         "en": {"headline": "Draft budget would set 33% rate", "body": [{"text": long_en, "claims": ["c1"]}]},
         "it": {"headline": "La bozza della manovra prevede un'aliquota del 33%", "body": [{"text": long_it, "claims": ["c1"]}]}}
    rep = validate.validate(d, e, meta)
    assert "too_long_for_access" in codes(rep)
    d["en"]["body"][0]["text"] = long_en.split(". ")[0] + "."
    d["it"]["body"][0]["text"] = long_it.split(". ")[0] + "."
    assert validate.validate(d, e, meta).passed
    assert "snippet_based" in writer.labels_for({"verification": "single_source"}, "article", "headline_snippet")


# 2 ─────────────────────────────────────────────────────────────────────
def test_02_two_sources_report_conflicting_figures():
    """Different independent sources give different death tolls: the conflict
    is detected; a story citing only one figure is rejected, one reporting
    both passes."""
    items = [
        make_item("ansa_cronaca", "Crollo a Napoli, 3 morti", "Il bilancio provvisorio è di 3 morti e 5 feriti.").to_dict(),
        make_item("guardian_italy", "Building collapse in Naples", "At least 4 dead and 5 injured after the collapse.").to_dict(),
    ]
    conflicts = evmod.detect_conflicts(items)
    assert conflicts and conflicts[0]["measure"] == "dead"
    assert {v["value"] for v in conflicts[0]["values"]} == {3.0, 4.0}
    e = [ev("e1", "ansa_cronaca", "Crollo a Napoli, 3 morti. Il bilancio provvisorio è di 3 morti e 5 feriti.", lang="it"),
         ev("e2", "guardian_italy", "Building collapse in Naples. At least 4 dead and 5 injured after the collapse.")]
    meta = {"verification": "corroborated", "access": "headline_snippet", "topics": ["crime"], "conflicts": conflicts}
    claims = [{"id": "c1", "source_ids": ["e1"], "support": "Il bilancio provvisorio è di 3 morti e 5 feriti",
               "en": "The provisional toll is 3 dead.", "it": "Il bilancio provvisorio è di 3 morti."},
              {"id": "c2", "source_ids": ["e2"], "support": "At least 4 dead and 5 injured after the collapse",
               "en": "At least 4 dead.", "it": "Almeno 4 morti."}]
    one = {"claims": claims, "en": {"headline": "Naples collapse", "body": [{"text": "Three people died in Naples, according to ANSA.", "claims": ["c1"]}]},
           "it": {"headline": "Crollo a Napoli", "body": [{"text": "Tre persone sono morte a Napoli, secondo l'ANSA.", "claims": ["c1"]}]}}
    assert "conflict_one_sided" in codes(validate.validate(one, e, meta))
    both = copy.deepcopy(one)
    both["en"]["body"][0] = {"text": "ANSA reports 3 dead in Naples, while The Guardian reports at least 4 dead.", "claims": ["c1", "c2"]}
    both["it"]["body"][0] = {"text": "L'ANSA riporta 3 morti a Napoli, mentre il Guardian riferisce di almeno 4 morti.", "claims": ["c1", "c2"]}
    rep = validate.validate(both, e, meta)
    assert "conflict_one_sided" not in codes(rep), rep.summary()


# 3 ─────────────────────────────────────────────────────────────────────
def test_03_same_agency_copy_is_not_independent_corroboration():
    """Three outlets reprinting one ANSA dispatch (credited or verbatim) count
    as ONE origin, not three."""
    txt = "Il consiglio dei ministri ha approvato il decreto sulle infrastrutture con uno stanziamento di 2 miliardi di euro per le opere"
    items = [make_item("messaggero", "Infrastrutture, via libera al decreto", "(ANSA) - ROMA. " + txt).to_dict(),
             make_item("tgcom24", "Infrastrutture, via libera al decreto", txt).to_dict(),
             make_item("libero", "Decreto infrastrutture approvato dal governo", txt + " (ANSA).").to_dict()]
    status, n, origins = evmod.verification_status(items)
    assert n == 1 and status == "single_source", origins
    items.append(make_item("guardian_italy", "Italy approves infrastructure decree",
                           "Italy's cabinet approved a decree on infrastructure, ministers said.").to_dict())
    assert evmod.verification_status(items)[1] == 2


# 4 ─────────────────────────────────────────────────────────────────────
def test_04_tax_proposal_not_yet_approved():
    """Evidence describes a proposal: calling it approved/in force — in text or
    in legal_status — is rejected; reporting it as a proposal passes."""
    e = [ev("e1", "guardian_tax", "Chancellor proposes new levy on online gambling firms. The proposal would raise the "
            "remote gaming duty to 40% and is subject to consultation."),
         ev("e2", "bbc_business", "The Treasury plans to raise remote gaming duty to 40%, under a proposal published for consultation.")]
    meta = {"verification": "corroborated", "access": "headline_snippet", "topics": ["tax"], "conflicts": []}
    claims = [{"id": "c1", "source_ids": ["e1"], "support": "The proposal would raise the remote gaming duty to 40%",
               "en": "The proposal would raise remote gaming duty to 40%.", "it": "La proposta porterebbe l'imposta sul gioco online al 40%."}]
    bad = {"claims": claims, "legal_status": "approved",
           "en": {"headline": "Gaming duty raised to 40%", "body": [{"text": "The remote gaming duty has been approved at 40% and is in force.", "claims": ["c1"]}]},
           "it": {"headline": "Imposta sul gioco al 40%", "body": [{"text": "L'imposta sul gioco online è stata approvata al 40% ed è in vigore.", "claims": ["c1"]}]}}
    rep = validate.validate(bad, e, meta)
    assert "legal_status_upgrade" in codes(rep)
    good = copy.deepcopy(bad)
    good["legal_status"] = "proposed"
    good["en"]["body"][0]["text"] = "Under a proposal now subject to consultation, remote gaming duty would rise to 40%."
    good["it"]["body"][0]["text"] = "Secondo una proposta ora in consultazione, l'imposta sul gioco online salirebbe al 40%."
    good["en"]["headline"] = "Proposal would raise gaming duty to 40%"
    good["it"]["headline"] = "Proposta: imposta sul gioco online al 40%"
    assert validate.validate(good, e, meta).passed, validate.validate(good, e, meta).summary()


# 5 ─────────────────────────────────────────────────────────────────────
def test_05_court_case_allegations_without_judgment():
    """Evidence reports an arrest and charges: guilt words are rejected and the
    text must signal that guilt is not established."""
    e = [ev("e1", "ansa_cronaca", "Arrestato un uomo di 45 anni a Mestre, è accusato di rapina aggravata. L'uomo è indagato "
            "dalla procura di Venezia.", lang="it")]
    meta = {"verification": "single_source", "access": "headline_snippet", "topics": ["crime"], "primary_topic": "crime", "conflicts": []}
    claims = [{"id": "c1", "source_ids": ["e1"], "support": "Arrestato un uomo di 45 anni a Mestre, è accusato di rapina aggravata",
               "en": "A 45-year-old man was arrested in Mestre.", "it": "Un uomo di 45 anni è stato arrestato a Mestre."}]
    guilty = {"claims": claims,
              "en": {"headline": "Robber arrested in Mestre", "body": [{"text": "The robber, a 45-year-old man, was convicted, ANSA reported.", "claims": ["c1"]}]},
              "it": {"headline": "Rapinatore arrestato a Mestre", "body": [{"text": "Il rapinatore, un uomo di 45 anni, è stato condannato, riporta l'ANSA.", "claims": ["c1"]}]}}
    assert "presumption_of_innocence" in codes(validate.validate(guilty, e, meta))
    unhedged = copy.deepcopy(guilty)
    unhedged["en"]["body"][0]["text"] = "Police in Mestre arrested a 45-year-old man, ANSA reported."
    unhedged["it"]["body"][0]["text"] = "La polizia a Mestre ha arrestato un uomo di 45 anni, riporta l'ANSA."
    unhedged["en"]["body"][0]["text"] = "A 45-year-old man was arrested in Mestre, ANSA reported."
    unhedged["it"]["body"][0]["text"] = "Un uomo di 45 anni è stato arrestato a Mestre, riporta l'ANSA."
    rep = validate.validate(unhedged, e, meta)
    assert "presumption_of_innocence" in codes(rep)
    hedged = copy.deepcopy(unhedged)
    hedged["en"]["body"][0]["text"] = "A 45-year-old man was arrested in Mestre and is accused of aggravated robbery, according to ANSA."
    hedged["it"]["body"][0]["text"] = "Un uomo di 45 anni è stato arrestato a Mestre ed è accusato di rapina aggravata, secondo l'ANSA."
    hedged["en"]["headline"] = "Man arrested in Mestre"
    hedged["it"]["headline"] = "Uomo arrestato a Mestre"
    rep = validate.validate(hedged, e, meta)
    assert rep.passed, rep.summary()


# 6 ─────────────────────────────────────────────────────────────────────
def test_06_market_data_is_stale():
    """An old observation is flagged stale (shown with its own date, never as
    current); a fresh one passes; a row without provider is invalid."""
    today = date(2026, 9, 30)
    fresh = {"provider": "ECB", "instrument": "EXR", "date": "2026-09-29", "value": 1.1, "frequency": "daily"}
    old = dict(fresh, date="2026-09-20")
    assert validate.check_market_observation(fresh, today, {"daily": 4}) == []
    assert any(p.startswith("stale") for p in validate.check_market_observation(old, today, {"daily": 4}))
    assert "missing provider" in validate.check_market_observation(dict(fresh, provider=""), today, {"daily": 4})
    monthly = {"provider": "ECB", "instrument": "IRS", "date": "2026-08-01", "value": 4.0, "frequency": "monthly"}
    assert validate.check_market_observation(monthly, today, {"monthly": 75}) == []


# 7 ─────────────────────────────────────────────────────────────────────
def test_07_source_url_is_invalid():
    """A URL that returns 404 (or is not http) is removed from the evidence;
    with no valid evidence left, no story is produced."""
    class FakeHttp:
        def head_ok(self, url, timeout=8):
            return (False, 404) if "dead" in url else (True, 200)
    e = [ev("e1", "guardian_italy", "Live report.", url="https://www.theguardian.com/dead-link"),
         ev("e2", "bbc_europe", "Another report.", url="https://www.bbc.com/news/ok")]
    kept = pipeline.check_urls(copy.deepcopy(e), FakeHttp(), 5)
    assert [k["eid"] for k in kept] == ["e2"]
    assert pipeline.domain_ok(dict(e[0], url="javascript:alert(1)")) is False
    bad = dict(e[1], url="https://unrelated-site.example/x")
    assert pipeline.domain_ok(bad) is True and bad["domain_mismatch"] is True
    assert pipeline.check_urls([dict(e[0])], FakeHttp(), 5) == []


# 8 ─────────────────────────────────────────────────────────────────────
def test_08_publication_date_is_ambiguous():
    """03-04-2026 in a URL could be 3 April or 4 March: it is marked ambiguous
    and such items are excluded from the news run; unambiguous or localized
    dates are parsed."""
    now = datetime(2026, 9, 30, 5, 0, tzinfo=timeutil.UTC)
    dt, q = ingest.date_from_url("https://x.fr/paris-75/story-03-04-2026-ABC.php", now, "Europe/Paris")
    assert dt is None and q == "ambiguous"
    dt, q = ingest.date_from_url("https://x.fr/paris-75/story-28-09-2026-ABC.php", now, "Europe/Paris")
    assert q == "from_url" and dt.day == 28 and dt.month == 9
    dt, q = ingest.parse_date("mer, 28 ott 2026 13:17:36 +0100", "Europe/Rome")
    assert q == "exact" and (dt.month, dt.day, dt.hour) == (10, 28, 12)
    dt, q = ingest.parse_date("Tuesday, October 27, 2026 - 12:15", "Europe/London")
    assert q == "no_tz" and dt.day == 27
    item = make_item("leparisien_paris", "Titre", "Résumé assez long pour être un extrait utile de l'article en question.")
    item.published, item.date_quality = None, "ambiguous"
    news, _, dropped = pipeline.prepare([item], timeutil.now_utc())
    assert news == [] and dropped["date_ambiguous"] == 1


# 9 ─────────────────────────────────────────────────────────────────────
def test_09_city_has_insufficient_verified_local_news(tmp_path):
    """A city with no qualifying stories renders the standard empty-section
    message; when its sources failed, the page says so instead of implying
    there was no news."""
    plan_ = {"international": {"front": [], "sections": {}}, "cities": {c: {"front": [], "sections": {}} for c in config.editions()["cities"]}}
    feed_results = [{"source_id": "veneziatoday", "ok": False, "error": "HTTP 503"}]
    run = {"items": {"fresh": 0}, "lookback_hours": 30, "sources": {"ok": 0, "checked": 1, "failed": ["veneziatoday"]},
           "clusters": 0, "llm": {}, "llm_status": "unavailable"}
    ed = pipeline.assemble("2026-09-30", plan_, {}, {}, [], feed_results, [], {"rows": [], "summary": {}}, run)
    assert ed["cities"]["venice"]["sources_failed"] == ["veneziatoday"]
    ed["status"] = "published"
    store.save_edition("2026-09-30", ed)
    store.set_index("2026-09-30", status="published")
    site.Site().build()
    html = (tmp_path / "site" / "2026-09-30" / "en" / "city-venice.html").read_text()
    assert "No significant developments meeting today’s editorial and verification criteria." in html
    assert "could not be reached" in html and "veneziatoday" in html
    html_it = (tmp_path / "site" / "2026-09-30" / "it" / "city-milan.html").read_text()
    assert "Nessuno sviluppo significativo" in html_it and "non erano raggiungibili" not in html_it


# 10 ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("mutation, code", [
    (lambda d: d["en"]["body"][0].update(text=d["en"]["body"][0]["text"] + " Core inflation was 1.9%."), "unsupported_number"),
    (lambda d: d["it"]["body"][1].update(text="Lo ha dichiarato il presidente Mario Rossi."), "unsupported_name"),
    (lambda d: d["en"]["body"][1].update(text='Istat called it "a temporary spike in energy prices".'), "unsupported_quote"),
    (lambda d: d["en"]["body"][1].update(text="Consumer prices increased 0.3% on 12 September."), "unsupported_date"),
    (lambda d: d["claims"][0].update(support="prices rose sharply because of energy costs"), "unsupported_claim"),
    (lambda d: d["en"]["body"][0].update(text="See https://example.com for more."), "url_in_text"),
])
def test_10_ai_draft_with_plausible_unsupported_detail(mutation, code):
    """A fluent draft that adds a plausible detail absent from the evidence —
    an extra figure, an invented name, a made-up quote, a date, a fabricated
    support excerpt, a link — is blocked by the code-level gate."""
    d = good_draft()
    mutation(d)
    rep = validate.validate(d, INFLATION_EVIDENCE, META)
    assert not rep.passed and code in codes(rep), rep.summary()


# 11 ────────────────────────────────────────────────────────────────────
def test_11_italian_and_english_versions_contain_different_numbers():
    """EN says 2.1%, IT says 2,2% (unsupported) or drops a figure: blocked by
    parity (and support) checks; identical figures in each convention pass."""
    d = good_draft()
    d["it"]["key_facts"][0]["text"] = "Tasso annuo: 2,1%."
    rep = validate.validate(d, INFLATION_EVIDENCE, META)
    assert "parity_numbers" in codes(rep)
    d = good_draft()
    d["it"]["body"][1]["text"] = "I prezzi al consumo sono aumentati dello 0,2% rispetto al mese precedente."
    rep = validate.validate(d, INFLATION_EVIDENCE, META)
    assert {"parity_numbers", "unsupported_number"} <= codes(rep)
    assert textutil.canonical_numbers("1,234.5 and 2.1%", "en") == textutil.canonical_numbers("1.234,5 e 2,1%", "it")


# 12 ────────────────────────────────────────────────────────────────────
def test_12_story_changes_after_publication():
    """A correction keeps the old version, adds a bilingual note, must pass the
    gate, and is logged; an unverifiable correction is refused. A follow-up
    cluster in a later run is linked to the earlier story as an update."""
    d = good_draft()
    w = writer.Writer(ScriptedLLM([d]))
    w.llm.available = True
    s = w.write_story(cand(copy.deepcopy(INFLATION_EVIDENCE)), "2026-09-30")
    assert s["kind"] == "article"
    ed = {"date": "2026-09-30", "status": "published", "stories": {s["id"]: s}}
    store.save_edition("2026-09-30", ed)
    fixed = good_draft()
    fixed["en"]["subheadline"] = "Istat's flash estimate shows monthly prices up 0.3%."
    corrections.correct("2026-09-30", s["id"], fixed, "Subheadline clarified.", "Sommario precisato.")
    after = store.load_edition("2026-09-30")["stories"][s["id"]]
    assert after["versions"][0]["content"]["en"]["subheadline"] == d["en"]["subheadline"]
    assert after["corrections"][0]["note"]["it"] == "Sommario precisato."
    assert store.corrections()[-1]["action"] == "correction"
    wrong = good_draft()
    wrong["en"]["headline"] = "Italian inflation rises to 2.4% in September"
    with pytest.raises(corrections.CorrectionRejected):
        corrections.correct("2026-09-30", s["id"], wrong, "x", "x")
    # follow-up detection
    store.set_index("2026-09-30", status="published")
    prev = store.previous_stories("2026-10-01")
    c = {"theme": pipeline._theme_key({"items": [{"title": "Istat: Italian inflation September estimate confirmed by Istat Italy"}]}),
         "items": [{"url": "https://www.istat.it/new-release", "title": "Istat Italy inflation final"}]}
    upd = pipeline.find_update(c, prev)
    assert upd and upd["story_id"] == s["id"]


# 13 ────────────────────────────────────────────────────────────────────
def test_13_search_snippet_conflicts_with_official_document():
    """A news snippet says 12 jobs lost, the official statement says 15: the
    official figure must be included; a story using only the snippet figure
    is rejected."""
    items = [make_item("veneziatoday", "Chiude lo stabilimento, a casa 12 lavoratori", "Secondo i sindacati sono 12 lavoratori.").to_dict(),
             make_item("comune_venezia", "Tavolo di crisi per lo stabilimento", "Il Comune: coinvolti 15 lavoratori, convocato il tavolo.").to_dict()]
    conflicts = evmod.detect_conflicts(items)
    assert conflicts and conflicts[0]["official_values"] == [15.0]
    e = [ev("e1", "veneziatoday", "Chiude lo stabilimento, a casa 12 lavoratori. Secondo i sindacati sono 12 lavoratori.", lang="it"),
         ev("e2", "comune_venezia", "Tavolo di crisi per lo stabilimento. Il Comune: coinvolti 15 lavoratori, convocato il tavolo.", lang="it")]
    meta = {"verification": "official_corroborated", "access": "headline_snippet", "topics": ["economics"], "conflicts": conflicts}
    claims = [{"id": "c1", "source_ids": ["e1"], "support": "Secondo i sindacati sono 12 lavoratori", "en": "Unions say 12 workers.", "it": "Per i sindacati 12 lavoratori."},
              {"id": "c2", "source_ids": ["e2"], "support": "Il Comune: coinvolti 15 lavoratori, convocato il tavolo", "en": "The city says 15 workers.", "it": "Il Comune parla di 15 lavoratori."}]
    snippet_only = {"claims": claims, "en": {"headline": "Plant closure", "body": [{"text": "Unions say 12 workers are affected.", "claims": ["c1"]}]},
                    "it": {"headline": "Chiude lo stabilimento", "body": [{"text": "Per i sindacati sono 12 i lavoratori coinvolti.", "claims": ["c1"]}]}}
    assert "conflict_official_missing" in codes(validate.validate(snippet_only, e, meta))
    ok = copy.deepcopy(snippet_only)
    ok["en"]["body"][0] = {"text": "The city council says 15 workers are affected; unions put the number at 12.", "claims": ["c1", "c2"]}
    ok["it"]["body"][0] = {"text": "Il Comune parla di 15 lavoratori coinvolti; per i sindacati sono 12.", "claims": ["c1", "c2"]}
    rep = validate.validate(ok, e, meta)
    assert not {"conflict_official_missing", "conflict_one_sided"} & codes(rep), rep.summary()


# 14 ────────────────────────────────────────────────────────────────────
def test_14_claim_cannot_be_independently_corroborated():
    """A single-source story must attribute its claims and is labelled; the
    front page prefers corroborated stories over higher-scoring single-source ones."""
    e = [INFLATION_EVIDENCE[1]]
    meta = {"verification": "single_source", "access": "headline_snippet", "topics": ["economics"], "conflicts": []}
    claims = [good_draft()["claims"][1]]
    d = {"claims": claims,
         "en": {"headline": "Italian inflation at 2.1%", "body": [{"text": "Annual inflation rose to 2.1% in September from 1.8% in August.", "claims": ["c2"]}]},
         "it": {"headline": "Inflazione al 2,1%", "body": [{"text": "L'inflazione annua è salita al 2,1% a settembre dall'1,8% di agosto.", "claims": ["c2"]}]}}
    assert "attribution_missing" in codes(validate.validate(d, e, meta))
    d["en"]["body"][0]["text"] = "Annual inflation rose to 2.1% in September from 1.8% in August, according to The Guardian."
    d["it"]["body"][0]["text"] = "L'inflazione annua è salita al 2,1% a settembre dall'1,8% di agosto, secondo il Guardian."
    rep = validate.validate(d, e, meta)
    assert "attribution_missing" not in codes(rep), rep.summary()
    assert "single_source" in writer.labels_for({"verification": "single_source"}, "article", "headline_snippet")
    mk = lambda cid, n, sc: {"cluster_id": cid, "n_independent": n, "official": False, "theme": {cid},  # noqa: E731
                             "scores": {"international": {"total": sc}}}
    picked = pipeline._pick([mk("single", 1, 90), mk("multi", 3, 70)], "international", 1, prefer_corroborated=True)
    assert picked[0]["cluster_id"] == "multi"


# 15 ────────────────────────────────────────────────────────────────────
def test_15_correlation_not_causation():
    """Shares fell and results were published: 'because of the results' is
    rejected when no source states the cause; the temporal 'after' passes."""
    e = [ev("e1", "ft_companies", "Stellantis shares fell 4% on Tuesday. The carmaker published quarterly results on Tuesday.")]
    meta = {"verification": "single_source", "access": "headline_snippet", "topics": ["finance"], "conflicts": []}
    claims = [{"id": "c1", "source_ids": ["e1"], "support": "Stellantis shares fell 4% on Tuesday",
               "en": "Stellantis shares fell 4%.", "it": "Le azioni Stellantis sono scese del 4%."}]
    causal = {"claims": claims,
              "en": {"headline": "Stellantis shares fall 4%", "body": [{"text": "Stellantis shares fell 4% because of its quarterly results, the Financial Times reported.", "claims": ["c1"]}]},
              "it": {"headline": "Stellantis cede il 4%", "body": [{"text": "Le azioni Stellantis sono scese del 4% a causa dei conti trimestrali, riporta il Financial Times.", "claims": ["c1"]}]}}
    assert "unsupported_causation" in codes(validate.validate(causal, e, meta))
    temporal = copy.deepcopy(causal)
    temporal["en"]["body"][0]["text"] = "Stellantis shares fell 4% on Tuesday, the day it published quarterly results, the Financial Times reported."
    temporal["it"]["body"][0]["text"] = "Le azioni Stellantis sono scese del 4% martedì, giorno della pubblicazione dei conti trimestrali, riporta il Financial Times."
    rep = validate.validate(temporal, e, meta)
    assert "unsupported_causation" not in codes(rep), rep.summary()


# 16 ────────────────────────────────────────────────────────────────────
def test_16_source_unavailable_during_ingestion(monkeypatch):
    """One feed errors out: its failure is recorded, the others are used, and
    the run continues."""
    srcs = [config.sources()["ansa_top"], config.sources()["bbc_world"]]
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
      <item><title>Test headline for ingestion</title><link>https://www.bbc.com/news/test-1</link>
      <description>A summary long enough to count as a snippet of the article.</description>
      <pubDate>Wed, 30 Sep 2026 04:00:00 GMT</pubDate></item></channel></rss>"""

    def fake_get(self, url, accept=None, timeout=None):
        if "ansa" in url:
            return "ERR", b"", url, "ConnectionError: simulated outage"
        return 200, rss, url, ""
    monkeypatch.setattr(ingest.Http, "get", fake_get)
    results, items = ingest.fetch_all(srcs)
    by = {r.source_id: r for r in results}
    assert not by["ansa_top"].ok and "simulated outage" in by["ansa_top"].error
    assert by["bbc_world"].ok and len(items) == 1 and items[0].date_quality == "exact"


# 17 ────────────────────────────────────────────────────────────────────
def test_17_ai_service_fails():
    """The model raises errors: every story falls back to an attributed
    headline brief (nothing invented), and the failure is counted."""
    w = writer.Writer(ScriptedLLM([LLMError("503 from provider")]))
    s = w.write_story(cand(copy.deepcopy(INFLATION_EVIDENCE)), "2026-09-30")
    assert s["kind"] == "brief" and s["labels"] == ["brief"]
    assert s["content"]["en"]["headline"] == INFLATION_EVIDENCE[0]["title"]
    assert "AI unavailable" in s["validation"]["brief_reason"] and w.metrics["llm_errors"] == 1
    w2 = writer.Writer(None)
    assert w2.write_story(cand(copy.deepcopy(INFLATION_EVIDENCE)), "2026-09-30")["kind"] == "brief"


def test_17b_circuit_breaker_stops_spending_quota(monkeypatch):
    """A provider returning garbage opens the circuit after a few failures
    instead of burning the whole daily budget."""
    from ledger import llm as llmmod

    class Resp:
        status_code, text, headers = 200, "OK", {}

        def json(self):
            raise ValueError("not json")
    monkeypatch.setattr(llmmod.requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(llmmod.time, "sleep", lambda s: None)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    c = llmmod.LLMClient()
    assert [p.name for p in c.providers] == ["groq"]
    for _ in range(5):
        with pytest.raises(LLMError):
            c.chat_json("s", "u")
    assert c.circuit_open and c.calls <= 7


# 18 ────────────────────────────────────────────────────────────────────
def test_18_email_delivery_fails(monkeypatch):
    """SMTP failure is recorded as 'failed' without raising; a missing
    configuration is reported as such; success is only 'accepted_by_smtp'."""
    ed = {"date": "2026-09-30", "stories": {}, "international": {"front": []}, "editor_note": {"it": "", "en": ""}}
    st = config.settings()
    monkeypatch.setitem(st["email"], "enabled", True)
    assert mailer.send(ed)["state"] == "not_configured"
    monkeypatch.setenv("SMTP_USER", "u@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("EMAIL_TO", "r@example.com")

    def boom(*a, **k):
        raise smtplib.SMTPConnectError(421, "simulated")
    monkeypatch.setattr(mailer.smtplib, "SMTP", boom)
    res = mailer.send(ed)
    assert res["state"] == "failed" and "simulated" in res["detail"]

    class OkSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): pass
        def login(self, u, p): pass
        def send_message(self, m): return {}
    monkeypatch.setattr(mailer.smtplib, "SMTP", OkSMTP)
    res = mailer.send(ed)
    assert res["state"] == "accepted_by_smtp" and "cannot be confirmed" in res["detail"]


# 19 ────────────────────────────────────────────────────────────────────
def test_19_scheduled_job_does_not_finish_before_0800():
    """Decision table: a build finishing after 08:00 publishes immediately and
    is flagged late; before 08:00 within the grace window it waits; DST is
    handled (08:00 is 06:00 UTC in summer, 07:00 UTC in winter)."""
    tz = ZoneInfo("Europe/Luxembourg")
    at = lambda h, m, d=30, mo=9: datetime(2026, mo, d, h, m, tzinfo=tz)  # noqa: E731
    assert pipeline.decide(at(5, 20), None, 20) == "build"
    assert pipeline.decide(at(8, 40), None, 20) == "build+publish"
    assert pipeline.decide(at(8, 40), "staged", 20) == "publish"
    assert pipeline.decide(at(7, 50), "staged", 20) == "wait+publish"
    assert pipeline.decide(at(6, 10), "staged", 20) == "nothing"
    assert pipeline.decide(at(9, 0), "published", 20) == "nothing"
    assert timeutil.publish_at(date(2026, 7, 1)).astimezone(timeutil.UTC).hour == 6
    assert timeutil.publish_at(date(2026, 12, 1)).astimezone(timeutil.UTC).hour == 7
    # a publish after 08:15 is recorded as late
    store.save_edition("2026-09-30", {"date": "2026-09-30", "status": "staged", "stories": {}, "run": {},
                                      "publish_at": timeutil.iso(timeutil.publish_at(date(2026, 9, 30)))})
    import os
    os.environ["LEDGER_NOW"] = "2026-09-30T08:40:00+02:00"
    res = pipeline.publish("2026-09-30")
    assert res["published"] and res["late"]
    assert pipeline.publish("2026-09-30")["reason"] == "already published"  # duplicate prevention


# 20 ────────────────────────────────────────────────────────────────────
def test_20_attempt_to_publish_without_sufficient_evidence():
    """Drafts without claims, or paragraphs citing nothing, are refused; with
    a failed repair the story becomes a brief. A cluster with no usable
    evidence produces no story at all."""
    empty = {"claims": [], "en": {"headline": "Something happened", "body": [{"text": "It was big.", "claims": []}]},
             "it": {"headline": "È successo qualcosa", "body": [{"text": "È stato grande.", "claims": []}]}}
    rep = validate.validate(empty, INFLATION_EVIDENCE, META)
    assert {"no_claims", "paragraph_without_claims"} <= codes(rep)
    w = writer.Writer(ScriptedLLM([empty, empty]))
    s = w.write_story(cand(copy.deepcopy(INFLATION_EVIDENCE)), "2026-09-30")
    assert s["kind"] == "brief" and "failed verification" in s["validation"]["brief_reason"]
    assert w.metrics["unsupported_claims_blocked"] >= 0 and w.metrics["validation_failures"] == 2

    class DeadHttp:
        def head_ok(self, url, timeout=8):
            return False, 404
    c = {"cluster_id": "cx", "items": [make_item("guardian_italy", "Gone", "This page no longer exists at all, sorry.").to_dict()],
         "agg": {"primary_topic": "economics", "topics": [], "subtopics": [], "countries": [], "cities": []},
         "verification": "single_source", "n_independent": 1, "origins": ["gmg"], "official": False, "theme": set(),
         "scores": {"international": {"total": 50}}}
    assert pipeline.prepare_story_inputs(c, [], DeadHttp(), config.settings()) is None
