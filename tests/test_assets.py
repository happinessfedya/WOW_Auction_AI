"""Synthetic contract tests; no real account identifiers or captured SV data."""

from copy import deepcopy
import unittest

from wow_auction.assets import AssetError, normalize_assets, reconcile_assets


def gx(gold=123_456, bank=0, seen=200, *, scope="fixture-a", group=None, region="EU"):
    return normalize_assets("GX", {"GX_DB": {
        "version": 1, "characters": {"Ada-TestRealm": {"gold": gold, "lastSeen": seen}},
        "warband": {"gold": bank, "lastSeen": seen}}},
        account_scope=scope, region=region, ownership_group=group)


def bag(money=123_456, bank=0, *, scope="fixture-a", group=None, region="EU", extra=None):
    char = {"guid": "Player-fixture-1", "money": money, "bag": {0: {1: "100;2"}},
            "bank": {}, "equip": {1: "200"}, "mailbox": {}}
    if extra:
        char.update(extra)
    return normalize_assets("BagSync", {"BagSyncDB": {
        "Test Realm": {"Ada": char}, "warband\u00a7": {"money": bank, "tabs": {1: {}}}}},
        account_scope=scope, region=region, ownership_group=group)


class AssetTests(unittest.TestCase):
    def test_matching_sources_not_added_and_copper_not_gold(self):
        result = reconcile_assets([gx(), bag()])
        self.assertEqual(len(result["cash"]), 2)
        self.assertEqual(result["observed_cash_copper"], 123_456)
        self.assertTrue(all(row["source"] == "GX" for row in result["cash"]))
        self.assertEqual(result["cash_status"], "mixed_time_observations")
        self.assertIsNone(result["current_nav_copper"])

    def test_default_gx_warband_zero_is_unknown(self):
        batch = gx(seen=0)
        self.assertEqual(batch["cash"][1]["reported_copper"], 0)
        self.assertIsNone(batch["cash"][1]["copper"])
        self.assertIsNone(reconcile_assets([batch])["observed_cash_copper"])
        self.assertEqual(gx(bank=0)["cash"][1]["copper"], 0)

    def test_missing_cash_is_not_zero(self):
        batch = normalize_assets("GX", {"GX_DB": {"version": 1, "characters": {}}}, account_scope="fixture")
        self.assertIsNone(batch["cash"][0]["copper"])
        self.assertEqual(reconcile_assets([batch])["cash_status"], "incomplete")

    def test_bagsync_new_file_time_cannot_override_gx(self):
        gx_batch, bag_batch = gx(gold=100, seen=20), bag(money=200)
        bag_batch["file_written_at"] = 50000
        result = reconcile_assets([bag_batch, gx_batch])
        char = next(row for row in result["cash"] if row["owner_kind"] == "character")
        self.assertEqual(char["copper"], 100)
        self.assertEqual(char["observed_at"], 20)
        self.assertTrue(char["conflict"])
        self.assertEqual(len(char["alternatives"]), 2)
        self.assertIn("cash_disagreement", [row["type"] for row in result["conflicts"]])

    def test_unknown_ownership_two_folders_does_not_sum_warbank(self):
        result = reconcile_assets([gx(scope="a", bank=100), gx(scope="b", bank=100)])
        self.assertIsNone(result["observed_cash_copper"])
        self.assertIn("cross_account_warband_ownership_unknown", [row["type"] for row in result["conflicts"]])

    def test_explicit_ownership_group_deduplicates_warbank(self):
        result = reconcile_assets([gx(scope="a", group="owned", bank=100),
                                   gx(scope="b", group="owned", bank=100)])
        banks = [r for r in result["cash"] if r["owner_kind"] == "warband"]
        self.assertEqual(len(banks), 1)
        self.assertEqual(banks[0]["copper"], 100)
        self.assertEqual(len(banks[0]["alternatives"]), 2)

    def test_cross_license_name_only_character_cannot_double_cash(self):
        result = reconcile_assets([gx(scope="a", group="owned"), gx(scope="b", group="owned")])
        self.assertIsNone(result["observed_cash_copper"])
        self.assertIn("ambiguous_cross_account_character_alias", [r["type"] for r in result["conflicts"]])

    def test_same_guid_across_licenses_deduplicates_character(self):
        batches = [gx(scope="a", group="owned"), bag(scope="a", group="owned"),
                   gx(scope="b", group="owned"), bag(scope="b", group="owned")]
        result = reconcile_assets(batches)
        self.assertEqual(len(result["cash"]), 2)
        self.assertEqual(result["observed_cash_copper"], 123_456)
        self.assertEqual(result["conflicts"], [])

    def test_same_guid_and_warband_inventory_copies_are_not_added(self):
        batches = [bag(scope="a", group="owned"), bag(scope="b", group="owned")]
        result = reconcile_assets(batches)
        self.assertEqual(len(result["inventory_observations"]), 4)
        self.assertEqual(len(result["items"]), 2)
        banks = [r for r in result["containers"] if r["kind"] == "warband"]
        self.assertEqual(len(banks), 1)
        self.assertEqual(len(banks[0]["alternatives"]), 2)

    def test_conflicting_untimed_inventory_has_no_selected_sum(self):
        batches = [bag(scope="a", group="owned"),
                   bag(scope="b", group="owned", extra={"bag": {0: {1: "100;4"}}})]
        result = reconcile_assets(batches)
        self.assertEqual(len([i for i in result["items"] if i["kind"] == "bag"]), 0)
        self.assertIn("inventory_disagreement", [c["type"] for c in result["conflicts"]])
        self.assertEqual(len(result["inventory_observations"]), 4)

    def test_duplicate_inventory_reorder_is_same_multiset_not_slot_move(self):
        batches = [bag(scope="a", group="owned", extra={"bag": {0: {1: "100;2", 2: "101"}}}),
                   bag(scope="b", group="owned", extra={"bag": {0: {1: "101", 2: "100;2"}}})]
        result = reconcile_assets(batches)
        self.assertEqual(len([i for i in result["items"] if i["kind"] == "bag"]), 2)
        self.assertNotIn("inventory_disagreement", [c["type"] for c in result["conflicts"]])

    def test_same_guid_conflicting_same_time_character_money_is_unknown(self):
        batches = [gx(scope="a", group="owned", gold=100), bag(scope="a", group="owned", money=100),
                   gx(scope="b", group="owned", gold=200), bag(scope="b", group="owned", money=200)]
        result = reconcile_assets(batches)
        char = next(r for r in result["cash"] if r["owner_kind"] == "character")
        self.assertIsNone(char["copper"])
        self.assertIsNone(result["observed_cash_copper"])

    def test_explicitly_separate_ownership_groups_remain_separate(self):
        result = reconcile_assets([gx(scope="a", group="owned-a", bank=100),
                                   gx(scope="b", group="owned-b", bank=200)])
        self.assertEqual(len([r for r in result["cash"] if r["owner_kind"] == "warband"]), 2)

    def test_equal_time_conflicting_warbanks_no_arbitrary_winner(self):
        result = reconcile_assets([gx(scope="a", group="owned", bank=100),
                                   gx(scope="b", group="owned", bank=200)])
        bank_row = next(r for r in result["cash"] if r["owner_kind"] == "warband")
        self.assertIsNone(bank_row["copper"])
        self.assertIsNone(result["observed_cash_copper"])

    def test_later_warbank_observation_selected_without_resetting_other_times(self):
        result = reconcile_assets([gx(scope="a", group="owned", bank=100, seen=200),
                                   gx(scope="b", group="owned", bank=200, seen=300)])
        row = next(r for r in result["cash"] if r["owner_kind"] == "warband")
        self.assertEqual((row["copper"], row["observed_at"]), (200, 300))
        self.assertTrue(row["conflict"])

    def test_conflicting_group_configuration_rejected(self):
        with self.assertRaisesRegex(AssetError, "inconsistent_ownership"):
            reconcile_assets([gx(group="a"), bag(group="b")])

    def test_alias_collision_does_not_merge_guid_owners_or_double_cash(self):
        batch = normalize_assets("BagSync", {"BagSyncDB": {
            "Test Realm": {"Ada": {"guid": "Player-A", "money": 1}},
            "TestRealm": {"Ada": {"guid": "Player-B", "money": 2}}}},
            account_scope="fixture-a", region="EU")
        result = reconcile_assets([gx(), batch])
        self.assertIsNone(result["observed_cash_copper"])
        self.assertIn("ambiguous_character_alias", [r["type"] for r in result["conflicts"]])

    def test_gx_alias_collision_without_guid_is_not_added(self):
        batch = normalize_assets("GX", {"GX_DB": {"version": 1, "characters": {
            "Ada-Test Realm": {"gold": 1, "lastSeen": 10},
            "Ada-TestRealm": {"gold": 2, "lastSeen": 20}},
            "warband": {"gold": 0, "lastSeen": 10}}}, account_scope="fixture", region="EU")
        result = reconcile_assets([batch])
        self.assertIsNone(result["observed_cash_copper"])

    def test_region_is_part_of_identity_and_ownership(self):
        result = reconcile_assets([gx(region="EU", group="owned"), gx(region="US", group="owned")])
        self.assertEqual(len(result["cash"]), 4)

    def test_missing_and_saved_empty_containers_are_different(self):
        batch = bag()
        bank = next(c for c in batch["containers"] if c["kind"] == "bank")
        auction = next(c for c in batch["containers"] if c["kind"] == "auction")
        self.assertEqual(bank["observation_status"], "source_empty")
        self.assertEqual(auction["observation_status"], "missing")
        self.assertIsNone(bank["complete"])
        self.assertIsNone(bank["observed_at"])

    def test_inventory_variants_and_pet_options_not_collapsed(self):
        batch = bag(extra={"bag": {0: {
            1: "123::::::::::::1:9;2", 2: "123::::::::::::1:10;3",
            3: "10024500000;1;petdata=245:12:4:1500:200:190",
            4: "10024500000;1;petdata=245:25:4:1500:200:190"}}})
        bag_items = [x for x in batch["items"] if x["kind"] == "bag"]
        self.assertEqual(len({x["variant_key"] for x in bag_items}), 4)
        self.assertEqual(bag_items[2]["pet"]["species_id"], 245)
        self.assertIsNone(bag_items[2]["item_id"])
        self.assertTrue(all(x["identity_completeness"] == "lossy_source" for x in bag_items))
        self.assertTrue(all(not x["eligible_liquid_asset"] for x in bag_items))

    def test_equipment_excluded_and_keystone_not_liquid(self):
        batch = bag(extra={"bag": {0: {1: "180653;1;keystone=507:10:1:2:3:4"}}})
        self.assertEqual(next(x for x in batch["items"] if x["kind"] == "equip")["tradeability"], "excluded_equipment")
        self.assertEqual(next(x for x in batch["items"] if x["kind"] == "bag")["tradeability"], "excluded_keystone")

    def test_auction_expiry_not_variant_or_sale(self):
        batch = bag(extra={"auction": {"bag": {1: "100;2;auction=1000"}, "count": 1, "lastscan": 500}})
        items = [x for x in batch["items"] if x["item_id"] == 100]
        self.assertEqual(len({x["variant_key"] for x in items}), 1)
        auction = next(x for x in items if x["kind"] == "auction")
        self.assertEqual((auction["auction_expires_at"], auction["observed_at"]), (1000, 500))
        result = reconcile_assets([batch])
        self.assertIsNone(result["inventory_current_total"])
        self.assertGreater(result["possible_stale_overlap_variants"], 0)

    def test_saved_overlapping_locations_never_become_exact_inventory(self):
        result = reconcile_assets([bag(extra={"bank": {1: {1: "100;2"}}})])
        self.assertIsNone(result["inventory_current_total"])
        self.assertEqual(result["possible_stale_overlap_variants"], 1)

    def test_guild_and_settings_excluded_from_personal_cash(self):
        batch = normalize_assets("BagSync", {"BagSyncDB": {
            "options\u00a7": {"money": 999},
            "Test Realm": {"Guild\u00a9": {"money": 50000, "tabs": {}},
                           "UnmarkedGuild": {"money": 50000, "tabs": {}},
                           "Ada": {"guid": "Player-A", "money": 10}},
            "warband\u00a7": {"money": 0}}}, account_scope="fixture")
        self.assertEqual(len(batch["characters"]), 1)
        self.assertEqual(reconcile_assets([batch])["observed_cash_copper"], 10)
        self.assertIn("guild_assets_excluded", batch["warnings"])

    def test_mutable_inputs_are_not_modified_by_reconciliation(self):
        batches = [gx(), bag()]
        before = deepcopy(batches)
        reconcile_assets(batches)
        self.assertEqual(batches, before)

    def test_history_must_be_selected_before_reconciliation(self):
        with self.assertRaisesRegex(AssetError, "latest_snapshots_only"):
            reconcile_assets([gx(), gx(seen=300)])

    def test_invalid_schema_negative_bool_and_fractional_money_fail_closed(self):
        for value in (-1, True, 1.5, "100", 2**70):
            with self.subTest(value=type(value).__name__), self.assertRaises(AssetError):
                gx(gold=value)
        for value in (False, True, -1, "200"):
            with self.subTest(timestamp=type(value).__name__), self.assertRaises(AssetError):
                gx(seen=value)
        with self.assertRaisesRegex(AssetError, "unsupported_gx_version"):
            normalize_assets("GX", {"GX_DB": {"version": 2, "characters": {}}}, account_scope="a")
        with self.assertRaisesRegex(AssetError, "missing_or_unsupported_source_root"):
            normalize_assets("BagSync", {}, account_scope="a")

    def test_invalid_item_counts_do_not_get_replaced_with_one(self):
        for raw in ("100;0", "100;-1", "100;many", "100;1;auction=x", "100;1;petdata=a|petdata=b"):
            with self.subTest(raw=raw), self.assertRaises(AssetError):
                bag(extra={"bag": {0: {1: raw}}})

    def test_craftsim_empty_cache_is_not_zero_profit(self):
        batch = normalize_assets("CraftSim", {"CraftSimDB": {
            "lastCraftingCostDB": {"version": 1, "data": {}},
            "crafterDB": {"version": 4, "data": {"fixture": {}}}}}, account_scope="fixture")
        cs = batch["craftsim"]
        self.assertFalse(cs["engine_evaluation_available"])
        self.assertEqual(cs["calculation_status"], "missing_data")
        self.assertEqual(cs["last_crafting_costs"], [])
        self.assertEqual(batch["cash"], [])
        self.assertIsNone(reconcile_assets([batch])["observed_cash_copper"])

    def test_craftsim_cost_is_preserved_as_cached_model_decimal(self):
        batch = normalize_assets("CraftSim", {"CraftSimDB": {
            "lastCraftingCostDB": {"version": 1, "data": {
                "123:2": {"fixture": {"cost": 12.5, "timestamp": 100}}}}}}, account_scope="fixture")
        cost = batch["craftsim"]["last_crafting_costs"][0]
        self.assertEqual(cost["expected_cost_copper_decimal"], "12.5")
        self.assertEqual(cost["observed_at"], 100)
        self.assertEqual(cost["provenance"], "cached_model_estimate")
        self.assertFalse(batch["craftsim"]["engine_evaluation_available"])

    def test_craftsim_repository_version_is_scalar_integer_or_unknown(self):
        for version in ({1: "a", "key": "b"}, "1", 1.0, True, -1):
            with self.subTest(version=type(version).__name__), self.assertRaises(AssetError):
                normalize_assets("CraftSim", {"CraftSimDB": {
                    "crafterDB": {"version": version, "data": {}}}}, account_scope="fixture")
        batch = normalize_assets("CraftSim", {"CraftSimDB": {
            "crafterDB": {"data": {}}}}, account_scope="fixture")
        self.assertIsNone(batch["craftsim"]["repositories"][0]["version"])


if __name__ == "__main__":
    unittest.main()
