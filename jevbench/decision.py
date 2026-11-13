"""Decision-model clients (Jev, SubQ Snap, Gemini Decision) behind one interface.

All three answer typed questions about a context with probabilities. We only
use one question type here: a `choice` over tool names. Raw HTTP on a
keep-alive client for each provider, so every provider is timed the same way.
"""

from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

from .config import env


class FatalProviderError(RuntimeError):
    """Auth, billing or access failure: stop the run, don't retry."""


@dataclass
class Decision:
    probabilities: dict[str, float]
    choice: str | None
    confidence: float | None
    latency_s: float
    input_tokens: int = 0
    model: str = ""
    raw: dict = field(default_factory=dict)


FATAL = {401, 402, 403}
QUESTION_KEY = "next_tool"


class DecisionClient:
    provider = "base"

    def __init__(self, model: str, timeout: float = 30.0):
        self.model = model
        self._local = threading.local()
        self.timeout = timeout

    # one keep-alive client per thread (httpx.Client is not thread-safe to share for streaming)
    @property
    def http(self) -> httpx.Client:
        c = getattr(self._local, "c", None)
        if c is None:
            c = httpx.Client(timeout=self.timeout, headers=self.headers())
            self._local.c = c
        return c

    def headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def url(self) -> str:
        raise NotImplementedError

    def body(self, state: str, questions: dict) -> dict:
        return {"model": self.model, "state": state, "questions": questions}

    def parse(self, data: dict) -> tuple[dict, str | None, float | None, int]:
        ans = (data.get("answers") or {}).get(QUESTION_KEY) or {}
        if ans.get("type") == "refusal":
            return {}, None, None, 0
        usage = data.get("usage") or {}
        tokens = usage.get("input_tokens") or usage.get("total_input_tokens") or 0
        return ans.get("probabilities") or {}, ans.get("choice"), ans.get("confidence"), int(tokens)

    def choose(self, state: str, instructions: str, options: dict[str, str], retries: int = 5) -> Decision:
        questions = {QUESTION_KEY: {"type": "choice", "instructions": instructions, "criteria": options}}
        payload = self.body(state, questions)
        last = None
        for attempt in range(retries + 1):
            t0 = time.monotonic()
            try:
                r = self.http.post(self.url(), json=payload)
            except httpx.HTTPError as e:
                last = e
                time.sleep(0.5 * (attempt + 1))
                continue
            dt = time.monotonic() - t0
            if r.status_code in FATAL:
                raise FatalProviderError(f"{self.provider} HTTP {r.status_code}: {r.text[:300]}")
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"{self.provider} HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(min(30.0, 1.5 * 2 ** attempt))   # 1.5, 3, 6, 12, 24 s
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{self.provider} HTTP {r.status_code}: {r.text[:500]}")
            data = r.json()
            probs, choice, conf, tokens = self.parse(data)
            return Decision(probs, choice, conf, dt, tokens, data.get("model", self.model), data)
        raise RuntimeError(f"{self.provider} failed after retries: {last}")


def jev_direct() -> bool:
    """TypeSafe direct when JEV_BASE_URL is set, or when only a TypeSafe key is present."""
    return bool(env("JEV_BASE_URL")) or (bool(env("TYPESAFE_API_KEY")) and not env("OPENROUTER_API_KEY"))


class JevClient(DecisionClient):
    """Jev via TypeSafe's own API (POST https://api.typesafe.ai/v1/systemone) or via
    OpenRouter's Decisions API. Model ids differ: TypeSafe `jev-1.14.0`, OpenRouter
    `typesafe/jev-1.14`."""

    provider = "jev"

    def __init__(self, model: str, timeout: float = 30.0):
        super().__init__(model, timeout)
        self.direct = jev_direct()
        if self.direct:
            m = model.split("/", 1)[1] if model.startswith("typesafe/") else model
            if re.fullmatch(r"jev-\d+\.\d+", m):
                m += ".0"  # TypeSafe versions are x.y.z
            self.model = env("JEV_MODEL") or m

    def headers(self):
        key = env("TYPESAFE_API_KEY") if self.direct else env("OPENROUTER_API_KEY")
        if not key:
            raise FatalProviderError("Jev needs TYPESAFE_API_KEY (direct) or OPENROUTER_API_KEY")
        return {**super().headers(), "Authorization": f"Bearer {key}"}

    def url(self):
        if self.direct:
            return (env("JEV_BASE_URL") or "https://api.typesafe.ai").rstrip("/") + "/v1/systemone"
        return "https://openrouter.ai/api/alpha/decisions"


class SubQClient(DecisionClient):
    provider = "subq"

    def headers(self):
        key = env("SUBQ_API_KEY")
        if not key:
            raise FatalProviderError("SubQ needs SUBQ_API_KEY")
        return {**super().headers(), "Authorization": f"Bearer {key}"}

    def url(self):
        return "https://api.subq.ai/v1/systemone"


class GeminiDecisionClient(DecisionClient):
    provider = "gemini_decision"

    def headers(self):
        key = env("GEMINI_API_KEY")
        if not key:
            raise FatalProviderError("Gemini Decision needs GEMINI_API_KEY")
        return {**super().headers(), "x-goog-api-key": key}

    def url(self):
        return "https://generativelanguage.googleapis.com/v1beta/decisions"

    def body(self, state, questions):
        model = self.model if self.model.startswith("models/") else f"models/{self.model}"
        return {"model": model, "input": state, "questions": questions}


class MockDecisionClient(DecisionClient):
    """Offline stand-in: scores options by word overlap with the state. For smoke tests."""

    provider = "mock"

    def choose(self, state, instructions, options, retries=0):
        t0 = time.monotonic()
        words = set(re.findall(r"[a-z]{3,}", state.lower()))
        scores = {}
        for k, desc in options.items():
            toks = set(re.findall(r"[a-z]{3,}", (k + " " + desc).lower().replace("_", " ")))
            scores[k] = len(words & toks) + random.random() * 0.1
        total = sum(2.0 ** s for s in scores.values()) or 1.0
        probs = {k: (2.0 ** s) / total for k, s in scores.items()}
        best = max(probs, key=probs.get)
        time.sleep(0.02)
        return Decision(probs, best, probs[best], time.monotonic() - t0, len(state) // 4, "mock")


def make_decision_client(name: str, cfg: dict) -> DecisionClient | None:
    if name in (None, "none"):
        return None
    model = (cfg.get("decision_providers", {}).get(name) or {}).get("model", "")
    cls = {
        "jev": JevClient,
        "subq": SubQClient,
        "gemini_decision": GeminiDecisionClient,
        "mock": MockDecisionClient,
    }.get(name)
    if cls is None:
        raise ValueError(f"unknown decision model {name}")
    return cls(model or name)
