"""Resumable source catalogs, immutable revisions and bounded local update jobs."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .knowledge import (KnowledgeError, SEMANTIC_REVISION, digest, encode, integer, semantic_digest,
                        sha256, text, validate_candidate)

APP_ID = 0x57414B33
WEEK = 7 * 86400

SCHEMA = (
    'CREATE TABLE imports(id INTEGER PRIMARY KEY, source TEXT NOT NULL, revision TEXT NOT NULL, '
    'parser TEXT NOT NULL, build TEXT NOT NULL, corpus_hash TEXT NOT NULL, manifest TEXT NOT NULL, '
    'status TEXT NOT NULL, checkpoint INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL, '
    'received_at INTEGER NOT NULL, available_at INTEGER, '
    'base_import_id INTEGER REFERENCES imports(id), UNIQUE(source,corpus_hash))',
    'CREATE TABLE entries(import_id INTEGER NOT NULL REFERENCES imports(id), path TEXT NOT NULL, '
    'payload TEXT NOT NULL, PRIMARY KEY(import_id,path))',
    'CREATE TABLE candidates(import_id INTEGER NOT NULL REFERENCES imports(id), key TEXT NOT NULL, '
    'semantic_hash TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(import_id,key))',
    'CREATE INDEX candidate_semantic ON candidates(semantic_hash)',
    'CREATE TABLE active_sources(source TEXT PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id))',
    'CREATE TABLE events(id INTEGER PRIMARY KEY, source TEXT, kind TEXT NOT NULL, '
    'created_at INTEGER NOT NULL, payload TEXT NOT NULL)',
    'CREATE TABLE research_jobs(id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, '
    'kind TEXT NOT NULL, scope TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, '
    'due_at INTEGER NOT NULL, lease_token TEXT, lease_until INTEGER, attempts INTEGER NOT NULL DEFAULT 0, '
    'error_code TEXT)',
    'CREATE TABLE artifacts(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, sha256 TEXT NOT NULL, '
    'identity_hash TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL, '
    'received_at INTEGER NOT NULL, file_written_at_ns INTEGER, UNIQUE(kind,identity_hash))',
    'CREATE TABLE corrections(id INTEGER PRIMARY KEY, source TEXT NOT NULL, candidate_key TEXT NOT NULL, '
    'base_import_id INTEGER NOT NULL REFERENCES imports(id), payload TEXT NOT NULL, '
    'evidence TEXT NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL)',
    'CREATE TABLE correction_heads(source TEXT NOT NULL, candidate_key TEXT NOT NULL, '
    'correction_id INTEGER NOT NULL REFERENCES corrections(id), PRIMARY KEY(source,candidate_key))',
)


class KnowledgeStore:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink() or str(self.path).startswith('\\\\') or self.path.suffix.lower() in {'.lua', '.bak'}:
            raise KnowledgeError('unsupported_knowledge_database_location')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA trusted_schema=OFF')
            self.db.execute('PRAGMA synchronous=FULL')
            if self.db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'delete':
                raise KnowledgeError('rollback_journal_required')
            app_id = self.db.execute('PRAGMA application_id').fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if app_id not in {0, APP_ID} or (app_id == 0 and (tables or version)):
                raise KnowledgeError('foreign_knowledge_database')
            if version not in {0, 1}:
                raise KnowledgeError('unsupported_knowledge_schema')
            if version == 0:
                with self.transaction():
                    for statement in SCHEMA:
                        self.db.execute(statement)
                    for table in ('entries', 'candidates', 'events', 'artifacts', 'corrections'):
                        for action in ('UPDATE', 'DELETE'):
                            self.db.execute(f'CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} '
                                            "BEGIN SELECT RAISE(ABORT,'immutable_knowledge_evidence'); END")
                    self.db.execute('CREATE TRIGGER immutable_import_identity BEFORE UPDATE OF '
                                    'source,revision,parser,build,corpus_hash,manifest,total,received_at,base_import_id '
                                    "ON imports BEGIN SELECT RAISE(ABORT,'immutable_knowledge_evidence'); END")
                    self.db.execute('CREATE TRIGGER immutable_import_delete BEFORE DELETE ON imports '
                                    "BEGIN SELECT RAISE(ABORT,'immutable_knowledge_evidence'); END")
                    self.db.execute(f'PRAGMA application_id={APP_ID}')
                    self.db.execute('PRAGMA user_version=1')
            # First observed availability after a crash is recovery time, never
            # the time the source file happened to be generated.
            with self.transaction():
                self.db.execute("UPDATE imports SET available_at=? WHERE status='complete' AND available_at IS NULL",
                                (int(time.time()),))
        except BaseException:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _event(self, kind, payload, *, source=None, now=None):
        self.db.execute('INSERT INTO events(source,kind,created_at,payload) VALUES(?,?,?,?)',
                        (source, kind, int(time.time()) if now is None else now, encode(payload)))

    def import_corpus(self, corpus, *, chunk_size=250, now=None, fault=None):
        """Checkpoint chunks; only a completely reconciled manifest becomes active.

        Traversal completion permits explicit unsupported/quarantine dispositions.
        It does not establish complete mechanics, source truth or world coverage.
        An interrupted import cannot remove any currently visible candidate.
        """
        supplied_clock = now is not None
        now = integer(int(time.time()) if now is None else now)
        integer(chunk_size, minimum=1)
        if chunk_size > 10000 or not isinstance(corpus, dict):
            raise KnowledgeError('invalid_corpus_import')
        source = text(corpus.get('source_id'), 200)
        revision = text(corpus.get('revision'), 200)
        parser = text(corpus.get('parser_revision'), 200)
        build = text(corpus.get('build'), 100)
        files = corpus.get('files')
        candidates = corpus.get('candidates')
        if (corpus.get('complete') is not True or not isinstance(files, list) or not files
                or len(files) > 100000 or not isinstance(candidates, list) or len(candidates) > 500000):
            raise KnowledgeError('incomplete_or_oversized_corpus')
        by_path = {}
        for entry in files:
            if not isinstance(entry, dict):
                raise KnowledgeError('invalid_file_entry')
            path = text(entry.get('path'), 2000)
            if path in by_path or entry.get('disposition') in {None, '', 'pending', 'fetch_failed'}:
                raise KnowledgeError('incomplete_or_duplicate_file_disposition')
            sha256(entry.get('sha256'))
            by_path[path] = entry
        keys = set()
        prepared = []
        for candidate in candidates:
            validate_candidate(candidate)
            if candidate['key'] in keys:
                raise KnowledgeError('duplicate_candidate_key')
            keys.add(candidate['key'])
            locator = candidate['source']
            entry = by_path.get(locator['path'])
            if entry is None or entry['sha256'] != locator['sha256']:
                raise KnowledgeError('candidate_not_in_manifest')
            prepared.append((candidate['key'], semantic_digest(candidate), encode(candidate)))
        # Include every provenance and disposition, even if semantic facts did not change.
        corpus_hash = digest({'semantic_revision': SEMANTIC_REVISION, 'corpus': corpus})
        manifest = {k: v for k, v in corpus.items() if k not in {'candidates', 'files'}}
        manifest['file_count'] = len(files)
        manifest['semantic_revision'] = SEMANTIC_REVISION
        with self.transaction():
            row = self.db.execute('SELECT * FROM imports WHERE source=? AND corpus_hash=?',
                                  (source, corpus_hash)).fetchone()
            if row is None:
                base = self.db.execute('SELECT import_id FROM active_sources WHERE source=?', (source,)).fetchone()
                cursor = self.db.execute('INSERT INTO imports(source,revision,parser,build,corpus_hash,manifest,'
                                         'status,total,received_at,base_import_id) VALUES(?,?,?,?,?,?,\'pending\',?,?,?)',
                                         (source, revision, parser, build, corpus_hash, encode(manifest), len(prepared), now,
                                          base[0] if base else None))
                import_id = cursor.lastrowid
                self.db.executemany('INSERT INTO entries VALUES(?,?,?)',
                                    ((import_id, e['path'], encode(e)) for e in files))
                checkpoint = 0
            else:
                import_id = row['id']
                checkpoint = row['checkpoint']
                if row['status'] == 'complete':
                    # Re-reading an old corpus never rolls a newer catalog back silently.
                    active = self.db.execute('SELECT import_id FROM active_sources WHERE source=?', (source,)).fetchone()
                    return {'status': 'unchanged' if active and active[0] == import_id else 'historical_reappearance',
                            'import_id': import_id, 'candidate_count': len(prepared)}
        while checkpoint < len(prepared):
            end = min(checkpoint + chunk_size, len(prepared))
            with self.transaction():
                actual = self.db.execute('SELECT checkpoint FROM imports WHERE id=?', (import_id,)).fetchone()[0]
                if actual != checkpoint:
                    raise KnowledgeError('concurrent_corpus_import')
                self.db.executemany('INSERT INTO candidates VALUES(?,?,?,?)',
                                    ((import_id, *r) for r in prepared[checkpoint:end]))
                self.db.execute('UPDATE imports SET checkpoint=? WHERE id=?', (end, import_id))
            checkpoint = end
            if fault:
                fault(checkpoint)
        available_at = now if supplied_clock else int(time.time())
        with self.transaction():
            finished = self.db.execute('SELECT * FROM imports WHERE id=?', (import_id,)).fetchone()
            if finished['status'] == 'complete':
                active = self.db.execute('SELECT import_id FROM active_sources WHERE source=?', (source,)).fetchone()
                return {'status': 'unchanged' if active and active[0] == import_id else 'historical_reappearance',
                        'import_id': import_id, 'candidate_count': len(prepared)}
            prior = self.db.execute('SELECT import_id FROM active_sources WHERE source=?', (source,)).fetchone()
            old_id = prior[0] if prior else None
            # Another revision or an explicit rollback may have changed the head
            # while these chunks were being fetched/committed. Preserve this
            # completed import as history, without silently replacing that head.
            activate = old_id == finished['base_import_id']
            previous_manifest = self.db.execute('SELECT manifest FROM imports WHERE id=?', (old_id,)).fetchone()
            old_semantic_revision = (json.loads(previous_manifest[0]).get('semantic_revision', 1)
                                     if previous_manifest else SEMANTIC_REVISION)
            if type(old_semantic_revision) is not int or not 1 <= old_semantic_revision <= SEMANTIC_REVISION:
                raise KnowledgeError('unsupported_previous_semantic_revision')
            if old_semantic_revision != SEMANTIC_REVISION:
                # Compare both sides with today's algorithm. Old payloads and
                # hashes remain immutable; an algorithm update is not a fact edit.
                old = {row['key']: semantic_digest(json.loads(row['payload'])) for row in self.db.execute(
                    'SELECT key,payload FROM candidates WHERE import_id=?', (old_id,))}
            else:
                old = dict(self.db.execute('SELECT key,semantic_hash FROM candidates WHERE import_id=?', (old_id,)))
            new = {key: semantic for key, semantic, payload in prepared}
            added = sorted(new.keys() - old.keys())
            removed = sorted(old.keys() - new.keys())
            changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
            superseded_jobs = 0
            if activate:
                superseded_jobs, replacements = self._supersede_research_jobs(source, new, build=build)
                for key in sorted(set(added + changed) | replacements):
                    self._enqueue('candidate_research', source + ':' + key,
                                  {'source': source, 'key': key, 'semantic_hash': new[key], 'build': build}, now)
                if superseded_jobs:
                    self._event('candidate_research_superseded', {'import_id': import_id,
                        'previous_import_id': old_id, 'count': superseded_jobs,
                        'replacement_keys': len(replacements), 'previous_semantic_revision': old_semantic_revision,
                        'semantic_revision': SEMANTIC_REVISION}, source=source, now=now)
            self.db.execute("UPDATE imports SET status='complete',available_at=NULL WHERE id=?", (import_id,))
            if activate:
                self.db.execute('INSERT INTO active_sources VALUES(?,?) ON CONFLICT(source) DO UPDATE SET import_id=excluded.import_id',
                                (source, import_id))
            delta = {'import_id': import_id, 'previous_import_id': old_id, 'added': added,
                     'changed': changed, 'withdrawn': removed, 'unchanged': len(new) - len(added) - len(changed),
                     'research_jobs_superseded': superseded_jobs}
            self._event('catalog_activated' if activate else 'catalog_completed_inactive',
                        delta if activate else {'import_id': import_id, 'base_import_id': finished['base_import_id'],
                            'current_import_id': old_id, 'reason': 'source_head_changed_during_import'},
                        source=source, now=available_at)
        # Persist a conservative post-commit availability time. A crash between
        # commits leaves a complete catalog whose visibility needs reconciliation.
        with self.transaction():
            self.db.execute('UPDATE imports SET available_at=? WHERE id=? AND available_at IS NULL',
                            (available_at if supplied_clock else int(time.time()), import_id))
        return {'status': 'imported' if activate else 'historical_completion',
                'import_id': import_id, 'candidate_count': len(prepared),
                'file_count': len(files), 'delta': {k: len(v) if isinstance(v, list) else v for k, v in delta.items()}}

    def rollback(self, source, import_id, *, reason, now=None):
        text(reason, 2000)
        integer(import_id, minimum=1)
        with self.transaction():
            row = self.db.execute('SELECT * FROM imports WHERE id=? AND source=?', (import_id, source)).fetchone()
            if row is None or row['status'] != 'complete' or row['available_at'] is None:
                raise KnowledgeError('rollback_requires_complete_same_source')
            prior = self.db.execute('SELECT import_id FROM active_sources WHERE source=?', (source,)).fetchone()
            self.db.execute('INSERT INTO active_sources VALUES(?,?) ON CONFLICT(source) DO UPDATE SET import_id=excluded.import_id',
                            (source, import_id))
            self._event('catalog_rollback', {'from': prior[0] if prior else None, 'to': import_id, 'reason': reason},
                        source=source, now=now)

    def _visible_catalog(self, source):
        """During post-commit publication, retain the previous published head."""
        row = self.db.execute('SELECT i.* FROM imports i JOIN active_sources a ON a.import_id=i.id '
                              'WHERE a.source=?', (source,)).fetchone()
        seen = set()
        while row is not None and (row['status'] != 'complete' or row['available_at'] is None):
            if row['id'] in seen:
                raise KnowledgeError('invalid_catalog_publication_chain')
            seen.add(row['id'])
            row = self.db.execute('SELECT * FROM imports WHERE id=? AND source=?',
                                  (row['base_import_id'], source)).fetchone()
        return row

    def current_candidates(self, source, *, limit=100, offset=0):
        integer(limit, minimum=1)
        integer(offset)
        if limit > 1000:
            raise KnowledgeError('candidate_page_limit')
        visible = self._visible_catalog(source)
        if visible is None:
            return []
        return [json.loads(row[0]) for row in self.db.execute(
            'SELECT COALESCE(r.payload,c.payload) FROM candidates c '
            'LEFT JOIN correction_heads h ON h.source=? AND h.candidate_key=c.key '
            'LEFT JOIN corrections r ON r.id=h.correction_id AND r.base_import_id=c.import_id '
            'WHERE c.import_id=? ORDER BY c.key LIMIT ? OFFSET ?', (source, visible['id'], limit, offset))]

    def _enqueue(self, kind, scope, payload, due_at):
        key = digest({'kind': kind, 'scope': scope, 'payload': payload})
        self.db.execute("INSERT OR IGNORE INTO research_jobs(fingerprint,kind,scope,payload,status,due_at) VALUES(?,?,?,?,'pending',?)",
                        (key, kind, scope, encode(payload), due_at))
        old = self.db.execute('SELECT id FROM research_jobs WHERE fingerprint=? AND status=\'superseded\'', (key,)).fetchone()
        if old is not None:
            self.db.execute("UPDATE research_jobs SET status='pending',due_at=?,error_code=NULL,lease_token=NULL,lease_until=NULL WHERE id=?",
                            (due_at, old[0]))
            self._event('research_job_reactivated', {'id': old[0], 'kind': kind},
                        source=payload.get('source'), now=due_at)
        return key

    def _supersede_research_jobs(self, source, semantic_hashes, *, build):
        """Retain obsolete jobs as history; only current facts remain claimable.

        No AI job is executed by this stage. A lease owner of an obsolete job
        also loses its token, preventing a future consumer from completing it.
        Already completed jobs are retained without scheduling duplicate work.
        """
        superseded, replacements = [], set()
        for row in self.db.execute("SELECT id,payload FROM research_jobs WHERE kind='candidate_research' "
                                   "AND status IN ('pending','running')"):
            payload = json.loads(row['payload'])
            if payload.get('source') != source:
                continue
            key = payload.get('key')
            if (key not in semantic_hashes or payload.get('semantic_hash') != semantic_hashes[key]
                    or payload.get('build') != build):
                superseded.append((row['id'],))
                if key in semantic_hashes:
                    replacements.add(key)
        self.db.executemany("UPDATE research_jobs SET status='superseded',error_code='superseded_by_catalog_revision',"
                            'lease_token=NULL,lease_until=NULL WHERE id=?', superseded)
        return len(superseded), replacements

    def schedule_updates(self, *, build, now=None):
        now = integer(int(time.time()) if now is None else now)
        text(build, 100)
        with self.transaction():
            for row in self.db.execute('SELECT i.* FROM imports i JOIN active_sources a ON a.import_id=i.id').fetchall():
                if now >= row['received_at'] + WEEK:
                    self._enqueue('source_update', row['source'], {'source': row['source'], 'week': now // WEEK}, now)
                if row['build'] != build:
                    self._enqueue('source_update', row['source'], {'source': row['source'], 'build': build, 'reason': 'patch'}, now)

    def feedback(self, source, key, message, *, now=None):
        text(message, 4000)
        text(key, 1000)
        now = integer(int(time.time()) if now is None else now)
        row = self.db.execute('SELECT c.semantic_hash,i.build FROM candidates c JOIN active_sources a ON a.import_id=c.import_id '
                              'JOIN imports i ON i.id=a.import_id WHERE a.source=? AND c.key=?', (source, key)).fetchone()
        if row is None:
            raise KnowledgeError('feedback_candidate_missing')
        with self.transaction():
            return self._enqueue('feedback_research', source + ':' + key,
                                 {'source': source, 'key': key, 'message': message,
                                  'semantic_hash': row['semantic_hash'], 'build': row['build']}, now)

    def claim_job(self, *, kind, now=None, lease_seconds=900):
        now = integer(int(time.time()) if now is None else now)
        integer(lease_seconds, minimum=1)
        if lease_seconds > 3600:
            raise KnowledgeError('knowledge_lease_limit')
        with self.transaction():
            row = self.db.execute("SELECT * FROM research_jobs WHERE kind=? AND due_at<=? AND "
                                  "(status='pending' OR status='running' AND lease_until<=?) ORDER BY id LIMIT 1",
                                  (kind, now, now)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            self.db.execute("UPDATE research_jobs SET status='running',lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=?",
                            (token, now + lease_seconds, row['id']))
            return {**dict(row), 'payload': json.loads(row['payload']), 'lease_token': token,
                    'status': 'running', 'lease_until': now + lease_seconds, 'attempts': row['attempts'] + 1}

    def renew_job(self, job_id, token, *, now=None, lease_seconds=3600):
        now = integer(int(time.time()) if now is None else now)
        integer(job_id, minimum=1)
        text(token, 100)
        integer(lease_seconds, minimum=1)
        if lease_seconds > 3600:
            raise KnowledgeError('knowledge_lease_limit')
        with self.transaction():
            row = self.db.execute("SELECT * FROM research_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                                  (job_id, token, now)).fetchone()
            if row is None:
                raise KnowledgeError('knowledge_job_lease_lost')
            deadline = max(row['lease_until'], now + lease_seconds)
            self.db.execute('UPDATE research_jobs SET lease_until=? WHERE id=?', (deadline, job_id))
            return {'id': job_id, 'lease_token': token, 'lease_until': deadline, 'status': 'running'}

    def finish_job(self, job_id, token, *, error_code=None, now=None):
        now = integer(int(time.time()) if now is None else now)
        if error_code is not None:
            text(error_code, 100)
        with self.transaction():
            row = self.db.execute("SELECT * FROM research_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                                  (job_id, token, now)).fetchone()
            if row is None:
                raise KnowledgeError('knowledge_job_lease_lost')
            delay = min(WEEK, 60 * 2**min(row['attempts'], 12))
            self.db.execute('UPDATE research_jobs SET status=?,due_at=?,lease_token=NULL,lease_until=NULL,error_code=? WHERE id=?',
                            ('pending' if error_code else 'complete', now + delay if error_code else now, error_code, job_id))

    @staticmethod
    def _artifact_identity(kind, identity):
        if kind not in {'craftsim', 'item_metadata', 'craftsim_cache', 'tsm_crafting_cache'}:
            raise KnowledgeError('unsupported_knowledge_artifact')
        if (not isinstance(identity, dict)
                or set(identity) != {'source_sha256', 'normalizer_revision', 'build'}):
            raise KnowledgeError('invalid_artifact_source_identity')
        sha256(identity['source_sha256'])
        sha256(identity['normalizer_revision'])
        text(identity['build'], 100)
        return digest(identity)

    def find_artifact(self, kind, identity):
        identity_hash = self._artifact_identity(kind, identity)
        row = self.db.execute('SELECT * FROM artifacts WHERE kind=? AND identity_hash=?',
                              (kind, identity_hash)).fetchone()
        if row is None:
            return None
        return {**dict(row), 'payload': json.loads(row['payload']), 'identity': json.loads(row['identity'])}

    def save_artifact(self, kind, payload, *, file_written_at_ns=None, now=None, identity=None):
        if kind not in {'craftsim', 'item_metadata', 'craftsim_cache', 'tsm_crafting_cache'}:
            raise KnowledgeError('unsupported_knowledge_artifact')
        body = encode(payload)
        if len(body.encode()) > 64 * 1024 * 1024:
            raise KnowledgeError('knowledge_artifact_size_limit')
        checksum = digest(payload)
        if identity is None:
            # Compatibility for callers storing an already stable domain DTO.
            # CLI source imports always supply raw hash + adapter/build context.
            identity = {'payload_sha256': checksum}
            identity_hash = digest(identity)
        else:
            identity_hash = self._artifact_identity(kind, identity)
            if not isinstance(payload, dict) or payload.get('source_sha256') != identity['source_sha256']:
                raise KnowledgeError('artifact_source_hash_mismatch')
        now = integer(int(time.time()) if now is None else now)
        if file_written_at_ns is not None:
            integer(file_written_at_ns)
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO artifacts(kind,sha256,identity_hash,identity,payload,received_at,file_written_at_ns) VALUES(?,?,?,?,?,?,?)',
                            (kind, checksum, identity_hash, encode(identity), body, now, file_written_at_ns))
        return self.db.execute('SELECT id FROM artifacts WHERE kind=? AND identity_hash=?', (kind, identity_hash)).fetchone()[0]

    def propose_correction(self, source, key, candidate, evidence, *, reason, now=None):
        """Evidence-bearing proposal; never bypass independent review by promoting it.

        Independence records refer to upstreams, not two mirrors of one dataset.
        Evidence is an assertion with provenance, not proof generated by this code.
        """
        now = integer(int(time.time()) if now is None else now)
        text(reason, 2000)
        validate_candidate(candidate)
        if candidate['key'] != key or not isinstance(evidence, list) or not 2 <= len(evidence) <= 20:
            raise KnowledgeError('independent_current_evidence_required')
        active = self.db.execute('SELECT i.* FROM imports i JOIN active_sources a ON a.import_id=i.id WHERE a.source=?', (source,)).fetchone()
        if active is None or not self.db.execute('SELECT 1 FROM candidates WHERE import_id=? AND key=?', (active['id'], key)).fetchone():
            raise KnowledgeError('correction_candidate_missing')
        self._validate_correction_evidence(evidence, build=active['build'], now=now)
        with self.transaction():
            cursor = self.db.execute('INSERT INTO corrections(source,candidate_key,base_import_id,payload,evidence,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                                     (source, key, active['id'], encode(candidate), encode(evidence), reason, now))
            correction_id = cursor.lastrowid
            self._event('correction_proposed', {'id': correction_id, 'key': key,
                                               'status': 'requires_independent_review'}, source=source, now=now)
        return correction_id

    @staticmethod
    def _validate_correction_evidence(evidence, *, build, now):
        if not isinstance(evidence, list) or not 2 <= len(evidence) <= 20:
            raise KnowledgeError('independent_current_evidence_required')
        upstreams = set()
        for fact in evidence:
            if not isinstance(fact, dict):
                raise KnowledgeError('invalid_correction_evidence')
            text(fact.get('reference'), 2000)
            upstreams.add(text(fact.get('upstream'), 200))
            sha256(fact.get('sha256'))
            if (fact.get('build') != build or fact.get('status') != 'current'
                    or not 0 <= now - integer(fact.get('observed_at')) <= 30 * 86400):
                raise KnowledgeError('evidence_not_current_for_build')
        if len(upstreams) < 2:
            raise KnowledgeError('evidence_same_upstream')

    def select_correction(self, source, key, correction_id, *, review_reference, reason, now=None):
        """Explicit reviewer decision or rollback of an overlay, with audit history.

        This method records a supplied review, it does not perform a scientific
        review. The candidate remains ineligible for economic recommendations.
        """
        text(review_reference, 2000)
        text(reason, 2000)
        now = integer(int(time.time()) if now is None else now)
        with self.transaction():
            prior = self.db.execute('SELECT correction_id FROM correction_heads WHERE source=? AND candidate_key=?',
                                    (source, key)).fetchone()
            if correction_id is None:
                self.db.execute('DELETE FROM correction_heads WHERE source=? AND candidate_key=?', (source, key))
            else:
                integer(correction_id, minimum=1)
                row = self.db.execute('SELECT r.*,i.build FROM corrections r JOIN active_sources a ON a.import_id=r.base_import_id '
                                      'JOIN imports i ON i.id=a.import_id '
                                      'WHERE r.id=? AND r.source=? AND r.candidate_key=? AND a.source=? '
                                      "AND i.status='complete' AND i.available_at IS NOT NULL",
                                      (correction_id, source, key, source)).fetchone()
                if row is None:
                    raise KnowledgeError('correction_wrong_or_stale_base')
                self._validate_correction_evidence(json.loads(row['evidence']), build=row['build'], now=now)
                self.db.execute('INSERT INTO correction_heads VALUES(?,?,?) ON CONFLICT(source,candidate_key) '
                                'DO UPDATE SET correction_id=excluded.correction_id', (source, key, correction_id))
            self._event('correction_review_decision', {'key': key, 'from': prior[0] if prior else None,
                        'to': correction_id, 'review_reference': review_reference, 'reason': reason}, source=source, now=now)

    def status(self):
        active = []
        for source in self.db.execute('SELECT source FROM active_sources ORDER BY source').fetchall():
            row = self._visible_catalog(source[0])
            if row is None:
                continue
            active.append({k: row[k] for k in ('id', 'source', 'revision', 'parser', 'build', 'total', 'received_at', 'available_at')}
                          | {'manifest': json.loads(row['manifest'])})
        return {'stage': 3, 'schema': 1, 'catalogs': active,
                'publishing_catalogs': self.db.execute('SELECT COUNT(*) FROM imports i JOIN active_sources a '
                    'ON a.import_id=i.id WHERE i.available_at IS NULL').fetchone()[0],
                'pending_imports': self.db.execute("SELECT COUNT(*) FROM imports WHERE status='pending'").fetchone()[0],
                'jobs': [dict(r) for r in self.db.execute('SELECT kind,status,COUNT(*) AS count FROM research_jobs GROUP BY kind,status')],
                'artifacts': [dict(r) for r in self.db.execute('SELECT kind,COUNT(*) AS count FROM artifacts GROUP BY kind')],
                'recommendations_implemented': False, 'whole_game_coverage': 'unknown',
                'personal_profession_availability_checked': False}
