import pytest
from fastapi import HTTPException
import services as sv, firebase
from conftest import ADMIN, CALLER, make_lead

SEND = "2026-03-01T09:30:00.000Z"

def msg(i, to="ann@acme.com", **kw):
    return {"id": f"m{i}", "to_email": to, "contact_id": f"c{i}", "emailer_campaign_id": "s1", "subject": f"Subject {i}", "body_text": "Hi Ann,\n\n  quick   idea about your team.", "completed_at": SEND, "campaign_position": 1, "campaign_name": "Seq", **kw}

class Apollo:
    """Fake Apollo: returns the base list, or the subset for a status / reply-class filter, and records every call."""
    def __init__(self, base, bounced=(), opened=(), clicked=(), replied=None):
        self.base, self.bounced, self.opened, self.clicked, self.replied, self.calls = base, set(bounced), set(opened), set(clicked), replied or {}, []
    def __call__(self, path, params=None, body=None, method="POST"):
        assert path == "/emailer_messages/search" and method == "GET"          # documented: a GET with query params
        self.calls.append(params); stats = [v for k, v in params if k == "emailer_message_stats[]"]; classes = [v for k, v in params if k == "emailer_message_reply_classes[]"]
        if "bounced" in stats: return {"emailer_messages": [m for m in self.base if m["id"] in self.bounced]}
        if "opened" in stats: return {"emailer_messages": [m for m in self.base if m["id"] in self.opened]}
        if "clicked" in stats: return {"emailer_messages": [m for m in self.base if m["id"] in self.clicked]}
        if classes: return {"emailer_messages": [{**m, "reply_class": self.replied[m["id"]]} for m in self.base if m["id"] in self.replied]}
        return {"emailer_messages": self.base}

@pytest.fixture
def synced(apollo_on):
    firebase.db.put("campaigns", "c_seq", {"name": "Seq", "channel": "email", "archived": False, "source": "apollo", "apollo_id": "s1"})

def acts(): return sorted(firebase.db.list("activities"), key=lambda a: a["id"])

def test_requires_a_key_synced_sequences_and_permission(monkeypatch):
    with pytest.raises(HTTPException) as e: sv.sync_apollo_emails(ADMIN)
    assert e.value.detail["code"] == "apollo_not_configured"
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    with pytest.raises(HTTPException) as e: sv.sync_apollo_emails(ADMIN)
    assert e.value.detail["code"] == "no_synced_sequences"
    firebase.db.put("campaigns", "c", {"archived": False, "source": "apollo", "apollo_id": "s1"})
    with pytest.raises(HTTPException) as e: sv.sync_apollo_emails(CALLER)
    assert e.value.status_code == 403

def test_sent_emails_are_appended_with_real_send_time_subject_and_excerpt(synced, monkeypatch):
    l = make_lead(email="ann@acme.com", owner="u_caller"); fake = Apollo([msg(1)]); monkeypatch.setattr(sv, "_apollo", fake)
    r = sv.sync_apollo_emails(ADMIN); a = acts()
    assert (r["messages"], r["sent"], r["replies"], r["bounces"], r["unmatched"]) == (1, 1, 0, 0, 0) and len(a) == 1
    assert a[0]["type"] == "email" and a[0]["outcome"] == "Sent" and a[0]["at"] == "2026-03-01T09:30:00+00:00" and a[0]["source"] == "apollo" and a[0]["user"] == "Apollo"
    assert a[0]["notes"].startswith("Subject 1\n\nHi Ann, quick idea about your team.") and a[0]["external_ref"] == "apollo:m1" and a[0]["user_id"] == "u_caller"
    got = firebase.db.get("leads", l["id"]); assert got["touches"] == 1 and got["last_activity_at"] == "2026-03-01T09:30:00+00:00"
    assert ("emailer_campaign_ids[]", "s1") in fake.calls[0] and ("per_page", 100) in fake.calls[0]

def test_resync_never_duplicates_or_double_counts(synced, monkeypatch):
    l = make_lead(email="ann@acme.com"); monkeypatch.setattr(sv, "_apollo", Apollo([msg(1)], bounced={"m1"}, replied={"m1": "follow_up_question"}))
    sv.sync_apollo_emails(ADMIN); first = acts(); r2 = sv.sync_apollo_emails(ADMIN)
    assert len(first) == 3 and acts() == first and (r2["sent"], r2["replies"], r2["bounces"]) == (0, 0, 0)
    assert firebase.db.get("leads", l["id"])["touches"] == 1                                    # only the send is a touch

def test_bounce_and_reply_are_timed_when_detected_and_honestly_labelled(synced, monkeypatch):
    make_lead(email="ann@acme.com"); make_lead(email="bo@acme.com", first_name="Bo")
    monkeypatch.setattr(sv, "_apollo", Apollo([msg(1), msg(2, to="bo@acme.com")], bounced={"m1"}, replied={"m2": "willing_to_meet"}))
    r = sv.sync_apollo_emails(ADMIN); by = {a["id"]: a for a in acts()}
    assert (r["bounces"], r["replies"]) == (1, 1)
    b, rp = by["act_apb_m1"], by["act_apr_m2"]
    assert b["outcome"] == "Bounced" and "does not say when" in b["notes"] and b["at"] > SEND and rp["outcome"] == "Meeting Requested" and rp["reply_class"] == "willing_to_meet"
    assert "does not provide the reply text" in rp["notes"] and rp["at"] > SEND                    # detected time, not the send time

@pytest.mark.parametrize("cls,outcome", [("willing_to_meet", "Meeting Requested"), ("not_interested", "Not Interested"), ("out_of_office", "No Response"), ("follow_up_question", "Replied"),
                                         ("person_referral", "Replied"), ("none_of_the_above", "Replied"), ("already_left_company_or_not_right_person", "Replied"), ("unsubscribe", "Replied")])
def test_reply_categories_map_to_outcomes(synced, monkeypatch, cls, outcome):
    make_lead(email="ann@acme.com"); monkeypatch.setattr(sv, "_apollo", Apollo([msg(1)], replied={"m1": cls}))
    sv.sync_apollo_emails(ADMIN); assert [a for a in acts() if a["id"].startswith("act_apr")][0]["outcome"] == outcome

def test_unsubscribe_is_noted_but_never_auto_suppresses_or_changes_anything_else(synced, monkeypatch):
    l = make_lead(email="ann@acme.com"); firebase.db.put("tasks", "t1", {"lead_id": l["id"], "status": "open", "title": "x", "due_at": "2030-01-01", "type": "follow_up"})
    monkeypatch.setattr(sv, "_apollo", Apollo([msg(1)], replied={"m1": "unsubscribe"})); sv.sync_apollo_emails(ADMIN)
    got = firebase.db.get("leads", l["id"]); r = [a for a in acts() if a["id"].startswith("act_apr")][0]
    assert "Do Not Contact" in r["notes"] and got["suppressed"] is False and got["stage"] == "New"
    assert [t["status"] for t in firebase.db.list("tasks")] == ["open"]                          # no tasks created, none cancelled

def test_opens_and_clicks_are_flags_not_activities_and_update_on_resync(synced, monkeypatch):
    make_lead(email="ann@acme.com"); fake = Apollo([msg(1)]); monkeypatch.setattr(sv, "_apollo", fake); sv.sync_apollo_emails(ADMIN)
    a = acts()[0]; assert not a.get("opened") and not a.get("clicked")
    fake.opened = {"m1"}; sv.sync_apollo_emails(ADMIN); assert len(acts()) == 1 and acts()[0]["opened"] is True and acts()[0]["clicked"] is False
    fake.opened, fake.clicked = set(), {"m1"}; sv.sync_apollo_emails(ADMIN); assert acts()[0]["opened"] is True and acts()[0]["clicked"] is True       # a click implies an open

def test_matching_uses_the_recipient_address_then_the_contact_id(synced, monkeypatch):
    byemail = make_lead(email="Ann@Acme.com"); bycid = make_lead(first_name="Cy", email="other@x.com"); bycid["apollo_contact_id"] = "c2"; sv.save_lead(bycid)
    monkeypatch.setattr(sv, "_apollo", Apollo([msg(1), msg(2, to="someone-else@zzz.com"), msg(3, to="stranger@zzz.com")]))
    r = sv.sync_apollo_emails(ADMIN)
    assert {a["lead_id"] for a in acts()} == {byemail["id"], bycid["id"]} and r["unmatched"] == 1 and r["leads_updated"] == 2        # unknown people are counted, never created
    assert len(firebase.db.list("leads")) == 2

def test_unsent_emails_are_not_logged_as_sent(synced, monkeypatch):
    make_lead(email="ann@acme.com"); monkeypatch.setattr(sv, "_apollo", Apollo([msg(1, completed_at=None, failed_at="2026-03-01T00:00:00Z")]))
    r = sv.sync_apollo_emails(ADMIN); assert r["sent"] == 0 and acts() == []                    # scheduled or failed emails never happened

def test_paging_is_followed_and_capped(synced, monkeypatch):
    make_lead(email="ann@acme.com"); pages = []
    def fake(path, params=None, body=None, method="POST"):
        pg = dict(params)["page"]; pages.append(pg); return {"emailer_messages": [msg(f"{pg}_{i}") for i in range(100 if pg < 3 else 5)]}
    monkeypatch.setattr(sv, "_apollo", fake); monkeypatch.setattr(sv, "EMAIL_REPLY_CLASSES", ["none_of_the_above"])
    r = sv.sync_apollo_emails(ADMIN); assert r["messages"] == 205 and pages[:3] == [1, 2, 3] and r["truncated"] is False
    monkeypatch.setattr(sv, "EMAIL_MAX_PAGES", 2); firebase.db.c["activities"].clear(); r = sv.sync_apollo_emails(ADMIN); assert r["truncated"] is True

def test_a_rate_limit_stops_the_sync_cleanly(synced, monkeypatch):
    make_lead(email="ann@acme.com")
    def limited(*a, **k): raise HTTPException(429, {"code": "apollo_rate_limited", "message": "Apollo rate limit reached. Try again in about 60 seconds"})
    monkeypatch.setattr(sv, "_apollo", limited)
    with pytest.raises(HTTPException) as e: sv.sync_apollo_emails(ADMIN)
    assert e.value.status_code == 429 and acts() == []

def test_sync_from_apollo_also_pulls_emails_and_remembers_contact_ids(apollo_on, monkeypatch):
    seqs = [{"id": "s1", "name": "Seq 1", "active": True}]
    contacts = [{"id": "ct1", "person_id": "p1", "first_name": "Ann", "last_name": "L", "email": "ann@acme.com", "contact_campaign_statuses": [{"emailer_campaign_id": "s1", "status": "active", "added_at": "1"}]}]
    def fake(path, params=None, body=None, method="POST"):
        if path.startswith("/emailer_campaigns"): return {"emailer_campaigns": seqs}
        if path.startswith("/contacts"): return {"contacts": contacts}
        return {"emailer_messages": [msg(1)] if not any(k.endswith("stats[]") or k.endswith("classes[]") for k, _ in params) else []}
    monkeypatch.setattr(sv, "_apollo", fake); r = sv.sync_apollo_campaigns(ADMIN)
    lead = firebase.db.list("leads")[0]
    assert lead["apollo_contact_id"] == "ct1" and r["emails"]["sent"] == 1 and acts()[0]["lead_id"] == lead["id"]
    assert sv.sync_apollo_campaigns(ADMIN)["emails"]["sent"] == 0                                # idempotent end to end
    assert [t["title"] for t in firebase.db.list("tasks")] == []                                 # emails never create tasks

def test_email_history_shows_in_the_lead_timeline_and_the_api_route_is_permissioned(synced, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    l = make_lead(email="ann@acme.com", owner="u_caller"); monkeypatch.setattr(sv, "_apollo", Apollo([msg(1)], replied={"m1": "willing_to_meet"}))
    c = TestClient(main.app); h = lambda u: {"X-Mock-User": u}
    assert c.post("/api/v1/integrations/apollo/sync-emails", headers=h("u_caller")).status_code == 403
    assert c.post("/api/v1/integrations/apollo/sync-emails", headers=h("u_admin")).json()["sent"] == 1
    tl = c.get(f"/api/v1/leads/{l['id']}/timeline", headers=h("u_caller")).json()
    assert {x["outcome"] for x in tl if x.get("kind") == "activity"} == {"Sent", "Meeting Requested"}
