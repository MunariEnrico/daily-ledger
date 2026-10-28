"""Persistence in plain JSON files under data/ (committed to git by the
workflow — git history is the backup and the audit log of every change).

data/
  index.json                      list of editions and their status
  status.json                     latest run status (status page)
  trackers.json                   persistent story trackers
  corrections.jsonl               append-only corrections log
  editions/YYYY-MM-DD/
      edition.json                stories (both languages + evidence), editions, market, brief
      sources.json                per-source retrieval results for that run
      audit.jsonl.gz              every draft and its validation report
      run.json                    run log and metrics
"""
from __future__ import annotations

import gzip
import json
import os
import tempfile
from pathlib import Path

from . import config, timeutil


def data_dir() -> Path:
    d = Path(os.environ.get("LEDGER_DATA", config.DATA_DIR))
    d.mkdir(parents=True, exist_ok=True)
    return d


def edition_dir(date_str: str) -> Path:
    d = data_dir() / "editions" / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written edition


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_audit(date_str: str, records: list[dict]) -> None:
    path = edition_dir(date_str) / "audit.jsonl.gz"
    with gzip.open(path, "at", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def read_audit(date_str: str) -> list[dict]:
    path = edition_dir(date_str) / "audit.jsonl.gz"
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def index() -> dict:
    return read_json(data_dir() / "index.json", {"editions": {}})


def set_index(date_str: str, **fields) -> dict:
    idx = index()
    entry = idx["editions"].setdefault(date_str, {})
    entry.update(fields)
    write_json(data_dir() / "index.json", idx)
    return entry


def load_edition(date_str: str) -> dict | None:
    return read_json(data_dir() / "editions" / date_str / "edition.json")


def save_edition(date_str: str, edition: dict) -> None:
    write_json(edition_dir(date_str) / "edition.json", edition)


def published_dates() -> list[str]:
    return sorted((d for d, e in index()["editions"].items() if e.get("status") == "published"), reverse=True)


def previous_stories(before: str, days: int = 3) -> list[dict]:
    out = []
    for d in [x for x in published_dates() if x < before][:days]:
        ed = load_edition(d) or {}
        out.extend(ed.get("stories", {}).values())
    return out


def append_correction(record: dict) -> None:
    path = data_dir() / "corrections.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({**record, "logged_at": timeutil.iso(timeutil.now_utc())}, ensure_ascii=False) + "\n")


def corrections() -> list[dict]:
    path = data_dir() / "corrections.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
