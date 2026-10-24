"""Durable one-shot reductions; never cancel native Full SL or refill exit quotas."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_DOWN


def _wire(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS llm_scenario_reductions(
        intent_id TEXT PRIMARY KEY, body TEXT NOT NULL, sha256 TEXT NOT NULL)""")


def _save(broker, ident, body, *, commit=True):
    raw = _wire(body)
    broker.conn.execute("INSERT OR REPLACE INTO llm_scenario_reductions VALUES(?,?,?)",
                        (ident, raw, hashlib.sha256(raw.encode()).hexdigest()))
    if commit:
        broker.conn.commit()


def load(broker, ident):
    row = broker.conn.execute("SELECT body,sha256 FROM llm_scenario_reductions WHERE intent_id=?", (ident,)).fetchone()
    if not row:
        broker._fail("reduce_preparation_missing")
    if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
        broker._fail("reduce_preparation_corrupt")
    return json.loads(row[0])


def prepare(broker, payload, ident):
    """No network or commit: caller atomically commits this with the intent."""
    from live.scenario_execution import decimal
    active = broker._active()
    plan = payload.get("reduce_plan") or {}
    if not active or payload.get("action") != "REDUCE" or active["scenario_id"] != payload["scenario_id"]:
        broker._fail("reduce_active_required")
    q, d, r = (decimal(plan.get(k)) for k in ("quantity_before", "requested_quantity", "target_remainder"))
    if not 0 < d < q or q - d != r:
        broker._fail("reduce_quantity_invalid")
    if broker.conn.execute("SELECT 1 FROM llm_scenario_reductions WHERE intent_id=?", (ident,)).fetchone():
        broker._fail("reduce_already_prepared")
    sources = []
    children = broker.children(active["scenario_id"])
    if any(not c["evidence"] or c["status"] == "UNKNOWN" or
           (c["kind"] in {"entry", "reduce", "exit"} and c["status"] != "TERMINAL") for c in children):
        broker._fail("reduce_unreconciled_children")
    for child in children:
        if child["kind"] not in {"tp", "partial_sl"} or child["status"] != "LIVE":
            continue
        order = child["evidence"]["order"]
        if (child["request"].get("reduceOnly") is not True or order.get("reduceOnly") is not True
                or child["request"].get("side") != ("Sell" if active["side"] == "LONG" else "Buy")):
            broker._fail("reduce_source_terms_invalid")
        quota = decimal(order["leavesQty"])
        if quota <= 0 or quota > q:
            broker._fail("reduce_source_quota_invalid")
        sources.append(dict(link_id=child["link_id"], order_id=child["order_id"],
                            kind=child["kind"], request=child["request"],
                            quota=str(quota), filled=str(decimal(order["cumExecQty"]))))
    if any(sum((decimal(s["quota"]) for s in sources if s["kind"] == kind), Decimal(0)) > q
           for kind in ("tp", "partial_sl")):
        broker._fail("reduce_source_quota_invalid")
    native_stops = [dict(link_id=c["link_id"], order_id=c["order_id"])
                    for c in children if c["kind"] == "native_sl" and c["status"] == "LIVE"]
    if not native_stops:
        broker._fail("reduce_native_protection_missing")
    body = dict(version=1, status="PREPARED", payload=payload, side=active["side"],
                native_stops=native_stops,
                hard_stop=active["hard_stop"], quantity_before=str(q), requested_quantity=str(d),
                target_remainder=str(r), sources=sources)
    _save(broker, ident, body, commit=False)


def _failed(active):
    return bool(active.get("entry_contract_failure") or active.get("entry_contract_model_failure")
                or (active.get("entry_contract_observation") or {}).get("status") == "FAILED"
                or (active.get("last_thesis_assessment") or {}).get("status") == "FAILED")


def _observation(broker, body):
    from live.scenario_execution import decimal
    active = broker._active()
    for child in broker.children(active["scenario_id"]):
        if child["status"] != "TERMINAL":
            broker._query_child(child)
    observed = broker.capture_account()
    if observed["legacy_fenced"]:
        broker._fail("reduce_external_exposure")
    q = decimal(observed["position"]["size"])
    if q and (observed["position"].get("side") != ("Buy" if body["side"] == "LONG" else "Sell")
              or not broker._verify_protection(observed, body["hard_stop"], body["side"])):
        broker._fail("reduce_protection_unconfirmed")
    own = broker.children(active["scenario_id"])
    if any(c["status"] == "UNKNOWN" or not c["evidence"] or
           (c["kind"] == "entry" and c["status"] != "TERMINAL") for c in own):
        broker._fail("reduce_unreconciled_children")
    accounting = broker._risk_accounting(observed, active, own)
    if not accounting or accounting.get("status") != "confirmed":
        broker._fail("reduce_accounting_unconfirmed")
    if sum((decimal(p["quantity"]) for p in accounting["positions"]), Decimal(0)) != q:
        broker._fail("reduce_accounting_quantity_conflict")
    return observed, q


def execute(broker, payload, ident):
    """Restart-safe state machine: cancellation, one IOC, exact result, restore."""
    from live.scenario_execution import decimal, text_number
    body = load(broker, ident)
    if body["payload"] != payload:
        broker._fail("reduce_preparation_changed")
    if body["status"] == "COMPLETE":
        return body
    latest = broker.conn.execute("SELECT id FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC LIMIT 1",
                                 (payload["scenario_id"],)).fetchone()
    if not latest or latest[0] != ident:
        broker._fail("reduce_superseded_intent")
    instrument = broker._instrument()
    q0, d0, r0 = (decimal(body[k]) for k in ("quantity_before", "requested_quantity", "target_remainder"))
    fraction = decimal(payload.get("reduce_fraction"))
    expected = (q0 * fraction / instrument["step"]).to_integral_value(rounding=ROUND_DOWN) * instrument["step"]
    if (not 0 < fraction < 1 or d0 != expected or q0 - d0 != r0 or q0 % instrument["step"]
            or d0 < instrument["minimum"] or r0 < instrument["minimum"]
            or d0 > instrument["market_maximum"]):
        broker._fail("reduce_quantity_invalid")
    own = {c["link_id"]: c for c in broker.children(payload["scenario_id"])}
    for source in body["sources"]:
        child = own.get(source["link_id"])
        if not child or child["order_id"] != source["order_id"] or child["request"] != source["request"]:
            broker._fail("reduce_source_changed")
    if body["status"] == "PREPARED":
        _, before = _observation(broker, body)
        if before > decimal(body["quantity_before"]):
            broker._fail("reduce_position_increased")
        # Even expired intents finish cancellation/protection recovery; no new
        # reduction will be submitted after the original deadline.
        for source in body["sources"]:
            broker._cancel(own[source["link_id"]])
        _, q1 = _observation(broker, body)
        if q1 > before:
            broker._fail("reduce_position_increased")
        remaining = []
        own = {c["link_id"]: c for c in broker.children(payload["scenario_id"])}
        for source in body["sources"]:
            child = own[source["link_id"]]
            filled = decimal(child["evidence"]["order"]["cumExecQty"]) - decimal(source["filled"])
            if filled < 0 or filled > decimal(source["quota"]):
                broker._fail("reduce_source_fill_conflict")
            remaining.append(dict(source, unfilled=str(decimal(source["quota"]) - filled)))
        quantity = max(Decimal(0), q1 - decimal(body["target_remainder"]))
        if quantity and (quantity % instrument["step"] or quantity < instrument["minimum"]
                         or quantity > instrument["market_maximum"]):
            # Dust cannot be rounded up into the retained remainder.
            quantity = Decimal(0)
        body.update(status="CANCELLED", quantity_after_cancels=str(q1),
                    market_quantity=str(quantity), sources=remaining)
        _save(broker, ident, body)
    if body["status"] == "CANCELLED":
        own = broker.children(payload["scenario_id"])
        market = next((c for c in own if c["intent_id"] == ident and c["kind"] == "reduce"), None)
        if market:
            market = broker._query_child(market)
        else:
            _, size = _observation(broker, body)
            quantity = min(decimal(body["market_quantity"]), max(Decimal(0), size - decimal(body["target_remainder"])))
            if _failed(broker._active()) or broker.clock() >= payload["expires_at"]:
                quantity = Decimal(0)
            if quantity >= instrument["minimum"] and quantity <= instrument["market_maximum"]:
                market = broker._submit(payload, ident, "reduce", "once", dict(
                    side="Sell" if body["side"] == "LONG" else "Buy", orderType="Market",
                    timeInForce="IOC", qty=text_number(quantity), reduceOnly=True, closeOnTrigger=True))
        if market and market["status"] != "TERMINAL":
            broker._fail("reduce_result_pending")
        _, size = _observation(broker, body)
        basis = decimal(body["quantity_after_cancels"])
        if size > basis:
            broker._fail("reduce_position_increased")
        body.update(status="RESTORING", remaining_quantity=str(size))
        _save(broker, ident, body)
    if body["status"] == "RESTORING":
        _, size = _observation(broker, body)
        basis = decimal(body["quantity_after_cancels"])
        original_remaining = decimal(body["remaining_quantity"])
        # Fixed restoration quotas survive retries and racing restored fills.
        allocated = {"tp": Decimal(0), "partial_sl": Decimal(0)}
        for source in body["sources"]:
            _, size = _observation(broker, body)
            quota = decimal(source["unfilled"])
            quantity = (quota * original_remaining / basis / instrument["step"]).to_integral_value(rounding=ROUND_DOWN) * instrument["step"] if basis else Decimal(0)
            quantity = min(quantity, quota)
            kind = source["kind"]
            allocated[kind] += quantity
            if allocated[kind] > original_remaining or quantity > instrument["maximum"]:
                broker._fail("reduce_restore_quota_invalid")
            local_id = "reduce:" + source["link_id"]
            previous = next((c for c in broker.children(payload["scenario_id"]) if
                             c["intent_id"] == ident and c["kind"] == kind and c["local_id"] == local_id), None)
            if previous:
                broker._query_child(previous)
                _, size = _observation(broker, body)
                continue
            # A restored exit can fill before the next sibling is submitted.
            # Never reserve that closed quantity again in either exit group.
            outstanding = sum((decimal(c["evidence"]["order"]["leavesQty"])
                               for c in broker.children(payload["scenario_id"])
                               if c["intent_id"] == ident and c["kind"] == kind
                               and c["status"] == "LIVE"), Decimal(0))
            quantity = min(quantity, max(Decimal(0), size - outstanding))
            quantity = (quantity / instrument["step"]).to_integral_value(rounding=ROUND_DOWN) * instrument["step"]
            if not size or quantity < instrument["minimum"]:
                continue
            request = {k: v for k, v in source["request"].items()
                       if k not in {"orderLinkId", "category", "symbol", "positionIdx", "qty"}}
            request["qty"] = text_number(quantity)
            broker._submit(payload, ident, kind, local_id, request)
            _, size = _observation(broker, body)
        body["status"] = "COMPLETE"
        _save(broker, ident, body)
    return body
