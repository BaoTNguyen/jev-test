"""Sentry tracing. One transaction per episode, spans for decision / LLM / tool calls.
Everything is a no-op when SENTRY_DSN is unset."""

from __future__ import annotations

import contextlib

from .config import env

_enabled = False


def init_sentry(component: str):
    global _enabled
    dsn = env("SENTRY_DSN")
    if not dsn:
        return False
    import sentry_sdk

    integrations = []
    try:  # gen_ai spans for every Gemini call (AI agent monitoring)
        from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration

        integrations.append(GoogleGenAIIntegration())
    except Exception:  # noqa: BLE001
        pass
    sentry_sdk.init(
        dsn=dsn,
        environment=env("SENTRY_ENVIRONMENT", "hackathon"),
        release=env("SENTRY_RELEASE", "jevbench@0.1.0"),
        # A benchmark wants every episode traced, so 1.0 everywhere (it's not user traffic).
        traces_sample_rate=float(env("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        enable_logs=True,
        send_default_pii=False,   # no prompts / tool payloads leave the machine
        integrations=integrations,
    )
    sentry_sdk.set_tag("component", component)
    _enabled = True
    return True


@contextlib.contextmanager
def transaction(name: str, **tags):
    if not _enabled:
        yield None
        return
    import sentry_sdk

    with sentry_sdk.isolation_scope() as scope:
        for k, v in tags.items():
            scope.set_tag(k, str(v))
        with sentry_sdk.start_transaction(op="eval.episode", name=name) as tx:
            yield tx


@contextlib.contextmanager
def span(op: str, name: str, **data):
    if not _enabled:
        yield None
        return
    import sentry_sdk

    with sentry_sdk.start_span(op=op, name=name) as s:
        for k, v in data.items():
            s.set_data(k, v)
        yield s


def capture(exc: BaseException):
    if _enabled:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
