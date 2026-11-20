"""Today command center. Rule-based "agents" PROPOSE work; nothing is applied without a person approving it.
Scoring and ranking are deterministic: CRM data plus each lead's `signal` column (typed, imported or uploaded by a person). No web or AI lookups.
Runs: manually (Run now), or by an external scheduler hitting POST /api/v1/cron/run 3x/day."""
import os, re, json, hashlib, random, threading
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from firebase import db, now, uid
import services as sv
import companies as co

SYSTEM = {"id": "system", "name": "Scheduler", "role": "admin"}
HOURS = [int(x) for x in os.getenv("SCHEDULE_HOURS", "8,13,18").split(",") if x.strip()]
CAP_PER_OWNER = 5            # max open suggestions per person: keeps the inbox readable
AGENTS = {"triage": "triage", "prep": "call prep", "cleaner": "data cleaner", "watcher": "stale watcher", "digest": "digest"}
LOCK = threading.Lock()

def g(items, key):
    d = defaultdict(list)
    for i in items: d[i[key]].append(i)
    return d

def schedule():
    n = datetime.now().astimezone(); slots = [n.replace(hour=h, minute=0, second=0, microsecond=0) for h in sorted(HOURS)]
    return next((s for s in slots if s > n), slots[0] + timedelta(days=1)).isoformat(timespec="minutes")

def last_run():
    r = sorted(db.list("agentRuns"), key=lambda r: r["at"]); return r[-1] if r else None

def event(agent, text, owner=None, lead_id=None):
    db.put("agentEvents", uid("evt"), {"at": now(), "agent": agent, "text": text, "owner_id": owner, "lead_id": lead_id})

# ----------------------------------------------------------------------------- deterministic priority
def score(l, tasks, acts, n, nd):
    s, why = 0, []
    cb = [t for t in tasks if t["type"] == "callback" and t["due_at"][:10] <= n[:10]]; od = [t for t in tasks if t["due_at"] < n]
    if cb: s += 40; why.append("Callback due")
    elif od: s += 30; why.append(f"{len(od)} overdue follow-up(s)")
    if not l.get("last_activity_at"):
        age = (nd - sv.dt(l["created_at"])).days; s += min(25, 10 + age * 2); why.append(f"Never contacted ({age}d old)")
    last = acts[-1] if acts else None
    if last and last["outcome"] in ("Interested", "Meeting Requested", "Connected") and (nd - sv.dt(last["at"])).days <= 7: s += 20; why.append(f"Recent outcome: {last['outcome']}")
    sig = sv.signal(l)
    if sig: s += 25; why.append("Signal: " + sig[:90])
    if l["stage"] in ("Connected", "Qualified", "Meeting Scheduled"): s += 10; why.append(f"Warm stage: {l['stage']}")
    if l.get("touches", 0) >= 4 and not any(a["outcome"] in sv.ENGAGED_ALL for a in acts): s -= 15; why.append("4+ touches without a response")
    return max(0, min(100, s)), why

def brief(l, acts, why, ch="call", pitch="", sender=""):
    """Deterministic drafts. The CRM never sends anything: a person reviews, then sends from their own phone/LinkedIn/inbox."""
    first = l.get("first_name") or l["full_name"]; co = l.get("company") or "your team"; last = acts[-1] if acts else None
    hook = sv.signal(l).rstrip("."); src = f" (source: {l['signal_url']})" if hook and l.get("signal_url") else ""
    ctx = "Why now: " + "; ".join(why) + "."
    if ch == "linkedin":
        note = (f"Hi {first}, I came across {co} ({hook.lower()}). " if hook else f"Hi {first}, I work with teams like {co}. ") + f"We focus on {pitch}. Would be good to connect."
        return f"LinkedIn note (review, then send it yourself):\n\n{note[:295]}\n\n{ctx}" + (f"\nSignal{src}" if hook else "")
    if ch == "email":
        body = (f"I noticed {co} ({hook.lower()}). " if hook else f"I work with teams like {co}. ") + f"We focus on {pitch}."
        return f"Email draft (review, then send it yourself):\n\nSubject: {co}: quick idea\n\nHi {first},\n\n{body} Would a 15-minute call next week make sense?\n\nBest,\n{sender}\n\n{ctx}" + (f"\nSignal{src}" if hook else "")
    ln = [f"Call {l['full_name']} ({l.get('title') or 'role unknown'}, {co}).", ctx, (f"Last touch: {last['outcome']} on {last['at'][:10]}" + (f" ({last['notes'][:100]})" if last.get("notes") else "") + ".") if last else "Last touch: none yet."]
    ln.append(("Opener: " + hook + src + ".") if hook else "Opener: introduce yourself and ask how they handle this today.")
    ln.append("Goal: confirm the right contact, understand the need, agree a next step.")
    return "\n".join(ln)

# ----------------------------------------------------------------------------- run
def run_agents(user, slot="manual"):
    if not LOCK.acquire(blocking=False): sv.err("run_in_progress", "A run is already in progress", 409)
    try:
        lr = last_run()
        if slot == "manual" and lr and (datetime.now(timezone.utc) - sv.dt(lr["at"])).total_seconds() < 20: sv.err("too_soon", "A run just finished. Wait a few seconds.", 429)
        return _run(user, slot)
    finally: LOCK.release()

def _run(user, slot):
    n = now(); nd = datetime.now(timezone.utc); sd = sv.settings()["stale_days"]
    leads = [l for l in db.list("leads") if not l.get("archived")]; live = [l for l in leads if l["stage"] not in sv.TERMINAL and not l["suppressed"]]
    tasks = g([t for t in db.list("tasks") if t["status"] == "open"], "lead_id"); acts = g(sorted(db.list("activities"), key=lambda a: a["at"]), "lead_id")
    def rank(): return sorted(((score(l, tasks.get(l["id"], []), acts.get(l["id"], []), n, nd), l) for l in live), key=lambda x: (not sv.signal(x[1]), -x[0][0]))   # signal leads first, then by score
    ranked = rank()
    old = db.list("suggestions"); blocked = set()
    for s in old:
        if s["status"] == "proposed" or (s["status"] == "dismissed" and s.get("decided_at", "") > (nd - timedelta(days=7)).isoformat()) or (s["status"] == "approved" and s.get("decided_at", "") > (nd - timedelta(days=1)).isoformat()): blocked.add(s["dedupe_key"])
    camps = {c["id"]: c for c in db.list("campaigns")}; users_by = {u["id"]: u for u in db.list("users")}; pitch = sv.settings()["pitch"]
    props = Counter(s.get("owner_id") or "mgr" for s in old if s["status"] == "proposed" and s["kind"] != "digest"); cands = []
    def add(kind, agent, l, title, body, ev, pri, action, owner, key=None, ch="ops"):
        key = key or f"{kind}:{l['id']}"
        if key in blocked: return
        blocked.add(key); cands.append({"kind": kind, "channel": ch, "agent": agent, "lead_id": l["id"] if l else None, "lead_name": l["full_name"] if l else "", "owner_id": owner, "title": title, "body": body, "evidence": ev, "priority": pri, "action": action, "dedupe_key": key})
    # call prep / triage (leads with a signal rank first)
    for (sc, why), l in ranked[:30]:
        if sc < 30 or not l.get("owner_id"): continue
        ag = "triage" if any(w.startswith(("Callback", "overdue")) or "overdue" in w for w in why) else "prep"
        ev = [{"text": w} for w in why]
        chs = sv.lead_channels(l, camps); cc = next((c for c in ("call", "linkedin", "email") if c in chs), "call"); me = users_by.get(l["owner_id"], {}).get("name", "")
        nm = l["full_name"]; ttl = {"call": f"Call {nm} today", "linkedin": f"LinkedIn: message {nm}", "email": f"Email {nm}"}[cc]; tt = {"call": f"Call {nm}", "linkedin": f"Send LinkedIn note to {nm}", "email": f"Send email to {nm}"}[cc]
        add("call_brief", ag, l, ttl, brief(l, acts.get(l["id"], []), why, cc, pitch, me), ev, sc, {"type": "create_task", "title": tt, "due_at": (nd + timedelta(hours=2)).isoformat(timespec="seconds")}, l["owner_id"], ch=cc)
    # stale watcher
    cut = (nd - timedelta(days=sd)).isoformat(); cnt = 0
    for l in live:
        if cnt >= 20: break
        la = l.get("last_activity_at")
        if l.get("owner_id") and la and la < cut and not tasks.get(l["id"]):
            days = (nd - sv.dt(la)).days; ev = [{"text": f"No activity for {days} days (stale after {sd})"}]; cnt += 1
            if l.get("touches", 0) >= 4 and l["stage"] == "Attempted Contact" and days >= 2 * sd:
                add("stage_change", "watcher", l, f"Move {l['full_name']} to Nurture", f"{l.get('touches', 0)} touches, no response, {days} days quiet. Nurture keeps the record without more call effort.", ev + [{"text": f"{l.get('touches', 0)} outreach touches"}], 28 + min(20, days // 2), {"type": "stage", "stage": "Nurture", "reason": "Auto-suggested: no connection after repeated attempts"}, l["owner_id"])
            else:
                add("reengage", "watcher", l, f"Re-engage {l['full_name']}", f"{l['full_name']} at {l.get('company') or 'unknown'} has been quiet for {days} days in {l['stage']}. Create a follow-up task to try again.", ev, 25 + min(20, days // 2), {"type": "create_task", "title": f"Re-engage {l['full_name']}", "due_at": (nd + timedelta(days=1)).isoformat(timespec="seconds")}, l["owner_id"])
    # data cleaner: duplicates + enrichment
    seen = set()
    for field, norm in (("email", sv.n_email), ("phone", sv.n_phone)):
        grp = defaultdict(list)
        for l in leads:
            k = norm(l.get(field))
            if field == "phone" and k: k = k + "|" + (l.get("full_name") or "").lower()          # a shared switchboard number is not a duplicate: it must be the same name too
            k and grp[k].append(l)
        for k, v in grp.items():
            if len(v) < 2: continue
            v.sort(key=lambda x: x["created_at"]); keep = v[0]
            for d in v[1:]:
                if d["id"] in seen: continue
                seen.add(d["id"]); add("archive_duplicate", "cleaner", d, f"Archive duplicate of {keep['full_name']}", f"{d['full_name']} shares the same {field} as {keep['full_name']} (created {keep['created_at'][:10]}). Archiving keeps history and can be reversed.", [{"text": f"Same {field}: {k.split('|')[0]}"}], 30, {"type": "archive"}, d.get("owner_id"))
    need = sorted([l for l in live if l.get("enrichment_state") == "not_enriched" and (not l.get("phone") or not l.get("email"))], key=lambda l: l["created_at"], reverse=True)[:10]
    if need: add("enrich", "cleaner", None, f"Enrich {len(need)} leads missing phone or email", "Leads: " + ", ".join(l["full_name"] for l in need) + ".\nThis may use provider credits. Existing and verified values are never overwritten.", [{"text": "Missing contact data"}], 22, {"type": "enrich", "lead_ids": [l["id"] for l in need]}, None, key="enrich:" + hashlib.sha1(",".join(sorted(l["id"] for l in need)).encode()).hexdigest()[:10])
    # unassigned leads are handled in batches on the Intake / Assign pages, not as one suggestion per lead
    # cap per owner, keep the best
    created = Counter(); dropped = 0
    for owner, items in _by_owner(cands).items():
        room = max(0, (CAP_PER_OWNER * (2 if owner == "mgr" else 1)) - props[owner]); items.sort(key=lambda c: -c["priority"]); dropped += max(0, len(items) - room)
        for c in items[:room]: db.put("suggestions", uid("sug"), {**c, "status": "proposed", "created_at": n, "run_slot": slot}); created[c["agent"]] += 1
    overdue = sum(t["due_at"] < n for ts in tasks.values() for t in ts); unassigned = sum(not l.get("owner_id") for l in live)
    db.put("suggestions", "sug_digest", {"kind": "digest", "channel": "ops", "agent": "digest", "lead_id": None, "lead_name": "", "owner_id": None, "title": "Run summary", "status": "proposed", "priority": 5, "created_at": n, "dedupe_key": "digest", "action": None, "evidence": [],
            "body": f"{len(live)} active leads reviewed. {sum(created.values())} new suggestions ({', '.join(f'{AGENTS[k]} {v}' for k, v in created.items()) or 'none'}).\n{overdue} overdue follow-ups, {unassigned} unassigned leads, {sum(bool(sv.signal(l)) for l in live)} leads with a signal.\nNothing has been changed. Approve or dismiss each suggestion."})
    run = db.put("agentRuns", uid("run"), {"at": n, "slot": slot, "by": user["name"], "reviewed": len(live), "created": dict(created), "dropped_by_cap": dropped})
    for k, v in created.items(): event(k, f"proposed {v} item(s)")
    event("digest", f"Run finished ({slot}): {sum(created.values())} suggestions")
    sv.audit(user, "agent.run", "agentRun", run["id"], f"{sum(created.values())} suggestions"); return run

def _by_owner(c):
    d = defaultdict(list)
    for x in c: d[x["owner_id"] or "mgr"].append(x)
    return d

# ----------------------------------------------------------------------------- decisions (all human)
def _get(user, sid):
    s = db.get("suggestions", sid)
    if not s or s["status"] != "proposed" or (user["role"] == "caller" and s.get("owner_id") != user["id"]): sv.err("not_found", "Suggestion not found or already handled", 404)
    if not sv.can(user, "agents:act"): sv.err("forbidden", "Not allowed", 403)
    return s

def approve(user, sid, edits):
    s = _get(user, sid); a = s.get("action")
    if not a: sv.err("no_action", "This item is informational. Dismiss it to mark it read.", 400)
    job = None; t = a["type"]; title = (edits.title if edits and edits.title else a.get("title")); due = (edits.due_at if edits and edits.due_at else a.get("due_at")); body = edits.body if edits and edits.body else s["body"]
    def need(p):
        if not sv.can(user, p): sv.err("forbidden", "You cannot do this action", 403)
    if t == "create_task":
        need("tasks:write"); l = sv.get_lead(user, s["lead_id"]); sv.make_task(l, title, "follow_up", due, s.get("owner_id") or l.get("owner_id") or user["id"], notes=body, idem=f"sug:{sid}")
    elif t == "assign": need("assign"); sv.assign(user, s["lead_id"], a["owner_id"])
    elif t == "stage": need("leads:write"); sv.change_stage(user, s["lead_id"], a["stage"], a.get("reason", ""))
    elif t == "archive":
        need("assign"); l = db.get("leads", s["lead_id"]); l["archived"] = True; sv.save_lead(l); sv.audit(user, "lead.archive", "lead", l["id"], "approved duplicate archive")
    elif t == "enrich": need("enrich"); job = sv.create_enrichment(user, a["lead_ids"], True)
    s.update(status="approved", decided_at=now(), decided_by=user["id"], final_title=title); db.put("suggestions", sid, s)
    sv.audit(user, "agent.approve", "suggestion", sid, s["title"]); event(s["agent"], f"{user['name']} approved: {s['title']}", s.get("owner_id"), s.get("lead_id")); return s, job

def dismiss(user, sid, reason=""):
    s = _get(user, sid); s.update(status="dismissed", decided_at=now(), decided_by=user["id"], reason=reason[:200]); db.put("suggestions", sid, s)
    sv.audit(user, "agent.dismiss", "suggestion", sid, s["title"]); return s

# ----------------------------------------------------------------------------- My day (the caller's Today: a live work list, no approval queue)
LEAD_FIELDS = ("id", "first_name", "last_name", "full_name", "title", "company", "phone", "email", "linkedin_url", "stage", "signal", "signal_url", "campaign_id", "touches", "last_activity_at",
               "next_action", "next_action_due", "owner_id", "source", "created_at")
BRIEF_LIMIT = 40      # talking points are prepared for the first N rows only
COMPANY_LIMIT = 30    # full company brief is sent inline for the first N rows; the rest load on demand (keeps this response small and fast)

def my_day(user):
    """Every lead assigned to the user, in working order: callbacks due, follow-ups due or overdue, new leads not contacted yet (ranked by fit), then the rest
    of their assigned leads. Visibility is simply assignment (not channel, not whether Apollo already emailed them). Nothing waits for approval."""
    n = now(); end = n[:10] + "T23:59:59"
    leads = [l for l in sv.active_leads(user, owner_id=user["id"], suppressed=False) if l["stage"] not in sv.TERMINAL]; byid = {l["id"]: l for l in leads}
    tasks = g(sv.list_tasks(user, "mine", "open"), "lead_id"); camps = {c["id"]: c for c in db.list("campaigns")}; items = []
    for l in leads:
        ts = tasks.get(l["id"], []); cb = sorted((t for t in ts if t["type"] == "callback" and t["due_at"] <= end), key=lambda t: t["due_at"])
        fu = sorted((t for t in ts if t["type"] not in ("callback", "first_contact") and t["due_at"] <= end), key=lambda t: t["due_at"])     # the system's first-contact reminder just means "new lead"
        if cb: grp, rank, t = "callbacks", 0, cb[0]
        elif fu: grp, rank, t = "followups", 1, fu[0]
        elif not l.get("last_activity_at"): grp, rank, t = "new", 2, None
        else: grp, rank, t = "other", 3, None                      # assigned and already in progress: still hers, so still listed
        fit, why = sv.fit_score(l)
        items.append({"group": grp, "rank": rank, "due_at": t["due_at"] if t else None, "overdue": bool(t and t["due_at"] < n), "fit": fit, "fit_why": why,
                      "reason": "New lead, not contacted yet" if grp == "new" else f"Last activity {l['last_activity_at'][:10]}" if grp == "other" else f"{'Overdue' if t['due_at'] < n else 'Due today'}: {t['title']}",
                      "lead": {k: l.get(k) for k in LEAD_FIELDS} | {"channels": sv.lead_channels(l, camps)}})
    items.sort(key=lambda x: (x["rank"], x["due_at"] or "", -x["fit"], x["lead"]["created_at"]))
    for i, x in enumerate(items):
        c = co.for_lead(byid[x["lead"]["id"]]); x["has_company"] = bool(c)
        if i < COMPANY_LIMIT: x["company"] = co.brief(c) if c else None
    top = items[:BRIEF_LIMIT]; acts = g(sorted(sv.for_leads("activities", [x["lead"]["id"] for x in top]), key=lambda a: a["at"]), "lead_id"); pitch = sv.settings()["pitch"]
    for x in top:
        sig = [w["label"] for w in x["fit_why"] if w["label"].startswith("Signal:")]
        x["brief"] = brief(byid[x["lead"]["id"]], acts.get(x["lead"]["id"], []), [x["reason"]] + sig, "call", pitch, user["name"])
        x["recent"] = [{"type": a["type"], "outcome": a["outcome"], "at": a["at"], "notes": (a.get("notes") or "")[:160]} for a in acts.get(x["lead"]["id"], [])[-3:]][::-1]
    mine = [a for a in db.query("activities", [("user_id", "==", user["id"])]) if a["at"][:10] == n[:10]]; c = Counter(x["group"] for x in items)
    ls, ac, ts, op = sv.scoped(user, {}); s7, e7 = sv.period(7); k = sv.kpis(ls, ac, ts, op, s7, e7)
    return {"tiles": {"to_call": c["callbacks"] + c["followups"] + c["new"], "assigned": len(items), "other": c["other"], "callbacks": c["callbacks"], "followups": c["followups"], "new": c["new"], "done_today": len(mine), "connected_today": sum(a["type"] == "phone_call" and a["outcome"] in sv.CONNECTED for a in mine)},
            "week": {"calls": k["attempts"], "connected": k["connected"], "meetings": k["meetings"], "tasks_done": sum(t["status"] == "done" and sv.inp(t.get("completed_at"), s7, e7) for t in ts)},
            "items": items[:200], "total": len(items)}

# ----------------------------------------------------------------------------- Today view
def today(user):
    L = {l["id"]: l for l in db.list("leads")}
    acts = g(sorted(db.list("activities"), key=lambda a: a["at"]), "lead_id"); items = []
    for s in sorted(db.list("suggestions"), key=lambda s: (not (sv.signal(L[s["lead_id"]]) if s.get("lead_id") in L else ""), -s["priority"])):   # signal leads first
        if s["status"] != "proposed" or (user["role"] == "caller" and s.get("owner_id") != user["id"]): continue
        l = L.get(s.get("lead_id")); d = dict(s)
        if l: d["lead"] = {k: l.get(k) for k in ("id", "full_name", "title", "company", "stage", "phone", "email", "source", "owner_id", "next_action", "next_action_due", "attempts", "touches", "campaign_id", "linkedin_url", "last_activity_at", "signal", "signal_url")}; d["lead"]["signal"] = sv.signal(l); d["recent"] = [{"outcome": a["outcome"], "at": a["at"], "notes": a.get("notes", "")} for a in acts.get(l["id"], [])[-3:]][::-1]
        if d["kind"] == "enrich": d["leads"] = [{"id": i, "name": L[i]["full_name"]} for i in s["action"]["lead_ids"] if i in L]
        items.append(d)
    ls, ac, ts, op = sv.scoped(user, {}); s7, e7 = sv.period(7); k = sv.kpis(ls, ac, ts, op, s7, e7); vis = {l["id"] for l in ls}
    mine = [s for s in db.list("suggestions") if sv.inp(s.get("decided_at"), s7, e7) and (user["role"] != "caller" or s.get("owner_id") == user["id"])]
    week = {"leads_added": k["new_leads"], "calls": k["attempts"], "connected": k["connected"], "unique_contacted": k["unique_contacted"], "meetings": k["meetings"],
            "tasks_done": sum(t["status"] == "done" and sv.inp(t.get("completed_at"), s7, e7) for t in ts), "leads_with_signal": sum(bool(sv.signal(l)) and l["stage"] not in sv.TERMINAL for l in ls),
            "approved": sum(s["status"] == "approved" for s in mine), "dismissed": sum(s["status"] == "dismissed" for s in mine)}
    lanes = Counter(l["stage"] for l in ls); st = sv.settings()["stages"]
    feed = [e for e in sorted(db.list("agentEvents"), key=lambda e: e["at"], reverse=True) if user["role"] != "caller" or e.get("owner_id") in (None, user["id"])][:14]
    lr = last_run()
    return {"suggestions": items, "week": week, "lanes": [{"stage": s, "count": lanes[s]} for s in st], "feed": feed, "status": {"last_run": lr["at"] if lr else None, "last_by": lr["by"] if lr else None, "next_run": schedule(), "cap": CAP_PER_OWNER, "hours": sorted(HOURS)}}