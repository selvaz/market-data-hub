"""Offline research fixture: recover a known lead, preserve gaps, hide inputs."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/research/mortgage_lead_study.py"
spec = importlib.util.spec_from_file_location("mortgage_lead_study", SCRIPT)
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


@pytest.fixture
def housing_fixture(tmp_path):
    rng = np.random.default_rng(study.SEED)
    n = 360
    shocks = rng.normal(0, 0.1, n)
    growth = rng.normal(0, 0.02, n)
    growth[2:] -= shocks[:-2]
    frame = pd.DataFrame({"date": pd.date_range("1990-01-01", periods=n, freq="MS"),
                          "MORTGAGE30US": 7+shocks.cumsum(),
                          "HSN1F": 500*np.exp(growth.cumsum()/100)})
    path = tmp_path / "fixture.csv"
    frame.to_csv(path, index=False)
    return path


def test_cli_recovers_fixture_lead_and_is_deterministic(housing_fixture, tmp_path, capsys):
    output, tables = tmp_path / "stats.json", tmp_path / "tables.md"
    args = ["--input", str(housing_fixture), "--output", str(output), "--tables", str(tables)]
    assert study.main(args) == 0
    first = output.read_bytes()
    assert study.main(args) == 0
    assert output.read_bytes() == first
    result = json.loads(first)
    full = next(r for r in result["results"] if r["predictor"] == "MORTGAGE30US"
                and r["target"] == "HSN1F" and r["period"] == "full")
    assert full["best_correlation"]["lag"] == 2
    assert full["best_correlation"]["r"] < -0.9
    assert full["granger"]["bic"]["lag"] == 2
    assert full["granger"]["bic"]["p"] < 0.001
    one_month = next(o for o in full["out_of_sample"] if o["horizon"] == 1)
    assert one_month["rmse_ratio"] < 0.5
    assert one_month["first_target"] == "2000-02"
    missing = next(r for r in result["results"] if r["predictor"] == "MBA_PURCHASE")
    assert missing["status"] == "insufficient_history"
    assert missing["best_correlation"] is None
    assert missing["out_of_sample"] == []
    assert "observations" not in result
    assert "date,MORTGAGE30US" not in tables.read_text()
    assert "statistics only" in capsys.readouterr().out


def test_weekly_average_and_missing_months_are_not_compressed():
    raw = pd.DataFrame({"MORTGAGE30US": [4.0, 6.0, 7.0, 8.0],
                        "HSN1F": [100.0, 100.0, 110.0, 121.0]},
                       index=pd.to_datetime(["2000-01-06", "2000-01-13", "2000-03-02", "2000-04-06"]))
    monthly, transformed = study.monthly_transforms(raw)
    assert monthly.loc["2000-01", "MORTGAGE30US"] == 5.0
    assert transformed.loc["2000-03"].isna().all()
    assert transformed.loc["2000-04", "MORTGAGE30US"] == 1.0
    assert transformed.loc["2000-04", "HSN1F"] == pytest.approx(100*np.log(1.1))
    with pytest.raises(ValueError, match="Duplicate"):
        study.monthly_transforms(pd.concat([raw, raw.iloc[:1]]))


def test_f_survival_and_credential_failure(tmp_path, monkeypatch, capsys):
    # Closed-form F(2,2) survival = 1/(1+f), plus F(1,1) symmetry.
    assert study.f_survival(1.0, 1, 1) == pytest.approx(0.5)
    assert study.f_survival(3.0, 2, 2) == pytest.approx(0.25)
    assert study.f_survival(0.0, 12, 100) == 1.0
    secret = "fixture-credential-never-log"
    monkeypatch.setenv("FRED_API_KEY", secret)

    def fail(*args, **kwargs):
        raise study.requests.ConnectionError("https://example.invalid/?api_key="+secret)

    monkeypatch.setattr(study.requests, "get", fail)
    assert study.main(["--fetch", "--output", str(tmp_path / "stats.json")]) == 1
    assert secret not in capsys.readouterr().err
