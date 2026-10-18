"""2026-10-14/17: tools/run_reentry_v3_entry.py --market us failed with
`cannot import name 'create_us_trading_scenario_agent' from 'cores.agents.trading_agents'`:
us_stock_tracking_agent prepends the shared root before its first `from cores...`, so the KR
package got cached. The US runners now pin prism-us `cores`/`tracking`/`trading` first.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import sys
sys.path.insert(0, {tools!r})
import {runner} as runner
runner._bootstrap_path("US")
import us_stock_tracking_agent as agent
assert hasattr(agent, "create_us_trading_scenario_agent")
assert sys.modules["cores"].__file__.startswith({us!r}), sys.modules["cores"].__file__
print("OK")
"""


@pytest.mark.parametrize("runner", ["run_reentry_v3_entry", "run_micro_split_add"])
def test_us_runner_loads_us_tracking_agent(runner, tmp_path):
    code = PROBE.format(tools=str(ROOT / "tools"), runner=runner, us=str(ROOT / "prism-us"))
    done = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert done.stdout.strip().endswith("OK"), done.stderr[-600:]
