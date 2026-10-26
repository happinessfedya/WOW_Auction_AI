"""Synthetic Ledger tests, deliberately not a claim of a real populated import."""

from copy import deepcopy
import unittest

from wow_auction.ledger import HEADERS, LedgerError, normalize_ledger, reconcile_ledger


def snapshot(families=None, *, realm="SyntheticRealm", account="synthetic-account", region=None):
    families = families or {}
    db = {f"r@{realm}@internalData@{family}": header + ("\n" + "\n".join(families[family]) if families.get(family) else "")
          for family, header in HEADERS.items()}
    db.update({f"r@internalData@{family}": 123 for family in HEADERS})
    return normalize_ledger({"TradeSkillMasterDB": db}, account_scope=account, region=region)


def sale(quantity=2, *, price="101", item="i:123", player="SyntheticPlayer", timestamp="1000", method="Auction"):
    return f"{item},1,{quantity},{price},SyntheticCounterparty,{player},{timestamp},{method}"


class NormalizeLedgerTests(unittest.TestCase):
    def test_six_empty_headers_are_complete_metadata_not_records(self):
        result = snapshot()
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["scopes"]), 6)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["warnings"], [])

    def test_missing_family_differs_from_empty(self):
        result = normalize_ledger({"TradeSkillMasterDB": {"r@R@internalData@csvSales": HEADERS["csvSales"]}}, account_scope="A")
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["missing_families"]), 5)
        self.assertIn("missing_ledger_families", result["warnings"])

    def test_missing_database_is_failure(self):
        with self.assertRaisesRegex(LedgerError, "missing_tsm_database"):
            normalize_ledger({}, account_scope="A")

    def test_actual_sale_net_unit_no_second_ah_cut(self):
        row = snapshot({"csvSales": [sale(quantity=3, price="100")]})["rows"][0]
        self.assertTrue(row["ah_cut_already_applied"])
        self.assertEqual(row["amount_copper"], 300)
        self.assertEqual(row["price_semantics"], "net_after_ah_cut_unit_rounded")
        self.assertEqual((row["total_lower_copper"], row["total_upper_copper" ]), (299, 301))
        self.assertIsNone(row["lost_deposit_copper"])
        self.assertIsNone(row["pnl_copper"])
        self.assertFalse(row["profit_eligible"])

    def test_rounding_bounds_cover_all_small_integer_originals(self):
        for quantity in range(1, 8):
            for total in range(0, 80):
                rounded_unit = (2 * total + quantity) // (2 * quantity)
                row = snapshot({"csvSales": [sale(quantity, price=str(rounded_unit))]})["rows"][0]
                self.assertLessEqual(row["total_lower_copper"], total)
                self.assertGreaterEqual(row["total_upper_copper"], total)

    def test_purchase_is_not_net_sale(self):
        row = snapshot({"csvBuys": [sale(quantity=2)]})["rows"][0]
        self.assertEqual(row["classification"], "purchase")
        self.assertFalse(row["ah_cut_already_applied"])
        self.assertEqual(row["price_semantics"], "payment_unit_rounded")
        self.assertIsNone(row["total_lower_copper"])
        self.assertEqual(row["rounding_bounds_status"], "unknown_original_invoice_allocation")

    def test_split_cod_invoice_does_not_claim_false_additive_bounds(self):
        # Original invoice: 201 items for 301 copper, rounded unit = 1.
        # TSM splits into max-stack 200 and remainder 1 AFTER rounding.
        # Applying per-row integer invoice bounds would exclude the real 301.
        result = snapshot({"csvBuys": ["i:123,200,200,1,Other,Player,1000,COD",
                                       "i:123,1,1,1,Other,Player,1000,COD"]})
        self.assertEqual(sum(r["total_estimate_copper"] for r in result["rows"]), 201)
        self.assertTrue(all(r["total_lower_copper"] is None and r["total_upper_copper"] is None
                            for r in result["rows"]))
        self.assertTrue(all(r["rounding_bounds_status"] == "unknown_original_invoice_allocation"
                            for r in result["rows"]))
        result_sale = snapshot({"csvSales": ["i:123,200,200,1,Other,Player,1000,COD",
                                             "i:123,1,1,1,Other,Player,1000,COD"]})
        self.assertTrue(all(r["total_upper_copper"] is None for r in result_sale["rows"]))

    def test_vendor_buy_batch_denominator_is_not_assumed(self):
        row = snapshot({"csvBuys": [sale(2, price="101", method="Vendor")]})["rows"][0]
        self.assertEqual(row["price_semantics"], "vendor_reported_unit")
        self.assertEqual(row["rounding_bounds_status"], "unknown_vendor_batch_allocation")
        self.assertIsNone(row["total_lower_copper"])

    def test_transfers_and_refund_candidates_are_not_profit(self):
        result = snapshot({"csvIncome": ["Money Transfer,500,Other,Player,1000", "Deposit Refund,12,Other,Player,1000"],
                           "csvExpense": ["Money Transfer,500,Other,Player,1000"]})
        self.assertEqual({r["classification"] for r in result["rows"]}, {"unreconciled_transfer", "unverified_refund"})
        self.assertTrue(all(r["pnl_copper"] is None and not r["profit_eligible"] for r in result["rows"]))

    def test_expired_cancelled_do_not_invent_deposit(self):
        result = snapshot({"csvExpired": ["i:12,1,3,Player,1000"], "csvCancelled": ["i:12,1,1,Player,1001"]})
        self.assertTrue(all(r["amount_copper"] is None and r["lost_deposit_copper"] is None for r in result["rows"]))

    def test_event_time_estimate_never_receipt_time(self):
        row = snapshot({"csvSales": [sale(timestamp="1000.75")]})["rows"][0]
        self.assertEqual(row["event_at"], 1000)
        self.assertEqual(row["reported_time_decimal"], "1000.75")
        self.assertEqual(row["event_time_semantics"], "mail_derived_event_estimate")
        self.assertNotIn("available_at", row)

    def test_crafting_orders_and_returned_listings_have_mail_time_estimates(self):
        result = snapshot({"csvIncome": ["Crafting Order,100,Other,Player,1000"],
                           "csvExpense": ["Crafting Order,200,Other,Player,1000"],
                           "csvExpired": ["i:123,1,2,Player,1000"],
                           "csvCancelled": ["i:123,1,2,Player,1000"]})
        self.assertEqual(len(result["rows"]), 4)
        self.assertTrue(all(r["event_time_semantics"] == "mail_derived_event_estimate" for r in result["rows"]))

    def test_fractional_trade_unit_preserved_and_floor_bounds(self):
        result = snapshot({"csvSales": [sale(3, price="100.333333333", method="Trade")]})
        row = result["rows"][0]
        self.assertEqual(row["unit_amount_copper"], 100)
        self.assertEqual(row["reported_unit_amount_decimal"], "100.333333333")
        self.assertEqual((row["total_lower_copper"], row["total_upper_copper"]), (300, 302))
        self.assertIn("fractional_unit_price_preserved", result["warnings"])

    def test_unknown_method_preserves_uncertainty(self):
        result = snapshot({"csvSales": [sale(method="UnknownSource")]})
        row = result["rows"][0]
        self.assertIsNone(row["total_lower_copper"])
        self.assertIn("unverified_price_semantics", result["warnings"])

    def test_variants_and_pets_remain_distinct(self):
        result = snapshot({"csvSales": [sale(item="i:123:4:5"), sale(item="i:123:4:6"), sale(item="p:39:25:3")]})
        self.assertEqual(len({r["match_fingerprint"] for r in result["rows"]}), 3)
        self.assertEqual({r["item_string"] for r in result["rows"]}, {"i:123:4:5", "i:123:4:6", "p:39:25:3"})

    def test_duplicates_preserve_multiplicity(self):
        result = snapshot({"csvSales": [sale(), sale()]})
        self.assertEqual([r["occurrence"] for r in result["rows"]], [1, 2])
        self.assertEqual(len({r["row_fingerprint"] for r in result["rows"]}), 1)

    def test_bad_header_and_rows_fail_whole_snapshot(self):
        for content in ("item,price\ni:1,2", HEADERS["csvSales"] + "\n" + sale() + ",extra", HEADERS["csvSales"] + "\n"):
            with self.subTest(case=len(content)), self.assertRaises(LedgerError):
                normalize_ledger({"TradeSkillMasterDB": {"r@R@internalData@csvSales": content}}, account_scope="A")

    def test_nonintegral_quantity_and_malformed_number_rejected(self):
        for quantity, price in (("1.5", "100"), ("0", "100"), ("-1", "100"), ("2", "NaN"), ("2", "1e999999")):
            with self.subTest(quantity=quantity, price=price), self.assertRaises(LedgerError):
                snapshot({"csvSales": [sale(quantity, price=price)]})

    def test_errors_do_not_echo_personal_data(self):
        secret = "PrivateSyntheticName"
        try:
            snapshot({"csvSales": [sale(player=secret, price="invalid")]})
        except LedgerError as exc:
            self.assertNotIn(secret, str(exc))
        else:
            self.fail("Expected validation error")


class ReconcileLedgerTests(unittest.TestCase):
    @staticmethod
    def rows(*lines, family="csvSales"):
        return snapshot({family: list(lines)})["rows"]

    def test_reimport_no_duplicate_events_or_revisions(self):
        rows = self.rows(sale())
        first = reconcile_ledger([], rows)
        again = reconcile_ledger(first["rows"], rows)
        self.assertEqual(again["events"], [])
        self.assertEqual(again["revisions"], [])

    def test_quantity_two_to_three_is_delta_one(self):
        first = reconcile_ledger([], self.rows(sale(2)))
        next_ = reconcile_ledger(first["rows"], self.rows(sale(3)))
        self.assertEqual(len(next_["events"]), 1)
        self.assertEqual(next_["events"][0]["quantity_delta"], 1)
        self.assertEqual(next_["events"][0]["amount_delta_copper"], 101)
        self.assertEqual(next_["events"][0]["kind"], "aggregate_growth")
        self.assertEqual(first["rows"][0]["canonical_id"], next_["rows"][0]["canonical_id"])

    def test_money_amount_is_mutable_aggregate(self):
        first = reconcile_ledger([], self.rows("Postage,30,Other,Player,1000", family="csvExpense"))
        next_ = reconcile_ledger(first["rows"], self.rows("Postage,60,Other,Player,1000", family="csvExpense"))
        self.assertEqual(next_["events"][0]["amount_delta_copper"], 30)
        self.assertIsNone(next_["events"][0]["quantity_delta"])

    def test_identical_sales_multiplicity_and_reorder(self):
        first = reconcile_ledger([], self.rows(sale(), sale(), sale(item="i:456")))
        self.assertEqual(len(first["events"]), 3)
        self.assertEqual(len({r["canonical_id"] for r in first["rows"]}), 3)
        again = reconcile_ledger(first["rows"], self.rows(sale(item="i:456"), sale(), sale()))
        self.assertEqual(again["events"], [])
        self.assertEqual(again["revisions"], [])

    def test_duplicate_multiplicity_increase_is_one_new_observation(self):
        first = reconcile_ledger([], self.rows(sale(), sale()))
        next_ = reconcile_ledger(first["rows"], self.rows(sale(), sale(), sale()))
        self.assertEqual(len(next_["events"]), 1)
        self.assertEqual(next_["events"][0]["quantity_delta"], 2)

    def test_trim_and_restore_not_negative_or_new_trade(self):
        first = reconcile_ledger([], self.rows(sale()))
        trim = reconcile_ledger(first["rows"], [])
        self.assertEqual(trim["events"], [])
        self.assertEqual(trim["rows"][0]["row_state"], "missing")
        self.assertEqual(reconcile_ledger(trim["rows"], [])["revisions"], [])
        restore = reconcile_ledger(trim["rows"], self.rows(sale()))
        self.assertEqual(restore["events"], [])
        self.assertEqual(restore["rows"][0]["canonical_id"], first["rows"][0]["canonical_id"])

    def test_ambiguous_growth_does_not_guess_old_origin(self):
        first = reconcile_ledger([], self.rows(sale(2), sale(2)))
        next_ = reconcile_ledger(first["rows"], self.rows(sale(2), sale(3)))
        self.assertEqual(next_["events"], [])
        self.assertIn("ambiguous_aggregate_matching", next_["warnings"])
        self.assertTrue(any(r["uncertain"] for r in next_["rows"]))
        again = reconcile_ledger(next_["rows"], self.rows(sale(2), sale(3)))
        self.assertEqual(again["events"], [])

    def test_many_identical_records_keep_multiplicity_on_ambiguous_change(self):
        first = reconcile_ledger([], self.rows(*([sale(2)] * 3000)))
        changed = reconcile_ledger(first["rows"], self.rows(*([sale(3)] * 3000)))
        self.assertEqual(changed["events"], [])
        self.assertEqual(sum(r["row_state"] == "present" for r in changed["rows"]), 3000)
        self.assertEqual(sum(r["row_state"] == "missing" for r in changed["rows"]), 3000)

    def test_decrease_and_regrowth_does_not_recount(self):
        first = reconcile_ledger([], self.rows(sale(3)))
        decreased = reconcile_ledger(first["rows"], self.rows(sale(2)))
        self.assertEqual(decreased["events"], [])
        self.assertTrue(decreased["rows"][0]["uncertain"])
        restored = reconcile_ledger(decreased["rows"], self.rows(sale(3)))
        self.assertEqual(restored["events"], [])
        self.assertEqual(restored["rows"][0]["high_water"], 3)

    def test_missing_rows_becoming_uncertain_have_a_revision(self):
        first = reconcile_ledger([], self.rows(sale(2), sale(3)))
        trimmed = reconcile_ledger(first["rows"], [])
        changed = reconcile_ledger(trimmed["rows"], self.rows(sale(4)))
        historical = {r["canonical_id"] for r in trimmed["rows"]}
        revisions = {r["canonical_id"] for r in changed["revisions"]}
        self.assertTrue(historical.issubset(revisions))
        self.assertTrue(all(r["uncertain"] for r in changed["rows"]))

    def test_trade_price_floor_on_tsm_reload_not_new_trade(self):
        first = reconcile_ledger([], self.rows(sale(3, price="100.333333", method="Trade")))
        next_ = reconcile_ledger(first["rows"], self.rows(sale(3, price="100", method="Trade")))
        self.assertEqual(next_["events"], [])
        self.assertEqual(next_["revisions"][0]["kind"], "source_revision")
        self.assertEqual(len(next_["rows"]), 1)

    def test_separate_accounts_and_realms_never_merge(self):
        row_a = snapshot({"csvSales": [sale()]}, account="A")["rows"]
        row_b = snapshot({"csvSales": [sale()]}, account="B")["rows"]
        self.assertNotEqual(row_a[0]["match_fingerprint"], row_b[0]["match_fingerprint"])
        result = reconcile_ledger([], row_a + row_b)
        self.assertEqual(len(result["rows"]), 2)

    def test_same_realm_label_in_different_regions_remains_distinct(self):
        eu = snapshot({"csvSales": [sale()]}, region="EU")
        us = snapshot({"csvSales": [sale()]}, region="US")
        unknown = snapshot({"csvSales": [sale()]})
        self.assertEqual(eu["region"], "EU")
        self.assertTrue(all(s["region"] == "US" for s in us["scopes"]))
        self.assertIsNone(unknown["region"])
        combined = eu["rows"] + us["rows"] + unknown["rows"]
        self.assertEqual(len({r["match_fingerprint"] for r in combined}), 3)
        self.assertEqual(len(reconcile_ledger([], combined)["rows"]), 3)
        with self.assertRaisesRegex(LedgerError, "unsupported_region"):
            snapshot(region="not-a-region")

    def test_no_fuzzy_300_second_dedup(self):
        first = reconcile_ledger([], self.rows(sale(timestamp="1000")))
        next_ = reconcile_ledger(first["rows"], self.rows(sale(timestamp="1000"), sale(timestamp="1001")))
        self.assertEqual(len(next_["events"]), 1)

    def test_inputs_not_mutated(self):
        initial = self.rows(sale(2))
        copy = deepcopy(initial)
        first = reconcile_ledger([], initial)
        before = deepcopy(first)
        reconcile_ledger(first["rows"], self.rows(sale(3)))
        self.assertEqual(initial, copy)
        self.assertEqual(first, before)


if __name__ == "__main__":
    unittest.main()
