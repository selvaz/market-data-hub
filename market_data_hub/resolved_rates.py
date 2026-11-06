"""Validated sovereign rate source chains and resolution helpers."""
from __future__ import annotations

import math
import re
from pathlib import Path

import yaml

from market_data_hub.config_loader import _CONFIG_DIR, _load_yaml


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f'duplicate configuration key: {key}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def validate_resolved_rates(config, known_series):
    """Validate the full table and return a concept-to-spec mapping."""
    if not isinstance(config, dict) or not isinstance(config.get('concepts'), list):
        raise ValueError('concepts must be a list')
    tolerance = config.get('tolerance_bp')
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('invalid tolerance_bp')
    result = {}
    for entry in config['concepts']:
        if not isinstance(entry, dict):
            raise ValueError('invalid concept entry')
        name = entry.get('concept')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Z]{2}_[0-9]+[MY]', name):
            raise ValueError(f'invalid concept: {name}')
        if name in result:
            raise ValueError(f'duplicate concept: {name}')
        sources = entry.get('sources')
        if not isinstance(sources, list) or not sources:
            raise ValueError(f'{name}: sources must be nonempty')
        seen = set()
        first_convention = None
        primary_count = 0
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                raise ValueError(f'{name}: invalid source')
            sid = source.get('series_id')
            if sid not in known_series:
                raise ValueError(f'{name}: unknown series id {sid}')
            if sid in seen:
                raise ValueError(f'{name}: duplicate series id {sid}')
            seen.add(sid)
            role = source.get('role')
            if role not in {'primary', 'secondary', 'fallback', 'history_only'}:
                raise ValueError(f'{name}: invalid role')
            primary_count += role == 'primary'
            if role == 'primary' and index != 0:
                raise ValueError(f'{name}: primary must be first')
            age = source.get('max_age_business_days')
            if isinstance(age, bool) or not isinstance(age, int) or age < 0:
                raise ValueError(f'{name}: invalid max age')
            convention = source.get('convention')
            if not isinstance(convention, str) or not convention.strip():
                raise ValueError(f'{name}: missing convention')
            declared = known_series[sid].get('convention')
            if declared and convention != declared:
                raise ValueError(f'{name}: convention does not match {sid}')
            description = known_series[sid].get('name', '').lower()
            if ('zero-coupon' in description or 'spot yield' in description or 'spot rate' in description):
                if not any(word in convention.lower() for word in ('zero-coupon', 'spot yield', 'spot rate')):
                    raise ValueError(f'{name}: convention misrepresents {sid}')
            if first_convention is None:
                first_convention = convention
            elif convention != first_convention:
                a = first_convention.lower()
                b = convention.lower()
                if any(word in a for word in ('zero-coupon', 'spot yield', 'spot rate')) != any(
                    word in b for word in ('zero-coupon', 'spot yield', 'spot rate')
                ):
                    raise ValueError(f'{name}: zero-coupon and bond yield conventions are incompatible')
                if not str(source.get('comparability_note') or '').strip():
                    raise ValueError(f'{name}: incompatible conventions need a comparability note')
            if sid.startswith('IRLTLT01') and (role != 'fallback' or index != len(sources) - 1):
                raise ValueError(f'{name}: monthly OECD must be last fallback')
        if primary_count != 1 and not (len(sources) == 1 and sources[0]['role'] == 'fallback'):
            raise ValueError(f'{name}: expected one primary')
        pairs = entry.get('pair_tolerances', [])
        if not isinstance(pairs, list):
            raise ValueError(f'{name}: pair_tolerances must be a list')
        seen_pairs = set()
        for pair in pairs:
            ids = pair.get('series_ids') if isinstance(pair, dict) else None
            if not isinstance(ids, list) or len(ids) != 2 or any(sid not in seen for sid in ids) or ids[0] == ids[1]:
                raise ValueError(f'{name}: invalid pair series_ids')
            key = frozenset(ids)
            if key in seen_pairs:
                raise ValueError(f'{name}: duplicate pair tolerance')
            seen_pairs.add(key)
            paired = [source for source in sources if source['series_id'] in key]
            if paired[0]['convention'] != paired[1]['convention'] or (
                known_series[ids[0]].get('frequency', 'D') != known_series[ids[1]].get('frequency', 'D')
            ):
                raise ValueError(f'{name}: pair tolerance requires matching conventions and frequency')
            value = pair.get('tolerance_bp')
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{name}: invalid pair tolerance')
            if not isinstance(pair.get('note'), str) or not pair['note'].strip():
                raise ValueError(f'{name}: pair tolerance requires a note')
        result[name] = entry
    return result


def load_resolved_rates(path=None):
    """Load and validate against the configured sovereign and macro catalogues."""
    path = Path(path) if path is not None else _CONFIG_DIR / 'resolved_rates.yaml'
    config = yaml.load(path.read_text(encoding='utf-8'), Loader=_UniqueLoader)
    known = {}
    for filename in ('sovereign_layers.yaml', 'sovereign_yields.yaml'):
        for item in _load_yaml(filename).get('series', []):
            known[item['symbol']] = item
    for group in _load_yaml('macro_series.yaml').values():
        for item in group:
            known[item['symbol']] = item
    table = validate_resolved_rates(config, known)
    for entry in table.values():
        for source in entry['sources']:
            source['frequency'] = known[source['series_id']].get('frequency', 'D')
    return config, table
