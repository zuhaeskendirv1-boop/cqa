import os, tempfile
os.environ["LLM_PROVIDER"] = "mock"
os.environ["CQA_DB"] = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
from fastapi.testclient import TestClient
from backend.main import app

ADMIN = {"X-Role": "admin"}
REV = {"X-Role": "reviewer"}


def client():
    c = TestClient(app)
    c.__enter__()  # triggers startup seed
    return c


def test_full_flow():
    c = client()
    assert c.get("/api/health").json()["ok"]
    convs = c.get("/api/conversations").json()
    assert len(convs) >= 4 and convs[0]["status"] == "new"
    conv = c.get("/api/conversations/s-ru-002").json()
    assert any(f["flag"] == "asks_password" for f in conv["prechecks"]["flags"])

    e = c.post("/api/evaluate", json={"conversation_id": "s-ru-002"}).json()
    assert e["status"] == "ai_scored" and set(e["ai"]) == {x["id"] for x in e["checklist_snapshot"]["criteria"]}
    assert e["final_totals"]["gross_error"] is True  # mock: password request -> gross_error=yes
    assert e["final_totals"]["total"] is not None
    assert len(c.get(f"/api/evaluations/{e['id']}/trace").json()["trace"]) == 8

    # appeal as operator, decision requires reviewer
    a = c.post(f"/api/evaluations/{e['id']}/appeals", json={"criterion_id": "contact", "comment": "спорно"}).json()
    assert a["status"] == "open"
    assert c.post(f"/api/appeals/{a['id']}/decision", json={"decision": "change", "new_answer": "yes", "reason": "x"}).status_code == 403
    r = c.post(f"/api/appeals/{a['id']}/decision", json={"decision": "change", "new_answer": "yes", "reason": "x"}, headers=REV).json()
    assert r["evaluation"]["final_answers"]["contact"] == "yes" and r["evaluation"]["status"] == "reviewed"
    assert r["evaluation"]["final_totals"]["total"] >= e["final_totals"]["total"]
    assert c.post(f"/api/appeals/{a['id']}/decision", json={"decision": "confirm", "reason": "x"}, headers=REV).status_code == 409

    # invalid decision values are rejected
    a3 = c.post(f"/api/evaluations/{e['id']}/appeals", json={"criterion_id": "empathy", "comment": "спорно"}).json()
    assert c.post(f"/api/appeals/{a3['id']}/decision", json={"decision": "change", "new_answer": "N/A", "reason": "x"}, headers=REV).status_code == 400  # N/A not allowed
    a2 = c.post(f"/api/evaluations/{e['id']}/appeals", json={"criterion_id": "consultation", "comment": "спорно"}).json()
    assert c.post(f"/api/appeals/{a2['id']}/decision", json={"decision": "change", "new_answer": 13, "reason": "x"}, headers=REV).status_code == 400  # not a step
    ok = c.post(f"/api/appeals/{a2['id']}/decision", json={"decision": "change", "new_answer": 15, "reason": "x"}, headers=REV).json()
    assert ok["evaluation"]["final_answers"]["consultation"] == 15


def test_checklist_versioning_and_roles():
    c = client()
    chk = c.get("/api/checklists/default").json()
    assert c.post("/api/checklists", json=chk).status_code == 403
    chk["criteria"][0]["weight"] = 15
    v = c.post("/api/checklists", json=chk, headers=ADMIN).json()["version"]
    assert v == chk["version"] + 1
    chk["criteria"][0]["weight"] = 10
    c.post("/api/checklists", json=chk, headers=ADMIN)  # restore for other tests
    steps = c.post("/api/checklists", json=dict(chk, id="copy1", criteria=[dict(chk["criteria"][3], scale={"steps": [20, 0, 10, 5, 15]})]), headers=ADMIN).json()
    assert steps["criteria"][0]["scale"]["steps"] == [0, 5, 10, 15, 20] and steps["criteria"][0]["scale"]["max"] == 20
    bad = dict(chk); bad["criteria"] = [dict(chk["criteria"][0], type="scale", scale={"min": 2, "max": 1})]
    assert c.post("/api/checklists", json=bad, headers=ADMIN).status_code == 400


def test_ground_truth_and_report():
    c = client()
    c.post("/api/evaluate", json={"conversation_id": "s-ru-001"})
    assert c.post("/api/ground_truth", json={"conversation_id": "s-ru-001", "total": 90}).status_code == 403
    g = c.post("/api/ground_truth", json={"conversation_id": "s-ru-001", "criteria": {"contact": "yes", "discovery": "yes", "identification": "N/A", "consultation": 15, "empathy": "yes", "dialogue": 10, "closure": "yes", "critical": "no"}}, headers=REV).json()
    assert g["total"] == 93.75  # (10+15+15+15+15+5)/80*100
    rep = c.get("/api/calibration/report").json()
    assert rep["n"] >= 1 and "mae" in rep and "contact" in rep["criteria"]


def test_results_json_official_format():
    c = client()
    c.post("/api/evaluate", json={"conversation_id": "s-en-001"})
    doc = c.get("/api/export/results.json?group=SAMPLE").json()
    assert doc["schema_version"] == "1.0" and doc["model"]["mode"] in ("local", "external_api", "hybrid")
    rec = next(r for r in doc["results"] if r["conversation_id"] == "s-en-001")
    assert rec["status"] == "ok"
    assert [x["criterion_id"] for x in rec["criteria"]] == ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]
    assert all(isinstance(x["value"], (bool, int)) or x["value"] is None for x in rec["criteria"])
    assert all(t >= 1 for x in rec["criteria"] for t in x["evidence_turns"])  # 1-based
    assert isinstance(rec["score"]["final_score"], float) and isinstance(rec["score"]["critical_flag"], bool)
    unscored = [r for r in doc["results"] if r["status"] == "error"]
    assert all(r["criteria"] == [] and r["score"] is None and r["error"] for r in unscored)
    w = c.post("/api/export/results/write", json={"group": "SAMPLE", "target": "dev"}).json()
    assert w["path"] == "results/dev/results.json" and w["problems"] == []


def test_import_and_export():
    c = client()
    n = c.post("/api/conversations/import", json={"payload": {"data": {"dataset_group_id": "T", "records": [{"conversation_id": "t-1", "channel": "agent chat", "language": "ru", "transcript": [{"timestamp": "00:00", "speaker": "Operator", "text": "Здравствуйте"}]}]}}}).json()["imported"]
    assert n == 1 and c.get("/api/conversations/t-1").json()["language"] == "RU"
    assert c.post("/api/conversations/import", json={"payload": {"foo": 1}}).status_code == 400
    csv = c.get("/api/export.csv").text
    assert csv.startswith("conversation_id,language,channel,total")
