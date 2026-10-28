"""BCB's documented public Copom statement API, distinct from daily SGS rates."""
from urllib.parse import parse_qs, urlsplit
import re
from .http import FetchError
from .latam import observation
from .latam_editions import EDITIONS, reviewed
from .policy import change
from .schedule import due, get_edition
from .tier1_publications import plain, require

ENDPOINT = "https://www.bcb.gov.br/api/servico/sitebcb/copom/comunicados_detalhes"
MEETINGS = {"2026-06-17": 279, "2026-08-05": 280, "2026-09-16": 281,
            "2026-11-04": 282, "2026-12-09": 283}


def statement(doc):
    parts = urlsplit(doc.url)
    if parts.scheme != "https" or parts.hostname != "www.bcb.gov.br" or parts.path != urlsplit(ENDPOINT).path:
        raise FetchError("BCB issuer origin needs review")
    try:
        rows = doc.json()["conteudo"]
        if len(rows) != 1:
            raise ValueError()
        row = rows[0]
        edition = row["dataReferencia"]
        meeting = MEETINGS[edition]
        if row["nro_reuniao"] != meeting or parse_qs(parts.query) != {"nro_reuniao": [str(meeting)]}:
            raise ValueError()
        title = require(r"Copom (?:reduz|mantém|eleva) a taxa Selic (?:para|em)\s*([\d,]+)% a\.a\.", row["titulo"])[1]
        text = plain(row["textoComunicado"]).replace("\u200b", "")
        value = require(r"(?:O Copom decidiu (?:reduzir|manter|elevar)|reduzindo) a taxa básica de juros (?:para|em) ([\d,]+)% a\.a\.", text)[1]
        if title != value or not 0 <= float(value.replace(",", ".")) <= 100:
            raise ValueError()
        return edition, value
    except (ValueError, KeyError, TypeError):
        raise FetchError("BCB statement schema/meeting/rate needs review") from None


def parse(doc, previous, family="bcb_policy", since="1900-01"):
    edition, printed = statement(doc)
    reviewed(family, edition)
    prior, old = statement(previous)
    if prior != max(d for d in MEETINGS if d < edition):
        raise FetchError("BCB previous decision needs review")
    delta = change(float(printed.replace(",", ".")), float(old.replace(",", ".")))
    row = observation(doc, family, edition, "bcb_selic_rate_decision", printed, method="api",
        policy=dict(rates={"selic_target": dict(rate=float(printed.replace(",", ".")), change_bp=delta,
                    change_basis="previous_issuer_statement")}, vote_split=None))
    return [row] if edition >= since else []


def fetch(client, family, since):
    from dataclasses import replace
    cache = {}
    def get(edition):
        if edition not in cache:
            meeting = MEETINGS[edition]
            doc = get_edition(client, "bcb", ENDPOINT, family, edition, {"nro_reuniao": meeting})
            cache[edition] = replace(doc, url=ENDPOINT + "?nro_reuniao=" + str(meeting)) if doc else None
        return cache[edition]
    out = []
    for edition in due(EDITIONS[family], family=family):
        if edition < since:
            continue
        doc = get(edition)
        if doc is not None:
            prior = get(max(d for d in MEETINGS if d < edition))
            if prior is None:
                raise FetchError("BCB preceding statement missing; needs review")
            rows = parse(doc, prior, family, since)
            if {r.edition for r in rows} != {edition}:
                raise FetchError("BCB requested edition needs review")
            out.extend(rows)
    return out
