import unittest

from wow_auction.knowledge_items import decode_tsm_item_info, ItemMetadataError


def cache(data, *, key="i:123", name="Synthetic item", version="19,enUS,12.1.5,70575"):
    return {"TSMItemInfoDB": {"versionStr": version, "names": name,
                             "itemStrings": key, "data": data}}


class ItemMetadataTests(unittest.TestCase):
    def test_little_endian_widths_nil_and_non_tradeability(self):
        # Independent literal fixture: 2*64+1, nil, 1*64^4+2*64+3, stack65.
        result = decode_tsm_item_info(cache("BC__DC AABBB".replace(" ", "")
                                           + "A" + "BAAAA" + "BCE" + "BA" + "_" + "L"),
                                      expected_build="12.1.5.70575")
        row = result["items"][0]
        self.assertEqual(row["fields"]["itemLevel"], 129)
        self.assertIsNone(row["fields"]["minLevel"])
        self.assertEqual(row["fields"]["vendorSell"], 64 ** 4 + 2 * 64 + 3)
        self.assertEqual(row["fields"]["maxStack"], 65)
        self.assertEqual(row["fields"]["isBOP"], 1)
        self.assertEqual(row["fields"]["craftedQuality"], 11)
        self.assertEqual(row["tradeability"], "unknown")
        self.assertIsNone(row["observed_at"])

    def test_all_nil_and_zero_are_distinct(self):
        unknown = decode_tsm_item_info(cache("_" * 24))["items"][0]
        zero = decode_tsm_item_info(cache("A" * 24))["items"][0]
        self.assertTrue(all(value is None for value in unknown["fields"].values()))
        self.assertTrue(all(value == 0 for value in zero["fields"].values()))
        self.assertEqual(zero["tradeability"], "unknown")

    def test_chunks_and_opaque_variants_preserved(self):
        data = cache("A" * 48)
        data["TSMItemInfoDB"].update(names={1: "A", 2: "B"},
            itemStrings={1: "i:123::2:4:5", 2: "p:333:25:4"})
        rows = decode_tsm_item_info(data)["items"]
        self.assertEqual([row["source_item_string"] for row in rows],
                         ["i:123::2:4:5", "p:333:25:4"])
        self.assertEqual(rows[1]["kind"], "battle_pet")

    def test_build_mismatch_stale_not_removed(self):
        result = decode_tsm_item_info(cache("A" * 24), expected_build="12.2.0.80000")
        self.assertEqual(result["status"], "stale_build")
        self.assertEqual(result["row_count"], 1)

    def test_empty_cache(self):
        result = decode_tsm_item_info({"TSMItemInfoDB": {"versionStr": "19,enUS,12.1.5,70575", "data": ""}})
        self.assertEqual(result["row_count"], 0)

    def test_invalid_contracts_fail_atomically(self):
        for values in (
            {"data": "A" * 23}, {"data": "A" * 23 + "!"},
            {"data": "A" * 20 + "C" + "AAA"},
            {"data": "A" * 48}, {"names": {2: "A"}},
            {"names": {True: "A"}}, {"names": "A\x00"},
            {"itemStrings": "i:0"}, {"itemStrings": "i:１２"},
            {"versionStr": "20,enUS,12.1.5,70575"},
            {"itemStrings": "i:123\x02i:123", "names": "A\x02B", "data": "A" * 48},
        ):
            with self.subTest(values=values):
                data = cache("A" * 24)
                data["TSMItemInfoDB"].update(values)
                with self.assertRaises(ItemMetadataError):
                    decode_tsm_item_info(data)

    def test_bounds_and_diagnostics(self):
        with self.assertRaises(ItemMetadataError) as error:
            decode_tsm_item_info(cache("A" * 24, key="SECRET"))
        self.assertNotIn("SECRET", str(error.exception))
        with self.assertRaises(ItemMetadataError):
            decode_tsm_item_info(cache("A" * 24), max_records=True)
        with self.assertRaises(ItemMetadataError):
            decode_tsm_item_info(cache("A" * 48), max_records=1)


if __name__ == "__main__":
    unittest.main()
