"""Shared fixtures. All test content below is SYNTHETIC test data written for
these tests (fictional figures attributed to real outlet ids only so that the
registry lookups work). It is never published."""
import copy
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger import config, ingest, timeutil  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("LEDGER_SITE", str(tmp_path / "site"))
    monkeypatch.setenv("LEDGER_NOW", "2026-09-30T07:00:00+02:00")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config.reset_caches()
    yield
    config.reset_caches()


def ev(eid, source_id, passage, lang="en", published="2026-09-29T08:00:00Z", access="headline_snippet", official=False,
       origin=None, url=None, title=None):
    src = config.sources()[source_id]
    return {"eid": eid, "item_id": eid + "x", "source_id": source_id, "source_name": src.name, "source_type": src.type,
            "official": official or src.official, "origin": origin or src.owner, "title": title or passage.split(".")[0],
            "url": url or f"{src.home}/test-{eid}", "published": published, "date_quality": "exact",
            "retrieved_at": "2026-09-30T04:00:00Z", "access_level": access, "lang": lang, "passage": passage,
            "paywall": src.paywall, "url_check": None}


INFLATION_EVIDENCE = [
    ev("e1", "istat", "Istat: a settembre l'inflazione sale al 2,1% su base annua. Secondo le stime preliminari, "
       "l'indice dei prezzi al consumo aumenta dello 0,3% su base mensile e del 2,1% su base annua, da 1,8% di agosto.",
       lang="it", access="official_document"),
    ev("e2", "guardian_italy", "Italian inflation rises to 2.1% in September. Italy's annual inflation rate rose to 2.1% "
       "in September from 1.8% in August, the national statistics office Istat said on Tuesday."),
]


def good_draft():
    return copy.deepcopy({
        "main_event": "Italian inflation September flash estimate",
        "claims": [
            {"id": "c1", "type": "official", "source_ids": ["e1"],
             "support": "l'indice dei prezzi al consumo aumenta dello 0,3% su base mensile e del 2,1% su base annua",
             "en": "Consumer prices rose 0.3% on the month and 2.1% on the year.",
             "it": "I prezzi al consumo sono aumentati dello 0,3% su base mensile e del 2,1% su base annua."},
            {"id": "c2", "type": "fact", "source_ids": ["e2"],
             "support": "Italy's annual inflation rate rose to 2.1% in September from 1.8% in August",
             "en": "Annual inflation rose to 2.1% in September from 1.8% in August.",
             "it": "L'inflazione annua è salita al 2,1% a settembre dall'1,8% di agosto."},
        ],
        "legal_status": "none",
        "en": {"headline": "Italian inflation rises to 2.1% in September",
               "subheadline": "Istat's preliminary estimate shows prices up 0.3% on the month.",
               "body": [{"text": "Italy's annual inflation rate rose to 2.1% in September from 1.8% in August, according to preliminary estimates from Istat.", "claims": ["c1", "c2"]},
                        {"text": "Consumer prices increased 0.3% compared with the previous month.", "claims": ["c1"]}],
               "why_it_matters": [{"text": "Faster price growth could weigh on household purchasing power.", "claims": ["c2"]}],
               "what_next": [], "key_facts": [{"text": "Annual rate: 2.1% (August: 1.8%).", "claims": ["c2"]}]},
        "it": {"headline": "Inflazione in Italia al 2,1% a settembre",
               "subheadline": "Le stime preliminari dell'Istat indicano prezzi in aumento dello 0,3% su base mensile.",
               "body": [{"text": "L'inflazione annua in Italia è salita al 2,1% a settembre, dall'1,8% di agosto, secondo le stime preliminari dell'Istat.", "claims": ["c1", "c2"]},
                        {"text": "I prezzi al consumo sono aumentati dello 0,3% rispetto al mese precedente.", "claims": ["c1"]}],
               "why_it_matters": [{"text": "Una crescita più rapida dei prezzi potrebbe pesare sul potere d'acquisto delle famiglie.", "claims": ["c2"]}],
               "what_next": [], "key_facts": [{"text": "Tasso annuo: 2,1% (agosto: 1,8%).", "claims": ["c2"]}]},
    })


META = {"verification": "official_corroborated", "access": "official_document", "topics": ["economics"],
        "primary_topic": "economics", "conflicts": []}


def make_item(source_id, title, summary, published="2026-09-30T04:30:00Z", url=None, agency=None):
    src = config.sources()[source_id]
    url = url or f"{src.home}/{abs(hash(title)) % 10**8}"
    return ingest.Item(id=ingest.item_id(url), source_id=source_id, source_name=src.name, owner=src.owner, url=url,
                       title=title, summary=summary, published=published, date_quality="exact",
                       retrieved_at="2026-09-30T04:40:00Z", lang=src.lang,
                       access_level="headline_snippet" if len(summary) > 40 else "headline_only",
                       agency=agency or ingest.detect_agency((title + " " + summary).lower()))
