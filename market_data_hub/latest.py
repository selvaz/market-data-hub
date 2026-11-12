"""One hub layer that returns the most recent correct value of a concept.

Every indicator is read through the concept registry (``concepts.py``): an
ordered source cascade per concept, a declared source on every answer and no
reconciliation between sources. This module resolves the kinds ``macro``,
``derived`` and ``release``; ``price`` concepts raise ``NotImplementedError``
until a later PR adds live/post-close rules and exchange calendars.

Resolution (one algorithm for every supported kind):

1. For each non-``history_only`` source take its newest usable observation:
   a macro row dated on or before ``as_of`` (after the declared ``transform``),
   or a release card released on or before ``as_known_at`` whose reference
   period started on or before ``as_of``.
2. Monthly and quarterly observations are dated at the reference period END
   (``YYYY-MM`` -> last day of month, ``YYYY-Qn`` -> last day of quarter).
3. A source is fresh when its age in business days (Mon-Fri, no holiday
   calendar) is at most its ``max_age_business_days``. Periods that end after
   ``as_of`` (flash surveys) have age 0.
4. The tier is the first non-empty set of fresh sources among
   {primary, secondary}, {fallback}, {proxy}. Inside the tier the newest
   observation date wins; ties go to the higher-ranked source.
5. With no fresh source the newest stale observation is returned with status
   ``stale``; with no observation at all the status is ``unavailable``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from market_data_hub.concepts import load_concepts, load_macro_catalogue
from market_data_hub.config_loader import _CONFIG_DIR

PRICE_NOT_IMPLEMENTED = "price kind arrives in a later PR"
TRANSFORMS = ("level", "diff", "pct_change_mm", "pct_change_qq_saar", "pct_change_yy")
STATUSES = ("primary", "newer_secondary", "fallback", "proxy", "stale", "unavailable")
USABLE_RELEASE_STATUSES = {"official", "private"}
RESULT_KEYS = (
    "concept", "label", "family", "unit", "value", "observation_date", "reference_period",
    "source_id", "source_kind", "role", "status", "age_bd", "max_age_bd", "released_at",
    "consensus", "alternatives", "inputs", "reason", "registry_version",
)
_TIERS = ({"primary", "secondary"}, {"fallback"}, {"proxy"})
_ROLE_STATUS = {"primary": "primary", "secondary": "newer_secondary",
                "fallback": "fallback", "proxy": "proxy"}
# Look-back for release cards: covers quarterly data and eight-week policy gaps.
_RELEASE_WINDOW_DAYS = 400
_MACRO_ROWS = 80
# Bound on backward same_date alignment steps for derived concepts.
_ALIGN_STEPS = 30


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date") and callable(value.date):  # pandas Timestamp
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _month_end(day):
    return date(day.year, day.month, monthrange(day.year, day.month)[1])


def period_end_date(period):
    """Reference period END as a date: YYYY-MM, YYYY-Qn, YYYY or YYYY-MM-DD."""
    period = str(period)
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return _month_end(date.fromisoformat(period + "-01"))
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if match:
        return _month_end(date(int(match[1]), int(match[2]) * 3, 1))
    if re.fullmatch(r"\d{4}", period):
        return date(int(period), 12, 31)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return date.fromisoformat(period)
    raise ValueError(f"unsupported reference period: {period}")


def period_start_date(period):
    period = str(period)
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return date.fromisoformat(period + "-01")
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if match:
        return date(int(match[1]), int(match[2]) * 3 - 2, 1)
    if re.fullmatch(r"\d{4}", period):
        return date(int(period), 1, 1)
    return period_end_date(period)


def _observation_date(stored, frequency):
    """Macro rows store monthly/quarterly values at the period start."""
    if frequency == "M":
        return _month_end(stored)
    if frequency == "Q":
        quarter_end_month = ((stored.month - 1) // 3 + 1) * 3
        return _month_end(date(stored.year, quarter_end_month, 1))
    if frequency == "A":
        return date(stored.year, 12, 31)
    return stored


def business_day_age(observation_date, as_of):
    """Weekdays after observation_date through as_of (no holiday calendar), floored at 0."""
    from market_data_hub.reader import _resolved_business_day_age

    if observation_date >= as_of:
        return 0
    return _resolved_business_day_age(observation_date, as_of)


def _shift_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def _prior_date(stored, frequency, periods):
    if frequency == "M":
        return _shift_months(stored, -periods)
    if frequency == "Q":
        return _shift_months(stored, -3 * periods)
    if frequency == "A":
        return date(stored.year - periods, stored.month, stored.day)
    if frequency == "W":
        return stored - timedelta(days=7 * periods)
    return None


def _year_periods(frequency):
    return {"M": 12, "Q": 4, "W": 52, "A": 1}.get(frequency)


# --------------------------------------------------------------------------
# Transforms
# --------------------------------------------------------------------------

def apply_transform(rows, transform, frequency, scale=1.0):
    """Return [(stored_date, value)] after ``transform`` and ``scale``.

    ``rows`` are ascending (stored_date, value) pairs. Changes compare with the
    exact prior period (one period, or one year for ``pct_change_yy``); a
    missing prior period yields no value instead of comparing across a gap.
    Daily series compare with the previous stored row.
    """
    transform = transform or "level"
    if transform not in TRANSFORMS:
        raise ValueError(f"unknown transform {transform}")
    by_date = dict(rows)
    ordered = sorted(by_date)
    out = []
    for position, stored in enumerate(ordered):
        value = by_date[stored]
        if transform == "level":
            result = value
        else:
            periods = _year_periods(frequency) if transform == "pct_change_yy" else 1
            prior_key = _prior_date(stored, frequency, periods) if periods else None
            if prior_key is None and frequency not in {"M", "Q", "A", "W"} and transform != "pct_change_yy":
                prior_key = ordered[position - 1] if position else None
            prior = by_date.get(prior_key) if prior_key is not None else None
            if prior is None:
                continue
            if transform == "diff":
                result = value - prior
            elif prior == 0:
                continue
            elif transform == "pct_change_qq_saar":
                ratio = value / prior
                if ratio <= 0:
                    continue
                result = (ratio ** 4 - 1) * 100
            else:
                result = (value / prior - 1) * 100
        result *= scale
        if math.isfinite(result):
            out.append((stored, result))
    return out


# --------------------------------------------------------------------------
# Registry helpers
# --------------------------------------------------------------------------

def registry_version():
    """Short content hash of the concept files and their catalogue dependencies."""
    digest = hashlib.sha256()
    paths = sorted((_CONFIG_DIR / "concepts").glob("*.yaml")) + [_CONFIG_DIR / "macro_series.yaml"]
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _active_sources(spec):
    return [s for s in spec.get("sources", []) if s["role"] != "history_only"]


def concept_kind(spec):
    """Kind of the first active source (``price`` concepts start with a price), or ``derived``."""
    if spec.get("kind") == "derived":
        return "derived"
    active = _active_sources(spec)
    first = active[0]["kind"] if active else "macro"
    # A cascade whose first source is another concept resolves like macro.
    return "macro" if first == "derived" else first


def _empty_result(name, spec, version):
    result = dict.fromkeys(RESULT_KEYS)
    result.update(concept=name, label=spec.get("label"), family=spec.get("family"),
                  unit=spec.get("unit"), status="unavailable", alternatives=[], inputs=[],
                  registry_version=version)
    return result


# --------------------------------------------------------------------------
# Context: one DuckDB connection and one release snapshot per call
# --------------------------------------------------------------------------

class _Context:
    def __init__(self, as_of, as_known_at, con, db_path, releases_db):
        self.as_of = _to_date(as_of) if as_of is not None else date.today()
        if as_known_at is None:
            end = datetime.combine(self.as_of + timedelta(days=1), datetime.min.time(),
                                   tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            self.as_known_at = min(end, now) if as_of is None else end
            self.as_known_at_explicit = False
        else:
            self.as_known_at = _parse_instant(as_known_at)
            self.as_known_at_explicit = True
        self._con = con
        self._own_con = False
        self.db_path = db_path
        self.releases_db = releases_db if releases_db is not None else os.environ.get("MACRO_RELEASES_DB")
        self._cards = None
        self._full_history = {}
        self._release_error = None
        self.registry = load_concepts()
        self.macro_catalogue = load_macro_catalogue()
        self.version = registry_version()
        self.cache = {}

    @property
    def con(self):
        if self._con is None:
            from market_data_hub.reader import _resolved_connection

            self._con = _resolved_connection(self.db_path)
            self._own_con = True
        return self._con

    def close(self):
        if self._own_con and self._con is not None:
            self._con.close()
            self._con = None

    def release_cards(self, indicators):
        """Cards per indicator from ONE ReadAPI snapshot, newest period first."""
        if self._cards is None and self._release_error is None:
            self._load_cards(indicators)
        if self._release_error is not None:
            return None, self._release_error
        return self._cards, None

    def _load_cards(self, indicators):
        if not self.releases_db:
            self._release_error = "macro releases store not configured (MACRO_RELEASES_DB)"
            return
        until = self.as_known_at.astimezone(timezone.utc).date()
        since = min(until, self.as_of) - timedelta(days=_RELEASE_WINDOW_DAYS)
        try:
            self._cards = self._query_cards(indicators, since)
        except Exception as exc:  # store missing, schema range, sqlite errors
            self._release_error = f"macro releases store unreadable: {exc}"

    def full_history_cards(self, indicator):
        """Cards for one indicator without the look-back window, so a store that
        stopped updating long ago still yields its newest observation as stale."""
        if indicator not in self._full_history:
            try:
                cards = self._query_cards([indicator], date(1900, 1, 1))
            except Exception:  # noqa: BLE001 - same failure modes as _load_cards
                cards = {}
            self._full_history[indicator] = cards.get(indicator, [])
        return self._full_history[indicator]

    def _query_cards(self, indicators, since):
        from market_data_hub.macro_releases.read_api import ReadAPI
        from market_data_hub.macro_releases.store import open_reader

        cutoff = self.as_known_at.astimezone(timezone.utc)
        with open_reader(self.releases_db) as store:
            result = ReadAPI(store).release_cards(
                since.isoformat(), cutoff.date().isoformat(), indicators=sorted(indicators),
                as_known_at=cutoff.isoformat().replace("+00:00", "Z"),
            )
        cards = {}
        for card in result["cards"]:
            cards.setdefault(card["indicator_id"], []).append(card)
        return cards


def _parse_instant(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    text = str(value)
    if len(text) == 10:
        return datetime.combine(date.fromisoformat(text) + timedelta(days=1),
                                datetime.min.time(), tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Per-source candidates
# --------------------------------------------------------------------------

def _candidate(source, rank, **fields):
    item = {"source_id": source["id"], "source_kind": source["kind"], "role": source["role"],
            "rank": rank, "max_age_bd": source["max_age_business_days"], "value": None,
            "observation_date": None, "reference_period": None, "released_at": None,
            "consensus": None, "age_bd": None, "fresh": False, "reason": None}
    item.update(fields)
    return item


def _macro_candidate(ctx, source, rank):
    frequency = ctx.macro_catalogue.get(source["id"], {}).get("frequency", "D")
    rows = ctx.con.execute(
        "SELECT date, value FROM macro_series WHERE series_id = ? AND date <= ? "
        "AND value IS NOT NULL ORDER BY date DESC LIMIT ?",
        [source["id"], ctx.as_of, _MACRO_ROWS],
    ).fetchall()
    rows = [(_to_date(d), float(v)) for d, v in rows if v is not None and math.isfinite(v)]
    if not rows:
        return _candidate(source, rank, reason="no observation on or before as_of")
    values = apply_transform(sorted(rows), source.get("transform", "level"), frequency,
                             source.get("scale", 1.0))
    if not values:
        return _candidate(source, rank, reason=f"transform {source.get('transform')} has no prior period")
    stored, value = values[-1]
    return _candidate(source, rank, value=value,
                      observation_date=_observation_date(stored, frequency))


def _usable_cards(ctx, cards):
    usable = []
    for card in cards:
        resolution = card.get("resolution") or {}
        if resolution.get("value") is None or resolution.get("status") not in USABLE_RELEASE_STATUSES:
            continue
        if period_start_date(card["reference_period"]) > ctx.as_of:
            continue
        usable.append(card)
    return usable


def _release_candidate(ctx, source, rank, release_ids):
    cards, error = ctx.release_cards(release_ids)
    if error:
        return _candidate(source, rank, reason=error)
    usable = _usable_cards(ctx, cards.get(source["id"], []))
    if not usable:
        usable = _usable_cards(ctx, ctx.full_history_cards(source["id"]))
    if not usable:
        return _candidate(source, rank, reason="no official release known at as_known_at")
    card = max(usable, key=lambda c: (period_end_date(c["reference_period"]), c["released_at"]))
    consensus = None
    for row in card.get("consensus") or []:
        if row.get("normalized_value") is not None:
            consensus = {"provider": row.get("provider"), "value": row["normalized_value"]}
            break
    return _candidate(source, rank, value=float(card["resolution"]["value"]),
                      observation_date=period_end_date(card["reference_period"]),
                      reference_period=card["reference_period"], released_at=card["released_at"],
                      consensus=consensus)


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def _release_ids(ctx, names):
    ids = set()
    for name in names:
        spec = ctx.registry.get(name)
        if spec is None:
            continue
        for source in _active_sources(spec):
            if source["kind"] == "release":
                ids.add(source["id"])
        if spec.get("kind") == "derived":
            ids |= _release_ids(ctx, _derived_inputs(spec)[0])
    return ids


def _derived_inputs(spec):
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_:]*) ([+-]) ([A-Za-z][A-Za-z0-9_:]*)", spec["expression"])
    return (match[1], match[3]), match[2]


def _resolve(ctx, name, release_ids, as_of=None):
    key = (name, as_of or ctx.as_of)
    if key in ctx.cache:
        return ctx.cache[key]
    spec = ctx.registry.get(name)
    if spec is None:
        raise KeyError(f"unknown concept: {name}")
    kind = concept_kind(spec)
    if kind == "price":
        raise NotImplementedError(PRICE_NOT_IMPLEMENTED)
    saved = ctx.as_of
    ctx.as_of = as_of or saved
    try:
        if kind == "derived":
            result = _resolve_derived(ctx, name, spec, release_ids)
        else:
            result = _resolve_cascade(ctx, name, spec, release_ids)
    finally:
        ctx.as_of = saved
    ctx.cache[key] = result
    return result


def _resolve_cascade(ctx, name, spec, release_ids):
    result = _empty_result(name, spec, ctx.version)
    if spec.get("status") == "pending_source":
        result["reason"] = f"pending source: {spec.get('pending_reason')}"
        return result
    candidates = []
    for rank, source in enumerate(_active_sources(spec)):
        if source["kind"] == "macro":
            item = _macro_candidate(ctx, source, rank)
        elif source["kind"] == "release":
            item = _release_candidate(ctx, source, rank, release_ids)
        elif source["kind"] == "price":
            item = _candidate(source, rank, reason=PRICE_NOT_IMPLEMENTED)
        else:
            inner = _resolve(ctx, source["id"], release_ids)
            item = _candidate(source, rank, value=inner["value"],
                              observation_date=_to_date(inner["observation_date"]),
                              reason=None if inner["value"] is not None else inner["reason"])
        if item["observation_date"] is not None:
            item["age_bd"] = business_day_age(item["observation_date"], ctx.as_of)
            item["fresh"] = item["age_bd"] <= item["max_age_bd"]
            if not item["fresh"]:
                item["reason"] = f"stale ({item['age_bd']} > {item['max_age_bd']} business days)"
        candidates.append(item)
    chosen = None
    for roles in _TIERS:
        tier = [c for c in candidates if c["fresh"] and c["role"] in roles]
        if tier:
            newest = max(c["observation_date"] for c in tier)
            chosen = min((c for c in tier if c["observation_date"] == newest), key=lambda c: c["rank"])
            status = _ROLE_STATUS[chosen["role"]]
            break
    if chosen is None:
        observed = [c for c in candidates if c["observation_date"] is not None]
        if not observed:
            result["alternatives"] = [_alternative(c) for c in candidates]
            result["reason"] = "; ".join(f"{c['source_id']}: {c['reason']}" for c in candidates) \
                or "no active source"
            return result
        newest = max(c["observation_date"] for c in observed)
        chosen = min((c for c in observed if c["observation_date"] == newest), key=lambda c: c["rank"])
        status = "stale"
    result.update({key: chosen[key] for key in (
        "value", "observation_date", "reference_period", "source_id", "source_kind", "role",
        "age_bd", "max_age_bd", "released_at", "consensus")})
    result["status"] = status
    result["alternatives"] = [_alternative(c) for c in candidates if c is not chosen]
    result["reason"] = _reason(status, chosen, candidates)
    return result


def _alternative(candidate):
    return {"source_id": candidate["source_id"], "value": candidate["value"],
            "observation_date": candidate["observation_date"], "role": candidate["role"],
            "fresh": candidate["fresh"], "reason": candidate["reason"]}


def _reason(status, chosen, candidates):
    if status == "stale":
        return f"no fresh source; newest stale observation from {chosen['source_id']}"
    if status == "primary":
        return "primary source fresh with the newest observation"
    higher = [c for c in candidates if c["rank"] < chosen["rank"]]
    if status == "newer_secondary":
        primary = next((c for c in higher if c["role"] == "primary"), None)
        if primary is not None and primary["fresh"]:
            return f"secondary {chosen['source_id']} has a newer observation than the primary"
        return f"primary not fresh; secondary {chosen['source_id']} is fresh"
    return f"{status} {chosen['source_id']} used: no fresh source in a higher tier"


_STATUS_ORDER = {status: index for index, status in enumerate(STATUSES)}


def _resolve_derived(ctx, name, spec, release_ids):
    result = _empty_result(name, spec, ctx.version)
    (left, right), operator = _derived_inputs(spec)
    result.update(source_id=spec["expression"], source_kind="derived")
    resolved = []
    for input_name in (left, right):
        input_spec = ctx.registry[input_name]
        if concept_kind(input_spec) == "price":
            result["reason"] = f"input {input_name} is a price concept: {PRICE_NOT_IMPLEMENTED}"
            return result
        resolved.append(_resolve(ctx, input_name, release_ids))
    if any(item["value"] is None for item in resolved):
        result["inputs"] = [_input(item) for item in resolved]
        missing = [item["concept"] for item in resolved if item["value"] is None]
        result["reason"] = f"input unavailable: {', '.join(missing)}"
        return result
    dates = {_to_date(item["observation_date"]) for item in resolved}
    # same_date alignment: re-resolve both inputs at the older date until the
    # dates match or an input runs out (staggered gaps can need several steps).
    for _ in range(_ALIGN_STEPS):
        if len(dates) == 1:
            break
        common = min(dates)
        resolved = [_resolve(ctx, item["concept"], release_ids, as_of=common) for item in resolved]
        if any(item["value"] is None for item in resolved):
            break
        dates = {_to_date(item["observation_date"]) for item in resolved}
    if any(item["value"] is None for item in resolved) or len(dates) > 1:
        result["inputs"] = [_input(item) for item in resolved]
        result["reason"] = "inputs have no common observation date (same_date alignment)"
        return result
    observation = _to_date(resolved[0]["observation_date"])
    value = resolved[0]["value"] + (resolved[1]["value"] if operator == "+" else -resolved[1]["value"])
    value *= spec.get("scale", 1)
    age = business_day_age(observation, ctx.as_of)
    max_age = min(item["max_age_bd"] for item in resolved)
    statuses = [item["status"] for item in resolved]
    status = max(statuses, key=lambda s: _STATUS_ORDER[s])
    if age > max_age:
        status = "stale"
    result.update(value=value, observation_date=observation, age_bd=age, max_age_bd=max_age,
                  status=status, inputs=[_input(item) for item in resolved],
                  reason=f"{spec['expression']} on {observation.isoformat()}")
    return result


def _input(item):
    return {"concept": item["concept"], "value": item["value"],
            "observation_date": item["observation_date"], "source_id": item["source_id"],
            "status": item["status"]}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def read_latest(concept, *, as_of=None, as_known_at=None, con=None, db_path=None, releases_db=None):
    """Most recent correct value of one concept, with its declared source.

    ``con`` is an open DuckDB connection to market_data.duckdb (otherwise one is
    opened read-only from ``db_path``/the configured path). ``releases_db`` is
    the macro releases SQLite store (default: ``MACRO_RELEASES_DB``).
    """
    return read_latest_many([concept], as_of=as_of, as_known_at=as_known_at, con=con,
                            db_path=db_path, releases_db=releases_db)[0]


def read_latest_many(concepts, *, as_of=None, as_known_at=None, con=None, db_path=None,
                     releases_db=None):
    """Resolve several concepts with one connection and one release snapshot."""
    names = [concepts] if isinstance(concepts, str) else list(concepts)
    ctx = _Context(as_of, as_known_at, con, db_path, releases_db)
    try:
        for name in names:
            if name not in ctx.registry:
                raise KeyError(f"unknown concept: {name}")
            if concept_kind(ctx.registry[name]) == "price":
                raise NotImplementedError(PRICE_NOT_IMPLEMENTED)
        release_ids = _release_ids(ctx, names)
        return [dict(_resolve(ctx, name, release_ids)) for name in names]
    finally:
        ctx.close()


def read_concept_history(concept, start=None, end=None, *, as_of=None, con=None, db_path=None):
    """History from the concept's ``history`` declaration; sources are never spliced.

    ``mode: single`` reads that one long source (macro kind, with its declared
    transform and scale). Without a declaration the primary macro source is
    used. Returns a DataFrame with columns date, value, source_id; monthly and
    quarterly dates are reference period ends.
    """
    import pandas as pd

    registry = load_concepts()
    if concept not in registry:
        raise KeyError(f"unknown concept: {concept}")
    spec = registry[concept]
    kind = concept_kind(spec)
    if kind == "price":
        raise NotImplementedError(PRICE_NOT_IMPLEMENTED)
    history = spec.get("history")
    if history is not None and history["mode"] != "single":
        raise ValueError(f"{concept}: history mode {history['mode']} splices sources; "
                         "only mode single is supported")
    sources = spec.get("sources", [])
    if history is not None:
        source = next(s for s in sources if s["id"] == history["id"])
    else:
        source = next((s for s in sources if s["role"] == "primary"), None)
        if source is None or source["kind"] != "macro":
            raise ValueError(f"{concept}: no history declaration and no primary macro source")
    if source["kind"] != "macro":
        raise ValueError(f"{concept}: history source {source['id']} is not a macro series")
    frequency = load_macro_catalogue().get(source["id"], {}).get("frequency", "D")
    cutoff = _to_date(as_of) if as_of is not None else date.today()
    if end is not None:
        cutoff = min(cutoff, _to_date(end))
    own = con is None
    if own:
        from market_data_hub.reader import _resolved_connection

        con = _resolved_connection(db_path)
    try:
        rows = con.execute(
            "SELECT date, value FROM macro_series WHERE series_id = ? AND date <= ? "
            "AND value IS NOT NULL ORDER BY date", [source["id"], cutoff]).fetchall()
    finally:
        if own:
            con.close()
    rows = [(_to_date(d), float(v)) for d, v in rows if math.isfinite(v)]
    values = apply_transform(rows, source.get("transform", "level"), frequency, source.get("scale", 1.0))
    start_date = _to_date(start) if start is not None else None
    out = [{"date": _observation_date(d, frequency), "value": v, "source_id": source["id"]}
           for d, v in values]
    # Stored dates are period starts: bound the normalized period ends too.
    out = [row for row in out if row["date"] <= cutoff]
    if start_date is not None:
        out = [row for row in out if row["date"] >= start_date]
    return pd.DataFrame(out, columns=["date", "value", "source_id"])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _format_value(value):
    if value is None:
        return "-"
    return f"{value:,.4f}".rstrip("0").rstrip(".") if abs(value) < 1e6 else f"{value:,.0f}"


def format_table(results):
    header = ("concept", "value", "date/period", "source", "status", "age")
    rows = [header]
    for r in results:
        period = r["reference_period"] or (r["observation_date"].isoformat()
                                           if r["observation_date"] else "-")
        age = "-" if r["age_bd"] is None else f"{r['age_bd']}/{r['max_age_bd']}"
        rows.append((r["concept"], _format_value(r["value"]), period, r["source_id"] or "-",
                     r["status"], age))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(header))]
    return "\n".join("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)).rstrip()
                     for row in rows)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m market_data_hub.latest",
                                     description="Latest value per concept (macro, derived, release).")
    parser.add_argument("--as-of", required=True, help="YYYY-MM-DD observation cutoff")
    parser.add_argument("--as-known-at", help="release knowledge cutoff (default: end of as-of day)")
    parser.add_argument("--concept", action="append", dest="concepts", help="repeatable")
    parser.add_argument("--family", help="registry family, e.g. releases")
    parser.add_argument("--db", help="market_data.duckdb path (default: configured)")
    parser.add_argument("--releases-db", help="macro releases SQLite (default: MACRO_RELEASES_DB)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)
    registry = load_concepts()
    if args.concepts:
        names = args.concepts
    else:
        names = [name for name, spec in registry.items()
                 if (args.family is None or spec["family"] == args.family)
                 and concept_kind(spec) != "price"]
    try:
        results = read_latest_many(names, as_of=args.as_of, as_known_at=args.as_known_at,
                                   db_path=args.db, releases_db=args.releases_db)
    except (KeyError, NotImplementedError, FileNotFoundError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(format_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
