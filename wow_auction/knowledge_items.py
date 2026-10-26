"""Read a user's local TSM item cache without executing or copying addon code.

Version 19 stores 24 six-bit characters per row, least-significant digit first.
This module describes that data contract independently.  It does not publish
TSM's cache or infer physical-instance binding from the template-level flag.
"""

from __future__ import annotations

import re
from typing import Any


class ItemMetadataError(ValueError):
    """Safe diagnostic containing neither personal values nor source text."""


_DIGITS = {char: value for value, char in enumerate(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")}
# Names are source field names, units and semantics are preserved explicitly.
_FIELDS = (("itemLevel", 2), ("minLevel", 2), ("vendorSell", 5),
           ("maxStack", 2), ("invSlotId", 1), ("texture", 5),
           ("classId", 1), ("subClassId", 1), ("quality", 1),
           ("isBOP", 1), ("isCraftingReagent", 1), ("expansionId", 1),
           ("craftedQuality", 1))
_VERSION = re.compile(r"19,([a-z]{2}[A-Z]{2}),([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}),([0-9]{1,9})\Z")
_KEY = re.compile(r"[ip]:[1-9][0-9]{0,8}(?::[0-9]*)*\Z")
MAX_RECORDS = 250_000


def _strings(value: Any, *, max_records: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        chunks = [value]
    elif isinstance(value, dict):
        if len(value) > (max_records + 999) // 1000:
            raise ItemMetadataError("item_metadata_chunk_limit")
        if (any(type(key) is not int for key in value)
                or set(value) != set(range(1, len(value) + 1))):
            raise ItemMetadataError("item_metadata_noncontiguous_chunks")
        chunks = [value[index] for index in range(1, len(value) + 1)]
    else:
        raise ItemMetadataError("item_metadata_invalid_chunks")
    records: list[str] = []
    size = 0
    for chunk in chunks:
        if not isinstance(chunk, str):
            raise ItemMetadataError("item_metadata_invalid_chunk")
        size += len(chunk.encode("utf-8"))
        if size > 32 * 1024 * 1024:
            raise ItemMetadataError("item_metadata_string_size_limit")
        values = chunk.split("\x02")
        if len(values) > 1000 or len(records) + len(values) > max_records:
            raise ItemMetadataError("item_metadata_record_limit")
        records.extend(values)
    return records


def decode_tsm_item_info(variables: dict, *, expected_build: str | None = None,
                         max_records: int = MAX_RECORDS) -> dict:
    """Return versioned local metadata; never change inventory eligibility.

    ``expected_build`` is the complete installed version, e.g. 12.1.5.70575.
    Different builds are retained as stale, with unknown individual record age.
    Keys including TSM bonus strings are preserved exactly. No equivalence to a
    BagSync physical item or an auction variant is asserted.
    """
    if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
        raise ItemMetadataError("item_metadata_invalid_record_limit")
    if not isinstance(variables, dict):
        raise ItemMetadataError("item_metadata_invalid_root")
    if expected_build is not None and (not isinstance(expected_build, str)
            or re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,9}", expected_build) is None):
        raise ItemMetadataError("item_metadata_invalid_expected_build")
    db = variables.get("TSMItemInfoDB")
    if not isinstance(db, dict):
        raise ItemMetadataError("item_metadata_missing_database")
    version = db.get("versionStr")
    match = _VERSION.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise ItemMetadataError("item_metadata_unsupported_version")
    data = db.get("data")
    if not isinstance(data, str) or len(data) > max_records * 24 or len(data) % 24:
        raise ItemMetadataError("item_metadata_invalid_encoded_length")
    names = _strings(db.get("names"), max_records=max_records)
    keys = _strings(db.get("itemStrings"), max_records=max_records)
    if len(names) != len(keys) or len(keys) * 24 != len(data):
        raise ItemMetadataError("item_metadata_row_count_mismatch")
    if len(set(keys)) != len(keys):
        raise ItemMetadataError("item_metadata_duplicate_key")
    rows = []
    for index, (key, name) in enumerate(zip(keys, names)):
        if (not isinstance(key, str) or len(key) > 2048 or _KEY.fullmatch(key) is None
                or len(name) > 4096 or any(ord(char) < 32 for char in name)):
            raise ItemMetadataError("item_metadata_invalid_identity")
        encoded = data[index * 24:(index + 1) * 24]
        if any(char not in _DIGITS for char in encoded):
            raise ItemMetadataError("item_metadata_invalid_alphabet")
        fields = {}
        offset = 0
        for field, width in _FIELDS:
            value = sum(_DIGITS[char] * (64 ** digit)
                        for digit, char in enumerate(encoded[offset:offset + width]))
            fields[field] = None if value == 64 ** width - 1 else value
            offset += width
        if any(fields[field] not in (None, 0, 1) for field in ("isBOP", "isCraftingReagent")):
            raise ItemMetadataError("item_metadata_invalid_flag")
        rows.append({"source_item_string": key, "identity_status": "source_key_only",
                     "kind": "item" if key.startswith("i:") else "battle_pet",
                     "name": name, "fields": fields, "observed_at": None,
                     "binding_instance": "unknown", "tradeability": "unknown"})
    locale, game_version, build_number = match.groups()
    build = game_version + "." + build_number
    return {"schema_version": 1, "source": "TSMItemInfoDB", "version_str": version,
            "locale": locale, "game_build": build, "row_count": len(rows), "items": rows,
            "status": ("stale_build" if expected_build is not None and build != expected_build
                       else "build_matches_record_age_unknown" if expected_build is not None
                       else "build_comparison_missing"),
            "scope": "local_cache_only", "redistribution": "not_authorized_by_this_decoder",
            "limitations": ["individual_record_observation_time_missing",
                            "isBOP_is_template_acquire_or_quest_flag_not_instance_binding",
                            "false_isBOP_does_not_prove_tradeability",
                            "TSM_item_keys_do_not_prove_cross_source_variant_equivalence",
                            "craftedQuality_is_source_enum_not_normalized_tier",
                            "cache_does_not_prove_global_item_coverage"]}
