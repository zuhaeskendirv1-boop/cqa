"""
Download a dataset from the organizers' API and save it to data/<group_id>.json.

  python tools/fetch_dataset.py "<GET url from the PDF button>"            -> data/dev.json
  python tools/fetch_dataset.py "<url>" --group CONTROL123                 -> replaces group_id in the url, saves data/control123.json

Then either restart the server (data/dev.json is auto-imported on first start)
or import via the UI / POST /api/conversations/import {"payload": <file contents>}.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests

ap = argparse.ArgumentParser()
ap.add_argument("url")
ap.add_argument("--group", default=None, help="override group_id query param")
ap.add_argument("--out", default=None)
a = ap.parse_args()

url = a.url
if a.group:
    url = re.sub(r"group_id=[^&]*", f"group_id={a.group}", url) if "group_id=" in url else url + f"&group_id={a.group}"
gid = re.search(r"group_id=([^&]*)", url)
gid = gid.group(1) if gid else "dataset"

r = requests.get(url, timeout=120)
r.raise_for_status()
payload = r.json()
if not payload.get("isSuccess", True):
    sys.exit(f"API error: {payload.get('error')}")
recs = payload.get("data", {}).get("records", [])
out = Path(a.out or Path(__file__).resolve().parent.parent / "data" / f"{gid.lower()}.json")
out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
langs = {}
for x in recs:
    langs[x.get("language")] = langs.get(x.get("language"), 0) + 1
print(f"saved {len(recs)} records -> {out}  languages={langs}  calls={sum(1 for x in recs if x.get('channel')=='voice call')}")
