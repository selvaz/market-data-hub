"""Explicit development capture, never imported by tests. Credentials stay in memory."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from market_data_hub.macro_releases.http import FetchError, HttpClient, api_key


def capture(client, name, url, *, provider, params=None, payload=None):
    root = Path(__file__).resolve().parents[1] / "tests/fixtures/macro_releases"
    base = root / name
    base.resolve().relative_to(root.resolve())
    if any(p for p in base.parent.glob(base.name + ".*") if ".meta." not in p.name):
        print(name, "already captured")
        return
    try:
        document = client.request(provider, url, params=params, payload=payload)
    except FetchError as exc:
        print(name, str(exc))
        return
    raw = document.body
    print(name, len(raw), "bytes")
    suffix = ".pdf" if raw.startswith(b"%PDF") else (
        ".html" if raw.lstrip().startswith((b"<!DOCTYPE", b"<html")) else (
            ".json" if raw.lstrip().startswith((b"{", b"[")) else ".csv"))
    target = base.with_suffix(suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    safe = {k: v for k, v in (params or payload or {}).items()
            if k.lower() not in {"api_key", "registrationkey", "userid"}}
    target.with_suffix(".meta.json").write_text(json.dumps({
        "url": url, "request": safe, "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "original_sha256": document.document_hash,
        "credential_redacted": hashlib.sha256(raw).hexdigest() != document.document_hash,
    }, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?")
    parser.add_argument("url", nargs="?")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--params", default="{}")
    parser.add_argument("--provider", choices=["bls", "bea", "fred", "ons", "eurostat"])
    args = parser.parse_args()
    if not args.plan and not (args.name and args.url):
        parser.error("provide --plan or both name and url")
    client = HttpClient()
    if args.plan:
        entries = json.loads(args.plan.read_text(encoding="utf-8"))
    else:
        entries = [dict(name=args.name, url=args.url, provider=args.provider,
                        params=json.loads(args.params))]
    fields = {"bls": ("BLS_API_KEY", "registrationkey"),
              "bea": ("BEA_API_KEY", "UserID"), "fred": ("FRED_API_KEY", "api_key")}
    for entry in entries:
        params = entry.get("params", {})
        provider = entry.get("provider")
        if provider in fields and entry.get("authenticate", True):
            env, field = fields[provider]
            params[field] = api_key(env)
        if not provider:
            provider = "ons" if "ons.gov.uk" in entry["url"] else "eurostat"
        capture(client, entry["name"], entry["url"], provider=provider, payload=params if provider == "bls" else None,
                params=None if provider == "bls" else params)


if __name__ == "__main__":
    main()
