-- Autonomous use of CraftSim's public calculation API. It may maintain its own
-- normal caches; we never assign CraftSimDB, alter CraftSim code or invoke craft,
-- scan, profession-opening, simulation UI, inventory, or reload operations.
local Engine = {running = false, registered = false}
local MAX_IDS, MAX_BYTES, MAX_NODES = 20000, 8 * 1024 * 1024, 120000
local DELAY, SUPPORTED_VERSION, SUPPORTED_BUILD = 0.2, "27.0.7", "12.1.5.70575"
local function validDB()
    return WowAuctionBridgeCapture and WowAuctionBridgeCapture.ValidDatabase()
end
local function finite(v) return type(v) == "number" and v == v and math.abs(v) < math.huge end
local function integer(v) return finite(v) and v > 0 and v < 2147483648 and v == math.floor(v) end
local function build()
    local version, number = GetBuildInfo()
    return version .. "." .. number
end
local function currentActor()
    local guid = type(UnitGUID) == "function" and UnitGUID("player")
    if type(guid) ~= "string" or #guid == 0 or #guid > 128 then return nil end
    return {player_guid = guid, kind = "logged_in_player_request"}
end
local function query(name, ...)
    local fn = C_TradeSkillUI and C_TradeSkillUI[name]
    if type(fn) ~= "function" then return nil end
    local ok, data = pcall(fn, ...)
    if ok then return data end
end
local function sameActor(a, b) return a and b and a.player_guid == b.player_guid end
local function discover(cs)
    local ids, seen, limited = {}, {}, false
    local function add(id)
        if not integer(id) or seen[id] then return end
        seen[id] = true
        if #ids >= MAX_IDS then limited = true; return end
        table.insert(ids, id)
    end
    -- Only the logged-in crafter is evaluated. Never let CraftSim's missing-alt
    -- operation fallback silently substitute the current character's stats.
    local player = cs.UTIL:GetPlayerCrafterData()
    local uid = player and player.name and player.realm and player.name .. "-" .. player.realm
    local all = CraftSimDB and CraftSimDB.crafterDB and CraftSimDB.crafterDB.data
    local cached = uid and type(all) == "table" and all[uid]
    if type(cached) == "table" then
        for _, name in ipairs({"recipeInfos", "operationInfos", "professionInfos"}) do
            for id in pairs(cached[name] or {}) do add(id) end
        end
        for _, list in pairs(cached.cachedRecipeIDs or {}) do
            for _, id in pairs(list) do add(id) end
        end
    end
    local live = query("GetAllRecipeIDs")
    if type(live) == "table" then for _, id in pairs(live) do add(id) end end
    table.sort(ids)
    return ids, limited, type(live) == "table" and "observed" or "unavailable"
end
local function preflight(id)
    if query("IsTradeSkillLinked") == true or query("IsTradeSkillGuild") == true then return nil, "foreign_profession_view" end
    if query("IsRecipeProfessionLearned", id) ~= true then return nil, "current_crafter_not_confirmed" end
    local info = query("GetRecipeInfo", id)
    local profession = query("GetProfessionInfoByRecipeID", id)
    local schematic = query("GetRecipeSchematic", id, false)
    local operation = query("GetCraftingOperationInfo", id, {}, nil, false)
    if type(info) ~= "table" or not integer(info.categoryID) then return nil, "recipe_info_unavailable" end
    if type(profession) ~= "table" or not integer(profession.professionID) or not profession.profession then
        return nil, "profession_info_unavailable"
    end
    if type(schematic) ~= "table" then return nil, "schematic_unavailable" end
    if type(operation) ~= "table" or not finite(operation.baseSkill) or not finite(operation.bonusSkill) then
        return nil, "operation_info_unavailable"
    end
    if info.isRecraft or info.isSalvageRecipe then return nil, "recipe_class_not_supported" end
    if info.supportsQualities and info.maxQuality ~= 1 and info.maxQuality ~= 2 and info.maxQuality ~= 3 and info.maxQuality ~= 5 then
        return nil, "quality_domain_not_supported"
    end
    return {info = info, profession = profession, schematic = schematic, operation = operation}
end
local function plans(rd)
    local list = {{kind = "quality_low"}, {kind = "quality_high"}}
    if rd.supportsQualities then
        for q = 1, rd.maxQuality do table.insert(list, {kind = "craftsim_optimized", target_quality = q}) end
    end
    local expanded = {}
    for _, row in ipairs(list) do
        table.insert(expanded, {kind = row.kind, target_quality = row.target_quality, concentrating = false})
        if rd.supportsQualities then
            table.insert(expanded, {kind = row.kind, target_quality = row.target_quality, concentrating = true})
        end
    end
    return expanded
end
local function setAllocation(rd, plan)
    rd.concentrating = false
    -- This mutates only our newly constructed RecipeData, never the open recipe.
    for _, slot in ipairs(rd.reagentData.requiredReagents) do
        local chosen
        for _, entry in ipairs(slot.items) do
            entry.quantity = 0
            if not chosen or (plan.kind == "quality_high" and (entry.qualityID or 0) > (chosen.qualityID or 0))
                or (plan.kind ~= "quality_high" and (entry.qualityID or 0) < (chosen.qualityID or 0)) then chosen = entry end
        end
        if not chosen then error("empty_required_slot", 0) end
        chosen.quantity = slot.requiredQuantity
    end
    rd:SetNonQualityReagentsMax()
    if plan.kind == "craftsim_optimized" then
        rd:OptimizeReagents({minQuality = plan.target_quality, maxQuality = plan.target_quality, highestProfit = false})
    end
    rd.concentrating = plan.concentrating
end
local function operationModel(rd, cs)
    local required = rd.reagentData:GetRequiredCraftingReagentInfoTbl()
    local all = rd.reagentData:GetCraftingReagentInfoTbl()
    local base = query("GetCraftingOperationInfo", rd.recipeID, {}, nil, false)
    local selected = query("GetCraftingOperationInfo", rd.recipeID, required, nil, false)
    local full = query("GetCraftingOperationInfo", rd.recipeID, all, nil, false)
    if type(base) ~= "table" or type(selected) ~= "table" or not finite(base.baseSkill)
        or not finite(base.bonusSkill) or not finite(selected.baseSkill) or not finite(selected.bonusSkill) then
        return nil, "selected_operation_unavailable"
    end
    if rd.supportsQualities and (type(full) ~= "table" or not finite(full.concentrationCost)) then
        return nil, "concentration_operation_unavailable"
    end
    local thresholds = {}
    if rd.supportsQualities then
        thresholds = cs.RECIPE_INFO:GetQualityThresholds(rd.maxQuality, rd.professionStats.recipeDifficulty.value,
            cs.DB.OPTIONS:Get("QUALITY_BREAKPOINT_OFFSET"))
    end
    return {schema_version = 1, quality_thresholds = thresholds,
        multicraft_constant = rd.supportsMulticraft and cs.UTIL:GetMulticraftConstantByBaseYield(rd.baseItemAmount) or nil,
        resourcefulness_constant = cs.DB.OPTIONS:Get("PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT"),
        reagent_skill_status = "observed", reagent_skill = selected.baseSkill + selected.bonusSkill - base.baseSkill - base.bonusSkill,
        concentration_cost_status = type(full) == "table" and finite(full.concentrationCost) and "observed" or "unavailable",
        concentration_cost = type(full) == "table" and full.concentrationCost or nil,
        full_operation_skill = type(full) == "table" and finite(full.baseSkill) and finite(full.bonusSkill)
            and full.baseSkill + full.bonusSkill or nil,
        operation_source = "C_TradeSkillUI.GetCraftingOperationInfo", operation_captured_at = GetServerTime()}
end
local function specializationObserved(rd)
    if not rd.supportsSpecializations or rd.isOldWorldRecipe then return true end
    local config = C_ProfSpecs and C_ProfSpecs.GetConfigIDForSkillLine and C_ProfSpecs.GetConfigIDForSkillLine(rd.professionData.skillLineID)
    if not config or config <= 0 or not rd.specializationData or not rd.specializationData.isImplemented
        or type(rd.specializationData.nodeData) ~= "table" or not next(rd.specializationData.nodeData)
        or rd.specializationDataCached or not C_Traits or type(C_Traits.GetNodeInfo) ~= "function" then return false end
    -- RecipeData's current-player fallback to cached specialization does NOT set
    -- specializationDataCached. Independently check every relevant actual rank.
    for _, node in pairs(rd.specializationData.nodeData) do
        local info = C_Traits.GetNodeInfo(config, node.nodeID)
        if type(info) ~= "table" or not finite(info.activeRank) or info.activeRank - 1 ~= node.rank then return false end
    end
    return true
end
local function makeScenario(rd, cs, plan, actor, epoch)
    if not specializationObserved(rd) then return nil, "live_specialization_unavailable" end
    setAllocation(rd, plan)
    local model, reason = operationModel(rd, cs)
    if not model then return nil, reason end
    -- Clear native value caches before the actual Update, so the direct current
    -- API observation is not paired with a prior allocation's cached zero.
    cs.RecipeData.ClearConcentrationCostCache()
    cs.ReagentData.ClearSkillFromReagentsCache()
    rd:Update()
    if math.abs(rd.reagentData:GetSkillFromRequiredReagents() - model.reagent_skill) > 0.000001 then
        return nil, "reagent_skill_source_mismatch"
    end
    if rd.supportsQualities and math.abs(rd.concentrationCost - model.concentration_cost) > 0.000001 then
        return nil, "concentration_source_mismatch"
    end
    if not finite(model.full_operation_skill) or math.abs(rd.professionStats.skill.value - model.full_operation_skill) > 0.000001 then
        return nil, "total_skill_source_mismatch"
    end
    model.specialization_rank_status = rd.supportsSpecializations and not rd.isOldWorldRecipe and "observed" or "not_applicable"
    -- Re-evaluate thresholds after Update; optional reagents may change difficulty.
    if rd.supportsQualities then
        model.quality_thresholds = cs.RECIPE_INFO:GetQualityThresholds(rd.maxQuality, rd.professionStats.recipeDifficulty.value,
            cs.DB.OPTIONS:Get("QUALITY_BREAKPOINT_OFFSET"))
    end
    local row = WowAuctionBridgeCapture.BuildPayload(rd, cs)
    row.event = "WAB_AUTONOMOUS_ENGINE_CAPTURE"
    row.engine.mean_origin = "autonomous_native_engine_result"
    row.engine.model = model
    row.source_actor = actor
    row.context_epoch = epoch
    row.scenario = {kind = plan.kind, target_quality = plan.target_quality, concentrating = plan.concentrating,
        allocation_scope = "required_reagents_no_optional_search",
        id = plan.kind .. (plan.target_quality and "_q" .. plan.target_quality or "") .. (plan.concentrating and ":concentration" or ":normal")}
    row, model, reason = WowAuctionBridgeCapture.CopyLiteral(row)
    -- This collection has its own local sequence. Do not disturb the observer
    -- ring's sequence = retained records + dropped records invariant.
    row.sequence = 1
    return row, nil, model, reason
end
local function loadRecipe(id, cs)
    local available, reason = preflight(id)
    if not available then return nil, reason end
    local rd = CraftSimAPI:GetRecipeData({recipeID = id, isRecraft = false, isWorkOrder = false})
    if type(rd) ~= "table" or rd.recipeID ~= id or not rd.recipeSchematic or not rd.resultData or not rd.priceData or not rd.baseOperationInfo
        or type(rd.Update) ~= "function" or not rd:IsCrafter() then return nil, "engine_recipe_not_ready" end
    if rd.recipeInfoCached or rd.professionInfoCached or rd.operationInfoCached then return nil, "cached_current_context_unverified" end
    if rd.orderData or rd.isRecraft or rd.isSalvageRecipe or rd.isAlchemicalExperimentation or rd.isQuestRecipe then
        return nil, "recipe_class_not_supported"
    end
    if not specializationObserved(rd) then return nil, "live_specialization_unavailable" end
    return rd
end
function Engine:FinishRecipe()
    local state, job = WowAuctionBridgeDB.engine, self.job
    local key = state.source_actor.player_guid .. ":" .. job.id
    local previous = state.records[key]
    local attemptedAt = GetServerTime()
    local row = {recipe_id = job.id, source_actor = state.source_actor, attempted_at = attemptedAt, diagnostics = job.diagnostics,
        status = #job.scenarios == 0 and "unavailable" or #job.diagnostics == 0 and "complete" or "partial"}
    if #job.scenarios > 0 then
        row.last_good = {captured_at = attemptedAt, context_epoch = state.context_epoch, game_build = state.build,
            craftsim_version = state.craftsim_version, scenarios = job.scenarios, scenario_count = #job.scenarios,
            coverage = "finite_standard_allocations"}
    elseif type(previous) == "table" then row.last_good = previous.last_good end
    local old = state.record_sizes[key] or {bytes = 0, nodes = 0}
    local size = #job.scenarios > 0 and {bytes = job.bytes + 2048, nodes = job.nodes + 64}
        or old.bytes > 0 and old or {bytes = 2048, nodes = 64}
    local totalBytes, totalNodes = state.storage_bytes - old.bytes + size.bytes, state.storage_nodes - old.nodes + size.nodes
    if totalBytes > MAX_BYTES or totalNodes > MAX_NODES then
        state.status, state.coverage_complete, state.blocked_recipe_id = "storage_budget_reached", false, job.id
        self.running, self.job = false, nil
        return
    end
    state.records[key], state.record_sizes[key] = row, size
    state.storage_bytes, state.storage_nodes = totalBytes, totalNodes
    state.processed = state.processed + 1
    if row.status ~= "complete" then state.failed_recipes = state.failed_recipes + 1 end
    state.cursor = state.cursor + 1
    self.job = nil
end
function Engine:Tick()
    if not self.running or not validDB() then self.running = false; return end
    local state = WowAuctionBridgeDB.engine
    if state.context_epoch ~= WowAuctionBridgeDB.context_epoch or not sameActor(state.source_actor, currentActor()) then
        state.status, state.coverage_complete = "context_changed", false
        self.running, self.job = false, nil
        return
    end
    if InCombatLockdown and InCombatLockdown() then
        state.status = "paused_combat"; C_Timer.After(1, function() Engine:Tick() end); return
    end
    state.status = "running"
    if self.cs.SIMULATION_MODE and self.cs.SIMULATION_MODE.isActive then
        state.status, state.coverage_complete = "simulation_mode_active", false
        self.running, self.job = false, nil
        return
    end
    if state.cursor > #state.queue then
        state.status = state.failed_recipes == 0 and not state.enumeration_limited and "complete" or "partial"
        -- Completion refers only to enumerated finite scenarios. It is not full
        -- recipe/world/optional-allocation coverage.
        state.coverage_complete = false
        state.finished_at = GetServerTime(); self.running = false; return
    end
    if not self.job then
        local id = state.queue[state.cursor]
        self.job = {id = id, scenarios = {}, diagnostics = {}, bytes = 0, nodes = 0, cursor = 1}
        local started = debugprofilestop and debugprofilestop()
        local ok, rd, reason = pcall(loadRecipe, id, self.cs)
        if started then state.longest_step_ms = math.max(state.longest_step_ms or 0, debugprofilestop() - started) end
        if not ok or not rd then
            table.insert(self.job.diagnostics, ok and reason or "native_constructor_error")
            self:FinishRecipe()
        else self.job.rd, self.job.plans = rd, plans(rd) end
    else
        local job = self.job
        local started = debugprofilestop and debugprofilestop()
        local ok, row, reason, bytes, nodes = pcall(makeScenario, job.rd, self.cs, job.plans[job.cursor], state.source_actor, state.context_epoch)
        if ok and row then
            row.sequence = job.cursor
            table.insert(job.scenarios, row); job.bytes, job.nodes = job.bytes + bytes, job.nodes + nodes
        else table.insert(job.diagnostics, ok and reason or "native_calculation_or_export_error") end
        if started then state.longest_step_ms = math.max(state.longest_step_ms or 0, debugprofilestop() - started) end
        job.cursor = job.cursor + 1
        if job.cursor > #job.plans then self:FinishRecipe() end
    end
    if self.running then C_Timer.After(DELAY, function() Engine:Tick() end) end
end
function Engine:Start(reason)
    if self.running or not validDB() then return end
    local previous = WowAuctionBridgeDB.engine
    if not CraftSimAPI or type(CraftSimAPI.GetRecipeData) ~= "function" or type(CraftSimAPI.GetCraftSim) ~= "function" then return end
    local actor, version, gameBuild = currentActor(), C_AddOns.GetAddOnMetadata("CraftSim", "Version"), build()
    if not actor then return end
    local state = {schema_version = 1, exporter_version = "0.3.3", build = gameBuild, craftsim_version = version,
        status = "running", scope = "available_current_actor_recipes", started_at = GetServerTime(), reason = reason,
        context_epoch = WowAuctionBridgeDB.context_epoch, source_actor = actor, coverage_complete = false,
        queue = {}, queued = 0, processed = 0, cursor = 1, failed_recipes = 0, enumeration_limited = false,
        records = type(previous) == "table" and previous.records or {},
        record_sizes = type(previous) == "table" and previous.record_sizes or {},
        storage_bytes = type(previous) == "table" and previous.storage_bytes or 0,
        storage_nodes = type(previous) == "table" and previous.storage_nodes or 0}
    WowAuctionBridgeDB.engine = state
    if version ~= SUPPORTED_VERSION or gameBuild ~= SUPPORTED_BUILD then state.status = "version_unsupported"; return end
    self.cs = CraftSimAPI:GetCraftSim()
    -- Simulation UI changes the native concentration calculation globally. Wait
    -- until it is naturally closed instead of changing another addon's settings.
    if self.cs.SIMULATION_MODE and self.cs.SIMULATION_MODE.isActive then state.status = "simulation_mode_active"; return end
    local ok, ids, limited, source = pcall(discover, self.cs)
    if not ok then state.status = "source_shape_error"; return end
    state.queue, state.queued, state.enumeration_limited, state.game_index_status = ids, #ids, limited, source
    if #ids == 0 then state.status = "no_available_recipe_ids"; return end
    self.running = true
    C_Timer.After(DELAY, function() Engine:Tick() end)
end
function Engine:CRAFTSIM_PROFESSION_INITIALIZED() self:Start("profession_ready") end
function Engine:CRAFTSIM_PROFESSION_OPENED() self:Start("profession_opened_naturally") end
function Engine:Register()
    if not self.registered and CraftSimAPI and CraftSimAPI.RegisterEvents then
        CraftSimAPI:RegisterEvents(self, {"CRAFTSIM_PROFESSION_INITIALIZED", "CRAFTSIM_PROFESSION_OPENED"})
        self.registered = true
    end
end
local frame = CreateFrame("Frame")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("ADDON_LOADED")
frame:SetScript("OnEvent", function(_, event, name)
    if event == "PLAYER_LOGIN" then
        Engine:Register(); Engine:Start("login")
        if C_Timer.NewTicker then Engine.ticker = C_Timer.NewTicker(300, function() Engine:Start("periodic") end) end
    elseif event == "ADDON_LOADED" and name == "CraftSim" then Engine:Register() end
end)
