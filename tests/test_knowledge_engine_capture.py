import copy
import unittest

from tests.test_knowledge_craftsim import fixture, literal
from wow_auction.knowledge_craftsim import CraftSimError, parse_bridge


def engine_fixture():
    actor = {'player_guid': 'Player-SYN-1', 'kind': 'logged_in_player_request'}
    scenario = fixture()
    scenario.update(event='WAB_AUTONOMOUS_ENGINE_CAPTURE', source_actor=actor,
        scenario={'id': 'quality_low:normal', 'kind': 'quality_low', 'concentrating': False,
                  'allocation_scope': 'required_reagents_no_optional_search'})
    record = {'recipe_id': 100, 'source_actor': actor, 'attempted_at': 1001,
        'status': 'complete', 'diagnostics': {}, 'last_good': {'captured_at': 1001, 'context_epoch': 1,
        'game_build': '12.1.5.70575', 'craftsim_version': '27.0.7',
        'scenarios': {'1': scenario}, 'scenario_count': 1, 'coverage': 'finite_standard_allocations'}}
    return {'schema_version': 1, 'exporter_version': '0.3.3', 'build': '12.1.5.70575',
        'craftsim_version': '27.0.7', 'status': 'complete', 'scope': 'available_current_actor_recipes',
        'started_at': 999, 'finished_at': 1001, 'context_epoch': 1, 'source_actor': actor,
        'queued': 1, 'processed': 1, 'cursor': 2, 'queue': {'1': 100},
        'coverage_complete': False, 'enumeration_limited': False,
        'records': {'Player-SYN-1:100': record}, 'record_sizes': {'Player-SYN-1:100': {'bytes': 5000, 'nodes': 100}},
        'storage_bytes': 5000, 'storage_nodes': 100}


def engine_export(engine=None, epoch=1, invalidation=None):
    value = {'schema_version': 1, 'records': {}, 'sequence': 0, 'dropped_records': 0,
             'context_epoch': epoch, 'engine': engine if engine is not None else engine_fixture()}
    if invalidation:
        value['last_invalidation'] = invalidation
    return ('WowAuctionBridgeDB='+literal(value)).encode()


class EngineCaptureTests(unittest.TestCase):
    def parse(self, engine=None, **kwargs):
        return parse_bridge(engine_export(engine, **kwargs), observed_at=1002, current_build='12.1.5.70575')

    def test_autonomous_collection_does_not_break_observer_ring(self):
        result = self.parse()
        self.assertEqual(result['profiles'], [])
        self.assertEqual(len(result['engine']['records'][0]['scenarios']), 1)
        self.assertEqual(result['engine']['rejected'], [])
        self.assertFalse(result['engine']['coverage_complete'])
        self.assertEqual(result['engine']['records'][0]['scenarios'][0]['capture_status'], 'model_estimate')

    def test_latest_failure_retains_original_capture_and_its_context(self):
        raw = engine_fixture()
        raw['records']['Player-SYN-1:100'].update(status='unavailable', diagnostics={'1': 'api_not_ready'})
        result = self.parse(raw, epoch=2, invalidation={'epoch': 2, 'reason': 'session_start'})['engine']['records'][0]
        self.assertTrue(result['last_good_retained'])
        self.assertEqual(result['scenarios'][0]['captured_at'], 1000)
        self.assertEqual(result['scenarios'][0]['status'], 'stale')
        self.assertEqual(result['scenarios'][0]['context_status'], 'unknown_after_session_start')

    def test_other_actor_is_preserved_as_historical(self):
        raw = engine_fixture(); raw['source_actor'] = dict(raw['source_actor'], player_guid='Player-SYN-2')
        scenario = self.parse(raw)['engine']['records'][0]['scenarios'][0]
        self.assertEqual(scenario['status'], 'stale')
        self.assertIn('current_actor_differs', scenario['reasons'])

    def test_record_actor_recipe_build_and_sequence_conflicts_rejected(self):
        for mutation in ('actor', 'recipe', 'build', 'future', 'duplicate', 'event'):
            with self.subTest(mutation=mutation):
                raw = engine_fixture(); good = raw['records']['Player-SYN-1:100']['last_good']
                scenario = good['scenarios']['1']
                if mutation == 'actor': scenario['source_actor'] = dict(scenario['source_actor'], player_guid='other')
                elif mutation == 'recipe': scenario['recipe']['recipeID'] = 200
                elif mutation == 'build': good['game_build'] = '12.2.0.1'
                elif mutation == 'future': scenario['captured_at'] = 1100
                elif mutation == 'duplicate': good['scenarios']['2'] = copy.deepcopy(scenario); good['scenario_count'] = 2
                else: scenario['event'] = 'CRAFTSIM_RECIPE_DATA_UPDATED'
                result = self.parse(raw)['engine']
                if mutation == 'event':
                    self.assertIn('ready_event_missing', result['records'][0]['scenarios'][0]['reasons'])
                else:
                    self.assertEqual(result['records'], [])
                    self.assertEqual(len(result['rejected']), 1)

    def test_malformed_envelope_fails_with_safe_diagnostic(self):
        for field, value in (('records', 1), ('queue', 'invalid'), ('coverage_complete', True), ('cursor', True),
                             ('source_actor', {}), ('storage_bytes', 9*1024*1024), ('started_at', 2000)):
            with self.subTest(field=field):
                raw = engine_fixture(); raw[field] = value
                with self.assertRaises(CraftSimError): self.parse(raw)

    def test_all_native_diagnostic_statuses_are_readable(self):
        for status in ('context_changed', 'simulation_mode_active', 'source_shape_error', 'no_available_recipe_ids', 'version_unsupported'):
            raw = engine_fixture(); raw['status'] = status
            self.assertEqual(self.parse(raw)['engine']['status'], status)

    def test_scalar_nested_scenario_isolated_from_good_other_records(self):
        raw = engine_fixture(); raw['records']['bad'] = 'invalid'
        self.assertEqual(len(self.parse(raw)['engine']['records']), 1)
        self.assertEqual(len(self.parse(raw)['engine']['rejected']), 1)


if __name__ == '__main__':
    unittest.main()
