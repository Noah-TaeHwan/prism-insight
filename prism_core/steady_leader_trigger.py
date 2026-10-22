"""Screening trigger T-B "Steady Leader Pullback" (꾸준 상승 주도주 눌림) for the afternoon batches.

``STEADY_LEADER_TRIGGER_MODE`` = off | observe | live (default off).

- off: nothing runs; the batch output is unchanged.
- observe: after the regular selection, run the detector (``prism_core.steady_leader``) on the
  batch's own eligible universe and write the result to the trigger JSON metadata
  (``steady_leader``) and the candidate ledger (mode ``steady_leader_observe``). Candidates never
  enter selection, BUY prompts or orders.
- live: the same detection, then at most one T-B candidate per market per day joins the final
  selection as a normal trigger (KR key "꾸준 상승 주도주 눌림", US key "Steady Leader Pullback").
  The regime's hard selection cap (``max_selections``, including the post-FTD pilot cap and the
  weak-regime plans) is never exceeded: with a free slot the pick is added; with every slot
  taken it replaces the lowest-scoring bottom-up pick, and only when the cap is at least 2. A
  top-down pick is never replaced, and a single-slot cap is never displaced. Ledger rows use
  mode ``steady_leader_live`` with ``selected`` set for the pick.

Signals use completed daily bars only. When the batch runs before the session's close (KR
15:30 KST, US 16:00 ET) the evaluated session is the previous completed one and the record says
so (``signal_date`` vs ``batch_trade_date``). Any error leaves the batch unchanged (fail-open).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Callable, Mapping

import pandas as pd

from prism_core import steady_leader as sl
from prism_core.ohlcv_shape import normalize_single_ticker_ohlcv
from prism_core.pocket_pivot_trigger import _iso, _trim, observe_universe, session_complete

MODE_ENV = "STEADY_LEADER_TRIGGER_MODE"
LEDGER_MODE_OBSERVE = "steady_leader_observe"
LEDGER_MODE_LIVE = "steady_leader_live"
TRIGGER_KEYS = {"KR": sl.TRIGGER_LABEL_KO, "US": sl.TRIGGER_TYPE}
LIVE_MAX_PICKS = 1              # T-B picks added per market per day
HISTORY_SESSIONS = 300          # >= 253 for the 252-session high, with slack
DEFAULT_MAX_TICKERS = 100       # history fetches per batch (most liquid first)
DEFAULT_BUDGET_SECONDS = 120.0  # wall-clock cap for the history fetches
_FEATURE_FIELDS = (
    "signal_date", "close", "atr20pct", "ma10", "ma20", "ma50", "ma200", "ma50_slope10_pct",
    "ma200_slope20_pct", "high_252", "close_vs_high_252", "high_252_vs_close_pct", "pullback_date",
    "signature_strength", "gap_atr", "chg_atr", "steadiness", "ret_120", "structural_stop",
    "structural_stop_pct",
)

logger = logging.getLogger(__name__)

__all__ = ["MODE_ENV", "LEDGER_MODE_OBSERVE", "LEDGER_MODE_LIVE", "TRIGGER_KEYS", "resolve_mode",
           "session_complete", "observe_universe", "evaluate", "merge_live", "unmark_displaced",
           "pick_columns", "export_fields", "ledger_rows", "record_ledger"]


def resolve_mode(log: logging.Logger | None = None) -> str:
    """'off', 'observe' or 'live'. Unknown values are refused (treated as off)."""
    log = log or logger
    raw = os.getenv(MODE_ENV, "off").strip().lower() or "off"
    if raw in ("off", "observe", "live"):
        return raw
    log.warning("[STEADY-LEADER] unknown %s=%r; behaving as off", MODE_ENV, raw)
    return "off"


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def evaluate(*, market: str, mode: str, batch_trade_date: str, signal_date: str,
             universe: pd.DataFrame, fetch_history: Callable[[str, str, int], pd.DataFrame],
             names: Mapping[str, str] | None = None, universe_basis: Mapping | None = None,
             log: logging.Logger | None = None, clock: Callable[[], float] = time.monotonic) -> dict:
    """Detect T-B for ``universe`` (index = tickers, ``Amount`` = signal-day value).

    ``universe`` must already respect the batch's liquidity and market-cap floors. Tickers are
    fetched most liquid first, bounded by ``STEADY_LEADER_MAX_TICKERS`` and
    ``STEADY_LEADER_BUDGET_SECONDS``; the rest are counted, never guessed.
    """
    log = log or logger
    market = market.upper()
    max_tickers = int(_env_number("STEADY_LEADER_MAX_TICKERS", DEFAULT_MAX_TICKERS))
    budget = _env_number("STEADY_LEADER_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS)
    ordered = list(universe.sort_values("Amount", ascending=False, kind="mergesort").index)
    started = clock()
    statuses = dict.fromkeys(("PASS", "FAIL", "MISSING"), 0)
    reasons: dict[str, int] = {}
    rows = []
    fetched = skipped_cap = skipped_budget = 0
    for position, ticker in enumerate(ordered):
        if position >= max_tickers:
            skipped_cap = len(ordered) - position
            break
        if clock() - started >= budget:
            skipped_budget = len(ordered) - position
            break
        fetched += 1
        try:
            history = _trim(normalize_single_ticker_ohlcv(
                fetch_history(ticker, signal_date, HISTORY_SESSIONS), ticker), signal_date)
        except Exception as exc:  # noqa: BLE001 - one provider failure is one MISSING row
            log.debug("[STEADY-LEADER] history unavailable for %s: %s", ticker, type(exc).__name__)
            history = pd.DataFrame()
        result = sl.detect(history, signal_date=_iso(signal_date))
        statuses[result["status"]] += 1
        for code in result["reason_codes"]:
            reasons[code] = reasons.get(code, 0) + 1
        if result["status"] == "PASS":
            m = result["metrics"]
            rows.append({"ticker": str(ticker), "name": (names or {}).get(ticker),
                         "status": result["status"], **{f: m.get(f) for f in _FEATURE_FIELDS}})
    ranked = sl.rank_candidates(rows)
    block = {
        "mode": mode,
        "trigger_type": sl.TRIGGER_TYPE,
        "trigger_key": TRIGGER_KEYS[market],
        "definition_version": sl.DEFINITION_VERSION,
        "market": market,
        "batch_trade_date": batch_trade_date,
        "signal_date": signal_date,
        "universe_basis": dict(universe_basis or {}),
        "coverage": {"universe": len(ordered), "fetched": fetched,
                     "skipped_max_tickers": skipped_cap, "skipped_budget": skipped_budget,
                     "max_tickers": max_tickers, "budget_seconds": budget,
                     "elapsed_seconds": round(clock() - started, 3)},
        "status_counts": statuses,
        "reason_counts": dict(sorted(reasons.items())),
        "candidates": ranked,
        "top_tickers": [row["ticker"] for row in ranked if row["top"]],
    }
    log.info("[STEADY-LEADER] mode=%s market=%s signal_date=%s universe=%d fetched=%d pass=%d top=%s",
             mode, market, signal_date, len(ordered), fetched, statuses["PASS"], block["top_tickers"])
    return block


def _score(frame: pd.DataFrame, ticker, score_column: str | None) -> float:
    if score_column and score_column in frame.columns:
        value = _number(frame.loc[[ticker], score_column].iloc[0])
        if value is not None:
            return value
    return -math.inf


def merge_live(final_results: Mapping[str, pd.DataFrame], block: Mapping, *, max_selections: int,
               pick_frame: Callable[[str, Mapping], pd.DataFrame | None],
               score_column: str | None, log: logging.Logger | None = None
               ) -> tuple[dict[str, pd.DataFrame], dict]:
    """Add at most one T-B pick to ``final_results`` within the hard cap ``max_selections``.

    ``pick_frame(ticker, candidate)`` returns the one-row frame the batch exports for that
    ticker (or None when the batch has no current row for it). Returns a new dict (input frames
    are not mutated) and the decision record. Displacement (all slots taken, cap >= 2) removes
    the lowest-scoring bottom-up pick; ties go to the later one in iteration order.
    """
    log = log or logger
    result = {name: frame for name, frame in final_results.items()}
    key = block["trigger_key"]
    picks = [(name, ticker) for name, frame in result.items() for ticker in frame.index]
    selected = {str(ticker) for _, ticker in picks}
    cap = max(0, int(max_selections))
    decision = {"applied": False, "selected_ticker": None, "displaced_ticker": None,
                "displaced_trigger": None, "max_selections": cap, "selected_before": len(selected),
                "selected_after": len(selected), "reason": None}
    if cap <= 0:
        decision["reason"] = "REGIME_CAP_ZERO"
        return result, decision
    chosen = frame = None
    for candidate in block.get("candidates") or []:
        if not candidate.get("top") or candidate["ticker"] in selected:
            continue
        frame = pick_frame(candidate["ticker"], candidate)
        if frame is not None and not frame.empty:
            chosen = candidate
            break
    if chosen is None:
        decision["reason"] = ("NO_CANDIDATE" if not block.get("top_tickers") else
                              "TOP_ALREADY_SELECTED_OR_UNPRICED")
        return result, decision
    if len(selected) >= cap:
        if cap < 2:
            decision["reason"] = "SINGLE_SLOT_CAP_FULL"
            return result, decision
        bottom_up = []
        for order, (name, ticker) in enumerate(picks):
            source = result[name]
            channel = (source.loc[[ticker], "SelectionChannel"].iloc[0]
                       if "SelectionChannel" in source.columns else "bottom-up")
            if channel == "bottom-up":
                bottom_up.append((_score(source, ticker, score_column), -order, name, ticker))
        if not bottom_up:
            decision["reason"] = "NO_BOTTOM_UP_TO_REPLACE"
            return result, decision
        _, _, name, ticker = min(bottom_up)
        remaining = result[name].drop(index=[ticker])
        if remaining.empty:
            del result[name]
        else:
            result[name] = remaining
        decision.update(displaced_ticker=str(ticker), displaced_trigger=str(name))
        log.info("[STEADY-LEADER] live pick replaces %s (trigger=%s) under cap %d",
                 ticker, name, cap)
    tagged = frame.copy()
    tagged["SelectionChannel"] = "bottom-up"
    result[key] = pd.concat([result[key], tagged]) if key in result else tagged
    decision.update(applied=True, selected_ticker=chosen["ticker"], reason="ADDED" if
                    decision["displaced_ticker"] is None else "REPLACED_LOWEST_BOTTOM_UP")
    decision["selected_after"] = sum(len(f.index) for f in result.values())
    log.info("[STEADY-LEADER] live pick %s (%s) selected=%d/%d", chosen["ticker"], key,
             decision["selected_after"], cap)
    return result, decision


EXPORT_COLUMNS = {
    "steady_leader_signal_date": "signal_date",
    "steady_leader_rank_score": "rank_score",
    "steady_leader_structural_stop": "structural_stop",
    "steady_leader_structural_stop_pct": "structural_stop_pct",
    "steady_leader_high_252": "high_252",
}


def pick_columns(candidate: Mapping) -> dict:
    """T-B evidence columns for the exported pick row (signal session, rank, natural stop)."""
    return {column: candidate.get(field) for column, field in EXPORT_COLUMNS.items()}


def export_fields(row: pd.Series) -> dict:
    """JSON-safe ``steady_leader`` block for one exported pick row."""
    out = {}
    for column, field in EXPORT_COLUMNS.items():
        value = row.get(column)
        if field == "signal_date":
            out[field] = None if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
        else:
            out[field] = _number(value)
    return out


def unmark_displaced(rows: list[dict] | None, ticker: str | None) -> None:
    """Clear the selected flag of a replaced pick in the regular candidate-ledger rows."""
    if not rows or not ticker:
        return
    for row in rows:
        if str(row.get("ticker")) == str(ticker) and row.get("selected"):
            row.update(selected=0, selected_trigger=None, selection_channel=None)


def ledger_rows(block: Mapping, universe: pd.DataFrame | None = None) -> list[dict]:
    """Candidate-ledger rows; ``selected`` is 1 only for a live pick."""
    picked = (block.get("live") or {}).get("selected_ticker")
    candidates = block.get("candidates") or []
    rows = []
    for item in candidates:
        ticker = item["ticker"]
        amount = volume = change = None
        if universe is not None and ticker in universe.index:
            record = universe.loc[ticker]
            amount, volume = _number(record.get("Amount")), _number(record.get("Volume"))
            change = _number(record.get("change_rate"))
        features = {**{k: v for k, v in item.items() if k != "name"},
                    "batch_trade_date": block.get("batch_trade_date"),
                    "definition_version": block.get("definition_version")}
        chosen = picked is not None and ticker == picked
        rows.append({"trigger_type": block.get("trigger_key") or sl.TRIGGER_TYPE, "ticker": ticker,
                     "name": item.get("name"), "rank_in_trigger": item.get("rank"),
                     "candidates_in_trigger": len(candidates),
                     "composite_score": _number(item.get("rank_score")),
                     "score_column": "steady_leader_rank_score", "close": _number(item.get("close")),
                     "volume": volume, "amount": amount, "change_rate": change,
                     "selected": 1 if chosen else 0,
                     "selected_trigger": block.get("trigger_key") if chosen else None,
                     "selection_channel": "bottom-up" if chosen else None,
                     "features_json": json.dumps(features, ensure_ascii=False, sort_keys=True,
                                                 default=str)})
    return rows


def record_ledger(block: Mapping | None, *, universe: pd.DataFrame | None, market: str,
                  market_regime: str | None, log: logging.Logger | None = None) -> None:
    """Write the T-B rows (trade_date = signal session). Fail-open."""
    log = log or logger
    if not block:
        return
    try:
        rows = ledger_rows(block, universe)
        if rows:
            from observability import candidate_ledger
            mode = LEDGER_MODE_LIVE if block.get("mode") == "live" else LEDGER_MODE_OBSERVE
            candidate_ledger.record(rows, market=market, trade_date=block["signal_date"], mode=mode,
                                    market_regime=market_regime, log=log)
    except Exception as exc:  # noqa: BLE001
        log.warning("[STEADY-LEADER] ledger unavailable: %s", type(exc).__name__)
