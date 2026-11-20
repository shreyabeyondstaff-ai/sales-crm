import pytest
import services as sv, agents as ag, firebase
from schemas import ImportOptions
from conftest import ADMIN, make_lead, csv_bytes

SWITCHBOARD = "+44 20 3917 1712"

def contact(i, phone=SWITCHBOARD, **kw):
    return {"id": f"c{i}", "person_id": f"p{i}", "first_name": f"Person{i}", "last_name": "Lee", "title": "Owner", "organization_name": "Firm", "email": f"p{i}@firm.com",
            "sanitized_phone": phone, "linkedin_url": f"https://l/in/{i}", **kw}

def test_a_shared_phone_alone_is_not_a_duplicate():
    make_lead(first_name="Ann", last_name="Lee", phone=SWITCHBOARD)
    assert sv.find_dupe({"first_name": "Bob", "last_name": "Ray", "phone": SWITCHBOARD})[0] is None
    d, conf, sig = sv.find_dupe({"first_name": "Ann", "last_name": "Lee", "phone": SWITCHBOARD}); assert d and conf == "high" and sig == "phone+name"       # same person, new email

def test_email_linkedin_and_apollo_id_still_match_on_their_own():
    make_lead(first_name="Ann", last_name="Lee", email="ann@x.com", linkedin_url="https://www.linkedin.com/in/ann/", apollo_id="p9")
    for rec, sig in (({"email": "ANN@x.com"}, "email"), ({"linkedin_url": "linkedin.com/in/ann"}, "linkedin"), ({"apollo_id": "p9"}, "apollo_id")): assert sv.find_dupe(rec)[2] == sig

def test_a_list_of_many_people_on_one_switchboard_imports_everyone(apollo_on, monkeypatch):
    members = [contact(i) for i in range(1, 41)]
    def fake(path, params=None, body=None, method="POST"):
        if path == "/labels": return [{"id": "L1", "name": "Callers", "modality": "contacts", "cached_count": 40}]
        return {"contacts": members, "pagination": {"total_pages": 1}}
    monkeypatch.setattr(sv, "_apollo", fake)
    r = sv.sync_apollo_lists(ADMIN, ["L1"]); assert (r["leads_created"], r["leads_duplicate"]) == (40, 0) and len(firebase.db.list("leads")) == 40
    r2 = sv.sync_apollo_lists(ADMIN, ["L1"]); assert (r2["leads_created"], r2["leads_duplicate"]) == (0, 40) and r2["skipped_by"] == {"email": 40}       # a re-sync skips them by email, and says so

def test_the_sync_reports_why_contacts_were_skipped(apollo_on, monkeypatch):
    make_lead(first_name="Person1", last_name="Lee", email="other@x.com", phone=SWITCHBOARD)                           # same name + phone as contact 1, different email
    make_lead(first_name="Z", last_name="Z", email="p2@firm.com")                                                      # same email as contact 2
    members = [contact(1), contact(2), contact(3)]
    monkeypatch.setattr(sv, "_apollo", lambda path, params=None, body=None, method="POST": [{"id": "L1", "name": "L", "modality": "contacts"}] if path == "/labels" else {"contacts": members})
    r = sv.sync_apollo_lists(ADMIN, ["L1"]); assert r["leads_created"] == 1 and r["skipped_by"] == {"phone+name": 1, "email": 1}
    assert firebase.db.list("importBatches")[0]["skipped_by"] == {"phone+name": 1, "email": 1}

def test_a_file_upload_with_a_shared_phone_keeps_everyone():
    b = sv.start_import(ADMIN, "l.csv", csv_bytes("First Name,Last Name,Phone\nAnn,Lee,020 3917 1712\nBob,Ray,020 3917 1712\nCy,Zed,020 3917 1712\n"))
    done = sv.confirm_import(ADMIN, b["id"], ImportOptions(mapping=b["suggested_mapping"])); assert (done["created"], done["skipped"]) == (3, 0)

def test_the_data_cleaner_does_not_propose_archiving_people_who_only_share_a_phone():
    for i in range(4): make_lead(first_name=f"P{i}", last_name="X", email=f"p{i}@x.com", phone=SWITCHBOARD, owner="u_caller")
    ag.run_agents(ag.SYSTEM, "test"); assert not [s for s in firebase.db.list("suggestions") if s["kind"] == "archive_duplicate"]
    make_lead(first_name="P0", last_name="X", email="p0b@x.com", phone=SWITCHBOARD, owner="u_caller")                  # same name + phone: a real duplicate
    firebase.db.c["suggestions"].clear(); firebase.db.c["agentRuns"].clear(); ag.run_agents(ag.SYSTEM, "test2")
    dup = [s for s in firebase.db.list("suggestions") if s["kind"] == "archive_duplicate"]; assert len(dup) == 1 and dup[0]["evidence"][0]["text"].startswith("Same phone: ") and "|" not in dup[0]["evidence"][0]["text"]
