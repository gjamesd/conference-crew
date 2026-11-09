from __future__ import annotations

from datetime import datetime
from typing import Any

from elasticsearch import Elasticsearch

from app.search import SearchMode, search_talks


def _ids(results: list[dict[str, Any]]) -> list[str]:
    return [r.get("talk_id") for r in results if r.get("talk_id")]


def compare_talk_search(
    es: Elasticsearch,
    query: str,
    *,
    size: int = 5,
    start_after: datetime | None = None,
    start_before: datetime | None = None,
    near_lat: float | None = None,
    near_lon: float | None = None,
    room: str | None = None,
    track: str | None = None,
) -> dict[str, Any]:
    """Run keyword, semantic, and hybrid side-by-side for judge demos."""
    modes: list[SearchMode] = ["keyword", "semantic", "hybrid"]
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for mode in modes:
        by_mode[mode] = search_talks(
            es,
            query,
            mode=mode,
            size=size,
            start_after=start_after,
            start_before=start_before,
            near_lat=near_lat,
            near_lon=near_lon,
            room=room,
            track=track,
        )

    keyword_ids = set(_ids(by_mode["keyword"]))
    semantic_ids = set(_ids(by_mode["semantic"]))
    hybrid_ids = set(_ids(by_mode["hybrid"]))

    only_semantic = sorted(semantic_ids - keyword_ids)
    only_keyword = sorted(keyword_ids - semantic_ids)
    hybrid_rescues = sorted(hybrid_ids - keyword_ids)

    narrative_bits = []
    if only_semantic:
        narrative_bits.append(
            f"Semantic/Jina found {len(only_semantic)} talk(s) keyword missed: {', '.join(only_semantic[:3])}."
        )
    if only_keyword:
        narrative_bits.append(
            f"Keyword found {len(only_keyword)} exact-ish hit(s) semantic ranked lower/out: {', '.join(only_keyword[:3])}."
        )
    if hybrid_rescues:
        narrative_bits.append(
            f"Hybrid kept meaning matches keyword missed: {', '.join(hybrid_rescues[:3])}."
        )
    if not narrative_bits:
        narrative_bits.append("All three modes largely agree on the top set for this query.")

    explanation = {
        "query": query,
        "filters": {
            "start_after": start_after.isoformat() if start_after else None,
            "start_before": start_before.isoformat() if start_before else None,
            "near": {"lat": near_lat, "lon": near_lon} if near_lat is not None else None,
            "room": room,
            "track": track,
        },
        "only_semantic": only_semantic,
        "only_keyword": only_keyword,
        "hybrid_rescues": hybrid_rescues,
        "narrative": " ".join(narrative_bits),
        "methods": {
            "keyword": "BM25 multi_match on title^3, abstract_text, tags, speakers, track",
            "semantic": "match on semantic_text abstract (Jina embeddings via EIS)",
            "hybrid": "RRF fusion of keyword + semantic retrievers",
        },
    }

    return {
        "query": query,
        "modes": by_mode,
        "explanation": explanation,
    }


def explain_results(
    *,
    query: str,
    mode: str,
    talks: list[dict[str, Any]],
    messages: list[dict[str, Any]] | None = None,
    interests: dict[str, str] | None = None,
    filters: dict[str, Any] | None = None,
    plan_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "query": query,
        "mode": mode,
        "filters": filters or {},
        "methods": {
            "keyword": "BM25",
            "semantic": "Jina semantic_text",
            "hybrid": "RRF(keyword, semantic)",
        }.get(mode, mode),
        "top_talks": [
            {
                "talk_id": t.get("talk_id"),
                "title": t.get("title"),
                "score": t.get("score"),
                "room": t.get("room"),
                "start": t.get("start"),
                "track": t.get("track"),
            }
            for t in talks[:6]
        ],
        "chat_hits": [
            {"sender": m.get("sender"), "body": m.get("body"), "score": m.get("score")}
            for m in (messages or [])[:4]
        ],
        "interest_map": interests or {},
        "plan_items": plan_items or [],
    }
