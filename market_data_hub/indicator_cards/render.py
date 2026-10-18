"""Compact deterministic text; safety wording is never truncated."""
from __future__ import annotations

from .models import IndicatorCard

MAX_CARD_CHARS = 600


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    return (head.rsplit(" ", 1)[0] if " " in head else head) + "…"


def render_card(card: IndicatorCard) -> str:
    """Render <=600 characters; full structured cards retain all evidence.

    Pair ids refer to the full card's directional relationship records. The
    preview lists every PRED-PASS horizon; it never substitutes peak lags.
    """
    timing = card.timing
    classification = "timing not classified" if timing.timing_class == "UNKNOWN" else timing.timing_class
    if timing.timing_class == "CTX":
        classification += " (context; no timing claim)"
    if timing.basis:
        classification += f"/{timing.basis}"
    parts = [
        f"{_clip(card.concept, 65)} ({card.country}); {_clip(card.theme, 32)}; {card.nature}.",
        f"{classification} vs {_clip(timing.reference, 50)}.",
    ]
    if timing.predictive_screen:
        screens = []
        show_pairs = len(timing.predictive_screen) <= 2
        for screen in timing.predictive_screen:
            name = screen.relationship_id + (f"[{screen.pair}]" if show_pairs else "")
            if screen.status == "PRED-PASS":
                screens.append(f"{name}:PRED-PASS {','.join(screen.pass_horizons)}")
            else:
                screens.append(f"{name}:{screen.status}")
        parts.append("Screen " + ("(pairs/transforms in full card) " if not show_pairs else "")
                     + "; ".join(screens) + " (revised data; no real-time validation).")
    else:
        parts.append("Predictive screen: " + ("not applicable" if timing.screen_note.startswith("n.a.") else "not tested") + ".")
    if timing.descriptive_peak:
        parts.append("Peak/event timing is descriptive only; not a validated lead.")
    elif timing.basis == "HYPOTHESIS":
        parts.append("Timing hypothesis; not validated.")
    evidence = card.evidence
    parts.append(f"Evidence meas/mech/timing: {evidence.measurement}/{evidence.mechanism}/{evidence.local_timing.rstrip('.')}.")
    fixed = " ".join(parts)
    # Allocate remaining space to source, revisions and caveats; mandatory
    # timing semantics above always survive. The complete rationale is in JSON.
    extras = [
        ("Source", card.source, 30),
        ("Revisions", card.revision_behaviour, 70),
        ("Caveat", "; ".join(filter(None, (card.flag, card.rationale))), 200),
    ]
    for label, value, cap in extras:
        available = MAX_CARD_CHARS - len(fixed) - len(label) - 4
        if available >= 12:
            fixed += f" {label}: {_clip(value, min(cap, available)).rstrip('.')}."
    if len(fixed) > MAX_CARD_CHARS:
        raise ValueError(f"Mandatory timing context exceeds {MAX_CARD_CHARS} characters: {card.key}")
    return fixed
