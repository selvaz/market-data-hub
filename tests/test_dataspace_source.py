# -*- coding: utf-8 -*-
"""The DataSpace adapter for this repository's market database.

Skipped entirely when ``lazydataspace`` is not installed: it is an optional
extra, and the repo must keep working standalone without it.
"""
from __future__ import annotations

import duckdb
import pytest

lazydataspace = pytest.importorskip("lazydataspace", reason="optional [lazydataspace] extra")

from lazydataspace import DataSpace, Health, Source, SourceInfo  # noqa: E402

from market_data_hub.dataspace_source import MarketDataSource  # noqa: E402


@pytest.fixture
def real_db(tmp_path):
    """A DuckDB file with the sentinel table — stands in for a live market DB.

    Carries every column the readiness probe requires (the ones
    ``read_prices`` filters and selects on), not just any table with the
    right name: a name-only stand-in is exactly the wrong file the probe
    exists to reject.
    """
    path = tmp_path / "market.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE prices_daily ("
        "date DATE, symbol VARCHAR, close DOUBLE, adj_close DOUBLE, "
        "is_live BOOLEAN)"
    )
    con.close()
    return str(path)


class TestProtocolConformance:
    def test_satisfies_the_source_protocol(self, real_db):
        assert isinstance(MarketDataSource(real_db), Source)

    def test_identity(self, real_db):
        source = MarketDataSource(real_db)
        assert source.name == "market"
        assert source.owner == "market-data-hub"

    def test_registrable_in_a_dataspace(self, real_db):
        space = DataSpace(MarketDataSource(real_db))
        assert space.list() == ["market"]
        assert space["market"].owner == "market-data-hub"


class TestDescribe:
    def test_returns_source_info_with_capabilities(self, real_db):
        info = MarketDataSource(real_db).describe()
        assert isinstance(info, SourceInfo)
        assert "market.prices" in info.capabilities

    def test_description_does_not_leak_the_db_path(self, real_db):
        """describe() is logged; a path is deployment information."""
        info = MarketDataSource(real_db).describe()
        assert real_db not in info.description
        assert ".duckdb" not in info.description


class TestHealth:
    def test_ready_against_a_real_database(self, real_db):
        health = MarketDataSource(real_db).health()
        assert isinstance(health, Health)
        assert health.ready is True

    def test_unready_when_the_file_is_absent(self, tmp_path):
        health = MarketDataSource(str(tmp_path / "missing.duckdb")).health()
        assert health.ready is False
        assert "does not exist" in health.detail

    def test_absent_database_is_not_created_by_the_check(self, tmp_path):
        """A readiness probe that creates an empty DB would report ready and
        hand the workflow an empty source — the reason this does not use
        get_conn()."""
        missing = tmp_path / "missing.duckdb"
        MarketDataSource(str(missing)).health()
        assert not missing.exists()

    def test_unready_when_the_file_is_not_a_database(self, tmp_path):
        junk = tmp_path / "not-a-db.duckdb"
        junk.write_text("this is not a duckdb file", encoding="utf-8")
        health = MarketDataSource(str(junk)).health()
        assert health.ready is False
        assert "cannot open database" in health.detail

    def test_unready_when_pointed_at_the_wrong_database(self, tmp_path):
        """A readable DuckDB file without prices_daily is the realistic
        misconfiguration: right format, wrong file."""
        other = tmp_path / "other.duckdb"
        con = duckdb.connect(str(other))
        con.execute("CREATE TABLE something_else (x INTEGER)")
        con.close()
        health = MarketDataSource(str(other)).health()
        assert health.ready is False
        assert "prices_daily" in health.detail

    def test_unready_when_the_sentinel_table_has_a_foreign_schema(self, tmp_path):
        """A table merely *named* prices_daily is not identity: a legacy or
        foreign DuckDB would pass a name-only probe and then fail the very
        first read_prices() on the columns it filters and selects."""
        legacy = tmp_path / "legacy.duckdb"
        con = duckdb.connect(str(legacy))
        con.execute("CREATE TABLE prices_daily (date DATE, symbol VARCHAR, close DOUBLE)")
        con.close()
        health = MarketDataSource(str(legacy)).health()
        assert health.ready is False
        assert "adj_close" in health.detail or "is_live" in health.detail

    def test_failure_detail_never_contains_the_path(self, tmp_path):
        """Health details are logged; DuckDB's own errors quote the full path."""
        junk = tmp_path / "secret-location.duckdb"
        junk.write_text("junk", encoding="utf-8")
        detail = MarketDataSource(str(junk)).health().detail
        assert str(junk) not in detail
        assert "secret-location" not in detail

    def test_health_is_read_only(self, real_db):
        """Repeated checks must not modify the database."""
        before = duckdb.connect(real_db, read_only=True).execute(
            "SELECT count(*) FROM information_schema.tables"
        ).fetchone()
        source = MarketDataSource(real_db)
        source.health()
        source.health()
        after = duckdb.connect(real_db, read_only=True).execute(
            "SELECT count(*) FROM information_schema.tables"
        ).fetchone()
        assert before == after


class TestReadinessGate:
    def test_gate_passes_with_a_real_database(self, real_db):
        DataSpace(MarketDataSource(real_db)).require_ready()

    def test_gate_fails_before_a_workflow_writes(self, tmp_path):
        space = DataSpace(MarketDataSource(str(tmp_path / "missing.duckdb")))
        with pytest.raises(lazydataspace.SourceNotReadyError) as exc:
            space.require_ready()
        assert "market" in str(exc.value)


class TestStandaloneIndependence:
    def test_the_package_does_not_import_the_adapter(self):
        """Importing market_data_hub must not require lazydataspace: the repo has
        to keep working standalone, which is why this is an optional extra."""
        import ast
        import pathlib

        import market_data_hub

        package_dir = pathlib.Path(market_data_hub.__file__).parent
        importers = []
        for module in package_dir.rglob("*.py"):
            if module.name == "dataspace_source.py":
                continue
            tree = ast.parse(module.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".")[0] == "lazydataspace":
                        importers.append(module.name)
                    elif node.module.endswith("dataspace_source"):
                        importers.append(module.name)
                elif isinstance(node, ast.Import):
                    if any(a.name.split(".")[0] == "lazydataspace" for a in node.names):
                        importers.append(module.name)
        assert not importers, f"these modules would make lazydataspace mandatory: {importers}"
