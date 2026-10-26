import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, mock_open

from wow_auction.ingestion import (ConfigError, Source, import_source, init_config,
                                    load_config, status, reject_source_destination)
from wow_auction.ledger import HEADERS, normalize_ledger, reconcile_ledger
from wow_auction.storage import Store, StorageError, restore_backup


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent
        self.directory = tempfile.TemporaryDirectory(dir=self.test_root)
        self.addCleanup(self.clean)
        self.root = Path(self.directory.name)
        self.wow = self.root / 'synthetic_retail'
        self.saved = self.wow / 'WTF/Account/synthetic/SavedVariables'
        self.saved.mkdir(parents=True)
        self.dbpath = self.root / 'private/book.sqlite3'
        self.store = Store(self.dbpath)
        self.addCleanup(self.store.close)
        self.path = self.saved / 'GX.lua'
        self.source = Source('GX', self.path, 'synthetic-account', 'EU')
        self.write_gx(100)

    def clean(self):
        self.assertEqual(self.root.resolve().parent, self.test_root)
        self.directory.cleanup()

    def write_gx(self, copper, seen=100):
        self.path.write_text('GX_DB={version=1,characters={["Hero-Realm"]={gold=' + str(copper)
                             + ',lastSeen=' + str(seen) + '}},warband={gold=0,lastSeen=100}}', encoding='utf-8')

    def ingest(self, **kwargs):
        return import_source(self.store, self.source, settle_seconds=0, **kwargs)


class IngestionTests(StorageTestCase):
    def test_delayed_savedvariables_purchase_and_repair_reconcile_without_duplicates(self):
        # Synthetic reconstruction of the observed scenario, not a WoW reload test.
        bag_path = self.saved / 'BagSync.lua'
        tsm_path = self.saved / 'TradeSkillMaster.lua'
        sources = [self.source,
                   Source('BagSync', bag_path, 'synthetic-account', 'EU'),
                   Source('TradeSkillMaster', tsm_path, 'synthetic-account', 'EU')]
        headers = {
            'csvBuys': 'itemString,stackSize,quantity,price,otherPlayer,player,time,source',
            'csvSales': 'itemString,stackSize,quantity,price,otherPlayer,player,time,source',
            'csvIncome': 'type,amount,otherPlayer,player,time',
            'csvExpense': 'type,amount,otherPlayer,player,time',
            'csvExpired': 'itemString,stackSize,quantity,player,time',
            'csvCancelled': 'itemString,stackSize,quantity,player,time',
        }

        def write_saved_files(copper, seen, rows):
            self.write_gx(copper, seen=seen)
            bag_path.write_text(
                'BagSyncDB={Realm={Hero={guid="Player-fixture-1",money=' + str(copper)
                + ',bag={},bank={}}},["warband§"]={money=0,tabs={}}}', encoding='utf-8')
            fields = [f'[{json.dumps("r@Realm@internalData@" + family)}]='
                      + json.dumps(header + ('\n' + rows[family] if family in rows else ''))
                      for family, header in headers.items()]
            tsm_path.write_text('TradeSkillMasterDB={' + ','.join(fields) + '}', encoding='utf-8')

        def scan():
            return [import_source(self.store, source, settle_seconds=0)['status'] for source in sources]

        def ledger(report):
            return next(source for source in report['sources'] if source['addon'] == 'TradeSkillMaster')

        write_saved_files(100_000, 900, {})
        self.assertEqual(scan(), ['accepted'] * 3)
        before = status(self.store, sources, details=True)
        self.assertEqual(before['assets']['observed_cash_copper'], 100_000)
        self.assertEqual(ledger(before)['ledger_rows'], 0)
        # An external reader sees no new operation until the saved bytes change.
        self.assertEqual(scan(), ['unchanged'] * 3)
        self.assertEqual(ledger(status(self.store, sources, details=True))['ledger_state'], [])

        write_saved_files(72_816, 1100, {
            'csvBuys': 'i:6532,1,1,100,SyntheticSeller,Hero,1000,Auction',
            'csvExpense': 'Repair Bill,27084,Merchant,Hero,1036',
        })
        self.assertEqual(scan(), ['accepted'] * 3)
        after = status(self.store, sources, details=True)
        self.assertEqual(after['assets']['observed_cash_copper'], 72_816)
        self.assertEqual(after['assets']['conflicts'], [])
        character = next(row for row in after['assets']['cash'] if row['owner_kind'] == 'character')
        self.assertEqual({row['source'] for row in character['alternatives']}, {'GX', 'BagSync'})
        self.assertEqual([row['copper'] for row in character['alternatives']], [72_816, 72_816])
        records = ledger(after)['normalized']['rows']
        self.assertEqual({row['family']: row['amount_copper'] for row in records},
                         {'csvBuys': 100, 'csvExpense': 27_084})
        self.assertEqual({row['family']: row['event_at'] for row in records},
                         {'csvBuys': 1000, 'csvExpense': 1036})
        self.assertEqual(before['assets']['observed_cash_copper'] - after['assets']['observed_cash_copper'],
                         sum(row['amount_copper'] for row in records))
        self.assertTrue(all(row['pnl_copper'] is None for row in records))
        self.assertEqual(ledger(after)['scope_family_count'], 6)
        changes = [json.loads(row[0]) for row in self.store.db.execute('SELECT payload FROM ledger_changes')]
        self.assertEqual(sorted(event['amount_delta_copper'] for change in changes for event in change['events']),
                         [100, 27_084])

        self.assertEqual(scan(), ['unchanged'] * 3)
        repeated = status(self.store, sources, details=True)
        self.assertEqual(repeated['assets'], after['assets'])
        self.assertEqual(ledger(repeated)['ledger_state'], ledger(after)['ledger_state'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 6)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ledger_changes').fetchone()[0], 2)

    def test_current_hash_reimport_mtime_only_no_duplicate_or_freshness_upgrade(self):
        first = self.ingest()
        current = self.store.current()[0]
        original = self.path.stat()
        os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns + 10000))
        second = self.ingest()
        self.assertEqual(second['status'], 'unchanged')
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        later = self.store.current()[0]
        self.assertEqual(current['available_at_ns'], later['available_at_ns'])
        self.assertEqual(current['file_written_at_ns'], later['file_written_at_ns'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 1)

    def test_content_a_b_a_is_new_occurrence_not_stuck_at_b(self):
        self.ingest()
        raw_a = self.path.read_bytes()
        self.write_gx(200)
        self.ingest()
        self.path.write_bytes(raw_a)
        result = self.ingest()
        self.assertEqual(result['status'], 'accepted')
        cash = self.store.current()[0]['normalized']['cash']
        self.assertEqual(next(r['copper'] for r in cash if r['owner_kind'] == 'character'), 100)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 3)

    def test_malformed_new_version_keeps_last_valid_and_safe_error(self):
        before = self.ingest()
        self.path.write_text('GX_DB={secret="PERSONAL_TEXT"', encoding='utf-8')
        failed = self.ingest()
        self.assertEqual(failed['status'], 'quarantined')
        self.assertNotIn('PERSONAL_TEXT', json.dumps(failed))
        current = self.store.current()[0]
        self.assertEqual(current['id'], before['snapshot_id'])
        self.assertEqual(current['health']['status'], 'quarantined')

    def test_valid_lua_invalid_adapter_schema_quarantines_whole_file(self):
        self.ingest()
        self.path.write_text('GX_DB={version=999,characters={}}', encoding='utf-8')
        self.assertEqual(self.ingest()['status'], 'quarantined')
        self.assertEqual(len(self.store.current()), 1)

    def test_repeated_bad_file_does_not_duplicate_quarantine_blob(self):
        self.path.write_bytes(b'GX_DB={')
        self.ingest()
        self.ingest()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(raw) FROM attempts').fetchone()[0], 1)

    def test_cli_scan_status_watch_backup_restore_without_game(self):
        original = self.path.read_bytes(), self.path.stat().st_mtime_ns
        config_path = self.root / 'private/config.local.json'
        init_config(config_path, self.wow, region='EU')
        def run(*args):
            result = subprocess.run([sys.executable, '-m', 'wow_auction', '--config', str(config_path), *args],
                                    capture_output=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        self.assertEqual(json.loads(run('scan'))['imports'][0]['status'], 'accepted')
        public = run('status')
        self.assertNotIn('Hero', public)
        self.assertEqual(json.loads(public)['assets']['characters_observed'], 1)
        runs = [json.loads(line) for line in run('watch', '--interval', '1', '--cycles', '2').splitlines()]
        self.assertEqual([r['imports'][0]['status'] for r in runs], ['unchanged', 'unchanged'])
        backup, restored = self.root / 'private/cli-backup.db', self.root / 'private/cli-restored.db'
        self.assertEqual(json.loads(run('backup', str(backup)))['status'], 'backup_verified')
        self.assertEqual(json.loads(run('restore', str(backup), str(restored)))['status'], 'restored_to_new_database')
        self.assertEqual(original, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_backdated_different_source_is_quarantined(self):
        self.ingest()
        old = self.path.stat()
        self.write_gx(200)
        os.utime(self.path, ns=(old.st_atime_ns, old.st_mtime_ns - 10000))
        result = self.ingest()
        self.assertEqual(result['error_code'], 'source_timestamp_regressed')

    def test_missing_source_preserves_last_confirmed_version(self):
        self.ingest()
        source = Source('GX', self.saved / 'missing/GX.lua', 'synthetic-account', 'EU')
        failed = import_source(self.store, source, settle_seconds=0)
        self.assertEqual(failed['status'], 'quarantined')
        self.assertEqual(len(self.store.current()), 1)

    def test_inprocess_failure_rolls_back_every_precommit_boundary(self):
        self.ingest()
        self.write_gx(200)
        for boundary in ['before_snapshot', 'after_snapshot', 'before_checkpoint', 'before_commit']:
            def fail(where):
                if where == boundary:
                    raise RuntimeError('injected')
            with self.assertRaises(RuntimeError):
                self.ingest(fault=fail)
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 1)
        self.assertEqual(self.ingest()['status'], 'accepted')

    def test_process_death_recovers_without_duplicates(self):
        self.ingest()
        self.write_gx(200)
        for boundary in ['after_snapshot', 'before_commit', 'after_commit']:
            code = (
                'import os,sys; from pathlib import Path; '
                'from wow_auction.storage import Store; '
                'from wow_auction.ingestion import Source,import_source; '
                's=Store(sys.argv[1]); '
                'import_source(s,Source("GX",Path(sys.argv[2]),"synthetic-account","EU"),'
                'settle_seconds=0,fault=lambda p: os._exit(73) if p==sys.argv[3] else None)'
            )
            completed = subprocess.run([sys.executable, '-c', code, str(self.dbpath), str(self.path), boundary],
                                       capture_output=True, timeout=20)
            self.assertEqual(completed.returncode, 73, completed.stderr.decode(errors='replace'))
            recovery_started = time.time_ns()
            with Store(self.dbpath) as recovered:
                rows = recovered.current()
                expected = 2 if boundary == 'after_commit' else 1
                self.assertEqual(recovered.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], expected)
                if boundary == 'after_commit':
                    self.assertGreaterEqual(rows[0]['available_at_ns'], recovery_started)
                    result = import_source(recovered, self.source, settle_seconds=0)
                    self.assertEqual(result['status'], 'unchanged')

    def test_availability_is_not_backdated_before_commit_delay(self):
        stamps = []
        def hook(point):
            if point == 'before_commit':
                time.sleep(0.01)
                stamps.append(time.time_ns())
        self.ingest(fault=hook)
        self.assertGreaterEqual(self.store.current()[0]['available_at_ns'], stamps[0])

    def test_concurrent_publication_waits_for_commit_before_sampling_time(self):
        self.ingest()
        self.write_gx(200)
        ready, end = self.root / 'ready', self.root / 'end'
        code = '''
import os, sys, time
from pathlib import Path
from wow_auction.storage import Store
from wow_auction.ingestion import Source, import_source
def fault(point):
    if point == 'before_commit':
        Path(sys.argv[3]).write_text('ready')
        time.sleep(0.3)
        Path(sys.argv[4]).write_text(str(time.time_ns()))
    elif point == 'after_commit':
        os._exit(73)
with Store(sys.argv[1]) as store:
    import_source(store, Source('GX',Path(sys.argv[2]),'synthetic-account','EU'),settle_seconds=0,fault=fault)
'''
        child = subprocess.Popen([sys.executable, '-c', code, str(self.dbpath), str(self.path), str(ready), str(end)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline and child.poll() is None:
                time.sleep(0.005)
            self.assertTrue(ready.exists())
            current = self.store.current()[0]
            self.assertGreaterEqual(current['available_at_ns'], int(end.read_text()))
            _, errors = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 73, errors.decode(errors='replace'))
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()

    def test_backup_restore_and_existing_destination_refused(self):
        self.ingest()
        backup = self.root / 'private/backup.sqlite3'
        self.store.backup(backup)
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        restored = self.root / 'private/restored.sqlite3'
        restore_backup(backup, restored)
        with Store(restored) as other:
            self.assertEqual(self.store.current(), other.current())
            self.assertEqual(import_source(other, self.source, settle_seconds=0)['status'], 'unchanged')
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), digest)
        with self.assertRaises(StorageError):
            self.store.backup(backup)
        with self.assertRaises(StorageError):
            restore_backup(backup, restored)

    def test_migration_1_to_3_and_checksum_tampering(self):
        path = self.root / 'private/old.sqlite3'
        with Store(path, migrate_to=1) as old:
            self.assertEqual(old.db.execute('PRAGMA user_version').fetchone()[0], 1)
        with Store(path) as upgraded:
            self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0], 3)
            upgraded.db.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=1")
        with self.assertRaisesRegex(StorageError, 'migration_history_mismatch'):
            Store(path)

    def test_foreign_or_wal_database_not_accepted(self):
        foreign = self.root / 'foreign.db'
        connection = sqlite3.connect(foreign)
        connection.execute('CREATE TABLE arbitrary (value)')
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(StorageError, 'foreign_database'):
            Store(foreign)
        wal = self.root / 'wal.db'
        connection = sqlite3.connect(wal)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.close()
        with self.assertRaisesRegex(StorageError, 'rollback_journal_required'):
            Store(wal)

    def test_config_discovery_backup_excluded_and_no_writes_to_wow(self):
        (self.saved / 'GX.lua.bak').write_bytes(self.path.read_bytes())
        private = self.root / 'private/config.local.json'
        original = self.path.read_bytes(), self.path.stat().st_mtime_ns
        init_config(private, self.wow, region='EU')
        config, sources = load_config(private)
        self.assertEqual(len(sources), 1)
        self.assertEqual(config['database'], str((private.parent / 'local.sqlite3').resolve()))
        self.assertEqual(original, (self.path.read_bytes(), self.path.stat().st_mtime_ns))
        for destination in (self.saved / 'book.db', self.saved / 'GX.lua', self.root / 'GX.lua'):
            with self.assertRaises(ConfigError):
                reject_source_destination(destination, [self.wow])

    def test_duplicate_or_inconsistent_configuration_rejected(self):
        path = self.root / 'private/config.local.json'
        config = init_config(path, self.wow, region='EU')
        config['sources'].append(config['sources'][0].copy())
        path.write_text(json.dumps(config), encoding='utf-8')
        with self.assertRaisesRegex(ConfigError, 'duplicate_source_configuration'):
            load_config(path)
        config['sources'][1].update(addon='BagSync', path=str(self.saved / 'BagSync.lua'), ownership_group='changed')
        path.write_text(json.dumps(config), encoding='utf-8')
        with self.assertRaisesRegex(ConfigError, 'inconsistent_account_policy'):
            load_config(path)

    def test_changed_config_requires_reimport_and_never_silently_relabels_history(self):
        self.ingest()
        changed = Source('GX', self.path, 'synthetic-account', 'US')
        report = status(self.store, [changed])
        self.assertEqual(report['sources'][0]['health']['status'], 'requires_reimport')
        self.assertEqual(report['accepted_sources'], 0)
        self.assertEqual(import_source(self.store, changed, settle_seconds=0)['status'], 'accepted')
        history = self.store.db.execute('SELECT normalized FROM snapshots ORDER BY id').fetchall()
        self.assertEqual([json.loads(r[0])['region'] for r in history], ['EU', 'US'])

    def test_configuration_size_limit_bounds_read_allocation(self):
        mocked = mock_open(read_data=b' ' * (1024 * 1024 + 1))
        with patch.object(Path, 'open', mocked):
            with self.assertRaisesRegex(ConfigError, 'configuration_size_limit'):
                load_config(self.root / 'oversized.local.json')
        mocked().read.assert_called_once_with(1024 * 1024 + 1)

    def test_gx_only_status_counts_characters_and_redacts(self):
        self.ingest()
        report = status(self.store, [self.source])
        self.assertEqual(report['assets']['characters_observed'], 1)
        self.assertNotIn('Hero', json.dumps(report))
        self.assertIsNone(report['real_pnl_copper'])

    def test_craftsim_nonempty_cache_identity_not_in_default_status(self):
        path = self.saved / 'CraftSim.lua'
        path.write_text('CraftSimDB={lastCraftingCostDB={version=1,data={["123"]={'
                        '["PRIVATE_CRAFTER"]={cost=42.5,timestamp=100}}}}}', encoding='utf-8')
        source = Source('CraftSim', path, 'synthetic-account', 'EU')
        self.assertEqual(import_source(self.store, source, settle_seconds=0)['status'], 'accepted')
        public = status(self.store, [source])
        self.assertNotIn('PRIVATE_CRAFTER', json.dumps(public))
        self.assertEqual(public['sources'][0]['craftsim']['cached_cost_count'], 1)
        self.assertIn('PRIVATE_CRAFTER', json.dumps(status(self.store, [source], details=True)))


class LedgerStorageTests(StorageTestCase):
    def accept_rows(self, families):
        variables = {'TradeSkillMasterDB': {'profiles': {}}}
        for family, lines in families.items():
            variables['TradeSkillMasterDB']['r@Realm@internalData@' + family] = '\n'.join([HEADERS[family]] + lines)
        normalized = normalize_ledger(variables, account_scope='synthetic-account')
        raw = json.dumps(variables, sort_keys=True).encode()
        return self.store.accept('ledger', raw, hashlib.sha256(raw).hexdigest(), normalized,
                                 time.time_ns(), time.time_ns(), reconcile=reconcile_ledger)

    def test_missing_family_retains_tombstone_not_false_reappearance(self):
        sale = 'i:123,1,1,100,Buyer,Seller,100,Auction'
        self.accept_rows({'csvSales': [sale]})
        self.accept_rows({'csvSales': []})
        self.assertEqual(self.store.current()[0]['ledger_state'][0]['row_state'], 'missing')
        self.accept_rows({'csvBuys': []})
        state = self.store.current()[0]['ledger_state']
        self.assertEqual(state[0]['row_state'], 'missing')
        last_changes = json.loads(self.store.db.execute('SELECT payload FROM ledger_changes ORDER BY id DESC').fetchone()[0])
        self.assertEqual(last_changes['revisions'], [])

    def test_manual_link_overlay_quantity_bounds_and_revisions(self):
        self.accept_rows({'csvSales': ['i:123,2,2,100,Buyer,Seller,100,Auction'],
                          'csvBuys': ['i:123,2,2,50,Seller,Buyer,90,Auction']})
        state = self.store.current()[0]['ledger_state']
        buy = next(r['canonical_id'] for r in state if r['family'] == 'csvBuys')
        sale = next(r['canonical_id'] for r in state if r['family'] == 'csvSales')
        first = self.store.add_link('ledger', buy, sale, 1, 'synthetic manual match')
        with self.assertRaisesRegex(StorageError, 'link_exceeds_observed_quantity'):
            self.store.add_link('ledger', buy, sale, 2)
        self.store.add_link('ledger', buy, sale, 2, supersedes_id=first)
        self.assertEqual(self.store.current()[0]['ledger_state'], state)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM manual_links').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
