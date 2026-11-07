"""Compact deterministic text; safety wording is never truncated."""
from __future__ import annotations

import re

from .models import IndicatorCard

MAX_CARD_CHARS = 600


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    return (head.rsplit(" ", 1)[0] if " " in head else head) + "…"


_SIDES = {
    "10y-2y spread": "10y2y", "10y-3m": "10y3m", "10y-3m spread": "10y3m",
    "5y breakeven": "BE5y", "baa-10y spread": "BAA10Y", "brent": "Brent",
    "broad usd": "USD", "building permits": "permits", "core cpi": "coreCPI",
    "core pce": "corePCE", "effr": "EFFR", "headline cpi": "CPI",
    "housing starts": "starts", "ip": "IP", "initial claims": "claims",
    "m2": "M2", "nfci": "NFCI", "retail sales": "retail", "payrolls": "payrolls",
    "unemployment": "unrate", "unemployment rate": "unrate", "vix": "VIX",
    "avg hourly earnings": "wages",
}
_PAIR_SPLIT = re.compile(r"\s+(->|vs)\s+")


def _short_side(side: str) -> str:
    side = re.sub(r"\([^)]*\)", "", side)       # "(level)", "(policy reaction)"
    side = side.split(",")[0]                    # ", long history"
    side = re.sub(r"\s+(y/y|\d+m change)$", "", side.strip())
    side = " ".join(side.split())
    return _SIDES.get(side.lower()) or side.replace(" ", "")[:12]


def short_pair(pair: str) -> str:
    """Brief direction such as ``IP->unrate``; the full pair stays in the card."""
    parts = _PAIR_SPLIT.split(pair, maxsplit=1)
    if len(parts) != 3:
        return _clip(pair, 16).replace(" ", "")
    left, arrow, right = parts
    return f"{_short_side(left)}{'->' if arrow == '->' else '~'}{_short_side(right)}"


def _screen_text(card: IndicatorCard) -> str:
    screens = card.timing.predictive_screen
    full = len(screens) <= 2
    items = []
    for screen in screens:
        name = f"{screen.relationship_id}[{screen.pair}]" if full else f"{screen.relationship_id} {short_pair(screen.pair)}"
        status = (f"PRED-PASS {','.join(screen.pass_horizons)}" if screen.status == "PRED-PASS" else screen.status)
        items.append(f"{name}:{status}")
    return "Screen " + "; ".join(items) + " (revised data; no real-time validation)."


def _head(card: IndicatorCard, with_theme: bool) -> str:
    timing = card.timing
    classification = "timing not classified" if timing.timing_class == "UNKNOWN" else timing.timing_class
    if timing.timing_class == "CTX":
        classification += " (context; no timing claim)"
    if timing.basis:
        classification += f"/{timing.basis}"
    theme = f"; {_clip(card.theme, 32)}" if with_theme else ""
    parts = [
        f"{_clip(card.concept, 65)} ({card.country}){theme}; {card.nature}.",
        f"{classification} vs {_clip(timing.reference, 50)}.",
    ]
    if timing.predictive_screen:
        parts.append(_screen_text(card))
    else:
        parts.append("Predictive screen: " + ("not applicable" if timing.screen_note.startswith("n.a.") else "not tested") + ".")
    hypothesis = timing.basis == "HYPOTHESIS"
    if timing.descriptive_peak:
        parts.append(("Class is a hypothesis; peak/event" if hypothesis else "Peak/event")
                     + " timing is descriptive only; not a validated lead.")
    elif hypothesis:
        parts.append("Timing class is a hypothesis; not validated.")
    evidence = card.evidence
    parts.append(f"Evidence meas/mech/timing: {evidence.measurement}/{evidence.mechanism}/{evidence.local_timing.rstrip('.')}.")
    return " ".join(parts)


def render_card(card: IndicatorCard) -> str:
    """Render <=600 characters; full structured cards retain all evidence.

    Every screen names its directional pair (short form beyond two screens) so
    a pass is never attributed to the wrong indicator. The preview lists every
    PRED-PASS horizon; it never substitutes peak lags. Caveats are kept whole
    where possible: the theme, revisions and source are shortened first.
    """
    caveat = " ".join("; ".join(filter(None, (card.flag, card.rationale))).split()).rstrip(".")
    for with_theme in (True, False):
        text = _head(card, with_theme)
        room = MAX_CARD_CHARS - len(text) - len(" Caveat: .")
        if len(caveat) <= room:
            break
    if len(text) > MAX_CARD_CHARS:
        raise ValueError(f"Mandatory timing context exceeds {MAX_CARD_CHARS} characters: {card.key}")
    if len(caveat) <= room:
        text += f" Caveat: {caveat}."
    elif room >= 12:
        # Last resort: cut at a clause boundary and say the caveat is incomplete.
        cut = _clip(caveat, room - len(" [truncated]")).rstrip(".…")
        text += f" Caveat: {cut}… [truncated]."
    # Remaining space goes to revisions, then source; both are lower priority.
    for label, value, cap in (("Revisions", card.revision_behaviour, 70), ("Source", card.source, 30)):
        available = MAX_CARD_CHARS - len(text) - len(label) - 4
        if available >= 12:
            text += f" {label}: {_clip(value, min(cap, available)).rstrip('.')}."
    return text
