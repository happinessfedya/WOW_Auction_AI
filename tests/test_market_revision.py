"""SYN parser upgrades must not be hidden by an unchanged upstream hash/304."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from wow_auction.market_http import HttpResponse
from wow_auction.market_storage import MarketStore
from wow_auction.market_worker import MarketWorker

CSV = (b'itemId,name,marketValue,minBuyout,recent,historical,updatedAt\r\n'
       b'123,Synthetic,100,80,85,90,2026-10-26T09:00:00Z\r\n')


class CapturingClient:
    def __init__(self):
        self.headers = []
    def request(self, url, *, headers):
        self.headers.append(headers)
        return HttpResponse(200, CSV, {'content-type': 'text/csv', 'etag': 'synthetic'}, time.time_ns(), 0, len(CSV))


class MarketRevisionTests(unittest.TestCase):
    def test_new_parser_revision_redownloads_and_does_not_reuse_old_normalized_projection(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            with MarketStore(Path(directory) / 'market.sqlite3') as store:
                job = {'provider': 'tsm', 'dataset': 'commodities', 'region': 'EU', 'enabled': True, 'interval_seconds': 3600}
                client = CapturingClient()
                worker = MarketWorker(store, {'jobs': [job]}, client=client, secrets={'undermine_api_key': ''})
                key = store.register(job)
                with patch.object(worker, '_normalizer_revision', return_value='synthetic-v1'):
                    self.assertEqual(worker.run_job(key, job, force=True)['status'], 'accepted')
                    first = store.current(key)
                    self.assertEqual(worker.run_job(key, job, force=True)['status'], 'unchanged')
                self.assertIn('If-None-Match', client.headers[-1])
                with patch.object(worker, '_normalizer_revision', return_value='synthetic-v2'):
                    self.assertEqual(worker.run_job(key, job, force=True)['status'], 'accepted')
                self.assertNotIn('If-None-Match', client.headers[-1])
                current = store.current(key)
                self.assertNotEqual(first['id'], current['id'])
                self.assertEqual(first['sha256'], current['sha256'])
                self.assertEqual(first['source_snapshot_at'], current['source_snapshot_at'])
                self.assertGreater(current['available_at_ns'], first['available_at_ns'])
                old = store.db.execute('SELECT normalized FROM snapshots WHERE id=?', (first['id'],)).fetchone()[0]
                self.assertEqual(json.loads(old)['normalizer_revision'], 'synthetic-v1')
                self.assertEqual(json.loads(current['normalized'])['normalizer_revision'], 'synthetic-v2')


if __name__ == '__main__':
    unittest.main()
