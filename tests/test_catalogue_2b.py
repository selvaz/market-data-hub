"""Publication cadence is separate from the spacing of observations."""
from datetime import date

import pandas as pd

from market_data_hub import catalog, config_loader
from market_data_hub.coverage import report, stalled_detector


def test_weekly_publication_cadence_and_freshness(monkeypatch):
    entries = config_loader.get_fred_series()
    affected = [e for e in entries if e["symbol"].startswith("DEX")
                or e["symbol"] == "THREEFYTP10"]
    assert len(affected) == 24
    assert all(e["publication_cadence"] == "W" and e["frequency"] == "D"
               for e in affected)
    rows = catalog.list_macro_series(with_coverage=False).set_index("series_id")
    assert all(rows.loc[e["symbol"], "publication_cadence"] == "W" for e in affected)
    meta = report._meta_lookup()["DEXJPUS"]
    # Exercise the cadence-based default even without the explicit lag override.
    meta["max_lag_days"] = None
    monkeypatch.setattr(stalled_detector, "lag_days", lambda *a, **kw: 7)
    data = pd.DataFrame({"date": pd.to_datetime(["2026-10-22", "2026-10-23", "2026-10-26"])})
    row = report._row_for("DEXJPUS", "fred", data, "date", meta, "test")
    assert row["freq_detected"] == "D"
    assert not row["stalled"]
    monkeypatch.undo()
    assert not stalled_detector.is_stalled(date(2026, 10, 23), "W", date(2026, 10, 30))
    assert stalled_detector.is_stalled(date(2026, 10, 19), "W", date(2026, 10, 30))
