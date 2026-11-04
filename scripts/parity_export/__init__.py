"""Golden exporter: runs the engine on fixed inputs and writes canonical JSON.

The Swift rewrite compares its own behaviour against these files. One module per
area (``llm``, …), each exposing ``export() -> {filename: object}``.
"""
import json
from pathlib import Path


def _normalize(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def canonical_json(obj) -> str:
    """The fixed canonical form both sides agree on (integral floats become ints)."""
    return json.dumps(_normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def write_canonical(out_dir: Path, name: str, obj) -> None:
    (out_dir / name).write_text(canonical_json(obj), encoding="utf-8")
