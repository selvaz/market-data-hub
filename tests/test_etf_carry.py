import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_data_hub import catalog_docs
from market_data_hub import etf_carry as carry
from market_data_hub import runner
from market_data_hub.db.connection import get_conn
from market_data_hub.reader import read_etf_carry


def test_saved_yahoo_fixture_and_missing_fields():
    fixture = json.loads((Path(__file__).parent / "fixtures/yahoo_etf_carry.json").read_text())
    values = carry.parse(fixture["quote"], fixture["summary"])
    assert values["trailing_distribution_yield"][0] == 0.026408149
    assert values["expense_ratio"][0] == 0.0015
    assert values["duration"][0] == 3.62
    assert "sec_30_day_yield" not in values
    assert "yield_to_maturity" not in values  # maturity is not yield
    assert "currency_hedged" not in values


def test_collect_immutable_and_no_backfill(tmp_path, monkeypatch):
    fixture = json.loads((Path(__file__).parent / "fixtures/yahoo_etf_carry.json").read_text())
    quote = fixture["quote"]
    monkeypatch.setattr(carry, "_quote_batch", lambda symbols: {"TLT": quote})
    monkeypatch.setattr(carry, "_summary", lambda symbol: fixture["summary"])
    db = str(tmp_path / "carry.duckdb")
    con = get_conn(db)
    entries = [{"symbol": "TLT", "asset_class": "FIXED_INCOME"}]
    carry.collect(con, entries, log=lambda _: None)
    quote["netExpenseRatio"] = 42
    carry.collect(con, entries, log=lambda _: None)
    assert con.execute("SELECT count(*), min(date), max(date) FROM etf_carry_snapshots").fetchone() == (
        3, datetime.now(timezone.utc).date(), datetime.now(timezone.utc).date())
    assert con.execute("SELECT value FROM etf_carry_snapshots WHERE field='expense_ratio'").fetchone()[0] == 0.0015
    con.close()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert read_etf_carry("TLT", as_of=yesterday, db_path=db).empty
    assert len(read_etf_carry("TLT", as_of=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), db_path=db)) == 3


def test_catalog_has_all_carry_series():
    catalog = catalog_docs.build_catalog()
    entries = carry.universe(catalog_docs.load_config(catalog_docs.CONFIG_DIR, "tickers.yaml")["yahoo"])
    rows = [r for r in catalog["series"] if r["table"] == "etf_carry_snapshots"]
    assert {r["id"] for r in rows} == {f"{e['symbol']}/{f}" for e in entries for f in carry.FIELDS}
    assert all(r["expected_lag_days"] == 1 and "no backfill" in r["history_start"] for r in rows)
    assert next(t for t in catalog["tables"] if t["table"] == "etf_carry_snapshots")["primary_key"] == ["date", "symbol", "field"]


def test_proxy_history_floor_and_explicit_override(monkeypatch):
    import duckdb
    import pandas as pd
    from market_data_hub.db.connection import apply_schema

    con = duckdb.connect(":memory:")
    apply_schema(con)
    monkeypatch.setattr(runner, "get_yahoo_tickers", lambda: [
        {"symbol": "VEIEX", "asset_class": "EQUITY", "history_start": "1994-05-04"}])
    calls = []
    monkeypatch.setattr(runner.yh, "yahoo_batch", lambda symbols, start, end, **kw:
                        calls.append(start) or {"VEIEX": pd.DataFrame()})
    cfg = {"backfill_start": {"yahoo": "2010-01-01"},
           "incremental": {"tail_refresh_days": 3},
           "parallelism": {"yahoo_batch_sleep": 0}}
    runner.run_yahoo(con, cfg, "one", end="2026-10-31")
    runner.run_yahoo(con, cfg, "two", start_override="2010-01-01", end="2026-10-31")
    assert calls == ["1994-05-04", "2010-01-01"]
    con.close()
