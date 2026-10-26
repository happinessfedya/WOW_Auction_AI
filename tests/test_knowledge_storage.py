"""SYN: resumable catalogs and evidence history, not real game mechanics."""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.knowledge import (KnowledgeError, SEMANTIC_REVISION, digest, encode,
    semantic_digest, validate_mechanic, mechanic_readiness)
from wow_auction.knowledge_storage import KnowledgeStore, WEEK


def candidate(key='recipe:1', **changes):
    return {'key': key, 'family': 'recipe', 'status': 'candidate',
            'source': {'path': 'Vanilla/recipes.lua', 'line': 1, 'sha256': 'a'*64},
            'claim': {'recipe_id': 1}, 'unknowns': ['reagents', 'current_applicability'], **changes}


def corpus(revision='pin1', candidates=None):
    return {'source_id': 'synthetic-att', 'revision': revision, 'parser_revision': 'test1',
            'build': '12.1.5.70575', 'complete': True,
            'files': [{'path': 'Vanilla/recipes.lua', 'sha256': 'a'*64, 'disposition': 'normalized'},
                      {'path': 'Legion/new-unknown-family.lua', 'sha256': 'b'*64, 'disposition': 'unsupported'}],
            'candidates': [candidate()] if candidates is None else candidates,
            'coverage': {'whole_game': 'unknown'}}


def located_candidate():
    """Real extractor shape, including the nested locators missed by v1."""
    from wow_auction.knowledge_att import extract_file
    rows = extract_file(b'n(10, { i(2, { i(3) }) })', 'Vanilla/recipes.lua')['candidates']
    result = next(row for row in rows if row['claim']['entity_id'] == 2)
    result['claim']['symbol_reference'] = {'name': 'SOME_ITEMS', 'status': 'definition_located_semantics_unresolved',
        'definitions': [{'path': 'shared.lua', 'line': 19, 'sha256': 'c'*64}]}
    result['claim']['explicit_fields']['custom'] = {'unresolved_expression': 'expression(...)', 'sha256': 'd'*64}
    return result


class KnowledgeStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'knowledge.sqlite3'
        self.store = KnowledgeStore(self.path)
        self.addCleanup(lambda: self.store.db.close())

    def test_import_complete_and_idempotent(self):
        first = self.store.import_corpus(corpus(), now=100)
        self.assertEqual(first['delta']['added'], 1)
        self.assertEqual(self.store.import_corpus(corpus(), now=200)['status'], 'unchanged')
        self.assertEqual(self.store.status()['catalogs'][0]['available_at'], 100)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM imports').fetchone()[0], 1)
        self.assertEqual(self.store.current_candidates('synthetic-att'), [candidate()])

    def test_crash_resume_keeps_old_active_until_all_chunks(self):
        self.store.import_corpus(corpus(), now=10)
        update = corpus('pin2', [candidate('recipe:2'), candidate('recipe:3'), candidate('recipe:4')])
        def crash(_):
            raise RuntimeError('simulated_process_failure')
        with self.assertRaises(RuntimeError):
            self.store.import_corpus(update, chunk_size=1, now=20, fault=crash)
        self.assertEqual(self.store.current_candidates('synthetic-att'), [candidate()])
        self.assertEqual(self.store.status()['pending_imports'], 1)
        self.store.db.close()
        self.store = KnowledgeStore(self.path)
        result = self.store.import_corpus(update, chunk_size=1, now=30)
        self.assertEqual(result['delta']['withdrawn'], 1)
        self.assertEqual(len(self.store.current_candidates('synthetic-att')), 3)
        row = self.store.status()['catalogs'][0]
        self.assertEqual((row['received_at'], row['available_at']), (20, 30))
        self.assertEqual(self.store.db.execute('PRAGMA quick_check').fetchone()[0], 'ok')

    def test_history_does_not_replace_newer_catalog(self):
        old = corpus()
        self.store.import_corpus(old, now=10)
        self.store.import_corpus(corpus('pin2', [candidate('new')]), now=20)
        self.assertEqual(self.store.import_corpus(old, now=30)['status'], 'historical_reappearance')
        self.assertEqual(self.store.current_candidates('synthetic-att')[0]['key'], 'new')

    def test_pending_old_import_cannot_replace_new_head_on_resume(self):
        base = self.store.import_corpus(corpus(), now=1)['import_id']
        old = corpus('pin2', [candidate('old1'), candidate('old2')])
        def crash(_):
            raise RuntimeError('interrupted_import')
        with self.assertRaises(RuntimeError):
            self.store.import_corpus(old, chunk_size=1, now=10, fault=crash)
        new_id = self.store.import_corpus(corpus('pin3', [candidate('new')]), now=20)['import_id']
        result = self.store.import_corpus(old, chunk_size=1, now=30)
        self.assertEqual(result['status'], 'historical_completion')
        self.assertEqual(self.store.status()['catalogs'][0]['id'], new_id)
        self.assertEqual(self.store.current_candidates('synthetic-att')[0]['key'], 'new')
        row = self.store.db.execute('SELECT * FROM imports WHERE id=?', (result['import_id'],)).fetchone()
        self.assertEqual((row['status'], row['base_import_id'], row['available_at']), ('complete', base, 30))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='catalog_completed_inactive'").fetchone()[0], 1)
        # Inactive semantic data must not enqueue research for the current head.
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM research_jobs').fetchone()[0], 2)
        self.store.rollback('synthetic-att', result['import_id'], reason='explicit selection of historical import', now=40)
        self.assertEqual([row['key'] for row in self.store.current_candidates('synthetic-att')], ['old1', 'old2'])

    def test_parallel_initial_import_cannot_overwrite_winner(self):
        first = corpus('first', [candidate('first1'), candidate('first2')])
        def crash(_):
            raise RuntimeError('interrupted_import')
        with self.assertRaises(RuntimeError):
            self.store.import_corpus(first, chunk_size=1, now=10, fault=crash)
        with KnowledgeStore(self.path) as other:
            winner = other.import_corpus(corpus('winner'), now=20)['import_id']
        resumed = self.store.import_corpus(first, now=30)
        self.assertEqual(resumed['status'], 'historical_completion')
        self.assertEqual(self.store.status()['catalogs'][0]['id'], winner)

    def test_unpublished_head_keeps_previous_visible_and_recovers_at_real_time(self):
        initial = self.store.import_corpus(corpus(), now=10)['import_id']
        original_transaction = self.store.transaction
        calls = 0
        @contextmanager
        def interrupt_publication():
            nonlocal calls
            calls += 1
            if calls == 4:
                raise RuntimeError('crash_after_source_commit_before_publication')
            with original_transaction():
                yield
        # Open the independent reader before the crash, so this read does not
        # itself run constructor recovery.
        with KnowledgeStore(self.path) as reader:
            with patch.object(self.store, 'transaction', interrupt_publication):
                with self.assertRaises(RuntimeError):
                    self.store.import_corpus(corpus('new', [candidate('new')]), now=20)
            self.assertEqual(reader.current_candidates('synthetic-att'), [candidate()])
            self.assertEqual(reader.status()['catalogs'][0]['id'], initial)
            self.assertEqual(reader.status()['publishing_catalogs'], 1)
            with patch('wow_auction.knowledge_storage.time.time', return_value=900):
                with KnowledgeStore(self.path) as recovered:
                    visible = recovered.status()['catalogs'][0]
                    self.assertEqual((visible['revision'], visible['received_at'], visible['available_at']), ('new', 20, 900))
                    self.assertEqual(recovered.current_candidates('synthetic-att')[0]['key'], 'new')
                    recovered.import_corpus(corpus('new', [candidate('new')]), now=1000)
                    self.assertEqual(recovered.status()['catalogs'][0]['available_at'], 900)

    def test_first_unpublished_catalog_is_not_exposed(self):
        self.store.import_corpus(corpus(), now=10)
        # Reproduce the persisted state in the publication crash window.
        self.store.db.execute('UPDATE imports SET available_at=NULL')
        self.assertEqual(self.store.current_candidates('synthetic-att'), [])
        self.assertEqual(self.store.status()['catalogs'], [])
        self.assertEqual(self.store.status()['publishing_catalogs'], 1)

    def test_rollback_retains_history_and_is_explicit(self):
        a = self.store.import_corpus(corpus(), now=10)['import_id']
        self.store.import_corpus(corpus('pin2', []), now=20)
        self.assertEqual(self.store.current_candidates('synthetic-att'), [])
        self.store.rollback('synthetic-att', a, reason='review rejected source change', now=30)
        self.assertEqual(self.store.current_candidates('synthetic-att'), [candidate()])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM imports').fetchone()[0], 2)
        with self.assertRaises(KnowledgeError):
            self.store.rollback('other-source', a, reason='wrong')

    def test_reject_incomplete_manifest_and_orphan_candidates(self):
        for mutate in (lambda c: c.update(complete=False),
                       lambda c: c['files'][0].update(disposition='fetch_failed'),
                       lambda c: c['files'].append(c['files'][0]),
                       lambda c: c['candidates'][0]['source'].update(path='missing.lua'),
                       lambda c: c['candidates'][0]['source'].update(sha256='c'*64),
                       lambda c: c['candidates'].append(candidate())):
            with self.subTest(mutation=mutate):
                value = corpus(); mutate(value)
                with self.assertRaises(KnowledgeError):
                    self.store.import_corpus(value)
        self.assertEqual(self.store.status()['catalogs'], [])

    def test_added_old_expansion_branch_is_audited(self):
        self.store.import_corpus(corpus(), now=10)
        data = corpus('pin2')
        data['files'].append({'path': 'BurningCrusade/new.lua', 'sha256': 'c'*64, 'disposition': 'unsupported'})
        result = self.store.import_corpus(data, now=20)
        self.assertEqual(result['file_count'], 3)
        self.assertEqual(result['delta']['unchanged'], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM research_jobs').fetchone()[0], 1)

    def test_provenance_only_revision_does_not_repeat_research(self):
        self.store.import_corpus(corpus(), now=10)
        data = corpus('pin2')
        data['candidates'][0]['source']['revision'] = 'pin2'
        self.store.import_corpus(data, now=20)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM research_jobs').fetchone()[0], 1)
        data['revision'] = 'pin3'; data['candidates'][0]['claim']['recipe_id'] = 2
        result = self.store.import_corpus(data, now=30)
        self.assertEqual(result['delta']['changed'], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM research_jobs').fetchone()[0], 2)

    def test_nested_locator_changes_do_not_repeat_research(self):
        claim = located_candidate()
        old = corpus(candidates=[claim]); old['files'][0]['sha256'] = claim['source']['sha256']
        self.store.import_corpus(old, now=10)
        changed = deepcopy(old); changed['revision'] = 'pin2'
        row = changed['candidates'][0]
        row['source'].update(line=100, sha256='b'*64)
        changed['files'][0]['sha256'] = 'b'*64
        row['claim']['ancestor_context'][0]['line'] += 100
        row['claim']['related_items'][0]['line'] += 100
        row['claim']['symbol_reference']['definitions'][0].update(line=500, sha256='e'*64)
        result = self.store.import_corpus(changed, now=20)
        self.assertEqual((result['delta']['changed'], result['delta']['unchanged']), (0, 1))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM research_jobs').fetchone()[0], 1)

    def legacy_import(self, value):
        """Install an immutable pre-v2 row exactly as the earlier storage wrote it."""
        manifest = {k: v for k, v in value.items() if k not in {'files', 'candidates'}}
        manifest['file_count'] = len(value['files'])
        with self.store.transaction():
            cursor = self.store.db.execute('INSERT INTO imports(source,revision,parser,build,corpus_hash,manifest,'
                'status,checkpoint,total,received_at,available_at,base_import_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (value['source_id'], value['revision'], value['parser_revision'], value['build'], digest(value),
                 encode(manifest), 'complete', len(value['candidates']), len(value['candidates']), 10, 10, None))
            import_id = cursor.lastrowid
            self.store.db.executemany('INSERT INTO entries VALUES(?,?,?)',
                [(import_id, row['path'], encode(row)) for row in value['files']])
            for row in value['candidates']:
                old_hash = digest({k: v for k, v in row.items() if k not in {'source', 'key', 'received_at', 'observed_at'}})
                self.store.db.execute('INSERT INTO candidates VALUES(?,?,?,?)',
                    (import_id, row['key'], old_hash, encode(row)))
                self.store._enqueue('candidate_research', value['source_id'] + ':' + row['key'],
                    {'source': value['source_id'], 'key': row['key'], 'semantic_hash': old_hash, 'build': value['build']}, 10)
            self.store.db.execute('INSERT INTO active_sources VALUES(?,?)', (value['source_id'], import_id))
        return import_id

    def test_semantic_revision_creates_new_history_without_false_fact_diff(self):
        claim = located_candidate()
        data = corpus(candidates=[claim]); data['files'][0]['sha256'] = claim['source']['sha256']
        previous = self.legacy_import(data)
        prior_import = tuple(self.store.db.execute('SELECT * FROM imports WHERE id=?', (previous,)).fetchone())
        prior_candidate = tuple(self.store.db.execute('SELECT * FROM candidates WHERE import_id=?', (previous,)).fetchone())
        old_job = dict(self.store.db.execute('SELECT * FROM research_jobs').fetchone())
        result = self.store.import_corpus(data, now=20)
        self.assertNotEqual(result['import_id'], previous)
        self.assertEqual((result['delta']['added'], result['delta']['changed'], result['delta']['unchanged']), (0, 0, 1))
        self.assertEqual(result['delta']['research_jobs_superseded'], 1)
        self.assertEqual(tuple(self.store.db.execute('SELECT * FROM imports WHERE id=?', (previous,)).fetchone()), prior_import)
        self.assertEqual(tuple(self.store.db.execute('SELECT * FROM candidates WHERE import_id=?', (previous,)).fetchone()), prior_candidate)
        superseded = self.store.db.execute('SELECT * FROM research_jobs WHERE id=?', (old_job['id'],)).fetchone()
        self.assertEqual(superseded['status'], 'superseded')
        self.assertEqual(superseded['payload'], old_job['payload'])
        current = self.store.claim_job(kind='candidate_research', now=20)
        self.assertEqual(current['payload']['semantic_hash'], semantic_digest(claim))
        self.assertIsNone(self.store.claim_job(kind='candidate_research', now=20))
        self.assertEqual(self.store.status()['catalogs'][0]['manifest']['semantic_revision'], SEMANTIC_REVISION)
        event = self.store.db.execute("SELECT payload FROM events WHERE kind='candidate_research_superseded'").fetchone()
        self.assertEqual(json.loads(event[0])['count'], 1)
        again = self.store.import_corpus(data, now=30)
        self.assertEqual(again['status'], 'unchanged')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM research_jobs').fetchone()[0], 2)

    def test_unchanged_legacy_hash_does_not_duplicate_pending_job(self):
        data = corpus()
        self.legacy_import(data)
        result = self.store.import_corpus(data, now=20)
        self.assertEqual(result['delta']['changed'], 0)
        self.assertEqual(result['delta']['research_jobs_superseded'], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM research_jobs').fetchone()[0], 1)

    def test_superseded_lease_cannot_finish_and_reappearance_reactivates_job(self):
        self.store.import_corpus(corpus(), now=10)
        leased = self.store.claim_job(kind='candidate_research', now=10)
        changed = corpus('pin2', [candidate(claim={'recipe_id': 2})])
        self.store.import_corpus(changed, now=20)
        with self.assertRaises(KnowledgeError):
            self.store.finish_job(leased['id'], leased['lease_token'], now=21)
        self.store.import_corpus(corpus('pin3'), now=30)
        resumed = self.store.claim_job(kind='candidate_research', now=30)
        self.assertEqual(resumed['id'], leased['id'])
        self.assertNotEqual(resumed['lease_token'], leased['lease_token'])
        self.assertIsNone(self.store.claim_job(kind='candidate_research', now=30))

    def test_evidence_tables_are_immutable(self):
        self.store.import_corpus(corpus(), now=10)
        for table in ('entries', 'candidates', 'events'):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.db.execute(f'DELETE FROM {table}')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("UPDATE imports SET revision='rewritten'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute('DELETE FROM imports')

    def test_weekly_patch_and_feedback_deduplicate(self):
        self.store.import_corpus(corpus(), now=10)
        self.store.schedule_updates(build='12.1.5.70575', now=11)
        self.assertIsNone(self.store.claim_job(kind='source_update', now=11))
        for _ in range(2):
            self.store.schedule_updates(build='12.1.6.80000', now=10+WEEK)
            self.store.feedback('synthetic-att', 'recipe:1', 'wrong quantity', now=10+WEEK)
        jobs = self.store.status()['jobs']
        self.assertEqual(next(j['count'] for j in jobs if j['kind'] == 'source_update'), 2)
        self.assertEqual(next(j['count'] for j in jobs if j['kind'] == 'feedback_research'), 1)

    def test_lease_recovery_and_stale_owner(self):
        self.store.import_corpus(corpus(), now=10)
        first = self.store.claim_job(kind='candidate_research', now=10, lease_seconds=10)
        self.assertIsNone(self.store.claim_job(kind='candidate_research', now=19))
        second = self.store.claim_job(kind='candidate_research', now=20, lease_seconds=10)
        with self.assertRaises(KnowledgeError):
            self.store.finish_job(first['id'], first['lease_token'], now=21)
        self.store.finish_job(second['id'], second['lease_token'], error_code='source_unavailable', now=21)
        self.assertIsNone(self.store.claim_job(kind='candidate_research', now=22))
        self.assertIsNotNone(self.store.claim_job(kind='candidate_research', now=1000))

    def test_expired_lease_cannot_finish_even_without_takeover(self):
        self.store.import_corpus(corpus(), now=10)
        job = self.store.claim_job(kind='candidate_research', now=10, lease_seconds=5)
        with self.assertRaises(KnowledgeError):
            self.store.finish_job(job['id'], job['lease_token'], now=15)

    def test_claim_returns_updated_lease_record(self):
        self.store.import_corpus(corpus(), now=10)
        job = self.store.claim_job(kind='candidate_research', now=10, lease_seconds=20)
        self.assertEqual((job['status'], job['lease_until'], job['attempts']), ('running', 30, 1))
        next_job = self.store.claim_job(kind='candidate_research', now=30, lease_seconds=20)
        self.assertEqual((next_job['status'], next_job['lease_until'], next_job['attempts']), ('running', 50, 2))

    def test_renew_job_guards_owner_expiry_and_duration(self):
        self.store.import_corpus(corpus(), now=10)
        job = self.store.claim_job(kind='candidate_research', now=10, lease_seconds=20)
        with self.assertRaises(KnowledgeError):
            self.store.renew_job(job['id'], 'wrong-token', now=20)
        with self.assertRaises(KnowledgeError):
            self.store.renew_job(job['id'], job['lease_token'], now=20, lease_seconds=3601)
        renewed = self.store.renew_job(job['id'], job['lease_token'], now=20, lease_seconds=40)
        self.assertEqual(renewed['lease_until'], 60)
        # A shorter requested lease must not reduce an existing deadline.
        self.assertEqual(self.store.renew_job(job['id'], job['lease_token'], now=21, lease_seconds=1)['lease_until'], 60)
        with self.assertRaises(KnowledgeError):
            self.store.renew_job(job['id'], job['lease_token'], now=60)
        taken = self.store.claim_job(kind='candidate_research', now=60)
        with self.assertRaises(KnowledgeError):
            self.store.renew_job(job['id'], job['lease_token'], now=61)
        self.assertEqual(self.store.renew_job(taken['id'], taken['lease_token'], now=61)['status'], 'running')

    def test_artifact_idempotent_and_immutable(self):
        first = self.store.save_artifact('craftsim', {'unknown': True}, now=10)
        self.assertEqual(first, self.store.save_artifact('craftsim', {'unknown': True}, now=20))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("UPDATE artifacts SET payload='{}'")

    def test_source_artifact_identity_retains_first_receive_and_payload(self):
        identity = {'source_sha256': 'a'*64, 'normalizer_revision': 'b'*64, 'build': '12.1.5.70575'}
        payload = {'source_sha256': 'a'*64, 'observed_at': 10, 'profiles': []}
        first = self.store.save_artifact('craftsim', payload, identity=identity, now=10, file_written_at_ns=1)
        later = {**payload, 'observed_at': 20}
        self.assertEqual(self.store.save_artifact('craftsim', later, identity=identity, now=20, file_written_at_ns=2), first)
        row = self.store.find_artifact('craftsim', identity)
        self.assertEqual((row['received_at'], row['file_written_at_ns'], row['payload']['observed_at']), (10, 1, 10))
        self.assertEqual(row['sha256'], digest(payload))
        changed_parser = {**identity, 'normalizer_revision': 'c'*64}
        second = self.store.save_artifact('craftsim', payload, identity=changed_parser, now=30)
        self.assertNotEqual(second, first)
        changed_build = {**identity, 'build': '12.2.0.80000'}
        self.assertNotEqual(self.store.save_artifact('craftsim', payload, identity=changed_build, now=40), first)
        with self.assertRaises(KnowledgeError):
            self.store.save_artifact('craftsim', {'source_sha256': 'd'*64}, identity=identity)

    def evidence(self):
        return [{'reference': 'https://example.test/' + upstream, 'upstream': upstream, 'sha256': 'a'*64,
                 'build': '12.1.5.70575', 'status': 'current', 'observed_at': 20} for upstream in ('a', 'b')]

    def test_correction_requires_independent_current_evidence(self):
        self.store.import_corpus(corpus(), now=10)
        for mutate in (lambda e: e.pop(), lambda e: e[1].update(upstream='a'),
                       lambda e: e[1].update(build='old'), lambda e: e[1].update(observed_at=31)):
            evidence = self.evidence(); mutate(evidence)
            with self.assertRaises(KnowledgeError):
                self.store.propose_correction('synthetic-att', 'recipe:1', candidate(), evidence, reason='test', now=30)

    def test_evidence_must_still_be_current_at_selection(self):
        self.store.import_corpus(corpus(), now=10)
        correction = self.store.propose_correction('synthetic-att', 'recipe:1', candidate(claim={'recipe_id': 2}),
            self.evidence(), reason='test', now=30)
        with self.assertRaises(KnowledgeError):
            self.store.select_correction('synthetic-att', 'recipe:1', correction,
                review_reference='delayed review', reason='test', now=20 + 30 * 86400 + 1)
        self.assertEqual(self.store.current_candidates('synthetic-att')[0]['claim']['recipe_id'], 1)
        self.store.select_correction('synthetic-att', 'recipe:1', correction,
            review_reference='timely review', reason='test', now=20 + 30 * 86400)
        # Revoking an old selection must remain possible regardless of age.
        self.store.select_correction('synthetic-att', 'recipe:1', None,
            review_reference='revoke', reason='test', now=20 + 60 * 86400)

    def test_malformed_entry_and_evidence_fail_with_safe_error(self):
        for invalid in (None, [], 3, 'secret_source_text'):
            value = corpus(); value['files'] = [invalid]
            with self.subTest(invalid=invalid), self.assertRaises(KnowledgeError):
                self.store.import_corpus(value)
        self.store.import_corpus(corpus(), now=10)
        for invalid in (None, [], 3, 'secret_source_text'):
            evidence = self.evidence(); evidence[1] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(KnowledgeError):
                self.store.propose_correction('synthetic-att', 'recipe:1', candidate(), evidence, reason='test', now=30)

    def test_correction_proposal_review_and_rollback_are_separate(self):
        self.store.import_corpus(corpus(), now=10)
        changed = candidate(claim={'recipe_id': 2})
        correction = self.store.propose_correction('synthetic-att', 'recipe:1', changed, self.evidence(), reason='source discrepancy', now=30)
        self.assertEqual(self.store.current_candidates('synthetic-att')[0]['claim']['recipe_id'], 1)
        self.store.select_correction('synthetic-att', 'recipe:1', correction, review_reference='local review R1', reason='evidence checked', now=40)
        self.assertEqual(self.store.current_candidates('synthetic-att')[0]['claim']['recipe_id'], 2)
        self.store.select_correction('synthetic-att', 'recipe:1', None, review_reference='R2', reason='rollback', now=50)
        self.assertEqual(self.store.current_candidates('synthetic-att')[0]['claim']['recipe_id'], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM corrections').fetchone()[0], 1)

    def test_new_catalog_invalidates_old_correction_overlay(self):
        self.store.import_corpus(corpus(), now=10)
        correction = self.store.propose_correction('synthetic-att', 'recipe:1', candidate(claim={'recipe_id': 2}), self.evidence(), reason='test', now=30)
        self.store.select_correction('synthetic-att', 'recipe:1', correction, review_reference='R1', reason='test', now=40)
        self.store.import_corpus(corpus('pin2'), now=50)
        self.assertEqual(self.store.current_candidates('synthetic-att')[0]['claim']['recipe_id'], 1)
        with self.assertRaises(KnowledgeError):
            self.store.select_correction('synthetic-att', 'recipe:1', correction, review_reference='R1', reason='test', now=60)

    def test_foreign_database_is_not_modified(self):
        other = Path(self.temp.name)/'other.sqlite3'
        db = sqlite3.connect(other); db.execute('CREATE TABLE unrelated(id)'); db.commit(); db.close()
        before = other.read_bytes()
        with self.assertRaises(KnowledgeError):
            KnowledgeStore(other)
        self.assertEqual(other.read_bytes(), before)


class MechanicContractTests(unittest.TestCase):
    def base(self):
        return {'family': 'mill', 'batch_size': 5, 'inputs': [{'item_id': 1, 'quantity': 5}],
                'outputs': None, 'joint_outcomes': None, 'expected_outputs': [{'item_id': 2, 'mean_quantity': '1.222'}]}

    def test_mean_is_not_a_physical_item_or_distribution(self):
        value = self.base()
        self.assertFalse(mechanic_readiness(value)['mechanically_complete'])
        value['outputs'] = [{'item_id': 2, 'quantity': 1.222}]
        with self.assertRaises(KnowledgeError):
            validate_mechanic(value)

    def test_semantic_digest_retains_actual_expression_hash_and_definition_identity(self):
        original = located_candidate()
        for mutate in (
            lambda row: row['claim']['explicit_fields']['custom'].update(sha256='e'*64),
            lambda row: row['claim']['symbol_reference']['definitions'][0].update(path='different-definition.lua'),
            lambda row: row['claim']['ancestor_context'][0]['fields'].update(line=12),
            lambda row: row['claim']['related_items'][0].update(quantity=2),
        ):
            changed = deepcopy(original); mutate(changed)
            with self.subTest(mutation=mutate):
                self.assertNotEqual(semantic_digest(original), semantic_digest(changed))

    def test_malformed_expected_output_is_safe_error(self):
        for invalid in (None, [], 3, 'secret_source_text'):
            value = self.base(); value['expected_outputs'] = [invalid]
            with self.subTest(invalid=invalid), self.assertRaises(KnowledgeError):
                validate_mechanic(value)

    def test_unknown_probability_is_not_uniform(self):
        value = self.base()
        value['joint_outcomes'] = [{'items': [{'item_id': 2, 'quantity': 1}], 'probability': None}]
        with self.assertRaises(KnowledgeError):
            validate_mechanic(value)

    def test_incomplete_joint_pmf_is_rejected(self):
        value = self.base(); value['distribution_basis'] = 'documented_model'
        value['joint_outcomes'] = [{'items': [], 'probability': '0.999'}]
        with self.assertRaises(KnowledgeError):
            validate_mechanic(value)

    def test_joint_coproducts_and_empty_failure_output(self):
        value = self.base(); value['distribution_basis'] = 'documented_model'
        value['joint_outcomes'] = [{'items': [{'item_id': 2, 'quantity': 1}, {'item_id': 3, 'quantity': 2}], 'probability': '0.5'},
                                   {'items': [], 'probability': '0.5'}]
        self.assertIs(validate_mechanic(value), value)
        self.assertFalse(mechanic_readiness(value)['recommendation_eligible'])

    def test_duplicate_or_fractional_inputs_rejected(self):
        for bad in ([{'item_id': 1, 'quantity': 0.5}], [{'item_id': 1, 'quantity': 1}]*2):
            value = self.base(); value['inputs'] = bad
            with self.assertRaises(KnowledgeError):
                validate_mechanic(value)


if __name__ == '__main__':
    unittest.main()
