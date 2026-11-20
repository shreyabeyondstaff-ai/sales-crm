"""Company-level data. People lists (from Apollo) say who to call; company lists say WHY to call and what to say.

A company list is a named group of companies (like a campaign is a group of leads). Each company has a standard profile (industry, size,
location...) plus "cues": the custom columns of the export (signal, hook, tailored opening, pitch script, priority...). A lead is matched to its
company by company name (website domain as a fallback) every time it is read, so imports in either order just work and nothing goes stale.
Collections: companyLists(name, color, archived, field_order[]) · companies(list_id, name, name_key, domain, profile fields, cues[{k, v}]).
Cues are an ARRAY, not a map, because Firestore does not keep map key order and the export's column order is the order callers should read in."""
import re, unicodedata
from collections import Counter
from functools import lru_cache
from firebase import db, now, uid

LEGAL = {"ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation", "gmbh", "plc", "co", "company", "the", "sa", "ag", "bv", "pvt", "private", "pte", "llp", "lp"}
COLORS = ["#5a5fe0", "#1fa971", "#f0a020", "#e5484d", "#2f9bf0", "#a548c7", "#14877f"]
MAX_ROWS = 5000

@lru_cache(maxsize=60000)
def name_key(s):
    """'Fáilte Solar UK Ltd.' and 'failte solar uk' give the same key."""
    t = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace("&", " and ")
    toks = re.sub(r"[^a-z0-9]+", " ", t).split()
    return " ".join([x for x in toks if x not in LEGAL] or toks)

def aliases(name):
    """Every name a company may be known by. Apollo account names are often decorated: 'Facetheory | B-Corp | Sustainable Skincare' is 'Facetheory' on
    a person's record, and 'Blue Vanilla (Blue Vanilla Clothing Limited)' is either half. The full name always wins; aliases only fill gaps."""
    n = name or ""; out = [name_key(n)]
    head = re.split(r"\s*[|(\u2013\u2014:]\s*|\s+-\s+", n, maxsplit=1)[0]
    if head and head != n: out.append(name_key(head))
    out += [name_key(m) for m in re.findall(r"\(([^)]+)\)", n)]
    return [k for k in dict.fromkeys(out) if len(k) >= 3]

def domain(w): return re.sub(r"^https?://(www\.)?|[/?#].*$", "", (w or "").strip().lower())

# ----------------------------------------------------------------------------- file columns
STD = {"company name": "name", "website": "website", "company linkedin url": "linkedin_url", "industry": "industry", "# employees": "employees", "company city": "city",
       "company state": "state", "company country": "country", "company phone": "phone", "short description": "description", "keywords": "keywords", "technologies": "technologies",
       "annual revenue": "annual_revenue", "total funding": "total_funding", "latest funding": "latest_funding", "latest funding amount": "latest_funding_amount",
       "founded year": "founded_year", "apollo account id": "apollo_account_id", "primary intent topic": "intent_topic", "primary intent score": "intent_score"}
SKIP = {"company name for emails", "account stage", "lists", "account owner", "facebook url", "twitter url", "company street", "company postal code", "company address",
        "number of retail locations", "sic codes", "naics codes", "logo url", "parent company (apollo data)", "parent company (apollo data - organization id)", "ultimate parent company name",
        "ultimate parent company website", "number of suborganizations", "suborganization domains", "suborganization names", "apollo org id", "apollo parent org id", "apollo parent org name",
        "apollo parent org domain", "secondary intent topic", "secondary intent score", "last raised at"}
NAME_HEADERS = ("company name", "company", "account name", "name", "organization", "organisation")
CAPS = {"description": 1200, "technologies": 1500, "keywords": 600}
PROFILE = ("website", "linkedin_url", "industry", "employees", "city", "state", "country", "phone", "description", "keywords", "technologies", "annual_revenue", "total_funding",
           "latest_funding", "latest_funding_amount", "founded_year", "apollo_account_id", "intent_topic", "intent_score")
CUE_CAP = 6000

def clean_label(h):
    """'ICP Fit 7367 0715075629' -> 'ICP Fit' (Apollo appends internal ids to the names of its AI columns)."""
    return re.sub(r"\s+\d{3,}(?:\s+\d{3,})*$", "", (h or "").strip()).strip()

def _v(x): return ("" if x is None else str(x)).strip().lstrip("'").strip()      # Excel prefixes text-formatted phone numbers with an apostrophe

def classify_columns(headers, rows):
    """Header -> ('name'|'std'|'cue'|None, key). Everything the export holds that is not a standard Apollo column becomes a cue, in file order."""
    lower = {h: (h or "").strip().lower() for h in headers}
    name_h = next((h for k in NAME_HEADERS for h in headers if lower[h] == k), None)
    out, seen = {}, {}
    for h in headers:
        k = lower[h]
        if not k: continue
        if h == name_h: out[h] = ("name", "name")
        elif k in STD: out[h] = ("std", STD[k])
        elif k in SKIP: out[h] = (None, "")
        elif any(_v(r.get(h)) for r in rows):
            lab = clean_label(h) or h.strip(); n = seen.get(lab.lower(), 0); seen[lab.lower()] = n + 1
            out[h] = ("cue", lab if not n else f"{lab} ({n + 1})")
        else: out[h] = (None, "")
    return name_h, out

# ----------------------------------------------------------------------------- lookup (lead -> company)
_memo = {"k": None, "key": {}, "dom": {}, "lists": {}, "lc": None}
def _index():
    k = (db.ver("companies"), db.ver("companyLists"))
    if _memo["k"] != k:
        lists = {x["id"]: x for x in db.list("companyLists") if not x.get("archived")}; bykey, bydom = {}, {}
        cs = sorted((c for c in db.list("companies") if c.get("list_id") in lists), key=lambda c: c.get("updated_at") or "")           # newest list wins a clash
        for c in cs:
            bykey[c.get("name_key") or name_key(c["name"])] = c
            if c.get("domain"): bydom[c["domain"]] = c
        for c in cs:
            for a in aliases(c["name"])[1:]: bykey.setdefault(a, c)
        _memo.update(k=k, key=bykey, dom=bydom, lists=lists, lc=None)
    return _memo

def for_lead(l):
    """The company a lead belongs to, or None."""
    ix = _index()
    if not ix["key"] and not ix["dom"]: return None
    k = name_key(l.get("company")); c = ix["key"].get(k) if k else None
    if not c and l.get("company"):
        for a in aliases(l["company"])[1:]:
            c = ix["key"].get(a)
            if c: break
    return c or (ix["dom"].get(domain(l.get("website"))) if domain(l.get("website")) else None)

def _lead_map():
    """lead id -> company id, memoised until a lead or a company changes."""
    ix = _index(); k = (db.ver("leads"), ix["k"])
    if ix["lc"] is None or ix["lc"][0] != k:
        m = {}
        for l in db.query("leads", [("archived", "==", False)]):
            c = for_lead(l)
            if c: m[l["id"]] = c["id"]
        ix["lc"] = (k, m)
    return ix["lc"][1]

# ----------------------------------------------------------------------------- cues for callers
HERO = re.compile(r"signal|hook|trigger|why now", re.I)
SCRIPT = re.compile(r"opening|pitch|script|talk", re.I)
DETAIL = re.compile(r"research|prerequisite|qualif|guideline|reasoning|analysis|notes?$", re.I)

def kind_of(label, value):
    """How a cue is shown: hero = why call now, script = what to say (copyable), chip = short fact, detail = long research (collapsed)."""
    if HERO.search(label) and len(value) <= 900: return "hero"
    if SCRIPT.search(label): return "script"
    if DETAIL.search(label) or len(value) > 160: return "detail"
    return "chip"

def cue_value(c, rx):
    for x in c.get("cues") or []:
        if re.search(rx, x["k"], re.I) and (x.get("v") or "").strip(): return x["v"].strip()
    return ""

def signal_of(c):
    """The company's own 'why call now' (the cue named Signal, else buying signal, else hook)."""
    if not c: return ""
    for rx in (r"^signal$", r"^buying signal$", r"signal", r"^hook$"):
        v = cue_value(c, rx)
        if v: return v[:240]
    return ""

def signal_for_lead(l):
    c = for_lead(l); return signal_of(c) if c else ""

def location(c): return ", ".join(x for x in (c.get("city"), c.get("country")) if x)

def brief(c, full=False):
    """Everything a caller needs about a company, grouped by how it is used. Long research is clipped unless full=True."""
    cues = []
    for x in c.get("cues") or []:
        v = (x.get("v") or "").strip()
        if not v: continue
        kd = kind_of(x["k"], v)
        cues.append({"label": x["k"], "value": v if (full or kd != "detail" or len(v) <= 1200) else v[:1200].rstrip() + "…", "kind": kd})
    lst = (_memo["lists"].get(c.get("list_id")) or {}).get("name", "")
    info = {"website": c.get("website", ""), "linkedin_url": c.get("linkedin_url", ""), "industry": c.get("industry", ""), "employees": c.get("employees"), "location": location(c),
            "phone": c.get("phone", ""), "description": c.get("description", "") if full else (c.get("description", "")[:300]), "keywords": c.get("keywords", "") if full else c.get("keywords", "")[:200],
            "technologies": c.get("technologies", "") if full else "", "annual_revenue": c.get("annual_revenue", ""), "total_funding": c.get("total_funding", ""),
            "latest_funding": c.get("latest_funding", ""), "founded_year": c.get("founded_year", "")}
    return {"id": c["id"], "name": c["name"], "list": lst, "info": info, "cues": cues}

def slim(c, counts):
    pr = cue_value(c, r"priority")
    try: prn = float(pr)
    except ValueError: prn = None
    return {"id": c["id"], "name": c["name"], "industry": c.get("industry", ""), "employees": c.get("employees"), "location": location(c), "website": domain(c.get("website")), "priority": pr, "priority_n": prn,
            "status": cue_value(c, r"^lead status$|status"), "signal": signal_of(c)[:120], "leads": counts.get(c["id"], 0)}

# ----------------------------------------------------------------------------- service functions (permission checks live here)
def _sv():
    import services as sv; return sv
def _need(user, perm):
    sv = _sv()
    if not sv.can(user, perm): sv.err("forbidden", "You do not have access to company data", 403)

def _counts(): return Counter(_lead_map().values())

def _sorted(items):
    return sorted(items, key=lambda r: (r["priority_n"] is None, -(r["priority_n"] or 0), r["name"].lower()))

def board(user, q="", list_id="", per=12):
    """Every company list with its first rows, in one pass over memory (no per-list requests). list_id may be several ids, comma-separated."""
    _need(user, "companies:read"); ix = _index(); counts = _counts(); q = q.strip().lower(); by = {}; want = {x for x in (list_id or "").split(",") if x}
    for c in db.list("companies"):
        if c.get("list_id") in ix["lists"] and (not want or c["list_id"] in want) and (not q or q in (c["name"] + " " + c.get("industry", "") + " " + c.get("website", "")).lower()):
            by.setdefault(c["list_id"], []).append(c)
    out = []
    for lid, lst in ix["lists"].items():
        if want and lid not in want: continue
        rows = by.get(lid, [])
        if q and not rows: continue
        sl = _sorted([slim(c, counts) for c in rows])
        out.append({"id": lid, "name": lst["name"], "color": lst.get("color") or COLORS[0], "fields": lst.get("field_order") or [], "total": len(sl), "leads": sum(r["leads"] for r in sl),
                    "apollo": bool(lst.get("apollo_label_id")), "items": sl[:per], "next_cursor": str(per) if len(sl) > per else None, "created_at": lst.get("created_at")})
    out.sort(key=lambda g: g["created_at"] or "", reverse=True)
    return {"lists": out, "all_lists": [{"id": i, "name": x["name"]} for i, x in sorted(ix["lists"].items(), key=lambda kv: kv[1]["name"].lower())]}

def page(user, list_id, q="", cursor="", limit=50):
    _need(user, "companies:read"); ix = _index(); counts = _counts(); q = q.strip().lower()
    rows = _sorted([slim(c, counts) for c in db.query("companies", [("list_id", "==", list_id)]) if not q or q in (c["name"] + " " + c.get("industry", "")).lower()])
    start = int(cursor or 0); limit = min(max(limit, 1), 200)
    return {"items": rows[start:start + limit], "next_cursor": str(start + limit) if start + limit < len(rows) else None, "total": len(rows)}

def get(user, cid):
    _need(user, "companies:read"); sv = _sv(); c = db.get("companies", cid)
    if not c: sv.err("not_found", "Company not found", 404)
    _index(); lm = _lead_map(); ids = {i for i, x in lm.items() if x == cid}
    leads = [{"id": l["id"], "full_name": l["full_name"], "title": l.get("title", ""), "stage": l["stage"], "owner_id": l.get("owner_id"), "campaign_id": l.get("campaign_id")}
             for l in db.query("leads", [("archived", "==", False)]) if l["id"] in ids and sv.visible(user, l)]
    return {"company": c, "brief": brief(c, full=True), "leads": leads, "cues": c.get("cues") or []}

def brief_for_lead(user, lid):
    c = for_lead(_sv().get_lead(user, lid)); return brief(c) if c else None

def create_list(user, name):
    _need(user, "companies:write"); sv = _sv()
    if not (name or "").strip(): sv.err("name_required", "Give the list a name", 422)
    n = len(db.list("companyLists")); x = db.put("companyLists", uid("cl"), {"name": name.strip()[:80], "color": COLORS[n % len(COLORS)], "archived": False, "created_at": now(), "field_order": []})
    sv.audit(user, "company_list.create", "company_list", x["id"], x["name"]); return x

def patch_list(user, lid, name=None, archived=None):
    _need(user, "companies:write"); sv = _sv(); x = db.get("companyLists", lid)
    if not x: sv.err("not_found", "List not found", 404)
    if name is not None and name.strip(): x["name"] = name.strip()[:80]
    if archived is not None: x["archived"] = archived
    sv.audit(user, "company_list.update", "company_list", lid, x["name"]); return db.put("companyLists", lid, x)

def _doc(base, row, cm):
    """Fill/refresh a company document from one file row. Blank cells never erase existing data."""
    for h, (kind, key) in cm.items():
        v = _v(row.get(h))
        if not v: continue
        if kind == "name": base["name"] = v[:160]
        elif kind == "std":
            if key == "employees":
                d = re.sub(r"[^\d]", "", v); base["employees"] = int(d) if d else None
            else: base[key] = v[:CAPS.get(key, 300)]
        elif kind == "cue":
            cues = base.setdefault("cues", [])
            hit = next((x for x in cues if x["k"] == key), None)
            if hit: hit["v"] = v[:CUE_CAP]
            else: cues.append({"k": key, "v": v[:CUE_CAP]})
    base["name_key"] = name_key(base["name"]); base["domain"] = domain(base.get("website")); return base

def import_file(user, filename, data, list_id="", new_name="", preview=True):
    """Preview or run an import of an Apollo accounts export (CSV/XLSX). Re-importing the same file into the same list updates it, never duplicates it."""
    _need(user, "companies:write"); sv = _sv()
    if len(data) > 15 * 1024 * 1024: sv.err("too_large", "File is larger than 15 MB", 413)
    rows, headers = sv.parse_file(filename, data)
    if len(rows) > MAX_ROWS: sv.err("too_many_rows", f"At most {MAX_ROWS:,} companies per file. Split the file and import it in parts.", 422)
    name_h, cm = classify_columns(headers, rows)
    if not name_h: sv.err("no_name_column", "The file needs a 'Company Name' column.", 422)
    rows = [r for r in rows if _v(r.get(name_h))]
    if not rows: sv.err("empty_file", "No companies found in this file.", 422)
    lst = db.get("companyLists", list_id) if list_id and list_id != "__new" else None
    if list_id and list_id != "__new" and not lst: sv.err("not_found", "List not found", 404)
    existing = db.query("companies", [("list_id", "==", lst["id"])]) if lst else []
    by_ap = {c["apollo_account_id"]: c for c in existing if c.get("apollo_account_id")}; by_key = {c["name_key"]: c for c in existing}
    cue_labels = [k for kind, k in cm.values() if kind == "cue"]
    leads_by_key = Counter(a for l in db.query("leads", [("archived", "==", False)]) if l.get("company") for a in {name_key(l["company"])} | set(aliases(l["company"])[1:]))
    plan = []; seen = set()
    for r in rows:
        ap = _v(r.get(next((h for h, (kd, k) in cm.items() if k == "apollo_account_id"), None))) if any(k == "apollo_account_id" for _, k in cm.values()) else ""
        k = name_key(_v(r.get(name_h)))
        if (ap or k) in seen: continue                                   # same company twice in one file: first row wins
        seen.add(ap or k); plan.append((r, by_ap.get(ap) or by_key.get(k)))
    created = sum(1 for _, e in plan if not e); updated = len(plan) - created
    matched = sum(1 for r, _ in plan if any(leads_by_key.get(a) for a in aliases(_v(r.get(name_h)))))
    if preview:
        return {"preview": True, "rows": len(plan), "create": created, "update": updated, "fields": cue_labels, "leads_matched": matched, "sample": [_v(r.get(name_h)) for r, _ in plan[:4]],
                "list": lst["name"] if lst else (new_name.strip() or filename.rsplit(".", 1)[0])}
    if not lst:
        lst = create_list(user, new_name.strip() or filename.rsplit(".", 1)[0] or "Companies")
    t = now(); docs = []
    for r, e in plan:
        base = dict(e) if e else {"id": uid("co"), "list_id": lst["id"], "created_at": t, "created_by": user["id"], "cues": []}
        base["cues"] = [dict(x) for x in base.get("cues") or []]; base.setdefault("name", ""); base["updated_at"] = t
        docs.append(_doc(base, r, cm))
    db.put_many("companies", docs)
    order = list(lst.get("field_order") or [])
    for k in cue_labels:
        if k not in order: order.append(k)
    lst["field_order"] = order; lst["updated_at"] = t; db.put("companyLists", lst["id"], lst)
    sv.audit(user, "company.import", "company_list", lst["id"], f"{created} new, {updated} updated, {len(cue_labels)} cue fields from {filename}")
    return {"preview": False, "list_id": lst["id"], "list": lst["name"], "created": created, "updated": updated, "fields": cue_labels, "leads_matched": matched}

def create_company(user, b):
    _need(user, "companies:write"); sv = _sv()
    if not b.name.strip(): sv.err("name_required", "Company name is required", 422)
    lst = db.get("companyLists", b.list_id)
    if not lst: sv.err("not_found", "Pick a company list", 404)
    t = now(); c = {"id": uid("co"), "list_id": lst["id"], "name": b.name.strip()[:160], "created_at": t, "updated_at": t, "created_by": user["id"], "cues": []}
    for k in PROFILE:
        v = getattr(b, k, None)
        if v not in (None, ""): c[k] = v
    c["name_key"] = name_key(c["name"]); c["domain"] = domain(c.get("website")); db.put("companies", c["id"], c)
    sv.audit(user, "company.create", "company", c["id"], c["name"]); return c

def patch_company(user, cid, b):
    _need(user, "companies:write"); sv = _sv(); c = db.get("companies", cid)
    if not c: sv.err("not_found", "Company not found", 404)
    p = b.model_dump(exclude_none=True)
    for k in ("name",) + PROFILE:
        if k in p: c[k] = p[k].strip() if isinstance(p[k], str) else p[k]
    if "cues" in p:
        cues = [dict(x) for x in c.get("cues") or []]
        for k, v in p["cues"].items():
            k = (k or "").strip()[:80]
            if not k: continue
            hit = next((x for x in cues if x["k"] == k), None)
            if hit: hit["v"] = (v or "")[:CUE_CAP]
            else: cues.append({"k": k, "v": (v or "")[:CUE_CAP]})
            lst = db.get("companyLists", c["list_id"])
            if lst and k not in (lst.get("field_order") or []): lst.setdefault("field_order", []).append(k); db.put("companyLists", lst["id"], lst)
        c["cues"] = cues
    c["name_key"] = name_key(c["name"]); c["domain"] = domain(c.get("website")); c["updated_at"] = now(); db.put("companies", cid, c)
    sv.audit(user, "company.update", "company", cid, ",".join(p)); return c

def delete_company(user, cid):
    _need(user, "companies:write"); sv = _sv(); c = db.get("companies", cid)
    if not c: sv.err("not_found", "Company not found", 404)
    db.delete("companies", cid); sv.audit(user, "company.delete", "company", cid, c["name"]); return {"ok": True}


# ----------------------------------------------------------------------------- Apollo company lists (accounts)
def _g(a, *keys):
    org = a.get("organization") or {}
    for k in keys:
        v = a.get(k) if a.get(k) not in (None, "", []) else org.get(k)
        if v not in (None, "", []): return v
    return ""

def apollo_lists(user):
    """The team's Apollo ACCOUNT lists (GET /labels, 0 credits). `synced` = already brought in as a company list."""
    _need(user, "companies:write"); sv = _sv(); sv._need_apollo()
    data = sv._apollo("/labels", method="GET"); items = data if isinstance(data, list) else ((data or {}).get("labels") or [])
    synced = {x.get("apollo_label_id") for x in db.list("companyLists") if x.get("apollo_label_id") and not x.get("archived")}
    out = [{"id": x.get("id") or x.get("_id"), "name": x.get("name") or "", "count": x.get("cached_count") or 0, "synced": (x.get("id") or x.get("_id")) in synced}
           for x in items if x.get("modality") == "accounts" and (x.get("id") or x.get("_id"))]
    return sorted(out, key=lambda x: x["name"].lower())

def _custom_field_names():
    """Apollo custom field id -> name, for account fields, in Apollo's own order. {} if the key cannot read them (cues then keep the field id as a label)."""
    sv = _sv()
    try: data = sv._apollo("/typed_custom_fields", method="GET")
    except Exception: return {}
    items = data if isinstance(data, list) else ((data or {}).get("typed_custom_fields") or [])
    return {x["id"]: (x.get("name") or x["id"]) for x in items if x.get("id") and (x.get("modality") in (None, "", "account", "accounts", "organization"))}

def _apollo_doc(base, a, names):
    base["name"] = (a.get("name") or base.get("name") or "").strip()[:160]
    w = _g(a, "website_url", "domain"); base["website"] = str(w)[:300] if w else base.get("website", "")
    for k, v in (("linkedin_url", _g(a, "linkedin_url")), ("industry", _g(a, "industry")), ("city", _g(a, "city")), ("state", _g(a, "state")), ("country", _g(a, "country")),
                 ("phone", a.get("sanitized_phone") or (a.get("primary_phone") or {}).get("number") or a.get("phone") or ""), ("description", _g(a, "short_description")),
                 ("annual_revenue", _g(a, "organization_revenue_printed", "annual_revenue_printed")), ("founded_year", _g(a, "founded_year")), ("apollo_account_id", a.get("id") or "")):
        if v: base[k] = str(v)[:CAPS.get(k, 300)]
    n = _g(a, "estimated_num_employees")
    if n:
        try: base["employees"] = int(n)
        except (TypeError, ValueError): pass
    kw = _g(a, "keywords")
    if kw: base["keywords"] = (", ".join(kw) if isinstance(kw, list) else str(kw))[:CAPS["keywords"]]
    tech = _g(a, "current_technologies")
    if tech: base["technologies"] = ", ".join(x.get("name", "") if isinstance(x, dict) else str(x) for x in tech)[:CAPS["technologies"]]
    cues = base.setdefault("cues", [])
    for fid, val in (a.get("typed_custom_fields") or {}).items():
        v = _v(val if not isinstance(val, (list, dict)) else ", ".join(map(str, val)))
        if not v or (names and fid not in names): continue          # when Apollo told us the account fields, ignore ids that are not one of them
        k = clean_label(names.get(fid) or f"Field {fid[-4:]}")
        hit = next((x for x in cues if x["k"] == k), None)
        if hit: hit["v"] = v[:CUE_CAP]
        else: cues.append({"k": k, "v": v[:CUE_CAP]})
    base["name_key"] = name_key(base["name"]); base["domain"] = domain(base.get("website")); return base

def sync_apollo(user, label_ids=None, max_pages=100):
    """Bring Apollo account lists in as company lists, with Apollo's custom fields as call cues. Idempotent (matched by Apollo account id, then name).
    With label_ids=None it re-syncs the lists already chosen before."""
    _need(user, "companies:write"); sv = _sv(); sv._need_apollo()
    explicit = label_ids is not None
    if not explicit: label_ids = [x["apollo_label_id"] for x in db.list("companyLists") if x.get("apollo_label_id") and not x.get("archived")]
    if not label_ids: return {"lists": 0, "created": 0, "updated": 0, "scanned": 0, "truncated": False, "cue_fields": 0}
    meta = {x["id"]: x for x in apollo_lists(user)}
    if explicit and [i for i in label_ids if i not in meta]: sv.err("bad_list", "One of those lists was not found in Apollo (or is not a company list).", 422)
    names = _custom_field_names(); created = updated = scanned = 0; trunc = False; done = 0; all_cues = set()
    for lid in [i for i in label_ids if i in meta]:
        lst = next((x for x in db.list("companyLists") if x.get("apollo_label_id") == lid), None)
        if lst: lst.update({"name": meta[lid]["name"], "archived": False})
        else: lst = {"id": uid("cl"), "name": meta[lid]["name"], "color": COLORS[len(db.list("companyLists")) % len(COLORS)], "archived": False, "created_at": now(), "field_order": [], "apollo_label_id": lid}
        existing = db.query("companies", [("list_id", "==", lst["id"])]) if db.get("companyLists", lst["id"]) else []
        by_ap = {c["apollo_account_id"]: c for c in existing if c.get("apollo_account_id")}; by_key = {c["name_key"]: c for c in existing}; docs = []; t = now(); page = 1
        while True:
            data = sv._apollo("/accounts/search", body={"account_label_ids": [lid], "page": page, "per_page": 100}) or {}
            accts = data.get("accounts") or []; scanned += len(accts)
            for a in accts:
                if not (a.get("name") or "").strip(): continue
                e = by_ap.get(a.get("id")) or by_key.get(name_key(a["name"]))
                base = dict(e) if e else {"id": uid("co"), "list_id": lst["id"], "created_at": t, "created_by": user["id"], "cues": []}
                base["cues"] = [dict(x) for x in base.get("cues") or []]; base["updated_at"] = t; d = _apollo_doc(base, a, names)
                if e: updated += 1
                else: created += 1
                docs.append(d); by_ap[d.get("apollo_account_id")] = d; by_key[d["name_key"]] = d
                all_cues.update(x["k"] for x in d["cues"])
            tp = (data.get("pagination") or {}).get("total_pages")
            if not accts or len(accts) < 100 or (tp and page >= tp): break
            if page >= max_pages: trunc = True; break
            page += 1
        order = list(lst.get("field_order") or [])
        for d in docs:
            for x in d["cues"]:
                if x["k"] not in order: order.append(x["k"])
        lst.update({"field_order": order, "synced_at": t}); db.put("companyLists", lst["id"], lst)
        # one batch per list; a company that appears twice in Apollo is written once (the last copy wins)
        db.put_many("companies", list({d["id"]: d for d in docs}.values())); done += 1
    sv.audit(user, "company_list.apollo_sync", "company_list", "", f"{done} lists, {created} new, {updated} updated")
    return {"lists": done, "created": created, "updated": updated, "scanned": scanned, "truncated": trunc, "cue_fields": len(all_cues)}