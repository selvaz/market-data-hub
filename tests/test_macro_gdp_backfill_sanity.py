"""GDP index history uses local changes and a verified scale anchor."""
from dataclasses import replace

from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.adapters import persist
from market_data_hub.macro_releases.discovery import FamilyFetch
from market_data_hub.macro_releases.ons import ONSAdapter
from market_data_hub.macro_releases.verification import configured_bands, verify
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
