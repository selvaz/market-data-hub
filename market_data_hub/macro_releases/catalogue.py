"""Reviewed exact measures for the M5 slice; stages never enter indicator ids."""
from .models import Indicator


def measure(id, issuer, geo, transform, adjustment, unit="%", scale=1, definition=None):
    return Indicator(id=id, measure=id, issuer_id=issuer, geography=geo,
                     seasonal_adjustment=adjustment, transform=transform, unit=unit,
                     scale=scale, frequency="Q" if transform == "qq_saar" else "M",
                     tier="T1", definition=definition or id, catalogue_status="supported", active=True)


INDICATORS = {
    i.id: i for i in [
        *[measure(f"us_cpi_{part}_{basis}", "BLS", "USA", basis, adjustment)
          for part in ("headline", "core") for basis, adjustment in (("mm", "SA"), ("yy", "NSA"))],
        measure("us_payrolls_change", "BLS", "USA", "change", "SA", "persons", 1000,
                "CES total nonfarm employment, monthly net change, thousands of persons"),
        measure("us_gdp_qq_saar", "BEA", "USA", "qq_saar", "SA",
                definition="NIPA T10101, line 1 A191RL, published percent change at annual rate"),
        measure("ea_hicp_yy", "Eurostat", "EA20", "yy", "NSA"),
        measure("ea_hicp_mm", "Eurostat", "EA20", "mm", "NSA"),
        measure("ea21_hicp_yy", "Eurostat", "EA21", "yy", "NSA"),
        measure("ea21_hicp_mm", "Eurostat", "EA21", "mm", "NSA"),
        measure("uk_gdp_mm", "ONS", "GBR", "mm", "SA"),
        measure("uk_gdp_index", "ONS", "GBR", "level", "SA", "index"),
    ]
}

BLS_SERIES = {
    "CUSR0000SA0": ("us_cpi_headline_mm", "pct_changes", "1"),
    "CUUR0000SA0": ("us_cpi_headline_yy", "pct_changes", "12"),
    "CUSR0000SA0L1E": ("us_cpi_core_mm", "pct_changes", "1"),
    "CUUR0000SA0L1E": ("us_cpi_core_yy", "pct_changes", "12"),
    "CES0000000001": ("us_payrolls_change", "net_changes", "1"),
}

FAMILIES = ("us_cpi", "us_payrolls", "us_gdp", "ea_hicp", "uk_gdp")


def hicp_indicator(period, basis, geography="EA"):
    """Resolve headline membership by period; preserve fixed EA20 golden identity."""
    if geography == "EA":
        if "2023-01" <= period <= "2025-12":
            geography = "EA20"
        elif "2026-01" <= period <= "2026-12":
            geography = "EA21"
        else:
            raise ValueError(f"Euro area composition needs review: {period}")
    if geography not in ("EA20", "EA21"):
        raise ValueError("Unsupported euro area composition")
    prefix = "ea" if geography == "EA20" else "ea21"
    return INDICATORS[f"{prefix}_hicp_{basis}"]
