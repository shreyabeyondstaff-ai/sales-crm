# SalesDesk: Internal Sales Outreach CRM (MVP)

Frontend: HTML + CSS + vanilla JS. Backend: Python + FastAPI. Auth: Firebase (switchable). DB: Firestore (switchable).
Every screen works today on demo data. Real integrations are marked `TODO(INTEGRATION)` in the code.

## Run it (2 minutes)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn main:app --reload --port 8000
```
Open http://localhost:8000 (FastAPI serves the frontend too, so there is no CORS setup). API docs: http://localhost:8000/docs

## The auth true/false switch
| Mode | backend `.env` | `frontend/config.js` | What you get |
|---|---|---|---|
| **Test** (default) | `AUTH_DISABLED=true` | `AUTH_ENABLED:false` | No login. A dashed "Test as" dropdown in the top bar switches between 6 mock users (admin, 2 callers, outreach, DA, developer). Roles are still enforced by the backend. |
| **Real** | `AUTH_DISABLED=false` | `AUTH_ENABLED:true` + fill `FIREBASE` | Firebase email/password login, ID token sent as Bearer, verified with Firebase Admin SDK. |

## Roles
`admin` (manager/CEO) and `developer`: everything. `outreach`: discovery, import, enrichment, assign, leads. `caller`: own leads only, log activity, tasks. `da`: read-only reports, all leads, CSV export. Edit `PERMS` in `backend/services.py`.

## Your integration checklist (where to fill gaps)
1. **Firebase Auth**: put web config in `frontend/config.js`; put the service account JSON at `backend/serviceAccount.json`; set `AUTH_DISABLED=false`. Create a doc in Firestore `users/{firebaseUID}` with `name, email, role, active:true` (see `backend/main.py` `auth()` for the first-login TODO).
2. **Firestore**: set `USE_FIRESTORE=true`. `FirestoreDB` in `backend/firebase.py` works as is, but it streams whole collections. Replace hot paths (`list_leads`, analytics scoping in `services.py`) with indexed queries. See `docs/FIRESTORE_SCHEMA.md`.
3. **Apollo**: set `APOLLO_API_KEY`; replace `apollo_search` and `_mock_apollo_enrich` in `services.py` with official Apollo endpoint calls (timeouts, bounded retries, Retry-After).
4. **LinkedIn**: official exports only. Upload the export in Lead Discovery and pick source "LinkedIn". Add an approved integration later under `integrations()`.
5. **AI (Claude/OpenAI)**: answers are deterministic today. Implement `llm_explain()` in `services.py` to let a provider phrase the already-computed, permission-scoped facts. Never send raw tables.
6. Import rows are held in memory (`ROWS`) between upload and confirm. Move to a Firestore subcollection for multi-instance hosting.

## Campaigns and channels
Leads live in **campaigns** (Leads page = monday-style board: expand a campaign to see its leads; also Table and Kanban views). Each campaign has a primary channel: **Calls, LinkedIn, Email or Multi-channel**. Activity types and outcomes follow the channel (for example "Connection Sent / Replied" for LinkedIn, "Sent / Bounced" for email). The CRM never places calls or sends messages: people act outside the CRM, then log the result. Create campaigns from Leads (+ Campaign) or Settings; choose a campaign when adding, importing or Apollo-importing leads.

## Today page (command center)
Landing page, built from the 'signals to brief to approve' layout. Channel tiles (calls, LinkedIn, email, pipeline) filter a grouped, scrollable approval queue. Left: suggestions waiting for approval, ranked by a deterministic priority score. Middle: account brief, evidence, signals with source links, and the proposed action (Approve / Edit / Dismiss). Right: this week's numbers and an activity feed. Bottom: pipeline lanes.
- **Nothing is automated.** Every suggestion (call task, assign owner, stage move, archive duplicate, enrich leads) changes data only after a person approves it; approvals are audit-logged.
- **Agents** (in `backend/agents.py`): triage, call prep, signal scout, data cleaner, stale watcher, plus a digest. Max 5 open suggestions per person (managers' bucket 10).
- **Runs:** "Run now" button, or schedule 3x/day: point Cloud Scheduler / cron at `POST /api/v1/cron/run` with header `X-Cron-Secret: <CRON_SECRET>`. Runs are locked against overlap and suggestions are de-duplicated, so retries are safe.
- **Signals** (`SIGNAL_PROVIDER` in `.env`): `demo` (labelled fake data, default), `claude_web` (Claude web search: sends only company name/website, keeps only results that have a source URL, always shown as "unverified, check the source"), `off`. Plug any other search API into `_generic_web()`.

## Look and feel
Light, dark (follows the OS, or use the moon/sun button) and a neon **Reports** page: a 5-chapter story (Acquire, Reach, Engage, Convert, Hold the pace) with period goals, XP levels, streaks, badges and a leaderboard. Every gamified number comes from logged activity and its formula is shown on the page. Goals and the one-line pitch used in drafts are editable in Settings > Pipeline & goals.

## What is built
Overview KPIs (13, with definition tooltips, vs previous period, insights) · Leads (server filters, sort, cursor pagination, saved views, column control, bulk assign/stage/archive, Kanban with drag-and-drop, profile drawer with timeline and enrichment provenance) · Lead Discovery (Apollo search/import with duplicate flags, CSV/XLSX wizard: upload → map → validate → duplicates → confirm, error report, history, safe rollback) · Sales Activity queue (callbacks → overdue → new; log outcome; stage change only on confirm) · Follow-ups (complete, snooze, reschedule, reassign, history) · Opportunities Kanban · 6 reports + insights + CSV export (formula-injection safe) · AI assistant (page + side panel + per-lead entry, confirmed actions only, audit trail) · Integrations health · Settings (users/roles, stages, outcomes, audit log).

Demo data is labelled with a "Demo data" badge and resets on restart.
