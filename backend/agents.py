"""Today command center. Rule-based "agents" PROPOSE work; nothing is applied without a person approving it.
Scoring and ranking are deterministic. Web/LLM is used only to FIND signals (with source links), never to decide.
Runs: manually (Run now), or by an external scheduler hitting POST /api/v1/cron/run 3x/day."""
import os, re, json, hashlib, random, threading
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from firebase import db, now, uid
import services as sv

SYSTEM = {"id": "system", "name": "Scheduler", "role": "admin"}
HOURS = [int(x) for x in os.getenv("SCHEDULE_HOURS", "8,13,18").split(",") if x.strip()]
CAP_PER_OWNER = 5            # max open suggestions per person: keeps the inbox readable
SIGNAL_LIMIT = 15            # max leads whose company is searched per run (cost control)
AGENTS = {"triage": "triage", "prep": "call prep", "scout": "signal scout", "cleaner": "data cleaner", "watcher": "stale watcher", "digest": "digest"}
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

# ----------------------------------------------------------------------------- signal providers
def provider(): return os.getenv("SIGNAL_PROVIDER", "demo").lower()     # demo | claude_web | off

def provider_info():
    p = provider()
    if p == "demo": return {"id": p, "label": "Demo signals (not real)", "ok": True, "demo": True, "note": "Set SIGNAL_PROVIDER=claude_web to search the real web."}
    if p == "claude_web":
        ok = bool(os.getenv("ANTHROPIC_API_KEY")); return {"id": p, "label": "Claude web search", "ok": ok, "demo": False, "note": None if ok else "Set ANTHROPIC_API_KEY"}
    return {"id": "off", "label": "Signals off", "ok": True, "demo": False, "note": None}

def _demo(l):
    rnd = random.Random(l["id"])
    if rnd.random() > .3: return []
    t = rnd.choice([("hiring", f"Hiring {rnd.randint(2, 6)} sales roles"), ("funding", "Announced a new funding round"), ("leadership", "New VP of Sales appointed"), ("expansion", "Opening a second office")])
    return [{"type": t[0], "summary": t[1], "url": "", "date": "", "source": "demo", "demo": True, "verified": False}]

def _claude_web(l):
    """Real web signals via Claude's web search tool. Sends ONLY the company name/website (no personal data).
    TODO(INTEGRATION): confirm model name + web_search tool version in current Anthropic docs, then test."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key or not l.get("company"): return []
    import httpx
    q = (f'Find public, recent (last 90 days) business signals for the company "{l["company"]}"' + (f' ({l["website"]})' if l.get("website") else "") +
         ". Useful: funding, hiring, leadership changes, launches, expansion, notable news. Reply with ONLY JSON: "
         '{"signals":[{"type":"funding|hiring|leadership|launch|expansion|news","summary":"one sentence","url":"source url","date":"YYYY-MM-DD or empty"}]}. '
         'Only include items you actually found with a real source URL. If nothing, reply {"signals":[]}.')
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       json={"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 1000, "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}], "messages": [{"role": "user", "content": q}]})
        r.raise_for_status(); txt = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", txt, re.S); data = json.loads(m.group(0)) if m else {}
    except Exception: return []                      # a failed lookup is "no signal", never a made-up one
    return [{"type": s.get("type", "news"), "summary": str(s["summary"])[:240], "url": s["url"], "date": s.get("date", ""), "source": "claude_web", "demo": False, "verified": False}
            for s in data.get("signals", [])[:3] if str(s.get("url", "")).startswith("http") and s.get("summary")]

def _generic_web(l):
    """TODO(INTEGRATION): plug any search API here (Serper, Brave, Bing...). Return the same dict shape as _claude_web."""
    return []

def refresh_signals(leads):
    p = provider(); fn = {"demo": _demo, "claude_web": _claude_web, "web": _generic_web}.get(p)
    if not fn: return 0
    used = 0; stale = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    for l in leads:
        if used >= SIGNAL_LIMIT: break
        if l.get("signals_checked_at") and l["signals_checked_at"] > stale: continue
        for s in fn(l):
            sid = "sig_" + hashlib.sha1((l["id"] + s["summary"]).encode()).hexdigest()[:10]   # idempotent
            db.put("signals", sid, {**s, "lead_id": l["id"], "company": l.get("company", ""), "fetched_at": now()})
        l["signals_checked_at"] = now(); db.put("leads", l["id"], l); used += 1
    return used

# ----------------------------------------------------------------------------- deterministic priority
def score(l, tasks, acts, sigs, n, nd):
    s, why = 0, []
    cb = [t for t in tasks if t["type"] == "callback" and t["due_at"][:10] <= n[:10]]; od = [t for t in tasks if t["due_at"] < n]
    if cb: s += 40; why.append("Callback due")
    elif od: s += 30; why.append(f"{len(od)} overdue follow-up(s)")
    if not l.get("last_activity_at"):
        age = (nd - sv.dt(l["created_at"])).days; s += min(25, 10 + age * 2); why.append(f"Never contacted ({age}d old)")
    last = acts[-1] if acts else None
    if last and last["outcome"] in ("Interested", "Meeting Requested", "Connected") and (nd - sv.dt(last["at"])).days <= 7: s += 20; why.append(f"Recent outcome: {last['outcome']}")
    if sigs: s += 20; why.append("Fresh signal")
    if l["stage"] in ("Connected", "Qualified", "Meeting Scheduled"): s += 10; why.append(f"Warm stage: {l['stage']}")
    if l.get("touches", 0) >= 4 and not any(a["outcome"] in sv.ENGAGED_ALL for a in acts): s -= 15; why.append("4+ touches without a response")
    return max(0, min(100, s)), why

def brief(l, acts, sigs, why, ch="call", pitch="", sender=""):
    """Deterministic drafts. The CRM never sends anything: a person reviews, then sends from their own phone/LinkedIn/inbox."""
    first = l.get("first_name") or l["full_name"]; co = l.get("company") or "your team"; last = acts[-1] if acts else None
    hook = sigs[0]["summary"].rstrip(".") if sigs else ""; src = (f" (source: {sigs[0]['url']})" if sigs and sigs[0].get("url") else " (demo signal)" if sigs else "")
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
    def sigs():
        cut = (nd - timedelta(days=30)).isoformat(); return g([s for s in db.list("signals") if s["fetched_at"] >= cut], "lead_id")
    def rank(sm): return sorted(((score(l, tasks.get(l["id"], []), acts.get(l["id"], []), sm.get(l["id"], []), n, nd), l) for l in live), key=lambda x: -x[0][0])
    ranked = rank(sigs()); checked = refresh_signals([l for _, l in ranked[:SIGNAL_LIMIT] if l.get("owner_id")]); sm = sigs(); ranked = rank(sm)
    old = db.list("suggestions"); blocked = set()
    for s in old:
        if s["status"] == "proposed" or (s["status"] == "dismissed" and s.get("decided_at", "") > (nd - timedelta(days=7)).isoformat()) or (s["status"] == "approved" and s.get("decided_at", "") > (nd - timedelta(days=1)).isoformat()): blocked.add(s["dedupe_key"])
    camps = {c["id"]: c for c in db.list("campaigns")}; users_by = {u["id"]: u for u in db.list("users")}; pitch = sv.settings()["pitch"]
    props = Counter(s.get("owner_id") or "mgr" for s in old if s["status"] == "proposed" and s["kind"] != "digest"); cands = []
    def add(kind, agent, l, title, body, ev, pri, action, owner, key=None, ch="ops"):
        key = key or f"{kind}:{l['id']}"
        if key in blocked: return
        blocked.add(key); cands.append({"kind": kind, "channel": ch, "agent": agent, "lead_id": l["id"] if l else None, "lead_name": l["full_name"] if l else "", "owner_id": owner, "title": title, "body": body, "evidence": ev, "priority": pri, "action": action, "dedupe_key": key})
    # call prep / triage / signal scout
    for (sc, why), l in ranked[:30]:
        if sc < 30 or not l.get("owner_id"): continue
        ls = sm.get(l["id"], []); ag = "scout" if ls else "triage" if any(w.startswith(("Callback", "overdue")) or "overdue" in w for w in why) else "prep"
        ev = [{"text": w} for w in why] + [{"text": s["summary"], "url": s.get("url"), "demo": s.get("demo"), "source": s["source"]} for s in ls[:3]]
        cc = camps.get(l.get("campaign_id"), {}).get("channel", "call"); cc = "call" if cc == "mixed" else cc; me = users_by.get(l["owner_id"], {}).get("name", "")
        nm = l["full_name"]; ttl = {"call": f"Call {nm} today", "linkedin": f"LinkedIn: message {nm}", "email": f"Email {nm}"}[cc]; tt = {"call": f"Call {nm}", "linkedin": f"Send LinkedIn note to {nm}", "email": f"Send email to {nm}"}[cc]
        add("call_brief", ag, l, ttl, brief(l, acts.get(l["id"], []), ls, why, cc, pitch, me), ev, sc, {"type": "create_task", "title": tt, "due_at": (nd + timedelta(hours=2)).isoformat(timespec="seconds")}, l["owner_id"], ch=cc)
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
            k = norm(l.get(field)); k and grp[k].append(l)
        for k, v in grp.items():
            if len(v) < 2: continue
            v.sort(key=lambda x: x["created_at"]); keep = v[0]
            for d in v[1:]:
                if d["id"] in seen: continue
                seen.add(d["id"]); add("archive_duplicate", "cleaner", d, f"Archive duplicate of {keep['full_name']}", f"{d['full_name']} shares the same {field} as {keep['full_name']} (created {keep['created_at'][:10]}). Archiving keeps history and can be reversed.", [{"text": f"Same {field}: {k}"}], 30, {"type": "archive"}, d.get("owner_id"))
    need = sorted([l for l in live if l.get("enrichment_state") == "not_enriched" and (not l.get("phone") or not l.get("email"))], key=lambda l: l["created_at"], reverse=True)[:10]
    if need: add("enrich", "cleaner", None, f"Enrich {len(need)} leads missing phone or email", "Leads: " + ", ".join(l["full_name"] for l in need) + ".\nThis may use provider credits. Existing and verified values are never overwritten.", [{"text": "Missing contact data"}], 22, {"type": "enrich", "lead_ids": [l["id"] for l in need]}, None, key="enrich:" + hashlib.sha1(",".join(sorted(l["id"] for l in need)).encode()).hexdigest()[:10])
    # triage: unassigned leads
    workers = [u for u in db.list("users") if u["role"] in ("caller", "outreach") and u.get("active", True)]; load = Counter(l.get("owner_id") for l in live)
    for l in sorted([l for l in live if not l.get("owner_id")], key=lambda l: l["created_at"])[:8]:
        if not workers: break
        w = min(workers, key=lambda u: load[u["id"]]); load[w["id"]] += 1; age = (nd - sv.dt(l["created_at"])).days
        add("assign", "triage", l, f"Assign {l['full_name']} to {w['name']}", f"{l['full_name']} has no owner. {w['name']} currently has the lightest queue ({load[w['id']] - 1} active leads).", [{"text": f"Unassigned for {age} days"}, {"text": f"{w['name']}: {load[w['id']] - 1} active leads"}], 35 + min(15, age), {"type": "assign", "owner_id": w["id"]}, None)
    # cap per owner, keep the best
    created = Counter(); dropped = 0
    for owner, items in _by_owner(cands).items():
        room = max(0, (CAP_PER_OWNER * (2 if owner == "mgr" else 1)) - props[owner]); items.sort(key=lambda c: -c["priority"]); dropped += max(0, len(items) - room)
        for c in items[:room]: db.put("suggestions", uid("sug"), {**c, "status": "proposed", "created_at": n, "run_slot": slot}); created[c["agent"]] += 1
    overdue = sum(t["due_at"] < n for ts in tasks.values() for t in ts); unassigned = sum(not l.get("owner_id") for l in live)
    db.put("suggestions", "sug_digest", {"kind": "digest", "channel": "ops", "agent": "digest", "lead_id": None, "lead_name": "", "owner_id": None, "title": "Run summary", "status": "proposed", "priority": 5, "created_at": n, "dedupe_key": "digest", "action": None, "evidence": [],
            "body": f"{len(live)} active leads reviewed. {sum(created.values())} new suggestions ({', '.join(f'{AGENTS[k]} {v}' for k, v in created.items()) or 'none'}).\n{overdue} overdue follow-ups, {unassigned} unassigned leads, {checked} companies checked for signals.\nNothing has been changed. Approve or dismiss each suggestion."})
    run = db.put("agentRuns", uid("run"), {"at": n, "slot": slot, "by": user["name"], "provider": provider(), "reviewed": len(live), "created": dict(created), "dropped_by_cap": dropped, "signals_checked": checked})
    for k, v in created.items(): event(k, f"proposed {v} item(s)")
    event("digest", f"Run finished ({slot}): {sum(created.values())} suggestions, {checked} companies checked")
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

# ----------------------------------------------------------------------------- Today view
def today(user):
    L = {l["id"]: l for l in db.list("leads")}; sg = g([s for s in db.list("signals") if s["fetched_at"] >= (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()], "lead_id")
    acts = g(sorted(db.list("activities"), key=lambda a: a["at"]), "lead_id"); items = []
    for s in sorted(db.list("suggestions"), key=lambda s: -s["priority"]):
        if s["status"] != "proposed" or (user["role"] == "caller" and s.get("owner_id") != user["id"]): continue
        l = L.get(s.get("lead_id")); d = dict(s)
        if l: d["lead"] = {k: l.get(k) for k in ("id", "full_name", "title", "company", "stage", "phone", "email", "source", "owner_id", "next_action", "next_action_due", "attempts", "touches", "campaign_id", "linkedin_url", "last_activity_at")}; d["signals"] = sg.get(l["id"], [])[:3]; d["recent"] = [{"outcome": a["outcome"], "at": a["at"], "notes": a.get("notes", "")} for a in acts.get(l["id"], [])[-3:]][::-1]
        if d["kind"] == "enrich": d["leads"] = [{"id": i, "name": L[i]["full_name"]} for i in s["action"]["lead_ids"] if i in L]
        items.append(d)
    ls, ac, ts, op = sv.scoped(user, {}); s7, e7 = sv.period(7); k = sv.kpis(ls, ac, ts, op, s7, e7); vis = {l["id"] for l in ls}
    mine = [s for s in db.list("suggestions") if sv.inp(s.get("decided_at"), s7, e7) and (user["role"] != "caller" or s.get("owner_id") == user["id"])]
    week = {"leads_added": k["new_leads"], "calls": k["attempts"], "connected": k["connected"], "unique_contacted": k["unique_contacted"], "meetings": k["meetings"],
            "tasks_done": sum(t["status"] == "done" and sv.inp(t.get("completed_at"), s7, e7) for t in ts), "signals_found": sum(sv.inp(s["fetched_at"], s7, e7) and s["lead_id"] in vis for s in db.list("signals")),
            "approved": sum(s["status"] == "approved" for s in mine), "dismissed": sum(s["status"] == "dismissed" for s in mine)}
    lanes = Counter(l["stage"] for l in ls); st = sv.settings()["stages"]
    feed = [e for e in sorted(db.list("agentEvents"), key=lambda e: e["at"], reverse=True) if user["role"] != "caller" or e.get("owner_id") in (None, user["id"])][:14]
    lr = last_run()
    return {"suggestions": items, "week": week, "lanes": [{"stage": s, "count": lanes[s]} for s in st], "feed": feed, "status": {"last_run": lr["at"] if lr else None, "last_by": lr["by"] if lr else None, "next_run": schedule(), "provider": provider_info(), "cap": CAP_PER_OWNER, "hours": sorted(HOURS)}}
