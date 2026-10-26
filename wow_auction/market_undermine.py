"""Documented Undermine API contracts; transport, credentials and policy live elsewhere.

API schema checked against https://undermine.exchange/api.html on 2026-10-26.
No response here proves a sale, an execution probability, or historical publication.
"""

from datetime import date
from decimal import Decimal
import re

from .market_common import MarketError, batch, integer, scope, strict_json, utc_timestamp, provider_group_scope


HOST = 'https://api.undermine.exchange'
_SUMMARIES = {
    'commodity_summary': ('region', 'commodities'),
    'realm_items': ('realm', 'items'), 'realm_pets': ('realm', 'pets'),
    'region_items': ('region', 'items'), 'region_pets': ('region', 'pets'),
}
_DETAILS = {
    **{f'commodity_{period}': ('region', 'commodities', period) for period in ('now', 'hourly', 'daily')},
    **{f'realm_item_{period}': ('realm', 'items', period) for period in ('now', 'hourly', 'daily')},
    **{f'realm_pet_{period}': ('realm', 'pets', period) for period in ('now', 'hourly', 'daily')},
    **{f'region_item_{period}': ('region', 'items', period) for period in ('now', 'daily')},
    **{f'region_pet_{period}': ('region', 'pets', period) for period in ('now', 'daily')},
}


def _object(value):
    if not isinstance(value, dict):
        raise MarketError('undermine_object_required')
    return value


def _list(value):
    if not isinstance(value, list) or len(value) > 2_000_000:
        raise MarketError('undermine_list_required_or_limit')
    return value


def _string(value, *, limit=512):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise MarketError('undermine_string_required')
    return value


def _id_key(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,19}', value)):
        raise MarketError('undermine_invalid_identifier_key')
    result = integer(int(value), zero=False)
    if str(result) != value:
        raise MarketError('undermine_noncanonical_identifier_key')
    return result


def _safe_raw(value, depth=0):
    if depth > 64:
        raise MarketError('undermine_raw_nesting_limit')
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _safe_raw(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_raw(v, depth + 1) for v in value]
    return value


def _slugs(value):
    values = _list(value)
    if not values or len(values) > 100:
        raise MarketError('undermine_connected_realms_required')
    for slug in values:
        if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9-]{1,80}', slug):
            raise MarketError('undermine_invalid_realm_slug')
    if len(set(values)) != len(values):
        raise MarketError('undermine_duplicate_realm_slug')
    return sorted(values)


def _definition(dataset, detail):
    if not isinstance(dataset, str):
        raise MarketError('unsupported_undermine_dataset')
    if dataset in ('realms', 'suffixes'):
        if detail is not None:
            raise MarketError('undermine_unexpected_detail')
        return {'area': 'static', 'collection': dataset, 'period': None, 'variant': {}, 'paid': False}
    if dataset == 'realm_stats':
        if detail is not None:
            raise MarketError('undermine_unexpected_detail')
        return {'area': 'realm', 'collection': 'stats', 'period': None, 'variant': {}, 'paid': True}
    if dataset in _SUMMARIES:
        if detail not in (None, 'base', 'full'):
            raise MarketError('undermine_invalid_summary_detail')
        area, collection = _SUMMARIES[dataset]
        return {'area': area, 'collection': collection, 'period': None, 'variant': {}, 'paid': detail == 'full'}
    if dataset not in _DETAILS:
        raise MarketError('unsupported_undermine_dataset')
    area, collection, period = _DETAILS[dataset]
    variant = {} if detail is None or detail == 'base' else _object(detail).copy()
    allowed = {'pet_breed'} if collection == 'pets' else {'item_level', 'item_suffix'}
    if not set(variant) <= allowed or ('item_suffix' in variant and 'item_level' not in variant):
        raise MarketError('undermine_invalid_variant_selector')
    if collection == 'commodities' and 'item_suffix' in variant:
        raise MarketError('undermine_commodity_suffix_unsupported')
    for number in variant.values():
        integer(number, zero=False)
    return {'area': area, 'collection': collection, 'period': period, 'variant': variant, 'paid': bool(variant)}


def _parameters(region, dataset, realm_slug, connected_realm_id, item_id, pet_species_id, detail):
    definition = _definition(dataset, detail)
    area, collection, period = definition['area'], definition['collection'], definition['period']
    kind = ('reference' if area == 'static' else 'commodity' if collection == 'commodities'
            else ('connected_realm' if connected_realm_id is not None else 'realm_unresolved') if area == 'realm'
            else 'region_aggregate')
    market_scope = scope(region, kind, connected_realm_id=connected_realm_id, realm_slug=realm_slug)
    if area == 'realm' and realm_slug is None:
        raise MarketError('undermine_realm_slug_required')
    if area != 'realm' and realm_slug is not None:
        raise MarketError('undermine_unexpected_realm_slug')
    identifier = None
    if period:
        identifier = integer(pet_species_id if collection == 'pets' else item_id, zero=False)
        if (collection == 'pets' and item_id is not None) or (collection != 'pets' and pet_species_id is not None):
            raise MarketError('undermine_unexpected_item_selector')
    elif item_id is not None or pet_species_id is not None:
        raise MarketError('undermine_unexpected_item_selector')
    return definition, market_scope, identifier


def build_url(*, region, dataset, realm_slug=None, connected_realm_id=None,
              item_id=None, pet_species_id=None, detail=None):
    definition, market_scope, identifier = _parameters(region, dataset, realm_slug, connected_realm_id,
                                                      item_id, pet_species_id, detail)
    if definition['area'] == 'static':
        return f'{HOST}/v1/static/{dataset}.json'
    area, collection, period = definition['area'], definition['collection'], definition['period']
    prefix = f'{HOST}/v1/{area}/{market_scope["region"].lower()}'
    if area == 'realm':
        prefix += '/' + realm_slug
    prefix += '/' + collection
    if not period:
        return prefix + '.json' + ('?detail=full' if detail == 'full' else '')
    prefix += '/' + str(identifier)
    for name in ('item_level', 'item_suffix', 'pet_breed'):
        if name in definition['variant']:
            prefix += '/' + str(definition['variant'][name])
    return prefix + '/' + period + '.json'


def request_cost(dataset, detail=None):
    """Documented points when Accept-Encoding:gzip is sent (not request count)."""
    d = _definition(dataset, detail)
    if d['area'] == 'static' or (not d['period'] and d['collection'] in ('pets', 'stats')):
        return 1
    if not d['period']:
        return 3
    if d['period'] == 'now':
        return 1 if d['area'] == 'realm' or d['collection'] == 'commodities' else 3
    if d['period'] == 'daily' and d['area'] == 'region' and d['collection'] != 'commodities':
        return 5
    return 3


def _echo(request, d, market_scope, identifier, detail):
    if d['area'] == 'static':
        if request.get('list') != d['collection']:
            raise MarketError('undermine_request_echo_mismatch')
        return []
    if request.get('region') != market_scope['region'].lower():
        raise MarketError('undermine_request_region_mismatch')
    realms = []
    if d['area'] == 'realm':
        realms = _slugs(request.get('realms'))
        if market_scope['realm_slug'] not in realms:
            raise MarketError('undermine_request_realm_mismatch')
    elif 'realms' in request:
        raise MarketError('undermine_unexpected_realm_echo')
    if not d['period']:
        expected_detail = None if d['collection'] == 'stats' else ('full' if detail == 'full' else 'base')
        if request.get('list') != d['collection'] or request.get('detail') != expected_detail:
            raise MarketError('undermine_request_echo_mismatch')
    else:
        id_field = 'petSpecies' if d['collection'] == 'pets' else 'itemId'
        if type(request.get(id_field)) is not int or request[id_field] != identifier or request.get('period') != d['period']:
            raise MarketError('undermine_request_echo_mismatch')
        for selector, field in (('item_level', 'itemLevel'), ('item_suffix', 'itemSuffix'), ('pet_breed', 'petBreed')):
            expected = d['variant'].get(selector)
            if field in request and (type(request[field]) is not int or request[field] != expected):
                raise MarketError('undermine_request_variant_mismatch')
            if expected is not None and request.get(field) != expected:
                raise MarketError('undermine_request_variant_mismatch')
    return realms


def _identity(collection, identifier, variant=None):
    return {'item_id': None if collection == 'pets' else identifier,
            'pet_species_id': identifier if collection == 'pets' else None,
            'variant': variant or {}, 'variant_status': 'partial_variant' if variant else 'base_identity_only'}


def _quote(raw, *, current, kind, identity, snapshot_at=None, generated_at=None):
    raw = _object(raw)
    price, quantity = integer(raw.get('price')), integer(raw.get('quantity'))
    seen = utc_timestamp(raw['seen']) if 'seen' in raw else None
    return {**identity, 'record_type': kind, 'reported_price_copper': price,
            'unit_price_copper': price if quantity > 0 and price > 0 else None,
            'quantity': quantity, 'quote_available': current and quantity > 0 and price > 0,
            'price_semantics': 'minimum_unit_asking_price' if kind != 'price_level' else 'unit_asking_price',
            'source_snapshot_at': snapshot_at, 'source_generated_at': generated_at,
            'last_seen_at': seen, 'raw_fields': _safe_raw(raw)}


def _summary_nodes(row, collection, detail, variant=None):
    """Yield overlapping aggregate levels separately; never sum parent and child."""
    variant = variant or {}
    yield row, variant
    children = [('breed', 'pet_breed')] if collection == 'pets' else [('level', 'item_level'), ('suffix', 'item_suffix')]
    for key, selector in children:
        if key not in row:
            continue
        # A child repeats its own identifier as a scalar under the same name
        # which denotes a mapping on its parent (e.g. level:{44:{level:44}}).
        if selector in variant:
            if type(row[key]) is int and row[key] == variant[selector]:
                continue
            raise MarketError('undermine_nested_variant_cycle')
        if detail != 'full':
            raise MarketError('undermine_unexpected_variant_summary')
        if selector == 'item_suffix' and (collection == 'commodities' or 'item_level' not in variant):
            raise MarketError('undermine_unexpected_suffix_summary')
        for child_key, child in _object(row[key]).items():
            number = _id_key(child_key)
            child = _object(child)
            if type(child.get(key)) is not int or child[key] != number:
                raise MarketError('undermine_variant_key_mismatch')
            yield from _summary_nodes(child, collection, detail, {**variant, selector: number})


def normalize_undermine(raw, *, region, dataset, realm_slug=None, connected_realm_id=None,
                        item_id=None, pet_species_id=None, detail=None):
    d, market_scope, identifier = _parameters(region, dataset, realm_slug, connected_realm_id,
                                             item_id, pet_species_id, detail)
    document = strict_json(raw)
    if 'error' in document:
        raise MarketError('undermine_error_payload')
    request = _object(document.get('request'))
    realms = _echo(request, d, market_scope, identifier, detail)
    if realms:
        market_scope = provider_group_scope(region, 'undermine', realms)
    result = document.get('result')
    for obj in (document, result):
        if isinstance(obj, dict) and (obj.get('partial') is True or obj.get('truncated') is True or obj.get('complete') is False):
            raise MarketError('undermine_partial_payload')
    records, warnings = [], []
    snapshot, generated = None, None
    completeness = 'complete'
    capabilities = {'requires_api_key': True, 'requires_paid': d['paid'],
                    'gzip_points': request_cost(dataset, detail), 'points_per_sliding_hour': 3000,
                    'confirmed_sales': False, 'execution_probability': False,
                    'independent_upstream': False, 'depth': False,
                    'variant_detail': bool(d['variant']) or detail == 'full' or dataset == 'realm_stats'}

    if d['area'] == 'static':
        result = _object(result)
        generated = utc_timestamp(result.get('lastUpdated'))
        seen_keys = set()
        for row in _list(result.get(d['collection'])):
            row = _object(row)
            if dataset == 'realms':
                row_region = scope(row.get('region'), 'reference')['region']
                slug = _slugs([row.get('slug')])[0]
                key = row_region, slug
                record = {'record_type': 'realm', 'region': row_region, 'realm_slug': slug,
                          'name': _string(row.get('name')), 'native_name': None,
                          'realm_id': None, 'connected_realm_id': None, 'raw_fields': _safe_raw(row)}
                if 'nativeName' in row:
                    record['native_name'] = _string(row['nativeName'])
            else:
                key = integer(row.get('id'), zero=False)
                names = _object(row.get('name'))
                for name in names.values():
                    _string(name)
                record = {'record_type': 'item_suffix', 'suffix_id': key, 'names': names, 'raw_fields': _safe_raw(row)}
            if key in seen_keys:
                raise MarketError('undermine_duplicate_reference')
            seen_keys.add(key)
            records.append(record)
    elif dataset == 'realm_stats':
        result = _object(result)
        generated, snapshot = utc_timestamp(result.get('lastUpdated')), utc_timestamp(result.get('snapshot'))
        identities = set()
        for stat, entries in _object(result.get('stats')).items():
            _string(stat, limit=80)
            for entry in _list(entries):
                entry = _object(entry)
                item = integer(entry.get('item'), zero=False)
                level = integer(entry.get('level'), zero=False)
                suffix = integer(entry['suffix'], zero=False) if 'suffix' in entry else None
                key = stat, item, level, suffix
                if key in identities:
                    raise MarketError('undermine_duplicate_stat_variant')
                identities.add(key)
                variant = {'item_level': level, 'stats': [stat]}
                if suffix is not None:
                    variant['item_suffix'] = suffix
                records.append({**_identity('items', item, variant), 'record_type': 'variant_presence',
                                'quantity': None, 'unit_price_copper': None, 'quote_available': False,
                                'source_snapshot_at': snapshot, 'source_generated_at': generated,
                                'raw_fields': _safe_raw(entry)})
        capabilities['variant_presence_index'] = True
    elif dataset in _SUMMARIES:
        result = _object(result)
        generated = utc_timestamp(result.get('lastUpdated'))
        regional = d['area'] == 'region' and d['collection'] != 'commodities'
        if not regional:
            snapshot = utc_timestamp(result.get('snapshot'))
        for key, base in _object(result.get(d['collection'])).items():
            base_id = _id_key(key)
            base = _object(base)
            identity_field = 'species' if d['collection'] == 'pets' else 'item'
            if type(base.get(identity_field)) is not int or base[identity_field] != base_id:
                raise MarketError('undermine_item_key_mismatch')
            for node, variant in _summary_nodes(base, d['collection'], detail):
                identity = _identity(d['collection'], base_id, variant)
                if regional:
                    if 'median' not in node:
                        if isinstance(node.get('suffix'), dict) and node['suffix']:
                            continue
                        raise MarketError('undermine_regional_median_missing')
                    median = integer(node['median'])
                    minimum = integer(node['min']) if 'min' in node else None
                    realm_count = integer(node['realms'], zero=False) if 'realms' in node else None
                    if not variant and (minimum is None or realm_count is None):
                        raise MarketError('undermine_region_summary_incomplete')
                    record = {**identity, 'record_type': 'regional_aggregate', 'quantity': None,
                              'unit_price_copper': None, 'quote_available': False,
                              'region_median_copper': median, 'region_min_copper': minimum,
                              'available_connected_realms': realm_count,
                              'price_semantics': 'cross_realm_asking_aggregate', 'raw_fields': _safe_raw(node)}
                else:
                    if ('price' not in node and 'quantity' not in node
                            and isinstance(node.get('suffix'), dict) and node['suffix']):
                        continue
                    record = _quote(node, current=True, kind='aggregate', identity=identity,
                                    snapshot_at=snapshot, generated_at=generated)
                record['overlapping_aggregate_level'] = 'base' if not variant else '+'.join(sorted(variant))
                records.append(record)
        if detail == 'full':
            warnings.append('summary_variant_levels_overlap_do_not_sum')
    elif d['period'] == 'now' and d['area'] == 'region' and d['collection'] != 'commodities':
        groups = set()
        for row in _list(result):
            row = _object(row)
            group = tuple(_slugs(row.get('realms')))
            if groups.intersection(group):
                raise MarketError('undermine_duplicate_connected_group')
            groups.update(group)
            record = _quote(row, current=True, kind='aggregate', identity=_identity(d['collection'], identifier, d['variant']),
                            snapshot_at=utc_timestamp(row.get('snapshot')), generated_at=utc_timestamp(row.get('lastUpdated')))
            record['market_scope'] = provider_group_scope(region, 'undermine', list(group))
            record['connected_realm_slugs'] = list(group)
            records.append(record)
        warnings.append('mixed_snapshot_times_across_connected_realms')
    else:
        result = _object(result)
        generated = utc_timestamp(result.get('lastUpdated'))
        identity = _identity(d['collection'], identifier, d['variant'])
        if d['period'] == 'now':
            last_seen = utc_timestamp(result.get('lastSeen'))
            summary = _quote(result, current=True, kind='aggregate', identity=identity, generated_at=generated)
            summary['last_seen_at'] = last_seen
            # lastSeen is a snapshot only while current quantity is positive.
            snapshot = last_seen if summary['quantity'] > 0 else None
            summary['source_snapshot_at'] = snapshot
            summary['overlapping_aggregate_level'] = 'summary_of_depth'
            records.append(summary)
            total, seen_prices = 0, set()
            for ordinal, auction in enumerate(_list(result.get('auctions'))):
                auction = _object(auction)
                quantity = 1 if d['variant'] else integer(auction.get('quantity'), zero=False)
                if d['variant'] and 'quantity' in auction and integer(auction['quantity'], zero=False) != 1:
                    raise MarketError('undermine_individual_quantity_not_one')
                price = integer(auction.get('price'))
                if not d['variant'] and price in seen_prices:
                    raise MarketError('undermine_duplicate_price_level')
                seen_prices.add(price)
                total += quantity
                variant = d['variant'].copy()
                if d['variant']:
                    for name in ('bonuses', 'sockets'):
                        if name in auction:
                            variant[name] = [integer(v) for v in _list(auction[name])]
                    if 'modifiers' in auction:
                        variant['modifiers'] = {str(_id_key(k)): integer(v) for k, v in _object(auction['modifiers']).items()}
                    if 'stats' in auction:
                        variant['stats'] = [_string(v, limit=80) for v in _list(auction['stats'])]
                elif any(k in auction for k in ('modifiers', 'bonuses', 'sockets', 'stats')):
                    raise MarketError('undermine_variant_fields_on_base_level')
                record = _quote({**auction, 'quantity': quantity}, current=True, kind='individual_offer' if d['variant'] else 'price_level',
                                identity=_identity(d['collection'], identifier, variant), snapshot_at=snapshot, generated_at=generated)
                record['offer_ordinal'] = ordinal
                record['auction_id'] = None
                record['price_semantics'] = 'unit_asking_price'
                record['raw_fields'] = _safe_raw(auction)
                record['quantity_basis'] = 'documented_individual_item' if d['variant'] else 'reported'
                records.append(record)
            if total != summary['quantity']:
                raise MarketError('undermine_depth_quantity_mismatch')
            if total and min(seen_prices) != summary['reported_price_copper']:
                raise MarketError('undermine_depth_minimum_price_mismatch')
            capabilities['depth'] = True
            capabilities['depth_representation'] = 'individual_items_without_auction_id' if d['variant'] else 'aggregated_price_levels'
        else:
            duplicate_points = set()
            for point in _list(result.get(d['period'])):
                point = _object(point)
                if d['period'] == 'hourly':
                    point_time = utc_timestamp(point.get('snapshot'))
                    point_key = point_time
                    record = _quote(point, current=False, kind='history', identity=identity, snapshot_at=point_time, generated_at=generated)
                    record['aggregation_rule'] = 'blizzard_snapshot_not_fixed_hourly_interval'
                else:
                    day = point.get('day')
                    try:
                        if not isinstance(day, str) or len(day) != 10 or date.fromisoformat(day).isoformat() != day:
                            raise ValueError
                    except ValueError:
                        raise MarketError('undermine_invalid_day') from None
                    point_key = day
                    record = _quote(point, current=False, kind='history', identity=identity, generated_at=generated)
                    record.update(period_day=day, period_start=None, period_end_exclusive=None,
                                  period_timezone=None, is_final=None,
                                  aggregation_rule=('sum_of_realm_daily_maxima_mean_corresponding_prices'
                                                    if d['area'] == 'region' and d['collection'] != 'commodities'
                                                    else 'daily_max_quantity_and_price_at_that_snapshot'))
                    record['price_semantics'] = ('mean_realm_daily_selected_asking_prices'
                                                 if d['area'] == 'region' and d['collection'] != 'commodities'
                                                 else 'minimum_asking_price_at_daily_max_quantity_snapshot')
                if point_key in duplicate_points:
                    raise MarketError('undermine_duplicate_history_point')
                duplicate_points.add(point_key)
                record.update(public_available_lower_bound=None, public_available_upper_bound=None,
                              publication_basis='unknown', reconstructed_historical_eligible=False)
                records.append(record)
            capabilities['history'] = d['period']
            if d['period'] == 'daily':
                warnings.append('daily_timezone_finalization_and_publication_unknown')
    if not records:
        completeness = 'unknown'
        warnings.append('empty_collection_unverified')
    if any(r.get('quantity') == 0 for r in records):
        warnings.append('zero_quantity_price_is_not_current_quote')
    if market_scope['kind'] == 'realm_unresolved':
        warnings.append('numeric_connected_realm_unverified')
    for record in records:
        record.setdefault('public_available_lower_bound', None)
        record.setdefault('public_available_upper_bound', None)
        record.setdefault('publication_basis', 'unknown')
        if realms:
            record['connected_realm_slugs'] = realms
    return batch('undermine', dataset, market_scope, records, source_snapshot_at=snapshot,
                 source_generated_at=generated, capabilities=capabilities, completeness=completeness,
                 warnings=sorted(set(warnings)), upstream='blizzard_auction_snapshot',
                 clock_semantics={'snapshot': 'upstream_blizzard_snapshot', 'lastUpdated': 'ue_file_generation',
                                  'lastSeen': 'last_nonzero_upstream_snapshot_not_current_if_zero',
                                  'historical_publication': 'unknown'})
