"""Reviewed central-bank meeting calendars, through 2026-12-31.

Meeting end dates are decision periods, never observation months. BOJ publishes
after the meeting, with no fixed clock; None must never become an exact time.
"""
SCHEDULES = {
    "boe_policy": "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates",
    "ecb_policy": "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
    "fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
}
ZONES = {"fomc": "America/New_York", "ecb_policy": "Europe/Berlin", "boe_policy": "Europe/London"}
EDITIONS = {
    "boe_policy": {d: (d, "final", "12:00") for d in (
        "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-18",
        "2026-07-30", "2026-09-17", "2026-11-05", "2026-12-17")},
    "ecb_policy": {d: (d, "final", "14:15") for d in (
        "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-11",
        "2026-07-23", "2026-09-10", "2026-10-29", "2026-12-17")},
    "fomc": {d: (d, "final", "14:00") for d in (
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09")},
}
