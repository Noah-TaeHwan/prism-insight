"""KIS overseas condition search as the US current-day market screen (#822).

One condition-search call (HHDFS76410000) returns today's quote, turnover and
market cap for up to 100 stocks. Measured on the production key (2026-09-28):
continuation requests (``tr_cont=N`` with the same or any KEYB) return the same
first page, which is always sorted by market cap, descending. We therefore walk
the market-cap range downward: after each full page the upper bound becomes the
smallest cap on that page (+1 thousand so ties are re-read and de-duplicated).

Units (KIS): CO_ST/EN_VALX and ``valx`` are thousand USD; ``avol`` is USD.
The rows carry no session date, so callers must not use them as a dated bar.
This module places no orders and reads no account data.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable

import pandas as pd

logger = logging.getLogger(__name__)

SEARCH_URL = "/uapi/overseas-price/v1/quotations/inquire-search"
SEARCH_TR_ID = "HHDFS76410000"
EXCHANGES = ("NAS", "NYS", "AMS")
PAGE_ROWS = 100
_VALX_MAX = "99999999999"
_UNUSED_CONDITIONS = (
    "PRICECUR", "RATE", "SHAR", "VOLUME", "AMT", "EPS", "PER",
)
COLUMNS = ("Open", "High", "Low", "Close", "Volume", "Amount", "MarketCap",
           "ChangeRate", "KisExchange", "KisTradable")


class KisScreenError(RuntimeError):
    """The screen could not be collected completely; callers fall back."""


def _params(excd: str, low_k: int, high_k: str) -> dict:
    params = {"AUTH": "", "EXCD": excd, "KEYB": "",
              "CO_YN_VALX": "1", "CO_ST_VALX": str(low_k), "CO_EN_VALX": high_k}
    for name in _UNUSED_CONDITIONS:
        params.update({f"CO_YN_{name}": "", f"CO_ST_{name}": "", f"CO_EN_{name}": ""})
    return params


def _number(value) -> float:
    try:
        number = float(str(value).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def normalize_symbol(symbol: str) -> str:
    """KIS class-share symbols to the directory/yfinance form (BRK/B -> BRK-B)."""
    return str(symbol or "").strip().upper().replace("/", "-").replace(".", "-")


def fetch_market_screen(request: Callable, min_cap_usd: float, *,
                        max_calls: int = 60, budget_seconds: float = 120.0,
                        pause_seconds: float = 0.06) -> tuple[pd.DataFrame, dict]:
    """Return every NAS/NYS/AMS stock with market cap >= ``min_cap_usd``.

    ``request(url, tr_id, params)`` must return a KIS ``APIResp``. Raises
    ``KisScreenError`` on any API error, stall, call cap or time budget so the
    caller can fall back to its previous collection path instead of screening
    a silently truncated market.
    """
    if not math.isfinite(min_cap_usd) or min_cap_usd <= 0:
        raise ValueError("min_cap_usd must be positive")
    low_k = int(math.ceil(min_cap_usd / 1000))
    started = time.monotonic()
    calls = 0
    rows: dict[str, dict] = {}
    per_exchange = {}
    for excd in EXCHANGES:
        high_k = _VALX_MAX
        expected = None
        seen: set[str] = set()
        while True:
            if calls >= max_calls:
                raise KisScreenError(f"call cap {max_calls} reached at {excd}")
            if time.monotonic() - started >= budget_seconds:
                raise KisScreenError(f"time budget {budget_seconds}s reached at {excd}")
            if calls:
                time.sleep(pause_seconds)
            response = request(SEARCH_URL, SEARCH_TR_ID, _params(excd, low_k, high_k))
            calls += 1
            if not response.isOK():
                raise KisScreenError(
                    f"{excd} condition search failed: {response.getErrorCode()}")
            body = response.getBody()
            header = getattr(body, "output1", None) or {}
            page = getattr(body, "output2", None) or []
            band_total = int(_number(header.get("trec")) or 0)
            if expected is None:
                expected = band_total
            new = 0
            smallest = math.inf
            for item in page:
                symbol = str(item.get("symb") or "").strip()
                cap_k = _number(item.get("valx"))
                if not symbol or not math.isfinite(cap_k):
                    continue
                smallest = min(smallest, cap_k)
                if symbol in seen:
                    continue
                seen.add(symbol)
                new += 1
                rows[normalize_symbol(symbol)] = {
                    "Open": _number(item.get("popen")),
                    "High": _number(item.get("phigh")),
                    "Low": _number(item.get("plow")),
                    "Close": _number(item.get("last")),
                    "Volume": _number(item.get("tvol")),
                    "Amount": _number(item.get("avol")),
                    "MarketCap": cap_k * 1000,
                    "ChangeRate": _number(item.get("rate")),
                    "KisExchange": excd,
                    "KisTradable": str(item.get("e_ordyn") or "").strip() == "○",
                }
            if band_total <= len(page) or len(page) < PAGE_ROWS:
                break
            if not new or not math.isfinite(smallest):
                raise KisScreenError(f"{excd} walk stalled at cap <= {high_k}k")
            high_k = str(int(smallest) + 1)
        per_exchange[excd] = {"expected": expected, "collected": len(seen)}
    frame = pd.DataFrame.from_dict(rows, orient="index", columns=list(COLUMNS))
    diagnostic = {
        "source": "kis_condition_search",
        "tr_id": SEARCH_TR_ID,
        "min_market_cap_usd": float(min_cap_usd),
        "calls": calls,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "exchanges": per_exchange,
        "row_count": len(frame),
        "status": ("COMPLETE" if all(v["collected"] >= (v["expected"] or 0)
                                     for v in per_exchange.values()) else "PARTIAL"),
    }
    logger.info("KIS US market screen: %s", diagnostic)
    return frame, diagnostic


# Overseas daily prices (HHDFS76240000). Unlike the condition search, each row
# carries its session date (``xymd``), so an exact-date row is a dated bar.
DAILY_URL = "/uapi/overseas-price/v1/quotations/dailyprice"
DAILY_TR_ID = "HHDFS76240000"
_BAR_FIELDS = ("Open", "High", "Low", "Close", "Volume")


def fetch_exact_daily_bars(request: Callable, exchanges: dict, date: str, *,
                           budget_seconds: float = 420.0, pause_seconds: float = 0.06,
                           failure_stop: int = 5) -> tuple[pd.DataFrame, dict]:
    """Return the ``date`` (YYYYMMDD) OHLCV bar for each ``{symbol: excd}``.

    Only a single row dated exactly ``date`` with finite positive prices is
    accepted (split-adjusted, ``MODP=1``). ~0.19 s per symbol on db-server
    (2026-10-22: 1,038 symbols in 199 s). Symbols are requested in the given
    order, so a budget or failure stop drops only the tail. Never raises on an
    API error: the caller keeps whatever it had for the unfilled symbols.
    """
    started = time.monotonic()
    rows: dict[str, dict] = {}
    reasons: dict[str, int] = {}
    calls = 0
    failures = 0
    stop_reason = None
    symbols = list(exchanges)

    def miss(reason, count=1):
        reasons[reason] = reasons.get(reason, 0) + count

    for position, symbol in enumerate(symbols):
        if failures >= failure_stop:
            stop_reason = "failure_stop"
        elif time.monotonic() - started >= budget_seconds:
            stop_reason = "time_budget"
        if stop_reason:
            miss(stop_reason, len(symbols) - position)
            break
        if "-" in symbol:  # class shares: the KIS spelling (BRK/B) is not recoverable here
            miss("unsupported_symbol")
            continue
        if calls:
            time.sleep(pause_seconds)
        calls += 1
        params = {"AUTH": "", "EXCD": exchanges[symbol], "SYMB": symbol,
                  "GUBN": "0", "BYMD": date, "MODP": "1"}
        try:
            response = request(DAILY_URL, DAILY_TR_ID, params)
            ok = response.isOK()
        except Exception:  # noqa: BLE001 - one failed quote must not stop the batch
            ok = False
        if not ok:
            failures += 1
            miss("request_failed")
            continue
        failures = 0
        page = getattr(response.getBody(), "output2", None) or []
        matching = [item for item in page if str(item.get("xymd") or "").strip() == date]
        if len(matching) != 1:
            miss("missing_exact_date" if not matching else "duplicate_exact_date")
            continue
        item = matching[0]
        bar = {"Open": _number(item.get("open")), "High": _number(item.get("high")),
               "Low": _number(item.get("low")), "Close": _number(item.get("clos")),
               "Volume": _number(item.get("tvol"))}
        prices = [bar[k] for k in ("Open", "High", "Low", "Close")]
        if (not all(math.isfinite(v) and v > 0 for v in prices)
                or not math.isfinite(bar["Volume"]) or bar["Volume"] < 0):
            miss("invalid_ohlcv")
            continue
        bar["Amount"] = bar["Close"] * bar["Volume"]
        rows[symbol] = bar
    frame = pd.DataFrame.from_dict(rows, orient="index", columns=[*_BAR_FIELDS, "Amount"])
    frame.index.name = "Ticker"
    diagnostic = {"source": "kis_dailyprice", "tr_id": DAILY_TR_ID, "date": date,
                  "requested_count": len(symbols), "filled_count": len(frame),
                  "calls": calls, "elapsed_seconds": round(time.monotonic() - started, 3),
                  "reason_counts": reasons, "stop_reason": stop_reason}
    logger.info("KIS US exact daily bars: %s", diagnostic)
    return frame, diagnostic
