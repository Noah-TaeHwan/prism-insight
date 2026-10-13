"""Bounded, read-only entry evidence; no inferred thesis failure or exit rule."""
import json
import math
import sqlite3

from live.scenario_notice_evidence import number


def entry_thesis(conn, active, children, observed):
    missing = dict(status="MISSING", reason="entry_provenance_unavailable")
    if not active:
        return dict(status="NOT_ACTIVE")
    try:
        sid, side = active["scenario_id"], active["side"]
        if side not in {"LONG", "SHORT"}:
            return missing
        row = conn.execute(
            "SELECT id,payload FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid LIMIT 1",
            (sid,)).fetchone()
        plan = json.loads(row[1]) if row else None
        if (not isinstance(plan, dict) or plan.get("action") != "OPEN"
                or plan.get("action_id") != row[0] or plan.get("scenario_id") != sid
                or plan.get("side") != side):
            return missing
        entries = plan.get("entries")
        if (not isinstance(entries, list) or not 1 <= len(entries) <= 8
                or any(not isinstance(e, dict) or not isinstance(e.get("id"), str) for e in entries)):
            return missing
        ids = {e["id"] for e in entries}
        if len(ids) != len(entries):
            return missing
        at = number(observed.get("captured_at"), positive=True)
        mark = number(observed.get("mark_price"), positive=True)
        stop = number(plan.get("hard_stop"), positive=True)
        if at is None or mark is None or stop is None:
            return missing
        original, all_fills, seen, anchors = [], [], set(), []
        for child in children:
            if not isinstance(child, dict):
                return missing
            if child.get("kind") != "entry":
                continue
            if child.get("scenario_id") != sid:
                return missing
            request = child.get("request", {})
            if request.get("side") != ("Buy" if side == "LONG" else "Sell"):
                return missing
            initial = child.get("intent_id") == row[0]
            if initial and child.get("local_id") not in ids:
                return missing
            if not initial:
                added_row = conn.execute(
                    "SELECT payload FROM llm_scenario_intents WHERE id=? AND scenario_id=?",
                    (child.get("intent_id"), sid)).fetchone()
                added = json.loads(added_row[0]) if added_row else None
                if (not isinstance(added, dict) or added.get("action") != "ADJUST"
                        or added.get("action_id") != child.get("intent_id")
                        or added.get("scenario_id") != sid or added.get("side") != side
                        or not isinstance(added.get("entries"), list)
                        or not any(isinstance(e, dict) and e.get("id") == child.get("local_id")
                                   for e in added["entries"])):
                    return missing
            evidence = child.get("evidence")
            if not isinstance(evidence, dict) or not isinstance(evidence.get("executions"), list):
                return missing
            if initial:
                price = number(request.get("price"), positive=True)
                trigger = number(request.get("triggerPrice"), positive=True) if "triggerPrice" in request else None
                if price is None or ("triggerPrice" in request and (trigger is None
                        or request.get("triggerBy") != "MarkPrice"
                        or request.get("triggerDirection") != (1 if side == "LONG" else 2))):
                    return missing
                anchor = dict(entry_id=child["local_id"], limit_price=price, activation_trigger=trigger)
            child_quantity = 0.0
            for fill in evidence["executions"]:
                if not isinstance(fill, dict):
                    return missing
                ident = fill.get("execId")
                when = number(fill.get("execTime"), positive=True)
                quantity = number(fill.get("execQty"), positive=True)
                price = number(fill.get("execPrice"), positive=True)
                if (not isinstance(ident, str) or not ident or ident in seen
                        or when is None or quantity is None or price is None or when / 1000 > at):
                    return missing
                # Children already carry broker-reconciled evidence; reject contrary optional fields.
                if (fill.get("side", request["side"]) != request["side"]
                        or fill.get("symbol", "BTCUSDT") != "BTCUSDT"
                        or (child.get("order_id") and fill.get("orderId", child["order_id"]) != child["order_id"])):
                    return missing
                seen.add(ident)
                child_quantity += quantity
                value = (when / 1000, quantity, price)
                all_fills.append(value)
                if initial:
                    original.append(value)
            order = evidence.get("order", {})
            if not isinstance(order, dict):
                return missing
            if child.get("order_id") and order.get("orderId", child["order_id"]) != child["order_id"]:
                return missing
            if "cumExecQty" in order:
                cumulative = number(order["cumExecQty"])
                if cumulative is None or not math.isclose(child_quantity, cumulative, rel_tol=1e-10, abs_tol=1e-12):
                    return missing
            if "qty" in request:
                requested = number(request["qty"], positive=True)
                if requested is None or child_quantity > requested + 1e-12:
                    return missing
            if initial and child_quantity:
                if len(anchor["entry_id"]) > 128:
                    return missing
                anchors.append(dict(**anchor, confirmed_filled_quantity=child_quantity))
        if not original:
            return dict(status="MISSING" if all_fills else "NO_CONFIRMED_INITIAL_FILL",
                        reason="initial_fill_not_confirmed")
        if not 1 <= len(anchors) <= 8:
            return missing
        quantity = sum(f[1] for f in original)
        average = sum(f[1] * f[2] for f in original) / quantity
        first = min(f[0] for f in original)
        if "created_at" in active:
            created = number(active["created_at"], positive=True)
            if created is None or first < created:
                return missing
        if min(f[0] for f in all_fills) != first:
            return missing
        result = dict(status="VERIFIED", side=side, initial_hard_stop=stop,
            initial_order_anchors=anchors, initial_filled_quantity=quantity,
            initial_fill_average=average, first_fill_at=first,
            latest_entry_fill_at=max(f[0] for f in all_fills),
            additional_entry_fills=len(all_fills) - len(original),
            elapsed_since_first_fill_seconds=at-first, observed_at=at,
            current_mark=mark, favorable_price_change_from_initial_average=(mark-average)*(1 if side == "LONG" else -1),
            scope="Original OPEN fills, not remaining-lot basis. Current endpoint only; no MFE/MAE or intervening path. Activation triggers are not automatic invalidation levels.")
        if len(json.dumps(result, allow_nan=False).encode()) > 2048:
            return dict(status="MISSING", reason="entry_evidence_size")
        return result
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return missing
