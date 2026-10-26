"""Retail Blizzard market/reference normalization; no network or credentials.

Primary endpoint contract: community.developer.battle.net documentation/world-of-warcraft/game-data-apis.
Last-Modified describes the document, not a transaction or proven publication.
"""

from datetime import timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from .market_common import MarketError, batch, fingerprint, integer, scope, strict_json


_LOCALES = {
    'EU': {'en_GB', 'es_ES', 'fr_FR', 'ru_RU', 'de_DE', 'pt_PT', 'it_IT'},
    'US': {'en_US', 'es_MX', 'pt_BR'}, 'KR': {'ko_KR'}, 'TW': {'zh_TW'},
}
_DATASETS = {'commodities', 'auctions', 'realm_index', 'realm', 'connected_realm'}


def _region(value):
    if not isinstance(value, str):
        raise MarketError('unsupported_market_region')
    return scope(value, 'reference')['region']


def _path(dataset, connected_realm_id, realm_slug):
    if not isinstance(dataset, str) or dataset not in _DATASETS:
        raise MarketError('unsupported_blizzard_dataset')
    if dataset == 'commodities':
        if connected_realm_id is not None or realm_slug is not None:
            raise MarketError('commodity_scope_has_realm')
        return '/data/wow/auctions/commodities'
    if dataset in {'auctions', 'connected_realm'}:
        integer(connected_realm_id, zero=False)
        if realm_slug is not None:
            scope('EU', 'reference', realm_slug=realm_slug)
        suffix = '/auctions' if dataset == 'auctions' else ''
        return f'/data/wow/connected-realm/{connected_realm_id}{suffix}'
    if connected_realm_id is not None:
        raise MarketError('unexpected_connected_realm_id')
    if dataset == 'realm_index':
        if realm_slug is not None:
            raise MarketError('realm_index_has_slug')
        return '/data/wow/realm/index'
    scope('EU', 'realm_unresolved', realm_slug=realm_slug)
    return '/data/wow/realm/' + realm_slug


def build_url(*, region, dataset, connected_realm_id=None, realm_slug=None, locale='ru_RU'):
    region = _region(region)
    if not isinstance(locale, str) or locale not in _LOCALES[region]:
        raise MarketError('unsupported_blizzard_locale')
    path = _path(dataset, connected_realm_id, realm_slug)
    return f'https://{region.lower()}.api.blizzard.com{path}?' + urlencode({
        'namespace': 'dynamic-' + region.lower(), 'locale': locale})


def _object(value, code='invalid_blizzard_object'):
    if not isinstance(value, dict):
        raise MarketError(code)
    return value


def _list(value, *, maximum=2_000_000):
    if not isinstance(value, list) or len(value) > maximum:
        raise MarketError('invalid_blizzard_record_list')
    return value


def _text(value, *, maximum=4096):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise MarketError('invalid_blizzard_text')
    return value


def _href(link, region, path_pattern):
    value = _text(_object(link).get('href'), maximum=2048)
    try:
        parts = urlsplit(value)
        query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=8)
    except ValueError:
        raise MarketError('invalid_blizzard_reference_url') from None
    match = re.fullmatch(path_pattern, parts.path)
    if (parts.scheme != 'https' or parts.netloc != region.lower() + '.api.blizzard.com'
            or parts.fragment or not match or set(query) - {'namespace', 'locale'}
            or query.get('namespace') != ['dynamic-' + region.lower()]
            or ('locale' in query and (len(query['locale']) != 1 or query['locale'][0] not in _LOCALES[region]))):
        raise MarketError('blizzard_reference_context_mismatch')
    return match


def _self_link(data, region, path):
    if '_links' in data:
        links = _object(data['_links'])
        if 'next' in links:
            raise MarketError('incomplete_blizzard_snapshot')
        if 'self' in links:
            _href(links['self'], region, re.escape(path))


def _last_modified(value):
    if value is None:
        return None, ['blizzard_last_modified_missing']
    try:
        if not isinstance(value, str) or len(value) > 100:
            raise ValueError
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            raise ValueError
        return integer(int(parsed.astimezone(timezone.utc).timestamp())), []
    except (ValueError, TypeError, OverflowError, OSError):
        return None, ['blizzard_last_modified_invalid']


def _variant(value):
    """Preserve unknown item fields, but never coerce decimal values to strings."""
    item = _object(value, 'invalid_blizzard_item')
    integer(item.get('id'), zero=False)
    pending = [(item, 0)]
    visited = 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if visited > 4096 or depth > 16:
            raise MarketError('blizzard_item_variant_limit')
        if isinstance(node, dict):
            if any(not isinstance(k, str) or not 1 <= len(k) <= 128 for k in node):
                raise MarketError('invalid_blizzard_item_field')
            pending.extend((x, depth + 1) for x in node.values())
        elif isinstance(node, list):
            pending.extend((x, depth + 1) for x in node)
        elif type(node) is int:
            integer(node)
        elif isinstance(node, str):
            if len(node) > 4096:
                raise MarketError('blizzard_item_variant_limit')
        elif node is not None and type(node) is not bool:
            raise MarketError('unsupported_blizzard_item_value')
    for key in ('context', 'pet_species_id', 'pet_level', 'pet_breed_id', 'pet_quality_id'):
        if key in item:
            integer(item[key])
    if 'bonus_lists' in item:
        for bonus in _list(item['bonus_lists'], maximum=1024):
            integer(bonus, zero=False)
    if 'modifiers' in item:
        for modifier in _list(item['modifiers'], maximum=1024):
            modifier = _object(modifier)
            integer(modifier.get('type'))
            integer(modifier.get('value'))
    return item


def _realm_record(value, region):
    value = _object(value)
    rid = integer(value.get('id'), zero=False)
    slug = value.get('slug')
    scope(region, 'realm_unresolved', realm_slug=slug)
    name = value.get('name')
    if isinstance(name, dict):
        if not name or len(name) > 32:
            raise MarketError('invalid_blizzard_localized_name')
        for key, text in name.items():
            _text(key, maximum=20)
            _text(text, maximum=256)
    else:
        _text(name, maximum=256)
    if 'key' in value:
        _href(value['key'], region, '/data/wow/realm/(?:' + re.escape(slug) + '|' + str(rid) + ')')
    return {'record_type': 'realm', 'realm_id': rid, 'slug': slug, 'name': name,
            'connected_realm_id': None, 'region': region}


def normalize_blizzard(raw, *, region, dataset, connected_realm_id=None, realm_slug=None,
                       last_modified=None, expected_realm_id=None):
    region = _region(region)
    path = _path(dataset, connected_realm_id, realm_slug)
    if expected_realm_id is not None:
        integer(expected_realm_id, zero=False)
    data = strict_json(raw)
    if data.get('truncated') is True or data.get('complete') is False:
        raise MarketError('incomplete_blizzard_snapshot')
    _self_link(data, region, path)
    snapshot, warnings = _last_modified(last_modified)
    records = []
    if dataset in {'auctions', 'commodities'}:
        kind = 'commodity' if dataset == 'commodities' else 'connected_realm'
        market_scope = scope(region, kind, connected_realm_id=connected_realm_id)
        if dataset == 'auctions':
            if 'connected_realm' in data:
                _href(data['connected_realm'], region, f'/data/wow/connected-realm/{connected_realm_id}')
            if 'commodities' in data:
                _href(data['commodities'], region, '/data/wow/auctions/commodities')
        elif 'connected_realm' in data:
            raise MarketError('commodity_payload_has_connected_realm')
        seen = set()
        for auction in _list(data.get('auctions')):
            auction = _object(auction)
            aid = integer(auction.get('id'), zero=False)
            if aid in seen:
                raise MarketError('duplicate_blizzard_auction_id')
            seen.add(aid)
            quantity = integer(auction.get('quantity'), zero=False)
            item = _variant(auction.get('item'))
            time_left = _text(auction.get('time_left'), maximum=64)
            if time_left not in {'SHORT', 'MEDIUM', 'LONG', 'VERY_LONG'}:
                if 'unknown_blizzard_time_left' not in warnings:
                    warnings.append('unknown_blizzard_time_left')
            row = {'record_type': 'offer', 'auction_id': aid, 'item_id': item['id'],
                   'variant': item, 'variant_key': fingerprint(item), 'quantity': quantity,
                   'time_left': time_left, 'unit_price_copper': None, 'buyout_total_copper': None,
                   'bid_total_copper': None, 'quote_available': False}
            if dataset == 'commodities':
                if 'buyout' in auction or 'bid' in auction:
                    raise MarketError('commodity_has_lot_price')
                price = integer(auction.get('unit_price'))
                row.update(reported_unit_price_copper=price, unit_price_copper=price or None,
                           quote_available=price > 0, price_semantics='commodity_unit_price')
                if price == 0 and 'zero_unit_price_not_quote' not in warnings:
                    warnings.append('zero_unit_price_not_quote')
            else:
                if 'unit_price' in auction or not {'buyout', 'bid'} & auction.keys():
                    raise MarketError('invalid_connected_auction_price')
                buyout = integer(auction['buyout']) if 'buyout' in auction else None
                bid = integer(auction['bid']) if 'bid' in auction else None
                row.update(buyout_total_copper=buyout, bid_total_copper=bid,
                           quote_available=buyout is not None and buyout > 0,
                           price_semantics='auction_lot_total')
            records.append(row)
        capabilities = {'offer_depth': True, 'auction_ids': True, 'quantities': True,
                        'variants': True, 'history': False, 'executed_sales': False}
        upstream = 'blizzard_auction_snapshot'
    else:
        market_scope = scope(region, 'reference', realm_slug=realm_slug)
        if dataset == 'realm':
            row = _realm_record(data, region)
            if row['slug'] != realm_slug or (expected_realm_id is not None and row['realm_id'] != expected_realm_id):
                raise MarketError('blizzard_realm_identity_mismatch')
            match = _href(data.get('connected_realm'), region, r'/data/wow/connected-realm/([0-9]{1,19})')
            row['connected_realm_id'] = integer(int(match.group(1)), zero=False)
            records = [row]
        else:
            if dataset == 'connected_realm' and integer(data.get('id'), zero=False) != connected_realm_id:
                raise MarketError('blizzard_connected_realm_identity_mismatch')
            for realm in _list(data.get('realms'), maximum=100_000):
                row = _realm_record(realm, region)
                if dataset == 'connected_realm':
                    row['connected_realm_id'] = connected_realm_id
                records.append(row)
            if len({row['realm_id'] for row in records}) != len(records) or len({row['slug'] for row in records}) != len(records):
                raise MarketError('duplicate_blizzard_realm')
            if dataset == 'connected_realm' and (not records or expected_realm_id is None):
                raise MarketError('selected_realm_required_for_connected_group')
            if dataset == 'connected_realm' and expected_realm_id not in {row['realm_id'] for row in records}:
                raise MarketError('blizzard_connected_group_missing_selected_realm')
        capabilities = {'realm_identity': True, 'connected_realm_mapping': dataset != 'realm_index'}
        upstream = 'blizzard_realm_reference'
    return batch('blizzard', dataset, market_scope, records, source_snapshot_at=snapshot,
                 capabilities=capabilities, warnings=warnings, upstream=upstream,
                 clock_semantics='http_last_modified_document_time_not_publication')
