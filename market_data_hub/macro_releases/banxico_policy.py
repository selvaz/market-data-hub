"""Banxico public monetary-policy statements; SIE's token API is unnecessary."""
from datetime import date
from urllib.parse import urlsplit
import re
from .http import FetchError
from .latam import observation
from .latam_editions import EDITIONS, reviewed
from .schedule import due, get_edition
from .tier1_publications import publication_text, require

HOST = "https://www.banxico.org.mx"
PATH = "/publicaciones-y-prensa/anuncios-de-las-decisiones-de-politica-monetaria/"
ARCHIVES = {
    "2026-08-06": HOST + PATH + "{8DA3F519-341F-480E-1D4E-1AEE71DD08DA}.pdf",
    "2026-09-24": HOST + PATH + "{7D0BDB6E-E519-9C69-04AA-ABD4D31E4850}.pdf",
}
MONTHS = {m: n for n, m in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"), 1)}


def spanish_date(day, month, year):
    try:
        return date(int(year), MONTHS[month.lower()], int(day)).isoformat()
    except (ValueError, KeyError):
        raise FetchError("Spanish publication date needs review") from None


def parse(doc, family="banxico_policy", since="1900-01"):
    if doc.url not in ARCHIVES.values() or not doc.body.startswith(b"%PDF"):
        raise FetchError("Banxico statement origin/format needs review")
    try:
        text, method = publication_text(doc)
    except Exception:
        raise FetchError("Banxico PDF format needs review") from None
    stamp = require(r"Anuncio de Política Monetaria Comunicado de Prensa (\d+) de (\w+) de (2026)", text)
    edition = spanish_date(*stamp.groups())
    reviewed(family, edition)
    if ARCHIVES.get(edition) != doc.url:
        raise FetchError("Banxico URL/edition needs review")
    decision = require(r"La Junta de Gobierno del Banco de México decidió "
        r"(mantener|disminuir|incrementar)(?: en (\d+) puntos base)? el objetivo para la Tasa de Interés "
        r"Interbancaria a un día (?:en|a un nivel de) ([\d.]+)%", text)
    action, delta, printed = decision.groups()
    if action != "mantener" and delta is None:
        raise FetchError("Banxico statement change needs review")
    repeated = require(r"con la presencia de todos sus miembros, decidió por unanimidad "
        r"(mantener|disminuir|incrementar)(?: en (\d+) puntos base)? el objetivo para la Tasa de "
        r"Interés Interbancaria a un día (?:en|a un nivel de) ([\d.]+)%", text)
    if repeated.groups() != decision.groups() or not 0 <= float(printed) <= 100:
        raise FetchError("Banxico statement rate/vote disagreement needs review")
    delta = 0 if action == "mantener" else int(delta) * (1 if action == "incrementar" else -1)
    row = observation(doc, family, edition, "banxico_overnight_rate_decision", printed, method=method,
        policy=dict(rates={"overnight_target": dict(rate=float(printed), change_bp=delta,
                    change_basis="issuer_statement")}, vote_split={"for": 5, "against": 0}))
    return [row] if edition >= since else []


def fetch(client, family, since):
    out = []
    for edition in due(EDITIONS[family], family=family):
        if edition < since:
            continue
        if edition not in ARCHIVES:
            raise FetchError("Banxico due statement archive needs review")
        doc = get_edition(client, "banxico", ARCHIVES[edition], family, edition)
        if doc is not None:
            rows = parse(doc, family, since)
            if {r.edition for r in rows} != {edition}:
                raise FetchError("Banxico requested publication edition needs review")
            out.extend(rows)
    return out
