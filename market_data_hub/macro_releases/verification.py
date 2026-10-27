"""Deterministic structural/sanity gate and append-only review queue."""
from dataclasses import replace
import hashlib
import json
import math

from .documents import put_document
from .run_log import now
from .models import utc


def reviewed_anchor(obs):
    from .asia_editions import EDITIONS as ASIA
    if obs.family in ASIA:
        return (obs.edition in ASIA[obs.family] and
                (obs.period, obs.stage) == ASIA[obs.family][obs.edition][:2])
    from .housing_editions import EDITIONS as HOUSING
    if obs.family in HOUSING:
        return obs.edition in HOUSING[obs.family] and obs.stage == HOUSING[obs.family][obs.edition][1]
    from .policy_editions import EDITIONS as POLICY
    if obs.family in POLICY:
        return obs.edition in POLICY[obs.family] and obs.stage == POLICY[obs.family][obs.edition][1]
    from .tier1_editions import EDITIONS as REMAINING
    if obs.family in REMAINING:
        return obs.edition in REMAINING[obs.family] and obs.stage == REMAINING[obs.family][obs.edition][1]
    from . import bea, bls, eurostat, ons
    from .us_editions import EDITIONS
    from .ons_rates import EDITIONS as UK_EDITIONS
    if obs.family in UK_EDITIONS:
        return obs.edition in UK_EDITIONS[obs.family]
    if obs.family == "ea_gdp":
        from .eurostat_gdp import EDITIONS as GDP_EDITIONS
        return obs.edition in GDP_EDITIONS and obs.stage == GDP_EDITIONS[obs.edition][1]
    if obs.family == "us_jobless_claims":
        from .dol import EDITIONS as CLAIMS_EDITIONS
        return obs.edition in CLAIMS_EDITIONS
    if obs.family in EDITIONS:
        return obs.edition in EDITIONS[obs.family]
    if obs.family in ("us_cpi", "us_payrolls", "us_unemployment", "us_ahe"):
        return obs.edition in {d for (family, _), d in bls.EDITIONS.items() if family == obs.family}
    if obs.family == "us_gdp":
        return obs.edition in bea.EDITIONS
    if obs.family == "ea_hicp":
        return (obs.edition == eurostat.PUBLICATIONS.get((obs.period, obs.stage)) or
                obs.stage == "final" and obs.edition in {d for (_, stage), d in eurostat.PUBLICATIONS.items() if stage == "final"})
    if obs.family == "uk_gdp":
        return ((obs.indicator_id == "uk_gdp_index" and obs.provider_vintage == str(ons.VERSION)
                 and obs.edition == "2026-08-24") or obs.provider_vintage in ons.BULLETINS)
    return False

# Changes are in the indicator's declared printed units (payroll: thousands).
SANITY_BANDS = {
    "cn_cpi_yy": 10,
    **{f"cn_new_home_prices_tier{tier}_{basis}": 20 for tier in (1, 2, 3) for basis in ("mm", "yy")},
    "jp_housing_starts_yy": 50,
    "ca_housing_starts": 300000,
    "de_building_permits": 100000, "de_building_permits_yy": 50,
    "boj_policy_rate_decision": 3,
    "pboc_lpr_decision": 3,
    "boc_overnight_rate_decision": 3,
    "boe_bank_rate_decision": 3,
    "ecb_deposit_facility_rate_decision": 3,
    "fed_funds_rate_decision": 3,
    "us_cpi_headline_mm": 3, "us_cpi_core_mm": 3,
    "us_cpi_headline_yy": 5, "us_cpi_core_yy": 5,
    "us_payrolls_change": 1500, "us_gdp_qq_saar": 15,
    "us_unemployment_rate": 5, "us_avg_hourly_earnings_mm": 10, "us_avg_hourly_earnings_yy": 20,
    "ea_hicp_yy": 5, "ea_hicp_mm": 3, "ea21_hicp_yy": 5, "ea21_hicp_mm": 3,
    "ea_future_hicp_yy": 5, "ea_future_hicp_mm": 3,
    "uk_gdp_mm": 5, "uk_gdp_index": 20,
}
SANITY_BANDS.update({
    "ea_unemployment_rate": 5,
    "ea_retail_sales_mm": 30,
    "ea_industrial_production_mm": 30,
    "ea_trade_balance": 200000,
    "uk_retail_sales_mm": 30,
    "us_new_home_sales": 2000,
    "ea21_core_hicp_yy": 10,
    "uk_average_weekly_earnings_yy": 15,
    "ea_economic_sentiment_indicator": 40, "ea_consumer_confidence": 40,
    "us_umich_consumer_sentiment": 50,
    "us_ppi_mm": 10, "us_ppi_yy": 20, "us_jolts_openings": 6000,
    "us_pce_price_index_mm": 5, "us_pce_price_index_yy": 10,
    "us_core_pce_mm": 5, "us_core_pce_yy": 10,
    "us_retail_sales_mm": 30, "us_retail_sales_ex_auto_mm": 30,
    "us_housing_starts": 1500, "us_building_permits": 1500,
    "us_durable_goods_orders_mm": 60, "us_durable_goods_ex_transport_mm": 30,
    "us_industrial_production_mm": 20, "us_capacity_utilization": 20,
    "us_trade_balance": 200,
    "uk_cpi_yy": 10, "uk_core_cpi_yy": 10,
    "uk_unemployment_rate": 5, "uk_regular_pay_yy": 15,
    "ea21_gdp_qq": 20, "ea21_gdp_yy": 30,
    "us_initial_jobless_claims": 3000,
})
ABSOLUTE_BOUNDS = {
    "ca_housing_starts": 2000000,
    "de_building_permits": 1000000,"us_jolts_openings": 20000, "us_housing_starts": 5000,
                   "ea_trade_balance": 1000000,
                   "us_new_home_sales": 5000,
                   "ea_economic_sentiment_indicator": 200,
                   "us_umich_consumer_sentiment": 150,
                   "us_building_permits": 5000, "us_trade_balance": 1000,
                   "us_initial_jobless_claims": 10000}
SANITY_BANDS.update({"uk_mortgage_approvals": 50000, "uk_hpi_yy": 30, "ea_loans_house_purchase_yy": 10, "ea_cost_borrow_house": 3,
                     "ea21_hpi_qq": 15, "ea21_hpi_yy": 30,
                     "us_fhfa_hpi_mm": 15, "us_fhfa_hpi_yy": 30,
                     "us_construction_spending_mm": 30, "us_pmms_30y": 3,
                     "us_existing_home_sales": 3, "us_existing_home_sales_mm": 50,
                     "us_pending_home_sales_mm": 50,
                     "us_case_shiller_national_yy": 30, "us_case_shiller_20_yy": 30})
ABSOLUTE_BOUNDS["us_existing_home_sales"] = 20
ABSOLUTE_BOUNDS["us_pmms_30y"] = 25
ABSOLUTE_BOUNDS["ea_cost_borrow_house"] = 25
ABSOLUTE_BOUNDS["uk_mortgage_approvals"] = 2000000


def configured_bands(overrides=None):
    values = dict(SANITY_BANDS)
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("Sanity bands must be an object")
    for indicator, value in (overrides or {}).items():
        if indicator not in values or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Sanity overrides must name known indicators and finite positive absolute changes")
        values[indicator] = value
    return values


def verify(store, batch, bands):
    # A known-good edition may bootstrap its own backfill/current final values.
    # Other editions can use that reviewed anchor as their initial comparison;
    # subsequent runs use the already committed verified vintage instead.
    anchors = [o for o in batch.observations if reviewed_anchor(o) and o.value is not None and not o.needs_review]
    for obs in batch.observations:
        if obs.value is None:
            continue
        if not math.isfinite(obs.value):
            batch.issue("non_finite_value")
            continue
        if obs.published_at and utc(obs.published_at) > utc(obs.document.retrieved_at):
            batch.issue("publication_after_capture")
        baseline = store.conn.execute(
            "SELECT v.value FROM value_vintages v JOIN source_series s ON s.id=v.source_series_id "
            "WHERE v.indicator_id=? AND v.verification_status='verified' AND s.evidence_role='issuer' "
            "AND s.is_published=1 "
            "ORDER BY (v.reference_period=?) DESC,v.issuer_published_at DESC,v.reference_period DESC,v.id DESC LIMIT 1",
            (obs.indicator_id, obs.period)).fetchone()
        if not baseline and not reviewed_anchor(obs):
            candidates = [o for o in anchors if o.indicator_id == obs.indicator_id]
            if candidates:
                anchor = max(candidates, key=lambda o: (o.period == obs.period, o.published_at or "", o.period))
                baseline = (anchor.value,)
        if baseline and abs(obs.value - baseline[0]) > bands[obs.indicator_id]:
            batch.issue("sanity_band_exceeded:" + obs.indicator_id)
        elif not baseline and not batch.anchors and not reviewed_anchor(obs):
            batch.issue("verified_baseline_missing:" + obs.indicator_id)
        # First bootstrap anchors still reject obviously invalid numeric payloads.
        cap = ABSOLUTE_BOUNDS.get(obs.indicator_id, 25000 if obs.indicator_id == "us_payrolls_change" else 500 if obs.indicator_id == "uk_gdp_index" else 100)
        if abs(obs.value) > cap:
            batch.issue("absolute_sanity_bound_exceeded:" + obs.indicator_id)
    if batch.issues:
        batch.observations = [replace(o, needs_review=True) for o in batch.observations
                              if o.value is None or math.isfinite(o.value)]
    return batch


def persist_review(store, batch, family, run_id):
    hashes = {put_document(store, doc) for doc in batch.documents}
    if not batch.issues and not batch.review_reasons:
        return
    reasons = json.dumps(sorted(set(batch.issues + batch.review_reasons)))
    for digest in hashes:
        identity = hashlib.sha256((family + digest + reasons).encode()).hexdigest()
        store.conn.execute("INSERT INTO review_queue VALUES (?,?,?,?,?,?,'pending') ON CONFLICT DO NOTHING",
                           (identity, family, run_id, digest, reasons, now()))
