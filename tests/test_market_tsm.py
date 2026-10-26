"""Synthetic public-CSV contracts; real captures stay outside the repository."""

import csv
import io
import unittest
from unittest.mock import patch

from wow_auction.market_common import MarketError
from wow_auction.market_tsm import build_url, normalize_tsm


def catalogue(*, pets=False, regional=False, rows=None):
    identifier = 'petSpeciesId' if pets else 'itemId'
    header = [identifier, 'name', 'marketValue'] + (
        ['historical', 'avgSalePrice', 'saleRate', 'soldPerDay', 'updatedAt'] if regional else
        ['minBuyout', 'recent', 'historical', 'updatedAt'])
    defaults = ([123, 'Synthetic item', 100, 90, 95, '0.25', '1234567.125', '2026-10-26T09:00:00Z']
                if regional else [123, 'Synthetic item', 100, 80, 85, 90, '2026-10-26T09:00:00Z'])
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows([defaults] if rows is None else rows)
    return output.getvalue().encode('utf-8')


class TSMMarketTests(unittest.TestCase):
    def test_documented_urls_and_regions_are_configurable(self):
        self.assertEqual(build_url(region='EU', dataset='commodities'),
                         'https://public-data.tradeskillmaster.com/retail/eu/commodities.csv')
        self.assertEqual(build_url(region='us', dataset='realm_pets', realm_slug='area-52'),
                         'https://public-data.tradeskillmaster.com/retail/us/realm/area-52/pets.csv')
        self.assertEqual(build_url(region='KR', dataset='region_items'),
                         'https://public-data.tradeskillmaster.com/retail/kr/region/items.csv')
        for options in ({'region': 'CN', 'dataset': 'commodities'},
                        {'region': 'EU', 'dataset': 'invented'},
                        {'region': 'EU', 'dataset': 'realm_items'},
                        {'region': 'EU', 'dataset': 'realm_items', 'realm_slug': '../realm'},
                        {'region': 'EU', 'dataset': 'commodities', 'realm_slug': 'synthetic'}):
            with self.subTest(options=options), self.assertRaises(MarketError):
                build_url(**options)

    def test_commodity_prices_stay_copper_without_invented_quantity_or_depth(self):
        result = normalize_tsm(catalogue(), region='EU', dataset='commodities')
        row = result['records'][0]
        self.assertEqual(result['scope']['kind'], 'commodity')
        self.assertIsNone(result['scope']['connected_realm_id'])
        self.assertEqual(row['unit_price_copper'], 80)
        self.assertEqual(row['metrics']['marketValue'], 100)
        self.assertIsNone(row['quantity'])
        self.assertFalse(result['capabilities']['depth'])
        self.assertFalse(result['capabilities']['executable_quotes'])

    def test_zero_quote_is_preserved_but_never_a_free_offer(self):
        raw = catalogue(rows=[[123, '', 100, 0, 0, 90, '2026-10-26T09:00:00Z']])
        row = normalize_tsm(raw, region='EU', dataset='commodities')['records'][0]
        self.assertEqual(row['name'], '')
        self.assertEqual(row['metrics']['minBuyout'], 0)
        self.assertIsNone(row['unit_price_copper'])
        self.assertEqual(row['quote_status'], 'no_positive_quote')

    def test_region_scalars_are_not_history_or_execution_probabilities(self):
        result = normalize_tsm(catalogue(regional=True), region='EU', dataset='region_items')
        row = result['records'][0]
        self.assertEqual(result['scope']['kind'], 'region_aggregate')
        self.assertEqual(row['metrics']['historical'], 90)
        self.assertEqual(row['metrics']['saleRate'], '0.25')
        self.assertEqual(row['metrics']['soldPerDay'], '1234567.125')
        self.assertIsNone(row['unit_price_copper'])
        self.assertFalse(result['capabilities']['history_timeline'])
        self.assertIn('not_position_execution_probability', result['metric_semantics']['saleRate'])

    def test_pet_identity_is_separate_and_never_fabricates_level_or_breed(self):
        pets = normalize_tsm(catalogue(pets=True), region='EU', dataset='realm_pets', realm_slug='synthetic')
        items = normalize_tsm(catalogue(), region='EU', dataset='realm_items', realm_slug='synthetic')
        row = pets['records'][0]
        self.assertIsNone(row['item_id'])
        self.assertEqual(row['pet_species_id'], 123)
        self.assertIsNone(row['variant']['level'])
        self.assertIsNone(row['variant']['breed_id'])
        self.assertNotEqual(row['variant_key'], items['records'][0]['variant_key'])

    def test_connected_realm_is_only_attached_when_explicitly_resolved(self):
        raw = catalogue()
        unresolved = normalize_tsm(raw, region='EU', dataset='realm_items', realm_slug='synthetic')
        resolved = normalize_tsm(raw, region='EU', dataset='realm_items', realm_slug='synthetic', connected_realm_id=999)
        self.assertEqual(unresolved['scope']['kind'], 'realm_unresolved')
        self.assertEqual(resolved['scope']['kind'], 'connected_realm')
        self.assertEqual(resolved['scope']['connected_realm_id'], 999)
        with self.assertRaises(MarketError):
            normalize_tsm(raw, region='EU', dataset='commodities', connected_realm_id=999)

    def test_source_scan_time_is_not_claimed_as_publication(self):
        result = normalize_tsm(catalogue(), region='EU', dataset='commodities')
        self.assertEqual(result['source_snapshot_at'], 1793005200)
        self.assertIsNone(result['source_generated_at'])
        self.assertIsNone(result['public_available_upper_bound'])
        self.assertEqual(result['clock_semantics'], 'upstream_scan_not_publication')
        self.assertEqual(result['records'][0]['updated_at_raw'], '2026-10-26T09:00:00Z')

    def test_csv_quoted_names_utf8_and_bom_are_preserved(self):
        raw = catalogue(rows=[[1, 'Test, "name"\nЯ', 1, 1, 1, 1, '2026-10-26T09:00:00Z']])
        result = normalize_tsm(b'\xef\xbb\xbf' + raw, region='EU', dataset='commodities')
        self.assertEqual(result['records'][0]['name'], 'Test, "name"\nЯ')

    def test_empty_html_changed_header_and_partial_row_reject_whole_response(self):
        inputs = [b'', catalogue(rows=[]), b'<html>failure</html>',
                  catalogue().replace(b'marketValue', b'newValue'), catalogue().rsplit(b',', 1)[0],
                  catalogue() + b'1,2\r\n', catalogue() + b'\r\n']
        for raw in inputs:
            with self.subTest(raw=raw[:25]), self.assertRaises(MarketError):
                normalize_tsm(raw, region='EU', dataset='commodities')

    def test_duplicate_ids_and_mixed_scan_times_are_rejected(self):
        row = [1, 'test', 1, 1, 1, 1, '2026-10-26T09:00:00Z']
        for rows in ([row, row], [row, [2, 'test', 1, 1, 1, 1, '2026-10-26T10:00:00Z']]):
            with self.assertRaises(MarketError):
                normalize_tsm(catalogue(rows=rows), region='EU', dataset='commodities')

    def test_invalid_numeric_values_fail_safely_without_source_text(self):
        for number in ('9' * 5000, '9223372036854775808', '-1', '1.1', 'NaN', '１２３'):
            for column in (0, 2):
                row = [1, 'PRIVATE_SOURCE_MARKER', 1, 1, 1, 1, '2026-10-26T09:00:00Z']
                row[column] = number
                with self.subTest(number=number[:10], column=column), self.assertRaises(MarketError) as raised:
                    normalize_tsm(catalogue(rows=[row]), region='EU', dataset='commodities')
                self.assertNotIn('PRIVATE_SOURCE_MARKER', str(raised.exception))
        for rate in ('1.01', '-0.1', 'NaN', '1e-4', 'Infinity', '9' * 65):
            row = [1, 'test', 1, 1, 1, rate, '0', '2026-10-26T09:00:00Z']
            with self.subTest(rate=rate), self.assertRaises(MarketError):
                normalize_tsm(catalogue(regional=True, rows=[row]), region='EU', dataset='region_items')

    def test_invalid_timestamps_and_encoding_do_not_receive_fabricated_time(self):
        for timestamp in ('2026-10-26', '2026-10-26T09:00:00', '2026-10-26T12:00:00+03:00',
                          '2026-02-30T09:00:00Z', '2026-10-26T09:00:00.5Z'):
            row = [1, 'test', 1, 1, 1, 1, timestamp]
            with self.subTest(timestamp=timestamp), self.assertRaises(MarketError):
                normalize_tsm(catalogue(rows=[row]), region='EU', dataset='commodities')
        for raw in (catalogue().replace(b'Synthetic', b'\xff'), catalogue().replace(b'Synthetic', b'\x00')):
            with self.assertRaises(MarketError):
                normalize_tsm(raw, region='EU', dataset='commodities')

    def test_source_and_identity_are_stable_across_prices_and_different_across_scopes(self):
        first = normalize_tsm(catalogue(), region='EU', dataset='commodities')
        second = normalize_tsm(catalogue().replace(b',80,', b',81,'), region='US', dataset='commodities')
        self.assertEqual(first['records'][0]['variant_key'], second['records'][0]['variant_key'])
        self.assertNotEqual(first['scope'], second['scope'])
        self.assertEqual(first['upstream'], 'blizzard_auction_snapshot')
        self.assertIn('not_independent', first['lineage_details']['independence'])

    def test_limits_and_malformed_quotes_fail_closed(self):
        raw = catalogue()
        with patch('wow_auction.market_tsm.MAX_BYTES', len(raw) - 1), self.assertRaises(MarketError):
            normalize_tsm(raw, region='EU', dataset='commodities')
        with patch('wow_auction.market_tsm.MAX_ROWS', 0), self.assertRaises(MarketError):
            normalize_tsm(raw, region='EU', dataset='commodities')
        with self.assertRaises(MarketError):
            normalize_tsm(raw.replace(b'Synthetic item', b'"unclosed'), region='EU', dataset='commodities')


if __name__ == '__main__':
    unittest.main()
