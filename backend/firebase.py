"""Firebase Admin init, token verification and the data-access layer.
Two interchangeable stores with the same tiny API: MemoryDB (demo) and FirestoreDB (real).
"""
import os, uuid, json, time, threading
from datetime import datetime, timezone
try:
    from dotenv import load_dotenv; load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception: pass

AUTH_DISABLED = os.getenv("AUTH_DISABLED", "true").lower() == "true"
USE_FIRESTORE = os.getenv("USE_FIRESTORE", "false").lower() == "true"

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def uid(p): return f"{p}_{uuid.uuid4().hex[:10]}"

"""query() contract (identical in both stores):
    query(col, where=[(field, op, value), ...], order_by=None, desc=False, limit=None, start_after=None)
  op: == != < <= > >= in   ("in" takes at most 30 values: a Firestore limit)
  start_after: the document ID of the last row of the previous page (keyset pagination; needs order_by).
FIRESTORE INDEX RULES the callers rely on, so no composite index is ever needed:
  * equality-only filters (any number of fields)  -> served by single-field indexes
  * order_by + limit with NO where                -> served by a single-field index
  Do NOT combine where() with a different-field order_by without adding a composite index
  (see docs/FIRESTORE_SCHEMA.md). Documents missing a filtered/ordered field are skipped in both stores.
Collection names may be sub-collection paths, e.g. "importBatches/imp_1/rows"."""
import operator as _op
_OPS = {"==": _op.eq, "!=": _op.ne, "<": _op.lt, "<=": _op.le, ">": _op.gt, ">=": _op.ge, "in": lambda a, b: a in b}

def _match(d, f, op, v):
    if f not in d: return False
    x = d[f]
    if op in ("<", "<=", ">", ">="):                      # Firestore never range-matches null or mixed types
        if x is None or v is None: return False
        try: return _OPS[op](x, v)
        except TypeError: return False
    return _OPS[op](x, v)

class MemoryDB:
    def __init__(s): s.c = {}; s.v = {}
    def ver(s, col):
        """Changes whenever the collection changes. Lets callers memoise derived data (e.g. the company lookup)."""
        d = s.c.get(col); return (s.v.get(col, 0), id(d), len(d) if d else 0)
    def list(s, col): return list(s.c.get(col, {}).values())
    def get(s, col, i): return s.c.get(col, {}).get(i)
    def put(s, col, i, doc): doc["id"] = i; s.c.setdefault(col, {})[i] = doc; s.v[col] = s.v.get(col, 0) + 1; return doc
    def put_many(s, col, docs):
        for d in docs: s.put(col, d["id"], d)
    def delete(s, col, i): s.c.get(col, {}).pop(i, None); s.v[col] = s.v.get(col, 0) + 1
    def query(s, col, where=(), order_by=None, desc=False, limit=None, start_after=None):
        docs = list(s.c.get(col, {}).values())
        for f, op, v in where:
            if op == "in" and len(v) > 30: raise ValueError("Firestore 'in' accepts at most 30 values")
            docs = [d for d in docs if _match(d, f, op, v)]
        if order_by:
            docs = [d for d in docs if order_by in d]
            docs.sort(key=lambda d: (d[order_by] is not None, "" if d[order_by] is None else d[order_by], d["id"]), reverse=desc)
            if start_after:
                ids = [d["id"] for d in docs]; docs = docs[ids.index(start_after) + 1:] if start_after in ids else []
        return docs[:limit] if limit else docs

class FirestoreDB:
    def __init__(s):
        _init_admin(); from firebase_admin import firestore; s.fs = firestore.client()
    def list(s, col): return [d.to_dict() | {"id": d.id} for d in s.fs.collection(col).stream()]
    def get(s, col, i):
        d = s.fs.collection(col).document(i).get(); return (d.to_dict() | {"id": d.id}) if d.exists else None
    def put(s, col, i, doc): doc["id"] = i; s.fs.collection(col).document(i).set(doc); return doc
    def delete(s, col, i): s.fs.collection(col).document(i).delete()
    def ver(s, col): return 0
    def put_many(s, col, docs):
        """Batched writes (500 per commit): importing a few thousand rows takes seconds, not minutes."""
        for i in range(0, len(docs), 450):
            b = s.fs.batch()
            for d in docs[i:i + 450]: b.set(s.fs.collection(col).document(d["id"]), d)
            b.commit()
    def query(s, col, where=(), order_by=None, desc=False, limit=None, start_after=None):
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter
        c = s.fs.collection(col); q = c
        for f, op, v in where:
            if op == "in" and len(v) > 30: raise ValueError("Firestore 'in' accepts at most 30 values")
            q = q.where(filter=FieldFilter(f, op, v))
        if order_by:
            q = q.order_by(order_by, direction=firestore.Query.DESCENDING if desc else firestore.Query.ASCENDING)
            if start_after:
                snap = c.document(start_after).get()
                if not snap.exists: return []
                q = q.start_after(snap)
        if limit: q = q.limit(limit)
        return [d.to_dict() | {"id": d.id} for d in q.stream()]

_admin = False
def _init_admin():
    global _admin
    if _admin: return
    import firebase_admin
    from firebase_admin import credentials
    p, j = os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if j:                                   # whole key pasted as an env var (easy on Render: no file needed)
        try: cred = credentials.Certificate(json.loads(j))
        except Exception as e: raise RuntimeError(f"FIREBASE_SERVICE_ACCOUNT_JSON is set but is not a valid service account JSON ({type(e).__name__}). Paste the full contents of the key file.")
    elif p and os.path.exists(p): cred = credentials.Certificate(p)
    elif p: raise RuntimeError(f"GOOGLE_APPLICATION_CREDENTIALS points to '{p}' but that file does not exist. On Render add a Secret File with exactly that name, or set FIREBASE_SERVICE_ACCOUNT_JSON instead.")
    else: cred = None                       # Google Cloud: use the runtime's own identity
    firebase_admin.initialize_app(cred)
    _admin = True

_tok = {}
def verify_token(token: str) -> dict:
    """Verify a Firebase ID token. Returns decoded claims or raises ValueError.
    The revocation check is a network round trip to Google, so a verified token is remembered for up to 5 minutes (never past its own expiry)."""
    hit = _tok.get(token)
    if hit and hit[0] > time.time(): return hit[1]
    _init_admin()
    from firebase_admin import auth
    try: claims = auth.verify_id_token(token, check_revoked=True)
    except Exception as e: raise ValueError(str(e))
    if len(_tok) > 500: _tok.clear()
    _tok[token] = (min(time.time() + 300, float(claims.get("exp") or 0) or 0), claims); return claims

HOT = {"leads", "campaigns", "users", "config", "tasks", "activities", "suggestions", "agentEvents", "agentRuns", "opportunities", "companies", "companyLists", "importBatches"}

class CachedStore:
    """Process-local copy of the collections every screen reads, in front of Firestore.
    Why: the app filters and groups whole collections in Python, and a Firestore round trip per request made every page slow.
    How: a hot collection is read ONCE, then served from memory with the exact same query() contract (MemoryDB). Writes go to Firestore first, then
    into memory, so a crash never leaves memory ahead of the database. Results are shallow copies, so a handler that edits a document without saving it
    cannot corrupt the cache. A collection is re-read from Firestore in the background every CACHE_TTL_SECONDS (default 300) so out-of-band edits and
    other instances show up. Other collections (auditLogs, enrichmentJobs, import rows) go straight to Firestore."""
    def __init__(s, inner):
        s.inner, s.mem, s.loaded, s.lock, s.refreshing, s.log = inner, MemoryDB(), {}, threading.RLock(), set(), {}
        s.ttl = int(os.getenv("CACHE_TTL_SECONDS", "300"))
    def _load(s, col):
        with s.lock: s.log[col] = []                       # remember writes that land while Firestore is being read
        docs = s.inner.list(col)
        with s.lock:
            fresh = {d["id"]: d for d in docs}
            for op, i, doc in s.log.pop(col, []):          # replay them so the refresh never rolls a newer write back
                if op == "put": fresh[i] = doc
                else: fresh.pop(i, None)
            s.mem.c[col] = fresh; s.mem.v[col] = s.mem.v.get(col, 0) + 1; s.loaded[col] = time.time()
    def _ready(s, col):
        t = s.loaded.get(col)
        if t is None:
            with s.lock:
                if col not in s.loaded: s._load(col)
        elif time.time() - t > s.ttl and col not in s.refreshing:
            s.refreshing.add(col)
            def go():
                try: s._load(col)
                except Exception: pass
                finally: s.refreshing.discard(col)
            threading.Thread(target=go, daemon=True).start()
    def warm(s):
        for c in sorted(HOT):
            try: s._ready(c)
            except Exception: pass
    def ver(s, col): return s.mem.ver(col) if col in HOT else 0
    @staticmethod
    def _cp(d): return None if d is None else dict(d)
    def list(s, col):
        if col not in HOT: return s.inner.list(col)
        s._ready(col); return [dict(d) for d in s.mem.list(col)]
    def get(s, col, i):
        if col not in HOT: return s.inner.get(col, i)
        s._ready(col); return s._cp(s.mem.get(col, i))
    def put(s, col, i, doc):
        doc = s.inner.put(col, i, doc)
        if col in HOT:
            s._ready(col)
            with s.lock:
                s.mem.put(col, i, dict(doc))
                if col in s.log: s.log[col].append(("put", i, dict(doc)))
        return doc
    def put_many(s, col, docs):
        s.inner.put_many(col, docs)
        if col in HOT:
            s._ready(col)
            with s.lock:
                for d in docs:
                    s.mem.put(col, d["id"], dict(d))
                    if col in s.log: s.log[col].append(("put", d["id"], dict(d)))
    def delete(s, col, i):
        s.inner.delete(col, i)
        if col in HOT:
            s._ready(col)
            with s.lock:
                s.mem.delete(col, i)
                if col in s.log: s.log[col].append(("del", i, None))
    def query(s, col, where=(), order_by=None, desc=False, limit=None, start_after=None):
        if col not in HOT: return s.inner.query(col, where, order_by, desc, limit, start_after)
        s._ready(col); return [dict(d) for d in s.mem.query(col, where, order_by, desc, limit, start_after)]

db = CachedStore(FirestoreDB()) if USE_FIRESTORE else MemoryDB()