"""Separate market book: immutable observations, durable scheduling and bounded retention."""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import uuid

from .market_common import MarketError, fingerprint, integer
from .market_freshness import describe, assess, compare
from .storage import encode

APP_ID = 0x57414D32
MAX_NS = 2**63 - 1
_RATE_LIMITS = {'blizzard': (36000, 100), 'undermine': (3000, 100), 'tsm': (120, 5)}
_PROVIDERS = set(_RATE_LIMITS)
_BOOLEAN_CAPABILITIES = {
    'aggregates', 'offers', 'depth', 'quantity', 'history_timeline', 'variant_detail',
    'sale_statistics', 'executable_quotes', 'offer_depth', 'auction_ids', 'quantities',
    'variants', 'executed_sales', 'realm_identity', 'connected_realm_mapping',
    'requires_api_key', 'requires_paid', 'confirmed_sales', 'execution_probability',
    'independent_upstream', 'variant_presence_index',
}


def _diagnostic_projection(normalized):
    """Only adapter diagnostics, never raw fields, rows, prices, names or keys."""
    if normalized is None:
        return {'capabilities': None, 'warnings': None, 'source_clock_ahead': None}
    try:
        document = json.loads(normalized)
        capabilities = document.get('capabilities', {})
        warnings = document.get('warnings', [])
        if not isinstance(capabilities, dict) or not isinstance(warnings, list):
            raise ValueError
        safe = {key: value for key, value in capabilities.items()
                if key in _BOOLEAN_CAPABILITIES and type(value) is bool}
        for key in ('gzip_points', 'points_per_sliding_hour'):
            value = capabilities.get(key)
            if type(value) is int and 0 <= value <= 1_000_000:
                safe[key] = value
        if capabilities.get('history') in ('hourly', 'daily', False):
            safe['history'] = capabilities['history']
        if capabilities.get('depth_representation') in {'individual_items_without_auction_id', 'aggregated_price_levels'}:
            safe['depth_representation'] = capabilities['depth_representation']
        return {'capabilities': safe,
                'warnings': [value for value in warnings[:32] if isinstance(value, str)
                             and re.fullmatch(r'[a-z][a-z0-9_]{0,95}', value)],
                'source_clock_ahead': document.get('source_clock_ahead') if type(document.get('source_clock_ahead')) is bool else None}
    except (ValueError, TypeError, AttributeError, RecursionError):
        return {'capabilities': None, 'warnings': ['stored_diagnostics_unavailable'], 'source_clock_ahead': None}


class MarketStore:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink() or str(self.path).startswith('\\\\') or self.path.suffix.lower() in {'.lua', '.bak'}:
            raise MarketError('unsafe_market_database_path')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA trusted_schema=OFF')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA secure_delete=ON')
            if self.db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'delete':
                raise MarketError('market_rollback_journal_required')
            self._migrate()
            if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise MarketError('market_database_integrity')
            self._publish()
        except BaseException:
            self.db.close()
            raise

    def __enter__(self): return self
    def __exit__(self, *_): self.db.close()

    def _migrate(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            app = self.db.execute('PRAGMA application_id').fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app != APP_ID and (app != 0 or tables):
                raise MarketError('foreign_market_database')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise MarketError('unsupported_market_schema')
            if version == 0:
                statements = [
                    'CREATE TABLE policies (fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL)',
                    'CREATE TABLE snapshots (id INTEGER PRIMARY KEY, job_key TEXT NOT NULL, provider TEXT NOT NULL, sha256 TEXT NOT NULL, '
                    'scope TEXT NOT NULL, dataset TEXT NOT NULL, raw BLOB, normalized TEXT, record_count INTEGER NOT NULL, '
                    'source_snapshot_at INTEGER, source_generated_at INTEGER, received_at_ns INTEGER NOT NULL, '
                    'available_at_ns INTEGER, expires_at_ns INTEGER NOT NULL, content_status TEXT NOT NULL, '
                    'policy_fingerprint TEXT NOT NULL REFERENCES policies(fingerprint), headers TEXT NOT NULL, completeness TEXT NOT NULL)',
                    'CREATE INDEX snapshots_job ON snapshots(job_key,id)',
                    'CREATE TABLE current_datasets (job_key TEXT PRIMARY KEY, snapshot_id INTEGER NOT NULL REFERENCES snapshots(id))',
                    'CREATE TABLE attempts (id INTEGER PRIMARY KEY, job_key TEXT NOT NULL, received_at_ns INTEGER NOT NULL, '
                    'status TEXT NOT NULL, code TEXT, http_status INTEGER, snapshot_id INTEGER REFERENCES snapshots(id), bytes INTEGER NOT NULL DEFAULT 0)',
                    'CREATE TABLE jobs (job_key TEXT PRIMARY KEY, config TEXT NOT NULL, next_due_ns INTEGER NOT NULL DEFAULT 0, '
                    'last_started_ns INTEGER, last_finished_ns INTEGER, failures INTEGER NOT NULL DEFAULT 0, health TEXT NOT NULL DEFAULT \'never_fetched\', '
                    'etag TEXT, last_modified TEXT, lease_token TEXT, lease_until_ns INTEGER)',
                    'CREATE TABLE rate_usage (provider TEXT NOT NULL, at_ns INTEGER NOT NULL, points INTEGER NOT NULL)',
                    'CREATE INDEX rate_window ON rate_usage(provider,at_ns)',
                    'CREATE TABLE gaps (id INTEGER PRIMARY KEY, job_key TEXT NOT NULL, start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL, reason TEXT NOT NULL, '
                    'UNIQUE(job_key,start_ns,end_ns,reason))',
                    'CREATE TABLE input_references (ref TEXT PRIMARY KEY, snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), created_at_ns INTEGER NOT NULL)',
                    'CREATE TABLE realm_mappings (region TEXT NOT NULL, slug TEXT NOT NULL, realm_id INTEGER NOT NULL, connected_id INTEGER NOT NULL, '
                    'snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), confirmed_at_ns INTEGER NOT NULL, expires_at_ns INTEGER NOT NULL, PRIMARY KEY(region,slug))',
                ]
                for sql in statements: self.db.execute(sql)
                self.db.execute(f'PRAGMA application_id={APP_ID}')
                self.db.execute('PRAGMA user_version=1')
            if version < 2:
                # Additive migration: existing content, identities, attempts and
                # scheduling state are retained byte-for-byte in their tables.
                self.db.execute('CREATE TABLE provider_cooldowns (provider TEXT PRIMARY KEY, deadline_ns INTEGER NOT NULL)')
                for job in self.db.execute("SELECT config,next_due_ns FROM jobs WHERE health IN ('rate_limited','retry_deadline_unrepresentable')").fetchall():
                    provider = json.loads(job['config']).get('provider')
                    if provider in _PROVIDERS:
                        self.db.execute('INSERT INTO provider_cooldowns VALUES (?,?) ON CONFLICT(provider) DO UPDATE '
                                        'SET deadline_ns=MAX(provider_cooldowns.deadline_ns,excluded.deadline_ns)',
                                        (provider, job['next_due_ns']))
                self.db.execute('PRAGMA user_version=2')
            if version < 3:
                self.db.execute('ALTER TABLE snapshots ADD COLUMN freshness_descriptor TEXT')
                for row in self.db.execute('SELECT * FROM snapshots').fetchall():
                    document = (json.loads(row['normalized']) if row['normalized'] else
                                {'source': row['provider'], 'dataset': row['dataset'], 'scope': json.loads(row['scope']),
                                 'source_snapshot_at': row['source_snapshot_at'], 'source_generated_at': row['source_generated_at']})
                    descriptor = describe(document)
                    descriptor['source_clock_ahead_at_receive'] = assess(descriptor, row['received_at_ns'] // 1_000_000_000)['state'] == 'source_clock_ahead'
                    self.db.execute('UPDATE snapshots SET freshness_descriptor=? WHERE id=?', (encode(descriptor), row['id']))
                self.db.execute('CREATE TABLE http_validators (job_key TEXT PRIMARY KEY REFERENCES jobs(job_key), '
                                'snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), normalizer_revision TEXT, '
                                'etag TEXT, last_modified TEXT, generation INTEGER NOT NULL)')
                # Snapshot headers were committed together with their content.
                # Legacy job headers can belong to a different response.
                for row in self.db.execute('SELECT s.* FROM current_datasets c JOIN snapshots s ON s.id=c.snapshot_id').fetchall():
                    document = json.loads(row['normalized']) if row['normalized'] else {}
                    headers = json.loads(row['headers'])
                    self.db.execute('INSERT INTO http_validators VALUES (?,?,?,?,?,1)',
                                    (row['job_key'], row['id'], document.get('normalizer_revision'),
                                     headers.get('etag'), headers.get('last-modified')))
                self.db.execute('UPDATE jobs SET etag=NULL,last_modified=NULL')
                self.db.execute('PRAGMA user_version=3')
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _publish(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('UPDATE snapshots SET available_at_ns=? WHERE available_at_ns IS NULL', (time.time_ns(),))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def register(self, job):
        identity = {k: job[k] for k in ('provider', 'dataset', 'region', 'realm_slug', 'connected_realm_id',
                                        'item_id', 'pet_species_id', 'detail', 'expected_realm_id') if k in job}
        key = fingerprint(identity)
        self.db.execute('INSERT INTO jobs(job_key,config) VALUES (?,?) ON CONFLICT(job_key) DO UPDATE SET config=excluded.config', (key, encode(job)))
        return key

    def claim(self, key, now_ns, *, force=False):
        token = uuid.uuid4().hex
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM jobs WHERE job_key=?', (key,)).fetchone()
            if not row or (row['lease_until_ns'] or 0) > now_ns or (not force and row['next_due_ns'] > now_ns):
                self.db.commit()
                return None
            interval = json.loads(row['config'])['interval_seconds'] * 1_000_000_000
            if row['last_finished_ns'] is not None and now_ns > row['next_due_ns'] + interval:
                self.db.execute('INSERT OR IGNORE INTO gaps(job_key,start_ns,end_ns,reason) VALUES (?,?,?,?)',
                                (key, row['next_due_ns'], now_ns, 'worker_not_observed_at_scheduled_time'))
            self.db.execute('UPDATE jobs SET lease_token=?,lease_until_ns=?,last_started_ns=? WHERE job_key=?',
                            (token, now_ns + 180 * 1_000_000_000, now_ns, key))
            self.db.commit()
            return token
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, key, token, *, health, next_due_ns, failed=False, headers=None):
        row = self.db.execute('SELECT lease_token FROM jobs WHERE job_key=?', (key,)).fetchone()
        if not row or row[0] != token:
            raise MarketError('market_worker_lease_lost')
        self.db.execute('UPDATE jobs SET health=?,next_due_ns=?,last_finished_ns=?,failures=CASE WHEN ? THEN failures+1 ELSE 0 END, '
                        'lease_token=NULL,lease_until_ns=NULL WHERE job_key=? AND lease_token=?',
                        (health, next_due_ns, time.time_ns(), int(failed), key, token))

    def _check_lease(self, key, token):
        lease = self.db.execute('SELECT lease_token,lease_until_ns FROM jobs WHERE job_key=?', (key,)).fetchone()
        if not lease or lease['lease_token'] != token or (lease['lease_until_ns'] or 0) <= time.time_ns():
            raise MarketError('market_worker_lease_lost')

    def _set_validators(self, key, snapshot_id, normalized, headers):
        self.db.execute('INSERT INTO http_validators VALUES (?,?,?,?,?,1) ON CONFLICT(job_key) DO UPDATE SET '
                        'snapshot_id=excluded.snapshot_id,normalizer_revision=excluded.normalizer_revision,'
                        'etag=excluded.etag,last_modified=excluded.last_modified,generation=http_validators.generation+1',
                        (key, snapshot_id, normalized.get('normalizer_revision'), headers.get('etag'), headers.get('last-modified')))

    def conditional_request(self, key, normalizer_revision):
        current = self.current(key)
        cached = self.db.execute('SELECT * FROM http_validators WHERE job_key=?', (key,)).fetchone()
        if (not current or not current['cache_available'] or current['source_freshness_state'] == 'source_clock_ahead'
                or not cached or cached['snapshot_id'] != current['id']
                or cached['normalizer_revision'] != normalizer_revision or not (cached['etag'] or cached['last_modified'])):
            return None
        return {**dict(cached), 'sha256': current['sha256']}

    def confirm_not_modified(self, key, conditional, response, *, normalizer_revision, lease_token):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._check_lease(key, lease_token)
            current = self.current(key)
            if not current or not current['cache_available']:
                raise MarketError('not_modified_without_usable_content')
            actual = self.conditional_request(key, normalizer_revision)
            if conditional is None or actual is None or conditional != actual:
                raise MarketError('unverified_not_modified_response')
            # A conflicting validator cannot confirm the request's representation.
            etag = response.headers.get('etag')
            if etag and conditional['etag'] and etag.removeprefix('W/') != conditional['etag'].removeprefix('W/'):
                raise MarketError('not_modified_validator_mismatch')
            if (not conditional['etag'] and response.headers.get('last-modified')
                    and response.headers['last-modified'] != conditional['last_modified']):
                raise MarketError('not_modified_validator_mismatch')
            self.db.execute('INSERT INTO attempts(job_key,received_at_ns,status,code,http_status,snapshot_id) VALUES (?,?,?,?,?,?)',
                            (key, response.received_at_ns, 'unchanged', 'conditional_not_modified', 304, current['id']))
            self.db.commit()
            return {'status': 'unchanged', 'snapshot_id': current['id']}
        except BaseException:
            self.db.rollback()
            raise

    def invalidate_validators(self, key, *, lease_token):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._check_lease(key, lease_token)
            self.db.execute('DELETE FROM http_validators WHERE job_key=?', (key,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def set_provider_cooldown(self, provider, deadline_ns):
        """Persist the longest server cooldown, shared by every job/worker."""
        if (not isinstance(provider, str) or provider not in _PROVIDERS
                or type(deadline_ns) is not int or deadline_ns < 0):
            raise MarketError('invalid_provider_cooldown')
        deadline_ns = min(deadline_ns, MAX_NS)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO provider_cooldowns(provider,deadline_ns) VALUES (?,?) '
                            'ON CONFLICT(provider) DO UPDATE SET deadline_ns=MAX(provider_cooldowns.deadline_ns,excluded.deadline_ns)',
                            (provider, deadline_ns))
            deadline = self.db.execute('SELECT deadline_ns FROM provider_cooldowns WHERE provider=?', (provider,)).fetchone()[0]
            self.db.commit()
            return deadline
        except BaseException:
            self.db.rollback()
            raise

    def reserve(self, provider, points, now_ns):
        if not isinstance(provider, str) or provider not in _RATE_LIMITS:
            raise MarketError('invalid_market_rate_provider')
        hourly, per_second = _RATE_LIMITS[provider]
        if type(points) is not int or not 1 <= points <= hourly:
            raise MarketError('invalid_market_rate_cost')
        integer(now_ns)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            cooldown = self.db.execute('SELECT deadline_ns FROM provider_cooldowns WHERE provider=?', (provider,)).fetchone()
            if cooldown is not None and cooldown['deadline_ns'] > now_ns:
                self.db.commit()
                return cooldown['deadline_ns']
            self.db.execute('DELETE FROM rate_usage WHERE at_ns<=?', (now_ns - 3600 * 1_000_000_000,))
            rows = self.db.execute('SELECT at_ns,points FROM rate_usage WHERE provider=? ORDER BY at_ns', (provider,)).fetchall()
            if sum(r['points'] for r in rows) + points > hourly:
                deadline = min(rows[0]['at_ns'] + 3600 * 1_000_000_000, MAX_NS)
            elif sum(1 for r in rows if r['at_ns'] > now_ns - 1_000_000_000) >= per_second:
                deadline = min(now_ns + 1_000_000_000, MAX_NS)
            else:
                self.db.execute('INSERT INTO rate_usage VALUES (?,?,?)', (provider, now_ns, points))
                deadline = None
            self.db.commit()
            return deadline
        except BaseException:
            self.db.rollback()
            raise

    def reject(self, key, code, *, http_status=None, status='quarantined', received_at_ns=None):
        self.db.execute('INSERT INTO attempts(job_key,received_at_ns,status,code,http_status) VALUES (?,?,?,?,?)',
                        (key, received_at_ns or time.time_ns(), status, code, http_status))

    def current(self, key, *, now_ns=None):
        row = self.db.execute('SELECT s.* FROM snapshots s JOIN current_datasets c ON c.snapshot_id=s.id WHERE c.job_key=?', (key,)).fetchone()
        if not row: return None
        result = dict(row)
        result['cache_available'] = row['content_status'] == 'available' and row['expires_at_ns'] > (now_ns or time.time_ns()) and row['available_at_ns'] is not None
        assessment = assess(json.loads(row['freshness_descriptor']) if row['freshness_descriptor'] else None,
                            (now_ns or time.time_ns()) // 1_000_000_000)
        result['source_freshness_state'] = assessment['state']
        result['usable'] = result['cache_available'] and row['completeness'] == 'complete' and assessment['current_usable']
        return result

    def accept(self, key, body, normalized, response, policy, *, fault=None, lease_token=None):
        hit = fault or (lambda _: None)
        if not policy['network_allowed'] or policy['retention_seconds'] <= 0:
            raise MarketError('market_source_policy_blocked')
        sha = hashlib.sha256(body).hexdigest()
        payload = encode(normalized)
        descriptor = describe(normalized)
        descriptor['source_clock_ahead_at_receive'] = assess(descriptor, response.received_at_ns // 1_000_000_000)['state'] == 'source_clock_ahead'
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if lease_token is not None:
                self._check_lease(key, lease_token)
            self.db.execute('INSERT OR IGNORE INTO policies VALUES (?,?)', (policy['fingerprint'], encode(policy)))
            current = self.current(key)
            if (current and current['sha256'] == sha and current['policy_fingerprint'] == policy['fingerprint']
                    and current['normalized'] == payload and current['cache_available']
                    and json.loads(current['freshness_descriptor'] or '{}').get('source_clock_ahead_at_receive', False)
                        == descriptor['source_clock_ahead_at_receive']):
                self._set_validators(key, current['id'], normalized, response.headers)
                self.db.execute('INSERT INTO attempts(job_key,received_at_ns,status,http_status,snapshot_id,bytes) VALUES (?,?,?,?,?,?)',
                                (key, response.received_at_ns, 'unchanged', response.status, current['id'], len(body)))
                self.db.commit()
                return {'status': 'unchanged', 'snapshot_id': current['id'], 'records': current['record_count']}
            cursor = self.db.execute('INSERT INTO snapshots(job_key,provider,sha256,scope,dataset,raw,normalized,record_count,source_snapshot_at,source_generated_at,received_at_ns,expires_at_ns,content_status,policy_fingerprint,headers,completeness) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key, normalized['source'], sha, encode(normalized['scope']), normalized['dataset'], body, payload,
                 len(normalized['records']), normalized['source_snapshot_at'], normalized['source_generated_at'], response.received_at_ns,
                 response.received_at_ns + policy['retention_seconds'] * 1_000_000_000, 'available', policy['fingerprint'], encode(response.headers), normalized['completeness']))
            sid = cursor.lastrowid
            self.db.execute('UPDATE snapshots SET freshness_descriptor=? WHERE id=?', (encode(descriptor), sid))
            hit('after_snapshot')
            complete = normalized['completeness'] == 'complete'
            state = 'accepted' if complete else 'partial_kept_last_good'
            promote = complete
            if complete and current:
                previous = json.loads(current['freshness_descriptor']) if current['freshness_descriptor'] else None
                previous_assessment = assess(previous, response.received_at_ns // 1_000_000_000)
                incoming_assessment = assess(descriptor, response.received_at_ns // 1_000_000_000)
                ordering = compare(previous, descriptor)
                if incoming_assessment['state'] == 'source_clock_ahead':
                    promote, state = False, 'source_clock_ahead_kept_last_good'
                elif previous_assessment['state'] != 'source_clock_ahead' and ordering in {'regression', 'missing_clock', 'incomparable'}:
                    promote, state = False, 'source_' + ordering + '_kept_last_good'
            if promote:
                # A legitimate zero-quantity response can omit its current scan.
                # It must not erase an already observed positive scan watermark.
                if current and previous_assessment['state'] != 'source_clock_ahead':
                    for name in descriptor.get('optional_ordering_clocks', []):
                        old = (previous or {}).get('ordering_clocks', {}).get(name)
                        incoming = descriptor['ordering_clocks'].get(name)
                        if (type(old) is int and old >= 0 and (incoming is None or
                                descriptor['role'] == 'no_quote' and incoming < old)):
                            descriptor['ordering_clocks'][name] = old
                            descriptor['ordering_watermark_inherited_from_snapshot_id'] = current['id']
                    self.db.execute('UPDATE snapshots SET freshness_descriptor=? WHERE id=?', (encode(descriptor), sid))
                self.db.execute('INSERT INTO current_datasets VALUES (?,?) ON CONFLICT(job_key) DO UPDATE SET snapshot_id=excluded.snapshot_id', (key, sid))
                self._set_validators(key, sid, normalized, response.headers)
            self.db.execute('INSERT INTO attempts(job_key,received_at_ns,status,http_status,snapshot_id,bytes) VALUES (?,?,?,?,?,?)',
                            (key, response.received_at_ns, state, response.status, sid, len(body)))
            hit('before_commit')
            self.db.commit()
            hit('after_commit')
            self._publish()
            return {'status': state, 'snapshot_id': sid, 'records': len(normalized['records'])}
        except BaseException:
            self.db.rollback()
            raise

    def expire(self, *, now_ns=None):
        now_ns = now_ns or time.time_ns()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            count = self.db.execute("SELECT COUNT(*) FROM snapshots WHERE expires_at_ns<=? AND content_status='available'", (now_ns,)).fetchone()[0]
            self.db.execute("UPDATE snapshots SET raw=NULL,normalized=NULL,content_status='replay_unavailable_due_to_retention' WHERE expires_at_ns<=? AND content_status='available'", (now_ns,))
            self.db.execute('DELETE FROM realm_mappings WHERE expires_at_ns<=?', (now_ns,))
            self.db.commit()
            return count
        except BaseException:
            self.db.rollback()
            raise

    def reference(self, ref, snapshot_id):
        self.db.execute('INSERT INTO input_references VALUES (?,?,?)', (ref, snapshot_id, time.time_ns()))

    def replay_input(self, ref, *, decision_at_ns=None):
        row = self.db.execute('SELECT s.* FROM snapshots s JOIN input_references r ON r.snapshot_id=s.id WHERE r.ref=?', (ref,)).fetchone()
        if not row: return {'status': 'unknown_input_reference'}
        if row['normalized'] is None or row['expires_at_ns'] <= time.time_ns():
            return {'status': 'replay_unavailable_due_to_retention', 'snapshot_id': row['id'], 'sha256': row['sha256']}
        if row['available_at_ns'] is None or (decision_at_ns is not None and row['available_at_ns'] > decision_at_ns):
            return {'status': 'not_available_at_decision'}
        return {'status': 'available', 'normalized': json.loads(row['normalized']), 'snapshot_id': row['id']}

    def report(self, keys):
        result = []
        as_of = time.time_ns()
        for key in keys:
            job = self.db.execute('SELECT * FROM jobs WHERE job_key=?', (key,)).fetchone()
            if not job: continue
            config = json.loads(job['config'])
            current = self.current(key, now_ns=as_of)
            entry = {'job_key': key, 'provider': config['provider'], 'dataset': config['dataset'],
                     'health': job['health'], 'next_due_ns': job['next_due_ns'], 'failures': job['failures'],
                     'as_of_ns': as_of}
            if current:
                entry['snapshot'] = {k: current[k] for k in ('id', 'sha256', 'record_count', 'source_snapshot_at', 'source_generated_at', 'received_at_ns', 'available_at_ns', 'expires_at_ns', 'content_status', 'usable', 'cache_available')}
                entry['snapshot']['scope'] = json.loads(current['scope'])
                entry['snapshot']['completeness'] = current['completeness']
                entry['snapshot'].update(_diagnostic_projection(current['normalized']))
                for field in ('source_snapshot_at', 'source_generated_at'):
                    entry['snapshot'][field + '_age_seconds'] = (as_of // 1_000_000_000 - current[field]
                                                                 if current[field] is not None else None)
                entry['snapshot']['received_age_seconds'] = (as_of - current['received_at_ns']) / 1_000_000_000
                entry['snapshot']['available_age_seconds'] = ((as_of - current['available_at_ns']) / 1_000_000_000
                                                              if current['available_at_ns'] is not None else None)
                assessment = assess(json.loads(current['freshness_descriptor']) if current['freshness_descriptor'] else None,
                                    as_of // 1_000_000_000)
                entry['snapshot']['source_freshness'] = assessment
                if not current['cache_available']:
                    state = 'expired_or_unavailable'
                elif not assessment['current_usable']:
                    state = assessment['state']
                elif job['health'] not in {'accepted', 'unchanged'}:
                    state = 'last_good_after_unsuccessful_refresh'
                elif job['next_due_ns'] <= as_of:
                    state = 'refresh_due'
                else:
                    state = 'current_observation'
                entry['snapshot']['freshness_state'] = state
                entry['snapshot']['capabilities_currently_usable'] = state == 'current_observation'
            entry['gap_count'] = self.db.execute('SELECT COUNT(*) FROM gaps WHERE job_key=?', (key,)).fetchone()[0]
            result.append(entry)
        return result
