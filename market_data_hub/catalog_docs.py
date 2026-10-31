"""Generate the complete offline data catalogue from collector config and schemas.

Run ``python -m market_data_hub.catalog_docs --write`` after changing a source.
``--check`` is deterministic, opens only in-memory databases, and needs no keys.
``--live PATH [PATH ...]`` is an optional read-only JSON inventory; it never
updates the checked-in files. Use --write --output-dir DIR to save a live copy.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import duckdb
import yaml

PACKAGE = Path(__file__).resolve().parent
CONFIG_DIR = PACKAGE / "config"
DOC_DIR = PACKAGE.parent / "docs"
HUB_SCHEMA = PACKAGE / "db" / "schema.sql"
UNKNOWN = "not configured"

# Fallback deployment declarations, reviewed against investmentcommittee's
# config/marketdata_jobs.toml and weekly_earnings_jobs.toml on 2026-10-31.
# No sibling checkout or production scheduler is required to regenerate in CI.
JOBS = {
    "daily_eu18": "daily 09:00 PT",
    "daily_usclose": "daily 13:15 PT",
    "fred_morning": "daily 23:45 PT",
    "macro_releases_daily": "daily 06:30 PT",
    "cftc_cot": "daily 15:30 PT (new reports normally Friday)",
    "treasury_fiscal": "daily 15:45 PT",
    "econ_calendar": "retired/frozen since 2026-09-25; former daily 06:00 PT",
    "alfred_backfill": "manual/on demand; intended hour 22:00 PT, no automatic trigger",
    "weekly_earnings_watchlist": "Sunday 15:00 PT",
    "weekly_earnings_daily": "Monday-Friday 22:30 PT",
    "sec_facts": "on demand via financials service; no configured periodic schedule",
}

# Descriptions are table semantics, not another list of series. A new schema
# table must receive an explicit description; silently inventing one hides drift.
HUB_TABLES = {
    "etf_carry_snapshots": "Append-only ETF carry observations by capture date, symbol and field, with provider field and retrieval time.",
    "schema_meta": "Warehouse schema version and migration metadata.",
    "prices_daily": "Daily OHLCV and adjusted closes by listing; is_live marks provisional intraday prices.",
    "crypto_ohlcv": "Binance candles by symbol and timeframe; ts is the UTC candle opening time.",
    "macro_series": "Single-value observations: FRED, NY Fed ACM, official sovereign curves and TradingView snapshots. Series IDs keep provider conventions separate.",
    "sovereign_bond_metadata": "Selected Bund/BTP bond identity, maturity, coupon, reference price and yield for each daily series observation.",
    "sovereign_series_metadata": "Provider and curve conventions for sovereign series, stored as JSON.",
    "sovereign_snapshot_attempts": "TradingView capture attempts, including failures; an attempt is not a valid observation.",
    "sovereign_cross_checks": "Official versus market yield comparisons and tolerance flags; these are diagnostics, not interchangeable curves.",
    "cftc_tff_positioning": "CFTC Traders in Financial Futures, futures-only reports: dealer, asset manager, leveraged money and other positions for all returned contracts.",
    "cftc_legacy_positioning": "CFTC Legacy futures-only reports: commercial and non-commercial positioning for all returned contracts. Separate contract universe from TFF.",
    "macro_panel": "Cross-country annual, quarterly and monthly indicators; countries and provider/fallback choices come from the collector configs.",
    "treasury_cash_balance": "Daily Treasury Statement operating cash balances by account, including the TGA.",
    "treasury_debt_outstanding": "Debt to the Penny: public, intragovernmental and total debt outstanding.",
    "treasury_auctions": "Treasury auction results by record date and CUSIP, including yields, tendered/accepted amounts and bid-to-cover.",
    "download_log": "Operational download run outcomes, timings, row counts and errors; not an external dataset.",
    "coverage_report": "Recomputed per-symbol coverage and freshness diagnostics.",
    "macro_panel_coverage": "Recomputed per-indicator country coverage, source and freshness diagnostics.",
    "factor_returns": "Ken French factor returns in decimal units, keeping factor sets distinct (including the risk-free return RF).",
    "custom_series": "Caller-published custom or derived scalar series; dynamic IDs, no external collector universe.",
    "macro_series_vintage": "Hub-observed changes to macro_series, with ingestion-date vintages.",
    "alfred_vintage_observations": "Vendor point-in-time FRED/ALFRED observations; as_of is the vendor availability date, not the hub ingestion date.",
    "alfred_backfill_progress": "Resumable ALFRED checkpoint per series and requested since-vintage floor.",
    "alfred_backfill_pending": "Incomplete ALFRED range and pending vintage-page state, kept separately from completed progress.",
    "macro_panel_vintage": "Hub-observed changes to cross-country panel observations, with ingestion-date vintages.",
    "issuers": "Issuer identity registry and SEC CIK mapping; company universe is populated on demand.",
    "instruments": "Stable instrument identities and issuer links, independent of listing symbols.",
    "listings": "Exchange listings linking symbols to stable instruments; daily prices use listing_id.",
    "etf_classification": "Config-derived ETF asset class and geographical classification.",
    "identifier_aliases": "External identifier aliases with validity intervals and identity targets.",
    "ingestion_runs": "Service ingestion request provenance and outcomes.",
    "ingestion_jobs": "Queued/idempotent service ingestion requests and execution status.",
    "sec_filings": "SEC submission metadata by CIK and accession: filing form, filing date and report period; no per-company listing in this catalogue.",
    "sec_company_facts": "SEC XBRL facts with taxonomy, concept, unit, period and filing accession; revised filings remain distinct facts.",
    "sec_coverage": "Recomputed SEC filing and fact coverage by CIK.",
    "calendar_indicators": "Retired economic-calendar indicator definitions and matching metadata; retained for historical reads.",
    "calendar_events": "Retired economic-calendar scheduled release events, keyed by indicator and release time.",
    "calendar_observations": "Retired economic-calendar actual, consensus and prior readings by provider and hub vintage date.",
    "calendar_indicator_aliases": "Retired economic-calendar provider name aliases and matching provenance.",
    "calendar_event_notes": "Retired generated economic-event enrichment notes and review metadata.",
    "earnings_events": "Earnings release calendar events by exchange, symbol and release day, including timing and market metadata; dynamic company universe.",
    "earnings_observations": "TradingView earnings actuals, estimates and prior values captured per provider and hub vintage date; dynamic company universe.",
}
MACRO_TABLES = {
    "indicators": "Exact-measure catalogue: issuer, geography, transformation, unit and activation state.",
    "source_series": "Source-specific measure identities, issuer/distributor roles and declared expected_lag_seconds.",
    "release_events": "Issuer publication editions with family and estimate stage; a scheduled event alone does not certify publication.",
    "schedule_versions": "Successive recorded versions of expected issuer release instants and their time precision.",
    "value_vintages": "Published numeric measure vintages, preserving reference period, issuer publication time, first-seen time and document provenance.",
    "readings": "Raw issuer, mirror, web, derived and unavailable evidence; not every reading is an official actual.",
    "resolution_versions": "Append-only evidence selection decisions, with knowledge cutoff and rule version.",
    "consensus_readings": "Captured provider consensus estimates linked to issuer editions; survey and capture time stay separate from publication.",
    "claims": "Verifiable release claims attached to events with evidence references.",
    "claim_evidence": "Verbatim claim evidence with document hashes and provenance.",
    "discrepancies": "Differences between raw readings and selected official vintages.",
    "legacy_candidates": "Reviewed mappings of legacy source codes to exact macro measures and stages.",
    "indicator_aliases": "Reviewed provider aliases, geography and legacy-code mappings.",
    "import_worklist": "Pending, complete or terminal import occurrences with explicit reasons and retry times.",
    "legacy_reading_links": "Provenance links from imported evidence to original legacy records.",
    "collect_runs": "Append-only official collection start and terminal outcomes.",
    "collect_run_families": "Per-family collection outcomes and fetched windows, including needs-review failures.",
    "documents": "Archived issuer HTML/PDF/API document bytes, raw and normalized hashes, URLs and retrieval times.",
    "review_queue": "Append-only family/document review requests; review flags are not official actuals.",
    "schema_compatibility": "Schema versions and compatible reader bounds.",
    "consensus_snapshots": "Imported consensus snapshot identities and processing results; raw snapshot files are inputs.",
    "collect_run_consensus": "Consensus ingestion outcomes, separate from official family collection status.",
    "consensus_link_versions": "Append-only corrections retiring a consensus-to-issuer association without rewriting raw estimates.",
    "boj_publication_times": "Recorded BoJ publication-time evidence and precision corrections.",
    "worklist_resolutions": "Optional append-only worklist resolver decisions and prior-state evidence.",
    "worklist_reverts": "Optional append-only reverts of worklist resolution runs.",
}


def load_config(config_dir, name):
    return yaml.safe_load((Path(config_dir) / name).read_text(encoding="utf-8"))


def code_assignment(path, name):
    """Read a collector's declaration without importing or executing its runner."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    return next(node.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets))


def schema_tables():
    """Execute the real DDL/migrations in memory; obtain keys from the engines."""
    from .macro_releases.migrations import migrate

    result = []
    with closing(duckdb.connect(":memory:")) as conn:
        conn.execute(HUB_SCHEMA.read_text(encoding="utf-8"))
        names = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()]
        for name in names:
            columns = conn.execute(f"PRAGMA table_info('{name}')").fetchall()
            result.append(dict(store="hub", table=name,
                               primary_key=[r[1] for r in columns if r[5]]))
    with closing(sqlite3.connect(":memory:")) as conn:
        migrate(conn)
        conn.executescript((PACKAGE / "macro_releases" / "worklist_audit.sql").read_text(encoding="utf-8"))
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            columns = conn.execute(f"PRAGMA table_info('{name}')").fetchall()
            result.append(dict(store="macro_releases.sqlite", table=name,
                               primary_key=[r[1] for r in sorted(columns, key=lambda r: r[5]) if r[5]]))
    return result


def _status(entry):
    for key, status in (("discontinued", "discontinued"), ("retired", "retired")):
        if entry.get(key):
            return status, str(entry[key])
    if entry.get("enabled") is False or entry.get("active") is False:
        return "disabled", entry.get("disabled_reason") or entry.get("unavailable_reason") or "disabled in config"
    if entry.get("role") == "history":
        return "history-only", entry.get("history_reason", "history role in config")
    return "active", ""


def _job(entry, default):
    names = entry.get("job", entry.get("job_name", default))
    if isinstance(names, str):
        names = [names]
    return [dict(name=n, schedule_pt=entry.get("schedule_pt") or JOBS.get(n, UNKNOWN)) for n in names]


def _row(entry, *, id, table, distributor, job, history=UNKNOWN, store="hub", frequency=None, issuer=None):
    lag = entry.get("expected_lag_days")
    if not isinstance(lag, (int, float)) or isinstance(lag, bool) or lag < 0:
        raise ValueError(f"{table}/{id}: missing or invalid declared expected_lag_days")
    status, reason = _status(entry)
    return dict(id=id, name=entry.get("name", id), table=table, store=store,
                issuer=entry.get("issuer") or issuer or entry.get("source") or UNKNOWN,
                distributor=distributor, frequency=entry.get("frequency") or entry.get("freq") or frequency or UNKNOWN,
                expected_lag_days=lag, lag_basis="declared; 2026-10-31 inventory estimate",
                history_start=str(entry.get("backfill_start") or entry.get("history_start") or history),
                jobs=_job(entry, job), status=status, status_reason=reason,
                notes=entry.get("notes", ""))


def build_rows(config_dir=CONFIG_DIR):
    from .macro_releases.catalogue import EXPECTED_LAG_DAYS, FAMILIES, INDICATORS
    from .macro_releases.read_api import FAMILY_INDICATORS
    from .sources.factors import CATALOG

    settings = load_config(config_dir, "settings.yaml")
    floors = settings["backfill_start"]
    alfred_floor = ast.literal_eval(code_assignment(PACKAGE / "alfred_backfill.py", "EARLIEST"))
    macro_lookback = ast.literal_eval(code_assignment(PACKAGE / "macro_releases" / "run_daily.py", "DEFAULT_LOOKBACK_MONTHS"))
    macro = load_config(config_dir, "macro_series.yaml")
    rows = []
    # Match get_yahoo_tickers: FRED takes precedence over duplicate Yahoo IDs.
    fred_ids = {e["symbol"] for e in macro["fred"]}
    for e in load_config(config_dir, "tickers.yaml")["yahoo"]:
        if e["symbol"] not in fred_ids:
            rows.append(_row(e, id=e["symbol"], table="prices_daily", distributor="Yahoo Finance",
                             issuer="Market venues / benchmark publishers (not declared per symbol)",
                             job=["daily_eu18", "daily_usclose"], history=floors["yahoo"], frequency="D"))
    from .etf_carry import FIELDS, universe
    for e in universe(load_config(config_dir, "tickers.yaml")["yahoo"]):
        for field, lag in FIELDS.items():
            row = _row(dict(name=f"{e['symbol']} {field}", expected_lag_days=lag,
                            notes="Point-in-time from first capture; no backfill. Missing values are absent."),
                       id=f"{e['symbol']}/{field}", table="etf_carry_snapshots",
                       distributor="Yahoo Finance quote and quoteSummary", issuer="Fund provider via Yahoo",
                       job=["daily_eu18", "daily_usclose"], history="Point-in-time from first capture; no backfill", frequency="D")
            rows.append(row)
    for group, distributor in (("fred", "FRED"), ("nyfed_acm", "NY Fed direct")):
        for e in macro[group]:
            row = _row(e, id=e["symbol"], table="macro_series", distributor=distributor,
                       job=["daily_eu18", "daily_usclose", "fred_morning"] if group == "fred" else ["daily_eu18", "daily_usclose"],
                       history=floors[group])
            row["stalled_after_days"] = e.get("max_lag_days")
            rows.append(row)
    for config in ("sovereign_yields.yaml", "sovereign_layers.yaml"):
        for e in load_config(config_dir, config)["series"]:
            rows.append(_row(e, id=e["symbol"], table="macro_series",
                             distributor="TradingView" if e["provider"] == "tradingview" else e["source"] + " direct",
                             job=["daily_eu18", "daily_usclose"], history=floors["sovereign_yields"]))
    crypto = settings["crypto"]
    for symbol in crypto["symbols"]:
        for timeframe in crypto["timeframes"]:
            rows.append(_row(crypto, id=f"{symbol}/{timeframe}", table="crypto_ohlcv", distributor="Binance direct",
                             issuer="Binance", frequency=timeframe, job=["daily_eu18", "daily_usclose"], history=floors["binance"]))
    for e in load_config(config_dir, "macro_panel.yaml")["indicators"]:
        row = _row(e, id=e["id"], table="macro_panel", distributor=e["source"],
                   job=["daily_eu18", "daily_usclose"], history=floors["macro_panel"],
                   issuer={"WB": "World Bank (upstream national/statistical agencies vary)",
                           "BIS": "Bank for International Settlements", "IMF": "International Monetary Fund",
                           "ECB": "European Central Bank"}.get(e["source"], e["source"]))
        row["provider_code"] = e["code"]
        row["fallback"] = e.get("fallback")
        rows.append(row)
    for dataset in settings["factors"]["datasets"]:
        spec = CATALOG[dataset]
        for factor in spec["factors"].split():
            e = dict(settings["factors"], name=f"{dataset}: {factor}")
            rows.append(_row(e, id=f"{dataset}/{factor}", table="factor_returns", distributor="Ken French Data Library direct",
                             issuer="Kenneth R. French / Dartmouth", frequency=spec["frequency"],
                             job=["daily_eu18", "daily_usclose"], history=floors["factors"]))
    for e in load_config(config_dir, "alfred_backfill.yaml")["series"]:
        from .indicator_cards import indicator_card
        card = indicator_card("ms:" + e["series_id"])
        e = dict(e, name=e.get("name") or (card.concept if card else e["reason"]))
        row = _row(e, id=e["series_id"], table="alfred_vintage_observations", distributor="ALFRED via FRED API",
                   job="alfred_backfill", history=alfred_floor)
        row["notes"] = e.get("reason", "") + " Default observation/vintage floor from alfred_backfill.EARLIEST; --since-vintage can override vintage floor."
        rows.append(row)
    for e in load_config(config_dir, "econ_calendar.yaml")["indicators"]:
        # The old catalogue is still authoritative for frozen indicator IDs.
        rows.append(_row(dict(e, retired="Collection disabled since 2026-09-25; retained/frozen historical tables"),
                         id=e["key"], table="calendar_indicators", distributor="Legacy calendar aggregators",
                         issuer=e.get("agency") or UNKNOWN, job="econ_calendar"))
    if set(FAMILIES) != set(EXPECTED_LAG_DAYS):
        raise ValueError("macro release families missing declared lag metadata")
    for id, indicator in sorted(INDICATORS.items()):
        families = sorted(f for f, ids in FAMILY_INDICATORS.items() if id in ids)
        if not families:
            raise ValueError(f"Macro indicator {id} has no collector family")
        lag = max(EXPECTED_LAG_DAYS[f] for f in families)
        e = dict(name=indicator.definition, expected_lag_days=lag, frequency=indicator.frequency,
                 active=indicator.active, issuer=indicator.issuer_id)
        if indicator.geography == "EA_PENDING":
            e.update(active=False, disabled_reason="Future euro-area composition placeholder; not a certified published series")
        row = _row(e, id=id, table="value_vintages", store="macro_releases.sqlite",
                   distributor=indicator.issuer_id + " direct; optional FRED/ALFRED mirror where mapped",
                   job="macro_releases_daily", history=f"CLI --since; scheduled rolling {macro_lookback}-month lookback, no fixed floor")
        row["families"] = families
        from .macro_releases.fred import MAPPINGS
        row["distributor"] = indicator.issuer_id + " direct" + ("; FRED/ALFRED mirror" if id in MAPPINGS else "")
        rows.append(row)
    # Dataset universes come from the runners' actual routing dictionaries.
    # These wide tables have dynamic contract/account/security universes.
    for filename, variable, config_key, publisher, frequency, job in (
        ("run_cftc_cot.py", "REPORTS", "cftc_cot", "CFTC", "W", "cftc_cot"),
        ("run_treasury_fiscal.py", "DATASETS", "treasury_fiscal", "US Treasury", "D / auction events", "treasury_fiscal"),
    ):
        routing = code_assignment(PACKAGE.parent / filename, variable)
        for key, value in zip(routing.keys, routing.values):
            id, table = ast.literal_eval(key), ast.literal_eval(value.elts[1])
            e = dict(settings["collection_metadata"][config_key], name=HUB_TABLES[table])
            rows.append(_row(e, id=id, table=table, distributor=publisher + " direct",
                             issuer=publisher, frequency=frequency, job=job,
                             history="CLI --start / rolling lookback; no fixed configured floor"))
    return sorted(rows, key=lambda r: (r["store"], r["table"], r["id"]))


def _table_details(store, name):
    if store != "hub":
        doc = "macro-releases.md"
        pit = ("Use the macro ReadAPI with news_cutoff/as_known_at and first-seen/recorded-at filters before selection. "
               "Published time alone does not prove the hub knew a value. Raw readings and mirror evidence are not automatically official. "
               "Append-only vintages, schedules, consensus and resolution history preserve evidence; registry/worklist tables describe current state.")
        return MACRO_TABLES[name], doc, pit
    doc = "ARCHITECTURE.md"
    pit = "Current state/upserted data; no historical as-known-at reconstruction from this table alone."
    if name == "etf_carry_snapshots":
        pit = "Point-in-time from first capture; no backfill. Filter retrieved_at by cutoff; missing fields have no row. Same-day first capture is immutable."
    elif name.startswith("calendar_"):
        doc = "macro-indicators.md"
        pit = "Frozen since 2026-09-25. Observations retain provider + vintage_date; filter vintage_date <= cutoff before choosing the latest per event/provider. Event metadata and aliases are current state."
    elif name.startswith("alfred_"):
        doc = "alfred-backfill.md"
        pit = "For observations, filter as_of <= cutoff and choose the latest as_of per series/date (respect vendor realtime_end when present). Progress/pending tables are mutable checkpoints, not vintages. Vendor knowledge is distinct from hub first-seen knowledge."
    elif name.startswith("sovereign_"):
        doc = "sovereign-layers.md"
    elif name.startswith("cftc_"):
        doc = "cftc-history.md"
    elif name.startswith("earnings_"):
        doc = "EXTRACTION.md"
        pit = "Observations preserve source + vintage_date; filter vintage_date <= cutoff before choosing a vintage. Events are upserted schedule metadata; no per-company static catalogue."
    elif name.startswith("sec_"):
        doc = "EXTRACTION.md"
        pit = "Facts retain filing accession and filed date; select filings available by the cutoff before period/concept selection. This is filing availability, not proof of historical hub ingestion; coverage is current state."
    elif name.startswith("macro_panel"):
        doc = "EXTRACTION.md"
        pit = "macro_panel is latest state. macro_panel_vintage keeps hub ingestion-date changes: filter vintage_date <= cutoff, then latest per date/country/indicator. Backfills do not certify original issuer availability."
    elif name.startswith("macro_series"):
        doc = "free-sources.md"
        pit = "macro_series is latest state. macro_series_vintage keeps hub ingestion-date changes: filter vintage_date <= cutoff then latest per series/date. Backfilled ingestion vintages are not issuer publication history; use ALFRED for vendor point-in-time data."
    elif name == "factor_returns":
        doc = "EXTRACTION.md"
        pit += " French files can revise historical returns; no archived factor vintages."
    return HUB_TABLES[name], doc, pit


def build_catalog(config_dir=CONFIG_DIR):
    rows = build_rows(config_dir)
    tables = schema_tables()
    identities = [(r["store"], r["table"], r["id"]) for r in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate catalogue series identity")
    known = {(t["store"], t["table"]) for t in tables}
    if any((r["store"], r["table"]) not in known for r in rows):
        raise ValueError("Catalogue row refers to a table missing from the schema")
    for table in tables:
        store, name = table["store"], table["table"]
        try:
            description, doc, pit = _table_details(store, name)
        except KeyError:
            raise ValueError(f"Undocumented schema table: {store}/{name}") from None
        table.update(description=description, doc=doc, point_in_time=pit,
                     status="retired" if store == "hub" and name.startswith("calendar_") else "active",
                     status_reason="Frozen since 2026-09-25; collection job disabled" if name.startswith("calendar_") else "",
                     series_count=sum(r["store"] == store and r["table"] == name for r in rows))
        jobs = {j["name"]: j for r in rows if r["store"] == store and r["table"] == name for j in r["jobs"]}
        if name.startswith("earnings_"):
            jobs = {j["name"]: j for j in _job({}, ["weekly_earnings_watchlist", "weekly_earnings_daily"])}
        elif name.startswith("sec_"):
            jobs = {j["name"]: j for j in _job({}, "sec_facts")}
        elif name.startswith("calendar_"):
            jobs = {j["name"]: j for j in _job({}, "econ_calendar")}
        elif store == "macro_releases.sqlite":
            jobs = {j["name"]: j for j in _job({}, "macro_releases_daily")}
        table["jobs"] = [jobs[j] for j in sorted(jobs)]
    return dict(format_version=1, lag_basis="declared estimates initialized 2026-10-31; not live-measured",
                stores={"hub": "DuckDB at MARKET_DATA_DB / explicit configured path", "macro_releases.sqlite": "Separate SQLite macro release store"},
                tables=tables, series=rows)


def _cell(value):
    return " ".join(str(value if value is not None else "not available").split()).replace("|", "\\|")


def _anchor(table):
    return "catalog-" + table["store"].replace(".", "-") + "-" + table["table"]


def render_markdown(catalog):
    out = ["# Generated data catalogue", "",
           "<!-- Generated by python -m market_data_hub.catalog_docs --write; do not edit. -->", "",
           "Structured external data is collected here; news and web text belong to LazyCrawler, and applications never collect. This inventory is generated from the collector configs, "
           "macro release catalogue and the actual DuckDB/SQLite schemas (including migrations and optional worklist audit tables).", "",
           "Regenerate with `python -m market_data_hub.catalog_docs --write`; CI runs `--check` against this page and "
           "[the machine-readable catalogue](data-catalog.json). Adding a source requires its collector configuration and regeneration.", "",
           "**Expected lag** is declared calendar days from reference-period end to availability in the hub, initialized from the "
           "31 October 2026 inventory. It is an estimate, not live-measured, and differs from a stalled-after age threshold. "
           "For weekly claims use survey week end; for candles use candle close; for event data use the event day. "
           "Zero permits same-day or before-period-end flash/survey releases. Payrolls use 7 days (usual range 2–7); "
           "OECD long rates use 60 (usual range 30–90). Monthly/quarterly dates can label period starts: do not subtract them directly to measure lag.", "",
           "Schedules below name investmentcommittee jobs in America/Los_Angeles (PT, including DST). Fallback declarations were reviewed "
           "on 2026-10-31; per-entry job/schedule config overrides them. Schedules describe intended runs, not successful collection. "
           "`not configured` means the source declares no fixed floor or metadata, not that history is absent. Dynamic SEC/earnings company "
           "and CFTC contract universes are documented at table level. Multiple configured crypto timeframes and factor components have separate rows.", "",
           "Prices use the collector's FRED-over-Yahoo precedence. Panel rows cover each indicator across configured countries; provider fallback "
           "metadata is in JSON. The macro store currently includes composition placeholders, explicitly disabled below.", "",
           "Optional live inventory: `python -m market_data_hub.catalog_docs --live PATH_TO_DUCKDB PATH_TO_SQLITE` prints JSON read-only. "
           "Use `--write --output-dir DIR` to save an isolated live copy. Observations do not alter the declared metadata. "
           "No live database or credentials are used by generation or CI.", "",
           "Topic guides: [Macro indicators](macro-indicators.md), [Macro releases](macro-releases.md), "
           "[Sovereign yields](f08-sovereign-yields.md), [Sovereign layers](sovereign-layers.md), "
           "[CFTC history](cftc-history.md), [ALFRED](alfred-backfill.md), [FRED FX](fred-fx.md), "
           "[Free sources / ACM](free-sources.md).", "",
           "## Counts per table", "", "| Store | Table | Series / indicators |", "|---|---|---:|"]
    for table in catalog["tables"]:
        out.append(f"| {table['store']} | [{table['table']}](#{_anchor(table)}) | {table['series_count']} |")
    out += ["", "Zero means a dynamic universe, registry, audit or supporting table, whose purpose and key are documented below. "
            "Vintages and metadata refer to the parent series universe; they are not duplicated as new series.", ""]
    for table in catalog["tables"]:
        store, name = table["store"], table["table"]
        out += [f'<a id="{_anchor(table)}"></a>', "", f"## {store} / {name}", "", table["description"], "",
                f"- Store: `{store}`; table: `{name}`.",
                "- Primary key: " + (", ".join(f"`{key}`" for key in table["primary_key"]) or "none declared (append-only operational log)") + ".",
                f"- Status: {table['status']}" + (f" — {table['status_reason']}" if table["status_reason"] else "") + ".",
                f"- Point-in-time / vintages: {table['point_in_time']}",
                "- Collection: " + ("; ".join(f"{j['name']} {j['schedule_pt']}" for j in table["jobs"]) or "hub pipeline / services; supporting state, no independent external collection schedule") + ".",
                f"- Guide: [{table['doc']}]({table['doc']}).", ""]
        rows = [r for r in catalog["series"] if r["store"] == store and r["table"] == name]
        if not rows:
            continue
        live = "live_sources" in catalog
        live_header = " Observed first | Observed last | Observed lag (days) / basis |" if live else ""
        out += ["| ID | Name | Issuer | Distributor | Frequency | Expected lag (days, declared) | History floor | Job / schedule (PT) | Status / reason |" + live_header,
                "|---|---|---|---|---|---:|---|---|---|" + ("---|---|---|" if live else "")]
        for row in rows:
            jobs = "; ".join(f"{j['name']} {j['schedule_pt']}" for j in row["jobs"])
            status = row["status"] + (": " + row["status_reason"] if row["status_reason"] else "")
            cells = [
                row["id"], row["name"], row["issuer"], row["distributor"], row["frequency"],
                row["expected_lag_days"], row["history_start"], jobs, status]
            if live:
                observed = row.get("observed", {})
                cells += [observed.get("first_date"), observed.get("last_date"),
                          f"{observed.get('lag_days')} / {observed.get('lag_basis', 'not observed')}"]
            out.append("| " + " | ".join(_cell(v) for v in cells) + " |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def artifacts(catalog):
    return {"DATA_CATALOG.md": render_markdown(catalog),
            "data-catalog.json": json.dumps(catalog, indent=2, ensure_ascii=False, sort_keys=True) + "\n"}


def check(catalog, output_dir=DOC_DIR):
    stale = [name for name, text in artifacts(catalog).items()
             if not (Path(output_dir) / name).exists() or (Path(output_dir) / name).read_text(encoding="utf-8") != text]
    if stale:
        raise SystemExit("Data catalogue is stale (" + ", ".join(stale) + "); run python -m market_data_hub.catalog_docs --write")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--output-dir", type=Path, default=DOC_DIR)
    parser.add_argument("--live", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.live and (args.check or args.write and args.output_dir.resolve() == DOC_DIR.resolve()):
        parser.error("Live observations cannot replace/check canonical files; use --write --output-dir DIR")
    catalog = build_catalog(args.config_dir)
    if args.live:
        from .catalog_live import observe
        observe(catalog, args.live)
    if args.check:
        check(catalog, args.output_dir)
        print("Data catalogue is up to date")
    elif args.write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, text in artifacts(catalog).items():
            with (args.output_dir / name).open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        print(f"Generated {len(catalog['series'])} series across {len(catalog['tables'])} tables")
    else:
        print(json.dumps(catalog, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
