"""Reviewed M9b issuer editions through 2026-12-31 (reviewed 2026-10-24).

Dates authorize a fetch; the publication must independently confirm its edition.
No dataset update or retrieval timestamp is promoted to a release date.
"""
EUROSTAT_SCHEDULE = "https://ec.europa.eu/eurostat/news/euro-indicators/release-calendar"
EDITIONS = {
    "us_umich_sentiment": {
        "2026-09-11": ("2026-09", "preliminary", "10:00"),
        "2026-09-25": ("2026-09", "final", "10:00"),
        "2026-10-09": ("2026-10", "preliminary", "10:00"),
        "2026-10-23": ("2026-10", "final", "10:00"),
        "2026-11-06": ("2026-11", "preliminary", "10:00"),
        "2026-11-20": ("2026-11", "final", "10:00"),
        "2026-12-04": ("2026-12", "preliminary", "10:00"),
        "2026-12-18": ("2026-12", "final", "10:00"),
    },
    "ea_ec_surveys": {
        "2026-09-29": ("2026-09", "final", "11:00"),
        "2026-10-22": ("2026-10", "flash", "16:00"),
        "2026-10-29": ("2026-10", "final", "11:00"),
        "2026-11-20": ("2026-11", "flash", "16:00"),
        "2026-11-27": ("2026-11", "final", "11:00"),
        "2026-12-21": ("2026-12", "flash", "16:00"),
        # December final/ESI is published 2027-01-07, outside this calendar.
    },
    "uk_total_pay": {
        "2026-09-15": ("2026-07", "final", "07:00"),
        "2026-10-20": ("2026-08", "final", "07:00"),
        "2026-11-17": ("2026-09", "final", "07:00"),
        "2026-12-15": ("2026-10", "final", "07:00"),
    },
    "ea_core_hicp": {
        "2026-10-02": ("2026-09", "flash", "11:00"),
        "2026-10-16": ("2026-09", "final", "11:00"),
        "2026-11-04": ("2026-10", "flash", "11:00"),
        "2026-11-18": ("2026-10", "final", "11:00"),
        "2026-12-01": ("2026-11", "flash", "11:00"),
        "2026-12-17": ("2026-11", "final", "11:00"),
    },
    "us_new_home_sales": {
        "2026-08-25": ("2026-07", "final", "10:00"),
        "2026-09-24": ("2026-08", "final", "10:00"),
        "2026-10-27": ("2026-09", "final", "10:00"),
        "2026-11-25": ("2026-10", "final", "10:00"),
        "2026-12-23": ("2026-11", "final", "10:00"),
    },
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
SCHEDULES["us_new_home_sales"] = "https://www.census.gov/economic-indicators/calendar-listview-2026.html"
SCHEDULES["uk_total_pay"] = "https://www.ons.gov.uk/releasecalendar?keywords=labour+market&release-type=type-upcoming"
SCHEDULES["ea_ec_surveys"] = "https://ec.europa.eu/economy_finance/db_indicators/surveys/documents/calendar/Publication%20dates%202026.pdf"
SCHEDULES["us_umich_sentiment"] = "https://data.sca.isr.umich.edu/fetchdoc.php?docid=79628"
