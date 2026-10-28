"""Deterministic text utilities: normalisation, tokenisation, and extraction of
the checkable 'atoms' of a claim — numbers, dates, quotations, causal
language, legal-status language and guilt language. These power both the
clustering and the publication gate, so they are deliberately conservative."""
from __future__ import annotations

import html
import re
import unicodedata
from functools import lru_cache

# ───────────────────────────────────────────── normalisation

_QUOTE_MAP = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
              "–": "-", "—": "-", "−": "-", " ": " ", " ": " ", " ": " "}


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = s.replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def fold(s: str | None) -> str:
    """Lowercase, strip accents, unify apostrophes/dashes/spaces."""
    if not s:
        return ""
    s = "".join(_QUOTE_MAP.get(c, c) for c in s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def squash(s: str) -> str:
    """Fold and drop punctuation — used for verbatim-span matching."""
    s = fold(s)
    s = re.sub(r"[^\w%€$£]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")

STOPWORDS = set("""
a an the and or but of to in on at by for with from as is are was were be been being it its this that these those
has have had will would can could should may might not no than then there their they them he she his her we our you
your i me my about after before over under into out up down off more most less new says said say also just very per
il lo la i gli le un una uno di da del della dello dei degli delle al allo alla ai agli alle dal dalla dai dagli
dalle nel nella nei negli nelle sul sulla sui sugli sulle con per tra fra e ed o ma che chi cui non piu come anche
se si sono era erano ha hanno essere stato stata stati dopo prima oggi ieri domani questo questa questi queste quello
quella ci ne gia ancora solo tutto tutti tutte molto
le la les un une des de du et ou mais que qui dans sur pour par avec sans est sont ete au aux ce cette ces plus
der die das ein eine und oder aber von zu im in auf mit fur ist sind war den dem des nicht
""".split())


def words(s: str) -> list[str]:
    return WORD_RE.findall(fold(s))


def content_tokens(s: str) -> list[str]:
    return [w for w in words(s) if (len(w) > 2 or w.isdigit()) and w not in STOPWORDS]


def word_count(s: str) -> int:
    return len(re.findall(r"\w+", s or ""))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ───────────────────────────────────────────── keyword matching

@lru_cache(maxsize=4096)
def term_regex(terms: tuple[str, ...]) -> re.Pattern | None:
    parts = []
    for t in terms:
        f = fold(t).strip()
        if not f:
            continue
        if f.endswith("*"):
            parts.append(r"\b" + re.escape(f[:-1]) + r"\w*")
        else:
            parts.append(r"\b" + re.escape(f) + r"\b")
    if not parts:
        return None
    return re.compile("|".join(parts))


def count_terms(text_folded: str, terms) -> int:
    rx = term_regex(tuple(terms))
    return len(rx.findall(text_folded)) if rx else 0


def has_term(text_folded: str, terms) -> bool:
    rx = term_regex(tuple(terms))
    return bool(rx and rx.search(text_folded))


# ───────────────────────────────────────────── numbers

_SCALE = {
    "thousand": 1e3, "thousands": 1e3, "k": 1e3, "mila": 1e3,
    "million": 1e6, "millions": 1e6, "mn": 1e6, "m": 1e6, "mln": 1e6, "milione": 1e6, "milioni": 1e6,
    "billion": 1e9, "billions": 1e9, "bn": 1e9, "mld": 1e9, "mrd": 1e9, "miliardo": 1e9, "miliardi": 1e9,
    "milliard": 1e9, "milliards": 1e9, "milliarde": 1e9, "milliarden": 1e9,
    "trillion": 1e12, "trillions": 1e12,
}
_PCT = {"%", "per cent", "percent", "per cento", "pour cent", "prozent", "pct"}
_BP = {"bp", "bps", "basis point", "basis points", "punti base", "points de base", "pb"}

_UNIT_ALT = "|".join(sorted(
    [re.escape(x) for x in list(_SCALE) + list(_PCT) + list(_BP)], key=len, reverse=True))
NUM_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?:\s?(" + _UNIT_ALT + r"))?(?![\w])")

_NUMBER_WORDS = {
    # en
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100, "dozen": 12,
    # it
    "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10,
    "undici": 11, "dodici": 12, "tredici": 13, "quattordici": 14, "quindici": 15, "sedici": 16,
    "diciassette": 17, "diciotto": 18, "diciannove": 19, "venti": 20, "trenta": 30, "quaranta": 40,
    "cinquanta": 50, "cento": 100, "mille": 1000, "dozzina": 12,
    # fr / de (evidence only)
    "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
    "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}
_NUMWORD_RE = re.compile(r"\b(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\b")


def _unit_kind(u: str | None) -> tuple[str, float]:
    if not u:
        return "", 1.0
    u = u.strip().lower()
    if u in _PCT:
        return "pct", 1.0
    if u in _BP:
        return "bp", 1.0
    return "", _SCALE.get(u, 1.0)


def _interpretations(core: str, lang: str | None) -> set[float]:
    """Possible numeric values of a written number. For a known output
    language the convention is strict; for evidence (lang=None) every
    plausible reading is kept so that we never wrongly reject a match."""
    has_dot, has_comma = "." in core, "," in core
    if has_dot and has_comma:
        dec = "." if core.rfind(".") > core.rfind(",") else ","
        thou = "," if dec == "." else "."
        return {float(core.replace(thou, "").replace(dec, "."))}
    if not has_dot and not has_comma:
        return {float(core)}
    sep = "." if has_dot else ","
    groups = core.split(sep)
    if len(groups) > 2:  # 1.234.567
        return {float("".join(groups))}
    as_thousands = float("".join(groups)) if len(groups[1]) == 3 else None
    as_decimal = float(groups[0] + "." + groups[1])
    if lang == "en":
        return {as_decimal} if sep == "." else ({as_thousands} if as_thousands is not None else {as_decimal})
    if lang in ("it", "fr", "de"):
        return {as_decimal} if sep == "," else ({as_thousands} if as_thousands is not None else {as_decimal})
    return {v for v in (as_thousands, as_decimal) if v is not None}


def extract_numbers(text: str, lang: str | None = None) -> list[dict]:
    """Return [{raw, values:set[float], kind}] for every number in text.
    kind is 'pct', 'bp' or '' (plain/currency/count)."""
    out = []
    f = fold(text)
    for m in NUM_RE.finditer(f):
        core, unit = m.group(1), m.group(2)
        kind, scale = _unit_kind(unit)
        vals = {round(v * scale, 6) for v in _interpretations(core, lang)}
        out.append({"raw": m.group(0).strip(), "values": vals, "kind": kind})
    for m in _NUMWORD_RE.finditer(f):
        out.append({"raw": m.group(0), "values": {float(_NUMBER_WORDS[m.group(1)])}, "kind": "", "word": True})
    return out


def number_supported(num: dict, evidence_numbers: list[dict]) -> bool:
    for ev in evidence_numbers:
        if num["kind"] and ev["kind"] and num["kind"] != ev["kind"]:
            continue
        if num["kind"] == "pct" and ev["kind"] != "pct":
            # "3%" must be backed by a percentage in the evidence
            continue
        for a in num["values"]:
            for b in ev["values"]:
                if abs(a - b) <= 1e-9 * max(1.0, abs(a)):
                    return True
    return False


def canonical_numbers(text: str, lang: str) -> list[tuple[str, float]]:
    """Strict canonical list used for IT/EN parity (number words excluded:
    'three' vs '3' are equivalent, so words are normalised too)."""
    out = []
    for n in extract_numbers(text, lang):
        for v in n["values"]:
            out.append((n["kind"], v))
    return sorted(out)


# ───────────────────────────────────────────── dates

_MONTHS = {
    1: ["january", "jan", "gennaio", "janvier", "januar", "janvier"],
    2: ["february", "feb", "febbraio", "fevrier", "februar"],
    3: ["march", "mar", "marzo", "mars", "marz"],
    4: ["april", "apr", "aprile", "avril"],
    5: ["may", "maggio", "mai"],
    6: ["june", "jun", "giugno", "juin", "juni"],
    7: ["july", "jul", "luglio", "juillet", "juli"],
    8: ["august", "aug", "agosto", "aout"],
    9: ["september", "sept", "sep", "settembre", "septembre"],
    10: ["october", "oct", "ottobre", "octobre", "oktober", "ott"],
    11: ["november", "nov", "novembre"],
    12: ["december", "dec", "dicembre", "decembre", "dezember", "dic"],
}
MONTH_WORDS = {w: n for n, ws in _MONTHS.items() for w in ws}
_MONTH_ALT = "|".join(sorted(MONTH_WORDS, key=len, reverse=True))
_DATE_DM = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th|er|°|º)?\.?\s+(" + _MONTH_ALT + r")\b")
_DATE_MD = re.compile(r"\b(" + _MONTH_ALT + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b")
_WEEKDAYS = {
    0: ["monday", "lunedi", "lundi", "montag"], 1: ["tuesday", "martedi", "mardi", "dienstag"],
    2: ["wednesday", "mercoledi", "mercredi", "mittwoch"], 3: ["thursday", "giovedi", "jeudi", "donnerstag"],
    4: ["friday", "venerdi", "vendredi", "freitag"], 5: ["saturday", "sabato", "samedi", "samstag"],
    6: ["sunday", "domenica", "dimanche", "sonntag"],
}
WEEKDAY_WORDS = {w: n for n, ws in _WEEKDAYS.items() for w in ws}
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(WEEKDAY_WORDS) + r")\b")


def extract_dates(text: str) -> set[tuple[int, int]]:
    f = fold(text)
    out = set()
    for d, mname in _DATE_DM.findall(f):
        if 1 <= int(d) <= 31:
            out.add((int(d), MONTH_WORDS[mname]))
    for mname, d in _DATE_MD.findall(f):
        if 1 <= int(d) <= 31 and mname not in ("may", "mar", "mai"):  # avoid "may 5 people"
            out.add((int(d), MONTH_WORDS[mname]))
    return out


def extract_weekdays(text: str) -> set[int]:
    return {WEEKDAY_WORDS[w] for w in _WEEKDAY_RE.findall(fold(text))}


# ───────────────────────────────────────────── quotations

_QUOTE_RES = [re.compile(p) for p in (
    r"“([^”]{3,400})”", r'"([^"]{3,400})"', r"«\s*([^»]{3,400}?)\s*»",
    r"„([^“”]{3,400})[“”]",
)]


def extract_quotes(text: str, min_words: int = 3) -> list[str]:
    out = []
    for rx in _QUOTE_RES:
        for q in rx.findall(text or ""):
            if word_count(q) >= min_words:
                out.append(q.strip())
    return out


def span_in(span: str, haystack_squashed: str) -> bool:
    s = squash(span)
    return bool(s) and s in haystack_squashed


# ───────────────────────────────────────────── semantic markers

CAUSAL = [
    "because", "because of", "due to", "caused", "causing", "led to", "leading to", "as a result", "resulted in",
    "triggered", "thanks to", "owing to", "driven by", "sparked", "prompted", "on the back of", "fuelled by", "fueled by",
    "perche", "a causa", "dovuto", "dovuta", "dovuti", "provocato", "provocata", "causato", "causata", "grazie a",
    "in seguito a", "per effetto", "spinto da", "spinta da", "trainato da", "trainata da", "innescato", "a seguito",
    "parce que", "a cause", "en raison", "grace a", "provoque", "entraine", "du fait",
    "wegen", "aufgrund", "verursacht", "infolge", "dank",
]

LEGAL_STATUS = {
    "proposed": ["proposal", "proposed", "proposes", "draft", "bill", "plans to", "plan to", "considering", "consultation",
                 "would introduce", "proposta", "propone", "bozza", "disegno di legge", "ddl", "ipotesi", "allo studio",
                 "valuta", "si valuta", "emendamento", "amendment", "projet de loi", "proposition", "envisage",
                 "entwurf", "geplant", "vorschlag"],
    "approved": ["approved", "passed", "adopted", "voted through", "enacted", "signed into law", "royal assent",
                 "approvato", "approvata", "approvati", "approvate", "via libera", "varato", "varata", "ok definitivo",
                 "convertito in legge", "a ete adopte", "a ete adoptee", "a ete vote", "verabschiedet", "beschlossen"],
    "in_force": ["in force", "takes effect", "took effect", "comes into force", "came into force", "effective from",
                 "in vigore", "entra in vigore", "entrata in vigore", "scatta", "scattano", "en vigueur",
                 "in kraft"],
    "published": ["gazzetta ufficiale", "official journal", "journal officiel", "published in the", "pubblicato in",
                  "statutory instrument", "bundesgesetzblatt"],
    "judgment": ["ruled", "ruling", "judgment", "judgement", "verdict", "sentenza", "ha stabilito", "ha respinto",
                 "ha accolto", "arret", "jugement", "urteil"],
    "guidance": ["guidance", "circolare", "interpello", "risoluzione", "faq", "chiarimenti", "clarification"],
}

GUILT = ["convicted", "found guilty", "pleaded guilty", "guilty", "sentenced", "murderer", "killer", "perpetrator",
         "condannato", "condannata", "condannati", "colpevole", "colpevoli", "assassino", "assassina", "omicida",
         "reo confesso", "rea confessa", "coupable", "condamne", "verurteilt", "schuldig", "morder"]
ACCUSATION = ["arrested", "charged", "accused", "suspect", "suspected", "investigated", "under investigation",
              "detained", "indicted", "allegation", "alleged", "arrestato", "arrestata", "arrestati", "fermato",
              "fermata", "indagato", "indagata", "indagati", "accusato", "accusata", "accusati", "sospettato",
              "denunciato", "rinviato a giudizio", "imputato", "inchiesta", "garde a vue", "mis en examen",
              "festgenommen", "verdachtig", "angeklagt"]
HEDGE = ["alleged", "allegedly", "accused", "suspect", "suspected", "according to", "charged with", "arrested on suspicion",
         "under investigation", "denies", "deny", "presumption of innocence", "has not been convicted", "not been charged",
         "presunto", "presunta", "presunti", "accusato", "accusata", "accusati", "sospettato", "sospettata",
         "indagato", "indagata", "secondo", "ipotesi di reato", "nega", "respinge le accuse", "presunzione di innocenza"]
FORECAST_HEDGE = ["could", "may", "might", "would", "expected", "likely", "possible", "possibly", "risk",
                  "potrebbe", "potrebbero", "potrà", "potra", "previsto", "prevista", "probabile", "possibile",
                  "rischio", "rischia", "atteso", "attesa"]
ATTRIBUTION = ["according to", "reported", "reports", "said", "says", "told", "stated", "announced", "per ",
               "secondo", "riporta", "riferisce", "ha detto", "ha dichiarato", "ha annunciato", "scrive", "fa sapere",
               "spiega", "ha reso noto", "comunica", "annuncia"]


def markers_in(text_folded: str, terms) -> list[str]:
    return [t for t in terms if re.search(r"\b" + re.escape(fold(t)) + r"\b", text_folded)]


def legal_statuses(text: str) -> set[str]:
    f = fold(text)
    return {k for k, terms in LEGAL_STATUS.items() if markers_in(f, terms)}
