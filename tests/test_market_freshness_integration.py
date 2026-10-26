"""S2-01 integration: real worker/normalizers/SQLite, synthetic clocks and HTTP."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.market_common import batch, scope
from wow_auction.market_http import HttpResponse
from wow_auction.market_policy import source_policy
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker
from tests.test_market_worker import CSV, ScriptedClient


SECOND = 10**9
HOUR = 3600
NOW = 1_793_016_000


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace('+00:00', 'Z')


class MarketFreshnessIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='synthetic_freshness_', dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'market.sqlite3'
        self.now = NOW * SECOND
        clock = patch('time.time_ns', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.store = MarketStore(self.path)
        self.addCleanup(lambda: self.store.db.close())
        self.job = {'provider': 'tsm', 'dataset': 'commodities', 'region': 'EU',
                    'interval_seconds': 3600, 'enabled': True}
        self.config = {'region': 'EU', 'realm_slug': 'synthetic-realm', 'jobs': [self.job],
                       'allow_paid_undermine': False}
        self.client = ScriptedClient(lambda: self.now)
        self.worker = MarketWorker(self.store, self.config, client=self.client,
                                   secrets={'undermine_api_key': 'SYNTHETIC'})
        self.key = self.store.register(self.job)

    def reply(self, body=CSV, *, status=200, headers=None):
        self.now += 2*SECOND
        self.client.replies.append((status, body, headers or {'content-type': 'text/csv', 'etag': '"A"'}))
        return self.worker.run_job(self.key, self.job, force=True)

    def diagnostic(self):
        return self.store.report([self.key])[0]['snapshot']

    def reopen(self):
        self.store.db.close()
        self.store = MarketStore(self.path)
        self.worker = MarketWorker(self.store, self.config, client=self.client,
                                   secrets={'undermine_api_key': 'SYNTHETIC'})

    def use_ue(self, dataset):
        self.job = {'provider': 'undermine', 'dataset': dataset, 'region': 'EU',
                    'interval_seconds': 3600, 'enabled': True, 'item_id': 123}
        self.config['jobs'] = [self.job]
        self.key = self.store.register(self.job)
        policy = {**source_policy('undermine'), 'network_allowed': True, 'retention_seconds': 4*HOUR,
                  'fingerprint': 'synthetic-freshness-policy-only'}
        change = patch('wow_auction.market_worker.source_policy', return_value=policy)
        change.start()
        self.addCleanup(change.stop)

    def ue_reply(self, result, period):
        raw = json.dumps({'request': {'region': 'eu', 'itemId': 123, 'period': period},
                          'result': result}).encode()
        return self.reply(raw, headers={'content-type': 'application/json', 'etag': '"UE"'})

    def test_a_b_a_retains_newer_current_and_original_availability(self):
        first = self.reply()
        original = self.store.current(self.key)
        b = CSV.replace(b'09:00:00', b'08:00:00').replace(b',80,', b',70,')
        rolled_back = self.reply(b, headers={'content-type': 'text/csv', 'etag': '"B"'})
        self.assertEqual(rolled_back['status'], 'source_regression_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], first['snapshot_id'])
        self.assertFalse(self.diagnostic()['capabilities_currently_usable'])
        again = self.reply()
        current = self.store.current(self.key)
        self.assertEqual(again['status'], 'unchanged')
        self.assertEqual(current['id'], original['id'])
        self.assertEqual(current['available_at_ns'], original['available_at_ns'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 2)
        self.assertTrue(self.diagnostic()['capabilities_currently_usable'])

    def test_same_scan_correction_is_new_immutable_revision(self):
        first = self.reply()
        original = self.store.current(self.key)
        second = self.reply(CSV.replace(b',80,', b',81,'))
        self.assertEqual(second['status'], 'accepted')
        self.assertNotEqual(second['snapshot_id'], first['snapshot_id'])
        self.assertEqual(self.store.current(self.key)['source_snapshot_at'], original['source_snapshot_at'])
        prior = self.store.db.execute('SELECT normalized,available_at_ns FROM snapshots WHERE id=?',
                                      (first['snapshot_id'],)).fetchone()
        self.assertEqual(json.loads(prior['normalized'])['records'][0]['unit_price_copper'], 80)
        self.assertEqual(prior['available_at_ns'], original['available_at_ns'])

    def test_stale_200_and_confirmed_304_do_not_become_current_usable(self):
        old = CSV.replace(b'2026-10-26', b'2020-01-01')
        self.assertEqual(self.reply(old)['status'], 'accepted')
        original = self.store.current(self.key)
        self.assertTrue(original['cache_available'])
        self.assertFalse(original['usable'])
        self.assertEqual(self.diagnostic()['freshness_state'], 'source_stale')
        self.assertEqual(self.reply(b'', status=304, headers={'etag': '"A"'})['status'], 'unchanged')
        repeated = self.store.current(self.key)
        self.assertEqual(repeated['available_at_ns'], original['available_at_ns'])
        self.assertEqual(self.diagnostic()['freshness_state'], 'source_stale')
        self.assertFalse(self.diagnostic()['capabilities_currently_usable'])

    def test_304_crossing_age_boundary_does_not_renew_source_age(self):
        body = CSV.replace(b'09:00:00', b'04:00:01')
        self.now = NOW * SECOND - 10*SECOND
        self.reply(body)
        self.assertTrue(self.store.current(self.key)['usable'])
        self.now += 20*SECOND
        self.reply(b'', status=304, headers={'etag': '"A"'})
        self.assertFalse(self.store.current(self.key)['usable'])
        self.assertEqual(self.diagnostic()['freshness_state'], 'source_stale')

    def test_restart_retention_keeps_watermark_and_reacquisition_is_not_backdated(self):
        self.reply()
        original = self.store.current(self.key)
        self.store.reference('original-decision', original['id'])
        self.now += (4*HOUR+1)*SECOND
        self.store.expire()
        self.reopen()
        expired = self.store.current(self.key)
        self.assertIsNone(expired['normalized'])
        self.assertIsNotNone(expired['freshness_descriptor'])
        backward = CSV.replace(b'09:00:00', b'08:00:00')
        self.assertEqual(self.reply(backward)['status'], 'source_regression_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], original['id'])
        reacquired = self.reply()
        self.assertEqual(reacquired['status'], 'accepted')
        current = self.store.current(self.key)
        self.assertGreater(current['available_at_ns'], original['available_at_ns'])
        self.assertEqual(current['source_snapshot_at'], original['source_snapshot_at'])
        self.store.reference('reacquired-input', current['id'])
        self.assertEqual(self.store.replay_input('reacquired-input', decision_at_ns=original['available_at_ns'])['status'],
                         'not_available_at_decision')
        self.assertEqual(self.store.replay_input('original-decision')['status'], 'replay_unavailable_due_to_retention')

    def test_stale_reacquisition_after_expiry_is_still_unusable(self):
        old = CSV.replace(b'2026-10-26', b'2020-01-01')
        self.reply(old)
        self.now += (4*HOUR+1)*SECOND
        self.store.expire()
        self.reply(old)
        self.assertTrue(self.store.current(self.key)['cache_available'])
        self.assertFalse(self.store.current(self.key)['usable'])
        self.assertEqual(self.diagnostic()['freshness_state'], 'source_stale')

    def test_parser_revision_cannot_reset_old_source_age(self):
        old = CSV.replace(b'2026-10-26', b'2020-01-01')
        with patch.object(self.worker, '_normalizer_revision', return_value='parser-v1'):
            self.reply(old)
        original = self.store.current(self.key)
        with patch.object(self.worker, '_normalizer_revision', return_value='parser-v2'):
            self.assertEqual(self.reply(old)['status'], 'accepted')
        current = self.store.current(self.key)
        self.assertNotEqual(current['id'], original['id'])
        self.assertNotIn('If-None-Match', self.client.calls[-1]['headers'])
        self.assertFalse(current['usable'])
        self.assertEqual(current['source_snapshot_at'], original['source_snapshot_at'])

    def test_unknown_source_time_does_not_displace_existing_known_scan(self):
        self.reply()
        original = self.store.current(self.key)
        unknown = batch('tsm', 'commodities', scope('EU', 'commodity'), [{'record_type': 'aggregate'}])
        body = b'SYNTHETIC_UNKNOWN_SOURCE_CLOCK'
        response = HttpResponse(200, body, {}, self.now, 0, len(body))
        result = self.store.accept(self.key, body, unknown, response, source_policy('tsm'))
        self.assertEqual(result['status'], 'source_missing_clock_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], original['id'])

    def test_future_initial_observation_cannot_poison_next_valid_source_clock(self):
        future = CSV.replace(b'2026-10-26', b'2030-01-01')
        self.reply(future)
        self.assertFalse(self.store.current(self.key)['usable'])
        self.assertEqual(self.diagnostic()['freshness_state'], 'source_clock_ahead')
        self.assertEqual(self.reply()['status'], 'accepted')
        self.assertTrue(self.store.current(self.key)['usable'])

    def test_history_points_and_zero_quantity_are_not_false_current_prices(self):
        self.use_ue('commodity_hourly')
        result = {'lastUpdated': iso(NOW-10), 'hourly': [
            {'snapshot': '2020-01-01T10:00:00Z', 'price': 100, 'quantity': 1}]}
        self.assertEqual(self.ue_reply(result, 'hourly')['status'], 'accepted')
        self.assertEqual(self.diagnostic()['freshness_state'], 'historical_dataset')
        self.assertFalse(self.store.current(self.key)['usable'])
        self.assertTrue(self.store.current(self.key)['cache_available'])
        self.use_ue('commodity_now')
        result = {'lastUpdated': '2020-01-01T10:05:00Z', 'lastSeen': '2020-01-01T10:00:00Z',
                  'price': 100, 'quantity': 0, 'auctions': []}
        self.assertEqual(self.ue_reply(result, 'now')['status'], 'accepted')
        self.assertEqual(self.diagnostic()['freshness_state'], 'no_current_quote')
        self.assertFalse(self.diagnostic()['capabilities_currently_usable'])

    def test_zero_does_not_erase_positive_watermark_before_or_after_retention(self):
        self.use_ue('commodity_now')
        positive = {'lastUpdated': iso(NOW-3*HOUR+300), 'lastSeen': iso(NOW-3*HOUR),
                    'price': 100, 'quantity': 1, 'auctions': [{'price': 100, 'quantity': 1}]}
        self.assertEqual(self.ue_reply(positive, 'now')['status'], 'accepted')
        positive_id = self.store.current(self.key)['id']
        # The stale lastSeen is permitted for zero; it must not replace the
        # stronger, already observed positive scan in retained ordering metadata.
        zero = {'lastUpdated': iso(NOW-3*HOUR+360), 'lastSeen': iso(NOW-24*HOUR),
                'price': 100, 'quantity': 0, 'auctions': []}
        self.assertEqual(self.ue_reply(zero, 'now')['status'], 'accepted')
        zero_row = self.store.current(self.key)
        descriptor = json.loads(zero_row['freshness_descriptor'])
        self.assertEqual(descriptor['ordering_clocks']['scan'], NOW-3*HOUR)
        self.assertEqual(descriptor['ordering_watermark_inherited_from_snapshot_id'], positive_id)
        self.assertEqual(descriptor['freshness_clocks'], {})
        self.assertEqual(self.diagnostic()['freshness_state'], 'no_current_quote')
        backward = {'lastUpdated': iso(NOW-3*HOUR+420), 'lastSeen': iso(NOW-3*HOUR-600),
                    'price': 90, 'quantity': 1, 'auctions': [{'price': 90, 'quantity': 1}]}
        self.assertEqual(self.ue_reply(backward, 'now')['status'], 'source_regression_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], zero_row['id'])
        self.now += (4*HOUR+1)*SECOND
        self.store.expire()
        self.reopen()
        self.assertIsNone(self.store.current(self.key)['normalized'])
        self.assertEqual(self.ue_reply(backward, 'now')['status'], 'source_regression_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], zero_row['id'])
        self.assertFalse(self.store.current(self.key)['usable'])

    def test_initial_zero_last_seen_blocks_subsequent_backwards_positive_scan(self):
        self.use_ue('commodity_now')
        zero = {'lastUpdated': iso(NOW-3*HOUR+360), 'lastSeen': iso(NOW-3*HOUR),
                'price': 100, 'quantity': 0, 'auctions': []}
        self.assertEqual(self.ue_reply(zero, 'now')['status'], 'accepted')
        zero_id = self.store.current(self.key)['id']
        backward = {'lastUpdated': iso(NOW-3*HOUR+420), 'lastSeen': iso(NOW-3*HOUR-600),
                    'price': 90, 'quantity': 1, 'auctions': [{'price': 90, 'quantity': 1}]}
        self.assertEqual(self.ue_reply(backward, 'now')['status'], 'source_regression_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], zero_id)

    def test_future_flag_requires_full_revalidation_after_wall_clock_catches_up(self):
        self.use_ue('commodity_now')
        zero = {'lastUpdated': iso(NOW+100), 'lastSeen': iso(NOW-3*HOUR),
                'price': 100, 'quantity': 0, 'auctions': []}
        self.ue_reply(zero, 'now')
        original = self.store.current(self.key)
        self.assertEqual(self.diagnostic()['freshness_state'], 'source_clock_ahead')
        self.now += 200*SECOND
        self.assertEqual(self.diagnostic()['freshness_state'], 'source_clock_ahead')
        result = self.ue_reply(zero, 'now')
        self.assertEqual(result['status'], 'accepted')
        current = self.store.current(self.key)
        self.assertNotEqual(current['id'], original['id'])
        self.assertNotIn('If-None-Match', self.client.calls[-1]['headers'])
        self.assertEqual(self.diagnostic()['freshness_state'], 'no_current_quote')
        self.assertFalse(json.loads(current['freshness_descriptor'])['source_clock_ahead_at_receive'])

    def test_mixed_group_clocks_reject_one_regression_without_losing_last_good(self):
        self.use_ue('region_item_now')
        initial = [{'lastUpdated': iso(NOW-100), 'snapshot': iso(NOW-110), 'price': 100,
                    'quantity': 1, 'realms': ['one']},
                   {'lastUpdated': iso(NOW-100), 'snapshot': iso(NOW-110), 'price': 200,
                    'quantity': 1, 'realms': ['two']}]
        self.assertEqual(self.ue_reply(initial, 'now')['status'], 'accepted')
        original = self.store.current(self.key)
        initial[0]['snapshot'] = iso(NOW-50)
        initial[0]['lastUpdated'] = iso(NOW-40)
        initial[1]['snapshot'] = iso(NOW-200)
        initial[1]['lastUpdated'] = iso(NOW-190)
        self.assertEqual(self.ue_reply(initial, 'now')['status'], 'source_regression_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], original['id'])


if __name__ == '__main__':
    unittest.main()
