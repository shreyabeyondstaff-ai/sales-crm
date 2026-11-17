# Architecture
- `backend/main.py` routes, auth dependency, RBAC (`need(perm)`), error schema `{error:{code,message,request_id}}`, security headers, per-user rate limit, body-size cap.
- `backend/services.py` all business rules. Outcome never changes stage; stage hints need user confirmation. Tasks are idempotent via `idem_key`. KPIs are computed in one place (`kpis`) and reused by dashboard, reports, AI.
- `backend/firebase.py` Firebase Admin init, token verification, `MemoryDB` / `FirestoreDB` with identical API.
- `backend/schemas.py` Pydantic request models.
- `frontend/` `index.html`, `style.css`, `app.js` (hash router, pages as functions), `config.js` (auth switch).
Background work: FastAPI BackgroundTasks, job state persisted in `enrichmentJobs`. No Redis/Celery.
