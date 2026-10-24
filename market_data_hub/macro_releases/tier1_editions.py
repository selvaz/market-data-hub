"""Reviewed M9b issuer editions through 2026-12-31 (reviewed 2026-10-24).

Dates authorize a fetch; the publication must independently confirm its edition.
No dataset update or retrieval timestamp is promoted to a release date.
"""
EUROSTAT_SCHEDULE = "https://ec.europa.eu/eurostat/news/euro-indicators/release-calendar"
EDITIONS = {
    "ea_unemployment": {
        "2026-09-01": ("2026-07", "final", "11:00"),
        "2026-10-01": ("2026-08", "final", "11:00"),
        "2026-10-30": ("2026-09", "final", "11:00"),
        "2026-12-02": ("2026-10", "final", "11:00"),
    },
}
SCHEDULES = {"ea_unemployment": EUROSTAT_SCHEDULE}
