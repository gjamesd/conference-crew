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
MAX_CHAT_MESSAGES = 12
ABSTRACT_CHARS = 400
MAX_TOKENS = 350
MAX_TOKENS_CHAT = 450


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


def _format_messages(messages: list[dict[str, Any]], limit: int = MAX_MESSAGES) -> str:
    lines = []
    for m in messages[:limit]:
        lines.append(
            f"- {m.get('sender')} @ {m.get('timestamp')}: {_truncate(m.get('body'), 240)}"
        )
    return "\n".join(lines) if messages else "(no chat hits)"


def _is_chat_grounded_intent(intent: str) -> bool:
    return intent in {"chat_summary", "sprint_plan", "recall", "linear_link"}


def _system_prompt_for_intent(intent: str) -> str:
    if intent == "chat_summary":
        return (
            "You are CrewBot helping a product crew. Use ONLY the crew chat snippets. "
            "Do NOT invent conference talks or sessions. "
            "Reply with: (1) 2–4 sentences on what the thread is about, "
            "(2) a ranked 'Most important' bullet list (max 5) grounded in the chat. "
            "Name owners (Alex/Sam/Jordan) when the chat does. Under 200 words."
        )
    if intent == "sprint_plan":
        return (
            "You are CrewBot drafting a sprint-planning artifact from crew chat ONLY. "
            "Do NOT invent conference talks. Structure markdown exactly as:\n"
            "## Sprint goal\n"
            "## In-scope MVP\n"
            "## Backlog (owner + why)\n"
            "## Explicitly out of scope\n"
            "## Risks & dependencies\n"
            "## Success / dogfood metrics\n"
            "## If we only finish 70%\n"
            "Keep it presentation-ready, under 280 words."
        )
    if intent == "linear_link":
        return (
            "You are CrewBot preparing a sprint planning session. "
            "Use ONLY the crew chat + the Linear issue catalog provided. "
            "Do NOT invent conference talks, rooms, or sessions. "
            "Start with one short sentence: these are what we need to plan for the sprint "
            "planning session, with associated Linear issues. "
            "Then numbered bullets: problem from chat → Linear identifier(s) + full Linear URL "
            "+ owner. Use only IDs/URLs from the catalog. Under 250 words."
        )
    if intent == "recall":
        return (
            "You are CrewBot. Answer ONLY from crew chat snippets. "
            "Quote who said what. Do not invent talks. Keep under 150 words."
        )
    return (
        "You are CrewBot, a conference group-chat assistant. "
        "Answer ONLY using the retrieved Elasticsearch talks and chat snippets. "
        "Cite talk titles, rooms, and times. If chat history is relevant, quote it briefly. "
        "If evidence is missing, say so. Keep answers concise (under 180 words)."
    )


def _template_answer(
    question: str,
    talks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    intent: str = "ask",
) -> str:
    if intent == "chat_summary":
        if not messages:
            return "I don’t have enough crew chat yet to summarize. Let the crew keep talking, then ask again."
        parts = [
            "**What this thread is about (from chat):**",
            "The crew is discussing product work captured in Elasticsearch chat messages.",
            "",
            "**Most important (chat evidence):**",
        ]
        for m in messages[:6]:
            parts.append(f"• {m.get('sender')}: {_truncate(m.get('body'), 180)}")
        return "\n".join(parts)

    if intent == "sprint_plan":
        if not messages:
            return "No crew chat to plan from yet. Advance the demo beats, then ask for a sprint plan."
        parts = [
            "## Sprint goal",
            "Ship the MVP the crew described in chat (see evidence below).",
            "",
            "## Backlog (owner + why)",
        ]
        for m in messages[:8]:
            parts.append(f"• **{m.get('sender')}** — {_truncate(m.get('body'), 160)}")
        parts.extend(
            [
                "",
                "## Risks & dependencies",
                "• Derived from chat — refine after LLM is available.",
                "",
                "## Success / dogfood metrics",
                "• Demo path works end-to-end without violating constraints stated in chat.",
            ]
        )
        return "\n".join(parts)

    if intent == "linear_link":
        from app.linear_sync import template_linear_link_answer

        return template_linear_link_answer()

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


def collect_chat_context(
    es: Elasticsearch,
    question: str,
    *,
    group_id: str,
    size: int = MAX_CHAT_MESSAGES,
) -> list[dict[str, Any]]:
    """Merge recent thread + hybrid chat hits; chat-only (no talks)."""
    from app.search import list_messages

    recent = list_messages(es, group_id, size=80)
    # Prefer human crew lines for grounding; keep a little bot context out.
    humans = [
        m
        for m in recent
        if (m.get("sender") or "") != "CrewBot" and (m.get("role") or "") != "assistant"
    ]
    hits = search_messages(es, question, group_id=group_id, mode="hybrid", size=size)
    by_id: dict[str, dict[str, Any]] = {}
    for m in humans[-size:] + hits:
        mid = m.get("message_id") or f"{m.get('sender')}-{m.get('timestamp')}-{m.get('body')}"
        by_id[mid] = m
    # Stable-ish order: chronological if timestamp present
    merged = list(by_id.values())

    def sort_key(m: dict[str, Any]) -> str:
        return str(m.get("timestamp") or "")

    merged.sort(key=sort_key)
    return merged[-size:]


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


def _msg_limit_for_intent(intent: str) -> int:
    return MAX_CHAT_MESSAGES if _is_chat_grounded_intent(intent) else MAX_MESSAGES


def _token_budget_for_intent(intent: str) -> int:
    return (
        MAX_TOKENS_CHAT
        if intent in {"chat_summary", "sprint_plan", "linear_link"}
        else MAX_TOKENS
    )


def _user_prompt(
    question: str,
    talks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    intent: str,
) -> str:
    msg_limit = _msg_limit_for_intent(intent)
    if intent == "linear_link":
        from app.linear_sync import format_issue_catalog

        return (
            f"Question: {question}\n\n"
            f"Crew chat (Elasticsearch):\n{_format_messages(messages, limit=msg_limit)}\n\n"
            f"Linear issue catalog (use only these IDs/URLs):\n{format_issue_catalog()}\n\n"
            "Map chat problems to Linear issues for sprint planning. No conference talks."
        )
    if _is_chat_grounded_intent(intent):
        return (
            f"Question: {question}\n\n"
            f"Crew chat (Elasticsearch):\n{_format_messages(messages, limit=msg_limit)}\n\n"
            "Use only this chat evidence."
        )
    return (
        f"Question: {question}\n\n"
        f"Retrieved talks:\n{_format_talks(talks)}\n\n"
        f"Retrieved crew chat:\n{_format_messages(messages, limit=msg_limit)}"
    )


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
        return _template_answer(question, talks, messages, intent=intent)

    msg_limit = _msg_limit_for_intent(intent)
    talk_ids = (
        []
        if _is_chat_grounded_intent(intent)
        else [t.get("talk_id") for t in talks[:MAX_TALKS] if t.get("talk_id")]
    )
    message_ids = [m.get("message_id") for m in messages[:msg_limit] if m.get("message_id")]
    key = _cache_key(
        intent=intent, mode=mode, query=question, talk_ids=talk_ids, message_ids=message_ids
    )
    if es is not None:
        cached = get_cached_answer(es, key)
        if cached:
            return cached

    settings = get_settings()
    if not settings.openai_api_key:
        return _template_answer(question, talks, messages, intent=intent)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _system_prompt_for_intent(intent)},
                {
                    "role": "user",
                    "content": _user_prompt(question, talks, messages, intent=intent),
                },
            ],
            temperature=0.2,
            max_tokens=_token_budget_for_intent(intent),
        )
        answer = (resp.choices[0].message.content or "").strip() or _template_answer(
            question, talks, messages, intent=intent
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
        return (
            _template_answer(question, talks, messages, intent=intent)
            + f"\n\n_(LLM fallback: {exc})_"
        )


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
        answer = _template_answer(question, talks, messages, intent=intent)
        yield {"event": "token", "data": {"text": answer}}
        return answer

    msg_limit = _msg_limit_for_intent(intent)
    talk_ids = (
        []
        if _is_chat_grounded_intent(intent)
        else [t.get("talk_id") for t in talks[:MAX_TALKS] if t.get("talk_id")]
    )
    message_ids = [m.get("message_id") for m in messages[:msg_limit] if m.get("message_id")]
    key = _cache_key(
        intent=intent, mode=mode, query=question, talk_ids=talk_ids, message_ids=message_ids
    )
    cached = get_cached_answer(es, key)
    if cached:
        yield {"event": "status", "data": {"phase": "cache_hit"}}
        step = 48
        for i in range(0, len(cached), step):
            yield {"event": "token", "data": {"text": cached[i : i + step]}}
        return cached

    settings = get_settings()
    if not settings.openai_api_key:
        answer = _template_answer(question, talks, messages, intent=intent)
        yield {"event": "token", "data": {"text": answer}}
        return answer

    yield {"event": "status", "data": {"phase": "generating"}}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        stream = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _system_prompt_for_intent(intent)},
                {
                    "role": "user",
                    "content": _user_prompt(question, talks, messages, intent=intent),
                },
            ],
            temperature=0.2,
            max_tokens=_token_budget_for_intent(intent),
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
        answer = "".join(chunks).strip() or _template_answer(
            question, talks, messages, intent=intent
        )
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
        answer = (
            _template_answer(question, talks, messages, intent=intent)
            + f"\n\n_(LLM fallback: {exc})_"
        )
        yield {"event": "token", "data": {"text": answer}}
        return answer


def _template_sprint_json(messages: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = [
        {"sender": m.get("sender"), "body": m.get("body"), "timestamp": m.get("timestamp")}
        for m in messages[:MAX_CHAT_MESSAGES]
    ]
    return {
        "sprint": {
            "name": "Glasses AI — next sprint",
            "goal": "Ship menu OCR + TTS, on-device ASR intents, dining/transit categorizer v0",
            "workstreams": [
                {
                    "owner": "Alex",
                    "area": "MenuOCR / on-device text",
                    "status": "in_progress",
                    "features": [
                        "confidence gate",
                        "font pack",
                        "TTS menu summary",
                    ],
                    "blockers": ["thermal contention with ASR", "font license"],
                },
                {
                    "owner": "Sam",
                    "area": "Audio / wake-word / ASR intents",
                    "status": "in_progress",
                    "features": [
                        "wake-word tighten",
                        "read_menu/whats_around/stop",
                        "tap-to-share privacy stub",
                    ],
                    "blockers": ["no continuous cloud audio", "false wakes", "battery"],
                },
                {
                    "owner": "Jordan",
                    "area": "Scene categorization / fusion",
                    "status": "in_progress",
                    "features": ["dining vs transit vs unknown", "confidence threshold", "eval harness"],
                    "blockers": ["labeled glasses dataset", "feature bus timing"],
                },
            ],
            "out_of_scope": [
                "live translation",
                "continuous cloud ASR",
                "10-way taxonomy",
            ],
            "dependencies": [
                {
                    "from": "Sam",
                    "to": "Alex/Jordan",
                    "item": "speech_active + asr_intent on feature bus by Wednesday",
                }
            ],
            "success_metrics": [
                "MenuOCR golden menus ≥85% top-3 dish extraction",
                "cafe false-wake ≤1.2/hour",
                "dining recall ≥0.75 or heuristic fallback",
            ],
            "fallback_70_percent": [
                "MenuOCR + TTS",
                "on-device intents + privacy stub",
                "dining heuristic from strong OCR",
            ],
        },
        "source": "template",
        "evidence_messages": evidence,
    }


def build_sprint_artifact(
    question: str,
    messages: list[dict[str, Any]],
    *,
    markdown: str,
) -> dict[str, Any]:
    """Structured JSON for sprint planning tools + markdown doc."""
    settings = get_settings()
    fallback = _template_sprint_json(messages)
    fallback["document_markdown"] = markdown
    if not settings.openai_api_key or not messages:
        return fallback

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        schema_hint = (
            '{"sprint":{"name":"","goal":"","workstreams":[{"owner":"","area":"","status":"",'
            '"features":[],"blockers":[],"acceptance":[]}],"out_of_scope":[],'
            '"dependencies":[{"from":"","to":"","item":""}],"risks":[],'
            '"success_metrics":[],"fallback_70_percent":[],"schedule":[]}}'
        )
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract a sprint planning JSON from the meeting chat ONLY. "
                        "Return valid JSON matching this shape: " + schema_hint
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Request: {question}\n\nMeeting chat:\n"
                        f"{_format_messages(messages, limit=MAX_CHAT_MESSAGES)}\n\n"
                        f"Markdown draft already written:\n{_truncate(markdown, 1200)}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return fallback
        data["document_markdown"] = markdown
        data["source"] = "openai"
        data["evidence_count"] = len(messages)
        return data
    except Exception as exc:  # noqa: BLE001
        fallback["source"] = f"template_fallback:{exc}"
        return fallback


def _with_sessions(answer: str, sessions: list[dict[str, Any]]) -> str:
    section = ""
    if sessions:
        from app.planner import format_sessions_section

        section = format_sessions_section(sessions)
    if not section:
        return answer
    return f"{answer.rstrip()}{section}"


def ask_chat_thread(
    es: Elasticsearch,
    question: str,
    *,
    group_id: str,
    intent: str = "chat_summary",
) -> dict[str, Any]:
    """Chat-grounded ask. Summary and sprint also attach talks that fit the thread."""
    messages = collect_chat_context(es, question, group_id=group_id)
    sessions: list[dict[str, Any]] = []
    if intent in {"chat_summary", "sprint_plan"}:
        from app.planner import sessions_for_meeting

        sessions = sessions_for_meeting(es, messages)
    answer = synthesize_answer(
        question, [], messages, es=es, intent=intent, mode="chat"
    )
    artifact = None
    if intent == "sprint_plan":
        artifact = build_sprint_artifact(question, messages, markdown=answer)
        if artifact is not None:
            artifact["sessions"] = sessions
            markdown = artifact.get("document_markdown") or answer
            artifact["document_markdown"] = _with_sessions(markdown, sessions)
    answer = _with_sessions(answer, sessions)
    explanation = {
        "query": question,
        "mode": "chat",
        "methods": "message hybrid + recent thread"
        + (" + talk hybrid per person" if sessions else " (no talks)"),
        "narrative": (
            f"Chat-grounded {intent}: {len(messages)} message(s), {len(sessions)} session(s)."
        ),
        "top_talks": sessions,
        "chat_hits": [
            {
                "sender": m.get("sender"),
                "body": _truncate(m.get("body"), 120),
                "score": m.get("score"),
            }
            for m in messages[:8]
        ],
        "interest_map": {},
        "plan_items": [],
        "has_artifact": bool(artifact),
    }
    query_id = log_query(
        es,
        query=question,
        group_id=group_id,
        mode="chat",
        kind=intent,
        result_ids=[m.get("message_id") for m in messages if m.get("message_id")],
        explanation=explanation,
    )
    return {
        "answer": answer,
        "talks": sessions,
        "messages": messages,
        "mode": "chat",
        "query_id": query_id,
        "explanation": explanation,
        "intent": intent,
        "artifact": artifact,
    }


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
