"""Read-only reassessment of inherited checks. No game action, database or secret access.

Usage: python -B tools/verify_stage3_prior.py OUTPUT_JSON [MARKET_PROBE_JSON]
The optional market result must come from tools/verify_stage2_market.py. Public
evidence contains counts/hashes only; raw personal inputs stay in .private.
"""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wow_auction.assets import normalize_assets, reconcile_assets
from wow_auction.file_reader import read_stable
from wow_auction.ingestion import load_config, reject_source_destination
from wow_auction.knowledge_items import decode_tsm_item_info
from wow_auction.ledger import normalize_ledger, reconcile_ledger
from wow_auction.lua_data import parse_saved_variables
from wow_auction.storage import Store


BASELINE = {
    "GX": "025bc76a6434d5634177ea37b9d1e45224c6f919c7cd800da85f44adf6cf0607",
    "BagSync": "1aee395169f9b55a042fcbd3cfa5a7dd2b733da6c2b0b2e054ff89da5003c352",
    "TradeSkillMaster": "ede4fe92e33054d359722c2c7c7759a68437fcfc84e4a20bd7ba466ffe94ba85",
    "CraftSim": "d52782702e110c4e94bed540bee0f3269906d1cd9e2ed96908193d2a500e78b6",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_captures(private, sources, ledger):
    """Exercise existing importer in an isolated, deleted temporary database."""
    first, second = [], []
    with tempfile.TemporaryDirectory(prefix="stage3_asset_probe_", dir=".private") as directory, \
            Store(Path(directory) / "probe.sqlite3") as store:
        for destination in (first, second):
            for source in sources:
                raw = (private / (source.addon + ".lua")).read_bytes()
                variables = parse_saved_variables(raw)
                normalized = (normalize_ledger(variables, account_scope=source.account_scope,
                    region=source.region) if source.addon == "TradeSkillMaster" else
                    normalize_assets(source.addon, variables, account_scope=source.account_scope,
                        region=source.region, ownership_group=source.ownership_group))
                accepted = store.accept(source.key, raw, digest(raw), normalized, 0,
                    time.time_ns(), adapter_version=source.adapter_version,
                    reconcile=reconcile_ledger if source.addon == "TradeSkillMaster" else None)
                destination.append({"addon": source.addon, "status": accepted["status"]})
        snapshots = store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        current = next(row for row in store.current() if row["normalized"]["source"] == "TradeSkillMaster")
        canonical_rows = len(current["ledger_state"])
        result = {"database": "isolated_temporary_deleted", "first": first, "second": second,
            "snapshots": snapshots, "ledger_canonical_rows": canonical_rows,
            "integrity": store.db.execute("PRAGMA quick_check").fetchone()[0],
            "foreign_key_errors": len(store.db.execute("PRAGMA foreign_key_check").fetchall()),
            "sale_observations": [{key: row[key] for key in
                ("quantity", "unit_amount_copper", "total_estimate_copper", "total_lower_copper",
                 "total_upper_copper", "price_semantics", "rounding_bounds_status",
                 "event_time_semantics", "ah_cut_already_applied", "profit_eligible")}
                for row in ledger["rows"] if row["family"] == "csvSales" and row["method"] == "Auction"]}
        assert snapshots == len(sources) and canonical_rows == len(ledger["rows"])
        assert all(row["status"] == "unchanged" for row in second)
    result["temporary_database_removed"] = not Path(directory).exists()
    # A real old snapshot is available for two rows, not the entire R2 30-row input.
    old_sha = "5f464a51b30371b0a2d3d4e5d8cf68b0e181d994eda87e3a1fc05540c32d1aa7"
    old = next((path for path in Path(".private/reload-probes").rglob("TradeSkillMaster*.lua")
                if digest(path.read_bytes()) == old_sha), None)
    if old is not None:
        source = next(source for source in sources if source.addon == "TradeSkillMaster")
        prior = normalize_ledger(parse_saved_variables(old.read_bytes()),
            account_scope=source.account_scope, region=source.region)
        comparison = reconcile_ledger(prior["rows"], ledger["rows"])
        again = reconcile_ledger(comparison["rows"], ledger["rows"])
        result["old_pair_comparison"] = {"old_sha256": old_sha,
            "old_rows": len(prior["rows"]), "new_rows": len(ledger["rows"]),
            "revisions_by_kind": dict(Counter(row["kind"] for row in comparison["revisions"])),
            "repeat_events": len(again["events"]), "repeat_revisions": len(again["revisions"]),
            "limitation": "Old two-row capture does not cover all 30 rows observed during stage2 R2"}
    return result


def probe():
    config, sources = load_config(Path(".private/config.local.json"))
    now = datetime.now(timezone.utc)
    private = Path(".private/stage3-prior-inputs") / now.strftime("%Y%m%dT%H%M%S%fZ")
    private.mkdir(parents=True, exist_ok=False)
    root = Path(config["wow_roots"][0])
    build_path = root.parent / ".build.info"
    build = read_stable(build_path)
    build_lines = build.data.decode("utf-8-sig").splitlines()
    headers = [part.split("!")[0] for part in build_lines[0].split("|")]
    builds = [dict(zip(headers, line.split("|"))) for line in build_lines[1:] if line]
    installed = [row["Version"] for row in builds if row.get("Active") == "1" and row.get("Product") == "wow"]
    if len(installed) != 1:
        raise ValueError("expected_one_active_retail_build")
    entries, batches = [], []
    ledger, item_info = None, None
    checked_paths = [(build_path, build.sha256, build.file_written_at_ns)]
    for source in sources:
        stable = read_stable(source.path)
        # Local evidence copy only, never write anything under the game root.
        capture = private / (source.addon + ".lua")
        with capture.open("xb") as stream:
            stream.write(stable.data)
        variables = parse_saved_variables(stable.data)
        entries.append({"addon": source.addon, "sha256": stable.sha256,
                        "bytes": stable.size_bytes, "written_at_ns": stable.file_written_at_ns,
                        "changed_since_stage2_r2": stable.sha256 != BASELINE[source.addon]})
        checked_paths.append((source.path, stable.sha256, stable.file_written_at_ns))
        if source.addon == "TradeSkillMaster":
            ledger = normalize_ledger(variables, account_scope=source.account_scope, region=source.region)
            item_info = decode_tsm_item_info(variables, expected_build=installed[0])
        else:
            batches.append(normalize_assets(source.addon, variables, account_scope=source.account_scope,
                region=source.region, ownership_group=source.ownership_group))
    assert ledger is not None and item_info is not None
    bag = next(batch for batch in batches if batch["source"] == "BagSync")
    craft = next(batch for batch in batches if batch["source"] == "CraftSim")
    asset_view = reconcile_assets(batches)
    by_key = {row["source_item_string"]: row for row in item_info["items"]}
    base_match = Counter()
    for row in bag["items"]:
        meta = by_key.get("i:" + str(row["item_id"])) if row["item_id"] is not None else None
        flag = meta["fields"]["isBOP"] if meta else None
        base_match["no_base_match" if meta is None else "base_flag_unknown" if flag is None
                   else "base_flag_bop" if flag == 1 else "base_flag_not_bop"] += 1
    source_paths = [
        "LibTSMTypes/Source/Item/Classes/ItemInfoSerializer.lua",
        "LibTSMTypes/Source/Item/ItemInfoCache.lua",
        "LibTSMUtil/Source/BaseType/LongString.lua",
        "LibTSMService/Source/Item/ItemInfo.lua",
        "LibTSMWoW/Source/API/Item.lua",
        "LibTSMApp/Source/Config/ItemInfoConfig.lua",
    ]
    source_hashes = {name: digest((root / "Interface/AddOns/TradeSkillMaster" / name).read_bytes())
                     for name in source_paths}
    # Record only aggregate metadata, never names/characters/wallets/item rows.
    result = {
        "kind": "REAL_READ_ONLY", "captured_at_utc": now.isoformat(),
        "review_register_sha256": digest(Path("docs/reviews/open-verifications.md").read_bytes()),
        "review_stage2_r2_sha256": digest(Path("docs/reviews/stage-2-review.md").read_bytes()),
        "build": installed[0], "build_info_sha256": build.sha256,
        "saved_variables": entries, "account_scopes": len({source.account_scope for source in sources}),
        "ledger": {"rows": len(ledger["rows"]),
            "by_family": dict(Counter(row["family"] for row in ledger["rows"])),
            "by_method": dict(Counter((row["method"] or "none") for row in ledger["rows"])),
            "by_money_type": dict(Counter((row["money_type"] or "none") for row in ledger["rows"])),
            "auction_sales": sum(row["family"] == "csvSales" and row["method"] == "Auction" for row in ledger["rows"]),
            "classification": dict(Counter(row["classification"] for row in ledger["rows"])),
            "pnl_status": ledger["pnl_status"]},
        "assets": {"item_rows": len(bag["items"]),
            "by_location": dict(Counter(row["kind"] for row in bag["items"])),
            "by_tradeability": dict(Counter(row["tradeability"] for row in bag["items"])),
            "containers_without_observed_at": sum(row["observed_at"] is None for row in bag["containers"]),
            "cash_observations": sum(len(batch["cash"]) for batch in batches),
            "selected_cash_observations": len(asset_view["cash"]),
            "cash_conflicts": len(asset_view["conflicts"]),
            "current_cash_copper_unknown": asset_view["current_cash_copper"] is None,
            "current_nav_copper_unknown": asset_view["current_nav_copper"] is None},
        "craftsim": {"repositories": craft["craftsim"]["repositories"],
            "last_crafting_cost_count": len(craft["craftsim"]["last_crafting_costs"]),
            "calculation_status": craft["craftsim"]["calculation_status"],
            "engine_evaluation_available": craft["craftsim"]["engine_evaluation_available"]},
        "item_metadata": {key: value for key, value in item_info.items() if key != "items"},
        "metadata_statistics": {
            "kind_counts": dict(Counter(row["kind"] for row in item_info["items"])),
            "non_base_source_keys": sum(row["source_item_string"].count(":") > 1 for row in item_info["items"]),
            "isBOP_counts": dict(Counter(str(row["fields"]["isBOP"]) for row in item_info["items"])),
            "inventory_base_id_matches_only": dict(base_match)},
        "installed_tsm_contract_source_sha256": source_hashes,
        "limitations": ["No independent game UI observation", "No real PC sleep or offline/backfill experiment",
            "No source-policy change", "No UE or Blizzard access", "No automatic closure of reviewer checks",
            "New metadata does not establish binding of a physical inventory instance"],
        "local_evidence_directory": private.as_posix(),
        "existing_database_files_opened": False, "secrets_read": False, "game_actions": False,
        "import_validation": validate_captures(private, sources, ledger),
    }
    # Source files can be changed by WoW while the probe runs: do not hide it.
    result["source_files_still_same_after_read"] = all(
        read_stable(path).sha256 == sha and path.stat().st_mtime_ns == mtime
        for path, sha, mtime in checked_paths)
    if not result["source_files_still_same_after_read"]:
        raise ValueError("game_inputs_changed_during_probe")
    return result, config


def main():
    output = Path(sys.argv[1])
    if output.exists():
        raise SystemExit("Evidence exists; choose a new filename")
    result, config = probe()
    reject_source_destination(output, [Path(root) for root in config["wow_roots"]])
    if len(sys.argv) > 2:
        new_path = Path(sys.argv[2])
        new = json.loads(new_path.read_text(encoding="utf-8"))
        previous_path = Path("docs/stage2-r1-final-live-evidence.json")
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        old_by_dataset = {row["dataset"]: row for row in previous["snapshots"]}
        comparisons = []
        for snapshot in new["snapshots"]:
            old = old_by_dataset[snapshot["dataset"]]
            comparisons.append({"dataset": snapshot["dataset"], "sha256": snapshot["sha256"],
                "source_snapshot_at": snapshot["source_snapshot_at"],
                "received_at_ns": snapshot["received_at_ns"], "available_at_ns": snapshot["available_at_ns"],
                "record_count": snapshot["record_count"],
                "changed_sha256": snapshot["sha256"] != old["sha256"],
                "changed_source_clock": snapshot["source_snapshot_at"] != old["source_snapshot_at"]})
        result["market"] = {"captured_at_utc": new["captured_at_utc"],
            "probe_sha256": digest(new_path.read_bytes()),
            "comparison_baseline": previous_path.as_posix(),
            "request_statuses": [r["http_status"] for r in new["requests"]],
            "secret_loader_calls": new["secret_loader_calls"],
            "authorization_sent": any(r["authorization_sent"] for r in new["requests"]),
            "snapshots": comparisons, "integrity": new["integrity"],
            "elapsed_seconds": new["elapsed_seconds"],
            "temporary_raw_database_removed": new["temporary_raw_database_removed"]}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"output": output.as_posix(), "ledger": result["ledger"],
                      "item_metadata_rows": result["item_metadata"]["row_count"],
                      "source_files_still_same_after_read": result["source_files_still_same_after_read"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
