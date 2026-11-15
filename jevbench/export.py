"""Freeze one run into a single static HTML file (no server, no database).

  python -m jevbench export-static run-20261113-132025 --out docs/index.html

Reads results/<run>/{run.json,episodes.jsonl,events.jsonl} directly, never Tiger Data,
so the page is reproducible from the local files alone. The dashboard template
(static/index.html) switches to snapshot mode when it finds window.SNAPSHOT.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .stats import summary
from .store import RESULTS

TEMPLATE = Path(__file__).parent / "static" / "index.html"
RECENT_KEYS = ("ts", "bench", "grp", "task_id", "llm", "decision", "surface", "score",
               "ttftc_s", "ttfctc_s", "agent_time_s", "cost_usd", "status", "error")


def _episodes(run_dir: Path) -> list[dict]:
    rows = {}
    for line in (run_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["episode_id"]] = r  # a rerun of the same episode replaces the earlier row
    return list(rows.values())


def latency_series(run_dir: Path) -> list[dict]:
    """Same shape as the call_latency_1m continuous aggregate, computed from events.jsonl."""
    p = run_dir / "events.jsonl"
    if not p.exists():
        return []
    acc = defaultdict(list)
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("kind") not in ("decision", "llm") or e.get("latency_s") is None:
            continue
        t = datetime.fromisoformat(e["ts"]).astimezone(timezone.utc).replace(second=0, microsecond=0)
        acc[(str(t), e["kind"], e["provider"])].append(e["latency_s"])
    return [dict(bucket=b, kind=k, provider=pv, n=len(xs), avg_s=sum(xs) / len(xs), max_s=max(xs))
            for (b, k, pv), xs in sorted(acc.items())]


def build(run_id: str) -> dict:
    run_dir = RESULTS / run_id
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rows = _episodes(run_dir)
    s = summary(rows, meta.get("planned"))
    # A snapshot has no "latest": list every attempt, oldest first.
    s["recent"] = [{k: r.get(k) for k in RECENT_KEYS} for r in sorted(rows, key=lambda r: str(r.get("ts") or ""))]
    ts = sorted(str(r["ts"]) for r in rows if r.get("ts"))
    s.update(run=run_id, runs=[{"run_id": run_id, "state": meta.get("state")}], state=meta.get("state"),
             started=ts[0] if ts else meta.get("started"), finished=ts[-1] if ts else meta.get("finished"),
             latency_series=latency_series(run_dir), attempts_recorded=len(rows), snapshot=True,
             exported=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return s


def render(s: dict, note: str = "") -> str:
    page = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(s, default=str, separators=(",", ":")).replace("</", "<\\/")
    inject = (f"<script>window.SNAPSHOT = {data};\nwindow.SNAPSHOT_NOTE = {json.dumps(note)};</script>\n")
    marker = "<script>\nconst DEC"
    if marker not in page:
        raise RuntimeError("dashboard template changed: snapshot injection point not found")
    page = page.replace(marker, inject + marker, 1)
    return page.replace("<title>Decision Model Bench</title>",
                        f"<title>Decision Model Bench · {html.escape(s['run'])}</title>", 1)


def export(run_id: str, out: Path, note: str = "") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build(run_id), note), encoding="utf-8")
    return out
