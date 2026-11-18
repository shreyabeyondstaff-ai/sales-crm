import pytest
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
import services as sv, firebase
from conftest import ADMIN, CALLER, CALLER2, make_lead

def code(e): return e.value.detail["code"]

def person(pid, **kw):
    return {"id": pid, "first_name": "Ann", "last_name": "Lee", "email": f"{pid}@acme.com", "title": "CTO", "linkedin_url": f"https://linkedin.com/in/{pid}",
            "match_confidence": "high", "city": "London", "country": "UK", "organization": {"name": "Acme"}, **kw}

class FakeApollo:
    """Stands in for _apollo(): answers bulk_match calls and records the batches it received."""
    def __init__(self, fn): self.fn, self.calls = fn, []
    def __call__(self, path, params=None, body=None):
        assert path == "/people/bulk_match"
        self.calls.append(body["details"]); return self.fn(body["details"], len(self.calls))

def searchable(n=1, **kw): return [make_lead(first_name=f"N{i}", last_name="X", company="Acme", email="", **kw) for i in range(n)]

def run(job): sv.run_enrichment(job["id"]); return firebase.db.get("enrichmentJobs", job["id"])

def test_without_key_everything_is_pending_not_faked():
    l = make_lead(company="Acme")
    with pytest.raises(HTTPException) as e: sv.create_enrichment(ADMIN, [l["id"]], True)
    assert code(e) == "apollo_not_configured"
    assert sv.enrichment_preview(ADMIN)["configured"] is False and "pending configuration" in sv.enrichment_preview(ADMIN)["pending_note"]

def test_confirmation_and_permission_required(apollo_on):
    l = searchable()[0]
    with pytest.raises(HTTPException) as e: sv.create_enrichment(ADMIN, [l["id"]], False)
    assert code(e) == "confirm_required"
    with pytest.raises(HTTPException) as e: sv.create_enrichment(CALLER, [l["id"]], True)
    assert e.value.status_code == 403

def test_preview_counts_only_qualifying_leads(apollo_on):
    ok = searchable(3)
    done = make_lead(first_name="Done", company="Acme"); done["enrichment_state"] = "enriched"; sv.save_lead(done)
    nomatch = make_lead(first_name="Nm", company="Acme"); nomatch["enrichment_state"] = "no_match"; sv.save_lead(nomatch)
    supp = make_lead(first_name="Dnc", company="Acme"); supp["suppressed"] = True; sv.save_lead(supp)
    full = make_lead(first_name="Full", company="Acme", email="f@x.com", title="T", linkedin_url="https://l/in/f")
    make_lead(first_name="", last_name="Solo")                                         # nothing Apollo could search on
    pv = sv.enrichment_preview(ADMIN)
    assert pv["qualifying"] == 3 and pv["will_run"] == 3 and pv["api_calls"] == 1 and "credits" in pv["credit_note"]

def test_enrich_all_runs_bounded_batches_and_reports_progress(apollo_on, monkeypatch):
    leads = searchable(25)
    fake = FakeApollo(lambda d, n: {"matches": [person(f"p{n}_{i}") for i in range(len(d))], "credits_consumed": 3})
    monkeypatch.setattr(sv, "_apollo", fake)
    j = sv.create_enrichment(ADMIN, [], True, all_=True); assert j["total"] == 25 and j["scope"] == "all"
    j = run(j)
    assert [len(c) for c in fake.calls] == [10, 10, 5]                                  # Apollo's 10-per-call limit
    assert (j["status"], j["processed"], j["credits_used"]) == ("completed", 25, 9) and j["counts"] == {"enriched": 25}
    l = firebase.db.get("leads", leads[0]["id"]); assert l["enrichment_state"] == "enriched" and l["email"].endswith("@acme.com") and l["provenance"]["email"]["provider"] == "apollo"
    assert sv.enrichment_report(ADMIN)["credit_usage"] == 9

def test_no_match_is_never_marked_enriched(apollo_on, monkeypatch):
    a, b, c, d = searchable(4)
    monkeypatch.setattr(sv, "_apollo", FakeApollo(lambda det, n: {"matches": [None, person("p1", match_confidence="none"), person("p2", match_confidence="low"), person("p3")]}))
    j = run(sv.create_enrichment(ADMIN, [x["id"] for x in (a, b, c, d)], True))
    st = {i["lead_id"]: i["status"] for i in j["items"]}
    assert [st[x["id"]] for x in (a, b, c, d)] == ["no_match", "no_match", "no_match", "enriched"]
    assert [firebase.db.get("leads", x["id"])["enrichment_state"] for x in (a, b, c, d)] == ["no_match", "no_match", "no_match", "enriched"]
    assert firebase.db.get("leads", a["id"])["email"] == ""                              # nothing was written for non-matches

def test_verified_and_existing_values_are_never_overwritten_but_masked_names_are_fixed(apollo_on, monkeypatch):
    l = make_lead(first_name="Ann", last_name="Le***", company="Acme", title="Mine", email="")
    l["verified_fields"] = ["title"]; sv.save_lead(l)
    monkeypatch.setattr(sv, "_apollo", FakeApollo(lambda d, n: {"matches": [person("p1", title="Theirs", last_name="Leeson")]}))
    j = run(sv.create_enrichment(ADMIN, [l["id"]], True)); got = firebase.db.get("leads", l["id"])
    assert got["title"] == "Mine" and got["last_name"] == "Leeson" and got["full_name"] == "Ann Leeson" and got["email"] == "p1@acme.com"
    assert "title" not in j["items"][0]["updated"] and "last_name" in j["items"][0]["updated"]

def test_locked_email_placeholder_is_not_stored(apollo_on, monkeypatch):
    l = searchable()[0]; monkeypatch.setattr(sv, "_apollo", FakeApollo(lambda d, n: {"matches": [person("p1", email="email_not_unlocked@domain.com")]}))
    run(sv.create_enrichment(ADMIN, [l["id"]], True)); assert firebase.db.get("leads", l["id"])["email"] == ""

def test_duplicate_concurrent_jobs_are_prevented(apollo_on):
    ls = searchable(2)
    sv.create_enrichment(ADMIN, [], True, all_=True)                                     # job 1 is queued
    with pytest.raises(HTTPException) as e: sv.create_enrichment(ADMIN, [], True, all_=True)
    assert code(e) == "job_running"
    with pytest.raises(HTTPException) as e: sv.create_enrichment(ADMIN, [ls[0]["id"]], True)
    assert code(e) == "duplicate_request"
    assert sv.enrichment_preview(ADMIN)["busy"] is True

def test_a_stuck_job_expires_and_stops_blocking(apollo_on):
    searchable(1); j = sv.create_enrichment(ADMIN, [], True, all_=True)
    j["at"] = (datetime.now(timezone.utc) - timedelta(minutes=sv.STALE_JOB_MIN + 5)).isoformat(timespec="seconds"); firebase.db.put("enrichmentJobs", j["id"], j)
    j2 = sv.create_enrichment(ADMIN, [], True, all_=True); assert j2["id"] != j["id"]
    assert firebase.db.get("enrichmentJobs", j["id"])["status"] == "failed"

def test_bad_key_stops_the_job_and_marks_the_rest(apollo_on, monkeypatch):
    searchable(25)
    def boom(d, n):
        if n == 1: return {"matches": [person(f"p{i}") for i in range(len(d))]}
        raise HTTPException(502, {"code": "apollo_auth", "message": "Apollo rejected the API key"})
    fake = FakeApollo(boom); monkeypatch.setattr(sv, "_apollo", fake)
    j = run(sv.create_enrichment(ADMIN, [], True, all_=True))
    assert len(fake.calls) == 2 and j["status"] == "partial" and j["counts"]["enriched"] == 10 and j["counts"]["error"] == 15 and "rejected" in j["error"]

def test_a_failed_batch_is_partial_success_and_retry_targets_only_failures(apollo_on, monkeypatch):
    searchable(15)
    def flaky(d, n):
        if n == 2: raise HTTPException(502, {"code": "apollo_error", "message": "Apollo returned an error (500)."})
        return {"matches": [person(f"p{n}_{i}") for i in range(len(d))]}
    monkeypatch.setattr(sv, "_apollo", FakeApollo(flaky))
    j = run(sv.create_enrichment(ADMIN, [], True, all_=True))
    assert j["status"] == "partial" and j["counts"] == {"enriched": 10, "error": 5}
    r = sv.retry_enrichment(ADMIN, j["id"]); assert r["total"] == 5 and r["scope"] == "selected"

def test_unexpected_response_shape_is_an_error_not_a_guess(apollo_on, monkeypatch):
    searchable(3); monkeypatch.setattr(sv, "_apollo", FakeApollo(lambda d, n: {"matches": [person("p1")]}))     # 1 match for 3 people
    j = run(sv.create_enrichment(ADMIN, [], True, all_=True)); assert j["status"] == "failed" and j["counts"] == {"error": 3}
    assert all(firebase.db.get("leads", i["lead_id"])["enrichment_state"] == "not_enriched" for i in j["items"])

def test_unsearchable_lead_is_skipped_without_an_api_call(apollo_on, monkeypatch):
    l = make_lead(first_name="", last_name="Solo"); fake = FakeApollo(lambda d, n: {"matches": []}); monkeypatch.setattr(sv, "_apollo", fake)
    j = run(sv.create_enrichment(ADMIN, [l["id"]], True)); assert fake.calls == [] and j["items"][0]["status"] == "no_match" and "Not enough data" in j["items"][0]["note"]
    assert firebase.db.get("leads", l["id"])["enrichment_state"] == "not_enriched"

def test_job_is_capped_and_reports_what_is_left(apollo_on):
    searchable(sv.ENRICH_MAX_JOB + 5)
    pv = sv.enrichment_preview(ADMIN); assert pv["qualifying"] == sv.ENRICH_MAX_JOB + 5 and pv["will_run"] == sv.ENRICH_MAX_JOB and pv["remaining_after"] == 5
    assert sv.create_enrichment(ADMIN, [], True, all_=True)["total"] == sv.ENRICH_MAX_JOB

def test_enrich_all_respects_record_scoping(apollo_on):
    mine = searchable(2)
    for l in mine: l["owner_id"] = CALLER["id"]; sv.save_lead(l)
    other = make_lead(first_name="O", company="Acme"); other["owner_id"] = CALLER2["id"]; sv.save_lead(other)
    assert len(sv.enrich_candidates(CALLER)) == 2                                        # only their own leads
