"""Reviewed issuer publication gaps: periods the issuer itself did not publish.

A gap is the issuer's, never a collection error, and the value is never
computed from other periods to fill it.
"""
from __future__ import annotations

SHUTDOWN_2025 = ("BLS did not publish October 2025 CPI because of the 2025 federal government "
                 "shutdown, so there is no issuer 12-month change for October 2026")

# (indicator_id, reference_period) -> (family, first due date, issuer reason)
ISSUER_GAPS = {
    ("us_cpi_headline_yy", "2026-10"): ("us_cpi", "2026-11-10", SHUTDOWN_2025),
    ("us_cpi_core_yy", "2026-10"): ("us_cpi", "2026-11-10", SHUTDOWN_2025),
}


def issuer_gap(indicator_id, period):
    gap = ISSUER_GAPS.get((indicator_id, period))
    return gap[2] if gap else None


def due_issuer_gaps(as_of_date):
    """Gaps whose edition was due on or before the given ISO date."""
    return [{"indicator_id": indicator, "reference_period": period, "family": family, "due": due,
             "reason": reason}
            for (indicator, period), (family, due, reason) in sorted(ISSUER_GAPS.items())
            if due <= as_of_date]
