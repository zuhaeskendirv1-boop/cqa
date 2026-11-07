"""
CLI twin of the "Калибровка" tab: re-score the hand-labeled dialogs and print the report.
Use it in a loop while tuning prompts / calibration.json / domain notes.

  python tools/compare_gt.py                         # report only
  python tools/compare_gt.py --reeval                # re-score labeled dialogs first (after a prompt change)
  python tools/compare_gt.py --reeval --samples 3 --mode per_criterion
  python tools/compare_gt.py --import-gt my_gt.json  # bulk-load labels: {"<conversation_id>": {"total": 95, "criteria": {...}}, ...}
"""
import argparse
import json
from pathlib import Path

import requests

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8000")
ap.add_argument("--checklist", default="default")
ap.add_argument("--reeval", action="store_true")
ap.add_argument("--samples", type=int, default=None)
ap.add_argument("--mode", default=None)
ap.add_argument("--import-gt", default=None)
ap.add_argument("--token", default="", help="ADMIN_TOKEN if roles are protected")
a = ap.parse_args()
H = {"X-Role": "admin", "X-Token": a.token}

if a.import_gt:
    for cid, g in json.loads(Path(a.import_gt).read_text(encoding="utf-8")).items():
        r = requests.post(f"{a.api}/api/ground_truth", params={"checklist_id": a.checklist}, headers=H,
                          json={"conversation_id": cid, "total": g.get("total"), "criteria": g.get("criteria", {}), "note": g.get("note", "")}, timeout=60)
        print("gt", cid, r.status_code, r.json() if r.status_code != 200 else "")

if a.reeval:
    for g in requests.get(f"{a.api}/api/ground_truth", timeout=60).json():
        cid = g["conversation_id"]
        r = requests.post(f"{a.api}/api/evaluate", json={"conversation_id": cid, "checklist_id": a.checklist, "samples": a.samples, "mode": a.mode}, timeout=900)
        print("re-evaluated", cid, r.json().get("ai_totals", {}).get("total") if r.ok else r.text)

rep = requests.get(f"{a.api}/api/calibration/report", timeout=60).json()
if not rep.get("n"):
    raise SystemExit(rep.get("message", "no data"))
print(f"\nDialogs: {rep['n']}   MAE={rep['mae']}   RMSE={rep['rmse']}   bias(AI-GT)={rep['bias']:+}   within ±5: {rep['within_5']:.0%}  ±10: {rep['within_10']:.0%}")
print("By language:", ", ".join(f"{k}: MAE {v['mae']} (n={v['n']})" for k, v in rep["by_language"].items()))
print("\nPer-criterion exact agreement:")
for k, v in sorted(rep["criteria"].items(), key=lambda kv: kv[1]["exact"]):
    print(f"  {k:22s} {v['exact']:.0%}  (n={v['n']}" + (f", mean |Δ|={v['mean_abs_delta']}" if v["mean_abs_delta"] is not None else "") + ")")
print("\nWorst dialogs:")
for r in rep["worst"][:5]:
    print(f"  {r['conversation_id']:14s} {r['language'] or '':3s} GT={r['gt']:>5}  AI={r['ai']:>5}  Δ={r['delta']:+}")
print("\n" + rep["advice"])
