"""Issuer calendars reviewed 2026-10-24 through 2026-12-31.

Only listed editions can be certified. Calendars permit a fetch, never prove a
publication. The saved issuer reports include the FHFA 2026 calendar.
"""
EDITIONS = {
    "us_fhfa_hpi": {
        "2026-08-25": ("2026-06", "regular", "09:00"),
        "2026-09-29": ("2026-07", "regular", "09:00"),
        "2026-10-27": ("2026-08", "regular", "09:00"),
        "2026-11-24": ("2026-09", "regular", "09:00"),
        "2026-12-29": ("2026-10", "regular", "09:00"),
    },
    # Census economic-indicators/calendar-listview-2026.html; monthly total,
    # all construction, seasonally adjusted annual rate, published m/m change.
    "us_construction_spending": {
        "2026-08-03": ("2026-06", "regular", "10:00"),
        "2026-09-01": ("2026-07", "regular", "10:00"),
        "2026-10-01": ("2026-08", "regular", "10:00"),
        "2026-11-02": ("2026-09", "regular", "10:00"),
        "2026-12-01": ("2026-10", "regular", "10:00"),
    },
}
SCHEDULES = {
    "us_fhfa_hpi": "https://www.fhfa.gov/data/hpi",
    "us_construction_spending": "https://www.census.gov/economic-indicators/calendar-listview-2026.html",
}
SOURCES = {"us_fhfa_hpi": ("FHFA", "official", "M"),
           "us_construction_spending": ("Census", "official", "M")}


def reviewed(family, edition):
    from .http import FetchError
    try:
        return EDITIONS[family][edition]
    except KeyError:
        raise FetchError("Housing publication edition needs review") from None
