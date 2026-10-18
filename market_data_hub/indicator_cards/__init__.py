"""Owner-approved indicator context, with no database or network dependency."""
from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache
from importlib.resources import files
from typing import Iterable, Optional

from .models import CardDataset, IndicatorCard
from .render import render_card

__all__ = ["IndicatorCard", "indicator_card", "cards_for", "unmapped", "render_card", "dataset"]


@lru_cache(maxsize=1)
def dataset() -> CardDataset:
    """Load and strictly validate the immutable, versioned package resource."""
    return CardDataset.model_validate_json(files(__package__).joinpath("cards_v2.1.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _index():
    exact: dict[str, list[IndicatorCard]] = {}
    patterns: list[tuple[str, IndicatorCard]] = []
    for card in sorted(dataset().cards, key=lambda c: (c.country, c.concept, c.key)):
        exact.setdefault(card.key, []).append(card)
        for store in ("ms", "es", "cal", "cat", "pan", "hub"):
            for value in getattr(card.ids, store):
                for identity in (f"{store}:{value}", value):
                    if "*" in value:
                        patterns.append((identity, card))
                    else:
                        exact.setdefault(identity, []).append(card)
    return exact, patterns


def indicator_card(indicator_id_or_key: str) -> Optional[IndicatorCard]:
    """Resolve key or id; exact before wildcard, then country/concept/key order.

    Unprefixed ids are accepted across stores. Prefer prefixed ids to avoid
    cross-store collisions. Derived-series ingredients are never aliases.
    """
    identity = indicator_id_or_key.strip()
    exact, patterns = _index()
    if identity in exact:
        return exact[identity][0]
    matches = [card for pattern, card in patterns if fnmatchcase(identity, pattern)]
    return min(matches, key=lambda c: (c.country, c.concept, c.key)) if matches else None


def cards_for(ids: Iterable[str]) -> list[IndicatorCard]:
    """Mapped cards in first-input order, de-duplicated by approved key."""
    result = {}
    for identity in ids:
        card = indicator_card(identity)
        if card is not None:
            result.setdefault(card.key, card)
    return list(result.values())


def unmapped(ids: Iterable[str]) -> list[str]:
    """Unmapped ids in input order, retaining duplicates; never an error."""
    return [identity for identity in ids if indicator_card(identity) is None]
