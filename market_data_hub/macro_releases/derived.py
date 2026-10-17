"""Optional calculations from stored levels, never a published official rate."""
from __future__ import annotations

from dataclasses import replace

from .adapters import persist
from .bls import shift_month
from .catalogue import INDICATORS


def percent_change(store, template, previous_id, current_id):
    rows = [store.conn.execute("SELECT * FROM value_vintages WHERE id=?", (i,)).fetchone()
            for i in (previous_id, current_id)]
    if any(r is None for r in rows):
        raise ValueError("Both input vintages must exist")
    before, after = rows
    if before["indicator_id"] != after["indicator_id"] or before["unit"] != after["unit"] or before["scale"] != after["scale"]:
        raise ValueError("Input levels must have identical measure, unit and scale")
    indicator = store.conn.execute("SELECT * FROM indicators WHERE id=?", (after["indicator_id"],)).fetchone()
    if indicator["transform"] != "level" or before["value"] == 0:
        raise ValueError("Nonzero base level required")
    if after["reference_period"] != template.period or before["reference_period"] >= after["reference_period"]:
        raise ValueError("Input periods do not match the calculation")
    target = INDICATORS[template.indicator_id]
    lag = {"mm": 1, "yy": 12}.get(target.transform)
    if lag is None or before["reference_period"] != shift_month(template.period, -lag):
        raise ValueError("Input lag must match the requested transform")
    if any(indicator[k] != getattr(target, k) for k in ("geography", "issuer_id", "seasonal_adjustment")):
        raise ValueError("Input levels must match target geography, issuer and adjustment")
    value = (after["value"] / before["value"] - 1) * 100
    return persist(store, [replace(template, value=value, printed=str(value), published=False,
                                   series_key=template.series_key + ":calculated",
                                   method="calculation", inputs=(previous_id, current_id),
                                   formula="percent-change:v1")])[0]
