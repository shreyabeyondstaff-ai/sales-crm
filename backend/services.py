"""All business logic. Routes in main.py stay thin; swap providers/stores without touching them."""
import csv, io, re, os, random, json, time
from math import ceil
import httpx
import ai
import companies as co
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from fastapi import HTTPException
from firebase import db, now, uid

# ----------------------------------------------------------------------------- constants
DEFAULT_STAGES = ["New", "Ready to Contact", "Attempted Contact", "Connected", "Qualified",
                  "Meeting Scheduled", "Opportunity", "Won", "Lost", "Nurture"]
OUTCOMES = ["Connected", "No Answer", "Busy", "Voicemail", "Call Back Requested", "Interested",
            "Not Interested", "Wrong Number", "Meeting Requested", "Meeting Scheduled", "Do Not Contact"]
CONNECTED = {"Connected", "Interested", "Not Interested", "Meeting Requested", "Meeting Scheduled"}
ACTIVITY_TYPES = ["phone_call", "linkedin", "email", "meeting", "other"]
CHANNELS = ["call", "linkedin", "email", "mixed"]
CH3 = ("call", "linkedin", "email")      # the real outreach channels; a lead can use any mix of them
LI_OUT = ["Connection Sent", "Connection Accepted", "Replied", "No Response", "Not Interested", "Meeting Requested", "Meeting Scheduled", "Do Not Contact"]
EM_OUT = ["Sent", "Replied", "Bounced", "No Response", "Not Interested", "Meeting Requested", "Meeting Scheduled", "Do Not Contact"]
MEET_OUT = ["Meeting Scheduled", "Meeting Held", "No Show", "Rescheduled"]
OUTCOMES_BY_TYPE = {"phone_call": OUTCOMES, "linkedin": LI_OUT, "email": EM_OUT, "meeting": MEET_OUT, "other": sorted(set(OUTCOMES + LI_OUT + EM_OUT))}
ALL_OUT = set(OUTCOMES) | set(LI_OUT) | set(EM_OUT) | set(MEET_OUT)
ENGAGED = {"phone_call": CONNECTED, "linkedin": {"Connection Accepted", "Replied", "Meeting Requested", "Meeting Scheduled"}, "email": {"Replied", "Meeting Requested", "Meeting Scheduled"}}
ENGAGED_ALL = set().union(*ENGAGED.values())
DEFAULT_GOALS = {"touches": 150, "engaged": 35, "meetings": 8, "won": 3}   # per 30 days, scaled to the selected period
SOURCES = ["Apollo", "LinkedIn", "Google Research", "Claude", "ChatGPT", "Referral", "Event", "File Upload", "Manual"]
OPP_STAGES = ["Discovery", "Proposal", "Negotiation", "Won", "Lost"]
TERMINAL = {"Won", "Lost", "Nurture"}
# Outcome -> suggested follow-up task (days). Outcomes never move the stage by themselves.
OUTCOME_TASK = {"No Answer": ("Retry call", 1), "Busy": ("Retry call", 1), "Voicemail": ("Follow up after voicemail", 2),
                "Call Back Requested": ("Callback requested", None), "Meeting Requested": ("Schedule the meeting", 1),
                "Interested": ("Send info / qualify", 2), "Meeting Scheduled": ("Meeting follow-up", 3),
                "Connection Sent": ("Follow up on LinkedIn request", 3), "Connection Accepted": ("Send first LinkedIn message", 1), "Replied": ("Reply and move forward", 1),
                "No Response": ("Follow-up nudge", 4), "Sent": ("Follow up on email", 3), "Bounced": ("Fix email address", 1)}
OUTCOME_STAGE_HINT = {"No Answer": "Attempted Contact", "Busy": "Attempted Contact", "Voicemail": "Attempted Contact",
                      "Connected": "Connected", "Interested": "Connected", "Meeting Requested": "Connected",
                      "Meeting Scheduled": "Meeting Scheduled", "Not Interested": "Lost", "Do Not Contact": "Lost",
                      "Connection Sent": "Attempted Contact", "Sent": "Attempted Contact", "Connection Accepted": "Connected", "Replied": "Connected"}

# Roles: admin = manager/CEO, caller, developer, da = data analyst, outreach = outreach team
PERMS = {
    "admin": {"*"}, "developer": {"*"},
    "da": {"leads:read", "tasks:read", "opps:read", "reports:read", "ai:use", "activities:read", "export", "companies:read"},
    "caller": {"leads:read", "leads:write", "activities:read", "activities:write", "tasks:read", "tasks:write",
               "opps:read", "opps:write", "reports:read", "ai:use", "agents:act"},
    "outreach": {"leads:read", "leads:write", "leads:create", "import", "enrich", "assign", "activities:read",
                 "activities:write", "tasks:read", "tasks:write", "opps:read", "opps:write", "reports:read",
                 "ai:use", "export", "integrations:read", "agents:act", "agents:run", "companies:read", "companies:write"},
}
def can(user, perm): p = PERMS.get(user["role"], set()); return "*" in p or perm in p
def signal(l):
    """Why call now: the lead's own signal, else its company's signal (company lists carry the signal; people lists do not)."""
    return (l.get("signal") or "").strip() or co.signal_for_lead(l)

def lead_channels(l, camps):
    """Channels a lead is worked on: its own list when set (multi-channel outreach), else its campaign's channel. [] = unspecified (shows under All only)."""
    own = [c for c in (l.get("channels") or []) if c in CH3]
    if own: return own
    c = (camps.get(l.get("campaign_id")) or {}).get("channel"); return [c] if c in CH3 else []

def _in_channel(l, camps, channel):
    chs = lead_channels(l, camps); return len(chs) != 1 if channel == "mixed" else channel in chs

def visible(user, lead): return user["role"] != "caller" or lead.get("owner_id") == user["id"]

def active_leads(user=None, **eq):
    """Non-archived leads. Equality filters (stage=, source=, owner_id=, campaign_id=, ...) are pushed down to the
    store, so Firestore only reads matching documents. Callers are scoped to their own leads here as well
    (callers still pass results through visible() as defence in depth). Equality-only => no composite index."""
    if user and user["role"] == "caller":
        if eq.get("owner_id", user["id"]) != user["id"]: return []
        eq["owner_id"] = user["id"]
    return db.query("leads", [("archived", "==", False)] + [(k, "==", v) for k, v in eq.items()])

def for_leads(col, ids):
    """Documents of `col` whose lead_id is in ids (chunked: Firestore 'in' allows 30 values)."""
    ids = list(ids); out = []
    for i in range(0, len(ids), 30): out += db.query(col, [("lead_id", "in", ids[i:i + 30])])
    return out

# ----------------------------------------------------------------------------- first login
def first_login(claims):
    """A valid Firebase account with no CRM profile signs in for the first time.
    FIRST_LOGIN_POLICY=pending (default): create a disabled 'caller' profile flagged pending; an admin approves it
    (PATCH /users/{id} active=true) and can change the role. FIRST_LOGIN_POLICY=reject: create nothing.
    BOOTSTRAP_ADMIN_EMAILS (comma list) gets an active admin profile on first login, only if Firebase says the
    email is verified. That is how the very first admin exists on an empty Firestore."""
    email = n_email(claims.get("email"))
    boot = {n_email(e) for e in os.getenv("BOOTSTRAP_ADMIN_EMAILS", "").split(",") if e.strip()}
    is_boot = bool(email) and email in boot and claims.get("email_verified") is True
    if not is_boot and os.getenv("FIRST_LOGIN_POLICY", "pending").lower() == "reject": return None
    name = claims.get("name") or (email.split("@")[0] if email else "New user")
    u = db.put("users", claims["uid"], {"name": name, "email": email, "role": "admin" if is_boot else "caller", "active": is_boot,
                                        "pending": not is_boot, "created_at": now(), "created_via": "first_login"})
    audit({"id": claims["uid"], "name": name}, "user.first_login", "user", claims["uid"], "bootstrap admin" if is_boot else "pending approval")
    return u

def settings():
    s = db.get("config", "main")
    if not s: s = db.put("config", "main", {"stages": DEFAULT_STAGES, "outcomes": OUTCOMES, "stale_days": 7, "require_lost_reason": True})
    s.setdefault("pitch", "helping sales teams follow up faster"); s.setdefault("goals", dict(DEFAULT_GOALS)); return s

def dt(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def audit(user, action, entity, eid, detail=""):
    db.put("auditLogs", uid("aud"), {"at": now(), "user_id": user["id"], "user": user["name"], "action": action, "entity": entity, "entity_id": eid, "detail": detail})
def err(code, msg, status=400): raise HTTPException(status, detail={"code": code, "message": msg})

# ----------------------------------------------------------------------------- normalisation / dedupe
def n_email(e): return (e or "").strip().lower()
def n_phone(p): d = re.sub(r"\D", "", p or ""); return d[-10:] if len(d) >= 7 else ""
def n_li(u): return re.sub(r"^https?://(www\.)?|/+$|\?.*$", "", (u or "").strip().lower())
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def find_dupe(rec, leads=None):
    """Return (lead, confidence, signal). Name+company is low confidence => human review.
    A phone number alone is NOT a match: many people at one company share its switchboard number. It only counts together with the same name."""
    leads = leads if leads is not None else active_leads()
    e, p, li, ap = n_email(rec.get("email")), n_phone(rec.get("phone")), n_li(rec.get("linkedin_url")), rec.get("apollo_id")
    nm = f"{rec.get('first_name','')} {rec.get('last_name','')}".strip().lower()
    for l in leads:
        if e and n_email(l.get("email")) == e: return l, "high", "email"
        if ap and l.get("apollo_id") == ap: return l, "high", "apollo_id"
        if li and n_li(l.get("linkedin_url")) == li: return l, "high", "linkedin"
        if p and nm and n_phone(l.get("phone")) == p and (l.get("full_name") or "").lower() == nm: return l, "high", "phone+name"
    co = (rec.get("company") or "").lower()
    if nm and co:
        for l in leads:
            if l["full_name"].lower() == nm and (l.get("company") or "").lower() == co: return l, "low", "name+company"
    return None, None, None

# ----------------------------------------------------------------------------- leads
FIELDS = ["first_name", "last_name", "title", "company", "email", "phone", "linkedin_url", "website", "location", "country", "signal", "signal_url"]

def new_lead(data, user, batch=None, demo=False, created=None, owner=None):
    t = created or now(); lid = uid("lead")
    l = {f: (data.get(f) or "").strip() if isinstance(data.get(f), str) else (data.get(f) or "") for f in FIELDS}
    l.update({"full_name": f"{l['first_name']} {l['last_name']}".strip(), "email": n_email(l["email"]),
              "source": data.get("source") or "Manual", "source_detail": data.get("source_detail", ""),
              "channels": [c for c in dict.fromkeys(data.get("channels") or []) if c in CH3], "apollo_id": data.get("apollo_id", ""), "apollo_contact_id": data.get("apollo_contact_id", ""), "owner_id": (owner if owner is not None else data.get("owner_id")) or None,
              "stage": "New", "stage_history": [{"stage": "New", "at": t, "by": user["id"]}], "tags": data.get("tags", []),
              "created_at": t, "updated_at": t, "created_by": user["id"], "import_batch_id": batch, "archived": False,
              "suppressed": False, "verified_fields": [], "provenance": {}, "enrichment_state": "not_enriched",
              "last_enriched_at": None, "last_activity_at": None, "attempts": 0, "next_action": None,
              "next_action_due": None, "demo": demo, "campaign_id": data.get("campaign_id") or None, "touches": 0})
    return db.put("leads", lid, l)

def get_lead(user, lid):
    l = db.get("leads", lid)
    if not l or not visible(user, l): err("not_found", "Lead not found", 404)
    return l

def save_lead(l): l["updated_at"] = now(); return db.put("leads", l["id"], l)

def create_lead(user, body):
    d = body.model_dump()
    if d.get("campaign_id") and not db.get("campaigns", d["campaign_id"]): err("bad_campaign", "Unknown campaign")
    if user["role"] == "caller": d["owner_id"] = user["id"]
    dup, conf, sig = find_dupe(d)
    if dup and conf == "high": err("duplicate", f"Possible duplicate of {dup['full_name']} (matched on {sig}). Open the existing lead instead.", 409)
    l = new_lead(d, user, owner=d.get("owner_id")); audit(user, "lead.create", "lead", l["id"], l["source"])
    return l

def _camp_set(campaign):
    """'a,b,none' -> {'a','b',None}: campaign filters accept several campaigns at once ('none' = leads without a campaign)."""
    return {(None if x == "none" else x) for x in (campaign or "").split(",") if x}

def _filtered(user, q="", stage="", source="", owner="", status="", campaign="", channel=""):
    eq = {}                                    # pushed to the store; everything else runs on the narrowed set
    if stage: eq["stage"] = stage
    if source: eq["source"] = source
    cs = _camp_set(campaign)
    if len(cs) == 1: eq["campaign_id"] = next(iter(cs))
    if owner: eq["owner_id"] = None if owner == "unassigned" else owner
    ls = [l for l in active_leads(user, **eq) if visible(user, l)]
    if len(cs) > 1: ls = [l for l in ls if (l.get("campaign_id") or None) in cs]
    if q:
        q = q.lower(); ls = [l for l in ls if q in (l["full_name"] + l.get("company", "") + l.get("email", "") + l.get("phone", "") + l.get("title", "")).lower()]
    if channel:
        camps = {c["id"]: c for c in db.list("campaigns")}; ls = [l for l in ls if _in_channel(l, camps, channel)]
    n = now()
    if status == "overdue": ls = [l for l in ls if l.get("next_action_due") and l["next_action_due"] < n]
    if status == "no_next_action": ls = [l for l in ls if not l.get("next_action") and l["stage"] not in TERMINAL]
    if status == "untouched": ls = [l for l in ls if not l.get("last_activity_at") and l["stage"] not in TERMINAL]
    if status == "unassigned": ls = [l for l in ls if not l.get("owner_id")]
    return ls

def _page(ls, sort, desc, cursor, limit):
    key = sort if sort in ("created_at", "full_name", "company", "stage", "last_activity_at", "next_action_due", "source", "updated_at") else "created_at"
    present = sorted((l for l in ls if l.get(key)), key=lambda l: str(l[key]).lower(), reverse=desc); ls = present + [l for l in ls if not l.get(key)]
    start = int(cursor or 0); limit = min(max(limit, 1), 100)
    page = [dict(l, has_company=bool(co.for_lead(l))) for l in ls[start:start + limit]]       # a copy per row: the flag is for the screen, never saved
    return {"items": page, "next_cursor": str(start + limit) if start + limit < len(ls) else None, "total": len(ls)}

def list_leads(user, q="", stage="", source="", owner="", status="", sort="created_at", desc=True, cursor="", limit=25, view="", campaign="", channel=""):
    return _page(_filtered(user, q, stage, source, owner, status, campaign, channel), sort, desc, cursor, limit)

def lead_board(user, q="", stage="", source="", owner="", status="", campaign="", channel="", sort="created_at", desc=True, per=10):
    """The Leads board in ONE request: every campaign group with its first rows. (It used to be one request per campaign, each re-reading every lead.)"""
    ls = _filtered(user, q, stage, source, owner, status, "", ""); camps = {c["id"]: c for c in db.list("campaigns")}; groups = defaultdict(list)
    for l in ls: groups[l.get("campaign_id") or "none"].append(l)
    cs = list_campaigns(user); want = _camp_set(campaign)
    if want: cs = [c for c in cs if c["id"] in want]
    elif channel: cs = [c for c in cs if c["channel"] == channel]
    out = {}
    for c in cs:
        g = groups.get(c["id"], [])
        if channel and not want: g = [l for l in g if _in_channel(l, camps, channel)]
        out[c["id"]] = _page(g, sort, desc, "", per)
    return {"campaigns": cs, "groups": out}

def update_lead(user, lid, patch):
    l = get_lead(user, lid); p = patch.model_dump(exclude_none=True)
    for k, v in p.items():
        if k in FIELDS and v != l.get(k):
            l[k] = n_email(v) if k == "email" else v
            if k not in ("verified_fields",) and k in ("email", "phone", "title", "company"): l["verified_fields"] = list(set(l["verified_fields"] + [k]))  # user edits are "user-verified"
        elif k == "channels": l["channels"] = [c for c in dict.fromkeys(v or []) if c in CH3]
        elif k in ("tags", "verified_fields", "campaign_id"): l[k] = v or ([] if k != "campaign_id" else None)
    l["full_name"] = f"{l['first_name']} {l['last_name']}".strip(); audit(user, "lead.update", "lead", lid, ",".join(p))
    return save_lead(l)

def change_stage(user, lid, stage, reason=""):
    s = settings(); l = get_lead(user, lid)
    if stage not in s["stages"]: err("bad_stage", "Unknown stage")
    if stage in ("Lost",) and s.get("require_lost_reason") and not reason.strip(): err("reason_required", "A reason is required for Lost", 422)
    if stage == l["stage"]: return l
    l["stage_history"].append({"stage": stage, "at": now(), "by": user["id"], "reason": reason, "from": l["stage"]})
    audit(user, "lead.stage", "lead", lid, f"{l['stage']} -> {stage}"); l["stage"] = stage
    return save_lead(l)

def assign(user, lid, owner_id):
    if not can(user, "assign"): err("forbidden", "You cannot assign leads", 403)
    if owner_id and not db.get("users", owner_id): err("bad_owner", "Unknown user")
    l = db.get("leads", lid)
    if not l: err("not_found", "Lead not found", 404)
    audit(user, "lead.assign", "lead", lid, f"{l.get('owner_id')} -> {owner_id}"); l["owner_id"] = owner_id or None
    if owner_id and l["stage"] in ("New", "Ready to Contact") and not db.query("tasks", [("lead_id", "==", lid), ("status", "==", "open")], limit=1):
        make_task(l, "First contact", "first_contact", (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(timespec="seconds"), owner_id, idem=f"assign:{lid}:{owner_id}")
    return save_lead(l)

def bulk(user, b):
    done = 0
    for i in b.ids[:200]:
        try:
            if b.action == "assign": assign(user, i, b.value)
            elif b.action == "stage": change_stage(user, i, b.value, b.reason)
            elif b.action == "campaign":
                l = get_lead(user, i); l["campaign_id"] = b.value or None; save_lead(l); audit(user, "lead.campaign", "lead", i, b.value)
            elif b.action == "archive":
                l = get_lead(user, i); l["archived"] = True; save_lead(l); audit(user, "lead.archive", "lead", i)
            done += 1
        except HTTPException: pass
    return {"updated": done, "requested": len(b.ids)}

def timeline(user, lid):
    get_lead(user, lid)
    acts = [dict(a, kind="activity") for a in db.query("activities", [("lead_id", "==", lid)])]
    tasks = [dict(t, kind="task") for t in db.query("tasks", [("lead_id", "==", lid)])]
    l = db.get("leads", lid); hist = [dict(h, kind="stage", at=h["at"]) for h in l["stage_history"]]
    return sorted(acts + tasks + hist, key=lambda x: x.get("at") or x.get("created_at"), reverse=True)

# ----------------------------------------------------------------------------- tasks & activities
def refresh_next(lid):
    l = db.get("leads", lid)
    if not l: return
    op = sorted(db.query("tasks", [("lead_id", "==", lid), ("status", "==", "open")]), key=lambda t: t["due_at"])
    l["next_action"], l["next_action_due"] = (op[0]["title"], op[0]["due_at"]) if op else (None, None); save_lead(l)

def make_task(lead, title, typ, due, owner, prio="medium", notes="", idem=None):
    if idem:
        hit = db.query("tasks", [("idem_key", "==", idem)], limit=1)
        if hit: return hit[0]                               # idempotent: retries never duplicate
    t = db.put("tasks", uid("task"), {"title": title, "type": typ, "lead_id": lead["id"], "lead_name": lead["full_name"],
               "company": lead.get("company", ""), "owner_id": owner or lead.get("owner_id"), "due_at": due, "priority": prio,
               "status": "open", "notes": notes, "created_at": now(), "completed_at": None, "idem_key": idem})
    refresh_next(lead["id"]); return t

def log_activity(user, a):
    if not can(user, "activities:write"): err("forbidden", "Not allowed", 403)
    l = get_lead(user, a.lead_id)
    if a.outcome not in (set(settings()["outcomes"]) | ALL_OUT): err("bad_outcome", "Unknown outcome")
    if a.type not in ACTIVITY_TYPES: err("bad_type", "Unknown activity type")
    if a.outcome == "Call Back Requested" and not a.follow_up_due: err("due_required", "Pick a callback date and time", 422)
    aid = uid("act")
    act = db.put("activities", aid, {"lead_id": l["id"], "lead_name": l["full_name"], "user_id": user["id"], "user": user["name"],
               "type": a.type, "outcome": a.outcome, "notes": a.notes, "next_action": a.next_action, "occurred_at": a.occurred_at or now(),
               "at": now(), "follow_up_due": a.follow_up_due, "duration_min": a.duration_min, "external_ref": a.external_ref})
    l["last_activity_at"] = act["at"]; l["attempts"] = l.get("attempts", 0) + (1 if a.type == "phone_call" else 0); l["touches"] = l.get("touches", 0) + (1 if a.type in ("phone_call", "linkedin", "email") else 0)
    task = None
    if a.outcome == "Do Not Contact":
        l["suppressed"] = True
        for t in db.query("tasks", [("lead_id", "==", l["id"]), ("status", "==", "open")]): t["status"] = "cancelled"; db.put("tasks", t["id"], t)
    elif a.outcome in OUTCOME_TASK or a.follow_up_due:
        title, days = OUTCOME_TASK.get(a.outcome, (a.next_action or "Follow up", 2))
        due = a.follow_up_due or (datetime.now(timezone.utc) + timedelta(days=days or 1)).isoformat(timespec="seconds")
        task = make_task(l, a.next_action or title, "callback" if "Call Back" in a.outcome else "follow_up", due, user["id"] if user["role"] == "caller" else l.get("owner_id") or user["id"], notes=a.notes, idem=f"act:{aid}")
    save_lead(l); refresh_next(l["id"])
    hint = OUTCOME_STAGE_HINT.get(a.outcome); st = settings()["stages"]
    cur = db.get("leads", l["id"])["stage"]
    sug = hint if hint and cur not in TERMINAL and (hint in ("Lost",) or (hint in st and cur in st and st.index(hint) > st.index(cur))) else None
    return {"activity": act, "task": task, "suggested_stage": sug, "lead": db.get("leads", l["id"])}

def list_tasks(user, scope="mine", status="open"):
    w = []
    if user["role"] == "caller" or scope == "mine": w.append(("owner_id", "==", user["id"]))
    if status != "all": w.append(("status", "==", status))
    ts = db.query("tasks", w)
    return sorted(ts, key=lambda t: t["due_at"])

def task_action(user, tid, action, body=None):
    t = db.get("tasks", tid)
    if not t: err("not_found", "Task not found", 404)
    if user["role"] == "caller" and t.get("owner_id") != user["id"]: err("forbidden", "Not your task", 403)
    if action == "complete": t["status"], t["completed_at"] = "done", now()
    elif action == "snooze": t["due_at"] = (dt(t["due_at"]) + timedelta(days=1)).isoformat(timespec="seconds")
    elif action == "reschedule": t["due_at"] = body.due_at
    elif action == "reassign":
        if not can(user, "assign"): err("forbidden", "Only managers/outreach can reassign", 403)
        t["owner_id"] = body.owner_id
    db.put("tasks", tid, t); refresh_next(t["lead_id"]); audit(user, f"task.{action}", "task", tid); return t

def create_task(user, b):
    l = get_lead(user, b.lead_id); return make_task(l, b.title, b.type, b.due_at, b.owner_id or user["id"], b.priority, b.notes)

def list_activities(user, lead_id="", limit=100):
    if lead_id: get_lead(user, lead_id); ac = db.query("activities", [("lead_id", "==", lead_id)])
    elif user["role"] == "caller": ac = db.query("activities", [("user_id", "==", user["id"])])
    else: return db.query("activities", order_by="at", desc=True, limit=limit)      # single-field index, reads only `limit` docs
    return sorted(ac, key=lambda a: a["at"], reverse=True)[:limit]

def workspace_queue(user):
    """Daily calling queue: callbacks due > overdue > first contact > untouched. Priority is explained."""
    n = now(); items = {}
    own = {"owner_id": user["id"]} if user["role"] in ("caller", "outreach") else {}
    mine = [l for l in active_leads(user, suppressed=False, **own) if l["stage"] not in TERMINAL]
    byl = {l["id"]: l for l in mine}
    for t in list_tasks(user, "mine"):
        l = byl.get(t["lead_id"])
        if not l: continue
        if t["due_at"] <= n[:10] + "T23:59:59": items[l["id"]] = (0 if t["type"] == "callback" else 1, t["due_at"], l, f"{'Overdue' if t['due_at'] < n else 'Due today'}: {t['title']}")
    for l in mine:
        if l["id"] in items: continue
        if not l.get("last_activity_at"): items[l["id"]] = (2, l["created_at"], l, "Not contacted yet")
    out = sorted(items.values(), key=lambda x: (x[0], x[1]))[:60]
    return [{"lead": x[2], "reason": x[3], "rank": x[0]} for x in out]

# ----------------------------------------------------------------------------- imports
MAP_HINTS = {"first_name": ["first name", "firstname", "first", "given name"], "last_name": ["last name", "lastname", "last", "surname"],
             "email": ["email", "e-mail", "email address", "work email"], "phone": ["phone", "mobile", "phone number", "tel", "direct phone"],
             "company": ["company", "organization", "organisation", "account", "company name"], "title": ["title", "job title", "role", "position"],
             "linkedin_url": ["linkedin", "linkedin url", "person linkedin url", "profile url"], "website": ["website", "domain", "company website"],
             "location": ["location", "city"], "country": ["country", "country code"],
             "signal": ["signal", "buying signal", "trigger", "trigger event", "intent", "intent signal"], "signal_url": ["signal url", "signal source", "signal link", "source url"]}
ROW_CHUNK_BYTES = 400_000   # parsed rows live in importBatches/{id}/rows/{chunk}; each doc stays well under Firestore's 1 MB cap

def _rows_col(bid): return f"importBatches/{bid}/rows"

def save_rows(bid, rows):
    col, chunk, size, n = _rows_col(bid), [], 0, 0
    def flush():
        nonlocal chunk, size, n
        db.put(col, f"c{n:05d}", {"n": n, "rows": chunk}); n += 1; chunk, size = [], 0
    for r in rows:
        sz = len(json.dumps(r, ensure_ascii=False).encode("utf-8"))
        if chunk and size + sz > ROW_CHUNK_BYTES: flush()
        chunk.append(r); size += sz
    flush()                                             # always at least one chunk, so an empty file is still "found"

def load_rows(bid):
    ch = db.list(_rows_col(bid))
    return [r for c in sorted(ch, key=lambda c: c["n"]) for r in c["rows"]] if ch else None

def drop_rows(bid):
    for c in db.list(_rows_col(bid)): db.delete(_rows_col(bid), c["id"])

def parse_file(name, data):
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext == "csv":
        txt = data.decode("utf-8-sig", errors="replace"); rd = csv.DictReader(io.StringIO(txt))
        return [dict(r) for r in rd], list(rd.fieldnames or [])
    if ext == "xlsx":
        if not data[:2] == b"PK": err("bad_file", "File content is not a valid XLSX")
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True); ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows: return [], []
        hdr = [str(h or "").strip() for h in rows[0]]
        return [{hdr[i]: ("" if v is None else str(v)) for i, v in enumerate(r) if i < len(hdr)} for r in rows[1:] if any(c not in (None, "") for c in r)], hdr
    err("bad_file", "Only CSV and XLSX files are supported")

def suggest_mapping(cols):
    m = {}
    for c in cols:
        k = c.strip().lower()
        for f, hs in MAP_HINTS.items():
            if k == f or k in hs: m[c] = f; break
    return m

def start_import(user, name, data):
    mb = int(os.getenv("MAX_UPLOAD_MB", "5"))
    if len(data) > mb * 1024 * 1024: err("too_large", f"File exceeds {mb} MB", 413)
    rows, cols = parse_file(name, data)
    if len(rows) > 5000: err("too_many_rows", "Max 5,000 rows per import in this MVP", 413)
    bid = uid("imp"); save_rows(bid, rows)
    return db.put("importBatches", bid, {"file": name, "status": "uploaded", "columns": cols, "sample": rows[:5], "suggested_mapping": suggest_mapping(cols),
            "row_count": len(rows), "created": 0, "updated": 0, "skipped": 0, "invalid": 0, "created_ids": [], "by": user["id"], "by_name": user["name"], "at": now()})

def _mapped(row, mapping):
    r = {}
    for c, f in mapping.items():
        if f and f != "ignore": r[f] = (row.get(c) or "").strip()
    return r

def validate_row(r):
    e = []
    if not (r.get("first_name") or r.get("last_name")): e.append("Name is required")
    if not (r.get("email") or r.get("phone")): e.append("Email or phone is required")
    if r.get("email") and not EMAIL_RE.match(r["email"]): e.append("Invalid email format")
    if r.get("phone") and not (7 <= len(re.sub(r"\D", "", r["phone"])) <= 15): e.append("Invalid phone number")
    if r.get("country") and not re.match(r"^[A-Za-z]{2,3}$|^[A-Za-z ]{4,}$", r["country"]): e.append("Invalid country")
    return e

def preview_import(user, bid, opt):
    b = db.get("importBatches", bid); rows = load_rows(bid) if b else None
    if not b or rows is None: err("not_found", "Import not found or expired", 404)
    leads = active_leads(); errors, dups, ok, seen = [], [], 0, set()
    for i, row in enumerate(rows, start=2):
        r = _mapped(row, opt.mapping); ve = validate_row(r)
        if ve: errors.append({"row": i, "errors": ve, "data": r}); continue
        key = n_email(r.get("email")) or n_phone(r.get("phone"))
        if key and key in seen: errors.append({"row": i, "errors": ["Duplicate inside this file"], "data": r}); continue
        seen.add(key); d, conf, sig = find_dupe(r, leads)
        if d:
            diff = {f: {"old": d.get(f, ""), "new": r[f]} for f in FIELDS if r.get(f) and r[f] != d.get(f) and f not in d.get("verified_fields", [])}
            dups.append({"row": i, "existing_id": d["id"], "existing": d["full_name"], "confidence": conf, "signal": sig, "changes": diff})
        else: ok += 1
    b.update({"status": "previewed", "invalid": len(errors)}); db.put("importBatches", bid, b)
    return {"valid_new": ok, "duplicates": dups[:200], "duplicate_count": len(dups), "errors": errors[:500], "error_count": len(errors), "total": len(rows)}

def _import_target(user, campaign_id, channels, new_name, owner_id, default_name):
    """Where an import's leads go: the campaign (existing, or created/reused by name), the channels they are worked on (any mix of call,
    linkedin, email, shown in the matching Sales Activity tabs) and who owns them. Validated before anything is written."""
    chs = list(dict.fromkeys(channels or []))
    if any(c not in CH3 for c in chs): err("bad_channel", "Channels must be any of: call, linkedin, email", 422)
    camp = None
    if campaign_id:
        camp = db.get("campaigns", campaign_id)
        if not camp: err("bad_campaign", "Unknown campaign")
    elif (new_name or "").strip():
        if not can(user, "assign"): err("forbidden", "Creating a campaign needs the assign permission", 403)
        name = new_name.strip()[:80]; hit = db.query("campaigns", [("name", "==", name), ("archived", "==", False)], limit=1)
        camp = hit[0] if hit else db.put("campaigns", uid("camp"), {"name": name, "channel": chs[0] if len(chs) == 1 else "mixed", "color": CAMP_COLORS[len(db.list("campaigns")) % len(CAMP_COLORS)], "archived": False, "created_at": now(), "source": "import"})
        if not hit: audit(user, "campaign.create", "campaign", camp["id"], f"{name} (from import)")
    if owner_id:
        if not can(user, "assign"): err("forbidden", "Assigning an owner needs the assign permission", 403)
        o = db.get("users", owner_id)
        if not o or not o.get("active", True): err("bad_owner", "Unknown or inactive owner")
    return {"campaign_id": camp["id"] if camp else None, "channels": chs, "owner_id": owner_id or None}

def confirm_import(user, bid, opt):
    if not can(user, "import"): err("forbidden", "Not allowed", 403)
    b = db.get("importBatches", bid)
    if b and b["status"] == "done": err("already_done", "This import was already confirmed (no duplicate run)", 409)
    rows = load_rows(bid) if b else None
    if not b or rows is None: err("not_found", "Import not found or expired", 404)
    if opt.policy == "separate" and not opt.confirm_separate: err("confirm_required", "Importing duplicates as separate records needs explicit confirmation", 422)
    tgt = _import_target(user, opt.campaign_id, opt.channels, opt.new_campaign_name, opt.owner_id, "Upload")
    leads = active_leads(); c = u = s = bad = 0; ids = []
    for row in rows:
        r = _mapped(row, opt.mapping)
        if validate_row(r): bad += 1; continue
        r["source"] = opt.source; r.update(tgt); d, conf, sig = find_dupe(r, leads)
        if d and opt.policy == "skip": s += 1; continue
        if d and opt.policy == "update":
            for f in FIELDS:
                if r.get(f) and f not in d.get("verified_fields", []) and not d.get(f): d[f] = r[f]   # fill blanks only; never overwrite silently
            d["full_name"] = f"{d['first_name']} {d['last_name']}".strip(); save_lead(d); u += 1; continue
        l = new_lead(r, user, batch=bid); l["source_detail"] = b["file"]; save_lead(l); leads.append(l); ids.append(l["id"]); c += 1
    b.update({"status": "done", "created": c, "updated": u, "skipped": s, "invalid": bad, "created_ids": ids, "policy": opt.policy, "source": opt.source, "campaign_id": tgt["campaign_id"], "channels": tgt["channels"], "finished_at": now()})
    db.put("importBatches", bid, b); audit(user, "import.confirm", "import", bid, f"{c} created, {u} updated, {s} skipped, {bad} invalid"); drop_rows(bid)
    return b

def rollback_import(user, bid):
    if not can(user, "import"): err("forbidden", "Not allowed", 403)
    b = db.get("importBatches", bid)
    if not b or b["status"] != "done": err("not_found", "Nothing to roll back", 404)
    blockers = {a["lead_id"] for a in for_leads("activities", b["created_ids"])}
    if blockers: err("unsafe_rollback", f"{len(blockers)} imported lead(s) already have activity, rollback is unsafe", 409)
    ids = list(b["created_ids"]); camp_ids = {l.get("campaign_id") for l in db.query("leads", [("import_batch_id", "==", bid)]) if l.get("campaign_id")}
    for t in for_leads("tasks", ids): db.delete("tasks", t["id"])
    for s in for_leads("suggestions", ids): db.delete("suggestions", s["id"])       # open Today suggestions about leads that no longer exist
    for i in ids: db.delete("leads", i)
    gone = remove_empty_import_campaigns(camp_ids)                                   # the whole list goes: its campaign too, so it leaves every dropdown and is not re-synced
    b.update(status="rolled_back", removed_campaigns=gone); db.put("importBatches", bid, b)
    audit(user, "import.rollback", "import", bid, f"{len(ids)} leads removed" + (f", campaign removed: {', '.join(gone)}" if gone else "")); return b

REMOVABLE_CAMPAIGN_SOURCES = ("apollo_list", "apollo", "import")     # campaigns the system made from an import or sync. A campaign someone made by hand is never auto-removed.

def remove_empty_import_campaigns(cids=None):
    """Delete import- or sync-made campaigns that hold no leads any more (cids=None checks all of them). Returns the names removed."""
    cs = [db.get("campaigns", i) for i in cids] if cids is not None else db.list("campaigns"); gone = []
    for c in cs:
        if c and c.get("source") in REMOVABLE_CAMPAIGN_SOURCES and not db.query("leads", [("campaign_id", "==", c["id"])], limit=1): db.delete("campaigns", c["id"]); gone.append(c["name"])
    return gone

def csv_safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v   # formula-injection guard
def to_csv(rows, cols):
    o = io.StringIO(); w = csv.writer(o); w.writerow(cols)
    for r in rows: w.writerow([csv_safe(r.get(c, "")) for c in cols])
    return o.getvalue()

# ----------------------------------------------------------------------------- Apollo + enrichment (real API only; pending configuration without a key)
def apollo_configured(): return bool(os.getenv("APOLLO_API_KEY"))

_FN = ["Aarav", "Priya", "Rohan", "Sneha", "Kabir", "Ananya", "Vikram", "Meera", "Arjun", "Isha", "Liam", "Olivia", "Noah", "Emma", "Lucas", "Mia", "Ethan", "Sofia"]
_LN = ["Sharma", "Patel", "Reddy", "Nair", "Mehta", "Iyer", "Gupta", "Rao", "Smith", "Johnson", "Brown", "Garcia", "Miller", "Davis", "Wilson"]
_CO = ["Northwind Labs", "Bluepeak Systems", "Helio Retail", "Quanta Freight", "Lumen Health", "Crestline Capital", "Orbit Learning", "Vantage Foods", "Pixelforge", "Atlas Mobility"]
_TI = ["VP Sales", "Head of Growth", "Director of Operations", "CEO", "Marketing Manager", "Sales Director", "CTO", "Procurement Lead"]
_LOC = ["Bengaluru, IN", "Mumbai, IN", "Delhi, IN", "Austin, US", "London, UK", "Singapore, SG", "Dubai, AE"]

APOLLO_BASE = "https://api.apollo.io/api/v1"

def _apollo(path, params=None, body=None, method="POST"):
    """One Apollo call: x-api-key header, timeout, bounded retries, honours Retry-After. Failures become clean API errors."""
    for attempt in range(3):
        try:
            hdr = {"x-api-key": os.environ["APOLLO_API_KEY"], "Accept": "application/json", "Cache-Control": "no-cache"}
            r = httpx.post(APOLLO_BASE + path, params=params, json=body or {}, timeout=25, headers=hdr) if method == "POST" else httpx.get(APOLLO_BASE + path, params=params, timeout=25, headers=hdr)
        except httpx.HTTPError:
            if attempt == 2: err("apollo_unreachable", "Could not reach Apollo. Try again shortly.", 502)
            time.sleep(1 + attempt); continue
        if r.status_code == 429:
            try: wait = int(float(r.headers.get("Retry-After", "2")))
            except ValueError: wait = 2
            if attempt < 2 and wait <= 15: time.sleep(wait); continue          # short limit: wait and retry
            err("apollo_rate_limited", f"Apollo rate limit reached. Try again in about {max(wait, 1)} seconds (limits depend on your Apollo plan).", 429)
        if r.status_code in (401, 403): err("apollo_auth", "Apollo rejected the API key: it is wrong, or it has no access to this endpoint (some endpoints need a master API key). Check APOLLO_API_KEY.", 502)
        if r.status_code == 422: err("apollo_bad_request", "Apollo rejected the request parameters.", 422)
        if r.status_code >= 400: err("apollo_error", f"Apollo returned an error ({r.status_code}).", 502)
        return r.json()

def _ap_email(p):
    e = (p.get("email") or "").strip().lower()
    return "" if (not e or "not_unlocked" in e or e.startswith("email_not")) else e   # Apollo's locked-email placeholder

def _ap_person(p):
    org = p.get("organization") or {}
    last = p.get("last_name") or p.get("last_name_obfuscated") or ""
    loc = ", ".join(x for x in (p.get("city"), p.get("state"), p.get("country")) if x)
    return {"apollo_id": p.get("id") or "", "first_name": p.get("first_name") or "", "last_name": last, "title": p.get("title") or "",
            "company": org.get("name") or p.get("organization_name") or "", "location": loc, "email": _ap_email(p),
            "linkedin_url": p.get("linkedin_url") or "", "phone": "", "has_email": bool(p.get("has_email") or _ap_email(p)),
            "obfuscated": "*" in last}     # search hides the full last name; enrichment fills it in

def sync_apollo_campaigns(user):
    """Mirror Apollo's ACTIVE sequences into CRM campaigns (matched by apollo_id, so re-running never duplicates).
    New -> created; known -> renamed/unarchived; previously synced but no longer active in Apollo -> archived
    (leads keep their campaign_id). Only campaigns that carry an apollo_id are ever touched."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can sync campaigns", 403)
    if not apollo_configured(): err("apollo_not_configured", "Set APOLLO_API_KEY in .env first", 400)
    seqs, page = [], 1
    while page <= 20:                                              # hard cap: 20 pages x 100
        data = _apollo("/emailer_campaigns/search", params=[("page", page), ("per_page", 100)])
        got = data.get("emailer_campaigns") or data.get("sequences") or []
        seqs += got
        total_pages = (data.get("pagination") or {}).get("total_pages")
        if not got or len(got) < 100 or (total_pages and page >= total_pages): break
        page += 1
    active = [s for s in seqs if s.get("id") and s.get("active") is True and not s.get("archived")]
    created = updated = archived = 0; keep = set(); n = len(db.list("campaigns")); cid_by_seq = {}; name_by_seq = {}
    for s in active:
        keep.add(s["id"]); name = (s.get("name") or "Apollo sequence").strip(); name_by_seq[s["id"]] = name
        hit = db.query("campaigns", [("apollo_id", "==", s["id"])], limit=1)
        if hit:
            c = hit[0]; c.update({"name": name, "archived": False, "synced_at": now()}); db.put("campaigns", c["id"], c); updated += 1; cid_by_seq[s["id"]] = c["id"]
        else:
            cid_by_seq[s["id"]] = uid("camp")
            db.put("campaigns", cid_by_seq[s["id"]], {"name": name, "channel": "email", "color": CAMP_COLORS[(n + created) % len(CAMP_COLORS)], "archived": False,
                                              "created_at": now(), "apollo_id": s["id"], "source": "apollo", "synced_at": now()}); created += 1
    for c in db.query("campaigns", [("source", "==", "apollo"), ("archived", "==", False)]):
        if c.get("apollo_id") not in keep: c["archived"] = True; c["synced_at"] = now(); db.put("campaigns", c["id"], c); archived += 1
    note = None
    if seqs and not active: note = "Apollo returned sequences but none were marked active. If you expected some, check they are activated in Apollo."
    out = {"apollo_total": len(seqs), "active": len(active), "created": created, "updated": updated, "archived": archived, "note": note,
           "leads_created": 0, "leads_duplicate": 0, "leads_linked": 0, "contacts_scanned": 0, "truncated": False}
    if cid_by_seq:                       # leads come along with the campaigns, automatically
        try: out.update(_import_sequence_contacts(user, cid_by_seq, name_by_seq))
        except HTTPException as e: out["note"] = "Campaigns synced, but importing their contacts failed: " + str((e.detail or {}).get("message", "error"))
        try: out["emails"] = sync_apollo_emails(user)
        except HTTPException as e: out["emails"] = {"error": str((e.detail or {}).get("message", "error"))}
    try: out["lists"] = sync_apollo_lists(user)             # re-sync the Apollo call lists you chose before (none chosen: nothing happens)
    except HTTPException as e: out["lists"] = {"error": str((e.detail or {}).get("message", "error"))}
    try: out["companies"] = co.sync_apollo(user)             # and the company lists chosen before, so signals stay current
    except HTTPException as e: out["companies"] = {"error": str((e.detail or {}).get("message", "error"))}
    audit(user, "campaign.apollo_sync", "campaign", "", f"{len(seqs)} in Apollo, {len(active)} active: {created} created, {updated} updated, {archived} archived; leads {out['leads_created']} created, {out['leads_duplicate']} duplicates")
    return out

def _import_sequence_contacts(user, cid_by_seq, name_by_seq, max_pages=100):
    """Scan the account's saved Apollo contacts (contacts search has no sequence filter, but every contact lists the
    sequences it is in) and add those in a synced sequence as leads of that campaign. Idempotent: existing leads are
    skipped (matched on email / apollo person id / LinkedIn / phone), and only get the campaign if they have none.
    A contact in several synced sequences goes to the one it joined most recently. Apollo status is kept as a tag."""
    leads = active_leads(); bid = uid("imp"); ids = []; created = dup = linked = scanned = 0; page = 1; truncated = False
    while True:
        data = _apollo("/contacts/search", body={"page": page, "per_page": 100, "sort_by_field": "contact_created_at", "sort_ascending": True})
        people = data.get("contacts") or []; scanned += len(people)
        for p in people:
            ms = [m for m in (p.get("contact_campaign_statuses") or []) if m.get("emailer_campaign_id") in cid_by_seq]
            if not ms: continue
            m = max(ms, key=lambda x: x.get("added_at") or ""); cid = cid_by_seq[m["emailer_campaign_id"]]
            org = p.get("organization") or {}
            loc = p.get("present_raw_address") or ", ".join(x for x in (p.get("city"), p.get("state"), p.get("country")) if x)
            ph = p.get("sanitized_phone") or next((x.get("sanitized_number") or x.get("raw_number") for x in (p.get("phone_numbers") or []) if x.get("sanitized_number") or x.get("raw_number")), "")
            r = {"first_name": p.get("first_name") or "", "last_name": p.get("last_name") or "", "title": p.get("title") or "", "company": p.get("organization_name") or org.get("name") or "",
                 "email": _ap_email(p), "phone": ph or "", "linkedin_url": p.get("linkedin_url") or "", "location": loc or "", "apollo_id": p.get("person_id") or p.get("id") or "",
                 "source": "Apollo", "campaign_id": cid, "channels": ["email"], "apollo_contact_id": p.get("id") or "", "tags": [f"apollo-{m.get('status') or 'unknown'}"], "source_detail": f"Apollo sequence: {name_by_seq[m['emailer_campaign_id']]}"}
            if not (r["first_name"] or r["last_name"]): continue
            d, conf, sig = find_dupe(r, leads)
            if d:
                dup += 1
                if p.get("id") and not d.get("apollo_contact_id"): d["apollo_contact_id"] = p["id"]; save_lead(d)      # backfill, so emails can be matched
                if d.get("channels") and "email" not in d["channels"]: d["channels"] = d["channels"] + ["email"]; save_lead(d)
                if not d.get("campaign_id"): d["campaign_id"] = cid; save_lead(d); linked += 1
                continue
            l = new_lead(r, user, batch=bid); leads.append(l); ids.append(l["id"]); created += 1      # new_lead already saves: one write per lead
        tp = (data.get("pagination") or {}).get("total_pages")
        if not people or len(people) < 100 or (tp and page >= tp): break
        if page >= max_pages: truncated = True; break
        page += 1
    if created: db.put("importBatches", bid, {"file": "Apollo sequence sync", "status": "done", "columns": [], "sample": [], "row_count": scanned, "created": created, "updated": 0, "skipped": dup,
                                              "invalid": 0, "created_ids": ids, "by": user["id"], "by_name": user["name"], "at": now(), "source": "Apollo"})
    return {"leads_created": created, "leads_duplicate": dup, "leads_linked": linked, "contacts_scanned": scanned, "truncated": truncated}

EMAIL_REPLY_CLASSES = ["willing_to_meet", "follow_up_question", "person_referral", "out_of_office", "already_left_company_or_not_right_person", "not_interested", "unsubscribe", "none_of_the_above"]
REPLY_OUTCOME = {"willing_to_meet": "Meeting Requested", "not_interested": "Not Interested", "out_of_office": "No Response"}   # everything else is a plain "Replied"
EMAIL_MAX_PAGES = 100       # 100 emails per page: at most 10,000 emails per pass (Apollo's own display limit is 50,000)

def _apollo_emails(seq_ids, extra=None):
    """Outreach emails of the given sequences (GET /emailer_messages/search, 0 credits), optionally narrowed by Apollo's own status or reply filters."""
    out, page = [], 1
    while True:
        params = [("emailer_campaign_ids[]", i) for i in seq_ids] + list(extra or []) + [("page", page), ("per_page", 100)]
        got = (_apollo("/emailer_messages/search", params=params, method="GET") or {}).get("emailer_messages") or []; out += got
        if len(got) < 100: return out, False
        if page >= EMAIL_MAX_PAGES: return out, True
        page += 1

def sync_apollo_emails(user):
    """Append what happens to emails inside Apollo sequences to the matching leads' history (nothing about emails is logged by hand).
    One 'Sent' activity per delivered email (at its real send time, with subject and a body excerpt); 'Bounced' and 'Replied' (or the mapped outcome)
    when Apollo reports them, timed when the CRM first detects them because Apollo gives no time for those; opens and clicks are flags on the Sent
    activity, not activities. Idempotent: every activity id is derived from Apollo's message id. Never changes a lead's stage, creates tasks, or suppresses a lead."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can sync emails", 403)
    _need_apollo()
    seqs = [c["apollo_id"] for c in db.query("campaigns", [("source", "==", "apollo"), ("archived", "==", False)]) if c.get("apollo_id")]
    if not seqs: err("no_synced_sequences", "Sync campaigns from Apollo first: emails are matched to your synced sequences.", 409)
    msgs, trunc = _apollo_emails(seqs); flag = {}
    for key, extra in (("bounced", [("emailer_message_stats[]", "bounced"), ("emailer_message_stats[]", "spam_blocked")]), ("opened", [("emailer_message_stats[]", "opened")]),
                       ("clicked", [("emailer_message_stats[]", "clicked")]), ("replied", [("emailer_message_reply_classes[]", c) for c in EMAIL_REPLY_CLASSES])):
        got, t = _apollo_emails(seqs, extra); trunc = trunc or t; flag[key] = {m["id"]: m for m in got if m.get("id")}
    leads = active_leads(); by_email = {n_email(l["email"]): l for l in leads if l.get("email")}; by_cid = {l["apollo_contact_id"]: l for l in leads if l.get("apollo_contact_id")}
    made = Counter(); changed = {}; unmatched = set(); detected = now()
    def add(aid, l, m, outcome, at, notes, **extra):
        if db.get("activities", aid): return False
        db.put("activities", aid, {"lead_id": l["id"], "lead_name": l["full_name"], "user_id": l.get("owner_id") or "apollo", "user": "Apollo", "type": "email", "outcome": outcome, "notes": notes[:1000],
               "next_action": "", "occurred_at": at, "at": at, "follow_up_due": None, "duration_min": None, "external_ref": "apollo:" + m["id"], "source": "apollo", "apollo_message_id": m["id"], **extra})
        l["last_activity_at"] = max(l.get("last_activity_at") or "", at); changed[l["id"]] = l; return True
    for m in msgs:
        mid = m.get("id")
        if not mid: continue
        l = by_email.get(n_email(m.get("to_email"))) or by_cid.get(m.get("contact_id"))
        if not l: unmatched.add(mid); continue
        if m.get("completed_at"):
            at = dt(m["completed_at"]).isoformat(timespec="seconds"); aid = "act_apm_" + mid
            body = (m.get("body_text") or re.sub(r"<[^>]+>", " ", m.get("body_html") or "")).strip()
            if add(aid, l, m, "Sent", at, f"{m.get('subject') or '(no subject)'}\n\n{re.sub(r'\s+', ' ', body)[:500]}", subject=m.get("subject") or "", campaign_name=m.get("campaign_name") or "", step=m.get("campaign_position")):
                made["sent"] += 1; l["touches"] = l.get("touches", 0) + 1
            a = db.get("activities", aid); fl = {"opened": mid in flag["opened"] or mid in flag["clicked"], "clicked": mid in flag["clicked"]}
            if a and (a.get("opened"), a.get("clicked")) != (fl["opened"], fl["clicked"]): a.update(fl); db.put("activities", aid, a)
        if mid in flag["bounced"] and add("act_apb_" + mid, l, m, "Bounced", detected, "Email bounced or was blocked. Apollo does not say when, so this time is when the CRM detected it."): made["bounces"] += 1
        if mid in flag["replied"]:
            cls = flag["replied"][mid].get("reply_class") or "none_of_the_above"
            note = f"Reply received (Apollo category: {str(cls).replace('_', ' ')}). Apollo does not provide the reply text or time, so this time is when the CRM detected it." + (" The person unsubscribed: consider marking the lead Do Not Contact." if cls == "unsubscribe" else "")
            if add("act_apr_" + mid, l, m, REPLY_OUTCOME.get(cls, "Replied"), detected, note, reply_class=cls): made["replies"] += 1
    for l in changed.values(): save_lead(l)
    res = {"messages": len(msgs), "sent": made["sent"], "replies": made["replies"], "bounces": made["bounces"], "unmatched": len(unmatched), "leads_updated": len(changed), "truncated": trunc, "at": detected}
    db.put("config", "apollo_email_sync", res); audit(user, "email.apollo_sync", "activity", "", f"{made['sent']} sent, {made['replies']} replies, {made['bounces']} bounces, {len(unmatched)} unmatched")
    return res

SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager", "senior", "entry", "intern"]
PENDING_APOLLO = "Apollo is pending configuration. Set APOLLO_API_KEY in the server .env (see docs/INTEGRATIONS.md)."

def _need_apollo():
    if not apollo_configured(): err("apollo_not_configured", PENDING_APOLLO, 400)

def apollo_search(user, b):
    """Apollo People API Search (0 credits; no emails/phones; last names are masked until enrichment).
    Real API only: without APOLLO_API_KEY this says so instead of returning fake rows."""
    if not can(user, "import"): err("forbidden", "Not allowed", 403)
    _need_apollo()
    page, per = min(max(b.page, 1), 500), min(max(b.per_page, 1), 100)        # Apollo: up to 100 per page, 500 pages
    params = [("page", page), ("per_page", per)]
    if b.title.strip(): params.append(("person_titles[]", b.title.strip()))
    if b.location.strip(): params.append(("person_locations[]", b.location.strip()))
    if b.seniority:
        if b.seniority not in SENIORITIES: err("bad_seniority", "Unknown seniority", 422)
        params.append(("person_seniorities[]", b.seniority))
    kw = b.keywords.strip()
    for tok in [t.strip() for t in re.split(r"[,;\n]+", b.company) if t.strip()]:
        if "." in tok and " " not in tok: params.append(("q_organization_domains_list[]", re.sub(r"^https?://(www\.)?|/.*$", "", tok.lower())))
        else: kw = f"{kw} {tok}".strip()
    if kw: params.append(("q_keywords", kw))
    data = _apollo("/mixed_people/api_search", params=params)
    res = [_ap_person(p) for p in (data.get("people") or [])]
    leads = active_leads()
    for r in res:
        d, conf, sig = find_dupe(r, leads); r["duplicate_of"] = d["full_name"] if d else None
    total = data.get("total_entries") if data.get("total_entries") is not None else (data.get("pagination") or {}).get("total_entries")
    has_more = bool(res) and page < 500 and ((page * per < min(total, 50000)) if isinstance(total, int) else len(res) == per)
    return {"results": res, "page": page, "per_page": per, "total": total, "has_more": has_more, "demo": False,
            "note": "Live Apollo results. Emails and phones are added by Enrichment (uses Apollo credits)." if res else "Apollo returned no people for these filters."}

def apollo_import(user, b):
    if not can(user, "import"): err("forbidden", "Not allowed", 403)
    tgt = _import_target(user, b.campaign_id, b.channels, b.new_campaign_name, b.owner_id, "Apollo search")
    c = s = 0; leads = active_leads(); bid = uid("imp"); ids = []
    for r in b.records[:100]:
        r = {k: v for k, v in r.items() if k not in ("duplicate_of", "has_email", "obfuscated")}; r["source"] = "Apollo"; r.update(tgt); d, conf, sig = find_dupe(r, leads)
        if d: s += 1; continue
        r["source_detail"] = "Apollo search"; l = new_lead(r, user, batch=bid); leads.append(l); ids.append(l["id"]); c += 1
    batch = db.put("importBatches", bid, {"file": "Apollo search import", "status": "done", "columns": [], "sample": [], "row_count": len(b.records), "created": c, "updated": 0, "skipped": s, "invalid": 0, "created_ids": ids, "by": user["id"], "by_name": user["name"], "at": now(), "source": "Apollo"})
    audit(user, "import.apollo", "import", bid, f"{c} created, {s} skipped"); return batch

# ----------------------------------------------------------------------------- enrichment (Apollo bulk people enrichment, bounded batches)
ENRICH_MAX_JOB = 500      # leads per job: keeps the job document small and the run bounded; "Enrich all" reports what is left
ENRICH_BATCH = 10         # Apollo bulk_match accepts up to 10 people per call
STALE_JOB_MIN = 30        # a job "running" longer than this was interrupted (e.g. server restart) and stops blocking new jobs
CREDIT_NOTE = ("Apollo charges credits only for people it matches (Apollo's docs: 1 to 9 credits per person, 1 for an email, more if a mobile number is returned; nothing when there is no match). "
               "This run does not request phone numbers. Existing and user-verified values are never overwritten.")

def _site_domain(w): return re.sub(r"^https?://(www\.)?|/.*$", "", (w or "").strip().lower())

def _enrich_detail(l):
    """The identifiers we can send to Apollo for this lead, or None when there is not enough to search on."""
    if l.get("apollo_id") and not str(l["apollo_id"]).startswith("demo_"): return {"id": l["apollo_id"]}
    d = {"first_name": l.get("first_name"), "last_name": l.get("last_name"), "organization_name": l.get("company"), "linkedin_url": l.get("linkedin_url"),
         "email": l.get("email"), "domain": _site_domain(l.get("website"))}
    d = {k: v for k, v in d.items() if v and "*" not in str(v)}
    return d if (d.get("linkedin_url") or d.get("email") or (d.get("first_name") and (d.get("organization_name") or d.get("domain")))) else None

def _needs_enrichment(l): return not l.get("email") or not l.get("title") or not l.get("linkedin_url") or "*" in (l.get("last_name") or "")

def _active_jobs():
    stale = (datetime.now(timezone.utc) - timedelta(minutes=STALE_JOB_MIN)).isoformat(timespec="seconds"); out = []
    for j in db.query("enrichmentJobs", [("status", "in", ["queued", "running"])]):
        if j.get("at", "") < stale: j.update(status="failed", error="Interrupted (server restarted or job timed out)", finished_at=now()); db.put("enrichmentJobs", j["id"], j)
        else: out.append(j)
    return out

def enrich_candidates(user):
    """Leads that qualify for 'Enrich all': active, visible, not suppressed, never enriched, still missing data, and searchable."""
    ls = [l for l in active_leads(user, enrichment_state="not_enriched", suppressed=False) if visible(user, l) and _needs_enrichment(l) and _enrich_detail(l)]
    return sorted(ls, key=lambda l: l["created_at"], reverse=True)

def enrichment_preview(user):
    if not can(user, "enrich"): err("forbidden", "Not allowed", 403)
    c = enrich_candidates(user); n = len(c); run = min(n, ENRICH_MAX_JOB)
    return {"configured": apollo_configured(), "qualifying": n, "will_run": run, "remaining_after": n - run, "batch_size": ENRICH_BATCH, "api_calls": ceil(run / ENRICH_BATCH),
            "busy": bool(_active_jobs()), "credit_note": CREDIT_NOTE, "pending_note": None if apollo_configured() else PENDING_APOLLO}

def create_enrichment(user, ids, confirm, all_=False, refresh=False):
    if not can(user, "enrich"): err("forbidden", "Not allowed", 403)
    if not confirm: err("confirm_required", "Enrichment may consume Apollo credits. Confirm to continue.", 422)
    _need_apollo()
    active = _active_jobs()
    if all_:
        if active: err("job_running", "An enrichment job is already running. Wait for it to finish.", 409)
        leads = enrich_candidates(user)[:ENRICH_MAX_JOB]; ids = [l["id"] for l in leads]
        if not ids: err("nothing_to_enrich", "No leads qualify: they are already enriched, have all their data, or lack anything Apollo could search on.", 409)
    else:
        busy = {i for j in active for i in j["lead_ids"]}
        ids = [i for i in dict.fromkeys(ids) if i not in busy][:ENRICH_MAX_JOB]
        if not ids: err("duplicate_request", "These leads are already being enriched", 409)
        leads = [get_lead(user, i) for i in ids]
    return db.put("enrichmentJobs", uid("enr"), {"status": "queued", "scope": "all" if all_ else "selected", "refresh": bool(refresh), "lead_ids": ids, "total": len(ids), "processed": 0, "credits_used": 0,
                  "items": [{"lead_id": l["id"], "name": l["full_name"], "status": "pending"} for l in leads], "by": user["id"], "by_name": user["name"], "at": now(), "provider": "apollo", "demo": False})

def _apply_match(it, l, p, refresh=False):
    """Apply one Apollo match to a lead. Never marks a lead enriched without a usable match; never overwrites verified or existing values."""
    if not p or p.get("match_confidence") in ("none", "low"):
        it["status"] = "no_match"; l["enrichment_state"] = "no_match"; save_lead(l); return
    cur = _ap_person(p); res = {f: cur[f] for f in ("last_name", "email", "title", "linkedin_url", "location") if cur.get(f) and "*" not in str(cur[f])}
    if p.get("id") and not l.get("apollo_id"): res["apollo_id"] = p["id"]
    applied = {}
    for f, v in res.items():
        if f in l.get("verified_fields", []): continue                      # preserve user-verified values
        if l.get(f) and "*" not in str(l.get(f)) and not (refresh and l.get(f) != v): continue   # keep existing values; a refresh updates ones Apollo now reports differently (verified values are still kept)
        l["provenance"][f] = {"provider": "apollo", "at": now(), "prev": l.get(f, "")}; l[f] = v; applied[f] = v
    if applied: l["enrichment_state"], l["last_enriched_at"] = "enriched", now()
    else: l["enrichment_state"] = l["enrichment_state"] if l["enrichment_state"] == "enriched" else "no_new_data"
    l["full_name"] = f"{l['first_name']} {l['last_name']}".strip(); save_lead(l); it.update(status="enriched" if applied else "no_new_data", updated=list(applied))

def run_enrichment(jid):
    j = db.get("enrichmentJobs", jid)
    if not j: return
    j["status"] = "running"; db.put("enrichmentJobs", jid, j); todo = []
    for it in j["items"]:
        l = db.get("leads", it["lead_id"]); d = _enrich_detail(l) if l else None
        if not l: it.update(status="error", error="Lead no longer exists")
        elif not d: it.update(status="no_match", note="Not enough data to search (needs a LinkedIn URL, an email, or name + company)")
        else: todo.append((it, l, d))
    j["processed"] = len(j["items"]) - len(todo); db.put("enrichmentJobs", jid, j)
    pause = float(os.getenv("APOLLO_BULK_PAUSE", "0.3")); fatal = None
    for i in range(0, len(todo), ENRICH_BATCH):
        chunk = todo[i:i + ENRICH_BATCH]
        try:
            data = _apollo("/people/bulk_match", params={"reveal_personal_emails": "false"}, body={"details": [d for _, _, d in chunk]}) or {}
            matches = data.get("matches")
            if not isinstance(matches, list) or len(matches) != len(chunk): raise ValueError("Apollo returned an unexpected response")
            for (it, l, _), p in zip(chunk, matches):
                try: _apply_match(it, l, p, j.get("refresh"))
                except Exception as e: it.update(status="error", error=str(e)[:120])
            j["credits_used"] = round(j.get("credits_used", 0) + float(data.get("credits_consumed") or 0), 2)
        except HTTPException as e:
            msg = str((e.detail or {}).get("message", "error"))[:160]
            for it, _, _ in chunk: it.update(status="error", error=msg)
            if (e.detail or {}).get("code") in ("apollo_auth", "apollo_rate_limited"): fatal = msg; j["processed"] += len(chunk); db.put("enrichmentJobs", jid, j); break
        except Exception as e:
            for it, _, _ in chunk: it.update(status="error", error=str(e)[:160])
        j["processed"] += len(chunk); db.put("enrichmentJobs", jid, j)
        if i + ENRICH_BATCH < len(todo) and pause: time.sleep(pause)
    for it in j["items"]:
        if it["status"] == "pending": it.update(status="error", error=f"Stopped: {fatal}" if fatal else "Not processed")
    c = Counter(i["status"] for i in j["items"]); j["counts"] = dict(c)
    j["status"] = "completed" if not c["error"] else "failed" if c["error"] == len(j["items"]) else "partial"
    if fatal: j["error"] = fatal
    j["finished_at"] = now(); db.put("enrichmentJobs", jid, j)

def retry_enrichment(user, jid):
    j = db.get("enrichmentJobs", jid)
    if not j: err("not_found", "Job not found", 404)
    bad = [i["lead_id"] for i in j["items"] if i["status"] in ("error", "no_match")]
    if not bad: err("nothing_to_retry", "No failed items", 400)
    return create_enrichment(user, bad, True)

# ----------------------------------------------------------------------------- integrations status + real connection tests
def integrations():
    fs = os.getenv("USE_FIRESTORE", "false").lower() == "true"; a = ai.provider(); out = [
        {"id": "firebase", "name": "Firebase Auth + Firestore", "status": "connected" if fs else "pending", "env": "USE_FIRESTORE, AUTH_DISABLED, FIREBASE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS",
         "note": "Live Firestore" if fs else "Pending configuration: demo in-memory data in use"},
        {"id": "apollo", "name": "Apollo", "status": "connected" if apollo_configured() else "pending", "env": "APOLLO_API_KEY",
         "note": "People search, bulk enrichment, sequence sync" if apollo_configured() else "Pending configuration: Apollo features are off until a key is set"}]
    for pid, label, env in (("gemini", "Google Gemini (AI assistant)", "GEMINI_API_KEY, GEMINI_MODEL"), ("groq", "Groq (AI assistant)", "GROQ_API_KEY, GROQ_MODEL")):
        key = bool(os.getenv("GEMINI_API_KEY" if pid == "gemini" else "GROQ_API_KEY"))
        out.append({"id": pid, "name": label, "status": "connected" if key else "pending", "env": f"{env}, AI_PROVIDER={pid}",
                    "note": ("Active AI provider" if a == pid else f"Key set, but AI_PROVIDER is '{a}'. Set AI_PROVIDER={pid} to use it") if key else "Pending configuration: rule-based answers until a key is set"})
    return out

def test_integration(name):
    if name == "apollo":
        if not apollo_configured(): return {"ok": False, "message": PENDING_APOLLO}
        try: _apollo("/mixed_people/api_search", params=[("per_page", 1), ("person_titles[]", "CEO")])
        except HTTPException as e: return {"ok": False, "message": (e.detail or {}).get("message", "Apollo check failed")}
        return {"ok": True, "message": "Apollo key works (people search reachable)"}
    if name in ("gemini", "groq"):
        ok, msg = ai.test(name); return {"ok": ok, "message": msg}
    if name == "firebase":
        if os.getenv("USE_FIRESTORE", "false").lower() != "true": return {"ok": False, "message": "Pending configuration: USE_FIRESTORE is false, demo in-memory data in use"}
        try: db.query("config", limit=1)
        except Exception: return {"ok": False, "message": "Firestore is not reachable. Check the service account credentials."}
        return {"ok": True, "message": "Firestore reachable"}
    err("not_found", "Unknown integration", 404)

# ----------------------------------------------------------------------------- Apollo lists (call lists) + fit ranking + bulk assignment
def apollo_lists(user):
    """The team's Apollo contact lists (GET /labels, 0 credits). `count` is Apollo's cached_count and may be stale."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can manage lists", 403)
    _need_apollo()
    data = _apollo("/labels", method="GET"); items = data if isinstance(data, list) else ((data or {}).get("labels") or [])
    synced = {c.get("apollo_label_id") for c in db.query("campaigns", [("source", "==", "apollo_list")]) if not c.get("archived")}
    out = [{"id": x.get("id") or x.get("_id"), "name": x.get("name") or "", "count": x.get("cached_count") or 0, "synced": (x.get("id") or x.get("_id")) in synced}
           for x in items if x.get("modality") == "contacts" and (x.get("id") or x.get("_id"))]
    return sorted(out, key=lambda x: x["name"].lower())

def _contact_lead(p, cid, list_name):
    org = p.get("organization") or {}
    loc = p.get("present_raw_address") or ", ".join(x for x in (p.get("city"), p.get("state"), p.get("country")) if x)
    ph = p.get("sanitized_phone") or next((x.get("sanitized_number") or x.get("raw_number") for x in (p.get("phone_numbers") or []) if x.get("sanitized_number") or x.get("raw_number")), "")
    return {"first_name": p.get("first_name") or "", "last_name": p.get("last_name") or "", "title": p.get("title") or "", "company": p.get("organization_name") or org.get("name") or "",
            "email": _ap_email(p), "phone": ph or "", "linkedin_url": p.get("linkedin_url") or "", "location": loc or "", "apollo_id": p.get("person_id") or p.get("id") or "",
            "apollo_contact_id": p.get("id") or "", "source": "Apollo", "source_detail": f"Apollo list: {list_name}", "campaign_id": cid, "channels": ["call"]}

def sync_apollo_lists(user, label_ids=None, max_pages=100):
    """Bring selected Apollo lists in as CALL campaigns (a list has no sequence and no emails): one campaign per list, its contacts as unassigned leads
    on the Calls channel. Idempotent. With label_ids=None it re-syncs the lists already chosen before. Assigning the leads is done on the Assign page."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can sync lists", 403)
    _need_apollo()
    explicit = label_ids is not None
    if not explicit: label_ids = [c["apollo_label_id"] for c in db.query("campaigns", [("source", "==", "apollo_list"), ("archived", "==", False)]) if c.get("apollo_label_id")]
    empty = {"lists": 0, "leads_created": 0, "leads_duplicate": 0, "leads_linked": 0, "contacts_scanned": 0, "truncated": False}
    if not label_ids: return empty
    meta = {l["id"]: l for l in apollo_lists(user)}
    bad = [i for i in label_ids if i not in meta]
    if explicit and bad: err("bad_list", "One of those lists was not found in Apollo (or is not a people list).", 422)
    leads = active_leads(); bid = uid("imp"); ids = []; created = dup = linked = scanned = 0; trunc = False; n = len(db.list("campaigns")); done = 0; why = Counter()
    for lid in [i for i in label_ids if i in meta]:
        name = meta[lid]["name"]; hit = db.query("campaigns", [("apollo_label_id", "==", lid)], limit=1)
        if hit: camp = hit[0]; camp.update({"name": name, "archived": False, "synced_at": now()}); db.put("campaigns", camp["id"], camp)
        else: camp = db.put("campaigns", uid("camp"), {"name": name, "channel": "call", "color": CAMP_COLORS[(n + done) % len(CAMP_COLORS)], "archived": False, "created_at": now(), "source": "apollo_list", "apollo_label_id": lid, "synced_at": now()})
        done += 1; page = 1
        while True:
            data = _apollo("/contacts/search", body={"contact_label_ids": [lid], "page": page, "per_page": 100, "sort_by_field": "contact_created_at", "sort_ascending": True}) or {}
            people = data.get("contacts") or []; scanned += len(people)
            for p in people:
                r = _contact_lead(p, camp["id"], name)
                if not (r["first_name"] or r["last_name"]): continue
                d, conf, sig = find_dupe(r, leads)
                if d:
                    dup += 1; why[sig] += 1
                    if r["apollo_contact_id"] and not d.get("apollo_contact_id"): d["apollo_contact_id"] = r["apollo_contact_id"]; save_lead(d)
                    if d.get("channels") and "call" not in d["channels"]: d["channels"] = d["channels"] + ["call"]; save_lead(d)
                    if not d.get("campaign_id"): d["campaign_id"] = camp["id"]; save_lead(d); linked += 1
                    continue
                l = new_lead(r, user, batch=bid); leads.append(l); ids.append(l["id"]); created += 1
            tp = (data.get("pagination") or {}).get("total_pages")
            if not people or len(people) < 100 or (tp and page >= tp): break
            if page >= max_pages: trunc = True; break
            page += 1
    if created: db.put("importBatches", bid, {"file": "Apollo list sync", "status": "done", "columns": [], "sample": [], "row_count": scanned, "created": created, "updated": 0, "skipped": dup, "invalid": 0, "created_ids": ids, "by": user["id"], "by_name": user["name"], "at": now(), "source": "Apollo", "skipped_by": dict(why)})
    audit(user, "list.apollo_sync", "campaign", "", f"{done} lists, {created} leads created, {dup} already in CRM")
    return {"lists": done, "leads_created": created, "leads_duplicate": dup, "leads_linked": linked, "contacts_scanned": scanned, "truncated": trunc, "skipped_by": dict(why)}

# Fit ranking: transparent and deterministic. Every point is shown to the user. Edit the numbers here to change what "most fit" means.
SENIORITY_TIERS = [(25, r"\b(ceo|cto|cfo|coo|cmo|cro|chief|owner|founder|co-?founder|partner|president|managing director)\b"), (20, r"\b(vp|vice president|head)\b"), (20, r"\bdirector\b"), (10, r"\bmanager\b"), (5, r"\b(senior|lead|principal)\b")]
ASSIGNABLE = ("caller", "outreach")      # roles that can own and work leads
MAX_ASSIGN = 500                          # leads per assignment run

def fit_score(l):
    """0-100 and the reasons: a signal (35, +5 with a source link), seniority of the title (up to 25), a phone number (15, calls need one), email (5), company (5), LinkedIn (5)."""
    pts = []; sig = signal(l)
    if sig:
        pts.append((f"Signal: {sig[:60]}", 35))
        if (l.get("signal_url") or "").strip(): pts.append(("Signal has a source link", 5))
    t = (l.get("title") or "").lower()
    for p_, rx in SENIORITY_TIERS:
        if re.search(rx, t): pts.append((f"Senior title: {l['title']}", p_)); break
    if n_phone(l.get("phone")): pts.append(("Has a phone number", 15))
    if l.get("email"): pts.append(("Has an email", 5))
    if (l.get("company") or "").strip(): pts.append(("Company known", 5))
    if (l.get("linkedin_url") or "").strip(): pts.append(("Has a LinkedIn profile", 5))
    return min(100, sum(p for _, p in pts)), [{"label": a, "points": b} for a, b in pts]

def _pool_rows(user, f):
    f = f or {}; camps = {c["id"]: c for c in db.list("campaigns")}; eq = {"owner_id": None, "suppressed": False}
    if f.get("source"): eq["source"] = f["source"]
    if f.get("campaign"): eq["campaign_id"] = None if f["campaign"] == "none" else f["campaign"]
    ls = [l for l in active_leads(user, **eq) if l["stage"] not in TERMINAL]
    if f.get("batch"): ls = [l for l in ls if (l.get("import_batch_id") or "none") == f["batch"]]       # one import / sync batch ("none" = added manually)
    if f.get("channel"): ls = [l for l in ls if _in_channel(l, camps, f["channel"])]
    rows = []
    for l in ls:
        sc, why = fit_score(l)
        if sc < int(f.get("min_fit") or 0): continue
        if f.get("signal") and not signal(l): continue
        if f.get("phone") and not n_phone(l.get("phone")): continue
        rows.append((sc, l, why, camps))
    rows.sort(key=lambda r: (-r[0], r[1]["created_at"])); return rows

def assignment_pool(user, f=None, limit=200):
    """Unassigned, active, contactable leads ranked by fit (best first)."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can assign leads", 403)
    rows = _pool_rows(user, f)
    return {"total": len(rows), "items": [{"id": l["id"], "full_name": l["full_name"], "title": l.get("title", ""), "company": l.get("company", ""), "campaign_id": l.get("campaign_id"), "source": l["source"],
                                          "signal": signal(l), "signal_url": l.get("signal_url", ""), "has_phone": bool(n_phone(l.get("phone"))), "channels": lead_channels(l, camps),
                                          "fit": sc, "why": why, "created_at": l["created_at"]} for sc, l, why, camps in rows[:limit]]}

def assignees(user):
    """Who can take leads, with their current load (open leads, and how many of those were never contacted)."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can assign leads", 403)
    load, fresh = Counter(), Counter()
    for l in active_leads():
        if l.get("owner_id") and l["stage"] not in TERMINAL:
            load[l["owner_id"]] += 1
            if not l.get("last_activity_at"): fresh[l["owner_id"]] += 1
    return sorted([{"id": u["id"], "name": u["name"], "role": u["role"], "open_leads": load[u["id"]], "untouched": fresh[u["id"]]} for u in db.list("users") if u.get("active", True) and u["role"] in ASSIGNABLE], key=lambda u: u["name"].lower())

def intake_overview(user):
    """The admin's 'new data' view: what has arrived, what still needs an owner, grouped by import / sync batch so decisions are made per batch, not per lead."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can see the intake view", 403)
    camps = {c["id"]: c for c in db.list("campaigns")}; ls = active_leads()
    un = [l for l in ls if not l.get("owner_id") and not l.get("suppressed") and l["stage"] not in TERMINAL]; unid = {l["id"] for l in un}
    fit = {l["id"]: fit_score(l)[0] for l in un}; week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds"); by = defaultdict(list)
    for l in ls: by[l.get("import_batch_id") or ""].append(l)
    def row(bid, label, source, at, by_name, members, b=None):
        mu = [l for l in members if l["id"] in unid]; done = not mu; rev = (b or {}).get("reviewed_at")
        top = [camps[c]["name"] for c, _ in Counter(l.get("campaign_id") for l in members).most_common(3) if c in camps]
        return {"id": bid, "label": label, "source": source, "at": at, "by": by_name, "total": len(members), "unassigned": len(mu), "assigned": len(members) - len(mu),
                "with_signal": sum(bool(signal(l)) for l in mu), "with_phone": sum(bool(n_phone(l.get("phone"))) for l in mu),
                "avg_fit": round(sum(fit[l["id"]] for l in mu) / len(mu)) if mu else 0, "campaigns": top, "reviewed_at": rev, "reviewed_by": (b or {}).get("reviewed_by_name"),
                "status": "done" if done else "reviewed" if rev else "new"}
    rows = []
    for b in db.query("importBatches", order_by="at", desc=True, limit=60):
        if b.get("status") != "done" or not by.get(b["id"]): continue
        rows.append(row(b["id"], b.get("file") or "Import", b.get("source") or "File upload", b["at"], b.get("by_name", ""), by[b["id"]], b))
    if any(l["id"] in unid for l in by.get("", [])): rows.append(row("none", "Added manually or earlier", "Manual", "", "", by[""]))
    rank = {"new": 0, "reviewed": 1, "done": 2}; rows.sort(key=lambda r: r["at"], reverse=True); rows.sort(key=lambda r: (rank[r["status"]], r["at"] == ""))      # to review first, newest first; manual row last
    return {"summary": {"unassigned": len(un), "new_7d": sum(l["created_at"] >= week for l in un), "with_signal": sum(bool(signal(l)) for l in un), "with_phone": sum(bool(n_phone(l.get("phone"))) for l in un),
                        "avg_fit": round(sum(fit.values()) / len(fit)) if fit else 0, "batches_to_review": sum(r["status"] == "new" and r["id"] != "none" for r in rows)}, "batches": rows, "people": assignees(user)}

def review_batch(user, bid):
    """Batch-level approval: the admin has looked at this batch. No per-lead sign-off is needed."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can review batches", 403)
    b = db.get("importBatches", bid)
    if not b or b.get("status") != "done": err("not_found", "Batch not found", 404)
    b.update(reviewed_at=now(), reviewed_by=user["id"], reviewed_by_name=user["name"]); db.put("importBatches", bid, b); audit(user, "import.review", "import", bid, b.get("file", "")); return b

def assign_run(user, people, mode, f, dry_run=True):
    """Hand the best-ranked unassigned leads to callers in fixed amounts. dry_run previews; otherwise it assigns (each lead gets the usual first-contact task and an audit entry).
    round_robin deals leads one at a time so everyone gets a similar mix of strong and weaker leads; top_first gives the first person the best N, the next person the next N."""
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can assign leads", 403)
    if mode not in ("round_robin", "top_first"): err("bad_mode", "Mode must be round_robin or top_first", 422)
    want = {}
    for p in people:
        u = db.get("users", p.user_id)
        if not u or not u.get("active", True) or u["role"] not in ASSIGNABLE: err("bad_assignee", "You can only assign to active callers or outreach users", 422)
        if p.count < 1 or p.count > MAX_ASSIGN: err("bad_count", f"Each amount must be between 1 and {MAX_ASSIGN}", 422)
        want[p.user_id] = want.get(p.user_id, 0) + p.count
    if not want: err("nobody", "Choose at least one person and an amount", 422)
    if sum(want.values()) > MAX_ASSIGN: err("too_many", f"At most {MAX_ASSIGN} leads per run", 422)
    rows = _pool_rows(user, f); pool = [(sc, l) for sc, l, _, _ in rows]; got = {u: [] for u in want}
    if mode == "top_first":
        i = 0
        for u, n in want.items(): got[u] = pool[i:i + n]; i += len(got[u])
    else:
        order = list(want); left = dict(want); k = 0
        for item in pool:
            if not any(left.values()): break
            while left[order[k % len(order)]] == 0: k += 1
            got[order[k % len(order)]].append(item); left[order[k % len(order)]] -= 1; k += 1
    users = {u: db.get("users", u) for u in want}
    plan = [{"user_id": u, "name": users[u]["name"], "role": users[u]["role"], "requested": want[u], "assigned": len(got[u]), "avg_fit": round(sum(s for s, _ in got[u]) / len(got[u])) if got[u] else 0,
             "sample": [{"name": l["full_name"], "company": l.get("company", ""), "fit": s} for s, l in got[u][:5]]} for u in want]
    short = sum(want.values()) - sum(len(v) for v in got.values())
    if dry_run: return {"dry_run": True, "pool": len(pool), "plan": plan, "shortfall": short}
    done = Counter()
    for u in want:
        for _, l in got[u]:
            try: assign(user, l["id"], u); done[u] += 1
            except HTTPException: pass
    for pl in plan: pl["assigned"] = done[pl["user_id"]]; pl.pop("sample")
    audit(user, "assign.bulk", "lead", "", f"{sum(done.values())} leads ({mode}): " + ", ".join(f"{users[u]['name']} {done[u]}" for u in want))
    return {"dry_run": False, "assigned": sum(done.values()), "pool": len(pool), "plan": plan, "shortfall": sum(want.values()) - sum(done.values())}

# ----------------------------------------------------------------------------- opportunities
def list_opps(user):
    o = db.query("opportunities", [("owner_id", "==", user["id"])] if user["role"] == "caller" else [])
    return sorted(o, key=lambda x: x["created_at"], reverse=True)

def create_opp(user, b):
    l = get_lead(user, b.lead_id)
    o = db.put("opportunities", uid("opp"), {"name": b.name, "lead_id": l["id"], "lead_name": l["full_name"], "company": l.get("company", ""), "value": b.value, "stage": b.stage, "owner_id": l.get("owner_id") or user["id"], "created_at": now(), "stage_history": [{"stage": b.stage, "at": now()}]})
    audit(user, "opp.create", "opportunity", o["id"]); return o

def patch_opp(user, oid, p):
    o = db.get("opportunities", oid)
    if not o or (user["role"] == "caller" and o.get("owner_id") != user["id"]): err("not_found", "Not found", 404)
    if p.stage and p.stage != o["stage"]:
        if p.stage not in OPP_STAGES: err("bad_stage", "Unknown stage")
        o["stage_history"].append({"stage": p.stage, "at": now()}); o["stage"] = p.stage; audit(user, "opp.stage", "opportunity", oid, p.stage)
    if p.value is not None: o["value"] = p.value
    if p.name: o["name"] = p.name
    return db.put("opportunities", oid, o)

# ----------------------------------------------------------------------------- analytics (deterministic; one definition shared by dashboard, reports, AI)
DEFS = {"total_leads": "All non-archived leads in scope (snapshot).", "new_leads": "Leads created in the selected period.",
        "ready": "Leads currently in stage Ready to Contact (snapshot).", "attempts": "Logged phone-call activities in the period.",
        "unique_contacted": "Distinct leads with at least one logged activity in the period.", "connected": f"Logged calls with outcome in {sorted(CONNECTED)}.",
        "due": "Open tasks due today (snapshot).", "overdue": "Open tasks past their due time (snapshot).",
        "qualified": "Leads that entered stage Qualified in the period.", "meetings": "Activities with outcome Meeting Scheduled in the period.",
        "touches": "Outbound activities (calls, LinkedIn, email) logged in the period.", "opps": "Opportunities created in the period.", "won": "Leads that entered Won in the period.", "lost": "Leads that entered Lost in the period.",
        "connect_rate": "Connected calls / call attempts."}

def period(days=30, start=None, end=None):
    e = dt(end) if end else datetime.now(timezone.utc); s = dt(start) if start else e - timedelta(days=int(days)); return s, e
def inp(ts, s, e): return bool(ts) and s <= dt(ts) < e

def scoped(user, f):
    eq = {}
    if f.get("owner"): eq["owner_id"] = None if f["owner"] == "unassigned" else f["owner"]
    if f.get("source"): eq["source"] = f["source"]
    if f.get("stage"): eq["stage"] = f["stage"]
    cs = _camp_set(f.get("campaign"))
    if len(cs) == 1: eq["campaign_id"] = next(iter(cs))
    ls = [l for l in active_leads(user, **eq) if visible(user, l)]
    if len(cs) > 1: ls = [l for l in ls if (l.get("campaign_id") or None) in cs]
    if f.get("channel"):
        camps = {c["id"]: c for c in db.list("campaigns")}; ls = [l for l in ls if _in_channel(l, camps, f["channel"])]
    ids = {l["id"] for l in ls}
    return ls, [a for a in db.list("activities") if a["lead_id"] in ids], [t for t in db.list("tasks") if t["lead_id"] in ids], [o for o in db.list("opportunities") if o["lead_id"] in ids]

def entered(l, stage, s, e): return any(h["stage"] == stage and inp(h["at"], s, e) for h in l["stage_history"])

def kpis(ls, ac, ts, op, s, e):
    calls = [a for a in ac if a["type"] == "phone_call" and inp(a["at"], s, e)]; n = now(); today = n[:10]
    con = [a for a in calls if a["outcome"] in CONNECTED]
    return {"total_leads": len(ls), "new_leads": sum(inp(l["created_at"], s, e) for l in ls), "ready": sum(l["stage"] == "Ready to Contact" for l in ls),
            "attempts": len(calls), "touches": sum(a["type"] in ("phone_call", "linkedin", "email") and inp(a["at"], s, e) for a in ac), "unique_contacted": len({a["lead_id"] for a in ac if inp(a["at"], s, e)}), "connected": len(con),
            "due": sum(t["status"] == "open" and t["due_at"][:10] == today for t in ts), "overdue": sum(t["status"] == "open" and t["due_at"] < n for t in ts),
            "qualified": sum(entered(l, "Qualified", s, e) for l in ls), "meetings": sum(a["outcome"] == "Meeting Scheduled" and inp(a["at"], s, e) for a in ac),
            "opps": sum(inp(o["created_at"], s, e) for o in op), "won": sum(entered(l, "Won", s, e) for l in ls), "lost": sum(entered(l, "Lost", s, e) for l in ls),
            "connect_rate": round(100 * len(con) / len(calls), 1) if calls else 0}

def overview(user, days=30, f=None):
    f = f or {}; s, e = period(days); ps = s - (e - s); ls, ac, ts, op = scoped(user, f)
    cur, prev = kpis(ls, ac, ts, op, s, e), kpis(ls, ac, ts, op, ps, s)
    out = {k: {"value": v, "previous": prev[k], "delta": round(v - prev[k], 1), "definition": DEFS[k]} for k, v in cur.items()}
    per_day = Counter(a["at"][:10] for a in ac if a["type"] == "phone_call" and inp(a["at"], s, e)); con_day = Counter(a["at"][:10] for a in ac if a["outcome"] in CONNECTED and inp(a["at"], s, e))
    series = [{"date": (s + timedelta(days=i + 1)).strftime("%Y-%m-%d")} for i in range(int(days))]
    for p in series: p["attempts"], p["connected"] = per_day.get(p["date"], 0), con_day.get(p["date"], 0)
    return {"period": {"start": s.isoformat(), "end": e.isoformat(), "days": int(days)}, "kpis": out, "series": series[-30:], "funnel": funnel(user, days, f)["stages"], "insights": insights(user, days, f)}

def funnel(user, days=30, f=None):
    f = f or {}; ls, *_ = scoped(user, f); st = settings()["stages"]; reached = Counter()
    for l in ls:
        for x in {h["stage"] for h in l["stage_history"]}: reached[x] += 1
    main = [x for x in st if x not in ("Lost", "Nurture")]; rows = []; prev = None
    for x in main:
        c = reached[x]; rows.append({"stage": x, "count": c, "conversion": round(100 * c / prev, 1) if prev else 100, "drop": (prev - c) if prev is not None else 0}); prev = c
    worst = max(rows[1:], key=lambda r: r["drop"], default=None)
    return {"stages": rows, "biggest_drop": worst, "lost": reached["Lost"], "nurture": reached["Nurture"], "definition": "Leads that ever reached each stage (from stage history); conversion = reached / previous stage."}

def sources_report(user, days=30, f=None):
    f = f or {}; ls, ac, *_ = scoped(user, f); con = {a["lead_id"] for a in ac if a["outcome"] in ENGAGED_ALL}; rows = []
    for src in sorted({l["source"] for l in ls}):
        g = [l for l in ls if l["source"] == src]; n = len(g)
        rows.append({"source": src, "leads": n, "contactable": sum(bool(l.get("phone") or l.get("email")) and not l["suppressed"] for l in g),
                     "connected": sum(l["id"] in con for l in g), "qualified": sum(any(h["stage"] == "Qualified" for h in l["stage_history"]) for l in g)})
        r = rows[-1]; r["connect_rate"] = round(100 * r["connected"] / n, 1) if n else 0; r["qualify_rate"] = round(100 * r["qualified"] / n, 1) if n else 0
    return {"rows": sorted(rows, key=lambda r: -r["leads"]), "definition": "Connected = lead has a logged connected outcome; Qualified = ever reached Qualified."}

def calls_report(user, days=30, f=None):
    f = f or {}; s, e = period(days); ls, ac, *_ = scoped(user, f); calls = [a for a in ac if a["type"] == "phone_call" and inp(a["at"], s, e)]
    oc = Counter(a["outcome"] for a in calls); n = len(calls)
    return {"attempts": n, "unique": len({a["lead_id"] for a in calls}), "connect_rate": round(100 * sum(a["outcome"] in CONNECTED for a in calls) / n, 1) if n else 0,
            "outcomes": [{"outcome": k, "count": v, "pct": round(100 * v / n, 1)} for k, v in oc.most_common()], "definition": DEFS["connected"]}

def team_report(user, days=30, f=None):
    f = f or {}; s, e = period(days); ls, ac, ts, op = scoped(user, f); users = {u["id"]: u for u in db.list("users")}; rows = []; n = now()
    for uid_, u in users.items():
        if u["role"] not in ("caller", "outreach"): continue
        a = [x for x in ac if x["user_id"] == uid_ and inp(x["at"], s, e)]; calls = [x for x in a if x["type"] == "phone_call"]
        mine = [l for l in ls if l.get("owner_id") == uid_]; ot = [t for t in ts if t.get("owner_id") == uid_ and t["status"] == "open"]
        rows.append({"user": u["name"], "role": u["role"], "owned_leads": len(mine), "activities": len(a), "attempts": len(calls), "unique": len({x["lead_id"] for x in a}),
                     "touches": sum(x["type"] in ("phone_call", "linkedin", "email") for x in a), "engaged": sum(x["outcome"] in ENGAGED.get(x["type"], set()) for x in a), "connected": sum(x["outcome"] in CONNECTED for x in calls), "attempts_per_lead": round(len(calls) / len(mine), 2) if mine else 0,
                     "open_tasks": len(ot), "overdue": sum(t["due_at"] < n for t in ot)})
    return {"rows": rows, "definition": "Per salesperson, same period; attempts_per_lead normalises for workload."}

def health_report(user, days=30, f=None):
    f = f or {}; ls, ac, ts, op = scoped(user, f); n = now(); sd = settings()["stale_days"]; cut = (datetime.now(timezone.utc) - timedelta(days=sd)).isoformat(); act = [l for l in ls if l["stage"] not in TERMINAL and not l["suppressed"]]
    def brief(g): return [{"id": l["id"], "name": l["full_name"], "company": l.get("company", ""), "stage": l["stage"], "owner_id": l.get("owner_id"), "last_activity_at": l.get("last_activity_at")} for l in g[:50]]
    st = [l for l in act if l.get("last_activity_at") and l["last_activity_at"] < cut]; un = [l for l in act if not l.get("last_activity_at")]
    od = [l for l in act if l.get("next_action_due") and l["next_action_due"] < n]; nn = [l for l in act if not l.get("next_action")]
    return {"stale": brief(st), "untouched": brief(un), "overdue": brief(od), "no_next_action": brief(nn), "counts": {"stale": len(st), "untouched": len(un), "overdue": len(od), "no_next_action": len(nn)}, "stale_days": sd,
            "definition": f"Stale = no activity for {sd}+ days; untouched = never contacted; both exclude Won/Lost/Nurture and Do Not Contact."}

def enrichment_report(user, days=30, f=None):
    js = db.list("enrichmentJobs"); items = [i for j in js for i in j["items"]]; c = Counter(i["status"] for i in items)
    return {"jobs": len(js), "items": len(items), "succeeded": c["enriched"], "no_new_data": c["no_new_data"], "no_match": c["no_match"], "errors": c["error"],
            "found_phone": sum("phone" in i.get("updated", []) for i in items), "found_email": sum("email" in i.get("updated", []) for i in items), "credit_usage": round(sum(j.get("credits_used") or 0 for j in js), 2) if js else None,
            "definition": "Credit usage is the sum of credits Apollo reports for each enrichment job."}

def insights(user, days=30, f=None):
    f = f or {}; s, e = period(days); ps = s - (e - s); ls, ac, ts, op = scoped(user, f); c, p = kpis(ls, ac, ts, op, s, e), kpis(ls, ac, ts, op, ps, s); out = []
    cov = lambda k, a, b: round(100 * k / a, 1) if a else 0
    if c["new_leads"] > p["new_leads"] * 1.15 and c["unique_contacted"] <= p["unique_contacted"]:
        out.append({"title": "Lead volume is up, first-contact coverage is not", "what": f"New leads rose {p['new_leads']} → {c['new_leads']} while unique prospects contacted went {p['unique_contacted']} → {c['unique_contacted']}.", "metric": "new_leads vs unique_contacted", "period": f"last {days}d vs previous {days}d", "why": "New leads may be going cold before anyone reaches them.", "action": "Rebalance assignments or add calling capacity."})
    if c["connect_rate"] < p["connect_rate"] - 5 and c["attempts"] >= 10:
        out.append({"title": "Connection rate dropped", "what": f"Connect rate fell {p['connect_rate']}% → {c['connect_rate']}%.", "metric": "connect_rate", "period": f"last {days}d vs previous {days}d", "why": "More attempts are not turning into conversations.", "action": "Review call timing and the quality of numbers from the weakest source."})
    if c["overdue"] > 0 and c["overdue"] >= c["due"]:
        out.append({"title": "Overdue follow-ups are piling up", "what": f"{c['overdue']} open tasks are overdue vs {c['due']} due today.", "metric": "overdue", "period": "snapshot (now)", "why": "Callbacks lose value quickly once missed.", "action": "Clear callbacks first or reassign from the longest queue."})
    for r in sources_report(user, days, f)["rows"]:
        if r["leads"] >= 8 and r["qualified"] == 0:
            out.append({"title": f"{r['source']} brings volume but no qualified leads", "what": f"{r['leads']} leads, {r['qualified']} qualified, {r['connect_rate']}% connected.", "metric": "qualify_rate by source", "period": "all time in scope", "why": "Effort on this source may not convert (correlation, not proven cause).", "action": "Tighten targeting for this source or verify data quality."}); break
    hr = health_report(user, days, f)["counts"]
    if hr["untouched"] > 0: out.append({"title": f"{hr['untouched']} leads have never been contacted", "what": f"{hr['untouched']} active leads have no logged activity.", "metric": "untouched", "period": "snapshot (now)", "why": "Speed to first contact drives connection rates.", "action": "Open the untouched list in Leads and assign owners."})
    return out[:6]

EXPORTS = {"funnel": lambda u, d, f: (funnel(u, d, f)["stages"], ["stage", "count", "conversion", "drop"]), "sources": lambda u, d, f: (sources_report(u, d, f)["rows"], ["source", "leads", "contactable", "connected", "qualified", "connect_rate", "qualify_rate"]),
           "team": lambda u, d, f: (team_report(u, d, f)["rows"], ["user", "role", "owned_leads", "activities", "attempts", "unique", "connected", "attempts_per_lead", "open_tasks", "overdue"]),
           "calls": lambda u, d, f: (calls_report(u, d, f)["outcomes"], ["outcome", "count", "pct"]),
           "leads": lambda u, d, f: (scoped(u, f or {})[0], ["full_name", "title", "company", "email", "phone", "source", "stage", "owner_id", "created_at"])}

# ----------------------------------------------------------------------------- AI assistant (deterministic tools; LLM only explains)
def _name(uid_): u = db.get("users", uid_) if uid_ else None; return u["name"] if u else "Unassigned"

EXPLAIN_SYS = ("You are the assistant inside a sales CRM. Answer ONLY from the facts provided. Never invent numbers, names, dates or outcomes. "
               "Be concise (at most 5 short sentences or a short list). If the facts are not enough, say what is missing. You cannot take actions and must never say you did.")
LEAD_SYS = ("You are the assistant inside a sales CRM, looking at ONE lead. Use ONLY the facts provided (CRM record and call transcripts). Never invent quotes, prices, dates, "
            "commitments or outcomes. If something is not in the facts, say it is not recorded. You cannot send messages or change data: any draft is for a person to review and send.")
LEAD_ASKS = re.compile(r"summar|objection|next step|follow.?up|draft|transcript|recap|what happened|call", re.I)
MAX_FACTS_CHARS = 14000     # hard cap on what is ever sent to the AI provider for one question

def llm_explain(facts_text, question=""):
    """Phrase facts the backend already calculated. Returns None when no provider is configured or the call fails,
    so the caller keeps its rule-based text. The LLM never calculates a KPI."""
    if not ai.configured(): return None
    try: return ai.complete(EXPLAIN_SYS, f"Question: {question[:300]}\n\nFacts calculated by the CRM:\n{facts_text[:6000]}", 450)
    except ai.AIError: return None

def lead_facts(user, l):
    """Text about ONE lead the signed-in user may see: CRM facts + stored call transcripts. Email and phone are left out. Length-capped."""
    acts = sorted(db.query("activities", [("lead_id", "==", l["id"])]), key=lambda a: a["at"], reverse=True)[:8]
    lines = [f"Lead: {l['full_name']}, {l.get('title') or 'title unknown'} at {l.get('company') or 'company unknown'}.", f"Stage: {l['stage']}. Source: {l['source']}. Touches: {l.get('touches', 0)}.",
             f"Signal: {l.get('signal') or 'none'}.", f"Next action: {l.get('next_action') or 'none'}.", "Recent activity:" if acts else "No logged activity."]
    lines += [f"- {a['at'][:10]} {a['type']}: {a['outcome']}" + (f" ({a['notes'][:200]})" if a.get("notes") else "") for a in acts]
    trs = sorted(db.query("transcripts", [("lead_id", "==", l["id"])]), key=lambda t: t.get("started_at") or "", reverse=True)
    budget = MAX_FACTS_CHARS - len("\n".join(lines)) - 200
    for t in trs[:3]:
        if budget < 500: break
        body = (t.get("text") or "")[:budget]; lines.append(f"\nCall transcript ({(t.get('started_at') or '')[:10]}):\n{body}"); budget -= len(body) + 80
    return "\n".join(lines), len(trs)

def _lead_task(msg):
    m = msg.lower()
    if "objection" in m: return "List the objections the prospect raised and the recommended next steps, as short bullet points."
    if "draft" in m or "follow-up" in m or "follow up" in m: return "Draft a short follow-up email (under 120 words). Do not claim it was sent and do not invent commitments, prices or dates."
    if "transcript" in m or "call" in m: return "Summarise the call transcript: what was discussed, the outcome, objections and next steps. If there is no transcript in the facts, say so."
    return "Summarise this lead in at most 6 short bullet points: who they are, where they are in the pipeline, what has happened so far, and the recommended next step."

def lead_ai(user, msg, lead_id):
    """Read-only AI answer about one lead. get_lead enforces record scoping; only this lead's facts are sent."""
    l = get_lead(user, lead_id); facts, ntr = lead_facts(user, l)
    def fallback(extra):
        r = _chat_rules(user, "summarise this lead", lead_id); r["answer"] += (f" {ntr} call transcript(s) are stored for this lead." if ntr else " No call transcript is stored for this lead.") + extra; return r
    if not ai.configured(): return fallback(" AI provider pending configuration: this is the rule-based summary, not an AI answer.")
    try: txt = ai.complete(LEAD_SYS, f"{_lead_task(msg)}\n\nUser question: {msg[:300]}\n\nFacts about this one lead:\n{facts}", 700)
    except ai.AIError as e: return fallback(f" (AI unavailable: {e})")
    return {"answer": txt, "period": "all time", "kind": "fact", "records": [{"id": l["id"], "name": l["full_name"]}], "proposed_action": None, "ai_phrased": True,
            "definition": "Written by the AI provider from this lead's CRM record and stored call transcripts only. Review before using. Nothing was sent or changed."}

def chat(user, msg, lead_id=None):
    if lead_id and LEAD_ASKS.search(msg): res = lead_ai(user, msg, lead_id)
    else:
        res = _chat_rules(user, msg, lead_id)
        if res.get("kind") != "help":
            facts = res["answer"] + (("\nRecords: " + "; ".join(f"{r.get('name', '')} {r.get('detail', '')}".strip() for r in res["records"][:10])) if res.get("records") else "")
            txt = llm_explain(facts, msg)
            if txt: res["answer"], res["ai_phrased"] = txt, True
    res["ai"] = ai.status(); return res

def _chat_rules(user, msg, lead_id=None):
    m = msg.lower(); days = 7 if "week" in m else 30; s, e = period(days); ls, ac, ts, op = scoped(user, {}); pl = f"last {days} days"; recs = []
    R = lambda **k: dict({"period": pl, "kind": "fact", "records": recs, "proposed_action": None}, **k)
    if lead_id or "summar" in m and "lead" in m:
        l = db.get("leads", lead_id) if lead_id else None
        if not l or not visible(user, l): return R(answer="Open a lead profile and ask again so I can summarise that record.", definition="")
        a = sorted(db.query("activities", [("lead_id", "==", l["id"])]), key=lambda x: x["at"], reverse=True)
        txt = f"{l['full_name']} ({l.get('title') or 'no title'} at {l.get('company') or 'unknown company'}) is in stage {l['stage']}, sourced from {l['source']}. {len(a)} logged activities; last: " + (f"{a[0]['outcome']} on {a[0]['at'][:10]}." if a else "none yet.") + f" Next action: {l.get('next_action') or 'none set'}."
        if not l.get("next_action") and l["stage"] not in TERMINAL: txt += " Recommendation: set a next action."
        return R(answer=txt, period="all time", definition="Summary from this lead's own record.", records=[{"id": l["id"], "name": l["full_name"]}])
    if "overdue" in m and ("who" in m or "salesperson" in m or "largest" in m or "queue" in m):
        c = Counter(t["owner_id"] for t in ts if t["status"] == "open" and t["due_at"] < now())
        if not c: return R(answer="No overdue follow-ups right now.", period="snapshot", definition=DEFS["overdue"])
        o, n = c.most_common(1)[0]; return R(answer=f"{_name(o)} has the largest overdue queue: {n} tasks. Team total overdue: {sum(c.values())}. Recommendation: clear callbacks first or reassign part of it.", period="snapshot (now)", definition=DEFS["overdue"])
    if "callback" in m or "due today" in m or "need attention" in m or "attention" in m:
        n = now(); due = [t for t in ts if t["status"] == "open" and t["due_at"][:10] <= n[:10]]
        if user["role"] == "caller": due = [t for t in due if t["owner_id"] == user["id"]]
        recs = [{"id": t["lead_id"], "name": t["lead_name"], "detail": f"{t['title']} ({'overdue' if t['due_at'] < n else 'today'})"} for t in sorted(due, key=lambda t: t["due_at"])[:10]]
        return R(answer=f"{len(due)} follow-ups are due today or overdue. Top of the list is below. These need attention first because missed callbacks lose value fast.", period="snapshot (now)", definition=DEFS["due"] + " " + DEFS["overdue"], records=recs,
                 proposed_action=_propose_untasked(user, ls) if not due else None)
    if "source" in m and any(k in m for k in ("strong", "rate", "best", "qualif", "connect")):
        r = sorted(sources_report(user, days)["rows"], key=lambda r: -r["connect_rate"]); top = r[0] if r else None
        return R(answer=("Strongest source by connection rate: %s (%s%% of %s leads), qualification %s%%. " % (top["source"], top["connect_rate"], top["leads"], top["qualify_rate"]) if top else "No source data yet. ") + "Small sources have noisy rates; read with volume in mind.", period="all time in scope", definition=sources_report(user)["definition"])
    if any(k in m for k in ("drop", "funnel", "pipeline")):
        f = funnel(user); d = f["biggest_drop"]; return R(answer=f"Biggest drop-off is before {d['stage']}: {d['drop']} leads did not progress (conversion {d['conversion']}% from the previous stage)." if d else "Not enough data.", period="all time in scope", definition=f["definition"])
    if any(k in m for k in ("compare", "changed", "previous", "vs")):
        o = overview(user, days)["kpis"]; ch = [f"{k.replace('_',' ')}: {v['previous']} → {v['value']}" for k, v in o.items() if k in ("new_leads", "attempts", "connected", "qualified", "unique_contacted") ]
        return R(answer="Compared with the previous period: " + "; ".join(ch) + ". This shows change, not cause.", period=f"{pl} vs previous {days}", definition="Same KPI definitions as the dashboard.")
    if "enrich" in m:
        r = enrichment_report(user); return R(answer=f"{r['jobs']} jobs, {r['items']} leads processed: {r['succeeded']} enriched, {r['no_match']} no match, {r['errors']} errors. Phones found: {r['found_phone']}, emails found: {r['found_email']}.", period="all time", definition=r["definition"])
    if "added" in m or "new lead" in m or "how many leads" in m:
        n = [l for l in ls if inp(l["created_at"], s, e)]; c = Counter(l["source"] for l in n)
        return R(answer=f"{len(n)} leads were added in the {pl}. By source: " + (", ".join(f"{k} {v}" for k, v in c.most_common()) or "none") + ".", definition=DEFS["new_leads"])
    if any(k in m for k in ("called", "connected", "qualified", "unique", "calls")):
        k = kpis(ls, ac, ts, op, s, e)
        return R(answer=f"In the {pl}: {k['attempts']} call attempts, {k['unique_contacted']} unique prospects contacted, {k['connected']} connected ({k['connect_rate']}%), {k['qualified']} qualified.", definition=DEFS["connected"] + " " + DEFS["qualified"])
    return R(answer="I can answer from live CRM data. Try: leads added this week, who has the largest overdue queue, which source connects best, biggest pipeline drop-off, what changed vs last period, or which leads need attention today.", period="", definition="", kind="help")

def _propose_untasked(user, ls):
    cand = [l for l in ls if l["stage"] not in TERMINAL and not l.get("next_action") and not l["suppressed"] and l.get("owner_id")][:25]
    if not cand: return None
    a = db.put("aiActions", uid("aia"), {"kind": "create_tasks", "lead_ids": [l["id"] for l in cand], "user_id": user["id"], "status": "proposed", "at": now()})
    return {"id": a["id"], "label": f"Create a 'Set next step' task for {len(cand)} leads with no next action", "count": len(cand)}

def confirm_ai_action(user, aid):
    a = db.get("aiActions", aid)
    if not a or a["user_id"] != user["id"] or a["status"] != "proposed": err("not_found", "Action not found or already handled", 404)
    n = 0
    for i in a["lead_ids"]:
        l = db.get("leads", i)
        if l and visible(user, l): make_task(l, "Set next step", "follow_up", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds"), l.get("owner_id"), idem=f"ai:{aid}:{i}"); n += 1
    a["status"] = "confirmed"; db.put("aiActions", aid, a); audit(user, "ai.action.confirm", "aiAction", aid, f"{n} tasks created"); return {"created": n}

# ----------------------------------------------------------------------------- campaigns
CAMP_COLORS = ["#5a5fe0", "#1fa971", "#f0a020", "#e5484d", "#2f9bf0", "#a548c7", "#14877f"]

def list_campaigns(user):
    cs = [c for c in db.list("campaigns") if not c.get("archived")]
    by = defaultdict(list)
    for l in active_leads(user):
        if visible(user, l): by[l.get("campaign_id") or "none"].append(l)
    out = []
    for c in cs:
        g = by.get(c["id"], [])
        if user["role"] == "caller" and not g: continue
        out.append({**c, "count": len(g), "stages": dict(Counter(l["stage"] for l in g)), "contacted": sum(bool(l.get("last_activity_at")) for l in g)})
    g = by.get("none", [])
    if g: out.append({"id": "none", "name": "No campaign", "channel": "mixed", "color": "#9aa0b4", "count": len(g), "stages": dict(Counter(l["stage"] for l in g)), "contacted": sum(bool(l.get("last_activity_at")) for l in g)})
    return sorted(out, key=lambda c: (c["id"] == "none", -c["count"]))

def create_campaign(user, b):
    if not can(user, "assign"): err("forbidden", "Only managers and outreach can create campaigns", 403)
    if b.channel not in CHANNELS: err("bad_channel", "Unknown channel")
    if not b.name.strip(): err("name_required", "Name is required", 422)
    n = len(db.list("campaigns")); c = db.put("campaigns", uid("camp"), {"name": b.name.strip(), "channel": b.channel, "color": b.color or CAMP_COLORS[n % len(CAMP_COLORS)], "archived": False, "created_at": now()})
    audit(user, "campaign.create", "campaign", c["id"], c["name"]); return c

def patch_campaign(user, cid, p):
    if not can(user, "assign"): err("forbidden", "Not allowed", 403)
    c = db.get("campaigns", cid)
    if not c: err("not_found", "Campaign not found", 404)
    for k, v in p.model_dump(exclude_none=True).items():
        if k == "channel" and v not in CHANNELS: err("bad_channel", "Unknown channel")
        c[k] = v
    audit(user, "campaign.update", "campaign", cid); return db.put("campaigns", cid, c)

# ----------------------------------------------------------------------------- demo seed
CAMPAIGNS = [("c_saas", "SaaS Founders · Cold Calling", "call", "#5a5fe0"), ("c_apollo", "Fintech VPs · Apollo Outbound", "call", "#2f9bf0"), ("c_linkedin", "RevOps Leaders · LinkedIn", "linkedin", "#0a8fd6"),
             ("c_email", "Retail Mid-market · Email", "email", "#f0a020"), ("c_event", "Event Follow-up · Multi-channel", "mixed", "#a548c7"), ("c_inbound", "Referrals & Inbound", "call", "#14877f")]

DEMO_SIGNALS = ["Hiring 4 sales roles (demo)", "Announced a funding round (demo)", "New VP of Sales appointed (demo)", "Opening a second office (demo)", "Launched a new product line (demo)"]   # demo data, labelled

def seed():
    if db.list("users"): return
    U = [("u_admin", "Meera Kapoor", "admin", "meera@demo.crm"), ("u_caller1", "Rahul Verma", "caller", "rahul@demo.crm"), ("u_caller2", "Sana Ali", "caller", "sana@demo.crm"),
         ("u_outreach", "Dev Malhotra", "outreach", "dev@demo.crm"), ("u_da", "Nisha Rao", "da", "nisha@demo.crm"), ("u_dev", "Karan Singh", "developer", "karan@demo.crm")]
    for i, n, r, e in U: db.put("users", i, {"name": n, "role": r, "email": e, "active": True, "demo": True})
    for cid, n, ch, col in CAMPAIGNS: db.put("campaigns", cid, {"name": n, "channel": ch, "color": col, "archived": False, "created_at": now(), "demo": True})
    chan = {c[0]: c[2] for c in CAMPAIGNS}; TYPE = {"call": "phone_call", "linkedin": "linkedin", "email": "email"}
    settings(); rnd = random.Random(7); sysu = {"id": "u_admin", "name": "Meera Kapoor", "role": "admin"}; nowd = datetime.now(timezone.utc); st = DEFAULT_STAGES
    w = [("New", 14), ("Ready to Contact", 12), ("Attempted Contact", 18), ("Connected", 12), ("Qualified", 8), ("Meeting Scheduled", 6), ("Opportunity", 5), ("Won", 3), ("Lost", 5), ("Nurture", 3)]
    srcs = ["Apollo"] * 4 + ["LinkedIn"] * 4 + ["Google Research"] * 2 + ["File Upload"] * 4 + ["Referral"] * 2 + ["Event", "Claude", "Manual"]
    camp_for = lambda src: rnd.choice(["c_saas", "c_apollo"]) if src == "Apollo" else "c_linkedin" if src == "LinkedIn" else "c_saas" if src == "Google Research" else rnd.choice(["c_email", "c_email", "c_event"]) if src == "File Upload" else "c_event" if src == "Event" else "c_inbound"
    def pick(ch, stage, j):
        if stage == "Meeting Scheduled" and j == 0: return "Meeting Scheduled"
        warm = stage in ("Connected", "Qualified", "Opportunity", "Won"); lost = stage in ("Lost", "Nurture")
        if ch == "linkedin": pool = ["Connection Accepted", "Replied", "Replied"] if warm else ["No Response", "Not Interested"] if lost else ["Connection Sent", "Connection Sent", "No Response"]
        elif ch == "email": pool = ["Replied", "Replied", "Meeting Requested"] if warm else ["No Response", "Not Interested"] if lost else ["Sent", "Sent", "No Response", "Bounced"]
        else: pool = ["Connected", "Interested", "Meeting Requested"] if warm else ["Not Interested", "Connected"] if lost else ["No Answer", "No Answer", "Busy", "Voicemail", "Call Back Requested"]
        return rnd.choice(pool)
    TT = {"call": ["Callback", "Retry call", "First contact"], "linkedin": ["Follow up on LinkedIn request", "Send first message"], "email": ["Follow up on email", "Send intro email"]}
    for k in range(120):
        created = nowd - timedelta(days=rnd.random() * 44, hours=rnd.random() * 8); fn, ln = rnd.choice(_FN), rnd.choice(_LN); co = rnd.choice(_CO) + (" " + rnd.choice(["Pvt", "Ltd", "Inc"]) if rnd.random() < .3 else "")
        stage = rnd.choices([a for a, _ in w], [b for _, b in w])[0]; owner = None if (stage == "New" and rnd.random() < .5) else rnd.choice(["u_caller1", "u_caller2", "u_caller1", "u_outreach"])
        src = rnd.choice(srcs); cid = camp_for(src); cch = chan[cid]
        d = {"first_name": fn, "last_name": ln, "title": rnd.choice(_TI), "company": co, "email": f"{fn}.{ln}{k}@{co.split()[0].lower()}.com".lower(), "phone": f"+91 9{rnd.randint(100000000, 999999999)}" if rnd.random() < .85 else "",
             "linkedin_url": f"https://linkedin.com/in/{fn}-{ln}-{k}".lower(), "location": rnd.choice(_LOC), "source": src, "website": co.split()[0].lower() + ".com", "campaign_id": cid, "signal": rnd.choice(DEMO_SIGNALS) if rnd.random() < .35 else ""}
        l = new_lead(d, sysu, demo=True, created=created.isoformat(timespec="seconds"), owner=owner); path = [x for x in st if x not in ("Lost", "Nurture")]
        idx = path.index(stage) if stage in path else (rnd.randint(2, 3) if stage in ("Lost", "Nurture") else 0); seq = path[1:idx + 1] + ([stage] if stage in ("Lost", "Nurture") else [])
        t = created
        for sname in seq: t = min(t + timedelta(hours=rnd.randint(4, 70)), nowd); l["stage_history"].append({"stage": sname, "at": t.isoformat(timespec="seconds"), "by": owner or "u_admin", "from": ""})
        l["stage"] = stage
        if st.index(stage) >= 2 and owner:
            t = created
            for j in range(rnd.randint(1, 4)):
                t = min(t + timedelta(hours=rnd.randint(5, 60)), nowd); ch = cch if cch != "mixed" else rnd.choice(["call", "linkedin", "email"]); oc = pick(ch, stage, j); ow = db.get("users", owner)
                db.put("activities", uid("act"), {"lead_id": l["id"], "lead_name": l["full_name"], "user_id": owner, "user": ow["name"], "type": TYPE[ch], "outcome": oc, "notes": "", "next_action": "", "occurred_at": t.isoformat(timespec="seconds"), "at": t.isoformat(timespec="seconds"), "follow_up_due": None, "duration_min": None, "external_ref": "", "demo": True})
                l["last_activity_at"] = t.isoformat(timespec="seconds"); l["touches"] += 1; l["attempts"] += 1 if ch == "call" else 0
        save_lead(l)
        if owner and stage not in TERMINAL and rnd.random() < .6:
            due = nowd + timedelta(days=rnd.randint(-4, 5), hours=rnd.randint(-5, 8)); make_task(l, rnd.choice(TT["call" if cch == "mixed" else cch]), "follow_up", due.isoformat(timespec="seconds"), owner)
        if stage in ("Opportunity", "Won"):
            o = create_opp({"id": "u_admin", "role": "admin", "name": "Meera Kapoor"}, type("B", (), {"lead_id": l["id"], "name": f"{co} deal", "value": rnd.choice([4000, 5500, 7500, 10000, 15200, 24000]), "stage": "Won" if stage == "Won" else rnd.choice(["Discovery", "Proposal", "Negotiation"])})); o["demo"] = True
    db.put("enrichmentJobs", "enr_demo", {"status": "completed", "lead_ids": [], "items": [{"lead_id": "x", "name": "demo", "status": "enriched", "updated": ["phone"]}, {"lead_id": "y", "name": "demo", "status": "no_match"}], "by": "u_outreach", "by_name": "Dev Malhotra", "at": now(), "provider": "apollo", "demo": True})