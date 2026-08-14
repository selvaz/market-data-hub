# -*- coding: utf-8 -*-
"""Does the matched row actually name the indicator it was bound to?

The catalogue vets *indicators*: 141 entries, each with a criticality argued
country by country. It does not vet the *binding*. That is done by a regex in
``match_rules``, and nothing downstream questions the result -- so a row that
slips through inherits the catalogue's authority along with its tier.

That is how the BLS 'Real Earnings' print, released alongside CPI, was filed as
'Average Hourly Earnings' (T1) and reported as a July reading of 0.0% when the
real figure, published five days earlier with the employment report, was 3.2%.
The rule was ``hourly|earnings``: the ``|`` is an OR, so 'earnings' alone was
enough. The same shape put Saxony's and Hesse's CPI under euro-area HICP.

Nothing in the pipeline could have noticed. The value parsed, the date was
plausible, three sources agreed. What gave it away was an enrichment agent
finding no press commentary -- because nobody writes about Real Earnings.
Waiting for that signal means waiting for a wrong number to reach a report
first, and it only fires for T1/T2 events that get enriched at all.

So this module asks the question directly: for one indicator, do the source
names group into one thing or several? An indicator legitimately collects
variants -- 'ISM Non-Manufacturing PMI' and 'ISM Services PMI' are the same
survey renamed -- so the mere presence of variants proves nothing. What is
worth a human's attention is the *odd one out*: a group carrying a word found
neither in the other groups nor in the catalogue's own name for the indicator.
'real', 'profit', 'yuan', 'forecast', 'saxony' are all of that shape.

This reviews, it does not reject. A binding is a judgement about what a source
means by a name, and the answer lives outside the data. The point is that the
judgement gets made by someone rather than inherited by default.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

import duckdb

# Frequency, adjustment and vintage tokens: they distinguish releases of the
# same indicator, never one indicator from another, so they are noise here.
_RUMORE = {
    "mm", "yy", "qq", "m", "y", "q", "yoy", "mom", "qoq", "sa", "nsa", "s", "a",
    "n", "final", "fnal", "prel", "prelim", "preliminary", "flash", "adv",
    "advance", "rev", "revised", "est", "estimate", "index", "rate", "the",
    "of", "to", "and", "ytd", "mo", "yr",
}


def _token(nome: str) -> frozenset[str]:
    parole = re.sub(r"[^a-z ]", " ", (nome or "").lower()).split()
    return frozenset(p for p in parole if p not in _RUMORE and len(p) > 1)


def _abbreviazione(parola: str, vocabolario: set[str]) -> bool:
    """Is this the same word another group spells out?

    Sources compress names to fit a column: 'Clm' for claims, 'Fnal' for final,
    'Unem Chng' for unemployment change, 'N-Mfg Bus Act' for non-manufacturing
    business activity. Each of those looks like a word no sibling group has,
    and flagging them buried the seven real errors under 120 spelling variants.

    A word is treated as an abbreviation when its letters appear in order
    inside a word some other group uses. It is a loose test and it will
    occasionally absolve a genuine outsider, which is the right way to be
    wrong here: this list is only useful if it stays short enough to read.
    """
    for altra in vocabolario:
        if len(altra) <= len(parola):
            continue
        i = 0
        for c in altra:
            if i < len(parola) and c == parola[i]:
                i += 1
        if i == len(parola):
            return True
    return False


def suspect_matches(
    con: duckdb.DuckDBPyConnection,
    *,
    indicator_keys: Optional[Iterable[str]] = None,
    max_share: float = 0.5,
) -> list[dict]:
    """Bindings worth a second look, most lopsided first.

    Returns one row per suspect group: the indicator it was filed under, the
    source names in the group, how many observations it accounts for, and the
    words that single it out. Ordering puts the rarest groups first -- a name
    seen once against a dominant one is the strongest signal, and the seven
    real errors found so far were all of that shape.

    ``max_share`` is what makes the list readable. A group holding most of an
    indicator's observations is that indicator's ordinary name, whatever odd
    words it carries: 'HSBC India Manufacturing PMI' is 89% of the Indian
    manufacturing PMI, and 'hsbc' says who runs the survey, not that the wrong
    survey was captured. An intruder is by nature a minority -- it arrives on
    the days the real indicator was not published.
    """
    dove, parametri = "", []
    if indicator_keys is not None:
        chiavi = list(indicator_keys)
        if not chiavi:
            return []
        dove = f"WHERE e.indicator_key IN ({','.join('?' * len(chiavi))})"
        parametri = chiavi

    righe = con.execute(f"""
        SELECT e.indicator_key, i.name, i.area, i.criticality,
               o.source_event_name, count(*) AS n
        FROM calendar_observations o
        JOIN calendar_events e USING (event_id)
        JOIN calendar_indicators i ON i.indicator_key = e.indicator_key
        {dove}
        GROUP BY ALL
    """, parametri).fetchall()

    per_indicatore: dict[tuple, dict] = {}
    for chiave, nome, area, crit, nome_fonte, n in righe:
        gruppi = per_indicatore.setdefault((chiave, nome, area, crit), {})
        g = gruppi.setdefault(_token(nome_fonte), {"names": set(), "n": 0})
        g["names"].add(nome_fonte)
        g["n"] += n

    sospetti = []
    for (chiave, nome, area, crit), gruppi in per_indicatore.items():
        if len(gruppi) < 2:
            continue
        totale = sum(g["n"] for g in gruppi.values())
        atteso = _token(nome)          # what the catalogue calls this indicator
        for token, g in gruppi.items():
            if g["n"] / totale > max_share:
                continue               # the indicator's usual name
            # words this group has and no sibling group has
            altrove = set().union(*(t for t in gruppi if t is not token))
            vocabolario = altrove | atteso
            propri = {p for p in set(token) - vocabolario
                      if not _abbreviazione(p, vocabolario)}
            if not propri:
                continue               # a variant, not an outsider
            sospetti.append({
                "indicator_key": chiave,
                "indicator_name": nome,
                "area": area,
                "criticality": crit,
                "source_names": sorted(g["names"]),
                "observations": g["n"],
                "share": g["n"] / totale,
                "distinctive_words": sorted(propri),
            })

    sospetti.sort(key=lambda s: (s["share"], -s["observations"]))
    return sospetti


def format_report(sospetti: list[dict]) -> str:
    """The review list as text, for a run log or an operator's eyes."""
    if not sospetti:
        return "No suspect binding: every indicator collects one kind of name."
    out = [f"{len(sospetti)} bindings to review "
           f"(a source name filed under an indicator it may not belong to):", ""]
    for s in sospetti:
        out.append(f"[{s['criticality']}] {s['area']} {s['indicator_name']} "
                   f"({s['indicator_key']})")
        out.append(f"      {' / '.join(s['source_names'][:3])}")
        out.append(f"      {s['observations']} obs, {s['share']:.0%} of the "
                   f"indicator | odd words: {', '.join(s['distinctive_words'])}")
        out.append("")
    return "\n".join(out)
