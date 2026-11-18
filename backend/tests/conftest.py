"""Test setup. Tests NEVER touch real services: env is forced to in-memory data and empty provider keys BEFORE the app is imported
(firebase.py loads .env without overriding values that are already set), and every external call is mocked in the tests."""
import os, sys
for k, v in {"USE_FIRESTORE": "false", "AUTH_DISABLED": "true", "APOLLO_API_KEY": "", "GEMINI_API_KEY": "", "GROQ_API_KEY": "", "AI_PROVIDER": "none",
             "APOLLO_BULK_PAUSE": "0", "CRON_SECRET": ""}.items():
    os.environ[k] = v
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import firebase, services as sv

ADMIN = {"id": "u_admin", "name": "Admin", "role": "admin"}
CALLER = {"id": "u_caller", "name": "Caller", "role": "caller"}
CALLER2 = {"id": "u_caller2", "name": "Caller Two", "role": "caller"}

@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Empty in-memory DB with a few users, no demo data, no provider keys."""
    firebase.db.c.clear()
    for u in (ADMIN, CALLER, CALLER2): firebase.db.put("users", u["id"], {**u, "email": u["id"] + "@x.test", "active": True})
    for k in ("APOLLO_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"): monkeypatch.setenv(k, "")
    monkeypatch.setenv("AI_PROVIDER", "none"); monkeypatch.setenv("APOLLO_BULK_PAUSE", "0")
    monkeypatch.setattr(sv.time, "sleep", lambda s: None)
    yield

@pytest.fixture
def apollo_on(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")

def make_lead(owner=None, **kw):
    d = {"first_name": "Ann", "last_name": "Lee", "email": "", "phone": "", "company": "", "source": "Manual"}; d.update(kw)
    l = sv.new_lead(d, ADMIN, owner=owner)
    return l

def csv_bytes(text): return text.encode("utf-8")
