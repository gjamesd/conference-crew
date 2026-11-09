from __future__ import annotations

from typing import Any

from elasticsearch import Elasticsearch

from app.config import get_settings
from app.telemetry import query_analytics


def conference_stats(es: Elasticsearch) -> dict[str, Any]:
    settings = get_settings()
    resp = es.search(
        index=settings.talks_index,
        size=0,
        aggs={
            "by_track": {"terms": {"field": "track", "size": 20}},
            "by_room": {"terms": {"field": "room", "size": 20}},
            "by_hour": {
                "date_histogram": {
                    "field": "start",
                    "calendar_interval": "hour",
                    "format": "yyyy-MM-dd'T'HH:mm",
                }
            },
            "by_level": {"terms": {"field": "level", "size": 10}},
        },
    )
    aggs = resp.get("aggregations", {})

    def buckets(name: str) -> list[dict[str, Any]]:
        return [
            {"key": b.get("key_as_string", b.get("key")), "count": b.get("doc_count", 0)}
            for b in aggs.get(name, {}).get("buckets", [])
        ]

    return {
        "total_talks": resp["hits"]["total"]["value"]
        if isinstance(resp["hits"]["total"], dict)
        else resp["hits"]["total"],
        "by_track": buckets("by_track"),
        "by_room": buckets("by_room"),
        "by_hour": buckets("by_hour"),
        "by_level": buckets("by_level"),
        "queries": query_analytics(es),
    }
