"""Date-only consensus must not invent a pre-release capture/publication time."""
import pytest

from market_data_hub.macro_releases import open_writer
from market_data_hub.macro_releases.models import ScheduleVersion
from test_macro_read_api import consensus, evidence

NOW = "2026-10-18T23:00:00Z"


@pytest.fixture
def store(tmp_path):
    with open_writer(tmp_path / "macro.sqlite") as store:
        yield store


def forecast(store, event, field, value, *, provider="Reuters", actual=3.6):
    consensus(store, event, provider, actual, captured="2026-10-12T12:00:00Z")
    # Model legacy rows written before timestamp validation; leave write
    # contracts and their eligibility CHECK constraints unchanged in production.
    store.conn.execute("DROP TRIGGER IF EXISTS consensus_readings_no_update")
    store.conn.execute("PRAGMA ignore_check_constraints=ON")
    store.conn.execute(f"UPDATE consensus_readings SET {field}=? WHERE provider=?", (value, provider))


def card(store):
    result = store.release_cards("2026-10-10", "2026-10-17", as_known_at=NOW)
    return result["cards"][0]


@pytest.mark.parametrize("field", ["captured_at", "published_at"])
@pytest.mark.parametrize("value", ["2026-10-14T15:00", "2026-10-14", "2026-10-15"])
def test_same_day_or_later_date_only_consensus_is_excluded(store, field, value):
    event, _ = evidence(store, issuer_timezone="America/New_York")
    forecast(store, event, field, value)
    result = card(store)
    assert result["consensus"] == []
    assert result["surprise"] is None
    diagnostic, = result["consensus_diagnostics"]
    assert diagnostic["reason"] == "date_only_consensus_not_pre_release"
    assert diagnostic["fields"] == [field]
    assert diagnostic["release_date"] == "2026-10-14"
    assert diagnostic["issuer_timezone"] == "America/New_York"


@pytest.mark.parametrize("field", ["captured_at", "published_at"])
def test_strictly_earlier_date_only_consensus_can_be_pre_release(store, field):
    event, _ = evidence(store, issuer_timezone="America/New_York")
    forecast(store, event, field, "2026-10-13T15:00")
    result = card(store)
    assert result["consensus"][0]["value"] == 3.6
    assert result["consensus"][0]["timestamp_precisions"][field] == "date_only"
    assert result["surprise"][0]["value"] == .1
    assert result["consensus_diagnostics"] == []


@pytest.mark.parametrize("zone,published,day", [
    ("America/New_York", "2026-10-14T02:30:00Z", "2026-10-13"),
    ("Pacific/Kiritimati", "2026-10-14T12:30:00Z", "2026-10-15"),
])
def test_release_date_uses_issuer_timezone_in_both_directions(store, zone, published, day):
    event, _ = evidence(store, issuer_timezone=zone, published=published)
    forecast(store, event, "captured_at", day, provider="Same date")
    previous = "2026-10-12" if zone == "America/New_York" else "2026-10-14"
    forecast(store, event, "captured_at", previous, provider="Earlier date")
    result = card(store)
    assert [row["provider"] for row in result["consensus"]] == ["Earlier date"]
    assert result["consensus_diagnostics"][0]["release_date"] == day


def test_saved_issuer_local_midnight_cutoff_remains_the_date_only_release_bound(store):
    event, _ = evidence(store, published="2026-10-14T00:00:00Z", issuer_timezone="America/New_York")
    store.conn.execute("DROP TRIGGER value_vintages_no_update")
    store.conn.execute("UPDATE value_vintages SET publication_precision='date_only'")
    for provider, value in [("Earlier date", "2026-10-13"), ("Same date", "2026-10-14")]:
        forecast(store, event, "captured_at", value, provider=provider)
    # The release date bucket is 00Z, but the saved issuer-local day starts 04Z.
    store.conn.execute("UPDATE consensus_readings SET release_cutoff_at='2026-10-14T04:00:00+00:00', "
                       "cutoff_precision='date_only'")
    result = card(store)
    earlier, = result["consensus"]
    assert earlier["provider"] == "Earlier date"
    assert earlier["eligibility_cutoff_at"].startswith("2026-10-14T04:00:00")
    assert result["consensus_diagnostics"][0]["provider"] == "Same date"
    assert result["consensus_diagnostics"][0]["release_date"] == "2026-10-14"


def test_date_only_consensus_can_use_the_stored_schedule_timezone(store):
    event, _ = evidence(store, source="FRED", role="mirror")
    store.put(ScheduleVersion(release_event_id=event, expected_at="2026-10-14T02:30:00Z",
                             issuer_timezone="America/New_York", precision="exact", source_id="BLS",
                             recorded_at="2026-10-01T00:00:00Z"))
    forecast(store, event, "captured_at", "2026-10-13")
    result = card(store)
    assert result["time_basis"] == "schedule"
    assert result["consensus"] == []
    assert result["consensus_diagnostics"][0]["release_date"] == "2026-10-13"


def test_stale_later_cutoff_cannot_change_an_exact_release_date(store):
    event, _ = evidence(store, source="FRED", role="mirror", issuer_timezone="America/New_York")
    forecast(store, event, "captured_at", "2026-10-14")
    store.conn.execute("UPDATE consensus_readings SET release_cutoff_at='2026-10-15T12:30:00+00:00'")
    result = card(store)
    assert result["time_basis"] == "source_publication"
    assert result["consensus"] == [] and result["surprise"] is None
    assert result["consensus_diagnostics"][0]["release_date"] == "2026-10-14"


@pytest.mark.parametrize("zone", [None, "invalid/timezone"])
def test_unknown_timezone_cannot_certify_date_only_consensus(store, zone):
    event, _ = evidence(store, issuer_timezone=zone)
    forecast(store, event, "captured_at", "2026-10-13")
    result = card(store)
    assert result["consensus"] == [] and result["surprise"] is None
    assert result["consensus_diagnostics"][0]["reason"] == "unknown_issuer_timezone"


def test_excluded_post_release_reread_does_not_replace_earlier_provider_snapshot(store):
    event, _ = evidence(store, issuer_timezone="America/New_York")
    consensus(store, event, "Reuters", 3.6, captured="2026-10-13T12:00:00Z")
    consensus(store, event, "Reuters", 99, captured="2026-10-14T15:00:00Z")
    store.conn.execute("DROP TRIGGER consensus_readings_no_update")
    store.conn.execute("PRAGMA ignore_check_constraints=ON")
    store.conn.execute("UPDATE consensus_readings SET captured_at='2026-10-14T15:00' WHERE value=99")
    result = card(store)
    assert result["consensus"][0]["value"] == 3.6
    assert result["surprise"][0]["value"] == .1
    assert result["consensus_diagnostics"][0]["reason"] == "date_only_consensus_not_pre_release"
