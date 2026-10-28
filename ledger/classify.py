"""Deterministic topic / geography / city classification from the multilingual
lexicon plus the source's own declared coverage."""
from __future__ import annotations

import re

from . import config, textutil

CORE_TOPICS = ["finance", "economics", "politics", "society", "tax", "law", "crime", "culture", "travel"]
SUB_TOPICS = ["housing", "transport", "events"]


_OPINION = re.compile(
    r"(\|\s*(editorial|letters?|opinion|[A-Z][a-z]+(\s[A-Z][a-z'’-]+){1,2})\s*$)|^(the guardian view|opinion|editorial|commento|l'editoriale|"
    r"il commento|l'analisi|analisi|op-ed|tribune|l'edito|lettere|letters)\b|\b(opinion|editorial|op-ed|commentary)\s*[:|]",
    re.I)


def is_opinion(title: str) -> bool:
    """Columns, editorials and letters are not reporting; they are excluded
    from the evidence base (spec: distinguish reporting from opinion)."""
    return bool(_OPINION.search(title.strip()))


def classify_text(title: str, summary: str, src: config.Source) -> dict:
    lex = config.lexicon()
    f_title = textutil.fold(title)
    f_all = textutil.fold(title + " . " + summary)
    scores = {}
    for topic, terms in lex["topics"].items():
        s = 2 * textutil.count_terms(f_title, terms) + textutil.count_terms(f_all, terms)
        if topic in src.topics:
            s += 1.5
        if s:
            scores[topic] = s
    core = {t: s for t, s in scores.items() if t in CORE_TOPICS}
    topics = sorted(core, key=lambda t: -core[t])
    primary = topics[0] if topics else None
    # keep secondary topics that are reasonably strong
    topics = [t for t in topics if core[t] >= max(1.5, core[primary] * 0.35)] if primary else []
    subs = [t for t in SUB_TOPICS if scores.get(t, 0) >= 1.5]

    countries = set()
    for c, terms in lex["countries"].items():
        if textutil.has_term(f_all, terms):
            countries.add(c)
    if not countries:
        countries.update(g for g in src.geo if g != "INT")
    text_cities = set()
    for city, terms in lex["cities"].items():
        if textutil.count_terms(f_title, terms) or textutil.count_terms(f_all, terms) >= 2:
            text_cities.add(city)
    cities = set(src.cities) | text_cities
    # A national source mentioning a region (e.g. "Veneto") is not a city story
    # unless the city itself is named; local sources keep their city.
    low_value = bool(textutil.has_term(f_all, lex["low_value"])) and not (
        primary in ("finance", "economics", "politics", "tax", "law") and core.get(primary, 0) >= 3)
    practical = textutil.count_terms(f_all, lex["practical"])
    return {
        "opinion": is_opinion(title),
        "topics": topics, "primary_topic": primary, "subtopics": subs, "topic_scores": scores,
        "countries": sorted(countries), "cities": sorted(cities), "text_cities": sorted(text_cities),
        "low_value": low_value, "practical": practical,
    }
