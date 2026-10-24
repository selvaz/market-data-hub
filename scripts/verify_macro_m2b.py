"""Opt-in offline consensus verification on a fresh SQLite backup under D:\\Temp.

Production and archive paths are read only as bytes. preview_store reconstructs
the main/WAL snapshot privately; SQLite never opens production. The baseline
module and reviewed aliases are read from a pinned Git revision, then both rules
run against the same initial database and archive. No issuer HTTP is performed.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_data_hub.econ_calendar.collect.forexfactory import load_snapshots
from market_data_hub.macro_releases import consensus, open_writer
from market_data_hub.macro_releases.catalogue import FAMILIES
from market_data_hub.macro_releases.run_daily import preview_store

ROOT = Path(__file__).resolve().parents[1]
NEXT_WEEK = {"us_gdp": "2026-10-29", "us_pce": "2026-10-29",
             "us_durable_goods": "2026-10-27", "fomc": "2026-10-28"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_fingerprint(raw):
    return {p.name: digest(p) for p in sorted(raw.iterdir())
            if p.suffix == ".json" or p.name == "manifest.jsonl"}


def baseline_module(ref, directory):
    folder = directory / "baseline"
    folder.mkdir()
    for name in ("consensus.py", "ff_aliases_v1.json"):
        result = subprocess.run(["git", "show", f"{ref}:market_data_hub/macro_releases/{name}"],
                                cwd=ROOT, capture_output=True, check=True)
        (folder / name).write_bytes(result.stdout)
    spec = importlib.util.spec_from_file_location("market_data_hub.macro_releases._m2b_baseline",
                                                 folder / "consensus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--consensus-raw-dir", type=Path, required=True)
    parser.add_argument("--baseline-ref", default="353240a")
    args = parser.parse_args()
    source, root, raw = args.source.resolve(), args.destination_root.resolve(), args.consensus_raw_dir.resolve()
    if not source.is_file() or not raw.is_dir() or root == source.parent or root == raw:
        raise ValueError("Existing source/archive and separate backup destination required")
    if any(p.lower() == "programdata" for p in root.parts):
        raise ValueError("Backup destination must be outside production")
    source_before, raw_before = digest(source), archive_fingerprint(raw)
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="mdh-m2b-", dir=root))
    target = directory / "macro_releases.sqlite"
    with preview_store(source) as snapshot:
        with sqlite3.connect(target) as destination:
            snapshot.conn.backup(destination)
    print("SQLite backup:", target, flush=True)
    baseline = baseline_module(args.baseline_ref, directory)
    with preview_store(target) as snapshot:
        before = baseline.ingest(snapshot, raw, reprocess=True)
    report = dict(copy=str(target), source=str(source), baseline_ref=args.baseline_ref,
        backup_method="SQLite backup API from stable private main/WAL snapshot",
        before=before, archive_files=raw_before)
    (directory / "before.json").write_text(json.dumps(before, indent=2) + "\n", encoding="utf-8")
    print("Before:", json.dumps({k: before[k] for k in ("readings_added", "events_total", "skip_reasons")}), flush=True)
    with open_writer(target) as store:
        last_id = store.conn.execute("SELECT COALESCE(MAX(id),0) FROM consensus_readings").fetchone()[0]
        vintages_before = store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
        report["after"] = consensus.ingest(store, raw, reprocess=True)
        counts = dict(store.conn.execute(
            "SELECT e.family,COUNT(*) FROM consensus_readings r JOIN release_events e "
            "ON e.id=r.release_event_id WHERE r.id>? GROUP BY e.family", (last_id,)))
        report["readings_added_by_family"] = {f: counts.get(f, 0) for f in FAMILIES}
        report["pre_release_readings_by_family"] = dict(store.conn.execute(
            "SELECT e.family,SUM(r.pre_release_eligible) FROM consensus_readings r JOIN release_events e "
            "ON e.id=r.release_event_id WHERE r.id>? GROUP BY e.family", (last_id,)))
        report["capture_days"] = [dict(r) for r in store.conn.execute(
            "SELECT date(captured_at) AS day,COUNT(*) AS readings,SUM(pre_release_eligible) AS pre_release "
            "FROM consensus_readings WHERE id>? GROUP BY 1", (last_id,))]
        report["next_week"] = {}
        catalogue = consensus.Catalogue(store)
        for family, edition in NEXT_WEEK.items():
            matches, eligible, reasons = [], 0, Counter()
            for snap in load_snapshots(raw):
                if snap.captured_at_source != "manifest" or snap[0].date().isoformat() != "2026-10-23":
                    continue
                for event in json.loads(snap[1].read_text(encoding="utf-8")):
                    mapping, _ = catalogue.match(event, consensus.mapping_title(event))
                    if not mapping or mapping.get("family") != family:
                        continue
                    linked, reason = catalogue.releases.match(mapping["indicator_id"], event)
                    if linked and linked["event"]["edition"] == edition:
                        matches.append(dict(title=event["title"], event_id=linked["event"]["id"]))
                        _, rejection, record = consensus.prepare(store, catalogue, event,
                            consensus.utc(snap[0].isoformat()), digest(snap[1]))
                        if record and record[1].captured_at < record[1].release_cutoff_at:
                            eligible += 1
                        elif rejection:
                            reasons[rejection] += 1
                    elif reason:
                        reasons[reason] += 1
            scheduled = [dict(r) for r in store.conn.execute(
                "SELECT e.id,s.expected_at,s.precision FROM release_events e JOIN schedule_versions s "
                "ON s.release_event_id=e.id WHERE e.family=? AND e.edition=?", (family, edition))]
            report["next_week"][family] = dict(edition=edition, matching_archive_forecasts=matches,
                eligible_forecasts=eligible, scheduled_events=scheduled, rejections=dict(reasons),
                verification="real_archive" if matches else "no_matching_forecast_in_2026_10_23_archive")
        report["idempotence"] = consensus.ingest(store, raw)
        report["integrity"] = store.conn.execute("PRAGMA quick_check").fetchone()[0]
        report["foreign_keys"] = [tuple(r) for r in store.conn.execute("PRAGMA foreign_key_check")]
        report["official_vintages_unchanged"] = vintages_before == store.conn.execute("SELECT COUNT(*) FROM value_vintages").fetchone()[0]
        cards = store.release_cards("2026-10-01", "2026-12-31")["cards"]
        ff = [r for c in cards for r in c["consensus"] if r["provider"] == "ff_forecast"]
        report["ff_card_readings"] = len(ff)
        report["ff_surprises_suppressed"] = all(r["surprise"] is None and not r["surprise_eligible"] for r in ff)
    report["source_main_unchanged"] = source_before == digest(source)
    report["raw_archive_unchanged"] = raw_before == archive_fingerprint(raw)
    check_env = dict(os.environ, MACRO_RELEASES_DB=str(target), PYTHONDONTWRITEBYTECODE="1")
    contract = subprocess.run([sys.executable, "-B",
        r"C:\ProgramData\lazytools\contract_checks\macro_checks.py", "consensus_five_days"],
        env=check_env, capture_output=True, text=True, check=False)
    report["external_contract_check"] = dict(exit_code=contract.returncode, output=contract.stdout.strip())
    report["status"] = "ok" if (report["after"]["status"] == "ok" and report["integrity"] == "ok"
        and not report["foreign_keys"] and report["source_main_unchanged"] and report["raw_archive_unchanged"]
        and report["official_vintages_unchanged"] and report["ff_surprises_suppressed"]
        and report["idempotence"]["readings_added"] == report["idempotence"]["links_changed"] == 0) else "failed"
    path = directory / "verification.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("After:", json.dumps({k: report["after"][k] for k in ("readings_added", "events_total", "skip_reasons")}), flush=True)
    print("Readings by family:", json.dumps(counts), flush=True)
    print("Next week:", json.dumps(report["next_week"]), flush=True)
    print("Report:", path, flush=True)
    print("Status:", report["status"], flush=True)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
