"""Source-clock eligibility and ordering, separate from HTTP/cache retention.

Limits are conservative project operating policies, never provider SLAs:
TSM realm/commodity 8 h (two local 4 h refresh opportunities), TSM regional
48 h (documented daily files plus one day of tolerance), UE current 6 h
(several roughly-hourly upstream observations; cadence is not guaranteed).
Sources: https://tradeskillmaster.com/public-data and
https://undermine.exchange/api.html, checked 2026-10-26.

Descriptors contain clocks and scope hashes only. Retain them after payload
expiry so reacquiring old bytes cannot erase a newer source watermark.
"""
from .market_common import fingerprint, integer


POLICY_VERSION = '2026-10-26-r1'
_TSM_FAST = {'commodities', 'realm_items', 'realm_pets'}
_TSM_REGIONAL = {'region_items', 'region_pets'}
_UE_SNAPSHOT = {'commodity_summary', 'realm_items', 'realm_pets', 'realm_stats'}
_UE_SINGLE_NOW = {'commodity_now', 'realm_item_now', 'realm_pet_now'}
_UE_REGIONAL_NOW = {'region_item_now', 'region_pet_now'}
_UE_HISTORY = {f'{kind}_{period}' for kind in ('commodity', 'realm_item', 'realm_pet')
               for period in ('hourly', 'daily')} | {'region_item_daily', 'region_pet_daily'}
_MAX_GROUPS = 4096


def _clock(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def describe(normalized):
    """Describe normalized clock semantics, without copying market values.

`freshness_clocks` contains every clock required for a current-market claim.
`ordering_clocks` guards monotonic current publication; nullable clocks are
not invented from local receive time. For zero-quantity individual UE now,
lastSeen is an ordering watermark only, never a current freshness clock.
"""
    if not isinstance(normalized, dict):
        normalized = {}
    source, dataset = normalized.get('source'), normalized.get('dataset')
    source = source if isinstance(source, str) else None
    dataset = dataset if isinstance(dataset, str) else None
    market_scope = normalized.get('scope')
    try:
        scope_key = fingerprint(market_scope) if isinstance(market_scope, dict) else None
    except (ValueError, TypeError, RecursionError):
        scope_key = None
    snapshot = _clock(normalized.get('source_snapshot_at'))
    generated = _clock(normalized.get('source_generated_at'))
    result = {'version': 1, 'provider': source, 'dataset': dataset, 'scope_key': scope_key,
              'role': 'unsupported', 'freshness_clocks': {}, 'ordering_clocks': {},
              'optional_ordering_clocks': [], 'max_age_seconds': None,
              'policy_version': POLICY_VERSION, 'clock_basis': 'unknown',
              'source_generated_at': generated}
    if source == 'tsm' and dataset in _TSM_FAST | _TSM_REGIONAL:
        result.update(role='current', clock_basis='upstream_scan',
                      max_age_seconds=8 * 3600 if dataset in _TSM_FAST else 48 * 3600,
                      freshness_clocks={'scan': snapshot}, ordering_clocks={'scan': snapshot})
    elif source == 'undermine':
        if dataset in _UE_HISTORY:
            result.update(role='history', clock_basis='file_generation_not_history_points',
                          ordering_clocks={'generation': generated})
        elif dataset in {'realms', 'suffixes'}:
            result.update(role='reference', clock_basis='reference_file_generation',
                          ordering_clocks={'generation': generated})
        elif dataset in _UE_SNAPSHOT:
            result.update(role='current', clock_basis='upstream_snapshot', max_age_seconds=6 * 3600,
                          freshness_clocks={'scan': snapshot},
                          ordering_clocks={'scan': snapshot, 'generation': generated})
        elif dataset in {'region_items', 'region_pets'}:
            # A recently generated aggregate does not establish the ages of
            # all contributing realm snapshots, which this endpoint omits.
            result.update(role='current', clock_basis='file_generation_only_upstream_unknown',
                          max_age_seconds=6 * 3600, freshness_clocks={'scan': None},
                          ordering_clocks={'generation': generated})
        elif dataset in _UE_SINGLE_NOW:
            records = normalized.get('records')
            summaries = ([r for r in records if isinstance(r, dict) and r.get('record_type') == 'aggregate']
                         if isinstance(records, list) else [])
            no_quote = (len(summaries) == 1 and type(summaries[0].get('quantity')) is int
                        and summaries[0]['quantity'] == 0)
            result.update(role='no_quote' if no_quote else 'current',
                          clock_basis='generation_no_current_quote' if no_quote else 'last_nonzero_upstream_snapshot',
                          max_age_seconds=None if no_quote else 6 * 3600,
                          freshness_clocks={} if no_quote else {'scan': snapshot},
                          ordering_clocks={'generation': generated,
                                           'scan': _clock(summaries[0].get('last_seen_at')) if no_quote else snapshot},
                          optional_ordering_clocks=['scan'])
        elif dataset in _UE_REGIONAL_NOW:
            result.update(role='current', clock_basis='per_connected_group_upstream_snapshots',
                          max_age_seconds=6 * 3600)
            records = normalized.get('records')
            if not isinstance(records, list) or not 1 <= len(records) <= _MAX_GROUPS:
                result['freshness_clocks'] = {'unverified_groups': None}
                result['ordering_clocks'] = {'unverified_groups': None}
            else:
                for record in records:
                    record = record if isinstance(record, dict) else {}
                    group = record.get('market_scope')
                    group_key = group.get('provider_group_key') if isinstance(group, dict) else None
                    if not isinstance(group_key, str) or len(group_key) != 64:
                        result['freshness_clocks']['unverified_group'] = None
                        result['ordering_clocks']['unverified_group'] = None
                        continue
                    scan_key, generation_key = 'scan:' + group_key, 'generation:' + group_key
                    if scan_key in result['freshness_clocks']:
                        result['freshness_clocks']['duplicate_group'] = None
                        result['ordering_clocks']['duplicate_group'] = None
                        continue
                    result['freshness_clocks'][scan_key] = _clock(record.get('source_snapshot_at'))
                    result['ordering_clocks'][scan_key] = _clock(record.get('source_snapshot_at'))
                    result['ordering_clocks'][generation_key] = _clock(record.get('source_generated_at'))
    return result


def _valid_descriptor(value):
    return (isinstance(value, dict) and type(value.get('version')) is int and value['version'] == 1
            and isinstance(value.get('provider'), str) and value['provider'] in {'tsm', 'undermine'}
            and isinstance(value.get('dataset'), str)
            and isinstance(value.get('scope_key'), str) and len(value['scope_key']) == 64
            and isinstance(value.get('freshness_clocks'), dict) and isinstance(value.get('ordering_clocks'), dict)
            and all(isinstance(k, str) for k in value['freshness_clocks'])
            and all(isinstance(k, str) for k in value['ordering_clocks'])
            and isinstance(value.get('optional_ordering_clocks', []), list)
            and all(isinstance(k, str) for k in value.get('optional_ordering_clocks', [])))


def assess(descriptor, now_seconds):
    """Evaluate source age now; no local download timestamp can refresh it.

`current_usable` is only source-clock eligibility. Callers must additionally
require retained/published complete payload, allowed policy and job health.
History, references and zero-quantity details are never current-price inputs;
their old history/lastSeen values are not malformed or regressed by themselves.
"""
    integer(now_seconds)
    base = {'state': 'unsupported_dataset', 'current_usable': False,
            'source_age_seconds': None, 'max_age_seconds': None, 'role': 'unsupported',
            'policy_version': POLICY_VERSION, 'source_generated_age_seconds': None}
    if not _valid_descriptor(descriptor):
        return base
    role = descriptor.get('role')
    generated = _clock(descriptor.get('source_generated_at'))
    base.update(role=role, max_age_seconds=descriptor.get('max_age_seconds'),
                source_generated_age_seconds=None if generated is None else now_seconds - generated)
    if descriptor.get('source_clock_ahead_at_receive') is True:
        # The original observation's anomaly does not silently become trusted
        # merely because the wall clock caught up. A new full response may
        # establish a separately available revision with a cleared flag.
        base['state'] = 'source_clock_ahead'
        return base
    known_ordering = [_clock(value) for value in descriptor['ordering_clocks'].values()]
    if (generated is not None and generated > now_seconds
            or any(value is not None and value > now_seconds for value in known_ordering)):
        # This also prevents a future-generated zero/reference/history file
        # from poisoning the ordering watermark for the next valid response.
        base['state'] = 'source_clock_ahead'
        return base
    if role in {'history', 'reference', 'no_quote'}:
        base['state'] = {'history': 'historical_dataset', 'reference': 'reference_dataset',
                         'no_quote': 'no_current_quote'}[role]
        return base
    if role != 'current':
        return base
    clocks = list(descriptor['freshness_clocks'].values())
    threshold = descriptor.get('max_age_seconds')
    if (not clocks or any(_clock(value) is None for value in clocks)
            or type(threshold) is not int or threshold <= 0):
        base['state'] = 'source_time_unknown'
        return base
    ages = [now_seconds - value for value in clocks]
    base['source_age_seconds'] = max(ages)
    # No undocumented clock-skew allowance. A future source timestamp remains
    # visible but cannot be a positive current-data eligibility assertion.
    if min(ages) < 0:
        base['state'] = 'source_clock_ahead'
    elif max(ages) > threshold:
        base['state'] = 'source_stale'
    else:
        base.update(state='fresh', current_usable=True)
    return base


def compare(previous, incoming):
    """Return initial/equal/advance/regression/missing_clock/incomparable.

Compare only the same provider/dataset/scope and the same clock semantics.
Same-clock byte changes are corrections, not a backwards-time rejection.
In mixed datasets a single regressing group rejects atomic promotion of the
whole response; new/missing membership cannot silently stand for equivalence.
"""
    if previous is None:
        return 'initial'
    if not _valid_descriptor(previous) or not _valid_descriptor(incoming):
        return 'incomparable'
    if any(previous.get(field) != incoming.get(field) for field in ('provider', 'dataset', 'scope_key')):
        return 'incomparable'
    before, after = previous['ordering_clocks'], incoming['ordering_clocks']
    optional = set(previous.get('optional_ordering_clocks') or []) & set(incoming.get('optional_ordering_clocks') or [])
    if not any(_clock(value) is not None for value in before.values()):
        # A legacy expired mixed payload may have no recoverable per-group
        # watermark. This unknown must not permanently block real evidence.
        return 'advance' if any(_clock(value) is not None for value in after.values()) else 'missing_clock'
    if set(before) != set(after):
        return 'incomparable'
    pairs = []
    for key in before:
        old, new = _clock(before[key]), _clock(after[key])
        if key in optional and incoming.get('role') == 'no_quote':
            # A zero response may carry a long-ago lastSeen. The store retains
            # max(previous known scan, this lastSeen) for later positive data;
            # freshness and ordering of this zero response use generation.
            continue
        if old is not None and new is None and key not in optional:
            return 'missing_clock'
        if new is None and key not in optional:
            return 'missing_clock'
        if old is not None and new is not None:
            pairs.append((old, new))
    if not pairs:
        # Unknown previous clocks can be replaced by an actual observation.
        return 'advance' if any(_clock(value) is not None for value in after.values()) else 'missing_clock'
    if any(new < old for old, new in pairs):
        return 'regression'
    if any(new > old for old, new in pairs) or any(_clock(before[key]) is None and _clock(after[key]) is not None for key in before):
        return 'advance'
    return 'equal'
