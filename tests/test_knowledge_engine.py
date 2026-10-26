import copy
from decimal import Decimal
import unittest

from wow_auction.knowledge_engine import (
    EngineError, evaluate_scenario, evaluate_scenarios, quality_thresholds,
    required_price_links, selected_inputs,
)
from tests.test_knowledge_craftsim import fixture as bridge_fixture
from tests.test_knowledge_craftsim import profile as bridge_profile
from wow_auction.knowledge_craftsim import CraftSimError, fixed_variant_reprice


def fixture():
    raw = bridge_fixture()
    raw["recipe"].update({"supportsQualities": True, "maxQuality": 2, "concentrationCost": 100,
                          "supportsMulticraft": True, "baseItemAmount": 5, "minItemAmount": 5, "maxItemAmount": 5})
    raw["stats"] = {
        "skill": {"value": 100, "percentDivisionFactor": 1, "extraValues": {}},
        "recipeDifficulty": {"value": 200, "percentDivisionFactor": 1, "extraValues": {}},
        "multicraft": {"value": 20, "percentDivisionFactor": 100, "extraValues": {"1": 0.1}},
        "resourcefulness": {"value": 20, "percentDivisionFactor": 100, "extraValues": {"1": 0.5}},
        "ingenuity": {"value": 30, "percentDivisionFactor": 100, "extraValues": {"1": 0.1}},
    }
    raw["options"]["QUALITY_BREAKPOINT_OFFSET"] = {"value": False, "origin": "saved"}
    raw["engine"]["model"] = {"schema_version": 1, "quality_thresholds": {"1": 200},
        "multicraft_constant": 2.5, "resourcefulness_constant": 0.3,
        "reagent_skill_status": "observed", "concentration_cost_status": "observed"}
    raw["reagents"]["required"]["1"] = {"requiredQuantity": 2, "hasQuality": True,
        "items": {"1": {"item": {"itemID": 200, "itemLink": "item:200"}, "quantity": 2},
                  "2": {"item": {"itemID": 201, "itemLink": "item:201"}, "quantity": 0}}}
    raw["result"]["items"] = {"1": {"itemID": 300, "itemLink": "item:300"},
                                 "2": {"itemID": 301, "itemLink": "item:301"}}
    raw["result"]["expectedYieldPerCraft"] = 6.475
    return raw


PRICES = {"item:200": 30000, "item:201": 40000, "item:300": 100000, "item:301": 200000}


class KnowledgeEngineTests(unittest.TestCase):
    def test_full_mean_recomputed_without_using_cached_mean(self):
        raw = fixture(); raw["engine"]["average_profit"] = -999999999
        value = evaluate_scenario(raw, PRICES)
        self.assertEqual(Decimal(value["expected_output_per_craft"]), Decimal("6.475"))
        self.assertEqual(Decimal(value["crafting_cost_copper"]), 60000)
        self.assertEqual(Decimal(value["expected_resourcefulness_savings_copper"]), 5400)
        self.assertEqual(Decimal(value["expected_profit_copper"]), Decimal("558525"))
        self.assertEqual(len(value["proc_states"]), 4)
        self.assertIsNone(value["joint_item_outcome_pmf"])
        self.assertFalse(value["execution_eligible"])

    def test_market_price_change_reranks_complete_observed_allocations(self):
        low, high = fixture(), fixture()
        high["stats"]["skill"]["value"] = 200
        high["result"]["expectedQuality"] = 2
        high["result"]["expectedItem"] = high["result"]["items"]["2"]
        high["reagents"]["required"]["1"]["items"]["1"]["quantity"] = 0
        high["reagents"]["required"]["1"]["items"]["2"]["quantity"] = 2
        result = evaluate_scenarios([low, high], PRICES)
        self.assertEqual(result["best_observed_scenario_index"], 1)
        changed = dict(PRICES, **{"item:201": 1000000})
        self.assertEqual(evaluate_scenarios([low, high], changed)["best_observed_scenario_index"], 0)
        self.assertEqual(result["optimization_scope"], "observed_allocations_only_global_optimum_unproven")

    def test_quality_boundary_offset_is_source_semantics(self):
        raw = fixture(); raw["stats"]["skill"]["value"] = 200
        raw["options"]["QUALITY_BREAKPOINT_OFFSET"]["value"] = True
        raw["engine"]["model"]["quality_thresholds"] = {"1": 201}
        self.assertEqual(evaluate_scenario(raw, PRICES)["expected_quality"], 1)
        raw["stats"]["skill"]["value"] = 201
        raw["result"]["expectedQuality"] = 2
        raw["result"]["expectedItem"] = raw["result"]["items"]["2"]
        self.assertEqual(evaluate_scenario(raw, PRICES)["expected_quality"], 2)

    def test_all_supported_quality_threshold_counts(self):
        self.assertEqual(quality_thresholds(1, 100, False), [])
        self.assertEqual(quality_thresholds(2, 100, False), [Decimal(100)])
        self.assertEqual(quality_thresholds(3, 100, True), [Decimal(51), Decimal(101)])
        self.assertEqual(quality_thresholds(5, 100, False), list(map(Decimal, [20, 50, 80, 100])))
        with self.assertRaisesRegex(EngineError, "unsupported_quality_count"):
            quality_thresholds(4, 100, False)

    def test_concentration_cost_and_ingenuity_refund_separate_from_money(self):
        raw = fixture(); raw["recipe"]["concentrating"] = True
        raw["result"]["expectedQuality"] = 2
        raw["result"]["expectedItem"] = raw["result"]["items"]["2"]
        value = evaluate_scenario(raw, PRICES)
        self.assertEqual(Decimal(value["concentration"]["expected_spent"]), 82)
        self.assertEqual(Decimal(value["concentration"]["upfront"]), 100)
        self.assertIsNone(value["concentration_opportunity_cost_copper"])

    def test_unknown_selected_concentration_not_zero(self):
        raw = fixture(); raw["recipe"]["concentrating"] = True
        raw["result"]["expectedQuality"] = 2
        raw["result"]["expectedItem"] = raw["result"]["items"]["2"]
        raw["engine"]["model"]["concentration_cost_status"] = "unavailable"
        with self.assertRaisesRegex(EngineError, "allocation_concentration_cost_not_observed"):
            evaluate_scenario(raw, PRICES)

    def test_resourcefulness_does_not_save_optional_inputs(self):
        raw = fixture()
        raw["reagents"]["optional"] = {"1": {"maxQuantity": 1, "activeReagent": {
            "item": {"itemID": 201, "itemLink": "item:201"}}}}
        value = evaluate_scenario(raw, PRICES)
        self.assertEqual(Decimal(value["crafting_cost_copper"]), 100000)
        self.assertEqual(Decimal(value["required_cost_copper"]), 60000)
        self.assertEqual(Decimal(value["expected_resourcefulness_savings_copper"]), 5400)

    def test_required_selectable_uses_max_quantity_without_resourcefulness(self):
        raw = fixture()
        raw["reagents"]["selected"] = {"maxQuantity": 3, "activeReagent": {
            "item": {"itemID": 201, "itemLink": "item:201"}}}
        value = evaluate_scenario(raw, PRICES)
        self.assertEqual(Decimal(value["crafting_cost_copper"]), 180000)
        self.assertEqual(Decimal(value["required_cost_copper"]), 60000)

    def test_no_stats_branch_single_deposit(self):
        raw = fixture(); raw["recipe"].update(supportsCraftingStats=False, supportsResourcefulness=False, supportsMulticraft=False)
        value = evaluate_scenario(raw, PRICES)
        self.assertEqual(Decimal(value["expected_profit_copper"]), 414000)
        self.assertEqual(value["engine_deposit_multiplier"], 1)
        self.assertEqual(len(value["proc_states"]), 1)

    def test_resource_only_branch_double_deposit(self):
        raw = fixture(); raw["recipe"]["supportsMulticraft"] = False
        value = evaluate_scenario(raw, PRICES)
        self.assertEqual(Decimal(value["expected_profit_copper"]), 418400)
        self.assertEqual(value["engine_deposit_multiplier"], 2)
        self.assertEqual(len(value["proc_states"]), 2)

    def test_unused_unpriced_reagent_is_not_needed(self):
        prices = {k: v for k, v in PRICES.items() if k != "item:201"}
        self.assertEqual(evaluate_scenario(fixture(), prices)["status"], "model_estimate")

    def test_unused_uncached_quality_link_does_not_block_selected_allocation(self):
        raw = fixture()
        del raw["reagents"]["required"]["1"]["items"]["2"]["item"]["itemLink"]
        self.assertEqual(evaluate_scenario(raw, PRICES)["status"], "model_estimate")

    def test_physical_output_bounds_are_integer_but_mean_may_be_fractional(self):
        for key in ("minItemAmount", "maxItemAmount"):
            for value in (1.5, True, "5"):
                raw = fixture(); raw["recipe"][key] = value
                with self.subTest(key=key, value=value):
                    with self.assertRaisesRegex(EngineError, "invalid_quantity_or_identifier"):
                        evaluate_scenario(raw, PRICES)
        raw = fixture(); raw["recipe"].update(minItemAmount=1,maxItemAmount=2,baseItemAmount=1.5)
        self.assertEqual(evaluate_scenario(raw, PRICES)["status"], "model_estimate")

    def test_missing_price_is_not_zero(self):
        prices = {k: v for k, v in PRICES.items() if k != "item:200"}
        with self.assertRaisesRegex(EngineError, "missing_exact_item_quote"):
            evaluate_scenario(fixture(), prices)

    def test_price_zero_is_explicit_and_not_missing(self):
        self.assertEqual(evaluate_scenario(fixture(), dict(PRICES, **{"item:300": 0}))["status"], "model_estimate")

    def test_quality_allocation_not_completed_by_cheapest_quote(self):
        raw = fixture(); raw["reagents"]["required"]["1"]["items"]["1"]["quantity"] = 0
        with self.assertRaisesRegex(EngineError, "quality_allocation_incomplete"):
            evaluate_scenario(raw, PRICES)

    def test_missing_operation_success_not_native_fallback_zero(self):
        raw = fixture(); raw["engine"]["model"]["reagent_skill_status"] = "unavailable"
        with self.assertRaisesRegex(EngineError, "allocation_skill_not_observed"):
            evaluate_scenario(raw, PRICES)

    def test_quality_output_crosscheck(self):
        raw = fixture(); raw["stats"]["skill"]["value"] = 200
        with self.assertRaisesRegex(EngineError, "captured_quality_disagrees_with_model"):
            evaluate_scenario(raw, PRICES)

    def test_threshold_crosscheck(self):
        raw = fixture(); raw["engine"]["model"]["quality_thresholds"]["1"] = 199
        with self.assertRaisesRegex(EngineError, "captured_thresholds_disagree_with_model"):
            evaluate_scenario(raw, PRICES)

    def test_source_constant_crosscheck(self):
        raw = fixture(); raw["engine"]["model"]["resourcefulness_constant"] = 0.5
        with self.assertRaisesRegex(EngineError, "resourcefulness_constant_mismatch"):
            evaluate_scenario(raw, PRICES)

    def test_unsupported_modes_are_not_ordinary_recipes(self):
        for key in ("isWorkOrder", "isRecraft", "isSalvageRecipe", "subRecipeCostsEnabled"):
            with self.subTest(key=key):
                raw = fixture(); raw["recipe"][key] = True
                with self.assertRaisesRegex(EngineError, "unsupported_recipe_mode"):
                    evaluate_scenario(raw, PRICES)

    def test_unsupported_build_or_engine(self):
        for field in ("craftsim_version", "game"):
            raw = fixture(); raw[field] = "new" if field == "craftsim_version" else {"version": "12.9", "build": "1"}
            with self.assertRaisesRegex(EngineError, "not_verified"):
                evaluate_scenario(raw, PRICES)

    def test_c21_does_not_return_zero_profit(self):
        raw = fixture(); raw["recipe"]["supportsResourcefulness"] = False
        with self.assertRaisesRegex(EngineError, "craftsim_empty_probability_fallback_c21"):
            evaluate_scenario(raw, PRICES)

    def test_vendor_quote_cannot_be_ah(self):
        raw = fixture(); raw["result"]["itemRarity"] = 0
        with self.assertRaisesRegex(EngineError, "vendor_output_repricing_unsupported"):
            evaluate_scenario(raw, PRICES)

    def test_variant_quotes_do_not_collapse_to_item_id(self):
        raw = fixture(); raw["result"]["items"]["1"]["itemLink"] = "item:300:variant"
        raw["result"]["expectedItem"]["itemLink"] = "item:300:variant"
        with self.assertRaisesRegex(EngineError, "missing_exact_item_quote"):
            evaluate_scenario(raw, PRICES)

    def test_currency_is_not_priced_as_item(self):
        raw = fixture(); raw["reagents"]["required"]["1"]["items"]["1"]["currencyID"] = 1
        with self.assertRaisesRegex(EngineError, "currency_input_unsupported"):
            evaluate_scenario(raw, PRICES)

    def test_missing_and_malformed_inputs_remain_safe(self):
        for raw in (None, [], {}, {"recipe": 1}, {"recipe": {}}, {"recipe": {"isWorkOrder": "false"}}):
            with self.subTest(raw=raw):
                self.assertEqual(evaluate_scenarios([raw], PRICES)["rejected_count"], 1)

    def test_non_finite_prices_and_invalid_probabilities_reject(self):
        for value in (float("nan"), float("inf"), "1e999999", "0e999999", "1e-999999", True, -1):
            with self.subTest(value=value):
                with self.assertRaises(EngineError):
                    evaluate_scenario(fixture(), dict(PRICES, **{"item:200": value}))
        raw = fixture(); raw["stats"]["multicraft"]["value"] = 101
        with self.assertRaisesRegex(EngineError, "invalid_stat_probability"):
            evaluate_scenario(raw, PRICES)

    def test_rejections_do_not_hide_valid_scenarios(self):
        result = evaluate_scenarios([{}, fixture()], PRICES)
        self.assertEqual((result["evaluated_count"], result["rejected_count"]), (1, 1))
        self.assertEqual(result["best_observed_scenario_index"], 1)

    def test_no_mutation_and_stable_fingerprint(self):
        raw = fixture(); original = copy.deepcopy(raw)
        one = evaluate_scenarios([raw], PRICES)
        two = evaluate_scenarios([raw], PRICES)
        self.assertEqual(raw, original)
        self.assertEqual(one, two)

    def test_quote_keys_only_selected_inputs_and_all_output_qualities(self):
        self.assertEqual(required_price_links(fixture()), {"item:200", "item:300", "item:301"})

    def test_optional_many_items_are_not_silently_underpriced(self):
        for maximum in (2, True, "1", 0):
            raw = fixture(); raw["reagents"]["optional"] = {"1": {"maxQuantity": maximum, "activeReagent": {
                "item": {"itemID": 201, "itemLink": "item:201"}}}}
            with self.subTest(maximum=maximum):
                with self.assertRaisesRegex(EngineError, "optional_multi_quantity_unsupported"):
                    selected_inputs(raw)


class LegacyRepriceOptionalTests(unittest.TestCase):
    def optional_profile(self, kind, maximum):
        raw = bridge_fixture()
        raw["reagents"][kind] = {"1": {"activeReagent": {
            "item": {"itemID": 201, "itemLink": "item:201"}}}}
        if maximum is not None:
            raw["reagents"][kind]["1"]["maxQuantity"] = maximum
        raw["prices"]["reagentPriceInfos"]["201"] = {"itemPrice": 1000, "priceInfo": {"isAHPrice": True}}
        raw["prices"]["craftingCosts"] += 1000
        raw["engine"]["average_profit"] -= 1000
        return bridge_profile(raw)

    def reprice(self, profile):
        return fixed_variant_reprice(profile, {"item:200": 30000, "item:201": 1500, "item:300": 100000},
            now=1001, current_build=profile["build"], expected_fingerprint=profile["state_fingerprint"])

    def test_optional_and_finishing_price_derivative_matches_one_item_source_cost(self):
        for kind in ("optional", "finishing"):
            for maximum in (None, 1):
                with self.subTest(kind=kind, maximum=maximum):
                    result = self.reprice(self.optional_profile(kind, maximum))
                    self.assertEqual(Decimal(result["expected_profit_copper_per_craft"]), 31500)

    def test_multi_quantity_optional_is_explicitly_unsupported_not_wrong_cost(self):
        for kind in ("optional", "finishing"):
            for maximum in (2, True, "1", 0):
                with self.subTest(kind=kind, maximum=maximum):
                    with self.assertRaisesRegex(CraftSimError, "optional_multi_quantity_unsupported"):
                        self.reprice(self.optional_profile(kind, maximum))


if __name__ == "__main__":
    unittest.main()
