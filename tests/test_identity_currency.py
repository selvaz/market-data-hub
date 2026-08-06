"""currency_for_symbol: explicit override > FX quote-currency derivation >
curated-universe USD default > unknown (None). Never guesses for an ad-hoc
symbol outside tickers.yaml.
"""

from __future__ import annotations

from market_data_hub.db.identity import currency_for_symbol


def test_explicit_stoxx_override_returns_eur() -> None:
    assert currency_for_symbol("EXSA.DE") == "EUR"


def test_euro_bond_ucits_overrides_return_eur() -> None:
    """DBXP.DE/IBCL.DE/IEAC.AS are in tickers.yaml (so the generic heuristic
    would default them to USD) but are actually EUR-denominated -- the
    explicit override must win over the curated-universe default."""
    assert currency_for_symbol("DBXP.DE") == "EUR"
    assert currency_for_symbol("IBCL.DE") == "EUR"
    assert currency_for_symbol("IEAC.AS") == "EUR"


def test_eem_override_returns_usd_despite_not_being_in_tickers_yaml() -> None:
    """EEM isn't in the curated universe, so without an override it would
    fall through to unknown (None) -- the explicit override bypasses that
    gate the same way the STOXX/euro-bond entries do."""
    assert currency_for_symbol("EEM") == "USD"


def test_fx_pair_quote_currency_derivation_still_works() -> None:
    assert currency_for_symbol("USDJPY=X") == "JPY"
    assert currency_for_symbol("EURUSD=X") == "USD"


def test_curated_universe_symbol_without_override_defaults_to_usd() -> None:
    assert currency_for_symbol("SPY") == "USD"


def test_unknown_ad_hoc_symbol_returns_none_never_guesses() -> None:
    assert currency_for_symbol("7203.T") is None
