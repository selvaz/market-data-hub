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
}
SCHEDULES = {"us_fhfa_hpi": "https://www.fhfa.gov/data/hpi"}
SOURCES = {"us_fhfa_hpi": ("FHFA", "official", "M")}


def reviewed(family, edition):
    from .http import FetchError
    try:
        return EDITIONS[family][edition]
    except KeyError:
        raise FetchError("Housing publication edition needs review") from None
