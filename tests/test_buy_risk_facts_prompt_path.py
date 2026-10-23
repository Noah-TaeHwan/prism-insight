"""BUY risk facts reach the real KR/US BUY user prompt only while PRISM_BUY_RISK_FACTS is on.

Off (unset or false, with or without decision-input capture), the prompt is byte-identical to the
baseline; with capture on, the ledger copy is still built. On, exactly the block is inserted after the
decision facts, with the holdings and trading-history rows of the agent's own database. No network,
broker or MCP.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_agent_virtual_initialization import SCRIPT as INITIALIZATION_SCRIPT

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = INITIALIZATION_SCRIPT.split("async def main():", 1)[0] + r'''
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from prism_core import buy_risk_facts

language = sys.argv[3]
if market == "KR":
    import cores.corporate_status  # KR TIER0 event check (network) is outside this test
    cores.corporate_status.check_event_exit = lambda *a, **k: (False, "")
    import stock_tracking_agent as buy_module
else:
    buy_module = module
holdings_table = "stock_holdings" if market == "KR" else "us_stock_holdings"
history_table = "trading_history" if market == "KR" else "us_trading_history"
ticker = "005930" if market == "KR" else "SYNT"
prompts = []

async def capture(**request):
    prompts.append(request["user_prompt"])
    return SimpleNamespace(text=json.dumps({"decision": "No Entry", "buy_score": 0, "rationale": "synthetic"}),
                           latency_s=0, mcp_calls=[])

days, day = [], date.today()
while len(days) < 30:
    if day.weekday() < 5:
        days.insert(0, day)
    day -= timedelta(days=1)
FACTS = "- Market Pulse: UPTREND | 분산일(distribution days, 최근 25세션): 7 (synthetic)"

def trend_facts(t):
    buy_risk_facts.remember_trend_inputs(agent, t, price=100.0, volume={"signal1": "met"},
                                         high52={"near_high": False}, dates=days)
    return FACTS

def env(flag, capture_on):
    os.environ.pop("PRISM_BUY_RISK_FACTS", None)
    if flag is not None:
        os.environ["PRISM_BUY_RISK_FACTS"] = flag
    os.environ["DECISION_INPUT_SHADOW_ENABLED"] = "true" if capture_on else "false"

async def main():
    global agent
    agent = cls(**kwargs)
    assert await agent.initialize(language=language)
    agent._get_trend_facts = trend_facts
    agent._pipeline_market_regime = "moderate_bull"
    buy_module.generate_codex_fast_async = capture
    scope = agent._account_scope()[0]
    agent.cursor.execute(
        "INSERT INTO " + holdings_table + " (account_key, ticker, company_name, buy_price, buy_date, current_price, "
        "scenario, target_price, stop_loss) VALUES (?,?,?,?,?,?,?,?,?)",
        (scope, "HELD", "Held", 10, "2026-10-01 09:35:00", 11, json.dumps({"sector": "IT"}), 12, 9))
    for buy, sell in ((days[-5], days[-3]), (days[-9], days[-7])):
        agent.cursor.execute(
            "INSERT INTO " + history_table + " (account_key, ticker, company_name, buy_price, buy_date, sell_price, "
            "sell_date, profit_rate, holding_days, exit_kind) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (scope, ticker, "Synthetic", 100, buy.isoformat() + " 09:40:00", 94, sell.isoformat() + " 10:00:00",
             -6.0, 2, "stop"))
    agent.conn.commit()

    seen = {}
    for name, flag, capture_on in (("unset", None, False), ("false", "false", False),
                                   ("capture", None, True), ("on", "true", True)):
        env(flag, capture_on)
        agent._buy_risk_facts = {}
        await agent._extract_trading_scenario("# synthetic report", ticker=ticker, sector="IT")
        seen[name] = (prompts[-1], dict(agent._buy_risk_facts))
    baseline = seen["unset"][0]
    assert seen["unset"][1] == {} and seen["false"] == (baseline, {})
    ledger_only = seen["capture"][1][ticker]
    assert seen["capture"][0] == baseline and ledger_only["prompt_included"] is False

    prompt, cached = seen["on"]
    facts = cached[ticker]
    block = buy_risk_facts.prompt_block(facts, market=market, language=language)
    assert block.startswith("\n### ") and prompt.count(block) == 1
    assert prompt.replace(block, "", 1) == baseline
    assert facts["stop_rr"]["effective_regime"] == "sideways" and facts["stop_rr"]["max_stop_price"] == 94.0
    assert facts["stopouts"]["stop_count"] == 2 and facts["stopouts"]["repeat_stop_condition"] is True
    assert facts["sector"]["same_sector_count"] == 1 and facts["sector"]["cap_allows_new_entry"] is True
    assert facts["momentum"]["signals"]["volume_200pct"] == "MET"
    assert {k: v for k, v in ledger_only.items() if k != "prompt_included"} == \
        {k: v for k, v in facts.items() if k != "prompt_included"}
    agent.conn.close()
    print(json.dumps({"ok": True}))

asyncio.run(main())
'''


@pytest.mark.parametrize("kind, language", [("KR", "ko"), ("KR", "en"), ("US", "ko"), ("US", "en")])
def test_risk_facts_prompt_is_flag_only_and_ledger_matches(tmp_path, kind, language):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    for key in ("PRISM_BUY_RISK_FACTS", "MICRO_SPLIT_LIVE_ENABLED", "MICRO_SPLIT_LIVE_MARKETS"):
        env.pop(key, None)
    market = "US" if kind == "US" else "KR"
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               PRISM_BUY_CODEX_MODEL="gpt-6-astra", PRISM_BUY_CODEX_EFFORT="low", PRISM_BUY_CODEX_TIMEOUT="90",
               **{f"PRISM_{market}_CODEX_FAST_TRADING": "1"})
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), kind, language],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180, check=False)
    assert result.returncode == 0, result.stderr[-8000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": True}
