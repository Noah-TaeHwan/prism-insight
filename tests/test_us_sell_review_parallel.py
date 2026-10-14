"""US sell reviews run concurrently (bounded); results are applied in holding order."""
import ast
import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, Dict, List

from prism_core import runner_hold_live

ROOT = Path(__file__).resolve().parents[1]


def _update_holdings(concurrency):
    source = (ROOT / "prism-us/us_stock_tracking_agent.py").read_text("utf-8")
    node = next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "update_holdings")
    namespace = {"Dict": Dict, "List": List, "Any": Any, "asyncio": asyncio, "datetime": datetime,
                 "logger": logging.getLogger(__name__), "effects_for": lambda *_: None,
                 "require_execution_runtime": lambda _: None, "defer_exit_capture": lambda fn: fn,
                 "US_TRADING_ANALYSIS_CONCURRENCY": concurrency,
                 "oneil_holding_observation": lambda **_: None, "capture_oneil_holding": None}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "us", "exec"), namespace)
    return namespace["update_holdings"]


def test_sell_reviews_overlap_up_to_limit_and_save_in_order(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE us_stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "buy_price REAL, buy_date TEXT, current_price REAL, scenario TEXT, target_price REAL, "
                 "stop_loss REAL, last_updated TEXT, trigger_type TEXT, trigger_mode TEXT, sector TEXT, "
                 "account_key TEXT, account_name TEXT)")
    tickers = ["A", "B", "C", "D", "E"]
    conn.executemany("INSERT INTO us_stock_holdings (ticker, buy_price, account_key) VALUES (?, 10, 'k')",
                     [(t,) for t in tickers])
    monkeypatch.setattr(runner_hold_live, "review_holding", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(runner_hold_live, "guard_result", lambda s, m, st, sell, reason, **k: (sell, reason))

    running, peak, saved = 0, 0, []

    async def analyze(stock):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01 * (5 - tickers.index(stock["ticker"])))  # later rows finish first
        running -= 1
        return False, f"hold {stock['ticker']}"

    async def save(ticker, price, should_sell, reason, stock):
        saved.append((ticker, price, reason))

    async def price(ticker):
        return 11.0

    agent = SimpleNamespace(conn=conn, cursor=conn.cursor(), _account_scope=lambda: ("k",),
                            _get_live_regime_safe=lambda: None, _get_current_stock_price=price,
                            _analyze_sell_decision=analyze, _save_holding_decision=save)
    sold = asyncio.run(MethodType(_update_holdings(3), agent)(raise_on_error=True))

    assert sold == []
    assert peak == 3
    assert saved == [(t, 11.0, f"hold {t}") for t in tickers]
