"""Pinned discovery source policy; importing data never executes source Lua."""

ATT_REVISION = "3d0461d9740940e09c9cb19390467ed6ec39cb9c"
ATT_TREE = "a1bf6ba088a155e71449d1009f4ae21967427b6f"
ATT_STANDARD_TREE = "5895efadd3d10995982defa777d8d5cca3b371e7"
ATT_STANDARD = ".contrib/.db/standard/"
ATT_SHARED = ".contrib/.db/shared/"
ATT_BUILD = "12.1.5.70575"
ATT_LICENSE_BLOB = "8c87dc3b43c3fa36652093b325fbbc0910cd57d8"
REQUIRED_JSON = {ATT_STANDARD + "00 - Item DB/itemDB.json", ATT_STANDARD + "00 - Item DB/questDB.json"}


def source_policy(revision: str = ATT_REVISION, tree: str | None = None) -> dict:
    return {
        "source_id": "att-standard", "revision": revision,
        "tree": tree or (ATT_TREE if revision == ATT_REVISION else None),
        "standard_tree": ATT_STANDARD_TREE if revision == ATT_REVISION else None,
        "build": ATT_BUILD if revision == ATT_REVISION else None,
        "license": "MIT: pinned author Lua/configuration only",
        "notice_path": "LICENSE", "network_hosts": ["api.github.com", "raw.githubusercontent.com"],
        "raw_local": True, "cloud": False, "redistribute_dataset": False,
        "expected_standard_lua_files": 1874 if revision == ATT_REVISION else None,
        "dependency_scope": "all shared Lua/configuration; standard configuration except imported Wago",
        "blocked": [
            {"source_id": "att-required-json-base", "disposition": "rights_provenance_blocked",
             "paths": sorted(REQUIRED_JSON), "required_dependency": True,
             "reason": "ATT Parser loads these before Lua; harvested external item/quest data pipeline is documented but artifact-wide reuse rights and per-row provenance are unresolved",
             "semantic_fields": ["item_metadata", "spell_links", "binding", "quest_rewards", "requirements", "repeatable"],
             "recipe_outputs_or_joint_pmf_confirmed": False},
            {"source_id": "att-missing-db-enumerations", "disposition": "rights_provenance_blocked",
             "root": ATT_STANDARD + "00 - Missing DB/", "required_dependency": True,
             "reason": "Harvester combines Wago DB2 enumerations with parser debug differences; generated lists are not excluded from discovery scope, but their upstream reuse policy is unresolved"},
            {"source_id": "att-conduit-metadata", "disposition": "rights_provenance_blocked",
             "root": ATT_STANDARD + "00 - Item DB/Conduits/",
             "reason": "CSV game metadata feeds conduit helper; code MIT is not proof of rights for every imported game-data record"},
            {"source_id": "att-wago-item-links", "disposition": "license_blocked",
             "paths": [ATT_STANDARD + ".config/.wago/12 - Midnight/ItemXItemEffect.12.1.5.70575.csv",
                       ATT_STANDARD + ".config/.wago/12 - Midnight/ItemEffect.12.1.5.70575.csv"],
             "known_rows_from_stage0": [60644, 62149],
             "reason": "upstream_bulk_reuse_rights_unresolved; ATT MIT does not relicense Wago/Blizzard data",
             "previous_read_is_not_current_import": True},
            {"source_id": "tsm-destructive-tables", "disposition": "license_blocked",
             "families": ["disenchant", "mill", "prospect", "transform"],
             "reason": "TSM ARR: installed source research does not authorize redistributable bulk extraction"},
            {"source_id": "full-build-yields", "disposition": "unavailable",
             "families": ["scrap", "obliterate", "disenchant", "mill", "prospect", "container"],
             "reason": "no confirmed lawful complete yield/PMF corpus; ATT SpellEffect is filtered"},
            {"source_id": "blizzard-web-api", "disposition": "disabled_by_user",
             "reason": "explicit_no_oauth_no_blizzard_api"},
        ],
    }


def include_path(path: str) -> bool:
    if path == "LICENSE":
        return True
    if "/.wago/" in path:
        return False
    return ((path.startswith(ATT_STANDARD) or path.startswith(ATT_SHARED))
            and (path.endswith(".lua") or path.endswith(".config")))


def dependency_disposition(path: str) -> dict:
    """Explicit material dependency gaps; these are not removed from scope."""
    if "/.wago/" in path:
        return {"disposition": "license_blocked", "reason": "upstream_Wago_rights_unresolved", "required_dependency": True}
    if path in REQUIRED_JSON:
        return {"disposition": "rights_provenance_blocked", "reason": "required_JSON_base_merge_external_game_data_provenance_and_reuse_unresolved",
                "required_dependency": True, "dependency_role": "Parser.RawJsonMerge_before_Lua"}
    if path.startswith(ATT_STANDARD + "00 - Missing DB/"):
        return {"disposition": "rights_provenance_blocked", "reason": "Wago_and_parser_debug_generated_enumeration_reuse_unresolved",
                "required_dependency": True, "dependency_role": "additional_discovery_ID_index"}
    if "/Conduits/" in path and path.endswith((".csv", ".txt")):
        return {"disposition": "rights_provenance_blocked", "reason": "conduit_game_metadata_provenance_and_reuse_unresolved",
                "required_dependency": True, "dependency_role": "conduit_class_spec_metadata"}
    if path.endswith(".py"):
        return {"disposition": "generator_not_executed", "reason": "helper_source_code_not_a_mechanics_dataset",
                "required_dependency": False}
    if "/Color - " in path and path.endswith(".txt"):
        return {"disposition": "descriptive_note", "reason": "color_style_note_not_a_mechanics_dataset", "required_dependency": False}
    return {"disposition": "unsupported_dependency_format", "reason": "semantic_dependency_role_not_resolved", "required_dependency": None}


def public_get(url: str, limit: int) -> bytes:
    """Public source transport with a production process-enforced deadline."""
    from .knowledge_transport import public_get as isolated_get
    return isolated_get(url, limit)


def resolve_latest_revision(*, fetch=None) -> dict:
    """Resolve once, then use the returned immutable commit for all requests."""
    import json
    import re
    fetch = fetch or public_get
    response = json.loads(fetch("https://api.github.com/repos/ATTWoWAddon/AllTheThings/commits/master", 2 * 1024 * 1024))
    revision, tree = response["sha"], response["commit"]["tree"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise ValueError("invalid_source_revision")
    return {"revision": revision, "tree": tree}


def fetch_corpus(root, revision: str = ATT_REVISION, workers: int = 8, *, fetch=None, progress=None, reuse_root=None) -> dict:
    """Resume by verified blob hashes; a failure never promotes partial content."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timezone
    import hashlib
    import json
    from pathlib import Path, PurePosixPath
    import re
    import time
    from urllib.parse import quote

    if not re.fullmatch(r"[0-9a-f]{40}", revision) or type(workers) is not int or not 1 <= workers <= 12:
        raise ValueError("invalid_fetch_settings")
    fetch = fetch or public_get
    root = Path(root).resolve()
    reuse_root = Path(reuse_root).resolve() if reuse_root is not None else None
    root.mkdir(parents=True, exist_ok=True)
    if revision == ATT_REVISION:
        tree_sha = ATT_TREE
    else:
        commit = json.loads(fetch(f"https://api.github.com/repos/ATTWoWAddon/AllTheThings/git/commits/{revision}", 2 * 1024 * 1024))
        if commit.get("sha") != revision or not re.fullmatch(r"[0-9a-f]{40}", commit.get("tree", {}).get("sha", "")):
            raise ValueError("source_commit_mismatch")
        tree_sha = commit["tree"]["sha"]
        (root / "commit.json").write_text(json.dumps(commit, sort_keys=True), encoding="utf-8")
    tree_path = root / "tree.json"
    tree = json.loads(tree_path.read_text(encoding="utf-8")) if tree_path.exists() else None
    if not tree or tree.get("sha") != tree_sha or tree.get("truncated") is not False:
        raw = fetch(f"https://api.github.com/repos/ATTWoWAddon/AllTheThings/git/trees/{tree_sha}?recursive=1", 8 * 1024 * 1024)
        tree = json.loads(raw)
        if tree.get("sha") != tree_sha or tree.get("truncated") is not False:
            raise ValueError("incomplete_or_wrong_pinned_tree")
        temporary = root / "tree.download"
        temporary.write_bytes(raw); temporary.replace(tree_path)
    entries = [entry for entry in tree["tree"] if entry["type"] == "blob" and include_path(entry["path"])]
    license_entry = next((entry for entry in entries if entry["path"] == "LICENSE"), None)
    if license_entry is None or license_entry.get("sha") != ATT_LICENSE_BLOB:
        raise ValueError("source_license_changed_or_missing_requires_policy_review")
    if len({entry["path"].casefold() for entry in entries}) != len(entries):
        raise ValueError("duplicate_source_paths")
    errors, counts = [], {"cached": 0, "reused": 0, "downloaded": 0}
    start = time.monotonic()

    def blob_hash(data):
        return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()

    def one(entry):
        path = PurePosixPath(entry["path"])
        if path.is_absolute() or ".." in path.parts or ":" in entry["path"] or "\\" in entry["path"]:
            raise ValueError("unsafe_source_path")
        destination = root.joinpath(*path.parts)
        if not destination.resolve().is_relative_to(root):
            raise ValueError("unsafe_source_target")
        if destination.exists() and blob_hash(destination.read_bytes()) == entry["sha"]:
            return "cached"
        if entry.get("size", 0) > 32 * 1024 * 1024:
            raise ValueError("source_file_limit")
        if reuse_root is not None:
            previous = reuse_root.joinpath(*path.parts)
            if previous.resolve().is_relative_to(reuse_root) and previous.is_file() and previous.stat().st_size <= 32 * 1024 * 1024:
                data = previous.read_bytes()
                if blob_hash(data) == entry["sha"]:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(destination.name + ".download")
                    temporary.write_bytes(data); temporary.replace(destination)
                    return "reused"
        url = f"https://raw.githubusercontent.com/ATTWoWAddon/AllTheThings/{revision}/" + quote(entry["path"], safe="/")
        for attempt in range(3):
            try:
                data = fetch(url, 32 * 1024 * 1024)
                if blob_hash(data) != entry["sha"]:
                    raise ValueError("source_blob_hash_mismatch")
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".download")
                temporary.write_bytes(data); temporary.replace(destination)
                return "downloaded"
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, entry): entry for entry in entries}
        for number, future in enumerate(as_completed(futures), 1):
            try:
                counts[future.result()] += 1
            except Exception as exc:
                errors.append({"path": futures[future]["path"], "error": type(exc).__name__})
            if progress and number % 250 == 0:
                progress({"processed": number, "expected": len(entries), **counts, "errors": len(errors)})
    capture = {"source": source_policy(revision, tree_sha), "revision": revision,
               "captured_at": datetime.now(timezone.utc).isoformat(), "root": str(root),
               "tree_sha256": hashlib.sha256(tree_path.read_bytes()).hexdigest(), "tree_entries": len(tree["tree"]),
               "expected": len(entries), **counts, "errors": errors, "complete": not errors,
               "elapsed_seconds": round(time.monotonic() - start, 3)}
    (root / "fetch-capture.json").write_text(json.dumps(capture, indent=2), encoding="utf-8")
    return capture
