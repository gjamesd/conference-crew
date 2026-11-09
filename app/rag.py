from __future__ import annotations

from datetime import datetime
from typing import Any

from elasticsearch import Elasticsearch

from app.compare import explain_results
from app.config import get_settings
from app.search import SearchMode, search_messages, search_talks
from app.telemetry import log_query


def _format_talks(talks: list[dict[str, Any]]) -> str:
    lines = []
    for t in talks:
        lines.append(
            f"- [{t.get('talk_id')}] {t.get('title')} | track={t.get('track')} | "
            f"room={t.get('room')} | {t.get('start')}–{t.get('end')} | "
            f"speakers={', '.join(t.get('speakers') or [])}\n  {t.get('abstract')}"
        )
    return "\n".join(lines) if lines else "(no talks retrieved)"


def _format_messages(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        lines.append(f"- {m.get('sender')} @ {m.get('timestamp')}: {m.get('body')}")
    return "\n".join(lines) if messages else "(no chat hits)"


def _template_answer(question: str, talks: list[dict[str, Any]], messages: list[dict[str, Any]]) -> str:
    parts = [f"Based on Elasticsearch retrieval for: “{question}”\n"]
    if talks:
        parts.append("**Talks that match:**")
        for t in talks[:5]:
            parts.append(
                f"• **{t['title']}** ({t.get('room')}, {t.get('start')}) — {t.get('abstract')}"
            )
    if messages:
        parts.append("\n**From your crew chat:**")
        for m in messages[:5]:
            parts.append(f"• {m.get('sender')}: {m.get('body')}")
    if not talks and not messages:
        parts.append("I couldn’t find matching talks or chat messages. Try a broader query.")
    return "\n".join(parts)


def synthesize_answer(
    question: str,
    talks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> str:
    settings = get_settings()
    if not settings.openai_api_key:
        return _template_answer(question, talks, messages)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        system = (
            "You are CrewBot, a conference group-chat assistant. "
            "Answer ONLY using the retrieved Elasticsearch talks and chat snippets. "
            "Cite talk titles, rooms, and times. If chat history is relevant, quote it briefly. "
            "If evidence is missing, say so. Keep answers concise and useful for a squad deciding what to attend."
        )
        user = (
            f"Question: {question}\n\n"
            f"Retrieved talks:\n{_format_talks(talks)}\n\n"
            f"Retrieved crew chat:\n{_format_messages(messages)}"
        )
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip() or _template_answer(
            question, talks, messages
        )
    except Exception as exc:  # noqa: BLE001 — fall back for demo resilience
        return _template_answer(question, talks, messages) + f"\n\n_(LLM fallback: {exc})_"


def ask(
    es: Elasticsearch,
    question: str,
    *,
    group_id: str,
    mode: SearchMode = "hybrid",
    start_after: datetime | None = None,
    start_before: datetime | None = None,
    near_lat: float | None = None,
    near_lon: float | None = None,
    room: str | None = None,
) -> dict[str, Any]:
    talks = search_talks(
        es,
        question,
        mode=mode,
        size=6,
        start_after=start_after,
        start_before=start_before,
        near_lat=near_lat,
        near_lon=near_lon,
        room=room,
    )
    messages = search_messages(es, question, group_id=group_id, mode=mode, size=6)
    answer = synthesize_answer(question, talks, messages)
    filters = {
        "start_after": start_after.isoformat() if start_after else None,
        "start_before": start_before.isoformat() if start_before else None,
        "near_lat": near_lat,
        "near_lon": near_lon,
        "room": room,
    }
    explanation = explain_results(
        query=question,
        mode=mode,
        talks=talks,
        messages=messages,
        filters=filters,
    )
    query_id = log_query(
        es,
        query=question,
        group_id=group_id,
        mode=mode,
        kind="ask",
        result_ids=[t.get("talk_id") for t in talks if t.get("talk_id")],
        filters=filters,
        explanation=explanation,
    )
    return {
        "answer": answer,
        "talks": talks,
        "messages": messages,
        "mode": mode,
        "query_id": query_id,
        "explanation": explanation,
    }
