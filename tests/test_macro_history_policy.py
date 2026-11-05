"""History policies must not turn a late revised snapshot into a first print."""
import hashlib

import pytest

from market_data_hub.macro_releases import ReadAPI, open_writer, resolve
from market_data_hub.macro_releases.catalogue import INDICATORS
from market_data_hub.macro_releases.models import ReleaseEvent, SourceSeries, ValueVintage


@pytest.fixture
def history(tmp_path):
    with open_writer(tmp_path / "history.sqlite") as store:
        indicator = INDICATORS["uk_cpi_yy"]
        store.put(indicator)
        store.put(SourceSeries(id="ons-cpi", indicator_id=indicator.id, source_id="ONS",
                               issuer_id="ONS", distributor_id="ONS", source_key="D7G7",
                               evidence_role="issuer", is_published=True))
        for edition, seen, value, kind in (
            ("2024-02-14", "2024-02-14T08:00:00Z", 2.0, "initial"),
            ("2024-03-14", "2024-03-14T08:00:00Z", 2.2, "routine"),
            ("2026-10-21", "2026-11-05T08:00:00Z", 2.4, "backfill"),
        ):
            event = "ONS:uk_cpi:regular:" + edition
            store.put(ReleaseEvent(id=event, issuer_id="ONS", family="uk_cpi",
                                   estimate_stage="regular", edition=edition))
            store.put(ValueVintage(
                indicator_id=indicator.id, reference_period="2024-01", release_event_id=event,
                source_series_id="ons-cpi", value=value, published_as=str(value), unit="%",
                scale=1, estimate_stage="regular", revision_kind=kind,
                issuer_published_at=edition + "T07:00:00Z", publication_precision="exact",
                first_seen_at=seen, retrieved_at=seen,
                document_hash=hashlib.sha256(event.encode()).hexdigest(), parser_version="fixture",
                extraction_method="api", verification_status="verified"))
        yield store


def test_history_policies_and_cutoffs(history):
    def query(policy, cutoff):
        return resolve(history, "uk_cpi_yy", "2024-01", policy=policy, as_known_at=cutoff)

    before = "2026-11-05T07:59:59Z"
    after = "2026-11-05T08:00:00Z"
    for policy, cutoff, value, kind in (
        ("first_release", after, 2.0, "initial"),
        ("as_known_at", before, 2.2, "routine"),
        ("as_known_at", after, 2.4, "backfill"),
        ("revised_history", after, 2.4, "backfill"),
        ("revised_history", before, 2.2, "routine"),
    ):
        result = query(policy, cutoff)
        assert (result.value, result.revision_kind) == (value, kind)
        assert result.to_dict()["revision_kind"] == kind
    assert resolve(history, "uk_cpi_yy", "2024-01", as_known_at=after).value == 2.4
    assert query("first_release", "2024-02-14T07:59:59Z") is None
    assert history.latest_official("uk_cpi_yy")[0]["revision_kind"] == "backfill"
    assert history.first_release("uk_cpi_yy")[0]["revision_kind"] == "initial"
    with pytest.raises(ValueError, match="requires an as_known_at"):
        resolve(history, "uk_cpi_yy", "2024-01", policy="as_known_at")
    with pytest.raises(ValueError, match="policy must"):
        resolve(history, "uk_cpi_yy", "2024-01", policy="unknown")


def test_backfill_without_initial_is_not_a_first_release(history):
    row = dict(history.conn.execute("SELECT * FROM value_vintages WHERE revision_kind='backfill'").fetchone())
    row = {key: value for key, value in row.items() if key in ValueVintage.model_fields}
    row["reference_period"] = "2023-12"
    history.put(ValueVintage(**row))
    cutoff = "2026-11-06T00:00:00Z"
    assert resolve(history, "uk_cpi_yy", "2023-12", policy="first_release", as_known_at=cutoff) is None
    assert resolve(history, "uk_cpi_yy", "2023-12", policy="revised_history", as_known_at=cutoff).revision_kind == "backfill"


def test_cards_apply_policy_and_surface_kind(history):
    cutoff = "2026-11-06T00:00:00Z"
    api = ReadAPI(history)
    kwargs = dict(since="2024-01-01", until="2026-11-05", indicators=["uk_cpi_yy"],
                  as_known_at=cutoff)
    revised = api.release_cards(**kwargs, policy="revised_history")["cards"]
    initial = history.release_cards(**kwargs, policy="first_release")["cards"]
    assert [(c["resolution"]["value"], c["revision_kind"]) for c in revised] == [
        (2.0, "initial"), (2.2, "routine"), (2.4, "backfill")]
    assert [(c["resolution"]["value"], c["revision_kind"]) for c in initial] == [(2.0, "initial")]
    assert initial[0]["resolution"]["revision_kind"] == "initial"
    with pytest.raises(ValueError, match="requires an as_known_at"):
        api.release_cards("2024-01-01", "2024-12-31", policy="as_known_at")


def test_same_edition_initial_then_backfill_never_announced_as_new(history):
    row = dict(history.conn.execute("SELECT * FROM value_vintages WHERE revision_kind='initial'").fetchone())
    row = {key: value for key, value in row.items() if key in ValueVintage.model_fields}
    row.update(value=2.3, published_as="2.3", revision_kind="backfill",
               first_seen_at="2024-02-14T09:00:00Z", retrieved_at="2024-02-14T09:00:00Z",
               document_hash=hashlib.sha256(b"same edition later snapshot").hexdigest())
    history.put(ValueVintage(**row))
    cutoff = "2024-02-14T10:00:00Z"
    first = resolve(history, "uk_cpi_yy", "2024-01", policy="first_release", as_known_at=cutoff)
    known = resolve(history, "uk_cpi_yy", "2024-01", policy="as_known_at", as_known_at=cutoff)
    assert (first.value, first.revision_kind) == (2.0, "initial")
    assert (known.value, known.revision_kind) == (2.3, "backfill")
    api = ReadAPI(history)
    kwargs = dict(since="2024-02-14T07:00:00Z", until=cutoff, as_known_at=cutoff)
    assert api.first_seen_release_cards(**kwargs)["cards"] == []
    assert api.first_seen_release_cards(**kwargs, policy="as_known_at")["cards"] == []
    initial_cards = api.first_seen_release_cards(**kwargs, policy="first_release")["cards"]
    assert [(card["resolution"]["value"], card["revision_kind"]) for card in initial_cards] == [(2.0, "initial")]


def test_initial_selected_after_backfill_first_seen_is_not_a_new_release(history):
    row = dict(history.conn.execute("SELECT * FROM value_vintages WHERE revision_kind='initial'").fetchone())
    row = {key: value for key, value in row.items() if key in ValueVintage.model_fields}
    row.update(value=1.9, published_as="1.9", revision_kind="backfill",
               first_seen_at="2024-02-14T07:45:00Z", retrieved_at="2024-02-14T07:45:00Z",
               document_hash=hashlib.sha256(b"same edition early snapshot").hexdigest())
    history.put(ValueVintage(**row))
    cards = ReadAPI(history).first_seen_release_cards(
        "2024-02-14T07:00:00Z", "2024-02-14T09:00:00Z",
        as_known_at="2024-02-14T09:00:00Z", policy="first_release")
    assert cards["cards"] == []
