"""Reviewed JPN/CHN editions to 2026-12-31; calendars do not prove values.

NBS English publications can follow the domestic calendar. Historical dates
below are the dates printed on the saved English publications; future dates
are permissions from the issuer calendar and require publication review.
"""
NBS_CALENDAR = "https://www.stats.gov.cn/english/PressRelease/ReleaseCalendar/202512/t20251226_1962154.html"
EDITIONS = {
    "jp_trade_balance": {
        "2026-09-29": ("2026-08", "revised", "09:30"),
        "2026-10-21": ("2026-09", "preliminary", "08:50"),
        "2026-10-29": ("2026-09", "revised", "09:30"),
        "2026-11-18": ("2026-10", "preliminary", "08:50"),
        "2026-11-27": ("2026-10", "revised", "09:30"),
        "2026-12-16": ("2026-11", "preliminary", "08:50"),
        "2026-12-25": ("2026-11", "revised", "09:30"),
    },
    "jp_tankan": {
        "2026-07-01": ("2026-Q2", "final", "08:50"),
        "2026-10-01": ("2026-Q3", "final", "08:50"),
        "2026-12-14": ("2026-Q4", "final", "08:50"),
    },
    "jp_tokyo_cpi": {
        "2026-08-28": ("2026-08", "preliminary", "08:30"),
        "2026-10-02": ("2026-09", "preliminary", "08:30"),
        "2026-10-30": ("2026-10", "preliminary", "08:30"),
        "2026-11-27": ("2026-11", "preliminary", "08:30"),
        "2026-12-25": ("2026-12", "preliminary", "08:30"),
    },
    "jp_cpi": {
        "2026-09-18": ("2026-08", "final", "08:30"),
        "2026-10-23": ("2026-09", "final", "08:30"),
        "2026-11-20": ("2026-10", "final", "08:30"),
        "2026-12-18": ("2026-11", "final", "08:30"),
    },
    "jp_gdp": {
        "2026-08-17": ("2026-Q2", "preliminary", "08:50"),
        "2026-09-08": ("2026-Q2", "revised", "08:50"),
        "2026-11-16": ("2026-Q3", "preliminary", "08:50"),
        "2026-12-08": ("2026-Q3", "revised", "08:50"),
    },
    "cn_gdp": {
        "2026-07-16": ("2026-Q2", "preliminary", "09:30"),
        "2026-10-19": ("2026-Q3", "preliminary", "10:00"),
        "2026-10-20": ("2026-Q3", "preliminary", "09:30"),
        # Q4 preliminary accounting is released in January 2027.
    },
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
SCHEDULES["jp_gdp"] = "https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262/pdf/note_e.pdf"
SCHEDULES["jp_cpi"] = "https://www.stat.go.jp/english/data/cpi/1582.htm"
SCHEDULES["jp_tokyo_cpi"] = SCHEDULES["jp_cpi"]
SCHEDULES["jp_tankan"] = "https://www.boj.or.jp/en/statistics/outline/tkohyos.xlsx"
SCHEDULES["jp_trade_balance"] = "https://www.customs.go.jp/toukei/calendar/calend_e.htm"
ZONES = {family: "Asia/Tokyo" if family.startswith("jp_") else "Asia/Shanghai" for family in EDITIONS}


def reviewed(family, edition):
    from .http import FetchError
    if edition not in EDITIONS.get(family, {}):
        raise FetchError("Asia issuer publication edition needs review")
    return EDITIONS[family][edition]
