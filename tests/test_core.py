"""Offline tests: no API keys, no network."""

from jevbench.episode import Recorder, ToolTask, run_tool_episode
from jevbench.llm import LLM, Call
from jevbench.router import Router, pick
from jevbench.decision import MockDecisionClient
from jevbench.stats import summary
from jevbench.tools import ToolSpec, clean_schema, pad, safe_name


def test_pick_mass_and_bounds():
    probs = {"a": 0.7, "b": 0.2, "c": 0.05, "d": 0.03, "e": 0.02}
    assert pick(probs, 0.95, 3, 8) == ["a", "b", "c"]
    assert pick(probs, 0.5, 1, 8) == ["a"]
    assert pick(probs, 0.999, 1, 2) == ["a", "b"]
    assert pick({"x": 0.99, "y": 0.01}, 0.95, 3, 8) == ["x", "y"]


def test_safe_name_and_schema():
    assert safe_name("GET {api_base}/Condition") == "GET__api_base__Condition"
    assert safe_name("1abc")[0].isalpha()
    s = clean_schema({"$schema": "x", "title": "T", "type": "object", "properties": {
        "a": {"type": ["string", "null"], "title": "A"}, "b": {"type": "array"}}, "required": ["a", "zz"]})
    assert "title" not in s and s["properties"]["a"] == {"type": "string", "nullable": True}
    assert s["properties"]["b"]["items"] == {"type": "string"} and s["required"] == ["a"]


def test_pad_marks_distractors():
    native = [ToolSpec("a", "", {"type": "object"})]
    pool = [ToolSpec("a", "", {}), ToolSpec("b", "", {}), ToolSpec("c", "", {})]
    out = pad(native, pool, 3)
    assert [t.name for t in out] == ["a", "b", "c"]
    assert out[0].executable and not out[1].executable


def _task(tools):
    return ToolTask(
        bench="t", group="g", task_id="1", system="s", user_text="please get the weather in tokyo",
        surface="native", tools=tools, execute=lambda c: {"ok": True},
        is_correct_call=lambda c: c.name == "get_weather",
        finalize=lambda calls, text: (1.0 if any(c["name"] == "get_weather" for c in calls) else 0.0, {}),
        gold_tools={"get_weather"},
    )


def test_episode_metrics_with_router():
    tools = [ToolSpec("get_weather", "get the weather forecast for a city", {"type": "object", "properties": {}}),
             ToolSpec("send_email", "send an email", {"type": "object", "properties": {}}),
             ToolSpec("book_flight", "book a flight", {"type": "object", "properties": {}}),
             ToolSpec("create_invoice", "create an invoice", {"type": "object", "properties": {}})]
    cfg = {"prices": {}}
    llm = LLM({"id": "mock", "model": "mock"})
    rec = Recorder(cfg, "mock", "mock")
    router = Router(MockDecisionClient("mock"), 0.95, 1, 2)
    score, status, details = run_tool_episode(_task(tools), llm, router, rec, 4)
    m = rec.metrics()
    assert score == 1.0 and status == "answered"
    assert m["n_decision_calls"] == 1 and m["shortlist_recall"] == 1.0
    assert m["ttftc_s"] is not None and m["ttfctc_s"] == m["ttftc_s"]
    assert m["ttftc_s"] >= m["decision_time_s"]  # decision time is on the clock


def test_summary_paired_delta():
    rows = []
    for dec, t in (("none", 2.0), ("jev", 1.5)):
        rows.append(dict(episode_id=dec, bench="b", grp="g", task_id="1", llm="m", decision=dec, surface="s", rep=0,
                         score=1.0, success=True, ttftc_s=t, ttfctc_s=t, agent_time_s=t, cost_usd=0.01))
    s = summary(rows, planned=4)
    jev = next(r for r in s["table"] if r["decision"] == "jev")
    assert abs(jev["d_ttfctc_p50"] - (-0.5)) < 1e-9 and jev["d_success"] == 0.0
    assert s["done"] == 2 and s["planned"] == 4


def test_schema_inlines_refs_and_keeps_property_named_title():
    s = clean_schema({
        "type": "object",
        "$defs": {"Flight": {"type": "object", "title": "Flight", "properties": {"num": {"type": "string"}}}},
        "properties": {
            "flights": {"type": "array", "items": {"anyOf": [{"$ref": "#/$defs/Flight"}]}},
            "title": {"type": "string", "title": "Title"},       # a parameter literally named "title"
        },
        "required": ["flights", "title"],
    })
    import json
    assert "$ref" not in json.dumps(s) and "$defs" not in s
    assert s["properties"]["flights"]["items"]["anyOf"][0]["properties"]["num"] == {"type": "string"}
    assert "title" in s["properties"] and s["required"] == ["flights", "title"]


def test_router_skips_tiny_surfaces():
    r = Router(MockDecisionClient("mock"), 0.95, 3, 8)
    assert not r.should_route([ToolSpec("a", "", {})])
    assert not r.should_route([ToolSpec(n, "", {}) for n in "abc"])
    assert r.should_route([ToolSpec(n, "", {}) for n in "abcd"])


def test_router_pins_answer_tools():
    full = [ToolSpec(n, "", {"type": "object"}) for n in ("search", "lookup", "order", "finish")]
    r = Router(MockDecisionClient("mock"), 0.95, 1, 1)
    names = [t.name for t in r.surface(full, ["search"], set(), {"finish"})]
    assert "finish" in names and "search" in names and "order" not in names
