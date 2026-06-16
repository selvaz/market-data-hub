# -*- coding: utf-8 -*-
"""Guards that protect the catalogs from corruption / accidental wipes."""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

import import_from_excel as imp
from market_data_hub.config import _generate_macro_panel as gen


def test_import_tickers_skips_fred_ids():
    df = pd.DataFrame([
        {"Ticker": "DGS10", "Asset_Class": "", "Area": "", "Priority": ""},
        {"Ticker": "CPIAUCSL", "Asset_Class": "", "Area": "", "Priority": ""},
        {"Ticker": "ZZTEST", "Asset_Class": "EQUITY", "Area": "X", "Priority": 1},
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        imp.import_tickers(df, validate_only=True)   # validate_only -> no write
    out = buf.getvalue()
    assert "DGS10: FRED series ID" in out
    assert "CPIAUCSL: FRED series ID" in out
    assert "1 new" in out and "2 skipped" in out


def test_generator_refuses_to_discard_live_data():
    cfg_dir = Path(gen.OUT)
    before = {f.name: f.read_bytes()
              for f in (cfg_dir / "macro_panel.yaml", cfg_dir / "countries.yaml")}
    rc = gen.main([])                      # no --force
    assert rc == 1                         # aborts
    after = {f.name: f.read_bytes()
             for f in (cfg_dir / "macro_panel.yaml", cfg_dir / "countries.yaml")}
    assert before == after                 # nothing overwritten
