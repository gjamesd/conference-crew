# Security and secrets

- Reject any PR that adds credentials, API keys, tokens, passwords, or private personal data.
- `.env` must never be committed. Only `.env.example` with placeholders is allowed.
- Do not allow real iMessage / Messages.db contents, phone numbers, or personal chat exports in the repo or sample data.
- Elasticsearch and OpenAI credentials must remain environment variables only.
