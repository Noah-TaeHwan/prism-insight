"""Prompt assembly/contract checks, not model behavior or profitability proof."""
from copy import deepcopy
import json

import pytest

from live.scenario_llm import FLAT_ENTRY_PROMPT
from live.scenario_model_input import restore_model_input
from tests.test_scenario_calculated_review import context, run, wire


MARKER = "Before opportunity framing, compare actual 30m/1h highs/lows"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("pattern", ["contraction_stalled", "fresh_breakout_narrowing", "strong_expansion", "tiny_widening_no_progress"])
def test_mirrored_structural_inputs_keep_both_phases_and_host_contract(side, pattern):
    ctx = context()
    # Supplied paired facts are retained, not a new classifier or forced decision.
    ctx["test_structural_facts"] = dict(side=side, pattern=pattern,
        price_progress=1 if side == "LONG" else -1,
        ma_gap_narrowing=pattern in {"contraction_stalled", "fresh_breakout_narrowing"})
    before = deepcopy(ctx)
    result, _, calls = run(ctx, [wire(ctx, side)])
    assert result["selected"]["raw_proposal"]["side"] == side
    assert ctx == before
    assert len(calls) == 2
    for call in calls:
        policy = " ".join(call["system_prompt"].split())
        assert FLAT_ENTRY_PROMPT in call["system_prompt"]
        assert policy.count(MARKER) == 1
        for clause in (
            "old MA order and ABOVE/BELOW persist",
            "If no structural entry edge exists, prefer WAIT; small risk cannot repair it",
            "nearer first TP before the actual obstacle",
            "In calculated review, use host-calculated candidate economics; do not discard",
            "a structurally justified candidate solely on an estimated-cost impression before that calculation",
            "The host still enforces economic and safety limits",
            "SL at structural invalidation with noise room",
            "Contraction alone is not a veto",
            "while the old MA gap still narrows, before a lagging crossover",
            "Do not require another MA10 cross, a candle close or unanimous 30m/1h alignment",
            "forming progress is provisional",
            "a tiny WIDENING sign alone is not strong expansion",
            "For LONG inspect higher lows",
            "for SHORT mirror with lower highs",
            "not a mandatory short target for every flat entry",
            "preserve later targets and a strong runner",
        ):
            assert clause in policy
        assert policy.index(MARKER) < policy.index("Compare a capped marketable LIMIT now")
        payload = restore_model_input(json.loads(call["user_prompt"]))
        assert payload["contract_context"]["test_structural_facts"] == ctx["test_structural_facts"]
        assert len(call["user_prompt"].encode()) <= 100000


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("phase", ["pending", "holding"])
def test_active_and_pending_prompts_do_not_receive_flat_entry_change(side, phase):
    ctx = context()
    candidate = wire(ctx, side)
    ctx.update(scenario_id="active", revision=1, side=side,
               positions=[dict(price=83410, quantity=.01)] if phase == "holding" else [],
               pending_entries=[] if phase == "holding" else [dict(id="pending", price=83410, quantity=.01)],
               previous_hard_stop=candidate["hard_stop"])
    candidate = wire(ctx, side)
    candidate.update(action="ADJUST", entries=[])
    before = deepcopy(ctx)
    _, _, calls = run(ctx, [candidate])
    assert len(calls) == 2
    assert ctx == before
    for call in calls:
        assert MARKER not in " ".join(call["system_prompt"].split())
        assert FLAT_ENTRY_PROMPT not in call["system_prompt"]


def test_host_allocation_both_phases_include_priority_without_new_fields():
    from tests.test_scenario_allocation_review import taped

    args, calls = taped("allocation-calculated")
    assert args[3]["outcome"]["status"] == "COMPLETE"
    assert len(calls) == 2
    for call in calls:
        assert MARKER in " ".join(call["system_prompt"].split())
    assert "Do not emit entry quantity" in calls[0]["system_prompt"]
    assert "Return exactly selection_token and rationale" in calls[1]["system_prompt"]
    assert set(calls[1]["response_schema"]["properties"]) == {"selection_token", "rationale"}
