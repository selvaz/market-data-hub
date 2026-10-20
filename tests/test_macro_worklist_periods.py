"""Real spellings from the 19 October pending worklist, with semantic guards."""

from types import SimpleNamespace

import pytest

from market_data_hub.macro_releases.worklist import decide
from market_data_hub.macro_releases.worklist_periods import period_evidence


@pytest.mark.parametrize(
    "raw",
    [
        "Sep 2026",
        "September 2026",
        "September, 2026",
        "Sept. 2026",
        "agosto de 2026",
        "Agosto de 2026",
        "Agosto 2026",
        "Septiembre de 2026",
        "septiembre de 2026",
        "August 2026",
        "8/2026",
        "Aug 2026 (advance)",
        "Aug 2026 (P)",
        "Oct 2026 (prelim)",
    ],
)
def test_explicit_month_spellings(raw):
    month = (
        "08"
        if any(w in raw.lower() for w in ("ago", "aug", "8/"))
        else "10"
        if raw.startswith("Oct")
        else "09"
    )
    assert period_evidence(raw, "M") == (f"2026-{month}", None)


@pytest.mark.parametrize(
    "raw",
    [
        "Sep 2026 Survey",
        "Sep 2026 survey",
        "September 2026 Survey",
        "Q3 2026 (Sep survey)",
        "Third Quarter of 2026",
        "QIII 2026",
    ],
)
def test_quarterly_survey_names_month_or_quarter(raw):
    assert period_evidence(
        raw, "quarterly", definition="Quarterly business survey"
    ) == ("2026-Q3", None)


def test_monthly_survey_and_frequency_gate():
    assert period_evidence("September 2026 Survey", "M") == ("2026-09", None)
    assert period_evidence("September 2026 Survey", "Q") == (
        None,
        "period_frequency_mismatch",
    )
    assert period_evidence("Q2 2026 (Sep survey)", "Q") == (
        None,
        "period_frequency_mismatch",
    )
    assert period_evidence("Apr-Jun 2026 (Q3 2026)", "Q") == (
        None,
        "period_frequency_mismatch",
    )
    assert period_evidence("Apr-Jun 2026 (Q2 2026)", "Q") == ("2026-Q2", None)


@pytest.mark.parametrize(
    "raw,month",
    [
        ("Jul 2026 meeting", "07"),
        ("Jul 30-31, 2026 meeting", "07"),
        ("July 30–31, 2026 meeting", "07"),
        ("Sep 2026 meeting", "09"),
        ("September 17–18, 2026 meeting", "09"),
        ("Sep 2026 FOMC meeting", "09"),
        ("Sep 15-16, 2026 FOMC meeting", "09"),
        ("Sep 15-16, 2026 meeting", "09"),
        ("September 15-16, 2026", "09"),
        ("Sep 16-17 2026 meeting", "09"),
        ("9-10 September 2026", "09"),
        ("9-10 Sep 2026 meeting", "09"),
        ("Meeting of 9-10 Sep 2026", "09"),
        ("FOMC meeting 15-16 Sep 2026", "09"),
        ("9 September 2026", "09"),
        ("9 September 2026 meeting", "09"),
        ("September 24, 2026 decision", "09"),
        ("Oct 2026 meeting", "10"),
        ("Oct 2026 MPC", "10"),
        ("Oct 2026 MPC meeting", "10"),
        ("Oct 2026 MPC Meeting", "10"),
        ("Oct 2026 MPC meeting (Oct 5-7)", "10"),
        ("October 5–7, 2026", "10"),
        ("6-7 October 2026", "10"),
    ],
)
def test_event_dates_name_the_month(raw, month):
    assert period_evidence(raw, "E", definition="Policy meeting minutes") == (
        f"2026-{month}",
        None,
    )


def test_monthly_policy_decision_and_date_guards():
    assert period_evidence(
        "6-7 October 2026", "M", definition="Policy rate decision"
    ) == ("2026-10", None)
    assert period_evidence(
        "Sep 2026 meeting", "M", definition="Consumer price index"
    ) == (None, "period_frequency_mismatch")
    assert period_evidence("October 5-7, 2026", "M") == (
        None,
        "period_format_unrecognised",
    )
    for raw in (
        "Sep 31-32, 2026 meeting",
        "Sep 17-16 2026 meeting",
        "Oct 2026 MPC meeting (Sep 5-7)",
    ):
        assert period_evidence(raw, "E") == (None, "period_format_unrecognised")
    # Existing ordinary daily periods on irregular indicators retain daily precision.
    assert period_evidence("October 2, 2026", "E") == ("2026-10-02", None)
    assert period_evidence("2026-09-24", "E") == ("2026-09-24", None)


@pytest.mark.parametrize(
    "raw,date",
    [
        ("week ending 2026-10-09", "2026-10-09"),
        ("As on October 2, 2026", "2026-10-02"),
        ("October 2, 2026", "2026-10-02"),
        ("Oct 09, 2026", "2026-10-09"),
        ("semana al 9 de octubre de 2026", "2026-10-09"),
        ("al 9 de octubre de 2026", "2026-10-09"),
    ],
)
def test_explicit_weekly_dates(raw, date):
    assert period_evidence(raw, "weekly") == (date, None)


def test_first_half_requires_catalogue_definition():
    raw = "Primera quincena de Septiembre de 2026"
    assert period_evidence(
        raw, "biweekly", definition="CPI for the first half of the month"
    ) == ("2026-09-15", None)
    assert period_evidence(raw, "biweekly") == (None, "period_is_submonthly_window")


@pytest.mark.parametrize(
    "raw,end",
    [
        ("Jan-Aug 2026", "08"),
        ("January-August 2026", "08"),
        ("January to September 2026", "09"),
        ("January–September 2026", "09"),
        ("First three quarters of 2026", "09"),
    ],
)
def test_cumulative_window_requires_ytd_definition(raw, end):
    assert period_evidence(raw, "M") == (None, "period_is_cumulative_window")
    assert period_evidence(raw, "M", definition="Cumulative year-to-date flow") == (
        f"2026-{end}",
        None,
    )
    assert period_evidence(raw, "Q", definition="Cumulative year-to-date flow") == (
        None,
        "period_is_cumulative_window",
    )


@pytest.mark.parametrize(
    "raw",
    [
        "Jun-Aug 2026",
        "three months to August 2026",
        "three months to Aug 2026",
        "3 months to Aug 2026",
        "trimestre móvel Jun-Aug 2026",
        "quarter ending Aug 2026",
        "quarter ended Aug 2026",
    ],
)
def test_rolling_window_requires_three_month_definition(raw):
    assert period_evidence(raw, "M", definition="Unemployment Rate") == (
        None,
        "period_is_rolling_window",
    )
    assert period_evidence(raw, "M", definition="GDP 3-Month Average (3M/3M)") == (
        "2026-08",
        None,
    )
    assert period_evidence(raw, "Q") == (None, "period_is_rolling_window")


@pytest.mark.parametrize(
    "raw,reason",
    [
        ("9 months to Aug 2026", "period_is_rolling_window"),
        ("Issue 1413", "period_is_document_identifier"),
        ("Issue Number 188", "period_is_document_identifier"),
        ("bond maturing 2028-11-01", "period_is_maturity_date"),
        ("2026-27", "period_fiscal_year_ambiguous"),
        ("Septermber 2026", "period_format_unrecognised"),
        ("September 2026 trailing text", "period_format_unrecognised"),
        ("September", "period_year_missing"),
        ("Agosto", "period_year_missing"),
        ("Septiembre", "period_year_missing"),
        ("Sept.", "period_year_missing"),
        ("Aug.", "period_year_missing"),
        ("August", "period_year_missing"),
        ("Week ending Sep 30", "period_year_missing"),
        ("week ending October 10", "period_year_missing"),
        ("week ending October 3", "period_year_missing"),
        ("Week ending Oct 07", "period_year_missing"),
        ("third quarter", "period_year_missing"),
    ],
)
def test_precise_reasons_without_guessed_periods(raw, reason):
    assert period_evidence(raw, "M") == (None, reason)


def decision(raws, freq="M", definition="Consumer price index"):
    rows = [
        dict(
            legacy_run_status="published",
            first_seen_at="2026-10-18T00:00:00Z",
            reading_id=n,
            content_hash=str(n),
            legacy_table="observations",
            reference_period=raw,
            raw={"release_date": "2026-10-02", "status": "released"},
            value=1,
        )
        for n, raw in enumerate(raws)
    ]
    index = SimpleNamespace(
        rows={("legacy", "1"): rows},
        import_reasons={},
        official=[],
        mapping=lambda work, published: (("indicator", "unknown", []), None),
        indicators={
            "indicator": dict(
                frequency=freq,
                geography="USA",
                measure=definition,
                definition=definition,
            )
        },
    )
    return decide(
        index,
        dict(
            source_id="legacy",
            occurrence_key="1",
            reason="period_unknown",
            indicator_id="indicator",
        ),
        "2026-10-19T12:00:00Z",
    )


def test_placeholders_missing_and_yearless_labels_do_not_veto_explicit_evidence():
    result = decision(["legacy-occurrence:103", "missing", "September", "Sep 2026"])
    assert (result["status"], result["reference_period"]) == ("complete", "2026-09")
    assert result["evidence"]["period_failures"] == []
    assert set(result["evidence"]["ignored_period_labels"]) == {
        "legacy-occurrence:103",
        "missing",
        "September",
    }
    assert (
        decision(["legacy-occurrence:103", "missing"])["reason"]
        == "period_not_derivable"
    )
    assert decision(["September"])["reason"] == "period_year_missing"
    assert decision(["Agosto", "Sep 2026"])["reason"] == "period_year_missing"
    assert (
        decision(["third quarter", "Third Quarter of 2026"], "Q")["reference_period"]
        == "2026-Q3"
    )


def test_cumulative_or_rolling_window_never_becomes_a_single_month_measure():
    for raw in (
        "Jan-Aug 2026",
        "January to September 2026",
        "3 months to Aug 2026",
        "quarter ending Aug 2026",
    ):
        result = decision(["legacy-occurrence:13", "8/2026", raw])
        assert result["status"] == "pending"
        assert result["reference_period"] is None
        assert result["reason"] in {
            "period_is_cumulative_window",
            "period_is_rolling_window",
        }


@pytest.mark.parametrize(
    "raw,reason",
    [
        ("Issue 1413", "period_is_document_identifier"),
        ("bond maturing 2028-11-01", "period_is_maturity_date"),
    ],
)
def test_document_and_maturity_are_terminal(raw, reason):
    result = decision([raw], "irregular")
    assert (result["status"], result["reason"], result["reference_period"]) == (
        "terminal",
        reason,
        None,
    )


def test_new_formats_still_detect_conflicting_explicit_periods():
    result = decision(["Sep 2026 meeting", "Oct 2026 MPC meeting"], "E")
    assert (result["status"], result["reason"]) == (
        "terminal",
        "conflicting_reference_periods",
    )


def test_invalid_new_shapes_are_not_silently_ignored():
    for raw in (
        "Apr-Jun 0000 (Q2 0000)",
        "Primera quincena de Septiembre de 0000",
        "Sep 2026 meeting (Oct 5-7)",
    ):
        assert (
            period_evidence(raw, "E", definition="First half of the month")[1]
            is not None
        )
    assert period_evidence("   ", "M") == (None, None)
