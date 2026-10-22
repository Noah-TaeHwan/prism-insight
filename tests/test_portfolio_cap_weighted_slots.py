"""The AI's max_portfolio_size is compared with slot-weighted usage, not row count (user decision 2026-10-19).

Six 35% micro-split holdings use about 2.1 slots; an AI cap of 6 must not block a new buy. Legacy rows
count as 1 slot. The hard row cap (MAX_SLOTS = 10 rows) stays a row-count check, KR and US.
"""
import json
from pathlib import Path

import pytest

from prism_core import micro_split_live as live

ROOT = Path(__file__).resolve().parents[1]


def _micro(allocation):
    return json.dumps({"micro_split": {"contract": "micro-split-live-v1", "allocation": str(allocation)}})


def test_six_partial_rows_use_2_1_slots_and_do_not_reach_a_cap_of_6():
    rows = [_micro(0.35)] * 6
    assert live.used_slots(rows) == pytest.approx(2.1)
    assert live.ai_cap_reached(rows, 6) is False


def test_weighted_usage_at_or_above_the_cap_blocks():
    assert live.ai_cap_reached([_micro(0.5)] * 12, 6) is True       # 6.0 >= 6
    assert live.ai_cap_reached([_micro(0.8)] * 5 + ["{}"] * 2, 6) is True  # 4.0 + 2 legacy = 6


def test_legacy_rows_count_as_one_slot():
    legacy = [json.dumps({"sector": "IT"}), "{}", None, "not-json"]
    assert live.used_slots(legacy) == 4
    assert live.ai_cap_reached(legacy, 4) is True and live.ai_cap_reached(legacy, 5) is False


@pytest.mark.parametrize("language, needle", [("ko", "보유 6종목 / 실제 사용 비중 2.1슬롯 (최대 10)"),
                                              ("en", "Holdings 6 / slot-weighted usage 2.1 slots (max 10)")])
def test_usage_line_shows_rows_and_weighted_slots(language, needle):
    assert live.usage_line(6, [_micro(0.35)] * 6, 10, language) == needle


@pytest.mark.parametrize("path, hard_checks", [
    ("stock_tracking_agent.py", ("if current_slots >= self.max_slots:",)),
    ("prism-us/us_stock_tracking_agent.py", ("if current_slots >= self.max_slots:",
                                             "if current_slots >= self.max_slots or weighted_slots >= scenario_slot_limit:",
                                             "if self._weighted_slots_if_capped(current_slots, slot_limit) >= slot_limit:")),
])
def test_call_sites_keep_the_hard_row_cap_and_use_weighted_ai_cap(path, hard_checks):
    src = (ROOT / path).read_text(encoding="utf-8")
    for check in hard_checks:
        assert check in src, check
    # The AI cap is never compared with the row count any more.
    assert "if current_slots >= max_portfolio_size:" not in src
    assert "if current_slots >= slot_limit:" not in src
    # 2026-10-23: the US re-entry path (enter_reentry_candidate) follows too, after #1052 merged.
    assert "if current_slots >= scenario_slot_limit:" not in src
    if path.startswith("prism-us"):
        assert ("if (current_slots >= self.max_slots\n"
                "                    or self._weighted_slots_if_capped(current_slots, scenario_slot_limit)"
                " >= scenario_slot_limit):") in src
    assert "usage_line(current_slots" in src


def test_prompts_no_longer_cite_a_report_risk_level():
    from cores.agents import trading_agents as kr
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("us_trading_agents_cap_20261019",
                                                  ROOT / "prism-us/cores/agents/trading_agents.py")
    us = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = us
    spec.loader.exec_module(us)
    for make in (kr.create_trading_scenario_agent, us.create_us_trading_scenario_agent):
        for language in ("ko", "en"):
            text = " ".join(make(language=language).instruction.split())
            assert "시장 리스크 레벨" not in text and "market risk level" not in text
            assert "6~10" in text and "Market Pulse" in text
            assert ("실제 사용 비중" in text) if language == "ko" else ("slot-weighted usage" in text)


class _CapAgent:
    """Minimal stand-in for the KR/US trackers' weighted-usage helper."""

    def __init__(self, scenarios=None, broken=False):
        self.scenarios, self.broken, self.reads = scenarios or [], broken, 0

    def _holding_scenarios(self):
        self.reads += 1
        if self.broken:
            raise RuntimeError("no such table")
        return self.scenarios


@pytest.mark.parametrize("path, cls", [("stock_tracking_agent.py", "StockTrackingAgent"),
                                       ("prism-us/us_stock_tracking_agent.py", "USStockTrackingAgent")])
def test_weighted_usage_is_read_only_at_the_cap_and_falls_back_to_rows(path, cls):
    import ast
    import logging
    src = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == cls
                for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "_weighted_slots_if_capped")
    ns = {"logger": logging.getLogger("cap-test")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    fn = ns["_weighted_slots_if_capped"]
    below = _CapAgent([_micro(0.35)] * 5)
    assert fn(below, 5, 6) == 5.0 and below.reads == 0          # weight <= rows: no read below the cap
    at = _CapAgent([_micro(0.35)] * 6)
    assert round(fn(at, 6, 6), 2) == 2.1 and at.reads == 1      # six 35% rows do not use up a cap of 6
    assert fn(_CapAgent(broken=True), 6, 6) == 6.0              # failed read keeps the stricter row count
