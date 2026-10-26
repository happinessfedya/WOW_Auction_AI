"""Offline evaluation on local observations and changing market snapshots.

Market data is read in SQLite read-only mode. Derived prices/results stay in
memory: this component does not turn a four-hour source cache into an archive.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time
from contextlib import closing

from .knowledge import KnowledgeError
from .knowledge_craftsim import assess_profile
from .knowledge_engine import evaluate_scenarios
from .market_common import MarketError
from .market_config import load_market_config
from .market_freshness import assess, describe
from .market_policy import source_policy


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def commodity_quotes(config_path, *, now):
    """Only region commodity minima can join these base item IDs automatically.

Realm equipment variants and regional statistics are not substituted. A minimum
is an aggregate observation, with no assertion of quantity or execution.
"""
    result = {'status': 'market_unavailable', 'items': {}, 'snapshots': [],
              'price_kind': 'tsm_commodity_aggregate_minimum', 'executable': False}
    try:
        config = load_market_config(config_path)
        if not any(j['enabled'] and j['provider'] == 'tsm' and j['dataset'] == 'commodities'
                   and j['region'] == config['region'] for j in config['jobs']):
            return dict(result, status='commodity_source_disabled')
        database = Path(config['database'])
        if not database.is_file() or database.is_symlink():
            return result
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA trusted_schema=OFF')
            if db.execute('PRAGMA application_id').fetchone()[0] != 0x57414D32:
                return dict(result, status='market_database_identity_invalid')
            # schema2 has no persisted descriptor. Derive it from its original
            # normalized clocks, also retaining anomalies at initial receipt.
            if db.execute('PRAGMA user_version').fetchone()[0] not in (2, 3):
                return dict(result, status='market_database_schema_unsupported')
            rows = db.execute("SELECT s.* FROM snapshots s JOIN current_datasets c ON c.snapshot_id=s.id "
                              "WHERE s.provider='tsm' AND s.dataset='commodities'").fetchall()
            for row in rows:
                if (row['content_status'] != 'available' or row['expires_at_ns'] <= now*10**9
                        or row['available_at_ns'] is None or row['available_at_ns'] > now*10**9
                        or row['received_at_ns'] > now*10**9 or row['completeness'] != 'complete'):
                    continue
                policy = db.execute('SELECT payload FROM policies WHERE fingerprint=?', (row['policy_fingerprint'],)).fetchone()
                if not policy or not json.loads(policy[0]).get('network_allowed') or not source_policy('tsm')['network_allowed']:
                    continue
                document = json.loads(row['normalized'])
                scope = document.get('scope', {})
                if (document.get('source') != 'tsm' or document.get('dataset') != 'commodities'
                        or scope.get('region') != config['region'] or scope.get('kind') != 'commodity'):
                    continue
                descriptor = json.loads(row['freshness_descriptor']) if 'freshness_descriptor' in row.keys() and row['freshness_descriptor'] else describe(document)
                if not assess(descriptor, now)['current_usable'] or not assess(descriptor, row['received_at_ns']//10**9)['current_usable']:
                    continue
                result['snapshots'].append({'id': row['id'], 'sha256': row['sha256'],
                    'source_snapshot_at': row['source_snapshot_at'], 'expires_at_ns': row['expires_at_ns'],
                    'policy_fingerprint': row['policy_fingerprint'], 'scope': scope})
                for entry in document.get('records', []):
                    item_id, price = entry.get('item_id'), entry.get('unit_price_copper')
                    if (type(item_id) is not int or item_id <= 0 or type(price) is not int or price <= 0
                            or entry.get('record_type') != 'aggregate' or entry.get('quote_status') != 'aggregate_minimum'):
                        continue
                    # Multiple applicable current snapshots are ambiguous, even
                    # when prices happen to agree. Do not silently pick one.
                    key = str(item_id)
                    if key in result['items']:
                        result['items'][key] = None
                    else:
                        result['items'][key] = price
        result['items'] = {k: v for k, v in result['items'].items() if v is not None}
        result['status'] = 'aggregate_observations' if result['snapshots'] else 'no_eligible_market_snapshot'
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error, MarketError):
        return dict(result, status='market_read_or_schema_error', items={}, snapshots=[])


def _links(raw):
    """Collect exact exported links; only the commodity-ID join supplies prices."""
    result = {}
    def visit(value, depth=0):
        if depth > 30:
            raise KnowledgeError('engine_item_structure_limit')
        if isinstance(value, dict):
            item_id, link = value.get('itemID'), value.get('itemLink')
            if type(item_id) is int and item_id > 0 and isinstance(link, str):
                if link in result and result[link] != str(item_id):
                    raise KnowledgeError('engine_item_identity_conflict')
                result[link] = str(item_id)
            for child in value.values():
                visit(child, depth+1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth+1)
    visit(raw)
    return result


class EngineEvaluator:
    def __init__(self, store, market_config):
        self.store, self.market_config = store, Path(market_config)
        self._cache = {}

    def run(self, local_result, *, now=None, build=None, details=False):
        now = int(time.time()) if now is None else now
        if type(now) is not int or now <= 0:
            raise KnowledgeError('engine_evaluation_clock_invalid')
        quotes = commodity_quotes(self.market_config, now=now)
        market_key = _hash({'snapshots': quotes['snapshots'], 'status': quotes['status'], 'items': quotes['items']})
        # Only artifacts corresponding to this poll's configured input files.
        # A missing latest file never silently selects arbitrary older history.
        ids = sorted({row['artifact_id'] for row in local_result.get('sources', [])
                      if row.get('kind') == 'craftsim' and type(row.get('artifact_id')) is int})
        reports, active_cache = [], {}
        for artifact_id in ids:
            row = self.store.db.execute("SELECT payload FROM artifacts WHERE id=? AND kind='craftsim'", (artifact_id,)).fetchone()
            if row is None:
                continue
            payload = json.loads(row[0])
            engine = payload.get('engine')
            if not isinstance(engine, dict):
                continue
            for record in engine['records']:
                profiles = record['scenarios']
                raw = [p['raw'] for p in profiles]
                key = _hash({'artifact': artifact_id, 'actor': record['actor_key'], 'recipe': record['recipe_id'],
                             'profiles': [p['id'] for p in profiles], 'market': market_key})
                cached = key in self._cache
                result = self._cache.get(key)
                if result is None:
                    links = _links(raw)
                    prices = {link: quotes['items'][item_id] for link, item_id in links.items() if item_id in quotes['items']}
                    result = evaluate_scenarios(raw, prices)
                active_cache[key] = result
                # Freshness is reassessed every pass, including when math and
                # source bytes are unchanged. Historical math remains available.
                contexts = [assess_profile(p, now=now, current_build=build) for p in profiles]
                report = {'artifact_id': artifact_id, 'recipe_id': record['recipe_id'], 'actor_key': record['actor_key'],
                          'calculation_status': result['status'], 'evaluated_count': result['evaluated_count'],
                          'rejected_count': result['rejected_count'], 'recomputed': not cached,
                          'fingerprint': key, 'attempt_status': record['attempt_status'],
                          'context_statuses': sorted({p['context_status'] for p in contexts}),
                          'capture_statuses_now': sorted({p['status'] for p in contexts}),
                          'historical_calculation_only': True, 'coverage_complete': False,
                          'execution_eligible': False, 'recommendation_eligible': False}
                if details:
                    report['calculation'] = result
                reports.append(report)
        # Never retain a stale price-derived cache after expiry or input removal.
        self._cache = active_cache
        return {'status': 'evaluated_available_engine_observations' if reports else 'no_engine_observations',
                'market_status': quotes['status'], 'market_snapshot_fingerprint': market_key,
                'price_kind': quotes['price_kind'], 'records': reports,
                'evaluated_scenarios': sum(r['evaluated_count'] for r in reports),
                'market_results_persisted': False, 'source_retention_respected': True,
                'manual_recipe_selection_required': False, 'game_actions_performed': False,
                'scope_limitations': ['finite_observed_allocations', 'commodity_aggregate_prices_only',
                                      'realm_variants_require_separate_verified_quotes',
                                      'current_character_state_after_export_unknown']}
