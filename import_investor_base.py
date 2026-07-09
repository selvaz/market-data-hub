# -*- coding: utf-8 -*-
"""
import_investor_base.py — MANUAL ingest of the Arslanalp-Tsuda "Sovereign Debt
Investor Base" dataset into macro_panel as `nonresident_debt_share`.

WHY THIS IS MANUAL (not a connector): the AT dataset has NO live API. It is
published as periodically-updated Excel workbooks alongside the IMF working
papers (advanced economies WP/12/284, emerging markets WP/14/39, updated 2024).
This is the closest official source for Dalio's #1 "can they print their way
out?" variable — the share of sovereign debt held by NON-RESIDENTS (and the
foreign-currency split). It must be downloaded by hand and dropped in.

USAGE
    1. Download the latest AT workbook (advanced + EM) to ./data/ .
    2. Inspect its layout and set SHEET / COLUMN mapping below — the published
       layout changes between vintages, so this is a TEMPLATE, not a
       fire-and-forget loader. Verify the parsed frame before writing.
    3. python import_investor_base.py path/to/at_workbook.xlsx --dry-run
       python import_investor_base.py path/to/at_workbook.xlsx        # writes

The series lands on the unweighted 'markets' pillar (staged), like the other
market inputs, so it is visible to the Dalio layer without changing composites
until explicitly wired in.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from market_data_hub.config_loader import get_countries          # noqa: E402
from market_data_hub.db.connection import get_conn                # noqa: E402
from market_data_hub.db.upsert import upsert                      # noqa: E402

INDICATOR_ID = "nonresident_debt_share"
INDICATOR_NAME = "Sovereign debt held by non-residents (% of total, Arslanalp-Tsuda)"

# --- LAYOUT MAPPING (verify against the actual workbook before trusting) ------
# The AT workbooks are wide (one sheet per country, or country x quarter panels).
# Set these to match the file you downloaded. Left as the documented default of
# the 2024 vintage's "combined" panel sheet; ADJUST as needed.
SHEET = "Data"
COL_COUNTRY = "Country"          # ISO name or code column
COL_DATE = "Date"                # quarter, e.g. '2024Q4'
COL_FOREIGN = "Foreign"          # foreign/non-resident holdings (level or %)
COL_TOTAL = "Total"              # total debt (level); omit if COL_FOREIGN is already %


def _name_to_iso3() -> dict:
    """Map AT country labels to our ISO3. Extend for names that don't match."""
    out = {}
    for c in get_countries():
        out[c["name"].lower()] = c["iso3"]
        out[c["iso3"].lower()] = c["iso3"]
        if c.get("iso2"):
            out[c["iso2"].lower()] = c["iso3"]
    return out


def _parse_quarter(v) -> pd.Timestamp | None:
    try:
        return pd.Period(str(v).replace("-", ""), freq="Q").end_time.normalize()
    except Exception:
        try:
            return pd.Timestamp(v).normalize()
        except Exception:
            return None


def load(xlsx: Path) -> pd.DataFrame:
    """Read the AT workbook into a canonical macro_panel frame. TEMPLATE — verify."""
    raw = pd.read_excel(xlsx, sheet_name=SHEET)
    iso = _name_to_iso3()
    now = datetime.now(timezone.utc)
    rows = []
    for _, r in raw.iterrows():
        c3 = iso.get(str(r.get(COL_COUNTRY, "")).strip().lower())
        dt = _parse_quarter(r.get(COL_DATE))
        if c3 is None or dt is None:
            continue
        foreign = pd.to_numeric(r.get(COL_FOREIGN), errors="coerce")
        if pd.isna(foreign):
            continue
        if COL_TOTAL in raw.columns:
            total = pd.to_numeric(r.get(COL_TOTAL), errors="coerce")
            if pd.isna(total) or total == 0:
                continue
            share = 100.0 * foreign / total
        else:
            share = float(foreign)          # already a percentage
        rows.append({
            "date": dt.date(), "country_iso3": c3, "indicator_id": INDICATOR_ID,
            "value": round(float(share), 2), "indicator_name": INDICATOR_NAME,
            "pillar": "markets", "orientation": -1, "source": "arslanalp_tsuda",
            "provider_dataset": "AT_investor_base", "provider_code": "manual_xlsx",
            "unit": "percent", "frequency": "Q", "updated_at": now,
        })
    return pd.DataFrame(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Manual ingest of the AT investor-base XLSX")
    p.add_argument("xlsx", type=Path)
    p.add_argument("--dry-run", action="store_true", help="parse and print, do not write")
    p.add_argument("--db")
    args = p.parse_args()

    df = load(args.xlsx)
    if df.empty:
        print("No rows parsed — check SHEET / COLUMN mapping against the workbook.")
        return 1
    print(f"Parsed {len(df)} rows, {df['country_iso3'].nunique()} countries, "
          f"{df['date'].min()}..{df['date'].max()}")
    print(df.groupby('country_iso3')['value'].last().sort_values(ascending=False).head(10))
    if args.dry_run:
        print("(dry-run: nothing written)")
        return 0
    con = get_conn(args.db)
    try:
        added, updated = upsert(con, "macro_panel", df)
        print(f"macro_panel <- nonresident_debt_share: +{added}/{updated}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
