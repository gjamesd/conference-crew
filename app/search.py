from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from elasticsearch import Elasticsearch

from app.config import get_settings

SearchMode = Literal["keyword", "semantic", "hybrid"]


def _hit_to_talk(hit: dict[str, Any]) -> dict[str, Any]:
    src = hit.get("_source", {})
    return {
        "talk_id": src.get("talk_id") or hit.get("_id"),
        "title": src.get("title"),
        "abstract": src.get("abstract_text") or src.get("abstract"),
        "track": src.get("track"),
        "speakers": src.get("speakers", []),
        "tags": src.get("tags", []),
        "room": src.get("room"),
        "location": src.get("location"),
        "start": src.get("start"),
        "end": src.get("end"),
        "day": src.get("day"),
        "level": src.get("level"),
        "score": hit.get("_score"),
    }


def _hit_to_message(hit: dict[str, Any]) -> dict[str, Any]:
    src = hit.get("_source", {})
    return {
        "message_id": src.get("message_id") or hit.get("_id"),
        "group_id": src.get("group_id"),
        "sender": src.get("sender"),
        "body": src.get("body_text") or src.get("body"),
        "role": src.get("role"),
        "timestamp": src.get("timestamp"),
        "source": src.get("source"),
        "score": hit.get("_score"),
    }


def _time_geo_filters(
    *,
    start_after: datetime | None,
    start_before: datetime | None,
    near_lat: float | None,
    near_lon: float | None,
    distance: str,
    room: str | None,
    track: str | None,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    if start_after or start_before:
        rng: dict[str, Any] = {}
        if start_after:
            rng["gte"] = start_after.isoformat()
        if start_before:
            rng["lte"] = start_before.isoformat()
        filters.append({"range": {"start": rng}})
    if room:
        filters.append({"term": {"room": room}})
    if track:
        filters.append({"term": {"track": track}})
    if near_lat is not None and near_lon is not None:
        filters.append(
            {
                "geo_distance": {
                    "distance": distance,
                    "location": {"lat": near_lat, "lon": near_lon},
                }
            }
        )
    return filters


def search_talks(
    es: Elasticsearch,
    query: str,
    *,
    mode: SearchMode = "hybrid",
    size: int = 8,
    start_after: datetime | None = None,
    start_before: datetime | None = None,
    near_lat: float | None = None,
    near_lon: float | None = None,
    distance: str = "300m",
    room: str | None = None,
    track: str | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    filters = _time_geo_filters(
        start_after=start_after,
        start_before=start_before,
        near_lat=near_lat,
        near_lon=near_lon,
        distance=distance,
        room=room,
        track=track,
    )

    keyword_query: dict[str, Any] = {
        "bool": {
            "must": [
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^3", "abstract_text", "tags", "speakers", "track"],
                        "fuzziness": "AUTO",
                    }
                }
            ],
            "filter": filters,
        }
    }

    semantic_query: dict[str, Any] = {
        "bool": {
            "must": [{"match": {"abstract": {"query": query}}}],
            "filter": filters,
        }
    }

    if mode == "keyword":
        resp = es.search(index=settings.talks_index, size=size, query=keyword_query)
    elif mode == "semantic":
        resp = es.search(index=settings.talks_index, size=size, query=semantic_query)
    else:
        # Hybrid via RRF retriever (keyword + semantic)
        resp = es.search(
            index=settings.talks_index,
            size=size,
            retriever={
                "rrf": {
                    "retrievers": [
                        {"standard": {"query": keyword_query}},
                        {"standard": {"query": semantic_query}},
                    ],
                    "rank_window_size": max(size * 5, 20),
                    "rank_constant": 60,
                }
            },
        )

    return [_hit_to_talk(h) for h in resp["hits"]["hits"]]


def search_messages(
    es: Elasticsearch,
    query: str,
    *,
    group_id: str,
    mode: SearchMode = "hybrid",
    size: int = 10,
) -> list[dict[str, Any]]:
    settings = get_settings()
    group_filter = [{"term": {"group_id": group_id}}]

    keyword_query = {
        "bool": {
            "must": [{"match": {"body_text": {"query": query, "fuzziness": "AUTO"}}}],
            "filter": group_filter,
        }
    }
    semantic_query = {
        "bool": {
            "must": [{"match": {"body": {"query": query}}}],
            "filter": group_filter,
        }
    }

    if mode == "keyword":
        resp = es.search(index=settings.messages_index, size=size, query=keyword_query)
    elif mode == "semantic":
        resp = es.search(index=settings.messages_index, size=size, query=semantic_query)
    else:
        resp = es.search(
            index=settings.messages_index,
            size=size,
            retriever={
                "rrf": {
                    "retrievers": [
                        {"standard": {"query": keyword_query}},
                        {"standard": {"query": semantic_query}},
                    ],
                    "rank_window_size": max(size * 5, 20),
                    "rank_constant": 60,
                }
            },
        )

    return [_hit_to_message(h) for h in resp["hits"]["hits"]]


def list_messages(
    es: Elasticsearch,
    group_id: str,
    *,
    size: int = 100,
) -> list[dict[str, Any]]:
    settings = get_settings()
    resp = es.search(
        index=settings.messages_index,
        size=size,
        query={"term": {"group_id": group_id}},
        sort=[{"timestamp": {"order": "asc"}}],
    )
    return [_hit_to_message(h) for h in resp["hits"]["hits"]]


ROOM_COORDS = {
    "Hall A": (40.7616, -111.8915),
    "Hall B": (40.7611, -111.8904),
    "Hall C": (40.7604, -111.8908),
    "Room 101": (40.7618, -111.8909),
    "Room 202": (40.7606, -111.8917),
    "Ballroom": (40.7609, -111.8911),
}


def resolve_near_room(room: str | None) -> tuple[float | None, float | None]:
    if not room:
        return None, None
    coords = ROOM_COORDS.get(room)
    if not coords:
        return None, None
    return coords
