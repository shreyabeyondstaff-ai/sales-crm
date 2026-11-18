import pytest
from fastapi import HTTPException
import services as sv, firebase
from conftest import ADMIN, CALLER, make_lead

LABELS = [{"id": "L1", "name": "Q1 Fintech call list", "modality": "contacts", "cached_count": 2}, {"id": "L2", "name": "Old list", "modality": "contacts", "cached_count": 1},
          {"id": "A1", "name": "Target accounts", "modality": "accounts", "cached_count": 9}, {"id": "E1", "name": "A sequence", "modality": "emailer_campaigns", "cached_count": 4}]

def contact(i, **kw): return {"id": f"c{i}", "person_id": f"p{i}", "first_name": f"N{i}", "last_name": "Lee", "title": "CEO", "organization_name": "Acme", "email": f"n{i}@acme.com", "sanitized_phone": f"+4477009001{i:02d}", "linkedin_url": f"https://l/in/{i}", **kw}

class Apollo:
    def __init__(self, members): self.members, self.calls = members, []
    def __call__(self, path, params=None, body=None, method="POST"):
        self.calls.append((path, method, body))
        if path == "/labels": assert method == "GET"; return LABELS
        assert path == "/contacts/search" and method == "POST"; lid = body["contact_label_ids"][0]
        allp = self.members.get(lid, []); pg = body["page"]; chunk = allp[(pg - 1) * 100: pg * 100]
        return {"contacts": chunk, "pagination": {"page": pg, "per_page": 100, "total_pages": max(1, -(-len(allp) // 100))}}

@pytest.fixture
def fake(apollo_on, monkeypatch):
    f = Apollo({"L1": [contact(1), contact(2)], "L2": [contact(3)]}); monkeypatch.setattr(sv, "_apollo", f); return f

def test_lists_need_a_key_and_the_assign_permission(monkeypatch):
    with pytest.raises(HTTPException) as e: sv.apollo_lists(ADMIN)
    assert e.value.detail["code"] == "apollo_not_configured"
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    with pytest.raises(HTTPException) as e: sv.apollo_lists(CALLER)
    assert e.value.status_code == 403

def test_only_people_lists_are_offered_and_marked_when_synced(fake):
    out = sv.apollo_lists(ADMIN); assert [x["name"] for x in out] == ["Old list", "Q1 Fintech call list"] and out[1]["count"] == 2 and not any(x["synced"] for x in out)
    sv.sync_apollo_lists(ADMIN, ["L1"]); assert {x["name"]: x["synced"] for x in sv.apollo_lists(ADMIN)} == {"Old list": False, "Q1 Fintech call list": True}

def test_syncing_a_list_creates_a_call_campaign_and_unassigned_call_leads_with_phones(fake):
    r = sv.sync_apollo_lists(ADMIN, ["L1"]); camp = [c for c in firebase.db.list("campaigns")][0]
    assert (r["lists"], r["leads_created"], r["contacts_scanned"]) == (1, 2, 2) and camp["name"] == "Q1 Fintech call list" and camp["channel"] == "call" and camp["source"] == "apollo_list" and camp["apollo_label_id"] == "L1"
    ls = firebase.db.list("leads"); assert len(ls) == 2 and all(l["channels"] == ["call"] and l["campaign_id"] == camp["id"] and not l["owner_id"] and l["source"] == "Apollo" for l in ls)
    l = [x for x in ls if x["first_name"] == "N1"][0]; assert l["phone"] == "+447700900101" and l["apollo_contact_id"] == "c1" and l["apollo_id"] == "p1" and l["source_detail"] == "Apollo list: Q1 Fintech call list"
    assert fake.calls[1][2]["contact_label_ids"] == ["L1"]

def test_resync_is_idempotent_and_picks_up_new_contacts(fake):
    sv.sync_apollo_lists(ADMIN, ["L1"]); r2 = sv.sync_apollo_lists(ADMIN, ["L1"]); assert (r2["leads_created"], r2["leads_duplicate"]) == (0, 2) and len(firebase.db.list("leads")) == 2 and len(firebase.db.list("campaigns")) == 1
    fake.members["L1"].append(contact(9)); r3 = sv.sync_apollo_lists(ADMIN, None); assert r3["leads_created"] == 1 and len(firebase.db.list("leads")) == 3     # None = re-sync the chosen lists

def test_nothing_chosen_means_nothing_synced_and_no_apollo_call(fake):
    assert sv.sync_apollo_lists(ADMIN)["lists"] == 0 and fake.calls == []

def test_unknown_or_non_people_lists_are_refused(fake):
    for bad in ("nope", "A1", "E1"):
        with pytest.raises(HTTPException) as e: sv.sync_apollo_lists(ADMIN, [bad])
        assert e.value.detail["code"] == "bad_list"
    assert firebase.db.list("campaigns") == [] and firebase.db.list("leads") == []

def test_existing_leads_are_kept_and_linked_not_duplicated(fake):
    old = make_lead(first_name="N1", last_name="Lee", email="n1@acme.com"); old["channels"] = ["email"]; sv.save_lead(old)
    r = sv.sync_apollo_lists(ADMIN, ["L1"]); got = firebase.db.get("leads", old["id"]); camp = firebase.db.list("campaigns")[0]
    assert (r["leads_created"], r["leads_duplicate"], r["leads_linked"]) == (1, 1, 1) and got["campaign_id"] == camp["id"] and got["channels"] == ["email", "call"] and got["apollo_contact_id"] == "c1"

def test_paging_and_the_cap(fake):
    fake.members["L1"] = [contact(i) for i in range(1, 251)]; r = sv.sync_apollo_lists(ADMIN, ["L1"]); assert r["leads_created"] == 250 and r["truncated"] is False
    assert [c[2]["page"] for c in fake.calls if c[0] == "/contacts/search"] == [1, 2, 3]
    firebase.db.c["leads"].clear(); r2 = sv.sync_apollo_lists(ADMIN, ["L1"], max_pages=1); assert r2["truncated"] is True and r2["leads_created"] == 100

def test_list_leads_flow_straight_into_the_assign_pool_ranked(fake):
    fake.members["L1"] = [contact(1, title="Analyst", sanitized_phone=""), contact(2, title="CEO")]; sv.sync_apollo_lists(ADMIN, ["L1"])
    pool = sv.assignment_pool(ADMIN, {"channel": "call"}); assert [x["full_name"] for x in pool["items"]] == ["N2 Lee", "N1 Lee"] and pool["items"][0]["fit"] > pool["items"][1]["fit"]

def test_sync_from_apollo_also_refreshes_the_lists_you_chose(fake, monkeypatch):
    sv.sync_apollo_lists(ADMIN, ["L1"]); fake.members["L1"].append(contact(7))
    def both(path, params=None, body=None, method="POST"):
        if path.startswith("/emailer_campaigns"): return {"emailer_campaigns": []}
        if path.startswith("/emailer_messages"): return {"emailer_messages": []}
        return fake(path, params, body, method)
    monkeypatch.setattr(sv, "_apollo", both); r = sv.sync_apollo_campaigns(ADMIN); assert r["lists"]["leads_created"] == 1 and len(firebase.db.list("leads")) == 3
