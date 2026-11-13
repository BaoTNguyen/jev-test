"""MedAgentBench (stanfordmlgroup/MedAgentBench, MIT): 300 physician-written tasks
against a FHIR server (docker image jyxsu6/medagentbench).

The original agent speaks a text protocol (GET url / POST url + JSON / FINISH([...])).
Here the 9 FHIR functions from funcs_v1.json become native function-calling tools
plus a `finish` tool, so tool-call timing can be measured. Behaviour matches the
original task loop: GETs hit the FHIR server, POSTs are accepted but not executed.

Grading uses the benchmark's own refsol.py (download it from the Stanford link in
the MedAgentBench README into vendor/MedAgentBench/src/server/tasks/medagentbench/).
refsol reads `results.result` (the FINISH list as a JSON string) and
`results.history` (agent/user turns in the original text protocol), so we rebuild
that history from the native calls.

"First correct tool call" needs a per-call key, which MedAgentBench doesn't have.
EXPECTED below is our annotation of the call each task type must make first
(e.g. task4 = a labs search for code MG on the task's patient).
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
import types as pytypes
from pathlib import Path

import requests

from ..config import env, vendor_dir
from ..episode import TaskRef, ToolTask
from ..llm import Call, to_json_text
from ..tools import ToolSpec, clean_schema, pad

SYSTEM = """You are an expert in using FHIR functions to assist medical professionals. You are given a question and a set of tools. Based on the question, make one or more tool calls to achieve the purpose. Call one tool at a time.
When you have the answers to all the questions and have finished every requested task, call `finish` with the answers as a list (use [] when the task only asks you to act)."""

# The graders compare typed values ([42], [1.8], ["S6534835"]), so answers travel as a JSON
# list string, exactly like the original FINISH([...]) protocol.
FINISH = ToolSpec(
    name="finish",
    description=("Finish the task. Call this exactly once at the end with every answer, in order, as a "
                 'JSON list: numbers as numbers, text in quotes. Examples: [42], [1.8], ["S6534835"], '
                 '[5.9, "2023-11-09T03:05:00+00:00"]. Use [] when the task only asked you to act.'),
    parameters={"type": "object", "properties": {"answers_json": {"type": "string",
                "description": 'JSON list of answers, e.g. [42] or ["Patient not found"]'}},
                "required": ["answers_json"]},
    group="fhir",
)

# name, method, resource, tag in description
_NAMES = [
    ("fhir_condition_search", "GET", "Condition", "Condition.Search"),
    ("fhir_observation_search_labs", "GET", "Observation", "(Labs)"),
    ("fhir_observation_search_vitals", "GET", "Observation", "(Vitals)"),
    ("fhir_observation_create_vitals", "POST", "Observation", "Observation.Create"),
    ("fhir_medicationrequest_search", "GET", "MedicationRequest", "MedicationRequest.Search"),
    ("fhir_medicationrequest_create", "POST", "MedicationRequest", "MedicationRequest.Create"),
    ("fhir_procedure_search", "GET", "Procedure", "Procedure.Search"),
    ("fhir_servicerequest_create", "POST", "ServiceRequest", "ServiceRequest.Create"),
    ("fhir_patient_search", "GET", "Patient", "Patient.Search"),
]

# task type -> (tool name, {arg: required substring (case-insensitive) or "MRN"})
EXPECTED = {
    "task1": ("fhir_patient_search", {}),
    "task2": ("fhir_patient_search", {"identifier": "MRN"}),
    "task3": ("fhir_observation_create_vitals", {"subject": "MRN"}),
    "task4": ("fhir_observation_search_labs", {"patient": "MRN", "code": "MG"}),
    "task5": ("fhir_observation_search_labs", {"patient": "MRN", "code": "MG"}),
    "task6": ("fhir_observation_search_labs", {"patient": "MRN", "code": "GLU"}),
    "task7": ("fhir_observation_search_labs", {"patient": "MRN", "code": "GLU"}),
    "task8": ("fhir_servicerequest_create", {"subject": "MRN"}),
    "task9": ("fhir_observation_search_labs", {"patient": "MRN", "code": "K"}),
    "task10": ("fhir_observation_search_labs", {"patient": "MRN", "code": "A1C"}),
}


def _load_refsol(root: Path):
    """Import refsol.py without running the AgentBench package __init__ (it needs the
    whole AgentBench server). A synthetic parent package lets `from .utils import *` work."""
    pkg_dir = root / "src" / "server" / "tasks" / "medagentbench"
    path = Path(env("MEDAGENTBENCH_REFSOL") or pkg_dir / "refsol.py")
    if not path.exists():
        return None
    pkg = pytypes.ModuleType("_mab")
    pkg.__path__ = [str(pkg_dir)]
    sys.modules.setdefault("_mab", pkg)
    spec = importlib.util.spec_from_file_location("_mab.refsol", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_mab.refsol"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Msg:
    def __init__(self, role, content):
        self.role, self.content = role, content


class _Results:
    def __init__(self, result, history):
        self.result, self.history = result, history


class MedAgentBench:
    name = "medagentbench"

    def __init__(self, bcfg: dict, cfg: dict, seed: int, distractor_pool: list[ToolSpec] | None = None):
        self.root = vendor_dir() / "MedAgentBench"
        data_dir = self.root / "data" / "medagentbench"
        if not data_dir.exists():
            raise FileNotFoundError(f"{data_dir} missing: run scripts/setup_benchmarks.sh")
        self.bcfg = bcfg
        self.base = env("FHIR_API_BASE", "http://localhost:8080/fhir/")
        if not self.base.endswith("/"):
            self.base += "/"
        self.refsol = _load_refsol(self.root)
        if self.refsol is None:
            print("[medagentbench] refsol.py not found: episodes will run but score = null. "
                  "Download it from the MedAgentBench README link.")
        self.data = json.loads((data_dir / "test_data_v2.json").read_text(encoding="utf-8"))
        funcs = json.loads((data_dir / "funcs_v1.json").read_text(encoding="utf-8"))
        self.tools = self._tools(funcs) + [FINISH]
        self.pool = distractor_pool or []
        rng = random.Random(seed)
        by_type: dict[str, list] = {}
        for case in self.data:
            by_type.setdefault(case["id"].split("_")[0], []).append(case)
        self.cases = []
        types = bcfg.get("task_types")           # e.g. [task1, task3, task4, task8, task10]
        for t in sorted(by_type, key=lambda s: int(s[4:])):
            if types and t not in types:
                continue
            cs = by_type[t][:]
            rng.shuffle(cs)
            self.cases += cs[: bcfg.get("per_task_type", 3)]

    def _tools(self, funcs) -> list[ToolSpec]:
        out = []
        for f in funcs:
            method, url = f["name"].split(" ", 1)
            resource = url.rsplit("/", 1)[-1]
            name = next(n for n, m, r, tag in _NAMES if m == method and r == resource and tag in f["description"])
            out.append(ToolSpec(name=name, description=f"{method} {resource}. {f['description']}",
                                parameters=clean_schema(f.get("parameters")), group="fhir",
                                meta={"method": method, "resource": resource}))
        return out

    def check_server(self) -> bool:
        try:
            return requests.get(self.base + "metadata", timeout=10).status_code == 200
        except requests.RequestException:
            return False

    def tasks(self) -> list[TaskRef]:
        out = []
        for case in self.cases:
            for surface in self.bcfg.get("surfaces", ["native"]):
                if surface == "native":
                    tools = self.tools
                elif surface.startswith("pad"):
                    tools = pad(self.tools, self.pool, int(surface[3:] or 100))
                else:
                    raise ValueError(f"medagentbench: unknown surface {surface}")
                out.append(TaskRef(self.name, case["id"].split("_")[0], case["id"], surface, len(tools),
                                   build=lambda c=case, s=surface, t=tools: self._task(c, s, t)))
        return out

    def _task(self, case: dict, surface: str, tools: list[ToolSpec]) -> ToolTask:
        ttype = case["id"].split("_")[0]
        mrn = case.get("eval_MRN", "")
        by_name = {t.name: t for t in self.tools}
        base = self.base
        history: list[_Msg] = []

        def execute(call: Call):
            spec = by_name[call.name]
            url = f"{base}{spec.meta['resource']}"
            if spec.meta["method"] == "GET":
                params = {k: v for k, v in call.args.items() if v not in (None, "")}
                qs = "&".join(f"{k}={v}" for k, v in params.items())
                history.append(_Msg("agent", f"GET {url}?{qs}"))
                try:
                    r = requests.get(url, params={**params, "_format": "json"}, timeout=30)
                    r.raise_for_status()
                    data = r.json()
                    history.append(_Msg("user", f"Here is the response from the GET request:\n{json.dumps(data)}"))
                    return {"result": to_json_text(data)}
                except Exception as e:  # noqa: BLE001
                    history.append(_Msg("user", f"Error in sending the GET request: {e}"))
                    return {"error": str(e)}
            history.append(_Msg("agent", f"POST {url}\n{json.dumps(call.args)}"))
            history.append(_Msg("user", "POST request accepted and executed successfully. Please call FINISH if you have got answers for all the questions and finished all the requested tasks"))
            return {"result": "POST request accepted and executed successfully."}

        exp_tool, exp_args = EXPECTED.get(ttype, (None, {}))

        def is_correct(call: Call) -> bool:
            if call.name != exp_tool:
                return False
            flat = json.dumps(call.args).lower()
            for k, want in exp_args.items():
                want = mrn if want == "MRN" else want
                v = call.args.get(k)
                hay = json.dumps(v).lower() if v is not None else flat
                if want.lower() not in hay:
                    return False
            return True

        refsol = self.refsol

        def finalize(calls, final_text):
            fin = [c for c in calls if c["name"] == "finish"]
            if not fin:
                return 0.0, {"reason": "no finish call"}
            raw = fin[-1]["args"].get("answers_json", fin[-1]["args"].get("answers", "[]"))
            if not isinstance(raw, str):
                raw = json.dumps(raw)
            try:
                answers = json.loads(raw)
                if not isinstance(answers, list):
                    answers = [answers]
            except json.JSONDecodeError:
                answers = [raw]          # graded as-is; a non-JSON answer will simply not match
            result = json.dumps(answers)
            if refsol is None:
                return None, {"answers": answers, "reason": "refsol.py missing"}
            grader = getattr(refsol, ttype, None)
            try:
                ok = bool(grader and grader(case, _Results(result, history), base) is True)
            except Exception as e:  # noqa: BLE001
                return 0.0, {"answers": answers, "grader_error": str(e)[:300]}
            return (1.0 if ok else 0.0), {"answers": answers}

        user = f"Context: {case.get('context', '')}\nQuestion: {case['instruction']}"
        return ToolTask(
            bench=self.name, group=ttype, task_id=case["id"], system=SYSTEM, user_text=user, surface=surface,
            tools=tools, execute=execute, is_correct_call=is_correct, finalize=finalize,
            gold_tools={exp_tool} if exp_tool else set(), stop_tools={"finish"}, route_state=user,
        )
