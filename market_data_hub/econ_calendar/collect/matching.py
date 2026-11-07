# -*- coding: utf-8 -*-
"""Name normalisation shared with the matching rules in the catalogue.

This used to also reconcile Yahoo Finance, Tradays and Forex Factory against
each other -- three sources with no shared key, matched on currency + UTC
instant + name similarity. That machinery (``accoppia``, ``prepara``,
``deduci_scarto_orario``, the currency tables) went with those sources when
the calendar was rebuilt around MyFXBook alone: single-sourcing has nothing
left to reconcile.

What survives is ``normalizza()``, because ``consolidate.raccogli()`` still
needs it to reduce a source's event name to tokens comparable against the
catalogue's ``match_rules``/``match_excludes``.
"""
import re

# rumore che non distingue un indicatore da un altro.
# NB: 'change' NON va qui - distingue "Employment Change" da "Employment",
# e "Stocks Change" dal livello delle scorte.
RUMORE = {'index', 'idx', 'the', 'of', 'and', 'total', 'nsa', 'sa', 'adj',
          'adjusted', 'seasonally', 'data', 'report', 'number'}


def normalizza(nome):
    """Riduce un nome a token confrontabili con le match_rules del catalogo."""
    s = (nome or '').lower()
    s = s.replace('y/y', ' yy ').replace('m/m', ' mm ').replace('q/q', ' qq ')
    s = re.sub(r'\byoy\b', 'yy', s)
    s = re.sub(r'\bmom\b', 'mm', s)
    s = re.sub(r'\bqoq\b', 'qq', s)
    s = re.sub(r'\bann\w*\b', 'yy', s)
    s = s.replace('preliminary', 'prelim').replace('flash', 'prelim')
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    token = [t for t in s.split() if t not in RUMORE]
    return token
