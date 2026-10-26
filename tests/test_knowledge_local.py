import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.knowledge import KnowledgeError
from wow_auction.knowledge_local import (LocalKnowledgePoller, detect_build,
    normalize_craftsim_cache, normalize_tsm_crafting_cache)
from wow_auction.knowledge_storage import KnowledgeStore
from tests.test_knowledge_craftsim import literal, exported, passive_fixture


class LocalKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.wow = self.root/'WoW'/'_retail_'; self.wow.mkdir(parents=True)
        self.saved = self.wow/'SavedVariables'; self.saved.mkdir()
        self.build = self.wow.parent/'.build.info'
        self.build.write_text('Active!DEC:1|Product!STRING:0|Version!STRING:0\n1|wow|12.1.5.70575\n')
        self.config = self.root/'config.json'
        self.config.write_text(json.dumps({'schema_version': 1, 'database': str(self.root/'unused-assets.sqlite3'),
            'wow_roots': [str(self.wow)], 'settle_seconds': 0,
            'sources': [{'addon': addon, 'path': str(self.saved/(addon+'.lua')), 'account_scope': 'synthetic',
                         'region': 'EU', 'ownership_group': None} for addon in ('TradeSkillMaster','CraftSim')]}))
        self.tsm = {'TradeSkillMasterDB': {'f@SYNTHETIC@internalData@crafts': {'c:1': {
            'mats': {'i:2': 5, 'q:1:3,4': 2, 'f:2:5,6': 1}, 'itemString': 'i:7', 'numResult': 2,
            'hasCD': False, 'rootCategoryId': 1, 'players': {'SyntheticPlayer': True}}}},
            'TSMItemInfoDB': {'versionStr': '19,enUS,12.1.5,70575', 'data': 'A'*24, 'names': 'Synthetic item', 'itemStrings': 'i:7'}}
        self.cs = {'CraftSimDB': {'crafterDB': {'version': 7, 'data': {'SyntheticPlayer': {
            'recipeInfos': {1: {'recipeID': 1}}, 'operationInfos': {1: {'baseSkill': 100}},
            'cachedRecipeIDs': {1: {1: 1}}, 'professionGear': {1: {}}, 'cooldownData': {}}}},
            'lastCraftingCostDB': {'version': 1, 'data': {}},
            'itemRecipeDB': {'version': 4, 'data': {7: {'recipeID': 1, 'itemID': 7}}}}}
        self.write('TradeSkillMaster', self.tsm); self.write('CraftSim', self.cs)
        (self.saved/'WowAuctionBridge.lua').write_bytes(exported())
        self.store = KnowledgeStore(self.root/'knowledge.sqlite3'); self.addCleanup(self.store.db.close)

    def write(self, name, variables):
        (self.saved/(name+'.lua')).write_text('\n'.join(k+'='+literal(v) for k,v in variables.items()), encoding='utf-8')

    def test_detect_installed_build_and_missing_or_conflicting_build(self):
        self.assertEqual(detect_build(self.config), '12.1.5.70575')
        self.build.write_text('Active!DEC:1|Product!STRING:0|Version!STRING:0\n1|wow|12.1.5.70575\n1|wow|12.2.0.80000\n')
        self.assertIsNone(detect_build(self.config))
        self.build.unlink()
        self.assertIsNone(detect_build(self.config))

    def test_cache_claims_are_not_fresh_engine_or_physical_output(self):
        cs = normalize_craftsim_cache(self.cs, source_sha256='a'*64, build='12.1.5.70575')
        self.assertEqual(cs['summary_counts']['operationInfos'], 1)
        self.assertEqual(cs['summary_counts']['cached_recipe_list_entries'], 1)
        self.assertFalse(cs['engine_evaluation_available'])
        self.assertIsNone(cs['observed_game_build'])
        self.assertNotIn('SyntheticPlayer', cs['repositories']['crafterDB']['data'])
        tsm = normalize_tsm_crafting_cache(self.tsm, source_sha256='a'*64)
        self.assertEqual(tsm['record_count'], 1)
        claim = tsm['recipes'][0]
        self.assertEqual(claim['fields']['mats']['q:1:3,4'], 2)
        self.assertEqual(claim['fields']['numResult'], 2)
        self.assertIsNone(claim['joint_outcome_pmf'])
        self.assertFalse(claim['execution_eligible'])
        self.assertIn('midpoint', claim['output_quantity_semantics'])

    def test_unknown_repository_version_is_preserved_as_unsupported(self):
        self.cs['CraftSimDB']['crafterDB']['version'] = 999
        result = normalize_craftsim_cache(self.cs, source_sha256='a'*64)
        self.assertEqual(result['repositories']['crafterDB']['status'], 'unsupported_schema')
        self.assertNotIn('data', result['repositories']['crafterDB'])

    def test_poll_without_game_or_network_imports_and_restarts_idempotently(self):
        before = {path: path.read_bytes() for path in self.saved.iterdir()}
        poller = LocalKnowledgePoller(self.store, self.config)
        result = poller.scan(now=1000)
        self.assertEqual(len(result['sources']), 4)
        self.assertTrue(all(row['status'] == 'artifact_stored' for row in result['sources']))
        self.assertFalse(result['game_actions_required'])
        again = LocalKnowledgePoller(self.store, self.config).scan(now=1001)
        self.assertTrue(all(row['status'] == 'artifact_unchanged' for row in again['sources']))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 4)
        self.assertEqual(before, {path: path.read_bytes() for path in self.saved.iterdir()})
        self.assertFalse((self.root/'unused-assets.sqlite3').exists())

    def test_unchanged_mtime_skips_reads_then_periodically_rechecks_hash(self):
        poller = LocalKnowledgePoller(self.store, self.config, build='12.1.5.70575', verify_interval=10)
        poller.scan(now=1000)
        with patch('wow_auction.knowledge_local.read_stable', side_effect=AssertionError('unnecessary read')):
            result = poller.scan(now=1001)
        self.assertTrue(all(row['status'] == 'file_unchanged' for row in result['sources']))
        result = poller.scan(now=1011)
        self.assertTrue(all(row['status'] == 'artifact_unchanged' for row in result['sources']))

    def test_changed_local_bytes_are_imported_automatically(self):
        poller = LocalKnowledgePoller(self.store, self.config)
        poller.scan(now=1000)
        self.tsm['TradeSkillMasterDB']['f@SYNTHETIC@internalData@crafts']['c:1']['numResult'] = 3
        self.write('TradeSkillMaster', self.tsm)
        result = poller.scan(now=1001)
        self.assertEqual([row['status'] for row in result['sources'][:2]], ['artifact_stored', 'artifact_stored'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 6)

    def test_bad_one_source_does_not_block_other_sources_or_erase_history(self):
        poller = LocalKnowledgePoller(self.store, self.config)
        poller.scan(now=1000)
        (self.saved/'CraftSim.lua').write_text('CraftSimDB=secret_function()')
        (self.saved/'WowAuctionBridge.lua').unlink()
        result = poller.scan(now=1001)
        by_kind = {row['kind']:row for row in result['sources']}
        self.assertEqual(by_kind['craftsim_cache']['status'], 'read_or_schema_error')
        self.assertEqual(by_kind['craftsim']['status'], 'missing_data')
        self.assertEqual(by_kind['item_metadata']['status'], 'file_unchanged')
        self.assertTrue(by_kind['craftsim_cache']['last_good_retained'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 4)

    def test_malformed_passive_nested_types_do_not_stop_other_changed_imports(self):
        poller = LocalKnowledgePoller(self.store, self.config, verify_interval=1)
        initial = poller.scan(now=1001)
        bridge_id = next(row['artifact_id'] for row in initial['sources'] if row['kind'] == 'craftsim')
        for index, (field, value) in enumerate((('recipe_info', 1), ('recipe_info', False),
                                               ('last_good', 1), ('last_good', False)), start=1):
            with self.subTest(field=field, value=value):
                passive = passive_fixture()
                passive['records'][100][field] = value
                self.write('WowAuctionBridge', {'WowAuctionBridgeDB': {
                    'schema_version': 1, 'records': {}, 'sequence': 0,
                    'context_epoch': 1, 'dropped_records': 0, 'passive': passive}})
                self.tsm['TradeSkillMasterDB']['f@SYNTHETIC@internalData@crafts']['c:1']['numResult'] = index + 2
                self.write('TradeSkillMaster', self.tsm)
                result = poller.scan(now=1001 + index)
                by_kind = {row['kind']: row for row in result['sources']}
                self.assertEqual(by_kind['craftsim']['status'], 'read_or_schema_error')
                self.assertTrue(by_kind['craftsim']['last_good_retained'])
                self.assertFalse(by_kind['craftsim']['history_removed'])
                self.assertEqual(by_kind['item_metadata']['status'], 'artifact_stored')
                self.assertEqual(by_kind['tsm_crafting_cache']['status'], 'artifact_stored')
                self.assertEqual(self.store.db.execute('SELECT id FROM artifacts WHERE kind = ?',
                                                      ('craftsim',)).fetchall()[0]['id'], bridge_id)
                self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 4 + index * 2)

    def test_no_configuration_does_not_require_user_game_actions(self):
        result = LocalKnowledgePoller(self.store, self.root/'absent.json').scan(now=1000)
        self.assertEqual(result['status'], 'local_configuration_unavailable')
        self.assertFalse(result['game_actions_required'])


if __name__ == '__main__':
    unittest.main()
