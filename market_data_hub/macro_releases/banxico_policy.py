"""Banxico public monetary-policy statements; SIE's token API is unnecessary."""
from datetime import date
import re
from urllib.parse import urljoin
from .http import FetchError
from .latam import fetch_editions, observation
from .latam_editions import reviewed
from .nbs_housing import Page
from .schedule import get_edition, mark_pending, note_review, publication_grace
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


ACTIONS = r"(mantener|disminuir|reducir|incrementar)"
DECISION = (r"{action}(?: en (\d+) puntos base)? el objetivo para la Tasa de Interés "
            r"Interbancaria a un día (?:en|a un nivel de) ([\d.]+)%")
BOARD_SIZE = 5


# Synonyms the statements use for the same action.
CANONICAL = {"reducir": "disminuir"}


def canonical(action):
    return CANONICAL.get(action, action)


def rejoin_numbers(text):
    """pypdf can split a printed rate across text runs ('6. 25%'); rejoin it.

    Only a rate printed after 'a un nivel de' or 'en' is repaired, so separate
    numbers elsewhere (e.g. a year then a figure) never merge.
    """
    return re.sub(r"\b((?:a un nivel de|en) \d{1,2})\.\s+(\d{1,2})\s*%", r"\1.\2%", text)


def board_names(clause):
    names = [n.strip() for n in re.split(r",\s*|\s+y\s+", clause.strip()) if n.strip()]
    if not names or len(names) != len(set(names)) or any(
            not re.fullmatch(r"[A-ZÁÉÍÓÚÑ][\w.\-]+(?: [A-ZÁÉÍÓÚÑa-z][\w.\-]+){0,5}", n) for n in names):
        raise FetchError("Banxico board member list needs review")
    return names


def vote(text, kind, action, rate):
    """Record the printed vote: unanimous or majority, counts and dissenters.

    Dissent is "Votó/Votaron en contra <names>" or "Votó/Votaron por/a favor de
    <action> el objetivo ... <rate>% <names>" naming a different action or rate
    (synonyms such as reducir/disminuir are the same action). Members voting
    with the decision may be listed too; then counts must cover the full board,
    which the statement says was present. A repeated identical sentence (a
    duplicated text layer) counts once; a name in two clauses needs review.
    """
    decision = (canonical(action), float(rate))
    sentences = list(dict.fromkeys(re.findall(r"\bVot(?:ó|aron) ([^.]+(?:\.\d[^.]*)*)\.", text)))
    majority = []
    dissent = []
    preference = None
    for sentence in sentences:
        favour = re.fullmatch(r"a favor(?: de (?:esta|la) decisión)? (.+)", sentence)
        against = re.fullmatch(r"en contra (.+?)(?:,? quien(?:es)? (?:prefería|preferían|votó|votaron|se pronunció|"
                               r"se pronunciaron) (?:por |a favor de )?" + ACTIONS + r".*)?", sentence)
        other = re.fullmatch(r"(?:por|a favor de) " + ACTIONS + r"(?: en \d+ puntos base)? el objetivo[^,]*? "
                             r"(?:en|a un nivel de) ([\d.]+)%,? (.+)", sentence)
        if against:
            dissent += board_names(against[1])
            preference = canonical(against[2]) if against[2] else preference
        elif other and (canonical(other[1]), float(other[2])) != decision:
            dissent += board_names(other[3])
            preference = canonical(other[1])
        elif other:
            majority += board_names(other[3])
        elif favour:
            majority += board_names(favour[1])
        else:
            raise FetchError("Banxico vote sentence needs review")
    if len(majority + dissent) != len(set(majority + dissent)):
        raise FetchError("Banxico board member named twice; needs review")
    if kind == "unanimidad":
        if dissent or (majority and len(majority) != BOARD_SIZE):
            raise FetchError("Banxico unanimous statement lists dissent; needs review")
        return dict(decision="unanimous", **{"for": BOARD_SIZE, "against": 0}, dissenters=[],
                    dissent_preference=None)
    if not dissent or len(dissent) >= BOARD_SIZE / 2:
        raise FetchError("Banxico majority vote/dissenters needs review")
    if majority and len(majority) + len(dissent) != BOARD_SIZE:
        raise FetchError("Banxico vote count disagreement needs review")
    return dict(decision="majority", **{"for": BOARD_SIZE - len(dissent), "against": len(dissent)},
                dissenters=dissent, dissent_preference=preference)


def parse(doc, family="banxico_policy", since="1900-01", *, archives=None):
    archives = ARCHIVES if archives is None else archives
    if doc.url not in archives.values() or not doc.body.startswith(b"%PDF"):
        raise FetchError("Banxico statement origin/format needs review")
    try:
        text, method = publication_text(doc)
    except Exception:
        raise FetchError("Banxico PDF format needs review") from None
    text = rejoin_numbers(text)
    stamp = require(r"Anuncio de Política Monetaria Comunicado de Prensa (\d+) de (\w+) de (\d{4})", text)
    edition = spanish_date(*stamp.groups())
    reviewed(family, edition)
    if archives.get(edition) != doc.url:
        raise FetchError("Banxico URL/edition needs review")
    decision = require(r"La Junta de Gobierno del Banco de México decidió "
                       + DECISION.format(action=ACTIONS), text)
    action, delta, printed = decision.groups()
    if action != "mantener" and delta is None:
        raise FetchError("Banxico statement change needs review")
    repeated = require(r"con la presencia de todos sus miembros, decidió por (unanimidad|mayor[íi]a) "
                       + DECISION.format(action=ACTIONS), text)
    if repeated.groups()[1:] != decision.groups() or not 0 <= float(printed) <= 100:
        raise FetchError("Banxico statement rate/vote disagreement needs review")
    votes = vote(text, "unanimidad" if repeated[1] == "unanimidad" else "mayoria", action, printed)
    delta = 0 if action == "mantener" else int(delta) * (1 if action == "incrementar" else -1)
    policy = dict(rates={"overnight_target": dict(rate=float(printed), change_bp=delta,
                  change_basis="issuer_statement")},
                  vote_split={"for": votes["for"], "against": votes["against"]})
    if votes["decision"] == "majority":
        # Unanimous decisions keep the policy facts already stored for them
        # (vote_split 5-0): a re-read must not change a stored vintage's
        # semantics. Majority decisions add the printed detail.
        policy["vote"] = votes
    row = observation(doc, family, edition, "banxico_overnight_rate_decision", printed, method=method,
        policy=policy)
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
                if publication_grace(family, edition):
                    mark_pending(family, edition, "not_yet_published")
                else:
                    mark_pending(family, edition, "scheduled_unreviewed")
                    note_review(family, edition, "not_yet_listed",
                                "Statement not yet listed on the Banxico decisions page")
                return None
            urls[edition] = url
        doc = get_edition(client, "banxico", urls[edition], family, edition)
        return parse(doc, family, since, archives=urls) if doc is not None else None
    return fetch_editions(family, since, "banxico", load)
