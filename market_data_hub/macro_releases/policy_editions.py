"""Reviewed central-bank meeting calendars, through 2026-12-31.

Meeting end dates are decision periods, never observation months. BOJ publishes
after the meeting, with no fixed clock; None must never become an exact time.
"""
SCHEDULES = {
    "fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
}
ZONES = {"fomc": "America/New_York"}
EDITIONS = {
    "fomc": {d: (d, "final", "14:00") for d in (
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09")},
}
