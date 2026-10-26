"""Execute the autonomous addon scheduler with synthetic WoW APIs, never the game.

Installed CraftSim ResultData and ProfitCalculation Lua are executed unchanged.
The constructor/optimizer/game APIs are explicitly synthetic boundaries. This is
not evidence of successful in-game loading or complete live character coverage.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_knowledge_craftsim import literal
from wow_auction.knowledge_craftsim import parse_bridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lua-runtime', required=True, type=Path)
    parser.add_argument('--craftsim', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists(): raise SystemExit('output_already_exists')
    sys.path.insert(0, str(args.lua_runtime.resolve()))
    from lupa.lua51 import LuaRuntime, lua_type
    sources = {p: (ROOT/p).read_bytes() for p in ['tools/fixtures/engine_capture_runtime.lua',
        'addons/WowAuctionBridge/Bridge.lua', 'addons/WowAuctionBridge/Engine.lua']}
    native = {p: (args.craftsim/p).read_bytes() for p in ['Classes/ResultData.lua', 'Pricing/ProfitCalculation.lua']}
    def setup(saved=None):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute(sources['tools/fixtures/engine_capture_runtime.lua'].decode())
        for data in native.values(): lua.execute(data.decode('utf-8-sig'), 'CraftSim', lua.globals().CS)
        if saved is not None: lua.execute('WowAuctionBridgeDB='+literal(saved))
        lua.execute(sources['addons/WowAuctionBridge/Bridge.lua'].decode(), 'WowAuctionBridge')
        lua.execute(sources['addons/WowAuctionBridge/Engine.lua'].decode(), 'WowAuctionBridge')
        return lua
    def unpack(v):
        return {k: unpack(x) for k, x in v.items()} if lua_type(v) == 'table' else v
    def state(lua): return unpack(lua.globals().WowAuctionBridgeDB.engine)
    def start(lua): lua.execute("Frames[2].event(nil,'PLAYER_LOGIN')")
    checks = []
    lua = setup();start(lua)
    assert lua.globals().ConstructorCalls == 0
    lua.execute('FlushOne()')
    assert lua.globals().ConstructorCalls == 1 and state(lua)['processed'] == 0
    lua.execute('FlushOne()')
    assert state(lua)['processed'] == 0
    lua.execute('FlushAll()')
    first = state(lua)
    assert first['status'] == 'complete' and first['processed'] == 1
    good = first['records']['Player-Synthetic-A:100']['last_good']
    assert good['scenario_count'] == 8 and not first['coverage_complete']
    assert lua.globals().WowAuctionBridgeDB.sequence == 0
    assert lua.globals().Optimizations == 4
    checks.append('login_calculates_8_scenarios_without_open_scan_or_ring_sequence_changes')
    low, high = good['scenarios'][1], good['scenarios'][3]
    assert low['result']['expectedQuality'] == 1 and high['result']['expectedQuality'] == 2
    assert low['recipe']['concentrationCost'] == 100 and high['recipe']['concentrationCost'] == 0
    assert low['reagents']['required'][2]['hasQuality'] is False
    assert low['engine']['average_profit'] != high['engine']['average_profit']
    checks.append('native_result_and_profit_methods_receive_complete_generated_allocations')
    parsed = parse_bridge(('WowAuctionBridgeDB='+literal(unpack(lua.globals().WowAuctionBridgeDB))).encode(), observed_at=1001)
    assert len(parsed['engine']['records'][0]['scenarios']) == 8 and not parsed['engine']['rejected']
    checks.append('runtime_output_passes_python_parser_with_provenance')
    lua.execute('Clock=1100;Unavailable=true;Tickers[1]();FlushAll()')
    failure = state(lua)['records']['Player-Synthetic-A:100']
    assert failure['last_good'] == good and failure['status'] == 'unavailable'
    assert lua.globals().ConstructorCalls == 1
    checks.append('unavailable_api_preflight_prevents_constructor_and_preserves_old_good')
    lua.execute("Actor='Player-Synthetic-B';Clock=1200;Tickers[1]();FlushAll()")
    assert state(lua)['records']['Player-Synthetic-A:100']['last_good'] == good
    assert 'last_good' not in state(lua)['records']['Player-Synthetic-B:100']
    checks.append('new_actor_unavailable_does_not_relabel_previous_actor')
    lua = setup();lua.execute('SelectedUnavailable=true');start(lua);lua.execute('FlushAll()')
    assert 'last_good' not in state(lua)['records']['Player-Synthetic-A:100']
    checks.append('native_zero_fallback_from_missing_selected_operation_never_exported')
    lua = setup();lua.execute('Combat=true');start(lua);lua.execute('FlushOne()')
    assert state(lua)['status'] == 'paused_combat' and lua.globals().ConstructorCalls == 0
    lua.execute('Combat=false;FlushAll()');assert state(lua)['status'] == 'complete'
    checks.append('combat_pause_resumes_automatically')
    lua = setup();start(lua);lua.execute('FlushOne();WowAuctionBridgeDB.context_epoch=2;FlushAll()')
    assert state(lua)['status'] == 'context_changed' and not state(lua)['records']
    checks.append('context_invalidation_discards_unfinished_recipe_without_freshness_relabel')
    lua = setup();lua.execute('Linked=true');start(lua);lua.execute('FlushAll()')
    assert lua.globals().ConstructorCalls == 0
    checks.append('linked_profession_view_rejected_before_native_cache_write')
    lua = setup();lua.execute('ProfessionUnknown=true');start(lua);lua.execute('FlushAll()')
    assert lua.globals().ConstructorCalls == 0
    checks.append('unknown_profession_does_not_enter_native_alt_cache_fallback')
    lua = setup();lua.execute("Version='99.0'");start(lua)
    assert state(lua)['status'] == 'version_unsupported' and lua.globals().ConstructorCalls == 0
    checks.append('untested_engine_version_not_executed')
    lua = setup();lua.execute('CS.SIMULATION_MODE.isActive=true');start(lua)
    assert state(lua)['status'] == 'simulation_mode_active' and lua.globals().ConstructorCalls == 0
    checks.append('simulation_global_mode_not_modified_or_used_as_live_context')
    for setting in ('ActualRank=2', 'MissingRank=true'):
        lua = setup();lua.execute(setting);start(lua);lua.execute('FlushAll()')
        record = state(lua)['records']['Player-Synthetic-A:100']
        assert record['status'] == 'unavailable' and 'last_good' not in record
        assert 'live_specialization_unavailable' in record['diagnostics'].values()
    checks.append('cached_specialization_fallback_not_fresh_without_matching_live_node_ranks')
    lua = setup();start(lua);lua.execute('FlushOne();WowAuctionBridgeDB.engine.storage_bytes=8*1024*1024;FlushAll()')
    assert state(lua)['status'] == 'storage_budget_reached' and not state(lua)['records']
    checks.append('storage_budget_preserves_existing_collection_and_reports_omission')
    result = {'captured_at': datetime.now(timezone.utc).isoformat(), 'kind': 'SYN_RUNTIME_WITH_INSTALLED_ENGINE_METHODS',
        'checks': checks, 'check_count': len(checks), 'all_passed': True,
        'native_methods_executed': ['ResultData.Update', 'CALC.GetAverageProfit', 'CALC.GetResourcefulnessSavedCosts', 'CALC.GetExpectedItemAmountMulticraft'],
        'synthetic_boundaries': ['WoW APIs', 'RecipeData constructor', 'reagent optimizer', 'character and prices'],
        'real_game_runtime_proven': False,
        'sources': {p: hashlib.sha256(data).hexdigest() for p, data in {**sources, **native}.items()},
        'initial_scenarios': good['scenario_count'], 'quality_low': low['result']['expectedQuality'],
        'quality_high': high['result']['expectedQuality'], 'global_observer_sequence_after_capture': 0}
    with args.output.open('x', encoding='utf-8') as f: json.dump(result, f, indent=2, ensure_ascii=False);f.write('\n')
    print(json.dumps({'checks': len(checks), 'all_passed': True, 'output': str(args.output)}))


if __name__ == '__main__': main()
