"""Run the whole matrix at once: every benchmark x task x surface x LLM x decision
model x rep, shuffled (seeded) and executed concurrently. Results stream to
results/<run_id>/ and Tiger Data as each episode finishes."""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

from .config import ROOT, env
from .decision import FatalProviderError, make_decision_client
from .episode import Recorder, TaskRef, run_tool_episode
from .llm import LLM
from .router import Router
from .store import Sink, load_episodes
from .telemetry import capture, init_sentry, transaction


@dataclass
class Job:
    ref: TaskRef
    llm: dict
    decision: str
    rep: int
    episode_id: str


def build_benchmarks(cfg: dict, seed: int, only: set[str] | None = None) -> dict:
    """Instantiate enabled benchmarks; a benchmark that can't load is skipped loudly."""
    b = cfg["benchmarks"]
    out, pool = {}, []

    def want(name):
        return b.get(name, {}).get("enabled", False) and (not only or name in only)

    if want("mcptoolbench") or want("medagentbench") or want("tau"):
        try:
            from .benches.mcptoolbench import MCPToolBench

            mt = MCPToolBench(b.get("mcptoolbench", {}), cfg, seed)
            pool = mt.pool()
            if want("mcptoolbench"):
                out["mcptoolbench"] = mt
        except Exception as e:  # noqa: BLE001
            print(f"[skip] mcptoolbench: {e}")
    if want("tau"):
        try:
            from .benches.tau import TauBench

            out["tau"] = TauBench(b["tau"], cfg, seed, distractor_pool=pool)
            pool = out["tau"].pool() + pool   # tau tools first: closer to EHR-style workflows than MCP tools
        except Exception as e:  # noqa: BLE001
            print(f"[skip] tau: {e}")
    if want("medagentbench"):
        try:
            from .benches.medagentbench import MedAgentBench

            med = MedAgentBench(b["medagentbench"], cfg, seed, distractor_pool=pool)
            if not med.check_server():
                print(f"[warn] medagentbench: FHIR server not reachable at {med.base} (GET calls will error)")
            out["medagentbench"] = med
        except Exception as e:  # noqa: BLE001
            print(f"[skip] medagentbench: {e}")
    return out


def plan(cfg: dict, benches: dict, run_id: str) -> list[Job]:
    reps = int(cfg["run"].get("reps", 1))
    jobs = []
    for bench in benches.values():
        for ref in bench.tasks():
            for llm in cfg["llms"]:
                for dec in cfg["decision_models"]:
                    for rep in range(reps):
                        key = "|".join([run_id, ref.bench, ref.task_id, ref.surface, llm["id"], dec, str(rep)])
                        jobs.append(Job(ref, llm, dec, rep, hashlib.sha1(key.encode()).hexdigest()[:16]))
    random.Random(cfg["run"].get("seed", 13)).shuffle(jobs)
    return jobs


def summarize_plan(jobs: list[Job]) -> str:
    from collections import Counter

    c = Counter((j.ref.bench, j.ref.surface) for j in jobs)
    lines = [f"  {b:<14} {s:<12} {n:>5} task attempts" for (b, s), n in sorted(c.items())]
    tools = Counter((j.ref.bench, j.ref.surface, j.ref.n_tools) for j in jobs)
    sizes = {}
    for (b, s, n), _ in tools.items():
        sizes.setdefault((b, s), []).append(n)
    lines.append("  tool surface sizes: " + ", ".join(f"{b}/{s}: {min(v)}-{max(v)}" for (b, s), v in sorted(sizes.items())))
    return "\n".join(lines)


class Runner:
    def __init__(self, cfg: dict, run_id: str | None = None, name: str = "run"):
        self.cfg = cfg
        self.run_id = run_id or f"{name}-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        self.stop = threading.Event()
        self.stop_reason = ""
        self.spent = 0.0
        self.lock = threading.Lock()
        self.budget = float(cfg["run"].get("budget_usd") or env("BUDGET_USD", "150"))
        self.decisions = {d: make_decision_client(d, cfg) for d in cfg["decision_models"]}
        r = cfg["router"]
        self.routers = {
            d: Router(c, r.get("mass", 0.95), r.get("min_k", 3), r.get("max_k", 8),
                      r.get("escape_hatch", True), r.get("max_options", 255)) if c else None
            for d, c in self.decisions.items()
        }
        self.llms = {l["id"]: LLM(l) for l in cfg["llms"]}

    def decision_model_id(self, dec: str) -> str:
        c = self.decisions.get(dec)
        return c.model if c else ""

    def run_job(self, job: Job, benches: dict, sink: Sink) -> dict | None:
        if self.stop.is_set():
            return None
        llm = self.llms[job.llm["id"]]
        router = self.routers[job.decision]
        rec = Recorder(self.cfg, llm.model, self.decision_model_id(job.decision), sink, job.episode_id)
        seed = int(self.cfg["run"].get("seed", 13)) + job.rep
        row = dict(episode_id=job.episode_id, bench=job.ref.bench, grp=job.ref.group, task_id=job.ref.task_id,
                   llm=job.llm["id"], decision=job.decision, surface=job.ref.surface, rep=job.rep,
                   n_tools=job.ref.n_tools)
        score, status, details, error = None, "error", {}, None
        try:
            with transaction(f"{job.ref.bench} {job.llm['id']}+{job.decision}", bench=job.ref.bench,
                             llm=job.llm["id"], decision=job.decision, surface=job.ref.surface):
                if job.ref.kind == "tau":
                    score, status, details = benches["tau"].run(job.ref.build(), llm, router, rec, seed)
                else:
                    score, status, details = run_tool_episode(job.ref.build(), llm, router, rec,
                                                              int(self.cfg["run"].get("max_turns", 8)))
        except FatalProviderError as e:
            self.halt(f"fatal provider error: {e}")
            error = str(e)
        except Exception as e:  # noqa: BLE001
            capture(e)
            error = f"{type(e).__name__}: {e}"[:1000]
            details = {"traceback": traceback.format_exc()[-3000:]}
        if rec.llm_errors:
            details["llm_errors"] = rec.llm_errors[:5]
        row.update(rec.metrics())
        row.update(status=status if error is None else "error", score=score,
                   success=(score is not None and score >= 0.999), error=error,
                   details={**details, "calls": rec.calls[:40], "shortlists": rec.shortlists[:10]})
        with self.lock:
            self.spent += row.get("cost_usd") or 0.0
            if self.spent >= self.budget:
                self.halt(f"budget reached (${self.spent:.2f} >= ${self.budget:.2f})")
        sink.episode(row)
        return row

    def halt(self, reason: str):
        if not self.stop.is_set():
            self.stop_reason = reason
            self.stop.set()
            print("\n" + "!" * 78 + f"\n!! STOPPING RUN: {reason}\n!! resume with: python -m jevbench run --resume {self.run_id}\n" + "!" * 78 + "\n")

    def run(self, only: set[str] | None = None, dry_run=False, resume=False, warmup=True) -> str:
        init_sentry("runner")
        seed = int(self.cfg["run"].get("seed", 13))
        benches = build_benchmarks(self.cfg, seed, only)
        if not benches:
            raise SystemExit("no benchmark could be loaded (see [skip] lines above)")
        jobs = plan(self.cfg, benches, self.run_id)
        print(f"run {self.run_id}: {len(jobs)} task attempts")
        print(summarize_plan(jobs))
        if dry_run:
            return self.run_id
        sink = Sink(self.run_id)
        done = set()
        if resume:
            from .stats import valid

            done = {r["episode_id"] for r in load_episodes(self.run_id) if valid(r)}
            print(f"resume: {len(done)} task attempts already done")
        sink.run_started(len(jobs), {k: v for k, v in self.cfg.items() if k != "prices"})
        if warmup:
            from .probe import warmup as _warm

            _warm(self.cfg, self.llms.values(), [c for c in self.decisions.values() if c])
        todo = [j for j in jobs if j.episode_id not in done]
        n, t0 = len(done), time.monotonic()
        workers = int(self.cfg["run"].get("max_workers", 8))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(self.run_job, j, benches, sink) for j in todo]
            for f in as_completed(futs):
                row = f.result()
                if row is None:
                    continue
                n += 1
                tt = row.get("ttfctc_s")
                print(f"[{n}/{len(jobs)}] {row['bench']:<12} {row['llm']}+{row['decision']:<15} {row['surface']:<11} "
                      f"score={row['score']} ttfctc={'-' if tt is None else f'{tt:.2f}s'} "
                      f"${row.get('cost_usd') or 0:.4f} (total ${self.spent:.2f}, {time.monotonic() - t0:.0f}s)"
                      + (f" ERROR {row['error'][:120]}" if row.get("error") else ""))
        state = "stopped: " + self.stop_reason if self.stop.is_set() else "finished"
        sink.run_finished(state)
        sink.close()
        if self.stop.is_set():
            (ROOT / "results" / self.run_id / "STOPPED.json").write_text(
                json.dumps({"reason": self.stop_reason, "resume": f"python -m jevbench run --resume {self.run_id}"}),
                encoding="utf-8")
        print(f"run {self.run_id} {state}. Spent ${self.spent:.2f}.")
        return self.run_id
