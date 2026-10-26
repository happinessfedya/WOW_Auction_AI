"""Read-only TSM Ledger contracts; all money is copper, never inferred profit.

TSM's installed CSV dialect is literal comma/newline splitting, not RFC 4180.
Snapshot row hashes identify observations, not unique in-game transactions.
Reconciliation retains missing rows and amount/quantity high-water marks so that
trimming, restores, and a later reappearance cannot manufacture reversal trades.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import json
import re


HEADERS = {
    "csvSales": "itemString,stackSize,quantity,price,otherPlayer,player,time,source",
    "csvBuys": "itemString,stackSize,quantity,price,otherPlayer,player,time,source",
    "csvIncome": "type,amount,otherPlayer,player,time",
    "csvExpense": "type,amount,otherPlayer,player,time",
    "csvExpired": "itemString,stackSize,quantity,player,time",
    "csvCancelled": "itemString,stackSize,quantity,player,time",
}
_MAX_INTEGER = 2**53 - 1  # Lua double's exact integer range.
_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


class LedgerError(ValueError):
    """Safe error code; never includes source fields, paths, or names."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _number(value: str, *, positive: bool = False, integral: bool = True) -> Decimal:
    if len(value) > 64 or not _DECIMAL.fullmatch(value):
        raise LedgerError("invalid_numeric_field")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise LedgerError("invalid_numeric_field") from None
    if not result.is_finite() or result > _MAX_INTEGER or result < 0:
        raise LedgerError("numeric_out_of_range")
    if positive and result <= 0:
        raise LedgerError("nonpositive_quantity_or_amount")
    if integral and result != result.to_integral_value():
        raise LedgerError("nonintegral_quantity_or_amount")
    return result


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _row(account: str, region: str | None, realm: str, family: str, ordinal: int,
         fields: dict[str, str]) -> dict:
    timestamp = _number(fields["time"], integral=False)
    method = fields.get("source")
    quantity = int(_number(fields["quantity"], positive=True)) if "quantity" in fields else None
    stack = int(_number(fields["stackSize"], positive=True)) if "stackSize" in fields else None
    if quantity is not None and stack is not None and quantity < stack:
        raise LedgerError("quantity_smaller_than_stack")
    item = fields.get("itemString")
    if item is not None and not re.fullmatch(r"[ip]:[0-9]+(?::[^,\r\n\x00]*)?", item):
        raise LedgerError("unsupported_item_string")
    if not fields["player"]:
        raise LedgerError("missing_player")
    price = _number(fields["price"], integral=False) if "price" in fields else None
    unit = _floor(price) if price is not None else None
    amount = int(_number(fields["amount"], positive=True)) if "amount" in fields else None
    lower = upper = estimate = None
    semantics = "not_applicable"
    bounds_status = "not_applicable"
    classification = {
        "csvSales": "sale", "csvBuys": "purchase", "csvExpired": "expired_listing",
        "csvCancelled": "cancelled_listing", "csvIncome": "unclassified_income",
        "csvExpense": "unclassified_expense",
    }[family]
    if price is not None:
        estimate = unit * quantity
        if estimate > _MAX_INTEGER:
            raise LedgerError("total_out_of_range")
        if method in ("Auction", "COD") and price == unit:
            semantics = ("net_after_ah_cut_unit_rounded" if family == "csvSales" and method == "Auction"
                         else "payment_unit_rounded")
            if family == "csvSales" and method == "Auction":
                # Auction sale quantity is the invoice denominator. Aggregating
                # equal rounded units only widens these integer-total bounds.
                lower = max(0, ((2 * unit - 1) * quantity + 1) // 2)
                upper = ((2 * unit + 1) * quantity + 1) // 2 - 1
                bounds_status = "bounded_net_invoice_rounding"
            else:
                # COD splits the original invoice into max-stack rows; Retail
                # auction buys replace invoice quantity with attachment quantity.
                # The denominator/group relation is absent from these CSV rows.
                bounds_status = "unknown_original_invoice_allocation"
        elif method == "Trade":
            # TSM reload floors fractional unit prices. The original fraction
            # is unavailable after a reload, even if this CSV contains integers.
            lower, upper = unit * quantity, (unit + 1) * quantity - 1
            semantics = "unit_payment_floor_loss_possible"
            bounds_status = "bounded_source_floor_loss"
        elif method == "Vendor":
            # Vendor buys round price/batchQuantity; quantity may differ from
            # batchQuantity. Keep the stated unit estimate without inventing an
            # invoice denominator or assuming the Trade floor rule applies.
            semantics = "vendor_reported_unit"
            bounds_status = "unknown_vendor_batch_allocation"
        else:
            semantics = "unit_amount_semantics_unverified"
            bounds_status = "unknown_unit_semantics"
        amount = estimate
    elif "amount" in fields:
        lower = upper = estimate = amount
        semantics = "reported_total_copper"
        bounds_status = "reported_total"
        money_type = fields["type"]
        if money_type == "Money Transfer":
            classification = "unreconciled_transfer"
        elif "refund" in money_type.casefold():
            classification = "unverified_refund"
        elif family == "csvExpense" and money_type in ("Postage", "Repair Bill", "Crafting Order"):
            classification = "reported_expense"
        elif family == "csvIncome" and money_type == "Garrison":
            classification = "nontrade_income"
        elif family == "csvIncome" and money_type == "Crafting Order":
            classification = "crafting_order_receipt"
    event_semantics = "source_reported_event_time"
    if (method in ("Auction", "COD") or family in ("csvExpired", "csvCancelled")
            or fields.get("type") == "Crafting Order"):
        event_semantics = "mail_derived_event_estimate"
    elif classification in ("unreconciled_transfer", "unverified_refund") and family == "csvIncome":
        event_semantics = "source_time_may_be_mail_estimate"
    row = {
        "account_scope": account, "region": region, "realm": realm, "family": family,
        "source_key": f"r@{realm}@internalData@{family}", "ordinal": ordinal,
        "item_string": item, "quantity": quantity, "stack_size": stack,
        "unit_amount_copper": unit,
        "reported_unit_amount_decimal": str(price) if price is not None else None,
        "amount_copper": amount, "total_estimate_copper": estimate,
        "total_lower_copper": lower, "total_upper_copper": upper,
        "rounding_bounds_status": bounds_status,
        "price_semantics": semantics, "event_at": _floor(timestamp),
        "reported_time_decimal": str(timestamp), "event_time_semantics": event_semantics,
        "method": method, "player": fields["player"], "other_player": fields.get("otherPlayer"),
        "money_type": fields.get("type"), "classification": classification,
        "pnl_copper": None, "pnl_status": "unknown",
        "purchase_cost_copper": None, "lost_deposit_copper": None,
        "ah_cut_already_applied": family == "csvSales" and method == "Auction",
        "profit_eligible": False,
    }
    fixed = {key: row[key] for key in (
        "account_scope", "region", "realm", "family", "item_string", "stack_size", "unit_amount_copper",
        "event_at", "method", "player", "other_player", "money_type")}
    row["match_fingerprint"] = _digest(fixed)
    row["row_fingerprint"] = _digest({"fixed": fixed, "quantity": quantity, "amount": amount,
                                      "reported_price": row["reported_unit_amount_decimal"],
                                      "reported_time": row["reported_time_decimal"]})
    return row


def normalize_ledger(variables: dict, *, account_scope: str, region: str | None = None) -> dict:
    """Normalize only the six current Ledger families; malformed data fails atomically.

    Empty CSV with a valid header is an observed empty family. Missing families
    are listed separately and do not prove zero operations. Personal fields are
    retained only in the local result, never in diagnostics or exception text.
    """
    if not isinstance(account_scope, str) or not account_scope:
        raise LedgerError("missing_account_scope")
    if region is not None and region not in ("EU", "US", "KR", "TW", "CN"):
        raise LedgerError("unsupported_region")
    database = variables.get("TradeSkillMasterDB")
    if not isinstance(database, dict):
        raise LedgerError("missing_tsm_database")
    rows, scopes, warnings = [], [], set()
    counts = Counter()
    total_bytes = 0
    for key, value in sorted(database.items(), key=lambda pair: str(pair[0])):
        if not isinstance(key, str):
            continue
        parts = key.split("@")
        if len(parts) != 4 or parts[0] != "r" or parts[2] != "internalData" or parts[3] not in HEADERS:
            continue  # Schema metadata such as r@internalData@csvSales is not data.
        realm, family = parts[1], parts[3]
        if not realm or not isinstance(value, str):
            raise LedgerError("invalid_ledger_scope")
        total_bytes += len(value.encode("utf-8"))
        if total_bytes > 32 * 1024 * 1024:
            raise LedgerError("ledger_size_limit")
        if value.count("\n") + len(rows) > 400_000:
            raise LedgerError("ledger_row_limit")
        lines = value.split("\n")
        if lines[0] != HEADERS[family]:
            raise LedgerError("unsupported_csv_header")
        names = HEADERS[family].split(",")
        for ordinal, line in enumerate(lines[1:], 1):
            if not line or "\r" in line or "\x00" in line:
                raise LedgerError("malformed_csv_row")
            cells = line.split(",")
            if len(cells) != len(names) or any(len(cell) > 4096 for cell in cells):
                raise LedgerError("malformed_csv_row")
            row = _row(account_scope, region, realm, family, ordinal, dict(zip(names, cells)))
            counts[row["row_fingerprint"]] += 1
            row["occurrence"] = counts[row["row_fingerprint"]]
            rows.append(row)
            if len(rows) > 400_000:
                raise LedgerError("ledger_row_limit")
            if row["reported_unit_amount_decimal"] is not None:
                if Decimal(row["reported_unit_amount_decimal"]) != row["unit_amount_copper"]:
                    warnings.add("fractional_unit_price_preserved")
            if row["price_semantics"] == "unit_amount_semantics_unverified":
                warnings.add("unverified_price_semantics")
        scopes.append({"region": region, "realm": realm, "family": family, "row_count": len(lines) - 1,
                       "complete": True})
    realms = sorted({scope["realm"] for scope in scopes})
    observed_scopes = {(scope["realm"], scope["family"]) for scope in scopes}
    missing = [{"region": region, "realm": realm, "family": family} for realm in realms for family in HEADERS
               if (realm, family) not in observed_scopes]
    if missing or not scopes:
        warnings.add("missing_ledger_families")
    if rows:
        warnings.add("pnl_unknown_missing_costs_and_deposits")
    return {"source": "TradeSkillMaster", "account_scope": account_scope, "region": region, "rows": rows,
            "scopes": scopes, "missing_families": missing, "complete": bool(scopes) and not missing,
            "warnings": sorted(warnings), "pnl_copper": None, "pnl_status": "unknown"}


def _metric(row: dict) -> int:
    return row["quantity"] if row["quantity"] is not None else row["amount_copper"]


def reconcile_ledger(previous_rows: list[dict], current_rows: list[dict]) -> dict:
    """Reconcile a complete current selection against retained canonical state.

    ``rows`` in the result is ALL retained canonical state, including missing and
    unresolved observations; persist it for the next call. Caller must restrict
    comparison to successfully parsed source scopes and keep snapshots/revisions.
    Absence never reverses a trade. Output deltas describe source observations,
    not proof that a trade occurred between imports and not a P&L calculation.
    """
    previous, current = deepcopy(previous_rows), deepcopy(current_rows)
    by_old, by_new = defaultdict(list), defaultdict(list)
    for row in previous:
        by_old[row["match_fingerprint"]].append(row)
    for row in current:
        by_new[row["match_fingerprint"]].append(row)
    output, revisions, events, warnings = [], [], [], set()

    def record(old: dict | None, new: dict, kind: str, delta: int = 0) -> None:
        if old is None or any(old.get(key) != new.get(key) for key in
                              ("row_state", "row_fingerprint", "uncertain", "reconciliation_status", "high_water")):
            new["revision"] = (old.get("revision", 0) if old else 0) + 1
            revisions.append({"canonical_id": new["canonical_id"], "revision": new["revision"],
                              "kind": kind, "previous_fingerprint": old["row_fingerprint"] if old else None,
                              "current_fingerprint": new["row_fingerprint"], "row_state": new["row_state"]})
        if delta > 0:
            qty = delta if new["quantity"] is not None else None
            amount = delta * new["unit_amount_copper"] if qty is not None and new["unit_amount_copper"] is not None else None
            if qty is None:
                amount = delta
            events.append({"canonical_id": new["canonical_id"], "revision": new["revision"],
                           "kind": kind, "quantity_delta": qty, "amount_delta_copper": amount,
                           "amount_delta_status": ("unit_times_quantity_estimate" if qty is not None and amount is not None
                                                   else "reported_money_amount" if qty is None else "unknown"),
                           "price_semantics": new["price_semantics"],
                           "classification": new["classification"], "profit_eligible": False,
                           "event_at": new["event_at"], "event_time_semantics": new["event_time_semantics"],
                           "basis": "source_observation_not_import_time_trade"})
        output.append(new)

    for group in sorted(set(by_old) | set(by_new)):
        old_group = sorted(by_old[group], key=lambda r: (r.get("row_state") == "missing", r.get("canonical_id", ""), r["row_fingerprint"]))
        new_group = sorted(by_new[group], key=lambda r: (r["row_fingerprint"], r["ordinal"]))
        for serial, row in enumerate(old_group, 1):
            row.setdefault("canonical_id", f"ledger:{group}:{serial}")
            row.setdefault("revision", 1)
            row.setdefault("high_water", _metric(row))
            row.setdefault("row_state", "present")
            row.setdefault("reconciliation_status", "observed")
            row.setdefault("uncertain", False)
        if len({r["canonical_id"] for r in old_group}) != len(old_group):
            raise LedgerError("duplicate_canonical_id")
        old_by_fingerprint = defaultdict(deque)
        for row in old_group:
            old_by_fingerprint[row["row_fingerprint"]].append(row)
        matched_ids = set()
        unmatched_new = []
        for fresh in new_group:
            matches = old_by_fingerprint[fresh["row_fingerprint"]]
            if not matches:
                unmatched_new.append(fresh)
                continue
            old = matches.popleft()
            matched_ids.add(old["canonical_id"])
            updated = {**fresh, **{k: old[k] for k in ("canonical_id", "revision", "high_water", "reconciliation_status", "uncertain")},
                       "row_state": "present"}
            record(old, updated, "reappeared" if old["row_state"] == "missing" else "unchanged")
        unmatched_old = [row for row in old_group if row["canonical_id"] not in matched_ids]

        # A one-to-one fixed-key match permits monotone aggregate reconciliation.
        # Duplicate candidate rows make growth ambiguous even after exact matches.
        if len(old_group) == len(new_group) == len(unmatched_old) == len(unmatched_new) == 1:
            old, fresh = unmatched_old.pop(), unmatched_new.pop()
            metric = _metric(fresh)
            uncertainty = old["uncertain"] or metric < _metric(old)
            updated = {**fresh, "canonical_id": old["canonical_id"], "revision": old["revision"],
                       "row_state": "present", "high_water": max(old["high_water"], metric),
                       "uncertain": uncertainty,
                       "reconciliation_status": "decreased_or_previously_uncertain" if uncertainty else "unambiguous_fixed_key"}
            delta = max(0, metric - old["high_water"]) if not uncertainty else 0
            kind = "aggregate_growth" if delta else "source_revision"
            if uncertainty:
                warnings.add("aggregate_decrease_or_uncertain_history")
            record(old, updated, kind, delta)

        ambiguous = bool(unmatched_old and unmatched_new)
        if ambiguous:
            warnings.add("ambiguous_aggregate_matching")
        for old in unmatched_old:
            updated = {**old, "row_state": "missing", "uncertain": old["uncertain"] or ambiguous}
            if ambiguous:
                updated["reconciliation_status"] = "ambiguous_matching"
            record(old, updated, "missing_without_reversal")
        used = {r["canonical_id"] for r in old_group}
        serial = 0
        for fresh in unmatched_new:
            while True:
                serial += 1
                canonical = f"ledger:{group}:{serial}"
                if canonical not in used:
                    used.add(canonical)
                    break
            updated = {**fresh, "canonical_id": canonical, "revision": 0, "row_state": "present",
                       "high_water": _metric(fresh), "uncertain": ambiguous,
                       "reconciliation_status": "ambiguous_matching" if ambiguous else "observed"}
            kind = "initial_observation" if not previous else "new_source_observation"
            record(None, updated, "ambiguous_observation" if ambiguous else kind,
                   0 if ambiguous else _metric(fresh))
    return {"rows": sorted(output, key=lambda r: r["canonical_id"]), "revisions": revisions,
            "events": events, "warnings": sorted(warnings), "pnl_copper": None, "pnl_status": "unknown"}
