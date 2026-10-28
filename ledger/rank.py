"""Transparent editorial ranking (100-point model, weights configurable).

    25  overall significance       — independent reporting origins, official confirmation
    20  economic/financial impact  — finance/economics/tax weight of the story
    15  political/regulatory      — politics/law/tax, institutions involved
    15  geographic relevance       — Italy > US/UK > EU > others (international);
                                     the city itself (city editions)
    10  novelty & recency
    10  source quality             — best source tier (an input, never proof)
     5  practical relevance        — deadlines, strikes, bills, rules for households

A high score does not make a story true: verification happens separately."""
from __future__ import annotations

from collections import Counter

from . import config, timeutil

TIER_POINTS = {1: 10, 2: 8, 3: 6, 4: 4}


def city_country(city: str) -> str | None:
    return config.editions()["cities"].get(city, {}).get("country")


def aggregate(cluster_items: list[dict]) -> dict:
    srcs = config.sources()
    topic_score: Counter = Counter()
    countries: Counter = Counter()
    city_votes: Counter = Counter()
    subtopics: Counter = Counter()
    practical = 0
    for it in cluster_items:
        cls = it["cls"]
        for t, s in cls["topic_scores"].items():
            topic_score[t] += s
        for c in cls["countries"]:
            countries[c] += 1
        src = srcs.get(it["source_id"])
        for c in cls["cities"]:
            # a city is credited by an explicit mention of the city in the text,
            # or by a local outlet — but only when the story concerns that
            # outlet's country (a Luxembourg site reprinting world news does
            # not make it Luxembourg news)
            local = bool(src and c in src.cities)
            text_mention = c in it["cls"].get("text_cities", cls["cities"])
            ccountry = city_country(c)
            about_home = (not cls["countries"]) or ccountry in cls["countries"]
            if local and about_home:
                city_votes[c] += 2
            elif text_mention and not local:
                city_votes[c] += 1
            elif local and text_mention:
                city_votes[c] += 2
        for s in cls["subtopics"]:
            subtopics[s] += 1
        practical = max(practical, cls["practical"])
    core = {t: s for t, s in topic_score.items() if t in ("finance", "economics", "politics", "society", "tax", "law",
                                                         "crime", "culture", "travel")}
    primary = max(core, key=core.get) if core else None
    topics = [t for t in sorted(core, key=lambda t: -core[t]) if core[t] >= max(2, core[primary] * 0.35)] if primary else []
    times = [timeutil.parse_iso(i["published"]) for i in cluster_items if i.get("published")]
    tiers = [srcs[i["source_id"]].tier for i in cluster_items if i["source_id"] in srcs]
    return {
        "primary_topic": primary, "topics": topics, "topic_score": dict(topic_score),
        "countries": [c for c, _ in countries.most_common()], "cities": sorted(c for c, v in city_votes.items() if v >= 2),
        "subtopics": [s for s, _ in subtopics.most_common()], "practical": practical,
        "latest": timeutil.iso(max(times)) if times else None, "earliest": timeutil.iso(min(times)) if times else None,
        "best_tier": min(tiers) if tiers else 4,
        "low_value": all(i["cls"]["low_value"] for i in cluster_items),
    }


def score(agg: dict, n_independent: int, has_official: bool, edition: str = "international", now=None) -> dict:
    st = config.settings()["editorial"]
    now = now or timeutil.now_utc()
    ts = agg["topic_score"]
    # 25 — significance
    sig = {1: 7, 2: 13, 3: 17, 4: 20}.get(n_independent, 22) + (3 if has_official else 0)
    sig = min(25, sig)
    # 20 — economic & financial impact
    econ_raw = ts.get("finance", 0) + ts.get("economics", 0) + 0.6 * ts.get("tax", 0)
    econ = min(20, econ_raw * 2.2)
    # 15 — political / regulatory
    pol_raw = ts.get("politics", 0) + ts.get("law", 0) + 0.6 * ts.get("tax", 0) + 0.3 * ts.get("society", 0)
    pol = min(15, pol_raw * 1.8)
    # 15 — geography
    if edition == "international":
        cw = st["country_weights"]
        geo = 15 * max((cw.get(c, 0.5) for c in agg["countries"]), default=0.5)
    else:
        geo = 15 if edition in agg["cities"] else 0
    # 10 — recency
    if agg["latest"]:
        age = timeutil.hours_between(timeutil.parse_iso(agg["latest"]), now)
        rec = 10 if age < 6 else 8 if age < 12 else 6 if age < 20 else 3 if age < 30 else 0
    else:
        rec = 0
    # 10 — source quality
    q = TIER_POINTS.get(agg["best_tier"], 4)
    # 5 — practical
    prac = min(5, agg["practical"] * 1.5)
    base = sig + econ + pol + geo + rec + q + prac
    # topic weighting & reader preferences
    tw = st["topic_weights"].get(agg["primary_topic"], 0.5) if agg["primary_topic"] else 0.4
    if agg["primary_topic"] in (st.get("followed_topics") or []):
        tw *= 1.1
    total = base * (0.55 + 0.45 * tw)
    if agg["low_value"]:
        total *= 0.3
    return {"total": round(total, 1), "significance": sig, "economic": round(econ, 1), "political": round(pol, 1),
            "geography": round(geo, 1), "recency": rec, "source_quality": q, "practical": round(prac, 1),
            "topic_weight": round(tw, 2)}
