"""2026-10-19~21: every US re-entry order failed with `No module named 'cores.buy_gate'`.

`tools/run_reentry_v3_entry.py --market US` pins `cores` to prism-us/cores (#1040), but
`reentry_v3_live.apply_stop_cap` imported `prism_core.reentry_campaign`, which imported the shared
`cores.buy_gate` by package name. The in-process tests load the shared `cores`, so they never saw it.
This probe runs the real `enter_reentry_candidate` in a subprocess with the runner's own path set-up,
down to the fake broker call (no network, no orders, no DB file).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
import asyncio, json, os, sqlite3, sys
os.environ["PRISM_DISABLE_SIGNAL_PUBLISH"] = "1"
sys.path.insert(0, {tools!r})
import run_reentry_v3_entry as runner
runner._bootstrap_path({market!r})
if {market!r} == "US":
    import us_stock_tracking_agent as mod
    cls, table, source = mod.USStockTrackingAgent, "us_stock_holdings", "us_reentry_v3"
    extra = {{"account": {{"name": "main", "account_key": "acct-key"}}}}
else:
    import stock_tracking_enhanced_agent as mod
    cls, table, source = mod.EnhancedStockTrackingAgent, "stock_holdings", "kr_reentry_v3"
    extra = {{}}
import reentry_cooldown
from observability import decision_inputs
from prism_core import corporate_filings, micro_split_live, oneil_routing
from prism_core import reentry_v3_live as live

async def _no_event(*a, **k):
    return {{"status": "ok", "blocking": [], "listed": 0}}
async def _not_owned(*a, **k):
    return False
reentry_cooldown.reentry_block = lambda *a, **k: None
if {market!r} == "KR":
    from cores import regime_policy
    regime_policy.regime_min_score_floor_enabled = lambda: False   # env-gated network floor, off here
corporate_filings.reentry_blocking_events_async = _no_event
oneil_routing.route_owned_entry = _not_owned
decision_inputs.emit_decision_inputs = lambda agent, **kw: None
micro_split_live.live_enabled = lambda market: True
micro_split_live.plan_available = lambda agent, **kw: True
micro_split_live.relaxed_min_score = lambda agent, *, min_score, scenario, **kw: (min_score, scenario)
for name in ("observe_or_emit", "emit_trading_context", "emit_micro_split_shadow"):
    if hasattr(mod, name):
        setattr(mod, name, lambda *a, **k: None)

agent = cls.__new__(cls)
agent.max_slots, agent.db_path, agent.enable_journal, agent.trigger_info_map = 10, ":memory:", False, {{}}
agent.conn = sqlite3.connect(":memory:")
agent.conn.execute(f"CREATE TABLE {{table}} (id INTEGER PRIMARY KEY, ticker TEXT, account_key TEXT, buy_price REAL, scenario TEXT)")
agent.cursor, calls = agent.conn.cursor(), []

async def _sector_ok(sector, is_pyramiding_add=False):
    return True
async def _quote(ticker):
    return {{"price": 100.0}} if {market!r} == "KR" else 100.0
async def _broker(**kw):
    calls.append(kw)
    return 1
agent._check_sector_diversity = _sector_ok
agent._evaluate_production_buy_gate = lambda scenario, price, score_override=None, is_add=False: {{"allowed": True, "reason": "ok", "findings": []}}
agent._account_scope = lambda: ("acct-key", "main")
agent._set_active_account = lambda account: None
agent._buy_floor_regime = lambda: "sideways"
agent._stamp_scenario_market_regime = lambda s: dict(s, _deterministic_market_regime="sideways")
agent._get_trend_facts = lambda ticker: "T1_hit: false"
agent._regime_policy_mod = lambda: None
agent._get_fresh_buy_quote = _quote
agent._refresh_buy_quote = _quote
agent._enter_eligible_candidate = _broker

item = {{"event_id": "ev1", "watch_ref": "w1", "ticker": "TEST", "source": "STOP_EXIT", "trigger": "R2S",
        "trigger_date": "2026-10-07", "decision_price": 100.0, "live_rule": "L97",
        "decision_time": "2026-10-07T05:00:00+00:00", "level": {{"L": 98.0, "basis": "primary_support"}},
        "stop": 95.0, "target": 110.0, "stop_rule": "STRUCT", "target_rule": "nearest", "rr": 1.5,
        "attempts": {{"L97": {{"attempt": 1, "max": 3, "prior": []}}}}, "report_ref": None, "campaign": {{"windows": {{}}}}}}
record = {{"event_id": "ev1", "trigger_date": "2026-10-07", "status": "OK", "approved": True,
          "scenario": {{"decision": "진입", "buy_score": 7, "min_score": 5, "entry_price": 100, "target_price": 110,
                       "stop_loss": 93, "risk_reward_ratio": 1.1, "sector": "Tech", "rationale": "test",
                       "add_plan": {{"scenarios": []}}}}}}
scenario = live.build_scenario(item, record, {market!r})
if {market!r} == "US":
    scenario["decision"] = "entry"
out = asyncio.run(agent.enter_reentry_candidate(ticker="TEST", company_name="Test", current_price=100.0,
                                                scenario=scenario, sector="Tech",
                                                source_decision_id=scenario["_decision_id"], **extra))
print("RESULT " + json.dumps({{"out": out, "calls": len(calls), "source": calls[0]["source"] if calls else None,
                               "cores": sys.modules["cores"].__file__}}, default=str))
"""


@pytest.mark.parametrize("market", ["US", "KR"])
def test_runner_path_reaches_the_broker_step(market, tmp_path):
    code = PROBE.format(tools=str(ROOT / "tools"), market=market)
    done = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=180)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, done.stderr[-1500:]
    result = json.loads(lines[-1][len("RESULT "):])
    assert result["out"].get("bought") is True, (result, done.stderr[-1500:])
    assert result["calls"] == 1 and result["source"] == ("us_reentry_v3" if market == "US" else "kr_reentry_v3")
    if market == "US":
        assert "prism-us" in result["cores"]      # the runner's US `cores` pin is what production uses
