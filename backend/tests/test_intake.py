import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import services as sv, agents as ag, firebase, main
from schemas import ImportOptions, AssignPerson
from conftest import ADMIN, CALLER, make_lead, csv_bytes

P = lambda u, n: AssignPerson(user_id=u, count=n)

def upload(rows, **kw):
    text = "First Name,Email,Title,Signal\n" + "\n".join(rows) + "\n"; b = sv.start_import(ADMIN, "list.csv", csv_bytes(text))
    return sv.confirm_import(ADMIN, b["id"], ImportOptions(mapping=b["suggested_mapping"], **kw))

def batch_row(ov, bid): return [b for b in ov["batches"] if b["id"] == bid][0]

def test_the_overview_counts_what_needs_an_owner_and_groups_it_by_batch():
    b1 = upload(["A,a@x.com,CEO,Hiring", "B,b@x.com,Analyst,", "C,c@x.com,VP Sales,"], channels=["call"], new_campaign_name="List one")
    b2 = upload(["D,d@x.com,Manager,Funding"], channels=["email"], new_campaign_name="List two")
    make_lead(first_name="Manual", email="m@x.com"); make_lead(first_name="Mine", owner="u_caller")
    ov = sv.intake_overview(ADMIN); s = ov["summary"]
    assert (s["unassigned"], s["with_signal"], s["new_7d"], s["batches_to_review"]) == (5, 2, 5, 2) and s["avg_fit"] > 0
    r1, r2 = batch_row(ov, b1["id"]), batch_row(ov, b2["id"])
    assert (r1["total"], r1["unassigned"], r1["assigned"], r1["with_signal"], r1["status"]) == (3, 3, 0, 1, "new") and r1["campaigns"] == ["List one"] and r1["by"] == "Admin" and r1["label"] == "list.csv"
    assert r2["unassigned"] == 1 and r2["campaigns"] == ["List two"]
    manual = batch_row(ov, "none"); assert manual["unassigned"] == 1 and manual["status"] == "new" and ov["batches"][-1]["id"] == "none"          # manual row last
    assert {p["id"] for p in ov["people"]} == {"u_caller", "u_caller2"}

def test_review_is_batch_level_and_assigning_everything_completes_the_batch():
    b = upload(["A,a@x.com,CEO,", "B,b@x.com,CEO,"], channels=["call"])
    sv.review_batch(ADMIN, b["id"]); r = batch_row(sv.intake_overview(ADMIN), b["id"]); assert r["status"] == "reviewed" and r["reviewed_by"] == "Admin" and sv.intake_overview(ADMIN)["summary"]["batches_to_review"] == 0
    assert "import.review" in [a["action"] for a in firebase.db.list("auditLogs")]
    sv.assign_run(ADMIN, [P("u_caller", 1)], "top_first", {"batch": b["id"]}, dry_run=False); r = batch_row(sv.intake_overview(ADMIN), b["id"]); assert (r["status"], r["unassigned"], r["assigned"]) == ("reviewed", 1, 1)
    sv.assign_run(ADMIN, [P("u_caller", 1)], "top_first", {"batch": b["id"]}, dry_run=False); r = batch_row(sv.intake_overview(ADMIN), b["id"]); assert r["status"] == "done" and r["unassigned"] == 0
    assert sv.intake_overview(ADMIN)["summary"]["unassigned"] == 0

def test_a_whole_batch_can_be_assigned_without_touching_other_batches():
    b1 = upload(["A,a@x.com,CEO,", "B,b@x.com,CEO,"], channels=["call"]); upload(["C,c@x.com,CEO,Hot signal"], channels=["call"])
    r = sv.assign_run(ADMIN, [P("u_caller", 10)], "top_first", {"batch": b1["id"]}, dry_run=False)
    assert r["assigned"] == 2 and r["shortfall"] == 8 and {l["first_name"] for l in firebase.db.list("leads") if l.get("owner_id")} == {"A", "B"}
    assert sv.assignment_pool(ADMIN, {"batch": b1["id"]})["total"] == 0 and sv.assignment_pool(ADMIN, {})["total"] == 1

def test_the_manual_group_can_be_filtered_too():
    upload(["A,a@x.com,CEO,"], channels=["call"]); make_lead(first_name="Manual", email="m@x.com")
    assert [x["full_name"] for x in sv.assignment_pool(ADMIN, {"batch": "none"})["items"]] == ["Manual Lee"]

def test_unknown_batches_cannot_be_reviewed_and_permissions_hold():
    with pytest.raises(HTTPException) as e: sv.review_batch(ADMIN, "nope")
    assert e.value.status_code == 404
    b = upload(["A,a@x.com,CEO,"], channels=["call"])
    for fn in (lambda: sv.review_batch(CALLER, b["id"]), lambda: sv.intake_overview(CALLER)):
        with pytest.raises(HTTPException) as e: fn()
        assert e.value.status_code == 403

def test_rolled_back_and_empty_batches_do_not_clutter_the_view():
    b = upload(["A,a@x.com,CEO,"], channels=["call"]); sv.rollback_import(ADMIN, b["id"])
    assert sv.intake_overview(ADMIN)["batches"] == [] and sv.intake_overview(ADMIN)["summary"]["unassigned"] == 0

def test_today_no_longer_asks_the_admin_to_assign_leads_one_by_one():
    for i in range(4): make_lead(first_name=f"U{i}", email=f"u{i}@x.com", signal="Hot")
    ag.run_agents(ag.SYSTEM, "test"); assert not [s for s in firebase.db.list("suggestions") if s["kind"] == "assign"]
    assert "unassigned leads" in firebase.db.get("suggestions", "sug_digest")["body"]                           # still counted in the digest

def test_api_overview_review_and_batch_assignment():
    b = upload(["A,a@x.com,CEO,", "B,b@x.com,CEO,"], channels=["call"]); c = TestClient(main.app); h = lambda u: {"X-Mock-User": u}; V = "/api/v1"
    assert c.get(V + "/intake", headers=h("u_caller")).status_code == 403
    ov = c.get(V + "/intake", headers=h("u_admin")).json(); assert ov["summary"]["unassigned"] == 2 and ov["batches"][0]["id"] == b["id"]
    assert c.post(f"{V}/intake/batches/{b['id']}/review", headers=h("u_admin")).json()["reviewed_by"] == "u_admin"
    assert c.get(V + f"/assign/pool?batch={b['id']}", headers=h("u_admin")).json()["total"] == 2 and c.get(V + "/assign/pool?batch=other", headers=h("u_admin")).json()["total"] == 0
    r = c.post(V + "/assign/run", headers=h("u_admin"), json={"people": [{"user_id": "u_caller", "count": 2}], "mode": "top_first", "filters": {"batch": b["id"]}, "dry_run": False}).json()
    assert r["assigned"] == 2 and c.get(V + "/intake", headers=h("u_admin")).json()["batches"][0]["status"] == "done"
