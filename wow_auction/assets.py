"""Read-only normalization of the observed GX, BagSync and CraftSim schemas.

Amounts are observed copper, not profit or a statement of current net worth.
Raw names and item strings belong in the private local database, not public logs.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import re


class AssetError(ValueError):
    """The source does not meet the supported data contract (no raw values)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


AssetSchemaError = AssetError


def _id(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _table(value: object) -> dict:
    if not isinstance(value, dict):
        raise AssetSchemaError("expected_table")
    return value


def _integer(value: object, *, positive: bool = False) -> int:
    if type(value) is not int or value < int(positive) or value > 2**63 - 1:
        raise AssetSchemaError("invalid_nonnegative_integer")
    return value


def _decimal_integer(value: str, *, code: str, positive: bool = False) -> int:
    """Bound source string numbers before int(), including non-ASCII digits.

    The signed-64-bit bound is the same data contract as _integer, not a
    gameplay limit. Unknown opaque options are preserved without conversion.
    """
    limit = "9223372036854775807"
    if (not 1 <= len(value) <= len(limit) or not value.isascii()
            or not value.isdecimal()
            or (len(value) == len(limit) and value > limit)):
        raise AssetSchemaError(code)
    number = int(value)
    if positive and number == 0:
        raise AssetSchemaError(code)
    return number


def _timestamp(value: object) -> int | None:
    if value is None or (type(value) is int and value == 0):
        return None
    return _integer(value, positive=True)


def _realm_alias(value: str) -> str:
    # GX removes spaces/apostrophes; do not remove hyphens or all punctuation.
    return re.sub(r"[\s'\u2019]", "", value).casefold()


def _alias(name: str, realm: str) -> str:
    return name.casefold() + "-" + _realm_alias(realm)


def _warn(batch: dict, code: str) -> None:
    if code not in batch["warnings"]:
        batch["warnings"].append(code)


def _cash(batch: dict, owner: str, kind: str, reported: object,
          observed: int | None, source_key: str, *, requires_time: bool = False) -> None:
    value = None if reported is None else _integer(reported)
    # GX initializes a synthetic 0 before the first successful bank read.
    valid = value is not None and (observed is not None or not requires_time)
    batch["cash"].append({
        "owner_key": owner, "owner_kind": kind, "source_key": source_key,
        "reported_copper": value, "copper": value if valid else None,
        "observed_at": observed,
        "completeness": "observed" if valid else "unknown",
        "freshness": "timestamped" if observed is not None else "unknown",
    })


def _character(batch: dict, name: str, realm: str, guid: object,
               source_key: str) -> str:
    if guid is not None and (not isinstance(guid, str) or not guid):
        raise AssetSchemaError("invalid_guid")
    owner = _id(batch["account_scope"], batch["region"], batch["source"], source_key)
    batch["characters"].append({
        "owner_key": owner, "source_key": source_key, "guid": guid,
        "name": name, "realm": realm, "alias": _alias(name, realm),
    })
    return owner


def _item(raw: object) -> dict:
    if type(raw) is int:
        raw = str(_integer(raw, positive=True))
    if not isinstance(raw, str) or not raw:
        raise AssetSchemaError("invalid_item_record")
    pieces = raw.split(";", 2)
    link = pieces[0]
    quantity = 1
    if len(pieces) > 1:
        quantity = _decimal_integer(pieces[1], code="invalid_item_quantity", positive=True)
    options = {}
    if len(pieces) > 2:
        for entry in pieces[2].split("|"):
            if not entry or "=" not in entry:
                raise AssetSchemaError("invalid_item_options")
            key, value = entry.split("=", 1)
            if not key or key in options:
                raise AssetSchemaError("duplicate_item_option")
            options[key] = value
    # Keep all source identity information. Known location metadata is not a variant.
    identity_options = {k: v for k, v in options.items()
                        if k not in {"auction", "bagslot", "gtab"}}
    match = re.fullmatch(r"(?:item:)?([0-9]+)(?::[0-9:]*)?", link)
    item_id = (_decimal_integer(match.group(1), code="invalid_item_id", positive=True)
               if match else None)
    pet = None
    if item_id is not None and item_id >= 10_000_000_000:
        fake = item_id
        item_id = None  # BagSync's fake species number is not a WoW item ID.
        pet = {"species_id": ((fake - 10_000_000_000) // 100_000
                              if (fake - 10_000_000_000) % 100_000 == 0 else None),
               "raw_petdata": options.get("petdata"), "breed_id": None}
    expiry = options.get("auction")
    if expiry is not None:
        expiry = _decimal_integer(expiry, code="invalid_auction_expiration", positive=True)
    return {
        "raw_item": raw, "raw_link": link, "item_id": item_id,
        "variant_key": _id("BagSync-lossy", link, sorted(identity_options.items())),
        "quantity": quantity, "options": options, "pet": pet,
        "auction_expires_at": expiry, "identity_completeness": "lossy_source",
        "tradeability": "unknown", "eligible_liquid_asset": False,
    }


def _container(batch: dict, owner: str, kind: str, source_key: str,
               values: dict | None, observed: int | None = None) -> None:
    key = _id(owner, kind, source_key)
    present = values is not None
    batch["containers"].append({
        "container_key": key, "owner_key": owner, "kind": kind,
        "source_key": source_key, "present": present, "complete": None,
        "observed_at": observed,
        "observation_status": ("missing" if not present else
                               "source_empty" if not values else "source_nonempty"),
        "freshness": "timestamped" if observed is not None else "unknown",
    })
    if values is None:
        return
    for ordinal, raw in _table(values).items():
        if type(ordinal) is not int or ordinal < 1:
            raise AssetSchemaError("invalid_inventory_list_index")
        row = _item(raw)
        row.update(owner_key=owner, container_key=key, kind=kind,
                   list_position=ordinal, observed_at=observed)
        if kind in {"equip", "equipbags"}:
            row["tradeability"] = "excluded_equipment"
        elif "keystone" in row["options"]:
            row["tradeability"] = "excluded_keystone"
        batch["items"].append(row)


def _containers(batch: dict, owner: str, record: dict) -> None:
    for kind in ("bag", "bank", "reagents", "equipbags"):
        value = record.get(kind)
        if value is None or not _table(value):
            _container(batch, owner, kind, kind, value)
        else:
            for index, items in value.items():
                if not isinstance(index, (int, str)) or type(index) is bool:
                    raise AssetSchemaError("invalid_container_key")
                _container(batch, owner, kind, kind + "/" + str(index), _table(items))
    for kind in ("equip", "mailbox"):
        _container(batch, owner, kind, kind,
                   _table(record[kind]) if kind in record else None)
    auction = record.get("auction")
    if auction is None:
        _container(batch, owner, "auction", "auction", None)
    else:
        auction = _table(auction)
        _container(batch, owner, "auction", "auction/bag",
                   _table(auction["bag"]) if "bag" in auction else None,
                   _timestamp(auction.get("lastscan")))


def normalize_assets(source: str, variables: dict, *, account_scope: str,
                     region: str | None = None,
                     ownership_group: str | None = None) -> dict:
    """Normalize one snapshot. ``ownership_group`` is an explicit user assertion.

    ``account_scope`` groups source files; it does NOT assert that other account
    folders have independent warbanks. Missing times remain null. File/receipt/
    availability clocks are attached by the importer, never inferred here.
    """
    if not isinstance(account_scope, str) or not account_scope:
        raise AssetSchemaError("missing_account_scope")
    if region is not None and region not in {"EU", "US", "KR", "TW", "CN"}:
        raise AssetSchemaError("invalid_region")
    if ownership_group is not None and (not isinstance(ownership_group, str) or not ownership_group):
        raise AssetSchemaError("invalid_ownership_group")
    batch = {"schema_version": 1, "source": source, "account_scope": account_scope,
             "region": region, "ownership_group": ownership_group,
             "characters": [], "cash": [], "containers": [], "items": [],
             "warnings": [], "craftsim": None}
    if region is None:
        _warn(batch, "region_unknown")
    if ownership_group is None:
        _warn(batch, "cross_account_ownership_unconfirmed")
    root_name = {"GX": "GX_DB", "BagSync": "BagSyncDB", "CraftSim": "CraftSimDB"}.get(source)
    if root_name is None or root_name not in variables:
        raise AssetSchemaError("missing_or_unsupported_source_root")
    db = _table(variables[root_name])
    if source == "GX":
        if type(db.get("version")) is not int or db["version"] != 1:
            raise AssetSchemaError("unsupported_gx_version")
        for key, record in _table(db.get("characters")).items():
            if not isinstance(key, str) or "-" not in key:
                raise AssetSchemaError("invalid_gx_character_key")
            name, realm = key.split("-", 1)
            if not name or not realm:
                raise AssetSchemaError("invalid_gx_character_key")
            record = _table(record)
            owner = _character(batch, name, realm, None, "characters/" + key)
            _cash(batch, owner, "character", record.get("gold"),
                  _timestamp(record.get("lastSeen")), "characters/" + key,
                  requires_time=True)
        warband = _table(db["warband"]) if "warband" in db else {}
        owner = _id(account_scope, region, "warband")
        _cash(batch, owner, "warband", warband.get("gold"),
              _timestamp(warband.get("lastSeen")), "warband", requires_time=True)
    elif source == "BagSync":
        for realm, realm_data in db.items():
            if not isinstance(realm, str):
                raise AssetSchemaError("invalid_bagsync_realm")
            if realm == "warband\u00a7":
                warband = _table(realm_data)
                owner = _id(account_scope, region, "warband")
                _cash(batch, owner, "warband", warband.get("money"), None, realm)
                tabs = warband.get("tabs")
                if tabs is None or not _table(tabs):
                    _container(batch, owner, "warband", "tabs", tabs)
                else:
                    for tab, items in tabs.items():
                        _container(batch, owner, "warband", "tabs/" + str(tab), _table(items))
                continue
            if "\u00a7" in realm:
                continue  # Known BagSync settings namespaces, not owners.
            for name, record in _table(realm_data).items():
                if not isinstance(name, str):
                    raise AssetSchemaError("invalid_bagsync_character")
                if "\u00a9" in name:
                    _warn(batch, "guild_assets_excluded")
                    continue
                if "\u00a7" in name:
                    continue
                record = _table(record)
                # Fail closed on a guild-shaped row without its expected marker.
                if "tabs" in record and "guid" not in record:
                    _warn(batch, "unclassified_owner_excluded")
                    continue
                owner = _character(batch, name, realm, record.get("guid"), realm + "/" + name)
                _cash(batch, owner, "character", record.get("money"), None, realm + "/" + name)
                _containers(batch, owner, record)
        if not any(row["owner_kind"] == "warband" for row in batch["cash"]):
            _cash(batch, _id(account_scope, region, "warband"), "warband", None, None, "warband\u00a7")
            _container(batch, _id(account_scope, region, "warband"), "warband", "tabs", None)
        _warn(batch, "inventory_completeness_and_binding_unknown")
    else:
        repositories = []
        for name, repository in db.items():
            if isinstance(name, str) and name.endswith("DB") and isinstance(repository, dict):
                data = repository.get("data")
                version = repository.get("version")
                if version is not None:
                    version = _integer(version)
                repositories.append({"name": name, "version": version,
                                     "entry_count": len(data) if isinstance(data, dict) else None})
        cost_repo = db.get("lastCraftingCostDB")
        records = []
        if cost_repo is not None:
            cost_repo = _table(cost_repo)
            if type(cost_repo.get("version")) is not int or cost_repo["version"] != 1:
                _warn(batch, "unsupported_last_crafting_cost_version")
            else:
                for item, crafters in _table(cost_repo.get("data")).items():
                    for crafter, record in _table(crafters).items():
                        record = _table(record)
                        cost = record.get("cost")
                        if type(cost) not in {int, float} or not math.isfinite(cost) or cost < 0:
                            raise AssetSchemaError("invalid_cached_crafting_cost")
                        # This expected/model cost may be fractional, unlike cash.
                        records.append({"item_key": str(item), "crafter_key": str(crafter),
                                        "expected_cost_copper_decimal": str(cost),
                                        "observed_at": _timestamp(record.get("timestamp")),
                                        "provenance": "cached_model_estimate"})
        batch["craftsim"] = {"repositories": repositories, "last_crafting_costs": records,
                             "calculation_status": "missing_data",
                             "missing_fields": ["runtime_recipe_inputs", "outcomes", "fingerprints"],
                             "engine_evaluation_available": False}
        _warn(batch, "craftsim_cache_is_not_engine_evaluation")
    return batch


def reconcile_assets(normalized_batches: list[dict]) -> dict:
    """Project latest snapshots without summing alternative observations.

    This deliberately exposes unresolved identities and mixed observation times.
    It does not compute liquid inventory value or present balances as current.
    """
    batches = deepcopy(normalized_batches)
    seen = set()
    scope_groups = {}
    for batch in batches:
        key = (batch["source"], batch["account_scope"], batch["region"])
        if key in seen:
            raise AssetSchemaError("reconciliation_requires_latest_snapshots_only")
        seen.add(key)
        scope = (batch["account_scope"], batch["region"])
        if scope in scope_groups and scope_groups[scope] != batch["ownership_group"]:
            raise AssetSchemaError("inconsistent_ownership_group_for_account")
        scope_groups[scope] = batch["ownership_group"]
    aliases = defaultdict(list)
    mapping = {}
    conflicts = []
    for batch in batches:
        for char in batch["characters"]:
            if batch["source"] == "BagSync":
                canonical = _id("guid", batch["region"],
                                None if batch["region"] else batch["account_scope"], char["guid"]) if char["guid"] else char["owner_key"]
                mapping[char["owner_key"]] = canonical
                aliases[(batch["account_scope"], batch["region"], char["alias"])].append(canonical)
    ambiguous = set()
    gx_aliases = defaultdict(list)
    for batch in batches:
        if batch["source"] == "GX":
            for char in batch["characters"]:
                gx_aliases[(batch["account_scope"], batch["region"], char["alias"])].append(char["owner_key"])
    for batch in batches:
        for char in batch["characters"]:
            if batch["source"] != "GX":
                continue
            candidates = sorted(set(aliases[(batch["account_scope"], batch["region"], char["alias"])]))
            duplicate_alias = len(gx_aliases[(batch["account_scope"], batch["region"], char["alias"])]) > 1
            if len(candidates) == 1 and not duplicate_alias:
                mapping[char["owner_key"]] = candidates[0]
            else:
                mapping[char["owner_key"]] = char["owner_key"]
                if len(candidates) > 1 or duplicate_alias:
                    ambiguous.update(candidates + [char["owner_key"]])
                    conflicts.append({"type": "ambiguous_character_alias", "owner_key": char["owner_key"]})
    # A shared ownership assertion resolves warbanks, not identity of archived
    # name-only records from two licenses. Require a common GUID-backed mapping.
    grouped_aliases = defaultdict(set)
    for batch in batches:
        if batch["source"] == "GX":
            for char in batch["characters"]:
                group_key = (batch["region"], batch["ownership_group"] or batch["account_scope"],
                             bool(batch["ownership_group"]), char["alias"])
                grouped_aliases[group_key].add(mapping[char["owner_key"]])
    for owners in grouped_aliases.values():
        if len(owners) > 1:
            ambiguous.update(owners)
            conflicts.append({"type": "ambiguous_cross_account_character_alias"})
    observations = defaultdict(list)
    warband_scopes = defaultdict(set)
    for batch in batches:
        for row in batch["cash"]:
            if row["owner_kind"] == "warband":
                group = batch["ownership_group"]
                canonical = _id("warband", batch["region"], group if group else batch["account_scope"], bool(group))
                mapping[row["owner_key"]] = canonical
                warband_scopes[batch["region"]].add((batch["account_scope"], group))
            else:
                canonical = mapping[row["owner_key"]]
            row.update(source=batch["source"], account_scope=batch["account_scope"],
                       canonical_owner_key=canonical)
            observations[canonical].append(row)
    unresolved_ownership = any(len({scope for scope, _ in values}) > 1
                               and any(group is None for _, group in values)
                               for values in warband_scopes.values())
    if unresolved_ownership:
        conflicts.append({"type": "cross_account_warband_ownership_unknown"})
    selected = []
    for owner, rows in sorted(observations.items()):
        known = [r for r in rows if r["copper"] is not None]
        timed = [r for r in known if r["observed_at"] is not None]
        choice = None
        reason = "unknown"
        if timed:
            latest = max(r["observed_at"] for r in timed)
            candidates = [r for r in timed if r["observed_at"] == latest]
            if len({r["copper"] for r in candidates}) == 1:
                choice, reason = candidates[0], "latest_explicit_observation"
        elif known and len({r["copper"] for r in known}) == 1:
            choice, reason = known[0], "untimed_observation"
        different = len({r["copper"] for r in known}) > 1
        if different:
            conflicts.append({"type": "cash_disagreement", "owner_key": owner,
                              "selected_by": reason, "current_amount_unknown": True})
        if owner in ambiguous:
            choice, reason = None, "ambiguous_identity"
        selected.append({"owner_key": owner, "owner_kind": rows[0]["owner_kind"],
                         "copper": choice["copper"] if choice else None,
                         "observed_at": choice["observed_at"] if choice else None,
                         "source": choice["source"] if choice else None,
                         "selection_reason": reason, "conflict": different,
                         "alternatives": rows})
    containers, items = [], []
    for batch in batches:
        for row in batch["containers"]:
            row.update(source=batch["source"], canonical_owner_key=mapping.get(row["owner_key"], row["owner_key"]))
            containers.append(row)
        for row in batch["items"]:
            row.update(source=batch["source"], canonical_owner_key=mapping.get(row["owner_key"], row["owner_key"]))
            items.append(row)
    inventory_observations = items
    observed_by_container = defaultdict(list)
    for row in items:
        observed_by_container[row["container_key"]].append(row)
    container_groups = defaultdict(list)
    for row in containers:
        canonical = _id(row["canonical_owner_key"], row["kind"], row["source_key"])
        container_groups[canonical].append(row)

    def content_signature(container: dict) -> str:
        # List ordinal is not a physical slot; compare a multiset, not ordering.
        contents = sorted((item["variant_key"], item["quantity"])
                          for item in observed_by_container[container["container_key"]])
        return _id(container["present"], contents)

    containers, items = [], []
    for canonical, alternatives in sorted(container_groups.items()):
        present = [c for c in alternatives if c["present"]]
        timed = [c for c in present if c["observed_at"] is not None]
        candidates = present
        reason = "untimed_observation"
        if timed:
            latest = max(c["observed_at"] for c in timed)
            candidates = [c for c in timed if c["observed_at"] == latest]
            reason = "latest_explicit_observation"
        choice = candidates[0] if candidates and len({content_signature(c) for c in candidates}) == 1 else None
        different = len({content_signature(c) for c in present}) > 1
        if different:
            conflicts.append({"type": "inventory_disagreement", "container_key": canonical,
                              "current_contents_unknown": True})
        projected = {
            "container_key": canonical, "owner_key": alternatives[0]["canonical_owner_key"],
            "kind": alternatives[0]["kind"], "source_key": alternatives[0]["source_key"],
            "observed_at": choice["observed_at"] if choice else None, "complete": None,
            "selection_reason": reason if choice else "unknown_or_conflicting",
            "selected_source_container_key": choice["container_key"] if choice else None,
            "present": bool(present), "conflict": different, "alternatives": alternatives,
        }
        containers.append(projected)
        if choice:
            for item in observed_by_container[choice["container_key"]]:
                items.append({**item, "source_container_key": item["container_key"],
                              "container_key": canonical, "owner_key": choice["canonical_owner_key"]})
    locations = defaultdict(set)
    for row in items:
        locations[row["variant_key"]].add(row["container_key"])
    overlaps = sum(len(v) > 1 for v in locations.values())
    incomplete = not selected or any(row["copper"] is None for row in selected) or bool(ambiguous) or unresolved_ownership
    return {
        "cash": selected, "identity_mapping": mapping, "conflicts": conflicts,
        "observed_cash_copper": None if incomplete else sum(row["copper"] for row in selected),
        "cash_status": "incomplete" if incomplete else "mixed_time_observations",
        "current_cash_copper": None, "current_nav_copper": None,
        "containers": containers, "items": items, "inventory_observations": inventory_observations,
        "inventory_current_total": None, "possible_stale_overlap_variants": overlaps,
        "warnings": sorted({w for b in batches for w in b["warnings"]}
                           | {"observed_cash_is_not_profit_or_current_nav",
                              "inventory_not_valued_without_tradeability_and_prices"}),
    }
