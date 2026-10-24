"""Reviewed M9b issuer editions through 2026-12-31 (reviewed 2026-10-24).

Dates authorize a fetch; the publication must independently confirm its edition.
No dataset update or retrieval timestamp is promoted to a release date.
"""
EUROSTAT_SCHEDULE = "https://ec.europa.eu/eurostat/news/euro-indicators/release-calendar"
EDITIONS = {
    "uk_retail_sales": {
        "2026-09-18": ("2026-08", "final", "07:00"),
        "2026-10-23": ("2026-09", "final", "07:00"),
        "2026-11-20": ("2026-10", "final", "07:00"),
        "2026-12-18": ("2026-11", "final", "07:00"),
    },
    "ea_trade_balance": {
        "2026-09-15": ("2026-07", "final", "11:00"),
        "2026-10-16": ("2026-08", "final", "11:00"),
        "2026-11-13": ("2026-09", "final", "11:00"),
        "2026-12-16": ("2026-10", "final", "11:00"),
    },
    "ea_industrial_production": {
        "2026-09-16": ("2026-07", "final", "11:00"),
        "2026-10-15": ("2026-08", "final", "11:00"),
        "2026-11-16": ("2026-09", "final", "11:00"),
        "2026-12-15": ("2026-10", "final", "11:00"),
    },
    "ea_retail_sales": {
        "2026-09-04": ("2026-07", "final", "11:00"),
        "2026-10-06": ("2026-08", "final", "11:00"),
        "2026-11-06": ("2026-09", "final", "11:00"),
        "2026-12-04": ("2026-10", "final", "11:00"),
    },
    "ea_unemployment": {
        "2026-09-01": ("2026-07", "final", "11:00"),
        "2026-10-01": ("2026-08", "final", "11:00"),
        "2026-10-30": ("2026-09", "final", "11:00"),
        "2026-12-02": ("2026-10", "final", "11:00"),
    },
}
SCHEDULES = {family: EUROSTAT_SCHEDULE for family in EDITIONS}
SCHEDULES["uk_retail_sales"] = "https://www.ons.gov.uk/releasecalendar?keywords=retail%20sales&release-type=type-upcoming"
