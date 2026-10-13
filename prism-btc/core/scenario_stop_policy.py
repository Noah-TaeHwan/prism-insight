"""Host-only boundaries for limited stop restoration, never a risk-budget override."""
import math


def _price(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def is_widening(side, new_stop, old_stop):
    return new_stop < old_stop if side == "LONG" else new_stop > old_stop


def build_stop_policy(active, *, hard_blocked, permit_valid, accounting_status,
                      protection_ok, legacy_fenced, pending_entries, positions,
                      unknown_intents=False):
    anchor = (active or {}).get("stop_policy") or {}
    if not isinstance(anchor, dict):
        anchor = {}
    valid = (type(anchor.get("version")) is int and anchor["version"] == 1
             and _price(anchor.get("initial_hard_stop"))
             and isinstance(anchor.get("initial_action_id"), str)
             and 0 < len(anchor["initial_action_id"]) <= 80)
    reason = ("no_active_scenario" if not active else "legacy_or_invalid_anchor" if not valid else
              "hard_risk_blocked" if hard_blocked is not False else
              "invalid_recovery_permission" if permit_valid is not True else
              "unconfirmed_accounting" if accounting_status != "confirmed" else
              "unconfirmed_protection" if protection_ok is not True else
              "legacy_exposure" if legacy_fenced is not False else
              "unresolved_orders" if unknown_intents is not False or pending_entries != [] else
              "no_confirmed_position" if not isinstance(positions, list) or not positions else None)
    return dict(version=1, anchor_available=valid,
                initial_hard_stop=anchor["initial_hard_stop"] if valid else None,
                initial_action_id=anchor["initial_action_id"] if valid else None,
                widening_eligible=reason is None, reason=reason,
                risk_budget_override=False)


def validate_stop_revision(payload, context):
    """Check bounded metadata and trusted permission. Caller MUST recheck risk.

    Strings preserve the model's structural explanation, not proof it is true.
    The broker additionally verifies the persisted original OPEN and fresh state.
    """
    policy, meta = context.get("stop_policy"), payload.get("stop_revision")
    if (not isinstance(policy, dict) or type(policy.get("version")) is not int
            or policy["version"] != 1 or policy.get("anchor_available") is not True
            or policy.get("widening_eligible") is not True):
        raise ValueError("stop revision not authorized")
    if (payload.get("action") != "ADJUST" or payload.get("entries") != []
            or payload.get("cancel_entry_ids", []) != [] or context.get("pending_entries") != []
            or not context.get("positions")):
        raise ValueError("stop revision requires settled holding only")
    if not isinstance(meta, dict) or set(meta) != {"reason", "structure_reference", "input_id"}:
        raise ValueError("stop revision evidence required")
    if any(not isinstance(meta[k], str) or not 1 <= len(meta[k].strip()) <= 240
           for k in ("reason", "structure_reference")):
        raise ValueError("stop revision evidence invalid")
    if (not isinstance(meta["input_id"], str) or not meta["input_id"]
            or meta["input_id"] != payload.get("input_id")
            or meta["input_id"] != context.get("input_id")):
        raise ValueError("stop revision input mismatch")
    old, new, initial, mark = (context.get("previous_hard_stop"), payload.get("hard_stop"),
                             policy.get("initial_hard_stop"), context.get("mark_price"))
    if not all(_price(v) for v in (old, new, initial, mark)):
        raise ValueError("stop revision prices invalid")
    side = payload.get("side")
    if side != context.get("side") or side not in ("LONG", "SHORT"):
        raise ValueError("stop revision side mismatch")
    if ((side == "LONG" and not initial <= new < old < mark)
            or (side == "SHORT" and not mark < old < new <= initial)):
        raise ValueError("stop revision outside initial boundary or old stop crossed")
