"""Read-only official HTTP probes. Never opens a hub database.

Usage: python scripts/probe_sovereign_yields.py OUTPUT_DIR label=URL ...
Responses are saved for inspection; do not use a directory containing secrets.
"""
from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests


def probe(label, url, output):
    record = {"label": label, "url": url,
              "probed_at": datetime.now(timezone.utc).isoformat()}
    try:
        response = requests.get(url, timeout=(10, 35), headers={
            "User-Agent": "market-data-hub/0.1 (official statistics research)",
            "Connection": "close"})
        record.update(status=response.status_code, final_url=response.url,
                      content_type=response.headers.get("Content-Type"),
                      bytes=len(response.content),
                      sha256=hashlib.sha256(response.content).hexdigest())
        (output / f"{label}.body").write_bytes(response.content)
        record["excerpt"] = response.text[:250].replace("\n", " ")
    except requests.RequestException as exc:
        record["error"] = str(exc)
    (output / f"{label}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    jobs = [arg.split("=", 1) for arg in sys.argv[2:]]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(probe, label, url, output) for label, url in jobs]
        for future in as_completed(futures):
            print(json.dumps(future.result()), flush=True)


if __name__ == "__main__":
    main()
