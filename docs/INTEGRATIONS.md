# Integrations
Apollo: `APOLLO_API_KEY` (live when set, demo when empty). Search = `POST /api/v1/mixed_people/api_search` (no emails/phones, last names may be masked); Enrichment = `POST /api/v1/people/match` (uses credits; phones arrive via Apollo webhook, not requested). Auth header `x-api-key`; some endpoints need a master API key. See `_apollo()` in services.py (timeouts, Retry-After). Check your plan's rate limits and credit cost.
LinkedIn / Sales Navigator: official exports or approved CRM integrations only. No scraping.
Claude / OpenAI: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, `AI_PROVIDER`. Implement `llm_explain()`.
All secrets stay in the server `.env`.

Signals (Today page): `SIGNAL_PROVIDER=claude_web` + `ANTHROPIC_API_KEY` (+ optional `ANTHROPIC_MODEL`). Verify the model name and web_search tool version in current Anthropic docs before relying on it. Any other search API: implement `_generic_web()` in `agents.py`, return `{type, summary, url, date, source, demo:false, verified:false}`.
Scheduler: `POST /api/v1/cron/run` with `X-Cron-Secret`. Example: `gcloud scheduler jobs create http crm-agents --schedule="0 8,13,18 * * *" --uri=https://YOUR_HOST/api/v1/cron/run --http-method=POST --headers=X-Cron-Secret=YOUR_SECRET`
