"""ECOICOP v2: flash from first-released FLS, final from monthly rates.

Dataset codes/dimensions verified live 2026-10-17. Old manr/mmor datasets are
archived at 2025. JSON-stat's updated is a DATASET timestamp, not a release date.
"""
from __future__ import annotations

from itertools import product
import math

from .adapters import Observation
from .catalogue import hicp_indicator
from .http import FetchError, json_payload

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
DATASETS = {"flash": "prc_hicp_fpd", "final": "prc_hicp_minr"}
# Explicit reviewed edition metadata. In particular, never turn dataset updated
# into a publication date for every old observation in the response.
PUBLICATIONS = {
    ("2026-09", "flash"): "2026-10-02", ("2026-09", "final"): "2026-10-16",
    ("2026-08", "flash"): "2026-09-01", ("2026-08", "final"): "2026-09-17",
    ("2026-07", "flash"): "2026-07-31", ("2026-07", "final"): "2026-08-19",
    ("2026-06", "flash"): "2026-07-01", ("2026-06", "final"): "2026-07-17",
}


def cells(payload):
    dims, sizes = payload["id"], payload["size"]
    codes = [payload["dimension"][k]["category"]["index"] for k in dims]
    values = payload.get("value", {})
    statuses = payload.get("status", {})
    for combination in product(*codes):
        index = sum(codes[i][code] * math.prod(sizes[i + 1:]) for i, code in enumerate(combination))
        value = values[index] if isinstance(values, list) else values.get(str(index))
        status = statuses[index] if isinstance(statuses, list) else statuses.get(str(index), "")
        if value is not None:
            yield dict(zip(dims, combination)), value, status


class EurostatAdapter:
    def __init__(self, client):
        self.client = client

    def fetch(self, families, since):
        if "ea_hicp" not in families:
            return []
        out = []
        for stage, dataset in DATASETS.items():
            params = {"lang": "EN", "geo": "EA", "coicop18": "TOTAL", "sinceTimePeriod": since}
            if stage == "flash":
                params["release"] = "FLS"
            doc = self.client.get("eurostat", BASE + dataset, params)
            out.extend(self.parse(doc, stage=stage, since=since))
        return out

    @staticmethod
    def parse(document, *, stage, since="1900-01", geography="EA"):
        payload = json_payload(document)
        if payload.get("source") != "ESTAT" or payload.get("class") != "dataset":
            raise FetchError("Eurostat dataset missing")
        required = {"freq", "unit", "coicop18", "geo", "time"}
        if not required.issubset(payload.get("id", [])) or (stage == "flash" and "release" not in payload["id"]):
            raise FetchError("Eurostat dimensions changed")
        out = []
        periods = payload["dimension"]["time"]["category"]["index"]
        newest = max(periods) if periods else None
        for dim, value, flag in cells(payload):
            if dim["geo"] != geography or dim["coicop18"] != "TOTAL" or dim["freq"] != "M":
                raise FetchError("Eurostat unexpected measure/geography")
            if dim["unit"] not in ("RCH_A", "RCH_M") or dim["time"] < since:
                continue
            if stage == "flash" and dim.get("release") != "FLS":
                continue
            if stage == "final" and "e" in flag:
                continue  # A provisional observation is never promoted to final.
            period = dim["time"]
            # minr is the current edition, including earlier revised periods.
            # fpd/FLS actually preserves each historical flash publication.
            publication = PUBLICATIONS.get((newest if stage == "final" else period, stage))
            if publication is None:
                raise FetchError(f"Eurostat publication metadata needs review: {period} {stage}")
            basis = "yy" if dim["unit"] == "RCH_A" else "mm"
            indicator = hicp_indicator(period, basis, geography)
            out.append(Observation(indicator.id, "ea_hicp", period, value, str(value),
                                   DATASETS[stage] + ":" + geography + ":TOTAL:" + dim["unit"] + ":" + stage,
                                   "Eurostat", document, publication, publication + "T00:00:00Z",
                                   stage=stage, provider_vintage=payload.get("updated"),
                                   composition_id=indicator.geography,
                                   revision="initial" if stage == "flash" else "routine"))
        if not out:
            raise FetchError(f"Eurostat has no certified {stage} observations in requested window")
        return out
