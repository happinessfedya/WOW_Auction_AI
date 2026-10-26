"""Bounded BagSync numeric strings and whole CLI pass recovery; synthetic only."""

import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from wow_auction.assets import AssetSchemaError, normalize_assets


def normalize_item(item):
    return normalize_assets('BagSync', {'BagSyncDB': {'Realm': {'Hero': {
        'money': 100, 'bag': {0: {1: item}},
    }}}}, account_scope='synthetic', region='EU')


class NumericFieldTests(unittest.TestCase):
    def test_all_converted_string_fields_are_bounded_before_int(self):
        for number in ('9' * 5000, '9223372036854775808', '0' * 20 + '1'):
            for item, code in ((f'100;{number}', 'invalid_item_quantity'),
                               (f'{number};1', 'invalid_item_id'),
                               (f'100;1;auction={number}', 'invalid_auction_expiration')):
                with self.subTest(field=code, length=len(number)):
                    with self.assertRaises(AssetSchemaError) as caught:
                        normalize_item(item)
                    self.assertEqual(str(caught.exception), code)
                    self.assertNotIn(number, str(caught.exception))

    def test_non_ascii_digits_are_safe_schema_errors(self):
        # isdigit() accepts superscripts that int() cannot parse.
        for number in ('²', '١'):
            for item, code in ((f'100;{number}', 'invalid_item_quantity'),
                               (f'100;1;auction={number}', 'invalid_auction_expiration')):
                with self.subTest(item=item):
                    with self.assertRaisesRegex(AssetSchemaError, code):
                        normalize_item(item)

    def test_supported_boundary_and_opaque_options_keep_their_values(self):
        bound = 2**63 - 1
        batch = normalize_item(f'100;{bound};auction={bound}|custom=' + '9' * 5000)
        item = batch['items'][0]
        self.assertEqual(item['quantity'], bound)
        self.assertEqual(item['auction_expires_at'], bound)
        self.assertEqual(item['options']['custom'], '9' * 5000)
        self.assertEqual(normalize_item('10024500000;1')['items'][0]['pet']['species_id'], 245)


class NumericQuarantineCliTests(unittest.TestCase):
    def test_scan_and_watch_quarantine_both_audit_cases_and_continue(self):
        for command in ('scan', 'watch'):
            for component in ('quantity', 'item_id'):
                with self.subTest(command=command, component=component):
                    self.run_case(command, component)

    def run_case(self, command, component):
        test_root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(dir=test_root) as directory:
            root = Path(directory)
            saved = root / 'synthetic_retail/WTF/Account/synthetic/SavedVariables'
            saved.mkdir(parents=True)
            bag = saved / 'BagSync.lua'
            gx = saved / 'GX.lua'
            db = root / 'private.sqlite3'
            config = root / 'config.json'
            config.write_text(json.dumps({
                'schema_version': 1, 'wow_roots': [str(root / 'synthetic_retail')],
                'database': str(db), 'settle_seconds': 0,
                # Broken BagSync comes FIRST; GX must still be processed.
                'sources': [{'addon': name, 'path': str(path), 'account_scope': 'synthetic',
                             'region': 'EU', 'ownership_group': None}
                            for name, path in [('BagSync', bag), ('GX', gx)]],
            }), encoding='utf-8')

            def write_bag(item):
                bag.write_text('BagSyncDB={Realm={PrivateFixtureHero={money=100,bag={[0]={[1]='
                               + json.dumps(item) + '}}}}}', encoding='utf-8')

            def write_gx(money):
                gx.write_text('GX_DB={version=1,characters={["PrivateFixtureHero-Realm"]={gold='
                              + str(money) + ',lastSeen=100}}}', encoding='utf-8')

            def run(*args):
                return subprocess.run([sys.executable, '-B', '-m', 'wow_auction', '--config',
                                       str(config), *args], capture_output=True, encoding='utf-8',
                                      timeout=20)

            write_bag('100;1')
            write_gx(100)
            good_hash = hashlib.sha256(bag.read_bytes()).hexdigest()
            initial = run('scan')
            self.assertEqual(initial.returncode, 0, initial.stderr)
            with closing(sqlite3.connect(db)) as connection:
                before_id = connection.execute('SELECT c.snapshot_id FROM current_sources c '
                                               'JOIN snapshots s ON s.id=c.snapshot_id '
                                               'WHERE s.sha256=?', (good_hash,)).fetchone()[0]
            digits = '9' * 5000
            write_bag('100;' + digits if component == 'quantity' else digits + ';1')
            write_gx(101)
            expected_gx_hash = hashlib.sha256(gx.read_bytes()).hexdigest()
            outcome = run(command, '--interval', '1', '--cycles', '2') if command == 'watch' else run(command)
            self.assertEqual(outcome.returncode, 2, outcome.stderr)
            self.assertEqual(outcome.stderr, '')
            self.assertNotIn('Traceback', outcome.stdout)
            self.assertNotIn(digits, outcome.stdout)
            self.assertNotIn('PrivateFixtureHero', outcome.stdout)
            reports = [json.loads(line)['imports'] for line in outcome.stdout.splitlines()]
            self.assertEqual(len(reports), 2 if command == 'watch' else 1)
            self.assertEqual([row['status'] for row in reports[0]], ['quarantined', 'accepted'])
            code = 'invalid_item_quantity' if component == 'quantity' else 'invalid_item_id'
            self.assertEqual(reports[0][0]['error_code'], code)
            if command == 'watch':
                self.assertEqual([row['status'] for row in reports[1]], ['quarantined', 'unchanged'])
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM attempts WHERE status="quarantined"').fetchone()[0], len(reports))
                self.assertEqual(connection.execute('SELECT count(raw) FROM attempts WHERE status="quarantined"').fetchone()[0], 1)
                self.assertEqual(connection.execute('SELECT c.snapshot_id FROM current_sources c '
                                                    'JOIN snapshots s ON s.id=c.snapshot_id '
                                                    'WHERE s.sha256=?', (good_hash,)).fetchone()[0], before_id)
                self.assertEqual(connection.execute('SELECT count(*) FROM current_sources c JOIN snapshots s '
                                                    'ON s.id=c.snapshot_id WHERE s.sha256=?',
                                                    (expected_gx_hash,)).fetchone()[0], 1)
                self.assertEqual(connection.execute('SELECT count(*) FROM snapshots').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
