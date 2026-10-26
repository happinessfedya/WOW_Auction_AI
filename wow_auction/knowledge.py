"""Versioned mechanics contracts. Source claims are never execution promises."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
import re


class KnowledgeError(ValueError):
    """A bounded diagnostic code, without source text or private paths."""


SEMANTIC_REVISION = 2


def encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise KnowledgeError('invalid_knowledge_json') from None


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def text(value, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise KnowledgeError('invalid_knowledge_text')
    return value


def integer(value, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise KnowledgeError('invalid_knowledge_integer')
    return value


def sha256(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise KnowledgeError('invalid_knowledge_hash')
    return value


def physical_item(value):
    """Physical quantities are integral; an expectation belongs elsewhere."""
    if not isinstance(value, dict):
        raise KnowledgeError('invalid_physical_item')
    integer(value.get('item_id'), minimum=1)
    integer(value.get('quantity'), minimum=1)
    variant = value.get('variant')
    if variant is not None and not isinstance(variant, dict):
        raise KnowledgeError('invalid_item_variant')
    return value


def validate_mechanic(value):
    """Validate explicit facts, without inventing missing probability or limits.

    Inputs apply to one physical batch. A joint outcome lists all coproducts
    together. Independent marginals and CraftSim means cannot enter that field.
    Null inputs/outputs/distribution are supported but cannot be executable.
    """
    if not isinstance(value, dict):
        raise KnowledgeError('invalid_mechanic')
    text(value.get('family'), 100)
    integer(value.get('batch_size'), minimum=1)
    for direction in ('inputs', 'outputs'):
        rows = value.get(direction)
        if rows is not None:
            if not isinstance(rows, list) or len(rows) > 1000:
                raise KnowledgeError('invalid_mechanic_items')
            seen = set()
            for row in rows:
                physical_item(row)
                key = digest({'item_id': row['item_id'], 'variant': row.get('variant')})
                if key in seen:
                    raise KnowledgeError('duplicate_mechanic_item')
                seen.add(key)
    pmf = value.get('joint_outcomes')
    if pmf is not None:
        if value.get('outputs') is not None or not isinstance(pmf, list) or not 1 <= len(pmf) <= 10000:
            raise KnowledgeError('ambiguous_joint_outcomes')
        total = Fraction(0)
        seen = set()
        for outcome in pmf:
            if not isinstance(outcome, dict) or not isinstance(outcome.get('items'), list):
                raise KnowledgeError('invalid_joint_outcome')
            p = outcome.get('probability')
            if not isinstance(p, str) or not re.fullmatch(r'(0|1)(\.\d{1,30})?', p):
                raise KnowledgeError('explicit_decimal_probability_required')
            try:
                probability = Decimal(p)
            except InvalidOperation:
                raise KnowledgeError('invalid_probability') from None
            if not 0 < probability <= 1:
                raise KnowledgeError('invalid_probability')
            total += Fraction(p)
            if len(outcome['items']) > 1000:
                raise KnowledgeError('joint_outcome_size_limit')
            for item in outcome['items']:
                physical_item(item)
            canonical = sorted(encode(item) for item in outcome['items'])
            if len(set(digest({'item_id': i['item_id'], 'variant': i.get('variant')})
                       for i in outcome['items'])) != len(outcome['items']):
                raise KnowledgeError('duplicate_outcome_item')
            identity = digest(canonical)
            if identity in seen:
                raise KnowledgeError('duplicate_joint_outcome')
            seen.add(identity)
        if total != 1:
            raise KnowledgeError('incomplete_joint_distribution')
        if value.get('distribution_basis') not in {'documented_model', 'confirmed_mechanic'}:
            raise KnowledgeError('joint_distribution_basis_required')
    # Means are decimal strings and are never interpreted as physical outputs.
    means = value.get('expected_outputs', [])
    if not isinstance(means, list) or len(means) > 1000:
        raise KnowledgeError('invalid_expected_outputs')
    for row in means:
        if not isinstance(row, dict):
            raise KnowledgeError('invalid_expected_output')
        integer(row.get('item_id'), minimum=1)
        quantity = row.get('mean_quantity')
        if not isinstance(quantity, str) or not re.fullmatch(r'\d{1,20}(\.\d{1,30})?', quantity):
            raise KnowledgeError('invalid_expected_quantity')
    return value


def mechanic_readiness(value):
    validate_mechanic(value)
    missing = []
    for field in ('inputs', 'requirements', 'cooldowns', 'binding', 'build', 'costs'):
        if value.get(field) is None:
            missing.append(field)
    if value.get('outputs') is None and value.get('joint_outcomes') is None:
        missing.append('outputs_or_joint_distribution')
    return {'mechanically_complete': not missing, 'missing': missing,
            'recommendation_eligible': False, 'reason': 'stage4_economic_and_execution_checks_required'}


def semantic_digest(candidate):
    """Ignore source locations, not arbitrary fields named line or sha256.

    ATT embeds locators in ancestor/child links and symbol definitions. Their
    positions and whole-file hashes change on comments alone. In contrast,
    hashes of unresolved expressions are semantic: the displayed expression
    can be truncated, so discarding its digest would hide real changes.
    """
    locator_fields = {
        ('claim', 'ancestor_context', '*'): {'line'},
        ('claim', 'related_items', '*'): {'line'},
        ('claim', 'symbol_reference', 'definitions', '*'): {'line', 'sha256'},
    }
    def semantic(value, path=()):
        if isinstance(value, dict):
            excluded = ({'source', 'key', 'received_at', 'observed_at'} if not path
                        else locator_fields.get(path, set()))
            return {key: semantic(child, path + (key,)) for key, child in value.items() if key not in excluded}
        if isinstance(value, list):
            return [semantic(child, path + ('*',)) for child in value]
        return value
    return digest(semantic(candidate))


def validate_candidate(candidate):
    if not isinstance(candidate, dict) or len(encode(candidate).encode()) > 128 * 1024:
        raise KnowledgeError('invalid_or_oversized_candidate')
    text(candidate.get('key'), 1000)
    text(candidate.get('family'), 100)
    if candidate.get('status') not in {'candidate', 'historical', 'quarantined', 'unsupported'}:
        raise KnowledgeError('unverified_candidate_status')
    source = candidate.get('source')
    if not isinstance(source, dict):
        raise KnowledgeError('candidate_source_required')
    text(source.get('path'), 2000)
    sha256(source.get('sha256'))
    if 'line' in source:
        integer(source['line'], minimum=1)
    if not isinstance(candidate.get('unknowns'), list):
        raise KnowledgeError('candidate_unknowns_required')
    if candidate.get('mechanic') is not None:
        validate_mechanic(candidate['mechanic'])
    return candidate
