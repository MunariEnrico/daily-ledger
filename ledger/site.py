"""Static site generator. The web edition is canonical; it is rebuilt from
data/ at every publication, so the archive, trackers and corrections always
reflect the stored record. Only *published* editions are rendered."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, store, textutil, timeutil

HERE = Path(__file__).parent
LANGS = ("it", "en")

T = {
    "front": {"en": "Front Page", "it": "Prima pagina"},
    "cities": {"en": "City Editions", "it": "Edizioni cittadine"},
    "archive": {"en": "Archive", "it": "Archivio"},
    "settings": {"en": "Settings", "it": "Impostazioni"},
    "big_picture": {"en": "The Big Picture", "it": "Il quadro generale"},
    "trackers": {"en": "Story trackers", "it": "Storie da seguire"},
    "about": {"en": "How we work", "it": "Come lavoriamo"},
    "status": {"en": "System status", "it": "Stato del sistema"},
    "morning_brief": {"en": "The Morning Brief", "it": "Il punto del mattino"},
    "five": {"en": "The five developments that matter", "it": "I cinque sviluppi che contano"},
    "for_markets": {"en": "For markets", "it": "Per i mercati"},
    "consequential": {"en": "Most consequential", "it": "Il più rilevante"},
    "households": {"en": "For households and taxpayers", "it": "Per famiglie e contribuenti"},
    "watch": {"en": "Watch in the next 72 hours", "it": "Da seguire nelle prossime 72 ore"},
    "day_ahead": {"en": "The day ahead", "it": "La giornata"},
    "official_calendar": {"en": "Official release calendars", "it": "Calendari ufficiali"},
    "editor_note": {"en": "Editor's note", "it": "Nota della redazione"},
    "markets": {"en": "Markets", "it": "Mercati"},
    "why": {"en": "Why it matters", "it": "Perché conta"},
    "next": {"en": "What happens next", "it": "Cosa succede ora"},
    "key_facts": {"en": "Key facts", "it": "I fatti"},
    "sources": {"en": "Sources", "it": "Fonti"},
    "evidence": {"en": "Evidence record", "it": "Registro delle prove"},
    "all_reports": {"en": "Other reports on this development", "it": "Altri articoli su questo sviluppo"},
    "read_more": {"en": "Full story and sources", "it": "Articolo completo e fonti"},
    "no_news": {"en": "No significant developments meeting today’s editorial and verification criteria.",
                "it": "Nessuno sviluppo significativo che soddisfi oggi i criteri editoriali e di verifica."},
    "sources_down": {"en": "Some local sources could not be reached this morning, so this edition may be incomplete:",
                     "it": "Alcune fonti locali non erano raggiungibili stamattina: questa edizione potrebbe essere incompleta:"},
    "nothing_for": {"en": "No verified story to point to today.", "it": "Nessuna notizia verificata da segnalare oggi."},
    "none_scheduled": {"en": "No verified upcoming milestones were found in today’s evidence.",
                       "it": "Nelle fonti di oggi non risultano scadenze future verificate."},
    "original_headline": {"en": "Original headline", "it": "Titolo originale"},
    "brief_note": {"en": "Shown as an attributed headline: the details could not be verified to our standard this morning.",
                   "it": "Riportato come titolo attribuito: stamattina non è stato possibile verificarne i dettagli secondo i nostri criteri."},
    "published": {"en": "Published", "it": "Pubblicato"},
    "retrieved": {"en": "retrieved", "it": "recuperato"},
    "edition_of": {"en": "Edition of", "it": "Edizione del"},
    "late": {"en": "This edition was published after 08:00 because the morning run finished late.",
             "it": "Questa edizione è uscita dopo le 08:00 perché l'elaborazione del mattino si è conclusa in ritardo."},
    "correction": {"en": "Correction", "it": "Rettifica"},
    "update_of": {"en": "Update to a story first reported on", "it": "Aggiornamento di una notizia pubblicata il"},
    "search_ph": {"en": "Search every edition", "it": "Cerca in tutte le edizioni"},
    "conflict": {"en": "Sources disagree", "it": "Le fonti non concordano"},
    "data_note": {"en": "Official sources only. Values are the latest observation with its own date; nothing is estimated.",
                  "it": "Solo fonti ufficiali. Ogni valore è l'ultima osservazione con la sua data; nulla è stimato."},
    "unavailable": {"en": "unavailable", "it": "non disponibile"},
    "stale": {"en": "last verified value", "it": "ultimo valore verificato"},
    "computed": {"en": "change computed from two provider observations", "it": "variazione calcolata da due osservazioni del fornitore"},
    "language": {"en": "Italiano", "it": "English"},
    "local_front": {"en": "Local front page", "it": "Prima pagina locale"},
    "priorities": {"en": "What this edition follows", "it": "Cosa segue questa edizione"},
    "outlook": {"en": "Outlook for the coming days", "it": "Le prossime giornate"},
    "no_tracker_news": {"en": "No new verified information was retrieved today.", "it": "Oggi non sono state recuperate nuove informazioni verificate."},
    "previous_update": {"en": "Previous update", "it": "Aggiornamento precedente"},
    "latest_dev": {"en": "Latest development", "it": "Ultimo sviluppo"},
    "next_milestone": {"en": "Next verified milestone", "it": "Prossima scadenza verificata"},
    "big_picture_empty": {"en": "No analysis today: the evidence did not support one to our standard.",
                          "it": "Nessuna analisi oggi: le fonti non consentivano un'analisi secondo i nostri criteri."},
}

LABELS = {
    "official_source": {"en": "Official source", "it": "Fonte ufficiale"},
    "corroborated": {"en": "Independently confirmed", "it": "Confermata da fonti indipendenti"},
    "single_source": {"en": "Single source, attributed", "it": "Fonte unica, attribuita"},
    "snippet_based": {"en": "From headlines and summaries", "it": "Da titoli e sommari"},
    "conflicting_reports": {"en": "Conflicting reports", "it": "Versioni discordanti"},
    "developing": {"en": "Developing story", "it": "Notizia in sviluppo"},
    "brief": {"en": "Headline only", "it": "Solo titolo"},
    "local_impact": {"en": "Local impact", "it": "Impatto locale"},
    "analysis": {"en": "Analysis", "it": "Analisi"},
    "corrected": {"en": "Corrected", "it": "Rettificata"},
}

LEGAL = {
    "proposed": {"en": "Proposal — not law", "it": "Proposta — non è legge"},
    "draft": {"en": "Draft — not yet adopted", "it": "Bozza — non ancora adottata"},
    "approved": {"en": "Approved", "it": "Approvata"},
    "published": {"en": "Published in official journal", "it": "Pubblicata in Gazzetta"},
    "in_force": {"en": "In force", "it": "In vigore"},
    "guidance": {"en": "Administrative guidance", "it": "Indicazione amministrativa"},
    "judgment": {"en": "Court decision", "it": "Decisione giudiziaria"},
    "unknown": {"en": "Legal status unclear from sources", "it": "Stato giuridico non chiaro dalle fonti"},
}

ACCESS = {
    "official_document": {"en": "official document read", "it": "documento ufficiale letto"},
    "full_text": {"en": "full text read", "it": "testo integrale letto"},
    "headline_snippet": {"en": "headline and summary only", "it": "solo titolo e sommario"},
    "headline_only": {"en": "headline only", "it": "solo titolo"},
}

TOPIC_NAMES = {
    "finance": {"en": "Finance & Markets", "it": "Finanza e mercati"}, "economics": {"en": "Economics", "it": "Economia"},
    "politics": {"en": "Politics", "it": "Politica"}, "society": {"en": "Society", "it": "Società"},
    "tax": {"en": "Tax & Fiscal Policy", "it": "Fisco"}, "law": {"en": "Law & Regulation", "it": "Diritto"},
    "crime": {"en": "Current Affairs", "it": "Cronaca"}, "culture": {"en": "Culture & Lifestyle", "it": "Cultura"},
    "travel": {"en": "Travel", "it": "Viaggi"},
}


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals.update(T=T, LABELS=LABELS, LEGAL=LEGAL, ACCESS=ACCESS, TOPIC_NAMES=TOPIC_NAMES)
    env.filters["when"] = lambda s, lang, t=True: timeutil.fmt_local(s, lang, t)
    env.filters["num"] = fmt_num
    env.filters["words"] = lambda s: textutil.word_count(s or "")
    return env


def fmt_num(v, lang: str, decimals: int = 2) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{decimals}f}"
    if lang == "it":
        s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return s


class Site:
    def __init__(self, out: Path | None = None):
        self.out = Path(out or os.environ.get("LEDGER_SITE", config.SITE_DIR))
        self.env = _env()
        self.st = config.settings()
        self.eds = config.editions()

    def _write(self, rel: str, html: str):
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")

    def render(self, tpl: str, rel: str, **ctx):
        depth = rel.count("/")
        ctx.setdefault("root", "../" * depth)
        ctx.setdefault("site", self.st["site"])
        ctx.setdefault("eds", self.eds)
        self._write(rel, self.env.get_template(tpl).render(**ctx))

    # ------------------------------------------------------------------
    def build(self) -> dict:
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        shutil.copytree(HERE / "static", self.out / "static")
        dates = store.published_dates()
        latest = dates[0] if dates else None
        search = []
        full_days = int(self.st["site"].get("full_render_editions", 90))
        for n, d in enumerate(dates):
            ed = store.load_edition(d)
            if not ed:
                continue
            if n < full_days:
                self.render_edition(ed, dates)
                search += search_entries(ed)
            else:
                # older editions: one compact page per language keeps the site
                # well under GitHub Pages' 1 GB limit while preserving every story
                for lang in LANGS:
                    self.render("compact.html", f"{d}/{lang}/index.html", page="archive", lang=lang, ed=ed,
                                S=ed["stories"], date=d, dates=dates, latest=dates[0])
                search += [dict(e, c=1) for e in search_entries(ed)]
        trackers = store.read_json(store.data_dir() / "trackers.json", {"trackers": {}})
        status = store.read_json(store.data_dir() / "status.json", {})
        for lang in LANGS:
            base = dict(lang=lang, latest=latest, dates=dates)
            self.render("archive.html", f"{lang}/archive.html", page="archive", **base)
            self.render("trackers.html", f"{lang}/trackers.html", page="trackers", trackers=trackers["trackers"],
                        tracker_defs=self.eds.get("trackers", []), **base)
            self.render("settings.html", f"{lang}/settings.html", page="settings", settings=public_settings(), **base)
            self.render("about.html", f"{lang}/about.html", page="about", sources=list(config.sources().values()), **base)
        self.render("status.html", "status.html", lang="en", page="status", status=status, latest=latest, dates=dates,
                    index=store.index()["editions"])
        self.render("root.html", "index.html", lang=self.st["site"].get("default_language", "it"), latest=latest, dates=dates)
        (self.out / "search-index.json").write_text(json.dumps(search, ensure_ascii=False), encoding="utf-8")
        (self.out / "latest.json").write_text(json.dumps({"date": latest}), encoding="utf-8")
        (self.out / ".nojekyll").write_text("")
        return {"editions": len(dates), "latest": latest, "search_entries": len(search)}

    def render_edition(self, ed: dict, dates: list[str]):
        d = ed["date"]
        S = ed["stories"]
        for lang in LANGS:
            base = dict(lang=lang, ed=ed, S=S, date=d, dates=dates, latest=dates[0] if dates else d)
            self.render("front.html", f"{d}/{lang}/index.html", page="front", **base)
            for sec in self.eds["international"]["sections"]:
                self.render("section.html", f"{d}/{lang}/{sec['id']}.html", page=sec["id"], sec=sec,
                            ids=ed["international"]["sections"].get(sec["id"], []), **base)
            self.render("bigpicture.html", f"{d}/{lang}/big-picture.html", page="big_picture",
                        ids=ed["international"].get("big_picture", []), **base)
            for city, cfg in self.eds["cities"].items():
                self.render("city.html", f"{d}/{lang}/city-{city}.html", page="city-" + city, city=city, ccfg=cfg,
                            cdata=ed["cities"].get(city, {"front": [], "sections": {}, "sources_failed": []}), **base)
            for sid, s in S.items():
                self.render("story.html", f"{d}/{lang}/s/{sid}.html", page="story", s=s, **base)


def search_entries(ed: dict) -> list[dict]:
    out = []
    for s in ed["stories"].values():
        out.append({
            "id": s["id"], "date": ed["date"], "kind": s["kind"],
            "h": {l: s["content"][l]["headline"] for l in ("en", "it")},
            "sub": {l: s["content"][l].get("subheadline", "") for l in ("en", "it")},
            "topic": s.get("primary_topic"), "countries": s.get("countries", [])[:4], "cities": s.get("cities", []),
            "sources": sorted({e["source_name"] for e in s.get("evidence", [])})[:6],
            "v": s.get("verification"), "n": s.get("n_independent", 1),
        })
    return out


def public_settings() -> dict:
    """Settings safe to show publicly (no secrets, no addresses)."""
    st = config.settings()
    return {"publication": st["publication"], "editorial": {k: v for k, v in st["editorial"].items()},
            "verification": st["verification"], "market": st["market"],
            "llm": {"provider": st["llm"]["provider"], "models": st["llm"]["models"],
                    "max_calls_per_run": st["llm"]["max_calls_per_run"]},
            "email_enabled": st["email"]["enabled"], "channels": st["delivery"]["channels"],
            "default_language": st["site"]["default_language"]}
