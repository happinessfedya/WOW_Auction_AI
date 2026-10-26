"""Validate autonomous CraftSim scenario observations, separately from UI events."""
from __future__ import annotations

import hashlib
from typing import Any

from .knowledge_craftsim import CraftSimError, _profile, assess_profile


def _table(value, code):
    if not isinstance(value, dict):
        raise CraftSimError(code)
    return value


def _integer(value, code, *, minimum=0):
    if type(value) is not int or value < minimum or value > 2**53:
        raise CraftSimError(code)
    return value


def _clock(value, observed_at):
    _integer(value, 'engine_capture_clock_invalid', minimum=1)
    if value > observed_at:
        raise CraftSimError('engine_capture_clock_after_receive')
    return value


def _actor(value):
    value = _table(value, 'engine_actor_invalid')
    guid = value.get('player_guid')
    if not isinstance(guid, str) or not 1 <= len(guid) <= 128 or value.get('kind') != 'logged_in_player_request':
        raise CraftSimError('engine_actor_invalid')
    return guid


def _dense(value, maximum, code):
    value = _table(value, code)
    if len(value) > maximum or set(value) != {str(i) for i in range(1, len(value)+1)}:
        raise CraftSimError(code)
    return [value[str(i)] for i in range(1, len(value)+1)]


def parse_engine(raw: Any, *, observed_at: int, current_build: str | None,
                 current_epoch: int, invalidation: dict) -> dict | None:
    if raw is None:
        return None
    _table(raw, 'engine_capture_invalid')
    if type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise CraftSimError('engine_capture_schema')
    if raw.get('scope') != 'available_current_actor_recipes':
        raise CraftSimError('engine_capture_scope')
    statuses = {'running', 'paused_combat', 'complete', 'partial', 'api_unavailable',
                'storage_budget_reached', 'version_unsupported', 'context_changed',
                'simulation_mode_active', 'source_shape_error', 'no_available_recipe_ids'}
    if raw.get('status') not in statuses:
        raise CraftSimError('engine_capture_status')
    actor = _actor(raw.get('source_actor'))
    _clock(raw.get('started_at'), observed_at)
    if 'finished_at' in raw:
        _clock(raw['finished_at'], observed_at)
        if raw['finished_at'] < raw['started_at']:
            raise CraftSimError('engine_capture_clock_order')
    epoch = _integer(raw.get('context_epoch'), 'engine_capture_epoch')
    if epoch > current_epoch:
        raise CraftSimError('engine_capture_epoch_after_container')
    ids = _dense(raw.get('queue'), 20000, 'engine_capture_queue')
    if any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
        raise CraftSimError('engine_capture_recipe_ids')
    for field in ('queued', 'processed', 'cursor', 'storage_bytes', 'storage_nodes'):
        _integer(raw.get(field), 'engine_capture_counter')
    if raw['queued'] != len(ids) or raw['processed'] > len(ids) or raw['cursor'] != raw['processed']+1:
        raise CraftSimError('engine_capture_checkpoint')
    if raw['storage_bytes'] > 8*1024*1024 or raw['storage_nodes'] > 120000:
        raise CraftSimError('engine_capture_storage_limit')
    for field in ('coverage_complete', 'enumeration_limited'):
        if type(raw.get(field)) is not bool:
            raise CraftSimError('engine_capture_coverage_flag')
    # A finite scenario search can never establish completeness of allocations.
    if raw['coverage_complete']:
        raise CraftSimError('engine_capture_unproven_complete_coverage')
    sizes = _table(raw.get('record_sizes', {}), 'engine_capture_sizes')
    for value in sizes.values():
        _table(value, 'engine_capture_size')
        _integer(value.get('bytes'), 'engine_capture_size')
        _integer(value.get('nodes'), 'engine_capture_size')
    records = _table(raw.get('records'), 'engine_capture_records')
    if len(records) > 20000:
        raise CraftSimError('engine_capture_record_limit')
    normalized, rejected = [], []
    for key, record in records.items():
        try:
            _table(record, 'engine_record_invalid')
            recipe_id = _integer(record.get('recipe_id'), 'engine_recipe_invalid', minimum=1)
            record_actor = _actor(record.get('source_actor'))
            if key != record_actor+':'+str(recipe_id):
                raise CraftSimError('engine_record_identity')
            attempted = _clock(record.get('attempted_at'), observed_at)
            if record.get('status') not in {'complete', 'partial', 'unavailable'}:
                raise CraftSimError('engine_record_status')
            reasons = _dense(record.get('diagnostics', {}), 128, 'engine_record_diagnostics')
            if any(not isinstance(s, str) or len(s) > 256 for s in reasons):
                raise CraftSimError('engine_record_diagnostics')
            good = record.get('last_good')
            scenarios = []
            if good is not None:
                _table(good, 'engine_last_good_invalid')
                captured = _clock(good.get('captured_at'), observed_at)
                good_epoch = _integer(good.get('context_epoch'), 'engine_last_good_epoch')
                if captured > attempted or good_epoch > current_epoch:
                    raise CraftSimError('engine_last_good_after_attempt')
                if good.get('coverage') != 'finite_standard_allocations':
                    raise CraftSimError('engine_scenario_coverage')
                scenarios_raw = _dense(good.get('scenarios'), 64, 'engine_scenarios_invalid')
                if type(good.get('scenario_count')) is not int or good['scenario_count'] != len(scenarios_raw) or not scenarios_raw:
                    raise CraftSimError('engine_scenario_count')
                seen = set()
                for entry in scenarios_raw:
                    _table(entry, 'engine_scenario_invalid')
                    scenario = _table(entry.get('scenario'), 'engine_scenario_identity')
                    sid = scenario.get('id')
                    if not isinstance(sid, str) or not 1 <= len(sid) <= 100 or sid in seen:
                        raise CraftSimError('engine_scenario_identity')
                    seen.add(sid)
                    if scenario.get('allocation_scope') != 'required_reagents_no_optional_search' or type(scenario.get('concentrating')) is not bool:
                        raise CraftSimError('engine_scenario_scope')
                    if entry.get('context_epoch') != good_epoch or entry.get('captured_at', captured+1) > captured:
                        raise CraftSimError('engine_scenario_context')
                    game = _table(entry.get('game'), 'engine_scenario_game')
                    if f"{game.get('version')}.{game.get('build')}" != good.get('game_build') or entry.get('craftsim_version') != good.get('craftsim_version'):
                        raise CraftSimError('engine_scenario_version_mismatch')
                    recipe = _table(entry.get('recipe'), 'engine_scenario_recipe')
                    if _actor(entry.get('source_actor')) != record_actor:
                        raise CraftSimError('engine_scenario_actor_mismatch')
                    if recipe.get('recipeID') != recipe_id or recipe.get('concentrating') != scenario['concentrating']:
                        raise CraftSimError('engine_scenario_recipe_mismatch')
                    profile = _profile(entry, observed_at, event='WAB_AUTONOMOUS_ENGINE_CAPTURE')
                    profile['last_invalidation'] = dict(invalidation, epoch=current_epoch)
                    profile['scenario_id'] = sid
                    assessment = assess_profile(profile, now=observed_at, current_build=current_build)
                    profile.update(status=assessment['status'], reasons=assessment['reasons'],
                                   context_status=assessment['context_status'])
                    if record_actor != actor:
                        profile['status'] = 'stale'
                        profile['context_status'] = 'other_actor_last_observation'
                        profile['reasons'] = sorted(set(profile['reasons']+['current_actor_differs']))
                    scenarios.append(profile)
            normalized.append({'recipe_id': recipe_id,
                'actor_key': hashlib.sha256(record_actor.encode()).hexdigest(),
                'attempted_at': attempted, 'attempt_status': record['status'],
                'diagnostics': reasons, 'scenarios': scenarios,
                'last_good_retained': bool(good and record['status'] != 'complete')})
        except (CraftSimError, TypeError, ValueError, AttributeError):
            rejected.append({'record_key_hash': hashlib.sha256(str(key).encode()).hexdigest(),
                             'reason': 'invalid_engine_capture_record'})
    return {'schema_version': 1, 'status': raw['status'], 'scope': raw['scope'],
            'records': normalized, 'rejected': rejected, 'processed': raw['processed'],
            'queued': raw['queued'], 'coverage_complete': False,
            'manual_recipe_selection_required': False, 'execution_eligible': False,
            'raw': raw}
