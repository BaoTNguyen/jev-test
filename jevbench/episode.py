"""The metrics of one episode, and the generic tool loop used by MedAgentBench and
MCPToolBench++ (tau-bench drives its own loop and reuses the Recorder).

Clock: the "agent clock" only advances while a decision model or the agent LLM
is working. Tool execution, the tau user simulator and judges are excluded,
the same rule as the time-to-first-tool-call repo (mock execution off the clock).

  TTFTC   agent-clock seconds until the first tool call is emitted (right or wrong)
  TTFCTC  agent-clock seconds until the first call the benchmark's answer key accepts
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .config import price
from .decision import FatalProviderError
from .llm import LLM, Call, TurnResult
from .router import Router
from .telemetry import span
from .tools import ESCAPE_HATCH, ToolSpec


@dataclass
class Recorder:
    cfg: dict
    llm_model: str
    decision_model: str = ""
    sink: object = None
    episode_id: str = ""
    clock: float = 0.0
    ttftc: float | None = None
    ttfctc: float | None = None
    calls_before_correct: int | None = None
    n_calls: int = 0
    wrong_calls: int = 0
    distractor_calls: int = 0
    n_llm_calls: int = 0
    n_decision_calls: int = 0
    decision_time: float = 0.0
    llm_time: float = 0.0
    first_chunk_times: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    decision_tokens: int = 0
    cost: float = 0.0
    hatch_used: bool = False
    shortlists: list = field(default_factory=list)
    gold_tools: set = field(default_factory=set)
    routed_tools: set = field(default_factory=set)
    calls: list = field(default_factory=list)
    llm_errors: list = field(default_factory=list)
    t_wall0: float = field(default_factory=time.monotonic)

    def event(self, kind, provider, latency, **data):
        if self.sink:
            self.sink.event(self.episode_id, kind, provider, latency, data)

    def on_route(self, r, surface_size: int):
        self.n_decision_calls += r.calls
        self.decision_time += r.latency_s
        self.clock += r.latency_s
        self.decision_tokens += r.input_tokens
        self.cost += price(self.cfg, self.decision_model, r.input_tokens, 0)
        self.shortlists.append(r.selected)
        self.routed_tools.update(r.selected)
        self.event("decision", self.decision_model, r.latency_s, selected=r.selected, top=r.top,
                   confidence=r.confidence, surface=surface_size, calls=r.calls,
                   gold_in_shortlist=bool(self.gold_tools & set(r.selected)) if self.gold_tools else None)

    def on_turn(self, tr: TurnResult, n_tools: int) -> float:
        """Advance the clock by one LLM turn; returns the clock value at the turn's first call."""
        start = self.clock
        self.n_llm_calls += 1
        self.llm_time += tr.duration
        self.clock += tr.duration
        if tr.t_first_chunk is not None:
            self.first_chunk_times.append(tr.t_first_chunk)
        self.input_tokens += tr.input_tokens
        self.output_tokens += tr.output_tokens
        self.cached_tokens += tr.cached_tokens
        self.cost += price(self.cfg, self.llm_model, tr.input_tokens, tr.output_tokens)
        if tr.error:
            self.llm_errors.append(tr.error)
        self.event("llm", self.llm_model, tr.duration, tools=n_tools, first_chunk=tr.t_first_chunk,
                   first_call=tr.t_first_call, calls=[c.name for c in tr.calls], in_tok=tr.input_tokens,
                   out_tok=tr.output_tokens, cached=tr.cached_tokens, error=tr.error, retries=tr.retries)
        return start + (tr.t_first_call if tr.t_first_call is not None else tr.duration)

    def on_call(self, call: Call, at: float, correct: bool, executable: bool = True):
        if call.name == ESCAPE_HATCH.name:
            self.hatch_used = True
            self.event("hatch", "", 0.0, args=call.args)
            return
        if self.ttftc is None:
            self.ttftc = at
        if correct and self.ttfctc is None:
            self.ttfctc = at
            self.calls_before_correct = self.n_calls
        self.n_calls += 1
        if not correct:
            self.wrong_calls += 1
        if not executable:
            self.distractor_calls += 1
        self.calls.append({"name": call.name, "args": call.args, "t": round(at, 4), "correct": correct, "exec": executable})
        self.event("tool_call", call.name, 0.0, correct=correct, executable=executable, t=at)

    def recall(self) -> float | None:
        if not self.gold_tools or not self.shortlists:
            return None
        return len(self.gold_tools & self.routed_tools) / len(self.gold_tools)

    def metrics(self) -> dict:
        return dict(
            ttftc_s=self.ttftc, ttfctc_s=self.ttfctc, agent_time_s=self.clock,
            wall_time_s=time.monotonic() - self.t_wall0, decision_time_s=self.decision_time,
            llm_time_s=self.llm_time,
            first_byte_s=(sum(self.first_chunk_times) / len(self.first_chunk_times)) if self.first_chunk_times else None,
            n_decision_calls=self.n_decision_calls, n_llm_calls=self.n_llm_calls, n_tool_calls=self.n_calls,
            calls_before_correct=self.calls_before_correct, wrong_calls=self.wrong_calls,
            distractor_calls=self.distractor_calls, hatch_used=self.hatch_used, shortlist_recall=self.recall(),
            avg_shortlist_size=(sum(len(s) for s in self.shortlists) / len(self.shortlists)) if self.shortlists else None,
            input_tokens=self.input_tokens + self.decision_tokens, output_tokens=self.output_tokens,
            cached_tokens=self.cached_tokens, cost_usd=self.cost,
        )


@dataclass
class ToolTask:
    """A single-agent task: one user request, a tool surface, an answer key."""

    bench: str
    group: str
    task_id: str
    system: str
    user_text: str
    surface: str
    tools: list[ToolSpec]
    execute: Callable[[Call], object]               # runs an executable tool (off the clock)
    is_correct_call: Callable[[Call], bool]         # the answer key for a single call
    finalize: Callable[[list[dict], str], tuple[float, dict]]  # (score 0..1, details)
    gold_tools: set = field(default_factory=set)
    stop_tools: set = field(default_factory=set)    # e.g. MedAgentBench "finish"
    route_state: str = ""                           # what the decision model reads


@dataclass
class TaskRef:
    """What the runner schedules. `build()` makes a fresh task (fresh per-episode state)
    for every job, so the same task can run under every LLM x decision model x rep."""

    bench: str
    group: str
    task_id: str
    surface: str
    n_tools: int
    build: Callable[[], object]
    kind: str = "tool"      # "tool" -> run_tool_episode, "tau" -> tau-bench orchestrator


def run_tool_episode(task: ToolTask, llm: LLM, router: Router | None, rec: Recorder, max_turns: int) -> tuple[float, str, dict]:
    """Returns (score, status, details)."""
    conv = llm.conversation(task.system)
    conv.add_user(task.user_text)
    rec.gold_tools = set(task.gold_tools)
    by_name = {t.name: t for t in task.tools}
    sticky: set[str] = set()
    current = task.tools
    if router is not None and not router.should_route(task.tools):
        router = None
    if router is not None:
        with span("gen_ai.decision", f"route {rec.decision_model}"):
            r = router.route(task.route_state or task.user_text, task.tools)
        rec.on_route(r, len(task.tools))
        current = router.surface(task.tools, r.selected, sticky, task.stop_tools)

    final_text, status = "", "max_turns"
    for _ in range(max_turns):
        with span("gen_ai.chat", f"{llm.model} turn"):
            tr = conv.step(current)
        at = rec.on_turn(tr, len(current))
        if tr.error and not tr.calls:
            status = "llm_error"
            break
        if not tr.calls:
            final_text, status = tr.text, "answered"
            break
        results, stop = [], False
        for i, call in enumerate(tr.calls):
            t_call = at if i == 0 else rec.clock
            if call.name == ESCAPE_HATCH.name:
                rec.on_call(call, t_call, False)
                current = task.tools
                results.append((call, {"result": "You now have the full tool list."}))
                continue
            spec = by_name.get(call.name)
            executable = bool(spec and spec.executable)
            correct = executable and task.is_correct_call(call)
            rec.on_call(call, t_call, correct, executable)
            sticky.add(call.name)
            if call.name in task.stop_tools:
                stop = True
                results.append((call, {"result": "Finished."}))
                continue
            if not executable:
                results.append((call, {"error": f"Tool '{call.name}' is not available in this environment."}))
                continue
            with span("gen_ai.execute_tool", call.name):
                results.append((call, task.execute(call)))
        if stop:
            status = "finished"
            break
        conv.add_tool_results(results)
        if router is not None and current is not task.tools:
            # keep newly used tools visible on the next turn
            current = router.surface(task.tools, [t.name for t in current], sticky, task.stop_tools)
    score, details = task.finalize(rec.calls, final_text)
    details["final_text"] = (final_text or "")[:2000]
    return score, status, details


__all__ = ["Recorder", "ToolTask", "run_tool_episode", "FatalProviderError"]
