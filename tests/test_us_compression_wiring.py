"""Tests for the US-market compression pass wired into compress_trading_memory.

Regression guard for #321: the weekly compression job previously only ran the KR
``StockTrackingAgent`` pass, so US intuitions were never derived from compression.
``run_us_compression`` now runs ``USCompressionManager`` over the same shared DB.

These tests build a minimal self-contained SQLite schema (only the columns the
compression code touches) so they run in the root pytest session WITHOUT importing
any prism-us package modules — avoiding the documented KR/US ``cores`` shadowing.
"""

import asyncio
import sqlite3
from unittest.mock import AsyncMock
from datetime import datetime, timedelta

import pytest

import compress_trading_memory as ctm
from tracking.db_schema import TABLE_TRADING_INTUITIONS


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """These tests cover the deterministic pass; the LLM memory steps have their own tests."""
    from tracking.compression import CompressionManager
    for name in ("refresh_intuitions", "review_memory_applicability", "tag_buy_situations"):
        monkeypatch.setattr(CompressionManager, name, AsyncMock(return_value={}))


def _create_minimal_schema(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE trading_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            company_name TEXT,
            profit_rate REAL,
            holding_days INTEGER,
            one_line_summary TEXT,
            lessons TEXT,
            pattern_tags TEXT,
            sell_price REAL,
            buy_scenario TEXT,
            compressed_summary TEXT,
            trade_date TEXT,
            compression_layer INTEGER DEFAULT 1,
            market TEXT DEFAULT 'KR'
        )
        """
    )
    # Production schema, so a column-name drift in the US SQL fails here (no such column).
    cur.executescript(TABLE_TRADING_INTUITIONS)
    cur.execute("ALTER TABLE trading_intuitions ADD COLUMN market TEXT DEFAULT 'KR'")
    cur.execute(
        """
        CREATE TABLE trading_principles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            confidence REAL,
            created_at TEXT,
            last_validated_at TEXT,
            is_active INTEGER DEFAULT 1,
            market TEXT DEFAULT 'KR'
        )
        """
    )
    conn.commit()
    conn.close()


def _insert_layer2_entry(db_path, ticker, profit_rate, pattern_tags, market, days_ago=40):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    trade_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    cur.execute(
        """
        INSERT INTO trading_journal
            (ticker, company_name, profit_rate, holding_days, one_line_summary,
             lessons, pattern_tags, sell_price, buy_scenario, trade_date,
             compression_layer, market)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?)
        """,
        (
            ticker, f"{ticker} Inc", profit_rate, 10, f"{ticker} summary",
            "[]", pattern_tags, 100.0, "{}", trade_date, market,
        ),
    )
    conn.commit()
    conn.close()


def test_us_compression_derives_us_intuitions(tmp_path):
    """3 US layer-2 entries sharing a pattern → one market='US' intuition is created."""
    db = str(tmp_path / "shared.sqlite")
    _create_minimal_schema(db)

    _insert_layer2_entry(db, "AAPL", 5.0, '["breakout"]', "US")
    _insert_layer2_entry(db, "MSFT", -2.0, '["breakout"]', "US")
    _insert_layer2_entry(db, "NVDA", 3.0, '["breakout"]', "US")

    result = asyncio.run(
        ctm.run_us_compression(db_path=db, min_entries=3, skip_cleanup=True)
    )

    assert result["status"] == "success"
    assert result["results"]["intuitions_generated"] >= 1

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT category, market FROM trading_intuitions WHERE market = 'US'")
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "breakout"
    assert rows[0][1] == "US"


def test_us_intuition_stats_cover_all_batches(tmp_path):
    """A second batch must not overwrite the win rate with that batch alone (prod: 'avg 0.5%' with 0% wins)."""
    db = str(tmp_path / "shared.sqlite")
    _create_minimal_schema(db)
    for ticker, profit in (("AAPL", 4.0), ("MSFT", 2.0), ("NVDA", 1.0)):
        _insert_layer2_entry(db, ticker, profit, '["breakout"]', "US")
    asyncio.run(ctm.run_us_compression(db_path=db, min_entries=3, skip_cleanup=True))
    for ticker, profit in (("AMD", -3.0), ("TSLA", -2.0), ("META", -1.0)):
        _insert_layer2_entry(db, ticker, profit, '["breakout"]', "US")
    asyncio.run(ctm.run_us_compression(db_path=db, min_entries=3, skip_cleanup=True))

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT insight, success_rate, supporting_trades, source_journal_ids "
        "FROM trading_intuitions WHERE market = 'US' AND is_active = 1").fetchall()
    conn.close()
    assert len(rows) == 1
    insight, success_rate, supporting, source_ids = rows[0]
    assert insight == "Avg return 0.2% over 6 trades"
    assert success_rate == 0.5
    assert supporting == 6
    assert source_ids == "[1, 2, 3, 4, 5, 6]"


def test_us_compression_does_not_touch_kr_rows(tmp_path):
    """KR layer-2 rows must remain untouched (no compression, no KR intuition)."""
    db = str(tmp_path / "shared.sqlite")
    _create_minimal_schema(db)

    # KR rows with the same pattern — US pass must ignore them (market filter).
    _insert_layer2_entry(db, "005930", 4.0, '["breakout"]', "KR")
    _insert_layer2_entry(db, "000660", 6.0, '["breakout"]', "KR")
    _insert_layer2_entry(db, "035420", 2.0, '["breakout"]', "KR")

    result = asyncio.run(
        ctm.run_us_compression(db_path=db, min_entries=3, skip_cleanup=True)
    )

    assert result["status"] == "success"
    # No US intuitions generated from KR rows.
    assert result["results"]["intuitions_generated"] == 0

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM trading_intuitions")
    intuition_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM trading_journal WHERE compression_layer = 2 AND market = 'KR'")
    kr_layer2_remaining = cur.fetchone()[0]
    conn.close()

    assert intuition_count == 0
    assert kr_layer2_remaining == 3


def test_us_compression_dry_run_makes_no_changes(tmp_path):
    """Dry run returns a preview and creates no intuitions."""
    db = str(tmp_path / "shared.sqlite")
    _create_minimal_schema(db)

    _insert_layer2_entry(db, "AAPL", 5.0, '["breakout"]', "US")
    _insert_layer2_entry(db, "MSFT", -2.0, '["breakout"]', "US")
    _insert_layer2_entry(db, "NVDA", 3.0, '["breakout"]', "US")

    result = asyncio.run(
        ctm.run_us_compression(db_path=db, min_entries=3, dry_run=True)
    )

    assert result["status"] == "dry_run"

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM trading_intuitions")
    intuition_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM trading_journal WHERE compression_layer = 3")
    layer3_count = cur.fetchone()[0]
    conn.close()

    assert intuition_count == 0
    assert layer3_count == 0
