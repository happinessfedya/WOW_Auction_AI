"""Read the project's bounded CraftSim export, never execute addon Lua.

This adapter preserves a conditional engine observation. It neither asserts that
the crafter can execute a recipe nor converts a mean yield into a loot PMF.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from .lua_data import ParseLimits, parse_saved_variables

SUPPORTED_CRAFTSIM = "27.0.7"
SUPPORTED_BUILD = "12.1.5.70575"
FINGERPRINT_FIELDS = ("game", "craftsim_version", "recipe", "crafter", "stats", "base_stats", "modifiers",
                      "gear", "reagents", "buffs", "specialization", "options", "concentration", "cooldown", "prices",
                      "result", "engine")


class CraftSimError(ValueError):
    """Safe diagnostics, without character names or embedded source text."""


def _json_value(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        raise CraftSimError("bridge_depth_limit")
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if type(key) not in (int, str) or (isinstance(key, str) and len(key) > 4096):
                raise CraftSimError("bridge_invalid_key")
            name = str(key)
            if name in result:
                raise CraftSimError("bridge_ambiguous_key")
            result[name] = _json_value(child, depth + 1)
        return result
    if type(value) in (str, int, float, bool) or value is None:
        if isinstance(value, str) and len(value) > 4096:
            raise CraftSimError("bridge_string_limit")
        return value
    raise CraftSimError("bridge_invalid_value")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _decimal(value: Any, *, nonnegative: bool = False) -> Decimal:
    if type(value) not in (int, float, str) or len(str(value)) > 100:
        raise CraftSimError("missing_or_invalid_number")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise CraftSimError("missing_or_invalid_number") from None
    if not result.is_finite() or (nonnegative and result < 0):
        raise CraftSimError("missing_or_invalid_number")
    return result


def state_fingerprint(raw: dict) -> str:
    return hashlib.sha256(_canonical({key: raw.get(key) for key in FINGERPRINT_FIELDS})).hexdigest()


def _values(value: Any) -> list:
    if not isinstance(value, dict):
        raise CraftSimError("bridge_expected_table")
    return list(value.values())


def _input_price_scope(raw: dict) -> tuple[set[str], list[str]]:
    """Select prices used by the captured allocation, not all candidate reagents.

    CraftSim PriceData.UpdateReagentPriceInfos also caches every optional/finishing
    candidate. For unallocated quality slots PriceData.Update uses the cheapest
    candidate; retain that model input scope but mark the fixed allocation absent.
    """
    needed: set[str] = set()
    issues: list[str] = []
    data = raw.get("reagents", {})
    def add(entry: dict) -> None:
        if entry.get("currencyID"):
            issues.append("currency_input_valuation_unknown")
            return
        item_id = entry.get("item", {}).get("itemID")
        if _positive_int(item_id): needed.add(str(item_id))
        else: issues.append("reagent_identity_missing")
    for slot in _values(data.get("required", {})):
        required = slot.get("requiredQuantity")
        entries = _values(slot.get("items", {}))
        if not _positive_int(required):
            issues.append("required_quantity_unknown")
            continue
        if slot.get("hasQuality") is True:
            quantities = [entry.get("quantity") for entry in entries]
            if any(type(q) is not int or q < 0 for q in quantities):
                issues.append("quality_allocation_invalid")
                for entry in entries: add(entry)
                continue
            total = sum(quantities)
            if total != required:
                issues.append("quality_allocation_incomplete" if total < required else "quality_allocation_exceeds_required")
            # The installed engine's unallocated branch reads all quality prices.
            for entry, quantity in zip(entries, quantities):
                if total < required or quantity > 0: add(entry)
        elif len(entries) == 1:
            add(entries[0])
        else:
            issues.append("required_allocation_ambiguous")
    for section in ("optional", "finishing"):
        for slot in _values(data.get(section, {})):
            if slot.get("activeReagent"): add(slot["activeReagent"])
    selected = data.get("selected", {})
    if selected.get("activeReagent"): add(selected["activeReagent"])
    elif selected: issues.append("selectable_allocation_missing")
    salvage = data.get("salvage", {})
    if salvage.get("item"): add(salvage)
    return needed, issues


def _profile(raw: dict, observed_at: int, *, event: str = "CRAFTSIM_RECIPE_DATA_UPDATED") -> dict:
    if raw.get("schema_version") != 1 or type(raw.get("schema_version")) is not int:
        raise CraftSimError("bridge_profile_schema")
    recipe, game = raw.get("recipe", {}), raw.get("game", {})
    if not _positive_int(raw.get("captured_at")) or not _positive_int(raw.get("sequence")):
        raise CraftSimError("bridge_invalid_observation")
    if type(raw.get("context_epoch")) is not int or raw["context_epoch"] < 0:
        raise CraftSimError("bridge_invalid_context_epoch")
    recipe_id = recipe.get("recipeID")
    missing: list[str] = []
    unsupported: list[str] = []
    # These flags choose different engine/economic branches. Absent or numeric
    # flags must not silently select the old-world/non-order/zero-cost branch.
    for flag in ("supportsCraftingStats", "supportsResourcefulness", "supportsMulticraft",
                 "supportsSpecializations", "isWorkOrder", "isRecraft", "isSalvageRecipe",
                 "subRecipeCostsEnabled"):
        if type(recipe.get(flag)) is not bool:
            missing.append("critical_flag_missing_or_invalid:" + flag)
    if not _positive_int(recipe_id):
        missing.append("recipe_id_missing")
    build = f"{game.get('version', '')}.{game.get('build', '')}"
    if raw.get("craftsim_version") != SUPPORTED_CRAFTSIM:
        unsupported.append("craftsim_version_not_verified")
    if build != SUPPORTED_BUILD:
        unsupported.append("game_build_not_verified")
    if raw.get("event") != event:
        missing.append("ready_event_missing")
    if raw.get("completeness", {}).get("schematic") is not True:
        missing.append("recipe_schematic_missing")
    if raw.get("completeness", {}).get("operation") is not True and recipe.get("supportsCraftingStats"):
        missing.append("operation_info_missing")
    for field in ("reagents", "stats", "gear", "buffs", "specialization", "prices", "result", "options"):
        if not isinstance(raw.get(field), dict):
            missing.append(field + "_missing")
    if recipe.get("supportsSpecializations") and raw.get("specialization", {}).get("implemented") is not True:
        missing.append("specialization_not_implemented")
    result = raw.get("result", {})
    output = result.get("expectedItem", {})
    if not _positive_int(output.get("itemID")) or not isinstance(output.get("itemLink"), str):
        missing.append("output_variant_missing")
    for key in ("minItemAmount", "maxItemAmount"):
        if not _positive_int(recipe.get(key)):
            missing.append(key + "_missing_or_not_integer")
    if recipe.get("minItemAmount", 0) > recipe.get("maxItemAmount", 0):
        missing.append("invalid_yield_bounds")
    engine = raw.get("engine", {})
    try:
        _decimal(engine.get("average_profit"))
        if _decimal(result.get("expectedYieldPerCraft"), nonnegative=True) <= 0:
            missing.append("expected_yield_nonpositive")
    except CraftSimError:
        missing.append("engine_mean_or_yield_missing")
    if engine.get("price_unit") != "copper" or engine.get("profit_unit") != "per_craft":
        missing.append("engine_units_unknown")
    if recipe.get("supportsCraftingStats") and not recipe.get("supportsResourcefulness"):
        unsupported.append("craftsim_empty_probability_fallback_c21")
    if recipe.get("isWorkOrder"):
        unsupported.append("work_order_profile_not_validated")
    if recipe.get("isRecraft") or recipe.get("isSalvageRecipe"):
        unsupported.append("recraft_or_salvage_profile_not_validated")
    # professionGearCached is also set true after a fresh local TopGear bag scan;
    # it is not an age/origin flag for the selected equipped gear snapshot.
    for key in ("specializationDataCached", "operationInfoCached", "recipeInfoCached", "professionInfoCached"):
        if recipe.get(key):
            missing.append("cached_context_age_unknown:" + key)
    price_infos = raw.get("prices", {}).get("reagentPriceInfos", {})
    needed_prices, allocation_issues = _input_price_scope(raw)
    missing.extend(allocation_issues)
    unused_unavailable_prices = []
    for item_id in sorted(needed_prices):
        entry = price_infos.get(item_id)
        if not isinstance(entry, dict):
            missing.append("reagent_price_unavailable")
            continue
        info = entry.get("priceInfo", {})
        if info.get("noPriceSource") or (info.get("noAHPriceFound") and not info.get("isOverride") and not info.get("isExpectedCost")):
            missing.append("reagent_price_unavailable")
        try:
            _decimal(entry.get("itemPrice"), nonnegative=True)
        except CraftSimError:
            missing.append("reagent_price_unavailable")
    for item_id, entry in price_infos.items():
        info = entry.get("priceInfo", {})
        if item_id not in needed_prices and (info.get("noPriceSource") or info.get("noAHPriceFound")):
            unused_unavailable_prices.append(item_id)
    try:
        quality = str(result.get("expectedQuality"))
        output_price = _decimal(raw.get("prices", {}).get("qualityPriceList", {}).get(quality), nonnegative=True)
        if output_price == 0 and raw.get("prices", {}).get("resultOverrides", {}).get(quality, {}).get("price") != 0:
            missing.append("zero_output_quote_ambiguous")
    except CraftSimError:
        missing.append("output_price_missing")
    if raw.get("status") == "missing_data":
        missing.append("export_capture_failed")
    reasons = sorted(set(missing + unsupported))
    status = "unsupported" if unsupported else "missing_data" if missing else "model_estimate"
    if raw.get("status") == "missing_data" and raw.get("error") == "capture_failed":
        status = "missing_data"
        diagnostic = raw.get("diagnostic", {})
        reasons.append("export_capture_failed:" + str(diagnostic.get("code", "legacy_reason_not_recorded")))
    if raw["captured_at"] > observed_at:
        reasons.append("capture_clock_after_receive")
        status = "missing_data"
    return {"id": hashlib.sha256(_canonical(raw)).hexdigest(), "recipe_id": recipe_id,
            "captured_at": raw["captured_at"], "observed_at": observed_at, "sequence": raw["sequence"],
            "context_epoch": raw["context_epoch"], "build": build, "state_fingerprint": state_fingerprint(raw),
            "status": status, "capture_status": status, "capture_reasons": list(reasons), "reasons": reasons, "unknowns": ["joint_item_outcome_pmf", "upstream_price_observed_at",
            "current_character_state_after_export", "server_mechanics_confirmation"],
            "price_scope": {"required_by_captured_model": sorted(needed_prices),
                            "unused_candidates_without_quote": sorted(unused_unavailable_prices)},
            "joint_outcome_pmf": None, "execution_eligible": False, "raw": raw}


def parse_bridge(data: bytes, *, observed_at: int, current_build: str | None = None) -> dict:
    """Return validated observations and per-record rejections. No persistence side effects.

    observed_at is this import's receive time, not a backdated source timestamp.
    The caller should retain the raw file in a private artifact store if permitted.
    """
    if not _positive_int(observed_at):
        raise CraftSimError("bridge_invalid_receive_time")
    parsed = parse_saved_variables(data, ParseLimits(max_bytes=32*1024*1024, max_nodes=1000000, max_depth=40))
    if set(parsed) != {"WowAuctionBridgeDB"}:
        raise CraftSimError("bridge_global_schema")
    db = _json_value(parsed["WowAuctionBridgeDB"])
    if not isinstance(db, dict) or type(db.get("schema_version")) is not int or db["schema_version"] != 1:
        raise CraftSimError("bridge_schema_version")
    records = db.get("records")
    if not isinstance(records, dict) or len(records) > 100:
        raise CraftSimError("bridge_record_limit_or_shape")
    if set(records) != {str(i) for i in range(1, len(records) + 1)}:
        raise CraftSimError("bridge_records_not_dense")
    for key in ("sequence", "context_epoch", "dropped_records"):
        if type(db.get(key)) is not int or db[key] < 0:
            raise CraftSimError("bridge_counter_invalid")
    if db["sequence"] != len(records) + db["dropped_records"]:
        raise CraftSimError("bridge_ring_counters_do_not_reconcile")
    profiles, rejected, seen = [], [], set()
    invalidation = db.get("last_invalidation", {})
    if not isinstance(invalidation, dict):
        raise CraftSimError("bridge_invalidation_invalid")
    if invalidation and (type(invalidation.get("epoch")) is not int or invalidation["epoch"] != db["context_epoch"]):
        raise CraftSimError("bridge_invalidation_epoch_invalid")
    current_invalidation = dict(invalidation, epoch=db["context_epoch"])
    for index in range(1, len(records) + 1):
        try:
            raw = records[str(index)]
            if not isinstance(raw, dict):
                raise CraftSimError("bridge_profile_not_table")
            profile = _profile(raw, observed_at)
            if profile["sequence"] in seen:
                raise CraftSimError("bridge_duplicate_sequence")
            if profile["sequence"] > db["sequence"] or profile["context_epoch"] > db["context_epoch"]:
                raise CraftSimError("bridge_profile_after_container")
            seen.add(profile["sequence"])
            profile["last_invalidation"] = current_invalidation
            assessed = assess_profile(profile, now=observed_at, current_build=current_build)
            profile["status"] = assessed["status"]
            profile["reasons"] = assessed["reasons"]
            profile["context_status"] = assessed["context_status"]
            profiles.append(profile)
        except (CraftSimError, TypeError, AttributeError, ValueError):
            rejected.append({"record_index": index, "reason": "invalid_bridge_record"})
    passive = parse_passive(db.get("passive"), observed_at=observed_at)
    from .knowledge_engine_capture import parse_engine
    engine = parse_engine(db.get("engine"), observed_at=observed_at, current_build=current_build,
                          current_epoch=db["context_epoch"], invalidation=invalidation)
    return {"schema_version": 1, "source_sha256": hashlib.sha256(data).hexdigest(),
            "observed_at": observed_at, "profiles": profiles, "rejected": rejected,
            "dropped_records": db.get("dropped_records"), "current_epoch": db.get("context_epoch"),
            "last_invalidation": invalidation, "flushed_at": db.get("flushed_at"), "passive": passive,
            "engine": engine}


def parse_passive(raw: Any, *, observed_at: int) -> dict | None:
    """Validate autonomous mechanical/cache observations, never engine profiles.

    Completion measures traversal of the available local ID set only. Game API
    data may be absent even on a completed pass; cached data has unknown age.
    """
    if not _positive_int(observed_at): raise CraftSimError("passive_invalid_receive_time")
    if raw is None:
        return None
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise CraftSimError("passive_schema_version")
    if raw.get("scope") != "available_local_recipe_ids" or raw.get("engine_profile") is not False:
        raise CraftSimError("passive_scope_or_profile_invalid")
    # SavedVariables are untrusted literals. Optional tables may be omitted by
    # Lua, but an explicitly supplied scalar must not reach .get()/.values().
    def table(value: Any, code: str, limit: int = 120000) -> dict:
        if not isinstance(value, dict) or len(value) > limit:
            raise CraftSimError(code)
        return value

    def nonnegative(value: Any) -> bool:
        return type(value) is int and value >= 0

    for field in ("source_counts", "source_statuses", "record_sizes", "contexts",
                  "last_discovery_error", "context_diagnostic", "queue", "records"):
        if field in raw:
            table(raw[field], "passive_" + field + "_invalid")
    for value in raw.get("source_counts", {}).values():
        if not nonnegative(value): raise CraftSimError("passive_source_counts_invalid")
    for value in raw.get("source_statuses", {}).values():
        if not isinstance(value, str) or value not in ("observed", "unavailable", "api_unavailable", "api_not_ready_or_error", "data_unavailable"):
            raise CraftSimError("passive_source_statuses_invalid")
    for size in raw.get("record_sizes", {}).values():
        table(size, "passive_record_sizes_invalid")
        if not nonnegative(size.get("bytes")) or not nonnegative(size.get("nodes")):
            raise CraftSimError("passive_record_sizes_invalid")
    contexts = raw.get("contexts", {})
    for context in contexts.values():
        table(context, "passive_context_invalid")
        for field in ("professionGear", "specializationData", "concentrationData", "cooldownData"):
            if field in context: table(context[field], "passive_context_section_invalid")
    if "last_discovery_error" in raw:
        error = raw["last_discovery_error"]
        if not _positive_int(error.get("at")) or not isinstance(error.get("code"), str):
            raise CraftSimError("passive_last_discovery_error_invalid")
    if "context_diagnostic" in raw:
        diagnostic = raw["context_diagnostic"]
        if not isinstance(diagnostic.get("status"), str) or not isinstance(diagnostic.get("code"), str):
            raise CraftSimError("passive_context_diagnostic_invalid")
    if "latest_attempt_status" in raw and not isinstance(raw["latest_attempt_status"], str):
        raise CraftSimError("passive_latest_attempt_status_invalid")
    if not isinstance(raw.get("status"), str):
        raise CraftSimError("passive_status_invalid")
    if raw.get("status") == "discovery_error":
        return {"schema_version":1,"status":"discovery_error","scope":raw["scope"],
                "engine_profile":False,"coverage_complete":False,"record_count":0,"records":{},"raw":raw}
    statuses = {"running", "paused_combat", "storage_budget_reached", "complete_available_scope", "source_limit"}
    if raw.get("status") not in statuses:
        raise CraftSimError("passive_status_invalid")
    queue = raw.get("queue")
    if not isinstance(queue, dict) or len(queue) > 20000 or set(queue) != {str(i) for i in range(1,len(queue)+1)}:
        raise CraftSimError("passive_queue_invalid")
    ids = [queue[str(i)] for i in range(1,len(queue)+1)]
    if any(not _positive_int(i) for i in ids) or len(set(ids)) != len(ids):
        raise CraftSimError("passive_recipe_ids_invalid")
    for key in ("generation", "started_at", "cursor"):
        if not _positive_int(raw.get(key)): raise CraftSimError("passive_positive_counter_invalid")
    for key in ("queued", "processed", "record_count", "storage_bytes", "storage_nodes", "schematics_observed", "operations_observed"):
        if type(raw.get(key)) is not int or raw[key] < 0: raise CraftSimError("passive_counter_invalid")
    if raw["queued"] != len(ids) or raw["cursor"] != raw["processed"] + 1 or raw["processed"] > len(ids):
        raise CraftSimError("passive_checkpoint_invalid")
    if raw["storage_bytes"] > 8*1024*1024 or raw["storage_nodes"] > 120000:
        raise CraftSimError("passive_storage_limit")
    if type(raw.get("enumeration_limited")) is not bool:
        raise CraftSimError("passive_enumeration_limited_invalid")
    for field in ("source_entry_count", "source_unique_ids", "omitted_id_count", "context_bytes", "context_nodes"):
        if field in raw and not nonnegative(raw[field]):
            raise CraftSimError("passive_counter_invalid")
    records = raw.get("records")
    if not isinstance(records, dict) or len(records) > 20000 or raw["record_count"] != len(records):
        raise CraftSimError("passive_records_invalid")
    queried, unavailable = 0, 0
    for key, row in records.items():
        if not isinstance(row, dict) or not _positive_int(row.get("recipe_id")) or str(row["recipe_id"]) != key:
            raise CraftSimError("passive_record_identity")
        if row.get("engine_profile") is not False or not _positive_int(row.get("captured_at")):
            raise CraftSimError("passive_record_schema")
        if row.get("source_kind") != "passive_game_and_cache_observation" or not _positive_int(row.get("generation")) or row["generation"] > raw["generation"]:
            raise CraftSimError("passive_record_generation_or_kind")
        if row["captured_at"] > observed_at: raise CraftSimError("passive_future_capture")
        cached = table(row.get("cached", {}), "passive_cached_invalid")
        for cache in cached.values():
            table(cache, "passive_cached_context_invalid")
            for field in ("recipe_info", "profession_info", "operation_info"):
                if field in cache: table(cache[field], "passive_cached_section_invalid")
        for field in ("recipe_info", "schematic", "operation_info"):
            value = table(row.get(field, {}), "passive_query_invalid")
            if value:
                if value.get("status") not in ("observed", "api_unavailable", "api_not_ready_or_error", "data_unavailable", "unsupported_data"):
                    raise CraftSimError("passive_query_status_invalid")
                if "data" in value: table(value["data"], "passive_query_data_invalid")
                actor = value.get("source_actor", {})
                if not isinstance(actor,dict) or actor.get("kind") not in ("unknown","logged_in_player_request"):
                    raise CraftSimError("passive_query_actor_missing")
                if actor["kind"] == "logged_in_player_request" and (not isinstance(actor.get("player_guid"),str) or not 1 <= len(actor["player_guid"]) <= 128):
                    raise CraftSimError("passive_query_actor_invalid")
                if not _positive_int(value.get("captured_at")) or value["captured_at"] > row["captured_at"]:
                    raise CraftSimError("passive_query_time_invalid")
            if value.get("status") == "observed":
                if not isinstance(value.get("data"), dict): raise CraftSimError("passive_observed_data_missing")
                queried += 1
            else: unavailable += 1
        last_good = table(row.get("last_good", {}), "passive_last_good_invalid", 3)
        for field, last in last_good.items():
            if field not in ("recipe_info", "schematic", "operation_info"):
                raise CraftSimError("passive_last_good_field_invalid")
            if not isinstance(last, dict) or not _positive_int(last.get("captured_at")) or last["captured_at"] > row["captured_at"]:
                raise CraftSimError("passive_last_good_time_invalid")
            if not isinstance(last.get("source_actor"),dict) or last["source_actor"].get("kind") not in ("unknown","logged_in_player_request"):
                raise CraftSimError("passive_last_good_actor_invalid")
            if last["source_actor"]["kind"] == "logged_in_player_request" and (not isinstance(last["source_actor"].get("player_guid"),str) or not 1 <= len(last["source_actor"]["player_guid"]) <= 128):
                raise CraftSimError("passive_last_good_actor_invalid")
            if not isinstance(last.get("build"),str) or not isinstance(last.get("data"),dict):
                raise CraftSimError("passive_last_good_payload_invalid")
    for recipe_id in ids[:raw["processed"]]:
        if str(recipe_id) not in records or records[str(recipe_id)]["generation"] != raw["generation"]:
            raise CraftSimError("passive_checkpoint_lost_record")
    complete = raw.get("status") == "complete_available_scope" and raw["processed"] == len(ids) and raw.get("enumeration_limited") is False
    if raw.get("coverage_complete") is not complete:
        raise CraftSimError("passive_completion_invalid")
    return {"schema_version":1,"status":raw["status"],"scope":raw["scope"],"engine_profile":False,
            "coverage_complete":complete,"world_coverage":None,"cache_time_status":"unknown",
            "current_character_state":"unverified","record_count":len(records),"queued":len(ids),
            "processed":raw["processed"],"observed_api_tables":queried,"unavailable_api_tables":unavailable,
            "latest_attempt_status":raw.get("latest_attempt_status",raw["status"]),
            "records":records,"contexts":contexts,"raw":raw}


def assess_profile(profile: dict, *, now: int, current_build: str | None = None,
                   expected_fingerprint: str | None = None, max_age_seconds: int = 86400) -> dict:
    if not _positive_int(now) or not _positive_int(max_age_seconds):
        raise CraftSimError("invalid_freshness_argument")
    reasons = list(profile["reasons"])
    stale = []
    context_status = "current_state_after_export_unknown"
    if now < profile["captured_at"]:
        stale.append("capture_clock_after_now")
    elif now - profile["captured_at"] > max_age_seconds:
        stale.append("profile_expired")
    if current_build is not None and current_build != profile["build"]:
        stale.append("build_changed")
        context_status = "build_changed"
    if expected_fingerprint is not None and expected_fingerprint != profile["state_fingerprint"]:
        stale.append("context_fingerprint_changed")
        context_status = "different_from_supplied_context"
    invalidation = profile.get("last_invalidation", {})
    if type(invalidation.get("epoch")) is int and invalidation["epoch"] > profile["context_epoch"]:
        stale.append("context_invalidated_after_capture")
        context_status = "unknown_after_session_start" if invalidation.get("reason") == "session_start" else "changed_after_capture"
    return {"status": "stale" if stale else profile["status"], "reasons": sorted(set(reasons + stale)),
            "capture_status": profile.get("capture_status", profile["status"]), "context_status": context_status,
            "execution_eligible": False, "max_age_seconds": max_age_seconds}


def fixed_variant_reprice(profile: dict, prices: dict[str, Any], *, now: int,
                          current_build: str, expected_fingerprint: str) -> dict:
    """Linear change to an exported engine mean, never a new CraftSim optimization.

    Quotes use exact exported item links as keys (no merging different variants).
    Keep the captured deposit and engine AH multiplier. This narrow adapter
    supports ordinary fixed item allocations, not orders, salvage, currencies,
    subrecipes or inventory risk simulation. Fresh market input approval belongs
    to the market adapter/caller; an arbitrary quote here is a scenario only.
    """
    state = assess_profile(profile, now=now, current_build=current_build, expected_fingerprint=expected_fingerprint)
    if state["status"] != "model_estimate":
        raise CraftSimError("profile_not_repriceable:" + state["status"])
    raw, recipe = profile["raw"], profile["raw"]["recipe"]
    if any(recipe.get(key) for key in ("isWorkOrder", "isRecraft", "isSalvageRecipe", "subRecipeCostsEnabled")):
        raise CraftSimError("unsupported_fixed_variant")
    result, old_prices, engine = raw["result"], raw["prices"], raw["engine"]
    if engine.get("deposit_status") not in ("disabled_or_order", "engine_estimate"):
        raise CraftSimError("deposit_unknown")
    if result.get("itemRarity") is None or type(result["itemRarity"]) is not int:
        raise CraftSimError("output_sale_channel_unknown")
    if result["itemRarity"] == 0:
        raise CraftSimError("vendor_output_repricing_unsupported")
    reagent_data = raw["reagents"]
    if reagent_data.get("selected", {}).get("activeReagent") or reagent_data.get("salvage", {}).get("item"):
        raise CraftSimError("selected_or_salvage_reagent_unsupported")
    allocations: list[tuple[dict, int, bool]] = []
    for slot in _values(reagent_data["required"]):
        count = slot.get("requiredQuantity")
        if not _positive_int(count):
            raise CraftSimError("required_quantity_unknown")
        items = _values(slot.get("items"))
        if slot.get("hasQuality"):
            total = 0
            for entry in items:
                qty = entry.get("quantity")
                if type(qty) is not int or qty < 0:
                    raise CraftSimError("allocation_not_integer")
                total += qty
                if qty: allocations.append((entry, qty, True))
            if total != count:
                raise CraftSimError("allocation_incomplete")
        elif len(items) == 1:
            allocations.append((items[0], count, True))
        else:
            raise CraftSimError("allocation_ambiguous")
    for kind in ("optional", "finishing"):
        for slot in _values(reagent_data[kind]):
            entry = slot.get("activeReagent")
            if entry:
                # Installed PriceData.Update counts optional/finishing once;
                # only requiredSelectableReagentSlot uses maxQuantity. Do not
                # multiply a source mean's price derivative by a slot capacity.
                maximum = slot.get("maxQuantity")
                if maximum is not None and (type(maximum) is not int or maximum != 1):
                    raise CraftSimError("optional_multi_quantity_unsupported")
                allocations.append((entry, 1, False))
    old_total = new_total = old_required = new_required = Decimal(0)
    for entry, count, required in allocations:
        if entry.get("currencyID"):
            raise CraftSimError("currency_conversion_not_supported")
        obj = entry.get("item", {})
        link, item_id = obj.get("itemLink"), obj.get("itemID")
        if not isinstance(link, str) or not _positive_int(item_id):
            raise CraftSimError("input_variant_missing")
        old = _decimal(old_prices["reagentPriceInfos"].get(str(item_id), {}).get("itemPrice"), nonnegative=True) * count
        new = _decimal(prices.get(link), nonnegative=True) * count
        old_total += old; new_total += new
        if required: old_required += old; new_required += new
    if abs(old_total - _decimal(old_prices.get("craftingCosts"), nonnegative=True)) > Decimal("0.0001"):
        raise CraftSimError("captured_cost_does_not_reconcile")
    if abs(old_required - _decimal(old_prices.get("craftingCostsRequired"), nonnegative=True)) > Decimal("0.0001"):
        raise CraftSimError("captured_required_cost_does_not_reconcile")
    res_delta = Decimal(0)
    if recipe.get("supportsCraftingStats") and recipe.get("supportsResourcefulness"):
        stat = raw["stats"]["resourcefulness"]
        divisor = _decimal(stat.get("percentDivisionFactor"), nonnegative=True)
        if divisor <= 0: raise CraftSimError("resourcefulness_divisor_invalid")
        chance = _decimal(stat.get("value"), nonnegative=True) / divisor
        extra = _decimal(stat.get("extraValues", {}).get("1", 0), nonnegative=True)
        constant = _decimal(raw["options"]["PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT"].get("value"), nonnegative=True)
        coefficient = chance * (1 + extra) * constant
        if not 0 <= chance <= 1 or not 0 <= coefficient <= 1:
            raise CraftSimError("resourcefulness_coefficient_invalid")
        if abs(old_required * coefficient - _decimal(old_prices.get("resourcefulnessSavedCostsAverage"), nonnegative=True)) > Decimal("0.0001"):
            raise CraftSimError("captured_resourcefulness_does_not_reconcile")
        res_delta = (new_required - old_required) * coefficient
    output_link = result["expectedItem"]["itemLink"]
    new_output = _decimal(prices.get(output_link), nonnegative=True)
    old_output = _decimal(old_prices["qualityPriceList"].get(str(result["expectedQuality"])), nonnegative=True)
    multiplier = _decimal(engine.get("ah_multiplier"), nonnegative=True)
    if not 0 < multiplier <= 1: raise CraftSimError("invalid_ah_multiplier")
    expected_yield = _decimal(result["expectedYieldPerCraft"], nonnegative=True)
    mean = _decimal(engine["average_profit"]) + (new_output-old_output)*expected_yield*multiplier - (new_total-old_total) + res_delta
    return {"status": "conditional_model_estimate", "expected_profit_copper_per_craft": str(mean),
            "original_engine_mean_copper_per_craft": str(engine["average_profit"]), "profile_id": profile["id"],
            "state_fingerprint": profile["state_fingerprint"], "deposit_treatment": "unchanged_engine_baseline",
            "joint_outcome_pmf": None, "execution_eligible": False, "global_optimum_claimed": False,
            "market_input_validation": "caller_required", "quantity_semantics": "expectation_not_physical_inventory"}
