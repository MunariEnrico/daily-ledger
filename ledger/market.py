"""Market Intelligence Panel — official, free, attributable data only.

Every observation carries provider, instrument (series key), observation date,
frequency and data nature (end-of-day reference rate, daily estimate, monthly
average…). Nothing is estimated; stale data is labelled with its real date.
Changes are computed only between two observations of the same series from
the same provider, and are labelled as computed.

Verified 2026-10-28 from the development environment:
  ECB Data Portal (EXR, FM, IRS)  · Deutsche Bundesbank SDMX API (BBSIS)
  U.S. Treasury daily par yield CSV · Bank of England IADB CSV
  FRED API (needs a free key: FRED_API_KEY) — Brent & Henry Hub (EIA, public domain),
  Fed funds target range; licensed index levels only if explicitly enabled.
No free, licensed source was found for FTSE MIB, EURO STOXX 50, STOXX Europe 600,
FTSE 100, gold or TTF gas; those rows are shown as unavailable, never filled in."""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import date, datetime, timedelta

from . import config, timeutil, validate
from .ingest import Http

log = logging.getLogger("ledger.market")

ECB = "https://data-api.ecb.europa.eu/service/data/"
BBK = "https://api.statistiken.bundesbank.de/rest/data/"
UST = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
       "{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv")
BOE = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom={frm}"
       "&Dateto=now&SeriesCodes={codes}&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
FRED = "https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={key}&file_type=json&sort_order=desc&limit=10"


def _obs(oid, group, en, it, unit, provider, instrument, nature, freq, url, points, decimals=4, note=""):
    """points: list of (date, value) sorted ascending (non-null)."""
    o = {"id": oid, "group": group, "label": {"en": en, "it": it}, "unit": unit, "provider": provider,
         "instrument": instrument, "nature": nature, "frequency": freq, "source_url": url, "decimals": decimals,
         "note": note, "status": "unavailable", "value": None, "date": None, "prev_value": None, "prev_date": None,
         "change": None}
    pts = [(d, v) for d, v in points if v is not None]
    if not pts:
        o["reason"] = {"en": "The provider returned no observation this morning.",
                       "it": "Il fornitore non ha restituito alcuna osservazione stamattina."}
    if pts:
        o["date"], o["value"] = pts[-1][0].isoformat(), pts[-1][1]
        o["status"] = "ok"
        if len(pts) >= 2:
            o["prev_date"], o["prev_value"] = pts[-2][0].isoformat(), pts[-2][1]
            o["change"] = round(pts[-1][1] - pts[-2][1], 6)
    return o


def unavailable(oid, group, en, it, reason_en, reason_it):
    return {"id": oid, "group": group, "label": {"en": en, "it": it}, "status": "unavailable",
            "reason": {"en": reason_en, "it": reason_it}, "value": None, "date": None}


class Market:
    def __init__(self, http: Http | None = None):
        self.http = http or Http(timeout=25, retries=2, per_host_delay=0.3)
        self.errors: list[str] = []

    # ── helpers ────────────────────────────────────────────────────────
    def _csv(self, url, accept=None):
        status, body, _, err = self.http.get(url, accept=accept)
        if status != 200 or not body:
            raise RuntimeError(f"{url[:80]}… → {status} {err}")
        return body.decode("utf-8-sig", "replace")

    def ecb(self, key: str, n: int = 2) -> dict[str, list[tuple[date, float]]]:
        url = f"{ECB}{key}?lastNObservations={n}&format=csvdata"
        text = self._csv(url)
        out: dict[str, list] = {}
        for row in csv.DictReader(io.StringIO(text)):
            try:
                d = row["TIME_PERIOD"]
                dt = date.fromisoformat(d) if len(d) == 10 else date.fromisoformat(d + "-01")
                out.setdefault(row["KEY"], []).append((dt, float(row["OBS_VALUE"])))
            except (KeyError, ValueError):
                continue
        return {k: sorted(v) for k, v in out.items()}

    # ── connectors ────────────────────────────────────────────────────
    def fx(self) -> list[dict]:
        url = ECB + "EXR/D.USD+GBP.EUR.SP00.A"
        data = self.ecb("EXR/D.USD+GBP.EUR.SP00.A", 3)
        usd = data.get("EXR.D.USD.EUR.SP00.A", [])
        gbp = data.get("EXR.D.GBP.EUR.SP00.A", [])
        out = [
            _obs("eurusd", "fx", "EUR/USD", "EUR/USD", "", "European Central Bank", "EXR.D.USD.EUR.SP00.A",
                 "ECB euro reference rate (published ~16:00 CET), end-of-day", "daily", url, usd, 4),
            _obs("eurgbp", "fx", "EUR/GBP", "EUR/GBP", "", "European Central Bank", "EXR.D.GBP.EUR.SP00.A",
                 "ECB euro reference rate (published ~16:00 CET), end-of-day", "daily", url, gbp, 4),
        ]
        g = dict(gbp)
        cross = [(d, round(v / g[d], 6)) for d, v in usd if d in g and g[d]]
        out.append(_obs("gbpusd", "fx", "GBP/USD", "GBP/USD", "", "European Central Bank (derived)",
                        "EXR USD/EUR ÷ GBP/EUR", "Cross rate computed from ECB reference rates of the same day",
                        "daily", url, cross, 4))
        return out

    def ecb_rates(self) -> list[dict]:
        url = ECB + "FM/D.U2.EUR.4F.KR.DFR.LEV"
        dfr = self.ecb("FM/D.U2.EUR.4F.KR.DFR.LEV", 2).get("FM.D.U2.EUR.4F.KR.DFR.LEV", [])
        o = _obs("ecb_dfr", "policy", "ECB deposit facility rate", "BCE — tasso sui depositi", "%",
                 "European Central Bank", "FM.D.U2.EUR.4F.KR.DFR.LEV", "Official policy rate (level in force)",
                 "event", url, dfr[-1:], 2)
        return [o]

    def btp_bund_monthly(self) -> list[dict]:
        url = ECB + "IRS/M.IT+DE.L.L40.CI.0000.EUR.N.Z"
        data = self.ecb("IRS/M.IT+DE.L.L40.CI.0000.EUR.N.Z", 2)
        it = data.get("IRS.M.IT.L.L40.CI.0000.EUR.N.Z", [])
        de = data.get("IRS.M.DE.L.L40.CI.0000.EUR.N.Z", [])
        dd = dict(de)
        spread = [(d, round((v - dd[d]) * 100, 1)) for d, v in it if d in dd]
        nature = "Monthly average, ECB long-term interest rate for convergence purposes"
        return [
            _obs("btp10_m", "bonds", "Italy 10Y yield (monthly avg.)", "BTP 10 anni (media mensile)", "%",
                 "European Central Bank", "IRS.M.IT.L.L40.CI.0000.EUR.N.Z", nature, "monthly", url, it, 3),
            _obs("spread_m", "bonds", "BTP–Bund 10Y spread (monthly avg.)", "Spread BTP–Bund 10 anni (media mensile)",
                 "bp", "European Central Bank (derived)", "IRS IT − IRS DE", "Difference of the two monthly averages",
                 "monthly", url, spread, 1),
        ]

    def bund_daily(self) -> list[dict]:
        key = "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
        url = f"{BBK}{key}?lastNObservations=3"
        text = self._csv(url, accept="application/vnd.sdmx.data+csv;version=1.0.0")
        pts = []
        first = text.split("\n", 1)[0]
        reader = csv.reader(io.StringIO(text), delimiter=";" if first.count(";") > first.count(",") else ",")
        header = next(reader, [])
        try:
            i_t, i_v = header.index("TIME_PERIOD"), header.index("OBS_VALUE")
        except ValueError:
            i_t, i_v = 16, 17
        for row in reader:
            try:
                pts.append((date.fromisoformat(row[i_t]), float(row[i_v])))
            except (ValueError, IndexError):
                continue
        return [_obs("bund10", "bonds", "Germany 10Y Bund yield", "Bund 10 anni", "%", "Deutsche Bundesbank", key,
                     "Daily yield-curve estimate for listed Federal securities (Svensson method)",
                     "daily", url, sorted(pts), 2)]

    def ust10(self, today: date) -> list[dict]:
        pts = []
        for y in sorted({today.year, (today - timedelta(days=10)).year}):
            url = UST.format(y=y)
            text = self._csv(url)
            for row in csv.DictReader(io.StringIO(text)):
                try:
                    pts.append((datetime.strptime(row["Date"], "%m/%d/%Y").date(), float(row["10 Yr"])))
                except (KeyError, ValueError):
                    continue
        return [_obs("ust10", "bonds", "US 10Y Treasury yield", "Treasury USA 10 anni", "%", "U.S. Department of the Treasury",
                     "Daily Treasury Par Yield Curve — 10 Yr", "Official par yield, end-of-day", "daily",
                     UST.format(y=today.year), sorted(pts)[-3:], 2)]

    def boe(self, today: date) -> list[dict]:
        frm = (today - timedelta(days=20)).strftime("%d/%b/%Y")
        url = BOE.format(frm=frm, codes="IUDMNPY,IUDBEDR")
        text = self._csv(url)
        if not text.lstrip().startswith("DATE"):
            # the IADB sometimes answers with an HTML error page and HTTP 200: retry once
            import time
            time.sleep(3)
            text = self._csv(url)
        gilt, bank = [], []
        for row in csv.DictReader(io.StringIO(text)):
            try:
                d = datetime.strptime(row["DATE"].strip(), "%d %b %Y").date()
            except (KeyError, ValueError):
                continue
            for col, lst in (("IUDMNPY", gilt), ("IUDBEDR", bank)):
                try:
                    lst.append((d, float(row[col])))
                except (KeyError, ValueError, TypeError):
                    pass
        return [
            _obs("gilt10", "bonds", "UK 10Y gilt yield", "Gilt UK 10 anni", "%", "Bank of England", "IADB IUDMNPY",
                 "10-year nominal par yield, daily, end-of-day", "daily", url, sorted(gilt)[-3:], 2),
            _obs("boe_rate", "policy", "Bank of England Bank Rate", "Bank of England — Bank Rate", "%", "Bank of England",
                 "IADB IUDBEDR", "Official Bank Rate (level in force)", "event", url, sorted(bank)[-1:], 2),
        ]

    def fred(self, sid: str) -> list[tuple[date, float]]:
        key = os.environ.get("FRED_API_KEY", "")
        status, body, _, err = self.http.get(FRED.format(sid=sid, key=key))
        if status != 200:
            raise RuntimeError(f"FRED {sid}: {status} {err}")
        import json
        pts = []
        for o in json.loads(body).get("observations", []):
            if o.get("value") not in (".", "", None):
                pts.append((date.fromisoformat(o["date"]), float(o["value"])))
        return sorted(pts)[-3:]

    def fred_block(self, show_licensed: bool) -> list[dict]:
        out = []
        furl = "https://fred.stlouisfed.org/series/"
        if not os.environ.get("FRED_API_KEY"):
            msg = ("Requires a free FRED API key (FRED_API_KEY).", "Richiede una chiave API FRED gratuita (FRED_API_KEY).")
            out += [unavailable("brent", "commodities", "Brent crude (USD/bbl)", "Petrolio Brent (USD/barile)", *msg),
                    unavailable("henryhub", "commodities", "US natural gas, Henry Hub", "Gas naturale USA, Henry Hub", *msg),
                    unavailable("fed_rate", "policy", "Fed funds target range", "Fed — intervallo obiettivo", *msg)]
        else:
            for sid, oid, en, it, unit in (("DCOILBRENTEU", "brent", "Brent crude (USD/bbl)", "Petrolio Brent (USD/barile)", "$"),
                                           ("DHHNGSP", "henryhub", "US natural gas, Henry Hub ($/MMBtu)",
                                            "Gas naturale USA, Henry Hub ($/MMBtu)", "$")):
                try:
                    out.append(_obs(oid, "commodities", en, it, unit, "U.S. EIA via FRED", sid,
                                    "Daily spot price (published with a lag of several days)", "daily", furl + sid,
                                    self.fred(sid), 2))
                except Exception as ex:
                    self.errors.append(str(ex))
                    out.append(unavailable(oid, "commodities", en, it, "Retrieval failed this morning.", "Recupero non riuscito stamattina."))
            try:
                lo, hi = self.fred("DFEDTARL"), self.fred("DFEDTARU")
                o = _obs("fed_rate", "policy", "Fed funds target range (upper bound)", "Fed — limite superiore dell'intervallo",
                         "%", "Federal Reserve via FRED", "DFEDTARU", "Target range in force", "event", furl + "DFEDTARU", hi[-1:], 2)
                if lo and o["value"] is not None:
                    o["note"] = f"{lo[-1][1]:.2f}–{hi[-1][1]:.2f}%"
                out.append(o)
            except Exception as ex:
                self.errors.append(str(ex))
                out.append(unavailable("fed_rate", "policy", "Fed funds target range", "Fed — intervallo obiettivo",
                                       "Retrieval failed this morning.", "Recupero non riuscito stamattina."))
            if show_licensed:
                for sid, oid, en in (("SP500", "spx", "S&P 500"), ("NASDAQCOM", "ndx", "Nasdaq Composite"),
                                     ("DJIA", "dji", "Dow Jones Industrial Average"), ("NIKKEI225", "n225", "Nikkei 225")):
                    try:
                        out.append(_obs(oid, "equities", en, en, "", "FRED (index provider data, licensed)", sid,
                                        "Daily close", "daily", furl + sid, self.fred(sid), 2))
                    except Exception as ex:
                        self.errors.append(str(ex))
        return out

    # ── assemble ──────────────────────────────────────────────────────
    def snapshot(self, today: date) -> dict:
        st = config.settings()["market"]
        rows: list[dict] = []
        for name, fn in (("fx", self.fx), ("ecb", self.ecb_rates), ("btp", self.btp_bund_monthly),
                         ("bund", self.bund_daily), ("ust", lambda: self.ust10(today)), ("boe", lambda: self.boe(today))):
            try:
                rows += fn()
            except Exception as ex:
                self.errors.append(f"{name}: {ex}")
                log.warning("market %s failed: %s", name, ex)
        rows += self.fred_block(st.get("show_licensed_index_levels", False))
        if not st.get("show_licensed_index_levels"):
            for oid, en in (("spx", "S&P 500"), ("ndx", "Nasdaq Composite"), ("dji", "Dow Jones Industrial Average"),
                            ("n225", "Nikkei 225")):
                rows.append(unavailable(oid, "equities", en, en,
                                        "Index levels are licensed by the index provider; not redistributed on a public site (can be enabled in settings for private use).",
                                        "I livelli degli indici sono concessi in licenza dal provider; non ridistribuiti su un sito pubblico (attivabili nelle impostazioni per uso privato)."))
        for oid, en, it in (("ftsemib", "FTSE MIB", "FTSE MIB"), ("sx5e", "EURO STOXX 50", "EURO STOXX 50"),
                            ("sxxp", "STOXX Europe 600", "STOXX Europe 600"), ("ukx", "FTSE 100", "FTSE 100")):
            rows.append(unavailable(oid, "equities", en, it, "No free licensed data source available.",
                                    "Nessuna fonte di dati gratuita e con licenza disponibile."))
        rows.append(unavailable("gold", "commodities", "Gold", "Oro", "No free licensed daily source available.",
                                "Nessuna fonte giornaliera gratuita e con licenza disponibile."))
        rows.append(unavailable("ttf", "commodities", "EU natural gas (TTF)", "Gas naturale UE (TTF)",
                                "No free licensed source available.", "Nessuna fonte gratuita e con licenza disponibile."))
        # gate: completeness + staleness
        for r in rows:
            if r["status"] == "ok":
                probs = validate.check_market_observation(r, today, st["stale_days"] if r["frequency"] != "event" else {"daily": 3650})
                if any(p.startswith("stale") for p in probs):
                    r["status"] = "stale"
                    r["problems"] = probs
                elif probs:
                    r["status"] = "invalid"
                    r["problems"] = probs
        return {"retrieved_at": timeutil.iso(timeutil.now_utc()), "rows": rows, "errors": self.errors,
                "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    """Deterministic, evidence-only market notes (no causes, no forecasts)."""
    by = {r["id"]: r for r in rows if r.get("status") == "ok"}
    notes = {"en": [], "it": []}

    def mv(r, en_up, en_dn, it_up, it_dn):
        if r.get("change") is None or r.get("prev_date") is None:
            return None
        c = r["change"]
        if abs(c) < 10 ** -(r.get("decimals", 2)):
            return ("unchanged", "invariato")
        return (en_up, it_up) if c > 0 else (en_dn, it_dn)

    for oid in ("bund10", "ust10", "gilt10"):
        r = by.get(oid)
        if r:
            d = mv(r, "rose", "fell", "in rialzo", "in calo")
            if d:
                unit = "bp"
                bp = round(abs(r["change"]) * 100)
                notes["en"].append(f"{r['label']['en']}: {r['value']:.2f}% on {r['date']} ({d[0]}"
                                   + (f" {bp} {unit}" if d[0] != "unchanged" else "") + f" from {r['prev_date']}).")
                notes["it"].append(f"{r['label']['it']}: {str(f'{r['value']:.2f}').replace('.', ',')}% al {r['date']} ({d[1]}"
                                   + (f" di {bp} pb" if d[1] != "invariato" else "") + f" rispetto al {r['prev_date']}).")
    r = by.get("eurusd")
    if r:
        notes["en"].append(f"ECB reference rate EUR/USD {r['value']:.4f} on {r['date']}.")
        notes["it"].append(f"Cambio di riferimento BCE EUR/USD {str(f'{r['value']:.4f}').replace('.', ',')} al {r['date']}.")
    return notes
