"""Opt-in M9b live verification on a SQLite backup, never the production writer.

The source files are read as bytes by preview_store. SQLite opens only that
private snapshot; its backup API creates a separate database under --destination-root.
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
from market_data_hub.macro_releases.run_daily import preview_store

from market_data_hub.macro_releases.catalogue import REMAINING_FAMILIES

FAMILIES = list(REMAINING_FAMILIES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    args = parser.parse_args()
    if any(part.lower() == "programdata" for part in args.destination_root.resolve().parts):
        raise ValueError("Verification refuses production destination directories")
    if args.destination_root.resolve() == args.source.resolve().parent:
        raise ValueError("Backup directory must be separate from the source directory")
    if not args.source.is_file():
        raise ValueError("An existing source file is required")
    args.destination_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="mdh-m9b-", dir=args.destination_root))
    target = directory / "macro_releases.sqlite"
    if target.resolve() == args.source.resolve():
        raise ValueError("A separate destination is required")
    before = args.source.stat()
    with preview_store(args.source) as source:
        with sqlite3.connect(str(target)) as destination:
            source.conn.backup(destination)
    print("SQLite backup:", target, flush=True)
    raw = directory / "no-consensus-archive"
    raw.mkdir()
    command = [sys.executable, "-m", "market_data_hub.macro_releases.run_daily",
               "--families", *FAMILIES, "--db", str(target), "--consensus-raw-dir", str(raw)]
    report = {"copy": str(target), "backup_method": "SQLite backup API from stable private main/WAL snapshot"}
    passed = True
    for label, extra in (("dry_run", ["--dry-run"]), ("copy_collection", [])):
        result = subprocess.run(command + extra, capture_output=True, text=True, check=False)
        try:
            report[label] = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("Verification subprocess did not return JSON") from None
        print(label, json.dumps({f: {k: r.get(k) for k in ("status", "vintages_added", "review_reasons", "error_message")}
                                 for f, r in report[label]["families"].items()}, indent=2), flush=True)
        passed &= result.returncode == 0 and all(r["status"] == "ok" and r["vintages_added"] > 0
                                                  for r in report[label]["families"].values())
    with open_reader(target) as store:
        new_ids = {m.id for measures in REMAINING_FAMILIES.values() for m in measures}
        report["week_cards"] = store.release_cards("2026-10-19", "2026-10-23", indicators=sorted(new_ids))["cards"]
        report["integrity"] = store.conn.execute("PRAGMA quick_check").fetchone()[0]
    after = args.source.stat()
    report["source_main_unchanged"] = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    cards = [dict(indicator=c["indicator_id"], period=c["resolution"]["reference_period"],
                  status=c["resolution"]["status"], value=c["resolution"]["value"],
                  edition=c["resolution"]["edition"]) for c in report["week_cards"]]
    required = {"uk_retail_sales_mm", "uk_average_weekly_earnings_yy", "ea_consumer_confidence", "us_umich_consumer_sentiment"}
    report["required_official_cards_found"] = required <= {c["indicator"] for c in cards if c["status"] == "official"}
    passed = passed and report["integrity"] == "ok" and report["required_official_cards_found"] and report["source_main_unchanged"]
    report["status"] = "ok" if passed else "failed"
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Week cards:", json.dumps(cards, indent=2), flush=True)
    print("Report:", directory / "verification.json", flush=True)
    print("Verification:", report["status"], flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
