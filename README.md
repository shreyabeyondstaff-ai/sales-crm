# SalesDesk: Internal Sales Outreach CRM (MVP)

Frontend: HTML + CSS + vanilla JS. Backend: Python + FastAPI. Auth: Firebase (switchable). DB: Firestore (switchable).
Every screen works on demo data. Real integrations (Apollo, Gemini/Groq, Firebase) are live when their keys are set and clearly show **"pending configuration"** when they are not: nothing is ever faked.

## Run it (2 minutes)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn main:app --reload --port 8000
```
Tests (in-memory data, no keys, every external call mocked): `pip install -r requirements-dev.txt && python -m pytest tests -q`
Open http://localhost:8000 (FastAPI serves the frontend too, so there is no CORS setup). API docs: http://localhost:8000/docs

## The auth true/false switch
| Mode | backend `.env` | `frontend/config.js` | What you get |
|---|---|---|---|
| **Test** (default) | `AUTH_DISABLED=true` | `AUTH_ENABLED:false` | No login. A dashed "Test as" dropdown in the top bar switches between 6 mock users (admin, 2 callers, outreach, DA, developer). Roles are still enforced by the backend. |
| **Real** | `AUTH_DISABLED=false` | `AUTH_ENABLED:true` + fill `FIREBASE` | Firebase email/password login, ID token sent as Bearer, verified with Firebase Admin SDK. |

## Roles
`admin` (manager/CEO) and `developer`: everything. `outreach`: discovery, import, enrichment, assign, leads. `caller`: own leads only, log activity, tasks. `da`: read-only reports, all leads, CSV export. Edit `PERMS` in `backend/services.py`.

## Your integration checklist
1. **Firebase Auth**: put the web config in `frontend/config.js`; give the backend the service account (`FIREBASE_SERVICE_ACCOUNT_JSON` or `GOOGLE_APPLICATION_CREDENTIALS`); set `AUTH_DISABLED=false`. First-login policy and the bootstrap admin are in `.env.example`.
2. **Firestore**: set `USE_FIRESTORE=true`. Queries are pushed to the store for the hot paths (see `docs/FIRESTORE_SCHEMA.md`).
3. **Apollo**: set `APOLLO_API_KEY`. Lead Discovery > Search Apollo (title, company/domain, location, seniority, paged, duplicate preview, import into a campaign); Leads > **Enrich all** / selected / per lead (bulk enrichment in the background, with progress, per-lead results, retry and credit usage); Leads > **Sync from Apollo** (active sequences become campaigns with their contacts). Without a key these say "pending configuration".
4. **LinkedIn**: official exports only. Upload the export in Lead Discovery and pick source "LinkedIn".
5. **AI assistant**: `AI_PROVIDER=gemini` + `GEMINI_API_KEY`, or `AI_PROVIDER=groq` + `GROQ_API_KEY` (model names are env variables). The LLM only phrases facts the backend already calculated. Lead questions (summarise, objections, next steps, draft a follow-up, summarise a call) send only that one lead's CRM facts and stored transcripts. Without a key the assistant stays rule-based and says "AI provider pending configuration".
6. Details, rate-limit and credit notes: `docs/INTEGRATIONS.md`.

## Campaigns and channels
Leads live in **campaigns** (Leads page = monday-style board: expand a campaign to see its leads; also Table and Kanban views). Each campaign has a primary channel: **Calls, LinkedIn, Email or Multi-channel**. Activity types and outcomes follow the channel (for example "Connection Sent / Replied" for LinkedIn, "Sent / Bounced" for email). The CRM never places calls or sends messages: people act outside the CRM, then log the result. Create campaigns from Leads (+ Campaign) or Settings; choose a campaign when adding, importing or Apollo-importing leads.

**Channels per lead (multi-channel outreach).** Every import (Apollo search or file upload) asks *Where should these leads go?*: tick any mix of **Calls, LinkedIn, Email**, pick a campaign (or create one), and optionally assign an owner. A lead appears in the Sales Activity tab of **every channel it has**, for its owner (callers only see their own leads). Channels live on the lead (editable in the lead form); a lead without its own channels follows its campaign's channel, and one with neither shows under All only. On the Today page a lead's suggestion uses its first channel in the order call, LinkedIn, email. Email leads from Apollo sequences show a read-only email history instead of a manual log button.

## New data (the admin's home)
The **admin** hands out work and does not work leads, so their home is **New data** (nav: first item) and **Today** and **Sales Activity** are hidden for them. Those stay for callers, outreach and developers. New data shows what has arrived and what still needs an owner: waiting / added this week / with a signal / with a phone, then one row per **batch** (each file upload, Apollo import, list sync or sequence sync): how many leads are waiting, how many assigned, average fit, status **New / Reviewed / All assigned**. Decisions are per batch, never per lead: **Review leads** (opens Assign leads on just that batch), **Assign…** (split the whole batch between callers in one step, with a preview), **Mark reviewed**. Callers' current load sits underneath. The Today agents no longer propose "assign this lead to X" one by one.

## Assign leads (the setup page for callers)
Nav: **Assign leads** (admin, developer, outreach). The flow for call lists: upload your outreach list in Apollo, open this page, **Show my Apollo lists**, tick the list(s) and **Sync**. Each list becomes a **Calls** campaign and its contacts become unassigned leads (no sequences or emails involved). Below, all unassigned leads are ranked by **fit**, best first, and filterable by campaign, channel, source, minimum fit, "has a signal" and "has a phone".
- **Fit (0 to 100, shown with a breakdown on hover):** signal 35 (+5 with a source link), title seniority up to 25, direct phone 15 (5 if it is only the company switchboard), email 5, company 5, LinkedIn 5. The numbers live in `fit_score()` in `services.py`: change them there to change what "most fit" means.
- **Assigning:** type how many leads each caller should get (for example 40), pick *Round-robin* (everyone gets a similar mix of strong and weaker leads) or *Best first* (the first person gets the top N, the next person the next N), **Preview**, then **Assign**. Max 500 per run. Each lead gets the usual first-contact task and an audit entry. The caller sees them in Sales Activity immediately; Today is refreshed in the background right after.
- Only active callers and outreach users can receive leads. Running it again continues with the next best unassigned leads.


## My day (the caller's Today)
Callers don't get the approval queue. Their **Today** lists **every lead assigned to them**, in working order: **callbacks due**, then **follow-ups due or overdue**, then **new leads they haven't contacted** (ranked by fit: signal, seniority, phone), then **other assigned leads** already in progress. Visibility is simply assignment: it does not depend on channel or on whether Apollo already emailed the lead. Each row shows its campaign, with a campaign filter on top. Each row has the phone number to call, the signal as the opener, the LinkedIn link, talking points (Notes), and **Log outcome**. Leads show up the moment they are assigned, with no agent run needed. Tiles and the weekly numbers are the caller's own. Everyone else keeps the command-center Today described below.

## Today page (command center)
Landing page, built from the 'signal, brief, approve' layout. Channel tiles (calls, LinkedIn, email, pipeline) filter a grouped, scrollable approval queue. Left: suggestions waiting for approval, ranked by a deterministic priority score. Middle: account brief, evidence, the lead's signal, and the proposed action (Approve / Edit / Dismiss). Right: this week's numbers and an activity feed. Bottom: pipeline lanes.
- **Nothing is automated.** Every suggestion (call task, assign owner, stage move, archive duplicate, enrich leads) changes data only after a person approves it; approvals are audit-logged.
- **Signals are a column, not a lookup.** Each lead has a `signal` (and optional `signal_url`) that a person types, uploads (a "Signal" column in CSV/XLSX) or imports. Leads with a signal get a priority boost and appear first, the signal shows in "Why this is here", and it is the opener in call briefs and LinkedIn/email drafts. Leads without a signal still appear through the existing rules (callbacks, overdue follow-ups, stale leads, unassigned). There are no web or AI signal lookups anywhere.
- **Agents** (in `backend/agents.py`): triage, call prep, data cleaner, stale watcher, plus a digest. Max 5 open suggestions per person (managers' bucket 10).
- **Runs:** "Run now" button, or schedule 3x/day: point Cloud Scheduler / cron at `POST /api/v1/cron/run` with header `X-Cron-Secret: <CRON_SECRET>`. Runs are locked against overlap and suggestions are de-duplicated, so retries are safe.

## Look and feel
Light, dark (follows the OS, or use the moon/sun button) and a neon **Reports** page: a 5-chapter story (Acquire, Reach, Engage, Convert, Hold the pace) with period goals, XP levels, streaks, badges and a leaderboard. Every gamified number comes from logged activity and its formula is shown on the page. Goals and the one-line pitch used in drafts are editable in Settings > Pipeline & goals.

## What is built
Overview KPIs (13, with definition tooltips, vs previous period, insights) · Leads (server filters, sort, cursor pagination, saved views, column control, bulk assign/stage/archive, Kanban with drag-and-drop, profile drawer with timeline and enrichment provenance) · Lead Discovery (Apollo search/import with filters, paging and duplicate flags, CSV/XLSX wizard incl. a Signal column: upload → map → validate → duplicates → confirm, error report, history, safe rollback) · Enrich all with live progress · Sales Activity queue (callbacks → overdue → new; log outcome; stage change only on confirm) · Follow-ups (complete, snooze, reschedule, reassign, history) · Opportunities Kanban · 6 reports + insights + CSV export (formula-injection safe) · AI assistant (page + side panel + per-lead entry, confirmed actions only, audit trail) · Integrations health (Apollo, Gemini, Groq, Firebase, each with a real test call) · Settings (users/roles, stages, outcomes, audit log).

Demo data is labelled with a "Demo data" badge and resets on restart.
