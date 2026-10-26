"""Source-specific processing, access and retention decisions, independently versioned."""
from .market_common import MarketError, fingerprint

POLICIES = {
    'tsm': {'status': 'local_working_cache', 'network_allowed': True, 'retention_seconds': 14400,
            'long_term_archive_allowed': False, 'redistribution_allowed': False,
            'cloud_forwarding_allowed': False, 'attribution': 'TradeSkillMaster public pricing data',
            'basis': 'Explicit public CSV consumption; four-hour operational cache, not a claim of an unlimited archive license',
            'evidence': ['https://tradeskillmaster.com/public-data', 'https://tradeskillmaster.com/terms']},
    'blizzard': {'status': 'disabled_by_user', 'network_allowed': False, 'retention_seconds': 30 * 86400,
                 'long_term_archive_allowed': False, 'redistribution_allowed': False,
                 'cloud_forwarding_allowed': False, 'attribution': 'World of Warcraft data by Blizzard Entertainment',
                 'basis': 'User prohibits obtaining or using Blizzard API credentials; historical policy TTL retained only for provenance',
                 'evidence': ['https://www.blizzard.com/ko-kr/legal/a2989b50-5f16-43b1-abec-2ae17cc09dd6/blizzard-developer-api-terms-of-use']},
    'undermine': {'status': 'unresolved_api_terms_conflict', 'network_allowed': False,
                  'retention_seconds': 0, 'long_term_archive_allowed': False,
                  'redistribution_allowed': False, 'cloud_forwarding_allowed': False,
                  'attribution': 'The Undermine Exchange',
                  'basis': 'Developer API describes automation but site terms restrict automated systematic collection; unresolved',
                  'evidence': ['https://undermine.exchange/api.html', 'https://undermine.exchange/terms.html']},
}


def source_policy(provider):
    if provider not in POLICIES:
        raise MarketError('unsupported_market_provider')
    policy = {**POLICIES[provider], 'provider': provider, 'version': '2026-10-26-v1'}
    return {**policy, 'fingerprint': fingerprint(policy)}
