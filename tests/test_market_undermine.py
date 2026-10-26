import copy
import json
import unittest

from wow_auction.market_common import MarketError
from wow_auction.market_undermine import build_url, normalize_undermine, request_cost


SNAPSHOT = '2026-10-26T10:00:00Z'
GENERATED = '2026-10-26T10:05:00Z'


def raw(request, result, **extra):
    return json.dumps({'request': request, 'result': result, **extra}).encode()


def summary_data(collection='commodities', *, full=False, realm=False):
    request = {'region': 'eu', 'list': collection, 'detail': 'full' if full else 'base'}
    if realm:
        request['realms'] = ['realm-one', 'realm-two']
    return request, {'lastUpdated': GENERATED, 'snapshot': SNAPSHOT, collection: {
        '123': {'item': 123, 'price': 200, 'quantity': 7},
        '124': {'item': 124, 'price': 500, 'quantity': 0, 'seen': '2026-10-20T10:00:00Z'},
    }}


class UndermineContractTests(unittest.TestCase):
    def test_urls_and_gzip_points_are_fixed_validated_endpoints(self):
        self.assertEqual(build_url(region='EU', dataset='realms'),
                         'https://api.undermine.exchange/v1/static/realms.json')
        self.assertEqual(build_url(region='EU', dataset='commodity_summary'),
                         'https://api.undermine.exchange/v1/region/eu/commodities.json')
        self.assertEqual(build_url(region='EU', dataset='realm_item_now', realm_slug='realm-one', item_id=123,
                                   detail={'item_level': 44, 'item_suffix': 13150}),
                         'https://api.undermine.exchange/v1/realm/eu/realm-one/items/123/44/13150/now.json')
        self.assertEqual(build_url(region='EU', dataset='realm_pet_daily', realm_slug='realm-one',
                                   pet_species_id=39, detail={'pet_breed': 6}),
                         'https://api.undermine.exchange/v1/realm/eu/realm-one/pets/39/6/daily.json')
        self.assertEqual(build_url(region='EU', dataset='region_items', detail='full'),
                         'https://api.undermine.exchange/v1/region/eu/items.json?detail=full')
        self.assertEqual(request_cost('realms'), 1)
        self.assertEqual(request_cost('realm_items'), 3)
        self.assertEqual(request_cost('realm_pets'), 1)
        self.assertEqual(request_cost('commodity_now'), 1)
        self.assertEqual(request_cost('region_item_now'), 3)
        self.assertEqual(request_cost('region_item_daily'), 5)
        self.assertEqual(request_cost('realm_item_daily'), 3)
        self.assertEqual(request_cost('realm_stats'), 1)

    def test_bad_parameters_do_not_form_arbitrary_urls(self):
        examples = [dict(region='CN', dataset='commodity_summary'),
                    dict(region='EU', dataset='realm_items', realm_slug='../private'),
                    dict(region='EU', dataset='realm_items'),
                    dict(region='EU', dataset='commodity_now', item_id=True),
                    dict(region='EU', dataset='commodity_now', item_id=123, connected_realm_id=10),
                    dict(region='EU', dataset='realm_item_now', item_id=123, realm_slug='r', detail={'item_suffix': 1}),
                    dict(region='EU', dataset='commodity_now', item_id=123, detail={'item_level': 1, 'item_suffix': 1}),
                    dict(region='EU', dataset='region_item_hourly', item_id=123),
                    dict(region='EU', dataset='realms', detail='full')]
        for params in examples:
            with self.subTest(params=params), self.assertRaises(MarketError):
                build_url(**params)

    def test_static_realms_do_not_invent_numeric_ids(self):
        doc = raw({'list': 'realms'}, {'lastUpdated': GENERATED, 'realms': [
            {'region': 'eu', 'slug': 'realm-one', 'name': 'Realm One', 'nativeName': 'Мир'},
            {'region': 'us', 'slug': 'realm-one', 'name': 'Realm One'}]})
        b = normalize_undermine(doc, region='EU', dataset='realms')
        self.assertEqual(b['scope']['kind'], 'reference')
        self.assertEqual(len(b['records']), 2)
        self.assertTrue(all(r['realm_id'] is None and r['connected_realm_id'] is None for r in b['records']))
        self.assertEqual(b['records'][0]['native_name'], 'Мир')
        self.assertIsNone(b['source_snapshot_at'])

    def test_summary_separates_source_clocks_and_zero_quantity(self):
        req, result = summary_data()
        b = normalize_undermine(raw(req, result), region='EU', dataset='commodity_summary')
        first, zero = b['records']
        self.assertEqual(first['unit_price_copper'], 200)
        self.assertEqual(first['quantity'], 7)
        self.assertTrue(first['quote_available'])
        self.assertEqual(zero['reported_price_copper'], 500)
        self.assertIsNone(zero['unit_price_copper'])
        self.assertFalse(zero['quote_available'])
        self.assertEqual(b['source_generated_at'] - b['source_snapshot_at'], 300)
        self.assertEqual(b['scope']['kind'], 'commodity')
        self.assertEqual(b['upstream'], 'blizzard_auction_snapshot')
        self.assertIsNone(b['public_available_upper_bound'])

    def test_realm_scope_uses_provider_membership_without_numeric_id(self):
        req, result = summary_data('items', realm=True)
        b = normalize_undermine(raw(req, result), region='EU', dataset='realm_items', realm_slug='realm-one')
        self.assertEqual(b['scope']['kind'], 'provider_connected_realm')
        self.assertIsNone(b['scope']['connected_realm_id'])
        verified = normalize_undermine(raw(req, result), region='EU', dataset='realm_items',
                                      realm_slug='realm-one', connected_realm_id=1234)
        self.assertEqual(verified['scope'], b['scope'])
        self.assertIsNone(verified['scope']['connected_realm_id'])
        self.assertEqual(b['scope']['realm_slugs'], ['realm-one', 'realm-two'])
        self.assertIsNone(b['scope']['membership_effective_at'])
        self.assertEqual(verified['records'][0]['connected_realm_slugs'], ['realm-one', 'realm-two'])

    def test_request_echo_mismatch_fails_not_silent_relabel(self):
        req, result = summary_data('items', realm=True)
        for change in ({'region': 'us'}, {'realms': ['another-realm']}, {'list': 'pets'}, {'detail': 'full'}):
            with self.subTest(change=change), self.assertRaises(MarketError):
                normalize_undermine(raw({**req, **change}, result), region='EU', dataset='realm_items', realm_slug='realm-one')

    def test_now_depth_has_separate_aggregate_and_price_levels(self):
        request = {'region': 'eu', 'itemId': 123, 'period': 'now'}
        result = {'lastUpdated': GENERATED, 'lastSeen': SNAPSHOT, 'price': 100, 'quantity': 5,
                  'auctions': [{'price': 100, 'quantity': 2}, {'price': 200, 'quantity': 3}]}
        b = normalize_undermine(raw(request, result), region='EU', dataset='commodity_now', item_id=123)
        self.assertEqual([r['record_type'] for r in b['records']], ['aggregate', 'price_level', 'price_level'])
        self.assertEqual(b['records'][0]['overlapping_aggregate_level'], 'summary_of_depth')
        self.assertEqual(sum(r['quantity'] for r in b['records'] if r['record_type'] == 'price_level'), 5)
        self.assertTrue(b['capabilities']['depth'])
        self.assertFalse(b['capabilities']['confirmed_sales'])
        with self.assertRaisesRegex(MarketError, 'depth_quantity_mismatch'):
            normalize_undermine(raw(request, {**result, 'quantity': 6}), region='EU', dataset='commodity_now', item_id=123)

    def test_zero_now_last_seen_is_not_current_snapshot(self):
        request = {'region': 'eu', 'itemId': 123, 'period': 'now'}
        result = {'lastUpdated': GENERATED, 'lastSeen': '2026-10-20T10:00:00Z', 'price': 100, 'quantity': 0, 'auctions': []}
        b = normalize_undermine(raw(request, result), region='EU', dataset='commodity_now', item_id=123)
        self.assertIsNone(b['source_snapshot_at'])
        self.assertIsNone(b['records'][0]['source_snapshot_at'])
        self.assertIsNotNone(b['records'][0]['last_seen_at'])
        self.assertFalse(b['records'][0]['quote_available'])

    def test_paid_individual_variants_preserve_modifiers_and_never_fabricate_auction_id(self):
        request = {'region': 'eu', 'realms': ['realm-one'], 'itemId': 123, 'itemLevel': 44, 'period': 'now'}
        result = {'lastUpdated': GENERATED, 'lastSeen': SNAPSHOT, 'price': 100, 'quantity': 2,
                  'auctions': [{'price': 100, 'bonuses': [41, 1527], 'modifiers': {'28': 2462}, 'stats': ['leech']},
                               {'price': 100, 'bonuses': [42, 1527], 'sockets': [7]}]}
        params = dict(region='EU', dataset='realm_item_now', realm_slug='realm-one', item_id=123, detail={'item_level': 44})
        b = normalize_undermine(raw(request, result), **params)
        offers = b['records'][1:]
        self.assertTrue(b['capabilities']['requires_paid'])
        self.assertEqual([r['quantity'] for r in offers], [1, 1])
        self.assertNotEqual(offers[0]['variant'], offers[1]['variant'])
        self.assertEqual(offers[0]['variant']['modifiers'], {'28': 2462})
        self.assertTrue(all(r['auction_id'] is None for r in offers))
        self.assertNotIn('quantity', offers[0]['raw_fields'])
        self.assertEqual(offers[0]['quantity_basis'], 'documented_individual_item')
        invalid = copy.deepcopy(result)
        invalid['auctions'][0]['quantity'] = True
        with self.assertRaises(MarketError):
            normalize_undermine(raw(request, invalid), **params)

    def test_full_summary_marks_overlapping_parent_and_variants(self):
        req = {'region': 'eu', 'realms': ['realm-one'], 'list': 'items', 'detail': 'full'}
        result = {'lastUpdated': GENERATED, 'snapshot': SNAPSHOT, 'items': {
            '123': {'item': 123, 'price': 100, 'quantity': 3, 'level': {
                '44': {'level': 44, 'suffix': {'10': {'suffix': 10, 'price': 100, 'quantity': 3}}}}}}}
        b = normalize_undermine(raw(req, result), region='EU', dataset='realm_items', realm_slug='realm-one', detail='full')
        self.assertEqual(len(b['records']), 2)
        self.assertEqual(b['records'][1]['variant'], {'item_level': 44, 'item_suffix': 10})
        self.assertIn('summary_variant_levels_overlap_do_not_sum', b['warnings'])
        self.assertTrue(b['capabilities']['requires_paid'])

    def test_hourly_is_history_with_carried_zero_price_not_turnover(self):
        req = {'region': 'eu', 'itemId': 123, 'period': 'hourly'}
        result = {'lastUpdated': GENERATED, 'hourly': [
            {'snapshot': '2026-10-25T10:04:05Z', 'price': 100, 'quantity': 5},
            {'snapshot': '2026-10-25T11:08:00Z', 'price': 100, 'quantity': 0}]}
        b = normalize_undermine(raw(req, result), region='EU', dataset='commodity_hourly', item_id=123)
        self.assertEqual(b['capabilities']['history'], 'hourly')
        self.assertIsNone(b['source_snapshot_at'])
        self.assertTrue(all(not r['quote_available'] for r in b['records']))
        self.assertIsNone(b['records'][1]['unit_price_copper'])
        self.assertEqual(b['records'][1]['reported_price_copper'], 100)
        self.assertTrue(all(r['public_available_upper_bound'] is None for r in b['records']))

    def test_daily_does_not_invent_timezone_period_end_or_publication(self):
        req = {'region': 'eu', 'itemId': 123, 'period': 'daily'}
        result = {'lastUpdated': GENERATED, 'daily': [{'day': '2026-10-25', 'price': 100, 'quantity': 15}]}
        b = normalize_undermine(raw(req, result), region='EU', dataset='commodity_daily', item_id=123)
        row = b['records'][0]
        self.assertEqual(row['period_day'], '2026-10-25')
        for field in ('source_snapshot_at', 'period_start', 'period_end_exclusive', 'period_timezone', 'is_final', 'public_available_upper_bound'):
            self.assertIsNone(row[field])
        self.assertFalse(row['reconstructed_historical_eligible'])
        self.assertEqual(row['aggregation_rule'], 'daily_max_quantity_and_price_at_that_snapshot')
        regional = normalize_undermine(raw(req, result), region='EU', dataset='region_item_daily', item_id=123)
        self.assertEqual(regional['records'][0]['aggregation_rule'], 'sum_of_realm_daily_maxima_mean_corresponding_prices')
        self.assertEqual(regional['records'][0]['price_semantics'], 'mean_realm_daily_selected_asking_prices')

    def test_regional_summary_realm_count_is_not_quantity(self):
        req = {'region': 'eu', 'list': 'items', 'detail': 'base'}
        result = {'lastUpdated': GENERATED, 'items': {'123': {'item': 123, 'median': 500, 'min': 100, 'realms': 12}}}
        b = normalize_undermine(raw(req, result), region='EU', dataset='region_items')
        row = b['records'][0]
        self.assertEqual(row['available_connected_realms'], 12)
        self.assertIsNone(row['quantity'])
        self.assertIsNone(row['unit_price_copper'])
        self.assertFalse(row['quote_available'])

    def test_region_now_keeps_distinct_unresolved_connected_groups_and_times(self):
        req = {'region': 'eu', 'itemId': 123, 'period': 'now'}
        result = [{'lastUpdated': GENERATED, 'snapshot': SNAPSHOT, 'price': 100, 'quantity': 2, 'realms': ['realm-one', 'realm-two']},
                  {'lastUpdated': GENERATED, 'snapshot': '2026-10-26T10:01:00Z', 'price': 200, 'quantity': 3, 'realms': ['realm-three']}]
        b = normalize_undermine(raw(req, result), region='EU', dataset='region_item_now', item_id=123)
        self.assertEqual(b['scope']['kind'], 'region_aggregate')
        self.assertEqual(len(b['records']), 2)
        self.assertNotEqual(b['records'][0]['market_scope'], b['records'][1]['market_scope'])
        self.assertNotEqual(b['records'][0]['source_snapshot_at'], b['records'][1]['source_snapshot_at'])
        with self.assertRaisesRegex(MarketError, 'duplicate_connected_group'):
            normalize_undermine(raw(req, result + [result[0]]), region='EU', dataset='region_item_now', item_id=123)

    def test_pet_identity_stays_separate_and_breed_preserved(self):
        req = {'region': 'eu', 'realms': ['realm-one'], 'petSpecies': 123, 'petBreed': 6, 'period': 'now'}
        result = {'lastUpdated': GENERATED, 'lastSeen': SNAPSHOT, 'price': 100, 'quantity': 1,
                  'auctions': [{'price': 100, 'modifiers': {'2': 2, '3': 123, '4': 6}}]}
        b = normalize_undermine(raw(req, result), region='EU', dataset='realm_pet_now', realm_slug='realm-one',
                               pet_species_id=123, detail={'pet_breed': 6})
        offer = b['records'][1]
        self.assertIsNone(offer['item_id'])
        self.assertEqual(offer['pet_species_id'], 123)
        self.assertEqual(offer['variant']['pet_breed'], 6)

    def test_bad_partial_empty_html_and_duplicate_inputs_do_not_become_full_books(self):
        req, result = summary_data()
        for payload in (b'<html>maintenance</html>', b'{}', b'{"error":"invalid auth"}',
                        raw(req, result, partial=True), raw(req, {**result, 'complete': False}),
                        b'{"request":{},"request":{},"result":{}}'):
            with self.subTest(payload=payload[:40]), self.assertRaises(MarketError):
                normalize_undermine(payload, region='EU', dataset='commodity_summary')
        empty = normalize_undermine(raw(req, {**result, 'commodities': {}}), region='EU', dataset='commodity_summary')
        self.assertEqual(empty['completeness'], 'unknown')
        self.assertIn('empty_collection_unverified', empty['warnings'])

    def test_identity_and_numeric_types_are_validated_before_publication(self):
        req, result = summary_data()
        for field, value in [('quantity', True), ('quantity', -1), ('price', 1.5), ('item', 999), ('price', 2**63)]:
            invalid = copy.deepcopy(result)
            invalid['commodities']['123'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(MarketError):
                normalize_undermine(raw(req, invalid), region='EU', dataset='commodity_summary')
        bad_key = {**result, 'commodities': {'9' * 5000: {'item': 1, 'price': 1, 'quantity': 1}}}
        with self.assertRaises(MarketError):
            normalize_undermine(raw(req, bad_key), region='EU', dataset='commodity_summary')

    def test_static_suffixes_and_full_pet_breed_keep_identity(self):
        reference = raw({'list': 'suffixes'}, {'lastUpdated': GENERATED, 'suffixes': [
            {'id': 10, 'name': {'enus': 'of the Example', 'ruru': 'примера'}}]})
        suffix = normalize_undermine(reference, region='EU', dataset='suffixes')['records'][0]
        self.assertEqual(suffix['names']['ruru'], 'примера')
        req = {'region': 'eu', 'list': 'pets', 'detail': 'full', 'realms': ['realm-one']}
        result = {'lastUpdated': GENERATED, 'snapshot': SNAPSHOT, 'pets': {
            '39': {'species': 39, 'price': 100, 'quantity': 2,
                   'breed': {'6': {'breed': 6, 'price': 100, 'quantity': 2}}}}}
        b = normalize_undermine(raw(req, result), region='EU', dataset='realm_pets', realm_slug='realm-one', detail='full')
        self.assertEqual(b['records'][1]['pet_species_id'], 39)
        self.assertEqual(b['records'][1]['variant'], {'pet_breed': 6})
        self.assertIsNone(b['records'][1]['item_id'])

    def test_regional_full_summary_old_variants_without_min_or_realm_count(self):
        req = {'region': 'eu', 'list': 'items', 'detail': 'full'}
        result = {'lastUpdated': GENERATED, 'items': {
            '123': {'item': 123, 'median': 500, 'min': 100, 'realms': 12,
                    'level': {'44': {'level': 44, 'median': 600,
                                    'suffix': {'10': {'suffix': 10, 'median': 700}}}}}}}
        b = normalize_undermine(raw(req, result), region='EU', dataset='region_items', detail='full')
        self.assertEqual(len(b['records']), 3)
        self.assertIsNone(b['records'][1]['region_min_copper'])
        self.assertIsNone(b['records'][2]['available_connected_realms'])
        self.assertEqual(b['records'][2]['variant'], {'item_level': 44, 'item_suffix': 10})
        self.assertTrue(all(r['quantity'] is None for r in b['records']))
        # A suffix leaf without its price must fail, not disappear from a complete batch.
        del result['items']['123']['level']['44']['suffix']['10']['median']
        with self.assertRaises(MarketError):
            normalize_undermine(raw(req, result), region='EU', dataset='region_items', detail='full')

    def test_tertiary_stats_are_paid_variant_presence_not_price_or_quantity(self):
        req = {'region': 'eu', 'list': 'stats', 'realms': ['realm-one']}
        result = {'lastUpdated': GENERATED, 'snapshot': SNAPSHOT, 'stats': {
            'speed': [{'item': 123, 'level': 44, 'suffix': 10}], 'leech': [{'item': 123, 'level': 44}]}}
        params = dict(region='EU', dataset='realm_stats', realm_slug='realm-one')
        self.assertEqual(build_url(**params), 'https://api.undermine.exchange/v1/realm/eu/realm-one/stats.json')
        b = normalize_undermine(raw(req, result), **params)
        self.assertTrue(b['capabilities']['requires_paid'])
        self.assertTrue(b['capabilities']['variant_presence_index'])
        self.assertEqual(b['records'][0]['variant'], {'item_level': 44, 'item_suffix': 10, 'stats': ['speed']})
        self.assertTrue(all(r['quantity'] is None and not r['quote_available'] for r in b['records']))
        result['stats']['speed'].append(result['stats']['speed'][0])
        with self.assertRaisesRegex(MarketError, 'duplicate_stat_variant'):
            normalize_undermine(raw(req, result), **params)

    def test_unknown_fields_remain_serializable_and_nesting_is_bounded(self):
        req, result = summary_data()
        result['commodities']['123']['future'] = {'ratio': 1.25, 'flags': [True, None]}
        b = normalize_undermine(raw(req, result), region='EU', dataset='commodity_summary')
        self.assertEqual(b['records'][0]['raw_fields']['future']['ratio'], '1.25')
        json.dumps(b, allow_nan=False)
        nested = []
        for _ in range(70):
            nested = [nested]
        result['commodities']['123']['future'] = nested
        with self.assertRaisesRegex(MarketError, 'raw_nesting_limit'):
            normalize_undermine(raw(req, result), region='EU', dataset='commodity_summary')

    def test_duplicate_history_and_invalid_day_are_not_silently_accepted(self):
        for period, point in [('hourly', {'snapshot': SNAPSHOT, 'price': 100, 'quantity': 1}),
                              ('daily', {'day': '2026-10-25', 'price': 100, 'quantity': 1})]:
            request = {'region': 'eu', 'itemId': 123, 'period': period}
            with self.subTest(period=period), self.assertRaisesRegex(MarketError, 'duplicate_history_point'):
                normalize_undermine(raw(request, {'lastUpdated': GENERATED, period: [point, point]}),
                                    region='EU', dataset='commodity_' + period, item_id=123)
        with self.assertRaisesRegex(MarketError, 'invalid_day'):
            normalize_undermine(raw({'region': 'eu', 'itemId': 123, 'period': 'daily'},
                                    {'lastUpdated': GENERATED, 'daily': [{'day': '2026-02-30', 'price': 1, 'quantity': 1}]}),
                                region='EU', dataset='commodity_daily', item_id=123)

    def test_variant_echo_and_book_minimum_mismatch_fail(self):
        request = {'region': 'eu', 'realms': ['realm-one'], 'itemId': 123, 'itemLevel': 44, 'period': 'now'}
        result = {'lastUpdated': GENERATED, 'lastSeen': SNAPSHOT, 'price': 100, 'quantity': 1,
                  'auctions': [{'price': 100}]}
        params = dict(region='EU', dataset='realm_item_now', realm_slug='realm-one', item_id=123, detail={'item_level': 44})
        for echo in ({**request, 'itemLevel': 45}, {k: v for k, v in request.items() if k != 'itemLevel'},
                     {**request, 'itemSuffix': 10}):
            with self.subTest(echo=echo), self.assertRaisesRegex(MarketError, 'variant_mismatch'):
                normalize_undermine(raw(echo, result), **params)
        with self.assertRaisesRegex(MarketError, 'depth_minimum_price_mismatch'):
            normalize_undermine(raw(request, {**result, 'price': 50}), **params)


if __name__ == '__main__':
    unittest.main()
