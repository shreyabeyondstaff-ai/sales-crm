"""FastAPI app. Auth: Firebase ID token (real) or mock users (AUTH_DISABLED=true).
Every protected route resolves role + record scope on the SERVER. Never trust the browser."""
import os, time, uuid
from typing import Optional
from fastapi import FastAPI, Depends, Header, Request, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import hmac, threading, firebase, ai, services as sv, agents as ag, story as st, companies as co
from firebase import db, AUTH_DISABLED
from schemas import *

app = FastAPI(title="Internal Sales CRM", version="1.0.0")
app.add_middleware(GZipMiddleware, minimum_size=800)      # app.js and every JSON page shrink about 5x on the wire
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8000").split(","), allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup():
    if firebase.USE_FIRESTORE: threading.Thread(target=firebase.db.warm, daemon=True).start()     # load the hot collections now, so the first person in doesn't wait for them
    if not firebase.USE_FIRESTORE:
        sv.seed()   # demo data only in memory mode
        try: ag.run_agents(ag.SYSTEM, "startup")
        except Exception: pass

@app.middleware("http")
async def mw(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]; request.state.rid = rid
    if int(request.headers.get("content-length") or 0) > 12 * 1024 * 1024:
        return JSONResponse({"error": {"code": "too_large", "message": "Request too large", "request_id": rid}}, 413)
    r = await call_next(request)
    r.headers.update({"X-Request-ID": rid, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"})
    return r

@app.exception_handler(HTTPException)
async def http_err(request: Request, exc: HTTPException):
    d = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse({"error": {**d, "request_id": getattr(request.state, "rid", "")}}, exc.status_code)

@app.exception_handler(Exception)
async def any_err(request: Request, exc: Exception):   # never leak stack traces
    return JSONResponse({"error": {"code": "internal", "message": "Something went wrong", "request_id": getattr(request.state, "rid", "")}}, 500)

# ------------------------------------------------------------------ auth + rate limit
_hits = {}
def limit(uid_, n=300):
    w = int(time.time() // 60); k = (uid_, w); _hits[k] = _hits.get(k, 0) + 1
    if len(_hits) > 5000: _hits.clear()
    if _hits[k] > n: raise HTTPException(429, {"code": "rate_limited", "message": "Too many requests, slow down"})

def auth(authorization: Optional[str] = Header(None), x_mock_user: Optional[str] = Header(None)):
    if AUTH_DISABLED:                                   # TEST MODE ONLY
        u = db.get("users", x_mock_user or "u_admin")
        if not u: raise HTTPException(401, {"code": "unauthenticated", "message": "Unknown mock user"})
    else:
        if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401, {"code": "unauthenticated", "message": "Missing token"})
        try: claims = firebase.verify_token(authorization[7:])
        except ValueError: raise HTTPException(401, {"code": "invalid_token", "message": "Invalid or expired token"})
        # Role comes from OUR users collection (keyed by Firebase uid), never from the request.
        u = db.get("users", claims["uid"]) or sv.first_login(claims)   # policy lives in services.first_login
        if not u: raise HTTPException(403, {"code": "no_profile", "message": "No CRM profile for this account. Ask an admin."})
        if u.get("pending"): raise HTTPException(403, {"code": "pending_approval", "message": "Your account is waiting for admin approval. You will get access once an admin activates it."})
        if not u.get("active", True): raise HTTPException(403, {"code": "disabled", "message": "Account disabled"})
    limit(u["id"]); return {**u, "id": u["id"]}

def need(perm):
    def dep(user=Depends(auth)):
        if not sv.can(user, perm): raise HTTPException(403, {"code": "forbidden", "message": "You do not have access to this"})
        return user
    return dep

V = "/api/v1"

@app.get("/healthz")
def health(): return {"ok": True}

@app.get(V + "/meta")
def meta(user=Depends(auth)):
    s = sv.settings(); perms = sorted(sv.PERMS[user["role"]])
    users = [{"id": u["id"], "name": u["name"], "role": u["role"]} for u in db.list("users") if u.get("active", True)]
    return {"me": {k: user.get(k) for k in ("id", "name", "role", "email")}, "perms": perms, "auth_disabled": AUTH_DISABLED, "stages": s["stages"], "outcomes": s["outcomes"],
            "sources": sv.SOURCES, "campaigns": [{"id": c["id"], "name": c["name"], "channel": c["channel"], "color": c["color"]} for c in db.list("campaigns") if not c.get("archived")], "outcomes_by_type": sv.OUTCOMES_BY_TYPE, "activity_types": sv.ACTIVITY_TYPES, "opp_stages": sv.OPP_STAGES, "users": users, "ai": ai.status(), "apollo_links": sv.apollo_links(), "demo": bool(db.query("leads", [("demo", "==", True)], limit=1))}

# ------------------------------------------------------------------ leads
@app.get(V + "/leads")
def leads(q: str = "", stage: str = "", source: str = "", owner: str = "", status: str = "", sort: str = "created_at", desc: bool = True, cursor: str = "", limit: int = 25, campaign: str = "", channel: str = "", user=Depends(need("leads:read"))):
    return sv.list_leads(user, q, stage, source, owner, status, sort, desc, cursor, limit, campaign=campaign, channel=channel)

@app.post(V + "/leads", status_code=201)
def lead_create(b: LeadIn, user=Depends(need("leads:write"))): return sv.create_lead(user, b)

@app.get(V + "/leads/board")
def leads_board(q: str = "", stage: str = "", source: str = "", owner: str = "", status: str = "", sort: str = "created_at", desc: bool = True, campaign: str = "", channel: str = "", limit: int = 10, user=Depends(need("leads:read"))):
    return sv.lead_board(user, q, stage, source, owner, status, campaign, channel, sort, desc, min(max(limit, 1), 50))

@app.get(V + "/leads/{lid}")
def lead_get(lid: str, user=Depends(need("leads:read"))):
    l = sv.get_lead(user, lid); c = co.for_lead(l)
    return {**l, "company_brief": co.brief(c) if c else None}

@app.get(V + "/leads/{lid}/company")
def lead_company(lid: str, user=Depends(need("leads:read"))): return co.brief_for_lead(user, lid)

@app.patch(V + "/leads/{lid}")
def lead_patch(lid: str, b: LeadPatch, user=Depends(need("leads:write"))): return sv.update_lead(user, lid, b)

@app.post(V + "/leads/{lid}/stage")
def lead_stage(lid: str, b: StageChange, user=Depends(need("leads:write"))): return sv.change_stage(user, lid, b.stage, b.reason)

@app.post(V + "/leads/{lid}/assign")
def lead_assign(lid: str, owner_id: Optional[str] = None, user=Depends(need("assign"))): return sv.assign(user, lid, owner_id)

@app.post(V + "/leads/bulk")
def lead_bulk(b: BulkAction, user=Depends(need("leads:write"))):
    if b.action == "assign" and not sv.can(user, "assign"): raise HTTPException(403, {"code": "forbidden", "message": "Cannot assign"})
    return sv.bulk(user, b)

@app.get(V + "/leads/{lid}/timeline")
def lead_timeline(lid: str, user=Depends(need("leads:read"))): return sv.timeline(user, lid)

# ------------------------------------------------------------------ campaigns
@app.get(V + "/campaigns")
def camps(user=Depends(need("leads:read"))): return sv.list_campaigns(user)

@app.post(V + "/campaigns", status_code=201)
def camp_new(b: CampaignIn, user=Depends(need("leads:read"))): return sv.create_campaign(user, b)

@app.patch(V + "/campaigns/{cid}")
def camp_patch(cid: str, b: CampaignPatch, user=Depends(need("leads:read"))): return sv.patch_campaign(user, cid, b)

# ------------------------------------------------------------------ companies (company-level lists and call cues)
@app.get(V + "/companies/board")
def co_board(q: str = "", list: str = "", user=Depends(need("companies:read"))): return co.board(user, q, list)

@app.get(V + "/companies")
def co_page(list: str, q: str = "", cursor: str = "", limit: int = 50, user=Depends(need("companies:read"))): return co.page(user, list, q, cursor, limit)

@app.post(V + "/companies/import")
async def co_import(file: UploadFile = File(...), list_id: str = Form(""), new_list_name: str = Form(""), preview: bool = Form(True), user=Depends(need("companies:write"))):
    return co.import_file(user, file.filename or "", await file.read(), list_id, new_list_name, preview)

@app.post(V + "/companies", status_code=201)
def co_new(b: CompanyIn, user=Depends(need("companies:write"))): return co.create_company(user, b)

@app.get(V + "/companies/apollo-lists")
def co_apollo_lists(user=Depends(need("companies:write"))): return co.apollo_lists(user)

@app.post(V + "/companies/apollo-sync")
def co_apollo_sync(b: CompanyApolloSync, user=Depends(need("companies:write"))): return co.sync_apollo(user, b.label_ids or None)

@app.get(V + "/companies/{cid}")
def co_get(cid: str, user=Depends(need("companies:read"))): return co.get(user, cid)

@app.patch(V + "/companies/{cid}")
def co_patch(cid: str, b: CompanyPatch, user=Depends(need("companies:write"))): return co.patch_company(user, cid, b)

@app.delete(V + "/companies/{cid}")
def co_del(cid: str, user=Depends(need("companies:write"))): return co.delete_company(user, cid)

@app.post(V + "/company-lists", status_code=201)
def col_new(b: CompanyListIn, user=Depends(need("companies:write"))): return co.create_list(user, b.name)

@app.patch(V + "/company-lists/{lid}")
def col_patch(lid: str, b: CompanyListPatch, user=Depends(need("companies:write"))): return co.patch_list(user, lid, b.name, b.archived)

# ------------------------------------------------------------------ imports
@app.post(V + "/imports/upload")
async def imp_upload(file: UploadFile = File(...), user=Depends(need("import"))):
    return sv.start_import(user, file.filename or "", await file.read())

@app.get(V + "/imports")
def imp_list(user=Depends(need("import"))): return db.query("importBatches", order_by="at", desc=True, limit=100)

@app.post(V + "/imports/{bid}/preview")
def imp_preview(bid: str, o: ImportOptions, user=Depends(need("import"))): return sv.preview_import(user, bid, o)

@app.post(V + "/imports/{bid}/confirm")
def imp_confirm(bid: str, o: ImportOptions, user=Depends(need("import"))): return sv.confirm_import(user, bid, o)

@app.post(V + "/imports/{bid}/rollback")
def imp_rb(bid: str, user=Depends(need("import"))): return sv.rollback_import(user, bid)

@app.post(V + "/imports/{bid}/errors.csv")
def imp_errors(bid: str, o: ImportOptions, user=Depends(need("import"))):
    p = sv.preview_import(user, bid, o); rows = [{"row": e["row"], "errors": "; ".join(e["errors"]), **e["data"]} for e in p["errors"]]
    cols = ["row", "errors"] + sorted({k for r in rows for k in r} - {"row", "errors"})
    return Response(sv.to_csv(rows, cols), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=import_errors.csv"})

# ------------------------------------------------------------------ integrations / enrichment
@app.get(V + "/integrations")
def integ(user=Depends(need("integrations:read"))): return sv.integrations()

@app.post(V + "/integrations/{name}/test")
def integ_test(name: str, user=Depends(need("integrations:read"))): return sv.test_integration(name)

@app.post(V + "/integrations/apollo/search")
def apollo_s(b: ApolloSearch, user=Depends(need("import"))): return sv.apollo_search(user, b)

@app.post(V + "/integrations/apollo/sync-campaigns")
def apollo_sync(user=Depends(need("assign"))): return sv.sync_apollo_campaigns(user)

@app.post(V + "/integrations/apollo/sync-emails")
def apollo_sync_emails(user=Depends(need("assign"))): return sv.sync_apollo_emails(user)

@app.get(V + "/integrations/apollo/lists")
def apollo_lists_route(user=Depends(need("assign"))): return sv.apollo_lists(user)

@app.post(V + "/integrations/apollo/lists/sync")
def apollo_lists_sync(b: ApolloListSync, user=Depends(need("assign"))): return sv.sync_apollo_lists(user, b.label_ids or None)

@app.get(V + "/intake")
def intake(user=Depends(need("assign"))): return sv.intake_overview(user)

@app.post(V + "/intake/batches/{bid}/review")
def intake_review(bid: str, user=Depends(need("assign"))): return sv.review_batch(user, bid)

@app.post(V + "/apollo/calls/scan")
def calls_scan_route(b: CallsIn, user=Depends(need("enrich"))): return sv.calls_scan(user, b.lead_ids)

@app.post(V + "/apollo/calls/sync")
def calls_sync_route(b: CallsIn, user=Depends(need("enrich"))): return sv.calls_sync(user, b.lead_ids, b.confirm)

@app.get(V + "/leads/{lid}/transcripts")
def lead_transcripts_route(lid: str, user=Depends(need("leads:read"))): return sv.lead_transcripts(user, lid)

@app.get(V + "/assign/people")
def assign_people(user=Depends(need("assign"))): return sv.assignees(user)

@app.get(V + "/assign/pool")
def assign_pool(campaign: str = "", channel: str = "", source: str = "", batch: str = "", min_fit: int = 0, signal: bool = False, phone: bool = False, limit: int = 100, user=Depends(need("assign"))):
    return sv.assignment_pool(user, {"campaign": campaign, "channel": channel, "source": source, "batch": batch, "min_fit": min_fit, "signal": signal, "phone": phone}, min(max(limit, 1), 500))

def _agents_after_assign():
    try: ag.run_agents(ag.SYSTEM, "assign")      # refresh the Today suggestions so assigned leads show up there too
    except Exception: pass

@app.post(V + "/assign/run")
def assign_run_route(b: AssignRun, bg: BackgroundTasks, user=Depends(need("assign"))):
    r = sv.assign_run(user, b.people, b.mode, b.filters.model_dump(), b.dry_run)
    if not b.dry_run and r["assigned"]: bg.add_task(_agents_after_assign)
    return r

@app.post(V + "/integrations/apollo/import")
def apollo_i(b: ApolloImport, user=Depends(need("import"))): return sv.apollo_import(user, b)

@app.post(V + "/enrichment/jobs", status_code=202)
def enr_create(b: EnrichIn, bg: BackgroundTasks, user=Depends(need("enrich"))):
    j = sv.create_enrichment(user, b.lead_ids, b.confirm, all_=b.all, refresh=b.refresh); bg.add_task(sv.run_enrichment, j["id"]); return j

@app.get(V + "/enrichment/preview")
def enr_preview(user=Depends(need("enrich"))): return sv.enrichment_preview(user)

@app.get(V + "/enrichment/jobs")
def enr_list(user=Depends(need("enrich"))): return db.query("enrichmentJobs", order_by="at", desc=True, limit=30)

@app.get(V + "/enrichment/jobs/{jid}")
def enr_get(jid: str, user=Depends(need("enrich"))):
    j = db.get("enrichmentJobs", jid)
    if not j: raise HTTPException(404, {"code": "not_found", "message": "Job not found"})
    return j

@app.post(V + "/enrichment/jobs/{jid}/retry", status_code=202)
def enr_retry(jid: str, bg: BackgroundTasks, user=Depends(need("enrich"))):
    j = sv.retry_enrichment(user, jid); bg.add_task(sv.run_enrichment, j["id"]); return j

# ------------------------------------------------------------------ activities / calls / tasks
@app.post(V + "/activities", status_code=201)
def act_create(b: ActivityIn, user=Depends(need("activities:write"))): return sv.log_activity(user, b)

@app.post(V + "/calls", status_code=201)
def call_log(b: ActivityIn, user=Depends(need("activities:write"))): b.type = "phone_call"; return sv.log_activity(user, b)

@app.get(V + "/activities")
def act_list(lead_id: str = "", limit: int = 100, user=Depends(need("activities:read"))): return sv.list_activities(user, lead_id, min(limit, 200))

@app.get(V + "/workspace/queue")
def queue(user=Depends(need("activities:write"))): return sv.workspace_queue(user)

@app.get(V + "/tasks")
def tasks(scope: str = "mine", status: str = "open", user=Depends(need("tasks:read"))): return sv.list_tasks(user, scope, status)

@app.post(V + "/tasks", status_code=201)
def task_new(b: TaskIn, user=Depends(need("tasks:write"))): return sv.create_task(user, b)

@app.post(V + "/tasks/{tid}/{action}")
def task_act(tid: str, action: str, b: Optional[TaskPatch] = None, user=Depends(need("tasks:write"))):
    if action not in ("complete", "snooze", "reschedule", "reassign"): raise HTTPException(404, {"code": "not_found", "message": "Unknown action"})
    if action == "reschedule" and not (b and b.due_at): raise HTTPException(422, {"code": "due_required", "message": "New date required"})
    return sv.task_action(user, tid, action, b)

# ------------------------------------------------------------------ opportunities
@app.get(V + "/opportunities")
def opps(user=Depends(need("opps:read"))): return sv.list_opps(user)

@app.post(V + "/opportunities", status_code=201)
def opp_new(b: OppIn, user=Depends(need("opps:write"))): return sv.create_opp(user, b)

@app.patch(V + "/opportunities/{oid}")
def opp_patch(oid: str, b: OppPatch, user=Depends(need("opps:write"))): return sv.patch_opp(user, oid, b)

# ------------------------------------------------------------------ analytics
def F(owner: str = "", source: str = "", stage: str = "", campaign: str = "", channel: str = ""): return {"owner": owner, "source": source, "stage": stage, "campaign": campaign, "channel": channel}

@app.get(V + "/analytics/overview")
def a_over(days: int = 30, f=Depends(F), user=Depends(need("reports:read"))): return st.overview(user, min(days, 365), f)

@app.get(V + "/analytics/{report}")
def a_rep(report: str, days: int = 30, f=Depends(F), user=Depends(need("reports:read"))):
    fn = {"funnel": sv.funnel, "sources": sv.sources_report, "calls": sv.calls_report, "team": sv.team_report, "health": sv.health_report, "enrichment": sv.enrichment_report, "insights": lambda u, d, f: {"insights": sv.insights(u, d, f)}, "story": st.story, "channels": st.channels_report, "campaigns": st.campaigns_report}.get(report)
    if not fn: raise HTTPException(404, {"code": "not_found", "message": "Unknown report"})
    return fn(user, min(days, 365), f)

@app.get(V + "/analytics/export/{report}")
def a_export(report: str, days: int = 30, f=Depends(F), user=Depends(need("export"))):
    fn = sv.EXPORTS.get(report)
    if not fn: raise HTTPException(404, {"code": "not_found", "message": "Unknown report"})
    rows, cols = fn(user, days, f); sv.audit(user, "export", "report", report, f"{len(rows)} rows")
    return Response(sv.to_csv(rows, cols), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={report}.csv"})

# ------------------------------------------------------------------ AI
@app.post(V + "/ai/chat")
def ai_chat(b: ChatIn, user=Depends(need("ai:use"))):
    r = sv.chat(user, b.message[:500], b.lead_id); sv.audit(user, "ai.query", "ai", "", b.message[:80]); return r

@app.post(V + "/ai/actions/{aid}/confirm")
def ai_confirm(aid: str, user=Depends(need("ai:use"))): return sv.confirm_ai_action(user, aid)

# ------------------------------------------------------------------ Today command center (all changes need human approval)
@app.get(V + "/today/mine")
def today_mine(user=Depends(need("activities:write"))): return ag.my_day(user)

@app.get(V + "/today")
def today(user=Depends(need("reports:read"))): return ag.today(user)

@app.post(V + "/today/run")
def today_run(user=Depends(need("agents:run"))): return ag.run_agents(user, "manual")

@app.post(V + "/suggestions/{sid}/approve")
def sug_approve(sid: str, bg: BackgroundTasks, b: Optional[SuggestionEdit] = None, user=Depends(auth)):
    s, job = ag.approve(user, sid, b)
    if job: bg.add_task(sv.run_enrichment, job["id"])
    return s

@app.post(V + "/suggestions/{sid}/dismiss")
def sug_dismiss(sid: str, b: Optional[DismissIn] = None, user=Depends(auth)): return ag.dismiss(user, sid, b.reason if b else "")

@app.post(V + "/cron/run")   # called by Cloud Scheduler / cron 3x per day. Secret header, no user session.
def cron_run(x_cron_secret: Optional[str] = Header(None)):
    sec = os.getenv("CRON_SECRET", "")
    if not sec or not hmac.compare_digest(x_cron_secret or "", sec): raise HTTPException(403, {"code": "forbidden", "message": "Bad or missing cron secret"})
    return ag.run_agents(ag.SYSTEM, "cron")

# ------------------------------------------------------------------ settings / users / audit (admin + developer via "*")
@app.get(V + "/settings")
def get_settings(user=Depends(need("settings:read"))): return sv.settings()

@app.put(V + "/settings")
def put_settings(b: SettingsIn, user=Depends(need("settings:write"))):
    s = sv.settings()
    for k, v in b.model_dump(exclude_none=True).items(): s[k] = v
    if s["stages"][:1] != ["New"]: raise HTTPException(422, {"code": "bad_stages", "message": "First stage must be New"})
    db.put("config", "main", s); sv.audit(user, "settings.update", "config", "main"); return s

@app.get(V + "/users")
def users(user=Depends(need("settings:read"))): return db.list("users")

@app.patch(V + "/users/{uid_}")
def user_patch(uid_: str, b: UserPatch, user=Depends(need("settings:write"))):
    u = db.get("users", uid_)
    if not u: raise HTTPException(404, {"code": "not_found", "message": "User not found"})
    if b.role:
        if b.role not in sv.PERMS: raise HTTPException(422, {"code": "bad_role", "message": "Unknown role"})
        u["role"] = b.role
    if b.active is not None:
        u["active"] = b.active
        if b.active: u.pop("pending", None)        # approving a first-login account clears its pending flag
    db.put("users", uid_, u); sv.audit(user, "user.update", "user", uid_, f"{b.role or ''} {b.active if b.active is not None else ''}"); return u

@app.get(V + "/audit")
def audit_logs(limit: int = 100, user=Depends(need("settings:read"))): return db.query("auditLogs", order_by="at", desc=True, limit=max(1, min(limit, 300)))

# ------------------------------------------------------------------ frontend (same origin, no CORS needed)
_fe = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_fe): app.mount("/", StaticFiles(directory=_fe, html=True), name="frontend")