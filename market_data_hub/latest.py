"""One hub layer for the most recent correct value of a concept (H2: macro, derived, release)."""
from __future__ import annotations


def read_latest(concept, *, as_of=None, as_known_at=None, con=None):
    raise NotImplementedError


def read_latest_many(concepts, *, as_of=None, as_known_at=None, con=None):
    return [read_latest(c, as_of=as_of, as_known_at=as_known_at, con=con) for c in concepts]
