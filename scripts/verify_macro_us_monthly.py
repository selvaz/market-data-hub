"""Opt-in live M9a verification on a private SQLite backup, never the live writer.

Run from the repository root. The source main/WAL files are read as bytes by
preview_store; SQLite opens only its private snapshot. SQLite's backup API then
copies that snapshot into the requested temporary destination directory.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data_hub.macro_releases import open_reader
from market_data_hub.macro_releases.catalogue import US_MONTHLY
from market_data_hub.macro_releases.run_daily import preview_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    args = parser.parse_args()
    args.destination_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="mdh-m9a-", dir=args.destination_root))
    target = directory / "macro_releases.sqlite"
    if not args.source.is_file() or target.resolve() == args.source.resolve():
        raise ValueError("An existing source and a separate destination are required")
    with preview_store(args.source) as source:
        with sqlite3.connect(str(target)) as destination:
            source.conn.backup(destination)
    print("SQLite backup:", target, flush=True)
    (directory / "no-consensus-archive").mkdir()
    command = [sys.executable, "-m", "market_data_hub.macro_releases.run_daily",
               "--families", *US_MONTHLY, "--db", str(target),
               "--consensus-raw-dir", str(directory / "no-consensus-archive")]
    report = {"copy": str(target), "backup_method": "SQLite backup API from stable private main/WAL snapshot"}
    for label, extra in (("dry_run", ["--dry-run"]), ("copy_collection", [])):
        result = subprocess.run(command + extra, capture_output=True, text=True, check=False)
        try:
            report[label] = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("Verification subprocess did not return JSON") from None
        print(label, json.dumps(report[label], indent=2), flush=True)
        if result.returncode != 0 or not all(r["vintages_added"] > 0 for r in report[label]["families"].values()):
            (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
            return 1
    with open_reader(target) as store:
        report["week_cards"] = store.release_cards("2026-10-19", "2026-10-23")["cards"]
        report["integrity"] = store.conn.execute("PRAGMA quick_check").fetchone()[0]
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Week cards:", json.dumps([{"indicator": c["indicator_id"], "period": c["resolution"]["reference_period"],
                                      "status": c["resolution"]["status"], "value": c["resolution"]["value"],
                                      "edition": c["resolution"]["edition"]}
                                     for c in report["week_cards"]], indent=2), flush=True)
    print("Report:", directory / "verification.json", flush=True)
    return 0 if any(c["resolution"]["status"] == "official" for c in report["week_cards"]) and report["integrity"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
