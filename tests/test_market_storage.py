"""Synthetic market persistence tests; no live API or retention-policy exception claims."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.market_common import MarketError, batch, fingerprint, scope, strict_json
from wow_auction.market_http import HttpResponse
from wow_auction.market_storage import APP_ID, MarketStore


SECOND = 1_000_000_000


class MarketStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='synthetic_market_', dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'market.sqlite3'
        self.now = 2_000_000_000 * SECOND
        clock = patch('wow_auction.market_storage.time.time_ns', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.store = MarketStore(self.path)
        self.addCleanup(lambda: self.store.db.close())
        self.job = {'provider': 'tsm', 'dataset': 'commodities', 'region': 'EU',
                    'interval_seconds': 3600, 'enabled': True}
        self.key = self.store.register(self.job)
        self.policy = {'network_allowed': True, 'retention_seconds': 3600,
                       'source': 'tsm', 'test_evidence': 'synthetic only'}
        self.policy['fingerprint'] = fingerprint(self.policy)

    def data(self, price=123456789, *, completeness='complete', region='EU', dataset='commodities',
             connected_id=None, item_id=123, source='tsm'):
        market_scope = scope(region, 'connected_realm' if connected_id else 'commodity',
                             connected_realm_id=connected_id)
        records = [{'record_type': 'offer', 'item_id': item_id, 'auction_id': 456,
                    'quantity': 3, 'unit_price_copper': price, 'quote_available': True}]
        normalized = batch(source, dataset, market_scope, records,
                           source_snapshot_at=1_999_990_000, completeness=completeness)
        raw = json.dumps({'synthetic': records}, separators=(',', ':')).encode('ascii')
        return raw, normalized

    def accept(self, price=123456789, *, key=None, received_at_ns=None, policy=None,
               fault=None, lease_token=None, **kwargs):
        body, normalized = self.data(price, **kwargs)
        response = HttpResponse(200, body, {'etag': '"synthetic-etag"'},
                                self.now if received_at_ns is None else received_at_ns, 0.1, len(body))
        return self.store.accept(key or self.key, body, normalized, response, policy or self.policy,
                                 fault=fault, lease_token=lease_token)

    def rows(self, table):
        return [dict(row) for row in self.store.db.execute('SELECT * FROM ' + table)]

    def test_first_accept_preserves_exact_raw_normalized_and_separate_clocks(self):
        received = self.now - 3 * SECOND
        outcome = self.accept(received_at_ns=received)
        row = self.store.current(self.key)
        body, normalized = self.data()
        self.assertEqual(outcome['status'], 'accepted')
        self.assertEqual(row['raw'], body)
        self.assertEqual(json.loads(row['normalized']), normalized)
        self.assertEqual(row['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(row['source_snapshot_at'], 1_999_990_000)
        self.assertEqual(row['received_at_ns'], received)
        self.assertEqual(row['available_at_ns'], self.now)
        self.assertEqual(row['expires_at_ns'], received + 3600 * SECOND)
        self.assertTrue(row['usable'])
        self.assertIsNone(normalized['public_available_lower_bound'])
        self.assertEqual(len(self.rows('policies')), 1)

    def test_identical_success_records_observation_without_refreshing_snapshot(self):
        first = self.accept()
        before = self.store.current(self.key)
        self.now += 700 * SECOND
        repeated = self.accept()
        after = self.store.current(self.key)
        self.assertEqual(repeated['status'], 'unchanged')
        self.assertEqual(repeated['snapshot_id'], first['snapshot_id'])
        self.assertEqual(before, after)
        self.assertEqual(len(self.rows('snapshots')), 1)
        attempts = self.rows('attempts')
        self.assertEqual([r['status'] for r in attempts], ['accepted', 'unchanged'])
        self.assertEqual(attempts[-1]['received_at_ns'], self.now)

    def test_partial_observation_cannot_replace_last_complete_book(self):
        first = self.accept()
        self.now += SECOND
        partial = self.accept(999, completeness='partial')
        self.assertEqual(partial['status'], 'partial_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], first['snapshot_id'])
        self.assertEqual(len(self.rows('snapshots')), 2)
        self.assertEqual(self.rows('snapshots')[-1]['completeness'], 'partial')

    def test_schema_failure_quarantine_preserves_current_data_and_clocks(self):
        self.accept()
        before = self.store.current(self.key)
        self.now += SECOND
        try:
            strict_json(b'{"auctions":[],"auctions":[{"bad":true}]}')
        except MarketError as exc:
            self.store.reject(self.key, exc.code, http_status=200, received_at_ns=self.now)
        else:
            self.fail('Synthetic duplicate-key payload must fail normalization')
        self.assertEqual(self.store.current(self.key), before)
        self.assertEqual(len(self.rows('snapshots')), 1)
        self.assertEqual(self.rows('attempts')[-1]['status'], 'quarantined')
        self.assertEqual(self.rows('attempts')[-1]['received_at_ns'], self.now)

    def test_empty_complete_book_replaces_previous_nonempty_book(self):
        self.accept()
        raw, normalized = self.data()
        normalized['records'] = []
        raw = b'{"auctions":[]}'
        response = HttpResponse(200, raw, {}, self.now, 0.1, len(raw))
        result = self.store.accept(self.key, raw, normalized, response, self.policy)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(self.store.current(self.key)['record_count'], 0)
        self.assertEqual(json.loads(self.store.current(self.key)['normalized'])['records'], [])

    def test_transaction_faults_before_commit_roll_back_every_new_row(self):
        self.accept()
        before = {table: self.rows(table) for table in ('snapshots', 'attempts', 'current_datasets', 'policies')}
        for boundary in ('after_snapshot', 'before_commit'):
            with self.subTest(boundary=boundary):
                def fail(point):
                    if point == boundary:
                        raise RuntimeError('synthetic crash')
                with self.assertRaisesRegex(RuntimeError, 'synthetic crash'):
                    self.accept(987, fault=fail)
                for table, expected in before.items():
                    self.assertEqual(self.rows(table), expected)

    def test_committed_crash_recovers_with_actual_reopen_availability(self):
        first = self.accept()
        self.store.reference('old-decision', first['snapshot_id'])
        self.now += 10 * SECOND
        committed_at = self.now
        def fail(point):
            if point == 'after_commit':
                raise RuntimeError('synthetic process crash after commit')
        with self.assertRaisesRegex(RuntimeError, 'after commit'):
            self.accept(987, fault=fail)
        pending = self.store.current(self.key)
        self.assertIsNone(pending['available_at_ns'])
        self.assertFalse(pending['usable'])
        self.store.reference('new-decision', pending['id'])
        self.assertEqual(self.store.replay_input('new-decision')['status'], 'not_available_at_decision')
        self.assertEqual(self.store.replay_input('old-decision')['status'], 'available')
        self.store.db.close()
        self.now += 25 * SECOND
        self.store = MarketStore(self.path)
        recovered = self.store.current(self.key)
        self.assertEqual(recovered['id'], pending['id'])
        self.assertEqual(recovered['available_at_ns'], self.now)
        self.assertEqual(recovered['received_at_ns'], committed_at)
        self.assertTrue(recovered['usable'])
        self.assertEqual(self.store.replay_input('new-decision', decision_at_ns=committed_at)['status'],
                         'not_available_at_decision')
        self.assertEqual(self.store.replay_input('new-decision', decision_at_ns=self.now)['status'], 'available')
        self.assertEqual(len(self.rows('snapshots')), 2)

    def test_replay_never_uses_source_or_receive_clock_as_local_availability(self):
        result = self.accept(received_at_ns=self.now - 30 * SECOND)
        self.store.reference('synthetic-plan-input', result['snapshot_id'])
        self.assertEqual(self.store.replay_input('synthetic-plan-input', decision_at_ns=self.now - 1)['status'],
                         'not_available_at_decision')
        replay = self.store.replay_input('synthetic-plan-input', decision_at_ns=self.now)
        self.assertEqual(replay['status'], 'available')
        self.assertEqual(replay['normalized']['records'][0]['unit_price_copper'], 123456789)
        self.assertEqual(self.store.replay_input('missing-reference')['status'], 'unknown_input_reference')

    def test_retention_erases_both_price_copies_and_preserves_only_unavailable_reference(self):
        price = 734819526058713
        result = self.accept(price)
        self.store.reference('synthetic-price-input', result['snapshot_id'])
        self.assertIn(str(price).encode(), self.path.read_bytes())
        self.now += 3600 * SECOND
        self.assertEqual(self.store.replay_input('synthetic-price-input')['status'],
                         'replay_unavailable_due_to_retention')
        self.assertFalse(self.store.current(self.key)['usable'])
        self.assertEqual(self.store.expire(now_ns=self.now), 1)
        row = self.store.current(self.key)
        self.assertIsNone(row['raw'])
        self.assertIsNone(row['normalized'])
        self.assertEqual(row['content_status'], 'replay_unavailable_due_to_retention')
        replay = self.store.replay_input('synthetic-price-input', decision_at_ns=self.now - 100 * SECOND)
        self.assertEqual(replay, {'status': 'replay_unavailable_due_to_retention',
                                  'snapshot_id': result['snapshot_id'], 'sha256': row['sha256']})
        self.assertNotIn(str(price).encode(), self.path.read_bytes())
        self.assertFalse(Path(str(self.path) + '-wal').exists())
        self.assertFalse(Path(str(self.path) + '-journal').exists())
        self.assertEqual(self.store.expire(now_ns=self.now), 0)
        self.assertEqual(len(self.rows('input_references')), 1)

    def test_expiration_removes_realm_mapping_without_deleting_snapshot_identity(self):
        result = self.accept()
        self.store.db.execute('INSERT INTO realm_mappings VALUES (?,?,?,?,?,?,?)',
                              ('EU', 'synthetic-realm', 123, 456, result['snapshot_id'], self.now,
                               self.now + SECOND))
        self.now += SECOND
        self.assertEqual(self.store.expire(now_ns=self.now), 0)
        self.assertEqual(self.rows('realm_mappings'), [])
        self.assertEqual(len(self.rows('snapshots')), 1)

    def test_disabled_policy_never_inserts_any_content(self):
        policy = {**self.policy, 'network_allowed': False}
        with self.assertRaisesRegex(MarketError, 'market_source_policy_blocked'):
            self.accept(policy=policy)
        self.assertEqual(self.rows('snapshots'), [])
        self.assertEqual(self.rows('attempts'), [])
        self.assertEqual(self.rows('policies'), [])

    def test_register_updates_schedule_without_forking_market_identity(self):
        changed = {**self.job, 'interval_seconds': 7200, 'enabled': False, 'locale': 'en_GB'}
        same = self.store.register(changed)
        self.assertEqual(same, self.key)
        self.assertEqual(len(self.rows('jobs')), 1)
        self.assertEqual(json.loads(self.rows('jobs')[0]['config']), changed)

    def test_distinct_market_scope_dataset_provider_and_item_jobs_stay_separate(self):
        jobs = [self.job, {**self.job, 'region': 'US'}, {**self.job, 'dataset': 'auctions', 'connected_realm_id': 100},
                {**self.job, 'dataset': 'auctions', 'connected_realm_id': 200},
                {**self.job, 'provider': 'undermine'}, {**self.job, 'realm_slug': 'first'},
                {**self.job, 'realm_slug': 'second'}, {**self.job, 'dataset': 'item', 'item_id': 111},
                {**self.job, 'dataset': 'item', 'item_id': 222}]
        keys = [self.store.register(job) for job in jobs]
        self.assertEqual(len(set(keys)), len(jobs))
        eu = self.accept()
        us = self.accept(key=keys[1], region='US', price=456)
        self.assertEqual(self.store.current(self.key)['id'], eu['snapshot_id'])
        self.assertEqual(self.store.current(keys[1])['id'], us['snapshot_id'])
        self.assertEqual(json.loads(self.store.current(keys[1])['scope'])['region'], 'US')

    def test_lease_excludes_other_workers_and_expired_owner_cannot_publish(self):
        owner = self.store.claim(self.key, self.now)
        self.assertTrue(owner)
        with MarketStore(self.path) as other:
            self.assertIsNone(other.claim(self.key, self.now, force=True))
            self.now += 181 * SECOND
            replacement = other.claim(self.key, self.now, force=True)
            self.assertNotEqual(owner, replacement)
            with self.assertRaisesRegex(MarketError, 'market_worker_lease_lost'):
                self.accept(lease_token=owner)
            self.assertEqual(self.rows('snapshots'), [])
            self.accept(lease_token=replacement)
            with self.assertRaisesRegex(MarketError, 'market_worker_lease_lost'):
                self.store.finish(self.key, owner, health='failed', next_due_ns=self.now + SECOND)
            self.assertEqual(self.rows('jobs')[0]['lease_token'], replacement)

    def test_expired_lease_rejects_publish_even_before_another_worker_claims(self):
        token = self.store.claim(self.key, self.now)
        self.now += 180 * SECOND
        with self.assertRaisesRegex(MarketError, 'market_worker_lease_lost'):
            self.accept(lease_token=token)
        self.assertEqual(self.rows('snapshots'), [])

    def test_scheduling_finish_and_downtime_gap_keep_real_observation_time(self):
        token = self.store.claim(self.key, self.now)
        self.accept(lease_token=token)
        due = self.now + 3600 * SECOND
        self.store.finish(self.key, token, health='healthy', next_due_ns=due,
                          headers={'etag': '"first"', 'last-modified': 'synthetic-document-time'})
        self.assertIsNone(self.store.claim(self.key, due - 1))
        self.now = due + 7200 * SECOND
        resumed = self.store.claim(self.key, self.now)
        self.assertTrue(resumed)
        self.accept(456, lease_token=resumed)
        row = self.store.current(self.key)
        self.assertEqual(row['received_at_ns'], self.now)
        self.assertEqual(row['available_at_ns'], self.now)
        gap = self.rows('gaps')[0]
        self.assertEqual((gap['start_ns'], gap['end_ns']), (due, self.now))
        self.assertEqual(gap['reason'], 'worker_not_observed_at_scheduled_time')
        report = self.store.report([self.key])[0]
        self.assertEqual(report['gap_count'], 1)
        self.assertEqual(report['snapshot']['available_at_ns'], self.now)

    def test_failure_count_resets_only_on_success_and_validators_survive_failure(self):
        token = self.store.claim(self.key, self.now)
        self.accept(lease_token=token)
        self.store.finish(self.key, token, health='healthy', next_due_ns=self.now,
                          headers={'etag': '"good"'})
        for expected in (1, 2):
            token = self.store.claim(self.key, self.now)
            self.store.finish(self.key, token, health='retry_wait', next_due_ns=self.now, failed=True)
            self.assertEqual(self.rows('jobs')[0]['failures'], expected)
            self.assertEqual(self.rows('http_validators')[0]['etag'], '"synthetic-etag"')
            self.assertIsNone(self.rows('jobs')[0]['etag'])
        token = self.store.claim(self.key, self.now)
        self.store.finish(self.key, token, health='healthy', next_due_ns=self.now)
        self.assertEqual(self.rows('jobs')[0]['failures'], 0)

    def test_rate_budget_is_durable_shared_and_provider_separate(self):
        for _ in range(5):
            self.assertIsNone(self.store.reserve('tsm', 1, self.now))
        with MarketStore(self.path) as other:
            self.assertEqual(other.reserve('tsm', 1, self.now), self.now + SECOND)
            self.assertIsNone(other.reserve('blizzard', 25, self.now))
            self.assertIsNone(other.reserve('tsm', 1, self.now + SECOND))
        self.assertEqual(len(self.rows('rate_usage')), 7)

    def test_hourly_weighted_quota_rejects_without_recording_extra_usage(self):
        old = self.now - 10 * SECOND
        self.store.db.execute('INSERT INTO rate_usage VALUES (?,?,?)', ('blizzard', old, 36000 - 25))
        self.assertIsNone(self.store.reserve('blizzard', 25, self.now))
        self.assertEqual(self.store.reserve('blizzard', 25, self.now), old + 3600 * SECOND)
        self.assertEqual(sum(r['points'] for r in self.rows('rate_usage')), 36000)

    def test_rate_usage_expires_at_exact_window_boundary(self):
        old = self.now - 3600 * SECOND
        self.store.db.execute('INSERT INTO rate_usage VALUES (?,?,?)', ('blizzard', old, 36000))
        self.assertIsNone(self.store.reserve('blizzard', 25, self.now))
        self.assertEqual(sum(r['points'] for r in self.rows('rate_usage')), 25)

    def test_provider_cooldown_is_shared_durable_monotonic_and_provider_separate(self):
        deadline = self.now + 900 * SECOND
        self.assertEqual(self.store.set_provider_cooldown('tsm', deadline), deadline)
        with MarketStore(self.path) as other:
            self.assertEqual(other.reserve('tsm', 1, self.now), deadline)
            self.assertEqual(other.set_provider_cooldown('tsm', self.now + 10 * SECOND), deadline)
            self.assertIsNone(other.reserve('undermine', 3, self.now))
            longer = deadline + 600 * SECOND
            self.assertEqual(other.set_provider_cooldown('tsm', longer), longer)
        self.store.db.close()
        self.store = MarketStore(self.path)
        self.assertEqual(self.store.reserve('tsm', 1, longer - 1), longer)
        self.assertEqual([row['provider'] for row in self.rows('rate_usage')], ['undermine'])
        self.assertIsNone(self.store.reserve('tsm', 1, longer))
        self.assertEqual([row['provider'] for row in self.rows('rate_usage')], ['undermine', 'tsm'])

    def test_provider_cooldown_caps_huge_deadlines_without_integer_overflow(self):
        maximum = 2**63 - 1
        self.assertEqual(self.store.set_provider_cooldown('undermine', 10**100), maximum)
        self.assertEqual(self.store.reserve('undermine', 1, self.now), maximum)
        self.assertEqual(self.store.set_provider_cooldown('undermine', self.now), maximum)
        self.assertEqual(self.rows('rate_usage'), [])
        self.assertEqual(self.rows('provider_cooldowns')[0]['deadline_ns'], maximum)
        for provider, deadline in [('unknown', self.now), ('tsm', True), ('tsm', -1), ('tsm', '999')]:
            with self.subTest(provider=provider, deadline=deadline), self.assertRaises(MarketError):
                self.store.set_provider_cooldown(provider, deadline)

    def test_reserve_rejects_invalid_cost_provider_and_clock_before_mutating_usage(self):
        self.assertIsNone(self.store.reserve('tsm', 1, self.now))
        original = self.rows('rate_usage')
        cases = [('tsm', 0, self.now), ('tsm', -1, self.now), ('tsm', True, self.now),
                 ('tsm', 1.0, self.now), ('tsm', '1', self.now), ('tsm', 121, self.now),
                 ('undermine', 3001, self.now), ('blizzard', 36001, self.now),
                 ('unknown', 1, self.now), ([], 1, self.now),
                 ('tsm', 1, -1), ('tsm', 1, True), ('tsm', 1, 2**63), ('tsm', 1, 1.5)]
        for provider, points, now in cases:
            with self.subTest(provider=provider, points=points, now=now), self.assertRaises(MarketError):
                self.store.reserve(provider, points, now)
            self.assertEqual(self.rows('rate_usage'), original)
        # Invalid calls also fail while a server cooldown is active.
        self.store.set_provider_cooldown('tsm', self.now + SECOND)
        with self.assertRaisesRegex(MarketError, 'invalid_market_rate_cost'):
            self.store.reserve('tsm', -1, self.now)
        self.assertEqual(self.rows('rate_usage'), original)

    def test_reserve_exact_budget_and_extreme_valid_clock_stay_representable(self):
        maximum = 2**63 - 1
        self.assertIsNone(self.store.reserve('tsm', 120, maximum))
        self.assertEqual(self.store.reserve('tsm', 1, maximum), maximum)
        self.assertEqual(self.rows('rate_usage'), [{'provider': 'tsm', 'at_ns': maximum, 'points': 120}])
        for _ in range(100):
            self.assertIsNone(self.store.reserve('undermine', 1, maximum))
        self.assertEqual(self.store.reserve('undermine', 1, maximum), maximum)

    def test_additive_v1_migration_preserves_existing_content_and_retry_state(self):
        accepted = self.accept()
        self.store.reference('v1-existing-input', accepted['snapshot_id'])
        self.store.reserve('tsm', 1, self.now)
        deadline = self.now + 900 * SECOND
        self.store.db.execute("UPDATE jobs SET health='rate_limited',next_due_ns=?,failures=2 WHERE job_key=?", (deadline, self.key))
        tables = ['snapshots', 'current_datasets', 'attempts', 'policies', 'jobs', 'rate_usage', 'input_references']
        before = {table: self.rows(table) for table in tables}
        current = self.store.current(self.key)
        # Remove the additive v2/v3 structures to simulate an actual v1 schema.
        self.store.db.execute('DROP TABLE http_validators')
        self.store.db.execute('ALTER TABLE snapshots DROP COLUMN freshness_descriptor')
        self.store.db.execute('DROP TABLE provider_cooldowns')
        self.store.db.execute('PRAGMA user_version=1')
        self.store.db.close()
        self.now += SECOND
        self.store = MarketStore(self.path)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(self.store.db.execute('PRAGMA application_id').fetchone()[0], APP_ID)
        self.assertEqual({table: self.rows(table) for table in tables}, before)
        self.assertEqual(self.store.current(self.key), current)
        self.assertEqual(self.store.replay_input('v1-existing-input')['status'], 'available')
        self.assertEqual(self.store.reserve('tsm', 1, self.now), deadline)
        self.assertEqual(self.rows('provider_cooldowns'), [{'provider': 'tsm', 'deadline_ns': deadline}])
        with MarketStore(self.path) as reopened:
            self.assertEqual(reopened.db.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(reopened.current(self.key)['raw'], current['raw'])
            self.assertEqual(reopened.reserve('tsm', 1, self.now), deadline)

    def test_report_exposes_bounded_capabilities_and_freshness_without_content(self):
        body, normalized = self.data()
        normalized.update(capabilities={'aggregates': True, 'depth': False, 'history': 'daily',
                                         'gzip_points': 3, 'unknown': 'PRIVATE_SECRET'},
                          warnings=['synthetic_warning'] * 40 + ['PRIVATE_SOURCE'], source_clock_ahead=False)
        normalized['records'][0]['name'] = 'PRIVATE_ITEM_NAME'
        response = HttpResponse(200, body, {}, self.now, 0.1, len(body))
        lease = self.store.claim(self.key, self.now)
        self.store.accept(self.key, body, normalized, response, self.policy, lease_token=lease)
        self.store.finish(self.key, lease, health='accepted', next_due_ns=self.now + 3600 * SECOND)
        self.now += 10 * SECOND
        report = self.store.report([self.key])[0]
        snapshot = report['snapshot']
        self.assertEqual(report['as_of_ns'], self.now)
        self.assertEqual(snapshot['capabilities'], {'aggregates': True, 'depth': False, 'history': 'daily', 'gzip_points': 3})
        self.assertEqual(snapshot['completeness'], 'complete')
        self.assertEqual(len(snapshot['warnings']), 32)
        self.assertFalse(snapshot['source_clock_ahead'])
        self.assertEqual(snapshot['received_age_seconds'], 10)
        self.assertEqual(snapshot['available_age_seconds'], 10)
        self.assertEqual(snapshot['source_snapshot_at_age_seconds'], 10010)
        self.assertIsNone(snapshot['source_generated_at_age_seconds'])
        self.assertEqual(snapshot['freshness_state'], 'current_observation')
        self.assertTrue(snapshot['capabilities_currently_usable'])
        self.assertNotIn('PRIVATE', json.dumps(report))
        self.assertNotIn('123456789', json.dumps(report))
        self.store.db.execute("UPDATE jobs SET health='authorization_unavailable' WHERE job_key=?", (self.key,))
        failed = self.store.report([self.key])[0]['snapshot']
        self.assertEqual(failed['freshness_state'], 'last_good_after_unsuccessful_refresh')
        self.assertFalse(failed['capabilities_currently_usable'])
        self.assertTrue(failed['usable'])
        self.now += 3600 * SECOND
        self.store.expire()
        expired = self.store.report([self.key])[0]['snapshot']
        self.assertEqual(expired['freshness_state'], 'expired_or_unavailable')
        self.assertIsNone(expired['capabilities'])
        self.assertIsNone(expired['warnings'])
        self.assertFalse(expired['capabilities_currently_usable'])

    def test_report_source_clock_anomaly_and_unknown_clock_are_explicit(self):
        body, normalized = self.data()
        normalized.update(source_snapshot_at=self.now // SECOND + 100, source_clock_ahead=True)
        self.store.accept(self.key, body, normalized, HttpResponse(200, body, {}, self.now, 0.1, len(body)), self.policy)
        snapshot = self.store.report([self.key])[0]['snapshot']
        self.assertEqual(snapshot['source_snapshot_at_age_seconds'], -100)
        self.assertEqual(snapshot['freshness_state'], 'source_clock_ahead')
        self.assertTrue(snapshot['source_clock_ahead'])
        self.assertFalse(snapshot['capabilities_currently_usable'])

    def test_foreign_database_is_rejected_without_schema_or_byte_changes(self):
        foreign = self.root / 'foreign.sqlite3'
        with closing(sqlite3.connect(foreign)) as db:
            db.execute('CREATE TABLE unrelated(secret TEXT)')
            db.execute('INSERT INTO unrelated VALUES (?)', ('synthetic-private-value',))
            db.commit()
        before = foreign.read_bytes()
        with self.assertRaisesRegex(MarketError, 'foreign_market_database'):
            MarketStore(foreign)
        self.assertEqual(foreign.read_bytes(), before)
        with closing(sqlite3.connect(foreign)) as db:
            self.assertEqual(db.execute('SELECT secret FROM unrelated').fetchone()[0], 'synthetic-private-value')

    def test_wal_database_is_refused_without_switching_journal_mode(self):
        wal = self.root / 'wal.sqlite3'
        with MarketStore(wal):
            pass
        with closing(sqlite3.connect(wal)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        before = wal.read_bytes()
        with self.assertRaisesRegex(MarketError, 'market_rollback_journal_required'):
            MarketStore(wal)
        self.assertEqual(wal.read_bytes(), before)
        with closing(sqlite3.connect(wal)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('PRAGMA application_id').fetchone()[0], APP_ID)

    def test_game_saved_variable_paths_are_never_opened_as_databases(self):
        for suffix in ('.lua', '.bak'):
            target = self.root / ('synthetic' + suffix)
            target.write_bytes(b'preserve synthetic addon data')
            with self.assertRaisesRegex(MarketError, 'unsafe_market_database_path'):
                MarketStore(target)
            self.assertEqual(target.read_bytes(), b'preserve synthetic addon data')


if __name__ == '__main__':
    unittest.main()
