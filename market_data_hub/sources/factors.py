# -*- coding: utf-8 -*-
"""
factors.py — Fama-French / momentum factor returns (Ken French Data Library).

A single zip per dataset holds a CSV with a preamble, a header row, then one row
per date (YYYYMMDD daily / YYYYMM monthly) of factor values expressed in PERCENT.
We parse the first data block, convert to decimal returns, and emit the canonical
factor_returns shape. The parser is split out (_parse_french_csv) so it can be
unit-tested without network access.

Canonical columns: date, factor_set, factor, value, frequency, source.
"""
from __future__ import annotations

import datetime as _dt
import io
import time
import zipfile
from typing import Dict, List, Optional

import pandas as pd

_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"

# factor_set -> {url, frequency}
CATALOG: Dict[str, Dict[str, str]] = {
    "FF5_daily": {
        "url": f"{_BASE}/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
        "frequency": "D",
    },
    "MOM_daily": {
        "url": f"{_BASE}/F-F_Momentum_Factor_daily_CSV.zip",
        "frequency": "D",
    },
    "FF5_monthly": {
        "url": f"{_BASE}/F-F_Research_Data_5_Factors_2x3_CSV.zip",
        "frequency": "M",
    },
}

_COLS = ["date", "factor_set", "factor", "value", "frequency", "source"]


def _parse_french_csv(text: str, factor_set: str, frequency: str,
                      source: str = "ken_french") -> pd.DataFrame:
    """Parse the FIRST data block of a Ken French CSV into long factor rows.

    French files prepend a free-text preamble and may append a second (e.g.
    annual) block after a blank line; we read only the first block. Values are
    in percent and converted to decimal returns. Robust to stray rows.
    """
    header: Optional[List[str]] = None
    started = False
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if started:          # blank line ends the first block
                break
            continue
        cells = [c.strip() for c in line.split(",")]
        if header is None:
            # header row: empty first cell followed by factor names
            if cells[0] == "" and len(cells) > 1 and any(cells[1:]):
                header = [c for c in cells[1:] if c]
            continue
        tok = cells[0]
        if not (tok.isdigit() and len(tok) in (6, 8)):
            if started:          # left the data block
                break
            continue
        started = True
        if len(tok) == 8:
            d = _dt.date(int(tok[:4]), int(tok[4:6]), int(tok[6:8]))
        else:                    # YYYYMM -> first of month
            d = _dt.date(int(tok[:4]), int(tok[4:6]), 1)
        for factor, v in zip(header, cells[1:1 + len(header)]):
            try:
                fv = float(v)
            except ValueError:
                continue
            if fv <= -99.0:      # French sentinel for missing
                continue
            rows.append({"date": d, "factor_set": factor_set, "factor": factor,
                         "value": fv / 100.0, "frequency": frequency,
                         "source": source})
    return pd.DataFrame(rows, columns=_COLS)


def fetch_french(factor_set: str, *, start: Optional[str] = None,
                 timeout: int = 30, retries: int = 3, base_sleep: float = 1.0
                 ) -> pd.DataFrame:
    """Download and parse one Ken French dataset into the factor_returns shape."""
    import requests   # lazy: keeps the module (and the parser) importable without it

    if factor_set not in CATALOG:
        raise ValueError(f"Unknown factor_set: {factor_set} (have {list(CATALOG)})")
    spec = CATALOG[factor_set]

    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = requests.get(spec["url"], timeout=timeout)
            r.raise_for_status()
            content = r.content
            break
        except Exception as e:                       # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(base_sleep * (2 ** attempt))
    else:
        raise last if last else RuntimeError("download failed")

    with zipfile.ZipFile(io.BytesIO(content)) as z:
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(csv_name).decode("latin-1")

    df = _parse_french_csv(text, factor_set, spec["frequency"])
    if start and not df.empty:
        df = df[df["date"] >= pd.to_datetime(start).date()].reset_index(drop=True)
    return df
