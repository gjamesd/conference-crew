from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from elasticsearch import Elasticsearch

from app.config import get_settings


def log_query(
    es: Elasticsearch,
    *,
    query: str,
    group_id: str,
    mode: str,
    kind: str,
    result_ids: list[str],
    filters: dict[str, Any] | None = None,
    explanation: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    query_id = f"q-{uuid.uuid4().hex[:12]}"
    doc = {
        "query_id": query_id,
        "group_id": group_id,
        "query": query,
        "query_text": query,
        "mode": mode,
        "kind": kind,
        "result_ids": result_ids,
        "filters": filters or {},
        "explanation": explanation or {},
        "timestamp": datetime.utcnow().isoformat(),
    }
    try:
        es.index(index=settings.query_logs_index, id=query_id, document=doc, refresh=False)
    except Exception:  # noqa: BLE001
        pass
    return query_id


def save_feedback(
    es: Elasticsearch,
    *,
    query_id: str,
    group_id: str,
    rating: str,
    talk_id: str | None = None,
    note: str | None = None,
) -> str:
    settings = get_settings()
    feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
    doc = {
        "feedback_id": feedback_id,
        "query_id": query_id,
        "group_id": group_id,
        "rating": rating,
        "talk_id": talk_id,
        "note": note or "",
        "timestamp": datetime.utcnow().isoformat(),
    }
    es.index(index=settings.feedback_index, id=feedback_id, document=doc, refresh=True)
    return feedback_id


def query_analytics(es: Elasticsearch) -> dict[str, Any]:
    settings = get_settings()
    try:
        resp = es.search(
            index=settings.query_logs_index,
            size=0,
            aggs={
                "by_mode": {"terms": {"field": "mode", "size": 10}},
                "by_kind": {"terms": {"field": "kind", "size": 10}},
                "top_queries": {"terms": {"field": "query.keyword", "size": 10}},
                "recent": {
                    "top_hits": {
                        "size": 8,
                        "sort": [{"timestamp": {"order": "desc"}}],
                        "_source": ["query", "mode", "kind", "timestamp", "result_ids"],
                    }
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "by_mode": [], "by_kind": [], "top_queries": [], "recent": []}

    aggs = resp.get("aggregations", {})

    def buckets(name: str) -> list[dict[str, Any]]:
        return [
            {"key": b.get("key"), "count": b.get("doc_count", 0)}
            for b in aggs.get(name, {}).get("buckets", [])
        ]

    recent_hits = aggs.get("recent", {}).get("hits", {}).get("hits", [])
    recent = [h.get("_source", {}) for h in recent_hits]
    return {
        "by_mode": buckets("by_mode"),
        "by_kind": buckets("by_kind"),
        "top_queries": buckets("top_queries"),
        "recent": recent,
    }
