"""Inventory regressions using temporary databases and mocked downloads only."""
from contextlib import closing
from datetime import date

import pandas as pd
import pytest

from market_data_hub import runner
from market_data_hub.config_loader import get_fred_series, get_macro_panel_specs, get_settings, _load_yaml
from market_data_hub.coverage.report import rebuild_macro_panel_coverage
from market_data_hub.db.connection import get_conn
from market_data_hub.db.upsert import upsert, record_vintage
from market_data_hub.indicator_cards import indicator_card
from test_panel_coverage import _panel_row


@pytest.mark.parametrize("provider", ["WB", "IMF", "IMF_SDMX", "BIS", "ECB"])
def test_panel_drops_projections_and_cleans_only_future_panel_keys(tmp_db, monkeypatch, provider):
    today = "2026-10-31"
    monkeypatch.setattr(runner, "_today", lambda: today)
    monkeypatch.setattr(runner, "get_macro_panel_specs", lambda: [{"id": "x", "source": provider}])
    monkeypatch.setattr(runner, "get_countries", lambda: [{"iso3": "USA"}])
    rows = pd.DataFrame([_panel_row("x", "USA", y) for y in (2025, 2026, 2031)])
    monkeypatch.setattr(runner.mp, "fetch_indicator", lambda *a, **kw: (rows, provider, "ok"))
    with closing(get_conn()) as con:
        upsert(con, "macro_panel", rows)
        # Old future vintages remain available for audit.
        record_vintage(con, "macro_panel", rows, "2026-10-16", run_id="old")
        con.execute("INSERT INTO macro_series (date, series_id, value) VALUES ('2031-12-31', 'unrelated', 1)")
        runner.run_macro_panel(con, {"http": {}, "backfill_start": {"fred": "1776-07-04"},
                                    "parallelism": {"imf_sleep": 0}}, "new")
        assert con.execute("SELECT date FROM macro_panel").fetchall() == [(date(2025, 12, 31),)]
        assert con.execute("SELECT count(*) FROM macro_panel_vintage WHERE date > ?::DATE AND run_id='new'", [today]).fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM macro_panel_vintage WHERE run_id='old'").fetchone()[0] == 3
        assert con.execute("SELECT count(*) FROM macro_series WHERE series_id='unrelated'").fetchone()[0] == 1


def test_panel_coverage_ignores_projections_and_marks_only_discontinued(tmp_db):
    discontinued = {s["id"] for s in get_macro_panel_specs() if s.get("discontinued")}
    assert discontinued == {"tourism_exports_share", "tourism_receipts_usd", "natural_resource_rents_gdp"}
    with closing(get_conn()) as con:
        rows = [_panel_row(iid, "USA", 2020) for iid in (*sorted(discontinued), "still_stalled")]
        rows += [_panel_row("still_stalled", "USA", 2031), _panel_row("future_only", "USA", 2031)]
        upsert(con, "macro_panel", pd.DataFrame(rows))
        assert rebuild_macro_panel_coverage(con, "coverage", 1) == 4
        cov = con.execute("SELECT * FROM macro_panel_coverage").fetch_df().set_index("indicator_id")
        assert (cov.lag_days >= 0).all()
        assert cov.loc["still_stalled", "status"] == "stalled"
        assert set(cov.loc[sorted(discontinued), "status"]) == {"discontinued"}
        assert not cov.loc[sorted(discontinued), "stalled"].any()
        con.execute("DELETE FROM macro_panel WHERE date <= CURRENT_DATE")
        assert rebuild_macro_panel_coverage(con, "empty", 1) == 0
        assert con.execute("SELECT count(*) FROM macro_panel_coverage").fetchone()[0] == 0


def test_retired_fred_id_is_neither_fetched_nor_advertised():
    retired = "LRHUTTTTEZM156S"
    assert retired not in {s["symbol"] for s in get_fred_series()}
    assert retired not in str(_load_yaml("alfred_backfill.yaml"))
    calendar = _load_yaml("econ_calendar.yaml")
    entries = next(v for v in calendar.values() if isinstance(v, list) and any(isinstance(e, dict) and e.get("key") == "ez_unemp" for e in v))
    assert next(e for e in entries if e.get("key") == "ez_unemp")["macro_series_id"] is None
    card = indicator_card("ea_unemployment")
    assert not card.ids.ms and card.ids.cat == ("ea_unemployment_rate",)


def test_fred_one_off_backfill_then_daily_tail(tmp_db, monkeypatch):
    cfg = {**get_settings(), "parallelism": {"fred_sleep": 0}}
    assert cfg["backfill_start"]["fred"] == "1776-07-04"
    monkeypatch.setattr(runner, "get_fred_series", lambda: [{"symbol": "INDPRO"}])
    starts = []
    def fetch(sid, start, end, **kwargs):
        starts.append(start)
        return pd.DataFrame({"date": pd.to_datetime(["1919-01-01", "2026-10-01"]),
                             "series_id": sid, "value": [1., 100.], "source": "fred"})
    monkeypatch.setattr(runner.fr, "fetch_fred", fetch)
    with closing(get_conn()) as con:
        # Existing post-2000 data is expanded by the existing backfill entry point.
        upsert(con, "macro_series", pd.DataFrame({"date": pd.to_datetime(["2026-10-01"]),
                                                "series_id": ["INDPRO"], "value": [100.]}))
        runner.run_fred(con, cfg, "backfill", start_override=cfg["backfill_start"]["fred"], end="2026-10-31")
        runner.run_fred(con, cfg, "daily", end="2026-10-31")
        assert con.execute("SELECT min(date) FROM macro_series").fetchone()[0] == date(1919, 1, 1)
    assert starts == ["1776-07-04", "2026-06-28"]
