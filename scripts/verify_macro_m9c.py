"""Opt-in six-bank live verification on a private SQLite backup under D:\\Temp.

The production files are read only as bytes via preview_store; SQLite never
opens production, even read-only. All migrations and collection affect the copy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_data_hub.macro_releases import open_reader
from market_data_hub.macro_releases.catalogue import CENTRAL_BANKS
from market_data_hub.macro_releases.run_daily import preview_store


def fingerprint(path):
    return dict(size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def successful_run(run, label):
    """The collector deliberately exits 3 when an idempotent run does no work."""
    expected = "no_change" if label == "idempotence" else "ok"
    return (run["exit_code"] == (3 if label == "idempotence" else 0)
            and set(run["families"]) == set(CENTRAL_BANKS)
            and all(r["status"] == expected for r in run["families"].values()))


def assess(report):
    return (all(successful_run(report[label], label) for label in ("dry_run", "copy_collection", "idempotence"))
            and report["integrity"] == "ok" and report["source_main_unchanged"]
            and set(report["latest_cards"]) == set(CENTRAL_BANKS)
            and all(c["resolution"]["status"] == "official" and c["policy_decision"]
                    and c["previous"] is not None and c["previous_revised"] is not None
                    and c["previous_period"] == c["previous"]["reference_period"]
                    for c in report["latest_cards"].values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    args = parser.parse_args()
    source, root = args.source.resolve(), args.destination_root.resolve()
    if not source.is_file() or root == source.parent or any(p.lower() == "programdata" for p in root.parts):
        raise ValueError("Existing source and separate non-production backup destination required")
    before = fingerprint(source)
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="mdh-m9c-", dir=root))
    target = directory / "macro_releases.sqlite"
    with preview_store(source) as snapshot:
        with sqlite3.connect(str(target)) as destination:
            snapshot.conn.backup(destination)
    raw = directory / "no-consensus-archive"
    raw.mkdir()
    report = dict(copy=str(target), backup_method="SQLite backup API from stable private main/WAL snapshot",
                  source_main_before=before)
    command = [sys.executable, "-m", "market_data_hub.macro_releases.run_daily",
               "--families", *CENTRAL_BANKS, "--db", str(target), "--consensus-raw-dir", str(raw)]
    passed = True
    print("SQLite backup:", target, flush=True)
    for label, extra in (("dry_run", ["--dry-run"]), ("copy_collection", []), ("idempotence", [])):
        result = subprocess.run(command + extra, capture_output=True, text=True, check=False)
        try:
            run = report[label] = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("Verification subprocess did not return JSON") from None
        compact = {f: {k: r.get(k) for k in ("status", "vintages_added", "review_reasons", "error_message")}
                   for f, r in run["families"].items()}
        print(label, json.dumps(compact, indent=2), flush=True)
        passed &= successful_run(run, label)
        (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with open_reader(target) as store:
        report["integrity"] = store.conn.execute("PRAGMA quick_check").fetchone()[0]
        cards = store.release_cards("2026-01-01", "2026-12-31",
                    indicators=[m.id for measures in CENTRAL_BANKS.values() for m in measures])["cards"]
        latest = {}
        for family in CENTRAL_BANKS:
            candidates = [c for c in cards if c["family"] == family and c["resolution"]["status"] == "official"]
            if not candidates:
                passed = False
                continue
            latest[family] = max(candidates, key=lambda c: c["reference_period"])
        report["latest_cards"] = latest
        report["card_counts"] = {f: len([c for c in cards if c["family"] == f]) for f in CENTRAL_BANKS}
    report["source_main_after"] = fingerprint(source)
    report["source_main_unchanged"] = before == report["source_main_after"]
    report["publication_time_gaps"] = {f: c["policy_decision"]["time_missing_reason"]
        for f, c in latest.items() if c["policy_decision"]["publication_precision"] != "exact"}
    report["status"] = "ok" if passed and assess(report) else "failed"
    report["exact_publication_times_complete"] = not report["publication_time_gaps"]
    path = directory / "verification.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Card counts:", json.dumps(report["card_counts"]), flush=True)
    print("Previous decisions:", json.dumps({f: dict(period=c["previous_period"],
        value=c["previous"]["value"] if c["previous"] else None)
        for f, c in latest.items()}), flush=True)
    print("Publication time gaps:", json.dumps(report["publication_time_gaps"]), flush=True)
    print("Report:", path, flush=True)
    print("Verification:", report["status"], flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
