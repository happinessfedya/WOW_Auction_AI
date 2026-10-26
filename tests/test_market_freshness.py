"""S2-01: source clocks, rather than delivery success, gate current data."""
import copy
import json
import unittest

from wow_auction.market_common import batch, provider_group_scope, scope
from wow_auction.market_freshness import POLICY_VERSION, assess, compare, describe
from wow_auction.market_tsm import normalize_tsm
from wow_auction.market_undermine import normalize_undermine


NOW = 1_793_016_000  # 2026-10-26 12:00 UTC; exact reference is fixed for tests.
HOUR = 3600


def tsm(stamp=NOW-HOUR, dataset='commodities', **extra):
    return batch('tsm', dataset, scope('EU', 'commodity' if dataset == 'commodities' else 'region_aggregate'),
                 [{'record_type': 'aggregate', 'unit_price_copper': 123456789, 'name': 'PRIVATE_MARKET_NAME'}],
                 source_snapshot_at=stamp, **extra)


def ue_now(quantity, snapshot=NOW-HOUR, generated=NOW-10):
    return batch('undermine', 'commodity_now', scope('EU', 'commodity'),
                 [{'record_type': 'aggregate', 'quantity': quantity, 'last_seen_at': snapshot}],
                 source_snapshot_at=snapshot if quantity else None, source_generated_at=generated)


def regional_now(times):
    records = []
    for realm, stamp in times:
        records.append({'record_type': 'aggregate', 'quantity': 1,
                        'market_scope': provider_group_scope('EU', 'undermine', [realm]),
                        'source_snapshot_at': stamp, 'source_generated_at': stamp+10})
    return batch('undermine', 'region_item_now', scope('EU', 'region_aggregate'), records)


class SourceFreshnessTests(unittest.TestCase):
    def test_source_age_gates_200_or_304_independently_of_received_time(self):
        old = tsm(NOW - 365 * 24 * HOUR)
        old.update(received_at_ns=NOW * 10**9, available_at_ns=NOW * 10**9, http_status=200)
        first = assess(describe(old), NOW)
        old.update(received_at_ns=(NOW + HOUR) * 10**9, http_status=304)
        repeated = assess(describe(old), NOW+HOUR)
        self.assertEqual(first['state'], 'source_stale')
        self.assertEqual(repeated['state'], 'source_stale')
        self.assertFalse(first['current_usable'])
        self.assertFalse(repeated['current_usable'])
        self.assertEqual(repeated['source_age_seconds'] - first['source_age_seconds'], HOUR)

    def test_tsm_fast_and_daily_datasets_have_explicit_distinct_limits(self):
        for dataset in ('commodities', 'realm_items', 'realm_pets'):
            self.assertEqual(describe(tsm(dataset=dataset))['max_age_seconds'], 8 * HOUR)
        for dataset in ('region_items', 'region_pets'):
            assessment = assess(describe(tsm(NOW - 34*HOUR, dataset)), NOW)
            self.assertEqual(assessment['max_age_seconds'], 48 * HOUR)
            self.assertTrue(assessment['current_usable'])
            self.assertFalse(assess(describe(tsm(NOW-49*HOUR, dataset)), NOW)['current_usable'])

    def test_threshold_boundary_is_explicit_not_rounded_days(self):
        self.assertTrue(assess(describe(tsm(NOW-8*HOUR)), NOW)['current_usable'])
        self.assertFalse(assess(describe(tsm(NOW-8*HOUR-1)), NOW)['current_usable'])

    def test_unknown_or_invalid_source_time_never_becomes_received_time(self):
        for stamp in (None, True, -1, '1793016000'):
            document = tsm()
            document['source_snapshot_at'] = stamp
            assessment = assess(describe(document), NOW)
            self.assertEqual(assessment['state'], 'source_time_unknown')
            self.assertIsNone(assessment['source_age_seconds'])
            self.assertFalse(assessment['current_usable'])

    def test_future_source_or_generation_clock_not_current_eligible(self):
        self.assertEqual(assess(describe(tsm(NOW+1)), NOW)['state'], 'source_clock_ahead')
        self.assertEqual(assess(describe(ue_now(1, generated=NOW+1)), NOW)['state'], 'source_clock_ahead')
        self.assertEqual(assess(describe(ue_now(0, generated=NOW+1)), NOW)['state'], 'source_clock_ahead')
        for dataset in ('commodity_hourly', 'realms'):
            document = batch('undermine', dataset, scope('EU', 'reference'), [], source_generated_at=NOW+1)
            self.assertEqual(assess(describe(document), NOW)['state'], 'source_clock_ahead')

    def test_receive_time_clock_anomaly_requires_new_observation_not_passage_of_time(self):
        descriptor = describe(tsm(NOW+100))
        descriptor['source_clock_ahead_at_receive'] = True
        self.assertEqual(assess(descriptor, NOW+200)['state'], 'source_clock_ahead')
        self.assertFalse(assess(descriptor, NOW+200)['current_usable'])
        fresh_observation = describe(tsm(NOW+100))
        fresh_observation['source_clock_ahead_at_receive'] = False
        self.assertTrue(assess(fresh_observation, NOW+200)['current_usable'])

    def test_a_b_a_keeps_source_watermark_after_payload_cleanup(self):
        a, b = describe(tsm(NOW-HOUR)), describe(tsm(NOW-2*HOUR))
        watermark = json.loads(json.dumps(a))  # descriptor survives raw/normalized deletion
        self.assertEqual(compare(None, a), 'initial')
        self.assertEqual(compare(watermark, b), 'regression')
        self.assertEqual(compare(watermark, a), 'equal')
        self.assertEqual(compare(watermark, describe(tsm(NOW-10))), 'advance')

    def test_same_source_time_changed_price_or_parser_is_correction(self):
        original = tsm()
        changed = copy.deepcopy(original)
        changed['records'][0]['unit_price_copper'] = 888
        changed['normalizer_revision'] = 'another-parser'
        self.assertEqual(compare(describe(original), describe(changed)), 'equal')
        self.assertEqual(assess(describe(original), NOW), assess(describe(changed), NOW))

    def test_missing_clock_cannot_displace_known_clock(self):
        unknown, known = describe(tsm(None)), describe(tsm())
        self.assertEqual(compare(known, unknown), 'missing_clock')
        self.assertEqual(compare(unknown, known), 'advance')

    def test_scope_and_provider_changes_are_not_comparable(self):
        a = describe(tsm())
        for key, changed in [('scope_key', '0'*64), ('provider', 'undermine'), ('dataset', 'region_items')]:
            other = {**a, key: changed}
            self.assertEqual(compare(a, other), 'incomparable')

    def test_descriptors_contain_clocks_not_prices_or_item_names(self):
        metadata = json.dumps(describe(tsm()))
        self.assertNotIn('123456789', metadata)
        self.assertNotIn('PRIVATE_MARKET_NAME', metadata)
        self.assertNotIn('records', metadata)
        self.assertIn(POLICY_VERSION, metadata)

    def test_real_tsm_adapter_timestamp_not_metadata_fallback(self):
        body = (b'itemId,name,marketValue,minBuyout,recent,historical,updatedAt\n'
                b'123,Synthetic,100,80,85,90,2020-01-01T09:00:00Z\n')
        normalized = normalize_tsm(body, region='EU', dataset='commodities')
        self.assertEqual(assess(describe(normalized), NOW)['state'], 'source_stale')

    def test_ue_current_uses_upstream_snapshot_not_recent_generation(self):
        current = batch('undermine', 'commodity_summary', scope('EU', 'commodity'), [{'quantity': 1}],
                        source_snapshot_at=NOW-7*HOUR, source_generated_at=NOW-10)
        assessment = assess(describe(current), NOW)
        self.assertEqual(assessment['state'], 'source_stale')
        self.assertEqual(assessment['max_age_seconds'], 6*HOUR)

    def test_zero_quantity_old_last_seen_is_no_quote_not_stale_market_error(self):
        zero = ue_now(0, NOW-365*24*HOUR, NOW-365*24*HOUR+10)
        assessment = assess(describe(zero), NOW)
        self.assertEqual(assessment['state'], 'no_current_quote')
        self.assertFalse(assessment['current_usable'])
        self.assertIsNone(assessment['source_age_seconds'])
        self.assertGreater(assessment['source_generated_age_seconds'], 300*24*HOUR)

    def test_positive_zero_positive_transition_compares_generation_without_faking_scan(self):
        positive = describe(ue_now(1, NOW-100, NOW-90))
        zero = describe(ue_now(0, NOW-100, NOW-80))
        restored = describe(ue_now(1, NOW-70, NOW-60))
        self.assertEqual(compare(positive, zero), 'advance')
        self.assertEqual(compare(zero, restored), 'advance')
        self.assertEqual(compare(restored, zero), 'regression')
        self.assertEqual(zero['ordering_clocks']['scan'], NOW-100)
        self.assertEqual(zero['freshness_clocks'], {})

    def test_zero_old_last_seen_does_not_regress_zero_generation(self):
        positive = describe(ue_now(1, NOW-100, NOW-90))
        zero = describe(ue_now(0, NOW-24*HOUR, NOW-80))
        self.assertEqual(compare(positive, zero), 'advance')
        self.assertEqual(assess(zero, NOW)['state'], 'no_current_quote')

    def test_initial_zero_last_seen_is_ordering_evidence_for_later_positive_quote(self):
        zero = describe(ue_now(0, NOW-100, NOW-90))
        backward_positive = describe(ue_now(1, NOW-200, NOW-80))
        self.assertEqual(compare(zero, backward_positive), 'regression')

    def test_ue_generation_only_regional_aggregate_keeps_upstream_age_unknown(self):
        document = batch('undermine', 'region_items', scope('EU', 'region_aggregate'), [{}], source_generated_at=NOW-1)
        assessment = assess(describe(document), NOW)
        self.assertEqual(assessment['state'], 'source_time_unknown')
        self.assertFalse(assessment['current_usable'])

    def test_mixed_region_times_use_oldest_group_not_newest_or_download_time(self):
        mixed = regional_now([('one', NOW-HOUR), ('two', NOW-7*HOUR)])
        assessment = assess(describe(mixed), NOW)
        self.assertEqual(assessment['state'], 'source_stale')
        self.assertEqual(assessment['source_age_seconds'], 7*HOUR)

    def test_mixed_group_future_generation_cannot_poison_watermark(self):
        mixed = regional_now([('one', NOW-HOUR), ('two', NOW-HOUR)])
        mixed['records'][1]['source_generated_at'] = NOW+1
        self.assertEqual(assess(describe(mixed), NOW)['state'], 'source_clock_ahead')

    def test_mixed_group_regression_detected_even_when_other_group_advances(self):
        previous = describe(regional_now([('one', NOW-2*HOUR), ('two', NOW-HOUR)]))
        incoming = describe(regional_now([('one', NOW-HOUR), ('two', NOW-2*HOUR)]))
        self.assertEqual(compare(previous, incoming), 'regression')
        changed_scope = describe(regional_now([('one', NOW-HOUR), ('three', NOW-HOUR)]))
        self.assertEqual(compare(previous, changed_scope), 'incomparable')

    def test_missing_group_clock_or_identity_is_unknown(self):
        mixed = regional_now([('one', NOW-HOUR)])
        mixed['records'][0]['source_snapshot_at'] = None
        self.assertEqual(assess(describe(mixed), NOW)['state'], 'source_time_unknown')
        mixed['records'][0].pop('market_scope')
        self.assertEqual(assess(describe(mixed), NOW)['state'], 'source_time_unknown')

    def test_legacy_expired_mixed_unknown_watermark_does_not_block_new_evidence(self):
        legacy = batch('undermine', 'region_item_now', scope('EU', 'region_aggregate'), [])
        current = regional_now([('one', NOW-HOUR)])
        self.assertEqual(compare(describe(legacy), describe(current)), 'advance')

    def test_old_history_points_not_current_or_a_regression(self):
        history = batch('undermine', 'commodity_hourly', scope('EU', 'commodity'),
                        [{'record_type': 'history', 'source_snapshot_at': 100}], source_generated_at=NOW-10)
        before = describe(history)
        history['records'].append({'record_type': 'history', 'source_snapshot_at': 50})
        history['source_generated_at'] = NOW-5
        after = describe(history)
        self.assertEqual(compare(before, after), 'advance')
        self.assertEqual(assess(after, NOW)['state'], 'historical_dataset')
        self.assertFalse(assess(after, NOW)['current_usable'])

    def test_documented_zero_quantity_adapter_does_not_reuse_last_seen(self):
        raw = json.dumps({'request': {'region': 'eu', 'itemId': 123, 'period': 'now'},
                          'result': {'lastUpdated': '2020-01-01T10:05:00Z', 'lastSeen': '2020-01-01T10:00:00Z',
                                     'price': 100, 'quantity': 0, 'auctions': []}}).encode()
        normalized = normalize_undermine(raw, region='EU', dataset='commodity_now', item_id=123)
        self.assertEqual(assess(describe(normalized), NOW)['state'], 'no_current_quote')

    def test_static_reference_and_unknown_source_never_become_current_prices(self):
        reference = batch('undermine', 'realms', scope('EU', 'reference'), [{}], source_generated_at=NOW-HOUR)
        self.assertEqual(assess(describe(reference), NOW)['state'], 'reference_dataset')
        self.assertFalse(assess(describe(reference), NOW)['current_usable'])
        self.assertEqual(assess(describe({'source': 'blizzard'}), NOW)['state'], 'unsupported_dataset')

    def test_bad_descriptor_is_safe_unknown_not_positive_eligibility(self):
        for descriptor in (None, {}, {'version': 1, 'provider': []}, {'version': True}):
            self.assertFalse(assess(descriptor, NOW)['current_usable'])
            self.assertEqual(compare(descriptor, describe(tsm())), 'incomparable' if descriptor is not None else 'initial')


if __name__ == '__main__':
    unittest.main()
