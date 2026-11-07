"""
Official submission format (CQA Student Guide Appendix, JSON 1.0):

  results/dev/results.json   — DEV set (60 chats + 5 calls)
  results/prod/results.json  — 10 control chats

One object per dialog; evidence turns are 1-based over the full transcript; value true/false/null/number;
status "error" with empty criteria + score null when a dialog could not be scored.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from . import db, llm

OFFICIAL_IDS = ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]
TEAM_NAME = os.getenv("TEAM_NAME", "Название команды")


def model_info() -> dict:
    mode = os.getenv("MODEL_MODE")
    if not mode:
        mode = {"ollama": "local", "openai": "external_api", "mock": "local"}.get(llm.PROVIDER, "hybrid")
        if llm.MODEL_BY_LANG and any(m != llm.MODEL for m in llm.MODEL_BY_LANG.values()):
            mode = "hybrid"
    names = sorted({llm.MODEL, *llm.MODEL_BY_LANG.values()})
    return {"mode": mode, "name": ", ".join(names)}


def _value(ctype: str, answer):
    if answer is None or (isinstance(answer, str) and answer.upper() == "N/A"):
        return None
    if ctype in ("yes_no", "gross_error"):
        return str(answer).lower() == "yes"
    return int(answer)


def record_for(conv: dict, ev: dict | None) -> dict:
    cid = conv["conversation_id"]
    if ev is None:
        return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": "Диалог не был оценён."}
    if ev.get("status") == "error":
        return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": ev.get("error") or "Не удалось получить ответ модели."}
    chk = ev["checklist_snapshot"]
    crit_by_id = {c["id"]: c for c in chk["criteria"]}
    criteria = []
    for oid in OFFICIAL_IDS:
        c = crit_by_id.get(oid)
        if not c:
            continue
        ai = ev["ai"].get(oid, {})
        answer = ev["final_answers"].get(oid)
        rationale = ai.get("explanation", "")
        if answer == "N/A" and ai.get("na_reason"):
            rationale = f"N/A: {ai['na_reason']} {rationale}".strip()
        d = ev.get("decisions", {}).get(oid)
        if d:
            rationale = f"{rationale} [Решение проверяющего: {d['reason']}]".strip()
        turns = ai.get("evidence_turns") or []
        if not turns and ai.get("checked_range"):
            a, b = ai["checked_range"]
            turns = list(range(int(a), int(b) + 1))[:20]
        criteria.append({"criterion_id": oid, "value": _value(c["type"], answer), "rationale": rationale, "evidence_turns": turns})
    ft = ev["final_totals"]
    critical_val = _value("gross_error", ev["final_answers"].get("critical"))
    return {
        "conversation_id": cid,
        "status": "ok" if len(criteria) == len(OFFICIAL_IDS) and ft["total"] is not None else "error",
        "criteria": criteria if len(criteria) == len(OFFICIAL_IDS) else [],
        "score": {"final_score": round(float(ft["total"]), 2), "critical_flag": bool(critical_val)} if ft["total"] is not None else None,
        **({} if ft["total"] is not None and len(criteria) == len(OFFICIAL_IDS) else {"error": "Чек-лист не содержит все 8 официальных критериев или итог не рассчитан (нет применимых критериев)."}),
    }


def build(group: str | None = None) -> dict:
    latest: dict[str, dict] = {}
    for e in sorted(db.get_all("evaluations"), key=lambda e: e["created_at"]):
        latest[e["conversation_id"]] = e
    convs = [c for c in db.get_all("conversations") if not group or (c.get("dataset_group_id") or "").upper() == group.upper()]
    results = [record_for(c, latest.get(c["conversation_id"])) for c in sorted(convs, key=lambda c: c["conversation_id"])]
    return {"schema_version": "1.0", "team_name": TEAM_NAME, "model": model_info(), "results": results}


def validate(doc: dict) -> list[str]:
    problems = []
    ids = [r["conversation_id"] for r in doc["results"]]
    if len(ids) != len(set(ids)):
        problems.append("duplicate conversation_id")
    for r in doc["results"]:
        if r["status"] == "ok":
            got = [c["criterion_id"] for c in r["criteria"]]
            if got != OFFICIAL_IDS:
                problems.append(f"{r['conversation_id']}: criteria ids {got}")
            if not (0 <= r["score"]["final_score"] <= 100):
                problems.append(f"{r['conversation_id']}: score out of range")
        elif r["status"] != "error":
            problems.append(f"{r['conversation_id']}: bad status")
    return problems


def write(group: str, out_dir: Path) -> tuple[Path, dict, list[str]]:
    doc = build(group)
    out = out_dir / ("prod" if group.upper() != "DEV" else "dev") / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, doc, validate(doc)
