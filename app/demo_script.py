from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from elasticsearch import Elasticsearch

from app.chat import index_message

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "data" / "demo_script.json"

CREW_SENDERS = frozenset({"Alex", "Sam", "Jordan"})


def load_demo_script() -> dict[str, Any]:
    return json.loads(SCRIPT_PATH.read_text())


def advance_demo_beat(
    es: Elasticsearch,
    *,
    group_id: str,
    beat_index: int,
) -> dict[str, Any]:
    """Index the next scripted crew beat into ES without invoking CrewBot."""
    script = load_demo_script()
    beats = script.get("beats") or []
    if beat_index < 0 or beat_index >= len(beats):
        return {
            "ok": False,
            "done": True,
            "beat_index": beat_index,
            "total_beats": len(beats),
            "messages": [],
            "cue": "Demo script finished — ask CrewBot anything from the thread.",
            "suggest": None,
            "title": None,
        }

    beat = beats[beat_index]
    indexed: list[dict[str, Any]] = []
    for msg in beat.get("messages") or []:
        sender = (msg.get("sender") or "Alex").strip()
        body = (msg.get("body") or "").strip()
        if not body:
            continue
        doc = index_message(
            es,
            group_id=group_id,
            sender=sender,
            body=body,
            role="user",
            source="demo_script",
            message_id=f"demo-{group_id}-{beat_index}-{len(indexed):02d}",
        )
        indexed.append(doc)

    next_index = beat_index + 1
    return {
        "ok": True,
        "done": next_index >= len(beats),
        "beat_index": beat_index,
        "next_beat_index": next_index,
        "total_beats": len(beats),
        "id": beat.get("id"),
        "title": beat.get("title"),
        "cue": beat.get("cue"),
        "suggest": beat.get("suggest"),
        "messages": indexed,
    }
