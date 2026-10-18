"""Resumable ALFRED real-time history. Network calls never hold a DB connection."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import time

import duckdb
import pandas as pd
import requests
import yaml

from market_data_hub.db.connection import get_conn, resolve_db_path
from market_data_hub.db.upsert import upsert
from market_data_hub.lock import db_write_lock
from market_data_hub.sources.alfred import (
    _OBSERVATIONS_URL, _VINTAGE_DATES_URL, _require_api_key,
)

CONFIG = Path(__file__).parent / "config" / "alfred_backfill.yaml"
EARLIEST = "1776-07-04"
LATEST = "9999-12-31"
VINTAGE_BATCH = 10000
PAGE_SIZE = 100000
WRITE_BATCH = 5000


class RequestLimit(RuntimeError):
    """The explicit HTTP-attempt budget has been consumed."""


class RateLimiter:
    """One shared, evenly spaced 90/minute limiter, including retry attempts."""

    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.next_request = 0.0

    def wait(self):
        now = self.clock()
        if now < self.next_request:
            self.sleep(self.next_request - now)
        self.next_request = self.clock() + 60 / 90


class Client:
    """Requests transport with bounded retries and secret-free exceptions."""

    def __init__(self, *, api_key=None, max_requests=None, limiter=None,
                 session=None, sleep=time.sleep):
        self.key = _require_api_key(api_key)
        self.max_requests = max_requests
        self.requests = 0
        self.limiter = limiter or RateLimiter()
        self.session = session or requests.Session()
        self.sleep = sleep

    def get(self, url, **params):
        for attempt in range(5):
            if self.max_requests is not None and self.requests >= self.max_requests:
                raise RequestLimit("HTTP request budget exhausted; rerun to resume")
            self.limiter.wait()
            self.requests += 1
            try:
                response = self.session.get(
                    url, params={**params, "api_key": self.key, "file_type": "json"},
                    timeout=60, headers={"User-Agent": "market-data-hub/alfred-backfill"})
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 4:
                    raise RuntimeError("ALFRED transport failed after 5 attempts") from None
                self.sleep(2 ** attempt)
                continue
            status = response.status_code
            if status == 429 or 500 <= status < 600:
                if attempt == 4:
                    raise RuntimeError(f"ALFRED HTTP {status} after 5 attempts")
                try:
                    delay = float(response.headers.get("Retry-After", 0))
                except ValueError:
                    delay = 0
                self.sleep(max(2 ** attempt, min(delay, 300)))
                continue
            if status != 200:
                # requests.raise_for_status includes the URL (and API key).
                raise RuntimeError(f"ALFRED HTTP {status}; request not retried")
            try:
                return response.json()
            except ValueError:
                raise RuntimeError("ALFRED returned invalid JSON") from None
        raise RuntimeError("ALFRED exhausted retries")  # pragma: no cover

    def vintages(self, series_id, since, until):
        dates, offset = [], 0
        while True:
            payload = self.get(
                _VINTAGE_DATES_URL, series_id=series_id, realtime_start=since,
                realtime_end=until, sort_order="asc", limit=10000, offset=offset)
            page = payload["vintage_dates"]
            dates.extend(page)
            offset += len(page)
            if offset >= int(payload["count"]):
                return sorted(set(dates))
            if not page:
                raise RuntimeError("ALFRED vintage pagination stopped before count")

    def observations(self, series_id, start, end, offset=0):
        while True:
            payload = self.get(
                _OBSERVATIONS_URL, series_id=series_id, output_type=1,
                realtime_start=start, realtime_end=end, observation_start=EARLIEST,
                observation_end=LATEST, sort_order="asc", limit=PAGE_SIZE, offset=offset)
            page = payload["observations"]
            offset += len(page)
            final = offset >= int(payload["count"])
            if not page and not final:
                raise RuntimeError("ALFRED observation pagination stopped before count")
            yield page, final
            if final:
                return


def configured_series():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["series"]


def _checkpoint(db_path, series_id, since):
    # Avoid get_conn(read_only=True)'s create-if-absent behavior, especially dry-run.
    if not Path(db_path).exists():
        return None
    with closing(duckdb.connect(db_path, read_only=True)) as con:
        exists = con.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'alfred_backfill_progress'").fetchone()
        if not exists:
            return None
        row = con.execute(
            "SELECT last_vintage FROM alfred_backfill_progress "
            "WHERE series_id = ? AND since_vintage = ?", [series_id, since]).fetchone()
        return row[0].isoformat() if row else None


def _pending(db_path, series_id, since):
    if not Path(db_path).exists():
        return None
    with closing(duckdb.connect(db_path, read_only=True)) as con:
        if not con.execute("SELECT 1 FROM information_schema.tables "
                           "WHERE table_name = 'alfred_backfill_pending'").fetchone():
            return None
        row = con.execute(
            "SELECT range_start::VARCHAR, range_end::VARCHAR, last_vintage::VARCHAR, "
            "vintage_count, next_offset, open_end FROM alfred_backfill_pending "
            "WHERE series_id = ? AND since_vintage = ?", [series_id, since]).fetchone()
        return dict(zip(("start", "end", "last", "count", "offset", "open_end"), row)) if row else None


def _frame(series_id, observations):
    # Keep DATE values as Python dates: pandas nanoseconds cannot represent 9999.
    records = []
    for obs in observations:
        records.append({
            "series_id": series_id, "date": date.fromisoformat(obs["date"]),
            "as_of": date.fromisoformat(obs["realtime_start"]),
            "realtime_end": date.fromisoformat(obs["realtime_end"]),
            "value": None if obs["value"] == "." else float(obs["value"]),
            "source": "alfred",
        })
    return pd.DataFrame(records)


def _plans(client, sid, since, checkpoint, until):
    start = max(since, (date.fromisoformat(checkpoint) + timedelta(days=1)
                        ).isoformat()) if checkpoint else since
    vintages = client.vintages(sid, start, until) if start <= until else []
    plans = []
    for offset in range(0, len(vintages), VINTAGE_BATCH):
        batch = vintages[offset:offset + VINTAGE_BATCH]
        next_index = offset + len(batch)
        final_range = next_index == len(vintages)
        end = (until if final_range else
               (date.fromisoformat(vintages[next_index]) - timedelta(days=1)).isoformat())
        plans.append({"start": checkpoint if offset == 0 and checkpoint else batch[0],
                      "end": end, "last": batch[-1], "count": len(batch),
                      "offset": 0, "open_end": final_range})
    return plans


def _write(db_path, series_id, since, observations, completed=None, pending=None):
    added = 0
    # Connections close BEFORE releasing the advisory lock.
    with db_write_lock(db_path), closing(get_conn(db_path)) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            for offset in range(0, len(observations), WRITE_BATCH):
                n, _ = upsert(con, "alfred_vintage_observations", _frame(
                    series_id, observations[offset:offset + WRITE_BATCH]), outer_txn=True)
                added += n
            if completed:
                con.execute(
                    "INSERT INTO alfred_backfill_progress "
                    "(series_id, since_vintage, last_vintage) VALUES (?, ?, ?) "
                    "ON CONFLICT (series_id, since_vintage) DO UPDATE SET "
                    "last_vintage = greatest(alfred_backfill_progress.last_vintage, "
                    "excluded.last_vintage), updated_at = now()",
                    [series_id, since, completed])
                con.execute("DELETE FROM alfred_backfill_pending "
                            "WHERE series_id = ? AND since_vintage = ?", [series_id, since])
            elif pending:
                con.execute(
                    "INSERT OR REPLACE INTO alfred_backfill_pending VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [series_id, since, pending["start"], pending["end"], pending["last"],
                     pending["count"], pending["offset"], pending["open_end"]])
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return added


def run(db_path, series, *, since=EARLIEST, dry_run=False, client=None, emit=print):
    """Return 0 for new rows, 3 for no new rows, 1 for incomplete/failed work."""
    db_path = resolve_db_path(str(db_path))
    client = client or Client()
    failed, total_added, total_vintages = False, 0, 0
    until = date.today().isoformat()
    for sid in series:
        summary = {"series_id": sid, "vintages_added": 0, "rows_added": 0,
                   "status": "ok"}
        before = client.requests
        try:
            checkpoint = _checkpoint(db_path, sid, since)
            pending = _pending(db_path, sid, since)
            plans = []
            if pending:
                # Finish the saved query before discovering later releases.
                # Its cutoff and row ordering stay fixed.
                plans = [pending]
            else:
                plans = _plans(client, sid, since, checkpoint, until)
            summary["vintages_pending"] = sum(p["count"] for p in plans)
            summary["minimum_observation_requests"] = len(plans)
            if dry_run:
                summary["status"] = "dry_run"
            else:
                for plan in plans:
                    # Re-fetch the last completed vintage as a baseline: its
                    # formerly open intervals may now have ended (withdrawals
                    # need this even if no replacement value was published).
                    for page, final in client.observations(
                            sid, plan["start"], plan["end"], offset=plan["offset"]):
                        for row_offset in range(0, max(1, len(page)), WRITE_BATCH):
                            rows = page[row_offset:row_offset + WRITE_BATCH]
                            if plan["open_end"]:
                                # The query cutoff clips currently valid periods;
                                # keep them open until a later incremental pull.
                                rows = [{**r, "realtime_end": LATEST}
                                        if r["realtime_end"] == plan["end"] else r for r in rows]
                            last_write = final and row_offset + WRITE_BATCH >= len(page)
                            plan["offset"] += len(rows)
                            n = _write(db_path, sid, since, rows,
                                       completed=plan["last"] if last_write else None, pending=plan)
                            summary["rows_added"] += n
                            total_added += n
                    summary["vintages_added"] += plan["count"]
                    total_vintages += plan["count"]
                    if pending:
                        more = _plans(client, sid, since, plan["last"], until)
                        plans.extend(more)
                        summary["vintages_pending"] += sum(p["count"] for p in more)
                        summary["minimum_observation_requests"] += len(more)
                        pending = None
        except (Exception, KeyboardInterrupt) as exc:
            failed = True
            summary["status"] = "incomplete" if isinstance(exc, (RequestLimit, KeyboardInterrupt)) else "error"
            # Only our own sanitized transport errors are exposed; unexpected
            # exception messages could contain a request URL with credentials.
            summary["error"] = (str(exc) if type(exc) in (RuntimeError, RequestLimit)
                                else type(exc).__name__)
        summary["requests"] = client.requests - before
        emit(json.dumps(summary, sort_keys=True))
        if failed:
            break
    return 1 if failed else (0 if total_added or total_vintages else 3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--series", nargs="+", help="Configured FRED IDs (spaces or commas)")
    parser.add_argument("--since-vintage", default=EARLIEST, type=lambda s: date.fromisoformat(s).isoformat())
    parser.add_argument("--dry-run", action="store_true", help="Metadata and resume plan only; no DB writes")
    parser.add_argument("--max-requests", type=int)
    args = parser.parse_args(argv)
    if args.max_requests is not None and args.max_requests < 0:
        parser.error("--max-requests must be nonnegative")
    configured = {entry["series_id"] for entry in configured_series()}
    selected = list(dict.fromkeys(s for item in args.series for s in item.split(","))) if args.series else sorted(configured)
    if set(selected) - configured:
        parser.error("--series must contain configured series IDs")
    try:
        with requests.Session() as session:
            return run(args.db, selected, since=args.since_vintage, dry_run=args.dry_run,
                       client=Client(max_requests=args.max_requests, session=session))
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
