import pytest
from fastapi import HTTPException
import services as sv, agents as ag, firebase
from schemas import ImportOptions, AssignPerson
from conftest import ADMIN, CALLER, make_lead, csv_bytes

def contact(i): return {"id": f"c{i}", "person_id": f"p{i}", "first_name": f"N{i}", "last_name": "Lee", "organization_name": "Acme", "email": f"n{i}@acme.com", "sanitized_phone": f"+4477009001{i:02d}"}

@pytest.fixture
def listfake(apollo_on, monkeypatch):
    members = {"L1": [contact(i) for i in range(1, 6)], "L2": [contact(i) for i in range(11, 14)]}
    def fake(path, params=None, body=None, method="POST"):
        if path == "/labels": return [{"id": "L1", "name": "Calls A", "modality": "contacts"}, {"id": "L2", "name": "Calls B", "modality": "contacts"}]
        return {"contacts": members[body["contact_label_ids"][0]], "pagination": {"total_pages": 1}}
    monkeypatch.setattr(sv, "_apollo", fake); return members

def camps(): return {c["name"]: c for c in firebase.db.list("campaigns")}
def batch_of(file): return [b for b in firebase.db.list("importBatches") if b["file"] == file][0]

def test_rolling_back_a_list_removes_its_leads_and_the_whole_campaign(listfake):
    sv.sync_apollo_lists(ADMIN, ["L1"]); sv.sync_apollo_lists(ADMIN, ["L2"]); assert set(camps()) == {"Calls A", "Calls B"}
    b = [x for x in firebase.db.list("importBatches") if x["created"] == 3][0]; r = sv.rollback_import(ADMIN, b["id"])
    assert r["status"] == "rolled_back" and r["removed_campaigns"] == ["Calls B"] and set(camps()) == {"Calls A"} and len(firebase.db.list("leads")) == 5
    assert {x["name"]: x["synced"] for x in sv.apollo_lists(ADMIN)} == {"Calls A": True, "Calls B": False}                    # the list is no longer "synced"

def test_a_rolled_back_list_is_not_quietly_re_synced_by_sync_from_apollo(listfake, monkeypatch):
    sv.sync_apollo_lists(ADMIN, ["L2"]); sv.rollback_import(ADMIN, firebase.db.list("importBatches")[0]["id"])
    r = sv.sync_apollo_lists(ADMIN); assert r["lists"] == 0 and firebase.db.list("leads") == [] and camps() == {}              # nothing chosen any more
    assert sv.sync_apollo_lists(ADMIN, ["L2"])["leads_created"] == 3 and "Calls B" in camps()                                  # choosing it again works

def test_open_today_suggestions_about_removed_leads_go_too(listfake):
    sv.sync_apollo_lists(ADMIN, ["L1"])
    for l in firebase.db.list("leads"): l["signal"] = "Hiring"; sv.save_lead(l); sv.assign(ADMIN, l["id"], "u_caller")        # a signal lifts the score enough for Today to propose something
    ag.run_agents(ag.SYSTEM, "t"); assert any(s.get("lead_id") for s in firebase.db.list("suggestions"))
    sv.rollback_import(ADMIN, firebase.db.list("importBatches")[0]["id"]); assert not [s for s in firebase.db.list("suggestions") if s.get("lead_id")]
    assert ag.my_day(CALLER)["items"] == [] and firebase.db.list("tasks") == []

def test_a_campaign_that_still_has_other_leads_is_kept(listfake):
    sv.sync_apollo_lists(ADMIN, ["L1"]); camp = camps()["Calls A"]
    keep = make_lead(first_name="Keep", email="keep@x.com"); keep["campaign_id"] = camp["id"]; sv.save_lead(keep)                    # a lead from somewhere else in the same campaign
    r = sv.rollback_import(ADMIN, firebase.db.list("importBatches")[0]["id"]); assert r["removed_campaigns"] == [] and "Calls A" in camps() and len(firebase.db.list("leads")) == 1

def test_a_hand_made_campaign_is_never_removed():
    firebase.db.put("campaigns", "mine", {"name": "My own", "channel": "call", "archived": False})
    b = sv.start_import(ADMIN, "l.csv", csv_bytes("First Name,Email\nA,a@x.com\n")); done = sv.confirm_import(ADMIN, b["id"], ImportOptions(mapping=b["suggested_mapping"], campaign_id="mine", channels=["call"]))
    r = sv.rollback_import(ADMIN, done["id"]); assert r["removed_campaigns"] == [] and "My own" in camps()

def test_a_campaign_created_by_an_upload_is_removed_with_it():
    b = sv.start_import(ADMIN, "l.csv", csv_bytes("First Name,Email\nA,a@x.com\nB,b@x.com\n")); done = sv.confirm_import(ADMIN, b["id"], ImportOptions(mapping=b["suggested_mapping"], new_campaign_name="Fintech Q1", channels=["call"]))
    assert camps()["Fintech Q1"]["source"] == "import"; r = sv.rollback_import(ADMIN, done["id"]); assert r["removed_campaigns"] == ["Fintech Q1"] and camps() == {}

def test_an_unsafe_rollback_changes_nothing_including_the_campaign(listfake):
    sv.sync_apollo_lists(ADMIN, ["L1"]); lead = firebase.db.list("leads")[0]
    firebase.db.put("activities", "a1", {"lead_id": lead["id"], "at": sv.now(), "type": "phone_call", "outcome": "Connected"})
    with pytest.raises(HTTPException) as e: sv.rollback_import(ADMIN, firebase.db.list("importBatches")[0]["id"])
    assert e.value.status_code == 409 and len(firebase.db.list("leads")) == 5 and "Calls A" in camps()

def test_cleanup_of_leftover_empty_import_campaigns_only_touches_system_made_empty_ones():
    firebase.db.put("campaigns", "a", {"name": "Empty list", "source": "apollo_list", "archived": False}); firebase.db.put("campaigns", "b", {"name": "Hand made empty", "archived": False})
    firebase.db.put("campaigns", "c", {"name": "Full list", "source": "apollo_list", "archived": False}); l = make_lead(first_name="X"); l["campaign_id"] = "c"; sv.save_lead(l)
    assert sv.remove_empty_import_campaigns() == ["Empty list"] and set(camps()) == {"Hand made empty", "Full list"}
