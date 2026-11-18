import pytest
from fastapi import HTTPException
import ai, services as sv, firebase
from conftest import ADMIN, CALLER, CALLER2, make_lead

class Resp:
    def __init__(self, status=200, body=None, headers=None): self.status_code, self._b, self.headers = status, body or {}, headers or {}
    def json(self): return self._b

@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq"); monkeypatch.setenv("GROQ_API_KEY", "gk-secret")
    sent = []
    def fake(method, url, headers, body=None, tries=3, timeout=30): sent.append({"url": url, "headers": headers, "body": body}); return {"choices": [{"message": {"content": "AI says hi"}}]}
    monkeypatch.setattr(ai, "_send", fake); return sent

def seed_lead_with_history(owner=None):
    l = make_lead(owner=owner, first_name="Dana", last_name="Roe", company="Acme", email="dana@secret.com", phone="+44 7700 900123", title="CTO", signal="Hiring 4 SDRs")
    firebase.db.put("activities", "a1", {"lead_id": l["id"], "at": "2026-01-02T10:00:00+00:00", "type": "phone_call", "outcome": "Interested", "notes": "wants pricing"})
    return l

# ---------------------------------------------------------------- no provider: rule-based fallback, clearly labelled
def test_no_provider_is_pending_and_chat_still_works():
    assert ai.status()["configured"] is False and "pending configuration" in ai.status()["note"]
    make_lead(first_name="A", company="Acme")
    r = sv.chat(ADMIN, "how many leads were added this week", None)
    assert "leads" in r["answer"].lower() and r["ai"]["configured"] is False and "ai_phrased" not in r
    with pytest.raises(ai.AIError): ai.complete("s", "p")

def test_lead_question_without_provider_falls_back_and_says_so():
    l = seed_lead_with_history()
    r = sv.chat(ADMIN, "summarise this lead", l["id"])
    assert "Dana Roe" in r["answer"] and "AI provider pending configuration" in r["answer"] and not r.get("ai_phrased")
    r2 = sv.chat(ADMIN, "list objections from the call transcript", l["id"]); assert "No call transcript is stored" in r2["answer"]

def test_provider_without_key_is_pending(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini"); s = ai.status()
    assert s["configured"] is False and "GEMINI_API_KEY" in s["note"] and ai.test("gemini")[0] is False

# ---------------------------------------------------------------- provider on: LLM only phrases calculated facts
def test_chat_phrases_deterministic_facts_and_sends_no_raw_tables(groq):
    for i in range(3): make_lead(first_name=f"Person{i}", email=f"p{i}@secret.com", company="Acme")
    r = sv.chat(ADMIN, "how many leads were added this week", None)
    assert r["answer"] == "AI says hi" and r["ai_phrased"] and r["ai"]["configured"]
    prompt = groq[0]["body"]["messages"][1]["content"]
    assert "3 leads were added" in prompt and "secret.com" not in prompt and "Person0" not in prompt      # aggregates only
    assert groq[0]["headers"]["Authorization"] == "Bearer gk-secret" and groq[0]["url"].endswith("/chat/completions")

def test_provider_failure_keeps_the_rule_based_answer(monkeypatch, groq):
    def fail(*a, **k): raise ai.AIError("The AI provider rejected the API key")
    monkeypatch.setattr(ai, "_send", fail); make_lead(first_name="A")
    r = sv.chat(ADMIN, "how many leads were added this week", None)
    assert "leads" in r["answer"].lower() and not r.get("ai_phrased")

def test_lead_ai_sends_only_that_lead_without_contact_details(groq):
    l = seed_lead_with_history(); make_lead(first_name="Other", last_name="Person", company="Elsewhere", email="other@secret.com")
    firebase.db.put("transcripts", "t1", {"lead_id": l["id"], "started_at": "2026-01-02T10:00:00Z", "text": "Prospect: we need pricing by Friday. " * 5})
    r = sv.chat(ADMIN, "summarise this call transcript", l["id"])
    assert r["answer"] == "AI says hi" and r["ai_phrased"] and "Review before using" in r["definition"]
    prompt = groq[0]["body"]["messages"][1]["content"]
    assert "Dana Roe" in prompt and "Hiring 4 SDRs" in prompt and "wants pricing" in prompt and "we need pricing by Friday" in prompt
    assert "Other Person" not in prompt and "secret.com" not in prompt and "900123" not in prompt            # one lead, no email/phone

def test_transcript_text_sent_to_the_provider_is_length_capped(groq):
    l = seed_lead_with_history(); firebase.db.put("transcripts", "t1", {"lead_id": l["id"], "started_at": "2026-01-02", "text": "x" * 200_000})
    sv.chat(ADMIN, "summarise the call transcript", l["id"]); assert len(groq[0]["body"]["messages"][1]["content"]) < sv.MAX_FACTS_CHARS + 2000

def test_ai_answers_respect_record_scoping(groq):
    l = seed_lead_with_history(owner=CALLER["id"])
    with pytest.raises(HTTPException) as e: sv.chat(CALLER2, "summarise this lead", l["id"])
    assert e.value.status_code == 404 and groq == []                                                         # nothing was sent to the provider
    assert sv.chat(CALLER, "summarise this lead", l["id"])["answer"] == "AI says hi"

def test_chat_is_read_only(groq):
    l = seed_lead_with_history(); before = {c: len(firebase.db.list(c)) for c in ("leads", "tasks", "activities")}
    sv.chat(ADMIN, "draft a follow-up email", l["id"]); sv.chat(ADMIN, "list objections and next steps", l["id"])
    assert {c: len(firebase.db.list(c)) for c in before} == before
    assert "Do not claim it was sent" in groq[0]["body"]["messages"][1]["content"]

# ---------------------------------------------------------------- wire formats (checked against each provider's current docs)
def test_gemini_request_shape_and_parsing(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini"); monkeypatch.setenv("GEMINI_API_KEY", "gem-secret"); seen = {}
    def fake(method, url, headers, body=None, **k): seen.update(url=url, headers=headers, body=body); return {"candidates": [{"content": {"parts": [{"text": "Hello "}, {"text": "there"}]}}]}
    monkeypatch.setattr(ai, "_send", fake)
    assert ai.complete("be brief", "hi", 50) == "Hello there"
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent" and "gem-secret" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "gem-secret" and seen["body"]["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 50 and seen["body"]["contents"][0]["parts"][0]["text"] == "hi"

def test_groq_request_shape_and_model_override(monkeypatch, groq):
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b"); ai.complete("sys", "usr", 77)
    b = groq[0]["body"]; assert b["model"] == "openai/gpt-oss-20b" and b["max_completion_tokens"] == 77 and b["messages"][0] == {"role": "system", "content": "sys"}

def test_blocked_or_empty_answers_are_errors_not_blank_text(monkeypatch, groq):
    monkeypatch.setattr(ai, "_send", lambda *a, **k: {"choices": [{"message": {"content": "  "}}]})
    with pytest.raises(ai.AIError): ai.complete("s", "p")
    monkeypatch.setenv("AI_PROVIDER", "gemini"); monkeypatch.setenv("GEMINI_API_KEY", "k"); monkeypatch.setattr(ai, "_send", lambda *a, **k: {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ai.AIError): ai.complete("s", "p")

def test_http_retries_and_error_messages_never_leak_keys_or_prompts(monkeypatch):
    monkeypatch.setattr(ai.time, "sleep", lambda s: None); seq = [Resp(429, headers={"retry-after": "1"}), Resp(200, {"ok": True})]
    monkeypatch.setattr(ai.httpx, "request", lambda *a, **k: seq.pop(0)); assert ai._send("POST", "https://x", {}, {}) == {"ok": True}
    for status, frag in ((401, "rejected the API key"), (404, "model name"), (429, "rate-limiting")):
        monkeypatch.setattr(ai.httpx, "request", lambda *a, _s=status, **k: Resp(_s))
        with pytest.raises(ai.AIError) as e: ai._send("POST", "https://x", {"x-goog-api-key": "TOPSECRET"}, {"contents": "PRIVATE PROMPT"}, tries=2)
        assert frag in str(e.value) and "TOPSECRET" not in str(e.value) and "PRIVATE" not in str(e.value)

def test_integrations_page_reports_all_four_with_real_tests(monkeypatch, groq):
    ids = {i["id"]: i for i in sv.integrations()}; assert set(ids) == {"firebase", "apollo", "gemini", "groq"}
    assert ids["groq"]["status"] == "connected" and ids["gemini"]["status"] == "pending" and "Pending configuration" in ids["gemini"]["note"]
    assert sv.test_integration("groq") == {"ok": True, "message": "Groq key works, model llama-3.3-70b-versatile"}
    assert sv.test_integration("gemini")["ok"] is False and sv.test_integration("apollo")["ok"] is False and sv.test_integration("firebase")["ok"] is False
    with pytest.raises(HTTPException): sv.test_integration("claude")
