"""S2-04: optional provider credentials cannot disable public data or diagnostics."""
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.__main__ import main
from wow_auction.market_config import init_market_config, load_secrets
from wow_auction.market_http import HttpResponse
from wow_auction.market_policy import source_policy
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker


CSV = (b'itemId,name,marketValue,minBuyout,recent,historical,updatedAt\r\n'
       b'123,Synthetic,100,80,85,90,2026-10-26T09:00:00Z\r\n')
UE = json.dumps({'request': {'region': 'eu', 'list': 'commodities', 'detail': 'base'},
                 'result': {'lastUpdated': '2026-10-26T09:05:00Z', 'snapshot': '2026-10-26T09:00:00Z',
                            'commodities': {'123': {'item': 123, 'price': 80, 'quantity': 2}}}}).encode()


class PublicAndUEClient:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def request(self, url, *, headers):
        self.calls.append((url, dict(headers)))
        body = CSV if 'tradeskillmaster.com' in url else UE
        content_type = 'text/csv' if body is CSV else 'application/json'
        return HttpResponse(200, body, {'content-type': content_type}, self.clock(), 0.01, len(body))


def test_allowed_policy(provider):
    policy = source_policy(provider)
    if provider == 'undermine':
        return {**policy, 'status': 'synthetic_allowed', 'network_allowed': True,
                'retention_seconds': 3600, 'fingerprint': 'SYNTHETIC_UE_POLICY'}
    return policy


class LazySecretTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='synthetic_lazy_secrets_', dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config_path = self.root / '.private' / 'market.local.json'
        self.config = init_market_config(self.config_path, region='EU', realm_slug='synthetic-realm')
        self.secret_path = Path(self.config['secrets_path'])
        self.tsm = self.config['jobs'][0]
        self.ue = self.config['jobs'][-1]
        self.config['jobs'] = [self.ue, self.tsm]
        self.now = int(datetime(2026, 10, 26, 9, 10, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
        clock = patch('wow_auction.market_worker.time.time_ns', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.store = MarketStore(self.config['database'])
        self.addCleanup(self.store.db.close)
        self.client = PublicAndUEClient(lambda: self.now)

    def test_tsm_only_or_disabled_ue_never_loads_secrets_even_with_bad_environment_or_file(self):
        for jobs in ([self.tsm], [self.ue, self.tsm]):
            for secret_content in (None, 'PRIVATE_MALFORMED_JSON', '{"undermine_api_key":123}'):
                self.now += 2_000_000_000
                if self.secret_path.exists(): self.secret_path.unlink()
                if secret_content is not None: self.secret_path.write_text(secret_content, encoding='utf-8')
                configured = {**self.config, 'jobs': jobs}
                with self.subTest(jobs=len(jobs), content=secret_content), \
                        patch.dict(os.environ, {'WOW_UNDERMINE_API_KEY': 'PRIVATE\nBROKEN'}, clear=True), \
                        patch('wow_auction.market_worker.load_secrets', side_effect=AssertionError('Unused credentials inspected')) as loader:
                    worker = MarketWorker(self.store, configured, client=self.client)
                    result = worker.scan(force=True)
                    worker.resolve_realm()
                    self.assertTrue(all(r['status'] in {'accepted', 'unchanged', 'disabled'} for r in result['imports']))
                    self.assertEqual(worker.credential_diagnostics(), {'undermine': {'status': 'not_inspected', 'configured': None}})
                    loader.assert_not_called()
        self.assertTrue(all('tradeskillmaster.com' in url and 'Authorization' not in headers
                            for url, headers in self.client.calls))

    def test_unreadable_unused_secret_file_is_not_opened(self):
        self.secret_path.write_text('PRIVATE', encoding='utf-8')
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path == self.secret_path:
                raise AssertionError('Optional secret file was opened')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded_open), \
                patch('wow_auction.market_worker.load_secrets', side_effect=PermissionError('PRIVATE_PATH')) as loader:
            worker = MarketWorker(self.store, self.config, client=self.client)
            self.assertIn(worker.scan(force=True)['imports'][1]['status'], {'accepted', 'unchanged'})
            loader.assert_not_called()

    def test_policy_blocked_enabled_ue_does_not_inspect_credentials(self):
        configured = {**self.config, 'jobs': [{**self.ue, 'enabled': True}, self.tsm]}
        with patch('wow_auction.market_worker.load_secrets', side_effect=AssertionError('Policy-blocked secret read')) as loader:
            worker = MarketWorker(self.store, configured, client=self.client)
            results = worker.scan(force=True)['imports']
            self.assertEqual(results[0]['status'], 'source_policy_unresolved_api_terms_conflict')
            self.assertEqual(results[1]['status'], 'accepted')
            loader.assert_not_called()
        self.assertEqual(len(self.client.calls), 1)
        self.assertNotIn('Authorization', self.client.calls[0][1])

    def test_allowed_ue_bad_secret_fails_only_its_job_and_can_be_fixed_on_next_request(self):
        self.secret_path.write_text('PRIVATE_BAD_FILE', encoding='utf-8')
        configured = {**self.config, 'jobs': [{**self.ue, 'enabled': True}, self.tsm]}
        with patch.dict(os.environ, {}, clear=True), patch('wow_auction.market_worker.source_policy', test_allowed_policy):
            worker = MarketWorker(self.store, configured, client=self.client)
            result = worker.scan(force=True)
            self.assertEqual(result['imports'][0]['code'], 'invalid_secret_file')
            self.assertEqual(result['imports'][1]['status'], 'accepted')
            self.assertEqual(worker.credential_diagnostics()['undermine'], {'status': 'invalid', 'configured': None})
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertEqual(len(self.client.calls), 1)
            self.secret_path.write_text(json.dumps({'undermine_api_key': 'SYNTHETIC_UE_KEY'}), encoding='utf-8')
            recovered = worker.scan(force=True)
            self.assertEqual(recovered['imports'][0]['status'], 'accepted')
            self.assertEqual(worker.credential_diagnostics()['undermine'], {'status': 'loaded', 'configured': True})
            self.assertNotIn('SYNTHETIC_UE_KEY', json.dumps(recovered))
            ue_call = next(headers for url, headers in self.client.calls if 'undermine.exchange' in url)
            self.assertEqual(ue_call['Authorization'], 'ApiKey SYNTHETIC_UE_KEY')

    def test_allowed_ue_missing_bad_environment_and_unreadable_file_are_local_job_failures(self):
        configured = {**self.config, 'jobs': [{**self.ue, 'enabled': True}, self.tsm]}
        original_open = Path.open
        def denied_secret_open(path, *args, **kwargs):
            if path == self.secret_path: raise PermissionError('PRIVATE_PATH')
            return original_open(path, *args, **kwargs)
        scenarios = [('missing', {}, 'undermine_credentials_required'),
                     ('invalid_env', {'WOW_UNDERMINE_API_KEY': 'PRIVATE\nBAD'}, 'invalid_credential_format'),
                     ('unreadable', {}, 'invalid_secret_file')]
        with patch('wow_auction.market_worker.source_policy', test_allowed_policy):
            for mode, environment, expected in scenarios:
                if self.secret_path.exists(): self.secret_path.unlink()
                if mode == 'unreadable': self.secret_path.write_text('PRIVATE_FILE', encoding='utf-8')
                with self.subTest(mode=mode), patch.dict(os.environ, environment, clear=True), \
                        patch.object(Path, 'open', denied_secret_open):
                    worker = MarketWorker(self.store, configured, client=self.client)
                    results = worker.scan(force=True)['imports']
                    self.assertEqual(results[0]['code'], expected)
                    self.assertIn(results[1]['status'], {'accepted', 'unchanged'})
                    self.assertNotIn('PRIVATE', json.dumps(results))

    def test_injected_keys_are_not_checked_until_needed_and_blizzard_values_are_ignored(self):
        secrets = {'undermine_api_key': 'PRIVATE\nINVALID', 'blizzard_client_secret': object()}
        worker = MarketWorker(self.store, self.config, client=self.client, secrets=secrets)
        self.assertEqual(worker.credential_diagnostics()['undermine']['status'], 'not_inspected')
        self.assertEqual(worker.scan(force=True)['imports'][1]['status'], 'accepted')
        self.assertEqual(worker._provider_secrets('blizzard'), {})
        self.assertEqual(worker._provider_secrets('tsm'), {})
        self.assertEqual(worker.credential_diagnostics()['undermine']['status'], 'not_inspected')

    def test_cli_status_and_resolve_never_load_or_report_secret_values(self):
        self.secret_path.write_text('PRIVATE_BROKEN_FILE', encoding='utf-8')
        configured = deepcopy(self.config)
        configured['jobs'][0]['enabled'] = True
        self.config_path.write_text(json.dumps(configured), encoding='utf-8')
        for command, expected in [('market-status', 0), ('market-resolve', 2)]:
            output, errors = io.StringIO(), io.StringIO()
            with self.subTest(command=command), \
                    patch.dict(os.environ, {'WOW_UNDERMINE_API_KEY': 'PRIVATE\nBAD'}, clear=True), \
                    patch('wow_auction.market_worker.load_secrets', side_effect=AssertionError('Diagnostic secret read')) as loader, \
                    patch('wow_auction.market_http.HttpClient.request', side_effect=AssertionError('Diagnostic network request')), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main(['--market-config', str(self.config_path), command])
            self.assertEqual(code, expected)
            self.assertEqual(errors.getvalue(), '')
            self.assertNotIn('PRIVATE', output.getvalue())
            document = json.loads(output.getvalue())
            if command == 'market-status':
                self.assertEqual(document['credentials'], {'undermine': {'status': 'not_inspected', 'configured': None}})
            loader.assert_not_called()

    def test_valid_environment_credential_does_not_read_broken_fallback_file(self):
        self.secret_path.write_text('PRIVATE_BROKEN_FILE', encoding='utf-8')
        with patch.dict(os.environ, {'WOW_UNDERMINE_API_KEY': 'SYNTHETIC_ENV_KEY'}, clear=True), \
                patch.object(Path, 'open', side_effect=AssertionError('Unneeded fallback read')):
            self.assertEqual(load_secrets(self.config), {'undermine_api_key': 'SYNTHETIC_ENV_KEY'})


if __name__ == '__main__':
    unittest.main()
