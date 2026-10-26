import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_knowledge_engine import fixture as scenario_fixture
from tests.test_knowledge_engine_capture import engine_fixture, engine_export
from wow_auction.knowledge_craftsim import parse_bridge
from wow_auction.knowledge_engine_jobs import EngineEvaluator, commodity_quotes
from wow_auction.knowledge_storage import KnowledgeStore
from wow_auction.market_common import batch, scope
from wow_auction.market_config import init_market_config
from wow_auction.market_http import HttpResponse
from wow_auction.market_policy import source_policy
from wow_auction.market_storage import MarketStore


class EngineJobTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.config = self.root/'market.json'
        init_market_config(self.config, region='EU', realm_slug='synthetic-realm')
        self.now = 1002
        clock = patch('wow_auction.market_storage.time.time_ns', side_effect=lambda: self.now*10**9)
        clock.start(); self.addCleanup(clock.stop)
        self.market = MarketStore(self.root/'market.sqlite3'); self.addCleanup(self.market.db.close)
        self.job = {'provider': 'tsm', 'dataset': 'commodities', 'region': 'EU', 'enabled': True, 'interval_seconds': 3600}
        self.key = self.market.register(self.job)
        self.knowledge = KnowledgeStore(self.root/'knowledge.sqlite3'); self.addCleanup(self.knowledge.db.close)
        engine = engine_fixture(); raw = scenario_fixture()
        original = engine['records']['Player-SYN-1:100']['last_good']['scenarios']['1']
        raw.update({name: original[name] for name in ('scenario', 'source_actor', 'event')})
        engine['records']['Player-SYN-1:100']['last_good']['scenarios']['1'] = raw
        payload = parse_bridge(engine_export(engine), observed_at=self.now, current_build='12.1.5.70575')
        aid = self.knowledge.save_artifact('craftsim', payload, now=self.now)
        self.local = {'sources': [{'kind': 'craftsim', 'artifact_id': aid, 'status': 'file_unchanged'}]}
        self.evaluator = EngineEvaluator(self.knowledge, self.config)

    def accept(self, output=100000, *, region='EU', source_time=None, row_type='aggregate'):
        prices = {200: 30000, 201: 40000, 300: output, 301: 200000}
        records = [{'record_type': row_type, 'item_id': i, 'unit_price_copper': p,
                    'quote_status': 'aggregate_minimum'} for i, p in prices.items()]
        document = batch('tsm', 'commodities', scope(region, 'commodity'), records,
                         source_snapshot_at=self.now-2 if source_time is None else source_time)
        raw = json.dumps(document).encode()
        response = HttpResponse(200, raw, {}, self.now*10**9, 0.1, len(raw))
        return self.market.accept(self.key, raw, document, response, source_policy('tsm'))

    def run_engine(self, **kwargs):
        return self.evaluator.run(self.local, now=self.now, build='12.1.5.70575', details=True, **kwargs)

    def test_automatic_price_change_recalculates_without_game_or_saved_file_change(self):
        self.accept()
        first = self.run_engine(); again = self.run_engine()
        self.assertEqual(first['evaluated_scenarios'], 1)
        self.assertTrue(first['records'][0]['recomputed'])
        self.assertFalse(again['records'][0]['recomputed'])
        old_profit = first['records'][0]['calculation']['scenarios'][0]['expected_profit_copper']
        self.now += 1; self.accept(output=200000)
        changed = self.run_engine()
        self.assertTrue(changed['records'][0]['recomputed'])
        self.assertNotEqual(changed['records'][0]['calculation']['scenarios'][0]['expected_profit_copper'], old_profit)
        self.assertEqual(self.knowledge.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 1)
        self.assertFalse(changed['market_results_persisted'])

    def test_read_only_market_does_not_migrate_schema2_or_change_database_bytes(self):
        self.accept()
        self.market.db.execute('ALTER TABLE snapshots DROP COLUMN freshness_descriptor')
        self.market.db.execute('PRAGMA user_version=2')
        path = self.root/'market.sqlite3'; before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = self.run_engine()
        self.assertEqual(result['evaluated_scenarios'], 1)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(self.market.db.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_expiry_invalidates_in_memory_values_and_keeps_history_immutable(self):
        self.accept(); self.run_engine()
        self.now += 14401
        result = self.run_engine()
        self.assertEqual(result['market_status'], 'no_eligible_market_snapshot')
        self.assertEqual(result['evaluated_scenarios'], 0)
        self.assertTrue(result['records'][0]['recomputed'])
        self.assertTrue(all(v['evaluated_count'] == 0 for v in self.evaluator._cache.values()))

    def test_source_clock_and_receive_time_cannot_refresh_old_market(self):
        self.now = 40000; self.accept(source_time=1000)
        self.assertEqual(self.run_engine()['evaluated_scenarios'], 0)

    def test_future_source_stays_untrusted_after_clock_catches_up(self):
        self.accept(source_time=1005); self.now = 1010
        self.assertEqual(self.run_engine()['evaluated_scenarios'], 0)

    def test_other_region_nonquote_and_disabled_source_do_not_supply_prices(self):
        self.accept(region='US')
        self.assertEqual(commodity_quotes(self.config, now=self.now)['items'], {})
        self.now += 1; self.accept(row_type='offer')
        self.assertEqual(commodity_quotes(self.config, now=self.now)['items'], {})
        document = json.loads(self.config.read_text())
        for job in document['jobs']: job['enabled'] = False
        self.config.write_text(json.dumps(document))
        self.assertEqual(commodity_quotes(self.config, now=self.now)['status'], 'commodity_source_disabled')

    def test_missing_live_source_does_not_use_unrelated_or_older_artifacts(self):
        self.accept(); self.run_engine()
        result = self.evaluator.run({'sources': [{'kind': 'craftsim', 'status': 'read_or_schema_error'}]}, now=self.now)
        self.assertEqual(result['status'], 'no_engine_observations')
        self.assertEqual(self.evaluator._cache, {})

    def test_context_age_reassessed_even_when_price_math_cached(self):
        self.accept(); self.run_engine()
        # The capture TTL and market TTL are separate. Build changes must affect
        # context even when the exact same price-derived math is reused.
        result = self.evaluator.run(self.local, now=self.now, build='12.2.0.1')
        self.assertFalse(result['records'][0]['recomputed'])
        self.assertIn('stale', result['records'][0]['capture_statuses_now'])
        self.assertIn('build_changed', result['records'][0]['context_statuses'])


if __name__ == '__main__':
    unittest.main()
