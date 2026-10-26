"""Synthetic Blizzard contracts; no credentials, network, or live-schema claims."""

from copy import deepcopy
import json
import unittest

from wow_auction.market_blizzard import build_url, normalize_blizzard
from wow_auction.market_common import MarketError


def raw(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


def href(path, region='eu'):
    return {'href': f'https://{region}.api.blizzard.com{path}?namespace=dynamic-{region}'}


def commodity(aid=1, price=70, quantity=3, item=None):
    return {'id': aid, 'item': item or {'id': 123}, 'quantity': quantity,
            'unit_price': price, 'time_left': 'LONG'}


def auction(aid=2, **kwargs):
    return {'id': aid, 'item': {'id': 456}, 'quantity': 3, 'buyout': 100,
            'bid': 90, 'time_left': 'SHORT', **kwargs}


def realm(rid=10, slug='synthetic-realm'):
    return {'id': rid, 'slug': slug, 'name': {'en_GB': 'Synthetic Realm', 'ru_RU': 'Тестовый мир'},
            'key': href('/data/wow/realm/' + slug)}


class BlizzardMarketTests(unittest.TestCase):
    def normalize(self, data, dataset='commodities', **kwargs):
        if dataset in {'auctions', 'connected_realm'}:
            kwargs.setdefault('connected_realm_id', 555)
        return normalize_blizzard(raw(data), region='EU', dataset=dataset, **kwargs)

    def test_urls_are_fixed_https_host_namespace_and_regional_dataset(self):
        self.assertEqual(build_url(region='eu', dataset='commodities'),
                         'https://eu.api.blizzard.com/data/wow/auctions/commodities?namespace=dynamic-eu&locale=ru_RU')
        self.assertIn('/connected-realm/555/auctions?', build_url(region='EU', dataset='auctions', connected_realm_id=555))
        self.assertIn('/realm/synthetic-realm?', build_url(region='EU', dataset='realm', realm_slug='synthetic-realm'))
        for region, locale in [('US', 'en_US'), ('KR', 'ko_KR'), ('TW', 'zh_TW')]:
            self.assertIn('dynamic-' + region.lower(), build_url(region=region, dataset='realm_index', locale=locale))

    def test_invalid_url_inputs_cannot_change_host_or_scope(self):
        cases = [dict(region='CN', dataset='commodities'), dict(region='EU', dataset='unsupported'),
                 dict(region='US', dataset='commodities'),
                 dict(region='EU', dataset='commodities', connected_realm_id=555),
                 dict(region='EU', dataset='auctions', connected_realm_id=True),
                 dict(region='EU', dataset='realm', realm_slug='../secret'),
                 dict(region='EU', dataset='realm_index', realm_slug='unexpected'),
                 dict(region=[], dataset='commodities'), dict(region='EU', dataset=[]),
                 dict(region='EU', dataset='commodities', locale={})]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(MarketError):
                build_url(**kwargs)

    def test_commodity_unit_price_is_not_stack_total_or_execution(self):
        result = self.normalize({'auctions': [commodity()]}, last_modified='Mon, 26 Oct 2026 08:00:00 GMT')
        row = result['records'][0]
        self.assertEqual((row['quantity'], row['unit_price_copper'], row['buyout_total_copper']), (3, 70, None))
        self.assertTrue(row['quote_available'])
        self.assertEqual(result['scope']['kind'], 'commodity')
        self.assertIsNone(result['scope']['connected_realm_id'])
        self.assertFalse(result['capabilities']['executed_sales'])
        self.assertFalse(result['capabilities']['history'])
        self.assertEqual(result['source_snapshot_at'], 1793001600)
        self.assertIsNone(result['public_available_upper_bound'])

    def test_lot_buyout_and_bid_do_not_become_rounded_unit_price(self):
        result = self.normalize({'auctions': [auction()], 'connected_realm': href('/data/wow/connected-realm/555')}, 'auctions')
        row = result['records'][0]
        self.assertEqual((row['quantity'], row['buyout_total_copper'], row['bid_total_copper']), (3, 100, 90))
        self.assertIsNone(row['unit_price_copper'])
        self.assertEqual(result['scope']['connected_realm_id'], 555)
        self.assertTrue(row['quote_available'])

    def test_zero_and_absent_buyout_are_not_free_quotes(self):
        bid_only = auction(aid=3)
        del bid_only['buyout']
        result = self.normalize({'auctions': [auction(buyout=0), bid_only]}, 'auctions')
        self.assertEqual([r['quote_available'] for r in result['records']], [False, False])
        self.assertEqual([r['buyout_total_copper'] for r in result['records']], [0, None])
        zero = self.normalize({'auctions': [commodity(price=0)]})
        self.assertIsNone(zero['records'][0]['unit_price_copper'])
        self.assertFalse(zero['records'][0]['quote_available'])
        self.assertEqual(zero['records'][0]['reported_unit_price_copper'], 0)

    def test_variants_pet_fields_and_unknown_item_fields_are_preserved(self):
        item = {'id': 82800, 'bonus_lists': [123], 'modifiers': [{'type': 9, 'value': 80}],
                'context': 11, 'pet_species_id': 21, 'pet_level': 25, 'pet_quality_id': 3,
                'pet_breed_id': 4, 'future_field': {'flag': True, 'values': [1, '2', None]}}
        other = deepcopy(item)
        other['pet_breed_id'] = 5
        result = self.normalize({'auctions': [auction(item=item), auction(aid=3, item=other)]}, 'auctions')
        self.assertEqual(result['records'][0]['variant'], item)
        self.assertNotEqual(result['records'][0]['variant_key'], result['records'][1]['variant_key'])

    def test_empty_complete_book_is_distinct_from_missing_or_partial(self):
        result = self.normalize({'auctions': []})
        self.assertEqual(result['records'], [])
        self.assertEqual(result['completeness'], 'complete')
        for data in ({}, {'auctions': None}, {'auctions': [], 'truncated': True},
                     {'auctions': [], 'complete': False}, {'auctions': [], '_links': {'next': href('/next')}}):
            with self.subTest(data=data), self.assertRaises(MarketError):
                self.normalize(data)

    def test_missing_or_bad_http_clock_stays_unknown(self):
        for value in (None, '', 'secret-invalid-header', 'Mon, 26 Oct 2026 08:00:00'):
            with self.subTest(value=value):
                result = self.normalize({'auctions': []}, last_modified=value)
                self.assertIsNone(result['source_snapshot_at'])
                self.assertTrue(result['warnings'])
                self.assertNotIn('secret-invalid-header', str(result))

    def test_invalid_json_and_numerical_values_fail_with_domain_error(self):
        for payload in (b'<html>upstream error</html>', b'[]', b'{"auctions":[],"auctions":[]}',
                        b'{"auctions":[],"x":NaN}', b'{"x":' + b'9' * 5000 + b'}'):
            with self.subTest(payload=payload[:40]), self.assertRaises(MarketError):
                normalize_blizzard(payload, region='EU', dataset='commodities')
        for field, value in [('id', True), ('quantity', 0), ('quantity', 1.0),
                             ('unit_price', -1), ('unit_price', '70'), ('id', 2**63)]:
            record = commodity()
            record[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(MarketError):
                self.normalize({'auctions': [record]})

    def test_mixed_price_schemas_and_duplicate_ids_are_rejected(self):
        for data, dataset in [({'auctions': [commodity(), commodity()]}, 'commodities'),
                              ({'auctions': [commodity() | {'buyout': 100}]}, 'commodities'),
                              ({'auctions': [auction() | {'unit_price': 100}]}, 'auctions'),
                              ({'auctions': [commodity()], 'connected_realm': href('/data/wow/connected-realm/555')}, 'commodities')]:
            with self.subTest(dataset=dataset), self.assertRaises(MarketError):
                self.normalize(data, dataset)

    def test_reference_links_cannot_cross_region_host_namespace_or_id(self):
        for bad in ['https://evil.invalid/data/wow/connected-realm/555?namespace=dynamic-eu',
                    'https://us.api.blizzard.com/data/wow/connected-realm/555?namespace=dynamic-us',
                    'https://eu.api.blizzard.com/data/wow/connected-realm/555?namespace=dynamic-us',
                    'https://eu.api.blizzard.com/data/wow/connected-realm/556?namespace=dynamic-eu',
                    'https://eu.api.blizzard.com/data/wow/connected-realm/555?namespace=dynamic-eu&access_token=secret']:
            with self.subTest(bad=bad), self.assertRaises(MarketError) as cm:
                self.normalize({'auctions': [], 'connected_realm': {'href': bad}}, 'auctions')
            self.assertNotIn('secret', str(cm.exception))

    def test_realm_and_connected_group_resolve_ids_without_hardcoding(self):
        data = realm() | {'connected_realm': href('/data/wow/connected-realm/555')}
        record = self.normalize(data, 'realm', realm_slug='synthetic-realm')['records'][0]
        self.assertEqual((record['realm_id'], record['connected_realm_id']), (10, 555))
        group = self.normalize({'id': 555, 'realms': [realm(), realm(11, 'second-realm')]},
                               'connected_realm', expected_realm_id=10)
        self.assertEqual([r['connected_realm_id'] for r in group['records']], [555, 555])
        self.assertEqual(group['scope']['kind'], 'reference')

    def test_group_membership_identity_and_duplicate_realms_are_checked(self):
        cases = [({'id': 556, 'realms': [realm()]}, 10),
                 ({'id': 555, 'realms': [realm()]}, 11),
                 ({'id': 555, 'realms': []}, 10),
                 ({'id': 555, 'realms': [realm(), realm()]}, 10),
                 ({'id': 555, 'realms': [realm()]}, None)]
        for data, expected in cases:
            with self.subTest(data=data, expected=expected), self.assertRaises(MarketError):
                self.normalize(data, 'connected_realm', expected_realm_id=expected)
        data = realm() | {'connected_realm': href('/data/wow/connected-realm/555')}
        with self.assertRaises(MarketError):
            self.normalize(data, 'realm', realm_slug='other-realm')

    def test_index_keeps_unresolved_group_and_localized_names(self):
        result = self.normalize({'realms': [realm()]}, 'realm_index')
        self.assertIsNone(result['records'][0]['connected_realm_id'])
        self.assertEqual(result['records'][0]['name']['ru_RU'], 'Тестовый мир')
        self.assertFalse(result['capabilities']['connected_realm_mapping'])

    def test_variant_bounds_and_fractional_schema_changes_are_safe(self):
        deep = {'id': 123, 'unknown': 1.5}
        with self.assertRaises(MarketError):
            self.normalize({'auctions': [commodity(item=deep)]})
        deep = {'id': 123}
        node = deep
        for _ in range(18):
            node['next'] = {}
            node = node['next']
        with self.assertRaises(MarketError):
            self.normalize({'auctions': [commodity(item=deep)]})


if __name__ == '__main__':
    unittest.main()
