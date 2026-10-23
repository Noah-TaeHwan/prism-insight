"""KR BUY growth facts reach the real KR BUY user prompt only while PRISM_BUY_GROWTH_FACTS is on.

Off (unset or false, with or without decision-input capture), the prompt is byte-identical to the baseline
and to a run without any growth facts in report_meta; with capture on, the ledger copy is still cached. On,
exactly the block is inserted after the decision and risk facts. No network, broker or MCP.
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
from types import SimpleNamespace
from prism_core import buy_growth_facts
from prism_core import kr_financial_summary as fs

language = sys.argv[3]
import cores.corporate_status  # KR TIER0 event check (network) is outside this test
cores.corporate_status.check_event_exit = lambda *a, **k: (False, "")
import stock_tracking_agent as buy_module
ticker = "005930"
prompts = []

async def capture(**request):
    prompts.append(request["user_prompt"])
    return SimpleNamespace(text=json.dumps({"decision": "No Entry", "buy_score": 0, "rationale": "synthetic"}),
                           latency_s=0, mcp_calls=[])

fixtures = source / "tests" / "fixtures" / "wisereport"
FACTS = buy_growth_facts.build(
    fs.parse_financial_summary((fixtures / "cF1001_Q_018880.html").read_text(encoding="utf-8")),
    fs.parse_financial_summary((fixtures / "cF1001_252990.html").read_text(encoding="utf-8")),
    asof="2026-01-01T00:00:00+00:00")

def env(flag, capture_on):
    os.environ.pop("PRISM_BUY_GROWTH_FACTS", None)
    if flag is not None:
        os.environ["PRISM_BUY_GROWTH_FACTS"] = flag
    os.environ["DECISION_INPUT_SHADOW_ENABLED"] = "true" if capture_on else "false"

async def main():
    agent = cls(**kwargs)
    assert await agent.initialize(language=language)
    agent._get_trend_facts = lambda t: "- synthetic trend facts"
    agent._pipeline_market_regime = "moderate_bull"
    buy_module.generate_codex_fast_async = capture

    seen = {}
    for name, flag, capture_on, meta in (("bare", None, False, {}), ("unset", None, False, {"growth_facts": FACTS}),
                                         ("false", "false", False, {"growth_facts": FACTS}),
                                         ("capture", None, True, {"growth_facts": FACTS}),
                                         ("on_no_meta", "true", True, {}),
                                         ("on", "true", False, {"growth_facts": FACTS})):
        env(flag, capture_on)
        agent._report_meta = {ticker: dict(meta)}
        agent._buy_growth_facts = {}
        await agent._extract_trading_scenario("# synthetic report", ticker=ticker, sector="IT")
        seen[name] = (prompts[-1], dict(agent._buy_growth_facts))
    baseline = seen["bare"][0]
    assert seen["bare"][1] == {} and seen["unset"] == (baseline, {}) and seen["false"] == (baseline, {})
    assert seen["capture"][0] == baseline and seen["capture"][1][ticker]["prompt_included"] is False
    assert seen["on_no_meta"][0] == baseline
    assert seen["on_no_meta"][1][ticker]["missing"] == {"report": "not_in_report_meta"}

    prompt, cached = seen["on"]
    block = buy_growth_facts.prompt_block(FACTS, language=language)
    assert block.startswith("\n### ") and prompt.count(block) == 1
    assert prompt.replace(block, "", 1) == baseline
    assert cached[ticker]["prompt_included"] is True and cached[ticker]["debt_ratio"]["value_pct"] == 157.17
    agent.conn.close()
    print(json.dumps({"ok": True}))

asyncio.run(main())
'''


@pytest.mark.parametrize("language", ["ko", "en"])
def test_growth_facts_prompt_is_flag_only(tmp_path, language):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    for key in ("PRISM_BUY_GROWTH_FACTS", "PRISM_BUY_RISK_FACTS", "MICRO_SPLIT_LIVE_ENABLED",
                "MICRO_SPLIT_LIVE_MARKETS"):
        env.pop(key, None)
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               MPLCONFIGDIR=str(tmp_path / "mpl"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               PRISM_BUY_CODEX_MODEL="gpt-6-astra", PRISM_BUY_CODEX_EFFORT="low", PRISM_BUY_CODEX_TIMEOUT="90",
               PRISM_KR_CODEX_FAST_TRADING="1")
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), "KR", language],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180, check=False)
    assert result.returncode == 0, result.stderr[-8000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": True}
