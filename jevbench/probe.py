"""Latency probe: how far away is each provider from this machine?

For each host: TCP+TLS handshake time (fresh connection), then N tiny API calls
on a warm connection (a 2-option decision for decision models, a one-word reply
for Gemini). Run it on the DigitalOcean droplet before the real run; the
results go to the events table (kind=probe) so the dashboard can show the
network floor next to the decision overhead.
"""

from __future__ import annotations

import socket
import ssl
import statistics
import time
from urllib.parse import urlparse

from .config import env
from .decision import DecisionClient, make_decision_client
from .llm import LLM

HOSTS = {
    "gemini (LLM + Decision)": "generativelanguage.googleapis.com",
    "typesafe (Jev direct)": "api.typesafe.ai",
    "openrouter (Jev via gateway)": "openrouter.ai",
    "subq": "api.subq.ai",
}


def tls_handshake(host: str, n: int = 5) -> list[float]:
    out = []
    ctx = ssl.create_default_context()
    for _ in range(n):
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, 443), timeout=10) as s:
                with ctx.wrap_socket(s, server_hostname=host):
                    out.append(time.monotonic() - t0)
        except OSError as e:
            print(f"  {host}: {e}")
            break
    return out


def _decision_ping(c: DecisionClient) -> float:
    d = c.choose("The user says: please check my order status.", "Which tool fits?",
                 {"get_order": "get_order: look up an order", "send_email": "send_email: send an email"})
    return d.latency_s


def _llm_ping(llm: LLM) -> float:
    conv = llm.conversation("Reply with one word.")
    conv.add_user("Say OK.")
    tr = conv.step([])
    if tr.error:
        raise RuntimeError(tr.error)
    return tr.t_first_chunk or tr.duration


def warmup(cfg, llms, decisions):
    """One discarded call per provider so no measured call pays for a cold connection."""
    for c in decisions:
        if c.provider == "mock":
            continue
        try:
            _decision_ping(c)
        except Exception as e:  # noqa: BLE001
            print(f"[warmup] {c.provider}: {e}")
            raise
    for llm in llms:
        if llm.model == "mock":
            continue
        try:
            _llm_ping(llm)
        except Exception as e:  # noqa: BLE001
            print(f"[warmup] {llm.model}: {e}")


def _fmt(xs):
    if not xs:
        return "      -"
    xs = sorted(xs)
    p95 = xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]
    return f"p50 {statistics.median(xs) * 1000:6.0f} ms  p95 {p95 * 1000:6.0f} ms  (n={len(xs)})"


def probe(cfg: dict, n: int = 10, sink=None):
    print("TLS handshake (DNS + TCP + TLS, fresh connection) - the network floor:")
    for label, host in HOSTS.items():
        xs = tls_handshake(host)
        print(f"  {label:<26} {host:<36} {_fmt(xs)}")
        if sink:
            for x in xs:
                sink.event("", "probe", f"tls:{host}", x, {})
    print(f"\nWarm API calls (n={n}, connection reused):")
    for name in [d for d in cfg["decision_models"] if d != "none"]:
        try:
            c = make_decision_client(name, cfg)
            _decision_ping(c)  # warm
            xs = [_decision_ping(c) for _ in range(n)]
            print(f"  decision {name:<17} {c.model:<24} {_fmt(xs)}")
            if sink:
                for x in xs:
                    sink.event("", "probe", f"decision:{name}", x, {"model": c.model})
        except Exception as e:  # noqa: BLE001
            print(f"  decision {name:<17} FAILED: {e}")
    for spec in cfg["llms"]:
        llm = LLM(spec)
        try:
            _llm_ping(llm)
            xs = [_llm_ping(llm) for _ in range(max(3, n // 2))]
            print(f"  llm      {spec['id']:<17} {spec['model']:<24} time-to-first-byte {_fmt(xs)}")
            if sink:
                for x in xs:
                    sink.event("", "probe", f"llm:{spec['id']}", x, {"model": spec["model"]})
        except Exception as e:  # noqa: BLE001
            print(f"  llm      {spec['id']:<17} FAILED: {e}")
    print("\nReading it: warm decision latency minus the TLS floor is roughly model + gateway time.\n"
          "If a provider's floor is >150 ms from SFO3, it is probably served far away.")


def check_keys(cfg: dict) -> bool:
    need = {"GEMINI_API_KEY": "Gemini LLMs, Gemini Decision, tau user simulator + judges"}
    if "jev" in cfg["decision_models"]:
        from .decision import jev_direct

        need["TYPESAFE_API_KEY" if jev_direct() else "OPENROUTER_API_KEY"] = (
            "Jev (TypeSafe direct)" if jev_direct() else "Jev (via OpenRouter)")
    if "subq" in cfg["decision_models"]:
        need["SUBQ_API_KEY"] = "SubQ Snap"
    optional = {"DATABASE_URL": "Tiger Data (dashboard storage)", "SENTRY_DSN": "Sentry tracing",
                "OPENAI_API_KEY": "only if tau judge_llm is null"}
    ok = True
    for k, why in need.items():
        present = bool(env(k))
        ok &= present
        print(f"  {'OK ' if present else 'MISSING'} {k:<22} {why}")
    for k, why in optional.items():
        print(f"  {'OK ' if env(k) else '-- '} {k:<22} {why} (optional)")
    fhir = env("FHIR_API_BASE", "http://localhost:8080/fhir/")
    print(f"  FHIR_API_BASE = {fhir} ({urlparse(fhir).hostname})")
    return ok
