"""Atomic local observations and ledger projections, with no writes to source files."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .ledger import LedgerError, normalize_ledger
from .ledger_context import replay_context, state_context_matches, transition_context
from .lua_data import LuaDataError, parse_saved_variables
from . import manual_links


class StorageError(ValueError):
    """Safe error code, never an SQL statement, path or private record."""


MIGRATIONS = (
    (
        "CREATE TABLE snapshots (id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, "
        "sha256 TEXT NOT NULL, adapter_version TEXT NOT NULL, raw BLOB NOT NULL, "
        "normalized TEXT NOT NULL, received_at_ns INTEGER NOT NULL, available_at_ns INTEGER, "
        "file_written_at_ns INTEGER NOT NULL)",
        "CREATE TABLE current_sources (source_key TEXT PRIMARY KEY, snapshot_id INTEGER NOT NULL "
        "REFERENCES snapshots(id), ledger_state TEXT)",
        "CREATE TABLE attempts (id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, sha256 TEXT, "
        "received_at_ns INTEGER NOT NULL, file_written_at_ns INTEGER, status TEXT NOT NULL, "
        "error_code TEXT, raw BLOB, snapshot_id INTEGER REFERENCES snapshots(id))",
        "CREATE TABLE ledger_changes (id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL "
        "REFERENCES snapshots(id), payload TEXT NOT NULL)",
    ),
    (
        "CREATE TABLE manual_links (id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, "
        "from_canonical_id TEXT NOT NULL, to_canonical_id TEXT NOT NULL, quantity INTEGER NOT NULL "
        "CHECK(quantity>0), note TEXT NOT NULL, created_at_ns INTEGER NOT NULL, "
        "supersedes_id INTEGER REFERENCES manual_links(id))",
    ),
    manual_links.MIGRATION_SQL + (
        "CREATE TABLE ledger_context_resolutions (id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, "
        "snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), prior_changes_through INTEGER NOT NULL, "
        "reason TEXT NOT NULL, backup_sha256 TEXT NOT NULL, created_at_ns INTEGER NOT NULL)",
        "CREATE VIEW effective_ledger_changes AS SELECT l.* FROM ledger_changes l "
        "JOIN snapshots s ON s.id=l.snapshot_id WHERE l.id > COALESCE("
        "(SELECT MAX(r.prior_changes_through) FROM ledger_context_resolutions r WHERE r.source_key=s.source_key),0)",
    ),
)
APPLICATION_ID = 0x57414931


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path: Path | str, *, migrate_to: int = len(MIGRATIONS)):
        self.path = Path(path)
        if self.path.is_symlink() or str(self.path).startswith("\\\\") or self.path.suffix.lower() in {".lua", ".bak"}:
            raise StorageError("unsupported_database_location")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=5000")
            if hasattr(self.db, "setconfig"):
                self.db.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
            # The installed 3.50.4 is not a fixed WAL runtime. Never open a WAL book here.
            if self.db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
                raise StorageError("rollback_journal_required")
            self._migrate(migrate_to)
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise StorageError("database_integrity_failed")
            self._publish_committed()
        except BaseException:
            self.db.close()
            raise

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def _migrate(self, target: int) -> None:
        if not 1 <= target <= len(MIGRATIONS):
            raise StorageError("unsupported_schema_version")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            app_id = self.db.execute("PRAGMA application_id").fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app_id not in (0, APPLICATION_ID) or (app_id == 0 and tables):
                raise StorageError("foreign_database")
            current = self.db.execute("PRAGMA user_version").fetchone()[0]
            if current > target:
                raise StorageError("newer_schema_requires_newer_application")
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                            "(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at_ns INTEGER NOT NULL)")
            recorded = self.db.execute("SELECT version,checksum FROM schema_migrations ORDER BY version").fetchall()
            expected = [(n, hashlib.sha256(encode(MIGRATIONS[n - 1]).encode()).hexdigest())
                        for n in range(1, current + 1)]
            if [tuple(r) for r in recorded] != expected:
                raise StorageError("migration_history_mismatch")
            for n in range(current + 1, target + 1):
                for statement in MIGRATIONS[n - 1]:
                    self.db.execute(statement)
                digest = hashlib.sha256(encode(MIGRATIONS[n - 1]).encode()).hexdigest()
                self.db.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (n, digest, time.time_ns()))
                self.db.execute(f"PRAGMA user_version={n}")
            self.db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def reject(self, source_key: str, code: str, *, raw: bytes | None = None,
               sha256: str | None = None, file_written_at_ns: int | None = None,
               received_at_ns: int | None = None) -> None:
        if raw is not None and self.db.execute("SELECT 1 FROM attempts WHERE source_key=? AND sha256=? "
                                               "AND raw IS NOT NULL LIMIT 1", (source_key, sha256)).fetchone():
            raw = None  # A repeated poll is another observation, not another private blob copy.
        self.db.execute("INSERT INTO attempts(source_key,sha256,received_at_ns,file_written_at_ns,"
                        "status,error_code,raw) VALUES (?,?,?,?,?,?,?)",
                        (source_key, sha256, received_at_ns or time.time_ns(), file_written_at_ns,
                         "quarantined", code, raw))

    def _publish_committed(self) -> None:
        # Called only OUTSIDE the ingestion transaction. The timestamp is sampled
        # after its durable commit. A crash in this gap leaves pending metadata;
        # recovery publishes it later, never backdates availability or reapplies events.
        # Obtain the writer lock BEFORE sampling time. A concurrent writer can
        # otherwise commit while this UPDATE waits and receive an earlier time.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE snapshots SET available_at_ns=? WHERE available_at_ns IS NULL", (time.time_ns(),))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def accept(self, source_key: str, raw: bytes, sha256: str, normalized: dict,
               file_written_at_ns: int, received_at_ns: int, *, adapter_version: str = "1",
               reconcile: Callable | None = None, fault: Callable[[str], None] | None = None) -> dict:
        """Snapshot, reconciled state, change journal and checkpoint commit together."""
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise StorageError("raw_hash_mismatch")
        hit = fault or (lambda _: None)
        self._publish_committed()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.db.execute("SELECT s.*,c.ledger_state FROM current_sources c JOIN snapshots s "
                                      "ON s.id=c.snapshot_id WHERE c.source_key=?", (source_key,)).fetchone()
            previous = json.loads(current["ledger_state"]) if current and current["ledger_state"] else []
            old_batch = json.loads(current["normalized"]) if current else None
            contaminated = bool(reconcile and current and not state_context_matches(previous, old_batch))
            unresolved = bool(old_batch and old_batch.get("ledger_context_status") == "requires_resolution")
            if current and current["sha256"] == sha256 and current["adapter_version"] == adapter_version and not contaminated:
                status = "unchanged"
                self.db.execute("INSERT INTO attempts(source_key,sha256,received_at_ns,file_written_at_ns,"
                                "status,snapshot_id) VALUES (?,?,?,?,?,?)",
                                (source_key, sha256, received_at_ns, file_written_at_ns, status, current["id"]))
                if reconcile:
                    manual_links.validate_links(self.db, source_key, previous, current["id"])
                self.db.commit()
                return {"status": status, "snapshot_id": current["id"],
                        "ledger_context_status": "requires_resolution" if unresolved else "confirmed"}
            if current and file_written_at_ns < current["file_written_at_ns"]:
                raise StorageError("source_timestamp_regressed")
            state = None
            changes = None
            if reconcile:
                changes = transition_context(previous, old_batch, normalized,
                                             same_raw=bool(current and current["sha256"] == sha256),
                                             unresolved=unresolved) if current else None
                if changes is None:
                    # Missing families have no authority to clear families in the SAME context.
                    present = {(s.get("region"), s["realm"], s["family"]) for s in normalized["scopes"]}
                    retained = [r for r in previous if (r.get("region"), r["realm"], r["family"]) not in present]
                    affected = [r for r in previous if (r.get("region"), r["realm"], r["family"]) in present]
                    changes = reconcile(affected, normalized["rows"])
                    changes["rows"].extend(retained)
                normalized = {**normalized, "ledger_context_status": changes.get("context_status", "confirmed")}
                state = encode(changes["rows"])
            hit("before_snapshot")
            cursor = self.db.execute("INSERT INTO snapshots(source_key,sha256,adapter_version,raw,normalized,"
                                     "received_at_ns,available_at_ns,file_written_at_ns) VALUES (?,?,?,?,?,?,?,?)",
                                     (source_key, sha256, adapter_version, raw, encode(normalized), received_at_ns,
                                      None, file_written_at_ns))
            sid = cursor.lastrowid
            hit("after_snapshot")
            if changes is not None:
                self.db.execute("INSERT INTO ledger_changes(snapshot_id,payload) VALUES (?,?)", (sid, encode(changes)))
            hit("before_checkpoint")
            self.db.execute("INSERT INTO current_sources VALUES (?,?,?) ON CONFLICT(source_key) "
                            "DO UPDATE SET snapshot_id=excluded.snapshot_id,ledger_state=excluded.ledger_state",
                            (source_key, sid, state))
            if changes is not None:
                manual_links.validate_links(self.db, source_key, changes["rows"], sid,
                                            context_changed=bool(changes.get("transition_kind")))
            self.db.execute("INSERT INTO attempts(source_key,sha256,received_at_ns,file_written_at_ns,status,snapshot_id) "
                            "VALUES (?,?,?,?,?,?)", (source_key, sha256, received_at_ns, file_written_at_ns, "accepted", sid))
            hit("before_commit")
            self.db.commit()
            hit("after_commit")
            self._publish_committed()
            return {"status": "accepted", "snapshot_id": sid,
                    "ledger_context_status": normalized.get("ledger_context_status", "confirmed")}
        except BaseException:
            self.db.rollback()
            raise

    def current(self) -> list[dict]:
        self._publish_committed()
        result = []
        for row in self.db.execute("SELECT s.*,c.ledger_state FROM current_sources c JOIN snapshots s ON s.id=c.snapshot_id ORDER BY c.source_key"):
            item = dict(row)
            item.pop("raw")
            item["normalized"] = json.loads(item["normalized"])
            item["ledger_state"] = json.loads(item["ledger_state"]) if item["ledger_state"] else None
            if item["normalized"].get("source") == "TradeSkillMaster":
                contaminated = not state_context_matches(item["ledger_state"] or [], item["normalized"])
                item["ledger_context_status"] = ("requires_resolution" if contaminated else
                                                 item["normalized"].get("ledger_context_status", "confirmed"))
                # Legacy overlays are assessed without mutating the historical records.
                item["manual_links"] = manual_links.read_links(self.db, item["source_key"])
            latest = self.db.execute("SELECT status,error_code,received_at_ns FROM attempts WHERE source_key=? ORDER BY id DESC LIMIT 1", (item["source_key"],)).fetchone()
            item["health"] = dict(latest) if latest else None
            result.append(item)
        return result

    def add_link(self, source_key: str, origin: str, destination: str, quantity: int,
                 note: str = "", supersedes_id: int | None = None) -> int:
        if type(quantity) is not int or quantity <= 0 or len(note) > 2000:
            raise StorageError("invalid_manual_link")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT c.ledger_state,s.normalized FROM current_sources c JOIN snapshots s ON s.id=c.snapshot_id WHERE c.source_key=?", (source_key,)).fetchone()
            records = {r["canonical_id"]: r for r in json.loads(row[0] or "[]")} if row else {}
            if row and (not state_context_matches(list(records.values()), json.loads(row[1]))
                        or json.loads(row[1]).get("ledger_context_status") == "requires_resolution"):
                raise StorageError("ledger_context_requires_resolution")
            if origin == destination or origin not in records or destination not in records:
                raise StorageError("unknown_link_endpoint")
            if records[origin]["family"] != "csvBuys" or records[destination]["family"] != "csvSales":
                raise StorageError("link_requires_buy_and_sale")
            try:
                manual_links.check_endpoints(list(records.values()), origin, destination, quantity)
            except manual_links.ManualLinkError as exc:
                raise StorageError(str(exc)) from None
            current = self.db.execute("SELECT snapshot_id FROM current_sources WHERE source_key=?", (source_key,)).fetchone()
            manual_links.validate_links(self.db, source_key, list(records.values()), current[0])
            if supersedes_id is not None:
                old = self.db.execute("SELECT source_key FROM manual_links WHERE id=? AND id NOT IN "
                                      "(SELECT supersedes_id FROM manual_links WHERE supersedes_id IS NOT NULL) "
                                      "AND id NOT IN (SELECT link_id FROM manual_link_revocations)", (supersedes_id,)).fetchone()
                if not old or old[0] != source_key:
                    raise StorageError("invalid_link_revision")
            active = [r for r in manual_links.read_links(self.db, source_key, include_history=False) if r["confirmed"]]
            for endpoint, field in ((origin, "from_canonical_id"), (destination, "to_canonical_id")):
                allocated = sum(r["quantity"] for r in active if r[field] == endpoint and r["id"] != supersedes_id)
                if allocated + quantity > records[endpoint]["quantity"]:
                    raise StorageError("link_exceeds_observed_quantity")
            cursor = self.db.execute("INSERT INTO manual_links(source_key,from_canonical_id,to_canonical_id,quantity,"
                                     "note,created_at_ns,supersedes_id) VALUES (?,?,?,?,?,?,?)",
                                     (source_key, origin, destination, quantity, note, time.time_ns(), supersedes_id))
            manual_links.bind_link(self.db, cursor.lastrowid, list(records.values()), current[0])
            manual_links.validate_links(self.db, source_key, list(records.values()), current[0])
            self.db.commit()
            return cursor.lastrowid
        except BaseException:
            self.db.rollback()
            raise

    def revoke_link(self, source_key: str, link_id: int, note: str = "") -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            manual_links.revoke_link(self.db, source_key, link_id, note)
            row = self.db.execute("SELECT snapshot_id,ledger_state FROM current_sources WHERE source_key=?", (source_key,)).fetchone()
            if row:
                manual_links.validate_links(self.db, source_key, json.loads(row[1] or "[]"), row[0])
            self.db.commit()
        except manual_links.ManualLinkError as exc:
            self.db.rollback()
            raise StorageError(str(exc)) from None
        except BaseException:
            self.db.rollback()
            raise

    def rebuild_ledger_context(self, source_key: str, *, account_scope: str, region: str,
                               adapter_version: str, backup_path: Path, reason: str,
                               fault: Callable[[str], None] | None = None) -> dict:
        """Explicitly reinterpret this source's immutable raw history in one chosen context.

        A backup is mandatory. Prior change batches remain auditable, but only the
        new projection epoch is authoritative; a baseline is never another trade.
        """
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise StorageError("context_resolution_reason_required")
        if region not in {"EU", "US", "KR", "TW", "CN"} or not account_scope:
            raise StorageError("explicit_context_required")
        version = self.db.execute("PRAGMA data_version").fetchone()[0]
        self.backup(backup_path)
        backup_hash = hashlib.sha256(Path(backup_path).read_bytes()).hexdigest()
        hit = fault or (lambda _: None)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("PRAGMA data_version").fetchone()[0] != version:
                raise StorageError("context_backup_stale_retry_with_new_backup")
            history = self.db.execute("SELECT * FROM snapshots WHERE source_key=? ORDER BY id", (source_key,)).fetchall()
            if not history or any(json.loads(r["normalized"]).get("source") != "TradeSkillMaster" for r in history):
                raise StorageError("ledger_source_required")
            batches = [normalize_ledger(parse_saved_variables(r["raw"]), account_scope=account_scope, region=region)
                       for r in history]
            changes = replay_context(batches)
            last = history[-1]
            normalized = {**batches[-1], "ledger_context_status": "confirmed",
                          "file_written_at_ns": last["file_written_at_ns"],
                          "provenance": {**json.loads(last["normalized"]).get("provenance", {}),
                                         "adapter_version": adapter_version,
                                         "context_evidence": "explicit_local_resolution"}}
            received = time.time_ns()
            cutoff = self.db.execute("SELECT COALESCE(MAX(l.id),0) FROM ledger_changes l JOIN snapshots s ON s.id=l.snapshot_id WHERE s.source_key=?", (source_key,)).fetchone()[0]
            cursor = self.db.execute("INSERT INTO snapshots(source_key,sha256,adapter_version,raw,normalized,received_at_ns,available_at_ns,file_written_at_ns) VALUES (?,?,?,?,?,?,NULL,?)",
                                     (source_key, last["sha256"], adapter_version, last["raw"], encode(normalized), received, last["file_written_at_ns"]))
            sid = cursor.lastrowid
            self.db.execute("INSERT INTO ledger_changes(snapshot_id,payload) VALUES (?,?)", (sid, encode(changes)))
            self.db.execute("INSERT INTO ledger_context_resolutions(source_key,snapshot_id,prior_changes_through,reason,backup_sha256,created_at_ns) VALUES (?,?,?,?,?,?)",
                            (source_key, sid, cutoff, reason, backup_hash, received))
            self.db.execute("UPDATE current_sources SET snapshot_id=?,ledger_state=? WHERE source_key=?", (sid, encode(changes["rows"]), source_key))
            manual_links.validate_links(self.db, source_key, changes["rows"], sid, context_changed=True)
            self.db.execute("INSERT INTO attempts(source_key,sha256,received_at_ns,file_written_at_ns,status,snapshot_id) VALUES (?,?,?,?,?,?)",
                            (source_key, last["sha256"], received, last["file_written_at_ns"], "context_rebuilt", sid))
            hit("before_commit")
            self.db.commit()
            self._publish_committed()
            return {"status": "context_rebuilt", "snapshot_id": sid, "prior_changes_through": cutoff,
                    "backup_sha256": backup_hash, "ledger_rows": len(changes["rows"]), "new_financial_events": 0}
        except (LedgerError, LuaDataError) as exc:
            self.db.rollback()
            raise StorageError("context_replay_invalid_saved_data") from None
        except BaseException:
            self.db.rollback()
            raise

    def backup(self, destination: Path | str) -> None:
        destination = Path(destination)
        if destination.suffix.lower() in {".lua", ".bak"}:
            raise StorageError("backup_destination_must_not_be_game_file")
        if destination.exists() or destination.resolve() == self.path.resolve():
            raise StorageError("backup_destination_must_be_new")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb"):
            pass
        target = sqlite3.connect(destination)
        try:
            self.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StorageError("backup_integrity_failed")
        finally:
            target.close()


def restore_backup(source: Path | str, destination: Path | str) -> None:
    """Restore to a NEW book only; never replace a live database or modify the backup."""
    source, destination = Path(source), Path(destination)
    if not source.is_file() or destination.exists() or source.is_symlink() or destination.suffix.lower() in {".lua", ".bak"}:
        raise StorageError("invalid_restore_paths")
    reader = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        reader.execute("PRAGMA trusted_schema=OFF")
        if reader.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise StorageError("foreign_database")
        if reader.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise StorageError("backup_integrity_failed")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb"):
            pass
        target = sqlite3.connect(destination)
        try:
            reader.backup(target)
        finally:
            target.close()
    finally:
        reader.close()
    with Store(destination):
        pass
