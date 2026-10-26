"""Offline Lua5.1 tests of autonomous bridge batches; no WoW actions or installs."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.test_knowledge_craftsim import literal
from wow_auction.knowledge_craftsim import parse_bridge
from wow_auction.lua_data import parse_saved_variables


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lua-runtime",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--wow-root",type=Path,help="Optional real SavedVariables source, read only; game APIs remain stubs")
    args=parser.parse_args()
    if args.output.exists():raise SystemExit("output_already_exists")
    sys.path.insert(0,str(args.lua_runtime.resolve()))
    from lupa.lua51 import LuaRuntime,lua_type
    source=(ROOT/"addons/WowAuctionBridge/Passive.lua").read_text()
    def unpack(v):
        return {k:unpack(x) for k,x in v.items()} if lua_type(v)=="table" else v
    def setup(saved=None):
        lua=LuaRuntime(unpack_returned_tuples=True)
        lua.execute("""
            Clock=1000;Combat=false;Calls={};Timers={};Ticks={};Listeners={}
            GetServerTime=function() return Clock end
            UnitGUID=function() return Actor or 'Player-Synthetic-A' end
            GetBuildInfo=function() return '12.1.5','70575' end
            InCombatLockdown=function() return Combat end
            C_AddOns={GetAddOnMetadata=function() return '27.0.7' end}
            CraftSimDB={crafterDB={data={Synthetic={recipeInfos={[100]={recipeID=100}},
                professionInfos={[200]={profession=1}},operationInfos={[300]={recipeID=300}},
                cachedRecipeIDs={[1]={100,200,300}},professionGear={[1]={cached=true}},specializationData={}}}},
                itemRecipeDB={data={[555]={recipeID=100,itemID=555}}}}
            CraftSimAPI={RegisterEvents=function(self,obj) table.insert(Listeners,obj) end,
                GetRecipeData=function() error('forbidden engine constructor') end,
                GetCraftSim=function() error('forbidden engine object') end}
            local function called(name,id) table.insert(Calls,{name=name,id=id}) end
            C_TradeSkillUI={GetAllRecipeIDs=function() return ExtraIDs or {} end,
                GetRecipeInfo=function(id) called('info',id);return {recipeID=id,name='Synthetic'} end,
                GetRecipeSchematic=function(id) called('schematic',id);if FailAll or id==200 then return nil end
                    if id==300 then error('not ready') end;return {recipeID=id,quantityMin=1,quantityMax=1,outputItemID=555} end,
                GetCraftingOperationInfo=function(id) called('operation',id);if GiveOperation then return {recipeID=id,baseSkill=100} end;return nil end,
                OpenTradeSkill=function() error('forbidden UI') end,CraftRecipe=function() error('forbidden craft') end}
            ReloadUI=function() error('forbidden reload') end
            C_Timer={After=function(delay,fn) table.insert(Timers,fn) end,
                NewTicker=function(delay,fn) table.insert(Ticks,fn);return {} end}
            CreateFrame=function() return {RegisterEvent=function() end,
                SetScript=function(self,key,fn) OnEvent=fn end} end
            FlushOne=function() local current=Timers;Timers={};for _,fn in ipairs(current) do fn() end end
            FlushAll=function() local steps=0;while #Timers>0 and steps<20000 do FlushOne();steps=steps+1 end
                assert(steps<20000,'unbounded scheduler') end
            WowAuctionBridgeDB={schema_version=1,records={},sequence=0,context_epoch=1,dropped_records=0}
        """)
        if saved is not None:lua.execute("WowAuctionBridgeDB="+literal(saved))
        lua.execute(source,"WowAuctionBridge")
        return lua
    lua=setup();before=unpack(lua.globals().CraftSimDB);checks=[]
    lua.execute("OnEvent(nil,'PLAYER_LOGIN')")
    state=unpack(lua.globals().WowAuctionBridgeDB.passive)
    assert state["queued"]==3 and state["processed"]==0 and len(unpack(lua.globals().Calls))==0
    checks.append("login_schedules_without_recipe_open_or_scan")
    lua.execute("FlushOne()")
    state=unpack(lua.globals().WowAuctionBridgeDB.passive)
    assert state["processed"]==2 and state["cursor"]==3
    assert len(unpack(lua.globals().Calls))==6
    checks.append("two_recipes_per_tick_bound")
    saved=unpack(lua.globals().WowAuctionBridgeDB)
    lua=setup(saved);lua.execute("OnEvent(nil,'PLAYER_LOGIN');FlushAll()")
    state=unpack(lua.globals().WowAuctionBridgeDB.passive)
    assert state["processed"]==3 and state["status"]=="complete_available_scope"
    assert {x["id"] for x in unpack(lua.globals().Calls).values()}=={300}
    checks.append("saved_checkpoint_resumes_without_requerying_prefix")
    parsed=parse_bridge(("WowAuctionBridgeDB="+literal(unpack(lua.globals().WowAuctionBridgeDB))).encode(),observed_at=1001)
    assert parsed["profiles"]==[] and parsed["passive"]["record_count"]==3
    assert parsed["passive"]["observed_api_tables"]==4 and parsed["passive"]["unavailable_api_tables"]==5
    checks.append("unavailable_game_data_not_fabricated_engine_profile")
    first_time=state["records"][100]["last_good"]["schematic"]["captured_at"]
    lua.execute("Clock=1100;FailAll=true;Ticks[1]();FlushAll()")
    state=unpack(lua.globals().WowAuctionBridgeDB.passive)
    assert state["records"][100]["schematic"]["status"]=="data_unavailable"
    assert state["records"][100]["last_good"]["schematic"]["captured_at"]==first_time
    checks.append("periodic_failure_preserves_last_good_with_original_clock")
    lua.execute("Clock=1150;GiveOperation=true;Actor='Player-Synthetic-A';Ticks[1]();FlushAll();Clock=1160;GiveOperation=false;Actor='Player-Synthetic-B';Ticks[1]();FlushAll()")
    row=unpack(lua.globals().WowAuctionBridgeDB.passive.records)[100]
    assert row["operation_info"]["source_actor"]["player_guid"]=="Player-Synthetic-B"
    assert row["last_good"]["operation_info"]["source_actor"]["player_guid"]=="Player-Synthetic-A"
    assert row["last_good"]["operation_info"]["captured_at"]==1150
    checks.append("actor_b_nil_does_not_relabel_actor_a_operation")
    lua.execute("ExtraIDs={};for i=1,110 do ExtraIDs[i]=1000+i end;Clock=1200;Listeners[1]:CRAFTSIM_PROFESSION_INITIALIZED();FlushAll()")
    state=unpack(lua.globals().WowAuctionBridgeDB.passive)
    assert state["processed"]==113 and state["record_count"]==113 and state["coverage_complete"]
    checks.append("full_available_scope_over_100_records_without_ring_eviction")
    assert unpack(lua.globals().CraftSimDB)==before
    checks.append("foreign_savedvariables_and_engine_untouched")
    lua.execute("Combat=true;Clock=1300;Ticks[1]();FlushAll()")
    assert lua.globals().WowAuctionBridgeDB.passive.status=="paused_combat"
    lua.execute("Combat=false;OnEvent(nil,'PLAYER_REGEN_ENABLED');FlushAll()")
    assert lua.globals().WowAuctionBridgeDB.passive.status=="complete_available_scope"
    checks.append("combat_pause_and_automatic_resume")
    prior=unpack(lua.globals().WowAuctionBridgeDB.passive)
    lua.execute("CraftSimDB.crafterDB.data.Synthetic.cachedRecipeIDs=5;Ticks[1]();FlushAll()")
    state=unpack(lua.globals().WowAuctionBridgeDB.passive)
    assert state["latest_attempt_status"]=="discovery_error" and state["records"]==prior["records"]
    checks.append("malformed_source_retains_last_good")
    # Budget limit is explicit and leaves the next recipe at the checkpoint.
    lua2=setup();lua2.execute("OnEvent(nil,'PLAYER_LOGIN');WowAuctionBridgeDB.passive.storage_bytes=8388608;FlushAll()")
    assert lua2.globals().WowAuctionBridgeDB.passive.status=="storage_budget_reached"
    assert lua2.globals().WowAuctionBridgeDB.passive.cursor==1
    assert lua2.globals().WowAuctionBridgeDB.passive.coverage_complete is False
    checks.append("storage_limit_retains_incomplete_checkpoint")
    real_cache=None
    if args.wow_root:
        files=list((args.wow_root/"WTF/Account").glob("*/SavedVariables/CraftSim.lua"))
        if len(files)!=1:raise SystemExit("real_cache_account_not_unique")
        real_bytes=files[0].read_bytes()
        real=parse_saved_variables(real_bytes)["CraftSimDB"]
        lua3=setup()
        def to_lua(value):
            if isinstance(value,dict):
                result=lua3.table()
                for key,child in value.items():result[key]=to_lua(child)
                return result
            return value
        lua3.globals().CraftSimDB=to_lua(real)
        lua3.execute("C_TradeSkillUI={};OnEvent(nil,'PLAYER_LOGIN');FlushAll()")
        state=unpack(lua3.globals().WowAuctionBridgeDB.passive)
        assert state["status"]=="complete_available_scope",{k:v for k,v in state.items() if k not in ('records','contexts','record_sizes')}
        parsed=parse_bridge(("WowAuctionBridgeDB="+literal(unpack(lua3.globals().WowAuctionBridgeDB))).encode(),observed_at=1001)
        assert parsed["profiles"]==[] and parsed["passive"]["observed_api_tables"]==0
        assert unpack(lua3.globals().CraftSimDB)==real
        real_cache={"kind":"REAL_SAVEDVARIABLES_WITH_SYN_GAME_APIS_UNAVAILABLE",
            "source_sha256":hashlib.sha256(real_bytes).hexdigest(),"source_bytes":len(real_bytes),
            "queued":state["queued"],"processed":state["processed"],"records":state["record_count"],
            "source_counts":state["source_counts"],"storage_bytes_estimate":state["storage_bytes"],
            "source_statuses":state["source_statuses"],
            "cache_contexts":len(state["contexts"]),"foreign_sv_unchanged":True,
            "observed_live_api_tables":0,"engine_profiles":0,"pass":True}
    report={"captured_at":datetime.now(timezone.utc).isoformat(),"kind":"SYN_LUA_AUTONOMOUS_PASSIVE_BRIDGE",
            "lua_version":lua.eval("_VERSION"),"checks":checks,"pass":True,
            "manual_recipe_open_calls":0,"manual_scan_calls":0,"craft_calls":0,"reload_calls":0,
            "constructor_calls":0,"game_runtime_verified":False,
            "real_cache_probe":real_cache,
            "project_hashes":{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in
                ["addons/WowAuctionBridge/Passive.lua","addons/WowAuctionBridge/Bridge.lua","addons/WowAuctionBridge/WowAuctionBridge.toc",
                 "wow_auction/knowledge_craftsim.py","tools/verify_stage3_bridge_passive.py"]}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as f:json.dump(report,f,indent=2)
    print(json.dumps({"checks":len(checks),"pass":True,"output":str(args.output)}))

if __name__=="__main__":main()
