import pytest
from fastapi import HTTPException
import services as sv, firebase
from schemas import ApolloSearch, ApolloImport
from conftest import ADMIN, CALLER, make_lead

class Resp:
    def __init__(self, status=200, body=None, headers=None): self.status_code, self._b, self.headers = status, body or {}, headers or {}
    def json(self): return self._b

def code(e): return e.value.detail["code"]

def test_search_without_key_says_pending_and_never_returns_fake_rows():
    with pytest.raises(HTTPException) as e: sv.apollo_search(ADMIN, ApolloSearch(title="CEO"))
    assert code(e) == "apollo_not_configured" and "pending configuration" in e.value.detail["message"]

def test_search_builds_the_official_request_and_maps_results(apollo_on, monkeypatch):
    seen = {}
    def fake(path, params=None, body=None):
        seen.update(path=path, params=params)
        return {"total_entries": 4000, "people": [{"id": "p1", "first_name": "Ann", "last_name_obfuscated": "Le***", "title": "CEO", "has_email": True, "organization": {"name": "Acme"}}]}
    monkeypatch.setattr(sv, "_apollo", fake)
    make_lead(email="x@y.com", apollo_id="p1")                        # existing lead with the same apollo id
    r = sv.apollo_search(ADMIN, ApolloSearch(title="CEO", company="acme.com, Globex Corp", location="London", seniority="c_suite", keywords="saas", page=2, per_page=500))
    p = seen["params"]
    assert seen["path"] == "/mixed_people/api_search" and ("per_page", 100) in p and ("page", 2) in p      # per_page clamped to Apollo's 100
    assert ("person_titles[]", "CEO") in p and ("person_seniorities[]", "c_suite") in p and ("person_locations[]", "London") in p
    assert ("q_organization_domains_list[]", "acme.com") in p and ("q_keywords", "saas Globex Corp") in p     # domain vs company name routing
    row = r["results"][0]
    assert row["last_name"] == "Le***" and row["obfuscated"] and row["email"] == "" and row["duplicate_of"]   # duplicate preview
    assert r["total"] == 4000 and r["has_more"] is True and r["demo"] is False

def test_last_page_has_no_more(apollo_on, monkeypatch):
    monkeypatch.setattr(sv, "_apollo", lambda *a, **k: {"total_entries": 30, "people": [{"id": "p", "first_name": "A"}] * 5})
    assert sv.apollo_search(ADMIN, ApolloSearch(page=2, per_page=25))["has_more"] is False

def test_bad_seniority_and_permissions(apollo_on):
    with pytest.raises(HTTPException) as e: sv.apollo_search(ADMIN, ApolloSearch(seniority="god"))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e: sv.apollo_search(CALLER, ApolloSearch())
    assert e.value.status_code == 403

def test_http_layer_retries_429_then_succeeds(apollo_on, monkeypatch):
    seq = [Resp(429, headers={"Retry-After": "1"}), Resp(200, {"ok": 1})]; monkeypatch.setattr(sv.httpx, "post", lambda *a, **k: seq.pop(0))
    assert sv._apollo("/x") == {"ok": 1} and seq == []

def test_http_layer_gives_up_on_long_rate_limit_with_clear_message(apollo_on, monkeypatch):
    monkeypatch.setattr(sv.httpx, "post", lambda *a, **k: Resp(429, headers={"Retry-After": "60"}))
    with pytest.raises(HTTPException) as e: sv._apollo("/x")
    assert code(e) == "apollo_rate_limited" and "60" in e.value.detail["message"]

@pytest.mark.parametrize("status,expected", [(401, "apollo_auth"), (403, "apollo_auth"), (422, "apollo_bad_request"), (500, "apollo_error")])
def test_http_layer_maps_errors(apollo_on, monkeypatch, status, expected):
    monkeypatch.setattr(sv.httpx, "post", lambda *a, **k: Resp(status))
    with pytest.raises(HTTPException) as e: sv._apollo("/x")
    assert code(e) == expected

def test_http_layer_timeouts_are_bounded(apollo_on, monkeypatch):
    calls = []
    def boom(*a, **k): calls.append(1); raise sv.httpx.ConnectTimeout("t")
    monkeypatch.setattr(sv.httpx, "post", boom)
    with pytest.raises(HTTPException) as e: sv._apollo("/x")
    assert code(e) == "apollo_unreachable" and len(calls) == 3

def test_import_selected_skips_duplicates_keeps_signal_and_batch(apollo_on):
    firebase.db.put("campaigns", "c1", {"name": "C", "channel": "call", "archived": False})
    make_lead(email="dup@x.com")
    recs = [{"apollo_id": "p1", "first_name": "A", "last_name": "B", "email": "dup@x.com"}, {"apollo_id": "p2", "first_name": "C", "last_name": "D", "signal": "Hiring", "duplicate_of": None, "has_email": True}]
    b = sv.apollo_import(ADMIN, ApolloImport(records=recs, campaign_id="c1"))
    assert (b["created"], b["skipped"]) == (1, 1)
    l = [l for l in firebase.db.list("leads") if l.get("apollo_id") == "p2"][0]
    assert l["source"] == "Apollo" and l["signal"] == "Hiring" and l["campaign_id"] == "c1" and l["import_batch_id"] == b["id"]
    with pytest.raises(HTTPException): sv.apollo_import(ADMIN, ApolloImport(records=[], campaign_id="nope"))

def test_sequence_sync_is_idempotent_and_archives_inactive(apollo_on, monkeypatch):
    seqs = [{"id": "s1", "name": "Seq 1", "active": True}, {"id": "s2", "name": "Paused", "active": False}]
    contacts = [{"id": "c1", "person_id": "p1", "first_name": "Ann", "last_name": "L", "email": "a@x.com", "contact_campaign_statuses": [{"emailer_campaign_id": "s1", "status": "active", "added_at": "1"}]},
                {"id": "c2", "person_id": "p2", "first_name": "Bo", "last_name": "L", "email": "b@x.com", "contact_campaign_statuses": [{"emailer_campaign_id": "s2", "status": "active", "added_at": "1"}]}]
    monkeypatch.setattr(sv, "_apollo", lambda path, params=None, body=None, method="POST": {"emailer_campaigns": seqs} if path.startswith("/emailer_campaigns") else {"emailer_messages": []} if path.startswith("/emailer_messages") else {"contacts": contacts})
    r1 = sv.sync_apollo_campaigns(ADMIN); r2 = sv.sync_apollo_campaigns(ADMIN)
    assert (r1["created"], r1["leads_created"]) == (1, 1) and (r2["created"], r2["leads_created"], r2["leads_duplicate"]) == (0, 0, 1)
    assert len(firebase.db.list("leads")) == 1
    seqs[0]["active"] = False; r3 = sv.sync_apollo_campaigns(ADMIN)
    assert r3["archived"] == 1 and [c for c in firebase.db.list("campaigns") if c.get("apollo_id") == "s1"][0]["archived"] is True
