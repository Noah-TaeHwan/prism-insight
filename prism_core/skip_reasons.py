"""Stable codes for why a KR/US new entry was not taken, plus the gate numbers (observation only).

The user-facing skip reason is Korean/English prose built from the same flags; the ledger needs
codes so later analysis can group "AI said enter, the sector cap blocked it" without parsing text.
Codes follow the order the reason text lists them. Nothing here changes a decision.
"""
from __future__ import annotations

AI_NO_ENTRY = "AI_NO_ENTRY"
SCORE_BELOW_MIN = "SCORE_BELOW_MIN"
SECTOR_CAP = "SECTOR_CAP"
DETERMINISTIC_GATE = "DETERMINISTIC_GATE"
REENTRY_COOLDOWN = "REENTRY_COOLDOWN"
REGIME_SCORE_FLOOR = "REGIME_SCORE_FLOOR"
OTHER = "OTHER"


def skip_codes(*, ai_entry, score_below_min=False, sector_blocked=False, gate=None, cooldown=False,
               regime_floor=False):
    codes = []
    if not ai_entry:
        codes.append(AI_NO_ENTRY)
    if score_below_min:
        codes.append(SCORE_BELOW_MIN)
    if sector_blocked:
        codes.append(SECTOR_CAP)
    if ai_entry and isinstance(gate, dict) and not gate.get("allowed", False):
        codes.append(DETERMINISTIC_GATE)
    if cooldown:
        codes.append(REENTRY_COOLDOWN)
    if regime_floor:
        codes.append(REGIME_SCORE_FLOOR)
    return codes or [OTHER]


def gate_numbers(gate):
    """The deterministic gate's codes and the numbers it judged with (None when unknown)."""
    if not isinstance(gate, dict):
        return {}
    findings = gate.get("findings") or []
    out = {"gate_hard_codes": [f.get("code") for f in findings if isinstance(f, dict) and f.get("hard")],
           "gate_soft_codes": [f.get("code") for f in findings if isinstance(f, dict) and not f.get("hard")],
           "recomputed_rr": gate.get("recomputed_rr"), "effective_regime": gate.get("effective_regime"),
           "required_score": (gate.get("score_policy") or {}).get("required_score")}
    try:
        from prism_core.reentry_campaign import REGIME_RULES   # the shared buy table, loaded by path
        rule = REGIME_RULES.get(str(gate.get("effective_regime") or "")) or {}
    except Exception:  # noqa: BLE001
        rule = {}
    out["rr_floor"] = rule.get("rr_floor")
    out["max_loss_pct"] = rule.get("max_loss_pct")
    return out


def decision_fields(**flags):
    """{skip_reason_codes, gate numbers} for a skip's decision_context."""
    gate = flags.get("gate")
    return {"skip_reason_codes": skip_codes(**flags), **gate_numbers(gate)}
