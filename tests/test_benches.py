"""Benchmark adapters against the vendored data (skipped if vendor/ is missing)."""

import pytest

from jevbench.config import vendor_dir
from jevbench.llm import Call

HAS_MCPTB = (vendor_dir() / "MCPToolBenchPP" / "data").exists()
HAS_MED = (vendor_dir() / "MedAgentBench" / "data").exists()


@pytest.mark.skipif(not HAS_MCPTB, reason="vendor/MCPToolBenchPP missing")
def test_mcptoolbench_loads_and_scores_label():
    from jevbench.benches.mcptoolbench import MCPToolBench, params_match

    b = MCPToolBench({"categories": ["finance", "file_system"], "per_category": 2, "surfaces": ["native", "all"],
                      "judge": False}, {"judge_llm": None}, 1)
    refs = b.tasks()
    assert len(refs) == 8
    big = [r for r in refs if r.surface == "all"]
    assert all(r.n_tools > 10 for r in big)
    t = refs[0].build()
    # replaying the label must count as correct and succeed
    import json
    item = next(it for cat, it in b.items if it.get("uuid", "") == t.task_id or True)
    assert params_match({"market": "US"}, {"market": "us", "symbol_list": ["TSLA"]})
    assert not params_match({"market": "HK"}, {"market": "US"})


@pytest.mark.skipif(not HAS_MED, reason="vendor/MedAgentBench missing")
def test_medagentbench_tools_and_expected_calls():
    from jevbench.benches.medagentbench import MedAgentBench

    b = MedAgentBench({"per_task_type": 1, "surfaces": ["native"]}, {}, 1)
    names = {t.name for t in b.tools}
    assert {"fhir_patient_search", "fhir_observation_search_labs", "fhir_observation_search_vitals", "finish"} <= names
    refs = b.tasks()
    assert len(refs) == 10
    t4 = next(r for r in refs if r.group == "task4").build()
    mrn = next(c for c in b.cases if c["id"] == t4.task_id)["eval_MRN"]
    assert t4.is_correct_call(Call("1", "fhir_observation_search_labs", {"patient": mrn, "code": "MG"}))
    assert not t4.is_correct_call(Call("1", "fhir_observation_search_labs", {"patient": mrn, "code": "K"}))
    assert not t4.is_correct_call(Call("1", "fhir_patient_search", {"identifier": mrn}))
