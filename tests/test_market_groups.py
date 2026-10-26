"""SYN membership evidence: provider identity must not become a Blizzard ID."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from wow_auction.market_common import MarketError, provider_group_scope
from wow_auction.market_http import HttpResponse
from wow_auction.market_policy import source_policy
from wow_auction.market_storage import MarketStore
from wow_auction.market_undermine import normalize_undermine
from wow_auction.market_worker import MarketWorker


class ProviderMembershipTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.store = MarketStore(Path(directory.name) / 'market.sqlite3')
        self.addCleanup(self.store.db.close)
        self.config = {'region': 'EU', 'realm_slug': 'realm-one', 'jobs': [], 'allow_paid_undermine': False}
        self.worker = MarketWorker(self.store, self.config, secrets={'undermine_api_key': ''})

    def add_evidence(self, members, dataset='realm_items', *, region='EU'):
        job = {'provider': 'undermine', 'region': region, 'realm_slug': 'realm-one', 'dataset': dataset,
               'interval_seconds': 3600, 'enabled': True}
        collection = 'pets' if dataset == 'realm_pets' else 'items'
        identity = {'species': 123} if collection == 'pets' else {'item': 123}
        body = json.dumps({'request': {'region': region.lower(), 'realms': members, 'list': collection, 'detail': 'base'},
                           'result': {'lastUpdated': '2026-10-26T10:05:00Z', 'snapshot': '2026-10-26T10:00:00Z',
                                      collection: {'123': {**identity, 'price': 200, 'quantity': 1}}}}).encode()
        normalized = normalize_undermine(body, region=region, dataset=dataset, realm_slug='realm-one')
        response = HttpResponse(200, body, {}, time.time_ns(), 0.01, len(body))
        policy = {**source_policy('undermine'), 'network_allowed': True, 'retention_seconds': 3600,
                  'fingerprint': 'synthetic-test-policy-only'}
        return self.store.accept(self.store.register(job), body, normalized, response, policy)['snapshot_id']

    def test_membership_order_is_irrelevant_region_and_members_are_identity(self):
        a = provider_group_scope('EU', 'undermine', ['a', 'b'])
        self.assertEqual(a, provider_group_scope('eu', 'undermine', ['b', 'a']))
        for region, members in [('US', ['a', 'b']), ('EU', ['a', 'c'])]:
            self.assertNotEqual(a['provider_group_key'], provider_group_scope(region, 'undermine', members)['provider_group_key'])
        self.assertIsNone(a['connected_realm_id'])

    def test_missing_duplicate_and_malformed_members_rejected(self):
        for members in ([], ['a', 'a'], ['../a'], [None], 'a'):
            with self.subTest(members=members), self.assertRaises(MarketError):
                provider_group_scope('EU', 'undermine', members)

    def test_resolver_returns_retained_provenance_without_network_or_cross_provider_merge(self):
        sid = self.add_evidence(['realm-one', 'realm-two'])
        resolved = self.worker.resolve_realm()
        self.assertEqual(resolved['status'], 'provider_membership_observed')
        self.assertFalse(resolved['blizzard_api_used'])
        self.assertEqual(resolved['evidence'][0]['snapshot_id'], sid)
        self.assertEqual(resolved['cross_provider_equivalence'], 'unverified')
        self.assertEqual(len(resolved['evidence'][0]['sha256']), 64)

    def test_disagreeing_current_membership_does_not_silently_pick_one(self):
        self.add_evidence(['realm-one', 'realm-two'])
        self.add_evidence(['realm-one', 'realm-three'], 'realm_pets')
        self.assertEqual(self.worker.resolve_realm()['status'], 'provider_membership_conflict')

    def test_other_region_expired_or_missing_payload_not_membership_evidence(self):
        self.add_evidence(['realm-one'], region='US')
        sid = self.add_evidence(['realm-one'])
        self.store.db.execute('UPDATE snapshots SET expires_at_ns=0 WHERE id=?', (sid,))
        self.assertEqual(self.worker.resolve_realm()['status'], 'undermine_membership_evidence_required')
        self.store.expire()
        self.assertEqual(self.worker.resolve_realm()['status'], 'undermine_membership_evidence_required')

    def test_old_numeric_mapping_cannot_enrich_tsm_jobs(self):
        sid = self.add_evidence(['realm-one'])
        self.store.db.execute('INSERT INTO realm_mappings VALUES (?,?,?,?,?,?,?)',
                              ('EU', 'realm-one', 1, 2, sid, time.time_ns(), time.time_ns() + 10**12))
        self.config['jobs'] = [{'provider': 'tsm', 'dataset': 'realm_items', 'region': 'EU',
                               'realm_slug': 'realm-one', 'enabled': True, 'interval_seconds': 3600}]
        self.assertNotIn('connected_realm_id', self.worker.jobs()[0][1])


if __name__ == '__main__':
    unittest.main()
