"""Through the real FastAPI routes (no startup hook, so no demo seed), with auth switched off and every provider call mocked."""
import pytest
from fastapi.testclient import TestClient
import main, services as sv, firebase
from conftest import make_lead

V = "/api/v1"
@pytest.fixture
def client(): return TestClient(main.app)
def H(u="u_admin"): return {"X-Mock-User": u}

def test_meta_reports_ai_pending_and_integrations_list_four(client):
    m = client.get(V + "/meta", headers=H()).json(); assert m["ai"]["configured"] is False and "pending configuration" in m["ai"]["note"]
    assert {i["id"] for i in client.get(V + "/integrations", headers=H()).json()} == {"firebase", "apollo", "gemini", "groq"}
    r = client.post(V + "/integrations/groq/test", headers=H()).json(); assert r["ok"] is False and "Pending configuration" in r["message"]
    assert client.post(V + "/integrations/claude/test", headers=H()).status_code == 404

def test_apollo_endpoints_say_pending_without_a_key(client):
    r = client.post(V + "/integrations/apollo/search", headers=H(), json={"title": "CEO"}); assert r.status_code == 400 and r.json()["error"]["code"] == "apollo_not_configured"
    r = client.post(V + "/enrichment/jobs", headers=H(), json={"all": True, "confirm": True}); assert r.status_code == 400 and r.json()["error"]["code"] == "apollo_not_configured"
    assert client.get(V + "/enrichment/preview", headers=H()).json()["configured"] is False

def test_lead_signal_roundtrip_and_ai_fallback(client):
    r = client.post(V + "/leads", headers=H(), json={"first_name": "Zed", "email": "z@z.com", "signal": "Hiring", "signal_url": "https://x.test"}); assert r.status_code == 201 and r.json()["signal"] == "Hiring"
    lid = r.json()["id"]; assert client.patch(f"{V}/leads/{lid}", headers=H(), json={"signal": "New CFO"}).json()["signal"] == "New CFO"
    c = client.post(V + "/ai/chat", headers=H(), json={"message": "summarise this lead", "lead_id": lid}).json()
    assert "Zed" in c["answer"] and "pending configuration" in c["answer"] and c["ai"]["configured"] is False

def test_enrich_all_end_to_end_through_the_api(client, apollo_on, monkeypatch):
    for i in range(12): make_lead(first_name=f"N{i}", last_name="X", company="Acme")
    monkeypatch.setattr(sv, "_apollo", lambda path, params=None, body=None: {"matches": [{"id": f"p{j}", "first_name": "N", "last_name": "X", "email": f"e{j}@acme.com", "title": "CTO", "match_confidence": "high"} for j, _ in enumerate(body["details"])], "credits_consumed": 1})
    pv = client.get(V + "/enrichment/preview", headers=H()).json(); assert pv["qualifying"] == 12 and pv["api_calls"] == 2
    j = client.post(V + "/enrichment/jobs", headers=H(), json={"all": True, "confirm": True}); assert j.status_code == 202
    done = client.get(f"{V}/enrichment/jobs/{j.json()['id']}", headers=H()).json()               # background task has run by now
    assert done["status"] == "completed" and done["processed"] == 12 and done["credits_used"] == 2
    assert client.post(V + "/enrichment/jobs", headers=H(), json={"all": True, "confirm": True}).status_code == 409   # nothing left to enrich

def test_callers_cannot_use_admin_integrations_or_enrich_all(client):
    assert client.post(V + "/enrichment/jobs", headers=H("u_caller"), json={"all": True, "confirm": True}).status_code == 403
    assert client.get(V + "/enrichment/preview", headers=H("u_caller")).status_code == 403
    assert client.post(V + "/integrations/apollo/search", headers=H("u_caller"), json={}).status_code == 403
