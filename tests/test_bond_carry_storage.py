from market_data_hub.coverage.report import rebuild_coverage
from market_data_hub.db.connection import get_conn, migrate
from market_data_hub.reader import read_etf_carry


def test_migrate_old_carry_and_reader_latest(tmp_path):
    path = str(tmp_path / "hub.duckdb")
    con = get_conn(path)
    con.execute("DROP TABLE etf_carry_snapshots")
    con.execute(
        "CREATE TABLE etf_carry_snapshots (date DATE, symbol VARCHAR, field VARCHAR, value DOUBLE, source VARCHAR, retrieved_at TIMESTAMP, raw_field VARCHAR, PRIMARY KEY(date,symbol,field))"
    )
    con.execute(
        "INSERT INTO schema_meta VALUES ('schema_version', '29') ON CONFLICT (key) DO UPDATE SET value='29'"
    )
    assert migrate(con) == 30
    con.execute(
        "INSERT INTO etf_carry_snapshots VALUES ('2026-01-01','AGG','yield_to_maturity',0.04,'ssga','2026-01-01 12:00:00','YTM','2025-12-31','SPAB','USD',NULL)"
    )
    con.execute(
        "INSERT INTO etf_carry_snapshots VALUES ('2026-01-02','AGG','yield_to_maturity',0.05,'ssga','2026-01-02 12:00:00','YTM','2026-01-01','SPAB','USD',NULL)"
    )
    con.close()
    early = read_etf_carry("AGG", as_of="2026-01-02 11:00:00", db_path=path)
    late = read_etf_carry("AGG", as_of="2026-01-03", db_path=path)
    assert early.value.tolist() == [0.04]
    assert late.value.tolist() == [0.05]
    assert late.proxy_symbol.tolist() == ["SPAB"]
    assert late.index_currency.tolist() == ["USD"]


def test_carry_coverage_does_not_replace_price(tmp_path):
    con = get_conn(str(tmp_path / "coverage.duckdb"))
    con.execute(
        "INSERT INTO etf_carry_snapshots VALUES ('2026-01-02','AGG','yield_to_maturity',0.05,'ssga','2026-01-02','YTM','2026-01-01','SPAB','USD',NULL)"
    )
    rebuild_coverage(con, "test")
    assert con.execute(
        "SELECT symbol FROM coverage_report WHERE symbol='carry:AGG'"
    ).fetchone() == ("carry:AGG",)
    assert (
        con.execute(
            "SELECT count(*) FROM coverage_report WHERE symbol='AGG'"
        ).fetchone()[0]
        == 0
    )
    con.close()
