# Status vs PRD
Done (working on demo data): all 10 navigation areas, RBAC, imports, duplicates, enrichment flow, activity logging, tasks, reports, AI assistant guardrails, audit log.
Mocked until you connect: Apollo search/enrichment, LLM explanations, Firebase (switch), LinkedIn (export only).
Not done: PDF export, XLS, webhooks, Firestore Security Rules file, firestore.indexes.json, automated test suite, backups/retention, Firestore-native pagination.

Today page: done on demo signals. Real signals need SIGNAL_PROVIDER=claude_web (untested against the live API) or your own search API. Scheduler needs your host's cron.
Collections added: suggestions, signals, agentRuns, agentEvents.

Campaigns, per-channel outcomes (calls, LinkedIn, email), board view, neon story reports, light/dark theme: done.
Collections added: campaigns. Lead fields added: campaign_id, touches.
