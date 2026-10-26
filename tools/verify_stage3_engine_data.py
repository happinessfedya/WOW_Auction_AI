"""Read-only, privacy-preserving survey of real CraftSim calculation inputs.

Run: python -B tools/verify_stage3_engine_data.py OUTPUT_JSON
Raw inputs and a source-only composite example stay in .private. Nothing here
asserts that separate caches form one synchronized calculation observation.
"""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wow_auction.file_reader import read_stable
from wow_auction.ingestion import reject_source_destination
from wow_auction.lua_data import parse_saved_variables
from verify_stage3_prior import probe


def digest(data):
    return hashlib.sha256(data).hexdigest()


def values(value):
    return value.values() if isinstance(value, dict) else ()


def scalar_fields(value):
    return {key: child for key, child in value.items()
            if isinstance(child, (bool, int, float))}


def allocation_status(queue):
    reagents = queue.get('requiredReagents', {})
    if not isinstance(reagents, dict) or not reagents:
        return 'empty_required_reagents_not_proof_of_no_inputs'
    for slot in reagents.values():
        if not isinstance(slot, dict) or not isinstance(slot.get('items'), dict):
            return 'malformed'
        amount = slot.get('requiredQuantity')
        quantities = [row.get('quantity') for row in slot['items'].values()]
        if type(amount) is not int or any(type(n) is not int or n < 0 for n in quantities):
            return 'malformed'
        if sum(quantities) != amount:
            return 'known_slot_quantity_incomplete'
    return 'known_slot_quantity_sums_complete'


def main():
    output = Path(sys.argv[1])
    if output.exists():
        raise SystemExit('Evidence exists; choose a new filename')
    inherited, config = probe()
    reject_source_destination(output, [Path(root) for root in config['wow_roots']])
    private = Path(inherited['local_evidence_directory'])
    craft_bytes = (private / 'CraftSim.lua').read_bytes()
    craft = parse_saved_variables(craft_bytes)['CraftSimDB']
    game_root = Path(config['wow_roots'][0])
    bridge_paths = list((game_root / 'WTF/Account').glob('*/SavedVariables/WowAuctionBridge.lua'))
    if len(bridge_paths) != 1:
        raise ValueError('expected_one_local_bridge_for_this_evidence_probe')
    bridge_source = read_stable(bridge_paths[0])
    (private / 'WowAuctionBridge.lua').write_bytes(bridge_source.data)
    bridge = parse_saved_variables(bridge_source.data)['WowAuctionBridgeDB']
    crafters = craft['crafterDB']['data']
    queues = list(craft['craftQueueDB']['data'].values())
    recipe_ids = set()
    counts = Counter()
    for row in crafters.values():
        for field in ('recipeInfos', 'professionInfos', 'operationInfos', 'professionGear',
                      'specializationData', 'concentrationData', 'cooldownData'):
            counts[field] += len(row.get(field, {}))
        recipe_ids.update(row.get('recipeInfos', {}))
    queue_rows = []
    for row in queues:
        crafter = row.get('crafterData', {})
        actor_key = str(crafter.get('name')) + '-' + str(crafter.get('realm'))
        cached_actor = crafters.get(actor_key, {})
        recipe = row['recipeID']
        queue_rows.append({'recipe_id': recipe, 'allocation_status': allocation_status(row),
            'actor_recipe_info_present': recipe in cached_actor.get('recipeInfos', {}),
            'actor_baseline_operation_present': recipe in cached_actor.get('operationInfos', {}),
            'concentrating': row.get('concentrating'),
            'has_optional_inputs': bool(row.get('optionalReagents')),
            'has_selectable_input': bool(row.get('requiredSelectableReagent'))})
    records = list(values(bridge.get('records')))
    sample_recipe = 1230869
    sample_queue = next(row for row in queues if row['recipeID'] == sample_recipe)
    sample_profile = next(row for row in reversed(records)
                          if row.get('recipe', {}).get('recipeID') == sample_recipe)
    sample_actor = sample_queue['crafterData']
    sample_cache = crafters[sample_actor['name'] + '-' + sample_actor['realm']]
    composite = {
        'kind': 'REAL_SOURCE_FIELDS_NOT_A_UNIFIED_ENGINE_PROFILE',
        'recipe_id': sample_recipe,
        'craftsim_sha256': digest(craft_bytes), 'bridge_sha256': bridge_source.sha256,
        'bridge_capture_at': sample_profile.get('captured_at'),
        'native_cache_observed_at': None,
        'native_selected_operation_info_present': False,
        'allocation_skill_equivalence_to_bridge': 'unconfirmed',
        'queue': {field: sample_queue.get(field) for field in
                  ('recipeID', 'concentrating', 'requiredReagents', 'optionalReagents',
                   'requiredSelectableReagent', 'subRecipeCostsEnabled')},
        'native_recipe_info_numeric': scalar_fields(sample_cache['recipeInfos'][sample_recipe]),
        'native_baseline_operation_numeric': scalar_fields(sample_cache['operationInfos'][sample_recipe]),
        'bridge': {field: sample_profile.get(field) for field in
                   ('recipe', 'stats', 'base_stats', 'modifiers', 'specialization',
                    'concentration', 'result', 'prices', 'engine', 'reagents', 'completeness')},
        'warnings': [
            'Queue and bridge are separate observations; no synchronized native cache timestamp.',
            'Do not substitute the queue into a fresh engine profile without allocation-specific skill proof.',
            'No fresh gameplay or economic-execution validity is implied.',
        ],
    }
    composite_path = private / 'craftsim-1230869-source-only-composite.json'
    with composite_path.open('x', encoding='utf-8') as stream:
        json.dump(composite, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    source_files = ['Classes/RecipeData.lua', 'Classes/ReagentData.lua', 'DB/CrafterDB.lua',
                    'Util/API.lua', 'CraftSim.toc']
    addon = game_root / 'Interface/AddOns/CraftSim'
    source_hashes = {rel: digest((addon / rel).read_bytes()) for rel in source_files}
    inherited_checks = {
        'S1-V01': 'New second Auction sale; no independent invoice, remains unclosed.',
        'S1-V02': 'Two actual expired listings now present; no independent return/deposit amount, income or refund proof.',
        'S1-V03': 'Read-only old-pair comparison provided; aggregate growth is not established by new rows.',
        'S1-V04': 'No independent absolute UI wallet/warband observation.',
        'S1-V05': 'Fresh container counts provided; completeness/transfer overlap remains unverified.',
        'S1-V06': 'One configured account; no second-account observation.',
        'S1-V07': 'Existing item metadata is not instance binding/variant tradeability proof.',
        'S2-V01': 'No new market HTTP cadence measurement in this local-data probe.',
        'S2-V02': 'No new authorized UE access or policy evidence.',
        'S2-V03': 'No real sleep/offline/backfill experiment.',
        'S2-V04': 'No new long-history/depth/archive-rights evidence.',
    }
    result = {
        'kind': 'REAL_READ_ONLY_SOURCE_SURVEY',
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'build': inherited['build'], 'inherited_checks': inherited_checks,
        'inherited_source_probe': inherited,
        'craftsim': {'sha256': digest(craft_bytes), 'bytes': len(craft_bytes),
            'crafter_contexts': len(crafters), 'distinct_recipe_ids': len(recipe_ids),
            'counts': dict(counts), 'queue_entries': len(queues), 'queue': queue_rows,
            'queue_allocation_status_counts': dict(Counter(row['allocation_status'] for row in queue_rows)),
            'native_recipe_schematic_repository_present': False,
            'per_recipe_observation_timestamps_present': False,
            'known_repository_versions': {name: row['version'] for name, row in craft.items()
                if isinstance(row, dict) and 'version' in row},
            'installed_source_sha256': source_hashes},
        'bridge': {'sha256': bridge_source.sha256, 'bytes': bridge_source.size_bytes,
            'schema_version': bridge.get('schema_version'), 'records': len(records),
            'exporter_versions': dict(Counter(str(row.get('exporter_version')) for row in records)),
            'passive_present': 'passive' in bridge, 'engine_batch_present': 'engine' in bridge,
            'flushed_at': bridge.get('flushed_at'),
            'source_still_same_after_read': read_stable(bridge_paths[0]).sha256 == bridge_source.sha256},
        'private_source_only_composite': {'path': composite_path.as_posix(),
            'sha256': digest(composite_path.read_bytes()), 'recipe_id': sample_recipe,
            'queue_allocation_status': allocation_status(sample_queue),
            'unified_engine_profile': False, 'selected_operation_proof': False},
        'source_findings': [
            {'location': 'Classes/RecipeData.lua:2704',
             'finding': 'Alt/forceCache missing operation info falls back to current-character API; caller must guard provenance.'},
            {'location': 'Classes/ReagentData.lua:390',
             'finding': 'Missing base or selected operation API returns reagent skill zero; successful Update alone does not prove sufficient data.'},
            {'location': 'DB/CrafterDB.lua:200',
             'finding': 'Native baseline operation cache lacks selected allocation and observation timestamp.'},
            {'location': 'Classes/RecipeData.lua:266',
             'finding': 'Recipe construction retrieves schematic from game API; native cache cannot be assumed to contain that schema.'},
        ],
        'game_actions': False, 'other_addon_mutation': False,
        'existing_database_files_opened': False, 'secrets_read': False,
        'self_sha256': digest(Path(__file__).read_bytes()),
    }
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': output.as_posix(), 'ledger_rows': inherited['ledger']['rows'],
        'queue_allocation_status_counts': result['craftsim']['queue_allocation_status_counts'],
        'bridge_passive_present': result['bridge']['passive_present'],
        'private_source_only_composite': composite_path.as_posix()}))


if __name__ == '__main__':
    main()
