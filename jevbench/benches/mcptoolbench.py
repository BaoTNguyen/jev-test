"""MCPToolBench++ (mcp-tool-bench/MCPToolBenchPP, MIT), run in its offline AST mode.

Each item ships its tool schemas, the query and the labelled call(s). We don't
start the real MCP servers: calls are scored against the label, like the
benchmark's AST metric. Tool results are mocked (the label's output when the
call matches a label step, else a neutral ok), so the model can finish.

Scoring follows check_functions.py: tool correct if the name equals the label
or one of its `similar_tools`; parameters compared deterministically first,
then (optionally) with the benchmark's own LLM-judge prompt for fuzzy matches.
Success = the last call matches the last label step (their DAG rule).
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from ..config import vendor_dir
from ..episode import TaskRef, ToolTask
from ..llm import Call
from ..tools import ToolSpec, clean_schema, dedupe, safe_name

SYSTEM = (
    "You are a helpful assistant with access to tools. Use the tools to complete the user's "
    "request, then reply with a short answer. Call tools instead of describing what you would do."
)

FILES = {
    "browser": "browser/browser_0724_single_v3.json",
    "file_system": "file_system/filesystem_0723_single.json",
    "finance": "finance/finance_0724_single_v3.json",
    "map": "map/map_0717_single_multi_lang_500.json",
    "pay": "pay/pay_0723_single.json",
    "search": "search/search_0725_single_v2.json",
}

# The benchmark's own AST judge prompt (src/mcp_tool_bench/agents/base_tool_call_agent/prompt.py).
JUDGE_SYSTEM = """# Role
You are an expert in evaluating the accuracy of intelligent tool calls. You need to evaluate the accuracy of model tool call link predictions based on the "model prediction" and "answer label" given by the user.
# Steps
1. "Model Prediction" and "Answer Label" are both JSON lists, with each JSON representing the tool to be called, including the tool name and input field as calling parameters. Note that we only care about the last json in the list. Carefully compare all the contents. The 'similar_tools' field provides tool names with similar functionality.
2. Tool accuracy: If the tool in "model prediction" is the tool in "answer label" (the tool name needs to be exactly the same, and if the tool name predicted by the model is a similar tool to the answer label tool, it can also be considered consistent), the tool accuracy is considered to be 1, otherwise it is 0.
3. Parameter accuracy: Evaluate each parameter of the tool in "model prediction" in turn. If it is consistent with the parameter of the corresponding tool in "answer label" or "similar answer label", the parameter accuracy is considered to be 1, otherwise it is 0. If the tool accuracy is already 0, the parameter accuracy is also directly 0.
# Notes
1. Note that we only care about the last json in the "Model Prediction" and "Answer Label" list.
2. Fuzzy matching: If the parameters of the tool in "Model Prediction" are not completely consistent with the parameters of the corresponding tool in "Answer Label", but the parameter content is similar, the parameter accuracy is considered to be 1.
- For example, the parameter "query" are all rewritten search statements, and the content may be similar but the expression is different. If the content is similar, the parameters can be considered consistent.
- For example, the location parameters are all place names, and there may be different expressions such as "Beijing", "Beijing Region", etc., which can be considered consistent.
- For example, the longitude and latitude parameters are consistent before one decimal place, which is considered to be consistent.
- No language verification, parameters can be in any language as long as the content is the same.
3. Skip if standard parameter values are not provided
- If some parameters of the tool in "Model Prediction" do not provide standard parameter values in "Answer Label", they are skipped and considered to be consistent.
4. It is not required that all parameters of "Answer Label" be included in "Model Prediction". Parameter accuracy only needs to evaluate the parameters in "Model Prediction".
5. Some parameters are irrelevant to the user query and can be ignored without verification.
# Output format requirements
Output strictly in json format with 2 fields, for example {"tool_correctness": 1, "parameter_correctness": 0}"""


def _norm(v):
    if isinstance(v, str):
        s = v.strip().lower()
        try:
            return round(float(s), 1)
        except ValueError:
            return s
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 1)
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


def params_match(pred: dict, label: dict) -> bool:
    """Lenient deterministic check: every predicted param that has a label value must equal it."""
    for k, v in (pred or {}).items():
        if k in (label or {}) and _norm(v) != _norm(label[k]):
            return False
    return True


class Judge:
    def __init__(self, model: str | None):
        self.model = model
        self.cache: dict[str, bool] = {}

    def params_ok(self, query: str, pred: dict, label: dict) -> bool:
        if not self.model:
            return False
        key = json.dumps([query, pred, label], sort_keys=True, default=str)
        if key in self.cache:
            return self.cache[key]
        ok = False
        try:
            from google.genai import types

            from ..llm import _genai_client

            user = f"## Model Prediction\n{json.dumps([pred], ensure_ascii=False)}\n## Answer Label\n{json.dumps([label], ensure_ascii=False)}\n## user query\n{query}"
            r = _genai_client().models.generate_content(
                model=self.model, contents=user,
                config=types.GenerateContentConfig(system_instruction=JUDGE_SYSTEM, temperature=0,
                                                   response_mime_type="application/json"),
            )
            d = json.loads(r.text or "{}")
            ok = bool(int(d.get("tool_correctness", 0))) and bool(int(d.get("parameter_correctness", 0)))
        except Exception as e:  # noqa: BLE001
            print(f"[mcptoolbench] judge failed: {e}")
        self.cache[key] = ok
        return ok


def _load(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            return json.loads(path.read_text(encoding="utf-8"), strict=False)
        except json.JSONDecodeError as e:
            print(f"[mcptoolbench] skipping {path.name}: invalid JSON upstream ({e})")
            return []


def _tid(it: dict) -> str:
    return it.get("uuid") or hashlib.md5(it["query"].encode("utf-8")).hexdigest()[:12]


def _spec(t: dict, group: str) -> ToolSpec:
    return ToolSpec(
        name=safe_name(t["name"]), description=t.get("description", ""),
        parameters=clean_schema(t.get("input_schema") or t.get("parameters")),
        group=group, origin_name=t["name"],
    )


class MCPToolBench:
    name = "mcptoolbench"

    def __init__(self, bcfg: dict, cfg: dict, seed: int):
        self.root = vendor_dir() / "MCPToolBenchPP" / "data"
        if not self.root.exists():
            raise FileNotFoundError(f"{self.root} missing: run scripts/setup_benchmarks.sh")
        self.bcfg, self.cfg = bcfg, cfg
        self.judge = Judge(cfg.get("judge_llm") if bcfg.get("judge", True) else None)
        rng = random.Random(seed)
        self.items: list[tuple[str, dict]] = []
        all_tools: list[ToolSpec] = []
        for cat in bcfg.get("categories", list(FILES)):
            data = _load(self.root / FILES[cat])
            for it in data:
                for t in it.get("tools", []):
                    all_tools.append(_spec(t, cat))
            pick = [it for it in data if it.get("function_call_label")]
            rng.shuffle(pick)
            self.items += [(cat, it) for it in pick[: bcfg.get("per_category", 5)]]
        self.all_tools = dedupe(all_tools)

    def pool(self) -> list[ToolSpec]:
        return self.all_tools

    def tasks(self) -> list[TaskRef]:
        out = []
        for cat, it in self.items:
            native = dedupe([_spec(t, cat) for t in it.get("tools", [])])
            for surface in self.bcfg.get("surfaces", ["native"]):
                if surface == "native":
                    tools = native
                elif surface == "all":
                    names = {t.name for t in native}
                    tools = native + [t for t in self.all_tools if t.name not in names]
                else:
                    raise ValueError(f"mcptoolbench: unknown surface {surface}")
                tid = _tid(it)
                out.append(TaskRef(self.name, cat, tid, surface, len(tools),
                                   build=lambda c=cat, i=it, s=surface, t=tools: self._task(c, i, s, t)))
        return out

    def _task(self, cat: str, it: dict, surface: str, tools: list[ToolSpec]) -> ToolTask:
        labels = it["function_call_label"]
        query = it["query"]
        judge = self.judge

        def ok_names(lbl):
            return {safe_name(lbl["name"])} | {safe_name(s.get("name", "")) for s in lbl.get("similar_tools") or []}

        def call_matches(call: Call, lbl: dict, use_judge: bool) -> bool:
            if call.name not in ok_names(lbl):
                return False
            if params_match(call.args, lbl.get("input") or {}):
                return True
            return use_judge and judge.params_ok(query, {"name": call.name, "input": call.args},
                                                 {"name": lbl["name"], "input": lbl.get("input"),
                                                  "similar_tools": lbl.get("similar_tools", [])})

        def is_correct(call: Call) -> bool:
            return any(call_matches(call, lbl, True) for lbl in labels)

        def execute(call: Call):
            for lbl in labels:
                if call.name in ok_names(lbl):
                    out = lbl.get("output") or {}
                    return out if out.get("result") else {"status_code": 200, "result": "ok"}
            return {"status_code": 200, "result": "ok"}

        def finalize(calls, final_text):
            real = [c for c in calls if c["exec"]]
            if not real:
                return 0.0, {"reason": "no tool call"}
            last = real[-1]
            ok = call_matches(Call("", last["name"], last["args"]), labels[-1], True)
            return (1.0 if ok else 0.0), {"label": labels[-1].get("name"), "pred": last["name"]}

        return ToolTask(
            bench=self.name, group=cat, task_id=_tid(it),
            system=SYSTEM, user_text=query, surface=surface, tools=tools, execute=execute,
            is_correct_call=is_correct, finalize=finalize,
            gold_tools={n for lbl in labels for n in ok_names(lbl)}, route_state=f"User request: {query}",
        )
