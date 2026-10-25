"""Opt-in BLS/ALFRED verification on a stable production byte-copy under D:\\Temp.

SQLite never opens the source. Main/WAL snapshots, backup, collection writes,
provider rate ledgers and the JSON report all stay under the destination root.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data_hub.macro_releases import open_reader, resolve, run_daily
from market_data_hub.macro_releases.read_api import FAMILY_INDICATORS
from market_data_hub.macro_releases.run_daily import preview_store
from market_data_hub.macro_releases.schedule import due, family_editions

FAMILIES = ("us_cpi", "us_payrolls", "us_ppi", "us_jolts", "us_unemployment", "us_ahe")


def fingerprints(source):
    return {suffix: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            for suffix, path in (("main", source), ("wal", Path(str(source) + "-wal")),
                                 ("shm", Path(str(source) + "-shm")))}


def vintage_fingerprint(conn, maximum):
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute("SELECT * FROM value_vintages WHERE id<=? ORDER BY id", (maximum,)):
        digest.update(json.dumps(tuple(row), ensure_ascii=True).encode())
        digest.update(b"\n")
        count += 1
    return dict(count=count, sha256=digest.hexdigest())


def verify_copy(args, directory):
    target = directory / "macro_releases.sqlite"
    before = fingerprints(args.source)
    with preview_store(args.source, expected_schema=9) as source:
        with sqlite3.connect(str(target)) as destination:
            source.conn.backup(destination)
    print("SQLite backup:", target, flush=True)
    with open_reader(target) as store:
        maximum = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM value_vintages").fetchone()[0]
        original_vintages = vintage_fingerprint(store.conn, maximum)
    raw = directory / "no-consensus-archive"
    raw.mkdir()
    report = dict(copy=str(target), backup_method="SQLite backup from stable private main/WAL bytes",
                  source_schema=9, temporary_root=str(directory), families=args.families)
    passed = True
    for label, dry_run in (("dry_run", True), ("copy_collection", False), ("idempotence", False)):
        result = run_daily.execute(target, args.families, 24, dry_run, raw_dir=raw)
        report[label] = result
        print(label, json.dumps(result["families"]), flush=True)
        allowed = {"no_change"} if label == "idempotence" else {"ok", "no_change"}
        passed &= all(r["status"] in allowed for r in result["families"].values())
    report["latest_cards"] = []
    with open_reader(target) as store:
        for family in args.families:
            edition = max(due(family_editions(family), family=family))
            headline, _, _ = family_editions(family)[edition]
            indicators = FAMILY_INDICATORS[family]
            cards = store.release_cards(edition, edition, indicators=sorted(indicators))["cards"]
            actuals = [c for c in cards if c.get("reference_period") == headline and c.get("resolution")]
            passed &= {c["indicator_id"] for c in actuals} == indicators
            for card in actuals:
                result = card["resolution"]
                kind = result.get("source_class")
                source_id = result.get("source", {}).get("source_id")
                direct = resolve(store, card["indicator_id"], headline, card["stage"])
                passed &= (kind in {"official", "redistributor"} and card.get("source_class") == kind and
                           result.get("issuer_id") == "BLS" and result.get("edition") == edition and
                           direct is not None and direct.source_class == kind)
                if kind == "redistributor":
                    passed &= result["status"] == "provisional" and source_id == "ALFRED"
                    passed &= all(p.get("source_class") == "redistributor"
                                  for p in result.get("provenance", []) if p.get("value") is not None)
                else:
                    passed &= result["status"] == "official" and source_id == "BLS"
                report["latest_cards"].append(dict(family=family, indicator=card["indicator_id"],
                    issuer=result.get("issuer_id"), source=source_id, source_class=kind,
                    period=headline, edition=edition, value=result["value"], unit=result["unit"], scale=result["scale"],
                    status=result["status"], released_at=card["released_at"], provenance=result.get("provenance")))
        report["integrity"] = store.conn.execute("PRAGMA quick_check").fetchone()[0]
        report["existing_vintages_unchanged"] = original_vintages == vintage_fingerprint(store.conn, maximum)
    report["source_files_unchanged"] = before == fingerprints(args.source)
    passed &= (report["integrity"] == "ok" and report["source_files_unchanged"] and
               report["existing_vintages_unchanged"])
    report["status"] = "ok" if passed else "failed"
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Latest cards:", json.dumps([{k: v for k, v in c.items() if k != "provenance"}
                                       for c in report["latest_cards"]], indent=2), flush=True)
    print("Report:", directory / "verification.json", flush=True)
    print("Verification:", report["status"], flush=True)
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination-root", required=True, type=Path)
    parser.add_argument("--families", nargs="+", choices=FAMILIES, default=list(FAMILIES))
    args = parser.parse_args()
    root = args.destination_root.resolve()
    allowed = Path("D:/Temp").resolve()
    if root != allowed and allowed not in root.parents:
        raise ValueError("BLS verification destination must be under D:\\Temp")
    if not args.source.is_file() or root == args.source.resolve().parent:
        raise ValueError("An existing source and separate backup directory are required")
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="mdh-m9h-", dir=root))
    previous = tempfile.tempdir
    try:
        tempfile.tempdir = str(directory)
        return verify_copy(args, directory)
    finally:
        tempfile.tempdir = previous


if __name__ == "__main__":
    raise SystemExit(main())
