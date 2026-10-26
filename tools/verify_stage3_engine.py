"""Compare the offline port to unmodified installed CraftSim Lua 5.1 functions.

All game state and market inputs in this test are synthetic. Source files are
read-only. This does not demonstrate a live WoW collection or a whole engine
constructor working outside WoW. Lupa is an optional isolated test dependency.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wow_auction.knowledge_engine import evaluate_scenario, quality_thresholds
from tests.test_knowledge_engine import fixture, PRICES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lua-runtime", type=Path, required=True)
    parser.add_argument("--craftsim", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output_already_exists")
    sys.path.insert(0, str(args.lua_runtime.resolve()))
    from lupa.lua51 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(r'''
        local function noop() end
        local function stat(v, extra)
            return {value=v or 0,percentDivisionFactor=100,extraValues={extra or 0},
                GetPercent=function(self) return self.value/self.percentDivisionFactor end,
                GetExtraValue=function(self,i) return self.extraValues[i or 1] or 0 end}
        end
        function item(id)
            return {GetItemID=function() return id end, GetItemLink=function() return 'item:'..id end}
        end
        function wipe(t) for k in pairs(t) do t[k]=nil end end
        function CopyTable(t) local out={} for k,v in pairs(t) do out[k]=type(v)=='table' and CopyTable(v) or v end return out end
        tinsert=table.insert
        tContains=function(t, v) for _, x in pairs(t) do if x==v then return true end end return false end
        tAppendAll=function(t, xs) for _, x in pairs(xs) do table.insert(t,x) end end
        Options={QUALITY_BREAKPOINT_OFFSET=false,PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT=0.3}
        CS={CONST={AUCTION_HOUSE_CUT=0.95},
            CraftSimObject={extend=function() return {} end},
            DEBUG={RegisterLogger=function() return {LogDebug=noop} end},
            LOCAL={GetLocalizer=function() return function(s) return s end end},
            MODULES={RegisterModule=noop},
            DB={OPTIONS={Get=function(self,k) return Options[k] end},
                PRICE_OVERRIDE={GetResultOverridePrice=function() return nil end}},
            PRICE_SOURCE={GetMinBuyoutByItemID=function(self,id) return Prices[id],{isAHPrice=true} end},
            UTIL={FormatMoney=function(self,v) return tostring(v) end,IsGreyItem=function() return false end,
                GetMulticraftConstantByBaseYield=function() return MulticraftConstant end,
                toBits=function(self,n,count) local out={} for i=count,1,-1 do out[i]=n%2;n=math.floor(n/2) end return out end},
            GUTIL={GetFormatter=function() return {} end,RegisterCustomEvents=noop,
                Map=function(self,xs,f) local out={} for k,x in pairs(xs) do local y=f(x,k);if y~=nil then table.insert(out,y) end end return out end,
                Concat=function(self,xs) local out={} for _,ys in ipairs(xs) do for _,y in ipairs(ys) do table.insert(out,y) end end return out end}}
        CraftSimTSM={GetExpectedDeposit=function() return Deposit end}
        function makeRD()
            local rd={recipeID=100,isGear=false,supportsQualities=true,supportsCraftingStats=true,
                supportsResourcefulness=true,supportsMulticraft=true,isSalvageRecipe=false,subRecipeCostsEnabled=false,
                baseItemAmount=5,maxQuality=2,concentrating=false,concentrationCost=100,
                professionStats={skill=stat(100),recipeDifficulty=stat(200),multicraft=stat(20,0.1),
                    resourcefulness=stat(20,0.5),ingenuity=stat(30,0.1)},
                reagentData={requiredReagents={{hasQuality=true,requiredQuantity=2,items={
                    {item=item(200),quantity=2},{item=item(201),quantity=0}}}},
                    optionalReagentSlots={},finishingReagentSlots={},HasRequiredSelectableReagent=function() return false end}}
            rd.resultData=setmetatable({recipeData=rd,itemsByQuality={item(300),item(301)}},{__index=CS.ResultData})
            rd.priceData=setmetatable({recipeData=rd,qualityPriceList={},selfCraftedReagents={},reagentPriceInfos={}},{__index=CS.PriceData})
            rd.Update=function(self)
                self.resultData:Update();self.priceData:Update();self.averageProfitCached=CS.CALC:GetAverageProfit(self)
            end
            return rd
        end
    ''')
    files = ("Pricing/ProfitCalculation.lua", "Classes/ResultData.lua", "Classes/PriceData.lua",
             "Modules/RecipeInfo/RecipeInfo.lua", "Classes/RecipeData.lua")
    source_hashes = {}
    for rel in files:
        data = (args.craftsim / rel).read_bytes()
        source_hashes[rel] = hashlib.sha256(data).hexdigest()
        lua.execute(data.decode("utf-8-sig"), "CraftSim", lua.globals().CS)
    checked = 0
    maximum_error = Decimal(0)
    rows = []
    # 144 combinations exercise resourcefulness/multicraft, zero/positive/max procs,
    # two output quantities, deposits, reagent mixes and concentration.
    for base, chance, res_chance, supports_mc, concentrating, deposit in itertools.product(
            (1, 5), (0, 20, 100), (0, 20, 100), (False, True), (False, True), (0, 1000)):
        raw = fixture()
        raw["recipe"].update(baseItemAmount=base,minItemAmount=base,maxItemAmount=base,
                             supportsMulticraft=supports_mc,concentrating=concentrating)
        raw["stats"]["multicraft"]["value"] = chance
        raw["stats"]["resourcefulness"]["value"] = res_chance
        raw["engine"]["expected_deposit"] = deposit
        if concentrating:
            raw["result"]["expectedQuality"] = 2
            raw["result"]["expectedItem"] = raw["result"]["items"]["2"]
        globals_ = lua.globals()
        globals_.Prices = lua.table_from({int(k.split(":")[1]): v for k, v in PRICES.items()})
        globals_.Deposit, globals_.MulticraftConstant = deposit, 2.5
        rd = globals_.makeRD()
        rd.baseItemAmount, rd.supportsMulticraft, rd.concentrating = base, supports_mc, concentrating
        rd.professionStats.multicraft.value = chance
        rd.professionStats.resourcefulness.value = res_chance
        rd.Update(rd)
        mean, _ = globals_.CS.CALC.GetAverageProfit(globals_.CS.CALC, rd)
        output = evaluate_scenario(raw, PRICES)
        pairs = {"mean_profit": (Decimal(output["expected_profit_copper"]), mean),
                 "expected_yield": (Decimal(output["expected_output_per_craft"]), rd.resultData.expectedYieldPerCraft),
                 "crafting_cost": (Decimal(output["crafting_cost_copper"]), rd.priceData.craftingCosts),
                 "expected_resource_savings": (Decimal(output["expected_resourcefulness_savings_copper"]), rd.priceData.resourcefulnessSavedCostsAverage)}
        if concentrating:
            value, profit = globals_.CS.RecipeData.GetConcentrationValue(rd)
            pairs["concentration_expected"] = (Decimal(output["concentration"]["expected_spent"]), profit/value)
        for name, (python_value, lua_value) in pairs.items():
            error = abs(python_value - Decimal(str(lua_value)))
            maximum_error = max(maximum_error, error)
            assert error < Decimal("0.00000001"), (name, python_value, lua_value)
        assert output["expected_quality"] == rd.resultData.expectedQuality
        checked += 1
        rows.append({"base":base,"mc_percent":chance,"res_percent":res_chance,"supports_mc":supports_mc,
                     "concentrating":concentrating,"deposit":deposit,"pass":True})
    # Independently run stock PriceData for optional/finishing/selectable costs.
    for name in ("no_stats", "optional", "finishing", "selectable", "mixed_qualities"):
        raw = fixture(); rd = globals_.makeRD()
        globals_.Deposit, globals_.MulticraftConstant = 1000, 2.5
        if name == "no_stats":
            raw["recipe"].update(supportsCraftingStats=False,supportsResourcefulness=False,supportsMulticraft=False)
            rd.supportsCraftingStats,rd.supportsResourcefulness,rd.supportsMulticraft=False,False,False
        elif name == "mixed_qualities":
            raw["reagents"]["required"]["1"]["items"]["1"]["quantity"] = 1
            raw["reagents"]["required"]["1"]["items"]["2"]["quantity"] = 1
            rd.reagentData.requiredReagents[1]["items"][1].quantity=1
            rd.reagentData.requiredReagents[1]["items"][2].quantity=1
        else:
            count = 3 if name == "selectable" else 1
            section = "selected" if name == "selectable" else name
            slot = {"maxQuantity": count, "activeReagent":{"item":{"itemID":201,"itemLink":"item:201"}}}
            raw["reagents"][section] = slot if name == "selectable" else {"1": slot}
            globals_.RD=rd
            lua.execute("Optional={item=item(201),IsCurrency=function() return false end}; Slot={maxQuantity="+str(count)+",activeReagent=Optional,possibleReagents={Optional}}")
            if name == "selectable":
                rd.reagentData.requiredSelectableReagentSlot=globals_.Slot
                rd.reagentData.HasRequiredSelectableReagent=lua.eval("function() return true end")
            else:
                getattr(rd.reagentData,name+"ReagentSlots")[1]=globals_.Slot
        rd.Update(rd)
        mean, _ = globals_.CS.CALC.GetAverageProfit(globals_.CS.CALC,rd)
        output=evaluate_scenario(raw,PRICES)
        assert abs(Decimal(output["expected_profit_copper"])-Decimal(str(mean))) < Decimal("0.00000001")
        assert Decimal(output["crafting_cost_copper"]) == Decimal(str(rd.priceData.craftingCosts))
        checked+=1
        rows.append({"case":name,"pass":True})
    boundaries = 0
    for quality, difficulty, offset in itertools.product((1,2,3,5),(100,530),(False,True)):
        native = globals_.CS.RECIPE_INFO.GetQualityThresholds(globals_.CS.RECIPE_INFO, quality, difficulty, offset)
        source = list(native.values())
        python = quality_thresholds(quality, difficulty, offset)
        assert [Decimal(str(n)) for n in source] == python
        for skill in sorted(set([Decimal(0)] + [v + d for v in python for d in (-1,0,1)])):
            rd = globals_.makeRD()
            rd.maxQuality, rd.professionStats.skill.value, rd.professionStats.recipeDifficulty.value = quality, float(skill), difficulty
            rd.resultData.itemsByQuality = lua.table_from([globals_.item(300+i) for i in range(quality)])
            globals_.Options.QUALITY_BREAKPOINT_OFFSET = offset
            rd.resultData.Update(rd.resultData)
            assert rd.resultData.expectedQuality == 1 + sum(skill >= v for v in python)
            boundaries += 1
    result = {"captured_at":datetime.now(timezone.utc).isoformat(),"evidence_kind":"SOURCE_AND_SYN",
              "live_game_test":False,"whole_constructor_outside_wow":False,"craftsim_version":"27.0.7",
              "source_sha256":source_hashes,"formula_cases":checked,"quality_boundary_cases":boundaries,
              "maximum_absolute_error":str(maximum_error),"all_pass":True,"cases":rows,
              "python_sha256":hashlib.sha256((ROOT/"wow_auction/knowledge_engine.py").read_bytes()).hexdigest(),
              "limitations":["Synthetic primitive inputs; no real character context proof",
                             "Finite observed allocations, no global optimizer proof",
                             "Stock CraftSim conditional means are not a full integer item PMF",
                             "CraftSim double deposit retained for supported statistics branches"]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2);stream.write("\n")
    print(json.dumps({"formula_cases":checked,"quality_boundary_cases":boundaries,"all_pass":True,"output":str(args.output)}))


if __name__ == "__main__":
    main()
