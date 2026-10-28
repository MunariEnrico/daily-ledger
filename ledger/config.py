"""Configuration loading. All YAML lives in ./config; secrets only in env vars."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("LEDGER_ROOT", Path(__file__).resolve().parent.parent))
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("LEDGER_DATA", ROOT / "data"))
SITE_DIR = Path(os.environ.get("LEDGER_SITE", ROOT / "_site"))

USER_AGENT = ("Mozilla/5.0 (compatible; TheDailyLedger/1.0; personal newspaper feed reader"
              + (f"; +{os.environ['LEDGER_CONTACT_URL']}" if os.environ.get("LEDGER_CONTACT_URL") else "") + ")")


def _load(name: str) -> dict:
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=None)
def settings() -> dict:
    return _load("settings")


@lru_cache(maxsize=None)
def editions() -> dict:
    return _load("editions")


@dataclass
class Source:
    id: str
    name: str
    feed: str
    home: str
    country: str
    lang: str
    type: str
    tier: int
    owner: str
    geo: list = field(default_factory=list)
    cities: list = field(default_factory=list)
    topics: list = field(default_factory=list)
    paywall: bool = False
    fulltext: bool = False
    enabled: bool = True
    calendar: bool = False
    notes: str = ""

    @property
    def official(self) -> bool:
        return self.type == "official"

    @property
    def domain(self) -> str:
        m = re.match(r"https?://([^/]+)", self.home)
        host = (m.group(1) if m else "").lower()
        return host[4:] if host.startswith("www.") else host

    def allowed_hosts(self) -> set[str]:
        """Registrable hosts an item URL from this source may legitimately use."""
        hosts = set()
        for u in (self.home, self.feed):
            m = re.match(r"https?://([^/]+)", u)
            if m:
                h = m.group(1).lower()
                h = h[4:] if h.startswith("www.") else h
                hosts.add(_registrable(h))
        return hosts


def _registrable(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "gov", "org", "ac"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def registrable(host: str) -> str:
    host = host.lower()
    host = host[4:] if host.startswith("www.") else host
    return _registrable(host)


@lru_cache(maxsize=None)
def sources() -> dict[str, Source]:
    raw = _load("sources")
    defaults = raw.get("defaults", {})
    out: dict[str, Source] = {}
    for entry in raw.get("sources", []):
        data = {**defaults, **entry}
        src = Source(**{k: v for k, v in data.items() if k in Source.__dataclass_fields__})
        if src.id in out:
            raise ValueError(f"duplicate source id {src.id}")
        out[src.id] = src
    return out


@lru_cache(maxsize=None)
def lexicon() -> dict:
    raw = _load("lexicon")

    def split(lines):
        terms = []
        for line in lines or []:
            for t in str(line).split(","):
                t = t.strip().strip('"').strip("'").strip()
                if t:
                    terms.append(t)
        return terms

    lex = {
        "topics": {k: split(v) for k, v in raw["topics"].items()},
        "low_value": split(raw.get("low_value")),
        "practical": split(raw.get("practical")),
        "countries": {k: [str(x) for x in v] for k, v in raw["countries"].items()},
        "cities": {k: [str(x) for x in v] for k, v in raw["cities"].items()},
        "agency_markers": raw.get("agency_markers", {}),
        "aliases": [[str(x) for x in grp] for grp in raw.get("aliases", [])],
    }
    return lex


def reset_caches() -> None:
    for fn in (settings, editions, sources, lexicon):
        fn.cache_clear()
