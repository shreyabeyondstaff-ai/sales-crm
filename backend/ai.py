"""AI providers behind one tiny interface: Gemini or Groq (AI_PROVIDER=gemini|groq|none). Backend only.
The LLM never calculates anything: it only phrases or summarises facts the CRM already computed.
Hygiene: keys travel in headers (never in URLs), timeouts + bounded retries, and nothing here logs keys, prompts or answers."""
import os, time
import httpx

class AIError(Exception):
    """Safe-to-show message. Never contains keys or prompt text."""

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_BASE = "https://api.groq.com/openai/v1"
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "groq": "llama-3.3-70b-versatile"}   # overridable: GEMINI_MODEL / GROQ_MODEL

def provider():
    p = (os.getenv("AI_PROVIDER") or "none").strip().lower()
    return p if p in ("gemini", "groq") else "none"

def _key(p): return os.getenv("GEMINI_API_KEY" if p == "gemini" else "GROQ_API_KEY") or ""
def model(p=None):
    p = p or provider()
    return (os.getenv("GEMINI_MODEL" if p == "gemini" else "GROQ_MODEL") or DEFAULT_MODELS.get(p, "")).strip()

def configured(): p = provider(); return p != "none" and bool(_key(p))

def status():
    p = provider()
    if p == "none": return {"provider": "none", "configured": False, "model": "", "note": "AI provider pending configuration. Answers are rule-based."}
    if not _key(p): return {"provider": p, "configured": False, "model": model(p), "note": f"AI provider pending configuration: set {'GEMINI_API_KEY' if p == 'gemini' else 'GROQ_API_KEY'}. Answers are rule-based."}
    return {"provider": p, "configured": True, "model": model(p), "note": None}

def _send(method, url, headers, body=None, tries=3, timeout=30):
    for attempt in range(tries):
        try:
            r = httpx.request(method, url, headers=headers, json=body, timeout=timeout)
        except httpx.HTTPError:
            if attempt == tries - 1: raise AIError("Could not reach the AI provider. Try again shortly.")
            time.sleep(1 + attempt); continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt < tries - 1:
                try: wait = min(float(r.headers.get("retry-after", 2 ** attempt)), 10)
                except ValueError: wait = 2
                time.sleep(wait); continue
            raise AIError("The AI provider is rate-limiting or unavailable. Try again in a minute." if r.status_code == 429 else "The AI provider had a server error. Try again shortly.")
        if r.status_code in (401, 403): raise AIError("The AI provider rejected the API key (wrong key, or no access to this model).")
        if r.status_code == 404: raise AIError("The AI model name was not found. Check GEMINI_MODEL / GROQ_MODEL.")
        if r.status_code >= 400: raise AIError(f"The AI provider rejected the request ({r.status_code}).")
        try: return r.json()
        except ValueError: raise AIError("The AI provider returned an unreadable response.")

def complete(system, prompt, max_tokens=600):
    """One completion. Raises AIError with a safe message on any failure."""
    p = provider()
    if p == "none" or not _key(p): raise AIError("AI provider pending configuration.")
    if p == "gemini":
        data = _send("POST", GEMINI_URL.format(model=model(p)), {"x-goog-api-key": _key(p), "Content-Type": "application/json"},
                     {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2}})
        try: parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError): raise AIError("The AI provider returned no answer (it may have been blocked).")
        text = "".join(x.get("text", "") for x in parts if isinstance(x, dict))
    else:
        data = _send("POST", GROQ_BASE + "/chat/completions", {"Authorization": "Bearer " + _key(p), "Content-Type": "application/json"},
                     {"model": model(p), "temperature": 0.2, "max_completion_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        try: text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError): raise AIError("The AI provider returned no answer.")
    text = text.strip()
    if not text: raise AIError("The AI provider returned an empty answer.")
    return text

def test(p):
    """Real, cheap call used by the Integrations page. Returns (ok, message)."""
    if not _key(p): return False, f"Pending configuration. Set {'GEMINI_API_KEY' if p == 'gemini' else 'GROQ_API_KEY'} in .env"
    try:
        if p == "groq":
            _send("GET", GROQ_BASE + "/models", {"Authorization": "Bearer " + _key(p)}, tries=2, timeout=15)
        else:
            _send("POST", GEMINI_URL.format(model=model(p)), {"x-goog-api-key": _key(p), "Content-Type": "application/json"},
                  {"contents": [{"role": "user", "parts": [{"text": "Reply with the word OK."}]}], "generationConfig": {"maxOutputTokens": 8}}, tries=2, timeout=20)
    except AIError as e: return False, str(e)
    sel = "" if provider() == p else f" (set AI_PROVIDER={p} to use it)"
    return True, f"{p.capitalize()} key works, model {model(p)}{sel}"
