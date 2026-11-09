# Demo script (5 minutes)

## Gitar (Sonar) — required for sponsor tooling

1. Install the Gitar GitHub App on this repo: https://app.gitar.ai  
2. Confirm reviews run on PRs (project rules live in `.gitar/review/`).  
3. Ship features through PRs so Gitar is part of how you build, not a last-minute checkbox.

## Setup before judges walk up

1. Elastic Serverless project running; `.env` filled (`OPENAI_API_KEY` + `gpt-4o-mini`)
2. `python scripts/ingest.py` completed (includes `llm_cache` index)
3. `uvicorn app.main:app --host 0.0.0.0 --port 8000`
4. Browser on http://localhost:8000 — **paced demo**: Alex/Sam/Jordan talk → you ask CrewBot → Continue crew chat
5. Ask the same question twice to show **cache hit** (no second spend)

## Paced meeting flow (Glasses AI standup → Linear sprint sync)

1. One-time: set `LINEAR_API_KEY` in `.env`, run `python scripts/setup_linear.py` (creates `JAM-5…JAM-11`)
2. Status chat cites Linear issues + real GitHub issues (`#3`–`#8`)
3. **Continue the conversation** for longer detail (or ask anytime)
4. Ask: `summarize this meeting — what's most important?` → pushes to **JAM-11** + comments workstreams
5. Ask: `create a sprint planning document and JSON from this meeting` → download + **Open in Linear**
6. Continue again → ask CrewBot to refresh the plan (pushes again)
7. **Restart meeting** starts a fresh live group id

## Talk track

1. **Problem:** Conferences are group decisions; Messages search is terrible; solo talk search ignores your crew.
2. **Product:** Conference Crew — group chat with Elasticsearch as the brain.
3. **Show seed chat** — Alex (eBPF), Sam (ethics), Jordan (platform), dinner plan.
4. **Send:** `compare: something on eBPF and security`  
   Show explain panel: keyword vs semantic (Jina) vs hybrid RRF — semantic/hybrid rescue talks keyword misses.
5. **Send:** `plan our afternoon — try to stick together`  
   Overlap scoring + conflict skipping + itinerary written to ES; explain panel shows interest map.
6. **Send:** `plan our afternoon — split up`  
   Per-person picks + reconvene room.
7. **Send:** `what did we say about dinner?`  
   Semantic search over **chat** index.
8. **Thumb up/down** on explain panel — feedback index in ES.
9. **Open Dashboard** — track/hour aggs + recent queries by mode.
10. **Optional terminal:** `python scripts/eval_search.py` — mean P@5 keyword vs hybrid.
11. **Close:** ES is the core data layer across talks, chat, itineraries, query logs, feedback.

## Privacy note if asked about iMessage

Optional Mac import exists for personal use. Public demo uses synthetic conference DMs only.
