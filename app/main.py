from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.chat import index_message, post_user_message, stream_user_message
from app.compare import compare_talk_search
from app.config import get_settings
from app.demo_script import CREW_SENDERS, advance_demo_beat, load_demo_script
from app.es_client import ElasticsearchNotConfigured, ensure_indices, get_es
from app.imessage_import import import_rows, parse_export_csv, parse_export_json, read_messages_db
from app.planner import build_crew_plan
from app.rag import ask
from app.search import list_messages, resolve_near_room, search_messages, search_talks
from app.stats import conference_stats
from app.telemetry import save_feedback

ROOT = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(ROOT / "templates"))

app = FastAPI(
    title="Conference Crew",
    description="Group-chat talk finder + shared itinerary planner powered by Elasticsearch + Jina",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


class SearchTalksRequest(BaseModel):
    query: str
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    size: int = Field(default=8, ge=1, le=30)
    start_after: Optional[datetime] = None
    start_before: Optional[datetime] = None
    near_room: Optional[str] = None
    distance: str = "300m"
    room: Optional[str] = None
    track: Optional[str] = None


class SearchMessagesRequest(BaseModel):
    query: str
    group_id: Optional[str] = None
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    size: int = Field(default=10, ge=1, le=50)


class AskRequest(BaseModel):
    question: str
    group_id: Optional[str] = None
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    start_after: Optional[datetime] = None
    start_before: Optional[datetime] = None
    near_room: Optional[str] = None
    room: Optional[str] = None


class ChatPostRequest(BaseModel):
    sender: str = "You"
    body: str
    group_id: Optional[str] = None
    # When false (or sender is Alex/Sam/Jordan), message is stored without CrewBot.
    invoke_bot: Optional[bool] = None


class DemoAdvanceRequest(BaseModel):
    beat_index: int = Field(default=0, ge=0, le=20)


class PlanRequest(BaseModel):
    request: str = "plan our afternoon — try to stick together"
    group_id: Optional[str] = None


class ImportRequest(BaseModel):
    group_id: Optional[str] = None
    path: str
    kind: Literal["json", "csv", "chat_db"] = "json"
    limit: int = 200
    redacted: bool = True


class CompareRequest(BaseModel):
    query: str
    size: int = Field(default=5, ge=1, le=15)
    start_after: Optional[datetime] = None
    start_before: Optional[datetime] = None
    near_room: Optional[str] = None
    room: Optional[str] = None
    track: Optional[str] = None


class FeedbackRequest(BaseModel):
    query_id: str
    rating: Literal["up", "down"]
    group_id: Optional[str] = None
    talk_id: Optional[str] = None
    note: Optional[str] = None


def _es_or_503():
    try:
        return get_es()
    except ElasticsearchNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "group_id": settings.default_group_id,
        },
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    status: dict[str, Any] = {
        "ok": True,
        "elasticsearch_configured": bool(settings.elasticsearch_url and settings.elasticsearch_api_key),
        "openai_configured": bool(settings.openai_api_key),
        "linear_configured": bool(settings.linear_api_key),
        "linear_team_key": settings.linear_team_key or None,
    }
    if status["elasticsearch_configured"]:
        try:
            es = get_es()
            status["elasticsearch_ping"] = bool(es.ping())
        except Exception as exc:  # noqa: BLE001
            status["elasticsearch_ping"] = False
            status["elasticsearch_error"] = str(exc)
    return status


@app.post("/api/setup/indices")
def setup_indices() -> dict[str, Any]:
    es = _es_or_503()
    created = ensure_indices(es)
    return {"created": created}


@app.post("/api/search/talks")
def api_search_talks(body: SearchTalksRequest) -> dict[str, Any]:
    es = _es_or_503()
    near_lat, near_lon = resolve_near_room(body.near_room)
    results = search_talks(
        es,
        body.query,
        mode=body.mode,
        size=body.size,
        start_after=body.start_after,
        start_before=body.start_before,
        near_lat=near_lat,
        near_lon=near_lon,
        distance=body.distance,
        room=body.room,
        track=body.track,
    )
    return {"query": body.query, "mode": body.mode, "results": results}


@app.post("/api/search/messages")
def api_search_messages(body: SearchMessagesRequest) -> dict[str, Any]:
    es = _es_or_503()
    settings = get_settings()
    group_id = body.group_id or settings.default_group_id
    results = search_messages(es, body.query, group_id=group_id, mode=body.mode, size=body.size)
    return {"query": body.query, "group_id": group_id, "mode": body.mode, "results": results}


@app.post("/api/ask")
def api_ask(body: AskRequest) -> dict[str, Any]:
    es = _es_or_503()
    settings = get_settings()
    group_id = body.group_id or settings.default_group_id
    near_lat, near_lon = resolve_near_room(body.near_room)
    return ask(
        es,
        body.question,
        group_id=group_id,
        mode=body.mode,
        start_after=body.start_after,
        start_before=body.start_before,
        near_lat=near_lat,
        near_lon=near_lon,
        room=body.room,
    )


@app.post("/api/plan")
def api_plan(body: PlanRequest) -> dict[str, Any]:
    es = _es_or_503()
    settings = get_settings()
    group_id = body.group_id or settings.default_group_id
    return build_crew_plan(es, body.request, group_id=group_id)


@app.post("/api/search/compare")
def api_compare(body: CompareRequest) -> dict[str, Any]:
    es = _es_or_503()
    near_lat, near_lon = resolve_near_room(body.near_room)
    return compare_talk_search(
        es,
        body.query,
        size=body.size,
        start_after=body.start_after,
        start_before=body.start_before,
        near_lat=near_lat,
        near_lon=near_lon,
        room=body.room,
        track=body.track,
    )


@app.post("/api/feedback")
def api_feedback(body: FeedbackRequest) -> dict[str, Any]:
    es = _es_or_503()
    settings = get_settings()
    group_id = body.group_id or settings.default_group_id
    feedback_id = save_feedback(
        es,
        query_id=body.query_id,
        group_id=group_id,
        rating=body.rating,
        talk_id=body.talk_id,
        note=body.note,
    )
    return {"feedback_id": feedback_id, "ok": True}


@app.get("/api/groups/{group_id}/messages")
def api_list_messages(group_id: str, size: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    es = _es_or_503()
    return {"group_id": group_id, "messages": list_messages(es, group_id, size=size)}


def _should_invoke_bot(body: ChatPostRequest) -> bool:
    if body.invoke_bot is False:
        return False
    if body.invoke_bot is True:
        return True
    # Default: crew chatter is silent; only "You" (and unknown) wake CrewBot.
    return body.sender.strip() not in CREW_SENDERS


@app.get("/api/demo/script")
def api_demo_script() -> dict[str, Any]:
    script = load_demo_script()
    beats = []
    for i, beat in enumerate(script.get("beats") or []):
        beats.append(
            {
                "index": i,
                "id": beat.get("id"),
                "title": beat.get("title"),
                "cue": beat.get("cue"),
                "suggest": beat.get("suggest"),
                "message_count": len(beat.get("messages") or []),
            }
        )
    return {"name": script.get("name"), "total_beats": len(beats), "beats": beats}


@app.post("/api/groups/{group_id}/demo/advance")
def api_demo_advance(group_id: str, body: DemoAdvanceRequest) -> dict[str, Any]:
    es = _es_or_503()
    return advance_demo_beat(es, group_id=group_id, beat_index=body.beat_index)


@app.post("/api/groups/{group_id}/messages")
def api_post_message(group_id: str, body: ChatPostRequest) -> dict[str, Any]:
    es = _es_or_503()
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="body is required")
    text = body.body.strip()
    if not _should_invoke_bot(body):
        doc = index_message(es, group_id=group_id, sender=body.sender, body=text, role="user")
        return {
            "user_message": doc,
            "bot_message": None,
            "meta": {"invoke_bot": False},
            "thread": list_messages(es, group_id),
        }
    return post_user_message(es, group_id=group_id, sender=body.sender, body=text)


@app.post("/api/groups/{group_id}/messages/stream")
def api_post_message_stream(group_id: str, body: ChatPostRequest) -> StreamingResponse:
    es = _es_or_503()
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="body is required")
    text = body.body.strip()

    if not _should_invoke_bot(body):
        def chat_only():
            doc = index_message(es, group_id=group_id, sender=body.sender, body=text, role="user")
            payload_status = json.dumps({"phase": "crew_chat", "user_message": doc}, default=str)
            payload_done = json.dumps(
                {
                    "bot_message": None,
                    "meta": {"invoke_bot": False},
                    "thread": list_messages(es, group_id),
                },
                default=str,
            )
            yield f"event: status\ndata: {payload_status}\n\n"
            yield f"event: done\ndata: {payload_done}\n\n"

        return StreamingResponse(
            chat_only(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    def event_source():
        yield from stream_user_message(
            es,
            group_id=group_id,
            sender=body.sender,
            body=text,
        )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    es = _es_or_503()
    return conference_stats(es)


@app.post("/api/import/imessage")
def api_import_imessage(body: ImportRequest) -> dict[str, Any]:
    """Optional Mac path. Do not import real personal chats into a public demo."""
    es = _es_or_503()
    settings = get_settings()
    group_id = body.group_id or settings.default_group_id
    path = Path(body.path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"path not found: {path}")

    if body.kind == "json":
        rows = parse_export_json(path)
    elif body.kind == "csv":
        rows = parse_export_csv(path)
    else:
        rows = read_messages_db(path, limit=body.limit, redacted=body.redacted)

    count = import_rows(es, rows, group_id=group_id)
    return {
        "imported": count,
        "group_id": group_id,
        "warning": "Keep private messages out of public repos and demos.",
    }
