from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import services as sv, agents as ag, firebase, main
from schemas import AssignPerson, ActivityIn
from conftest import ADMIN, CALLER, CALLER2, make_lead

def lead(name, owner="u_caller", **kw): return make_lead(first_name=name, last_name="X", owner=owner, **kw)
def iso(**d): return (datetime.now(timezone.utc) + timedelta(**d)).isoformat(timespec="seconds")
def task(lid, typ, due, owner="u_caller", status="open", title="t"): return firebase.db.put("tasks", f"t_{lid}_{typ}", {"lead_id": lid, "type": typ, "due_at": due, "status": status, "owner_id": owner, "title": title, "lead_name": "x"})
def names(d, grp=None): return [x["lead"]["first_name"] for x in d["items"] if grp is None or x["group"] == grp]

def test_freshly_assigned_leads_show_up_immediately_without_any_agent_run():
    for i in range(4): make_lead(first_name=f"L{i}", last_name="X", email=f"l{i}@x.com", signal="Hiring" if i == 0 else "", title="CEO" if i < 2 else "Analyst", phone="+44 7700 90010%d" % i)
    sv.assign_run(ADMIN, [AssignPerson(user_id="u_caller", count=3)], "top_first", {}, dry_run=False)
    assert firebase.db.list("suggestions") == []                                              # the agents have not run
    d = ag.my_day(CALLER); assert names(d) == ["L0", "L1", "L2"] and d["tiles"]["to_call"] == 3 and d["tiles"]["new"] == 3 and all(x["group"] == "new" for x in d["items"])
    assert ag.my_day(CALLER2)["items"] == []                                                  # someone else's leads are never shown

def test_order_is_callbacks_then_followups_then_new_leads_by_fit():
    cb = lead("Callback"); fu = lead("Followup"); od = lead("Overdue"); hi = lead("HotNew", signal="Funding", title="CEO", phone="+44 7700 900111"); lo = lead("ColdNew")
    task(cb["id"], "callback", iso(hours=-1), title="Call back"); task(fu["id"], "follow_up", iso(minutes=-5), title="Follow up"); task(od["id"], "follow_up", iso(days=-3), title="Old follow-up")
    d = ag.my_day(CALLER); assert names(d) == ["Callback", "Overdue", "Followup", "HotNew", "ColdNew"]
    assert [x["group"] for x in d["items"]] == ["callbacks", "followups", "followups", "new", "new"] and d["items"][0]["overdue"] is True
    assert d["items"][0]["reason"] == "Overdue: Call back" and d["items"][3]["fit"] > d["items"][4]["fit"] and (d["tiles"]["callbacks"], d["tiles"]["followups"], d["tiles"]["new"]) == (1, 2, 2)

def test_every_assigned_lead_is_listed_but_never_suppressed_finished_or_other_peoples():
    future = lead("Future"); future["last_activity_at"] = sv.now(); sv.save_lead(future); task(future["id"], "follow_up", iso(days=3))
    worked = lead("Worked"); worked["last_activity_at"] = sv.now(); sv.save_lead(worked)                       # contacted, nothing due: still assigned to her
    dnc = lead("Dnc"); dnc["suppressed"] = True; sv.save_lead(dnc); won = lead("Won"); won["stage"] = "Won"; sv.save_lead(won); lead("Theirs", owner="u_caller2"); lead("Shown")
    d = ag.my_day(CALLER); assert names(d)[0] == "Shown" and sorted(names(d)) == ["Future", "Shown", "Worked"] and sorted(names(d, "other")) == ["Future", "Worked"]
    assert (d["tiles"]["assigned"], d["tiles"]["to_call"], d["tiles"]["other"]) == (3, 1, 2)                     # progress counts only what needs action

def test_assigned_campaign_leads_that_apollo_already_emailed_all_show_up_whatever_the_channel():
    firebase.db.put("campaigns", "c1", {"name": "Apollo seq", "channel": "email", "archived": False})
    for i in range(5):
        l = lead(f"E{i}", email=f"e{i}@x.com"); l["campaign_id"] = "c1"; l["channels"] = ["email"]; l["last_activity_at"] = sv.now(); l["touches"] = 1; sv.save_lead(l)
        firebase.db.put("activities", f"a{i}", {"lead_id": l["id"], "user_id": "u_caller", "type": "email", "outcome": "Sent", "at": iso(days=-6), "notes": "Subject line", "source": "apollo"})
    d = ag.my_day(CALLER); assert sorted(names(d)) == [f"E{i}" for i in range(5)] and d["tiles"]["assigned"] == 5 and d["tiles"]["to_call"] == 0
    assert all(x["lead"]["campaign_id"] == "c1" and x["recent"][0]["outcome"] == "Sent" and x["group"] == "other" for x in d["items"]) and ag.my_day(CALLER2)["items"] == []

def test_each_row_carries_what_a_caller_needs_to_act():
    l = lead("Dana", signal="Opening a second office", signal_url="https://n/x", phone="+44 7700 900123", title="VP Sales", company="Acme", linkedin_url="https://l/in/d"); l["channels"] = ["call", "email"]; sv.save_lead(l)
    x = ag.my_day(CALLER)["items"][0]; ld = x["lead"]
    assert (ld["phone"], ld["signal"], ld["company"], ld["channels"], ld["linkedin_url"]) == ("+44 7700 900123", "Opening a second office", "Acme", ["call", "email"], "https://l/in/d")
    assert "Opener: Opening a second office" in x["brief"] and "Call Dana X" in x["brief"] and "Why now" in x["brief"] and any(w["label"].startswith("Signal") for w in x["fit_why"])

def test_leads_without_a_signal_get_a_neutral_opener():
    lead("Plain"); assert "Opener: introduce yourself" in ag.my_day(CALLER)["items"][0]["brief"]

def test_todays_work_and_the_week_are_counted_for_this_caller_only():
    a = lead("A"); b = lead("B"); lead("C")
    sv.log_activity(CALLER, ActivityIn(lead_id=a["id"], type="phone_call", outcome="Connected", notes="")); sv.log_activity(CALLER, ActivityIn(lead_id=b["id"], type="phone_call", outcome="No Answer", notes=""))
    d = ag.my_day(CALLER); t = d["tiles"]
    assert (t["done_today"], t["connected_today"]) == (2, 1) and d["week"]["calls"] == 2 and d["week"]["connected"] == 1
    assert "C" in names(d) and "A" not in names(d, "new")                                                        # contacted leads leave the "new" group
    assert ag.my_day(CALLER2)["tiles"]["done_today"] == 0

def test_a_no_answer_creates_a_retry_that_comes_back_the_same_day():
    a = lead("A"); sv.log_activity(CALLER, ActivityIn(lead_id=a["id"], type="phone_call", outcome="Call Back Requested", notes="", follow_up_due=iso(hours=-1)))
    d = ag.my_day(CALLER); assert names(d) == ["A"] and d["items"][0]["group"] == "callbacks"

def test_talking_points_are_only_prepared_for_the_first_rows():
    for i in range(ag.BRIEF_LIMIT + 5): lead(f"N{i:03d}")
    d = ag.my_day(CALLER); assert d["total"] == ag.BRIEF_LIMIT + 5 and sum("brief" in x for x in d["items"]) == ag.BRIEF_LIMIT

def test_the_route_works_for_callers_and_is_closed_to_read_only_roles():
    lead("A"); firebase.db.put("users", "u_da", {"id": "u_da", "name": "DA", "role": "da", "active": True}); c = TestClient(main.app); h = lambda u: {"X-Mock-User": u}
    r = c.get("/api/v1/today/mine", headers=h("u_caller")); assert r.status_code == 200 and r.json()["tiles"]["to_call"] == 1 and r.json()["items"][0]["lead"]["first_name"] == "A"
    assert c.get("/api/v1/today/mine", headers=h("u_da")).status_code == 403
