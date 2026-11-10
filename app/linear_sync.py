from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings
from app.linear_client import (
    LinearError,
    comment_on_issue,
    linear_configured,
    update_issue,
)

ROOT = Path(__file__).resolve().parents[1]
ISSUES_PATH = ROOT / "data" / "linear_issues.json"
SYNC_TITLE = "CrewBot Sprint Sync"


def load_linear_issues() -> dict[str, Any]:
    if not ISSUES_PATH.exists():
        return {"issues": []}
    return json.loads(ISSUES_PATH.read_text())


def workstream_issues() -> list[dict[str, Any]]:
    return [i for i in (load_linear_issues().get("issues") or []) if i.get("role") == "workstream"]


def sprint_sync_issue() -> Optional[dict[str, Any]]:
    for i in load_linear_issues().get("issues") or []:
        if i.get("role") == "sprint_sync" or i.get("title") == SYNC_TITLE:
            return i
    return None


def format_issue_catalog() -> str:
    lines = []
    for i in workstream_issues():
        lines.append(
            f"- {i.get('identifier')} | owner={i.get('owner')} | {i.get('title')} | "
            f"{i.get('url')} | github={i.get('github_url') or 'n/a'}"
        )
    sync = sprint_sync_issue()
    if sync:
        lines.append(
            f"- {sync.get('identifier')} | owner=CrewBot | {sync.get('title')} | {sync.get('url')}"
        )
    return "\n".join(lines) if lines else "(no Linear issues loaded — run scripts/setup_linear.py)"


def template_linear_link_answer() -> str:
    parts = [
        "Here’s what to plan for the sprint planning session, with the associated Linear issues:",
        "",
    ]
    for i, issue in enumerate(workstream_issues(), start=1):
        parts.append(
            f"{i}. **{issue.get('title')}** (owner: {issue.get('owner')}) — "
            f"**{issue.get('identifier')}** — {issue.get('url')}"
            + (f" · GitHub: {issue.get('github_url')}" if issue.get("github_url") else "")
        )
    sync = sprint_sync_issue()
    if sync:
        parts.extend(
            [
                "",
                f"Rolling sync issue: **{sync.get('identifier')}** — {sync.get('url')}",
            ]
        )
    return "\n".join(parts)


def push_sprint_sync_to_linear(
    *,
    intent: str,
    question: str,
    markdown: str,
    artifact: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Upsert Sprint Sync description + comment on workstream issues. Non-raising wrapper returns meta."""
    if not linear_configured():
        return {"ok": False, "error": "LINEAR_API_KEY not configured", "pushed_at": None}

    sync = sprint_sync_issue()
    if not sync or not sync.get("id"):
        return {
            "ok": False,
            "error": "Missing CrewBot Sprint Sync in data/linear_issues.json — run scripts/setup_linear.py",
            "pushed_at": None,
        }

    settings = get_settings()
    now = datetime.utcnow().isoformat() + "Z"
    json_block = ""
    if artifact:
        slim = {k: v for k, v in artifact.items() if k != "evidence_messages"}
        json_block = (
            "\n\n## Sprint planning JSON\n```json\n"
            + json.dumps(slim, indent=2)[:6000]
            + "\n```\n"
        )

    description = (
        f"# CrewBot Sprint Sync\n\n"
        f"_Last push: {now}_\n\n"
        f"**Intent:** `{intent}`  \n"
        f"**Prompt:** {question}\n\n"
        f"**Team:** {settings.linear_team_key or 'Linear'}\n\n"
        f"## Summary / plan\n\n{markdown}\n"
        f"{json_block}"
        f"\n## Workstreams\n"
        + "\n".join(
            f"- **{i.get('identifier')}** ({i.get('owner')}): {i.get('title')} — {i.get('url')}"
            + (f" · [GitHub]({i.get('github_url')})" if i.get("github_url") else "")
            for i in workstream_issues()
        )
    )

    try:
        updated = update_issue(issue_id=sync["id"], description=description)
        commented: list[dict[str, str]] = []
        blurb = (
            f"CrewBot {intent.replace('_', ' ')} from Conference Crew chat.\n\n"
            f"{markdown[:500]}{'…' if len(markdown) > 500 else ''}\n\n"
            f"Full sync: {updated.get('url') or sync.get('url')}"
        )
        for issue in workstream_issues():
            if not issue.get("id"):
                continue
            comment_on_issue(issue_id=issue["id"], body=blurb)
            commented.append(
                {
                    "identifier": issue.get("identifier") or "",
                    "url": issue.get("url") or "",
                    "owner": issue.get("owner") or "",
                }
            )
        return {
            "ok": True,
            "pushed_at": now,
            "intent": intent,
            "sync_issue_id": updated.get("id") or sync.get("id"),
            "sync_issue_identifier": updated.get("identifier") or sync.get("identifier"),
            "sync_issue_url": updated.get("url") or sync.get("url"),
            "commented_issue_urls": [c["url"] for c in commented if c.get("url")],
            "commented_issues": commented,
            "error": None,
        }
    except LinearError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "pushed_at": now,
            "sync_issue_url": sync.get("url"),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"unexpected Linear push failure: {exc}",
            "pushed_at": now,
            "sync_issue_url": sync.get("url"),
        }
