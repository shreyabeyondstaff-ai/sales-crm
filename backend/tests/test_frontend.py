"""Cheap guards for the vanilla-JS frontend (no browser needed) plus the exact requests the lead drawer's forms send."""
import os, re
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
import main, agents as ag, firebase
from conftest import make_lead

APP = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "app.js")
CSS = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "style.css")
V = "/api/v1"

def test_every_modal_form_or_dialog_a_button_calls_is_actually_defined():
    """A button wired to a function that does not exist fails silently in the browser (this is how '+ Task' and '+ Opportunity' stayed dead)."""
    src = open(APP, encoding="utf-8").read()
    called = set(re.findall(r"\b([a-z][A-Za-z]*(?:Modal|Box|Form|UI))\(", src)) - {"leadForm"} | {"leadForm"}
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_]\w*)|(?:const|let|var)\s+([A-Za-z_]\w*)\s*=", src))
    names = {a or b for a, b in defined}
    missing = sorted(n for n in called if n not in names)
    assert not missing, f"called but never defined in app.js: {missing}"

def test_a_class_used_for_a_page_column_is_never_also_a_full_width_paint_rule():
    """'.cx-main' was both the My day column and a button highlight, which painted the whole column blue."""
    css = open(CSS, encoding="utf-8").read()
    assert not re.search(r"(?m)^\.cx-main\{[^}]*background", css), "bare .cx-main must not set a background (use .btn.cx-main for the button)"

def test_the_task_form_request_works_for_a_caller_and_a_callback_lands_on_my_day():
    lead = make_lead(first_name="Dana", last_name="X", owner="u_caller"); c = TestClient(main.app); h = {"X-Mock-User": "u_caller"}
    due = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    r = c.post(V + "/tasks", headers=h, json={"lead_id": lead["id"], "title": "Call back about pricing", "type": "callback", "due_at": due, "priority": "high", "owner_id": None, "notes": "asked at 3pm"})
    assert r.status_code == 201 and r.json()["owner_id"] == "u_caller" and r.json()["type"] == "callback"
    day = ag.my_day({"id": "u_caller", "role": "caller", "name": "Caller"}); assert day["items"][0]["group"] == "callbacks" and day["items"][0]["reason"].endswith("Call back about pricing")
    other = c.post(V + "/tasks", headers={"X-Mock-User": "u_caller2"}, json={"lead_id": lead["id"], "title": "x", "due_at": due}); assert other.status_code == 404      # not their lead

def test_a_manager_can_give_the_task_to_someone_else():
    lead = make_lead(first_name="Dana", last_name="X", owner="u_caller"); c = TestClient(main.app)
    r = c.post(V + "/tasks", headers={"X-Mock-User": "u_admin"}, json={"lead_id": lead["id"], "title": "Chase", "type": "follow_up", "due_at": "2030-01-01T10:00:00+00:00", "priority": "low", "owner_id": "u_caller"})
    assert r.status_code == 201 and r.json()["owner_id"] == "u_caller"

def test_the_opportunity_form_request_works():
    lead = make_lead(first_name="Dana", last_name="X", company="Acme", owner="u_caller"); c = TestClient(main.app); h = {"X-Mock-User": "u_caller"}
    r = c.post(V + "/opportunities", headers=h, json={"lead_id": lead["id"], "name": "Acme deal", "value": 12500, "stage": "Discovery"})
    assert r.status_code == 201 and r.json()["value"] == 12500 and r.json()["stage"] == "Discovery" and r.json()["company"] == "Acme"
    assert [o["name"] for o in c.get(V + "/opportunities", headers=h).json()] == ["Acme deal"]
    assert c.post(V + "/opportunities", headers={"X-Mock-User": "u_caller2"}, json={"lead_id": lead["id"], "name": "x"}).status_code == 404
