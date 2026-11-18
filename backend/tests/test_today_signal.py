import services as sv, agents as ag, firebase
from conftest import ADMIN, CALLER, make_lead

def lead(owner="u_caller", **kw): return make_lead(owner=owner, first_name=kw.pop("first_name", "Ann"), last_name="X", company="Acme", **kw)

def run_and_load():
    ag.run_agents(ag.SYSTEM, "test"); return ag.today(ADMIN)["suggestions"]

def test_no_signal_lookup_code_or_collection_remains():
    assert not hasattr(ag, "refresh_signals") and not hasattr(ag, "provider_info") and not hasattr(ag, "_claude_web")
    lead(signal="Hiring"); run_and_load(); assert firebase.db.list("signals") == []
    assert "scout" not in ag.AGENTS and "provider" not in ag.today(ADMIN)["status"]

def test_signal_leads_come_first_and_the_signal_is_the_opener():
    plain = lead(first_name="Plain"); sig = lead(first_name="Sig", signal="Opening a second office", signal_url="https://news.example/x")
    firebase.db.put("tasks", "t1", {"lead_id": plain["id"], "title": "cb", "type": "callback", "due_at": "2000-01-01T00:00:00+00:00", "status": "open", "owner_id": "u_caller", "lead_name": "Plain X"})
    items = [s for s in run_and_load() if s.get("lead")]
    names = [s["lead"]["full_name"] for s in items]; assert names[0] == "Sig X" and "Plain X" in names           # signal first; others still appear via rules
    first = items[0]; assert "Opening a second office" in first["body"] and "https://news.example/x" in first["body"]
    assert any("Signal: Opening a second office" in e["text"] for e in first["evidence"])
    assert first["lead"]["signal"] == "Opening a second office" and first["lead"]["signal_url"].startswith("https://")

def test_leads_without_a_signal_still_surface_through_existing_rules():
    """Existing rules unchanged: an overdue follow-up surfaces a lead with no signal, with a neutral opener."""
    l = lead(first_name="Cold")
    firebase.db.put("tasks", "t1", {"lead_id": l["id"], "title": "Retry call", "type": "follow_up", "due_at": "2000-01-01T00:00:00+00:00", "status": "open", "owner_id": "u_caller", "lead_name": "Cold X"})
    items = [s for s in run_and_load() if s.get("lead")]
    assert items and items[0]["lead"]["id"] == l["id"] and "Opener: introduce yourself" in items[0]["body"]
    assert any("overdue" in e["text"] for e in items[0]["evidence"])

def test_never_contacted_alone_scores_below_the_threshold_but_a_signal_lifts_it_over():
    now, nd = sv.now(), sv.datetime.now(sv.timezone.utc); l = lead(first_name="Fresh")
    base, _ = ag.score(l, [], [], now, nd); assert base < 30                              # existing behaviour: not enough by itself
    l["signal"] = "Funding round"; boosted, _ = ag.score(l, [], [], now, nd); assert boosted >= 30

def test_drafts_use_the_signal_for_linkedin_and_email():
    l = lead(signal="Raised a seed round")
    for ch in ("linkedin", "email"):
        assert "raised a seed round" in ag.brief(l, [], ["Never contacted"], ch, "helping teams", "Sam").lower()

def test_signal_can_be_edited_and_boosts_priority():
    from schemas import LeadPatch
    l = lead(); base, _ = ag.score(sv.get_lead(ADMIN, l["id"]), [], [], sv.now(), sv.datetime.now(sv.timezone.utc))
    sv.update_lead(ADMIN, l["id"], LeadPatch(signal="Hiring SDRs", signal_url="https://x.test"))
    got = sv.get_lead(ADMIN, l["id"]); boosted, why = ag.score(got, [], [], sv.now(), sv.datetime.now(sv.timezone.utc))
    assert got["signal"] == "Hiring SDRs" and boosted == base + 25 and any(w.startswith("Signal:") for w in why)

def test_seed_marks_demo_signals_as_demo():
    firebase.db.c.clear(); sv.seed(); sig = [l for l in firebase.db.list("leads") if l.get("signal")]
    assert sig and all(l["demo"] and "(demo)" in l["signal"] for l in sig)
