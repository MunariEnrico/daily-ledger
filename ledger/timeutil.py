"""Time handling. All stored timestamps are UTC ISO-8601; display uses the
publication timezone (Europe/Luxembourg by default). DST is handled by zoneinfo."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

UTC = timezone.utc


def tz() -> ZoneInfo:
    return ZoneInfo(config.settings()["publication"]["timezone"])


def now_utc() -> datetime:
    """Current time; LEDGER_NOW (ISO) overrides it for tests and replays."""
    override = os.environ.get("LEDGER_NOW")
    if override:
        dt = datetime.fromisoformat(override)
        return dt if dt.tzinfo else dt.replace(tzinfo=tz())
    return datetime.now(UTC)


def now_local() -> datetime:
    return now_utc().astimezone(tz())


def today_local() -> date:
    return now_local().date()


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def publish_at(d: date) -> datetime:
    """Aware datetime of the 08:00 (configurable) publication on local date d."""
    return datetime.combine(d, _hm(config.settings()["publication"]["publish_time"]), tzinfo=tz())


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_local(s: str | datetime | None, lang: str = "en", with_time: bool = True) -> str:
    dt = parse_iso(s) if isinstance(s, str) else s
    if dt is None:
        return ""
    loc = dt.astimezone(tz())
    months_en = ["January", "February", "March", "April", "May", "June", "July", "August",
                 "September", "October", "November", "December"]
    months_it = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
                 "settembre", "ottobre", "novembre", "dicembre"]
    if lang == "it":
        out = f"{loc.day} {months_it[loc.month - 1]} {loc.year}"
    else:
        out = f"{loc.day} {months_en[loc.month - 1]} {loc.year}"
    if with_time:
        out += f", {loc:%H:%M} {loc.tzname()}"
    return out


def fmt_date_long(d: date, lang: str) -> str:
    days_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_it = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    base = fmt_local(datetime.combine(d, time(12), tzinfo=tz()), lang, with_time=False)
    return f"{(days_it if lang == 'it' else days_en)[d.weekday()]} {base}"


def hours_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)
