"""One explicit-path, lease-locked daily official release collection."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from uuid import uuid4

from .adapters import persist
from .bea import BEAAdapter
from .bls import BLSAdapter, shift_month
from .catalogue import FAMILIES, REMAINING_FAMILIES
from .consensus import DEFAULT_RAW_DIR, ingest
from .eurostat import EurostatAdapter
from .fred import USRedistributorAdapter
from .dol import DOLAdapter
from .discovery import FamilyFetch, fetch_daily
from .http import FetchError, HttpClient
from .lease import LeaseBusy, LeaseLost, RunLease
from .migrations import migrate
from .ons import ONSAdapter
from . import run_log
from .schedule import RUN_INSTANT, PENDING_EDITIONS, edition_status, family_editions, run_instant
from .tier1_publications import Tier1Adapter
from .store import Store, open_writer
from .verification import configured_bands, persist_review, verify

DEFAULT_LOOKBACK_MONTHS = 24
ADAPTERS = {"us_cpi": BLSAdapter, "us_payrolls": BLSAdapter, "us_gdp": BEAAdapter,
            "ea_hicp": EurostatAdapter, "ea_gdp": EurostatAdapter, "uk_gdp": ONSAdapter,
            "uk_cpi": ONSAdapter, "uk_labour_market": ONSAdapter,
            "us_jobless_claims": DOLAdapter,
            "us_ppi": BLSAdapter, "us_jolts": BLSAdapter, "us_pce": BEAAdapter,
            **{f: USRedistributorAdapter for f in ("us_retail_sales", "us_housing_starts", "us_durable_goods",
                                                   "us_industrial_production", "us_trade_balance")}}
DISCOVERY_ADAPTERS = dict(ADAPTERS)
ADAPTERS.update({f: Tier1Adapter for f in REMAINING_FAMILIES})
DISCOVERY_ADAPTERS.update({f: Tier1Adapter for f in REMAINING_FAMILIES})
from .catalogue import CENTRAL_BANKS
from .policy import PolicyAdapter
ADAPTERS.update({f: PolicyAdapter for f in CENTRAL_BANKS})
DISCOVERY_ADAPTERS.update({f: PolicyAdapter for f in CENTRAL_BANKS})


def fetch_windows(families, lookback, today):
    since = shift_month(today.strftime("%Y-%m"), -lookback)
    # Revision/benchmark widening belongs to the provider; persist routine
    # history within this window and discover its publication evidence.
    return since, {family: {"fetch_since": since,
                            "fetch_until": today.isoformat()} for family in families}


@contextmanager
def preview_store(target):
    """Open only a private snapshot; even mode=ro can create live WAL sidecars.

    Read the main file and WAL twice and require stable bytes (bounded retries).
    SQLite interprets committed WAL frames on the private copy. Never copy SHM:
    it is disposable shared locking state and belongs only to the live store.
    """
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if target.exists():
            def read_files():
                wal = Path(str(target) + "-wal")
                return target.read_bytes(), wal.read_bytes() if wal.exists() else None
            for attempt in range(3):
                try:
                    data, wal = read_files()
                    if (data, wal) == read_files():
                        break
                except FileNotFoundError:
                    pass  # checkpoint removed/reset WAL during the snapshot
            else:
                raise RuntimeError("Could not obtain stable preview snapshot")
            with tempfile.TemporaryDirectory(prefix="mdh-macro-preview-") as directory:
                snapshot = Path(directory) / "snapshot.sqlite"
                snapshot.write_bytes(data)
                if wal is not None:
                    Path(str(snapshot) + "-wal").write_bytes(wal)
                source = sqlite3.connect(str(snapshot))
                try:
                    if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("Preview snapshot failed integrity check")
                    source.backup(conn)
                finally:
                    source.close()
        migrate(conn)
        yield Store(conn, writable=True)
    finally:
        conn.close()


def safe_error(exc):
    # Adapter/schema exceptions can contain response bodies, URLs or credentials.
    # Persist only a controlled category, never arbitrary exception text.
    if isinstance(exc, FetchError):
        text = str(exc)
        if re.fullmatch(r"Missing credential: (BLS|BEA|FRED)_API_KEY", text):
            message = text
        elif re.fullmatch(r"(bls|bea|fred|eurostat|ons|dol|census|ecfin|umich): HTTP \d{3}", text):
            message = text
        elif "needs review" in text or "metadata" in text:
            message = "Provider publication metadata/version needs review before this edition can be certified"
        elif "quota" in text:
            message = "Provider daily quota exhausted; try the next daily run"
        elif "transport" in text:
            message = "Provider transport failed after bounded retries"
        else:
            message = "Provider fetch/parse failed; check availability and certified response schema"
    elif isinstance(exc, (LeaseBusy, LeaseLost)):
        message = "Daily collector lease is live" if isinstance(exc, LeaseBusy) else "Daily collector lease ownership lost"
    elif isinstance(exc, (ValueError, KeyError, TypeError)):
        message = "Observation validation or certified source schema failed"
    else:
        message = "Collection failed; inspect provider availability, lease and database access"
    return {"error_type": type(exc).__name__, "error_message": message}


def persist_counts(store, observations):
    before = {table: store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("value_vintages", "readings")}
    persist(store, observations)
    added = {table: store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - count
             for table, count in before.items()}
    return {"vintages_added": added["value_vintages"], "readings_added": added["readings"],
            "readings_skipped": len(observations) - added["readings"]}


def execute(
    target, families, lookback, dry_run, *, bands=None, raw_dir=DEFAULT_RAW_DIR
):
    run_at = datetime.now(timezone.utc)
    since, windows = fetch_windows(families, lookback, run_at.date())
    run_id = uuid4().hex
    summary = {"run_id": run_id, "db": str(target), "dry_run": dry_run,
               "requested_since": since, "families": {}}
    parameters = {
        "lookback_months": lookback,
        "run_at": run_at.isoformat(),
        "fetch_windows": windows,
        "sanity_bands": bands or configured_bands(),
        "consensus_raw_dir": str(raw_dir),
    }
    if dry_run:
        client = HttpClient()
        with preview_store(target) as store:
            for family in families:
                summary["families"].update(collect_family(
                    lambda: nullcontext(store), client, run_id, family, windows, dry_run=True, bands=bands, run_at=run_at))
            summary["consensus"] = collect_consensus(
                lambda: nullcontext(store), run_id, raw_dir, dry_run=True
            )
    else:
        with RunLease(target) as lease:
            client = HttpClient(before_request=lease.renew)
            parameters["lease_events"] = lease.events
            with open_writer(target) as store:
                lease.validate()
                run_log.recover(store)
                run_log.start(store, run_id, families, parameters)
            # Keep the writer lock/transaction out of all network and parsing work.
            for family in families:
                summary["families"].update(collect_family(
                    lambda: open_writer(target), client, run_id, family, windows, lease=lease, bands=bands, run_at=run_at))
            summary["consensus"] = collect_consensus(
                lambda: open_writer(target), run_id, raw_dir, lease=lease
            )
            with open_writer(target) as store:
                lease.validate()
                run_log.finish(store, run_id, outcome(summary["families"]))
    summary["status"] = outcome(summary["families"])
    summary["exit_code"] = 1 if summary["status"] == "failed" else 0 if any(
        r["vintages_added"] for r in summary["families"].values()) else 3
    return summary


def collect_consensus(writer, run_id, raw_dir, *, lease=None, dry_run=False):
    """Independent status under the official run's lease, after its families."""
    try:
        with writer() as store:
            if lease:
                lease.validate()
            result = ingest(
                store, raw_dir, before_snapshot=lease.validate if lease else None
            )
    except Exception as exc:
        result = dict(status="failed", readings_added=0, **safe_error(exc))
    if not dry_run:
        try:
            with writer() as store:
                if lease:
                    lease.validate()
                run_log.consensus_result(store, run_id, result)
        except Exception as exc:
            # A consensus/logging failure must not overwrite official outcomes.
            result = dict(result, status="failed", **safe_error(exc))
    return result


def collect_family(writer, client, run_id, family, windows, *, lease=None, dry_run=False, bands=None, run_at=None):
    """Fetch outside the writer lock, then atomically commit data and outcome."""
    result = dict(windows[family], vintages_added=0, readings_added=0, readings_skipped=0)
    try:
        if lease:
            lease.renew()
        instant = run_instant(run_at if run_at is not None else result["fetch_until"])
        token = RUN_INSTANT.set(instant)
        pending_token = PENDING_EDITIONS.set({})
        try:
            batch = (fetch_daily(client, family, result["fetch_since"]) if ADAPTERS[family] is DISCOVERY_ADAPTERS[family]
                     else FamilyFetch(observations=list(ADAPTERS[family](client).fetch([family], result["fetch_since"]))))
            calendar = family_editions(family)
            if calendar:
                result["scheduled_editions"] = [dict(edition=d, period=p, stage=s, time=t, status="scheduled",
                    **({"reason": "not_yet_published"} if (family, d) in PENDING_EDITIONS.get() else {}))
                    for d, (p, s, t) in calendar.items()
                    if edition_status(d, metadata=(p, s, t), family=family) == "scheduled"]
            # An endpoint exposing a reviewed edition early does not make it due.
            batch.observations = [o for o in batch.observations if o.edition not in calendar or
                edition_status(o.edition, metadata=calendar[o.edition], family=family) == "due"]
        finally:
            PENDING_EDITIONS.reset(pending_token)
            RUN_INSTANT.reset(token)
        observations = batch.observations
        if not observations and not batch.issues and not result.get("scheduled_editions"):
            raise FetchError("No observations in requested window")
        if any(o.family != family for o in observations):
            raise ValueError("Adapter returned another family")
        with writer() as store:
            if lease:
                lease.validate()
            batch = verify(store, batch, bands or configured_bands())
            observations = batch.observations
            with store.batch():
                persist_review(store, batch, family, None if dry_run else run_id)
                result.update(persist_counts(store, observations))
                result["status"] = "needs_review" if batch.issues else "ok" if result["vintages_added"] else "no_change"
                if batch.issues:
                    result["review_reasons"] = batch.issues
                if not dry_run:
                    run_log.family_result(store, run_id, family, result)
    except Exception as exc:
        result.update(status="failed", vintages_added=0, readings_added=0, readings_skipped=0,
                      **safe_error(exc))
        if not dry_run:
            with writer() as store:
                if lease:
                    lease.validate()
                run_log.family_result(store, run_id, family, result)
    return {family: result}


def outcome(results):
    statuses = {r["status"] for r in results.values()}
    return "failed" if "failed" in statuses else "ok" if "ok" in statuses else "needs_review" if "needs_review" in statuses else "no_change"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Explicit database path; no environment fallback")
    parser.add_argument("--families", nargs="+", default=list(FAMILIES), help="Names separated by spaces or commas")
    parser.add_argument("--lookback-months", type=int, default=DEFAULT_LOOKBACK_MONTHS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--consensus-raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
        help="Read-only FF archive; consensus status is independent of official status",
    )
    parser.add_argument("--sanity-bands", type=Path, help="JSON per-indicator max absolute changes")
    args = parser.parse_args(argv)
    families = list(dict.fromkeys(f for group in args.families for f in group.split(",")))
    if not families or any(f not in FAMILIES for f in families):
        parser.error("--families must contain names from: " + ",".join(FAMILIES))
    if not 1 <= args.lookback_months <= 180:
        parser.error("--lookback-months must be between 1 and 180")
    try:
        bands = configured_bands(json.loads(args.sanity_bands.read_text()) if args.sanity_bands else None)
    except (ValueError, OSError):
        parser.error("--sanity-bands must be a JSON object of positive per-indicator changes")
    try:
        summary = execute(
            args.db.resolve(),
            families,
            args.lookback_months,
            args.dry_run,
            bands=bands,
            raw_dir=args.consensus_raw_dir,
        )
    except Exception as exc:
        summary = {
            "status": "failed",
            "exit_code": 1,
            "dry_run": args.dry_run,
            **safe_error(exc),
        }
    print(json.dumps(summary, indent=2))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
