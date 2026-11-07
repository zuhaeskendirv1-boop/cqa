"""
CQA — Conversation Quality Analytics API.
Run:  uvicorn backend.main:app --port 8000   (from the repo root)

Roles (lightweight, no login per the hackathon guide): header X-Role = operator | reviewer | admin.
  reviewer/admin may decide appeals; admin may save checklists & ground truth.
  Set REVIEWER_TOKEN / ADMIN_TOKEN in .env to additionally require X-Token for those roles.
"""
from __future__ import annotations
import csv
import io
import json
import math
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, llm, prechecks, results_export
from .evaluator import evaluate
from .scoring import compute_totals

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
VERSION = "1.0.0"

@asynccontextmanager
async def lifespan(_: FastAPI):
    seed()
    yield


app = FastAPI(title="CQA — Conversation Quality Analytics", version=VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------- roles ----------

ROLE_TOKENS = {"reviewer": os.getenv("REVIEWER_TOKEN", ""), "admin": os.getenv("ADMIN_TOKEN", "")}


def role(x_role: str = Header("operator"), x_token: str = Header("")) -> str:
    r = (x_role or "operator").lower()
    if r not in ("operator", "reviewer", "admin"):
        raise HTTPException(400, "unknown role")
    need = ROLE_TOKENS.get(r, "")
    if need and x_token != need:
        raise HTTPException(403, f"invalid token for role {r}")
    return r


def require(*roles: str):
    def dep(r: str = Depends(role)) -> str:
        if r not in roles:
            raise HTTPException(403, f"requires role: {' or '.join(roles)}")
        return r
    return dep


# ---------- startup: seed default checklist + dialogs ----------


def seed() -> None:
    if not db.count("checklists"):
        chk = json.loads((ROOT / "backend" / "default_checklist.json").read_text(encoding="utf-8"))
        db.put("checklists", chk["id"], chk)
    if not db.count("conversations"):
        for p in [db.DATA_DIR / "dev.json", ROOT / "data" / "sample_dev.json"]:
            if p.exists():
                _import_records(json.loads(p.read_text(encoding="utf-8")))
                break
    for j in db.get_all("jobs"):  # a batch interrupted by a restart is marked, not lost
        if j.get("running"):
            j["running"] = False
            j["errors"].append({"conversation_id": "*", "error": "server restarted during batch"})
            db.put("jobs", j["id"], j)


def _import_records(payload: Any) -> int:
    if isinstance(payload, dict):
        records = payload.get("data", {}).get("records") if "data" in payload else payload.get("records")
        group = (payload.get("data") or {}).get("dataset_group_id", "manual")
    else:
        records, group = payload, "manual"
    if not isinstance(records, list):
        raise HTTPException(400, "Expected organizers' JSON ({data:{records:[...]}}) or a list of records")
    n = 0
    for r in records:
        if not isinstance(r, dict) or not isinstance(r.get("transcript"), list):
            continue
        cid = str(r.get("conversation_id") or db.new_id("conv"))
        db.put("conversations", cid, {
            "conversation_id": cid, "channel": r.get("channel", "agent chat"), "language": (r.get("language") or "RU").upper(),
            "transcript": [{"timestamp": t.get("timestamp"), "speaker": str(t.get("speaker", "")).lower(), "text": str(t.get("text", ""))} for t in r["transcript"] if isinstance(t, dict)],
            "audio_url": r.get("audio_url") or None, "dataset_group_id": group,
        })
        n += 1
    return n


# ---------- checklists ----------


@app.get("/api/checklists")
def list_checklists():
    return db.get_all("checklists")


@app.get("/api/checklists/{cid}")
def get_checklist(cid: str):
    c = db.get("checklists", cid)
    if not c:
        raise HTTPException(404, "checklist not found")
    return c


@app.post("/api/checklists", dependencies=[Depends(require("admin"))])
def save_checklist(chk: dict):
    """Create or update. Every save bumps `version`; evaluations keep a frozen snapshot of the version they used."""
    if not chk.get("criteria"):
        raise HTTPException(400, "checklist needs criteria")
    cid = chk.get("id") or db.new_id("chk")
    old = db.get("checklists", cid)
    chk["id"] = cid
    chk["version"] = (old.get("version", 0) + 1) if old else 1
    chk.setdefault("rules", {})
    chk.setdefault("domain_notes", "")
    seen = set()
    for c in chk["criteria"]:
        c["id"] = (c.get("id") or db.new_id("crit")).strip()
        if c["id"] in seen:
            raise HTTPException(400, f"duplicate criterion id {c['id']}")
        seen.add(c["id"])
        c.setdefault("type", "yes_no")
        c["weight"] = float(c.get("weight", 10))
        if c["weight"] < 0:
            raise HTTPException(400, f"negative weight on {c['id']}")
        c.setdefault("na_allowed", False)
        if c["type"] not in ("yes_no", "scale", "gross_error"):
            raise HTTPException(400, f"unknown type {c['type']} on {c['id']}")
        if c["type"] == "gross_error":
            c["weight"] = 0.0
            c["na_allowed"] = False
        if c["type"] == "scale":
            sc = c.setdefault("scale", {"min": 0, "max": 2})
            steps = sc.get("steps") or []
            if steps:
                steps = sorted({int(x) for x in steps})
                sc["min"], sc["max"], sc["steps"] = steps[0], steps[-1], steps
            if int(sc.get("max", 0)) <= int(sc.get("min", 0)):
                raise HTTPException(400, f"scale max must be > min on {c['id']}")
    if sum(c["weight"] for c in chk["criteria"]) <= 0:
        raise HTTPException(400, "total weight must be > 0")
    return db.put("checklists", cid, chk)


@app.delete("/api/checklists/{cid}", dependencies=[Depends(require("admin"))])
def delete_checklist(cid: str):
    if cid == "default":
        raise HTTPException(400, "cannot delete the default checklist; edit it instead")
    db.delete("checklists", cid)
    return {"ok": True}


# ---------- conversations ----------


class ImportBody(BaseModel):
    url: str | None = None
    payload: Any | None = None


@app.post("/api/conversations/import")
def import_conversations(body: ImportBody):
    """Import from the organizers' GET URL (server-side, so no CORS) or from pasted JSON."""
    if body.url:
        try:
            r = requests.get(body.url, timeout=90)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"fetch failed: {e}")
    elif body.payload is not None:
        payload = body.payload
    else:
        raise HTTPException(400, "give url or payload")
    if isinstance(payload, dict) and payload.get("isSuccess") is False:
        raise HTTPException(502, f"organizers API error: {payload.get('error')}")
    return {"imported": _import_records(payload)}


def _latest_evals() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for e in sorted(db.get_all("evaluations"), key=lambda e: e["created_at"]):
        latest[e["conversation_id"]] = e
    return latest


@app.get("/api/conversations")
def list_conversations():
    latest = _latest_evals()
    gt = {g["conversation_id"] for g in db.get_all("ground_truth")}
    out = []
    for c in sorted(db.get_all("conversations"), key=lambda c: c["conversation_id"]):
        e = latest.get(c["conversation_id"])
        out.append({
            "conversation_id": c["conversation_id"], "channel": c["channel"], "language": c["language"], "turns": len(c["transcript"]),
            "has_audio": bool(c.get("audio_url")), "group": c.get("dataset_group_id"), "has_gt": c["conversation_id"] in gt,
            "evaluation_id": e["id"] if e else None, "total": e["final_totals"]["total"] if e else None, "error": e.get("error") if e else None,
            "gross_error": e["final_totals"]["gross_error"] if e else None, "status": e["status"] if e else "new",
        })
    return out


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    c = db.get("conversations", cid)
    if not c:
        raise HTTPException(404, "conversation not found")
    c["prechecks"] = prechecks.run(c)
    return c


# ---------- evaluation ----------


class EvalBody(BaseModel):
    conversation_id: str
    checklist_id: str = "default"
    mode: str | None = None
    samples: int | None = None


def _run_eval(conversation_id: str, checklist_id: str, mode: str | None, samples: int | None) -> dict:
    conv = db.get("conversations", conversation_id)
    chk = db.get("checklists", checklist_id)
    if not conv or not chk:
        raise HTTPException(404, "conversation or checklist not found")
    if samples is not None and not 1 <= samples <= 5:
        raise HTTPException(400, "samples must be 1..5")
    res = evaluate(conv, chk, mode=mode, samples=samples)
    eid = db.new_id("eval")
    doc = {
        "id": eid, "conversation_id": conversation_id, "checklist_id": checklist_id, "checklist_snapshot": chk,
        "ai": res["criteria"], "ai_totals": res["totals"],
        "final_answers": {cid: r["answer"] for cid, r in res["criteria"].items()}, "final_totals": res["totals"],
        "decisions": {}, "status": "ai_scored", "meta": res["meta"], "prechecks": res["prechecks"], "trace": res["trace"],
    }
    return db.put("evaluations", eid, doc)


@app.post("/api/evaluate")
def run_evaluation(body: EvalBody):
    return _run_eval(body.conversation_id, body.checklist_id, body.mode, body.samples)


class BatchBody(BaseModel):
    conversation_ids: list[str] | None = None  # None = all
    checklist_id: str = "default"
    mode: str | None = None
    samples: int | None = None


@app.post("/api/evaluate/batch")
def run_batch(body: BatchBody):
    """Score many dialogs in a background thread; poll /api/evaluate/batch/status. Job state is persisted."""
    if any(j.get("running") for j in db.get_all("jobs")):
        raise HTTPException(409, "batch already running")
    ids = body.conversation_ids or [c["conversation_id"] for c in db.get_all("conversations")]
    job = {"id": db.new_id("job"), "running": True, "done": 0, "total": len(ids), "errors": [], "checklist_id": body.checklist_id}
    db.put("jobs", job["id"], job)

    def worker():
        for cid in ids:
            try:
                _run_eval(cid, body.checklist_id, body.mode, body.samples)
            except Exception as e:  # noqa: BLE001
                job["errors"].append({"conversation_id": cid, "error": str(e)})
                eid = db.new_id("eval")  # keep the failure: it becomes status="error" in results.json, never a silent zero
                db.put("evaluations", eid, {"id": eid, "conversation_id": cid, "checklist_id": body.checklist_id, "status": "error", "error": str(e),
                                            "ai": {}, "ai_totals": {"total": None, "gross_error": False, "sections": {}, "criteria": {}, "total_scale": 100, "gross_error_criteria": []},
                                            "final_answers": {}, "final_totals": {"total": None, "gross_error": False, "sections": {}, "criteria": {}, "total_scale": 100, "gross_error_criteria": []},
                                            "decisions": {}, "meta": {"provider": llm.PROVIDER, "model": llm.MODEL, "mode": body.mode, "samples": body.samples, "usage": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}, "checklist_id": body.checklist_id, "checklist_version": 0}, "trace": []})
            job["done"] += 1
            db.put("jobs", job["id"], job)
        job["running"] = False
        db.put("jobs", job["id"], job)

    threading.Thread(target=worker, daemon=True).start()
    return job


@app.get("/api/evaluate/batch/status")
def batch_status():
    jobs = db.get_all("jobs")
    return jobs[-1] if jobs else {"running": False, "done": 0, "total": 0, "errors": []}


@app.get("/api/evaluations")
def list_evaluations(conversation_id: str | None = None):
    ev = db.get_all("evaluations")
    if conversation_id:
        ev = [e for e in ev if e["conversation_id"] == conversation_id]
    for e in ev:
        e.pop("trace", None)  # heavy; fetch via /api/evaluations/{id}/trace
    return sorted(ev, key=lambda e: e["created_at"], reverse=True)


@app.get("/api/evaluations/{eid}")
def get_evaluation(eid: str):
    e = db.get("evaluations", eid)
    if not e:
        raise HTTPException(404, "evaluation not found")
    e.pop("trace", None)
    return e


@app.get("/api/evaluations/{eid}/trace")
def get_trace(eid: str):
    """Full prompts + raw model responses: proof the system analyses each dialog live."""
    e = db.get("evaluations", eid)
    if not e:
        raise HTTPException(404, "evaluation not found")
    return {"evaluation_id": eid, "meta": e["meta"], "trace": e.get("trace", [])}


# ---------- appeals & review ----------


class AppealBody(BaseModel):
    criterion_id: str
    comment: str
    author: str = "operator"


@app.post("/api/evaluations/{eid}/appeals")
def create_appeal(eid: str, body: AppealBody):
    e = db.get("evaluations", eid)
    if not e:
        raise HTTPException(404, "evaluation not found")
    if body.criterion_id not in e["ai"]:
        raise HTTPException(400, "unknown criterion")
    if not body.comment.strip():
        raise HTTPException(400, "comment required")
    if any(a["evaluation_id"] == eid and a["criterion_id"] == body.criterion_id and a["status"] == "open" for a in db.get_all("appeals")):
        raise HTTPException(409, "an open appeal already exists for this criterion")
    aid = db.new_id("appeal")
    appeal = {"id": aid, "evaluation_id": eid, "conversation_id": e["conversation_id"], "criterion_id": body.criterion_id,
              "comment": body.comment.strip(), "author": body.author, "disputed_answer": e["final_answers"][body.criterion_id],
              "evidence_turns": e["ai"][body.criterion_id]["evidence_turns"], "status": "open", "decision": None}
    db.put("appeals", aid, appeal)
    e["status"] = "appealed"
    db.put("evaluations", eid, e)
    return appeal


@app.get("/api/appeals")
def list_appeals(status: str | None = None):
    a = db.get_all("appeals")
    if status:
        a = [x for x in a if x["status"] == status]
    return sorted(a, key=lambda x: x["created_at"], reverse=True)


class DecisionBody(BaseModel):
    decision: str  # "confirm" | "change"
    new_answer: Any | None = None
    reason: str
    reviewer: str = "reviewer"


@app.post("/api/appeals/{aid}/decision")
def decide(aid: str, body: DecisionBody, r: str = Depends(require("reviewer", "admin"))):
    a = db.get("appeals", aid)
    if not a:
        raise HTTPException(404, "appeal not found")
    if a["status"] != "open":
        raise HTTPException(409, "appeal already decided")
    if body.decision not in ("confirm", "change"):
        raise HTTPException(400, "decision must be confirm|change")
    if not body.reason.strip():
        raise HTTPException(400, "reason required")
    e = db.get("evaluations", a["evaluation_id"])
    chk = e["checklist_snapshot"]
    crit = next(c for c in chk["criteria"] if c["id"] == a["criterion_id"])
    if body.decision == "change":
        if body.new_answer is None:
            raise HTTPException(400, "new_answer required")
        na = str(body.new_answer).upper() == "N/A"
        if na and not crit.get("na_allowed"):
            raise HTTPException(400, "N/A not allowed for this criterion")
        if not na and crit["type"] == "scale":
            try:
                v = int(body.new_answer)
            except (TypeError, ValueError):
                raise HTTPException(400, "scale answer must be an integer")
            if not crit["scale"]["min"] <= v <= crit["scale"]["max"]:
                raise HTTPException(400, "answer outside scale")
            steps = crit["scale"].get("steps")
            if steps and v not in steps:
                raise HTTPException(400, f"answer must be one of {steps}")
            body.new_answer = v
        if not na and crit["type"] in ("yes_no", "gross_error") and str(body.new_answer).lower() not in ("yes", "no"):
            raise HTTPException(400, "answer must be yes|no")
        e["final_answers"][a["criterion_id"]] = "N/A" if na else body.new_answer
    e["decisions"][a["criterion_id"]] = {"appeal_id": aid, "decision": body.decision, "original_answer": a["disputed_answer"],
                                        "final_answer": e["final_answers"][a["criterion_id"]], "reason": body.reason.strip(),
                                        "reviewer": body.reviewer, "role": r, "decided_at": db.now()}
    e["final_totals"] = compute_totals(chk, e["final_answers"])
    open_left = [x for x in db.get_all("appeals") if x["evaluation_id"] == e["id"] and x["status"] == "open" and x["id"] != aid]
    e["status"] = "appealed" if open_left else "reviewed"
    db.put("evaluations", e["id"], e)
    a["status"] = "decided"
    a["decision"] = e["decisions"][a["criterion_id"]]
    db.put("appeals", aid, a)
    e.pop("trace", None)
    return {"appeal": a, "evaluation": e}


# ---------- ground truth & calibration report ----------


class GTBody(BaseModel):
    conversation_id: str
    total: float | None = None
    criteria: dict[str, Any] = {}
    note: str = ""


@app.get("/api/ground_truth")
def list_gt():
    return db.get_all("ground_truth")


@app.post("/api/ground_truth", dependencies=[Depends(require("admin", "reviewer"))])
def save_gt(body: GTBody, checklist_id: str = "default"):
    """Hand label by the team. If `total` is omitted it is computed from `criteria` with the checklist rules."""
    if not db.get("conversations", body.conversation_id):
        raise HTTPException(404, "conversation not found")
    chk = db.get("checklists", checklist_id)
    total = body.total
    if total is None and body.criteria:
        total = compute_totals(chk, body.criteria)["total"]
    if total is None:
        raise HTTPException(400, "give total or criteria")
    return db.put("ground_truth", body.conversation_id, {"conversation_id": body.conversation_id, "total": total, "criteria": body.criteria, "note": body.note, "checklist_id": checklist_id})


@app.delete("/api/ground_truth/{cid}", dependencies=[Depends(require("admin", "reviewer"))])
def delete_gt(cid: str):
    db.delete("ground_truth", cid)
    return {"ok": True}


@app.get("/api/calibration/report")
def calibration_report():
    """MAE / RMSE / bias of AI totals vs hand labels, per-criterion agreement, per-language, worst dialogs."""
    latest = _latest_evals()
    rows, crit, by_lang = [], {}, {}
    for g in db.get_all("ground_truth"):
        e = latest.get(g["conversation_id"])
        if not e:
            continue
        conv = db.get("conversations", g["conversation_id"]) or {}
        s, t = e["ai_totals"]["total"], g["total"]
        if s is None or t is None:
            continue
        rows.append({"conversation_id": g["conversation_id"], "gt": t, "ai": s, "delta": round(s - t, 2), "language": conv.get("language")})
        by_lang.setdefault(conv.get("language"), []).append(abs(s - t))
        for k, v in (g.get("criteria") or {}).items():
            sv = e["ai"].get(k, {}).get("answer")
            d = crit.setdefault(k, {"n": 0, "exact": 0, "abs_delta": []})
            d["n"] += 1
            d["exact"] += int(str(sv).lower() == str(v).lower())
            try:
                d["abs_delta"].append(abs(float(sv) - float(v)))
            except (TypeError, ValueError):
                pass
    n = len(rows)
    if not n:
        return {"n": 0, "labeled": db.count("ground_truth"), "message": "Разметьте диалоги (вкладка Разметка) и оцените их — тогда появится отчёт."}
    mae = sum(abs(r["delta"]) for r in rows) / n
    rmse = math.sqrt(sum(r["delta"] ** 2 for r in rows) / n)
    bias = sum(r["delta"] for r in rows) / n
    return {
        "n": n, "labeled": db.count("ground_truth"), "mae": round(mae, 2), "rmse": round(rmse, 2), "bias": round(bias, 2),
        "within_5": round(sum(abs(r["delta"]) <= 5 for r in rows) / n, 2), "within_10": round(sum(abs(r["delta"]) <= 10 for r in rows) / n, 2),
        "by_language": {k: {"n": len(v), "mae": round(sum(v) / len(v), 2)} for k, v in by_lang.items()},
        "criteria": {k: {"n": d["n"], "exact": round(d["exact"] / d["n"], 2), "mean_abs_delta": round(sum(d["abs_delta"]) / len(d["abs_delta"]), 2) if d["abs_delta"] else None} for k, d in crit.items()},
        "worst": sorted(rows, key=lambda r: -abs(r["delta"]))[:10],
        "advice": ("AI слишком щедр: ужесточьте формулировки критериев / добавьте примеры среднего балла в calibration.json" if bias > 3
                   else "AI слишком строг: смягчите формулировки или добавьте примеры высокого балла" if bias < -3 else "Смещение в норме; работайте над худшими диалогами."),
    }


# ---------- export / stats ----------


@app.get("/api/export.csv")
def export_csv(checklist_id: str | None = None):
    """Final scores per dialog — hand this to organizers for Ground-Truth comparison."""
    latest = {k: v for k, v in _latest_evals().items() if not checklist_id or v["checklist_id"] == checklist_id}
    crit_ids: list[str] = []
    for e in latest.values():
        for cid in e["final_answers"]:
            if cid not in crit_ids:
                crit_ids.append(cid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["conversation_id", "language", "channel", "total", "ai_total", "gross_error", *crit_ids])
    for cid, e in sorted(latest.items()):
        c = db.get("conversations", cid) or {}
        w.writerow([cid, c.get("language"), c.get("channel"), e["final_totals"]["total"], e["ai_totals"]["total"], int(e["final_totals"]["gross_error"]), *[e["final_answers"].get(k, "") for k in crit_ids]])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=cqa_results.csv"})


@app.get("/api/export/results.json")
def export_results(group: str | None = None):
    """Official submission JSON (Appendix, schema 1.0). ?group=DEV / ?group=<control group id>; omit for all dialogs."""
    doc = results_export.build(group)
    return JSONResponse(doc, headers={"Content-Disposition": f"attachment; filename=results_{(group or 'all').lower()}.json", "X-CQA-Problems": str(len(results_export.validate(doc)))})


class WriteResultsBody(BaseModel):
    group: str
    target: str = "dev"  # dev | prod


@app.post("/api/export/results/write")
def write_results(body: WriteResultsBody):
    """Write results/<dev|prod>/results.json into the repo and validate it — then `git add results && git commit && git push`."""
    doc = results_export.build(body.group)
    out = ROOT / "results" / ("prod" if body.target == "prod" else "dev") / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    probs = results_export.validate(doc)
    n_ok = sum(r["status"] == "ok" for r in doc["results"])
    return {"path": str(out.relative_to(ROOT)), "records": len(doc["results"]), "ok": n_ok, "error": len(doc["results"]) - n_ok, "problems": probs, "team_name": doc["team_name"], "model": doc["model"]}


@app.get("/api/groups")
def groups():
    g: dict[str, int] = {}
    for c in db.get_all("conversations"):
        g[c.get("dataset_group_id") or "manual"] = g.get(c.get("dataset_group_id") or "manual", 0) + 1
    return g


@app.get("/api/export.json")
def export_json():
    latest = _latest_evals()
    return [{"conversation_id": cid, "total": e["final_totals"]["total"], "ai_total": e["ai_totals"]["total"], "gross_error": e["final_totals"]["gross_error"],
             "criteria": e["final_answers"], "checklist_version": e["meta"]["checklist_version"]} for cid, e in sorted(latest.items())]


@app.get("/api/stats")
def stats():
    return {"version": VERSION, "llm": llm.cost_estimate(), "conversations": db.count("conversations"), "evaluations": db.count("evaluations"),
            "open_appeals": len([a for a in db.get_all("appeals") if a["status"] == "open"]), "ground_truth": db.count("ground_truth"),
            "roles_protected": {k: bool(v) for k, v in ROLE_TOKENS.items()}, "team_name": results_export.TEAM_NAME, "submission_model": results_export.model_info()}


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION, "provider": llm.PROVIDER, "model": llm.MODEL}


# ---------- frontend ----------

app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
