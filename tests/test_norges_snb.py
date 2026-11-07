from pathlib import Path
from unittest.mock import Mock

import duckdb
import pandas as pd
import pytest

from market_data_hub import reader, sovereign_refresh
from market_data_hub.db.connection import get_conn
from market_data_hub.sources import sovereign_layers as feeds
from market_data_hub.sources.sovereign_yields import SourceUnavailable

FIX = Path(__file__).parent / "fixtures" / "sovereign_layers"


def spec(sid):
    return next(
        x for x in sovereign_refresh.catalogue()["series"] if x["symbol"] == sid
    )


@pytest.mark.parametrize(
    "sid,fixture,last,value",
    [
        ("NB_NO_10Y", "norges_10y.csv", "2026-11-05", 4.31),
        ("SNB_CH_10Y", "snb_r10.json", "2026-10-09", 0.579),
    ],
)
def test_recorded_parsers(sid, fixture, last, value):
    frame = feeds.parse_official(spec(sid), (FIX / fixture).read_bytes())
    assert frame.date.max() == pd.Timestamp(last)
    assert frame.loc[frame.date.idxmax(), "value"] == pytest.approx(value)
    assert set(frame.unit) == {"%"}


@pytest.mark.parametrize("sid", ["NB_NO_10Y", "SNB_CH_10Y"])
def test_html_and_http_failure_store_nothing(sid, monkeypatch, tmp_db):
    meta = spec(sid)
    monkeypatch.setattr(sovereign_refresh, "catalogue", lambda: {"series": [meta]})
    monkeypatch.setattr(
        feeds,
        "_request",
        Mock(return_value=Mock(status_code=200, content=b"<html>denied</html>")),
    )
    with get_conn(tmp_db) as con:
        assert sovereign_refresh.refresh(con, {}, "html", end="2026-11-06") == 2
        assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 0
        monkeypatch.setattr(
            feeds,
            "_request",
            Mock(side_effect=SourceUnavailable("official source returned HTTP 403")),
        )
        assert sovereign_refresh.refresh(con, {}, "http", end="2026-11-06") == 2
        assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 0
        assert (
            "HTTP 403"
            in con.execute(
                "SELECT error_msg FROM download_log WHERE run_id='http'"
            ).fetchone()[0]
        )


@pytest.mark.parametrize("sid", ["NB_NO_10Y", "SNB_CH_10Y"])
def test_non_200_success_status_rejected(sid, monkeypatch):
    monkeypatch.setattr(
        feeds, "_request", Mock(return_value=Mock(status_code=206, content=b"partial"))
    )
    with pytest.raises(SourceUnavailable, match="HTTP 206"):
        feeds.fetch_official(spec(sid), "2026-10-01", "2026-11-06")


def test_norges_incremental_window(monkeypatch, tmp_db):
    meta = spec("NB_NO_10Y")
    monkeypatch.setattr(sovereign_refresh, "catalogue", lambda: {"series": [meta]})
    request = Mock(
        return_value=Mock(
            status_code=200, content=(FIX / "norges_10y.csv").read_bytes()
        )
    )
    monkeypatch.setattr(feeds, "_request", request)
    with get_conn(tmp_db) as con:
        assert sovereign_refresh.refresh(con, {}, "first", end="2026-11-06") == 0
        assert request.call_args.kwargs["params"]["startPeriod"] == "2019-01-02"
        assert sovereign_refresh.refresh(con, {}, "second", end="2026-11-06") == 0
        assert request.call_args.kwargs["params"]["startPeriod"] == "2026-10-29"
        assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 2


def test_actual_chains_resolve_current_and_history(tmp_path):
    path = str(tmp_path / "chain.duckdb")
    with duckdb.connect(path) as con:
        con.execute(
            "CREATE TABLE macro_series (date DATE, series_id VARCHAR, value DOUBLE)"
        )
        con.executemany(
            "INSERT INTO macro_series VALUES (?, ?, ?)",
            [
                ("2026-11-05", "NB_NO_10Y", 4.31),
                ("2026-10-01", "IRLTLT01NOM156N", 4.0),
                ("2026-11-05", "SNB_CH_10Y", 0.45),
                ("2026-11-05", "TV_CH_10Y", 0.7),
                ("2026-11-05", "CB_CH_10Y", 0.45),
                ("2026-10-01", "CB_CH_10Y", 0.5),
            ],
        )
    no = reader.read_resolved_rate("NO_10Y", as_of="2026-11-06", db_path=path)
    ch = reader.read_resolved_rate("CH_10Y", as_of="2026-11-06", db_path=path)
    assert no["series_id"] == "NB_NO_10Y"
    assert ch["series_id"] == "SNB_CH_10Y" and ch["status"] == "primary"
    assert all(not row["voted"] for row in ch["alternatives"])
    history = reader.read_resolved_history("CH_10Y", end="2026-11-06", db_path=path)
    assert "CB_CH_10Y" in set(history.series_id)
