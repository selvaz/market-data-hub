"""Download and parse the authors' daily and monthly GPR workbooks."""

from __future__ import annotations

from io import BytesIO

import certifi
import pandas as pd
import requests

URLS = {
    "D": "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls",
    "M": "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls",
}


def parse(content: bytes, frequency: str, metadata: list[dict]) -> pd.DataFrame:
    """Parse published observations only; retain the workbook's period dates."""
    try:
        raw = pd.read_excel(BytesIO(content), engine="xlrd")
        date_col = "date" if frequency == "D" else "month"
        required = "GPRD" if frequency == "D" else "GPR"
        if date_col not in raw or required not in raw:
            raise ValueError("missing required columns")
        pieces = []
        for meta in metadata:
            sid = meta["symbol"]
            if meta["frequency"] != frequency or sid not in raw:
                continue
            selected = raw.loc[raw[sid].notna(), [date_col, sid]].copy()
            selected.columns = ["date", "value"]
            selected["date"] = pd.to_datetime(selected["date"], errors="raise")
            selected["value"] = pd.to_numeric(selected["value"], errors="raise")
            if selected["date"].isna().any() or selected["date"].duplicated().any():
                raise ValueError("missing or duplicate observation date")
            selected = selected.assign(
                series_id=sid,
                series_name=meta["name"],
                unit=meta["unit"],
                frequency=frequency,
                source="gpr",
                country=meta["country"],
            )
            pieces.append(selected)
        if not pieces or pieces[0].empty:
            raise ValueError("no observations")
        return pd.concat(pieces, ignore_index=True)
    except Exception as exc:
        raise ValueError(f"Invalid GPR {frequency} workbook: {exc}") from exc


def fetch(frequency: str, metadata: list[dict], timeout: int = 60) -> pd.DataFrame:
    """Use certifi explicitly: the Windows certificate store may be broken."""
    try:
        response = requests.get(
            URLS[frequency],
            timeout=timeout,
            verify=certifi.where(),
            headers={"User-Agent": "market-data-hub GPR collector/1.0"},
        )
        response.raise_for_status()
    except requests.RequestException:
        raise RuntimeError(f"GPR {frequency} download failed") from None
    return parse(response.content, frequency, metadata)
