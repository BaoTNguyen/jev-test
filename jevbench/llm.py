"""Agent LLMs: streaming Gemini with per-turn timestamps, plus an offline mock.

Each episode gets a Conversation that owns its own history in the provider's
native format (Gemini needs its own Content parts back, thought signatures
included, for multi-turn function calling).
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from .config import env
from .decision import FatalProviderError
from .tools import ToolSpec


@dataclass
class Call:
    id: str
    name: str
    args: dict


@dataclass
class TurnResult:
    calls: list[Call]
    text: str
    t_first_chunk: float | None     # seconds from request send
    t_first_call: float | None      # seconds from request send to the first function call part
    duration: float                 # seconds from send to stream end
    input_tokens: int = 0
    output_tokens: int = 0          # includes thinking tokens
    cached_tokens: int = 0
    thinking_tokens: int = 0
    error: str | None = None
    retries: int = 0


class Conversation:
    def add_user(self, text: str): ...
    def add_tool_results(self, results: list[tuple[Call, object]]): ...
    def step(self, tools: list[ToolSpec]) -> TurnResult: ...


# ----------------------------------------------------------------------------- Gemini

_clients: dict[int, object] = {}
_lock = threading.Lock()


def _genai_client():
    from google import genai

    tid = threading.get_ident()
    with _lock:
        c = _clients.get(tid)
        if c is None:
            key = env("GEMINI_API_KEY")
            if not key:
                raise FatalProviderError("GEMINI_API_KEY is not set")
            c = genai.Client(api_key=key)
            _clients[tid] = c
    return c


class GeminiConversation(Conversation):
    def __init__(self, model: str, system: str, thinking_level: str | None = None, max_output_tokens: int = 8192):
        from google.genai import types

        self.types = types
        self.model = model
        self.system = system
        self.thinking_level = thinking_level
        self.max_output_tokens = max_output_tokens
        self.contents: list = []
        self._decl_cache: dict[str, object] = {}
        self._provider_ids: set[str] = set()   # only echo ids Gemini issued itself

    def add_user(self, text: str):
        t = self.types
        self.contents.append(t.Content(role="user", parts=[t.Part.from_text(text=text or " ")]))

    def add_tool_results(self, results):
        t = self.types
        parts = []
        for call, result in results:
            payload = result if isinstance(result, dict) else {"result": result}
            fid = call.id if call.id in self._provider_ids else None
            parts.append(t.Part(function_response=t.FunctionResponse(id=fid, name=call.name, response=payload)))
        if parts:
            self.contents.append(t.Content(role="user", parts=parts))

    def _decl(self, tool: ToolSpec):
        d = self._decl_cache.get(tool.name)
        if d is None:
            d = self.types.FunctionDeclaration(
                name=tool.name,
                description=(tool.description or "")[:4000],
                parameters_json_schema=tool.parameters,
            )
            self._decl_cache[tool.name] = d
        return d

    def step(self, tools):
        t = self.types
        cfg = dict(
            system_instruction=self.system,
            max_output_tokens=self.max_output_tokens,
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
        )
        if tools:
            cfg["tools"] = [t.Tool(function_declarations=[self._decl(x) for x in tools])]
        if self.thinking_level:
            cfg["thinking_config"] = t.ThinkingConfig(thinking_level=self.thinking_level.upper())
        client = _genai_client()

        for attempt in range(6):
            tr = self._attempt(client, t.GenerateContentConfig(**cfg))
            retryable = tr.error and tr.t_first_chunk is None and any(
                c in tr.error for c in ("429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
            if not retryable or attempt == 5:
                tr.retries = attempt
                return tr
            time.sleep(1.5 * 2 ** attempt)  # off the clock: only the successful attempt is timed
        return tr

    def _attempt(self, client, config) -> TurnResult:
        t = self.types
        parts, calls, texts = [], [], []
        usage = None
        t_first_chunk = t_first_call = None
        t0 = time.monotonic()
        try:
            stream = client.models.generate_content_stream(
                model=self.model, contents=self.contents, config=config
            )
            for chunk in stream:
                now = time.monotonic() - t0
                if t_first_chunk is None:
                    t_first_chunk = now
                if chunk.usage_metadata:
                    usage = chunk.usage_metadata
                cand = (chunk.candidates or [None])[0]
                if not cand or not cand.content or not cand.content.parts:
                    continue
                for p in cand.content.parts:
                    parts.append(p)
                    if p.function_call:
                        if t_first_call is None:
                            t_first_call = now
                        fc = p.function_call
                        if fc.id:
                            self._provider_ids.add(fc.id)
                        calls.append(Call(fc.id or f"call_{uuid.uuid4().hex[:10]}", fc.name, dict(fc.args or {})))
                    elif p.text and not p.thought:
                        texts.append(p.text)
        except Exception as e:  # noqa: BLE001 - classify provider errors
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if code in (401, 402, 403):
                raise FatalProviderError(f"Gemini {code}: {e}") from e
            return TurnResult([], "", t_first_chunk, t_first_call, time.monotonic() - t0, error=f"{type(e).__name__}: {e}"[:500])
        duration = time.monotonic() - t0
        if parts:
            # keep the parts exactly as returned so thought signatures go back with them
            self.contents.append(t.Content(role="model", parts=parts))
        u = usage
        return TurnResult(
            calls, "".join(texts), t_first_chunk, t_first_call, duration,
            input_tokens=getattr(u, "prompt_token_count", 0) or 0,
            output_tokens=(getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0),
            cached_tokens=getattr(u, "cached_content_token_count", 0) or 0,
            thinking_tokens=getattr(u, "thoughts_token_count", 0) or 0,
        )


# ----------------------------------------------------------------------------- mock

class MockConversation(Conversation):
    """Offline LLM for smoke tests: calls the tool whose name best matches the request,
    fills required args from words in the request, then answers in text."""

    def __init__(self, system: str):
        self.system = system
        self.user_text = ""
        self.turns = 0

    def add_user(self, text):
        self.user_text += " " + (text or "")
        self.turns = 0

    def add_tool_results(self, results):
        pass

    def step(self, tools):
        self.turns += 1
        time.sleep(0.05 + random.random() * 0.05)
        real = [x for x in tools if x.name != "request_more_tools"]
        if self.turns > 1 or not real:
            return TurnResult([], "Done.", 0.04, None, 0.06, input_tokens=100, output_tokens=5)
        words = set(re.findall(r"[a-z]{3,}", self.user_text.lower()))
        best = max(real, key=lambda x: len(words & set(re.findall(r"[a-z]{3,}", (x.name + " " + x.description).lower().replace("_", " ")))) + random.random() * 0.01)
        args = {}
        for k in (best.parameters.get("required") or [])[:4]:
            args[k] = next(iter(words), "x")
        return TurnResult([Call(f"call_{uuid.uuid4().hex[:8]}", best.name, args)], "", 0.04, 0.05, 0.06,
                          input_tokens=200 + 50 * len(tools), output_tokens=20)


class LLM:
    """Factory for conversations with one configured agent model."""

    def __init__(self, spec: dict):
        self.id = spec["id"]
        self.model = spec["model"]
        self.thinking_level = spec.get("thinking_level")

    def conversation(self, system: str) -> Conversation:
        if self.model == "mock":
            return MockConversation(system)
        return GeminiConversation(self.model, system, self.thinking_level)


def to_json_text(obj, limit: int = 60000) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "...(truncated)"
