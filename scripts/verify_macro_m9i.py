"""Opt-in Latin American verification on a private SQLite backup under D:\\Temp.

Production SQLite is never opened. Stable source bytes are interpreted only
on a private snapshot before the SQLite backup, collection and read-API checks.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data_hub.macro_releases import open_reader, run_daily
from market_data_hub.macro_releases.latam_editions import EDITIONS as LATAM_EDITIONS
from market_data_hub.macro_releases.policy_editions import EDITIONS as POLICY_EDITIONS
from market_data_hub.macro_releases.catalogue import LATAM_FAMILIES, CENTRAL_BANKS
from market_data_hub.macro_releases.run_daily import preview_store
from market_data_hub.macro_releases.schedule import due
from scripts.verify_macro_m9h import fingerprints, vintage_fingerprint

MEASURES = {**LATAM_FAMILIES, **{f: CENTRAL_BANKS[f] for f in ("boe_policy", "pboc_lpr")}}
EDITIONS = {**LATAM_EDITIONS, **{f: POLICY_EDITIONS[f] for f in ("boe_policy", "pboc_lpr")}}
FAMILIES = tuple(MEASURES)


def verify_copy(args, directory):
    target = directory / "macro_releases.sqlite"
    before = fingerprints(args.source)
    with preview_store(args.source, expected_schema=9) as source:
        with sqlite3.connect(str(target)) as destination:
            source.conn.backup(destination)
    print("SQLite backup:", target, flush=True)
    with open_reader(target) as store:
        maximum = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM value_vintages").fetchone()[0]
        original = vintage_fingerprint(store.conn, maximum)
    raw = directory / "no-consensus-archive"
    raw.mkdir()
    report = dict(copy=str(target), backup_method="SQLite backup from stable private main/WAL bytes",
                  families=args.families, source_schema=9, source_files_unchanged=False)
    passed = True
    for label, dry in (("dry_run", True), ("copy_collection", False), ("idempotence", False)):
        result = run_daily.execute(target, args.families, 24, dry, raw_dir=raw)
        report[label] = result
        print(label, json.dumps(result["families"]), flush=True)
        allowed = {"no_change"} if label == "idempotence" else {"ok", "no_change"}
        passed &= all(r["status"] in allowed for r in result["families"].values())
    report["latest_cards"] = []
    ids = sorted({m.id for f in args.families for m in MEASURES[f]})
    with open_reader(target) as store:
        # Include both saved historical editions and the reviewed future horizon.
        all_cards = store.release_cards("2026-08-01", "2026-12-31", indicators=ids)["cards"]
        for family in args.families:
            edition = max(due(EDITIONS[family], family=family))
            period, stage, _ = EDITIONS[family][edition]
            cards = [c for c in all_cards if c.get("family") == family and c.get("edition") == edition
                     and c.get("reference_period") == period and c.get("resolution")]
            # Some read API shapes expose edition through resolution only.
            if not cards:
                cards = [c for c in all_cards if c.get("family") == family and c.get("resolution", {})
                         and c["resolution"].get("edition") == edition and c.get("reference_period") == period]
            passed &= {c["indicator_id"] for c in cards} == {m.id for m in MEASURES[family]}
            for card in cards:
                result = card["resolution"]
                passed &= (card["source_class"] == result["source_class"] == result["status"] == "official"
                           and card["stage"] == stage and result["edition"] == edition)
                report["latest_cards"].append(dict(family=family, indicator=card["indicator_id"],
                    edition=edition, stage=stage, period=period, value=result["value"],
                    unit=result["unit"], scale=result["scale"], issuer=result.get("issuer_id"),
                    source_class=result["source_class"], released_at=card["released_at"],
                    policy_decision=card.get("policy_decision")))
        report["window_cards"] = [dict(family=c["family"], indicator=c["indicator_id"],
            edition=c["resolution"].get("edition"), stage=c["stage"], period=c["reference_period"],
            value=c["resolution"]["value"], source_class=c["source_class"], released_at=c["released_at"])
            for c in store.release_cards("2026-10-01", "2026-10-27", indicators=ids)["cards"]
            if c.get("resolution")]
        if "mx_cpi" in args.families:
            passed &= {c["indicator"] for c in report["window_cards"] if c["family"] == "mx_cpi"} == {"mx_cpi_yy", "mx_core_cpi_yy"}
        report["excluded_families"] = {
            "br_ipca": "IBGE publication-calendar page returned HTTP 403; stopped under the brief's access rule",
            "br_ipca15": "IBGE publication-calendar page returned HTTP 403; stopped under the brief's access rule",
        }
        report["approved_card_gaps"] = ["mx_core_cpi_yy"]
        report["integrity"] = store.conn.execute("PRAGMA quick_check").fetchone()[0]
        report["existing_vintages_unchanged"] = original == vintage_fingerprint(store.conn, maximum)
    report["source_files_unchanged"] = before == fingerprints(args.source)
    passed &= report["integrity"] == "ok" and report["source_files_unchanged"] and report["existing_vintages_unchanged"]
    report["status"] = "ok" if passed else "failed"
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Window cards:", json.dumps(report["window_cards"], indent=2), flush=True)
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
        raise ValueError("M9i verification destination must be under D:\\Temp")
    if not args.source.is_file() or root == args.source.resolve().parent:
        raise ValueError("An existing source and separate backup directory are required")
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="mdh-m9i-", dir=root))
    previous = tempfile.tempdir
    try:
        tempfile.tempdir = str(directory)
        return verify_copy(args, directory)
    finally:
        tempfile.tempdir = previous


if __name__ == "__main__":
    raise SystemExit(main())
