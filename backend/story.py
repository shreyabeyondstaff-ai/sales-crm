"""Executive overview + Reports "story". Everything is computed from real CRM records (deterministic).
Gamification (XP, levels, streaks, badges) is derived only from logged activity and uses the visible formulas below."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from firebase import db, now
import services as sv

TYPES = ("phone_call", "linkedin", "email"); CH_LABEL = {"phone_call": "Calls", "linkedin": "LinkedIn", "email": "Email"}
XP_FORMULA = "XP = touches x1 + engaged x3 + meetings x15 + qualified leads x10 + won x50. Level = 1 + lifetime XP / 300."
BADGES = [("fast", "Fast starter", "⚡", "5+ owned leads created in the period and 80%+ contacted within 24 hours."), ("closer", "Closer", "🏆", "At least 1 owned lead won in the period."),
          ("fire", "On fire", "🔥", "Activity logged on 5+ consecutive days."), ("connector", "Connector", "🤝", "20+ touches with an engagement rate of 30% or more."),
          ("clean", "Clean desk", "✨", "10+ touches and zero overdue follow-ups.")]

def _eng(a): return a["outcome"] in sv.ENGAGED.get(a["type"], set())
def _touch(a): return a["type"] in TYPES
def pct(a, b): return round(100 * a / b, 1) if b else 0

def _series(items, key, start, size, n, pick=lambda x: True):
    out = [0] * n
    for x in items:
        if not x.get(key) or not pick(x): continue
        i = int((sv.dt(x[key]) - start).total_seconds() // (86400 * size))
        if 0 <= i < n: out[i] += 1
    return out

def channels_report(user, days=30, f=None):
    f = f or {}; days = int(days); s, e = sv.period(days); ps = s - (e - s); ls, ac, ts, op = sv.scoped(user, f); rows = []
    for t in TYPES:
        cur = [a for a in ac if a["type"] == t and sv.inp(a["at"], s, e)]; prev = [a for a in ac if a["type"] == t and sv.inp(a["at"], ps, s)]; eng = sum(_eng(a) for a in cur)
        rows.append({"channel": t, "label": CH_LABEL[t], "touches": len(cur), "prev_touches": len(prev), "engaged": eng, "rate": pct(eng, len(cur)), "unique": len({a["lead_id"] for a in cur})})
    return {"rows": rows, "definition": "Engaged = connected call, accepted LinkedIn request, reply, or meeting requested/scheduled on that channel."}

def campaigns_report(user, days=30, f=None):
    f = f or {}; s, e = sv.period(int(days)); ls, ac, ts, op = sv.scoped(user, f); camps = {c["id"]: c for c in db.list("campaigns")}; by = defaultdict(list); acl = defaultdict(list); rows = []
    for l in ls: by[l.get("campaign_id") or "none"].append(l)
    for a in ac: acl[a["lead_id"]].append(a)
    for cid, g in by.items():
        c = camps.get(cid, {"name": "No campaign", "channel": "mixed", "color": "#9aa0b4"}); n = len(g)
        contacted = sum(bool(acl.get(l["id"])) for l in g); eng = sum(any(_eng(a) for a in acl.get(l["id"], [])) for l in g)
        q = sum(any(h["stage"] == "Qualified" for h in l["stage_history"]) for l in g); w = sum(any(h["stage"] == "Won" for h in l["stage_history"]) for l in g)
        rows.append({"id": cid, "name": c["name"], "channel": c["channel"], "color": c["color"], "leads": n, "contacted": contacted, "contacted_pct": pct(contacted, n), "engaged": eng, "engaged_pct": pct(eng, n),
                     "qualified": q, "qualified_pct": pct(q, n), "won": w, "touches": sum(_touch(a) and sv.inp(a["at"], s, e) for l in g for a in acl.get(l["id"], []))})
    return {"rows": sorted(rows, key=lambda r: -r["leads"]), "definition": "Contacted = any logged activity. Engaged = a positive reply/connection/connect. Qualified and Won = ever reached that stage."}

def leaderboard(days=30):
    """Team stats (points only, no lead data), so everyone can see the table."""
    s, e = sv.period(int(days)); users = [u for u in db.list("users") if u["role"] in ("caller", "outreach") and u.get("active", True)]; today = datetime.now(timezone.utc).date(); n = now()
    acts = defaultdict(list); first = {}
    for a in db.list("activities"):
        acts[a["user_id"]].append(a)
        if a["lead_id"] not in first or a["at"] < first[a["lead_id"]]: first[a["lead_id"]] = a["at"]
    leads = [l for l in db.list("leads") if not l.get("archived")]; tasks = [t for t in db.list("tasks") if t["status"] == "open"]; rows = []
    def xp(mine, owned, win):
        tk = [a for a in mine if _touch(a) and (not win or sv.inp(a["at"], s, e))]; eng = sum(_eng(a) for a in tk); m = sum(a["outcome"] == "Meeting Scheduled" and (not win or sv.inp(a["at"], s, e)) for a in mine)
        ent = lambda st: sum(any(h["stage"] == st and (not win or sv.inp(h["at"], s, e)) for h in l["stage_history"]) for l in owned)
        return len(tk) + eng * 3 + m * 15 + ent("Qualified") * 10 + ent("Won") * 50, len(tk), eng, m, ent("Qualified"), ent("Won")
    for u in users:
        mine = acts[u["id"]]; owned = [l for l in leads if l.get("owner_id") == u["id"]]
        pxp, tk, eng, m, q, w = xp(mine, owned, True); axp = xp(mine, owned, False)[0]
        ds = {sv.dt(a["at"]).date() for a in mine if _touch(a) or a["type"] == "meeting"}; d = today if today in ds else today - timedelta(days=1); streak = 0
        while d in ds: streak += 1; d -= timedelta(days=1)
        newl = [l for l in owned if sv.inp(l["created_at"], s, e)]; fast = sum(l["id"] in first and sv.dt(first[l["id"]]) - sv.dt(l["created_at"]) <= timedelta(hours=24) for l in newl)
        od = sum(t.get("owner_id") == u["id"] and t["due_at"] < n for t in tasks)
        got = {"fast": len(newl) >= 5 and fast >= .8 * len(newl), "closer": w >= 1, "fire": streak >= 5, "connector": tk >= 20 and pct(eng, tk) >= 30, "clean": tk >= 10 and od == 0}
        rows.append({"id": u["id"], "name": u["name"], "role": u["role"], "xp": pxp, "touches": tk, "engaged": eng, "meetings": m, "qualified": q, "won": w, "streak": streak, "level": 1 + axp // 300, "level_pct": round((axp % 300) / 3), "overdue": od, "badges": [b[0] for b in BADGES if got[b[0]]]})
    rows.sort(key=lambda r: -r["xp"])
    for i, r in enumerate(rows): r["rank"] = i + 1
    return {"rows": rows, "badges": [{"id": b[0], "name": b[1], "icon": b[2], "rule": b[3]} for b in BADGES], "formula": XP_FORMULA}

def overview(user, days=30, f=None):
    f = f or {}; days = int(days); s, e = sv.period(days); ps = s - (e - s); ls, ac, ts, op = sv.scoped(user, f)
    cur, prev = sv.kpis(ls, ac, ts, op, s, e), sv.kpis(ls, ac, ts, op, ps, s); size = max(1, -(-days // 30)); n = -(-days // size); tk = [a for a in ac if _touch(a)]
    ser = {"labels": [(s + timedelta(days=size * i)).strftime("%d %b") for i in range(n)], "touches": _series(tk, "at", s, size, n), "engaged": _series(tk, "at", s, size, n, _eng), "prev_touches": _series(tk, "at", ps, size, n),
           "new_leads": _series(ls, "created_at", s, size, n), "meetings": _series(ac, "at", s, size, n, lambda a: a["outcome"] == "Meeting Scheduled")}
    kp = {k: {"value": v, "previous": prev[k], "delta": round(v - prev[k], 1), "pct": round(100 * (v - prev[k]) / prev[k]) if prev[k] else None, "definition": sv.DEFS[k]} for k, v in cur.items()}
    def eng_rate(x, y): z = [t for t in tk if sv.inp(t["at"], x, y)]; return pct(sum(_eng(t) for t in z), len(z))
    src = Counter(l["source"] for l in ls if sv.inp(l["created_at"], s, e)); now_ = now()
    over = sorted([t for t in ts if t["status"] == "open" and t["due_at"] < now_], key=lambda t: t["due_at"])[:6]
    hc = sv.health_report(user, days, f)["counts"]
    return {"period": {"days": days, "bucket_days": size}, "kpis": kp, "engagement": {"value": eng_rate(s, e), "previous": eng_rate(ps, s)}, "series": ser, "funnel": sv.funnel(user, days, f)["stages"], "sources": [{"source": k, "count": v} for k, v in src.most_common()],
            "channels": channels_report(user, days, f)["rows"], "campaigns": campaigns_report(user, days, f)["rows"],
            "money": {"open": sum(o["value"] for o in op if o["stage"] not in ("Won", "Lost")), "won": sum(o["value"] for o in op if o["stage"] == "Won")},
            "attention": {"counts": hc, "overdue": [{"lead_id": t["lead_id"], "lead_name": t["lead_name"], "title": t["title"], "due_at": t["due_at"], "owner_id": t.get("owner_id")} for t in over]}, "insights": sv.insights(user, days, f)}

def _dtxt(c, p): d = c - p; return "flat against the previous period" if d == 0 else f"{'up' if d > 0 else 'down'} {abs(d)} against the previous period"

def story(user, days=30, f=None):
    f = f or {}; days = int(days); s, e = sv.period(days); ps = s - (e - s); ls, ac, ts, op = sv.scoped(user, f)
    cur, prev = sv.kpis(ls, ac, ts, op, s, e), sv.kpis(ls, ac, ts, op, ps, s); ch = channels_report(user, days, f)["rows"]; camp = campaigns_report(user, days, f)["rows"]
    fun = sv.funnel(user, days, f); hr = sv.health_report(user, days, f); sd = sv.settings(); tk = [a for a in ac if _touch(a) and sv.inp(a["at"], s, e)]
    eng = sum(_eng(a) for a in tk); active = [l for l in ls if l["stage"] not in sv.TERMINAL and not l["suppressed"]]; touched = {a["lead_id"] for a in tk}
    coverage = pct(sum(l["id"] in touched for l in active), len(active)); src = Counter(l["source"] for l in ls if sv.inp(l["created_at"], s, e)); top = src.most_common(1)
    best_ch = max(ch, key=lambda r: r["rate"] if r["touches"] >= 5 else -1, default=None); bc = [c for c in camp if c["leads"] >= 5]; best_c = max(bc, key=lambda c: c["engaged_pct"], default=None)
    drop = fun["biggest_drop"]; money = sum(o["value"] for o in op if o["stage"] not in ("Won", "Lost")); c = hr["counts"]
    chapters = [
        {"id": "acquire", "n": "01", "title": "Acquire", "headline": f"{cur['new_leads']} new leads entered the pipeline, {_dtxt(cur['new_leads'], prev['new_leads'])}.",
         "sub": (f"{top[0][0]} led with {top[0][1]} leads ({pct(top[0][1], cur['new_leads'])}% of new volume)." if top else "No new leads were added in this period."), "sources": [{"source": k, "count": v} for k, v in src.most_common(6)], "definition": sv.DEFS["new_leads"]},
        {"id": "reach", "n": "02", "title": "Reach", "headline": f"{len(tk)} outreach touches reached {len({a['lead_id'] for a in tk})} prospects.",
         "sub": f"{coverage}% of active leads were touched. " + ", ".join(f"{r['label']} {r['touches']}" for r in ch) + ".", "channels": ch, "coverage": coverage, "definition": "Coverage = active leads with at least one touch in the period / all active leads."},
        {"id": "engage", "n": "03", "title": "Engage", "headline": f"{eng} touches earned a positive response, an engagement rate of {pct(eng, len(tk))}%.",
         "sub": (f"Strongest channel: {best_ch['label']} at {best_ch['rate']}%. " if best_ch and best_ch["touches"] >= 5 else "") + (f"Strongest campaign: {best_c['name']} ({best_c['engaged_pct']}% of its leads engaged)." if best_c else ""), "channels": ch, "campaigns": camp, "definition": "Engagement rate = engaged touches / touches in the period."},
        {"id": "convert", "n": "04", "title": "Convert", "headline": f"{cur['qualified']} qualified, {cur['meetings']} meetings, {cur['opps']} opportunities, {cur['won']} won.",
         "sub": f"Open pipeline value is ${money:,.0f}." + (f" The biggest drop-off is before {drop['stage']} ({drop['drop']} leads)." if drop else ""), "funnel": fun["stages"], "definition": fun["definition"]},
        {"id": "pace", "n": "05", "title": "Hold the pace", "headline": f"{cur['overdue']} follow-ups are overdue and {c['untouched']} leads were never contacted.",
         "sub": f"{c['stale']} leads have gone quiet for {sd['stale_days']}+ days and {c['no_next_action']} have no next action.", "counts": c, "definition": hr["definition"]}]
    goals = []
    for k, label, actual in (("touches", "Outreach touches", len(tk)), ("engaged", "Engaged responses", eng), ("meetings", "Meetings booked", cur["meetings"]), ("won", "Deals won", cur["won"])):
        target = max(1, round(sd["goals"].get(k, sv.DEFAULT_GOALS[k]) * days / 30)); goals.append({"id": k, "label": label, "actual": actual, "target": target, "pct": round(100 * actual / target)})
    return {"period": {"days": days}, "chapters": chapters, "goals": goals, "leaderboard": leaderboard(days)}

sv.EXPORTS["campaigns"] = lambda u, d, f: (campaigns_report(u, d, f)["rows"], ["name", "channel", "leads", "contacted", "contacted_pct", "engaged", "engaged_pct", "qualified", "qualified_pct", "won", "touches"])
sv.EXPORTS["channels"] = lambda u, d, f: (channels_report(u, d, f)["rows"], ["label", "touches", "prev_touches", "engaged", "rate", "unique"])
