"""Conservative numeric transcription, without measure inference or tolerances."""

from decimal import Decimal
import re
from typing import Optional


def parse_number(
    text: str,
    *,
    decimal_separator: Optional[str] = None,
    target_scale: float = 1,
    allow_bare_magnitudes: bool = False,
) -> float:
    """Return a number in the requested scale; percent stays percentage points.

    A single comma followed by three digits defaults to grouping (162,000).
    Callers must specify decimal_separator=',' for ambiguous decimal prints.
    Reject prose with multiple numbers; a collector must identify the cell first.
    Bare m/t magnitudes require explicit opt-in; m/m and t/t are period bases.
    """
    if target_scale <= 0:
        raise ValueError("target_scale must be positive")
    text = (
        text.strip()
        .replace("\u2212", "-")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
    )
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    # Keep a sign before a currency attached to the numeric token. The input
    # print remains untouched in the caller's published_as/raw evidence.
    text = re.sub(r"^([+-])\s*([$€£¥]|[A-Z]{3})\s*(?=\d)", r"\2\1", text)
    text = re.sub(r"([+-])\s+(?=\d)", r"\1", text)
    matches = list(re.finditer(r"[+-]?\d+(?:[.,\s]\d+)*", text))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one number: {text!r}")
    token = re.sub(r"\s", "", matches[0].group())
    if decimal_separator is not None and decimal_separator not in (",", "."):
        raise ValueError("decimal_separator must be comma or dot")
    separator = decimal_separator
    if separator is None:
        if "." in token and "," in token:
            separator = "." if token.rfind(".") > token.rfind(",") else ","
        elif "," in token and not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+", token):
            separator = ","
        else:
            separator = "."
    grouping = "," if separator == "." else "."
    integer_part = token.split(separator)[0]
    if grouping in integer_part and not re.fullmatch(
        rf"[+-]?\d{{1,3}}(?:{re.escape(grouping)}\d{{3}})+", integer_part
    ):
        raise ValueError("invalid numeric grouping")
    token = token.replace(grouping, "").replace(separator, ".")
    value = Decimal(token)
    suffix = text[matches[0].end() :].strip().lower()
    if text.startswith("(") and text.endswith(")"):
        suffix = suffix.removesuffix(")").rstrip()
    # A slash belongs to the period basis, never to a magnitude (including k/k).
    suffix = re.sub(r"^(?:%\s*)?[mtyqk]\s*/\s*[mtyqk](?=\s|$)", "", suffix).strip()
    magnitude = re.match(r"^([a-z]+)(?=\s|$)", suffix)
    if magnitude and magnitude[1] in {"m", "t"} and not allow_bare_magnitudes:
        raise ValueError("bare m/t magnitude requires allow_bare_magnitudes=True")
    multiplier = Decimal(1)
    for pattern, scale in (
        (r"^(k|thousand)(?=\s|$)", "1000"),
        (r"^(mrd|b|bn|billion)(?=\s|$)", "1000000000"),
        (r"^(t|tn|trillion)(?=\s|$)", "1000000000000"),
        (r"^(m|mn|million)(?=\s|$)", "1000000"),
    ):
        if re.search(pattern, suffix):
            multiplier = Decimal(scale)
            break
    if re.search(r"\b(drop|decline|decrease|fall)\b", suffix) or (
        text.startswith("(") and text.endswith(")")
    ):
        value = -abs(value)
    return float(value * multiplier / Decimal(str(target_scale)))
