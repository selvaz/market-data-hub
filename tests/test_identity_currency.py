"""currency_for_symbol: explicit override > FX quote-currency derivation >
curated-universe USD default > unknown (None). Never guesses for an ad-hoc
symbol outside tickers.yaml.

Also covers sync_currency_overrides: the idempotent migration that corrects
an already-populated listings row (ensure_listing's INSERT ... WHERE NOT
EXISTS never touches an existing row, so a new/changed _CURRENCY_OVERRIDES
entry needs this to actually reach a pre-existing database).
"""

from __future__ import annotations

from market_data_hub.db.connection import get_conn
from market_data_hub.db.identity import (
    currency_for_symbol,
    ensure_listing,
    sync_currency_overrides,
)


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


def test_sync_currency_overrides_corrects_an_already_wrong_row(tmp_db) -> None:
    """The exact gap ensure_listing/the general backfill can't close: an
    existing row stuck on a stale/wrong currency."""
    con = get_conn(tmp_db)
    try:
        ensure_listing(con, "DBXP.DE", currency="USD")  # simulates the pre-fix bad state
        updated = sync_currency_overrides(con)
        row = con.execute(
            "SELECT currency FROM listings WHERE symbol = 'DBXP.DE'"
        ).fetchone()
    finally:
        con.close()
    assert updated == 1
    assert row[0] == "EUR"


def test_sync_currency_overrides_fills_a_null_row_outside_tickers_yaml(tmp_db) -> None:
    """EEM is outside the curated universe entirely, so the general
    tickers.yaml-scoped backfill never reaches it -- sync_currency_overrides
    must, since it iterates the override dict directly."""
    con = get_conn(tmp_db)
    try:
        ensure_listing(con, "EEM", currency=None)
        con.execute("UPDATE listings SET currency = NULL WHERE symbol = 'EEM'")
        updated = sync_currency_overrides(con)
        row = con.execute("SELECT currency FROM listings WHERE symbol = 'EEM'").fetchone()
    finally:
        con.close()
    assert updated == 1
    assert row[0] == "USD"


def test_sync_currency_overrides_is_idempotent(tmp_db) -> None:
    con = get_conn(tmp_db)
    try:
        ensure_listing(con, "DBXP.DE", currency="USD")
        first = sync_currency_overrides(con)
        second = sync_currency_overrides(con)
    finally:
        con.close()
    assert first == 1
    assert second == 0


def test_sync_currency_overrides_leaves_non_override_symbols_untouched(tmp_db) -> None:
    con = get_conn(tmp_db)
    try:
        ensure_listing(con, "SPY", currency="EUR")  # deliberately "wrong" -- not an override
        sync_currency_overrides(con)
        row = con.execute("SELECT currency FROM listings WHERE symbol = 'SPY'").fetchone()
    finally:
        con.close()
    assert row[0] == "EUR"  # untouched: SPY has no explicit override
