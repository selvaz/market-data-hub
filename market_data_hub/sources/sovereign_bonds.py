"""Dated venue/issuer bond snapshots through plain HTTP only.

Published gross yields are used directly, never inferred from last trade prices.
These current-only exports accumulate daily; they cannot backfill past rolls.
"""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO

import numpy as np
import pandas as pd

from .sovereign_yields import COLUMNS, SourceUnavailable, _normalize, _Tables, _text

SELECTION_RULE = "nearest_10y_residual_then_earlier_maturity_then_isin"
METADATA_COLUMNS = ["isin", "maturity_date", "coupon_percent", "reference_price",
                    "price_type", "selection_rule", "source_url"]
MOT_ROOT = "https://www.borsaitaliana.it"


def select_bond(bonds: list[dict], as_of: date) -> dict:
    """Roll daily by calendar distance to date + 10 years; deterministic ties.

    The caller supplies the eligible fixed-rate nominal universe. Leap day
    targets clamp to February 28. Selection never substitutes a further bond
    just because the nearest bond has missing/stale prices.
    """
    target = pd.Timestamp(as_of) + pd.DateOffset(years=10)
    eligible = [b for b in bonds if b["maturity_date"] > pd.Timestamp(as_of)]
    if not eligible:
        raise SourceUnavailable("no eligible nominal bond")
    return min(eligible, key=lambda b: (abs((b["maturity_date"] - target).days),
                                        b["maturity_date"], b["isin"]))


def _number(raw: str) -> float:
    if not re.fullmatch(r"-?\d+(?:[.,]\d+)?", raw):
        raise SourceUnavailable("missing or invalid bond numeric field")
    value = float(raw.replace(",", "."))
    if not np.isfinite(value):
        raise SourceUnavailable("non-finite bond field")
    return value


def _tables(body: bytes):
    text = _text(body)
    if re.search(r"enodia|proof.of.work|verify you are human", text, re.I):
        raise SourceUnavailable("publisher returned an anti-bot challenge; no bypass")
    # MOT's malformed surrounding scripts confuse whole-page HTML parsers.
    # Read only literal original tables; never evaluate any publisher JavaScript.
    parser = _Tables()
    parser.feed("\n".join(re.findall(r"<table\b.*?</table>", text, re.S | re.I)))
    tables = [[[" ".join(cell.split()) for cell in row] for row in t] for t in parser.tables]
    return text, tables


def _mot_list(body: bytes) -> tuple[list[dict], set[int]]:
    text, tables = _tables(body)
    tables = [t for t in tables if any(r[:5] == ["Isin", "Descrizione", "Ultimo", "Cedola", "Scadenza"] for r in t)]
    if len(tables) != 1:
        raise SourceUnavailable("missing MOT BTP universe table")
    bonds = []
    listed = []
    for row in tables[0]:
        if not row or not re.search(r"IT\d{10}", row[0]):
            continue
        if len(row) != 6:
            raise SourceUnavailable("changed MOT universe row")
        listed.append(re.search(r"IT\d{10}", row[0])[0])
        # Excludes strips, linkers (Btpi), BTP Italia and step-up retail bonds.
        if not re.match(r"^Btp(?:-| (?:Green )?(?:Tf|Fx) )", row[1], re.I):
            continue
        isin = re.search(r"IT\d{10}", row[0])[0]
        try:
            maturity = pd.to_datetime(row[4], format="%d/%m/%Y")
        except ValueError:
            raise SourceUnavailable("invalid MOT universe maturity date") from None
        bonds.append(dict(isin=isin, maturity_date=maturity))
    if not listed or len(set(listed)) != len(listed):
        raise SourceUnavailable("empty or duplicate MOT universe page")
    pages = {int(n) for n in re.findall(r'/borsa/obbligazioni/mot/btp/lista\.html\?[^"<>]*?\bpage=(\d+)', text)}
    return bonds, pages | {1}


def _snapshot(meta: dict, when: pd.Timestamp, bond: dict, value: float,
              price_type: str, source_url: str) -> pd.DataFrame:
    if bond["reference_price"] <= 0 or bond["coupon_percent"] < 0:
        raise SourceUnavailable("invalid bond price or coupon")
    df = _normalize(meta, [(when.date().isoformat(), value)])
    for key in ["isin", "maturity_date", "coupon_percent", "reference_price"]:
        df[key] = bond[key]
    df["price_type"] = price_type
    df["source_url"] = source_url
    df["selection_rule"] = SELECTION_RULE
    return df[COLUMNS + METADATA_COLUMNS]


def _mot_bond(meta: dict, body: bytes, expected: dict, url: str) -> pd.DataFrame:
    _, tables = _tables(body)
    fields = {}
    for table in tables:
        for row in table:
            if len(row) == 2:
                if row[0] in fields and fields[row[0]] != row[1]:
                    raise SourceUnavailable("conflicting MOT bond fields")
                fields[row[0]] = row[1]
    if (fields.get("Codice Isin") != expected["isin"]
            or fields.get("Emittente") != "REPUBLIC OF ITALY"
            or fields.get("Struttura Bond") != "Plain Vanilla"
            or fields.get("Mercato") != "MOT"
            or fields.get("Valuta di Negoziazione/ Liquidazione") != "EUR/EUR"
            or fields.get("Periodicità cedola") != "Semestrale"
            or fields.get("Modalità di Negoziazione") != "Corso Secco"
            or fields.get("Indicizzazione", "") != ""):
        raise SourceUnavailable("MOT bond identity/type does not match nominal BTP")
    try:
        # Use the explicit yield-reference date, never today's request date or
        # the differently dated 'Prezzo ufficiale' / official list price.
        when = pd.to_datetime(fields["Data di riferimento"], format="%d/%m/%Y")
        maturity = pd.to_datetime(fields["Scadenza"], format="%d/%m/%y")
        if maturity != expected["maturity_date"]:
            raise SourceUnavailable("MOT maturity disagrees with universe")
        coupon = _number(fields["Tasso Cedola su base Annua"])
        if abs(coupon - 2 * _number(fields["Tasso Cedola Periodale"])) > 1e-8:
            raise SourceUnavailable("MOT fixed coupon schedule disagrees")
        bond = dict(expected, coupon_percent=coupon,
                    reference_price=_number(fields["Prezzo di riferimento"]))
        return _snapshot(meta, when, bond, _number(fields["Rendimento effettivo a scadenza lordo"]),
                         "venue_yield_reference_clean", url)
    except (KeyError, ValueError) as exc:
        raise SourceUnavailable(f"invalid MOT snapshot ({type(exc).__name__})") from None


def parse_finanzagentur(meta: dict, body: bytes) -> pd.DataFrame:
    """Issuer's current PDF republishes Bundesbank noon reference prices/yields."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not body.startswith(b"%PDF-"):
        raise SourceUnavailable("Finanzagentur response is not a PDF; no challenge bypass")
    try:
        reader = PdfReader(BytesIO(body))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        stamps = re.findall(r"Prices and yields of listed Federal securities as of (\d{2}\.\d{2}\.\d{4})", text)
        if len(set(stamps)) != 1 or not all(x in text for x in ["Yield", "in %", "Dirty price", "Bundesbank reference price"]):
            raise SourceUnavailable("changed Finanzagentur date/units/price definition")
        when = pd.to_datetime(stamps[0], format="%d.%m.%Y")
        bonds = []
        for line in text.splitlines():
            if not re.match(r"DE[A-Z0-9]{10}\s", line):
                continue
            match = re.fullmatch(
                r"(DE[A-Z0-9]{10})\s+(\d+\.\d+)\s+(.*?)\s+(\d{2}\.\d{2}\.\d{4})"
                r"\s+\d+/\d+\s+[\d.]+\s+([\d.]+)\s+(-?[\d.]+)\s+([\d.]+)", line.strip())
            if not match:
                raise SourceUnavailable("changed Finanzagentur security row")
            isin, coupon, description, maturity, price, value, dirty = match.groups()
            # Conventional nominal Bunds only, including conventional twins.
            if not description.startswith("DBR ") or description.endswith(" G"):
                continue
            bonds.append(dict(isin=isin, maturity_date=pd.to_datetime(maturity, format="%d.%m.%Y"),
                              coupon_percent=_number(coupon), reference_price=_number(price), value=_number(value)))
        if len({b['isin'] for b in bonds}) != len(bonds):
            raise SourceUnavailable("duplicate Finanzagentur securities")
        bond = select_bond(bonds, when.date())
        return _snapshot(meta, when, bond, bond["value"], "bundesbank_noon_reference_clean", meta["endpoint"])
    except SourceUnavailable:
        raise
    except (ValueError, KeyError, PdfReadError) as exc:
        raise SourceUnavailable(f"invalid Finanzagentur PDF ({type(exc).__name__})") from None


def _request_date() -> date:
    return pd.Timestamp.now(tz="Europe/Rome").date()


def fetch_bond(meta: dict, get) -> pd.DataFrame:
    """Fetch complete eligible universe before selecting; no nearest-priced fallback."""
    if meta["provider"] == "finanzagentur":
        return parse_finanzagentur(meta, get(meta["endpoint"]))
    universe, discovered = _mot_list(get(meta["endpoint"]))
    pending, seen = discovered - {1}, {1}
    while pending:
        page = min(pending)
        if page > 100:
            raise SourceUnavailable("unexpected MOT pagination size")
        bonds, pages = _mot_list(get(meta["endpoint"], {"page": page}))
        universe.extend(bonds)
        seen.add(page)
        pending = (pending | pages) - seen
    if seen != set(range(1, max(seen) + 1)):
        raise SourceUnavailable("incomplete MOT pagination")
    if len({b['isin'] for b in universe}) != len(universe):
        raise SourceUnavailable("duplicate/repeated MOT universe pages")
    # The universe is current-only, even for a requested historical range.
    # Do not select an obsolete benchmark using the caller's historical end.
    chosen = select_bond(universe, _request_date())
    for _ in range(3):
        url = f"{MOT_ROOT}/borsa/obbligazioni/mot/btp/scheda/{chosen['isin']}-MOTX.html?lang=it"
        frame = _mot_bond(meta, get(url), chosen, url)
        # Selection belongs to the published observation date, including weekends.
        correct = select_bond(universe, frame.date.iloc[0].date())
        if correct['isin'] == chosen['isin']:
            return frame
        chosen = correct
    raise SourceUnavailable("MOT snapshot dates do not permit a stable benchmark selection")
