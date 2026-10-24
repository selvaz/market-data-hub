"""Reviewed central-bank meeting calendars, through 2026-12-31.

Meeting end dates are decision periods, never observation months. BOJ publishes
after the meeting, with no fixed clock; None must never become an exact time.
"""
SCHEDULES = {
    "pboc_lpr": "https://www.chinamoney.com.cn/chinese/bklpr/",
    "boc_policy": "https://www.bankofcanada.ca/2025/08/bank-canada-publishes-2026-schedule-policy-interest-rate-announcements-other-major-publications/",
    "boe_policy": "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates",
    "ecb_policy": "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
    "fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
}
ZONES = {"fomc": "America/New_York", "ecb_policy": "Europe/Berlin", "boe_policy": "Europe/London"}
ZONES["boc_policy"] = "America/Toronto"
ZONES["pboc_lpr"] = "Asia/Shanghai"
EDITIONS = {
    # Past dates independently printed in the PBOC archive (including Sept 20).
    # Nov 20 / Dec 21 follow NIFC's published 20th/next-business-day rule.
    "pboc_lpr": {d: (d, "final", "09:00") for d in (
        "2026-01-20", "2026-02-24", "2026-03-20", "2026-04-20",
        "2026-05-20", "2026-06-22", "2026-07-20", "2026-08-20",
        "2026-09-20", "2026-10-20", "2026-11-20", "2026-12-21")},
    "boc_policy": {d: (d, "final", "09:45") for d in (
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-10",
        "2026-07-15", "2026-09-02", "2026-10-28", "2026-12-09")},
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
