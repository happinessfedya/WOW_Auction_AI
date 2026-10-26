"""Identity-context transitions for one Ledger source, without invented trades.

Region completion is a correction of the SAME saved observation only when its
raw hash is unchanged. Other identity changes need an explicit resolution;
matching similar records is never evidence that real regional books are equal.
"""

from collections import Counter
from copy import deepcopy

from .ledger import HEADERS, LedgerError, _row, reconcile_ledger


def context_key(batch: dict) -> tuple[str, str | None]:
    return batch["account_scope"], batch.get("region")


def state_context_matches(rows: list[dict], batch: dict) -> bool:
    """Detect legacy mixed-context projections before an unchanged shortcut."""
    expected = context_key(batch)
    return all((row.get("account_scope"), row.get("region")) == expected for row in rows)


def _with_context(row: dict, context: tuple[str, str | None]) -> dict:
    # Reuse the same row constructor so price/time fingerprints cannot drift
    # from normal normalization. Canonical identity and history remain intact.
    values = {
        "itemString": row["item_string"], "stackSize": row["stack_size"],
        "quantity": row["quantity"], "price": row["reported_unit_amount_decimal"],
        "amount": row["amount_copper"], "otherPlayer": row["other_player"],
        "player": row["player"], "time": row["reported_time_decimal"],
        "source": row["method"], "type": row["money_type"],
    }
    fields = {key: str(values[key]) for key in HEADERS[row["family"]].split(",")}
    normalized = _row(context[0], context[1], row["realm"], row["family"], row["ordinal"], fields)
    return {**deepcopy(row), **normalized}


def _same_observations(old_batch: dict, new_batch: dict) -> bool:
    target = context_key(new_batch)
    old_rows = Counter(_with_context(row, target)["row_fingerprint"] for row in old_batch["rows"])
    new_rows = Counter(row["row_fingerprint"] for row in new_batch["rows"])
    old_scopes = {(s["realm"], s["family"]) for s in old_batch["scopes"]}
    new_scopes = {(s["realm"], s["family"]) for s in new_batch["scopes"]}
    return old_rows == new_rows and old_scopes == new_scopes


def _revision(old: dict | None, new: dict, kind: str) -> dict:
    new["revision"] = (old.get("revision", 0) if old else 0) + 1
    return {"canonical_id": new["canonical_id"], "revision": new["revision"],
            "kind": kind, "previous_fingerprint": old["row_fingerprint"] if old else None,
            "current_fingerprint": new["row_fingerprint"], "row_state": new["row_state"]}


def transition_context(previous_rows: list[dict], old_batch: dict, new_batch: dict,
                       *, same_raw: bool, unresolved: bool = False) -> dict | None:
    """Return a no-financial-event transition, or None for normal reconciliation.

    Store owns the immutable snapshots and the persistent unresolved flag. An
    unresolved transition stays unresolved even if a later setting is reverted.
    The conflicting projection contains only the latest normalized observations;
    discarded old projections remain accessible through their saved history.
    """
    old_context, new_context = context_key(old_batch), context_key(new_batch)
    clean = state_context_matches(previous_rows, old_batch)
    if old_context == new_context and clean and not unresolved:
        return None

    completion = (clean and not unresolved and same_raw
                  and old_context[0] == new_context[0]
                  and old_context[1] is None and new_context[1] is not None
                  and _same_observations(old_batch, new_batch))
    if completion:
        rows, revisions = [], []
        for old in previous_rows:
            new = _with_context(old, new_context)
            revisions.append(_revision(old, new, "identity_context_completed"))
            rows.append(new)
        return {"rows": sorted(rows, key=lambda row: row["canonical_id"]),
                "revisions": revisions, "events": [], "warnings": [],
                "pnl_copper": None, "pnl_status": "unknown",
                "context_status": "confirmed", "transition_kind": "region_completed"}

    # A new projection is evidence to resolve, not a second active financial
    # book. Even an apparently equal EU/US row is not silently merged.
    fresh = reconcile_ledger([], new_batch["rows"])
    prior = {row["canonical_id"]: row for row in previous_rows}
    revisions = []
    for row in fresh["rows"]:
        old = prior.get(row["canonical_id"])
        row.update(row_state="context_conflict", uncertain=True,
                   reconciliation_status="identity_context_requires_resolution")
        if old:
            row["revision"] = old["revision"]
            row["high_water"] = max(old["high_water"], row["high_water"])
        if old is None or any(old.get(key) != row.get(key) for key in (
                "row_fingerprint", "row_state", "uncertain", "reconciliation_status", "high_water")):
            revisions.append(_revision(old, row, "identity_context_conflict"))
    fresh.update(revisions=revisions, events=[], warnings=["ledger_context_requires_resolution"],
                 context_status="requires_resolution", transition_kind="context_conflict")
    return fresh


def replay_context(batches: list[dict]) -> dict:
    """Rebuild from re-normalized raw history after an explicit scope decision.

    The caller must normalize all snapshots in the ONE explicitly confirmed
    target context and keep their original history. This computes a projection,
    never a replacement financial stream: it intentionally emits no events.
    Missing families still cannot clear previously observed family state.
    """
    if not batches:
        raise LedgerError("context_replay_requires_history")
    target = context_key(batches[0])
    if any(context_key(batch) != target for batch in batches):
        raise LedgerError("context_replay_mixed_target_contexts")
    state, warnings = [], set()
    for batch in batches:
        present = {(s.get("region"), s["realm"], s["family"]) for s in batch["scopes"]}
        retained = [row for row in state if (row.get("region"), row["realm"], row["family"]) not in present]
        affected = [row for row in state if (row.get("region"), row["realm"], row["family"]) in present]
        changes = reconcile_ledger(affected, batch["rows"])
        state = changes["rows"] + retained
        warnings.update(changes["warnings"])
    revisions = []
    for row in state:
        revisions.append(_revision(deepcopy(row), row, "identity_context_rebuilt"))
    return {"rows": sorted(state, key=lambda row: row["canonical_id"]),
            "revisions": revisions, "events": [], "warnings": sorted(warnings),
            "pnl_copper": None, "pnl_status": "unknown", "context_status": "confirmed",
            "transition_kind": "context_replayed"}
