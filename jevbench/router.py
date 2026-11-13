"""Turn decision-model probabilities into a tool shortlist.

Rule (same for every decision model): sort tools by probability, keep them
until they cover `mass` of the probability, with at least `min_k` and at most
`max_k`. Tools already used in the episode stay in ("sticky") so the LLM's
history never references a tool it can no longer see. Above `max_options`
tools, route in two stages: pick groups (server/domain) first, then tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decision import DecisionClient
from .tools import ESCAPE_HATCH, ToolSpec

INSTRUCTIONS = (
    "You are routing for an AI agent. Read the conversation and pick the single tool "
    "the agent should call next to make progress on the user's request."
)
GROUP_INSTRUCTIONS = (
    "You are routing for an AI agent. Pick the tool group (service) that contains the "
    "tool the agent should call next for the user's request."
)


@dataclass
class RouteResult:
    selected: list[str]
    probabilities: dict[str, float]
    latency_s: float
    calls: int
    input_tokens: int
    top: str | None = None
    confidence: float | None = None
    groups: list[str] = field(default_factory=list)


def pick(probs: dict[str, float], mass: float, min_k: int, max_k: int) -> list[str]:
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    out, acc = [], 0.0
    for name, p in ranked:
        if len(out) >= max_k:
            break
        if len(out) >= min_k and acc >= mass:
            break
        out.append(name)
        acc += p
    return out


class Router:
    def __init__(self, client: DecisionClient, mass=0.95, min_k=3, max_k=8, escape_hatch=True, max_options=255):
        self.client = client
        self.mass, self.min_k, self.max_k = mass, min_k, max_k
        self.escape_hatch = escape_hatch
        self.max_options = max_options

    def should_route(self, tools: list[ToolSpec]) -> bool:
        """Nothing to shortlist when the surface is already within min_k (decision APIs also
        need >= 2 options). Those episodes run exactly like the baseline, with no decision call."""
        return len(tools) > max(self.min_k, 1)

    def route(self, state: str, tools: list[ToolSpec]) -> RouteResult:
        if len(tools) <= self.max_options:
            d = self.client.choose(state, INSTRUCTIONS, {t.name: t.short() for t in tools})
            return RouteResult(
                pick(d.probabilities, self.mass, self.min_k, self.max_k),
                d.probabilities, d.latency_s, 1, d.input_tokens, d.choice, d.confidence,
            )
        # two-stage: groups, then tools inside the chosen groups
        groups: dict[str, list[ToolSpec]] = {}
        for t in tools:
            groups.setdefault(t.group or "default", []).append(t)
        gopts = {
            g: f"{g}: " + ", ".join(x.name for x in ts[:25]) for g, ts in list(groups.items())[: self.max_options]
        }
        g = self.client.choose(state, GROUP_INSTRUCTIONS, gopts)
        chosen = pick(g.probabilities, self.mass, 1, 3)
        cand = [t for name in chosen for t in groups.get(name, [])][: self.max_options]
        d = self.client.choose(state, INSTRUCTIONS, {t.name: t.short() for t in cand})
        return RouteResult(
            pick(d.probabilities, self.mass, self.min_k, self.max_k),
            d.probabilities, g.latency_s + d.latency_s, 2, g.input_tokens + d.input_tokens,
            d.choice, d.confidence, chosen,
        )

    def surface(self, full: list[ToolSpec], selected: list[str], sticky: set[str],
                pinned: set[str] = frozenset()) -> list[ToolSpec]:
        """`pinned` = the benchmark's answer/terminate tools (e.g. MedAgentBench `finish`):
        part of the answer format, never subject to routing."""
        keep = set(selected) | sticky | set(pinned)
        out = [t for t in full if t.name in keep]
        if self.escape_hatch:
            out.append(ESCAPE_HATCH)
        return out
