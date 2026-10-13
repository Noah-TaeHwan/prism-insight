"""Pure and runtime boundaries for user-approved limited restoration."""
import copy

import pytest

from core.llm_scenario import validate_scenario, ScenarioValidationError
from core.scenario_stop_policy import build_stop_policy
from tests.test_llm_scenario import active
from tests.test_scenario_runtime import setup as setup, terminal, wait_proposal


def candidate(side="LONG"):
    p, c = active()
    c.update(scenario_risk_fraction=.005, accounting_status="confirmed")
    c["positions"][0]["quantity"] = .01
    p["hard_stop"] = 98500
    initial = 98000
    if side == "SHORT":
        initial = 102000
        c.update(side="SHORT", previous_hard_stop=101000, mark_price=99000)
        p.update(side="SHORT", hard_stop=101500)
        p["take_profits"][0]["price"] = 98000
    a = dict(stop_policy=dict(version=1, initial_hard_stop=initial, initial_action_id="original"))
    c["stop_policy"] = build_stop_policy(a, hard_blocked=False, permit_valid=True,
        accounting_status="confirmed", protection_ok=True, legacy_fenced=False,
        pending_entries=[], positions=c["positions"])
    p["stop_revision"] = dict(reason="New structural support observation",
        structure_reference="30m confirmed low with 1h thesis intact", input_id=c["input_id"])
    return p, c


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_bounded_restoration_keeps_original_half_percent_budget(side):
    p, c = candidate(side)
    before = copy.deepcopy((p, c))
    out = validate_scenario(p, c)
    assert out["risk"]["budget"] == 50 and out["risk"]["within_budget"]
    assert out["stop_revision"] == p["stop_revision"]
    assert (p, c) == before


@pytest.mark.parametrize("fault", ["outside_anchor", "old_stop_crossed", "missing_meta", "wrong_input",
                                  "pending", "new_entry", "cancellation", "hard_block", "legacy"])
def test_widening_fail_closed(fault):
    p, c = candidate()
    if fault == "outside_anchor": p["hard_stop"] = 97999
    elif fault == "old_stop_crossed": c["mark_price"] = c["previous_hard_stop"]
    elif fault == "missing_meta": p.pop("stop_revision")
    elif fault == "wrong_input": p["stop_revision"]["input_id"] = "old-input"
    elif fault == "pending": c["pending_entries"] = [{"id": "pending", "price": 100000, "quantity": .001}]
    elif fault == "new_entry": p["entries"] = [{"id": "new", "price": 100000, "quantity": .001}]
    elif fault == "cancellation":
        c["pending_entries"] = [{"id": "pending", "price": 100000, "quantity": .001}]
        p["cancel_entry_ids"] = ["pending"]
    elif fault == "hard_block": c["stop_policy"]["widening_eligible"] = False
    elif fault == "legacy": c.pop("stop_policy")
    with pytest.raises(ScenarioValidationError, match="stop widening forbidden"):
        validate_scenario(p, c)


def test_no_entry_widening_checks_total_risk_even_with_partial_stop():
    p, c = candidate()
    c["positions"][0]["quantity"] = .1
    p["partial_stops"] = [{"id": "partial", "price": 100500, "fraction": .9}]
    with pytest.raises(ScenarioValidationError, match="risk budget exceeded"):
        validate_scenario(p, c)


def test_bad_unused_revision_does_not_block_tightening_or_exit():
    p, c = candidate()
    p.update(hard_stop=99500, stop_revision={"broken": True})
    assert "stop_revision" not in validate_scenario(p, c)
    p.update(action="EXIT", entries=[], take_profits=[], partial_stops=[])
    for key in ("side", "hard_stop", "chase"): p.pop(key)
    assert "stop_revision" not in validate_scenario(p, c)


@pytest.mark.parametrize("anchor", [None, {}, "corrupt", {"version": True}, {"version": 1, "initial_hard_stop": 10**500}])
def test_unknown_anchor_never_grants_permission(anchor):
    policy = build_stop_policy({"stop_policy": anchor}, hard_blocked=False, permit_valid=True,
        accounting_status="confirmed", protection_ok=True, legacy_fenced=False,
        pending_entries=[], positions=[{"price": 100, "quantity": 1}])
    assert not policy["widening_eligible"]


def test_runtime_initial_anchor_is_immutable_and_legacy_not_backfilled(setup):
    r, b, now, _ = setup
    assert r.tick()["status"] == "intent_pending"
    expected = dict(version=1, initial_hard_stop=90, initial_action_id="action-1")
    assert r.state()["active"]["stop_policy"] == expected
    b.evidence = dict(intents=[terminal()])
    b.ctx.update(positions=[{"price": 100, "quantity": 1}], accounting_status="confirmed")
    now[0] += 300
    r.propose = lambda s, c: wait_proposal(s, c, action_id="wait-1")
    assert r.tick()["status"] == "wait"
    assert r.state()["active"]["stop_policy"] == expected
    state = r.state()
    state["active"].pop("stop_policy")
    r._save(state)
    now[0] += 300
    r.propose = lambda s, c: wait_proposal(s, c, action_id="wait-2")
    assert r.tick()["status"] == "wait"
    assert "stop_policy" not in r.state()["active"]
    assert not r._context(r.state())["stop_policy"]["widening_eligible"]


def test_wire_exposes_nullable_revision_and_keeps_exit_independent():
    from live.scenario_contract import response_schema, validate_wire_proposal
    from tests.test_scenario_llm import context, wire
    c = context(True)
    c["stop_policy"] = dict(version=1, widening_eligible=False)
    assert "stop_revision" in response_schema(c)["required"]
    p = wire(c, "EXIT")
    p["stop_revision"] = {"invalid": True}
    assert "stop_revision" not in validate_wire_proposal(p, c)
    assert validate_scenario(validate_wire_proposal(p, c), c)["action"] == "EXIT"


@pytest.mark.parametrize("side,old,new", [("LONG", 84600, 84500), ("SHORT", 84900, 85000)])
def test_confirmed_reversal_notice_explicitly_warns_of_increased_price_risk(side, old, new):
    from live.scenario_notice import render_notice
    from tests.test_scenario_notice import position
    before = position(side=side, hard_stop=old, timestamp=900)
    after = position(side=side, hard_stop=new)
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, protection_confirmed=True,
                             position_before=before, position_after=after))
    assert "SL 완화 확인" in out and "가격 위험 증가" in out
    assert f"{old:,.2f} → {new:,.2f}" in out
