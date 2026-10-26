"""Local market configuration; secret values are read only at request time."""
import json
import os
from pathlib import Path
import re

from .market_common import MarketError, scope
from .ingestion import reject_source_destination

DEFAULT_CONFIG = Path('.private/market.local.json')
SECRET_ENV = {'undermine_api_key': 'WOW_UNDERMINE_API_KEY'}


def init_market_config(path, *, region='EU', realm_slug=None, wow_roots=()):
    path = Path(path)
    reject_source_destination(path, list(wow_roots))
    scope(region, 'realm_unresolved', realm_slug=realm_slug)
    if path.exists():
        raise MarketError('market_configuration_already_exists')
    region = region.upper()
    base = {'region': region, 'realm_slug': realm_slug, 'interval_seconds': 14400, 'enabled': True}
    jobs = [{**base, 'provider': 'tsm', 'dataset': dataset} for dataset in
            ('commodities', 'realm_items', 'region_items', 'realm_pets', 'region_pets')]
    jobs += [{**base, 'provider': 'undermine', 'dataset': 'commodity_summary', 'enabled': False,
              'interval_seconds': 3600}]
    for job in jobs:
        if not job['dataset'].startswith('realm_') and job['dataset'] != 'auctions':
            job.pop('realm_slug', None)
    config = {'schema_version': 1, 'database': str((path.parent/'market.sqlite3').resolve()),
              'secrets_path': str((path.parent/'market-secrets.local.json').resolve()),
              'wow_roots': [str(Path(p).resolve()) for p in wow_roots], 'region': region,
              'realm_slug': realm_slug, 'jobs': jobs, 'allow_paid_undermine': False,
              'blizzard_api_enabled': False}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return config


def load_market_config(path):
    try:
        with Path(path).open('rb') as stream: raw = stream.read(1_048_577)
        if len(raw) > 1_048_576: raise MarketError('market_configuration_size_limit')
        config = json.loads(raw)
        if type(config['schema_version']) is not int or config['schema_version'] != 1:
            raise MarketError('unsupported_market_configuration')
        roots = [Path(p) for p in config['wow_roots']]
        for field in ('database', 'secrets_path'):
            target = Path(config[field])
            if not target.is_absolute(): raise MarketError('absolute_market_paths_required')
            reject_source_destination(target, roots)
        if Path(config['database']).resolve() == Path(config['secrets_path']).resolve():
            raise MarketError('market_paths_must_differ')
        config['region'] = scope(config['region'], 'realm_unresolved', realm_slug=config['realm_slug'])['region']
        if type(config.get('allow_paid_undermine')) is not bool:
            raise MarketError('invalid_market_paid_setting')
        if config.get('blizzard_api_enabled', False) is not False:
            raise MarketError('blizzard_disabled_by_user')
        jobs = config['jobs']
        if not isinstance(jobs, list) or not 1 <= len(jobs) <= 100:
            raise MarketError('invalid_market_job_count')
        identities = set()
        for job in jobs:
            if not isinstance(job, dict) or job.get('provider') not in ('tsm', 'blizzard', 'undermine'):
                raise MarketError('invalid_market_provider')
            if not isinstance(job.get('dataset'), str) or not re.fullmatch(r'[a-z_]{1,40}', job['dataset']):
                raise MarketError('invalid_market_dataset')
            job['region'] = scope(job['region'], 'reference', realm_slug=job.get('realm_slug'))['region']
            if type(job.get('interval_seconds')) is not int or not 60 <= job['interval_seconds'] <= 86400:
                raise MarketError('invalid_market_poll_interval')
            if type(job.get('enabled')) is not bool:
                raise MarketError('invalid_market_job_enabled')
            if any(k in job for k in ('url', 'headers', 'client_secret', 'api_key', 'token')):
                raise MarketError('credentials_or_arbitrary_url_in_job')
            allowed = {'provider', 'dataset', 'region', 'realm_slug', 'interval_seconds', 'enabled',
                       'item_id', 'pet_species_id', 'detail'}
            if set(job) - allowed:
                raise MarketError('unsupported_market_job_field')
            if job.get('realm_slug') and not (job['dataset'].startswith('realm_') or job['dataset'] in {'realm', 'auctions'}):
                raise MarketError('unexpected_market_realm_context')
            selectors = {'item_id', 'pet_species_id', 'detail'} & set(job)
            if job['provider'] != 'undermine' and selectors:
                raise MarketError('unsupported_market_selector')
            if job['provider'] == 'blizzard':
                if job['dataset'] not in {'commodities', 'auctions', 'realm_index', 'realm'}:
                    raise MarketError('unsupported_blizzard_dataset')
                if job['dataset'] in {'auctions', 'realm'} and not job.get('realm_slug'):
                    raise MarketError('market_realm_slug_required')
            elif job['provider'] == 'tsm':
                from .market_tsm import build_url
                build_url(region=job['region'], dataset=job['dataset'], realm_slug=job.get('realm_slug'))
            else:
                from .market_undermine import build_url
                build_url(**{k: job[k] for k in ('region', 'dataset', 'realm_slug', 'item_id', 'pet_species_id', 'detail') if k in job})
            identity = json.dumps({k: v for k, v in job.items() if k not in {'interval_seconds', 'enabled'}}, sort_keys=True)
            if identity in identities: raise MarketError('duplicate_market_job')
            identities.add(identity)
        return config
    except MarketError:
        raise
    except (ValueError, TypeError, KeyError, OSError):
        raise MarketError('invalid_market_configuration') from None


def validate_secrets(values):
    """Select only the supported provider's fields; never inspect Blizzard keys."""
    if not isinstance(values, dict):
        raise MarketError('invalid_secret_file')
    result = {key: values.get(key, '') for key in SECRET_ENV}
    if any(not isinstance(v, str) or len(v) > 4096 or '\r' in v or '\n' in v for v in result.values()):
        raise MarketError('invalid_credential_format')
    return result


def load_secrets(config):
    # Called lazily only by an enabled, policy-approved UE job. A usable
    # environment value makes reading its fallback file unnecessary.
    result = validate_secrets({key: os.environ.get(env, '') for key, env in SECRET_ENV.items()})
    if all(result.values()):
        return result
    try:
        path = Path(config['secrets_path'])
        if path.is_symlink(): raise MarketError('unsafe_secret_file')
        if path.exists():
            with path.open('rb') as stream: raw = stream.read(16385)
            if len(raw) > 16384: raise MarketError('secret_file_size_limit')
            data = json.loads(raw)
            if not isinstance(data, dict): raise MarketError('invalid_secret_file')
            for key in result:
                if not result[key]: result[key] = data.get(key, '')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, MarketError): raise
        raise MarketError('invalid_secret_file') from None
    return validate_secrets(result)
