-- Own SavedVariables only. No crafting, inventory moves, price changes or reload.
-- The Python importer computes cryptographic fingerprints; Lua exports literal DTOs.
local ADDON = ...
local Bridge = { registered = false }
local MAX_RECORDS, MAX_NODES, MAX_DEPTH, MAX_BYTES = 100, 4000, 16, 128000
local function validDatabase()
    local db = WowAuctionBridgeDB
    if type(db) ~= "table" or db.schema_version ~= 1 or type(db.records) ~= "table" or #db.records > MAX_RECORDS then return false end
    for _, key in ipairs({"sequence", "context_epoch", "dropped_records"}) do
        local v = db[key]
        if type(v) ~= "number" or v < 0 or v >= 9007199254740991 or v ~= math.floor(v) then return false end
    end
    return true
end
local function scalar(v)
    local t = type(v)
    if t == "number" then return v == v and math.abs(v) < math.huge end
    return t == "boolean" or (t == "string" and #v <= 4096)
end
local function copy(v, state, depth)
    state.nodes = state.nodes + 1
    -- Conservative upper bound for SavedVariables quoting and table overhead.
    state.bytes = state.bytes + (type(v) == "string" and #v * 4 + 8 or 64)
    local function fail(code) error({code = code, section = state.section or "root", nodes = state.nodes, estimated_bytes = state.bytes}) end
    if state.nodes > MAX_NODES then fail("node_limit") end
    if depth > MAX_DEPTH then fail("depth_limit") end
    if state.bytes > MAX_BYTES then fail("byte_limit") end
    if type(v) == "string" and #v > 4096 then fail("string_limit") end
    if type(v) == "number" and not scalar(v) then fail("nonfinite_number") end
    if scalar(v) then return v end
    if v == nil then return nil end
    if type(v) ~= "table" then fail("unsupported_value_type") end
    if state.seen[v] then fail("cyclic_data") end
    state.seen[v] = true
    local out = {}
    for k, value in pairs(v) do
        if depth == 0 then state.section = type(k) == "string" and k or "root" end
        if type(k) ~= "number" and type(k) ~= "string" then fail("unsupported_key_type") end
        if not scalar(k) then fail("invalid_key") end
        state.bytes = state.bytes + (type(k) == "string" and #k * 4 + 8 or 64)
        out[k] = copy(value, state, depth + 1)
    end
    state.seen[v] = nil
    return out
end
local function selectFields(obj, names)
    if type(obj) ~= "table" then return nil end
    local out = {}
    for _, key in ipairs(names) do out[key] = obj[key] end
    return out
end
local function scalarFields(obj, names)
    if type(obj) ~= "table" then return nil end
    local out = {}
    for _, key in ipairs(names) do
        local value = obj[key]
        if value ~= nil then
            if not scalar(value) then error({code = "invalid_scalar_field", section = key}) end
            out[key] = value
        end
    end
    return out
end
local function numericMap(obj, section)
    if obj == nil then return nil end
    if type(obj) ~= "table" then error({code = "invalid_numeric_map", section = section}) end
    local out = {}
    for key, value in pairs(obj) do
        if (type(key) ~= "number" and key ~= "DEFAULT") or type(value) ~= "number" or not scalar(value) then
            error({code = "invalid_numeric_entry", section = section})
        end
        out[key] = value
    end
    return out
end
local function item(obj)
    if not obj then return nil end
    if type(obj.GetItemID) ~= "function" or type(obj.GetItemLink) ~= "function" then
        error({code = "missing_item_accessor", section = "item"})
    end
    return { itemID = obj:GetItemID(), itemLink = obj:GetItemLink() }
end
local function stats(obj)
    if not obj then return nil end
    local out = {}
    for _, key in ipairs({"recipeDifficulty", "skill", "multicraft", "resourcefulness", "ingenuity", "craftingspeed"}) do
        out[key] = scalarFields(obj[key], {"value", "percentDivisionFactor"})
        if out[key] then out[key].extraValues = numericMap(obj[key].extraValues, "stats.extraValues") end
    end
    return out
end
local function gear(obj)
    if not obj then return nil end
    local out = {profession = obj.professionID, isCooking = obj.isCooking}
    for _, key in ipairs({"gear1", "gear2", "tool"}) do
        if obj[key] then out[key] = {item = item(obj[key].item), stats = stats(obj[key].professionStats)} end
    end
    return out
end
local function reagent(obj)
    if not obj then return nil end
    return {item = item(obj.item), originalItem = item(obj.originalItem), currencyID = obj.currencyID,
        qualityID = obj.qualityID, quantity = obj.quantity, stats = stats(obj.professionStats)}
end
local function optional(slot)
    if not slot then return nil end
    return {dataSlotIndex = slot.dataSlotIndex, maxQuantity = slot.maxQuantity,
        quantity = slot.quantity, locked = slot.locked, activeReagent = reagent(slot.activeReagent)}
end
local function reagents(obj)
    if not obj then return nil end
    local out = {required = {}, optional = {}, finishing = {}, selected = optional(obj.requiredSelectableReagentSlot)}
    for k, r in pairs(obj.requiredReagents or {}) do
        -- Native Reagent:new leaves this nil for one-item slots; its documented
        -- construction rule is more than one schematic reagent => quality.
        local row = {requiredQuantity = r.requiredQuantity, dataSlotIndex = r.dataSlotIndex, hasQuality = r.hasQuality == true, items = {}}
        for i, value in pairs(r.items or {}) do row.items[i] = reagent(value) end
        out.required[k] = row
    end
    for k, slot in pairs(obj.optionalReagentSlots or {}) do out.optional[k] = optional(slot) end
    for k, slot in pairs(obj.finishingReagentSlots or {}) do out.finishing[k] = optional(slot) end
    local slot = obj.salvageReagentSlot
    if slot then out.salvage = {item = item(slot.activeItem), requiredQuantity = slot.requiredQuantity} end
    return out
end
local OPTION_KEYS = {"PRICE_SOURCE", "TSM_PRICE_KEY_REAGENTS", "TSM_PRICE_KEY_ITEMS", "TSM_DEPOSIT_ENABLED", "QUALITY_BREAKPOINT_OFFSET",
    "TSM_DEPOSIT_EXPRESSION", "PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT", "PROFIT_CALCULATION_MULTICRAFT_CONSTANTS"}
local function settings(cs)
    -- OPTIONS:Get initializes CraftSimDB, so read existing values/defaults directly.
    local out = {}
    local saved = CraftSimDB and CraftSimDB.optionsDB and CraftSimDB.optionsDB.data or {}
    local defaults = cs.CONST and cs.CONST.GENERAL_OPTIONS_DEFAULTS or {}
    for _, key in ipairs(OPTION_KEYS) do
        local value = saved[key]
        local origin = "saved"
        if value == nil then value, origin = defaults[key], "default" end
        if key == "PROFIT_CALCULATION_MULTICRAFT_CONSTANTS" then value = numericMap(value, "options.multicraft")
        elseif value ~= nil and not scalar(value) then error({code = "invalid_setting", section = key}) end
        out[key] = {value = value, origin = origin}
    end
    return out
end
local function payload(rd, cs)
    Bridge.captureStage = "payload_fields"
    local version, build, buildDate, interface = GetBuildInfo()
    local out = {schema_version = 1, exporter_version = "0.3.3", captured_at = GetServerTime(),
        context_epoch = WowAuctionBridgeDB.context_epoch, event = "CRAFTSIM_RECIPE_DATA_UPDATED",
        craftsim_version = C_AddOns.GetAddOnMetadata("CraftSim", "Version"),
        game = {version = version, build = build, buildDate = buildDate, interface = interface, locale = GetLocale()},
        recipe = selectFields(rd, {"recipeID", "categoryID", "expansionID", "concentrating", "concentrationCost",
            "learned", "isSimulationModeData", "supportsQualities", "supportsCraftingStats", "supportsMulticraft",
            "supportsResourcefulness", "supportsSpecializations", "isGear", "isSoulbound", "isRecraft", "isSalvageRecipe",
            "isOldWorldRecipe", "baseItemAmount", "minItemAmount", "maxItemAmount", "maxQuality", "allocationItemGUID",
            "subRecipeCostsEnabled", "specializationDataCached", "operationInfoCached", "professionGearCached",
            "recipeInfoCached", "professionInfoCached"}),
        crafter = selectFields(rd.crafterData, {"name", "realm", "class"}),
        stats = stats(rd.professionStats), base_stats = stats(rd.baseProfessionStats), modifiers = stats(rd.professionStatModifiers),
        gear = gear(rd.professionGearSet), reagents = reagents(rd.reagentData), buffs = {}, specialization = {},
        options = settings(cs),
        concentration = selectFields(rd.concentrationData, {"currencyID", "lastUpdated", "amount", "maxQuantity", "rechargingDurationMS"}),
        cooldown = selectFields(rd.cooldownData, {"recipeID", "isCooldownRecipe", "currentCharges", "maxCharges", "startTime", "cooldownPerCharge", "sharedCD"}),
        engine = {average_profit = rd.averageProfitCached, relative_profit = rd.relativeProfitCached, price_unit = "copper",
            profit_unit = "per_craft", probability_kind = "unknown_joint_item_outcomes", mean_origin = "engine_cached_result",
            ah_multiplier = cs.CONST and cs.CONST.AUCTION_HOUSE_CUT},
        completeness = {schematic = type(rd.recipeSchematic) == "table", operation = type(rd.baseOperationInfo) == "table"}}
    out.recipe.isWorkOrder = rd.orderData ~= nil
    Bridge.captureStage = "order"
    if rd.orderData then
        out.order = selectFields(rd.orderData, {"orderID", "orderType", "tipAmount", "consortiumCut", "reagents", "npcOrderRewards"})
    end
    Bridge.captureStage = "buffs"
    if rd.buffData then
        for k, buff in pairs(rd.buffData.buffs or {}) do
            out.buffs[k] = scalarFields(buff, {"buffID", "qualityID", "active", "stacks"})
            out.buffs[k].valuePointData = scalarFields(buff.valuePointData, {"index", "value"})
            out.buffs[k].stats = stats(buff.professionStats)
        end
    else out.buffs = nil end
    Bridge.captureStage = "specialization"
    if rd.specializationData then
        out.specialization.implemented = rd.specializationData.isImplemented
        out.specialization.nodes = {}
        for _, node in pairs(rd.specializationData.nodeData or {}) do out.specialization.nodes[node.nodeID] = node.rank end
    elseif rd.supportsSpecializations then out.specialization = nil end
    Bridge.captureStage = "result"
    if rd.resultData then
        out.result = selectFields(rd.resultData, {"expectedQuality", "expectedQualityConcentration", "expectedYieldPerCraft"})
        out.result.items = {}
        for quality, obj in pairs(rd.resultData.itemsByQuality or {}) do out.result.items[quality] = item(obj) end
        out.result.expectedItem = item(rd.resultData.expectedItem)
        if out.result.expectedItem and C_Item and C_Item.GetItemInfo then
            out.result.itemRarity = select(3, C_Item.GetItemInfo(out.result.expectedItem.itemLink or out.result.expectedItem.itemID))
        end
    end
    Bridge.captureStage = "prices"
    if rd.priceData then
        out.prices = scalarFields(rd.priceData, {"craftingCosts", "craftingCostsRequired", "craftingCostsFixed",
            "craftingCostsNoOrderReagents", "expectedCostsPerItem", "resourcefulnessSavedCosts", "resourcefulnessSavedCostsAverage",
            "averageCraftingCosts", "averageCraftingCostsNoOrderReagents"})
        out.prices.qualityPriceList = numericMap(rd.priceData.qualityPriceList, "prices.qualityPriceList")
        out.prices.selfCraftedReagents = numericMap(rd.priceData.selfCraftedReagents, "prices.selfCraftedReagents")
        out.prices.reagentPriceInfos = {}
        for id, entry in pairs(rd.priceData.reagentPriceInfos or {}) do
            local info = entry.priceInfo
            local row = scalarFields(entry, {"itemPrice"})
            row.priceInfo = scalarFields(info, {"ahPrice", "priceOverride", "isAHPrice", "noAHPriceFound", "isOverride", "noPriceSource", "isExpectedCost"})
            if info and info.expectedCostsData then
                row.priceInfo.expectedCostsData = scalarFields(info.expectedCostsData,
                    {"qualityID", "crafter", "expectedCostsPerItem", "expectedYieldPerCraft", "profession", "concentration", "concentrationCost"})
            end
            out.prices.reagentPriceInfos[id] = row
        end
        out.prices.timestamp_status = "upstream_quote_time_not_exposed"
        local overrides = CraftSimDB and CraftSimDB.priceOverrideDB and CraftSimDB.priceOverrideDB.data
        out.prices.resultOverrides = {}
        local existing = overrides and overrides.recipeResultOverrides and overrides.recipeResultOverrides[rd.recipeID] or {}
        for quality, value in pairs(existing) do
            out.prices.resultOverrides[quality] = scalarFields(value, {"recipeID", "itemID", "price", "qualityID"})
        end
    end
    -- GetExpectedDeposit may initialize OPTIONS. Only invoke after all keys it reads already exist.
    Bridge.captureStage = "deposit"
    local saved = CraftSimDB and CraftSimDB.optionsDB and CraftSimDB.optionsDB.data or {}
    out.engine.deposit_status = "unavailable_or_zero_ambiguous"
    if saved.TSM_DEPOSIT_ENABLED == false or rd.orderData then
        out.engine.expected_deposit, out.engine.deposit_status = 0, "disabled_or_order"
    elseif out.craftsim_version == "27.0.7" and saved.TSM_DEPOSIT_ENABLED == true and saved.TSM_DEPOSIT_EXPRESSION ~= nil and CraftSimAPI.GetExpectedDeposit then
        local ok, value = pcall(CraftSimAPI.GetExpectedDeposit, CraftSimAPI, rd)
        if ok then
            out.engine.expected_deposit = value
            if type(value) == "number" and value > 0 then out.engine.deposit_status = "engine_estimate" end
        end
    end
    Bridge.captureStage = "bounded_copy"
    return copy(out, {nodes = 0, bytes = 0, seen = {}}, 0)
end
local function append(row)
    local db = WowAuctionBridgeDB
    db.sequence = db.sequence + 1
    row.sequence = db.sequence
    table.insert(db.records, row)
    while #db.records > MAX_RECORDS do table.remove(db.records, 1); db.dropped_records = db.dropped_records + 1 end
end
function Bridge:Invalidate(reason)
    if not validDatabase() then return end
    local db = WowAuctionBridgeDB
    db.context_epoch = db.context_epoch + 1
    db.last_invalidation = {epoch = db.context_epoch, at = GetServerTime(), reason = reason}
end
function Bridge:CRAFTSIM_RECIPE_DATA_MODIFIED() self:Invalidate("recipe_modified") end
function Bridge:CRAFTSIM_SETTINGS_UPDATED() self:Invalidate("settings_changed") end
function Bridge:CRAFTSIM_CRAFT_BUFFS_UPDATED() self:Invalidate("buffs_changed") end
function Bridge:CRAFTSIM_RECIPE_DATA_UPDATED(rd)
    if not rd or not validDatabase() then return end
    local epoch = WowAuctionBridgeDB.context_epoch
    C_Timer.After(0, function()
        if epoch ~= WowAuctionBridgeDB.context_epoch or CraftSimAPI:GetOpenRecipeData() ~= rd then return end
        local ok, data = pcall(payload, rd, CraftSimAPI:GetCraftSim())
        if ok then append(data) else
            local diagnostic = {code = "unexpected_lua_error", section = Bridge.captureStage or "payload_build"}
            if type(data) == "table" and type(data.code) == "string" then
                diagnostic = scalarFields(data, {"code", "section", "nodes", "estimated_bytes"})
            end
            local version, build = GetBuildInfo()
            append({schema_version = 1, exporter_version = "0.3.3", captured_at = GetServerTime(), context_epoch = epoch,
                status = "missing_data", error = "capture_failed", diagnostic = diagnostic,
                recipe = {recipeID = rd.recipeID}, craftsim_version = C_AddOns.GetAddOnMetadata("CraftSim", "Version"),
                game = {version = version, build = build}})
        end
    end)
end
function Bridge:Register()
    if self.registered or not validDatabase() or not CraftSimAPI or not CraftSimAPI.RegisterEvents then return end
    CraftSimAPI:RegisterEvents(self, {"CRAFTSIM_RECIPE_DATA_MODIFIED", "CRAFTSIM_RECIPE_DATA_UPDATED",
        "CRAFTSIM_SETTINGS_UPDATED", "CRAFTSIM_CRAFT_BUFFS_UPDATED"})
    self.registered = true
end
local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_EQUIPMENT_CHANGED")
frame:RegisterEvent("TRAIT_CONFIG_UPDATED")
frame:RegisterEvent("PLAYER_LOGOUT")
frame:SetScript("OnEvent", function(_, event, name)
    if event == "ADDON_LOADED" and name == ADDON then
        if WowAuctionBridgeDB == nil then
            WowAuctionBridgeDB = {schema_version = 1, records = {}, sequence = 0, context_epoch = 0, dropped_records = 0}
        end
        if not validDatabase() then return end
        Bridge:Invalidate("session_start")
        Bridge:Register()
    elseif event == "ADDON_LOADED" and name == "CraftSim" and WowAuctionBridgeDB then Bridge:Register()
    elseif event == "PLAYER_LOGIN" then Bridge:Register()
    elseif event == "PLAYER_EQUIPMENT_CHANGED" or event == "TRAIT_CONFIG_UPDATED" then Bridge:Invalidate(event)
    elseif event == "PLAYER_LOGOUT" and validDatabase() then WowAuctionBridgeDB.flushed_at = GetServerTime() end
end)

-- Shared only with our own autonomous calculator. No references to the mutable
-- recipe object escape BuildPayload; the result is a bounded literal copy.
WowAuctionBridgeCapture = {
    BuildPayload = payload,
    CopyLiteral = function(value)
        local state = {nodes = 0, bytes = 0, seen = {}}
        return copy(value, state, 0), state.bytes, state.nodes
    end,
    ValidDatabase = validDatabase,
}
