from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from elasticsearch import Elasticsearch

from app.compare import explain_results
from app.config import get_settings
from app.rag import synthesize_answer
from app.search import search_messages, search_talks
from app.telemetry import log_query

PlanMode = Literal["stick_together", "split"]

INTEREST_HINTS = {
    "Alex": "eBPF runtime security observability falco probes",
    "Sam": "AI ethics responsible RAG governance consent",
    "Jordan": "platform engineering internal developer platform golden paths IDP",
}


def detect_plan_intent(text: str) -> bool:
    t = text.lower()
    triggers = [
        "plan our",
        "plan the afternoon",
        "plan afternoon",
        "shared itinerary",
        "stick together",
        "split up",
        "what should we go",
        "schedule for us",
        "crew plan",
    ]
    return any(x in t for x in triggers)


def detect_chat_recall_intent(text: str) -> bool:
    t = text.lower()
    triggers = [
        "what did we",
        "what did i",
        "did we say",
        "remind me",
        "dinner",
        "ramen",
        "earlier",
        "we decide",
        "we decided",
    ]
    return any(x in t for x in triggers)


def detect_compare_intent(text: str) -> bool:
    t = text.lower()
    return any(
        x in t
        for x in [
            "compare search",
            "keyword vs",
            "semantic vs",
            "show hybrid",
            "triple mode",
            "compare modes",
        ]
    )


def _infer_mode(text: str) -> PlanMode:
    t = text.lower()
    if "split" in t:
        return "split"
    return "stick_together"


def _afternoon_window(day: datetime | None = None) -> tuple[datetime, datetime]:
    day = day or datetime(2026, 11, 9)
    start = day.replace(hour=13, minute=0, second=0, microsecond=0)
    end = day.replace(hour=18, minute=0, second=0, microsecond=0)
    return start, end


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def talks_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_start, a_end = _parse_dt(a.get("start")), _parse_dt(a.get("end"))
    b_start, b_end = _parse_dt(b.get("start")), _parse_dt(b.get("end"))
    if not all([a_start, a_end, b_start, b_end]):
        return False
    return a_start < b_end and a_end > b_start  # type: ignore[operator]


def _pick_non_overlapping(
    talks: list[dict[str, Any]],
    limit: int = 4,
) -> tuple[list[dict[str, Any]], int]:
    picked: list[dict[str, Any]] = []
    skipped = 0
    for talk in talks:
        if any(talks_overlap(talk, other) for other in picked):
            skipped += 1
            continue
        picked.append(talk)
        if len(picked) >= limit:
            break
    return picked, skipped


def _room_density(es: Elasticsearch) -> dict[str, int]:
    settings = get_settings()
    try:
        resp = es.search(
            index=settings.talks_index,
            size=0,
            aggs={"by_room": {"terms": {"field": "room", "size": 20}}},
        )
        return {
            b["key"]: b["doc_count"]
            for b in resp.get("aggregations", {}).get("by_room", {}).get("buckets", [])
        }
    except Exception:  # noqa: BLE001
        return {}


def build_crew_plan(
    es: Elasticsearch,
    request_text: str,
    *,
    group_id: str,
) -> dict[str, Any]:
    mode = _infer_mode(request_text)
    start_after, start_before = _afternoon_window()
    filters = {
        "start_after": start_after.isoformat(),
        "start_before": start_before.isoformat(),
        "mode": mode,
    }

    chat_hits = search_messages(
        es,
        "interests eBPF ethics platform engineering afternoon dinner ramen",
        group_id=group_id,
        mode="hybrid",
        size=12,
    )

    interests: dict[str, str] = dict(INTEREST_HINTS)
    for msg in chat_hits:
        sender = msg.get("sender") or ""
        body = (msg.get("body") or "").lower()
        if sender in interests:
            interests[sender] = f"{interests[sender]} {body}"[:420]

    named = re.findall(
        r"\b(eBPF|ethics|platform|security|RAG|kubernetes|observability)\b",
        request_text,
        re.I,
    )
    shared_query = " ".join(named) if named else "cloud native conference talks for a mixed crew"

    per_person_talks: dict[str, list[dict[str, Any]]] = {}
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()

    for person, interest in interests.items():
        hits = search_talks(
            es,
            interest,
            mode="hybrid",
            size=6,
            start_after=start_after,
            start_before=start_before,
        )
        per_person_talks[person] = hits
        for h in hits:
            tid = h.get("talk_id")
            if tid and tid not in seen:
                seen.add(tid)
                pool.append(h)

    for h in search_talks(
        es,
        shared_query or "platform security ai ethics",
        mode="hybrid",
        size=6,
        start_after=start_after,
        start_before=start_before,
    ):
        tid = h.get("talk_id")
        if tid and tid not in seen:
            seen.add(tid)
            pool.append(h)

    density = _room_density(es)
    conflicts_resolved = 0
    reconvene_room = "Hall B"

    if mode == "stick_together":
        scores: dict[str, float] = {}
        talk_by_id = {t["talk_id"]: t for t in pool if t.get("talk_id")}
        for person, hits in per_person_talks.items():
            for rank, h in enumerate(hits):
                tid = h.get("talk_id")
                if not tid:
                    continue
                # Overlap + rank + avoid overcrowded rooms slightly
                room = h.get("room") or ""
                crowd_penalty = 0.15 * density.get(room, 0)
                scores[tid] = scores.get(tid, 0) + (3.0 - rank * 0.35) - crowd_penalty

        ranked = sorted(
            talk_by_id.values(),
            key=lambda t: (-scores.get(t["talk_id"], 0), -(t.get("score") or 0)),
        )
        itinerary, conflicts_resolved = _pick_non_overlapping(ranked, limit=4)
        why = []
        for t in itinerary:
            fans = [
                p
                for p, hits in per_person_talks.items()
                if any(x.get("talk_id") == t["talk_id"] for x in hits)
            ]
            why.append(
                {
                    "talk_id": t["talk_id"],
                    "title": t["title"],
                    "room": t.get("room"),
                    "start": t.get("start"),
                    "end": t.get("end"),
                    "fits": fans or ["crew"],
                    "overlap_score": round(scores.get(t["talk_id"], 0), 2),
                    "room_density": density.get(t.get("room") or "", 0),
                    "reason": (
                        f"Crew overlap score {scores.get(t['talk_id'], 0):.1f}; "
                        f"track={t.get('track')}; room load={density.get(t.get('room') or '', 0)}"
                    ),
                }
            )
    else:
        itinerary = []
        why = []
        for person, hits in per_person_talks.items():
            pick, skipped = _pick_non_overlapping(hits, limit=1)
            conflicts_resolved += skipped
            if not pick:
                continue
            t = pick[0]
            itinerary.append(t)
            why.append(
                {
                    "talk_id": t["talk_id"],
                    "title": t["title"],
                    "room": t.get("room"),
                    "start": t.get("start"),
                    "end": t.get("end"),
                    "fits": [person],
                    "overlap_score": 1.0,
                    "room_density": density.get(t.get("room") or "", 0),
                    "reason": f"Best afternoon match for {person}; reconvene at {reconvene_room}",
                }
            )

    narrative = synthesize_answer(
        f"Create a {mode} afternoon plan for the crew. Request: {request_text}. "
        f"Conflicts skipped while packing schedule: {conflicts_resolved}.",
        itinerary,
        chat_hits,
    )

    itinerary_id = f"itin-{uuid.uuid4().hex[:10]}"
    doc = {
        "itinerary_id": itinerary_id,
        "group_id": group_id,
        "mode": mode,
        "summary": narrative,
        "talk_ids": [t.get("talk_id") for t in itinerary if t.get("talk_id")],
        "created_at": datetime.utcnow().isoformat(),
        "interests": list(interests.keys()),
        "conflicts_resolved": conflicts_resolved,
        "reconvene_room": reconvene_room if mode == "split" else None,
    }
    settings = get_settings()
    try:
        es.index(index=settings.itineraries_index, id=itinerary_id, document=doc, refresh=True)
    except Exception:  # noqa: BLE001
        pass

    explanation = explain_results(
        query=request_text,
        mode="hybrid",
        talks=itinerary,
        messages=chat_hits,
        interests=interests,
        filters=filters,
        plan_items=why,
    )
    explanation["conflicts_resolved"] = conflicts_resolved
    explanation["plan_mode"] = mode
    explanation["reconvene_room"] = reconvene_room if mode == "split" else None

    query_id = log_query(
        es,
        query=request_text,
        group_id=group_id,
        mode="hybrid",
        kind="plan",
        result_ids=[t.get("talk_id") for t in itinerary if t.get("talk_id")],
        filters=filters,
        explanation=explanation,
    )

    return {
        "itinerary_id": itinerary_id,
        "query_id": query_id,
        "mode": mode,
        "summary": narrative,
        "items": why,
        "talks": itinerary,
        "chat_context": chat_hits[:5],
        "conflicts_resolved": conflicts_resolved,
        "reconvene_room": reconvene_room if mode == "split" else None,
        "explanation": explanation,
    }
