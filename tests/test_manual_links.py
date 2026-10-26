import copy
import json
import sqlite3
import unittest

from wow_auction.ledger import HEADERS, normalize_ledger, reconcile_ledger
from wow_auction.manual_links import (
    MIGRATION_SQL, ManualLinkError, bind_link, check_endpoints, read_links,
    revoke_link, validate_links,
)


class ManualLinkTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE snapshots (id INTEGER PRIMARY KEY)')
        self.db.execute('CREATE TABLE manual_links (id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, '
                        'from_canonical_id TEXT NOT NULL, to_canonical_id TEXT NOT NULL, quantity INTEGER NOT NULL, '
                        'note TEXT NOT NULL, created_at_ns INTEGER NOT NULL, supersedes_id INTEGER REFERENCES manual_links(id))')
        for sql in MIGRATION_SQL:
            self.db.execute(sql)
        self.snapshot = 0
        self.state = []
        self.import_rows()

    def import_rows(self, buy_quantities=(2,), sale_quantity=2):
        families = {family: header for family, header in HEADERS.items()}
        families['csvBuys'] += ''.join(f'\ni:123,1,{q},50,Seller,Hero,100,Auction' for q in buy_quantities)
        families['csvSales'] += f'\ni:123,1,{sale_quantity},100,Buyer,Hero,200,Auction'
        normalized = normalize_ledger({'TradeSkillMasterDB': {
            'r@Realm@internalData@' + family: value for family, value in families.items()
        }}, account_scope='synthetic', region='EU')
        self.state = reconcile_ledger(self.state, normalized['rows'])['rows']
        self.snapshot += 1
        self.db.execute('INSERT INTO snapshots VALUES (?)', (self.snapshot,))
        return self.state

    def ids(self, quantity=2):
        buy = next(row['canonical_id'] for row in self.state
                   if row['family'] == 'csvBuys' and row['quantity'] == quantity and row['row_state'] == 'present')
        sale = next(row['canonical_id'] for row in self.state if row['family'] == 'csvSales')
        return buy, sale

    def add(self, quantity=2, *, supersedes=None, bind=True, endpoints=None):
        buy, sale = endpoints or self.ids()
        cursor = self.db.execute('INSERT INTO manual_links(source_key,from_canonical_id,to_canonical_id,quantity,note,created_at_ns,supersedes_id) '
                                 'VALUES (?,?,?,?,?,?,?)', ('s', buy, sale, quantity, 'Synthetic manual decision', 1, supersedes))
        if bind:
            bind_link(self.db, cursor.lastrowid, self.state, self.snapshot)
        return cursor.lastrowid

    def validate(self, **kwargs):
        return validate_links(self.db, 's', self.state, self.snapshot, **kwargs)

    def read(self, lid):
        return next(row for row in read_links(self.db, 's') if row['id'] == lid)

    def test_binding_and_original_link_are_immutable(self):
        lid = self.add()
        self.assertEqual(self.validate(), 1)
        row = self.read(lid)
        self.assertEqual(row['status'], 'confirmed')
        self.assertEqual(row['confirmed_quantity'], 2)
        self.assertEqual(row['binding']['endpoint_data']['from']['revision'], 1)
        with self.assertRaisesRegex(ManualLinkError, 'binding_is_immutable'):
            bind_link(self.db, lid, self.state, self.snapshot)
        self.assertEqual(self.db.execute('SELECT quantity FROM manual_links').fetchone()[0], 2)

    def test_quantity_decrease_requires_review_without_truncating_manual_quantity(self):
        lid = self.add()
        self.validate()
        original_binding = self.read(lid)['binding']
        self.import_rows((1,))
        self.validate()
        row = self.read(lid)
        self.assertEqual(row['quantity'], 2)
        self.assertEqual(row['confirmed_quantity'], 0)
        self.assertEqual(row['status'], 'requires_review')
        self.assertIn('link_endpoint_uncertain', row['reasons'])
        self.assertIn('link_exceeds_observed_quantity', row['reasons'])
        self.assertEqual(row['binding'], original_binding)
        self.assertEqual([check['status'] for check in row['validation_history']], ['confirmed', 'requires_review'])

    def test_ambiguous_matching_marks_allocation_for_review(self):
        self.state = []
        self.import_rows((1, 2), sale_quantity=3)
        lid = self.add()
        self.validate()
        self.import_rows((3,), sale_quantity=3)
        self.validate()
        row = self.read(lid)
        self.assertEqual(row['status'], 'requires_review')
        self.assertIn('link_endpoint_not_present', row['reasons'])
        self.assertIn('link_endpoint_uncertain', row['reasons'])
        self.assertEqual(row['confirmed_quantity'], 0)

    def test_missing_endpoint_requires_review_and_does_not_silently_reconfirm(self):
        lid = self.add()
        self.validate()
        prior = copy.deepcopy(self.state)
        self.state = [row for row in self.state if row['family'] != 'csvBuys']
        self.validate()
        self.assertIn('link_endpoint_missing', self.read(lid)['reasons'])
        self.state = prior
        self.validate()
        self.assertEqual(self.read(lid)['status'], 'requires_review')

    def test_scope_change_is_visible_when_canonical_ids_are_retained(self):
        lid = self.add()
        self.validate()
        for row in self.state:
            row['region'] = 'US'
            row['revision'] += 1
        self.validate(context_changed=True)
        row = self.read(lid)
        self.assertEqual(row['status'], 'requires_review')
        self.assertIn('link_endpoint_scope_changed', row['reasons'])
        self.assertIn('source_context_changed', row['reasons'])
        self.assertEqual(row['confirmed_quantity'], 0)

    def test_forced_context_review_is_sticky_even_if_endpoint_fields_did_not_change(self):
        lid = self.add()
        self.validate()
        self.assertEqual(self.validate(context_changed=True), 1)
        self.assertEqual(self.validate(), 0)
        row = self.read(lid)
        self.assertEqual(row['status'], 'requires_review')
        self.assertEqual(row['reasons'], ['source_context_changed'])

    def test_repeated_import_does_not_duplicate_validation_history(self):
        lid = self.add()
        self.validate()
        self.import_rows((1,))
        self.validate()
        checks = len(self.read(lid)['validation_history'])
        self.import_rows((1,))
        self.assertEqual(self.validate(), 0)
        self.assertEqual(len(self.read(lid)['validation_history']), checks)

    def test_aggregate_overallocation_flags_all_competing_links(self):
        self.import_rows((3,), sale_quantity=3)
        a = self.add(endpoints=self.ids(quantity=3))
        b = self.add(endpoints=self.ids(quantity=3))
        self.validate()
        for lid in (a, b):
            row = self.read(lid)
            self.assertEqual(row['status'], 'requires_review')
            self.assertIn('aggregate_allocation_exceeds_observed_quantity', row['reasons'])
            self.assertEqual(row['confirmed_quantity'], 0)

    def test_legacy_link_is_not_retroactively_given_a_verified_binding(self):
        lid = self.add(bind=False)
        self.validate()
        row = self.read(lid)
        self.assertEqual(row['status'], 'requires_review')
        self.assertIn('legacy_link_not_revision_bound', row['reasons'])
        self.assertIsNone(row['binding'])

    def test_explicit_superseding_decision_has_new_binding_and_retains_old_history(self):
        first = self.add()
        self.validate()
        self.import_rows((3,), sale_quantity=3)
        self.validate()
        second = self.add(3, supersedes=first, endpoints=self.ids(quantity=3))
        self.validate()
        old, new = self.read(first), self.read(second)
        self.assertEqual(old['status'], 'superseded')
        self.assertFalse(old['is_current'])
        self.assertEqual(old['quantity'], 2)
        self.assertEqual([x['status'] for x in old['validation_history']], ['confirmed', 'requires_review', 'superseded'])
        self.assertEqual(new['status'], 'confirmed')
        self.assertEqual(new['confirmed_quantity'], 3)
        self.assertEqual(new['supersedes_id'], first)
        self.assertNotEqual(new['binding']['endpoint_data'], old['binding']['endpoint_data'])
        self.assertEqual(self.validate(), 0)

    def test_explicit_revocation_is_append_only_and_idempotent(self):
        lid = self.add()
        self.validate()
        self.assertTrue(revoke_link(self.db, 's', lid, 'Synthetic withdrawal'))
        self.validate()
        row = self.read(lid)
        self.assertEqual(row['status'], 'revoked')
        self.assertEqual(row['confirmed_quantity'], 0)
        self.assertEqual(row['revocation']['note'], 'Synthetic withdrawal')
        self.assertEqual(row['quantity'], 2)
        self.assertFalse(revoke_link(self.db, 's', lid, 'Repeated command'))
        self.assertEqual(self.validate(), 0)
        self.assertEqual(len(row['validation_history']), 2)

    def test_invalid_endpoints_cannot_receive_new_confirmed_binding(self):
        self.import_rows((1,))
        buy = next(r['canonical_id'] for r in self.state if r['family'] == 'csvBuys')
        sale = next(r['canonical_id'] for r in self.state if r['family'] == 'csvSales')
        with self.assertRaisesRegex(ManualLinkError, 'link_endpoint_uncertain'):
            check_endpoints(self.state, buy, sale, 1)
        with self.assertRaisesRegex(ManualLinkError, 'unknown_link_endpoint'):
            check_endpoints(self.state, 'unknown', sale, 1)

    def test_different_account_or_region_cannot_confirm_new_or_legacy_link(self):
        for field, value in (('account_scope', 'different-account'), ('region', 'US')):
            with self.subTest(field=field):
                state = copy.deepcopy(self.state)
                buy, sale = self.ids()
                next(row for row in state if row['canonical_id'] == sale)[field] = value
                with self.assertRaisesRegex(ManualLinkError, 'link_endpoint_context_mismatch'):
                    check_endpoints(state, buy, sale, 1)
                lid = self.add(1, bind=False)
                validate_links(self.db, 's', state, self.snapshot)
                row = self.read(lid)
                self.assertEqual(row['status'], 'requires_review')
                self.assertIn('link_endpoint_context_mismatch', row['reasons'])
                self.assertEqual(row['confirmed_quantity'], 0)

    def test_different_realms_in_same_account_and_region_are_not_implicitly_forbidden(self):
        state = copy.deepcopy(self.state)
        buy, sale = self.ids()
        next(row for row in state if row['canonical_id'] == sale)['realm'] = 'OtherRealm'
        check_endpoints(state, buy, sale, 1)


if __name__ == '__main__':
    unittest.main()
