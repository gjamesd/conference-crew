from __future__ import annotations

from functools import lru_cache

from elasticsearch import Elasticsearch

from app.config import get_settings
from app.mappings import (
    FEEDBACK_MAPPING,
    ITINERARIES_MAPPING,
    MESSAGES_MAPPING,
    QUERY_LOGS_MAPPING,
    TALKS_MAPPING,
)


class ElasticsearchNotConfigured(RuntimeError):
    pass


@lru_cache
def get_es() -> Elasticsearch:
    settings = get_settings()
    if not settings.elasticsearch_url or not settings.elasticsearch_api_key:
        raise ElasticsearchNotConfigured(
            "Set ELASTICSEARCH_URL and ELASTICSEARCH_API_KEY in .env "
            "(Elastic Cloud Serverless → Elasticsearch project type)."
        )
    return Elasticsearch(
        settings.elasticsearch_url,
        api_key=settings.elasticsearch_api_key,
        request_timeout=120,
    )


def ensure_indices(es: Elasticsearch | None = None) -> dict[str, bool]:
    es = es or get_es()
    settings = get_settings()
    created: dict[str, bool] = {}
    for name, body in (
        (settings.talks_index, TALKS_MAPPING),
        (settings.messages_index, MESSAGES_MAPPING),
        (settings.itineraries_index, ITINERARIES_MAPPING),
        (settings.query_logs_index, QUERY_LOGS_MAPPING),
        (settings.feedback_index, FEEDBACK_MAPPING),
    ):
        if es.indices.exists(index=name):
            created[name] = False
        else:
            es.indices.create(index=name, mappings=body["mappings"])
            created[name] = True
    return created
