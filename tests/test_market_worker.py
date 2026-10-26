"""MOCK integration against the real worker, SQLite store, and source adapters.

No live API access, account credentials, or REAL acceptance is claimed here.
"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.market_common import MarketError, fingerprint
from wow_auction.market_http import HttpResponse
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker


SECOND = 1_000_000_000
SECRETS = {'blizzard_client_id': 'synthetic-client',
           'blizzard_client_secret': 'SYNTHETIC_PRIVATE_SECRET', 'undermine_api_key': ''}
CSV = (b'itemId,name,marketValue,minBuyout,recent,historical,updatedAt\r\n'
       b'123,Synthetic,100,80,85,90,2026-10-26T09:00:00Z\r\n')


class ScriptedClient:
    def __init__(self, clock, replies=()):
        self.clock, self.replies, self.calls = clock, list(replies), []

    def request(self, url, *, headers=None, method='GET', data=None):
        self.calls.append({'url': url, 'headers': dict(headers or {}), 'method': method, 'data': data})
        if not self.replies:
            raise AssertionError('Unexpected network request in synthetic test')
        answer = self.replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body, metadata = answer
        return HttpResponse(status, body, metadata, self.clock(), 0.01, len(body))


class MarketWorkerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='synthetic_worker_', dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'market.sqlite3'
        self.now = 2_000_000_000 * SECOND
        clock = patch('wow_auction.market_worker.time.time_ns', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.store = MarketStore(self.path)
        self.addCleanup(self.store.db.close)
        self.job = {'provider': 'tsm', 'dataset': 'commodities', 'region': 'EU',
                    'interval_seconds': 3600, 'enabled': True}
        self.config = {'region': 'EU', 'realm_slug': 'synthetic-realm',
                       'jobs': [self.job], 'allow_paid_undermine': False}
        self.client = ScriptedClient(lambda: self.now)
        self.worker = MarketWorker(self.store, self.config, client=self.client, secrets=SECRETS)
        self.key = self.store.register(self.job)

    def run_reply(self, status=200, body=CSV, headers=None, *, force=True):
        self.client.replies.append((status, body, headers or {'content-type': 'text/csv'}))
        return self.worker.run_job(self.key, self.job, force=force)

    def state(self):
        return dict(self.store.db.execute('SELECT * FROM jobs WHERE job_key=?', (self.key,)).fetchone())

    def test_success_then_304_keeps_first_content_age_and_publication(self):
        first = self.run_reply(headers={'content-type': 'text/csv', 'etag': '"test-v1"',
                                        'last-modified': 'Mon, 26 Oct 2026 09:00:00 GMT'})
        self.assertEqual(first['status'], 'accepted')
        original = self.store.current(self.key)
        self.now += 3600 * SECOND
        result = self.run_reply(304, b'', {'etag': '"test-v1"'})
        current = self.store.current(self.key)
        self.assertEqual(result['status'], 'unchanged')
        for field in ('id', 'sha256', 'received_at_ns', 'available_at_ns', 'source_snapshot_at', 'expires_at_ns'):
            self.assertEqual(current[field], original[field], field)
        self.assertEqual(self.client.calls[-1]['headers']['If-None-Match'], '"test-v1"')
        self.assertEqual(self.client.calls[-1]['headers']['If-Modified-Since'], 'Mon, 26 Oct 2026 09:00:00 GMT')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT http_status FROM attempts ORDER BY id DESC').fetchone()[0], 304)

    def test_304_without_usable_content_is_rejected(self):
        result = self.run_reply(304, b'', {})
        self.assertEqual(result['code'], 'not_modified_without_usable_content')
        self.assertIsNone(self.store.current(self.key))

    def test_429_retry_after_survives_restart_and_force_cannot_bypass_it(self):
        self.run_reply()
        original = self.store.current(self.key)
        self.now += 61 * SECOND
        result = self.run_reply(429, b'NEVER_STORE_ERROR_BODY', {'retry-after': '1800'})
        self.assertEqual(result['status'], 'rate_limited')
        due = self.state()['next_due_ns']
        self.assertEqual(due, self.now + 1800 * SECOND)
        second_store = MarketStore(self.path)
        self.addCleanup(second_store.db.close)
        second_client = ScriptedClient(lambda: self.now)
        restarted = MarketWorker(second_store, self.config, client=second_client, secrets=SECRETS)
        for _ in range(2):
            self.assertEqual(restarted.scan(force=True)['imports'][0]['status'], 'not_due_or_leased')
        self.assertEqual(second_client.calls, [])
        self.assertEqual(second_store.current(self.key)['id'], original['id'])
        self.now = due
        second_client.replies.append((200, CSV, {'content-type': 'text/csv'}))
        self.assertEqual(restarted.scan()['imports'][0]['status'], 'unchanged')
        self.assertNotIn('NEVER_STORE_ERROR_BODY', '\n'.join(second_store.db.iterdump()))

    def test_429_blocks_other_dataset_and_region_after_restart_even_with_force(self):
        second_job = {**self.job, 'dataset': 'realm_items', 'realm_slug': 'synthetic-realm', 'region': 'US'}
        self.config['jobs'] = [self.job, second_job]
        self.client.replies = [(429, b'', {'retry-after': '1800'})]
        result = self.worker.scan(force=True)
        self.assertEqual([row['status'] for row in result['imports']], ['rate_limited', 'rate_limited'])
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM rate_usage').fetchone()[0], 1)
        deadline = self.now + 1800 * SECOND
        other_store = MarketStore(self.path)
        self.addCleanup(other_store.db.close)
        other_client = ScriptedClient(lambda: self.now)
        # A new job never existed when the server cooldown was received.
        third_job = {**self.job, 'region': 'KR'}
        restarted = MarketWorker(other_store, {**self.config, 'jobs': [third_job]}, client=other_client, secrets={})
        self.assertEqual(restarted.scan(force=True)['imports'][0]['status'], 'rate_limited')
        self.assertEqual(other_client.calls, [])
        self.now = deadline - 1
        self.assertEqual(restarted.scan(force=True)['imports'][0]['status'], 'not_due_or_leased')
        self.assertEqual(other_client.calls, [])
        self.now = deadline
        other_client.replies.append((200, CSV, {'content-type': 'text/csv'}))
        self.assertEqual(restarted.scan()['imports'][0]['status'], 'accepted')
        self.assertEqual(len(other_client.calls), 1)

    def test_429_delay_starts_after_slow_response_receipt(self):
        original_request = self.client.request
        def delayed_request(*args, **kwargs):
            self.now += 35 * SECOND + 123
            return original_request(*args, **kwargs)
        with patch.object(self.client, 'request', side_effect=delayed_request):
            self.assertEqual(self.run_reply(429, b'', {'retry-after': '1800'})['status'], 'rate_limited')
        deadline = self.state()['next_due_ns']
        self.assertGreaterEqual(deadline, self.now + 1800 * SECOND)
        self.assertLess(deadline, self.now + 1801 * SECOND)
        self.assertEqual(self.store.reserve('tsm', 1, self.now), deadline)

    def test_retry_after_http_date_and_unrepresentable_deadline_are_preserved(self):
        from datetime import datetime, timezone
        from email.utils import format_datetime
        target = self.now // SECOND + 7200
        header = format_datetime(datetime.fromtimestamp(target, timezone.utc), usegmt=True)
        self.assertEqual(self.run_reply(429, b'', {'retry-after': header})['status'], 'rate_limited')
        self.assertEqual(self.state()['next_due_ns'], target * SECOND)
        self.now = target * SECOND
        result = self.run_reply(429, b'', {'retry-after': '9999999999'})
        self.assertEqual(result['status'], 'retry_deadline_unrepresentable')
        self.assertEqual(self.state()['next_due_ns'], 2**63 - 1)
        self.assertEqual(self.worker.run_job(self.key, self.job, force=True)['status'], 'not_due_or_leased')
        other_job = {**self.job, 'region': 'US'}
        result = self.worker.run_job(self.store.register(other_job), other_job, force=True)
        self.assertEqual(result['status'], 'rate_limited')
        self.assertEqual(self.store.reserve('tsm', 1, self.now), 2**63 - 1)

    def test_5xx_has_one_request_per_attempt_and_bounded_persistent_backoff(self):
        self.run_reply()
        original = self.store.current(self.key)['id']
        self.now += SECOND
        delays = []
        for _ in range(8):
            calls_before = len(self.client.calls)
            result = self.run_reply(503, b'upstream synthetic error')
            self.assertEqual(result['status'], 'retry_scheduled')
            self.assertEqual(len(self.client.calls), calls_before + 1)
            delay = (self.state()['next_due_ns'] - self.now) // SECOND
            delays.append(delay)
            self.assertEqual(self.worker.run_job(self.key, self.job, force=True)['status'], 'not_due_or_leased')
            self.now = self.state()['next_due_ns']
        self.assertEqual(delays, [60, 120, 240, 480, 960, 1920, 3600, 3600])
        self.assertEqual(self.store.current(self.key)['id'], original)

    def test_offline_does_not_remove_last_good_and_wake_records_gap(self):
        self.run_reply()
        original = self.store.current(self.key)['id']
        self.now += 3 * 3600 * SECOND
        self.client.replies.append(MarketError('market_network_unavailable'))
        result = self.worker.scan()['imports'][0]
        self.assertEqual(result['status'], 'retry_scheduled')
        self.assertEqual(self.store.current(self.key)['id'], original)
        gaps = self.store.db.execute('SELECT * FROM gaps WHERE job_key=?', (self.key,)).fetchall()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['end_ns'], self.now)
        self.assertEqual(gaps[0]['reason'], 'worker_not_observed_at_scheduled_time')

    def test_403_schema_and_partial_content_keep_last_good(self):
        self.run_reply()
        original = self.store.current(self.key)['id']
        cases = [(403, b'PRIVATE_ERROR_ECHO', {'content-type': 'application/json'}),
                 (200, b'<html>maintenance</html>', {'content-type': 'text/html'}),
                 (200, CSV + b'124,partial\r\n', {'content-type': 'text/csv'}),
                 (200, CSV.replace(b'marketValue', b'unknownColumn'), {'content-type': 'text/csv'})]
        for status, body, headers in cases:
            self.now += 61 * SECOND
            outcome = self.run_reply(status, body, headers)
            self.assertNotIn(outcome['status'], ('accepted', 'unchanged'))
            self.assertEqual(self.store.current(self.key)['id'], original)
        self.assertNotIn('PRIVATE_ERROR_ECHO', '\n'.join(self.store.db.iterdump()))

    def test_adapter_partial_batch_is_preserved_without_replacing_current(self):
        self.run_reply()
        original = self.store.current(self.key)['id']
        from wow_auction.market_tsm import normalize_tsm
        partial = normalize_tsm(CSV, region='EU', dataset='commodities')
        partial['completeness'] = 'partial'
        self.now += SECOND
        with patch('wow_auction.market_worker.market_tsm.normalize_tsm', return_value=partial):
            result = self.run_reply(body=CSV.replace(b'Synthetic', b'Different'))
        self.assertEqual(result['status'], 'partial_kept_last_good')
        self.assertEqual(self.store.current(self.key)['id'], original)
        self.assertEqual(self.store.db.execute('SELECT completeness FROM snapshots ORDER BY id DESC').fetchone()[0], 'partial')

    def test_blizzard_cannot_make_requests_even_with_inherited_credentials_or_policy_override(self):
        job = {**self.job, 'provider': 'blizzard'}
        key = self.store.register(job)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch('wow_auction.market_worker.source_policy', return_value={'network_allowed': True}):
            result = self.worker.run_job(key, job, force=True)
            self.config['jobs'] = [job]
            scanned = self.worker.scan(force=True)
        self.assertEqual(result['status'], 'blizzard_disabled_by_user')
        self.assertEqual(scanned['imports'][0]['status'], 'blizzard_disabled_by_user')
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM rate_usage').fetchone()[0], 0)
        public = json.dumps(result) + json.dumps(self.store.report([key])) + stdout.getvalue() + stderr.getvalue()
        persisted = '\n'.join(self.store.db.iterdump())
        for secret in (SECRETS['blizzard_client_id'], SECRETS['blizzard_client_secret']):
            self.assertNotIn(secret, public)
            self.assertNotIn(secret, persisted)

    def test_missing_credentials_and_policy_block_make_no_network_requests(self):
        empty = dict.fromkeys(SECRETS, '')
        worker = MarketWorker(self.store, self.config, client=self.client, secrets=empty)
        for provider, dataset, expected in [('blizzard', 'commodities', 'blizzard_disabled_by_user'),
                                             ('undermine', 'commodity_summary', 'source_policy_unresolved_api_terms_conflict')]:
            job = {**self.job, 'provider': provider, 'dataset': dataset}
            result = worker.run_job(self.store.register(job), job, force=True)
            self.assertEqual(result['code'], expected)
        self.assertEqual(self.client.calls, [])

    def test_undermine_missing_key_stays_explicit_if_its_policy_is_resolved(self):
        job = {**self.job, 'provider': 'undermine', 'dataset': 'commodity_summary'}
        policy = {'network_allowed': True, 'retention_seconds': 3600, 'status': 'synthetic_test_permission'}
        policy['fingerprint'] = fingerprint(policy)
        with patch('wow_auction.market_worker.source_policy', return_value=policy):
            result = self.worker.run_job(self.store.register(job), job, force=True)
        self.assertEqual(result['code'], 'undermine_credentials_required')
        self.assertEqual(self.client.calls, [])

    def test_paid_stats_guard_runs_before_network_even_if_policy_changes(self):
        job = {**self.job, 'provider': 'undermine', 'dataset': 'realm_stats', 'realm_slug': 'synthetic-realm'}
        policy = {'network_allowed': True, 'retention_seconds': 3600, 'status': 'synthetic_test_permission'}
        policy['fingerprint'] = fingerprint(policy)
        with patch('wow_auction.market_worker.source_policy', return_value=policy):
            result = self.worker.run_job(self.store.register(job), job, force=True)
        self.assertEqual(result['code'], 'paid_capability_not_enabled')
        self.assertEqual(self.client.calls, [])

    def test_global_budget_and_active_lease_block_another_worker(self):
        self.assertIsNone(self.store.reserve('tsm', 120, self.now))
        other_store = MarketStore(self.path)
        self.addCleanup(other_store.db.close)
        other_client = ScriptedClient(lambda: self.now)
        other_worker = MarketWorker(other_store, self.config, client=other_client, secrets=SECRETS)
        result = other_worker.scan(force=True)['imports'][0]
        self.assertEqual(result['status'], 'rate_limited')
        self.assertEqual(other_client.calls, [])
        self.now += 3600 * SECOND
        lease = self.store.claim(self.key, self.now, force=True)
        self.assertIsNotNone(lease)
        self.assertEqual(other_worker.scan(force=True)['imports'][0]['status'], 'not_due_or_leased')
        self.assertEqual(other_client.calls, [])

    def test_realm_resolution_needs_undermine_evidence_without_blizzard_network(self):
        result = self.worker.resolve_realm()
        self.assertEqual(result['status'], 'undermine_membership_evidence_required')
        self.assertFalse(result['blizzard_api_used'])
        self.assertEqual((result['region'], result['realm_slug']), ('EU', 'synthetic-realm'))
        self.assertNotIn('connected_realm_id', result)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM realm_mappings').fetchone()[0], 0)

    def test_tsm_realm_scan_works_without_any_credentials_or_numeric_realm_mapping(self):
        self.config['jobs'] = [{**self.job, 'dataset': 'realm_items', 'realm_slug': 'synthetic-realm'}]
        worker = MarketWorker(self.store, self.config, client=self.client, secrets={})
        self.client.replies = [(200, CSV, {'content-type': 'text/csv'})]
        result = worker.scan()['imports'][0]
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue(self.client.calls[0]['url'].startswith('https://public-data.tradeskillmaster.com/'))
        self.assertNotIn('Authorization', self.client.calls[0]['headers'])
        current = self.store.current(worker.jobs()[0][0])
        normalized = json.loads(current['normalized'])
        self.assertEqual(normalized['scope']['kind'], 'realm_unresolved')
        self.assertIsNone(normalized['scope']['connected_realm_id'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM realm_mappings').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
