"""
LLM provider abstraction. Pick with env LLM_PROVIDER = openai | ollama | mock

  openai : any OpenAI-compatible endpoint (OpenAI, OpenRouter, Groq, DeepSeek, vLLM...)
           env: OPENAI_API_KEY, OPENAI_BASE_URL (default https://api.openai.com/v1), LLM_MODEL
  ollama : local models. env: OLLAMA_URL (default http://localhost:11434), LLM_MODEL (e.g. qwen2.5:14b)
  mock   : no network, heuristic answers — for UI demos / tests only.

complete_json(system, user, temperature, model=None) -> (parsed dict, raw text)
Token usage is tracked for the cost readout. Raw text is stored in the evaluation trace
so judges can verify the system really analyses each dialog (no pre-baked results).
"""
from __future__ import annotations
import json
import os
import re
import time
import requests

PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
MODEL_BY_LANG = {k.split("_")[-1]: v for k, v in os.environ.items() if k.startswith("LLM_MODEL_") and v}  # LLM_MODEL_KZ=...
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

# USD per 1M tokens (input, output) — used only for the cost readout; extend for your model
PRICES = {
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00),
    "deepseek-chat": (0.27, 1.10), "llama-3.3-70b-versatile": (0.59, 0.79),
}

usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0, "errors": 0}


def model_for(language: str | None) -> str:
    return MODEL_BY_LANG.get((language or "").upper(), MODEL)


def _extract_json(text: str) -> dict:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        t = t[start : end + 1]
    return json.loads(t)


def _openai(system: str, user: str, temperature: float, model: str) -> tuple[str, int, int]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set (see .env.example)")
    r = requests.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": model, "temperature": temperature, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    u = d.get("usage", {})
    return d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def _ollama(system: str, user: str, temperature: float, model: str) -> tuple[str, int, int]:
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": model, "stream": False, "format": "json",
              "options": {"temperature": temperature, "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "16384"))},
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        timeout=TIMEOUT * 3,
    )
    r.raise_for_status()
    d = r.json()
    return d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0)


def _mock(system: str, user: str, temperature: float, model: str) -> tuple[str, int, int]:
    """Keyword heuristics so the whole pipeline runs with no model. Not for real scoring."""
    if user.startswith("Evaluate ALL criteria"):
        ids = re.findall(r"^- id=(\S+)", user, re.M)
        res = {cid: json.loads(_mock(system, f"CRITERION_ID: {cid}\n{user}", temperature, model)[0]) for cid in ids}
        return json.dumps({"results": res}, ensure_ascii=False), 0, 0
    m = re.search(r"CRITERION_ID:\s*(\S+)", user)
    cid = m.group(1) if m else "x"
    ops = re.findall(r"^\[(\d+)\]\s+operator:\s*(.*)$", user, re.M)
    text = " ".join(t for _, t in ops).lower()
    first = int(ops[0][0]) if ops else 0
    last = int(ops[-1][0]) if ops else 0
    neg_kw = ["парол", "password", "точно вырастут", "переведите мне", "на мою карту", "личный номер", "ватсап", "whatsapp"]
    rules = {  # keyed by the official checklist ids
        "contact": (["здравств", "добрый", "сәлем", "қайырлы", "hello", "good "], "yes_no"),
        "discovery": (["уточн", "подскажите", "какой", "what ", "which", "қандай", "could you"], "yes_no"),
        "identification": (["последние 4", "четыре цифр", "кодовое", "last four", "дату рождения"], "yes_no"),
        "consultation": (neg_kw, "neg_scale"),
        "empathy": (["пожалуйста", "спасибо", "рахмет", "thank", "please", "понимаю", "поняла"], "yes_no"),
        "dialogue": (["как хотите", "ну "], "neg_scale"),
        "closure": (["до свидан", "хорошего", "сау бол", "goodbye", "have a", "всего добр", "остались"], "yes_no"),
        "critical": (neg_kw, "yes_no"),
    }
    kws, t = rules.get(cid, (["."], "yes_no"))
    hit = any(k in text for k in kws)
    sc = {"consultation": [0, 5, 10, 15, 20], "dialogue": [0, 4, 7, 10]}.get(cid, [0, 1])
    ans = {"yes_no": "yes" if hit else "no", "neg_scale": sc[1] if hit else sc[-1]}[t]
    idx = last if cid == "closure" else first
    out = {"answer": ans, "confidence": 0.5, "evidence_turns": [idx] if ops else [], "checked_range": [first, last],
           "explanation": "[mock provider] эвристика по ключевым словам — для реальной оценки подключите LLM."}
    return json.dumps(out, ensure_ascii=False), 0, 0


_FN = {"openai": _openai, "ollama": _ollama, "mock": _mock}


def _wait_for(e: Exception, attempt: int) -> float:
    """Rate limit (429): wait as long as the provider asks (Retry-After), else 15 s; other errors: short backoff."""
    resp = getattr(e, "response", None)
    if resp is not None and resp.status_code == 429:
        try:
            return min(60.0, float(resp.headers.get("retry-after", 15))) + 1
        except ValueError:
            return 15.0
    return 1.5 * (attempt + 1)


def complete_json(system: str, user: str, temperature: float = 0.1, model: str | None = None, retries: int = int(os.getenv("LLM_RETRIES", "5"))) -> tuple[dict, str]:
    if PROVIDER not in _FN:
        raise RuntimeError(f"unknown LLM_PROVIDER={PROVIDER}")
    model = model or MODEL
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            raw, pin, pout = _FN[PROVIDER](system, user, temperature, model)
            parsed = _extract_json(raw)
            usage["calls"] += 1
            usage["prompt_tokens"] += pin
            usage["completion_tokens"] += pout
            usage["seconds"] += time.time() - t0
            return parsed, raw
        except Exception as e:  # noqa: BLE001
            last_err = e
            usage["errors"] += 1
            if attempt < retries:
                time.sleep(_wait_for(e, attempt))
    raise RuntimeError(f"LLM call failed after {retries + 1} attempts: {last_err}")


def cost_estimate() -> dict:
    pin, pout = PRICES.get(MODEL, (0.0, 0.0))
    usd = usage["prompt_tokens"] / 1e6 * pin + usage["completion_tokens"] / 1e6 * pout
    return {"provider": PROVIDER, "model": MODEL, "models_by_language": MODEL_BY_LANG, **usage, "usd": round(usd, 4)}
