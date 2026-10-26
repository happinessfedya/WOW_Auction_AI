from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.__main__ import main
from wow_auction.knowledge_storage import KnowledgeStore, WEEK
from wow_auction.knowledge_worker import update_sources
from tests.test_knowledge_storage import corpus, candidate
from tests.test_knowledge_craftsim import exported, fixture


class KnowledgeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'knowledge.sqlite3'

    def invoke(self, *args):
        out = StringIO(); err = StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['--config', str(self.root/'absent.json'), '--knowledge-db', str(self.path), *args])
        return code, json.loads(out.getvalue() or err.getvalue())

    def test_cli_import_status_and_pages_without_game_or_secrets(self):
        source = self.root/'corpus.json'; source.write_text(json.dumps(corpus()), encoding='utf-8')
        code, result = self.invoke('knowledge-import', str(source))
        self.assertEqual((code, result['status']), (0, 'imported'))
        code, result = self.invoke('knowledge-status')
        self.assertEqual(result['catalogs'][0]['total'], 1)
        self.assertFalse(result['recommendations_implemented'])
        code, result = self.invoke('knowledge-candidates', 'synthetic-att', '--limit', '1')
        self.assertEqual(len(result['candidates']), 1)

    def test_cli_bad_json_no_traceback(self):
        source = self.root/'bad.json'; source.write_text('{"x":1,"x":2}')
        code, result = self.invoke('knowledge-import', str(source))
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'error')

    def test_game_destination_rejected_without_configuration(self):
        self.path = self.root/'WTF'/'SavedVariables'/'knowledge.sqlite3'
        code, result = self.invoke('knowledge-status')
        self.assertEqual(code, 2)
        self.assertFalse(self.path.exists())

    def test_local_scan_without_game_or_configuration_is_nonblocking(self):
        code, result = self.invoke('knowledge-local-scan')
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'local_configuration_unavailable')
        self.assertFalse(result['game_actions_required'])

    def test_watch_polls_local_data_and_detects_build_without_manual_recipe(self):
        from unittest.mock import patch
        with patch('wow_auction.knowledge_local.detect_build', return_value='12.1.5.70575'), \
             patch('wow_auction.knowledge_local.LocalKnowledgePoller') as poller, \
             patch('wow_auction.knowledge_worker.update_sources', return_value={'status': 'not_due_or_leased'}) as update:
            poller.return_value.scan.return_value = {'status': 'local_observations_checked', 'game_actions_required': False}
            code, result = self.invoke('knowledge-watch', '--cycles', '1', '--interval', '1')
        self.assertEqual(code, 0)
        poller.return_value.scan.assert_called_once_with(build='12.1.5.70575')
        self.assertEqual(update.call_args.kwargs['build'], '12.1.5.70575')
        self.assertEqual(result['build_context']['basis'], 'local_build_info')
        self.assertFalse(result['local']['game_actions_required'])

    def test_absent_game_build_uses_explicitly_labelled_source_reference(self):
        from unittest.mock import patch
        with patch('wow_auction.knowledge_local.detect_build', return_value=None), \
             patch('wow_auction.knowledge_local.LocalKnowledgePoller') as poller, \
             patch('wow_auction.knowledge_worker.update_sources', return_value={'status': 'not_due_or_leased'}):
            poller.return_value.scan.return_value = {'status': 'local_configuration_unavailable', 'game_actions_required': False}
            code, result = self.invoke('knowledge-watch', '--cycles', '1')
        self.assertEqual(code, 0)
        self.assertFalse(result['build_context']['installed_build_known'])
        self.assertEqual(result['build_context']['basis'], 'last_known_or_source_reference')

    def test_malformed_savedvariables_returns_safe_json(self):
        source = self.root/'broken.lua'; source.write_bytes(b'WowAuctionBridgeDB = secret_function()')
        for command in ('knowledge-craftsim', 'knowledge-items'):
            with self.subTest(command=command):
                code, result = self.invoke(command, str(source), '--build', '12.1.5.70575')
                self.assertEqual(code, 2)
                self.assertEqual(result['status'], 'error')
                self.assertNotIn('secret_function', result['error_code'])

    def test_same_bridge_bytes_do_not_create_new_receive_history(self):
        source = self.root/'bridge.lua'; source.write_bytes(exported(fixture()))
        args = ('knowledge-craftsim', str(source), '--build', '12.1.5.70575')
        with patch('wow_auction.knowledge_cli.time.time', return_value=1001):
            code, first = self.invoke(*args)
        with patch('wow_auction.knowledge_cli.time.time', return_value=1002):
            code, second = self.invoke(*args)
        self.assertEqual(first['artifact_id'], second['artifact_id'])
        self.assertEqual(second['status'], 'artifact_unchanged')
        with KnowledgeStore(self.path) as store:
            rows = store.db.execute('SELECT payload,received_at FROM artifacts').fetchall()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0]['payload'])
            self.assertEqual((payload['observed_at'], rows[0]['received_at']), (1001, 1001))
        # Present-time expiry is reassessed without altering the first payload.
        with patch('wow_auction.knowledge_cli.time.time', return_value=1000+86401):
            code, expired = self.invoke(*args)
        self.assertEqual(expired['artifact_id'], first['artifact_id'])
        self.assertEqual(expired['profile_statuses_now'], {'stale': 1})

    def test_new_parser_or_build_requires_new_derived_artifact(self):
        source = self.root/'bridge.lua'; source.write_bytes(exported(fixture()))
        args = ('knowledge-craftsim', str(source), '--build', '12.1.5.70575')
        with patch('wow_auction.knowledge_cli.artifact_normalizer_revision', return_value='a'*64):
            _, first = self.invoke(*args)
        with patch('wow_auction.knowledge_cli.artifact_normalizer_revision', return_value='b'*64):
            _, parser_changed = self.invoke(*args)
            _, build_changed = self.invoke('knowledge-craftsim', str(source), '--build', '12.2.0.80000')
        self.assertEqual(len({first['artifact_id'], parser_changed['artifact_id'], build_changed['artifact_id']}), 3)

    def test_cli_reports_rejected_export_records(self):
        source = self.root/'bridge.lua'; source.write_bytes(exported({'schema_version': 1}))
        code, result = self.invoke('knowledge-craftsim', str(source), '--build', '12.1.5.70575')
        self.assertEqual(result['records'], 0)
        self.assertEqual(result['rejected_records'], 1)
        self.assertEqual(result['profile_statuses_now'], {})

    def test_worker_renews_during_import_chunks(self):
        with KnowledgeStore(self.path) as store:
            stages = []
            renew = store.renew_job
            def record_renew(*args, **kwargs):
                stages.append(store.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0])
                return renew(*args, **kwargs)
            def scan(root, *, revision, build):
                value = corpus(revision, [candidate('row:' + str(i)) for i in range(251)])
                value['source_id'] = 'att-standard'; value['build'] = build
                return value
            with patch.object(store, 'renew_job', record_renew):
                result = update_sources(store, self.root/'cache', build='12.1.5.70575', now=10,
                    fetch=lambda *args, **kwargs: {'complete': True}, scan=scan)
            self.assertEqual(result['status'], 'source_checked')
            self.assertIn(250, stages)
            self.assertIn(251, stages)

    def test_update_initial_pin_then_changed_revision_and_retry(self):
        with KnowledgeStore(self.path) as store:
            calls = []
            def fetch(root, **kwargs):
                calls.append(kwargs['revision']); return {'complete': True}
            def scan(root, *, revision, build):
                value = corpus(revision); value['source_id'] = 'att-standard'; value['build'] = build
                return value
            result = update_sources(store, self.root/'cache', build='12.1.5.70575', fetch=fetch, scan=scan)
            self.assertEqual(result['status'], 'source_checked')
            self.assertFalse(result['paid_ai_called'])
            self.assertEqual(len(calls), 1)
            # No force/due patch -> no network and no paid AI.
            result = update_sources(store, self.root/'cache', build='12.1.5.70575', fetch=fetch, scan=scan)
            self.assertEqual(result['status'], 'not_due_or_leased')
            result = update_sources(store, self.root/'cache', build='12.1.6.80000',
                                    resolve=lambda: {'revision': 'a'*40}, fetch=fetch, scan=scan)
            self.assertEqual(result['status'], 'source_checked')
            self.assertEqual(store.status()['catalogs'][0]['revision'], 'a'*40)
            result = update_sources(store, self.root/'cache', build='12.1.7.81000',
                                    resolve=lambda: {'revision': 'b'*40}, fetch=lambda *a, **k: {'complete': False}, scan=scan)
            self.assertEqual(result['status'], 'retry_scheduled')
            self.assertEqual(store.status()['catalogs'][0]['revision'], 'a'*40)


if __name__ == '__main__':
    unittest.main()
