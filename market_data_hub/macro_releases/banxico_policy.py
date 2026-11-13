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
SYNONYMS = {"reducir": "disminuir"}
# A printed rate. pypdf can split one across text runs ('6. 25%'): the optional
# single space after the decimal point is accepted only inside the matched
# decision and vote passages, never by rewriting the whole document.
RATE = r"(\d{1,3}(?:\.[ ]?\d{1,2})?)[ ]?%"
DECISION = (r"{action}(?: en (\d+) puntos base)? el objetivo para la Tasa de Interés "
            r"Interbancaria a un día (?:en|a un nivel de) " + RATE.replace("{", "{{").replace("}", "}}"))
# One vote sentence: it ends at a full stop, except a decimal point inside a
# rate ('6.50%' or the split '6. 50%', a digit on both sides).
VOTE = r"\bVot(?:ó|aron) ((?:[^.]|(?<=\d)\.(?=\d)|(?<=\d)\.[ ](?=\d{1,2}[ ]?%))+)\."
DISSENT_SYNTAX = r"\bVot(?:ó|aron) (?:en contra\b|(?:por|a favor de) " + ACTIONS + r"\b)"
BOARD_SIZE = 5


def canonical(action):
    return SYNONYMS.get(action, action)


def rate_value(printed):
    """The printed rate with a split-run space removed ('6. 25' -> '6.25')."""
    return printed.replace(" ", "")


def board_names(clause):
    names = [n.strip() for n in re.split(r",\s*|\s+y\s+", clause.strip()) if n.strip()]
    if not names or len(names) != len(set(names)) or any(
            not re.fullmatch(r"[A-ZÁÉÍÓÚÑ][\w.\-]+(?: [A-ZÁÉÍÓÚÑa-z][\w.\-]+){0,5}", n) for n in names):
        raise FetchError("Banxico board member list needs review")
    return names


def vote(text, kind, action, rate):
    """Record the printed vote: unanimous or majority, counts and dissenters.

    A majority statement names the dissenting members ("Votó/Votaron en contra
    ..." or "Votó/Votaron por/a favor de <action> ... <rate> <names>"). A member
    dissents when the action (after synonyms: reducir = disminuir) or the
    proposed rate differs from the decision. Members voting with the decision
    may be listed too; when listed, counts must add up to the full board, which
    the statement says was present. A member named twice counts once.
    """
    action = canonical(action)
    if kind == "unanimidad":
        if re.search(DISSENT_SYNTAX, text):
            raise FetchError("Banxico unanimous statement lists votes or dissent; needs review")
        return dict(decision="unanimous", **{"for": BOARD_SIZE, "against": 0}, dissenters=[],
                    dissent_preference=None)
    majority: dict[str, None] = {}
    dissent: dict[str, None] = {}
    preference = None
    for sentence in re.findall(VOTE, text):
        favour = re.fullmatch(r"a favor(?: de (?:esta|la) decisión)? (.+)", sentence)
        against = re.fullmatch(r"en contra (.+?)(?:,? quien(?:es)? (?:prefería|preferían|votó|votaron|se pronunció|"
                               r"se pronunciaron) (?:por |a favor de )?" + ACTIONS + r".*)?", sentence)
        other = re.fullmatch(r"(?:por|a favor de) " + ACTIONS + r"(?: en \d+ puntos base)? el objetivo[^,]*? "
                             r"(?:en|a un nivel de) "
                             + RATE + r",? (.+)", sentence)
        if against:
            dissent.update(dict.fromkeys(board_names(against[1])))
            preference = canonical(against[2]) if against[2] else preference
        elif other:
            names = dict.fromkeys(board_names(other[3]))
            if canonical(other[1]) != action or float(rate_value(other[2])) != float(rate):
                dissent.update(names)
                preference = canonical(other[1])
            else:
                majority.update(names)
        elif favour:
            majority.update(dict.fromkeys(board_names(favour[1])))
    if not dissent or len(dissent) >= BOARD_SIZE / 2 or set(majority) & set(dissent):
        raise FetchError("Banxico majority vote/dissenters needs review")
    if majority and len(majority) + len(dissent) != BOARD_SIZE:
        raise FetchError("Banxico vote count disagreement needs review")
    return dict(decision="majority", **{"for": BOARD_SIZE - len(dissent), "against": len(dissent)},
                dissenters=list(dissent), dissent_preference=preference)


def parse(doc, family="banxico_policy", since="1900-01", *, archives=None):
    archives = ARCHIVES if archives is None else archives
    if doc.url not in archives.values() or not doc.body.startswith(b"%PDF"):
        raise FetchError("Banxico statement origin/format needs review")
    try:
        text, method = publication_text(doc)
    except Exception:
        raise FetchError("Banxico PDF format needs review") from None
    stamp = require(r"Anuncio de Política Monetaria Comunicado de Prensa (\d+) de (\w+) de (\d{4})", text)
    edition = spanish_date(*stamp.groups())
    reviewed(family, edition)
    if archives.get(edition) != doc.url:
        raise FetchError("Banxico URL/edition needs review")
    decision = require(r"La Junta de Gobierno del Banco de México decidió "
                       + DECISION.format(action=ACTIONS), text)
    action, delta, printed = decision.groups()
    printed = rate_value(printed)
    if action != "mantener" and delta is None:
        raise FetchError("Banxico statement change needs review")
    repeated = require(r"con la presencia de todos sus miembros, decidió por (unanimidad|mayor[íi]a) "
                       + DECISION.format(action=ACTIONS), text)
    if (canonical(repeated[2]), repeated[3], rate_value(repeated[4])) != (canonical(action), delta, printed) \
            or not 0 <= float(printed) <= 100:
        raise FetchError("Banxico statement rate/vote disagreement needs review")
    votes = vote(text, "unanimidad" if repeated[1] == "unanimidad" else "mayoria", action, printed)
    action = canonical(action)
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
