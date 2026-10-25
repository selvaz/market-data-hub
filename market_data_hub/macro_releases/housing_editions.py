"""Issuer calendars reviewed 2026-10-24 through 2026-12-31.

Only listed editions can be certified. Calendars permit a fetch, never prove a
publication. The saved issuer reports include the FHFA 2026 calendar.
"""
EDITIONS = {
    # Eurostat eventsJson plus exact UTC publication metadata. The calendar
    # is reviewed July–December; the next HPI release is 11 January 2027.
    "ea_hpi": {
        "2026-07-02": ("2026-Q1", "regular", "11:00"),
        "2026-10-01": ("2026-Q2", "regular", "11:00"),
    },
    # Reviewed against the issuer's last-Tuesday, 09:00 ET rule printed in
    # both saved press releases; dates are enumerated, never forecast at run time.
    "us_case_shiller": {
        "2026-08-25": ("2026-06", "regular", "09:00"),
        "2026-09-29": ("2026-07", "regular", "09:00"),
        "2026-10-27": ("2026-08", "regular", "09:00"),
        "2026-11-24": ("2026-09", "regular", "09:00"),
        "2026-12-29": ("2026-10", "regular", "09:00"),
    },
    "us_pending_home_sales": {
        "2026-08-18": ("2026-07", "regular", "10:00"),
        "2026-09-17": ("2026-08", "regular", "10:00"),
        "2026-10-20": ("2026-09", "regular", "10:00"),
        "2026-11-18": ("2026-10", "regular", "10:00"),
        "2026-12-17": ("2026-11", "regular", "10:00"),
    },
    "us_existing_home_sales": {
        "2026-08-11": ("2026-07", "regular", "10:00"),
        "2026-09-10": ("2026-08", "regular", "10:00"),
        "2026-10-13": ("2026-09", "regular", "10:00"),
        "2026-11-12": ("2026-10", "regular", "10:00"),
        "2026-12-09": ("2026-11", "regular", "10:00"),
    },
    # Freddie Mac PMMS publication calendar: (survey week ENDING, stage, ET).
    # Thanksgiving publishes Wednesday 25 November, covering through Tuesday.
    "us_pmms": {
        "2026-08-06": ("2026-08-05", "regular", "12:00"),
        "2026-08-13": ("2026-08-12", "regular", "12:00"),
        "2026-08-20": ("2026-08-19", "regular", "12:00"),
        "2026-08-27": ("2026-08-26", "regular", "12:00"),
        "2026-09-03": ("2026-09-02", "regular", "12:00"),
        "2026-09-10": ("2026-09-09", "regular", "12:00"),
        "2026-09-17": ("2026-09-16", "regular", "12:00"),
        "2026-09-24": ("2026-09-23", "regular", "12:00"),
        "2026-10-01": ("2026-09-30", "regular", "12:00"),
        "2026-10-08": ("2026-10-07", "regular", "12:00"),
        "2026-10-15": ("2026-10-14", "regular", "12:00"),
        "2026-10-22": ("2026-10-21", "regular", "12:00"),
        "2026-10-29": ("2026-10-28", "regular", "12:00"),
        "2026-11-05": ("2026-11-04", "regular", "12:00"),
        "2026-11-12": ("2026-11-11", "regular", "12:00"),
        "2026-11-19": ("2026-11-18", "regular", "12:00"),
        "2026-11-25": ("2026-11-24", "regular", "12:00"),
        "2026-12-03": ("2026-12-02", "regular", "12:00"),
        "2026-12-10": ("2026-12-09", "regular", "12:00"),
        "2026-12-17": ("2026-12-16", "regular", "12:00"),
        "2026-12-24": ("2026-12-23", "regular", "12:00"),
        "2026-12-31": ("2026-12-30", "regular", "12:00"),
    },
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
    "ea_hpi": "https://ec.europa.eu/eurostat/news/euro-indicators/release-calendar",
    "us_case_shiller": "https://press.spglobal.com/2026-09-29-S-P-Cotality-Case-Shiller-Index-Reports-Annual-Gain-in-July-2026",
    "us_pending_home_sales": "https://www.nar.realtor/press-releases/nar-releases-2026-statistical-news-release-schedule",
    "us_existing_home_sales": "https://www.nar.realtor/press-releases/nar-releases-2026-statistical-news-release-schedule",
    "us_pmms": "https://www.freddiemac.com/pmms/docs/PMMS_Publication_Calendar.pdf",
    "us_fhfa_hpi": "https://www.fhfa.gov/data/hpi",
    "us_construction_spending": "https://www.census.gov/economic-indicators/calendar-listview-2026.html",
}
SOURCES = {"ea_hpi": ("Eurostat", "official", "Q"),
           "us_case_shiller": ("SPDJI", "private", "M"),
           "us_pending_home_sales": ("NAR", "private", "M"),
           "us_existing_home_sales": ("NAR", "private", "M"),
           "us_pmms": ("FreddieMac", "official", "W"),
           "us_fhfa_hpi": ("FHFA", "official", "M"),
           "us_construction_spending": ("Census", "official", "M")}


def reviewed(family, edition):
    from .http import FetchError
    try:
        return EDITIONS[family][edition]
    except KeyError:
        raise FetchError("Housing publication edition needs review") from None
