"""Revised-data housing timing study; statistics out, never observations.

Uses only the project's numpy/pandas/requests dependencies; no DB imports.
Freeze public FRED responses locally with --fetch, then rerun offline from
--cache-dir. Credentials come exclusively from FRED_API_KEY. Cached observations
are research inputs, must stay untracked, and are never included in the report.
Alternatively --input accepts a local CSV: date,MORTGAGE30US,HSN1F,HOUST,
EXHOSLUSM495S,PHSI,MBA_PURCHASE (only date and a predictor are required).
MBA_PURCHASE must be an authorized seasonally adjusted level, not composite
applications or rounded weekly percent changes. Input dates denote periods.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import requests

SEED = 1729
MAX_LAG = 12
HORIZONS = (1, 2, 3, 6, 12)
FRED_IDS = ("MORTGAGE30US", "EXHOSLUSM495S", "HSN1F", "HOUST")
PREDICTORS = ("MORTGAGE30US", "MBA_PURCHASE")
TARGETS = ("PHSI", "EXHOSLUSM495S", "HSN1F", "HOUST")
PERIODS = {
    "full": (None, None),
    "pre_2008": (None, "2007-12"),
    "2009_2019": ("2009-01", "2019-12"),
    "2020_onward": ("2020-01", None),
}


def _beta_fraction(a, b, x):
    """Continued fraction for the regularized incomplete beta (Lentz)."""
    tiny = 1e-300
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 501):
        for aa in (m * (b - m) * x / ((a + 2*m - 1) * (a + 2*m)),
                   -(a + m) * (a + b + m) * x / ((a + 2*m) * (a + 2*m + 1))):
            d = 1.0 + aa * d
            c = 1.0 + aa / c
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = c if abs(c) > tiny else tiny
            delta = d * c
            h *= delta
        if abs(delta - 1.0) < 3e-14:
            return h
    raise ArithmeticError("Incomplete beta did not converge")


def beta_cdf(x, a, b):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    scale = math.exp(math.lgamma(a+b) - math.lgamma(a) - math.lgamma(b)
                     + a*math.log(x) + b*math.log1p(-x))
    if x < (a+1) / (a+b+2):
        return scale * _beta_fraction(a, b, x) / a
    return 1.0 - scale * _beta_fraction(b, a, 1-x) / b


def f_survival(f, df1, df2):
    return beta_cdf(df2 / (df2 + df1 * max(0.0, f)), df2/2, df1/2)


def cross_correlations(frame):
    """Positive k: predictor X in month t-k precedes Y in month t.

    Pearson p values assume independent Gaussian observations; the 13-test
    Bonferroni correction handles lag search, not serial dependence.
    """
    rows = []
    for lag in range(MAX_LAG + 1):
        pair = pd.concat([frame.x.shift(lag), frame.y], axis=1).dropna()
        n = len(pair)
        if n < 24 or pair.x.std() == 0 or pair.y.std() == 0:
            continue
        r = float(pair.x.corr(pair.y))
        p = beta_cdf(max(0.0, 1-r*r), (n-2)/2, 0.5)
        rows.append({"lag": lag, "n": n, "r": r, "p_iid": p,
                     "p_bonferroni_13": min(1.0, 13*p)})
    return rows


def peak_block_p(frame, observed):
    """Max-|r| null: permute 12-month X blocks, keep Y fixed.

    Preserves X dependence within blocks and Y's entire order; the maximum
    across 0..12 corrects lag search. Not structural-break-robust inference.
    """
    if observed is None:
        return None
    x, y = frame.x.to_numpy(), frame.y.to_numpy()
    blocks = [x[j:j+12] for j in range(0, len(x), 12)]
    rng = np.random.default_rng(SEED)
    exceed = 0
    for _ in range(499):
        draw = np.concatenate([blocks[j] for j in rng.permutation(len(blocks))])
        peak = 0.0
        for k in range(MAX_LAG+1):
            a, b = (draw[:-k], y[k:]) if k else (draw, y)
            valid = np.isfinite(a) & np.isfinite(b)
            if valid.sum() >= 24 and np.std(a[valid]) > 0 and np.std(b[valid]) > 0:
                peak = max(peak, abs(float(np.corrcoef(a[valid], b[valid])[0, 1])))
        exceed += peak >= abs(observed["r"])
    return (1+exceed)/500


def _ols(design, target):
    coef, _, rank, _ = np.linalg.lstsq(design, target, rcond=None)
    if rank < design.shape[1]:
        raise ValueError("Rank deficient regression")
    errors = target - design @ coef
    return coef, errors


def granger(frame):
    """Bivariate VAR order via joint Gaussian AIC/BIC, same rows for all p.

    Classical nested OLS F test of all lagged X coefficients in the Y equation.
    The selected-order p values are exploratory and not post-selection valid.
    Calendar gaps remain gaps; shifting never compresses missing months.
    """
    data = {"x": frame.x, "y": frame.y}
    for lag in range(1, MAX_LAG+1):
        data[f"x{lag}"] = frame.x.shift(lag)
        data[f"y{lag}"] = frame.y.shift(lag)
    table = pd.DataFrame(data).dropna()
    if len(table) < 40:
        return {"status": "insufficient_history", "effective_n": len(table)}
    n = len(table)
    response = table[["x", "y"]].to_numpy()
    candidates = []
    for lag in range(1, MAX_LAG+1):
        cols = [f"y{j}" for j in range(1, lag+1)]
        cols += [f"x{j}" for j in range(1, lag+1)]
        design = np.column_stack([np.ones(n), table[cols]])
        _, errors = _ols(design, response)
        sign, logdet = np.linalg.slogdet(errors.T @ errors / n)
        if sign <= 0:
            raise ValueError("Singular VAR covariance")
        k = 2*(2*lag+1)
        candidates.append((n*logdet+2*k, n*logdet+math.log(n)*k, lag))
    results = {"status": "ok", "effective_n": n}
    for criterion, score_index in (("aic", 0), ("bic", 1)):
        lag = min(candidates, key=lambda c: (c[score_index], c[2]))[2]
        ycols = [f"y{j}" for j in range(1, lag+1)]
        xcols = [f"x{j}" for j in range(1, lag+1)]
        restricted = np.column_stack([np.ones(n), table[ycols]])
        unrestricted = np.column_stack([restricted, table[xcols]])
        _, er = _ols(restricted, table.y.to_numpy())
        _, eu = _ols(unrestricted, table.y.to_numpy())
        rssr, rssu = float(er @ er), float(eu @ eu)
        df2 = n - unrestricted.shape[1]
        f = max(0.0, (rssr-rssu)/lag / (rssu/df2))
        results[criterion] = {"lag": lag, "f": f, "p": f_survival(f, lag, df2),
                              "df1": lag, "df2": df2}
    return results


def _forecast_table(frame, horizon, cap):
    data = {"target": frame.y}
    for j in range(cap):
        data[f"y{j}"] = frame.y.shift(horizon+j)
        data[f"x{j}"] = frame.x.shift(horizon+j)
    return pd.DataFrame(data)


def _select_forecast_lag(train, cap, augmented):
    n = len(train)
    target = train.target.to_numpy()
    candidates = []
    for p in range(1 if augmented else 0, cap+1):
        cols = [f"y{j}" for j in range(p)]
        if augmented:
            cols += [f"x{j}" for j in range(p)]
        design = np.column_stack([np.ones(n), train[cols]])
        _, errors = _ols(design, target)
        rss = max(float(errors @ errors), 1e-300)
        bic = n*math.log(rss/n) + math.log(n)*design.shape[1]
        candidates.append((bic, p, cols))
    return min(candidates, key=lambda c: (c[0], c[1]))


def _rmse_interval(baseline, augmented, horizon):
    """Paired circular block bootstrap, conditional on fitted forecasts."""
    rng = np.random.default_rng(SEED + horizon)
    n = len(baseline)
    block = max(12, horizon)
    indices = (rng.integers(0, n, (499, math.ceil(n/block), 1))
               + np.arange(block)[None, None, :]) % n
    indices = indices.reshape(499, -1)[:, :n]
    ratios = np.sqrt(np.mean(augmented[indices]**2, axis=1)
                     / np.mean(baseline[indices]**2, axis=1))
    return [float(v) for v in np.quantile(ratios, [0.025, 0.975])]


def out_of_sample(frame):
    """Direct h-month-ahead growth forecasts with expanding training windows.

    Lag choices are frozen using only the initial training set, separately for
    each horizon/model. Coefficients refit at each origin. At t only outcomes
    dated <=t may train; features of the forecast for t+h are dated <=t.
    This assumes period-t releases are already known: pseudo-OOS, not real time.
    """
    results = []
    initial = max(36, min(120, len(frame)//2))
    for h in HORIZONS:
        cap = min(MAX_LAG, (initial-h-10)//5)
        if cap < 1:
            continue
        table = _forecast_table(frame, h, cap)
        train = table.iloc[:initial].dropna()
        if len(train) < 24:
            continue
        choices = [_select_forecast_lag(train, cap, aug) for aug in (False, True)]
        designs = [np.column_stack([np.ones(len(table)), table[cols]]) for _, _, cols in choices]
        targets = table.target.to_numpy()
        valid = ~table.isna().any(axis=1).to_numpy()
        errors = [[], []]
        forecast_dates = []
        for origin in range(initial-1, len(frame)-h):
            if not valid[origin+h]:
                continue
            training_rows = np.flatnonzero(valid[:origin+1])
            for model, design in enumerate(designs):
                coef, _ = _ols(design[training_rows], targets[training_rows])
                forecast = float(design[origin+h] @ coef)
                errors[model].append(float(targets[origin+h])-forecast)
            forecast_dates.append(str(frame.index[origin+h]))
        if len(forecast_dates) < 12:
            continue
        base, aug = (np.asarray(e) for e in errors)
        rmse0, rmse1 = (float(np.sqrt(np.mean(e**2))) for e in (base, aug))
        results.append({"horizon": h, "n_forecasts": len(base),
                        "first_target": forecast_dates[0], "last_target": forecast_dates[-1],
                        "initial_months": initial, "candidate_max_lag": cap,
                        "baseline_lag": choices[0][1], "augmented_lag": choices[1][1],
                        "baseline_rmse": rmse0, "augmented_rmse": rmse1,
                        "rmse_ratio": rmse1/rmse0,
                        "rmse_ratio_ci95": _rmse_interval(base, aug, h)})
    return results


def monthly_transforms(raw):
    """Mean of available observations per calendar month; no interpolation.

    PMMS uses its weekly observation date, monthly releases their reference
    month. A missing month survives reindexing, including during differencing.
    """
    raw = raw.sort_index()
    if raw.index.has_duplicates:
        raise ValueError("Duplicate input dates")
    if any(s not in PREDICTORS + TARGETS for s in raw.columns):
        raise ValueError("Unexpected series column")
    monthly = raw.groupby(raw.index.to_period("M")).mean()
    monthly = monthly.reindex(pd.period_range(monthly.index.min(), monthly.index.max(), freq="M"))
    transformed = pd.DataFrame(index=monthly.index)
    for name in monthly:
        values = monthly[name]
        if name == "MORTGAGE30US":
            transformed[name] = values.diff()
        else:
            if (values.dropna() <= 0).any():
                raise ValueError("Activity levels must be positive")
            transformed[name] = 100*np.log(values).diff()
    return monthly, transformed


def run_study(raw, provenance):
    monthly, transformed = monthly_transforms(raw)
    inventory = []
    for sid in PREDICTORS + TARGETS:
        values = monthly[sid].dropna() if sid in monthly else pd.Series(dtype=float)
        inventory.append({"series": sid, "months": len(values),
                          "first": str(values.index[0]) if len(values) else None,
                          "last": str(values.index[-1]) if len(values) else None})
    results = []
    for predictor in PREDICTORS:
        for target in TARGETS:
            if predictor in transformed and target in transformed:
                frame = transformed[[predictor, target]].rename(columns={predictor: "x", target: "y"})
                valid = frame.dropna()
                if len(valid):
                    frame = frame.loc[valid.index[0]:valid.index[-1]]
            else:
                frame = pd.DataFrame(columns=["x", "y"], index=pd.PeriodIndex([], freq="M"))
            for period, (start, end) in PERIODS.items():
                sample = frame.loc[start:end]
                correlations = cross_correlations(sample)
                best = max(correlations, key=lambda row: (abs(row["r"]), -row["lag"])) if correlations else None
                results.append({"predictor": predictor, "target": target, "period": period,
                                "paired_months": len(sample.dropna()),
                                "first": str(sample.index[0]) if len(sample) else None,
                                "last": str(sample.index[-1]) if len(sample) else None,
                                "status": "ok" if len(sample.dropna()) >= 60 else "insufficient_history",
                                "correlations": correlations, "best_correlation": best,
                                "peak_block_permutation_p": peak_block_p(sample, best),
                                "granger": granger(sample),
                                "out_of_sample": out_of_sample(sample) if len(sample.dropna()) >= 60 else []})
    # Planning arithmetic, not a statistical-power guarantee. One initial
    # level is lost to differencing; weeks/year approximated by 365.25/7.
    collection = {"one_month_screen_monthly_levels": 61,
                  "one_month_screen_weeks": math.ceil(61/12*365.25/7),
                  "recommended_monthly_levels": 120,
                  "recommended_weeks": math.ceil(120/12*365.25/7)}
    return {"seed": SEED, "provenance": provenance, "inventory": inventory, "results": results,
            "collection_planning": collection,
            "method": {"max_lag": MAX_LAG, "horizons": list(HORIZONS),
                       "rate_transform": "monthly mean first difference (percentage points)",
                       "activity_transform": "100 * first difference log monthly SA level",
                       "forecast_basis": "revised data, period dates; not a real-time backtest",
                       "screen": "exploratory; no approved v2 PRED-PASS assigned"}}


def fetch_snapshot(cache_dir, as_of, end):
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise ValueError("FRED_API_KEY must be set; credentials are never read from files")
    snapshot = {"as_of": as_of, "end": end, "redistributor": "FRED / ALFRED, Federal Reserve Bank of St. Louis",
                "observations": {}, "availability": {}}
    for sid in FRED_IDS:
        params = {"api_key": key, "file_type": "json", "series_id": sid,
                  "observation_end": end, "realtime_start": as_of, "realtime_end": as_of}
        response = requests.get("https://api.stlouisfed.org/fred/series/observations", params=params, timeout=20)
        # NAR series exists in FRED only; do not stitch historical vintages.
        mode = "ALFRED fixed as-of vintage"
        if response.status_code == 400 and "does not exist in ALFRED" in response.text:
            params.pop("realtime_start")
            params.pop("realtime_end")
            response = requests.get("https://api.stlouisfed.org/fred/series/observations", params=params, timeout=20)
            mode = "FRED latest; ALFRED unavailable; frozen local response"
        if response.status_code != 200:
            raise ValueError(f"FRED retrieval failed for {sid}: HTTP {response.status_code}")
        # Do not raise_for_status(): its exception can contain the credential URL.
        payload = response.json()
        if payload.get("count", 0) > len(payload.get("observations", [])):
            raise ValueError("FRED pagination required; refusing a truncated sample")
        snapshot["observations"][sid] = [{"date": v["date"], "value": v["value"]}
                                           for v in payload.get("observations", [])]
        snapshot["availability"][sid] = mode
    # Keep availability checks reproducible as metadata, never error URLs.
    probe = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"api_key": key, "file_type": "json", "series_id": "EXHOSLUSM495S",
                                 "realtime_start": "2024-10-25", "realtime_end": "2024-10-25"}, timeout=20)
    no_archive = probe.status_code == 400 and "does not exist in ALFRED" in probe.text
    snapshot["research_probes"] = {"EXHOSLUSM495S_2024_10_25": {
        "http_status": probe.status_code, "not_in_alfred": no_archive}}
    if no_archive:
        snapshot["availability"]["EXHOSLUSM495S"] = "FRED current-date snapshot; historical ALFRED unavailable"
    for query in ("mortgage purchase applications", "pending home sales"):
        response = requests.get("https://api.stlouisfed.org/fred/series/search",
                                params={"api_key": key, "file_type": "json", "search_text": query,
                                        "limit": 100}, timeout=20)
        if response.status_code != 200:
            raise ValueError("FRED availability search failed")
        payload = response.json()
        snapshot["research_probes"][query] = {
            "count": payload.get("count"), "returned_series_ids": [s["id"] for s in payload.get("seriess", [])]}
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "observations.json"
    path.write_text(json.dumps(snapshot, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    return path


def load_snapshot(path):
    encoded = path.read_bytes()
    snapshot = json.loads(encoded)
    columns = {}
    for sid, rows in snapshot["observations"].items():
        columns[sid] = pd.Series({pd.Timestamp(v["date"]): float(v["value"])
                                  for v in rows if v["value"] != "."}, dtype=float)
    metadata = {k: v for k, v in snapshot.items() if k != "observations"}
    metadata["input_sha256"] = hashlib.sha256(encoded).hexdigest()
    return pd.DataFrame(columns), metadata


def render_tables(study):
    lines = ["<!-- Generated by mortgage_lead_study.py; statistics only. -->", "",
             "| Series | Monthly levels | First | Last |", "| --- | ---: | --- | --- |"]
    for r in study["inventory"]:
        lines.append(f"| {r['series']} | {r['months']} | {r['first'] or 'N/A'} | {r['last'] or 'N/A'} |")
    lines += ["", "| X -> Y | Period | n | Best k | r | p (IID) | p x 13 | Block max-r p | Granger AIC lag / p | Granger BIC lag / p |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for r in study["results"]:
        best, g = r["best_correlation"], r["granger"]
        peak = f"{best['lag']} | {best['r']:.3f} | {best['p_iid']:.3g} | {best['p_bonferroni_13']:.3g}" if best else "N/A | N/A | N/A | N/A"
        gs = [f"{g[c]['lag']} / {g[c]['p']:.3g}" if c in g else "N/A" for c in ("aic", "bic")]
        block_p = f"{r['peak_block_permutation_p']:.3g}" if r['peak_block_permutation_p'] is not None else "N/A"
        lines.append(f"| {r['predictor']} -> {r['target']} | {r['period']} | {r['paired_months']} | {peak} | {block_p} | {gs[0]} | {gs[1]} |")
    lines += ["", "| X -> Y | Period | h | Forecasts | First / last target | AR / ARX lag | RMSE AR | RMSE ARX | Ratio | Block CI 95% |",
              "| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |"]
    for r in study["results"]:
        for o in r["out_of_sample"]:
            lo, hi = o["rmse_ratio_ci95"]
            lines.append(f"| {r['predictor']} -> {r['target']} | {r['period']} | {o['horizon']} | {o['n_forecasts']} | {o['first_target']} / {o['last_target']} | {o['baseline_lag']} / {o['augmented_lag']} | {o['baseline_rmse']:.3f} | {o['augmented_rmse']:.3f} | {o['rmse_ratio']:.3f} | [{lo:.3f}, {hi:.3f}] |")
    lines += ["", "| X -> Y (full sample) | " + " | ".join(f"k={k}" for k in range(13)) + " |",
              "| --- | " + " | ".join(["---:"]*13) + " |"]
    for r in study["results"]:
        if r["period"] == "full" and r["correlations"]:
            values = {v["lag"]: v["r"] for v in r["correlations"]}
            lines.append(f"| {r['predictor']} -> {r['target']} | " + " | ".join(f"{values[k]:.3f}" if k in values else "N/A" for k in range(13)) + " |")
    return "\n".join(lines)+"\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path(".tmp/mortgage-lead"))
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--as-of", default="2026-10-25")
    parser.add_argument("--end", default="2026-09-30")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tables", type=Path)
    args = parser.parse_args(argv)
    if args.fetch and args.input:
        parser.error("--fetch and --input are mutually exclusive")
    try:
        if args.fetch:
            fetch_snapshot(args.cache_dir, args.as_of, args.end)
        if args.input:
            encoded = args.input.read_bytes()
            raw = pd.read_csv(io.BytesIO(encoded), index_col="date", parse_dates=True)
            provenance = {"source": "local authorized input", "input_sha256": hashlib.sha256(encoded).hexdigest()}
        else:
            raw, provenance = load_snapshot(args.cache_dir / "observations.json")
            if provenance["as_of"] != args.as_of or provenance["end"] != args.end:
                raise ValueError("Cached dates differ from --as-of/--end; fetch explicitly")
        cutoff = pd.Timestamp(args.end)
        if cutoff != cutoff + pd.offsets.MonthEnd(0):
            raise ValueError("--end must be a month end to exclude partial calendar months")
        study = run_study(raw.loc[:cutoff], provenance)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(study, sort_keys=True, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        if args.tables:
            args.tables.parent.mkdir(parents=True, exist_ok=True)
            args.tables.write_text(render_tables(study), encoding="utf-8")
        print(f"Study complete: {len(study['results'])} pair/period records; statistics only.")
        return 0
    except (requests.RequestException, ValueError, OSError, KeyError, ArithmeticError):
        # Never echo request exceptions/URLs or input values into agent output.
        print("Study failed: check input schema, local cache and FRED_API_KEY; no credentials logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
