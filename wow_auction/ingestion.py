"""Local configuration, one-shot ingestion and redacted diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from .assets import AssetError, normalize_assets, reconcile_assets
from .file_reader import StableReadError, read_stable
from .ledger import LedgerError, normalize_ledger, reconcile_ledger
from .lua_data import LuaDataError, parse_saved_variables
from .storage import Store, StorageError, encode

SOURCES = ("GX", "BagSync", "TradeSkillMaster", "CraftSim")
REGIONS = {"EU", "US", "KR", "TW", "CN"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    addon: str
    path: Path
    account_scope: str
    region: str | None = None
    ownership_group: str | None = None

    @property
    def key(self) -> str:
        return hashlib.sha256(encode([self.account_scope, self.addon]).encode()).hexdigest()

    @property
    def adapter_version(self) -> str:
        return "2:" + hashlib.sha256(encode([self.region, self.ownership_group]).encode()).hexdigest()


def reject_source_destination(destination: Path, roots: list[Path]) -> None:
    resolved = destination.resolve()
    if any(resolved == root.resolve() or root.resolve() in resolved.parents for root in roots):
        raise ConfigError("output_must_not_be_inside_wow")
    # Catch accidentally naming game data even if the wrong root was supplied.
    if destination.suffix.lower() in {".lua", ".bak"}:
        raise ConfigError("output_must_not_be_game_file")


def discover(wow_root: Path, *, region: str | None = None) -> list[dict]:
    if region is not None and region not in REGIONS:
        raise ConfigError("unsupported_region")
    account_root = wow_root / "WTF" / "Account"
    if not account_root.is_dir():
        raise ConfigError("retail_account_directory_not_found")
    found = []
    # Only account-wide SavedVariables: never enumerate authentication files.
    for account in sorted(account_root.iterdir()):
        if not account.is_dir() or account.is_symlink():
            continue
        saved = account / "SavedVariables"
        if not saved.is_dir() or saved.is_symlink():
            continue
        scope = hashlib.sha256(str(account.resolve()).casefold().encode()).hexdigest()[:24]
        for addon in SOURCES:
            path = saved / (addon + ".lua")
            if path.is_file():
                found.append({"addon": addon, "path": str(path.resolve()), "account_scope": scope,
                              "region": region, "ownership_group": None})
    return found


def init_config(path: Path, wow_root: Path, *, region: str | None = None) -> dict:
    reject_source_destination(path, [wow_root])
    if path.exists():
        raise ConfigError("configuration_already_exists")
    sources = discover(wow_root, region=region)
    if not sources:
        raise ConfigError("no_supported_saved_variables")
    config = {"schema_version": 1, "wow_roots": [str(wow_root.resolve())],
              "database": str((path.parent / "local.sqlite3").resolve()),
              "settle_seconds": 0.1, "sources": sources}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return config


def load_config(path: Path) -> tuple[dict, list[Source]]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ConfigError("configuration_size_limit")
        config = json.loads(raw)
        if config["schema_version"] != 1:
            raise ConfigError("unsupported_configuration_version")
        roots = [Path(p) for p in config["wow_roots"]]
        if not roots or not all(p.is_absolute() for p in roots):
            raise ConfigError("absolute_wow_roots_required")
        db = Path(config["database"])
        if not db.is_absolute():
            raise ConfigError("absolute_database_path_required")
        reject_source_destination(db, roots)
        settle = config.get("settle_seconds", 0.1)
        if type(settle) not in (float, int) or not 0 <= settle <= 10:
            raise ConfigError("invalid_settle_seconds")
        sources = []
        seen_keys, seen_paths, policies = set(), set(), {}
        for row in config["sources"]:
            source = Source(row["addon"], Path(row["path"]), row["account_scope"],
                            row.get("region"), row.get("ownership_group"))
            if source.addon not in SOURCES or source.path.name != source.addon + ".lua":
                raise ConfigError("unsupported_source")
            if not source.path.is_absolute() or not any(root.resolve() in source.path.resolve().parents for root in roots):
                raise ConfigError("source_outside_declared_wow_roots")
            if not isinstance(source.account_scope, str) or not source.account_scope or len(source.account_scope) > 128:
                raise ConfigError("invalid_account_scope")
            if source.region is not None and source.region not in REGIONS:
                raise ConfigError("unsupported_region")
            if source.ownership_group is not None and (not isinstance(source.ownership_group, str) or not source.ownership_group or len(source.ownership_group) > 128):
                raise ConfigError("invalid_ownership_group")
            if source.account_scope in policies and policies[source.account_scope] != (source.region, source.ownership_group):
                raise ConfigError("inconsistent_account_policy")
            policies[source.account_scope] = (source.region, source.ownership_group)
            resolved = str(source.path.resolve()).casefold()
            if source.key in seen_keys or resolved in seen_paths:
                raise ConfigError("duplicate_source_configuration")
            seen_keys.add(source.key)
            seen_paths.add(resolved)
            sources.append(source)
        if not sources:
            raise ConfigError("no_configured_sources")
        return config, sources
    except ConfigError:
        raise
    except (ValueError, KeyError, TypeError, OSError):
        raise ConfigError("invalid_configuration") from None


def import_source(store: Store, source: Source, *, settle_seconds: float = 0.1,
                  fault=None) -> dict:
    stable = None
    received = time.time_ns()
    try:
        stable = read_stable(source.path, settle_seconds=settle_seconds)
        received = time.time_ns()
        variables = parse_saved_variables(stable.data)
        if source.addon == "TradeSkillMaster":
            normalized = normalize_ledger(variables, account_scope=source.account_scope, region=source.region)
            reconcile = reconcile_ledger
        else:
            normalized = normalize_assets(source.addon, variables, account_scope=source.account_scope,
                                           region=source.region, ownership_group=source.ownership_group)
            reconcile = None
        normalized["file_written_at_ns"] = stable.file_written_at_ns
        normalized["provenance"] = {"source": source.addon, "adapter_version": source.adapter_version,
                                    "source_sha256": stable.sha256, "game_build": None,
                                    "game_build_status": "not_exported_by_source",
                                    "installed_addon_version": None, "version_evidence": "unknown"}
        # An installed TOC is metadata, never proof of a running addon version.
        if len(source.path.parents) >= 5 and source.path.parents[3].name == "WTF":
            toc = source.path.parents[4] / "Interface" / "AddOns" / source.addon / (source.addon + ".toc")
            try:
                with toc.open("rb") as stream:
                    raw_toc = stream.read(65537)
                if len(raw_toc) <= 65536:
                    versions = [line.partition(":")[2].strip() for line in raw_toc.decode("utf-8-sig").splitlines()
                                if line.startswith("## Version:")]
                    if len(versions) == 1 and len(versions[0]) < 128:
                        normalized["provenance"].update(installed_addon_version=versions[0],
                                                       version_evidence="installed_toc_not_runtime")
            except (OSError, UnicodeError):
                pass
        result = store.accept(source.key, stable.data, stable.sha256, normalized,
                              stable.file_written_at_ns, received, adapter_version=source.adapter_version,
                              reconcile=reconcile, fault=fault)
        return {"addon": source.addon, "source_key": source.key, **result,
                "sha256": stable.sha256, "size_bytes": stable.size_bytes}
    except (StableReadError, LuaDataError, AssetError, LedgerError, StorageError) as exc:
        code = getattr(exc, "code", str(exc))
        store.reject(source.key, code, raw=stable.data if stable else None,
                     sha256=stable.sha256 if stable else None,
                     file_written_at_ns=stable.file_written_at_ns if stable else None,
                     received_at_ns=received)
        return {"addon": source.addon, "source_key": source.key, "status": "quarantined", "error_code": code}


def status(store: Store, sources: list[Source], *, details: bool = False) -> dict:
    keys = {s.key for s in sources}
    stored = [r for r in store.current() if r["source_key"] in keys]
    versions = {s.key: s.adapter_version for s in sources}
    current = [r for r in stored if r["adapter_version"] == versions[r["source_key"]]]
    batches = [r["normalized"] for r in current if r["normalized"]["source"] != "TradeSkillMaster"]
    assets = reconcile_assets(batches)
    report: dict[str, Any] = {"stage": 1, "source_count": len(sources),
                              "accepted_sources": len(current), "real_pnl_copper": None,
                              "virtual_portfolio": "not_implemented_stage_5",
                              "assets": assets if details else {
                                  "characters_observed": sum(r["owner_kind"] == "character" for r in assets["cash"]),
                                  "selected_cash_observations": len(assets["cash"]),
                                  "cash_status": assets["cash_status"],
                                  "conflict_count": len(assets["conflicts"]),
                                  "inventory_observation_rows": sum(len(b["items"]) for b in batches),
                                  "selected_inventory_rows": len(assets["items"]),
                                  "current_nav_copper": None,
                                  "private_values_hidden": True}, "sources": []}
    by_key = {r["source_key"]: r for r in current}
    now = time.time_ns()
    for source in sources:
        row = by_key.get(source.key)
        item = {"addon": source.addon, "source_key": source.key}
        if not row:
            attempt = store.db.execute("SELECT status,error_code FROM attempts WHERE source_key=? ORDER BY id DESC LIMIT 1", (source.key,)).fetchone()
            item["health"] = {"status": "requires_reimport"} if any(r["source_key"] == source.key for r in stored) else (dict(attempt) if attempt else {"status": "not_imported"})
        else:
            batch = row["normalized"]
            item.update({"sha256": row["sha256"], "snapshot_id": row["id"],
                         "file_written_at_ns": row["file_written_at_ns"],
                         "received_at_ns": row["received_at_ns"], "available_at_ns": row["available_at_ns"],
                         "file_age_seconds": max(0, (now - row["file_written_at_ns"]) // 1_000_000_000),
                         "file_clock_ahead": row["file_written_at_ns"] > now,
                         "health": row["health"], "warnings": batch["warnings"]})
            if source.addon == "TradeSkillMaster":
                item["ledger_rows"] = len(batch["rows"])
                item["scope_family_count"] = len(batch["scopes"])
                item["nonempty_ledger_observed"] = bool(batch["rows"])
                item["invoice_validation"] = "not_recorded"
                item["ledger_context_status"] = row["ledger_context_status"]
                links = row["manual_links"]
                item["manual_link_counts"] = {state: sum(link["status"] == state for link in links)
                                              for state in ("confirmed", "requires_review", "superseded", "revoked")}
                if details:
                    item["ledger_state"] = row["ledger_state"]
                    item["manual_links"] = links
            elif source.addon == "CraftSim":
                craft = batch["craftsim"]
                item["craftsim"] = craft if details else {
                    "calculation_status": craft["calculation_status"],
                    "engine_evaluation_available": False,
                    "repository_count": len(craft["repositories"]),
                    "cached_cost_count": len(craft["last_crafting_costs"]),
                    "missing_fields": craft["missing_fields"],
                }
            if details:
                item["normalized"] = batch
        report["sources"].append(item)
    return report
