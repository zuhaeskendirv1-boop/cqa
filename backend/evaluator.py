"""
Evaluator: transcript + checklist -> per-criterion answers with evidence, totals and a full LLM trace.

Accuracy levers:
  EVAL_MODE        per_criterion (default, most accurate) | batch (1 call per dialog, cheap)
  EVAL_SAMPLES     1..5  self-consistency: N samples -> majority (yes/no, N/A) / median (scale)
  EVAL_SAMPLES_KZ  per-language override (small models are weaker on Kazakh -> 3 recommended)
  LLM_MODEL_KZ     per-language model override
  checklist.domain_notes   facts the auditor must know (settlement, forbidden phrases) — edited in the UI
  data/calibration.json    few-shot examples per criterion from hand-labeled DEV dialogs
"""
from __future__ import annotations
import json
import os
from collections import Counter
from pathlib import Path
from statistics import median

from . import llm, prechecks
from .scoring import compute_totals, snap_to_steps

DATA_DIR = Path(os.getenv("CQA_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
EVAL_MODE = os.getenv("EVAL_MODE", "per_criterion")
EVAL_SAMPLES = max(1, int(os.getenv("EVAL_SAMPLES", "1")))
SAMPLES_BY_LANG = {k.split("_")[-1]: int(v) for k, v in os.environ.items() if k.startswith("EVAL_SAMPLES_") and v.isdigit()}

SYSTEM_PROMPT = """You are a strict, consistent Quality Assurance auditor for a brokerage company's customer support (Freedom Broker, Kazakhstan).
You evaluate ONLY the human OPERATOR's work. Turns by `bot`, `system` and `client` are context, never scored.
Transcripts are in Russian, Kazakh or English. Kazakh transcripts: read carefully, do not downgrade the operator for the language itself. Write explanations in Russian.

Scoring discipline:
- Base every answer on literal evidence in the transcript; cite turn numbers.
- If the required action never happened, give the worst value, `evidence_turns: []`, and set `checked_range` to the segment you inspected.
- "N/A" ONLY when the criterion's own N/A rule applies. If N/A is not allowed, never answer N/A.
- Top value only when the requirement is met fully and unambiguously. Partial / late / formal-only performance gets the middle value. When unsure between two values, choose the LOWER one. Human auditors are strict; match them.
- Judge each criterion independently: politeness does not fix wrong information, correct information does not excuse rudeness.
- A criterion of type gross_error is answered "yes" ONLY for a real violation listed in its definition (e.g. asking the client to transfer money to the operator personally, asking for a personal phone number / moving to a private messenger, asking for a password or full card number, knowingly false information, insults). Ordinary weak service is "no".
- For step scales, answer with one of the listed step values only.
- Regex HINTS are given as help; they can be wrong — verify them in context.
- Output ONLY valid JSON matching the requested schema."""


def format_transcript(transcript: list[dict]) -> str:
    """Turns are numbered from 1 over the FULL transcript (client, bot, system included) — same as the organizers."""
    return "\n".join(f"[{i}] {t.get('speaker','?')}: {str(t.get('text','')).strip()}" for i, t in enumerate(transcript, start=1))


def _answer_schema(c: dict) -> str:
    if c["type"] == "gross_error":
        allowed = '"yes" (a gross error DID happen) | "no"'
    elif c["type"] == "yes_no":
        allowed = '"yes" | "no"'
    else:
        sc = c.get("scale", {"min": 0, "max": 1})
        labels = sc.get("labels") or {}
        lab = "; ".join(f"{k} = {v}" for k, v in labels.items())
        steps = sc.get("steps")
        allowed = (f'one of {steps}' if steps else f'integer {sc["min"]}..{sc["max"]}') + (f" ({lab})" if lab else "")
    if c.get("na_allowed"):
        allowed += ' | "N/A"'
    return allowed


def _load_calibration() -> dict:
    p = DATA_DIR / "calibration.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _context_block(checklist: dict, conversation: dict, pre: dict) -> str:
    dn = (checklist.get("domain_notes") or "").strip()
    out = f"CHANNEL: {conversation.get('channel')} | LANGUAGE: {conversation.get('language')}\n"
    if dn:
        out += f"\nDOMAIN KNOWLEDGE (treat as ground truth for correctness/compliance):\n{dn}\n"
    out += f"\nHINTS (deterministic pre-checks):\n{prechecks.as_hints(pre)}\n"
    return out


def build_criterion_prompt(c: dict, ctx: str, transcript_text: str, calibration: dict) -> str:
    ex = calibration.get(c["id"], [])
    ex_text = ""
    if ex:
        ex_text = "\nCALIBRATION EXAMPLES (how human auditors scored similar cases — match this strictness):\n" + "\n".join(
            f"- snippet: {e.get('snippet','')!r} -> answer: {e.get('answer')} ; why: {e.get('why','')}" for e in ex[:6])
    na = (f"\nN/A rule: {c.get('na_hint')}" if c.get("na_allowed") and c.get("na_hint")
          else "\nN/A rule: N/A only if the situation never arose." if c.get("na_allowed") else "\nN/A is NOT allowed for this criterion.")
    return f"""{ctx}
CRITERION_ID: {c['id']}
CRITERION: {c['title']}
DEFINITION: {c.get('description','')}
ANSWER FORMAT: {_answer_schema(c)}{na}{ex_text}

TRANSCRIPT (turn numbers in brackets, numbered from 1 over all turns incl. client/bot/system):
{transcript_text}

Return JSON:
{{
  "answer": <{_answer_schema(c)}>,
  "confidence": <0.0-1.0>,
  "evidence_turns": [<turn numbers that prove the answer>],
  "checked_range": [<first turn inspected>, <last turn inspected>],
  "explanation": "<2-3 sentences in Russian: what the operator did / did not do, quoting key words>",
  "na_reason": "<only if answer is N/A: why the criterion does not apply>"
}}"""


def build_batch_prompt(checklist: dict, ctx: str, transcript_text: str, calibration: dict) -> str:
    crit = "\n".join(
        f"- id={c['id']} | {c['title']} | {c.get('description','')} | answer: {_answer_schema(c)}"
        + (f" | N/A rule: {c.get('na_hint')}" if c.get("na_allowed") and c.get("na_hint") else "")
        for c in checklist["criteria"])
    return f"""Evaluate ALL criteria below for the operator.
{ctx}
CRITERIA:
{crit}

TRANSCRIPT (turn numbers in brackets, numbered from 1 over all turns incl. client/bot/system):
{transcript_text}

Return JSON: {{"results": {{"<criterion id>": {{"answer": ..., "confidence": ..., "evidence_turns": [...], "checked_range": [a,b], "explanation": "...", "na_reason": "..."}}, ...}}}}"""


def _vote(c: dict, samples: list[dict]) -> dict:
    if len(samples) == 1:
        return samples[0]
    answers = [str(s.get("answer")).strip() for s in samples]
    na_votes = sum(a.upper() == "N/A" for a in answers)
    if na_votes * 2 > len(answers):
        chosen = next(s for s, a in zip(samples, answers) if a.upper() == "N/A")
    elif c["type"] in ("yes_no", "gross_error"):
        valid = [a.lower() for a in answers if a.upper() != "N/A"]
        top = Counter(valid).most_common(1)[0][0] if valid else "no"
        chosen = next((s for s, a in zip(samples, answers) if a.lower() == top), samples[0])
    else:
        nums = []
        for a in answers:
            try:
                nums.append(float(a))
            except ValueError:
                pass
        med = int(round(median(nums))) if nums else 0
        chosen = dict(min(samples, key=lambda s: abs(_num(s.get("answer")) - med)), answer=med)
    chosen = dict(chosen)
    chosen["votes"] = answers
    chosen["confidence"] = round(sum(float(s.get("confidence", 0.5) or 0.5) for s in samples) / len(samples), 2)
    return chosen


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _clean(c: dict, r: dict, n_turns: int) -> dict:
    ans = r.get("answer")
    if isinstance(ans, str) and ans.strip().upper() in ("N/A", "NA"):
        ans = "N/A" if c.get("na_allowed") else ("no" if c["type"] in ("yes_no", "gross_error") else int(c.get("scale", {}).get("min", 0)))
    elif c["type"] in ("yes_no", "gross_error"):
        ans = "yes" if str(ans).strip().lower() in ("yes", "да", "true", "1", "иә") else "no"
    else:
        ans = int(snap_to_steps(c, ans))  # clamp + snap to the allowed steps (e.g. 13 -> 15)
    ev = sorted({int(i) for i in r.get("evidence_turns", []) if str(i).lstrip("-").isdigit() and 1 <= int(i) <= n_turns})
    cr = r.get("checked_range") or [1, max(1, n_turns)]
    try:
        cr = [max(1, int(cr[0])), min(max(1, n_turns), int(cr[1]))]
    except (TypeError, ValueError, IndexError):
        cr = [1, max(1, n_turns)]
    return {"answer": ans, "confidence": float(r.get("confidence", 0.5) or 0.5), "evidence_turns": ev, "checked_range": cr,
            "explanation": str(r.get("explanation", "")).strip(), "na_reason": str(r.get("na_reason", "")).strip() if ans == "N/A" else "",
            "votes": r.get("votes")}


def evaluate(conversation: dict, checklist: dict, mode: str | None = None, samples: int | None = None) -> dict:
    lang = (conversation.get("language") or "").upper()
    mode = mode or EVAL_MODE
    samples = samples or SAMPLES_BY_LANG.get(lang, EVAL_SAMPLES)
    model = llm.model_for(lang)
    transcript = conversation["transcript"]
    text = format_transcript(transcript)
    pre = prechecks.run(conversation)
    ctx = _context_block(checklist, conversation, pre)
    calibration = _load_calibration()
    temp = 0.1 if samples == 1 else 0.4
    before = dict(llm.usage)
    trace: list[dict] = []
    results: dict[str, dict] = {}

    def call(prompt: str, label: str) -> dict:
        parsed, raw = llm.complete_json(SYSTEM_PROMPT, prompt, temperature=temp, model=model)
        trace.append({"label": label, "prompt": prompt, "raw_response": raw})
        return parsed

    if mode == "batch":
        prompt = build_batch_prompt(checklist, ctx, text, calibration)
        runs = [call(prompt, f"batch#{k+1}") for k in range(samples)]
        for c in checklist["criteria"]:
            per = [r.get("results", {}).get(c["id"], {}) for r in runs]
            results[c["id"]] = _clean(c, _vote(c, per), len(transcript))
    else:
        for c in checklist["criteria"]:
            prompt = build_criterion_prompt(c, ctx, text, calibration)
            per = [call(prompt, f"{c['id']}#{k+1}") for k in range(samples)]
            results[c["id"]] = _clean(c, _vote(c, per), len(transcript))

    totals = compute_totals(checklist, {cid: r["answer"] for cid, r in results.items()})
    used = {k: llm.usage[k] - before.get(k, 0) for k in ("calls", "prompt_tokens", "completion_tokens")}
    return {
        "criteria": results, "totals": totals, "prechecks": pre,
        "trace": [{"label": t["label"], "system_prompt": SYSTEM_PROMPT, **t} for t in trace],
        "meta": {"provider": llm.PROVIDER, "model": model, "mode": mode, "samples": samples, "usage": used,
                 "checklist_id": checklist["id"], "checklist_version": checklist.get("version", 1),
                 "calibration_examples": sum(len(v) for k, v in calibration.items() if not k.startswith("_"))},
    }
