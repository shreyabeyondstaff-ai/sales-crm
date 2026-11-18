# Status vs PRD
Done (working on demo data): all 10 navigation areas, RBAC, imports, duplicates, enrichment flow, activity logging, tasks, reports, AI assistant guardrails, audit log.
Live when configured, "pending configuration" otherwise (never faked): Apollo search, bulk enrichment and sequence sync; Gemini/Groq answers; Firebase (switch). LinkedIn: export only.
Not done: Apollo call-transcript sync (waiting on two open questions about Apollo's Conversations API), PDF export, XLS, webhooks, Firestore Security Rules file, backups/retention, Firestore-native pagination. Tests: `backend/tests` (import, Apollo, enrichment, AI, Today/signal).

Today page: driven by each lead's signal column (no AI/web signal lookup). Scheduler needs your host's cron.
Collections added: suggestions, agentRuns, agentEvents. (The signals collection was removed.)

Campaigns, per-channel outcomes (calls, LinkedIn, email), board view, neon story reports, light/dark theme: done.
Collections added: campaigns. Lead fields added: campaign_id, touches.
