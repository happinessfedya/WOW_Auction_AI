-- Synthetic WoW/native-constructor boundary for the real own-addon scheduler.
-- Real installed ProfitCalculation and ResultData are loaded by the verifier.
Clock=1000; Combat=false; Timers={}; Tickers={}; Frames={}; Listeners={}; ConstructorCalls=0
GetServerTime=function() return Clock end
UnitGUID=function() return Actor or 'Player-Synthetic-A' end
GetBuildInfo=function() return '12.1.5','70575','Oct 2026',120105 end
GetLocale=function() return 'enUS' end
InCombatLockdown=function() return Combat end
C_AddOns={GetAddOnMetadata=function() return Version or '27.0.7' end}
C_Timer={After=function(_,fn) table.insert(Timers,fn) end,NewTicker=function(_,fn) table.insert(Tickers,fn);return {} end}
CreateFrame=function() local f={RegisterEvent=function() end,SetScript=function(self,_,fn) self.event=fn end};table.insert(Frames,f);return f end
FlushOne=function() local current=Timers;Timers={};for _,fn in ipairs(current) do fn() end end
FlushAll=function() local n=0;while #Timers>0 and n<2000 do FlushOne();n=n+1 end;assert(n<2000,'unbounded scheduler') end
ReloadUI=function() error('forbidden reload') end
ShowUIPanel=function() error('forbidden UI') end
local function item(id) return {GetItemID=function() return id end,GetItemLink=function() return 'item:'..id end} end
local function stat(value,extra)
 return {value=value,extraValues={[1]=extra or 0},percentDivisionFactor=100,
  GetPercent=function(self) return self.value/100 end,GetExtraValue=function(self) return self.extraValues[1] end}
end
local function stats(skill)
 return {skill=stat(skill or 60),recipeDifficulty=stat(100),multicraft=stat(10),resourcefulness=stat(20),ingenuity=stat(0),craftingspeed=stat(0)}
end
CraftSimDB={optionsDB={data={TSM_DEPOSIT_ENABLED=false,QUALITY_BREAKPOINT_OFFSET=false,
 PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT=0.3}},
 crafterDB={data={['Synthetic-Test']={recipeInfos={[100]={recipeID=100}},operationInfos={[100]={}},professionInfos={[100]={}}}}}}
local function skill(reagents)
 local s=0
 for _,r in ipairs(reagents or {}) do if r.reagent.itemID==201 then s=s+25*r.quantity end end
 return s
end
C_TradeSkillUI={
 GetAllRecipeIDs=function() return ExtraIDs or {100} end,
 IsTradeSkillLinked=function() return Linked or false end,
 IsTradeSkillGuild=function() return false end,
 IsRecipeProfessionLearned=function() return not ProfessionUnknown end,
 GetRecipeInfo=function(id) return {recipeID=id,categoryID=10,supportsQualities=true,maxQuality=2} end,
 GetProfessionInfoByRecipeID=function() return {professionID=1,profession=1} end,
 GetRecipeSchematic=function() return {quantityMin=1,quantityMax=1} end,
 GetCraftingOperationInfo=function(id,reagents)
  if Unavailable or (SelectedUnavailable and #reagents>0) then return nil end
  return {baseSkill=60,bonusSkill=skill(reagents),concentrationCost=skill(reagents)>=40 and 0 or 100}
 end,
 OpenTradeSkill=function() error('forbidden UI') end,
 CraftRecipe=function() error('forbidden craft') end,
 CraftSalvage=function() error('forbidden craft') end,
}
C_ProfSpecs={GetConfigIDForSkillLine=function() return 1 end}
C_Traits={GetNodeInfo=function() if MissingRank then return nil end;return {activeRank=ActualRank or 4} end}
C_Item={GetItemInfo=function() return 'Synthetic',nil,2 end}
CS={CONST={AUCTION_HOUSE_CUT=0.95,GENERAL_OPTIONS_DEFAULTS={}},SIMULATION_MODE={isActive=false},
 DEBUG={RegisterLogger=function() return {LogDebug=function() end} end},
 CraftSimObject={extend=function() return {} end},
 DB={OPTIONS={Get=function(_,key) return CraftSimDB.optionsDB.data[key] end}},
 UTIL={GetPlayerCrafterData=function() return {name='Synthetic',realm='Test'} end,
 GetMulticraftConstantByBaseYield=function() return 2.5 end,IsGreyItem=function() return false end,
 toBits=function(_,n,count) local out={} for i=count,1,-1 do out[i]=n%2;n=math.floor(n/2) end return out end,
 FormatMoney=function(_,n) return tostring(n) end},
 GUTIL={ColorizeText=function(_,s) return s end,COLORS={RED='red'}},
 RECIPE_INFO={GetQualityThresholds=function(_,max,diff,offset) return {diff+(offset and 1 or 0)} end},
 RecipeData={ClearConcentrationCostCache=function() end},ReagentData={ClearSkillFromReagentsCache=function() end}}
CraftSimTSM={GetExpectedDeposit=function() return 0 end}
local function makeRecipe(id)
 ConstructorCalls=ConstructorCalls+1
 -- Models the legitimate public API's own caching side effect.
 CraftSimDB.native_constructor_count=ConstructorCalls
 local rd={recipeID=id,categoryID=10,expansionID=11,concentrating=false,concentrationCost=100,
 learned=true,isSimulationModeData=false,supportsQualities=true,supportsCraftingStats=true,
 supportsMulticraft=true,supportsResourcefulness=true,supportsSpecializations=true,
 isGear=false,isSoulbound=false,isRecraft=false,isSalvageRecipe=false,isOldWorldRecipe=false,
 baseItemAmount=1,minItemAmount=1,maxItemAmount=1,maxQuality=2,subRecipeCostsEnabled=false,
 specializationDataCached=false,operationInfoCached=false,professionGearCached=true,
 recipeInfoCached=false,professionInfoCached=false,crafterData={name='Synthetic',realm='Test'},
 professionData={skillLineID=1},recipeSchematic={},baseOperationInfo={},
 professionStats=stats(),baseProfessionStats=stats(),professionStatModifiers=stats(0),
 professionGearSet={},buffData={buffs={}},specializationData={isImplemented=true,nodeData={{nodeID=1,rank=3}}},
 cooldownData={isCooldownRecipe=false},concentrationData={amount=500,maxQuantity=1000,lastUpdated=Clock}}
 rd.reagentData={requiredReagents={
  {requiredQuantity=2,dataSlotIndex=1,hasQuality=true,items={{item=item(200),qualityID=1,quantity=0},{item=item(201),qualityID=2,quantity=0}}},
  {requiredQuantity=1,dataSlotIndex=2,items={{item=item(202),qualityID=1,quantity=0}}}},
  optionalReagentSlots={},finishingReagentSlots={}}
 rd.reagentData.GetRequiredCraftingReagentInfoTbl=function(self)
  local out={};for _,entry in ipairs(self.requiredReagents[1].items) do
   table.insert(out,{reagent={itemID=entry.item:GetItemID()},quantity=entry.quantity,dataSlotIndex=1}) end;return out end
 rd.reagentData.GetCraftingReagentInfoTbl=rd.reagentData.GetRequiredCraftingReagentInfoTbl
 rd.reagentData.GetSkillFromRequiredReagents=function(self) return skill(self:GetRequiredCraftingReagentInfoTbl()) end
 rd.resultData={recipeData=rd,itemsByQuality={item(300),item(301)}}
 rd.resultData.Update=function(self) return CS.ResultData.Update(self) end
 rd.priceData={qualityPriceList={10000,20000},reagentPriceInfos={
  [200]={itemPrice=1000,priceInfo={isAHPrice=true}},[201]={itemPrice=1500,priceInfo={isAHPrice=true}},[202]={itemPrice=50,priceInfo={isAHPrice=true}}},
  craftingCostsFixed=0}
 rd.IsCrafter=function() return true end
 rd.SetNonQualityReagentsMax=function(self) self.reagentData.requiredReagents[2].items[1].quantity=1 end
 rd.OptimizeReagents=function(self,options)
  Optimizations=(Optimizations or 0)+1
  local entries=self.reagentData.requiredReagents[1].items
  entries[1].quantity=options.maxQuality==1 and 2 or 0;entries[2].quantity=options.maxQuality==2 and 2 or 0
 end
 rd.Update=function(self)
  self.professionStats.skill.value=60+self.reagentData:GetSkillFromRequiredReagents()
  self.concentrationCost=C_TradeSkillUI.GetCraftingOperationInfo(id,self.reagentData:GetCraftingReagentInfoTbl()).concentrationCost
  self.resultData:Update()
  local cost=0;for _,slot in ipairs(self.reagentData.requiredReagents) do
   for _,entry in ipairs(slot.items) do cost=cost+entry.quantity*self.priceData.reagentPriceInfos[entry.item:GetItemID()].itemPrice end end
  self.priceData.craftingCosts=cost;self.priceData.craftingCostsRequired=cost
  self.priceData.craftingCostsNoOrderReagents=cost
  self.priceData.resourcefulnessSavedCostsAverage=CS.CALC:GetResourcefulnessSavedCosts(self)*0.2
  self.averageProfitCached=CS.CALC:GetAverageProfit(self)
 end
 return rd
end
CraftSimAPI={RegisterEvents=function(_,obj) table.insert(Listeners,obj) end,
 GetCraftSim=function() return CS end,GetRecipeData=function(_,options) return makeRecipe(options.recipeID) end,
 GetOpenRecipeData=function() return nil end}
WowAuctionBridgeDB={schema_version=1,records={},sequence=0,context_epoch=1,dropped_records=0}
