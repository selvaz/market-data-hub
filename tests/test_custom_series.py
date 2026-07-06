# -*- coding: utf-8 -*-
"""App-published series: custom.store_series -> reader/extract round-trip.

The custom_series table is the sanctioned expansion point for downstream apps
(LazyFin & co.): they publish series the hub's connectors do not provide and
read them back through the same extract surface as everything else.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from market_data_hub import custom, extract, reader
from market_data_hub.db import connection as C


def test_store_and_read_roundtrip(tmp_db):
    added, updated = custom.store_series(
        "lazyfin:nav:pf-1",
        {"2026-01-02": 100_000.0, date(2026, 1, 5): 101_500.0},
        series_name="Portfolio pf-1 NAV", unit="USD", frequency="D",
        source="lazyfin",
    )
    assert (added, updated) == (2, 0)

    wide = reader.read_custom("lazyfin:nav:pf-1")
    assert list(wide.columns) == ["lazyfin:nav:pf-1"]
    assert wide["lazyfin:nav:pf-1"].tolist() == [100_000.0, 101_500.0]

    long = reader.read_custom("lazyfin:nav:pf-1", wide=False)
    assert set(long["source"]) == {"lazyfin"}
    assert set(long["unit"]) == {"USD"}


def test_store_replaces_existing_dates(tmp_db):
    custom.store_series("s1", {"2026-01-02": 1.0})
    added, updated = custom.store_series("s1", {"2026-01-02": 2.0,
                                                "2026-01-03": 3.0})
    assert (added, updated) == (1, 1)
    wide = reader.read_custom("s1")
    assert wide["s1"].tolist() == [2.0, 3.0]


def test_extract_series_custom_domain(tmp_db):
    custom.store_series("acme:index", pd.Series(
        [10.0, 11.0, 12.1],
        index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
    ))
    df, meta = extract.extract_series(["acme:index"], domain="custom",
                                      transform="pct_change")
    assert meta["domain"] == "custom"
    assert list(df.columns) == ["acme:index"]
    assert df["acme:index"].dropna().tolist() == pytest.approx([0.10, 0.10])


def test_custom_namespace_is_separate_from_macro(tmp_db):
    # A custom id equal to a FRED id must not touch macro_series.
    custom.store_series("CPIAUCSL", {"2026-01-01": 999.0})
    assert reader.read_macro("CPIAUCSL").empty
    assert not reader.read_custom("CPIAUCSL").empty


def test_list_and_delete_series(tmp_db):
    custom.store_series("a", {"2026-01-02": 1.0, "2026-01-03": 2.0},
                        source="app1")
    custom.store_series("b", {"2026-01-02": 5.0}, source="app2")
    cat = custom.list_series()
    assert cat["series_id"].tolist() == ["a", "b"]
    assert cat.set_index("series_id").loc["a", "obs_count"] == 2

    assert custom.delete_series("a") == 2
    assert custom.list_series()["series_id"].tolist() == ["b"]
    assert custom.delete_series("a") == 0  # idempotent


def test_store_rejects_bad_input(tmp_db):
    with pytest.raises(ValueError, match="non-empty"):
        custom.store_series("", {"2026-01-02": 1.0})
    with pytest.raises(ValueError, match="no observations"):
        custom.store_series("s", {})
    with pytest.raises(ValueError, match="duplicate"):
        custom.store_series("s", [("2026-01-02", 1.0), ("2026-01-02", 2.0)])
    with pytest.raises(ValueError, match="None"):
        custom.store_series("s", {"2026-01-02": None})
    with pytest.raises(ValueError, match="unparseable"):
        custom.store_series("s", {"not-a-date": 1.0})


def test_existing_db_gains_table_via_migrate(tmp_db):
    # A pre-custom_series database (simulated by dropping the table and
    # stamping v1) must gain the table by simply reopening / migrating.
    con = C.get_conn()
    con.execute("DROP TABLE custom_series")
    con.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '1')"
    )
    assert C.migrate(con) == C.SCHEMA_VERSION
    assert con.execute("SELECT count(*) FROM custom_series").fetchone()[0] == 0
    con.close()
