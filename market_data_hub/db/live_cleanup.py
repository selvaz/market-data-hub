"""Targeted maintenance for duplicated, wrong-dated provisional prices."""

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
