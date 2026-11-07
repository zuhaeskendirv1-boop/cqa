"""Write results/<dev|prod>/results.json in the official format and validate it (server must be running)."""
import argparse, json, requests
ap = argparse.ArgumentParser()
ap.add_argument("--group", required=True, help="dataset group_id as imported (DEV or the control id)")
ap.add_argument("--target", default="dev", choices=["dev", "prod"])
ap.add_argument("--api", default="http://localhost:8000")
a = ap.parse_args()
r = requests.post(f"{a.api}/api/export/results/write", json={"group": a.group, "target": a.target}, timeout=120)
r.raise_for_status()
d = r.json()
print(json.dumps(d, ensure_ascii=False, indent=2))
if d["problems"]:
    raise SystemExit("VALIDATION PROBLEMS — fix before push")
print(f"\nOK -> {d['path']}  (ok={d['ok']}, error={d['error']})\nNext: git add results && git commit -m 'results {a.target}' && git push")
