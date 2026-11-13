"""tau-bench (sierra-research/tau2-bench, MIT; text mode, airline / retail / telecom).

tau runs its own conversation loop: a user simulator LLM talks to the agent, the
agent calls domain tools, and the final database state + communicated facts are
graded. We register a custom agent ("jevbench_agent") that:
  * drives a streaming Gemini conversation (same client as the other benchmarks),
  * on every new user message, optionally asks the decision model for a tool
    shortlist over the surface (domain tools, or every tau domain's tools),
  * handles the escape hatch and distractor calls itself so the environment
    only ever sees real domain tool calls.

Clock: only the agent's decision + LLM time counts (user simulator excluded).
A call is "correct" when it matches one of the task's expected assistant actions
(tau's own Action.compare_with_tool_call).

The user simulator and the judges are fixed across conditions. By default the
judges are switched from tau's OpenAI models to `judge_llm` (Gemini) so a single
GEMINI_API_KEY is enough; set judge_llm: null to keep tau's defaults.
"""

from __future__ import annotations

import os
import random
import threading
import uuid
from dataclasses import dataclass

from ..config import env
from ..episode import Recorder, TaskRef
from ..llm import LLM, Call
from ..router import Router
from ..tools import ESCAPE_HATCH, ToolSpec, clean_schema, dedupe, pad

_CTX: dict[str, "_Ctx"] = {}
_registered = False
_reg_lock = threading.Lock()


@dataclass
class _Ctx:
    llm: LLM
    router: Router | None
    rec: Recorder
    extra: list[ToolSpec]
    gold_actions: list


@dataclass
class TauJob:
    domain: str
    task: object
    surface: str
    extra: list[ToolSpec]


def _specs(tools, domain: str, executable=True) -> list[ToolSpec]:
    out = []
    for t in tools:
        fn = t.openai_schema["function"]
        out.append(ToolSpec(name=fn["name"], description=fn.get("description", ""),
                            parameters=clean_schema(fn.get("parameters")), group=domain, executable=executable))
    return out


def _register():
    global _registered
    with _reg_lock:
        if _registered:
            return
        from tau2.agent.llm_agent import LLMAgent
        from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage, UserMessage
        from tau2.registry import registry

        class JevbenchAgent(LLMAgent):
            def __init__(self, tools, domain_policy, llm, llm_args, ctx: _Ctx):
                super().__init__(tools=tools, domain_policy=domain_policy, llm=llm, llm_args={})
                self.ctx = ctx
                native = _specs(tools, "native")
                names = {t.name for t in native}
                self.full = native + [t for t in ctx.extra if t.name not in names]
                self.by_name = {t.name: t for t in self.full}
                self.current = self.full
                self.sticky: set[str] = set()
                self.id2name: dict[str, str] = {}
                self.pending: list = []           # synthetic results waiting to go out with real ones
                self.transcript: list[str] = []
                self.conv = ctx.llm.conversation(self.system_prompt)

            def set_seed(self, seed):  # our client ignores tau's litellm seed
                pass

            def _route(self):
                r = self.ctx.router
                if r is None or not r.should_route(self.full):
                    return
                state = "\n".join(self.transcript[-12:])
                res = r.route(state, self.full)
                self.ctx.rec.on_route(res, len(self.full))
                self.current = r.surface(self.full, res.selected, self.sticky)

            def _is_correct(self, call: Call) -> bool:
                tc = ToolCall(id=call.id, name=call.name, arguments=call.args, requestor="assistant")
                return any(a.compare_with_tool_call(tc) for a in self.ctx.gold_actions)

            def _generate_next_message(self, message, state):
                rec = self.ctx.rec
                if isinstance(message, MultiToolMessage):
                    state.messages.extend(message.tool_messages)
                    tool_msgs = list(message.tool_messages)
                else:
                    state.messages.append(message)
                    tool_msgs = [message] if isinstance(message, ToolMessage) else []
                if isinstance(message, UserMessage):
                    self.transcript.append(f"User: {message.content}")
                    self.conv.add_user(message.content or "")
                    self._route()
                elif tool_msgs:
                    res = [(Call(m.id, self.id2name.get(m.id, ""), {}), {"result": m.content or ""}) for m in tool_msgs]
                    self.conv.add_tool_results(self.pending + res)
                    self.pending = []

                for _ in range(4):
                    tr = self.conv.step(self.current)
                    at = rec.on_turn(tr, len(self.current))
                    if tr.error and not tr.calls:
                        return AssistantMessage.text("Sorry, I hit a technical problem. Could you say that again?")
                    if not tr.calls:
                        text = tr.text.strip() or "Could you tell me a bit more about what you need?"
                        self.transcript.append(f"Agent: {text}")
                        return AssistantMessage.text(text, generation_time_seconds=tr.duration)
                    real, synth = [], []
                    for i, c in enumerate(tr.calls):
                        t_call = at if i == 0 else rec.clock
                        if c.name == ESCAPE_HATCH.name:
                            rec.on_call(c, t_call, False)
                            self.current = self.full
                            synth.append((c, {"result": "You now have the full tool list."}))
                            continue
                        spec = self.by_name.get(c.name)
                        if spec is None or not spec.executable:
                            rec.on_call(c, t_call, False, executable=False)
                            synth.append((c, {"error": f"Tool '{c.name}' is not available in this environment."}))
                            continue
                        rec.on_call(c, t_call, self._is_correct(c))
                        self.sticky.add(c.name)
                        real.append(c)
                    if real:
                        self.pending = synth
                        if self.ctx.router is not None and self.current is not self.full:
                            self.current = self.ctx.router.surface(self.full, [t.name for t in self.current], self.sticky)
                        for c in real:
                            self.id2name[c.id] = c.name
                        self.transcript.append("Agent called: " + ", ".join(c.name for c in real))
                        return AssistantMessage(
                            role="assistant", content=None,
                            tool_calls=[ToolCall(id=c.id, name=c.name, arguments=c.args, requestor="assistant") for c in real],
                            generation_time_seconds=tr.duration,
                        )
                    self.conv.add_tool_results(synth)
                return AssistantMessage.text("Sorry, I couldn't complete that step. Could you rephrase?")

        def factory(tools, domain_policy, **kw):
            key = (kw.get("llm_args") or {}).get("jevbench_key")
            ctx = _CTX[key]
            return JevbenchAgent(tools, domain_policy, kw.get("llm"), kw.get("llm_args"), ctx)

        registry.register_agent_factory(factory, "jevbench_agent")
        _registered = True


class TauBench:
    name = "tau"

    def __init__(self, bcfg: dict, cfg: dict, seed: int, distractor_pool: list[ToolSpec] | None = None):
        if env("TAU2_DATA_DIR"):
            os.environ.setdefault("TAU2_DATA_DIR", env("TAU2_DATA_DIR"))
        try:
            import tau2  # noqa: F401
        except ImportError as e:
            raise ImportError("tau2 not installed: run scripts/setup_benchmarks.sh") from e
        from tau2.registry import registry
        from tau2.runner.helpers import get_tasks

        self.bcfg, self.cfg = bcfg, cfg
        self.extra_pool = distractor_pool or []
        self.judge = bcfg.get("judge_llm")
        if self.judge:
            import tau2.evaluator.evaluator_nl_assertions as nl

            nl.DEFAULT_LLM_NL_ASSERTIONS = self.judge
            # tau parses the judge reply with a bare json.loads; Gemini fences it in ```json
            # unless JSON mode is on, which crashes grading for tasks with NL assertions.
            nl.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {"response_format": {"type": "json_object"}}
        _register()
        rng = random.Random(seed)
        self.domains = bcfg.get("domains", ["airline", "retail", "telecom"])
        self.domain_tools: dict[str, list[ToolSpec]] = {}
        for d in self.domains:
            envi = registry.get_env_constructor(d)()
            self.domain_tools[d] = _specs(envi.get_tools(), d)
        self.items = []
        for d in self.domains:
            try:
                tasks = get_tasks(d, task_split_name=bcfg.get("split", "test"))
            except Exception:  # noqa: BLE001 - domain without that split
                tasks = get_tasks(d)
            tasks = list(tasks)
            rng.shuffle(tasks)
            per = bcfg.get("per_domain", 10)
            n = per.get(d, 0) if isinstance(per, dict) else per
            self.items += [(d, t) for t in tasks[:n]]

    def pool(self) -> list[ToolSpec]:
        return dedupe([t for d in self.domains for t in self.domain_tools[d]])

    def tasks(self) -> list[TaskRef]:
        out = []
        for d, task in self.items:
            for surface in self.bcfg.get("surfaces", ["native"]):
                own = self.domain_tools[d]
                # other tau domains first (same industry = hard distractors), then the global pool
                others = [t for od in self.domains if od != d for t in self.domain_tools[od]]
                if surface == "native":
                    extra = []
                elif surface == "all_domains":
                    extra = pad(own, others, 10**6)[len(own):]
                elif surface.startswith("pad"):
                    extra = pad(own, others + self.extra_pool, int(surface[3:] or 100))[len(own):]
                else:
                    raise ValueError(f"tau: unknown surface {surface}")
                n = len(self.domain_tools[d]) + len(extra)
                out.append(TaskRef(self.name, d, f"{d}:{task.id}", surface, n, kind="tau",
                                   build=lambda d=d, t=task, s=surface, e=extra: TauJob(d, t, s, e)))
        return out

    def run(self, job: TauJob, llm: LLM, router: Router | None, rec: Recorder, seed: int):
        from tau2.data_model.simulation import TextRunConfig
        from tau2.runner.batch import run_single_task

        crit = job.task.evaluation_criteria
        gold = [a for a in (crit.actions or [])] if crit else []
        gold = [a for a in gold if getattr(a, "requestor", "assistant") == "assistant"]
        rec.gold_tools = {a.name for a in gold}
        key = uuid.uuid4().hex
        _CTX[key] = _Ctx(llm, router, rec, job.extra, gold)
        kw = {}
        if self.judge:
            kw["communicate_scoring"] = {"scorer": "semantic", "judge": {"model": self.judge, "model_args": {}}}
        try:
            config = TextRunConfig(
                domain=job.domain, agent="jevbench_agent", user="user_simulator",
                llm_agent=llm.model, llm_args_agent={"jevbench_key": key},
                llm_user=self.bcfg.get("user_llm", "gemini/gemini-3.8-flash"), llm_args_user={"temperature": 0.0, "num_retries": 6},
                max_steps=self.bcfg.get("max_steps", 60), max_errors=10, seed=seed, **kw,
            )
            sim = run_single_task(config, job.task, seed=seed)
        finally:
            _CTX.pop(key, None)
        reward = sim.reward_info.reward if sim.reward_info else None
        status = str(getattr(sim, "termination_reason", "") or "done")
        details = {"reward": reward, "termination": status,
                   "gold_actions": [a.name for a in gold], "n_messages": len(sim.messages or [])}
        return reward, status, details
