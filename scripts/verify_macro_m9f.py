"""Opt-in live housing verification on a SQLite backup under D:\\Temp.

The production source is read as stable bytes, including its WAL. SQLite opens
only that private snapshot; all collection writes target a separate backup.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data_hub.macro_releases import open_reader, run_daily
from market_data_hub.macro_releases.catalogue import HOUSING_FAMILIES
from market_data_hub.macro_releases.housing_editions import SOURCES
from market_data_hub.macro_releases.run_daily import preview_store
from market_data_hub.macro_releases.schedule import due, family_editions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    parser.add_argument("--families", nargs="+", choices=sorted(HOUSING_FAMILIES),
                        default=list(HOUSING_FAMILIES))
    args = parser.parse_args()
    root = args.destination_root.resolve()
    allowed = Path("D:/Temp").resolve()
    if root != allowed and allowed not in root.parents:
        raise ValueError("Housing verification destination must be under D:\\Temp")
    if not args.source.is_file() or root == args.source.resolve().parent:
        raise ValueError("An existing source and separate backup directory are required")
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="mdh-m9f-", dir=root))
    previous_tempdir = tempfile.tempdir
    try:
        # Private SQLite main/WAL snapshots and rate ledgers also stay under
        # the approved verification root, not the host's shared temp directory.
        tempfile.tempdir = str(directory)
        return verify_copy(args, directory)
    finally:
        tempfile.tempdir = previous_tempdir


def verify_copy(args, directory):
    target = directory / "macro_releases.sqlite"
    before = args.source.stat()
    with preview_store(args.source, expected_schema=9) as source:
        with sqlite3.connect(str(target)) as destination:
            source.conn.backup(destination)
    print("SQLite backup:", target, flush=True)
    raw = directory / "no-consensus-archive"
    raw.mkdir()
    report = dict(copy=str(target), backup_method="SQLite backup API from stable private main/WAL snapshot",
                  source_schema=9, temporary_root=str(directory), families=args.families)
    passed = True
    for label, dry_run in (("dry_run", True), ("copy_collection", False), ("idempotence", False)):
        result = run_daily.execute(target, args.families, 24, dry_run, raw_dir=raw)
        report[label] = result
        statuses = {f: r["status"] for f, r in result["families"].items()}
        print(label, json.dumps(statuses), flush=True)
        passed &= all(s in ({"no_change"} if label == "idempotence" else {"ok"}) for s in statuses.values())
    report["latest_cards"] = []
    with open_reader(target) as store:
        for family in args.families:
            edition = max(due(family_editions(family), family=family))
            ids = [m.id for m in HOUSING_FAMILIES[family]]
            cards = store.release_cards(edition, edition, indicators=ids)["cards"]
            passed &= {c["indicator_id"] for c in cards if c["resolution"]["status"] == "official"} == set(ids)
            for card in cards:
                r = card["resolution"]
                passed &= card.get("source_class") == SOURCES[family][1]
                report["latest_cards"].append(dict(family=family, indicator=card["indicator_id"],
                    issuer=card["issuer_id"], source_class=card["source_class"],
                    period=card["reference_period"], edition=r["edition"], value=r["value"],
                    status=r["status"], released_at=card["released_at"], provenance=r.get("provenance")))
        report["integrity"] = store.conn.execute("PRAGMA quick_check").fetchone()[0]
    after = args.source.stat()
    report["source_main_unchanged"] = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    passed &= report["integrity"] == "ok" and report["source_main_unchanged"]
    report["status"] = "ok" if passed else "failed"
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Latest cards:", json.dumps([{k: v for k, v in c.items() if k != "provenance"}
                                       for c in report["latest_cards"]], indent=2), flush=True)
    print("Report:", directory / "verification.json", flush=True)
    print("Verification:", report["status"], flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
