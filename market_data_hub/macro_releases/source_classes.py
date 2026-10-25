"""Reviewed source ownership, separate from extraction verification/authority."""
from .housing_editions import SOURCES

PRIVATE_ISSUERS = frozenset(issuer for issuer, kind, _ in SOURCES.values() if kind == "private")
REDISTRIBUTORS = frozenset({"FRED", "ALFRED"})


def source_class(issuer_id, source_id, role="issuer"):
    # A verified provider file does not turn a redistributor into an issuer.
    if source_id.upper() in REDISTRIBUTORS or role == "mirror":
        return "redistributor"
    if source_id in PRIVATE_ISSUERS or role == "issuer" and issuer_id in PRIVATE_ISSUERS:
        return "private"
    return "official" if role == "issuer" else "web"


def certification_status(kind, *, verified_issuer):
    return kind if verified_issuer and kind in {"official", "private"} else "provisional"
