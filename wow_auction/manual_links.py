"""Version-bound manual Ledger allocations; never an inferred profit calculation.

All writes join the caller's transaction. Original links and endpoint bindings
are immutable; validation results and explicit revocations are append-only.
"""

from __future__ import annotations

from collections import defaultdict
import json
import time


class ManualLinkError(ValueError):
    """Safe domain diagnostic, without private values."""


MIGRATION_SQL = (
    "CREATE TABLE manual_link_bindings (link_id INTEGER PRIMARY KEY REFERENCES manual_links(id), "
    "snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), endpoint_data TEXT NOT NULL, "
    "created_at_ns INTEGER NOT NULL)",
    "CREATE TABLE manual_link_checks (id INTEGER PRIMARY KEY, link_id INTEGER NOT NULL REFERENCES manual_links(id), "
    "snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), status TEXT NOT NULL, reasons TEXT NOT NULL, "
    "endpoint_data TEXT NOT NULL, created_at_ns INTEGER NOT NULL)",
    "CREATE INDEX manual_link_checks_by_link ON manual_link_checks(link_id,id)",
    "CREATE TABLE manual_link_revocations (link_id INTEGER PRIMARY KEY REFERENCES manual_links(id), "
    "note TEXT NOT NULL, created_at_ns INTEGER NOT NULL)",
)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _endpoint(row):
    if row is None:
        return None
    return {key: row.get(key) for key in (
        "canonical_id", "revision", "account_scope", "region", "realm", "family",
        "row_fingerprint", "row_state", "uncertain", "quantity",
    )}


def _endpoints(link, records):
    return {"from": _endpoint(records.get(link["from_canonical_id"])),
            "to": _endpoint(records.get(link["to_canonical_id"]))}


def _problems(endpoints, quantity):
    reasons = set()
    if endpoints["from"] is not None and endpoints["to"] is not None:
        if any(endpoints["from"][key] != endpoints["to"][key] for key in ("account_scope", "region")):
            reasons.add("link_endpoint_context_mismatch")
    for side, family in (("from", "csvBuys"), ("to", "csvSales")):
        row = endpoints[side]
        if row is None:
            reasons.add("link_endpoint_missing")
            continue
        if row["family"] != family:
            reasons.add("link_requires_buy_and_sale")
        if row["row_state"] != "present":
            reasons.add("link_endpoint_not_present")
        if row["uncertain"] is not False:
            reasons.add("link_endpoint_uncertain")
        if type(row["quantity"]) is not int or row["quantity"] < quantity:
            reasons.add("link_exceeds_observed_quantity")
    return reasons


def check_endpoints(state, origin, destination, quantity):
    """Validate a candidate's endpoints, independent of other allocations."""
    if type(quantity) is not int or quantity <= 0 or origin == destination:
        raise ManualLinkError("invalid_manual_link")
    records = {r["canonical_id"]: r for r in state}
    if origin not in records or destination not in records:
        raise ManualLinkError("unknown_link_endpoint")
    reasons = _problems({"from": _endpoint(records[origin]), "to": _endpoint(records[destination])}, quantity)
    if reasons:
        raise ManualLinkError(sorted(reasons)[0])


def bind_link(db, link_id, state, snapshot_id):
    """Bind a NEW manual decision to the exact endpoint observations it reviewed."""
    link = db.execute("SELECT * FROM manual_links WHERE id=?", (link_id,)).fetchone()
    if link is None:
        raise ManualLinkError("unknown_manual_link")
    if db.execute("SELECT 1 FROM manual_link_bindings WHERE link_id=?", (link_id,)).fetchone():
        raise ManualLinkError("manual_link_binding_is_immutable")
    check_endpoints(state, link["from_canonical_id"], link["to_canonical_id"], link["quantity"])
    endpoints = _endpoints(link, {r["canonical_id"]: r for r in state})
    db.execute("INSERT INTO manual_link_bindings VALUES (?,?,?,?)",
               (link_id, snapshot_id, _encode(endpoints), time.time_ns()))


def validate_links(db, source_key, state, snapshot_id, *, context_changed=False):
    """Recheck active allocations atomically with the current Ledger projection.

    Once a link needs review, only a new superseding manual decision or an
    explicit revocation resolves it. A later identical import cannot silently
    reconfirm a link whose context or endpoints changed earlier.
    """
    records = {r["canonical_id"]: r for r in state}
    links = db.execute("SELECT * FROM manual_links WHERE source_key=? ORDER BY id", (source_key,)).fetchall()
    superseded = {r[0] for r in db.execute("SELECT supersedes_id FROM manual_links WHERE supersedes_id IS NOT NULL")}
    revoked = {r[0] for r in db.execute("SELECT link_id FROM manual_link_revocations")}
    decisions = {}
    for link in links:
        lid = link["id"]
        latest = db.execute("SELECT * FROM manual_link_checks WHERE link_id=? ORDER BY id DESC LIMIT 1", (lid,)).fetchone()
        if lid in revoked or lid in superseded:
            # Terminal states do not gain meaningless revisions on later imports.
            state_name = "revoked" if lid in revoked else "superseded"
            decisions[lid] = (state_name, set(), {}, latest)
            continue
        endpoints = _endpoints(link, records)
        reasons = _problems(endpoints, link["quantity"])
        binding = db.execute("SELECT endpoint_data FROM manual_link_bindings WHERE link_id=?", (lid,)).fetchone()
        if binding is None:
            reasons.add("legacy_link_not_revision_bound")
        elif json.loads(binding[0]) != endpoints:
            reasons.add("link_endpoint_revision_changed")
            old = json.loads(binding[0])
            for side in ("from", "to"):
                if old[side] and endpoints[side] and any(old[side].get(k) != endpoints[side].get(k)
                                                        for k in ("account_scope", "region", "realm")):
                    reasons.add("link_endpoint_scope_changed")
        if context_changed:
            reasons.add("source_context_changed")
        if latest and latest["status"] == "requires_review":
            # Retain evidence of the unresolved decision instead of losing it
            # when a field happens to return to its former value.
            reasons.update(json.loads(latest["reasons"]))
        decisions[lid] = ("requires_review" if reasons else "confirmed", reasons, endpoints, latest)

    # Only otherwise-confirmable current links compete for confirmed capacity.
    # If that set over-allocates an endpoint, all involved links need review;
    # picking an arbitrary winner would silently reinterpret manual decisions.
    allocations = defaultdict(list)
    for link in links:
        if decisions[link["id"]][0] == "confirmed":
            for field in ("from_canonical_id", "to_canonical_id"):
                allocations[link[field]].append(link)
    for endpoint, allocated in allocations.items():
        if sum(link["quantity"] for link in allocated) > records[endpoint]["quantity"]:
            for link in allocated:
                _, reasons, endpoints, latest = decisions[link["id"]]
                reasons.add("aggregate_allocation_exceeds_observed_quantity")
                decisions[link["id"]] = ("requires_review", reasons, endpoints, latest)

    added = 0
    for link in links:
        state_name, reasons, endpoints, latest = decisions[link["id"]]
        reason_text, endpoint_text = _encode(sorted(reasons)), _encode(endpoints)
        if latest and (latest["status"], latest["reasons"], latest["endpoint_data"]) == (state_name, reason_text, endpoint_text):
            continue
        db.execute("INSERT INTO manual_link_checks(link_id,snapshot_id,status,reasons,endpoint_data,created_at_ns) "
                   "VALUES (?,?,?,?,?,?)", (link["id"], snapshot_id, state_name, reason_text, endpoint_text, time.time_ns()))
        added += 1
    return added


def read_links(db, source_key, *, include_history=True):
    """Private diagnostic rows; callers must redact notes and endpoint IDs by default."""
    result = []
    superseded = {r[0] for r in db.execute("SELECT supersedes_id FROM manual_links WHERE supersedes_id IS NOT NULL")}
    for record in db.execute("SELECT * FROM manual_links WHERE source_key=? ORDER BY id", (source_key,)):
        row = dict(record)
        checks = [dict(c) for c in db.execute("SELECT * FROM manual_link_checks WHERE link_id=? ORDER BY id", (row["id"],))]
        for check in checks:
            check["reasons"] = json.loads(check["reasons"])
            check["endpoint_data"] = json.loads(check["endpoint_data"])
        revocation = db.execute("SELECT note,created_at_ns FROM manual_link_revocations WHERE link_id=?", (row["id"],)).fetchone()
        binding = db.execute("SELECT * FROM manual_link_bindings WHERE link_id=?", (row["id"],)).fetchone()
        row["binding"] = dict(binding) if binding else None
        if row["binding"]:
            row["binding"]["endpoint_data"] = json.loads(row["binding"]["endpoint_data"])
        row["is_current"] = row["id"] not in superseded and revocation is None
        row["status"] = ("revoked" if revocation else "superseded" if row["id"] in superseded
                         else checks[-1]["status"] if checks else "requires_review")
        row["reasons"] = checks[-1]["reasons"] if checks else ["manual_link_not_validated"]
        row["confirmed"] = row["is_current"] and row["status"] == "confirmed"
        row["confirmed_quantity"] = row["quantity"] if row["confirmed"] else 0
        row["revocation"] = dict(revocation) if revocation else None
        if include_history:
            row["validation_history"] = checks
        result.append(row)
    return result


def revoke_link(db, source_key, link_id, note=""):
    """Append an explicit withdrawal; retain its source link, binding and checks."""
    if not isinstance(note, str) or len(note) > 2000:
        raise ManualLinkError("invalid_manual_link_note")
    link = db.execute("SELECT source_key FROM manual_links WHERE id=?", (link_id,)).fetchone()
    if link is None or link[0] != source_key:
        raise ManualLinkError("unknown_manual_link")
    if db.execute("SELECT 1 FROM manual_links WHERE supersedes_id=?", (link_id,)).fetchone():
        raise ManualLinkError("manual_link_already_superseded")
    if db.execute("SELECT 1 FROM manual_link_revocations WHERE link_id=?", (link_id,)).fetchone():
        return False
    db.execute("INSERT INTO manual_link_revocations VALUES (?,?,?)", (link_id, note, time.time_ns()))
    return True
