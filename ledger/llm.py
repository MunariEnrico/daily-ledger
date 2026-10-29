"""LLM client for OpenAI-compatible chat-completions endpoints.

The client walks a chain of free providers, each with its own API key and its
own models (every model has its own free daily quota):

    Groq        https://api.groq.com/openai/v1            GROQ_API_KEY
    Gemini      https://generativelanguage.googleapis.com/v1beta/openai   GEMINI_API_KEY
    Cerebras    https://api.cerebras.ai/v1                CEREBRAS_API_KEY

Providers without a key are skipped. A provider that keeps failing is switched
off for the rest of the run (circuit breaker) and the next one is used; daily
quota exhaustion moves on to the next model. LLM_ENDPOINT / LLM_MODELS /
LLM_API_KEY define a single custom provider instead (e.g. a local Ollama).

The model is a drafting tool only. Nothing it writes is published unless the
deterministic gate in validate.py can trace every checkable atom to evidence."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass

import requests

from . import config

log = logging.getLogger("ledger.llm")


class LLMError(Exception):
    pass


class BudgetExhausted(LLMError):
    pass


@dataclass
class Provider:
    name: str
    endpoint: str
    token: str
    models: list
    failures: int = 0       # consecutive failures
    open: bool = False      # circuit open: skipped for the rest of the run


def configured_providers(cfg: dict) -> list[Provider]:
    if os.environ.get("LLM_ENDPOINT"):
        models = [m.strip() for m in os.environ.get("LLM_MODELS", "").split(",") if m.strip()]
        token = os.environ.get("LLM_API_KEY") or os.environ.get("GITHUB_TOKEN", "")
        return [Provider("custom", os.environ["LLM_ENDPOINT"], token, models)]
    out = []
    for p in cfg.get("providers", []):
        token = os.environ.get(p["key_env"], "").strip()
        if token or "localhost" in p["endpoint"]:
            out.append(Provider(p["name"], p["endpoint"], token, list(p["models"])))
    return out


class LLMClient:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or config.settings()["llm"]
        self.providers = configured_providers(self.cfg)
        self.max_calls = int(self.cfg.get("max_calls_per_run", 100))
        self.max_tokens = int(self.cfg.get("max_output_tokens", 3000))
        self.calls = 0
        self.failures = 0
        self.by_model: dict[str, int] = {}
        self.exhausted: set[str] = set()
        self.errors: list[str] = []
        self._lock = threading.Lock()
        self._no_json_mode: set[str] = set()
        self.circuit_threshold = int(self.cfg.get("circuit_breaker_failures", 4))

    @property
    def circuit_open(self) -> bool:
        return bool(self.providers) and all(p.open for p in self.providers)

    @property
    def consecutive_failures(self) -> int:
        return max((p.failures for p in self.providers), default=0)

    @property
    def available(self) -> bool:
        if os.environ.get("LEDGER_LLM", "").lower() in ("off", "0", "false", "disabled"):
            return False
        return bool(self.cfg.get("enabled", True)) and bool(self.providers) and not self.circuit_open

    def _note(self, msg: str):
        with self._lock:
            self.errors.append(msg[:400])
            log.warning("llm: %s", msg[:400])

    def _fail(self, prov: Provider, msg: str):
        self._note(msg)
        with self._lock:
            prov.failures += 1
            if prov.failures >= self.circuit_threshold:
                prov.open = True
                self.errors.append(f"{prov.name}: switched off for this run after {prov.failures} consecutive failures")

    def _reserve(self):
        with self._lock:
            if self.calls >= self.max_calls:
                raise BudgetExhausted(f"LLM call budget of {self.max_calls} reached")
            self.calls += 1

    def chat_json(self, system: str, user: str, max_tokens: int | None = None) -> tuple[dict, str]:
        """Return (parsed JSON object, "provider:model"). Walks providers and
        models in order. Raises LLMError when nothing works."""
        if not self.providers:
            raise LLMError("no AI provider configured (add GROQ_API_KEY, GEMINI_API_KEY or CEREBRAS_API_KEY)")
        if self.circuit_open:
            raise LLMError("all AI providers switched off after repeated failures; last: " + (self.errors[-2] if len(self.errors) > 1 else ""))
        max_tokens = max_tokens or self.max_tokens
        last = ""
        for prov in self.providers:
            for model in prov.models:
                key = f"{prov.name}:{model}"
                if prov.open:
                    break
                if key in self.exhausted:
                    continue
                for attempt in range(3):
                    self._reserve()
                    try:
                        content = self._post(prov, model, system, user, max_tokens)
                        data = parse_json_object(content)
                        with self._lock:
                            self.by_model[key] = self.by_model.get(key, 0) + 1
                            prov.failures = 0
                        return data, key
                    except _RateLimited as rl:
                        last = f"{key}: rate limited ({rl.detail})"
                        if rl.daily or rl.wait > 90:
                            self.exhausted.add(key)
                            self._note(last + " → model skipped for this run")
                            break
                        time.sleep(max(1.0, rl.wait))
                    except _Unavailable as ua:
                        last = f"{key}: {ua}"
                        self.exhausted.add(key)
                        self._note(last)
                        if ua.whole_provider:
                            prov.open = True
                        break
                    except (requests.RequestException, LLMError, ValueError) as ex:
                        last = f"{key}: {type(ex).__name__}: {str(ex)[:300]}"
                        self._fail(prov, last)
                        if prov.open:
                            break
                        time.sleep(1.5 * (attempt + 1))
                with self._lock:
                    self.failures += 1
        raise LLMError(last or "all models failed")

    def _post(self, prov: Provider, model: str, system: str, user: str, max_tokens: int) -> str:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": float(self.cfg.get("temperature", 0.1)),
            "max_tokens": max_tokens,
        }
        key = f"{prov.name}:{model}"
        if key not in self._no_json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if prov.token:
            headers["Authorization"] = f"Bearer {prov.token}"
        r = requests.post(prov.endpoint, json=body, headers=headers, timeout=float(self.cfg.get("timeout_s", 120)))
        text = r.text or ""
        low = text.lower()
        if r.status_code == 429:
            ra = r.headers.get("Retry-After") or ""
            wait = float(ra) if re.fullmatch(r"\d+(\.\d+)?", ra) else 20.0
            m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", low)
            if m:
                wait = 60 * int(m.group(1) or 0) + float(m.group(2))
            daily = any(k in low for k in ("per day", "tokens per day", "requests per day", "(tpd)", "(rpd)", "daily", "perday", "byday", "86400"))
            raise _RateLimited(wait, daily=daily, detail=text[:200])
        if r.status_code == 400 and ("response_format" in low or "json_object" in low or "json mode" in low) \
                and key not in self._no_json_mode:
            self._no_json_mode.add(key)
            return self._post(prov, model, system, user, max_tokens)
        if r.status_code in (401, 403) or (r.status_code == 400 and "api key" in low):
            raise _Unavailable(f"HTTP {r.status_code}: key rejected — {text[:160]}", whole_provider=True)
        if r.status_code in (404, 410, 422) or (r.status_code == 400 and ("model" in low and ("not found" in low or "does not exist" in low or "decommissioned" in low))):
            raise _Unavailable(f"HTTP {r.status_code}: model unavailable — {text[:160]}")
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {text[:240]}")
        try:
            data = r.json()
        except ValueError:
            raise LLMError(f"HTTP {r.status_code} non-JSON response (content-type {r.headers.get('Content-Type')}): {text[:200]!r}")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as ex:
            raise LLMError(f"unexpected response shape: {str(data)[:240]}") from ex
        if not content:
            raise LLMError(f"empty content (finish_reason={data['choices'][0].get('finish_reason')})")
        return content

    def stats(self) -> dict:
        return {"calls": self.calls, "failures": self.failures, "by_model": self.by_model, "circuit_open": self.circuit_open,
                "providers": [{"name": p.name, "models": p.models, "switched_off": p.open} for p in self.providers],
                "exhausted_models": sorted(self.exhausted), "errors": self.errors[-15:], "budget": self.max_calls}


class _RateLimited(Exception):
    def __init__(self, wait: float, daily: bool, detail: str):
        super().__init__(detail)
        self.wait, self.daily, self.detail = wait, daily, detail


class _Unavailable(Exception):
    def __init__(self, msg: str, whole_provider: bool = False):
        super().__init__(msg)
        self.whole_provider = whole_provider


def parse_json_object(content: str) -> dict:
    s = (content or "").strip()
    s = re.sub(r"(?is)<think>.*?</think>", "", s).strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise ValueError("model did not return JSON")
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("model JSON is not an object")
    return obj


class ScriptedLLM:
    """Test double: returns queued responses (dicts, or exceptions to raise)."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []
        self.available = True

    def chat_json(self, system: str, user: str, max_tokens: int = 3500):
        self.calls += 1
        self.prompts.append(user)
        if not self.responses:
            raise LLMError("no scripted response left")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return json.loads(json.dumps(r)), "scripted"

    def stats(self):
        return {"calls": self.calls, "failures": 0, "by_model": {"scripted": self.calls}, "exhausted_models": [],
                "errors": [], "budget": None}
