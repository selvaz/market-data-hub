"""Missing catalogue total-pay measure; never substitute regular pay."""
from .ons_rates import parse_rate
from .tier1_editions import EDITIONS

PATH = "/employmentandlabourmarket/peopleinwork/earningsandworkinghours/timeseries/kac3/lms"
SPEC = {"KAC3": ("uk_total_pay", "uk_average_weekly_earnings_yy", "LMS",
    "AWE: Whole Economy Year on Year Three Month Average Growth (%): Seasonally Adjusted Total Pay Excluding Arrears", PATH)}


def parse(document, family="uk_total_pay", since="1900-01"):
    return parse_rate(document, "KAC3", since, series=SPEC, editions=EDITIONS)


def fetch(client, family, since):
    return parse(client.get("ons", "https://www.ons.gov.uk" + PATH + "/data"), family, since)
