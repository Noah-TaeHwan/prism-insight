"""Post-close broker fill confirmation for recorded buys and micro-split adds (observation only).

The order ledger (``order_intents`` + ``broker_orders``) says what we sent; the broker's
daily execution inquiry says what filled. This module matches the two and emits one
``entry.fill_reconciled`` per order on the trade's trace. It never places, amends or
cancels an order and never writes the trading database. A failed or ambiguous inquiry is
``MISSING``; it is never read as "not filled".

fill_status: CONFIRMED (all filled) / PARTIAL / NOT_FILLED (broker shows the order, zero
filled after the close) / MISSING (no authoritative answer).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from observability.events import emit_event
from prism_core.trace_ids import trade_trace

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
FINAL_STATUSES = frozenset({"CONFIRMED", "PARTIAL", "NOT_FILLED"})
MARKET_TZ = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}
# Inquiry is complete only after the regular close (+ margin for the broker's batch).
CLOSE_LOCAL = {"KR": (15, 40), "US": (16, 10)}
EVIDENCE_SOURCE = {"KR": "kis_inquire_daily_ccld", "US": "kis_overseas_inquire_ccnl"}
PROVENANCE_STATUS = {"CONFIRMED": "CONFIRMED", "PARTIAL": "PARTIAL", "NOT_FILLED": "CANCELLED",
                     "MISSING": "UNKNOWN"}
TIME_WINDOW = timedelta(minutes=15)

_TARGET_SQL = """
SELECT i.id, i.market, i.account_id, i.symbol, i.source, i.status, i.quantity, i.limit_price,
       i.source_decision_id, i.source_position_id, i.created_at,
       (SELECT b.broker_order_id FROM broker_orders b WHERE b.intent_id = i.id AND b.accepted = 1
        ORDER BY b.submitted_at DESC LIMIT 1) AS order_no,
       (SELECT b.submitted_quantity FROM broker_orders b WHERE b.intent_id = i.id AND b.accepted = 1
        ORDER BY b.submitted_at DESC LIMIT 1) AS submitted_quantity
FROM order_intents i
WHERE i.market = ? AND upper(i.side) = 'BUY' AND i.status IN ('SUBMITTED', 'UNKNOWN')
  AND i.created_at >= ? AND i.created_at < ?
ORDER BY i.created_at
"""


def _ref(value) -> str | None:
    return hashlib.sha256(str(value).encode()).hexdigest()[:16] if value not in (None, "") else None


def _dec(value) -> Decimal | None:
    """Strict broker number: blank or malformed stays unknown (None), never 0."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _int(value) -> int | None:
    parsed = _dec(value)
    return int(parsed) if parsed is not None and parsed == int(parsed) else None


def _parse_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else None


def session_bounds(market: str, day: str) -> tuple[str, str]:
    """UTC ISO bounds of one market-local calendar day (YYYYMMDD)."""
    tz = MARKET_TZ[market]
    start = datetime.strptime(day, "%Y%m%d").replace(tzinfo=tz)
    return (start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat())


def after_close(market: str, day: str, now: datetime | None = None) -> bool:
    tz = MARKET_TZ[market]
    hour, minute = CLOSE_LOCAL[market]
    close = datetime.strptime(day, "%Y%m%d").replace(hour=hour, minute=minute, tzinfo=tz)
    return (now or datetime.now(timezone.utc)) >= close


def load_targets(db_path, market: str, day: str) -> list[dict[str, Any]]:
    """Buys and adds submitted that market-local day, read-only (``mode=ro``)."""
    start, end = session_bounds(market, day)
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(_TARGET_SQL, (market, start, end))]
        for row in rows:
            row["is_add"] = row["source"] == "micro_split_add"
            row["trace_decision_id"] = row["source_decision_id"]
            if row["is_add"]:
                # An add's own decision id is the add hash; the trade trace is the entry's decision.
                found = None
                if row["source_position_id"]:
                    try:
                        found = conn.execute(
                            "SELECT o.source_decision_id FROM positions p JOIN order_intents o "
                            "ON o.id = p.entry_intent_id WHERE p.id = ?", (row["source_position_id"],)).fetchone()
                    except sqlite3.Error:
                        found = None
                row["trace_decision_id"] = found[0] if found and found[0] else None
    return rows


def normalize_rows(market: str, raw_rows) -> tuple[bool, list[dict[str, Any]]]:
    """Broker rows → one shape; any unparseable buy row makes the answer non-authoritative."""
    out, ok = [], True
    for raw in raw_rows or []:
        if not isinstance(raw, dict):
            return False, out
        if market == "KR":
            row = {"order_no": str(raw.get("odno") or "").strip(), "orig_order_no": str(raw.get("orgn_odno") or "").strip(),
                   "symbol": str(raw.get("pdno") or "").strip(), "side": str(raw.get("sll_buy_dvsn_cd") or "").strip(),
                   "ordered_qty": _int(raw.get("ord_qty")), "filled_qty": _int(raw.get("tot_ccld_qty")),
                   "remaining_qty": _int(raw.get("rmn_qty")), "rejected_qty": _int(raw.get("rjct_qty")),
                   "filled_amount": _dec(raw.get("tot_ccld_amt")), "avg_price": _dec(raw.get("avg_prvs")),
                   "order_date": str(raw.get("ord_dt") or "").strip(), "order_time": str(raw.get("ord_tmd") or "").strip(),
                   "cancelled": str(raw.get("cncl_yn") or "").strip().upper() == "Y"}
        else:
            row = {"order_no": str(raw.get("odno") or "").strip(), "orig_order_no": str(raw.get("orgn_odno") or "").strip(),
                   "symbol": str(raw.get("pdno") or "").strip().upper(), "side": str(raw.get("sll_buy_dvsn_cd") or "").strip(),
                   "ordered_qty": _int(raw.get("ft_ord_qty")), "filled_qty": _int(raw.get("ft_ccld_qty")),
                   "remaining_qty": _int(raw.get("nccs_qty")), "rejected_qty": None,
                   "filled_amount": _dec(raw.get("ft_ccld_amt3")), "avg_price": _dec(raw.get("ft_ccld_unpr3")),
                   "order_date": str(raw.get("ord_dt") or "").strip(), "order_time": str(raw.get("ord_tmd") or "").strip(),
                   "cancelled": str(raw.get("rvse_cncl_dvsn") or "").strip() == "02"}
        if row["side"] == "02" and (not row["order_no"] or not row["symbol"] or row["filled_qty"] is None):
            ok = False
        out.append(row)
    return ok, out


def _chain(rows, order_no):
    """The order plus every amendment/cancellation that points back to it (fill chaser amends)."""
    members, frontier = [], {order_no}
    seen = set()
    while frontier:
        nxt = set()
        for row in rows:
            key = row["order_no"]
            if key in seen:
                continue
            if key in frontier or (row["orig_order_no"].strip("0") and row["orig_order_no"] in frontier):
                members.append(row)
                seen.add(key)
                nxt.add(key)
        frontier = nxt - frontier
    members.sort(key=lambda row: row["order_no"] != order_no)
    return members


def _order_moment(row) -> datetime | None:
    try:
        moment = datetime.strptime(row["order_date"] + row["order_time"][:6].ljust(6, "0"), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return moment.replace(tzinfo=MARKET_TZ["KR"])  # KIS stamps KR orders in KST


def reconcile_target(target, rows, *, market, authoritative=True, reason=None) -> dict[str, Any]:
    """One target vs the broker rows of its account → the fill facts (MISSING when unproven)."""
    base = {"fill_status": "MISSING", "reason_code": reason, "match_basis": None, "order_no_matched": False,
            "ordered_qty": _int(target.get("submitted_quantity")) or _int(target.get("quantity")),
            "filled_qty": None, "remaining_qty": None, "rejected_qty": None, "avg_fill_price": None,
            "filled_amount": None, "order_date": None, "order_time": None, "fill_time": None,
            "chain_orders": 0}
    if not authoritative:
        base["reason_code"] = reason or "BROKER_QUERY_FAILED"
        return base
    symbol = str(target["symbol"]).strip().upper()
    buys = [row for row in rows if row["side"] == "02" and row["symbol"].upper() == symbol]
    order_no = str(target.get("order_no") or "").strip()
    members = []
    if order_no:
        members = _chain(buys, order_no)
        if members and any(row["order_no"] == order_no for row in members):
            base.update(match_basis="order_no" if len(members) == 1 else "order_no+amend_chain", order_no_matched=True)
        else:
            members = []
    if not members:
        known = {row["order_no"] for row in buys}
        roots = [row for row in buys if not row["orig_order_no"].strip("0") or row["orig_order_no"] not in known]
        qty = base["ordered_qty"]
        if qty is not None:
            roots = [row for row in roots if row["ordered_qty"] == qty]
        if market == "KR":
            created = _parse_time(target.get("created_at"))
            roots = [row for row in roots if created and _order_moment(row) is not None
                     and abs(_order_moment(row) - created) <= TIME_WINDOW]
            basis = "symbol_side_qty_time"
        else:
            basis = "symbol_side_qty_unique"
        if len(roots) != 1:
            base["reason_code"] = "ORDER_NOT_FOUND" if not roots else "ORDER_MATCH_AMBIGUOUS"
            return base
        members = _chain(buys, roots[0]["order_no"])
        base["match_basis"] = basis
    root = members[0]  # _chain lists the original order first
    if any(row["filled_qty"] is None for row in members):
        base["reason_code"] = "FILL_QUANTITY_MISSING"
        return base
    filled = sum(row["filled_qty"] for row in members)
    amounts = [row["filled_amount"] for row in members if row["filled_qty"]]
    amount = sum(amounts, Decimal(0)) if amounts and all(a is not None and a > 0 for a in amounts) else None
    ordered = base["ordered_qty"] if base["ordered_qty"] is not None else root["ordered_qty"]
    avg = (amount / filled) if amount is not None and filled else None
    if avg is None and filled and len([r for r in members if r["filled_qty"]]) == 1:
        avg = next(r["avg_price"] for r in members if r["filled_qty"]) or None
    base.update(filled_qty=filled, ordered_qty=ordered, order_date=root["order_date"] or None,
                order_time=root["order_time"] or None, chain_orders=len(members),
                remaining_qty=None if ordered is None else max(ordered - filled, 0),
                rejected_qty=root.get("rejected_qty"),
                avg_fill_price=float(avg) if avg is not None else None,
                filled_amount=float(amount) if amount is not None else None)
    if ordered is None or ordered <= 0:
        base.update(fill_status="MISSING", reason_code="ORDERED_QUANTITY_MISSING")
    elif filled >= ordered:
        base.update(fill_status="CONFIRMED", reason_code="BROKER_FILL_CONFIRMED")
    elif filled > 0:
        base.update(fill_status="PARTIAL", reason_code="BROKER_PARTIAL_AT_CLOSE")
    else:
        base.update(fill_status="NOT_FILLED", reason_code="BROKER_UNFILLED_AT_CLOSE")
    if base["fill_status"] in {"CONFIRMED", "PARTIAL"} and base["avg_fill_price"] is None:
        base["reason_code"] = "FILL_PRICE_MISSING"
    return base


def build_attributes(target, result, *, market, execution_profile_ref) -> dict[str, Any]:
    limit = _dec(target.get("limit_price"))
    avg = result.get("avg_fill_price")
    slip = round((avg / float(limit) - 1) * 100, 3) if avg and limit else None
    provenance_status = PROVENANCE_STATUS[result["fill_status"]]
    if provenance_status in {"CONFIRMED", "PARTIAL"} and avg is None:
        provenance_status = "UNKNOWN"
    return {
        "source": f"{market.lower()}_post_close_fill_reconcile",
        "execution_profile_ref": execution_profile_ref,
        "intent_ref": _ref(target["id"]),
        "order_ref": _ref(target.get("order_no")),
        "entry_kind": "add" if target.get("is_add") else "entry",
        "intent_source": target.get("source"),
        "add_ref": _ref(target.get("source_decision_id")) if target.get("is_add") else None,
        "submitted_limit_price": float(limit) if limit is not None else None,
        "fill_vs_limit_pct": slip,
        **result,
        "fill_provenance": {
            "schema_version": SCHEMA_VERSION, "status": provenance_status,
            "reason_code": result.get("reason_code"), "broker": "KIS",
            "submission_scope": "SINGLE_ACCOUNT", "evidence_source": EVIDENCE_SOURCE[market],
            "confirmed_fill_price": avg if provenance_status in {"CONFIRMED", "PARTIAL"} else None,
            "confirmed_fill_quantity": result.get("filled_qty") if provenance_status in {"CONFIRMED", "PARTIAL"} else None,
            "confirmed_fill_date": result.get("order_date") if provenance_status in {"CONFIRMED", "PARTIAL"} else None,
            # KIS daily inquiries give the order time, not each execution's time.
            "confirmed_fill_at": None,
            "intent_ref": _ref(target["id"]),
        },
    }


def emit(target, attributes, *, market, spool_path=None):
    decision = target.get("trace_decision_id")
    position = target.get("source_position_id")
    status = attributes["fill_status"]
    event_id = hashlib.sha256(f"entry-fill-confirmed|{market}|{target['id']}|{status}".encode()).hexdigest()[:32]
    kwargs = {"spool_path": spool_path} if spool_path else {}
    return emit_event("entry.fill_reconciled", event_id=event_id, service=f"prism-{market.lower()}-context-ledger",
                      market=market, ticker=target["symbol"],
                      trace_id=trade_trace(market, decision_id=decision, position_id=position, ticker=target["symbol"]),
                      decision_id=decision, position_id=position, attributes=attributes, **kwargs)


class EmittedState:
    """Local record of what was already emitted, so a rerun never repeats a final status."""

    def __init__(self, path=None):
        self.path = Path(path or os.getenv("FILL_RECONCILE_STATE") or ROOT / "runtime" / "fill_reconciliation_state.json")
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def should_emit(self, intent_id, status) -> bool:
        previous = self.data.get(str(intent_id))
        return previous not in FINAL_STATUSES and previous != status

    def mark(self, intent_id, status):
        self.data[str(intent_id)] = status

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, sort_keys=True))
        os.replace(tmp, self.path)
