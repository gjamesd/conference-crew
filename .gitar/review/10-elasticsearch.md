# Elasticsearch as the core data layer

- Talks, crew messages, itineraries, query logs, and feedback must continue to live in Elasticsearch — not a local SQLite/JSON store as source of truth.
- Prefer `semantic_text` (Jina via Elastic Cloud) for meaning search; keep keyword and hybrid (RRF) paths working.
- Time filters on talk `start`/`end` and geo filters on room `location` should remain available for schedule queries.
- Search/plan APIs should keep explainability payloads (mode, scores, filters) so demos can show how retrieval works.
