"""Reviewed JPN/CHN editions to 2026-12-31; calendars do not prove values.

NBS English publications can follow the domestic calendar. Historical dates
below are the dates printed on the saved English publications; future dates
are permissions from the issuer calendar and require publication review.
"""
NBS_CALENDAR = "https://www.stats.gov.cn/english/PressRelease/ReleaseCalendar/202512/t20251226_1962154.html"
EDITIONS = {
    "cn_activity": {
        "2026-09-15": ("2026-08", "final", "10:00"),
        "2026-10-19": ("2026-09", "final", "10:00"),
        "2026-11-16": ("2026-10", "final", "10:00"),
        "2026-12-15": ("2026-11", "final", "10:00"),
    },
    "cn_nbs_pmi": {
        "2026-09-01": ("2026-08", "final", "09:30"),
        "2026-10-01": ("2026-09", "final", "09:30"),
        "2026-10-31": ("2026-10", "final", "09:30"),
        "2026-11-30": ("2026-11", "final", "09:30"),
        "2026-12-31": ("2026-12", "final", "09:30"),
    },
    "cn_cpi": {
        "2026-09-10": ("2026-08", "final", "09:30"),
        "2026-10-15": ("2026-09", "final", "09:30"),
        "2026-11-09": ("2026-10", "final", "09:30"),
        "2026-12-09": ("2026-11", "final", "09:30"),
    },
}
SCHEDULES = {family: NBS_CALENDAR for family in EDITIONS}
ZONES = {family: "Asia/Shanghai" for family in EDITIONS}


def reviewed(family, edition):
    from .http import FetchError
    if edition not in EDITIONS.get(family, {}):
        raise FetchError("Asia issuer publication edition needs review")
    return EDITIONS[family][edition]
