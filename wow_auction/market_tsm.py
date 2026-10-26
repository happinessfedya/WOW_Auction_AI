"""TSM public CSV statistics, not an executable auction order book.

Schema confirmed against the five published Retail CSV kinds on 2026-10-26.
Only source bytes are normalized here; HTTP, storage and credentials are external.
"""

import csv
from decimal import Decimal
import io
import re

from .market_common import MarketError, batch, decimal_string, fingerprint, integer, scope, utc_timestamp


DATASETS = frozenset({'commodities', 'realm_items', 'realm_pets', 'region_items', 'region_pets'})
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 400_000
MAX_FIELD_CHARS = 4096
_PRICES = ('marketValue', 'minBuyout', 'recent', 'historical', 'avgSalePrice')
_COMMON_COLUMNS = ('name', 'marketValue')
_REALM_COLUMNS = ('minBuyout', 'recent', 'historical', 'updatedAt')
_REGION_COLUMNS = ('historical', 'avgSalePrice', 'saleRate', 'soldPerDay', 'updatedAt')


def _market_scope(region, dataset, realm_slug, connected_realm_id=None):
    if not isinstance(dataset, str) or dataset not in DATASETS:
        raise MarketError('unsupported_tsm_dataset')
    if dataset.startswith('realm_'):
        if realm_slug is None:
            raise MarketError('tsm_realm_slug_required')
        kind = 'realm_unresolved' if connected_realm_id is None else 'connected_realm'
        return scope(region, kind, realm_slug=realm_slug, connected_realm_id=connected_realm_id)
    if realm_slug is not None or connected_realm_id is not None:
        raise MarketError('unexpected_tsm_realm_context')
    return scope(region, 'commodity' if dataset == 'commodities' else 'region_aggregate')


def build_url(*, region, dataset, realm_slug=None):
    """Build only a documented public Retail URL from validated scope fields."""
    market = _market_scope(region, dataset, realm_slug)
    prefix = 'https://public-data.tradeskillmaster.com/retail/' + market['region'].lower()
    if dataset == 'commodities':
        return prefix + '/commodities.csv'
    kind, subject = dataset.split('_')
    path = '/realm/' + market['realm_slug'] if kind == 'realm' else '/region'
    return prefix + path + '/' + subject + '.csv'


def _number(value, *, positive=False):
    maximum = '9223372036854775807'
    if (not isinstance(value, str) or not 1 <= len(value) <= 19
            or not value.isascii() or not value.isdecimal()
            or (len(value) == 19 and value > maximum)):
        raise MarketError('invalid_tsm_integer')
    return integer(int(value), zero=not positive)


def _timestamp(value):
    # The current schema is an integral UTC scan timestamp; preserve rather
    # than silently rounding a future fractional or timezone-dependent format.
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value):
        raise MarketError('invalid_tsm_timestamp')
    return utc_timestamp(value)


def normalize_tsm(raw, *, region, dataset, realm_slug=None, connected_realm_id=None):
    """Normalize a complete response while retaining missing quote semantics.

    Complete means all received CSV rows passed the supported contract; it does
    not establish coverage of every game item or current auction. Empty/header-
    only input cannot replace a previous populated catalogue.
    """
    market = _market_scope(region, dataset, realm_slug, connected_realm_id)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise MarketError('invalid_tsm_payload_size')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        raise MarketError('invalid_tsm_utf8') from None
    if '\x00' in text:
        raise MarketError('invalid_tsm_control_character')
    pet = dataset.endswith('_pets')
    regional = dataset.startswith('region_')
    id_column = 'petSpeciesId' if pet else 'itemId'
    expected = (id_column,) + _COMMON_COLUMNS + (_REGION_COLUMNS if regional else _REALM_COLUMNS)
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    records, seen, timestamp = [], set(), None
    try:
        header = next(reader, None)
        if header != list(expected):
            raise MarketError('unsupported_tsm_csv_header')
        for fields in reader:
            if len(records) >= MAX_ROWS:
                raise MarketError('tsm_row_limit')
            if len(fields) != len(expected) or any(len(value) > MAX_FIELD_CHARS for value in fields):
                raise MarketError('invalid_tsm_csv_row')
            values = dict(zip(expected, fields))
            identifier = _number(values[id_column], positive=True)
            if identifier in seen:
                raise MarketError('duplicate_tsm_identity')
            seen.add(identifier)
            event_time = _timestamp(values['updatedAt'])
            if timestamp is not None and timestamp != event_time:
                raise MarketError('inconsistent_tsm_snapshot_time')
            timestamp = event_time
            metrics = {key: _number(values[key]) for key in _PRICES if key in values}
            if regional:
                metrics.update({key: decimal_string(values[key]) for key in ('saleRate', 'soldPerDay')})
                if Decimal(metrics['saleRate']) > 1:
                    raise MarketError('invalid_tsm_sale_rate')
            variant = ({'kind': 'base_species', 'pet_species_id': identifier,
                        'level': None, 'breed_id': None, 'quality': None} if pet else
                       {'kind': 'base_item', 'item_id': identifier, 'bonus_ids': None,
                        'modifiers': None, 'context': None, 'quality': None})
            minimum = metrics.get('minBuyout')
            records.append({
                'record_type': 'aggregate', 'item_id': None if pet else identifier,
                'pet_species_id': identifier if pet else None,
                'name': values['name'], 'variant': variant, 'variant_key': fingerprint(variant),
                'quantity': None, 'unit_price_copper': minimum if minimum else None,
                'metrics': metrics, 'updated_at_raw': values['updatedAt'],
                'quote_status': ('not_a_quote' if regional else
                                 'aggregate_minimum' if minimum else 'no_positive_quote'),
                'variant_completeness': 'base_species_only' if pet else 'base_item_only',
            })
    except csv.Error:
        raise MarketError('invalid_tsm_csv') from None
    if not records:
        raise MarketError('empty_tsm_catalogue_unconfirmed')
    result = batch(
        'tsm', dataset, market, records, source_snapshot_at=timestamp,
        clock_semantics='upstream_scan_not_publication',
        capabilities={'aggregates': True, 'offers': False, 'depth': False, 'quantity': False,
                      'history_timeline': False, 'variant_detail': False,
                      'sale_statistics': regional, 'executable_quotes': False},
        warnings=['aggregates_are_not_executable_offers',
                  'base_species_only' if pet else 'item_variants_not_exported'],
    )
    result['coverage_semantics'] = 'published_file_rows_not_all_game_items_or_current_auctions'
    result['metric_semantics'] = {
        'historical': 'scalar_statistic_not_timeline',
        'zero_prices': 'source_zero_preserved_not_free_executable_quote',
        'saleRate': 'reported_regional_statistic_not_position_execution_probability',
        'soldPerDay': 'reported_regional_statistic_population_not_verified',
    }
    result['lineage_details'] = {
        'price_statistics': 'blizzard_auction_upstream_aggregated_by_tsm',
        'sale_statistics': 'tsm_reported_regional_statistics' if regional else None,
        'independence': 'not_independent_evidence_by_source_domain_alone',
    }
    return result
