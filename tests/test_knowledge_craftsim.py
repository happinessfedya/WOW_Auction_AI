import copy
import json
import unittest

from wow_auction.knowledge_craftsim import (
    CraftSimError, assess_profile, fixed_variant_reprice, parse_bridge, parse_passive, state_fingerprint,
)


def fixture():
    return {
        "schema_version": 1, "exporter_version": "0.3.0", "sequence": 1, "context_epoch": 1,
        "captured_at": 1000, "event": "CRAFTSIM_RECIPE_DATA_UPDATED", "craftsim_version": "27.0.7",
        "game": {"version": "12.1.5", "build": "70575", "interface": 120105},
        "recipe": {"recipeID": 100, "minItemAmount": 1, "maxItemAmount": 1, "baseItemAmount": 1,
                   "supportsCraftingStats": True, "supportsResourcefulness": True,
                   "supportsMulticraft": False, "supportsSpecializations": False,
                   "concentrating": False, "isWorkOrder": False, "isRecraft": False,
                   "isSalvageRecipe": False, "subRecipeCostsEnabled": False},
        "crafter": {"name": "Synthetic", "realm": "Test"}, "gear": {"tool": {"itemLink": "tool"}},
        "stats": {"resourcefulness": {"value": 0, "percentDivisionFactor": 100, "extraValues": {}}},
        "base_stats": {}, "modifiers": {}, "buffs": {}, "specialization": {"nodes": {}},
        "options": {"PROFIT_CALCULATION_RESOURCEFULNESS_CONSTANT": {"value": 0.3, "origin": "saved"}},
        "reagents": {"required": {"1": {"requiredQuantity": 2, "hasQuality": False, "items": {
            "1": {"item": {"itemID": 200, "itemLink": "item:200"}, "quantity": 0}}}},
            "optional": {}, "finishing": {}},
        "result": {"expectedQuality": 1, "expectedQualityConcentration": 2, "expectedYieldPerCraft": 1,
                   "expectedItem": {"itemID": 300, "itemLink": "item:300"}, "itemRarity": 2},
        "prices": {"craftingCosts": 60000, "craftingCostsRequired": 60000,
                   "resourcefulnessSavedCostsAverage": 0, "qualityPriceList": {"1": 100000},
                   "reagentPriceInfos": {"200": {"itemPrice": 30000, "priceInfo": {"isAHPrice": True}}}},
        "engine": {"average_profit": 33000, "price_unit": "copper", "profit_unit": "per_craft",
                   "expected_deposit": 1000, "deposit_status": "engine_estimate", "ah_multiplier": 0.95},
        "completeness": {"schematic": True, "operation": True},
    }


def literal(value):
    if value is None: return "nil"
    if value is True: return "true"
    if value is False: return "false"
    if isinstance(value, str): return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (int, float)): return str(value)
    return "{" + ",".join("[" + literal(k) + "]=" + literal(v) for k, v in value.items()) + "}"


def exported(*rows, invalidation=None):
    data = {"schema_version": 1, "records": {i+1: value for i, value in enumerate(rows)},
            "context_epoch": 1, "sequence": len(rows), "dropped_records": 0}
    if invalidation:
        data["last_invalidation"] = invalidation
        data["context_epoch"] = invalidation["epoch"]
    return ("WowAuctionBridgeDB=" + literal(data)).encode()


def profile(raw=None):
    return parse_bridge(exported(raw or fixture()), observed_at=1001)["profiles"][0]


class CraftSimAdapterTests(unittest.TestCase):
    def test_literal_import_preserves_engine_mean_separate_from_execution(self):
        p = profile()
        self.assertEqual(p["status"], "model_estimate")
        self.assertEqual(p["raw"]["engine"]["average_profit"], 33000)
        self.assertIsNone(p["joint_outcome_pmf"])
        self.assertFalse(p["execution_eligible"])

    def test_unsupported_version_is_never_silently_accepted(self):
        raw = fixture(); raw["craftsim_version"] = "99.0"
        self.assertEqual(profile(raw)["status"], "unsupported")

    def test_unsupported_build_requires_adapter_review(self):
        raw = fixture(); raw["game"]["build"] = "99999"
        self.assertIn("game_build_not_verified", profile(raw)["reasons"])

    def test_missing_schematic_and_cached_mean_fail_closed(self):
        raw = fixture(); raw["completeness"]["schematic"] = False; del raw["engine"]["average_profit"]
        self.assertEqual(profile(raw)["status"], "missing_data")

    def test_c21_zero_without_supported_branch_not_valid_zero(self):
        raw = fixture(); raw["recipe"]["supportsResourcefulness"] = False; raw["engine"]["average_profit"] = 0
        self.assertEqual(profile(raw)["status"], "unsupported")
        self.assertIn("craftsim_empty_probability_fallback_c21", profile(raw)["reasons"])

    def test_missing_or_nonboolean_branch_flags_fail_closed(self):
        for flag in ("supportsCraftingStats", "supportsResourcefulness", "supportsMulticraft",
                     "supportsSpecializations", "isWorkOrder", "isRecraft", "isSalvageRecipe",
                     "subRecipeCostsEnabled"):
            for bad in (None, 0, 1, "false"):
                raw = fixture()
                if bad is None:
                    del raw['recipe'][flag]
                else:
                    raw['recipe'][flag] = bad
                with self.subTest(flag=flag, bad=bad):
                    result = profile(raw)
                    self.assertNotEqual(result['status'], 'model_estimate')
                    self.assertIn('critical_flag_missing_or_invalid:' + flag, result['reasons'])

    def test_missing_stats_flag_cannot_bypass_zero_fallback(self):
        raw = fixture(); del raw['recipe']['supportsCraftingStats']
        raw['recipe']['supportsResourcefulness'] = False; raw['engine']['average_profit'] = 0
        self.assertEqual(profile(raw)['status'], 'missing_data')

    def test_true_zero_in_supported_branch_is_preserved(self):
        raw = fixture(); raw["engine"]["average_profit"] = 0
        self.assertEqual(profile(raw)["status"], "model_estimate")

    def test_mean_is_not_integer_outcome_pmf(self):
        raw = fixture(); raw["result"]["expectedYieldPerCraft"] = 1.375
        p = profile(raw)
        self.assertEqual(p["status"], "model_estimate")
        self.assertIsNone(p["joint_outcome_pmf"])

    def test_minimum_physical_batch_is_integer(self):
        raw = fixture(); raw["recipe"]["minItemAmount"] = 1.25
        self.assertEqual(profile(raw)["status"], "missing_data")

    def test_reagent_missing_quote_not_free_input(self):
        raw = fixture(); raw["prices"]["reagentPriceInfos"]["200"]["priceInfo"] = {"noAHPriceFound": True}
        self.assertIn("reagent_price_unavailable", profile(raw)["reasons"])

    def test_explicit_override_not_missing_ah_quote(self):
        raw = fixture(); raw["prices"]["reagentPriceInfos"]["200"]["priceInfo"] = {"noAHPriceFound": True, "isOverride": True}
        self.assertEqual(profile(raw)["status"], "model_estimate")

    def test_each_context_section_changes_fingerprint(self):
        raw = fixture(); original = state_fingerprint(raw)
        for key in ("gear", "specialization", "buffs", "prices", "reagents", "game", "options", "recipe", "result"):
            changed = copy.deepcopy(raw); changed[key]["change"] = "observed"
            with self.subTest(key=key): self.assertNotEqual(original, state_fingerprint(changed))

    def test_capture_time_does_not_change_state_fingerprint(self):
        raw = fixture(); changed = copy.deepcopy(raw); changed["captured_at"] += 1
        self.assertEqual(state_fingerprint(raw), state_fingerprint(changed))

    def test_ttl_context_build_invalidation(self):
        p = profile()
        self.assertEqual(assess_profile(p, now=1000+86401)["status"], "stale")
        self.assertEqual(assess_profile(p, now=1001, current_build="new")["status"], "stale")
        self.assertEqual(assess_profile(p, now=1001, expected_fingerprint="changed")["status"], "stale")

    def test_invalidated_after_last_capture_is_stale(self):
        result = parse_bridge(exported(fixture(), invalidation={"epoch": 2, "at": 1000}), observed_at=1001)
        self.assertEqual(result["profiles"][0]["status"], "stale")
        self.assertEqual(result["profiles"][0]["capture_status"], "model_estimate")

    def test_exporter_diagnostic_does_not_discard_recipe_id(self):
        raw = {"schema_version":1,"sequence":1,"context_epoch":1,"captured_at":1000,
               "status":"missing_data","error":"capture_failed","recipe":{"recipeID":100},
               "diagnostic":{"code":"byte_limit","section":"reagents"}}
        p = profile(raw)
        self.assertEqual(p["recipe_id"], 100)
        self.assertEqual(p["capture_status"], "missing_data")
        self.assertIn("export_capture_failed:byte_limit", p["reasons"])

    def test_reload_preserves_snapshot_facts_but_current_context_unknown(self):
        result = parse_bridge(exported(fixture(), invalidation={"epoch":2,"at":1001,"reason":"session_start"}), observed_at=1002)
        p = result["profiles"][0]
        self.assertEqual(p["capture_status"], "model_estimate")
        self.assertEqual(p["capture_reasons"], [])
        self.assertEqual(p["context_status"], "unknown_after_session_start")
        self.assertFalse(p["execution_eligible"])

    def test_real_context_change_is_not_session_unknown(self):
        result = parse_bridge(exported(fixture(), invalidation={"epoch":2,"at":1001,"reason":"PLAYER_EQUIPMENT_CHANGED"}), observed_at=1002)
        p = result["profiles"][0]
        self.assertEqual(p["capture_status"], "model_estimate")
        self.assertEqual(p["status"], "stale")
        self.assertEqual(p["context_status"], "changed_after_capture")

    def test_future_capture_clock_stays_explicit(self):
        raw = fixture(); raw["captured_at"] = 1002
        self.assertIn("capture_clock_after_receive", profile(raw)["reasons"])

    def test_cached_crafter_context_has_unknown_age(self):
        raw = fixture(); raw["recipe"]["operationInfoCached"] = True
        self.assertEqual(profile(raw)["status"], "missing_data")

    def test_gear_inventory_cache_ready_flag_is_not_selected_gear_age(self):
        raw = fixture(); raw["recipe"]["professionGearCached"] = True
        p = profile(raw)
        self.assertEqual(p["capture_status"], "model_estimate")
        self.assertTrue(p["raw"]["recipe"]["professionGearCached"])
        self.assertIn("current_character_state_after_export", p["unknowns"])

    def test_missing_unused_finishing_candidate_is_not_missing_selected_price(self):
        raw = fixture()
        raw["prices"]["reagentPriceInfos"]["247724"] = {
            "itemPrice":0,"priceInfo":{"ahPrice":0,"noAHPriceFound":True,"isOverride":False,
                                       "noPriceSource":False,"isAHPrice":True,"isExpectedCost":False}}
        raw["reagents"]["finishing"] = {"1":{"locked":False,"maxQuantity":1,"dataSlotIndex":5}}
        p = profile(raw)
        self.assertEqual(p["capture_status"], "model_estimate")
        self.assertEqual(p["price_scope"]["unused_candidates_without_quote"], ["247724"])

    def test_same_unquoted_finishing_reagent_when_selected_blocks(self):
        raw = fixture()
        raw["prices"]["reagentPriceInfos"]["247724"] = {"itemPrice":0,"priceInfo":{"noAHPriceFound":True}}
        raw["reagents"]["finishing"] = {"1":{"maxQuantity":1,"activeReagent":{"item":{"itemID":247724,"itemLink":"item:247724"}}}}
        self.assertIn("reagent_price_unavailable", profile(raw)["capture_reasons"])

    def test_unallocated_quality_means_engine_cheapest_fallback_not_fixed_input(self):
        raw = fixture()
        raw["reagents"]["required"]["1"] = {"requiredQuantity":8,"hasQuality":True,"items":{
            "1":{"quantity":0,"item":{"itemID":236761,"itemLink":"item:236761"}},
            "2":{"quantity":0,"item":{"itemID":236767,"itemLink":"item:236767"}}}}
        raw["prices"]["reagentPriceInfos"] = {"236761":{"itemPrice":19300,"priceInfo":{"isAHPrice":True}},
                                               "236767":{"itemPrice":27700,"priceInfo":{"isAHPrice":True}}}
        p = profile(raw)
        self.assertIn("quality_allocation_incomplete", p["capture_reasons"])
        self.assertNotIn("reagent_price_unavailable", p["capture_reasons"])
        self.assertEqual(p["price_scope"]["required_by_captured_model"], ["236761","236767"])

    def test_absent_required_price_row_blocks(self):
        raw=fixture(); raw["prices"]["reagentPriceInfos"]={}
        self.assertIn("reagent_price_unavailable", profile(raw)["capture_reasons"])

    def test_unimplemented_specialization_is_not_complete_context(self):
        raw = fixture(); raw["recipe"]["supportsSpecializations"] = True
        raw["specialization"]["implemented"] = False
        self.assertIn("specialization_not_implemented", profile(raw)["reasons"])

    def test_unknown_schema_and_extra_globals_rejected(self):
        with self.assertRaises(CraftSimError): parse_bridge(b"WowAuctionBridgeDB={schema_version=2,records={}}", observed_at=1)
        with self.assertRaises(CraftSimError): parse_bridge(exported() + b" Other={}", observed_at=1)

    def test_sparse_record_array_rejected(self):
        with self.assertRaises(CraftSimError):
            parse_bridge(b"WowAuctionBridgeDB={schema_version=1,records={[2]={}}}", observed_at=1)

    def test_one_bad_record_does_not_discard_other_evidence(self):
        result = parse_bridge(exported(fixture(), {"schema_version": 1}), observed_at=1001)
        self.assertEqual(len(result["profiles"]), 1)
        self.assertEqual(len(result["rejected"]), 1)

    def test_duplicate_sequence_rejected(self):
        result = parse_bridge(exported(fixture(), fixture()), observed_at=1001)
        self.assertEqual(len(result["profiles"]), 1)
        self.assertEqual(len(result["rejected"]), 1)

    def test_record_ring_bound(self):
        with self.assertRaises(CraftSimError): parse_bridge(exported(*[fixture()]*101), observed_at=1001)

    def test_zero_result_price_is_not_a_verified_quote(self):
        raw = fixture(); raw["prices"]["qualityPriceList"]["1"] = 0
        self.assertIn("zero_output_quote_ambiguous", profile(raw)["reasons"])

    def test_deleted_invalidation_detail_does_not_resurrect_profile(self):
        data = {"schema_version":1,"sequence":1,"context_epoch":2,"dropped_records":0,"records":{1:fixture()}}
        result = parse_bridge(("WowAuctionBridgeDB="+literal(data)).encode(), observed_at=1001)
        self.assertEqual(result["profiles"][0]["status"], "stale")

    def test_code_is_not_executed(self):
        with self.assertRaises(ValueError): parse_bridge(b"WowAuctionBridgeDB=os.execute('bad')", observed_at=1001)


class FixedVariantRepriceTests(unittest.TestCase):
    def reprice(self, p=None, quotes=None):
        p = p or profile()
        return fixed_variant_reprice(p, quotes or {"item:200": 30000, "item:300": 100000}, now=1001,
                                     current_build=p["build"], expected_fingerprint=p["state_fingerprint"])

    def test_c11_baseline_two_deposits_preserved(self):
        result = self.reprice()
        self.assertEqual(result["expected_profit_copper_per_craft"], "33000.00")
        self.assertEqual(result["deposit_treatment"], "unchanged_engine_baseline")

    def test_changed_price_keeps_fixed_allocation(self):
        result = self.reprice(quotes={"item:200": 31000, "item:300": 110000})
        self.assertEqual(result["expected_profit_copper_per_craft"], "40500.00")
        self.assertFalse(result["global_optimum_claimed"])

    def test_resourcefulness_reprices_required_cost_only(self):
        raw = fixture(); raw["stats"]["resourcefulness"]["value"] = 20
        raw["prices"]["resourcefulnessSavedCostsAverage"] = 3600
        raw["engine"]["average_profit"] += 3600
        result = self.reprice(profile(raw), {"item:200": 31000, "item:300": 100000})
        self.assertEqual(result["expected_profit_copper_per_craft"], "34720.00")

    def test_unknown_deposit_prevents_repricing(self):
        raw = fixture(); raw["engine"]["deposit_status"] = "unavailable_or_zero_ambiguous"
        with self.assertRaisesRegex(CraftSimError, "deposit_unknown"): self.reprice(profile(raw))

    def test_subrecipe_and_currency_are_not_item_costs(self):
        raw = fixture(); raw["recipe"]["subRecipeCostsEnabled"] = True
        with self.assertRaisesRegex(CraftSimError, "unsupported"): self.reprice(profile(raw))
        raw = fixture(); raw["reagents"]["required"]["1"]["items"]["1"]["currencyID"] = 44
        p = profile(raw)
        self.assertIn("currency_input_valuation_unknown", p["capture_reasons"])
        with self.assertRaisesRegex(CraftSimError, "missing_data"): self.reprice(p)

    def test_partial_quality_allocation_rejected(self):
        raw = fixture(); raw["reagents"]["required"]["1"]["hasQuality"] = True
        raw["reagents"]["required"]["1"]["items"]["1"]["quantity"] = 1
        p = profile(raw)
        self.assertIn("quality_allocation_incomplete", p["capture_reasons"])
        with self.assertRaisesRegex(CraftSimError, "missing_data"): self.reprice(p)

    def test_cost_reconciliation_required(self):
        raw = fixture(); raw["prices"]["craftingCosts"] += 1
        with self.assertRaisesRegex(CraftSimError, "reconcile"): self.reprice(profile(raw))

    def test_no_fallback_from_exact_variant_to_item_id(self):
        with self.assertRaises(CraftSimError): self.reprice(quotes={"200": 30000, "300": 100000})

    def test_stale_profile_cannot_reprice(self):
        p = profile(); p["last_invalidation"] = {"epoch": 2}
        with self.assertRaisesRegex(CraftSimError, "stale"): self.reprice(p)

    def test_no_mutation_of_original_engine_observation(self):
        p = profile(); before = copy.deepcopy(p); self.reprice(p)
        self.assertEqual(p, before)


def passive_fixture():
    return {"schema_version":1,"scope":"available_local_recipe_ids","engine_profile":False,
            "generation":1,"started_at":999,"build":"12.1.5.70575","status":"running",
            "queue":{1:100,2:200},"queued":2,"processed":1,"cursor":2,"record_count":1,
            "storage_bytes":5000,"storage_nodes":50,"schematics_observed":1,"operations_observed":0,
            "coverage_complete":False,"enumeration_limited":False,"records":{100:{"recipe_id":100,
                "generation":1,"captured_at":1000,"engine_profile":False,"source_kind":"passive_game_and_cache_observation",
                "schematic":{"status":"observed","data":{"recipeID":100,"quantityMin":1,"quantityMax":1},"captured_at":1000,"source_actor":{"kind":"logged_in_player_request","player_guid":"Synthetic-A"}},
                "recipe_info":{"status":"data_unavailable","captured_at":1000,"source_actor":{"kind":"unknown"}},
                "operation_info":{"status":"api_not_ready_or_error","captured_at":1000,"source_actor":{"kind":"unknown"}},
                "cached":{},"last_good":{}}},"contexts":{}}


class PassiveBridgeTests(unittest.TestCase):
    def parse(self, passive):
        db={"schema_version":1,"records":{},"sequence":0,"context_epoch":1,"dropped_records":0,"passive":passive}
        return parse_bridge(("WowAuctionBridgeDB="+literal(db)).encode(), observed_at=1001)

    def test_passive_data_is_not_an_engine_profile(self):
        result=self.parse(passive_fixture())
        self.assertEqual(result["profiles"], [])
        self.assertFalse(result["passive"]["engine_profile"])
        self.assertEqual(result["passive"]["processed"],1)
        self.assertIsNone(result["passive"]["world_coverage"])
        self.assertEqual(result["passive"]["current_character_state"],"unverified")

    def test_partial_checkpoint_not_complete(self):
        data=passive_fixture();data["coverage_complete"]=True
        with self.assertRaisesRegex(CraftSimError,"completion"):self.parse(data)

    def test_completed_scope_may_have_no_operation_data(self):
        data=passive_fixture();data["queue"]={1:100};data["queued"]=1
        data["status"]="complete_available_scope";data["coverage_complete"]=True
        result=self.parse(data)["passive"]
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["unavailable_api_tables"],2)
        self.assertFalse(result["engine_profile"])

    def test_missing_processed_record_rejected(self):
        data=passive_fixture();data["records"]={};data["record_count"]=0
        with self.assertRaisesRegex(CraftSimError,"lost_record"):self.parse(data)

    def test_future_last_good_is_not_backdated(self):
        data=passive_fixture();data["records"][100]["last_good"]={"schematic":{"captured_at":1001,"data":{}}}
        with self.assertRaisesRegex(CraftSimError,"last_good_time"):self.parse(data)

    def test_old_actor_operation_not_relabelled_after_new_actor_nil(self):
        data=passive_fixture();row=data["records"][100]
        row["operation_info"]["source_actor"]={"kind":"logged_in_player_request","player_guid":"Synthetic-B"}
        row["last_good"]={"operation_info":{"captured_at":990,"build":"12.1.5.70575","data":{"baseSkill":100},
            "source_actor":{"kind":"logged_in_player_request","player_guid":"Synthetic-A"}}}
        parsed=self.parse(data)["passive"]["records"]["100"]
        self.assertEqual(parsed["operation_info"]["source_actor"]["player_guid"],"Synthetic-B")
        self.assertEqual(parsed["last_good"]["operation_info"]["source_actor"]["player_guid"],"Synthetic-A")

    def test_previous_generation_cannot_complete_current_checkpoint(self):
        data=passive_fixture();data["generation"]=2
        with self.assertRaisesRegex(CraftSimError,"lost_record"):self.parse(data)

    def test_discovery_failure_preserves_explicit_unknown(self):
        data={"schema_version":1,"status":"discovery_error","scope":"available_local_recipe_ids","engine_profile":False}
        result=self.parse(data)
        self.assertFalse(result["passive"]["coverage_complete"])
        self.assertEqual(result["profiles"],[])

    def test_storage_limit_does_not_become_complete(self):
        data=passive_fixture();data["status"]="storage_budget_reached"
        result=self.parse(data)["passive"]
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["status"],"storage_budget_reached")

    def test_duplicate_queue_ids_rejected(self):
        data=passive_fixture();data["queue"][2]=100
        with self.assertRaisesRegex(CraftSimError,"recipe_ids"):self.parse(data)

    def test_passive_engine_profile_claim_rejected(self):
        data=passive_fixture();data["engine_profile"]=True
        with self.assertRaisesRegex(CraftSimError,"scope_or_profile"):self.parse(data)

    def test_scalar_query_and_last_good_are_safe_schema_errors(self):
        # Direct calls preserve explicit None; Lua nil would remove the field.
        for field in ("recipe_info", "schematic", "operation_info", "last_good"):
            for invalid in (1, False, None, "bad", []):
                with self.subTest(field=field, invalid=invalid):
                    data=json.loads(json.dumps(passive_fixture()))
                    data["records"]["100"][field]=invalid
                    with self.assertRaises(CraftSimError): parse_passive(data, observed_at=1001)

    def test_scalar_nested_metadata_is_rejected(self):
        for field in ("contexts", "source_counts", "source_statuses", "record_sizes", "queue", "records",
                      "last_discovery_error", "context_diagnostic"):
            for invalid in (1, False, None, "bad", []):
                with self.subTest(field=field, invalid=invalid):
                    data=json.loads(json.dumps(passive_fixture()));data[field]=invalid
                    with self.assertRaises(CraftSimError): parse_passive(data, observed_at=1001)

    def test_nested_cache_and_actor_shapes_fail_closed(self):
        for invalid in (1, False, None, "bad", []):
            cases=(
                ("contexts", {"Synthetic":invalid}),
                ("contexts", {"Synthetic":{"professionGear":invalid}}),
                ("record_sizes", {"100":invalid}),
                ("source_counts", {"cache":invalid}),
                ("source_statuses", {"cache":invalid}),
            )
            for field, value in cases:
                if field == "source_counts" and type(invalid) is int and invalid >= 0:
                    continue
                with self.subTest(field=field, invalid=invalid):
                    data=json.loads(json.dumps(passive_fixture()));data[field]=value
                    with self.assertRaises(CraftSimError): parse_passive(data, observed_at=1001)
            cases=(
                ("cached", invalid),
                ("cached", {"Synthetic":invalid}),
                ("cached", {"Synthetic":{"operation_info":invalid}}),
                ("last_good", {"operation_info":invalid}),
            )
            for field, value in cases:
                with self.subTest(field=field, invalid=invalid):
                    data=json.loads(json.dumps(passive_fixture()));data["records"]["100"][field]=value
                    with self.assertRaises(CraftSimError): parse_passive(data, observed_at=1001)
            for field in ("source_actor", "data"):
                with self.subTest(query_field=field, invalid=invalid):
                    data=json.loads(json.dumps(passive_fixture()))
                    data["records"]["100"]["schematic"][field]=invalid
                    with self.assertRaises(CraftSimError): parse_passive(data, observed_at=1001)

    def test_unhashable_status_is_a_safe_schema_error(self):
        for invalid in ({}, [], False, None):
            with self.subTest(invalid=invalid):
                data=json.loads(json.dumps(passive_fixture()));data["status"]=invalid
                with self.assertRaisesRegex(CraftSimError,"passive_status_invalid"):
                    parse_passive(data, observed_at=1001)

    def test_malformed_saved_variables_query_reports_schema_error(self):
        for field in ("recipe_info", "last_good"):
            for invalid in (1, False, "bad"):
                with self.subTest(field=field, invalid=invalid):
                    data=passive_fixture();data["records"][100][field]=invalid
                    with self.assertRaises(CraftSimError): self.parse(data)

    def test_omitted_optional_queries_on_unsupported_row_are_allowed(self):
        data=passive_fixture();row=data["records"][100]
        for field in ("recipe_info", "schematic", "operation_info", "cached", "last_good"):
            row.pop(field)
        row["status"]="unsupported_data"
        parsed=self.parse(data)["passive"]
        self.assertEqual(parsed["unavailable_api_tables"],3)
        self.assertFalse(parsed["engine_profile"])


if __name__ == "__main__": unittest.main()
