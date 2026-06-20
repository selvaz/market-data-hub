# -*- coding: utf-8 -*-
"""Tests for the lazydatacore shared contract."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from market_data_hub.lazydatacore import (
    AnalysisResult,
    Domain,
    InstrumentId,
    Money,
    NotResolvableError,
    PriceBar,
    Provenance,
    ResolvedRef,
    SourceRef,
    ensure_utc,
    now_utc,
    parse_iso,
    to_duckdb,
    to_iso,
    validate_long_prices,
    validate_wide_prices,
)


# --------------------------------------------------------------------------- #
# identity                                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,domain,key,qualifier",
    [
        ("price:AAPL", Domain.PRICE, "AAPL", None),
        ("crypto:BTCUSDT@1h", Domain.CRYPTO, "BTCUSDT", "1h"),
        ("macro:FEDFUNDS", Domain.MACRO, "FEDFUNDS", None),
        ("macro_panel:USA/real_gdp_growth", Domain.MACRO_PANEL, "USA/real_gdp_growth", None),
        ("factor:FF5_daily/MKT", Domain.FACTOR, "FF5_daily/MKT", None),
        ("ticker:AAPL", Domain.TICKER, "AAPL", None),
        ("cik:0000320193", Domain.CIK, "0000320193", None),
    ],
)
def test_instrument_id_parse_roundtrip(text, domain, key, qualifier):
    iid = InstrumentId.parse(text)
    assert iid.domain is domain
    assert iid.key == key
    assert iid.qualifier == qualifier
    # canonical string round-trips
    assert str(iid) == text
    # parse is idempotent on already-parsed values
    assert InstrumentId.parse(iid) is iid


def test_instrument_id_rejects_unknown_domain():
    with pytest.raises(ValueError, match="unknown domain"):
        InstrumentId.parse("bogus:AAPL")


def test_instrument_id_requires_namespace():
    with pytest.raises(ValueError, match="namespaced"):
        InstrumentId.parse("AAPL")


def test_instrument_id_json_roundtrip():
    iid = InstrumentId.parse("crypto:ETHUSDT@4h")
    dumped = iid.model_dump(mode="json")
    assert InstrumentId.model_validate(dumped) == iid


# --------------------------------------------------------------------------- #
# resolver                                                                     #
# --------------------------------------------------------------------------- #
def test_resolve_price():
    ref = to_duckdb("price:AAPL")
    assert ref == ResolvedRef(
        dataset="prices", table="prices_daily",
        filters={"symbol": "AAPL"}, reader="read_prices",
    )


def test_resolve_ticker_alias_matches_price_dataset():
    assert to_duckdb("ticker:AAPL").table == "prices_daily"


def test_resolve_crypto_default_and_explicit_timeframe():
    assert to_duckdb("crypto:BTCUSDT").filters["timeframe"] == "1h"
    assert to_duckdb("crypto:BTCUSDT@4h").filters["timeframe"] == "4h"


def test_resolve_macro_panel_pair():
    ref = to_duckdb("macro_panel:USA/real_gdp_growth")
    assert ref.filters == {"country_iso3": "USA", "indicator_id": "real_gdp_growth"}
    assert ref.reader == "read_macro_panel"


def test_resolve_factor_pair():
    ref = to_duckdb("factor:FF5_daily/MKT")
    assert ref.filters == {"factor_set": "FF5_daily", "factor": "MKT"}


def test_resolve_bad_pair_raises():
    with pytest.raises(NotResolvableError):
        to_duckdb("factor:MKT")  # missing factor_set/factor split


def test_resolve_reference_identity_not_in_warehouse():
    with pytest.raises(NotResolvableError):
        to_duckdb("cik:0000320193")


# --------------------------------------------------------------------------- #
# time                                                                         #
# --------------------------------------------------------------------------- #
def test_ensure_utc_tags_naive_and_converts_aware():
    naive = datetime(2024, 1, 1, 12, 0, 0)
    assert ensure_utc(naive).tzinfo is timezone.utc

    # An aware datetime in +05:00 is converted (not just relabelled) to UTC.
    east = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    converted = ensure_utc(east)
    assert converted.tzinfo is timezone.utc
    assert converted.hour == 7


def test_iso_roundtrip_and_zulu():
    dt = now_utc()
    assert parse_iso(to_iso(dt)) == dt
    assert parse_iso("2024-01-01T00:00:00Z").tzinfo is timezone.utc


# --------------------------------------------------------------------------- #
# result envelopes                                                             #
# --------------------------------------------------------------------------- #
def test_money_is_decimal():
    m = Money(amount=Decimal("10.50"), currency="USD")
    assert isinstance(m.amount, Decimal)
    with pytest.raises(ValueError):
        Money(amount=Decimal("1"), currency="usd")  # must be 3 upper letters


def test_analysis_result_envelope_roundtrips():
    res = AnalysisResult(
        kind="signal",
        produced_by="lazyhmm.regime.v1",
        instruments=[InstrumentId.parse("price:SPY")],
        payload={"state": 1, "prob_highvol": 0.8},
        provenance=Provenance(
            source=SourceRef(source="lazyhmm"),
            as_of=now_utc(),
            tool_version="0.1.0",
        ),
    )
    dumped = res.model_dump(mode="json")
    again = AnalysisResult.model_validate(dumped)
    assert again.instruments[0] == InstrumentId.parse("price:SPY")
    assert again.payload["state"] == 1


def test_forbid_extra_fields():
    with pytest.raises(ValueError):
        SourceRef(source="x", bogus=1)


# --------------------------------------------------------------------------- #
# series                                                                       #
# --------------------------------------------------------------------------- #
def test_price_bar_requires_aware_ts():
    bar = PriceBar(ts=now_utc(), close=100.0, volume=1000.0)
    assert bar.close == 100.0


def test_wide_and_long_validators():
    pd = pytest.importorskip("pandas")
    wide = pd.DataFrame(
        {"AAPL": [1.0, 2.0]},
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )
    assert validate_wide_prices(wide) is wide
    with pytest.raises(ValueError):
        validate_wide_prices(pd.DataFrame({"AAPL": [1.0]}))  # no DatetimeIndex

    long = pd.DataFrame({"date": ["2024-01-01"], "symbol": ["AAPL"], "close": [1.0]})
    assert validate_long_prices(long) is long
    with pytest.raises(ValueError):
        validate_long_prices(pd.DataFrame({"close": [1.0]}))
