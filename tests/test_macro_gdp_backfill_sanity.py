"""GDP index history uses local changes and a verified scale anchor."""
from dataclasses import replace

from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.discovery import FamilyFetch
from market_data_hub.macro_releases.ons import ONSAdapter
from market_data_hub.macro_releases.verification import _gdp_backfill_check, configured_bands, verify
from test_macro_official_adapters import document


def test_reviewed_history_drift_crash_and_scale(tmp_path):
    template = ONSAdapter.parse_index(document("ons/ecy2-mgdp.json"), "2026-08")[-1]
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [template])
        def check(values, **changes):
            observations = [replace(template, period=p, value=v, printed=str(v), **changes)
                            for p, v in values]
            return verify(store, FamilyFetch(observations=observations), configured_bands())

        # Long drift is checked through adjacent months, anchored at an overlap.
        drift = [(f"{2020 + i // 12:04d}-{i % 12 + 1:02d}", 80 + i * 0.3)
                 for i in range(80)]
        drift[-1] = ("2026-08", template.value)
        assert not check(drift).issues
        crash = [("2020-03", 90.0), ("2020-04", 71.8), ("2020-05", 73.4)]
        bridge = [(f"{2020 + i // 12:04d}-{i % 12 + 1:02d}", 80 + i * 0.3)
                  for i in range(5, 80)]
        bridge[0] = ("2020-06", 79.0)
        bridge[-1] = ("2026-08", template.value)
        assert not check(crash + bridge).issues
        # The same drop outside the reviewed edition cannot claim the allowance.
        assert check(crash + bridge, edition="2026-11-12").issues
        assert check([(p, v * 10) for p, v in drift]).issues
        assert check([(p, v + 30) for p, v in drift]).issues
        # The exception is tied to April 2020, not any large monthly change.
        assert check([("2020-03", 90), ("2020-04", 71.8), ("2020-05", 90)] + bridge).issues


def test_unreviewed_crash_with_adjacent_verified_baseline_is_held(tmp_path):
    template = ONSAdapter.parse_index(document("ons/ecy2-mgdp.json"), "2026-08")[-1]
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [replace(template, period="2020-03", value=90.0)])
        april = replace(template, period="2020-04", value=71.8, edition="2026-11-12")
        batch = verify(store, FamilyFetch(observations=[april]), configured_bands())
        assert "sanity_band_exceeded:uk_gdp_index" in batch.issues
        assert batch.observations[0].needs_review


def test_reviewed_history_must_connect_to_verified_anchor(tmp_path):
    template = ONSAdapter.parse_index(document("ons/ecy2-mgdp.json"), "2026-08")[-1]
    with open_writer(tmp_path / "macro.sqlite") as store:
        persist(store, [template])

        def rows(values):
            return [replace(template, period=p, value=v, printed=str(v)) for p, v in values]

        disconnected = rows([("2018-01", 400), ("2018-02", 400), ("2026-08", 103.1)])
        batch = verify(store, FamilyFetch(observations=disconnected), configured_bands())
        assert "sanity_band_exceeded:uk_gdp_index" in batch.issues
        assert all(o.needs_review for o in batch.observations)

        continuous = rows([(f"2026-{month:02}", 99 + month * 0.5) for month in range(1, 8)]
                          + [("2026-08", 103.1)])
        assert not verify(store, FamilyFetch(observations=continuous), configured_bands()).issues
        broken_step = [replace(o, value=150) if o.period == "2026-05" else o for o in continuous]
        assert not _gdp_backfill_check(store, broken_step[0], broken_step, 20)
        gap = [o for o in continuous if o.period != "2026-05"]
        assert not _gdp_backfill_check(store, gap[0], gap, 20)
        # June has no chain across the gap, but is two months from verified August.
        near_gap = [o for o in gap if o.period != "2026-07"]
        june = next(o for o in near_gap if o.period == "2026-06")
        assert _gdp_backfill_check(store, june, near_gap, 20)
        assert "sanity_band_exceeded:uk_gdp_index" in verify(
            store, FamilyFetch(observations=gap), configured_bands()).issues
