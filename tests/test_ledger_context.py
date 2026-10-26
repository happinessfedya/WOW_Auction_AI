from copy import deepcopy
import unittest

from wow_auction.ledger import HEADERS, LedgerError, normalize_ledger, reconcile_ledger
from wow_auction.ledger_context import (context_key, replay_context, state_context_matches,
                                        transition_context)


def batch(region=None, quantity=1, families=None, account="synthetic"):
    if families is None:
        families = {"csvBuys": [f"i:123,1,{quantity},100,Seller,Hero,1000,Auction"]}
    variables = {"TradeSkillMasterDB": {
        "r@Realm@internalData@" + family: "\n".join([HEADERS[family]] + rows)
        for family, rows in families.items()}}
    return normalize_ledger(variables, account_scope=account, region=region)


def initial(value):
    return reconcile_ledger([], value["rows"])["rows"]


class LedgerContextTests(unittest.TestCase):
    def test_same_context_uses_normal_reconciliation(self):
        old, new = batch("EU"), batch("EU", quantity=2)
        self.assertIsNone(transition_context(initial(old), old, new, same_raw=False))

    def test_none_to_region_same_bytes_preserves_identity_and_never_reemits_amount(self):
        old, new = batch(), batch("EU")
        state = initial(old)
        frozen = deepcopy(state), deepcopy(old), deepcopy(new)
        changes = transition_context(state, old, new, same_raw=True)
        self.assertEqual(changes["context_status"], "confirmed")
        self.assertEqual(changes["transition_kind"], "region_completed")
        self.assertEqual(changes["events"], [])
        row = changes["rows"][0]
        self.assertEqual(row["canonical_id"], state[0]["canonical_id"])
        self.assertEqual(row["revision"], state[0]["revision"] + 1)
        self.assertEqual(row["high_water"], 1)
        self.assertEqual(row["row_fingerprint"], new["rows"][0]["row_fingerprint"])
        self.assertEqual(row["row_state"], "present")
        self.assertFalse(row["uncertain"])
        repeated = reconcile_ledger(changes["rows"], new["rows"])
        self.assertEqual(repeated["events"], [])
        self.assertEqual(repeated["revisions"], [])
        self.assertEqual((state, old, new), frozen)

    def test_region_completion_retains_missing_family_and_tombstone_history(self):
        earlier = batch(families={"csvSales": ["i:123,1,1,150,Buyer,Hero,2000,Auction"]})
        removed = batch(families={"csvSales": []})
        state = reconcile_ledger(initial(earlier), removed["rows"])["rows"]
        old, new = batch(), batch("EU")
        state += initial(old)
        changes = transition_context(state, old, new, same_raw=True)
        self.assertEqual(len(changes["rows"]), 2)
        sale = next(row for row in changes["rows"] if row["family"] == "csvSales")
        self.assertEqual(sale["row_state"], "missing")
        self.assertEqual(sale["region"], "EU")
        self.assertEqual(changes["events"], [])

    def test_known_region_change_is_one_conflicted_projection_without_events(self):
        old, new = batch("EU"), batch("US")
        changes = transition_context(initial(old), old, new, same_raw=True)
        self.assertEqual(changes["context_status"], "requires_resolution")
        self.assertEqual(changes["events"], [])
        self.assertEqual(len(changes["rows"]), 1)
        row = changes["rows"][0]
        self.assertEqual(row["region"], "US")
        self.assertEqual(row["row_state"], "context_conflict")
        self.assertTrue(row["uncertain"])
        self.assertNotEqual(row["canonical_id"], initial(old)[0]["canonical_id"])

    def test_revert_and_new_bytes_cannot_implicitly_resolve_conflict(self):
        eu, us = batch("EU"), batch("US")
        first = transition_context(initial(eu), eu, us, same_raw=True)
        repeated = transition_context(first["rows"], us, us, same_raw=True, unresolved=True)
        self.assertEqual(repeated["revisions"], [])
        self.assertEqual(repeated["rows"], first["rows"])
        changed = batch("US", quantity=2)
        grown = transition_context(repeated["rows"], us, changed, same_raw=False, unresolved=True)
        self.assertEqual(grown["events"], [])
        self.assertEqual(grown["rows"][0]["quantity"], 2)
        back = transition_context(grown["rows"], changed, eu, same_raw=False, unresolved=True)
        self.assertEqual(back["context_status"], "requires_resolution")
        self.assertEqual(back["events"], [])
        self.assertEqual(len(back["rows"]), 1)

    def test_completion_requires_same_bytes_and_observations_and_account(self):
        old = batch()
        for new, same_raw in ((batch("EU"), False), (batch("EU", quantity=2), True),
                              (batch("EU", account="another"), True)):
            with self.subTest(context=context_key(new), same_raw=same_raw):
                result = transition_context(initial(old), old, new, same_raw=same_raw)
                self.assertEqual(result["context_status"], "requires_resolution")
                self.assertEqual(result["events"], [])

    def test_legacy_mixed_projection_is_detected_even_with_unchanged_context(self):
        old, current = batch(), batch("EU")
        damaged = initial(old) + initial(current)
        self.assertFalse(state_context_matches(damaged, current))
        result = transition_context(damaged, current, current, same_raw=True)
        self.assertEqual(result["context_status"], "requires_resolution")
        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(state_context_matches(result["rows"], current))
        self.assertEqual(result["events"], [])

    def test_empty_conflict_keeps_explicit_status(self):
        eu, us = batch("EU", families={"csvBuys": []}), batch("US", families={"csvBuys": []})
        result = transition_context([], eu, us, same_raw=True)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["context_status"], "requires_resolution")

    def test_explicit_replay_produces_one_book_without_financial_events(self):
        # Raw snapshots were explicitly re-normalized in the confirmed target.
        result = replay_context([batch("EU"), batch("EU"), batch("EU", quantity=2)])
        self.assertEqual(result["context_status"], "confirmed")
        self.assertEqual(result["events"], [])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["quantity"], 2)
        self.assertEqual(result["rows"][0]["high_water"], 2)
        self.assertTrue(all(r["kind"] == "identity_context_rebuilt" for r in result["revisions"]))

    def test_replay_preserves_missing_families_and_real_uncertainty(self):
        result = replay_context([batch("EU", quantity=2), batch("EU"),
                                 batch("EU", families={"csvExpense": ["Repair Bill,20,Merchant,Hero,2000"]})])
        buy = next(r for r in result["rows"] if r["family"] == "csvBuys")
        self.assertEqual(buy["quantity"], 1)
        self.assertEqual(buy["high_water"], 2)
        self.assertTrue(buy["uncertain"])
        self.assertEqual(result["events"], [])

    def test_replay_rejects_unconfirmed_mixed_targets_or_empty_history(self):
        for batches in ([], [batch("EU"), batch("US")], [batch("EU"), batch("EU", account="different")]):
            with self.subTest(count=len(batches)):
                with self.assertRaises(LedgerError):
                    replay_context(batches)


if __name__ == "__main__":
    unittest.main()
