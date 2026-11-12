"""Offline concept registry loading and validation (no value resolution)."""
from __future__ import annotations

import math
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import yaml

from market_data_hub.config_loader import _CONFIG_DIR, _load_yaml

SOURCE_KINDS = {"macro", "price", "derived", "release"}
ROLES = {"primary", "secondary", "fallback", "proxy", "history_only"}
FAMILIES = {
    "rates", "money_market", "real_inflation", "credit", "vol", "fx",
    "commodities", "equity", "conditions", "releases",
}
FAMILY_KEYS = {"concepts", "generate", "tolerance_bp"}
CONCEPT_KEYS = {
    "concept", "family", "label", "unit", "frequency", "calendar", "sources",
    "kind", "status", "pending_reason", "expression", "alignment", "scale",
    "check", "history", "spot_bond_justification", "pair_tolerances",
}
SOURCE_KEYS = {
    "kind", "id", "role", "max_age_business_days", "convention", "comparability_note", "unit",
}
CHECK_KEYS = {"ids", "tolerance", "unit", "note", "basis", "window"}
HISTORY_KEYS = {"mode", "id"}
PAIR_TOLERANCE_KEYS = {"series_ids", "tolerance_bp", "note"}


def _reject_unknown_keys(mapping, allowed, context):
    unknown = mapping.keys() - allowed
    if unknown:
        raise ValueError(f"{context}: unknown keys {sorted(unknown, key=str)}")


def _validate_family_config(config, context):
    if not isinstance(config, dict) or not isinstance(config.get("concepts"), list):
        raise ValueError(f"{context}: concepts must be a list")
    _reject_unknown_keys(config, FAMILY_KEYS, context)


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate configuration key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_macro_catalogue():
    """Return only the macro metadata needed by the sovereign rate reader."""
    macro = {}
    for filename in ("sovereign_layers.yaml", "sovereign_yields.yaml"):
        macro.update({e["symbol"]: e for e in _load_yaml(filename).get("series", [])})
    for group in _load_yaml("macro_series.yaml").values():
        macro.update({e["symbol"]: e for e in group})
    return macro


def load_source_catalogues():
    """Return static source metadata without opening a DB or downloading data."""
    macro = load_macro_catalogue()
    fred = {e["symbol"] for e in _load_yaml("macro_series.yaml").get("fred", [])}
    from market_data_hub.macro_releases.catalogue import INDICATORS

    return {
        "macro": macro,
        "price": {e["symbol"]: e for e in _load_yaml("tickers.yaml").get("yahoo", [])
                  if e["symbol"] not in fred},
        "release": INDICATORS,
    }


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _nonnegative(value, integer=False):
    return (
        not isinstance(value, bool)
        and isinstance(value, int if integer else (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def _validate_check(entry, sources):
    name = entry["concept"]
    check = entry.get("check")
    if check is None:
        return
    if isinstance(check, dict):
        _reject_unknown_keys(check, CHECK_KEYS, f"{name} check")
    ids = check.get("ids") if isinstance(check, dict) else None
    declared = {s["id"] for s in sources}
    if (not isinstance(ids, list) or len(ids) != 2
            or any(not isinstance(sid, str) or sid not in declared for sid in ids)
            or ids[0] == ids[1]):
        raise ValueError(f"{name}: check must name a declared pair")
    if not _nonnegative(check.get("tolerance")) or check.get("unit") not in {"bp", "pct", "absolute"}:
        raise ValueError(f"{name}: invalid check tolerance or unit")
    if not _text(check.get("note")):
        raise ValueError(f"{name}: check requires a note")
    if "basis" in check and check["basis"] != "rolling":
        raise ValueError(f"{name}: invalid check basis")
    if "window" in check and (not _nonnegative(check["window"], integer=True) or not check["window"]):
        raise ValueError(f"{name}: invalid check window")


def _validate_source(name, source, catalogues, result):
    if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
        raise ValueError(f"{name}: invalid source kind")
    allowed = SOURCE_KEYS | {"field", "live"} if source["kind"] == "price" else SOURCE_KEYS
    _reject_unknown_keys(source, allowed, f"{name} source")
    kind, sid = source["kind"], source.get("id")
    known = result if kind == "derived" else catalogues[kind]
    if not _text(sid) or sid not in known:
        raise ValueError(f"{name}: unknown {kind} source {sid}")
    if source.get("role") not in ROLES:
        raise ValueError(f"{name}: invalid source role")
    if not _nonnegative(source.get("max_age_business_days"), integer=True):
        raise ValueError(f"{name}: invalid max_age_business_days")
    if not _text(source.get("convention")):
        raise ValueError(f"{name}: missing convention")
    if "comparability_note" in source and not _text(source["comparability_note"]):
        raise ValueError(f"{name}: empty comparability_note")
    if kind == "macro":
        convention = known[sid].get("convention")
        if convention and convention != source["convention"]:
            raise ValueError(f"{name}: convention does not match {sid}")
    if kind == "price":
        if source.get("field") not in {"close", "adj_close"}:
            raise ValueError(f"{name}: invalid price field")
        if source.get("live") not in {"never", "post_close", "intraday"}:
            raise ValueError(f"{name}: invalid live policy")
    if source["role"] == "proxy" and not _text(source.get("unit")):
        raise ValueError(f"{name}: proxy requires its own unit")


def _validate_entry(entry, catalogues, result):
    name = entry["concept"]
    _reject_unknown_keys(entry, CONCEPT_KEYS, name)
    if entry.get("family") not in FAMILIES:
        raise ValueError(f"{name}: invalid family")
    for key in ("label", "unit", "frequency", "calendar"):
        if not _text(entry.get(key)):
            raise ValueError(f"{name}: missing {key}")
    if "status" in entry and entry["status"] != "pending_source":
        raise ValueError(f"{name}: invalid status")
    sources = entry.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"{name}: sources must be a list")
    dependencies = set()
    if entry.get("kind") == "derived":
        expression = entry.get("expression")
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_:]*) ([+-]) ([A-Za-z][A-Za-z0-9_:]*)", expression or "")
        if not match or entry.get("alignment") != "same_date":
            raise ValueError(f"{name}: derived requires a difference/sum and same_date alignment")
        dependencies.update((match[1], match[3]))
        if not dependencies <= result.keys():
            raise ValueError(f"{name}: unknown derived inputs {sorted(dependencies - result.keys())}")
        if "scale" in entry and not _nonnegative(entry["scale"]):
            raise ValueError(f"{name}: invalid derived scale")
    elif "kind" in entry and entry["kind"] not in SOURCE_KINDS:
        raise ValueError(f"{name}: invalid concept kind")
    elif entry.get("status") == "pending_source":
        if sources or not _text(entry.get("pending_reason")):
            raise ValueError(f"{name}: pending_source requires no sources and a reason")
    elif not sources:
        raise ValueError(f"{name}: sources must be nonempty")
    seen = set()
    for source in sources:
        _validate_source(name, source, catalogues, result)
        key = (source["kind"], source["id"])
        if key in seen:
            raise ValueError(f"{name}: duplicate source {source['id']}")
        seen.add(key)
        if source["kind"] == "derived":
            dependencies.add(source["id"])
    if sources:
        # Preserve the nine existing monthly-only sovereign chains as fallbacks.
        legacy_monthly = (entry["family"] == "rates" and len(sources) == 1
                          and sources[0]["role"] == "fallback"
                          and sources[0]["id"].startswith("IRLTLT01"))
        if not legacy_monthly and (sources[0]["role"] != "primary"
                                  or sum(s["role"] == "primary" for s in sources) != 1):
            raise ValueError(f"{name}: expected one primary first")
    _validate_check(entry, sources)
    if "pair_tolerances" in entry:
        pairs = entry["pair_tolerances"]
        if not isinstance(pairs, list):
            raise ValueError(f"{name}: pair_tolerances must be a list")
        for pair in pairs:
            if not isinstance(pair, dict):
                raise ValueError(f"{name}: invalid pair tolerance")
            _reject_unknown_keys(pair, PAIR_TOLERANCE_KEYS, f"{name} pair tolerance")
    if "history" in entry:
        history = entry["history"]
        if not isinstance(history, dict) or history.get("mode") not in {"single", "chain"}:
            raise ValueError(f"{name}: invalid history mode")
        _reject_unknown_keys(history, HISTORY_KEYS, f"{name} history")
        if history["mode"] == "single" and history.get("id") not in {s["id"] for s in sources}:
            raise ValueError(f"{name}: history must name a declared source")
    return dependencies


def load_concepts(directory=None):
    """Load and validate all packaged concept families, including generated prices."""
    directory = Path(directory) if directory is not None else _CONFIG_DIR / "concepts"
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise ValueError(f"no concept family files in {directory}")
    dependencies = [_CONFIG_DIR / filename for filename in (
        "macro_series.yaml", "sovereign_layers.yaml", "sovereign_yields.yaml", "tickers.yaml",
    )]
    return deepcopy(_load_concepts_cached(_file_versions(paths), _file_versions(dependencies)))


def _file_versions(paths):
    """Cache identity includes absolute paths and nanosecond file modification times."""
    return tuple((path.resolve(), path.stat().st_mtime_ns, path.stat().st_size) for path in paths)


@lru_cache(maxsize=8)
def _load_concepts_cached(families, dependencies):
    catalogues = load_source_catalogues()
    entries = []
    for path, *_ in families:
        config = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueLoader)
        _validate_family_config(config, path.name)
        entries.extend(config["concepts"])
        if "generate" in config:
            if path.stem != "equity" or config["generate"] != "yahoo_prices":
                raise ValueError(f"{path.name}: invalid concept generator")
            entries.extend(_price_concepts(catalogues["price"].values()))
    return validate_concepts(entries, catalogues)


def _price_concepts(tickers):
    for ticker in tickers:
        symbol = ticker["symbol"]
        quoted = symbol.startswith("^") or symbol.endswith(("=F", "=X", "-USD")) or symbol == "DX-Y.NYB"
        yield {
            "concept": f"PX:{symbol}", "family": "equity", "label": ticker["name"],
            # The canonical listing supplies currency; the universe is multi-currency.
            "unit": "quote_currency" if quoted else "quote_currency/share",
            "frequency": "D", "calendar": "listing",
            "sources": [{"kind": "price", "id": symbol, "role": "primary",
                         "max_age_business_days": 2, "field": "close" if quoted else "adj_close",
                         "live": "post_close", "convention": "unadjusted close" if quoted else "adjusted close"}],
        }


def validate_concepts(entries, catalogues=None):
    """Validate the registry union and return a concept-to-spec mapping."""
    if not isinstance(entries, list):
        raise ValueError("concepts must be a list")
    catalogues = load_source_catalogues() if catalogues is None else catalogues
    result = {}
    for entry in deepcopy(entries):
        if not isinstance(entry, dict) or not _text(entry.get("concept")):
            raise ValueError("invalid concept entry")
        name = entry["concept"]
        if name in result:
            raise ValueError(f"duplicate concept: {name}")
        result[name] = entry
    graph = {name: _validate_entry(entry, catalogues, result) for name, entry in result.items()}
    visited, visiting = set(), set()

    def visit(name):
        if name in visiting:
            raise ValueError(f"derived concept cycle: {name}")
        if name not in visited:
            visiting.add(name)
            for dependency in graph[name]:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

    for name in graph:
        visit(name)
    return result
