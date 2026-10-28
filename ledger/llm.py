"""LLM client for an OpenAI-compatible chat-completions endpoint.

Default: GitHub Models (free tier, authenticated with the GITHUB_TOKEN that
GitHub Actions injects — no paid key). Works equally with Groq, a local
Ollama / llama.cpp server, or any OpenAI-compatible endpoint.

The model is a drafting tool only. Nothing it writes is published unless the
deterministic gate in validate.py can trace every checkable atom to evidence."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import requests

from . import config

log = logging.getLogger("ledger.llm")


class LLMError(Exception):
    pass


class BudgetExhausted(LLMError):
    pass


class LLMClient:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or config.settings()["llm"]
        self.endpoint = os.environ.get("LLM_ENDPOINT", self.cfg["endpoint"])
        env_models = os.environ.get("LLM_MODELS")
        self.models = [m.strip() for m in env_models.split(",")] if env_models else list(self.cfg["models"])
        self.token = os.environ.get(self.cfg.get("token_env", "GITHUB_TOKEN"), "")
        self.max_calls = int(self.cfg.get("max_calls_per_run", 100))
        self.calls = 0
        self.failures = 0
        self.by_model: dict[str, int] = {}
        self.exhausted: set[str] = set()
        self.errors: list[str] = []
        self._lock = threading.Lock()
        self._no_json_mode: set[str] = set()
        self.consecutive_failures = 0
        self.circuit_open = False      # opened after repeated failures: stop spending quota on a broken service
        self.circuit_threshold = int(self.cfg.get("circuit_breaker_failures", 6))

    @property
    def available(self) -> bool:
        if os.environ.get("LEDGER_LLM", "").lower() in ("off", "0", "false", "disabled"):
            return False
        return bool(self.cfg.get("enabled", True)) and bool(self.token or "localhost" in self.endpoint) and not self.circuit_open

    def _fail(self):
        with self._lock:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.circuit_threshold:
                self.circuit_open = True

    def _reserve(self):
        with self._lock:
            if self.calls >= self.max_calls:
                raise BudgetExhausted(f"LLM call budget of {self.max_calls} reached")
            self.calls += 1

    def chat_json(self, system: str, user: str, max_tokens: int = 3500) -> tuple[dict, str]:
        """Return (parsed JSON object, model id). Falls through the model list on
        quota/availability errors. Raises LLMError when nothing works."""
        if self.circuit_open:
            raise LLMError(f"circuit open after {self.consecutive_failures} consecutive failures")
        if not self.available:
            raise LLMError("LLM not configured (no token)")
        last = ""
        for model in self.models:
            if model in self.exhausted:
                continue
            for attempt in range(3):
                self._reserve()
                try:
                    content = self._post(model, system, user, max_tokens)
                    data = parse_json_object(content)
                    with self._lock:
                        self.by_model[model] = self.by_model.get(model, 0) + 1
                        self.consecutive_failures = 0
                    return data, model
                except _RateLimited as rl:
                    last = f"{model}: rate limited ({rl.detail})"
                    if rl.daily or rl.wait > 60:
                        self.exhausted.add(model)
                        break
                    time.sleep(max(1.0, rl.wait))
                except _Unavailable as ua:
                    last = f"{model}: {ua}"
                    self.exhausted.add(model)
                    break
                except (requests.RequestException, LLMError, ValueError) as ex:
                    last = f"{model}: {type(ex).__name__}: {str(ex)[:200]}"
                    self._fail()
                    if self.circuit_open:
                        raise LLMError(f"circuit open after repeated failures; last: {last}")
                    time.sleep(1.5 * (attempt + 1))
            with self._lock:
                self.failures += 1
                self.errors.append(last)
        raise LLMError(last or "all models failed")

    def _post(self, model: str, system: str, user: str, max_tokens: int) -> str:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": float(self.cfg.get("temperature", 0.1)),
            "max_tokens": max_tokens,
        }
        if model not in self._no_json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        r = requests.post(self.endpoint, json=body, headers=headers, timeout=float(self.cfg.get("timeout_s", 90)))
        if r.status_code == 429:
            ra = r.headers.get("Retry-After") or r.headers.get("x-ratelimit-reset-requests") or ""
            wait = float(ra) if re.fullmatch(r"\d+(\.\d+)?", ra or "") else 20.0
            text = r.text.lower()
            raise _RateLimited(wait, daily=("per day" in text or "daily" in text or "userbymodelbyday" in text), detail=r.text[:160])
        if r.status_code == 400 and "response_format" in r.text and model not in self._no_json_mode:
            self._no_json_mode.add(model)
            return self._post(model, system, user, max_tokens)
        if r.status_code in (401, 403):
            raise _Unavailable(f"HTTP {r.status_code} (token lacks models access?) {r.text[:120]}")
        if r.status_code in (404, 422):
            raise _Unavailable(f"HTTP {r.status_code} model unavailable {r.text[:120]}")
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as ex:
            raise LLMError(f"unexpected response shape: {str(data)[:200]}") from ex

    def stats(self) -> dict:
        return {"calls": self.calls, "failures": self.failures, "by_model": self.by_model, "circuit_open": self.circuit_open,
                "exhausted_models": sorted(self.exhausted), "errors": self.errors[-10:], "budget": self.max_calls}


class _RateLimited(Exception):
    def __init__(self, wait: float, daily: bool, detail: str):
        super().__init__(detail)
        self.wait, self.daily, self.detail = wait, daily, detail


class _Unavailable(Exception):
    pass


def parse_json_object(content: str) -> dict:
    s = (content or "").strip()
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
