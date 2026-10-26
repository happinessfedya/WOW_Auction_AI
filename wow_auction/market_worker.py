"""Foreground, restartable market polling. It never launches or controls WoW."""
import json
import hashlib
from pathlib import Path
import time

from .market_common import MarketError
from .market_config import load_secrets, validate_secrets
from .market_http import HttpClient, retry_deadline
from .market_policy import source_policy
from . import market_common, market_freshness, market_tsm, market_undermine

MAX_NS = 2**63 - 1


class MarketWorker:
    def __init__(self, store, config, *, client=None, secrets=None):
        self.store, self.config = store, config
        self.client = client or HttpClient()
        # Do not inspect optional provider credentials while constructing a
        # public-data worker or a read-only diagnostic command.
        self._injected_secrets = secrets
        self._credential_state = {'status': 'not_inspected', 'configured': None}

    def _provider_secrets(self, provider):
        """Called immediately before a request, after enabled/policy checks."""
        if provider != 'undermine':
            return {}
        try:
            values = (load_secrets(self.config) if self._injected_secrets is None
                      else validate_secrets(self._injected_secrets))
        except MarketError:
            self._credential_state = {'status': 'invalid', 'configured': None}
            raise
        self._credential_state = {'status': 'loaded' if values['undermine_api_key'] else 'missing',
                                  'configured': bool(values['undermine_api_key'])}
        return values

    def credential_diagnostics(self):
        """Report prior inspection only; status never opens a secret file."""
        return {'undermine': dict(self._credential_state)}

    def jobs(self):
        jobs = []
        for configured in self.config['jobs']:
            job = dict(configured)
            # Cross-provider equivalence is not inferred from a matching slug.
            job.pop('connected_realm_id', None)
            job.pop('expected_realm_id', None)
            key = self.store.register(job)
            jobs.append((key, job))
        return jobs

    def _request_args(self, job):
        args = {k: job[k] for k in ('region', 'dataset', 'realm_slug', 'connected_realm_id',
                                   'item_id', 'pet_species_id', 'detail') if k in job}
        if not job['dataset'].startswith('realm_') and job['dataset'] not in {'realm', 'auctions', 'connected_realm'}:
            args.pop('realm_slug', None)
            args.pop('connected_realm_id', None)
        return args

    def _url(self, job):
        args = self._request_args(job)
        if job['provider'] == 'tsm':
            return market_tsm.build_url(**{k: args[k] for k in ('region', 'dataset', 'realm_slug') if k in args})
        if job['provider'] == 'blizzard':
            raise MarketError('blizzard_disabled_by_user')
        return market_undermine.build_url(**args)

    def _normalize(self, job, response):
        args = self._request_args(job)
        if job['provider'] == 'tsm':
            normalized = market_tsm.normalize_tsm(response.body, **args)
        elif job['provider'] == 'blizzard':
            raise MarketError('blizzard_disabled_by_user')
        else:
            normalized = market_undermine.normalize_undermine(response.body, **args)
        if normalized['source'] != job['provider'] or normalized['dataset'] != job['dataset']:
            raise MarketError('market_adapter_identity_mismatch')
        snapshot = normalized.get('source_snapshot_at')
        normalized['source_clock_ahead'] = snapshot is not None and snapshot > response.received_at_ns // 1_000_000_000
        normalized['normalizer_revision'] = self._normalizer_revision(job['provider'])
        return normalized

    def _normalizer_revision(self, provider):
        # This source-distributed application invalidates conditional reuse when
        # normalizing code changes. The old observation remains immutable.
        adapter = {'tsm': market_tsm, 'undermine': market_undermine}.get(provider)
        if adapter is None:
            raise MarketError('blizzard_disabled_by_user')
        digest = hashlib.sha256()
        for path in (Path(market_common.__file__), Path(market_freshness.__file__), Path(adapter.__file__), Path(__file__)):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _download(self, job, key):
        provider = job['provider']
        headers = {'Accept': 'text/csv' if provider == 'tsm' else 'application/json'}
        if provider == 'blizzard': raise MarketError('blizzard_disabled_by_user')
        if provider == 'undermine':
            secrets = self._provider_secrets(provider)
            if not secrets['undermine_api_key']: raise MarketError('undermine_credentials_required')
            headers['Authorization'] = 'ApiKey ' + secrets['undermine_api_key']
        conditional = self.store.conditional_request(key, self._normalizer_revision(provider))
        if conditional:
            if conditional['etag']: headers['If-None-Match'] = conditional['etag']
            if conditional['last_modified']: headers['If-Modified-Since'] = conditional['last_modified']
        url = self._url(job)
        response = self.client.request(url, headers=headers)
        return response, conditional

    def run_job(self, key, job, *, force=False):
        now = time.time_ns()
        saved = self.store.db.execute('SELECT * FROM jobs WHERE job_key=?', (key,)).fetchone()
        if saved['health'] in {'rate_limited', 'retry_scheduled', 'retry_deadline_unrepresentable'} and saved['next_due_ns'] > now:
            force = False
        token = self.store.claim(key, now, force=force)
        if token is None: return {'provider': job['provider'], 'dataset': job['dataset'], 'status': 'not_due_or_leased'}
        due = now + job['interval_seconds'] * 1_000_000_000
        health, failed, headers = 'unavailable', False, {}
        try:
            if job['provider'] == 'blizzard':
                raise MarketError('blizzard_disabled_by_user')
            policy = source_policy(job['provider'])
            if not job['enabled']:
                health = 'disabled'
                return {'provider': job['provider'], 'dataset': job['dataset'], 'status': health}
            if not policy['network_allowed']:
                raise MarketError('source_policy_' + policy['status'])
            if job['provider'] == 'undermine' and (job['dataset'] == 'realm_stats' or job.get('detail') not in (None, 'base')) and not self.config['allow_paid_undermine']:
                raise MarketError('paid_capability_not_enabled')
            cost = market_undermine.request_cost(job['dataset'], job.get('detail')) if job['provider'] == 'undermine' else 1
            budget_due = self.store.reserve(job['provider'], cost, now)
            if budget_due:
                due, health = budget_due, 'rate_limited'
                return {'provider': job['provider'], 'dataset': job['dataset'], 'status': health}
            response, conditional = self._download(job, key)
            headers = response.headers
            if response.status == 304:
                result = self.store.confirm_not_modified(key, conditional, response,
                    normalizer_revision=self._normalizer_revision(job['provider']), lease_token=token)
                health = 'unchanged'
                return {'provider': job['provider'], 'dataset': job['dataset'], **result}
            if response.status != 200:
                failed = True
                health = 'authorization_unavailable' if response.status in (401, 403) else 'retry_scheduled'
                if response.status == 429: health = 'rate_limited'
                retry_base = max(now, response.received_at_ns) if response.status == 429 else now
                # A server delay starts when its response is observed, not when
                # our possibly slow request began. Round up rather than retry early.
                retry_seconds = ((retry_base + 999_999_999) // 1_000_000_000
                                 if response.status == 429 else retry_base // 1_000_000_000)
                minimum = retry_deadline(response.headers.get('retry-after'), retry_seconds)
                retry = retry_base + min(3600, 60 * 2**min(saved['failures'], 6)) * 1_000_000_000
                due = max(retry, minimum * 1_000_000_000 if minimum is not None else 0)
                if response.status in (401, 403) or 400 <= response.status < 500 and response.status != 429:
                    due = max(due, now + job['interval_seconds'] * 1_000_000_000)
                if due > MAX_NS:
                    due, health = MAX_NS, 'retry_deadline_unrepresentable'
                if response.status == 429:
                    due = self.store.set_provider_cooldown(job['provider'], due)
                self.store.reject(key, 'market_http_' + str(response.status), http_status=response.status, status=health,
                                  received_at_ns=response.received_at_ns)
                return {'provider': job['provider'], 'dataset': job['dataset'], 'status': health, 'http_status': response.status}
            content_type = response.headers.get('content-type', '').lower()
            if ('html' in content_type or (job['provider'] != 'tsm' and content_type and 'json' not in content_type)):
                raise MarketError('unexpected_market_content_type')
            normalized = self._normalize(job, response)
            result = self.store.accept(key, response.body, normalized, response, policy, lease_token=token)
            health = result['status']
            # Cache-Control is freshness guidance, not a publication timestamp.
            cache = response.headers.get('cache-control', '')
            for directive in cache.split(','):
                name, _, value = directive.strip().partition('=')
                if name.lower() == 'max-age' and value.isascii() and value.isdecimal() and len(value) <= 8:
                    due = max(due, now + int(value) * 1_000_000_000)
            return {'provider': job['provider'], 'dataset': job['dataset'], **result,
                    'body_bytes': len(response.body), 'wire_bytes': response.wire_bytes,
                    'elapsed_seconds': response.elapsed_seconds}
        except MarketError as exc:
            health, failed = exc.code, True
            if exc.code in {'unverified_not_modified_response', 'not_modified_validator_mismatch', 'not_modified_without_usable_content'}:
                try:
                    self.store.invalidate_validators(key, lease_token=token)
                except MarketError as cleanup_error:
                    if cleanup_error.code != 'market_worker_lease_lost':
                        raise
                    self.store.reject(key, cleanup_error.code, status=cleanup_error.code)
                    return {'provider': job['provider'], 'dataset': job['dataset'],
                            'status': cleanup_error.code, 'code': cleanup_error.code}
            if exc.code in {'market_network_unavailable', 'market_response_read_failed', 'market_request_deadline_exceeded',
                            'unverified_not_modified_response', 'not_modified_validator_mismatch', 'not_modified_without_usable_content'}:
                health = 'retry_scheduled'
                due = now + min(3600, 60 * 2**min(saved['failures'], 6)) * 1_000_000_000
            self.store.reject(key, exc.code, status=health)
            return {'provider': job['provider'], 'dataset': job['dataset'], 'status': health, 'code': exc.code}
        finally:
            try:
                self.store.finish(key, token, health=health, next_due_ns=min(due, MAX_NS), failed=failed)
            except MarketError as exc:
                # A superseded worker cannot overwrite its successor's health
                # or stop other independent jobs when releasing an old lease.
                if exc.code != 'market_worker_lease_lost':
                    raise

    def scan(self, *, force=False):
        expired = self.store.expire()
        results = [self.run_job(key, job, force=force) for key, job in self.jobs()]
        return {'imports': results, 'expired_content_records': expired}

    def resolve_realm(self):
        # Read retained evidence only. No authentication or network side effect.
        region, slug = self.config['region'], self.config['realm_slug']
        candidates = []
        for row in self.store.db.execute(
                'SELECT s.* FROM current_datasets c JOIN snapshots s ON s.id=c.snapshot_id '
                "WHERE s.provider='undermine' AND s.available_at_ns IS NOT NULL "
                "AND s.expires_at_ns>? AND s.normalized IS NOT NULL AND s.completeness='complete'",
                (time.time_ns(),)):
            evidence = json.loads(row['normalized'])
            scopes = [evidence['scope']] + [r['market_scope'] for r in evidence['records'] if 'market_scope' in r]
            for market_scope in scopes:
                if (market_scope['kind'] == 'provider_connected_realm' and market_scope['region'] == region
                        and slug in market_scope['realm_slugs']):
                    candidates.append({'scope': market_scope, 'snapshot_id': row['id'], 'sha256': row['sha256'],
                                       'received_at_ns': row['received_at_ns'], 'available_at_ns': row['available_at_ns']})
        result = {'region': region, 'realm_slug': slug, 'blizzard_api_used': False}
        groups = {candidate['scope']['provider_group_key'] for candidate in candidates}
        if len(groups) > 1:
            return {**result, 'status': 'provider_membership_conflict', 'evidence': candidates}
        if candidates:
            return {**result, 'status': 'provider_membership_observed', 'evidence': candidates,
                    'cross_provider_equivalence': 'unverified'}
        return {**result, 'status': 'undermine_membership_evidence_required'}
