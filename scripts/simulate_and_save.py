#!/usr/bin/env python3
"""Generate a demo conversation ONCE (optional OpenAI), save to disk, replay forever.

Usage:
  # Offline fixture (no OpenAI spend) — writes data/saved_conversations/demo_crew.json
  python scripts/simulate_and_save.py --mode fixture

  # One-shot OpenAI generation, then save (needs OPENAI_API_KEY)
  python scripts/simulate_and_save.py --mode generate

  # Replay a saved conversation through the live API (no LLM needed for users)
  python scripts/simulate_and_save.py --mode replay --file data/saved_conversations/demo_crew.json

  # Ingest saved user lines into Elasticsearch as chat history (no live bot calls)
  python scripts/simulate_and_save.py --mode ingest --file data/saved_conversations/demo_crew.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAVE_DIR = ROOT / "data" / "saved_conversations"
DEFAULT_FILE = SAVE_DIR / "demo_crew.json"


def fixture_conversation() -> dict:
    """Hand-authored hero demo — zero API cost, always reproducible."""
    base = datetime(2026, 11, 9, 8, 30, 0)
    turns = [
        {"sender": "Alex", "body": "I'm hunting eBPF + runtime security talks today."},
        {"sender": "Sam", "body": "Keep AI ethics / responsible RAG on the list — no hype-only sessions."},
        {"sender": "Jordan", "body": "Platform engineering + IDP golden paths for me."},
        {"sender": "Alex", "body": "Let's stick together for the afternoon if we can."},
        {"sender": "Sam", "body": "Dinner: ramen near Hall B around 7 after the keynote."},
        {"sender": "Jordan", "body": "Ramen works. Can CrewBot plan a shared afternoon block?"},
        {
            "sender": "You",
            "body": "plan our afternoon — try to stick together",
            "expect": "plan",
        },
        {
            "sender": "You",
            "body": "what did we say about dinner?",
            "expect": "recall",
        },
        {
            "sender": "You",
            "body": "something on eBPF and security after lunch",
            "expect": "search",
        },
    ]
    messages = []
    for i, t in enumerate(turns):
        messages.append(
            {
                "sender": t["sender"],
                "body": t["body"],
                "timestamp": (base + timedelta(minutes=i * 2)).isoformat(),
                "expect": t.get("expect"),
            }
        )
    return {
        "name": "demo_crew",
        "group_id": "crew-demo",
        "created_at": datetime.utcnow().isoformat(),
        "source": "fixture",
        "notes": "Saved conversation for replay without burning OpenAI credits.",
        "messages": messages,
        "recorded_replies": [],
    }


def generate_with_openai() -> dict:
    from app.config import get_settings

    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY missing. Use --mode fixture or add the key to .env")

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    prompt = (
        "Write a short conference group-chat script (8-10 messages) for Alex, Sam, and Jordan "
        "attending a cloud-native hackathon. Interests: eBPF/security, AI ethics/RAG, platform/IDP. "
        "Include a dinner plan near Hall B, and end with three user prompts that a bot should handle: "
        "1) plan our afternoon — try to stick together  2) what did we say about dinner?  "
        "3) something on eBPF and security after lunch. "
        "Return JSON: {\"messages\":[{\"sender\":\"\",\"body\":\"\"}]}"
    )
    resp = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )
    raw = resp.choices[0].message.content or "{}"
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    data = json.loads(raw)
    base = datetime(2026, 11, 9, 8, 30, 0)
    messages = []
    for i, m in enumerate(data.get("messages") or []):
        messages.append(
            {
                "sender": m.get("sender") or "You",
                "body": m.get("body") or "",
                "timestamp": (base + timedelta(minutes=i * 2)).isoformat(),
            }
        )
    return {
        "name": "demo_crew_generated",
        "group_id": "crew-demo",
        "created_at": datetime.utcnow().isoformat(),
        "source": "openai_once",
        "notes": "Generated once with OpenAI. Replay/ingest without calling OpenAI again.",
        "messages": messages,
        "recorded_replies": [],
    }


def save(conversation: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(conversation, indent=2))
    return path


def replay(path: Path, base_url: str, pause: float) -> dict:
    conv = json.loads(path.read_text())
    group_id = conv.get("group_id") or "crew-demo"
    recorded = []
    with httpx.Client(base_url=base_url, timeout=120.0) as client:
        for msg in conv.get("messages") or []:
            body = msg.get("body") or ""
            sender = msg.get("sender") or "You"
            print(f"→ {sender}: {body}")
            res = client.post(
                f"/api/groups/{group_id}/messages",
                json={"sender": sender, "body": body},
            )
            res.raise_for_status()
            data = res.json()
            bot = (data.get("bot_message") or {}).get("body") or ""
            print(f"← CrewBot: {bot[:240]}{'…' if len(bot) > 240 else ''}\n")
            recorded.append(
                {
                    "user": msg,
                    "bot_message": data.get("bot_message"),
                    "meta_keys": list((data.get("meta") or {}).keys()),
                }
            )
            time.sleep(pause)
    conv["recorded_replies"] = recorded
    conv["replayed_at"] = datetime.utcnow().isoformat()
    out = path.with_name(path.stem + "_replayed.json")
    save(conv, out)
    print(f"Saved full replay (including bot answers) to {out}")
    return conv


def ingest_only(path: Path) -> int:
    """Index user lines into ES without calling the bot (cheap seed)."""
    from app.chat import index_message
    from app.es_client import ensure_indices, get_es

    conv = json.loads(path.read_text())
    group_id = conv.get("group_id") or "crew-demo"
    es = get_es()
    ensure_indices(es)
    count = 0
    for msg in conv.get("messages") or []:
        body = (msg.get("body") or "").strip()
        if not body:
            continue
        ts = msg.get("timestamp")
        try:
            timestamp = datetime.fromisoformat(ts) if ts else datetime.utcnow()
        except ValueError:
            timestamp = datetime.utcnow()
        index_message(
            es,
            group_id=group_id,
            sender=msg.get("sender") or "unknown",
            body=body,
            role="user",
            source="saved_conversation",
            timestamp=timestamp,
        )
        count += 1
    print(f"Ingested {count} saved messages into Elasticsearch group={group_id}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fixture", "generate", "replay", "ingest"], required=True)
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--pause", type=float, default=0.4)
    args = parser.parse_args()

    if args.mode == "fixture":
        path = save(fixture_conversation(), args.file)
        print(f"Wrote zero-cost fixture to {path}")
    elif args.mode == "generate":
        path = save(generate_with_openai(), args.file)
        print(f"Generated once with OpenAI and saved to {path}")
    elif args.mode == "replay":
        if not args.file.exists():
            raise SystemExit(f"Missing {args.file}. Run --mode fixture first.")
        replay(args.file, args.base_url, args.pause)
    elif args.mode == "ingest":
        if not args.file.exists():
            raise SystemExit(f"Missing {args.file}. Run --mode fixture first.")
        ingest_only(args.file)


if __name__ == "__main__":
    main()
