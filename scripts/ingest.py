#!/usr/bin/env python3
"""Create indices and bulk-load sample talks + seed messages into Elasticsearch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.es_client import ensure_indices, get_es


def bulk_index(es, index: str, docs: list[dict], id_field: str) -> int:
    if not docs:
        return 0
    operations: list[dict] = []
    for doc in docs:
        operations.append({"index": {"_index": index, "_id": doc[id_field]}})
        operations.append(doc)
    resp = es.bulk(operations=operations, refresh=True)
    if resp.get("errors"):
        first = next(
            (item for item in resp["items"] if "error" in item.get("index", {})),
            None,
        )
        raise RuntimeError(f"Bulk indexing errors on {index}: {first}")
    return len(docs)


def main() -> None:
    settings = get_settings()
    es = get_es()
    created = ensure_indices(es)
    print("indices:", created)

    talks = json.loads((ROOT / "data" / "talks.json").read_text())
    messages = json.loads((ROOT / "data" / "seed_messages.json").read_text())

    n_talks = bulk_index(es, settings.talks_index, talks, "talk_id")
    n_msgs = bulk_index(es, settings.messages_index, messages, "message_id")
    print(f"indexed {n_talks} talks, {n_msgs} messages")
    print("semantic_text fields will embed via Elastic Inference Service (Jina on Cloud).")


if __name__ == "__main__":
    main()
