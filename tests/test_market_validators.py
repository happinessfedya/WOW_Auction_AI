"""S2-02/S2-03 regression: validators confirm one exact retained revision."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.market_common import MarketError
from wow_auction.market_http import HttpResponse
from wow_auction.market_policy import source_policy
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker

NS = 1_000_000_000
DATE = 'Mon, 26 Oct 2026 09:00:00 GMT'


def csv(price=80):
    return ('itemId,name,marketValue,minBuyout,recent,historical,updatedAt\n'
            f'123,Synthetic,100,{price},85,90,2026-10-26T09:00:00Z\n').encode()


class Client:
    def __init__(self, owner):
        self.owner, self.replies, self.calls = owner, [], []
    def request(self, url, *, headers):
        self.calls.append(dict(headers))
        answer = self.replies.pop(0)
        if callable(answer): answer = answer()
        if isinstance(answer, Exception): raise answer
        status, body, metadata = answer
        return HttpResponse(status, body, metadata, self.owner.now, 0.01, len(body))


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'market.sqlite3'
        self.now = 1793012400 * NS
        clock = patch('wow_auction.market_worker.time.time_ns', side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)
        self.store = MarketStore(self.path)
        self.addCleanup(lambda: self.store.db.close())
        self.job = {'provider': 'tsm', 'dataset': 'commodities', 'region': 'EU', 'enabled': True, 'interval_seconds': 3600}
        self.config = {'jobs': [self.job], 'region': 'EU', 'realm_slug': 'synthetic'}
        self.client = Client(self)
        self.worker = MarketWorker(self.store, self.config, client=self.client)
        self.key = self.store.register(self.job)

    def run_reply(self, price=80, *, status=200, headers=None, body=None):
        self.now += NS
        self.client.replies.append((status, csv(price) if body is None else body, headers or {}))
        return self.worker.run_job(self.key, self.job, force=True)

    def cache(self):
        return dict(self.store.db.execute('SELECT * FROM http_validators WHERE job_key=?', (self.key,)).fetchone())

    def test_new_200_without_validators_cannot_confirm_old_representation(self):
        self.run_reply(headers={'etag': '"A"', 'last-modified': DATE})
        self.run_reply(81)
        self.assertIsNone(self.cache()['etag']); self.assertIsNone(self.cache()['last_modified'])
        current = self.store.current(self.key)
        result = self.run_reply(status=304, body=b'')
        self.assertEqual(result['code'], 'unverified_not_modified_response')
        self.assertNotIn('If-None-Match', self.client.calls[-1])
        self.assertNotIn('If-Modified-Since', self.client.calls[-1])
        self.assertEqual(self.store.current(self.key)['id'], current['id'])
        calls = len(self.client.calls)
        self.assertEqual(self.worker.run_job(self.key, self.job, force=True)['status'], 'not_due_or_leased')
        self.assertEqual(len(self.client.calls), calls)
        self.now += 60 * NS
        self.assertEqual(self.run_reply(headers={'etag': '"A"'})['status'], 'accepted')
        self.assertNotIn('If-None-Match', self.client.calls[-1])

    def test_each_missing_validator_is_cleared_even_with_identical_body(self):
        for replacement, expected in [({'etag': '"B"'}, ('"B"', None)),
                                      ({'last-modified': DATE}, (None, DATE)), ({}, (None, None))]:
            with self.subTest(replacement=replacement):
                self.run_reply(headers={'etag': '"A"', 'last-modified': DATE})
                self.run_reply(headers=replacement)
                self.assertEqual((self.cache()['etag'], self.cache()['last_modified']), expected)

    def test_etag_and_last_modified_only_each_confirm_exact_snapshot(self):
        for metadata in ({'etag': '"A"'}, {'last-modified': DATE}):
            with self.subTest(metadata=metadata):
                self.run_reply(headers=metadata)
                current = self.store.current(self.key)
                self.assertEqual(self.run_reply(status=304, headers=metadata, body=b'')['status'], 'unchanged')
                for field in ('id', 'received_at_ns', 'available_at_ns', 'expires_at_ns', 'source_snapshot_at'):
                    self.assertEqual(self.store.current(self.key)[field], current[field])

    def test_parser_change_and_unsolicited_304_requires_full_fetch(self):
        self.run_reply(headers={'etag': '"A"'})
        current = self.store.current(self.key)
        with patch.object(self.worker, '_normalizer_revision', return_value='new-parser'):
            result = self.run_reply(status=304, headers={'etag': '"A"'}, body=b'')
        self.assertEqual(result['code'], 'unverified_not_modified_response')
        self.assertNotIn('If-None-Match', self.client.calls[-1])
        self.assertEqual(self.store.current(self.key)['id'], current['id'])

    def test_conflicting_304_validator_is_not_a_success(self):
        self.run_reply(headers={'etag': '"A"'})
        result = self.run_reply(status=304, headers={'etag': '"different"'}, body=b'')
        self.assertEqual(result['code'], 'not_modified_validator_mismatch')
        self.assertEqual(result['status'], 'retry_scheduled')

    def test_schema_and_partial_failures_preserve_last_good_validators(self):
        self.run_reply(headers={'etag': '"A"', 'last-modified': DATE})
        original = self.cache()
        self.run_reply(body=b'<html>broken</html>', headers={'etag': '"bad"'})
        self.assertEqual(self.cache(), original)
        response = HttpResponse(200, csv(83), {'etag': '"partial"'}, self.now, 0, 1)
        normalized = self.worker._normalize(self.job, response)
        normalized['completeness'] = 'partial'
        self.store.accept(self.key, response.body, normalized, response, source_policy('tsm'))
        self.assertEqual(self.cache(), original)

    def test_atomic_commit_before_finish_crash_reopens_with_new_content_validators(self):
        self.run_reply(headers={'etag': '"A"', 'last-modified': DATE})
        self.now += NS
        token = self.store.claim(self.key, self.now, force=True)
        response = HttpResponse(200, csv(81), {'etag': '"B"'}, self.now, 0, 1)
        normalized = self.worker._normalize(self.job, response)
        def crash(point):
            if point == 'after_commit': raise RuntimeError('synthetic crash')
        with self.assertRaises(RuntimeError):
            self.store.accept(self.key, response.body, normalized, response, source_policy('tsm'), lease_token=token, fault=crash)
        snapshot_id = self.cache()['snapshot_id']
        self.store.db.close(); self.now += 181 * NS
        self.store = MarketStore(self.path)
        self.worker = MarketWorker(self.store, self.config, client=self.client)
        self.assertEqual(self.run_reply(status=304, body=b'', headers={'etag': '"B"'})['snapshot_id'], snapshot_id)
        self.assertEqual(self.client.calls[-1]['If-None-Match'], '"B"')
        self.assertNotIn('If-Modified-Since', self.client.calls[-1])

    def test_before_commit_crash_rolls_back_content_and_validators_together(self):
        self.run_reply(headers={'etag': '"A"'})
        before = self.cache()
        response = HttpResponse(200, csv(81), {'etag': '"B"'}, self.now, 0, 1)
        def crash(point):
            if point == 'before_commit': raise RuntimeError('synthetic crash')
        with self.assertRaises(RuntimeError):
            self.store.accept(self.key, response.body, self.worker._normalize(self.job, response), response, source_policy('tsm'), fault=crash)
        self.assertEqual(self.cache(), before)

    def test_v2_migration_uses_snapshot_headers_not_stale_job_headers(self):
        self.run_reply(81, headers={'etag': '"B"'})
        original = self.store.current(self.key)
        self.store.db.execute('DROP TABLE http_validators')
        self.store.db.execute('ALTER TABLE snapshots DROP COLUMN freshness_descriptor')
        self.store.db.execute('UPDATE jobs SET etag=?,last_modified=?', ('"A"', DATE))
        self.store.db.execute('PRAGMA user_version=2')
        self.store.db.close(); self.store = MarketStore(self.path)
        self.assertEqual(self.cache()['etag'], '"B"'); self.assertIsNone(self.cache()['last_modified'])
        self.assertEqual(self.store.current(self.key)['raw'], original['raw'])
        self.assertEqual(self.store.current(self.key)['available_at_ns'], original['available_at_ns'])
        self.assertIsNone(self.store.db.execute('SELECT etag FROM jobs').fetchone()[0])

    def test_takeover_during_304_cannot_confirm_or_finish_new_owners_snapshot(self):
        self.run_reply(headers={'etag': '"A"'})
        def takeover():
            self.now += 181 * NS
            token = self.store.claim(self.key, self.now, force=True)
            response = HttpResponse(200, csv(81), {'etag': '"B"'}, self.now, 0, 1)
            self.store.accept(self.key, response.body, self.worker._normalize(self.job, response), response,
                              source_policy('tsm'), lease_token=token)
            self.store.finish(self.key, token, health='accepted', next_due_ns=self.now + 3600 * NS)
            return 304, b'', {'etag': '"A"'}
        self.client.replies.append(takeover)
        result = self.worker.run_job(self.key, self.job, force=True)
        self.assertEqual(result['code'], 'market_worker_lease_lost')
        self.assertEqual(self.cache()['etag'], '"B"')
        self.assertEqual(self.store.db.execute('SELECT health FROM jobs').fetchone()[0], 'accepted')

    def test_takeover_during_invalid_304_cleanup_does_not_stop_queue_or_clear_new_validators(self):
        self.run_reply(headers={'etag': '"A"'})
        original = self.store.invalidate_validators
        def takeover(key, *, lease_token):
            self.now += 181 * NS
            token = self.store.claim(key, self.now, force=True)
            response = HttpResponse(200, csv(81), {'etag': '"B"'}, self.now, 0, 1)
            self.store.accept(key, response.body, self.worker._normalize(self.job, response), response,
                              source_policy('tsm'), lease_token=token)
            original(key, lease_token=lease_token)
        with patch.object(self.store, 'invalidate_validators', side_effect=takeover):
            result = self.run_reply(status=304, headers={'etag': '"wrong"'}, body=b'')
        self.assertEqual(result['code'], 'market_worker_lease_lost')
        self.assertEqual(self.cache()['etag'], '"B"')

    def test_timeout_or_expired_lease_does_not_stop_other_jobs(self):
        other = {**self.job, 'region': 'US'}
        self.config['jobs'] = [self.job, other]
        self.run_reply(headers={'etag': '"A"'})
        current = self.store.current(self.key)
        self.client.replies.extend([MarketError('market_request_deadline_exceeded'), (200, csv(), {})])
        results = self.worker.scan(force=True)['imports']
        self.assertEqual([r['status'] for r in results], ['retry_scheduled', 'accepted'])
        self.assertEqual(self.store.current(self.key)['id'], current['id'])
        self.now += 61 * NS
        def expire():
            self.now += 181 * NS
            return 200, csv(82), {'etag': '"late"'}
        self.client.replies.extend([expire, (200, csv(), {})])
        results = self.worker.scan(force=True)['imports']
        self.assertEqual(results[0]['code'], 'market_worker_lease_lost')
        self.assertIn(results[1]['status'], {'accepted', 'unchanged'})
        self.assertEqual(self.store.current(self.key)['id'], current['id'])


if __name__ == '__main__':
    unittest.main()
