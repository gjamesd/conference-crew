from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Generator

from elasticsearch import Elasticsearch

from app.compare import compare_talk_search, explain_results
from app.config import get_settings
from app.planner import (
    build_crew_plan,
    detect_chat_recall_intent,
    detect_compare_intent,
    detect_plan_intent,
)
from app.rag import MAX_MESSAGES, MAX_TALKS, ask, synthesize_answer_stream
from app.search import list_messages, search_messages, search_talks
from app.telemetry import log_query


def index_message(
    es: Elasticsearch,
    *,
    group_id: str,
    sender: str,
    body: str,
    role: str = "user",
    source: str = "web",
    message_id: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    message_id = message_id or f"msg-{uuid.uuid4().hex[:12]}"
    timestamp = timestamp or datetime.utcnow()
    doc = {
        "message_id": message_id,
        "group_id": group_id,
        "sender": sender,
        "body": body,
        "body_text": body,
        "role": role,
        "timestamp": timestamp.isoformat(),
        "source": source,
    }
    es.index(index=settings.messages_index, id=message_id, document=doc, refresh="wait_for")
    return doc


def _format_compare(compare: dict[str, Any]) -> str:
    exp = compare.get("explanation") or {}
    lines = [
        "**Search mode compare** (keyword vs semantic/Jina vs hybrid RRF)",
        exp.get("narrative") or "",
        "",
    ]
    for mode in ("keyword", "semantic", "hybrid"):
        lines.append(f"**{mode}**")
        for t in (compare.get("modes") or {}).get(mode) or []:
            lines.append(
                f"• {t.get('talk_id')} · {t.get('title')} "
                f"(score={t.get('score')}, {t.get('room')}, {t.get('start')})"
            )
        lines.append("")
    return "\n".join(lines).strip()


def post_user_message(
    es: Elasticsearch,
    *,
    group_id: str,
    sender: str,
    body: str,
) -> dict[str, Any]:
    user_doc = index_message(es, group_id=group_id, sender=sender, body=body, role="user")

    bot_text: str
    meta: dict[str, Any] = {}

    if detect_compare_intent(body) or body.lower().startswith("compare:"):
        query = body.split(":", 1)[1].strip() if ":" in body else body
        for prefix in ("compare search", "compare modes", "triple mode"):
            if query.lower().startswith(prefix):
                query = query[len(prefix) :].strip(" :-")
        if not query:
            query = "eBPF and security"
        compare = compare_talk_search(es, query, size=4)
        meta["compare"] = compare
        meta["explanation"] = compare.get("explanation")
        query_id = log_query(
            es,
            query=query,
            group_id=group_id,
            mode="compare",
            kind="compare",
            result_ids=(compare.get("explanation") or {}).get("hybrid_rescues") or [],
            explanation=compare.get("explanation"),
        )
        meta["query_id"] = query_id
        bot_text = _format_compare(compare)
    elif detect_plan_intent(body):
        plan = build_crew_plan(es, body, group_id=group_id)
        meta["plan"] = plan
        meta["explanation"] = plan.get("explanation")
        meta["query_id"] = plan.get("query_id")
        lines = [plan["summary"], "", "**Proposed itinerary:**"]
        for item in plan.get("items") or []:
            fits = ", ".join(item.get("fits") or [])
            lines.append(
                f"• {item.get('start')} · {item.get('room')} · **{item.get('title')}** "
                f"(fits: {fits}; overlap={item.get('overlap_score')}) — {item.get('reason')}"
            )
        if plan.get("conflicts_resolved"):
            lines.append(
                f"\n_Skipped {plan['conflicts_resolved']} conflicting slot(s) while packing._"
            )
        if plan.get("reconvene_room"):
            lines.append(f"\nReconvene at **{plan['reconvene_room']}** after split sessions.")
        bot_text = "\n".join(lines)
    elif detect_chat_recall_intent(body):
        result = ask(es, body, group_id=group_id, mode="hybrid")
        meta["ask"] = result
        meta["explanation"] = result.get("explanation")
        meta["query_id"] = result.get("query_id")
        bot_text = result["answer"]
    else:
        result = ask(es, body, group_id=group_id, mode="hybrid")
        meta["ask"] = result
        meta["explanation"] = result.get("explanation")
        meta["query_id"] = result.get("query_id")
        bot_text = result["answer"]

    bot_doc = index_message(
        es,
        group_id=group_id,
        sender="CrewBot",
        body=bot_text,
        role="assistant",
    )

    return {
        "user_message": user_doc,
        "bot_message": bot_doc,
        "meta": meta,
        "thread": list_messages(es, group_id),
    }


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def stream_user_message(
    es: Elasticsearch,
    *,
    group_id: str,
    sender: str,
    body: str,
) -> Generator[str, None, None]:
    """SSE generator: status → explanation → talks → token* → done."""
    user_doc = index_message(es, group_id=group_id, sender=sender, body=body, role="user")
    yield _sse("status", {"phase": "indexed", "user_message": user_doc})

    meta: dict[str, Any] = {}
    bot_text = ""

    if detect_compare_intent(body) or body.lower().startswith("compare:"):
        yield _sse("status", {"phase": "comparing"})
        query = body.split(":", 1)[1].strip() if ":" in body else body
        for prefix in ("compare search", "compare modes", "triple mode"):
            if query.lower().startswith(prefix):
                query = query[len(prefix) :].strip(" :-")
        if not query:
            query = "eBPF and security"
        compare = compare_talk_search(es, query, size=4)
        meta["compare"] = compare
        meta["explanation"] = compare.get("explanation")
        query_id = log_query(
            es,
            query=query,
            group_id=group_id,
            mode="compare",
            kind="compare",
            result_ids=(compare.get("explanation") or {}).get("hybrid_rescues") or [],
            explanation=compare.get("explanation"),
        )
        meta["query_id"] = query_id
        talks = (compare.get("modes") or {}).get("hybrid") or []
        yield _sse("explanation", meta["explanation"] or {})
        yield _sse("talks", {"talks": talks})
        bot_text = _format_compare(compare)
        yield _sse("token", {"text": bot_text})

    elif detect_plan_intent(body):
        yield _sse("status", {"phase": "planning"})
        plan = build_crew_plan(es, body, group_id=group_id)
        meta["plan"] = plan
        meta["explanation"] = plan.get("explanation")
        meta["query_id"] = plan.get("query_id")
        yield _sse("explanation", meta["explanation"] or {})
        yield _sse("talks", {"talks": plan.get("talks") or []})
        lines = [plan["summary"], "", "**Proposed itinerary:**"]
        for item in plan.get("items") or []:
            fits = ", ".join(item.get("fits") or [])
            lines.append(
                f"• {item.get('start')} · {item.get('room')} · **{item.get('title')}** "
                f"(fits: {fits}; overlap={item.get('overlap_score')}) — {item.get('reason')}"
            )
        if plan.get("conflicts_resolved"):
            lines.append(
                f"\n_Skipped {plan['conflicts_resolved']} conflicting slot(s) while packing._"
            )
        if plan.get("reconvene_room"):
            lines.append(f"\nReconvene at **{plan['reconvene_room']}** after split sessions.")
        bot_text = "\n".join(lines)
        # Plan summary already LLM'd (cached); stream in chunks for UX
        step = 56
        for i in range(0, len(bot_text), step):
            yield _sse("token", {"text": bot_text[i : i + step]})

    else:
        yield _sse("status", {"phase": "searching"})
        intent = "recall" if detect_chat_recall_intent(body) else "ask"
        talks = search_talks(es, body, mode="hybrid", size=MAX_TALKS)
        messages = search_messages(es, body, group_id=group_id, mode="hybrid", size=MAX_MESSAGES)
        explanation = explain_results(
            query=body, mode="hybrid", talks=talks, messages=messages, filters={}
        )
        query_id = log_query(
            es,
            query=body,
            group_id=group_id,
            mode="hybrid",
            kind=intent,
            result_ids=[t.get("talk_id") for t in talks if t.get("talk_id")],
            explanation=explanation,
        )
        meta["explanation"] = explanation
        meta["query_id"] = query_id
        meta["ask"] = {"talks": talks, "messages": messages, "mode": "hybrid", "query_id": query_id}
        yield _sse("explanation", explanation)
        yield _sse("talks", {"talks": talks})

        pieces: list[str] = []
        for evt in synthesize_answer_stream(
            body, talks, messages, es=es, intent=intent, mode="hybrid"
        ):
            if evt.get("event") == "status":
                yield _sse("status", evt.get("data") or {})
            elif evt.get("event") == "token":
                text = (evt.get("data") or {}).get("text") or ""
                pieces.append(text)
                yield _sse("token", {"text": text})
        bot_text = "".join(pieces)
        meta["ask"]["answer"] = bot_text

    bot_doc = index_message(
        es,
        group_id=group_id,
        sender="CrewBot",
        body=bot_text,
        role="assistant",
    )
    yield _sse(
        "done",
        {
            "bot_message": bot_doc,
            "meta": meta,
            "thread": list_messages(es, group_id),
            "query_id": meta.get("query_id"),
        },
    )
