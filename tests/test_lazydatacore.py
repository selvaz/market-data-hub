# -*- coding: utf-8 -*-
"""Tests for the lazydatacore shared contract."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from market_data_hub.lazydatacore import (
    OHLCV_COLUMNS,
    AnalysisResult,
    Domain,
    InstrumentId,
    Money,
    NotResolvableError,
    PriceBar,
    Provenance,
    ResolvedRef,
    ResultKind,
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
        ("ticker:AAPL", Domain.TICKER, "AAPL", None),
        ("crypto:BTCUSDT@1h", Domain.CRYPTO, "BTCUSDT", "1h"),
        ("macro:FEDFUNDS", Domain.MACRO, "FEDFUNDS", None),
        ("macro_panel:USA/real_gdp_growth", Domain.MACRO_PANEL, "USA/real_gdp_growth", None),
        ("factor:FF5_daily/Mkt-RF", Domain.FACTOR, "FF5_daily/Mkt-RF", None),
        ("cik:0000320193", Domain.CIK, "0000320193", None),
        ("isin:US0378331005", Domain.ISIN, "US0378331005", None),
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


def test_price_is_alias_for_ticker():
    # 'price:' normalises to 'ticker:' — same id, same hash, same string.
    p, t = InstrumentId.parse("price:AAPL"), InstrumentId.parse("ticker:AAPL")
    assert p == t and hash(p) == hash(t)
    assert str(p) == "ticker:AAPL"
    assert p.domain is Domain.TICKER


def test_cik_zero_padded_and_numeric():
    assert InstrumentId.parse("cik:320193") == InstrumentId.parse("cik:0000320193")
    assert str(InstrumentId.parse("cik:320193")) == "cik:0000320193"
    with pytest.raises(ValueError, match="cik key"):
        InstrumentId.parse("cik:AAPL")


def test_isin_upper_and_format_checked():
    assert str(InstrumentId.parse("isin:us0378331005")) == "isin:US0378331005"
    with pytest.raises(ValueError, match="isin key"):
        InstrumentId.parse("isin:NOTANISIN")


def test_qualifier_only_for_crypto():
    with pytest.raises(ValueError, match="only valid for the 'crypto'"):
        InstrumentId.parse("macro:FEDFUNDS@2020-01-01")
    with pytest.raises(ValueError, match="only valid for the 'crypto'"):
        InstrumentId.parse("ticker:AAPL@x")


def test_instrument_id_rejects_unknown_domain():
    with pytest.raises(ValueError, match="unknown domain"):
        InstrumentId.parse("bogus:AAPL")


def test_instrument_id_requires_namespace():
    with pytest.raises(ValueError, match="namespaced"):
        InstrumentId.parse("AAPL")


def test_instrument_id_serializes_as_canonical_string():
    # The whole point of the contract: serialise to a plain string, not a nested
    # object, so it is interchangeable with LazyFin's string identity.
    iid = InstrumentId.parse("crypto:ETHUSDT@4h")
    dumped = iid.model_dump(mode="json")
    assert dumped == "crypto:ETHUSDT@4h"
    assert isinstance(dumped, str)
    # ...and it coerces back from the string on validation.
    assert InstrumentId.model_validate(dumped) == iid
    assert InstrumentId.model_validate("price:AAPL") == InstrumentId.parse("price:AAPL")


def test_instrument_id_rejects_empty_qualifier():
    with pytest.raises(ValueError, match="must not be empty"):
        InstrumentId.parse("crypto:BTCUSDT@")


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
    ref = to_duckdb("factor:FF5_daily/Mkt-RF")
    assert ref.filters == {"factor_set": "FF5_daily", "factor": "Mkt-RF"}


def test_resolve_bad_pair_raises():
    with pytest.raises(NotResolvableError):
        to_duckdb("factor:Mkt-RF")  # missing factor_set/factor split


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
        instruments=[InstrumentId.parse("ticker:SPY")],
        payload={"state": 1, "prob_highvol": 0.8},
        provenance=Provenance(
            source=SourceRef(source="lazyhmm"),
            as_of=now_utc(),
            tool_version="0.1.0",
        ),
    )
    dumped = res.model_dump(mode="json")
    # instruments serialise as plain canonical strings, not nested objects
    assert dumped["instruments"] == ["ticker:SPY"]
    assert dumped["kind"] == "signal"
    again = AnalysisResult.model_validate(dumped)
    assert again.instruments[0] == InstrumentId.parse("ticker:SPY")
    assert again.kind is ResultKind.SIGNAL
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


def test_contract_timestamps_normalized_to_utc():
    # An aware non-UTC timestamp must be converted to UTC, not stored with its
    # offset, so shared results compare cleanly with the UTC warehouse.
    east = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    bar = PriceBar(ts=east, close=1.0)
    assert bar.ts.tzinfo is timezone.utc
    assert bar.ts.hour == 7

    prov = Provenance(source=SourceRef(source="s", retrieved_at=east), as_of=east)
    assert prov.as_of.utcoffset() == timezone.utc.utcoffset(None)
    assert prov.source.retrieved_at.hour == 7
    res = AnalysisResult(kind="report", produced_by="x", provenance=prov)
    assert res.created_at.tzinfo is timezone.utc


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


def test_validators_accept_empty_reader_result():
    # reader.read_prices() returns a bare empty DataFrame on no-data; the
    # validators must accept it rather than raising.
    pd = pytest.importorskip("pandas")
    empty = pd.DataFrame()
    assert validate_wide_prices(empty) is empty
    assert validate_long_prices(empty) is empty


def test_canonical_exports_present():
    # ResultKind and OHLCV_COLUMNS are part of the public contract.
    assert OHLCV_COLUMNS == ("open", "high", "low", "close", "adj_close", "volume")
    assert ResultKind.SCORE.value == "score"


# --------------------------------------------------------------------------- #
# import boundary (piano v3.1, Fase 1)                                        #
# --------------------------------------------------------------------------- #
def test_lazydatacore_has_no_heavy_dependencies():
    """lazydatacore is the shared contract package: it must stay importable
    without DuckDB, HTTP clients or the hub's own db/sources layers, so it can
    be extracted as a standalone distribution (plan v3.1, Step 1)."""
    import ast
    import pkgutil
    from pathlib import Path

    import market_data_hub.lazydatacore as ldc

    forbidden = {"duckdb", "requests", "httpx", "urllib3", "yfinance"}
    forbidden_prefixes = ("market_data_hub.db", "market_data_hub.sources",
                          "market_data_hub.reader", "market_data_hub.extract",
                          "lazybridge")

    pkg_dir = Path(ldc.__file__).parent
    for mod in pkgutil.iter_modules([str(pkg_dir)]):
        tree = ast.parse((pkg_dir / f"{mod.name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                assert root not in forbidden, (
                    f"lazydatacore/{mod.name}.py imports forbidden '{name}'")
                assert not name.startswith(forbidden_prefixes), (
                    f"lazydatacore/{mod.name}.py imports hub-internal '{name}'")
