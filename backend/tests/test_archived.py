from fastapi.testclient import TestClient
import services as sv, agents as ag, firebase, main
from conftest import ADMIN, CALLER, make_lead

def camp(cid, name, archived=False): return firebase.db.put("campaigns", cid, {"name": name, "channel": "email", "archived": archived, "color": "#123456"})
def lead(name, cid, owner="u_caller"):
    l = make_lead(first_name=name, last_name="X", owner=owner); l["campaign_id"] = cid; sv.save_lead(l); return l
names = lambda d: sorted(x["lead"]["first_name"] for x in d["items"])

def test_leads_of_an_archived_campaign_leave_my_day_and_come_back_when_it_is_unarchived():
    camp("live", "Live list"); camp("old", "Old sequence", archived=True); lead("Keep", "live"); lead("Hidden", "old"); lead("NoCamp", None)
    d = ag.my_day(CALLER); assert names(d) == ["Keep", "NoCamp"] and d["tiles"]["assigned"] == 2
    firebase.db.put("campaigns", "old", {**firebase.db.get("campaigns", "old"), "archived": False}); assert names(ag.my_day(CALLER)) == ["Hidden", "Keep", "NoCamp"]

def test_the_sales_activity_queue_hides_them_too():
    camp("old", "Old sequence", archived=True); lead("Hidden", "old"); lead("Shown", None)
    assert [x["lead"]["first_name"] for x in sv.workspace_queue(CALLER)] == ["Shown"]

def test_hidden_leads_stay_assigned_and_visible_on_the_leads_page_with_their_history():
    camp("old", "Old sequence", archived=True); l = lead("Hidden", "old"); c = TestClient(main.app); h = {"X-Mock-User": "u_caller"}
    assert c.get(f"/api/v1/leads/{l['id']}", headers=h).json()["owner_id"] == "u_caller" and firebase.db.get("leads", l["id"])["campaign_id"] == "old"

def test_the_page_is_told_the_names_of_archived_campaigns_so_nothing_is_called_no_campaign():
    camp("live", "Live list"); camp("old", "Old sequence", archived=True); m = TestClient(main.app).get("/api/v1/meta", headers={"X-Mock-User": "u_admin"}).json()
    assert [c["name"] for c in m["campaigns"]] == ["Live list"] and m["archived_campaigns"] == [{"id": "old", "name": "Old sequence", "channel": "email", "color": "#123456"}]   # pickers stay clean; labels can still be resolved

def test_a_sequence_that_stops_being_active_hides_its_leads_from_the_caller(apollo_on, monkeypatch):
    seqs = [{"id": "s1", "name": "Seq", "active": True}]
    contacts = [{"id": "c1", "person_id": "p1", "first_name": "Ann", "last_name": "L", "email": "a@x.com", "contact_campaign_statuses": [{"emailer_campaign_id": "s1", "status": "active", "added_at": "1"}]}]
    monkeypatch.setattr(sv, "_apollo", lambda path, params=None, body=None, method="POST": {"emailer_campaigns": seqs} if path.startswith("/emailer_campaigns") else {"emailer_messages": []} if path.startswith("/emailer_messages") else {"contacts": contacts})
    sv.sync_apollo_campaigns(ADMIN); sv.assign(ADMIN, firebase.db.list("leads")[0]["id"], "u_caller"); assert names(ag.my_day(CALLER)) == ["Ann"]
    seqs[0]["active"] = False; sv.sync_apollo_campaigns(ADMIN)                                              # the sequence was switched off in Apollo: its campaign is archived
    assert ag.my_day(CALLER)["items"] == [] and firebase.db.list("leads")[0]["owner_id"] == "u_caller"
