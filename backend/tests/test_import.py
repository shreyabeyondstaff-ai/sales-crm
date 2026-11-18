import io
import pytest
from fastapi import HTTPException
import services as sv, firebase
from schemas import ImportOptions
from conftest import ADMIN, CALLER, make_lead, csv_bytes

CSV = "First Name,Last Name,Email,Company,Signal\nAnn,Lee,ann@acme.com,Acme,Hiring 3 SDRs\nBob,Ray,bob@acme.com,Acme,\n,,,,\nCy,Zed,not-an-email,Zed,Raised funding\nAnn,Lee,ann@acme.com,Acme,dup in file\n"

def opts(mapping, **kw): return ImportOptions(mapping=mapping, **kw)

def test_signal_column_is_auto_mapped_and_imported_with_campaign():
    camp = firebase.db.put("campaigns", "c1", {"name": "C", "channel": "call", "archived": False})
    b = sv.start_import(ADMIN, "leads.csv", csv_bytes(CSV))
    assert b["suggested_mapping"]["Signal"] == "signal"
    mp = b["suggested_mapping"]
    pv = sv.preview_import(ADMIN, b["id"], opts(mp))
    assert pv["valid_new"] == 2 and pv["error_count"] == 3          # blank row, bad email, duplicate inside file
    done = sv.confirm_import(ADMIN, b["id"], opts(mp, source="File Upload", campaign_id="c1"))
    assert (done["created"], done["invalid"], done["skipped"]) == (2, 2, 1)   # blank row + bad email invalid; second Ann skipped as duplicate
    leads = {l["email"]: l for l in firebase.db.list("leads")}
    assert leads["ann@acme.com"]["signal"] == "Hiring 3 SDRs" and leads["ann@acme.com"]["campaign_id"] == "c1"
    assert leads["bob@acme.com"]["signal"] == ""

def test_xlsx_with_signal_column():
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.append(["First name", "Email", "Signal", "Signal URL"]); ws.append(["Dee", "dee@x.com", "New CTO", "https://news.example/cto"])
    buf = io.BytesIO(); wb.save(buf)
    b = sv.start_import(ADMIN, "l.xlsx", buf.getvalue()); assert b["suggested_mapping"]["Signal"] == "signal" and b["suggested_mapping"]["Signal URL"] == "signal_url"
    sv.confirm_import(ADMIN, b["id"], opts(b["suggested_mapping"]))
    l = firebase.db.list("leads")[0]; assert l["signal"] == "New CTO" and l["signal_url"].startswith("https://")

def test_existing_duplicates_are_skipped_and_never_overwritten():
    make_lead(email="ann@acme.com", company="Mine")
    b = sv.start_import(ADMIN, "l.csv", csv_bytes("First Name,Email,Company\nAnn,ann@acme.com,Theirs\nNew,new@acme.com,N\n"))
    done = sv.confirm_import(ADMIN, b["id"], opts(b["suggested_mapping"], policy="skip"))
    assert done["created"] == 1 and done["skipped"] == 1
    assert [l for l in firebase.db.list("leads") if l["email"] == "ann@acme.com"][0]["company"] == "Mine"

def test_confirm_twice_is_refused_and_rows_are_cleaned_up():
    b = sv.start_import(ADMIN, "l.csv", csv_bytes("Email\na@x.com\n"))
    sv.confirm_import(ADMIN, b["id"], opts(b["suggested_mapping"]))
    assert sv.load_rows(b["id"]) is None
    with pytest.raises(HTTPException) as e: sv.confirm_import(ADMIN, b["id"], opts(b["suggested_mapping"]))
    assert e.value.status_code == 409

def test_rollback_removes_leads_but_is_blocked_after_activity():
    b = sv.start_import(ADMIN, "l.csv", csv_bytes("First Name,Email\nA,a@x.com\nB,b@x.com\n"))
    sv.confirm_import(ADMIN, b["id"], opts(b["suggested_mapping"]))
    assert sv.rollback_import(ADMIN, b["id"])["status"] == "rolled_back" and firebase.db.list("leads") == []
    b2 = sv.start_import(ADMIN, "l2.csv", csv_bytes("First Name,Email\nA,a@x.com\n"))
    done = sv.confirm_import(ADMIN, b2["id"], opts(b2["suggested_mapping"]))
    firebase.db.put("activities", "a1", {"lead_id": done["created_ids"][0], "at": sv.now(), "type": "email", "outcome": "Sent"})
    with pytest.raises(HTTPException) as e: sv.rollback_import(ADMIN, b2["id"])
    assert e.value.status_code == 409 and len(firebase.db.list("leads")) == 1

def test_bad_files_and_permissions():
    with pytest.raises(HTTPException): sv.start_import(ADMIN, "x.txt", b"hi")
    b = sv.start_import(ADMIN, "l.csv", csv_bytes("Email\na@x.com\n"))
    with pytest.raises(HTTPException) as e: sv.confirm_import(CALLER, b["id"], opts(b["suggested_mapping"]))
    assert e.value.status_code == 403

def test_large_import_rows_are_chunked_under_the_document_limit():
    rows = [{"a": "x" * 2000, "n": str(i)} for i in range(1000)]; sv.save_rows("big", rows)
    chunks = firebase.db.list("importBatches/big/rows"); assert len(chunks) > 1
    assert all(len(str(c).encode()) < 900_000 for c in chunks) and sv.load_rows("big") == rows

def test_csv_formula_injection_is_neutralised_on_export():
    assert sv.csv_safe("=cmd()") == "'=cmd()" and sv.csv_safe("ok") == "ok"
