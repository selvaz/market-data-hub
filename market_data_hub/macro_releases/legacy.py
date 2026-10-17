"""Read-only pull of LazyCommittee evidence. No official-value promotion.

Every run scans the source again, using immutable raw-row hashes for idempotency.
The worklist tracks missing/late evidence rather than a MAX(run_id) watermark.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Optional, Union

from .models import Indicator, Reading, utc
from .numbers import parse_number
from .store import Store, connect_readonly

PARSER_VERSION = "legacy-calendar-m4-v2"
_STAGE = re.compile(r"^(.*?)\s*\((flash|final)\)\s*$", re.IGNORECASE)
# Exactly the raw columns protected by sightings_no_raw_update, plus its id.
_SIGHTING_RAW = (
    "id",
    "run_id",
    "searcher",
    "region",
    "jurisdiction",
    "raw_name",
    "raw_period",
    "release_at_utc",
    "release_date",
    "raw_actual",
    "raw_consensus",
    "raw_previous",
    "unit_hint",
    "sources_json",
)


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _period(raw):
    if not raw:
        return None, "legacy reference period missing"
    if raw.startswith("d:"):
        return None, "legacy d: key identifies a release date, not a reference period"
    # Do not infer a month from a release date or silently reinterpret free text.
    if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2]|Q[1-4])|\d{4}|\d{4}-\d{2}-\d{2}", raw):
        return raw, None
    return None, "unrecognised legacy reference period; retained in raw payload"


def _url(value: str) -> str:
    parts = urlsplit(value)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            urlencode(sorted(query)),
            "",
        )
    )


def _origins(raw_sources):
    sources = json.loads(raw_sources)
    if not isinstance(sources, list):
        sources = [sources]
    for source in sources or [None]:
        if isinstance(source, str):
            url = _url(source)
            origin = "url:" + url
        elif isinstance(source, dict):
            url = _url(source.get("url") or source.get("link") or "") or None
            explicit = (
                source.get("evidence_origin_id")
                or source.get("origin_id")
                or source.get("wire_id")
            )
            origin = (
                "declared:" + str(explicit)
                if explicit
                else ("url:" + url if url else "unknown")
            )
        else:
            url, origin = None, "unknown"
        # Unknown origin is deliberately shared, never one vote per searcher.
        yield url, origin


def _catalogue(rows, source_id):
    groups = {}
    for row in rows:
        match = _STAGE.fullmatch(row["name"])
        if match:
            # Exact base name and matching non-missing semantic metadata only.
            signature = tuple(
                row.get(k)
                for k in (
                    "jurisdiction",
                    "agency",
                    "unit_code",
                    "transform",
                    "basis",
                    "frequency",
                    "scale",
                )
            )
            groups.setdefault((match[1].strip().casefold(), signature), []).append(row)
    certain = {}
    for (_, signature), pair in groups.items():
        if len(pair) != 2 or any(v is None or v == "" for v in signature):
            continue
        stages = {_STAGE.fullmatch(row["name"])[2].lower() for row in pair}
        if stages != {"flash", "final"}:
            continue
        common_id = (
            f"{source_id}:stage-pair:" + _hash(sorted(row["code"] for row in pair))[:24]
        )
        for row in pair:
            certain[row["code"]] = common_id
    result = {}
    for row in rows:
        match = _STAGE.fullmatch(row["name"])
        stage = match[2].lower() if match else "unknown"
        indicator_id = certain.get(row["code"], f"{source_id}:{row['code']}")
        reason = (
            "exact flash/final base and semantic metadata match; candidate only"
            if row["code"] in certain
            else "unreviewed legacy catalogue entry; no semantic merge"
        )
        result[row["code"]] = (indicator_id, stage, reason)
    return result


def _work_item(
    store, source_id, key, indicator_id, reason, now, next_check, *, status="pending"
):
    with store.transaction():
        if reason:
            if status == "terminal":
                existing = store.conn.execute(
                    "SELECT status, reason FROM import_worklist WHERE source_id=? AND occurrence_key=?",
                    (source_id, key),
                ).fetchone()
                if (
                    existing
                    and existing["status"] == status
                    and existing["reason"] == reason
                ):
                    return
            store.conn.execute(
                "INSERT INTO import_worklist VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id, occurrence_key) DO UPDATE SET "
                "reason=excluded.reason, status=excluded.status, indicator_id=excluded.indicator_id, "
                "last_checked_at=excluded.last_checked_at, next_check_at=excluded.next_check_at",
                (
                    source_id,
                    key,
                    indicator_id,
                    reason,
                    status,
                    now,
                    now,
                    None if status == "terminal" else next_check,
                ),
            )
        else:
            store.conn.execute(
                "UPDATE import_worklist SET status='complete', last_checked_at=?, next_check_at=NULL "
                ", indicator_id=? "
                "WHERE source_id=? AND occurrence_key=?",
                (now, indicator_id, source_id, key),
            )


def _link_reading(store, reading_id, source_id, table, row, kind, index, mapping, now):
    indicator_id, stage, _ = mapping
    if kind == "previous":
        stage = "unknown"
    with store.transaction():
        store.conn.execute(
            "INSERT INTO legacy_reading_links VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(reading_id) DO UPDATE SET legacy_code=excluded.legacy_code, "
            "indicator_id=excluded.indicator_id, estimate_stage=excluded.estimate_stage, "
            "occurrence_key=excluded.occurrence_key, resolution=excluded.resolution, "
            "unresolved_reason=excluded.unresolved_reason, legacy_run_status=excluded.legacy_run_status, "
            "updated_at=excluded.updated_at",
            (
                reading_id,
                source_id,
                table,
                row["id"],
                kind,
                index,
                row.get("indicator_code"),
                indicator_id,
                stage,
                str(row["occurrence_id"]) if row.get("occurrence_id") else None,
                row.get("resolution"),
                row.get("unresolved_reason"),
                row["legacy_run_status"],
                now,
            ),
        )


def _existing_sighting(store, source_id, row_id, kind, index):
    """Also recognize pre-fix v1 imports without changing append-only evidence."""
    linked = store.conn.execute(
        "SELECT reading_id FROM legacy_reading_links WHERE source_id=? AND legacy_table='sightings' "
        "AND legacy_row_id=? AND reading_kind=? AND source_index=?",
        (source_id, row_id, kind, index),
    ).fetchone()
    if linked:
        return linked["reading_id"]
    old = store.conn.execute(
        "SELECT id FROM readings WHERE source_id=? AND parser_version='legacy-calendar-m4-v1' "
        "AND reading_kind=? AND json_extract(raw_payload_json, '$.table')='sightings' "
        "AND json_extract(raw_payload_json, '$.row.id')=? "
        "AND json_extract(raw_payload_json, '$.source_index')=? ORDER BY first_seen_at, id LIMIT 1",
        (source_id, kind, row_id, index),
    ).fetchone()
    return old["id"] if old else None


def _import_evidence(store, source, mappings, source_id, now, next_check):
    for table in ("sightings", "observations"):
        raw_columns = tuple(r[1] for r in source.execute(f"PRAGMA table_info({table})"))
        sql = (
            "SELECT s.*, r.status AS legacy_run_status, r.started_at AS legacy_first_seen_at "
            "FROM sightings s JOIN runs r ON r.run_id=s.run_id ORDER BY s.id"
            if table == "sightings"
            else "SELECT o.*, oc.indicator_code, oc.period_norm, r.status AS legacy_run_status, "
            "r.started_at AS legacy_first_seen_at FROM observations o "
            "JOIN occurrences oc ON oc.id=o.occurrence_id JOIN runs r ON r.run_id=o.run_id ORDER BY o.id"
        )
        for raw in source.execute(sql):
            row = dict(raw)
            mapping = mappings.get(
                row.get("indicator_code"), (None, "unknown", "unmapped")
            )
            if table == "sightings":
                reason, status = None, "pending"
                if row["legacy_run_status"] == "failed":
                    reason, status = "failed_run", "terminal"
                elif row["legacy_run_status"] == "running":
                    reason = "running_run_deferred"
                elif not row.get("occurrence_id"):
                    reason = (
                        "period_unknown"
                        if _period(row["raw_period"])[0] is None
                        else "unlinked_sighting"
                    )
                _work_item(
                    store,
                    source_id,
                    f"sighting:{row['id']}",
                    mapping[0],
                    reason,
                    now,
                    next_check,
                    status=status,
                )
            if row["legacy_run_status"] == "running":
                continue
            immutable = {
                key: row[key]
                for key in (_SIGHTING_RAW if table == "sightings" else raw_columns)
            }
            content_hash = _hash(immutable)
            period_raw = (
                row.get("raw_period") if table == "sightings" else row["period_norm"]
            )
            period, unknown_reason = _period(period_raw)
            origins = list(_origins(row["sources_json"]))
            for kind in ("actual", "consensus", "previous"):
                raw_value = row.get(
                    f"raw_{kind}" if table == "sightings" else f"{kind}_raw"
                )
                value = row.get(f"{kind}_value")
                scale = row.get("scale")
                if table == "sightings" and raw_value is not None:
                    try:
                        # The legacy currency typography explicitly uses M/T.
                        currency = bool(
                            re.search(r"[$€£¥]|\b(?:USD|EUR|GBP|JPY)\b", raw_value)
                        )
                        value = parse_number(raw_value, allow_bare_magnitudes=currency)
                        scale = 1
                    except (ValueError, ArithmeticError):
                        value = None
                if kind != "actual" and raw_value is None and value is None:
                    continue
                value_period = None if kind == "previous" else period
                value_reason = (
                    (
                        unknown_reason
                        or "legacy previous field has no explicit reference period"
                    )
                    if kind == "previous"
                    else unknown_reason
                )
                reading_kind = kind if table == "sightings" else f"legacy_merged_{kind}"
                evidence = (
                    origins
                    if table == "sightings"
                    else [(None, f"legacy_merge:{source_id}:observation:{row['id']}")]
                )
                for index, (url, origin) in enumerate(evidence):
                    envelope = {
                        "table": table,
                        "row": immutable,
                        "kind": kind,
                        "source_index": index,
                        "legacy_first_seen_at": row["legacy_first_seen_at"],
                        "run_status_at_import": row["legacy_run_status"],
                        "origins": [
                            {"url": u, "evidence_origin_id": o} for u, o in origins
                        ],
                    }
                    reading_id = (
                        _existing_sighting(store, source_id, row["id"], kind, index)
                        if table == "sightings"
                        else None
                    )
                    if reading_id is None:
                        reading_id = store.put(
                            Reading(
                                source_id=source_id,
                                series_key=(
                                    f"sighting:{row['id']}:{kind}:source:{index}"
                                    if table == "sightings"
                                    else f"observation:{row['id']}:{kind}"
                                ),
                                period_key=(
                                    period or f"unknown:{period_raw or 'missing'}"
                                )
                                if table == "sightings"
                                else f"legacy-occurrence:{row['occurrence_id']}",
                                reference_period=value_period,
                                period_unknown_reason=value_reason,
                                edition=f"legacy-run:{row['run_id']}",
                                estimate_stage="unknown",
                                reading_kind=reading_kind,
                                value=value,
                                published_as=raw_value,
                                unit=row.get("unit_hint")
                                if table == "sightings"
                                else row.get("unit_code"),
                                scale=scale,
                                issuer_id="unknown",
                                distributor_id="legacy_web_search"
                                if table == "sightings"
                                else "LazyCommittee",
                                evidence_origin_id=origin,
                                first_seen_at=now,
                                retrieved_at=now,
                                content_hash=content_hash,
                                parser_version=PARSER_VERSION,
                                extraction_method="web_search"
                                if table == "sightings"
                                else "legacy_merge",
                                verification_status="unverified",
                                url=url,
                                raw_payload_json=_json(envelope),
                            )
                        )
                    _link_reading(
                        store,
                        reading_id,
                        source_id,
                        table,
                        row,
                        kind,
                        index,
                        mapping,
                        now,
                    )


def import_calendar(
    store: Store,
    path: Union[str, Path],
    *,
    source_id: str = "lazycommittee_calendar",
    captured_at: Optional[str] = None,
) -> dict:
    """Pull source v4, including failed-run sightings, into a writable hub store.

    Source mode=ro/query_only is enforced. No attached databases or source DDL.
    Each target row is committed independently; a crash/retry is safe.
    """
    now = utc(captured_at or datetime.now(timezone.utc).isoformat())
    next_check = (datetime.fromisoformat(now) + timedelta(days=1)).isoformat(
        timespec="microseconds"
    )
    before = store.conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    source = connect_readonly(path)
    try:
        if source.execute("PRAGMA user_version").fetchone()[0] != 4:
            raise RuntimeError("legacy importer requires calendar schema v4")
        catalogue = [
            dict(row)
            for row in source.execute("SELECT * FROM indicators ORDER BY code")
        ]
        mappings = _catalogue(catalogue, source_id)
        # Existing mappings are stable even if the legacy catalogue grows later.
        for saved in store.conn.execute(
            "SELECT * FROM legacy_candidates WHERE source_id=?", (source_id,)
        ):
            if saved["legacy_code"] in mappings:
                mappings[saved["legacy_code"]] = (
                    saved["indicator_id"],
                    saved["estimate_stage"],
                    saved["mapping_reason"],
                )
        for row in catalogue:
            indicator_id, stage, reason = mappings[row["code"]]
            # One representative for an exact stage pair, with every original below.
            if not store.conn.execute(
                "SELECT 1 FROM indicators WHERE id=?", (indicator_id,)
            ).fetchone():
                transform = row.get("transform")
                if transform == "pct_change":
                    transform = {"mom": "mm", "yoy": "yy", "qoq": "qq"}.get(
                        row.get("basis"), "unknown"
                    )
                if transform not in {"level", "mm", "yy", "qq", "qq_saar", "change"}:
                    transform = "unknown"
                measure = (
                    _STAGE.sub(r"\1", row["name"]).strip()
                    if "stage-pair:" in indicator_id
                    else row["name"]
                )
                store.put(
                    Indicator(
                        id=indicator_id,
                        measure=measure,
                        geography=row["jurisdiction"],
                        seasonal_adjustment="unknown",
                        transform=transform,
                        unit=row.get("unit_code") or "unknown",
                        scale=row.get("scale") or 1,
                        frequency=row.get("frequency") or "unknown",
                        issuer_id=row.get("agency") or "unknown",
                        tier=row.get("tier"),
                        definition=row.get("explanation")
                        or "Unreviewed legacy candidate",
                    )
                )
            with store.transaction():
                store.conn.execute(
                    "INSERT INTO legacy_candidates VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_id, legacy_code) DO NOTHING",
                    (source_id, row["code"], indicator_id, stage, reason, _json(row)),
                )
        for row in source.execute(
            "SELECT * FROM indicator_aliases ORDER BY alias_norm, jurisdiction"
        ):
            indicator_id, stage, _ = mappings[row["code"]]
            with store.transaction():
                store.conn.execute(
                    "INSERT INTO indicator_aliases "
                    "(source_id, alias, geography, legacy_code, indicator_id, estimate_stage) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (
                        source_id,
                        row["alias_norm"],
                        row["jurisdiction"],
                        row["code"],
                        indicator_id,
                        stage,
                    ),
                )

        _import_evidence(store, source, mappings, source_id, now, next_check)
        # No high-water mark: inspect ALL occurrences and their latest evidence.
        sql = """SELECT oc.*, o.actual_value, o.status, o.observed_at,
                        r.status AS run_status
                 FROM occurrences oc LEFT JOIN observations o ON o.id = (
                     SELECT x.id FROM observations x JOIN runs xr ON xr.run_id=x.run_id
                     WHERE x.occurrence_id=oc.id AND xr.status='published'
                     ORDER BY x.observed_at DESC, x.id DESC LIMIT 1
                 ) LEFT JOIN runs r ON r.run_id=o.run_id ORDER BY oc.id"""
        for raw in source.execute(sql):
            row = dict(raw)
            period, _ = _period(row["period_norm"])
            reason = None
            if period is None:
                reason = "period_unknown"
            elif row["actual_value"] is None or row["status"] != "released":
                reason = "overdue" if row["release_date"] < now[:10] else "incomplete"
            elif row["observed_at"] and row["observed_at"][:10] > row["release_date"]:
                reason = "late_value_needs_review"
            key = str(row["id"])
            _work_item(
                store,
                source_id,
                key,
                mappings[row["indicator_code"]][0],
                reason,
                now,
                next_check,
            )
        return {
            "new_readings": store.conn.execute(
                "SELECT COUNT(*) FROM readings"
            ).fetchone()[0]
            - before,
            "pending": store.conn.execute(
                "SELECT COUNT(*) FROM import_worklist WHERE source_id=? AND status='pending'",
                (source_id,),
            ).fetchone()[0],
        }
    finally:
        source.close()
