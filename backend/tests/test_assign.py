import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import services as sv, firebase, main
from schemas import AssignPerson
from conftest import ADMIN, CALLER, CALLER2, make_lead

P = lambda u, n: AssignPerson(user_id=u, count=n)
OUTREACH = {"id": "u_out", "name": "Outreach", "role": "outreach"}

def lead(name, **kw): return make_lead(first_name=name, last_name="X", **kw)
def owner(lid): return firebase.db.get("leads", lid)["owner_id"]

def ranked(n=10, **kw):
    """n unassigned leads with strictly descending fit: the first has the most signal/phone/seniority."""
    out = []
    for i in range(n):
        out.append(lead(f"L{i:02d}", company="Acme", email=f"l{i}@x.com", phone="+44 7700 900%03d" % i if i < 6 else "", signal="Hiring SDRs" if i < 3 else "", title="CEO" if i < 2 else "Analyst", **kw))
    return out

# ---------------------------------------------------------------- fit score
def test_fit_score_is_transparent_and_adds_up():
    full = lead("A", title="Chief Revenue Officer", company="Acme", email="a@x.com", phone="+44 7700 900111", linkedin_url="https://l/in/a", signal="Raised funding", signal_url="https://n/x")
    sc, why = sv.fit_score(full); assert sc == sum(w["points"] for w in why) == 95 and why[0]["label"].startswith("Signal: Raised funding")
    assert sv.fit_score(lead("B"))[0] == 0 and sv.fit_score(lead("C", signal="x"))[0] == 35
    assert sv.fit_score(lead("D", title="Head of Sales"))[0] == 20 and sv.fit_score(lead("E", title="Senior Engineer"))[0] == 5 and sv.fit_score(lead("F", title="Sales Manager"))[0] == 10
    assert sv.fit_score(lead("G", signal="s", signal_url="u", title="CEO", phone="+44 7700 900111", email="g@x.com", company="C", linkedin_url="l"))[0] <= 100

# ---------------------------------------------------------------- pool
def test_pool_ranks_best_first_and_only_contains_assignable_leads():
    ls = ranked(6); assigned = lead("Owned", signal="x", owner="u_caller"); dnc = lead("Dnc", signal="x"); dnc["suppressed"] = True; sv.save_lead(dnc)
    won = lead("Won", signal="x"); won["stage"] = "Won"; sv.save_lead(won); arch = lead("Arch", signal="x"); arch["archived"] = True; sv.save_lead(arch)
    pool = sv.assignment_pool(ADMIN); names = [x["full_name"] for x in pool["items"]]
    assert pool["total"] == 6 and names[:2] == ["L00 X", "L01 X"] and "Owned X" not in names and "Dnc X" not in names and "Won X" not in names and "Arch X" not in names
    assert [x["fit"] for x in pool["items"]] == sorted((x["fit"] for x in pool["items"]), reverse=True) and pool["items"][0]["why"]

def test_pool_filters():
    ranked(8); firebase.db.put("campaigns", "c1", {"name": "Lst", "channel": "call", "archived": False}); l = sv.get_lead(ADMIN, [x for x in firebase.db.list("leads") if x["first_name"] == "L07"][0]["id"]); l["campaign_id"] = "c1"; sv.save_lead(l)
    n = lambda **f: sv.assignment_pool(ADMIN, f)["total"]
    assert n() == 8 and n(signal=True) == 3 and n(phone=True) == 6 and n(min_fit=40) == 3 and n(campaign="c1") == 1 and n(campaign="none") == 7 and n(channel="call") == 1 and n(source="Manual") == 8

def test_pool_and_assignees_need_the_assign_permission():
    for fn in (lambda: sv.assignment_pool(CALLER), lambda: sv.assignees(CALLER), lambda: sv.assign_run(CALLER, [P("u_caller", 1)], "round_robin", {}, True)):
        with pytest.raises(HTTPException) as e: fn()
        assert e.value.status_code == 403

def test_assignees_show_load_and_exclude_non_workers():
    a = lead("A", owner="u_caller"); lead("B", owner="u_caller"); a["last_activity_at"] = sv.now(); sv.save_lead(a)
    rows = {u["id"]: u for u in sv.assignees(ADMIN)}; assert set(rows) == {"u_caller", "u_caller2"} and (rows["u_caller"]["open_leads"], rows["u_caller"]["untouched"]) == (2, 1) and rows["u_caller2"]["open_leads"] == 0

# ---------------------------------------------------------------- assigning
def test_top_first_gives_the_best_leads_to_the_first_person():
    ranked(10); r = sv.assign_run(ADMIN, [P("u_caller", 3), P("u_caller2", 2)], "top_first", {}, dry_run=False)
    by = {}; [by.setdefault(l["owner_id"], []).append(l["first_name"]) for l in firebase.db.list("leads") if l.get("owner_id")]
    assert sorted(by["u_caller"]) == ["L00", "L01", "L02"] and sorted(by["u_caller2"]) == ["L03", "L04"] and r["assigned"] == 5 and r["shortfall"] == 0

def test_round_robin_deals_strong_and_weak_leads_evenly():
    ranked(10); sv.assign_run(ADMIN, [P("u_caller", 4), P("u_caller2", 4)], "round_robin", {}, dry_run=False)
    by = {}; [by.setdefault(l["owner_id"], []).append(l["first_name"]) for l in firebase.db.list("leads") if l.get("owner_id")]
    assert sorted(by["u_caller"]) == ["L00", "L02", "L04", "L06"] and sorted(by["u_caller2"]) == ["L01", "L03", "L05", "L07"]

def test_round_robin_respects_different_amounts():
    ranked(10); r = sv.assign_run(ADMIN, [P("u_caller", 2), P("u_caller2", 5)], "round_robin", {}, dry_run=False)
    assert {p["user_id"]: p["assigned"] for p in r["plan"]} == {"u_caller": 2, "u_caller2": 5}

def test_preview_changes_nothing_and_reports_the_plan():
    ranked(10); r = sv.assign_run(ADMIN, [P("u_caller", 3)], "top_first", {}, dry_run=True)
    assert r["dry_run"] and r["plan"][0]["assigned"] == 3 and r["plan"][0]["sample"][0]["name"] == "L00 X" and r["plan"][0]["avg_fit"] > 0
    assert all(not l.get("owner_id") for l in firebase.db.list("leads")) and firebase.db.list("tasks") == [] and firebase.db.list("auditLogs") == []

def test_asking_for_more_than_exists_assigns_what_there_is_and_reports_the_shortfall():
    ranked(4); r = sv.assign_run(ADMIN, [P("u_caller", 3), P("u_caller2", 3)], "top_first", {}, dry_run=False)
    assert r["assigned"] == 4 and r["shortfall"] == 2 and [p["assigned"] for p in r["plan"]] == [3, 1]

def test_filters_apply_to_what_gets_assigned():
    ranked(10); sv.assign_run(ADMIN, [P("u_caller", 50)], "top_first", {"signal": True}, dry_run=False)
    assert sorted(l["first_name"] for l in firebase.db.list("leads") if l.get("owner_id")) == ["L00", "L01", "L02"]

def test_assigned_leads_reach_the_callers_queue_with_a_first_contact_task_and_an_audit_trail():
    ranked(5); sv.assign_run(ADMIN, [P("u_caller", 3)], "top_first", {}, dry_run=False)
    q = sv.workspace_queue(CALLER); assert {x["lead"]["first_name"] for x in q} == {"L00", "L01", "L02"} and sv.workspace_queue(CALLER2) == []
    assert sorted(t["title"] for t in firebase.db.list("tasks")) == ["First contact"] * 3 and all(t["owner_id"] == "u_caller" for t in firebase.db.list("tasks"))
    acts = [a["action"] for a in firebase.db.list("auditLogs")]; assert acts.count("lead.assign") == 3 and "assign.bulk" in acts

def test_a_second_run_picks_up_where_the_first_stopped():
    ranked(10); sv.assign_run(ADMIN, [P("u_caller", 3)], "top_first", {}, dry_run=False); sv.assign_run(ADMIN, [P("u_caller2", 3)], "top_first", {}, dry_run=False)
    by = {}; [by.setdefault(l["owner_id"], set()).add(l["first_name"]) for l in firebase.db.list("leads") if l.get("owner_id")]
    assert by["u_caller"] == {"L00", "L01", "L02"} and by["u_caller2"] == {"L03", "L04", "L05"}

@pytest.mark.parametrize("people,mode,code", [([P("u_admin", 1)], "round_robin", "bad_assignee"), ([P("ghost", 1)], "round_robin", "bad_assignee"), ([P("u_caller", 0)], "round_robin", "bad_count"),
                                              ([P("u_caller", 501)], "round_robin", "bad_count"), ([P("u_caller", 300), P("u_caller2", 300)], "round_robin", "too_many"), ([], "round_robin", "nobody"),
                                              ([P("u_caller", 1)], "random", "bad_mode")])
def test_bad_requests_are_rejected_before_anything_changes(people, mode, code):
    ranked(3)
    with pytest.raises(HTTPException) as e: sv.assign_run(ADMIN, people, mode, {}, dry_run=False)
    assert e.value.detail["code"] == code and all(not l.get("owner_id") for l in firebase.db.list("leads"))

def test_inactive_users_cannot_receive_leads():
    firebase.db.put("users", "u_off", {"id": "u_off", "name": "Off", "role": "caller", "active": False}); ranked(2)
    with pytest.raises(HTTPException) as e: sv.assign_run(ADMIN, [P("u_off", 1)], "top_first", {}, dry_run=False)
    assert e.value.detail["code"] == "bad_assignee"

def test_api_routes_end_to_end_and_triggers_the_today_refresh(monkeypatch):
    ranked(6); ran = []; monkeypatch.setattr(main.ag, "run_agents", lambda *a, **k: ran.append(a)); c = TestClient(main.app); h = lambda u: {"X-Mock-User": u}; V = "/api/v1"
    assert c.get(V + "/assign/pool", headers=h("u_caller")).status_code == 403 and c.post(V + "/assign/run", headers=h("u_caller"), json={"people": []}).status_code == 403
    pool = c.get(V + "/assign/pool?signal=true&limit=2", headers=h("u_admin")).json(); assert pool["total"] == 3 and len(pool["items"]) == 2
    assert {p["id"] for p in c.get(V + "/assign/people", headers=h("u_admin")).json()} == {"u_caller", "u_caller2"}
    body = {"people": [{"user_id": "u_caller", "count": 2}, {"user_id": "u_caller2", "count": 2}], "mode": "round_robin", "filters": {"min_fit": 1}}
    prev = c.post(V + "/assign/run", headers=h("u_admin"), json=body).json(); assert prev["dry_run"] and ran == []
    done = c.post(V + "/assign/run", headers=h("u_admin"), json={**body, "dry_run": False}).json(); assert done["assigned"] == 4 and ran                  # Today refresh started
    assert len(c.get(V + "/workspace/queue", headers=h("u_caller")).json()) == 2
