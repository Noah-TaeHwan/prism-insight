"""yfinance (unlicensed Yahoo scraping) must not spread: only listed files may import it directly.

New US price code goes through prism_core/us_market_data.py (provider adapters).
The allowlist may only shrink: when a file moves behind the adapter, delete it here.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The adapter itself (YFinanceUsSource, opt-in via PRISM_US_MARKET_DATA_SOURCES).
ADAPTER = "prism_core/us_market_data.py"

# Direct importers as of 2026-10-23. When a file moves behind the adapter, delete its line here.
ALLOWED = {
    "cores/regime_policy.py",
    "demo.py",
    "observability/decision_inputs.py",
    "prism_core/negative_equity_f2.py",
    "prism_core/oneil_runtime_inputs.py",
    "prism_core/us_peer_comparison.py",
    "prism-us/cores/data_prefetch.py",
    "prism-us/cores/us_analysis.py",
    "prism-us/cores/us_data_client.py",
    "prism-us/cores/us_stock_chart.py",
    "prism-us/cores/us_surge_detector.py",
    "prism-us/tracking/compression.py",
    "prism-us/trading/us_stock_trading.py",
    "prism-us/us_stock_tracking_agent.py",
    "prism-us/us_trigger_batch.py",
    "telegram_ai_bot.py",
    "tools/bench/verify_grounded_facts.py",
    "tools/build_oneil_auto_review.py",
    "tools/build_us_theme_map.py",
    "tools/collect_trend_replay_data.py",
    "tools/market_pulse_backtest.py",
    "tools/regime_backtest.py",
    "tools/replay_buy_decision_facts.py",
    "tools/replay_confirmed_resistance_target.py",
    "tools/replay_us_udvr_proxy.py",
    "tools/rs_rating_backtest.py",
    "tools/run_kr_legacy_adx_identity_v2.py",
    "tools/run_oneil_watchlist_shadow.py",
    "tools/run_reentry_v3_shadow.py",
}


def _is_test(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return any(p in {"tests", "test"} for p in parts) or path.name.startswith("test_") or path.name == "conftest.py"


def _imports_yfinance(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "yfinance" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "yfinance":
            return True
    return False


def direct_importers() -> set[str]:
    found = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if any(part.startswith(".") or part in {"node_modules", "venv"} for part in rel.parts) or _is_test(path):
            continue
        if _imports_yfinance(path):
            found.add(rel.as_posix())
    return found


def test_no_new_direct_yfinance_imports():
    new = direct_importers() - ALLOWED - {ADAPTER}
    assert not new, (f"New direct yfinance import(s): {sorted(new)}. Read US prices through "
                     f"prism_core/us_market_data.py instead of importing yfinance.")


def test_allowlist_only_names_existing_importers():
    # Keeps the list honest: a file that stopped importing yfinance must be removed from ALLOWED.
    stale = ALLOWED - direct_importers()
    assert not stale, f"Remove from ALLOWED (no longer import yfinance): {sorted(stale)}"
