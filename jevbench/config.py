"""Load .env and the matrix YAML (with ${VAR:default} expansion)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::([^}]*))?\}")


def _expand(value):
    if isinstance(value, str):
        m = _ENV.fullmatch(value.strip())
        if m:
            raw = os.environ.get(m.group(1)) or m.group(2) or ""
            try:
                return float(raw) if "." in raw else int(raw)
            except ValueError:
                return raw
        return _ENV.sub(lambda m: os.environ.get(m.group(1)) or m.group(2) or "", value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = _expand(yaml.safe_load(f))
    cfg.setdefault("run", {})
    cfg.setdefault("router", {})
    cfg.setdefault("benchmarks", {})
    cfg.setdefault("prices", {})
    return cfg


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def vendor_dir() -> Path:
    v = env("VENDOR_DIR")
    return Path(v) if v else ROOT / "vendor"


def price(cfg: dict, model: str, input_tokens: int, output_tokens: int) -> float:
    p = cfg.get("prices", {}).get(model)
    if not p:
        return 0.0
    return (input_tokens * p.get("input", 0) + output_tokens * p.get("output", 0)) / 1e6
