# Demo script (5 minutes)

## Setup before judges walk up

1. Elastic Serverless project running; `.env` filled
2. `python scripts/ingest.py` completed
3. `uvicorn app.main:app --host 0.0.0.0 --port 8000`
4. Browser on http://localhost:8000 and http://localhost:8000/dashboard

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
