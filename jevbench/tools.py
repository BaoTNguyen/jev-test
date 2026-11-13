"""One tool format shared by every benchmark, LLM and decision model."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

_SAFE = re.compile(r"[^A-Za-z0-9_]")


def safe_name(name: str, max_len: int = 64) -> str:
    """Gemini function names: letters, digits, underscores; start with a letter or _."""
    s = _SAFE.sub("_", name).strip("_") or "tool"
    if not (s[0].isalpha() or s[0] == "_"):
        s = "t_" + s
    return s[:max_len]


@dataclass
class ToolSpec:
    name: str                       # name the LLM and decision model see
    description: str
    parameters: dict                # JSON Schema (type: object)
    group: str = ""                 # server / domain / category, used for hierarchical routing
    executable: bool = True         # False = distractor from another surface
    origin_name: str = ""           # name inside the benchmark, if different
    meta: dict = field(default_factory=dict)

    def short(self, n: int = 400) -> str:
        d = " ".join((self.description or "").split())
        return f"{self.name}: {d[:n]}"


ESCAPE_HATCH = ToolSpec(
    name="request_more_tools",
    description=(
        "Call this only if none of the tools you currently have can do what is needed next. "
        "You will then get the full tool list."
    ),
    parameters={
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "What you need to do."}},
        "required": ["reason"],
    },
    group="_harness",
)

# JSON Schema keywords Gemini's function schema does not accept.
_DROP = {"$schema", "$id", "title", "examples", "default", "additionalProperties", "$defs", "definitions",
         "$comment"}
# keywords whose value is a map of NAME -> schema (the names are data, never keywords)
_SCHEMA_MAPS = {"properties", "patternProperties"}


def _resolve_refs(node, defs: dict, depth: int = 0):
    """Inline local $refs (#/$defs/X, #/definitions/X). Gemini rejects dangling refs."""
    if isinstance(node, list):
        return [_resolve_refs(v, defs, depth) for v in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        name = ref.rsplit("/", 1)[-1]
        target = defs.get(name)
        if target is None or depth > 8:
            rest = {k: v for k, v in node.items() if k != "$ref"}
            return _resolve_refs(rest or {"type": "object"}, defs, depth)
        merged = {**copy.deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
        return _resolve_refs(merged, defs, depth + 1)
    return {k: _resolve_refs(v, defs, depth) for k, v in node.items()}


def clean_schema(schema: dict | None) -> dict:
    """Make a JSON schema Gemini-friendly without changing what it means for the call."""
    if not schema:
        return {"type": "object", "properties": {}}
    defs = {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}
    s = _resolve_refs(copy.deepcopy(schema), defs)

    def walk_schema(node):
        if isinstance(node, list):
            for v in node:
                walk_schema(v)
            return
        if not isinstance(node, dict):
            return
        for k in list(node):
            if k in _DROP:
                node.pop(k)
        t = node.get("type")
        if isinstance(t, list):  # ["string","null"] -> "string", nullable
            non_null = [x for x in t if x != "null"]
            node["type"] = non_null[0] if non_null else "string"
            if "null" in t:
                node["nullable"] = True
        if node.get("type") == "array" and "items" not in node:
            node["items"] = {"type": "string"}
        if isinstance(node.get("required"), list):
            props = node.get("properties") or {}
            node["required"] = [r for r in node["required"] if r in props]
            if not node["required"]:
                node.pop("required")
        for k, v in node.items():
            if k in _SCHEMA_MAPS and isinstance(v, dict):
                for sub in v.values():      # property NAMES stay; only their schemas are cleaned
                    walk_schema(sub)
            elif k == "enum":
                continue
            else:
                walk_schema(v)

    walk_schema(s)
    if s.get("type") != "object":
        s = {"type": "object", "properties": {"value": s}}
    s.setdefault("properties", {})
    return s


def dedupe(tools: list[ToolSpec]) -> list[ToolSpec]:
    seen, out = set(), []
    for t in tools:
        if t.name in seen:
            continue
        seen.add(t.name)
        out.append(t)
    return out


def pad(native: list[ToolSpec], pool: list[ToolSpec], target: int) -> list[ToolSpec]:
    """Native tools plus non-executable distractors from `pool`, up to `target` tools."""
    names = {t.name for t in native}
    extra = []
    for t in pool:
        if len(native) + len(extra) >= target:
            break
        if t.name in names:
            continue
        names.add(t.name)
        d = copy.copy(t)
        d.executable = False
        extra.append(d)
    return native + extra
