"""Integration regressions for audit S1-01/S1-03, with synthetic raw history."""

from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from wow_auction.ingestion import Source, import_source
from wow_auction.ledger import HEADERS, normalize_ledger, reconcile_ledger
from wow_auction.lua_data import parse_saved_variables
from wow_auction.storage import Store, StorageError, encode, restore_backup


class ReviewStorageTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent
        self.directory = tempfile.TemporaryDirectory(dir=self.test_root)
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.path = self.root / 'synthetic_retail/WTF/Account/synthetic/SavedVariables/TradeSkillMaster.lua'
        self.path.parent.mkdir(parents=True)
        self.source = Source('TradeSkillMaster', self.path, 'synthetic', None)
        self.store = Store(self.root / 'book.sqlite3')
        self.addCleanup(self.store.close)
        self.write()

    def write(self, buys=(1,), sale=None):
        families = {'csvBuys': '\n'.join(f'i:123,1,{q},100,SyntheticSeller,Hero,1000,Auction' for q in buys)}
        if sale is not None:
            families['csvSales'] = f'i:123,1,{sale},200,SyntheticBuyer,Hero,2000,Auction'
        data = {'r@Realm@internalData@' + family: header + ('\n' + families[family] if families.get(family) else '')
                for family, header in HEADERS.items()}
        self.path.write_text('TradeSkillMasterDB={' + ','.join(
            '[' + json.dumps(k) + ']=' + json.dumps(v) for k, v in data.items()) + '}', encoding='utf-8')

    def ingest(self, region):
        return import_source(self.store, replace(self.source, region=region), settle_seconds=0)

    def current(self):
        return self.store.current()[0]

    def history(self):
        return [tuple(r) for r in self.store.db.execute('SELECT * FROM snapshots ORDER BY id')]

    def events(self, effective=False):
        table = 'effective_ledger_changes' if effective else 'ledger_changes'
        return [event for r in self.store.db.execute('SELECT payload FROM ' + table + ' ORDER BY id')
                for event in json.loads(r[0])['events']]

    def checks(self):
        return self.store.db.execute('SELECT count(*) FROM manual_link_checks').fetchone()[0]

    def link(self, quantity=2):
        rows = self.current()['ledger_state']
        buy = next(r for r in rows if r['family'] == 'csvBuys' and r['quantity'] == quantity)
        sale = next(r for r in rows if r['family'] == 'csvSales')
        return self.store.add_link(self.source.key, buy['canonical_id'], sale['canonical_id'], quantity,
                                   'synthetic decision')

    def rebuild(self, region='EU', name='before-rebuild.sqlite3', fault=None):
        source = replace(self.source, region=region)
        return self.store.rebuild_ledger_context(source.key, account_scope=source.account_scope,
                                                region=region, adapter_version=source.adapter_version,
                                                backup_path=self.root / name,
                                                reason='Synthetic explicit correction of source context', fault=fault)

    def test_same_bytes_unknown_region_completion_keeps_one_event_and_canonical_id(self):
        self.assertEqual(self.ingest(None)['status'], 'accepted')
        original = self.current()['ledger_state'][0]
        history = self.history()
        outcome = self.ingest('EU')
        self.assertEqual(outcome['ledger_context_status'], 'confirmed')
        rows = self.current()['ledger_state']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['region'], rows[0]['row_state'], rows[0]['uncertain']), ('EU', 'present', False))
        self.assertEqual(rows[0]['canonical_id'], original['canonical_id'])
        self.assertGreater(rows[0]['revision'], original['revision'])
        self.assertEqual([e['amount_delta_copper'] for e in self.events()], [100])
        self.assertEqual(self.history()[:len(history)], history)
        snapshots = len(self.history())
        self.assertEqual(self.ingest('EU')['status'], 'unchanged')
        self.assertEqual(self.current()['ledger_state'], rows)
        self.assertEqual(len(self.history()), snapshots)

    def test_known_region_change_and_return_need_resolution_without_duplicate_events(self):
        self.ingest('EU')
        original_history = self.history()
        for region in ('US', 'EU'):
            outcome = self.ingest(region)
            self.assertEqual(outcome['ledger_context_status'], 'requires_resolution')
            rows = self.current()['ledger_state']
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['region'], region)
            self.assertEqual(rows[0]['row_state'], 'context_conflict')
            self.assertTrue(rows[0]['uncertain'])
            batches = self.store.db.execute('SELECT count(*) FROM ledger_changes').fetchone()[0]
            self.assertEqual(self.ingest(region)['status'], 'unchanged')
            self.assertEqual(self.current()['ledger_state'], rows)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM ledger_changes').fetchone()[0], batches)
        self.assertEqual([e['amount_delta_copper'] for e in self.events()], [100])
        self.assertEqual(self.history()[:len(original_history)], original_history)

    def test_link_decrease_requires_review_preserves_decision_and_repeat_is_idempotent(self):
        self.write(buys=(2,), sale=2)
        self.ingest('EU')
        link_id = self.link()
        before = self.current()['manual_links'][0]
        self.assertTrue(before['confirmed'])
        self.write(buys=(1,), sale=2)
        self.ingest('EU')
        after = self.current()['manual_links'][0]
        self.assertEqual(after['id'], link_id)
        self.assertEqual(after['quantity'], 2)
        self.assertEqual(after['binding'], before['binding'])
        self.assertEqual(after['status'], 'requires_review')
        self.assertFalse(after['confirmed'])
        self.assertEqual(after['confirmed_quantity'], 0)
        self.assertIn('link_endpoint_uncertain', after['reasons'])
        self.assertIn('link_exceeds_observed_quantity', after['reasons'])
        count = self.checks()
        self.ingest('EU')
        self.assertEqual(self.checks(), count)
        rows = self.current()['ledger_state']
        buy = next(r for r in rows if r['family'] == 'csvBuys')
        sale = next(r for r in rows if r['family'] == 'csvSales')
        with self.assertRaises(StorageError):
            self.store.add_link(self.source.key, buy['canonical_id'], sale['canonical_id'], 1)
        self.store.revoke_link(self.source.key, link_id, 'synthetic withdrawal')
        revoked = self.current()['manual_links'][0]
        self.assertEqual(revoked['status'], 'revoked')
        self.assertEqual(revoked['revocation']['note'], 'synthetic withdrawal')
        self.assertEqual(revoked['quantity'], 2)
        count = self.checks()
        self.store.revoke_link(self.source.key, link_id, 'duplicate request')
        self.assertEqual(self.checks(), count)

    def test_ambiguous_matching_invalidates_dependent_allocation_without_trimming_it(self):
        self.write(buys=(2, 3), sale=2)
        self.ingest('EU')
        self.link()
        self.write(buys=(3, 4), sale=2)
        self.ingest('EU')
        link = self.current()['manual_links'][0]
        self.assertEqual((link['quantity'], link['confirmed_quantity'], link['status']), (2, 0, 'requires_review'))
        self.assertIn('link_endpoint_uncertain', link['reasons'])
        self.assertIn('link_endpoint_not_present', link['reasons'])
        count = self.checks()
        self.ingest('EU')
        self.assertEqual(self.checks(), count)

    def test_scope_completion_reviews_overlay_and_explicit_supersession_preserves_history(self):
        self.write(buys=(2,), sale=2)
        self.ingest(None)
        old_id = self.link()
        self.ingest('EU')
        old = self.current()['manual_links'][0]
        self.assertEqual(old['status'], 'requires_review')
        self.assertIn('source_context_changed', old['reasons'])
        self.assertIn('link_endpoint_scope_changed', old['reasons'])
        count = self.checks()
        self.ingest('EU')
        self.assertEqual(self.checks(), count)
        new_id = self.store.add_link(self.source.key, old['from_canonical_id'], old['to_canonical_id'], 2,
                                     'reviewed in corrected context', supersedes_id=old_id)
        links = {r['id']: r for r in self.current()['manual_links']}
        self.assertEqual(links[old_id]['status'], 'superseded')
        self.assertTrue(links[new_id]['confirmed'])
        self.assertEqual(links[new_id]['supersedes_id'], old_id)
        self.ingest('US')
        links = {r['id']: r for r in self.current()['manual_links']}
        self.assertEqual(links[new_id]['status'], 'requires_review')
        self.assertFalse(links[new_id]['confirmed'])

    def inject_legacy_duplicate(self):
        """Persist exactly the former mixed-region bug as historical input."""
        self.ingest(None)
        prior = self.current()['ledger_state']
        raw = self.path.read_bytes()
        new = normalize_ledger(parse_saved_variables(raw), account_scope='synthetic', region='EU')
        change = reconcile_ledger([], new['rows'])
        change['rows'].extend(prior)
        source = replace(self.source, region='EU')
        stamp = time.time_ns()
        db = self.store.db
        db.execute('BEGIN IMMEDIATE')
        sid = db.execute('INSERT INTO snapshots(source_key,sha256,adapter_version,raw,normalized,received_at_ns,'
                         'available_at_ns,file_written_at_ns) VALUES (?,?,?,?,?,?,?,?)',
                         (source.key, hashlib.sha256(raw).hexdigest(), source.adapter_version, raw,
                          encode(new), stamp, stamp, self.path.stat().st_mtime_ns)).lastrowid
        db.execute('INSERT INTO ledger_changes(snapshot_id,payload) VALUES (?,?)', (sid, encode(change)))
        db.execute('UPDATE current_sources SET snapshot_id=?,ledger_state=? WHERE source_key=?',
                   (sid, encode(change['rows']), source.key))
        db.commit()

    def test_legacy_duplicate_rebuild_retains_history_and_backup_but_resets_effective_stream(self):
        self.inject_legacy_duplicate()
        self.assertEqual(len(self.current()['ledger_state']), 2)
        self.assertEqual(self.current()['ledger_context_status'], 'requires_resolution')
        self.assertEqual([e['amount_delta_copper'] for e in self.events()], [100, 100])
        # Even the unchanged-byte path must detect legacy contamination.
        self.assertEqual(self.ingest('EU')['ledger_context_status'], 'requires_resolution')
        self.assertEqual(len(self.current()['ledger_state']), 1)
        history, events = self.history(), self.events()
        source_before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
        outcome = self.rebuild()
        self.assertEqual((outcome['status'], outcome['new_financial_events']), ('context_rebuilt', 0))
        rows = self.current()['ledger_state']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['region'], rows[0]['quantity'], rows[0]['row_state'], rows[0]['uncertain']),
                         ('EU', 1, 'present', False))
        self.assertEqual(self.events(), events)
        self.assertEqual(self.events(effective=True), [])
        self.assertEqual(self.history()[:len(history)], history)
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), source_before)
        self.assertEqual(self.ingest('EU')['status'], 'unchanged')
        with closing(sqlite3.connect(self.root / 'before-rebuild.sqlite3')) as backup:
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(backup.execute('SELECT count(*) FROM snapshots').fetchone()[0], len(history))
        self.store.backup(self.root / 'after-rebuild.sqlite3')
        restore_backup(self.root / 'after-rebuild.sqlite3', self.root / 'restored.sqlite3')
        with Store(self.root / 'restored.sqlite3') as restored:
            self.assertEqual(restored.current()[0]['ledger_state'], rows)
            self.assertEqual(import_source(restored, replace(self.source, region='EU'), settle_seconds=0)['status'], 'unchanged')
            self.assertEqual(restored.db.execute('SELECT count(*) FROM ledger_context_resolutions').fetchone()[0], 1)
            self.assertEqual([event for r in restored.db.execute('SELECT payload FROM effective_ledger_changes')
                              for event in json.loads(r[0])['events']], [])

    def test_rebuild_failure_rolls_back_projection_and_resolution_with_backup_preserved(self):
        self.ingest('EU')
        self.ingest('US')
        history, current, events = self.history(), self.current(), self.events()

        def fault(point):
            if point == 'before_commit':
                raise RuntimeError('synthetic rebuild failure')

        with self.assertRaisesRegex(RuntimeError, 'synthetic rebuild failure'):
            self.rebuild(fault=fault)
        self.assertEqual(self.history(), history)
        self.assertEqual(self.current(), current)
        self.assertEqual(self.events(), events)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM ledger_context_resolutions').fetchone()[0], 0)
        self.assertTrue((self.root / 'before-rebuild.sqlite3').is_file())
        self.assertEqual(self.rebuild(name='retry-backup.sqlite3')['status'], 'context_rebuilt')

    def test_schema_two_legacy_link_migrates_to_review_without_rewriting_source_history(self):
        self.write(buys=(2,), sale=2)
        raw = self.path.read_bytes()
        source = replace(self.source, region='EU')
        batch = normalize_ledger(parse_saved_variables(raw), account_scope='synthetic', region='EU')
        changes = reconcile_ledger([], batch['rows'])
        buy = next(r['canonical_id'] for r in changes['rows'] if r['family'] == 'csvBuys')
        sale = next(r['canonical_id'] for r in changes['rows'] if r['family'] == 'csvSales')
        legacy_path = self.root / 'legacy-schema-two.sqlite3'
        with Store(legacy_path, migrate_to=2) as legacy:
            stamp = time.time_ns()
            sid = legacy.db.execute('INSERT INTO snapshots(source_key,sha256,adapter_version,raw,normalized,'
                                    'received_at_ns,available_at_ns,file_written_at_ns) VALUES (?,?,?,?,?,?,?,?)',
                                    (source.key, hashlib.sha256(raw).hexdigest(), source.adapter_version, raw,
                                     encode(batch), stamp, stamp, self.path.stat().st_mtime_ns)).lastrowid
            legacy.db.execute('INSERT INTO ledger_changes(snapshot_id,payload) VALUES (?,?)', (sid, encode(changes)))
            legacy.db.execute('INSERT INTO current_sources VALUES (?,?,?)', (source.key, sid, encode(changes['rows'])))
            legacy.db.execute('INSERT INTO manual_links(source_key,from_canonical_id,to_canonical_id,quantity,'
                              'note,created_at_ns) VALUES (?,?,?,?,?,?)',
                              (source.key, buy, sale, 2, 'legacy synthetic decision', stamp))
            before = tuple(legacy.db.execute('SELECT * FROM snapshots').fetchone())
            original_link = tuple(legacy.db.execute('SELECT * FROM manual_links').fetchone())
        with Store(legacy_path) as migrated:
            self.assertEqual(migrated.db.execute('PRAGMA user_version').fetchone()[0], 3)
            link = migrated.current()[0]['manual_links'][0]
            self.assertEqual(link['status'], 'requires_review')
            self.assertFalse(link['confirmed'])
            self.assertIsNone(link['binding'])
            self.assertEqual(import_source(migrated, source, settle_seconds=0)['status'], 'unchanged')
            link = migrated.current()[0]['manual_links'][0]
            self.assertIn('legacy_link_not_revision_bound', link['reasons'])
            self.assertEqual(tuple(migrated.db.execute('SELECT * FROM snapshots').fetchone()), before)
            self.assertEqual(tuple(migrated.db.execute('SELECT * FROM manual_links').fetchone()), original_link)
            self.assertEqual(migrated.db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 1)

    def test_cli_context_rebuild_and_unlink_keep_history_and_private_diagnostics_redacted(self):
        self.write(buys=(2,), sale=2)
        self.ingest('EU')
        lid = self.link()
        self.ingest('US')
        config = self.root / 'config.json'
        source = replace(self.source, region='EU')
        config.write_text(json.dumps({
            'schema_version': 1, 'wow_roots': [str(self.path.parents[4])],
            'database': str(self.store.path), 'settle_seconds': 0,
            'sources': [{'addon': source.addon, 'path': str(source.path),
                         'account_scope': source.account_scope, 'region': source.region,
                         'ownership_group': source.ownership_group}],
        }), encoding='utf-8')
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)

        def run(*args):
            result = subprocess.run([sys.executable, '-B', '-m', 'wow_auction', '--config', str(config), *args],
                                    capture_output=True, encoding='utf-8', timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            for private in ('SyntheticSeller', 'SyntheticBuyer', 'Hero', 'synthetic decision'):
                self.assertNotIn(private, result.stdout)
            return json.loads(result.stdout)

        rebuilt = run('rebuild-ledger-context', source.key, '--backup', str(self.root / 'cli-before.sqlite3'),
                      '--reason', 'synthetic CLI context correction')
        self.assertEqual(rebuilt['status'], 'context_rebuilt')
        self.assertEqual(rebuilt['new_financial_events'], 0)
        self.assertEqual(self.current()['manual_links'][0]['status'], 'requires_review')
        self.assertEqual(run('unlink', source.key, str(lid), '--note', 'synthetic withdrawal')['status'], 'manual_overlay_revoked')
        checks = self.checks()
        run('unlink', source.key, str(lid), '--note', 'duplicate request')
        self.assertEqual(self.checks(), checks)
        self.assertEqual(self.current()['manual_links'][0]['status'], 'revoked')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM manual_links').fetchone()[0], 1)
        self.assertEqual(run('scan')['imports'][0]['status'], 'unchanged')
        run('status')
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)


if __name__ == '__main__':
    unittest.main()
