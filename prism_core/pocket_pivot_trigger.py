"""Screening trigger "Pocket Pivot Base" (layer L1): observation only.

``POCKET_PIVOT_TRIGGER_MODE`` = off | observe | live (default off).

- off: nothing runs; the batch output is unchanged.
- observe: after the regular selection, evaluate the base pocket pivot detector
  (``prism_core.pocket_pivot``) on the batch's own eligible universe and write the result to the
  trigger JSON metadata (``pocket_pivot_observe``) and the candidate ledger
  (mode ``pocket_pivot_observe``). Candidates never enter ``select_final_tickers``, ranking,
  BUY prompts or orders.
- live: refused. Selection/BUY branches (L2/L3) do not exist and KR is blocked until KR evidence
  exists (``pocket_pivot.live_allowed``); the batch behaves exactly as off.

Signals use completed daily bars only. When the batch runs before the session's close (US
afternoon batch, 15:10 ET) the evaluated session is the previous completed one and the record says
so (``signal_date`` vs ``batch_trade_date``). Any error leaves the batch unchanged (fail-open).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os
import time
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from prism_core import pocket_pivot as pp
from prism_core.ohlcv_shape import normalize_single_ticker_ohlcv

MODE_ENV = "POCKET_PIVOT_TRIGGER_MODE"
LEDGER_MODE = "pocket_pivot_observe"
HISTORY_SESSIONS = 300          # >= 252 + 1 for the PP0 window, with slack
DEFAULT_MAX_TICKERS = 100       # history fetches per batch (most liquid first)
DEFAULT_BUDGET_SECONDS = 120.0  # wall-clock cap for the history fetches
_SESSION_CLOSE = {"KR": ("Asia/Seoul", 15, 30), "US": ("America/New_York", 16, 0)}
_FEATURE_FIELDS = (
    "signal_date", "close", "atr20pct", "ma50", "close_vs_ma50", "low_vs_ma10", "pp2_window",
    "signature_strength", "base_length", "base_window", "base_high_date", "pivot_252", "base_depth",
    "depth_bucket", "pp5_range20", "pp5_ratio_atr", "vol_dryup_10_50", "pp6_drift10_atr",
    "pp6_rising_into_pp", "gap_atr", "chg_atr", "kind", "quiet_pp", "pp_seq_in_base",
    "pivot_base", "pivot_base_vs_close_pct", "structural_stop", "structural_stop_pct",
    "v_spike_peak_date", "v_spike_break_date",
)

logger = logging.getLogger(__name__)


def resolve_mode(log: logging.Logger | None = None) -> str:
    """'observe' or 'off'. 'live' and unknown values are refused (treated as off)."""
    log = log or logger
    raw = os.getenv(MODE_ENV, "off").strip().lower() or "off"
    if raw == "observe":
        return "observe"
    if raw == "live":
        log.warning("[POCKET-PIVOT] %s=live refused: selection/BUY layers (L2/L3) are not "
                    "implemented; behaving as off", MODE_ENV)
    elif raw != "off":
        log.warning("[POCKET-PIVOT] unknown %s=%r; behaving as off", MODE_ENV, raw)
    return "off"


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def session_complete(market: str, trade_date: str, now: _dt.datetime | None = None) -> bool:
    """True when ``trade_date``'s regular session has closed in the exchange's time zone."""
    zone, hour, minute = _SESSION_CLOSE[market.upper()]
    tz = ZoneInfo(zone)
    now = now.astimezone(tz) if now is not None else _dt.datetime.now(tz)
    day = _dt.datetime.strptime(trade_date, "%Y%m%d").date()
    if now.date() != day:
        return now.date() > day
    return (now.hour, now.minute) >= (hour, minute)


def _iso(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}" if len(day) == 8 and day.isdigit() else str(day)


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rs_percentiles(raw: Mapping[str, float | None]) -> dict[str, float | None]:
    known = pd.Series({t: v for t, v in raw.items() if v is not None}, dtype=float)
    pct = (known.rank(pct=True) * 100).to_dict() if not known.empty else {}
    return {t: (float(pct[t]) if t in pct else None) for t in raw}


def _trim(history: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    """Rows dated on or before the signal session (no later bar can leak in)."""
    if history.empty:
        return history
    index = pd.to_datetime(history.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    frame = history.copy()
    frame.index = index.normalize()
    return frame.loc[frame.index <= pd.Timestamp(_iso(signal_date))]


def evaluate(*, market: str, batch_trade_date: str, signal_date: str, universe: pd.DataFrame,
             fetch_history: Callable[[str, str, int], pd.DataFrame],
             names: Mapping[str, str] | None = None, universe_basis: Mapping | None = None,
             log: logging.Logger | None = None, clock: Callable[[], float] = time.monotonic) -> dict:
    """Detect base pocket pivots for ``universe`` (index = tickers, ``Amount`` = signal-day value).

    ``universe`` must already respect the batch's liquidity and market-cap floors. Tickers are
    fetched most liquid first, bounded by ``POCKET_PIVOT_MAX_TICKERS`` and
    ``POCKET_PIVOT_BUDGET_SECONDS``; the rest are counted, never guessed.
    """
    log = log or logger
    market = market.upper()
    max_tickers = int(_env_number("POCKET_PIVOT_MAX_TICKERS", DEFAULT_MAX_TICKERS))
    budget = _env_number("POCKET_PIVOT_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS)
    ordered = list(universe.sort_values("Amount", ascending=False, kind="mergesort").index)
    started = clock()
    statuses = {"PASS": 0, "FAIL": 0, "MISSING": 0}
    reasons: dict[str, int] = {}
    evaluated: dict[str, dict] = {}
    rs_raw: dict[str, float | None] = {}
    fetched = skipped_cap = skipped_budget = 0
    from cores.rs_rating import oneil_weighted_return
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
            log.debug("[POCKET-PIVOT] history unavailable for %s: %s", ticker, type(exc).__name__)
            history = pd.DataFrame()
        result = pp.detect(history, market=market, signal_date=_iso(signal_date))
        statuses[result["status"]] += 1
        for code in result["reason_codes"]:
            reasons[code] = reasons.get(code, 0) + 1
        if result["status"] == "PASS":
            evaluated[ticker] = result
        if result["status"] != "MISSING":
            closes = pd.to_numeric(history["Close"], errors="coerce")
            rs_raw[ticker] = oneil_weighted_return(closes[closes > 0])
    rs = _rs_percentiles(rs_raw)   # RS percentile across every evaluated (non-MISSING) ticker
    rows = []
    for ticker, result in evaluated.items():
        m = result["metrics"]
        row = {"ticker": str(ticker), "name": (names or {}).get(ticker), "rs": rs.get(ticker),
               "status": result["status"], "diag_codes": result["diag_codes"],
               **{field: m.get(field) for field in _FEATURE_FIELDS}}
        rows.append(row)
    ranked = pp.rank_candidates(rows)
    block = {
        "mode": "observe",
        "trigger_type": pp.TRIGGER_TYPE,
        "definition_version": pp.DEFINITION_VERSION,
        "market": market,
        "params": {"base_min": pp.params_for(market).base_min, "pp5_max": pp.params_for(market).pp5_max,
                   "optimized": pp.params_for(market).optimized},
        "kr_not_optimized": market == "KR" and pp.KR_NOT_OPTIMIZED,
        "live_allowed": False,
        "live_block_reason": pp.live_allowed(market)[1],
        "batch_trade_date": batch_trade_date,
        "signal_date": signal_date,
        "universe_basis": dict(universe_basis or {}),
        "rs_basis": "percentile_within_evaluated_universe",
        "earnings_within_5d": None,
        "missing": ["earnings_within_5d"],
        "coverage": {"universe": len(ordered), "fetched": fetched,
                     "skipped_max_tickers": skipped_cap, "skipped_budget": skipped_budget,
                     "max_tickers": max_tickers, "budget_seconds": budget,
                     "elapsed_seconds": round(clock() - started, 3)},
        "status_counts": statuses,
        "reason_counts": dict(sorted(reasons.items())),
        "candidates": ranked,
        "top_tickers": [row["ticker"] for row in ranked if row["top"]],
    }
    log.info("[POCKET-PIVOT] market=%s signal_date=%s universe=%d fetched=%d pass=%d top=%s",
             market, signal_date, len(ordered), fetched, statuses["PASS"], block["top_tickers"])
    return block


def ledger_rows(block: Mapping, universe: pd.DataFrame | None = None) -> list[dict]:
    """Candidate-ledger rows (mode ``pocket_pivot_observe``); never marked selected."""
    rows = []
    candidates = block.get("candidates") or []
    for item in candidates:
        ticker = item["ticker"]
        amount = volume = change = None
        if universe is not None and ticker in universe.index:
            record = universe.loc[ticker]
            amount, volume = _number(record.get("Amount")), _number(record.get("Volume"))
            change = _number(record.get("change_rate"))
        features = {**{k: v for k, v in item.items() if k not in {"name"}},
                    "batch_trade_date": block.get("batch_trade_date"),
                    "definition_version": block.get("definition_version"),
                    "kr_not_optimized": block.get("kr_not_optimized"),
                    "earnings_within_5d": None, "missing": list(block.get("missing") or [])}
        rows.append({"trigger_type": pp.TRIGGER_TYPE, "ticker": ticker, "name": item.get("name"),
                     "rank_in_trigger": item.get("rank"), "candidates_in_trigger": len(candidates),
                     "composite_score": _number(item.get("rank_score")), "rs_score": _number(item.get("rs")),
                     "score_column": "pocket_pivot_rank_score", "close": _number(item.get("close")),
                     "volume": volume, "amount": amount, "change_rate": change, "selected": 0,
                     "features_json": json.dumps(features, ensure_ascii=False, sort_keys=True, default=str)})
    return rows


def run_observe(*, market: str, batch_trade_date: str, signal_date: str, universe: pd.DataFrame,
                fetch_history: Callable[[str, str, int], pd.DataFrame],
                names: Mapping[str, str] | None = None, universe_basis: Mapping | None = None,
                market_regime: str | None = None, log: logging.Logger | None = None) -> dict | None:
    """Detection + ledger write for a batch already in observe mode. Returns the metadata block,
    or None on any failure. Never raises."""
    log = log or logger
    try:
        block = evaluate(market=market, batch_trade_date=batch_trade_date, signal_date=signal_date,
                         universe=universe, fetch_history=fetch_history, names=names,
                         universe_basis=universe_basis, log=log)
    except Exception as exc:  # noqa: BLE001 - observation must never change the batch
        log.warning("[POCKET-PIVOT] observe unavailable: %s", type(exc).__name__)
        return None
    try:
        rows = ledger_rows(block, universe)
        if rows:
            from observability import candidate_ledger
            candidate_ledger.record(rows, market=market, trade_date=signal_date, mode=LEDGER_MODE,
                                    market_regime=market_regime, log=log)
    except Exception as exc:  # noqa: BLE001
        log.warning("[POCKET-PIVOT] ledger unavailable: %s", type(exc).__name__)
    return block


def observe_universe(snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, *, complete: bool,
                     min_amount: float) -> pd.DataFrame:
    """Signal-day rows above the liquidity floor.

    complete: the batch's own session is the signal day; keep up days (Close > previous Close,
    gate PP1) to bound history fetches and attach ``change_rate`` (percent).
    not complete: the previous completed session is the signal day; its up/down status needs
    history, so no prefilter is applied here.
    """
    if complete:
        common = snapshot.index.intersection(prev_snapshot.index)
        current = snapshot.loc[common].copy()
        prev_close = pd.to_numeric(prev_snapshot.loc[common, "Close"], errors="coerce")
        close = pd.to_numeric(current["Close"], errors="coerce")
        current["change_rate"] = (close / prev_close - 1) * 100
        current = current[close > prev_close]
    else:
        current = prev_snapshot.copy()
    amount = pd.to_numeric(current["Amount"], errors="coerce")
    current = current.assign(Amount=amount)
    return current[current["Amount"] >= min_amount]
