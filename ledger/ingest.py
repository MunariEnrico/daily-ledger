"""Retrieval: fetch every enabled feed with timeouts, retries and rate-limit
handling; normalise entries into Items; record per-source status. One failing
source never stops the run."""
from __future__ import annotations

import calendar
import concurrent.futures as cf
import hashlib
import logging
import re
import time
import urllib.robotparser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from dateutil import parser as dateparser

from . import config, textutil, timeutil

log = logging.getLogger("ledger.ingest")

# ───────────────────────────────────────────── HTTP


class Http:
    """Small HTTP client: honest User-Agent, timeouts, retries with backoff,
    Retry-After on 429/503, per-host politeness delay."""

    def __init__(self, timeout: float = 20, retries: int = 2, per_host_delay: float = 0.5):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en,it;q=0.8,fr;q=0.6"})
        self.timeout, self.retries, self.delay = timeout, retries, per_host_delay
        self._last: dict[str, float] = {}

    def get(self, url: str, accept: str | None = None, timeout: float | None = None) -> tuple[int | str, bytes, str, str]:
        host = urlparse(url).netloc
        err = ""
        for attempt in range(self.retries + 1):
            wait = self.delay - (time.time() - self._last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.time()
            try:
                r = self.s.get(url, timeout=timeout or self.timeout,
                               headers={"Accept": accept} if accept else None, allow_redirects=True)
                if r.status_code in (429, 502, 503, 504) and attempt < self.retries:
                    ra = r.headers.get("Retry-After", "")
                    time.sleep(min(int(ra), 30) if ra.isdigit() else 2 * (attempt + 1))
                    continue
                return r.status_code, r.content, r.url, ""
            except requests.RequestException as ex:
                err = f"{type(ex).__name__}: {str(ex)[:160]}"
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        return "ERR", b"", url, err

    def head_ok(self, url: str, timeout: float = 8) -> tuple[bool | None, int | str]:
        """Does the URL resolve? True = exists (2xx/3xx, or 401/403/402 = exists behind
        access control), False = 404/410/DNS failure, None = undetermined (timeouts, 5xx)."""
        try:
            r = self.s.head(url, timeout=timeout, allow_redirects=True)
            code = r.status_code
            if code in (405, 400, 501):  # some servers refuse HEAD
                r = self.s.get(url, timeout=timeout, allow_redirects=True, stream=True)
                code = r.status_code
                r.close()
        except requests.ConnectionError as ex:
            return (False if "Name or service not known" in str(ex) or "NameResolution" in str(ex) else None), "ERR"
        except requests.RequestException:
            return None, "ERR"
        if code < 400 or code in (401, 402, 403, 451):
            return True, code
        if code in (404, 410):
            return False, code
        return None, code


# ───────────────────────────────────────────── items


@dataclass
class Item:
    id: str
    source_id: str
    source_name: str
    owner: str
    url: str
    title: str
    summary: str
    published: str | None
    date_quality: str          # exact | no_tz | from_url | ambiguous | missing
    retrieved_at: str
    lang: str
    access_level: str          # headline_snippet | headline_only | official_document | full_text
    agency: str | None = None  # wire agency credited in the text, if any
    text: str = ""             # full text (official documents only)
    also_in: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|refsrc|cmpid|ito$|xtor|at_medium|at_campaign|wt_mc|ns_)")


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if not _TRACKING.match(k.lower())]
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    return urlunparse((scheme, p.netloc.lower(), p.path, "", urlencode(q), ""))


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


_IT_FR_DATE_WORDS = {
    # weekday abbreviations → removed; month names → English
    "gen": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "mag": "May", "giu": "Jun", "lug": "Jul",
    "ago": "Aug", "set": "Sep", "ott": "Oct", "nov": "Nov", "dic": "Dec",
    "janv": "Jan", "fevr": "Feb", "avr": "Apr", "mai": "May", "juin": "Jun", "juil": "Jul", "aout": "Aug",
    "sept": "Sep", "oct": "Oct", "dec": "Dec",
}
_WEEKDAY_ABBR = r"\b(lun|mar|mer|gio|ven|sab|dom|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b,?"


def parse_date(raw: str | None, source_tz: str) -> tuple[datetime | None, str]:
    """Parse a feed date string that feedparser could not handle.
    Returns (aware UTC datetime | None, quality)."""
    if not raw:
        return None, "missing"
    s = textutil.fold(raw).replace(" - ", " ")
    s = re.sub(_WEEKDAY_ABBR, " ", s)
    for k, v in _IT_FR_DATE_WORDS.items():
        s = re.sub(rf"\b{k}\w*\.?", v, s)
    try:
        dt = dateparser.parse(s, dayfirst=True)
    except (ValueError, OverflowError):
        return None, "missing"
    if dt is None:
        return None, "missing"
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(source_tz)).astimezone(timeutil.UTC), "no_tz"
    return dt.astimezone(timeutil.UTC), "exact"


_URL_DATE_PATTERNS = [
    (re.compile(r"/(20\d\d)/(\d{1,2})/(\d{1,2})/"), "ymd"),
    (re.compile(r"[-_/](\d{1,2})[-_](\d{1,2})[-_](20\d\d)(?:[-_/.]|$)"), "xx-xx-yyyy"),
    (re.compile(r"(20\d\d)-(\d{2})-(\d{2})"), "ymd"),
]

COUNTRY_TZ = {"IT": "Europe/Rome", "UK": "Europe/London", "US": "America/New_York", "FR": "Europe/Paris",
              "LU": "Europe/Luxembourg", "DE": "Europe/Berlin", "EU": "Europe/Brussels", "CN": "Asia/Shanghai",
              "IN": "Asia/Kolkata", "JP": "Asia/Tokyo", "ME": "Asia/Dubai", "INT": "UTC"}


def date_from_url(url: str, retrieved: datetime, source_tz: str) -> tuple[datetime | None, str]:
    """Derive a date from the URL. DD-MM vs MM-DD is ambiguous when both parts
    are ≤ 12; we only resolve it if exactly one reading is not in the future."""
    for rx, kind in _URL_DATE_PATTERNS:
        m = rx.search(url)
        if not m:
            continue
        a, b, c = (int(x) for x in m.groups())
        cands = []
        if kind == "ymd":
            cands = [(a, b, c)]
        else:
            if a <= 12 and b <= 12 and a != b:
                cands = [(c, b, a), (c, a, b)]  # d-m-y, m-d-y
            elif b <= 12:
                cands = [(c, b, a)]
            elif a <= 12:
                cands = [(c, a, b)]
        valid = []
        for y, mo, d in cands:
            try:
                dt = datetime(y, mo, d, 12, 0, tzinfo=ZoneInfo(source_tz))
            except ValueError:
                continue
            if dt.astimezone(timeutil.UTC) <= retrieved + timedelta(hours=14):
                valid.append(dt)
        if len(valid) == 1:
            return valid[0].astimezone(timeutil.UTC), "from_url"
        if len(valid) > 1:
            return None, "ambiguous"
    return None, "missing"


def detect_agency(text_folded: str) -> str | None:
    for agency, markers in config.lexicon()["agency_markers"].items():
        for mk in markers:
            if textutil.fold(mk) in text_folded:
                return agency
    return None


def entry_to_item(entry, src: config.Source, retrieved: datetime) -> Item | None:
    url = entry.get("link") or ""
    title = textutil.strip_html(entry.get("title", ""))
    if not url.startswith(("http://", "https://")) or not title:
        return None
    summary = textutil.strip_html(entry.get("summary") or entry.get("description") or "")
    if textutil.fold(summary).startswith(textutil.fold(title)) and len(summary) <= len(title) + 3:
        summary = ""
    summary = summary[:1500]
    tzname = COUNTRY_TZ.get(src.country, "UTC")
    published, quality = None, "missing"
    for key in ("published_parsed", "updated_parsed"):
        if entry.get(key):
            published = datetime.fromtimestamp(calendar.timegm(entry[key]), tz=timeutil.UTC)
            quality = "exact"
            break
    if published is None:
        raw = entry.get("published") or entry.get("updated") or entry.get("dc_date")
        published, quality = parse_date(raw, tzname)
    if published is None:
        published, quality = date_from_url(url, retrieved, tzname)
    if published and published > retrieved + timedelta(hours=14) and not src.calendar:
        # a date in the future is not trustworthy as a publication date
        # (release calendars are the exception: their dates ARE future events)
        published, quality = None, "ambiguous"
    agency = detect_agency(textutil.fold(title + " " + summary))
    return Item(
        id=item_id(url), source_id=src.id, source_name=src.name, owner=src.owner, url=canonical_url(url),
        title=title, summary=summary, published=timeutil.iso(published), date_quality=quality,
        retrieved_at=timeutil.iso(retrieved), lang=src.lang,
        access_level="headline_snippet" if len(summary) >= 40 else "headline_only", agency=agency)


@dataclass
class FeedResult:
    source_id: str
    ok: bool
    status: int | str
    error: str
    n_items: int
    fetched_at: str
    seconds: float
    newest: str | None = None


def fetch_source(src: config.Source, http: Http) -> tuple[FeedResult, list[Item]]:
    t0 = time.time()
    now = timeutil.now_utc()
    status, body, _final, err = http.get(src.feed, accept="application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5")
    items: list[Item] = []
    if status == 200 and body:
        parsed = feedparser.parse(body)
        for e in parsed.entries[:150]:
            it = entry_to_item(e, src, now)
            if it:
                items.append(it)
        if not items:
            err = err or ("feed parsed but contained no usable entries" + (f" ({parsed.bozo_exception})" if parsed.bozo else ""))
    elif not err:
        err = f"HTTP {status}"
    newest = max((i.published for i in items if i.published), default=None)
    res = FeedResult(src.id, bool(items), status, err, len(items), timeutil.iso(now), round(time.time() - t0, 2), newest)
    return res, items


def fetch_all(srcs: list[config.Source], workers: int = 12) -> tuple[list[FeedResult], list[Item]]:
    http = Http()
    results, items = [], []
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_source, s, http): s for s in srcs}
        for fut in cf.as_completed(futs):
            src = futs[fut]
            try:
                res, its = fut.result()
            except Exception as ex_:  # never let one source kill the run
                res, its = FeedResult(src.id, False, "ERR", f"{type(ex_).__name__}: {ex_}", 0,
                                      timeutil.iso(timeutil.now_utc()), 0.0), []
            results.append(res)
            items.extend(its)
            if not res.ok:
                log.warning("source %s failed: %s", src.id, res.error)
    return sorted(results, key=lambda r: r.source_id), items


def merge_duplicates(items: list[Item]) -> list[Item]:
    """Same canonical URL from several feeds of one publisher → one item; the
    same headline republished by one owner under another URL → one item."""
    by_id: dict[str, Item] = {}
    seen_title: dict[tuple[str, str], str] = {}
    for it in items:
        tkey = (it.owner, textutil.squash(it.title))
        if tkey in seen_title and seen_title[tkey] != it.id:
            it.id = seen_title[tkey]
        else:
            seen_title.setdefault(tkey, it.id)
        if it.id in by_id:
            keep = by_id[it.id]
            if it.source_id not in keep.also_in and it.source_id != keep.source_id:
                keep.also_in.append(it.source_id)
            if len(it.summary) > len(keep.summary):
                keep.summary, keep.access_level = it.summary, it.access_level
        else:
            by_id[it.id] = it
    return list(by_id.values())


# ───────────────────────────────────────────── official full text


_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allows(url: str, http: Http) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        status, body, _, _ = http.get(base + "/robots.txt", timeout=8)
        if status == 200:
            rp.parse(body.decode("utf-8", "replace").splitlines())
            _robots[base] = rp
        else:
            _robots[base] = None  # no robots.txt → allowed
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(config.USER_AGENT, url)


def extract_main_text(html_bytes: bytes) -> str:
    try:
        import trafilatura  # optional, better extraction
        txt = trafilatura.extract(html_bytes.decode("utf-8", "replace"), include_comments=False) or ""
        if txt:
            return re.sub(r"\s+", " ", txt)[:8000]
    except Exception:
        pass
    h = html_bytes.decode("utf-8", "replace")
    m = re.search(r"(?is)<(main|article)[^>]*>(.*?)</\1>", h)
    return textutil.strip_html(m.group(2) if m else h)[:8000]


def enrich_official(items: list[Item], http: Http | None = None) -> int:
    """Fetch the public page of official-source items (government, central
    banks, regulators, statistics offices) so claims can be checked against the
    document itself, not just its feed summary. Returns how many succeeded."""
    http = http or Http(timeout=15, retries=1)
    srcs = config.sources()
    n = 0
    for it in items:
        src = srcs.get(it.source_id)
        if not src or not src.fulltext or it.text:
            continue
        if not robots_allows(it.url, http):
            continue
        status, body, _, _ = http.get(it.url, accept="text/html,application/xhtml+xml")
        if status == 200 and body and b"<" in body[:2000]:
            text = extract_main_text(body)
            if textutil.word_count(text) >= 60:
                it.text, it.access_level = text, "official_document"
                n += 1
    return n
