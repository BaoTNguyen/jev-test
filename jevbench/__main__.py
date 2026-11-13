"""CLI.

  python -m jevbench check                  keys + config sanity
  python -m jevbench init-db                create tables (Tiger Data / Postgres)
  python -m jevbench probe                  network + API latency per provider
  python -m jevbench run [--dry-run]        run the whole matrix
  python -m jevbench run --resume RUN_ID    continue a stopped run
  python -m jevbench dashboard              live scoreboard on :8000
  python -m jevbench report [RUN_ID]        print the final scoreboard
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import ROOT, load_config


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jevbench")
    ap.add_argument("--config", default=str(ROOT / "config" / "matrix.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("init-db")
    p = sub.add_parser("probe")
    p.add_argument("-n", type=int, default=10)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--resume", metavar="RUN_ID")
    r.add_argument("--only", help="comma list: tau,medagentbench,mcptoolbench")
    r.add_argument("--name", default="run", help="run id prefix, e.g. smoke or live")
    r.add_argument("--workers", type=int, help="override run.max_workers (attempts in parallel)")
    r.add_argument("--no-warmup", action="store_true")
    d = sub.add_parser("dashboard")
    d.add_argument("--host", default="0.0.0.0")
    d.add_argument("--port", type=int, default=8000)
    rp = sub.add_parser("report")
    rp.add_argument("run_id", nargs="?")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "check":
        from .probe import check_keys

        print("API keys:")
        ok = check_keys(cfg)
        from .runner import build_benchmarks, plan, summarize_plan

        benches = build_benchmarks(cfg, cfg["run"].get("seed", 13))
        jobs = plan(cfg, benches, "check")
        print(f"\nBenchmarks loaded: {', '.join(benches) or 'none'}; {len(jobs)} episodes planned")
        print(summarize_plan(jobs))
        sys.exit(0 if ok else 1)
    if a.cmd == "init-db":
        from .store import init_db

        print(init_db())
    elif a.cmd == "probe":
        from .probe import probe
        from .store import Sink

        sink = Sink("probe")
        probe(cfg, a.n, sink)
        sink.close()
    elif a.cmd == "run":
        from .runner import Runner

        only = set(a.only.split(",")) if a.only else None
        if a.workers:
            cfg["run"]["max_workers"] = a.workers
        Runner(cfg, a.resume, a.name).run(only=only, dry_run=a.dry_run, resume=bool(a.resume), warmup=not a.no_warmup)
    elif a.cmd == "dashboard":
        import uvicorn

        from .dashboard import app

        uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    elif a.cmd == "report":
        from .stats import summary
        from .store import list_runs, load_episodes

        run_id = a.run_id or (list_runs() or [{}])[0].get("run_id")
        if not run_id:
            sys.exit("no runs found")
        s = summary(load_episodes(run_id))
        print(f"run {run_id}: {s['done']} episodes, ${s['cost_total']:.2f}")
        cols = ["llm", "decision", "n", "success_rate", "ttftc_p50", "ttfctc_p50", "ttfctc_p95",
                "decision_time_p50", "shortlist_recall", "cost_per_episode"]
        print(" | ".join(cols))
        for row in s["leaderboard"]:
            print(" | ".join(_f(row.get(c)) for c in cols))
        (ROOT / "results" / run_id / "summary.json").write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")


def _f(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


if __name__ == "__main__":
    main()
