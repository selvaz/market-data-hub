"""Offline, deterministic disposition of imported calendar evidence.

Completion means a catalogue/period mapping, never verification of a legacy value.
Final decisions are append-only; recent missing releases remain re-runnable.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re

from .models import utc
from .resolve import canonical_unit
from .store import open_writer

RULE_VERSION = "legacy-worklist-v1"
LATENESS_DAYS = 14
REASONS = {"period_unknown", "unlinked_sighting", "late_value_needs_review", "overdue", "incomplete"}
MONTHS = {name: n for n, name in enumerate((
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december"), 1)}
MONTHS.update({name[:3]: n for name, n in list(MONTHS.items())})


def normalize_name(value):
    """The stored alias spelling convention: case and punctuation, no fuzzy match."""
    return re.sub(r"[\W_]+", " ", value.casefold()).strip()


def frequency(value):
    return {"monthly": "M", "quarterly": "Q", "annual": "A", "yearly": "A",
            "daily": "D", "weekly": "W"}.get(value.lower(), value.upper())


def explicit_period(raw, freq):
    """Parse only a fully stated period. Release dates/URLs never supply a year."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    freq = frequency(freq)
    period, kind = None, None
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        period, kind = text, "M"
    elif re.fullmatch(r"\d{4}-Q[1-4]", text):
        period, kind = text, "Q"
    elif re.fullmatch(r"\d{4}", text) and int(text) > 0:
        period, kind = text, "A"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            period, kind = date.fromisoformat(text).isoformat(), "D"
        except ValueError:
            return None
    else:
        month = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{4})", text)
        quarter = re.fullmatch(r"Q([1-4])\s+(\d{4})", text, re.I)
        if month and month[1].lower() in MONTHS:
            period, kind = f"{month[2]}-{MONTHS[month[1].lower()]:02}", "M"
        elif quarter:
            period, kind = f"{quarter[2]}-Q{quarter[1]}", "Q"
    if period and not period.startswith("0000") and (freq in {"UNKNOWN", "IRREGULAR"} or
            freq == kind or (kind == "D" and freq == "W")):
        return period
    return None


class EvidenceIndex:
    def __init__(self, store):
        conn = store.conn
        self.indicators = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM indicators")}
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
        self.rows = defaultdict(list)
        for row in conn.execute(
                "SELECT l.*,r.reference_period,r.value,r.unit,r.scale,r.content_hash,r.raw_payload_json,r.first_seen_at "
                "FROM legacy_reading_links l JOIN readings r ON r.id=l.reading_id "
                "WHERE l.reading_kind='actual' ORDER BY l.reading_id"):
            row = dict(row)
            row["raw"] = json.loads(row.pop("raw_payload_json"))["row"]
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
            return None, "indicator_ambiguous" if identities else "name_not_in_catalogue"
        indicator, stage = next(iter(identities))
        if indicator not in self.indicators:
            return None, "name_not_in_catalogue"
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


def decide(index, work, as_of):
    rows = index.rows[work["source_id"], work["occurrence_key"]]
    published = [r for r in rows if r["legacy_run_status"] == "published" and r["first_seen_at"] <= as_of]
    evidence = {"reading_ids": [r["reading_id"] for r in rows],
                "content_hashes": sorted({r["content_hash"] for r in rows}),
                "as_of": as_of, "lateness_days": LATENESS_DAYS}
    decision = dict(source_id=work["source_id"], occurrence_key=work["occurrence_key"],
                    status="pending", reason=work["reason"], indicator_id=work["indicator_id"],
                    reference_period=None, estimate_stage="unknown", selected_vintage_id=None,
                    rule_version=RULE_VERSION, resolved_at=as_of, evidence=evidence)

    def finish(status, reason):
        decision.update(status=status, reason=reason)
        return decision

    if not published:
        return finish("pending", "legacy_evidence_unavailable")
    mapping, error = index.mapping(work, published)
    if error:
        return finish("terminal", error)
    indicator_id, stage, codes = mapping
    decision.update(indicator_id=indicator_id, estimate_stage=stage)
    evidence["legacy_codes"] = codes
    indicator = index.indicators[indicator_id]
    periods = set()
    for row in published:
        raw_period = (row["raw"].get("raw_period") if row["legacy_table"] == "sightings"
                      else row["reference_period"])
        period = explicit_period(raw_period, indicator["frequency"])
        if period:
            periods.add(period)
    evidence["explicit_periods"] = sorted(periods)
    if len(periods) > 1:
        return finish("terminal", "conflicting_reference_periods")
    period = next(iter(periods), None)
    # Missing reference periods can only be supplied by a unique issuer
    # publication on the explicitly recorded release date, with equal actual
    # value, compatible units/scales and an explicitly matching stage.
    ids = official_ids(index, indicator_id, codes, period)
    matches = []
    for v in index.official:
        if v["indicator_id"] not in ids or (stage != "unknown" and v["estimate_stage"] != stage):
            continue
        if v["first_seen_at"] > as_of or v["issuer_published_at"] > as_of:
            continue
        if period:
            if v["reference_period"] == period:
                matches.append(v)
        elif stage != "unknown":
            for row in published:
                if (v["issuer_published_at"][:10] == row["raw"].get("release_date") and
                        row["value"] is not None and row["scale"] is not None and
                        canonical_unit(row["unit"]) == canonical_unit(v["unit"]) and
                        row["value"] * row["scale"] == v["value"] * v["scale"]):
                    matches.append(v)
                    break
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
    if work["reason"] == "overdue":
        # A changed schedule must extend the window rather than use the oldest
        # date retained in an occurrence's observation history.
        dates = [date.fromisoformat(r["raw"]["release_date"]) for r in published]
        latest = max(dates)
        evidence["latest_release_date"] = latest.isoformat()
        evidence["days_late"] = (date.fromisoformat(as_of[:10]) - latest).days
        if evidence["days_late"] > LATENESS_DAYS:
            return finish("terminal", "lateness_window_expired")
        return finish("pending", "overdue")
    return finish("complete", "late_value_mapped_unverified" if work["reason"] == "late_value_needs_review"
                  else "catalogue_period_mapped")


def counts(store):
    return {f"{r['status']}/{r['reason']}": r["n"] for r in store.conn.execute(
        "SELECT status,reason,COUNT(*) n FROM import_worklist GROUP BY status,reason ORDER BY status,reason")}


def resolve_worklist(store, *, as_of=None):
    """Resolve pending supported legacy reasons atomically, using no network.

    ``as_of`` is a UTC knowledge/evaluation cutoff, recorded with every decision.
    Repeating a pass never rewrites a final decision or changes raw evidence.
    """
    as_of = utc(as_of or datetime.now(timezone.utc).isoformat())
    with store.batch():
        before = counts(store)
        index = EvidenceIndex(store)
        decisions = [decide(index, dict(row), as_of) for row in store.conn.execute(
            "SELECT * FROM import_worklist WHERE status='pending' ORDER BY source_id,occurrence_key")
            if row["reason"] in REASONS]
        for d in decisions:
            if d["status"] == "pending":
                continue
            values = {k: v for k, v in d.items() if k != "evidence"}
            values["evidence_json"] = json.dumps(d["evidence"], sort_keys=True)
            store.conn.execute(
                f"INSERT INTO worklist_resolutions ({','.join(values)}) "
                f"VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            store.conn.execute(
                "UPDATE import_worklist SET status=?,reason=?,indicator_id=?,last_checked_at=?,next_check_at=NULL "
                "WHERE source_id=? AND occurrence_key=?",
                (d["status"], d["reason"], d["indicator_id"], as_of, d["source_id"], d["occurrence_key"]))
        after = counts(store)
    return {"rule_version": RULE_VERSION, "as_of": as_of, "before": before, "after": after,
            "rules": dict(sorted(Counter(d["reason"] for d in decisions if d["status"] != "pending").items())),
            "pending": sum(n for key, n in after.items() if key.startswith("pending/")),
            "decisions": decisions}


def execute(path, *, dry_run=False, as_of=None):
    path = Path(path).resolve()
    if any(part.casefold() == "programdata" for part in path.parts):
        raise ValueError("resolve-worklist refuses ProgramData paths; use a local copy")
    if not path.is_file():
        raise ValueError("resolve-worklist requires an existing database")
    if dry_run:
        from .run_daily import preview_store
        context = preview_store(path)
    else:
        context = open_writer(path)
    with context as store:
        result = resolve_worklist(store, as_of=as_of)
    return dict(result, dry_run=dry_run)
