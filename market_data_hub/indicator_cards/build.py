"""Reproduce the versioned JSON from the approved PSV and timing evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from .models import (
    CLASSIFICATION_VERSION, CardDataset, DescriptivePeak, Evidence, IndicatorCard,
    PredictiveScreen, StoreIds, Timing,
)

DATA_DIR = Path(__file__).resolve().parent
PSV_PATH = DATA_DIR / "master_table_v2.psv"
TIMING_PATH = DATA_DIR / f"timing_{CLASSIFICATION_VERSION}.json"
ALIASES_PATH = DATA_DIR / f"aliases_{CLASSIFICATION_VERSION}.json"
JSON_PATH = DATA_DIR / f"cards_{CLASSIFICATION_VERSION}.json"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write(path: Path, text: str) -> None:
    # Path.write_text(newline=...) requires Python 3.10; the hub supports 3.9.
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def extract_timing(guide_path: Path, psv_path: Path = PSV_PATH) -> dict:
    """Copy §3's two timing columns verbatim; assert the row identities match."""
    with psv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="|"))
    by_key = {r["key"]: r for r in rows}
    extracted = {}
    for line in guide_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        cells = [s.strip() for s in line.strip("|").split("|")]
        if cells[0] not in by_key:
            continue
        row = by_key[cells[0]]
        offset = 1 if len(cells) == 17 else 0  # US table omits Country.
        if len(cells) not in (16, 17) or cells[1 + offset] != row["concept"]:
            raise ValueError(f"Guide/PSV concept mismatch: {row['key']}")
        if cells[2 + offset] != row["ids"] or row["key"] in extracted:
            raise ValueError(f"Guide/PSV ids mismatch or duplicate: {row['key']}")
        extracted[row["key"]] = {
            "predictive_screen": cells[8 + offset],
            "descriptive_peak": cells[9 + offset],
        }
    if set(extracted) != set(by_key):
        raise ValueError(f"Missing timing rows: {sorted(set(by_key) - set(extracted))}")
    return {
        "classification_version": CLASSIFICATION_VERSION,
        "source": "04-classification-v2.md, section 3, columns (a) and (b)",
        "source_guide_sha256": _hash(guide_path),
        "source_table_sha256": _hash(psv_path),
        "rows": extracted,
    }


def parse_ids(raw: str) -> StoreIds:
    groups: dict[str, list[str]] = {}
    notes = []
    for group in raw.split(";"):
        group = group.strip()
        if not group:
            continue
        match = re.fullmatch(r"(ms|es|cal|cat|pan|hub|derived ms):(.+)", group)
        if not match:
            notes.append(group)
            continue
        store = match[1].replace("derived ms", "derived_ms")
        groups.setdefault(store, []).extend(s.strip() for s in match[2].split(","))
    return StoreIds(**{k: tuple(dict.fromkeys(v)) for k, v in groups.items()}, notes=tuple(notes))


def parse_screen(text: str) -> tuple[tuple[PredictiveScreen, ...], str]:
    screens = []
    notes = []
    for part in text.split("; "):
        match = re.fullmatch(
            r"([AQ]\d+) \[(.+)\]: (PRED-PASS|PRED-PARTIAL|PRED-NOT-SUPPORTED)"
            r"(?: at h=([\dm,]+))?(?: \(PRED-PARTIAL ([\dm,]+)\))?", part,
        )
        if not match:
            if "PRED-" in part:
                raise ValueError(f"Unparsed predictive result: {part}")
            notes.append(part)
            continue
        relationship, pair, status, horizons, partial = match.groups()
        screens.append(PredictiveScreen(
            relationship_id=relationship, pair=pair, status=status,
            pass_horizons=tuple(horizons.split(",")) if status == "PRED-PASS" else (),
            partial_horizons=tuple((partial or (horizons if status == "PRED-PARTIAL" else "") or "").split(","))
            if partial or (status == "PRED-PARTIAL" and horizons) else (),
            source_text=part,
        ))
    return tuple(screens), "; ".join(notes)


def build_dataset(
    psv_path: Path = PSV_PATH,
    timing_path: Path = TIMING_PATH,
    aliases_path: Path = ALIASES_PATH,
) -> CardDataset:
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
    for name, resource in (("Timing", timing), ("Aliases", aliases)):
        if resource["classification_version"] != CLASSIFICATION_VERSION:
            raise ValueError(f"{name} classification version must be {CLASSIFICATION_VERSION}")
    if len({alias["id"] for alias in aliases["aliases"]}) != len(aliases["aliases"]):
        raise ValueError("Duplicate alias ids")
    if timing["source_table_sha256"] != _hash(psv_path):
        raise ValueError("Timing extract belongs to a different PSV; re-import the approved guide")
    with psv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="|"))
    if set(timing["rows"]) != {r["key"] for r in rows}:
        raise ValueError("Timing extract must cover exactly the PSV keys")
    cards = []
    for row in rows:
        ids = parse_ids(row["ids"])
        for alias in aliases["aliases"]:
            if alias["key"] == row["key"]:
                store, value = alias["id"].split(":", 1)
                ids = StoreIds(**{**ids.model_dump(), store: tuple(dict.fromkeys((*getattr(ids, store), value)))})
        evidence = timing["rows"][row["key"]]
        screens, note = parse_screen(evidence["predictive_screen"])
        peak = evidence["descriptive_peak"]
        revision = " ".join(s.strip() for s in re.split(r"(?<=\.)\s+|;\s*", row["rationale"])
                            if re.search(r"\brevis", s, re.IGNORECASE))
        cards.append(IndicatorCard(
            key=row["key"], concept=row["concept"], country=row["country"], ids=ids,
            raw_ids=row["ids"], theme=row["theme"],
            secondary=tuple(s.strip() for s in row["secondary"].split(";") if s.strip()),
            nature=row["nature"],
            timing=Timing(
                timing_class=row["timing_class"],
                basis=None if row["timing_basis"] == "—" else row["timing_basis"],
                reference=row["reference"], class_pair=row["class_pair"] or None,
                relationship_keys=tuple(s for s in row["vkeys"].split(";") if s),
                predictive_screen=screens, screen_note=note,
                descriptive_peak=None if peak in ("n.a.", "not estimated") else DescriptivePeak(source_text=peak),
            ),
            evidence=Evidence(measurement=row["ev_meas"], mechanism=row["ev_mech"], local_timing=row["ev_timing"]),
            revision_behaviour=revision or "Not specified in approved row.",
            known_failure_modes=tuple(s.strip() for s in row["flag"].split(";") if s.strip()),
            flag=row["flag"], rationale=row["rationale"], source=row["source"], frequency=row["freq"],
        ))
    keys = {c.key for c in cards}
    if any(a["key"] not in keys for a in aliases["aliases"]):
        raise ValueError("Alias targets a missing approved card")
    return CardDataset(
        source_table_sha256=_hash(psv_path), timing_extract_sha256=_hash(timing_path),
        aliases_sha256=_hash(aliases_path), cards=tuple(cards),
    )


def build_json() -> str:
    return json_text(build_dataset().model_dump(mode="json", by_alias=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-guide", type=Path, help="Refresh the verbatim timing extract from an approved guide")
    parser.add_argument("--check", action="store_true", help="Check freshness without writing")
    args = parser.parse_args()
    if args.import_guide:
        extracted = json_text(extract_timing(args.import_guide))
        if args.check:
            if TIMING_PATH.read_text(encoding="utf-8") != extracted:
                raise SystemExit("Timing extract is stale")
        else:
            _write(TIMING_PATH, extracted)
    result = build_json()
    if args.check:
        if JSON_PATH.read_text(encoding="utf-8") != result:
            raise SystemExit("Indicator-card JSON is stale; run python -m market_data_hub.indicator_cards.build")
        print("Indicator-card JSON is up to date")
    else:
        _write(JSON_PATH, result)


if __name__ == "__main__":
    main()
