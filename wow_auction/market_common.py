"""Bounded, source-neutral market contracts. No network or credentials here."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re


class MarketError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def integer(value, *, zero=True):
    if type(value) is not int or value < (0 if zero else 1) or value > 2**63 - 1:
        raise MarketError('invalid_market_integer')
    return value


def decimal_string(value):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
        raise MarketError('invalid_market_decimal')
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise MarketError('invalid_market_decimal') from None
    if not number.is_finite() or number < 0:
        raise MarketError('invalid_market_decimal')
    return str(number)


def utc_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise MarketError('invalid_market_timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError
        return integer(int(parsed.timestamp()))
    except (ValueError, OverflowError, OSError):
        raise MarketError('invalid_market_timestamp') from None


def strict_json(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > 256 * 1024 * 1024:
        raise MarketError('invalid_market_payload_size')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MarketError('duplicate_market_json_key')
            result[key] = value
        return result
    def bad_constant(_):
        raise MarketError('nonfinite_market_number')
    def bounded_int(value):
        if len(value) > 20:
            raise MarketError('market_integer_limit')
        return int(value)
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                           parse_int=bounded_int, parse_float=Decimal, parse_constant=bad_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, MarketError):
            raise
        raise MarketError('invalid_market_json') from None
    if not isinstance(value, dict):
        raise MarketError('market_json_object_required')
    return value


def scope(region, kind, *, connected_realm_id=None, realm_slug=None):
    region = region.upper() if isinstance(region, str) else region
    if not isinstance(region, str) or region not in {'EU', 'US', 'KR', 'TW'}:
        raise MarketError('unsupported_market_region')
    if not isinstance(kind, str) or kind not in {'commodity', 'connected_realm', 'region_aggregate', 'realm_unresolved', 'reference'}:
        raise MarketError('invalid_market_scope')
    if kind == 'connected_realm':
        integer(connected_realm_id, zero=False)
    elif connected_realm_id is not None:
        raise MarketError('unexpected_connected_realm_id')
    if realm_slug is not None and (not isinstance(realm_slug, str) or not re.fullmatch(r'[a-z0-9-]{1,80}', realm_slug)):
        raise MarketError('invalid_realm_slug')
    if kind == 'realm_unresolved' and realm_slug is None:
        raise MarketError('unresolved_realm_slug_required')
    return {'game': 'retail', 'region': region, 'kind': kind,
            'connected_realm_id': connected_realm_id, 'realm_slug': realm_slug}


def provider_group_scope(region, provider, realm_slugs):
    """A provider's membership assertion, never a fabricated Blizzard numeric ID."""
    base = scope(region, 'reference')
    if provider != 'undermine' or not isinstance(realm_slugs, list) or not 1 <= len(realm_slugs) <= 100:
        raise MarketError('invalid_provider_realm_group')
    for slug in realm_slugs:
        scope(region, 'realm_unresolved', realm_slug=slug)
    if len(set(realm_slugs)) != len(realm_slugs):
        raise MarketError('duplicate_provider_realm_member')
    members = sorted(realm_slugs)
    identity = {'provider': provider, 'game': 'retail', 'region': base['region'], 'members': members}
    return {**base, 'kind': 'provider_connected_realm', 'provider': provider,
            'provider_group_key': fingerprint(identity), 'realm_slugs': members,
            'membership_basis': 'provider_response', 'membership_effective_at': None}


def batch(source, dataset, market_scope, records, *, source_snapshot_at=None,
          source_generated_at=None, capabilities=None, completeness='complete',
          warnings=None, upstream='blizzard_auction_snapshot', clock_semantics=None):
    if not isinstance(source, str) or source not in {'tsm', 'blizzard', 'undermine'} or not isinstance(records, list):
        raise MarketError('invalid_market_batch')
    if len(records) > 2_000_000 or completeness not in {'complete', 'partial', 'unknown'}:
        raise MarketError('market_batch_limit_or_completeness')
    for value in (source_snapshot_at, source_generated_at):
        if value is not None:
            integer(value)
    return {'source': source, 'dataset': dataset, 'scope': market_scope, 'records': records,
            'source_snapshot_at': source_snapshot_at, 'source_generated_at': source_generated_at,
            'clock_semantics': clock_semantics, 'capabilities': capabilities or {},
            'completeness': completeness, 'warnings': warnings or [], 'upstream': upstream,
            'schema_version': 1, 'public_available_lower_bound': None,
            'public_available_upper_bound': None, 'publication_basis': 'unknown'}
