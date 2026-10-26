"""Synthetic local configuration and secret-isolation checks; no real keys."""
from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wow_auction.market_common import MarketError
from wow_auction.market_config import DEFAULT_CONFIG, SECRET_ENV, init_market_config, load_market_config, load_secrets


class MarketConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='synthetic_config_', dir=Path(__file__).parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / '.private' / 'market.local.json'
        self.game = self.root / 'synthetic-game'
        self.game.mkdir()
        self.config = init_market_config(self.path, region='EU', realm_slug='synthetic-realm', wow_roots=[self.game])

    def write(self, config):
        self.path.write_text(json.dumps(config), encoding='utf-8')

    def invalid(self, config):
        self.write(config)
        with self.assertRaises(MarketError):
            load_market_config(self.path)

    def test_default_private_config_roundtrips_without_creating_or_outputting_secrets(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            loaded = load_market_config(self.path)
        self.assertEqual(loaded, self.config)
        self.assertEqual(DEFAULT_CONFIG.parts[0], '.private')
        self.assertEqual(Path(loaded['database']).parent, self.path.parent.resolve())
        self.assertEqual(Path(loaded['secrets_path']).parent, self.path.parent.resolve())
        self.assertFalse(Path(loaded['secrets_path']).exists())
        self.assertFalse(Path(loaded['database']).exists())
        self.assertEqual(output.getvalue() + errors.getvalue(), '')
        self.assertFalse(loaded['allow_paid_undermine'])
        self.assertFalse(loaded['blizzard_api_enabled'])
        self.assertFalse(any(job['provider'] == 'blizzard' for job in loaded['jobs']))
        self.assertTrue(all(not job['enabled'] for job in loaded['jobs'] if job['provider'] == 'undermine'))
        raw = self.path.read_text(encoding='utf-8')
        self.assertNotIn('client_secret', raw)
        self.assertNotIn('api_key', raw)

    def test_init_is_exclusive_and_never_overwrites_existing_configuration(self):
        original = self.path.read_bytes()
        with self.assertRaisesRegex(MarketError, 'already_exists'):
            init_market_config(self.path, region='US', realm_slug='another-realm')
        self.assertEqual(self.path.read_bytes(), original)

    def test_environment_credentials_take_precedence_over_private_file(self):
        secret_path = Path(self.config['secrets_path'])
        file_values = {'undermine_api_key': 'FILE_UE_KEY'}
        secret_path.write_text(json.dumps(file_values), encoding='utf-8')
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'WOW_UNDERMINE_API_KEY': 'ENV_UE_KEY'}, clear=True):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                loaded = load_secrets(self.config)
        self.assertEqual(loaded, {'undermine_api_key': 'ENV_UE_KEY'})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_secrets(self.config), file_values)
        self.assertEqual(secret_path.read_text(encoding='utf-8'), json.dumps(file_values))
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')
        self.assertTrue(all(value not in self.path.read_text() for value in loaded.values()))

    def test_missing_credentials_are_empty_values_not_fabricated_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_secrets(self.config), dict.fromkeys(SECRET_ENV, ''))

    def test_invalid_private_secret_file_errors_do_not_echo_input(self):
        path = Path(self.config['secrets_path'])
        samples = ['PRIVATE_BROKEN_SECRET', '[]', '{"undermine_api_key":123}',
                   json.dumps({'undermine_api_key': 'PRIVATE\r\nHEADER_INJECTION'}),
                   json.dumps({'undermine_api_key': 'P' * 4097}), ' ' * 16385]
        with patch.dict(os.environ, {}, clear=True):
            for sample in samples:
                path.write_text(sample, encoding='utf-8')
                with self.subTest(sample=sample[:30]), self.assertRaises(MarketError) as raised:
                    load_secrets(self.config)
                self.assertNotIn('PRIVATE', str(raised.exception))
                self.assertNotIn(sample, str(raised.exception))

    def test_invalid_environment_credential_is_rejected_without_echo(self):
        with patch.dict(os.environ, {'WOW_UNDERMINE_API_KEY': 'PRIVATE\nINJECTION'}, clear=True):
            with self.assertRaisesRegex(MarketError, '^invalid_credential_format$'):
                load_secrets(self.config)

    def test_blizzard_credentials_are_not_loaded_from_environment_or_old_private_files(self):
        Path(self.config['secrets_path']).write_text(json.dumps({
            'blizzard_client_id': 'IGNORED_FILE_ID', 'blizzard_client_secret': 'IGNORED_FILE_SECRET'}), encoding='utf-8')
        with patch.dict(os.environ, {'WOW_BLIZZARD_CLIENT_ID': 'IGNORED_ENV_ID',
                                     'WOW_BLIZZARD_CLIENT_SECRET': 'IGNORED_ENV_SECRET'}, clear=True):
            self.assertEqual(load_secrets(self.config), {'undermine_api_key': ''})
        self.assertEqual(SECRET_ENV, {'undermine_api_key': 'WOW_UNDERMINE_API_KEY'})

    def test_configuration_cannot_reenable_blizzard_against_user_constraint(self):
        invalid = deepcopy(self.config)
        invalid['blizzard_api_enabled'] = True
        self.write(invalid)
        with self.assertRaisesRegex(MarketError, '^blizzard_disabled_by_user$'):
            load_market_config(self.path)

    def test_game_destinations_and_game_file_extensions_are_never_created(self):
        for target in (self.game / 'market.local.json', self.root / 'TradeSkillMaster.lua', self.root / 'addon.bak'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                init_market_config(target, region='EU', realm_slug='synthetic-realm', wow_roots=[self.game])
            self.assertFalse(target.exists())
        for field in ('database', 'secrets_path'):
            for target in (self.game / 'new.sqlite3', self.root / 'SavedVariables.lua', self.root / 'backup.bak'):
                invalid = deepcopy(self.config)
                invalid[field] = str(target.resolve())
                with self.subTest(field=field, target=target):
                    self.invalid(invalid)
                self.assertFalse(target.exists())

    def test_paths_must_be_distinct_absolute_and_do_not_redirect_to_arbitrary_url(self):
        for field, value in [('database', 'relative.sqlite3'), ('secrets_path', 'relative.json'),
                             ('database', self.config['secrets_path']), ('database', 'https://evil.invalid/local.sqlite3')]:
            invalid = deepcopy(self.config)
            invalid[field] = value
            with self.subTest(field=field, value=value):
                self.invalid(invalid)

    def test_job_cannot_supply_credentials_url_headers_or_unverified_realm_id(self):
        for field, value in [('url', 'https://evil.invalid/collect'), ('headers', {'Authorization': 'PRIVATE_TOKEN'}),
                             ('client_secret', 'PRIVATE_SECRET'), ('api_key', 'PRIVATE_KEY'), ('token', 'PRIVATE_TOKEN'),
                             ('connected_realm_id', 555), ('expected_realm_id', 10), ('path', '/private')]:
            invalid = deepcopy(self.config)
            invalid['jobs'][0][field] = value
            self.write(invalid)
            with self.subTest(field=field), self.assertRaises(MarketError) as raised:
                load_market_config(self.path)
            self.assertNotIn('PRIVATE', str(raised.exception))
            self.assertNotIn('evil.invalid', str(raised.exception))

    def test_datasets_and_selectors_are_validated_before_worker_can_request(self):
        examples = [('tsm', 'invented', {}), ('blizzard', 'region_items', {}),
                    ('blizzard', 'commodities', {'detail': 'full'}),
                    ('blizzard', 'commodities', {'item_id': 123}),
                    ('tsm', 'commodities', {'item_id': 123}),
                    ('undermine', 'commodity_now', {}),
                    ('undermine', 'commodity_now', {'item_id': True}),
                    ('undermine', 'commodity_now', {'item_id': 123, 'detail': {'item_suffix': 10}}),
                    ('undermine', 'region_item_hourly', {'item_id': 123}),
                    ('blizzard', 'auctions', {})]
        for provider, dataset, selectors in examples:
            invalid = deepcopy(self.config)
            invalid['jobs'] = [{'provider': provider, 'dataset': dataset, 'region': 'EU',
                                'enabled': True, 'interval_seconds': 3600, **selectors}]
            with self.subTest(provider=provider, dataset=dataset, selectors=selectors):
                self.invalid(invalid)

    def test_paid_variant_configuration_is_explicit_without_performing_network_access(self):
        configured = deepcopy(self.config)
        configured['jobs'] = [{'provider': 'undermine', 'dataset': 'realm_item_daily', 'region': 'EU',
                               'realm_slug': 'synthetic-realm', 'item_id': 123,
                               'detail': {'item_level': 44, 'item_suffix': 10},
                               'enabled': False, 'interval_seconds': 3600}]
        self.write(configured)
        loaded = load_market_config(self.path)
        self.assertEqual(loaded['jobs'][0]['detail'], {'item_level': 44, 'item_suffix': 10})
        self.assertFalse(loaded['allow_paid_undermine'])

    def test_region_case_is_canonical_before_locale_mapping_and_job_identity(self):
        configured = deepcopy(self.config)
        configured['region'] = 'eu'
        for job in configured['jobs']:
            job['region'] = 'eu'
        self.write(configured)
        loaded = load_market_config(self.path)
        self.assertEqual(loaded['region'], 'EU')
        self.assertTrue(all(job['region'] == 'EU' for job in loaded['jobs']))

    def test_schema_types_poll_limits_duplicates_and_realm_slug_are_checked(self):
        mutations = [('schema_version', True), ('schema_version', 2), ('allow_paid_undermine', 1),
                     ('jobs', []), ('jobs', self.config['jobs'] + [deepcopy(self.config['jobs'][0])]),
                     ('region', 'CN'), ('realm_slug', '../private')]
        for field, value in mutations:
            invalid = deepcopy(self.config)
            invalid[field] = value
            with self.subTest(field=field):
                self.invalid(invalid)
        for field, value in [('interval_seconds', True), ('interval_seconds', 59), ('interval_seconds', 86401),
                             ('enabled', 1), ('realm_slug', 'private/realm'), ('region', 'CN')]:
            invalid = deepcopy(self.config)
            invalid['jobs'][0][field] = value
            with self.subTest(job_field=field, value=value):
                self.invalid(invalid)

    def test_bounded_malformed_configuration_has_safe_diagnostic(self):
        for content in (b'{"schema_version":1, PRIVATE_SOURCE', b'[]', b' ' * 1_048_577):
            self.path.write_bytes(content)
            with self.subTest(prefix=content[:20]), self.assertRaises(MarketError) as raised:
                load_market_config(self.path)
            self.assertNotIn('PRIVATE_SOURCE', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
