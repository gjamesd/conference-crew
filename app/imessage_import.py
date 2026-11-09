from __future__ import annotations

import csv
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from elasticsearch import Elasticsearch

from app.chat import index_message

# Apple Absolute Time epoch (2001-01-01) used by Messages.db
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _apple_to_iso(ts: int | float | None) -> str:
    if ts is None:
        return datetime.utcnow().isoformat()
    # nanoseconds vs seconds heuristics
    value = float(ts)
    if value > 1e12:
        value = value / 1_000_000_000
    elif value > 1e10:
        value = value / 1_000
    dt = APPLE_EPOCH.timestamp() + value
    return datetime.utcfromtimestamp(dt).isoformat()


def parse_export_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "messages" in data:
        data = data["messages"]
    rows = []
    for item in data:
        rows.append(
            {
                "sender": item.get("sender") or item.get("handle") or "unknown",
                "body": item.get("body") or item.get("text") or "",
                "timestamp": item.get("timestamp") or datetime.utcnow().isoformat(),
            }
        )
    return [r for r in rows if r["body"].strip()]


def parse_export_csv(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for item in reader:
            body = (item.get("body") or item.get("text") or "").strip()
            if not body:
                continue
            rows.append(
                {
                    "sender": item.get("sender") or item.get("handle") or "unknown",
                    "body": body,
                    "timestamp": item.get("timestamp") or datetime.utcnow().isoformat(),
                }
            )
    return rows


def read_messages_db(
    db_path: Path,
    *,
    limit: int = 200,
    redacted: bool = True,
) -> list[dict[str, Any]]:
    """Optional local Messages.db reader. Requires Full Disk Access on macOS.

    Never commit real chat.db contents. Prefer redacted=True for demos.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              message.ROWID,
              message.text,
              message.date,
              handle.id as handle
            FROM message
            LEFT JOIN handle ON message.handle_id = handle.ROWID
            WHERE message.text IS NOT NULL AND message.text != ''
            ORDER BY message.date DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = []
        for _rowid, text, date, handle in cur.fetchall():
            body = text or ""
            if redacted and len(body) > 80:
                body = body[:77] + "..."
            sender = "redacted" if redacted else (handle or "unknown")
            rows.append(
                {
                    "sender": sender,
                    "body": body,
                    "timestamp": _apple_to_iso(date),
                }
            )
        return list(reversed(rows))
    finally:
        conn.close()


def import_rows(
    es: Elasticsearch,
    rows: Iterable[dict[str, Any]],
    *,
    group_id: str,
    source: str = "imessage_import",
) -> int:
    count = 0
    for row in rows:
        body = (row.get("body") or "").strip()
        if not body:
            continue
        ts_raw = row.get("timestamp")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "")) if ts_raw else datetime.utcnow()
        except ValueError:
            ts = datetime.utcnow()
        index_message(
            es,
            group_id=group_id,
            sender=str(row.get("sender") or "unknown"),
            body=body,
            role="user",
            source=source,
            message_id=f"imp-{uuid.uuid4().hex[:12]}",
            timestamp=ts,
        )
        count += 1
    return count
