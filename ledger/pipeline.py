"""The daily run: retrieve → normalise → cluster → rank → verify & write →
assemble 16 logical editions → stage → publish at 08:00 Europe/Luxembourg."""
from __future__ import annotations

import concurrent.futures as cf
import logging
import time
import traceback
from collections import Counter
from datetime import timedelta

from . import cluster as clmod, classify, config, evidence as evmod, ingest, rank, store, textutil, timeutil
from .llm import LLMClient
from .market import Market
from .writer import Writer

log = logging.getLogger("ledger.pipeline")

CITY_SECTION_FOR_TOPIC = {"politics": "government", "economics": "economy", "finance": "economy", "tax": "tax",
                          "law": "tax", "society": "society", "crime": "crime", "culture": "culture", "travel": "events"}


# ───────────────────────────────────────────── helpers

def _city_section(c: dict) -> str | None:
    subs = c["agg"]["subtopics"]
    if "housing" in subs:
        return "housing"
    if "transport" in subs and c["agg"]["primary_topic"] not in ("crime",):
        return "transport"
    if "events" in subs and c["agg"]["primary_topic"] in ("culture", "travel", None):
        return "events"
    return CITY_SECTION_FOR_TOPIC.get(c["agg"]["primary_topic"])


def _theme_key(c: dict) -> set[str]:
    return clmod.anchors(" . ".join(i["title"] for i in c["items"][:5]), include_initial=True)


def _same_theme(a: set, b: set) -> bool:
    inter = a & b
    return len(inter) >= 2 and len(inter) / max(1, min(len(a), len(b))) >= 0.5


def _pick(pool: list[dict], key: str, n: int, theme_dedupe: bool = True, prefer_corroborated: bool = False,
          max_per_country: int | None = None) -> list[dict]:
    pool = sorted(pool, key=lambda c: (-(1 if prefer_corroborated and (c["n_independent"] >= 2 or c["official"]) else 0),
                                       -c["scores"][key]["total"]))
    out, themes, per_country = [], [], {}
    skipped = []
    for c in pool:
        if len(out) >= n:
            break
        t = c["theme"]
        if theme_dedupe and any(_same_theme(t, x) for x in themes):
            continue
        lead_country = (c.get("agg", {}).get("countries") or ["INT"])[0]
        if max_per_country and per_country.get(lead_country, 0) >= max_per_country:
            skipped.append(c)  # diversity: keep for later only if nothing else qualifies
            continue
        out.append(c)
        themes.append(t)
        per_country[lead_country] = per_country.get(lead_country, 0) + 1
    for c in skipped:
        if len(out) >= n:
            break
        out.append(c)
    return out


# ───────────────────────────────────────────── stages

def collect(now, items_override=None):
    srcs = [s for s in config.sources().values() if s.enabled]
    if items_override is not None:
        results, raw = items_override
    else:
        results, raw = ingest.fetch_all(srcs)
    raw = ingest.merge_duplicates(raw)
    return results, raw


def prepare(raw: list, now) -> tuple[list[dict], list[dict], Counter]:
    st = config.settings()["editorial"]
    srcs = config.sources()
    excluded = set(st.get("excluded_topics") or [])
    drop = Counter()
    news, calendar = [], []
    for it in raw:
        d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
        src = srcs.get(d["source_id"])
        if not src:
            drop["unknown_source"] += 1
            continue
        if src.calendar:
            calendar.append(d)
            continue
        if not d.get("published"):
            drop[f"date_{d.get('date_quality')}"] += 1  # undated/ambiguous items cannot prove they are news today
            continue
        age = timeutil.hours_between(timeutil.parse_iso(d["published"]), now)
        if age > st["lookback_hours"] or age < -14:
            drop["too_old"] += 1
            continue
        d["cls"] = classify.classify_text(d["title"], d["summary"], src)
        if d["cls"]["low_value"]:
            drop["low_value"] += 1
            continue
        if d["cls"]["opinion"]:
            drop["opinion"] += 1
            continue
        if d["cls"]["primary_topic"] in excluded:
            drop["excluded_topic"] += 1
            continue
        d["_tier"] = src.tier
        news.append(d)
    return news, calendar, drop


def build_candidates(news: list[dict], now) -> list[dict]:
    st = config.settings()["editorial"]
    srcs = config.sources()
    prefs = st.get("source_preferences") or {}
    groups = clmod.cluster_items(news)
    cities = list(config.editions()["cities"])
    cands = []
    for g in groups:
        items = [news[i] for i in g]
        agg = rank.aggregate(items)
        if not agg["primary_topic"]:
            continue
        status, n, origins = evmod.verification_status(items)
        official = status.startswith("official")
        mult = max((prefs.get(i["source_id"], 1.0) for i in items), default=1.0)
        scores = {"international": rank.score(agg, n, official, "international", now)}
        for c in agg["cities"]:
            if c in cities:
                scores[c] = rank.score(agg, n, official, c, now)
        for s in scores.values():
            s["total"] = round(s["total"] * mult, 1)
        cid = clmod.cluster_id([i["id"] for i in items])
        cands.append({"cluster_id": cid, "items": items, "agg": agg, "verification": status, "n_independent": n,
                      "origins": origins, "official": official, "scores": scores, "theme": None})
    for c in cands:
        c["theme"] = _theme_key(c)
    return cands


def plan(cands: list[dict]) -> dict:
    st = config.settings()["editorial"]
    eds = config.editions()
    minimum = st["min_score"]
    pool = [c for c in cands if c["scores"]["international"]["total"] >= minimum]
    plan_ = {"international": {"front": [], "sections": {}}, "cities": {}}
    plan_["international"]["front"] = [c["cluster_id"] for c in
                                       _pick(pool, "international", st["front_page_stories"], prefer_corroborated=True,
                                             max_per_country=3)]
    for sec in eds["international"]["sections"]:
        sp = [c for c in pool if c["agg"]["primary_topic"] in sec["topics"]]
        plan_["international"]["sections"][sec["id"]] = [c["cluster_id"] for c in _pick(sp, "international", st["section_stories"])]
    for city in st["city_order"]:
        cpool = [c for c in cands if city in c["scores"] and c["scores"][city]["total"] >= minimum * 0.8]
        front = _pick(cpool, city, 5, prefer_corroborated=False)
        secs = {}
        for sec in eds["city_sections"]:
            sp = [c for c in cpool if _city_section(c) == sec["id"]]
            secs[sec["id"]] = [c["cluster_id"] for c in _pick(sp, city, st["city_section_stories"])]
        plan_["cities"][city] = {"front": [c["cluster_id"] for c in front], "sections": secs}
    return plan_


def writing_order(plan_: dict, by_id: dict) -> list[str]:
    tiers = [plan_["international"]["front"]]
    tiers += [plan_["cities"][c]["front"] for c in plan_["cities"]]
    tiers += list(plan_["international"]["sections"].values())
    tiers += [ids for c in plan_["cities"].values() for ids in c["sections"].values()]
    seen, order = set(), []
    for tier in tiers:
        for cid in sorted(tier, key=lambda x: -max(s["total"] for s in by_id[x]["scores"].values())):
            if cid not in seen:
                seen.add(cid)
                order.append(cid)
    return order


def find_update(c: dict, previous: list[dict]) -> dict | None:
    """Does this cluster continue a story published in the last days?"""
    t = c["theme"]
    for s in previous:
        if s.get("kind") not in ("article", "brief"):
            continue
        prev_t = clmod.anchors(" . ".join(e["title"] for e in s.get("evidence", [])[:5]), include_initial=True)
        new_urls = {i["url"] for i in c["items"]} - {e["url"] for e in s.get("evidence", [])}
        if _same_theme(t, prev_t) and new_urls:
            return {"story_id": s["id"], "date": s["date"],
                    "headline": {l: s["content"][l]["headline"] for l in ("en", "it")}}
    return None


def check_urls(evidence: list[dict], http: ingest.Http, timeout: float) -> list[dict]:
    kept = []
    for e in evidence:
        ok, code = http.head_ok(e["url"], timeout=timeout)
        e["url_check"] = {"ok": ok, "code": code, "at": timeutil.iso(timeutil.now_utc())}
        if ok is False:
            continue  # 404/410/DNS: the source does not exist (any more) → not evidence
        kept.append(e)
    return kept


def domain_ok(e: dict) -> bool:
    """URL must be a well-formed http(s) link from a registered source. A host
    outside the source's own domains is kept (feeds sometimes link partners)
    but flagged in the evidence record."""
    src = config.sources().get(e["source_id"])
    host = ingest.urlparse(e["url"]).netloc
    if not src or not host or not e["url"].startswith(("http://", "https://")):
        return False
    e["domain_mismatch"] = config.registrable(host) not in src.allowed_hosts()
    return True


def prepare_story_inputs(c: dict, previous, http, st) -> dict | None:
    ev = evmod.build_evidence(c["items"])
    ev = [e for e in ev if domain_ok(e)]
    if st["verification"].get("check_urls_live", True):
        ev = check_urls(ev, http, st["verification"].get("url_check_timeout_s", 8))
    if not ev:
        return None
    # official documents: fetch the public page so claims can be checked against it
    items_by_id = {i["id"]: i for i in c["items"]}
    officials = [items_by_id[e["item_id"]] for e in ev if e["official"] and not items_by_id[e["item_id"]].get("text")]
    if officials:
        objs = [ingest.Item(**{k: v for k, v in o.items() if k in ingest.Item.__dataclass_fields__}) for o in officials]
        ingest.enrich_official(objs, http)
        for o, obj in zip(officials, objs):
            if obj.text:
                o["text"], o["access_level"] = obj.text, obj.access_level
                for e in ev:
                    if e["item_id"] == o["id"]:
                        e["passage"], e["access_level"] = evmod.passage(o), obj.access_level
    meta = {"cluster_id": c["cluster_id"], "primary_topic": c["agg"]["primary_topic"], "topics": c["agg"]["topics"],
            "subtopics": c["agg"]["subtopics"], "countries": c["agg"]["countries"], "cities": c["agg"]["cities"],
            "verification": c["verification"], "n_independent": c["n_independent"], "origins": c["origins"],
            "conflicts": evmod.detect_conflicts(c["items"]), "updates": find_update(c, previous)}
    all_reports = [{"source_name": i["source_name"], "title": i["title"], "url": i["url"], "published": i.get("published"),
                    "origin": i.get("agency") or i["owner"]} for i in c["items"][:20]]
    return {"cluster_id": c["cluster_id"], "meta": meta, "evidence": ev,
            "score": max(s["total"] for s in c["scores"].values()), "score_breakdown": c["scores"],
            "all_reports": all_reports}


def big_picture_themes(cands: list[dict], st) -> list[dict]:
    pool = [c for c in cands if c["n_independent"] >= 3 and c["agg"]["primary_topic"] in
            ("finance", "economics", "politics", "tax", "society")]
    pool.sort(key=lambda c: (-c["n_independent"], -c["scores"]["international"]["total"]))
    out = []
    for c in _pick(pool, "international", st["editorial"]["big_picture_max"]):
        ev = evmod.build_evidence(c["items"], max_items=10)
        out.append({"theme_id": c["cluster_id"], "label": c["items"][0]["title"], "evidence": ev,
                    "score": c["scores"]["international"]["total"], "related": [c["cluster_id"]],
                    "meta": {"primary_topic": c["agg"]["primary_topic"], "topics": c["agg"]["topics"],
                             "countries": c["agg"]["countries"], "verification": c["verification"],
                             "n_independent": c["n_independent"], "conflicts": evmod.detect_conflicts(c["items"]),
                             "cluster_id": "bp-" + c["cluster_id"]}})
    return out


# ───────────────────────────────────────────── assembly


def _order_ids(cids, story_by_cluster, stories):
    ids = [story_by_cluster[c] for c in cids if c in story_by_cluster]
    return sorted(ids, key=lambda i: (stories[i]["kind"] != "article", -stories[i]["score"]))


def assemble(date_str, plan_, story_by_cluster, stories, analyses, feed_results, calendar, market, run) -> dict:
    eds = config.editions()
    srcs = config.sources()
    failed = {r["source_id"] for r in feed_results if not r["ok"]}
    intl = {"front": [], "sections": {}}
    front = _order_ids(plan_["international"]["front"], story_by_cluster, stories)
    if len([i for i in front if stories[i]["kind"] == "article"]) < 5:
        # promote further verified articles from the sections, if any
        extra = [i for ids in plan_["international"]["sections"].values() for i in _order_ids(ids, story_by_cluster, stories)
                 if stories[i]["kind"] == "article" and i not in front]
        extra.sort(key=lambda i: -stories[i]["score"])
        arts = [i for i in front if stories[i]["kind"] == "article"] + extra
        briefs = [i for i in front if stories[i]["kind"] != "article"]
        front = (arts + briefs)[:5]  # verified articles first, attributed briefs fill the remaining slots
    intl["front"] = front[:5]
    for sec, cids in plan_["international"]["sections"].items():
        intl["sections"][sec] = _order_ids(cids, story_by_cluster, stories)
    intl["big_picture"] = [a["id"] for a in analyses]
    cities = {}
    for city, p in plan_["cities"].items():
        city_srcs = [s.id for s in srcs.values() if city in s.cities]
        down = sorted(set(city_srcs) & failed)
        cities[city] = {"front": _order_ids(p["front"], story_by_cluster, stories),
                        "sections": {k: _order_ids(v, story_by_cluster, stories) for k, v in p["sections"].items()},
                        "sources_total": len(city_srcs), "sources_failed": down}
    edition = {
        "date": date_str, "status": "staged", "built_at": timeutil.iso(timeutil.now_utc()),
        "publish_at": timeutil.iso(timeutil.publish_at(timeutil.parse_iso(date_str + "T12:00:00+00:00").date())),
        "stories": stories, "international": intl, "cities": cities, "market": market,
        "day_ahead": day_ahead(calendar, stories, date_str), "run": run,
    }
    edition["morning_brief"] = morning_brief(edition)
    edition["editor_note"] = editor_note(edition)
    edition["headline"] = {l: (stories[intl["front"][0]]["content"][l]["headline"] if intl["front"] else "")
                           for l in ("en", "it")}
    return edition


def day_ahead(calendar: list[dict], stories: dict, date_str: str) -> dict:
    now = timeutil.now_utc()
    out = {"official_calendar": [], "from_stories": []}
    for c in calendar:
        p = timeutil.parse_iso(c.get("published"))
        if p and now - timedelta(hours=2) <= p <= now + timedelta(hours=72):
            out["official_calendar"].append({"when": c["published"], "title": c["title"], "source": c["source_name"],
                                             "url": c["url"]})
    out["official_calendar"].sort(key=lambda x: x["when"])
    for s in stories.values():
        if s["kind"] != "article":
            continue
        en, it = s["content"]["en"].get("what_next") or [], s["content"]["it"].get("what_next") or []
        for a, b in zip(en, it):
            out["from_stories"].append({"story_id": s["id"], "text": {"en": a["text"], "it": b["text"]}})
    return out


def morning_brief(ed: dict) -> dict:
    S = ed["stories"]
    arts = sorted((s for s in S.values() if s["kind"] == "article"), key=lambda s: -s["score"])

    def first(pred):
        return next((s["id"] for s in arts if pred(s)), None)

    return {
        "top": ed["international"]["front"][:5],
        "markets": first(lambda s: s["primary_topic"] == "finance"),
        "consequential": first(lambda s: s["primary_topic"] in ("politics", "economics") and s["n_independent"] >= 2)
        or first(lambda s: s["primary_topic"] in ("politics", "economics")),
        "households": first(lambda s: s["primary_topic"] in ("tax", "society") or "housing" in s.get("subtopics", [])),
        "watch": [x for x in ed["day_ahead"]["from_stories"][:4]],
        "calendar": ed["day_ahead"]["official_calendar"][:5],
    }


def editor_note(ed: dict) -> dict:
    r = ed["run"]
    S = ed["stories"]
    n_art = sum(1 for s in S.values() if s["kind"] == "article")
    n_brief = sum(1 for s in S.values() if s["kind"] == "brief")
    multi = sum(1 for s in S.values() if s["kind"] == "article" and s["n_independent"] >= 2)
    off = sum(1 for s in S.values() if s["kind"] == "article" and str(s["verification"]).startswith("official"))
    failed = r["sources"]["failed"]
    en = (f"This edition draws on {r['items']['fresh']:,} reports published in the past {r['lookback_hours']} hours by "
          f"{r['sources']['ok']} of {r['sources']['checked']} monitored sources, grouped into {r['clusters']:,} distinct developments. "
          f"{n_art} stories passed every evidence check ({multi} confirmed by two or more independent outlets, {off} backed by official sources); "
          f"{n_brief} more appear only as attributed headlines because their details could not be verified to our standard.")
    it_n = lambda x: f"{x:,}".replace(",", ".")  # noqa: E731
    it = (f"Questa edizione si basa su {it_n(r['items']['fresh'])} notizie pubblicate nelle ultime {r['lookback_hours']} ore da "
          f"{r['sources']['ok']} delle {r['sources']['checked']} fonti monitorate, raggruppate in {it_n(r['clusters'])} sviluppi distinti. "
          f"{n_art} articoli hanno superato tutti i controlli sulle fonti ({multi} confermati da almeno due testate indipendenti, {off} basati su fonti ufficiali); "
          f"altri {n_brief} compaiono solo come titoli attribuiti perché i dettagli non erano verificabili secondo i nostri criteri.")
    if failed:
        en += f" Sources unavailable this morning: {', '.join(failed[:8])}{'…' if len(failed) > 8 else ''}."
        it += f" Fonti non raggiungibili stamattina: {', '.join(failed[:8])}{'…' if len(failed) > 8 else ''}."
    if r["llm"].get("calls", 0) == 0 or r["llm_status"] != "ok":
        en += " Automated drafting was unavailable or limited today; affected stories are shown as headline briefs."
        it += " La redazione automatica oggi non era disponibile o era limitata; le notizie interessate compaiono come titoli."
    return {"en": en, "it": it}


def update_trackers(ed: dict) -> dict:
    data = store.read_json(store.data_dir() / "trackers.json", {"trackers": {}})
    defs = config.editions().get("trackers", [])
    today = ed["date"]
    for t in defs:
        rec = data["trackers"].setdefault(t["id"], {"label": t["label"], "entries": []})
        rec["label"] = t["label"]
        matches = []
        for s in ed["stories"].values():
            if s["kind"] not in ("article",):
                continue
            text = textutil.fold(" ".join(s["content"]["en"].get("headline", "") for _ in [0]) + " " +
                                 " ".join(e["title"] for e in s.get("evidence", [])))
            if not textutil.has_term(text, t.get("any", [])):
                continue
            if t.get("all_topics") and not set(t["all_topics"]) & set(s.get("topics", []) + [s.get("primary_topic")]):
                continue
            if t.get("cities") and not set(t["cities"]) & set(s.get("cities", [])):
                continue
            matches.append(s)
        rec["entries"] = [e for e in rec["entries"] if e["date"] != today]
        if matches:
            s = max(matches, key=lambda x: x["score"])
            rec["entries"].append({"date": today, "story_id": s["id"],
                                   "headline": {l: s["content"][l]["headline"] for l in ("en", "it")},
                                   "subheadline": {l: s["content"][l].get("subheadline", "") for l in ("en", "it")},
                                   "next": {l: [p["text"] for p in (s["content"][l].get("what_next") or [])] for l in ("en", "it")},
                                   "sources": [{"name": e["source_name"], "url": e["url"]} for e in s.get("evidence", [])][:6]})
        rec["entries"] = sorted(rec["entries"], key=lambda e: e["date"])[-60:]
        rec["checked"] = today
    store.write_json(store.data_dir() / "trackers.json", data)
    return data


# ───────────────────────────────────────────── commands


def build(date_str: str | None = None, llm=None, items_override=None, market_override=None, force: bool = False) -> dict:
    """Retrieve, verify, write and stage today's edition (status 'staged')."""
    t0 = time.time()
    st = config.settings()
    now = timeutil.now_utc()
    date_str = date_str or timeutil.today_local().isoformat()
    existing = store.index()["editions"].get(date_str, {})
    if existing.get("status") == "published" and not force:
        log.info("edition %s already published — nothing to do (use --force to rebuild as a new version)", date_str)
        return {"skipped": True, "reason": "already published"}
    store.set_index(date_str, status="building", started_at=timeutil.iso(now))
    errors: list[str] = []
    audit: list[dict] = []
    # 1 retrieval
    results, raw = collect(now, items_override)
    res_dicts = [r.__dict__ if hasattr(r, "__dict__") else r for r in results]
    store.write_json(store.edition_dir(date_str) / "sources.json", res_dicts)
    # 2 normalise / filter
    news, calendar, dropped = prepare(raw, now)
    # 3–4 cluster & rank
    cands = build_candidates(news, now)
    by_id = {c["cluster_id"]: c for c in cands}
    plan_ = plan(cands)
    order = writing_order(plan_, by_id)
    # 5 verify & write
    llm = llm if llm is not None else LLMClient()
    writer = Writer(llm, audit)
    http = ingest.Http(timeout=12, retries=1, per_host_delay=0.2)
    previous = store.previous_stories(date_str)
    inputs: dict[str, dict] = {}

    def prep(cid):
        try:
            return cid, prepare_story_inputs(by_id[cid], previous, http, st)
        except Exception as ex:  # one bad cluster never stops the edition
            errors.append(f"prepare {cid}: {type(ex).__name__}: {ex}")
            return cid, None

    with cf.ThreadPoolExecutor(8) as ex:
        for cid, inp in ex.map(prep, order):
            if inp:
                inputs[cid] = inp
    stories, story_by_cluster = {}, {}

    def write(cid):
        try:
            return cid, writer.write_story(inputs[cid], date_str)
        except Exception as ex:
            errors.append(f"write {cid}: {type(ex).__name__}: {ex}\n{traceback.format_exc(limit=2)}")
            return cid, None

    ordered_inputs = [cid for cid in order if cid in inputs]
    with cf.ThreadPoolExecutor(int(st["llm"].get("max_parallel", 3))) as ex:
        for cid, story in ex.map(write, ordered_inputs):
            if story:
                stories[story["id"]] = story
                story_by_cluster[cid] = story["id"]
    # 6 Big Picture
    analyses = []
    for theme in big_picture_themes(cands, st):
        try:
            a = writer.write_analysis(theme, date_str)
        except Exception as ex_:
            errors.append(f"analysis: {ex_}")
            a = None
        if a:
            stories[a["id"]] = a
            analyses.append(a)
    # 7 market
    if market_override is not None:
        market = market_override
    elif st["market"].get("enabled", True):
        try:
            market = Market().snapshot(timeutil.parse_iso(date_str + "T12:00:00+00:00").date())
        except Exception as ex_:
            market = {"rows": [], "errors": [str(ex_)], "summary": {"en": [], "it": []}}
    else:
        market = {"rows": [], "errors": ["disabled"], "summary": {"en": [], "it": []}}
    # 8 assemble
    ok_sources = [r for r in res_dicts if r["ok"]]
    llm_stats = llm.stats() if hasattr(llm, "stats") else {}
    llm_status = "ok" if getattr(llm, "available", False) and writer.metrics["llm_errors"] == 0 else (
        "unavailable" if not getattr(llm, "available", False) else "degraded")
    run = {
        "started_at": timeutil.iso(now), "finished_at": None, "seconds": None,
        "sources": {"checked": len(res_dicts), "ok": len(ok_sources),
                    "failed": [r["source_id"] for r in res_dicts if not r["ok"]],
                    "failures": {r["source_id"]: r["error"] for r in res_dicts if not r["ok"]}},
        "items": {"retrieved": len(raw), "fresh": len(news), "dropped": dict(dropped), "calendar": len(calendar)},
        "lookback_hours": st["editorial"]["lookback_hours"], "clusters": len(cands),
        "candidates_planned": len(order), "candidates_with_evidence": len(inputs),
        "stories": {"articles": sum(1 for s in stories.values() if s["kind"] == "article"),
                    "briefs": sum(1 for s in stories.values() if s["kind"] == "brief"),
                    "analyses": len(analyses)},
        "verification": writer.metrics, "llm": llm_stats, "llm_status": llm_status,
        "market_status": ("ok" if market.get("rows") and not market.get("errors") else "partial" if market.get("rows") else "failed"),
        "market_errors": market.get("errors", []), "errors": errors,
    }
    edition = assemble(date_str, plan_, story_by_cluster, stories, analyses, res_dicts, calendar, market, run)
    run["finished_at"] = timeutil.iso(timeutil.now_utc())
    run["seconds"] = round(time.time() - t0, 1)
    edition["editions_status"] = {"en": "ready" if stories else "empty", "it": "ready" if stories else "empty"}
    # keep any earlier version of this date (e.g. a forced rebuild) in the record
    old = store.load_edition(date_str)
    if old and old.get("status") == "published":
        edition["previous_versions"] = (old.get("previous_versions") or []) + [
            {"built_at": old.get("built_at"), "published_at": old.get("published_at"),
             "story_ids": list(old.get("stories", {}))}]
    store.save_edition(date_str, edition)
    save_state(date_str, {"plan": plan_, "inputs": inputs, "story_by_cluster": story_by_cluster, "calendar": calendar,
                          "feed_results": res_dicts, "themes": [t["theme_id"] for t in big_picture_themes(cands, st)]})
    store.write_audit(date_str, audit)
    store.write_json(store.edition_dir(date_str) / "run.json", run)
    store.set_index(date_str, status="staged", built_at=edition["built_at"],
                    articles=run["stories"]["articles"], briefs=run["stories"]["briefs"])
    _write_status(run, date_str, "staged")
    return {"date": date_str, "run": run}


def _write_status(run: dict, date_str: str, state: str, extra: dict | None = None):
    status = store.read_json(store.data_dir() / "status.json", {})
    status.update({"date": date_str, "state": state, "run": run, "updated_at": timeutil.iso(timeutil.now_utc())})
    if extra:
        status.update(extra)
    store.write_json(store.data_dir() / "status.json", status)


def publish(date_str: str | None = None, late: bool = False) -> dict:
    """Flip a staged edition to published (idempotent), update trackers."""
    date_str = date_str or timeutil.today_local().isoformat()
    ed = store.load_edition(date_str)
    if not ed:
        return {"published": False, "reason": "no staged edition"}
    if ed.get("status") == "published":
        return {"published": False, "reason": "already published", "date": date_str}
    now = timeutil.now_utc()
    ed["status"] = "published"
    ed["published_at"] = timeutil.iso(now)
    ed["late"] = late or now > timeutil.parse_iso(ed["publish_at"]) + timedelta(minutes=15)
    for s in ed["stories"].values():
        s.setdefault("published_at", ed["published_at"])
    store.save_edition(date_str, ed)
    update_trackers(ed)
    store.set_index(date_str, status="published", published_at=ed["published_at"], late=ed["late"])
    _write_status(ed["run"], date_str, "published", {"last_published": ed["published_at"], "late": ed["late"]})
    return {"published": True, "date": date_str, "late": ed["late"]}


def decide(now_local, state: str | None, grace_min: int) -> str:
    """Scheduler decision table (pure, unit-tested):
    returns one of build, build+publish, publish, wait+publish, nothing."""
    pub = now_local.replace(hour=int(config.settings()["publication"]["publish_time"][:2]),
                            minute=int(config.settings()["publication"]["publish_time"][3:]), second=0, microsecond=0)
    if state == "published":
        return "nothing"
    if state in (None, "", "building", "failed"):
        return "build+publish" if now_local >= pub else "build"
    if state == "staged":
        if now_local >= pub:
            return "publish"
        if (pub - now_local) <= timedelta(minutes=grace_min):
            return "wait+publish"
        return "nothing"
    return "nothing"


# ───────────────────────────────────────────── recovery: redraft from stored evidence

def save_state(date_str: str, state: dict) -> None:
    import gzip
    import json
    with gzip.open(store.edition_dir(date_str) / "state.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, default=str)


def load_state(date_str: str) -> dict | None:
    import gzip
    import json
    p = store.edition_dir(date_str) / "state.json.gz"
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def redraft(date_str: str, llm=None, only_briefs: bool = True, force: bool = False) -> dict:
    """Re-run drafting for an already built edition using the evidence stored at
    build time — no re-fetching. Used when the AI service failed or ran out of
    quota during the morning run (each story and each language is retried
    independently). A published edition is only changed with force=True, and
    every replaced story keeps its previous version."""
    ed = store.load_edition(date_str)
    state = load_state(date_str)
    if not ed or not state:
        return {"redrafted": 0, "reason": "no stored build state"}
    if ed.get("status") == "published" and not force:
        return {"redrafted": 0, "reason": "edition already published (use --force to add new versions)"}
    llm = llm if llm is not None else LLMClient()
    audit: list[dict] = []
    writer = Writer(llm, audit)
    changed = 0
    # highest-priority stories first (same order as the morning run), so a
    # limited AI quota is spent where it matters most
    ordered = sorted(state["story_by_cluster"].items(), key=lambda kv: -state["inputs"].get(kv[0], {}).get("score", 0))
    for cid, sid in ordered:
        old = ed["stories"].get(sid)
        if not old or (only_briefs and old["kind"] != "brief") or cid not in state["inputs"]:
            continue
        if getattr(llm, "circuit_open", False) or (hasattr(llm, "calls") and hasattr(llm, "max_calls") and llm.calls >= llm.max_calls):
            break
        new = writer.write_story(state["inputs"][cid], date_str)
        if new["kind"] == "article" or not only_briefs:
            if ed.get("status") == "published":
                new["versions"] = old.get("versions", []) + [{"replaced_at": timeutil.iso(timeutil.now_utc()),
                                                              "reason": "expanded after verification", "content": old["content"]}]
                new["published_at"] = old.get("published_at")
            ed["stories"][sid] = new
            changed += 1
    if changed or force:
        run = ed["run"]
        run["stories"]["articles"] = sum(1 for s in ed["stories"].values() if s["kind"] == "article")
        run["stories"]["briefs"] = sum(1 for s in ed["stories"].values() if s["kind"] == "brief")
        for k, v in writer.metrics.items():
            run["verification"][k] = run["verification"].get(k, 0) + v
        run["llm_status"] = "ok" if writer.metrics["llm_errors"] == 0 else "degraded"
        rebuilt = assemble(date_str, state["plan"], state["story_by_cluster"], ed["stories"],
                           [s for s in ed["stories"].values() if s["kind"] == "analysis"], state["feed_results"],
                           state["calendar"], ed["market"], run)
        for k in ("status", "published_at", "late", "built_at", "previous_versions"):
            if k in ed:
                rebuilt[k] = ed[k]
        store.save_edition(date_str, rebuilt)
        store.write_audit(date_str, audit)
        store.set_index(date_str, articles=run["stories"]["articles"], briefs=run["stories"]["briefs"])
    return {"redrafted": changed, "llm": llm.stats() if hasattr(llm, "stats") else {}}
