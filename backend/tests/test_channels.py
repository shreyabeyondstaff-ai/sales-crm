import pytest
from fastapi import HTTPException
import services as sv, agents as ag, firebase
from schemas import ImportOptions, ApolloImport, LeadPatch
from conftest import ADMIN, CALLER, make_lead, csv_bytes

OUTREACH = {"id": "u_out", "name": "Outreach", "role": "outreach"}

def upload(text="First Name,Email\nAnn,ann@x.com\nBob,bob@x.com\n"): return sv.start_import(ADMIN, "l.csv", csv_bytes(text))
def do_import(**kw):
    b = upload(); return sv.confirm_import(ADMIN, b["id"], ImportOptions(mapping=b["suggested_mapping"], **kw))
def leads(): return sorted(firebase.db.list("leads"), key=lambda l: l["first_name"])
def camps(): return {c["id"]: c for c in firebase.db.list("campaigns")}

def test_multi_channel_import_sets_channels_creates_a_campaign_and_assigns_an_owner():
    done = do_import(channels=["call", "email"], new_campaign_name="Fintech VPs", owner_id="u_caller")
    ls = leads(); assert done["created"] == 2 and all(l["channels"] == ["call", "email"] and l["owner_id"] == "u_caller" for l in ls)
    c = camps()[ls[0]["campaign_id"]]; assert c["name"] == "Fintech VPs" and c["channel"] == "mixed"               # two channels: the campaign is multi-channel
    assert done["channels"] == ["call", "email"] and done["campaign_id"] == c["id"]

def test_single_channel_new_campaign_takes_that_channel_and_a_same_name_campaign_is_reused():
    do_import(channels=["linkedin"], new_campaign_name="RevOps"); first = list(camps().values()); assert len(first) == 1 and first[0]["channel"] == "linkedin"
    b = upload("First Name,Email\nCy,cy@x.com\n"); sv.confirm_import(ADMIN, b["id"], ImportOptions(mapping=b["suggested_mapping"], channels=["linkedin"], new_campaign_name="RevOps"))
    assert len(camps()) == 1                                                                                    # reused, not duplicated

def test_existing_campaign_with_explicit_channels_overriding_its_own():
    firebase.db.put("campaigns", "c1", {"name": "Calls only", "channel": "call", "archived": False})
    do_import(campaign_id="c1", channels=["call", "email"]); ls = leads()
    assert all(l["campaign_id"] == "c1" and l["channels"] == ["call", "email"] for l in ls)

def test_leads_appear_in_every_chosen_channel_filter_and_mixed_means_not_exactly_one():
    do_import(channels=["call", "email"]); do_b = upload("First Name,Email\nSolo,solo@x.com\n")
    sv.confirm_import(ADMIN, do_b["id"], ImportOptions(mapping=do_b["suggested_mapping"], channels=["linkedin"]))
    n = lambda ch: sv.list_leads(ADMIN, channel=ch)["total"]
    assert (n("call"), n("email"), n("linkedin"), n("mixed")) == (2, 2, 1, 2)
    scoped_names = lambda ch: {l["first_name"] for l in sv.scoped(ADMIN, {"channel": ch})[0]}; assert scoped_names("email") == {"Ann", "Bob"} and scoped_names("linkedin") == {"Solo"}

def test_without_explicit_channels_a_lead_follows_its_campaign():
    firebase.db.put("campaigns", "ce", {"name": "E", "channel": "email", "archived": False}); firebase.db.put("campaigns", "cm", {"name": "M", "channel": "mixed", "archived": False})
    a = make_lead(first_name="A"); a["campaign_id"] = "ce"; b = make_lead(first_name="B"); b["campaign_id"] = "cm"; c = make_lead(first_name="C")
    cp = camps(); assert [sv.lead_channels(x, cp) for x in (a, b, c)] == [["email"], [], []]
    assert sv.list_leads(ADMIN, channel="email")["total"] == 1

def test_validation_and_permissions_happen_before_anything_is_written():
    b = upload()
    for kw, status in (({"channels": ["fax"]}, 422), ({"campaign_id": "nope"}, 400), ({"owner_id": "ghost"}, 400)):
        with pytest.raises(HTTPException) as e: sv.confirm_import(ADMIN, b["id"], ImportOptions(mapping=b["suggested_mapping"], **kw))
        assert e.value.status_code == status
    assert leads() == [] and camps() == {}
    firebase.db.put("users", "u_out", {**OUTREACH, "active": True}); firebase.db.put("users", "u_off", {"id": "u_off", "name": "Off", "role": "caller", "active": False})
    with pytest.raises(HTTPException) as e: sv.confirm_import(ADMIN, b["id"], ImportOptions(mapping=b["suggested_mapping"], owner_id="u_off"))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e: sv.confirm_import(CALLER, b["id"], ImportOptions(mapping=b["suggested_mapping"]))
    assert e.value.status_code == 403

def test_apollo_import_takes_the_same_channel_campaign_and_owner_choices(apollo_on):
    recs = [{"apollo_id": "p1", "first_name": "A", "last_name": "B", "email": "a@x.com"}, {"apollo_id": "p2", "first_name": "C", "last_name": "D"}]
    b = sv.apollo_import(ADMIN, ApolloImport(records=recs, channels=["email", "linkedin"], new_campaign_name="Apollo Q1", owner_id="u_caller"))
    ls = leads(); assert b["created"] == 2 and all(l["channels"] == ["email", "linkedin"] and l["owner_id"] == "u_caller" and l["campaign_id"] for l in ls)
    with pytest.raises(HTTPException) as e: sv.apollo_import(ADMIN, ApolloImport(records=recs, channels=["pigeon"]))
    assert e.value.status_code == 422

def test_channels_can_be_edited_and_cleared_on_a_lead():
    l = make_lead(); got = sv.update_lead(ADMIN, l["id"], LeadPatch(channels=["call", "bogus", "call", "email"])); assert got["channels"] == ["call", "email"]
    assert sv.update_lead(ADMIN, l["id"], LeadPatch(channels=[]))["channels"] == []

def test_sales_activity_queue_carries_the_channels_so_each_tab_can_filter():
    do_import(channels=["call", "email"], owner_id="u_caller")
    q = sv.workspace_queue(CALLER); assert len(q) == 2 and all(x["lead"]["channels"] == ["call", "email"] for x in q)

def test_today_suggestion_uses_the_first_channel_call_then_linkedin_then_email():
    for name, chs, want in (("Both", ["email", "linkedin"], "linkedin"), ("OnlyEmail", ["email"], "email"), ("Phone", ["email", "call"], "call")):
        l = make_lead(first_name=name, owner="u_caller"); l["channels"] = chs; sv.save_lead(l)
        firebase.db.put("tasks", "t_" + name, {"lead_id": l["id"], "title": "cb", "type": "callback", "due_at": "2000-01-01T00:00:00+00:00", "status": "open", "owner_id": "u_caller", "lead_name": name})
    ag.run_agents(ag.SYSTEM, "test"); got = {s["lead_name"].split()[0]: s["channel"] for s in firebase.db.list("suggestions") if s.get("lead_id")}
    assert got == {"Both": "linkedin", "OnlyEmail": "email", "Phone": "call"}

def test_sequence_sync_marks_email_without_replacing_existing_channels(apollo_on, monkeypatch):
    seqs = [{"id": "s1", "name": "Seq", "active": True}]
    mk = lambda i, em: {"id": f"c{i}", "person_id": f"p{i}", "first_name": f"N{i}", "last_name": "L", "email": em, "contact_campaign_statuses": [{"emailer_campaign_id": "s1", "status": "active", "added_at": "1"}]}
    contacts = [mk(1, "new@x.com"), mk(2, "old@x.com")]; existing = make_lead(first_name="Old", email="old@x.com"); existing["channels"] = ["call"]; sv.save_lead(existing)
    monkeypatch.setattr(sv, "_apollo", lambda path, params=None, body=None, method="POST": {"emailer_campaigns": seqs} if path.startswith("/emailer_campaigns") else {"emailer_messages": []} if path.startswith("/emailer_messages") else {"contacts": contacts})
    sv.sync_apollo_campaigns(ADMIN); by = {l["email"]: l for l in firebase.db.list("leads")}
    assert by["new@x.com"]["channels"] == ["email"] and by["old@x.com"]["channels"] == ["call", "email"]
