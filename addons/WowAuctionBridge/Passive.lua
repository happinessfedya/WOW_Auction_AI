-- Autonomous observation of available data. No CraftSim constructor/repository
-- getters: several such getters initialize foreign SavedVariables.
local ADDON = ...
local Passive = {running = false, registered = false}
local MAX_IDS, MAX_BYTES, MAX_NODES = 20000, 8 * 1024 * 1024, 120000
local DELAY, PER_TICK, TICK_MS = 0.2, 2, 5
local function integer(value) return type(value) == "number" and value > 0 and value < 2147483648 and value == math.floor(value) end
local function validDB() return type(WowAuctionBridgeDB) == "table" and WowAuctionBridgeDB.schema_version == 1 end
local function copy(value, budget, depth, seen)
    budget.nodes = budget.nodes + 1
    budget.bytes = budget.bytes + (type(value) == "string" and #value * 4 + 8 or 64)
    if budget.nodes > MAX_NODES or budget.bytes > MAX_BYTES or depth > 16 then error("passive_copy_limit", 0) end
    local kind = type(value)
    if kind == "number" then
        if value ~= value or math.abs(value) == math.huge then error("passive_nonfinite", 0) end
        return value
    end
    if kind == "string" then
        if #value > 4096 then error("passive_string_limit", 0) end
        return value
    end
    if kind == "boolean" or kind == "nil" then return value end
    if kind ~= "table" or seen[value] then error("passive_nonliteral_or_cycle", 0) end
    seen[value] = true
    local out = {}
    for key, child in pairs(value) do
        if type(key) ~= "string" and type(key) ~= "number" then error("passive_key_type", 0) end
        if type(key) == "string" and #key > 256 then error("passive_key_limit", 0) end
        budget.bytes = budget.bytes + (type(key) == "string" and #key * 4 + 8 or 64)
        out[key] = copy(child, budget, depth + 1, seen)
    end
    seen[value] = nil
    return out
end
local function literal(value)
    local budget = {bytes = 0, nodes = 0}
    local ok, result = pcall(copy, value, budget, 0, {})
    if not ok then return nil, {status = "unsupported_data", code = "nonliteral_or_budget"}, budget end
    return result, nil, budget
end
local function actor()
    local identity = {kind = "unknown", identity_status = "unavailable"}
    if type(UnitGUID) == "function" then
        local ok, guid = pcall(UnitGUID, "player")
        if ok and type(guid) == "string" and #guid > 0 and #guid <= 128 then
            identity = {kind = "logged_in_player_request", player_guid = guid, identity_status = "observed"}
        end
    end
    if C_TradeSkillUI and type(C_TradeSkillUI.IsTradeSkillLinked) == "function" then
        local ok, linked = pcall(C_TradeSkillUI.IsTradeSkillLinked)
        if ok and type(linked) == "boolean" then identity.linked_profession_view = linked end
    end
    -- This identifies the API caller, not proof that an operation can be executed.
    return identity
end
local function query(name, recipeID, ...)
    local identity, at = actor(), GetServerTime()
    local function result(status, data)
        return {status = status, data = data, source_actor = identity, captured_at = at}
    end
    local api = C_TradeSkillUI and C_TradeSkillUI[name]
    if type(api) ~= "function" then return result("api_unavailable") end
    local ok, data = pcall(api, recipeID, ...)
    if not ok then return result("api_not_ready_or_error") end
    if type(data) ~= "table" then return result("data_unavailable") end
    local copied, err = literal(data)
    if err then return result("unsupported_data") end
    return result("observed", copied)
end
local function databases()
    local db = type(CraftSimDB) == "table" and CraftSimDB or {}
    local crafters = db.crafterDB and db.crafterDB.data or {}
    local items = db.itemRecipeDB and db.itemRecipeDB.data or {}
    return type(crafters) == "table" and crafters or {}, type(items) == "table" and items or {}
end
local function discover()
    local ids, seen, sources, overflow, entries, unique = {}, {}, {}, false, 0, 0
    local availability = {craftsim_cache = type(CraftSimDB) == "table" and "observed" or "unavailable",
        game_recipe_index = "api_unavailable"}
    local function add(id, source)
        entries = entries + 1
        if entries % 100 == 0 then coroutine.yield() end
        if not integer(id) then return end
        sources[source] = (sources[source] or 0) + 1
        if seen[id] then return end
        seen[id] = true
        unique = unique + 1
        if #ids >= MAX_IDS then overflow = true; return end
        table.insert(ids, id)
    end
    local crafters, items = databases()
    for _, row in pairs(crafters) do
        if type(row) == "table" then
            for _, section in ipairs({"recipeInfos", "operationInfos", "professionInfos"}) do
                for id in pairs(row[section] or {}) do add(id, "craftsim_" .. section) end
            end
            for _, list in pairs(row.cachedRecipeIDs or {}) do
                for _, id in pairs(list) do add(id, "craftsim_cachedRecipeIDs") end
            end
        end
    end
    for _, row in pairs(items) do if type(row) == "table" then add(row.recipeID, "craftsim_itemRecipeDB") end end
    if C_TradeSkillUI and type(C_TradeSkillUI.GetAllRecipeIDs) == "function" then
        local ok, list = pcall(C_TradeSkillUI.GetAllRecipeIDs)
        availability.game_recipe_index = not ok and "api_not_ready_or_error" or type(list) == "table" and "observed" or "data_unavailable"
        if ok and type(list) == "table" then for _, id in pairs(list) do add(id, "available_game_recipe_index") end end
    end
    table.sort(ids)
    return ids, sources, overflow, entries, unique, availability
end
local function contexts()
    local result = {}
    local crafters = databases()
    for key, row in pairs(crafters) do
        if type(key) == "string" and type(row) == "table" then
            result[key] = {cache_time_status = "unknown"}
            for _, name in ipairs({"professionGear", "specializationData", "concentrationData", "cooldownData"}) do
                result[key][name] = row[name]
            end
        end
    end
    return literal(result)
end
local function cachedRecipe(recipeID)
    local result = {}
    local crafters = databases()
    for key, row in pairs(crafters) do
        if type(key) == "string" and type(row) == "table" then
            local recipe = row.recipeInfos and row.recipeInfos[recipeID]
            local profession = row.professionInfos and row.professionInfos[recipeID]
            local operation = row.operationInfos and row.operationInfos[recipeID]
            if recipe or profession or operation then
                result[key] = {recipe_info = recipe, profession_info = profession, operation_info = operation,
                    cache_time_status = "unknown"}
            end
        end
    end
    return result
end
local function sameQueue(a, b)
    if type(a) ~= "table" or #a ~= #b then return false end
    for i, id in ipairs(b) do if a[i] ~= id then return false end end
    return true
end
local function budgetForExisting(state)
    local bytes, nodes = state.context_bytes or 0, state.context_nodes or 0
    for _, size in pairs(state.record_sizes or {}) do bytes = bytes + size.bytes; nodes = nodes + size.nodes end
    return bytes, nodes
end
local function countRows(rows)
    local count = 0
    for _ in pairs(rows or {}) do count = count + 1 end
    return count
end
function Passive:Tick()
    if not self.running or not validDB() then return end
    local state = WowAuctionBridgeDB.passive
    if type(state) ~= "table" or state.schema_version ~= 1 then self.running = false; return end
    if InCombatLockdown and InCombatLockdown() then
        state.status = "paused_combat"; self.running = false; return
    end
    local began = debugprofilestop and debugprofilestop()
    for _ = 1, PER_TICK do
        local id = state.queue[state.cursor]
        if not id then
            state.status = state.enumeration_limited and "source_limit" or "complete_available_scope"
            state.completed_at = GetServerTime()
            state.coverage_complete = not state.enumeration_limited
            state.record_count = countRows(state.records)
            self.running = false
            return
        end
        local recipe = {recipe_id = id, captured_at = GetServerTime(), generation = state.generation,
            source_kind = "passive_game_and_cache_observation", engine_profile = false,
            recipe_info = query("GetRecipeInfo", id), schematic = query("GetRecipeSchematic", id, false),
            operation_info = query("GetCraftingOperationInfo", id, {}, nil, false),
            cached = cachedRecipe(id), current_character_state = "unverified", joint_outcome_pmf = "unknown"}
        recipe.captured_at = GetServerTime()
        -- A failed later query never overwrites the time or contents of last-good
        -- observations. These remain historical, not silently refreshed values.
        local previous = state.records[id]
        recipe.last_good = {}
        for _, key in ipairs({"recipe_info", "schematic", "operation_info"}) do
            if recipe[key].status == "observed" then
                recipe.last_good[key] = {captured_at = recipe[key].captured_at, build = state.build,
                    source_actor = recipe[key].source_actor, data = recipe[key].data}
            elseif previous and previous.last_good then recipe.last_good[key] = previous.last_good[key] end
        end
        local copied, err, size = literal(recipe)
        if err then
            copied = {recipe_id = id, captured_at = GetServerTime(), generation = state.generation,
                source_kind = "passive_game_and_cache_observation", engine_profile = false,
                status = "unsupported_data", diagnostic = err.code}
            copied, err, size = literal(copied)
        end
        local old = state.record_sizes[id] or {bytes = 0, nodes = 0}
        local nextBytes, nextNodes = state.storage_bytes - old.bytes + size.bytes, state.storage_nodes - old.nodes + size.nodes
        if nextBytes > MAX_BYTES or nextNodes > MAX_NODES then
            state.status = "storage_budget_reached"; state.coverage_complete = false
            state.blocked_recipe_id = id; self.running = false; return
        end
        state.records[id], state.record_sizes[id] = copied, size
        state.storage_bytes, state.storage_nodes = nextBytes, nextNodes
        state.processed = state.processed + 1
        if recipe.schematic.status == "observed" then state.schematics_observed = state.schematics_observed + 1 end
        if recipe.operation_info.status == "observed" then state.operations_observed = state.operations_observed + 1 end
        state.cursor = state.cursor + 1
        state.record_count = countRows(state.records)
        if began and debugprofilestop() - began >= TICK_MS then break end
    end
    C_Timer.After(DELAY, function() Passive:Tick() end)
end
function Passive:DiscoverTick(reason)
    if not validDB() then self.discovering = false; return end
    local ok, ids, sources, limited, entries, unique, availability = coroutine.resume(self.discovery)
    if not ok then
        self.discovering = false
        local previous = WowAuctionBridgeDB.passive
        if type(previous) == "table" and previous.schema_version == 1 then
            previous.last_discovery_error = {at = GetServerTime(), code = "source_shape_or_query_error"}
            previous.latest_attempt_status = "discovery_error"
        else
            WowAuctionBridgeDB.passive = {schema_version = 1, status = "discovery_error", scope = "available_local_recipe_ids",
                observed_at = GetServerTime(), coverage_complete = false, engine_profile = false}
        end
        return
    end
    if coroutine.status(self.discovery) ~= "dead" then
        C_Timer.After(DELAY, function() Passive:DiscoverTick(reason) end)
        return
    end
    self.discovering = false
    self:Begin(reason, ids, sources, limited, entries, unique, availability)
end
function Passive:Start(reason)
    if not validDB() or self.running or self.discovering then return end
    self.discovering = true
    self.discovery = coroutine.create(discover)
    self:DiscoverTick(reason)
end
function Passive:Begin(reason, ids, sources, limited, entries, unique, availability)
    local version, build = GetBuildInfo()
    local previous = WowAuctionBridgeDB.passive
    if type(previous) == "table" and previous.schema_version == 1 and previous.build == version .. "." .. build
        and sameQueue(previous.queue, ids) and (previous.status == "running" or previous.status == "paused_combat" or previous.status == "storage_budget_reached")
        and type(previous.cursor) == "number" and previous.cursor >= 1 and previous.cursor <= #ids + 1 then
        previous.status = "running"; previous.resume_reason = reason; previous.resumed_at = GetServerTime()
        self.running = true; C_Timer.After(DELAY, function() Passive:Tick() end); return
    end
    local context, contextError, contextSize = contexts()
    local state = {schema_version = 1, exporter_version = "0.3.2", scope = "available_local_recipe_ids",
        status = "running", generation = (type(previous) == "table" and tonumber(previous.generation) or 0) + 1,
        started_at = GetServerTime(), reason = reason, build = version .. "." .. build,
        craftsim_version = C_AddOns.GetAddOnMetadata("CraftSim", "Version"), engine_profile = false,
        cache_time_status = "unknown", current_character_state = "unverified", coverage_complete = false,
        queue = ids, queued = #ids, source_entry_count = entries, source_counts = sources, enumeration_limited = limited,
        source_unique_ids = unique, omitted_id_count = unique - #ids,
        source_statuses = availability,
        cursor = 1, processed = 0, record_count = 0, schematics_observed = 0, operations_observed = 0,
        records = type(previous) == "table" and previous.schema_version == 1 and type(previous.records) == "table" and previous.records or {},
        record_sizes = type(previous) == "table" and previous.schema_version == 1 and type(previous.record_sizes) == "table" and previous.record_sizes or {},
        contexts = context or {}, contexts_copied_at = GetServerTime(), context_diagnostic = contextError,
        context_bytes = context and contextSize.bytes or 0, context_nodes = context and contextSize.nodes or 0}
    state.storage_bytes, state.storage_nodes = budgetForExisting(state)
    if (state.storage_bytes > MAX_BYTES or state.storage_nodes > MAX_NODES) and type(previous) == "table" then
        state.contexts, state.context_bytes, state.context_nodes = previous.contexts or {}, previous.context_bytes or 0, previous.context_nodes or 0
        state.contexts_copied_at = previous.contexts_copied_at
        state.context_diagnostic = {status = "retained_previous", code = "replacement_context_over_budget"}
        state.storage_bytes, state.storage_nodes = budgetForExisting(state)
    end
    state.record_count = countRows(state.records)
    WowAuctionBridgeDB.passive = state
    self.running = true
    C_Timer.After(DELAY, function() Passive:Tick() end)
end
function Passive:CRAFTSIM_PROFESSION_INITIALIZED() self:Start("profession_ready") end
function Passive:CRAFTSIM_PROFESSION_OPENED() self:Start("profession_opened_naturally") end
function Passive:Register()
    if not self.registered and CraftSimAPI and CraftSimAPI.RegisterEvents then
        CraftSimAPI:RegisterEvents(self, {"CRAFTSIM_PROFESSION_INITIALIZED", "CRAFTSIM_PROFESSION_OPENED"})
        self.registered = true
    end
end
local frame = CreateFrame("Frame")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_REGEN_ENABLED")
frame:SetScript("OnEvent", function(_, event, name)
    if event == "PLAYER_LOGIN" then
        Passive:Register()
        Passive:Start("login")
        if C_Timer.NewTicker then Passive.ticker = C_Timer.NewTicker(300, function() Passive:Start("periodic") end) end
    elseif event == "ADDON_LOADED" and name == "CraftSim" then Passive:Register()
    elseif event == "PLAYER_REGEN_ENABLED" and validDB() and WowAuctionBridgeDB.passive
        and WowAuctionBridgeDB.passive.status == "paused_combat" then Passive:Start("combat_ended") end
end)
