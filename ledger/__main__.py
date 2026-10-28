"""Command line.

  python -m ledger auto            what the scheduler runs: build and/or publish as the clock requires
  python -m ledger build           retrieve, verify, write and stage today's edition
  python -m ledger publish         publish the staged edition (idempotent) and send email
  python -m ledger render          rebuild the static site from data/
  python -m ledger redraft         retry AI drafting for today's briefs from stored evidence (no re-fetch)
  python -m ledger sources --check test every registered feed now
  python -m ledger market          print today's market panel
  python -m ledger correct DATE STORY_ID --draft corrected.json --note-en "…" --note-it "…"
  python -m ledger withdraw DATE STORY_ID --note-en "…" --note-it "…"
  python -m ledger status          print the last run status
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from . import config, corrections, ingest, mailer, pipeline, site, store, timeutil


def _gh_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


def cmd_publish(date_str, late=False):
    res = pipeline.publish(date_str, late=late)
    print(json.dumps(res))
    ed = store.load_edition(res.get("date") or date_str or timeutil.today_local().isoformat())
    if res.get("published") and ed:
        email = mailer.send(ed)
        status = store.read_json(store.data_dir() / "status.json", {})
        status["email"] = email
        status["editions_status"] = ed.get("editions_status")
        store.write_json(store.data_dir() / "status.json", status)
        print("email:", json.dumps(email))
    out = site.Site().build()
    print("site:", json.dumps(out))
    _gh_output(deploy="true" if res.get("published") else "false")
    return res


def cmd_auto(args):
    """Decide from the local clock and the stored state. Safe to run any
    number of times: a published edition is never rebuilt or re-sent."""
    st = config.settings()
    now = timeutil.now_local()
    date_str = now.date().isoformat()
    state = store.index()["editions"].get(date_str, {}).get("status")
    action = pipeline.decide(now, state, st["publication"].get("late_grace_minutes", 20))
    print(f"[auto] {now.isoformat()} state={state} → {action}")
    if action in ("build", "build+publish"):
        try:
            res = pipeline.build(date_str)
        except Exception as ex:  # record and re-raise so the run shows as failed
            store.set_index(date_str, status="failed", error=f"{type(ex).__name__}: {ex}")
            raise
        print(json.dumps({k: v for k, v in res.get("run", {}).items() if k in ("sources", "stories", "seconds")}, default=str))
        now = timeutil.now_local()
        action = pipeline.decide(now, "staged", st["publication"].get("late_grace_minutes", 20))
        print(f"[auto] after build {now.isoformat()} → {action}")
    if action == "wait+publish":
        target = timeutil.publish_at(now.date())
        wait = (target - timeutil.now_local()).total_seconds()
        if 0 < wait <= st["publication"].get("late_grace_minutes", 20) * 60:
            print(f"[auto] waiting {int(wait)} s for {target.isoformat()}")
            time.sleep(wait)
        action = "publish"
    if action == "publish":
        late = timeutil.now_local() > timeutil.publish_at(now.date()).replace(minute=15)
        return cmd_publish(date_str, late=late)
    # still (re)render so the status page reflects the latest run
    site.Site().build()
    _gh_output(deploy="true" if action != "nothing" else "false")
    return {"action": action}


def main(argv=None):
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="ledger")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auto")
    b = sub.add_parser("build"); b.add_argument("--date"); b.add_argument("--force", action="store_true")  # noqa: E702
    pb = sub.add_parser("publish"); pb.add_argument("--date")  # noqa: E702
    sub.add_parser("render")
    rd = sub.add_parser("redraft"); rd.add_argument("--date"); rd.add_argument("--force", action="store_true")  # noqa: E702
    s = sub.add_parser("sources"); s.add_argument("--check", action="store_true")  # noqa: E702
    sub.add_parser("market")
    sub.add_parser("status")
    c = sub.add_parser("correct")
    c.add_argument("date"); c.add_argument("story_id"); c.add_argument("--draft", required=True)  # noqa: E702
    c.add_argument("--note-en", required=True); c.add_argument("--note-it", required=True)  # noqa: E702
    w = sub.add_parser("withdraw")
    w.add_argument("date"); w.add_argument("story_id")  # noqa: E702
    w.add_argument("--note-en", required=True); w.add_argument("--note-it", required=True)  # noqa: E702
    a = p.parse_args(argv)

    if a.cmd == "auto":
        cmd_auto(a)
    elif a.cmd == "build":
        res = pipeline.build(a.date, force=a.force)
        print(json.dumps({k: v for k, v in res.get("run", res).items() if k not in ("errors",)}, default=str, indent=1)[:4000])
    elif a.cmd == "publish":
        cmd_publish(a.date)
    elif a.cmd == "redraft":
        print(json.dumps(pipeline.redraft(a.date or timeutil.today_local().isoformat(), force=a.force), default=str))
        print(json.dumps(site.Site().build()))
    elif a.cmd == "render":
        print(json.dumps(site.Site().build()))
    elif a.cmd == "sources":
        srcs = [x for x in config.sources().values() if x.enabled]
        results, _ = ingest.fetch_all(srcs)
        bad = [r for r in results if not r.ok]
        for r in results:
            print(("OK  " if r.ok else "FAIL"), r.source_id.ljust(22), str(r.status).ljust(5), r.n_items, r.newest or "", r.error[:80])
        print(f"\n{len(results) - len(bad)}/{len(results)} sources OK")
        sys.exit(1 if len(bad) > len(results) * 0.25 else 0)
    elif a.cmd == "market":
        from .market import Market
        print(json.dumps(Market().snapshot(timeutil.today_local()), indent=1, ensure_ascii=False, default=str))
    elif a.cmd == "status":
        print(json.dumps(store.read_json(store.data_dir() / "status.json", {}), indent=1, default=str)[:6000])
    elif a.cmd == "correct":
        with open(a.draft, encoding="utf-8") as fh:
            draft = json.load(fh)
        try:
            corrections.correct(a.date, a.story_id, draft, a.note_en, a.note_it)
        except corrections.CorrectionRejected as ex:
            print(ex)
            sys.exit(2)
        print(json.dumps(site.Site().build()))
    elif a.cmd == "withdraw":
        corrections.withdraw(a.date, a.story_id, a.note_en, a.note_it)
        print(json.dumps(site.Site().build()))


if __name__ == "__main__":
    main()
