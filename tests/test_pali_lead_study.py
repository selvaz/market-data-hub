"""PALI reuses the study method with explicit NSA levels and monthly cutoffs."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.research.mortgage_lead_study import monthly_transforms, run_study

PATH = Path(__file__).resolve().parents[1] / "scripts/research/pali_lead_study.py"
SPEC = importlib.util.spec_from_file_location("pali_study", PATH)
pali = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pali)


def test_pali_monthly_mean_then_log_difference_keeps_missing_months():
    raw = pd.DataFrame({"PALI_UPB": [100.0, 200.0, 180.0, 190.0]},
                       index=pd.to_datetime(["2004-01-09", "2004-01-16", "2004-02-06", "2004-04-02"]))
    monthly, transformed = monthly_transforms(raw, ("PALI_UPB",))
    assert monthly.loc["2004-01", "PALI_UPB"] == 150
    assert transformed.loc["2004-02", "PALI_UPB"] == pytest.approx(100 * np.log(180 / 150))
    assert pd.isna(transformed.loc["2004-04", "PALI_UPB"])


def test_pali_cutoff_and_snapshot_contract(tmp_path, monkeypatch):
    snapshot = tmp_path / "observations.json"
    snapshot.write_text(json.dumps({"end": "2026-09-30", "observations": {
        "HSN1F": [{"date": "2003-12-01", "value": "100"}, {"date": "2004-01-01", "value": "101"}],
        "HOUST": [], "EXHOSLUSM495S": [],
    }}))
    def capture(raw, provenance, **kwargs):
        assert raw.index.min() == pd.Timestamp("2004-01-01")
        assert raw.index.max() <= pd.Timestamp("2026-09-30")
        assert kwargs["predictors"] == ("PALI_UPB", "PALI_COUNT")
        assert provenance["pali_history_start"] == "2004-01-09"
        assert provenance["pali_adjustment"].startswith("NSA")
        return {"method": {}}
    monkeypatch.setattr(pali, "run_study", capture)
    workbook = PATH.parents[2] / "tests/fixtures/macro_releases/mortgage_applications/pali-history.xlsx"
    pali.study(workbook, snapshot, "2026-09-30")
    with pytest.raises(ValueError, match="Month-end"):
        pali.study(workbook, snapshot, "2026-09-29")
    with pytest.raises(ValueError, match="cutoffs"):
        pali.study(workbook, snapshot, "2026-08-31")


def test_custom_predictor_uses_same_deterministic_statistics_and_full_span():
    rng = np.random.default_rng(1729)
    x = rng.normal(size=150)
    y = np.roll(x, 2) + rng.normal(scale=0.1, size=150)
    raw = pd.DataFrame({"PALI_UPB": 100 * np.exp(np.cumsum(x) / 100),
                        "HSN1F": 100 * np.exp(np.cumsum(y) / 100)},
                       index=pd.date_range("2004-01-01", periods=150, freq="MS"))
    kwargs = dict(predictors=("PALI_UPB",), targets=("HSN1F",), periods={"full": (None, None)})
    first = run_study(raw, {}, **kwargs)
    second = run_study(raw, {}, **kwargs)
    assert json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(second, sort_keys=True, allow_nan=False)
    result, = first["results"]
    assert result["best_correlation"]["lag"] == 2
    assert len(result["correlations"]) == 13
    assert result["granger"]["bic"]["p"] < .01
    assert min(o["rmse_ratio"] for o in result["out_of_sample"]) < .5
