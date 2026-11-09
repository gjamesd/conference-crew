# Conference Crew

**Find the right talk — together.**

Group-chat conference assistant for the WeMakeDevs Cloud Native Hackathon **Track 4: Find the Right Talk** (Elasticsearch). Elasticsearch is the core data layer: talks, crew chat, and itineraries live there. Embeddings use **Jina** via Elastic Cloud `semantic_text`. The app does keyword, semantic, and hybrid search, plus time/geo filters, aggregations, RAG answers, and a shared afternoon planner.

Built with AI coding assistance (Cursor); architecture and behavior reviewed by the participant.

## Why this project

Most talk finders are a solo search box. **Conference Crew** is an iMessage-style **crew chat** that:

1. **Finds talks by meaning** (hybrid keyword + Jina semantic search)
2. **Recalls what the group said** (semantic search over chat in Elasticsearch)
3. **Plans a shared itinerary** when interests conflict (stick together vs split)

### Hero demo

1. Open the chat (seeded with Alex / Sam / Jordan interests)
2. Send: `plan our afternoon — try to stick together`
3. Follow up: `what did we say about dinner?`
4. Try: `something on eBPF and security after lunch`

## Rubric mapping

### Official judging ([cnh](https://www.wemakedevs.org/hackathons/cnh))

| Criterion | How we hit it |
|---|---|
| Elasticsearch is the core data layer | Talks, messages, itineraries indexed in ES |
| Smart keyword / semantic / hybrid search | `/api/search/*`, RRF hybrid, `semantic_text` |
| Makes conferences easier | Crew picks talks together; shared itinerary |

### Features to use ([resources](https://www.wemakedevs.org/hackathons/cnh/resources))

| Feature | Where |
|---|---|
| Store data in Elasticsearch | `talks`, `messages`, `itineraries` indices |
| Embeddings with Jina | `semantic_text` on abstracts + chat bodies |
| Keyword / vector / time / geo | search modes + `start` range + room `geo_point` |
| Aggregations and dashboards | `/api/stats` + `/dashboard` |
| Agent Builder / MCP | `mcp_server/server.py` tools |
| Elastic Agent Skills | Use while building: [elastic/agent-skills](https://github.com/elastic/agent-skills) |

## OpenAI $50 credits (hackathon)

There is **no self-serve redeem link** on the CNH site. Credits are part of in-person swag (“everyone who attends”). Ask organizers / check-in / Discord for the coupon or invite code, then add it in [OpenAI billing](https://platform.openai.com/settings/organization/billing) and put the resulting `OPENAI_API_KEY` in `.env`.

Until then the app still works: RAG falls back to template answers from Elasticsearch hits. Use `scripts/simulate_and_save.py` so conversations are saved and replayed without re-burning tokens.

## Quick start

### 1. Elastic Cloud Serverless trial

1. Prefer the hackathon link: [ela.st/hack-kubecon](https://ela.st/hack-kubecon) → **Elasticsearch** project type  
2. Create an API key  
3. Copy **endpoint URL** + key into `.env`:

```bash
cp .env.example .env
# edit .env — ELASTICSEARCH_URL, ELASTICSEARCH_API_KEY
# optional: OPENAI_API_KEY for nicer RAG prose (retrieval still uses ES)
```

### Save / replay conversations (don’t burn credits)

```bash
# zero-cost hero script (already checked in after first run)
python scripts/simulate_and_save.py --mode fixture

# after ES is wired + server is up: replay through the live API once, save bot answers
python scripts/simulate_and_save.py --mode replay

# later: re-ingest user lines only (no bot / no OpenAI)
python scripts/simulate_and_save.py --mode ingest
```

Optional one-shot OpenAI script generation (only when you have credits): `--mode generate`

### 2. Install & ingest

```bash
cd hackathon
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest.py
```

Ingest creates mappings with `semantic_text` (Jina via Elastic Inference Service on Cloud) and bulk-loads sample talks + seed chat.

### 3. Run the app

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Chat UI: http://localhost:8000  
- Aggregations dashboard: http://localhost:8000/dashboard  
- OpenAPI: http://localhost:8000/docs  

### 4. MCP (optional, Python 3.10+)

```bash
python3.11 -m venv .venv310 && source .venv310/bin/activate  # if system Python is 3.9
pip install -r requirements.txt
python mcp_server/server.py
```

Tools: `search_talks_tool`, `search_crew_chat`, `ask_schedule`, `plan_crew_afternoon`, `schedule_stats`.

## API overview

| Endpoint | Purpose |
|---|---|
| `POST /api/search/talks` | keyword \| semantic \| hybrid (+ time/geo) |
| `POST /api/search/compare` | side-by-side keyword vs semantic vs hybrid + narrative |
| `POST /api/search/messages` | search crew chat |
| `POST /api/ask` | RAG over talks + chat + explanation |
| `POST /api/plan` | conflict-aware shared itinerary |
| `POST /api/groups/{id}/messages` | chat turn (user + CrewBot) |
| `POST /api/feedback` | thumbs up/down on a query_id |
| `GET /api/stats` | track / hour / room + query analytics |
| `POST /api/import/imessage` | optional Mac/export import |

### Prove search quality

```bash
python scripts/eval_search.py
```

Compares mean precision@5 for keyword vs semantic vs hybrid on `data/golden_queries.json`.

## Optional Mac iMessage path

For personal use only — **do not** put real private messages in the public repo or judged demo.

- JSON/CSV export: `POST /api/import/imessage` with `kind=json|csv`  
- Local `chat.db` (needs Full Disk Access): `kind=chat_db`, `redacted=true`  
- Sample synthetic export: `data/sample_imessage_export.json`

## Project layout

```
app/           FastAPI app, ES client, search, RAG, planner, chat
data/          Sample talks + seed crew messages
scripts/       ingest.py
mcp_server/    MCP tool server
templates/     Chat + dashboard UI
static/        CSS/JS
```

## Gitar / Sonar

Review conventions live in [`.gitar/review/`](.gitar/review/). Install the [Gitar GitHub App](https://app.gitar.ai) on this repository and land features through pull requests so Gitar reviews each change.

## AI disclosure

This project was built with Cursor (AI coding assistant). The participant directed product decisions, verified Elasticsearch behavior, and can explain the architecture (indices, hybrid retrieval, planner, RAG grounding).

## License

MIT — hackathon submission by the participant/team.
