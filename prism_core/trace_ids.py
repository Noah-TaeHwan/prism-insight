"""The position trace id shared by every event of one trade (observation only).

Same rule as observability.trading_context / holding_decisions: identity = the decision id, else the
position id, else the ticker; trace = sha256("trade-trace|<MARKET>|<identity>")[:32]. Events that
know only a position id (micro-split, runner) must still look the decision id up so they land on
the trace the buy and sell events use.
"""
from __future__ import annotations

import hashlib
import json


def decision_of(scenario):
    """``_decision_id`` from a scenario dict or JSON string, else None."""
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario)
        except ValueError:
            return None
    value = scenario.get("_decision_id") if isinstance(scenario, dict) else None
    return str(value).strip() or None if value else None


def trade_trace(market, *, decision_id=None, position_id=None, ticker=None):
    identity = (str(decision_id or "").strip() or str(position_id or "").strip()
                or str(ticker or "").strip().upper())
    if not identity:
        return None
    return hashlib.sha256(f"trade-trace|{str(market or '').upper()}|{identity}".encode()).hexdigest()[:32]
