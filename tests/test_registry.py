# -*- coding: utf-8 -*-
"""Tests for the symbol ⇄ InstrumentId registry (inverse of the resolver)."""
from __future__ import annotations

import pytest

from market_data_hub.lazydatacore import (
    Domain,
    InstrumentId,
    NotResolvableError,
    from_duckdb,
    from_symbol,
    to_duckdb,
    to_symbol,
)


# --------------------------------------------------------------------------- #
# from_symbol — bare symbol -> canonical id                                   #
# --------------------------------------------------------------------------- #
def test_bare_symbol_defaults_to_ticker() -> None:
    assert from_symbol("AAPL") == InstrumentId.parse("ticker:AAPL")
    assert str(from_symbol("AAPL")) == "ticker:AAPL"


def test_bare_symbol_with_explicit_domain() -> None:
    assert from_symbol("FEDFUNDS", domain=Domain.MACRO) == InstrumentId.parse("macro:FEDFUNDS")
    # domain accepts the string form too
    assert from_symbol("FEDFUNDS", domain="macro") == InstrumentId.parse("macro:FEDFUNDS")


def test_bare_crypto_symbol_takes_qualifier() -> None:
    iid = from_symbol("BTCUSDT", domain=Domain.CRYPTO, qualifier="1h")
    assert iid == InstrumentId.parse("crypto:BTCUSDT@1h")


def test_namespaced_string_passes_through() -> None:
    # already canonical (incl. the price-> ticker alias) is parsed, not re-wrapped
    assert from_symbol("crypto:BTCUSDT@4h") == InstrumentId.parse("crypto:BTCUSDT@4h")
    assert from_symbol("price:AAPL") == InstrumentId.parse("ticker:AAPL")


def test_instrument_id_passes_through() -> None:
    iid = InstrumentId.parse("ticker:MSFT")
    assert from_symbol(iid) is iid


def test_empty_symbol_rejected() -> None:
    with pytest.raises(ValueError):
        from_symbol("")


# --------------------------------------------------------------------------- #
# to_symbol — id -> flat warehouse key                                        #
# --------------------------------------------------------------------------- #
def test_to_symbol_returns_flat_key() -> None:
    assert to_symbol("ticker:AAPL") == "AAPL"
    assert to_symbol("crypto:BTCUSDT@1h") == "BTCUSDT"
    assert to_symbol("factor:FF5_daily/Mkt-RF") == "FF5_daily/Mkt-RF"


# --------------------------------------------------------------------------- #
# from_duckdb — warehouse row -> canonical id (inverse of to_duckdb)          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "table,key,qualifier,expected",
    [
        ("prices_daily", "AAPL", None, "ticker:AAPL"),
        ("crypto_ohlcv", "BTCUSDT", "1h", "crypto:BTCUSDT@1h"),
        ("crypto_ohlcv", "ETHUSDT", None, "crypto:ETHUSDT@1h"),  # default timeframe
        ("macro_series", "FEDFUNDS", None, "macro:FEDFUNDS"),
        ("macro_panel", "USA/real_gdp_growth", None, "macro_panel:USA/real_gdp_growth"),
        ("factor_returns", "FF5_daily/Mkt-RF", None, "factor:FF5_daily/Mkt-RF"),
    ],
)
def test_from_duckdb_reconstructs_id(table, key, qualifier, expected) -> None:
    assert from_duckdb(table, key, qualifier=qualifier) == InstrumentId.parse(expected)


def test_from_duckdb_unknown_table() -> None:
    with pytest.raises(NotResolvableError):
        from_duckdb("not_a_table", "AAPL")


def test_from_duckdb_qualifier_only_for_crypto() -> None:
    with pytest.raises(ValueError):
        from_duckdb("prices_daily", "AAPL", qualifier="1h")


# --------------------------------------------------------------------------- #
# round-trip: to_duckdb ∘ from_duckdb is identity on the warehouse coordinates #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "iid_str",
    [
        "ticker:AAPL",
        "crypto:BTCUSDT@1h",
        "macro:FEDFUNDS",
        "macro_panel:USA/real_gdp_growth",
        "factor:FF5_daily/Mkt-RF",
    ],
)
def test_roundtrip_through_warehouse(iid_str) -> None:
    iid = InstrumentId.parse(iid_str)
    ref = to_duckdb(iid)
    # the resolver's primary key column(s) feed from_duckdb back to the same id
    if ref.table == "crypto_ohlcv":
        back = from_duckdb(ref.table, ref.filters["symbol"], qualifier=ref.filters["timeframe"])
    elif ref.table == "prices_daily":
        back = from_duckdb(ref.table, ref.filters["symbol"])
    elif ref.table == "macro_series":
        back = from_duckdb(ref.table, ref.filters["series_id"])
    elif ref.table == "macro_panel":
        back = from_duckdb(ref.table, f"{ref.filters['country_iso3']}/{ref.filters['indicator_id']}")
    else:  # factor_returns
        back = from_duckdb(ref.table, f"{ref.filters['factor_set']}/{ref.filters['factor']}")
    assert back == iid
