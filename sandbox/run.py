"""Sandbox runner: call skill scripts by hand and print what they return.

Usage (from the repo root):
    python sandbox/run.py              # list scenarios
    python sandbox/run.py <name> ...   # run one or more scenarios
    python sandbox/run.py --all        # run every scenario

Scenarios live in `sandbox/scenarios.py`. See `sandbox/README.md`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# Skill scripts import each other as top-level modules (`from wiki_contracts import ...`).
sys.path.insert(0, str(ROOT / "b2c-demand-validation" / "scripts"))

from scenarios import SCENARIOS  # noqa: E402


def to_jsonable(value: Any) -> Any:
    """Turn dataclasses, enums and tuples into plain JSON-friendly values."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    return value


def show(value: Any, max_items: int | None = None) -> None:
    """Pretty-print a result. `max_items` trims long lists for readability."""
    data = to_jsonable(value)
    if max_items is not None:
        data = _trim(data, max_items)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _trim(data: Any, max_items: int) -> Any:
    if isinstance(data, dict):
        return {k: _trim(v, max_items) for k, v in data.items()}
    if isinstance(data, list):
        head = [_trim(v, max_items) for v in data[:max_items]]
        if len(data) > max_items:
            head.append(f"... {len(data) - max_items} more")
        return head
    return data


def run(name: str, max_items: int | None) -> None:
    scenario = SCENARIOS[name]
    print(f"\n=== {name} === {scenario.__doc__ or ''}".rstrip())
    started = time.perf_counter()
    result = scenario()
    show(result, max_items)
    print(f"--- {name} took {time.perf_counter() - started:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="scenario names to run")
    parser.add_argument("--all", action="store_true", help="run every scenario")
    parser.add_argument("--max-items", type=int, default=10, help="trim lists to N items, 0 for no limit")
    args = parser.parse_args()

    names = list(SCENARIOS) if args.all else args.names
    if not names:
        print("Scenarios:")
        for name, scenario in SCENARIOS.items():
            print(f"  {name:<32} {scenario.__doc__ or ''}")
        return

    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario(s): {', '.join(unknown)}. Run without arguments to list them.")

    for name in names:
        run(name, args.max_items or None)


if __name__ == "__main__":
    main()
