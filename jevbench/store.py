"""Results storage: always JSONL under results/<run_id>/, plus Postgres / Tiger Data
(TimescaleDB) when DATABASE_URL is set. A single writer thread does all DB writes."""

from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, env

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
RESULTS = ROOT / "results"

EPISODE_COLS = [
    "episode_id", "run_id", "ts", "bench", "grp", "task_id", "llm", "decision", "surface", "rep", "n_tools",
    "status", "score", "success", "ttftc_s", "ttfctc_s", "agent_time_s", "wall_time_s", "decision_time_s",
    "llm_time_s", "first_byte_s", "n_decision_calls", "n_llm_calls", "n_tool_calls", "calls_before_correct",
    "wrong_calls", "distractor_calls", "hatch_used", "shortlist_recall", "avg_shortlist_size", "input_tokens",
    "output_tokens", "cached_tokens", "cost_usd", "error", "details",
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    url = env("DATABASE_URL")
    if not url:
        return None
    import psycopg

    return psycopg.connect(url, autocommit=True, connect_timeout=10)


def init_db() -> str:
    conn = connect()
    if conn is None:
        return "DATABASE_URL not set; using JSONL only"
    with conn:
        conn.execute(SCHEMA)
        has_ts = conn.execute("select count(*) from pg_extension where extname='timescaledb'").fetchone()[0]
    return "schema ready" + (" (TimescaleDB hypertable + continuous aggregate)" if has_ts else " (plain Postgres)")


class Sink:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.dir = RESULTS / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._conn = None
        try:
            self._conn = connect()
        except Exception as e:  # noqa: BLE001
            print(f"[store] database unavailable, JSONL only: {e}")
        self._t = threading.Thread(target=self._writer, daemon=True)
        self._t.start()

    # ----------------------------------------------------------------- API
    def run_started(self, planned: int, config: dict):
        row = {"run_id": self.run_id, "started": _now(), "planned": planned, "config": config}
        (self.dir / "run.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        self._q.put(("run", row))

    def run_finished(self, state: str):
        p = self.dir / "run.json"
        row = json.loads(p.read_text(encoding="utf-8"))
        row.update(finished=_now(), state=state)
        p.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        self._q.put(("run_end", row))

    def event(self, episode_id, kind, provider, latency, data):
        self._q.put(("event", {"ts": _now(), "run_id": self.run_id, "episode_id": episode_id, "kind": kind,
                               "provider": provider, "latency_s": latency, "data": data}))

    def episode(self, row: dict):
        row = {**row, "run_id": self.run_id, "ts": row.get("ts") or _now()}
        with self._lock, open(self.dir / "episodes.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        self._q.put(("episode", row))

    def close(self, timeout=30):
        self._q.put(("stop", None))
        self._t.join(timeout)

    # ----------------------------------------------------------------- writer
    def _writer(self):
        events_f = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        while True:
            kind, row = self._q.get()
            if kind == "stop":
                events_f.close()
                return
            if kind == "event":
                events_f.write(json.dumps(row, default=str) + "\n")
                events_f.flush()
            if self._conn is None:
                continue
            try:
                self._write_db(kind, row)
            except Exception as e:  # noqa: BLE001
                print(f"[store] db write failed ({kind}): {e}")
                try:
                    self._conn = connect()
                except Exception:  # noqa: BLE001
                    time.sleep(1)

    def _write_db(self, kind, row):
        from psycopg.types.json import Jsonb

        c = self._conn
        if kind == "run":
            c.execute(
                "insert into runs(run_id, started, planned, config) values (%s,%s,%s,%s) "
                "on conflict (run_id) do update set planned=excluded.planned",
                (row["run_id"], row["started"], row["planned"], Jsonb(row["config"])),
            )
        elif kind == "run_end":
            c.execute("update runs set finished=%s, state=%s where run_id=%s", (row["finished"], row["state"], row["run_id"]))
        elif kind == "event":
            c.execute(
                "insert into events(ts, run_id, episode_id, kind, provider, latency_s, data) values (%s,%s,%s,%s,%s,%s,%s)",
                (row["ts"], row["run_id"], row["episode_id"], row["kind"], row["provider"], row["latency_s"], Jsonb(row["data"])),
            )
        elif kind == "episode":
            vals = [Jsonb(row.get(k)) if k == "details" else row.get(k) for k in EPISODE_COLS]
            cols = ",".join(EPISODE_COLS)
            ph = ",".join(["%s"] * len(EPISODE_COLS))
            upd = ",".join(f"{k}=excluded.{k}" for k in EPISODE_COLS if k != "episode_id")
            c.execute(f"insert into episodes({cols}) values ({ph}) on conflict (episode_id) do update set {upd}", vals)


# ----------------------------------------------------------------------------- readers (dashboard, resume)

def list_runs() -> list[dict]:
    conn = None
    try:
        conn = connect()
    except Exception:  # noqa: BLE001
        conn = None
    if conn is not None:
        with conn:
            rows = conn.execute(
                "select r.run_id, r.started, r.finished, r.planned, r.state, e.last_ts from runs r "
                "left join (select run_id, max(ts) last_ts from episodes group by run_id) e using (run_id) "
                "order by coalesce(e.last_ts, r.started) desc limit 50").fetchall()
        return [dict(run_id=r[0], started=str(r[1]), finished=str(r[2]) if r[2] else None, planned=r[3], state=r[4],
                     last_activity=str(r[5]) if r[5] else None) for r in rows]
    out = []
    def _mtime(p):
        e = p.parent / "episodes.jsonl"
        return (e if e.exists() else p).stat().st_mtime

    for p in sorted(RESULTS.glob("*/run.json"), key=_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: d.get(k) for k in ("run_id", "started", "finished", "planned", "state")})
        except Exception:  # noqa: BLE001
            pass
    return out


def load_episodes(run_id: str) -> list[dict]:
    conn = None
    try:
        conn = connect()
    except Exception:  # noqa: BLE001
        conn = None
    if conn is not None:
        with conn:
            cur = conn.execute(f"select {','.join(EPISODE_COLS)} from episodes where run_id=%s", (run_id,))
            return [dict(zip(EPISODE_COLS, r)) for r in cur.fetchall()]
    p = RESULTS / run_id / "episodes.jsonl"
    if not p.exists():
        return []
    rows = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["episode_id"]] = r
    return list(rows.values())


def load_latency_series(run_id: str) -> list[dict]:
    """Per-minute decision/LLM latency, from the continuous aggregate when on TimescaleDB."""
    try:
        conn = connect()
    except Exception:  # noqa: BLE001
        conn = None
    if conn is None:
        return []
    with conn:
        try:
            cur = conn.execute(
                "select bucket, kind, provider, n, avg_s, max_s from call_latency_1m "
                "where run_id=%s order by bucket", (run_id,))
        except Exception:  # noqa: BLE001
            return []
        return [dict(bucket=str(r[0]), kind=r[1], provider=r[2], n=r[3], avg_s=r[4], max_s=r[5]) for r in cur.fetchall()]
