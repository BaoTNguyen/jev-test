"""Scoreboard math, shared by the dashboard API and the CLI report."""

from __future__ import annotations

import statistics
from collections import defaultdict

BASELINE = "none"


def valid(r: dict) -> bool:
    """A clean attempt: no crash and no provider error mid-way (e.g. a 429 rate limit).
    Those measure the account quota, not the models, so they are rerun, not scored."""
    return not r.get("error") and not (r.get("details") or {}).get("llm_errors")


def _q(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    i = (len(xs) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _cell(rows):
    bad = sum(1 for r in rows if not valid(r))
    rows = [r for r in rows if valid(r)]
    scored = [r for r in rows if r.get("score") is not None]
    return dict(
        n=len(rows),
        errors=bad,
        success_rate=_mean([1.0 if r.get("success") else 0.0 for r in scored]) if scored else None,
        mean_score=_mean([r.get("score") for r in scored]),
        ttftc_p50=_q([r.get("ttftc_s") for r in rows], 0.5),
        ttftc_p95=_q([r.get("ttftc_s") for r in rows], 0.95),
        ttfctc_p50=_q([r.get("ttfctc_s") for r in rows], 0.5),
        ttfctc_p95=_q([r.get("ttfctc_s") for r in rows], 0.95),
        correct_call_rate=_mean([0.0 if r.get("ttfctc_s") is None else 1.0 for r in rows]),
        agent_time_p50=_q([r.get("agent_time_s") for r in rows], 0.5),
        decision_time_p50=_q([(r.get("decision_time_s") / r["n_decision_calls"]) if r.get("n_decision_calls") else None for r in rows], 0.5),
        first_byte_p50=_q([r.get("first_byte_s") for r in rows], 0.5),
        calls_before_correct=_mean([r.get("calls_before_correct") for r in rows]),
        wrong_calls=_mean([r.get("wrong_calls") for r in rows]),
        shortlist_recall=_mean([r.get("shortlist_recall") for r in rows]),
        shortlist_size=_mean([r.get("avg_shortlist_size") for r in rows]),
        hatch_rate=_mean([1.0 if r.get("hatch_used") else 0.0 for r in rows]),
        input_tokens=_mean([r.get("input_tokens") for r in rows]),
        cost_per_episode=_mean([r.get("cost_usd") for r in rows]),
        cost_total=sum((r.get("cost_usd") or 0.0) for r in rows),
    )


def _paired(rows):
    """Median difference vs the LLM-alone baseline on the same (task, llm, surface, rep),
    over pairs where both got a correct call (so a condition can't look fast by failing)."""
    rows = [r for r in rows if valid(r)]
    base = {}
    for r in rows:
        if r["decision"] == BASELINE:
            base[(r["bench"], r["task_id"], r["llm"], r["surface"], r["rep"])] = r
    out = defaultdict(lambda: {"d_ttfctc": [], "d_agent": [], "d_success": []})
    for r in rows:
        if r["decision"] == BASELINE:
            continue
        b = base.get((r["bench"], r["task_id"], r["llm"], r["surface"], r["rep"]))
        if not b:
            continue
        k = (r["bench"], r["llm"], r["decision"], r["surface"])
        if r.get("ttfctc_s") is not None and b.get("ttfctc_s") is not None:
            out[k]["d_ttfctc"].append(r["ttfctc_s"] - b["ttfctc_s"])
        if r.get("agent_time_s") is not None and b.get("agent_time_s") is not None:
            out[k]["d_agent"].append(r["agent_time_s"] - b["agent_time_s"])
        if r.get("score") is not None and b.get("score") is not None:
            out[k]["d_success"].append((1.0 if r.get("success") else 0.0) - (1.0 if b.get("success") else 0.0))
    return {k: dict(n_pairs=len(v["d_ttfctc"]), d_ttfctc_p50=_q(v["d_ttfctc"], 0.5),
                    d_agent_p50=_q(v["d_agent"], 0.5), d_success=_mean(v["d_success"]))
            for k, v in out.items()}


def surface_class(surface: str) -> str:
    return "small" if surface == "native" else "large"


KEY = ("success_rate", "ttftc_p50", "ttfctc_p50", "agent_time_p50", "n")


def views(rows: list[dict]) -> dict:
    """Per tool-list size (all / small / large): one tile per (model, setup) and one bar
    per (benchmark, model, setup). Deltas are vs the same model with no decision model."""
    out = {}
    for sc in ("all", "small", "large"):
        rs = [r for r in rows if sc == "all" or surface_class(r["surface"]) == sc]
        g = defaultdict(list)
        for r in rs:
            g[(r["llm"], r["decision"])].append(r)
        cells = {k: _cell(v) for k, v in g.items()}
        tiles = []
        for (llm, dec), c in sorted(cells.items()):
            base = cells.get((llm, BASELINE), {})
            t = {"llm": llm, "decision": dec, **{k: c.get(k) for k in KEY}}
            for k in ("success_rate", "ttftc_p50", "ttfctc_p50", "agent_time_p50"):
                t["d_" + k] = (c[k] - base[k]) if dec != BASELINE and c.get(k) is not None and base.get(k) is not None else None
            tiles.append(t)
        gb = defaultdict(list)
        for r in rs:
            gb[(r["bench"], r["llm"], r["decision"])].append(r)
        bars = [{"bench": b, "llm": l, "decision": d, **{k: _cell(v).get(k) for k in KEY}} for (b, l, d), v in sorted(gb.items())]
        out[sc] = {"tiles": tiles, "bars": bars}
    return out


def summary(rows: list[dict], planned: int | None = None) -> dict:
    cells = defaultdict(list)
    for r in rows:
        cells[(r["bench"], r["llm"], r["decision"], r["surface"])].append(r)
    paired = _paired(rows)
    table = []
    for k, rs in sorted(cells.items()):
        table.append({"bench": k[0], "llm": k[1], "decision": k[2], "surface": k[3], **_cell(rs),
                      **paired.get(k, {})})
    overall = defaultdict(list)
    for r in rows:
        overall[(r["llm"], r["decision"])].append(r)
    leaderboard = [{"llm": k[0], "decision": k[1], **_cell(rs)} for k, rs in sorted(overall.items())]
    by_decision = defaultdict(list)
    for r in rows:
        by_decision[r["decision"]].append(r)
    headline = {d: _cell(rs) for d, rs in by_decision.items()}
    recent = sorted(rows, key=lambda r: str(r.get("ts") or ""), reverse=True)[:25]
    return dict(
        done=sum(1 for r in rows if valid(r)), planned=planned, errors=sum(1 for r in rows if not valid(r)),
        cost_total=sum((r.get("cost_usd") or 0.0) for r in rows),
        headline=headline, leaderboard=leaderboard, table=table, views=views(rows),
        recent=[{k: r.get(k) for k in ("ts", "bench", "grp", "task_id", "llm", "decision", "surface", "score",
                                       "ttftc_s", "ttfctc_s", "agent_time_s", "cost_usd", "status", "error")}
                for r in recent],
    )
