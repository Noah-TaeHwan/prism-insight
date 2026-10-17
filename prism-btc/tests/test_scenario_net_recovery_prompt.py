"""Recovery evidence informs model context but never grants model risk authority."""
import pytest

from tests.test_scenario_calculated_review import context, wire, run


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("holding", [False, True])
def test_rolling_net_rule_is_shared_across_candidate_and_selection(side, holding):
    ctx = context()
    candidate = wire(ctx, side)
    if holding:
        ctx.update(scenario_id="active", revision=1, side=side,
                   positions=[dict(price=83410, quantity=.01)],
                   previous_hard_stop=candidate["hard_stop"])
        candidate = wire(ctx, side)
        candidate.update(action="ADJUST", entries=[])
    _, _, calls = run(ctx, [candidate])
    assert len(calls) == 2
    for call in calls:
        text = " ".join(call["system_prompt"].split())
        for clause in (
            "Follow the current recovery.promotion_rule_version and offered stage",
            "an active rule-2 scenario retains that rule until exact settlement",
            "last six prospective, exact filled settlements in the current stage",
            "at least two positive net outcomes", "summed cost-net PnL greater than zero",
            "closed-trade realized drawdown within the stage budget",
            "A small loss does not reset this rolling window when its net remains positive",
            "not a win-streak or win-rate rule", "Only a stage change resets the window",
            "unfilled scenarios do not count", "not invented settlement IDs or carried-over wins",
            "do not recalculate them or choose a higher stage in model text",
            "Closed-trade drawdown is not intratrade or whole-account drawdown",
            "not a statistical proof of an edge or optimal parameters",
            "do not trade solely to complete a promotion sample",
            "never raises the risk budget of an active scenario",
            "Stages0/1 retain original-OPEN-batch-only scope and zero chase",
            "do not assume every single loss automatically downgrades rule 3",
        ):
            assert clause in text
