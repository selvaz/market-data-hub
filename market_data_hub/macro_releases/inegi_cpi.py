"""INEGI monthly INPC: headline and core printed annual rates, not index ratios.

INEGI's indicator API requires a token. These public indicator bulletins do not.
The first-half edition is not in the supplied tier-1 catalogue and is excluded.
"""
from .banxico_policy import MONTHS, spanish_date
from .http import FetchError
from .latam import fetch_editions, observation
from .latam_editions import EDITIONS, reviewed
from .schedule import get_edition
from .tier1_publications import publication_text, require

BASE = "https://www.inegi.org.mx/contenidos/saladeprensa/boletines/2026/inpc/"
ARCHIVES = {d: BASE + "inpc_2q2026_" + d[5:7] + ".pdf" for d in EDITIONS["mx_cpi"]}


def parse(doc, family="mx_cpi", since="1900-01"):
    if doc.url not in ARCHIVES.values() or not doc.body.startswith(b"%PDF"):
        raise FetchError("INEGI monthly bulletin origin/format needs review")
    try:
        text, method = publication_text(doc)
    except Exception:
        raise FetchError("INEGI PDF format needs review") from None
    stamp = require(r"ÍNDICE NACIONAL DE PRECIOS AL CONSUMIDOR \(INPC\) (\d+) de (\w+) de (2026) Página 1/6", text)
    edition = spanish_date(*stamp.groups())
    period, _, _ = reviewed(family, edition)
    if ARCHIVES.get(edition) != doc.url or "Primera quincena" in text:
        raise FetchError("INEGI monthly edition/URL needs review")
    headline = require(r"La inflación anual fue de ([\d.]+)\s*% en (\w+) de (2026)", text)
    if f"{headline[3]}-{MONTHS.get(headline[2].lower(), 0):02d}" != period:
        raise FetchError("INEGI bulletin reference period needs review")
    # Cuadro 1 has 3 monthly rates, 3 annual rates, then 5 incidence columns.
    # Lock the full column headings and all 11 numeric cells, including incidence.
    require(r"Concepto Variación porcentual Incidencia1/ mensual anual mensual anual "
            r"2024 2025 2026 2024 2025 2026 2024 2025 2026 2025 2026", text)
    rates = {}
    for label, indicator in (("INPC", "mx_cpi_yy"), ("Subyacente", "mx_core_cpi_yy")):
        row = require(r"\b" + label + r" ((?:-?\d+\.\d+ ){10}-?\d+\.\d+)(?![\d.])", text)
        cells = row[1].split()
        rates[indicator] = cells[5]
    if rates["mx_cpi_yy"] != headline[1] or any(abs(float(v)) > 100 for v in rates.values()):
        raise FetchError("INEGI printed rate disagreement needs review")
    return [observation(doc, family, edition, indicator, printed, method=method)
            for indicator, printed in rates.items()] if period >= since else []


def fetch(client, family, since):
    def load(edition):
        doc = get_edition(client, "inegi", ARCHIVES[edition], family, edition)
        return parse(doc, family, since) if doc is not None else None
    return fetch_editions(family, since, "inegi", load)
