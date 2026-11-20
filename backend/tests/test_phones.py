import pytest
import services as sv, firebase
from schemas import LeadPatch
from conftest import ADMIN, make_lead

def pn(num, typ, pos=0): return {"sanitized_number": num, "raw_number": num, "type": typ, "position": pos}
HQ, MOB = "+448081641088", "+447552279412"

def contact(i, phones, sp=None):
    return {"id": f"c{i}", "person_id": f"p{i}", "first_name": f"N{i}", "last_name": "Lee", "organization_name": "Acme", "email": f"n{i}@acme.com", "phone_numbers": phones, **({"sanitized_phone": sp} if sp else {})}

def test_a_direct_number_beats_the_company_switchboard_whatever_the_order():
    assert sv._best_phone(contact(1, [pn(HQ, "work_hq", 0), pn(MOB, "mobile", 1)])) == (MOB, "mobile")
    assert sv._best_phone(contact(1, [pn(MOB, "mobile", 0), pn(HQ, "work_hq", 1)])) == (MOB, "mobile")

def test_apollos_default_number_is_used_and_typed_from_its_entry():
    assert sv._best_phone(contact(1, [pn(HQ, "work_hq", 0), pn(MOB, "mobile", 1)], sp=MOB)) == (MOB, "mobile")
    assert sv._best_phone(contact(1, [], sp="+441234")) == ("+441234", "direct")                    # no entry to read the kind from: still a contact-level number

def test_only_a_switchboard_is_kept_but_labelled_and_nothing_gives_nothing():
    assert sv._best_phone(contact(1, [pn(HQ, "work_hq")])) == (HQ, "work_hq") and sv._best_phone(contact(1, [])) == ("", "") and sv._best_phone({}) == ("", "")

def test_the_lead_made_from_a_contact_records_which_kind_of_number_it_has():
    assert sv._contact_lead(contact(1, [pn(HQ, "work_hq")]), "c", "L")["phone_type"] == "work_hq" and sv._contact_lead(contact(2, [pn(MOB, "mobile")], sp=MOB), "c", "L")["phone_type"] == "mobile"
    l = sv.new_lead(sv._contact_lead(contact(1, [pn(HQ, "work_hq")]), "c", "L"), ADMIN); assert (l["phone"], l["phone_type"]) == (HQ, "work_hq")

def synced(monkeypatch, members):
    monkeypatch.setattr(sv, "_apollo", lambda path, params=None, body=None, method="POST": [{"id": "L1", "name": "L", "modality": "contacts"}] if path == "/labels" else {"contacts": members})

def test_a_resync_upgrades_the_switchboard_to_the_direct_line_that_apollo_has_since_found(apollo_on, monkeypatch):
    members = [contact(1, [pn(HQ, "work_hq")]), contact(2, [pn(HQ, "work_hq")])]; synced(monkeypatch, members); sv.sync_apollo_lists(ADMIN, ["L1"])
    assert {l["first_name"]: l["phone"] for l in firebase.db.list("leads")} == {"N1": HQ, "N2": HQ}
    members[0] = contact(1, [pn(MOB, "mobile", 0), pn(HQ, "work_hq", 1)], sp=MOB)                         # Apollo now has Ben's mobile
    r = sv.sync_apollo_lists(ADMIN, ["L1"]); ben = [l for l in firebase.db.list("leads") if l["first_name"] == "N1"][0]; other = [l for l in firebase.db.list("leads") if l["first_name"] == "N2"][0]
    assert r["phones_updated"] == 1 and r["leads_created"] == 0 and (ben["phone"], ben["phone_type"]) == (MOB, "mobile") and ben["provenance"]["phone"]["prev"] == HQ and (other["phone"], other["phone_type"]) == (HQ, "work_hq")
    assert sv.sync_apollo_lists(ADMIN, ["L1"])["phones_updated"] == 0                                    # stable: nothing changes again

def test_old_leads_without_a_label_get_one_and_a_typed_number_is_never_overwritten(apollo_on, monkeypatch):
    mine = make_lead(first_name="N1", last_name="Lee", email="n1@acme.com", phone="+441111111111"); sv.update_lead(ADMIN, mine["id"], LeadPatch(phone="+442222222222"))     # a person typed it
    old = make_lead(first_name="N2", last_name="Lee", email="n2@acme.com", phone=HQ); old["source"] = "Apollo"; sv.save_lead(old)                                                # synced before types existed
    csv = make_lead(first_name="N3", last_name="Lee", email="n3@acme.com", phone="+443333333333")                                                                              # from a file upload
    synced(monkeypatch, [contact(1, [pn(MOB, "mobile")], sp=MOB), contact(2, [pn(HQ, "work_hq")]), contact(3, [pn(MOB, "mobile")], sp=MOB)])
    r = sv.sync_apollo_lists(ADMIN, ["L1"]); g = lambda lid: firebase.db.get("leads", lid)
    assert g(mine["id"])["phone"] == "+442222222222" and g(mine["id"])["phone_type"] == ""                # typed by a person: untouched, kind unknown
    assert (g(old["id"])["phone"], g(old["id"])["phone_type"]) == (HQ, "work_hq") and "phone" not in g(old["id"])["provenance"]        # same number: only labelled
    assert g(csv["id"])["phone"] == "+443333333333" and r["phones_updated"] == 0                          # not Apollo's own data: left alone

def test_a_lead_with_no_number_gets_one_and_enriched_numbers_are_respected(apollo_on, monkeypatch):
    empty = make_lead(first_name="N1", last_name="Lee", email="n1@acme.com"); enr = make_lead(first_name="N2", last_name="Lee", email="n2@acme.com", phone="+449999999999"); enr["provenance"] = {"phone": {"provider": "apollo", "at": "x", "prev": ""}}; sv.save_lead(enr)
    synced(monkeypatch, [contact(1, [pn(HQ, "work_hq")]), contact(2, [pn(MOB, "mobile")], sp=MOB)]); sv.sync_apollo_lists(ADMIN, ["L1"])
    assert (firebase.db.get("leads", empty["id"])["phone"], firebase.db.get("leads", empty["id"])["phone_type"]) == (HQ, "work_hq") and firebase.db.get("leads", enr["id"])["phone"] == "+449999999999"

def test_a_switchboard_number_counts_for_less_in_the_fit_score():
    direct = make_lead(first_name="A", last_name="X", phone=MOB); hq = make_lead(first_name="B", last_name="X", phone=HQ); hq["phone_type"] = "work_hq"; none = make_lead(first_name="C", last_name="X")
    (d, dw), (h, hw) = sv.fit_score(direct), sv.fit_score(hq)
    assert (d, h, sv.fit_score(none)[0]) == (15, 5, 0) and dw[0]["label"] == "Has a direct phone number" and hw[0]["label"] == "Has only a company switchboard number"

def test_editing_a_number_by_hand_clears_its_kind():
    l = make_lead(first_name="A", last_name="X", phone=HQ); l["phone_type"] = "work_hq"; sv.save_lead(l)
    assert sv.update_lead(ADMIN, l["id"], LeadPatch(phone="+441234567890"))["phone_type"] == ""

def test_my_day_rows_carry_the_number_kind_and_the_apollo_ids():
    import agents as ag
    l = make_lead(first_name="A", last_name="X", phone=HQ, owner="u_caller"); l["phone_type"] = "work_hq"; l["apollo_contact_id"] = "c9"; sv.save_lead(l)
    row = ag.my_day({"id": "u_caller", "role": "caller", "name": "C"})["items"][0]["lead"]; assert (row["phone_type"], row["apollo_contact_id"]) == ("work_hq", "c9")
