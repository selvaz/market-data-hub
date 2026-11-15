"""Targeted maintenance for wrong-dated provisional prices."""

from __future__ import annotations

from datetime import date
from typing import Union


def remove_wrong_dated_live_duplicates(con, wrong_date: Union[date, str], *,
                                       dry_run: bool = True) -> int:
    """Count or remove exact live duplicates on ``wrong_date``.

    A candidate must have the same listing, source, close and adjusted close as
    that listing's immediately preceding live row.  Requiring an explicit
    incident date keeps this repair deliberately narrower than normal live-row
    retention.  Re-running the delete is safe and returns zero.
    """
    candidates = """
        WITH previous AS (
            SELECT bad.date, bad.listing_id
            FROM prices_daily bad
            JOIN prices_daily prior
              ON prior.listing_id = bad.listing_id
             AND prior.is_live = TRUE
             AND prior.date = (
                 SELECT max(p.date) FROM prices_daily p
                 WHERE p.listing_id = bad.listing_id
                   AND p.is_live = TRUE AND p.date < bad.date
             )
             AND prior.source IS NOT DISTINCT FROM bad.source
             AND prior.close IS NOT DISTINCT FROM bad.close
             AND prior.adj_close IS NOT DISTINCT FROM bad.adj_close
            WHERE bad.is_live = TRUE AND bad.date = ?
        )
    """
    count = con.execute(
        candidates + "SELECT count(*) FROM previous", [wrong_date]
    ).fetchone()[0]
    if not dry_run and count:
        con.execute(
            candidates
            + "DELETE FROM prices_daily bad USING previous p "
              "WHERE bad.date = p.date AND bad.listing_id = p.listing_id",
            [wrong_date],
        )
    return count


def remove_non_trading_day_live_rows(con, wrong_date: Union[date, str], *,
                                     dry_run: bool = True) -> int:
    """Count or remove live rows on an explicitly confirmed non-trading date.

    The operator must establish that ``wrong_date`` was not a trading session
    for the affected listings; this function does not infer exchange calendars.
    Require settled history for the same listing before that date and no
    settled row on it. Prices need not match, and prior live rows are irrelevant.
    """
    candidates = """
        SELECT bad.date, bad.listing_id
        FROM prices_daily bad
        WHERE bad.is_live = TRUE AND bad.date = ?
          AND EXISTS (
              SELECT 1 FROM prices_daily settled
              WHERE settled.listing_id = bad.listing_id
                AND settled.is_live = FALSE AND settled.date < bad.date
          )
          AND NOT EXISTS (
              SELECT 1 FROM prices_daily settled
              WHERE settled.listing_id = bad.listing_id
                AND settled.is_live = FALSE AND settled.date = bad.date
          )
    """
    count = con.execute(
        "SELECT count(*) FROM (" + candidates + ")", [wrong_date]
    ).fetchone()[0]
    if not dry_run and count:
        con.execute(
            "DELETE FROM prices_daily bad USING (" + candidates + ") p "
            "WHERE bad.is_live = TRUE AND bad.date = p.date "
            "AND bad.listing_id = p.listing_id",
            [wrong_date],
        )
    return count
