import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import services as sv, firebase, main
from conftest import ADMIN, CALLER, CALLER2, make_lead

V = "/api/v1"

def conv(i, **kw): return {"id": f"cv{i}", "topic": f"Call {i}", "start_time": "2026-03-01T09:30:00Z", "duration": 600, "conversation_type": "phone_call", "host": "Sam", "participant_names": ["Sam", "Dana"], "can_access_conversation": True, **kw}

def detail(i, segs=None, **kw):
    segs = segs if segs is not None else [{"spoken_sentence": "Hi Dana.", "participant_name": "Sam"}, {"spoken_sentence": "How can I help?", "participant_name": "Sam"}, {"spoken_sentence": "Pricing please.", "participant_name": "Dana"}]
    return {"id": f"cv{i}", "topic": f"Call {i}", "start_time": "2026-03-01T09:30:00Z", "duration": 600, "conversation_type": "phone_call", "transcript": segs, "audio_recording": {"url": f"https://rec/{i}.mp3"}, "video_recording": {"url": None},
            "call_summary": {"outcome": "Interested", "next_steps": ["Send pricing"]}, **kw}

class Apollo:
    """Fake Apollo Conversations: search is by contact id (POST), details by conversation id (GET). Counts what costs credits."""
    def __init__(self, by_contact, details): self.by_contact, self.details, self.searches, self.fetched = by_contact, details, [], []
    def __call__(self, path, params=None, body=None, method="POST"):
        if path == "/conversations/search":
            assert method == "POST" and len(body["contact_ids"]) == 1; self.searches.append(body["contact_ids"][0]); return {"conversations": self.by_contact.get(body["contact_ids"][0], []) if body.get("page", 1) == 1 else [], "pagination": {"total_pages": 1}}
        assert method == "GET" and path.startswith("/conversations/"); cid = path.rsplit("/", 1)[1]; self.fetched.append(cid)
        d = self.details[cid]
        if isinstance(d, Exception): raise d
        return d

@pytest.fixture
def setup(apollo_on, monkeypatch):
    lead = make_lead(first_name="Dana", last_name="X", owner="u_caller"); lead["apollo_contact_id"] = "c1"; sv.save_lead(lead)
    fake = Apollo({"c1": [conv(1), conv(2)]}, {"cv1": detail(1), "cv2": detail(2)}); monkeypatch.setattr(sv, "_apollo", fake); return lead, fake

def trs(): return sorted(firebase.db.list("transcripts"), key=lambda t: t["conversation_id"])

def test_scan_is_free_it_only_searches_and_never_fetches_details(setup):
    lead, fake = setup; r = sv.calls_scan(ADMIN, [lead["id"]])
    assert (r["conversations"], r["new"], r["already"], r["no_contact_id"]) == (2, 2, 0, 0) and fake.fetched == [] and fake.searches == ["c1"] and r["fetch_cap"] == sv.CALL_FETCH_CAP

def test_syncing_needs_confirmation_because_it_can_cost_credits(setup):
    lead, fake = setup
    with pytest.raises(HTTPException) as e: sv.calls_sync(ADMIN, [lead["id"]], False)
    assert e.value.detail["code"] == "confirm_required" and fake.fetched == [] and trs() == []

def test_a_transcript_is_attached_with_speakers_summary_and_recording_links(setup):
    lead, fake = setup; r = sv.calls_sync(ADMIN, [lead["id"]], True); t = trs()[0]
    assert (r["stored"], r["pending"], r["remaining"], r["stopped"]) == (2, 0, 0, None) and sorted(fake.fetched) == ["cv1", "cv2"]
    assert t["lead_id"] == lead["id"] and t["text"] == "Sam: Hi Dana. How can I help?\nDana: Pricing please."            # a speaker's consecutive sentences are merged
    assert (t["topic"], t["duration_s"], t["conversation_type"], t["host"], t["participants"]) == ("Call 1", 600, "phone_call", "Sam", ["Sam", "Dana"])
    assert t["started_at"] == "2026-03-01T09:30:00+00:00" and t["audio_url"] == "https://rec/1.mp3" and t["video_url"] == "" and t["summary"] == {"outcome": "Interested", "next_steps": ["Send pricing"]} and not t["pending"]

def test_resyncing_never_duplicates_and_costs_nothing_more(setup):
    lead, fake = setup; sv.calls_sync(ADMIN, [lead["id"]], True); fake.fetched.clear()
    r = sv.calls_sync(ADMIN, [lead["id"]], True); assert (r["stored"], r["remaining"]) == (0, 0) and fake.fetched == [] and len(trs()) == 2
    assert sv.calls_scan(ADMIN, [lead["id"]])["already"] == 2 and sv.calls_scan(ADMIN, [lead["id"]])["new"] == 0

def test_a_call_with_no_transcript_yet_is_kept_as_pending_and_retried_later(setup):
    lead, fake = setup; fake.details["cv1"] = detail(1, segs=[]); r = sv.calls_sync(ADMIN, [lead["id"]], True)
    assert (r["stored"], r["pending"]) == (1, 1) and [t["pending"] for t in trs()] == [True, False] and trs()[0]["text"] == ""
    fake.details["cv1"] = detail(1); fake.fetched.clear(); r2 = sv.calls_sync(ADMIN, [lead["id"]], True)
    assert fake.fetched == ["cv1"] and r2["stored"] == 1 and trs()[0]["text"].startswith("Sam: Hi Dana.") and not trs()[0]["pending"]               # only the pending one is fetched again

def test_a_run_is_capped_and_says_how_many_are_left(apollo_on, monkeypatch):
    lead = make_lead(first_name="D", last_name="X"); lead["apollo_contact_id"] = "c1"; sv.save_lead(lead); n = sv.CALL_FETCH_CAP + 4
    fake = Apollo({"c1": [conv(i) for i in range(n)]}, {f"cv{i}": detail(i) for i in range(n)}); monkeypatch.setattr(sv, "_apollo", fake)
    r = sv.calls_sync(ADMIN, [lead["id"]], True); assert (r["stored"], r["remaining"]) == (sv.CALL_FETCH_CAP, 4) and len(fake.fetched) == sv.CALL_FETCH_CAP
    r2 = sv.calls_sync(ADMIN, [lead["id"]], True); assert (r2["stored"], r2["remaining"]) == (4, 0) and len(trs()) == n

def test_running_out_of_credits_stops_cleanly_and_keeps_what_was_attached(setup):
    lead, fake = setup; fake.details["cv2"] = HTTPException(422, {"code": "apollo_no_credits", "message": "Apollo says you are out of credits."})
    r = sv.calls_sync(ADMIN, [lead["id"]], True); assert r["stored"] == 1 and r["stopped"] == "Apollo says you are out of credits." and r["remaining"] == 1 and [t["conversation_id"] for t in trs()] == ["cv1"]

def test_a_missing_conversation_is_skipped_not_fatal(setup):
    lead, fake = setup; fake.details["cv1"] = HTTPException(404, {"code": "apollo_not_found", "message": "gone"})
    r = sv.calls_sync(ADMIN, [lead["id"]], True); assert (r["stored"], r["skipped"], r["stopped"]) == (1, 1, None)

def test_inaccessible_conversations_are_not_fetched(apollo_on, monkeypatch):
    lead = make_lead(first_name="D", last_name="X"); lead["apollo_contact_id"] = "c1"; sv.save_lead(lead)
    fake = Apollo({"c1": [conv(1, can_access_conversation=False), conv(2)]}, {"cv2": detail(2)}); monkeypatch.setattr(sv, "_apollo", fake)
    assert sv.calls_scan(ADMIN, [lead["id"]])["inaccessible"] == 1 and sv.calls_sync(ADMIN, [lead["id"]], True)["stored"] == 1 and fake.fetched == ["cv2"]

def test_leads_without_an_apollo_contact_id_are_counted_and_never_guessed(apollo_on, monkeypatch):
    plain = make_lead(first_name="No", last_name="Id"); fake = Apollo({}, {}); monkeypatch.setattr(sv, "_apollo", fake)
    r = sv.calls_scan(ADMIN, [plain["id"]]); assert (r["no_contact_id"], r["conversations"]) == (1, 0) and fake.searches == []
    assert sv.calls_sync(ADMIN, [plain["id"]], True)["no_contact_id"] == 1 and trs() == []

def test_attaching_calls_creates_no_activity_and_changes_nothing_on_the_lead(setup):
    lead, fake = setup; before = dict(firebase.db.get("leads", lead["id"])); sv.calls_sync(ADMIN, [lead["id"]], True)
    after = firebase.db.get("leads", lead["id"]); assert firebase.db.list("activities") == [] and firebase.db.list("tasks") == [] and (after["touches"], after["stage"], after["last_activity_at"]) == (before["touches"], before["stage"], before["last_activity_at"])

def test_permissions_and_record_scoping(setup):
    lead, fake = setup
    for fn in (lambda: sv.calls_scan(CALLER, [lead["id"]]), lambda: sv.calls_sync(CALLER, [lead["id"]], True)):
        with pytest.raises(HTTPException) as e: fn()
        assert e.value.status_code == 403
    sv.calls_sync(ADMIN, [lead["id"]], True); c = TestClient(main.app); h = lambda u: {"X-Mock-User": u}
    assert len(c.get(f"{V}/leads/{lead['id']}/transcripts", headers=h("u_caller")).json()) == 2              # the owner can read them
    assert c.get(f"{V}/leads/{lead['id']}/transcripts", headers=h("u_caller2")).status_code == 404            # someone else's lead: hidden
    assert c.post(V + "/apollo/calls/scan", headers=h("u_caller"), json={"lead_ids": [lead["id"]]}).status_code == 403

def test_the_ai_assistant_reads_the_attached_transcript(setup, monkeypatch):
    lead, fake = setup; sv.calls_sync(ADMIN, [lead["id"]], True); facts, n = sv.lead_facts(ADMIN, firebase.db.get("leads", lead["id"]))
    assert n == 2 and "Dana: Pricing please." in facts

def test_apollo_credit_and_missing_errors_get_their_own_clear_codes(apollo_on, monkeypatch):
    class R:
        def __init__(self, status, text=""): self.status_code, self.text, self.headers = status, text, {}
        def json(self): return {}
    for status, text, code in ((422, '{"error": "Insufficient credits. Please upgrade your plan."}', "apollo_no_credits"), (422, "bad", "apollo_bad_request"), (404, "", "apollo_not_found")):
        monkeypatch.setattr(sv.httpx, "get", lambda *a, _r=R(status, text), **k: _r)
        with pytest.raises(HTTPException) as e: sv._apollo("/conversations/x", method="GET")
        assert e.value.detail["code"] == code

def test_open_in_apollo_addresses_default_and_can_be_overridden_safely(monkeypatch):
    assert sv.apollo_links() == {"contact": "https://app.apollo.io/#/contacts/{id}", "person": "https://app.apollo.io/#/people/{id}"}
    monkeypatch.setenv("APOLLO_CONTACT_URL", "https://eu.apollo.io/#/c/{id}"); monkeypatch.setenv("APOLLO_PERSON_URL", "javascript:alert({id})")
    assert sv.apollo_links() == {"contact": "https://eu.apollo.io/#/c/{id}", "person": "https://app.apollo.io/#/people/{id}"}              # a non-https or {id}-less value is ignored
    monkeypatch.setenv("APOLLO_CONTACT_URL", "https://x.example/no-placeholder"); assert sv.apollo_links()["contact"].startswith("https://app.apollo.io")
    assert TestClient(main.app).get(V + "/meta", headers={"X-Mock-User": "u_admin"}).json()["apollo_links"]["contact"].startswith("https://app.apollo.io")
