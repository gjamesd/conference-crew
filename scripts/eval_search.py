#!/usr/bin/env python3
"""Golden-set eval: keyword vs semantic vs hybrid precision@k.

Prove search quality to judges with a terminal one-liner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.es_client import get_es
from app.search import search_talks


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    hits = sum(1 for x in top if x in relevant)
    return hits / k


def main() -> None:
    golden = json.loads((ROOT / "data" / "golden_queries.json").read_text())
    es = get_es()
    k = 5
    modes = ["keyword", "semantic", "hybrid"]
    totals = {m: 0.0 for m in modes}

    print(f"Evaluating {len(golden)} golden queries @k={k}\n")
    for case in golden:
        q = case["query"]
        relevant = set(case["relevant_talk_ids"])
        print(f"[{case['id']}] {q}")
        for mode in modes:
            results = search_talks(es, q, mode=mode, size=k)  # type: ignore[arg-type]
            ids = [r.get("talk_id") for r in results if r.get("talk_id")]
            p = precision_at_k(ids, relevant, k)
            totals[mode] += p
            mark = "✓" if p > 0 else "·"
            print(f"  {mode:9} P@{k}={p:.2f} {mark} {ids}")
        print()

    n = max(len(golden), 1)
    print("=== Mean precision ===")
    for mode in modes:
        print(f"{mode:9} {totals[mode] / n:.3f}")
    best = max(modes, key=lambda m: totals[m])
    print(f"\nBest mean mode: {best}")


if __name__ == "__main__":
    main()
