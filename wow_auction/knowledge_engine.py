"""Offline CraftSim 27.0.7 model for automatically observed allocations.

This is a port of the installed CraftSim arithmetic, not an execution-price or
loot-distribution model. The game resolves an allocation's skill/concentration;
CraftSim resolves its character/gear/spec/buff totals. This module recalculates
quality, mean output, resource savings and profit for new prices without WoW.
It compares a finite set of observed allocations; it does not claim a global
optimum or extrapolate stats to an unobserved allocation.

Source: CraftSim Classes/ResultData.lua, Classes/PriceData.lua,
Classes/RecipeData.lua, Modules/RecipeInfo/RecipeInfo.lua and
Pricing/ProfitCalculation.lua. See THIRD_PARTY_LICENSES/CraftSim-MIT.txt.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from typing import Any

ENGINE_REVISION = 1
SUPPORTED_CRAFTSIM = "27.0.7"
SUPPORTED_BUILD = "12.1.5.70575"


class EngineError(ValueError):
    """A bounded diagnostic, never an embedded source value."""


def _number(value: Any, *, nonnegative: bool = False) -> Decimal:
    if type(value) not in (str, int, float) or len(str(value)) > 100:
        raise EngineError("missing_or_invalid_number")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise EngineError("missing_or_invalid_number") from None
    if (not result.is_finite() or abs(result) > Decimal("1e30")
            or abs(result.as_tuple().exponent) > 100 or (nonnegative and result < 0)):
        raise EngineError("missing_or_invalid_number")
    return result


def _table(value: Any) -> dict:
    if not isinstance(value, dict) or len(value) > 2000:
        raise EngineError("invalid_model_table")
    return value


def _flag(table: dict, key: str) -> bool:
    value = table.get(key)
    if type(value) is not bool:
        raise EngineError("missing_or_invalid_flag:" + key)
    return value


def _integer(value: Any, *, positive: bool = False) -> int:
    if type(value) is not int or value < int(positive) or value > 1000000:
        raise EngineError("invalid_quantity_or_identifier")
    return value


def _text(number: Decimal) -> str:
    return format(number, "f")


def _item(entry: dict) -> tuple[int, str]:
    obj = _table(entry.get("item"))
    item_id = obj.get("itemID")
    if type(item_id) is not int or item_id <= 0:
        raise EngineError("item_identity_missing")
    link = obj.get("itemLink")
    if not isinstance(link, str) or not 1 <= len(link) <= 4096:
        raise EngineError("exact_item_link_missing")
    return item_id, link


def _stat(raw: dict, name: str) -> tuple[Decimal, Decimal]:
    row = _table(_table(raw.get("stats")).get(name))
    factor = _number(row.get("percentDivisionFactor"), nonnegative=True)
    if factor <= 0:
        raise EngineError("invalid_stat_divisor")
    probability = _number(row.get("value"), nonnegative=True) / factor
    if not 0 <= probability <= 1:
        raise EngineError("invalid_stat_probability:" + name)
    extras = _table(row.get("extraValues"))
    extra = _number(extras.get("1", extras.get(1, 0)))
    return probability, extra


def _option(raw: dict, name: str) -> Any:
    return _table(_table(raw.get("options")).get(name)).get("value")


def quality_thresholds(max_quality: int, difficulty: Any, offset: bool) -> list[Decimal]:
    """CraftSim RECIPE_INFO:GetQualityThresholds, including its +1 option."""
    if type(max_quality) is not int or type(offset) is not bool:
        raise EngineError("invalid_quality_parameters")
    factors = {1: (), 2: ("1",), 3: ("0.5", "1"), 5: ("0.2", "0.5", "0.8", "1")}
    if max_quality not in factors:
        raise EngineError("unsupported_quality_count")
    difficulty = _number(difficulty, nonnegative=True)
    return [difficulty * Decimal(x) + int(offset) for x in factors[max_quality]]


def selected_inputs(raw: dict) -> list[dict]:
    """Exact selected reagent quantities; no cheapest-quality substitution."""
    data = _table(raw.get("reagents"))
    result = []

    def add(entry: dict, quantity: int, required: bool) -> None:
        entry = _table(entry)
        if not quantity:
            return
        if entry.get("currencyID"):
            raise EngineError("currency_input_unsupported")
        item_id, link = _item(entry)
        result.append({"item_id": item_id, "item_link": link, "quantity": quantity,
                       "resourcefulness_cost_basis": required})

    for slot in _table(data.get("required")).values():
        slot = _table(slot)
        count = _integer(slot.get("requiredQuantity"), positive=True)
        items = list(_table(slot.get("items")).values())
        if _flag(slot, "hasQuality"):
            if len(items) not in (2, 3):
                raise EngineError("unsupported_reagent_quality_count")
            quantities = [_integer(_table(entry).get("quantity")) for entry in items]
            if sum(quantities) != count:
                raise EngineError("quality_allocation_incomplete")
            for entry, quantity in zip(items, quantities):
                add(entry, quantity, True)
        elif len(items) == 1:
            add(items[0], count, True)
        else:
            raise EngineError("required_allocation_ambiguous")
    for kind in ("optional", "finishing"):
        for slot in _table(data.get(kind)).values():
            slot = _table(slot)
            if slot.get("activeReagent"):
                # PriceData.Update counts each optional/finishing once. A slot
                # implying more than one item needs a separately checked model.
                maximum = slot.get("maxQuantity")
                if maximum is not None and (type(maximum) is not int or maximum != 1):
                    raise EngineError("optional_multi_quantity_unsupported")
                add(slot["activeReagent"], 1, False)
    selected = _table(data.get("selected", {}))
    if selected.get("activeReagent"):
        add(selected["activeReagent"], _integer(selected.get("maxQuantity"), positive=True), False)
    elif selected:
        raise EngineError("selectable_allocation_missing")
    if _table(data.get("salvage", {})):
        raise EngineError("salvage_input_unsupported")
    return result


def required_price_links(raw: dict) -> set[str]:
    """Exact selected inputs and every captured output quality, for quote joins."""
    links = {row["item_link"] for row in selected_inputs(raw)}
    result = _table(raw.get("result"))
    for item in _table(result.get("items")).values():
        links.add(_item({"item": item})[1])
    return links


def _calculate(raw: dict, prices: dict[str, Any]) -> dict:
    raw = _table(raw)
    recipe = _table(raw.get("recipe"))
    if raw.get("craftsim_version") != SUPPORTED_CRAFTSIM:
        raise EngineError("craftsim_version_not_verified")
    game = _table(raw.get("game"))
    if str(game.get("version")) + "." + str(game.get("build")) != SUPPORTED_BUILD:
        raise EngineError("game_build_not_verified")
    for key in ("isWorkOrder", "isRecraft", "isSalvageRecipe", "subRecipeCostsEnabled"):
        if _flag(recipe, key):
            raise EngineError("unsupported_recipe_mode:" + key)
    for key in ("schematic", "operation"):
        if not _flag(_table(raw.get("completeness")), key):
            raise EngineError("missing_" + key)
    supports_stats = _flag(recipe, "supportsCraftingStats")
    supports_res = _flag(recipe, "supportsResourcefulness")
    supports_mc = _flag(recipe, "supportsMulticraft")
    supports_quality = _flag(recipe, "supportsQualities")
    concentrating = _flag(recipe, "concentrating")
    if supports_stats and not supports_res:
        raise EngineError("craftsim_empty_probability_fallback_c21")
    engine = _table(raw.get("engine"))
    model = _table(engine.get("model"))
    if type(model.get("schema_version")) is not int or model["schema_version"] != 1:
        raise EngineError("unsupported_engine_model_schema")
    allocations = selected_inputs(raw)
    has_quality_inputs = any(slot.get("hasQuality") is True for slot in raw["reagents"]["required"].values())
    if has_quality_inputs and model.get("reagent_skill_status") != "observed":
        raise EngineError("allocation_skill_not_observed")
    base = _number(recipe.get("baseItemAmount"), nonnegative=True)
    if base <= 0:
        raise EngineError("invalid_base_yield")
    lower = _integer(recipe.get("minItemAmount"), positive=True)
    upper = _integer(recipe.get("maxItemAmount"), positive=True)
    if not 0 < lower <= base <= upper:
        raise EngineError("inconsistent_base_yield")
    result = _table(raw.get("result"))
    if supports_quality:
        stats = _table(raw.get("stats"))
        skill = _number(_table(stats.get("skill")).get("value"), nonnegative=True)
        difficulty = _table(stats.get("recipeDifficulty")).get("value")
        thresholds = quality_thresholds(recipe.get("maxQuality"), difficulty, _option(raw, "QUALITY_BREAKPOINT_OFFSET"))
        quality = 1 + sum(skill >= threshold for threshold in thresholds)
        concentration_quality = min(quality + 1, recipe["maxQuality"])
        if concentrating:
            quality = concentration_quality
    else:
        thresholds, quality, concentration_quality = [], 1, 1
        if concentrating:
            raise EngineError("concentration_without_quality")
    if type(result.get("expectedQuality")) is not int or result["expectedQuality"] != quality:
        raise EngineError("captured_quality_disagrees_with_model")
    if "quality_thresholds" in model:
        exported = _table(model["quality_thresholds"])
        wanted = [exported.get(str(i), exported.get(i)) for i in range(1, len(exported) + 1)]
        if [_number(value) for value in wanted] != thresholds:
            raise EngineError("captured_thresholds_disagree_with_model")
    output = _table(result.get("items")).get(str(quality), _table(result.get("items")).get(quality))
    output_id, output_link = _item({"item": output})
    if _table(result.get("expectedItem")).get("itemLink") != output_link:
        raise EngineError("captured_output_disagrees_with_quality")
    rarity = result.get("itemRarity")
    if type(rarity) is not int or not 0 <= rarity <= 8:
        raise EngineError("output_sale_channel_unknown")
    grey = rarity == 0
    if grey:
        # Caller-supplied AH quotes cannot be interpreted as vendor sell prices.
        raise EngineError("vendor_output_repricing_unsupported")
    if not isinstance(prices, dict) or len(prices) > 100000:
        raise EngineError("invalid_prices")

    def quote(link: str) -> Decimal:
        if link not in prices:
            raise EngineError("missing_exact_item_quote")
        return _number(prices[link], nonnegative=True)

    crafting_costs = required_costs = Decimal(0)
    for entry in allocations:
        cost = quote(entry["item_link"]) * entry["quantity"]
        crafting_costs += cost
        if entry["resourcefulness_cost_basis"]:
            required_costs += cost
    output_price = quote(output_link)
    ah = _number(engine.get("ah_multiplier"), nonnegative=True)
    if not 0 <= ah <= 1:
        raise EngineError("invalid_ah_multiplier")
    if engine.get("deposit_status") not in ("disabled_or_order", "engine_estimate"):
        raise EngineError("deposit_unknown")
    deposit = _number(engine.get("expected_deposit"), nonnegative=True)
    if engine["deposit_status"] == "disabled_or_order" and deposit != 0:
        raise EngineError("inconsistent_disabled_deposit")
    res_probability = saved_cost = mc_probability = extra_items = Decimal(0)
    if supports_stats and supports_res:
        res_probability, res_extra = _stat(raw, "resourcefulness")
        resource_constant = _number(model.get("resourcefulness_constant"), nonnegative=True)
        if resource_constant != _number(_option(raw, "PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT"), nonnegative=True):
            raise EngineError("resourcefulness_constant_mismatch")
        save_factor = resource_constant * (1 + res_extra)
        if not 0 <= save_factor <= 1:
            raise EngineError("invalid_resourcefulness_savings_factor")
        saved_cost = required_costs * save_factor
    if supports_mc:
        mc_probability, mc_extra = _stat(raw, "multicraft")
        constant = _number(model.get("multicraft_constant"), nonnegative=True)
        if 1 + mc_extra < 0:
            raise EngineError("invalid_multicraft_extra")
        extra_items = (1 + constant * base * (1 + mc_extra)) / 2
    expected_yield = base + mc_probability * extra_items
    # Keep the installed engine's double-deposit behavior for stats branches.
    deposit_multiplier = 2 if supports_stats else 1
    states = []
    for mc in ((False, True) if supports_stats and supports_mc else (False,)):
        for res in ((False, True) if supports_stats and supports_res else (False,)):
            chance = (mc_probability if mc else 1-mc_probability) if supports_stats and supports_mc else Decimal(1)
            chance *= (res_probability if res else 1-res_probability) if supports_stats and supports_res else Decimal(1)
            yield_in_state = base + (extra_items if mc else 0)
            costs_in_state = crafting_costs - (saved_cost if res else 0)
            profit = output_price * yield_in_state * ah - costs_in_state - deposit * deposit_multiplier
            states.append({"multicraft": mc, "resourcefulness": res, "chance": _text(chance),
                           "conditional_mean_output": _text(yield_in_state), "conditional_mean_profit_copper": _text(profit)})
    mean_profit = sum((_number(row["chance"]) * _number(row["conditional_mean_profit_copper"]) for row in states), Decimal(0))
    if abs(sum((_number(row["chance"]) for row in states), Decimal(0)) - 1) > Decimal("1e-45"):
        raise EngineError("probability_sum_mismatch")
    concentration = {"used": concentrating, "upfront": "0", "expected_spent": "0", "status": "not_used"}
    if concentrating:
        if model.get("concentration_cost_status") != "observed":
            raise EngineError("allocation_concentration_cost_not_observed")
        upfront = _number(recipe.get("concentrationCost"), nonnegative=True)
        chance, extra = _stat(raw, "ingenuity")
        refund = Decimal("0.5") + extra
        if not 0 <= refund <= 1:
            raise EngineError("invalid_ingenuity_refund_factor")
        concentration = {"used": True, "upfront": _text(upfront), "expected_spent": _text(upfront * (1 - chance * refund)),
                         "status": "allocation_observed_cost_with_craftsim_ingenuity_mean"}
    source_mean = engine.get("average_profit")
    return {"status": "model_estimate", "engine_revision": ENGINE_REVISION, "recipe_id": recipe.get("recipeID"),
            "expected_quality": quality, "expected_quality_with_concentration": concentration_quality,
            "quality_thresholds": [_text(n) for n in thresholds], "selected_inputs": allocations,
            "output_item_id": output_id, "output_item_link": output_link,
            "expected_output_per_craft": _text(expected_yield), "crafting_cost_copper": _text(crafting_costs),
            "required_cost_copper": _text(required_costs), "expected_resourcefulness_savings_copper": _text(saved_cost * res_probability),
            "expected_crafting_cost_copper": _text(crafting_costs - saved_cost * res_probability),
            "expected_profit_copper": _text(mean_profit), "concentration": concentration,
            "proc_states": states, "proc_states_kind": "craftsim_conditional_means_not_item_pmf",
            "joint_item_outcome_pmf": None, "engine_deposit_multiplier": deposit_multiplier,
            "deposit_status": "captured_engine_estimate_not_repriced", "concentration_opportunity_cost_copper": None,
            "captured_engine_mean_copper": str(source_mean) if source_mean is not None else None,
            "execution_eligible": False, "recommendation_eligible": False,
            "context_status": "captured_character_state_current_state_unknown",
            "market_status": "caller_supplied_scenario_quotes_not_execution_prices"}


def evaluate_scenario(raw: dict, prices: dict[str, Any]) -> dict:
    """Calculate one complete observed allocation or raise EngineError.

    Do not use this function as a freshness gate: it deliberately also evaluates
    historical observations. Its caller attaches source/capture/market clocks.
    """
    try:
        with localcontext() as context:
            context.prec = 50
            return _calculate(raw, prices)
    except (KeyError, TypeError, AttributeError, InvalidOperation, OverflowError):
        raise EngineError("invalid_engine_model") from None


def evaluate_scenarios(scenarios: list[dict], prices: dict[str, Any]) -> dict:
    """Re-evaluate and rank a bounded observed set, retaining every rejection."""
    if not isinstance(scenarios, list) or len(scenarios) > 256:
        raise EngineError("invalid_scenario_collection")
    rows = []
    for index, raw in enumerate(scenarios):
        try:
            row = evaluate_scenario(raw, prices)
        except EngineError as exc:
            row = {"status": "unsupported_or_missing_data", "reason": str(exc),
                   "execution_eligible": False, "recommendation_eligible": False}
        row["scenario_index"] = index
        if isinstance(raw, dict):
            row["scenario"] = raw.get("scenario", {})
            try:
                encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            except (ValueError, TypeError):
                encoded = b"invalid"
            row["scenario_fingerprint"] = hashlib.sha256(encoded).hexdigest()
        rows.append(row)
    comparable = [row for row in rows if row["status"] == "model_estimate"]
    comparable.sort(key=lambda row: (-Decimal(row["expected_profit_copper"]), row["scenario_index"]))
    return {"schema_version": 1, "engine_revision": ENGINE_REVISION,
            "status": "model_estimate" if comparable else "no_evaluable_scenarios", "scenarios": rows,
            "evaluated_count": len(comparable), "rejected_count": len(rows)-len(comparable),
            "ranked_scenario_indices": [row["scenario_index"] for row in comparable],
            "best_observed_scenario_index": comparable[0]["scenario_index"] if comparable else None,
            "optimization_scope": "observed_allocations_only_global_optimum_unproven",
            "ranking_basis": "craftsim_mean_before_concentration_opportunity_cost",
            "execution_eligible": False, "recommendation_eligible": False}
