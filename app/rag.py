from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Generator, Iterable

from elasticsearch import Elasticsearch

from app.compare import explain_results
from app.config import get_settings
from app.search import SearchMode, search_messages, search_talks
from app.telemetry import log_query

MAX_TALKS = 4
MAX_MESSAGES = 3
ABSTRACT_CHARS = 400
MAX_TOKENS = 350


def _truncate(text: str | None, limit: int = ABSTRACT_CHARS) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_talks(talks: list[dict[str, Any]]) -> str:
    lines = []
    for t in talks[:MAX_TALKS]:
        lines.append(
            f"- [{t.get('talk_id')}] {t.get('title')} | track={t.get('track')} | "
            f"room={t.get('room')} | {t.get('start')}–{t.get('end')} | "
            f"speakers={', '.join(t.get('speakers') or [])}\n  {_truncate(t.get('abstract'))}"
        )
    return "\n".join(lines) if lines else "(no talks retrieved)"


def _format_messages(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages[:MAX_MESSAGES]:
        lines.append(
            f"- {m.get('sender')} @ {m.get('timestamp')}: {_truncate(m.get('body'), 240)}"
        )
    return "\n".join(lines) if messages else "(no chat hits)"


def _template_answer(question: str, talks: list[dict[str, Any]], messages: list[dict[str, Any]]) -> str:
    parts = [f"Based on Elasticsearch retrieval for: “{question}”\n"]
    if talks:
        parts.append("**Talks that match:**")
        for t in talks[:5]:
            parts.append(
                f"• **{t['title']}** ({t.get('room')}, {t.get('start')}) — {_truncate(t.get('abstract'), 220)}"
            )
    if messages:
        parts.append("\n**From your crew chat:**")
        for m in messages[:5]:
            parts.append(f"• {m.get('sender')}: {m.get('body')}")
    if not talks and not messages:
        parts.append("I couldn’t find matching talks or chat messages. Try a broader query.")
    return "\n".join(parts)


def _cache_key(
    *,
    intent: str,
    mode: str,
    query: str,
    talk_ids: Iterable[str],
    message_ids: Iterable[str],
) -> str:
    payload = {
        "intent": intent,
        "mode": mode,
        "query": query.strip().lower(),
        "talk_ids": list(talk_ids),
        "message_ids": list(message_ids),
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_answer(es: Elasticsearch, cache_key: str) -> str | None:
    settings = get_settings()
    try:
        doc = es.get(index=settings.llm_cache_index, id=cache_key)
        return (doc.get("_source") or {}).get("answer")
    except Exception:  # noqa: BLE001
        return None


def put_cached_answer(
    es: Elasticsearch,
    *,
    cache_key: str,
    intent: str,
    mode: str,
    query: str,
    answer: str,
    talk_ids: list[str],
    message_ids: list[str],
) -> None:
    settings = get_settings()
    try:
        es.index(
            index=settings.llm_cache_index,
            id=cache_key,
            document={
                "cache_key": cache_key,
                "intent": intent,
                "mode": mode,
                "query": query,
                "answer": answer,
                "model": settings.openai_model,
                "talk_ids": talk_ids,
                "message_ids": message_ids,
                "created_at": datetime.utcnow().isoformat(),
            },
            refresh=False,
        )
    except Exception:  # noqa: BLE001
        pass


def should_skip_llm(talks: list[dict[str, Any]], messages: list[dict[str, Any]]) -> bool:
    return not talks and not messages


def synthesize_answer(
    question: str,
    talks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    es: Elasticsearch | None = None,
    intent: str = "ask",
    mode: str = "hybrid",
) -> str:
    """Non-streaming synthesis with cache + caps. Used by /ask and planner."""
    if should_skip_llm(talks, messages):
        return _template_answer(question, talks, messages)

    talk_ids = [t.get("talk_id") for t in talks[:MAX_TALKS] if t.get("talk_id")]
    message_ids = [m.get("message_id") for m in messages[:MAX_MESSAGES] if m.get("message_id")]
    key = _cache_key(
        intent=intent, mode=mode, query=question, talk_ids=talk_ids, message_ids=message_ids
    )
    if es is not None:
        cached = get_cached_answer(es, key)
        if cached:
            return cached

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
            "If evidence is missing, say so. Keep answers concise (under 180 words)."
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
            max_tokens=MAX_TOKENS,
        )
        answer = (resp.choices[0].message.content or "").strip() or _template_answer(
            question, talks, messages
        )
        if es is not None:
            put_cached_answer(
                es,
                cache_key=key,
                intent=intent,
                mode=mode,
                query=question,
                answer=answer,
                talk_ids=talk_ids,
                message_ids=message_ids,
            )
        return answer
    except Exception as exc:  # noqa: BLE001
        return _template_answer(question, talks, messages) + f"\n\n_(LLM fallback: {exc})_"


def synthesize_answer_stream(
    question: str,
    talks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    es: Elasticsearch,
    intent: str = "ask",
    mode: str = "hybrid",
) -> Generator[dict[str, Any], None, str]:
    """Yield SSE-friendly dicts; return the full answer string at the end."""
    if should_skip_llm(talks, messages):
        answer = _template_answer(question, talks, messages)
        yield {"event": "token", "data": {"text": answer}}
        return answer

    talk_ids = [t.get("talk_id") for t in talks[:MAX_TALKS] if t.get("talk_id")]
    message_ids = [m.get("message_id") for m in messages[:MAX_MESSAGES] if m.get("message_id")]
    key = _cache_key(
        intent=intent, mode=mode, query=question, talk_ids=talk_ids, message_ids=message_ids
    )
    cached = get_cached_answer(es, key)
    if cached:
        yield {"event": "status", "data": {"phase": "cache_hit"}}
        # chunk cached text for a live feel without spending tokens
        step = 48
        for i in range(0, len(cached), step):
            yield {"event": "token", "data": {"text": cached[i : i + step]}}
        return cached

    settings = get_settings()
    if not settings.openai_api_key:
        answer = _template_answer(question, talks, messages)
        yield {"event": "token", "data": {"text": answer}}
        return answer

    yield {"event": "status", "data": {"phase": "generating"}}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        system = (
            "You are CrewBot, a conference group-chat assistant. "
            "Answer ONLY using the retrieved Elasticsearch talks and chat snippets. "
            "Cite talk titles, rooms, and times. Keep answers concise (under 180 words)."
        )
        user = (
            f"Question: {question}\n\n"
            f"Retrieved talks:\n{_format_talks(talks)}\n\n"
            f"Retrieved crew chat:\n{_format_messages(messages)}"
        )
        stream = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=MAX_TOKENS,
            stream=True,
        )
        chunks: list[str] = []
        for event in stream:
            delta = ""
            try:
                delta = event.choices[0].delta.content or ""
            except Exception:  # noqa: BLE001
                delta = ""
            if delta:
                chunks.append(delta)
                yield {"event": "token", "data": {"text": delta}}
        answer = "".join(chunks).strip() or _template_answer(question, talks, messages)
        put_cached_answer(
            es,
            cache_key=key,
            intent=intent,
            mode=mode,
            query=question,
            answer=answer,
            talk_ids=talk_ids,
            message_ids=message_ids,
        )
        return answer
    except Exception as exc:  # noqa: BLE001
        answer = _template_answer(question, talks, messages) + f"\n\n_(LLM fallback: {exc})_"
        yield {"event": "token", "data": {"text": answer}}
        return answer


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
        size=MAX_TALKS + 2,
        start_after=start_after,
        start_before=start_before,
        near_lat=near_lat,
        near_lon=near_lon,
        room=room,
    )[:MAX_TALKS]
    messages = search_messages(es, question, group_id=group_id, mode=mode, size=MAX_MESSAGES + 2)[
        :MAX_MESSAGES
    ]
    answer = synthesize_answer(question, talks, messages, es=es, intent="ask", mode=mode)
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
