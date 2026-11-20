# Integrations
All secrets live in the server `.env` (or your host's environment). Nothing is ever sent to the browser. Any integration without its key shows **"pending configuration"** and never returns fake data. The Integrations page has a real **Test connection** call for each one.

## Apollo (`APOLLO_API_KEY`)
Header `x-api-key`, base `https://api.apollo.io/api/v1` (see `_apollo()` in `services.py`: timeouts, bounded retries, `Retry-After`, clear errors for 401/403/422/429/5xx).
- **Search**: `POST /mixed_people/api_search`. Filters: title, company or domain (comma separated), location, seniority, keywords. Paged (25 per page here; Apollo allows up to 100 per page and 500 pages). Uses no credits, returns no emails or phones, and masks last names until enrichment. Duplicates against existing leads are flagged before import.
- **Enrichment** ("Enrich all", selected leads, per lead): `POST /people/bulk_match`, up to 10 people per call, run in the background in bounded jobs (max 500 leads per job). Credits are charged by Apollo per matched person (its docs: 1 to 9; phones are not requested here). Only fills missing fields; verified and existing values are never overwritten; a lead is marked enriched only on a usable match (`match_confidence` of `none` or `low` counts as no match). Job status, per-lead results and credits used are stored in `enrichmentJobs`.
- **Sequences**: `POST /emailer_campaigns/search` and `POST /contacts/search` (Leads page > Sync from Apollo).
- **Call recordings and transcripts** (lead drawer > Calls tab, and Sync calls on selected leads): `POST /conversations/search` with the lead's Apollo **contact id** (free, summaries only) then `GET /conversations/{id}` for the transcript, recording links and summary (Apollo: 1 credit per call that has AI insights, so the person confirms with real numbers first; max 25 per run). Only what Apollo returns is stored, one document per conversation id (re-syncing never duplicates). Recordings are links into Apollo, not copies. No call activity is created and nothing changes in Apollo. Leads without an Apollo contact id (not from a synced list or sequence) cannot be looked up.
- **Open in Apollo**: the button on My day opens the lead in Apollo in a new tab and never dials. Page addresses are not in Apollo's docs: defaults are `https://app.apollo.io/#/contacts/{id}` (else `/#/people/{id}`), overridable with `APOLLO_CONTACT_URL` / `APOLLO_PERSON_URL`.
- **Lists** (call lists): `GET /labels` (0 credits) shows your Apollo people lists; `POST /contacts/search` with `contact_label_ids` (JSON body) returns a list's contacts. Assign leads page > *Show my Apollo lists* > tick > *Sync*: one **Calls** campaign per list (`source: apollo_list`, `apollo_label_id`), contacts as unassigned leads on the Calls channel with phone, title, company and LinkedIn. Idempotent. Lists you synced before are refreshed by *Sync from Apollo*. Apollo's list count is cached and can be stale. How CSV uploads in the Apollo UI map to lists is not documented, so you choose which lists to sync instead of the CRM guessing.
- **Emails**: `GET /emailer_messages/search` (0 credits, query-string filters, 100 per page). Nothing about emails is logged by hand: Sales Activity > Email shows a read-only history and a **Sync emails from Apollo** button; Sync from Apollo runs it too. For each email in your synced sequences, matched to a lead by recipient address (then Apollo contact id): a **Sent** activity at the real send time (`completed_at`) with the subject and a body excerpt; **Bounced** and **Replied** (or the mapped outcome: `willing_to_meet` = Meeting Requested, `not_interested` = Not Interested, `out_of_office` = No Response) when Apollo reports them. Apollo gives no time for replies, bounces, opens or clicks and no reply text, so those are timed when the CRM first detects them and say so. Opens and clicks are flags on the Sent activity, not activities. Unsubscribes are noted, never auto-suppressed. Emails never create tasks or move stages. Idempotent: activity ids come from Apollo's message id. Apollo documents a 600 calls/hour cap on this endpoint.
- **Rate limits** depend on your Apollo plan and are per team and per endpoint (minute, hour, day). Nothing is hardcoded: a 429 is retried only for short waits, otherwise the UI tells you how long to wait. Check `view-api-usage-stats` in Apollo for your real limits.
- Which key type each endpoint needs depends on your plan. A 401/403 is reported as "Apollo rejected the API key".

## AI assistant (`AI_PROVIDER=gemini|groq|none`)
`backend/ai.py`. Backend-only calls with timeouts and bounded retries. Keys travel in headers, never in URLs, and keys, prompts and answers are never logged.
- **Gemini**: `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-3.5-flash-lite`). REST `generateContent` on `v1beta`.
- **Groq**: `GROQ_API_KEY`, `GROQ_MODEL` (default `llama-3.3-70b-versatile`). OpenAI-compatible `/openai/v1/chat/completions`.
- Model names change: set `GEMINI_MODEL` / `GROQ_MODEL` instead of editing code.
- The LLM only **phrases facts the backend already calculated** (`llm_explain`). KPIs stay deterministic.
- **Lead questions** (summarise, objections and next steps, draft a follow-up, summarise a call): only that one lead's CRM facts and stored call transcripts are sent (no email or phone, hard-capped at `MAX_FACTS_CHARS`), after the same record-scoping check as everywhere else. Read-only: nothing is written; drafts are never sent. Questions are audit-logged.
- No key set: rule-based answers and "AI provider pending configuration" in the assistant.

## Firebase
Auth (ID tokens verified server-side) and Firestore. Credentials: `FIREBASE_SERVICE_ACCOUNT_JSON` (whole key as a value) or `GOOGLE_APPLICATION_CREDENTIALS` (file path). See `.env.example` and `docs/FIRESTORE_SCHEMA.md`.

## LinkedIn / Sales Navigator
Official exports or approved CRM integrations only. Upload the export in Lead Discovery. No scraping.

## Scheduler
`POST /api/v1/cron/run` with `X-Cron-Secret`. Example: `gcloud scheduler jobs create http crm-agents --schedule="0 8,13,18 * * *" --uri=https://YOUR_HOST/api/v1/cron/run --http-method=POST --headers=X-Cron-Secret=YOUR_SECRET`

## Signals
There is no web or AI signal lookup. A lead's **signal** (and optional `signal_url`) is a normal field: type it, upload it as a "Signal" column in CSV/XLSX, or pass it in an import. The Today page ranks leads with a signal first and uses it as the opener in call briefs and LinkedIn/email drafts.

## Tests
`pip install -r requirements-dev.txt` then `python -m pytest tests -q` from `backend/`. Tests run on in-memory data with empty provider keys and mock every external call.
