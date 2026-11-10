#!/usr/bin/env python3
"""Delete/recreate talks+messages (so Jina mapping applies), then bulk-load samples."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.es_client import ensure_indices, get_es
from app.mappings import MESSAGES_MAPPING, TALKS_MAPPING
from app.search import ROOM_COORDS


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


def _recreate(es, index: str, mapping: dict) -> None:
    if es.indices.exists(index=index):
        es.indices.delete(index=index)
        print(f"deleted index {index}")
    es.indices.create(index=index, body=mapping)
    print(f"created index {index}")


def main() -> None:
    settings = get_settings()
    es = get_es()

    _recreate(es, settings.talks_index, TALKS_MAPPING)
    _recreate(es, settings.messages_index, MESSAGES_MAPPING)
    # Keep itineraries / logs / feedback / cache (create if missing)
    created = ensure_indices(es)
    print("other indices ensure:", created)

    talks = json.loads((ROOT / "data" / "talks.json").read_text())
    for talk in talks:
        room = talk.get("room")
        if room in ROOM_COORDS:
            lat, lon = ROOM_COORDS[room]
            talk["location"] = {"lat": lat, "lon": lon}
        # Keep BM25 text field in sync if only abstract present
        if talk.get("abstract") and not talk.get("abstract_text"):
            talk["abstract_text"] = talk["abstract"]
        if talk.get("abstract_text") and not talk.get("abstract"):
            talk["abstract"] = talk["abstract_text"]

    messages = json.loads((ROOT / "data" / "seed_messages.json").read_text())
    for msg in messages:
        if msg.get("body") and not msg.get("body_text"):
            msg["body_text"] = msg["body"]
        if msg.get("body_text") and not msg.get("body"):
            msg["body"] = msg["body_text"]

    n_talks = bulk_index(es, settings.talks_index, talks, "talk_id")
    n_msgs = bulk_index(es, settings.messages_index, messages, "message_id")
    print(f"indexed {n_talks} talks, {n_msgs} messages")
    print("semantic_text fields embed via .jina-embeddings-v3")


if __name__ == "__main__":
    main()
