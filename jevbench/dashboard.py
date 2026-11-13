"""Live scoreboard. Reads Tiger Data (or results/*.jsonl) and serves a page that
polls /api/summary every few seconds while a run is in flight."""

from __future__ import annotations

import base64
import json
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .config import env
from .stats import summary
from .store import list_runs, load_episodes, load_latency_series

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="jevbench")
PASSWORD = env("DASHBOARD_PASSWORD")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if PASSWORD and request.url.path != "/healthz":
        h = request.headers.get("authorization", "")
        ok = False
        if h.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw, PASSWORD)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="jevbench"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/runs")
def runs():
    return list_runs()


@app.get("/api/summary")
def api_summary(run: str | None = None):
    rs = list_runs()
    if not run:
        if not rs:
            return JSONResponse({"run": None, "runs": []})
        run = rs[0]["run_id"]
    meta = next((r for r in rs if r["run_id"] == run), {})
    s = summary(load_episodes(run), meta.get("planned"))
    s.update(run=run, runs=rs, state=meta.get("state"), started=meta.get("started"),
             latency_series=load_latency_series(run))
    # rows from Tiger carry datetime / Decimal values
    return Response(json.dumps(s, default=str), media_type="application/json")
