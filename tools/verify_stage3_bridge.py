"""Optional offline Lua 5.1 verification; does not load WoW or install an addon.

Requires lupa 2.6 in an explicitly supplied private --lua-runtime directory.
The application and ordinary unittest suite have no lupa dependency.
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
from wow_auction.knowledge_craftsim import parse_bridge, fixed_variant_reprice
from tests.test_knowledge_craftsim import fixture, literal, exported


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lua-runtime", required=True, type=Path)
    parser.add_argument("--craftsim", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists(): raise SystemExit("output_already_exists")
    sys.path.insert(0, str(args.lua_runtime.resolve()))
    from lupa.lua51 import LuaRuntime, lua_type
    lua = LuaRuntime(unpack_returned_tuples=True)
    def unpack(value):
        if lua_type(value) == "table": return {key: unpack(child) for key, child in value.items()}
        return value
    lua.execute("""
        CraftSimDB = {optionsDB={data={TSM_DEPOSIT_ENABLED=false}}}
        ExternalSentinel = {wallet=123, nested={value='unchanged'}}
        local function stat(value, extra)
            return {value=value or 0, extraValues={extra or 0}, percentDivisionFactor=100,
                GetPercent=function(self) return self.value/100 end,
                GetExtraValue=function(self) return self.extraValues[1] end}
        end
        local function item(id) return {GetItemID=function() return id end,GetItemLink=function() return 'item:'..id end} end
        local function stats() return {skill=stat(100),recipeDifficulty=stat(100),multicraft=stat(0),
            resourcefulness=stat(0),ingenuity=stat(0),craftingspeed=stat(0)} end
        CS = {CONST={AUCTION_HOUSE_CUT=0.95,GENERAL_OPTIONS_DEFAULTS={PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT=0.3}},
            DEBUG={RegisterLogger=function() return {LogDebug=function() end} end},
            DB={OPTIONS={Get=function(self,key) if key=='PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT' then return 0.3 end; error('unexpected option') end}},
            UTIL={FormatMoney=function(self,n) return tostring(n) end,IsGreyItem=function() return false end,
                GetMulticraftConstantByBaseYield=function() return 2.5 end,
                toBits=function(self,n,count) local out={} for i=count,1,-1 do out[i]=n%2;n=math.floor(n/2) end return out end},
            GUTIL={ColorizeText=function(self,x) return x end,COLORS={RED='red'}}}
        RD = {recipeID=100,categoryID=10,expansionID=11,minItemAmount=1,maxItemAmount=1,baseItemAmount=1,
            concentrating=false,concentrationCost=0, supportsCraftingStats=true,supportsResourcefulness=true,
            supportsMulticraft=false,supportsSpecializations=false,isRecraft=false,isSalvageRecipe=false,subRecipeCostsEnabled=false,
            recipeSchematic={},baseOperationInfo={},crafterData={name='Synthetic',realm='Test'},
            professionStats=stats(),baseProfessionStats=stats(),professionStatModifiers=stats(),
            professionGearSet={},buffData={buffs={}},averageProfitCached=35000,
            reagentData={requiredReagents={{requiredQuantity=2,hasQuality=false,dataSlotIndex=1,items={{item=item(200),quantity=0}}}},optionalReagentSlots={},finishingReagentSlots={}},
            resultData={expectedQuality=1,expectedQualityConcentration=2,expectedYieldPerCraft=1,expectedItem=item(300),itemsByQuality={item(300)}},
            priceData={craftingCosts=60000,craftingCostsRequired=60000,resourcefulnessSavedCostsAverage=0,
                qualityPriceList={100000},reagentPriceInfos={[200]={itemPrice=30000,priceInfo={isAHPrice=true}}}}}
        CurrentRecipe=RD
        CraftSimTSM={GetExpectedDeposit=function() return Deposit or 0 end}
        CraftSimAPI={RegisterEvents=function(self,obj) Listener=obj end,GetOpenRecipeData=function() return CurrentRecipe end,
            GetCraftSim=function() return CS end,GetExpectedDeposit=function() return Deposit or 0 end}
        C_AddOns={GetAddOnMetadata=function() return '27.0.7' end}
        C_Item={GetItemInfo=function() return 'synthetic',nil,2 end}
        GetBuildInfo=function() return '12.1.5','70575','Oct 2026',120105 end
        GetServerTime=function() return Clock or 1000 end
        GetLocale=function() return 'enUS' end
        Timers={}
        C_Timer={After=function(delay,fn) table.insert(Timers,fn) end}
        FlushTimers=function() local work=Timers;Timers={};for _,fn in ipairs(work) do fn() end end
        CreateFrame=function() return {RegisterEvent=function() end,SetScript=function(self,key,fn) OnEvent=fn end} end
    """)
    profit_source = (args.craftsim / "Pricing/ProfitCalculation.lua").read_bytes()
    lua.execute(profit_source.decode("utf-8-sig"), "CraftSim", lua.globals().CS)
    outcomes = []
    for case, stats, resource, multi, deposit, chance, cost, price, expected in [
        ("C11_no_stats_single_deposit", False, False, False, 1000, 0, 60000, 100000, 34000),
        ("C11_resourcefulness_double_deposit", True, True, False, 1000, 0, 60000, 100000, 33000),
        ("C11_resourcefulness_20pct", True, True, False, 1000, 20, 60000, 100000, 36600),
        ("C11_fixed_reprice_input", True, True, False, 1000, 20, 62000, 100000, 34720),
        ("C11_fixed_reprice_output", True, True, False, 1000, 0, 62000, 110000, 40500),
        ("C21_unsupported_empty", True, False, False, 1000, 0, 60000, 100000, 0),
        ("C11_multicraft_zero_proc", True, True, True, 1000, 0, 60000, 100000, 33000),
    ]:
        rd = lua.globals().RD
        rd.supportsCraftingStats, rd.supportsResourcefulness, rd.supportsMulticraft = stats, resource, multi
        rd.professionStats.resourcefulness.value = chance
        rd.priceData.craftingCosts, rd.priceData.craftingCostsRequired = cost, cost
        rd.priceData.qualityPriceList[1] = price
        lua.globals().Deposit = deposit
        mean, states = lua.eval("CS.CALC:GetAverageProfit(RD)")
        states = unpack(states)
        assert abs(mean-expected)<1e-8, (case, mean, expected)
        if case.startswith("C21"): assert len(states) == 0
        outcomes.append({"case": case, "actual_copper_per_craft": mean, "proc_states": len(states), "pass": True})
    # Feed captured source values to the Python fixed-variant adapter, not a reimplementation of Lua.
    raw = fixture(); raw["engine"]["average_profit"] = outcomes[1]["actual_copper_per_craft"]
    profile = parse_bridge(exported(raw), observed_at=1001)["profiles"][0]
    reprice = fixed_variant_reprice(profile, {"item:200":31000,"item:300":110000}, now=1001,
        current_build=profile["build"], expected_fingerprint=profile["state_fingerprint"])
    assert float(reprice["expected_profit_copper_per_craft"]) == outcomes[4]["actual_copper_per_craft"]
    # Capture paths must not call OPTIONS:Get, result overrides or update methods.
    lua.execute("""
        CS.DB.OPTIONS.Get=function() error('forbidden mutating accessor') end
        RD.supportsCraftingStats=true;RD.supportsResourcefulness=true;RD.supportsMulticraft=false
        RD.professionStats.resourcefulness.value=0;RD.priceData.craftingCosts=60000;RD.priceData.craftingCostsRequired=60000
        RD.priceData.qualityPriceList[1]=100000
        RD.Update=function() error('forbidden update') end
        RD.GetAverageProfit=function() error('forbidden engine cache mutation') end
    """)
    before = unpack(lua.globals().CraftSimDB)
    sentinel_before = unpack(lua.globals().ExternalSentinel)
    lua.execute((ROOT / "addons/WowAuctionBridge/Bridge.lua").read_text(), "WowAuctionBridge")
    lua.execute("OnEvent(nil,'ADDON_LOADED','WowAuctionBridge');Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);FlushTimers()")
    assert unpack(lua.globals().CraftSimDB) == before
    assert unpack(lua.globals().ExternalSentinel) == sentinel_before
    db = unpack(lua.globals().WowAuctionBridgeDB)
    parsed = parse_bridge(("WowAuctionBridgeDB="+literal(db)).encode(), observed_at=1001)
    assert len(parsed["profiles"]) == 1 and parsed["profiles"][0]["status"] == "model_estimate", parsed
    checks = ["Lua5.1_bridge_load", "ready_event_dto_roundtrip", "other_savedvariables_unchanged", "mutating_accessor_not_called"]
    lua.execute("Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);Listener:CRAFTSIM_RECIPE_DATA_MODIFIED();FlushTimers()")
    assert len(unpack(lua.globals().WowAuctionBridgeDB.records)) == 1
    checks.append("pending_capture_cancelled_after_invalidation")
    lua.execute("Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);CurrentRecipe={};FlushTimers();CurrentRecipe=RD")
    assert len(unpack(lua.globals().WowAuctionBridgeDB.records)) == 1
    checks.append("pending_capture_cancelled_after_recipe_replacement")
    lua.execute("RD.averageProfitCached=nil;Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);FlushTimers()")
    db = unpack(lua.globals().WowAuctionBridgeDB)
    parsed = parse_bridge(("WowAuctionBridgeDB="+literal(db)).encode(), observed_at=1001)
    assert parsed["profiles"][-1]["status"] == "missing_data"
    checks.append("missing_engine_cache_not_zero")
    lua.execute("RD.averageProfitCached=35000;for i=1,105 do Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);FlushTimers() end")
    assert len(unpack(lua.globals().WowAuctionBridgeDB.records)) == 100
    assert lua.globals().WowAuctionBridgeDB.dropped_records == 7
    checks.append("bounded_ring_with_drop_counter")
    lua.execute("RD.priceData.reagentPriceInfos[200].priceInfo.cycle=RD.priceData.reagentPriceInfos;Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);FlushTimers()")
    records = unpack(lua.globals().WowAuctionBridgeDB.records)
    assert "error" not in records[100]
    assert "cycle" not in records[100]["prices"]["reagentPriceInfos"][200]["priceInfo"]
    checks.append("unselected_price_object_cycle_not_serialized")
    lua.execute("RD.professionStats.skill.extraValues.bad=RD;Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);FlushTimers();RD.professionStats.skill.extraValues.bad=nil")
    records = unpack(lua.globals().WowAuctionBridgeDB.records)
    assert records[100]["error"] == "capture_failed"
    assert records[100]["diagnostic"]["code"] == "invalid_numeric_entry"
    checks.append("invalid_selected_numeric_map_has_safe_diagnostic")
    lua.execute("RD.priceData.reagentPriceInfos[200].priceInfo.cycle=nil;RD.crafterData.name=string.rep('x',4097);Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);FlushTimers()")
    assert unpack(lua.globals().WowAuctionBridgeDB.records)[100]["error"] == "capture_failed"
    checks.append("string_budget_capture_quarantined")
    lua.execute("""
        RD.crafterData.name='Synthetic'
        local s=RD.professionStats
        RD.professionGearSet={gear1={item=RD.resultData.expectedItem,professionStats=s},
            gear2={item=RD.resultData.expectedItem,professionStats=s},tool={item=RD.resultData.expectedItem,professionStats=s}}
        for i=1,7 do RD.buffData.buffs[i]={buffID=1000+i,active=true,stacks=1,professionStats=s} end
        local reagent=RD.reagentData.requiredReagents[1]
        for i=2,4 do RD.reagentData.requiredReagents[i]=reagent end
        Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);FlushTimers()
    """)
    rich_row = unpack(lua.globals().WowAuctionBridgeDB.records)[100]
    assert "error" not in rich_row, rich_row
    checks.append("normal_gear_buffs_reagents_fit_export_budget")
    # A controlled 64KB replay proves the former bound rejected this legitimate shape.
    # Source revision is identical except its threshold: isolates the defect.
    current_source = (ROOT / "addons/WowAuctionBridge/Bridge.lua").read_text()
    lua.execute("WowAuctionBridgeDB=nil")
    lua.execute(current_source.replace("16, 128000", "16, 64000"), "WowAuctionBridge")
    lua.execute("OnEvent(nil,'ADDON_LOADED','WowAuctionBridge');Listener:CRAFTSIM_RECIPE_DATA_UPDATED(RD);FlushTimers()")
    limited = unpack(lua.globals().WowAuctionBridgeDB.records)[1]
    assert limited["error"] == "capture_failed" and limited["diagnostic"]["code"] == "byte_limit", limited
    checks.append("former_64kb_threshold_regression_reproduced")
    lua.execute("WowAuctionBridgeDB={schema_version=99,marker='preserve'};OnEvent(nil,'ADDON_LOADED','WowAuctionBridge');OnEvent(nil,'PLAYER_LOGOUT')")
    assert unpack(lua.globals().WowAuctionBridgeDB) == {"schema_version":99,"marker":"preserve"}
    checks.append("unknown_savedvariables_schema_preserved")
    assert unpack(lua.globals().CraftSimDB) == before
    paths = ["CraftSim.toc", "Util/API.lua", "Util/Events.lua", "Classes/RecipeData.lua", "Classes/PriceData.lua",
             "Classes/ResultData.lua", "Pricing/ProfitCalculation.lua", "DB/optionsDB.lua", "DB/priceOverrideDB.lua"]
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "kind": "SYN_LUA_INSTALLED_SOURCE_NOT_GAME_RUNTIME",
        "lua_version": lua.eval("_VERSION"), "lupa_version": "2.6", "source_hashes": {
            path: hashlib.sha256((args.craftsim/path).read_bytes()).hexdigest() for path in paths},
        "project_hashes": {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in
            ["addons/WowAuctionBridge/Bridge.lua","addons/WowAuctionBridge/WowAuctionBridge.toc","wow_auction/knowledge_craftsim.py",
             "tests/test_knowledge_craftsim.py","tools/verify_stage3_bridge.py"]},
        "engine_cases": outcomes, "bridge_checks": checks, "fixed_variant_matches_engine_recalculation": True,
        "game_runtime_verified": False, "addon_installed": False, "other_savedvariables_unchanged_in_stub": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x",encoding="utf-8") as handle: json.dump(report,handle,indent=2)
    print(json.dumps({"cases":len(outcomes),"bridge_checks":len(checks),"pass":True,"output":str(args.output)}))


if __name__ == "__main__": main()
