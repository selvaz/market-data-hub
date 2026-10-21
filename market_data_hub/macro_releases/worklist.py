"""Offline, deterministic disposition of imported calendar evidence.

Completion means a catalogue/period mapping, never verification of a legacy value.
Final decisions are append-only; recent missing releases remain re-runnable.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .models import utc
from .resolve import canonical_unit
from .store import open_writer
from .lease import RunLease
from .worklist_periods import (explicit_period as explicit_period, frequency, period_evidence, partial_agrees,
                              PERIOD_REASONS, NON_PERIOD_REASONS)

RULE_VERSION = "legacy-worklist-v3"
LATENESS_DAYS = 14
REASONS = {"period_unknown", "unlinked_sighting", "late_value_needs_review", "overdue", "incomplete",
           "release_in_future", "release_date_missing", "release_date_invalid", "period_format_unrecognised",
           "period_frequency_mismatch", "evidence_horizon_before_deadline", "legacy_evidence_unavailable"} | PERIOD_REASONS


def normalize_name(value):
    """The stored alias spelling convention: case and punctuation, no fuzzy match."""
    return re.sub(r"[\W_]+", " ", value.casefold()).strip()


class EvidenceIndex:
    def __init__(self, store):
        conn = store.conn
        self.indicators = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM indicators")}
        self.import_reasons = {}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='active_worklist_resolutions'").fetchone():
            for row in conn.execute("SELECT * FROM active_worklist_resolutions"):
                self.import_reasons[row["source_id"], row["occurrence_key"]] = json.loads(row["evidence_json"]).get(
                    "original_import_reason", row["prior_reason"])
        self.candidates = {}
        self.names = defaultdict(set)
        for row in conn.execute("SELECT * FROM legacy_candidates ORDER BY source_id,legacy_code"):
            row = dict(row)
            self.candidates[row["source_id"], row["legacy_code"]] = row
            raw = json.loads(row["raw_payload_json"])
            self.names[row["source_id"], raw["jurisdiction"], normalize_name(raw["name"])].add(
                (row["indicator_id"], row["estimate_stage"], row["legacy_code"]))
        for row in conn.execute("SELECT * FROM indicator_aliases"):
            self.names[row["source_id"], row["geography"], normalize_name(row["alias"])].add(
                (row["indicator_id"], row["estimate_stage"], row["legacy_code"]))
        self.name_sources = {(source, name) for source, geo, name in self.names}
        self.rows = defaultdict(list)
        for row in conn.execute(
                "SELECT l.*,r.reference_period,r.value,r.unit,r.scale,r.content_hash,r.raw_payload_json,r.first_seen_at "
                "FROM legacy_reading_links l JOIN readings r ON r.id=l.reading_id "
                "WHERE l.reading_kind='actual' ORDER BY l.reading_id"):
            row = dict(row)
            payload = json.loads(row.pop("raw_payload_json"))
            row["raw"] = payload["row"]
            row["observed_at"] = row["raw"].get("observed_at") or payload.get("legacy_first_seen_at")
            if row["legacy_table"] == "sightings":
                self.rows[row["source_id"], f"sighting:{row['legacy_row_id']}"].append(row)
            if row["occurrence_key"]:
                self.rows[row["source_id"], row["occurrence_key"]].append(row)
        self.official = [dict(r) for r in conn.execute(
            "SELECT * FROM official_candidates WHERE formula_version IS NULL ORDER BY id")]

    def mapping(self, work, rows):
        mappings = set()
        for row in rows:
            if row["legacy_table"] == "sightings" and work["occurrence_key"].startswith("sighting:"):
                raw = row["raw"]
                mappings.update(self.names.get((work["source_id"], raw["jurisdiction"],
                                               normalize_name(raw["raw_name"])), ()))
            if row["indicator_id"]:
                mappings.add((row["indicator_id"], row["estimate_stage"], row["legacy_code"]))
        if work["indicator_id"] and not mappings:
            mappings.add((work["indicator_id"], "unknown", None))
        identities = {(i, s) for i, s, _ in mappings}
        if len(identities) != 1:
            elsewhere = any((work["source_id"], normalize_name(row["raw"].get("raw_name", "")))
                            in self.name_sources for row in rows)
            return None, ("indicator_ambiguous" if identities else
                          "catalogue_jurisdiction_mismatch" if elsewhere else "outside_catalogue_scope")
        indicator, stage = next(iter(identities))
        if indicator not in self.indicators:
            return None, "outside_catalogue_scope"
        return (indicator, stage, sorted(c for i, s, c in mappings if c)), None


def official_ids(index, indicator_id, codes, period):
    """Reviewed exact-measure bridges, gated by the shipped classification.

    Context-card equality alone is insufficient (e.g. GDP index != m/m).
    Unknown legacy seasonal adjustment stays unknown on legacy readings. Only
    an existing verified issuer vintage supplies the supported measure/value.
    """
    from market_data_hub.indicator_cards import indicator_card

    bridges = {"us_cpi_mm": "us_cpi_headline_mm", "us_cpi_yy": "us_cpi_headline_yy",
               "us_core_cpi_yy": "us_cpi_core_yy", "us_core_cpi_mm": "us_cpi_core_mm",
               "us_nfp": "us_payrolls_change", "us_gdp": "us_gdp_qq_saar",
               "us_gdp_qq_third": "us_gdp_qq_saar", "uk_gdp_mm": "uk_gdp_mm"}
    result = {indicator_id}
    for code in codes:
        target = bridges.get(code)
        if code in {"ez_hicp_yy", "ez_hicp_yy_final", "ez_hicp_mm_flash", "ez_hicp_mm_final"}:
            prefix = "ea21" if period and period >= "2026-01" else "ea"
            target = f"{prefix}_hicp_{'mm' if '_mm_' in code else 'yy'}"
            if not period or period < "2023-01":
                continue
        if target not in index.indicators:
            continue
        legacy, official = index.indicators[indicator_id], index.indicators[target]
        if (frequency(legacy["frequency"]) != official["frequency"] or
                canonical_unit(legacy["unit"]) != canonical_unit(official["unit"]) or
                legacy["scale"] != official["scale"]):
            continue
        if legacy["transform"] != official["transform"]:
            # The legacy seed stores q/q even when the name explicitly says
            # annualised. Do not similarly repair contradictory core CPI metadata.
            if not (code == "us_gdp" and legacy["transform"] == "qq" and
                    "annualizz" in legacy["measure"].casefold()):
                continue
        if legacy["geography"] != official["geography"] and not (
                code.startswith("ez_hicp_") and legacy["geography"] == "EMU" and
                official["geography"] in {"EA20", "EA21"}):
            continue
        left, right = indicator_card("cal:" + code), indicator_card("cat:" + target)
        if left is not None and right is not None and left.key == right.key:
            result.add(target)
    return result


def has_actual(row):
    return row["value"] is not None or row["raw"].get("raw_actual", row["raw"].get("actual_raw")) is not None


def value_agrees(row, vintage):
    return (row["value"] is not None and row["scale"] is not None and
            canonical_unit(row["unit"]) == canonical_unit(vintage["unit"]) and
            Decimal(str(row["value"])) * Decimal(str(row["scale"])) ==
            Decimal(str(vintage["value"])) * Decimal(str(vintage["scale"])))


def decide(index, work, as_of, lateness_days=LATENESS_DAYS):
    rows = index.rows[work["source_id"], work["occurrence_key"]]
    published = [r for r in rows if r["legacy_run_status"] == "published" and r["first_seen_at"] <= as_of]
    evidence = {"reading_ids": [r["reading_id"] for r in rows],
                "content_hashes": sorted({r["content_hash"] for r in rows}),
                "as_of": as_of, "lateness_days": lateness_days}
    original_reason = index.import_reasons.get((work["source_id"], work["occurrence_key"]), work["reason"])
    evidence["original_import_reason"] = original_reason
    decision = dict(source_id=work["source_id"], occurrence_key=work["occurrence_key"],
                    status="pending", reason=work["reason"], indicator_id=work["indicator_id"],
                    reference_period=None, estimate_stage="unknown", selected_vintage_id=None,
                    rule_version=RULE_VERSION, resolved_at=as_of, evidence=evidence)

    def finish(status, reason):
        decision.update(status=status, reason=reason)
        return decision

    if not published:
        return finish("pending", "legacy_evidence_unavailable")
    if any(not r["raw"].get("release_date") for r in published):
        return finish("pending", "release_date_missing")
    try:
        latest = max(date.fromisoformat(r["raw"]["release_date"]) for r in published)
    except (ValueError, TypeError):
        return finish("pending", "release_date_invalid")
    evidence["latest_release_date"] = latest.isoformat()
    evidence["days_late"] = (date.fromisoformat(as_of[:10]) - latest).days
    if latest > date.fromisoformat(as_of[:10]):
        return finish("pending", "release_in_future")
    mapping, error = index.mapping(work, published)
    if error:
        return finish("terminal", error)
    indicator_id, stage, codes = mapping
    decision.update(indicator_id=indicator_id, estimate_stage=stage)
    evidence["legacy_codes"] = codes
    indicator = index.indicators[indicator_id]
    periods = set()
    failures = set()
    partials = []
    evidence["raw_period_failures"] = []
    evidence["ignored_period_labels"] = []
    for row in published:
        raw_period = (row["raw"].get("raw_period") if row["legacy_table"] == "sightings"
                      else row["reference_period"])
        period, reason = period_evidence(
            raw_period,
            indicator["frequency"],
            indicator["geography"],
            indicator["definition"] or "",
            measure=indicator["measure"],
        )
        if period:
            periods.add(period)
        elif reason == "period_year_missing":
            partials.append(raw_period)
        elif reason:
            failures.add(reason)
            evidence["raw_period_failures"].append({"raw": raw_period, "reason": reason})
        elif raw_period:
            evidence["ignored_period_labels"].append(raw_period)
    for raw_period in sorted(set(partials)):
        if len(periods) == 1 and partial_agrees(raw_period, next(iter(periods))):
            evidence["ignored_period_labels"].append(raw_period)
        else:
            failures.add("period_year_missing")
            evidence["raw_period_failures"].append({"raw": raw_period, "reason": "period_year_missing"})
    evidence["explicit_periods"] = sorted(periods)
    evidence["period_failures"] = sorted(failures)
    if len(periods) > 1:
        return finish("terminal", "conflicting_reference_periods")
    if failures & NON_PERIOD_REASONS:
        return finish("terminal", sorted(failures & NON_PERIOD_REASONS)[0])
    if failures:
        # A known incompatible window explains why even a valid month cannot map.
        windows = failures & {"period_is_cumulative_window", "period_is_rolling_window", "period_is_submonthly_window"}
        return finish("pending", sorted(windows or failures)[0])
    period = next(iter(periods), None)
    actuals = [row for row in published if has_actual(row)]
    ids = official_ids(index, indicator_id, codes, period)
    matches = []
    for v in index.official:
        if v["indicator_id"] not in ids or (stage != "unknown" and v["estimate_stage"] != stage):
            continue
        if v["first_seen_at"] > as_of or v["issuer_published_at"] > as_of:
            continue
        if v["issuer_published_at"][:10] != latest.isoformat():
            continue
        if actuals and not all(value_agrees(row, v) for row in actuals):
            continue
        if (period and v["reference_period"] == period) or (not period and stage != "unknown" and actuals):
            matches.append(v)
    targets = {(v["indicator_id"], v["reference_period"]) for v in matches}
    if len(targets) == 1:
        chosen = max(matches, key=lambda v: (v["issuer_published_at"], v["first_seen_at"], v["id"]))
        decision.update(indicator_id=chosen["indicator_id"], reference_period=chosen["reference_period"],
                        estimate_stage=chosen["estimate_stage"], selected_vintage_id=chosen["id"])
        evidence["official_match"] = {k: chosen[k] for k in (
            "id", "indicator_id", "reference_period", "issuer_published_at", "document_hash")}
        return finish("complete", "official_vintage_linked")
    if not period:
        return finish("terminal", "period_not_derivable")
    decision["reference_period"] = period
    released = any(r["raw"].get("status") == "released" or has_actual(r) for r in published)
    evidence["released_evidence_present"] = released
    if not released and original_reason in {"overdue", "lateness_window_expired", "evidence_horizon_before_deadline"}:
        if evidence["days_late"] > lateness_days:
            horizon = max((r["observed_at"] or "")[:10] for r in published)
            evidence["evidence_horizon"] = horizon
            if horizon < (latest + timedelta(days=lateness_days)).isoformat():
                return finish("pending", "evidence_horizon_before_deadline")
            return finish("terminal", "lateness_window_expired")
        return finish("pending", "overdue")
    return finish("complete", "late_value_mapped_unverified" if original_reason == "late_value_needs_review"
                  else "catalogue_period_mapped")


def counts(store):
    return {f"{r['status']}/{r['reason']}": r["n"] for r in store.conn.execute(
        "SELECT status,reason,COUNT(*) n FROM import_worklist GROUP BY status,reason ORDER BY status,reason")}


def now_utc():
    return utc(datetime.now(timezone.utc).isoformat())


def evaluation_cutoff(as_of, lateness_days):
    now = now_utc()
    cutoff = utc(as_of) if as_of else now
    if cutoff > now:
        raise ValueError("--as-of cannot be later than the current UTC time")
    if isinstance(lateness_days, bool) or not isinstance(lateness_days, int) or lateness_days < 1:
        raise ValueError("--lateness-days must be an integer of at least 1")
    return cutoff


def ensure_audit(store):
    """Create the optional audit feature on v5 and additive compatible schemas."""
    from .migrations import SCHEMA_VERSION

    if (
        not 5
        <= store.conn.execute("PRAGMA user_version").fetchone()[0]
        <= SCHEMA_VERSION
    ):
        raise ValueError(
            "worklist resolution requires a schema v5 or later database copy"
        )
    statement = ""
    for line in Path(__file__).with_name("worklist_audit.sql").read_text(encoding="utf-8").splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            store.conn.execute(statement)
            statement = ""


def post_state(prior, decision):
    return dict(prior, status=decision["status"], reason=decision["reason"], indicator_id=decision["indicator_id"],
                last_checked_at=decision["resolved_at"], next_check_at=(
                    (datetime.fromisoformat(decision["resolved_at"]) + timedelta(days=1)).isoformat(timespec="microseconds")
                    if decision["status"] == "pending" else None))


def write_state(store, state):
    store.conn.execute(
        "UPDATE import_worklist SET indicator_id=?,reason=?,status=?,first_seen_at=?,last_checked_at=?,next_check_at=? "
        "WHERE source_id=? AND occurrence_key=?", tuple(state[k] for k in (
            "indicator_id", "reason", "status", "first_seen_at", "last_checked_at", "next_check_at",
            "source_id", "occurrence_key")))


def resolve_worklist(store, *, as_of=None, lateness_days=LATENESS_DAYS):
    """Resolve pending supported legacy reasons atomically, using no network.

    ``as_of`` is a UTC knowledge/evaluation cutoff, recorded with every decision.
    Repeating a pass never rewrites a final decision or changes raw evidence.
    """
    as_of = evaluation_cutoff(as_of, lateness_days)
    run_id = uuid4().hex
    with store.batch():
        last_import = store.conn.execute(
            "SELECT MAX(first_seen_at) FROM readings WHERE parser_version LIKE 'legacy-calendar-%'").fetchone()[0]
        last_state = store.conn.execute("SELECT MAX(last_checked_at) FROM import_worklist").fetchone()[0]
        floor = max((t for t in (last_import, last_state) if t), default=None)
        if floor and as_of < floor:
            # import_worklist holds only the latest state, not its history: a cutoff before the last
            # import or worklist update would judge items by reasons that did not exist at that cutoff.
            raise ValueError(f"--as-of {as_of} precedes the worklist state it would read (last change {floor}); "
                             "a past cutoff cannot be honoured on mutable worklist rows")
        ensure_audit(store)
        before = counts(store)
        index = EvidenceIndex(store)
        work = [dict(row) for row in store.conn.execute(
            "SELECT * FROM import_worklist WHERE status='pending' OR reason='lateness_window_expired' "
            "ORDER BY source_id,occurrence_key") if row["reason"] in REASONS or row["reason"] == "lateness_window_expired"]
        decisions = [decide(index, row, as_of, lateness_days) for row in work]
        for prior, d in zip(work, decisions):
            d["changed"] = any(prior[k] != d[k] for k in ("status", "reason", "indicator_id"))
            if not d["changed"]:
                continue
            values = {k: v for k, v in d.items() if k not in {"evidence", "changed"}}
            values.update(run_id=run_id, prior_status=prior["status"], prior_reason=prior["reason"],
                          prior_state_json=json.dumps(prior, sort_keys=True))
            values["evidence_json"] = json.dumps(d["evidence"], sort_keys=True)
            store.conn.execute(
                f"INSERT INTO worklist_resolutions ({','.join(values)}) "
                f"VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            write_state(store, post_state(prior, d))
        after = counts(store)
    return {"run_id": run_id, "rule_version": RULE_VERSION, "as_of": as_of, "lateness_days": lateness_days,
            "warnings": [], "before": before, "after": after,
            "rules": dict(sorted(Counter(d["reason"] for d in decisions if d["changed"]).items())),
            "pending": sum(n for key, n in after.items() if key.startswith("pending/")),
            "decisions": decisions}


def revert_worklist(store, run_id):
    """Restore every worklist field atomically; refuse changed or superseded rows."""
    with store.batch():
        ensure_audit(store)
        before = counts(store)
        if store.conn.execute("SELECT 1 FROM worklist_reverts WHERE run_id=?", (run_id,)).fetchone():
            return {"reverted_run_id": run_id, "reverted": 0, "before": before, "after": before}
        rows = [dict(r) for r in store.conn.execute(
            "SELECT * FROM worklist_resolutions WHERE run_id=? ORDER BY audit_id DESC", (run_id,))]
        if not rows:
            raise ValueError("unknown worklist run_id")
        for row in rows:
            active = store.conn.execute(
                "SELECT audit_id FROM active_worklist_resolutions WHERE source_id=? AND occurrence_key=?",
                (row["source_id"], row["occurrence_key"])).fetchone()
            current = dict(store.conn.execute(
                "SELECT * FROM import_worklist WHERE source_id=? AND occurrence_key=?",
                (row["source_id"], row["occurrence_key"])).fetchone())
            prior = json.loads(row["prior_state_json"])
            if not active or active["audit_id"] != row["audit_id"] or current != post_state(prior, row):
                raise ValueError("revert refused: worklist item changed or a newer run must be reverted first")
            write_state(store, prior)
        store.conn.execute("INSERT INTO worklist_reverts VALUES (?,?)", (run_id, now_utc()))
        return {"reverted_run_id": run_id, "reverted": len(rows), "before": before, "after": counts(store)}


def execute(path, *, dry_run=False, as_of=None, lateness_days=LATENESS_DAYS, revert=None):
    path = Path(path).resolve()
    if any(part.casefold() == "programdata" for part in path.parts):
        raise ValueError("resolve-worklist refuses ProgramData paths; use a local copy")
    if not path.is_file():
        raise ValueError("resolve-worklist requires an existing database")
    if revert and (as_of is not None or lateness_days != LATENESS_DAYS):
        raise ValueError("--revert cannot be combined with --as-of or --lateness-days")
    cutoff = None if revert else evaluation_cutoff(as_of, lateness_days)
    if dry_run:
        from .run_daily import preview_store
        with preview_store(path) as store:
            result = (revert_worklist(store, revert) if revert else
                      resolve_worklist(store, as_of=cutoff, lateness_days=lateness_days))
    else:
        with RunLease(path, wait_for_writer=False) as lease:
            with open_writer(path, lock_timeout=0) as store:
                lease.validate()
                result = (revert_worklist(store, revert) if revert else
                          resolve_worklist(store, as_of=cutoff, lateness_days=lateness_days))
    return dict(result, dry_run=dry_run)
