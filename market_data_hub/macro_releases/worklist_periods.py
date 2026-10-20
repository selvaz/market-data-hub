"""Explicit legacy period spellings, without release-date or year inference."""
from datetime import date
import re
import unicodedata


def fold(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(c))


MONTHS = {}
for n, words in enumerate((
    'january jan enero gennaio janvier janeiro januar',
    'february feb febrero febbraio fevrier fevereiro februar',
    'march mar marzo mars marco marz',
    'april apr abril aprile avril', 'may mayo maggio mai maio',
    'june jun junio giugno juin junho juni', 'july jul julio luglio juillet julho juli',
    'august aug agosto aout', 'september sep sept septiembre settembre septembre setembro set',
    'october oct octubre ottobre octobre outubro oktober',
    'november nov noviembre novembre novembro', 'december dec diciembre dicembre decembre dezembro dezember'), 1):
    MONTHS.update({word: n for word in words.split()})


def frequency(value):
    value = value.strip().casefold()
    return {'monthly': 'M', 'quarterly': 'Q', 'annual': 'A', 'yearly': 'A', 'y': 'A',
            'daily': 'D', 'weekly': 'W', 'biweekly': 'W', 'bi-weekly': 'W', 'fortnightly': 'W',
            'e': 'IRREGULAR', 'event': 'IRREGULAR', 'aperiodic': 'IRREGULAR',
            'per meeting': 'IRREGULAR', 'every 6 weeks': 'IRREGULAR',
            '8 times per year': 'IRREGULAR', 'bi-monthly': 'IRREGULAR'}.get(value, value.upper())


def parsed_period(raw, geography=None):
    if not isinstance(raw, str):
        return None, None
    text = fold(raw.strip())
    text = re.sub(r'^week\s+ending\s+', '', text)
    if re.fullmatch(r'[0-9]{4}', text) and int(text) > 0:
        return text, 'A'
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', text):
        try:
            return date.fromisoformat(text).isoformat(), 'D'
        except ValueError:
            return None, None
    month = re.fullmatch(r'([0-9]{4})[-.]([0-9]{1,2})', text)
    if month:
        year, number = map(int, month.groups())
    else:
        month = re.fullmatch(r'([0-9]{1,2})/([0-9]{4})', text)
        if month:
            number, year = map(int, month.groups())
        else:
            month = re.fullmatch(r'([a-z]+)\.?[\s-]+(?:de\s+)?([0-9]{4})', text)
            year, number = (int(month[2]), MONTHS.get(month[1], 0)) if month else (0, 0)
    if year and number:
        try:
            date(year, number, 1)
            return f'{year:04}-{number:02}', 'M'
        except ValueError:
            return None, None
    quarter = re.fullmatch(r'([0-9]{4})[-\s]?q([1-4])', text)
    if quarter:
        return f'{quarter[1]}-Q{quarter[2]}', 'Q'
    quarter = re.fullmatch(r'(?:q|quarter\s+)([1-4])[/\s]+([0-9]{4})', text)
    if not quarter:
        quarter = re.fullmatch(r'([1-4])(?:st|nd|rd|th)?\s+quarter\s+(?:of\s+)?([0-9]{4})', text)
    if quarter:
        return f'{quarter[2]}-Q{quarter[1]}', 'Q'
    quarter = re.fullmatch(r'(first|second|third|fourth|i|ii|iii|iv|primero|segundo|tercero|cuarto)\s+'
                           r'(?:quarter|trimestre)\s+(?:(?:of|de)\s+)?([0-9]{4})', text)
    if quarter:
        words = [('first', 'i', 'primero'), ('second', 'ii', 'segundo'),
                 ('third', 'iii', 'tercero'), ('fourth', 'iv', 'cuarto')]
        n = next(n for n, names in enumerate(words, 1) if quarter[1] in names)
        return f'{quarter[2]}-Q{n}', 'Q'
    roc = re.fullmatch(r'(?:roc\s*|民國|民国)?([0-9]{2,4})(?:年|/)([0-9]{1,2})月?', text)
    if not roc and geography == 'TWN':
        roc = re.fullmatch(r'([0-9]{3})([0-9]{2})', text)
    if roc:
        year, month = map(int, roc.groups())
        if year < 1000:
            if geography != 'TWN' and not text.startswith(('roc', '民國', '民国')):
                return None, None
            year += 1911
        try:
            date(year, month, 1)
            return f'{year:04}-{month:02}', 'M'
        except ValueError:
            return None, None
    day = re.fullmatch(r'([a-z]+)\.?\s+([0-9]{1,2}),?\s+([0-9]{4})', text)
    if day:
        month, number, year = MONTHS.get(day[1], 0), int(day[2]), int(day[3])
    else:
        day = re.fullmatch(r'([0-9]{1,2})\s+(?:de\s+)?([a-z]+)\.?\s+(?:de\s+)?([0-9]{4})', text)
        number, month, year = (int(day[1]), MONTHS.get(day[2], 0), int(day[3])) if day else (0, 0, 0)
    try:
        return date(year, month, number).isoformat(), 'D'
    except ValueError:
        return None, None


def explicit_period(raw, freq, geography=None):
    period, kind = parsed_period(raw, geography)
    freq = frequency(freq)
    if period and not period.startswith('0000') and (freq in {'UNKNOWN', 'IRREGULAR'} or
            freq == kind or (kind == 'D' and freq == 'W')):
        return period
    return None


PERIOD_REASONS = {
    "period_year_missing",
    "period_is_cumulative_window",
    "period_is_rolling_window",
    "period_is_submonthly_window",
    "period_is_document_identifier",
    "period_is_maturity_date",
    "period_fiscal_year_ambiguous",
    "period_survey_quarter_unspecified",
}
NON_PERIOD_REASONS = {"period_is_document_identifier", "period_is_maturity_date"}


def period_text(raw):
    """Normalize only punctuation seen in the imported period inventory."""
    return fold(raw.strip().replace("–", "-").replace("—", "-"))


def catalogue_windows(measure, definition):
    """Require a measure field or a definitional phrase, never a passing mention."""
    ytd = bool(
        re.fullmatch(
            r"(?:[^.;]+(?:, |\())?(?:ytd|year-to-date)(?: (?:y/y|flow|level|growth|total))?\)?",
            measure,
        )
        or re.match(
            r"^(?:cumulative year-to-date|year-to-date (?:year-on-year |net new )?(?:growth|change|flow|level|total))\b",
            definition,
        )
        or re.fullmatch(
            r"cumulative [^.;]+ from the start of the calendar year through the reporting period\.?",
            definition,
        )
    )
    three_month = bool(
        re.search(r"\b3m/3m\)?$", measure)
        or re.search(
            r"\b(?:three|3)[ -]month (?:moving |rolling )?(?:average|total|growth)(?: \(3m/3m\))?$",
            measure,
        )
        or re.match(
            r"^(?:three|3)[ -]month(?:-on-(?:three|3)-month change| (?:moving|rolling) (?:average|total))\b",
            definition,
        )
        or re.fullmatch(
            r"(?:growth rate|percentage change) [^.;]+ over the latest (?:rolling )?three months compared with the previous three months(?:, [^.;]+)?\.?",
            definition,
        )
        or re.fullmatch(
            r"[^.;]+, reported as a rolling (?:three|3)-month (?:total|average)(?:;[^.]+)?\.?",
            definition,
        )
    )
    calendar_quarter = bool(
        re.fullmatch(
            r"(?:the )?(?:reference period|release) is (?:the|a) calendar quarter\.?",
            definition,
        )
    )
    return ytd, three_month, calendar_quarter


def window_period(text, freq, definition, measure="", named_quarter=None):
    """Return a window's reporting-month index only for its catalogue definition.

    A monthly publication frequency alone says nothing about the measured window.
    None means this text is not a recognized window spelling.
    """
    span = re.fullmatch(r"([a-z]+)(?:\s*-\s*|\s+to\s+)([a-z]+)\s+([0-9]{4})", text)
    rolling = re.fullmatch(
        r"(?:([0-9]+|three) months to|quarter end(?:ing|ed)) "
        r"([a-z]+) ([0-9]{4})",
        text,
    )
    mobile = re.fullmatch(r"trimestre movel ([a-z]+)-([a-z]+) ([0-9]{4})", text)
    quarters = re.fullmatch(r"first three quarters of ([0-9]{4})", text)
    if not any((span, rolling, mobile, quarters)):
        return None
    start = None
    duration = None
    if span or mobile:
        match = span or mobile
        start, end, year = (
            MONTHS.get(match[1], 0),
            MONTHS.get(match[2], 0),
            int(match[3]),
        )
        duration = end - start + 1
        valid = start and start <= end
    elif quarters:
        start, end, year, duration, valid = 1, 9, int(quarters[1]), 9, True
    else:
        end, year = MONTHS.get(rolling[2], 0), int(rolling[3])
        duration = 3 if not rolling[1] or rolling[1] == "three" else int(rolling[1])
        valid = duration > 0
    if not valid or not explicit_period(f"{year:04}-{end:02}", "M"):
        return None, "period_format_unrecognised"
    cumulative = start == 1 and not mobile
    reason = "period_is_cumulative_window" if cumulative else "period_is_rolling_window"
    ytd, three_month, calendar_quarter = catalogue_windows(measure, definition)
    if freq == "M" and ((cumulative and ytd) or (duration == 3 and three_month)):
        return f"{year:04}-{end:02}", None
    if freq == "Q" and span and start in {1, 4, 7, 10} and duration == 3:
        quarter = f"{year:04}-Q{(start - 1) // 3 + 1}"
        if named_quarter:
            return (
                (quarter, None)
                if quarter == named_quarter
                else (None, "period_frequency_mismatch")
            )
        if calendar_quarter:
            return quarter, None
    return None, reason


def period_evidence(raw, freq, geography=None, definition="", *, measure=""):
    """Classify a v3 period, retaining semantic failures instead of guessing.

    Catalogue measure and definition remain separate fields. Partial labels
    return a separate reason so the caller can compare their named component
    with independently explicit evidence; they never supply a year themselves.
    """
    if raw is None or raw == "":
        return None, None
    if not isinstance(raw, str):
        return None, "period_format_unrecognised"
    text = period_text(raw)
    if (
        not text
        or text == "missing"
        or re.fullmatch(r"legacy-occurrence:[0-9]+", text)
        or text.startswith("d:")
    ):
        return None, None
    freq, definition, measure = frequency(freq), fold(definition), fold(measure)
    if re.fullmatch(r"issue(?: number)? [0-9]+", text):
        return None, "period_is_document_identifier"
    if re.fullmatch(r"bond maturing [0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return None, "period_is_maturity_date"
    if (
        re.fullmatch(r"[0-9]{4}-[0-9]{2}", text)
        and int(text[-2:]) == (int(text[:4]) + 1) % 100
        and not parsed_period(text)[0]
    ):
        return None, "period_fiscal_year_ambiguous"
    # Explicit redundant quarter annotations must agree with the spelled range/month.
    annotation = re.fullmatch(r"(.+) \((q[1-4] [0-9]{4})\)", text)
    survey = re.fullmatch(r"(q[1-4] [0-9]{4}) \(([a-z]+) survey\)", text)
    if annotation:
        quarter = explicit_period(annotation[2], "Q")
        result = window_period(
            annotation[1], freq, definition, measure, named_quarter=quarter
        )
        period, reason = (
            result
            if result is not None
            else period_evidence(
                annotation[1], freq, geography, definition, measure=measure
            )
        )
        return (
            (period, None)
            if period and period == quarter
            else (None, reason or "period_frequency_mismatch")
        )
    if survey:
        quarter = explicit_period(survey[1], freq)
        month = MONTHS.get(survey[2], 0)
        if quarter and month and int(quarter[-1]) == (month - 1) // 3 + 1:
            return quarter, None
        return None, "period_frequency_mismatch"
    result = window_period(text, freq, definition, measure)
    if result is not None:
        return result
    roman = re.fullmatch(r"q(i|ii|iii|iv) ([0-9]{4})", text)
    if roman:
        text = f"q{('i', 'ii', 'iii', 'iv').index(roman[1]) + 1} {roman[2]}"
    half = re.fullmatch(r"primera quincena de ([a-z]+) de ([0-9]{4})", text)
    if half:
        month = MONTHS.get(half[1], 0)
        if (
            freq == "W"
            and ("first half" in definition or "first-half" in definition)
            and month
        ):
            period = explicit_period(f"{half[2]}-{month:02}-15", "W")
            return period, None if period else "period_format_unrecognised"
        return None, "period_is_submonthly_window"
    text = re.sub(r"^(?:week ending|as on|semana al|al)\s+", "", text)
    text = re.sub(r" \((?:advance|p|prelim)\)$", "", text)
    # V3 punctuation repair must not alter the shared v2 explicit_period parser.
    text = re.sub(r"^([a-z]+)\.?[,] ([0-9]{4})$", r"\1 \2", text)
    survey_month = re.fullmatch(r"([a-z]+)\.? ([0-9]{4}) survey", text)
    if survey_month:
        month = explicit_period(f"{survey_month[1]} {survey_month[2]}", "M")
        if month and freq == "Q":
            return None, "period_survey_quarter_unspecified"
        if month and freq in {"M", "UNKNOWN", "IRREGULAR"}:
            return month, None
        return None, "period_frequency_mismatch"
    event = freq == "IRREGULAR" or (
        freq == "M"
        and any(
            word in f"{measure} {definition}" for word in ("meeting", "rate decision")
        )
    )
    meeting = re.search(r"\b(?:meeting|mpc|fomc|decision)\b", text)
    if meeting:
        if not event:
            return None, "period_frequency_mismatch"
        detail = re.fullmatch(
            r"([a-z]+) ([0-9]{4}) mpc meeting \(([a-z]+) ([0-9]{1,2})-([0-9]{1,2})\)",
            text,
        )
        if detail:
            try:
                month = MONTHS.get(detail[1], 0)
                if month != MONTHS.get(detail[3], 0) or int(detail[4]) > int(detail[5]):
                    raise ValueError("contradictory meeting dates")
                date(int(detail[2]), month, int(detail[4]))
                date(int(detail[2]), month, int(detail[5]))
            except ValueError:
                return None, "period_format_unrecognised"
            text = text[: text.index(" (")]
        text = re.sub(r"^(?:fomc meeting|meeting of) ", "", text)
        text = re.sub(r" (?:fomc meeting|mpc meeting|mpc|meeting|decision)$", "", text)
    # Only ranges use a month index. Single dates always retain their day.
    month_first = re.fullmatch(
        r"([a-z]+)\.? ([0-9]{1,2})(?:-([0-9]{1,2}))?,? ([0-9]{4})", text
    )
    day_first = re.fullmatch(
        r"([0-9]{1,2})(?:-([0-9]{1,2}))? ([a-z]+)\.? ([0-9]{4})", text
    )
    if event and (month_first or day_first) and "-" in text:
        if month_first:
            word, first, last, year = month_first.groups()
        else:
            first, last, word, year = day_first.groups()
        month = MONTHS.get(word, 0)
        try:
            date(int(year), month, int(first))
            date(int(year), month, int(last or first))
            if last and int(last) < int(first):
                raise ValueError("reversed range")
            return f"{year}-{month:02}", None
        except ValueError:
            return None, "period_format_unrecognised"
    period = explicit_period(text, freq, geography)
    if period:
        return period, None
    if partial_period(text):
        return None, "period_year_missing"
    return None, (
        "period_frequency_mismatch"
        if parsed_period(text, geography)[0]
        else "period_format_unrecognised"
    )


def partial_period(text):
    """The explicit month/day or quarter in a label with no year."""
    text = re.sub(r"^week ending\s+", "", period_text(text))
    month = re.fullmatch(r"([a-z]+)\.?(?: ([0-9]{1,2}))?", text)
    if month and month[1] in MONTHS and (not month[2] or 1 <= int(month[2]) <= 31):
        return "M", MONTHS[month[1]], int(month[2]) if month[2] else None
    quarter = re.fullmatch(r"(first|second|third|fourth) quarter", text)
    if quarter:
        return "Q", ("first", "second", "third", "fourth").index(quarter[1]) + 1, None
    return None


def partial_agrees(raw, period):
    kind, number, day = partial_period(raw)
    if kind == "Q":
        return period.endswith(f"-Q{number}")
    if len(period) not in {7, 10} or period[4] != "-" or not period[5:7].isdigit():
        return False
    return int(period[5:7]) == number and (day is None or period[8:] == f"{day:02}")
