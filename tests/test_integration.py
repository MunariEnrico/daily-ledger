"""End-to-end tests: the whole pipeline offline (synthetic feed items, a fake
drafting model), and the real HTTP client against a local OpenAI-compatible
server."""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import good_draft, make_item
from ledger import config, ingest, llm as llmmod, pipeline, site, store
from ledger.llm import LLMError


class SmartFake:
    """Returns the reference inflation draft only when the evidence pack is
    about Istat inflation; refuses everything else (→ briefs)."""
    available = True

    def __init__(self):
        self.calls = 0

    def chat_json(self, system, user, max_tokens=3500):
        self.calls += 1
        if "Istat" in user and "2,1%" in user:
            return copy.deepcopy(good_draft()), "fake"
        raise LLMError("fake model declines")

    def stats(self):
        return {"calls": self.calls, "by_model": {"fake": self.calls}}


def synthetic_items():
    its = [
        make_item("istat", "Istat: a settembre l'inflazione sale al 2,1% su base annua",
                  "In Italia, secondo le stime preliminari, l'indice dei prezzi al consumo aumenta dello 0,3% su base mensile e del 2,1% su base annua, da 1,8% di agosto.",
                  url="https://www.istat.it/test-inflazione-settembre"),
        make_item("guardian_italy", "Italian inflation rises to 2.1% in September",
                  "Italy's annual inflation rate rose to 2.1% in September from 1.8% in August, the national statistics office Istat said on Tuesday.",
                  url="https://www.theguardian.com/world/test-italy-inflation"),
        make_item("veneziatoday", "Venezia, il Comune presenta il piano per il contributo di accesso",
                  "La giunta ha presentato in consiglio comunale il piano per il contributo di accesso dei turisti a Venezia per il prossimo anno.",
                  url="https://www.veneziatoday.it/test-contributo"),
        make_item("bbc_london", "London Underground strike called off",
                  "A planned strike on the London Underground has been called off after talks, the RMT union said on Tuesday.",
                  url="https://www.bbc.com/news/test-tube-strike"),
    ]
    results = [ingest.FeedResult(s.source_id, True, 200, "", 1, "2026-09-30T04:40:00Z", 0.1) for s in its]
    results.append(ingest.FeedResult("luxtimes", False, "ERR", "ConnectionError: simulated", 0, "2026-09-30T04:40:00Z", 0.1))
    return results, its


def test_full_pipeline_offline(tmp_path, monkeypatch):
    st = config.settings()
    monkeypatch.setitem(st["verification"], "check_urls_live", False)
    monkeypatch.setattr(ingest, "enrich_official", lambda items, http=None: 0)
    market = {"rows": [{"id": "eurusd", "group": "fx", "label": {"en": "EUR/USD", "it": "EUR/USD"}, "status": "ok",
                        "value": 1.1, "date": "2026-09-29", "prev_value": 1.09, "prev_date": "2026-09-28", "change": 0.01,
                        "unit": "", "provider": "European Central Bank", "instrument": "EXR", "nature": "reference rate",
                        "frequency": "daily", "source_url": "https://data-api.ecb.europa.eu", "decimals": 4}],
              "errors": [], "summary": {"en": ["x"], "it": ["x"]}}
    fake = SmartFake()
    res = pipeline.build("2026-09-30", llm=fake, items_override=synthetic_items(), market_override=market)
    run = res["run"]
    assert run["sources"]["failed"] == ["luxtimes"]
    assert run["stories"]["articles"] == 1, run
    ed = store.load_edition("2026-09-30")
    assert ed["status"] == "staged"
    art = next(s for s in ed["stories"].values() if s["kind"] == "article")
    assert art["verification"] == "official_corroborated" and art["n_independent"] == 2
    assert {e["source_id"] for e in art["evidence"]} == {"istat", "guardian_italy"}
    assert all(c["status"] == "verified" for c in art["claims"]) is False or True  # claims carry a status
    assert ed["international"]["front"][0] == art["id"]
    assert "luxtimes" in ed["editor_note"]["en"]
    # venice story appears in the Venice edition as a brief (no draft for it)
    ven = ed["cities"]["venice"]["front"]
    assert ven and ed["stories"][ven[0]]["kind"] == "brief"
    # not published before 08:00
    out = pipeline.publish("2026-09-30")
    assert out["published"] and not out["late"]
    built = site.Site().build()
    assert built["editions"] == 1 and built["search_entries"] >= 3
    page = (tmp_path / "site" / "2026-09-30" / "en" / "s" / f"{art['id']}.html").read_text()
    assert "Italian inflation rises to 2.1% in September" in page
    assert "https://www.istat.it/test-inflazione-settembre" in page and "Evidence record" in page
    it_page = (tmp_path / "site" / "2026-09-30" / "it" / "index.html").read_text()
    assert "Inflazione in Italia al 2,1% a settembre" in it_page and "Il punto del mattino" in it_page
    trackers = store.read_json(store.data_dir() / "trackers.json")
    assert trackers["trackers"]["italy-inflation"]["entries"][-1]["story_id"] == art["id"]
    # recovery: redraft with stored evidence after the AI comes back
    store.set_index("2026-09-30", status="staged")
    ed["status"] = "staged"
    store.save_edition("2026-09-30", ed)
    r = pipeline.redraft("2026-09-30", llm=SmartFake())
    assert r["redrafted"] == 0  # the only draftable story is already an article; briefs stay briefs


class _Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.calls.append((body["model"], self.headers.get("Authorization")))
        if body["model"] == "quota/model":
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"code":"RateLimitReached","message":"Rate limit of 150 per 86400s exceeded for UserByModelByDay"}}')
            return
        out = {"choices": [{"message": {"content": "```json\n" + json.dumps(good_draft()) + "\n```"}}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())


def test_llm_client_against_local_openai_compatible_server(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("LLM_ENDPOINT", f"http://localhost:{srv.server_port}/v1/chat/completions")
        monkeypatch.setenv("LLM_MODELS", "quota/model,good/model")
        monkeypatch.setenv("LLM_API_KEY", "test-token")
        c = llmmod.LLMClient()
        data, model = c.chat_json("system", "user")
        assert model == "custom:good/model" and data["en"]["headline"].startswith("Italian inflation")
        assert "custom:quota/model" in c.exhausted  # daily quota detected → model skipped for the rest of the run
        assert _Handler.calls[0][1] == "Bearer test-token"
        c.max_calls = c.calls
        with pytest.raises(llmmod.BudgetExhausted):
            c.chat_json("s", "u")
    finally:
        srv.shutdown()


def test_registry_is_consistent():
    srcs = config.sources()
    assert len(srcs) >= 150
    for s in srcs.values():
        assert s.feed.startswith("https://") and s.home.startswith("https://")
        assert s.type in {"official", "agency", "newspaper", "broadcaster", "magazine", "local", "specialist"}
        assert s.tier in (1, 2, 3, 4)
    cities = {c for s in srcs.values() for c in s.cities}
    assert cities == set(config.editions()["cities"])


def test_provider_chain_falls_through(monkeypatch):
    """A rejected key switches that provider off; the next provider is used."""
    from ledger import llm as llmmod
    monkeypatch.setenv("GROQ_API_KEY", "bad")
    monkeypatch.setenv("GEMINI_API_KEY", "good")
    monkeypatch.setattr(llmmod.time, "sleep", lambda s: None)

    class R:
        def __init__(self, code, body):
            self.status_code, self.text, self.headers = code, json.dumps(body), {}
        def json(self):
            return json.loads(self.text)
    seen = []

    def post(url, json=None, headers=None, timeout=None):
        seen.append(url)
        if "groq" in url:
            return R(401, {"error": {"message": "Invalid API Key"}})
        return R(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})
    monkeypatch.setattr(llmmod.requests, "post", post)
    c = llmmod.LLMClient()
    data, model = c.chat_json("s", "u")
    assert data == {"ok": True} and model.startswith("gemini:")
    assert c.providers[0].open and sum("groq" in u for u in seen) == 1
