"""Autonomous, read-only polling of configured local recipe observations.

No game action is needed to run this importer. Existing caches are useful source
claims; their existence does not establish fresh personal CraftSim calculations.
"""
from collections import Counter
import hashlib
import math
from pathlib import Path
import re
import time

from .file_reader import read_stable, StableReadError
from .ingestion import load_config, ConfigError
from .knowledge import KnowledgeError
from .knowledge_craftsim import parse_bridge, CraftSimError, assess_profile
from .knowledge_items import decode_tsm_item_info, ItemMetadataError
from .lua_data import parse_saved_variables, LuaDataError


_REPOSITORIES = {'itemRecipeDB': 4, 'crafterDB': 7, 'craftQueueDB': 1,
    'lastCraftingCostDB': 1, 'itemOptimizedCostsDB': 4, 'priceOverrideDB': 1,
    'optionsDB': 2, 'recipeSubCrafterDB': 3, 'optimizationOptionsDB': 2}
_CRAFTER_FIELDS = ('cachedRecipeIDs', 'recipeInfos', 'professionInfos', 'operationInfos',
    'professionGear', 'specializationData', 'concentrationData', 'cooldownData', 'preCraftBuffStale')


def _json_value(value, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 40 or budget[0] > 500_000:
        raise KnowledgeError('local_cache_structure_limit')
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if type(key) not in (int, str) or str(key) in result:
                raise KnowledgeError('local_cache_ambiguous_key')
            result[str(key)] = _json_value(child, depth + 1, budget)
        return result
    if type(value) in (str, int, bool) or value is None:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise KnowledgeError('invalid_local_cache_value')


def normalize_craftsim_cache(variables, *, source_sha256, build=None):
    root = variables.get('CraftSimDB')
    if not isinstance(root, dict):
        raise KnowledgeError('craftsim_cache_root_missing')
    repositories, counts = {}, Counter()
    for name, expected in _REPOSITORIES.items():
        repo = root.get(name)
        if repo is None:
            repositories[name] = {'status': 'missing_data'}
            continue
        if not isinstance(repo, dict) or type(repo.get('version')) is not int:
            repositories[name] = {'status': 'unsupported_schema'}
            continue
        data = repo.get('data')
        if repo['version'] != expected or not isinstance(data, dict):
            repositories[name] = {'status': 'unsupported_schema', 'version': repo['version']}
            continue
        selected = data
        if name == 'crafterDB':
            selected = {}
            for crafter, record in data.items():
                if not isinstance(record, dict):
                    raise KnowledgeError('craftsim_cache_crafter_shape')
                # Account character labels are not needed for offline discovery.
                token = hashlib.sha256(str(crafter).encode()).hexdigest()
                selected[token] = {field: record[field] for field in _CRAFTER_FIELDS if field in record}
                for field in _CRAFTER_FIELDS:
                    values = record.get(field)
                    if isinstance(values, dict):
                        counts[field] += len(values)
                        if field == 'cachedRecipeIDs':
                            counts['cached_recipe_list_entries'] += sum(len(v) for v in values.values() if isinstance(v, dict))
        if name == 'lastCraftingCostDB':
            counts['cached_cost_records'] = sum(len(v) for v in data.values() if isinstance(v, dict))
        repositories[name] = {'status': 'cached_observations', 'version': repo['version'],
                               'entry_count': len(data), 'data': _json_value(selected)}
    return {'schema_version': 1, 'source': 'CraftSimDB', 'source_sha256': source_sha256,
        'configured_build': build, 'observed_game_build': None, 'status': 'cached_observations',
        'repositories': repositories, 'summary_counts': dict(counts), 'profiles': [],
        'engine_evaluation_available': False, 'execution_eligible': False,
        'limitations': ['cache_age_unknown_except_explicit_record_timestamps',
            'saved_operation_info_is_not_a_new_engine_calculation',
            'queue_allocations_do_not_prove_execution_or_recipe_schematic_completeness',
            'current_gear_buffs_reagent_choices_and_build_not_proven_by_cache',
            'global_catalog_not_limited_to_personal_cached_recipes']}


def normalize_tsm_crafting_cache(variables, *, source_sha256, build=None):
    root = variables.get('TradeSkillMasterDB')
    if not isinstance(root, dict):
        raise KnowledgeError('tsm_crafting_cache_root_missing')
    scopes, recipes = [], []
    for key, rows in root.items():
        if not isinstance(key, str):
            continue
        parts = key.split('@')
        if len(parts) != 4 or parts[0] != 'f' or parts[2:] != ['internalData', 'crafts']:
            continue
        if not isinstance(rows, dict):
            raise KnowledgeError('tsm_crafting_cache_scope_shape')
        scope = hashlib.sha256(key.encode()).hexdigest()
        scopes.append({'scope_key': scope, 'records': len(rows)})
        for craft_string, row in rows.items():
            if not isinstance(craft_string, str) or not isinstance(row, dict) or not isinstance(row.get('mats'), dict):
                raise KnowledgeError('tsm_crafting_cache_recipe_shape')
            recipes.append({'source_scope': scope, 'craft_string': craft_string,
                'status': 'source_claim', 'observed_at': None,
                'fields': _json_value({name: row[name] for name in
                    ('mats', 'itemString', 'numResult', 'profession', 'hasCD', 'rootCategoryId', 'name') if name in row}),
                'crafter_count': len(row['players']) if isinstance(row.get('players'), dict) else None,
                'output_quantity_semantics': 'source_floor_of_range_midpoint_not_guaranteed_yield',
                'joint_outcome_pmf': None, 'execution_eligible': False})
    return {'schema_version': 1, 'source': 'TSM_crafting_cache', 'source_sha256': source_sha256,
        'configured_build': build, 'observed_game_build': None,
        'status': 'cached_observations' if scopes else 'missing_data',
        'scopes': scopes, 'recipes': recipes, 'record_count': len(recipes),
        'limitations': ['per_recipe_capture_time_and_build_missing',
            'quality_and_finishing_mat_strings_are_choices_not_all_consumed_items',
            'numResult_is_not_a_joint_distribution', 'no_personal_availability_inference',
            'private_cache_not_a_redistributable_full_transformation_catalog']}


def detect_build(config_path):
    """Read installed active Retail .build.info, never substitute a pinned build."""
    try:
        config, _ = load_config(Path(config_path))
        versions = set()
        for root in config['wow_roots']:
            text = read_stable(Path(root).parent / '.build.info', max_bytes=1024 * 1024).data.decode('utf-8-sig')
            lines = text.splitlines()
            headers = [part.split('!')[0] for part in lines[0].split('|')]
            for line in lines[1:]:
                row = dict(zip(headers, line.split('|')))
                version = row.get('Version', '')
                if (row.get('Active') == '1' and row.get('Product') == 'wow'
                        and re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,9}', version)):
                    versions.add(version)
        return next(iter(versions)) if len(versions) == 1 else None
    except (OSError, ValueError, IndexError, KeyError):
        return None


def _normalizer_revision(kind):
    if kind in {'craftsim', 'item_metadata'}:
        from .knowledge_cli import artifact_normalizer_revision
        return artifact_normalizer_revision(kind)
    checksum = hashlib.sha256(b'local-recipe-cache-v1\x00')
    for name in ('knowledge_local.py', 'lua_data.py', 'knowledge.py'):
        checksum.update(name.encode() + b'\x00' + Path(__file__).with_name(name).read_bytes())
    return checksum.hexdigest()


class LocalKnowledgePoller:
    def __init__(self, store, config_path, *, build=None, verify_interval=300):
        if type(verify_interval) is not int or not 1 <= verify_interval <= 3600:
            raise KnowledgeError('invalid_local_verification_interval')
        self.store, self.config_path, self.build = store, Path(config_path), build
        self.verify_interval = verify_interval
        self._seen = {}

    def scan(self, *, now=None, build=None):
        now = int(time.time()) if now is None else now
        if type(now) is not int or now <= 0:
            raise KnowledgeError('invalid_local_poll_time')
        current_build = build or self.build or detect_build(self.config_path)
        try:
            config, sources = load_config(self.config_path)
        except (ConfigError, OSError, ValueError):
            return {'status': 'local_configuration_unavailable', 'game_actions_required': False, 'sources': []}
        jobs, bridge_paths = [], set()
        for source in sources:
            if source.addon == 'TradeSkillMaster':
                jobs.extend((kind, source.path) for kind in ('item_metadata', 'tsm_crafting_cache'))
            if source.addon == 'CraftSim':
                jobs.append(('craftsim_cache', source.path))
            if source.addon in {'TradeSkillMaster', 'CraftSim'}:
                bridge_paths.add(source.path.with_name('WowAuctionBridge.lua'))
        jobs.extend(('craftsim', path) for path in sorted(bridge_paths))
        results, captures = [], {}
        for kind, path in jobs:
            token = (kind, str(path.resolve()).casefold())
            try:
                revision = _normalizer_revision(kind)
                stat = path.lstat()
                stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, current_build, revision)
                previous = self._seen.get(token)
                if previous and previous[0] == stamp and 0 <= now - previous[1] < self.verify_interval:
                    results.append({'kind': kind, 'status': 'file_unchanged', 'artifact_id': previous[2]})
                    continue
                source = captures.get(path)
                if source is None:
                    source = read_stable(path, settle_seconds=config.get('settle_seconds', 0.1))
                    captures[path] = source
                identity = {'source_sha256': source.sha256, 'normalizer_revision': revision,
                            'build': current_build or 'unknown'}
                existing = self.store.find_artifact(kind, identity)
                if existing is None:
                    if kind == 'craftsim':
                        payload = parse_bridge(source.data, observed_at=now, current_build=current_build)
                    else:
                        variables = parse_saved_variables(source.data)
                        if kind == 'item_metadata':
                            payload = decode_tsm_item_info(variables, expected_build=current_build)
                            payload['source_sha256'] = source.sha256
                        elif kind == 'craftsim_cache':
                            payload = normalize_craftsim_cache(variables, source_sha256=source.sha256, build=current_build)
                        else:
                            payload = normalize_tsm_crafting_cache(variables, source_sha256=source.sha256, build=current_build)
                    self.store.save_artifact(kind, payload, file_written_at_ns=source.file_written_at_ns,
                                             identity=identity, now=now)
                saved = self.store.find_artifact(kind, identity)
                self._seen[token] = (stamp, now, saved['id'])
                row = {'kind': kind, 'status': 'artifact_unchanged' if existing else 'artifact_stored',
                       'artifact_id': saved['id'], 'source_sha256': source.sha256,
                       'build_comparison': 'installed_or_supplied' if current_build else 'unknown'}
                if kind == 'craftsim':
                    row['profile_statuses_now'] = dict(Counter(assess_profile(profile, now=now,
                        current_build=current_build)['status'] for profile in saved['payload']['profiles']))
                    row['rejected_records'] = len(saved['payload']['rejected'])
                    passive = saved['payload'].get('passive', {})
                    row['passive_records'] = len(passive.get('records', {})) if isinstance(passive, dict) else 0
                    engine = saved['payload'].get('engine')
                    row['engine_records'] = len(engine['records']) if isinstance(engine, dict) else 0
                    row['engine_scenarios'] = sum(len(record['scenarios']) for record in engine['records']) if isinstance(engine, dict) else 0
                results.append(row)
            except (OSError, KnowledgeError, LuaDataError, CraftSimError, ItemMetadataError):
                # An unavailable source never removes prior immutable evidence or
                # prevents independent local sources and public markets running.
                results.append({'kind': kind, 'status': 'missing_data' if not path.exists() else 'read_or_schema_error',
                                'last_good_retained': True if token in self._seen else None,
                                'history_removed': False})
        return {'status': 'local_observations_checked', 'build': current_build,
                'game_actions_required': False, 'sources': results,
                'fresh_personal_engine_evaluation_guaranteed': False}
