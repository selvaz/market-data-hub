"""Reviewed US monthly editions. Dates are issuer dates, never retrieval dates.

Reviewed 2026-10-24 against the linked issuer schedules. Only released editions
are enabled. A new headline month or publication date must be reviewed first.
"""
from .http import FetchError

BLS_SCHEDULE = "https://www.bls.gov/schedule/2026/home.htm"
CENSUS_SCHEDULE = "https://www.census.gov/economic-indicators/calendar-listview-2026.html"
BEA_SCHEDULE = "https://www.bea.gov/news/schedule/full"
FED_SCHEDULE = "https://www.federalreserve.gov/releases/g17/"

# edition -> (headline reference month, event stage, New York local time)
EDITIONS = {
    # https://www.bls.gov/schedule/2026/home.htm (national JOLTS)
    "us_ppi": {"2026-08-13": ("2026-07", "regular", "08:30"),
               "2026-09-10": ("2026-08", "regular", "08:30"),
               "2026-10-15": ("2026-09", "regular", "08:30")},
    "us_jolts": {"2026-08-04": ("2026-06", "regular", "10:00"),
                 "2026-09-01": ("2026-07", "regular", "10:00"),
                 "2026-09-29": ("2026-08", "regular", "10:00")},
    # https://www.bea.gov/news/schedule/full (September PCE: October 29)
    "us_pce": {"2026-08-26": ("2026-07", "regular", "08:30"),
               "2026-09-30": ("2026-08", "regular", "08:30")},
    # https://www.census.gov/economic-indicators/calendar-listview-2026.html
    # Includes the M3 full reports at 10:00.
    "us_retail_sales": {"2026-08-14": ("2026-07", "advance", "08:30"),
                        "2026-09-16": ("2026-08", "advance", "08:30"),
                        "2026-10-15": ("2026-09", "advance", "08:30")},
    "us_housing_starts": {"2026-08-18": ("2026-07", "regular", "08:30"),
                          "2026-09-17": ("2026-08", "regular", "08:30"),
                          "2026-10-20": ("2026-09", "regular", "08:30")},
    "us_durable_goods": {"2026-08-04": ("2026-06", "final", "10:00"),
                         "2026-08-26": ("2026-07", "advance", "08:30"),
                         "2026-09-02": ("2026-07", "final", "10:00"),
                         "2026-09-25": ("2026-08", "advance", "08:30"),
                         "2026-10-02": ("2026-08", "final", "10:00")},
    # https://www.federalreserve.gov/releases/g17/ (09:15 monthly releases)
    "us_industrial_production": {"2026-08-18": ("2026-07", "regular", "09:15"),
                                 "2026-09-18": ("2026-08", "regular", "09:15"),
                                 "2026-10-16": ("2026-09", "regular", "09:15")},
    # https://www.bea.gov/news/schedule/full (joint Census/BEA publication)
    "us_trade_balance": {"2026-08-04": ("2026-06", "regular", "08:30"),
                         "2026-09-03": ("2026-07", "regular", "08:30"),
                         "2026-10-06": ("2026-08", "regular", "08:30")},
}

SCHEDULES = {f: BLS_SCHEDULE if f in ("us_ppi", "us_jolts") else
             BEA_SCHEDULE if f in ("us_pce", "us_trade_balance") else
             FED_SCHEDULE if f == "us_industrial_production" else CENSUS_SCHEDULE
             for f in EDITIONS}


def reviewed(family, edition):
    try:
        return EDITIONS[family][edition]
    except KeyError:
        raise FetchError(f"US publication metadata needs review: {family} {edition}") from None


def observation_stage(family, edition, period):
    head, stage, _ = reviewed(family, edition)
    if period > head:
        raise FetchError(f"US headline period needs review: {family} {period}")
    if period != head and family in ("us_retail_sales", "us_durable_goods"):
        return "revised" if family == "us_retail_sales" else "final"
    return stage
