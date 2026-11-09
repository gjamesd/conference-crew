from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import get_settings

LINEAR_GRAPHQL = "https://api.linear.app/graphql"


class LinearError(RuntimeError):
    pass


def linear_configured() -> bool:
    return bool(get_settings().linear_api_key.strip())


def _headers() -> dict[str, str]:
    settings = get_settings()
    key = settings.linear_api_key.strip()
    if not key:
        raise LinearError("LINEAR_API_KEY is not set")
    return {
        "Authorization": key,
        "Content-Type": "application/json",
    }


def graphql(query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    payload = {"query": query, "variables": variables or {}}
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(LINEAR_GRAPHQL, headers=_headers(), json=payload)
        resp.raise_for_status()
        data = resp.json()
    if data.get("errors"):
        raise LinearError(str(data["errors"]))
    return data.get("data") or {}


def list_teams() -> list[dict[str, Any]]:
    data = graphql(
        """
        query Teams {
          teams {
            nodes { id key name }
          }
        }
        """
    )
    return (data.get("teams") or {}).get("nodes") or []


def resolve_team() -> dict[str, Any]:
    settings = get_settings()
    teams = list_teams()
    if not teams:
        raise LinearError("No Linear teams visible for this API key")
    if settings.linear_team_id:
        for t in teams:
            if t.get("id") == settings.linear_team_id:
                return t
    if settings.linear_team_key:
        key = settings.linear_team_key.upper()
        for t in teams:
            if (t.get("key") or "").upper() == key:
                return t
    return teams[0]


def find_issue_by_title(team_id: str, title: str) -> Optional[dict[str, Any]]:
    data = graphql(
        """
        query Issues($filter: IssueFilter) {
          issues(filter: $filter, first: 25) {
            nodes { id identifier title url description }
          }
        }
        """,
        {
            "filter": {
                "team": {"id": {"eq": team_id}},
                "title": {"eq": title},
            }
        },
    )
    nodes = (data.get("issues") or {}).get("nodes") or []
    return nodes[0] if nodes else None


def create_issue(
    *,
    team_id: str,
    title: str,
    description: str = "",
) -> dict[str, Any]:
    data = graphql(
        """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier title url description }
          }
        }
        """,
        {
            "input": {
                "teamId": team_id,
                "title": title,
                "description": description,
            }
        },
    )
    payload = data.get("issueCreate") or {}
    if not payload.get("success") or not payload.get("issue"):
        raise LinearError(f"issueCreate failed: {payload}")
    return payload["issue"]


def update_issue(*, issue_id: str, description: str) -> dict[str, Any]:
    data = graphql(
        """
        mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue { id identifier title url description }
          }
        }
        """,
        {"id": issue_id, "input": {"description": description}},
    )
    payload = data.get("issueUpdate") or {}
    if not payload.get("success") or not payload.get("issue"):
        raise LinearError(f"issueUpdate failed: {payload}")
    return payload["issue"]


def comment_on_issue(*, issue_id: str, body: str) -> dict[str, Any]:
    data = graphql(
        """
        mutation CommentCreate($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment { id url body }
          }
        }
        """,
        {"input": {"issueId": issue_id, "body": body}},
    )
    payload = data.get("commentCreate") or {}
    if not payload.get("success"):
        raise LinearError(f"commentCreate failed: {payload}")
    return payload.get("comment") or {}


def upsert_issue_by_title(
    *,
    team_id: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    existing = find_issue_by_title(team_id, title)
    if existing:
        return update_issue(issue_id=existing["id"], description=description)
    return create_issue(team_id=team_id, title=title, description=description)
