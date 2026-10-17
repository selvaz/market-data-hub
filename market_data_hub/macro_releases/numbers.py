"""Conservative numeric transcription, without measure inference or tolerances."""

from decimal import Decimal
import re
from typing import Optional


def parse_number(
    text: str, *, decimal_separator: Optional[str] = None, target_scale: float = 1
) -> float:
    """Return a number in the requested scale; percent stays percentage points.

    A single comma followed by three digits defaults to grouping (162,000).
    Callers must specify decimal_separator=',' for ambiguous decimal prints.
    Reject prose with multiple numbers; a collector must identify the cell first.
    """
    if target_scale <= 0:
        raise ValueError("target_scale must be positive")
    text = (
        text.strip()
        .replace("\u2212", "-")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
    )
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
    multiplier = Decimal(1)
    for pattern, scale in (
        (r"^(k|thousand)\b", "1000"),
        (r"^(mrd|bn|billion)\b", "1000000000"),
        (r"^(m|mn|million)\b", "1000000"),
    ):
        if re.search(pattern, suffix):
            multiplier = Decimal(scale)
            break
    if re.search(r"\b(drop|decline|decrease|fall)\b", suffix) or re.fullmatch(
        r"\([\d.,\s]+\)", text
    ):
        value = -abs(value)
    return float(value * multiplier / Decimal(str(target_scale)))
