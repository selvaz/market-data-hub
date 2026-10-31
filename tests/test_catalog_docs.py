"""Offline completeness, freshness and optional read-only catalogue inventory."""
from copy import deepcopy
from hashlib import sha256
import shutil
import sqlite3

import duckdb
import pytest
import yaml

from market_data_hub import catalog_docs as docs
from market_data_hub.catalog_live import observe, period_end
from market_data_hub.macro_releases.catalogue import EXPECTED_LAG_DAYS, FAMILIES, INDICATORS
from market_data_hub.sources.factors import CATALOG


@pytest.fixture(scope="module")
def catalog():
    return docs.build_catalog()


def save(catalog, directory):
    for name, content in docs.artifacts(catalog).items():
        (directory / name).write_text(content, encoding="utf-8")


def test_checked_in_files_are_fresh_and_deterministic(catalog):
    docs.main(["--check"])
    assert docs.artifacts(catalog) == docs.artifacts(docs.build_catalog())


def test_all_configured_universes_are_present(catalog):
    actual = {}
    for row in catalog["series"]:
        actual.setdefault((row["store"], row["table"]), set()).add(row["id"])
    def config(name):
        return docs.load_config(docs.CONFIG_DIR, name + ".yaml")
    macro = config("macro_series")
    macro_ids = {e["symbol"] for group in macro.values() for e in group}
    for name in ("sovereign_yields", "sovereign_layers"):
        macro_ids.update(e["symbol"] for e in config(name)["series"])
    assert actual["hub", "macro_series"] == macro_ids
    fred = {e["symbol"] for e in macro["fred"]}
    assert actual["hub", "prices_daily"] == {e["symbol"] for e in config("tickers")["yahoo"]} - fred
    assert actual["hub", "macro_panel"] == {e["id"] for e in config("macro_panel")["indicators"]}
    assert actual["hub", "alfred_vintage_observations"] == {e["series_id"] for e in config("alfred_backfill")["series"]}
    assert actual["hub", "calendar_indicators"] == {e["key"] for e in config("econ_calendar")["indicators"]}
    assert actual["macro_releases.sqlite", "value_vintages"] == set(INDICATORS)
    settings = config("settings")
    assert actual["hub", "crypto_ohlcv"] == {f"{s}/{t}" for s in settings["crypto"]["symbols"] for t in settings["crypto"]["timeframes"]}
    assert actual["hub", "factor_returns"] == {f"{d}/{f}" for d in settings["factors"]["datasets"] for f in CATALOG[d]["factors"].split()}
    assert set(EXPECTED_LAG_DAYS) == set(FAMILIES)


def test_every_effective_schema_table_has_a_section(catalog):
    assert {(t["store"], t["table"]) for t in catalog["tables"]} == {
        (t["store"], t["table"]) for t in docs.schema_tables()}
    markdown = docs.render_markdown(catalog)
    for table in catalog["tables"]:
        assert f"## {table['store']} / {table['table']}\n" in markdown
        assert table["description"] and table["point_in_time"]
        assert (docs.DOC_DIR / table["doc"]).is_file()
    by_name = {t["table"]: t for t in catalog["tables"]}
    assert by_name["prices_daily"]["primary_key"] == ["date", "listing_id"]
    assert by_name["collect_runs"]["primary_key"] == ["run_id", "status"]
    assert by_name["download_log"]["primary_key"] == []
    assert "documents" in by_name and "worklist_resolutions" in by_name


@pytest.mark.parametrize("name,group,key", [
    ("tickers", "yahoo", "symbol"), ("macro_series", "fred", "symbol"),
    ("macro_series", "nyfed_acm", "symbol"), ("macro_panel", "indicators", "id"),
    ("alfred_backfill", "series", "series_id"), ("sovereign_yields", "series", "symbol"),
    ("sovereign_layers", "series", "symbol"), ("econ_calendar", "indicators", "key"),
])
def test_new_fixture_series_breaks_gate_until_regenerated(tmp_path, catalog, name, group, key):
    config_dir = tmp_path / "config"
    shutil.copytree(docs.CONFIG_DIR, config_dir)
    output = tmp_path / "docs"
    output.mkdir()
    save(catalog, output)
    docs.main(["--check", "--config-dir", str(config_dir), "--output-dir", str(output)])
    path = config_dir / (name + ".yaml")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config[group].append(dict(config[group][0], **{key: "FIXTURE_UNDOCUMENTED", "name": "Fixture source"}))
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(SystemExit, match="stale"):
        docs.main(["--check", "--config-dir", str(config_dir), "--output-dir", str(output)])
    docs.main(["--write", "--config-dir", str(config_dir), "--output-dir", str(output)])
    docs.main(["--check", "--config-dir", str(config_dir), "--output-dir", str(output)])
    assert "FIXTURE_UNDOCUMENTED" in (output / "DATA_CATALOG.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", ["DATA_CATALOG.md", "data-catalog.json"])
def test_either_artifact_stale_or_missing_fails(tmp_path, catalog, filename):
    save(catalog, tmp_path)
    path = tmp_path / filename
    path.write_text("stale", encoding="utf-8")
    with pytest.raises(SystemExit, match="stale"):
        docs.check(catalog, tmp_path)
    path.unlink()
    with pytest.raises(SystemExit, match="stale"):
        docs.check(catalog, tmp_path)
    save(catalog, tmp_path)
    docs.check(catalog, tmp_path)


def test_new_schema_table_requires_description(tmp_path, monkeypatch):
    schema = tmp_path / "schema.sql"
    schema.write_text(docs.HUB_SCHEMA.read_text(encoding="utf-8") +
                      "\nCREATE TABLE new_raw_dataset (id VARCHAR PRIMARY KEY);\n", encoding="utf-8")
    monkeypatch.setattr(docs, "HUB_SCHEMA", schema)
    with pytest.raises(ValueError, match="Undocumented schema table: hub/new_raw_dataset"):
        docs.build_catalog()


def test_missing_lag_is_rejected():
    with pytest.raises(ValueError, match="expected_lag_days"):
        docs._row({}, id="new", table="macro_series", distributor="direct", job="daily_eu18")


def test_metadata_statuses_and_lag_semantics(catalog):
    rows = {r["id"]: r for r in catalog["series"]}
    assert rows["CPIAUCSL"]["expected_lag_days"] == 14
    assert rows["DEXJPUS"]["expected_lag_days"] == 7
    assert rows["DEXJPUS"]["stalled_after_days"] == 10
    assert rows["NYFED_ACM_TP10_MONTHLY"]["expected_lag_days"] == 30
    assert rows["CB_IT_10Y"]["status"] == "history-only"
    assert rows["CB_DE_10Y"]["status"] == "disabled"
    assert rows["natural_resource_rents_gdp"]["status"] == "discontinued"
    for r in catalog["series"]:
        assert r["frequency"] != docs.UNKNOWN
        assert r["issuer"] and r["distributor"] and r["history_start"]
        assert r["expected_lag_days"] >= 0 and r["lag_basis"].startswith("declared")
        assert all(j["schedule_pt"] != docs.UNKNOWN for j in r["jobs"])
    for t in catalog["tables"]:
        if t["table"].startswith("calendar_"):
            assert t["status"] == "retired" and "2026-09-25" in t["status_reason"]


def test_job_config_overrides_static_schedule():
    assert docs._job({"job_name": "custom_job", "schedule_pt": "daily 12:34 PT"}, "daily_eu18") == [
        {"name": "custom_job", "schedule_pt": "daily 12:34 PT"}]


def test_source_lag_declaration_preserves_existing_immutable_metadata():
    from market_data_hub.macro_releases.adapters import source_lag_seconds
    from types import SimpleNamespace
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE source_series (id TEXT PRIMARY KEY, expected_lag_seconds INTEGER)")
        conn.executemany("INSERT INTO source_series VALUES (?,?)", [("legacy", None), ("declared", 123)])
        store = SimpleNamespace(conn=conn)
        assert source_lag_seconds(store, "legacy", "us_cpi") is None
        assert source_lag_seconds(store, "declared", "us_cpi") == 123
        assert source_lag_seconds(store, "new", "us_cpi") == 14 * 86400
    finally:
        conn.close()


def test_collection_persists_declared_lag_and_rereads_legacy_sources(tmp_path):
    from market_data_hub.macro_releases.adapters import Observation, persist
    from market_data_hub.macro_releases.http import Document
    from market_data_hub.macro_releases.models import SourceSeries
    from market_data_hub.macro_releases.scheduled_events import persist_scheduled_events
    from market_data_hub.macro_releases.store import open_writer

    indicator = INDICATORS["us_cpi_headline_yy"]
    issuer = indicator.issuer_id
    with open_writer(tmp_path / "collection.sqlite") as store:
        store.put(indicator)
        store.put(SourceSeries(id=f"{issuer}:{indicator.id}:fixture:published", indicator_id=indicator.id,
                               source_id=issuer, issuer_id=issuer, distributor_id=issuer,
                               source_key="fixture", evidence_role="issuer"))
        body = b"fixture CPI 3.0"
        observation = Observation(indicator.id, "us_cpi", "2026-09", 3.0, "3.0", "fixture", issuer,
                                  Document(body, "https://issuer.example/cpi", "2026-10-14T13:00:00Z", sha256(body).hexdigest()),
                                  "2026-10-14", "2026-10-14T12:30:00Z")
        assert persist(store, [observation]) == persist(store, [observation])
        assert store.conn.execute("SELECT expected_lag_seconds FROM source_series WHERE source_key='fixture'").fetchone()[0] is None
        persist_scheduled_events(store, recorded_at="2026-10-31T12:00:00Z")
        persist_scheduled_events(store, recorded_at="2026-10-31T12:00:00Z")
        assert store.conn.execute("SELECT expected_lag_seconds FROM source_series WHERE id=?",
                                  (issuer + ":reviewed-calendar:" + indicator.id,)).fetchone()[0] == 14 * 86400


def test_generation_uses_only_inmemory_databases(monkeypatch):
    import requests
    duck_connect, sqlite_connect = duckdb.connect, sqlite3.connect
    def duck_memory(path, *args, **kwargs):
        assert path == ":memory:"
        return duck_connect(path, *args, **kwargs)
    def sqlite_memory(path, *args, **kwargs):
        assert path == ":memory:"
        return sqlite_connect(path, *args, **kwargs)
    def no_network(*args, **kwargs):
        pytest.fail("Catalogue generation attempted network access")
    monkeypatch.setattr(duckdb, "connect", duck_memory)
    monkeypatch.setattr(sqlite3, "connect", sqlite_memory)
    monkeypatch.setattr(requests.Session, "request", no_network)
    docs.build_catalog()


def test_live_uses_readonly_files_and_period_end_lag(tmp_path, catalog):
    hub = tmp_path / "hub.duckdb"
    conn = duckdb.connect(str(hub))
    conn.execute("CREATE TABLE macro_series (series_id VARCHAR, date DATE, value DOUBLE)")
    conn.execute("INSERT INTO macro_series VALUES ('CPIAUCSL', '2026-09-01', 1)")
    conn.execute("CREATE TABLE macro_series_vintage (series_id VARCHAR, date DATE, value DOUBLE, vintage_date DATE)")
    conn.execute("INSERT INTO macro_series_vintage VALUES ('CPIAUCSL', '2026-09-01', 1, '2026-10-14')")
    conn.close()
    macro = tmp_path / "macro.sqlite"
    conn = sqlite3.connect(macro)
    conn.execute("CREATE TABLE value_vintages (indicator_id TEXT, reference_period TEXT, first_seen_at TEXT)")
    conn.executemany("INSERT INTO value_vintages VALUES (?,?,?)", [
        ("us_cpi_headline_yy", "2026-08", "2026-09-14T12:30:00+00:00"),
        ("us_cpi_headline_yy", "2026-09", "2026-10-14T12:30:00+00:00"),
        ("us_cpi_headline_yy", "2026-09", "2026-10-20T12:30:00+00:00"),
    ])
    conn.commit()
    conn.close()
    before = {p: sha256(p.read_bytes()).hexdigest() for p in (hub, macro)}
    live = deepcopy(catalog)
    observe(live, [hub, macro])
    assert before == {p: sha256(p.read_bytes()).hexdigest() for p in (hub, macro)}
    rows = {(r["store"], r["id"]): r for r in live["series"]}
    hub_row = rows["hub", "CPIAUCSL"]
    assert hub_row["observed"]["first_date"] == "2026-09-01"
    assert hub_row["observed"]["lag_days"] == 14
    macro_row = rows["macro_releases.sqlite", "us_cpi_headline_yy"]
    assert macro_row["observed"]["last_date"] == "2026-09"
    assert macro_row["observed"]["lag_days"] == pytest.approx(14 + 12.5 / 24, abs=0.0001)
    assert macro_row["expected_lag_days"] == 14
    assert "Observed lag" in docs.render_markdown(live)
    assert "observed" not in catalog["series"][0]


def test_live_does_not_create_missing_db_or_overwrite_canonical(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        observe({"series": []}, [missing])
    assert not missing.exists()
    with pytest.raises(SystemExit):
        docs.main(["--live", str(missing), "--write"])
    with pytest.raises(SystemExit):
        docs.main(["--live", str(missing), "--check"])


def test_period_end_quarters_annual_and_candles():
    assert period_end("2026-Q3", "Q").date().isoformat() == "2026-09-30"
    assert period_end("2026-07-01", "Q").date().isoformat() == "2026-09-30"
    assert period_end("2024", "A").date().isoformat() == "2024-12-31"
    assert period_end("2024-02-01", "M").date().isoformat() == "2024-02-29"
    assert period_end("2026-10-01T00:00:00", "4h").hour == 4
