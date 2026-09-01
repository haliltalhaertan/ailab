"""Exact small-n helper for complete-graph simple s-t path counts.

Usage:
    python tropical_path_counts.py 8

This is deliberately deterministic and tiny; LLMs may request it through the
ScriptTool but cannot modify/execute arbitrary Python at runtime.
"""

from __future__ import annotations

import json
import math
import sys


def counts(n: int) -> dict:
    if n < 2:
        raise ValueError("n >= 2 olmalı")
    internal = n - 2
    by_internal_vertices = {}
    total = 0
    for k in range(internal + 1):
        value = math.factorial(internal) // math.factorial(internal - k)
        by_internal_vertices[str(k)] = value
        total += value
    return {
        "n": n,
        "source": 1,
        "target": n,
        "simple_path_count": total,
        "by_internal_vertices": by_internal_vertices,
    }


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    print(json.dumps(counts(n), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
