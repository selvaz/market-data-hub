"""Offline concept registry loading and validation (no value resolution)."""
from __future__ import annotations


def load_concepts(directory=None):
    """Load and validate all packaged concept families, including generated prices."""
    raise NotImplementedError


def validate_concepts(entries, catalogues=None):
    """Validate the registry union and return a concept-to-spec mapping."""
    raise NotImplementedError
