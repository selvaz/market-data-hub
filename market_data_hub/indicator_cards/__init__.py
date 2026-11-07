"""Owner-approved indicator context, with no database or network dependency."""
from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache
from importlib.resources import files
from typing import Iterable, Optional

from .models import CLASSIFICATION_VERSION, CardDataset, IndicatorCard
from .render import render_card

__all__ = ["IndicatorCard", "indicator_card", "cards_for", "unmapped", "render_card", "dataset"]


_STORES = ("ms", "es", "cal", "cat", "pan", "hub")


@lru_cache(maxsize=1)
def dataset() -> CardDataset:
    """Load and strictly validate the immutable, versioned package resource."""
    return CardDataset.model_validate_json(
        files(__package__).joinpath(f"cards_{CLASSIFICATION_VERSION}.json").read_text(encoding="utf-8"),
    )


@lru_cache(maxsize=1)
def _index():
    exact: dict[str, list[IndicatorCard]] = {}
    patterns: list[tuple[str, IndicatorCard]] = []
    for card in sorted(dataset().cards, key=lambda c: (c.country, c.concept, c.key)):
        exact.setdefault(card.key, []).append(card)
        for store in _STORES:
            for value in getattr(card.ids, store):
                for identity in (f"{store}:{value}", value):
                    if "*" in value:
                        patterns.append((identity, card))
                    else:
                        exact.setdefault(identity, []).append(card)
    return exact, patterns


def _own_id_row(card: IndicatorCard, identity: str) -> bool:
    """True when the row's whole id list is exactly this one id (same prefix)."""
    own = [(store, value) for store in _STORES for value in getattr(card.ids, store)]
    return len(own) == 1 and identity in (f"{own[0][0]}:{own[0][1]}", own[0][1])


def indicator_card(indicator_id_or_key: Optional[str]) -> Optional[IndicatorCard]:
    """Resolve key or id; exact before wildcard, then country/concept/key order.

    A row whose own id list is exactly the requested id wins a shared id (so
    ``pan:bis_policy_rate`` is the BIS panel row). ``None`` resolves to None.
    Unprefixed ids are accepted across stores. Prefer prefixed ids to avoid
    cross-store collisions. Derived-series ingredients are never aliases.
    """
    if indicator_id_or_key is None:
        return None
    identity = indicator_id_or_key.strip()
    exact, patterns = _index()
    if identity in exact:
        candidates = exact[identity]
        return min(candidates, key=lambda c: (
            c.key != identity, not _own_id_row(c, identity), c.country, c.concept, c.key))
    matches = [card for pattern, card in patterns if fnmatchcase(identity, pattern)]
    return min(matches, key=lambda c: (c.country, c.concept, c.key)) if matches else None


def _reject_bare_string(ids) -> None:
    if isinstance(ids, (str, bytes)):
        raise TypeError("Expected an iterable of ids, not a single string; wrap it in a list")


def cards_for(ids: Iterable[str]) -> list[IndicatorCard]:
    """Mapped cards in first-input order, de-duplicated by approved key."""
    _reject_bare_string(ids)
    result = {}
    for identity in ids:
        card = indicator_card(identity)
        if card is not None:
            result.setdefault(card.key, card)
    return list(result.values())


def unmapped(ids: Iterable[str]) -> list[str]:
    """Unmapped ids in input order, retaining duplicates; a bare string is a TypeError."""
    _reject_bare_string(ids)
    return [identity for identity in ids if indicator_card(identity) is None]
