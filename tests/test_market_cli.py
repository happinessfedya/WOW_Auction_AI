"""CLI retention and safe diagnostics use local synthetic data only."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.__main__ import main
from wow_auction.market_common import batch, fingerprint, scope
from wow_auction.market_config import init_market_config
from wow_auction.market_http import HttpResponse
from wow_auction.market_storage import MarketStore


class MarketCLITests(unittest.TestCase):
    def test_status_and_resolve_purge_expired_content_without_network_or_secrets(self):
        with tempfile.TemporaryDirectory(prefix='synthetic_cli_', dir=Path(__file__).parent) as directory:
            root = Path(directory)
            config_path = root / '.private' / 'market.local.json'
            config = init_market_config(config_path, region='EU', realm_slug='synthetic-realm')
            now = 2_000_000_000 * 1_000_000_000
            with patch('wow_auction.market_storage.time.time_ns', side_effect=lambda: now), \
                    patch('wow_auction.market_http.HttpClient.request', side_effect=AssertionError('Diagnostic command attempted network')):
                for command, expected_code in [('market-status', 0), ('market-resolve', 2)]:
                    with self.subTest(command=command):
                        with MarketStore(config['database']) as store:
                            key = store.register(config['jobs'][0])
                            body = b'PRIVATE_RAW_RETAINED_UNTIL_EXPIRY'
                            normalized = batch('tsm', 'commodities', scope('EU', 'commodity'),
                                               [{'name': 'PRIVATE_ITEM_NAME', 'price': 123456789}],
                                               capabilities={'aggregates': True, 'depth': False})
                            policy = {'network_allowed': True, 'retention_seconds': 1, 'test_only': True}
                            policy['fingerprint'] = fingerprint(policy)
                            result = store.accept(key, body, normalized, HttpResponse(200, body, {}, now, 0.1, len(body)), policy)
                            store.reference(command, result['snapshot_id'])
                        now += 2 * 1_000_000_000
                        output, errors = io.StringIO(), io.StringIO()
                        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                            code = main(['--market-config', str(config_path), command])
                        self.assertEqual(code, expected_code)
                        self.assertEqual(errors.getvalue(), '')
                        self.assertNotIn('PRIVATE', output.getvalue())
                        self.assertNotIn('123456789', output.getvalue())
                        json.loads(output.getvalue())
                        with MarketStore(config['database']) as store:
                            current = store.current(key)
                            self.assertIsNone(current['raw'])
                            self.assertIsNone(current['normalized'])
                            self.assertFalse(current['usable'])
                            self.assertEqual(store.replay_input(command)['status'], 'replay_unavailable_due_to_retention')


if __name__ == '__main__':
    unittest.main()
