"""Opt-in capture of public issuer evidence for offline M9f certification."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import certifi
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("url")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "tests/fixtures/macro_releases/m9f"
    root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    response = session.get(args.url, timeout=(10, 45), verify=certifi.where(),
                           headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    target = root / args.name
    target.write_bytes(response.content)
    meta = dict(url=response.url, retrieved_at=datetime.now(timezone.utc).isoformat(),
                sha256=hashlib.sha256(response.content).hexdigest())
    target.with_name(target.name + ".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(args.name, response.status_code, len(response.content), response.url)


if __name__ == "__main__":
    main()
