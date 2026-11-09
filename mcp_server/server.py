#!/usr/bin/env python3
"""MCP server exposing Conference Crew search / ask / plan tools.

Requires Python 3.10+ and the `mcp` package. On Python 3.9, use the FastAPI
OpenAPI tools at /docs as the agent surface instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "The official `mcp` package needs Python 3.10+.\n"
        "Install a newer Python, then: pip install 'mcp>=1.1.0'\n"
        "Meanwhile, agents can call the same capabilities via HTTP:\n"
        "  POST /api/search/talks\n"
        "  POST /api/search/messages\n"
        "  POST /api/ask\n"
        "  POST /api/plan\n"
        "  GET  /api/stats\n",
        file=sys.stderr,
    )
    raise SystemExit(1)

from app.config import get_settings
from app.es_client import get_es
from app.planner import build_crew_plan
from app.rag import ask
from app.search import search_messages, search_talks
from app.stats import conference_stats

mcp = FastMCP("conference-crew")


@mcp.tool()
def search_talks_tool(query: str, mode: str = "hybrid", size: int = 8) -> str:
    """Hybrid/keyword/semantic search over conference talks in Elasticsearch."""
    es = get_es()
    results = search_talks(es, query, mode=mode, size=size)  # type: ignore[arg-type]
    return json.dumps(results, indent=2)


@mcp.tool()
def search_crew_chat(query: str, group_id: str | None = None, mode: str = "hybrid") -> str:
    """Semantic/hybrid search over a crew's conference chat history."""
    es = get_es()
    gid = group_id or get_settings().default_group_id
    results = search_messages(es, query, group_id=gid, mode=mode)  # type: ignore[arg-type]
    return json.dumps(results, indent=2)


@mcp.tool()
def ask_schedule(question: str, group_id: str | None = None) -> str:
    """RAG over talks + crew chat with citations from Elasticsearch retrieval."""
    es = get_es()
    gid = group_id or get_settings().default_group_id
    return json.dumps(ask(es, question, group_id=gid), indent=2)


@mcp.tool()
def plan_crew_afternoon(
    request: str = "plan our afternoon — try to stick together",
    group_id: str | None = None,
) -> str:
    """Build a shared stick-together or split itinerary for the crew."""
    es = get_es()
    gid = group_id or get_settings().default_group_id
    return json.dumps(build_crew_plan(es, request, group_id=gid), indent=2)


@mcp.tool()
def schedule_stats() -> str:
    """Aggregations: talks by track, room, and hour."""
    es = get_es()
    return json.dumps(conference_stats(es), indent=2)


if __name__ == "__main__":
    mcp.run()
