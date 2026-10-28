"""Banxico public monetary-policy statements; SIE's token API is unnecessary."""
from datetime import date
import re
from urllib.parse import urljoin
from .http import FetchError
from .latam import fetch_editions, observation
from .latam_editions import reviewed
from .nbs_housing import Page
from .schedule import get_edition, mark_pending, publication_grace
from .tier1_publications import plain, publication_text, require

HOST = "https://www.banxico.org.mx"
PATH = "/publicaciones-y-prensa/anuncios-de-las-decisiones-de-politica-monetaria/"
INDEX = HOST + PATH + "anuncios-politica-monetaria-t.html"
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


def publication_url(document, edition):
    """Bind a public statement link to the listing's printed edition date."""
    if document.url != INDEX:
        raise FetchError("Banxico listing origin needs review")
    # The issuer page mixes UTF-8 comments and Latin-1 content. All fields used
    # here (numeric date, class names and 'Texto completo') are ASCII.
    tables = [t for t in Page(document.body.decode("latin-1").encode("utf-8")).root.find_all("table")
              if "bmtableview" in t.get("class", "").split()]
    if len(tables) != 1:
        raise FetchError("Banxico listing format needs review")
    stamp = date.fromisoformat(edition).strftime("%d/%m/%y")
    urls = set()
    for row in tables[0].find_all("tr"):
        cells = row.find_all("td", {"class": "bmdateview"})
        if len(cells) != 1 or plain(cells[0].text()) != stamp:
            continue
        for link in row.find_all("a"):
            if plain(link.text()) != "Texto completo":
                continue
            url = urljoin(INDEX, link.get("href"))
            if not re.fullmatch(re.escape(HOST + PATH) + r"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}\.pdf", url):
                raise FetchError("Banxico listing statement URL needs review")
            urls.add(url)
    if len(urls) > 1:
        raise FetchError("Banxico listing edition links disagree; needs review")
    return next(iter(urls), None)


def parse(doc, family="banxico_policy", since="1900-01", *, archives=None):
    archives = ARCHIVES if archives is None else archives
    if doc.url not in archives.values() or not doc.body.startswith(b"%PDF"):
        raise FetchError("Banxico statement origin/format needs review")
    try:
        text, method = publication_text(doc)
    except Exception:
        raise FetchError("Banxico PDF format needs review") from None
    stamp = require(r"Anuncio de Política Monetaria Comunicado de Prensa (\d+) de (\w+) de (2026)", text)
    edition = spanish_date(*stamp.groups())
    reviewed(family, edition)
    if archives.get(edition) != doc.url:
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
    urls = dict(ARCHIVES)
    listing = None
    def load(edition):
        nonlocal listing
        if edition not in urls:
            if listing is None:
                listing = client.get("banxico", INDEX)
            url = publication_url(listing, edition)
            if url is None:
                mark_pending(family, edition, "not_yet_published" if publication_grace(family, edition)
                             else "scheduled_unreviewed")
                return None
            urls[edition] = url
        doc = get_edition(client, "banxico", urls[edition], family, edition)
        return parse(doc, family, since, archives=urls) if doc is not None else None
    return fetch_editions(family, since, "banxico", load)
